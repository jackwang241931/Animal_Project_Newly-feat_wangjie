from Module.monitor_camera.index.tab_4 import Tab_4
from PyQt6.QtWidgets import QMessageBox

from my_abc.BaseInterfaceWidget import BaseInterfaceWidget
from my_abc.BaseModule import BaseModule
from my_abc.BaseService import BaseService
from public.entity.BaseWindow import BaseWindow
from public.entity.enum.Public_Enum import BaseInterfaceType, AppState
from public.config_class.global_setting import global_setting


class Main_Monitor_camera_service(BaseService):
    def __init__(self):
        pass

    def start(self, resolve, reject):
        resolve()

    def stop(self):
        pass


class MonitorCameraWidget(BaseInterfaceWidget):
    def __init__(self, display_mode: str):
        super().__init__()
        self.display_mode = display_mode
        self.type = self.get_type()
        self.frame_obj = self.create_middle_window()
        self.left_frame_obj = self.create_left_window()
        self.right_frame_obj = self.create_right_window()
        self.bottom_frame_obj = self.create_bottom_window()

    def get_type(self):
        return BaseInterfaceType.WIDGET

    def create_middle_window(self) -> BaseWindow:
        return Tab_4(display_mode=self.display_mode)

    def create_left_window(self) -> BaseWindow:
        return None

    def create_right_window(self) -> BaseWindow:
        return None

    def create_bottom_window(self) -> BaseWindow:
        return None


class MonitorCameraModuleBase(BaseModule):
    __module_loader_skip__ = True
    module_name = ""
    module_title = ""
    display_mode = Tab_4.MODE_INFRARED
    toolbar_order = 999

    def __init__(self):
        super().__init__()
        self.interface_widget = self.get_interface_widget()
        self.name = self.get_name()
        self.title = self.get_title()
        self.toolbar_order = self.__class__.toolbar_order
        self.menu_name = self.get_menu_name()
        self.service = self.create_service()
        self.app_state = self.get_app_state()

    def get_app_state(self) -> AppState:
        return AppState.MONITORING

    def get_name(self):
        return self.__class__.module_name

    def get_title(self):
        return self.__class__.module_title

    def get_menu_name(self):
        return {"id": 2, "text": "实验检测"}

    def create_service(self) -> BaseService:
        return Main_Monitor_camera_service()

    def get_interface_widget(self) -> BaseInterfaceWidget:
        widget_builder = MonitorCameraWidget(display_mode=self.__class__.display_mode)
        widget_builder.module = self
        return widget_builder


class Main_Infrared_camera_Module(MonitorCameraModuleBase):
    module_name = "Main_Infrared_camera"
    module_title = "红外相机"
    display_mode = Tab_4.MODE_INFRARED


class Main_Video_image_Module(MonitorCameraModuleBase):
    module_name = "Main_Video_image"
    module_title = "视频图像"
    display_mode = Tab_4.MODE_VIDEO

    def click_method(self):
        camera_state = global_setting.get_setting("deep_camera_instance_state", "starting")
        if camera_state != "ready":
            message = (
                "独立视频模块正在运行。请先关闭独立模块，再打开上位机视频功能。"
                if camera_state == "blocked"
                else "视频模块正在初始化，请稍后再试。"
            )
            QMessageBox.warning(
                self.main_gui,
                "视频功能不可用",
                message,
            )
            return
        super().click_method()
