from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox


class InfoDialog(QMessageBox):
    """自定义提示框"""

    def __init__(self,title="", info="",icon:QMessageBox.Icon=QMessageBox.Icon.Warning):
        super().__init__()
        self.setWindowTitle(title)
        self.setText(info)
        # self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.setIcon(icon)  # 设置图标
        self.setStandardButtons(QMessageBox.StandardButton.Ok)