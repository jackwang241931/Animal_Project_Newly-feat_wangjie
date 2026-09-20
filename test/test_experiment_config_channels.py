"""No-hardware checks for the P2 per-cage configuration contract."""

import os
import inspect
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6 import sip
from PyQt6.QtWidgets import QApplication, QComboBox, QLabel, QPushButton, QScrollArea, QWidget

from public.component.experiment_config.experiment_config_page import ExperimentConfigPage
from Module.new_experiment_setting.index.Tab_1 import Tab_1
from public.config_class.global_setting import global_setting
from public.entity.experiment_setting_entity import Experiment_setting_entity, Group


class ExperimentConfigChannelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        setting = Experiment_setting_entity()
        setting.groups = [Group(id=index, name=f"CH{index:02d}", is_selected=True) for index in range(1, 9)]
        global_setting.set_setting("experiment_setting", setting)
        global_setting.set_setting("experiment_setting_new", setting)

    def test_config_broadcast_ignores_stale_startup_calibration(self):
        setting = global_setting.get_setting("experiment_setting")
        setting.startup_calibration = "Air 空气校准"
        monitor_queue = Mock()
        original_get_setting = global_setting.get_setting
        with patch.object(global_setting, "get_setting") as get_setting:
            get_setting.side_effect = lambda key, default=None: (
                monitor_queue if key == "send_message_queue" else
                None if key == "queue" else
                original_get_setting(key, default)
            )
            ExperimentConfigPage._broadcast_setting(setting)

        self.assertEqual(global_setting.get_setting("startup_calibration_mode"), "none")
        self.assertFalse(global_setting.get_setting("is_auto_calibration"))
        messages = [call.args[0] for call in monitor_queue.put.call_args_list]
        self.assertEqual([message.title for message in messages], ["set_experiment_basic_config", "experiment_setting"])
        self.assertEqual(messages[0].data["startup_calibration_mode"], "none")

    def test_new_experiment_defaults_to_no_startup_calibration(self):
        setting = global_setting.get_setting("experiment_setting")
        monitor_queue = Mock()
        with patch.dict(global_setting._setting, {
            "send_message_queue": monitor_queue, "queue": None,
            "startup_calibration_mode": "full", "is_auto_calibration": True,
        }):
            ExperimentConfigPage._broadcast_setting(setting)
            self.assertEqual(global_setting.get_setting("startup_calibration_mode"), "none")
            self.assertFalse(global_setting.get_setting("is_auto_calibration"))
        config = monitor_queue.put.call_args_list[0].args[0].data
        self.assertEqual(config, {"startup_calibration_mode": "none", "is_auto_calibration": False})

    def test_startup_calibration_selector_is_not_rendered(self):
        setting = global_setting.get_setting("experiment_setting")
        setting.startup_calibration = "完整校准"
        page = ExperimentConfigPage()
        self.addCleanup(page.deleteLater)
        self.assertFalse(hasattr(page, "startup_calibration"))
        self.assertNotIn("启动前校准方式", [label.text() for label in page.findChildren(QLabel)])

    def test_storage_location_button_updates_the_path(self):
        page = ExperimentConfigPage()
        self.addCleanup(page.deleteLater)
        selected_path = os.path.normpath(r"D:\AnimalExperimentData")
        with patch(
            "public.component.experiment_config.experiment_config_page.QFileDialog.getExistingDirectory",
            return_value=selected_path,
        ) as chooser:
            page.storage_browse_button.click()
        chooser.assert_called_once()
        self.assertEqual(page.storage_path.text(), selected_path)

    def test_channel_tabs_are_exclusive_and_values_are_bound(self):
        page = ExperimentConfigPage()
        self.addCleanup(page.deleteLater)
        self.assertEqual(len(page._channel_tab_buttons), 8)
        self.assertEqual(sum(button.isChecked() for button in page._channel_tab_buttons), 1)

        page._data_editors["fps"].setText("11")
        page._animal_editors["individual_id"].setText("M001")
        page._animal_editors["starting_weight_g"].setText("24")
        page._channel_tab_buttons[1].click()
        self.assertEqual(page._selected_channel, 2)
        self.assertEqual(sum(button.isChecked() for button in page._channel_tab_buttons), 1)
        self.assertEqual(page._data_editors["fps"].text(), "30")

        page._data_editors["fps"].setText("22")
        page._data_editors["behavior_category"].setCurrentText("蛰伏")
        page.save_experiment_config()
        setting = global_setting.get_setting("experiment_setting")
        self.assertEqual(setting.channel_data_config["1"]["fps"], "11")
        self.assertEqual(setting.channel_data_config["2"]["fps"], "22")
        self.assertEqual(
            set(setting.channel_modules["1"]),
            {"UFC", "UGC", "ZOS", "weight", "water", "food", "environment", "behavior", "infrared"},
        )
        self.assertEqual(setting.channel_animal_info["1"]["starting_weight_g"], "24")
        self.assertEqual(setting.channel_data_config["2"]["behavior_category"], "蛰伏")
        self.assertEqual(setting.pre_experiment_weights["1"], 24.0)

    def test_enabled_channels_control_visible_config_tabs(self):
        page = ExperimentConfigPage()
        self.addCleanup(page.deleteLater)
        page.show()
        page._channel_tab_buttons[6].click()
        page._data_editors["fps"].setText("17")

        for number in (6, 7, 8):
            page.channel_buttons[number - 1].click()
        self.assertEqual(page._selected_channel, 1)
        self.assertEqual(
            [number for number, button in enumerate(page._channel_tab_buttons, 1) if not button.isHidden()],
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(sum(button.isChecked() for button in page._channel_tab_buttons), 1)

        page.channel_buttons[6].click()
        page._channel_tab_buttons[6].click()
        self.assertEqual(page._data_editors["fps"].text(), "17")

        for number in (1, 2, 3, 4, 5, 7):
            page.channel_buttons[number - 1].click()
        self.assertTrue(all(button.isHidden() for button in page._channel_tab_buttons))
        self.assertTrue(all(box.isHidden() for box in page._channel_editor_boxes))
        self.assertIn("请先在上方启用通道", page._channel_summary.text())

    def test_saved_channel_selection_is_reflected_when_page_opens(self):
        setting = Experiment_setting_entity()
        setting.groups = [
            Group(id=number, name=str(number), is_selected=number in (2, 4, 5))
            for number in range(1, 9)
        ]
        global_setting.set_setting("experiment_setting", setting)
        global_setting.set_setting("experiment_setting_new", setting)
        page = ExperimentConfigPage()
        self.addCleanup(page.deleteLater)
        page.show()
        self.assertEqual(page._selected_channel, 2)
        self.assertEqual(
            [number for number, button in enumerate(page._channel_tab_buttons, 1) if not button.isHidden()],
            [2, 4, 5],
        )

    def test_original_protocol_modules_and_three_behavior_categories(self):
        page = ExperimentConfigPage()
        self.addCleanup(page.deleteLater)
        labels = [check.text() for _, check in page._measurement_checks]
        for code in ("UFC", "UGC", "ZOS"):
            self.assertTrue(any(code in label for label in labels), code)
        for code in ("WM", "EM", "DWM", "ENM"):
            self.assertFalse(any(code in label for label in labels), code)
        self.assertEqual(page._data_editors["behavior_category"].count(), 3)
        self.assertEqual(
            {page._data_editors["behavior_category"].itemText(index) for index in range(3)},
            {"自由活动", "蛰伏", "攀爬"},
        )

    def test_measurement_self_check_is_unknown_without_hardware(self):
        page = ExperimentConfigPage()
        self.addCleanup(page.deleteLater)
        self.assertEqual(
            set(page._measurement_status_labels),
            {"UFC", "UGC", "ZOS", "weight", "water", "food", "environment", "behavior", "infrared"},
        )
        for module in ("UFC", "UGC", "ZOS", "weight", "water", "food", "environment"):
            self.assertEqual(page._measurement_status_labels[module].text(), "自检：未知")
        self.assertEqual(page._measurement_status_labels["behavior"].text(), "自检：未配置")
        self.assertEqual(page._measurement_status_labels["infrared"].text(), "自检：未配置")
        self.assertEqual(len(page.findChildren(QScrollArea)), 1)

    def test_measurement_self_check_uses_real_health_snapshot(self):
        page = ExperimentConfigPage(
            health_source=lambda: {
                "components": [
                    {"component": "gas_path", "scope": "UGC", "status": "normal"},
                    {"component": "gas_path", "scope": "UFC", "status": "fault"},
                ]
            }
        )
        self.addCleanup(page.deleteLater)
        page._refresh_module_self_check()
        self.assertEqual(page._measurement_status_labels["UGC"].text(), "自检：自检正常")
        self.assertEqual(page._measurement_status_labels["UFC"].text(), "自检：故障")
        self.assertEqual(page._measurement_status_labels["ZOS"].text(), "自检：未知")

    def test_one_gas_probe_is_shared_and_four_cage_results_are_visible(self):
        page = ExperimentConfigPage()
        self.addCleanup(page.deleteLater)
        detection = {
            1: {"air_modules": {"UFC": True, "UGC": True, "ZOS": True},
                "cage_modules": {"WM": True, "EM": True, "DWM": True, "ENM": True},
                "detection_complete": True},
            2: {"air_modules": {"UFC": True, "UGC": True, "ZOS": True},
                "cage_modules": {"WM": False}, "detection_complete": True},
        }
        legacy = SimpleNamespace(port_confirmed=True, send_message={"port": "COM7"})
        setting = global_setting.get_setting("experiment_setting")
        with patch.dict(global_setting._setting, {
            "confirmed_serial_setting": setting,
            "confirmed_serial_port": "COM7",
            "mouse_cage_detect_state_dict": detection,
        }), patch.object(
            page, "_legacy_device_page", return_value=legacy
        ), patch.object(
            page, "_read_camera_mapping", side_effect=lambda _kind: {1: "camera-1"}
        ):
            page._refresh_module_self_check()
        self.assertEqual(page._measurement_status_labels["UFC"].text(), "自检：正常")
        self.assertEqual(page._measurement_status_labels["weight"].text(), "自检：正常")
        self.assertEqual(page._channel_tab_buttons[0].property("channelTone"), "normal")
        self.assertEqual(page._channel_tab_buttons[1].property("channelTone"), "fault")
        self.assertFalse(hasattr(page, "shared_air_detection_label"))
        self.assertFalse(hasattr(page, "cage_detection_labels"))

        with patch.dict(global_setting._setting, {
            "confirmed_serial_setting": setting,
            "confirmed_serial_port": "COM7",
            "mouse_cage_detect_state_dict": detection,
        }), patch.object(page, "_legacy_device_page", return_value=legacy), patch.object(
            page, "_read_camera_mapping", side_effect=lambda _kind: {1: "camera-1"}
        ):
            page._channel_tab_buttons[1].click()
            page._refresh_module_self_check()
        self.assertEqual(page._measurement_status_labels["weight"].text(), "自检：异常")
        self.assertEqual(page._measurement_status_labels["water"].text(), "自检：未响应")

    def test_confirmed_port_cannot_be_changed_even_if_legacy_widget_is_reset(self):
        page = ExperimentConfigPage()
        self.addCleanup(page.deleteLater)
        page._serial_ports = [{"device": "COM7", "description": "Test"}, {"device": "COM8", "description": "Other"}]
        page.serial_port_combo.clear()
        page.serial_port_combo.addItem("COM7", "COM7")
        page.serial_port_combo.addItem("COM8", "COM8")
        legacy = SimpleNamespace(port_confirmed=False, detection_in_progress=False, send_message={}, confirm_port=Mock())
        setting = global_setting.get_setting("experiment_setting")
        with patch.dict(global_setting._setting, {
            "port": "COM7", "confirmed_serial_setting": setting, "confirmed_serial_port": "COM7"
        }), patch.object(
            page, "_legacy_device_page", return_value=legacy
        ):
            page._refresh_serial_status()
            page.serial_port_combo.setCurrentIndex(1)
            page._confirm_serial_port()
        self.assertFalse(page.serial_port_combo.isEnabled())
        self.assertFalse(page.serial_refresh_button.isEnabled())
        self.assertFalse(page.serial_confirm_button.isEnabled())
        legacy.confirm_port.assert_not_called()

    def test_stale_global_port_does_not_lock_unconfirmed_experiment(self):
        page = ExperimentConfigPage()
        self.addCleanup(page.deleteLater)
        page._serial_ports = [{"device": "COM7", "description": "Test"}]
        page.serial_port_combo.clear()
        page.serial_port_combo.addItem("COM7", "COM7")
        previous_setting = Experiment_setting_entity()
        legacy = SimpleNamespace(
            port_confirmed=True, experiment_setting=previous_setting,
            detection_in_progress=False, send_message={"port": "COM7"},
        )
        with patch.dict(global_setting._setting, {
            "port": "COM7", "confirmed_serial_setting": previous_setting,
            "confirmed_serial_port": "COM7",
        }), patch.object(page, "_legacy_device_page", return_value=legacy):
            page._refresh_serial_status()
        self.assertTrue(page.serial_port_combo.isEnabled())
        self.assertTrue(page.serial_refresh_button.isEnabled())
        self.assertTrue(page.serial_confirm_button.isEnabled())

    def test_legacy_gas_reply_is_copied_to_all_enabled_cages(self):
        host = SimpleNamespace(
            _is_current_detection_session=lambda _result: True,
            air_module_detection_lock=threading.Lock(),
            _air_detection_finished=False,
            air_modules_completed={"UFC": False, "UGC": False, "ZOS": False},
            air_modules_valid={"UFC": False, "UGC": False, "ZOS": False},
            air_modules_to_detect=["UFC", "UGC", "ZOS"],
            cage_list_to_detect=[1, 2],
            signal_air_module_update=SimpleNamespace(emit=Mock()),
            _process_air_detection_final_results=Mock(),
        )
        detection = {number: {"air_modules": {}, "cage_modules": {}} for number in (1, 2)}
        with patch.dict(global_setting._setting, {"mouse_cage_detect_state_dict": detection}):
            Tab_1.not_each_Mouse_Cage_detect_update_state(
                host, {"module_name": "UFC", "response_state": True}
            )
        self.assertTrue(detection[1]["air_modules"]["UFC"])
        self.assertTrue(detection[2]["air_modules"]["UFC"])
        host.signal_air_module_update.emit.assert_called_once_with("UFC", True)

    def test_channel_tabs_reflect_complete_self_check(self):
        components = [
            {"component": "gas_path", "scope": module, "status": "normal"}
            for module in ("UGC", "UFC", "ZOS")
        ]
        for module in ("WM", "EM", "DWM", "ENM"):
            components.append(
                {
                    "component": "monitor_collection",
                    "scope": "CH01",
                    "status": "normal",
                    "metrics": {"module": module},
                }
            )
        components.extend(
            [
                {"component": "deep_camera", "scope": "CH01", "status": "normal"},
                {"component": "infrared_camera", "scope": "CH01", "status": "normal"},
            ]
        )
        page = ExperimentConfigPage(health_source=lambda: {"components": components})
        self.addCleanup(page.deleteLater)
        with patch.object(page, "_read_camera_mapping", side_effect=lambda _kind: {1: "camera-1"}):
            page._refresh_module_self_check()
            self.assertEqual(page._channel_tab_buttons[0].property("channelTone"), "normal")
            self.assertEqual(page._channel_tab_buttons[1].property("channelTone"), "fault")

            components[1]["status"] = "fault"  # UFC: one real module fault turns CH01 red.
            page._refresh_module_self_check()
            self.assertEqual(page._channel_tab_buttons[0].property("channelTone"), "fault")

    def test_action_bar_and_pending_sampling_placeholder_are_explicit(self):
        page = ExperimentConfigPage()
        self.addCleanup(page.deleteLater)
        self.assertEqual(page.save_button.text(), "保存配置")
        self.assertEqual(page.create_button.text(), "创建配置")
        self.assertEqual(page.import_button.text(), "导入配置")
        self.assertEqual(len({card.maximumHeight() for card in page._top_cards}), 1)
        self.assertIn("待后续定义", page.sample_interval.toolTip())
        self.assertFalse(page.sample_interval.isEnabled())

    def test_save_button_reports_success_without_affecting_silent_auto_save(self):
        page = ExperimentConfigPage()
        self.addCleanup(page.deleteLater)
        with patch.object(page, "save_experiment_config", return_value=True) as save, patch(
            "public.component.experiment_config.experiment_config_page.QMessageBox.information"
        ) as information:
            page.save_button.click()
        save.assert_called_once_with()
        information.assert_called_once_with(page, "保存配置", "配置保存成功。")

    def test_unavailable_self_check_uses_red_fault_styling(self):
        page = ExperimentConfigPage()
        self.addCleanup(page.deleteLater)
        self.assertIn(
            'QLabel#moduleStatus[moduleTone="unavailable"] { color:#d33f49; background:#ffebec; }',
            page.styleSheet(),
        )

    def test_new_configuration_enables_all_channels_by_default(self):
        setting = Experiment_setting_entity()
        global_setting.set_setting("experiment_setting", setting)
        global_setting.set_setting("experiment_setting_new", setting)
        page = ExperimentConfigPage()
        self.addCleanup(page.deleteLater)
        self.assertEqual(len(page.channel_buttons), 8)
        self.assertTrue(all(button.isChecked() for button in page.channel_buttons))

    def test_camera_mapping_panel_shows_both_camera_types(self):
        page = ExperimentConfigPage()
        self.addCleanup(page.deleteLater)
        self.assertEqual(set(page._camera_mapping_labels), {"deep", "infrared"})
        self.assertEqual(len(page._camera_mapping_labels["deep"]), 8)
        self.assertEqual(len(page._camera_mapping_labels["infrared"]), 8)
        self.assertIn("CH01", page._camera_mapping_labels["deep"][0].text())
        self.assertIn("未配置", page._camera_mapping_labels["infrared"][0].text())

    def test_camera_self_check_follows_each_cage_mapping(self):
        page = ExperimentConfigPage()
        self.addCleanup(page.deleteLater)
        mappings = {"deep": {2: "deep-2"}, "infrared": {1: "infrared-1"}}
        with patch.object(page, "_read_camera_mapping", side_effect=lambda kind: mappings[kind]):
            page._selected_channel = 1
            page._refresh_module_self_check()
            self.assertEqual(page._measurement_status_labels["behavior"].text(), "自检：未配置")
            self.assertEqual(page._measurement_status_labels["infrared"].text(), "自检：正常")

            page._selected_channel = 2
            page._refresh_module_self_check()
            self.assertEqual(page._measurement_status_labels["behavior"].text(), "自检：正常")
            self.assertEqual(page._measurement_status_labels["infrared"].text(), "自检：未配置")

    def test_serial_confirmation_button_uses_short_label(self):
        page = ExperimentConfigPage()
        self.addCleanup(page.deleteLater)
        self.assertEqual(page.serial_confirm_button.text(), "确认串口")

    def test_serial_confirmation_failure_stays_visible_after_status_refresh(self):
        page = ExperimentConfigPage()
        self.addCleanup(page.deleteLater)
        page._serial_ports = [{"device": "COM7", "description": "Test"}]
        page.serial_port_combo.addItem("COM7", "COM7")
        legacy = SimpleNamespace(
            port_confirmed=False,
            detection_in_progress=False,
            port_combox=QComboBox(),
            send_message={},
            device_config_ready=False,
            confirm_port=Mock(return_value=False),
        )
        with patch.object(page, "_legacy_device_page", return_value=legacy), patch.object(
            page, "save_experiment_config"
        ), patch.object(global_setting, "get_setting", side_effect=lambda key, default=None: Mock() if key == "send_message_queue" else self._setting_for_test(key, default)):
            page._confirm_serial_port()
            self.assertIn("未开始检测", page.serial_status_label.text())
            page._refresh_serial_status()
            self.assertIn("未开始检测", page.serial_status_label.text())

    def test_deleted_legacy_device_page_is_rebuilt_and_rebound(self):
        class MainWindow(QWidget):
            def navigate_workflow_step(self, _step):
                pass

        main_window = MainWindow()
        self.addCleanup(main_window.deleteLater)
        page = ExperimentConfigPage(parent=main_window)
        self.addCleanup(page.deleteLater)
        old_frame = QWidget()
        old_frame.port_combox = QComboBox(old_frame)
        old_frame.current_detection_session_id = 2
        sip.delete(old_frame)
        new_frame = QWidget(main_window)
        new_frame.port_combox = QComboBox(new_frame)
        new_frame.current_detection_session_id = 0
        new_frame.set_main_gui = Mock()
        interface = SimpleNamespace(frame_obj=old_frame, create_middle_window=Mock(side_effect=lambda: new_frame))
        main_window.modules = [SimpleNamespace(name="New_main_experiment_setting", interface_widget=interface)]

        self.assertIs(page._legacy_device_page(), new_frame)
        self.assertIs(interface.frame_obj, new_frame)
        self.assertEqual(new_frame.current_detection_session_id, 3)
        interface.create_middle_window.assert_called_once_with()
        new_frame.set_main_gui.assert_called_once_with(main_window)

    def test_rebuilt_legacy_device_page_keeps_confirmed_port_locked(self):
        class MainWindow(QWidget):
            def navigate_workflow_step(self, _step):
                pass

        main_window = MainWindow()
        self.addCleanup(main_window.deleteLater)
        page = ExperimentConfigPage(parent=main_window)
        self.addCleanup(page.deleteLater)
        page._serial_ports = [{"device": "COM7", "description": "Test"}]
        old_frame = QWidget()
        old_frame.port_combox = QComboBox(old_frame)
        old_frame.current_detection_session_id = 1
        sip.delete(old_frame)
        new_frame = QWidget(main_window)
        new_frame.port_combox = QComboBox(new_frame)
        new_frame.current_detection_session_id = 0
        new_frame.port_confirmed = False
        new_frame.send_message = {}
        new_frame.set_main_gui = Mock()
        interface = SimpleNamespace(frame_obj=old_frame, create_middle_window=Mock(return_value=new_frame))
        main_window.modules = [SimpleNamespace(name="New_main_experiment_setting", interface_widget=interface)]

        with patch.dict(global_setting._setting, {
            "confirmed_serial_setting": global_setting.get_setting("experiment_setting"),
            "confirmed_serial_port": "COM7",
        }):
            self.assertIs(page._legacy_device_page(), new_frame)
        self.assertTrue(new_frame.port_confirmed)
        self.assertEqual(new_frame.send_message["port"], "COM7")
        self.assertFalse(new_frame.port_combox.isEnabled())

    def test_confirm_and_redetect_keep_direct_start_as_default(self):
        setting = global_setting.get_setting("experiment_setting")
        setting.startup_calibration = "Air 空气校准"
        page = ExperimentConfigPage()
        self.addCleanup(page.deleteLater)
        page._serial_ports = [{"device": "COM7", "description": "Test"}]
        page.serial_port_combo.clear()
        page.serial_port_combo.addItem("COM7", "COM7")
        legacy = SimpleNamespace(
            port_confirmed=False, detection_in_progress=False,
            port_combox=QComboBox(), send_message={"port": "COM7"},
            device_config_ready=False, ports=[], experiment_setting=None,
        )
        def begin_detection():
            global_setting.set_setting("startup_calibration_mode", "none")
            legacy.port_confirmed = True
            legacy.detection_in_progress = True
            return True
        legacy.confirm_port = Mock(side_effect=begin_detection)
        legacy.refresh_detection = Mock(side_effect=begin_detection)
        monitor_queue = Mock()
        with patch.dict(global_setting._setting, {"send_message_queue": monitor_queue, "queue": None}), patch.object(
            page, "_legacy_device_page", return_value=legacy
        ), patch.object(page, "save_experiment_config"):
            page._confirm_serial_port()
            self.assertEqual(global_setting.get_setting("startup_calibration_mode"), "none")
            legacy.detection_in_progress = False
            page._refresh_serial_detection()
            self.assertEqual(global_setting.get_setting("startup_calibration_mode"), "none")
        legacy.confirm_port.assert_called_once_with()
        legacy.refresh_detection.assert_called_once_with()

    def test_redetect_waits_for_air_and_cage_detection_to_finish(self):
        page = ExperimentConfigPage()
        self.addCleanup(page.deleteLater)
        page._serial_ports = [{"device": "COM7", "description": "Test"}]
        legacy = SimpleNamespace(
            port_confirmed=True, detection_in_progress=False,
            _air_detection_finished=False, _cage_detection_finished=True,
            send_message={"port": "COM7"}, device_config_ready=False,
        )
        with patch.dict(global_setting._setting, {
            "confirmed_serial_setting": global_setting.get_setting("experiment_setting"),
            "confirmed_serial_port": "COM7",
        }), patch.object(page, "_legacy_device_page", return_value=legacy):
            page._refresh_serial_status()
            self.assertFalse(page.serial_detect_button.isEnabled())
            legacy._air_detection_finished = True
            page._refresh_serial_status()
        self.assertTrue(page.serial_detect_button.isEnabled())

    def test_detector_keeps_original_gas_then_four_cage_module_sequence(self):
        begin = inspect.getsource(Tab_1._start_detection_cycle)
        cage = inspect.getsource(Tab_1._detect_next_cage)
        self.assertLess(begin.index("title='set_port'"), begin.index('title="detect_air_modules_only"'))
        self.assertIn('title="detect_cage_modules_only"', cage)
        host = SimpleNamespace(_environment_module_only_enabled=lambda: False)
        self.assertEqual(Tab_1._required_cage_modules(host), {"ENM", "EM", "DWM", "WM"})

    def test_legacy_confirmation_rolls_back_when_detection_cannot_start(self):
        host = QWidget()
        self.addCleanup(host.deleteLater)
        host.port_combox = QComboBox(host)
        host.port_combox.addItem("COM7")
        host.ports = [{"device": "COM7"}]
        host.send_message = {}
        host.port_confirmed = False
        host.detection_in_progress = False
        host.confirm_port_btn = QPushButton(host)
        host.config_btn = None
        host.start_btn = None
        host._start_detection_cycle = Mock(return_value=False)
        host.update_device_config_button_state = Mock()
        with patch.object(global_setting, "get_setting", return_value="COM1"), patch.object(
            global_setting, "set_setting"
        ) as set_setting:
            self.assertFalse(Tab_1.confirm_port(host))
        self.assertFalse(host.port_confirmed)
        self.assertTrue(host.port_combox.isEnabled())
        set_setting.assert_any_call("port", "COM1")

    @staticmethod
    def _setting_for_test(key, default):
        if key == "experiment_setting":
            setting = Experiment_setting_entity()
            setting.groups = [Group(id=1, name="CH01", is_selected=True)]
            return setting
        return default


if __name__ == "__main__":
    unittest.main()
