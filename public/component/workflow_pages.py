"""P3--P6 workflow pages.

The pages in this module are deliberately thin UI adapters.  They do not
invent measurements: status values come from the main-window context and
health snapshot providers. Monitoring uses the experiment database; legacy
two-dimensional trajectory analysis uses its matching per-cage CSV export.
"""

from __future__ import annotations

import math
import os
import re
import sqlite3
import time
import uuid
import csv
import tempfile
from itertools import chain, zip_longest
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional

from PyQt6.QtCore import QEvent, QPointF, QRect, QSize, QThread, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QBrush, QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PyQt6.QtSvg import QSvgGenerator
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QButtonGroup,
    QFrame,
    QFileDialog,
    QGridLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSizePolicy,
    QScrollArea,
    QSlider,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)
from public.component.analysis_export_widgets import ChartImageDialog
from public.component.analysis_performance import extrema_preview, sleep_preview, sleep_segments
from public.component.live_analysis_snapshot import LiveDatabaseSnapshotLoader
from loguru import logger

from public.config_class.global_setting import global_setting
from public.component.system_status.system_status_page import (
    calculate_ee,
    derive_channel_status_issues,
    format_duration,
    health_summary_text,
)
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.dao.SQLite.Monitor_Datas_Handle import Monitor_Datas_Handle
from public.function.Modbus.Modbus_Type import Others_Tables
from public.function.weight.weight_postprocessor import (
    EPOCH_WEIGHT_COLUMN_KEY,
    fit_epoch_weight_rows,
    load_weight_postprocess_config,
    weight_row_key,
)
from public.function.cage_control_commands import configurable_cages
from public.util.shared_video_frames import shared_video_frame_store
from public.util.project_paths import resolve_project_path
from Module.mouse_trajectory.paths import EXPORT_DIR as TRAJECTORY_EXPORT_DIR
from Module.mouse_trajectory_analysis.analysis_core import (
    find_matching_trajectory_export,
    find_matching_monitor_workbook,
    load_monitor_workbook_bundle,
    load_trajectory_experiment,
    parse_trajectory_export_time,
    parse_experiment_time,
    scan_experiment_files,
    scan_trajectory_experiments,
)


# Keep the workflow projection tied to the same physical cage dimensions used
# by trajectory calibration. The fallback keeps the UI usable if optional
# calibration dependencies are unavailable during a no-hardware test.
try:
    from Module.mouse_trajectory.utils.animal_box_calibration import (
        BOTTOM_W as CAGE_WIDTH_MM,
        BOTTOM_L as CAGE_LENGTH_MM,
        HEIGHT_EST as CAGE_HEIGHT_MM,
    )
except Exception:  # pragma: no cover - optional calibration dependencies
    CAGE_WIDTH_MM, CAGE_LENGTH_MM, CAGE_HEIGHT_MM = 180.0, 360.0, 150.0

UNKNOWN = "未知"
PENDING = "None / 公式待定"
LIVE_ANALYSIS_WINDOW_SECONDS = 3600.0


def _live_analysis_time_range() -> tuple[float, float]:
    now = time.time()
    return now - LIVE_ANALYSIS_WINDOW_SECONDS, now


class _PhysicalMillimeterPoint(tuple):
    """Mark legacy CSV coordinates so small millimetre values stay millimetres."""

    def __new__(cls, x: float, y: float, z: float = 0.0):
        return super().__new__(cls, (float(x), float(y), float(z)))

# The comparison axes expose the persisted raw/calculated measurements used by
# the original data-analysis view.  Keep the labels and aliases together so
# the UI and the SQLite lookup cannot drift apart.  Values are read from the
# selected channel's Epoch_data_cage_N table; no demo series is generated.
COMPARE_METRIC_DEFINITIONS = (
    ("称重（g）", ("WM_weight_num", "weight_num", "body_weight", "weight", "称重", "重量")),
    ("饮食（g）", ("DWM_weight_num", "food_num", "food", "cumulative_food_amount", "饮食", "食物", "摄食")),
    ("体温（°C）", ("temperature_num", "cage_inside_temperature", "body_temperature", "core_body_temperature", "体温", "温度")),
    ("氧气（%）", ("oxygen_num", "o2in_concentration", "O2", "O₂", "氧气", "氧气(%)")),
    ("二氧化碳（ppm）", ("CO2_num", "co2in_concentration", "CO2", "CO₂", "二氧化碳")),
    ("跑轮", ("ENM_running_wheel_num", "running_wheel_num", "running_wheel_rotations", "跑轮", "跑轮圈数")),
    ("VO₂ 氧耗量（L/min）", ("vo2_consumption", "VO2_L_min", "VO2_ml_min", "VO2", "vo2")),
    ("VCO₂ 二氧化碳产生量（L/min）", ("vco2_production", "VCO2_L_min", "VCO2_ml_min", "VCO2", "vco2")),
    ("RER 呼吸熵", ("rer_rq", "RER", "RQ", "respiratory_exchange_ratio")),
    ("EE 能量消耗（kcal/hr）", ("ee_energy_expenditure", "EE_kcal_hr", "EE", "energy_expenditure")),
)
COMPARE_METRIC_LABELS = [label for label, _aliases in COMPARE_METRIC_DEFINITIONS]


def _provider_value(provider: Any) -> Mapping[str, Any]:
    if provider is None:
        return {}
    try:
        value = provider() if callable(provider) else provider
        if hasattr(value, "to_dict"):
            value = value.to_dict()
        return value if isinstance(value, Mapping) else {}
    except Exception:
        return {}


def _text(value: Any, default: str = UNKNOWN) -> str:
    if value is None or value == "":
        return default
    return str(value)


def _number(value: Any) -> Optional[float]:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _plot_time_value(value: Any) -> Optional[float]:
    """Normalize numeric/ISO timestamps the same way the plot does."""
    numeric = _number(value)
    if numeric is not None:
        return numeric / 1000.0 if abs(numeric) > 100_000_000_000 else numeric
    text = str(value or "").strip()
    if not text:
        return None
    candidates = (text, text.replace("Z", "+00:00"), text.replace("_", "-"))
    for candidate in candidates:
        try:
            return datetime.fromisoformat(candidate).timestamp()
        except ValueError:
            continue
    for pattern in ("%Y-%m-%d-%H-%M-%S-%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d-%H-%M-%S"):
        try:
            return datetime.strptime(text.replace("_", "-"), pattern).timestamp()
        except ValueError:
            continue
    return None


def _numbers(value: Any) -> list[float]:
    """Convert a scalar or stored comma-separated weight series to numbers."""
    if isinstance(value, (list, tuple)):
        values = value
    elif isinstance(value, str) and "," in value:
        values = value.split(",")
    else:
        values = (value,)
    return [number for item in values if (number := _number(item)) is not None]


def _table_matches_channel(table_name: Any, channel: int) -> bool:
    """Match cage tables whether their suffix is ``1`` or zero-padded ``01``."""
    match = re.search(r"(?:cage|channel|ch)[_\-]?(\d+)$", str(table_name), re.IGNORECASE)
    if match is None:
        match = re.search(r"[_\-](\d+)$", str(table_name))
    return match is not None and int(match.group(1)) == int(channel)


def _health_items(provider: Any) -> list[Mapping[str, Any]]:
    health = _provider_value(provider)
    return [item for item in (health.get("components") or []) if isinstance(item, Mapping)]


class _HistoricalWorkbookLoader(QThread):
    """Read a potentially large historical monitor workbook off the UI thread."""

    loaded = pyqtSignal(int, object, float, object, object)
    failed = pyqtSignal(int, object, str)

    def __init__(self, generation: int, cache_key: tuple[Any, ...], path: str):
        super().__init__()
        self.generation = int(generation)
        self.cache_key = cache_key
        self.path = str(path)

    def run(self) -> None:  # pragma: no cover - exercised through Qt signals
        try:
            source = Path(self.path)
            stat = source.stat()
            started = parse_experiment_time(source) or datetime.fromtimestamp(stat.st_mtime)
            comparison, latest_rows = load_monitor_workbook_bundle(
                source,
                started.timestamp(),
                self.isInterruptionRequested,
            )
        except InterruptedError:
            return
        except Exception as error:
            if not self.isInterruptionRequested():
                self.failed.emit(self.generation, self.cache_key, str(error))
            return
        if not self.isInterruptionRequested():
            self.loaded.emit(
                self.generation,
                self.cache_key,
                started.timestamp(),
                comparison,
                latest_rows,
            )


class _BaseWorkflowPage(QWidget):
    """Shared card styling and refresh helpers for P3--P6."""

    refreshed = pyqtSignal()

    def __init__(self, context_provider: Any = None, health_source: Any = None, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._context_provider = context_provider
        self._health_source = health_source
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(2000)
        self._refresh_timer.timeout.connect(self.refresh)
        self._build_style()

    def _build_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget { color:#17395c; font-family:"Microsoft YaHei","SimSun"; }
            QWidget#workflowPage { background:#f4f8fc; }
            QFrame[card="true"] { background:#fff; border:1px solid #d5e5f3; border-radius:10px; }
            QLabel[eyebrow="true"] { color:#1872c5; font-size:11px; font-weight:700; letter-spacing:1px; }
            QLabel[title="true"] { color:#12385f; font-size:17px; font-weight:700; }
            QLabel[hint="true"] { color:#7191b2; font-size:11px; }
            QLabel[label="true"] { color:#6585a6; font-size:11px; }
            QLabel[value="true"] { color:#0b579e; font-size:18px; font-weight:700; }
            QLabel[chip="true"] { border-radius:10px; padding:3px 8px; font-size:11px; font-weight:700; }
            QLabel[tone="neutral"] { background:#edf1f5; color:#56616d; }
            QLabel[tone="info"] { background:#e2f1ff; color:#156fc1; }
            QLabel[tone="normal"] { background:#e4f7ed; color:#178850; }
            QLabel[tone="warning"] { background:#fff4df; color:#b56800; }
            QLabel[tone="fault"] { background:#ffebec; color:#d33f49; }
            QPushButton { min-height:34px; border-radius:8px; padding:4px 14px; border:1px solid #b9d7f1; background:#fff; color:#1767ad; font-weight:700; }
            QPushButton:hover { background:#eef7ff; }
            QPushButton:checked { color:#fff; background:#2389da; border-color:#2389da; }
            QPushButton#primary { color:#fff; background:#2389da; border-color:#2389da; }
            QPushButton#danger { color:#fff; background:#e6545b; border-color:#e6545b; }
            QProgressBar { min-height:8px; max-height:8px; border:0; border-radius:4px; background:#e5f1fb; }
            QProgressBar::chunk { border-radius:4px; background:#47a4e9; }
            QTableWidget { background:#fff; border:1px solid #d5e5f3; border-radius:8px; gridline-color:#e4edf5; }
            QTableWidget#runLogTable { border:0; background:#fff; }
            QTableWidget#runLogTable::item { padding:5px 8px; border:0; }
            QHeaderView::section { background:#eef6ff; color:#18558a; border:0; padding:7px; font-weight:700; }
            QComboBox { min-height:32px; border:1px solid #cfe1f1; border-radius:7px; padding:2px 8px; background:#fff; }
            """
        )
        self.setObjectName("workflowPage")

    def _canvas(self) -> tuple[QScrollArea, QVBoxLayout]:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        canvas = QWidget(scroll)
        canvas.setObjectName("workflowCanvas")
        canvas.setStyleSheet("QWidget#workflowCanvas { background:#f4f8fc; }")
        layout = QVBoxLayout(canvas)
        layout.setContentsMargins(14, 12, 14, 18)
        layout.setSpacing(12)
        scroll.setWidget(canvas)
        root.addWidget(scroll)
        return scroll, layout

    @staticmethod
    def _header(layout: QVBoxLayout, eyebrow: str, title: str, hint: str = "") -> None:
        row = QHBoxLayout()
        label = QLabel(eyebrow.upper())
        label.setProperty("eyebrow", True)
        row.addWidget(label)
        row.addStretch(1)
        heading = QLabel(title)
        heading.setProperty("title", True)
        row.addWidget(heading)
        layout.addLayout(row)
        if hint:
            note = QLabel(hint)
            note.setProperty("hint", True)
            note.setWordWrap(True)
            layout.addWidget(note)

    @staticmethod
    def _card(title: str, value: str = "", detail: str = "") -> tuple[QFrame, QLabel, QLabel]:
        card = QFrame()
        card.setProperty("card", True)
        box = QVBoxLayout(card)
        box.setContentsMargins(14, 12, 14, 12)
        label = QLabel(title)
        label.setProperty("label", True)
        box.addWidget(label)
        value_label = QLabel(value)
        value_label.setProperty("value", True)
        value_label.setWordWrap(True)
        box.addWidget(value_label)
        detail_label = QLabel(detail)
        detail_label.setProperty("hint", True)
        detail_label.setWordWrap(True)
        box.addWidget(detail_label)
        return card, value_label, detail_label

    @staticmethod
    def _set_chip(label: QLabel, text: str, tone: str = "neutral") -> None:
        label.setText(text)
        label.setProperty("chip", True)
        label.setProperty("tone", tone)
        label.style().unpolish(label)
        label.style().polish(label)

    def start_refresh(self) -> None:
        if not self._refresh_timer.isActive():
            self._refresh_timer.start()
        self.refresh()

    def stop_refresh(self) -> None:
        self._refresh_timer.stop()

    def refresh(self) -> None:  # pragma: no cover - interface hook
        self.refreshed.emit()


class CageCalibrationImage(QLabel):
    """Visible-light frame with an optional four-point cage calibration overlay.

    Points are emitted in the source-frame coordinate system, not in widget
    coordinates.  This keeps a calibration valid when the image is resized
    by Qt or when the window is moved to another display.
    """

    point_clicked = pyqtSignal(float, float)

    def __init__(self, text: str = "", parent: Optional[QWidget] = None):
        super().__init__(text, parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._source_pixmap = QPixmap()
        self._frame_size = (0, 0)
        self._points: list[tuple[float, float]] = []
        self._detected_points: list[tuple[float, float]] = []
        self._calibration_mode = False

    def set_frame_image(self, pixmap: QPixmap, frame_size: tuple[int, int]) -> None:
        self._source_pixmap = pixmap if isinstance(pixmap, QPixmap) else QPixmap()
        self._frame_size = (max(int(frame_size[0]), 0), max(int(frame_size[1]), 0))
        self._refresh_pixmap()

    def clear_frame_image(self) -> None:
        self._source_pixmap = QPixmap()
        self._frame_size = (0, 0)
        self.setPixmap(QPixmap())
        self.update()

    def set_calibration_mode(self, enabled: bool) -> None:
        self._calibration_mode = bool(enabled)
        self.setCursor(
            Qt.CursorShape.CrossCursor if self._calibration_mode else Qt.CursorShape.ArrowCursor
        )
        self.update()

    def set_calibration_points(self, points: Iterable[Iterable[float]]) -> None:
        parsed: list[tuple[float, float]] = []
        for point in points or []:
            try:
                x, y = point
                parsed.append((float(x), float(y)))
            except (TypeError, ValueError):
                continue
        self._points = parsed[:4]
        self.update()

    def calibration_points(self) -> list[tuple[float, float]]:
        return list(self._points)

    def set_detected_cage_points(self, points: Iterable[Any]) -> None:
        """Show the cage corners produced by the legacy trajectory service."""
        parsed: list[tuple[float, float]] = []
        for point in points or []:
            try:
                if isinstance(point, Mapping):
                    x, y = point.get("x"), point.get("y")
                else:
                    x, y = point
                parsed.append((float(x), float(y)))
            except (TypeError, ValueError):
                continue
        self._detected_points = parsed[:4]
        self.update()


    def _display_rect(self) -> QRect:
        pixmap = self.pixmap()
        if pixmap is None or pixmap.isNull():
            return QRect()
        x = max((self.width() - pixmap.width()) // 2, 0)
        y = max((self.height() - pixmap.height()) // 2, 0)
        return QRect(x, y, pixmap.width(), pixmap.height())

    def _refresh_pixmap(self) -> None:
        if self._source_pixmap.isNull():
            self.setPixmap(QPixmap())
            self.update()
            return
        scaled = self._source_pixmap.scaled(
            self.size(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled)
        self.update()

    def resizeEvent(self, event) -> None:  # pragma: no cover - Qt callback
        super().resizeEvent(event)
        self._refresh_pixmap()

    def mousePressEvent(self, event) -> None:  # pragma: no cover - Qt callback
        if not self._calibration_mode or event.button() != Qt.MouseButton.LeftButton:
            super().mousePressEvent(event)
            return
        if len(self._points) >= 4:
            return
        frame_width, frame_height = self._frame_size
        display = self._display_rect()
        if frame_width <= 0 or frame_height <= 0 or display.isNull():
            return
        position = event.position()
        if not display.contains(int(position.x()), int(position.y())):
            return
        x = (position.x() - display.left()) * frame_width / max(display.width(), 1)
        y = (position.y() - display.top()) * frame_height / max(display.height(), 1)
        self._points.append((x, y))
        self.point_clicked.emit(x, y)
        self.update()

    def paintEvent(self, event) -> None:  # pragma: no cover - Qt callback
        super().paintEvent(event)
        if not self._points and not self._detected_points:
            return
        frame_width, frame_height = self._frame_size
        display = self._display_rect()
        if frame_width <= 0 or frame_height <= 0 or display.isNull():
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        detected = [
            QPointF(
                display.left() + x * display.width() / frame_width,
                display.top() + y * display.height() / frame_height,
            )
            for x, y in self._detected_points
        ]
        if len(detected) == 4:
            painter.setPen(QPen(QColor("#f4b400"), 2))
            for first, second in zip(detected, detected[1:] + detected[:1]):
                painter.drawLine(first, second)


        pen = QPen(QColor("#ff4d5a" if self._calibration_mode else "#28b779"), 3)
        painter.setPen(pen)
        mapped: list[QPointF] = []
        for x, y in self._points:
            mapped.append(
                QPointF(
                    display.left() + x * display.width() / frame_width,
                    display.top() + y * display.height() / frame_height,
                )
            )
        if len(mapped) >= 2:
            for first, second in zip(mapped, mapped[1:]):
                painter.drawLine(first, second)
        if len(mapped) == 4:
            painter.drawLine(mapped[-1], mapped[0])
        painter.setBrush(QColor("#ff4d5a" if self._calibration_mode else "#28b779"))
        for index, point in enumerate(mapped, 1):
            painter.drawEllipse(point, 6, 6)
            painter.drawText(point + QPointF(8, -8), str(index))
        painter.end()


class ExperimentControlPage(_BaseWorkflowPage):
    """P3 run control and gas-path calibration entry point."""

    def __init__(self, main_window: Any = None, context_provider: Any = None, health_source: Any = None, parent=None):
        self.main_window = main_window
        self._last_run_status = None
        self._active_calibrations: set[str] = set()
        self._stopping_calibrations: set[str] = set()
        self._combined_calibration = False
        self.main_log_list = QTableWidget(0, 2)
        super().__init__(context_provider, health_source, parent)
        # The displayed duration contains seconds, so P3 must refresh once per
        # second.  Other workflow pages keep their less frequent data refresh.
        self._refresh_timer.setInterval(1000)
        self._build_ui()
        self.start_refresh()

    def _build_ui(self) -> None:
        _scroll, layout = self._canvas()
        self._header(layout, "CONTROL", "实验运行控制", "开始、终止和气路校准均调用现有后台服务；本页不提供暂停按钮。")

        status_grid = QGridLayout()
        status_grid.setHorizontalSpacing(12)
        self.status_grid = status_grid
        self.summary_cards = []
        self.status_value = QLabel(UNKNOWN); self.status_value.setProperty("value", True)
        self.duration_value = QLabel(UNKNOWN); self.duration_value.setProperty("value", True)
        self.health_value = QLabel(UNKNOWN); self.health_value.setProperty("value", True)
        for column, (title, value, detail) in enumerate((
            ("当前状态", self.status_value, "实验生命周期状态"),
            ("运行时长", self.duration_value, "由真实开始/停止时间计算"),
            ("系统健康", self.health_value, "来自独立健康快照"),
        )):
            card = QFrame(); card.setProperty("card", True)
            card.setMinimumSize(280, 118)
            card.setMaximumHeight(125)
            card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            self.summary_cards.append(card)
            box = QHBoxLayout(card) if column == 0 else QVBoxLayout(card)
            box.setContentsMargins(14, 12, 14, 12)
            if column == 0:
                self.status_card = card
                status_box = QVBoxLayout()
                progress_box = QVBoxLayout()
                progress_box.setSpacing(7)
                self.progress_title = QLabel("当前操作进度 0%")
                self.progress_title.setProperty("label", True)
                self.operation_progress = QProgressBar()
                self.operation_progress.setRange(0, 100)
                self.operation_progress.setValue(0)
                self.operation_progress.setTextVisible(False)
                self.operation_label = QLabel("当前操作：未开始")
                self.operation_label.setProperty("hint", True)
                self.operation_label.setWordWrap(True)
                self._operation_base_text = "当前操作：未开始"
                progress_box.addWidget(self.progress_title)
                progress_box.addWidget(self.operation_progress)
                progress_box.addWidget(self.operation_label)
                box.addLayout(status_box)
                box.addSpacing(12)
                box.addLayout(progress_box, 1)
                value_box = status_box
            else:
                value_box = box
            label = QLabel(title); label.setProperty("label", True); value_box.addWidget(label)
            value_box.addWidget(value)
            note = QLabel(detail); note.setProperty("hint", True); value_box.addWidget(note)
            status_grid.addWidget(card, 0, column)
            status_grid.setColumnStretch(column, 1)
        layout.addLayout(status_grid)

        action_card = QFrame(); action_card.setProperty("card", True)
        action_box = QVBoxLayout(action_card); action_box.setContentsMargins(10, 8, 10, 8)
        action_box.setSpacing(4)
        # Keep all four summary cards at the same stable width and height.
        action_card.setMinimumSize(280, 118)
        action_card.setMaximumHeight(125)
        action_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        action_box.addWidget(QLabel("操作（开始 / 终止）"))
        actions = QHBoxLayout()
        self.start_button = QPushButton("▶ 开始实验"); self.start_button.setObjectName("primary")
        self.stop_button = QPushButton("■ 终止实验"); self.stop_button.setObjectName("danger")
        for button in (self.start_button, self.stop_button):
            button.setMinimumSize(96, 32)
            button.setMaximumSize(116, 36)
        self.start_button.clicked.connect(self._start_experiment)
        self.stop_button.clicked.connect(self._stop_experiment)
        actions.addWidget(self.start_button); actions.addWidget(self.stop_button); actions.addStretch(1)
        action_box.addLayout(actions)
        self.operation_hint = QLabel("终止实验需要二次确认，默认不会因健康告警自动终止。")
        self.operation_hint.setProperty("hint", True); action_box.addWidget(self.operation_hint)
        calibration_card = QFrame(); calibration_card.setProperty("card", True)
        calibration_box = QVBoxLayout(calibration_card); calibration_box.setContentsMargins(14, 12, 14, 12)
        calibration_box.setSpacing(10)
        calibration_card.setMinimumHeight(300)
        calibration_box.addWidget(QLabel("空气校准"))
        self.calibration_status = QLabel("待校准"); self.calibration_status.setProperty("value", True)
        calibration_box.addWidget(self.calibration_status)
        self.calibration_buttons: dict[str, QPushButton] = {}
        air_button = QPushButton("空气校准")
        air_button.clicked.connect(self._start_air_calibration)
        self.calibration_buttons["air"] = air_button
        calibration_box.addWidget(air_button)

        # Match the legacy monitor page: three distinct start operations,
        # their matching stop operations, and the real calibration dialog.
        control_grid = QGridLayout(); control_grid.setHorizontalSpacing(8); control_grid.setVerticalSpacing(8)
        for column, (key, title) in enumerate((
            ("zero", "校零"), ("span", "校量程"), ("all", "校零且量程"),
        )):
            button = QPushButton(title)
            button.clicked.connect(lambda _checked=False, key=key: self._start_calibration(key))
            self.calibration_buttons[key] = button
            control_grid.addWidget(button, 0, column)
        self.calibration_details_button = QPushButton("显示校准信息详情")
        self.calibration_details_button.clicked.connect(lambda: self._open_calibration_details(None))
        control_grid.addWidget(self.calibration_details_button, 0, 3)
        for column, (key, title) in enumerate((
            ("stop_zero", "停止校零"), ("stop_span", "停止校量程"),
            ("stop_all", "停止校零且量程"),
        )):
            button = QPushButton(title)
            button.clicked.connect(lambda _checked=False, key=key: self._stop_calibration(key))
            self.calibration_buttons[key] = button
            control_grid.addWidget(button, 1, column)
        calibration_box.addLayout(control_grid)
        self.calibration_evidence = QLabel("校准读数（采样流量、O₂、CO₂、气室压差、气室温度）：未知")
        self.calibration_evidence.setProperty("hint", True); self.calibration_evidence.setVisible(True); calibration_box.addWidget(self.calibration_evidence)
        self._update_calibration_buttons()
        # Align controls as a fourth card beside the three status cards.
        status_grid.addWidget(action_card, 0, 3)
        status_grid.setColumnStretch(3, 1)
        self.summary_cards.append(action_card)
        layout.addWidget(calibration_card)

        cage_card = QFrame(); cage_card.setProperty("card", True)
        cage_box = QVBoxLayout(cage_card); cage_box.setContentsMargins(14, 12, 14, 12)
        cage_box.setSpacing(10)
        cage_title = QLabel("逐笼硬件配置"); cage_title.setProperty("title", True)
        cage_box.addWidget(cage_title)
        self.cage_control_hint = QLabel("仅显示已启用且设备检测通过的鼠笼。")
        self.cage_control_hint.setProperty("hint", True)
        cage_box.addWidget(self.cage_control_hint)
        self.cage_control_rows = {}
        self.cage_control_buttons = {}
        self.cage_control_sliders = {}
        self._pending_cage_controls: dict[str, tuple[int, str, Any]] = {}
        for cage in range(1, 9):
            row = QFrame()
            row.setStyleSheet("QFrame { background:#f8fbff; border:1px solid #dcebf7; border-radius:8px; }")
            row_box = QVBoxLayout(row); row_box.setContentsMargins(10, 8, 10, 8); row_box.setSpacing(6)
            switches = QHBoxLayout(); switches.setSpacing(8)
            cage_label = QLabel(f"CH{cage:02d}")
            cage_label.setMinimumWidth(48)
            switches.addWidget(cage_label)
            environment_widgets = []
            food_widgets = []
            for control, value, label in (
                ("wheel_brake", "enable", "启用跑轮刹车"),
                ("wheel_brake", "release", "解除跑轮刹车"),
                ("light", "on", "开灯"),
                ("light", "off", "关灯"),
                ("food_door", "open", "开食槽门"),
                ("food_door", "close", "关食槽门"),
            ):
                button = QPushButton(label)
                button.clicked.connect(
                    lambda _checked=False, c=cage, kind=control, setting=value:
                    self._send_cage_control(c, kind, setting)
                )
                self.cage_control_buttons[(cage, control, value)] = button
                (food_widgets if control == "food_door" else environment_widgets).append(button)
                switches.addWidget(button)
            switches.addStretch(1)
            row_box.addLayout(switches)
            levels = QHBoxLayout(); levels.setSpacing(8)
            for control, title in (("light_color", "光照色温"), ("light_brightness", "光照亮度")):
                label = QLabel(title)
                slider = QSlider(Qt.Orientation.Horizontal)
                slider.setRange(1, 9)
                slider.setValue(1)
                current_value = QLabel("当前值: 1")
                slider.valueChanged.connect(
                    lambda value, target=current_value: target.setText(f"当前值: {value}")
                )
                slider.sliderReleased.connect(
                    lambda c=cage, kind=control, editor=slider:
                    self._send_cage_control(c, kind, editor.value())
                )
                self.cage_control_sliders[(cage, control)] = slider
                for widget in (label, slider, current_value):
                    environment_widgets.append(widget)
                    levels.addWidget(widget)
                levels.setStretch(levels.count() - 2, 1)
            row_box.addLayout(levels)
            cage_box.addWidget(row)
            self.cage_control_rows[cage] = (row, environment_widgets, food_widgets)
        self.cage_control_feedback = QLabel("操作发出后以设备回包为准，此处不预设开关状态。")
        self.cage_control_feedback.setProperty("hint", True)
        cage_box.addWidget(self.cage_control_feedback)
        layout.addWidget(cage_card)

        system_card = QFrame(); system_card.setProperty("card", True)
        system_box = QGridLayout(system_card); system_box.setContentsMargins(14, 12, 14, 12)
        system_title = QLabel("系统运行信息"); system_title.setProperty("title", True); system_box.addWidget(system_title, 0, 0, 1, 3)
        self.system_info_labels: dict[str, QLabel] = {}
        self.system_info_details: dict[str, QLabel] = {}
        system_specs = (
            ("collection", "数据采集链路"), ("gas_path", "气体代谢子系统"),
            ("environment", "温控/环境"), ("trajectory", "轨迹相机"),
            ("infrared", "红外相机"), ("behavior", "行为识别子系统"),
            ("storage", "数据存储"),
        )
        for index, (key, title) in enumerate(system_specs):
            card, value, detail = self._card(title, UNKNOWN, "未收到健康快照")
            self.system_info_labels[key] = value
            self.system_info_details[key] = detail
            system_box.addWidget(card, 1 + index // 3, index % 3)
        layout.addWidget(system_card)
        log_card = QFrame(); log_card.setProperty("card", True)
        log_box = QVBoxLayout(log_card); log_box.setContentsMargins(14, 12, 14, 12)
        log_title = QLabel("运行日志"); log_title.setProperty("title", True); log_box.addWidget(log_title)
        log_hint = QLabel("实验运行过程事件流。"); log_hint.setProperty("hint", True); log_box.addWidget(log_hint)
        self.main_log_list = QTableWidget(0, 3)
        self.main_log_list.setObjectName("runLogTable")
        self.main_log_list.setHorizontalHeaderLabels(["时间", "类型", "事件"])
        self.main_log_list.setMaximumHeight(220)
        self.main_log_list.setMinimumHeight(150)
        self.main_log_list.setShowGrid(False)
        self.main_log_list.setAlternatingRowColors(False)
        self.main_log_list.verticalHeader().setVisible(False)
        self.main_log_list.horizontalHeader().setVisible(False)
        log_header = self.main_log_list.horizontalHeader()
        log_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Fixed)
        log_header.setSectionResizeMode(1, QHeaderView.ResizeMode.Fixed)
        log_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        # Keep HH:mm:ss fully visible at different Windows DPI/font scales.
        log_metrics = self.main_log_list.fontMetrics()
        self.main_log_list.setColumnWidth(
            0, max(96, log_metrics.horizontalAdvance("00:00:00") + 28)
        )
        self.main_log_list.setColumnWidth(
            1, max(68, log_metrics.horizontalAdvance("校准") + 30)
        )
        self.main_log_list.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.main_log_list.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        log_box.addWidget(self.main_log_list)
        layout.addWidget(log_card)
        layout.addStretch(1)

    def set_operation_progress(self, value: int, label: str | None = None) -> None:
        """Update the shared bar from real backend lifecycle events."""
        percent = max(0, min(100, int(value)))
        self.operation_progress.setValue(percent)
        self.progress_title.setText(f"当前操作进度 {percent}%")
        if label:
            self._operation_base_text = f"当前操作：{label}"
            self.operation_label.setText(self._operation_base_text)

    def _send_monitor_command(self, title: str, data: Any = None) -> bool:
        queue = global_setting.get_setting("send_message_queue", None)
        if queue is None:
            self.operation_hint.setText("后台采集服务未连接，操作未发送。")
            return False
        try:
            queue.put(ObjectQueueItem(origin="ExperimentControlPage", to="main_monitor_data", title=title, data=data, time=time.strftime("%Y-%m-%d %H:%M:%S")))
        except Exception as error:
            self.operation_hint.setText(f"后台命令发送失败：{error}")
            return False
        return True

    def _refresh_cage_controls(self) -> None:
        setting = global_setting.get_setting("experiment_setting", None)
        detection = global_setting.get_setting("mouse_cage_detect_state_dict", {})
        cages = configurable_cages(
            setting, detection,
            bool(global_setting.get_setting("environment_module_only", False)),
        )
        ready = bool(global_setting.get_setting("port", None)) and global_setting.get_setting("send_message_queue", None) is not None
        for cage, (row, environment_widgets, food_widgets) in self.cage_control_rows.items():
            available = cages.get(cage)
            row.setVisible(bool(available and any(available.values())))
            for widget in environment_widgets:
                widget.setVisible(bool(available and available["environment"]))
                widget.setEnabled(ready)
            for widget in food_widgets:
                widget.setVisible(bool(available and available["food"]))
                widget.setEnabled(ready)
        if not cages:
            self.cage_control_hint.setText("暂无可配置鼠笼，请先在“实验配置”中确认串口并完成检测。")
        elif not ready:
            self.cage_control_hint.setText("已检测到可配置鼠笼；请先确认串口和采集服务。")
        else:
            self.cage_control_hint.setText("仅显示已启用且设备检测通过的鼠笼。")

    def _send_cage_control(self, cage: int, control: str, value: Any) -> None:
        setting = global_setting.get_setting("experiment_setting", None)
        detection = global_setting.get_setting("mouse_cage_detect_state_dict", {})
        cages = configurable_cages(
            setting, detection,
            bool(global_setting.get_setting("environment_module_only", False)),
        )
        needed = "food" if control == "food_door" else "environment"
        if not cages.get(cage, {}).get(needed, False):
            self.cage_control_feedback.setText("操作未发送：该笼子未启用、未通过检测或相应模块未启用。")
            return
        if not global_setting.get_setting("port", None):
            self.cage_control_feedback.setText("操作未发送：请先确认串口。")
            return
        queue = global_setting.get_setting("send_message_queue", None)
        if queue is None:
            self.cage_control_feedback.setText("操作未发送：后台采集服务未连接。")
            return

        request_id = uuid.uuid4().hex
        self._pending_cage_controls[request_id] = (cage, control, value)
        try:
            queue.put(ObjectQueueItem(
                origin="ExperimentControlPage", to="main_monitor_data", title="cage_control",
                data={
                    "cage_number": cage,
                    "control": control,
                    "value": value,
                    "request_id": request_id,
                },
                time=time.strftime("%Y-%m-%d %H:%M:%S"),
            ))
        except Exception as error:
            self._pending_cage_controls.pop(request_id, None)
            self.cage_control_feedback.setText(f"操作未发送：{error}")
            return
        self.cage_control_feedback.setStyleSheet("color:#956000;")
        self.cage_control_feedback.setText(f"CH{cage:02d} 操作请求已发送，等待设备响应。")

    def on_cage_control_response(self, response: Mapping[str, Any] | None) -> None:
        """Show the device acknowledgement instead of leaving the UI pending."""
        payload = dict(response) if isinstance(response, Mapping) else {}
        request_id = str(payload.get("request_id") or "")
        pending = self._pending_cage_controls.pop(request_id, None) if request_id else None
        # Ignore a late acknowledgement for a request that has already been
        # superseded/cleared, so an old response cannot overwrite a newer one.
        if request_id and pending is None:
            return

        pending_cage, pending_control, pending_value = pending or (None, None, None)
        try:
            cage = int(payload.get("cage_number") or pending_cage)
        except (TypeError, ValueError):
            return
        control = str(payload.get("control") or pending_control or "")
        value = payload.get("value", pending_value)
        success = bool(payload.get("success", False))
        action_labels = {
            ("wheel_brake", "enable"): "启用跑轮刹车",
            ("wheel_brake", "release"): "解除跑轮刹车",
            ("light", "on"): "开灯",
            ("light", "off"): "关灯",
            ("food_door", "open"): "开食槽门",
            ("food_door", "close"): "关食槽门",
            ("light_color", None): "设置光照色温",
            ("light_brightness", None): "设置光照亮度",
        }
        action = action_labels.get((control, value)) or action_labels.get((control, None)) or control
        if success:
            text = f"CH{cage:02d} {action}完成，设备已成功响应。"
            self.cage_control_feedback.setStyleSheet("color:#16803c;")
        else:
            detail = str(payload.get("message") or "设备未返回有效响应")
            text = f"CH{cage:02d} {action}失败：{detail}"
            self.cage_control_feedback.setStyleSheet("color:#b42318;")
        self.cage_control_feedback.setText(text)

        button = self.cage_control_buttons.get((cage, control, value))
        if button is not None:
            button.setToolTip(text)
        slider = self.cage_control_sliders.get((cage, control))
        if success and slider is not None:
            try:
                slider.blockSignals(True)
                slider.setValue(int(value))
            except (TypeError, ValueError):
                pass
            finally:
                slider.blockSignals(False)

    def _open_calibration_details(self, calibration_type: str | None) -> bool:
        show_details = getattr(self.main_window, "show_calibration_windows", None)
        if not callable(show_details):
            self.operation_hint.setText("校准详情窗口不可用。")
            return False
        try:
            show_details({"type": calibration_type, "time": time.strftime("%Y-%m-%d %H:%M:%S")})
        except Exception as error:
            self.operation_hint.setText(f"打开校准详情失败：{error}")
            return False
        return True

    def _calibration_ready(self, for_stop: bool = False) -> bool:
        if self._last_run_status not in {"running", "monitoring"} and not (for_stop and getattr(self, "_last_can_stop", False)):
            self.operation_hint.setText("请等待实验启动完成后再执行校零或量程校准。")
            return False
        if global_setting.get_setting("environment_module_only", False):
            self.operation_hint.setText("仅环境模块模式不运行气路，无法执行气体校准。")
            return False
        if not for_stop and not global_setting.get_setting("air_modules_all_valid", False):
            self.operation_hint.setText("请先确认串口并完成检测，UFC、UGC、ZOS 全部通过后才能校准。")
            return False
        if global_setting.get_setting("send_message_queue", None) is None:
            self.operation_hint.setText("后台采集服务未连接，操作未发送。")
            return False
        return True

    def _start_calibration(self, key: str) -> None:
        # Before the experiment starts, these buttons select the startup workflow.
        # The backend performs the selected calibration in the normal start chain.
        if self._last_run_status not in {"running", "monitoring"}:
            self._select_startup_calibration(key)
            return
        if not self._calibration_ready():
            return
        details_type, command, label, channels = {
            "zero": ("zero_calibration", "start_zero_calibration", "校零", {"zero"}),
            "span": ("span_calibration", "start_span_calibration", "校量程", {"span"}),
            "all": ("all_calibration", "start_calibration", "校零且量程", {"zero", "span"}),
        }[key]
        if self._active_calibrations:
            active_labels = "、".join("校零" if channel == "zero" else "校量程" for channel in sorted(self._active_calibrations))
            self.operation_hint.setText(f"{active_labels}正在进行，请等待完成后再操作。")
            return
        if not self._open_calibration_details(details_type):
            return
        if not self._send_monitor_command(command):
            return
        self._active_calibrations.update(channels)
        self._stopping_calibrations.difference_update(channels)
        if key == "all":
            self._combined_calibration = True
        self._update_calibration_buttons()
        self.set_operation_progress(0, f"{label}请求已发送")
        self.operation_hint.setText(f"{label}已发送到气路服务，等待实际校准事件。")

    def _select_startup_calibration(self, key: str):
        if self._last_run_status in {"starting", "running", "monitoring", "stopping"} or getattr(self, "_last_can_stop", False):
            self.operation_hint.setText("校准方式仅支持在开始实验前选择。")
            return
        if global_setting.get_setting("environment_module_only", False):
            self.operation_hint.setText("仅环境模块模式不运行气路，无法选择气体校准。")
            return
        if not global_setting.get_setting("air_modules_all_valid", False):
            self.operation_hint.setText("请先确认串口并完成检测，UFC、UGC、ZOS 全部通过后才能选择校准方式。")
            return
        mode, label = {
            "zero": ("zero", "校零"),
            "span": ("span", "校量程"),
            "all": ("full", "校零且量程"),
        }[key]
        current_mode = global_setting.get_setting("startup_calibration_mode", "none")
        if current_mode == mode:
            self.calibration_status.setText(f"已选择{label}")
            self.operation_hint.setText(f"已选择{label}，请点击“开始实验”进行校准。")
            return
        global_setting.set_setting("startup_calibration_mode", mode)
        global_setting.set_setting("is_auto_calibration", True)
        global_setting.set_setting("device_config_calibration_selected", True)
        selection_signal = getattr(self.main_window, "calibration_selection_changed_signal", None)
        if selection_signal is not None:
            selection_signal.emit(True, mode)
        self.calibration_status.setText(f"已选择{label}")
        self.operation_hint.setText(f"已选择{label}，请点击“开始实验”进行校准。")
        self._update_calibration_buttons()
        QMessageBox.information(self, "选择校准方式", f"已选择{label}，请点击“开始实验”进行校准。")

    def _stop_calibration(self, key: str) -> None:
        if not self._active_calibrations or not self._calibration_ready(for_stop=True):
            return
        command, label, channels = {
            "stop_zero": ("stop_zero_calibration", "停止校零", {"zero"}),
            "stop_span": ("stop_span_calibration", "停止校量程", {"span"}),
            "stop_all": ("stop_calibration", "停止校零且量程", {"zero", "span"}),
        }[key]
        if channels & self._stopping_calibrations:
            self.operation_hint.setText(f"{label}停止请求已发送，请等待完成。")
            return
        if not self._send_monitor_command(command):
            return
        self._stopping_calibrations.update(channels & self._active_calibrations)
        self._update_calibration_buttons()
        self.set_operation_progress(0, f"{label}请求已发送")
        self.operation_hint.setText(f"{label}已发送到气路服务，等待停止完成事件。")

    def _update_calibration_buttons(self) -> None:
        gas_enabled = not global_setting.get_setting("environment_module_only", False)
        gas_valid = bool(global_setting.get_setting("air_modules_all_valid", False))
        ready = gas_enabled and gas_valid and self._last_run_status in {"running", "monitoring"}
        startup_ready = (
            gas_enabled and gas_valid
            and self._last_run_status not in {"starting", "running", "monitoring", "stopping"}
            and not getattr(self, "_last_can_stop", False)
        )
        can_stop = gas_enabled and (self._last_run_status in {"running", "monitoring"} or getattr(self, "_last_can_stop", False))
        active = self._active_calibrations
        stopping = self._stopping_calibrations
        for channel, title in (("zero", "校零"), ("span", "校量程")):
            self.calibration_buttons[channel].setEnabled(
                (ready and not active) or (startup_ready and not active)
            )
            self.calibration_buttons[channel].setText(f"正在{title}中" if channel in active else title)
            stop_button = self.calibration_buttons[f"stop_{channel}"]
            stop_button.setEnabled(can_stop and channel in active and channel not in stopping)
            stop_button.setText(f"正在停止{title}中" if channel in stopping else f"停止{title}")
        self.calibration_buttons["all"].setEnabled((ready or startup_ready) and not active)
        self.calibration_buttons["all"].setText("正在校零且量程中" if self._combined_calibration else "校零且量程")
        self.calibration_buttons["stop_all"].setEnabled(can_stop and bool(active) and not stopping)
        self.calibration_buttons["stop_all"].setText("正在停止校零且量程中" if stopping == {"zero", "span"} else "停止校零且量程")
        self.calibration_buttons["air"].setEnabled(
            gas_enabled and gas_valid and self._last_run_status not in {"starting", "running", "monitoring", "stopping"}
            and not getattr(self, "_last_can_stop", False)
        )

    def on_calibration_event(self, event: str) -> None:
        """React to the same zero/span start and finish events used by the legacy UI."""
        channel, _, phase = event.partition("_")
        if channel not in {"zero", "span"}:
            return
        if phase == "started":
            self._active_calibrations.add(channel)
            self.set_operation_progress(5, "校零进行中" if channel == "zero" else "校量程进行中")
        elif phase == "finished":
            self._active_calibrations.discard(channel)
            self._stopping_calibrations.discard(channel)
            if not self._active_calibrations:
                self._combined_calibration = False
        self._update_calibration_buttons()

    def _start_air_calibration(self) -> None:
        """Select startup Air calibration; execute it when the experiment starts."""
        if self._last_run_status in {"starting", "running", "monitoring", "stopping"} or getattr(self, "_last_can_stop", False):
            self.operation_hint.setText("空气校准仅支持在开始实验前选择。")
            return
        if global_setting.get_setting("environment_module_only", False):
            self.operation_hint.setText("仅环境模块模式不运行气路，无法选择空气校准。")
            return
        if not global_setting.get_setting("air_modules_all_valid", False):
            self.operation_hint.setText("请先确认串口并完成检测，UFC、UGC、ZOS 全部通过后才能选择空气校准。")
            return

        global_setting.set_setting("startup_calibration_mode", "air")
        global_setting.set_setting("is_auto_calibration", True)
        selection_signal = getattr(self.main_window, "calibration_selection_changed_signal", None)
        if selection_signal is not None:
            selection_signal.emit(True, "air")
        self.calibration_status.setText("已选择空气校准")
        self.operation_hint.setText("已选择空气校准，请点击“开始实验”进行校准。")
        self._update_calibration_buttons()
        QMessageBox.information(
            self,
            "选择空气校准",
            "已选择空气校准，请点击“开始实验”进行校准。",
        )
    def _start_experiment(self) -> None:
        if callable(getattr(self.main_window, "start_experiment", None)):
            self.set_operation_progress(0, "正在检查启动条件")
            self.operation_hint.setText("正在检查实验配置和后台连接...")
            self.start_button.setEnabled(False)
            try:
                self.main_window.start_experiment()
            except Exception as error:
                failure_handler = getattr(self.main_window, "_mark_workflow_operation_failed", None)
                if callable(failure_handler):
                    failure_handler("start", error)
                else:
                    self.set_operation_progress(0, "启动失败")
                    self.operation_hint.setText(f"启动失败：{error}")
                    self.start_button.setEnabled(True)
        else:
            self.operation_hint.setText("主窗口启动入口不可用。")

    def _stop_experiment(self) -> None:
        if QMessageBox.question(self, "确认终止实验", "终止后将停止采集并保存当前数据，是否继续？", QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        self._operation_base_text = "当前操作：终止实验"
        self.operation_label.setText(self._operation_base_text)
        if callable(getattr(self.main_window, "stop_experiment", None)):
            self.main_window.stop_experiment()
        else:
            self.operation_hint.setText("主窗口终止入口不可用。")

    def refresh(self) -> None:
        context = _provider_value(self._context_provider)
        self._refresh_cage_controls()
        raw_status = getattr(
            context.get("experiment_status", "unknown"),
            "value",
            context.get("experiment_status", "unknown"),
        )
        status = str(raw_status or "unknown").strip().lower()
        status_labels = {"not_started":"未开始", "initialized":"未开始", "configuring":"配置中", "starting":"启动中", "running":"运行中", "monitoring":"运行中", "stopping":"停止中", "stopped":"已停止", "start_failed":"启动失败", "stop_failed":"停止失败", "failed":"操作失败"}
        self.status_value.setText(status_labels.get(status, UNKNOWN))
        self.duration_value.setText(format_duration(context.get("elapsed_seconds")))
        countdown_seconds = context.get("operation_countdown_seconds")
        countdown_phase = context.get("operation_countdown_phase")
        if countdown_seconds is not None and countdown_phase in {"启动", "停止"}:
            self.operation_label.setText(
                f"{self._operation_base_text} ｜ {countdown_phase}剩余时间：{int(countdown_seconds)}秒"
            )
        else:
            self.operation_label.setText(self._operation_base_text)
        # Do not overwrite real backend calibration progress on every refresh.
        if status != self._last_run_status and status in {"running", "monitoring"}:
            self.set_operation_progress(100, "实验已启动")
        if status != self._last_run_status and status in {"not_started", "initialized", "configuring", "stopped"}:
            self._active_calibrations.clear()
            self._stopping_calibrations.clear()
            self._combined_calibration = False
        self._last_run_status = status
        running = status in {"running", "monitoring", "starting"}
        can_stop = bool(context.get("can_stop_experiment", False))
        self._last_can_stop = can_stop
        self.start_button.setEnabled(not running and not can_stop and status != "stopping")
        self.stop_button.setEnabled((running or can_stop) and status != "stopping")
        self._update_calibration_buttons()
        health_snapshot = _provider_value(self._health_source)
        items = [item for item in (health_snapshot.get("components") or []) if isinstance(item, Mapping)]
        alerts = [item for item in (health_snapshot.get("alerts") or []) if isinstance(item, Mapping)]
        if not alerts:
            alerts = [
                item for item in items
                if str(item.get("status", item.get("state", "unknown"))).lower()
                in {"warning", "fault", "unavailable"}
                and bool(item.get("enabled", True))
            ]
        channel_issues = derive_channel_status_issues(context, items, alerts)
        self.health_value.setText(health_summary_text(context, items, alerts, channel_issues))
        metrics: dict[str, Any] = {}
        for item in items:
            metrics.update(dict(item.get("metrics") or {}))
            if item.get("component") in {"gas_path", "ufc_ugc_zos", "gas"}:
                self.calibration_status.setText(_text(item.get("message"), "待校准"))
        # No synthetic normal state: each card is rendered from its matching
        # health snapshot and stays unknown when the subsystem is not enabled.
        component_aliases = {
            "monitor_collection": "collection", "monitor_data": "collection",
            "data_collection": "collection", "gas_path": "gas_path",
            "ufc_ugc_zos": "gas_path", "environment": "environment",
            "temperature_control": "environment", "trajectory_camera": "trajectory",
            "trajectory_service": "trajectory", "deep_camera": "trajectory", "infrared_camera": "infrared",
            "behavior_recognition": "behavior", "recognition": "behavior", "yolo_behavior": "behavior",
            "storage": "storage", "database": "storage", "data_storage": "storage",
        }
        for key, label in self.system_info_labels.items():
            label.setText(UNKNOWN)
            self.system_info_details[key].setText("未收到健康快照")
        for item in items:
            key = component_aliases.get(str(item.get("component", "")))
            if key not in self.system_info_labels:
                continue
            state = str(item.get("status", item.get("state", "unknown"))).lower()
            text = {"normal": "在线", "warning": "告警", "fault": "故障", "unavailable": "不可用"}.get(state, UNKNOWN)
            if key == "behavior":
                class_count = (item.get("metrics") or {}).get("class_count")
                if class_count is not None:
                    text += f"；类别数：{class_count}"
            if item.get("message"):
                text += f"：{item.get('message')}"
            self.system_info_labels[key].setText(text)
            self.system_info_details[key].setText(
                f"最近成功：{_text(item.get('last_success_at'))}；连续失败：{_text(item.get('consecutive_failures'), '0')}"
            )
            item_metrics = item.get("metrics") or {}
            explicit_progress = item_metrics.get("progress_percent", item_metrics.get("progress"))
            if explicit_progress is not None:
                try:
                    self.set_operation_progress(int(float(explicit_progress)))
                except (TypeError, ValueError):
                    pass
        evidence = "；".join(
            f"{title}={_text(metrics.get(key))}"
            for key, title in (("flow", "采样流量"), ("oxygen", "O₂"),
                               ("co2", "CO₂"), ("pressure", "气室压差"),
                               ("temperature", "气室温度"))
        )
        self.calibration_evidence.setText(f"校准读数：{evidence}")
        logs = context.get("main_logs") or []
        self.main_log_list.setRowCount(0)
        for entry in list(logs)[-30:]:
            if isinstance(entry, Mapping):
                stamp, message = entry.get("time", ""), entry.get("message", "")
                level = entry.get("level", entry.get("type", "信息"))
            elif isinstance(entry, (tuple, list)) and len(entry) >= 2:
                stamp, message = entry[0], entry[1]
                level = entry[2] if len(entry) >= 3 else "信息"
            else:
                stamp, message = "", entry
                level = "信息"
            row = self.main_log_list.rowCount(); self.main_log_list.insertRow(row)
            stamp_item = QTableWidgetItem(_text(stamp, ""))
            stamp_item.setBackground(QColor("#eef6ff"))
            self.main_log_list.setItem(row, 0, stamp_item)
            level_label = QLabel(_text(level, "信息"))
            level_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            level_label.setProperty("chip", True)
            level_label.setProperty("tone", "info")
            level_label.setStyleSheet("background:#e2f1ff;color:#156fc1;border-radius:8px;padding:2px 5px;font-size:10px;font-weight:700;")
            self.main_log_list.setCellWidget(row, 1, level_label)
            message_item = QTableWidgetItem(_text(message, ""))
            message_item.setBackground(QColor("#eef6ff"))
            self.main_log_list.setItem(row, 2, message_item)
            self.main_log_list.setRowHeight(row, 32)
        if self.main_log_list.rowCount():
            self.main_log_list.scrollToBottom()
        self.refreshed.emit()


class DataViewPage(_BaseWorkflowPage):
    """P4 automatic monitoring of the current experiment's Epoch rows."""

    def __init__(self, context_provider: Any = None, health_source: Any = None, parent=None):
        self._last_export_directory: Optional[str] = None
        self._last_resolved_db_path: Optional[str] = None
        super().__init__(context_provider, health_source, parent)
        self._build_ui()
        self.start_refresh()

    def _build_ui(self) -> None:
        _scroll, layout = self._canvas()
        self._header(layout, "LOCAL DATA", "本地实验数据监控", "自动读取当前实验 SQLite 监测记录；没有真实记录时保持空白。")
        filter_card = QFrame(); filter_card.setProperty("card", True)
        filter_box = QHBoxLayout(filter_card); filter_box.setContentsMargins(12, 10, 12, 10)
        filter_box.addWidget(QLabel("通道"))
        self.channel_filter_buttons: list[QPushButton] = []
        self.channel_filter_group = QButtonGroup(self)
        self.channel_filter_group.setExclusive(True)
        self._selected_channel_filter = -1
        # Keep the legacy combo hidden for integrations that still read it;
        # the visible control is the screenshot-compatible button row.
        self.channel_filter = QComboBox(self); self.channel_filter.addItem("全部", -1)
        for channel in range(1, 9): self.channel_filter.addItem(f"CH{channel:02d}", channel)
        self.channel_filter.currentIndexChanged.connect(
            lambda _index: self._select_channel_filter(int(self.channel_filter.currentData() or -1))
        )
        self.channel_filter.hide()
        for value, text in [(-1, "全部"), *[(channel, f"通道{channel}") for channel in range(1, 9)]]:
            button = QPushButton(text)
            button.setCheckable(True)
            button.setChecked(value == -1)
            button.clicked.connect(lambda _checked=False, selected=value: self._select_channel_filter(selected))
            self.channel_filter_group.addButton(button, value)
            self.channel_filter_buttons.append(button)
            filter_box.addWidget(button)
        self.reload_button = QPushButton("刷新")
        self.reload_button.setToolTip("直接读取当前实验本地数据库，无需确认通道")
        self.reload_button.clicked.connect(self.refresh)
        filter_box.addWidget(self.reload_button)
        self.export_button = QPushButton("导出当前查询")
        self.export_button.setToolTip("选择保存位置，将当前筛选结果导出为 CSV")
        self.export_button.clicked.connect(self.export_current_query)
        filter_box.addWidget(self.export_button)
        self.fill_note = QLabel("每 2 秒自动刷新；VO₂、VCO₂、RER、EE 只在参考气与样本数据有效时计算。")
        self.fill_note.setProperty("hint", True); filter_box.addWidget(self.fill_note, 1)
        layout.addWidget(filter_card)
        self.status_label = QLabel("暂无本地数据"); self.status_label.setProperty("hint", True); layout.addWidget(self.status_label)
        self.table = QTableWidget(0, 0); self.table.setAlternatingRowColors(True); self.table.setSortingEnabled(True); layout.addWidget(self.table, 1)

    def _select_channel_filter(self, channel: int) -> None:
        self._selected_channel_filter = channel
        for index, button in enumerate(self.channel_filter_buttons):
            button.setChecked((index == 0 and channel == -1) or (index == channel and channel > 0))
        self.channel_filter.blockSignals(True)
        self.channel_filter.setCurrentIndex(0 if channel == -1 else channel)
        self.channel_filter.blockSignals(False)
        self.refresh()

    def _db_path(self) -> Optional[str]:
        context = _provider_value(self._context_provider)
        path = context.get("experiment_db_path")
        if path and os.path.isfile(path):
            self._last_resolved_db_path = os.path.abspath(str(path))
            return self._last_resolved_db_path

        root_path = context.get("experiment_root_path")
        if root_path:
            candidate = os.path.join(str(root_path), "data", "data.db")
            if os.path.isfile(candidate):
                self._last_resolved_db_path = os.path.abspath(candidate)
                return self._last_resolved_db_path

        # The collection process is the source of truth for the directory
        # name.  If the GUI's predicted path differs (for example an old
        # template with an empty experiment prefix), locate the database by
        # the exact experiment start timestamp instead of showing an empty
        # page or falling back to an unrelated older experiment.
        started_at = context.get("started_at")
        if started_at in (None, ""):
            started_at = global_setting.get_setting("start_experiment_time", None)
        try:
            started_at = float(started_at)
        except (TypeError, ValueError):
            started_at = None
        if started_at and started_at > 0:
            storage = global_setting.get_setting("monitor_data", {}).get("STORAGE", {})
            storage_root = str(resolve_project_path(
                Path(str(storage.get("fold_path", "./data/")))
                / str(storage.get("sub_fold_path", "monitor_data")).strip("/\\")
            ))
            best: tuple[float, str] | None = None
            if os.path.isdir(storage_root):
                timestamp_pattern = re.compile(
                    r"20\d{2}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}_\d{3}(?!\d)"
                )
                try:
                    entries = os.scandir(storage_root)
                except OSError:
                    entries = None
                if entries is not None:
                    with entries:
                        for entry in entries:
                            if not entry.is_dir(follow_symlinks=False):
                                continue
                            match = timestamp_pattern.search(entry.name)
                            if match is None:
                                continue
                            try:
                                folder_started_at = datetime.strptime(
                                    match.group(), "%Y_%m_%d_%H_%M_%S_%f"
                                ).timestamp()
                            except ValueError:
                                continue
                            difference = abs(folder_started_at - started_at)
                            candidate = os.path.join(entry.path, "data", "data.db")
                            if difference <= 10.0 and os.path.isfile(candidate):
                                if best is None or difference < best[0]:
                                    best = (difference, os.path.abspath(candidate))
            if best is not None:
                self._last_resolved_db_path = best[1]
                return best[1]
            return None

        if self._last_resolved_db_path and os.path.isfile(self._last_resolved_db_path):
            return self._last_resolved_db_path
        return None

    @staticmethod
    def _epoch_tables(connection: sqlite3.Connection) -> list[str]:
        rows = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'Epoch_data_cage_%' ORDER BY name").fetchall()
        return [str(row[0]) for row in rows if re.fullmatch(r"Epoch_data_cage_\d+", str(row[0]))]

    @staticmethod
    def _cage_display_name(value: Any) -> str:
        """Keep the local-data table's cage naming consistent with the old monitor."""
        try:
            if int(value) == 0:
                return "参考笼"
        except (TypeError, ValueError):
            pass
        return _text(value, "None")

    @staticmethod
    def _row_has_remark_error(headers: Sequence[str], row: Sequence[Any]) -> bool:
        """Mirror the old monitor: a substantive remarks value marks the whole row red."""
        for index, header in enumerate(headers):
            normalized = str(header or "").strip().lower()
            if normalized not in {"remarks", "备注"} and "备注" not in normalized:
                continue
            if index >= len(row):
                return False
            value = row[index]
            return value is not None and len(str(value).strip()) > 3
        return False

    @staticmethod
    def _epoch_rows(connection: sqlite3.Connection, selected: int, db_path: Optional[str] = None) -> tuple[list[str], list[tuple[Any, ...]]]:
        """Use the old monitor's one-row-per-cage Epoch source, not raw-table unions."""
        cage_tables = DataViewPage._epoch_tables(connection)
        if selected not in (None, -1):
            tables = [name for name in cage_tables if _table_matches_channel(name, int(selected))]
        else:
            all_exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='Epoch_data_all'"
            ).fetchone()
            all_has_rows = all_exists and connection.execute("SELECT 1 FROM Epoch_data_all LIMIT 1").fetchone()
            tables = ["Epoch_data_all"] if all_has_rows else cage_tables

        # Real experiment databases have the legacy Epoch schema and metadata.
        # Read those with exactly the same DAO as the old monitoring table;
        # retain the direct reader only for older/partial databases without it.
        if db_path and len(tables) == 1:
            table = tables[0]
            quoted = '"' + table.replace('"', '""') + '"'
            columns = {str(row[1]) for row in connection.execute(f"PRAGMA table_info({quoted})")}
            has_meta = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (f"{table}_meta",),
            ).fetchone()
            if "time" in columns and has_meta:
                return DataViewPage._legacy_epoch_rows(db_path, table)

        definitions = Others_Tables.Epoch_Data.value["data"]["column"]
        descriptions = {name: description for name, description, _type in definitions}
        records: list[tuple[str, dict[str, Any]]] = []
        column_names: list[str] = []
        for name in tables:
            quoted_name = '"' + name.replace('"', '""') + '"'
            columns = [str(row[1]) for row in connection.execute(f"PRAGMA table_info({quoted_name})")]
            if not columns:
                continue
            column_names.extend(column for column in columns if column not in column_names)
            order_column = '"id"' if "id" in columns else "rowid"
            for values in connection.execute(
                f"SELECT * FROM {quoted_name} ORDER BY {order_column} DESC LIMIT 200"
            ).fetchall():
                record = dict(zip(columns, values))
                cage_number = record.get("mouse_cage_number") if name == "Epoch_data_all" else name.rsplit("_", 1)[-1]
                records.append((str(cage_number), record))
        if not records:
            return [], []
        if len(tables) > 1:
            records.sort(key=lambda item: str(item[1].get("time") or ""), reverse=True)
            records = records[:200]
        headers = [descriptions.get(column, column) for column in column_names]
        rows = []
        for _cage_number, record in records:
            values = (
                DataViewPage._cage_display_name(record.get(column))
                if column == "mouse_cage_number"
                else record.get(column)
                for column in column_names
            )
            rows.append(tuple(values))
        return headers, rows

    @staticmethod
    def _legacy_epoch_rows(db_path: str, table: str) -> tuple[list[str], list[tuple[Any, ...]]]:
        """Project the old monitor DAO result without changing the new table UI."""
        handle = Monitor_Datas_Handle(db_name=db_path)
        try:
            gid = -1 if table == "Epoch_data_all" else int(table.rsplit("_", 1)[-1])
            result = handle.query_epoch_data_all_tables_paging(
                gid=gid, page=1, page_size=200, all_column_datas=["all"],
            )
            columns = list(result.get("columns", []))
            records = list(result.get("rows", []))
            if not records:
                return [], []
            titles = list(result.get("columns_title", columns))
            config = load_weight_postprocess_config()
            if config.display_fitted_weight and EPOCH_WEIGHT_COLUMN_KEY in columns:
                history = handle.query_epoch_weight_rows(gid)
                fitted = fit_epoch_weight_rows(
                    history,
                    initial_weights=DataViewPage._legacy_initial_weights(),
                    config=config,
                    fallback_cage=gid if gid != -1 else None,
                ) if history else {}
                for record in records:
                    key = weight_row_key(record, fallback_cage=gid if gid != -1 else None)
                    if key in fitted:
                        record[EPOCH_WEIGHT_COLUMN_KEY] = fitted[key]
            rows = []
            for record in records:
                values = (
                    DataViewPage._cage_display_name(record.get(column))
                    if column == "mouse_cage_number"
                    else record.get(column)
                    for column in columns
                )
                rows.append(tuple(values))
            return titles, rows
        finally:
            handle.stop()

    @staticmethod
    def _legacy_initial_weights() -> dict[str, Any]:
        setting = global_setting.get_setting("experiment_setting", None)
        configured = dict(getattr(setting, "pre_experiment_weights", {}) or {})
        for group in getattr(setting, "groups", []) or []:
            group_id = getattr(group, "id", None)
            group_name = str(getattr(group, "name", "") or "").strip()
            weight = configured.get(str(group_id), configured.get(group_id))
            if weight is not None and group_name:
                configured[group_name] = weight
        return configured

    def _capture_table_anchor(self) -> tuple[Optional[tuple[str, ...]], int]:
        scroll_bar = self.table.verticalScrollBar()
        if self.table.rowCount() <= 0:
            return None, scroll_bar.value()
        top_row = self.table.rowAt(0)
        if top_row < 0:
            top_row = 0
        signature = tuple(
            self.table.item(top_row, column).text()
            if self.table.item(top_row, column) is not None
            else ""
            for column in range(self.table.columnCount())
        )
        return signature, scroll_bar.value()

    def _restore_table_anchor(
        self,
        signature: Optional[tuple[str, ...]],
        previous_scroll_value: int,
    ) -> None:
        if signature is not None:
            for row in range(self.table.rowCount()):
                candidate = tuple(
                    self.table.item(row, column).text()
                    if self.table.item(row, column) is not None
                    else ""
                    for column in range(self.table.columnCount())
                )
                if candidate == signature:
                    item = self.table.item(row, 0)
                    if item is not None:
                        self.table.scrollToItem(
                            item,
                            QAbstractItemView.ScrollHint.PositionAtTop,
                        )
                    return
        scroll_bar = self.table.verticalScrollBar()
        scroll_bar.setValue(min(previous_scroll_value, scroll_bar.maximum()))

    def refresh(self) -> None:
        anchor_signature, previous_scroll_value = self._capture_table_anchor()
        db_path = self._db_path()
        if not db_path:
            state = _provider_value(self._context_provider).get("experiment_status")
            message = (
                "实验已启动，等待采集进程创建本地数据库；页面将自动显示新数据"
                if state in {"starting", "running"}
                else "未开始实验或暂无本地数据库"
            )
            self.table.clear(); self.table.setRowCount(0); self.table.setColumnCount(0); self.status_label.setText(message)
            self.refreshed.emit(); return
        try:
            with sqlite3.connect(db_path) as connection:
                selected = self._selected_channel_filter
                headers, rows = self._epoch_rows(connection, selected, db_path)
                rows = list(reversed(rows))
                self.table.setSortingEnabled(False); self.table.clear(); self.table.setColumnCount(len(headers)); self.table.setHorizontalHeaderLabels(headers); self.table.setRowCount(len(rows))
                for r, row in enumerate(rows):
                    highlight_error = self._row_has_remark_error(headers, row)
                    for c, value in enumerate(row):
                        header = str(headers[c] if c < len(headers) else "").strip().lower()
                        is_remarks = header in {"remarks", "remark", "备注"} or "备注" in header
                        if is_remarks:
                            display_value = "" if value is None or (isinstance(value, str) and value.strip().lower() in {"", "none", "null"}) else str(value)
                        else:
                            display_value = _text(value, "None")
                        item = QTableWidgetItem(display_value)
                        if highlight_error:
                            item.setForeground(QBrush(QColor(255, 0, 0)))
                        self.table.setItem(r, c, item)
                self.table.resizeColumnsToContents()
                self.table.horizontalHeader().setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
                self.table.setSortingEnabled(True)
                self._restore_table_anchor(anchor_signature, previous_scroll_value)
                selected_label = "全部" if selected in (None, -1) else f"CH{int(selected):02d}"
                self.status_label.setText(f"当前筛选：{selected_label}；读取 {len(rows)} 条监测记录，数据源：{db_path}")
        except (OSError, sqlite3.Error) as error:
            self.status_label.setText(f"数据库读取失败：{error}")
        self.refreshed.emit()

    def export_current_query(self) -> bool:
        if self.table.columnCount() == 0 or self.table.rowCount() == 0:
            self.status_label.setText("当前没有可导出的真实数据")
            return False

        selected = self._selected_channel_filter
        channel_label = "全部" if selected in (None, -1) else f"CH{int(selected):02d}"
        default_directory = self._last_export_directory
        if not default_directory:
            db_path = self._db_path()
            default_directory = os.path.dirname(db_path) if db_path else str(resolve_project_path("."))
        default_path = os.path.join(
            default_directory,
            f"采集查询_{channel_label}_{time.strftime('%Y%m%d_%H%M%S')}.csv",
        )
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "选择当前查询导出位置",
            default_path,
            "CSV 文件 (*.csv);;所有文件 (*)",
        )
        if not file_path:
            return False
        if not os.path.splitext(file_path)[1]:
            file_path = f"{file_path}.csv"
        self._last_export_directory = os.path.dirname(os.path.abspath(file_path))
        try:
            with open(file_path, "w", newline="", encoding="utf-8-sig") as stream:
                writer = csv.writer(stream)
                writer.writerow([
                    self.table.horizontalHeaderItem(index).text()
                    if self.table.horizontalHeaderItem(index) is not None else ""
                    for index in range(self.table.columnCount())
                ])
                for row in range(self.table.rowCount()):
                    writer.writerow([
                        self.table.item(row, column).text()
                        if self.table.item(row, column) is not None else ""
                        for column in range(self.table.columnCount())
                    ])
            self.status_label.setText(f"已导出当前查询：{file_path}")
            return True
        except OSError as error:
            self.status_label.setText(f"导出失败：{error}")
            return False


class TrajectoryCanvas(QWidget):
    """Dependency-free 3D cage projection used by P5/P6.

    The trajectory service persists physical XYZ points.  Both pages use this
    same fixed oblique top-down camera and the calibrated cage dimensions, so
    the cage proportions and viewing angle cannot drift between P5 and P6.
    """

    VIEW_AZIMUTH_DEG = 35.0
    VIEW_ELEVATION_DEG = 28.0

    def __init__(self, parent=None):
        super().__init__(parent); self.points: list[tuple[float, float, float]] = []; self.segments: list[list[tuple[float, float, float]]] = []; self.heatmap: list[tuple[float, float, int]] = []; self.mode = "trajectory"; self.setMinimumHeight(260)

    def set_points(self, points: Iterable[tuple[float, float, float]]) -> None:
        self.points = list(points); self.segments = [list(self.points)] if self.points else []; self.mode = "trajectory"; self.update()

    def set_segmented_points(self, samples: Iterable[Any]) -> None:
        """Build continuous trajectory pieces; ``None`` means a data gap."""
        segments: list[list[tuple[float, float, float]]] = []
        current: list[tuple[float, float, float]] = []
        for sample in samples:
            if sample is None:
                if current:
                    segments.append(current)
                    current = []
                continue
            point = sample.get("point") if isinstance(sample, Mapping) else sample
            break_before = bool(sample.get("break_before")) if isinstance(sample, Mapping) else False
            if break_before and current:
                segments.append(current)
                current = []
            try:
                parsed = (float(point[0]), float(point[1]), float(point[2]))
            except (TypeError, ValueError, IndexError):
                if current:
                    segments.append(current)
                    current = []
                continue
            current.append(parsed)
        if current:
            segments.append(current)
        self.segments = segments
        self.points = [point for segment in segments for point in segment]
        self.mode = "trajectory"
        self.update()

    def set_heatmap(self, cells: Iterable[tuple[float, float, int]]) -> None:
        self.heatmap = list(cells); self.mode = "heatmap"; self.update()

    @staticmethod
    def _cage_corners() -> list[tuple[float, float, float]]:
        half_width = float(CAGE_WIDTH_MM) / 2.0
        return [
            (x, y, z)
            for z in (0.0, float(CAGE_HEIGHT_MM))
            for y in (0.0, float(CAGE_LENGTH_MM))
            for x in (-half_width, half_width)
        ]

    @staticmethod
    def _to_cage_mm(point: tuple[float, float, float]) -> tuple[float, float, float]:
        """Convert metre-scale camera output and clamp it to the real cage."""
        values = tuple(float(value) for value in point)
        # RealSense/trajectory exports may use metres while the calibration
        # model uses millimetres.  Keep already physical millimetre rows intact.
        if not isinstance(point, _PhysicalMillimeterPoint) and max(abs(value) for value in values) <= 5.0:
            values = tuple(value * 1000.0 for value in values)
        x, y, z = values
        return (
            max(-float(CAGE_WIDTH_MM) / 2.0, min(float(CAGE_WIDTH_MM) / 2.0, x)),
            max(0.0, min(float(CAGE_LENGTH_MM), y)),
            max(0.0, min(float(CAGE_HEIGHT_MM), z)),
        )

    @classmethod
    def _project_raw(cls, point: tuple[float, float, float]) -> tuple[float, float]:
        x, y, z = cls._to_cage_mm(point)
        azimuth = math.radians(cls.VIEW_AZIMUTH_DEG)
        elevation = math.radians(cls.VIEW_ELEVATION_DEG)
        # Camera is above the front-right corner: Y recedes up-left and Z
        # rises vertically, producing a fixed oblique top-down projection.
        horizontal = x * math.cos(azimuth) - y * math.sin(azimuth)
        vertical = (
            x * math.sin(azimuth) * math.sin(elevation)
            + y * math.cos(azimuth) * math.sin(elevation)
            + z * math.cos(elevation)
        )
        return horizontal, vertical

    @classmethod
    def _project(cls, point: tuple[float, float, float], rect, bounds=None) -> QPointF:
        del bounds  # retained for compatibility with older callers
        cage_raw = [cls._project_raw(corner) for corner in cls._cage_corners()]
        min_u, max_u = min(item[0] for item in cage_raw), max(item[0] for item in cage_raw)
        min_v, max_v = min(item[1] for item in cage_raw), max(item[1] for item in cage_raw)
        span_u = max(max_u - min_u, 1e-9); span_v = max(max_v - min_v, 1e-9)
        scale = min(max(rect.width() - 16.0, 1.0) / span_u, max(rect.height() - 28.0, 1.0) / span_v)
        center_u = (min_u + max_u) / 2.0; center_v = (min_v + max_v) / 2.0
        raw_u, raw_v = cls._project_raw(point)
        return QPointF(
            rect.center().x() + (raw_u - center_u) * scale,
            rect.center().y() - (raw_v - center_v) * scale,
        )

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#fbfdff"))
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = self.rect().adjusted(24, 18, -24, -24)
        if self.mode == "heatmap":
            self._paint_heatmap(painter, rect)
            painter.end()
            return
        # Project the calibrated cage itself; trajectory extents never change
        # the cage scale or camera angle.
        projected = [self._project(corner, rect) for corner in self._cage_corners()]
        painter.setPen(QPen(QColor("#bad3e9"), 1, Qt.PenStyle.DashLine))
        edges = ((0,1),(0,2),(0,4),(1,3),(1,5),(2,3),(2,6),(3,7),
                 (4,5),(4,6),(5,7),(6,7))
        for first, second in edges:
            painter.drawLine(projected[first], projected[second])
        painter.setPen(QColor("#7191b2"))
        painter.drawText(
            rect.adjusted(0, 0, 0, 4),
            Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter,
            f"笼体尺寸 {float(CAGE_WIDTH_MM):g} × {float(CAGE_LENGTH_MM):g} × {float(CAGE_HEIGHT_MM):g} mm · 固定斜俯视角",
        )
        if not self.points:
            painter.setPen(QColor("#7191b2"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "暂无轨迹数据")
            painter.end()
            return
        painter.setPen(QPen(QColor("#47bd8d"), 2))
        projected_segments = [
            [self._project(point, rect) for point in segment]
            for segment in (self.segments or [self.points])
            if segment
        ]
        for trail in projected_segments:
            for first, second in zip(trail, trail[1:]):
                painter.drawLine(first, second)
            if len(trail) == 1:
                painter.drawEllipse(trail[0], 1.5, 1.5)
        first_point = projected_segments[0][0]
        last_point = projected_segments[-1][-1]
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#e69536")); painter.drawEllipse(first_point, 4, 4)
        painter.setBrush(QColor("#288ee0")); painter.drawEllipse(last_point, 4, 4)
        painter.end()

    def _paint_heatmap(self, painter, rect) -> None:
        # Screenshot-compatible rounded-cell heatmap.  Cells are derived from
        # real trajectory density; an empty database remains a blank grid.
        columns, rows = 12, 10
        gap = 5
        cell_w = max(8, int((rect.width() - (columns - 1) * gap) / columns))
        cell_h = max(8, int((rect.height() - (rows - 1) * gap) / rows))
        density = {(int(x), int(y)): int(count) for x, y, count in self.heatmap}
        maximum = max(density.values(), default=0)
        palette = (QColor("#e7eef8"), QColor("#9ec0ea"), QColor("#4e8edb"),
                   QColor("#f5bf2f"), QColor("#ed772d"), QColor("#d93632"))
        for row in range(rows):
            for column in range(columns):
                value = density.get((column, row), 0)
                index = 0 if not value or not maximum else min(len(palette) - 1, int(value / maximum * (len(palette) - 1)) + 1)
                x = rect.left() + column * (cell_w + gap)
                y = rect.top() + row * (cell_h + gap)
                painter.setBrush(palette[index]); painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(int(x), int(y), max(1, int(cell_w)), max(1, int(cell_h)), 5, 5)


class ActivityLineChart(QWidget):
    """Minimal line chart for real movement samples, with no fake fallback."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.values: list[float] = []
        self.setMinimumHeight(150)

    def set_values(self, values: Iterable[float]) -> None:
        self.values = [float(value) for value in values if _number(value) is not None]
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self); painter.fillRect(self.rect(), QColor("#fbfdff"))
        rect = self.rect().adjusted(28, 14, -16, -24)
        painter.setPen(QPen(QColor("#d8e7f3"), 1))
        painter.drawRect(rect)
        if not self.values:
            painter.setPen(QColor("#7191b2")); painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "暂无真实运动量数据"); painter.end(); return
        maximum = max(max(self.values), 1.0)
        path = QPainterPath()
        for index, value in enumerate(self.values):
            x = rect.left() + index / max(len(self.values) - 1, 1) * rect.width()
            y = rect.bottom() - value / maximum * rect.height()
            point = QPointF(x, y)
            if index == 0: path.moveTo(point)
            else: path.lineTo(point)
        painter.setPen(QPen(QColor("#ed8c21"), 2)); painter.drawPath(path); painter.end()


class BehaviorCountChart(QWidget):
    """Behaviour-count bars with action types on X and counts on Y."""

    _COLORS = (
        QColor("#1677B8"), QColor("#D95F02"), QColor("#009E73"), QColor("#CC79A7"),
        QColor("#E69F00"), QColor("#56B4E9"), QColor("#D62728"), QColor("#1A1A1A"),
    )

    def __init__(self, parent=None):
        super().__init__(parent)
        self.counts: list[tuple[str, dict[str, int]]] = []
        self.setMinimumHeight(220)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_counts(self, counts: Iterable[Any]) -> None:
        normalized: list[tuple[str, dict[str, int]]] = []
        for item in counts:
            if len(item) == 3 and not isinstance(item[1], Mapping):
                label, free, hibernate = item
                values = {"自由活动": int(free), "蛰伏": int(hibernate)}
            else:
                label, values = item
                values = {str(key): int(value) for key, value in (values or {}).items()}
            normalized.append((str(label), values))
        self.counts = normalized
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor("#fbfdff"))
        # Reserve space for the two axis labels.
        rect = self.rect().adjusted(62, 34, -24, -58)
        painter.setPen(QPen(QColor("#d8e7f3"), 1)); painter.drawRect(rect)
        if not self.counts:
            painter.setPen(QColor("#7191b2")); painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "暂无真实行为识别数据"); painter.end(); return
        category_order = ["自由活动", "蛰伏", "攀爬"]
        categories = [category for category in category_order if any(category in values for _label, values in self.counts)]
        if not categories:
            painter.setPen(QColor("#7191b2")); painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "暂无已选择的行为类别"); painter.end(); return
        totals = {
            category: sum(max(0, int(values.get(category, 0))) for _label, values in self.counts)
            for category in categories
        }
        maximum = max([*totals.values(), 1])
        # Counts are integers.  Do not draw five fractional ticks and round
        # them back to integers, otherwise a maximum of 1 produces labels
        # like 0, 0, 0, 1, 1, 1.  Use every integer for small totals and
        # evenly spaced integer steps for larger totals.
        if maximum <= 5:
            tick_values = list(range(maximum + 1))
        else:
            tick_step = max(1, math.ceil(maximum / 5))
            tick_values = list(range(0, maximum + 1, tick_step))
            if tick_values[-1] != maximum:
                tick_values.append(maximum)
        for tick_value in tick_values:
            ratio = tick_value / maximum
            y = rect.bottom() - ratio * rect.height()
            painter.setPen(QPen(QColor("#e8f0f7"), 1)); painter.drawLine(rect.left(), int(y), rect.right(), int(y))
            painter.setPen(QColor("#7191b2")); painter.drawText(8, int(y - 8), 48, 18, Qt.AlignmentFlag.AlignRight, str(tick_value))

        category_width = rect.width() / max(len(categories), 1)
        for category_index, category in enumerate(categories):
            center = rect.left() + (category_index + 0.5) * category_width
            value = totals[category]
            bar_width = max(16.0, min(90.0, category_width * 0.52))
            height = float(value) / maximum * max(rect.height() - 2, 1)
            x = center - bar_width / 2.0
            y = rect.bottom() - height
            painter.setBrush(self._COLORS[category_index % len(self._COLORS)])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawRect(int(x), int(y), max(1, int(bar_width)), max(1, int(height)))
            painter.setPen(QColor("#355775"))
            painter.drawText(int(x - 8), max(rect.top() + 12, int(y) - 4), int(bar_width + 16), 18, Qt.AlignmentFlag.AlignHCenter, str(value))
            painter.setPen(QColor("#355775"))
            painter.drawText(int(center - category_width * 0.45), rect.bottom() + 10, int(category_width * 0.9), 24, Qt.AlignmentFlag.AlignHCenter, category)

        painter.setPen(QColor("#7191b2"))
        painter.drawText(rect.left(), rect.bottom() + 36, rect.width(), 18, Qt.AlignmentFlag.AlignHCenter, "动作类型")
        painter.drawText(8, rect.top() - 18, 48, 18, Qt.AlignmentFlag.AlignRight, "次数")
        painter.end()


class TrajectoryComparisonPlot(QWidget):
    """Lightweight multi-channel trajectory comparison canvas.

    The legacy trajectory-analysis module uses the same physical quantities:
    XY positions in millimetres, Z height in millimetres, cumulative 2-D
    distance and 60-second binned movement.  This widget keeps those plots
    available inside the workflow page without importing the optional Excel
    analysis window (which would make the no-hardware GUI depend on
    ``openpyxl``).
    """

    # Keep the old analysis window's channel palette so the comparison plots
    # remain visually compatible with the legacy charts.
    _COLORS = ("#1677B8", "#D95F02", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#D62728", "#1A1A1A")
    # Fixed, high-contrast cage colors for the infrared comparison chart.
    _INFRARED_COLORS = ("#0072B2", "#D55E00", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#F0E442", "#000000")
    _SLEEP_COLORS = (QColor("#f4f7fb"), QColor("#e0584f"))

    def __init__(self, mode: str, parent=None):
        super().__init__(parent)
        self.mode = str(mode)
        self.series: list[tuple[str, Any]] = []
        self.setMinimumHeight(270)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_series(self, series: Iterable[tuple[str, Any]]) -> None:
        self.series = [(str(label), values) for label, values in series]
        self._update_adaptive_size()
        self.update()

    _PER_CAGE_MODES = frozenset(("3d", "xy", "height", "dwell"))

    @staticmethod
    def _grid_shape(count: int) -> tuple[int, int]:
        """Return an adaptive (columns, rows) layout for cage subplots."""
        count = max(1, int(count))
        if count == 1:
            columns = 1
        elif count <= 4:
            columns = 2
        else:
            columns = 4
        return columns, int(math.ceil(count / columns))

    def _update_adaptive_size(self) -> None:
        count = max(1, len(self.series))
        if self.mode in self._PER_CAGE_MODES:
            # All cage-based views use the same compact grid: four columns
            # for up to eight cages, matching the 3D comparison layout.
            _columns, rows = self._grid_shape(count)
            height = max(340, rows * 250 + 48)
        elif self.mode == "sleep":
            # One heat-map row represents one selected cage.
            height = max(320, min(760, 130 + count * 42))
        else:
            # Lines and bars remain one large comparison figure.  Their width
            # carries the channel comparison; only the minimum height grows
            # slightly when many series need a larger legend.
            height = max(340, min(520, 340 + max(0, count - 4) * 12))
        self.setMinimumHeight(height)
        self.updateGeometry()

    def sizeHint(self) -> QSize:
        count = max(1, len(self.series))
        if self.mode in self._PER_CAGE_MODES:
            columns, _rows = self._grid_shape(count)
            width = 760 if columns == 1 else 980
        else:
            width = 980
        return QSize(width, max(340, self.minimumHeight()))

    @classmethod
    def _tile_rects(cls, rect, count: int, mode: str = "") -> list[QRect]:
        columns, rows = cls._grid_shape(count)
        gap = 12
        tile_width = max(1, int((rect.width() - gap * (columns - 1)) / columns))
        tile_height = max(1, int((rect.height() - gap * (rows - 1)) / rows))
        tiles = []
        for index in range(count):
            row, column = divmod(index, columns)
            tiles.append(QRect(
                rect.left() + column * (tile_width + gap),
                rect.top() + row * (tile_height + gap),
                tile_width,
                tile_height,
            ))
        return tiles

    @staticmethod
    def _fit_plan_rect(rect, top_reserved: int = 0):
        """Fit a horizontal 360 x 180 mm plan inside the available plot area."""
        available = rect.adjusted(34, int(top_reserved) + 2, -10, -30)
        ratio = max(float(CAGE_LENGTH_MM), 1.0) / max(float(CAGE_WIDTH_MM), 1.0)
        height = min(max(1, available.height()), max(1, int(available.width() / ratio)))
        width = min(max(1, available.width()), max(1, int(height * ratio)))
        return QRect(
            available.center().x() - width // 2,
            available.center().y() - height // 2,
            width,
            height,
        )

    @staticmethod
    def _draw_plan_dimensions(painter: QPainter, rect) -> None:
        painter.setPen(QPen(QColor("#d8e7f3"), 1))
        painter.setBrush(QColor("#fbfdff"))
        painter.drawRect(rect)
        painter.setPen(QColor("#7191b2"))
        painter.drawText(
            QRect(rect.left(), rect.bottom() + 4, rect.width(), 22),
            Qt.AlignmentFlag.AlignCenter,
            f"Y / 笼长 {float(CAGE_LENGTH_MM):g} mm",
        )
        painter.save()
        painter.translate(rect.left() - 21, rect.center().y())
        painter.rotate(-90)
        painter.drawText(
            QRect(-rect.height() // 2, -12, rect.height(), 24),
            Qt.AlignmentFlag.AlignCenter,
            f"X / 笼宽 {float(CAGE_WIDTH_MM):g} mm",
        )
        painter.restore()

    @staticmethod
    def _draw_tile_label(painter: QPainter, tile, label: str) -> None:
        painter.setPen(QPen(QColor("#d7e5f1"), 1))
        painter.setBrush(QColor("#fbfdff"))
        painter.drawRoundedRect(tile, 6, 6)
        painter.setPen(QColor("#24567f"))
        painter.drawText(tile.adjusted(9, 6, -8, -6), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft, label)

    @staticmethod
    def _infrared_color(label: str, index: int) -> QColor:
        """Return a stable color derived from the physical cage number."""
        match = re.search(r"(\d+)$", str(label))
        palette_index = int(match.group(1)) - 1 if match else int(index)
        return QColor(TrajectoryComparisonPlot._INFRARED_COLORS[palette_index % len(TrajectoryComparisonPlot._INFRARED_COLORS)])

    @staticmethod
    def _display_label(label: str) -> str:
        """Show both the channel and its physical cage number."""
        text = str(label)
        match = re.search(r"(\d+)$", text)
        return f"笼子 {int(match.group(1))}" if match else text

    def _empty(self, painter: QPainter, rect) -> None:
        painter.setPen(QColor("#7191b2"))
        if self.mode == "infrared":
            message = "暂无所选通道的真实红外温度数据"
        elif self.mode == "height":
            message = "暂无所选通道的真实高度数据"
        elif self.mode == "dwell":
            message = "暂无所选通道的真实停留数据"
        else:
            message = "暂无所选通道的真实轨迹数据"
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, message)

    @staticmethod
    def _mm(point: tuple[float, float, float]) -> tuple[float, float, float]:
        return TrajectoryCanvas._to_cage_mm(point)

    def _draw_legend(self, painter: QPainter, rect) -> None:
        x = rect.left() + 8
        # Keep up to three wrapped legend rows inside the reserved header
        # band, never on top of the plot itself.
        y = rect.top() - 44
        palette = self._INFRARED_COLORS if self.mode == "infrared" else self._COLORS
        for index, (label, _values) in enumerate(self.series):
            color = self._infrared_color(label, index) if self.mode == "infrared" else QColor(palette[index % len(palette)])
            painter.setPen(QPen(color, 3)); painter.drawLine(x, y - 4, x + 13, y - 4)
            display = self._display_label(label)
            painter.setPen(QColor("#355775")); painter.drawText(x + 18, y, display)
            x += max(96, len(display) * 10 + 28)
            if x > rect.right() - 85:
                x = rect.left() + 8; y += 17

    def _draw_axes(self, painter: QPainter, rect, x_label: str, y_label: str) -> None:
        painter.setPen(QPen(QColor("#d8e7f3"), 1)); painter.drawRect(rect)
        for tick in range(1, 5):
            x = rect.left() + rect.width() * tick / 5.0
            y = rect.top() + rect.height() * tick / 5.0
            painter.setPen(QPen(QColor("#e8f0f7"), 1)); painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom())); painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        painter.setPen(QColor("#7191b2")); painter.drawText(rect.left(), rect.bottom() + 20, x_label); painter.drawText(rect.left() - 8, rect.top() - 8, y_label)

    def _paint_legacy_height_tile(self, painter: QPainter, rect, values: list[tuple[float, float, float]]) -> None:
        """Draw a fixed-scale X/Z chart using one consistent trajectory color."""
        converted = [self._mm(point) for point in values if len(point) >= 3 and all(_number(v) is not None for v in point)]
        if not converted:
            self._empty(painter, rect)
            return
        xs = [float(point[0]) for point in converted]
        heights = [float(point[2]) for point in converted]
        chart = rect.adjusted(0, 22, 0, -30)
        painter.setPen(QPen(QColor("#d8e7f3"), 1))
        painter.setBrush(QColor("#f8fafc"))
        painter.drawRect(chart)
        painter.setPen(QColor("#355775"))
        painter.drawText(rect.adjusted(2, 1, -2, -1), Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignHCenter, "Mouse Height by X")
        for tick in range(1, 5):
            x = chart.left() + chart.width() * tick / 5.0
            y = chart.top() + chart.height() * tick / 5.0
            painter.setPen(QPen(QColor("#e8f0f7"), 1))
            painter.drawLine(QPointF(x, chart.top()), QPointF(x, chart.bottom()))
            painter.drawLine(QPointF(chart.left(), y), QPointF(chart.right(), y))
        def map_point(x_value: float, z_value: float) -> QPointF:
            x_ratio = (x_value + float(CAGE_WIDTH_MM) / 2.0) / max(float(CAGE_WIDTH_MM), 1.0)
            z_ratio = z_value / max(float(CAGE_HEIGHT_MM), 1.0)
            return QPointF(chart.left() + x_ratio * chart.width(), chart.bottom() - z_ratio * chart.height())
        points = [map_point(x_value, z_value) for x_value, z_value in zip(xs, heights)]
        painter.setPen(QPen(QColor("#2563eb"), 2))
        for first, second in zip(points, points[1:]):
            painter.drawLine(first, second)
        painter.setPen(QColor("#7191b2"))
        painter.drawText(QRect(chart.left(), chart.bottom() + 4, chart.width(), 22), Qt.AlignmentFlag.AlignCenter, f"X / 笼宽 {float(CAGE_WIDTH_MM):g} mm")
        painter.save()
        painter.translate(chart.left() - 21, chart.center().y())
        painter.rotate(-90)
        painter.drawText(QRect(-chart.height() // 2, -12, chart.height(), 24), Qt.AlignmentFlag.AlignCenter, f"Z / 笼高 {float(CAGE_HEIGHT_MM):g} mm")
        painter.restore()

    def _paint_per_cage_trajectory(self, painter: QPainter, rect) -> None:
        """Draw each cage using the real cage width, length and height."""
        tiles = self._tile_rects(rect, len(self.series), self.mode)
        for series_index, ((label, values), tile) in enumerate(zip(self.series, tiles)):
            self._draw_tile_label(painter, tile, self._display_label(label))
            plot_rect = tile.adjusted(9, 27, -9, -9)
            valid = [point for point in values if len(point) >= 3 and all(_number(v) is not None for v in point)]
            if not valid:
                self._empty(painter, plot_rect)
                continue
            if self.mode == "height":
                self._paint_legacy_height_tile(painter, plot_rect, valid)
                continue
            if self.mode == "3d":
                projected = [TrajectoryCanvas._project(corner, plot_rect) for corner in TrajectoryCanvas._cage_corners()]
                painter.setPen(QPen(QColor("#bad3e9"), 1, Qt.PenStyle.DashLine))
                edges = ((0, 1), (0, 2), (0, 4), (1, 3), (1, 5), (2, 3), (2, 6), (3, 7),
                         (4, 5), (4, 6), (5, 7), (6, 7))
                for first, second in edges:
                    painter.drawLine(projected[first], projected[second])
                half_width = float(CAGE_WIDTH_MM) / 2.0
                origin = TrajectoryCanvas._project((-half_width, 0.0, 0.0), plot_rect)
                axes = (
                    (TrajectoryCanvas._project((half_width, 0.0, 0.0), plot_rect), "X"),
                    (TrajectoryCanvas._project((-half_width, float(CAGE_LENGTH_MM), 0.0), plot_rect), "Y"),
                    (TrajectoryCanvas._project((-half_width, 0.0, float(CAGE_HEIGHT_MM)), plot_rect), "Z"),
                )
                for endpoint, axis_name in axes:
                    painter.setPen(QPen(QColor("#7191b2"), 2))
                    painter.drawLine(origin, endpoint)
                    painter.drawText(int(endpoint.x()) + 4, int(endpoint.y()) - 3, axis_name)
                painter.setPen(QColor("#7191b2"))
                painter.drawText(
                    plot_rect.adjusted(4, 2, -4, -2),
                    Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft,
                    f"X={float(CAGE_WIDTH_MM):g} mm   Y={float(CAGE_LENGTH_MM):g} mm   Z={float(CAGE_HEIGHT_MM):g} mm",
                )
                trail = [TrajectoryCanvas._project(point, plot_rect) for point in valid]
                painter.setPen(QPen(QColor(self._COLORS[series_index % len(self._COLORS)]), 2))
                for first, second in zip(trail, trail[1:]):
                    painter.drawLine(first, second)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#e69536")); painter.drawEllipse(trail[0], 3, 3)
                painter.setBrush(QColor("#288ee0")); painter.drawEllipse(trail[-1], 3, 3)
                continue
            painter.setPen(QPen(QColor("#d8e7f3"), 1))
            if self.mode == "xy":
                plan_rect = self._fit_plan_rect(plot_rect)
                self._draw_plan_dimensions(painter, plan_rect)
                width, length = float(CAGE_WIDTH_MM), float(CAGE_LENGTH_MM)
                mapped = []
                for point in valid:
                    x, y, _z = self._mm(point)
                    mapped.append(QPointF(
                        plan_rect.left() + y / max(length, 1.0) * plan_rect.width(),
                        plan_rect.bottom() - (x + width / 2.0) / max(width, 1.0) * plan_rect.height(),
                    ))
                axis_label = ""
            else:
                maximum_height = max(float(CAGE_HEIGHT_MM), max(self._mm(point)[2] for point in valid), 1.0)
                mapped = [
                    QPointF(
                        plot_rect.left() + index / max(len(valid) - 1, 1) * plot_rect.width(),
                        plot_rect.bottom() - self._mm(point)[2] / maximum_height * plot_rect.height(),
                    )
                    for index, point in enumerate(valid)
                ]
                axis_label = "高度 Z (mm)"
            if mapped:
                path = QPainterPath(); path.moveTo(mapped[0])
                for point in mapped[1:]:
                    path.lineTo(point)
                color = QColor(self._COLORS[series_index % len(self._COLORS)])
                painter.setPen(QPen(color, 2)); painter.drawPath(path)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.setBrush(QColor("#1B9E77")); painter.drawEllipse(mapped[0], 3, 3)
                painter.setBrush(QColor("#D95F02")); painter.drawEllipse(mapped[-1], 3, 3)
                if axis_label:
                    painter.setPen(QColor("#7191b2")); painter.drawText(plot_rect.adjusted(4, 0, -4, -3), Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignLeft, axis_label)

    def _paint_dwell(self, painter: QPainter, rect) -> None:
        """Draw the activity heatmap in a horizontal cage-sized grid."""
        tiles = self._tile_rects(rect, len(self.series), self.mode)
        palette = (QColor("#edf4fb"), QColor("#b9d5ef"), QColor("#6fa4df"), QColor("#f2b233"), QColor("#df4438"))
        for (label, values), tile in zip(self.series, tiles):
            self._draw_tile_label(painter, tile, self._display_label(label))
            plot_rect = tile.adjusted(9, 27, -9, -9)
            if not values:
                self._empty(painter, plot_rect)
                continue
            painter.setPen(QColor("#7191b2"))
            painter.drawText(QRect(plot_rect.left(), plot_rect.top(), plot_rect.width(), 18), Qt.AlignmentFlag.AlignLeft, "活动强度")
            swatch_width = 16
            for index, color in enumerate(palette):
                x = plot_rect.left() + 55 + index * (swatch_width + 3)
                painter.setBrush(color); painter.setPen(Qt.PenStyle.NoPen)
                painter.drawRoundedRect(x, plot_rect.top() + 3, swatch_width, 12, 3, 3)
            painter.setPen(QColor("#7191b2"))
            painter.drawText(plot_rect.right() - 35, plot_rect.top() + 14, "低→高")
            plan_rect = self._fit_plan_rect(plot_rect, top_reserved=22)
            self._draw_plan_dimensions(painter, plan_rect)
            columns, rows = 12, 10
            cells = {(int(item[0]), int(item[1])): max(0.0, float(item[2])) for item in values if len(item) >= 3}
            maximum = max(cells.values(), default=0.0)
            if maximum <= 0:
                self._empty(painter, plan_rect)
                continue
            gap = 3.0
            cell_w = max(3.0, (plan_rect.width() - gap * (columns - 1)) / columns)
            cell_h = max(3.0, (plan_rect.height() - gap * (rows - 1)) / rows)
            for row in range(rows):
                for column in range(columns):
                    value = cells.get((column, row), 0.0)
                    index = 0 if not value else min(len(palette) - 1, int(value / maximum * (len(palette) - 1)) + 1)
                    x = plan_rect.left() + column * (cell_w + gap)
                    draw_row = rows - 1 - row
                    y = plan_rect.top() + draw_row * (cell_h + gap)
                    painter.setBrush(palette[index]); painter.setPen(Qt.PenStyle.NoPen)
                    painter.drawRoundedRect(int(x), int(y), max(1, int(cell_w)), max(1, int(cell_h)), 4, 4)

    def _paint_trajectory_2d(self, painter: QPainter, rect) -> None:
        self._draw_axes(painter, rect, "X（mm）", "Y（mm）")
        width, length = float(CAGE_WIDTH_MM), float(CAGE_LENGTH_MM)
        def map_xy(point):
            x, y, _z = self._mm(point)
            return QPointF(rect.left() + (x + width / 2.0) / max(width, 1.0) * rect.width(), rect.bottom() - y / max(length, 1.0) * rect.height())
        any_data = False
        for index, (label, values) in enumerate(self.series):
            points = [map_xy(point) for point in values if all(_number(v) is not None for v in point)]
            if not points:
                continue
            any_data = True; path = QPainterPath(); path.moveTo(points[0])
            for point in points[1:]: path.lineTo(point)
            painter.setPen(QPen(QColor(self._COLORS[index % len(self._COLORS)]), 2)); painter.drawPath(path)
        if not any_data: self._empty(painter, rect)

    def _paint_height(self, painter: QPainter, rect) -> None:
        self._draw_axes(painter, rect, "采样序号", "高度 Z（mm）")
        maximum = 2
        converted = []
        for label, values in self.series:
            items = [self._mm(point)[2] for point in values if all(_number(v) is not None for v in point)]
            converted.append((label, items)); maximum = max(maximum, len(items))
        maximum_height = max([max(items, default=0.0) for _label, items in converted] + [float(CAGE_HEIGHT_MM), 1.0])
        any_data = False
        for index, (_label, items) in enumerate(converted):
            if not items: continue
            any_data = True; path = QPainterPath()
            for item_index, value in enumerate(items):
                point = QPointF(rect.left() + item_index / max(maximum - 1, 1) * rect.width(), rect.bottom() - value / maximum_height * rect.height())
                if item_index == 0: path.moveTo(point)
                else: path.lineTo(point)
            painter.setPen(QPen(QColor(self._COLORS[index % len(self._COLORS)]), 2)); painter.drawPath(path)
        if not any_data: self._empty(painter, rect)

    def _paint_lines(self, painter: QPainter, rect, y_label: str) -> None:
        self._draw_axes(painter, rect, "实验时间（分钟）", y_label)
        maximum_x = max([max((float(item[0]) for item in values), default=0.0) for _label, values in self.series] + [1.0])
        maximum_y = max([max((float(item[1]) for item in values), default=0.0) for _label, values in self.series] + [1.0])
        any_data = False
        for index, (_label, values) in enumerate(self.series):
            numeric = [(float(item[0]), float(item[1])) for item in values if len(item) >= 2 and _number(item[0]) is not None and _number(item[1]) is not None]
            if not numeric: continue
            any_data = True; path = QPainterPath()
            for point_index, (x_value, y_value) in enumerate(numeric):
                point = QPointF(rect.left() + x_value / maximum_x * rect.width(), rect.bottom() - y_value / maximum_y * rect.height())
                if point_index == 0: path.moveTo(point)
                else: path.lineTo(point)
            painter.setPen(QPen(QColor(self._COLORS[index % len(self._COLORS)]), 2)); painter.drawPath(path)
        if not any_data: self._empty(painter, rect)

    def _paint_infrared(self, painter: QPainter, rect) -> None:
        """Draw the full Excel temperature series in the legacy right-panel style."""
        values = [
            (float(stamp), float(temperature))
            for _label, samples in self.series
            for stamp, temperature in samples
            if _number(stamp) is not None and _number(temperature) is not None
        ]
        if not values:
            self._empty(painter, rect)
            return
        min_time = min(stamp for stamp, _temperature in values)
        max_time = max(stamp for stamp, _temperature in values)
        min_temp = min(temperature for _stamp, temperature in values)
        max_temp = max(temperature for _stamp, temperature in values)
        padding = max((max_temp - min_temp) * 0.1, 0.5)
        if min_temp == max_temp:
            padding = max(padding, 1.0)
        low, high = min_temp - padding, max_temp + padding

        # QDateTimeAxis/QValueAxis in the legacy TemperatureTrendWidget both
        # used six ticks, a HH:mm:ss time format, and a two-decimal °C axis.
        painter.setPen(QPen(QColor("#d8e7f3"), 1))
        painter.drawRect(rect)
        tick_count = 6
        for index in range(tick_count):
            ratio = index / (tick_count - 1)
            x = rect.left() + ratio * rect.width()
            y = rect.bottom() - ratio * rect.height()
            painter.setPen(QPen(QColor("#e8f0f7"), 1))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            stamp = min_time + (max_time - min_time) * ratio
            time_text = datetime.fromtimestamp(stamp).strftime("%H:%M:%S")
            painter.setPen(QColor("#7191b2"))
            painter.drawText(
                QRect(int(x - 42), rect.bottom() + 5, 84, 18),
                Qt.AlignmentFlag.AlignHCenter,
                time_text,
            )
            temperature = low + (high - low) * ratio
            painter.drawText(
                QRect(rect.left() - 58, int(y - 9), 52, 18),
                Qt.AlignmentFlag.AlignRight,
                f"{temperature:.2f}",
            )
        painter.setPen(QColor("#7191b2"))
        painter.drawText(
            QRect(rect.left() - 70, rect.top() - 28, 130, 18),
            Qt.AlignmentFlag.AlignLeft,
            "温度 (°C)",
        )
        painter.drawText(
            QRect(rect.left(), rect.bottom() + 25, rect.width(), 18),
            Qt.AlignmentFlag.AlignHCenter,
            "时间",
        )

        for index, (_label, samples) in enumerate(self.series):
            numeric = [
                (float(stamp), float(temperature))
                for stamp, temperature in samples
                if _number(stamp) is not None and _number(temperature) is not None
            ]
            if not numeric:
                continue
            color = self._infrared_color(_label, index)
            painter.setPen(QPen(color, 1))
            painter.setBrush(color)
            for stamp, temperature in numeric:
                point = QPointF(
                    rect.left() + (stamp - min_time) / max(max_time - min_time, 1.0) * rect.width(),
                    rect.bottom() - (temperature - low) / max(high - low, 1.0) * rect.height(),
                )
                painter.drawEllipse(point, 4, 4)

    def _paint_totals(self, painter: QPainter, rect) -> None:
        self._draw_axes(painter, rect, "通道", "二维总路程（mm）")
        totals = [(label, _number(value) or 0.0) for label, value in self.series]
        maximum = max([value for _label, value in totals] + [1.0]); count = max(len(totals), 1)
        for index, (label, value) in enumerate(totals):
            width = rect.width() / count * 0.58; x = rect.left() + (index + 0.21) * rect.width() / count
            height = value / maximum * rect.height(); y = rect.bottom() - height
            painter.setBrush(QColor(self._COLORS[index % len(self._COLORS)])); painter.setPen(Qt.PenStyle.NoPen); painter.drawRect(int(x), int(y), int(width), int(height))
            painter.setPen(QColor("#355775")); painter.drawText(int(x), rect.bottom() + 18, self._display_label(label)); painter.drawText(int(x), max(rect.top() + 12, int(y) - 4), f"{value:.1f}")

    def _paint_sleep(self, painter: QPainter, rect) -> None:
        if not self.series:
            self._empty(painter, rect); return
        rows = len(self.series); columns = max([len(values) for _label, values in self.series] + [1]); cell_w = max(4.0, rect.width() / columns); cell_h = max(16.0, rect.height() / rows)
        for row, (label, values) in enumerate(self.series):
            painter.setPen(QColor("#355775")); painter.drawText(rect.left() - 90, int(rect.top() + row * cell_h + cell_h * 0.65), self._display_label(label))
            for column in range(columns):
                state = values[column] if column < len(values) else None
                color = QColor("#d9dde3") if state is None else (self._SLEEP_COLORS[1] if float(state) >= 0.5 else self._SLEEP_COLORS[0])
                painter.setBrush(color); painter.setPen(Qt.PenStyle.NoPen); painter.drawRect(int(rect.left() + column * cell_w), int(rect.top() + row * cell_h), max(1, int(cell_w - 1)), max(1, int(cell_h - 2)))
        multiplier = max(1, int(getattr(self, "_sleep_segment_multiplier", 1)))
        interval_text = f"每格约 {10 * multiplier} 秒" if multiplier > 1 else "每格 10 秒"
        painter.setPen(QColor("#7191b2")); painter.drawText(rect.left(), rect.bottom() + 20, f"实验时间（{interval_text}；红色=睡眠，浅色=活动）")

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self); painter.fillRect(self.rect(), QColor("#fbfdff")); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        left_margin = 96 if self.mode == "sleep" else (82 if self.mode == "infrared" else 58)
        # Reserve a clear header band for the per-channel legend.  The card
        # title is outside this widget, so keeping the legend below it avoids
        # the title/legend collision seen with long channel names.
        bottom_margin = -58 if self.mode == "infrared" else -36
        rect = self.rect().adjusted(left_margin, 68, -18, bottom_margin)
        if not self.series:
            self._empty(painter, rect); painter.end(); return
        if self.mode in self._PER_CAGE_MODES:
            if self.mode == "dwell": self._paint_dwell(painter, rect)
            else: self._paint_per_cage_trajectory(painter, rect)
            painter.end()
            return
        self._draw_legend(painter, rect)
        if self.mode == "3d":
            projected = [TrajectoryCanvas._project(corner, rect) for corner in TrajectoryCanvas._cage_corners()]
            painter.setPen(QPen(QColor("#bad3e9"), 1, Qt.PenStyle.DashLine))
            edges = ((0,1),(0,2),(0,4),(1,3),(1,5),(2,3),(2,6),(3,7),(4,5),(4,6),(5,7),(6,7))
            for first, second in edges: painter.drawLine(projected[first], projected[second])
            for index, (_label, values) in enumerate(self.series):
                trail = [TrajectoryCanvas._project(point, rect) for point in values]
                if not trail: continue
                painter.setPen(QPen(QColor(self._COLORS[index % len(self._COLORS)]), 2))
                for first, second in zip(trail, trail[1:]): painter.drawLine(first, second)
            painter.end()
            return
        if self.mode == "xy": self._paint_trajectory_2d(painter, rect)
        elif self.mode == "height": self._paint_height(painter, rect)
        elif self.mode == "sleep": self._paint_sleep(painter, rect)
        elif self.mode == "total": self._paint_totals(painter, rect)
        elif self.mode == "infrared": self._paint_infrared(painter, rect)
        else: self._paint_lines(painter, rect, "二维运动距离（mm）")
        painter.end()


class DualAxisChart(QWidget):
    """Draw persisted comparison data as a readable dual-axis chart."""

    viewport_changed = pyqtSignal(float, float)
    _CHANNEL_COLORS = ("#1677B8", "#D95F02", "#009E73", "#CC79A7", "#E69F00", "#56B4E9", "#D62728", "#1A1A1A")
    _LEFT_COLORS = _CHANNEL_COLORS
    _RIGHT_COLORS = _CHANNEL_COLORS
    _GRID_COLOR = QColor("#DCEAF6")
    _LEFT_AXIS_COLOR = QColor("#1677B8")
    _RIGHT_AXIS_COLOR = QColor("#D95F02")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.left: list[float] = []; self.right: list[float] = []
        self.left_series: list[tuple[str, list[float]]] = []
        self.right_series: list[tuple[str, list[float]]] = []
        self._left_points: list[tuple[str, list[tuple[float, float]]]] = []
        self._right_points: list[tuple[str, list[tuple[float, float]]]] = []
        self._left_axis_label = "左轴"
        self._right_axis_label = "右轴"
        self._uses_elapsed_time = False
        self._full_x_range: Optional[tuple[float, float]] = None
        self._view_x_range: Optional[tuple[float, float]] = None
        self._drag_anchor: Optional[QPointF] = None
        self._detail_mode = False
        self.setMouseTracking(True)
        self.setMinimumHeight(360)

    def sizeHint(self) -> QSize:
        return QSize(1200, 420)

    def set_axis_labels(self, left: str, right: str) -> None:
        self._left_axis_label = str(left or "左轴")
        self._right_axis_label = str(right or "右轴")
        self.update()

    def set_series(self, left: Iterable[float], right: Iterable[float]) -> None:
        """Backward-compatible setter for one series on each axis."""
        self.set_channel_series([("CH01", left)], [("CH01", right)])

    @staticmethod
    def _clean_series(values: Iterable[float]) -> list[float]:
        cleaned: list[float] = []
        for value in values:
            numeric = _number(value)
            if numeric is not None:
                cleaned.append(float(numeric))
        return cleaned

    def set_channel_series(
        self,
        left: Iterable[tuple[str, Iterable[float]]],
        right: Iterable[tuple[str, Iterable[float]]],
    ) -> None:
        self.left_series = [(str(label), self._clean_series(values)) for label, values in left]
        self.right_series = [(str(label), self._clean_series(values)) for label, values in right]
        self.left = [value for _label, values in self.left_series for value in values]
        self.right = [value for _label, values in self.right_series for value in values]
        self._left_points = [(label, [(float(index), value) for index, value in enumerate(values)]) for label, values in self.left_series]
        self._right_points = [(label, [(float(index), value) for index, value in enumerate(values)]) for label, values in self.right_series]
        self._uses_elapsed_time = False
        self.update()

    @staticmethod
    def _time_number(value: Any) -> Optional[float]:
        numeric = _number(value)
        if numeric is not None:
            numeric = float(numeric)
            return numeric / 1000.0 if abs(numeric) > 100_000_000_000 else numeric
        text = str(value or "").strip()
        if not text:
            return None
        candidates = (text, text.replace("Z", "+00:00"), text.replace("_", "-"))
        for candidate in candidates:
            try:
                return datetime.fromisoformat(candidate).timestamp()
            except ValueError:
                continue
        for pattern in ("%Y-%m-%d-%H-%M-%S-%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d-%H-%M-%S"):
            try:
                return datetime.strptime(text.replace("_", "-"), pattern).timestamp()
            except ValueError:
                continue
        return None

    @classmethod
    def _clean_timed_series(cls, values: Iterable[tuple[Any, Any]]) -> tuple[list[float], list[tuple[float, float]], bool]:
        numeric_values: list[float] = []
        timed_values: list[tuple[float, float]] = []
        has_clock_time = False
        for index, item in enumerate(values):
            try:
                raw_time, raw_value = item
            except (TypeError, ValueError):
                continue
            numeric = _number(raw_value)
            if numeric is None:
                continue
            timestamp = cls._time_number(raw_time)
            if timestamp is None:
                timestamp = float(index)
            elif abs(timestamp) > 100_000_000:
                has_clock_time = True
            numeric_values.append(float(numeric))
            timed_values.append((float(timestamp), float(numeric)))
        timed_values.sort(key=lambda point: point[0])
        return numeric_values, timed_values, has_clock_time

    @property
    def full_x_range(self) -> Optional[tuple[float, float]]:
        return self._full_x_range

    @property
    def view_x_range(self) -> Optional[tuple[float, float]]:
        return self._view_x_range

    def reset_view(self) -> None:
        if self._full_x_range is not None:
            self._view_x_range = self._full_x_range
        self._detail_mode = False
        self.update()

    def _clamp_view(self, low: float, high: float) -> tuple[float, float]:
        full_low, full_high = self._full_x_range or (low, high)
        full_span = max(full_high - full_low, 1e-9)
        span = min(max(high - low, full_span / 10000.0), full_span)
        low = max(full_low, min(low, full_high - span))
        return low, low + span

    def _emit_viewport(self) -> None:
        if self._full_x_range is None or self._view_x_range is None:
            return
        full_low, full_high = self._full_x_range
        full_span = max(full_high - full_low, 1e-9)
        view_low, view_high = self._view_x_range
        start = max(0.0, min(1.0, (view_low - full_low) / full_span))
        end = max(start, min(1.0, (view_high - full_low) / full_span))
        if start <= 0.000001 and end >= 0.999999:
            self.viewport_changed.emit(0.0, 1.0)
        else:
            self.viewport_changed.emit(float(start), float(end))

    def _plot_rect(self) -> QRect:
        legend_rows = 1
        metrics = self.fontMetrics()
        usable_width = max(200, self.width() - 96)
        row_width = 0
        for label, _color, _line_style in self._legend_items():
            item_width = metrics.horizontalAdvance(label) + 42
            if row_width and row_width + item_width > usable_width:
                legend_rows += 1
                row_width = 0
            row_width += item_width
        bottom_margin = 48 + (legend_rows * 24 if self._legend_items() else 0)
        return self.rect().adjusted(72, 38, -72, -bottom_margin)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        if not self._full_x_range or not self._view_x_range:
            event.ignore()
            return
        delta = event.angleDelta().y()
        if not delta:
            event.ignore()
            return
        rect = self._plot_rect()
        if not rect.contains(event.position().toPoint()):
            event.ignore()
            return
        factor = 0.72 if delta > 0 else 1.0 / 0.72
        view_low, view_high = self._view_x_range
        center_ratio = max(0.0, min(1.0, (event.position().x() - rect.left()) / max(rect.width(), 1)))
        center = view_low + center_ratio * (view_high - view_low)
        span = (view_high - view_low) * factor
        self._view_x_range = self._clamp_view(center - span * center_ratio, center + span * (1.0 - center_ratio))
        self._detail_mode = self._view_x_range != self._full_x_range
        self.update()
        self._emit_viewport()
        event.accept()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton and self._view_x_range:
            if self._plot_rect().contains(event.position().toPoint()):
                self._drag_anchor = event.position()
                self.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._drag_anchor is not None and self._view_x_range and self._full_x_range:
            rect = self._plot_rect()
            delta = event.position().x() - self._drag_anchor.x()
            span = self._view_x_range[1] - self._view_x_range[0]
            shift = -delta / max(rect.width(), 1) * span
            low, high = self._clamp_view(self._view_x_range[0] + shift, self._view_x_range[1] + shift)
            actual_shift = low - self._view_x_range[0]
            self._view_x_range = (low, high)
            self._drag_anchor = QPointF(
                self._drag_anchor.x() - actual_shift / max(span, 1e-9) * rect.width(),
                self._drag_anchor.y(),
            )
            self._detail_mode = self._view_x_range != self._full_x_range
            self.update()
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton and self._drag_anchor is not None:
            self._drag_anchor = None
            self.unsetCursor()
            self._emit_viewport()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def set_timed_channel_series(
        self,
        left: Iterable[tuple[str, Iterable[tuple[Any, Any]]]],
        right: Iterable[tuple[str, Iterable[tuple[Any, Any]]]],
        full_x_range: Optional[tuple[float, float]] = None,
        reset_view: bool = False,
        detail_mode: bool = False,
    ) -> None:
        left_cleaned = [(str(label), *self._clean_timed_series(values)) for label, values in left]
        right_cleaned = [(str(label), *self._clean_timed_series(values)) for label, values in right]
        self.left_series = [(label, numeric) for label, numeric, _points, _clock in left_cleaned]
        self.right_series = [(label, numeric) for label, numeric, _points, _clock in right_cleaned]
        self._left_points = [(label, points) for label, _numeric, points, _clock in left_cleaned]
        self._right_points = [(label, points) for label, _numeric, points, _clock in right_cleaned]
        self.left = [value for _label, values in self.left_series for value in values]
        self.right = [value for _label, values in self.right_series for value in values]
        self._uses_elapsed_time = any(clock for *_rest, clock in left_cleaned + right_cleaned)
        current_points = [point for _label, points in self._left_points + self._right_points for point in points]
        current_values = [point[0] for point in current_points]
        if full_x_range is None:
            full_x_range = (
                min(current_values) if current_values else 0.0,
                max(current_values) if current_values else 1.0,
            )
        full_low, full_high = full_x_range
        if math.isclose(full_low, full_high):
            full_high = full_low + 1.0
        self._full_x_range = (float(full_low), float(full_high))
        if reset_view or self._view_x_range is None:
            self._view_x_range = self._full_x_range
        else:
            self._view_x_range = self._clamp_view(*self._view_x_range)
        self._detail_mode = bool(detail_mode)
        self.update()

    @staticmethod
    def _axis_bounds(values: list[float]) -> tuple[float, float, float]:
        if not values:
            return 0.0, 1.0, 0.2
        low, high = min(values), max(values)
        if math.isclose(low, high):
            padding = max(abs(low) * 0.02, 0.5)
            low -= padding
            high += padding
        raw_step = max((high - low) / 5.0, 1e-12)
        magnitude = 10 ** math.floor(math.log10(raw_step))
        scaled = raw_step / magnitude
        nice = 1.0 if scaled <= 1 else 2.0 if scaled <= 2 else 2.5 if scaled <= 2.5 else 5.0 if scaled <= 5 else 10.0
        step = nice * magnitude
        return math.floor(low / step) * step, math.ceil(high / step) * step, step

    @staticmethod
    def _tick_text(value: float, step: float) -> str:
        if abs(value) >= 10000:
            return f"{value:.1e}"
        decimals = max(0, min(5, -math.floor(math.log10(abs(step))) if 0 < abs(step) < 1 else 0))
        return f"{value:.{decimals}f}"

    @staticmethod
    def _metric_short_name(label: str) -> str:
        text = re.sub(r"（[^）]*）|\([^)]*\)", "", str(label)).strip()
        return text or str(label)

    @classmethod
    def _channel_color(cls, label: str, fallback_index: int = 0) -> str:
        match = re.search(r"(\d+)$", str(label))
        index = int(match.group(1)) - 1 if match else fallback_index
        return cls._CHANNEL_COLORS[index % len(cls._CHANNEL_COLORS)]

    @staticmethod
    def _decimate(points: list[tuple[float, float]], limit: int) -> list[tuple[float, float]]:
        if len(points) <= limit:
            return points
        stride = max(1, math.ceil(len(points) / limit))
        sampled = points[::stride]
        if sampled[-1] != points[-1]:
            sampled.append(points[-1])
        return sampled

    def _legend_items(self) -> list[tuple[str, str, Qt.PenStyle]]:
        left_metric = self._metric_short_name(self._left_axis_label)
        right_metric = self._metric_short_name(self._right_axis_label)
        items = [
            (f"{label} · {left_metric}", self._channel_color(label, index), Qt.PenStyle.SolidLine)
            for index, (label, values) in enumerate(self.left_series) if values
        ]
        items.extend(
            (f"{label} · {right_metric}", self._channel_color(label, index), Qt.PenStyle.DashLine)
            for index, (label, values) in enumerate(self.right_series) if values
        )
        return items

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.fillRect(self.rect(), QColor("#ffffff"))
        legend_items = self._legend_items()
        metrics = painter.fontMetrics()
        usable_legend_width = max(200, self.width() - 96)
        legend_rows = 1
        row_width = 0
        for label, _color, _line_style in legend_items:
            item_width = metrics.horizontalAdvance(label) + 42
            if row_width and row_width + item_width > usable_legend_width:
                legend_rows += 1
                row_width = 0
            row_width += item_width
        bottom_margin = 48 + (legend_rows * 24 if legend_items else 0)
        rect = self.rect().adjusted(72, 38, -72, -bottom_margin)
        if rect.width() < 40 or rect.height() < 40:
            painter.end()
            return
        painter.setPen(QPen(QColor("#d6e4ef"), 1))
        painter.setBrush(QColor("#fbfdff"))
        painter.drawRoundedRect(rect, 9, 9)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        if not self.left and not self.right:
            painter.setPen(QColor("#7191b2"))
            painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "请选择左右轴采集模块并勾选通道")
            painter.end()
            return

        view_low, view_high = self._view_x_range or (0.0, 1.0)
        visible_left = [
            (label, [point for point in points if view_low <= point[0] <= view_high])
            for label, points in self._left_points
        ]
        visible_right = [
            (label, [point for point in points if view_low <= point[0] <= view_high])
            for label, points in self._right_points
        ]
        left_low, left_high, left_step = self._axis_bounds(
            [value for _label, points in visible_left for _time, value in points]
        )
        right_low, right_high, right_step = self._axis_bounds(
            [value for _label, points in visible_right for _time, value in points]
        )
        horizontal_ticks = 5
        for tick in range(horizontal_ticks + 1):
            ratio = tick / horizontal_ticks
            y = rect.bottom() - ratio * rect.height()
            painter.setPen(QPen(self._GRID_COLOR, 1))
            painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
            left_value = left_low + ratio * (left_high - left_low)
            right_value = right_low + ratio * (right_high - right_low)
            painter.setPen(self._LEFT_AXIS_COLOR)
            painter.drawText(QRect(rect.left() - 65, int(y - 9), 56, 18), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._tick_text(left_value, left_step))
            painter.setPen(self._RIGHT_AXIS_COLOR)
            painter.drawText(QRect(rect.right() + 9, int(y - 9), 56, 18), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._tick_text(right_value, right_step))

        all_points = [point for _label, values in visible_left + visible_right for point in values]
        x_values = [point[0] for point in all_points]
        x_low, x_high = view_low, view_high
        if not x_values:
            x_low, x_high = (min(x_values), max(x_values)) if x_values else (0.0, 1.0)
            if math.isclose(x_low, x_high):
                x_high = x_low + 1.0
        x_ticks = 6
        for tick in range(x_ticks + 1):
            ratio = tick / x_ticks
            x = rect.left() + ratio * rect.width()
            painter.setPen(QPen(self._GRID_COLOR, 1))
            painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
            if self._uses_elapsed_time:
                elapsed = (x_low + ratio * (x_high - x_low)) - x_low
                label = f"{elapsed / 3600:.1f}h" if elapsed >= 7200 else f"{elapsed / 60:.0f}′"
            else:
                label = str(int(round(x_low + ratio * (x_high - x_low))))
            painter.setPen(QColor("#7892aa"))
            painter.drawText(QRect(int(x - 30), rect.bottom() + 7, 60, 18), Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop, label)

        painter.setPen(self._LEFT_AXIS_COLOR)
        painter.drawText(QRect(rect.left() - 52, 8, rect.width() // 2, 24), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, f"左 · {self._left_axis_label}")
        painter.setPen(self._RIGHT_AXIS_COLOR)
        painter.drawText(QRect(rect.center().x(), 8, rect.width() // 2 + 52, 24), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"右 · {self._right_axis_label}")

        for series, axis_low, axis_high, line_style in (
            (visible_left, left_low, left_high, Qt.PenStyle.SolidLine),
            (visible_right, right_low, right_high, Qt.PenStyle.DashLine),
        ):
            axis_span = max(axis_high - axis_low, 1e-12)
            for series_index, (_label, points) in enumerate(series):
                points = self._decimate(
                    points,
                    max(300, rect.width() * (8 if self._detail_mode else 2)),
                )
                if not points:
                    continue
                path = QPainterPath()
                for index, (timestamp, value) in enumerate(points):
                    x = rect.left() + (timestamp - x_low) / (x_high - x_low) * rect.width()
                    y = rect.bottom() - (value - axis_low) / axis_span * rect.height()
                    point = QPointF(x, y)
                    if index == 0:
                        path.moveTo(point)
                    else:
                        path.lineTo(point)
                painter.setPen(QPen(QColor(self._channel_color(_label, series_index)), 1.9, line_style))
                painter.drawPath(path)

        legend_x = rect.left()
        legend_y = rect.bottom() + 48
        for label, color, line_style in legend_items:
            item_width = metrics.horizontalAdvance(label) + 42
            if legend_x > rect.left() and legend_x + item_width > rect.right():
                legend_x = rect.left()
                legend_y += 24
            painter.setPen(QPen(QColor(color), 4, line_style, Qt.PenCapStyle.RoundCap))
            painter.drawLine(legend_x, legend_y - 5, legend_x + 16, legend_y - 5)
            painter.setPen(QColor("#476985"))
            painter.drawText(legend_x + 23, legend_y, label)
            legend_x += item_width
        painter.end()


class MachineVisionPage(_BaseWorkflowPage):
    """P5 real-time trajectory-camera view with sleep/free-activity output."""

    def __init__(self, context_provider: Any = None, health_source: Any = None, parent=None):
        self.selected_channel = 1
        self._coord3d_config_window = None
        self._last_frame_id = None
        self._last_frame_size: tuple[int, int] = (0, 0)
        self._last_good_video_frames: dict[int, dict[str, Any]] = {}
        self._display_max_age_seconds = 2.0
        self._cage_calibration_mode = False
        self._cage_calibration_points: list[tuple[float, float]] = []
        self._manual_calibration_active_channels: set[int] = set()
        self._detected_cage_points: dict[int, list[dict[str, float]]] = {}

        self._live_trajectory_samples: dict[int, list[Optional[tuple[float, float, float]]]] = {}
        self._live_trajectory_frame_ids: dict[int, int] = {}
        self.trajectory_thread = None
        self.trajectory_submitter_thread = None
        self._trajectory_module_loader = None
        self._connected_trajectory_signal_thread = None
        self._infrared_last_refresh = 0.0
        self._infrared_channel = None
        self._infrared_loader_thread = None
        self._infrared_image_paths: dict[int, str] = {}
        self._infrared_display_path = ""
        self._infrared_display_size = QSize(0, 0)
        self._recognition_records: list[dict[str, Any]] = []
        super().__init__(context_provider, health_source, parent)
        self._build_ui()
        self._display_max_age_seconds = self._configured_display_max_age_seconds()
        self._load_cage_calibration_overlay()
        self._frame_timer = QTimer(self); self._frame_timer.setInterval(250); self._frame_timer.timeout.connect(self.refresh); self._frame_timer.start(); self.refresh()
        QTimer.singleShot(0, self.start_trajectory_thread)

    def start_refresh(self) -> None:
        """Start the page refresh and the legacy infrared image loader.

        The old monitor-camera page deliberately kept filesystem polling out of
        the GUI thread.  The workflow page used to scan the infrared folder
        directly from ``refresh`` instead, which made the display stale and
        caused it to disagree with the old page's ``data_delay`` behaviour.
        Keep the new page layout, but use the old loader lifecycle and payload
        format exactly.
        """
        super().start_refresh()
        if hasattr(self, "_frame_timer") and not self._frame_timer.isActive():
            self._frame_timer.start()
        self._start_legacy_infrared_loader()
        self.start_trajectory_thread()
        self.refresh()

    def stop_refresh(self) -> None:
        super().stop_refresh()
        if hasattr(self, "_frame_timer"):
            self._frame_timer.stop()
        self._stop_legacy_infrared_loader()

    def _request_trajectory_module_load(self) -> None:
        """Load the old trajectory worker off the GUI thread, as Tab4 did."""
        try:
            from Module.monitor_camera.index import tab_4

            should_start = False
            with tab_4.trajectory_module_loader_lock:
                trajectory_class = tab_4.mouse_trajectory_thread_class
                if trajectory_class is None:
                    loader = tab_4.trajectory_module_loader
                    if loader is None or loader.isFinished():
                        loader = tab_4.TrajectoryModuleLoaderThread()
                        tab_4.trajectory_module_loader = loader
                        should_start = True
                    if self._trajectory_module_loader is not loader:
                        loader.loaded.connect(self._on_trajectory_module_loaded)
                        loader.failed.connect(self._on_trajectory_module_load_failed)
                        self._trajectory_module_loader = loader
                else:
                    loader = None
            if trajectory_class is not None:
                self.start_trajectory_thread()
            elif should_start:
                loader.start()
        except Exception as exc:  # pragma: no cover - optional model dependency
            logger.exception("加载旧版轨迹模块失败: {}", exc)
            if hasattr(self, "cage_calibration_status"):
                self.cage_calibration_status.setText("轨迹服务加载失败；仍可保存鼠笼框校准")

    def _on_trajectory_module_loaded(self, _trajectory_class: Any) -> None:
        self._trajectory_module_loader = None
        self.start_trajectory_thread()

    def _on_trajectory_module_load_failed(self, error_message: str) -> None:
        self._trajectory_module_loader = None
        logger.error("旧版轨迹模块不可用: {}", error_message)
        if hasattr(self, "cage_calibration_status"):
            self.cage_calibration_status.setText("轨迹服务加载失败；仍可保存鼠笼框校准")

    def _connect_trajectory_signal(self, trajectory_thread: Any) -> None:
        if self._connected_trajectory_signal_thread is trajectory_thread:
            return
        self._disconnect_trajectory_signal()
        trajectory_thread.trajectory_ready.connect(self._on_trajectory_result)
        self._connected_trajectory_signal_thread = trajectory_thread

    def _disconnect_trajectory_signal(self) -> None:
        trajectory_thread = self._connected_trajectory_signal_thread
        if trajectory_thread is None:
            return
        try:
            trajectory_thread.trajectory_ready.disconnect(self._on_trajectory_result)
        except (TypeError, RuntimeError):
            pass
        self._connected_trajectory_signal_thread = None

    @staticmethod
    def _normalize_detected_cage_points(points: Any) -> list[dict[str, float]]:
        """Normalize legacy trajectory corner payloads for the live preview."""
        parsed: list[dict[str, float]] = []
        for point in points or []:
            try:
                if isinstance(point, Mapping):
                    x, y = point.get("x"), point.get("y")
                else:
                    x, y = point[0], point[1]
                parsed.append({"x": float(x), "y": float(y)})
            except (IndexError, KeyError, TypeError, ValueError):
                return []
        return parsed[:4] if len(parsed) >= 4 else []

    @staticmethod
    def _draw_legacy_cage_corners(frame: Any, corners: Any) -> Any:
        """Draw the cage polygon into the video frame exactly as old Tab4 did."""
        try:
            display_frame = frame.copy()
        except (AttributeError, MemoryError, SystemError):
            return frame
        normalized = MachineVisionPage._normalize_detected_cage_points(corners)
        if len(normalized) != 4:
            return display_frame
        try:
            import cv2
            import numpy as np

            points = np.array(
                [[point["x"], point["y"]] for point in normalized],
                dtype=np.int32,
            )
            cv2.polylines(display_frame, [points], True, (0, 255, 255), 2)
        except Exception as exc:  # pragma: no cover - optional image backend
            logger.debug("绘制旧版鼠笼框失败: %s", exc)
        return display_frame

    def _sync_detected_cage_points_from_service(self, channel: Optional[int] = None) -> list[dict[str, float]]:
        """Keep the old page's last-good cage frame visible between results.

        The legacy Tab4 page used ``last_good_corners`` while the trajectory
        worker was processing frames.  P5 previously only updated its overlay
        from a newly delivered signal, so a refresh or a page switch could
        leave the raw frame visible with no cage frame at all.
        """
        channel_id = int(channel or self.selected_channel)
        points = self._normalize_detected_cage_points(
            self._detected_cage_points.get(channel_id, [])
        )
        if points:
            return points

        trajectory_thread = getattr(self, "trajectory_thread", None)
        if trajectory_thread is None:
            return []
        for attribute in ("fixed_corners_by_cage", "previous_corners_by_cage"):
            corner_map = getattr(trajectory_thread, attribute, None)
            if not isinstance(corner_map, Mapping):
                continue
            corner_value = corner_map.get(channel_id)
            candidate = getattr(corner_value, "corners", corner_value)
            points = self._normalize_detected_cage_points(candidate)
            if points:
                self._detected_cage_points[channel_id] = points
                return points
        return []

    def start_trajectory_thread(self) -> None:
        """Attach P5 to the exact shared worker/submitter used by old Tab4."""
        try:
            from Module.monitor_camera.index import tab_4

            with tab_4.trajectory_module_loader_lock:
                trajectory_class = tab_4.mouse_trajectory_thread_class
            with tab_4.trajectory_service_lock:
                existing = tab_4.shared_trajectory_thread
                needs_new = existing is None or not existing.isRunning()
            if needs_new and trajectory_class is None:
                self._request_trajectory_module_load()
                return
            trajectory_thread = tab_4.attach_shared_trajectory_service(self)
            if trajectory_thread is None:
                return
            # ``attach_shared_trajectory_service`` assigns the same reference
            # to the consumer, but keep it explicit here so P5 can read the
            # legacy worker's last-good corner cache during a refresh.
            self.trajectory_thread = trajectory_thread
            apply_tuning = getattr(trajectory_thread, "apply_trajectory_tuning", None)
            if callable(apply_tuning):
                apply_tuning(tab_4.trajectory_tuning_store.snapshot())
            set_preview_cage = getattr(trajectory_thread, "set_preview_cage_number", None)
            if callable(set_preview_cage):
                set_preview_cage(self.selected_channel)
            self._connect_trajectory_signal(trajectory_thread)
            self._sync_detected_cage_points_from_service(self.selected_channel)
        except Exception as exc:  # pragma: no cover - model/hardware dependent
            logger.exception("启动旧版轨迹服务失败: %s", exc)
            if hasattr(self, "cage_calibration_status"):
                self.cage_calibration_status.setText("轨迹服务启动失败；仍可保存鼠笼框校准")

    def stop_trajectory_thread(self) -> bool:
        """Detach/finalize through the same contract used during experiment stop."""
        self._disconnect_trajectory_signal()
        try:
            from Module.monitor_camera.index import tab_4

            return tab_4.detach_shared_trajectory_service(self)
        except Exception as exc:  # pragma: no cover - shutdown dependent
            logger.exception("停止旧版轨迹服务失败: %s", exc)
            return False

    def _on_trajectory_result(self, result: Any) -> None:
        if not isinstance(result, Mapping):
            return
        try:
            channel = int(result.get("cage_number", 0) or 0)
        except (TypeError, ValueError):
            return
        if channel <= 0:
            return
        corners = self._normalize_detected_cage_points(result.get("corners"))
        if corners:
            # Match legacy Tab4: retain the latest valid frame and reuse it
            # when the next result is a blank/failed sample.
            self._detected_cage_points[channel] = corners

        # A normal frame result always carries ``solved``.  A missing solution
        # is a real blank sample and therefore terminates the current line;
        # plot/finalization notifications do not represent samples and are
        # ignored here.
        if result.get("event_type") == "plot_ready" or result.get("status") in {
            "final_done", "final_partial"
        }:
            return
        frame_id = int(result.get("frame_id", 0) or 0)
        previous_frame_id = self._live_trajectory_frame_ids.get(channel, -1)
        if frame_id > 0 and frame_id <= previous_frame_id:
            return
        if frame_id > 0:
            self._live_trajectory_frame_ids[channel] = frame_id
        samples = self._live_trajectory_samples.setdefault(channel, [])
        solved = result.get("solved")
        point = None
        if isinstance(solved, Mapping):
            try:
                x = _number(solved.get("X"))
                y = _number(solved.get("Y"))
                z = _number(solved.get("Z_total", solved.get("Z")))
                if x is not None and y is not None and z is not None:
                    point = (float(x), float(y), float(z))
            except (TypeError, ValueError):
                point = None
        if point is None:
            if samples and samples[-1] is not None:
                samples.append(None)
        else:
            samples.append(point)
        if len(samples) > 3600:
            del samples[: len(samples) - 3600]
        if channel == self.selected_channel and hasattr(self, "trajectory_canvas"):
            self.trajectory_canvas.set_segmented_points(samples)

    def _start_legacy_infrared_loader(self) -> None:
        loader = self._infrared_loader_thread
        if loader is not None and loader.isRunning():
            return
        camera_config = global_setting.get_setting("camera_config", {}) or {}
        if not isinstance(camera_config, Mapping) or not isinstance(
            camera_config.get("INFRARED_CAMERA"), Mapping
        ):
            self._clear_infrared_display("红外相机未提供新图像")
            return
        try:
            # Import lazily so pages that do not use machine vision do not
            # load the optional legacy camera UI during application startup.
            from Module.monitor_camera.index.tab_4 import ImageLoaderThread

            loader = ImageLoaderThread(display_mode="infrared")
            loader.image_loaded.connect(self._on_legacy_infrared_images)
            self._infrared_loader_thread = loader
            loader.start()
        except Exception as exc:  # pragma: no cover - hardware/config dependent
            self._infrared_loader_thread = None
            logger.exception("启动旧版红外图像加载线程失败: %s", exc)
            self._clear_infrared_display("红外相机未提供新图像")

    @staticmethod
    def _configured_display_max_age_seconds() -> float:
        """Use the same live-preview age threshold as legacy Tab4."""
        camera_config = global_setting.get_setting("camera_config", {}) or {}
        trajectory_config = (
            camera_config.get("MOUSE_TRAJECTORY", {})
            if isinstance(camera_config, Mapping)
            else {}
        )
        try:
            return max(
                float(trajectory_config.get("display_max_age_seconds", 2.0) or 2.0),
                0.1,
            )
        except (TypeError, ValueError):
            return 2.0

    def _stop_legacy_infrared_loader(self) -> None:
        loader = self._infrared_loader_thread
        self._infrared_loader_thread = None
        if loader is None:
            return
        try:
            if loader.isRunning():
                loader.stop()
                loader.requestInterruption()
                loader.wait(3000)
        except Exception as exc:  # pragma: no cover - shutdown dependent
            logger.warning("停止旧版红外图像加载线程失败: %s", exc)

    @staticmethod
    def _legacy_infrared_image_map(image_paths: Any, prefix: Any) -> dict[int, str]:
        """Build the same cage-to-image map as the legacy Tab4 page."""
        result: dict[int, str] = {}
        if not isinstance(image_paths, (list, tuple)):
            return result
        prefix_text = str(prefix or "mouse_cage_")
        for image_path in image_paths:
            if not image_path:
                continue
            match = re.search(rf"{re.escape(prefix_text)}(\d+)", str(image_path))
            if match is None:
                continue
            try:
                result[int(match.group(1))] = str(image_path)
            except (TypeError, ValueError):
                continue
        return result

    def _on_legacy_infrared_images(self, payload: Any) -> None:
        """Accept the old ImageLoaderThread payload without blocking the UI."""
        if not isinstance(payload, Mapping):
            return
        image_paths = payload.get("infrared_camera", [])
        config = global_setting.get_setting("camera_config", {}) or {}
        infrared = config.get("INFRARED_CAMERA", {}) if isinstance(config, Mapping) else {}
        prefix = infrared.get("mouse_cage_prefix", "mouse_cage_") if isinstance(infrared, Mapping) else "mouse_cage_"
        self._infrared_image_paths = self._legacy_infrared_image_map(image_paths, prefix)
        self._infrared_last_refresh = time.time()
        self._render_infrared_image(force=True)

    def _clear_infrared_display(self, text: str) -> None:
        if not hasattr(self, "infrared_label"):
            return
        self._infrared_display_path = ""
        self._infrared_display_size = QSize(0, 0)
        self.infrared_label.setPixmap(QPixmap())
        self.infrared_label.setText(text)

    def _render_infrared_image(self, force: bool = False) -> None:
        if not hasattr(self, "infrared_label"):
            return
        image_path = self._infrared_image_paths.get(int(self.selected_channel), "")
        label_size = self.infrared_label.size()
        if (
            not force
            and image_path == self._infrared_display_path
            and label_size == self._infrared_display_size
        ):
            return
        if not image_path or not os.path.isfile(image_path):
            self._clear_infrared_display("红外相机未提供新图像")
            return
        pixmap = QPixmap(image_path)
        if pixmap.isNull():
            self._clear_infrared_display("红外图像读取失败")
            return
        self._infrared_display_path = image_path
        self._infrared_display_size = QSize(label_size)
        self.infrared_label.setText("")
        target_size = label_size if label_size.width() > 0 and label_size.height() > 0 else QSize(360, 280)
        self.infrared_label.setPixmap(
            pixmap.scaled(
                target_size,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )

    def _latest_infrared_image(self) -> Optional[str]:
        """Return the image selected by the legacy loader for this cage."""
        return self._infrared_image_paths.get(int(self.selected_channel))

    def _build_ui(self) -> None:
        _scroll, layout = self._canvas()
        self._header(layout, "MACHINE VISION", "机器视觉 · 双模成像（逐通道）", "左侧显示重要信息与红外证据，中间显示轨迹相机画面，右侧只显示笼内 3D 轨迹。")
        channels = QHBoxLayout(); self.channel_buttons = []
        self.channel_group = QButtonGroup(self); self.channel_group.setExclusive(True)
        for channel in range(1, 9):
            button = QPushButton(f"CH{channel:02d}"); button.setCheckable(True); button.setChecked(channel == 1); button.clicked.connect(lambda _checked=False, value=channel: self._select_channel(value)); self.channel_group.addButton(button, channel); channels.addWidget(button); self.channel_buttons.append(button)
        channels.addStretch(1)
        # The legacy 3D calibration builder remains available to administrators
        # without changing its file format or calibration behaviour.  It is
        # opened lazily so ordinary users do not load the optional calibration
        # canvas and do not see an unrelated configuration control.
        if str(global_setting.get_setting("user_role", "用户") or "用户").strip() == "管理员":
            self.config_file_button = QPushButton("配置文件")
            self.config_file_button.setObjectName("primary")
            self.config_file_button.setToolTip("打开原有 3D 标定构建系统")
            self.config_file_button.clicked.connect(self._open_coord3d_config)
            channels.addWidget(self.config_file_button)
        else:
            self.config_file_button = None
        layout.addLayout(channels)
        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(12)
        # P5 uses a three-column composition: information/IR evidence on the
        # left, the live visible-light frame in the centre and the calibrated
        # 3D cage on the right.  Explicit minimums prevent the left and right
        # cards from collapsing when the page is first shown in a scroll area.
        grid.setColumnMinimumWidth(0, 280)
        grid.setColumnMinimumWidth(1, 420)
        grid.setColumnMinimumWidth(2, 420)
        grid.setColumnStretch(0, 3)
        grid.setColumnStretch(1, 5)
        grid.setColumnStretch(2, 5)
        grid.setRowStretch(0, 1)
        grid.setRowStretch(1, 1)
        info_card, _, _ = self._card("重要信息", "", "")
        info_card.setMinimumHeight(230)
        info_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        info_layout = info_card.layout(); self.info_label = QLabel("通道：CH01\n个体：未知\n状态：未知\n行为：未知"); self.info_label.setProperty("hint", True); info_layout.addWidget(self.info_label)
        frame_card, _, _ = self._card("可见光实时帧", "", "")
        frame_card.setMinimumHeight(620)
        frame_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        frame_layout = frame_card.layout()
        self.frame_label = CageCalibrationImage("轨迹相机无新帧")
        self.frame_label.setMinimumSize(460, 480)
        self.frame_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.frame_label.point_clicked.connect(self._on_cage_calibration_point)
        frame_layout.addWidget(self.frame_label, 1)
        calibration_row = QHBoxLayout()
        self.calibrate_cage_button = QPushButton("校准鼠笼框")
        self.calibrate_cage_button.clicked.connect(self._start_cage_calibration)
        self.confirm_cage_button = QPushButton("确认校准")
        self.confirm_cage_button.setObjectName("primary")
        self.confirm_cage_button.setEnabled(False)
        self.confirm_cage_button.setVisible(False)
        self.confirm_cage_button.clicked.connect(self._confirm_cage_calibration)
        self.cancel_cage_button = QPushButton("取消")
        self.cancel_cage_button.setVisible(False)
        self.cancel_cage_button.clicked.connect(self._cancel_cage_calibration)
        self.cage_calibration_status = QLabel("使用算法检测鼠笼框；需要时可手动校准")
        self.cage_calibration_status.setProperty("hint", True)
        self.cage_calibration_status.setWordWrap(True)
        calibration_row.addWidget(self.calibrate_cage_button)
        calibration_row.addWidget(self.confirm_cage_button)
        calibration_row.addWidget(self.cancel_cage_button)
        calibration_row.addWidget(self.cage_calibration_status, 1)
        frame_layout.addLayout(calibration_row)
        infrared_card, _, _ = self._card("红外图像（按原逻辑更新）", "", "")
        infrared_card.setMinimumHeight(350)
        infrared_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        infrared_layout = infrared_card.layout(); self.infrared_label = QLabel("红外相机未提供新图像"); self.infrared_label.setAlignment(Qt.AlignmentFlag.AlignCenter); self.infrared_label.setMinimumSize(360, 280); self.infrared_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding); infrared_layout.addWidget(self.infrared_label, 1)
        trajectory_card, _, _ = self._card("笼内三维行动轨迹", "", "实时更新轨迹数据；轨迹使用实时 XYZ 数据")
        trajectory_card.setMinimumHeight(620)
        trajectory_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        trajectory_layout = trajectory_card.layout(); self.trajectory_canvas = TrajectoryCanvas(); self.trajectory_canvas.setMinimumSize(460, 540); self.trajectory_canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding); trajectory_layout.addWidget(self.trajectory_canvas, 1)
        grid.addWidget(info_card, 0, 0); grid.addWidget(frame_card, 0, 1, 2, 1); grid.addWidget(trajectory_card, 0, 2, 2, 1); grid.addWidget(infrared_card, 1, 0)
        layout.addLayout(grid)

        lower = QGridLayout()
        lower.setHorizontalSpacing(14)
        lower.setColumnStretch(0, 1)
        lower.setColumnStretch(1, 1)
        stats_card, _, _ = self._card("行为识别统计", "", "仅统计真实识别记录")
        stats_card.setMinimumWidth(360)
        stats_layout = stats_card.layout()
        self.recognition_count_label = QLabel("行为类别：按 P2 选择（自由活动/蛰伏/攀爬）\n自由活动：未知\n蛰伏：未知\n攀爬：未知")
        self.recognition_count_label.setProperty("hint", True)
        self.recognition_count_label.setWordWrap(True)
        stats_layout.addWidget(self.recognition_count_label)
        self.activity_chart = ActivityLineChart()
        self.activity_chart.setMinimumHeight(190)
        self.activity_chart.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        stats_layout.addWidget(self.activity_chart, 1)
        log_card, _, _ = self._card("识别日志", "", "双模成像与行为识别记录")
        log_card.setMinimumWidth(360)
        log_layout = log_card.layout()
        self.recognition_log = QTableWidget(0, 3)
        self.recognition_log.setHorizontalHeaderLabels(["图像/时间", "行为", "通道"])
        self.recognition_log.setMinimumHeight(220)
        self.recognition_log.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        log_header = self.recognition_log.horizontalHeader()
        log_header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        log_header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        log_header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        log_layout.addWidget(self.recognition_log, 1)
        lower.addWidget(stats_card, 0, 0); lower.addWidget(log_card, 0, 1)
        # The recognition panel now lives on P6 together with the historical
        # analysis views.  Keep this hidden compatibility widget so existing
        # integrations that read the P5 attributes continue to work, without
        # rendering a duplicate panel on the machine-vision page.
        self._compat_recognition_widget = QWidget(self)
        self._compat_recognition_widget.setLayout(lower)
        self._compat_recognition_widget.hide()
        layout.addStretch(1)

    def _open_coord3d_config(self) -> None:
        """Open the unchanged legacy 3D calibration page for administrators."""
        try:
            if self._coord3d_config_window is None:
                from Module.coord_3d.index.coord_3d_index import Coord3DIndex

                self._coord3d_config_window = Coord3DIndex()
                self._coord3d_config_window.setAttribute(
                    Qt.WidgetAttribute.WA_DeleteOnClose, False
                )
            self._coord3d_config_window.show()
            self._coord3d_config_window.raise_()
            self._coord3d_config_window.activateWindow()
        except Exception as exc:  # pragma: no cover - optional UI dependency
            logger.exception("打开 3D 配置文件窗口失败: %s", exc)
            QMessageBox.warning(self, "配置文件", f"无法打开配置文件界面：{exc}")

    def _select_channel(self, channel: int) -> None:
        self._cancel_cage_calibration(update_status=False)
        self.selected_channel = channel
        if self.trajectory_thread is not None:
            set_preview = getattr(self.trajectory_thread, "set_preview_cage_number", None)
            if callable(set_preview):
                set_preview(channel)
        for index, button in enumerate(self.channel_buttons, 1): button.setChecked(index == channel)
        self._sync_detected_cage_points_from_service(channel)
        self.frame_label.set_detected_cage_points([])
        self._load_cage_calibration_overlay()
        self.refresh()

    def _cage_calibration_entry(self, channel: Optional[int] = None) -> Mapping[str, Any]:
        """Return the persisted manual calibration for one channel."""
        setting = global_setting.get_setting("experiment_setting", None)
        data = getattr(setting, "channel_data_config", {}) if setting is not None else {}
        if not isinstance(data, Mapping):
            data = {}
        channel_id = int(channel or self.selected_channel)
        channel_data = data.get(str(channel_id), data.get(channel_id, {}))
        value = channel_data.get("cage_frame_calibration", {}) if isinstance(channel_data, Mapping) else {}
        if not isinstance(value, Mapping):
            value = {}
        if not value:
            persisted = global_setting.get_setting("cage_frame_calibrations", {})
            if isinstance(persisted, Mapping):
                value = persisted.get(str(channel_id), persisted.get(channel_id, {}))
        return value if isinstance(value, Mapping) else {}

    @staticmethod
    def _order_cage_points(points: Iterable[Iterable[float]]) -> list[tuple[float, float]]:
        """Order four arbitrary clicks as TL, TR, BR, BL and reject bad quads."""
        parsed = [(float(point[0]), float(point[1])) for point in points]
        if len(parsed) != 4:
            raise ValueError("exactly four cage-frame points are required")
        sums = [x + y for x, y in parsed]
        diffs = [x - y for x, y in parsed]
        indexes = [
            min(range(4), key=sums.__getitem__),
            max(range(4), key=diffs.__getitem__),
            max(range(4), key=sums.__getitem__),
            min(range(4), key=diffs.__getitem__),
        ]
        if len(set(indexes)) != 4:
            raise ValueError("cage-frame points do not form four distinct corners")
        ordered = [parsed[index] for index in indexes]
        area = abs(
            sum(
                ordered[index][0] * ordered[(index + 1) % 4][1]
                - ordered[(index + 1) % 4][0] * ordered[index][1]
                for index in range(4)
            )
        ) / 2.0
        if area <= 1.0:
            raise ValueError("cage-frame quadrilateral area is too small")
        return ordered

    def _load_cage_calibration_overlay(self) -> None:
        # Match the old page: every application start uses automatic cage-frame
        # detection. A persisted manual overlay must not silently replace it.
        # Manual points become active only after the operator confirms them in
        # this running session.
        if self.selected_channel not in self._manual_calibration_active_channels:
            self._cage_calibration_points = []
            self._update_cage_calibration_overlay()
            self.cage_calibration_status.setText(
                "使用算法自动检测鼠笼框；需要时可手动校准"
            )
            return
        calibration = self._cage_calibration_entry()
        points = calibration.get("points_normalized", calibration.get("points", []))
        normalized: list[tuple[float, float]] = []
        for point in points if isinstance(points, (list, tuple)) else []:
            try:
                x, y = point
                normalized.append((float(x), float(y)))
            except (TypeError, ValueError):
                continue
        self._cage_calibration_points = normalized[:4]
        self._update_cage_calibration_overlay()
        if self._cage_calibration_points:
            self.cage_calibration_status.setText("已校准鼠笼框；后续轨迹映射优先使用人工四角")
        else:
            self.cage_calibration_status.setText("使用算法检测鼠笼框；需要时可手动校准")

    def _update_cage_calibration_overlay(self) -> None:
        # While the user is choosing four corners, ``CageCalibrationImage``
        # owns the in-progress pixel points.  The live frame refresh runs every
        # 250 ms; replacing those points here with the persisted calibration
        # made every refresh erase the previous click, so the UI could never
        # advance beyond 1/4.
        self._sync_detected_cage_points_from_service(self.selected_channel)
        # The old page drew the yellow cage polygon into the BGR frame before
        # converting it to a QPixmap. Keep the QLabel overlay exclusively for
        # in-progress/manual calibration points so the automatic frame follows
        # the exact legacy display path.
        self.frame_label.set_detected_cage_points([])
        if self._cage_calibration_mode:
            return
        width, height = self._last_frame_size
        if width <= 0 or height <= 0:
            self.frame_label.set_calibration_points([])
            return
        points = [(x * width, y * height) for x, y in self._cage_calibration_points]
        self.frame_label.set_calibration_points(points)

    def _start_cage_calibration(self) -> None:
        width, height = self._last_frame_size
        if width <= 0 or height <= 0:
            self.cage_calibration_status.setText("当前没有可用的轨迹相机画面，无法校准")
            return
        self._cage_calibration_mode = True
        self._cage_calibration_points = []
        self.frame_label.set_calibration_points([])
        self.frame_label.set_calibration_mode(True)
        self.calibrate_cage_button.setEnabled(False)
        self.confirm_cage_button.setVisible(True)
        self.cancel_cage_button.setVisible(True)
        self.confirm_cage_button.setEnabled(False)
        self.cage_calibration_status.setText("请点击鼠笼框的四个角点（顺序不限）")

    def _on_cage_calibration_point(self, _x: float, _y: float) -> None:
        if not self._cage_calibration_mode:
            return
        count = len(self.frame_label.calibration_points())
        self.cage_calibration_status.setText(
            "已选择 %d/4 个角点%s" % (count, "，请点击确认校准" if count == 4 else "")
        )
        self.confirm_cage_button.setEnabled(count == 4)

    def _cancel_cage_calibration(self, update_status: bool = True) -> None:
        self._cage_calibration_mode = False
        if hasattr(self, "frame_label"):
            self.frame_label.set_calibration_mode(False)
        if hasattr(self, "calibrate_cage_button"):
            self.calibrate_cage_button.setEnabled(True)
        if hasattr(self, "confirm_cage_button"):
            self.confirm_cage_button.setVisible(False)
            self.confirm_cage_button.setEnabled(False)
        if hasattr(self, "cancel_cage_button"):
            self.cancel_cage_button.setVisible(False)
        if update_status and hasattr(self, "cage_calibration_status"):
            self._load_cage_calibration_overlay()

    def _confirm_cage_calibration(self) -> None:
        points = self.frame_label.calibration_points()
        width, height = self._last_frame_size
        if len(points) != 4 or width <= 0 or height <= 0:
            self.cage_calibration_status.setText("需要在当前画面中选择四个角点")
            return
        try:
            points = self._order_cage_points(points)
        except (TypeError, ValueError) as exc:
            self.cage_calibration_status.setText(f"四个角点无效，请重新校准：{exc}")
            return
        normalized = [[round(x / width, 8), round(y / height, 8)] for x, y in points]
        setting = global_setting.get_setting("experiment_setting", None)
        if setting is not None:
            data = getattr(setting, "channel_data_config", {})
            if not isinstance(data, dict):
                data = dict(data) if isinstance(data, Mapping) else {}
            channel_data = data.setdefault(str(self.selected_channel), {})
            if not isinstance(channel_data, dict):
                channel_data = {}
                data[str(self.selected_channel)] = channel_data
            channel_data["cage_frame_calibration"] = {
                "points_normalized": normalized,
                "frame_size": [width, height],
                "source": "manual_visible_light",
                "updated_at": time.time(),
            }
            setting.channel_data_config = data
            global_setting.set_setting("experiment_setting", setting)
            global_setting.set_setting("experiment_setting_new", setting)
            try:
                from public.component.experiment_config.experiment_config_page import ExperimentConfigPage
                ExperimentConfigPage._persist_template(setting)
                ExperimentConfigPage._broadcast_setting(setting)
            except Exception:
                # A template may not exist during a new experiment.  The live
                # setting and trajectory service are still updated below.
                pass
        all_calibrations = global_setting.get_setting("cage_frame_calibrations", {})
        if not isinstance(all_calibrations, dict):
            all_calibrations = {}
        all_calibrations[str(self.selected_channel)] = {
            "points_normalized": normalized,
            "frame_size": [width, height],
            "source": "manual_visible_light",
            "updated_at": time.time(),
        }
        global_setting.set_setting("cage_frame_calibrations", all_calibrations)
        self._manual_calibration_active_channels.add(int(self.selected_channel))
        self._cage_calibration_points = [(x / width, y / height) for x, y in points]
        self._cancel_cage_calibration(update_status=False)
        self.frame_label.set_calibration_points(points)
        self.start_trajectory_thread()
        applied = self._apply_selected_cage_calibration_to_service()
        if applied:
            self.cage_calibration_status.setText("校准完成；后续轨迹映射使用人工鼠笼框")
        else:
            self.cage_calibration_status.setText("校准已保存；轨迹服务就绪后自动应用")

    def _apply_selected_cage_calibration_to_service(self) -> bool:
        trajectory_thread = self.trajectory_thread
        setter = getattr(trajectory_thread, "set_manual_cage_corners", None)
        width, height = self._last_frame_size
        if not callable(setter) or width <= 0 or height <= 0:
            return False
        calibration = self._cage_calibration_entry()
        normalized = calibration.get("points_normalized", calibration.get("points", []))
        try:
            points = self._order_cage_points(
                (float(point[0]) * width, float(point[1]) * height)
                for point in normalized
            )
        except (TypeError, ValueError, IndexError):
            return False
        try:
            return bool(
                setter(
                    self.selected_channel,
                    points,
                    (width, height),
                    source="manual_visible_light",
                )
            )
        except Exception as exc:  # pragma: no cover - worker shutdown race
            logger.exception("应用鼠笼框四点校准失败: %s", exc)
            return False

    def _trajectory_records(self, channel: Optional[int] = None, limit: Optional[int] = None) -> list[dict[str, Any]]:
        """Read persisted XYZ records for one channel.

        ``limit`` is retained for the live P5 refresh path, while P6 passes
        ``None`` so comparisons cover the complete experiment history.
        """
        channel = int(channel or self.selected_channel)
        context = _provider_value(self._context_provider); db_path = context.get("experiment_db_path")
        if not db_path or not os.path.isfile(str(db_path)): return []
        records: list[dict[str, Any]] = []
        try:
            with closing(sqlite3.connect(str(db_path))) as connection:
                tables = connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE 'DetectionResults%cage_%' OR name LIKE '%Trajectory%cage_%')").fetchall()
                for (table,) in tables:
                    if not _table_matches_channel(table, channel): continue
                    columns = {row[1] for row in connection.execute(f'PRAGMA table_info("{table}")').fetchall()}
                    candidates = [
                        ("x", "y", "z"),
                        ("center_x", "center_y", "center_z"),
                        ("X", "Y", "Z"),
                        ("X (m)", "Y (m)", "Z (m)"),
                    ]
                    choice = next((item for item in candidates if set(item).issubset(columns)), None)
                    if not choice: continue
                    time_column = next((name for name in ("time", "timestamp", "image_name", "frameName") if name in columns), None)
                    # YOLO mouse detections are persisted with the box geometry
                    # when the trajectory service has a valid detection.  Keep
                    # this metadata alongside XYZ so behavior classification can
                    # require both a real detection box and a Z-axis change.
                    bbox_size = next((item for item in (
                        ("mouseBoxWidth", "mouseBoxHeight"),
                        ("bbox_width", "bbox_height"),
                        ("box_width", "box_height"),
                    ) if set(item).issubset(columns)), None)
                    bbox_xyxy = next((item for item in (
                        ("mouseX1", "mouseY1", "mouseX2", "mouseY2"),
                        ("bbox_x1", "bbox_y1", "bbox_x2", "bbox_y2"),
                        ("box_x1", "box_y1", "box_x2", "box_y2"),
                    ) if set(item).issubset(columns)), None)
                    select = [f'"{choice[0]}"', f'"{choice[1]}"', f'"{choice[2]}"']
                    if time_column: select.append(f'"{time_column}"')
                    if bbox_size:
                        select.extend(f'"{name}"' for name in bbox_size)
                    elif bbox_xyxy:
                        select.extend(f'"{name}"' for name in bbox_xyxy)
                    limit_clause = f" LIMIT {int(limit)}" if limit is not None and int(limit) > 0 else ""
                    for row_index, row in enumerate(connection.execute(f'SELECT {", ".join(select)} FROM "{table}" ORDER BY rowid ASC{limit_clause}')):
                        interrupted = getattr(self, "_interrupted", None)
                        if row_index % 1000 == 0 and callable(interrupted) and interrupted():
                            return []
                        values = [_number(item) for item in row[:3]]
                        if all(item is not None for item in values):
                            offset = 3
                            stamp = row[offset] if time_column and len(row) > offset else ""
                            if time_column:
                                offset += 1
                            record = {"point": tuple(values), "time": stamp}
                            if bbox_size and len(row) >= offset + 2:
                                record["bbox_width"] = _number(row[offset])
                                record["bbox_height"] = _number(row[offset + 1])
                            elif bbox_xyxy and len(row) >= offset + 4:
                                corners = [_number(item) for item in row[offset:offset + 4]]
                                if all(item is not None for item in corners):
                                    record["bbox_x1"], record["bbox_y1"], record["bbox_x2"], record["bbox_y2"] = corners
                                    record["bbox_width"] = abs(corners[2] - corners[0])
                                    record["bbox_height"] = abs(corners[3] - corners[1])
                            records.append(record)
        except (OSError, sqlite3.Error): return []
        return list(reversed(records))

    def _trajectory_points(self) -> list[tuple[float, float, float]]:
        return [item["point"] for item in self._trajectory_records()]

    @staticmethod
    def _normalize_behavior_categories(categories: Any) -> tuple[str, ...]:
        aliases = {"睡眠": "蛰伏"}
        allowed = ("自由活动", "蛰伏", "攀爬")
        if isinstance(categories, str):
            values = [item.strip() for item in categories.replace("，", ",").split(",") if item.strip()]
        elif isinstance(categories, (list, tuple, set)):
            values = [str(item).strip() for item in categories if str(item).strip()]
        else:
            values = []
        normalized = []
        for value in values:
            value = aliases.get(value, value)
            if value in allowed and value not in normalized:
                normalized.append(value)
        return tuple(normalized)

    def _configured_behavior_categories(self, channel: int) -> tuple[str, ...]:
        context = _provider_value(self._context_provider)
        data = context.get("channel_data_config")
        info = data.get(str(int(channel)), {}) if isinstance(data, Mapping) else {}
        if not isinstance(info, Mapping):
            info = {}
        selected = self._normalize_behavior_categories(info.get("behavior_category"))
        return selected or ("自由活动",)

    @staticmethod
    def _has_detection_box(record: Mapping[str, Any]) -> bool:
        """Return whether this trajectory sample has a usable YOLO box.

        A trajectory point can still be stored when YOLO failed to produce a
        mouse detection.  Such a point is valid for plotting, but it must not
        be used as evidence of climbing.
        """
        width = _number(record.get("bbox_width"))
        height = _number(record.get("bbox_height"))
        return width is not None and height is not None and width > 0.0 and height > 0.0

    @classmethod
    def _is_vertical_detection_box(cls, record: Mapping[str, Any]) -> bool:
        if not cls._has_detection_box(record):
            return False
        width = float(_number(record.get("bbox_width")) or 0.0)
        height = float(_number(record.get("bbox_height")) or 0.0)
        # A climbing mouse produces a vertically elongated YOLO box.  The
        # aspect-ratio threshold is deliberately conservative to avoid turning
        # ordinary perspective changes into climbing events.
        return height / max(width, 1e-6) >= 1.6

    @classmethod
    def _classify(cls, records: list[dict[str, Any]], categories: Any = None) -> str:
        if not records:
            return UNKNOWN
        enabled = cls._normalize_behavior_categories(categories)
        if not enabled:
            enabled = ("自由活动",)
        points = [item["point"] for item in records]
        window = points[-4:]
        if len(window) < 4:
            return UNKNOWN
        physical = [TrajectoryCanvas._to_cage_mm(tuple(point)) for point in window]
        height_span = max(point[2] for point in physical) - min(point[2] for point in physical)
        if "攀爬" in enabled and height_span >= 15.0:
            # Climbing is a two-signal decision: every sample in the decision
            # window must carry a real YOLO detection box, and the boxes must
            # show the vertical elongation expected during climbing.  If the
            # detector has no box, do not infer climbing from Z alone.
            has_boxes = all(cls._has_detection_box(item) for item in records[-4:])
            vertical_boxes = sum(cls._is_vertical_detection_box(item) for item in records[-4:])
            if has_boxes and vertical_boxes >= 2:
                return "攀爬"
        timestamps = []
        for item in records[-4:]:
            stamp = item.get("time")
            numeric = _number(stamp)
            if numeric is None and stamp:
                try:
                    numeric = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).timestamp()
                except (TypeError, ValueError, OverflowError):
                    numeric = None
            if numeric is not None:
                timestamps.append(numeric)
        # Four consecutive video frames are not enough evidence for sleep,
        # but they are still enough to count ordinary free movement.  The old
        # early return incorrectly discarded every action in a high-FPS
        # trajectory, which made the statistics chart stay at zero.
        sleep_window_ready = len(timestamps) < 4 or max(timestamps) - min(timestamps) >= 30.0
        center = tuple(sum(point[index] for point in physical) / len(physical) for index in range(3))
        radius = max(math.dist(point, center) for point in physical)
        if "蛰伏" in enabled and sleep_window_ready and radius <= 5.0:
            return "蛰伏"
        if "自由活动" in enabled:
            return "自由活动"
        return UNKNOWN

    @classmethod
    def _classify_sequence(cls, records: list[dict[str, Any]], categories: Any = None) -> list[dict[str, Any]]:
        classified: list[dict[str, Any]] = []
        for index in range(len(records)):
            # _classify only inspects the latest four samples.  Limiting the
            # window keeps long experiments linear instead of repeatedly
            # copying and rescanning the complete history.
            behavior = cls._classify(records[max(0, index - 3): index + 1], categories)
            if behavior == UNKNOWN:
                continue
            source = records[index]
            classified.append({"time": source.get("time", ""), "behavior": behavior,
                               "channel": source.get("channel")})
        return classified


    def refresh(self) -> None:
        fresh_frame = shared_video_frame_store.read_frame(
            "deep_camera",
            self.selected_channel,
            max_age_seconds=self._display_max_age_seconds,
        )
        frame_available = fresh_frame is not None and fresh_frame.get("frame") is not None
        if frame_available:
            # Match old Tab4: a transient shared-memory miss must not replace a
            # valid preview with an empty placeholder.  Keep the last complete
            # payload per cage and replace it only after another valid read.
            self._last_good_video_frames[int(self.selected_channel)] = fresh_frame
        frame = fresh_frame or self._last_good_video_frames.get(int(self.selected_channel))
        if frame is None:
            self._last_frame_size = (0, 0)
            self.frame_label.clear_frame_image(); self.frame_label.setText("轨迹相机无新帧（未启用或画面超时）")
        else:
            array = frame.get("frame")
            if array is not None:
                detected_points = self._sync_detected_cage_points_from_service(
                    self.selected_channel
                )
                display_array = self._draw_legacy_cage_corners(array, detected_points)
                rgb = (
                    display_array[:, :, ::-1].copy()
                    if getattr(display_array, "ndim", 0) == 3
                    else display_array.copy()
                )
                height, width = rgb.shape[:2]
                self._last_frame_size = (int(width), int(height))
                image = QImage(rgb.data, width, height, rgb.strides[0], QImage.Format.Format_RGB888).copy()
                self.frame_label.setText("")
                self.frame_label.set_frame_image(QPixmap.fromImage(image), self._last_frame_size)

                self._update_cage_calibration_overlay()
            else:
                self._last_frame_size = (0, 0)
                self.frame_label.clear_frame_image(); self.frame_label.setText("轨迹相机无新帧（未启用或画面超时）")

        # P5 is a live view; keep the recent window bounded.  P6 explicitly
        # requests the complete history for comparison and export.
        records = self._trajectory_records(limit=500); points = [item["point"] for item in records]
        live_samples = self._live_trajectory_samples.get(int(self.selected_channel), [])
        if any(sample is not None for sample in live_samples):
            self.trajectory_canvas.set_segmented_points(live_samples)
        else:
            self.trajectory_canvas.set_points(points)
        self._render_infrared_image()
        behavior_categories = self._configured_behavior_categories(self.selected_channel)
        behavior = self._classify(records, behavior_categories)
        status = "在线" if frame_available else UNKNOWN
        context = _provider_value(self._context_provider)
        individuals = context.get("channel_individuals")
        individual = individuals.get(f"CH{self.selected_channel:02d}", {}) if isinstance(individuals, Mapping) else {}
        individual_id = individual.get("id") if isinstance(individual, Mapping) else None
        self.info_label.setText(f"通道：CH{self.selected_channel:02d}\n个体：{_text(individual_id)}\n状态：{status}\n行为：{behavior}")
        tagged_records = [dict(item, channel=self.selected_channel) for item in records]
        self._recognition_records = self._classify_sequence(tagged_records, behavior_categories)[-30:]
        free_count = sum(item["behavior"] == "自由活动" for item in self._recognition_records)
        hibernate_count = sum(item["behavior"] == "蛰伏" for item in self._recognition_records)
        climb_count = sum(item["behavior"] == "攀爬" for item in self._recognition_records)
        self.recognition_count_label.setText(
            f"行为类别：{len(behavior_categories)}（{'/'.join(behavior_categories)}）\n"
            f"自由活动：{free_count if self._recognition_records else UNKNOWN}\n"
            f"蛰伏：{hibernate_count if self._recognition_records else UNKNOWN}\n"
            f"攀爬：{climb_count if self._recognition_records else UNKNOWN}"
        )
        self.activity_chart.set_values([math.dist(a["point"], b["point"]) for a, b in zip(records, records[1:])])
        self.recognition_log.setRowCount(0)
        for item in self._recognition_records[-20:]:
            row = self.recognition_log.rowCount(); self.recognition_log.insertRow(row)
            values = (item.get("time", ""), item["behavior"], f"CH{item['channel']:02d}")
            for column, value in enumerate(values): self.recognition_log.setItem(row, column, QTableWidgetItem(_text(value, "")))
        self.refreshed.emit()


class LegacyTrajectoryImageGrid(QWidget):
    """Display the trajectory PNGs produced by the legacy worker.

    The old export is the source of truth for these two views.  Do not redraw
    them from a reduced in-memory series because that changes the smoothing,
    axes, colour scale, and cage geometry used by the old version.
    """

    def __init__(self, empty_text: str, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.empty_text = str(empty_text)
        self._grid = QGridLayout(self)
        self._grid.setContentsMargins(0, 0, 0, 0)
        self._grid.setSpacing(10)
        self.setMinimumHeight(430)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def _clear(self) -> None:
        while self._grid.count():
            item = self._grid.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def set_image_paths(self, image_paths: Mapping[int, str]) -> None:
        self._clear()
        valid = [
            (int(channel), str(path))
            for channel, path in sorted((image_paths or {}).items())
            if path and os.path.isfile(str(path))
        ]
        if not valid:
            label = QLabel(self.empty_text)
            label.setProperty("hint", True)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._grid.addWidget(label, 0, 0)
            return

        columns = 1 if len(valid) == 1 else 2
        for index, (channel, path) in enumerate(valid):
            card = QFrame(self)
            card.setProperty("card", True)
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 10, 8)
            title = QLabel(f"CH{channel:02d}")
            title.setProperty("title", True)
            card_layout.addWidget(title)
            image_label = QLabel()
            image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            image_label.setMinimumHeight(320)
            image_label.setSizePolicy(
                QSizePolicy.Policy.Expanding,
                QSizePolicy.Policy.Expanding,
            )
            pixmap = QPixmap(path)
            if pixmap.isNull():
                image_label.setText("图片读取失败")
            else:
                image_label.setPixmap(
                    pixmap.scaled(
                        860,
                        480,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
            card_layout.addWidget(image_label, 1)
            self._grid.addWidget(card, index // columns, index % columns)
        for column in range(columns):
            self._grid.setColumnStretch(column, 1)

class DataAnalysisPage(_BaseWorkflowPage):
    """P6 behaviour analysis and historical trajectory view."""

    LIVE_EXPERIMENT_KEY = "__LIVE_EXPERIMENT__"

    _ANALYSIS_EXPORT_META = {
        "3d": {"title": "三维轨迹图", "data": "三维轨迹数据", "filename": "trajectory_3d"},
        "xy": {"title": "二维轨迹平面图", "data": "二维轨迹数据", "filename": "trajectory_xy"},
        "height": {"title": "高度图", "data": "高度数据", "filename": "height"},
        "sleep": {"title": "睡眠热力图", "data": "睡眠热力数据", "filename": "sleep_heatmap"},
        "cumulative": {"title": "累计二维距离图", "data": "累计二维距离数据", "filename": "cumulative_distance"},
        "total": {"title": "二维总路程图", "data": "二维总路程数据", "filename": "total_distance"},
        "minute": {"title": "每分钟运动量图", "data": "每分钟运动量数据", "filename": "minute_movement"},
        "dwell": {"title": "活动热力图", "data": "活动热力数据", "filename": "activity_heatmap"},
        "recognition": {"title": "行为识别统计图", "data": "行为识别数据", "filename": "behavior_recognition"},
        "infrared": {"title": "红外温度折线图", "data": "红外温度数据", "filename": "infrared_temperature"},
    }
    def __init__(self, context_provider: Any = None, health_source: Any = None, parent=None):
        self._base_context_provider = context_provider
        self._base_health_source = health_source
        self._selected_experiment_db_path: Optional[str] = None
        self._selected_experiment_excel_path: Optional[str] = None
        self._selected_experiment_selection_path: Optional[str] = None
        self._selected_trajectory_export_path: Optional[str] = None
        self._trajectory_selection_user_set = False
        self.selected_channel = 1; self._history_points: list[tuple[float, float, float]] = []; self._animation_index = 0
        self._trajectory_animation_active = False; self._trajectory_animation_index = 1
        self._trajectory_playback_speed = 1
        self._trajectory_base_interval_ms = 120
        self._trajectory_animation_states: dict[str, dict[str, Any]] = {
            module: {"active": False, "index": 1, "finished": False}
            for module in ("3d", "xy", "height", "sleep", "cumulative", "total", "minute", "dwell", "recognition", "infrared")
        }
        self._trajectory_channels: set[int] = {1}
        self._trajectory_modules: set[str] = {"3d"}
        self._zoomable_widgets: dict[int, tuple[QWidget, str]] = {}
        self._chart_export_sizes: dict[int, QSize] = {}
        self._live_analysis_mode = True
        self._live_snapshot_db_path: Optional[str] = None
        self._live_snapshot_source_db_path: Optional[str] = None
        self._live_snapshot_updated_at: Optional[float] = None
        self._live_snapshot_last_requested_at = 0.0
        self._live_snapshot_interval_seconds = 5.0
        self._live_snapshot_generation = 0
        self._live_snapshot_worker: Optional[LiveDatabaseSnapshotLoader] = None
        self._live_snapshot_error = ""
        self._trajectory_records_cache: dict[int, list[dict[str, Any]]] = {}
        # Playback uses one immutable snapshot so every selected cage advances with the same frame index.
        self._trajectory_animation_records: dict[int, list[dict[str, Any]]] = {}
        self._legacy_trajectory_cache_key: Optional[tuple[Any, ...]] = None
        self._legacy_trajectory_cache: Any = None
        self._legacy_comparison_cache_key: Optional[tuple[Any, ...]] = None
        self._legacy_comparison_cache: dict[int, Any] = {}
        self._historical_latest_rows: dict[int, dict[str, Any]] = {}
        self._historical_start_timestamp = 0.0
        self._historical_load_generation = 0
        self._historical_workers: set[_HistoricalWorkbookLoader] = set()
        self._analysis_worker: Optional[_AnalysisPreviewLoader] = None
        self._csv_export_worker: Optional[_AnalysisCsvExporter] = None
        self._analysis_requested_key: Optional[tuple[Any, ...]] = None
        self._analysis_completed_key: Optional[tuple[Any, ...]] = None
        self._analysis_started_at = 0.0
        self._analysis_next_allowed_at = 0.0
        self._compare_viewport: Optional[tuple[float, float]] = None
        self._left_channels: set[int] = {1}; self._right_channels: set[int] = {1}
        self._compare_left_series: list[tuple[str, float]] = []
        self._compare_right_series: list[tuple[str, float]] = []
        self._compare_left_channel_series: list[tuple[str, list[tuple[str, float]]]] = []
        self._compare_right_channel_series: list[tuple[str, list[tuple[str, float]]]] = []
        super().__init__(self._selected_experiment_context, self._selected_experiment_health, parent)
        self._build_ui(); self._enhance_bio_card(); self._animation_timer = QTimer(self); self._animation_timer.setInterval(120); self._animation_timer.timeout.connect(self._advance_animation)
        self.refresh_experiment_times()
        self.refresh_trajectory_experiments()
        self.start_refresh()

    def stop_refresh(self) -> None:
        super().stop_refresh()
        if self._csv_export_worker is not None:
            self._csv_export_worker.requestInterruption()
            self._csv_export_worker.wait(3000)
        if self._analysis_worker is not None:
            self._analysis_worker.requestInterruption()
            self._analysis_worker.wait(3000)
        self._cancel_historical_workbook_loads()
        self._cancel_live_snapshot_worker()
        live_worker = self._live_snapshot_worker
        if live_worker is not None:
            try:
                if live_worker.isRunning():
                    live_worker.wait(3000)
            except RuntimeError:
                pass
        for worker in tuple(self._historical_workers):
            if worker.isRunning():
                worker.wait(3000)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        QTimer.singleShot(0, self.refresh)

    def _analysis_export_meta(self, module: Optional[str] = None) -> dict[str, str]:
        selected = str(module or next(iter(self._trajectory_modules), "3d"))
        return dict(self._ANALYSIS_EXPORT_META.get(selected, self._ANALYSIS_EXPORT_META["3d"]))

    def _register_zoomable_chart(self, widget: QWidget, title: str) -> None:
        widget.installEventFilter(self)
        self._zoomable_widgets[id(widget)] = (widget, str(title))

    def eventFilter(self, watched, event) -> bool:  # noqa: N802 - Qt API
        item = self._zoomable_widgets.get(id(watched))
        if item is not None and event.type() == QEvent.Type.MouseButtonDblClick:
            widget, title = item
            dialog = ChartImageDialog(widget, title, self)
            dialog.exec()
            if dialog.export_size.isValid():
                self._chart_export_sizes[id(widget)] = QSize(dialog.export_size)
            return True
        return super().eventFilter(watched, event)

    def _chart_export_size(self, widget: QWidget) -> QSize:
        size = self._chart_export_sizes.get(id(widget), widget.size())
        return QSize(max(1, size.width()), max(1, size.height()))

    def _render_chart_svg(self, widget: QWidget, file_path: str, title: str) -> None:
        target = self._chart_export_size(widget)
        source_size = widget.size()
        if source_size.width() < 2 or source_size.height() < 2:
            source_size = widget.sizeHint()
        source_size = QSize(max(1, source_size.width()), max(1, source_size.height()))
        generator = QSvgGenerator()
        generator.setFileName(file_path)
        generator.setSize(target)
        generator.setViewBox(QRect(0, 0, target.width(), target.height()))
        generator.setTitle(title)
        painter = QPainter(generator)
        painter.scale(target.width() / source_size.width(), target.height() / source_size.height())
        widget.render(painter)
        painter.end()
    def _build_ui(self) -> None:
        _scroll, layout = self._canvas()
        experiment_row = QHBoxLayout(); experiment_row.setSpacing(8)
        experiment_row.addWidget(QLabel("实验时间"))
        self.experiment_time_combo = QComboBox()
        self.experiment_time_combo.setObjectName("analysisExperimentTimeCombo")
        self.experiment_time_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.experiment_time_combo.currentIndexChanged.connect(self._experiment_time_changed)
        experiment_row.addWidget(self.experiment_time_combo, 1)
        self.refresh_experiment_times_button = QPushButton("刷新")
        self.refresh_experiment_times_button.clicked.connect(self.refresh_experiment_times)
        self.analysis_loading_label = QLabel("加载中…")
        self.analysis_loading_label.setProperty("chip", True)
        self.analysis_loading_label.setProperty("tone", "info")
        self.analysis_loading_label.setVisible(False)
        experiment_row.addWidget(self.analysis_loading_label)
        self.live_analysis_status_label = QLabel("")
        self.live_analysis_status_label.setProperty("hint", True)
        self.live_analysis_status_label.setMinimumWidth(260)
        experiment_row.addWidget(self.live_analysis_status_label)
        experiment_row.addWidget(self.refresh_experiment_times_button)
        layout.addLayout(experiment_row)
        self._header(layout, "DATA ANALYSIS", "数据分析 · 高级统计", "实时实验固定显示最近 1 小时数据；历史实验仍按已保存 Excel / 轨迹 CSV 读取，实时快照允许有几秒延迟。")
        summary = QGridLayout(); self.metric_labels = {}
        for index, (key, title) in enumerate((("patterns", "行为模式"), ("main_behavior", "主导行为"), ("night_day", "昼夜活动比"), ("coverage", "轨迹覆盖度"), ("heat", "平均活动"), ("alerts", "异常通道"))):
            card, value, _detail = self._card(title, PENDING if key not in {"patterns", "main_behavior", "alerts"} else UNKNOWN, "真实数据/公式待定")
            self.metric_labels[key] = value; summary.addWidget(card, 0, index)
        layout.addLayout(summary)
        bio_card = QFrame(); bio_card.setProperty("card", True); bio_layout = QVBoxLayout(bio_card); bio_layout.setContentsMargins(14, 12, 14, 12); bio_layout.addWidget(QLabel("通道生物状态"));
        channel_row = QHBoxLayout(); self.channel_buttons = []
        self.channel_group = QButtonGroup(self); self.channel_group.setExclusive(True)
        for channel in range(1, 9):
            button = QPushButton(f"CH{channel:02d}"); button.setCheckable(True); button.setChecked(channel == 1); button.clicked.connect(lambda _checked=False, value=channel: self._select_channel(value)); self.channel_group.addButton(button, channel); self.channel_buttons.append(button); channel_row.addWidget(button)
        channel_row.addStretch(1); bio_layout.addLayout(channel_row); self.bio_label = QLabel("暂无该通道真实数据"); self.bio_label.setProperty("hint", True); bio_layout.addWidget(self.bio_label); layout.addWidget(bio_card)
        self._header(layout, "COMPARE", "数据对比图", "左右坐标分别选择一个采集指标和一个通道；未选通道或没有真实数据时，图表保持空白。")
        compare_grid = QGridLayout(); compare_grid.setHorizontalSpacing(14)
        left_card, self.left_metric, self.left_channel_buttons = self._axis_compare_card(
            "左轴 · 主坐标", "info", COMPARE_METRIC_LABELS, self._left_channels
        )
        right_card, self.right_metric, self.right_channel_buttons = self._axis_compare_card(
            "右轴 · 副坐标", "warning", COMPARE_METRIC_LABELS, self._right_channels
        )
        compare_grid.addWidget(left_card, 0, 0); compare_grid.addWidget(right_card, 0, 1)
        layout.addLayout(compare_grid)
        compare_chart = QFrame(); compare_chart.setProperty("card", True); chart_layout = QVBoxLayout(compare_chart); chart_layout.setContentsMargins(14, 12, 14, 12)
        chart_title = QLabel("对比曲线（近 1 小时 · 左右双轴）"); chart_title.setProperty("title", True); chart_layout.addWidget(chart_title)
        chart_hint = QLabel("左轴使用蓝色，右轴使用橙色；只有左右通道都选中并存在真实采集数据时才绘制曲线。")
        chart_title.setText("对比曲线（所选实验全部数据 · 左右双轴）")
        chart_hint.setText("实时实验显示最近 1 小时真实采集数据；滚轮放大/缩小，按住鼠标左键拖动查看。历史实验显示所选实验数据。")
        chart_hint.setProperty("hint", True); chart_hint.setWordWrap(True); chart_layout.addWidget(chart_hint)
        export_row = QHBoxLayout()
        export_row.addStretch(1)
        self.export_compare_csv_button = QPushButton("导出对比数据")
        self.export_compare_csv_button.clicked.connect(self._export_compare_csv)
        export_row.addWidget(self.export_compare_csv_button)
        self.export_compare_svg_button = QPushButton("导出 SVG 图片")
        self.export_compare_svg_button.clicked.connect(self._export_compare_svg)
        export_row.addWidget(self.export_compare_svg_button)
        chart_layout.addLayout(export_row)
        self.compare_canvas = DualAxisChart()
        self.compare_canvas.viewport_changed.connect(self._compare_viewport_changed)
        chart_layout.addWidget(self.compare_canvas, 1)
        self._register_zoomable_chart(self.compare_canvas, "对比曲线")
        layout.addWidget(compare_chart)
        self.compare_export_status = QLabel("")
        self.compare_export_status.setProperty("hint", True)
        chart_layout.addWidget(self.compare_export_status)
        # Match the established view: oxygen on the primary axis and carbon
        # dioxide on the secondary axis, while retaining all five choices.
        self.left_metric.setCurrentIndex(3)
        self.right_metric.setCurrentIndex(4)
        behaviour_grid = QGridLayout()
        heat_card, _, _ = self._card("行动轨迹 · 活动热力图", "", "颜色由真实轨迹点密度计算"); heat_layout = heat_card.layout(); self.heatmap_canvas = TrajectoryCanvas(); heat_layout.addWidget(self.heatmap_canvas, 1); self._register_zoomable_chart(self.heatmap_canvas, "活动热力图")
        history_card, _, _ = self._card("历史 3D 轨迹", "", "动画描述轨迹变动"); history_layout = history_card.layout(); self.history_canvas = TrajectoryCanvas(); history_layout.addWidget(self.history_canvas, 1); self._register_zoomable_chart(self.history_canvas, "历史 3D 轨迹"); self.play_button = QPushButton("播放历史轨迹"); self.play_button.clicked.connect(self._start_animation); history_layout.addWidget(self.play_button)
        day_card, _, _ = self._card("昼夜节律", PENDING, "公式待定")
        sleep_card, _, _ = self._card("运动蛰伏", PENDING, "沿用停留不动定义算法，汇总指标待定")
        behaviour_grid.addWidget(heat_card, 0, 0); behaviour_grid.addWidget(history_card, 0, 1)
        behaviour_grid.addWidget(day_card, 1, 0); behaviour_grid.addWidget(sleep_card, 1, 1)
        # The legacy single-channel cards remain instantiated for backwards
        # compatible refresh/animation state, but the visible UI uses the
        # multi-channel comparison section below to avoid duplicate entries.
        # This hidden compatibility container still receives refresh updates.
        # Keep it owned by the page so orphan-window cleanup cannot delete
        # its heatmap/history canvases during navigation.
        self._legacy_behaviour_widget = QWidget(self)
        self._legacy_behaviour_widget.setLayout(behaviour_grid)
        self._legacy_behaviour_widget.hide()

        # Multi-channel trajectory analysis.  Each module is independently
        # selectable, so a user can compare only the views relevant to the
        # current experiment while all channels remain bound to their own
        # persisted trajectory tables.
        trajectory_section = QFrame(); trajectory_section.setProperty("card", True)
        trajectory_layout = QVBoxLayout(trajectory_section); trajectory_layout.setContentsMargins(14, 12, 14, 12); trajectory_layout.setSpacing(8)
        trajectory_title = QLabel("行为、轨迹与红外温度对比"); trajectory_title.setProperty("title", True); trajectory_layout.addWidget(trajectory_title)
        trajectory_hint = QLabel("先选择一个对比模块，再选择一个或多个通道；实时实验显示最近 1 小时对应通道的真实数据；历史实验显示所选实验数据。")
        trajectory_hint.setProperty("hint", True); trajectory_hint.setWordWrap(True); trajectory_layout.addWidget(trajectory_hint)
        trajectory_experiment_row = QHBoxLayout(); trajectory_experiment_row.setSpacing(8)
        trajectory_experiment_row.addWidget(QLabel("轨迹实验"))
        self.trajectory_experiment_combo = QComboBox()
        self.trajectory_experiment_combo.setObjectName("trajectoryExperimentTimeCombo")
        self.trajectory_experiment_combo.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.trajectory_experiment_combo.currentIndexChanged.connect(self._trajectory_experiment_changed)
        trajectory_experiment_row.addWidget(self.trajectory_experiment_combo, 1)
        self.refresh_trajectory_experiments_button = QPushButton("刷新")
        self.refresh_trajectory_experiments_button.clicked.connect(self.refresh_trajectory_experiments)
        trajectory_experiment_row.addWidget(self.refresh_trajectory_experiments_button)
        trajectory_layout.addLayout(trajectory_experiment_row)
        trajectory_layout.addWidget(QLabel("先选择对比模块"))
        module_grid = QGridLayout(); module_grid.setHorizontalSpacing(8); module_grid.setVerticalSpacing(8)
        self.trajectory_module_buttons: dict[str, QPushButton] = {}
        self.trajectory_module_group = QButtonGroup(self); self.trajectory_module_group.setExclusive(True)
        module_definitions = (
            ("3d", "三维轨迹图对比"), ("xy", "二维轨迹平面对比"), ("height", "高度图"),
            ("sleep", "睡眠热力图对比"), ("cumulative", "累计二维轨迹距离对比"),
            ("total", "二维总路程对比"), ("minute", "每分钟运动量对比"),
            ("dwell", "活动热力图"), ("recognition", "行为识别统计"),
            ("infrared", "红外温度折线图对比"),
        )
        for index, (key, title) in enumerate(module_definitions):
            button = QPushButton(title); button.setCheckable(True); button.setChecked(key == "3d")
            button.toggled.connect(lambda checked, value=key: self._set_trajectory_module(value, checked))
            self.trajectory_module_group.addButton(button)
            self.trajectory_module_buttons[key] = button; module_grid.addWidget(button, index // 6, index % 6)
        trajectory_layout.addLayout(module_grid)
        channel_compare_row = QHBoxLayout(); channel_compare_row.addWidget(QLabel("再选择通道（可多选）")); self.trajectory_channel_buttons: list[QPushButton] = []
        for channel in range(1, 9):
            button = QPushButton(f"CH{channel:02d}"); button.setCheckable(True); button.setChecked(channel == 1)
            button.toggled.connect(lambda checked, value=channel: self._set_trajectory_channel(value, checked))
            self.trajectory_channel_buttons.append(button); channel_compare_row.addWidget(button)
        channel_compare_row.addStretch(1); trajectory_layout.addLayout(channel_compare_row)
        export_row = QHBoxLayout(); export_row.addStretch(1)
        self.trajectory_play_button = QPushButton("播放轨迹动画")
        self.trajectory_play_button.clicked.connect(self._toggle_trajectory_animation)
        export_row.addWidget(self.trajectory_play_button)
        self.trajectory_speed_label = QLabel("播放倍速")
        export_row.addWidget(self.trajectory_speed_label)
        self.trajectory_speed_combo = QComboBox()
        self.trajectory_speed_combo.setObjectName("trajectoryPlaybackSpeedCombo")
        for speed in (1, 2, 5, 10):
            self.trajectory_speed_combo.addItem(f"{speed}倍", speed)
        self.trajectory_speed_combo.currentIndexChanged.connect(self._set_trajectory_playback_speed)
        export_row.addWidget(self.trajectory_speed_combo)
        self._update_trajectory_play_visibility()
        self.export_trajectory_csv_button = QPushButton("导出轨迹对比数据")
        self.export_trajectory_csv_button.clicked.connect(self._export_trajectory_csv); export_row.addWidget(self.export_trajectory_csv_button)
        self.export_trajectory_svg_button = QPushButton("导出轨迹对比 SVG")
        self.export_trajectory_svg_button.clicked.connect(self._export_trajectory_svg); export_row.addWidget(self.export_trajectory_svg_button)
        trajectory_layout.addLayout(export_row)
        self.trajectory_compare_container = QWidget(); self.trajectory_compare_grid = QGridLayout(self.trajectory_compare_container); self.trajectory_compare_grid.setContentsMargins(0, 0, 0, 0); self.trajectory_compare_grid.setSpacing(10)
        self.trajectory_compare_plots: dict[str, QWidget] = {}
        self.trajectory_compare_cards: dict[str, QFrame] = {}
        for key, title in module_definitions:
            # Every card must belong to the analysis page even while hidden.
            # Otherwise the main window's orphan-window cleanup deletes the
            # unselected cards and their labels while this page keeps refreshing.
            card = QFrame(self.trajectory_compare_container); card.setProperty("card", True); card_layout = QVBoxLayout(card); card_layout.setContentsMargins(10, 8, 10, 8); card_layout.setSpacing(6)
            card_title = QLabel(title); card_title.setProperty("title", True); card_title.setContentsMargins(4, 0, 4, 2); card_layout.addWidget(card_title)
            if key == "recognition":
                plot = BehaviorCountChart()
                plot.setMinimumHeight(300)
                self.analysis_behavior_chart = plot
                self.analysis_recognition_channel_label = QLabel("按下方选择的通道显示")
                self.analysis_recognition_channel_label.setProperty("hint", True)
                card_layout.addWidget(self.analysis_recognition_channel_label)
                self.analysis_recognition_count_label = QLabel("行为类别：自由活动、蛰伏、攀爬")
                self.analysis_recognition_count_label.setProperty("hint", True)
                self.analysis_recognition_count_label.setWordWrap(True)
                card_layout.addWidget(self.analysis_recognition_count_label)
            else:
                plot = TrajectoryComparisonPlot("minute" if key == "minute" else key)
            card_layout.addWidget(plot, 1)
            self.trajectory_compare_plots[key] = plot; self.trajectory_compare_cards[key] = card; self._register_zoomable_chart(plot, title)
        self._relayout_trajectory_comparison()
        trajectory_layout.addWidget(self.trajectory_compare_container, 1); layout.addWidget(trajectory_section)
        layout.addStretch(1)

    def _live_context_status(self) -> str:
        raw_status = _provider_value(self._base_context_provider).get("experiment_status", "unknown")
        return str(getattr(raw_status, "value", raw_status) or "unknown").strip().lower()

    def _live_experiment_record(self) -> Optional[tuple[str, str]]:
        if self._live_context_status() not in {"starting", "running", "monitoring", "stopping"}:
            return None
        context = _provider_value(self._base_context_provider)
        started_at = context.get("started_at")
        label = "实时实验数据分析（当前实验）"
        try:
            if started_at not in (None, ""):
                label += " · " + datetime.fromtimestamp(float(started_at)).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError, OSError):
            pass
        return label, self.LIVE_EXPERIMENT_KEY

    def _live_source_database_path(self) -> Optional[str]:
        if self._live_context_status() not in {"starting", "running", "monitoring", "stopping"}:
            return None
        source = _provider_value(self._base_context_provider).get("experiment_db_path")
        if not source:
            return None
        source = os.path.abspath(str(source))
        return source if os.path.isfile(source) else None

    def _live_snapshot_directory(self, source_path: str) -> Path:
        context = _provider_value(self._base_context_provider)
        root = context.get("experiment_root_path")
        if root:
            return Path(str(root)) / "analysis_snapshots"
        return Path(source_path).parent.parent / "analysis_snapshots"

    def _update_live_analysis_status(self) -> None:
        label = getattr(self, "live_analysis_status_label", None)
        if label is None:
            return
        if not self._live_analysis_mode:
            label.setText("历史实验：按已保存数据读取")
            return
        source = self._live_source_database_path()
        worker = self._live_snapshot_worker
        if worker is not None and worker.isRunning():
            label.setText("实时分析：正在生成数据库快照…")
        elif source is None:
            if self._live_context_status() in {"starting", "running", "monitoring", "stopping"}:
                label.setText("实时分析：等待当前实验数据库…")
            else:
                label.setText("实时分析：当前未运行实验")
        elif self._live_snapshot_error:
            label.setText(f"实时分析：快照失败（{self._live_snapshot_error}）")
        elif self._live_snapshot_updated_at is not None:
            delay = max(0, int(time.time() - self._live_snapshot_updated_at))
            stamp = datetime.fromtimestamp(self._live_snapshot_updated_at).strftime("%H:%M:%S")
            label.setText(f"实时分析：{stamp} 更新，约延迟 {delay} 秒")
        else:
            label.setText("实时分析：等待首次快照")

    def _cleanup_live_snapshots(self, directory: Path, current_path: Path) -> None:
        try:
            files = sorted(
                directory.glob("analysis_live_*.db"),
                key=lambda item: item.stat().st_mtime,
                reverse=True,
            )
        except OSError:
            return
        keep = {str(current_path.resolve())}
        for item in files:
            if len(keep) < 4:
                keep.add(str(item.resolve()))
                continue
            try:
                item.unlink()
            except OSError:
                pass

    def _live_snapshot_worker_finished(self, worker: LiveDatabaseSnapshotLoader) -> None:
        if self._live_snapshot_worker is worker:
            self._live_snapshot_worker = None
        worker.deleteLater()
        self._update_live_analysis_status()

    def _live_snapshot_loaded(
        self,
        generation: int,
        source_path: str,
        snapshot_path: str,
        updated_at: float,
    ) -> None:
        if not self._live_analysis_mode or generation != self._live_snapshot_generation:
            return
        current_source = self._live_source_database_path()
        if current_source and os.path.normcase(os.path.abspath(source_path)) != os.path.normcase(current_source):
            return
        self._live_snapshot_source_db_path = os.path.abspath(source_path)
        self._live_snapshot_db_path = os.path.abspath(snapshot_path)
        self._live_snapshot_updated_at = float(updated_at)
        self._live_snapshot_error = ""
        self._cleanup_live_snapshots(Path(snapshot_path).parent, Path(snapshot_path))
        self._update_live_analysis_status()
        self.refresh()

    def _live_snapshot_failed(self, generation: int, _source_path: str, error: str) -> None:
        if not self._live_analysis_mode or generation != self._live_snapshot_generation:
            return
        self._live_snapshot_error = str(error)
        logger.warning(f"实时分析快照失败：{error}")
        self._update_live_analysis_status()

    def _maybe_start_live_snapshot(self) -> None:
        if not self._live_analysis_mode:
            return
        worker = self._live_snapshot_worker
        if worker is not None and worker.isRunning():
            self._update_live_analysis_status()
            return
        source = self._live_source_database_path()
        if source is None:
            self._update_live_analysis_status()
            return
        now = time.monotonic()
        if (
            os.path.normcase(source) == os.path.normcase(self._live_snapshot_source_db_path or "")
            and now - self._live_snapshot_last_requested_at < self._live_snapshot_interval_seconds
        ):
            self._update_live_analysis_status()
            return
        if os.path.normcase(source) != os.path.normcase(self._live_snapshot_source_db_path or ""):
            self._live_snapshot_db_path = None
            self._live_snapshot_updated_at = None
        self._live_snapshot_source_db_path = source
        self._live_snapshot_last_requested_at = now
        self._live_snapshot_generation += 1
        snapshot_directory = self._live_snapshot_directory(source)
        snapshot_path = snapshot_directory / f"analysis_live_{int(time.time() * 1000)}.db"
        worker = LiveDatabaseSnapshotLoader(self._live_snapshot_generation, source, str(snapshot_path))
        self._live_snapshot_worker = worker
        worker.loaded.connect(self._live_snapshot_loaded)
        worker.failed.connect(self._live_snapshot_failed)
        worker.finished.connect(lambda current=worker: self._live_snapshot_worker_finished(current))
        self._update_live_analysis_status()
        worker.start()

    def _cancel_live_snapshot_worker(self, clear_snapshot: bool = False) -> None:
        self._live_snapshot_generation += 1
        worker = self._live_snapshot_worker
        if worker is not None and worker.isRunning():
            worker.requestInterruption()
        if clear_snapshot:
            self._live_snapshot_db_path = None
            self._live_snapshot_source_db_path = None
            self._live_snapshot_updated_at = None
            self._live_snapshot_error = ""

    def _activate_live_analysis(self, refresh_page: bool = True) -> None:
        self._reset_compare_viewport()
        self._live_analysis_mode = True
        self._selected_experiment_selection_path = self.LIVE_EXPERIMENT_KEY
        self._selected_experiment_db_path = None
        self._selected_experiment_excel_path = None
        self._selected_trajectory_export_path = None
        self._trajectory_selection_user_set = False
        self._legacy_comparison_cache_key = None
        self._legacy_comparison_cache = {}
        self._historical_latest_rows = {}
        self._historical_start_timestamp = 0.0
        self._cancel_historical_workbook_loads()
        self._set_historical_loading(False)
        self._live_snapshot_error = ""
        self._update_live_analysis_status()
        if hasattr(self, "trajectory_experiment_combo"):
            self.refresh_trajectory_experiments()
        if refresh_page:
            self.refresh()
    def _selected_experiment_context(self) -> dict[str, Any]:
        context = dict(_provider_value(self._base_context_provider))
        if self._live_analysis_mode:
            snapshot = self._live_snapshot_db_path
            if snapshot and os.path.isfile(snapshot):
                context["experiment_db_path"] = snapshot
            return context
        selected = self._selected_experiment_db_path
        if selected is None:
            return context
        if not selected:
            context["experiment_db_path"] = None
            context["experiment_root_path"] = None
            context.pop("channel_individuals", None)
            context.pop("channel_data_config", None)
            return context
        current = context.get("experiment_db_path")
        if not current or os.path.normcase(os.path.abspath(str(current))) != os.path.normcase(selected):
            # Live health and animal metadata belong to the current run, not a
            # separately selected historical experiment.
            context.pop("channel_individuals", None)
            context.pop("channel_data_config", None)
        context["experiment_db_path"] = selected
        context["experiment_root_path"] = os.path.dirname(os.path.dirname(selected))
        return context

    def _selected_experiment_health(self) -> Mapping[str, Any]:
        if self._live_analysis_mode:
            return _provider_value(self._base_health_source)
        selected = self._selected_experiment_db_path
        current = _provider_value(self._base_context_provider).get("experiment_db_path")
        if selected is not None and (
            not selected or not current
            or os.path.normcase(os.path.abspath(str(current))) != os.path.normcase(selected)
        ):
            return {}
        return _provider_value(self._base_health_source)
    def _experiment_storage_root(self) -> str:
        context = _provider_value(self._base_context_provider)
        specified = context.get("experiment_storage_root")
        if specified:
            return os.path.abspath(str(specified))
        config = global_setting.get_setting("monitor_data", {}) or {}
        storage = config.get("STORAGE", {}) if isinstance(config, Mapping) else {}
        folder = str(storage.get("fold_path", "./data/") or "./data/")
        subfolder = str(storage.get("sub_fold_path", "/monitor_data/") or "/monitor_data/")
        return str(resolve_project_path(Path(folder) / subfolder.lstrip("/\\")))

    def _scan_experiment_times(self) -> list[tuple[str, str]]:
        # Historical monitor analysis is file-backed: do not present every
        # transient data.db directory as if an exported experiment exists.
        workbooks = scan_experiment_files(self._experiment_storage_root())
        records = [
            (f"{item.started_at:%Y-%m-%d %H:%M:%S.%f}"[:-3] + f" · Excel（{item.experiment_name or '实验'}）", str(item.path))
            for item in workbooks
        ]
        trajectory_exports = scan_trajectory_experiments(TRAJECTORY_EXPORT_DIR)
        for item in trajectory_exports:
            if any(abs((item.started_at - workbook.started_at).total_seconds()) <= 300 for workbook in workbooks):
                continue
            records.append((f"{item.started_at:%Y-%m-%d %H:%M:%S.%f}"[:-3] + " · 轨迹 CSV", str(item.path)))
        if records:
            return records

        if self._live_analysis_mode:
            return []
        # Keep live, not-yet-exported experiments usable until their workbook
        # exists. This is a fallback only, never mixed into historical Excel.
        root = self._experiment_storage_root()
        if not os.path.isdir(root):
            return []
        fallback: list[tuple[datetime, str, str]] = []
        with os.scandir(root) as entries:
            for entry in entries:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                db_path = os.path.join(entry.path, "data", "data.db")
                match = re.search(r"20\d{2}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}_\d{3}(?!\d)", entry.name)
                if not match or not os.path.isfile(db_path):
                    continue
                try:
                    started = datetime.strptime(match.group(), "%Y_%m_%d_%H_%M_%S_%f")
                except ValueError:
                    continue
                fallback.append((started, started.strftime("%Y-%m-%d %H:%M:%S.%f")[:-3] + " · 实时数据库", os.path.abspath(db_path)))
        fallback.sort(reverse=True)
        return [(label, path) for _stamp, label, path in fallback]

    def _scan_trajectory_experiment_times(self) -> list[tuple[str, str]]:
        """Return only the legacy trajectory exports for the lower selector."""
        return [
            (
                f"{item.started_at:%Y-%m-%d %H:%M:%S.%f}"[:-3] + " · 轨迹 CSV",
                str(item.path),
            )
            for item in scan_trajectory_experiments(TRAJECTORY_EXPORT_DIR)
        ]

    def refresh_trajectory_experiments(self) -> None:
        """Refresh the trajectory selector without changing monitor Excel."""
        preferred = self._selected_trajectory_export_path
        if not self._trajectory_selection_user_set and not preferred:
            monitor_path = self._selected_experiment_excel_path
            if not monitor_path:
                current = self.experiment_time_combo.currentData()
                monitor_path = str(current or "") if str(current or "").lower().endswith(".xlsx") else None
            if monitor_path:
                started = parse_experiment_time(monitor_path)
                if started is not None:
                    matched = find_matching_trajectory_export(TRAJECTORY_EXPORT_DIR, started)
                    preferred = str(matched) if matched is not None else None

        records = self._scan_trajectory_experiment_times()
        self.trajectory_experiment_combo.blockSignals(True)
        try:
            self.trajectory_experiment_combo.clear()
            self.trajectory_experiment_combo.addItem("实时轨迹数据（跟随上方当前实验）", "")
            selected_index = 0
            for label, path in records:
                self.trajectory_experiment_combo.addItem(label, path)
                if preferred and os.path.normcase(path) == os.path.normcase(preferred):
                    selected_index = self.trajectory_experiment_combo.count() - 1
            self.trajectory_experiment_combo.setCurrentIndex(selected_index)
        finally:
            self.trajectory_experiment_combo.blockSignals(False)

        if selected_index > 0:
            self._selected_trajectory_export_path = str(
                self.trajectory_experiment_combo.itemData(selected_index)
            )
        elif self._trajectory_selection_user_set:
            self._selected_trajectory_export_path = None
        else:
            self._selected_trajectory_export_path = None
        self._legacy_trajectory_cache_key = None
        self._legacy_trajectory_cache = None

    def _trajectory_experiment_changed(self, _index: int) -> None:
        self._reset_compare_viewport()
        self._trajectory_selection_user_set = True
        selected = str(self.trajectory_experiment_combo.currentData() or "")
        self._selected_trajectory_export_path = selected if selected and os.path.isdir(selected) else None
        if self._selected_trajectory_export_path:
            self._activate_monitor_workbook(
                self._matching_monitor_workbook_for_trajectory(
                    self._selected_trajectory_export_path
                )
            )
        self._legacy_trajectory_cache_key = None
        self._legacy_trajectory_cache = None
        self._reset_trajectory_animation_states()
        self.refresh()

    def _matching_monitor_workbook_for_trajectory(self, export_path: str) -> Optional[str]:
        """Return the data/monitor_data workbook belonging to a trajectory run."""
        started = parse_trajectory_export_time(export_path)
        if started is None:
            return None
        matched = find_matching_monitor_workbook(self._experiment_storage_root(), started)
        return str(matched) if matched is not None else None

    def _activate_monitor_workbook(self, workbook_path: Optional[str]) -> None:
        """Make a matched monitor Excel file the active historical source."""
        if not workbook_path:
            return
        workbook_path = os.path.abspath(str(workbook_path))
        current = self._selected_experiment_excel_path
        if current and os.path.normcase(os.path.abspath(current)) == os.path.normcase(workbook_path):
            return
        self._selected_experiment_excel_path = workbook_path
        self._selected_experiment_selection_path = workbook_path
        candidate = os.path.join(
            self._experiment_storage_root(),
            Path(workbook_path).stem,
            "data",
            "data.db",
        )
        self._selected_experiment_db_path = os.path.abspath(candidate) if os.path.isfile(candidate) else ""
        self._legacy_comparison_cache_key = None
        self._legacy_comparison_cache = {}
        self._historical_latest_rows = {}
        self._historical_start_timestamp = 0.0
        self._start_historical_workbook_load(workbook_path)

    def _matching_monitor_db(self, export_path: str) -> Optional[str]:
        """Attach monitor data to a selected legacy trajectory run by time."""
        from Module.mouse_trajectory_analysis.analysis_core import parse_trajectory_export_time

        started = parse_trajectory_export_time(export_path)
        root = self._experiment_storage_root()
        if started is None or not os.path.isdir(root):
            return None
        best: tuple[float, str] | None = None
        try:
            with os.scandir(root) as entries:
                for entry in entries:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    match = re.search(r"20\d{2}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}_\d{3}(?!\d)", entry.name)
                    if match is None:
                        continue
                    try:
                        difference = abs((datetime.strptime(match.group(), "%Y_%m_%d_%H_%M_%S_%f") - started).total_seconds())
                    except ValueError:
                        continue
                    db_path = os.path.join(entry.path, "data", "data.db")
                    if difference <= 300 and os.path.isfile(db_path) and (best is None or difference < best[0]):
                        best = (difference, os.path.abspath(db_path))
        except OSError:
            return None
        return best[1] if best else None

    def refresh_experiment_times(self) -> None:
        initial_selection = (
            self._selected_experiment_selection_path is None
            and self._selected_experiment_db_path is None
            and self._selected_experiment_excel_path is None
        )
        preferred = self.LIVE_EXPERIMENT_KEY if self._live_analysis_mode else (
            self._selected_experiment_selection_path or self._selected_experiment_excel_path
        )
        if not self._live_analysis_mode and preferred is None and self._selected_experiment_db_path is None:
            current = _provider_value(self._base_context_provider).get("experiment_db_path")
            if current:
                folder = os.path.basename(os.path.dirname(os.path.dirname(str(current))))
                workbook = os.path.join(self._experiment_storage_root(), f"{folder}.xlsx")
                if os.path.isfile(workbook):
                    preferred = os.path.abspath(workbook)
                match = re.search(r"20\d{2}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}_\d{3}(?!\d)", folder)
                if match and preferred is None:
                    try:
                        started = datetime.strptime(match.group(), "%Y_%m_%d_%H_%M_%S_%f")
                        candidate = find_matching_trajectory_export(TRAJECTORY_EXPORT_DIR, started)
                        preferred = str(candidate) if candidate is not None else None
                    except ValueError:
                        pass
                if preferred is None and os.path.isfile(str(current)):
                    preferred = os.path.abspath(str(current))
        records = self._scan_experiment_times()
        live_record = self._live_experiment_record()
        if live_record is not None:
            records.insert(0, live_record)
        self.experiment_time_combo.blockSignals(True)
        try:
            self.experiment_time_combo.clear()
            self.experiment_time_combo.addItem("请选择实验时间", "")
            selected_index = 0
            for label, path in records:
                self.experiment_time_combo.addItem(label, path)
                if preferred and path == self.LIVE_EXPERIMENT_KEY and preferred == self.LIVE_EXPERIMENT_KEY:
                    selected_index = self.experiment_time_combo.count() - 1
                elif preferred and path != self.LIVE_EXPERIMENT_KEY and os.path.normcase(path) == os.path.normcase(preferred):
                    selected_index = self.experiment_time_combo.count() - 1
            self.experiment_time_combo.setCurrentIndex(selected_index)
        finally:
            self.experiment_time_combo.blockSignals(False)
        selected_data = str(self.experiment_time_combo.itemData(selected_index) or "")
        if selected_data == self.LIVE_EXPERIMENT_KEY:
            self._activate_live_analysis(refresh_page=False)
            self.refresh()
        elif not initial_selection:
            self._experiment_time_changed(selected_index)
        else:
            self.refresh()
    @staticmethod
    def _workbook_cache_key(path: str) -> tuple[Any, ...]:
        source = Path(path)
        stat = source.stat()
        return (str(source.resolve()), stat.st_size, stat.st_mtime_ns)

    def _set_historical_loading(self, loading: bool, text: str = "加载中…", tone: str = "info") -> None:
        self._set_chip(self.analysis_loading_label, text, tone)
        self.analysis_loading_label.setVisible(bool(loading))

    def _cancel_historical_workbook_loads(self) -> None:
        self._historical_load_generation += 1
        for worker in tuple(self._historical_workers):
            if worker.isRunning():
                worker.requestInterruption()

    def _start_historical_workbook_load(self, path: str) -> None:
        try:
            cache_key = self._workbook_cache_key(path)
        except OSError as error:
            self._set_historical_loading(True, f"加载失败：{error}", "fault")
            return
        if cache_key == self._legacy_comparison_cache_key:
            self._set_historical_loading(False)
            return
        if any(worker.isRunning() and worker.cache_key == cache_key for worker in self._historical_workers):
            self._set_historical_loading(True)
            return

        self._cancel_historical_workbook_loads()
        generation = self._historical_load_generation
        worker = _HistoricalWorkbookLoader(generation, cache_key, path)
        self._historical_workers.add(worker)
        worker.loaded.connect(self._historical_workbook_loaded)
        worker.failed.connect(self._historical_workbook_failed)
        worker.finished.connect(lambda current=worker: self._historical_worker_finished(current))
        self._set_historical_loading(True)
        worker.start()

    def _historical_worker_finished(self, worker: _HistoricalWorkbookLoader) -> None:
        self._historical_workers.discard(worker)
        worker.deleteLater()

    def _historical_workbook_loaded(
        self,
        generation: int,
        cache_key: tuple[Any, ...],
        start_timestamp: float,
        comparison: Mapping[int, Any],
        latest_rows: Mapping[int, Mapping[str, Any]],
    ) -> None:
        if generation != self._historical_load_generation:
            return
        try:
            current_key = self._workbook_cache_key(self._selected_experiment_excel_path or "")
        except OSError:
            return
        if cache_key != current_key:
            return
        self._legacy_comparison_cache_key = cache_key
        self._legacy_comparison_cache = dict(comparison)
        self._historical_latest_rows = {
            int(channel): dict(values) for channel, values in latest_rows.items()
        }
        self._historical_start_timestamp = float(start_timestamp)
        self._set_historical_loading(False)
        self.refresh()

    def _historical_workbook_failed(self, generation: int, _cache_key: tuple[Any, ...], error: str) -> None:
        if generation != self._historical_load_generation:
            return
        logger.warning(f"读取实验 Excel 失败：{error}")
        self._set_historical_loading(True, "加载失败", "fault")

    def _experiment_time_changed(self, _index: int) -> None:
        self._reset_compare_viewport()
        selected = str(self.experiment_time_combo.currentData() or "")
        if selected == self.LIVE_EXPERIMENT_KEY:
            self._activate_live_analysis()
            return
        self._live_analysis_mode = False
        self._cancel_live_snapshot_worker(clear_snapshot=True)
        self._selected_experiment_selection_path = selected or None
        self._selected_experiment_excel_path = selected if selected.lower().endswith(".xlsx") and os.path.isfile(selected) else None
        if selected and os.path.isdir(selected):
            # Keep compatibility with the old mixed list, while making a
            # deliberate upper-selector directory choice explicit.
            self._selected_trajectory_export_path = selected
            self._trajectory_selection_user_set = True
        if self._selected_experiment_excel_path:
            candidate = os.path.join(self._experiment_storage_root(), Path(selected).stem, "data", "data.db")
            self._selected_experiment_db_path = os.path.abspath(candidate) if os.path.isfile(candidate) else ""
        elif selected and os.path.isdir(selected):
            self._selected_experiment_db_path = self._matching_monitor_db(selected) or ""
        else:
            self._selected_experiment_db_path = selected if selected.lower().endswith(".db") else ""
        self._legacy_comparison_cache_key = None
        self._legacy_comparison_cache = {}
        self._historical_latest_rows = {}
        self._historical_start_timestamp = 0.0
        if self._selected_experiment_excel_path:
            self._start_historical_workbook_load(self._selected_experiment_excel_path)
        else:
            self._cancel_historical_workbook_loads()
            self._set_historical_loading(False)
        self._update_live_analysis_status()
        self.refresh()
    def _enhance_bio_card(self) -> None:
        """Add the screenshot-style selected-cage measurement tiles."""
        card = self.bio_label.parentWidget()
        card_layout = card.layout() if card is not None else None
        if card_layout is None:
            return
        self.bio_status_label = QLabel("None")
        self.bio_status_label.setProperty("chip", True)
        self.bio_status_label.setProperty("tone", "neutral")
        card_layout.addWidget(self.bio_status_label, 0, Qt.AlignmentFlag.AlignLeft)
        metrics = QGridLayout(); metrics.setSpacing(10)
        self.bio_metric_labels: dict[str, QLabel] = {}
        for index, (key, title) in enumerate((
            ("weight", "称重"), ("food", "饮食"), ("water", "饮水"),
            ("rer", "呼吸熵（RER）"), ("movement", "运动"),
            ("behavior", "行为"), ("temperature", "体温"),
        )):
            tile = QFrame(); tile.setProperty("card", True)
            tile.setStyleSheet("QFrame { background:#f1f7fd; border:0; border-radius:10px; }")
            tile_layout = QVBoxLayout(tile); tile_layout.setContentsMargins(12, 9, 12, 9); tile_layout.setSpacing(2)
            title_label = QLabel(title); title_label.setProperty("label", True); tile_layout.addWidget(title_label)
            value_label = QLabel("None"); value_label.setProperty("value", True); tile_layout.addWidget(value_label)
            self.bio_metric_labels[key] = value_label
            metrics.addWidget(tile, 0, index)
        # Keep the old internal key as a compatibility alias for integrations
        # that still look up ``metabolism``; the displayed metric is RER.
        self.bio_metric_labels["metabolism"] = self.bio_metric_labels["rer"]
        card_layout.addLayout(metrics)

    def _axis_compare_card(self, title: str, tone: str, metrics: list[str], target: set[int]) -> tuple[QFrame, QComboBox, list[QPushButton]]:
        card = QFrame(); card.setProperty("card", True)
        box = QVBoxLayout(card); box.setContentsMargins(14, 12, 14, 12); box.setSpacing(8)
        top = QHBoxLayout(); top.setSpacing(10)
        chip = QLabel(title); chip.setProperty("chip", True); chip.setProperty("tone", tone); top.addWidget(chip)
        metric = QComboBox(); metric.addItems(metrics); metric.currentIndexChanged.connect(self._compare_source_changed); top.addWidget(metric, 1)
        box.addLayout(top)
        hint = QLabel("可多选通道；同一坐标轴使用相近色系区分")
        hint.setProperty("hint", True); box.addWidget(hint)
        buttons_layout = QHBoxLayout(); buttons_layout.setSpacing(6)
        buttons: list[QPushButton] = []
        for channel in range(1, 9):
            button = QPushButton(f"CH{channel:02d}"); button.setCheckable(True)
            button.blockSignals(True)
            button.setChecked(channel in target)
            button.blockSignals(False)
            button.toggled.connect(lambda checked, value=channel, selected=target, btns=buttons: self._set_compare_channel(selected, btns, value, checked))
            buttons.append(button); buttons_layout.addWidget(button)
        buttons_layout.addStretch(1); box.addLayout(buttons_layout)
        return card, metric, buttons

    def _reset_compare_viewport(self) -> None:
        self._compare_viewport = None
        if hasattr(self, "compare_canvas"):
            self.compare_canvas.reset_view()

    def _compare_source_changed(self, _index: int = -1) -> None:
        self._reset_compare_viewport()
        self._refresh_compare_chart()

    def _compare_viewport_changed(self, start: float, end: float) -> None:
        start = max(0.0, min(1.0, float(start)))
        end = max(start, min(1.0, float(end)))
        self._compare_viewport = None if start <= 0.000001 and end >= 0.999999 else (start, end)
        self._schedule_analysis_refresh(force=True)

    def _relayout_trajectory_comparison(self) -> None:
        """Show one selected module and let it consume the available width."""
        if not hasattr(self, "trajectory_compare_grid"):
            return
        selected = next(iter(self._trajectory_modules), "3d")
        if selected not in self.trajectory_compare_cards:
            selected = "3d"
            self._trajectory_modules = {selected}
        for key, card in self.trajectory_compare_cards.items():
            self.trajectory_compare_grid.removeWidget(card)
            card.setVisible(False)
        card = self.trajectory_compare_cards[selected]
        self.trajectory_compare_grid.addWidget(card, 0, 0)
        card.setVisible(True)
        self.trajectory_compare_grid.setColumnStretch(0, 1)
        self.trajectory_compare_grid.setColumnStretch(1, 0)
        self.trajectory_compare_grid.setRowStretch(0, 1)
        self.trajectory_compare_container.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.trajectory_compare_container.setMinimumHeight(self.trajectory_compare_plots[selected].minimumHeight() + 58)
        self.trajectory_compare_container.updateGeometry()

    def _set_trajectory_channel(self, channel: int, checked: bool) -> None:
        if checked:
            self._trajectory_channels.add(int(channel))
        else:
            self._trajectory_channels.discard(int(channel))
        # Keep at least one channel selected so the comparison always has a
        # meaningful adaptive canvas.
        if not self._trajectory_channels:
            self._trajectory_channels.add(int(channel))
            button = self.trajectory_channel_buttons[int(channel) - 1]
            button.blockSignals(True); button.setChecked(True); button.blockSignals(False)
        self._reset_trajectory_animation_states()
        self.refresh()
        self._refresh_analysis_recognition()

    def _set_trajectory_module(self, module: str, checked: bool) -> None:
        if not checked:
            # QButtonGroup keeps one module selected; ignore the uncheck event
            # emitted for the previously selected button.
            return
        previous = next(iter(self._trajectory_modules), "3d")
        if previous != str(module):
            previous_state = self._trajectory_animation_states.get(previous)
            if previous_state is not None:
                previous_state["active"] = False
            if hasattr(self, "_animation_timer"):
                self._animation_timer.stop()
            self._trajectory_animation_active = False
        self._trajectory_modules = {str(module)}
        meta = self._analysis_export_meta(str(module))
        self.export_trajectory_csv_button.setText(f"导出{meta['data']}")
        self.export_trajectory_svg_button.setText(f"导出{meta['title']} SVG")
        self._update_trajectory_play_visibility()
        self._relayout_trajectory_comparison()
        self.refresh()

    def _set_trajectory_playback_speed(self, index: int) -> None:
        """Change playback cadence without changing the current frame."""
        if not hasattr(self, "trajectory_speed_combo"):
            return
        value = self.trajectory_speed_combo.itemData(index)
        try:
            speed = int(value)
        except (TypeError, ValueError):
            speed = 1
        self._trajectory_playback_speed = speed if speed in (1, 2, 5, 10) else 1
        if hasattr(self, "_animation_timer"):
            interval = max(1, int(round(self._trajectory_base_interval_ms / self._trajectory_playback_speed)))
            self._animation_timer.setInterval(interval)

    def _update_trajectory_play_visibility(self) -> None:
        """Only trajectory views with a time-ordered path expose playback."""
        if not hasattr(self, "trajectory_play_button"):
            return
        selected = next(iter(self._trajectory_modules), "3d")
        # The legacy height view is redrawn from the selected trajectory samples.
        # Summary charts, heatmaps, recognition and infrared views remain static.
        visible = selected in {"3d", "xy", "height"}
        self.trajectory_play_button.setVisible(visible)
        if hasattr(self, "trajectory_speed_label"):
            self.trajectory_speed_label.setVisible(visible)
        if hasattr(self, "trajectory_speed_combo"):
            self.trajectory_speed_combo.setVisible(visible)
        if not visible:
            return
        state = self._trajectory_animation_states.setdefault(
            selected,
            {"active": False, "index": 1, "finished": False},
        )
        if state["active"]:
            text = "暂停轨迹动画"
        elif state["finished"]:
            text = "重新播放轨迹动画"
        elif state["index"] > 1:
            text = "继续播放轨迹动画"
        else:
            text = "播放轨迹动画"
        self.trajectory_play_button.setText(text)

    def _reset_trajectory_animation_states(self) -> None:
        """Reset playback independently for each trajectory view after data changes."""
        if hasattr(self, "_animation_timer"):
            self._animation_timer.stop()
        for state in self._trajectory_animation_states.values():
            state.update(active=False, index=1, finished=False)
        self._trajectory_animation_active = False
        self._trajectory_animation_index = 1
        self._trajectory_animation_records = {}
        self._update_trajectory_play_visibility()

    def _selected_trajectory_animation_state(self) -> Optional[dict[str, Any]]:
        selected = next(iter(self._trajectory_modules), "3d")
        return self._trajectory_animation_states.get(selected)

    @staticmethod
    def _trajectory_time_seconds(value: Any) -> Optional[float]:
        numeric = _number(value)
        if numeric is not None:
            return numeric
        if value in (None, ""):
            return None
        text = str(value).strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(text).timestamp()
        except (TypeError, ValueError, OverflowError):
            pass
        # Common trajectory export names contain YYYYMMDD_HHMMSS_mmm.
        match = re.search(r"(20\d{2})[_-]?(\d{2})[_-]?(\d{2})[_-](\d{2})(\d{2})(\d{2})(?:[_-](\d{1,3}))?", text)
        if match:
            try:
                values = [int(item or 0) for item in match.groups()]
                return datetime(values[0], values[1], values[2], values[3], values[4], values[5], values[6] * 1000).timestamp()
            except (TypeError, ValueError, OverflowError):
                return None
        return None

    @classmethod
    def _trajectory_elapsed(cls, records: list[dict[str, Any]]) -> list[float]:
        stamps = [cls._trajectory_time_seconds(item.get("time")) for item in records]
        valid = [value for value in stamps if value is not None]
        if len(valid) < 2:
            return [float(index) for index in range(len(records))]
        origin = valid[0]
        return [max(0.0, (value - origin) if value is not None else float(index)) for index, value in enumerate(stamps)]

    @classmethod
    def _trajectory_distance_metrics(cls, records: list[dict[str, Any]]) -> tuple[list[tuple[float, float, float]], list[float], list[float]]:
        points = []
        for item in records:
            source = item["point"]
            converted = TrajectoryCanvas._to_cage_mm(source)
            points.append(_PhysicalMillimeterPoint(*converted) if isinstance(source, _PhysicalMillimeterPoint) else converted)
        elapsed = cls._trajectory_elapsed(records)
        steps = [0.0]
        for index, (first, second) in enumerate(zip(points, points[1:]), 1):
            legacy_step = records[index].get("legacy_distance_step_mm")
            steps.append(float(legacy_step) if legacy_step is not None else math.hypot(second[0] - first[0], second[1] - first[1]))
        cumulative: list[float] = []
        total = 0.0
        for step in steps:
            total += step; cumulative.append(total)
        return points, elapsed, cumulative

    @classmethod
    def _minute_distance(cls, elapsed: list[float], steps: list[float]) -> list[tuple[float, float]]:
        if not elapsed or not steps:
            return []
        buckets: dict[int, float] = {}
        for time_value, step in zip(elapsed, steps):
            bucket = max(0, int(float(time_value) // 60.0)); buckets[bucket] = buckets.get(bucket, 0.0) + float(step)
        return [(float(bucket), buckets[bucket]) for bucket in sorted(buckets)]

    @classmethod
    def _sleep_states(cls, records: list[dict[str, Any]], segment_seconds: float = 10.0) -> list[float]:
        points, elapsed, _cumulative = cls._trajectory_distance_metrics(records)
        return sleep_segments(points, elapsed, segment_seconds)

    def _trajectory_records_for_channels(self, channels: Optional[set[int]] = None) -> dict[int, list[dict[str, Any]]]:
        records: dict[int, list[dict[str, Any]]] = {}
        requested_channels = self._trajectory_channels if channels is None else {int(channel) for channel in channels}
        historical_selection = bool(
            self._selected_experiment_excel_path or self._selected_trajectory_export_path
        )
        # The live trajectory worker persists the same legacy CSV format while
        # the experiment is running.  Use it for every trajectory module,
        # including 3D, before falling back to the monitor database.  The
        # previous condition skipped 3D/height in live mode, which made the
        # analysis page show an empty chart even though trajectory.csv already
        # contained fresh samples.
        legacy = self._legacy_trajectory_analysis() if (historical_selection or self._live_analysis_mode) else None
        for channel in sorted(requested_channels):
            if self._trajectory_modules == {"height"} and historical_selection:
                # The legacy analysis object intentionally exposes only X/Y.
                # Keep the persisted Z column for the calculated height chart.
                records[channel] = self._height_records_from_export(channel)
                continue
            channel_data = legacy.channels.get(channel) if legacy is not None else None
            if channel_data is not None and channel_data.valid_rows:
                records[channel] = [
                    {
                        "point": _PhysicalMillimeterPoint(channel_data.x_mm[index], channel_data.y_mm[index]),
                        "time": float(channel_data.times[index]),
                        "legacy_distance_step_mm": float(channel_data.distance_steps_mm[index]),
                        "source": "legacy_trajectory_csv",
                    }
                    for index in range(channel_data.total_rows)
                    if bool(channel_data.valid[index])
                ]
            elif not historical_selection:
                records[channel] = MachineVisionPage._trajectory_records(self, channel=channel, limit=None)
            else:
                # A historical monitor workbook is not a trajectory source.
                # Do not silently replace a missing matching export with the
                # live database from the currently running experiment.
                records[channel] = []
        self._trajectory_records_cache = records
        return records

    def _legacy_trajectory_analysis(self) -> Any:
        """Load behavior/trajectory data from the legacy trajectory export.

        The old application deliberately kept these sources separate:

        * comparison curves read monitor measurements from the selected
          ``data/monitor_data/<experiment>.xlsx``;
        * behavior and trajectory charts read coordinates from the matching
          ``cage_*/data/trajectory.csv`` files.

        Do not feed the monitor workbook into ``load_experiment_data`` here.
        That helper also accepts coordinate sheets in an Excel workbook and
        can therefore make a monitor workbook look like the trajectory source.
        """
        try:
            if self._trajectory_selection_user_set:
                if not self._selected_trajectory_export_path:
                    return None
                export_path = Path(self._selected_trajectory_export_path)
                # Accept both layouts produced by the old and new trajectory
                # workers: cage_1/... and cage1/....
                paths = sorted(export_path.glob("cage*/data/trajectory.csv"))
                signature = tuple((str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in paths)
                key = ("trajectory-csv-explicit", str(export_path), signature)
                if key != self._legacy_trajectory_cache_key:
                    try:
                        self._legacy_trajectory_cache = (
                            load_trajectory_experiment(export_path) if export_path.is_dir() and paths else None
                        )
                    except (OSError, ValueError):
                        self._legacy_trajectory_cache = None
                    self._legacy_trajectory_cache_key = key
                return self._legacy_trajectory_cache
            if self._selected_experiment_excel_path:
                workbook = Path(self._selected_experiment_excel_path)
                workbook_stat = workbook.stat()
                started = parse_experiment_time(workbook)
                export = find_matching_trajectory_export(TRAJECTORY_EXPORT_DIR, started) if started else None
                paths = sorted(export.glob("cage*/data/trajectory.csv")) if export else []
                signature = tuple((str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in paths)
                key = ("trajectory-csv", str(workbook), workbook_stat.st_size, workbook_stat.st_mtime_ns, str(export or ""), signature)
                if key != self._legacy_trajectory_cache_key:
                    try:
                        # A monitor Excel file is never a behavior/trajectory
                        # source. If its matching CSV export is unavailable,
                        # keep the trajectory side empty until real trajectory
                        # data appears instead of mixing in monitor sheets.
                        self._legacy_trajectory_cache = load_trajectory_experiment(export) if export and paths else None
                    except (OSError, ValueError):
                        self._legacy_trajectory_cache = None
                    self._legacy_trajectory_cache_key = key
                return self._legacy_trajectory_cache
            db_path = _provider_value(self._context_provider).get("experiment_db_path")
            if self._selected_trajectory_export_path:
                export = self._selected_trajectory_export_path
            else:
                if not db_path:
                    return None
                experiment_dir = os.path.basename(os.path.dirname(os.path.dirname(str(db_path))))
                match = re.search(r"20\d{2}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}_\d{3}(?!\d)", experiment_dir)
                if match is None:
                    return None
                started = datetime.strptime(match.group(), "%Y_%m_%d_%H_%M_%S_%f")
                export = find_matching_trajectory_export(TRAJECTORY_EXPORT_DIR, started)
            if export is None:
                return None
            export = os.fspath(export)
            export_path = Path(export)
            paths = sorted(export_path.glob("cage*/data/trajectory.csv"))
            signature = tuple((str(path), path.stat().st_size, path.stat().st_mtime_ns) for path in paths)
            # Refresh the live rolling window periodically even when the CSV
            # writer has not changed its file metadata yet. Historical
            # selections keep the original file-based cache key.
            live_window_key = (
                int(time.time() // 5) if self._live_analysis_mode else None
            )
            key = (str(db_path), str(export_path), signature, live_window_key)
            if key != self._legacy_trajectory_cache_key:
                self._legacy_trajectory_cache = load_trajectory_experiment(export_path, time_range=_live_analysis_time_range() if self._live_analysis_mode else None)
                self._legacy_trajectory_cache_key = key
            return self._legacy_trajectory_cache
        except (OSError, ValueError, csv.Error) as error:
            logger.warning(f"读取旧版轨迹 CSV 失败：{error}")
            return None

    def _legacy_comparison_series(self) -> tuple[float, dict[int, Any]]:
        workbook_path = self._selected_experiment_excel_path
        if not workbook_path:
            return 0.0, {}
        try:
            key = self._workbook_cache_key(workbook_path)
            if key != self._legacy_comparison_cache_key:
                self._start_historical_workbook_load(workbook_path)
                return 0.0, {}
            return self._historical_start_timestamp, self._legacy_comparison_cache
        except OSError as error:
            logger.warning(f"读取实验 Excel 失败：{error}")
            return 0.0, {}

    def _infrared_temperature_records_for_channels(self) -> dict[int, list[tuple[str, float, float]]]:
        """Read the infrared-temperature column from the selected monitor Excel."""
        records: dict[int, list[tuple[str, float, float]]] = {
            channel: [] for channel in sorted(self._trajectory_channels)
        }
        if not self._selected_experiment_excel_path and self._selected_trajectory_export_path:
            self._activate_monitor_workbook(
                self._matching_monitor_workbook_for_trajectory(
                    self._selected_trajectory_export_path
                )
            )
        origin, comparison = self._legacy_comparison_series()
        for channel in records:
            series = comparison.get(channel, {}).get("temperature")
            if series is None:
                continue
            for elapsed, value in zip(series.elapsed_seconds, series.values):
                try:
                    timestamp = float(origin) + float(elapsed)
                    temperature = float(value)
                    if not math.isfinite(timestamp) or not math.isfinite(temperature):
                        continue
                    stamp = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                    records[channel].append((stamp, timestamp, temperature))
                except (TypeError, ValueError, OverflowError):
                    continue
        return records

    def _configured_behavior_categories(self, channel: int) -> tuple[str, ...]:
        """Read the same per-channel behavior selection used by P5.

        P6 is a separate page class, so it cannot inherit the live-view helper;
        keep the lookup here tied to the shared context rather than duplicating
        a hard-coded category list in the chart.
        """
        context = _provider_value(self._context_provider)
        sources = []
        # P2 writes the live selection to global_setting.  Prefer it over a
        # context snapshot because the workflow shell may have been created
        # before the user changed the behaviour chips.
        for setting_key in ("experiment_setting", "experiment_setting_new"):
            setting = global_setting.get_setting(setting_key, None)
            sources.append(getattr(setting, "channel_data_config", None) if setting is not None else None)
        sources.append(context.get("channel_data_config"))
        for data in sources:
            if not isinstance(data, Mapping):
                continue
            info = data.get(str(int(channel)), data.get(int(channel), {}))
            if not isinstance(info, Mapping):
                continue
            selected = MachineVisionPage._normalize_behavior_categories(info.get("behavior_category"))
            if selected:
                return selected
        return ("自由活动",)

    @staticmethod
    def _dwell_cells(points: list[tuple[float, float, float]], columns: int = 12, rows: int = 10) -> list[tuple[int, int, int]]:
        """Aggregate real XY samples into the legacy rounded-cell heatmap."""
        if not points:
            return []
        converted = [TrajectoryCanvas._to_cage_mm(point) for point in points]
        width = max(float(CAGE_WIDTH_MM), 1.0); length = max(float(CAGE_LENGTH_MM), 1.0)
        cells: dict[tuple[int, int], int] = {}
        for x, y, _z in converted:
            column = min(columns - 1, max(0, int(y / length * columns)))
            row = min(rows - 1, max(0, int((x + width / 2.0) / width * rows)))
            cells[(column, row)] = cells.get((column, row), 0) + 1
        return [(column, row, count) for (column, row), count in cells.items()]

    def _height_records_from_export(self, channel: int) -> list[dict[str, Any]]:
        """Read physical XYZ samples for the calculated height comparison.

        The old analysis loader keeps only X/Y.  Height therefore reads the
        selected experiment's CSV directly so the persisted Z values are not
        replaced by a synthetic zero.
        """
        export_path = Path(self._selected_trajectory_export_path or "")
        if not export_path.is_dir():
            return []
        csv_path = next(
            (
                export_path / cage_name / "data" / "trajectory.csv"
                for cage_name in (f"cage_{int(channel)}", f"cage{int(channel)}")
                if (export_path / cage_name / "data" / "trajectory.csv").is_file()
            ),
            None,
        )
        if csv_path is None:
            return []
        records: list[dict[str, Any]] = []
        allowed_statuses = {"ok", "detected", "true", "1", "有效", "已检测", "检测到"}
        try:
            with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
                reader = csv.DictReader(stream)
                for row_index, row in enumerate(reader):
                    status = str(row.get("status") or "").strip().lower()
                    if status and status not in allowed_statuses:
                        continue
                    values = {}
                    for key, names in {
                        "x": ("X", "x", "rawX"),
                        "y": ("Y", "y", "rawY"),
                        "z": ("Z", "z", "mouseHeight", "rawZ"),
                    }.items():
                        values[key] = next(
                            (_number(row.get(name)) for name in names if _number(row.get(name)) is not None),
                            None,
                        )
                    if any(value is None for value in values.values()):
                        continue
                    stamp = next(
                        (
                            parsed
                            for name in ("captureTimestamp", "timestamp", "datetime", "frameName")
                            for parsed in (self._trajectory_time_seconds(row.get(name)),)
                            if parsed is not None
                        ),
                        float(row_index) * 0.1,
                    )
                    records.append({
                        "point": _PhysicalMillimeterPoint(values["x"], values["y"], values["z"]),
                        "time": stamp,
                        "source": "trajectory_csv_xyz",
                    })
        except (OSError, csv.Error):
            return []
        return records

    def _trajectory_payload(self, records_by_channel: dict[int, list[dict[str, Any]]]) -> dict[str, Any]:
        trajectories: list[tuple[str, list[tuple[float, float, float]]]] = []
        xy_series: list[tuple[str, list[tuple[float, float, float]]]] = []
        height_series: list[tuple[str, list[tuple[float, float, float]]]] = []
        cumulative_series: list[tuple[str, list[tuple[float, float]]]] = []
        total_series: list[tuple[str, float]] = []
        minute_series: list[tuple[str, list[tuple[float, float]]]] = []
        sleep_series: list[tuple[str, list[float]]] = []
        dwell_series: list[tuple[str, list[tuple[int, int, int]]]] = []
        for channel, records in records_by_channel.items():
            label = f"CH{channel:02d}"
            points, elapsed, cumulative = self._trajectory_distance_metrics(records)
            trajectories.append((label, points)); xy_series.append((label, points)); height_series.append((label, points))
            cumulative_series.append((label, list(zip([value / 60.0 for value in elapsed], cumulative))))
            total_series.append((label, cumulative[-1] if cumulative else 0.0))
            steps = [0.0] + [cumulative[index] - cumulative[index - 1] for index in range(1, len(cumulative))]
            minute_series.append((label, self._minute_distance(elapsed, steps)))
            sleep_series.append((label, self._sleep_states(records)))
            dwell_series.append((label, self._dwell_cells(points)))
        return {
            "3d": trajectories, "xy": xy_series, "height": height_series,
            "cumulative": cumulative_series, "total": total_series,
            "minute": minute_series, "sleep": sleep_series, "dwell": dwell_series,
        }

    def _legacy_trajectory_image_paths(self, module: str) -> dict[int, str]:
        """Resolve the exact height/occupancy images from a legacy export."""
        export_root = Path(self._selected_trajectory_export_path or "")
        if not export_root.is_dir():
            try:
                analysis = self._legacy_trajectory_analysis()
                source_path = getattr(analysis, "source_path", None)
                if source_path:
                    export_root = Path(source_path)
            except (OSError, ValueError):
                export_root = Path()
        if not export_root.is_dir():
            return {}

        if str(module) == "height":
            total_name = "height_trajectory.png"
            latest_name = "latest_height_trajectory.png"
        else:
            total_name = "occupancy_heatmap.png"
            latest_name = "latest_occupancy_heatmap.png"

        paths: dict[int, str] = {}
        for channel in sorted(self._trajectory_channels):
            cage_dir = next(
                (
                    export_root / name
                    for name in (f"cage_{int(channel)}", f"cage{int(channel)}")
                    if (export_root / name).is_dir()
                ),
                None,
            )
            if cage_dir is None:
                continue
            candidates = (
                cage_dir / "plots" / "total" / total_name,
                cage_dir / "plots" / latest_name,
                cage_dir / "plots" / total_name,
            )
            selected = next((path for path in candidates if path.is_file()), None)
            if selected is not None:
                paths[int(channel)] = str(selected)
        return paths
    def _refresh_trajectory_comparison(self) -> None:
        if not hasattr(self, "trajectory_compare_plots"):
            return
        if self._trajectory_animation_active:
            return
        if self._trajectory_modules == {"infrared"}:
            infrared_records = self._infrared_temperature_records_for_channels()
            self.trajectory_compare_plots["infrared"].set_series([
                (f"CH{channel:02d}", [(stamp, value) for _text, stamp, value in samples])
                for channel, samples in infrared_records.items()
            ])
            self._relayout_trajectory_comparison()
            return
        selected = next(iter(self._trajectory_modules), "3d")
        records_by_channel = self._trajectory_records_for_channels()
        payloads = self._trajectory_payload(records_by_channel)
        state = self._trajectory_animation_states.get(selected)
        if state is not None and 1 < state["index"] and not state["finished"]:
            partial_records = {
                channel: records[: state["index"]]
                for channel, records in records_by_channel.items()
            }
            payloads = self._trajectory_payload(partial_records)
        for key, plot in self.trajectory_compare_plots.items():
            if key == "recognition":
                continue
            plot.set_series(payloads.get(key, []))
        self._relayout_trajectory_comparison()
        self.trajectory_compare_container.adjustSize()

    def _export_trajectory_csv(self) -> None:
        selected = next(iter(self._trajectory_modules), "3d")
        meta = self._analysis_export_meta(selected)
        if selected == "recognition":
            self._refresh_analysis_recognition()
            counts = list(getattr(self.analysis_behavior_chart, "counts", []))
            if not counts or not any(values for _label, values in counts):
                self.compare_export_status.setText("当前没有可导出的真实行为识别数据")
                return
            file_path, _ = QFileDialog.getSaveFileName(self, "保存行为识别数据", f"{meta['filename']}.csv", "CSV 文件 (*.csv)")
            if not file_path:
                return
            try:
                with open(file_path, "w", newline="", encoding="utf-8-sig") as stream:
                    writer = csv.writer(stream)
                    writer.writerow(["通道", "行为", "次数"])
                    for label, values in counts:
                        for behavior, count in values.items():
                            writer.writerow([label, behavior, int(count)])
                self.compare_export_status.setText(f"已导出行为识别数据：{file_path}")
            except (OSError, csv.Error) as error:
                self.compare_export_status.setText(f"行为识别数据导出失败：{error}")
            return
        if selected == "infrared":
            file_path, _ = QFileDialog.getSaveFileName(self, "保存红外温度数据", f"{meta['filename']}.csv", "CSV 文件 (*.csv)")
            if not file_path:
                return
            self._start_csv_export("infrared", file_path, meta["data"])
            return
        file_path, _ = QFileDialog.getSaveFileName(self, f"保存{meta['data']}", f"{meta['filename']}.csv", "CSV 文件 (*.csv)")
        if not file_path:
            return
        self._start_csv_export("trajectory", file_path, meta["data"])

    def _export_trajectory_svg(self) -> None:
        selected = next(iter(self._trajectory_modules), "3d")
        meta = self._analysis_export_meta(selected)
        plot = self.trajectory_compare_plots.get(selected)
        if selected == "recognition":
            self._refresh_analysis_recognition()
            has_data = bool(getattr(self.analysis_behavior_chart, "counts", []))
        else:
            has_data = bool(plot and any(values for _label, values in plot.series))
        if not has_data:
            self.compare_export_status.setText(f"当前没有可导出的真实{meta['title']}")
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self, f"保存{meta['title']} SVG", f"{meta['filename']}.svg", "SVG 图片 (*.svg)"
        )
        if not file_path:
            return
        try:
            if plot is None:
                raise RuntimeError("当前模块没有可导出的图表")
            self._render_chart_svg(plot, file_path, meta["title"])
            self.compare_export_status.setText(f"已导出{meta['title']} SVG：{file_path}")
        except (OSError, RuntimeError) as error:
            self.compare_export_status.setText(f"{meta['title']} SVG 导出失败：{error}")
    def _set_compare_channel(self, target: set[int], buttons: list[QPushButton], channel: int, checked: bool) -> None:
        """Maintain an independent multi-selection for one comparison axis."""
        if checked:
            target.add(int(channel))
        else:
            target.discard(int(channel))
        self._reset_compare_viewport()
        self._refresh_compare_chart()

    def _select_channel(self, channel: int) -> None:
        self.selected_channel = channel
        trajectory_thread = getattr(self, "trajectory_thread", None)
        if trajectory_thread is not None:
            set_preview = getattr(trajectory_thread, "set_preview_cage_number", None)
            if callable(set_preview):
                set_preview(channel)
        for index, button in enumerate(self.channel_buttons, 1): button.setChecked(index == channel)
        self.refresh()

    def _selected_individual_id(self) -> Any:
        context = _provider_value(self._context_provider)
        individuals = context.get("channel_individuals")
        if not isinstance(individuals, Mapping):
            return None
        individual = individuals.get(f"CH{self.selected_channel:02d}")
        return individual.get("id") if isinstance(individual, Mapping) else None

    @staticmethod
    def _latest_measurement_from_mapping(measurements: Mapping[str, Any], *aliases: str) -> Any:
        """Find a directly observed value without deriving a replacement."""
        lowered = {str(key).lower(): value for key, value in measurements.items()}
        for alias in aliases:
            value = lowered.get(str(alias).lower())
            if value is not None:
                return value
        for key, value in measurements.items():
            key_text = str(key).lower()
            if any(str(alias).lower() in key_text for alias in aliases):
                if value is not None:
                    return value
        return None

    def _latest_channel_measurements(self) -> dict[str, Any]:
        """Return exactly the selected cage's last persisted Epoch row."""
        if self._selected_experiment_excel_path:
            return dict(self._historical_latest_rows.get(self.selected_channel, {}))
        measurements: dict[str, Any] = {}
        context = _provider_value(self._context_provider)
        db_path = context.get("experiment_db_path")
        if db_path and os.path.isfile(str(db_path)):
            table = f"Epoch_data_cage_{self.selected_channel}"
            try:
                with closing(sqlite3.connect(str(db_path))) as connection:
                    exists = connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                    ).fetchone()
                    if exists:
                        columns = [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')]
                        if columns:
                            row = connection.execute(
                                f'SELECT * FROM "{table}" ORDER BY rowid DESC LIMIT 1'
                            ).fetchone()
                            if row:
                                measurements.update(zip(columns, row))
            except (OSError, sqlite3.Error):
                pass
        return measurements

    def _channel_bio_metadata(self) -> Mapping[str, Any]:
        context = _provider_value(self._context_provider)
        individuals = context.get("channel_individuals")
        if not isinstance(individuals, Mapping):
            return {}
        value = individuals.get(f"CH{self.selected_channel:02d}")
        return value if isinstance(value, Mapping) else {}

    def _bio_value_text(self, value: Any, unit: str = "") -> str:
        if value is None or value == "":
            return "None"
        numbers = _numbers(value)
        if numbers:
            number = numbers[-1]
            rendered = f"{number:.3f}".rstrip("0").rstrip(".")
            return f"{rendered}{unit}"
        return f"{value}{unit}"

    def _update_bio_card(self, measurements: Mapping[str, Any], points: list[tuple[float, float, float]]) -> None:
        metadata = self._channel_bio_metadata()
        aliases = {
            "weight": ("WM_weight_num", "weight_num", "body_weight", "称重"),
            "food": ("DWM_weight_num", "food_num", "food", "食物重量", "饮食"),
            "water": ("EM_weight_num", "water_num", "water", "饮水重量", "饮水"),
            "movement": ("ENM_running_wheel_num", "running_wheel_num", "运动计数", "跑轮", "运动"),
            "temperature": ("body_temperature", "core_body_temperature", "tmp_hs_max", "红外", "体温"),
            "behavior": ("behavior", "behavior_label", "action", "行为"),
        }
        rer_value = self._latest_measurement_from_mapping(
            measurements, "rer_rq", "RER", "RQ", "respiratory_exchange_ratio"
        )
        values = {
            "weight": self._bio_value_text(self._latest_measurement_from_mapping(measurements, *aliases["weight"]), " g"),
            "food": self._bio_value_text(self._latest_measurement_from_mapping(measurements, *aliases["food"]), " g"),
            "water": self._bio_value_text(self._latest_measurement_from_mapping(measurements, *aliases["water"]), " mL"),
            "rer": self._bio_value_text(rer_value),
            "movement": self._bio_value_text(self._latest_measurement_from_mapping(measurements, *aliases["movement"]), " 计数"),
            "behavior": _text(self._latest_measurement_from_mapping(measurements, *aliases["behavior"]), "None"),
            "temperature": self._bio_value_text(self._latest_measurement_from_mapping(measurements, *aliases["temperature"]), " °C"),
        }
        for key, value in values.items():
            label = self.bio_metric_labels.get(key)
            if label is not None:
                label.setText(value)
        individual_id = _text(metadata.get("id"), "None")
        sex = _text(metadata.get("sex"), "None")
        group = _text(metadata.get("group"), "None")
        self.bio_label.setText(
            f"CH{self.selected_channel:02d}    个体 {individual_id}    性别 {sex}    分组 {group}"
        )

    def _update_bio_status(self) -> None:
        """Render the selected channel's health from the live snapshot only."""
        channel = f"CH{self.selected_channel:02d}"
        states: list[str] = []
        health = _provider_value(self._health_source)
        for item in list(health.get("components") or []) + list(health.get("alerts") or []):
            if not isinstance(item, Mapping):
                continue
            scope = str(item.get("scope", "")).upper()
            if scope not in {channel, str(self.selected_channel)}:
                continue
            states.append(str(item.get("status", item.get("state", "unknown"))).lower())
        if "fault" in states:
            text, tone = "异常", "fault"
        elif "warning" in states:
            text, tone = "告警", "warning"
        elif "unavailable" in states:
            text, tone = "不可用", "warning"
        elif "normal" in states:
            text, tone = "在线", "normal"
        else:
            text, tone = "None", "neutral"
        self._set_chip(self.bio_status_label, text, tone)

    def _load_points(self) -> list[tuple[float, float, float]]:
        # Use the same source as the trajectory comparison and recognition
        # modules. Historical selections use the matching trajectory export;
        # only the live, unselected view uses the current database.
        records = self._trajectory_records_for_channels({self.selected_channel})
        return [item["point"] for item in records.get(self.selected_channel, [])]

    def _metric_values(self, channels: set[int], metric_index: int) -> list[float]:
        return [value for _timestamp, value in self._metric_series(channels, metric_index)]

    def _metric_channel_series(
        self,
        channels: set[int],
        metric_index: int,
        preview_limit: Optional[int] = None,
        time_range: Optional[tuple[float, float]] = None,
    ) -> list[tuple[str, list[tuple[str, float]]]]:
        """Return every persisted value grouped by channel, without a time cap."""
        legacy_keys = ("weight", "food", "temperature", "oxygen", "co2")
        if 0 <= metric_index < len(legacy_keys):
            excel_origin, excel_series = self._legacy_comparison_series()
            if self._selected_experiment_excel_path and excel_series:
                key = legacy_keys[metric_index]
                series_by_channel = []
                for channel in sorted(channels):
                    series = excel_series.get(channel, {}).get(key)
                    if series is not None:
                        values = [
                            (str(excel_origin + float(elapsed)), float(value))
                            for elapsed, value in zip(series.elapsed_seconds, series.values)
                            if math.isfinite(float(value))
                            and (
                                time_range is None
                                or time_range[0] <= excel_origin + float(elapsed) <= time_range[1]
                            )
                        ]
                        if values:
                            if preview_limit is not None:
                                values = extrema_preview(values, preview_limit)
                            series_by_channel.append((f"CH{channel:02d}", values))
                if series_by_channel or self._selected_experiment_excel_path:
                    return series_by_channel
            if self._selected_experiment_excel_path:
                return []
        context = _provider_value(self._context_provider)
        db_path = context.get("experiment_db_path")
        if not db_path or not os.path.isfile(str(db_path)):
            return []
        if metric_index < 0 or metric_index >= len(COMPARE_METRIC_DEFINITIONS):
            return []
        _label, metric_candidates = COMPARE_METRIC_DEFINITIONS[metric_index]
        channel_series: list[tuple[str, list[tuple[str, float]]]] = []
        try:
            with closing(sqlite3.connect(str(db_path))) as connection:
                selected = sorted(channels)
                for channel in selected:
                    table = f"Epoch_data_cage_{channel}"
                    exists = connection.execute(
                        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
                    ).fetchone()
                    if not exists:
                        continue
                    columns = [str(row[1]) for row in connection.execute(f'PRAGMA table_info("{table}")')]
                    lower = {column.lower(): column for column in columns}
                    column = next((lower[name.lower()] for name in metric_candidates if name.lower() in lower), None)
                    if column is None:
                        # RER and EE are derived only when the persisted VO2
                        # and VCO2 fields are both present.  This keeps the UI
                        # formula-backed without fabricating values from raw
                        # concentration percentages.
                        if metric_index not in {8, 9}:
                            continue
                        vo2_column = next((lower[name.lower()] for name in COMPARE_METRIC_DEFINITIONS[6][1] if name.lower() in lower), None)
                        vco2_column = next((lower[name.lower()] for name in COMPARE_METRIC_DEFINITIONS[7][1] if name.lower() in lower), None)
                        if vo2_column is None or vco2_column is None:
                            continue
                        quoted_vo2 = '"' + vo2_column.replace('"', '""') + '"'
                        quoted_vco2 = '"' + vco2_column.replace('"', '""') + '"'
                        query_params: tuple[str, ...] = ()
                        if "time" in lower:
                            quoted_time = '"' + lower["time"].replace('"', '""') + '"'
                            if time_range is not None:
                                cutoff_start = datetime.fromtimestamp(time_range[0]).strftime("%Y-%m-%d %H:%M:%S")
                                cutoff_end = datetime.fromtimestamp(time_range[1]).strftime("%Y-%m-%d %H:%M:%S")
                                query = (
                                    f'SELECT {quoted_time}, {quoted_vo2}, {quoted_vco2} '
                                    f'FROM "{table}" WHERE {quoted_time} >= ? AND {quoted_time} <= ? '
                                    f'ORDER BY {quoted_time} ASC, rowid ASC'
                                )
                                query_params = (cutoff_start, cutoff_end)
                            else:
                                query = f'SELECT {quoted_time}, {quoted_vo2}, {quoted_vco2} FROM "{table}" ORDER BY {quoted_time} ASC, rowid ASC'
                        else:
                            query = f'SELECT rowid, {quoted_vo2}, {quoted_vco2} FROM "{table}" ORDER BY rowid ASC'
                        series: list[tuple[str, float]] = []
                        for row_index, (raw_time, raw_vo2, raw_vco2) in enumerate(connection.execute(query, query_params), 1):
                            interrupted = getattr(self, "_interrupted", None)
                            if row_index % 1000 == 0 and callable(interrupted) and interrupted():
                                return []
                            vo2_values = _numbers(raw_vo2); vco2_values = _numbers(raw_vco2)
                            if time_range is not None:
                                plot_time = _plot_time_value(raw_time if raw_time is not None else row_index)
                                if plot_time is None or not (time_range[0] <= plot_time <= time_range[1]):
                                    continue
                            for vo2, vco2 in zip(vo2_values, vco2_values):
                                if metric_index == 8:
                                    if vo2 == 0:
                                        continue
                                    value = vco2 / vo2
                                else:
                                    value = 60.0 * (3.815 * vo2 + 1.232 * vco2)
                                series.append((str(raw_time if raw_time is not None else row_index), value))
                        if series:
                            if preview_limit is not None:
                                series = extrema_preview(series, preview_limit)
                            channel_series.append((f"CH{channel:02d}", series))
                        continue
                    quoted_column = '"' + column.replace('"', '""') + '"'
                    query_params: tuple[str, ...] = ()
                    if "time" in lower:
                        quoted_time = '"' + lower["time"].replace('"', '""') + '"'
                        if time_range is not None:
                            cutoff_start = datetime.fromtimestamp(time_range[0]).strftime("%Y-%m-%d %H:%M:%S")
                            cutoff_end = datetime.fromtimestamp(time_range[1]).strftime("%Y-%m-%d %H:%M:%S")
                            query = (
                                f'SELECT {quoted_time}, {quoted_column} '
                                f'FROM "{table}" WHERE {quoted_time} >= ? AND {quoted_time} <= ? '
                                f'ORDER BY {quoted_time} ASC, rowid ASC'
                            )
                            query_params = (cutoff_start, cutoff_end)
                        else:
                            query = f'SELECT {quoted_time}, {quoted_column} FROM "{table}" ORDER BY {quoted_time} ASC, rowid ASC'
                    else:
                        query = f'SELECT rowid, {quoted_column} FROM "{table}" ORDER BY rowid ASC'
                    series: list[tuple[str, float]] = []
                    for row_index, (raw_time, raw_value) in enumerate(connection.execute(query, query_params), 1):
                        interrupted = getattr(self, "_interrupted", None)
                        if row_index % 1000 == 0 and callable(interrupted) and interrupted():
                            return []
                        plot_time = _plot_time_value(raw_time if raw_time is not None else row_index)
                        if time_range is not None and (
                            plot_time is None or not (time_range[0] <= plot_time <= time_range[1])
                        ):
                            continue
                        for value in _numbers(raw_value):
                            series.append((str(raw_time if raw_time is not None else row_index), value))
                    if series:
                        if preview_limit is not None:
                            series = extrema_preview(series, preview_limit)
                        channel_series.append((f"CH{channel:02d}", series))
        except (OSError, sqlite3.Error, ValueError):
            return []
        return channel_series

    def _metric_series(self, channels: set[int], metric_index: int) -> list[tuple[str, float]]:
        """Flatten all selected channel series for compatibility/export helpers."""
        return [point for _channel, series in self._metric_channel_series(channels, metric_index) for point in series]

    def _refresh_compare_chart(self) -> None:
        if not hasattr(self, "_analysis_worker"):
            return
        self._schedule_analysis_refresh()

    def _display_compare_chart(self, left, right) -> None:
        self._compare_left_channel_series = left
        self._compare_right_channel_series = right
        self._compare_left_series = [point for _channel, series in left for point in series]
        self._compare_right_series = [point for _channel, series in right for point in series]
        self.compare_canvas.set_axis_labels(self.left_metric.currentText(), self.right_metric.currentText())
        self.compare_canvas.set_timed_channel_series(
            left,
            right,
            full_x_range=getattr(self, "_analysis_full_x_range", None),
            reset_view=self._compare_viewport is None,
            detail_mode=self._compare_viewport is not None,
        )

    def _comparison_export_rows(self) -> tuple[list[tuple[str, list[tuple[str, float]]]], list[tuple[str, list[tuple[str, float]]]], str, str]:
        """Return complete per-channel series and the selected metric labels."""
        left = self._metric_channel_series(self._left_channels, self.left_metric.currentIndex())
        right = self._metric_channel_series(self._right_channels, self.right_metric.currentIndex())
        return left, right, self.left_metric.currentText(), self.right_metric.currentText()

    def _export_compare_csv(self) -> None:
        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存对比数据", "experiment_comparison.csv", "CSV 文件 (*.csv)"
        )
        if not file_path:
            return
        self._start_csv_export("compare", file_path, "对比数据")

    def _start_csv_export(self, kind: str, file_path: str, title: str) -> None:
        if self._csv_export_worker is not None:
            self.compare_export_status.setText("已有导出任务正在进行，请稍候")
            return
        _key, request = self._analysis_request()
        worker = _AnalysisCsvExporter(kind, file_path, title, request)
        self._csv_export_worker = worker
        self.compare_export_status.setText(f"正在导出完整{title}…")
        worker.completed.connect(self.compare_export_status.setText)
        worker.finished.connect(lambda current=worker: self._csv_export_finished(current))
        worker.start()

    def _csv_export_finished(self, worker: "_AnalysisCsvExporter") -> None:
        if self._csv_export_worker is worker:
            self._csv_export_worker = None
        worker.deleteLater()

    def _export_compare_svg(self) -> None:
        left, right = self._compare_left_channel_series, self._compare_right_channel_series
        left_metric, right_metric = self.left_metric.currentText(), self.right_metric.currentText()
        if not left and not right:
            self.compare_export_status.setText("至少需要一个轴存在真实数据后才能导出 SVG")
            return
        file_path, _ = QFileDialog.getSaveFileName(
            self, "保存对比曲线 SVG", "experiment_comparison.svg", "SVG 图片 (*.svg)"
        )
        if not file_path:
            return
        try:
            self._render_chart_svg(self.compare_canvas, file_path, f"{left_metric} vs {right_metric}")
            self.compare_export_status.setText(f"已导出对比曲线 SVG：{file_path}")
        except (OSError, RuntimeError) as error:
            self.compare_export_status.setText(f"对比曲线 SVG 导出失败：{error}")
    def _start_animation(self) -> None:
        self._animation_index = 1; self.history_canvas.set_points(self._history_points[:1]); self._animation_timer.start()

    def _advance_animation(self) -> None:
        if self._trajectory_animation_active:
            selected = next(iter(self._trajectory_modules), "3d")
            state = self._trajectory_animation_states.get(selected)
            if state is None:
                self._trajectory_animation_active = False
                self._animation_timer.stop()
                return
            records_by_channel = self._trajectory_animation_records
            if not records_by_channel:
                records_by_channel = self._trajectory_records_for_channels()
                self._trajectory_animation_records = {
                    channel: list(records)
                    for channel, records in records_by_channel.items()
                }
            if not any(records_by_channel.values()):
                state.update(active=False, index=1, finished=False)
                self._trajectory_animation_active = False
                self._animation_timer.stop()
                self._update_trajectory_play_visibility()
                return
            maximum = max((len(records) for records in records_by_channel.values()), default=0)
            index = max(1, state["index"])
            animated = {channel: records[:index] for channel, records in records_by_channel.items()}
            payloads = self._trajectory_payload(animated)
            plot = self.trajectory_compare_plots.get(selected)
            if plot is not None:
                plot.set_series(payloads.get(selected, []))
            if index >= maximum:
                state.update(active=False, index=maximum, finished=True)
                self._trajectory_animation_active = False
                self._animation_timer.stop()
            else:
                state["index"] = index + 1
            self._trajectory_animation_index = state["index"]
            self._update_trajectory_play_visibility()
            return
        if self._animation_index >= len(self._history_points): self._animation_timer.stop(); return
        self.history_canvas.set_points(self._history_points[: self._animation_index]); self._animation_index += 1

    def _toggle_trajectory_animation(self) -> None:
        state = self._selected_trajectory_animation_state()
        if state is None:
            return
        if state["active"]:
            state["active"] = False
            self._trajectory_animation_active = False
            self._animation_timer.stop()
            self._update_trajectory_play_visibility()
            return
        records_by_channel = self._trajectory_records_for_channels()
        if not any(records_by_channel.values()):
            self.compare_export_status.setText("当前没有可播放的真实轨迹数据")
            return
        # Capture every selected cage when playback starts. The timer then
        # slices one shared snapshot, so all cage cards advance together.
        self._trajectory_animation_records = {
            channel: list(records)
            for channel, records in records_by_channel.items()
        }
        maximum = max((len(records) for records in records_by_channel.values()), default=0)
        if state["finished"] or state["index"] >= maximum:
            state.update(index=1, finished=False)
        state["active"] = True
        self._trajectory_animation_active = True
        self._trajectory_animation_index = state["index"]
        self._update_trajectory_play_visibility()
        self._animation_timer.start()

    def _refresh_analysis_recognition(self) -> None:
        """Count how many selected channels enabled each behaviour type.

        This chart is a configuration summary, not a frame-level trajectory
        classifier: one channel selecting a behaviour contributes exactly one
        count, and another channel selecting the same behaviour contributes
        one more.
        """
        if not hasattr(self, "analysis_recognition_count_label"):
            return
        chart_counts: list[tuple[str, dict[str, int]]] = []
        for selected in sorted(self._trajectory_channels):
            categories = self._configured_behavior_categories(selected)
            values = {category: 1 for category in categories}
            chart_counts.append((f"CH{selected:02d}", values))
        labels = ", ".join(label for label, _values in chart_counts) if chart_counts else "无通道"
        self.analysis_recognition_channel_label.setText(f"当前选择：{labels}")
        categories = []
        for _label, values in chart_counts:
            for category in values:
                if category not in categories:
                    categories.append(category)
        category_text = "、".join(categories) if categories else "无"
        self.analysis_recognition_count_label.setText(f"统计类别：{category_text}；每个通道勾选一次计 1 次")
        self.analysis_behavior_chart.set_counts(chart_counts)

    @staticmethod
    def _analysis_file_signature(path: Any) -> tuple[Any, ...]:
        if not path:
            return ("",)
        try:
            source = Path(path)
            stat = source.stat()
            return (str(source), stat.st_size, stat.st_mtime_ns)
        except OSError:
            return (str(path),)

    def _analysis_request(self) -> tuple[tuple[Any, ...], dict[str, Any]]:
        context = dict(_provider_value(self._context_provider))
        module = next(iter(self._trajectory_modules), "3d")
        export_path = self._selected_trajectory_export_path
        viewport = self._compare_viewport
        full_x_range = (
            getattr(self.compare_canvas, "full_x_range", None)
            if viewport is not None
            else None
        )
        csv_signature = ()
        if export_path:
            root = Path(export_path)
            csv_signature = tuple(
                self._analysis_file_signature(path)
                for channel in sorted(self._trajectory_channels | {self.selected_channel})
                for path in (root / f"cage_{channel}" / "data" / "trajectory.csv",
                             root / f"cage{channel}" / "data" / "trajectory.csv")
                if path.is_file()
            )
        live_window_key = int(time.time() // 5) if self._live_analysis_mode else None
        key = (
            self._analysis_file_signature(context.get("experiment_db_path")),
            self._analysis_file_signature(self._selected_experiment_excel_path),
            csv_signature,
            self._legacy_comparison_cache_key,
            live_window_key,
            module,
            tuple(sorted(self._trajectory_channels)),
            self.selected_channel,
            tuple(sorted(self._left_channels)),
            tuple(sorted(self._right_channels)),
            self.left_metric.currentIndex(),
            self.right_metric.currentIndex(),
            viewport,
            self._configured_behavior_categories(self.selected_channel),
        )
        request = {
            "context": context,
            "live_mode": self._live_analysis_mode,
            "module": module,
            "trajectory_channels": tuple(self._trajectory_channels),
            "bio_channel": self.selected_channel,
            "trajectory_export_path": export_path,
            "trajectory_selection_user_set": self._trajectory_selection_user_set,
            "excel_path": self._selected_experiment_excel_path,
            "comparison_origin": self._historical_start_timestamp,
            "comparison_cache": self._legacy_comparison_cache,
            "left_channels": tuple(self._left_channels),
            "right_channels": tuple(self._right_channels),
            "left_metric": self.left_metric.currentIndex(),
            "right_metric": self.right_metric.currentIndex(),
            "left_metric_label": self.left_metric.currentText(),
            "right_metric_label": self.right_metric.currentText(),
            "compare_full_x_range": full_x_range,
            "compare_viewport": viewport,
            "behavior_categories": self._configured_behavior_categories(self.selected_channel),
        }
        return key, request

    def _schedule_analysis_refresh(self, force: bool = False) -> None:
        if not self.isVisible() or not hasattr(self, "right_metric") or not hasattr(self, "trajectory_compare_plots"):
            return
        key, request = self._analysis_request()
        previous = self._analysis_requested_key
        self._analysis_requested_key = key
        if self._analysis_worker is not None or key == self._analysis_completed_key:
            return
        if not force and previous is not None and key[4:] == previous[4:] and time.monotonic() < self._analysis_next_allowed_at:
            return
        worker = _AnalysisPreviewLoader(key, request)
        self._analysis_worker = worker
        self._analysis_started_at = time.monotonic()
        worker.loaded.connect(self._analysis_preview_loaded)
        worker.failed.connect(self._analysis_preview_failed)
        worker.finished.connect(lambda current=worker: self._analysis_worker_finished(current))
        worker.start()

    def _analysis_worker_finished(self, worker: "_AnalysisPreviewLoader") -> None:
        duration = max(0.0, time.monotonic() - self._analysis_started_at)
        self._analysis_next_allowed_at = time.monotonic() + min(30.0, duration)
        if self._analysis_worker is worker:
            self._analysis_worker = None
        worker.deleteLater()
        if self._refresh_timer.isActive() and self._analysis_requested_key != self._analysis_completed_key:
            self._schedule_analysis_refresh()

    def _analysis_preview_failed(self, key: tuple[Any, ...], error: str) -> None:
        if key != self._analysis_requested_key:
            return
        self._analysis_completed_key = key
        logger.warning("数据分析预览加载失败：{}", error)
        self.compare_export_status.setText(f"数据加载失败：{error}")

    def _analysis_preview_loaded(self, key: tuple[Any, ...], payload: Mapping[str, Any]) -> None:
        requested = self._analysis_requested_key
        if requested is None or (key != requested and key[4:] != requested[4:]):
            return
        self._analysis_completed_key = key
        self._analysis_full_x_range = payload.get("compare_full_x_range")
        left, right = payload["compare"]
        self._display_compare_chart(left, right)
        module = next(iter(self._trajectory_modules), "3d")
        if module != "recognition":
            plot = self.trajectory_compare_plots[module]
            plot._sleep_segment_multiplier = payload["sleep_multiplier"]
            plot.set_series(payload["series"])
            self._relayout_trajectory_comparison()
        points = payload["bio_points"]
        self._history_points = points
        self.heatmap_canvas.set_heatmap(payload["bio_cells"])
        self.history_canvas.set_points(points)
        self.metric_labels["main_behavior"].setText(payload["behavior"])
        activity = payload["activity"]
        self.metric_labels["heat"].setText(f"{activity:.2f}" if activity is not None else UNKNOWN)

    def refresh(self) -> None:
        if self._live_analysis_mode:
            live_record = self._live_experiment_record()
            if live_record is not None and hasattr(self, "experiment_time_combo") and self.experiment_time_combo.currentData() != self.LIVE_EXPERIMENT_KEY:
                self.refresh_experiment_times()
            self._maybe_start_live_snapshot()
        else:
            self._update_live_analysis_status()
        measurements = self._latest_channel_measurements()
        self._update_bio_card(measurements, [])
        self._update_bio_status()
        self._refresh_analysis_recognition()
        self.metric_labels["patterns"].setText(f"{len(self._configured_behavior_categories(self.selected_channel))} 类")
        alerts = []
        for item in _health_items(self._health_source):
            if str(item.get("status", item.get("state", ""))).lower() in {"warning", "fault", "unavailable"}:
                scope = str(item.get("scope", ""))
                if scope.upper().startswith("CH"):
                    alerts.append(scope.upper())
        self.metric_labels["alerts"].setText(", ".join(sorted(set(alerts))) if alerts else "0")
        self._schedule_analysis_refresh()
        self.refreshed.emit()


class _AnalysisWorkContext:
    """Immutable inputs for analysis work that must never touch Qt widgets."""

    _trajectory_records_for_channels = DataAnalysisPage._trajectory_records_for_channels
    _height_records_from_export = DataAnalysisPage._height_records_from_export
    _trajectory_time_seconds = staticmethod(DataAnalysisPage._trajectory_time_seconds)
    _trajectory_distance_metrics = DataAnalysisPage._trajectory_distance_metrics
    _minute_distance = DataAnalysisPage._minute_distance
    _sleep_states = DataAnalysisPage._sleep_states
    _dwell_cells = staticmethod(DataAnalysisPage._dwell_cells)
    _metric_channel_series = DataAnalysisPage._metric_channel_series

    def __init__(self, request: Mapping[str, Any], interrupted: Callable[[], bool]):
        self._request = dict(request)
        self._interrupted = interrupted
        self._context_provider = lambda: self._request["context"]
        self._trajectory_channels = set(request["trajectory_channels"])
        self._requested_channels = self._trajectory_channels | {int(request["bio_channel"])}
        self._trajectory_modules = {request["module"]}
        self._live_analysis_mode = bool(request.get("live_mode", False))
        self._trajectory_selection_user_set = bool(request["trajectory_selection_user_set"])
        self._selected_trajectory_export_path = request["trajectory_export_path"]
        self._selected_experiment_excel_path = request["excel_path"]
        self.selected_channel = int(request["bio_channel"])
        self._trajectory_records_cache: dict[int, list[dict[str, Any]]] = {}
        self._analysis_time_range: tuple[float, float] | None = None

    def _legacy_comparison_series(self) -> tuple[float, dict[int, Any]]:
        return float(self._request["comparison_origin"]), self._request["comparison_cache"]

    def _legacy_trajectory_analysis(self) -> Any:
        export = self._selected_trajectory_export_path
        if self._trajectory_selection_user_set and not export:
            return None
        if not export and self._selected_experiment_excel_path:
            workbook = Path(self._selected_experiment_excel_path)
            started = parse_experiment_time(workbook)
            export = find_matching_trajectory_export(TRAJECTORY_EXPORT_DIR, started) if started else None
        if not export:
            db_path = self._request["context"].get("experiment_db_path")
            if db_path:
                folder = os.path.basename(os.path.dirname(os.path.dirname(str(db_path))))
                match = re.search(r"20\d{2}_\d{2}_\d{2}_\d{2}_\d{2}_\d{2}_\d{3}(?!\d)", folder)
                if match:
                    started = datetime.strptime(match.group(), "%Y_%m_%d_%H_%M_%S_%f")
                    export = find_matching_trajectory_export(TRAJECTORY_EXPORT_DIR, started)
        if not export or self._interrupted():
            return None
        export_path = Path(export)
        if not export_path.is_dir() or not any(export_path.glob("cage*/data/trajectory.csv")):
            return None
        try:
            return load_trajectory_experiment(
                export_path,
                interruption_requested=self._interrupted,
                selected_channels=self._requested_channels,
                include_comparison=False,
                time_range=self._analysis_time_range,
            )
        except (OSError, ValueError, csv.Error, InterruptedError) as error:
            if not self._interrupted():
                logger.warning("读取轨迹 CSV 失败：{}", error)
            return None


class _AnalysisPreviewLoader(QThread):
    """Prepare screen-sized chart data without blocking the Qt event loop."""

    loaded = pyqtSignal(object, object)
    failed = pyqtSignal(object, str)

    def __init__(self, key: tuple[Any, ...], request: Mapping[str, Any]):
        super().__init__()
        self.key = key
        self.request = dict(request)

    def run(self) -> None:
        try:
            context = _AnalysisWorkContext(self.request, self.isInterruptionRequested)
            viewport = self.request.get("compare_viewport")
            requested_full_range = self.request.get("compare_full_x_range")
            time_range = _live_analysis_time_range() if context._live_analysis_mode else None
            if (
                viewport
                and requested_full_range
                and len(requested_full_range) == 2
            ):
                full_low, full_high = map(float, requested_full_range)
                viewport_range = (
                    full_low + (full_high - full_low) * float(viewport[0]),
                    full_low + (full_high - full_low) * float(viewport[1]),
                )
                if time_range is None:
                    time_range = viewport_range
                else:
                    time_range = (
                        max(time_range[0], viewport_range[0]),
                        min(time_range[1], viewport_range[1]),
                    )
                    if time_range[0] > time_range[1]:
                        # The live window moved beyond an old zoom/pan
                        # viewport. Fall back to the current latest hour.
                        time_range = _live_analysis_time_range()
            context._analysis_time_range = time_range
            preview_limit = 20000 if time_range is not None else 3000
            left = context._metric_channel_series(
                set(self.request["left_channels"]), int(self.request["left_metric"]),
                preview_limit, time_range,
            )
            if self.isInterruptionRequested():
                return
            right = context._metric_channel_series(
                set(self.request["right_channels"]), int(self.request["right_metric"]),
                preview_limit, time_range,
            )
            compare = (left, right)
            if self.isInterruptionRequested():
                return
            module = str(self.request["module"])
            channels = set(self.request["trajectory_channels"]) | {int(self.request["bio_channel"])}
            records_by_channel = context._trajectory_records_for_channels(
                {int(self.request["bio_channel"])} if module == "infrared" else channels
            )
            if self.isInterruptionRequested():
                return
            bio_records = records_by_channel.get(int(self.request["bio_channel"]), [])
            bio_points = [item["point"] for item in bio_records]
            bio_preview = extrema_preview(bio_points, 3000, (0, 1, 2))
            cells: dict[tuple[int, int], int] = {}
            if bio_points:
                xs = [point[0] for point in bio_points]
                ys = [point[1] for point in bio_points]
                min_x, max_x = min(xs), max(xs)
                min_y, max_y = min(ys), max(ys)
                span_x, span_y = max(max_x - min_x, 1e-9), max(max_y - min_y, 1e-9)
                for x, y, _z in bio_points:
                    cell = (int((x - min_x) / span_x * 9), int((y - min_y) / span_y * 9))
                    cells[cell] = cells.get(cell, 0) + 1
            behavior = MachineVisionPage._classify(
                bio_records[-4:], self.request["behavior_categories"]
            )
            activity = sum(math.dist(a, b) for a, b in zip(bio_points, bio_points[1:]))
            series: list[tuple[str, Any]] = []
            sleep_multiplier = 1
            if module == "infrared":
                if self._live_analysis_mode:
                    live_temperature = context._metric_channel_series(
                        set(self.request["trajectory_channels"]),
                        2,
                        3000,
                        time_range,
                    )
                    series.extend(live_temperature)
                else:
                    origin = float(self.request["comparison_origin"])
                    comparison = self.request["comparison_cache"]
                    for channel in sorted(self.request["trajectory_channels"]):
                        temperature = comparison.get(channel, {}).get("temperature")
                        if temperature is None:
                            continue
                        values = [
                            (origin + float(elapsed), float(value))
                            for elapsed, value in zip(temperature.elapsed_seconds, temperature.values)
                            if math.isfinite(float(elapsed)) and math.isfinite(float(value))
                        ]
                        series.append((f"CH{channel:02d}", extrema_preview(values, 3000)))
            elif module != "recognition":
                for channel in sorted(self.request["trajectory_channels"]):
                    records = records_by_channel.get(channel, [])
                    label = f"CH{channel:02d}"
                    if not records:
                        series.append((label, []))
                        continue
                    points, elapsed, cumulative = context._trajectory_distance_metrics(records)
                    if module in {"3d", "xy", "height"}:
                        values = extrema_preview(points, 3000, (0, 1, 2))
                    elif module == "cumulative":
                        values = extrema_preview(
                            list(zip((time_value / 60.0 for time_value in elapsed), cumulative)), 3000
                        )
                    elif module == "total":
                        values = cumulative[-1] if cumulative else 0.0
                    elif module == "minute":
                        steps = [0.0] + [
                            cumulative[index] - cumulative[index - 1]
                            for index in range(1, len(cumulative))
                        ]
                        values = extrema_preview(context._minute_distance(elapsed, steps), 3000)
                    elif module == "sleep":
                        values = sleep_segments(points, elapsed, 10.0)
                    elif module == "dwell":
                        values = context._dwell_cells(points)
                    else:
                        values = []
                    series.append((label, values))
                    if self.isInterruptionRequested():
                        return
                if module == "sleep":
                    longest = max((len(values) for _label, values in series), default=0)
                    sleep_multiplier = max(1, math.ceil(longest / 1800))
                    series = [
                        (label, sleep_preview(values, 1800, sleep_multiplier)[0])
                        for label, values in series
                    ]
            current_points = [
                point
                for _label, series in left + right
                for point in series
                if _plot_time_value(point[0]) is not None
            ]
            current_x_range = (
                min(_plot_time_value(point[0]) for point in current_points),
                max(_plot_time_value(point[0]) for point in current_points),
            ) if current_points else (0.0, 1.0)
            compare_full_x_range = (
                current_x_range if context._live_analysis_mode
                else (requested_full_range or current_x_range)
            )
            self.loaded.emit(self.key, {
                "compare": compare,
                "compare_full_x_range": compare_full_x_range,
                "series": series,
                "sleep_multiplier": sleep_multiplier,
                "bio_points": bio_preview,
                "bio_cells": [(x, y, count) for (x, y), count in cells.items()],
                "behavior": behavior,
                "activity": activity if len(bio_points) > 1 else None,
            })
        except Exception as error:
            if not self.isInterruptionRequested():
                self.failed.emit(self.key, str(error))


class _AnalysisCsvExporter(QThread):
    """Write complete analysis data in a worker and publish only a finished CSV."""

    completed = pyqtSignal(str)

    def __init__(self, kind: str, file_path: str, title: str, request: Mapping[str, Any]):
        super().__init__()
        self.kind = kind
        self.file_path = file_path
        self.title = title
        self.request = dict(request)

    def run(self) -> None:
        temporary_path = ""
        try:
            context = _AnalysisWorkContext(self.request, self.isInterruptionRequested)
            channels = set(self.request["trajectory_channels"])
            context._requested_channels = channels
            destination = Path(self.file_path)
            with tempfile.NamedTemporaryFile(
                mode="w", newline="", encoding="utf-8-sig",
                prefix=".analysis_export_", suffix=".csv",
                dir=destination.parent, delete=False,
            ) as stream:
                temporary_path = stream.name
                writer = csv.writer(stream)
                if self.kind == "compare":
                    rows = self._write_comparison(context, writer)
                elif self.kind == "infrared":
                    rows = self._write_infrared(writer, channels)
                else:
                    rows = self._write_trajectory(context, writer, channels)
            if self.isInterruptionRequested():
                return
            if rows == 0:
                self.completed.emit(f"当前没有可导出的真实{self.title}")
                return
            os.replace(temporary_path, destination)
            temporary_path = ""
            self.completed.emit(f"已导出全部{self.title}：{destination}")
        except (OSError, ValueError, csv.Error, sqlite3.Error) as error:
            if not self.isInterruptionRequested():
                self.completed.emit(f"{self.title}导出失败：{error}")
        finally:
            if temporary_path:
                try:
                    os.unlink(temporary_path)
                except OSError:
                    pass

    def _write_comparison(self, context: _AnalysisWorkContext, writer: Any) -> int:
        left = context._metric_channel_series(
            set(self.request["left_channels"]), int(self.request["left_metric"])
        )
        if self.isInterruptionRequested():
            return 0
        right = context._metric_channel_series(
            set(self.request["right_channels"]), int(self.request["right_metric"])
        )
        left_rows = chain.from_iterable(
            ((channel, timestamp, value) for timestamp, value in series)
            for channel, series in left
        )
        right_rows = chain.from_iterable(
            ((channel, timestamp, value) for timestamp, value in series)
            for channel, series in right
        )
        writer.writerow(["序号", "左轴通道", "左轴指标", "左轴时间", "左轴数值",
                         "右轴通道", "右轴指标", "右轴时间", "右轴数值"])
        count = 0
        for count, (left_item, right_item) in enumerate(
            zip_longest(left_rows, right_rows, fillvalue=("", "", "")), 1
        ):
            if count % 1000 == 0 and self.isInterruptionRequested():
                return 0
            writer.writerow([
                count, left_item[0], self.request["left_metric_label"],
                left_item[1], left_item[2], right_item[0],
                self.request["right_metric_label"], right_item[1], right_item[2],
            ])
        return count

    def _write_infrared(self, writer: Any, channels: set[int]) -> int:
        writer.writerow(["通道", "采集时间", "红外最高温度(°C)"])
        origin = float(self.request["comparison_origin"])
        comparison = self.request["comparison_cache"]
        count = 0
        for channel in sorted(channels):
            series = comparison.get(channel, {}).get("temperature")
            if series is None:
                continue
            for elapsed, raw_value in zip(series.elapsed_seconds, series.values):
                try:
                    timestamp = origin + float(elapsed)
                    value = float(raw_value)
                    if not math.isfinite(timestamp) or not math.isfinite(value):
                        continue
                    stamp = datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
                except (TypeError, ValueError, OverflowError):
                    continue
                count += 1
                if count % 1000 == 0 and self.isInterruptionRequested():
                    return 0
                writer.writerow([f"CH{channel:02d}", stamp, value])
        return count

    def _write_trajectory(
        self, context: _AnalysisWorkContext, writer: Any, channels: set[int]
    ) -> int:
        writer.writerow(["通道", "时间", "X(mm)", "Y(mm)", "Z(mm)",
                         "累计二维距离(mm)", "每分钟运动量(mm)", "蛰伏状态"])
        records_by_channel = context._trajectory_records_for_channels(channels)
        count = 0
        for channel, records in records_by_channel.items():
            if self.isInterruptionRequested():
                return 0
            points, elapsed, cumulative = context._trajectory_distance_metrics(records)
            steps = [0.0] + [
                cumulative[index] - cumulative[index - 1]
                for index in range(1, len(cumulative))
            ]
            minute_map = dict(context._minute_distance(elapsed, steps))
            states = context._sleep_states(records)
            for index, (record, point) in enumerate(zip(records, points)):
                count += 1
                if count % 1000 == 0 and self.isInterruptionRequested():
                    return 0
                bucket = int(elapsed[index] // 60.0)
                state_index = int(elapsed[index] // 10.0)
                state = states[state_index] if state_index < len(states) else None
                xyz = [f"{value:.6f}" for value in point]
                if record.get("source") == "legacy_trajectory_csv":
                    xyz[2] = ""
                writer.writerow([
                    f"CH{channel:02d}", record.get("time", ""), *xyz,
                    f"{cumulative[index]:.6f}", f"{minute_map.get(bucket, 0.0):.6f}",
                    "蛰伏" if state == 1.0 else ("自由活动" if state == 0.0 else UNKNOWN),
                ])
        return count
