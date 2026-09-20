import sys
from PyQt6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QScrollArea,
                             QSplitter, QLabel, QHBoxLayout, QGridLayout, QSizePolicy)
from PyQt6.QtCore import Qt, QTimer, QSize
from loguru import logger

from public.component.dock_widget.DraggableDockWidget import TabNavigator, DraggableContainer, DraggableFrame


class DemoDraggableDockWidget(QScrollArea):
    """演示如何使用拖拽框架的主窗口 - 朴素风格"""

    def __init__(self, parent=None, is_showt_tab=True, enable_detach=True,
                 disable_vertical_wheel=False):
        super().__init__(parent)
        self.setWindowTitle("Tab导航 + 拖拽框架")
        self.is_showt_tab = is_showt_tab
        self.enable_detach = enable_detach
        self.disable_vertical_wheel = disable_vertical_wheel
        self.frames = []  # 存储所有Frame的引用

        # 设置QScrollArea的属性
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        self.setupUI()

    def wheelEvent(self, event):
        if self.disable_vertical_wheel:
            # 外层区域只负责横向切换通道，避免空白区域被鼠标滚轮带动。
            event.accept()
            return
        super().wheelEvent(event)

    def setupUI(self):
        # 创建主widget作为scrollArea的内容
        main_widget = QWidget()
        main_layout = QVBoxLayout(main_widget)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(12)

        # Tab导航栏

        self.tab_navigator = TabNavigator()
        self.tab_navigator.tabClicked.connect(self.navigateToFrame)
        self.tab_navigator.tabOrderChanged.connect(self.onTabOrderChanged)
        if self.is_showt_tab:
            main_layout.addWidget(self.tab_navigator)

        # 使用DraggableContainer作为父容器
        self.container = DraggableContainer()

        # 使用 QSplitter 作为 container_layout
        self.container_layout = QSplitter(Qt.Orientation.Horizontal, self.container)
        self.container_layout.setObjectName("container_layout")
        self.container_layout.setChildrenCollapsible(False)  # 防止子widget被折叠
        self.container_layout.setHandleWidth(1)  # 设置分割条宽度

        # 关键：设置QSplitter的大小策略，防止被VBoxLayout压缩
        self.container_layout.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Preferred)

        # 设置样式，可选
        self.container_layout.setStyleSheet("""
            QSplitter::handle {
                background-color: #cccccc;
              
            }
            QSplitter::handle:hover {
                background-color: #bbbbbb;
            }
        """)

        # 设置splitter填充整个container
        container_main_layout = QVBoxLayout(self.container)
        container_main_layout.setContentsMargins(12, 12, 12, 12)
        # 关键：不让layout强制调整子widget大小
        container_main_layout.setSizeConstraint(QVBoxLayout.SizeConstraint.SetNoConstraint)
        container_main_layout.addWidget(self.container_layout)

        # 将container添加到主布局
        main_layout.addWidget(self.container)

        # 设置主widget为scrollArea的内容
        self.setWidget(main_widget)

    def addFrames(self, widgets):
        """添加Frame组件 - 使用QSplitter水平排列"""
        total_width = 0
        max_height = 0

        for i, widget in enumerate(widgets):
            # 不修改widget的大小，保持原有设置
            frame = DraggableFrame(
                widget.windowTitle(),
                widget,
                self.container,
                enable_detach=self.enable_detach
            )

            frame.frameDetached.connect(self.onFrameDetached)
            frame.frameAttached.connect(self.onFrameAttached)

            # 计算尺寸
            min_size = widget.minimumSize()
            total_width += max(min_size.width(), widget.minimumWidth(), frame.minimumWidth())
            max_height = max(max_height, min_size.height(), widget.minimumHeight(), frame.minimumHeight())

            # 添加到QSplitter
            self.container_layout.addWidget(frame)

            self.frames.append(frame)
            self.tab_navigator.addFrame(frame)

        # 设置QSplitter和container的大小
        if self.frames:
            # 计算所需的总大小
            splitter_width = total_width + 100  # 额外空间
            splitter_height = max_height + 100

            # 设置QSplitter的固定大小
            self.container_layout.setMinimumSize(splitter_width, splitter_height)
            self.container_layout.resize(splitter_width, splitter_height)

            # 设置container的大小
            self.container.setMinimumSize(splitter_width + 24, splitter_height + 24)  # 考虑margins
            self.container.resize(splitter_width + 24, splitter_height + 24)

            # 设置主widget的大小，确保滚动条能正常工作
            main_widget = self.widget()
            main_widget.setMinimumSize(splitter_width + 48, splitter_height + 100)  # 考虑tab navigator和margins

            # 设置初始分割比例
            sizes = [
                max(frame.content_widget.minimumWidth(), frame.minimumWidth())
                for frame in self.frames
            ]
            self.container_layout.setSizes(sizes)

    def navigateToFrame(self, frame):
        """导航到指定Frame"""
        if frame.isVisible() and not frame.is_detached:
            # 滚动到Frame位置
            self.ensureWidgetVisible(frame)
            # 简单的高亮效果
            self.highlightFrame(frame)

    def highlightFrame(self, frame):
        """高亮指定Frame"""
        original_style = frame.styleSheet()
        highlight_style = """
            QFrame {
                
                background-color: #fff9c4;
                border-radius: 3px;
            }
        """
        frame.setStyleSheet(highlight_style)
        QTimer.singleShot(1000, lambda: frame.setStyleSheet(original_style))

    def onFrameDetached(self, frame):
        frame.updateStatus("detached")
        self.tab_navigator.updateFrameStatus(frame, True)

    def onFrameAttached(self, frame):
        frame.updateStatus("attached")
        self.tab_navigator.updateFrameStatus(frame, False)

        # 重新排列splitter
        self.rearrangeSplitter()

    def rearrangeSplitter(self):
        """重新排列QSplitter中的widgets"""
        # 收集所有可见的Frame
        visible_frames = [frame for frame in self.frames if frame.isVisible() and not frame.is_detached]

        # 从splitter中移除所有widgets
        for frame in self.frames:
            if frame.parent() == self.container:
                frame.setParent(None)

        # 重新添加可见的frames到splitter
        for frame in visible_frames:
            self.container_layout.addWidget(frame)

    def onTabOrderChanged(self, new_frame_order):
        """响应Tab重排序事件，重新排列Frame"""
        logger.info(f"Tab重排序，新顺序: {[f.title for f in new_frame_order]}")

        # 更新frames列表
        self.frames = new_frame_order.copy()

        # 重新排列splitter
        self.rearrangeSplitter()

    def remove_all(self):
        """将界面恢复到初始状态"""
        # 移除所有Frame
        for frame in self.frames:
            frame.hide()
            self.tab_navigator.removeFrame(frame)
            frame.setParent(None)
            frame.deleteLater()
        self.frames.clear()

    def setSplitterSizes(self, sizes):
        """设置splitter中各个widget的大小"""
        if len(sizes) == len(self.frames):
            self.container_layout.setSizes(sizes)

    def getSplitterSizes(self):
        """获取splitter中各个widget的当前大小"""
        return self.container_layout.sizes()


# 示例使用
if __name__ == "__main__":
    app = QApplication(sys.argv)

    # 创建一些测试用的 QWidget 并添加内容
    widget1 = QWidget()
    widget1.setWindowTitle("数据面板")
    layout1 = QVBoxLayout(widget1)
    layout1.addWidget(QLabel("这是数据面板的内容"))
    layout1.addWidget(QLabel("包含各种数据显示组件"))
    widget1.setMinimumSize(350, 400)

    widget2 = QWidget()
    widget2.setWindowTitle("控制面板")
    layout2 = QVBoxLayout(widget2)
    layout2.addWidget(QLabel("这是控制面板的内容"))
    layout2.addWidget(QLabel("包含各种控制按钮"))
    widget2.setMinimumSize(400, 450)

    widget3 = QWidget()
    widget3.setWindowTitle("设置面板")
    layout3 = QVBoxLayout(widget3)
    layout3.addWidget(QLabel("这是设置面板的内容"))
    layout3.addWidget(QLabel("包含各种设置选项"))
    widget3.setMinimumSize(250, 200)

    widget4 = QWidget()
    widget4.setWindowTitle("监控面板")
    layout4 = QVBoxLayout(widget4)
    layout4.addWidget(QLabel("这是监控面板的内容"))
    layout4.addWidget(QLabel("包含各种监控信息"))
    widget4.setMinimumSize(250, 200)

    widget5 = QWidget()
    widget5.setWindowTitle("日志面板")
    layout5 = QVBoxLayout(widget5)
    layout5.addWidget(QLabel("这是日志面板的内容"))
    layout5.addWidget(QLabel("包含各种日志信息"))
    widget5.setMinimumSize(250, 200)

    widget6 = QWidget()
    widget6.setWindowTitle("统计面板")
    layout6 = QVBoxLayout(widget6)
    layout6.addWidget(QLabel("这是统计面板的内容"))
    layout6.addWidget(QLabel("包含各种统计图表"))
    widget6.setMinimumSize(250, 200)

    widget7 = QWidget()
    widget7.setWindowTitle("统计面板7")
    layout7 = QVBoxLayout(widget7)
    layout7.addWidget(QLabel("这是统计面板的内容"))
    layout7.addWidget(QLabel("包含各种统计图表"))
    widget7.setMinimumSize(250, 200)

    widget8 = QWidget()
    widget8.setWindowTitle("统计面板8")
    layout8 = QVBoxLayout(widget8)
    layout8.addWidget(QLabel("这是统计面板的内容"))
    layout8.addWidget(QLabel("包含各种统计图表"))
    widget8.setMinimumSize(250, 200)

    widget9 = QWidget()
    widget9.setWindowTitle("统计面板9")
    layout9 = QVBoxLayout(widget9)
    layout9.addWidget(QLabel("这是统计面板的内容"))
    layout9.addWidget(QLabel("包含各种统计图表"))
    widget9.setMinimumSize(250, 200)

    widget10 = QWidget()
    widget10.setWindowTitle("统计面板10")
    layout10 = QVBoxLayout(widget10)
    layout10.addWidget(QLabel("这是统计面板的内容"))
    layout10.addWidget(QLabel("包含各种统计图表"))
    widget10.setMinimumSize(250, 200)

    widget11 = QWidget()
    widget11.setWindowTitle("统计面板11")
    layout11 = QVBoxLayout(widget11)
    layout11.addWidget(QLabel("这是统计面板的内容"))
    layout11.addWidget(QLabel("包含各种统计图表"))
    widget11.setMinimumSize(250, 200)

    widget12 = QWidget()
    widget12.setWindowTitle("统计面板12")
    layout12 = QVBoxLayout(widget12)
    layout12.addWidget(QLabel("这是统计面板的内容"))
    layout12.addWidget(QLabel("包含各种统计图表"))
    widget12.setMinimumSize(250, 200)

    widget13 = QWidget()
    widget13.setWindowTitle("统计面板13")
    layout13 = QVBoxLayout(widget13)
    layout13.addWidget(QLabel("这是统计面板的内容"))
    layout13.addWidget(QLabel("包含各种统计图表"))
    widget13.setMinimumSize(250, 200)

    widget14 = QWidget()
    widget14.setWindowTitle("统计面板14")
    layout14 = QVBoxLayout(widget14)
    layout14.addWidget(QLabel("这是统计面板的内容"))
    layout14.addWidget(QLabel("包含各种统计图表"))
    widget14.setMinimumSize(250, 200)

    # 创建 DemoWidget 并添加 Frame
    demo_widget = DemoDraggableDockWidget()
    demo_widget.remove_all()
    demo_widget.addFrames(
        [widget1, widget2, widget3, widget4, widget5, widget6, widget7, widget8, widget9, widget10, widget11, widget12,
         widget13, widget14])
    demo_widget.show()

    sys.exit(app.exec())
