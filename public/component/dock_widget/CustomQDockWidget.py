from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QDockWidget, QScrollArea


class CustomDockWidget(QDockWidget):
    def __init__(self, title, main_window, parent=None):
        super().__init__(title, parent)


        # 创建一个 QWidget 作为 QDockWidget 的内容
        self.upper_scroll = QScrollArea()
        self.upper_scroll.setWidgetResizable(True)
        self.upper_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.upper_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_widget = QWidget()
        layout = QVBoxLayout(self.content_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(main_window)
        self.upper_scroll.setWidget(self.content_widget)
        self.setWidget(self.upper_scroll)