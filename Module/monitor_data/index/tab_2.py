import json
import traceback
import typing

from loguru import logger


from PyQt6 import QtWidgets, QtGui
from PyQt6.QtCore import QRect, pyqtSignal, QSize
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QScrollArea, QPushButton, QComboBox

from Module.monitor_data.ui.tab.index.tab2_tab import Tab2_tab
from Module.monitor_data.ui.tab2_window import Ui_tab2_window
from public.config_class.global_setting import global_setting
from public.entity.experiment_setting_entity import Experiment_setting_entity
from theme.ThemeQt6 import ThemedWindow

#logger = logger.bind(category="gui_logger")
class Tab_2(ThemedWindow):
    # 状态栏更新信息信号
    # 更新btn css样式
    update_btn_css_signal = pyqtSignal()
    def resizeEvent(self, a0 :typing.Optional[QtGui.QResizeEvent]):
        # 获取新的大小
        new_size: QSize = a0.size()

        old_size: QSize = a0.oldSize()


        # 模糊查找包含 'scroll_tab_content_widget_' 的对象名
        # # 使用 findChildren 查找所有 QWidget 的子组件
        # all_widgets =self.tabWidget.findChildren(QWidget)
        # filtered_widgets_SCROLL = [widget for widget in all_widgets if 'scroll_tab_content_widget_' in widget.objectName()]
        if self.tabWidget is not None:
            filtered_widgets_SCROLL = [self.tabWidget.findChild(QWidget,f"scroll_tab_content_widget_{i}") for i in range(10)]
            for widget in filtered_widgets_SCROLL:
                widget:QWidget
                if widget is not None:
                    widget.setFixedSize(int(new_size.width()*0.95), int(new_size.height()))
                    widget.updateGeometry()

        super().resizeEvent(a0)
    def showEvent(self, a0: typing.Optional[QtGui.QShowEvent]) -> None:
        # 线程重新响应
        logger.warning("tab2——show")
        # 实验设置
        self.experiment_setting:Experiment_setting_entity=global_setting.get_setting("experiment_setting",None)
        self.experiment_setting_file=global_setting.get_setting("experiment_setting_file",None)

        # 获取相关数据
        self._init_data()
        # 实例化自定义ui
        self._init_customize_ui()
        # 实例化功能
        self._init_function()
        self._init_style_sheet()
        self._init_customize_style_sheet()
        try:
            for tab_frame in  self.tab_frames:
                tab_current = tab_frame.tab
                if tab_current is not None:
                    if tab_current.store_thread_for_tab_frame is not None and tab_current.store_thread_for_tab_frame.isRunning():

                        tab_current.store_thread_for_tab_frame.resume()
                    elif not tab_current.store_thread_for_tab_frame.isRunning():

                        tab_current.store_thread_for_tab_frame.start()

                    if tab_current.detaildata_table is not None and tab_current.detaildata_table.data_fetcher_thread is not None and tab_current.detaildata_table.data_fetcher_thread.isRunning():
                        tab_current.detaildata_table.data_fetcher_thread.resume()
                    elif not tab_current.detaildata_table.data_fetcher_thread.isRunning():
                        tab_current.detaildata_table.data_fetcher_thread.start()
                    pass

                    if tab_current.now_data_chart_widget is not None and tab_current.now_data_chart_widget.data_fetcher_thread is not None and tab_current.now_data_chart_widget.data_fetcher_thread.isRunning():
                        tab_current.now_data_chart_widget.data_fetcher_thread.resume()
                    elif not tab_current.now_data_chart_widget.data_fetcher_thread.isRunning():
                        tab_current.now_data_chart_widget.data_fetcher_thread.start()
                    pass
        except Exception as e:
            logger.error(f"<UNK>{e} |  <UNK>{traceback.print_exc()}<UNK>")
        super().showEvent(a0)

    def hideEvent(self, a0: typing.Optional[QtGui.QHideEvent]) -> None:
        # 线程暂停

        logger.warning("tab2--hide")
        for tab_frame in  self.tab_frames:
            tab_current = tab_frame.tab
            if tab_current is not None:
                if tab_current.store_thread_for_tab_frame is not None and tab_current.store_thread_for_tab_frame.isRunning():
                    tab_current.store_thread_for_tab_frame.pause()
                if tab_current.now_data_chart_widget is not None and tab_current.now_data_chart_widget.data_fetcher_thread is not None and tab_current.now_data_chart_widget.data_fetcher_thread.isRunning():
                    tab_current.now_data_chart_widget.data_fetcher_thread.pause()
                if tab_current.detaildata_table is not None and tab_current.detaildata_table.data_fetcher_thread is not None and tab_current.detaildata_table.data_fetcher_thread.isRunning():
                    tab_current.detaildata_table.data_fetcher_thread.pause()
        super().hideEvent(a0)

    def __init__(self, parent=None, geometry: QRect = None, title=""):
        super().__init__()

        # 鼠笼下拉框数据列表
        self.mouse_cages = []
        # 实验设置
        self.experiment_setting:Experiment_setting_entity=global_setting.get_setting("experiment_setting",None)
        self.experiment_setting_file=global_setting.get_setting("experiment_setting_file",None)
        # 发送的数据结构

        # self.send_message = {
        #     'port': '',
        #     'data': '',
        #     'slave_id': 0,
        #     'function_code': 0,
        #     'timeout': 0,
        #     'mouse_cage': 0
        # }
        self.modbus = None
        # tab页面保存
        self.tab_frames = []
        self.tabWidget=None
        # 实例化ui
        self._init_ui(parent, geometry, title)

        # 实例化ui

    def _init_ui(self, parent=None, geometry: QRect = None, title=""):
        # 将ui文件转成py文件后 直接实例化该py文件里的类对象  uic工具转换之后就是这一段代码
        # 有父窗口添加父窗口
        if parent != None and geometry != None:
            self.setParent(parent)
            self.setGeometry(geometry)
        else:
            pass
        self.ui = Ui_tab2_window()
        self.ui.setupUi(self)

        self._retranslateUi()
        pass
        # 获得相关数据

    def _init_data(self):
        if self.experiment_setting is not None:
            # 鼠笼下拉框数据
            self.mouse_cages = [{
                "id": i + 1,
                "description": f"鼠笼{i + 1}",
            } for i in range(len(self.experiment_setting.groups))]
            pass

    # 实例化自定义ui
    def _init_customize_ui(self):

        # 鼠笼下拉框
        self.init_mouse_cage_combox()
        # 根据监测数据项配置tab页
        self._init_monitor_data_tab_page()

        pass

    # 实例化功能
    def _init_function(self):
        # 实例化按钮信号槽绑定
        self.init_btn_func()


        # 更新btn css
        self.update_btn_css_signal.connect(self._init_customize_style_sheet)

        # # 让tab页面找到自己 为后续数据交流做准备
        # for tab_frame in self.tab_frames:
        #     tab_frame.tab.get_ancestor("tab2_frame")
        #     tab_frame.tab.update_ancestor_and_send_thread_signal.emit()
        pass

    def _init_customize_style_sheet(self):
        pushbtns: [QPushButton] = self.findChildren(QPushButton)
        for btn in pushbtns:
            btn.setStyleSheet("""
               QPushButton{

                       padding: 5px;

                   }
               """)
        pass

    # 实例化按钮信号槽绑定
    def init_btn_func(self):

        pass

    # 实例化鼠笼下拉框
    def init_mouse_cage_combox(self):
        mouse_cage_combox: QComboBox = self.findChild(QComboBox, "tab_2_mouse_cage_combox")
        if mouse_cage_combox == None:
            logger.error("tab2实例化鼠笼下拉框失败！")
            return
        mouse_cage_combox.clear()
        for mouse_cage_obj in self.mouse_cages:
            mouse_cage_combox.addItem(f"{mouse_cage_obj['description']}")
            pass
        if len(self.mouse_cages) != 0:
            # 默认下拉项
            global_setting.set_setting("tab2_select_mouse_cage", self.mouse_cages[0]['id'])

        mouse_cage_combox.disconnect()
        mouse_cage_combox.currentIndexChanged.connect(self.selection_change_mouse_cage_combox)

    def selection_change_mouse_cage_combox(self, index):
        try:
            global_setting.set_setting("tab2_select_mouse_cage", self.mouse_cages[index]['id'])
            # 发送信号给子tab页面进行更新数据
            for tab_frame in self.tab_frames:
                tab_frame.tab.update_port_and_mouse_cage.emit()

        except Exception as e:
            logger.error(f"{e}")
        pass



    # 根据监测数据项配置tab页
    def _init_monitor_data_tab_page(self):
        # tabwidget是否存在
        self.tabWidget = self.findChild(QtWidgets.QTabWidget, "tab_2_tabWidget")
        if self.tabWidget is None:
            # tab页布局
            content_layout_son: QVBoxLayout = self.findChild(QVBoxLayout, "content_layout_son")

            self.tabWidget = QtWidgets.QTabWidget()
            self.tabWidget.setObjectName("tab_2_tabWidget")
            self.tabWidget.setStyleSheet("")
            monitor_data_tab_page_config = global_setting.get_setting("configer")['monitoring_data']
            monitor_data_tab_page_config_data = json.loads(monitor_data_tab_page_config['value'])
            i = 0
            self.tab_frames = []
            for monitor_data in monitor_data_tab_page_config_data:
                tab_content = QtWidgets.QWidget()
                tab_content.setObjectName(f"tab_{monitor_data['id'] - 1}_{monitor_data['object_name']}")
                tab_layout = QVBoxLayout(tab_content)
                tab_layout.setObjectName(f"tab_{monitor_data['id'] - 1}_{monitor_data['object_name']}_layout")
                # 创建一个 QScrollArea
                scroll_area = QScrollArea(tab_content)
                scroll_area.setObjectName(f"scroll_tab_{i}")
                scroll_area.setWidgetResizable(True)  # 使内容小部件可以调整大小

                # 创建一个内容小部件并填充内容

                tab_frame = Tab2_tab(id=monitor_data['id'])
                self.tab_frames.append(tab_frame)
                content_widget = QWidget()
                content_widget.setObjectName(f"scroll_tab_content_widget_{i}")

                content_layout = QVBoxLayout(content_widget)
                content_layout.setObjectName(f"scroll_tab_{i}_content_widget_layout")
                content_layout.addWidget(tab_frame.tab)

                # 将内容小部件添加到 QScrollArea
                scroll_area.setWidget(content_widget)
                # 将 scroll_area 添加进去
                tab_layout.addWidget(scroll_area)
                tab_content.setLayout(tab_layout)
                self.tabWidget.addTab(tab_content, monitor_data['title'])
                i += 1
                pass
                pass
            content_layout_son.addWidget(self.tabWidget)
