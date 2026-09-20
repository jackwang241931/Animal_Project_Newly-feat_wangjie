

from theme.ThemeQt6 import ThemedWindow
from ui.content import Ui_content_window


class content_index(ThemedWindow):
    def __init__(self):
        super().__init__()
        # 实例化ui
        self._init_ui()
        # 实例化自定义ui
        self._init_customize_ui()
        # 实例化功能
        self._init_function()
        # 加载qss样式表
        self._init_custom_style_sheet()
        pass
    def _init_ui(self):
        self.ui = Ui_content_window()
        self.ui.setupUi(self)

        self.setObjectName("content_Index")
        pass
    def _init_customize_ui(self):
        pass
    def _init_function(self):
        pass
    def _init_custom_style_sheet(self):
        pass