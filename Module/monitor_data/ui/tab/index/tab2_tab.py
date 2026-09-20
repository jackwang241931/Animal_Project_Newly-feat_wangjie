from PyQt6.QtCore import QRect

from Module.monitor_data.ui.tab.index.tab2_tab0_index import Tab2_tab0
from Module.monitor_data.ui.tab.index.tab2_tab1_index import Tab2_tab1
from Module.monitor_data.ui.tab.index.tab2_tab2_index import Tab2_tab2
from Module.monitor_data.ui.tab.index.tab2_tab3_index import Tab2_tab3
from Module.monitor_data.ui.tab.index.tab2_tab4_index import Tab2_tab4
from Module.monitor_data.ui.tab.index.tab2_tab5_index import Tab2_tab5
from Module.monitor_data.ui.tab.index.tab2_tab6_index import Tab2_tab6
from theme.ThemeQt6 import ThemedWidget

# 左侧菜单按钮控制的菜单窗口类



class Tab2_tab(ThemedWidget):
    # 不同的菜单窗口类
    classes = [Tab2_tab0, Tab2_tab1, Tab2_tab2, Tab2_tab3, Tab2_tab4, Tab2_tab5, Tab2_tab6]

    # 实例化
    def __init__(self, parent=None, geometry: QRect = None, title="", id=1):
        super().__init__()
        # 实例化ui
        # self.classes = class_util.get_class_obj_from_modules_names(path="./index/", mapping="Tab_")
        self._init_ui(parent, geometry, title, id)
        pass

    # 实例化ui
    def _init_ui(self, parent=None, geometry: QRect = None, title="", id=1):
        # 根据 id 绑定相应的菜单窗口
        self.tab = self.classes[id - 1]()
        # self.tab = Tab_1()
