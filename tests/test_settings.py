import copy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

import gi
gi.require_version("Gtk", "3.0")

from core import DEFAULTS
from settings import Settings


class SizeSyncTests(unittest.TestCase):
    def spin(self, value):
        control = Mock()
        control.get_value.return_value = value
        control.set_value.side_effect = lambda updated: setattr(control.get_value, "return_value", updated)
        return control

    def setUp(self):
        config = copy.deepcopy(DEFAULTS)
        self.settings = Settings.__new__(Settings)
        self.settings.app = SimpleNamespace(config=config, apply_settings=Mock())
        self.settings.rows = [(Mock(), copy.deepcopy(frame), Mock(get_text=Mock(return_value=frame["name"])),
                               self.spin(frame["size"])) for frame in config["frames"]]
        self.settings.player_width = self.spin(config["player"]["width"])
        self.settings.visualizer_width = self.spin(config["visualizer"]["width"])

    def test_photo_resize_refreshes_only_the_matching_size_control(self):
        self.settings.rows[1][3].get_value.return_value = 600
        self.settings.sync_size("photo-1", 512)
        self.assertEqual(self.settings.rows[0][3].get_value(), 512)
        self.assertEqual(self.settings.rows[1][3].get_value(), 600)
        self.settings.rows[1][3].set_value.assert_not_called()
        self.settings.player_width.set_value.assert_not_called()
        self.settings.visualizer_width.set_value.assert_not_called()

    def test_turntable_and_visualizer_resizes_refresh_their_width_controls(self):
        self.settings.sync_size("player", 710)
        self.settings.sync_size("visualizer", 570)
        self.assertEqual(self.settings.player_width.get_value(), 710)
        self.assertEqual(self.settings.visualizer_width.get_value(), 570)
        for row, frame, name, size in self.settings.rows:
            size.set_value.assert_not_called()

    def test_apply_after_desktop_resize_keeps_new_size_and_other_pending_edits(self):
        settings = self.settings
        config = settings.app.config
        settings.folder = Mock(get_filename=Mock(return_value=config["photo_directory"]))
        settings.interval_min = self.spin(12)
        settings.interval_max = self.spin(24)
        settings.fade = self.spin(config["fade_seconds"])
        settings.radius = self.spin(config["corner_radius"])
        settings.visualizer_enabled = Mock(get_active=Mock(return_value=True))
        settings.error = Mock()
        settings.rows[1][2].get_text.return_value = "Pending name"
        settings.rows[1][3].get_value.return_value = 600
        config["frames"][0]["size"] = 512
        settings.sync_size("photo-1", 512)
        settings._apply(None)
        saved = settings.app.apply_settings.call_args.args[0]
        self.assertEqual(saved["frames"][0]["size"], 512)
        self.assertEqual(saved["frames"][1]["size"], 600)
        self.assertEqual(saved["frames"][1]["name"], "Pending name")
        self.assertEqual((saved["interval_min_seconds"], saved["interval_max_seconds"]), (12, 24))


if __name__ == "__main__":
    unittest.main()
