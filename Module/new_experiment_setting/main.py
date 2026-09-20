# Module/new_experiment_setting/index/main.py
import time

from PyQt6.QtWidgets import QMainWindow, QMessageBox

from Module.new_experiment_setting.index.Tab_1 import Tab_1
from Module.new_experiment_setting.index.monitor_hardware_config_dialog import MonitorHardwareConfigDialog
from Module.new_experiment_setting.index.lighting_schedule_dialog import LightingScheduleDialog
from my_abc.BaseInterfaceWidget import BaseInterfaceWidget
from my_abc.BaseModule import BaseModule
from my_abc.BaseService import BaseService
from public.component.dialog.index.deep_camera_config_dialog_index import deep_camera_config_dialog
from public.component.dialog.index.infrared_camera_config_dialog_index import infrared_camera_config_dialog
from public.config_class.global_setting import global_setting
from public.entity.BaseWidget import BaseWidget
from public.entity.BaseWindow import BaseWindow
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.entity.enum.Public_Enum import BaseInterfaceType, AppState
from public.util.time_util import time_util


class Main_experiment_setting_service(BaseService):
    def __init__(self):
        pass

    def start(self, resolve, reject):
        resolve()

    def stop(self):
        pass


class Main_experiment_setting_widget(BaseInterfaceWidget):
    def __init__(self):
        super().__init__()
        self.type = self.get_type()
        self.frame_obj = self.create_middle_window()
        self.left_frame_obj = None
        self.right_frame_obj = None
        self.bottom_frame_obj = None

    def get_type(self):
        return BaseInterfaceType.WIDGET

    def create_middle_window(self, parent=None) -> BaseWindow:
        # 清除旧的单例缓存，确保每次都能创建新实例
        from Module.new_experiment_setting.index.Tab_1 import SafeSingletonMeta
        with SafeSingletonMeta._lock:
            if Tab_1 in SafeSingletonMeta._instances:
                del SafeSingletonMeta._instances[Tab_1]
            if Tab_1 in SafeSingletonMeta._init_completed:
                del SafeSingletonMeta._init_completed[Tab_1]

        with Tab_1._instance_lock:
            Tab_1._initialization_state.clear()
            Tab_1._failed_instances.clear()

        return Tab_1(parent=parent)

    def create_left_window(self) -> BaseWindow:
        return None

    def create_right_window(self) -> BaseWindow:
        return None

    def create_bottom_window(self) -> BaseWindow:
        return None


class Main_experiment_setting(BaseModule):
    def __init__(self):
        super().__init__()
        self.interface_widget = self.get_interface_widget()
        self.name = self.get_name()
        self.title = self.get_title()
        self.toolbar_order = 10
        self.menu_name = self.get_menu_name()
        self.service = self.create_service()
        self.app_state = self.get_app_state()

    def get_app_state(self) -> AppState:
        return AppState.APPLYING

    def get_name(self):
        return "New_main_experiment_setting"

    def get_title(self):
        return "设置设备"

    def get_menu_name(self):
        return {"id": 1, "text": "设备信息"}

    def create_service(self) -> BaseService:
        return Main_experiment_setting_service()

    def get_interface_widget(self) -> BaseInterfaceWidget:
        widget_builder = Main_experiment_setting_widget()
        widget_builder.module = self
        return widget_builder


class Calibration_selection_service(BaseService):
    def __init__(self):
        pass

    def start(self, resolve, reject):
        resolve()

    def stop(self):
        pass


class Calibration_selection_widget(BaseInterfaceWidget):
    def __init__(self):
        super().__init__()
        self.type = self.get_type()

    def get_type(self):
        return BaseInterfaceType.WIDGET

    def create_middle_window(self) -> BaseWindow:
        return None

    def create_left_window(self) -> BaseWindow:
        return None

    def create_right_window(self) -> BaseWindow:
        return None

    def create_bottom_window(self) -> BaseWindow:
        return None


class Camera_config_action_widget(BaseInterfaceWidget):
    def __init__(self):
        super().__init__()
        self.type = self.get_type()

    def get_type(self):
        return BaseInterfaceType.WIDGET

    def create_middle_window(self) -> BaseWindow:
        return None

    def create_left_window(self) -> BaseWindow:
        return None

    def create_right_window(self) -> BaseWindow:
        return None

    def create_bottom_window(self) -> BaseWindow:
        return None


class Main_experiment_calibration(BaseModule):
    def __init__(self):
        super().__init__()
        self.interface_widget = self.get_interface_widget()
        self.name = self.get_name()
        self.title = self.get_title()
        self.toolbar_order = 11
        self.menu_name = self.get_menu_name()
        self.service = self.create_service()
        self.app_state = self.get_app_state()

    def get_app_state(self) -> AppState:
        return AppState.APPLYING

    def get_name(self):
        return "New_main_experiment_calibration"

    def get_title(self):
        return "校准"

    def get_menu_name(self):
        return {"id": 1, "text": "设备信息"}

    def create_service(self) -> BaseService:
        return Calibration_selection_service()

    def get_interface_widget(self) -> BaseInterfaceWidget:
        widget_builder = Calibration_selection_widget()
        widget_builder.module = self
        return widget_builder

    def refresh_display_text(self):
        self.title = "校准"

        if self.main_gui is None:
            return

        action_name = f"dynamic_{self.name}"
        for action_dict in getattr(self.main_gui, "dynamic_tool_bar_actions", []):
            if action_dict.get("obj_name") == action_name:
                action_dict["action"].setText(self.title)
                break

        self.sync_action_enabled_state()
        return
        self.title = "校准"

        if self.main_gui is None:
            return

        action_name = f"dynamic_{self.name}"
        for action_dict in getattr(self.main_gui, "dynamic_tool_bar_actions", []):
            if action_dict.get("obj_name") == action_name:
                action_dict["action"].setText(self.title)
                break

        self.sync_action_enabled_state()

    @staticmethod
    def normalize_startup_calibration_mode(mode):
        if mode == "air_co2":
            return "air"
        if mode in {"none", "air", "full"}:
            return mode
        return "none"

    def get_startup_calibration_mode(self):
        mode = global_setting.get_setting("startup_calibration_mode", None)
        if mode == "air_co2":
            mode = "air"
        if mode not in {"none", "air", "full"}:
            mode = "none"
        return mode

    def sync_startup_calibration_mode(self, mode, selection_made=True):
        mode = self.normalize_startup_calibration_mode(mode)
        global_setting.set_setting("startup_calibration_mode", mode)
        global_setting.set_setting("is_auto_calibration", mode != "none")
        global_setting.set_setting("device_config_calibration_selected", selection_made)

        send_message_queue = global_setting.get_setting("send_message_queue")
        if send_message_queue is not None:
            send_message_queue.put(
                ObjectQueueItem(
                    origin='Main_experiment_calibration',
                    to='main_monitor_data',
                    title='set_experiment_basic_config',
                    data={
                        "startup_calibration_mode": mode,
                        "is_auto_calibration": mode != "none"
                    },
                    time=time_util.get_format_from_time(time.time())
                )
            )

        if self.main_gui is not None and hasattr(self.main_gui, "calibration_selection_changed_signal"):
            self.main_gui.calibration_selection_changed_signal.emit(selection_made, mode)

    def is_calibration_gate_passed(self) -> bool:
        if global_setting.get_setting("environment_module_only", False):
            return False
        if global_setting.get_setting("allow_test_calibration_without_air_validation", False):
            return True
        return bool(global_setting.get_setting("air_modules_all_valid", False))

    def sync_action_enabled_state(self):
        """校准按钮只在气路检测全部有效时可点击。"""
        if self.main_gui is None:
            return

        action_name = f"dynamic_{self.name}"
        enabled = self.is_calibration_gate_passed()
        for action_dict in getattr(self.main_gui, "dynamic_tool_bar_actions", []):
            if action_dict.get("obj_name") == action_name:
                action_dict["action"].setEnabled(enabled)
                break

    def click_method(self):
        if not self.is_calibration_gate_passed():
            QMessageBox.warning(self.main_gui, "提示", "请先完成气路检测，并确认 UFC、UGC、ZOS 全部有效后再选择校准。")
            self.sync_action_enabled_state()
            return

        current_mode = self.get_startup_calibration_mode()

        msg_box = QMessageBox(self.main_gui)
        msg_box.setIcon(QMessageBox.Icon.Question)
        msg_box.setWindowTitle("启动前校准模式")
        msg_box.setText("请选择确认设备配置后、开始实验前的校准模式：")

        air_button = msg_box.addButton("Air空气校准后开启实验", QMessageBox.ButtonRole.ActionRole)
        full_button = msg_box.addButton("调零+调span后开启实验", QMessageBox.ButtonRole.ActionRole)
        msg_box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        msg_box.setDefaultButton({
            "air": air_button,
            "full": full_button
        }.get(current_mode, air_button))
        msg_box.exec()

        clicked_button = msg_box.clickedButton()
        if clicked_button == air_button:
            mode = "air"
        elif clicked_button == full_button:
            mode = "full"
        else:
            self.refresh_display_text()
            return

        self.sync_startup_calibration_mode(mode, selection_made=True)
        self.refresh_display_text()
        return
        if not self.is_calibration_gate_passed():
            QMessageBox.warning(self.main_gui, "提示", "请先完成气路检测，且确保 UFC、UGC、ZOS 全部有效后再选择校准。")
            self.sync_action_enabled_state()
            return

        current_value = global_setting.get_setting("is_auto_calibration", False)

        msg_box = QMessageBox(self.main_gui)
        msg_box.setIcon(QMessageBox.Icon.Question)
        msg_box.setWindowTitle("校准设置")
        msg_box.setText("确认设备配置时是否走校准逻辑？")

        yes_button = msg_box.addButton("是", QMessageBox.ButtonRole.YesRole)
        no_button = msg_box.addButton("否", QMessageBox.ButtonRole.NoRole)
        msg_box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        msg_box.setDefaultButton(yes_button if current_value else no_button)
        msg_box.exec()

        clicked_button = msg_box.clickedButton()
        if clicked_button == yes_button:
            is_auto_calibration = True
        elif clicked_button == no_button:
            is_auto_calibration = False
        else:
            self.refresh_display_text()
            return

        global_setting.set_setting("is_auto_calibration", is_auto_calibration)
        global_setting.set_setting("device_config_calibration_selected", True)

        send_message_queue = global_setting.get_setting("send_message_queue")
        if send_message_queue is not None:
            send_message_queue.put(
                ObjectQueueItem(
                    origin='Main_experiment_calibration',
                    to='main_monitor_data',
                    title='set_experiment_basic_config',
                    data={"is_auto_calibration": is_auto_calibration},
                    time=time_util.get_format_from_time(time.time())
                )
            )

        if self.main_gui is not None and hasattr(self.main_gui, "calibration_selection_changed_signal"):
            self.main_gui.calibration_selection_changed_signal.emit(True, is_auto_calibration)

        self.refresh_display_text()


class Camera_config_action_base(BaseModule):
    __module_loader_skip__ = True
    action_title = ""
    action_name = ""
    dialog_title = ""
    toolbar_order = 0

    def __init__(self):
        super().__init__()
        self.interface_widget = self.get_interface_widget()
        self.name = self.get_name()
        self.title = self.get_title()
        self.toolbar_order = self.__class__.toolbar_order
        self.menu_name = self.get_menu_name()
        self.service = self.create_service()
        self.app_state = self.get_app_state()
        self.dialog_frame = None

    def get_app_state(self) -> AppState:
        return AppState.APPLYING

    def get_name(self):
        return self.__class__.action_name

    def get_title(self):
        return self.__class__.action_title

    def get_menu_name(self):
        return {"id": 1, "text": "设备信息"}

    def create_service(self) -> BaseService:
        return Calibration_selection_service()

    def get_interface_widget(self) -> BaseInterfaceWidget:
        widget_builder = Camera_config_action_widget()
        widget_builder.module = self
        return widget_builder

    def click_method(self):
        self.open_dialog()

    def open_dialog(self):
        raise NotImplementedError


class Main_deep_camera_mapping_config(Camera_config_action_base):
    action_name = "Main_deep_camera_mapping_config"
    action_title = "轨迹相机对应鼠笼配置"
    dialog_title = "深度相机配置"
    toolbar_order = 12

    def open_dialog(self):
        camera_state = global_setting.get_setting("deep_camera_instance_state", "starting")
        if camera_state != "ready":
            message = (
                "独立视频模块正在运行，请关闭后再配置深度相机。"
                if camera_state == "blocked"
                else "深度相机服务正在初始化，请稍后再试。"
            )
            QMessageBox.warning(
                self.main_gui,
                "深度相机被占用",
                message,
            )
            return
        self.dialog_frame = deep_camera_config_dialog(
            title=self.dialog_title,
            tip="\n设置好后要重新启动程序！！！！！！"
        )
        self.dialog_frame.show_frame()


class Main_infrared_camera_mapping_config(Camera_config_action_base):
    action_name = "Main_infrared_camera_mapping_config"
    action_title = "红外相机对应鼠笼配置"
    dialog_title = "红外相机配置"
    toolbar_order = 13

    def open_dialog(self):
        self.dialog_frame = infrared_camera_config_dialog(
            title=self.dialog_title,
            tip="\n设置好后要重新启动程序！！！！！！"
        )
        self.dialog_frame.show_frame()

class Main_monitor_hardware_config(Camera_config_action_base):
    action_name = "Main_monitor_hardware_config"
    action_title = "硬件配置"
    dialog_title = "硬件配置"
    toolbar_order = 14

    def get_app_state(self) -> AppState:
        return AppState.APPLYING

    def get_menu_name(self):
        return {"id": 1, "text": "设备信息"}

    def open_dialog(self):
        self.dialog_frame = MonitorHardwareConfigDialog(title=self.dialog_title)
        self.dialog_frame.set_main_gui(self.main_gui)
        self.dialog_frame.show()
        self.dialog_frame.raise_()
        self.dialog_frame.activateWindow()


class Main_lighting_schedule(Camera_config_action_base):
    action_name = "Main_lighting_schedule"
    action_title = "光照时间表"
    dialog_title = "光照时间表"
    toolbar_order = 15

    def get_app_state(self) -> AppState:
        return AppState.INITIALIZED

    def open_dialog(self):
        self.dialog_frame = LightingScheduleDialog(parent=self.main_gui)
        self.dialog_frame.show()
        self.dialog_frame.raise_()
        self.dialog_frame.activateWindow()
