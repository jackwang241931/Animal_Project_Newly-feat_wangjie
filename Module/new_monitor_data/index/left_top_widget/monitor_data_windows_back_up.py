import math
import typing

from PyQt6 import QtGui
from PyQt6.QtCore import Qt, QRect
from PyQt6.QtGui import QGuiApplication
from PyQt6.QtWidgets import QDockWidget, QWidget

from Module.new_monitor_data.ui.custom.table.Table_select_columns_paging_bottom import Table_select_columns_paging_bottom
from theme.ThemeQt6 import ThemedWindow


class MonitorDataWindows(ThemedWindow):
    def resizeEvent(self, a0: typing.Optional[QtGui.QResizeEvent]):
        self.setMinimumSize(0, 0)
    def __init__(self):
        super().__init__()
        # 存放创建的 dock 引用
        self.centerWidget = QWidget()
        self.setCentralWidget(self.centerWidget)
        self._docks = []
        self._docks_widget = []
        self.delete_central_widget()


    def clear_existing_docks(self):
        for d in list(self._docks):
            try:
                d.hide()
                self.removeDockWidget(d)
                d.deleteLater()
            except Exception:
                pass
        self._docks = []
        for d in self._docks_widget:
            d.hide()
            d.deleteLater()
        self._docks_widget = []



    def create_tiled_docks(self,n=8,gids=[]):
        self.clear_existing_docks()


        columns = 4
        rows = math.ceil(n/columns)
        for row in range(rows):
            row0 = []
            for i in range(columns):
                if (row)*columns+i+1 <= n:
                    dock = QDockWidget(f"通道 {gids[(row)*columns+i]} {'(参考气)' if gids[(row)*columns+i]==0 else ''}", self)
                    dock.setAllowedAreas(Qt.DockWidgetArea.AllDockWidgetAreas)
                    dock.setFeatures(
                        QDockWidget.DockWidgetFeature.DockWidgetMovable
                        | QDockWidget.DockWidgetFeature.DockWidgetClosable
                        | QDockWidget.DockWidgetFeature.DockWidgetFloatable
                    )

                    widget =Table_select_columns_paging_bottom(gid=gids[(row)*columns+i])
                    # ！！！！！！！！！！！！！！！！！！！！！！！！！！临时添加！！！！！！！！！！！！！！！！！！
                    widget.on_replace_headers([1])
                    # ！！！！！！！！！！！！！！！！！！！！！！！！！！临时添加！！！！！！！！！！！！！！！！！！
                    dock.setWidget(widget)
                    self.addDockWidget(Qt.DockWidgetArea.LeftDockWidgetArea, dock)
                    row0.append(dock)
                    self._docks_widget.append(widget)
                    self._docks.append(dock)

                    current = row0[0]
                    for next_dock in row0[1:]:
                        self.splitDockWidget(current, next_dock, Qt.Orientation.Horizontal)
                        current = next_dock



        self.update()
        print(f"已创建 {n}个 dock（dock 形式平铺）")

    def float_and_tile_docks_as_windows(self):
        # 如果还没创建 docks，先创建
        if not self._docks:
            self.create_tiled_docks()

        central = self.centralWidget()
        if central is None:
            # 备用：使用 main window 的全局 rect
            origin_global = self.mapToGlobal(self.rect().topLeft())
            central_rect_global = QRect(origin_global, self.size())
        else:
            origin_pt = central.mapToGlobal(central.rect().topLeft())
            central_rect_global = QRect(origin_pt, central.size())

        # 找到放置区域所在的屏幕（支持多屏），若找不到则用主屏幕
        screen = QGuiApplication.screenAt(central_rect_global.center())
        if screen is None:
            screen = QGuiApplication.primaryScreen()

        screen_avail = screen.availableGeometry()

        # 用 central rect 与屏幕可用区做交集作为目标摆放区域
        target_rect = central_rect_global.intersected(screen_avail)
        if target_rect.isEmpty():
            # 若交集为空（例如 central 在不可见位置），退回屏幕可用区
            target_rect = QRect(screen_avail)

        # 给摆放区域留一点边距，避免覆盖任务栏或窗口边框
        margin = 8
        target_rect.adjust(margin, margin, -margin, -margin)
        if target_rect.width() < 100 or target_rect.height() < 100:
            # 如果太小则使用屏幕可用区作为保障
            target_rect = QRect(screen_avail.adjusted(margin, margin, -margin, -margin))

        cols = 4
        rows = 2
        min_cell_w = 120
        min_cell_h = 80

        # 基本单元尺寸（整除），但保证最小值
        cell_w = max(min_cell_w, target_rect.width() // cols)
        cell_h = max(min_cell_h, target_rect.height() // rows)

        # 若总尺寸超出 target_rect（因为最小单元太大），缩小单元以适配
        if cell_w * cols > target_rect.width():
            cell_w = max(1, target_rect.width() // cols)
        if cell_h * rows > target_rect.height():
            cell_h = max(1, target_rect.height() // rows)

        # 逐个浮动并设置位置/大小，最后一列/行吸收剩余像素
        for idx, dock in enumerate(self._docks):
            col = idx % cols
            row = idx // cols
            x = target_rect.left() + col * cell_w
            y = target_rect.top() + row * cell_h

            # 计算宽高，最后一列/行吸收剩余空间，避免像素缺失或超出
            if col == cols - 1:
                w = target_rect.right() - x + 1
            else:
                w = cell_w
            if row == rows - 1:
                h = target_rect.bottom() - y + 1
            else:
                h = cell_h

            # 设置为浮动并确保 geometry 在屏幕可用区内
            dock.setFloating(True)
            # 有时 setGeometry 的参数需要整数 QRect 或四元组
            geom = QRect(x, y, max(10, w), max(10, h))
            # 再次确保与屏幕可用区相交（防止微小越界）
            geom = geom.intersected(screen_avail)
            if geom.isEmpty():
                # 兜底：把窗口放到屏幕中心的一个合理大小
                geom = QRect(
                    screen_avail.center().x() - 200,
                    screen_avail.center().y() - 150,
                    400,
                    300,
                ).intersected(screen_avail)

            dock.setGeometry(geom)
            dock.show()

        print("已将 docks 转换为独立浮动窗口并按 4x2 平铺（受屏幕约束）。")