#!/usr/bin/python3
"""Gallery Desk: original-aspect photographs and a Spotify desktop turntable."""

import copy
import json
import logging
import os
from pathlib import Path
import sys
import time
import urllib.parse

# Xwayland supports positioned, sticky DESKTOP windows in the current GNOME
# session. Native Wayland top-levels intentionally cannot choose their position.
os.environ["GDK_BACKEND"] = "x11"

import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk

from assets import AssetLoader
from audio import AudioMonitor
from conky_panel import Conky
from core import PhotoDeck, contain_rect, load_config, save_config, snap_axis, validate_config
from media import Spotify
from settings import ArrangeToolbar, Settings
from windows import AudioVisualizer, PhotoFrame, Turntable


CONTROL_XML = """<node><interface name="org.alan.GalleryDesk.Control">
<method name="GetState"><arg type="s" direction="out"/></method>
<method name="SetArrange"><arg type="b" direction="in"/></method>
<method name="NextPhoto"><arg type="s" direction="in"/></method>
<method name="PlaceWidget"><arg type="s" direction="in"/><arg type="i" direction="in"/>
<arg type="i" direction="in"/><arg type="i" direction="in"/></method>
<method name="Snapshot"><arg type="s" direction="out"/></method>
</interface></node>"""


class GalleryDesk(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.alan.GalleryDesk", flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.config_path = Path.home() / ".config/gallery-desk/settings.json"
        self.cache_path = Path.home() / ".cache/gallery-desk"
        self.cache_path.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(filename=self.cache_path / "gallery-desk.log", level=logging.INFO,
                            format="%(asctime)s %(levelname)s %(message)s")
        self.frames = []
        self.player = None
        self.visualizer = None
        self.settings = None
        self.toolbar = None
        self.arranging = False
        self.scanning = False
        self.scan_generation = 0
        self.photo_error = ""
        self.initialized = False

    def do_startup(self):
        Gtk.Application.do_startup(self)
        self.hold()
        self.config = load_config(self.config_path)
        display = Gdk.Display.get_default()
        if display is None:
            raise RuntimeError("Gallery Desk needs a running graphical desktop.")
        self.workarea = (display.get_primary_monitor() or display.get_monitor(0)).get_workarea()
        self.loader = AssetLoader(self.cache_path / "images")
        self.deck = PhotoDeck()
        self.media = Spotify(self.loader, self.media_changed)
        self.audio = AudioMonitor()
        self.conky = Conky(self)
        self.conky.start()
        self.create_windows()
        self.scan()
        self.animation_timer = GLib.timeout_add(33, self.tick)
        self.scan_timer = GLib.timeout_add_seconds(30, self.scan)
        self.geometry_timer = GLib.timeout_add_seconds(5, self.check_workarea)
        info = Gio.DBusNodeInfo.new_for_xml(CONTROL_XML).interfaces[0]
        self.control_registration = self.get_dbus_connection().register_object(
            "/org/alan/GalleryDesk/Control", info, self.control, None, None)
        self.initialized = True
        self.persist()
        logging.info("Gallery Desk started with %d photo frames", len(self.frames))

    def do_command_line(self, command_line):
        arguments = command_line.get_arguments()[1:]
        if "--quit" in arguments:
            self.quit()
        elif "--status" in arguments:
            command_line.print_literal(json.dumps(self.state(), indent=2) + "\n")
        elif "--snapshot" in arguments:
            command_line.print_literal(str(self.snapshot()) + "\n")
        elif "--arrange" in arguments:
            if not self.arranging:
                self.toggle_arrange()
        elif "--background" not in arguments:
            self.show_settings()
        return 0

    def create_windows(self):
        for index, layout in enumerate(self.config["frames"]):
            self.frames.append(PhotoFrame(self, layout, index))
        self.player = Turntable(self, self.config["player"])
        self.update_visualizer()

    def surfaces(self):
        return [*self.frames, self.player] + ([self.visualizer] if self.visualizer else [])

    def update_visualizer(self):
        layout = self.config["visualizer"]
        if layout["enabled"]:
            if self.visualizer is None:
                self.visualizer = AudioVisualizer(self, layout)
            else:
                self.visualizer.layout = layout
                self.visualizer.resize(layout["width"])
            self.audio.start()
        else:
            if self.visualizer:
                self.visualizer.destroy()
                self.visualizer = None
            self.audio.stop()

    def media_changed(self):
        if self.player and not self.player.destroyed:
            self.player.area.queue_draw()

    def frame_index(self, frame):
        return self.frames.index(frame) if frame in self.frames else 0

    def widget_rectangles(self, excluded=None):
        rectangles = []
        for window in self.surfaces():
            if window is excluded or getattr(window, "destroyed", False):
                continue
            rectangles.append((window.layout["x"], window.layout["y"], window.width, window.height))
        if excluded is None or getattr(excluded, "identifier", None) != "graphs":
            conky = getattr(self, "conky", None)
            if conky is not None:
                rectangles.append((self.config["graphs"]["x"], self.config["graphs"]["y"],
                                   getattr(conky, "width", 258), getattr(conky, "height", 362)))
        return rectangles

    def set_snap_enabled(self, enabled):
        self.config["snap_enabled"] = bool(enabled)
        for window in self.surfaces():
            window.area.queue_draw()

    def set_snap_spacing(self, spacing):
        self.config["snap_spacing"] = max(4, min(200, round(spacing)))
        for window in self.surfaces():
            window.area.queue_draw()

    def snap_position(self, surface, x, y):
        if not self.config.get("snap_enabled", True):
            return x, y
        spacing = self.config.get("snap_spacing", 24)
        others = self.widget_rectangles(surface)
        horizontal = [(other_x, other_width, other_y, other_height)
                      for other_x, other_y, other_width, other_height in others]
        vertical = [(other_y, other_height, other_x, other_width)
                    for other_x, other_y, other_width, other_height in others]
        return (snap_axis(x, surface.width, y, surface.height, horizontal,
                          self.workarea.x, spacing),
                snap_axis(y, surface.height, x, surface.width, vertical,
                          self.workarea.y, spacing))

    def snap_size(self, surface, size):
        if not self.config.get("snap_enabled", True):
            return size
        spacing = self.config.get("snap_spacing", 24)
        tolerance = max(6, spacing * .65)
        minimum = {"player": 480, "visualizer": 400}.get(surface.identifier, 160)
        maximum = 1000
        candidates = [(2, abs(size - round(size / spacing) * spacing),
                       round(size / spacing) * spacing)]
        x, y = surface.layout["x"], surface.layout["y"]
        for other_x, other_y, other_width, other_height in self.widget_rectangles(surface):
            if abs(other_y - y) <= tolerance or y < other_y + other_height and y + surface.height > other_y:
                for edge in (other_x, other_x + other_width):
                    candidate = edge - x
                    if minimum <= candidate <= maximum and abs(candidate - size) <= tolerance:
                        candidates.append((1, abs(candidate - size), candidate))
            if abs(other_x - x) <= tolerance or x < other_x + other_width and x + surface.width > other_x:
                for edge in (other_y, other_y + other_height):
                    candidate = edge - y
                    if minimum <= candidate <= maximum and abs(candidate - size) <= tolerance:
                        candidates.append((1, abs(candidate - size), candidate))
        return min(candidates, key=lambda candidate: (candidate[0], candidate[1]))[2]

    def tick(self):
        now = time.monotonic()
        for frame in self.frames:
            frame.tick(now)
        self.player.tick(now)
        if self.visualizer:
            self.visualizer.tick(now)
        return GLib.SOURCE_CONTINUE

    def scan(self):
        if self.scanning:
            return GLib.SOURCE_CONTINUE
        self.scanning = True
        generation = self.scan_generation

        def scanned(files, error):
            if generation != self.scan_generation:
                return
            self.scanning = False
            self.photo_error = str(error) if error else ""
            if error:
                logging.warning("Scanning photos: %s", error)
                return
            if files != self.deck.files:
                self.deck.replace_files(files)
            if not files:
                self.photo_error = "This folder contains no supported photos."
            for frame in self.frames:
                if frame.current is None:
                    frame.next_due = time.monotonic() + self.frame_index(frame) * .3
                    frame.area.queue_draw()

        self.loader.scan(self.config["photo_directory"], scanned)
        return GLib.SOURCE_CONTINUE

    def check_workarea(self):
        display = Gdk.Display.get_default()
        area = (display.get_primary_monitor() or display.get_monitor(0)).get_workarea()
        if (area.x, area.y, area.width, area.height) != (self.workarea.x, self.workarea.y, self.workarea.width, self.workarea.height):
            self.workarea = area
            for window in self.surfaces():
                window.reposition()
            self.persist()
        return GLib.SOURCE_CONTINUE

    def persist(self):
        try:
            save_config(self.config_path, self.config)
            if self.arranging:
                self.conky.save()
        except (OSError, ValueError) as error:
            logging.error("Saving settings: %s", error)
            self.show_error("Your layout could not be saved", str(error))

    def toggle_arrange(self):
        self.arranging = not self.arranging
        self.conky.set_arranging(self.arranging)
        if self.arranging:
            self.toolbar = ArrangeToolbar(self)
        elif self.toolbar:
            self.toolbar.destroy()
            self.toolbar = None
            self.conky.save()
            self.persist()
        for window in self.surfaces():
            window.set_arranging(self.arranging)
        if self.toolbar:
            self.toolbar.window.present()
        if self.settings:
            self.settings.arrange.set_label("Done arranging" if self.arranging else "Arrange widgets on the desktop")

    def new_frame_layout(self, identifiers=None):
        identifiers = identifiers if identifiers is not None else {frame.identifier for frame in self.frames}
        index = 1
        while f"photo-{index}" in identifiers:
            index += 1
        return {"id": f"photo-{index}", "name": f"Photo {index}", "size": 340,
                "x": self.workarea.x + (self.workarea.width - 340) // 2,
                "y": self.workarea.y + (self.workarea.height - 340) // 2}

    def add_frame(self):
        if len(self.frames) >= 12:
            self.show_error("Frame limit", "You can use up to 12 photo frames.")
            return
        layout = self.new_frame_layout()
        self.config["frames"].append(layout)
        self.frames.append(PhotoFrame(self, layout, len(self.frames)))
        self.persist()

    def remove_frame(self, identifier):
        for frame in self.frames[:]:
            if frame.identifier == identifier:
                self.frames.remove(frame)
                self.config["frames"].remove(frame.layout)
                frame.destroy()
        self.persist()

    def apply_settings(self, config):
        from pathlib import Path
        validated = validate_config(config)
        if not Path(validated["photo_directory"]).is_dir():
            raise ValueError("Choose a photo folder that exists.")
        folder_changed = validated["photo_directory"] != self.config["photo_directory"]
        save_config(self.config_path, validated)
        old_frames = {frame.identifier: frame for frame in self.frames}
        self.config = validated
        self.frames = []
        for index, layout in enumerate(self.config["frames"]):
            frame = old_frames.pop(layout["id"], None)
            if frame is None:
                frame = PhotoFrame(self, layout, index)
            else:
                frame.layout = layout
                frame.title = layout["name"]
                frame.resize(layout["size"])
            if frame.current is not None:
                frame.schedule_next()
            self.frames.append(frame)
        for frame in old_frames.values():
            frame.destroy()
        self.player.layout = self.config["player"]
        self.player.resize(self.config["player"]["width"])
        self.update_visualizer()
        if self.conky.overlay:
            self.conky.overlay.layout = self.config["graphs"]
        if folder_changed:
            self.scan_generation += 1
            self.scanning = False
            self.deck.replace_files([])
            for frame in self.frames:
                frame.reset()
            self.scan()
        self.persist()

    def show_settings(self):
        if self.settings is None:
            self.settings = Settings(self)
        self.settings.window.show_all()
        self.settings.window.present()

    def show_error(self, title, detail):
        dialog = Gtk.MessageDialog(message_type=Gtk.MessageType.ERROR, buttons=Gtk.ButtonsType.CLOSE, text=title)
        dialog.format_secondary_text(detail)
        dialog.connect("response", lambda widget, response: widget.destroy())
        dialog.show()

    def state(self):
        frames = []
        for frame in self.frames:
            picture = frame.current
            frames.append({**frame.layout, "current": frame.current_path, "incoming": frame.incoming_path,
                           "loading": frame.loading, "fade": frame.fraction(),
                           "delay_seconds": frame.delay_seconds,
                           "next_change_seconds": max(0, frame.next_due - time.monotonic()),
                           "image_size": [picture.get_width(), picture.get_height()] if picture else None,
                           "image_rect": contain_rect(picture.get_width(), picture.get_height(), frame.width, frame.height) if picture else None})
        return {"arranging": self.arranging, "photo_count": len(self.deck.files), "photo_error": self.photo_error,
                "photo_interval_range": [self.config["interval_min_seconds"], self.config["interval_max_seconds"]],
                "snap": {"enabled": self.config.get("snap_enabled", True),
                         "spacing": self.config.get("snap_spacing", 24)},
                "frames": frames, "player": self.config["player"], "graphs": self.config["graphs"],
                "visualizer": {**self.config["visualizer"], **self.audio.sample(),
                               "peaks": self.visualizer.envelope.peaks.tolist() if self.visualizer else []},
                "spotify": {"connected": self.media.connected, "title": self.media.title,
                            "artist": self.media.artist, "playing": self.media.clock.playing,
                            "position": self.media.clock.at(time.monotonic()), "length": self.media.clock.length,
                            "artwork_loaded": self.media.art is not None, "track_id": self.media.track_id}}

    def control(self, connection, sender, path, interface, method, parameters, invocation):
        try:
            values = parameters.unpack()
            if method == "GetState":
                invocation.return_value(GLib.Variant("(s)", (json.dumps(self.state()),)))
                return
            if method == "Snapshot":
                invocation.return_value(GLib.Variant("(s)", (str(self.snapshot()),)))
                return
            if method == "SetArrange":
                if values[0] != self.arranging:
                    self.toggle_arrange()
            elif method == "NextPhoto":
                frame = next(frame for frame in self.frames if frame.identifier == values[0])
                frame.next_photo()
            elif method == "PlaceWidget":
                identifier, x, y, size = values
                if identifier == "graphs":
                    if not self.conky.find():
                        raise ValueError("The system graphs are not running.")
                    self.config["graphs"].update(x=x, y=y)
                    self.conky.move()
                    self.conky.save()
                else:
                    window = next(window for window in self.surfaces() if window.identifier == identifier)
                    window.layout.update(x=x, y=y)
                    window.resize(size)
                self.persist()
            invocation.return_value(None)
        except (ValueError, OSError, StopIteration) as error:
            invocation.return_dbus_error("org.alan.GalleryDesk.Error", str(error) or "Unknown widget")

    def snapshot(self):
        screen = Gdk.Screen.get_default()
        width, height = screen.get_width(), screen.get_height()
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        context = cairo.Context(surface)
        settings = Gio.Settings.new("org.gnome.desktop.background")
        uri = settings.get_string("picture-uri-dark") or settings.get_string("picture-uri")
        wallpaper_path = urllib.parse.unquote(urllib.parse.urlparse(uri).path)
        wallpaper = GdkPixbuf.Pixbuf.new_from_file(wallpaper_path)
        scale = max(width / wallpaper.get_width(), height / wallpaper.get_height())
        context.save()
        context.translate((width - wallpaper.get_width() * scale) / 2, (height - wallpaper.get_height() * scale) / 2)
        context.scale(scale, scale)
        Gdk.cairo_set_source_pixbuf(context, wallpaper, 0, 0)
        context.paint()
        context.restore()
        for window in self.surfaces():
            context.save()
            context.translate(window.layout["x"], window.layout["y"])
            window.render(context)
            if self.arranging:
                import drawing
                spacing = self.config.get("snap_spacing", 24) if self.config.get("snap_enabled", True) else None
                offset = (self.workarea.x - window.layout["x"], self.workarea.y - window.layout["y"])
                drawing.arrange_overlay(context, window.width, window.height, window.title, spacing, offset)
            context.restore()
        if self.conky.find():
            picture = Gdk.pixbuf_get_from_window(self.conky.window, 0, 0, self.conky.width, self.conky.height)
            if picture:
                Gdk.cairo_set_source_pixbuf(context, picture, self.config["graphs"]["x"], self.config["graphs"]["y"])
                context.paint()
        path = self.cache_path / "layout-preview.png"
        surface.write_to_png(str(path))
        return path

    def do_shutdown(self):
        if self.initialized:
            for timer in (self.animation_timer, self.scan_timer, self.geometry_timer):
                GLib.source_remove(timer)
            self.get_dbus_connection().unregister_object(self.control_registration)
            self.media.close()
            self.audio.stop()
            self.conky.close()
            for frame in self.frames:
                frame.destroy()
            self.loader.close()
            logging.info("Gallery Desk stopped")
        Gtk.Application.do_shutdown(self)


if __name__ == "__main__":
    app = GalleryDesk()
    sys.exit(app.run(sys.argv))
