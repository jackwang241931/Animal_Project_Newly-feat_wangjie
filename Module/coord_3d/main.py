from Module.coord_3d.index.coord_3d_index import Coord3DIndex
from my_abc.BaseInterfaceWidget import BaseInterfaceWidget
from my_abc.BaseModule import BaseModule
from my_abc.BaseService import BaseService
from public.entity.BaseWindow import BaseWindow
from public.entity.enum.Public_Enum import AppState, BaseInterfaceType


class Main_Coord3D_service(BaseService):
    def __init__(self):
        pass

    def start(self, resolve, reject):
        resolve()

    def stop(self):
        pass


class Main_Coord3D_widget(BaseInterfaceWidget):
    def __init__(self):
        super().__init__()
        self.type = self.get_type()
        self.frame_obj = self.create_middle_window()
        self.left_frame_obj = self.create_left_window()
        self.right_frame_obj = self.create_right_window()
        self.bottom_frame_obj = self.create_bottom_window()

    def get_type(self):
        return BaseInterfaceType.WIDGET

    def create_middle_window(self) -> BaseWindow:
        return Coord3DIndex()

    def create_left_window(self) -> BaseWindow:
        return None

    def create_right_window(self) -> BaseWindow:
        return None

    def create_bottom_window(self) -> BaseWindow:
        return None


class Main_Coord3D_Module(BaseModule):
    def __init__(self):
        super().__init__()
        self.interface_widget = self.get_interface_widget()
        self.name = self.get_name()
        self.title = self.get_title()
        self.menu_name = self.get_menu_name()
        self.service = self.create_service()
        self.app_state = self.get_app_state()

    def get_app_state(self) -> AppState:
        return AppState.MONITORING

    def get_name(self):
        return "Main_Coord3D"

    def get_title(self):
        return "坐标标定"

    def get_menu_name(self):
        return {"id": 2, "text": "实验检测"}

    def create_service(self) -> BaseService:
        return Main_Coord3D_service()

    def get_interface_widget(self) -> BaseInterfaceWidget:
        widget_builder = Main_Coord3D_widget()
        widget_builder.module = self
        return widget_builder
