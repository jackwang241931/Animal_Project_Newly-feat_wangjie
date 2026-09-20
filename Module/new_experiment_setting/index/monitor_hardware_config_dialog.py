from PyQt6 import QtCore, QtGui
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QLabel, QListWidgetItem
from loguru import logger

from Module.new_experiment_setting.index.Tab_1 import Tab_1
from public.config_class.global_setting import global_setting
from public.function.Modbus import Modbus_Type


class MonitorHardwareConfigDialog(Tab_1):
    """实验检测页使用的硬件配置弹窗。"""

    def __init__(self, parent=None, title="硬件配置"):
        self._popup_title = title
        super().__init__(parent=parent, title=title)

    def _init_customize_ui(self):
        super()._init_customize_ui()
        self._apply_popup_mode()

    def init_port_combox(self):
        """弹窗直接复用已确认串口，不重新初始化串口下拉框。"""
        self._sync_port_from_global()

    def showEvent(self, event):
        super().showEvent(event)
        self._apply_popup_mode()
        self._sync_port_from_global()
        self.init_cage_list()

    def _apply_popup_mode(self):
        self.setWindowTitle(self._popup_title)
        self.resize(1280, 760)
        self.setMinimumSize(1120, 680)
        self.setWindowModality(Qt.WindowModality.ApplicationModal)

        for attr_name in (
            "label_3",
            "tab_1_port_combox",
            "tab_1_refresh_port_btn",
            "tab_1_confirm_port_btn",
            "tab_1_refresh_detection_btn",
            "start_btn",
        ):
            widget = getattr(self.ui, attr_name, None)
            if widget is not None:
                widget.hide()

        if hasattr(self.ui, "top_layout"):
            self.ui.top_layout.setContentsMargins(0, 0, 0, 0)
            self.ui.top_layout.setSpacing(0)

        if hasattr(self.ui, "module_detection_group") and self.ui.module_detection_group is not None:
            self.ui.module_detection_group.hide()

        if self.menuBar() is not None:
            self.menuBar().hide()
        if self.statusBar() is not None:
            self.statusBar().hide()

    def _sync_port_from_global(self):
        port = global_setting.get_setting("port", "")
        if not port:
            return

        self.send_message["port"] = port
        self.port_confirmed = True

    def _notify_main_monitor_data_set_port(self, show_error=False):
        """硬件配置弹窗不负责切换主监测串口。"""
        return True

    def init_cage_list(self):
        """弹窗里只显示当前可配置的鼠笼。"""
        if self.experiment_setting is None:
            self.experiment_setting = global_setting.get_setting("experiment_setting", None)

        if self.cage_list_widget is None:
            logger.error("cage_list_widget 为 None")
            return

        self.cage_list_widget.clear()
        self.cage_enabled_status.clear()

        try:
            self.cage_list_widget.itemClicked.disconnect()
        except TypeError:
            pass
        self.cage_list_widget.itemClicked.connect(self._on_cage_clicked)

        detect_state = global_setting.get_setting("mouse_cage_detect_state_dict", {}) or {}
        valid_cage_ids = {
            int(cage_id)
            for cage_id, cage_data in detect_state.items()
            if isinstance(cage_data, dict) and cage_data.get("cage_is_valid", False)
        }

        if self.experiment_setting is not None and getattr(self.experiment_setting, "groups", None):
            enabled_groups = [g for g in self.experiment_setting.groups if g.is_selected == 1]
            for group in enabled_groups:
                group_id = int(group.id)
                if group_id not in valid_cage_ids:
                    continue

                item_text = f"鼠笼 {group_id} [{group.name}] - 可配置"
                item = QListWidgetItem(item_text)
                item.setData(Qt.ItemDataRole.UserRole, group_id)
                item.setFlags(Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                item.setBackground(QtGui.QColor(240, 255, 240))
                item.setForeground(QtGui.QColor(34, 139, 34))
                self.cage_list_widget.addItem(item)
                self.cage_enabled_status[group_id] = group

        if self.cage_list_widget.count() == 0:
            placeholder = QListWidgetItem("暂无可配置鼠笼，请先在“设置设备”中完成检测")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            self.cage_list_widget.addItem(placeholder)
            self.init_config_ui()
            return

        self.cage_list_widget.setCurrentRow(0)
        first_item = self.cage_list_widget.item(0)
        if first_item is not None:
            self._on_cage_clicked(first_item)

    def init_config_ui(self):
        """弹窗默认提示页，不显示基础配置。"""
        if self.content_layout is None:
            logger.error("content_layout 未找到")
            return

        self.remove_layout_items(self.content_layout)

        if not self.send_message.get("port"):
            tip_text = "当前未同步到有效串口，请先在“设置设备”界面确认串口后再配置。"
        else:
            tip_text = "请选择左侧可配置鼠笼，然后在右侧进行硬件配置。"

        tip_label = QLabel(tip_text)
        tip_label.setWordWrap(True)
        tip_label.setStyleSheet("color: #666; font-style: italic; padding: 6px 0;")
        self.content_layout.addWidget(tip_label)

        if self.config:
            for module_key, module_value in self.config.items():
                if module_key == Modbus_Type.Modbus_Slave_Ids.ENM.value["name"]:
                    self.init_enm_config_ui_default(module_key, module_value, self.content_layout)
                elif module_key == Modbus_Type.Modbus_Slave_Ids.EM.value["name"]:
                    self.init_em_config_ui_default(module_key, module_value, self.content_layout)

        self.content_layout.addStretch()

    def load_cage_config(self, group_num):
        """弹窗加载指定鼠笼的配置，不展示基础配置。"""
        try:
            if self.content_layout is None:
                logger.error("content_layout 未找到")
                return

            self.remove_layout_items(self.content_layout)

            saved_config = self._load_cage_config_from_json(group_num)
            self.current_cage_config = saved_config.copy()

            for module_key, module_value in self.config.items():
                if module_key == Modbus_Type.Modbus_Slave_Ids.ENM.value["name"]:
                    module_config = saved_config.get("ENM", {})
                    self.init_enm_config_ui_for_group(
                        module_key, module_value, self.content_layout, group_num, module_config
                    )
                elif module_key == Modbus_Type.Modbus_Slave_Ids.EM.value["name"]:
                    module_config = saved_config.get("EM", {})
                    self.init_em_config_ui_for_group(
                        module_key, module_value, self.content_layout, group_num, module_config
                    )

            self.content_layout.addStretch()
        except Exception as e:
            logger.error(f"加载鼠笼配置出错: {e}", exc_info=True)
