"""Cached wallpaper blur for the two frosted desktop audio panels."""

import logging
from pathlib import Path
from urllib.parse import unquote, urlparse

import gi
gi.require_version("Gdk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk
from PIL import Image, ImageFilter, ImageOps

from drawing import rounded


BLUR_RADIUS = 16
_settings = None
_interface = None
_key = None
_picture = None


def blur_wallpaper(picture, width, height):
    # Match the desktop's zoomed wallpaper before blurring. A single cached
    # screen image keeps the blur aligned while either panel moves or resizes.
    fitted = ImageOps.fit(picture.convert("RGB"), (width, height), method=Image.Resampling.LANCZOS)
    return fitted.filter(ImageFilter.GaussianBlur(BLUR_RADIUS))


def _changed(*args):
    global _key
    _key = None
    application = Gio.Application.get_default()
    if isinstance(application, Gtk.Application):
        for window in application.get_windows():
            if window.get_title() in ("Gallery Desk · Turntable", "Gallery Desk · Audio visualizer"):
                window.queue_draw()


def wallpaper():
    global _settings, _interface, _key, _picture
    screen = Gdk.Screen.get_default()
    if screen is None:
        return None
    if _settings is None:
        _settings = Gio.Settings.new("org.gnome.desktop.background")
        _interface = Gio.Settings.new("org.gnome.desktop.interface")
        _settings.connect("changed", _changed)
        _interface.connect("changed::color-scheme", _changed)
    uri = _settings.get_string("picture-uri")
    if _interface.get_string("color-scheme") == "prefer-dark":
        uri = _settings.get_string("picture-uri-dark") or uri
    path = Path(unquote(urlparse(uri).path))
    width, height = screen.get_width(), screen.get_height()
    key = (uri, width, height)
    if key != _key:
        _key, _picture = key, None
        try:
            with Image.open(path) as source:
                image = blur_wallpaper(source, width, height)
            _picture = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(image.tobytes()),
                                                      GdkPixbuf.Colorspace.RGB, False, 8,
                                                      width, height, width * 3)
        except (OSError, ValueError) as error:
            logging.warning("Loading frosted panel wallpaper: %s", error)
    return _picture


def paint(context, layout, width, height, radius):
    picture = wallpaper()
    if picture is None:
        return
    context.save()
    rounded(context, 0, 0, width, height, radius)
    context.clip()
    Gdk.cairo_set_source_pixbuf(context, picture, -layout["x"], -layout["y"])
    context.paint()
    context.restore()
