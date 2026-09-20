from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QWidget


class TitleBar(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedHeight(30)

        layout = QHBoxLayout()
        self.setLayout(layout)

        # 添加自定义标题文本
        title_label = QLabel("My Application")
        layout.addWidget(title_label)

        # 添加最小化按钮
        minimize_button = QPushButton("-")
        minimize_button.clicked.connect(self.minimize_window)
        layout.addWidget(minimize_button)

        # 添加关闭按钮
        close_button = QPushButton("X")
        close_button.clicked.connect(self.close_window)
        layout.addWidget(close_button)

        # 设置布局的对齐方式
        layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

    def minimize_window(self):
        self.window().showMinimized()

    def close_window(self):
        self.window().close()