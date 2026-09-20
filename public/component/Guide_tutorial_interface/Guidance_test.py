import sys
import math
from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import *

from public.component.Guide_tutorial_interface.Tutorial_Manager import TutorialManager
from public.component.dialog.custom.welcome_dialog import WelcomeDialog
from public.config_class.App_Setting import AppSettings
from public.entity.enum.Public_Enum import Tutorial_Type


# 这里放入上面的所有类定义...

class DemoMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("优化的多种引导方式演示")
        self.setGeometry(100, 100, 1000, 700)

        # 初始化设置管理器
        self.settings = AppSettings()

        self.tutorial = None
        self.current_guide_type = self.settings.get_guide_type()

        self.setup_ui()
        self.setup_tutorial()

        # 检查是否是第一次运行
        QTimer.singleShot(1000,self.check_first_visit)

    def check_first_visit(self):
        """检查是否是第一次访问"""
        if self.settings.is_first_visit("main_page"):
            self.start_tutorial_if_exists()


    def start_tutorial_if_exists(self):
        if self.tutorial:
            self.tutorial.start_tutorial()

    def setup_ui(self):
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # 主布局
        main_layout = QVBoxLayout(central_widget)
        main_layout.setSpacing(15)
        main_layout.setContentsMargins(25, 25, 25, 25)

        # 工具栏
        toolbar = self.addToolBar("主工具栏")
        self.save_action = toolbar.addAction("💾", "保存文件")
        self.open_action = toolbar.addAction("📁", "打开文件")
        self.new_action = toolbar.addAction("📄", "新建文件")

        # 引导类型选择区域
        guide_layout = QHBoxLayout()
        guide_label = QLabel("🎯 选择引导方式:")
        guide_label.setStyleSheet("font-weight: bold; font-size: 14px; color: #2c3e50;")

        self.overlay_btn = QPushButton("🔍 高亮遮罩引导")
        self.bubble_btn = QPushButton("💬 气泡提示引导")
        self.arrow_btn = QPushButton("➤ 箭头指向引导")

        self.overlay_btn.clicked.connect(lambda: self.switch_guide_type(Tutorial_Type.OVERLAY_GUIDE))
        self.bubble_btn.clicked.connect(lambda: self.switch_guide_type(Tutorial_Type.BUBBLE_GUIDE))
        self.arrow_btn.clicked.connect(lambda: self.switch_guide_type(Tutorial_Type.ARROW_GUIDE))

        # 设置按钮样式
        for btn in [self.overlay_btn, self.bubble_btn, self.arrow_btn]:
            btn.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #f8f9fa, stop:1 #e9ecef);
                    border: 2px solid #dee2e6;
                    border-radius: 8px;
                    padding: 10px 20px;
                    font-size: 12px;
                    font-weight: 500;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #e3f2fd, stop:1 #bbdefb);
                    border-color: #2196F3;
                }
                QPushButton:checked {
                    background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                        stop:0 #4CAF50, stop:1 #45a049);
                    color: white;
                    border-color: #4CAF50;
                }
            """)

        # 设置为可选中状态
        for btn in [self.overlay_btn, self.bubble_btn, self.arrow_btn]:
            btn.setCheckable(True)

        # 根据设置选中对应按钮
        if self.current_guide_type == Tutorial_Type.OVERLAY_GUIDE:
            self.overlay_btn.setChecked(True)
        elif self.current_guide_type == Tutorial_Type.BUBBLE_GUIDE:
            self.bubble_btn.setChecked(True)
        else:
            self.arrow_btn.setChecked(True)

        guide_layout.addWidget(guide_label)
        guide_layout.addWidget(self.overlay_btn)
        guide_layout.addWidget(self.bubble_btn)
        guide_layout.addWidget(self.arrow_btn)
        guide_layout.addStretch()

        # 顶部操作区
        top_layout = QHBoxLayout()

        self.start_btn = QPushButton("🚀 开始项目")
        self.start_btn.setMinimumSize(120, 45)
        self.start_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #4CAF50, stop:1 #45a049);
                color: white;
                border: none;
                border-radius: 8px;
                font-size: 14px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #5CBF60, stop:1 #55b059);
            }
        """)

        self.pause_btn = QPushButton("⏸️ 暂停")
        self.stop_btn = QPushButton("⏹️ 停止")

        top_layout.addWidget(self.start_btn)
        top_layout.addWidget(self.pause_btn)
        top_layout.addWidget(self.stop_btn)
        top_layout.addStretch()

        # 中间内容区域
        content_layout = QHBoxLayout()

        # 左侧面板
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_widget.setStyleSheet("""
            QWidget {
                background: #f8f9fa;
                border-radius: 10px;
                padding: 15px;
            }
        """)

        self.project_list = QListWidget()
        self.project_list.addItems([
            "🔵 项目 Alpha - 数据分析",
            "🟢 项目 Beta - 网站开发",
            "🟡 项目 Gamma - 移动应用",
            "🔴 项目 Delta - 人工智能"
        ])

        self.export_btn = QPushButton("📤 导出项目数据")
        self.import_btn = QPushButton("📥 导入配置文件")

        left_layout.addWidget(QLabel("📂 项目管理中心"))
        left_layout.addWidget(self.project_list)
        left_layout.addWidget(self.export_btn)
        left_layout.addWidget(self.import_btn)

        # 右侧编辑区
        self.text_editor = QTextEdit()
        self.text_editor.setPlaceholderText("📝 在此输入您的项目内容和想法...\n\n支持富文本编辑、代码高亮等功能。")

        content_layout.addWidget(left_widget, 1)
        content_layout.addWidget(self.text_editor, 2)

        # 底部控制区域
        bottom_layout = QHBoxLayout()

        self.restart_tutorial_btn = QPushButton("🎯 重新开始引导教程")
        self.restart_tutorial_btn.clicked.connect(self.restart_tutorial)
        self.restart_tutorial_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #17a2b8, stop:1 #138496);
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #20c997, stop:1 #1e7e34);
            }
        """)

        # 重置首次运行按钮（仅用于测试）
        self.reset_first_run_btn = QPushButton("🔄 重置首次运行状态 (测试用)")
        self.reset_first_run_btn.clicked.connect(self.reset_first_run_status)
        self.reset_first_run_btn.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #fd7e14, stop:1 #e55a00);
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 #ff8530, stop:1 #fd7e14);
            }
        """)

        bottom_layout.addWidget(self.restart_tutorial_btn)
        bottom_layout.addWidget(self.reset_first_run_btn)
        bottom_layout.addStretch()

        # 组装主布局
        main_layout.addLayout(guide_layout)
        main_layout.addLayout(top_layout)
        main_layout.addLayout(content_layout)
        main_layout.addLayout(bottom_layout)

        self.setMinimumSize(800, 600)

    def switch_guide_type(self, guide_type):
        """切换引导类型"""
        self.current_guide_type = guide_type

        # 保存设置
        self.settings.set_guide_type(guide_type)

        # 更新按钮状态
        self.overlay_btn.setChecked(guide_type == Tutorial_Type.OVERLAY_GUIDE)
        self.bubble_btn.setChecked(guide_type == Tutorial_Type.BUBBLE_GUIDE)
        self.arrow_btn.setChecked(guide_type == Tutorial_Type.ARROW_GUIDE)

        # 重新设置教程
        self.setup_tutorial()

        # 显示切换成功的消息
        guide_names = {
            Tutorial_Type.OVERLAY_GUIDE: "🔍 高亮遮罩引导",
            Tutorial_Type.BUBBLE_GUIDE: "💬 气泡提示引导",
            Tutorial_Type.ARROW_GUIDE: "➤ 箭头指向引导"
        }

        # 使用状态栏显示切换信息
        self.statusBar().showMessage(f"已切换到: {guide_names[guide_type]} - 点击'重新开始引导教程'体验", 3000)

    def setup_tutorial(self):
        """设置教程"""
        if self.tutorial:
            self.tutorial.end_tutorial()

        self.tutorial = TutorialManager(self,"main_page", self.current_guide_type, self.settings)

        # 连接教程完成信号
        self.tutorial.tutorial_completed.connect(self.on_tutorial_completed)

        # 添加更详细的引导步骤
        save_widgets = self.save_action.associatedObjects()
        if save_widgets:
            self.tutorial.add_step(save_widgets[1],
                                   "欢迎使用本应用！\n这是保存功能，可以保存您的工作进度和项目文件。\n建议定期保存以防数据丢失。")

        self.tutorial.add_step(self.start_btn,
                               "开始您的创作之旅\n点击此按钮可以启动新项目。\n系统会为您创建一个全新的工作环境。")

        self.tutorial.add_step(self.project_list,
                               "项目管理中心\n这里显示您的所有项目。\n您可以选择现有项目进行编辑，或查看项目详情。\n支持多项目并行开发。")

        self.tutorial.add_step(self.export_btn,
                               "数据导出功能\n使用此功能可以将项目数据导出为多种格式。\n支持 JSON、CSV、XML 等格式。")

        self.tutorial.add_step(self.text_editor,
                               "主编辑区域\n这是您的创作空间。\n支持富文本编辑、语法高亮、自动补全等功能。\n您可以在这里编写文档、代码或其他内容。")

        self.tutorial.add_step(self.restart_tutorial_btn,
                               "🎉 恭喜！教程完成！\n您已经了解了应用的主要功能。\n随时可以点击此按钮重新查看教程。\n\n开始您的创作之旅吧！")

    def on_tutorial_completed(self):
        """教程完成处理"""
        self.statusBar().showMessage("🎉 教程已完成！感谢您的耐心学习。", 3000)

    def restart_tutorial(self):
        """重新开始教程"""
        if self.tutorial:
            self.tutorial.start_tutorial()

    def reset_first_run_status(self):
        """重置首次运行状态（仅用于测试）"""
        reply = QMessageBox.question(
            self,
            "确认重置",
            "这将重置所有页面的首次访问状态，下次进入各个页面时会再次显示引导教程。\n\n确定要继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            # 重置程序首次运行状态
            self.settings.settings["first_run"] = True
            self.settings.settings["tutorial_completed"] = False

            # 获取所有以 "first_visit_" 开头的设置项并重置为 True
            keys_to_reset = []
            for key in self.settings.settings.keys():
                if key.startswith("first_visit_"):
                    keys_to_reset.append(key)

            # 重置所有页面的首次访问状态
            for key in keys_to_reset:
                self.settings.settings[key] = True

            # 也可以直接重置特定页面（如果已知页面名称）
            page_names = ["main_page", "project_page", "settings_page", "help_page"]  # 可根据实际页面名称调整
            for page_name in page_names:
                self.settings.settings[f"first_visit_{page_name}"] = True

            self.settings.save_settings()

            # 显示重置的页面信息
            reset_pages = [key.replace("first_visit_", "") for key in keys_to_reset]
            if reset_pages:
                pages_info = "、".join(reset_pages)
                message = f"所有状态已重置。\n\n已重置的页面: {pages_info}\n\n重新进入这些页面时将显示引导教程。"
            else:
                message = "首次运行状态已重置。\n重新启动程序或进入页面时将显示引导教程。"

            QMessageBox.information(
                self,
                "重置完成",
                message
            )

            self.statusBar().showMessage("✅ 所有页面的首次访问状态已重置", 3000)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = DemoMainWindow()
    window.show()
    sys.exit(app.exec())