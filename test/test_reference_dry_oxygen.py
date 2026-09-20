import importlib.util
import math
import unittest
from pathlib import Path


MODULE_PATH = (
    Path(__file__).resolve().parents[1]
    / "Service"
    / "UFC_UGC_ZOS_Service"
    / "function"
    / "o2_compensation"
    / "reference_dry_oxygen.py"
)
MODULE_SPEC = importlib.util.spec_from_file_location("reference_dry_oxygen", MODULE_PATH)
reference_dry_oxygen = importlib.util.module_from_spec(MODULE_SPEC)
MODULE_SPEC.loader.exec_module(reference_dry_oxygen)

DEFAULT_REFERENCE_DRY_OXYGEN_PERCENT = (
    reference_dry_oxygen.DEFAULT_REFERENCE_DRY_OXYGEN_PERCENT
)
get_reference_dry_oxygen_percent = (
    reference_dry_oxygen.get_reference_dry_oxygen_percent
)
get_reference_dry_oxygen_percent_text = (
    reference_dry_oxygen.get_reference_dry_oxygen_percent_text
)
initialize_reference_dry_oxygen_percent = (
    reference_dry_oxygen.initialize_reference_dry_oxygen_percent
)
reset_reference_dry_oxygen_percent = (
    reference_dry_oxygen.reset_reference_dry_oxygen_percent
)
set_reference_dry_oxygen_percent = (
    reference_dry_oxygen.set_reference_dry_oxygen_percent
)


class ReferenceDryOxygenTest(unittest.TestCase):
    def setUp(self):
        reset_reference_dry_oxygen_percent()

    def tearDown(self):
        reset_reference_dry_oxygen_percent()

    def test_process_default_is_20_930_percent(self):
        self.assertEqual(20.930, DEFAULT_REFERENCE_DRY_OXYGEN_PERCENT)
        self.assertEqual(20.930, get_reference_dry_oxygen_percent())
        self.assertEqual("20.930", get_reference_dry_oxygen_percent_text())

    def test_runtime_value_can_be_updated_to_three_decimal_places(self):
        self.assertEqual(20.935, set_reference_dry_oxygen_percent(20.9346))
        self.assertEqual(20.935, get_reference_dry_oxygen_percent())
        self.assertEqual("20.935", get_reference_dry_oxygen_percent_text())

    def test_startup_value_is_loaded_from_zos_configuration(self):
        initialized = initialize_reference_dry_oxygen_percent(
            {"ZOS": {"reference_dry_oxygen_percent": "20.8764"}}
        )
        self.assertEqual(20.876, initialized)
        self.assertEqual("20.876", get_reference_dry_oxygen_percent_text())

        set_reference_dry_oxygen_percent(19.500)
        self.assertEqual(20.876, reset_reference_dry_oxygen_percent())

    def test_invalid_startup_configuration_uses_safe_default(self):
        initialized = initialize_reference_dry_oxygen_percent(
            {"ZOS": {"reference_dry_oxygen_percent": "invalid"}}
        )
        self.assertEqual(DEFAULT_REFERENCE_DRY_OXYGEN_PERCENT, initialized)

    def test_reset_restores_startup_default(self):
        set_reference_dry_oxygen_percent(19.876)
        self.assertEqual(20.930, reset_reference_dry_oxygen_percent())

    def test_invalid_values_are_rejected(self):
        for invalid_value in (-0.001, 100.001, math.nan, math.inf, "not-a-number"):
            with self.subTest(value=invalid_value):
                with self.assertRaises(ValueError):
                    set_reference_dry_oxygen_percent(invalid_value)


if __name__ == "__main__":
    unittest.main()
