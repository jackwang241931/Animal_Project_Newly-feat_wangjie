from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

import numpy as np
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

from Module.mouse_trajectory_analysis.analysis_core import (
    ExperimentAnalysis,
)


CHANNEL_COLORS = (
    "#1677B8",
    "#D95F02",
    "#009E73",
    "#CC79A7",
    "#E69F00",
    "#56B4E9",
    "#D62728",
    "#1A1A1A",
)


@dataclass(frozen=True)
class MetricDefinition:
    key: str
    label: str
    unit: str

    @property
    def display_name(self) -> str:
        return f"{self.label}（{self.unit}）"


METRICS = (
    MetricDefinition("weight", "称重", "g"),
    MetricDefinition("food", "饮食", "g"),
    MetricDefinition("temperature", "体温", "°C"),
    MetricDefinition("oxygen", "氧气", "%"),
    MetricDefinition("co2", "二氧化碳", "ppm"),
)


def _decimate(x_values: np.ndarray, y_values: np.ndarray, max_points: int = 6000):
    if x_values.size <= max_points:
        return x_values, y_values
    indexes = np.linspace(0, x_values.size - 1, max_points, dtype=int)
    indexes = np.unique(np.concatenate(([0], indexes, [x_values.size - 1])))
    return x_values[indexes], y_values[indexes]


class ChannelTagBar(QWidget):
    """Channel selectors shared by the comparison controls."""

    selectionChanged = pyqtSignal(list)

    def __init__(self, exclusive: bool, parent=None):
        super().__init__(parent)
        self._exclusive = exclusive
        self._buttons: dict[int, QPushButton] = {}
        self._group = QButtonGroup(self)
        self._group.setExclusive(exclusive)
        self._group.buttonClicked.connect(self._selection_changed)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)
        self._layout = layout

    def set_channels(self, channels: Iterable[int], selected: Iterable[int] = ()):
        for button in self._buttons.values():
            self._group.removeButton(button)
            button.deleteLater()
        self._buttons.clear()

        selected_set = set(selected)
        for channel in sorted(set(int(value) for value in channels)):
            button = QPushButton(f"CH{channel:02d}")
            button.setObjectName("channelTagButton")
            button.setCheckable(True)
            button.setMinimumSize(68, 34)
            button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
            button.setChecked(channel in selected_set)
            button.setProperty("channel", channel)
            self._group.addButton(button, channel)
            self._buttons[channel] = button
            self._layout.addWidget(button)

    def selected_channels(self) -> list[int]:
        return sorted(
            int(button.property("channel"))
            for button in self._buttons.values()
            if button.isChecked()
        )

    def _selection_changed(self, _button):
        if self._exclusive and not self.selected_channels() and self._buttons:
            first_button = next(iter(self._buttons.values()))
            first_button.setChecked(True)
        self.selectionChanged.emit(self.selected_channels())


class MetricCard(QFrame):
    def __init__(self, title: str, parent=None):
        super().__init__(parent)
        self.setObjectName("analysisMetricCard")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(3)
        self.title_label = QLabel(title)
        self.title_label.setObjectName("metricTitle")
        self.value_label = QLabel("--")
        self.value_label.setObjectName("metricValue")
        self.detail_label = QLabel("待接入")
        self.detail_label.setObjectName("metricDetail")
        layout.addWidget(self.title_label)
        layout.addWidget(self.value_label)
        layout.addWidget(self.detail_label)

    def set_value(self, value: str, detail: str):
        self.value_label.setText(value)
        self.detail_label.setText(detail)


class DataComparisonWidget(QFrame):
    """A two-axis comparison chart backed by the currently loaded analysis."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("comparisonPanel")
        self.analysis: ExperimentAnalysis | None = None

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(14, 14, 14, 12)
        root_layout.setSpacing(10)

        selector_layout = QGridLayout()
        selector_layout.setHorizontalSpacing(12)
        selector_layout.setVerticalSpacing(0)
        root_layout.addLayout(selector_layout)

        self.left_metric_combo, self.left_tags = self._create_selector("左轴 · 主坐标")
        self.right_metric_combo, self.right_tags = self._create_selector("右轴 · 副坐标")
        selector_layout.addWidget(self._selector_cards[0], 0, 0)
        selector_layout.addWidget(self._selector_cards[1], 0, 1)

        self.figure = Figure(figsize=(12, 4.4), dpi=100)
        self.canvas = FigureCanvas(self.figure)
        self.canvas.setObjectName("comparisonChartCanvas")
        self.canvas.setMinimumHeight(330)
        self.canvas.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Expanding,
        )
        root_layout.addWidget(self.canvas, 1)

        self.left_metric_combo.currentIndexChanged.connect(self._render)
        self.right_metric_combo.currentIndexChanged.connect(self._render)
        self.left_tags.selectionChanged.connect(self._render)
        self.right_tags.selectionChanged.connect(self._render)
        self._draw_placeholder("请选择实验")

    def _create_selector(self, title: str):
        card = QFrame()
        card.setObjectName("comparisonSelectorCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(7)

        header = QHBoxLayout()
        badge = QLabel(title)
        badge.setObjectName("axisBadge")
        combo = QComboBox()
        combo.setMinimumHeight(34)
        combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        header.addWidget(badge)
        header.addWidget(combo, 1)
        layout.addLayout(header)

        hint = QLabel("选择需要对比的通道（可多选）")
        hint.setObjectName("comparisonHint")
        layout.addWidget(hint)
        tags = ChannelTagBar(exclusive=False)
        layout.addWidget(tags)

        if not hasattr(self, "_selector_cards"):
            self._selector_cards = []
        self._selector_cards.append(card)
        return combo, tags

    def set_analysis(
        self,
        analysis: ExperimentAnalysis | None,
        channels: Iterable[int] | None = None,
    ):
        self.analysis = analysis
        if channels is None:
            channels = [
                channel_number
                for channel_number, channel in (analysis.channels.items() if analysis else ())
                if channel.total_rows > 0
            ]
        else:
            channels = [
                int(channel_number)
                for channel_number in channels
                if analysis is not None and int(channel_number) in analysis.channels
            ]
        for combo in (self.left_metric_combo, self.right_metric_combo):
            combo.blockSignals(True)
            combo.clear()
            for metric in METRICS:
                combo.addItem(metric.display_name, metric.key)
            combo.blockSignals(False)
        for tags in (self.left_tags, self.right_tags):
            tags.set_channels(channels, channels[:1])
        self._render()

    def _metric(self, combo: QComboBox) -> MetricDefinition:
        key = combo.currentData()
        return next((metric for metric in METRICS if metric.key == key), METRICS[0])

    def _series(self, channel_number: int, metric: MetricDefinition):
        if self.analysis is None:
            return None
        return self.analysis.comparison_series.get(channel_number, {}).get(metric.key)

    def _draw_placeholder(self, message: str):
        self.figure.clear()
        axis = self.figure.add_subplot(111)
        axis.axis("off")
        axis.text(
            0.5,
            0.5,
            message,
            transform=axis.transAxes,
            ha="center",
            va="center",
            fontsize=13,
            color="#8298B2",
        )
        self.canvas.draw_idle()

    def _render(self, *_args):
        if self.analysis is None:
            self._draw_placeholder("请选择实验")
            return

        left_metric = self._metric(self.left_metric_combo)
        right_metric = self._metric(self.right_metric_combo)
        left_channels = self.left_tags.selected_channels()
        right_channels = self.right_tags.selected_channels()
        if not left_channels and not right_channels:
            self._draw_placeholder("请选择需要对比的通道")
            return

        self.figure.clear()
        left_axis = self.figure.add_subplot(111)
        right_axis = left_axis.twinx()
        left_axis.set_facecolor("#FBFDFF")
        right_axis.set_facecolor("none")
        left_axis.grid(True, color="#DCEAF6", linewidth=0.8)
        left_axis.set_axisbelow(True)

        max_elapsed = max(
            (
                float(series.elapsed_seconds[-1])
                for channel_series in self.analysis.comparison_series.values()
                for series in channel_series.values()
                if series.elapsed_seconds.size
            ),
            default=self.analysis.duration_seconds,
        )
        window_start = max(0.0, max_elapsed - 3600.0)

        plotted = False
        for axis, metric, channels, linestyle in (
            (left_axis, left_metric, left_channels, "-"),
            (right_axis, right_metric, right_channels, "--"),
        ):
            for channel_number in channels:
                series = self._series(channel_number, metric)
                if series is None:
                    continue
                x_values = series.elapsed_seconds.astype(float) - window_start
                y_values = series.values.astype(float)
                visible = x_values >= 0
                x_values = x_values[visible] / 60.0
                y_values = y_values[visible]
                x_values, y_values = _decimate(x_values, y_values)
                if not x_values.size or not np.any(np.isfinite(y_values)):
                    continue
                axis.plot(
                    x_values,
                    y_values,
                    color=CHANNEL_COLORS[(channel_number - 1) % len(CHANNEL_COLORS)],
                    linewidth=1.9,
                    linestyle=linestyle,
                    label=f"CH{channel_number:02d} · {metric.label}",
                )
                plotted = True

        left_axis.set_xlabel("实验时间（分钟）", color="#6082A4")
        left_axis.set_ylabel(f"左 · {left_metric.label}（{left_metric.unit}）", color="#1677B8")
        right_axis.set_ylabel(
            f"右 · {right_metric.label}（{right_metric.unit}）", color="#D95F02"
        )
        left_axis.tick_params(axis="y", colors="#1677B8")
        right_axis.tick_params(axis="y", colors="#D95F02")
        left_axis.tick_params(axis="x", colors="#6082A4")
        left_axis.set_title("对比曲线（近 1 小时 · 左右双纵轴）", color="#193B5C", pad=14)
        if max_elapsed <= 3600.0:
            left_axis.set_xlim(left=0.0, right=max(max_elapsed / 60.0, 1.0))
        else:
            left_axis.set_xlim(left=0.0, right=60.0)

        handles, labels = left_axis.get_legend_handles_labels()
        right_handles, right_labels = right_axis.get_legend_handles_labels()
        legend_handles = handles + right_handles
        legend_labels = labels + right_labels
        if legend_handles:
            column_count = min(
                8,
                max(2, self.canvas.width() // 170),
                len(legend_handles),
            )
            row_count = (len(legend_handles) + column_count - 1) // column_count
            bottom_margin = min(0.38, 0.21 + max(0, row_count - 1) * 0.055)
            self.figure.legend(
                legend_handles,
                legend_labels,
                loc="lower left",
                bbox_to_anchor=(0.055, 0.012),
                ncol=column_count,
                frameon=False,
                fontsize=9,
                handlelength=1.7,
                columnspacing=1.25,
                labelspacing=0.8,
            )
        else:
            bottom_margin = 0.17
        if not plotted:
            left_axis.text(
                0.5,
                0.5,
                "当前通道没有有效数据",
                transform=left_axis.transAxes,
                ha="center",
                va="center",
                color="#8298B2",
            )
        self.figure.subplots_adjust(
            left=0.07,
            right=0.94,
            top=0.88,
            bottom=bottom_margin,
        )
        self.canvas.draw_idle()


class AnalysisDashboard(QScrollArea):
    """Scrollable data-analysis dashboard matching the existing application style."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.analysis: ExperimentAnalysis | None = None
        self.setObjectName("analysisDashboard")
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        content = QWidget()
        content.setObjectName("analysisDashboardContent")
        self.setWidget(content)
        self.content_layout = QVBoxLayout(content)
        self.content_layout.setContentsMargins(2, 2, 10, 14)
        self.content_layout.setSpacing(12)

        self._set_style()
        self._build_summary_section()
        self._build_compare_section()

        self._legacy_title = QLabel("更多轨迹分析")
        self._legacy_title.setObjectName("dashboardSectionTitle")
        self._legacy_title.hide()
        self.content_layout.addWidget(self._legacy_title)

    def _set_style(self):
        self.setStyleSheet(
            """
            QScrollArea#analysisDashboard { border: 0; }
            QFrame#analysisSection, QFrame#comparisonPanel,
            QFrame#analysisMetricCard, QFrame#comparisonSelectorCard {
                background: #FFFFFF;
                border: 1px solid #C9C9C9;
                border-radius: 2px;
            }
            QFrame#analysisMetricCard { min-height: 82px; }
            QLabel#dashboardEyebrow { color: #555555; font-weight: 700; }
            QLabel#dashboardSectionTitle { color: #333333; font-size: 15px; font-weight: 700; }
            QLabel#dashboardHint, QLabel#comparisonHint { color: #777777; }
            QLabel#metricTitle { color: #666666; font-size: 12px; }
            QLabel#metricValue { color: #333333; font-size: 20px; font-weight: 700; }
            QLabel#metricDetail { color: #777777; font-size: 12px; }
            QPushButton#channelTagButton {
                color: #333333;
                background: #F0F0F0;
                border: 1px solid #BDBDBD;
                border-radius: 2px;
                padding: 4px 9px;
            }
            QPushButton#channelTagButton:checked {
                color: #FFFFFF;
                background: #555555;
                border: 1px solid #444444;
            }
            QLabel#axisBadge { font-weight: 700; }
            QComboBox {
                background: #FFFFFF;
                border: 1px solid #BDBDBD;
                border-radius: 2px;
                padding: 4px 8px;
            }
            """
        )

    def _section_header(self, eyebrow: str, title: str, hint: str = ""):
        header = QVBoxLayout()
        header.setSpacing(2)
        eyebrow_label = QLabel(eyebrow)
        eyebrow_label.setObjectName("dashboardEyebrow")
        title_label = QLabel(title)
        title_label.setObjectName("dashboardSectionTitle")
        header.addWidget(eyebrow_label)
        header.addWidget(title_label)
        if hint:
            hint_label = QLabel(hint)
            hint_label.setObjectName("dashboardHint")
            header.addWidget(hint_label)
        return header

    def _build_summary_section(self):
        section = QFrame()
        section.setObjectName("analysisSection")
        layout = QVBoxLayout(section)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.addLayout(
            self._section_header(
                "DATA ANALYSIS",
                "数据分析 · 高级计算",
                "行为模式、活动覆盖度和异常通道指标预留区域。",
            )
        )
        grid = QGridLayout()
        grid.setSpacing(10)
        titles = ("行为模式", "主导行为", "昼夜活动比", "轨迹覆盖度", "平均活跃度", "异常通道")
        self.metric_cards = []
        for index, title in enumerate(titles):
            card = MetricCard(title)
            self.metric_cards.append(card)
            grid.addWidget(card, 0, index)
        layout.addLayout(grid)
        self.content_layout.addWidget(section)

    def _build_compare_section(self):
        section_title = QLabel("数据对比图")
        section_title.setObjectName("dashboardSectionTitle")
        self.content_layout.addWidget(section_title)
        self.comparison_widget = DataComparisonWidget()
        self.content_layout.addWidget(self.comparison_widget)

    def add_legacy_charts(self, widget: QWidget):
        widget.setMinimumHeight(390)
        self.content_layout.addWidget(widget)
        self._legacy_title.show()

    def set_analysis(
        self,
        analysis: ExperimentAnalysis | None,
        channels: Iterable[int] | None = None,
    ):
        self.analysis = analysis
        self.comparison_widget.set_analysis(analysis, channels)
