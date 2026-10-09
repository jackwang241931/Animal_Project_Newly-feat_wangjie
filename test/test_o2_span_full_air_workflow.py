import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from Module.new_experiment_setting.index.Tab_1 import Tab_1
from Module.new_experiment_setting.main import Main_experiment_calibration
from Service.UFC_UGC_ZOS_Service.function.startup_air_calibration.Startup_Air_Calibration import (
    Startup_Air_Calibration,
)
from Service.UFC_UGC_ZOS_Service.function.gas_calibration.Gas_Carlibration import (
    _patched_wait_one_minute_for_zero_v2,
)
from Service.UFC_UGC_ZOS_Service.index.UFC_UGC_ZOS_index import UFC_UGC_ZOS_index
from public.config_class.global_setting import global_setting


CHANNELS = ["REF"] + [f"M{i}" for i in range(1, 9)]


class _ContinuedO2Handler:
    def __init__(self):
        self.channels = CHANNELS
        self.target_points = None
        self.air_touchup_started = False

    def get_status(self):
        return {"is_active": True, "need_air": True}

    def set_target_points(self, target_points):
        self.target_points = target_points

    def start_air_touchup(self):
        self.air_touchup_started = True


class _CO2Handler:
    def __init__(self, target_points):
        self.target_points = target_points
        self.time_sync_tolerance = 0
        self.started = False

    def start_new_calibration(self):
        self.started = True


class O2SpanFullAirWorkflowTests(unittest.TestCase):
    def test_new_combined_modes_survive_legacy_normalizers(self):
        for mode in ("o2_span_air", "co2_o2_span_air"):
            self.assertEqual(mode, Tab_1.normalize_startup_calibration_mode(mode))
            self.assertEqual(
                mode,
                Main_experiment_calibration.normalize_startup_calibration_mode(mode),
            )

    def test_full_air_reuses_active_span_handler_without_resetting_it(self):
        air = Startup_Air_Calibration()
        continued = _ContinuedO2Handler()
        air.pre_calibration_references = {
            channel: [20.8, 20.8, 20.8] for channel in CHANNELS
        }
        air.continue_full_o2_calibration(continued)

        with patch.object(
            Startup_Air_Calibration,
            "_normalize_target_points",
            return_value=7,
        ), patch(
            "Service.UFC_UGC_ZOS_Service.function.o2_compensation."
            "create_o2_calibration_handler"
        ) as create_handler, patch(
            "Service.UFC_UGC_ZOS_Service.function.co2_compensation."
            "co2_calibration_handler.CO2CalibrationHandler",
            _CO2Handler,
        ):
            air._build_calibration_handlers(sample_interval=1, active_channel_count=9)

        create_handler.assert_not_called()
        self.assertIs(continued, air.o2_calibration_handler)
        self.assertEqual(7, continued.target_points)
        self.assertFalse(continued.air_touchup_started)
        self.assertTrue(air.co2_calibration_handler.started)

    def test_combined_startup_chain_runs_complete_air_sequence_after_span(self):
        source = inspect.getsource(
            UFC_UGC_ZOS_index.start_btn_handle_with_o2_span_air_calibration
        )
        expected_order = (
            "run_span_only",
            "_handoff_o2_span_handler_to_full_air",
            "Startup_air_calibration_obj.start",
            "Startup_air_calibration_obj.wait_air_calibration_prepare",
            "Startup_air_calibration_obj.run",
            "Startup_air_calibration_obj.stop",
            "complete_after_full_air",
            "finish_start",
        )
        positions = [source.index(token) for token in expected_order]
        self.assertEqual(sorted(positions), positions)

    def test_co2_zero_forwards_real_ufc_status_dictionary(self):
        expected = {"ufc_status": 1, "air_pump": 0}

        class _StateCheck:
            @staticmethod
            def read_start_status(resolve, reject):
                resolve(expected)

        service = SimpleNamespace(
            co2_zero_calibration_obj_calibrate_wrapped=(
                lambda resolve, reject: resolve()
            ),
            UFC_gas_state_check_obj=_StateCheck(),
        )
        resolved = []
        rejected = []
        UFC_UGC_ZOS_index._co2_zero_then_read_ufc_start_status(
            service,
            resolved.append,
            rejected.append,
        )

        self.assertEqual([expected], resolved)
        self.assertEqual([], rejected)

    def test_legacy_full_calibration_failure_stops_later_startup_steps(self):
        calls = []

        def zero_done(resolve, reject):
            calls.append("zero")
            resolve()

        def span_failed(resolve, reject):
            calls.append("span")
            reject("span failed")

        def must_not_start(resolve, reject):
            calls.append("experiment")
            resolve()

        resolved = []
        rejected = []
        UFC_UGC_ZOS_index._run_callback_steps(
            [zero_done, span_failed, must_not_start],
            lambda: resolved.append(True),
            rejected.append,
        )

        self.assertEqual(["zero", "span"], calls)
        self.assertEqual([], resolved)
        self.assertEqual(["span failed"], rejected)

    def test_calibration_wait_observes_process_wide_stop_request(self):
        worker = SimpleNamespace(
            is_STOP=False,
            _stop_requested=False,
            isInterruptionRequested=lambda: False,
        )
        resolved = []
        rejected = []
        global_setting.set_setting("calibration_cancel_requested", True)
        try:
            _patched_wait_one_minute_for_zero_v2(
                worker,
                lambda: resolved.append(True),
                rejected.append,
                "开启气泵后等待一分钟",
            )
        finally:
            global_setting.set_setting("calibration_cancel_requested", False)

        self.assertEqual([], resolved)
        self.assertEqual(["stop"], rejected)


if __name__ == "__main__":
    unittest.main()
