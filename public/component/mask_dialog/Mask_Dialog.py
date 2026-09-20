import sys
from PyQt6.QtWidgets import (QApplication, QMainWindow, QPushButton, QWidget,
                             QVBoxLayout, QHBoxLayout, QLabel, QTextEdit)
from PyQt6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QRect
from PyQt6.QtGui import QPainter, QColor


class AdaptiveMaskDialog(QWidget):
    """自适应父窗口大小的遮罩弹窗"""

    def __init__(self, parent=None, title="提示", message="消息内容"):
        super().__init__(parent)
        self.parent_widget = parent
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Widget)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        self.title = title
        self.message = message

        if parent:
            self.setGeometry(parent.geometry())
            self.move(0, 0)  # 相对于父窗口的坐标

        self.setup_ui()
        self.setup_animation()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 对话框内容
        self.dialog_widget = QWidget()
        self.dialog_widget.setFixedSize(350, 200)
        self.dialog_widget.setStyleSheet("""
            QWidget {
                background-color: white;
                border-radius: 12px;
                border: 1px solid #ddd;
            }
        """)

        dialog_layout = QVBoxLayout(self.dialog_widget)
        dialog_layout.setContentsMargins(25, 25, 25, 25)
        dialog_layout.setSpacing(15)

        # 标题
        title_label = QLabel(self.title)
        title_label.setStyleSheet("""
            font-size: 18px; 
            font-weight: bold; 
            color: #2c3e50;
            margin-bottom: 5px;
        """)
        dialog_layout.addWidget(title_label)

        # 消息内容
        message_label = QLabel(self.message)
        message_label.setStyleSheet("""
            font-size: 14px;
            color: #5a6c7d;
            line-height: 1.4;
        """)
        message_label.setWordWrap(True)
        dialog_layout.addWidget(message_label)

        # 按钮区域
        button_layout = QHBoxLayout()
        button_layout.addStretch()

        self.cancel_btn = QPushButton("取消")
        self.cancel_btn.setStyleSheet(self.get_button_style("#95a5a6", "#7f8c8d"))
        self.cancel_btn.clicked.connect(self.hide)

        self.confirm_btn = QPushButton("确定")
        self.confirm_btn.setStyleSheet(self.get_button_style("#3498db", "#2980b9"))
        self.confirm_btn.clicked.connect(self.accept)

        button_layout.addWidget(self.cancel_btn)
        button_layout.addWidget(self.confirm_btn)

        dialog_layout.addStretch()
        dialog_layout.addLayout(button_layout)

        layout.addWidget(self.dialog_widget)

    def get_button_style(self, bg_color, hover_color):
        return f"""
            QPushButton {{
                background-color: {bg_color};
                color: white;
                border: none;
                border-radius: 6px;
                padding: 10px 20px;
                font-size: 13px;
                font-weight: 500;
                min-width: 70px;
                margin-left: 5px;
            }}
            QPushButton:hover {{
                background-color: {hover_color};
            }}
            QPushButton:pressed {{
                background-color: {hover_color};
                transform: translateY(1px);
            }}
        """

    def setup_animation(self):
        # 设置初始状态
        self.dialog_widget.move(
            (self.width() - self.dialog_widget.width()) // 2,
            (self.height() - self.dialog_widget.height()) // 2 - 50
        )

        # 创建动画
        self.slide_animation = QPropertyAnimation(self.dialog_widget, b"geometry")
        self.slide_animation.setDuration(300)
        self.slide_animation.setEasingCurve(QEasingCurve.Type.OutBack)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 120))

    def showEvent(self, event):
        super().showEvent(event)

        if self.parent_widget:
            # 更新几何位置以匹配父窗口
            parent_rect = self.parent_widget.geometry()
            self.setGeometry(0, 0, parent_rect.width(), parent_rect.height())

        # 设置动画
        center_x = (self.width() - self.dialog_widget.width()) // 2
        center_y = (self.height() - self.dialog_widget.height()) // 2

        start_rect = QRect(center_x, center_y - 50,
                           self.dialog_widget.width(), self.dialog_widget.height())
        end_rect = QRect(center_x, center_y,
                         self.dialog_widget.width(), self.dialog_widget.height())

        self.slide_animation.setStartValue(start_rect)
        self.slide_animation.setEndValue(end_rect)
        self.slide_animation.start()

    def accept(self):
        if hasattr(self.parent_widget, 'on_dialog_accepted'):
            self.parent_widget.on_dialog_accepted()
        self.hide()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, 'dialog_widget'):
            center_x = (self.width() - self.dialog_widget.width()) // 2
            center_y = (self.height() - self.dialog_widget.height()) // 2
            self.dialog_widget.move(center_x, center_y)