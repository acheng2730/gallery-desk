"""Passive desktop-audio capture and a small, logarithmic stereo spectrum."""

import json
import logging
import os
import selectors
import subprocess
import threading
import time

import numpy as np


SAMPLE_RATE = 48000
FFT_SIZE = 4096
HOP_SIZE = 1536
BANDS = 32
LOW_FREQUENCY = 50
HIGH_FREQUENCY = 16000


def capture_command(target="auto"):
    # Capture sink monitor ports, never the microphone. Passive links let the
    # audio device suspend normally; WirePlumber follows the default output.
    properties = {"application.name": "Gallery Desk", "node.name": "gallery-desk-spectrum",
                  "media.name": "Desktop audio spectrum", "stream.capture.sink": True,
                  "stream.monitor": True, "node.passive": True}
    return ["pw-cat", "--record", "--target", target, "--media-role", "DSP",
            "--rate", str(SAMPLE_RATE), "--channels", "2", "--format", "f32",
            "--latency", "50ms", "--properties", json.dumps(properties), "-"]


class SpectrumAnalyzer:
    def __init__(self):
        self.pending = bytearray()
        self.samples = np.zeros((FFT_SIZE, 2))
        self.unprocessed = 0
        self.window = np.hanning(FFT_SIZE)
        self.normalization = (2 / self.window.sum()) ** 2
        self.edges = np.geomspace(LOW_FREQUENCY, HIGH_FREQUENCY, BANDS + 1)
        bins = np.ceil(self.edges * FFT_SIZE / SAMPLE_RATE).astype(int)
        self.slices = [slice(low, max(low + 1, high)) for low, high in zip(bins[:-1], bins[1:])]

    def feed(self, data):
        self.pending.extend(data)
        length = len(self.pending) // 8 * 8
        if not length:
            return None
        samples = np.frombuffer(bytes(self.pending[:length]), dtype=np.float32).reshape(-1, 2)
        del self.pending[:length]
        samples = np.clip(np.nan_to_num(samples, nan=0, posinf=0, neginf=0), -1, 1)
        count = min(len(samples), FFT_SIZE)
        self.samples = np.concatenate((self.samples[count:], samples[-count:]))
        self.unprocessed += len(samples)
        if self.unprocessed < HOP_SIZE:
            return None
        self.unprocessed %= HOP_SIZE
        centered = self.samples - self.samples.mean(axis=0)
        spectrum = np.fft.rfft(centered * self.window[:, None], axis=0)
        # Combine channel power instead of waveforms: opposite-phase stereo
        # still has energy and must not disappear from the display.
        power = np.mean(np.abs(spectrum) ** 2, axis=1) * self.normalization
        levels = np.array([power[band].max() for band in self.slices])
        decibels = 10 * np.log10(np.maximum(levels, 1e-12))
        return np.clip((decibels + 65) / 65, 0, 1).tolist()


class SpectrumEnvelope:
    def __init__(self):
        self.levels = np.zeros(BANDS)
        self.peaks = np.zeros(BANDS)
        self.hold = np.zeros(BANDS)

    def update(self, targets, elapsed):
        elapsed = max(0, min(1, elapsed))
        targets = np.clip(np.asarray(targets), 0, 1)
        previous = (self.levels.copy(), self.peaks.copy())
        speeds = np.where(targets > self.levels, .035, .20)
        self.levels += (targets - self.levels) * (1 - np.exp(-elapsed / speeds))
        self.levels[self.levels < .002] = 0
        rising = self.levels >= self.peaks
        self.hold = np.where(rising, .22, np.maximum(0, self.hold - elapsed))
        falling = np.where(self.hold > 0, self.peaks, self.peaks - elapsed * .45)
        self.peaks = np.maximum(self.levels, falling)
        return not (np.array_equal(previous[0], self.levels) and np.array_equal(previous[1], self.peaks))


class AudioMonitor:
    def __init__(self):
        self.thread = None
        self.stopping = threading.Event()
        self.lock = threading.Lock()
        self.levels = [0.0] * BANDS
        self.received_at = 0
        self.connected = False
        self.error = ""

    def start(self):
        if self.thread and self.thread.is_alive():
            return
        self.stopping.clear()
        self.thread = threading.Thread(target=self._run, name="gallery-audio", daemon=True)
        self.thread.start()

    def stop(self):
        self.stopping.set()
        if self.thread:
            self.thread.join(timeout=2)
            if self.thread.is_alive():
                logging.error("The audio monitor did not stop within two seconds.")
            else:
                self.thread = None
        self._publish([0.0] * BANDS, connected=False)

    def _publish(self, levels, connected=True, error=""):
        with self.lock:
            self.levels = list(levels)
            self.received_at = time.monotonic()
            self.connected = connected
            self.error = error

    def sample(self, now=None):
        now = time.monotonic() if now is None else now
        with self.lock:
            # A passive sink stops sending samples when suspended. Expire the
            # last FFT so pausing audio never leaves frozen bars on the desktop.
            levels = self.levels.copy() if now - self.received_at < .3 else [0.0] * BANDS
            return {"levels": levels, "connected": self.connected, "error": self.error}

    def _run(self):
        previous_error = ""
        while not self.stopping.is_set():
            process = None
            try:
                process = subprocess.Popen(capture_command(), stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                           bufsize=0, close_fds=True)
                self._publish([0.0] * BANDS)
                self._read(process)
            except (OSError, RuntimeError, ValueError) as error:
                if not self.stopping.is_set():
                    message = str(error)
                    if message != previous_error:
                        logging.warning("Desktop audio capture: %s", message)
                        previous_error = message
                    self._publish([0.0] * BANDS, connected=False, error=message)
            finally:
                if process is not None:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=1)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
                    process.stdout.close()
                    process.stderr.close()
            self.stopping.wait(3)

    def _read(self, process):
        analyzer = SpectrumAnalyzer()
        errors = ""
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ, "audio")
            selector.register(process.stderr, selectors.EVENT_READ, "error")
            while not self.stopping.is_set():
                for key, events in selector.select(timeout=.2):
                    data = os.read(key.fileobj.fileno(), 65536)
                    if key.data == "error":
                        errors = (errors + data.decode(errors="replace"))[-2048:]
                        if not data:
                            selector.unregister(key.fileobj)
                    elif not data:
                        raise RuntimeError(errors.strip() or "Desktop audio stream ended; reconnecting.")
                    else:
                        levels = analyzer.feed(data)
                        if levels is not None:
                            self._publish(levels)
                if process.poll() is not None:
                    raise RuntimeError(errors.strip() or "Desktop audio capture stopped; reconnecting.")
