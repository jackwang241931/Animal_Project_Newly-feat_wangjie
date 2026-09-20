from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import QPushButton, QColorDialog


class ColorButton(QPushButton):
    """颜色选择按钮"""

    def __init__(self, color: str = "#000000", parent=None):
        super().__init__(parent)
        self.color = color
        self.setMaximumSize(30, 25)
        self.setMinimumSize(30, 25)
        self.update_button()
        self.clicked.connect(self.pick_color)

    def update_button(self):
        """更新按钮显示"""
        self.setStyleSheet(f"""
            QPushButton {{
                background-color: {self.color};
                border: 1px solid #666;
                border-radius: 3px;
            }}
        """)

    def pick_color(self):
        """打开颜色选择对话框"""
        color = QColorDialog.getColor(QColor(self.color), self)
        if color.isValid():
            self.color = color.name()
            self.update_button()

    def get_color(self):
        return self.color