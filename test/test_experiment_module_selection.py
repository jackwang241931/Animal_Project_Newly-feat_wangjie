from types import SimpleNamespace
import unittest

from public.util.experiment_module_selection import (
    any_selected_module_enabled,
    channel_module_enabled,
    filter_camera_mappings,
    gas_module_selection,
)


def _setting(selected=(1,), modules=None):
    return SimpleNamespace(
        groups=[
            SimpleNamespace(id=index, is_selected=index in selected)
            for index in range(1, 4)
        ],
        channel_modules=modules,
    )


class ExperimentModuleSelectionTests(unittest.TestCase):
    def test_old_template_defaults_every_module_to_enabled(self):
        setting = _setting(modules=None)
        self.assertTrue(channel_module_enabled(setting, 1, "weight"))
        self.assertTrue(any_selected_module_enabled(setting, "behavior"))
        self.assertTrue(gas_module_selection(setting)["enabled"])

    def test_camera_mapping_keeps_only_selected_enabled_cages(self):
        setting = _setting(
            selected=(1, 2),
            modules={
                "1": {"behavior": False},
                "2": {"behavior": True},
                "3": {"behavior": True},
            },
        )
        mappings = [
            {"mouse_cage_number": 1, "serial": "A"},
            {"mouse_cage_number": "2", "serial": "B"},
            {"mouse_cage_number": 3, "serial": "C"},
        ]
        self.assertEqual(
            filter_camera_mappings(mappings, setting, "behavior"),
            [{"mouse_cage_number": "2", "serial": "B"}],
        )

    def test_gas_path_can_be_disabled_for_all_selected_cages(self):
        off = {key: False for key in ("UFC", "UGC", "ZOS")}
        setting = _setting(selected=(1, 2), modules={"1": off, "2": dict(off)})
        result = gas_module_selection(setting)
        self.assertTrue(result["valid"])
        self.assertFalse(result["enabled"])

    def test_partial_gas_selection_is_rejected(self):
        setting = _setting(
            modules={"1": {"UFC": True, "UGC": False, "ZOS": True}},
        )
        result = gas_module_selection(setting)
        self.assertFalse(result["valid"])
        self.assertIn("统一全选或全不选", result["message"])

    def test_mixed_per_cage_gas_selection_is_rejected(self):
        on = {key: True for key in ("UFC", "UGC", "ZOS")}
        off = {key: False for key in ("UFC", "UGC", "ZOS")}
        setting = _setting(selected=(1, 2), modules={"1": on, "2": off})
        result = gas_module_selection(setting)
        self.assertFalse(result["valid"])
        self.assertIn("CH01", result["message"])
        self.assertIn("CH02", result["message"])


if __name__ == "__main__":
    unittest.main()
