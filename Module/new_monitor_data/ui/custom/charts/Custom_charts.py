# advanced_chart_widget.py
import numpy as np
import sys
import threading
from typing import Dict, List, Tuple, Optional
from collections import deque
from PyQt6.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout,
                             QPushButton, QComboBox, QColorDialog,
                             QLabel, QApplication, QSlider, QSpinBox,
                             QDoubleSpinBox, QDialog, QFormLayout,
                             QCheckBox, QGroupBox, QListWidget, QListWidgetItem,
                             QAbstractItemView, QToolButton, QButtonGroup,
                             QFileDialog, QMessageBox, QInputDialog)
from PyQt6.QtCore import Qt, QTimer, QRect, QPoint, QDate, QDateTime, QSize, pyqtSignal
from PyQt6.QtGui import QIcon, QColor, QFont
import matplotlib
from loguru import logger

from Module.new_monitor_data.ui.custom.charts.canvas.chart_canvas import ChartCanvas
from Module.new_monitor_data.ui.custom.charts.convert_data import convert_data_to_cage_format
from Module.new_monitor_data.ui.custom.charts.dialog.axis_settings_dialog import AxisSettingsDialog
from Module.new_monitor_data.ui.custom.charts.dialog.chart_config_dialog import ChartConfigDialog
from Module.new_monitor_data.ui.custom.charts.dialog.legend_settings_dialog import LegendSettingsDialog
from Module.new_monitor_data.ui.custom.charts.dialog.series_settings_dialog import SeriesSettingsDialog
from Module.new_monitor_data.ui.custom.charts.dialog.series_visibility_dialog import SeriesVisibilityDialog
from public.config_class.global_setting import global_setting
from public.dao.SQLite.Monitor_Datas_Handle import Monitor_Datas_Handle
from public.entity.BaseWidget import BaseWidget
from public.entity.MyQThread import MyQThread
from theme.ThemeQt6 import ThemedWidget

matplotlib.use('Qt5Agg')

from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.dates import DateFormatter, AutoDateLocator

from datetime import datetime, date
import matplotlib.ticker as mticker
import json
import os

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['SimHei', 'DejaVu Sans']
plt.rcParams['axes.unicode_minus'] = False

class DataFetcher(MyQThread):
    data_fetched = pyqtSignal(dict)  # 信号传递值

    def __init__(self, name,gid,page_size):
        super().__init__(name=name)
        self.gid = gid
        self.page_size = page_size
        self.refresh_interval_ms = 3000
        self._state_lock = threading.Lock()
        self._last_signature = None
        # 数据库操作类
        self.handle: Monitor_Datas_Handle = None

    def stop(self):
        super().stop()

    def run(self):
        try:
            super().run()
        finally:
            if self.handle is not None:
                self.handle.stop()
                self.handle = None

    def request_refresh(self):
        self.mutex.lock()
        self.condition.wakeAll()
        self.mutex.unlock()

    def dosomething(self):
        if self.handle is None:
            self.handle = Monitor_Datas_Handle()
        try:
            datas = self.handle.query_epoch_data_all_tables_expect_text_column(
                gid=self.gid,
                page_size=self.page_size
            )
        except Exception as exc:
            logger.error(f"{self.name}图表数据加载失败: {exc}")
            self.mutex.lock()
            self.condition.wait(self.mutex, self.refresh_interval_ms)
            self.mutex.unlock()
            return
        if datas is None:
            datas = {}

        rows = datas.get("rows", [])
        signature = (
            datas.get("total_items", 0),
            tuple(row.get("id") for row in rows),
            tuple(datas.get("columns", [])),
        )
        with self._state_lock:
            changed = signature != self._last_signature
            self._last_signature = signature
        if changed:
            self.data_fetched.emit(datas)

        self.mutex.lock()
        self.condition.wait(self.mutex, self.refresh_interval_ms)
        self.mutex.unlock()

class AdvancedChartWidget(BaseWidget):
    """
    高级图表组件 - 支持完整的图表自定义和交互
    """

    THEMES = {
        "默认": {
            "bg_color": "#FFFFFF",
            "grid_color": "#CCCCCC",
            "text_color": "#000000",
            "line_colors": ["#1f77b4", "#ff7f0e", "#2ca02c", "#d62728", "#9467bd"]
        },
        "暗黑": {
            "bg_color": "#1E1E1E",
            "grid_color": "#3E3E3E",
            "text_color": "#FFFFFF",
            "line_colors": ["#00D9FF", "#FF6B9D", "#C3FF00", "#FFA500", "#00FF00"]
        },
        "科技蓝": {
            "bg_color": "#0A1929",
            "grid_color": "#1E3A5F",
            "text_color": "#B2BAC2",
            "line_colors": ["#00B4D8", "#90E0EF", "#CAF0F8", "#0077B6", "#023E8A"]
        },
        "温暖": {
            "bg_color": "#FFF8DC",
            "grid_color": "#FFE4B5",
            "text_color": "#8B4513",
            "line_colors": ["#FF6347", "#FF8C00", "#FFD700", "#FF1493", "#DC143C"]
        }
    }

    def __init__(self, parent=None, max_points: int = 100,gid=-1):
        super().__init__(parent)
        self.gid=gid
        # 获取数据线程
        self.data_fetcher_thread: DataFetcher = None
        self.max_points = max_points
        self.chart_type = "折线图"
        self.current_theme = "默认"
        # 添加已处理的X轴值集合（用于去重）
        self.processed_x_values = set()  # 跟踪当前窗口内已处理过的X轴值
        self.processed_x_order = deque()
        # 配置文件路径
        self.config_dir = os.path.expanduser(f"~/.{global_setting.get_setting('configer').get('basic').get('name')}/chart_configs")
        #self.config_dir = os.path.expanduser(f"~/.animal_box_app/chart_configs")
        self.default_config_file = os.path.join(self.config_dir, "default_monitor_data_charts_config.json")
        os.makedirs(self.config_dir, exist_ok=True)

        # 数据存储 - 使用鼠笼名称作为键
        self.cage_data: Dict[str, Dict[str, deque]] = {}  # {鼠笼名: {数据类型: deque}}
        self.cage_ids: Dict[str, int] = {}  # {鼠笼名: 鼠笼ID} - 用于跟踪鼠笼号
        self.next_cage_id = 0  # 下一个可用的鼠笼ID

        # X轴数据存储
        self.x_data = deque(maxlen=max_points)  # 存储X轴标签/值
        self.data_counter = 0  # 自动生成X轴数据的计数器
        self.visible_series: set = set()

        # 当前显示的数据类型
        self.current_data_type = ""
        self.available_data_types = set()

        # 初始化默认配置
        self._init_default_configs()

        # 尝试加载保存的默认配置
        self._load_saved_default_config()
        self.setMinimumWidth(1100)
        self.setMinimumHeight(300)
        self.init_ui()
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.setInterval(50)
        self._refresh_timer.timeout.connect(self.refresh_chart)
        self.apply_theme(self.current_theme)
        if self.data_fetcher_thread is None:
            self.data_fetcher_thread = DataFetcher(name=f"new_monitor_data_charts_mouse_cage_{self.gid}_data_fetch_thread", gid=self.gid,
                                                   page_size=self.max_points)
            self.data_fetcher_thread.data_fetched.connect(self.update_page)
        if not self.data_fetcher_thread.isRunning():
            self.data_fetcher_thread.start()

    def _init_default_configs(self):
        """初始化所有默认配置"""
        # X轴配置 - 默认为时间类型
        self.x_axis_config = {
            "label": "时间",
            "label_fontsize": 10,
            "label_color": "#000000",
            "data_type": "自动检测",
            "auto_ticks": True,
            "ticks_min": 0,
            "ticks_max": 86400,
            "ticks_step": 3600,
            "tick_labelsize": 9,
            "tick_color": "#000000",
            "visible": True,
            "markersize": 6.0  # 添加markersize
        }

        # Y轴配置
        self.y_axis_config = {
            "label": "数值",
            "label_fontsize": 10,
            "label_color": "#000000",
            "data_type": "自动检测",
            "auto_ticks": True,
            "ticks_min": 0,
            "ticks_max": 100,
            "ticks_step": 10,
            "tick_labelsize": 9,
            "tick_color": "#000000",
            "visible": True,
            "markersize": 6.0  # 添加markersize
        }

        # 图例配置
        self.legend_config = {
            "visible": True,
            "position": "upper left",
            "fontsize": 10,
            "bg_color": "#FFFFFF",
            "edge_color": "#000000",
            "framealpha": 0.9,
            "edgewidth": 1.0,
            "ncol": 1,
            "markersize": 6.0  # 添加markersize
        }

        # 鼠笼配置 - 使用鼠笼名称作为键
        self.cage_configs: Dict[str, Dict] = {}
    def _load_saved_default_config(self):
        """加载保存的默认配置"""
        if os.path.exists(self.default_config_file):
            try:
                with open(self.default_config_file, 'r', encoding='utf-8') as f:
                    config = json.load(f)
                    self.load_config(config)
            except Exception as e:
                logger.error(f"加载保存的默认配置失败: {e}")

    def init_ui(self):
        """初始化UI"""
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(5, 5, 5, 5)
        main_layout.setSpacing(5)

        # 单行紧凑工具栏
        toolbar = self._create_compact_toolbar()
        main_layout.addLayout(toolbar)

        # 创建Matplotlib图表
        self.figure = Figure(figsize=(10, 6), dpi=100)
        self.figure.subplots_adjust(left=0.08, right=0.98, top=0.92, bottom=0.14)
        self.ax = self.figure.add_subplot(111)
        self._setup_chart()
        self.canvas = ChartCanvas(self.figure, ax=self.ax,parent=self)




        main_layout.addWidget(self.canvas)

    def _create_compact_toolbar(self) -> QHBoxLayout:
        """创建紧凑的单行工具栏"""
        toolbar = QHBoxLayout()
        toolbar.setSpacing(5)

        # 图表类型下拉框
        toolbar.addWidget(QLabel("图表:"))
        self.chart_combo = QComboBox()
        self.chart_combo.addItems(["折线图", "柱状图", "散点图", "面积图", "混合图"])
        self.chart_combo.currentTextChanged.connect(self.change_chart_type)
        toolbar.addWidget(self.chart_combo)

        toolbar.addWidget(self._create_separator())

        # 主题选择
        toolbar.addWidget(QLabel("主题:"))
        self.theme_combo = QComboBox()
        self.theme_combo.addItems(list(self.THEMES.keys()))
        self.theme_combo.currentTextChanged.connect(self.apply_theme)
        toolbar.addWidget(self.theme_combo)

        toolbar.addWidget(self._create_separator())

        # 数据类型选择
        toolbar.addWidget(QLabel("数据:"))
        self.data_type_combo = QComboBox()
        self.data_type_combo.currentTextChanged.connect(self.change_data_type)
        toolbar.addWidget(self.data_type_combo)

        toolbar.addWidget(self._create_separator())

        # 设置按钮
        settings_buttons = [
            ("图例", self.settings_legend),
            ("X轴", self.settings_x_axis),
            ("Y轴", self.settings_y_axis),
        ]

        for text, handler in settings_buttons:
            btn = QPushButton(text)
            btn.clicked.connect(handler)
            toolbar.addWidget(btn)

        toolbar.addWidget(self._create_separator())

        # 鼠笼操作按钮
        cage_buttons = [
            ("编辑", self.edit_cage),
            ("显示", self.show_visibility_dialog),
        ]

        for text, handler in cage_buttons:
            btn = QPushButton(text)
            btn.clicked.connect(handler)
            toolbar.addWidget(btn)

        toolbar.addWidget(self._create_separator())

        # 线宽控制
        toolbar.addWidget(QLabel("线宽:"))
        self.global_width_spin = QDoubleSpinBox()
        self.global_width_spin.setValue(2.0)
        self.global_width_spin.setRange(0.5, 10)
        self.global_width_spin.setSingleStep(0.5)
        self.global_width_spin.setDecimals(1)
        self.global_width_spin.valueChanged.connect(self.refresh_chart)
        toolbar.addWidget(self.global_width_spin)

        toolbar.addWidget(self._create_separator())

        # 标记大小控制（新增）
        toolbar.addWidget(QLabel("标记:"))
        self.global_markersize_spin = QDoubleSpinBox()
        self.global_markersize_spin.setValue(6.0)
        self.global_markersize_spin.setRange(2, 20)
        self.global_markersize_spin.setSingleStep(1)
        self.global_markersize_spin.setDecimals(1)
        self.global_markersize_spin.valueChanged.connect(self.refresh_chart)
        toolbar.addWidget(self.global_markersize_spin)

        toolbar.addWidget(self._create_separator())

        # 配置管理按钮
        config_btn = QPushButton("配置")
        config_btn.clicked.connect(self.show_config_dialog)
        toolbar.addWidget(config_btn)

        # 清空按钮
        clear_btn = QPushButton("清空")
        clear_btn.clicked.connect(self.clear_data)
        toolbar.addWidget(clear_btn)

        # 弹性空间
        toolbar.addStretch()

        return toolbar

    def _create_separator(self):
        """创建分隔线"""
        line = QLabel("|")
        line.setStyleSheet("color: #CCCCCC;")
        return line

    def _setup_chart(self):
        """设置图表基本属性"""
        self.ax.clear()
        self.ax.set_xlabel('数据点', fontsize=10)
        self.ax.set_xticks([])
        self.ax.set_yticks([])


        self.ax.set_ylabel('数值', fontsize=10)

        self.ax.set_title('实时数据图表', fontsize=12, fontweight='bold')
        self.ax.grid(True, alpha=0.3)

    def add_batch_data_with_deduplication(
            self,
            data_dict: Dict,
            refresh: bool = True,
            update_combo: bool = True
    ):
        """批量添加数据，自动去除重复部分，使用内部追踪"""

        # 检查是否为完整格式
        x_value = None
        if "x_value" in data_dict:
            x_value = data_dict["x_value"]
            cage_dict = data_dict.get("cages", {})
        else:
            # 简单格式，直接使用data_dict
            cage_dict = data_dict

        # 关键：检查此X值是否已处理过
        if x_value is not None:
            if x_value in self.processed_x_values:
                # logger.debug(f"批次数据已处理过，跳过此批次: x_value={x_value}")
                return False

            # 标记此X值已处理
            self.processed_x_values.add(x_value)
            self.processed_x_order.append(x_value)
            while len(self.processed_x_order) > self.max_points:
                expired = self.processed_x_order.popleft()
                self.processed_x_values.discard(expired)

        # 添加所有鼠笼的数据
        for cage_name, cage_data_dict in cage_dict.items():
            for data_type, value in cage_data_dict.items():
                # 直接添加数据，不再做重复检查
                if cage_name not in self.cage_data:
                    self.add_cage(cage_name)

                if data_type not in self.cage_data[cage_name]:
                    self.cage_data[cage_name][data_type] = deque(maxlen=self.max_points)

                self.cage_data[cage_name][data_type].append(value)
                self.available_data_types.add(data_type)

        # 更新X轴数据
        if x_value is not None:
            self.x_data.append(x_value)
        else:
            # 自动生成X轴数据
            current_len = max((len(data_q) for cage_data in self.cage_data.values()
                               for data_q in cage_data.values()), default=0)
            while len(self.x_data) < current_len:
                self.x_data.append(self.data_counter)
                self.data_counter += 1

        if update_combo:
            self._update_data_type_combo()
        if refresh:
            self._schedule_refresh()
        return True

    def _schedule_refresh(self):
        self._refresh_timer.start()
    def add_cage(self, cage_name: str) -> str:
        """添加鼠笼

        Args:
            cage_name: 鼠笼名称 (如果为空，则自动生成)

        Returns:
            实际的鼠笼名称
        """
        # 如果名称为空，自动生成
        if not cage_name or cage_name.strip() == "":
            cage_name = f"鼠笼{self.next_cage_id}"

        if cage_name not in self.cage_data:
            self.cage_data[cage_name] = {}
            self.cage_ids[cage_name] = self.next_cage_id
            self.next_cage_id += 1

            # 获取主题颜色
            theme = self.THEMES[self.current_theme]
            color_index = len(self.cage_configs) % len(theme["line_colors"])
            color = theme["line_colors"][color_index]

            # 保存鼠笼配置
            self.cage_configs[cage_name] = {
                "color": color,
                "linewidth": 2.0,
                "markersize": 6.0,
                "alpha": 1.0,
                "marker": "o"
            }

            self.visible_series.add(cage_name)

        return cage_name

    def add_cage_data(self, cage_name: str, data_type: str, value: float, x_value: Optional[float] = None):
        """为指定鼠笼添加指定类型的数据

        Args:
            cage_name: 鼠笼名称
            data_type: 数据类型
            value: 数据值
            x_value: X轴数据 (可选，如果不提供则自动生成)
        """

        # 如果鼠笼不存在，先创建
        cage_name = self.add_cage(cage_name)

        # 如果数据类型不存在，先创建
        if data_type not in self.cage_data[cage_name]:
            self.cage_data[cage_name][data_type] = deque(maxlen=self.max_points)

        # ==================== 关键改动：检查是否重复 ====================
        # 如果提供了x_value，检查是否已存在
        if x_value is not None:
            # 检查X轴数据是否已存在
            if x_value in self.x_data:
                # X轴数据已存在，跳过此条数据
                logger.debug(f"数据已存在，跳过: {cage_name} - {data_type} - {x_value}")
                return

        # 添加数据点
        self.cage_data[cage_name][data_type].append(value)

        # 更新可用数据类型
        self.available_data_types.add(data_type)
        self._update_data_type_combo()

        # 更新X轴数据
        if x_value is not None:
            # 使用提供的X轴数据
            self.x_data.append(x_value)
        else:
            # 自动生成X轴数据
            current_len = max((len(data_q) for cage_data in self.cage_data.values()
                               for data_q in cage_data.values()), default=0)
            while len(self.x_data) < current_len:
                self.x_data.append(self.data_counter)
                self.data_counter += 1

    def _update_data_type_combo(self):
        """更新数据类型下拉框"""
        current_text = self.data_type_combo.currentText()
        self.data_type_combo.blockSignals(True)
        self.data_type_combo.clear()

        sorted_types = sorted(self.available_data_types)
        self.data_type_combo.addItems(sorted_types)

        if current_text in sorted_types:
            self.data_type_combo.setCurrentText(current_text)
        elif sorted_types:
            self.data_type_combo.setCurrentText(sorted_types[0])
            self.current_data_type = sorted_types[0]
        self.data_type_combo.setSizeAdjustPolicy(QComboBox.SizeAdjustPolicy.AdjustToContents)
        self.data_type_combo.blockSignals(False)

    def change_data_type(self, data_type: str):
        """切换显示的数据类型"""
        if data_type and data_type != self.current_data_type:
            self.current_data_type = data_type
            self.refresh_chart()

    def add_batch_data(self, data_dict: Dict):
        """批量添加数据

        支持两种格式：

        1. 简单格式（自动生成X轴）:
           {
               "鼠笼1": {
                   "温度": 25.5,
                   "湿度": 60.0
               },
               "鼠笼2": {
                   "温度": 24.5,
                   "湿度": 62.0
               }
           }

        2. 完整格式（指定X轴数据）:
           {
               "x_value": "2024-01-01 10:00",  # 可选：X轴数据（时间、日期等）
               "cages": {
                   "鼠笼1": {
                       "温度": 25.5,
                       "湿度": 60.0
                   },
                   "鼠笼2": {
                       "温度": 24.5,
                       "湿度": 62.0
                   }
               }
           }
        """
        # 检查是否为完整格式
        x_value = None
        if "x_value" in data_dict:
            x_value = data_dict["x_value"]
            cage_dict = data_dict.get("cages", {})
        else:
            # 简单格式，直接使用data_dict
            cage_dict = data_dict

        # 添加所有鼠笼的数据
        for cage_name, cage_data_dict in cage_dict.items():
            for data_type, value in cage_data_dict.items():
                self.add_cage_data(cage_name, data_type, value, x_value)

        self.refresh_chart()

    def edit_cage(self):
        """编辑选中的鼠笼 - 从显示对话框中选择"""
        if not self.cage_data:
            QMessageBox.warning(self, "提示", "没有可编辑的鼠笼")
            return

        try:
            # 使用单选模式的可见性对话框选择要编辑的鼠笼
            dialog = SeriesVisibilityDialog(
                list(self.cage_data.keys()),
                self.visible_series,
                self,
                multi_select=False,
                title="选择要编辑的鼠笼"
            )

            if dialog.exec() == QDialog.DialogCode.Accepted:
                selected_cage = dialog.get_selected_series()
                if selected_cage and selected_cage in self.cage_configs:
                    # 打开该鼠笼的设置对话框
                    settings_dialog = SeriesSettingsDialog(
                        selected_cage,
                        self.cage_configs[selected_cage],
                        self
                    )

                    if settings_dialog.exec() == QDialog.DialogCode.Accepted:
                        settings = settings_dialog.get_settings()
                        self.cage_configs[selected_cage].update(settings)
                        self.refresh_chart()
        except Exception as e:
            logger.error(f"编辑鼠笼出错: {e}")
            QMessageBox.warning(self, "错误", f"编辑出错: {str(e)}")

    def show_visibility_dialog(self):
        """显示鼠笼可见性对话框"""
        if not self.cage_data:
            QMessageBox.warning(self, "提示", "没有可显示的鼠笼")
            return

        try:
            dialog = SeriesVisibilityDialog(
                list(self.cage_data.keys()),
                self.visible_series,
                self,
                multi_select=True,
                title="选择显示的数据系列"
            )

            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.visible_series = dialog.get_visible_series()
                self.refresh_chart()
        except Exception as e:
            logger.error(f"鼠笼可见性对话框出错: {e}")
            QMessageBox.warning(self, "错误", f"操作失败: {str(e)}")

    def change_chart_type(self, chart_type: str):
        """切换图表类型"""
        self.chart_type = chart_type
        self.refresh_chart()

    def apply_theme(self, theme_name: str):
        """应用主题"""
        if theme_name not in self.THEMES:
            return

        self.current_theme = theme_name
        theme = self.THEMES[theme_name]

        self.figure.patch.set_facecolor(theme["bg_color"])
        self.ax.set_facecolor(theme["bg_color"])

        self.ax.title.set_color(theme["text_color"])
        self.ax.xaxis.label.set_color(theme["text_color"])
        self.ax.yaxis.label.set_color(theme["text_color"])
        self.ax.tick_params(colors=theme["text_color"])

        self.ax.grid(True, alpha=0.3, color=theme["grid_color"])

        for spine in self.ax.spines.values():
            spine.set_edgecolor(theme["text_color"])

        self.refresh_chart()

    def settings_legend(self):
        """图例设置"""
        try:
            dialog = LegendSettingsDialog(self.legend_config, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.legend_config.update(dialog.get_settings())
                self.refresh_chart()
        except Exception as e:
            logger.error(f"图例设置出错: {e}")

    def settings_x_axis(self):
        """X轴设置"""
        try:
            dialog = AxisSettingsDialog("X", self.x_axis_config, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.x_axis_config.update(dialog.get_settings())
                self.refresh_chart()
        except Exception as e:
            logger.error(f"X轴设置出错: {e}")

    def settings_y_axis(self):
        """Y轴设置"""
        try:
            dialog = AxisSettingsDialog("Y", self.y_axis_config, self)
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.y_axis_config.update(dialog.get_settings())
                self.refresh_chart()
        except Exception as e:
            logger.error(f"Y轴设置出错: {e}")

    def show_config_dialog(self):
        """显示配置管理对话框"""
        dialog = ChartConfigDialog(self, self)
        dialog.exec()

    def get_all_config(self) -> Dict:
        """获取所有配置"""
        return {
            "chart_type": self.chart_type,
            "current_theme": self.current_theme,
            "x_axis_config": self.x_axis_config,
            "y_axis_config": self.y_axis_config,
            "legend_config": self.legend_config,
            "cage_configs": self.cage_configs,
            "cage_ids": self.cage_ids,
            "next_cage_id": self.next_cage_id,
            "global_width": self.global_width_spin.value(),
            "global_markersize": self.global_width_spin.value() if hasattr(self, 'global_markersize_spin') else 6.0
        }

    def load_config(self, config: Dict):
        """加载配置"""
        try:
            if "chart_type" in config:
                self.chart_type = config["chart_type"]
                self.chart_combo.blockSignals(True)
                self.chart_combo.setCurrentText(self.chart_type)
                self.chart_combo.blockSignals(False)

            if "current_theme" in config:
                self.current_theme = config["current_theme"]
                self.theme_combo.blockSignals(True)
                self.theme_combo.setCurrentText(self.current_theme)
                self.theme_combo.blockSignals(False)

            if "x_axis_config" in config:
                self.x_axis_config.update(config["x_axis_config"])

            if "y_axis_config" in config:
                self.y_axis_config.update(config["y_axis_config"])

            if "legend_config" in config:
                self.legend_config.update(config["legend_config"])

            if "cage_configs" in config:
                self.cage_configs.update(config["cage_configs"])

            # 恢复鼠笼ID信息
            if "cage_ids" in config:
                self.cage_ids.update(config["cage_ids"])
            if "next_cage_id" in config:
                self.next_cage_id = config["next_cage_id"]

            if "global_width" in config:
                self.global_width_spin.blockSignals(True)
                self.global_width_spin.setValue(config["global_width"])
                self.global_width_spin.blockSignals(False)

        except Exception as e:
            logger.error(f"加载配置失败: {e}")

    def save_default_config(self, config: Dict):
        """保存为默认配置"""
        try:
            with open(self.default_config_file, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            raise Exception(f"保存默认配置失败: {str(e)}")

    def export_config(self, config: Dict, file_path: str):
        """导出配置到文件"""
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
        except Exception as e:
            raise Exception(f"导出配置失败: {str(e)}")

    def import_config(self, file_path: str) -> Dict:
        """从文件导入配置"""
        try:
            with open(file_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            return config
        except Exception as e:
            raise Exception(f"导入配置失败: {str(e)}")

    def restore_default_config(self):
        """恢复系统默认配置"""
        self._init_default_configs()
        self.cage_ids.clear()
        self.next_cage_id = 0
        if os.path.exists(self.default_config_file):
            os.remove(self.default_config_file)

    def _get_data_type(self, data: list) -> str:
        """自动检测数据类型"""
        if not data:
            return "浮点数"

        # 过滤None值
        valid_data = [v for v in data if v is not None]
        if not valid_data:
            return "浮点数"

        first_val = valid_data[0]

        # 检查是否为时间格式
        if isinstance(first_val, str):
            try:
                # 尝试解析完整的日期时间格式 (YYYY-MM-DD HH:MM:SS 或类似格式)
                import re
                # 匹配各种日期时间格式
                datetime_patterns = [
                    r'\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}',  # 2024-01-01 12:30:45
                    r'\d{4}/\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2}',  # 2024/01/01 12:30:45
                    r'\d{2}-\d{2}-\d{4}\s+\d{2}:\d{2}:\d{2}',  # 01-01-2024 12:30:45
                    r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}',  # 2024-01-01T12:30:45 (ISO格式)
                ]

                for pattern in datetime_patterns:
                    if re.match(pattern, first_val.strip()):
                        return "日期时间"

                # 检查是否为纯时间格式 (HH:MM:SS 或 MM:SS)
                if ':' in first_val:
                    parts = first_val.split(':')
                    if len(parts) == 2 or len(parts) == 3:
                        # 检查是否都是数字
                        if all(p.isdigit() for p in parts):
                            return "时间"
            except:
                pass
            return "浮点数"

        # 检查是否为数字类型
        try:
            # 检查是否全为整数
            all_int = all(
                isinstance(v, int) or
                (isinstance(v, float) and v.is_integer() and abs(v) < 1e10)
                for v in valid_data
            )
            if all_int:
                return "整数"
            else:
                return "浮点数"
        except:
            return "浮点数"

    def _convert_time_to_seconds(self, time_str: str) -> float:
        """将时间字符串 (HH:MM:SS 或 MM:SS) 转换为秒数"""
        try:
            parts = time_str.strip().split(':')

            if len(parts) == 3:  # HH:MM:SS
                hours = int(parts[0])
                minutes = int(parts[1])
                seconds = int(parts[2])
                return hours * 3600 + minutes * 60 + seconds
            elif len(parts) == 2:  # MM:SS
                minutes = int(parts[0])
                seconds = int(parts[1])
                return minutes * 60 + seconds
            else:
                return 0
        except:
            return 0

    def _get_data_range(self, data: list, data_type: str) -> Tuple[float, float]:
        """获取数据的最小值和最大值"""
        if not data:
            return 0, 100

        # 过滤None值
        valid_data = [v for v in data if v is not None]
        if not valid_data:
            return 0, 100

        try:
            if data_type == "日期时间":
                # 将日期时间字符串转换为时间戳
                timestamp_values = []
                for v in valid_data:
                    if isinstance(v, str):
                        timestamp = self._convert_datetime_to_timestamp(v)
                        if timestamp > 0:  # 只添加有效的时间戳
                            timestamp_values.append(timestamp)
                    else:
                        try:
                            timestamp_values.append(float(v))
                        except (TypeError, ValueError):
                            continue

                if not timestamp_values:
                    # 如果没有有效时间戳，返回当前时间前后24小时
                    from datetime import datetime
                    now = datetime.now().timestamp()
                    return float(now - 86400), float(now + 86400)

                min_val = min(timestamp_values)
                max_val = max(timestamp_values)

                # 处理边界情况
                if min_val == max_val:
                    min_val = min_val - 3600  # 减少1小时
                    max_val = max_val + 3600  # 增加1小时

                return float(min_val), float(max_val)

            elif data_type == "时间":
                # 原有的时间处理逻辑保持不变
                time_values = []
                for v in valid_data:
                    if isinstance(v, str):
                        seconds = self._convert_time_to_seconds(v)
                        time_values.append(seconds)
                    else:
                        try:
                            time_values.append(float(v))
                        except (TypeError, ValueError):
                            continue

                if not time_values:
                    return 0, 86400

                min_val = min(time_values)
                max_val = max(time_values)

                if min_val == max_val:
                    min_val = max(0, min_val - 600)  # 减少10分钟
                    max_val = min_val + 3600  # 加1小时

                return float(min_val), float(max_val)

            else:
                # 数值类型处理保持不变
                numeric_data = []
                for v in valid_data:
                    try:
                        numeric_data.append(float(v))
                    except (TypeError, ValueError):
                        continue

                if not numeric_data:
                    return 0, 100

                min_val = min(numeric_data)
                max_val = max(numeric_data)

                if min_val == max_val:
                    if min_val == 0:
                        min_val = -10
                        max_val = 10
                    else:
                        abs_val = abs(min_val)
                        min_val = min_val - abs_val * 0.1
                        max_val = max_val + abs_val * 0.1

                if np.isnan(min_val) or np.isnan(max_val) or np.isinf(min_val) or np.isinf(max_val):
                    return 0, 100

                return float(min_val), float(max_val)

        except Exception as e:
            logger.error(f"获取数据范围出错: {e}")
            return 0, 100

    def time_string_to_seconds(self,time_str: str) -> int:
        """将时间字符串转换为秒数

        Args:
            time_str: 时间字符串，格式如 "05:31:23" (HH:MM:SS)

        Returns:
            int: 总秒数

        Examples:
            >>> time_string_to_seconds("05:31:23")
            19883
            >>> time_string_to_seconds("00:01:30")
            90
            >>> time_string_to_seconds("23:59:59")
            86399
        """
        try:
            # 分割时间字符串
            parts = time_str.split(":")

            if len(parts) != 3:
                raise ValueError(f"时间格式错误，应为 HH:MM:SS，实际为: {time_str}")

            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = int(parts[2])

            # 验证时间有效性
            if not (0 <= hours <= 23):
                raise ValueError(f"小时值无效: {hours}")
            if not (0 <= minutes <= 59):
                raise ValueError(f"分钟值无效: {minutes}")
            if not (0 <= seconds <= 59):
                raise ValueError(f"秒钟值无效: {seconds}")

            # 计算总秒数
            total_seconds = hours * 3600 + minutes * 60 + seconds

            return total_seconds

        except ValueError as e:
            print(f"转换时间出错: {e}")
            return 0
        except Exception as e:
            print(f"未知错误: {e}")
            return 0
    def _format_time_label(self, value: float) -> str:
        """将秒数格式化为时间标签 (HH:MM:SS)"""
        try:
            # 处理None值
            if value is None:
                return ""

            # 转换为float
            value = float(value)

            # 处理负数
            if value < 0:
                return f"-{self._format_time_label(-value)}"

            hours = int(value // 3600)
            minutes = int((value % 3600) // 60)
            seconds = int(value % 60)
            return f"{hours:02d}:{minutes:02d}:{seconds:02d}"
        except Exception as e:
            print(f"格式化时间标签出错: {e}")
            return str(value)
    """
    新增 start
    """

    def _convert_datetime_to_timestamp(self, datetime_str: str) -> float:
        """将日期时间字符串转换为时间戳（秒）"""
        try:
            import re
            from datetime import datetime

            datetime_str = datetime_str.strip()

            # 定义各种日期时间格式
            formats = [
                '%Y-%m-%d %H:%M:%S',  # 2024-01-01 12:30:45
                '%Y/%m/%d %H:%M:%S',  # 2024/01/01 12:30:45
                '%d-%m-%Y %H:%M:%S',  # 01-01-2024 12:30:45
                '%Y-%m-%dT%H:%M:%S',  # 2024-01-01T12:30:45
                '%Y-%m-%d',  # 2024-01-01 (只有日期)
                '%Y/%m/%d',  # 2024/01/01
                '%d-%m-%Y',  # 01-01-2024
            ]

            # 尝试每种格式
            for fmt in formats:
                try:
                    dt = datetime.strptime(datetime_str, fmt)
                    # 返回时间戳（秒）
                    return dt.timestamp()
                except ValueError:
                    continue

            # 如果都不匹配，尝试ISO格式解析
            try:
                dt = datetime.fromisoformat(datetime_str.replace('T', ' '))
                return dt.timestamp()
            except:
                pass

            logger.warning(f"无法解析日期时间格式: {datetime_str}")
            return 0

        except Exception as e:
            logger.error(f"转换日期时间出错: {e}")
            return 0

    def _format_datetime_label(self, timestamp: float) -> str:
        """将时间戳格式化为日期时间标签"""
        try:
            if timestamp is None:
                return ""

            timestamp = float(timestamp)

            # 处理负数时间戳
            if timestamp < 0:
                return "Invalid Time"

            from datetime import datetime
            dt = datetime.fromtimestamp(timestamp)

            # # 根据时间范围选择合适的格式
            # now = datetime.now()
            # time_diff = abs((now - dt).total_seconds())
            #
            # if time_diff < 86400:  # 24小时内，只显示时间
            #     return dt.strftime('%H:%M:%S')
            # elif time_diff < 86400 * 30:  # 30天内，显示月-日 时:分
            #     return dt.strftime('%m-%d %H:%M')
            # else:  # 超过30天，显示完整日期时间
            #     return dt.strftime('%Y-%m-%d %H:%M')
            return dt.strftime('%m-%d %H:%M')
        except Exception as e:
            logger.error(f"格式化日期时间标签出错: {e}")
            return str(timestamp)
    """
    新增 end
    """

    def _calculate_optimal_step(self, min_val: float, max_val: float, data_type: str) -> float:
        """根据数据范围计算最优的步长"""
        try:
            if min_val is None or max_val is None:
                return 1

            min_val = float(min_val)
            max_val = float(max_val)

            if np.isnan(min_val) or np.isnan(max_val) or np.isinf(min_val) or np.isinf(max_val):
                return 1

            range_val = max_val - min_val

            if range_val == 0 or range_val < 0:
                return 1

            if data_type == "日期时间":
                # 日期时间类型：返回时间戳秒数
                ideal_step = range_val / 8

                # 常用的时间步长（秒）
                datetime_steps = [
                    60,  # 1分钟
                    300,  # 5分钟
                    600,  # 10分钟
                    1800,  # 30分钟
                    3600,  # 1小时
                    7200,  # 2小时
                    10800,  # 3小时
                    21600,  # 6小时
                    43200,  # 12小时
                    86400,  # 1天
                    172800,  # 2天
                    604800,  # 1周
                    2592000,  # 30天
                    7776000,  # 90天
                    31536000  # 1年
                ]

                optimal_step = min(datetime_steps, key=lambda x: abs(x - ideal_step))
                return float(optimal_step)

            elif data_type == "时间":
                # 原有时间类型处理保持不变
                ideal_step = range_val / 8
                time_steps = [
                    1, 5, 10, 15, 30,  # 秒
                    60, 300, 600, 900, 1800,  # 分
                    3600, 7200, 10800, 21600, 86400  # 小时和更大
                ]
                optimal_step = min(time_steps, key=lambda x: abs(x - ideal_step))
                return float(optimal_step)

            elif data_type == "整数":
                # 整数处理保持不变
                ideal_step = range_val / 8
                int_steps = [1, 2, 5, 10, 20, 50, 100, 200, 500, 1000, 2000, 5000, 10000,
                             50000, 100000, 500000, 1000000]
                optimal_step = min(int_steps, key=lambda x: abs(x - ideal_step))
                return float(optimal_step)

            else:  # 浮点数
                # 浮点数处理保持不变
                ideal_step = range_val / 8
                if ideal_step == 0 or ideal_step < 0:
                    return 0.1

                log_val = np.log10(abs(ideal_step))
                if np.isnan(log_val) or np.isinf(log_val):
                    return 0.1

                magnitude = 10 ** int(np.floor(log_val))
                if magnitude <= 0 or np.isnan(magnitude) or np.isinf(magnitude):
                    magnitude = 0.1

                normalized_step = ideal_step / magnitude
                step_options = [0.1, 0.2, 0.5, 1, 1.5, 2, 2.5, 5]
                best_normalized = min(step_options, key=lambda x: abs(x - normalized_step))
                optimal_step = best_normalized * magnitude

                if optimal_step <= 0 or np.isnan(optimal_step) or np.isinf(optimal_step):
                    return 0.1

                log_step = np.log10(optimal_step)
                if np.isnan(log_step) or np.isinf(log_step):
                    decimals = 2
                else:
                    decimals = max(0, -int(np.floor(log_step)))

                return round(optimal_step, min(decimals + 1, 10))

        except Exception as e:
            logger.error(f"计算最优步长出错: {e}")
            return 1

    def _setup_ticks(self, axis, axis_config: Dict, data_list: list, is_y_axis: bool = False):
        """设置坐标轴刻度"""
        if not data_list or len(data_list) == 0:
            return

        try:
            if is_y_axis:
                axis_obj = axis.yaxis
                set_lim = lambda min_val, max_val: axis.set_ylim(min_val, max_val)
            else:
                axis_obj = axis.xaxis
                set_lim = lambda min_val, max_val: axis.set_xlim(min_val, max_val)

            axis_obj.set_major_locator(mticker.NullLocator())
            axis_obj.set_minor_locator(mticker.NullLocator())

            data_type = axis_config.get("data_type", "自动检测")
            if data_type == "自动检测":
                data_type = self._get_data_type(data_list)

            valid_data = [v for v in data_list if v is not None]
            if not valid_data:
                return

            min_val, max_val = self._get_data_range(data_list, data_type)

            if axis_config.get("auto_ticks", True):
                margin = (max_val - min_val) * 0.05
                if margin == 0:
                    margin = abs(max_val) * 0.1 if max_val != 0 else 1

                auto_min = min_val - margin
                auto_max = max_val + margin

                optimal_step = self._calculate_optimal_step(auto_min, auto_max, data_type)

                if data_type == "整数":
                    auto_min = int(np.floor(auto_min / optimal_step) * optimal_step)
                    auto_max = int(np.ceil(auto_max / optimal_step) * optimal_step)
                else:
                    auto_min = np.floor(auto_min / optimal_step) * optimal_step
                    auto_max = np.ceil(auto_max / optimal_step) * optimal_step

                set_lim(auto_min, auto_max)

                ticks = []
                tick_labels = []
                current = auto_min
                epsilon = optimal_step / 10000

                tick_count = 0
                max_ticks = 100

                while current <= auto_max + epsilon and tick_count < max_ticks:
                    tick_value = round(current, 10)
                    ticks.append(tick_value)

                    # 格式化刻度标签
                    if data_type == "日期时间":
                        tick_labels.append(self._format_datetime_label(tick_value))
                    elif data_type == "时间":
                        tick_labels.append(self._format_time_label(tick_value))
                    elif data_type == "整数":
                        tick_labels.append(str(int(tick_value)))
                    else:
                        # 浮点数处理保持不变
                        if optimal_step < 0.01:
                            tick_labels.append(f"{tick_value:.4f}")
                        elif optimal_step < 0.1:
                            tick_labels.append(f"{tick_value:.3f}")
                        elif optimal_step < 1:
                            tick_labels.append(f"{tick_value:.2f}")
                        else:
                            tick_labels.append(str(int(tick_value) if tick_value == int(tick_value) else tick_value))

                    current += optimal_step
                    tick_count += 1

                if is_y_axis:
                    axis.set_yticks(ticks)
                    if tick_labels:
                        axis.set_yticklabels(tick_labels)
                else:
                    axis.set_xticks(ticks)
                    if tick_labels:
                        # 日期时间标签可能较长，设置旋转角度
                        rotation = 45 if data_type == "日期时间" else 0
                        axis.set_xticklabels(tick_labels, rotation=rotation)

            else:
                # 手动刻度模式处理，类似上面的逻辑
                manual_min = float(axis_config.get("ticks_min", min_val))
                manual_max = float(axis_config.get("ticks_max", max_val))
                manual_step = float(axis_config.get("ticks_step", 10))

                if manual_step <= 0:
                    manual_step = self._calculate_optimal_step(manual_min, manual_max, data_type)

                set_lim(manual_min, manual_max)

                ticks = []
                tick_labels = []
                current = manual_min
                epsilon = manual_step / 10000

                tick_count = 0
                max_ticks = 100

                while current <= manual_max + epsilon and tick_count < max_ticks:
                    tick_value = round(current, 10)
                    ticks.append(tick_value)

                    if data_type == "日期时间":
                        tick_labels.append(self._format_datetime_label(tick_value))
                    elif data_type == "时间":
                        tick_labels.append(self._format_time_label(tick_value))
                    elif data_type == "整数":
                        tick_labels.append(str(int(tick_value)))
                    else:
                        if manual_step < 0.01:
                            tick_labels.append(f"{tick_value:.4f}")
                        elif manual_step < 0.1:
                            tick_labels.append(f"{tick_value:.3f}")
                        elif manual_step < 1:
                            tick_labels.append(f"{tick_value:.2f}")
                        else:
                            tick_labels.append(str(int(tick_value) if tick_value == int(tick_value) else tick_value))

                    current += manual_step
                    tick_count += 1

                if is_y_axis:
                    axis.set_yticks(ticks)
                    if tick_labels:
                        axis.set_yticklabels(tick_labels)
                else:
                    axis.set_xticks(ticks)
                    if tick_labels:
                        rotation = 45 if data_type == "日期时间" else 0
                        axis.set_xticklabels(tick_labels, rotation=rotation)

        except Exception as e:
            logger.error(f"设置刻度出错: {e}")
            import traceback
            traceback.print_exc()
            try:
                axis_obj.set_major_locator(mticker.AutoLocator())
            except:
                pass

    def _plot_line_with_gaps(self, x_data, y_data, **kwargs):
        """
        绘制带断点的折线图（在None值处断开）

        Args:
            x_data: X轴数据
            y_data: Y轴数据（可能包含None）
            **kwargs: plot方法的其他参数
        """
        # 分割成多个连续段
        segments_x = []
        segments_y = []
        current_x = []
        current_y = []

        for x, y in zip(x_data, y_data):
            if y is None:
                # 遇到None，保存当前段并开始新段
                if current_x:
                    segments_x.append(current_x)
                    segments_y.append(current_y)
                    current_x = []
                    current_y = []
            else:
                current_x.append(x)
                current_y.append(y)

        # 保存最后一段
        if current_x:
            segments_x.append(current_x)
            segments_y.append(current_y)

        # 绘制所有段
        for seg_x, seg_y in zip(segments_x, segments_y):
            if seg_x:  # 确保段不为空
                self.ax.plot(seg_x, seg_y, **kwargs)
                # 只在第一段显示标签
                if 'label' in kwargs:
                    kwargs.pop('label')

    def refresh_chart(self):
        """刷新图表显示"""
        try:
            self.ax.clear()
            self._setup_chart()

            if not self.cage_data or len(self.x_data) == 0 or not self.current_data_type:
                self.canvas.draw_idle()
                return

            self._setup_ticks(self.ax, self.x_axis_config, list(self.x_data), False)

            # 根据X轴数据类型进行转换
            x_data_type = self._get_data_type(list(self.x_data))

            if x_data_type == "日期时间":
                # 将日期时间字符串转换为时间戳
                x_data_list = [self._convert_datetime_to_timestamp(i) for i in list(self.x_data)]
            elif x_data_type == "时间":
                # 将时间字符串转换为秒数（保持原有逻辑）
                x_data_list = [self.time_string_to_seconds(i) for i in list(self.x_data)]
            else:
                # 数值类型直接使用
                x_data_list = list(self.x_data)
            # logger.critical(f"<UNK>: x_data:{list(self.x_data)},x_data_type:{x_data_type},x_data_list:{x_data_list}")
            all_y_data = []

            # 按照鼠笼ID排序显示
            sorted_cages = sorted(
                self.cage_data.keys(),
                key=lambda x: self.cage_ids.get(x, float('inf'))
            )

            for display_idx, cage_name in enumerate(sorted_cages):
                # 跳过不可见的鼠笼
                if cage_name not in self.visible_series:
                    continue

                # 检查是否有当前数据类型的数据
                if self.current_data_type not in self.cage_data[cage_name]:
                    continue

                data_list = list(self.cage_data[cage_name][self.current_data_type])
                if not data_list:
                    continue

                x_display = x_data_list[-len(data_list):] if len(data_list) > 0 else []

                all_y_data.extend(data_list)

                config = self.cage_configs.get(cage_name, {})
                color = config.get("color", "#000000")
                linewidth = self.global_width_spin.value()
                markersize = self.global_markersize_spin.value() if hasattr(self,
                                                                            'global_markersize_spin') else config.get(
                    "markersize", 6.0)
                alpha = config.get("alpha", 1.0)
                marker = config.get("marker", "o")

                if marker == "None":
                    marker = None

                if self.chart_type == "折线图":
                    self.ax.plot(x_display, data_list, label=cage_name,
                                 color=color, marker=marker, markersize=markersize,
                                 linewidth=linewidth, alpha=alpha)
                elif self.chart_type == "柱状图":
                    visible_cages = [c for c in sorted_cages if c in self.visible_series
                                     and self.current_data_type in self.cage_data[c]]
                    visible_count = len(visible_cages)
                    if visible_count > 0:
                        width = 0.8 / visible_count
                        visible_idx = visible_cages.index(cage_name)
                        positions = [i + visible_idx * width for i in range(len(x_display))]
                        self.ax.bar(positions, data_list, width=width,
                                    label=cage_name, color=color, alpha=alpha)

                elif self.chart_type == "散点图":
                    self.ax.scatter(x_display, data_list, label=cage_name,
                                    color=color, s=markersize ** 2, alpha=alpha)

                elif self.chart_type == "面积图":
                    self.ax.fill_between(range(len(x_display)), data_list,
                                         label=cage_name, color=color, alpha=alpha * 0.5)
                    self.ax.plot(x_display, data_list, color=color,
                                 linewidth=linewidth, marker=marker, markersize=markersize)

                elif self.chart_type == "混合图":
                    if display_idx % 2 == 0:
                        self.ax.plot(x_display, data_list, label=cage_name,
                                     color=color, marker=marker, markersize=markersize,
                                     linewidth=linewidth, alpha=alpha)
                    else:
                        self.ax.bar(range(len(x_display)), data_list,
                                    label=cage_name, color=color, alpha=alpha)

            # 应用配置
            self.ax.set_xlabel(self.x_axis_config["label"],
                               fontsize=self.x_axis_config["label_fontsize"],
                               color=self.x_axis_config["label_color"])
            y_label = f"{self.y_axis_config['label']} ({self.current_data_type})"
            self.ax.set_ylabel(y_label,
                               fontsize=self.y_axis_config["label_fontsize"],
                               color=self.y_axis_config["label_color"])

            self.ax.tick_params(axis='x', labelsize=self.x_axis_config["tick_labelsize"],
                                colors=self.x_axis_config["tick_color"])
            self.ax.tick_params(axis='y', labelsize=self.y_axis_config["tick_labelsize"],
                                colors=self.y_axis_config["tick_color"])

            if all_y_data:
                self._setup_ticks(self.ax, self.y_axis_config, all_y_data, True)

            # 应用主题
            theme = self.THEMES[self.current_theme]
            self.ax.set_facecolor(theme["bg_color"])
            self.ax.title.set_color(theme["text_color"])

            self.ax.grid(True, alpha=0.3, color=theme["grid_color"])

            title = f"{self.current_data_type} - {self.chart_type}"
            self.ax.set_title(title, fontsize=12, fontweight='bold')

            # 应用图例设置
            if self.legend_config["visible"] and self.visible_series:
                legend = self.ax.legend(loc=self.legend_config["position"],
                                        framealpha=self.legend_config["framealpha"],
                                        fontsize=self.legend_config["fontsize"],
                                        ncol=self.legend_config["ncol"])
                frame = legend.get_frame()
                frame.set_facecolor(self.legend_config["bg_color"])
                frame.set_edgecolor(self.legend_config["edge_color"])
                frame.set_linewidth(self.legend_config["edgewidth"])
                for text in legend.get_texts():
                    text.set_color(theme["text_color"])

            self.canvas.draw_idle()
        except Exception as e:
            logger.error(f"刷新图表出错: {e}")
            import traceback
            traceback.print_exc()


    def clear_data(self):
        """清空所有数据"""
        for cage_data in self.cage_data.values():
            for data_deque in cage_data.values():
                data_deque.clear()
        self.x_data.clear()
        self.processed_x_values.clear()
        self.processed_x_order.clear()
        self.data_counter = 0
        self.refresh_chart()
    def update_page(self,result:dict):
        """
        根据 current_page 与 page_size 刷新表格显示（只显示当前页数据）
        :param result: [{表名:数据}....]
        :return:
        """





        if not result or not result.get("columns"):
            return
        new_result = convert_data_to_cage_format(result)
        changed = False
        for data in new_result:
            changed = self.add_batch_data_with_deduplication(
                data,
                refresh=False,
                update_combo=False
            ) or changed
        if changed:
            self._update_data_type_combo()
            self._schedule_refresh()

    def showEvent(self, event):
        super().showEvent(event)
        if self.data_fetcher_thread is not None:
            if self.data_fetcher_thread.isRunning():
                self.data_fetcher_thread.resume()
                self.data_fetcher_thread.request_refresh()
            else:
                self.data_fetcher_thread.start()

    def hideEvent(self, event):
        if self.data_fetcher_thread is not None and self.data_fetcher_thread.isRunning():
            self.data_fetcher_thread.pause()
        super().hideEvent(event)

    def shutdown(self, wait_ms=1000):
        thread = self.data_fetcher_thread
        if thread is None:
            return
        thread.stop()
        if wait_ms and thread.isRunning():
            thread.wait(wait_ms)

    def closeEvent(self, event):
        self.shutdown(wait_ms=0)
        super().closeEvent(event)


# 演示程序
if __name__ == "__main__":
    import random
    from datetime import datetime, timedelta

    app = QApplication(sys.argv)

    # 创建主窗口
    window = QWidget()
    window.setWindowTitle("高级图表组件 - 支持完整X轴数据")
    window.setGeometry(50, 50, 1400, 800)
    layout = QVBoxLayout(window)

    # 创建图表组件
    chart = AdvancedChartWidget(max_points=100)

    layout.addWidget(chart)

    counter = 0
    start_time = datetime.now()


    # 模拟实时数据更新
    def update_data():
        global counter, start_time



        cage_names = ["鼠笼3", "鼠笼1", "鼠笼5"]  # 不按顺序的鼠笼号


        # 完整格式：包含X轴时间数据
        current_time = start_time + timedelta(seconds=counter * 5)
        time_str = current_time.strftime("%H:%M:%S")

        data = {
            "x_value": time_str,  # 指定X轴为时间
            "cages": {
                cage_name: {
                    "温度": 20 + idx * 2 + random.uniform(-2, 2),
                    "湿度": np.nan,
                    "压力": 100 + idx * 3 + random.uniform(-2, 2)
                }
                for idx, cage_name in enumerate(cage_names)
            }
        }

        chart.add_batch_data(data)
        counter += 1


    # # 设置定时器
    timer = QTimer()
    timer.timeout.connect(update_data)
    timer.start(500)

    window.show()
    sys.exit(app.exec())
