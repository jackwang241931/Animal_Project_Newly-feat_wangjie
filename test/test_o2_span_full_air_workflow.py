import configparser
import inspect
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from Module.new_experiment_setting.index.Tab_1 import Tab_1
from Module.new_experiment_setting.main import Main_experiment_calibration
from index.MainWindow_index import _calculate_o2_span_full_air_wait_seconds
from Service.UFC_UGC_ZOS_Service.function.startup_air_calibration.Startup_Air_Calibration import (
    Startup_Air_Calibration,
)
from Service.UFC_UGC_ZOS_Service.function.co2_compensation.startup_co2_air_calibration import (
    Startup_CO2_Air_Calibration,
)
from Service.UFC_UGC_ZOS_Service.function.startup_air_calibration.Startup_O2_Span_Air_Calibration import (
    Startup_O2_Span_Air_Calibration,
)
from Service.UFC_UGC_ZOS_Service.function.o2_compensation.host_wet_o2_guard import (
    WetOxygenReferenceFilter,
)
from Service.UFC_UGC_ZOS_Service.function.gas_calibration.Gas_Carlibration import (
    Gas_Carlibration,
    Gas_Carlibration_Type,
    _patched_fail_calibration_v2,
    _log_ugc_calibration_command_result,
    _patched_open_gas_pump_and_flow_for_zero_v2,
    _patched_prepare_co2_span_gas_path_v2,
    _patched_prepare_co2_zero_gas_path_v2,
    _patched_zero_dosomething_v2,
    _patched_range_dosomething_v2,
    _patched_start_ufc_if_needed_for_zero_v2,
    _patched_wait_one_minute_for_zero_v2,
    _patched_finalize_calibration_flow_v3,
    _patched_zero_cyclic_sampling_of_ugc_carbon_sensor,
    _patched_range_cyclic_sampling_of_ugc_carbon_sensor,
)
from Service.UFC_UGC_ZOS_Service.function.gas_path_system.Gas_path_system import (
    UFC_gas_path_system_close_thread,
    UGC_gas_path_system,
    ZOS_gas_path_system,
)
from Service.UFC_UGC_ZOS_Service.index.UFC_UGC_ZOS_index import UFC_UGC_ZOS_index
from public.config_class.global_setting import global_setting
from public.component.workflow_pages import ExperimentControlPage
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
    def test_documented_calibration_timing_defaults(self):
        parser = configparser.ConfigParser()
        parser.read(
            Path(__file__).resolve().parents[1] / "config" / "UFC_UGC_ZOS_Test.ini",
            encoding="utf-8",
        )

        self.assertEqual(120, parser.getint("Calibration", "co2_zero_purge_time"))
        self.assertEqual(120, parser.getint("Calibration", "co2_span_purge_time"))
        self.assertEqual(300, parser.getint("Calibration", "startup_air_calibration_wait_time"))
        self.assertEqual(25, parser.getint("Calibration", "startup_air_calibration_target_points"))
        self.assertEqual(60, parser.getint("UFC", "wait_time"))

    def test_documented_shutdown_command_order_is_preserved(self):
        coordinator = inspect.getsource(UFC_UGC_ZOS_index.stop_btn_handle)
        path_order = (
            coordinator.index('("ZOS", self.ZOS_gas_path_system_obj)'),
            coordinator.index('("UFC", self.UFC_gas_path_system_obj)'),
            coordinator.index('("UGC", self.UGC_gas_path_system_obj)'),
        )
        self.assertEqual(tuple(sorted(path_order)), path_order)

        zos_stop = inspect.getsource(ZOS_gas_path_system.stop)
        self.assertIn('number_util.set_int_to_4_bytes_list("00090000")', zos_stop)
        zos_start = inspect.getsource(ZOS_gas_path_system.start)
        self.assertIn('number_util.set_int_to_4_bytes_list("00090000")', zos_start)
        close_air = inspect.getsource(UGC_gas_path_system.close_air_valve_for_shutdown)
        self.assertIn('number_util.set_int_to_4_bytes_list("00000000")', close_air)
        ufc_close = inspect.getsource(UFC_gas_path_system_close_thread.close_UFC_valve)
        self.assertLess(ufc_close.index("before_close_ufc"), ufc_close.index("_send_close_ufc_command"))
        air_stop = inspect.getsource(Startup_Air_Calibration.stop)
        self.assertIn('number_util.set_int_to_4_bytes_list("00090000")', air_stop)

    def test_combined_co2_zero_sends_documented_zos_merge_command(self):
        calls = []
        sent_messages = []

        class _SendThread:
            send_message = None

            def Send(self, resolve, reject):
                sent_messages.append(self.send_message)
                calls.append("open_zos_zero")
                resolve({"send_state": True})

        class _Signal:
            @staticmethod
            def send(*args, **kwargs):
                return None

        def step(name):
            def executor(resolve, reject, **kwargs):
                calls.append(name)
                resolve()

            return executor

        worker = SimpleNamespace(
            co2_only_mode=True,
            type=Gas_Carlibration_Type.ZERO,
            is_STOP=False,
            port="COM3",
            send_thread=_SendThread(),
            update_status_main_signal_gui_update=_Signal(),
            title="calibration",
            name="CO2校零",
            start_ufc_if_needed_for_zero=step("ensure_ufc"),
            open_gas_pump_and_flow_for_zero=step("ensure_pump"),
            wait_one_minute_for_zero=step("purge"),
            cyclic_sampling_of_ugc_carbon_sensor=step("sample_co2"),
            zero_finalize_flow=step("finalize_zero"),
            stop=lambda: calls.append("stop_worker"),
            fail_calibration=lambda error: calls.append(("failure", error)),
        )
        worker.open_zos_zero_or_span_valve = (
            Gas_Carlibration.open_zos_zero_or_span_valve.__get__(worker)
        )

        def prepare_co2_zero(self, resolve, reject, port):
            calls.append("prepare_ugc_zero_gas")
            resolve()

        with patch(
            "Service.UFC_UGC_ZOS_Service.function.gas_calibration.Gas_Carlibration._patched_prepare_co2_zero_gas_path_v2",
            new=prepare_co2_zero,
        ):
            _patched_zero_dosomething_v2(worker)

        command = sent_messages[0]
        command_data = "".join(
            f"{int(value, 16) if isinstance(value, str) else int(value):02X}"
            for value in command["data"]
        )
        self.assertEqual("0009FF00", command_data)
        self.assertEqual("4", command["slave_id"])
        self.assertLess(
            calls.index("prepare_ugc_zero_gas"),
            calls.index("open_zos_zero"),
        )
        self.assertEqual(
            [
                "prepare_ugc_zero_gas",
                "open_zos_zero",
                "ensure_ufc",
                "ensure_pump",
                "purge",
                "sample_co2",
                "finalize_zero",
                "stop_worker",
            ],
            calls,
        )

    def test_user_stop_cancellation_does_not_enter_startup_failure_rollback(self):
        calls = []

        class _StartupAir:
            def clear_continued_o2_calibration(self):
                calls.append("clear_air_handoff")

        class _StartupO2:
            def request_stop(self):
                calls.append("request_o2_stop")

            def abort_pending_calibration(self):
                calls.append("abort_pending")

            def _restore_safe_gas_state(self):
                calls.append("restore_gas_state")

        service = SimpleNamespace(
            Startup_air_calibration_obj=_StartupAir(),
            Startup_o2_span_air_calibration_obj=_StartupO2(),
            _experiment_stop_requested=True,
            update_status_main_signal_gui_update=SimpleNamespace(
                send=lambda *args, **kwargs: calls.append("log")
            ),
        )

        result = UFC_UGC_ZOS_index._reject_o2_span_air_start(
            service, "实验停止，校准已取消"
        )

        self.assertIsNone(result)
        self.assertNotIn("restore_gas_state", calls)
        self.assertEqual(1, calls.count("abort_pending"))

    def test_cancelled_calibration_is_not_reported_as_failure(self):
        calls = []
        worker = SimpleNamespace(
            is_STOP=True,
            calibration_done_event=threading.Event(),
            calibration_error="old error",
            name="CO2 Span",
            title="span",
            set_calibration_running_state=lambda running: calls.append(("running", running)),
            update_status_main_signal_gui_update=SimpleNamespace(
                send=lambda message, **kwargs: calls.append(("message", message))
            ),
            stop=lambda: calls.append(("stop",)),
        )

        _patched_fail_calibration_v2(worker, "stop")

        self.assertIsNone(worker.calibration_error)
        self.assertTrue(worker.calibration_done_event.is_set())
        self.assertIn(("stop",), calls)
        self.assertFalse(any("失败" in item[1] for item in calls if item[0] == "message"))

    def test_cancelled_ugc_open_does_not_send_valve_command(self):
        previous_cancel = global_setting.get_setting("calibration_cancel_requested", False)
        global_setting.set_setting("calibration_cancel_requested", True)
        calls = []
        worker = SimpleNamespace(
            is_STOP=False,
            type=Gas_Carlibration_Type.ZERO,
            isInterruptionRequested=lambda: False,
        )
        try:
            Gas_Carlibration.open_ugc_zero_or_span_valve(
                worker,
                lambda *_: calls.append("resolved"),
                lambda error=None: calls.append(("rejected", error)),
                "COM1",
            )
        finally:
            global_setting.set_setting("calibration_cancel_requested", previous_cancel)

        self.assertEqual([("rejected", "stop")], calls)

    def test_cancelled_co2_stage_does_not_resolve_into_the_next_calibration(self):
        for stage, worker_attr, wrapper_attr, stage_wrapper, worker_wait_wrapper in (
            (
                "zero",
                "Zero_carlibration_obj",
                "zero_carlibration_obj_calibrate_wrapped",
                UFC_UGC_ZOS_index.co2_zero_calibration_obj_calibrate_wrapped,
                UFC_UGC_ZOS_index.zero_carlibration_obj_calibrate_wrapped,
            ),
            (
                "span",
                "Range_carlibration_obj",
                "span_carlibration_obj_calibrate_wrapped",
                UFC_UGC_ZOS_index.co2_span_calibration_obj_calibrate_wrapped,
                UFC_UGC_ZOS_index.span_carlibration_obj_calibrate_wrapped,
            ),
        ):
            with self.subTest(stage=stage):
                worker = SimpleNamespace(
                    co2_only_mode=False,
                    keep_gas_path_open_after_calibration=False,
                    gas_path_prepared_before_calibration=False,
                    calibration_done_event=threading.Event(),
                    calibration_error=None,
                )

                def calibrate(resolve, reject):
                    resolve()
                    worker.calibration_done_event.set()

                worker.calibrate = calibrate
                service = SimpleNamespace(_experiment_stop_requested=True)
                setattr(service, worker_attr, worker)
                setattr(
                    service,
                    wrapper_attr,
                    lambda resolve, reject: worker_wait_wrapper(
                        service, resolve, reject
                    ),
                )
                resolved = []
                rejected = []

                stage_wrapper(
                    service,
                    lambda: resolved.append(True),
                    rejected.append,
                )

                self.assertEqual([], resolved)
                self.assertEqual(1, len(rejected))
                self.assertIn("实验停止", rejected[0])
                self.assertIn("已取消", rejected[0])
                self.assertFalse(worker.co2_only_mode)
                self.assertFalse(worker.keep_gas_path_open_after_calibration)
                self.assertFalse(worker.gas_path_prepared_before_calibration)

    def test_o2_span_stage_will_not_clear_a_pending_stop_and_open_gas(self):
        calibration = Startup_O2_Span_Air_Calibration()
        calibration.request_stop()
        rejected = []

        calibration.run_span_only_keep_span(lambda *_: self.fail("unexpected resolve"), rejected.append)

        self.assertEqual(1, len(rejected))
        self.assertIn("实验停止", str(rejected[0]))
        self.assertTrue(calibration.is_stop_requested())

    def test_air_wait_ends_without_extra_reference_reads(self):
        air = Startup_Air_Calibration()
        resolved = []
        rejected = []
        air._normalize_startup_wait_seconds = lambda: 0
        air._send_text = lambda *_: None
        air._read_zos_channel_snapshot = lambda *_: self.fail(
            "等待结束后不应再做正式采样前的额外读取"
        )

        air.wait_air_calibration_prepare(
            lambda: resolved.append(True),
            rejected.append,
        )

        self.assertEqual([True], resolved)
        self.assertEqual([], rejected)

    def test_air_snapshot_parses_sht_temperature_and_humidity(self):
        air = Startup_Air_Calibration()
        air._get_port = lambda: "COM3"
        items = [
            {"desc": "氧分压(hPa)", "value": 210.9},
            {"desc": "气体压力(hPa)", "value": 1013.2},
            {"desc": "氧浓度(%)", "value": 20.8},
            {"desc": "ZOS温度2测量值(°C)", "value": 25.0},
            {"desc": "ZOS湿度测量值(%RH)", "value": 30.0},
            {"desc": "SHT温度测量值(°C)", "value": 24.5},
            {"desc": "SHT湿度测量值(%RH)", "value": 42.0},
        ]
        air.send_thread = SimpleNamespace(
            Send_no_promise=lambda: ({"data": items}, None)
        )

        snapshot = air._read_zos_channel_snapshot(8)

        self.assertEqual(24.5, snapshot["sht_temp"])
        self.assertEqual(42.0, snapshot["sht_rh"])
        self.assertEqual(25.0, snapshot["zos_temp"])
        self.assertEqual(30.0, snapshot["zos_rh"])

    def test_air_o2_handler_receives_sht_temperature_and_humidity(self):
        source = inspect.getsource(Startup_Air_Calibration.run)

        self.assertIn('sht_temp=snapshot.get("sht_temp")', source)
        self.assertIn('sht_rh=snapshot.get("sht_rh")', source)

    def test_air_co2_log_contains_raw_pressure_and_compensated_values(self):
        for source in (
            inspect.getsource(Startup_Air_Calibration.run),
            inspect.getsource(Startup_CO2_Air_Calibration.run),
        ):
            self.assertIn("原始值=", source)
            self.assertIn("ZOS气压=", source)
            self.assertIn("气压补偿后=", source)
            self.assertIn("校准采样和拟合使用气压补偿后数值", source)

    def test_air_formal_polling_puts_reference_channel_first(self):
        self.assertEqual(
            [8, 0, 1, 2],
            Startup_Air_Calibration._reference_first_channels([0, 1, 2, 8]),
        )

    def test_wet_o2_guard_uses_first_three_formal_samples_as_references(self):
        guard = WetOxygenReferenceFilter()

        for value in (20.8, 20.81, 20.79):
            result = guard.filter_with_status("M1", value)
            self.assertEqual(value, result["value"])
            self.assertTrue(result["accepted"])
            self.assertEqual("collecting_initial_references", result["reason"])

        self.assertTrue(guard.has_initial_references("M1"))
        jump = guard.filter_with_status("M1", 22.0)
        self.assertIsNone(jump["value"])
        self.assertFalse(jump["accepted"])

    def test_combined_o2_span_reuses_prepared_co2_path_without_reopening_or_purge(self):
        calls = []

        class _Handler:
            @staticmethod
            def get_status():
                return {"need_air": True}

        calibration = Startup_O2_Span_Air_Calibration()
        calibration._target_points = lambda: 25
        calibration._sample_interval = lambda: 2
        calibration._span_value = lambda: 20.8
        calibration._send_span_values = lambda value: calls.append(("span_value", value))
        calibration._get_active_channels = lambda: [8]
        calibration._channel_name = lambda channel: "REF"
        calibration._send_text = lambda message: calls.append(("log", message))
        calibration._set_coil = lambda *args: calls.append(("coil", args))
        calibration._emit_calibration_time = lambda name: calls.append(("time", name))
        calibration._wait_for_gas_flush = lambda *args: calls.append(("purge", args))
        calibration._sample_phase = lambda *args: calls.append(("sample", args))
        calibration._finish_running_state = lambda: None

        settings = {"port": "COM3", "calibration_cancel_requested": False}
        resolved = []
        rejected = []
        with patch.object(
            global_setting,
            "get_setting",
            side_effect=lambda key, default=None: settings.get(key, default),
        ), patch.object(global_setting, "set_setting"), patch(
            "Service.UFC_UGC_ZOS_Service.function.startup_air_calibration.Startup_O2_Span_Air_Calibration.start_new_o2_calibration"
        ), patch(
            "Service.UFC_UGC_ZOS_Service.function.startup_air_calibration.Startup_O2_Span_Air_Calibration.get_o2_calibration_handler",
            return_value=_Handler(),
        ):
            calibration.run_span_only_keep_span(resolved.append, rejected.append)

        self.assertEqual([_Handler], [type(item) for item in resolved])
        self.assertEqual([], rejected)
        self.assertFalse(any(call[0] == "coil" for call in calls))
        self.assertFalse(any(call[0] == "purge" for call in calls))
        self.assertTrue(any(call[0] == "sample" for call in calls))

    def test_air_button_routes_by_experiment_state(self):
        source = inspect.getsource(ExperimentControlPage._start_air_calibration)

        self.assertIn('_select_startup_calibration("co2_o2_span_air")', source)
        self.assertIn('_send_monitor_command("start_air_calibration")', source)
        self.assertNotIn('startup_calibration_mode", "air"', source)

    def test_runtime_air_worker_runs_standalone_sequence(self):
        calls = []

        def step(name):
            def executor(resolve, reject):
                calls.append(name)
                resolve()

            return executor

        class _Air:
            name = "运行前 Air 空气校准"

            def clear_continued_o2_calibration(self):
                calls.append("clear")

            def set_external_stop_checker(self, checker):
                calls.append("checker")
                self.checker = checker

            start = staticmethod(step("start"))
            wait_air_calibration_prepare = staticmethod(step("wait"))
            run = staticmethod(step("run"))
            stop = staticmethod(step("stop"))

        air = _Air()
        service = SimpleNamespace(
            Startup_air_calibration_obj=air,
            _runtime_air_stop_event=threading.Event(),
            _runtime_air_lock=threading.Lock(),
            _runtime_air_thread=threading.current_thread(),
            update_status_main_signal_gui_update=None,
            _run_runtime_air_step=UFC_UGC_ZOS_index._run_runtime_air_step,
            _publish_runtime_air_result=lambda *_: None,
        )
        global_setting.set_setting("is_calibrating", True)
        global_setting.set_setting("current_calibration_type", "实验中 Air 空气校准")

        UFC_UGC_ZOS_index._run_runtime_air_calibration(service)

        self.assertEqual(
            ["clear", "checker", "start", "wait", "run", "stop", "clear"],
            calls,
        )
        self.assertEqual("运行前 Air 空气校准", air.name)
        self.assertIsNone(service._runtime_air_thread)
        self.assertFalse(global_setting.get_setting("is_calibrating", True))
        self.assertIsNone(global_setting.get_setting("current_calibration_type", "missing"))

    def test_gui_timeout_covers_the_complete_combined_calibration(self):
        calibration_config = {
            "calibration_max_timeout": 300,
            "co2_zero_purge_time": 120,
            "co2_span_purge_time": 120,
            "o2_span_air_target_points": 25,
            "startup_air_calibration_channel_interval": 2,
            "startup_air_calibration_wait_time": 1800,
            "startup_air_calibration_target_points": 120,
            "startup_air_calibration_max_timeout": 3000,
            "startup_air_calibration_reference_max_attempts": 10,
            "startup_air_calibration_reference_retry_interval": 5,
        }

        without_co2_zero = _calculate_o2_span_full_air_wait_seconds(
            calibration_config,
            active_channel_count=9,
            include_co2_zero=False,
        )
        with_co2_zero = _calculate_o2_span_full_air_wait_seconds(
            calibration_config,
            active_channel_count=9,
            include_co2_zero=True,
        )

        self.assertEqual(9759, without_co2_zero)
        self.assertEqual(10839, with_co2_zero)
        self.assertGreater(
            without_co2_zero,
            calibration_config["startup_air_calibration_wait_time"]
            + calibration_config["startup_air_calibration_max_timeout"],
        )

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
        air._get_active_channels = lambda: list(range(9))
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
            air._build_calibration_handlers(
                sample_interval=1,
                active_channel_count=9,
                active_channels=list(range(9)),
            )

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

    def test_documented_complete_mode_adds_co2_span_before_shared_air(self):
        source = inspect.getsource(
            UFC_UGC_ZOS_index.start_btn_handle_with_o2_span_air_calibration
        )
        expected_order = (
            "UFC_gas_state_check_obj.read_start_status",
            "co2_zero_calibration_obj_calibrate_wrapped",
            "co2_span_calibration_obj_calibrate_wrapped",
            "run_span_only_keep_span",
            "_handoff_o2_span_handler_to_full_air",
            "prepare_air_path",
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

    def test_complete_mode_keeps_ufc_running_between_co2_span_o2_span_and_air(self):
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

        span = SimpleNamespace(
            run_span_only_keep_span=step("o2_span", handler),
            prepare_air_path=step("prepare_air"),
            is_stop_requested=lambda: False,
            complete_after_full_air=step("complete"),
            abort_pending_calibration=lambda: None,
        )
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
        service = SimpleNamespace(
            update_status_main_signal_gui_update=_Signal(),
            _prepare_new_start=lambda: None,
            _prepare_startup_calibration_cages=lambda: calls.append("prepare"),
            UFC_gas_state_check_obj=SimpleNamespace(
                read_start_status=step(
                    "read_status", {"ufc_status": 0, "air_pump": 0}
                )
            ),
            _remember_ufc_start_status=lambda status: (
                calls.append("remember_status") or status
            ),
            UGC_gas_path_system_obj=SimpleNamespace(start=step("ugc_start")),
            ZOS_gas_path_system_obj=SimpleNamespace(start=step("zos_start")),
            UFC_gas_path_system_obj=SimpleNamespace(start=step("ufc_start")),
            Startup_o2_span_air_calibration_obj=span,
            Startup_air_calibration_obj=air,
            _handoff_o2_span_handler_to_full_air=lambda value: (
                UFC_UGC_ZOS_index._handoff_o2_span_handler_to_full_air(
                    service, value
                )
            ),
            co2_zero_calibration_obj_calibrate_wrapped=step("co2_zero"),
            co2_span_calibration_obj_calibrate_wrapped=step("co2_span"),
            finish_start=step("finish_start"),
            _reject_o2_span_air_start=lambda error: AsyPromise.reject_immediately(error),
        )

        result = UFC_UGC_ZOS_index.start_btn_handle_with_o2_span_air_calibration(
            service,
            include_co2_zero=True,
        )

        self.assertEqual(AsyPromise.RESOLVED, result._state)
        self.assertEqual(
            [
                "prepare",
                "log",
                "read_status",
                "remember_status",
                "zos_start",
                "ugc_start",
                "ufc_start",
                "co2_zero",
                "co2_span",
                "o2_span",
                "handoff",
                "prepare_air",
                "air_start",
                "air_prepare",
                "air_run",
                "air_stop",
                "complete",
                "finish_start",
            ],
            calls,
        )

    def test_co2_span_wrapper_restores_legacy_range_mode(self):
        range_worker = SimpleNamespace(co2_only_mode=False)
        observed = []
        service = SimpleNamespace(
            Range_carlibration_obj=range_worker,
            span_carlibration_obj_calibrate_wrapped=(
                lambda resolve, reject: (
                    observed.append(range_worker.co2_only_mode),
                    resolve(),
                )
            ),
        )

        resolved = []
        UFC_UGC_ZOS_index.co2_span_calibration_obj_calibrate_wrapped(
            service,
            lambda: resolved.append(True),
            self.fail,
        )

        self.assertEqual([True], observed)
        self.assertEqual([True], resolved)
        self.assertFalse(range_worker.co2_only_mode)

    def test_prepared_combined_calibration_does_not_repeat_ufc_or_pump_start(self):
        calls = []

        class _Signal:
            @staticmethod
            def send(*args, **kwargs):
                calls.append("log")

        worker = SimpleNamespace(
            gas_path_prepared_before_calibration=True,
            ufc_started_by_calibration=True,
            update_status_main_signal_gui_update=_Signal(),
            name="CO2",
            title="calibration",
            is_STOP=False,
        )

        _patched_start_ufc_if_needed_for_zero_v2(
            worker,
            lambda: calls.append("ufc_kept"),
            self.fail,
            "COM3",
        )
        _patched_open_gas_pump_and_flow_for_zero_v2(
            worker,
            lambda: calls.append("pump_kept"),
            self.fail,
            "COM3",
        )

        self.assertEqual(["log", "ufc_kept", "log", "pump_kept"], calls)
        self.assertFalse(worker.ufc_started_by_calibration)

    def test_co2_span_writes_standard_value_before_purge_wait(self):
        source = inspect.getsource(_patched_range_dosomething_v2)
        co2_only_branch = source.split("if getattr(self, \"co2_only_mode\", False):", 1)[1]
        co2_only_branch = co2_only_branch.split("        return", 1)[0]

        self.assertLess(
            co2_only_branch.index("set_ugc_standard_gas_co2"),
            co2_only_branch.index('step_desc="等待CO2 Span气体置换"'),
        )

    def test_co2_zero_and_span_valve_entry_order_matches_1012(self):
        class _Signal:
            @staticmethod
            def send(*args, **kwargs):
                return None

        class _SendThread:
            def __init__(self):
                self.commands = []

            def Send(self, resolve, reject):
                message = self.send_message
                self.commands.append(message)
                data = bytes(
                    int(value, 16) if isinstance(value, str) else int(value)
                    for value in message["data"]
                )
                response = bytes((int(message["slave_id"]), int(message["function_code"])))
                resolve({
                    "send_state": True,
                    "response_hex": (response + data + b"\x00\x00").hex(),
                })

        expected = (
            (
                _patched_prepare_co2_zero_gas_path_v2,
                ["0001ff00", "00000000", "00020000"],
            ),
            (
                _patched_prepare_co2_span_gas_path_v2,
                ["0002ff00", "00000000", "00010000"],
            ),
        )
        previous_cancel = global_setting.get_setting("calibration_cancel_requested", False)
        global_setting.set_setting("calibration_cancel_requested", False)
        try:
            for prepare_path, expected_data in expected:
                send_thread = _SendThread()
                worker = SimpleNamespace(
                    is_STOP=False,
                    isInterruptionRequested=lambda: False,
                    send_thread=send_thread,
                    update_status_main_signal_gui_update=_Signal(),
                    name="CO2校准",
                    title="calibration",
                )
                resolved = []
                rejected = []
                prepare_path(
                    worker,
                    lambda: resolved.append(True),
                    rejected.append,
                    "COM3",
                )

                self.assertEqual([True], resolved, repr(rejected))
                self.assertEqual([], rejected)
                actual_data = [
                    "".join(
                        f"{int(value, 16) if isinstance(value, str) else int(value):02x}"
                        for value in message["data"]
                    )
                    for message in send_thread.commands
                ]
                self.assertEqual(expected_data, actual_data)
        finally:
            global_setting.set_setting("calibration_cancel_requested", previous_cancel)

    def test_co2_gas_path_sequence_aborts_on_mismatched_valve_ack(self):
        class _Signal:
            @staticmethod
            def send(*args, **kwargs):
                return None

        class _SendThread:
            commands = []

            def Send(self, resolve, reject):
                self.commands.append(self.send_message)
                resolve({"send_state": True, "response_hex": "030500000000"})

        previous_cancel = global_setting.get_setting("calibration_cancel_requested", False)
        global_setting.set_setting("calibration_cancel_requested", False)
        try:
            sender = _SendThread()
            worker = SimpleNamespace(
                is_STOP=False,
                isInterruptionRequested=lambda: False,
                send_thread=sender,
                update_status_main_signal_gui_update=_Signal(),
                name="CO2校零",
                title="calibration",
            )
            resolved = []
            rejected = []
            _patched_prepare_co2_zero_gas_path_v2(
                worker, resolved.append, rejected.append, "COM3"
            )

            self.assertEqual([], resolved)
            self.assertEqual(1, len(rejected))
            self.assertIn("回包不匹配", str(rejected[0]))
            self.assertEqual(1, len(sender.commands))
        finally:
            global_setting.set_setting("calibration_cancel_requested", previous_cancel)

    def test_combined_co2_stage_keeps_active_ugc_valve_and_zos_until_next_stage(self):
        range_source = inspect.getsource(_patched_range_dosomething_v2)
        co2_branch = range_source.split(
            'if getattr(self, "co2_only_mode", False):', 1
        )[1].split("        return", 1)[0]
        self.assertIn("_patched_prepare_co2_span_gas_path_v2", co2_branch)
        self.assertNotIn("open_zos_zero_or_span_valve", co2_branch)

        calls = []

        def step(name):
            def executor(resolve, reject, port=None):
                calls.append(name)
                resolve()
            return executor

        worker = SimpleNamespace(
            co2_only_mode=True,
            type=Gas_Carlibration_Type.ZERO,
            keep_gas_path_open_after_calibration=True,
            defer_zos_close_until_finalize=True,
            name="CO2校零",
            title="calibration",
            update_status_main_signal_gui_update=SimpleNamespace(
                send=lambda *args, **kwargs: None
            ),
            close_ugc_zero_or_span_valve=step("close_ugc"),
            close_zos_zero_or_span_valve=step("close_zos"),
            close_ufc_if_open_for_zero=step("keep_ufc"),
            close_gas_pump_and_flow_for_zero=step("close_pump"),
            wait_one_minute_for_zero=step("wait"),
            finish_calibration=step("finish"),
        )
        resolved = []
        rejected = []

        _patched_finalize_calibration_flow_v3(
            worker,
            lambda: resolved.append(True),
            rejected.append,
            "COM3",
            "CO2零点",
        )

        self.assertEqual([True], resolved)
        self.assertEqual([], rejected)
        self.assertEqual(["keep_ufc", "finish"], calls)
        self.assertNotIn("close_zos", calls)
        self.assertNotIn("close_pump", calls)
        self.assertNotIn("wait", calls)

    def test_co2_stability_timeout_rejects_instead_of_continuing(self):
        settings = {
            "mouse_cages": [1],
            "UFC_UGC_ZOS_config": {
                "Calibration": {"calibration_max_timeout": -1}
            },
        }
        previous_cancel = global_setting.get_setting("calibration_cancel_requested", False)
        global_setting.set_setting("calibration_cancel_requested", False)
        try:
            with patch.object(
                global_setting,
                "get_setting",
                side_effect=lambda key, default=None: settings.get(key, default),
            ), patch(
                "Service.UFC_UGC_ZOS_Service.function.gas_calibration.Gas_Carlibration.get_enabled_calibration_channels",
                return_value=[0],
            ):
                for sampler in (
                    _patched_zero_cyclic_sampling_of_ugc_carbon_sensor,
                    _patched_range_cyclic_sampling_of_ugc_carbon_sensor,
                ):
                    worker = SimpleNamespace(
                        is_STOP=False,
                        name="CO2校准",
                        title="calibration",
                        ugc_span_target_co2_ppm=None,
                        update_status_main_signal_gui_update=SimpleNamespace(
                            send=lambda *args, **kwargs: None
                        ),
                    )
                    resolved = []
                    rejected = []
                    sampler(worker, resolved.append, rejected.append, "COM3")

                    self.assertEqual([], resolved)
                    self.assertEqual(1, len(rejected))
                    self.assertIn("未满足稳定条件", rejected[0])
        finally:
            global_setting.set_setting("calibration_cancel_requested", previous_cancel)

    def test_ugc_calibration_ack_requires_complete_echo(self):
        worker = SimpleNamespace(
            send_message={
                "slave_id": "3",
                "function_code": "6",
                "data": ["00", "10", "00", "00"],
            },
            update_status_main_signal_gui_update=SimpleNamespace(
                send=lambda *args, **kwargs: None
            ),
            name="CO2校零",
            title="calibration",
        )

        self.assertFalse(_log_ugc_calibration_command_result(
            worker,
            {"send_state": True, "response_hex": "030600100000"},
            command_name="UGC零点设置指令",
            calibration_name="校0",
        ))
        self.assertTrue(_log_ugc_calibration_command_result(
            worker,
            {"send_state": True, "response_hex": "0306001000000000"},
            command_name="UGC零点设置指令",
            calibration_name="校0",
        ))

    def test_co2_zero_and_span_calibration_ack_mismatch_abort_the_stage(self):
        settings = {
            "mouse_cages": [1],
            "UFC_UGC_ZOS_config": {
                "Calibration": {
                    "calibration_max_timeout": 300,
                    "calibration_stable_duration": 0,
                    "calibration_sample_interval": 0,
                    "zero_calibration_carbon_threshold": 5,
                    "span_calibration_carbon_threshold": 15,
                    "span_calibration_co2_tolerance": 25,
                }
            },
            "span_standard_carbon_value": 0.03,
        }
        previous_cancel = global_setting.get_setting("calibration_cancel_requested", False)
        global_setting.set_setting("calibration_cancel_requested", False)
        try:
            with patch.object(
                global_setting,
                "get_setting",
                side_effect=lambda key, default=None: settings.get(key, default),
            ), patch(
                "Service.UFC_UGC_ZOS_Service.function.gas_calibration.Gas_Carlibration.get_enabled_calibration_channels",
                return_value=[0],
            ), patch(
                "Service.UFC_UGC_ZOS_Service.function.gas_calibration.Gas_Carlibration.time.time",
                return_value=100.0,
            ), patch(
                "Service.UFC_UGC_ZOS_Service.function.gas_calibration.Gas_Carlibration.time.sleep",
                return_value=None,
            ):
                for sampler in (
                    _patched_zero_cyclic_sampling_of_ugc_carbon_sensor,
                    _patched_range_cyclic_sampling_of_ugc_carbon_sensor,
                ):
                    class _SendThread:
                        def Send_no_promise(self):
                            return {"data": [{"desc": "CO2浓度", "value": 500}]}, None

                        @staticmethod
                        def Send(resolve, reject):
                            resolve({
                                "send_state": True,
                                "response_hex": "0306000000000000",
                            })

                    worker = SimpleNamespace(
                        is_STOP=False,
                        name="CO2校准",
                        title="calibration",
                        ugc_span_target_co2_ppm=300,
                        send_thread=_SendThread(),
                        _read_zos_channel_snapshot=lambda port, channel: {
                            "pressure": 100,
                            "oxygen": 20.9,
                        },
                        _normalize_co2_to_ppm=lambda value: float(value),
                        _calculate_compensated_co2_ppm=lambda value, pressure: value,
                        push_calibration_values_to_ui=lambda **kwargs: None,
                        update_status_main_signal_gui_update=SimpleNamespace(
                            send=lambda *args, **kwargs: None
                        ),
                    )
                    resolved = []
                    rejected = []
                    sampler(
                        worker,
                        lambda: resolved.append(True),
                        rejected.append,
                        "COM3",
                    )

                    self.assertEqual([], resolved)
                    self.assertEqual(1, len(rejected))
                    self.assertIn("响应不匹配", str(rejected[0]))
        finally:
            global_setting.set_setting("calibration_cancel_requested", previous_cancel)

    def test_co2_standard_write_rejects_timeout_instead_of_logging_success(self):
        messages = []

        class _Signal:
            @staticmethod
            def send(message, **kwargs):
                messages.append(message)

        class _SendThread:
            send_message = None

            @staticmethod
            def Send(resolve, reject):
                resolve({"send_state": False, "response_hex": None, "data": None})

        worker = SimpleNamespace(
            is_STOP=False,
            send_thread=_SendThread(),
            update_status_main_signal_gui_update=_Signal(),
            name="量程",
            title="calibration",
        )
        settings = {
            "UFC_UGC_ZOS_config": {"Calibration": {}},
            "span_standard_carbon_value": 0.03,
        }
        resolved = []
        rejected = []

        with patch.object(
            global_setting,
            "get_setting",
            side_effect=lambda key, default=None: settings.get(key, default),
        ):
            Gas_Carlibration.set_ugc_standard_gas_co2(
                worker,
                lambda: resolved.append(True),
                rejected.append,
                "COM3",
            )

        self.assertEqual([], resolved)
        self.assertEqual(1, len(rejected))
        self.assertIn("未收到有效响应", rejected[0])
        self.assertFalse(any("校span成功" in message for message in messages))

    def test_co2_standard_write_accepts_only_matching_modbus_echo(self):
        messages = []

        class _Signal:
            @staticmethod
            def send(message, **kwargs):
                messages.append(message)

        class _SendThread:
            send_message = None

            @staticmethod
            def Send(resolve, reject):
                resolve({
                    "send_state": True,
                    "response_hex": "03060012012c2860",
                    "data": {},
                })

        worker = SimpleNamespace(
            is_STOP=False,
            send_thread=_SendThread(),
            update_status_main_signal_gui_update=_Signal(),
            name="量程",
            title="calibration",
        )
        settings = {
            "UFC_UGC_ZOS_config": {"Calibration": {}},
            "span_standard_carbon_value": 0.03,
        }
        resolved = []
        rejected = []

        with patch.object(
            global_setting,
            "get_setting",
            side_effect=lambda key, default=None: settings.get(key, default),
        ):
            Gas_Carlibration.set_ugc_standard_gas_co2(
                worker,
                lambda: resolved.append(True),
                rejected.append,
                "COM3",
            )

        self.assertEqual([True], resolved)
        self.assertEqual([], rejected)
        self.assertEqual(300, worker.ugc_span_target_co2_ppm)
        self.assertTrue(any("已确认写入" in message for message in messages))

    def test_span_wrapper_rejects_runtime_failure_after_worker_started(self):
        completion_event = threading.Event()
        worker = SimpleNamespace(
            calibration_done_event=completion_event,
            calibration_error=None,
        )

        def calibrate(resolve, reject):
            resolve()
            worker.calibration_error = "CO2标准气写入未获得有效响应"
            completion_event.set()

        worker.calibrate = calibrate
        service = SimpleNamespace(Range_carlibration_obj=worker)
        resolved = []
        rejected = []

        UFC_UGC_ZOS_index.span_carlibration_obj_calibrate_wrapped(
            service,
            lambda: resolved.append(True),
            rejected.append,
        )

        self.assertEqual([], resolved)
        self.assertEqual(["CO2标准气写入未获得有效响应"], rejected)

    def test_span_wrapper_resolves_only_after_runtime_completion(self):
        completion_event = threading.Event()
        worker = SimpleNamespace(
            calibration_done_event=completion_event,
            calibration_error=None,
        )

        def calibrate(resolve, reject):
            resolve()
            completion_event.set()

        worker.calibrate = calibrate
        service = SimpleNamespace(Range_carlibration_obj=worker)
        resolved = []
        rejected = []

        UFC_UGC_ZOS_index.span_carlibration_obj_calibrate_wrapped(
            service,
            lambda: resolved.append(True),
            rejected.append,
        )

        self.assertEqual([True], resolved)
        self.assertEqual([], rejected)

    def test_calibration_failure_marks_worker_done_and_stops_thread(self):
        messages = []

        class _Signal:
            @staticmethod
            def send(message, **kwargs):
                messages.append(message)

        worker = SimpleNamespace(
            calibration_done_event=threading.Event(),
            calibration_error=None,
            update_status_main_signal_gui_update=_Signal(),
            name="SPan量程标定",
            title="calibration",
            set_calibration_running_state=lambda running: messages.append(running),
            stop=lambda: messages.append("stop"),
        )

        Gas_Carlibration.fail_calibration(worker, "CO2 Span报文写入失败")

        self.assertTrue(worker.calibration_done_event.is_set())
        self.assertEqual("CO2 Span报文写入失败", worker.calibration_error)
        self.assertIn(False, messages)
        self.assertIn("stop", messages)
        self.assertTrue(any("后续标定不再继续" in str(item) for item in messages))

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
            _prepare_new_start=lambda: None,
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
                "zos_start",
                "ugc_start",
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
