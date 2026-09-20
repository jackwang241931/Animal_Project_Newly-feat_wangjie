from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QApplication, QHBoxLayout, QLabel, QCheckBox, QGroupBox, QButtonGroup, \
    QRadioButton, QPushButton

from public.component.Guide_tutorial_interface.Tutorial_Manager import TutorialManager


class WelcomeDialog(QDialog):
    """欢迎对话框 - 仅在首次运行时显示"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("欢迎使用本应用")
        self.setFixedSize(500, 350)
        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowCloseButtonHint)

        # 设置为模态对话框
        self.setModal(True)

        self.setup_ui()

        # 居中显示
        self.center_on_screen()

    def center_on_screen(self):
        """在屏幕中央显示对话框"""
        screen = QApplication.primaryScreen().geometry()
        x = (screen.width() - self.width()) // 2
        y = (screen.height() - self.height()) // 2
        self.move(x, y)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(20)
        layout.setContentsMargins(30, 30, 30, 30)

        # 欢迎图标和标题
        title_layout = QHBoxLayout()

        icon_label = QLabel("🎉")
        icon_label.setStyleSheet("font-size: 48px;")

        title_label = QLabel("欢迎使用本应用！")
        title_label.setStyleSheet("""
            QLabel {
                font-size: 24px;
                font-weight: bold;
                color: #2c3e50;
            }
        """)

        title_layout.addWidget(icon_label)
        title_layout.addWidget(title_label)
        title_layout.addStretch()

        # 欢迎信息
        welcome_text = QLabel("""
        <p style="font-size: 14px; line-height: 1.6; color: #34495e;">
        这是您第一次使用本应用。为了帮助您快速上手，我们为您准备了一个简短的引导教程。
        </p>

        <p style="font-size: 14px; line-height: 1.6; color: #34495e;">
        教程将向您介绍应用的主要功能和操作方法，只需要几分钟时间。
        </p>

        <p style="font-size: 14px; line-height: 1.6; color: #7f8c8d;">
        您也可以选择跳过教程，稍后通过菜单或按钮重新开始。
        </p>
        """)
        welcome_text.setWordWrap(True)
        welcome_text.setAlignment(Qt.AlignmentFlag.AlignJustify)

        # 选择框
        self.show_tutorial_checkbox = QCheckBox("显示引导教程")
        self.show_tutorial_checkbox.setChecked(True)
        self.show_tutorial_checkbox.setStyleSheet("""
            QCheckBox {
                font-size: 14px;
                color: #2c3e50;
                spacing: 8px;
            }
            QCheckBox::indicator {
                width: 18px;
                height: 18px;
            }
        """)

        # 引导类型选择
        guide_group = QGroupBox("选择引导方式:")
        guide_group.setStyleSheet("""
            QGroupBox {
                font-size: 14px;
                font-weight: bold;
                color: #2c3e50;
                border: 2px solid #bdc3c7;
                border-radius: 8px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 8px 0 8px;
            }
        """)

        guide_layout = QVBoxLayout(guide_group)

        self.guide_radio_group = QButtonGroup()
        self.overlay_radio = QRadioButton("🔍 高亮遮罩引导")
        self.bubble_radio = QRadioButton("💬 气泡提示引导")
        self.arrow_radio = QRadioButton("➤ 箭头指向引导")

        self.overlay_radio.setChecked(True)  # 默认选中

        for radio in [self.overlay_radio, self.bubble_radio, self.arrow_radio]:
            radio.setStyleSheet("font-size: 13px; color: #34495e; margin: 3px;")
            guide_layout.addWidget(radio)
            self.guide_radio_group.addButton(radio)

        # 按钮
        button_layout = QHBoxLayout()

        self.skip_btn = QPushButton("跳过 (3秒后自动关闭)")
        self.start_btn = QPushButton("开始教程")

        for btn in [self.skip_btn, self.start_btn]:
            btn.setFixedHeight(40)
            btn.setStyleSheet("""
                QPushButton {
                    font-size: 14px;
                    font-weight: bold;
                    border-radius: 6px;
                    padding: 8px 20px;
                }
            """)

        self.skip_btn.setStyleSheet(self.skip_btn.styleSheet() + """
            QPushButton {
                background-color: #95a5a6;
                color: white;
                border: none;
            }
            QPushButton:hover {
                background-color: #7f8c8d;
            }
        """)

        self.start_btn.setStyleSheet(self.start_btn.styleSheet() + """
            QPushButton {
                background-color: #3498db;
                color: white;
                border: none;
            }
            QPushButton:hover {
                background-color: #2980b9;
            }
        """)

        # 连接信号
        self.skip_btn.clicked.connect(self.reject)
        self.start_btn.clicked.connect(self.accept)

        button_layout.addWidget(self.skip_btn)
        button_layout.addWidget(self.start_btn)

        # 组装布局
        layout.addLayout(title_layout)
        layout.addWidget(welcome_text)
        layout.addWidget(self.show_tutorial_checkbox)
        layout.addWidget(guide_group)
        layout.addStretch()
        layout.addLayout(button_layout)

        # 自动关闭定时器
        self.countdown_timer = QTimer()
        self.countdown_timer.timeout.connect(self.update_countdown)
        self.countdown_seconds = 15  # 15秒倒计时
        self.countdown_timer.start(1000)  # 每秒更新

    def update_countdown(self):
        """更新倒计时"""
        self.countdown_seconds -= 1
        if self.countdown_seconds > 0:
            self.skip_btn.setText(f"跳过 ({self.countdown_seconds}秒后自动关闭)")
        else:
            self.countdown_timer.stop()
            self.reject()  # 自动关闭

    def get_selected_guide_type(self):
        """获取选择的引导类型"""
        if self.overlay_radio.isChecked():
            return TutorialManager.OVERLAY_GUIDE
        elif self.bubble_radio.isChecked():
            return TutorialManager.BUBBLE_GUIDE
        else:
            return TutorialManager.ARROW_GUIDE

    def should_show_tutorial(self):
        """是否应该显示教程"""
        return self.show_tutorial_checkbox.isChecked()