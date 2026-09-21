from __future__ import annotations

import json
import math
import threading
from datetime import datetime
from pathlib import Path
from statistics import median
from typing import Any, Callable

try:
    from PyQt6.QtCore import Qt, QTimer, pyqtSignal
    from PyQt6.QtWidgets import (
        QCheckBox,
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox,
        QFormLayout,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QSpinBox,
        QTabWidget,
        QVBoxLayout,
        QWidget,
    )
    PYQT_AVAILABLE = True
except ModuleNotFoundError:  # Pure algorithm tests do not require the GUI runtime.
    PYQT_AVAILABLE = False

    class _UnavailableSignal:
        def emit(self, *_args: Any, **_kwargs: Any) -> None:
            return None

    class _UnavailableWidget:
        pass

    def pyqtSignal(*_args: Any, **_kwargs: Any) -> _UnavailableSignal:
        return _UnavailableSignal()

    Qt = QTimer = QCheckBox = QDialogButtonBox = QDoubleSpinBox = _UnavailableWidget
    QFormLayout = QHBoxLayout = QLabel = QPushButton = QSpinBox = _UnavailableWidget
    QTabWidget = QVBoxLayout = QWidget = _UnavailableWidget

    class QDialog(_UnavailableWidget):
        pass


PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TUNING_PATH = PROJECT_ROOT / "config" / "mouse_trajectory_tuning.json"

RECOMMENDED_SETTINGS: dict[str, float | int | bool] = {
    "detection_confidence": 0.50,
    "bbox_weight": 0.60,
    "volume_weight": 0.25,
    "bottom_near_weight": 0.15,
    "bottom_far_weight": 0.05,
    "volume_reject_delta_mm": 120.0,
    "bottom_reject_delta_mm": 80.0,
    "bbox_near_px": 560.0,
    "bbox_far_px": 125.0,
    "stabilization_enabled": True,
    "median_window": 5,
    "stationary_speed_mm_s": 120.0,
    "stationary_ema": 0.20,
    "moving_ema": 0.50,
    "max_xy_speed_mm_s": 700.0,
    "jump_confirmation_frames": 2,
    "height_ema": 0.30,
    "max_z_speed_mm_s": 500.0,
    "plot_refresh_seconds": 3.0,
}

INTEGER_FIELDS = {"median_window", "jump_confirmation_frames"}
BOOL_FIELDS = {"stabilization_enabled"}
FIELD_LIMITS: dict[str, tuple[float, float]] = {
    "detection_confidence": (0.01, 1.0),
    "bbox_weight": (0.0, 5.0),
    "volume_weight": (0.0, 5.0),
    "bottom_near_weight": (0.0, 5.0),
    "bottom_far_weight": (0.0, 5.0),
    "volume_reject_delta_mm": (1.0, 360.0),
    "bottom_reject_delta_mm": (1.0, 360.0),
    "bbox_near_px": (1.0, 4000.0),
    "bbox_far_px": (1.0, 4000.0),
    "median_window": (1.0, 31.0),
    "stationary_speed_mm_s": (0.0, 5000.0),
    "stationary_ema": (0.01, 1.0),
    "moving_ema": (0.01, 1.0),
    "max_xy_speed_mm_s": (1.0, 10000.0),
    "jump_confirmation_frames": (1.0, 10.0),
    "height_ema": (0.01, 1.0),
    "max_z_speed_mm_s": (1.0, 10000.0),
    "plot_refresh_seconds": (0.5, 60.0),
}


def normalize_settings(values: dict[str, Any] | None) -> dict[str, float | int | bool]:
    values = values or {}
    normalized: dict[str, float | int | bool] = {}
    for key, default in RECOMMENDED_SETTINGS.items():
        if key in BOOL_FIELDS:
            raw_value = values.get(key, default)
            if isinstance(raw_value, bool):
                normalized[key] = raw_value
            else:
                normalized[key] = str(raw_value).strip().lower() in {
                    "1",
                    "true",
                    "yes",
                    "on",
                    "enabled",
                    "启用",
                }
            continue
        try:
            value = float(values.get(key, default))
        except (TypeError, ValueError):
            value = float(default)
        lower, upper = FIELD_LIMITS[key]
        value = max(lower, min(upper, value))
        if key in INTEGER_FIELDS:
            normalized[key] = int(round(value))
        else:
            normalized[key] = float(value)

    # Median filters behave predictably with an odd-sized window.
    if int(normalized["median_window"]) % 2 == 0:
        normalized["median_window"] = min(int(normalized["median_window"]) + 1, 31)
    if float(normalized["bbox_near_px"]) <= float(normalized["bbox_far_px"]):
        normalized["bbox_near_px"] = float(normalized["bbox_far_px"]) + 1.0
    normalized["bottom_far_weight"] = min(
        float(normalized["bottom_far_weight"]),
        float(normalized["bottom_near_weight"]),
    )
    return normalized


class TrajectoryTuningStore:
    def __init__(self, path: Path = DEFAULT_TUNING_PATH) -> None:
        self.path = Path(path)
        self._lock = threading.RLock()
        self._settings = normalize_settings(None)
        self._revision = 0
        self._saved_version = "recommended"
        self.reload()

    @property
    def has_saved_file(self) -> bool:
        return self.path.exists()

    def reload(self) -> dict[str, Any]:
        with self._lock:
            if self.path.exists():
                try:
                    payload = json.loads(self.path.read_text(encoding="utf-8"))
                    self._settings = normalize_settings(payload)
                    self._saved_version = str(payload.get("parameter_version") or "saved")
                except (OSError, ValueError, TypeError):
                    self._settings = normalize_settings(None)
                    self._saved_version = "recommended"
            return self.snapshot()

    def apply_ini_defaults(self, values: dict[str, Any] | None) -> dict[str, Any]:
        with self._lock:
            if not self.path.exists():
                self._settings = normalize_settings(values)
                self._revision += 1
            return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            result = dict(self._settings)
            result["parameter_version"] = f"{self._saved_version}.r{self._revision}"
            return result

    def update(self, values: dict[str, Any], *, save: bool = False) -> dict[str, Any]:
        with self._lock:
            merged = dict(self._settings)
            merged.update(values)
            self._settings = normalize_settings(merged)
            self._revision += 1
            if save:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self._saved_version = datetime.now().strftime("%Y%m%d_%H%M%S")
                payload = dict(self._settings)
                payload["parameter_version"] = self._saved_version
                payload["saved_at"] = datetime.now().isoformat(timespec="seconds")
                temporary = self.path.with_suffix(self.path.suffix + ".tmp")
                temporary.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                temporary.replace(self.path)
            return self.snapshot()

    def restore_recommended(self, *, save: bool = False) -> dict[str, Any]:
        return self.update(dict(RECOMMENDED_SETTINGS), save=save)


trajectory_tuning_store = TrajectoryTuningStore()


def fuse_y_sources(
    bbox_y: float | None,
    volume_y: float | None,
    bottom_y: float | None,
    settings: dict[str, Any] | None = None,
    *,
    cage_length_mm: float = 360.0,
) -> tuple[float | None, dict[str, Any]]:
    cfg = normalize_settings(settings)
    source_values = {
        "bbox": None if bbox_y is None else float(bbox_y),
        "volume": None if volume_y is None else float(volume_y),
        "bottom": None if bottom_y is None else float(bottom_y),
    }
    raw_weights = {
        "bbox": float(cfg["bbox_weight"]) if bbox_y is not None else 0.0,
        "volume": float(cfg["volume_weight"]) if volume_y is not None else 0.0,
        "bottom": 0.0,
    }
    rejected: dict[str, str] = {}
    reference_y = bbox_y if bbox_y is not None else volume_y

    if bbox_y is not None and volume_y is not None:
        delta = abs(float(volume_y) - float(bbox_y))
        limit = float(cfg["volume_reject_delta_mm"])
        if delta > limit:
            raw_weights["volume"] *= max(0.10, limit / max(delta, 1.0) * 0.35)
            rejected["volume"] = f"与框尺寸相差 {delta:.1f} mm，已降权"

    if bottom_y is not None:
        distance_reference = float(reference_y if reference_y is not None else bottom_y)
        far_ratio = max(0.0, min(1.0, distance_reference / max(cage_length_mm, 1.0)))
        bottom_weight = (
            float(cfg["bottom_near_weight"]) * (1.0 - far_ratio)
            + float(cfg["bottom_far_weight"]) * far_ratio
        )
        if reference_y is not None:
            delta = abs(float(bottom_y) - float(reference_y))
            limit = float(cfg["bottom_reject_delta_mm"])
            if delta > limit:
                bottom_weight = 0.0
                rejected["bottom"] = f"与主来源相差 {delta:.1f} mm，已拒绝"
            else:
                bottom_weight *= max(0.15, 1.0 - delta / max(limit, 1.0))
        raw_weights["bottom"] = bottom_weight

    weighted = [
        (float(source_values[key]), max(float(raw_weights[key]), 0.0))
        for key in ("bbox", "volume", "bottom")
        if source_values[key] is not None and float(raw_weights[key]) > 0.0
    ]
    if not weighted:
        fallback = next((value for value in source_values.values() if value is not None), None)
        return fallback, {
            "sources": source_values,
            "rawWeights": raw_weights,
            "effectiveWeights": {key: 0.0 for key in raw_weights},
            "rejected": rejected,
        }

    total_weight = sum(weight for _value, weight in weighted)
    effective = {
        key: (float(raw_weights[key]) / total_weight if total_weight > 0.0 else 0.0)
        for key in raw_weights
    }
    fused = sum(value * weight for value, weight in weighted) / total_weight
    return max(0.0, min(float(cage_length_mm), fused)), {
        "sources": source_values,
        "rawWeights": raw_weights,
        "effectiveWeights": effective,
        "rejected": rejected,
    }


class TrajectoryStabilizer:
    def __init__(self) -> None:
        self._history: list[tuple[float, float, float]] = []
        self._stable: tuple[float, float, float] | None = None
        self._last_timestamp: float | None = None
        self._pending_jump_count = 0

    def reset(self) -> None:
        self._history.clear()
        self._stable = None
        self._last_timestamp = None
        self._pending_jump_count = 0

    def update(
        self,
        x: float,
        y: float,
        z: float,
        timestamp: float,
        settings: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        cfg = normalize_settings(settings)
        raw = (float(x), float(y), float(z))

        if not bool(cfg["stabilization_enabled"]):
            self.reset()
            version = str((settings or {}).get("parameter_version", "recommended"))
            return self._result(raw, raw, 0.0, 0.0, False, "disabled", 1.0, version)

        self._history.append(raw)
        window = int(cfg["median_window"])
        if len(self._history) > window:
            del self._history[:-window]
        median_point = tuple(median(axis) for axis in zip(*self._history))

        version = str((settings or {}).get("parameter_version", "recommended"))
        if self._stable is None:
            self._stable = median_point
            self._last_timestamp = float(timestamp)
            return self._result(raw, median_point, 0.0, 0.0, False, "initial", 1.0, version)

        previous_timestamp = (
            float(self._last_timestamp)
            if self._last_timestamp is not None
            else float(timestamp)
        )
        dt = max(float(timestamp) - previous_timestamp, 1e-3)
        dx = median_point[0] - self._stable[0]
        dy = median_point[1] - self._stable[1]
        dz = median_point[2] - self._stable[2]
        planar_speed = math.hypot(dx, dy) / dt
        height_speed = abs(dz) / dt
        is_jump = (
            planar_speed > float(cfg["max_xy_speed_mm_s"])
            or height_speed > float(cfg["max_z_speed_mm_s"])
        )
        confirmation_frames = int(cfg["jump_confirmation_frames"])
        jump_rejected = False
        if is_jump and confirmation_frames > 1:
            self._pending_jump_count += 1
            if self._pending_jump_count < confirmation_frames:
                jump_rejected = True
                self._last_timestamp = float(timestamp)
                return self._result(
                    raw,
                    median_point,
                    planar_speed,
                    height_speed,
                    True,
                    "jump_pending",
                    0.0,
                    version,
                )
        else:
            self._pending_jump_count = 0

        motion_state = (
            "stationary"
            if planar_speed <= float(cfg["stationary_speed_mm_s"])
            else "moving"
        )
        xy_alpha = float(
            cfg["stationary_ema"] if motion_state == "stationary" else cfg["moving_ema"]
        )
        z_alpha = float(cfg["height_ema"])
        self._stable = (
            self._stable[0] + xy_alpha * dx,
            self._stable[1] + xy_alpha * dy,
            self._stable[2] + z_alpha * dz,
        )
        self._last_timestamp = float(timestamp)
        self._pending_jump_count = 0
        return self._result(
            raw,
            median_point,
            planar_speed,
            height_speed,
            jump_rejected,
            motion_state,
            xy_alpha,
            version,
        )

    def _result(
        self,
        raw: tuple[float, float, float],
        median_point: tuple[float, float, float],
        planar_speed: float,
        height_speed: float,
        jump_rejected: bool,
        motion_state: str,
        alpha: float,
        version: str,
    ) -> dict[str, Any]:
        stable = self._stable or median_point
        return {
            "rawX": raw[0],
            "rawY": raw[1],
            "rawZ": raw[2],
            "medianX": median_point[0],
            "medianY": median_point[1],
            "medianZ": median_point[2],
            "stableX": stable[0],
            "stableY": stable[1],
            "stableZ": stable[2],
            "planarSpeedMmS": planar_speed,
            "heightSpeedMmS": height_speed,
            "jumpRejected": jump_rejected,
            "motionState": motion_state,
            "stabilizationAlpha": alpha,
            "parameterVersion": version,
        }


class TrajectoryTuningDialog(QDialog):
    settings_applied = pyqtSignal(dict)

    def __init__(
        self,
        parent: QWidget | None = None,
        store: TrajectoryTuningStore = trajectory_tuning_store,
    ) -> None:
        if not PYQT_AVAILABLE:
            raise RuntimeError("PyQt6 is required to open trajectory tuning dialog")
        super().__init__(parent)
        self.store = store
        self.controls: dict[str, QCheckBox | QDoubleSpinBox | QSpinBox] = {}
        self.diagnostic_labels: dict[str, QLabel] = {}
        self._loading = False
        self.setWindowTitle("轨迹实时调参")
        self.setMinimumSize(700, 610)
        self._live_timer = QTimer(self)
        self._live_timer.setSingleShot(True)
        self._live_timer.setInterval(120)
        self._live_timer.timeout.connect(self._apply_live)
        self._build_ui()
        self._load_values(self.store.snapshot())

    def _build_ui(self) -> None:
        root = QVBoxLayout(self)
        summary = QLabel(
            "Y由框尺寸、框中心体网格KNN、框底部地面网格三路融合。"
            "框尺寸保持主来源，底部网格权重随距离连续下降。"
        )
        summary.setWordWrap(True)
        root.addWidget(summary)

        tabs = QTabWidget(self)
        root.addWidget(tabs, 1)
        tabs.addTab(self._build_form_tab([
            ("检测置信度", "detection_confidence", ""),
            ("框尺寸深度权重", "bbox_weight", ""),
            ("框中心体网格权重", "volume_weight", ""),
            ("底部网格近端权重", "bottom_near_weight", ""),
            ("底部网格远端权重", "bottom_far_weight", ""),
            ("框中心分歧阈值", "volume_reject_delta_mm", " mm"),
            ("底部网格接纳阈值", "bottom_reject_delta_mm", " mm"),
            ("近端框尺度", "bbox_near_px", " px"),
            ("远端框尺度", "bbox_far_px", " px"),
        ]), "融合")
        timing_tab = QWidget(self)
        timing_layout = QVBoxLayout(timing_tab)
        enable_layout = QHBoxLayout()
        enable_layout.addWidget(QLabel("时序稳定", timing_tab))
        self.stabilization_enabled = QCheckBox("启用", timing_tab)
        self.stabilization_enabled.stateChanged.connect(self._schedule_live_apply)
        self.controls["stabilization_enabled"] = self.stabilization_enabled
        enable_layout.addWidget(self.stabilization_enabled)
        enable_layout.addStretch(1)
        timing_layout.addLayout(enable_layout)
        timing_layout.addWidget(self._build_form_tab([
            ("中值窗口", "median_window", ""),
            ("运动判定速度", "stationary_speed_mm_s", " mm/s"),
            ("静止 EMA 系数", "stationary_ema", ""),
            ("运动 EMA 系数", "moving_ema", ""),
            ("平面最大速度", "max_xy_speed_mm_s", " mm/s"),
            ("突跳确认帧数", "jump_confirmation_frames", ""),
            ("高度 EMA 系数", "height_ema", ""),
            ("高度最大速度", "max_z_speed_mm_s", " mm/s"),
            ("轨迹刷新间隔", "plot_refresh_seconds", " s"),
        ]))
        timing_layout.addStretch(1)
        tabs.addTab(timing_tab, "时序与显示")
        tabs.addTab(self._build_diagnostics_tab(), "实时诊断")

        self.live_apply = QCheckBox("参数变化后立即应用", self)
        self.live_apply.setChecked(True)
        root.addWidget(self.live_apply)

        buttons = QHBoxLayout()
        restore_button = QPushButton("恢复推荐值", self)
        restore_button.clicked.connect(self._restore_recommended)
        buttons.addWidget(restore_button)
        buttons.addStretch(1)
        apply_button = QPushButton("立即应用", self)
        apply_button.clicked.connect(lambda: self._apply(False))
        buttons.addWidget(apply_button)
        save_button = QPushButton("应用并保存默认", self)
        save_button.clicked.connect(lambda: self._apply(True))
        buttons.addWidget(save_button)
        close_buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close, self)
        close_buttons.rejected.connect(self.close)
        buttons.addWidget(close_buttons)
        root.addLayout(buttons)

    def _build_form_tab(self, fields: list[tuple[str, str, str]]) -> QWidget:
        widget = QWidget(self)
        form = QFormLayout(widget)
        for label, key, suffix in fields:
            lower, upper = FIELD_LIMITS[key]
            if key in INTEGER_FIELDS:
                control: QDoubleSpinBox | QSpinBox = QSpinBox(widget)
                control.setRange(int(lower), int(upper))
            else:
                control = QDoubleSpinBox(widget)
                control.setRange(lower, upper)
                control.setDecimals(3 if upper <= 5 else 1)
                control.setSingleStep(0.05 if upper <= 5 else 5.0)
            control.setSuffix(suffix)
            control.valueChanged.connect(self._schedule_live_apply)
            self.controls[key] = control
            form.addRow(label, control)
        return widget

    def _build_diagnostics_tab(self) -> QWidget:
        widget = QWidget(self)
        form = QFormLayout(widget)
        labels = [
            ("当前笼", "cage"),
            ("三路 Y", "y_sources"),
            ("有效归一化权重", "weights"),
            ("原始 XYZ", "raw_xyz"),
            ("中值 XYZ", "median_xyz"),
            ("稳定 XYZ", "stable_xyz"),
            ("检测框宽高", "bbox"),
            ("检测置信度", "confidence"),
            ("平面/高度速度", "speed"),
            ("突跳状态", "jump"),
            ("参数版本", "version"),
        ]
        for title, key in labels:
            value = QLabel("--", widget)
            value.setTextInteractionFlags(
                value.textInteractionFlags()
                | Qt.TextInteractionFlag.TextSelectableByMouse
            )
            value.setWordWrap(True)
            self.diagnostic_labels[key] = value
            form.addRow(title, value)
        return widget

    def _collect_values(self) -> dict[str, Any]:
        return {
            key: control.isChecked() if isinstance(control, QCheckBox) else control.value()
            for key, control in self.controls.items()
        }

    def _load_values(self, settings: dict[str, Any]) -> None:
        self._loading = True
        try:
            for key, control in self.controls.items():
                if isinstance(control, QCheckBox):
                    control.setChecked(bool(settings.get(key, RECOMMENDED_SETTINGS[key])))
                else:
                    control.setValue(settings.get(key, RECOMMENDED_SETTINGS[key]))
        finally:
            self._loading = False

    def _schedule_live_apply(self) -> None:
        if not self._loading and self.live_apply.isChecked():
            self._live_timer.start()

    def _apply_live(self) -> None:
        if self.live_apply.isChecked():
            self._apply(False)

    def _apply(self, save: bool) -> None:
        settings = self.store.update(self._collect_values(), save=save)
        self._load_values(settings)
        self.settings_applied.emit(settings)

    def _restore_recommended(self) -> None:
        self._load_values(RECOMMENDED_SETTINGS)
        if self.live_apply.isChecked():
            self._apply(False)

    @staticmethod
    def _fmt_xyz(diagnostics: dict[str, Any], prefix: str) -> str:
        values = [diagnostics.get(f"{prefix}{axis}") for axis in ("X", "Y", "Z")]
        if any(value is None for value in values):
            return "--"
        return ", ".join(f"{float(value):.1f}" for value in values) + " mm"

    def set_diagnostics(self, cage_number: int | None, diagnostics: dict[str, Any] | None) -> None:
        data = diagnostics or {}
        self.diagnostic_labels["cage"].setText(f"鼠笼{cage_number}" if cage_number else "--")
        self.diagnostic_labels["y_sources"].setText(
            "框 {:.1f} / 体网格 {:.1f} / 底网格 {:.1f} mm".format(
                float(data.get("bboxSizeY") or 0.0),
                float(data.get("volumeKnnY") or 0.0),
                float(data.get("bottomGridY") or 0.0),
            )
        )
        self.diagnostic_labels["weights"].setText(
            "框 {:.2f} / 体网格 {:.2f} / 底网格 {:.2f}".format(
                float(data.get("bboxEffectiveWeight") or 0.0),
                float(data.get("volumeEffectiveWeight") or 0.0),
                float(data.get("bottomEffectiveWeight") or 0.0),
            )
        )
        self.diagnostic_labels["raw_xyz"].setText(self._fmt_xyz(data, "raw"))
        self.diagnostic_labels["median_xyz"].setText(self._fmt_xyz(data, "median"))
        self.diagnostic_labels["stable_xyz"].setText(self._fmt_xyz(data, "stable"))
        self.diagnostic_labels["bbox"].setText(
            f"{float(data.get('mouseBoxWidth') or 0.0):.1f} x "
            f"{float(data.get('mouseBoxHeight') or 0.0):.1f} px"
        )
        self.diagnostic_labels["confidence"].setText(
            f"{float(data.get('mouseConf') or 0.0):.3f}"
        )
        self.diagnostic_labels["speed"].setText(
            f"{float(data.get('planarSpeedMmS') or 0.0):.1f} / "
            f"{float(data.get('heightSpeedMmS') or 0.0):.1f} mm/s"
        )
        self.diagnostic_labels["jump"].setText(
            "已拒绝，等待连续确认" if data.get("jumpRejected") else str(data.get("motionState") or "正常")
        )
        self.diagnostic_labels["version"].setText(str(data.get("parameterVersion") or "--"))
