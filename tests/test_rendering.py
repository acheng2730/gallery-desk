import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import cairo
import gi
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf
from PIL import Image

from assets import scan_photos, upright_thumbnail
from drawing import photo, visualizer
import glass


class RenderingTests(unittest.TestCase):
    def solid(self, color, width=200, height=100):
        picture = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, True, 8, width, height)
        picture.fill(color)
        return picture

    def render(self, current, incoming=None, fraction=0):
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 200, 200)
        photo(cairo.Context(surface), 200, 200, current, incoming, fraction, 20)
        surface.flush()
        return surface

    def pixel(self, surface, x, y):
        offset = y * surface.get_stride() + x * 4
        return tuple(surface.get_data()[offset:offset + 4])

    def test_mid_fade_overlap_is_opaque_and_evenly_mixed(self):
        surface = self.render(self.solid(0xff0000ff), self.solid(0x0000ffff), .5)
        blue, green, red, alpha = self.pixel(surface, 100, 100)
        self.assertEqual(alpha, 255)
        self.assertLessEqual(abs(red - blue), 1)
        self.assertEqual(green, 0)

    def test_landscape_has_transparent_bars_and_rounded_corners(self):
        surface = self.render(self.solid(0xff0000ff))
        self.assertEqual(self.pixel(surface, 100, 25)[3], 0)
        self.assertEqual(self.pixel(surface, 100, 175)[3], 0)
        self.assertEqual(self.pixel(surface, 0, 50)[3], 0)
        self.assertEqual(self.pixel(surface, 100, 100)[3], 255)

    def test_landscape_to_portrait_fade_preserves_both_rectangles(self):
        surface = self.render(self.solid(0xff0000ff), self.solid(0x0000ffff, 100, 200), .5)
        self.assertEqual(self.pixel(surface, 100, 100)[3], 255)
        self.assertIn(self.pixel(surface, 100, 20)[3], (127, 128))
        self.assertIn(self.pixel(surface, 20, 100)[3], (127, 128))
        self.assertEqual(self.pixel(surface, 20, 20)[3], 0)

    def test_visualizer_has_transparent_corners_and_lights_bars_only_with_audio(self):
        quiet = cairo.ImageSurface(cairo.FORMAT_ARGB32, 640, 160)
        active = cairo.ImageSurface(cairo.FORMAT_ARGB32, 640, 160)
        with patch("glass.wallpaper", return_value=self.solid(0x555555ff, 640, 160)):
            for surface in (quiet, active):
                glass.paint(cairo.Context(surface), {"x": 0, "y": 0}, 640, 160, 20)
        visualizer(cairo.Context(quiet), 640, 160, [0] * 32, [0] * 32, "QUIET")
        visualizer(cairo.Context(active), 640, 160, [.8] * 32, [.9] * 32, "LIVE")
        quiet.flush()
        active.flush()
        self.assertEqual(self.pixel(active, 0, 0)[3], 0)
        self.assertGreater(self.pixel(active, 320, 80)[3], 240)
        self.assertGreater(self.pixel(active, 32, 123)[2], self.pixel(quiet, 32, 123)[2] + 50)


class AssetTests(unittest.TestCase):
    def test_thumbnail_applies_exif_rotation_without_modifying_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source, target = Path(directory) / "source.jpg", Path(directory) / "cached.png"
            picture = Image.new("RGB", (80, 40), "red")
            exif = picture.getexif()
            exif[274] = 6
            picture.save(source, exif=exif)
            original_hash = hashlib.sha256(source.read_bytes()).digest()
            upright_thumbnail(source, target, 40)
            with Image.open(target) as result:
                self.assertEqual(result.size, (20, 40))
                self.assertIsNone(result.getexif().get(274))
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), original_hash)

    def test_folder_scan_ignores_hidden_files_and_accepts_nested_photos(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "album").mkdir()
            for name in ("album/a.JPG", ".hidden.jpg", "notes.txt", "b.png"):
                (root / name).touch()
            self.assertEqual(scan_photos(root), [str(root / "album/a.JPG"), str(root / "b.png")])

    def test_missing_photo_folder_reports_error(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError):
                scan_photos(Path(directory) / "missing")
