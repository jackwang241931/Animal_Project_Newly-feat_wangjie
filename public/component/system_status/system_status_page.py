"""Read-only P1 system-status dashboard.

The page deliberately renders only the context and health snapshots supplied
by its callers.  It does not read a database, inspect log text, or infer that
an enabled device is healthy.  This keeps its status language consistent with
the independent health-snapshot channel.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Optional

from PyQt6.QtCore import QTimer, Qt
from PyQt6.QtWidgets import (
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)


UNKNOWN_TEXT = "未知"

# Gas-exchange metrics are shared by the P1 channel cards and the P6
# biological-status card.  Prefer a persisted/observed value when one is
# available; otherwise derive it only when both raw VO2 and VCO2 samples are
# present.  A missing input remains unknown rather than becoming a demo value.
_VO2_ALIASES = (
    "vo2_consumption", "VO2_L_min", "VO2_ml_min", "VO2", "vo2",
    "VO2耗氧量", "氧耗量", "氧气消耗速率",
)
_VCO2_ALIASES = (
    "vco2_production", "VCO2_L_min", "VCO2_ml_min", "VCO2", "vco2",
    "VCO2二氧化碳产率", "二氧化碳产率", "二氧化碳产生速率",
)
_RER_ALIASES = ("rer_rq", "RER", "RQ", "respiratory_exchange_ratio", "呼吸熵", "呼吸商")
_EE_ALIASES = (
    "ee_energy_expenditure", "EE_kcal_hr", "EE", "energy_expenditure",
    "能量消耗", "产热", "热量消耗",
)


def _formula_number(value: Any) -> Optional[float]:
    if isinstance(value, Mapping):
        value = value.get("value")
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def _formula_measurement(measurements: Mapping[str, Any], aliases: Iterable[str]) -> Optional[float]:
    lowered = {str(key).strip().lower(): value for key, value in measurements.items()}
    for alias in aliases:
        value = lowered.get(str(alias).strip().lower())
        number = _formula_number(value)
        if number is not None:
            return number
    for key, value in measurements.items():
        key_text = str(key).strip().lower()
        if any(str(alias).strip().lower() in key_text for alias in aliases):
            number = _formula_number(value)
            if number is not None:
                return number
    return None


def calculate_rer(measurements: Mapping[str, Any]) -> Optional[float]:
    """Return RER (VCO2 / VO2) from real observations, if available."""
    explicit = _formula_measurement(measurements, _RER_ALIASES)
    if explicit is not None:
        return explicit
    vo2 = _formula_measurement(measurements, _VO2_ALIASES)
    vco2 = _formula_measurement(measurements, _VCO2_ALIASES)
    if vo2 is None or vco2 is None or vo2 == 0:
        return None
    return vco2 / vo2


def calculate_ee(measurements: Mapping[str, Any]) -> Optional[float]:
    """Return EE in kcal/hr using the configured Weir equation."""
    explicit = _formula_measurement(measurements, _EE_ALIASES)
    if explicit is not None:
        return explicit
    vo2 = _formula_measurement(measurements, _VO2_ALIASES)
    vco2 = _formula_measurement(measurements, _VCO2_ALIASES)
    if vo2 is None or vco2 is None:
        return None
    return 60.0 * (3.815 * vo2 + 1.232 * vco2)


def format_duration(value: Any) -> str:
    """Format elapsed/remaining seconds consistently across workflow pages."""
    if value is None or value == "":
        return UNKNOWN_TEXT
    try:
        seconds = max(int(float(value)), 0)
    except (TypeError, ValueError):
        return UNKNOWN_TEXT
    days, remainder = divmod(seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{days}天{hours}小时{minutes}分{seconds}秒"


def _health_state(value: Any) -> str:
    raw = getattr(value, "value", value)
    return str(raw or "unknown").strip().lower()


_IGNORED_ALERT_REASONS = frozenset({
    "data_stale",
    "frame_stale",
    "stale_data",
    "stale_frame",
})


def _is_ignored_alert(item: Mapping[str, Any]) -> bool:
    reason = str(item.get("reason_code") or "").strip().lower()
    return reason in _IGNORED_ALERT_REASONS


def derive_channel_status_issues(
    context: Mapping[str, Any],
    components: list[Mapping[str, Any]],
    alerts: list[Mapping[str, Any]],
) -> list[str]:
    """Derive the same channel-level issues used by the P1 channel cards."""
    run_state = _health_state(context.get("experiment_status"))
    running = run_state in {"running", "monitoring", "starting", "stopping"}
    enabled = context.get("enabled_channels")
    enabled_set = {str(item).upper() for item in enabled} if isinstance(enabled, (list, tuple, set)) else set()
    issues: list[str] = []
    for index in range(1, 9):
        channel = f"CH{index:02d}"
        scoped = [
            item for item in [*components, *alerts]
            if (
                not _is_ignored_alert(item)
                and (
                    str(item.get("scope", "")).upper() == channel
                    or str(item.get("channel", item.get("channel_id", ""))).upper() == channel
                )
            )
        ]
        states = [_health_state(item.get("status", item.get("state"))) for item in scoped]
        if any(state in {"warning", "fault"} for state in states):
            issues.append(channel)
        elif running and channel in enabled_set and "normal" not in states:
            issues.append(channel)
    return issues


def health_summary_text(
    context: Mapping[str, Any],
    components: list[Mapping[str, Any]],
    alerts: list[Mapping[str, Any]],
    channel_status_issues: Iterable[str] = (),
) -> str:
    """Return the single health summary wording shared by P1 and P3."""
    affected = sorted({
        str(item.get("scope", item.get("channel", ""))).upper()
        for item in alerts
        if str(item.get("scope", item.get("channel", ""))).upper().startswith("CH")
    })
    issue_channels = list(dict.fromkeys(str(item).upper() for item in channel_status_issues))
    if alerts or issue_channels:
        issue_count = len(alerts) + len(issue_channels)
        value = f"{issue_count}项异常"
        affected_channels = affected or issue_channels
        if affected_channels:
            value += " · " + ", ".join(affected_channels)
        return value

    health_state = _health_state(context.get("health_state"))
    component_states = {
        _health_state(item.get("status", item.get("state")))
        for item in components
        if bool(item.get("enabled", True))
    }
    if health_state == "normal" or (health_state == "unknown" and component_states and component_states <= {"normal"}):
        return "在线"
    if health_state == "unavailable":
        return "部分不可用"
    if health_state in {"warning", "fault"}:
        return "告警" if health_state == "warning" else "故障"
    return UNKNOWN_TEXT


class SystemStatusPage(QWidget):
    """P1 dashboard fed by a health provider and a context provider.

    ``health_source`` may be a callable or an object exposing ``snapshot()``,
    ``get_snapshot()`` or ``get_health_snapshot()``.  It should return a
    mapping with ``components`` and ``alerts`` lists.  ``context_provider``
    follows the same convention and returns the experiment/data summary.
    Missing values are intentionally rendered as ``未知`` rather than zero or
    demo data.
    """

    COMPONENTS = (
        ("collection", "采集与 Modbus", {"monitor_collection", "monitor_data", "data_collection"}),
        ("gas", "气路", {"gas_path", "ufc_ugc_zos", "ufc", "ugc", "zos"}),
        ("storage", "数据库存储", {"data_storage", "storage", "database"}),
        ("deep", "轨迹相机", {"deep_camera"}),
        ("infrared", "红外相机", {"infrared_camera"}),
        ("trajectory", "轨迹服务", {"trajectory", "mouse_trajectory", "trajectory_service"}),
    )

    _STATE_META = {
        "unknown": ("未知", "neutral"),
        "normal": ("正常", "normal"),
        "warning": ("告警", "warning"),
        "fault": ("故障", "fault"),
        "unavailable": ("不可用", "neutral"),
    }
    _STATE_SEVERITY = {"unknown": 0, "normal": 1, "unavailable": 2, "warning": 3, "fault": 4}
    _RUN_META = {
        "not_started": ("未开始", "neutral"),
        "initialized": ("未开始", "neutral"),
        "configuring": ("配置中", "info"),
        "starting": ("启动中", "info"),
        "running": ("运行中", "normal"),
        "monitoring": ("运行中", "normal"),
        "stopping": ("停止中", "warning"),
        "stopped": ("已停止", "neutral"),
        "start_failed": ("启动失败", "fault"),
        "stop_failed": ("停止失败", "fault"),
        "failed": ("操作失败", "fault"),
    }

    def __init__(
        self,
        health_source: Any = None,
        context_provider: Any = None,
        refresh_interval_ms: int = 2000,
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self._health_source = health_source
        self._context_provider = context_provider
        self._refresh_interval_ms = max(int(refresh_interval_ms or 2000), 250)
        self._overview_values: dict[str, QLabel] = {}
        self._data_values: dict[str, QLabel] = {}
        self._data_details: dict[str, QLabel] = {}
        self._environment_values: dict[str, QLabel] = {}
        self._health_cards: dict[str, dict[str, QLabel]] = {}
        self._channel_cards: dict[str, dict[str, QLabel]] = {}
        self._alerts_layout: Optional[QVBoxLayout] = None
        self._alerts_empty_label: Optional[QLabel] = None
        self._alert_strip: Optional[QFrame] = None
        self._alert_strip_label: Optional[QLabel] = None
        self._evidence_toggle: Optional[QToolButton] = None
        self._evidence_body: Optional[QWidget] = None
        self._evidence_layout: Optional[QVBoxLayout] = None
        self._last_components: list[dict[str, Any]] = []
        self._last_alerts: list[dict[str, Any]] = []
        self._channel_status_issues: list[str] = []
        self._timer = QTimer(self)
        self._timer.setInterval(self._refresh_interval_ms)
        self._timer.timeout.connect(self.refresh)
        self._build_ui()
        self.start_refresh()

    # -- public provider/refresh API -------------------------------------------------
    def set_health_source(self, health_source: Any) -> None:
        self._health_source = health_source
        self.refresh()

    def set_context_provider(self, context_provider: Any) -> None:
        self._context_provider = context_provider
        self.refresh()

    def start_refresh(self) -> None:
        if not self._timer.isActive():
            self._timer.start()

    def stop_refresh(self) -> None:
        if self._timer.isActive():
            self._timer.stop()

    def focus_alerts(self) -> None:
        """Compatibility hook for the global alert button.

        The detailed P1-E panel and diagnostic drawer are no longer rendered
        on the landing page.  Keep this method so older callers remain safe;
        the structured alert data is still retained for the global summary
        and channel cards.
        """

        if self._evidence_toggle is not None and self._last_alerts:
            self._evidence_toggle.setChecked(True)
            self._toggle_evidence(True)
        # With the detailed P1-E section removed, take the user to the first
        # affected channel card when the global alert chip is clicked.
        target = None
        for channel in self._channel_status_issues:
            labels = self._channel_cards.get(channel)
            if labels:
                target = labels.get("card")
                if target is not None:
                    break
        if target is not None:
            target.setFocus(Qt.FocusReason.OtherFocusReason)
            target.ensurePolished()
            scroll = getattr(self, "_scroll", None)
            if scroll is not None:
                scroll.ensureWidgetVisible(target, 0, 12)

    def refresh(self) -> None:
        context = self._as_mapping(self._read_provider(self._context_provider))
        health = self._as_mapping(self._read_provider(self._health_source))
        components = [
            item for item in self._normalise_items(health.get("components"))
            if not _is_ignored_alert(item)
        ]
        alerts = [
            item for item in self._normalise_items(health.get("alerts"))
            if not _is_ignored_alert(item)
        ]

        # A provider may omit an explicit alerts list.  Derive it only from
        # active structured health states; never inspect an unrelated log.
        if not alerts:
            alerts = [
                item for item in components
                if self._normalise_state(item.get("status", item.get("state")))
                in {"warning", "fault", "unavailable"}
                and bool(item.get("enabled", True))
            ]

        self._last_components = components
        self._last_alerts = alerts
        self._update_component_health(components)
        self._update_channel_cards(context, components, alerts)
        self._update_overview(context, components, alerts)
        self._update_data_summary(context, components, alerts)
        self._update_environment(context)
        self._update_alerts(alerts)
        self._update_evidence(components)

    # -- layout ----------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.setObjectName("systemStatusPage")
        self.setStyleSheet(
            """
            QWidget#systemStatusPage { background: #f4f8fc; color: #17395c; }
            QFrame#statusSection { background: transparent; border: 0; }
            QFrame#statusCard, QFrame#healthCard, QFrame#alertCard, QFrame#alertStrip {
                background: #ffffff; border: 1px solid #d7e6f4; border-radius: 10px;
            }
            QLabel#sectionEyebrow { color: #1872c5; font-size: 11px; font-weight: 700; letter-spacing: 1px; }
            QLabel#sectionTitle { color: #12385f; font-size: 17px; font-weight: 700; }
            QLabel#sectionHint { color: #7191b2; font-size: 11px; }
            QLabel#cardTitle { color: #6585a6; font-size: 11px; }
            QLabel#cardValue { color: #0b579e; font-size: 17px; font-weight: 700; }
            QLabel#cardDetail { color: #7191b2; font-size: 11px; }
            QLabel#channelData { color: #173f66; font-size: 11px; line-height: 1.35; }
            QLabel[statusChip="true"] { border-radius: 10px; padding: 3px 8px; font-size: 11px; font-weight: 700; }
            QLabel[statusTone="neutral"] { background: #edf1f5; color: #56616d; }
            QLabel[statusTone="info"] { background: #e2f1ff; color: #156fc1; }
            QLabel[statusTone="normal"] { background: #e4f7ed; color: #178850; }
            QLabel[statusTone="warning"] { background: #fff4df; color: #b56800; }
            QLabel[statusTone="fault"] { background: #ffebec; color: #d33f49; }
            QToolButton#evidenceToggle { color: #156fc1; font-weight: 700; border: 0; padding: 5px; }
            QToolButton#alertDetailButton { color: #156fc1; border: 0; font-weight: 700; padding: 2px 0; }
            """
        )

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self._scroll = QScrollArea(self)
        self._scroll.setObjectName("systemStatusScroll")
        self._scroll.setWidgetResizable(True)
        self._scroll.setFrameShape(QFrame.Shape.NoFrame)
        root.addWidget(self._scroll)

        canvas = QWidget(self._scroll)
        canvas.setObjectName("systemStatusCanvas")
        canvas.setStyleSheet("QWidget#systemStatusCanvas { background: #f4f8fc; }")
        content = QVBoxLayout(canvas)
        content.setContentsMargins(14, 12, 14, 18)
        content.setSpacing(14)
        self._scroll.setWidget(canvas)

        overview_grid = QGridLayout()
        overview_grid.setHorizontalSpacing(10)
        overview_grid.setVerticalSpacing(10)
        for index, (key, title) in enumerate((
            ("experiment_status", "实验运行状态"),
            ("run_duration", "运行时长"),
            ("system_health", "系统健康"),
        )):
            card, value, detail = self._make_value_card(title)
            self._overview_values[key] = value
            if key == "run_duration":
                detail.setObjectName("progressDetail")
                self._overview_values["progress_detail"] = detail
            overview_grid.addWidget(card, 0, index)
        content.addLayout(overview_grid)

        data_grid = QGridLayout()
        data_grid.setHorizontalSpacing(10)
        data_grid.setVerticalSpacing(10)
        for index, (key, title) in enumerate((
            ("enabled_channels", "在线通道"),
            ("data_points_total", "数据点总量"),
            ("average_ee", "平均 EE（能量消耗）"),
            ("storage_size", "数据总量"),
        )):
            card, value, detail = self._make_value_card(title)
            self._data_values[key] = value
            self._data_details[key] = detail
            data_grid.addWidget(card, 0, index)
        content.addLayout(data_grid)

        health_header = self._make_header("P1-C", "系统健康", "健康来自独立快照：进程存活、设备可用与数据新鲜度分别判断。")
        health_grid = QGridLayout()
        health_grid.setHorizontalSpacing(10)
        health_grid.setVerticalSpacing(10)
        for index, (key, title, _aliases) in enumerate(self.COMPONENTS):
            card, labels = self._make_health_card(title)
            self._health_cards[key] = labels
            health_grid.addWidget(card, index // 3, index % 3)
        health_host = QWidget(canvas)
        health_host.setLayout(health_grid)
        # Detailed subsystem health is kept in the health store for the
            # global top bar and summary card, but is intentionally removed from the P1 landing
        # layout per the confirmed compact design.
        health_header.hide()
        health_host.hide()
        evidence = self._make_evidence_drawer()
        evidence.setParent(canvas)
        evidence.hide()

        environment_header = self._make_header("ENV", "实时环境指标", "只显示当前可取得的真实环境数据。")
        environment_header.hide()
        environment_grid = QGridLayout()
        environment_grid.setHorizontalSpacing(10)
        environment_grid.setVerticalSpacing(10)
        for index, (key, title) in enumerate((
            ("temperature", "温度"), ("humidity", "湿度"), ("noise", "噪声"),
            ("light", "光照"), ("oxygen", "氧气"), ("co2", "二氧化碳"),
        )):
            card, value, _detail = self._make_value_card(title)
            self._environment_values[key] = value
            environment_grid.addWidget(card, index // 3, index % 3)
        # Keep the hidden environment cards owned by the page.  They are not
        # rendered on P1, but providers and existing tests still use their
        # labels as the real-time environment value sink.
        environment_host = QWidget(canvas)
        environment_host.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        environment_host.setLayout(environment_grid)
        environment_host.hide()

        # P1-F: keep the eight cage/channel view visible on the landing page.
        # Channel health is derived only from structured snapshots/alerts; an
        # enabled channel without an observation is never displayed as 在线.
        channel_header = QFrame(canvas)
        channel_header_layout = QHBoxLayout(channel_header)
        channel_header_layout.setContentsMargins(0, 0, 0, 0)
        channel_header_layout.addWidget(
            self._make_header("CHANNELS", "8 通道视窗", "显示各笼状态；异常通道会单独标红。"), 1
        )
        content.addWidget(channel_header)
        channel_grid = QGridLayout()
        channel_grid.setHorizontalSpacing(10)
        channel_grid.setVerticalSpacing(10)
        for index in range(1, 9):
            channel = f"CH{index:02d}"
            card, labels = self._make_channel_card(channel)
            self._channel_cards[channel] = labels
            channel_grid.addWidget(card, (index - 1) // 4, (index - 1) % 4)
        content.addLayout(channel_grid)

        advanced_grid = QGridLayout()
        advanced_grid.setHorizontalSpacing(10)
        for index, title in enumerate(("EE（能量消耗）", "RER（呼吸熵）", "行为复杂度")):
            card, value, detail = self._make_value_card(title)
            value.setText("None")
            detail.setText("None")
            advanced_grid.addWidget(card, 0, index)
        advanced_host = QWidget(canvas)
        advanced_host.setLayout(advanced_grid)
        advanced_header = self._make_header("P1-D", "高级指标", "公式未确定前仅显示占位，不参与健康判定。")
        advanced_header.hide()
        advanced_host.hide()

        self._alerts_anchor = self._make_header("HEALTH", "系统健康异常", "显示当前系统健康快照中的具体异常；正常组件显示在线。")
        self._alerts_anchor.setParent(canvas)
        self._alerts_anchor.hide()
        alerts_frame = QFrame()
        alerts_frame.setObjectName("statusSection")
        self._alerts_layout = QVBoxLayout(alerts_frame)
        self._alerts_layout.setContentsMargins(0, 0, 0, 0)
        self._alerts_layout.setSpacing(8)
        self._alerts_empty_label = QLabel("系统正常，当前没有需要处理的异常。")
        self._alerts_empty_label.setObjectName("sectionHint")
        self._alerts_layout.addWidget(self._alerts_empty_label)
        alerts_frame.setParent(canvas)
        alerts_frame.hide()

        self._alert_strip = self._make_alert_strip()
        self._alert_strip.setParent(canvas)
        self._alert_strip.hide()
        content.addStretch(1)

    def _make_header(self, eyebrow: str, title: str, hint: str) -> QWidget:
        frame = QFrame()
        frame.setObjectName("statusSection")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)
        label = QLabel(eyebrow)
        label.setObjectName("sectionEyebrow")
        layout.addWidget(label)
        title_label = QLabel(title)
        title_label.setObjectName("sectionTitle")
        layout.addWidget(title_label)
        layout.addStretch(1)
        hint_label = QLabel(hint)
        hint_label.setObjectName("sectionHint")
        hint_label.setWordWrap(True)
        hint_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(hint_label, 1)
        return frame

    def _make_value_card(self, title: str) -> tuple[QFrame, QLabel, QLabel]:
        card = QFrame()
        card.setObjectName("statusCard")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        card.setMinimumHeight(88)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 9)
        layout.setSpacing(3)
        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        value = QLabel(UNKNOWN_TEXT)
        value.setObjectName("cardValue")
        value.setWordWrap(True)
        detail = QLabel("")
        detail.setObjectName("cardDetail")
        detail.setWordWrap(True)
        layout.addWidget(title_label)
        layout.addWidget(value)
        layout.addWidget(detail)
        return card, value, detail

    def _make_health_card(self, title: str) -> tuple[QFrame, dict[str, QLabel]]:
        card = QFrame()
        card.setObjectName("healthCard")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        card.setMinimumHeight(138)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(5)
        top = QHBoxLayout()
        name = QLabel(title)
        name.setObjectName("cardTitle")
        chip = QLabel()
        chip.setProperty("statusChip", True)
        top.addWidget(name)
        top.addStretch(1)
        top.addWidget(chip)
        reason = QLabel(UNKNOWN_TEXT)
        reason.setObjectName("cardValue")
        reason.setWordWrap(True)
        success = QLabel("最近成功：未知")
        success.setObjectName("cardDetail")
        failures = QLabel("连续失败：未知")
        failures.setObjectName("cardDetail")
        layout.addLayout(top)
        layout.addWidget(reason)
        layout.addWidget(success)
        layout.addWidget(failures)
        return card, {"chip": chip, "reason": reason, "success": success, "failures": failures}

    def _make_channel_card(self, channel: str) -> tuple[QFrame, dict[str, QLabel]]:
        card = QFrame()
        card.setObjectName("healthCard")
        card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        card.setMinimumHeight(112)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        top = QHBoxLayout()
        name = QLabel(channel)
        name.setObjectName("cardValue")
        chip = QLabel("未知")
        chip.setProperty("statusChip", True)
        top.addWidget(name)
        top.addStretch(1)
        top.addWidget(chip)
        issue = QLabel("暂无通道健康快照")
        issue.setObjectName("cardDetail")
        issue.setWordWrap(True)
        basis = QLabel("在线状态由采样新鲜度与连续失败次数判定")
        basis.setObjectName("cardDetail")
        basis.setWordWrap(True)
        data = QLabel()
        data.setObjectName("channelData")
        data.setTextFormat(Qt.TextFormat.RichText)
        data.setWordWrap(True)
        layout.addLayout(top)
        layout.addWidget(data)
        layout.addWidget(issue)
        layout.addWidget(basis)
        return card, {
            "card": card,
            "chip": chip,
            "data": data,
            "issue": issue,
            "basis": basis,
        }

    def _make_evidence_drawer(self) -> QWidget:
        frame = QFrame()
        frame.setObjectName("statusCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(10, 7, 10, 8)
        self._evidence_toggle = QToolButton(frame)
        self._evidence_toggle.setObjectName("evidenceToggle")
        self._evidence_toggle.setCheckable(True)
        self._evidence_toggle.setChecked(False)
        self._evidence_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self._evidence_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self._evidence_toggle.setText("子系统健康抽屉（原始诊断证据）")
        self._evidence_toggle.toggled.connect(self._toggle_evidence)
        layout.addWidget(self._evidence_toggle)
        self._evidence_body = QWidget(frame)
        self._evidence_layout = QVBoxLayout(self._evidence_body)
        self._evidence_layout.setContentsMargins(6, 3, 6, 2)
        self._evidence_layout.setSpacing(4)
        self._evidence_body.hide()
        layout.addWidget(self._evidence_body)
        return frame

    def _make_alert_strip(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("alertStrip")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(12, 9, 12, 9)
        self._alert_strip_label = QLabel()
        self._alert_strip_label.setWordWrap(True)
        layout.addWidget(self._alert_strip_label, 1)
        button = QToolButton(frame)
        button.setObjectName("alertDetailButton")
        button.setText("查看异常")
        button.clicked.connect(self.focus_alerts)
        layout.addWidget(button)
        frame.hide()
        return frame

    # -- rendering -------------------------------------------------------------------
    def _update_overview(
        self,
        context: Mapping[str, Any],
        components: list[dict[str, Any]] | None = None,
        alerts: list[dict[str, Any]] | None = None,
    ) -> None:
        run_state = self._normalise_state(context.get("experiment_status"))
        run_text, _tone = self._RUN_META.get(run_state, (UNKNOWN_TEXT, "neutral"))
        self._set_value(self._overview_values.get("experiment_status"), run_text)
        elapsed = self._format_duration(context.get("elapsed_seconds"))
        remaining = self._format_duration(context.get("remaining_seconds"))
        self._set_value(self._overview_values.get("run_duration"), elapsed)
        progress = self._format_percent(context.get("progress_percent"))
        detail = self._overview_values.get("progress_detail")
        if detail is not None:
            if elapsed == UNKNOWN_TEXT:
                detail.setText("已运行：未知")
            elif remaining == UNKNOWN_TEXT:
                detail.setText(f"已运行：{elapsed} · 剩余：未知")
            else:
                detail.setText(f"已运行：{elapsed} · 剩余：{remaining} · 进度：{progress}")

        self._set_value(
            self._overview_values.get("system_health"),
            self._health_summary_value(context, components or [], alerts or []),
        )

    def _health_summary_value(
        self,
        context: Mapping[str, Any],
        components: list[dict[str, Any]],
        alerts: list[dict[str, Any]],
    ) -> str:
        """Render health from the structured snapshot, never from defaults."""
        return health_summary_text(context, components, alerts, self._channel_status_issues)

    def _update_data_summary(
        self,
        context: Mapping[str, Any],
        components: list[dict[str, Any]] | None = None,
        alerts: list[dict[str, Any]] | None = None,
    ) -> None:
        components = components or []
        configured = context.get("enabled_channels")
        configured_set = {
            str(item).upper()
            for item in configured
            if str(item).strip()
        } if isinstance(configured, (list, tuple, set)) else set()

        online = context.get("online_channels")
        if isinstance(online, (list, tuple, set)):
            online_set = {str(item).upper() for item in online if str(item).strip()}
        else:
            # Fallback for standalone page tests/providers: only a normal,
            # channel-scoped collection snapshot counts as online.
            online_set = {
                str(item.get("scope", item.get("channel", ""))).upper()
                for item in components
                if self._normalise_state(item.get("status", item.get("state"))) == "normal"
                and self._component_matches(item, {"monitor_collection", "monitor_data", "data_collection"})
                and str(item.get("scope", item.get("channel", ""))).upper().startswith("CH")
            }

        if configured_set:
            online_set &= configured_set
            channel_value = f"{len(online_set)}/{len(configured_set)}"
            channel_detail = (
                "来自通道级健康快照"
                if online_set
                else "尚无有效通道快照"
            )
        else:
            channel_value = UNKNOWN_TEXT
            channel_detail = "尚未加载通道配置"
        self._set_value(self._data_values.get("enabled_channels"), channel_value)
        self._set_value(self._data_details.get("enabled_channels"), channel_detail)
        self._set_value(self._data_values.get("data_points_total"), context.get("data_points_total"))
        points_detail = context.get("data_points_detail")
        if points_detail is None:
            points_detail = (
                "所有启用通道累计"
                if context.get("data_points_total") is not None
                else "尚无实际落盘数据"
            )
        self._set_value(self._data_details.get("data_points_total"), points_detail)
        average_ee = context.get("average_ee")
        average_ee_detail = context.get("average_ee_detail")
        if average_ee is None:
            # Build the summary only from the latest real per-channel health
            # observations.  No observation means the card stays unknown.
            ee_values: list[float] = []
            scopes = {
                str(item.get("scope", item.get("channel", ""))).upper()
                for item in components
                if str(item.get("scope", item.get("channel", ""))).upper().startswith("CH")
            }
            for scope in scopes:
                scoped = [
                    item for item in components
                    if str(item.get("scope", item.get("channel", ""))).upper() == scope
                ]
                value = calculate_ee(self._channel_measurements(scoped))
                if value is not None:
                    ee_values.append(value)
            if ee_values:
                average_ee = f"{sum(ee_values) / len(ee_values):.3f} kcal/hr"
                average_ee_detail = f"{len(ee_values)} 个通道的真实 EE 平均值"
        self._set_value(self._data_values.get("average_ee"), average_ee)
        self._set_value(
            self._data_details.get("average_ee"),
            average_ee_detail or "由 VO₂/VCO₂ 按 EE 公式计算",
        )
        self._set_value(self._data_values.get("storage_size"), self._format_size(context.get("storage_size")))
        self._set_value(
            self._data_details.get("storage_size"),
            "实验目录全部文件" if context.get("storage_size") is not None else "尚未找到实验目录",
        )

    def _update_component_health(self, components: list[dict[str, Any]]) -> None:
        for key, _title, aliases in self.COMPONENTS:
            labels = self._health_cards.get(key)
            if not labels:
                continue
            matched = [item for item in components if self._component_matches(item, aliases)]
            summary = self._summarise_component(matched)
            state = summary["state"]
            state_text, tone = self._STATE_META.get(state, (UNKNOWN_TEXT, "neutral"))
            chip = labels["chip"]
            chip.setText(state_text)
            self._apply_tone(chip, tone)
            labels["reason"].setText(summary["reason"] or UNKNOWN_TEXT)
            labels["success"].setText(f"最近成功：{self._format_timestamp(summary['last_success_at'])}")
            failures = summary["consecutive_failures"]
            failures_text = str(failures) if failures is not None else UNKNOWN_TEXT
            labels["failures"].setText(f"连续失败：{failures_text}")

    @classmethod
    def _channel_measurements(cls, observations: list[dict[str, Any]]) -> dict[str, Any]:
        """Return the newest directly observed values for one channel."""

        measurements: dict[str, Any] = {}
        metric_times: dict[str, float] = {}
        for item in observations:
            if cls._normalise_state(item.get("status", item.get("state"))) != "normal":
                continue
            metrics = item.get("metrics") or {}
            if not isinstance(metrics, Mapping):
                continue
            observed_at = item.get("observed_at", 0) or 0
            try:
                observed_at = float(observed_at)
            except (TypeError, ValueError):
                observed_at = 0.0
            source = metrics.get("measurements")
            if isinstance(source, Mapping):
                for key, value in source.items():
                    if value is None:
                        continue
                    if observed_at >= metric_times.get(str(key), float("-inf")):
                        measurements[str(key)] = value
                        metric_times[str(key)] = observed_at
            for key in ("behavior", "behavior_label", "action"):
                value = metrics.get(key)
                if value is not None and observed_at >= metric_times.get(key, float("-inf")):
                    measurements[key] = value
                    metric_times[key] = observed_at
        return measurements

    @classmethod
    def _measurement_value(cls, measurements: Mapping[str, Any], *terms: str) -> Any:
        for key, value in measurements.items():
            text = str(key).lower()
            if any(term.lower() in text for term in terms):
                return value
        return None

    @classmethod
    def _render_channel_value(cls, value: Any, unit: str = "") -> str:
        rendered = cls._safe_text(value)
        if rendered == UNKNOWN_TEXT:
            return rendered
        if isinstance(value, float):
            rendered = f"{value:.3f}".rstrip("0").rstrip(".")
        return f"{rendered}{unit}"

    def _render_channel_data(self, channel: str, context: Mapping[str, Any], scoped: list[dict[str, Any]]) -> str:
        measurements = self._channel_measurements(scoped)
        individuals = context.get("channel_individuals")
        individual = individuals.get(channel, {}) if isinstance(individuals, Mapping) else {}
        individual_id = individual.get("id") if isinstance(individual, Mapping) else None
        behavior = (
            measurements.get("behavior")
            or measurements.get("behavior_label")
            or measurements.get("action")
            or self._measurement_value(measurements, "行为")
            or "自由活动"
        )
        motion = self._measurement_value(measurements, "跑轮圈数", "运动计数", "运动量", "运动")
        body_temperature = self._measurement_value(
            measurements, "核心体温", "体温", "体表温度", "红外温度"
        )
        rer = calculate_rer(measurements)
        ee = calculate_ee(measurements)
        # RER/EE formulas are defined.  A missing real sample is represented
        # by None, not by the old "公式待定" placeholder.
        rer_text = "None" if rer is None else self._render_channel_value(rer)
        ee_text = "None" if ee is None else self._render_channel_value(ee, " kcal/hr")
        rows = (
            ("个体", self._render_channel_value(individual_id), "行为", self._safe_text(behavior), "体温", self._render_channel_value(body_temperature, " °C")),
            ("运动", self._render_channel_value(motion), "呼吸熵（RER）", rer_text, "产热（EE）", ee_text),
        )
        html_rows = []
        for left, left_value, middle, middle_value, right, right_value in rows:
            html_rows.append(
                "<tr>"
                f"<td>{left}</td><td><b>{left_value}</b></td>"
                f"<td>{middle}</td><td><b>{middle_value}</b></td>"
                f"<td>{right}</td><td><b>{right_value}</b></td>"
                "</tr>"
            )
        return "<table width='100%' cellspacing='0' cellpadding='1'>" + "".join(html_rows) + "</table>"

    def _update_channel_cards(
        self,
        context: Mapping[str, Any],
        components: list[dict[str, Any]],
        alerts: list[dict[str, Any]],
    ) -> None:
        # Normalize enum/string providers alike.  ``str(Enum)`` is not the
        # lifecycle value on all Python versions, so do not compare it raw.
        run_state = self._normalise_state(context.get("experiment_status"))
        running = run_state in {"running", "monitoring", "starting", "stopping"}
        enabled = context.get("enabled_channels")
        enabled_set = {
            str(item).upper() for item in enabled
        } if isinstance(enabled, (list, tuple, set)) else set()
        self._channel_status_issues = []
        for channel, labels in self._channel_cards.items():
            issue_label = labels["issue"]
            basis_label = labels["basis"]
            data_label = labels["data"]
            issue_label.show()
            basis_label.show()
            data_label.show()
            scoped = [
                item for item in [*components, *alerts]
                if (
                    not _is_ignored_alert(item)
                    and (
                        str(item.get("scope", "")).upper() == channel
                        or str(item.get("channel", item.get("channel_id", ""))).upper() == channel
                    )
                )
            ]
            data_label.setText(self._render_channel_data(channel, context, scoped))
            issue_states = [
                self._normalise_state(item.get("status", item.get("state")))
                for item in scoped
                if self._normalise_state(item.get("status", item.get("state")))
                in {"warning", "fault", "unavailable"}
            ]
            if issue_states:
                state = max(issue_states, key=lambda item: self._STATE_SEVERITY.get(item, 0))
                state_text, tone = self._STATE_META.get(state, ("异常", "warning"))
                issue = next(
                    item for item in scoped
                    if self._normalise_state(item.get("status", item.get("state"))) == state
                )
                reason = issue.get("reason_code") or issue.get("message") or "当前通道存在健康异常"
                # Replace the generic chip text with the concrete issue, as
                # requested for the channel cards in the reference layout.
                compact_reason = {
                    "not_configured": "未配置",
                    "external_occupied": "外部占用",
                    "stale_data": "数据超时",
                    "stale_frame": "画面超时",
                    "read_failed": "读取失败",
                    "write_failed": "写入失败",
                    "out_of_range": "数据越界",
                }.get(str(reason).lower(), str(reason).replace("_", " ").strip())
                state_text = compact_reason[:12] if compact_reason else state_text
                issue_label.setText(f"异常：{self._display_component(issue.get('component'))} · {reason}")
                basis_label.setText(f"最近观测：{self._format_timestamp(issue.get('observed_at'))}")
                if state in {"warning", "fault"}:
                    self._channel_status_issues.append(channel)
            else:
                has_channel_observation = any(
                    self._normalise_state(item.get("status", item.get("state"))) == "normal"
                    for item in scoped
                )
                if running and channel in enabled_set:
                    if has_channel_observation:
                        state_text, tone = "在线", "normal"
                        issue_label.hide()
                        basis_label.hide()
                    else:
                        state_text, tone = "未收到采样", "warning"
                        issue_label.setText("异常：未收到下位机采样")
                        basis_label.setText("等待真实通道数据，不能判定为在线")
                        self._channel_status_issues.append(channel)
                else:
                    state_text, tone = "未知", "neutral"
                    issue_label.setText("尚未启用或尚未开始监测")
                    basis_label.setText("开始实验并收到真实数据后才判断可用、告警或故障")
            labels["chip"].setText(state_text)
            self._apply_tone(labels["chip"], tone)
            card = labels.get("card")
            if card is not None:
                card.setStyleSheet(
                    "QFrame#healthCard { background: #fff5f5; border: 1px solid #f2a5a8; border-radius: 10px; }"
                    if tone in {"warning", "fault"} else ""
                )

    def _update_environment(self, context: Mapping[str, Any]) -> None:
        environment = self._as_mapping(context.get("environment"))
        for key, label in self._environment_values.items():
            item = environment.get(key)
            if isinstance(item, Mapping):
                value = item.get("value")
                unit = item.get("unit", "")
                rendered = self._render_value(value, unit)
            else:
                rendered = self._render_value(item)
            self._set_value(label, rendered)

    def _update_alerts(self, alerts: list[dict[str, Any]]) -> None:
        if self._alerts_layout is None:
            return
        while self._alerts_layout.count() > 0:
            child = self._alerts_layout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()

        if not alerts:
            self._alerts_empty_label = QLabel("系统正常，当前没有需要处理的异常。")
            self._alerts_empty_label.setObjectName("sectionHint")
            self._alerts_layout.addWidget(self._alerts_empty_label)
            if self._alert_strip is not None:
                self._alert_strip.hide()
            return

        for alert in alerts:
            self._alerts_layout.addWidget(self._make_alert_card(alert))
        if self._alert_strip is not None and self._alert_strip_label is not None:
            self._alert_strip_label.setText(f"当前健康快照包含 {len(alerts)} 项未恢复异常。")
            tone = "fault" if any(self._normalise_state(item.get("status", item.get("state"))) == "fault" for item in alerts) else "warning"
            self._alert_strip.setStyleSheet(
                "QFrame#alertStrip { background: #ffebec; border: 1px solid #f4b4b9; border-radius: 10px; color: #c3313b; }"
                if tone == "fault"
                else "QFrame#alertStrip { background: #fff8eb; border: 1px solid #f1d392; border-radius: 10px; color: #a46400; }"
            )
            # The detailed P1-E alert strip was removed from the compact
            # landing page; alert counts remain available in the global bar
            # and the top summary card.
            self._alert_strip.hide()

    def _make_alert_card(self, alert: Mapping[str, Any]) -> QFrame:
        state = self._normalise_state(alert.get("status", alert.get("state")))
        state_text, tone = self._STATE_META.get(state, (UNKNOWN_TEXT, "neutral"))
        component = self._display_component(alert.get("component"))
        scope = self._display_scope(alert.get("scope"))
        reason = str(alert.get("reason_code") or alert.get("message") or UNKNOWN_TEXT)
        observed = self._format_timestamp(alert.get("observed_at"))
        frame = QFrame()
        frame.setObjectName("alertCard")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(4)
        top = QHBoxLayout()
        title = QLabel(f"{component} · {scope}")
        title.setObjectName("cardValue")
        chip = QLabel(state_text)
        chip.setProperty("statusChip", True)
        self._apply_tone(chip, tone)
        top.addWidget(title)
        top.addStretch(1)
        top.addWidget(chip)
        basis = QLabel(f"判定依据：{reason}")
        basis.setObjectName("cardDetail")
        basis.setWordWrap(True)
        occurred = QLabel(f"发生时间：{observed}")
        occurred.setObjectName("cardDetail")
        suggestion = QLabel(f"建议动作：{self._suggestion(alert)}")
        suggestion.setObjectName("cardDetail")
        suggestion.setWordWrap(True)
        detail = QToolButton(frame)
        detail.setObjectName("alertDetailButton")
        detail.setText("查看原始证据")
        detail.clicked.connect(self.focus_alerts)
        layout.addLayout(top)
        layout.addWidget(basis)
        layout.addWidget(occurred)
        layout.addWidget(suggestion)
        layout.addWidget(detail, alignment=Qt.AlignmentFlag.AlignRight)
        return frame

    def _update_evidence(self, components: list[dict[str, Any]]) -> None:
        if self._evidence_layout is None:
            return
        while self._evidence_layout.count() > 0:
            child = self._evidence_layout.takeAt(0)
            widget = child.widget()
            if widget is not None:
                widget.deleteLater()
        if not components:
            label = QLabel("尚无健康快照；未启用或尚未产生首样本的组件保持未知。")
            label.setObjectName("sectionHint")
            label.setWordWrap(True)
            self._evidence_layout.addWidget(label)
            return
        for item in components:
            state = self._normalise_state(item.get("status", item.get("state")))
            item_label = QLabel(
                f"{self._display_component(item.get('component'))} · {self._display_scope(item.get('scope'))}"
                f" | 状态：{self._STATE_META.get(state, (UNKNOWN_TEXT, 'neutral'))[0]}"
                f" | 最近成功：{self._format_timestamp(item.get('last_success_at'))}"
                f" | 连续失败：{self._safe_text(item.get('consecutive_failures'))}"
                f" | 原因：{self._safe_text(item.get('reason_code') or item.get('message'))}"
            )
            item_label.setObjectName("cardDetail")
            item_label.setWordWrap(True)
            self._evidence_layout.addWidget(item_label)
            metrics = item.get("metrics")
            if isinstance(metrics, Mapping) and metrics:
                metrics_label = QLabel(f"指标：{json.dumps(dict(metrics), ensure_ascii=False, default=str, sort_keys=True)}")
                metrics_label.setObjectName("cardDetail")
                metrics_label.setWordWrap(True)
                self._evidence_layout.addWidget(metrics_label)

    # -- data normalisation ----------------------------------------------------------
    @staticmethod
    def _read_provider(provider: Any) -> Any:
        if provider is None:
            return {}
        try:
            if callable(provider):
                return provider()
            for name in ("get_health_snapshot", "get_snapshot", "snapshot"):
                method = getattr(provider, name, None)
                if callable(method):
                    return method()
        except Exception:
            # A status page must never turn a provider error into an invented
            # healthy state or crash the application UI.
            return {}
        return provider if isinstance(provider, Mapping) else {}

    @staticmethod
    def _as_mapping(value: Any) -> Mapping[str, Any]:
        return value if isinstance(value, Mapping) else {}

    @classmethod
    def _normalise_items(cls, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, Iterable) or isinstance(value, (str, bytes, Mapping)):
            return []
        return [dict(item) for item in value if isinstance(item, Mapping)]

    @staticmethod
    def _normalise_state(value: Any) -> str:
        raw = getattr(value, "value", value)
        return str(raw or "unknown").strip().lower()

    @classmethod
    def _component_matches(cls, item: Mapping[str, Any], aliases: set[str]) -> bool:
        raw = str(item.get("component", "")).strip().lower()
        return raw in aliases

    @classmethod
    def _summarise_component(cls, items: list[dict[str, Any]]) -> dict[str, Any]:
        if not items:
            return {"state": "unknown", "reason": "", "last_success_at": None, "consecutive_failures": None}
        selected = max(
            items,
            key=lambda item: (
                cls._STATE_SEVERITY.get(cls._normalise_state(item.get("status", item.get("state"))), 0),
                float(item.get("observed_at") or 0),
            ),
        )
        successes = [item.get("last_success_at") for item in items if item.get("last_success_at") is not None]
        return {
            "state": cls._normalise_state(selected.get("status", selected.get("state"))),
            "reason": selected.get("reason_code") or selected.get("message") or "",
            "last_success_at": max(successes) if successes else None,
            "consecutive_failures": max(
                (cls._as_nonnegative_int(item.get("consecutive_failures")) for item in items),
                default=None,
            ),
        }

    @staticmethod
    def _as_nonnegative_int(value: Any) -> int:
        try:
            return max(int(value), 0)
        except (TypeError, ValueError):
            return 0

    # -- formatters/helpers ----------------------------------------------------------
    @staticmethod
    def _apply_tone(widget: QWidget, tone: str) -> None:
        widget.setProperty("statusTone", tone)
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    @classmethod
    def _set_value(cls, label: Optional[QLabel], value: Any) -> None:
        if label is None:
            return
        try:
            label.setText(cls._safe_text(value))
        except RuntimeError as exc:
            # A queued refresh may overlap Qt teardown of a hidden/replaced
            # widget. Ignore only the stale-wrapper case.
            message = str(exc)
            if "wrapped C/C++ object" not in message or "deleted" not in message:
                raise

    @staticmethod
    def _safe_text(value: Any) -> str:
        if value is None:
            return UNKNOWN_TEXT
        rendered = str(value).strip()
        return rendered if rendered else UNKNOWN_TEXT

    @classmethod
    def _render_value(cls, value: Any, unit: Any = "") -> str:
        rendered = cls._safe_text(value)
        if rendered == UNKNOWN_TEXT:
            return rendered
        unit_text = str(unit or "").strip()
        return f"{rendered} {unit_text}".strip()

    @staticmethod
    def _format_timestamp(value: Any) -> str:
        if value is None or value == "":
            return UNKNOWN_TEXT
        try:
            timestamp = float(value)
            if timestamp <= 0:
                return UNKNOWN_TEXT
            return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError, OverflowError, OSError):
            return str(value)

    @staticmethod
    def _format_duration(value: Any) -> str:
        return format_duration(value)

    @staticmethod
    def _format_percent(value: Any) -> str:
        if value is None or value == "":
            return UNKNOWN_TEXT
        try:
            return f"{min(max(float(value), 0.0), 100.0):.0f}%"
        except (TypeError, ValueError):
            return UNKNOWN_TEXT

    @staticmethod
    def _format_size(value: Any) -> str:
        if value is None or value == "":
            return UNKNOWN_TEXT
        try:
            size = max(float(value), 0.0)
        except (TypeError, ValueError):
            return str(value)
        # The dashboard reports the complete experiment folder (database,
        # images, videos, CSV/XLSX and other generated files).  Keep the
        # display at MB or larger so the unit remains useful for experiments.
        size_mb = size / (1024.0 ** 2)
        if size_mb < 1024.0:
            return f"{size_mb:.2f} MB"
        size_gb = size_mb / 1024.0
        if size_gb < 1024.0:
            return f"{size_gb:.2f} GB"
        return f"{size_gb / 1024.0:.2f} TB"

    @staticmethod
    def _display_component(value: Any) -> str:
        names = {
            "monitor_collection": "采集与 Modbus",
            "monitor_data_service": "采集服务",
            "p_monitor_data": "采集服务进程",
            "data_storage": "数据库存储",
            "deep_camera": "轨迹相机",
            "deep_camera_lock": "轨迹相机占用状态",
            "p_deep_camera": "轨迹相机进程",
            "infrared_camera": "红外相机",
            "p_infrared_camera": "红外相机进程",
            "trajectory_service": "轨迹服务",
            "mouse_trajectory": "轨迹服务",
            "gas_path": "气路",
        }
        raw = str(value or "").strip().lower()
        return names.get(raw, str(value or UNKNOWN_TEXT))

    @staticmethod
    def _display_scope(value: Any) -> str:
        raw = str(value or "").strip()
        return "全局" if raw.lower() in {"", "global"} else raw

    @staticmethod
    def _suggestion(alert: Mapping[str, Any]) -> str:
        reason = str(alert.get("reason_code") or "").lower()
        if reason == "external_occupied":
            return "确认独立视频程序是否应继续占用相机；如需上位机采集，请先释放相机。"
        if "sqlite" in reason or "storage" in reason:
            return "检查磁盘空间、实验目录权限和数据库写入错误。"
        if "modbus" in reason or "serial" in reason or "collection" in reason:
            return "检查串口、供电、线缆与对应通道模块，然后观察连续成功次数。"
        if "stale" in reason or "frame" in reason:
            return "检查设备连接和数据流；等待连续两次成功后状态会自动恢复。"
        if "process" in reason:
            return "查看进程诊断并确认服务是否仍在运行；由操作员决定是否重启或终止实验。"
        if "out_of_range" in reason or "anomaly" in reason:
            return "先核对传感器值与校准条件；数据异常不等同于仪器损坏。"
        return "查看原始诊断证据，核对设备连接、配置和最近成功时间。"

    def _toggle_evidence(self, visible: bool) -> None:
        if self._evidence_body is not None:
            self._evidence_body.setVisible(bool(visible))
        if self._evidence_toggle is not None:
            self._evidence_toggle.setArrowType(
                Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow
            )
