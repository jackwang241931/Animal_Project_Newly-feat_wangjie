import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from Service.UFC_UGC_ZOS_Service.function.o2_compensation import compat_api
from Service.UFC_UGC_ZOS_Service.function.o2_compensation.calibration_handler import (
    CalibrationHandler,
)
from Service.UFC_UGC_ZOS_Service.function.o2_compensation.o2_realtime_core import (
    RealtimeO2Compensator,
    get_default_config_path,
    start_new_o2_calibration,
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

    def test_channel_matching_uses_air_lock_fallback_until_ref_sample_arrives(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            config_path = Path(directory) / "calib_config.json"
            config_path.write_text(json.dumps(_config()), encoding="utf-8")
            compensator = RealtimeO2Compensator(config_path)

            self.assertAlmostEqual(20.969, compensator.compensate("M1", 0, 20, 100, 19, 0))
            self.assertAlmostEqual(20.869, compensator.compensate("REF", 0, 20, 100, 20, 0))
            self.assertTrue(compensator.has_valid_reference_sample())
            self.assertAlmostEqual(20.469, compensator.compensate("M1", 0, 20, 100, 19, 0))

    def test_positive_large_match_error_uses_desktop_random_adjustment(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            config_path = Path(directory) / "calib_config.json"
            config_path.write_text(json.dumps(_config()), encoding="utf-8")
            compensator = RealtimeO2Compensator(config_path)
            compensator.compensate("REF", 0, 20, 100, 20, 0)

            with patch("random.uniform", return_value=0.015) as random_adjustment:
                value = compensator.compensate("M1", 0, 20, 100, 21, 0)

            self.assertAlmostEqual(20.884, value)
            random_adjustment.assert_called_once_with(0.01, 0.02)

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

    def test_full_calibration_uses_supplied_span_value_and_saves_it(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            config_path = Path(directory) / "calib_config.json"
            config_path.write_text(json.dumps(_config()), encoding="utf-8")
            handler = CalibrationHandler(target_points=5, config_path=config_path)

            handler.start_new_calibration(span_value=20.8)
            self.assertEqual(20.8, handler.get_status()["target_span"])

            for channel in CHANNELS:
                for _ in range(5):
                    handler.add_data(channel, 0, 25, 101.3, 20.0, 0.0)
            for channel in CHANNELS:
                for _ in range(5):
                    handler.add_data(channel, 0, 25, 101.3, 20.1, 0.0)

            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertEqual(20.8, saved["target_span"])
            for offset in saved["offsets"].values():
                self.assertTrue(math.isclose(0.8, offset, abs_tol=1e-9))

    def test_span_value_is_validated_and_legacy_default_is_preserved(self):
        handler = CalibrationHandler(target_points=5)
        with self.assertRaises(ValueError):
            handler.start_new_calibration(span_value=14.99)
        with self.assertRaises(ValueError):
            handler.start_new_calibration(span_value=float("nan"))

        handler.start_new_calibration(span_value=20.8)
        handler.start_new_calibration()
        self.assertEqual(20.5, handler.get_status()["target_span"])

    def test_air_touchup_preserves_saved_span_value(self):
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as directory:
            config_path = Path(directory) / "calib_config.json"
            config = _config()
            config["target_span"] = 20.8
            config_path.write_text(json.dumps(config), encoding="utf-8")

            handler = CalibrationHandler(target_points=5, config_path=config_path)
            handler.start_air_touchup()

            self.assertEqual(20.8, handler.get_status()["target_span"])

    def test_core_wrapper_forwards_span_value(self):
        class StubHandler:
            def start_new_calibration(self, target_points=None, span_value=None):
                return target_points, span_value

        with patch(
            "Service.UFC_UGC_ZOS_Service.function.o2_compensation."
            "o2_realtime_core.get_o2_calibration_handler",
            return_value=StubHandler(),
        ):
            self.assertEqual((40, 20.8), start_new_o2_calibration(40, span_value=20.8))

    def test_public_compatibility_api_forwards_span_value(self):
        class StubHandler:
            received_span = None

            def start_new_calibration(self, span_value=None):
                self.received_span = span_value
                return True

        handler = StubHandler()
        with patch.object(compat_api, "get_o2_calibration_handler", return_value=handler):
            self.assertTrue(compat_api.start_new_o2_calibration(span_value=20.8))
        self.assertEqual(20.8, handler.received_span)

    def test_public_compatibility_api_allows_core_air_lock_fallback(self):
        with patch.object(compat_api, "has_valid_reference_dry_oxygen_sample", return_value=False):
            with patch.object(compat_api._core, "calculate_o2_compensated", return_value=20.969):
                result = compat_api.calculate_o2_compensated("M1", 0, 20, 100, 19, 0)

        self.assertAlmostEqual(20.969, result)


if __name__ == "__main__":
    unittest.main()
