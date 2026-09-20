"""启动前程序自检与身份选择窗口。

身份目前只用于界面标识，用户和管理员共用同一套功能与权限。窗口仍然
通过 ``QDialog.Accepted`` 作为进入主程序的唯一信号，避免改变旧启动流程。
"""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QButtonGroup,
    QDialog,
    QDialogButtonBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from public.config_class.global_setting import global_setting
from theme.ThemeQt6 import ThemedDialog


class Program_self_check_index(ThemedDialog):
    """新版启动检查页面。

    这里不伪造设备检查结果：设备状态由进入主窗口后的健康快照通道判定，
    自检页只负责让操作者确认身份并进入上位机。
    """

    def __init__(self):
        self.selected_role = None
        self.role_group: QButtonGroup | None = None
        self.role_buttons: dict[str, QPushButton] = {}
        self.buttonBox: QDialogButtonBox | None = None
        super().__init__()

    def _init_ui(self):
        self.setObjectName("Program_self_check_index")
        self.setWindowTitle("程序自检")
        self.setModal(True)
        self.setMinimumSize(620, 500)
        self.resize(760, 570)

        root = QVBoxLayout(self)
        root.setContentsMargins(28, 24, 28, 22)
        root.setSpacing(16)

        heading = QHBoxLayout()
        heading.setSpacing(12)
        icon = QLabel("✓")
        icon.setObjectName("selfCheckIcon")
        icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon.setFixedSize(42, 42)
        heading.addWidget(icon)

        title_box = QVBoxLayout()
        title_box.setSpacing(2)
        title = QLabel("程序自检")
        title.setObjectName("selfCheckTitle")
        subtitle = QLabel("进入上位机前，请选择当前操作身份")
        subtitle.setObjectName("selfCheckSubtitle")
        title_box.addWidget(title)
        title_box.addWidget(subtitle)
        heading.addLayout(title_box)
        heading.addStretch(1)
        root.addLayout(heading)

        notice = QFrame()
        notice.setObjectName("selfCheckNotice")
        notice_layout = QVBoxLayout(notice)
        notice_layout.setContentsMargins(14, 11, 14, 11)
        notice_layout.setSpacing(4)
        notice_title = QLabel("启动检查")
        notice_title.setObjectName("selfCheckSectionTitle")
        notice_text = QLabel(
            "程序将继续启动主界面。相机、串口和采集服务的真实状态会在主界面中通过健康快照显示。"
        )
        notice_text.setWordWrap(True)
        notice_text.setObjectName("selfCheckHint")
        notice_layout.addWidget(notice_title)
        notice_layout.addWidget(notice_text)
        root.addWidget(notice)

        section_title = QLabel("选择身份")
        section_title.setObjectName("selfCheckSectionTitle")
        root.addWidget(section_title)

        role_row = QHBoxLayout()
        role_row.setSpacing(12)
        self.role_group = QButtonGroup(self)
        self.role_group.setExclusive(True)
        for role, description in (
            ("用户", "日常实验操作"),
            ("管理员", "系统配置与维护"),
        ):
            button = QPushButton()
            button.setObjectName("identityOption")
            button.setCheckable(True)
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setProperty("role", role)
            button.setText(f"{role}\n{description}")
            button.setMinimumHeight(78)
            button.setToolTip(f"以{role}身份进入上位机")
            self.role_group.addButton(button)
            self.role_buttons[role] = button
            role_row.addWidget(button, 1)
        # 用户是当前默认身份；管理员与用户暂时使用相同功能权限。
        self.role_buttons["用户"].setChecked(True)
        root.addLayout(role_row)

        detail = QFrame()
        detail.setObjectName("selfCheckDetail")
        detail_layout = QGridLayout(detail)
        detail_layout.setContentsMargins(14, 12, 14, 12)
        detail_layout.setHorizontalSpacing(22)
        detail_layout.setVerticalSpacing(8)
        checks = (
            ("主程序", "可启动"),
            ("配置读取", "待进入主界面确认"),
            ("设备状态", "由实时健康快照判断"),
            ("数据服务", "由采集服务心跳判断"),
        )
        for index, (name, value) in enumerate(checks):
            name_label = QLabel(name)
            name_label.setObjectName("selfCheckItemName")
            value_label = QLabel(value)
            value_label.setObjectName("selfCheckItemValue")
            detail_layout.addWidget(name_label, index // 2, (index % 2) * 2)
            detail_layout.addWidget(value_label, index // 2, (index % 2) * 2 + 1)
        root.addWidget(detail)
        root.addStretch(1)

        self.buttonBox = QDialogButtonBox(self)
        self.buttonBox.setOrientation(Qt.Orientation.Horizontal)
        self.enter_button = self.buttonBox.addButton(
            "进入上位机", QDialogButtonBox.ButtonRole.AcceptRole
        )
        self.cancel_button = self.buttonBox.addButton(
            "退出程序", QDialogButtonBox.ButtonRole.RejectRole
        )
        self.enter_button.setObjectName("primaryAction")
        self.cancel_button.setObjectName("secondaryAction")
        self.enter_button.setDefault(True)
        self.buttonBox.accepted.connect(self._accept_with_role)
        self.buttonBox.rejected.connect(self.reject)
        root.addWidget(self.buttonBox)

    def _init_customize_ui(self):
        # BaseDialog 默认会为旧教程插入 help 按钮；新版自检页不再显示旧教程控件。
        self._self_check_style = """
            QDialog#Program_self_check_index {
                background: #f4f8fc;
            }
            QLabel#selfCheckIcon {
                background: #238bd7;
                color: #ffffff;
                border-radius: 21px;
                font-size: 22px;
                font-weight: 700;
            }
            QLabel#selfCheckTitle {
                color: #123d68;
                font-size: 22px;
                font-weight: 700;
            }
            QLabel#selfCheckSubtitle, QLabel#selfCheckHint {
                color: #6b8aaa;
                font-size: 12px;
            }
            QLabel#selfCheckSectionTitle {
                color: #125f9d;
                font-size: 14px;
                font-weight: 700;
            }
            QFrame#selfCheckNotice, QFrame#selfCheckDetail {
                background: #ffffff;
                border: 1px solid #d7e7f6;
                border-radius: 10px;
            }
            QPushButton#identityOption {
                background: #ffffff;
                color: #214b73;
                border: 1px solid #cfe1f3;
                border-radius: 10px;
                font-size: 14px;
                font-weight: 700;
                text-align: left;
                padding: 10px 16px;
            }
            QPushButton#identityOption:hover {
                border-color: #58a9e8;
                background: #f3f9ff;
            }
            QPushButton#identityOption:checked {
                color: #075fae;
                border: 2px solid #238bd7;
                background: #e8f4ff;
            }
            QLabel#selfCheckItemName { color: #6685a3; font-size: 12px; }
            QLabel#selfCheckItemValue { color: #1d78bc; font-size: 12px; font-weight: 600; }
            QPushButton#primaryAction {
                min-width: 126px; min-height: 36px; border: 0; border-radius: 8px;
                background: #238bd7; color: white; font-weight: 700;
            }
            QPushButton#primaryAction:hover { background: #1978c0; }
            QPushButton#secondaryAction {
                min-width: 92px; min-height: 36px; border: 1px solid #c7dced;
                border-radius: 8px; background: #ffffff; color: #4e6d8b;
            }
            QPushButton#secondaryAction:hover { background: #f0f6fb; }
            """
        self.setStyleSheet(self.styleSheet() + self._self_check_style)

    def _init_style_sheet(self):
        """Apply the application theme and then keep the self-check skin."""
        ThemedDialog._init_style_sheet(self)
        extra = getattr(self, "_self_check_style", "")
        if extra:
            self.setStyleSheet(self.styleSheet() + extra)

    def _init_function(self):
        pass

    def setup_tutorial(self):
        # 新版自检页不启动旧的箭头教程，避免出现“help”按钮和空白引导层。
        self.tutorial = None

    def _accept_with_role(self):
        button = self.role_group.checkedButton() if self.role_group else None
        role = button.property("role") if button is not None else None
        if not role:
            return
        self.selected_role = str(role)
        # 两种身份暂时完全同权；该值只作为顶栏身份展示和后续权限扩展的入口。
        global_setting.set_setting("user_role", self.selected_role)
        global_setting.set_setting("current_identity", self.selected_role)
        self.accept()
