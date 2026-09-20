from PyQt6.QtWidgets import QMainWindow

from Module.experiment_setting.index.tab_7 import Tab_7
from my_abc.BaseInterfaceWidget import BaseInterfaceWidget
from my_abc.BaseModule import BaseModule
from my_abc.BaseService import BaseService
from public.entity.BaseWidget import BaseWidget
from public.entity.BaseWindow import BaseWindow
from public.entity.enum.Public_Enum import BaseInterfaceType, AppState


class Main_experiment_setting_service(BaseService):
    # 组件服务
    def __init__(self):
        pass

    def start(self, resolve, reject):
        resolve()
    def stop(self):
        pass

class Main_experiment_setting_widget(BaseInterfaceWidget):
    # 组件自定义界面
    def __init__(self):
        super().__init__()
        self.type = self.get_type()
        self.frame_obj = self.create_middle_window()
        #  左侧窗口
        self.left_frame_obj=self.create_left_window()
        #  右侧窗口
        self.right_frame_obj=self.create_right_window()
        #  bottom窗口
        self.bottom_frame_obj=self.create_bottom_window()

    def get_type(self):
        """获得类型 """
        return BaseInterfaceType.WIDGET
    def create_middle_window(self) -> BaseWindow:
        return Tab_7()

    def create_left_window(self) -> BaseWindow:
        """创建并返回自定义的界面部件left WINDOW"""
        return None


    def create_right_window(self) -> BaseWindow:
        """创建并返回自定义的界面部件right WINDOW"""
        return None

    def create_bottom_window(self) -> BaseWindow:
        """创建并返回自定义的界面部件bottom WINDOW"""
        return None





class Main_experiment_setting(BaseModule):
    def __init__(self):
        super().__init__()
        self.interface_widget=self.get_interface_widget()
        self.name = self.get_name()
        self.title = self.get_title()
        self.menu_name = self.get_menu_name()
        self.service= self.create_service()
        self.app_state = self.get_app_state()
        pass


    def get_app_state(self) -> AppState:
        return AppState.MONITORING
    def get_name(self):
        """返回组件名称"""
        return "Main_experiment_setting"
        pass
    def get_title(self):
        """获取组件title"""
        return "设备配置（旧版本）"
    def get_menu_name(self):
        """返回组件所属菜单{id:,text:} 在./config/gui_config.ini文件查看"""
        return {"id":2,"text":"实验检测"}
        pass

    def create_service(self) -> BaseService:
        """创建并返回组件的相关服务"""
        return Main_experiment_setting_service()
        pass

    def get_interface_widget(self) -> BaseInterfaceWidget:
        """返回自定义界面构建器"""
        widget_builder =Main_experiment_setting_widget()
        widget_builder.module = self  # 可以通过引用将组件功能传递给界面构建器
        return widget_builder
        pass

