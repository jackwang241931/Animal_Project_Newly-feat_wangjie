from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QVBoxLayout, QLabel, QScrollArea, QPushButton, QWidget, QHBoxLayout, QTextEdit, \
    QApplication, QTableWidget, QTableWidgetItem

from theme.ThemeQt6 import ThemedDialog


class CellDetailDialog(ThemedDialog):
    """单元格详情弹窗"""

    def __init__(self, cell_value, row, column, column_name, parent=None):
        super().__init__()
        self.setWindowTitle(f"单元格详情 - 行{row + 1}, 列{column_name}")
        # self.setModal(True)
        self.resize(600, 450)
        # self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)

        layout = QVBoxLayout()

        # 标题信息
        title_widget = QWidget()
        title_layout = QHBoxLayout()
        title_widget.setStyleSheet("background-color: #f0f8ff; padding: 10px; border-radius: 5px; margin-bottom: 10px;")

        row_label = QLabel(f"行号: {row + 1}")
        col_label = QLabel(f"列名: {column_name}")
        length_label = QLabel(f"长度: {len(str(cell_value))} 字符")

        for label in [row_label, col_label, length_label]:
            label.setFont(QFont("Arial", 10, QFont.Weight.Bold))
            title_layout.addWidget(label)

        title_layout.addStretch()
        title_widget.setLayout(title_layout)
        layout.addWidget(title_widget)

        # 单元格内容显示
        if len(cell_value) > 200:  # 长文本使用文本编辑器
            content_edit = QTextEdit()
            content_edit.setPlainText(cell_value)
            content_edit.setReadOnly(True)
            content_edit.setStyleSheet("border: 1px solid #bdc3c7; background-color: #fafafa;")
            layout.addWidget(content_edit)
        else:  # 短文本使用标签
            content_label = QLabel(cell_value if cell_value else "(空)")
            content_label.setWordWrap(True)
            content_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            content_label.setStyleSheet("""
                padding: 10px;
                background-color: #fafafa;
                border: 1px solid #bdc3c7;
                border-radius: 3px;
                font-family: 'Courier New', monospace;
            """)
            if not cell_value:
                content_label.setStyleSheet(content_label.styleSheet() + "color: #95a5a6; font-style: italic;")
            layout.addWidget(content_label)

        # 行数据显示表格
        row_data_table = QTableWidget()
        row_data_table.setRowCount(1)
        row_data_table.setColumnCount(parent.columnCount())
        row_data_table.setHorizontalHeaderLabels(
            [parent.horizontalHeaderItem(col).text() if parent.horizontalHeaderItem(col) else f"列{col + 1}" for col in
             range(parent.columnCount())]
        )
        row_data_table.setStyleSheet("""
            QTableWidget {
                background-color: #f0f0f0;
                border: none;
                font-size: 9px;
            }
            QTableWidget::item {
                padding: 5px;
            }
        """)

        for col in range(parent.columnCount()):
            if col != column:
                item = QTableWidgetItem(parent.item(row, col).text() if parent.item(row, col) else "(空)")
                row_data_table.setItem(0, col, item)

        row_data_table.resizeColumnsToContents()
        row_data_table.setMaximumHeight(row_data_table.rowHeight(0) * 2 + 10)
        layout.addWidget(row_data_table)

        # 按钮区域
        button_layout = QHBoxLayout()

        copy_button = QPushButton("复制内容")
        copy_button.clicked.connect(lambda: QApplication.clipboard().setText(str(cell_value)))

        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)
        close_button.setDefault(True)

        button_layout.addWidget(copy_button)
        button_layout.addStretch()
        button_layout.addWidget(close_button)

        layout.addLayout(button_layout)
        self.setLayout(layout)