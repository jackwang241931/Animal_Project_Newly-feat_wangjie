"""Offscreen tests for the fixed workflow shell and the P1 status page."""

import os
import unittest

# The project is a Windows desktop application; offscreen makes the widget
# checks safe in the no-hardware/no-desktop test path.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtWidgets import QApplication, QTabWidget, QVBoxLayout, QWidget

from public.component.system_status import SystemStatusPage
from public.component.workflow_shell import WorkflowShell, resolve_legacy_workflow_step


class SystemStatusPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def _new_page(self, health=None, context=None):
        page = SystemStatusPage(
            health_source=lambda: health or {},
            context_provider=lambda: context or {},
            refresh_interval_ms=60_000,
        )
        self.addCleanup(page.stop_refresh)
        return page

    def test_no_first_sample_keeps_real_time_values_unknown(self):
        page = self._new_page(
            health={"components": [], "alerts": []},
            context={"experiment_id": "ABM-2026-0831", "experiment_status": "not_started"},
        )
        page.refresh()

        self.assertEqual(page._data_values["data_points_total"].text(), "未知")
        self.assertEqual(page._environment_values["temperature"].text(), "未知")
        self.assertEqual(page._health_cards["collection"]["chip"].text(), "未知")
        self.assertEqual(page._overview_values["system_health"].text(), "未知")
        self.assertEqual(page._overview_values["run_duration"].text(), "未知")
        self.assertEqual(page._alert_strip.isHidden(), True)

    def test_fault_is_visible_as_alert_and_preserves_evidence(self):
        component = {
            "component": "monitor_collection",
            "scope": "CH01",
            "status": "fault",
            "reason_code": "modbus_read_failed",
            "last_success_at": 1_700_000_000,
            "consecutive_failures": 3,
            "observed_at": 1_700_000_001,
            "metrics": {"sampling_interval_seconds": 1},
        }
        page = self._new_page(health={"components": [component]})
        page.refresh()

        self.assertEqual(page._health_cards["collection"]["chip"].text(), "故障")
        self.assertEqual(page._health_cards["collection"]["failures"].text(), "连续失败：3")
        # Detailed P1-E alert strip was removed from the landing page.  The
        # alert is still retained in the health snapshot for the top-level
        # status summary and diagnostic consumers.
        self.assertTrue(page._alert_strip.isHidden())
        self.assertEqual(len(page._last_alerts), 1)

    def test_external_camera_occupation_is_unavailable_not_fault(self):
        page = self._new_page(
            health={
                "components": [
                    {
                        "component": "deep_camera",
                        "scope": "global",
                        "status": "unavailable",
                        "reason_code": "external_occupied",
                        "observed_at": 1,
                    }
                ]
            }
        )
        page.refresh()

        self.assertEqual(page._health_cards["deep"]["chip"].text(), "不可用")
        self.assertNotEqual(page._health_cards["deep"]["chip"].text(), "故障")

    def test_process_heartbeat_does_not_make_an_absent_device_normal(self):
        page = self._new_page(
            health={
                "components": [
                    {
                        "component": "p_deep_camera",
                        "status": "normal",
                        "message": "Process heartbeat and metrics received.",
                        "observed_at": 2,
                    }
                ]
            }
        )
        page.refresh()

        self.assertEqual(page._health_cards["deep"]["chip"].text(), "未知")
        self.assertEqual(page._health_cards["deep"]["reason"].text(), "未知")

    def test_running_without_channel_sample_is_not_marked_online(self):
        page = self._new_page(
            health={"components": [], "alerts": []},
            context={"experiment_status": "running", "enabled_channels": ["CH01"]},
        )
        page.refresh()

        self.assertEqual(page._channel_cards["CH01"]["chip"].text(), "未收到采样")
        self.assertEqual(page._channel_cards["CH01"]["chip"].property("statusTone"), "warning")

    def test_not_started_channel_is_unknown_until_a_real_observation_arrives(self):
        page = self._new_page(
            health={"components": [], "alerts": []},
            context={"experiment_status": "not_started", "enabled_channels": ["CH01"]},
        )
        page.refresh()

        self.assertEqual(page._channel_cards["CH01"]["chip"].text(), "未知")
        self.assertEqual(page._channel_cards["CH01"]["chip"].property("statusTone"), "neutral")

    def test_p1_has_no_separate_not_started_channel_view(self):
        page = self._new_page(
            health={"components": [], "alerts": []},
            context={"experiment_status": "not_started", "enabled_channels": ["CH01"]},
        )
        page.refresh()

        labels = page._channel_cards["CH01"]
        self.assertFalse(labels["data"].isHidden())
        self.assertLess(labels["card"].minimumHeight(), 500)
        self.assertNotIn("modules", labels)
        self.assertNotIn("legend", labels)

    def test_channel_header_has_no_experiment_mode_switches(self):
        page = self._new_page(context={"experiment_status": "not_started"})
        self.assertFalse(hasattr(page, "_channel_running_button"))
        self.assertFalse(hasattr(page, "_channel_idle_button"))

    def test_data_summary_does_not_count_configured_channels_as_online(self):
        page = self._new_page(
            health={"components": [], "alerts": []},
            context={"experiment_status": "not_started", "enabled_channels": ["CH01"]},
        )
        page.refresh()

        self.assertEqual(page._data_values["enabled_channels"].text(), "0/1")
        self.assertEqual(page._overview_values["system_health"].text(), "未知")

    def test_data_summary_counts_only_normal_channel_snapshot_as_online(self):
        page = self._new_page(
            health={
                "components": [
                    {
                        "component": "monitor_collection",
                        "scope": "CH01",
                        "status": "normal",
                        "last_success_at": 1_700_000_000,
                    }
                ],
                "alerts": [],
            },
            context={"experiment_status": "running", "enabled_channels": ["CH01"]},
        )
        page.refresh()

        self.assertEqual(page._data_values["enabled_channels"].text(), "1/1")
        self.assertEqual(page._overview_values["system_health"].text(), "在线")

    def test_summary_uses_real_data_values_when_provider_supplies_them(self):
        page = self._new_page(
            health={
                "components": [
                    {
                        "component": "monitor_collection",
                        "scope": "CH01",
                        "status": "normal",
                        "observed_at": 1_700_000_000,
                    }
                ],
                "alerts": [],
            },
            context={
                "experiment_status": "running",
                "elapsed_seconds": 3661,
                "remaining_seconds": 7200,
                "health_state": "normal",
                "enabled_channels": ["CH01"],
                "online_channels": ["CH01"],
                "data_points_total": 128,
                "storage_size": 5 * 1024 * 1024,
            },
        )
        page.refresh()

        self.assertEqual(page._overview_values["experiment_status"].text(), "运行中")
        self.assertEqual(page._overview_values["run_duration"].text(), "0天1小时1分1秒")
        self.assertEqual(page._overview_values["system_health"].text(), "在线")
        self.assertEqual(page._data_values["data_points_total"].text(), "128")
        self.assertEqual(page._data_values["storage_size"].text(), "5.00 MB")

    def test_running_channel_card_shows_real_measurements_and_none_for_missing_formulas(self):
        page = self._new_page(
            health={
                "components": [
                    {
                        "component": "monitor_collection",
                        "scope": "CH02",
                        "status": "normal",
                        "observed_at": 1_700_000_000,
                        "metrics": {
                            "measurements": {
                                "当前计量周期内跑轮圈数测量值": 40356,
                                "鼠笼红外温度(°C)": 37.4,
                            }
                        },
                    }
                ],
                "alerts": [],
            },
            context={
                "experiment_status": "running",
                "enabled_channels": ["CH02"],
                "channel_individuals": {"CH02": {"id": "M002"}},
            },
        )
        page.refresh()

        rendered = page._channel_cards["CH02"]["data"].text()
        self.assertIn("M002", rendered)
        self.assertIn("40356", rendered)
        self.assertIn("37.4", rendered)
        self.assertIn("自由活动", rendered)
        self.assertIn("<b>None</b>", rendered)
        self.assertNotIn("置信度", rendered)


class WorkflowShellTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_six_step_header_keeps_active_step_blue_contract(self):
        shell = WorkflowShell()
        self.assertEqual(len(shell._buttons), 6)
        self.assertEqual(shell._buttons[1].text(), "系统状态\n门户首页")
        self.assertEqual(shell._buttons[6].text(), "数据分析\n综合分析")
        shell.set_active_step(4)

        self.assertTrue(shell._buttons[4].property("workflowActive"))
        self.assertFalse(shell._buttons[1].property("workflowActive"))
        self.assertFalse(shell._buttons[6].property("workflowActive"))

    def test_header_lives_outside_replaced_tab_content(self):
        holder = QWidget()
        layout = QVBoxLayout(holder)
        shell = WorkflowShell(holder)
        tabs = QTabWidget(holder)
        layout.addWidget(shell)
        layout.addWidget(tabs)
        tabs.addTab(QWidget(), "legacy page")
        tabs.clear()

        self.assertIs(shell.parentWidget(), holder)
        self.assertEqual(shell._buttons[1].property("workflowActive"), True)

    def test_unknown_health_does_not_use_green_no_alerts_badge(self):
        shell = WorkflowShell()
        shell.set_health_summary("unknown", 0)

        self.assertTrue(shell.alert_button.isHidden())

    def test_legacy_routes_keep_the_correct_workflow_step(self):
        self.assertEqual(resolve_legacy_workflow_step(menu_id=0), 2)
        self.assertEqual(resolve_legacy_workflow_step(menu_id=2), 3)
        self.assertEqual(
            resolve_legacy_workflow_step("Main_User_monitor_data", menu_id=2),
            4,
        )
        self.assertEqual(
            resolve_legacy_workflow_step("Main_Video_image", menu_id=2),
            5,
        )
        self.assertEqual(
            resolve_legacy_workflow_step("MainMouseTrajectoryAnalysis", menu_id=3),
            6,
        )


if __name__ == "__main__":
    unittest.main()
