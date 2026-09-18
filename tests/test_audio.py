import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import numpy as np

from audio import AudioMonitor, BANDS, FFT_SIZE, SAMPLE_RATE, SpectrumAnalyzer, SpectrumEnvelope, capture_command


def tone(frequency, opposite=False):
    signal = .4 * np.sin(2 * np.pi * frequency * np.arange(FFT_SIZE) / SAMPLE_RATE)
    return np.column_stack((signal, -signal if opposite else signal)).astype(np.float32).tobytes()


class SpectrumTests(unittest.TestCase):
    def test_bass_midrange_and_treble_tones_peak_in_their_frequency_bands(self):
        for frequency in (100, 1000, 8000):
            with self.subTest(frequency=frequency):
                analyzer = SpectrumAnalyzer()
                levels = analyzer.feed(tone(frequency))
                band = np.argmax(levels)
                self.assertGreater(levels[band], .7)
                self.assertLessEqual(analyzer.edges[band] - SAMPLE_RATE / FFT_SIZE, frequency)
                self.assertGreaterEqual(analyzer.edges[band + 1] + SAMPLE_RATE / FFT_SIZE, frequency)

    def test_opposite_phase_stereo_preserves_the_spectrum(self):
        normal = SpectrumAnalyzer().feed(tone(1000))
        opposite = SpectrumAnalyzer().feed(tone(1000, opposite=True))
        np.testing.assert_allclose(normal, opposite)

    def test_fragmented_pcm_matches_complete_stereo_frames(self):
        data = tone(1000)
        analyzer = SpectrumAnalyzer()
        self.assertIsNone(analyzer.feed(data[:3]))
        self.assertIsNone(analyzer.feed(data[3:23]))
        fragmented = analyzer.feed(data[23:])
        np.testing.assert_allclose(fragmented, SpectrumAnalyzer().feed(data))
        self.assertEqual(len(analyzer.pending), 0)

    def test_silence_and_constant_dc_leave_all_bands_empty(self):
        for value in (0, .5, float('nan'), float('inf')):
            data = np.full((FFT_SIZE, 2), value, dtype=np.float32).tobytes()
            self.assertEqual(SpectrumAnalyzer().feed(data), [0.0] * BANDS)

    def test_silence_replaces_previous_audio_without_retaining_old_peaks(self):
        analyzer = SpectrumAnalyzer()
        self.assertGreater(max(analyzer.feed(tone(1000))), .7)
        self.assertEqual(analyzer.feed(bytes(FFT_SIZE * 8)), [0.0] * BANDS)

    def test_envelope_attacks_quickly_holds_peaks_then_settles_to_silence(self):
        envelope = SpectrumEnvelope()
        envelope.update([1] * BANDS, .1)
        self.assertGreater(envelope.levels[0], .9)
        peak = envelope.peaks[0]
        envelope.update([0] * BANDS, .1)
        self.assertGreater(envelope.levels[0], .5)
        self.assertEqual(envelope.peaks[0], peak)
        envelope.update([0] * BANDS, .2)
        self.assertLess(envelope.peaks[0], peak)
        for _ in range(100):
            envelope.update([0] * BANDS, .1)
        self.assertEqual(envelope.levels.tolist(), [0.0] * BANDS)
        self.assertEqual(envelope.peaks.tolist(), [0.0] * BANDS)
        self.assertFalse(envelope.update([0] * BANDS, .1))


class AudioCaptureTests(unittest.TestCase):
    def test_capture_uses_passive_sink_monitor_and_raw_stdout(self):
        command = capture_command()
        properties = json.loads(command[command.index('--properties') + 1])
        self.assertTrue(properties['stream.capture.sink'])
        self.assertTrue(properties['stream.monitor'])
        self.assertTrue(properties['node.passive'])
        self.assertEqual(command[command.index('--target') + 1], 'auto')
        self.assertEqual(command[-1], '-')

    def test_suspended_audio_expires_old_samples(self):
        monitor = AudioMonitor()
        with patch('audio.time.monotonic', return_value=100):
            monitor._publish([.5] * BANDS)
        self.assertEqual(monitor.sample(100.1)['levels'], [.5] * BANDS)
        self.assertEqual(monitor.sample(100.4)['levels'], [0.0] * BANDS)

    def test_missing_audio_tool_reports_error_and_waits_before_retrying(self):
        monitor = AudioMonitor()
        with patch('audio.subprocess.Popen', side_effect=FileNotFoundError('pw-cat missing')):
            with patch.object(monitor.stopping, 'wait', side_effect=lambda seconds: monitor.stopping.set()) as wait:
                monitor._run()
        self.assertIn('pw-cat missing', monitor.sample()['error'])
        self.assertFalse(monitor.sample()['connected'])
        wait.assert_called_once_with(3)

    def test_capture_failure_reconnects_and_closes_each_child_and_pipe(self):
        monitor = AudioMonitor()
        first, second = Mock(), Mock()
        first.poll.return_value = second.poll.return_value = None

        def read(process):
            if process is first:
                raise RuntimeError('Audio server stopped')
            monitor._publish([.5] * BANDS)
            monitor.stopping.set()

        with patch('audio.subprocess.Popen', side_effect=(first, second)):
            with patch.object(monitor, '_read', side_effect=read):
                with patch.object(monitor.stopping, 'wait', return_value=False):
                    monitor._run()
        self.assertTrue(monitor.sample()['connected'])
        self.assertEqual(monitor.sample()['error'], '')
        for process in (first, second):
            process.terminate.assert_called_once()
            process.wait.assert_called_once_with(timeout=1)
            process.stdout.close.assert_called_once()
            process.stderr.close.assert_called_once()

    def test_disabling_visualizer_stops_capture_and_reenabling_creates_window(self):
        from app import GalleryDesk
        app = SimpleNamespace(config={'visualizer': {'enabled': True, 'width': 640, 'x': 20, 'y': 30}},
                              audio=Mock(), visualizer=None)
        with patch('app.AudioVisualizer') as widget:
            GalleryDesk.update_visualizer(app)
            app.audio.start.assert_called_once()
            window = app.visualizer
            app.config['visualizer']['enabled'] = False
            GalleryDesk.update_visualizer(app)
            window.destroy.assert_called_once()
            app.audio.stop.assert_called_once()
            self.assertIsNone(app.visualizer)
            app.config['visualizer']['enabled'] = True
            GalleryDesk.update_visualizer(app)
            self.assertEqual(widget.call_count, 2)


if __name__ == '__main__':
    unittest.main()
