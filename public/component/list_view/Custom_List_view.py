

import sys
from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QWidget, QPushButton, QLineEdit, QHBoxLayout
from PyQt6.QtCore import QStringListModel, Qt
from PyQt6.QtGui import QStandardItemModel, QStandardItem
class CustomListView(QMainWindow):
    def __init__(self):
        super().__init__()
        self.max_items = 100  # 最大数据条数
        self.single_row_height = 25  # 单行高度
        self.expanded_height = 550  # 展开时的高度（约15行）
        self.default_height = self.single_row_height * 2  # 默认显示2行
        self.is_expanded = False  # 是否展开状态
        self._init_ui()
        self.init_model()

    def _init_ui(self):
        """初始化UI界面"""
        self.setWindowTitle("QListView with Scroll Bar")
        self.setGeometry(100, 100, 800, 200)  # 调整窗口大小

        # 创建中央部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # 创建布局
        layout = QVBoxLayout(central_widget)

        # 创建QListView
        from PyQt6.QtWidgets import QListView
        from PyQt6.QtCore import Qt
        self.list_view = QListView()

        # 设置滚动条策略
        self.list_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        # 设置鼠标样式为可点击的手型光标
        self.list_view.setCursor(Qt.CursorShape.PointingHandCursor)

        # 设置初始高度为两行
        self.list_view.setFixedHeight(self.default_height)

        # 创建"收缩"按钮
        from PyQt6.QtWidgets import QPushButton
        self.collapse_button = QPushButton("收缩")
        self.collapse_button.clicked.connect(self._collapse_list)
        self.collapse_button.setVisible(False)  # 初始隐藏

        # 连接ListView的点击事件
        self.list_view.clicked.connect(self._on_list_clicked)

        # 添加到布局
        layout.addWidget(self.list_view)
        layout.addWidget(self.collapse_button)

    def _on_list_clicked(self):
        """ListView被点击时的处理"""
        if not self.is_expanded:
            self._expand_list()

    def mousePressEvent(self, event):
        """处理窗口的鼠标点击事件"""
        # 如果点击的不是listview或收缩按钮区域，则收缩列表
        if self.is_expanded and not (self.list_view.geometry().contains(event.pos()) or
                                    self.collapse_button.geometry().contains(event.pos())):
            self._collapse_list()
        super().mousePressEvent(event)

    def _expand_list(self):
        """展开列表显示多行"""
        if not self.is_expanded:
            self.list_view.setFixedHeight(self.expanded_height)
            self.collapse_button.setVisible(True)
            self.is_expanded = True
            # 调整窗口大小以适应展开的列表
            current_geometry = self.geometry()
            self.setGeometry(current_geometry.x(), current_geometry.y(),
                            current_geometry.width(), self.expanded_height + 50)

    def _collapse_list(self):
        """收缩列表显示单行"""
        if self.is_expanded:
            self.list_view.setFixedHeight(self.default_height)
            self.collapse_button.setVisible(False)
            self.is_expanded = False
            # 调整窗口大小
            current_geometry = self.geometry()
            self.setGeometry(current_geometry.x(), current_geometry.y(),
                            current_geometry.width(), self.default_height + 50)

    def _init_customize_ui(self):
        pass

    def _init_function(self):
        pass

    def _init_custom_style_sheet(self):
        pass

    def init_model(self):
        """初始化数据模型"""
        from PyQt6.QtGui import QStandardItemModel
        self.model = QStandardItemModel()
        self.list_view.setModel(self.model)

    def _enforce_max_items(self):
        """确保数据不超过最大条数"""
        while self.model.rowCount() > self.max_items:
            # 删除最后一行（最早插入的数据）
            self.model.removeRow(self.model.rowCount() - 1)

    def insert_data(self, data):
        """
        插入新数据到列表最前面的接口方法

        Args:
            data (str): 要插入的数据
        """
        from PyQt6.QtGui import QStandardItem

        if isinstance(data, str):
            item = QStandardItem(data)
        else:
            item = QStandardItem(str(data))

        # 插入到第一行（索引0）
        self.model.insertRow(0, item)

        # 检查并限制数据条数
        self._enforce_max_items()

        # 可选：滚动到顶部显示新添加的数据
        self.list_view.scrollToTop()

    def insert_multiple_data(self, data_list):
        """
        批量插入多个数据的接口方法

        Args:
            data_list (list): 要插入的数据列表
        """
        for data in reversed(data_list):  # 反向插入保持顺序
            self.insert_data(data)

    def get_all_data(self):
        """
        获取所有数据的方法

        Returns:
            list: 包含所有数据的列表
        """
        data_list = []
        for row in range(self.model.rowCount()):
            item = self.model.item(row)
            if item:
                data_list.append(item.text())
        return data_list

    def clear_all_data(self):
        """清空所有数据的方法"""
        self.model.clear()

# 如果需要测试，可以添加以下代码：
if __name__ == "__main__":
    from PyQt6.QtWidgets import QApplication
    from PyQt6.QtCore import Qt
    import sys

    app = QApplication(sys.argv)
    window = CustomListView()

    # 添加一些测试数据
    for i in range(15):
        window.insert_data(f"测试数据 {i+1}")

    window.show()
    sys.exit(app.exec())
