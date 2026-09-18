import unittest
from unittest.mock import patch

import cairo
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib
from PIL import Image

from drawing import frosted_panel
from glass import blur_wallpaper, paint


class FrostedPanelTests(unittest.TestCase):
    def pixel(self, surface, x, y):
        surface.flush()
        offset = y * surface.get_stride() + x * 4
        return tuple(surface.get_data()[offset:offset + 4])

    def test_wallpaper_blur_softens_edges_and_preserves_flat_regions(self):
        source = Image.new("RGB", (256, 128), "black")
        source.paste("white", (128, 0, 256, 128))
        blurred = blur_wallpaper(source, 256, 128)
        self.assertEqual(blurred.getpixel((0, 64)), (0, 0, 0))
        self.assertEqual(blurred.getpixel((255, 64)), (255, 255, 255))
        self.assertTrue(80 < blurred.getpixel((128, 64))[0] < 175)

    def test_moving_and_resizing_panel_keeps_wallpaper_at_desktop_coordinates(self):
        pixels = bytes(channel for y in range(200) for x in range(240) for channel in (x, y, 50))
        picture = GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(pixels), GdkPixbuf.Colorspace.RGB,
                                                  False, 8, 240, 200, 240 * 3)
        with patch("glass.wallpaper", return_value=picture):
            for x, width in ((20, 100), (40, 160)):
                surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, 100)
                paint(cairo.Context(surface), {"x": x, "y": 30}, width, 100, 20)
                self.assertEqual(self.pixel(surface, 50, 50), (50, 80, x + 50, 255))
                self.assertEqual(self.pixel(surface, 0, 0)[3], 0)

    def test_glass_tint_is_translucent_and_keeps_rounded_corners(self):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 100)
        frosted_panel(cairo.Context(surface), 200, 100, 20)
        self.assertEqual(self.pixel(surface, 0, 0)[3], 0)
        self.assertTrue(60 < self.pixel(surface, 100, 50)[3] < 130)
