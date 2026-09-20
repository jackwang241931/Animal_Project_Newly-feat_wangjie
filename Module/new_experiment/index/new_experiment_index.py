import typing

from PyQt6 import QtGui
from PyQt6.QtCore import QRect, QTimer
from PyQt6.QtWidgets import QDockWidget, QMainWindow

from Module.new_experiment.ui.animal_window import AnimalWindow
from Module.new_experiment.ui.content_window import ContentWindow
from Module.new_experiment.ui.group_window import GroupWindow
from Module.new_experiment.ui.new_experiment import Ui_new_experiment_window
from public.component.Guide_tutorial_interface.Tutorial_Manager import TutorialManager
from public.config_class.App_Setting import AppSettings
from public.config_class.global_setting import global_setting
from public.entity.enum.Public_Enum import Tutorial_Type
from public.entity.experiment_setting_entity import Experiment_setting_entity
from theme.ThemeQt6 import ThemedWindow


class New_experiment_index(ThemedWindow):


    def showEvent(self, a0: typing.Optional[QtGui.QShowEvent]) -> None:
        # 加载qss样式表

        self.center_widget_content.main_gui=self.main_gui
        self.center_widget_content.init_content(is_update=False)
        self.center_widget_content.update_group_signal.emit(False)
        self.center_widget_content.update_animal_signal.emit(False)
        super().showEvent(a0)
    def hideEvent(self, a0: typing.Optional[QtGui.QHideEvent]) -> None:
        # 是否修改模板
        self.center_widget_content.is_update = False
        self.center_widget_content.is_import = False
        self.center_widget_content.template_file_path_label.setText("未导入实验模板文件")
        self.center_widget_content.import_file_path = ""
        self.center_widget_content.setting_file_path=""
        # 将该全局变量重置
        self.setting_data = Experiment_setting_entity()
        global_setting.set_setting("experiment_setting_new", self.setting_data)
        self.left_dock_widget_content.update_content_signal.emit(False)
        self.right_dock_widget_content.update_content_signal.emit(False)
        self.center_widget_content.update_group_signal.emit(False)
        self.center_widget_content.update_animal_signal.emit(False)
        super().hideEvent(a0)
    def closeEvent(self, a0: typing.Optional[QtGui.QCloseEvent]) -> None:
        # 是否修改模板
        self.center_widget_content.is_update = False
        self.center_widget_content.is_import = False
        self.center_widget_content.import_file_path = ""
        self.center_widget_content.setting_file_path=""
        self.center_widget_content.template_file_path_label.setText("未导入实验模板文件")
        # 将该全局变量重置
        self.setting_data = Experiment_setting_entity()
        global_setting.set_setting("experiment_setting_new", self.setting_data)
        self.left_dock_widget_content.update_content_signal.emit(False)
        self.right_dock_widget_content.update_content_signal.emit(False)
        self.center_widget_content.update_group_signal.emit(False)
        self.center_widget_content.update_animal_signal.emit(False)
        super().closeEvent(a0)

    def setup_tutorial(self):
        # 实例化提示引导器 下面式实例化模板
        if self.tutorial:
            self.tutorial.end_tutorial()

        self.tutorial = TutorialManager(self, "new_experiment_index", Tutorial_Type.ARROW_GUIDE,
                                        global_setting.get_setting("app_setting", AppSettings()))

        # 连接教程完成信号
        self.tutorial.tutorial_completed.connect(self.on_tutorial_completed)

        self.tutorial.add_step(self.left_dock_widget,
                               f"步骤1：\n先在通道配置页面配置相关通道。")
        self.tutorial.add_step(self.right_dock_widget,
                               f"步骤2：\n然后在动物信息配置页面配置相关动物信息。")
        self.tutorial.add_step(self.center_widget_content,
                               f"步骤3：\n随后在通道/动物信息配置页面配置相关通道与相关动物信息绑定。")
        self.tutorial.add_step(self.center_widget_content.apply_button,
                               f"步骤4：\n最后单击该按钮完成配置。")
        self.tutorial.add_step(self.center_widget_content.clear_button,
                               f"tips1：\n你可以清空当前实验模板。")
        self.tutorial.add_step(self.center_widget_content.save_button,
                               f"tips2：\n你可以保存当前实验模板至文件。")
        self.tutorial.add_step(self.center_widget_content.import_button,
                               f"tips3：\n你可以导入实验模板文件。")
        self.tutorial.add_step(self.status_bar.tip_btn,
                               f"tips4：\n如果还不会操作，可再次单击该按钮查看教程。")
    def __init__(self, parent=None, geometry: QRect = None, title=""):
        super().__init__()
        # 实验配置数据
        self.setting_data: Experiment_setting_entity = None

        self.setting_data = global_setting.get_setting("experiment_setting_new",None)
        if self.setting_data is None:
            #如果全局变量没有存储设置则新建一个放进去
            self.setting_data = Experiment_setting_entity()
            global_setting.set_setting("experiment_setting_new",self.setting_data)
        # 布局
        self.left_dock_widget:QDockWidget=None
        self.left_dock_widget_content:GroupWindow=None
        self.right_dock_widget:QDockWidget=None
        self.right_dock_widget_content: AnimalWindow = None
        self.center_widget_content:ContentWindow=None
        # 实例化ui
        self._init_ui(parent, geometry, title)
        # 实例化自定义ui
        self._init_customize_ui()
        # 实例化功能
        self._init_function()
        # 加载qss样式表
        self._init_style_sheet()
        # 实例化提示器
        self.setup_tutorial()
        # 自动启动提示教程 如果有提示页面的话
        QTimer.singleShot(400, self.start_tutorial_if_exists)
        pass

        # 实例化ui

    def _init_ui(self, parent=None, geometry: QRect = None, title=""):
        # 将ui文件转成py文件后 直接实例化该py文件里的类对象  uic工具转换之后就是这一段代码
        # 有父窗口添加父窗口
        if parent != None and geometry != None:
            self.setParent(parent)
            self.setGeometry(geometry)
        else:
            pass

        self.ui = Ui_new_experiment_window()

        self.ui.setupUi(self)

        self._retranslateUi()

        pass
    def _init_customize_ui(self) -> None:
        self.left_dock_widget: QDockWidget = self.findChild(QDockWidget, "left_dock_widget")
        self.right_dock_widget: QDockWidget =self.findChild(QDockWidget, "right_dock_widget")

        self.left_dock_widget_content:GroupWindow = GroupWindow()
        self.right_dock_widget_content:AnimalWindow = AnimalWindow()

        self.center_widget_content:ContentWindow=ContentWindow()
        # 连接信号
        self.left_dock_widget_content.update_content_signal.connect(self.center_widget_content.init_content)
        self.left_dock_widget_content.update_content_signal.connect(self.center_widget_content.update_status)
        self.left_dock_widget_content.update_content_signal.connect(
            self.right_dock_widget_content.refresh_pre_weight_table
        )
        self.right_dock_widget_content.update_content_signal.connect(self.center_widget_content.init_content)
        self.right_dock_widget_content.update_content_signal.connect(self.center_widget_content.update_status)
        self.center_widget_content.update_group_signal.connect(self.left_dock_widget_content.init_group)
        self.center_widget_content.update_animal_signal.connect(self.right_dock_widget_content.init_animal)
        self.center_widget_content.setWindowTitle("新建实验操作")
        if self.left_dock_widget != None:
            self.left_dock_widget.setWindowTitle("组/通道操作")
            self.left_dock_widget.setWidget(self.left_dock_widget_content)

        if self.right_dock_widget != None:
            self.right_dock_widget.setWindowTitle("动物操作")
            self.right_dock_widget.setWidget(self.right_dock_widget_content)
        self.setCentralWidget(self.center_widget_content)
        # self.center_widget_content.setParent(self.centralWidget())
        super()._init_customize_ui()
