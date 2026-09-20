from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtWidgets import QTableWidget, QAbstractItemView, QTableWidgetItem, QHeaderView
from PyQt6.QtGui import QCursor

from Module.User_monitor.ui.custom.table.UserTableCellDetailDialog import CellDetailDialog


class BiDirectionalFrozenTable(QTableWidget):
    """支持左右双向冻结列的表格"""

    def __init__(self, rows, columns, left_frozen_headers=None, right_frozen_headers=None, parent=None):
        super().__init__(rows, columns, parent)

        # 存储冻结列的表头名称
        self.left_frozen_headers = left_frozen_headers or []
        self.right_frozen_headers = right_frozen_headers or []

        # 实际的冻结列索引（初始化时为空，在设置表头后计算）
        self.left_frozen_indices = []
        self.right_frozen_indices = []

        self.total_columns = columns

        # 创建冻结表格（初始时可能为空）
        self.left_frozen_table = None
        self.right_frozen_table = None

        # 表头设置标志
        self.headers_set = False

    def setHorizontalHeaderLabels(self, labels):
        """重写设置水平表头标签的方法，根据表头名称计算冻结列"""
        super().setHorizontalHeaderLabels(labels)

        # 根据表头名称计算冻结列索引
        self._calculate_frozen_indices(labels)

        # 创建和设置冻结表格
        self._create_frozen_tables()

        # 设置冻结表格的表头
        self._sync_frozen_headers(labels)

        self.headers_set = True

        # 初始化冻结表格
        self.setup_frozen_tables()

        # 连接信号（只连接一次）
        if not hasattr(self, 'signals_connected'):
            self.connect_signals()
            self.signals_connected = True

        # 更新位置
        self.update_frozen_tables_geometry()

    def _calculate_frozen_indices(self, labels):
        """根据表头名称计算冻结列索引"""
        self.left_frozen_indices = []
        self.right_frozen_indices = []

        # 计算左侧冻结列索引
        for header_name in self.left_frozen_headers:
            try:
                index = labels.index(header_name)
                self.left_frozen_indices.append(index)
            except ValueError:
                print(f"警告：左侧冻结列表头 '{header_name}' 未找到")

        # 计算右侧冻结列索引
        for header_name in self.right_frozen_headers:
            try:
                index = labels.index(header_name)
                self.right_frozen_indices.append(index)
            except ValueError:
                print(f"警告：右侧冻结列表头 '{header_name}' 未找到")

        # 排序索引
        self.left_frozen_indices.sort()
        self.right_frozen_indices.sort()

        # print(f"左侧冻结列索引: {self.left_frozen_indices}")
        # print(f"右侧冻结列索引: {self.right_frozen_indices}")

    def _create_frozen_tables(self):
        """根据冻结列数量创建冻结表格"""
        # 销毁旧的冻结表格
        if self.left_frozen_table:
            self.left_frozen_table.deleteLater()
        if self.right_frozen_table:
            self.right_frozen_table.deleteLater()

        # 创建新的冻结表格
        if self.left_frozen_indices:
            self.left_frozen_table = QTableWidget(self.rowCount(), len(self.left_frozen_indices))
            self.left_frozen_table.setParent(self)

        else:
            self.left_frozen_table = None


        if self.right_frozen_indices:
            self.right_frozen_table = QTableWidget(self.rowCount(), len(self.right_frozen_indices))
            self.right_frozen_table.setParent(self)
        else:
            self.right_frozen_table = None


    def _sync_frozen_headers(self, labels):
        """同步冻结表格的表头"""
        # 设置左侧冻结表格的表头
        if self.left_frozen_table:
            left_labels = [labels[i] for i in self.left_frozen_indices]
            self.left_frozen_table.setHorizontalHeaderLabels(left_labels)

        # 设置右侧冻结表格的表头
        if self.right_frozen_table:
            right_labels = [labels[i] for i in self.right_frozen_indices]
            self.right_frozen_table.setHorizontalHeaderLabels(right_labels)


    def set_frozen_columns_by_headers(self, left_headers=None, right_headers=None):
        """通过表头名称设置冻结列"""
        self.left_frozen_headers = left_headers or []
        self.right_frozen_headers = right_headers or []

        # 如果表头已经设置过，重新计算冻结列
        if self.headers_set:
            labels = []
            for col in range(self.columnCount()):
                header_item = self.horizontalHeaderItem(col)
                labels.append(header_item.text() if header_item else f"列{col + 1}")

            self.setHorizontalHeaderLabels(labels)


    def setRowCount(self, rows):
        """重写setRowCount方法，同时设置冻结表格的行数"""
        super().setRowCount(rows)
        if self.left_frozen_table:
            self.left_frozen_table.setRowCount(rows)
        if self.right_frozen_table:
            self.right_frozen_table.setRowCount(rows)


    def insertRow(self, row):
        """重写insertRow方法，同时在冻结表格中插入行"""
        super().insertRow(row)
        if self.left_frozen_table:
            self.left_frozen_table.insertRow(row)
        if self.right_frozen_table:
            self.right_frozen_table.insertRow(row)


    def removeRow(self, row):
        """重写removeRow方法，同时在冻结表格中删除行"""
        super().removeRow(row)
        if self.left_frozen_table:
            self.left_frozen_table.removeRow(row)
        if self.right_frozen_table:
            self.right_frozen_table.removeRow(row)


    def clear(self):
        """重写clear方法，同时清空冻结表格"""
        super().clear()
        if self.left_frozen_table:
            self.left_frozen_table.clear()
        if self.right_frozen_table:
            self.right_frozen_table.clear()


    def setHorizontalHeaderItem(self, column, item):
        """重写设置水平表头项的方法"""
        super().setHorizontalHeaderItem(column, item)

        if not self.headers_set:
            return

        # 更新左侧冻结表格的表头
        if self.left_frozen_table and column in self.left_frozen_indices:
            frozen_col = self.left_frozen_indices.index(column)
            header_item = QTableWidgetItem(item.text() if item else "")
            if item:
                header_item.setFont(item.font())
                header_item.setForeground(item.foreground())
                header_item.setBackground(item.background())
                header_item.setTextAlignment(item.textAlignment())
            self.left_frozen_table.setHorizontalHeaderItem(frozen_col, header_item)

        # 更新右侧冻结表格的表头
        if self.right_frozen_table and column in self.right_frozen_indices:
            frozen_col = self.right_frozen_indices.index(column)
            header_item = QTableWidgetItem(item.text() if item else "")
            if item:
                header_item.setFont(item.font())
                header_item.setForeground(item.foreground())


                header_item.setBackground(item.background())
                header_item.setTextAlignment(item.textAlignment())
            self.right_frozen_table.setHorizontalHeaderItem(frozen_col, header_item)


    def setItem(self, row, column, item):
        """重写setItem方法，同时更新冻结表格"""
        super().setItem(row, column, item)

        if not self.headers_set:
            return

        # 确保冻结表格有足够的行数
        if self.left_frozen_table and row >= self.left_frozen_table.rowCount():
            self.left_frozen_table.setRowCount(row + 1)
        if self.right_frozen_table and row >= self.right_frozen_table.rowCount():
            self.right_frozen_table.setRowCount(row + 1)

        # 更新左侧冻结列
        if self.left_frozen_table and column in self.left_frozen_indices:
            frozen_col = self.left_frozen_indices.index(column)
            left_item = QTableWidgetItem(item.text() if item else "")
            if item:
                left_item.setForeground(item.foreground())
                left_item.setTextAlignment(item.textAlignment())
                left_item.setBackground(item.background())
            self.left_frozen_table.setItem(row, frozen_col, left_item)

        # 更新右侧冻结列
        if self.right_frozen_table and column in self.right_frozen_indices:
            frozen_col = self.right_frozen_indices.index(column)
            right_item = QTableWidgetItem(item.text() if item else "")
            if item:
                right_item.setForeground(item.foreground())
                right_item.setTextAlignment(item.textAlignment())
                right_item.setBackground(item.background())
            self.right_frozen_table.setItem(row, frozen_col, right_item)


    def setup_frozen_tables(self):
        """设置冻结表格属性"""

        # 设置左侧冻结表格
        if self.left_frozen_table:
            self._setup_frozen_table(self.left_frozen_table, is_left=True)

        # 设置右侧冻结表格
        if self.right_frozen_table:
            self._setup_frozen_table(self.right_frozen_table, is_left=False)


    def _setup_frozen_table(self, frozen_table, is_left=True):
        """设置单个冻结表格的属性"""
        # 显示水平表头，隐藏垂直表头
        frozen_table.horizontalHeader().show()
        frozen_table.verticalHeader().hide()

        # 设置表头样式与主表格一致
        frozen_table.horizontalHeader().setStyleSheet(self.horizontalHeader().styleSheet())
        frozen_table.horizontalHeader().setDefaultSectionSize(self.horizontalHeader().defaultSectionSize())
        frozen_table.horizontalHeader().setMinimumSectionSize(self.horizontalHeader().minimumSectionSize())
        frozen_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)

        # 设置选择模式和焦点
        frozen_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        frozen_table.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        # 禁用滚动条
        frozen_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        frozen_table.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)



        # 确保始终显示
        frozen_table.show()
        frozen_table.raise_()


    def connect_signals(self):
        """连接同步信号"""
        # 垂直滚动同步
        self.verticalScrollBar().valueChanged.connect(self.sync_vertical_scroll)

        # 水平滚动时更新冻结表格位置
        self.horizontalScrollBar().valueChanged.connect(self.on_horizontal_scroll)

        # 行高同步
        self.verticalHeader().sectionResized.connect(self.sync_row_height)

        # 列宽同步（仅对应列）
        self.horizontalHeader().sectionResized.connect(self.sync_column_width)

        # 表头高度同步
        self.horizontalHeader().sectionResized.connect(self.sync_header_height)

        # 选择同步
        self.itemSelectionChanged.connect(self.sync_selection)


    def sync_vertical_scroll(self, value):
        """同步垂直滚动"""
        if self.left_frozen_table:
            self.left_frozen_table.verticalScrollBar().setValue(value)
        if self.right_frozen_table:
            self.right_frozen_table.verticalScrollBar().setValue(value)


    def sync_row_height(self, logical_index, old_size, new_size):
        """同步行高"""
        if self.left_frozen_table:
            self.left_frozen_table.setRowHeight(logical_index, new_size)
        if self.right_frozen_table:
            self.right_frozen_table.setRowHeight(logical_index, new_size)


    def sync_column_width(self, logical_index, old_size, new_size):
        """同步列宽"""
        # 同步左侧冻结列
        if self.left_frozen_table and logical_index in self.left_frozen_indices:
            frozen_col = self.left_frozen_indices.index(logical_index)
            self.left_frozen_table.setColumnWidth(frozen_col, new_size)

        # 同步右侧冻结列
        if self.right_frozen_table and logical_index in self.right_frozen_indices:
            frozen_col = self.right_frozen_indices.index(logical_index)
            self.right_frozen_table.setColumnWidth(frozen_col, new_size)

        # 更新冻结表格位置（因为列宽改变可能影响位置）
        QTimer.singleShot(0, self.update_frozen_tables_geometry)


    def sync_header_height(self):
        """同步表头高度"""
        header_height = self.horizontalHeader().height()
        if self.left_frozen_table:
            self.left_frozen_table.horizontalHeader().setFixedHeight(header_height)
        if self.right_frozen_table:
            self.right_frozen_table.horizontalHeader().setFixedHeight(header_height)


    def sync_selection(self):
        """同步选择状态"""
        current_item = self.currentItem()
        if not current_item or not self.headers_set:
            return

        row = current_item.row()
        col = current_item.column()

        # 同步到左侧冻结表格
        if self.left_frozen_table and col in self.left_frozen_indices:
            frozen_col = self.left_frozen_indices.index(col)
            self.left_frozen_table.setCurrentCell(row, frozen_col)

        # 同步到右侧冻结表格
        if self.right_frozen_table and col in self.right_frozen_indices:
            frozen_col = self.right_frozen_indices.index(col)
            self.right_frozen_table.setCurrentCell(row, frozen_col)


    def on_horizontal_scroll(self, value):
        """水平滚动时处理冻结表格显示 - 修改为始终显示"""
        if not self.headers_set:
            return

        # 始终显示冻结表格，不管滚动位置
        if self.left_frozen_table:
            self.left_frozen_table.show()
            self.left_frozen_table.raise_()  # 确保在最上层

        if self.right_frozen_table:
            self.right_frozen_table.show()
            self.right_frozen_table.raise_()  # 确保在最上层

        # 更新位置
        self.update_frozen_tables_geometry()


    def resizeEvent(self, event):
        """窗口大小改变事件"""
        super().resizeEvent(event)
        if self.headers_set:
            self.update_frozen_tables_geometry()


    def showEvent(self, event):
        """显示事件 - 确保冻结表格也显示"""
        super().showEvent(event)
        if self.headers_set:
            if self.left_frozen_table:
                self.left_frozen_table.show()
                self.left_frozen_table.raise_()
            if self.right_frozen_table:
                self.right_frozen_table.show()
                self.right_frozen_table.raise_()

    def update_frozen_tables_geometry(self):
        """更新冻结表格的几何位置"""
        if not self.parent() or not self.headers_set:
            return

        viewport_rect = self.viewport().geometry()
        header_height = self.horizontalHeader().height()
        v_header_width = self.verticalHeader().width()

        # 更新左侧冻结表格位置
        if self.left_frozen_table:
            left_width = sum(self.columnWidth(i) for i in self.left_frozen_indices)
            self.left_frozen_table.setGeometry(
                v_header_width,  # x
                0,  # y (包含表头)
                left_width,  # width
                viewport_rect.height() + header_height  # height (包含表头)
            )

            # 同步列宽
            for i, col in enumerate(self.left_frozen_indices):
                self.left_frozen_table.setColumnWidth(i, self.columnWidth(col))

            # 确保显示在最上层
            self.left_frozen_table.show()
            self.left_frozen_table.raise_()

        # 更新右侧冻结表格位置
        if self.right_frozen_table:
            right_width = sum(self.columnWidth(i) for i in self.right_frozen_indices)

            self.right_frozen_table.setGeometry(
                viewport_rect.width() - right_width + v_header_width,  # x (右对齐)
                0,  # y (包含表头)
                right_width,  # width
                viewport_rect.height() + header_height  # height (包含表头)
            )

            # 同步列宽
            for i, col in enumerate(self.right_frozen_indices):
                self.right_frozen_table.setColumnWidth(i, self.columnWidth(col))

            # 确保显示在最上层
            self.right_frozen_table.show()
            self.right_frozen_table.raise_()

        # 同步行高
        for row in range(self.rowCount()):
            row_height = self.rowHeight(row)
            if self.left_frozen_table and row < self.left_frozen_table.rowCount():
                self.left_frozen_table.setRowHeight(row, row_height)
            if self.right_frozen_table and row < self.right_frozen_table.rowCount():
                self.right_frozen_table.setRowHeight(row, row_height)

        # 同步表头高度
        self.sync_header_height()





class CustomTableWidget(BiDirectionalFrozenTable):
    """自定义表格控件"""

    def __init__(self, parent=None):
        # 指定要冻结的列名
        left_frozen_headers = ["ID", "姓名"]  # 左侧冻结的列名
        right_frozen_headers = ["总分", "备注"]  # 右侧冻结的列名

        super().__init__(parent=parent, rows=0, columns=12,
                         left_frozen_headers=left_frozen_headers,
                         right_frozen_headers=right_frozen_headers)
        self.setMouseTracking(True)

        self.hover_timer = QTimer()
        self.hover_timer.setSingleShot(True)
        self.hover_timer.timeout.connect(self.show_tooltip)

        # 使用行列索引而不是item引用
        self.current_hover_row = -1
        self.current_hover_column = -1
        self.hover_delay = 500

        self.cellClicked.connect(self.on_cell_double_clicked)

    def mouseMoveEvent(self, event):
        """处理鼠标移动事件"""
        super().mouseMoveEvent(event)

        item = self.itemAt(event.pos())
        if item:
            try:
                row = item.row()
                column = item.column()
            except (RuntimeError, AttributeError):
                row = -1
                column = -1
        else:
            row = -1
            column = -1
        if row != self.current_hover_row or column != self.current_hover_column:
            self.current_hover_row = row
            self.current_hover_column = column

            if row >= 0 and column >= 0:
                # 设置鼠标样式
                self.setCursor(QCursor(Qt.CursorShape.PointingHandCursor))
                # 启动悬停计时器
                self.hover_timer.start(self.hover_delay)
            else:
                self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
                self.hover_timer.stop()

    def get_cell_content_safe(self, row, column):
        """安全地获取单元格内容"""
        try:
            # 检查行列索引是否有效
            if (row < 0 or row >= self.rowCount() or
                    column < 0 or column >= self.columnCount()):
                return ""

            item = self.item(row, column)
            if item is None:
                return ""

            return item.text()
        except (RuntimeError, AttributeError, IndexError):
            return ""

    def show_tooltip(self):
        """显示工具提示"""
        if self.current_hover_row < 0 or self.current_hover_column < 0:
            return

        cell_content = self.get_cell_content_safe(self.current_hover_row, self.current_hover_column)

        # 只有当内容较长时才显示
        if cell_content and len(cell_content) > 10:
            # 格式化显示内容
            formatted_content = f"<div style='max-width: 300px; word-wrap: break-word;'>{cell_content}</div>"

            # 显示在鼠标位置
            from PyQt6.QtWidgets import QToolTip
            QToolTip.showText(QCursor.pos(), formatted_content, self)

    def leaveEvent(self, event):
        """鼠标离开控件时的处理"""
        super().leaveEvent(event)
        self.setCursor(QCursor(Qt.CursorShape.ArrowCursor))
        self.hover_timer.stop()
        # 延迟隐藏 tooltip，给用户足够的时间移动到 tooltip 上
        from PyQt6.QtWidgets import QToolTip
        QTimer.singleShot(5000, QToolTip.hideText)
        self.current_hover_row = -1
        self.current_hover_column = -1

    def on_cell_double_clicked(self, row, column):
        """处理单元格双击事件"""
        from PyQt6.QtWidgets import QToolTip
        QToolTip.hideText()

        item = self.item(row, column)
        if item:
            cell_value = item.text()
        else:
            cell_value = ""

        column_name = self.horizontalHeaderItem(column).text() if self.horizontalHeaderItem(
            column) else f"列{column + 1}"

        dialog = CellDetailDialog(cell_value, row, column, column_name, self)
        dialog.exec()
