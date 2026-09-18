from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")

from gi.repository import Gdk

from windows import DesktopSurface, PhotoFrame, Turntable


class ArrangeInteractionTests(unittest.TestCase):
    def surface(self, identifier="photo-1"):
        surface = DesktopSurface.__new__(DesktopSurface)
        surface.app = SimpleNamespace(arranging=True, persist=Mock(), settings=None,
                                      workarea=SimpleNamespace(x=0, y=0, width=2560, height=1536))
        surface.identifier = identifier
        surface.width, surface.height = (640, 350) if identifier == "player" else (400, 400)
        if identifier == "visualizer":
            surface.width, surface.height = 640, 160
        surface.layout = {"x": 100, "y": 100, "size": 400, "width": 640}
        surface.window = Mock()
        surface.window.get_window.return_value = None
        surface.area = Mock()
        surface.drag = None
        surface.window_arranging = False
        return surface

    def event(self, x, y, root_x=None, root_y=None):
        return SimpleNamespace(button=1, x=x, y=y,
                               x_root=x + 100 if root_x is None else root_x,
                               y_root=y + 100 if root_y is None else root_y)

    def test_drag_moves_frame_and_saves_only_on_release(self):
        surface = self.surface()
        surface._press(None, self.event(20, 20))
        surface._motion(None, self.event(40, 50))
        self.assertEqual((surface.layout["x"], surface.layout["y"]), (120, 130))
        surface.app.persist.assert_not_called()
        surface._release(None, self.event(40, 50))
        surface.app.persist.assert_called_once()
        self.assertIsNone(surface.drag)

    def test_drag_cannot_lose_frame_beyond_workarea(self):
        surface = self.surface()
        surface._press(None, self.event(20, 20))
        surface._motion(None, self.event(20, 20, 9000, 9000))
        self.assertEqual((surface.layout["x"], surface.layout["y"]), (2160, 1136))

    def test_corner_drag_resizes_photo_as_square(self):
        surface = self.surface()
        surface._press(None, self.event(390, 390))
        surface._motion(None, self.event(430, 410))
        self.assertEqual((surface.width, surface.height, surface.layout["size"]), (440, 440, 440))

    def test_corner_drag_preserves_turntable_proportions(self):
        surface = self.surface("player")
        surface._press(None, self.event(630, 340))
        surface._motion(None, self.event(694, 375))
        self.assertEqual((surface.width, surface.height, surface.layout["width"]), (704, 385, 704))

    def test_desktop_resize_updates_settings_before_the_drag_is_released(self):
        for identifier in ("photo-1", "player", "visualizer"):
            with self.subTest(identifier=identifier):
                surface = self.surface(identifier)
                surface.app.settings = Mock()
                surface._press(None, self.event(surface.width - 10, surface.height - 10))
                surface._motion(None, self.event(surface.width + 30, surface.height + 10))
                surface.app.settings.sync_size.assert_called_once_with(identifier, surface.width)
                surface.app.persist.assert_not_called()

    def test_reposition_with_unchanged_size_does_not_overwrite_pending_size_edits(self):
        surface = self.surface()
        surface.app.settings = Mock()
        surface.resize(surface.width)
        surface.app.settings.sync_size.assert_not_called()

    def test_visualizer_corner_drag_preserves_four_to_one_proportions_and_saves(self):
        surface = self.surface("visualizer")
        surface._press(None, self.event(630, 150))
        surface._motion(None, self.event(790, 190))
        self.assertEqual((surface.width, surface.height, surface.layout["width"]), (800, 200, 800))
        surface._release(None, self.event(790, 190))
        surface.app.persist.assert_called_once()

    def test_visualizer_resize_limits_and_clamps_its_whole_rectangle(self):
        surface = self.surface("visualizer")
        surface.layout.update(x=2400, y=1500)
        surface.resize(2000)
        self.assertEqual((surface.width, surface.height), (1000, 250))
        self.assertEqual((surface.layout["x"], surface.layout["y"]), (1560, 1286))
        surface.resize(10)
        self.assertEqual((surface.width, surface.height), (400, 100))

    def test_visualizer_context_menu_does_not_offer_photo_actions(self):
        surface = self.surface("visualizer")
        for name in ("toggle_arrange", "add_frame", "show_settings", "quit"):
            setattr(surface.app, name, Mock())
        with patch("windows.Gtk") as gtk:
            surface.context_menu(None)
        labels = [call.kwargs["label"] for call in gtk.MenuItem.call_args_list]
        self.assertIn("Settings…", labels)
        self.assertNotIn("Next photo", labels)
        self.assertNotIn("Remove this frame", labels)

    def test_arrange_remaps_window_above_desktop_and_done_restores_desktop_type(self):
        surface = self.surface()
        surface.set_arranging(True)
        surface.window.set_type_hint.assert_called_with(Gdk.WindowTypeHint.NORMAL)
        surface.window.set_keep_above.assert_called_with(True)
        names = [call[0] for call in surface.window.method_calls]
        self.assertLess(names.index("hide"), names.index("set_type_hint"))
        self.assertLess(names.index("set_type_hint"), names.index("show_all"))
        surface.set_arranging(False)
        surface.window.set_type_hint.assert_called_with(Gdk.WindowTypeHint.DESKTOP)
        surface.window.set_keep_above.assert_called_with(False)


class TurntableInteractionTests(unittest.TestCase):
    def player(self, width=640):
        player = Turntable.__new__(Turntable)
        player.app = SimpleNamespace(arranging=False, media=Mock(), persist=Mock())
        player.app.media.connected = True
        player.app.media.properties = {"CanSeek": True}
        player.app.media.track_id = "/test/track_one"
        player.identifier = "player"
        player.width, player.height = width, round(width * 350 / 640)
        player.layout = {"x": 100, "y": 100, "width": width}
        player.window = Mock()
        player.area = Mock()
        player.reposition = Mock()
        player.window_arranging = False
        player.drag = player.seek_track = player.seek_preview = None
        player.hover_control = None
        player.showing_desktop = False
        return player

    def event(self, player, x, y=229, button=1):
        return SimpleNamespace(button=button, x=x * player.width / 640,
                               y=y * player.height / 350, x_root=x + 100, y_root=y + 100)

    def test_player_and_controls_share_normal_layer_outside_arrange_mode(self):
        player = self.player()
        player._set_window_layer(False)
        player.window.set_type_hint.assert_called_with(Gdk.WindowTypeHint.NORMAL)
        player.window.set_keep_below.assert_called_with(False)
        player.window.set_keep_above.assert_called_with(False)
        player.app.arranging = True
        player.set_arranging(True)
        player.window.set_type_hint.assert_called_with(Gdk.WindowTypeHint.NORMAL)
        player.window.set_keep_below.assert_called_with(False)
        player.window.set_keep_above.assert_called_with(True)
        player.app.arranging = False
        player.set_arranging(False)
        player.window.set_type_hint.assert_called_with(Gdk.WindowTypeHint.NORMAL)
        player.window.set_keep_below.assert_called_with(False)
        player.window.set_keep_above.assert_called_with(False)

    def test_scaled_input_shape_accepts_controls_and_passes_decoration_clicks_through(self):
        for width in (480, 640, 1000):
            with self.subTest(width=width):
                player = self.player(width)
                player.update_input_shape()
                region = player.window.get_window().input_shape_combine_region.call_args.args[0]
                for x, y in ((403, 292), (466, 292), (529, 292), (324, 229),
                             (609, 229), (611, 27), (580, 325)):
                    event = self.event(player, x, y)
                    self.assertTrue(region.contains_point(round(event.x), round(event.y)), (x, y))
                for x, y in ((153, 184), (420, 100), (310, 290), (435, 292), (2, 2)):
                    event = self.event(player, x, y)
                    self.assertFalse(region.contains_point(round(event.x), round(event.y)), (x, y))
                player.app.arranging = True
                player.update_input_shape()
                region = player.window.get_window().input_shape_combine_region.call_args.args[0]
                self.assertTrue(region.contains_point(2, 2))
                self.assertTrue(region.contains_point(player.width - 2, player.height - 2))

    def test_seek_expands_input_on_the_rendered_window_only(self):
        player = self.player()
        player.update_input_shape()
        overlay_region = player.window.get_window.return_value.input_shape_combine_region.call_args.args[0]
        self.assertTrue(overlay_region.contains_point(466, 292))
        self.assertFalse(overlay_region.contains_point(153, 184))
        player.seek_track = "/test/track_one"
        player.update_input_shape()
        overlay_region = player.window.get_window.return_value.input_shape_combine_region.call_args.args[0]
        self.assertTrue(overlay_region.contains_point(2, 2))
        self.assertTrue(overlay_region.contains_point(player.width - 2, player.height - 2))

    def test_show_desktop_uses_dock_and_restoring_apps_removes_dock_layer(self):
        player = self.player()
        screen = Mock()
        screen.get_showing_desktop.return_value = True
        player._desktop_changed(screen)
        player.window.set_type_hint.assert_called_with(Gdk.WindowTypeHint.DOCK)
        player.window.set_keep_above.assert_called_with(False)
        screen.get_showing_desktop.return_value = False
        player._desktop_changed(screen)
        player.window.set_type_hint.assert_called_with(Gdk.WindowTypeHint.NORMAL)
        player.window.set_keep_below.assert_called_with(False)
        player.window.set_keep_above.assert_called_with(False)

    def test_hover_tracks_each_control_and_clears_when_pointer_leaves(self):
        player = self.player()
        for x, y, name in ((403, 292, "previous"), (466, 292, "play"),
                           (529, 292, "next"), (395, 229, "seek"),
                           (611, 27, "menu"), (580, 325, "spotify")):
            with self.subTest(name=name):
                self.assertTrue(player.motion(self.event(player, x, y)))
                self.assertEqual(player.hover_control, name)
        player.leave(None)
        self.assertIsNone(player.hover_control)

    def test_transport_buttons_dispatch_after_scaling(self):
        for width in (480, 640, 1000):
            for x, method in ((403, "Previous"), (466, "PlayPause"), (529, "Next")):
                with self.subTest(width=width, method=method):
                    player = self.player(width)
                    event = self.event(player, x, 292)
                    self.assertTrue(player._press(None, event))
                    player._release(None, event)
                    player.app.media.command.assert_called_once_with(method)
                    player.app.media.seek.assert_not_called()

    def test_progress_click_seeks_to_fraction_and_clears_preview(self):
        for width in (480, 640, 1000):
            with self.subTest(width=width):
                player = self.player(width)
                event = self.event(player, 324 + 285 / 4)
                player._press(None, event)
                self.assertAlmostEqual(player.seek_preview, .25)
                player.app.media.seek.assert_not_called()
                player._release(None, event)
                fraction, track = player.app.media.seek.call_args.args
                self.assertAlmostEqual(fraction, .25)
                self.assertEqual(track, "/test/track_one")
                self.assertIsNone(player.seek_track)
                self.assertIsNone(player.seek_preview)

    def test_seek_drag_uses_final_release_position_and_original_track(self):
        player = self.player()
        player._press(None, self.event(player, 324))
        player._motion(None, self.event(player, 324 + 285 / 2))
        self.assertAlmostEqual(player.seek_preview, .5)
        player.app.media.seek.assert_not_called()
        player.app.media.track_id = "/test/track_two"
        player._release(None, self.event(player, 324 + 285 * .75))
        player.app.media.seek.assert_called_once_with(.75, "/test/track_one")

    def test_seek_drag_outside_bar_clamps_to_track_endpoints(self):
        for x, fraction in ((-100, 0), (900, 1)):
            with self.subTest(x=x):
                player = self.player()
                player._press(None, self.event(player, 466))
                player._motion(None, self.event(player, x, -100))
                self.assertEqual(player.seek_preview, fraction)
                player._release(None, self.event(player, x, -100))
                player.app.media.seek.assert_called_once_with(fraction, "/test/track_one")

    def test_disabled_seeking_and_middle_click_do_not_send_player_commands(self):
        player = self.player()
        player.app.media.properties["CanSeek"] = False
        event = self.event(player, 466)
        player._press(None, event)
        player._release(None, event)
        self.assertIsNone(player.seek_preview)
        player._press(None, self.event(player, 466, 292, button=2))
        player.app.media.seek.assert_not_called()
        player.app.media.command.assert_not_called()

    def test_arrange_drag_over_controls_moves_widget_without_playback_commands(self):
        player = self.player()
        player.app.arranging = True
        player._press(None, self.event(player, 466, 292))
        player._motion(None, self.event(player, 486, 312))
        player._release(None, self.event(player, 486, 312))
        self.assertEqual((player.layout["x"], player.layout["y"]), (120, 120))
        player.app.persist.assert_called_once()
        player.app.media.command.assert_not_called()
        player.app.media.seek.assert_not_called()

    def test_disconnected_play_and_spotify_link_open_spotify(self):
        for x, y in ((466, 292), (580, 325)):
            with self.subTest(x=x):
                player = self.player()
                player.app.media.connected = False
                player._press(None, self.event(player, x, y))
                player.app.media.open_spotify.assert_called_once()
                player.app.media.command.assert_not_called()


class FrameTimingTests(unittest.TestCase):
    def frame(self, minimum=15, maximum=30):
        frame = PhotoFrame.__new__(PhotoFrame)
        frame.layout = {}
        frame.app = SimpleNamespace(config={"interval_min_seconds": minimum, "interval_max_seconds": maximum, "fade_seconds": 3},
                                    arranging=False, deck=Mock(), loader=Mock())
        frame.app.deck.reserve_next.return_value = "photo.jpg"
        frame.identifier = "test-frame"
        frame.destroyed = frame.loading = False
        frame.incoming = None
        frame.current = object()
        frame.generation = 0
        frame.area = Mock()
        frame.update_input_shape = Mock()
        frame.next_due = 0
        return frame

    def switch(self, frame):
        frame.incoming = None
        frame.next_photo()
        callback = frame.app.loader.photo.call_args.args[1]
        with patch("windows.pixbuf", return_value=object()):
            callback("cached.png", None)

    def test_every_frame_draws_a_fresh_delay_at_every_successful_photo_switch(self):
        first, second = self.frame(), self.frame()
        with patch("windows.random.uniform", side_effect=(15, 30, 22.5)) as sample:
            with patch("windows.time.monotonic", return_value=100):
                self.switch(first)
                self.switch(second)
                self.assertEqual((first.next_due, second.next_due), (115, 130))
            with patch("windows.time.monotonic", return_value=110):
                self.switch(first)
                self.assertEqual(first.next_due, 132.5)
                self.assertEqual(second.next_due, 130)
            self.assertEqual(sample.call_count, 3)
            sample.assert_called_with(15, 30)

    def test_loading_time_does_not_shorten_the_new_photo_delay(self):
        frame = self.frame()
        with patch("windows.time.monotonic", return_value=100):
            frame.next_photo()
        callback = frame.app.loader.photo.call_args.args[1]
        with patch("windows.time.monotonic", return_value=105), patch("windows.random.uniform", return_value=20), patch("windows.pixbuf", return_value=object()):
            callback("cached.png", None)
        self.assertEqual(frame.next_due, 125)

    def test_fade_is_capped_by_minimum_interval(self):
        fast = self.frame(2, 30)
        fast.incoming = object()
        fast.fade_started = 100
        self.assertAlmostEqual(fast.fraction(100.65), .5)
