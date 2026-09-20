from PyQt6 import QtWidgets, QtCore
from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QDialog, QToolBar, QDialogButtonBox
from loguru import logger

from public.component.custom_status_bar import CustomStatusBar
from public.config_class import App_Setting
from wrapper.After_execution import after_execution


class BaseDialog(QDialog):
    def __init__(self):
        super().__init__()
        # 弹窗按钮
        self.buttonBox=None
        #帮助按钮
        self.help_button=None
        # 实例化ui
        self._init_ui()
        # 实例化自定义ui
        self._init_customize_ui()
        # 实例化功能
        self._init_function()

        # 先初始化tutorial 提示指示器为None
        self.tutorial = None
        self.setup_tutorial()
        QTimer.singleShot(400, self.start_tutorial_if_exists)


    # 实例化ui
    def _init_ui(self):
        pass
    def insert_buttonBox_button(self,self2):
        """
        对buttonBox进行插入自定义按钮操作
        :return:
        """

        if self.buttonBox is None:
            self.buttonBox = QtWidgets.QDialogButtonBox(parent=self)
            self.buttonBox.setOrientation(QtCore.Qt.Orientation.Horizontal)
            self.buttonBox.setObjectName("buttonBox")
            # 添加自定义按钮

            self.help_button = self.buttonBox.addButton("help", QDialogButtonBox.ButtonRole.HelpRole)
            self.buttonBox.accepted.connect(self.accept)  # type: ignore
            self.buttonBox.rejected.connect(self.reject)  # type: ignore
            self.buttonBox.clicked.connect(self.button_clicked)
            pass
        else:
            self.help_button = self.buttonBox.addButton("help", QDialogButtonBox.ButtonRole.HelpRole)
            self.buttonBox.clicked.connect(self.button_clicked)
            pass
    def button_clicked(self,button):
        """处理按钮点击"""
        if button == self.help_button:
            self.restart_tutorial()
        pass
    @after_execution(insert_buttonBox_button)
    def _init_customize_ui(self):
        pass

    def _init_function(self):
        pass
    # def createStatusBar(self):
    #     self.status_bar = CustomStatusBar()
    #     return  self.status_bar
    #     pass
    # 开始提示引导
    def start_tutorial_if_exists(self):
        if self.tutorial:
            settings: App_Setting = self.tutorial.settings_manager
            if settings and settings.is_first_visit(page_name=self.tutorial.page_name):
                self.tutorial.start_tutorial()
    def setup_tutorial(self):
        # 实例化提示引导器 下面式实例化模板
        # if self.tutorial:
        #     self.tutorial.end_tutorial()
        #
        # self.tutorial = TutorialManager(self, "main_page", self.current_guide_type, self.settings)
        #
        # # 连接教程完成信号
        # self.tutorial.tutorial_completed.connect(self.on_tutorial_completed)
        #
        # # 添加更详细的引导步骤
        # save_widgets = self.save_action.associatedObjects()
        # if save_widgets:
        #     self.tutorial.add_step(save_widgets[0],
        #                            "欢迎使用本应用！\n这是保存功能，可以保存您的工作进度和项目文件。\n建议定期保存以防数据丢失。")
        #
        # self.tutorial.add_step(self.start_btn,
        #                        "开始您的创作之旅\n点击此按钮可以启动新项目。\n系统会为您创建一个全新的工作环境。")
        #
        # self.tutorial.add_step(self.project_list,
        #                        "项目管理中心\n这里显示您的所有项目。\n您可以选择现有项目进行编辑，或查看项目详情。\n支持多项目并行开发。")
        #
        # self.tutorial.add_step(self.export_btn,
        #                        "数据导出功能\n使用此功能可以将项目数据导出为多种格式。\n支持 JSON、CSV、XML 等格式。")
        #
        # self.tutorial.add_step(self.text_editor,
        #                        "主编辑区域\n这是您的创作空间。\n支持富文本编辑、语法高亮、自动补全等功能。\n您可以在这里编写文档、代码或其他内容。")
        #
        # self.tutorial.add_step(self.restart_tutorial_btn,
        #                        "🎉 恭喜！教程完成！\n您已经了解了应用的主要功能。\n随时可以点击此按钮重新查看教程。\n\n开始您的创作之旅吧！")
        pass
    def on_tutorial_completed(self,page_name):
        """教程完成处理"""
        pass

    def restart_tutorial(self):
        """重新开始教程"""
        if self.tutorial:
            self.tutorial.start_tutorial()