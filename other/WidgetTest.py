import sys

from PyQt6.QtWidgets import (QApplication, QMainWindow, QDockWidget, QVBoxLayout, QWidget, QLabel)
from PyQt6.QtCore import Qt

class DemoMainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Demo Main Window")
        self.setGeometry(100, 100, 800, 600)
        self.centerWidget = QWidget()
        label = QLabel( self.centerWidget)
        label.setText("12333333333333333as d")
        # 在这里添加您的 DemoWidget 或其他组件
        self.setCentralWidget(self.centerWidget )

class CustomDockWidget(QDockWidget):
    def __init__(self, title, main_window, parent=None):
        super().__init__(title, parent)


        # 创建一个 QWidget 作为 QDockWidget 的内容
        content_widget = QWidget()
        layout = QVBoxLayout(content_widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(main_window)
        self.setWidget(content_widget)



if __name__ == "__main__":
    app = QApplication(sys.argv)

    # 创建 DemoMainWindow 并将其放入 CustomDockWidget
    demo_main_window = DemoMainWindow()
    custom_dock_widget = CustomDockWidget("Demo Widget", demo_main_window)

    # 将 CustomDockWidget 添加到主窗口
    main_window = QMainWindow()
    main_window.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, custom_dock_widget)
    main_window.show()

    sys.exit(app.exec())