import math
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import cairo
import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from PIL import Image

from drawing import tonearm
from tonearm import PARKED_ANGLE, PLAYING_ANGLES, Tonearm
from windows import DesktopSurface, Turntable


class TonearmTrackTests(unittest.TestCase):
    def test_track_changes_choose_new_angles_and_repaints_keep_them(self):
        arm = Tonearm()
        first, second = PLAYING_ANGLES[0], PLAYING_ANGLES[-1]
        with patch("tonearm.random.choice", side_effect=(first, second, first)) as choose:
            self.assertEqual(arm.angle_for("/track/one", True), first)
            self.assertEqual(arm.angle_for("/track/one", True), first)
            self.assertEqual(choose.call_count, 1)
            self.assertEqual(arm.angle_for("/track/two", True), second)
            self.assertTrue(all(abs(angle - first) >= math.radians(1.9)
                                for angle in choose.call_args.args[0]))
            self.assertEqual(arm.angle_for("/track/one", True), first)
            self.assertEqual(choose.call_count, 3)

    def test_pause_and_resume_park_and_restore_the_same_track_angle(self):
        arm = Tonearm()
        with patch("tonearm.random.choice", return_value=PLAYING_ANGLES[4]) as choose:
            playing = arm.angle_for("/track/one", True)
            self.assertEqual(arm.angle_for("/track/one", False), PARKED_ANGLE)
            self.assertEqual(arm.angle_for("/track/one", True), playing)
            choose.assert_called_once()

    def test_track_changed_while_paused_gets_its_own_angle_on_resume(self):
        arm = Tonearm()
        with patch("tonearm.random.choice", side_effect=(PLAYING_ANGLES[0], PLAYING_ANGLES[-1])) as choose:
            arm.angle_for("/track/one", True)
            self.assertEqual(arm.angle_for("/track/two", False), PARKED_ANGLE)
            self.assertEqual(arm.angle_for("/track/two", True), PLAYING_ANGLES[-1])
            self.assertEqual(choose.call_count, 2)

    def test_missing_track_parks_without_losing_the_last_track_choice(self):
        arm = Tonearm()
        with patch("tonearm.random.choice", return_value=PLAYING_ANGLES[7]) as choose:
            playing = arm.angle_for("/track/one", True)
            for track in (None, "", "/org/mpris/MediaPlayer2/TrackList/NoTrack"):
                self.assertEqual(arm.angle_for(track, True), PARKED_ANGLE)
            self.assertEqual(arm.angle_for("/track/one", True), playing)
            choose.assert_called_once()

    def test_turntable_render_passes_current_track_angle_and_preserves_seek_preview(self):
        media = SimpleNamespace(track_id="/track/one", clock=SimpleNamespace(playing=True))
        app = SimpleNamespace(media=media)
        with patch.object(DesktopSurface, "__init__", lambda table, *args: setattr(table, "area", Mock())), \
                patch("windows.Wnck.Screen.get_default"):
            table = Turntable(app, {"width": 640})
        table.app, table.width, table.height = app, 640, 350
        table.layout = {"x": 0, "y": 0}
        table.seek_preview = .25
        context = Mock()
        with patch("tonearm.random.choice", return_value=PLAYING_ANGLES[3]), patch("drawing.player") as draw, patch("glass.paint"):
            table.render(context)
            draw.assert_called_with(context, 640, 350, media, 0, .25,
                                    arm_angle=PLAYING_ANGLES[3], hover_control=None)
            media.clock.playing = False
            table.render(context)
            self.assertEqual(draw.call_args.kwargs["arm_angle"], PLAYING_ANGLES[3])
            table._update_arm_target(table.arm_transition_started + table.arm_transition_seconds)
            table.render(context)
            self.assertEqual(draw.call_args.kwargs["arm_angle"], PARKED_ANGLE)

    def test_pause_and_resume_arm_position_is_smooth(self):
        media = SimpleNamespace(track_id="/track/one",
                                clock=SimpleNamespace(playing=False))
        app = SimpleNamespace(media=media)
        with patch.object(DesktopSurface, "__init__", lambda table, *args: setattr(table, "area", Mock())), \
                patch("windows.Wnck.Screen.get_default"):
            table = Turntable(app, {"width": 640})
        table.app = app
        table.arm.angle_for = Mock(side_effect=(PARKED_ANGLE, PLAYING_ANGLES[4],
                                                PLAYING_ANGLES[4], PLAYING_ANGLES[4]))
        table.arm_target = PARKED_ANGLE
        table.arm_current = PARKED_ANGLE
        table.arm_transition_started = None
        table._update_arm_target(10)
        media.clock.playing = True
        table._update_arm_target(10.35)
        halfway = table._update_arm_target(10.7)
        self.assertGreater(halfway, PARKED_ANGLE)
        self.assertLess(halfway, PLAYING_ANGLES[4])
        self.assertEqual(table._update_arm_target(11.1), PLAYING_ANGLES[4])


class HeadMaskContext:
    """Copy the head's actual fill path and transform into a separate mask."""

    def __init__(self, context, head_context):
        self.context = context
        self.head_context = head_context

    def __getattr__(self, name):
        return getattr(self.context, name)

    def fill(self):
        if self.context.get_source().get_rgba()[:3] == (.25, .24, .23):
            self.head_context.set_matrix(self.context.get_matrix())
            self.head_context.append_path(self.context.copy_path())
            self.head_context.set_source_rgba(1, 1, 1, 1)
            self.head_context.fill()
        self.context.fill()


class TonearmClearanceTests(unittest.TestCase):
    def pixels(self, angle, width):
        scale = width / 640
        height = round(350 * scale)
        surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        head_surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
        context = cairo.Context(surface)
        context.scale(scale, scale)
        tonearm(HeadMaskContext(context, cairo.Context(head_surface)), angle)
        arm = self.visible_pixels(surface, width, height, scale)
        head = self.visible_pixels(head_surface, width, height, scale)
        self.assertGreater(len(head), 30)
        return arm, head

    def visible_pixels(self, surface, width, height, scale):
        surface.flush()
        picture = Image.frombytes("RGBA", (width, height), bytes(surface.get_data()),
                                  "raw", "BGRA", surface.get_stride())
        bounds = picture.getchannel("A").getbbox()
        self.assertIsNotNone(bounds)
        cropped = picture.crop(bounds)
        points = []
        for index, (red, green, blue, alpha) in enumerate(cropped.getdata()):
            if not alpha:
                continue
            point = ((bounds[0] + index % cropped.width + .5) / scale,
                     (bounds[1] + index // cropped.width + .5) / scale)
            points.append(point)
        return points

    def test_all_playing_angles_keep_head_on_black_vinyl_and_entire_arm_off_album(self):
        for width in (480, 640, 1000):
            for angle in PLAYING_ANGLES:
                with self.subTest(width=width, degrees=math.degrees(angle)):
                    arm, head = self.pixels(angle, width)
                    self.assertGreater(min(math.dist(point, (153, 184)) for point in arm), 59)
                    self.assertGreater(min(math.dist(point, (153, 184)) for point in head), 59)
                    self.assertLess(max(math.dist(point, (153, 184)) for point in head), 118)

    def test_parked_head_clears_platter_and_entire_arm_clears_text(self):
        for width in (480, 640, 1000):
            with self.subTest(width=width):
                arm, head = self.pixels(PARKED_ANGLE, width)
                self.assertGreater(min(math.dist(point, (153, 184)) for point in head), 130)
                # Text starts at x=324; the heading and footer occupy y<45 and y>310.
                self.assertLess(max(x for x, y in arm), 318)
                self.assertGreater(min(y for x, y in arm), 55)
                self.assertLess(max(y for x, y in arm), 300)
