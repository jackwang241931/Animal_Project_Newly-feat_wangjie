from __future__ import annotations

import os
import copy
import time
from typing import Any, Mapping, Optional

from PyQt6.QtCore import QTimer, Qt, QTime, pyqtSignal
from PyQt6 import sip
from loguru import logger
from PyQt6.QtWidgets import (
    QComboBox, QFrame, QGridLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QCheckBox, QScrollArea, QSizePolicy, QVBoxLayout, QWidget,
    QSpinBox, QTimeEdit, QButtonGroup, QFileDialog, QMessageBox,
)
from public.config_class.global_setting import global_setting
from public.entity.experiment_setting_entity import Group, Animal, AnimalGroupRecord
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.function.Modbus.COM_Scan import scan_serial_ports_with_id
from public.util.time_util import time_util
from public.dao.SQLite.Experiment_Setting_DAO_Handle import Experiment_Setting_DAO_Handle
from public.util.custom_data_file_util import custom_template_file_util
from public.util.lighting_schedule import (
    LIGHTING_COLOR_PRESETS,
    default_lighting_schedule,
    normalize_lighting_schedule,
)


class BehaviorCategorySelector(QWidget):
    """Multi-select behaviour chips persisted as a comma-separated value."""

    OPTIONS = ("自由活动", "蛰伏", "攀爬")
    _ALIASES = {"睡眠": "蛰伏"}
    selectionChanged = pyqtSignal()

    def __init__(self, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("behaviorCategorySelector")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(7)
        self.buttons: dict[str, QPushButton] = {}
        for option in self.OPTIONS:
            button = QPushButton(option)
            button.setCheckable(True)
            button.setMinimumHeight(30)
            button.setObjectName("behaviorChip")
            button.setStyleSheet(
                "QPushButton#behaviorChip { background:#fff; color:#1767ad; border:1px solid #b9d7f1; border-radius:15px; padding:3px 13px; font-weight:700; }"
                "QPushButton#behaviorChip:checked { background:#258dde; color:#fff; border-color:#258dde; }"
            )
            button.toggled.connect(lambda _checked=False: self.selectionChanged.emit())
            self.buttons[option] = button
            layout.addWidget(button)
        layout.addStretch(1)

    def selected_values(self) -> list[str]:
        return [option for option, button in self.buttons.items() if button.isChecked()]

    def set_selected_values(self, values: Any) -> None:
        if isinstance(values, (list, tuple, set)):
            raw_values = [str(value).strip() for value in values]
        else:
            raw_values = [item.strip() for item in str(values or "").replace("，", ",").split(",") if item.strip()]
        normalized = {self._ALIASES.get(value, value) for value in raw_values}
        if not normalized and not raw_values:
            normalized = {"自由活动"}
        for option, button in self.buttons.items():
            button.blockSignals(True)
            button.setChecked(option in normalized)
            button.blockSignals(False)

    # QComboBox-compatible helpers keep older template/UI integrations working.
    def currentText(self) -> str:
        return ",".join(self.selected_values())

    def setCurrentText(self, value: str) -> None:
        self.set_selected_values(value)
        self.selectionChanged.emit()

    def count(self) -> int:
        return len(self.OPTIONS)

    def itemText(self, index: int) -> str:
        return self.OPTIONS[int(index)]


class ExperimentConfigPage(QWidget):
    """Compact P2 configuration shell matching the confirmed UI layout."""

    SPECIES_OPTIONS = ("大鼠", "小鼠")
    GROUPING_PLANS = {
        "对照组 / 高脂组 / 运动组": ("对照组", "高脂组", "运动组"),
        "对照组 / 模型组": ("对照组", "模型组"),
        "单组基线": tuple(f"分组{index}" for index in range(1, 9)),
    }
    BEHAVIOR_CATEGORIES = BehaviorCategorySelector.OPTIONS

    MEASUREMENT_MODULES = (
        ("UGC", "UGC（二氧化碳）"),
        ("UFC", "UFC（气路流量）"),
        ("ZOS", "ZOS（氧气）"),
        ("weight", "称重模块"),
        ("water", "饮水模块"),
        ("food", "饮食模块"),
        ("environment", "环境模块"),
        ("behavior", "行为识别/轨迹相机"),
        ("infrared", "热红外模块"),
    )
    DETECTION_MODULE_KEYS = {
        "weight": "WM", "water": "EM", "food": "DWM", "environment": "ENM",
    }

    def __init__(self, context_provider: Any = None, health_source: Any = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._context_provider = context_provider
        self._health_source = health_source
        self._selected_channel = 1
        self._measurement_checks = []
        self._measurement_status_labels = {}
        self._data_editors = {}
        self._animal_editors = {}
        self._channel_tab_group = None
        self._loading_channel = False
        self._channel_module_values = {}
        self._channel_data_values = {}
        self._channel_animal_values = {}
        self._camera_mapping_labels = {"deep": [], "infrared": []}
        self._serial_ports = []
        self._serial_action_error = None
        self._restore_last_saved_configuration()
        self._build_ui()
        self._bind_auto_save()
        self._refresh_serial_ports()
        self._health_timer = QTimer(self)
        self._health_timer.setInterval(2000)
        self._health_timer.timeout.connect(self._refresh_module_self_check)
        self._health_timer.timeout.connect(self._refresh_serial_status)
        self._health_timer.start()
        self._refresh_module_self_check()
        self._refresh_serial_status()

    def _build_ui(self) -> None:
        self.setObjectName("experimentConfigPage")
        self.setStyleSheet("""
            QWidget#experimentConfigPage { background:#f2f8fd; color:#17395c; font-family:"Microsoft YaHei","SimSun"; }
            QFrame#configCard, QFrame#channelPanel { background:#fff; border:1px solid #d5e5f3; border-radius:10px; }
            QFrame#cameraMappingPanel { background:#fff; border:1px solid #d5e5f3; border-radius:10px; }
            QFrame#cameraMappingColumn { background:#f7fbff; border:1px solid #dcebf7; border-radius:8px; }
            QLabel#sectionTitle { color:#075fae; font-size:14px; font-weight:700; }
            QLabel#fieldLabel { color:#567b9e; font-size:12px; min-height:18px; }
            QLineEdit, QComboBox { min-height:34px; border:1px solid #cfe1f1; border-radius:7px; padding:2px 9px; background:#fff; }
            QPushButton#channelButton { min-width:58px; min-height:30px; border:1px solid #cfe1f1; border-radius:8px; background:#fff; color:#17395c; }
            QPushButton#channelButton:checked { background:#258dde; color:#fff; border-color:#258dde; }
            QPushButton#channelConfigButton { min-width:74px; min-height:32px; border:1px solid #cfe1f1; border-radius:8px; background:#fff; color:#17395c; }
            QPushButton#channelConfigButton:checked { background:#258dde; color:#fff; border-color:#258dde; }
            QPushButton#channelConfigButton[channelTone="normal"] { background:#e4f7ed; color:#178850; border-color:#63c994; }
            QPushButton#channelConfigButton[channelTone="fault"] { background:#ffebec; color:#d33f49; border-color:#ef858b; }
            /* Fill colour is health; selected cages use a stronger same-colour outline. */
            QPushButton#channelConfigButton:checked { color:#17395c; font-weight:700; }
            QPushButton#channelConfigButton:checked[channelTone="normal"] { background:#d8f4e4; border:2px solid #178850; }
            QPushButton#channelConfigButton:checked[channelTone="fault"] { background:#ffe0e2; border:2px solid #d33f49; }
            QPushButton#channelConfigButton:checked[channelTone=""] { background:#eef6ff; border:2px solid #258dde; }
            QLabel#statusNote { background:#eef6ff; border-radius:8px; padding:9px; color:#6c8eaf; }
            QLabel#mappingPill { background:#edf1f5; border:1px solid #d5e1eb; border-radius:7px; padding:3px 7px; color:#56616d; font-size:11px; }
            QLabel#mappingPill[mappingState="configured"] { background:#e4f7ed; border-color:#9cdbb8; color:#178850; }
            QLabel#moduleStatus { border-radius:8px; padding:2px 5px; color:#6b7785; background:#edf1f5; font-size:11px; }
            QLabel#moduleStatus[moduleTone="normal"] { color:#178850; background:#e4f7ed; }
            QLabel#moduleStatus[moduleTone="warning"] { color:#b56800; background:#fff4df; }
            QLabel#moduleStatus[moduleTone="fault"] { color:#d33f49; background:#ffebec; }
            QLabel#moduleStatus[moduleTone="unavailable"] { color:#d33f49; background:#ffebec; }
            QFrame#configActionBar { background:#fff; border-top:1px solid #d5e5f3; }
            QPushButton#primaryAction, QPushButton#secondaryAction { min-height:40px; padding:4px 20px; border-radius:9px; font-size:13px; font-weight:700; }
            QPushButton#primaryAction { color:#fff; background:#1f78c8; border:1px solid #1f78c8; }
            QPushButton#primaryAction:hover { background:#1767ad; }
            QPushButton#secondaryAction { color:#1767ad; background:#fff; border:1px solid #b9d7f1; }
            QPushButton#secondaryAction:hover { background:#eef7ff; }
            QComboBox:disabled { color:#7188a0; background:#f3f6f9; }
        """)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        scroll = QScrollArea(self)
        scroll.setObjectName("experimentConfigScroll")
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        canvas = QWidget(scroll)
        root = QVBoxLayout(canvas)
        root.setContentsMargins(12, 10, 12, 18)
        root.setSpacing(12)
        title = QLabel("实验环境配置")
        title.setObjectName("sectionTitle")
        root.addWidget(title, alignment=Qt.AlignmentFlag.AlignRight)

        cards = QGridLayout(); cards.setHorizontalSpacing(12); cards.setVerticalSpacing(12)
        cards.setColumnStretch(0, 1)
        cards.setColumnStretch(1, 1)
        cards.setColumnStretch(2, 1)
        self._top_cards = [
            self._basic_card(),
            self._environment_card(),
            self._collection_card(),
        ]
        # Keep the three top-level configuration cards visually aligned even
        # when one card contains fewer fields than the others.
        top_card_height = max(
            max(card.sizeHint().height(), card.minimumSizeHint().height())
            for card in self._top_cards
        )
        for column, card in enumerate(self._top_cards):
            card.setMinimumHeight(top_card_height)
            card.setMaximumHeight(top_card_height)
            cards.addWidget(card, 0, column)
        root.addLayout(cards)
        root.addWidget(self._serial_panel())
        root.addWidget(self._camera_mapping_panel())
        root.addWidget(self._channel_panel())
        action_bar = QFrame()
        action_bar.setObjectName("configActionBar")
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(8, 8, 8, 8)
        action_layout.setSpacing(10)
        self.save_button = QPushButton("保存配置")
        self.save_button.setObjectName("primaryAction")
        self.save_button.clicked.connect(self._save_experiment_config_with_feedback)
        self.create_button = QPushButton("创建配置")
        self.create_button.setObjectName("secondaryAction")
        self.create_button.clicked.connect(self.create_configuration)
        self.import_button = QPushButton("导入配置")
        self.import_button.setObjectName("secondaryAction")
        self.import_button.clicked.connect(self.import_configuration)
        self.apply_button = QPushButton("应用并进入实验控制 →")
        self.apply_button.setObjectName("primaryAction")
        self.apply_button.clicked.connect(self.apply_and_open_control)
        self.return_button = QPushButton("返回系统状态")
        self.return_button.setObjectName("secondaryAction")
        self.return_button.clicked.connect(self.return_to_system_status)
        action_layout.addWidget(self.save_button)
        action_layout.addWidget(self.create_button)
        action_layout.addWidget(self.import_button)
        action_layout.addStretch(1)
        action_layout.addWidget(self.apply_button)
        action_layout.addWidget(self.return_button)
        root.addWidget(action_bar)
        root.addStretch(1)
        scroll.setWidget(canvas)
        outer.addWidget(scroll)
        # At construction time Qt has not resolved the final card widths yet;
        # recompute once the scroll canvas is laid out so child rows cannot be
        # compressed into one another (especially the eight channel buttons).
        QTimer.singleShot(0, self._equalize_top_cards)

    def _equalize_top_cards(self) -> None:
        cards = getattr(self, "_top_cards", ())
        if not cards:
            return
        height = max(
            max(card.sizeHint().height(), card.minimumSizeHint().height())
            for card in cards
        )
        for card in cards:
            card.setMinimumHeight(height)
            card.setMaximumHeight(height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        QTimer.singleShot(0, self._equalize_top_cards)

    def _card(self, title: str) -> tuple[QFrame, QVBoxLayout]:
        card = QFrame(); card.setObjectName("configCard")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        layout = QVBoxLayout(card); layout.setContentsMargins(16, 13, 16, 16); layout.setSpacing(8)
        label = QLabel(title); label.setObjectName("sectionTitle"); layout.addWidget(label)
        return card, layout

    def _serial_panel(self) -> QFrame:
        card, layout = self._card("串口与设备检测")
        row = QHBoxLayout(); row.setSpacing(8)
        self.serial_port_combo = QComboBox()
        self.serial_port_combo.setMinimumWidth(260)
        self.serial_refresh_button = QPushButton("刷新串口")
        self.serial_refresh_button.setObjectName("secondaryAction")
        self.serial_refresh_button.clicked.connect(self._refresh_serial_ports)
        self.serial_confirm_button = QPushButton("确认串口")
        self.serial_confirm_button.setObjectName("primaryAction")
        self.serial_confirm_button.clicked.connect(self._confirm_serial_port)
        self.serial_detect_button = QPushButton("重新检测")
        self.serial_detect_button.setObjectName("secondaryAction")
        self.serial_detect_button.clicked.connect(self._refresh_serial_detection)
        for widget in (self.serial_port_combo, self.serial_refresh_button,
                       self.serial_confirm_button, self.serial_detect_button):
            row.addWidget(widget)
        row.addStretch(1)
        layout.addLayout(row)
        self.serial_status_label = QLabel("请选择串口并确认，随后检测已启用的笼位。")
        self.serial_status_label.setObjectName("statusNote")
        layout.addWidget(self.serial_status_label)
        return card

    def _legacy_device_page(self):
        main_window = self._find_main_window()
        for module in getattr(main_window, "modules", ()) if main_window is not None else ():
            if getattr(module, "name", None) == "New_main_experiment_setting":
                interface = getattr(module, "interface_widget", None)
                if interface is None:
                    return None
                frame = getattr(interface, "frame_obj", None)
                combo = getattr(frame, "port_combox", None)
                if (frame is None or sip.isdeleted(frame)
                        or combo is None or sip.isdeleted(combo)):
                    try:
                        previous_session = int(getattr(frame, "current_detection_session_id", 0) or 0)
                        # The legacy builder is also shipped without a parent
                        # parameter.  Keep this controller unparented so a
                        # workflow-tab replacement cannot delete it again.
                        frame = interface.create_middle_window()
                        frame.current_detection_session_id = max(
                            frame.current_detection_session_id, previous_session + 1
                        )
                        frame.set_main_gui(main_window)
                        frame.hide()
                        interface.frame_obj = frame
                    except Exception as error:
                        self._set_serial_action_error(f"设备配置模块重建失败：{error}")
                        return None
                confirmed_port = self._confirmed_serial_port()
                if confirmed_port and not getattr(frame, "port_confirmed", False):
                    # The hidden legacy widget may be recreated by navigation.
                    # Its lifetime must not unlock a previously confirmed port.
                    ports = list(self._serial_ports)
                    if not any(item.get("device") == confirmed_port for item in ports):
                        ports.append({"device": confirmed_port, "description": "已确认串口"})
                    frame.ports = ports
                    frame.send_message["port"] = confirmed_port
                    old_signal_state = frame.port_combox.blockSignals(True)
                    try:
                        frame.port_combox.clear()
                        for item in ports:
                            frame.port_combox.addItem(f"设备: {item['device']} - {item.get('description', '')}")
                        frame.port_combox.setCurrentIndex(next(
                            index for index, item in enumerate(ports)
                            if item["device"] == confirmed_port
                        ))
                    finally:
                        frame.port_combox.blockSignals(old_signal_state)
                    frame.port_confirmed = True
                    frame.port_combox.setEnabled(False)
                return frame
        return None

    def _refresh_serial_ports(self) -> None:
        self._serial_action_error = None
        legacy = self._legacy_device_page()
        confirmed_port = self._confirmed_serial_port()
        try:
            ports = scan_serial_ports_with_id()
        except Exception as error:
            self.serial_status_label.setText(f"串口扫描失败：{error}")
            ports = []
        self._serial_ports = [item for item in ports if isinstance(item, dict) and item.get("device")]
        self.serial_port_combo.clear()
        for item in self._serial_ports:
            self.serial_port_combo.addItem(
                f"{item['device']} - {item.get('description', '')}", item["device"]
            )
        if confirmed_port:
            index = self.serial_port_combo.findData(confirmed_port)
            if index < 0:
                self.serial_port_combo.addItem(f"{confirmed_port} - 当前未检测到", confirmed_port)
                index = self.serial_port_combo.count() - 1
            self.serial_port_combo.setCurrentIndex(index)
        self._refresh_serial_status()

    def _refresh_serial_status(self) -> None:
        legacy = self._legacy_device_page()
        confirmed_port = self._confirmed_serial_port()
        confirmed = bool(confirmed_port)
        busy = bool(
            getattr(legacy, "detection_in_progress", False)
            or not getattr(legacy, "_air_detection_finished", True)
            or not getattr(legacy, "_cage_detection_finished", True)
        )
        available = bool(self._serial_ports)
        port_present = any(item.get("device") == confirmed_port for item in self._serial_ports)
        self.serial_port_combo.setEnabled(available and not confirmed)
        self.serial_refresh_button.setEnabled(not confirmed)
        self.serial_confirm_button.setEnabled(available and not confirmed and legacy is not None)
        self.serial_detect_button.setEnabled(confirmed and port_present and not busy and legacy is not None)
        if self._serial_action_error:
            self.serial_status_label.setText(self._serial_action_error)
            return
        if legacy is None:
            self.serial_status_label.setText("设备配置模块未就绪，无法确认串口。")
        elif not confirmed:
            self.serial_status_label.setText("请选择串口并确认；确认后将检测已启用的笼位。" if available else "未发现可用串口，请连接设备后刷新。")
        elif not port_present:
            self.serial_status_label.setText(f"已确认的串口 {confirmed_port} 当前未检测到，请检查连接后刷新串口。")
        elif busy:
            self.serial_status_label.setText("串口已确认，正在检测设备；请等待检测完成。")
        else:
            detection = global_setting.get_setting("mouse_cage_detect_state_dict", {}) or {}
            valid = [str(cage) for cage, state in detection.items()
                     if isinstance(state, dict) and state.get("cage_is_valid", False)]
            port = confirmed_port
            if not getattr(legacy, "device_config_ready", False):
                self.serial_status_label.setText(f"串口 {port} 已确认，等待设备检测。")
            elif valid:
                self.serial_status_label.setText(f"串口 {port} 检测完成；可配置笼位：{', '.join(valid)}。")
            else:
                self.serial_status_label.setText(f"串口 {port} 检测完成，但没有检测通过的笼位。")

    @staticmethod
    def _confirmed_serial_port():
        setting = global_setting.get_setting("experiment_setting", None)
        if setting is None:
            return None
        if global_setting.get_setting("confirmed_serial_setting", None) is setting:
            return global_setting.get_setting("confirmed_serial_port", None)
        return None

    @staticmethod
    def _detection_record(detection, channel: int):
        record = detection.get(channel, detection.get(str(channel), {}))
        return record if isinstance(record, Mapping) else {}

    def _detected_module_result(self, module: str, channel: int, legacy, detection):
        """Read one confirmed detection round, never infer success from a checkbox."""
        if not self._confirmed_serial_port():
            return None
        if module in {"UFC", "UGC", "ZOS"}:
            if global_setting.get_setting("environment_module_only", False):
                return "已跳过", "unknown"
            record = self._detection_record(detection, channel)
            shared = record.get("air_modules", {})
            if module in shared:
                return ("正常", "normal") if shared[module] else ("异常", "fault")
            completed = getattr(legacy, "air_modules_completed", {}) or {}
            if completed.get(module):
                valid = (getattr(legacy, "air_modules_valid", {}) or {}).get(module, False)
                return ("正常", "normal") if valid else ("异常", "fault")
            return "待检测", "unknown"
        cage_module = self.DETECTION_MODULE_KEYS.get(module)
        if cage_module is None:
            return None
        record = self._detection_record(detection, channel)
        cage_modules = record.get("cage_modules", {})
        if cage_module in cage_modules:
            return ("正常", "normal") if cage_modules[cage_module] else ("异常", "fault")
        if record.get("detection_complete") or (getattr(legacy, "_completed_cages", {}) or {}).get(channel):
            return "未响应", "fault"
        if global_setting.get_setting("environment_module_only", False) and cage_module != "ENM":
            return "已跳过", "unknown"
        return "待检测", "unknown"

    def _channel_detection_tone(self, channel: int, detection):
        record = self._detection_record(detection, channel)
        if not record:
            return None
        cage_modules = record.get("cage_modules", {})
        air_modules = record.get("air_modules", {})
        required_cage = {"ENM"} if global_setting.get_setting("environment_module_only", False) else {"WM", "EM", "DWM", "ENM"}
        required_air = set() if global_setting.get_setting("environment_module_only", False) else {"UFC", "UGC", "ZOS"}
        if any(name in cage_modules and not cage_modules[name] for name in required_cage):
            return "fault"
        if any(name in air_modules and not air_modules[name] for name in required_air):
            return "fault"
        if record.get("detection_complete") and (
            not required_cage.issubset(cage_modules) or not required_air.issubset(air_modules)
        ):
            return "fault"
        if required_cage.issubset(cage_modules) and required_air.issubset(air_modules):
            return "normal"
        return None

    def _set_serial_action_error(self, message: str) -> None:
        self._serial_action_error = message
        self.serial_status_label.setText(message)

    def _confirm_serial_port(self) -> None:
        self._serial_action_error = None
        port = self.serial_port_combo.currentData()
        if not port:
            self._set_serial_action_error("请先选择有效串口，并确认设备配置模块已就绪。")
            return
        legacy = self._legacy_device_page()
        if legacy is None:
            if not self._serial_action_error:
                self._set_serial_action_error("设备配置模块未就绪，无法确认串口。")
            return
        if self._confirmed_serial_port():
            self._refresh_serial_status()
            return
        if global_setting.get_setting("send_message_queue", None) is None:
            self._set_serial_action_error("后台采集服务未连接，无法确认串口。")
            return
        try:
            self.save_experiment_config()
        except Exception as error:
            logger.exception("新版实验配置页保存配置失败，串口确认已取消")
            self._set_serial_action_error(f"保存实验配置失败，未确认串口：{error}")
            return
        legacy = self._legacy_device_page()
        if legacy is None:
            if not self._serial_action_error:
                self._set_serial_action_error("设备配置模块已失效，请刷新串口后重试。")
            return
        setting = global_setting.get_setting("experiment_setting", None)
        if setting is None:
            self._set_serial_action_error("请先创建或导入实验配置。")
            return
        selected = [group for group in getattr(setting, "groups", []) or [] if getattr(group, "is_selected", False)]
        if not selected:
            self._set_serial_action_error("请先启用至少一个笼位。")
            return
        legacy.experiment_setting = setting
        legacy.ports = list(self._serial_ports)
        try:
            old_signal_state = legacy.port_combox.blockSignals(True)
            try:
                legacy.port_combox.clear()
                for item in legacy.ports:
                    legacy.port_combox.addItem(f"设备: {item['device']} - {item.get('description', '')}")
                legacy.port_combox.setCurrentIndex(self.serial_port_combo.currentIndex())
            finally:
                legacy.port_combox.blockSignals(old_signal_state)
            confirmed = legacy.confirm_port()
        except Exception as error:
            logger.exception("新版实验配置页确认串口失败")
            self._set_serial_action_error(f"确认串口失败：{error}")
            return
        if confirmed is False or not getattr(legacy, "detection_in_progress", False):
            self._set_serial_action_error("串口未开始检测；请检查后台采集服务后重试。")
            return
        global_setting.set_setting("confirmed_serial_setting", setting)
        global_setting.set_setting("confirmed_serial_port", port)
        # The legacy detector resets its own calibration selection at the
        # beginning of each round.  P2's saved startup choice remains the
        # authority for the later experiment start.
        self._broadcast_setting(setting)
        self._refresh_serial_status()

    def _refresh_serial_detection(self) -> None:
        self._serial_action_error = None
        legacy = self._legacy_device_page()
        if legacy is None or not self._confirmed_serial_port():
            self.serial_status_label.setText("请先确认串口。")
            return
        if not any(item.get("device") == (getattr(legacy, "send_message", {}) or {}).get("port")
                   for item in self._serial_ports):
            self.serial_status_label.setText("已确认的串口当前未检测到，请检查连接后刷新串口。")
            return
        if getattr(legacy, "detection_in_progress", False):
            return
        self.save_experiment_config()
        setting = global_setting.get_setting("experiment_setting", None)
        if setting is None:
            self._set_serial_action_error("请先创建或导入实验配置。")
            return
        legacy.experiment_setting = setting
        try:
            legacy.refresh_detection()
        except Exception as error:
            logger.exception("新版实验配置页重新检测失败")
            self._set_serial_action_error(f"重新检测失败：{error}")
            return
        if not getattr(legacy, "detection_in_progress", False):
            self._set_serial_action_error("重新检测未启动；请检查后台采集服务后重试。")
            return
        self._broadcast_setting(setting)
        self._refresh_serial_status()

    def _field(self, layout: QVBoxLayout, name: str, value: str = "", combo: bool = False):
        label = QLabel(name); label.setObjectName("fieldLabel"); layout.addWidget(label)
        widget = QComboBox() if combo else QLineEdit()
        if combo:
            widget.addItems([value] if value else ["请选择"])
        else:
            widget.setText(value)
        layout.addWidget(widget)
        return widget

    def _basic_card(self) -> QFrame:
        card, layout = self._card("实验基本信息")
        setting = global_setting.get_setting("experiment_setting", None) or global_setting.get_setting("experiment_setting_new", None)
        self.experiment_id = self._field(layout, "研究编号", str(getattr(setting, "experiment_id", "")))
        self.experiment_title = self._field(layout, "实验标题", str(getattr(setting, "experiment_title", "")))
        saved_species = str(getattr(setting, "animal_species", "小鼠") or "小鼠")
        if saved_species not in self.SPECIES_OPTIONS:
            saved_species = "大鼠" if "大鼠" in saved_species else "小鼠"
        self.species = self._field(layout, "实验动物品系", "", True)
        self.species.clear(); self.species.addItems(self.SPECIES_OPTIONS); self.species.setCurrentText(saved_species)
        saved_plan = str(getattr(setting, "grouping_plan", "对照组 / 高脂组 / 运动组") or "对照组 / 高脂组 / 运动组")
        if saved_plan not in self.GROUPING_PLANS:
            saved_plan = "对照组 / 高脂组 / 运动组"
        self.grouping = self._field(layout, "分组方案", "", True)
        self.grouping.clear(); self.grouping.addItems(tuple(self.GROUPING_PLANS)); self.grouping.setCurrentText(saved_plan)
        return card

    def _environment_card(self) -> QFrame:
        card, layout = self._card("实验环境设置")
        schedule = normalize_lighting_schedule(
            getattr(global_setting.get_setting("experiment_setting", None), "lighting_schedule", None)
        )
        self.lighting_enabled = QCheckBox("启用光照计划")
        self.lighting_enabled.setChecked(bool(schedule["enabled"]))
        layout.addWidget(self.lighting_enabled)
        self.lighting_mode = self._field(layout, "运行模式", "每日定时", True)
        self.lighting_mode.clear(); self.lighting_mode.addItems(["每日定时", "阶段实验"])
        self.lighting_transition = self._field(layout, "渐变过渡（分钟）", str(schedule["transition_minutes"]))
        stage_grid = QGridLayout(); stage_grid.setHorizontalSpacing(6)
        self._lighting_stages = []
        for index, stage in enumerate(schedule["stages"][:4]):
            time_edit = QTimeEdit(); time_edit.setDisplayFormat("HH:mm"); time_edit.setTime(QTime.fromString(stage["time"], "HH:mm"))
            power = QCheckBox("开灯"); power.setChecked(bool(stage["power"]))
            brightness = QSpinBox(); brightness.setRange(0, 9); brightness.setValue(int(stage["brightness"]))
            color = QComboBox()
            for preset_value, preset_label in LIGHTING_COLOR_PRESETS:
                color.addItem(preset_label, preset_value)
            color_index = color.findData(int(stage["color_temperature"]))
            color.setCurrentIndex(max(0, color_index))
            stage_grid.addWidget(QLabel(f"阶段 {index + 1}"), index, 0); stage_grid.addWidget(time_edit, index, 1); stage_grid.addWidget(power, index, 2)
            stage_grid.addWidget(QLabel("亮度"), index, 3); stage_grid.addWidget(brightness, index, 4); stage_grid.addWidget(QLabel("光色/色温"), index, 5); stage_grid.addWidget(color, index, 6)
            self._lighting_stages.append((time_edit, power, brightness, color))
        layout.addLayout(stage_grid)
        return card

    def _collection_card(self) -> QFrame:
        card, layout = self._card("数据采集设置")
        self.channel_buttons = []
        setting = global_setting.get_setting("experiment_setting", None) or global_setting.get_setting("experiment_setting_new", None)
        selected = {int(getattr(group, "id", 0) or 0) for group in getattr(setting, "groups", []) or [] if getattr(group, "is_selected", False)}
        label = QLabel("启用通道"); label.setObjectName("fieldLabel"); layout.addWidget(label)
        row = QHBoxLayout(); row.setSpacing(5)
        for i in range(1, 9):
            # A new configuration starts with all eight channels enabled.
            # Once a template contains an explicit selection, restore that
            # selection instead of silently changing it while opening P2.
            default_selected = not selected
            button = QPushButton(f"CH{i:02d}"); button.setObjectName("channelButton"); button.setCheckable(True); button.setChecked(i in selected if selected else default_selected)
            self.channel_buttons.append(button); button.clicked.connect(self._on_enabled_channels_changed); row.addWidget(button)
        layout.addLayout(row)
        self.sample_interval = self._field(layout, "气体代谢采样频率（待定）", "每 10 分钟", True)
        self.sample_interval.setEnabled(False)
        self.sample_interval.setToolTip("原系统没有该实验配置项，采样周期待后续定义；当前不会影响实际采集。")
        default_path = os.path.normpath(os.path.join(os.getcwd(), "data", "monitor_data"))
        saved_path = str(getattr(setting, "storage_path", "") or "").strip()
        storage_label = QLabel("本地存储路径（自动保存）")
        storage_label.setObjectName("fieldLabel")
        layout.addWidget(storage_label)
        storage_row = QHBoxLayout()
        storage_row.setSpacing(8)
        self.storage_path = QLineEdit(saved_path or default_path)
        self.storage_browse_button = QPushButton("选择位置")
        self.storage_browse_button.setObjectName("secondaryAction")
        self.storage_browse_button.setFixedWidth(110)
        self.storage_browse_button.clicked.connect(self._select_storage_directory)
        storage_row.addWidget(self.storage_path, 1)
        storage_row.addWidget(self.storage_browse_button)
        layout.addLayout(storage_row)
        self.backup = self._field(layout, "自动备份", "启动（每日）", True)
        self.backup.clear()
        self.backup.addItems(["启动（每日）", "关闭"])
        for widget in (self.experiment_id, self.experiment_title):
            if widget is not None and hasattr(widget, "editingFinished"):
                widget.editingFinished.connect(self.save_experiment_config)
        return card

    def _select_storage_directory(self) -> None:
        """Let the operator choose the root used for new experiment data."""
        current_path = self.storage_path.text().strip()
        initial_path = current_path if os.path.isdir(current_path) else os.path.dirname(current_path)
        if not initial_path or not os.path.isdir(initial_path):
            initial_path = os.path.normpath(os.path.join(os.getcwd(), "data", "monitor_data"))
        selected_path = QFileDialog.getExistingDirectory(
            self,
            "选择本地实验数据存储位置",
            initial_path,
            QFileDialog.Option.ShowDirsOnly,
        )
        if selected_path:
            self.storage_path.setText(os.path.normpath(selected_path))

    def _camera_mapping_panel(self) -> QFrame:
        """Show and edit the existing camera-to-cage mapping files in P2.

        The mapping is deliberately kept separate from per-cage acquisition
        switches: selecting a camera here changes which physical device feeds
        a cage, while the channel panel controls whether that feed is used by
        the experiment.  Both columns reuse the original dialogs and queue
        protocol, so this panel does not create a second configuration format.
        """
        panel = QFrame()
        panel.setObjectName("cameraMappingPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 13, 16, 14)
        layout.setSpacing(8)

        title_row = QHBoxLayout()
        title = QLabel("相机与笼号配对")
        title.setObjectName("sectionTitle")
        title_row.addWidget(title)
        title_row.addStretch(1)
        title_row.addWidget(QLabel("轨迹相机 / 热红外相机"))
        layout.addLayout(title_row)

        note = QLabel("将每台相机绑定到唯一笼号；逐笼自检按配对结果显示，已配对为正常，未配置为不可用。保存后由对应相机服务重新加载。")
        note.setObjectName("statusNote")
        note.setWordWrap(True)
        layout.addWidget(note)

        columns = QHBoxLayout()
        columns.setSpacing(12)
        for kind, heading, action_text in (
            ("deep", "轨迹相机", "配置轨迹相机配对"),
            ("infrared", "热红外相机", "配置热红外配对"),
        ):
            column = QFrame()
            column.setObjectName("cameraMappingColumn")
            column_layout = QVBoxLayout(column)
            column_layout.setContentsMargins(10, 9, 10, 10)
            column_layout.setSpacing(7)
            header = QHBoxLayout()
            heading_label = QLabel(heading)
            heading_label.setObjectName("sectionTitle")
            header.addWidget(heading_label)
            header.addStretch(1)
            action = QPushButton(action_text)
            action.setObjectName("secondaryAction")
            action.setMinimumHeight(30)
            action.clicked.connect(lambda _checked=False, mapping_kind=kind: self._open_camera_mapping(mapping_kind))
            header.addWidget(action)
            column_layout.addLayout(header)

            grid = QGridLayout()
            grid.setHorizontalSpacing(6)
            grid.setVerticalSpacing(6)
            labels = []
            for index in range(1, 9):
                label = QLabel()
                label.setObjectName("mappingPill")
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)
                label.setMinimumHeight(38)
                label.setWordWrap(True)
                label.setToolTip("未配置")
                labels.append(label)
                grid.addWidget(label, (index - 1) // 4, (index - 1) % 4)
            self._camera_mapping_labels[kind] = labels
            column_layout.addLayout(grid)
            columns.addWidget(column, 1)
        layout.addLayout(columns)
        self._refresh_camera_mappings()
        return panel

    def _camera_mapping_file(self, kind: str) -> str:
        camera_config = global_setting.get_setting("camera_config", {}) or {}
        section_name = "DEEP_CAMERA" if kind == "deep" else "INFRARED_CAMERA"
        section = camera_config.get(section_name, {}) if isinstance(camera_config, Mapping) else {}
        file_name = section.get(
            "camera_to_mouse_cage_number_file_name",
            "deep_camera_to_mouse_cage_number_file_name.json"
            if kind == "deep" else "infrared_camera_to_mouse_cage_number_file_name.json",
        )
        path = os.fspath(file_name)
        return path if os.path.isabs(path) else os.path.join(os.getcwd(), path)

    def _read_camera_mapping(self, kind: str) -> dict[int, str]:
        result: dict[int, str] = {}
        try:
            path = self._camera_mapping_file(kind)
            if not os.path.isfile(path):
                return result
            from public.util.json_util import json_util

            records = json_util.read_json_to_dict_list(path)
            for record in records if isinstance(records, list) else []:
                if not isinstance(record, Mapping):
                    continue
                try:
                    cage = int(record.get("mouse_cage_number"))
                except (TypeError, ValueError):
                    continue
                if not 1 <= cage <= 8:
                    continue
                display = (
                    record.get("display_name")
                    or record.get("serial")
                    or record.get("stable_id")
                    or record.get("device_path")
                    or "已配置"
                )
                result[cage] = str(display)
        except Exception:
            # A malformed mapping must remain visible as unconfigured instead
            # of preventing the rest of P2 from opening.
            return {}
        return result

    def _refresh_camera_mappings(self) -> None:
        for kind, labels in getattr(self, "_camera_mapping_labels", {}).items():
            mapping = self._read_camera_mapping(kind)
            for cage, label in enumerate(labels, 1):
                value = mapping.get(cage)
                label.setText(f"CH{cage:02d}\n{value or '未配置'}")
                label.setToolTip(str(value or "未配置"))
                label.setProperty("mappingState", "configured" if value else "empty")
                label.style().unpolish(label)
                label.style().polish(label)
        if getattr(self, "_measurement_status_labels", None):
            self._refresh_module_self_check()

    def _open_camera_mapping(self, kind: str) -> None:
        """Open the legacy scanner/editor and refresh the P2 summary on close."""
        try:
            if kind == "deep":
                from public.component.dialog.index.deep_camera_config_dialog_index import deep_camera_config_dialog

                dialog = deep_camera_config_dialog(title="轨迹相机对应鼠笼配置")
            else:
                from public.component.dialog.index.infrared_camera_config_dialog_index import infrared_camera_config_dialog

                dialog = infrared_camera_config_dialog(title="红外相机对应鼠笼配置")
            dialog.finished.connect(lambda _result: self._refresh_camera_mappings())
            dialog.show_frame()
        except Exception as error:
            QMessageBox.warning(self, "相机配对", f"无法打开相机配对窗口：{error}")
        finally:
            self._refresh_camera_mappings()

    def _initialize_channel_state(self, setting) -> None:
        """Normalize legacy flat values into independent per-cage maps."""
        modules = getattr(setting, "channel_modules", {}) if setting is not None else {}
        data = getattr(setting, "channel_data_config", {}) if setting is not None else {}
        animals = getattr(setting, "channel_animal_info", {}) if setting is not None else {}
        self._channel_module_values = {str(k): dict(v) for k, v in (modules or {}).items() if isinstance(v, dict)}
        self._channel_data_values = {str(k): dict(v) for k, v in (data or {}).items() if isinstance(v, dict)}
        for channel_data in self._channel_data_values.values():
            channel_data.pop("light_control", None)
        self._channel_animal_values = {str(k): dict(v) for k, v in (animals or {}).items() if isinstance(v, dict)}
        # The old shell stored one flat dictionary.  Treat it as CH01 only;
        # all other cages get their own defaults and will not share edits.
        if data and not any(str(k).isdigit() for k in data):
            self._channel_data_values["1"] = dict(data)
            self._channel_data_values["1"].pop("light_control", None)
        if animals and not any(str(k).isdigit() for k in animals):
            self._channel_animal_values["1"] = dict(animals)
        for number in range(1, 9):
            key = str(number)
            self._channel_module_values.setdefault(key, {module: True for module, _ in self.MEASUREMENT_MODULES})
            self._channel_data_values.setdefault(key, {})
            self._channel_animal_values.setdefault(key, {})
            # Restore the legacy manual starting-weight value when a template
            # predates the new channel_config table.
            if "starting_weight_g" not in self._channel_animal_values[key]:
                weight = (getattr(setting, "pre_experiment_weights", {}) or {}).get(key) if setting is not None else None
                if weight is not None:
                    self._channel_animal_values[key]["starting_weight_g"] = str(weight)
            if "individual_id" not in self._channel_animal_values[key] and setting is not None:
                relation = next((item for item in getattr(setting, "animalGroupRecords", []) or [] if int(getattr(item, "gid", 0) or 0) == number), None)
                animal = next((item for item in getattr(setting, "animals", []) or [] if relation is not None and int(getattr(item, "id", 0) or 0) == int(getattr(relation, "aid", 0) or 0)), None)
                if animal is not None and getattr(animal, "name", None):
                    self._channel_animal_values[key]["individual_id"] = str(animal.name)

    @staticmethod
    def _set_editor_value(editor, value) -> None:
        if isinstance(editor, BehaviorCategorySelector):
            editor.set_selected_values(value)
            return
        value = "" if value is None else str(value)
        if isinstance(editor, QComboBox):
            index = editor.findText(value)
            editor.setCurrentIndex(index if index >= 0 else 0)
        else:
            editor.setText(value)

    @staticmethod
    def _read_editor_value(editor) -> str:
        if isinstance(editor, BehaviorCategorySelector):
            return editor.currentText()
        return editor.currentText() if isinstance(editor, QComboBox) else editor.text()

    def _group_options_for_plan(self) -> tuple[str, ...]:
        plan = self.grouping.currentText() if hasattr(self, "grouping") else "对照组 / 高脂组 / 运动组"
        return self.GROUPING_PLANS.get(plan, self.GROUPING_PLANS["对照组 / 高脂组 / 运动组"])

    def _set_group_editor_options(self) -> None:
        editor = getattr(self, "_animal_editors", {}).get("group")
        if not isinstance(editor, QComboBox):
            return
        current = editor.currentText()
        options = self._group_options_for_plan()
        editor.blockSignals(True)
        editor.clear(); editor.addItems(options)
        editor.setCurrentText(current if current in options else options[0])
        editor.blockSignals(False)

    def _on_grouping_changed(self, _value: str = "") -> None:
        if self._loading_channel or not hasattr(self, "_animal_editors"):
            return
        options = self._group_options_for_plan()
        for info in self._channel_animal_values.values():
            if isinstance(info, dict) and info.get("group") not in options:
                info["group"] = options[0]
        self._set_group_editor_options()
        self._capture_current_channel()
        self.save_experiment_config()

    def _capture_current_channel(self) -> None:
        key = str(self._selected_channel)
        self._channel_module_values[key] = {
            module_key: bool(check.isChecked()) for module_key, check in self._measurement_checks
        }
        self._channel_data_values[key] = {
            field: self._read_editor_value(editor) for field, editor in self._data_editors.items()
        }
        self._channel_animal_values[key] = {
            field: self._read_editor_value(editor) for field, editor in self._animal_editors.items()
        }

    def _load_channel_state(self, number: int) -> None:
        key = str(number)
        self._loading_channel = True
        try:
            for module_key, check in self._measurement_checks:
                check.setChecked(bool(self._channel_module_values.get(key, {}).get(module_key, True)))
            data = self._channel_data_values.get(key, {})
            for field, editor in self._data_editors.items():
                default = "30" if field == "fps" else ("可见 + 红外同步" if field == "dual_camera" else ["自由活动"])
                self._set_editor_value(editor, data.get(field, default))
            animal = self._channel_animal_values.get(key, {})
            self._set_group_editor_options()
            for field, editor in self._animal_editors.items():
                default = "M%03d" % number if field == "individual_id" else ("雄" if field == "sex" else ("C57BL/6J" if field == "strain" else (self._group_options_for_plan()[0] if field == "group" else "")))
                self._set_editor_value(editor, animal.get(field, default))
        finally:
            self._loading_channel = False
        self._update_channel_summary()
        self._refresh_module_self_check()

    def _update_channel_summary(self) -> None:
        if not hasattr(self, "_channel_summary"):
            return
        if not any(button.isChecked() for button in self.channel_buttons):
            self._channel_summary.setText("请先在上方启用通道，再设置逐笼参数。")
            return
        enabled = any(button.isChecked() for button in getattr(self, "channel_buttons", [])[self._selected_channel - 1:self._selected_channel])
        self._channel_summary.setText(
            f"CH{self._selected_channel:02d} · {self._animal_editors.get('individual_id').text() if hasattr(self, '_animal_editors') and isinstance(self._animal_editors.get('individual_id'), QLineEdit) else 'M%03d' % self._selected_channel}　"
            f"{'通道已启用' if enabled else '通道未启用'}　 本通道配置独立保存，切换笼号不会串值。"
        )

    def _sync_animals_and_weights(self, setting) -> None:
        """Mirror the per-cage editor into the legacy Animal/weight entities."""
        animals = list(getattr(setting, "animals", []) or [])
        records = list(getattr(setting, "animalGroupRecords", []) or [])
        groups = {int(getattr(group, "id", 0) or 0): group for group in getattr(setting, "groups", []) or []}
        next_animal_id = max([int(getattr(item, "id", 0) or 0) for item in animals] or [0]) + 1
        next_record_id = max([int(getattr(item, "id", 0) or 0) for item in records] or [0]) + 1
        weights = dict(getattr(setting, "pre_experiment_weights", {}) or {})
        for channel_id in range(1, 9):
            info = self._channel_animal_values.get(str(channel_id), {})
            if not info:
                continue
            relation = next((item for item in records if int(getattr(item, "gid", 0) or 0) == channel_id), None)
            animal = next((item for item in animals if relation is not None and int(getattr(item, "id", 0) or 0) == int(getattr(relation, "aid", 0) or 0)), None)
            if animal is None:
                animal = Animal(id=next_animal_id, id_write=channel_id, name=None, sex=False, weight=None, weight_unit="g")
                next_animal_id += 1; animals.append(animal)
            animal.name = str(info.get("individual_id", "") or "")
            animal.sex = str(info.get("sex", "雄")) == "雌"
            animal.weight_unit = "g"
            try:
                weight = float(info.get("starting_weight_g", ""))
            except (TypeError, ValueError):
                weight = None
            animal.weight = weight
            animal.note = f"品系={info.get('strain', '')}; 分组={info.get('group', '')}"
            if relation is None:
                records.append(AnimalGroupRecord(id=next_record_id, aid=animal.id, gid=channel_id, note="P2逐笼配置"))
                next_record_id += 1
            if weight is None:
                weights.pop(str(channel_id), None)
            else:
                weights[str(channel_id)] = round(weight, 3)
        setting.animals = animals; setting.animalGroupRecords = records; setting.pre_experiment_weights = weights

    def _channel_panel(self) -> QFrame:
        """Build the exclusive selector and the three per-cage config cards."""
        panel = QFrame(); panel.setObjectName("channelPanel")
        layout = QVBoxLayout(panel); layout.setContentsMargins(16, 13, 16, 16); layout.setSpacing(10)
        title_row = QHBoxLayout(); title = QLabel("通道参数配置"); title.setObjectName("sectionTitle"); title_row.addWidget(title)
        title_row.addStretch(1); title_row.addWidget(QLabel("8 通道 · 逐笼设置")); layout.addLayout(title_row)
        note = QLabel("通道启用状态统一由上方数据采集设置控制；此处每次只选择一个笼子编辑其配置。")
        note.setObjectName("statusNote"); layout.addWidget(note)
        setting = global_setting.get_setting("experiment_setting", None) or global_setting.get_setting("experiment_setting_new", None)
        enabled_channels = self._enabled_channel_numbers()
        if enabled_channels and self._selected_channel not in enabled_channels:
            self._selected_channel = enabled_channels[0]
        self._initialize_channel_state(setting)
        tabs = QHBoxLayout(); tabs.setSpacing(7); self._channel_tab_group = QButtonGroup(self); self._channel_tab_group.setExclusive(True); self._channel_tab_buttons = []
        for number in range(1, 9):
            button = QPushButton(f"● CH{number:02d}"); button.setObjectName("channelConfigButton"); button.setCheckable(True); button.setChecked(number == self._selected_channel)
            self._channel_tab_group.addButton(button, number); button.clicked.connect(lambda _checked, n=number: self._select_channel(n)); self._channel_tab_buttons.append(button); tabs.addWidget(button)
        layout.addLayout(tabs)
        self._channel_summary = QLabel(); self._channel_summary.setObjectName("statusNote"); layout.addWidget(self._channel_summary)

        columns = QHBoxLayout(); columns.setSpacing(12)
        module_box = QFrame(); module_box.setObjectName("configCard"); module_layout = QVBoxLayout(module_box)
        head = QLabel("测量模块（自检 + 启用）"); head.setObjectName("sectionTitle"); module_layout.addWidget(head)
        module_note = QLabel("勾选后纳入本笼配置；状态来自最近一次真实自检。行为识别控制轨迹相机及图片/视频保存，热红外控制热成像及红外文件保存。")
        module_note.setObjectName("fieldLabel"); module_note.setWordWrap(True); module_layout.addWidget(module_note)
        self._measurement_checks = []
        self._measurement_status_labels = {}
        for module_key, module_label in self.MEASUREMENT_MODULES:
            row = QHBoxLayout(); row.setSpacing(7)
            status = QLabel("自检：未知"); status.setObjectName("moduleStatus"); status.setMinimumWidth(78)
            check = QCheckBox(module_label); check.setStyleSheet("QCheckBox { color:#17395c; padding:3px; }"); check.setProperty("module_key", module_key)
            check.setChecked(bool(self._channel_module_values[str(self._selected_channel)].get(module_key, True)))
            self._measurement_checks.append((module_key, check)); self._measurement_status_labels[module_key] = status
            row.addWidget(check); row.addWidget(status); row.addStretch(1); module_layout.addLayout(row)
        module_layout.addWidget(QLabel("状态：绿色正常　橙色告警　红色故障　灰色未知")); columns.addWidget(module_box, 1)

        data_box = QFrame(); data_box.setObjectName("configCard"); data_layout = QVBoxLayout(data_box); head = QLabel("数据采集配置"); head.setObjectName("sectionTitle"); data_layout.addWidget(head); self._data_editors = {}
        data_fields = (("fps", "行为成像帧率（fps）"), ("dual_camera", "双模成像"), ("behavior_category", "行为类别"))
        for key, label_text in data_fields:
            label = QLabel(label_text); label.setObjectName("fieldLabel"); data_layout.addWidget(label)
            if key == "dual_camera": editor = QComboBox(); editor.addItems(["可见 + 红外同步", "仅可见光", "仅红外"])
            elif key == "behavior_category":
                editor = BehaviorCategorySelector()
                editor.set_selected_values(["自由活动"])
            else: editor = QLineEdit()
            data_layout.addWidget(editor); self._data_editors[key] = editor
        data_layout.addStretch(1); columns.addWidget(data_box, 1)

        animal_box = QFrame(); animal_box.setObjectName("configCard"); animal_layout = QVBoxLayout(animal_box); head = QLabel("小鼠基本信息"); head.setObjectName("sectionTitle"); animal_layout.addWidget(head); self._animal_editors = {}
        animal_fields = (("individual_id", "个体编号"), ("strain", "品系"), ("sex", "性别"), ("group", "分组"), ("starting_weight_g", "起始体重（人工输入，g）"))
        for key, label_text in animal_fields:
            label = QLabel(label_text); label.setObjectName("fieldLabel"); animal_layout.addWidget(label); editor = QComboBox() if key in {"sex", "group"} else QLineEdit()
            if key == "sex": editor.addItems(["雄", "雌"])
            elif key == "group": editor.addItems(self._group_options_for_plan())
            animal_layout.addWidget(editor); self._animal_editors[key] = editor
        animal_layout.addStretch(1)
        for box in (module_box, data_box, animal_box):
            box.setMinimumHeight(360)
            box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        columns.addWidget(animal_box, 1); layout.addLayout(columns)
        self._channel_editor_boxes = (module_box, data_box, animal_box)
        self._sync_enabled_channel_tabs()
        self._load_channel_state(self._selected_channel); self._update_channel_summary()
        return panel

    def _enabled_channel_numbers(self) -> list[int]:
        return [number for number, button in enumerate(self.channel_buttons, 1) if button.isChecked()]

    def _sync_enabled_channel_tabs(self) -> None:
        enabled = set(self._enabled_channel_numbers())
        for number, button in enumerate(self._channel_tab_buttons, 1):
            button.setVisible(number in enabled)
        for box in self._channel_editor_boxes:
            box.setVisible(bool(enabled))
        self._update_channel_summary()

    def _on_enabled_channels_changed(self) -> None:
        enabled = self._enabled_channel_numbers()
        if enabled and self._selected_channel not in enabled:
            self._capture_current_channel()
            self._selected_channel = enabled[0]
            self._channel_tab_buttons[self._selected_channel - 1].setChecked(True)
            self._load_channel_state(self._selected_channel)
        self._sync_enabled_channel_tabs()
        self.save_experiment_config()

    def _select_channel(self, number: int) -> None:
        """Switch the visible editor while keeping every cage's values isolated."""
        number = int(number)
        if number == self._selected_channel:
            return
        self._capture_current_channel()
        self._selected_channel = number
        self._load_channel_state(number)
        self._update_channel_summary()
        self.save_experiment_config()

    def _bind_auto_save(self) -> None:
        for widget in self.findChildren(QLineEdit):
            widget.editingFinished.connect(self.save_experiment_config)
        for widget in self.findChildren(QComboBox):
            if widget is self.serial_port_combo:
                continue
            if widget is self.grouping:
                widget.currentTextChanged.connect(self._on_grouping_changed)
            else:
                widget.currentTextChanged.connect(lambda _value: self.save_experiment_config())
        for widget in self.findChildren(QCheckBox):
            widget.stateChanged.connect(lambda _state: self.save_experiment_config())
        for widget in self.findChildren(BehaviorCategorySelector):
            widget.selectionChanged.connect(self.save_experiment_config)

    def set_health_source(self, health_source: Any) -> None:
        self._health_source = health_source
        self._refresh_module_self_check()

    def _read_health_snapshot(self) -> Mapping[str, Any]:
        source = self._health_source
        if source is None:
            return {}
        try:
            if callable(source):
                value = source()
            else:
                reader = getattr(source, "snapshot", None) or getattr(source, "get_snapshot", None)
                value = reader() if callable(reader) else source
        except Exception:
            return {}
        return value if isinstance(value, Mapping) else {}

    def _refresh_module_self_check(self) -> None:
        """Render the confirmed serial check, falling back to runtime health.

        A checked box is a user selection, not proof that hardware exists.
        Without a matching health snapshot the state deliberately stays
        ``未知``; this is important for single-machine/no-hardware testing.
        """
        health = self._read_health_snapshot()
        observations = list(health.get("components") or []) + list(health.get("alerts") or [])
        legacy = self._legacy_device_page()
        detection = global_setting.get_setting("mouse_cage_detect_state_dict", {}) or {}
        confirmed = bool(self._confirmed_serial_port())
        severity = {"unknown": 0, "unavailable": 1, "normal": 2, "warning": 3, "fault": 4}
        for module, label in self._measurement_status_labels.items():
            camera_result = self._camera_mapping_self_check(module, self._selected_channel)
            if camera_result is not None:
                state_text, state = camera_result
            else:
                detected = self._detected_module_result(module, self._selected_channel, legacy, detection)
                if detected is not None:
                    state_text, state = detected
                else:
                    matched = self._matching_health_items(module, self._selected_channel, observations)
                    state = "unknown"
                    if matched:
                        state = max(
                            (str(item.get("status", item.get("state", "unknown"))).lower() for item in matched),
                            key=lambda value: severity.get(value, 0),
                        )
                    state_text = {
                        "normal": "自检正常",
                        "warning": "告警",
                        "fault": "故障",
                        "unavailable": "不可用",
                    }.get(state, "未知")
            label.setText(f"自检：{state_text}")
            label.setProperty("moduleTone", state)
            label.style().unpolish(label)
            label.style().polish(label)

        # The CH01–CH08 selector is also a compact self-check summary.  It is
        # deliberately stricter than the per-module labels: green means every
        # enabled module has a real normal snapshot; red means at least one
        # enabled module is warning/fault/unavailable.  Missing snapshots stay
        # neutral so a checked box can never be mistaken for connected hardware.
        for channel, button in enumerate(self._channel_tab_buttons, 1):
            if confirmed:
                device_tone = self._channel_detection_tone(channel, detection)
                camera_tone = self._channel_camera_mapping_tone(channel)
                tone = self._combine_channel_tones(device_tone, camera_tone)
            else:
                tone = self._channel_health_tone(channel, observations)
            button.setProperty("channelTone", tone or "")
            if tone == "normal":
                button.setToolTip("自检正常：已启用模块均返回真实正常快照")
            elif tone == "fault":
                button.setToolTip("自检异常：存在告警、故障或不可用模块")
            else:
                button.setToolTip("自检状态未知：尚无完整真实快照")
            button.style().unpolish(button)
            button.style().polish(button)

    def _camera_mapping_self_check(self, module: str, channel: int):
        kind = {"behavior": "deep", "infrared": "infrared"}.get(module)
        if kind is None:
            return None
        configured = int(channel) in self._read_camera_mapping(kind)
        return ("正常", "normal") if configured else ("未配置", "unavailable")

    def _channel_camera_mapping_tone(self, channel: int) -> str:
        values = self._channel_module_values.get(str(channel), {})
        enabled = [
            module for module in ("behavior", "infrared")
            if bool(values.get(module, True))
        ]
        if not enabled:
            return "normal"
        return "normal" if all(
            self._camera_mapping_self_check(module, channel)[1] == "normal"
            for module in enabled
        ) else "fault"

    @staticmethod
    def _combine_channel_tones(device_tone: Optional[str], camera_tone: Optional[str]) -> Optional[str]:
        if "fault" in {device_tone, camera_tone}:
            return "fault"
        if device_tone == "normal" and camera_tone == "normal":
            return "normal"
        return None

    @staticmethod
    def _matching_health_items(module: str, channel: int, observations) -> list[Mapping[str, Any]]:
        """Return health snapshots that prove ``module`` for one cage.

        Global gas-path snapshots are intentionally allowed to match every
        cage, while monitor/camera snapshots must carry the requested CH scope.
        This keeps the tab colour tied to the same independent health channel
        used by the detailed labels.
        """
        component_aliases = {
            "weight": {"weight", "weighing", "monitor_collection"},
            "water": {"water", "drinking", "monitor_collection"},
            "food": {"food", "feeding", "monitor_collection"},
            "environment": {"environment", "temperature_control", "monitor_collection"},
            "behavior": {"behavior", "behavior_recognition", "trajectory", "deep_camera"},
            "infrared": {"infrared", "infrared_camera"},
        }
        collection_modules = {
            "weight": "WM",
            "water": "EM",
            "food": "DWM",
            "environment": "ENM",
        }
        channel_scopes = {f"ch{int(channel):02d}".lower(), f"ch{int(channel)}".lower()}
        matched = []
        for item in observations:
            if not isinstance(item, Mapping):
                continue
            component = str(item.get("component", "")).lower()
            scope = str(item.get("scope", "")).lower()
            aliases = component_aliases.get(module, {module.lower()})
            if component == "monitor_collection" and scope not in {"", *channel_scopes}:
                continue
            if component == "monitor_collection":
                # A monitor_collection heartbeat is emitted for one physical
                # module at a time.  Only its explicit WM/EM/DWM/ENM metric
                # can prove the corresponding P2 module; a generic heartbeat
                # must not make every module (and every cage) look healthy.
                metrics = item.get("metrics")
                observed_module = str(metrics.get("module", "")).upper() if isinstance(metrics, Mapping) else ""
                if observed_module != collection_modules.get(module, ""):
                    continue
            if module == "behavior" and component in {"deep_camera_lock", "deep_camera_process"}:
                # A process heartbeat/lock is not evidence of a camera frame.
                continue
            if module in {"behavior", "infrared"}:
                camera_components = {
                    "behavior": {"deep_camera", "trajectory", "behavior_recognition"},
                    "infrared": {"infrared_camera"},
                }[module]
                if component not in camera_components:
                    continue
            if component in aliases or any(alias in component for alias in aliases) or module.lower() in scope:
                matched.append(item)
        return matched

    def _channel_health_tone(self, channel: int, observations) -> Optional[str]:
        """Aggregate enabled-module health for a CH selector.

        ``None`` means unknown/incomplete rather than normal.  This is the
        important distinction for a single-machine test with no lower machine.
        """
        values = self._channel_module_values.get(str(channel), {})
        enabled_modules = [
            module
            for module, _label in self.MEASUREMENT_MODULES
            if bool(values.get(module, True))
        ]
        if not enabled_modules:
            return None
        states = []
        has_unknown = False
        for module in enabled_modules:
            camera_result = self._camera_mapping_self_check(module, channel)
            if camera_result is not None:
                states.append(camera_result[1])
                continue
            matched = self._matching_health_items(module, channel, observations)
            if not matched:
                has_unknown = True
                continue
            states.extend(
                str(item.get("status", item.get("state", "unknown"))).lower()
                for item in matched
            )
        if not states:
            return None
        if any(state in {"warning", "fault", "unavailable"} for state in states):
            return "fault"
        if has_unknown:
            return None
        if all(state == "normal" for state in states):
            return "normal"
        return None

    def _find_main_window(self):
        widget = self.parentWidget()
        while widget is not None:
            if callable(getattr(widget, "navigate_workflow_step", None)):
                return widget
            widget = widget.parentWidget()
        return None

    def import_configuration(self) -> None:
        """Load an existing ``.template`` experiment into the active settings."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "导入实验配置",
            "",
            f"实验模板 (*.{custom_template_file_util.extension_name});;所有文件 (*.*)",
        )
        if not file_path:
            return
        db_file_path = None
        try:
            db_file_path = custom_template_file_util.load_template_contents_from_custom_file(file_path)
            folder_path = os.path.dirname(db_file_path)
            file_name = os.path.basename(db_file_path)
            handle = Experiment_Setting_DAO_Handle(db_fold_path=folder_path, db_name=file_name)
            try:
                setting = handle.query_data_database_all()
            finally:
                handle.stop()
            if setting is None or getattr(setting, "is_emtpy", lambda: False)():
                raise ValueError("模板中没有可用的实验配置")
            global_setting.set_setting("experiment_setting_new", setting)
            global_setting.set_setting("experiment_setting", setting)
            global_setting.set_setting("experiment_setting_file_open", file_path)
            global_setting.set_setting("experiment_setting_file", file_path)
            main_window = self._find_main_window()
            if main_window is not None and callable(getattr(main_window, "show_experiment_config_page", None)):
                main_window.show_experiment_config_page()
            else:
                QMessageBox.information(self, "导入配置", "配置已导入，请重新打开实验配置页面查看。")
        except Exception as error:
            QMessageBox.warning(self, "导入配置失败", str(error))
        finally:
            if db_file_path and os.path.isfile(db_file_path):
                try:
                    os.remove(db_file_path)
                except OSError:
                    pass

    def apply_and_open_control(self) -> None:
        """Persist the current configuration and route to workflow step P3."""
        if not any(button.isChecked() for button in getattr(self, "channel_buttons", ())):
            QMessageBox.warning(self, "应用配置", "请至少启用一个通道后再进入实验控制。")
            return
        self.save_experiment_config()
        main_window = self._find_main_window()
        if main_window is not None and callable(getattr(main_window, "navigate_workflow_step", None)):
            # P3 is the pre-run control/monitoring entry.  It must be
            # reachable after applying a valid configuration, before the
            # monitoring process has transitioned AppState to MONITORING.
            main_window.navigate_workflow_step(3, allow_pre_run=True)

    def return_to_system_status(self) -> None:
        main_window = self._find_main_window()
        if main_window is not None and callable(getattr(main_window, "navigate_workflow_step", None)):
            main_window.navigate_workflow_step(1)

    def create_configuration(self) -> bool:
        """Create a new ``.template`` file at a user-selected location.

        ``保存配置`` remains an in-place save for the currently opened
        configuration.  This action is deliberately separate so that an
        accidental save cannot replace the active template with a new file.
        """
        if self._loading_channel:
            return False
        setting = global_setting.get_setting("experiment_setting", None)
        if setting is None:
            setting = global_setting.get_setting("experiment_setting_new", None)
        if setting is None:
            QMessageBox.warning(self, "创建配置", "当前没有可保存的实验配置。")
            return False

        # Capture the latest editor values before showing the file dialog.
        self.save_experiment_config()
        setting = global_setting.get_setting("experiment_setting", setting)
        current_path = (
            global_setting.get_setting("experiment_setting_file", "")
            or global_setting.get_setting("experiment_setting_file_open", "")
        )
        default_dir = os.path.dirname(current_path) if current_path else ""
        if not default_dir:
            default_dir = os.path.join(os.getcwd(), "data", "monitor_data")
        experiment_id = str(getattr(setting, "experiment_id", "") or "").strip()
        default_name = f"{experiment_id or 'experiment_config'}.template"
        default_path = os.path.join(default_dir, default_name)
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "创建实验配置",
            default_path,
            f"实验配置 (*.{custom_template_file_util.extension_name});;所有文件 (*)",
        )
        if not file_path:
            return False
        file_path = custom_template_file_util.get_template_extension_file(file_path)
        try:
            if not self._write_template_file(setting, file_path):
                raise IOError("配置文件写入失败")
            global_setting.set_setting("experiment_setting_file", file_path)
            global_setting.set_setting("experiment_setting_file_open", file_path)
            # The active setting path changed only after the file dialog;
            # publish the new path as well as the already-captured values.
            self._broadcast_setting(setting)
            QMessageBox.information(self, "创建配置", f"配置已创建：\n{file_path}")
            return True
        except Exception as error:
            QMessageBox.warning(self, "创建配置失败", str(error))
            return False

    def _save_experiment_config_with_feedback(self, _checked: bool = False) -> None:
        try:
            if not self.save_experiment_config():
                QMessageBox.warning(self, "保存配置", "当前没有可保存的实验配置。")
                return
        except Exception as error:
            logger.exception("新版实验配置页保存配置失败")
            QMessageBox.warning(self, "保存配置失败", str(error))
            return
        QMessageBox.information(self, "保存配置", "配置保存成功。")

    @staticmethod
    def _startup_calibration_mode_for_save() -> str:
        """Return the selected startup mode without reviving stale template values."""
        if not global_setting.get_setting("device_config_calibration_selected", False):
            return "none"
        mode = global_setting.get_setting("startup_calibration_mode", "none")
        if mode == "air_co2":
            mode = "air"
        return mode if mode in {"none", "air", "zero", "span", "full"} else "none"

    @staticmethod
    def _startup_calibration_label(mode: str) -> str:
        return {
            "air": "Air 空气校准",
            "zero": "校零",
            "span": "校量程",
            "full": "校零+校span",
        }.get(mode, "不校准")

    @staticmethod
    def _default_template_path() -> str:
        """Return the user-local fallback used when no template was opened."""
        return os.path.join(
            os.path.expanduser("~"),
            ".mouse_experiment_config",
            "last_experiment_config.template",
        )

    @staticmethod
    def _configured_template_path() -> str:
        for key in ("experiment_setting_file", "experiment_setting_file_open"):
            value = str(global_setting.get_setting(key, "") or "").strip()
            if value:
                return value
        return ""

    @staticmethod
    def _has_experiment_content(setting) -> bool:
        if setting is None:
            return False
        return any(
            bool(getattr(setting, field, None))
            for field in (
                "groups",
                "animals",
                "animalGroupRecords",
                "channel_modules",
                "channel_data_config",
                "channel_animal_info",
                "experiment_id",
                "experiment_title",
            )
        )

    def _restore_last_saved_configuration(self) -> None:
        """Restore the last saved P2 configuration when no template is open.

        The old configuration page kept user edits locally.  The redesigned
        page previously only wrote a file when an explicit template path had
        already been opened, so a normal ``保存配置`` on a new experiment was
        lost after restarting the application.
        """
        current = (
            global_setting.get_setting("experiment_setting", None)
            or global_setting.get_setting("experiment_setting_new", None)
        )
        configured_path = self._configured_template_path()
        if self._has_experiment_content(current) or configured_path:
            return
        template_path = self._default_template_path()
        if not os.path.isfile(template_path):
            return

        db_path = ""
        handle = None
        try:
            db_path = custom_template_file_util.load_template_contents_from_custom_file(template_path)
            handle = Experiment_Setting_DAO_Handle(
                db_fold_path=os.path.dirname(db_path),
                db_name=os.path.basename(db_path),
            )
            setting = handle.query_data_database_all()
            if setting is None or not self._has_experiment_content(setting):
                return
            global_setting.set_setting("experiment_setting", setting)
            global_setting.set_setting("experiment_setting_new", setting)
            global_setting.set_setting("experiment_setting_file", template_path)
            global_setting.set_setting("experiment_setting_file_open", template_path)
            logger.info("已恢复上次保存的实验配置: {}", template_path)
        except Exception as error:
            logger.warning("恢复上次实验配置失败: {}", error)
        finally:
            if handle is not None:
                handle.stop()
            if db_path and os.path.isfile(db_path):
                os.remove(db_path)

    def save_experiment_config(self) -> bool:
        """Write the edited P2 values into the live experiment setting object."""
        if self._loading_channel:
            return False
        setting = global_setting.get_setting("experiment_setting", None)
        if setting is None:
            setting = global_setting.get_setting("experiment_setting_new", None)
        if setting is None:
            return False
        groups = list(getattr(setting, "groups", []) or [])
        by_id = {int(getattr(group, "id", 0) or 0): group for group in groups}
        for index, button in enumerate(getattr(self, "channel_buttons", []), 1):
            group = by_id.get(index)
            if group is None:
                group = Group(id=index, name=str(index), is_selected=False)
                groups.append(group)
            group.is_selected = bool(button.isChecked())
        setting.groups = groups
        # Preserve the existing entity while attaching P2-only values for
        # subsequent save/template code to consume.
        setting.experiment_id = self.experiment_id.text().strip()
        setting.experiment_title = self.experiment_title.text().strip()
        setting.animal_species = self.species.currentText()
        setting.grouping_plan = self.grouping.currentText()
        setting.lighting_plan = "光照时间表"
        setting.lighting_schedule = self._collect_lighting_schedule()
        # Keep the legacy behavior: saving a configuration must not erase an
        # explicitly selected startup calibration mode.
        startup_calibration_mode = self._startup_calibration_mode_for_save()
        setting.startup_calibration = self._startup_calibration_label(startup_calibration_mode)
        setting.storage_path = self.storage_path.text().strip()
        setting.auto_backup = self.backup.currentText()
        self._capture_current_channel()
        setting.channel_modules = copy.deepcopy(self._channel_module_values)
        setting.channel_data_config = copy.deepcopy(self._channel_data_values)
        setting.channel_animal_info = copy.deepcopy(self._channel_animal_values)
        self._sync_animals_and_weights(setting)
        global_setting.set_setting("experiment_setting", setting)
        global_setting.set_setting("experiment_setting_new", setting)
        self._persist_template(setting)
        self._broadcast_setting(setting)
        send_queue = global_setting.get_setting("send_message_queue", None)
        if send_queue is not None:
            schedule = copy.deepcopy(setting.lighting_schedule)
            send_queue.put(ObjectQueueItem(
                origin="ExperimentConfigPage",
                to="main_monitor_data",
                title="lighting_schedule",
                data=schedule,
                time=time_util.get_format_from_time(time.time()),
            ))
        return True

    @staticmethod
    def _persist_template(setting) -> None:
        """Persist the current setting to the opened template or local fallback."""
        template_path = ExperimentConfigPage._configured_template_path()
        if not template_path:
            template_path = ExperimentConfigPage._default_template_path()

        # ``保存配置`` must also work for a newly created experiment.  In that
        # case there is no file-open path yet, so create the same user-local
        # template that the next page/app opening can restore.
        if not os.path.isfile(template_path):
            if ExperimentConfigPage._write_template_file(setting, template_path):
                global_setting.set_setting("experiment_setting_file", template_path)
                global_setting.set_setting("experiment_setting_file_open", template_path)
            return

        db_path = custom_template_file_util.load_template_contents_from_custom_file(template_path)
        try:
            handle = Experiment_Setting_DAO_Handle(
                db_fold_path=os.path.dirname(db_path),
                db_name=os.path.basename(db_path),
            )
            try:
                handle.remove_data_database_all_not_include_metaDB()
                handle.insert_data(setting)
            finally:
                handle.stop()
            custom_template_file_util.save_template_contents_as_custom_file(db_path)
            global_setting.set_setting("experiment_setting_file", template_path)
            global_setting.set_setting("experiment_setting_file_open", template_path)
        finally:
            if os.path.isfile(db_path):
                os.remove(db_path)

    @staticmethod
    def _write_template_file(setting, template_path: str) -> bool:
        """Serialize a setting entity to a newly selected template path."""
        db_path = custom_template_file_util.get_db_extension_file(template_path)
        folder_path = os.path.dirname(db_path)
        os.makedirs(folder_path, exist_ok=True)
        handle = None
        try:
            # The temporary DB is an implementation detail of the template
            # format.  Remove only the exact selected target's temporary DB.
            if os.path.isfile(db_path):
                os.remove(db_path)
            handle = Experiment_Setting_DAO_Handle(
                db_fold_path=folder_path,
                db_name=os.path.basename(db_path),
            )
            written = bool(handle.insert_data(data=setting))
            if not written:
                return False
            handle.stop()
            handle = None
            custom_template_file_util.save_template_contents_as_custom_file(db_path)
            return os.path.isfile(template_path)
        finally:
            if handle is not None:
                handle.stop()
            if os.path.isfile(db_path):
                os.remove(db_path)

    @staticmethod
    def _broadcast_setting(setting) -> None:
        calibration_mode = ExperimentConfigPage._startup_calibration_mode_for_save()
        calibration_enabled = calibration_mode != "none"
        global_setting.set_setting("startup_calibration_mode", calibration_mode)
        global_setting.set_setting("is_auto_calibration", calibration_enabled)
        payload = {
            "experiment_setting": setting,
            "experiment_setting_file": global_setting.get_setting("experiment_setting_file", ""),
        }
        monitor_queue = global_setting.get_setting("send_message_queue", None)
        if monitor_queue is not None:
            monitor_queue.put(ObjectQueueItem(
                origin="ExperimentConfigPage", to="main_monitor_data", title="set_experiment_basic_config",
                data={"startup_calibration_mode": calibration_mode, "is_auto_calibration": calibration_enabled},
                time=time_util.get_format_from_time(time.time()),
            ))
            monitor_queue.put(ObjectQueueItem(
                origin="ExperimentConfigPage", to="main_monitor_data", title="experiment_setting",
                data=payload, time=time_util.get_format_from_time(time.time()),
            ))
        camera_queue = global_setting.get_setting("queue", None)
        if camera_queue is None:
            return
        for target in ("main_deep_camera", "main_infrared_camera"):
            camera_queue.put(ObjectQueueItem(
                origin="ExperimentConfigPage", to=target, title="experiment_setting",
                data=payload, time=time_util.get_format_from_time(time.time()),
            ))

    def _collect_lighting_schedule(self) -> dict:
        stages = []
        for time_edit, power, brightness, color in self._lighting_stages:
            stages.append({"time": time_edit.time().toString("HH:mm"), "power": power.isChecked(), "brightness": brightness.value(), "color_temperature": int(color.currentData() or 7), "duration_minutes": 720})
        return normalize_lighting_schedule({
            "enabled": self.lighting_enabled.isChecked(),
            "mode": "stage" if self.lighting_mode.currentIndex() == 1 else "daily",
            "transition_minutes": int(self.lighting_transition.text() or 0),
            "repeat": True,
            "stages": stages or default_lighting_schedule()["stages"],
        })
