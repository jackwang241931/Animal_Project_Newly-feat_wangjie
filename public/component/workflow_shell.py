"""Persistent six-step workflow header for the main application window.

This widget intentionally owns presentation only.  It does not decide whether
hardware is healthy and it does not issue experiment-control commands.
"""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from public.config_class.global_setting import global_setting

# The legacy application still exposes some pages through its old menu and
# dynamic-module toolbar.  Keep those compatibility routes synchronized with
# the new six-step shell even after P2--P6 have dedicated workflow pages.
# Names are deliberately stable module identifiers rather than display text,
# which is user-configurable.
_LEGACY_MENU_TO_WORKFLOW_STEP = {
    0: 2,  # 创建实验
    1: 2,  # 设备信息 / 配置
    2: 3,  # 实验检测
    3: 6,  # 数据分析
}
_LEGACY_MODULE_TO_WORKFLOW_STEP = {
    "main_new_experiment": 2,
    "main_new_experiment_open": 2,
    "main_experiment_setting": 2,
    "new_main_experiment_setting": 2,
    "new_main_experiment_calibration": 2,
    "main_new_monitor_data": 3,
    "main_monitor_data": 3,
    "main_user_monitor_data": 4,
    "main_video_image": 5,
    "main_infrared_camera": 5,
    "main_coord3d": 5,
    "mainmousetrajectoryanalysis": 6,
    "maindatacomparison": 6,
    "main_com_assistant": 6,
}


def resolve_legacy_workflow_step(module_name: object = None, menu_id: object = None) -> int | None:
    """Map a legacy route to its confirmed six-step workflow position.

    P4 and P5 share the historical ``实验检测`` menu with P3, so their stable
    module name takes precedence over the coarser menu mapping.
    """

    name = str(module_name or "").strip().lower()
    if name in _LEGACY_MODULE_TO_WORKFLOW_STEP:
        return _LEGACY_MODULE_TO_WORKFLOW_STEP[name]
    try:
        return _LEGACY_MENU_TO_WORKFLOW_STEP.get(int(menu_id))
    except (TypeError, ValueError):
        return None


class WorkflowShell(QFrame):
    """A persistent workflow navigator with separate run and health states."""

    workflow_requested = pyqtSignal(int)
    alert_requested = pyqtSignal()

    STEPS = (
        (1, "系统状态", "门户首页"),
        (2, "实验配置", "实验方案"),
        (3, "实验控制", "运行控制"),
        (4, "数据查看", "本地数据"),
        (5, "机器视觉", "可见 + 红外"),
        (6, "数据分析", "综合分析"),
    )

    _RUN_STATE_LABELS = {
        "not_started": ("实验：未开始", "neutral"),
        "initialized": ("实验：未开始", "neutral"),
        "configuring": ("实验：配置中", "info"),
        "starting": ("实验：启动中", "info"),
        "running": ("实验：运行中", "normal"),
        "monitoring": ("实验：运行中", "normal"),
        "stopping": ("实验：停止中", "warning"),
        "stopped": ("实验：已停止", "neutral"),
        "start_failed": ("实验：启动失败", "fault"),
        "stop_failed": ("实验：停止失败", "fault"),
        "failed": ("实验：操作失败", "fault"),
    }

    _HEALTH_STATE_LABELS = {
        "unknown": ("系统：待检测", "neutral"),
        "unavailable": ("系统：部分不可用", "neutral"),
        "normal": ("系统：正常", "normal"),
        "warning": ("系统：存在告警", "warning"),
        "fault": ("系统：存在故障", "fault"),
    }

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self._buttons: dict[int, QPushButton] = {}
        self._active_step = 1
        self._build_ui()
        self.set_active_step(1)
        self.set_experiment_state("not_started")
        self.set_health_summary("unknown", 0)

    def _build_ui(self) -> None:
        self.setObjectName("globalWorkflowShell")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            """
            QFrame#globalWorkflowShell {
                background: #edf6ff;
                border: 1px solid #cfe4f8;
                border-radius: 12px;
            }
            QLabel#workflowEyebrow {
                color: #156fc1;
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 1px;
            }
            QLabel#workflowTitle {
                color: #113a63;
                font-size: 16px;
                font-weight: 700;
            }
            QPushButton[workflow="true"] {
                background: #ffffff;
                border: 1px solid #d5e6f7;
                border-radius: 10px;
                color: #183d61;
                font-size: 13px;
                font-weight: 600;
                min-height: 46px;
                padding: 5px 10px;
                text-align: left;
            }
            QPushButton[workflow="true"]:hover {
                border-color: #70b5ed;
                background: #f7fbff;
            }
            QPushButton[workflowActive="true"] {
                background: #e8f4ff;
                border: 1px solid #268ee4;
                color: #075fae;
            }
            QLabel[statusPill="true"] {
                border-radius: 12px;
                padding: 4px 9px;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel[statusTone="neutral"] { background: #edf1f5; color: #56616d; }
            QLabel[statusTone="info"] { background: #e2f1ff; color: #156fc1; }
            QLabel[statusTone="normal"] { background: #e4f7ed; color: #178850; }
            QLabel[statusTone="warning"] { background: #fff4df; color: #b56800; }
            QLabel[statusTone="fault"] { background: #ffebec; color: #d33f49; }
            QPushButton#workflowAlertButton {
                border: 0;
                border-radius: 12px;
                padding: 4px 9px;
                font-size: 12px;
                font-weight: 700;
            }
            QFrame#identityBadge {
                background: #ffffff;
                border: 1px solid #d5e6f7;
                border-radius: 18px;
            }
            QLabel#identityAvatar {
                background: #58afea;
                color: #ffffff;
                border-radius: 15px;
                font-size: 15px;
                font-weight: 700;
            }
            QLabel#identityRole {
                color: #164d80;
                font-size: 12px;
                font-weight: 700;
            }
            QLabel#identityCaption {
                color: #7894af;
                font-size: 10px;
            }
            """
        )

        outer = QVBoxLayout(self)
        outer.setContentsMargins(12, 9, 12, 10)
        outer.setSpacing(7)

        top_row = QHBoxLayout()
        top_row.setContentsMargins(0, 0, 0, 0)
        brand = QFrame()
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 10, 0)
        brand_layout.setSpacing(7)
        logo = QLabel()
        logo.setPixmap(QPixmap("resource/logo.png").scaled(34, 34, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        brand_layout.addWidget(logo)
        brand_text = QVBoxLayout()
        brand_text.setSpacing(0)
        brand_name = QLabel("达燊科技")
        brand_name.setObjectName("workflowTitle")
        brand_subtitle = QLabel("DASHEN TECH · 动物箱平台")
        brand_subtitle.setObjectName("sectionHint")
        brand_text.addWidget(brand_name)
        brand_text.addWidget(brand_subtitle)
        brand_layout.addLayout(brand_text)
        top_row.addWidget(brand)
        eyebrow = QLabel("核心操作台 · WORKFLOW")
        eyebrow.setObjectName("workflowEyebrow")
        top_row.addWidget(eyebrow)
        top_row.addStretch(1)

        self.run_state_label = QLabel()
        self.run_state_label.setProperty("statusPill", True)
        top_row.addWidget(self.run_state_label)

        self.health_state_label = QLabel()
        self.health_state_label.setProperty("statusPill", True)
        top_row.addWidget(self.health_state_label)

        self.alert_button = QPushButton()
        self.alert_button.setObjectName("workflowAlertButton")
        self.alert_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.alert_button.clicked.connect(self.alert_requested.emit)
        top_row.addWidget(self.alert_button)

        identity_badge = QFrame()
        identity_badge.setObjectName("identityBadge")
        identity_layout = QHBoxLayout(identity_badge)
        identity_layout.setContentsMargins(5, 3, 10, 3)
        identity_layout.setSpacing(6)
        avatar = QLabel("人")
        avatar.setObjectName("identityAvatar")
        avatar.setAlignment(Qt.AlignmentFlag.AlignCenter)
        avatar.setFixedSize(30, 30)
        identity_layout.addWidget(avatar)
        identity_text = QVBoxLayout()
        identity_text.setContentsMargins(0, 0, 0, 0)
        identity_text.setSpacing(0)
        role_label = QLabel()
        role_label.setObjectName("identityRole")
        caption_label = QLabel("当前身份")
        caption_label.setObjectName("identityCaption")
        identity_text.addWidget(role_label)
        identity_text.addWidget(caption_label)
        identity_layout.addLayout(identity_text)
        top_row.addWidget(identity_badge)
        self.identity_badge = identity_badge
        self.identity_avatar = avatar
        self.identity_role_label = role_label
        self.set_identity(global_setting.get_setting("user_role", "用户"))
        outer.addLayout(top_row)

        navigation = QHBoxLayout()
        navigation.setContentsMargins(0, 0, 0, 0)
        navigation.setSpacing(8)
        for step, title, subtitle in self.STEPS:
            # The six cards are already ordered; repeating the step number in
            # each card adds visual noise and consumes the title's left space.
            button = QPushButton(f"{title}\n{subtitle}")
            button.setProperty("workflow", True)
            button.setProperty("workflowActive", False)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setMinimumWidth(132)
            button.clicked.connect(lambda checked=False, requested=step: self.workflow_requested.emit(requested))
            navigation.addWidget(button, 1)
            self._buttons[step] = button
        outer.addLayout(navigation)

    @staticmethod
    def _normalise_state(value: object) -> str:
        raw = getattr(value, "value", value)
        return str(raw or "unknown").strip().lower()

    @staticmethod
    def _apply_tone(widget: QWidget, tone: str) -> None:
        widget.setProperty("statusTone", tone)
        style = widget.style()
        style.unpolish(widget)
        style.polish(widget)
        widget.update()

    def set_active_step(self, step: int) -> None:
        self._active_step = step
        for item_step, button in self._buttons.items():
            button.setProperty("workflowActive", item_step == step)
            style = button.style()
            style.unpolish(button)
            style.polish(button)
            button.update()

    def set_experiment_state(self, state: object) -> None:
        key = self._normalise_state(state)
        text, tone = self._RUN_STATE_LABELS.get(key, ("实验：状态未知", "neutral"))
        self.run_state_label.setText(text)
        self._apply_tone(self.run_state_label, tone)

    def set_identity(self, role: object) -> None:
        """Update the persistent identity badge without changing permissions."""
        value = str(role or "用户").strip() or "用户"
        self.identity_role_label.setText(value)
        self.identity_avatar.setText("管" if value == "管理员" else "人")
        self.identity_badge.setToolTip(f"当前身份：{value}（用户与管理员暂时使用相同功能）")

    def set_health_summary(self, state: object, active_alerts: int = 0) -> None:
        key = self._normalise_state(state)
        text, tone = self._HEALTH_STATE_LABELS.get(key, ("系统：待检测", "neutral"))
        self.health_state_label.setText(text)
        self._apply_tone(self.health_state_label, tone)

        active_alerts = max(int(active_alerts or 0), 0)
        if active_alerts:
            self.alert_button.setText(f"需处理 {active_alerts} 项")
            alert_tone = "fault" if key == "fault" else "warning"
            self.alert_button.setStyleSheet(
                "background: #ffebec; color: #d33f49;" if alert_tone == "fault"
                else "background: #fff4df; color: #b56800;"
            )
            self.alert_button.show()
        else:
            if key == "normal":
                self.alert_button.setText("无待处理异常")
                self.alert_button.setStyleSheet("background: #e4f7ed; color: #178850;")
            elif key == "unknown":
                # “系统：待检测”已经由健康状态标签表达；异常摘要在没有
                # 真实告警时隐藏，避免顶栏出现两个重复的“待检测”。
                self.alert_button.hide()
                return
            else:
                self.alert_button.setText("状态待确认")
                self.alert_button.setStyleSheet("background: #edf1f5; color: #56616d;")
            self.alert_button.show()
