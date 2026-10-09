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
from public.function.promise.AsyPromise import AsyPromise


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

    def test_span_handler_handoff_receives_unwrapped_handler(self):
        handler = _ContinuedO2Handler()
        received = []
        service = SimpleNamespace(
            Startup_air_calibration_obj=SimpleNamespace(
                continue_full_o2_calibration=lambda value, stop_checker=None: received.append(
                    (value, stop_checker)
                )
            ),
            Startup_o2_span_air_calibration_obj=SimpleNamespace(
                is_stop_requested=lambda: False
            ),
        )

        AsyPromise.resolve_immediately(None).then(
            lambda _: AsyPromise.resolve_immediately(handler)
        ).then(
            lambda value: UFC_UGC_ZOS_index._handoff_o2_span_handler_to_full_air(
                service, value
            )
        )

        self.assertEqual(handler, received[0][0])
        self.assertIs(handler, received[0][0])

    def test_span_handler_handoff_rejects_nested_promise_value(self):
        service = SimpleNamespace(
            Startup_air_calibration_obj=SimpleNamespace(),
            Startup_o2_span_air_calibration_obj=SimpleNamespace(
                is_stop_requested=lambda: False
            ),
        )

        with self.assertRaisesRegex(TypeError, "不是有效处理器"):
            UFC_UGC_ZOS_index._handoff_o2_span_handler_to_full_air(
                service,
                AsyPromise.resolve_immediately(_ContinuedO2Handler()),
            )

    def test_combined_startup_chain_handles_synchronous_callback_steps(self):
        calls = []
        handler = _ContinuedO2Handler()

        def step(name, value=None):
            def executor(resolve, reject):
                calls.append(name)
                resolve(value)

            return executor

        class _Signal:
            @staticmethod
            def send(*args, **kwargs):
                calls.append("log")

        air = SimpleNamespace(
            continue_full_o2_calibration=lambda value, stop_checker=None: calls.append(
                "handoff"
            ),
            start=step("air_start"),
            wait_air_calibration_prepare=step("air_prepare"),
            run=step("air_run"),
            stop=step("air_stop"),
            clear_continued_o2_calibration=lambda: None,
        )
        span = SimpleNamespace(
            run_span_only=step("span", handler),
            complete_after_full_air=step("complete"),
            is_stop_requested=lambda: False,
            abort_pending_calibration=lambda: None,
        )
        service = SimpleNamespace(
            update_status_main_signal_gui_update=_Signal(),
            _prepare_startup_calibration_cages=lambda: calls.append("prepare"),
            UFC_gas_state_check_obj=SimpleNamespace(
                read_start_status=step("read_status", {"ufc_status": 1})
            ),
            UFC_gas_path_system_obj=SimpleNamespace(
                start=step("ufc_start"),
                set_startup_status=lambda status: calls.append("remember_status"),
            ),
            UGC_gas_path_system_obj=SimpleNamespace(start=step("ugc_start")),
            ZOS_gas_path_system_obj=SimpleNamespace(start=step("zos_start")),
            Startup_o2_span_air_calibration_obj=span,
            Startup_air_calibration_obj=air,
            _remember_ufc_start_status=lambda status: (
                calls.append("remember_status") or status
            ),
            _handoff_o2_span_handler_to_full_air=lambda value: (
                UFC_UGC_ZOS_index._handoff_o2_span_handler_to_full_air(
                    service, value
                )
            ),
            finish_start=step("finish_start"),
            _reject_o2_span_air_start=lambda error: AsyPromise.reject_immediately(error),
        )

        result = UFC_UGC_ZOS_index.start_btn_handle_with_o2_span_air_calibration(
            service
        )

        self.assertEqual(AsyPromise.RESOLVED, result._state)
        self.assertEqual(
            [
                "prepare",
                "log",
                "read_status",
                "remember_status",
                "ugc_start",
                "zos_start",
                "ufc_start",
                "span",
                "handoff",
                "air_start",
                "air_prepare",
                "air_run",
                "air_stop",
                "complete",
                "finish_start",
            ],
            calls,
        )

    def test_failed_startup_rolls_back_ufc_before_ugc_and_zos(self):
        calls = []

        def stop_step(name, should_fail=False):
            def executor(resolve, reject):
                calls.append(name)
                if should_fail:
                    reject(f"{name} failed")
                else:
                    resolve()

            return executor

        service = SimpleNamespace(
            stop=lambda: calls.append("thread_stop"),
        )
        service._rollback_failed_start_paths = (
            lambda original_error, paths, index: UFC_UGC_ZOS_index._rollback_failed_start_paths(
                service, original_error, paths, index
            )
        )
        service._continue_failed_start_rollback = (
            lambda original_error, module_name, cleanup_error, paths, next_index: UFC_UGC_ZOS_index._continue_failed_start_rollback(
                service,
                original_error,
                module_name,
                cleanup_error,
                paths,
                next_index,
            )
        )
        paths = [
            ("UFC", SimpleNamespace(stop=stop_step("ufc", should_fail=True))),
            ("UGC", SimpleNamespace(stop=stop_step("ugc"))),
            ("ZOS", SimpleNamespace(stop=stop_step("zos"))),
        ]

        result = UFC_UGC_ZOS_index._rollback_failed_start_paths(
            service,
            "startup failed",
            paths,
            0,
        )

        self.assertEqual(["ufc", "ugc", "zos", "thread_stop"], calls)
        self.assertEqual(AsyPromise.REJECTED, result._state)
        self.assertEqual("startup failed", result._reason)

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
