"""Preserve the existing Conky graphs and expose their position in arrange mode."""

import logging
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import shutil
import signal
import subprocess
import tempfile
import time

import gi
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11, GLib

from core import conky_gap
from windows import DesktopSurface


class Conky:
    def __init__(self, app):
        self.app = app
        self.window = None
        self.overlay = None
        self.config_path = Path.home() / ".config/conky/system.conf"
        self.width, self.height = 258, 362
        self.updates = ThreadPoolExecutor(max_workers=1, thread_name_prefix="gallery-conky")
        self.closed = False

    def find(self):
        try:
            listing = subprocess.run(["xwininfo", "-root", "-tree"], capture_output=True, text=True,
                                     check=True, timeout=3).stdout
            match = re.search(r'^\s*(0x[0-9a-f]+) "conky[^\n]*\("Conky" "Conky"\)', listing, re.MULTILINE | re.IGNORECASE)
            self.window = (GdkX11.X11Window.foreign_new_for_display(Gdk.Display.get_default(), int(match[1], 16))
                           if match else None)
            if self.window:
                geometry = self.window.get_geometry()
                self.width, self.height = geometry.width, geometry.height
        except (OSError, subprocess.SubprocessError) as error:
            logging.warning("Finding system graphs: %s", error)
            self.window = None
        return self.window is not None

    def start(self):
        """Start the configured graph panel when it is not already running."""
        if self.find() or not self.config_path.is_file():
            return
        try:
            subprocess.Popen(["conky", "-c", str(self.config_path)],
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True,
                             close_fds=True)
        except OSError as error:
            logging.warning("Starting system graphs failed: %s", error)

    def stop(self):
        """Stop only the Conky instance using Gallery Desk's configuration."""
        try:
            result = subprocess.run(["pgrep", "-x", "conky"], capture_output=True,
                                    text=True, timeout=2)
        except (OSError, subprocess.SubprocessError):
            return
        for value in result.stdout.split():
            try:
                arguments = (Path("/proc") / value / "cmdline").read_bytes().split(b"\0")
                if os.fsencode(self.config_path) in arguments:
                    os.kill(int(value), signal.SIGTERM)
            except (FileNotFoundError, ProcessLookupError, ValueError):
                continue
        # Conky can take a moment to release its X11 window. Confirm the
        # process is gone so a subsequent launch cannot inherit a stale panel.
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if not self.find():
                return
            time.sleep(.05)

    def move(self):
        if self.window and not self.window.is_destroyed():
            position = self.app.config["graphs"]
            self.window.move(round(position["x"]), round(position["y"]))
            Gdk.Display.get_default().flush()

    def save(self):
        if not self.config_path.is_file() or not self.window:
            return
        original = self.config_path.read_text()
        backup = self.app.config_path.parent / "backups/conky-system.conf"
        backup.parent.mkdir(parents=True, exist_ok=True)
        if not backup.exists():
            shutil.copy2(self.config_path, backup)
        def setting(name, default):
            match = re.search(rf"(?m)^\s*{name}\s*=\s*(\d+)", original)
            return int(match[1]) if match else default

        # Gap values locate Conky's text origin, not its outer window. Both
        # gaps and borders scale with Xft DPI (120 DPI on this desktop).
        border = setting("border_inner_margin", 3) + setting("border_outer_margin", 3) + setting("border_width", 1)
        dpi = Gdk.Screen.get_default().get_resolution()
        dpi = dpi if dpi > 0 else 96
        x = conky_gap(self.app.config["graphs"]["x"], dpi, border)
        y = conky_gap(self.app.config["graphs"]["y"], dpi, border)
        updated, count_x = re.subn(r"(?m)^(\s*gap_x\s*=\s*)-?\d+(\s*,)", rf"\g<1>{round(x)}\2", original)
        updated, count_y = re.subn(r"(?m)^(\s*gap_y\s*=\s*)-?\d+(\s*,)", rf"\g<1>{round(y)}\2", updated)
        if count_x != 1 or count_y != 1:
            logging.warning("Conky position was not saved: expected one gap_x and gap_y setting.")
            return
        if updated != original:
            future = self.updates.submit(self._restart_with_config, updated)
            future.add_done_callback(self._updated)

    def _restart_with_config(self, updated):
        # This Conky 1.19/Cairo setup crashes during repeated in-process reloads.
        # Stop only the instance using this config before saving, then restart it.
        # A single worker keeps rapid layout saves ordered without blocking fades.
        if self.closed:
            return
        result = subprocess.run(["pgrep", "-x", "conky"], capture_output=True, text=True, timeout=2)
        for pid in result.stdout.split():
            try:
                arguments = (Path("/proc") / pid / "cmdline").read_bytes().split(b"\0")
            except FileNotFoundError:
                continue
            if os.fsencode(self.config_path) in arguments:
                try:
                    os.kill(int(pid), signal.SIGTERM)
                except ProcessLookupError:
                    continue
                deadline = time.monotonic() + 3
                while True:
                    try:
                        state = (Path("/proc") / pid / "stat").read_text().split(")", 1)[1].split()[0]
                    except FileNotFoundError:
                        break
                    if state == "Z":
                        break
                    if time.monotonic() >= deadline:
                        raise RuntimeError("The system graphs did not stop to save their new position.")
                    time.sleep(.03)
        descriptor, temporary = tempfile.mkstemp(prefix="gallery-", dir=self.config_path.parent)
        try:
            with os.fdopen(descriptor, "w") as output:
                output.write(updated)
                output.flush()
                os.fsync(output.fileno())
            os.chmod(temporary, self.config_path.stat().st_mode & 0o777)
            os.replace(temporary, self.config_path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        if self.closed:
            return
        with (self.app.cache_path / "conky.log").open("a") as output:
            subprocess.Popen(["conky", "-c", str(self.config_path)], stdin=subprocess.DEVNULL,
                             stdout=output, stderr=subprocess.STDOUT, start_new_session=True,
                             close_fds=True)

    def _updated(self, future):
        try:
            future.result()
        except Exception as error:
            logging.error("Saving system graph position: %s", error)
            if not self.closed:
                GLib.idle_add(self.app.show_error, "The system graphs could not be repositioned", str(error))
        else:
            if not self.closed:
                GLib.timeout_add(300, self._refresh_window)

    def _refresh_window(self):
        if not self.closed and self.find() and self.overlay:
            self.overlay.window.get_window().raise_()
        return GLib.SOURCE_REMOVE

    def close(self):
        self.closed = True
        self.updates.shutdown(wait=False)
        self.stop()

    def set_arranging(self, arranging):
        if arranging and self.find():
            self.overlay = GraphHandle(self.app, self)
        elif self.overlay:
            self.overlay.destroy()
            self.overlay = None


class GraphHandle(DesktopSurface):
    def __init__(self, app, conky):
        self.conky = conky
        super().__init__(app, "graphs", "System load", app.config["graphs"], conky.width, conky.height)

    def reposition(self):
        super().reposition()
        self.conky.move()

    def resize(self, size):
        # Graph dimensions are owned by the user's existing Conky configuration.
        pass

    def _press(self, area, event):
        result = super()._press(area, event)
        if self.drag:
            self.drag = ("move", *self.drag[1:])
        return result

    def context_menu(self, event):
        self.app.toggle_arrange()
