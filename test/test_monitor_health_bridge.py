"""No-hardware tests for collecting real P1 environmental and gas evidence."""

import queue
import unittest

from Service import main_monitor_data


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


if __name__ == "__main__":
    unittest.main()
