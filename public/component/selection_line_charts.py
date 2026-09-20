# 下拉选择框的折线图
import random
import sqlite3
import sys
import threading
import time
import traceback
from datetime import datetime

from PyQt6 import QtCore
from PyQt6.QtGui import QPainter, QColor, QBrush, QFont, QPen
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QComboBox, QLabel, QScrollArea, QApplication, QHBoxLayout, \
    QSpacerItem, QSizePolicy
from PyQt6.QtCharts import QChart, QChartView, QLineSeries, QValueAxis, QSplineSeries, QDateTimeAxis
from PyQt6.QtCore import QObject, pyqtSignal, pyqtSlot, Qt, QDateTime, QPointF
from loguru import logger

from public.config_class.global_setting import global_setting
from public.dao.SQLite.Monitor_Datas_Handle import Monitor_Datas_Handle
from public.dao.SQLite.SQliteManager import SQLiteManager
from public.entity.MyQThread import MyQThread
from theme.ThemeManager import Charts_Style_Name

#logger = logger.bind(category="gui_logger")
class DataFetcher(MyQThread):
    data_fetched = pyqtSignal(list)  # 信号传递时间和值

    def __init__(self, name, table_name, data_type, data_types):
        super().__init__(name=name)
        self.table_name = table_name
        # 选中的数据类型
        self.data_type = data_type
        # 所有的数据类型
        self.data_types = data_types
        # 数据库操作类
        self.handle: Monitor_Datas_Handle = None

    def stop(self):
        if self.handle is not None:
            self.handle.stop()
            self.handle=None
        super().stop()
        # if self.handle is not None:
        #     self.handle.stop()

    def pause(self):
        super().pause()
        # if self.handle is not None:
        #     self.handle.stop()

    def dosomething(self):
        # if self.handle is not None:
        #     self.handle.stop()
        if self.handle is None:
            self.handle = Monitor_Datas_Handle()  # # 创建数据库
        data =[]
        # 可能会有多个数据源
        """[
                {
                    'temperature':{
                    'desc':'温度',
                    'value':1
                    },
                    'time':{
                    'desc':'获取时间',
                    'value':1
                    },
                }
            ]
        """

        for data_type_temp in self.data_types:
            if self.data_type['name'] == data_type_temp['name']:
                data = self.handle.query_data_one_column_current(table_name=self.table_name,
                                                                 columns_flag=[data_type_temp['name'],
                                                                               SQLiteManager.TIME_COLUMN_NAME])
                break
        else:
            pass
        # logger.error(f"get_data:{data}")
        self.data_fetched.emit(data)

        time.sleep(1)  # 每秒获取一次数据


class LineChartWidget(QWidget):
    def __init__(self, parent: QVBoxLayout = None, object_name: str = "", data_read_counts=10, data_origin_nums=1,
                 is_span=False, type=None, data_type="", mouse_cage_number=0):
        """

        :param parent:父组件
        :param object_name:图表objectName
        :param data_read_counts:图表数据展示的总数量
        :param is_span:图表是否平滑
        :param data_origin_nums:数据源数量
        :param type:模块类型 UFC,UGC等 枚举类
        :param data_type: 数据类型 比如monitor_data senior_state等
        """
        super().__init__()
        self.mouse_cage_number = mouse_cage_number
        self.type = type
        self.data_type = data_type
        # [{'name':'','desc':''}]
        self.columns_desc_combobox_data = []
        # 选中
        self.columns_desc_combobox_selected={}
        if self.mouse_cage_number == 0:
            self.table_name = f"{self.type.value['name']}_{self.data_type}"
        else:
            self.table_name = f"{self.type.value['name']}_{self.data_type}_cage_{self.mouse_cage_number}"

        # 数据库操作类
        self.handle: Monitor_Datas_Handle = None
        # 数据源数量
        self.data_origin_nums = data_origin_nums
        self.data_points=[]
        for i in range(data_origin_nums):
            self.data_points.append([])   # 用于存储最新数据点
        # 主题颜色
        self.theme = global_setting.get_setting("theme_manager").get_charts_style()
        # 当前样式名
        self.theme_name = Charts_Style_Name.NORMAL.value
        # 是否平滑图表
        self.is_span = is_span

        # obejctName
        self.object_name = object_name
        self.setObjectName(self.object_name)
        # 父布局
        self.parent_layout = parent
        # 图表对象
        self.chart: QChart = None
        # 数据系列对象 可能有多个数据源 所以设置为列表
        self.series: [QLineSeries| QSplineSeries] = []
        # x轴
        self.x_axis: QValueAxis = None
        # y轴
        self.y_axis: QValueAxis = None

        # 图表每次读取的数据数量
        self.data_read_counts = data_read_counts

        # 数据的最大值 和最小值
        self.min_and_max_x = [0, 0]
        self.min_and_max_y = [0, 0]

        self.get_combobox_data()
        self._init_ui()

    def get_combobox_data(self):
        """
        获取combobox 下拉框数据
        :return:
        """
        self.handle = Monitor_Datas_Handle()  # # 创建数据库
        self.columns_desc_combobox_data = self.handle.query_meta_table_data(self.table_name)
        # 获取表格column数据

    def _init_ui(self):
        # 设置布局
        layout = QVBoxLayout(self)

        # 创建下拉框
        self.combo_box = QComboBox()
        if len(self.columns_desc_combobox_data) != 0:
            # 默认下拉项
            self.columns_desc_combobox_selected =self.columns_desc_combobox_data[0]
        self.combo_box.addItems([data['desc'] for data in self.columns_desc_combobox_data])
        self.combo_box.currentTextChanged.connect(self.change_data_type)

        # 添加下拉框到布局
        sub_layout = QHBoxLayout()
        sub_layout.addWidget(QLabel("选择数据："))
        sub_layout.addWidget(self.combo_box)
        sub_layout.addItem(QSpacerItem(20,40, QSizePolicy.Policy.Expanding,  QSizePolicy.Policy.Expanding))
        layout.addLayout(sub_layout)

        # 创建 QScrollArea
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)

        # 创建一个新的 QWidget 用于图表
        self.chart_widget = QWidget()

        self.chart_layout = QVBoxLayout(self.chart_widget)
        # 创建布局和图表视图
        self.chart_view = QChartView()
        self.chart_view.setMouseTracking(True)  # 开启鼠标追踪

        # self.chart_view.setFixedSize(500 + 200, 400)  # 固定大小

        self.chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)  # 关键设置 抗锯齿
        self.chart_view.setObjectName(f"{self.object_name}")

        # 初始化图表
        self._init_chart()

        # 将图表视图添加到 layout，并添加到滚动区域
        self.chart_layout.addWidget(self.chart_view)
        scroll_area.setWidget(self.chart_widget)
        sub_layout_2 = QVBoxLayout()
        # 将滚动区域添加到布局
        sub_layout_2.addWidget(scroll_area)
        layout.addLayout(sub_layout_2)

        # layout.addSpacerItem(QSpacerItem(40, 20, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding))
        #
        # layout.setStretch(6,2)
        scroll_area.ensureWidgetVisible(self.chart_view)
        self.setLayout(layout)
        self.parent_layout.addWidget(self)

    def _init_chart(self):
        theme = self.theme
        # 创建图表对象
        self.chart = QChart()


        self.chart.setObjectName(f"{self.object_name}_chart")
        # self.chart.setTitle("自定义图表")


        # 设置序列 和图表类型
        self._set_series()
        # 初始化数据获取线程
        self.data_fetcher_thread: DataFetcher = None

        self.change_data_type(self.combo_box.currentText())  # 初始化为默认数据类型
        self.get_max_and_min_data()
        # 设置坐标轴
        self._set_x_axis()
        self._set_y_axis()
        # 设置样式
        # self.set_style()
        # 添加到视图
        self.chart_view.setChart(self.chart)

    @pyqtSlot(str)
    def change_data_type(self, data_type):
        if self.data_fetcher_thread is not None and self.data_fetcher_thread.isRunning():
            self.data_fetcher_thread.stop()

        # self.series.clear()  # 清除现有数据
        for data in self.columns_desc_combobox_data:
            if data['desc'] == data_type:
                self.columns_desc_combobox_selected=data
                break
        # 数据的最大值 和最小值
        self.min_and_max_x = [0, 0]
        self.min_and_max_y = [0, 0]
        for i in range(self.data_origin_nums):
            self.series[i].clear()
            self.series[i].setName(f"{self.columns_desc_combobox_selected['desc']}")
        self.data_points.clear()  # 清除历史数据
        self.data_points=[]  # 清除历史数据
        for i in range(self.data_origin_nums):
            self.data_points.append([])
            # self.data_points.append([QPointF(random.randint(120000,130000),random.randint(1,10))  for i in range(random.randint(5,10))])  # 用于存储最新数据点
        # 启动新线程来获取新的数据类型
        self.data_fetcher_thread = DataFetcher(name="tab_2_tab_0_data_fetch_thread", table_name=self.table_name,
                                               data_type=self.columns_desc_combobox_selected,
                                               data_types=self.columns_desc_combobox_data)
        self.data_fetcher_thread.data_fetched.connect(self.update_chart)
        self.data_fetcher_thread.start()

    @pyqtSlot(list)
    def update_chart(self, data):
        """[
                        {
                            'temperature':{
                            'desc':'温度',
                            'value':1
                            },
                            'time':{
                            'desc':'获取时间',
                            'value':1
                            },
                        }
            ]
                """

        if len(data)>0 and len(data)==self.data_origin_nums and len(data[0])>0:
            for i in range(len(data)):
                if len(data[i])!=0:
                    # self.data_points[i].append(QPointF( int(datetime.strptime(data[i][SQLiteManager.TIME_COLUMN_NAME]['value'], "%Y-%m-%d %H:%M:%S").timestamp() ),
                    #                                     data[i][self.columns_desc_combobox_selected['name']]['value']
                    #                                     )

                    date_data = int(datetime.strptime(data[i][SQLiteManager.TIME_COLUMN_NAME]['value'],
                                              "%Y-%m-%d %H:%M:%S.%f").timestamp()* 1000)
                    point =QPointF(
                            date_data,
                            data[i][self.columns_desc_combobox_selected['name']]['value']
                        )
                    # 如果原来是空直接插入
                    if len(self.data_points[i])==0:
                        self.data_points[i].append(
                            point
                        )
                        self.series[i].append(
                            point
                        )

                    # 如果数据一样就不放进去 ，日期一样
                    elif len(self.data_points[i])!=0 and  self.data_points[i][len(self.data_points[i])-1] is not None and self.data_points[i][len(self.data_points[i])-1].x() !=date_data:
                        self.data_points[i].append(point)
                        self.series[i].append(
                            point
                        )

                    else:
                        return


        else:
            return
        try:
            for origin_data_index in range(len(self.data_points)):
                # 每个数据源需要遵守的规则
                if len(self.data_points[origin_data_index]) > self.data_read_counts and len(self.data_points[origin_data_index]) != 0:
                    # logger.info(f"图表{self.object_name}持数据源{origin_data_index}长度为{len(self.data[origin_data_index])}")
                    self.data_points[origin_data_index].pop(0)  # 保持数据长度为data_read_counts
                    self.series[origin_data_index].remove(0)
        except Exception as e:
            logger.error(
                f"图表{self.object_name}持数据长度为data_read_counts失败，series[0]数据长度{len(self.data_points[0])}，失败原因：{e} |  异常堆栈跟踪：{traceback.print_exc()}")
        logger.error(f"{self.objectName()}|update_data:{data}")
        # 获取 x 和y值的最大值和最小值来确定坐标轴范围
        logger.debug(f"{self.objectName()}|len()={len(self.data_points[0])}|{self.data_points}")
        self.get_max_and_min_data()
        # self.update_series()
        # 设置坐标轴
        self._set_x_axis()
        self._set_y_axis()
        # 更新样式
        # self.set_series_lenged_style()

    #   设置图表类型 line 折线图
    def _set_series(self):

        # 折线图
        # 添加数据系列n
        for i in range(self.data_origin_nums):
            if self.is_span:
                single_series = QSplineSeries()
            else:
                single_series = QLineSeries()
            single_series.setObjectName(f"{self.object_name}_series_{i + 1}")
            single_series.setName(f"{self.columns_desc_combobox_selected['desc']}")
            self.series.append(single_series)
            self.chart.addSeries(self.series[i])
        pass

    # 设置x轴
    def _set_x_axis(self):
        # 将坐标轴关联到图表
        if self.x_axis == None:
            self.x_axis = QDateTimeAxis()
            self.x_axis.setFormat("hh:mm:ss")  # 设置日期时间格式，可以根据需求调整
            # self.x_axis = QValueAxis()


        # 设置坐标轴范围
        # self.x_axis.setLabelsAngle(90)  # 旋转90°竖着显示日期
        self.x_axis.setRange(QDateTime.fromSecsSinceEpoch(int(self.min_and_max_x[0])),
                             QDateTime.fromSecsSinceEpoch(int(self.min_and_max_x[1])))
        # self.x_axis.setRange(int(self.min_and_max_x[0]),
        #                     int(self.min_and_max_x[1]))
        self.chart.removeAxis(self.x_axis)
        self.chart.addAxis(self.x_axis, Qt.AlignmentFlag.AlignBottom)
        for i in range(len(self.series)):
            self.series[i].attachAxis(self.x_axis)

        pass

    # 设置y轴
    def _set_y_axis(self):
        # 将坐标轴关联到图表
        if self.y_axis == None:
            self.y_axis = QValueAxis()
        # 设置坐标轴范围
        self.y_axis.setRange(self.min_and_max_y[0], self.min_and_max_y[1])
        # self.y_axis.setLabelFormat("%.3f")
        self.chart.removeAxis(self.y_axis)
        self.chart.addAxis(self.y_axis, Qt.AlignmentFlag.AlignLeft)
        for i in range(len(self.series)):
            self.series[i].attachAxis(self.y_axis)

        pass
    # 获取数据的最大x最小x最大y最小y
    def get_max_and_min_data(self):
        rows = len(self.data_points)
        # 如果没有数据 直接传0为最大最小值
        if rows == 0:
            self.min_and_max_x = [0, 0]
            self.min_and_max_y = [0, 0]
            return

        # 找一个初始值
        max_x = 0
        max_y = 0
        min_x = 0
        min_y = 0
        for row in range(rows):
            if len(self.data_points[0]) != 0:
                max_y = self.data_points[row][0].y()
                min_y = self.data_points[row][0].y()
                max_x = self.data_points[row][0].x()
                min_x = self.data_points[row][0].x()


        for row in range(rows):
            if len(self.data_points[row]) != 0:
                for column in range(len(self.data_points[row])):
                    max_y = max(max_y, self.data_points[row][column].y())
                    max_x = max(max_x, self.data_points[row][column].x())
                    min_y = min(min_y, self.data_points[row][column].y())
                    min_x = min(min_x, self.data_points[row][column].x())
        # 如果调整的数据的最大值和最小值都还是比之前的最大值和最小值小或大就不进行改变 大部分时间固定坐标轴 x轴不需要大部分时间固定
        # if min_x < self.min_and_max_x[0]:
        #     self.min_and_max_x[0] = min_x
        # if max_x > self.min_and_max_x[1]:
        #     self.min_and_max_x[1] = max_x
        self.min_and_max_x[0] = min_x/1000
        self.min_and_max_x[1] = max_x/1000
        if min_y < self.min_and_max_y[0]:
            self.min_and_max_y[0] = min_y-20
        if max_y > self.min_and_max_y[1]:
            self.min_and_max_y[1] = max_y+20


        logger.warning(
            f"{self.object_name}‘s data’s min&max x=[{self.min_and_max_x[0]},{self.min_and_max_x[1]}] y=[{self.min_and_max_y[0]},{self.min_and_max_y[1]}]")

# 设置标题字体和颜色
    def set_title_style(self, font_style: QFont = QFont("Arial", 8, QFont.Weight.Bold),
                        font_color: QColor = QColor("#2C3E50")):
        # 设置标题字体和颜色
        self.chart.setTitleFont(font_style)
        self.chart.setTitleBrush(QBrush(font_color))  # 深蓝色

    # 设置x坐标轴样式
    def set_x_axis_style(self, root_object_name: str = "", x_axis_title: str = "x",
                         title_font_style: QFont = QFont("微软雅黑", 6),
                         labels_color: QColor = QColor("#34495E"), grid_line_color: QColor = QColor("#BDC3C7")):
        _translate = QtCore.QCoreApplication.translate

        # self.x_axis.setTitleText(_translate(root_object_name, x_axis_title))
        self.x_axis.setTitleText(x_axis_title)
        self.x_axis.setTitleFont(title_font_style)

        self.x_axis.setLabelsColor(labels_color)  # 标签颜色
        self.x_axis.setGridLineColor(grid_line_color)  # 网格线颜色
        pass

    # 设置y坐标轴样式
    def set_y_axis_style(self, root_object_name: str = "", y_axis_title: str = "y",
                         title_font_style: QFont = QFont("微软雅黑", 6),
                         labels_color: QColor = QColor("#34495E"), grid_line_color: QColor = QColor("#BDC3C7")):
        _translate = QtCore.QCoreApplication.translate
        # self.y_axis.setTitleText(_translate(root_object_name, y_axis_title))
        self.y_axis.setTitleText(y_axis_title)
        self.y_axis.setTitleFont(title_font_style)
        self.y_axis.setLabelsColor(labels_color)  # 标签颜色
        self.y_axis.setGridLineColor(grid_line_color)  # 网格线颜色
        pass

    # 设置图例样式
    def set_legend_style(self, align=Qt.AlignmentFlag.AlignTop,
                         font_colors: [QColor] = [QColor("#121212")]):
        self.chart.legend().setVisible(True)
        self.chart.legend().setAlignment(align)
        # 将图例放置在图表内部右上角，偏移一点距离
        self.chart.legend().setPos(QPointF(self.chart.plotArea().right() - 150, self.chart.plotArea().top() + 10))

        markers = self.chart.legend().markers()
        for i in range(len(markers)):
            markers[i].setLabelBrush(QBrush(font_colors[i]))

    # 设置series样式
    def set_series_style(self, font_colors: [QColor] = [QColor("#121212")]):
        for i in range(self.data_origin_nums):
            self.series[i].setColor(font_colors[i])
            self.series[i].setPen(QPen(font_colors[i]))
            self.series[i].setBrush(QBrush(font_colors[i]))

    # 设置chart样式
    def set_chart_style(self, backgroud_color: QBrush = QBrush(QColor("#121212")),
                        plotarea_color: QBrush = QBrush(QColor("#121212")),
                        title_font_style: QFont = QFont("Arial", 8, QFont.Weight.Bold),
                        title_font_color: QColor = QColor("#ffffff")):
        self.chart.setPlotAreaBackgroundBrush(backgroud_color)
        self.chart.setBackgroundBrush(plotarea_color)
        self.set_title_style(font_color=title_font_color, font_style=title_font_style)

    # 设置series和legend样式
    def set_series_lenged_style(self):
        theme_manager = global_setting.get_setting("theme_manager")
        series_colors = theme_manager.get_neighbor_color(
            colorHex=self.theme[self.theme_name]['series']['series_color'], color_delta_start=-60, color_delta_end=60,
            color_nums=self.data_origin_nums)
        legend_font_colors = theme_manager.get_neighbor_color(
            colorHex=self.theme[self.theme_name]['legend']['legend_font_color'], color_delta_start=-60,
            color_delta_end=60,
            color_nums=self.data_origin_nums)

        self.set_series_style(font_colors=[QColor(color) for color in
                                           series_colors])
        self.set_legend_style(font_colors=[QColor(color) for color in
                                           legend_font_colors
                                           ])
        pass

    # 设置样式
    def set_style(self):
        self.theme = global_setting.get_setting("theme_manager").get_charts_style()
        self.set_chart_style(
            backgroud_color=QBrush(QColor(QColor(self.theme[self.theme_name]['chart']['chart_background_color']))),
            plotarea_color=QBrush(QColor(self.theme[self.theme_name]['chart']['plot_area_color'])),
            title_font_color=QColor(self.theme[self.theme_name]['chart']['title_font_color']),
            title_font_style=QFont("Arial", 8, QFont.Weight.Bold))
        self.set_series_lenged_style()
        self.set_x_axis_style(root_object_name=self.parent_layout.objectName(),
                              labels_color=QColor(self.theme[self.theme_name]['axis']['axis_label_color']),
                              grid_line_color=QColor(self.theme[self.theme_name]['axis']['axis_grid_line_color']))
        self.set_y_axis_style(root_object_name=self.parent_layout.objectName(),
                              labels_color=QColor(self.theme[self.theme_name]['axis']['axis_label_color']),
                              grid_line_color=QColor(self.theme[self.theme_name]['axis']['axis_grid_line_color']))

        pass
if __name__ == "__main__":
    app = QApplication(sys.argv)

    # 创建一个示例 SQLite 数据库和表格以进行演示
    conn = sqlite3.connect('data.db')
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sensor_data (
            datetime REAL,
            temperature REAL,
            humidity REAL
        )
    ''')
    # 插入一些示例数据
    for i in range(100):
        cursor.execute("INSERT INTO sensor_data (datetime, temperature, humidity) VALUES (?, ?, ?)",
                       (i, 20 + i * 0.1, 50 + i * 0.2))
    conn.commit()
    conn.close()

    main_window = QWidget()
    main_layout = QVBoxLayout(main_window)

    chart_widget = LineChartWidget()
    main_layout.addWidget(chart_widget)

    main_window.setLayout(main_layout)
    main_window.setWindowTitle("Line Chart with Scroll Area")
    main_window.resize(800, 600)
    main_window.show()

    sys.exit(app.exec())
