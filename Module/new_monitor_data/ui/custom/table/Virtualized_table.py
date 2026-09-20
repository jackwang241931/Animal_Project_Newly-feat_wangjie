from datetime import datetime

from PyQt6.QtCore import (
    QAbstractTableModel,
    QEvent,
    QModelIndex,
    QItemSelectionModel,
    QTimer,
    Qt,
)
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QHeaderView,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from Module.new_monitor_data.ui.custom.table.TableCellDetailDialog import CellDetailDialog
from public.config_class.global_setting import global_setting
from public.function.weight.running_wheel import (
    RUNNING_WHEEL_COLUMN_KEYS,
    format_running_wheel_distance,
)
from public.function.weight.weight_series_formatter import format_weight_series


# CO2 对外展示顺序只作用于监控界面，数据库中的原始列顺序不变。
CO2_DISPLAY_COLUMN_ORDER = (
    "UGC_flow_num_1",
    "UGC_CO2_num",
    "UGC_air_pressure",
)
CO2_DISPLAY_COLUMN_TITLES = {
    "UGC_flow_num_1": "传感器状态码",
    "UGC_CO2_num": "气压补偿后CO2",
    "UGC_air_pressure": "对齐后CO2",
}
RUNNING_WHEEL_DISPLAY_COLUMN_TITLES = {
    "running_wheel_num": "当前计量周期内跑轮距离测量值(m)",
    "ENM_running_wheel_num": "当前计量周期内跑轮距离测量值(m)",
}
CO2_HIDDEN_COLUMN_KEYS = {"UGC_CO2_origin_num"}


def _format_sensor_status_code(value):
    if value is None:
        return "None"
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, (int, float)):
        if value == 1:
            return "1"
        if value == 0:
            return "0"

    normalized = str(value).strip().lower()
    if normalized in {"1", "正常", "ok", "normal", "true"}:
        return "1"
    if normalized in {"0", "故障", "错误", "fault", "error", "false"}:
        return "0"
    return str(value)


def _format_co2_display_value(value):
    if value is None:
        return "None"
    try:
        return f"{float(value):.04f}"
    except (TypeError, ValueError):
        return str(value)


def prepare_co2_display_result(columns, titles, rows):
    """Prepare the CO2 columns for display without mutating the raw query result."""
    current_columns = list(columns or [])
    current_titles = list(titles or [])
    if not current_columns:
        return current_columns, current_titles, list(rows or [])

    target_columns = [
        column for column in CO2_DISPLAY_COLUMN_ORDER if column in current_columns
    ]
    has_running_wheel = any(
        column in RUNNING_WHEEL_COLUMN_KEYS for column in current_columns
    )
    if not target_columns and not any(
        column in current_columns for column in CO2_HIDDEN_COLUMN_KEYS
    ) and not has_running_wheel:
        return current_columns, current_titles or current_columns, list(rows or [])

    title_by_column = {
        column: current_titles[index] if index < len(current_titles) else column
        for index, column in enumerate(current_columns)
    }
    visible_other_columns = [
        column
        for column in current_columns
        if column not in CO2_HIDDEN_COLUMN_KEYS
        and column not in CO2_DISPLAY_COLUMN_ORDER
    ]

    target_positions = [
        index
        for index, column in enumerate(current_columns)
        if column in CO2_DISPLAY_COLUMN_ORDER
    ]
    first_target_index = min(target_positions, default=len(current_columns))
    insert_at = sum(
        1
        for column in current_columns[:first_target_index]
        if column not in CO2_HIDDEN_COLUMN_KEYS
        and column not in CO2_DISPLAY_COLUMN_ORDER
    )
    ordered_columns = (
        visible_other_columns[:insert_at]
        + target_columns
        + visible_other_columns[insert_at:]
    )
    ordered_titles = [
        RUNNING_WHEEL_DISPLAY_COLUMN_TITLES.get(
            column,
            CO2_DISPLAY_COLUMN_TITLES.get(column, title_by_column.get(column, column)),
        )
        for column in ordered_columns
    ]

    # Keep every field in each row so cell-detail dialogs and remarks remain intact.
    display_rows = [dict(row) for row in (rows or [])]
    return ordered_columns, ordered_titles, display_rows


class EpochTableModel(QAbstractTableModel):
    """A lightweight model that renders only cells visible in the viewport."""

    def __init__(self, parent=None, highlight_remarks=True):
        super().__init__(parent)
        self.highlight_remarks = highlight_remarks
        self._columns = []
        self._titles = []
        self._rows = []
        self._highlighted_rows = set()

    def set_result(self, columns, titles, rows):
        self.beginResetModel()
        self._columns = list(columns or [])
        self._titles = list(titles or self._columns)
        self._rows = list(rows or [])
        self._highlighted_rows = set()
        if self.highlight_remarks:
            self._highlighted_rows = {
                index
                for index, row in enumerate(self._rows)
                if row.get("remarks") is not None
                and len(str(row.get("remarks")).strip()) > 3
            }
        self.endResetModel()

    def rowCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._rows)

    def columnCount(self, parent=QModelIndex()):
        return 0 if parent.isValid() else len(self._columns)

    def headerData(self, section, orientation, role=Qt.ItemDataRole.DisplayRole):
        if role == Qt.ItemDataRole.DisplayRole:
            if orientation == Qt.Orientation.Horizontal and section < len(self._titles):
                return self._titles[section]
            if orientation == Qt.Orientation.Vertical:
                return section + 1
        return None

    def _display_value(self, row, column):
        key = self._columns[column]
        value = self._rows[row].get(key)
        if key in RUNNING_WHEEL_COLUMN_KEYS:
            return format_running_wheel_distance(value)
        if key == "WM_weight_num":
            resolved = self._rows[row].get('_weight_resolved_points', 30)
            return format_weight_series(value, length=resolved)
        if key == "mouse_cage_number" and value is not None:
            configer = global_setting.get_setting("configer", {}) or {}
            reference_cage = int(configer.get("mouse_cage", {}).get("reference", -1))
            try:
                if int(value) == reference_cage:
                    return "参考笼"
            except (TypeError, ValueError):
                pass
        if key == "UGC_flow_num_1":
            return _format_sensor_status_code(value)
        if key in {"UGC_CO2_num", "UGC_air_pressure"}:
            return _format_co2_display_value(value)
        if ("oxygen" in key or "CO2" in key) and value is not None and not isinstance(value, str):
            try:
                return f"{value:.04f}"
            except (TypeError, ValueError):
                pass
        return str(value)

    def data(self, index, role=Qt.ItemDataRole.DisplayRole):
        if not index.isValid():
            return None

        if role == Qt.ItemDataRole.DisplayRole:
            return self._display_value(index.row(), index.column())
        if role == Qt.ItemDataRole.ToolTipRole:
            row = self._rows[index.row()]
            if self._columns[index.column()] == 'WM_weight_num' and '_weight_start_time' in row:
                start = datetime.fromtimestamp(row['_weight_start_time']).strftime('%H:%M:%S')
                end = datetime.fromtimestamp(row['_weight_end_time']).strftime('%H:%M:%S')
                return (
                    f"称重窗口：{start}~{end}，已确认{row['_weight_resolved_points']}/30点\n"
                    f"{self._display_value(index.row(), index.column())}"
                )
            text = self._display_value(index.row(), index.column())
            return text if len(text) > 10 else None
        if role == Qt.ItemDataRole.ForegroundRole and index.row() in self._highlighted_rows:
            return QColor(255, 0, 0)
        if role == Qt.ItemDataRole.TextAlignmentRole:
            return Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft
        return None

    def cell_text(self, row, column):
        if 0 <= row < len(self._rows) and 0 <= column < len(self._columns):
            return self._display_value(row, column)
        return ""

    def column_title(self, column):
        if 0 <= column < len(self._titles):
            return self._titles[column]
        return f"列{column + 1}"


class VirtualizedFrozenTable(QWidget):
    """Virtualized table with frozen columns overlaid like the original widget."""

    MIN_COLUMN_WIDTH = 38
    MAX_COLUMN_WIDTH = 280

    def __init__(self, parent=None, highlight_remarks=True):
        super().__init__(parent)
        self.model = EpochTableModel(self, highlight_remarks=highlight_remarks)
        self.left_frozen_headers = []
        self.right_frozen_headers = []
        self._left_indices = set()
        self._right_indices = set()

        self.main_view = self._create_view()
        self.left_view = self._create_frozen_view(self.main_view)
        self.right_view = self._create_frozen_view(self.main_view)

        selection_model = QItemSelectionModel(self.model, self)
        for view in (self.main_view, self.left_view, self.right_view):
            view.setModel(self.model)
            view.setSelectionModel(selection_model)
            view.clicked.connect(self._show_cell_detail)

        self.main_view.verticalScrollBar().valueChanged.connect(self._sync_vertical_scroll)
        self.left_view.verticalScrollBar().valueChanged.connect(
            self.main_view.verticalScrollBar().setValue
        )
        self.right_view.verticalScrollBar().valueChanged.connect(
            self.main_view.verticalScrollBar().setValue
        )
        self.main_view.horizontalScrollBar().valueChanged.connect(
            lambda _value: self._schedule_frozen_geometry_update()
        )
        self.main_view.horizontalHeader().sectionResized.connect(
            self._on_main_column_resized
        )
        self.main_view.verticalHeader().sectionResized.connect(self._sync_row_height)
        self.left_view.horizontalHeader().sectionResized.connect(
            self._on_frozen_column_resized
        )
        self.right_view.horizontalHeader().sectionResized.connect(
            self._on_frozen_column_resized
        )
        self.main_view.viewport().installEventFilter(self)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)
        layout.addWidget(self.main_view)

        self.left_view.hide()
        self.right_view.hide()

    @staticmethod
    def _create_view(parent=None):
        view = QTableView(parent)
        view.setAlternatingRowColors(True)
        view.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        view.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        view.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        view.setSortingEnabled(False)
        view.setWordWrap(False)
        header = view.horizontalHeader()
        header.setMinimumSectionSize(VirtualizedFrozenTable.MIN_COLUMN_WIDTH)
        header.setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        header.setResizeContentsPrecision(100)
        return view

    @classmethod
    def _create_frozen_view(cls, parent):
        view = cls._create_view(parent)
        view.verticalHeader().hide()
        view.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        return view

    def set_frozen_columns_by_headers(self, left_headers=None, right_headers=None):
        self.left_frozen_headers = list(left_headers or [])
        self.right_frozen_headers = list(right_headers or [])
        self._configure_columns()

    def set_result(self, columns, titles, rows):
        headers_changed = list(titles or columns or []) != self.model._titles
        self.setUpdatesEnabled(False)
        try:
            self.model.set_result(columns, titles, rows)
            self._configure_columns()
            if headers_changed:
                self._set_initial_column_widths()
        finally:
            self.setUpdatesEnabled(True)
            self.update()
            self._schedule_frozen_geometry_update()

    def _configure_columns(self):
        titles = self.model._titles
        self._left_indices = {
            titles.index(name) for name in self.left_frozen_headers if name in titles
        }
        self._right_indices = {
            titles.index(name) for name in self.right_frozen_headers if name in titles
        }

        for column in range(self.model.columnCount()):
            self.main_view.setColumnHidden(column, False)
            self.left_view.setColumnHidden(column, column not in self._left_indices)
            self.right_view.setColumnHidden(column, column not in self._right_indices)

        self._schedule_frozen_geometry_update()

    def _set_initial_column_widths(self):
        # Match the compact QTableWidget layout used before virtualization:
        # each column follows its header/current-page content and stays resizable.
        self.main_view.resizeColumnsToContents()
        for column in range(self.model.columnCount()):
            width = max(
                self.MIN_COLUMN_WIDTH,
                min(self.MAX_COLUMN_WIDTH, self.main_view.columnWidth(column)),
            )
            for view in (self.main_view, self.left_view, self.right_view):
                view.setColumnWidth(column, width)

    def _visible_columns_width(self, view, indices):
        return sum(view.columnWidth(index) for index in sorted(indices))

    def _schedule_frozen_geometry_update(self):
        QTimer.singleShot(0, self._update_frozen_geometry)

    def _update_frozen_geometry(self):
        if not self.isVisible():
            return

        viewport = self.main_view.viewport().geometry()
        header_height = self.main_view.horizontalHeader().height()
        overlay_height = viewport.height() + header_height

        if self._left_indices:
            left_width = self._visible_columns_width(self.left_view, self._left_indices)
            self.left_view.setGeometry(viewport.x(), 0, left_width, overlay_height)
            self.left_view.show()
            self.left_view.raise_()
        else:
            self.left_view.hide()

        if self._right_indices:
            right_width = self._visible_columns_width(self.right_view, self._right_indices)
            right_x = viewport.x() + viewport.width() - right_width
            self.right_view.setGeometry(right_x, 0, right_width, overlay_height)
            self.right_view.show()
            self.right_view.raise_()
        else:
            self.right_view.hide()

    def _on_main_column_resized(self, logical_index, _old_size, new_size):
        self.left_view.setColumnWidth(logical_index, new_size)
        self.right_view.setColumnWidth(logical_index, new_size)
        self._schedule_frozen_geometry_update()

    def _on_frozen_column_resized(self, logical_index, _old_size, new_size):
        if logical_index in self._left_indices or logical_index in self._right_indices:
            self.main_view.setColumnWidth(logical_index, new_size)

    def _sync_row_height(self, logical_index, _old_size, new_size):
        self.left_view.setRowHeight(logical_index, new_size)
        self.right_view.setRowHeight(logical_index, new_size)

    def _sync_vertical_scroll(self, value):
        self.left_view.verticalScrollBar().setValue(value)
        self.right_view.verticalScrollBar().setValue(value)

    def eventFilter(self, watched, event):
        if watched is self.main_view.viewport() and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Show,
        }:
            self._schedule_frozen_geometry_update()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_frozen_geometry_update()

    def showEvent(self, event):
        super().showEvent(event)
        self._schedule_frozen_geometry_update()

    def _show_cell_detail(self, index):
        if not index.isValid():
            return
        dialog = CellDetailDialog(
            self.model.cell_text(index.row(), index.column()),
            index.row(),
            index.column(),
            self.model.column_title(index.column()),
            self,
        )
        dialog.exec()
