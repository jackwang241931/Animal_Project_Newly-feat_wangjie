import math
import typing

from loguru import logger

from public.component.paging_exportcsv_table_widget import TableWidgetPaging
from public.component.selection_line_charts import LineChartWidget
from Module.monitor_data.ui.tab.index.tab2_tab0_index import Store_thread_for_tab_frame
from Module.monitor_data.ui.tab.tab2_tab5_window import Ui_tab_5_window

from public.config_class.global_setting import global_setting
from public.function.Modbus.Modbus_Type import Modbus_Slave_Ids
from theme.ThemeQt6 import ThemedWindow
from PyQt6 import QtGui
from PyQt6.QtCore import QRect, pyqtSignal
from PyQt6.QtWidgets import QWidget, QPushButton, QGroupBox, QGridLayout, QHBoxLayout, QLabel, \
    QVBoxLayout, QScrollArea

#logger = logger.bind(category="gui_logger")
class Tab2_tab5(ThemedWindow):
    # 更新端口选择和鼠笼选择
    update_port_and_mouse_cage = pyqtSignal()
    # 显示数据
    show_data_signal = pyqtSignal(dict)

    def showEvent(self, a0: typing.Optional[QtGui.QShowEvent]) -> None:
        logger.warning(f"{self.objectName()}——show")
        if self.store_thread_for_tab_frame is not None and self.store_thread_for_tab_frame.isRunning():

            self.store_thread_for_tab_frame.resume()
        elif not self.store_thread_for_tab_frame.isRunning():

            self.store_thread_for_tab_frame.start()

        if self.now_data_chart_widget is not None and self.now_data_chart_widget.data_fetcher_thread is not None and self.now_data_chart_widget.data_fetcher_thread.isRunning():
            self.now_data_chart_widget.data_fetcher_thread.resume()
        elif not self.now_data_chart_widget.data_fetcher_thread.isRunning():
            self.now_data_chart_widget.data_fetcher_thread.start()

        if self.detaildata_table is not None and self.detaildata_table.data_fetcher_thread is not None and self.detaildata_table.data_fetcher_thread.isRunning():
            self.detaildata_table.data_fetcher_thread.resume()
        elif not self.detaildata_table.data_fetcher_thread.isRunning():
            self.detaildata_table.data_fetcher_thread.start()

    def hideEvent(self, a0: typing.Optional[QtGui.QHideEvent]) -> None:
        logger.warning(f"{self.objectName()}--hidden")
        if self.store_thread_for_tab_frame is not None and self.store_thread_for_tab_frame.isRunning():
            self.store_thread_for_tab_frame.pause()
        if self.now_data_chart_widget is not None and self.now_data_chart_widget.data_fetcher_thread is not None and self.now_data_chart_widget.data_fetcher_thread.isRunning():
            self.now_data_chart_widget.data_fetcher_thread.pause()
        if self.detaildata_table is not None and self.detaildata_table.data_fetcher_thread is not None and self.detaildata_table.data_fetcher_thread.isRunning():
            self.detaildata_table.data_fetcher_thread.pause()

    def __init__(self, parent=None, geometry: QRect = None, title=""):
        super().__init__()
        # 类型
        self.type = Modbus_Slave_Ids.EM
        # 图表：
        self.now_data_layout: QHBoxLayout = None
        # 图表widget
        self.now_data_chart_widget: LineChartWidget = None
        # table
        self.detaildata_table: TableWidgetPaging = None
        # 找到开始获取信息按钮
        self.start_btn: QPushButton = None
        # 找到停止获取信息按钮
        self.stop_btn: QPushButton = None
        # 找到刷新全部信息按钮
        self.refresh_btn: QPushButton = None
        # 要发送的数据
        self.store_thread_for_tab_frame = None

        # 实例化ui
        self._init_ui(parent, geometry, title)

        # 实例化自定义ui
        self._init_customize_ui()
        # 实例化功能
        self._init_function()
        # 加载qss样式表
        self._init_style_sheet()

        pass

    # 实例化ui
    def _init_ui(self, parent=None, geometry: QRect = None, title=""):
        # 将ui文件转成py文件后 直接实例化该py文件里的类对象  uic工具转换之后就是这一段代码
        # 有父窗口添加父窗口
        if parent != None and geometry != None:
            self.setParent(parent)
            self.setGeometry(geometry)
        else:
            pass
        self.ui = Ui_tab_5_window()
        self.ui.setupUi(self)

        self._retranslateUi()
        pass

    # 实例化自定义ui
    def _init_customize_ui(self):
        # 添加最新数据图表charts
        self.now_data_layout = self.findChild(QHBoxLayout, "now_data_layout")
        # charts!
        try:
            self.now_data_chart_widget = LineChartWidget(type=self.type, object_name=f"{self.type.value['name']}_monitor_data_line_chart",data_type='monitor_data',
                                                         mouse_cage_number=global_setting.get_setting("tab2_select_mouse_cage"), parent=self.now_data_layout)
        except Exception as e:
            logger.error(f"tab_tab5_index图表创建错误：{e}")
        pass

        # 添加最新数据表格table
        self.detaildata_layout: QVBoxLayout = self.findChild(QVBoxLayout, "detaildata_layout")
        # charts!
        try:
            self.detaildata_table = TableWidgetPaging(type=self.type, data_type='monitor_data',
                                                      mouse_cage_number=global_setting.get_setting(
                                                          "tab2_select_mouse_cage"),
                                                      parent=self.detaildata_layout)
        except Exception as e:
            logger.error(f"tab_tab5_index table创建错误：{e}")
        pass

    # 实例化功能
    def _init_function(self):
        # 绑定更新发送数据的槽
        self.update_port_and_mouse_cage.connect(self.update_send_data)
        # 绑定显示数据的槽
        self.show_data_signal.connect(self.show_data)

        # 实例化发送查询报文线程
        if self.store_thread_for_tab_frame is None:
            self.store_thread_for_tab_frame = Store_thread_for_tab_frame(
                name=self.objectName(),
                type=self.type,
                show_data_signal=self.show_data_signal,
                tab_int=5
            )
            self.store_thread_for_tab_frame.mouse_cage_number = global_setting.get_setting("tab2_select_mouse_cage")
        # 实例化按钮功能
        self.init_btn_func()
        pass

    def init_btn_func(self):
        # 实例化按钮功能
        # 找到开始获取信息按钮
        self.start_btn: QPushButton = self.findChild(QPushButton, 'start')
        # 找到停止获取信息按钮
        self.stop_btn: QPushButton = self.findChild(QPushButton, 'stop')
        # 找到刷新全部信息按钮
        self.refresh_btn: QPushButton = self.findChild(QPushButton, 'refresh')
        self.start_btn.setDisabled(True)
        self.start_btn.clicked.connect(self.start_btn_func)
        self.stop_btn.clicked.connect(self.stop_btn_func)
        self.refresh_btn.clicked.connect(self.refresh_btn_func)
        pass

    def start_btn_func(self):
        """
        开始获取信息按钮功能
        :return:
        """
        if self.store_thread_for_tab_frame is not None and self.store_thread_for_tab_frame.isRunning():

            self.store_thread_for_tab_frame.resume()
        elif not self.store_thread_for_tab_frame.isRunning():

            self.store_thread_for_tab_frame.start()

        if self.now_data_chart_widget is not None and self.now_data_chart_widget.data_fetcher_thread is not None and self.now_data_chart_widget.data_fetcher_thread.isRunning():
            self.now_data_chart_widget.data_fetcher_thread.resume()
        elif not self.now_data_chart_widget.data_fetcher_thread.isRunning():
            self.now_data_chart_widget.data_fetcher_thread.start()

        if self.detaildata_table is not None and self.detaildata_table.data_fetcher_thread is not None and self.detaildata_table.data_fetcher_thread.isRunning():
            self.detaildata_table.data_fetcher_thread.resume()
        elif not self.detaildata_table.data_fetcher_thread.isRunning():
            self.detaildata_table.data_fetcher_thread.start()

        self.stop_btn.setDisabled(False)
        self.start_btn.setDisabled(True)

    def stop_btn_func(self):
        """
        停止获取信息按钮功能
        :return:
        """
        if self.store_thread_for_tab_frame is not None and self.store_thread_for_tab_frame.isRunning():
            self.store_thread_for_tab_frame.pause()
        if self.now_data_chart_widget is not None and self.now_data_chart_widget.data_fetcher_thread is not None and self.now_data_chart_widget.data_fetcher_thread.isRunning():
            self.now_data_chart_widget.data_fetcher_thread.pause()
        if self.detaildata_table is not None and self.detaildata_table.data_fetcher_thread is not None and self.detaildata_table.data_fetcher_thread.isRunning():
            self.detaildata_table.data_fetcher_thread.pause()
        self.start_btn.setDisabled(False)
        self.stop_btn.setDisabled(True)

    def refresh_btn_func(self):
        """
        刷新全部信息按钮功能
        :return:
        """
        # 实例化发送查询报文线程
        self.refresh_btn.setDisabled(True)
        self.store_thread_for_tab_frame.stop()
        self.store_thread_for_tab_frame.terminate()
        self.store_thread_for_tab_frame = None
        self.store_thread_for_tab_frame = Store_thread_for_tab_frame(
            name=self.objectName(),
            type=self.type,
            show_data_signal=self.show_data_signal,
            tab_int=5
        )
        self.store_thread_for_tab_frame.mouse_cage_number = global_setting.get_setting("tab2_select_mouse_cage")
        self.store_thread_for_tab_frame.start()
        self.start_btn.setDisabled(True)
        self.stop_btn.setDisabled(False)
        self.refresh_btn.setDisabled(False)

    def update_send_data(self):
        # 更新mouse_cage_number
        logger.info(f"{self.objectName()}触发发送报文更新数据")
        # 更新表格的鼠笼号
        if self.detaildata_table is not None:
            self.detaildata_table.mouse_cage_number = global_setting.get_setting("tab2_select_mouse_cage")
            self.detaildata_table.table_name = f"{self.detaildata_table.type.value['name']}_{self.detaildata_table.data_type}_cage_{self.detaildata_table.mouse_cage_number}"
            if self.detaildata_table.data_fetcher_thread is not None:
                self.detaildata_table.data_fetcher_thread.table_name = self.detaildata_table.table_name
        # 更新图表的鼠笼号
        if self.now_data_chart_widget is not None:
            self.now_data_chart_widget.mouse_cage_number = global_setting.get_setting("tab2_select_mouse_cage")
            self.now_data_chart_widget.table_name = f"{self.now_data_chart_widget.type.value['name']}_{self.now_data_chart_widget.data_type}_cage_{self.now_data_chart_widget.mouse_cage_number}"
            if self.now_data_chart_widget.data_fetcher_thread is not None:
                self.now_data_chart_widget.data_fetcher_thread.table_name = self.now_data_chart_widget.table_name
        if self.store_thread_for_tab_frame is not None:
            self.store_thread_for_tab_frame.mouse_cage_number = global_setting.get_setting("tab2_select_mouse_cage")
        else:
            self.store_thread_for_tab_frame = Store_thread_for_tab_frame(
                name=self.objectName(),
                type=self.type,
                show_data_signal=self.show_data_signal,
                tab_int=5
            )
            self.store_thread_for_tab_frame.mouse_cage_number = global_setting.get_setting("tab2_select_mouse_cage")
            self.store_thread_for_tab_frame.start()
        pass

    def show_data(self, data: dict):
        # 显示数据
        #logger.info(f"{self.objectName()}显示数据：{data}")
        if data is not None and len(data) != 0 and 'data' in data and  len(data['data']) != 0:
            match data['function_code']:
                case 1:
                    self.show_data_by_function_code_1(data['data'])
                    pass
                case 2:
                    self.show_data_by_function_code_2(data['data'])
                    pass
                case 4:
                    self.show_data_by_function_code_4(data['data'])
                    pass
                case 17:  # 0x11的10进制就是17
                    self.show_data_by_function_code_17(data['data'])
                    pass
                case _:
                    pass
            pass

    def show_data_by_function_code_1(self, data):
        #  根据不同功能码显示数据
        function_group_box: QGroupBox = self.findChild(QGroupBox, 'function_01')
        function_layout: QVBoxLayout = self.findChild(QVBoxLayout, "function_01_layout")
        grid_layout: QGridLayout = self.findChild(QGridLayout, "function_01_gird_layout")
        if grid_layout is None:
            # 没有就新建
            # 创建一个 QScrollArea
            scroll_area = QScrollArea()
            scroll_area.setWidgetResizable(True)  # 允许调整大小
            # 创建 QGridLayout
            grid_layout = QGridLayout()
            grid_layout.setContentsMargins(10, 20, 10, 10)
            grid_layout.setObjectName(f"function_01_gird_layout")
            # 3列 n行
            cols = 3
            rows = math.ceil(len(data) / cols)
            for row in range(rows):
                for col in range(cols):
                    if row * cols + col < len(data):
                        content_frame = QWidget()
                        content_frame_layout = QHBoxLayout()

                        desc_label = QLabel()
                        desc_label.setText(data[row * cols + col]['desc'] + ":")
                        value_label = QLabel()
                        value_label.setText(f"<span style='color: green;'>打开</span>"
                                            if data[row * cols + col][
                                                   'value'] == 1 else f"<span style='color: red;'>关闭</span>")
                        content_frame_layout.addWidget(desc_label)
                        content_frame_layout.addWidget(value_label)
                        content_frame.setLayout(content_frame_layout)

                        grid_layout.addWidget(content_frame, row, col)

            # 设置 QGroupBox 的布局为 QGridLayout
            function_group_box.setLayout(grid_layout)
            scroll_area.setWidget(function_group_box)
            function_layout.addWidget(scroll_area)
        else:
            # 有就修改值
            labels_layout = function_group_box.findChildren(QHBoxLayout)
            if labels_layout is not None and len(labels_layout) != 0:
                i = 0
                for label_layout in labels_layout:
                    labels = label_layout.parent().findChildren(QLabel)
                    if labels is not None and len(labels) != 0:
                        labels[0].setText(data[i]['desc'] + ":")
                        labels[1].setText(f"<span style='color: green;'>打开</span>"
                                          if data[i]['value'] == 1 else f"<span style='color: red;'>关闭</span>")
                        pass
                    i += 1

            pass
        pass

    def show_data_by_function_code_2(self, data):
        #  根据不同功能码显示数据
        function_group_box: QGroupBox = self.findChild(QGroupBox, 'function_02')
        function_layout: QVBoxLayout = self.findChild(QVBoxLayout, "function_02_layout")
        grid_layout: QGridLayout = self.findChild(QGridLayout, "function_02_gird_layout")
        if grid_layout is None:
            # 没有就新建
            # 创建一个 QScrollArea
            scroll_area = QScrollArea()
            scroll_area.setWidgetResizable(True)  # 允许调整大小
            # 创建 QGridLayout
            grid_layout = QGridLayout()
            grid_layout.setContentsMargins(10, 20, 10, 10)
            grid_layout.setObjectName(f"function_02_gird_layout")
            # 3列 n行
            cols = 3
            rows = math.ceil(len(data) / cols)
            for row in range(rows):
                for col in range(cols):
                    if row * cols + col < len(data):
                        content_frame = QWidget()
                        content_frame_layout = QHBoxLayout()

                        desc_label = QLabel()
                        desc_label.setText(data[row * cols + col]['desc'] + ":")
                        value_label = QLabel()
                        value_label.setText(f"<span style='color: green;'>正常</span>"
                                            if data[row * cols + col][
                                                   'value'] == 1 else f"<span style='color: red;'>故障</span>")
                        content_frame_layout.addWidget(desc_label)
                        content_frame_layout.addWidget(value_label)
                        content_frame.setLayout(content_frame_layout)

                        grid_layout.addWidget(content_frame, row, col)

            # 设置 QGroupBox 的布局为 QGridLayout
            function_group_box.setLayout(grid_layout)
            scroll_area.setWidget(function_group_box)
            function_layout.addWidget(scroll_area)
        else:
            # 有就修改值
            labels_layout = function_group_box.findChildren(QHBoxLayout)
            if labels_layout is not None and len(labels_layout) != 0:
                i = 0
                for label_layout in labels_layout:
                    labels = label_layout.parent().findChildren(QLabel)
                    if labels is not None and len(labels) != 0:
                        labels[0].setText(data[i]['desc'] + ":")
                        labels[1].setText(f"<span style='color: green;'>正常</span>"
                                          if data[i]['value'] == 1 else f"<span style='color: red;'>故障</span>")
                        pass
                    i += 1

            pass
        pass

    def show_data_by_function_code_4(self, data):
        #  根据不同功能码显示数据
        function_group_box: QGroupBox = self.findChild(QGroupBox, 'function_04')
        function_layout: QVBoxLayout = self.findChild(QVBoxLayout, "function_04_layout")
        grid_layout: QGridLayout = self.findChild(QGridLayout, "function_04_gird_layout")
        if grid_layout is None:
            # 没有就新建
            # 创建一个 QScrollArea
            scroll_area = QScrollArea()
            scroll_area.setWidgetResizable(True)  # 允许调整大小
            # 创建 QGridLayout
            grid_layout = QGridLayout()
            grid_layout.setContentsMargins(10, 20, 10, 10)
            grid_layout.setObjectName(f"function_04_gird_layout")
            # 3列 n行
            cols = 3
            rows = math.ceil(len(data) / cols)
            for row in range(rows):
                for col in range(cols):
                    if row * cols + col < len(data):
                        content_frame = QWidget()
                        content_frame_layout = QHBoxLayout()

                        desc_label = QLabel()
                        desc_label.setText(f"<span style=''>{data[row * cols + col]['desc']}:</span>")
                        value_label = QLabel()
                        value_label.setText(
                            f"<span style='font-weight: bold;font-size:17px;'>{data[row * cols + col]['value']}</span>")

                        content_frame_layout.addWidget(desc_label)
                        content_frame_layout.addWidget(value_label)
                        content_frame.setLayout(content_frame_layout)

                        grid_layout.addWidget(content_frame, row, col)

            # 设置 QGroupBox 的布局为 QGridLayout
            function_group_box.setLayout(grid_layout)
            scroll_area.setWidget(function_group_box)
            function_layout.addWidget(scroll_area)
        else:
            # 有就修改值
            labels_layout = function_group_box.findChildren(QHBoxLayout)
            if labels_layout is not None and len(labels_layout) != 0:
                i = 0
                for label_layout in labels_layout:
                    labels = label_layout.parent().findChildren(QLabel)
                    if labels is not None and len(labels) != 0:
                        labels[0].setText(f"<span style=''>{data[i]['desc']}:</span>")
                        labels[1].setText(f"<span style='font-weight: bold;font-size:17px;'>{data[i]['value']}</span>")
                        pass
                    i += 1

            pass
        pass

    def show_data_by_function_code_17(self, data):
        #  根据不同功能码显示数据
        function_group_box: QGroupBox = self.findChild(QGroupBox, 'function_11')
        function_layout: QVBoxLayout = self.findChild(QVBoxLayout, "function_11_layout")
        grid_layout: QGridLayout = self.findChild(QGridLayout, "function_11_gird_layout")
        if grid_layout is None:
            # 没有就新建
            # 创建一个 QScrollArea
            scroll_area = QScrollArea()
            scroll_area.setWidgetResizable(True)  # 允许调整大小
            # 创建 QGridLayout
            grid_layout = QGridLayout()
            grid_layout.setContentsMargins(10, 20, 10, 10)
            grid_layout.setObjectName(f"function_11_gird_layout")
            # 3列 n行
            cols = 3
            rows = math.ceil(len(data) / cols)
            for row in range(rows):
                for col in range(cols):
                    if row * cols + col < len(data):
                        content_frame = QWidget()
                        content_frame_layout = QHBoxLayout()

                        desc_label = QLabel()
                        desc_label.setText(data[row * cols + col]['desc'] + ":")
                        value_label = QLabel()
                        value_label.setText(f"{data[row * cols + col]['value']}")

                        content_frame_layout.addWidget(desc_label)
                        content_frame_layout.addWidget(value_label)
                        content_frame.setLayout(content_frame_layout)

                        grid_layout.addWidget(content_frame, row, col)

            # 设置 QGroupBox 的布局为 QGridLayout
            function_group_box.setLayout(grid_layout)
            scroll_area.setWidget(function_group_box)
            function_layout.addWidget(scroll_area)
        else:
            # 有就修改值
            labels_layout = function_group_box.findChildren(QHBoxLayout)
            if labels_layout is not None and len(labels_layout) != 0:
                i = 0
                for label_layout in labels_layout:
                    labels = label_layout.parent().findChildren(QLabel)
                    if labels is not None and len(labels) != 0:
                        labels[0].setText(data[i]['desc'] + ":")
                        labels[1].setText(f"{data[i]['value']}")
                        pass
                    i += 1

            pass
        pass
        pass
