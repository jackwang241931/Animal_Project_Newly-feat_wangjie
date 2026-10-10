import json
import tempfile
import unittest
from pathlib import Path

from Service.UFC_UGC_ZOS_Service.function.calibration_channels import (
    get_enabled_calibration_channels,
    get_enabled_calibration_handler_names,
)
from Service.UFC_UGC_ZOS_Service.function.co2_compensation.co2_calibration_handler import (
    CO2CalibrationHandler,
)
from Service.UFC_UGC_ZOS_Service.function.o2_compensation.calibration_handler import (
    CalibrationHandler,
)
from public.config_class.global_setting import global_setting


PROJECT_ROOT = Path(__file__).resolve().parents[1]
ALL_HANDLER_CHANNELS = ["REF"] + [f"M{i}" for i in range(1, 9)]


class CalibrationChannelSelectionTests(unittest.TestCase):
    def setUp(self):
        self.previous_mouse_cages = global_setting.get_setting("mouse_cages", None)

    def tearDown(self):
        global_setting.set_setting("mouse_cages", self.previous_mouse_cages)

    def test_enabled_cages_are_deduplicated_and_reference_is_appended(self):
        global_setting.set_setting("mouse_cages", [3, "1", 3, 8, 0, 9, "bad"])

        self.assertEqual([2, 0, 7, 8], get_enabled_calibration_channels())
        self.assertEqual(
            ["M3", "M1", "M8", "REF"],
            get_enabled_calibration_handler_names(),
        )

    def test_no_enabled_cage_does_not_run_reference_only_calibration(self):
        global_setting.set_setting("mouse_cages", [])
        self.assertEqual([], get_enabled_calibration_channels())

    def test_o2_selected_channels_complete_without_waiting_for_disabled_cages(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as directory:
            config_path = Path(directory) / "calib_config.json"
            config_path.write_text(
                json.dumps({
                    "offsets": {channel: 0.25 for channel in ALL_HANDLER_CHANNELS},
                    "gains": {channel: 1.0 for channel in ALL_HANDLER_CHANNELS},
                    "blk_u": {channel: 20.0 for channel in ALL_HANDLER_CHANNELS},
                    "blk_v": {channel: 20.0 for channel in ALL_HANDLER_CHANNELS},
                }),
                encoding="utf-8",
            )
            handler = CalibrationHandler(target_points=5, config_path=config_path)
            handler.set_channels(["M2", "REF"])
            handler.start_new_calibration(span_value=20.8)

            for channel in handler.channels:
                for _ in range(5):
                    handler.add_data(channel, 0, 25, 101.3, 20.0, 0.0)
            for channel in handler.channels:
                for _ in range(5):
                    handler.add_data(channel, 0, 25, 101.3, 20.1, 0.0)

            self.assertTrue(handler.get_status()["completed"])
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(0.25, saved["offsets"]["M3"])

    def test_co2_selected_channels_keep_disabled_channel_coefficients(self):
        with tempfile.TemporaryDirectory(dir=PROJECT_ROOT) as directory:
            config_path = Path(directory) / "co2_calib_config.json"
            config_path.write_text(
                json.dumps({
                    "channels": {
                        "M3": {"coef_k": 0.95, "coef_b": 1.2, "r2": 0.99}
                    }
                }),
                encoding="utf-8",
            )
            handler = CO2CalibrationHandler(target_points=5, config_path=config_path)
            handler.set_channels(["M2", "REF"])
            handler.start_new_calibration()

            for index in range(5):
                handler.add_data("REF", 400 + index)
            for index in range(5):
                handler.add_data("M2", 410 + index)

            self.assertTrue(handler.get_status()["calibrated"])
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(0.95, saved["channels"]["M3"]["coef_k"])


if __name__ == "__main__":
    unittest.main()
