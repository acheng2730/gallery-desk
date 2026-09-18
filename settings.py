"""Small settings window for the photo collection, frame sizes, and desktop layout."""

import copy
from gi.repository import Gtk

from core import validate_config


class Settings:
    def __init__(self, app):
        self.app = app
        self.window = Gtk.ApplicationWindow(application=app, title="Gallery Desk")
        self.window.set_default_size(650, 700)
        self.window.set_border_width(22)
        self.window.connect("destroy", lambda window: setattr(app, "settings", None))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.window.add(box)
        heading = Gtk.Label(xalign=0)
        heading.set_markup('<span size="xx-large" weight="bold">Gallery Desk</span>')
        box.pack_start(heading, False, False, 0)
        self.arrange = Gtk.Button(label="Done arranging" if app.arranging else "Arrange widgets on the desktop")
        self.arrange.connect("clicked", self._arrange)
        box.pack_start(self.arrange, False, False, 0)
        self.folder = Gtk.FileChooserButton(title="Choose your photo folder", action=Gtk.FileChooserAction.SELECT_FOLDER)
        self.folder.set_filename(app.config["photo_directory"])
        box.pack_start(Gtk.Label(label="Photo folder (includes subfolders)", xalign=0), False, False, 0)
        box.pack_start(self.folder, False, False, 0)
        grid = Gtk.Grid(column_spacing=16, row_spacing=10)
        box.pack_start(grid, False, False, 0)
        grid.attach(Gtk.Label(label="Change photos every (seconds)", xalign=0), 0, 0, 1, 1)
        interval_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.interval_min = Gtk.SpinButton.new_with_range(2, 3600, 1)
        self.interval_max = Gtk.SpinButton.new_with_range(2, 3600, 1)
        for spin, key, label in ((self.interval_min, "interval_min_seconds", "Minimum photo interval"),
                                  (self.interval_max, "interval_max_seconds", "Maximum photo interval")):
            spin.set_value(app.config[key])
            spin.set_numeric(True)
            spin.set_width_chars(4)
            spin.get_accessible().set_name(label)
        interval_box.pack_start(self.interval_min, False, False, 0)
        interval_box.pack_start(Gtk.Label(label="to"), False, False, 0)
        interval_box.pack_start(self.interval_max, False, False, 0)
        grid.attach(interval_box, 1, 0, 1, 1)
        range_hint = Gtk.Label(label="Each frame chooses a new random delay after every photo change.", xalign=0)
        range_hint.set_line_wrap(True)
        grid.attach(range_hint, 0, 1, 2, 1)
        self.fade = self._spin(grid, 2, "Fade duration (seconds)", app.config["fade_seconds"], .1, 3, .1)
        self.radius = self._spin(grid, 3, "Photo corner radius", app.config["corner_radius"], 0, 80, 1)
        self.player_width = self._spin(grid, 4, "Turntable width", app.config["player"]["width"], 480, 1000, 10)
        grid.attach(Gtk.Label(label="Audio visualizer", xalign=0), 0, 5, 1, 1)
        self.visualizer_enabled = Gtk.CheckButton(label="Show spectrum")
        self.visualizer_enabled.set_active(app.config["visualizer"]["enabled"])
        grid.attach(self.visualizer_enabled, 1, 5, 1, 1)
        self.visualizer_width = self._spin(grid, 6, "Visualizer width", app.config["visualizer"]["width"], 400, 1000, 10)
        label = Gtk.Label(label="Photo frames · size is the longest side in pixels", xalign=0)
        box.pack_start(label, False, False, 0)
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_min_content_height(155)
        box.pack_start(scroll, True, True, 0)
        self.frame_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        scroll.add(self.frame_box)
        self.rows = []
        for frame in app.config["frames"]:
            self._frame_row(frame)
        add = Gtk.Button(label="Add another frame")
        add.connect("clicked", lambda button: self._add_row())
        box.pack_start(add, False, False, 0)
        self.error = Gtk.Label(xalign=0)
        self.error.set_line_wrap(True)
        box.pack_start(self.error, False, False, 0)
        footer = Gtk.ButtonBox(orientation=Gtk.Orientation.HORIZONTAL)
        footer.set_layout(Gtk.ButtonBoxStyle.END)
        box.pack_start(footer, False, False, 0)
        close = Gtk.Button(label="Close")
        close.connect("clicked", lambda button: self.window.destroy())
        footer.add(close)
        apply = Gtk.Button(label="Apply settings")
        apply.get_style_context().add_class("suggested-action")
        apply.connect("clicked", self._apply)
        footer.add(apply)
        self.window.show_all()

    def _spin(self, grid, row, label, value, low, high, step):
        grid.attach(Gtk.Label(label=label, xalign=0), 0, row, 1, 1)
        spin = Gtk.SpinButton.new_with_range(low, high, step)
        spin.set_digits(1 if step < 1 else 0)
        spin.set_value(value)
        spin.set_numeric(True)
        grid.attach(spin, 1, row, 1, 1)
        return spin

    def _frame_row(self, frame):
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        name = Gtk.Entry(text=frame["name"])
        name.set_max_length(80)
        name.set_width_chars(15)
        row.pack_start(name, True, True, 0)
        size = Gtk.SpinButton.new_with_range(160, 1000, 10)
        size.set_value(frame["size"])
        size.set_numeric(True)
        row.pack_start(size, False, False, 0)
        remove = Gtk.Button.new_from_icon_name("list-remove-symbolic", Gtk.IconSize.BUTTON)
        remove.set_tooltip_text("Remove frame")
        row.pack_start(remove, False, False, 0)
        item = (row, copy.deepcopy(frame), name, size)
        self.rows.append(item)
        remove.connect("clicked", lambda button: self._remove_row(item))
        self.frame_box.pack_start(row, False, False, 0)
        row.show_all()

    def sync_size(self, identifier, value):
        # Refresh only the resized widget, preserving other unapplied edits.
        if identifier == "player":
            self.player_width.set_value(value)
        elif identifier == "visualizer":
            self.visualizer_width.set_value(value)
        else:
            for row, frame, name, size in self.rows:
                if frame["id"] == identifier:
                    size.set_value(value)
                    break

    def _remove_row(self, item):
        self.rows.remove(item)
        item[0].destroy()

    def _add_row(self):
        if len(self.rows) >= 12:
            self.error.set_text("You can use up to 12 photo frames.")
            return
        identifiers = {item[1]["id"] for item in self.rows}
        self._frame_row(self.app.new_frame_layout(identifiers))

    def _arrange(self, button):
        self.app.toggle_arrange()
        if self.app.arranging:
            self.window.hide()

    def _apply(self, button):
        config = copy.deepcopy(self.app.config)
        config.update(photo_directory=self.folder.get_filename() or "",
                      interval_min_seconds=self.interval_min.get_value(),
                      interval_max_seconds=self.interval_max.get_value(),
                      fade_seconds=self.fade.get_value(),
                      corner_radius=self.radius.get_value())
        config["player"]["width"] = round(self.player_width.get_value())
        config["visualizer"].update(enabled=self.visualizer_enabled.get_active(),
                                    width=round(self.visualizer_width.get_value()))
        current = {frame["id"]: frame for frame in self.app.config["frames"]}
        config["frames"] = []
        for row, frame, name, size in self.rows:
            updated = copy.deepcopy(current.get(frame["id"], frame))
            updated.update(name=name.get_text(), size=round(size.get_value()))
            config["frames"].append(updated)
        try:
            validate_config(config)
            self.app.apply_settings(config)
        except (ValueError, OSError) as error:
            self.error.set_text(str(error))
            return
        self.error.set_text("Saved. Right-click any widget to arrange it.")


class ArrangeToolbar:
    def __init__(self, app):
        self.window = Gtk.ApplicationWindow(application=app, title="Arrange Gallery Desk")
        self.window.set_resizable(False)
        self.window.set_keep_above(True)
        self.window.set_border_width(14)
        self.window.set_position(Gtk.WindowPosition.CENTER)
        self.window.connect("delete-event", lambda *args: app.toggle_arrange() or True)
        self.window.connect("key-press-event", lambda window, event: app.toggle_arrange() if event.keyval == 65307 else False)
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=14)
        self.window.add(box)
        box.pack_start(Gtk.Label(label="Drag a widget to move it. Drag its lower-right corner to resize."), True, True, 0)
        snap = Gtk.CheckButton(label="Snap")
        snap.set_tooltip_text("Snap to a grid and equalize gaps between widgets")
        snap.set_active(app.config.get("snap_enabled", True))
        snap.connect("toggled", lambda button: app.set_snap_enabled(button.get_active()))
        box.pack_start(snap, False, False, 0)
        spacing = Gtk.SpinButton.new_with_range(4, 200, 4)
        spacing.set_value(app.config.get("snap_spacing", 24))
        spacing.set_numeric(True)
        spacing.set_width_chars(3)
        spacing.set_tooltip_text("Grid spacing in pixels")
        spacing.connect("value-changed", lambda spin: app.set_snap_spacing(spin.get_value()))
        box.pack_start(Gtk.Label(label="px"), False, False, 0)
        box.pack_start(spacing, False, False, 0)
        add = Gtk.Button(label="Add frame")
        add.connect("clicked", lambda button: app.add_frame())
        box.pack_start(add, False, False, 0)
        done = Gtk.Button(label="Done")
        done.get_style_context().add_class("suggested-action")
        done.connect("clicked", lambda button: app.toggle_arrange())
        box.pack_start(done, False, False, 0)
        self.window.show_all()

    def destroy(self):
        self.window.destroy()
