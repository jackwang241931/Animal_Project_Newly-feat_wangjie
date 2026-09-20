import copy
import os
from datetime import datetime

from loguru import logger
from PyQt6.QtCore import QDateTime, QSize, QTime, QTimer, Qt
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDateTimeEdit,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QStyle,
    QTableWidget,
    QTableWidgetItem,
    QTimeEdit,
    QVBoxLayout,
    QWidget,
)

from public.config_class.global_setting import global_setting
from public.dao.SQLite.Experiment_Setting_DAO_Handle import Experiment_Setting_DAO_Handle
from public.entity.enum.Public_Enum import AppState
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.util.custom_data_file_util import custom_template_file_util
from public.util.lighting_schedule import (
    DAILY_MODE,
    STAGE_MODE,
    default_lighting_schedule,
    next_lighting_change,
    normalize_lighting_schedule,
    resolve_lighting_state,
    validate_lighting_schedule,
)
from public.util.time_util import time_util


class LightingScheduleDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("光照时间表")
        self.setMinimumSize(920, 620)
        self._loading = False
        self._row_token_counter = 0
        self._build_ui()
        self._load_current_schedule()

    def _build_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(18, 18, 18, 18)
        main_layout.setSpacing(12)

        title = QLabel("实验光照时间表")
        title_font = title.font()
        title_font.setPointSize(15)
        title_font.setBold(True)
        title.setFont(title_font)
        main_layout.addWidget(title)

        description = QLabel(
            "按系统实际时间控制本次实验已启用的鼠笼。每日定时适合 07:00 开灯、19:00 关灯；"
            "阶段模式适合按持续时间执行多段光照。"
        )
        description.setWordWrap(True)
        main_layout.addWidget(description)

        settings_box = QGroupBox("计划设置")
        settings_layout = QFormLayout(settings_box)
        settings_layout.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        self.enabled_check = QCheckBox("启用实验定时光照")
        settings_layout.addRow("状态", self.enabled_check)

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("每日定时", DAILY_MODE)
        self.mode_combo.addItem("阶段实验", STAGE_MODE)
        settings_layout.addRow("运行方式", self.mode_combo)

        self.start_datetime = QDateTimeEdit()
        self.start_datetime.setCalendarPopup(True)
        self.start_datetime.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.start_datetime.setDateTime(QDateTime.currentDateTime())
        settings_layout.addRow("阶段开始时间", self.start_datetime)

        option_row = QWidget()
        option_layout = QHBoxLayout(option_row)
        option_layout.setContentsMargins(0, 0, 0, 0)
        self.repeat_check = QCheckBox("循环执行")
        self.transition_spin = QSpinBox()
        self.transition_spin.setRange(0, 60)
        self.transition_spin.setSuffix(" 分钟")
        option_layout.addWidget(self.repeat_check)
        option_layout.addSpacing(24)
        option_layout.addWidget(QLabel("渐变过渡"))
        option_layout.addWidget(self.transition_spin)
        option_layout.addStretch()
        settings_layout.addRow("执行参数", option_row)
        main_layout.addWidget(settings_box)

        table_header = QHBoxLayout()
        self.table_hint = QLabel()
        table_header.addWidget(self.table_hint)
        table_header.addStretch()
        self.add_button = QPushButton("添加时段")
        self.add_button.setToolTip("在时间表末尾添加一个光照时段")
        self.reset_button = QPushButton("恢复昼夜默认值")
        table_header.addWidget(self.add_button)
        table_header.addWidget(self.reset_button)
        main_layout.addLayout(table_header)

        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(
            ["序号", "执行时间", "状态", "色温档位", "亮度档位", "持续时间（分钟）", "操作"]
        )
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(5, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(6, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(6, 92)
        main_layout.addWidget(self.table, 1)

        self.preview_label = QLabel()
        self.preview_label.setWordWrap(True)
        main_layout.addWidget(self.preview_label)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存设置")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("关闭")
        main_layout.addWidget(buttons)

        self.mode_combo.currentIndexChanged.connect(self._update_mode_ui)
        self.enabled_check.toggled.connect(self._update_enabled_ui)
        self.start_datetime.dateTimeChanged.connect(self._update_preview)
        self.repeat_check.toggled.connect(self._update_preview)
        self.transition_spin.valueChanged.connect(self._update_preview)
        self.add_button.clicked.connect(self._add_default_row)
        self.reset_button.clicked.connect(self._load_default_rows)
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)

    def _load_current_schedule(self):
        entity = self._get_experiment_entity()
        schedule = normalize_lighting_schedule(
            getattr(entity, "lighting_schedule", None) if entity is not None else None
        )
        self._apply_schedule(schedule)

    def _apply_schedule(self, schedule):
        self._loading = True
        try:
            self.enabled_check.setChecked(schedule["enabled"])
            mode_index = self.mode_combo.findData(schedule["mode"])
            self.mode_combo.setCurrentIndex(max(0, mode_index))
            self.repeat_check.setChecked(schedule["repeat"])
            self.transition_spin.setValue(schedule["transition_minutes"])
            start_text = schedule.get("start_at")
            if start_text:
                parsed = QDateTime.fromString(start_text, Qt.DateFormat.ISODate)
                if parsed.isValid():
                    self.start_datetime.setDateTime(parsed)
            self.table.setRowCount(0)
            for stage in schedule["stages"]:
                self._append_stage_row(stage)
        finally:
            self._loading = False
        self._update_mode_ui()
        self._update_enabled_ui()
        self._update_preview()

    def _load_default_rows(self):
        default = default_lighting_schedule()
        self.mode_combo.setCurrentIndex(self.mode_combo.findData(DAILY_MODE))
        self.repeat_check.setChecked(True)
        self.table.setRowCount(0)
        for stage in default["stages"]:
            self._append_stage_row(stage)
        self.transition_spin.setValue(default["transition_minutes"])
        self._update_preview()

    def _add_default_row(self):
        row = self.table.rowCount()
        hour = (7 + row * 6) % 24
        self._append_stage_row({
            "time": f"{hour:02d}:00",
            "power": True,
            "color_temperature": 7,
            "brightness": 7,
            "duration_minutes": 60,
        })
        self._update_mode_ui()

    def _append_stage_row(self, stage):
        row = self.table.rowCount()
        self.table.insertRow(row)
        number_item = QTableWidgetItem(str(row + 1))
        number_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        number_item.setFlags(number_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
        self.table.setItem(row, 0, number_item)

        time_edit = QTimeEdit()
        time_edit.setDisplayFormat("HH:mm")
        time_edit.setTime(QTime.fromString(stage.get("time", "07:00"), "HH:mm"))
        self.table.setCellWidget(row, 1, time_edit)

        power_combo = QComboBox()
        power_combo.addItem("开灯", True)
        power_combo.addItem("关灯", False)
        power_combo.setCurrentIndex(0 if stage.get("power", False) else 1)
        self.table.setCellWidget(row, 2, power_combo)

        color_spin = QSpinBox()
        color_spin.setRange(1, 9)
        color_spin.setValue(int(stage.get("color_temperature", 7)))
        self.table.setCellWidget(row, 3, color_spin)

        brightness_spin = QSpinBox()
        brightness_spin.setRange(1, 9)
        brightness_spin.setValue(max(1, int(stage.get("brightness", 7) or 1)))
        self.table.setCellWidget(row, 4, brightness_spin)

        duration_spin = QSpinBox()
        duration_spin.setRange(1, 10080)
        duration_spin.setValue(int(stage.get("duration_minutes", 60)))
        duration_spin.setSuffix(" 分钟")
        self.table.setCellWidget(row, 5, duration_spin)

        delete_button = QPushButton("删除")
        delete_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon)
        )
        delete_button.setIconSize(QSize(16, 16))
        delete_button.setMinimumSize(76, 32)
        delete_button.setStyleSheet("""
            QPushButton {
                color: #b42318;
                background-color: #ffffff;
                border: 1px solid #d0d5dd;
                padding: 4px 10px;
            }
            QPushButton:hover {
                color: #ffffff;
                background-color: #d92d20;
                border-color: #d92d20;
            }
            QPushButton:pressed {
                background-color: #b42318;
            }
            QPushButton:disabled {
                color: #667085;
                background-color: #f2f4f7;
                border-color: #d0d5dd;
            }
        """)
        delete_button.setToolTip("删除这个光照时段")
        self._row_token_counter += 1
        row_token = self._row_token_counter
        delete_button.setProperty("lighting_schedule_row_token", row_token)
        delete_button.clicked.connect(
            lambda _checked=False, token=row_token: QTimer.singleShot(
                0, lambda target_token=token: self._remove_row(target_token)
            )
        )
        self.table.setCellWidget(row, 6, delete_button)

        power_combo.currentIndexChanged.connect(
            lambda _index, combo=power_combo, color=color_spin, brightness=brightness_spin:
            self._sync_power_controls(combo, color, brightness)
        )
        power_combo.currentIndexChanged.connect(self._update_preview)
        time_edit.timeChanged.connect(self._update_preview)
        color_spin.valueChanged.connect(self._update_preview)
        brightness_spin.valueChanged.connect(self._update_preview)
        duration_spin.valueChanged.connect(self._update_preview)
        self._sync_power_controls(power_combo, color_spin, brightness_spin)

    def _remove_row(self, row_token):
        row = next(
            (
                candidate
                for candidate in range(self.table.rowCount())
                if self.table.cellWidget(candidate, 6) is not None
                and self.table.cellWidget(candidate, 6).property(
                    "lighting_schedule_row_token"
                ) == row_token
            ),
            -1,
        )
        if row < 0:
            return

        remaining_stages = [
            self._stage_from_row(candidate)
            for candidate in range(self.table.rowCount())
            if candidate != row
        ]

        self._loading = True
        self.table.setUpdatesEnabled(False)
        try:
            # QTableWidget releases cell widgets with deleteLater(). Detach and
            # hide them first so the old row cannot remain as a disabled ghost.
            self._detach_all_row_widgets()
            self.table.clearContents()
            self.table.setRowCount(0)
        finally:
            self.table.setUpdatesEnabled(True)
            self._loading = False
        self.table.clearSelection()
        self.table.doItemsLayout()
        self.table.viewport().repaint()
        QTimer.singleShot(
            0,
            lambda stages=remaining_stages: self._rebuild_stage_rows(stages),
        )

    def _detach_all_row_widgets(self):
        for row in range(self.table.rowCount()):
            for column in range(1, self.table.columnCount()):
                widget = self.table.cellWidget(row, column)
                if widget is None:
                    continue
                widget.hide()
                self.table.removeCellWidget(row, column)
                widget.setParent(None)
                widget.deleteLater()

    def _rebuild_stage_rows(self, stages):
        self._loading = True
        self.table.setUpdatesEnabled(False)
        try:
            for stage in stages:
                self._append_stage_row(stage)
        finally:
            self.table.setUpdatesEnabled(True)
            self._loading = False
        self.table.doItemsLayout()
        self.table.viewport().repaint()
        self._update_mode_ui()

    def _renumber_rows(self):
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setText(str(row + 1))

    @staticmethod
    def _sync_power_controls(power_combo, color_spin, brightness_spin):
        is_on = bool(power_combo.currentData())
        color_spin.setEnabled(is_on)
        brightness_spin.setEnabled(is_on)

    def _update_mode_ui(self):
        is_daily = self.mode_combo.currentData() == DAILY_MODE
        self.start_datetime.setEnabled(not is_daily)
        self.repeat_check.setEnabled(not is_daily)
        if is_daily:
            self.repeat_check.setChecked(True)
            self.table_hint.setText("每天在指定钟点切换灯光状态，跨天自动循环。")
        else:
            self.table_hint.setText("从阶段开始时间起，按每行持续时间依次执行。")
        for row in range(self.table.rowCount()):
            self.table.cellWidget(row, 1).setEnabled(is_daily)
            self.table.cellWidget(row, 5).setEnabled(not is_daily)
        self._update_preview()

    def _update_enabled_ui(self):
        enabled = self.enabled_check.isChecked()
        for widget in (
            self.mode_combo,
            self.start_datetime,
            self.repeat_check,
            self.transition_spin,
            self.table,
            self.add_button,
            self.reset_button,
        ):
            widget.setEnabled(enabled)
        if enabled:
            self._update_mode_ui()
        self._update_preview()

    def _collect_schedule(self):
        stages = [
            self._stage_from_row(row)
            for row in range(self.table.rowCount())
        ]
        return {
            "version": 1,
            "enabled": self.enabled_check.isChecked(),
            "mode": self.mode_combo.currentData(),
            "transition_minutes": self.transition_spin.value(),
            "repeat": self.repeat_check.isChecked(),
            "start_at": self.start_datetime.dateTime().toPyDateTime().isoformat(timespec="seconds"),
            "stages": stages,
        }

    def _stage_from_row(self, row):
        time_edit = self.table.cellWidget(row, 1)
        power_combo = self.table.cellWidget(row, 2)
        color_spin = self.table.cellWidget(row, 3)
        brightness_spin = self.table.cellWidget(row, 4)
        duration_spin = self.table.cellWidget(row, 5)
        power = bool(power_combo.currentData())
        return {
            "time": time_edit.time().toString("HH:mm"),
            "power": power,
            "color_temperature": color_spin.value(),
            "brightness": brightness_spin.value() if power else 0,
            "duration_minutes": duration_spin.value(),
        }

    def _update_preview(self, *_args):
        if self._loading:
            return
        try:
            schedule = normalize_lighting_schedule(self._collect_schedule())
            if not schedule["enabled"]:
                self.preview_label.setText("定时光照未启用，实验期间保持手动控制。")
                return
            state = resolve_lighting_state(schedule, datetime.now())
            next_change = next_lighting_change(schedule, datetime.now())
            if state is None:
                self.preview_label.setText("计划尚未开始。")
                return
            state_text = (
                f"开灯，色温 {state['color_temperature']}，亮度 {state['brightness']}"
                if state["power"] else "关灯"
            )
            next_text = next_change.strftime("%Y-%m-%d %H:%M:%S") if next_change else "无"
            self.preview_label.setText(f"按当前时间预览：{state_text}；下一次切换：{next_text}")
        except Exception as e:
            self.preview_label.setText(f"当前设置需要调整：{e}")

    def _save(self):
        entity = self._get_experiment_entity()
        if entity is None:
            QMessageBox.warning(self, "无法保存", "请先创建或应用一个实验模板。")
            return
        try:
            schedule = validate_lighting_schedule(self._collect_schedule())
        except ValueError as e:
            QMessageBox.warning(self, "设置有误", str(e))
            return

        if global_setting.get_setting("app_state", AppState.INITIALIZED) == AppState.MONITORING:
            reply = QMessageBox.question(
                self,
                "实验正在运行",
                "保存后将立即按当前时间重新计算并应用灯光状态，确定继续吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return

        entity.lighting_schedule = copy.deepcopy(schedule)
        global_setting.set_setting("lighting_schedule", copy.deepcopy(schedule))
        for key in ("experiment_setting", "experiment_setting_new"):
            current = global_setting.get_setting(key, None)
            if current is not None:
                current.lighting_schedule = copy.deepcopy(schedule)
                global_setting.set_setting(key, current)

        template_path = (
            global_setting.get_setting("experiment_setting_file", "")
            or global_setting.get_setting("experiment_setting_file_open", "")
        )
        if template_path:
            try:
                self._persist_to_template(template_path, schedule)
            except Exception as e:
                logger.exception("保存光照时间表到实验模板失败")
                QMessageBox.warning(
                    self,
                    "部分保存失败",
                    f"当前运行设置已保存，但写入实验模板失败：\n{e}",
                )
                return

        send_queue = global_setting.get_setting("send_message_queue", None)
        if send_queue is not None:
            send_queue.put(ObjectQueueItem(
                origin="Main_lighting_schedule",
                to="main_monitor_data",
                title="lighting_schedule",
                data=copy.deepcopy(schedule),
                time=time_util.get_format_from_time(datetime.now().timestamp()),
            ))

        QMessageBox.information(self, "保存成功", "光照时间表已保存。")
        self.accept()

    @staticmethod
    def _persist_to_template(template_path, schedule):
        template_path = os.path.abspath(template_path)
        if not os.path.isfile(template_path):
            raise FileNotFoundError(template_path)
        db_path = custom_template_file_util.load_template_contents_from_custom_file(template_path)
        try:
            handle = Experiment_Setting_DAO_Handle(
                db_fold_path=os.path.dirname(db_path),
                db_name=os.path.basename(db_path),
            )
            try:
                handle.save_lighting_schedule(schedule)
            finally:
                handle.stop()
            custom_template_file_util.save_template_contents_as_custom_file(db_path)
        finally:
            if os.path.isfile(db_path):
                os.remove(db_path)

    @staticmethod
    def _get_experiment_entity():
        return (
            global_setting.get_setting("experiment_setting", None)
            or global_setting.get_setting("experiment_setting_new", None)
        )
