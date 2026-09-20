from __future__ import annotations

from collections import OrderedDict
from pathlib import Path
from threading import Lock
from typing import Callable

import numpy as np
from PyQt6.QtCore import QSize, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import (
    QApplication,
    QCompleter,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QSizePolicy,
    QStyle,
    QTabWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from loguru import logger
from matplotlib import rcParams
from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.colors import ListedColormap
from matplotlib.figure import Figure

from Module.mouse_trajectory_analysis.analysis_core import (
    ExperimentAnalysis,
    ExperimentFile,
    aggregate_distance,
    _trajectory_csv_paths,
    load_trajectory_experiment,
    scan_trajectory_experiments,
    sleep_state_matrix,
    trajectory_plot_arrays,
)
from Module.mouse_trajectory_analysis.ui.analysis_dashboard import AnalysisDashboard
from public.config_class.global_setting import global_setting
from theme.ThemeQt6 import ThemedWindow


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
MAX_CACHE_ITEMS = 3
MAX_LINE_POINTS = 12_000
MAX_TRAJECTORY_POINTS = 20_000
_ACTIVE_LOAD_THREADS: set[QThread] = set()
_ANALYSIS_CACHE: OrderedDict[tuple[str, int, int], ExperimentAnalysis] = OrderedDict()
_LATEST_ANALYSIS_BY_PATH: dict[
    str,
    tuple[tuple[str, int, int] | None, ExperimentAnalysis],
] = {}
_ANALYSIS_CACHE_LOCK = Lock()


def _normalized_experiment_path(path: str | Path) -> str:
    return str(Path(path).expanduser().resolve())


def _trajectory_path_signature(path: Path) -> tuple[str, int, int]:
    csv_paths = _trajectory_csv_paths(path)
    if not csv_paths:
        raise FileNotFoundError(f"实验目录中没有 trajectory.csv：{path}")
    stats = [csv_path.stat() for csv_path in csv_paths]
    return (
        _normalized_experiment_path(path),
        max(int(stat.st_mtime_ns) for stat in stats),
        sum(int(stat.st_size) for stat in stats),
    )


def _remember_latest_analysis(analysis: ExperimentAnalysis) -> None:
    try:
        signature = _trajectory_path_signature(analysis.source_path)
    except OSError:
        signature = None
    with _ANALYSIS_CACHE_LOCK:
        _LATEST_ANALYSIS_BY_PATH[
            _normalized_experiment_path(analysis.source_path)
        ] = (signature, analysis)


def _latest_analysis_for_path(
    path: Path,
) -> tuple[tuple[str, int, int] | None, ExperimentAnalysis] | None:
    with _ANALYSIS_CACHE_LOCK:
        return _LATEST_ANALYSIS_BY_PATH.get(_normalized_experiment_path(path))

rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
rcParams["axes.unicode_minus"] = False


class TrajectoryLoadThread(QThread):
    loaded = pyqtSignal(int, object)
    failed = pyqtSignal(int, str)
    cancelled = pyqtSignal(int)

    def __init__(self, token: int, path: Path):
        super().__init__()
        self.token = token
        self.path = path

    def run(self):
        try:
            analysis = load_trajectory_experiment(
                self.path,
                interruption_requested=self.isInterruptionRequested,
            )
            _remember_latest_analysis(analysis)
            if self.isInterruptionRequested():
                self.cancelled.emit(self.token)
                return
            self.loaded.emit(self.token, analysis)
        except InterruptedError:
            self.cancelled.emit(self.token)
        except ValueError as error:
            logger.warning(f"轨迹分析数据不可用: {self.path} | {error}")
            self.failed.emit(self.token, str(error))
        except Exception as error:
            logger.exception(f"读取轨迹实验目录失败: {self.path}")
            self.failed.emit(self.token, str(error))


class TrajectoryAnalysisWindow(ThemedWindow):
    BEHAVIOR_MODE = "behavior"
    COMPARISON_MODE = "comparison"
    experiment_selection_changed = pyqtSignal(str)

    TAB_DEFINITIONS = (
        ("累计路程", "cumulative"),
        ("每秒/每分钟", "distance_rate"),
        ("总路程", "total"),
        ("二维轨迹", "trajectory"),
        ("睡眠热力图", "sleep"),
    )

    def __init__(self, content_mode: str = BEHAVIOR_MODE):
        super().__init__()
        self.content_mode = content_mode
        self.setFont(QFont("Microsoft YaHei", 10))
        self.trajectory_root = self._resolve_trajectory_root()
        self.analysis: ExperimentAnalysis | None = None
        self.experiment_records: list[ExperimentFile] = []
        # Keep the cache while the host rebuilds this page when the user changes menus.
        self._cache = _ANALYSIS_CACHE
        self._load_thread: TrajectoryLoadThread | None = None
        self._pending_record: ExperimentFile | None = None
        self._load_token = 0
        self._rendered_keys: set[str] = set()
        self._figures: dict[str, Figure] = {}
        self._canvases: dict[str, FigureCanvas] = {}
        self._experiment_list_initialized = False
        self.chart_tabs: QTabWidget | None = None
        self.dashboard: AnalysisDashboard | None = None
        self._closing = False
        self._preferred_experiment_path: str | None = None

        self._init_ui()
        self.status_label.setText("进入页面后自动读取实验列表")

    def _resolve_trajectory_root(self) -> Path:
        from Module.mouse_trajectory.paths import EXPORT_DIR

        return Path(EXPORT_DIR).resolve()

    def calculate_minimum_suggested_size(self) -> QSize:
        # The module host wraps this window in a resizable scroll area and uses
        # this value as the embedded minimum. Ignore Matplotlib's 1200x700 hint
        # so the canvas follows the host's real available height.
        return QSize(640, 360)

    def _init_ui(self):
        title = "行为规律" if self.content_mode == self.BEHAVIOR_MODE else "数据对比"
        self.setWindowTitle(title)
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        root_layout = QVBoxLayout(central_widget)
        # BaseWindow expands the embedded central widget underneath the main
        # status bar. Reserve that covered strip so chart axes remain visible.
        root_layout.setContentsMargins(14, 12, 14, 82)
        root_layout.setSpacing(8)

        control_band = QFrame()
        control_band.setObjectName("trajectoryAnalysisControls")
        control_layout = QHBoxLayout(control_band)
        control_layout.setContentsMargins(6, 4, 6, 4)
        control_layout.setSpacing(8)

        control_layout.addWidget(QLabel("实验时间"))
        self.experiment_combo = QComboBox()
        self.experiment_combo.setObjectName("trajectoryExperimentCombo")
        self.experiment_combo.setMinimumWidth(360)
        self.experiment_combo.setEditable(True)
        self.experiment_combo.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.experiment_combo.completer().setCompletionMode(
            QCompleter.CompletionMode.PopupCompletion
        )
        self.experiment_combo.completer().setFilterMode(Qt.MatchFlag.MatchContains)
        self.experiment_combo.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Fixed,
        )
        self.experiment_combo.currentIndexChanged.connect(self._experiment_changed)
        control_layout.addWidget(self.experiment_combo, 1)

        self.refresh_button = QToolButton()
        self.refresh_button.setObjectName("trajectoryRefreshButton")
        self.refresh_button.setText("刷新")
        self.refresh_button.setToolTip("刷新实验列表")
        self.refresh_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.refresh_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_BrowserReload)
        )
        self.refresh_button.clicked.connect(self.refresh_experiment_list)
        control_layout.addWidget(self.refresh_button)

        self.threshold_label = QLabel("移动阈值")
        self.threshold_combo = QComboBox()
        self.threshold_combo.addItem("2.5 mm", 2.5)
        self.threshold_combo.addItem("5 mm", 5.0)
        self.threshold_combo.setCurrentIndex(1)
        self.threshold_combo.currentIndexChanged.connect(self._threshold_changed)
        control_layout.addWidget(self.threshold_label)
        control_layout.addWidget(self.threshold_combo)

        self.save_button = QToolButton()
        self.save_button.setObjectName("trajectorySaveChartButton")
        self.save_button.setText("保存图片")
        self.save_button.setToolTip("以300 DPI保存当前图表")
        self.save_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.save_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogSaveButton)
        )
        self.save_button.clicked.connect(self._save_current_figure)
        self.save_button.setEnabled(False)
        control_layout.addWidget(self.save_button)
        if self.content_mode != self.BEHAVIOR_MODE:
            self.threshold_label.hide()
            self.threshold_combo.hide()
            self.save_button.hide()
        root_layout.addWidget(control_band)

        information_band = QFrame()
        information_layout = QHBoxLayout(information_band)
        information_layout.setContentsMargins(6, 0, 6, 0)
        self.status_label = QLabel()
        self.status_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status_label.setSizePolicy(
            QSizePolicy.Policy.Expanding,
            QSizePolicy.Policy.Preferred,
        )
        information_layout.addWidget(self.status_label, 1)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFixedWidth(180)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.hide()
        information_layout.addWidget(self.progress_bar)
        if self.content_mode != self.BEHAVIOR_MODE:
            information_band.hide()
        root_layout.addWidget(information_band)

        if self.content_mode == self.BEHAVIOR_MODE:
            self.chart_tabs = QTabWidget()
            self.chart_tabs.setObjectName("trajectoryAnalysisTabs")
            for chart_title, key in self.TAB_DEFINITIONS:
                page = QWidget()
                page_layout = QVBoxLayout(page)
                page_layout.setContentsMargins(0, 4, 0, 0)
                figure = Figure(
                    figsize=(12, 4.8),
                    dpi=100,
                    layout=None if key == "sleep" else "constrained",
                )
                canvas = FigureCanvas(figure)
                canvas.setSizePolicy(
                    QSizePolicy.Policy.Expanding,
                    QSizePolicy.Policy.Ignored,
                )
                canvas.setMinimumSize(0, 0)
                page_layout.addWidget(canvas)
                self.chart_tabs.addTab(page, chart_title)
                self._figures[key] = figure
                self._canvases[key] = canvas
                self._draw_placeholder(key, "请选择实验")
            self.chart_tabs.currentChanged.connect(self._chart_tab_changed)
            root_layout.addWidget(self.chart_tabs, 1)
        else:
            self.dashboard = AnalysisDashboard()
            root_layout.addWidget(self.dashboard, 1)
        self._update_threshold_visibility()

    def refresh_experiment_list(self):
        selected_path = (
            self.experiment_combo.currentData()
            or self._preferred_experiment_path
        )
        self.experiment_records = scan_trajectory_experiments(self.trajectory_root)
        self.experiment_combo.blockSignals(True)
        self.experiment_combo.clear()
        selected_index = -1
        for index, record in enumerate(self.experiment_records):
            self.experiment_combo.addItem(record.display_text, str(record.path))
            self.experiment_combo.setItemData(index, str(record.path), Qt.ItemDataRole.ToolTipRole)
            if selected_path and str(record.path) == selected_path:
                selected_index = index
        self.experiment_combo.blockSignals(False)

        if not self.experiment_records:
            self.status_label.setText(f"未找到轨迹实验数据：{self.trajectory_root}")
            self.status_label.setToolTip(str(self.trajectory_root))
            self.analysis = None
            self.save_button.setEnabled(False)
            if self.dashboard is not None:
                self.dashboard.set_analysis(None)
            if self.content_mode == self.BEHAVIOR_MODE:
                for _, key in self.TAB_DEFINITIONS:
                    self._draw_placeholder(key, "当前目录没有可分析的 trajectory.csv")
            return

        self.status_label.setText(
            f"已发现 {len(self.experiment_records)} 次实验  |  数据目录：{self.trajectory_root}"
        )
        self.status_label.setToolTip(str(self.trajectory_root))
        self.experiment_combo.blockSignals(True)
        self.experiment_combo.setCurrentIndex(selected_index if selected_index >= 0 else 0)
        self.experiment_combo.blockSignals(False)
        self._experiment_changed(self.experiment_combo.currentIndex())

    def _experiment_changed(self, index: int):
        if index < 0 or index >= len(self.experiment_records):
            return
        record = self.experiment_records[index]
        self._preferred_experiment_path = str(record.path)
        self.experiment_selection_changed.emit(self._preferred_experiment_path)
        self._queue_load(record)

    def set_preferred_experiment_path(self, path: str | Path | None):
        """Restore the last selected experiment after the host rebuilds this page."""
        if not path:
            self._preferred_experiment_path = None
            return
        self._preferred_experiment_path = str(Path(path).expanduser().resolve())

    @staticmethod
    def _path_signature(path: Path) -> tuple[str, int, int]:
        return _trajectory_path_signature(path)

    @classmethod
    def _cache_key(cls, record: ExperimentFile) -> tuple[str, int, int]:
        return cls._path_signature(record.path)

    def _queue_load(self, record: ExperimentFile):
        try:
            cache_key = self._cache_key(record)
        except OSError as error:
            self._show_load_error(str(error))
            return

        cached = self._cache.get(cache_key)
        if cached is not None:
            self._cache.move_to_end(cache_key)
            self._apply_analysis(cached)
            return

        latest = _latest_analysis_for_path(record.path)
        if latest is not None:
            latest_signature, latest_analysis = latest
            self._apply_analysis(latest_analysis)
            if latest_signature == cache_key:
                self._cache[cache_key] = latest_analysis
                self._cache.move_to_end(cache_key)
                return

        if self._load_thread is not None and self._load_thread.isRunning():
            self._pending_record = record
            self._load_token += 1
            self._load_thread.requestInterruption()
            self.status_label.setText("正在切换实验，请稍候...")
            return

        self._start_load(record)

    def _start_load(self, record: ExperimentFile):
        self._pending_record = None
        self._load_token += 1
        token = self._load_token
        self.progress_bar.show()
        self.refresh_button.setEnabled(False)
        self.experiment_combo.setEnabled(False)
        self.save_button.setEnabled(False)
        if self.analysis is not None:
            self.status_label.setText(f"正在更新：{record.display_text}")
        else:
            self.status_label.setText(f"正在读取：{record.display_text}")

        normalized_path = _normalized_experiment_path(record.path)
        worker = next(
            (
                current
                for current in tuple(_ACTIVE_LOAD_THREADS)
                if isinstance(current, TrajectoryLoadThread)
                and current.isRunning()
                and not current.isInterruptionRequested()
                and _normalized_experiment_path(current.path) == normalized_path
            ),
            None,
        )
        if worker is not None:
            self._load_thread = worker
            worker.loaded.connect(self._shared_trajectory_loaded)
            worker.failed.connect(self._shared_trajectory_failed)
            worker.finished.connect(self._worker_finished)
            if worker.isFinished():
                self._load_thread = None
                latest = _latest_analysis_for_path(record.path)
                if latest is not None:
                    self._trajectory_loaded(token, latest[1])
                    self.progress_bar.hide()
                    self.refresh_button.setEnabled(True)
                    self.experiment_combo.setEnabled(True)
                else:
                    self._start_load(record)
            return

        worker = TrajectoryLoadThread(token, record.path)
        self._load_thread = worker
        _ACTIVE_LOAD_THREADS.add(worker)
        worker.loaded.connect(self._trajectory_loaded)
        worker.failed.connect(self._trajectory_failed)
        worker.finished.connect(self._worker_finished)
        worker.finished.connect(lambda current=worker: _ACTIVE_LOAD_THREADS.discard(current))
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _shared_trajectory_loaded(
        self,
        _worker_token: int,
        analysis: ExperimentAnalysis,
    ) -> None:
        selected_path = self._preferred_experiment_path
        if (
            self.sender() is self._load_thread
            and selected_path is not None
            and _normalized_experiment_path(analysis.source_path)
            == _normalized_experiment_path(selected_path)
        ):
            self._trajectory_loaded(self._load_token, analysis)

    def _shared_trajectory_failed(self, _worker_token: int, message: str) -> None:
        if self.sender() is self._load_thread:
            self._trajectory_failed(self._load_token, message)

    def _trajectory_loaded(self, token: int, analysis: ExperimentAnalysis):
        if self._closing or token != self._load_token:
            return
        _remember_latest_analysis(analysis)
        try:
            cache_key = self._path_signature(analysis.source_path)
            self._cache[cache_key] = analysis
            self._cache.move_to_end(cache_key)
            while len(self._cache) > MAX_CACHE_ITEMS:
                self._cache.popitem(last=False)
        except OSError:
            pass
        self._apply_analysis(analysis)

    def _trajectory_failed(self, token: int, message: str):
        if self._closing or token != self._load_token:
            return
        self._show_load_error(message)

    def _worker_finished(self):
        if self._closing:
            return
        worker = self.sender()
        if not isinstance(worker, TrajectoryLoadThread):
            return
        if self._load_thread is worker:
            self._load_thread = None
        self.progress_bar.hide()
        self.refresh_button.setEnabled(True)
        self.experiment_combo.setEnabled(True)
        pending_record = self._pending_record
        self._pending_record = None
        if pending_record is not None:
            self._queue_load(pending_record)

    def _show_load_error(self, message: str):
        self.analysis = None
        self.save_button.setEnabled(False)
        if self.dashboard is not None:
            self.dashboard.set_analysis(None)
        self.status_label.setText(f"实验数据读取失败：{message}")
        if self.content_mode == self.BEHAVIOR_MODE:
            for _, key in self.TAB_DEFINITIONS:
                self._draw_placeholder(key, "实验数据读取失败")

    def _apply_analysis(self, analysis: ExperimentAnalysis):
        self.analysis = analysis
        self._rendered_keys.clear()
        duration_minutes = analysis.duration_seconds / 60.0
        available_channels = self._enabled_channel_numbers(analysis)
        detection_details = "  ".join(
            f"{channel}号 {analysis.channels[channel].detection_rate:.1%}"
            for channel in available_channels
        )
        self.status_label.setText(
            f"已加载 {analysis.source_path.name}  |  时长 {duration_minutes:.1f} 分钟"
            f"  |  检测率：{detection_details or '无有效通道'}"
        )
        self.status_label.setToolTip(
            f"实验目录：{analysis.source_path}\n"
            "坐标来源：cage_1～cage_8/data/trajectory.csv"
        )
        self.save_button.setEnabled(True)
        if self.dashboard is not None:
            self.dashboard.set_analysis(analysis, available_channels)
        if self.content_mode == self.BEHAVIOR_MODE:
            self._render_current_chart()

    def _current_chart_key(self) -> str:
        if self.chart_tabs is None:
            return self.TAB_DEFINITIONS[0][1]
        index = self.chart_tabs.currentIndex()
        if index < 0:
            return self.TAB_DEFINITIONS[0][1]
        return self.TAB_DEFINITIONS[index][1]

    def _available_channel_numbers(self) -> list[int]:
        if self.analysis is None:
            return []
        return self._enabled_channel_numbers(self.analysis)

    @staticmethod
    def _enabled_channel_numbers(analysis: ExperimentAnalysis) -> list[int]:
        """Return the channels enabled for the current experiment.

        The GUI keeps the filtered experiment setting, while the monitor service
        also publishes its active cage list. Both are preferred over inferring
        enabled channels from whichever CSV files happen to contain rows.
        """
        configured: list[int] = []
        setting = global_setting.get_setting("experiment_setting")
        groups = getattr(setting, "groups", None) if setting is not None else None
        if groups:
            configured = [
                int(group.id)
                for group in groups
                if getattr(group, "is_selected", True) and getattr(group, "id", None) is not None
            ]

        if not configured:
            active_cages = global_setting.get_setting("mouse_cages", None) or []
            configured = [
                int(channel_number)
                for channel_number in active_cages
                if str(channel_number).strip().lstrip("-").isdigit()
            ]

        configured = sorted({channel for channel in configured if channel in analysis.channels})
        if configured:
            return configured

        return [
            channel_number
            for channel_number, channel in analysis.channels.items()
            if channel.total_rows > 0
        ]

    def _chart_tab_changed(self, _index: int):
        if self.content_mode != self.BEHAVIOR_MODE:
            return
        self._update_threshold_visibility()
        self._render_current_chart()

    def _threshold_changed(self, _index: int):
        if self.content_mode != self.BEHAVIOR_MODE:
            return
        self._rendered_keys.discard("sleep")
        if self._current_chart_key() == "sleep":
            self._render_current_chart()

    def _update_threshold_visibility(self):
        if self.content_mode != self.BEHAVIOR_MODE:
            return
        visible = self._current_chart_key() == "sleep"
        self.threshold_label.setVisible(visible)
        self.threshold_combo.setVisible(visible)

    def _render_current_chart(self):
        if self.analysis is None:
            return
        key = self._current_chart_key()
        if key in self._rendered_keys:
            return
        renderers: dict[str, Callable[[], None]] = {
            "cumulative": self._render_cumulative,
            "distance_rate": self._render_distance_rate,
            "total": self._render_total_distance,
            "trajectory": self._render_trajectories,
            "sleep": self._render_sleep_heatmap,
        }
        QApplication.setOverrideCursor(Qt.CursorShape.WaitCursor)
        try:
            renderers[key]()
            self._rendered_keys.add(key)
        except Exception as error:
            logger.exception(f"绘制轨迹分析图失败: {key}")
            self._draw_placeholder(key, f"图表绘制失败：{error}")
        finally:
            QApplication.restoreOverrideCursor()

    def _draw_placeholder(self, key: str, message: str):
        figure = self._figures[key]
        figure.clear()
        axis = figure.add_subplot(111)
        axis.axis("off")
        axis.text(
            0.5,
            0.5,
            message,
            transform=axis.transAxes,
            ha="center",
            va="center",
            fontsize=13,
            color="#666666",
        )
        self._canvases[key].draw_idle()

    @staticmethod
    def _decimate_line(
        x_values: np.ndarray,
        y_values: np.ndarray,
        max_points: int = MAX_LINE_POINTS,
    ) -> tuple[np.ndarray, np.ndarray]:
        if x_values.size <= max_points:
            return x_values, y_values
        indexes = np.linspace(0, x_values.size - 1, max_points, dtype=int)
        indexes = np.unique(np.concatenate(([0], indexes, [x_values.size - 1])))
        return x_values[indexes], y_values[indexes]

    @staticmethod
    def _decimate_trajectory(
        x_values: np.ndarray,
        y_values: np.ndarray,
    ) -> tuple[np.ndarray, np.ndarray]:
        if x_values.size <= MAX_TRAJECTORY_POINTS:
            return x_values, y_values
        step = int(np.ceil(x_values.size / MAX_TRAJECTORY_POINTS))
        indexes = np.arange(0, x_values.size, step, dtype=int)
        if indexes[-1] != x_values.size - 1:
            indexes = np.append(indexes, x_values.size - 1)
        sampled_x = x_values[indexes].copy()
        sampled_y = y_values[indexes].copy()
        for sample_index in range(1, indexes.size):
            start = indexes[sample_index - 1] + 1
            end = indexes[sample_index] + 1
            if np.any(~np.isfinite(x_values[start:end]) | ~np.isfinite(y_values[start:end])):
                sampled_x[sample_index] = np.nan
                sampled_y[sample_index] = np.nan
        return sampled_x, sampled_y

    @staticmethod
    def _style_axis(axis):
        axis.grid(True, color="#D6DADF", linewidth=0.8, alpha=0.65)
        axis.spines["top"].set_visible(False)
        axis.spines["right"].set_visible(False)

    def _render_cumulative(self):
        assert self.analysis is not None
        key = "cumulative"
        figure = self._figures[key]
        figure.clear()
        axis = figure.add_subplot(111)
        has_valid_data = False
        channel_numbers = self._available_channel_numbers()
        for channel_number in channel_numbers:
            channel = self.analysis.channels[channel_number]
            x_values = channel.elapsed_seconds / 60.0
            y_values = channel.cumulative_distance_mm
            x_values, y_values = self._decimate_line(x_values, y_values)
            if x_values.size:
                has_valid_data = has_valid_data or channel.valid_rows > 0
                axis.plot(
                    x_values,
                    y_values,
                    color=CHANNEL_COLORS[channel_number - 1],
                    linewidth=1.7,
                    label=f"笼子 {channel_number}",
                )
        axis.set_title(f"{len(channel_numbers)}个笼子累计二维运动距离")
        axis.set_xlabel("实验时间（分钟）")
        axis.set_ylabel("累计二维运动距离（mm）")
        self._style_axis(axis)
        if channel_numbers:
            axis.legend(
                ncol=min(4, len(channel_numbers)),
                loc="upper left",
                frameon=False,
            )
        if not has_valid_data:
            axis.text(0.5, 0.5, "没有有效坐标数据", transform=axis.transAxes, ha="center")
        self._canvases[key].draw_idle()

    def _render_distance_rate(self):
        assert self.analysis is not None
        key = "distance_rate"
        figure = self._figures[key]
        figure.clear()
        second_axis, minute_axis = figure.subplots(2, 1)
        channel_numbers = self._available_channel_numbers()
        for channel_number in channel_numbers:
            channel = self.analysis.channels[channel_number]
            second_time, second_distance = aggregate_distance(channel, 1.0)
            minute_time, minute_distance = aggregate_distance(channel, 60.0)
            second_time, second_distance = self._decimate_line(second_time, second_distance)
            minute_time, minute_distance = self._decimate_line(minute_time / 60.0, minute_distance)
            color = CHANNEL_COLORS[channel_number - 1]
            second_axis.plot(
                second_time,
                second_distance,
                color=color,
                linewidth=1.0,
                label=f"笼子 {channel_number}",
            )
            minute_axis.plot(
                minute_time,
                minute_distance,
                color=color,
                linewidth=1.5,
                label=f"笼子 {channel_number}",
            )
        second_axis.set_title("每秒二维运动距离对比")
        second_axis.set_xlabel("实验时间（秒）")
        second_axis.set_ylabel("距离（mm）")
        minute_axis.set_title("每分钟二维运动距离对比")
        minute_axis.set_xlabel("实验时间（分钟）")
        minute_axis.set_ylabel("距离（mm）")
        self._style_axis(second_axis)
        self._style_axis(minute_axis)
        if channel_numbers:
            minute_axis.legend(
                ncol=min(4, len(channel_numbers)),
                loc="upper center",
                frameon=False,
            )
        self._canvases[key].draw_idle()

    def _render_total_distance(self):
        assert self.analysis is not None
        key = "total"
        figure = self._figures[key]
        figure.clear()
        axis = figure.add_subplot(111)
        channel_numbers = np.asarray(self._available_channel_numbers(), dtype=int)
        totals = np.asarray(
            [
                self.analysis.channels[channel].total_distance_mm
                for channel in channel_numbers
            ],
            dtype=float,
        )
        bars = axis.bar(
            channel_numbers,
            totals,
            color=[CHANNEL_COLORS[channel - 1] for channel in channel_numbers],
            width=0.68,
        )
        offset = max(float(np.max(totals, initial=0.0)) * 0.015, 1.0)
        for bar, total in zip(bars, totals):
            axis.text(
                bar.get_x() + bar.get_width() / 2.0,
                total + offset,
                f"{total:,.1f}",
                ha="center",
                va="bottom",
                fontsize=9,
            )
        axis.set_title(f"{len(channel_numbers)}个笼子二维总路程对比")
        axis.set_xlabel("笼子编号")
        axis.set_ylabel("二维总路程（mm）")
        axis.set_xticks(channel_numbers, [f"笼子 {channel}" for channel in channel_numbers])
        axis.set_ylim(bottom=0)
        self._style_axis(axis)
        self._canvases[key].draw_idle()

    def _render_trajectories(self):
        assert self.analysis is not None
        key = "trajectory"
        figure = self._figures[key]
        figure.clear()
        channel_numbers = self._available_channel_numbers()
        column_count = min(4, max(len(channel_numbers), 1))
        row_count = max((len(channel_numbers) + column_count - 1) // column_count, 1)
        axes = np.asarray(
            figure.subplots(
                row_count,
                column_count,
                sharex=True,
                sharey=True,
                squeeze=False,
            )
        )
        all_x: list[np.ndarray] = []
        all_y: list[np.ndarray] = []
        for channel_number in channel_numbers:
            channel = self.analysis.channels[channel_number]
            if channel.valid_rows:
                all_x.append(channel.x_mm[channel.valid])
                all_y.append(channel.y_mm[channel.valid])
        if all_x and all_y:
            combined_x = np.concatenate(all_x)
            combined_y = np.concatenate(all_y)
            x_margin = max(float(np.ptp(combined_x)) * 0.04, 1.0)
            y_margin = max(float(np.ptp(combined_y)) * 0.04, 1.0)
            x_limits = (float(np.min(combined_x)) - x_margin, float(np.max(combined_x)) + x_margin)
            y_limits = (float(np.min(combined_y)) - y_margin, float(np.max(combined_y)) + y_margin)
        else:
            x_limits = (-1.0, 1.0)
            y_limits = (-1.0, 1.0)

        for channel_number, axis in zip(channel_numbers, axes.flat):
            channel = self.analysis.channels[channel_number]
            x_values, y_values = trajectory_plot_arrays(channel)
            x_values, y_values = self._decimate_trajectory(x_values, y_values)
            color = CHANNEL_COLORS[channel_number - 1]
            if x_values.size:
                axis.plot(x_values, y_values, color=color, linewidth=0.8, alpha=0.78)
            valid_indexes = np.flatnonzero(channel.valid)
            if valid_indexes.size:
                first_index = int(valid_indexes[0])
                last_index = int(valid_indexes[-1])
                axis.scatter(
                    [channel.x_mm[first_index]],
                    [channel.y_mm[first_index]],
                    color="#1B9E77",
                    s=24,
                    zorder=3,
                )
                axis.scatter(
                    [channel.x_mm[last_index]],
                    [channel.y_mm[last_index]],
                    color="#D95F02",
                    marker="x",
                    s=28,
                    zorder=3,
                )
            else:
                axis.text(0.5, 0.5, "无有效轨迹", transform=axis.transAxes, ha="center")
            axis.set_title(f"笼子 {channel_number}", color=color)
            axis.set_xlim(*x_limits)
            axis.set_ylim(*y_limits)
            axis.set_aspect("equal", adjustable="box")
            self._style_axis(axis)
        for unused_axis in axes.flat[len(channel_numbers):]:
            figure.delaxes(unused_axis)
        figure.suptitle(
            f"{len(channel_numbers)}个笼子二维轨迹对比（空白帧不跨段连线）"
        )
        figure.supxlabel("X 坐标（mm）")
        figure.supylabel("Y 坐标（mm）")
        self._canvases[key].draw_idle()

    def _render_sleep_heatmap(self):
        assert self.analysis is not None
        key = "sleep"
        figure = self._figures[key]
        figure.clear()
        # The explanatory note is outside the axes; reserve a real bottom
        # margin so it cannot collide with the x-axis label or colorbar.
        figure.subplots_adjust(left=0.08, right=0.92, bottom=0.22, top=0.88)
        axis = figure.add_subplot(111)
        threshold = float(self.threshold_combo.currentData())
        states, time_edges = sleep_state_matrix(
            self.analysis,
            movement_threshold_mm=threshold,
        )
        channel_numbers = self._available_channel_numbers()
        states = states[np.asarray(channel_numbers, dtype=int) - 1]
        color_map = ListedColormap(["#FFFBE6", "#C00000"])
        color_map.set_bad("#D9DDE3")
        image = axis.imshow(
            states,
            aspect="auto",
            interpolation="nearest",
            cmap=color_map,
            vmin=0.0,
            vmax=1.0,
            extent=[
                time_edges[0],
                time_edges[-1],
                len(channel_numbers) + 0.5,
                0.5,
            ],
        )
        axis.set_title(
            f"{len(channel_numbers)}个笼子10秒分段连续静止睡眠热力图"
            f"（移动阈值 {threshold:g} mm）"
        )
        axis.set_xlabel("实验时间（分钟）", labelpad=12)
        axis.set_ylabel("笼子编号")
        axis.set_yticks(
            range(1, len(channel_numbers) + 1),
            [f"笼子 {channel}" for channel in channel_numbers],
        )
        color_bar = figure.colorbar(image, ax=axis, fraction=0.028, pad=0.02)
        color_bar.set_ticks([0.0, 1.0], labels=["活动", "睡眠"])
        self._canvases[key].draw_idle()

    def _save_current_figure(self):
        if self.content_mode != self.BEHAVIOR_MODE or self.analysis is None:
            return
        key = self._current_chart_key()
        chart_title = dict((key_name, title) for title, key_name in self.TAB_DEFINITIONS)[key]
        source_path = self.analysis.source_path
        if source_path.is_dir():
            output_directory = source_path / "analysis"
            output_directory.mkdir(parents=True, exist_ok=True)
            default_path = output_directory / f"{source_path.name}_{chart_title}.png"
        else:
            default_path = source_path.with_name(f"{source_path.stem}_{chart_title}.png")
        output_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存图表",
            str(default_path),
            "PNG 图片 (*.png);;JPEG 图片 (*.jpg *.jpeg)",
        )
        if not output_path:
            return
        try:
            self._figures[key].savefig(output_path, dpi=300, bbox_inches="tight")
            self.status_label.setText(f"图片已保存：{output_path}")
        except Exception as error:
            logger.exception(f"保存轨迹分析图失败: {output_path}")
            QMessageBox.warning(self, "保存失败", str(error))

    def closeEvent(self, event):
        self._closing = True
        self._load_token += 1
        self._pending_record = None
        # Loading is shared across rebuilt menu pages. Let it finish so the next
        # page can reuse the parsed result instead of restarting the same file.
        self._load_thread = None
        super().closeEvent(event)

    def showEvent(self, event):
        if not self._experiment_list_initialized:
            self._experiment_list_initialized = True
            QTimer.singleShot(0, self.refresh_experiment_list)


class DataComparisonWindow(TrajectoryAnalysisWindow):
    """Data comparison page using the shared experiment-loading workflow."""

    def __init__(self):
        super().__init__(content_mode=self.COMPARISON_MODE)
