from enum import Enum

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QLabel, QHBoxLayout, QPushButton

class Save_Experiment_Dialog_TYPE(Enum):
    # 关闭窗口
    CLOSED =1
    # 将修改保存到原模板文件
    SAVE_SELF=2
    """另存为新的模板文件"""
    SAVE_NEW=3
class Save_Experiment_Dialog(QDialog):
    def closeEvent(self, event):

        # 如果用户确定关闭，则设置结果为 cLOSED
        self.dialog_result = Save_Experiment_Dialog_TYPE.CLOSED
        event.accept()  # 允许关闭对话框
    def __init__(self,title="",text=""):
        super().__init__()
        # self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowTitle(title)
        # 记录对话框结果的变量
        self.dialog_result = None
        # 布局
        layout = QVBoxLayout()

        top_layout = QHBoxLayout()
        self.label = QLabel(text)
        top_layout.addWidget(self.label)

        content_layout= QHBoxLayout()
        # 添加自定义按钮
        self.save_self_Btn = QPushButton("将修改保存到原模板文件")
        self.save_new_Btn = QPushButton("另存为新的模板文件")

        # 将按钮添加到布局中
        content_layout.addWidget(self.save_self_Btn)
        content_layout.addWidget(self.save_new_Btn)

        # 连接按钮的点击事件
        self.save_self_Btn.clicked.connect(self.save_self)
        self.save_new_Btn.clicked.connect(self.save_new)

        layout.addLayout(top_layout)
        layout.addLayout(content_layout)
        self.setLayout(layout)

    def save_self(self):
        """将修改保存到原模板文件"""
        self.dialog_result = Save_Experiment_Dialog_TYPE.SAVE_SELF
        self.accept()  # 关闭对话框并返回 QDialog.Accepted

    def save_new(self):
        """另存为新的模板文件"""
        self.dialog_result = Save_Experiment_Dialog_TYPE.SAVE_NEW
        self.reject()  # 关闭对话框并返回 QDialog.Rejected
    def exec(self):
            """重写 exec 方法以确保获得关闭结果"""
            super().exec()
            return self.dialog_result