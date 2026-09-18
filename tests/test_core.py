import copy
import json
from pathlib import Path
import tempfile
import unittest

from core import DEFAULTS, PhotoDeck, PlaybackClock, conky_gap, contain_rect, fade_fraction, load_config, save_config, seek_arguments, snap_axis, validate_config


class GeometryTests(unittest.TestCase):
    def test_contain_landscape_preserves_ratio_and_centers(self):
        self.assertEqual(contain_rect(1600, 900, 400), (0, 87.5, 400, 225))

    def test_contain_portrait_preserves_ratio_and_centers(self):
        self.assertEqual(contain_rect(900, 1600, 400), (87.5, 0, 225, 400))

    def test_contain_extreme_panorama_remains_inside_footprint(self):
        x, y, width, height = contain_rect(10000, 300, 360)
        self.assertEqual(width, 360)
        self.assertAlmostEqual(height, 10.8)
        self.assertAlmostEqual(y + height / 2, 180)

    def test_fade_clamps_endpoints_and_has_equal_midpoint(self):
        self.assertEqual([fade_fraction(t, 1) for t in [-1, 0, .5, 1, 2]], [0, 0, .5, 1, 1])

    def test_snap_axis_prefers_equal_gaps_over_a_nearer_grid_line(self):
        others = [(100, 100, 0, 100), (340, 100, 0, 100)]
        self.assertEqual(snap_axis(218, 80, 0, 100, others, spacing=24), 230)

    def test_snap_axis_falls_back_to_grid_when_equal_spacing_is_not_nearby(self):
        others = [(100, 100, 0, 100), (340, 100, 0, 100)]
        self.assertEqual(snap_axis(38, 80, 0, 100, others, spacing=24), 48)

    def test_conky_position_accounts_for_xft_scaling_and_inner_border(self):
        self.assertEqual(conky_gap(47, 120, 13), 50)
        self.assertEqual(conky_gap(2157, 120, 13), 1738)
        self.assertEqual(conky_gap(1098, 120, 13), 891)
        self.assertEqual(conky_gap(47, 96, 13), 60)


class PhotoSelectionTests(unittest.TestCase):
    def test_frames_never_share_outgoing_or_incoming_photos(self):
        deck = PhotoDeck([f"photo-{i}" for i in range(8)], seed=3)
        current = {frame: deck.reserve_next(frame) for frame in ["a", "b", "c", "d"]}
        incoming = {frame: deck.reserve_next(frame) for frame in current}
        self.assertEqual(len(set(current.values()) | set(incoming.values())), 8)
        self.assertIsNone(deck.reserve_next("extra"))
        deck.settle("a", incoming["a"])
        self.assertEqual(deck.reserve_next("extra"), current["a"])

    def test_too_few_images_leaves_extra_frame_empty(self):
        deck = PhotoDeck(["one"], seed=1)
        self.assertEqual(deck.reserve_next("a"), "one")
        self.assertIsNone(deck.reserve_next("b"))

    def test_rejected_image_is_released_and_not_reselected(self):
        deck = PhotoDeck(["one", "two"], seed=1)
        bad = deck.reserve_next("a")
        deck.reject("a", bad)
        self.assertNotEqual(deck.reserve_next("a"), bad)


class PlaybackTests(unittest.TestCase):
    def test_playing_clock_advances_and_paused_clock_stays_fixed(self):
        clock = PlaybackClock()
        clock.update(10, position=20_000_000, length=100_000_000, playing=True)
        self.assertEqual(clock.at(12.5), 22_500_000)
        clock.update(13, playing=False)
        self.assertEqual(clock.at(40), 23_000_000)

    def test_track_change_resets_position_and_clamps_track_end(self):
        clock = PlaybackClock()
        clock.update(0, position=90_000_000, length=100_000_000, playing=True)
        self.assertEqual(clock.at(20), 100_000_000)
        clock.update(21, position=0, length=60_000_000)
        self.assertEqual(clock.at(22), 1_000_000)

    def test_seek_includes_track_id_and_microseconds(self):
        self.assertEqual(seek_arguments("/track/one", .25, 240_000_000, True), ("/track/one", 60_000_000))
        self.assertIsNone(seek_arguments("/track/one", .25, 240_000_000, False))
        self.assertIsNone(seek_arguments("/org/mpris/MediaPlayer2/TrackList/NoTrack", .5, 50, True))


class SettingsTests(unittest.TestCase):
    def test_old_settings_add_visualizer_without_changing_existing_widgets(self):
        config = copy.deepcopy(DEFAULTS)
        config.pop("visualizer")
        config["player"].update(x=1476, y=961)
        migrated = validate_config(config)
        self.assertTrue(migrated["visualizer"]["enabled"])
        for section in ("player", "frames", "graphs"):
            self.assertEqual(migrated[section], config[section])

    def test_visualizer_geometry_and_visibility_survive_save_and_load(self):
        config = copy.deepcopy(DEFAULTS)
        config["visualizer"].update(x=1476, y=1335, width=600, enabled=False)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_config(path, config)
            self.assertEqual(load_config(path)["visualizer"], config["visualizer"])

    def test_invalid_visualizer_widths_positions_and_visibility_are_rejected(self):
        for key, value in (("width", 399), ("width", 1001), ("width", float("nan")),
                           ("enabled", "true"), ("enabled", 1), ("x", float("inf"))):
            with self.subTest(key=key, value=value):
                config = copy.deepcopy(DEFAULTS)
                config["visualizer"][key] = value
                with self.assertRaises(ValueError):
                    validate_config(config)

    def test_old_fixed_intervals_migrate_to_default_range_without_changing_layout(self):
        config = copy.deepcopy(DEFAULTS)
        config["interval_seconds"] = 15
        config["frames"][0].update(x=213, y=165, size=500)
        config["frames"][0]["interval_seconds"] = 12
        config.pop("interval_min_seconds")
        config.pop("interval_max_seconds")
        migrated = validate_config(config)
        self.assertEqual((migrated["interval_min_seconds"], migrated["interval_max_seconds"]), (15, 30))
        self.assertNotIn("interval_seconds", migrated)
        self.assertNotIn("interval_seconds", migrated["frames"][0])
        self.assertEqual(migrated["frames"][0]["size"], 500)
        self.assertEqual(migrated["frames"][0]["x"], 213)

    def test_photo_interval_range_survives_saving_and_loading(self):
        config = copy.deepcopy(DEFAULTS)
        config.update(interval_min_seconds=7, interval_max_seconds=45)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_config(path, config)
            loaded = load_config(path)
            self.assertEqual((loaded["interval_min_seconds"], loaded["interval_max_seconds"]), (7, 45))

    def test_invalid_photo_interval_ranges_are_rejected(self):
        for interval in (0, -1, 3601, float("nan"), "15", True):
            with self.subTest(interval=interval):
                config = copy.deepcopy(DEFAULTS)
                config["interval_min_seconds"] = interval
                with self.assertRaises(ValueError):
                    validate_config(config)
        config = copy.deepcopy(DEFAULTS)
        config.update(interval_min_seconds=40, interval_max_seconds=15)
        with self.assertRaises(ValueError):
            validate_config(config)

    def test_equal_range_endpoints_allow_a_fixed_interval(self):
        config = copy.deepcopy(DEFAULTS)
        config.update(interval_min_seconds=20, interval_max_seconds=20)
        self.assertEqual(validate_config(config)["interval_max_seconds"], 20)

    def test_saved_layout_round_trips_positions_sizes_and_timers(self):
        config = copy.deepcopy(DEFAULTS)
        config["frames"][0].update(x=137, y=247, size=512)
        config["interval_min_seconds"] = 18
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            save_config(path, config)
            self.assertEqual(load_config(path), config)

    def test_invalid_duplicate_frames_and_nonfinite_intervals_fail(self):
        config = copy.deepcopy(DEFAULTS)
        config["frames"].append(copy.deepcopy(config["frames"][0]))
        with self.assertRaises(ValueError):
            validate_config(config)
        config = copy.deepcopy(DEFAULTS)
        config["interval_min_seconds"] = float("nan")
        with self.assertRaises(ValueError):
            validate_config(config)


if __name__ == "__main__":
    unittest.main()
