import os

from PyQt6.QtWidgets import QFileDialog, QMessageBox

from Module.new_experiment.index.new_experiment_index import New_experiment_index
from my_abc.BaseInterfaceWidget import BaseInterfaceWidget
from my_abc.BaseModule import BaseModule
from my_abc.BaseService import BaseService
from public.config_class.global_setting import global_setting
from public.dao.SQLite.Experiment_Setting_DAO_Handle import Experiment_Setting_DAO_Handle
from public.entity.BaseWindow import BaseWindow
from public.entity.enum.Public_Enum import BaseInterfaceType, AppState
from public.util.custom_data_file_util import custom_template_file_util, custom_data_file_util
from public.util.folder_util import folder_util


class Main_load_metadata_file_service(BaseService):
    # 组件服务
    def __init__(self):
        super().__init__()
        pass
    def start(self,resolve,reject):
        # 打开实验设置文件
        file_path, _ = QFileDialog.getOpenFileName(self.module.main_gui, "选择Mdata文件", "", f"Mdata files (*.{custom_data_file_util.extension_name});")
        if file_path:
            target_folder =custom_data_file_util.load_folder_contents_from_custom_file(file_path)
            msg_box = QMessageBox(
                QMessageBox.Icon.Information,
                "解析成功",
                f"数据文件已解析到: {target_folder}\n\n点击'open'按钮可以打开文件夹。",
                QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Open
            )
            msg_box.setDefaultButton(QMessageBox.StandardButton.Open)
            # msg_box.setWindowFlags(msg_box.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
            msg_box.setWindowFlags(msg_box.windowFlags())

            if QMessageBox.StandardButton.Open == msg_box.exec():
                # 打开文件夹
                folder_util.open_folder(target_folder)
            resolve()
        else:
            reject()
        pass
    def stop(self):
        pass

class Main_load_metadata_file_widget(BaseInterfaceWidget):
    # 组件自定义界面
    def __init__(self):
        super().__init__()
        self.type = self.get_type()
        self.frame_obj = self.create_middle_window()
        #  左侧窗口
        self.left_frame_obj = self.create_left_window()
        #  右侧窗口
        self.right_frame_obj = self.create_right_window()
        #  bottom窗口
        self.bottom_frame_obj = self.create_bottom_window()

    def get_type(self):
        """获得类型 """
        return BaseInterfaceType.WINDOW

    def create_middle_window(self) -> BaseWindow:

        return None

    def create_left_window(self) -> BaseWindow:
        """创建并返回自定义的界面部件left WINDOW"""
        return None

    def create_right_window(self) -> BaseWindow:
        """创建并返回自定义的界面部件right WINDOW"""
        return None

    def create_bottom_window(self) -> BaseWindow:
        """创建并返回自定义的界面部件bottom WINDOW"""
        return None




class Main_load_metadata_file_Module(BaseModule):
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
        return AppState.INITIALIZED
    def get_name(self):
        """返回组件名称"""
        return "Main_load_metadata_file"
        pass
    def get_title(self):
        """获取组件title"""
        return "加载Mdata文件"
    def get_menu_name(self):
        """返回组件所属菜单{id:,text:} 在./config/gui_config.ini文件查看"""
        # return {"id":3,"text":"工具"}
        pass

    def create_service(self) -> BaseService:
        """创建并返回组件的相关服务"""
        service = Main_load_metadata_file_service()
        service.module = self  # 可以通过引用将组件功能传递给service
        return service
        pass

    def get_interface_widget(self) -> BaseInterfaceWidget:
        """返回自定义界面构建器"""
        widget_builder =Main_load_metadata_file_widget()
        widget_builder.module = self  # 可以通过引用将组件功能传递给界面构建器
        return widget_builder
        pass

