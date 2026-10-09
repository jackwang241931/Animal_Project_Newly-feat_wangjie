import json
import tempfile
import unittest
from pathlib import Path

from Service.UFC_UGC_ZOS_Service.function.o2_compensation.calibration_handler import (
    CalibrationHandler,
)
from Service.UFC_UGC_ZOS_Service.function.o2_compensation.o2_realtime_core import (
    RealtimeO2Compensator,
    get_default_config_path,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
CHANNELS = ["REF"] + [f"M{i}" for i in range(1, 9)]


def _config():
    return {
        "version": "3.1",
        "air_lock": 20.869,
        "offsets": {channel: 0.0 for channel in CHANNELS},
        "blk_u": {channel: 20.0 for channel in CHANNELS},
        "blk_v": {channel: 20.0 for channel in CHANNELS},
        "match_gain": {f"M{i}": 0.5 for i in range(1, 9)},
        "match_bias": {f"M{i}": 0.1 for i in range(1, 9)},
    }


class O2CompensationCompatibilityTests(unittest.TestCase):
    def test_default_config_path_stays_in_project_config_directory(self):
        self.assertEqual(PROJECT_ROOT / "config" / "calib_config.json", get_default_config_path())

    def test_channel_matching_is_applied_only_after_a_real_reference_sample(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            config_path = Path(directory) / "calib_config.json"
            config_path.write_text(json.dumps(_config()), encoding="utf-8")
            compensator = RealtimeO2Compensator(config_path)

            self.assertIsNone(compensator.compensate("M1", 0, 20, 100, 19, 0))
            self.assertAlmostEqual(20.869, compensator.compensate("REF", 0, 20, 100, 20, 0))
            self.assertTrue(compensator.has_valid_reference_sample())
            self.assertAlmostEqual(20.469, compensator.compensate("M1", 0, 20, 100, 19, 0))

    def test_existing_positional_add_data_contract_and_status_fields_are_preserved(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            config_path = Path(directory) / "calib_config.json"
            config_path.write_text(json.dumps(_config()), encoding="utf-8")
            handler = CalibrationHandler(target_points=5, config_path=config_path)
            handler.start_air_touchup()

            # The sixth positional argument is the legacy/current ZOS humidity value.
            handler.add_data("REF", 0, 25, 101.3, 20.8, 50.0)
            status = handler.get_status()

            self.assertEqual(1, status["current_counts"]["REF"])
            self.assertFalse(status["all_ready"])
            self.assertFalse(status["failed"])
            self.assertIsNone(status["failure_reason"])
            self.assertEqual(_config()["match_gain"], status["match_gain"])
            self.assertEqual(_config()["match_bias"], status["match_bias"])


if __name__ == "__main__":
    unittest.main()
