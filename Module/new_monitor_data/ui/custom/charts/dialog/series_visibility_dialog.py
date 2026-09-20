from typing import List

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QPushButton, QHBoxLayout, QListWidgetItem, QAbstractItemView, QListWidget, QLabel, \
    QVBoxLayout, QDialog


class SeriesVisibilityDialog(QDialog):
    """数据系列显示/隐藏对话框 - 也用于选择编辑的鼠笼"""

    def __init__(self, series_list: List[str], visible_series: set,
                 parent=None, multi_select: bool = True, title: str = "选择显示的数据系列"):
        super().__init__(parent)
        self.series_list = sorted(series_list, key=self._extract_cage_number)
        self.visible_series = visible_series.copy()
        self.multi_select = multi_select
        self.selected_series = None
        self.setWindowTitle(title)
        self.init_ui()
        # 应用样式表
        self.apply_styles()

    def apply_styles(self):
        """应用样式表以确保复选框正确显示"""
        style_sheet = """
                   QListWidget {
                       background-color: #FFFFFF;
                       color: #000000;
                       border: 1px solid #CCCCCC;
                       border-radius: 4px;
                       padding: 5px;
                   }

                   QListWidget::item {
                       padding: 5px;
                       margin: 2px 0px;
                   }

                   QListWidget::item:hover {
                       background-color: #E8F4F8;
                   }

                   QListWidget::item:selected {
                       background-color: #B3D9E8;
                       color: #000000;
                   }

                   QCheckBox {
                       spacing: 8px;
                   }

                   QCheckBox::indicator {
                       width: 18px;
                       height: 18px;
                   }

                   QCheckBox::indicator:unchecked {
                       image: url(:/icons/checkbox_unchecked.png);
                   }

                   QCheckBox::indicator:checked {
                       image: url(:/icons/checkbox_checked.png);
                   }

                   QPushButton {
                       background-color: #0078D4;
                       color: white;
                       border: none;
                       border-radius: 4px;
                       padding: 6px 15px;
                       font-weight: bold;
                   }

                   QPushButton:hover {
                       background-color: #005A9E;
                   }

                   QPushButton:pressed {
                       background-color: #004578;
                   }
               """
        self.setStyleSheet(style_sheet)
    def _extract_cage_number(self, cage_name: str) -> int:
        """从鼠笼名称提取数字用于排序"""
        try:
            # 尝试从字符串中提取数字
            import re
            numbers = re.findall(r'\d+', cage_name)
            return int(numbers[0]) if numbers else float('inf')
        except:
            return float('inf')

    def init_ui(self):
        """初始化UI"""
        self.setGeometry(100, 100, 350, 400)

        layout = QVBoxLayout(self)

        # 说明文字
        if self.multi_select:
            label = QLabel("勾选要显示的数据系列:")
        else:
            label = QLabel("选择要编辑的数据系列:")
        layout.addWidget(label)

        # 列表
        self.list_widget = QListWidget()
        # if self.multi_select:
        #     self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        # else:
        #     self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)

        for series in self.series_list:
            item = QListWidgetItem(series)
            if self.multi_select:
                item.setCheckState(Qt.CheckState.Checked if series in self.visible_series
                                   else Qt.CheckState.Unchecked)
            else:
                item.setSelected(False)
            self.list_widget.addItem(item)

        layout.addWidget(self.list_widget)

        # 快捷按钮 - 仅在多选模式显示
        if self.multi_select:
            button_row = QHBoxLayout()
            select_all_btn = QPushButton("全选")
            select_all_btn.clicked.connect(self.select_all)
            button_row.addWidget(select_all_btn)

            deselect_all_btn = QPushButton("全不选")
            deselect_all_btn.clicked.connect(self.deselect_all)
            button_row.addWidget(deselect_all_btn)

            layout.addLayout(button_row)

        # 确定/取消按钮
        dialog_buttons = QHBoxLayout()
        ok_btn = QPushButton("确定")
        cancel_btn = QPushButton("取消")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        dialog_buttons.addWidget(ok_btn)
        dialog_buttons.addWidget(cancel_btn)
        layout.addLayout(dialog_buttons)

    def select_all(self):
        """全选"""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setCheckState(Qt.CheckState.Checked)

    def deselect_all(self):
        """全不选"""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            item.setCheckState(Qt.CheckState.Unchecked)

    def get_visible_series(self) -> set:
        """获取选中的数据系列（多选模式）"""
        visible = set()
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.checkState() == Qt.CheckState.Checked:
                visible.add(item.text())
        return visible

    def get_selected_series(self) -> str:
        """获取选中的单个数据系列（单选模式）"""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item.isSelected():
                return item.text()
        return None
