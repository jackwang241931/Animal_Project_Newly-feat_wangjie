"""No-hardware tests for collecting real P1 environmental and gas evidence."""

import queue
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from Service import main_monitor_data
from Service.UFC_UGC_ZOS_Service.function.Send_Message.Send_Message import Send_Message
from public.config_class.global_setting import global_setting


class MonitorHealthBridgeTests(unittest.TestCase):
    def tearDown(self):
        main_monitor_data._set_health_snapshot_queue(None)

    def test_environment_snapshot_only_uses_direct_enm_measurements(self):
        metrics = main_monitor_data._environment_health_metrics_from_return_data(
            {
                "module_name": "ENM",
                "data": [
                    {"desc": "温度测量值(°C)", "value": 22.2},
                    {"desc": "湿度测量值(%RH)", "value": 56},
                    {"desc": "噪声测量值(dB)", "value": 41},
                    {"desc": "温度传感器状态", "value": True},
                ],
            }
        )

        environment = metrics["environment"]
        self.assertEqual(environment["temperature"]["value"], 22.2)
        self.assertEqual(environment["temperature"]["unit"], "°C")
        self.assertEqual(environment["humidity"]["value"], 56.0)
        self.assertEqual(environment["noise"]["value"], 41.0)
        self.assertNotIn("oxygen", environment)

    def test_nonzero_gas_fault_code_becomes_immediate_structured_fault(self):
        health_queue = queue.Queue()
        main_monitor_data._set_health_snapshot_queue(health_queue)

        main_monitor_data._report_gas_path_health(
            {
                "module_name": "ZOS",
                "data": [{"desc": "ZOS故障码", "value": 2}],
            },
            {"response_seconds": 0.1},
        )
        message = health_queue.get_nowait()

        self.assertEqual(message["component"], "gas_path")
        self.assertEqual(message["scope"], "ZOS")
        self.assertEqual(message["state"], "fault")
        self.assertEqual(message["reason_code"], "gas_device_fault_code")

    def test_emergency_shutdown_sends_pump_off_and_verifies_state(self):
        serial_lock = Mock()
        serial_lock.acquire.return_value = True
        modbus = Mock()
        modbus.send_command.side_effect = [
            (b"pump-off", "pump-off", True, {}),
            (b"pump-state", "pump-state", True, {}),
        ]
        modbus.parse_response.return_value = (
            {"data": [{"desc": "机器状态", "value": 1},
                      {"desc": "气泵", "value": 0}]},
            "UFC state",
        )

        with patch.dict(global_setting._setting, {
            "port": "COM7",
            "modbus": modbus,
            "serial_lock": serial_lock,
        }, clear=False), patch.object(
            main_monitor_data, "ufc_ugc_zos_thread", None
        ), patch.object(
            main_monitor_data, "_ufc_shutdown_writes_required", return_value=True
        ), patch.object(main_monitor_data.time, "sleep"):
            result = main_monitor_data.emergency_stop_ufc_pump()

        self.assertEqual(result, 1)
        self.assertEqual(modbus.send_command.call_count, 2)
        pump_off = modbus.send_command.call_args_list[0].kwargs
        self.assertEqual(pump_off["slave_id"], "2")
        self.assertEqual(pump_off["function_code"], "5")
        self.assertEqual(pump_off["data_hex_list"], ["00", "0A", "00", "00"])
        state_read = modbus.send_command.call_args_list[1].kwargs
        self.assertEqual(state_read["function_code"], "1")
        self.assertEqual(state_read["data_hex_list"], ["00", "00", "00", "0C"])
        serial_lock.release.assert_called_once_with()

    def test_emergency_shutdown_releases_serial_lock_after_send_failure(self):
        serial_lock = Mock()
        serial_lock.acquire.return_value = True
        modbus = Mock()
        modbus.send_command.return_value = (None, None, False, {})

        with patch.dict(global_setting._setting, {
            "port": "COM7",
            "modbus": modbus,
            "serial_lock": serial_lock,
        }, clear=False), patch.object(
            main_monitor_data, "ufc_ugc_zos_thread", None
        ), patch.object(
            main_monitor_data, "_ufc_shutdown_writes_required", return_value=True
        ):
            result = main_monitor_data.emergency_stop_ufc_pump()

        self.assertEqual(result, 0)
        serial_lock.release.assert_called_once_with()

    def test_emergency_shutdown_without_gas_session_does_not_touch_serial(self):
        modbus = Mock()

        with patch.dict(global_setting._setting, {
            "port": "COM7",
            "modbus": modbus,
            "ufc_start_time_state": False,
            "is_calibrating": False,
        }, clear=False), patch.object(
            main_monitor_data, "ufc_ugc_zos_thread", None
        ):
            result = main_monitor_data.emergency_stop_ufc_pump()

        self.assertEqual(result, 3)
        modbus.send_command.assert_not_called()
        modbus.close.assert_not_called()

    def test_regular_gas_command_is_blocked_after_emergency_shutdown_starts(self):
        sender = Send_Message.__new__(Send_Message)
        sender.send_message = {
            "slave_id": "2",
            "function_code": "5",
            "data": ["00", "0A", "00", "FF"],
        }
        sender.modbus = Mock()

        with patch.dict(global_setting._setting, {
            "ufc_emergency_shutdown_active": True,
        }, clear=False):
            _, _, send_state, return_data = sender._send_with_retry()

        self.assertFalse(send_state)
        self.assertIn("已阻止", return_data["response_msg"])
        sender.modbus.send_command.assert_not_called()

    def test_application_shutdown_blocks_stale_start_and_leaves_ufc_off(self):
        serial_lock = Mock()
        serial_lock.acquire.return_value = True
        modbus = Mock()
        modbus.send_command.return_value = (None, None, False, {})
        start_thread = SimpleNamespace(is_stop=False)
        run_thread = Mock()
        run_thread.isRunning.return_value = False
        controller = SimpleNamespace(
            _experiment_stop_requested=False,
            _runtime_air_stop_event=Mock(),
            Zero_carlibration_obj=None,
            Range_carlibration_obj=None,
            Startup_air_calibration_obj=None,
            Startup_o2_span_air_calibration_obj=None,
            UFC_gas_path_system_obj=SimpleNamespace(
                ufc_gas_path_system_start_thread=start_thread,
                ufc_gas_path_system_run_thread=run_thread,
            ),
        )

        with patch.dict(global_setting._setting, {
            "modbus": modbus,
            "serial_lock": serial_lock,
            "application_shutdown_requested": False,
            "application_shutdown_serial_closed": False,
            "calibration_cancel_requested": False,
            "ufc_emergency_shutdown_active": False,
        }, clear=False), patch.object(
            main_monitor_data, "ufc_ugc_zos_thread", controller
        ):
            main_monitor_data.stop_modbus()

            self.assertTrue(global_setting.get_setting("application_shutdown_requested"))
            self.assertTrue(global_setting.get_setting("ufc_emergency_shutdown_active"))
            self.assertTrue(controller._experiment_stop_requested)
            self.assertTrue(start_thread.is_stop)

        payloads = [
            call.kwargs["data_hex_list"]
            for call in modbus.send_command.call_args_list
        ]
        self.assertEqual(payloads, [
            ["00", "0A", "00", "00"],
            ["00", "0B", "00", "00"],
        ])
        modbus.close.assert_called_once_with()
        serial_lock.release.assert_called_once_with()

    def test_application_shutdown_without_gas_session_only_closes_serial(self):
        modbus = Mock()

        with patch.dict(global_setting._setting, {
            "modbus": modbus,
            "application_shutdown_requested": False,
            "application_shutdown_serial_closed": False,
            "calibration_cancel_requested": False,
            "ufc_emergency_shutdown_active": False,
            "ufc_start_time_state": False,
            "is_calibrating": False,
        }, clear=False), patch.object(
            main_monitor_data, "ufc_ugc_zos_thread", None
        ):
            main_monitor_data.stop_modbus()

            self.assertTrue(global_setting.get_setting("application_shutdown_requested"))
            self.assertTrue(global_setting.get_setting("application_shutdown_serial_closed"))

        modbus.send_command.assert_not_called()
        modbus.close.assert_called_once_with()


if __name__ == "__main__":
    unittest.main()
