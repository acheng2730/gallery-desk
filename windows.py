"""Movable, transparent desktop surfaces and the photo/turntable controllers."""

import logging
import math
import random
import time

import cairo
import gi
gi.require_version("Wnck", "3.0")
from gi.repository import Gdk, GLib, Gtk, Wnck

from assets import pixbuf
from audio import SpectrumEnvelope
from core import clamp_position, contain_rect, fade_fraction
from tonearm import PARKED_ANGLE, Tonearm
import drawing
import glass


class DesktopSurface:
    def __init__(self, app, identifier, title, layout, width, height):
        self.app = app
        self.identifier = identifier
        self.title = title
        self.layout = layout
        self.width, self.height = int(width), int(height)
        self.drag = None
        self.destroyed = False
        self.window_arranging = app.arranging
        self.window = Gtk.ApplicationWindow(application=app)
        self.window.set_title("Gallery Desk · " + title)
        self.window.set_wmclass("gallery-desk", "GalleryDesk")
        self.window.set_decorated(False)
        self._set_window_layer(app.arranging)
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.set_accept_focus(False)
        self.window.set_focus_on_map(False)
        self.window.set_app_paintable(True)
        self.window.set_resizable(True)
        visual = self.window.get_screen().get_rgba_visual()
        if visual is None:
            raise RuntimeError("The desktop does not offer transparent windows.")
        self.window.set_visual(visual)
        self.area = Gtk.DrawingArea()
        self.area.set_size_request(self.width, self.height)
        self.window.add(self.area)
        self.area.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON_RELEASE_MASK
                             | Gdk.EventMask.POINTER_MOTION_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.area.connect("draw", self._draw)
        self.area.connect("button-press-event", self._press)
        self.area.connect("button-release-event", self._release)
        self.area.connect("motion-notify-event", self._motion)
        self.window.connect("realize", lambda window: self.update_input_shape())
        self.window.connect("size-allocate", self._allocated)
        self.window.connect("delete-event", lambda *args: True)
        self.window.set_default_size(self.width, self.height)
        self.reposition()
        self.window.stick()
        self.window.show_all()

    def _set_window_layer(self, arranging):
        if arranging:
            hint = Gdk.WindowTypeHint.NORMAL
        else:
            hint = Gdk.WindowTypeHint.DESKTOP
        self.window.set_type_hint(hint)
        self.window.set_keep_above(arranging)
        self.window.set_keep_below(False)

    def set_arranging(self, arranging):
        if arranging != self.window_arranging:
            # GNOME's desktop layer can pass pointer input to desktop icons.
            # Remap as an ordinary window while editing so handles receive it.
            self.window.hide()
            self._set_window_layer(arranging)
            self.window_arranging = arranging
            self.window.show_all()
            self.window.stick()
            self.reposition()
        self.update_input_shape()
        self.area.queue_draw()

    def _allocated(self, window, allocation):
        if allocation.width != self.width or allocation.height != self.height:
            self.width, self.height = allocation.width, allocation.height
        self.update_input_shape()

    def reposition(self):
        bounds = self.app.workarea
        x, y = clamp_position(self.layout["x"] - bounds.x, self.layout["y"] - bounds.y,
                              self.width, self.height, bounds.width, bounds.height)
        self.layout.update(x=x + bounds.x, y=y + bounds.y)
        self.window.move(self.layout["x"], self.layout["y"])
        if self.identifier in ("player", "visualizer"):
            self.area.queue_draw()

    def resize(self, size):
        previous_width = self.width
        if self.identifier == "player":
            self.width = max(480, min(1000, round(size)))
            self.height = round(self.width * 350 / 640)
            self.layout["width"] = self.width
        elif self.identifier == "visualizer":
            self.width = max(400, min(1000, round(size)))
            self.height = round(self.width / 4)
            self.layout["width"] = self.width
        else:
            self.width = self.height = max(160, min(1000, round(size)))
            self.layout["size"] = self.width
        self.area.set_size_request(self.width, self.height)
        self.window.resize(self.width, self.height)
        self.reposition()
        self.update_input_shape()
        self.area.queue_draw()
        if self.width != previous_width and self.app.settings:
            self.app.settings.sync_size(self.identifier, self.width)

    def _draw(self, area, context):
        context.set_operator(cairo.OPERATOR_SOURCE)
        context.set_source_rgba(0, 0, 0, 0)
        context.paint()
        context.set_operator(cairo.OPERATOR_OVER)
        self.render(context)
        if self.app.arranging:
            spacing = self.app.config.get("snap_spacing", 24) if self.app.config.get("snap_enabled", True) else None
            bounds = self.app.workarea
            offset = (bounds.x - self.layout["x"], bounds.y - self.layout["y"])
            drawing.arrange_overlay(context, self.width, self.height, self.title, spacing, offset)
        return False

    def render(self, context):
        pass

    def rectangles(self):
        return [(0, 0, self.width, self.height)]

    def update_input_shape(self):
        rectangles = (([(0, 0, self.width, self.height)]
                       if self.app.arranging or getattr(self, "seek_track", None) is not None
                       else self.rectangles()))
        self._update_window_input_shape(self.window, rectangles)

    def _update_window_input_shape(self, gtk_window, rectangles):
        window = gtk_window.get_window()
        if window is None:
            return
        region = cairo.Region()
        for x, y, width, height in rectangles:
            # Only visible photos accept clicks; transparent letterbox space
            # stays available to desktop icons and desktop context menus.
            region.union(cairo.RectangleInt(math.floor(x), math.floor(y), math.ceil(width), math.ceil(height)))
        window.input_shape_combine_region(region, 0, 0)

    def _press(self, area, event):
        if event.button == 3:
            self.context_menu(event)
            return True
        if event.button != 1:
            return False
        if self.app.arranging:
            mode = "resize" if event.x >= self.width - 36 and event.y >= self.height - 36 else "move"
            self.drag = (mode, event.x_root, event.y_root,
                         self.layout["x"], self.layout["y"], self.width)
            return True
        return self.click(event)

    def _motion(self, area, event):
        if self.drag:
            mode, start_x, start_y, x, y, size = self.drag
            dx, dy = event.x_root - start_x, event.y_root - start_y
            if mode == "move":
                target_x, target_y = round(x + dx), round(y + dy)
                snap = getattr(self.app, "snap_position", None)
                if snap:
                    target_x, target_y = snap(self, target_x, target_y)
                self.layout.update(x=round(target_x), y=round(target_y))
                self.reposition()
            else:
                target_size = size + (dx if abs(dx) >= abs(dy) else dy)
                snap = getattr(self.app, "snap_size", None)
                if snap:
                    target_size = snap(self, target_size)
                self.resize(target_size)
            return True
        return self.motion(event)

    def _release(self, area, event):
        if event.button != 1:
            return False
        if self.drag:
            self.drag = None
            self.app.persist()
            return True
        return self.release(event)

    def click(self, event):
        return False

    def motion(self, event):
        return False

    def release(self, event):
        return False

    def context_menu(self, event):
        menu = Gtk.Menu()

        def item(label, action):
            entry = Gtk.MenuItem(label=label)
            entry.connect("activate", lambda widget: action())
            menu.append(entry)

        item("Done arranging" if self.app.arranging else "Arrange widgets", self.app.toggle_arrange)
        if self.identifier not in ("player", "visualizer"):
            item("Next photo", self.next_photo)
            item("Remove this frame", lambda: self.app.remove_frame(self.identifier))
        item("Add photo frame", self.app.add_frame)
        item("Settings…", self.app.show_settings)
        menu.append(Gtk.SeparatorMenuItem())
        item("Quit Gallery Desk", self.app.quit)
        menu.show_all()
        menu.popup_at_pointer(event)
        self.menu = menu

    def destroy(self):
        self.destroyed = True
        self.window.destroy()


class PhotoFrame(DesktopSurface):
    def __init__(self, app, layout, index):
        self.current = None
        self.current_path = None
        self.incoming = None
        self.incoming_path = None
        self.loading = False
        self.generation = 0
        self.fade_started = 0
        self.delay_seconds = None
        self.next_due = time.monotonic() + index * .3
        super().__init__(app, layout["id"], layout["name"], layout, layout["size"], layout["size"])

    def schedule_next(self, now=None):
        self.delay_seconds = random.uniform(self.app.config["interval_min_seconds"],
                                            self.app.config["interval_max_seconds"])
        self.next_due = (time.monotonic() if now is None else now) + self.delay_seconds

    def fraction(self, now=None):
        if self.incoming is None:
            return 0
        duration = min(self.app.config["fade_seconds"], self.app.config["interval_min_seconds"] * .65)
        return fade_fraction((time.monotonic() if now is None else now) - self.fade_started, duration)

    def render(self, context):
        drawing.photo(context, self.width, self.height, self.current, self.incoming,
                      self.fraction(), self.app.config["corner_radius"])
        if self.current is None and self.incoming is None:
            drawing.rounded(context, 0, 0, self.width, self.height, 24)
            context.set_source_rgba(.15, .14, .13, .85)
            context.fill()
            message = "Loading your photos…" if self.app.scanning or self.loading else "Choose more photos in Settings"
            drawing.text(context, message, 24, self.height / 2 - 10, self.width - 48, 12)

    def rectangles(self):
        pictures = [picture for picture in (self.current, self.incoming) if picture is not None]
        return [contain_rect(picture.get_width(), picture.get_height(), self.width, self.height)
                for picture in pictures] or [(0, 0, self.width, self.height)]

    def tick(self, now):
        if self.incoming is not None:
            if self.fraction(now) >= 1:
                self.current, self.current_path = self.incoming, self.incoming_path
                self.incoming = self.incoming_path = None
                self.app.deck.settle(self.identifier, self.current_path)
                self.update_input_shape()
            self.area.queue_draw()
        if now >= self.next_due and not self.loading and self.incoming is None and not self.app.arranging:
            self.next_photo()

    def next_photo(self):
        if self.destroyed or self.loading or self.incoming is not None:
            return
        self.next_due = time.monotonic() + self.app.config["interval_min_seconds"]
        path = self.app.deck.reserve_next(self.identifier)
        if path is None:
            return
        self.loading = True
        generation = self.generation

        def loaded(cached, error):
            if self.destroyed or generation != self.generation:
                return
            self.loading = False
            if not error:
                try:
                    picture = pixbuf(cached)
                except GLib.Error as exception:
                    error = exception
            if error:
                logging.warning("Photo %s: %s", path, error)
                self.app.deck.reject(self.identifier, path)
                self.next_due = time.monotonic() + .1
                return
            if self.current is None:
                self.current, self.current_path = picture, path
                self.app.deck.settle(self.identifier, path)
            else:
                self.incoming, self.incoming_path = picture, path
                self.fade_started = time.monotonic()
            # Draw a fresh delay at every successful image switch, including
            # the first image. Loading time does not eat into its display time.
            self.schedule_next()
            self.update_input_shape()
            self.area.queue_draw()

        self.app.loader.photo(path, loaded)

    def reset(self):
        self.generation += 1
        self.loading = False
        self.current = self.current_path = self.incoming = self.incoming_path = None
        self.delay_seconds = None
        self.app.deck.release(self.identifier)
        self.next_due = time.monotonic() + self.app.frame_index(self) * .3
        self.update_input_shape()
        self.area.queue_draw()

    def destroy(self):
        self.generation += 1
        self.app.deck.release(self.identifier)
        super().destroy()


class AudioVisualizer(DesktopSurface):
    def __init__(self, app, layout):
        self.envelope = SpectrumEnvelope()
        self.last_tick = time.monotonic()
        self.status = "QUIET"
        super().__init__(app, "visualizer", "Audio visualizer", layout,
                         layout["width"], round(layout["width"] / 4))

    def render(self, context):
        glass.paint(context, self.layout, self.width, self.height, 20 * self.width / 640)
        drawing.visualizer(context, self.width, self.height, self.envelope.levels,
                           self.envelope.peaks, self.status)

    def tick(self, now):
        sample = self.app.audio.sample(now)
        changed = self.envelope.update(sample["levels"], now - self.last_tick)
        self.last_tick = now
        status = ("AUDIO UNAVAILABLE" if sample["error"] else
                  "LIVE" if max(sample["levels"]) > .03 else "QUIET")
        if changed or status != self.status:
            self.status = status
            self.area.queue_draw()

    def click(self, event):
        if event.x * 640 / self.width > 586 and event.y * 160 / self.height < 40:
            self.context_menu(event)
        return True


class Turntable(DesktopSurface):
    def __init__(self, app, layout):
        self.angle = 0
        self.arm = Tonearm()
        self.last_tick = time.monotonic()
        self.last_second = -1
        self.seek_track = None
        self.seek_preview = None
        self.hover_control = None
        self.arm_current = PARKED_ANGLE
        self.arm_target = None
        self.arm_from = PARKED_ANGLE
        self.arm_transition_started = None
        self.arm_transition_seconds = .7
        self.desktop_screen = Wnck.Screen.get_default()
        self.desktop_screen.force_update()
        self.showing_desktop = self.desktop_screen.get_showing_desktop()
        super().__init__(app, "player", "Turntable", layout, layout["width"], round(layout["width"] * 350 / 640))
        self.area.connect("leave-notify-event", lambda area, event: self.leave(event))
        self.desktop_signal = self.desktop_screen.connect("showing-desktop-changed", self._desktop_changed)
        self._lower_window()

    def _set_window_layer(self, arranging):
        # The same window owns drawing and input, so a covering application
        # covers both. A normal layer receives hardware input above desktop icons.
        # Only Show Desktop needs a dock: ordinary windows are hidden in that mode.
        hint = Gdk.WindowTypeHint.DOCK if self.showing_desktop and not arranging else Gdk.WindowTypeHint.NORMAL
        if getattr(self, "_layer_hint", None) != hint:
            self.window.set_type_hint(hint)
            self._layer_hint = hint
        # Layer remapping can make the WM discard these hints; reapply them
        # after every Arrange/Show Desktop transition so the widget stays out
        # of task switching and the pager.
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)
        self.window.set_keep_above(arranging)
        # _NET_WM_STATE_BELOW also goes under GNOME's transparent desktop-icon
        # surface, which prevents physical clicks from reaching the controls.
        # Lowering a normal window keeps it below applications while retaining
        # input above that desktop surface.
        self.window.set_keep_below(False)
        if not arranging and not self.showing_desktop:
            GLib.idle_add(self._lower_window)

    def _desktop_changed(self, screen):
        self.showing_desktop = screen.get_showing_desktop()
        # Remap so GNOME applies the new type to both visibility and input.
        self.window.hide()
        self._set_window_layer(self.app.arranging)
        self.window.show_all()
        self.window.deiconify()
        self.window.stick()
        self._hide_from_task_switchers()
        self._lower_window()
        self.reposition()
        self.update_input_shape()

    def set_arranging(self, arranging):
        if arranging != self.window_arranging:
            # Keep the mapped NORMAL window in place; hiding/showing it makes
            # Mutter drop the skip-taskbar state during Arrange transitions.
            self._set_window_layer(arranging)
            self.window_arranging = arranging
        self.update_input_shape()
        self.area.queue_draw()
        self._hide_from_task_switchers()
        if not arranging and not self.showing_desktop:
            self._lower_window()

    def _hide_from_task_switchers(self):
        self.window.set_skip_taskbar_hint(True)
        self.window.set_skip_pager_hint(True)

    def _lower_window(self):
        if getattr(self, "destroyed", False):
            return GLib.SOURCE_REMOVE
        gtk_window = getattr(self, "window", None)
        if gtk_window is None:
            return GLib.SOURCE_REMOVE
        window = gtk_window.get_window()
        if window is not None:
            window.lower()
        return GLib.SOURCE_REMOVE

    def destroy(self):
        self.desktop_screen.disconnect(self.desktop_signal)
        super().destroy()

    def rectangles(self):
        # Hit areas use the renderer's 640 × 350 coordinates. The vinyl and
        # background pass clicks through; Arrange mode uses the whole window.
        controls = [(586, 0, 54, 50), (314, 212, 305, 33),
                    (376, 265, 54, 51), (439, 265, 54, 51),
                    (502, 265, 54, 51), (530, 317, 110, 33)]
        return [(x * self.width / 640, y * self.height / 350,
                 width * self.width / 640, height * self.height / 350)
                for x, y, width, height in controls]

    def render(self, context):
        media = self.app.media
        arm_angle = self._update_arm_target(time.monotonic())
        glass.paint(context, self.layout, self.width, self.height, 25 * self.width / 640)
        drawing.player(context, self.width, self.height, media, self.angle, self.seek_preview,
                       arm_angle=arm_angle, hover_control=getattr(self, "hover_control", None))

    def _update_arm_target(self, now):
        target = self.arm.angle_for(self.app.media.track_id, self.app.media.clock.playing)
        if self.arm_target is None:
            self.arm_current = self.arm_target = target
            self.arm_from = target
            self.arm_transition_started = None
            return target
        if abs(target - self.arm_target) > 1e-9:
            self.arm_current = self._arm_position(now)
            self.arm_from = self.arm_current
            self.arm_target = target
            self.arm_transition_started = now
        return self._arm_position(now)

    def _arm_position(self, now):
        if self.arm_transition_started is None:
            return self.arm_current
        progress = max(0, min(1, (now - self.arm_transition_started) / self.arm_transition_seconds))
        eased = progress * progress * (3 - 2 * progress)
        self.arm_current = self.arm_from + (self.arm_target - self.arm_from) * eased
        if progress >= 1 - 1e-9:
            self.arm_transition_started = None
            self.arm_current = self.arm_target
        return self.arm_current

    def tick(self, now):
        elapsed, self.last_tick = now - self.last_tick, now
        previous_arm = self.arm_current
        arm_angle = self._update_arm_target(now)
        if self.app.media.clock.playing:
            self.angle = (self.angle + elapsed * 2 * math.pi / 12) % (2 * math.pi)
            self.area.queue_draw()
        elif abs(arm_angle - previous_arm) > 1e-5:
            self.area.queue_draw()
        elif int(now) != self.last_second:
            self.area.queue_draw()
        self.last_second = int(now)

    def click(self, event):
        x, y = event.x * 640 / self.width, event.y * 350 / self.height
        if x > 586 and y < 50:
            self.context_menu(event)
        elif 314 <= x <= 619 and 212 <= y <= 245 and self.app.media.properties.get("CanSeek", False):
            self.seek_track = self.app.media.track_id
            self.seek_preview = max(0, min(1, (x - 324) / 285))
            self.update_input_shape()
            self.area.queue_draw()
        elif 265 <= y <= 316:
            for method, cx in (("Previous", 403), ("PlayPause", 466), ("Next", 529)):
                if abs(x - cx) < 27:
                    if not self.app.media.connected and method == "PlayPause":
                        self.app.media.open_spotify()
                    else:
                        self.app.media.command(method)
        elif (x > 530 and y > 312) or (not self.app.media.connected and x > 300):
            self.app.media.open_spotify()
        return True

    def motion(self, event):
        if self.seek_track is not None:
            self.seek_preview = max(0, min(1, (event.x * 640 / self.width - 324) / 285))
            self._set_hover("seek")
            self.area.queue_draw()
            return True
        self._set_hover(self._control_name(event))
        return getattr(self, "hover_control", None) is not None

    def leave(self, event):
        self._set_hover(None)
        return False

    def _control_name(self, event):
        x, y = event.x * 640 / self.width, event.y * 350 / self.height
        if x > 586 and y < 50:
            return "menu"
        if 314 <= x <= 619 and 212 <= y <= 245:
            return "seek"
        if 265 <= y <= 316:
            for name, center in (("previous", 403), ("play", 466), ("next", 529)):
                if abs(x - center) < 27:
                    return name
        if x > 530 and y > 312:
            return "spotify"
        return None

    def _set_hover(self, control):
        if control != getattr(self, "hover_control", None):
            self.hover_control = control
            self.area.queue_draw()

    def release(self, event):
        if self.seek_track is not None:
            # Use the release position even if GTK compressed the last motion.
            fraction = max(0, min(1, (event.x * 640 / self.width - 324) / 285))
            self.app.media.seek(fraction, self.seek_track)
            self.seek_track = self.seek_preview = None
            self.update_input_shape()
            self.area.queue_draw()
            return True
        return False
