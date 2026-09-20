import sys
from collections import deque

import matplotlib
from PyQt6.QtWidgets import (QApplication, QDialog, QVBoxLayout,
                             QHBoxLayout, QLabel, QGridLayout, QWidget,
                             QScrollArea, QFrame, QPushButton, QListWidget,
                             QFileDialog, QMessageBox, QSizePolicy, QComboBox)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QScreen
from datetime import datetime
import os

from matplotlib.figure import Figure
matplotlib.use('Qt5Agg')
from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas

from public.entity.BaseWindow import BaseWindow

import sys
from PyQt6.QtWidgets import (QApplication, QDialog, QVBoxLayout,
                             QHBoxLayout, QLabel, QGridLayout, QWidget,
                             QScrollArea, QFrame, QPushButton, QListWidget,
                             QFileDialog, QMessageBox, QSizePolicy, QSplitter)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QScreen
from datetime import datetime
import os

#设置matplotlib支持中文和负号
matplotlib.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

from public.entity.BaseWindow import BaseWindow


class CalibrationChartWidget(QWidget):
    """标定图表区域"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.initUI()

        # 数据容量为50条
        self.max_data_points = 50
        self.data_o2_current = deque(maxlen=self.max_data_points)
        self.data_co2_current = deque(maxlen=self.max_data_points)
        self.data_pressure_current = deque(maxlen=self.max_data_points)

        self.current_chart_type = "O2当前数据"

    def initUI(self):
        """初始化UI"""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(5, 5, 5, 5)
        layout.setSpacing(10)

        # 上方控制栏
        control_layout = QHBoxLayout()
        control_layout.setSpacing(10)

        chart_label = QLabel("图表类型:")
        chart_label_font = QFont()
        chart_label_font.setPointSize(10)
        chart_label.setFont(chart_label_font)
        chart_label.setStyleSheet("color: #495057;")

        self.chart_combo = QComboBox()
        self.chart_combo.addItems(["O2当前数据", "CO2当前数据", "O2压力当前数据"])
        self.chart_combo.setMaximumWidth(150)
        self.chart_combo.currentTextChanged.connect(self.onChartTypeChanged)
        self.chart_combo.setStyleSheet("""
            QComboBox {
                padding: 5px;
                border: 1px solid #ddd;
                border-radius: 4px;
                background-color: white;
            }
            QComboBox:hover {
                border: 1px solid #999;
            }
        """)

        control_layout.addWidget(chart_label)
        control_layout.addWidget(self.chart_combo)
        control_layout.addStretch()

        layout.addLayout(control_layout)

        # 创建matplotlib图表
        self.figure = Figure(figsize=(5, 4), dpi=100)
        self.figure.patch.set_facecolor('white')
        self.canvas = FigureCanvas(self.figure)

        # 为canvas外层创建frame设置样式
        canvas_frame = QFrame()
        canvas_frame.setStyleSheet("""
            QFrame {
                border: 1px solid #dee2e6;
                border-radius: 4px;
                background-color: white;
            }
        """)
        canvas_layout = QVBoxLayout(canvas_frame)
        canvas_layout.setContentsMargins(0, 0, 0, 0)
        canvas_layout.addWidget(self.canvas)

        layout.addWidget(canvas_frame)

        # 初始化图表
        self.initChart()

    def initChart(self):
        """初始化图表"""
        self.figure.clear()
        self.ax = self.figure.add_subplot(111)
        self.ax.set_title("O2当前数据变化趋势", fontsize=12, fontweight='bold')
        self.ax.set_xlabel("数据点", fontsize=10)
        self.ax.set_ylabel("数值", fontsize=10)
        self.ax.grid(True, alpha=0.3, linestyle='--')
        self.ax.set_facecolor('#f8f9fa')

        # 绘制空线
        self.line, = self.ax.plot([], [], 'b-o', linewidth=2, markersize=4, label="数据")
        self.ax.legend(loc='upper left', fontsize=9)

        self.canvas.draw()

    def onChartTypeChanged(self, chart_type):
        """切换图表类型"""
        self.current_chart_type = chart_type
        self.updateChart()

    def _filter_valid_data(self, data_list):
        """过滤掉None值，返回有效数据和对应的索引"""
        valid_data = []
        valid_indices = []

        for idx, value in enumerate(data_list):
            if value is not None:
                try:
                    valid_data.append(float(value))
                    valid_indices.append(idx + 1)  # 从1开始计数
                except (TypeError, ValueError):
                    continue

        return valid_data, valid_indices

    def addDataPoint(self, data_type, value):
        """添加数据点"""
        if value is None:
            # 直接添加None，不过滤
            if data_type == "o2_current":
                self.data_o2_current.append(None)
            elif data_type == "co2_current":
                self.data_co2_current.append(None)
            elif data_type == "pressure_current":
                self.data_pressure_current.append(None)
            self.updateChart()
            return

        try:
            value = float(value)
        except (TypeError, ValueError):
            # 添加None而不是抛弃
            if data_type == "o2_current":
                self.data_o2_current.append(None)
            elif data_type == "co2_current":
                self.data_co2_current.append(None)
            elif data_type == "pressure_current":
                self.data_pressure_current.append(None)
            self.updateChart()
            return

        # 添加数据到对应的deque
        if data_type == "o2_current":
            self.data_o2_current.append(value)
        elif data_type == "co2_current":
            self.data_co2_current.append(value)
        elif data_type == "pressure_current":
            self.data_pressure_current.append(value)

        # 更新图表
        self.updateChart()

    def updateChart(self):
        """更新图表"""
        self.figure.clear()
        self.ax = self.figure.add_subplot(111)

        # 根据选择的图表类型获取数据
        if self.current_chart_type == "O2当前数据":
            raw_data = list(self.data_o2_current)
            ylabel = "O2 (%)"
            title = "O2当前数据变化趋势"
            color = '#2196f3'
        elif self.current_chart_type == "CO2当前数据":
            raw_data = list(self.data_co2_current)
            ylabel = "CO2 (ppm)"
            title = "CO2当前数据变化趋势"
            color = '#ff9800'
        else:  # O2压力当前数据
            raw_data = list(self.data_pressure_current)
            ylabel = "压力 (KPa)"
            title = "O2压力当前数据变化趋势"
            color = '#4caf50'

        # 过滤掉None值
        data, indices = self._filter_valid_data(raw_data)

        # 绘制折线图
        if data:
            self.line, = self.ax.plot(indices, data, color=color, marker='o', linewidth=2.5,
                                      markersize=5, label="数据", linestyle='-', alpha=0.8)

            # 设置图表属性
            self.ax.set_title(title, fontsize=12, fontweight='bold', pad=10)
            self.ax.set_xlabel("数据点序号", fontsize=10)
            self.ax.set_ylabel(ylabel, fontsize=10)
            self.ax.grid(True, alpha=0.3, linestyle='--', color='gray')
            self.ax.set_facecolor('#f8f9fa')

            # 设置x轴范围
            if len(data) > 0:
                min_idx = min(indices)
                max_idx = max(indices)
                range_padding = (max_idx - min_idx) * 0.1 if max_idx > min_idx else 1
                self.ax.set_xlim(min_idx - range_padding, max_idx + range_padding)

            # 添加数值标签到最后一个数据点 和第一个数据点
            if data:
                last_idx = indices[-1]
                last_val = data[-1]
                self.ax.annotate(f'{last_val:.2f}',
                                 xy=(last_idx, last_val),
                                 xytext=(5, 5),
                                 textcoords='offset points',
                                 fontsize=9,
                                 bbox=dict(boxstyle='round,pad=0.3',
                                           facecolor=color, alpha=0.2),
                                 arrowprops=dict(arrowstyle='->',
                                                 color=color, lw=1))
                first_idx = indices[0]
                first_val = data[0]
                self.ax.annotate(f'{first_val:.2f}',
                                 xy=(first_idx, first_val),
                                 xytext=(5, 5),
                                 textcoords='offset points',
                                 fontsize=9,
                                 bbox=dict(boxstyle='round,pad=0.3',
                                           facecolor=color, alpha=0.2),
                                 arrowprops=dict(arrowstyle='->',
                                                 color=color, lw=1))
            self.ax.legend(loc='upper left', fontsize=9)
        else:
            # 没有有效数据时显示提示
            self.ax.text(0.5, 0.5, '暂无有效数据',
                         ha='center', va='center',
                         transform=self.ax.transAxes,
                         fontsize=14, color='gray')
            self.ax.set_title(title, fontsize=12, fontweight='bold')
            self.ax.set_xlabel("数据点序号", fontsize=10)
            self.ax.set_ylabel(ylabel, fontsize=10)
            self.ax.grid(True, alpha=0.3, linestyle='--')
            self.ax.set_facecolor('#f8f9fa')

        # 调整布局
        self.figure.tight_layout()

        # 绘制
        self.canvas.draw()


class CalibrationDialog(QDialog):
    def __init__(self, parent=None, main_gui=None):
        super().__init__(parent)
        self.main_gui: BaseWindow = main_gui
        self.max_visible_logs = 0
        self.log_history = []
        self.log_sequence = 0
        self._suppress_next_log = False
        self._next_log_event_time = None
        self.initUI()
        self.setupData()

    def initUI(self):
        self.setWindowTitle("标定信息监控")
        self.setWindowFlags(Qt.WindowType.Window | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowModality(Qt.WindowModality.WindowModal)  # 窗口模态
        # 设置窗口大小
        self.resize(800, 800)  # 增加宽度以适应分割器

        # 主布局
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)

        # 创建水平分割器
        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setChildrenCollapsible(False)  # 防止子窗口被完全折叠

        # 设置分割器样式
        splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #d0d0d0;
                border: 1px solid #999;
                width: 8px;
                margin: 0px;
            }

            QSplitter::handle:hover {
                background-color: #a0a0a0;
            }
            QSplitter::handle:pressed {
                background-color: #707070;
            }
        """)

        # 左侧区域 - 状态、表格和图表
        left_widget = self.createLeftWidget()

        # 右侧区域 - 日志
        right_widget = self.createRightWidget()

        # 添加到分割器
        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)

        # 设置初始分割比例 (左侧:右侧 = 3:2)
        splitter.setSizes([600, 400])

        # 设置最小宽度
        splitter.setStretchFactor(0, 0)  # 左侧不拉伸
        splitter.setStretchFactor(1, 1)  # 右侧可拉伸

        # 添加分割器到主布局
        main_layout.addWidget(splitter)

        # 定位窗口到屏幕右边缘
        self.moveToRightEdge()

    def createLeftWidget(self):
        """创建左侧区域"""
        left_widget = QWidget()
        left_widget.setMinimumWidth(400)  # 设置最小宽度
        left_widget.setMaximumWidth(800)  # 设置最大宽度

        left_layout = QVBoxLayout(left_widget)
        left_layout.setContentsMargins(0, 0, 5, 0)  # 右侧留点间距给分割器
        left_layout.setSpacing(10)

        # 创建垂直分割器（用于分隔状态/表格和图表）
        vertical_splitter = QSplitter(Qt.Orientation.Vertical)
        vertical_splitter.setChildrenCollapsible(False)

        # 设置分割器样式
        vertical_splitter.setStyleSheet("""
            QSplitter::handle {
                background-color: #d0d0d0;
                border: 1px solid #999;
                height: 6px;
                margin: 0px;
            }

            QSplitter::handle:hover {
                background-color: #a0a0a0;
            }
            QSplitter::handle:pressed {
                background-color: #707070;
            }
        """)

        # 上部分 - 状态和表格区域
        top_widget = self.createStatusAndTableWidget()

        # 下部分 - 图表区域（使用scrollarea）
        chart_scroll_area = QScrollArea()
        chart_scroll_area.setWidgetResizable(True)
        chart_scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        chart_scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        chart_scroll_area.setStyleSheet("""
            QScrollArea {
                border: 1px solid #dee2e6;
                border-radius: 4px;
                background-color: white;
            }
            QScrollArea > QWidget > QWidget {
                background-color: white;
            }
        """)

        # 创建图表widget
        self.chart_widget = CalibrationChartWidget()
        chart_scroll_area.setWidget(self.chart_widget)

        # 添加到垂直分割器
        vertical_splitter.addWidget(top_widget)
        vertical_splitter.addWidget(chart_scroll_area)

        # 设置垂直分割比例 (上部:下部 = 2:1)
        vertical_splitter.setSizes([300, 200])

        left_layout.addWidget(vertical_splitter)

        return left_widget

    def createStatusAndTableWidget(self):
        """创建状态和表格区域"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        # 创建滚动区域
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        scroll_area.setStyleSheet("""
            QScrollArea {
                border: 1px solid #dee2e6;
                border-radius: 4px;
                background-color: white;
            }
        """)

        # 滚动区域内容
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)
        scroll_layout.setContentsMargins(5, 5, 5, 5)
        scroll_layout.setSpacing(15)

        # 状态显示区域
        self.createStatusArea(scroll_layout)

        # 创建表格布局
        self.createTable(scroll_layout)

        # 设置滚动区域
        scroll_area.setWidget(scroll_content)
        layout.addWidget(scroll_area)

        return widget

    def createRightWidget(self):
        """创建右侧区域"""
        right_widget = QWidget()
        right_widget.setMinimumWidth(300)  # 设置最小宽度

        right_layout = QVBoxLayout(right_widget)
        right_layout.setContentsMargins(5, 0, 0, 0)  # 左侧留点间距给分割器
        right_layout.setSpacing(10)

        # 日志标题和导出按钮区域
        log_header_layout = QHBoxLayout()
        log_header_layout.setSpacing(10)

        log_title = QLabel("标定校准日志")
        log_title_font = QFont()
        log_title_font.setPointSize(12)
        log_title_font.setBold(True)
        log_title.setFont(log_title_font)
        log_title.setStyleSheet("color: #495057;")

        self.export_btn = QPushButton("导出日志")
        self.export_btn.setMaximumWidth(100)
        self.export_btn.setStyleSheet("""
            QPushButton {
                background-color: #28a745;
                color: white;
                border: none;
                border-radius: 4px;
                padding: 8px 16px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #218838;
            }
            QPushButton:pressed {
                background-color: #1e7e34;
            }
        """)
        self.export_btn.clicked.connect(self.exportLogs)

        log_header_layout.addWidget(log_title)
        log_header_layout.addStretch()
        log_header_layout.addWidget(self.export_btn)

        # 日志列表
        self.log_list = QListWidget()
        self.log_list.setStyleSheet("""
            QListWidget {
                border: 1px solid #dee2e6;
                border-radius: 4px;
                background-color: white;
                alternate-background-color: #f8f9fa;
                selection-background-color: #007bff;
                font-family: 'Consolas', 'Monaco', monospace;
                font-size: 10pt;
            }
            QListWidget::item {
                padding: 5px;
                border-bottom: 1px solid #e9ecef;
            }
            QListWidget::item:selected {
                background-color: #007bff;
                color: white;
            }
        """)

        right_layout.addLayout(log_header_layout)
        right_layout.addWidget(self.log_list)

        # 初始化一些示例日志
        self.addLog("当前状态: 未标定", "STATUS")

        return right_widget

    def createStatusArea(self, parent_layout):
        """创建状态显示区域"""
        status_frame = QFrame()
        status_frame.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
        status_frame.setLineWidth(1)
        status_frame.setStyleSheet("""
            QFrame {
                background-color: #f8f9fa;
                border-radius: 5px;
                padding: 5px;
            }
        """)
        status_frame.setFixedHeight(80)
        status_layout = QHBoxLayout(status_frame)
        status_layout.setContentsMargins(10, 8, 10, 8)
        status_layout.setSpacing(10)

        # "当前状态："标签
        status_title = QLabel("当前状态：")
        status_title_font = QFont()
        status_title_font.setPointSize(12)
        status_title_font.setBold(True)
        status_title.setFont(status_title_font)
        status_title.setStyleSheet("color: #495057;")

        # 状态内容标签 - 默认为"未标定"
        self.status_label = QLabel("未标定")
        status_font = QFont()
        status_font.setPointSize(14)
        status_font.setBold(True)
        self.status_label.setFont(status_font)
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        # 未标定状态的样式（灰色）
        self.status_label.setStyleSheet("""
            QLabel {
                background-color: #f8f9fa;
                border: 2px solid #6c757d;
                border-radius: 5px;
                padding: 8px 15px;
                color: #495057;
                min-width: 100px;
            }
        """)

        # 设置大小策略
        status_title.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.status_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        status_layout.addWidget(status_title)
        status_layout.addWidget(self.status_label)
        status_layout.addStretch(1)  # 添加弹性空间

        parent_layout.addWidget(status_frame)

    def createTable(self, parent_layout):
        """创建表格布局"""
        # 创建表格容器
        table_frame = QFrame()
        table_frame.setFrameStyle(QFrame.Shape.Box | QFrame.Shadow.Raised)
        table_frame.setLineWidth(1)

        # 表格布局
        grid_layout = QGridLayout(table_frame)
        grid_layout.setSpacing(0)

        # 数据字体
        data_font = QFont()
        data_font.setPointSize(12)
        data_font.setBold(True)

        # 标签字体
        label_font = QFont()
        label_font.setPointSize(10)

        # 创建所有单元格
        self.cells = {}

        # 定义表格数据结构
        table_data = [
            ["", "当前数据", "Span标准气体数值", ""],
            ["O2（%）", "0.0000", "0.0000", ""],
            ["CO2（ppm）", "0.0000", "0.0000", ""],
            ["O2压力（KPa）", "0.000", "0.000", ""],
            ["零点标定开始时间", "Nan", "", ""],
            ["零点标定结束时间", "Nan", "", ""],
            ["量程标定开始时间", "Nan", "", ""],
            ["量程标定结束时间", "Nan", "", ""]
        ]

        for row in range(8):
            for col in range(4):
                # 创建标签
                label = QLabel(table_data[row][col])

                # 设置字体
                if row == 0:  # 表头
                    header_font = QFont()
                    header_font.setPointSize(11)
                    header_font.setBold(True)
                    label.setFont(header_font)
                elif col == 0:  # 第一列标签
                    label.setFont(label_font)
                elif col == 1 and row in [1, 2, 3]:  # 数据显示列
                    label.setFont(data_font)
                elif col == 2 and row in [1, 2, 3]:  # Span数值列
                    label.setFont(data_font)
                else:
                    label.setFont(label_font)

                # 设置对齐
                label.setAlignment(Qt.AlignmentFlag.AlignCenter)

                # 设置样式和边框
                style = self.getCellStyle(row, col)
                label.setStyleSheet(style)

                # 设置最小高度
                label.setMinimumHeight(35)

                # 添加到布局
                grid_layout.addWidget(label, row, col)

                # 保存引用以便后续更新
                self.cells[(row, col)] = label

        parent_layout.addWidget(table_frame)

    def getCellStyle(self, row, col):
        """获取单元格样式"""
        base_style = "QLabel { padding: 8px; "

        # 表头样式
        if row == 0:
            base_style += "background-color: #f5f5f5; font-weight: bold; "

        # 第一列标签样式
        if col == 0 and row > 0:
            base_style += "background-color: #fafafa; "

        # 数据显示列样式
        if col == 1 and row in [1, 2, 3]:
            base_style += "background-color: #e8f5e8; color: #2e7d32; "
        elif col == 2 and row in [1, 2, 3]:
            base_style += "background-color: #fff3e0; color: #f57c00; "

        # 边框设置 - 最后两列从第2行开始不要框线
        if row >= 1 and col >= 2:  # 从第2行开始的最后两列
            base_style += "border: none; "
        else:
            base_style += "border: 1px solid #ddd; "

        base_style += "}"
        return base_style

    def setupData(self):
        """设置数据字典以便更新"""
        self.data_cells = {
            'o2_current': (1, 1),
            'o2_span': (1, 2),
            'co2_current': (2, 1),
            'co2_span': (2, 2),
            'pressure_current': (3, 1),
            'pressure_span': (3, 2),
            'zero_start_time': (4, 1),
            'zero_end_time': (5, 1),
            'span_start_time': (6, 1),
            'span_end_time': (7, 1)
        }

    def moveToRightEdge(self):
        """将窗口移动到屏幕右边缘"""
        screen = QApplication.primaryScreen()
        screen_geometry = screen.availableGeometry()

        # 计算右边缘位置
        x = screen_geometry.width() - self.width() - 10  # 留10像素边距
        y = (screen_geometry.height() - self.height()) // 2  # 垂直居中

        self.move(x, y)

    # 日志相关方法
    def _parse_event_time(self, value):
        if isinstance(value, datetime):
            return value
        if value in (None, ""):
            return None

        text = str(value).strip()
        if text.startswith("["):
            closing_index = text.find("]")
            if closing_index != -1:
                text = text[1:closing_index].strip()
        elif " | " in text:
            text = text.split(" | ", 1)[0].strip()

        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(text, fmt)
            except ValueError:
                continue
        return None

    def addLog(self, message, log_type="INFO", has_time=False, event_time=None):
        """添加日志条目"""
        if self._suppress_next_log:
            self._suppress_next_log = False
            self._next_log_event_time = None
            return

        effective_event_time = event_time if event_time is not None else self._next_log_event_time
        self._next_log_event_time = None
        event_dt = self._parse_event_time(effective_event_time)
        if not has_time:
            if event_dt is None:
                event_dt = datetime.now()
            log_entry = f"[{event_dt.strftime('%Y-%m-%d %H:%M:%S')}] [{log_type}] {message}"
        else:
            log_entry = f"{message}"
            if event_dt is None:
                event_dt = self._parse_event_time(message)
        self.log_history.append({
            "display": log_entry,
            "event_time": event_dt,
            "sequence": self.log_sequence,
        })
        self.log_sequence += 1
        # 在最前面插入新日志
        self.log_list.insertItem(0, log_entry)

        # 限制日志条目数量（可选，防止内存占用过多）
        if self.max_visible_logs > 0 and self.log_list.count() > self.max_visible_logs:
            self.log_list.takeItem(self.log_list.count() - 1)

        # 自动滚动到顶部显示最新日志
        # self.log_list.setCurrentRow(0)

    def exportLogs(self):
        """导出日志到txt文件"""
        if not self.log_history:
            QMessageBox.information(self, "提示", "没有日志可以导出！")
            return

        # 获取保存文件路径
        default_filename = f"calibration_logs_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "导出日志文件",
            default_filename,
            "文本文件 (*.txt);;所有文件 (*)"
        )

        if file_path:
            try:
                with open(file_path, 'w', encoding='utf-8') as f:
                    f.write("标定系统操作日志\n")
                    f.write("=" * 50 + "\n")
                    f.write(f"导出时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                    f.write("=" * 50 + "\n\n")
                    sorted_logs = sorted(
                        self.log_history,
                        key=lambda item: (
                            item.get("event_time") is None,
                            item.get("event_time") or datetime.max,
                            item.get("sequence", 0),
                        ),
                    )
                    self.log_history = sorted_logs

                    # 按完整历史写入日志
                    for log_entry in self.log_history:
                        f.write(log_entry.get("display", "") + "\n")

                QMessageBox.information(self, "成功", f"日志已成功导出到:\n{file_path}")
                self.addLog(f"日志已导出到: {os.path.basename(file_path)}")

            except Exception as e:
                QMessageBox.critical(self, "错误", f"导出日志时发生错误:\n{str(e)}")
                self.addLog(f"导出日志失败: {str(e)}", "ERROR")

    # 公共接口方法
    def updateStatus(self, status_text, event_time=None, log_event=True):
        """更新状态文字"""
        old_status = self.status_label.text()
        self.status_label.setText(status_text)
        self._next_log_event_time = event_time
        self._suppress_next_log = not log_event

        # 添加日志
        self.addLog(f"状态变更: {old_status} -> {status_text}", "STATUS")

        # 根据状态改变颜色
        if "完成" in status_text:
            self.status_label.setStyleSheet("""
                QLabel {
                    background-color: #e8f5e9;
                    border: 2px solid #2e7d32;
                    border-radius: 5px;
                    padding: 8px 15px;
                    color: #1b5e20;
                    min-width: 100px;
                }
            """)
        elif "失败" in status_text:
            self.status_label.setStyleSheet("""
                QLabel {
                    background-color: #ffebee;
                    border: 2px solid #c62828;
                    border-radius: 5px;
                    padding: 8px 15px;
                    color: #b71c1c;
                    min-width: 100px;
                }
            """)
        elif "零点" in status_text:
            self.status_label.setStyleSheet("""
                QLabel {
                    background-color: #e3f2fd;
                    border: 2px solid #2196f3;
                    border-radius: 5px;
                    padding: 8px 15px;
                    color: #1976d2;
                    min-width: 100px;
                }
            """)
        elif "量程" in status_text:
            self.status_label.setStyleSheet("""
                QLabel {
                    background-color: #f3e5f5;
                    border: 2px solid #9c27b0;
                    border-radius: 5px;
                    padding: 8px 15px;
                    color: #7b1fa2;
                    min-width: 100px;
                }
            """)
        else:  # 未标定状态
            self.status_label.setStyleSheet("""
                QLabel {
                    background-color: #f8f9fa;
                    border: 2px solid #6c757d;
                    border-radius: 5px;
                    padding: 8px 15px;
                    color: #495057;
                    min-width: 100px;
                }
            """)

    def format_value(self, value, precision=4, none_text="NaN"):
        """格式化浮点数，None时返回指定文本"""
        if value is None:
            return none_text
        try:
            return f"{value:.{precision}f}"
        except (TypeError, ValueError):
            return none_text

    def updateO2Current(self, value, event_time=None, log_event=True):
        """更新O2当前数据"""
        formatted = self.format_value(value, 4)
        self.cells[self.data_cells['o2_current']].setText(formatted)
        self._next_log_event_time = event_time
        self._suppress_next_log = not log_event
        if value is not None:
            self.addLog(f"O2当前数据更新: {formatted}%")
        # 添加数据到图表（包括None值）
        if hasattr(self, 'chart_widget'):
            self.chart_widget.addDataPoint("o2_current", value)

    def updateO2Span(self, value, event_time=None, log_event=True):
        """更新O2 Span数值"""
        formatted = self.format_value(value, 4)
        self.cells[self.data_cells['o2_span']].setText(formatted)
        self._next_log_event_time = event_time
        self._suppress_next_log = not log_event
        if value is not None:
            self.addLog(f"O2 Span数值更新: {formatted}%")

    def updateCO2Current(self, value, event_time=None, log_event=True):
        """更新CO2当前数据"""
        formatted = self.format_value(value, 4)
        self.cells[self.data_cells['co2_current']].setText(formatted)
        self._next_log_event_time = event_time
        self._suppress_next_log = not log_event
        if value is not None:
            self.addLog(f"CO2当前数据更新: {formatted}ppm")
        # 添加数据到图表（包括None值）
        if hasattr(self, 'chart_widget'):
            self.chart_widget.addDataPoint("co2_current", value)

    def updateCO2Span(self, value, event_time=None, log_event=True):
        """更新CO2 Span数值"""
        formatted = self.format_value(value, 4)
        self.cells[self.data_cells['co2_span']].setText(formatted)
        self._next_log_event_time = event_time
        self._suppress_next_log = not log_event
        if value is not None:
            self.addLog(f"CO2 Span数值更新: {formatted}ppm")

    def updatePressureCurrent(self, value, event_time=None, log_event=True):
        """更新O2压力当前数据"""
        formatted = self.format_value(value, 3)
        self.cells[self.data_cells['pressure_current']].setText(formatted)
        self._next_log_event_time = event_time
        self._suppress_next_log = not log_event
        if value is not None:
            self.addLog(f"O2压力当前数据更新: {formatted} KPa")
        # 添加数据到图表（包括None值）
        if hasattr(self, 'chart_widget'):
            self.chart_widget.addDataPoint("pressure_current", value)

    def updatePressureSpan(self, value, event_time=None, log_event=True):
        """更新O2压力Span数值"""
        formatted = self.format_value(value, 3)
        self.cells[self.data_cells['pressure_span']].setText(formatted)
        self._next_log_event_time = event_time
        self._suppress_next_log = not log_event
        if value is not None:
            self.addLog(f"O2压力Span数值更新: {formatted} KPa")

    def updateZeroStartTime(self, time_str=None, event_time=None, log_event=True):
        """更新零点标定开始时间"""
        if time_str is None:
            time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cells[(4, 0)].setText("零点标定开始时间")
        self.cells[self.data_cells['zero_start_time']].setText(time_str)
        self._next_log_event_time = event_time or time_str
        self._suppress_next_log = not log_event
        self.addLog("零点标定开始", "CALIBRATION")

    def updateZeroEndTime(self, time_str=None, event_time=None, log_event=True):
        """更新零点标定结束时间"""
        if time_str is None:
            time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cells[(5, 0)].setText("零点标定结束时间")
        self.cells[self.data_cells['zero_end_time']].setText(time_str)
        self._next_log_event_time = event_time or time_str
        self._suppress_next_log = not log_event
        self.addLog("零点标定结束", "CALIBRATION")

    def updateAirStartTime(self, time_str=None, event_time=None, log_event=True):
        """更新 Air 空气校准开始时间。"""
        if time_str is None:
            time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cells[(4, 0)].setText("Air校准开始时间")
        self.cells[self.data_cells['zero_start_time']].setText(time_str)
        self._next_log_event_time = event_time or time_str
        self._suppress_next_log = not log_event
        self.addLog("Air空气校准开始", "CALIBRATION")

    def updateAirEndTime(self, time_str=None, event_time=None, log_event=True):
        """更新 Air 空气校准完成时间。"""
        if time_str is None:
            time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cells[(5, 0)].setText("Air校准完成时间")
        self.cells[self.data_cells['zero_end_time']].setText(time_str)
        self._next_log_event_time = event_time or time_str
        self._suppress_next_log = not log_event
        self.addLog("Air空气校准完成", "CALIBRATION")

    def updateSpanStartTime(self, time_str=None, event_time=None, log_event=True):
        """更新量程标定开始时间"""
        if time_str is None:
            time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cells[self.data_cells['span_start_time']].setText(time_str)
        self._next_log_event_time = event_time or time_str
        self._suppress_next_log = not log_event
        self.addLog("量程标定开始", "CALIBRATION")

    def updateSpanEndTime(self, time_str=None, event_time=None, log_event=True):
        """更新量程标定结束时间"""
        if time_str is None:
            time_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        self.cells[self.data_cells['span_end_time']].setText(time_str)
        self._next_log_event_time = event_time or time_str
        self._suppress_next_log = not log_event
        self.addLog("量程标定结束", "CALIBRATION")

    def updateAllData(self, data_dict):
        """批量更新数据"""
        for key, value in data_dict.items():
            if key in self.data_cells:
                if 'time' in key:
                    formatted = str(value) if value is not None else "NaN"
                elif 'pressure' in key:
                    formatted = self.format_value(value, 3)
                else:
                    formatted = self.format_value(value, 4)

                self.cells[self.data_cells[key]].setText(formatted)

        self.addLog("批量数据更新完成")

class MainWindow(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.initUI()
        self.calibration_dialog = None

        # 模拟数据更新定时器
        self.timer = QTimer()
        self.timer.timeout.connect(self.simulateDataUpdate)
        self.data_counter = 0
        self.current_status_index = 0  # 用于状态循环

    def initUI(self):
        self.setWindowTitle("主窗口")
        self.setGeometry(100, 100, 400, 300)

        # 创建中心部件
        layout = QVBoxLayout(self)

        # 打开标定窗口按钮
        open_btn = QPushButton("打开标定监控窗口")
        open_btn.clicked.connect(self.openCalibrationDialog)
        layout.addWidget(open_btn)

        # 模拟数据更新按钮
        simulate_btn = QPushButton("开始模拟数据更新")
        simulate_btn.clicked.connect(self.startSimulation)
        layout.addWidget(simulate_btn)

        # 停止模拟按钮
        stop_btn = QPushButton("停止模拟数据更新")
        stop_btn.clicked.connect(self.stopSimulation)
        layout.addWidget(stop_btn)

        # 状态切换按钮
        status_btn = QPushButton("切换标定状态")
        status_btn.clicked.connect(self.toggleStatus)
        layout.addWidget(status_btn)

        # 手动添加日志按钮
        log_btn = QPushButton("添加测试日志")
        log_btn.clicked.connect(self.addTestLog)
        layout.addWidget(log_btn)

        # 关闭按钮
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)

        layout.addStretch()

    def openCalibrationDialog(self):
        """打开标定对话框"""
        if self.calibration_dialog is None:
            self.calibration_dialog = CalibrationDialog(self)

        self.calibration_dialog.show()
        self.calibration_dialog.raise_()
        self.calibration_dialog.activateWindow()

    def startSimulation(self):
        """开始模拟数据更新"""
        if self.calibration_dialog:
            self.timer.start(2000)  # 每2秒更新一次
            self.calibration_dialog.addLog("开始模拟数据更新", "SYSTEM")

    def stopSimulation(self):
        """停止模拟数据更新"""
        self.timer.stop()
        if self.calibration_dialog:
            self.calibration_dialog.addLog("停止模拟数据更新", "SYSTEM")

    def toggleStatus(self):
        """切换标定状态"""
        if self.calibration_dialog:
            # 状态循环: 未标定 -> 零点标定 -> 量程标定 -> 未标定
            current_text = self.calibration_dialog.status_label.text()

            if current_text == "未标定":
                self.calibration_dialog.updateStatus("零点标定")
            elif current_text == "零点标定":
                self.calibration_dialog.updateStatus("量程标定")
            else:
                self.calibration_dialog.updateStatus("未标定")

    def addTestLog(self):
        """添加测试日志"""
        if self.calibration_dialog:
            import random
            test_messages = [
                "传感器自检完成",
                "气路切换成功",
                "数据采集正常",
                "温度补偿已应用",
                "压力补偿已应用",
                "标定参数已保存",
                "系统自检通过",
                "通信连接正常"
            ]
            message = random.choice(test_messages)
            log_type = random.choice(["INFO", "SUCCESS", "WARNING", "SYSTEM"])
            self.calibration_dialog.addLog(message, log_type)

    def simulateDataUpdate(self):
        """模拟数据更新"""
        if self.calibration_dialog:
            import random

            self.data_counter += 1

            # 模拟传感器数据
            o2_value = 20.95 + random.uniform(-0.1, 0.1)
            co2_value = 0.04 + random.uniform(-0.01, 0.01)
            pressure_value = 101.325 + random.uniform(-1, 1)

            # 更新当前数据（这里会自动记录日志）
            self.calibration_dialog.updateO2Current(o2_value)
            self.calibration_dialog.updateCO2Current(co2_value)
            self.calibration_dialog.updatePressureCurrent(pressure_value)

            # 每10秒更新一次标准气体数值
            if self.data_counter % 5 == 0:
                self.calibration_dialog.updateO2Span(20.95)
                self.calibration_dialog.updateCO2Span(0.04)
                self.calibration_dialog.updatePressureSpan(101.325)

            # 每20秒更新一次时间
            if self.data_counter % 10 == 0:
                self.calibration_dialog.updateZeroStartTime()

            if self.data_counter % 15 == 0:
                self.calibration_dialog.updateSpanEndTime()

    def closeEvent(self, event):
        """窗口关闭事件"""
        self.timer.stop()
        if self.calibration_dialog:
            self.calibration_dialog.close()
        event.accept()


def main():
    app = QApplication(sys.argv)

    # 设置应用程序样式
    app.setStyle('Fusion')

    window = MainWindow()
    window.show()

    sys.exit(app.exec())


if __name__ == '__main__':
    main()
