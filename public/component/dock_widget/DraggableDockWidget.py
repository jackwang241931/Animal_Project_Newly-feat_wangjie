import sys
from PyQt6.QtWidgets import (QApplication, QMainWindow, QFrame, QVBoxLayout,
                             QHBoxLayout, QLabel, QPushButton, QWidget, QSplitter, QScrollArea)
from PyQt6.QtCore import Qt, QPoint, pyqtSignal, QRect, QTimer, QSize, QPropertyAnimation, QEasingCurve
from PyQt6.QtGui import QMouseEvent, QPainter, QColor, QPen, QCloseEvent, QFont, QIcon, QScreen
from loguru import logger

from theme.ThemeQt6 import ThemedWidget, ThemedFrame, ThemedWindow


# ========================= 核心拖拽框架类 =========================

class DraggableContainer(ThemedWidget):
    """支持拖拽高亮提示的父容器 - 开箱即用"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.is_highlighted = False
        self.original_stylesheet = ""
        self.setupStyle()

    def setupStyle(self):
        """设置朴素的默认样式"""
        self.original_stylesheet = """
            QWidget {
                background-color: #ffffff;
             
                border-radius: 3px;
            }
        """
        self.setStyleSheet(self.original_stylesheet)

    def setHighlight(self, highlight):
        """设置高亮状态"""
        if self.is_highlighted != highlight:
            self.is_highlighted = highlight
            self.updateHighlightStyle()

    def updateHighlightStyle(self):
        """更新朴素的高亮样式"""
        if self.is_highlighted:
            # 朴素的高亮样式 - 灰蓝色边框
            highlight_style = """
                QWidget {
                    background-color: #f5f7fa;
                    border: 2px solid #6c7b7f;
                    border-radius: 4px;
                }
            """
            self.setStyleSheet(highlight_style)

            # 简单的闪烁效果
            self.animation_timer = QTimer()
            self.animation_timer.timeout.connect(self.animateHighlight)
            self.animation_timer.start(800)  # 较慢的闪烁
            self.animation_state = False
        else:
            # 恢复原始样式
            if hasattr(self, 'animation_timer'):
                self.animation_timer.stop()
            self.setStyleSheet(self.original_stylesheet)

    def animateHighlight(self):
        """朴素的动画高亮效果"""
        if not self.is_highlighted:
            return

        self.animation_state = not self.animation_state

        if self.animation_state:
            # 稍微强调的样式
            style = """
                QWidget {
                    background-color: #eef2f5;
                    border: 2px solid #5a6268;
                    border-radius: 4px;
                }
            """
        else:
            # 正常高亮样式
            style = """
                QWidget {
                    background-color: #f5f7fa;
                    border: 2px solid #6c7b7f;
                    border-radius: 4px;
                }
            """

        self.setStyleSheet(style)


class TabButton(QPushButton):
    """可拖拽的Tab按钮 - 带反馈效果"""

    # 定义拖拽相关信号
    dragStarted = pyqtSignal(object)  # 开始拖拽
    dragMoved = pyqtSignal(object, QPoint)  # 拖拽移动
    dragFinished = pyqtSignal(object)  # 拖拽结束

    def __init__(self, title, frame_ref, parent=None):
        super().__init__(title, parent)
        self.frame_ref = frame_ref
        self.setFixedHeight(32)
        self.setMinimumWidth(100)
        self.setMaximumWidth(180)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self.setToolTip("可拖拽调整顺序")
        # 拖拽相关属性
        self.is_dragging = False
        self.drag_start_position = QPoint()
        self.drag_threshold = 10
        self.original_opacity = 1.0

        # 拖拽反馈相关
        self.drag_preview = None
        self.original_z_value = 0

        # 设置样式
        self.setupStyle()

        # 添加状态指示器
        self.setText(f"● {title}")

    def setupStyle(self):
        """设置朴素样式"""
        self.setStyleSheet("""
            QPushButton {
                background-color: #f8f9fa;
              
                border-radius: 3px;
                padding: 6px 10px;
                font-weight: normal;
                color: #343a40;
                text-align: center;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #e9ecef;
                border-color: #adb5bd;
            }
            QPushButton:pressed {
                background-color: #dee2e6;
                color: #495057;
                border-color: #6c757d;
            }
        """)

    def setDragStyle(self, is_dragging):
        """设置朴素的拖拽状态样式"""
        if is_dragging:
            self.setStyleSheet("""
                QPushButton {
                    background-color: #e9ecef;
                    border: 2px solid #6c757d;
                    border-radius: 3px;
                    padding: 6px 10px;
                    font-weight: bold;
                    color: #495057;
                    text-align: center;
                    font-size: 11px;
                }
            """)
            # 提升层级以便在其他按钮上方显示
            self.raise_()
        else:
            self.setupStyle()

    def createDragPreview(self):
        """创建拖拽预览"""
        preview = QLabel(self.text())
        preview.setStyleSheet("""
            QLabel {
                background-color: #f8f9fa;
              
                border-radius: 3px;
                padding: 6px 10px;
                color: #6c757d;
                font-size: 11px;
            }
        """)
        preview.setFixedSize(self.size())
        preview.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        return preview

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.drag_start_position = event.pos()
            # 延迟判断是否开始拖拽，避免影响点击事件
            self.click_timer = QTimer()
            self.click_timer.setSingleShot(True)
            self.click_timer.timeout.connect(self.checkDragStart)
            self.click_timer.start(150)  # 增加延迟以区分点击和拖拽
        super().mousePressEvent(event)

    def checkDragStart(self):
        """检查是否开始拖拽"""
        if not self.drag_start_position.isNull():
            # 如果鼠标还在按下状态，准备开始拖拽
            self.is_dragging = True
            self.setDragStyle(True)
            self.dragStarted.emit(self)

    def mouseMoveEvent(self, event):
        if (self.is_dragging and
                event.buttons() == Qt.MouseButton.LeftButton and
                not self.drag_start_position.isNull()):

            # 计算移动距离
            distance = (event.pos() - self.drag_start_position).manhattanLength()

            if distance > self.drag_threshold:
                # 发送拖拽移动信号
                global_pos = self.mapToGlobal(event.pos())
                self.dragMoved.emit(self, global_pos)

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if hasattr(self, 'click_timer'):
            self.click_timer.stop()

        if self.is_dragging:
            self.is_dragging = False
            self.setDragStyle(False)
            self.dragFinished.emit(self)

        self.drag_start_position = QPoint()
        super().mouseReleaseEvent(event)


class TabNavigator(ThemedWidget):
    """支持拖拽重排序的Tab导航栏组件 - 带反馈效果"""

    tabClicked = pyqtSignal(object)  # 发送被点击的frame对象
    tabOrderChanged = pyqtSignal(list)  # 发送新的frame顺序列表

    def __init__(self, parent=None):
        super().__init__(parent)
        self.tab_buttons = {}  # frame -> button的映射
        # Keep delayed tab-style callbacks owned by this navigator.  A static
        # QTimer.singleShot callback can outlive a TabButton that is removed
        # during a page switch and then call into a deleted C++ QWidget.
        self._tab_style_timers = {}
        self._tab_removal_timers = {}
        self.frame_order = []  # 维护frame的顺序
        self.drag_indicator = None  # 拖拽指示器
        self.dragging_button = None  # 当前拖拽的按钮
        self.setupUI()

    def setupUI(self):
        self.setFixedHeight(45)

        self.setStyleSheet("""
            QWidget {
                background-color: #f1f3f4;
           
                border-radius: 3px;
            }
        """)

        # 主布局
        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(8, 6, 8, 6)
        main_layout.setSpacing(8)

        # # 标题
        # title_label = QLabel("📋 导航:")
        # title_label.setStyleSheet("""
        #     QLabel {
        #         color: #495057;
        #         font-weight: bold;
        #         font-size: 12px;
        #         background: transparent;
        #         border: none;
        #         padding: 4px;
        #     }
        # """)
        # main_layout.addWidget(title_label)

        # Tab按钮容器（支持横向滚动）
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:horizontal {
                background: #e9ecef;
                height: 6px;
                border-radius: 3px;
            }
            QScrollBar::handle:horizontal {
                background: #adb5bd;
                border-radius: 3px;
                min-width: 20px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #868e96;
            }
        """)

        # Tab按钮容器widget
        self.tab_container = QWidget()
        self.tab_layout = QHBoxLayout(self.tab_container)
        self.tab_layout.setContentsMargins(0, 0, 0, 0)
        self.tab_layout.setSpacing(4)
        self.tab_layout.addStretch()  # 添加弹性空间

        self.scroll_area.setWidget(self.tab_container)
        main_layout.addWidget(self.scroll_area)

        # 状态标签
        self.status_label = QLabel("0个Frame")
        self.status_label.setStyleSheet("""
            QLabel {
                color: #6c757d;
                font-size: 10px;
                background: transparent;
                border: none;
                padding: 4px;
            }
        """)
        main_layout.addWidget(self.status_label)

        # 创建拖拽指示器
        self.createDragIndicator()

    def createDragIndicator(self):
        """创建朴素的拖拽插入位置指示器"""
        self.drag_indicator = QLabel()
        self.drag_indicator.setFixedSize(2, 28)
        self.drag_indicator.setStyleSheet("""
            QLabel {
                background-color: #6c757d;
                border-radius: 1px;
            }
        """)
        self.drag_indicator.hide()
        # 将指示器添加到tab_container，但不在布局中
        self.drag_indicator.setParent(self.tab_container)

    def addFrame(self, frame):
        """添加Frame对应的Tab"""
        if frame in self.tab_buttons:
            return  # 已存在

        # 创建Tab按钮
        tab_button = TabButton(frame.title, frame)
        tab_button.clicked.connect(lambda: self.tabClicked.emit(frame))

        # 连接拖拽相关信号
        tab_button.dragStarted.connect(self.onTabDragStarted)
        tab_button.dragMoved.connect(self.onTabDragMoved)
        tab_button.dragFinished.connect(self.onTabDragFinished)

        # 在弹性空间前插入按钮
        button_count = self.tab_layout.count() - 1  # 减去stretch
        self.tab_layout.insertWidget(button_count, tab_button)

        self.tab_buttons[frame] = tab_button
        self.frame_order.append(frame)
        self.updateStatus()

        # 添加入场动画
        self.animateTabEntry(tab_button)

    def animateTabEntry(self, tab_button):
        """Tab入场动画"""
        # 简单的淡入效果
        tab_button.setStyleSheet(tab_button.styleSheet() + "background-color: #dee2e6;")
        frame = getattr(tab_button, "frame_ref", None)
        self._cancel_tab_style_timer(frame)

        # Use a QTimer parented to the navigator instead of the static
        # singleShot overload.  The timer is destroyed together with the
        # navigator and is cancelled when the corresponding frame is removed.
        timer = QTimer(self)
        timer.setSingleShot(True)

        def restore_style():
            self._tab_style_timers.pop(frame, None)
            current_button = self.tab_buttons.get(frame)
            if current_button is not tab_button:
                timer.deleteLater()
                return
            try:
                tab_button.setupStyle()
            except RuntimeError:
                # The underlying QPushButton may have been deleted by Qt
                # during a queued page teardown.  Nothing remains to style.
                pass
            timer.deleteLater()

        timer.timeout.connect(restore_style)
        self._tab_style_timers[frame] = timer
        timer.start(100)

    def _cancel_tab_style_timer(self, frame):
        """Cancel a pending style callback for ``frame`` if one exists."""
        timer = self._tab_style_timers.pop(frame, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    def _cancel_tab_removal_timer(self, frame):
        """Cancel a pending delayed removal for ``frame`` if one exists."""
        timer = self._tab_removal_timers.pop(frame, None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()

    def removeFrame(self, frame):
        """移除Frame对应的Tab"""
        if frame not in self.tab_buttons:
            return

        button = self.tab_buttons[frame]
        self._cancel_tab_style_timer(frame)
        self._cancel_tab_removal_timer(frame)

        # 添加退场动画
        button.setStyleSheet("""
            QPushButton {
                background-color: #f8d7da;
               
                border-radius: 3px;
                padding: 6px 10px;
                color: #721c24;
                font-size: 11px;
            }
        """)

        # 延迟删除
        timer = QTimer(self)
        timer.setSingleShot(True)

        def finalize():
            self._tab_removal_timers.pop(frame, None)
            if self.tab_buttons.get(frame) is button:
                self.finalizeRemoval(frame, button)
            timer.deleteLater()

        timer.timeout.connect(finalize)
        self._tab_removal_timers[frame] = timer
        timer.start(300)

    def finalizeRemoval(self, frame, button):
        """完成Tab移除"""
        self._cancel_tab_style_timer(frame)
        self._cancel_tab_removal_timer(frame)
        # The delayed removal can race with a second removal or a page
        # teardown.  Ignore stale callbacks rather than touching a deleted
        # button or deleting the dictionary entry twice.
        if self.tab_buttons.get(frame) is not button:
            return
        self.tab_layout.removeWidget(button)
        button.deleteLater()
        del self.tab_buttons[frame]

        if frame in self.frame_order:
            self.frame_order.remove(frame)

        self.updateStatus()

    def onTabDragStarted(self, tab_button):
        """Tab开始拖拽"""
        logger.info(f"开始拖拽Tab: {tab_button.frame_ref.title}")
        self.dragging_button = tab_button

        # 显示拖拽指示器
        self.drag_indicator.show()

        # 为其他Tab添加潜在目标样式
        self.highlightPotentialTargets(True)

    def onTabDragMoved(self, tab_button, global_pos):
        """Tab拖拽移动"""
        # 将全局坐标转换为tab_container的本地坐标
        local_pos = self.tab_container.mapFromGlobal(global_pos)

        # 寻找插入位置
        insert_index = self.findInsertPosition(local_pos.x(), tab_button)

        # 更新拖拽指示器位置
        self.updateDragIndicator(insert_index, tab_button)

        # 实时预览排序效果
        self.previewReorder(tab_button, insert_index)

    def onTabDragFinished(self, tab_button):
        """Tab拖拽结束"""
        logger.info(f"结束拖拽Tab: {tab_button.frame_ref.title}")

        # 隐藏拖拽指示器
        self.drag_indicator.hide()
        self.dragging_button = None

        # 移除其他Tab的高亮
        self.highlightPotentialTargets(False)

        # 获取鼠标当前位置
        global_pos = self.cursor().pos()
        local_pos = self.tab_container.mapFromGlobal(global_pos)

        # 计算新的插入位置
        new_index = self.findInsertPosition(local_pos.x(), tab_button)
        current_index = self.frame_order.index(tab_button.frame_ref)

        if new_index != current_index:
            # 重新排序
            self.reorderTabs(tab_button.frame_ref, new_index)
        else:
            # 如果位置没变，恢复所有按钮样式
            self.resetAllTabStyles()

    def highlightPotentialTargets(self, highlight):
        """高亮显示潜在的拖放目标"""
        for button in self.tab_buttons.values():
            if button != self.dragging_button:
                if highlight:
                    button.setStyleSheet("""
                        QPushButton {
                            background-color: #e2e3e5;
                           
                            border-radius: 3px;
                            padding: 6px 10px;
                            color: #6c757d;
                            font-size: 11px;
                        }
                    """)
                else:
                    button.setupStyle()

    def previewReorder(self, dragging_button, target_index):
        """实时预览重排序效果"""
        current_index = self.frame_order.index(dragging_button.frame_ref)

        if target_index != current_index:
            # 为目标位置的按钮添加特殊样式
            for i, frame in enumerate(self.frame_order):
                button = self.tab_buttons[frame]
                if button != dragging_button:
                    if i == target_index:
                        # 目标位置高亮
                        button.setStyleSheet("""
                            QPushButton {
                                background-color: #d1ecf1;
                                border: 2px solid #bee5eb;
                                border-radius: 3px;
                                padding: 6px 10px;
                                color: #0c5460;
                                font-size: 11px;
                                font-weight: bold;
                            }
                        """)
                    else:
                        # 其他位置保持淡化样式
                        button.setStyleSheet("""
                            QPushButton {
                                background-color: #e2e3e5;
                             
                                border-radius: 3px;
                                padding: 6px 10px;
                                color: #6c757d;
                                font-size: 11px;
                            }
                        """)

    def resetAllTabStyles(self):
        """重置所有Tab样式"""
        for button in self.tab_buttons.values():
            button.setupStyle()

    def findInsertPosition(self, x_pos, dragging_button):
        """根据鼠标X位置寻找插入位置"""
        button_count = self.tab_layout.count() - 1  # 减去stretch

        for i in range(button_count):
            widget = self.tab_layout.itemAt(i).widget()
            if widget and widget != dragging_button:
                widget_center = widget.x() + widget.width() / 2
                if x_pos < widget_center:
                    return i

        return button_count  # 插入到最后

    def updateDragIndicator(self, insert_index, dragging_button):
        """更新拖拽指示器位置"""
        button_count = self.tab_layout.count() - 1  # 减去stretch

        if insert_index == 0:
            # 插入到第一个位置
            first_widget = self.tab_layout.itemAt(0).widget()
            if first_widget:
                x = first_widget.x() - 1
            else:
                x = 4
        elif insert_index >= button_count:
            # 插入到最后位置
            last_widget = self.tab_layout.itemAt(button_count - 1).widget()
            if last_widget:
                x = last_widget.x() + last_widget.width() + 1
            else:
                x = self.tab_container.width() - 4
        else:
            # 插入到中间位置
            widget = self.tab_layout.itemAt(insert_index).widget()
            if widget:
                x = widget.x() - 1
            else:
                x = 4

        # 设置指示器位置
        self.drag_indicator.move(x, 2)

    def reorderTabs(self, frame, new_index):
        """重新排序Tab和Frame - 带动画反馈"""
        current_index = self.frame_order.index(frame)

        if current_index == new_index:
            return

        # 显示排序反馈
        self.showReorderFeedback(frame, current_index, new_index)

        # 更新frame_order
        self.frame_order.pop(current_index)
        self.frame_order.insert(new_index, frame)

        # 重新排列UI中的Tab按钮
        self.rebuildTabLayout()

        # 发送顺序改变信号
        self.tabOrderChanged.emit(self.frame_order.copy())

        logger.info(f"Tab重排序完成: {[f.title for f in self.frame_order]}")

    def showReorderFeedback(self, frame, old_index, new_index):
        """显示重排序反馈"""
        button = self.tab_buttons[frame]

        # 临时高亮被移动的Tab
        button.setStyleSheet("""
            QPushButton {
                background-color: #d4edda;
                border: 2px solid #c3e6cb;
                border-radius: 3px;
                padding: 6px 10px;
                color: #155724;
                font-size: 11px;
                font-weight: bold;
            }
        """)

        # 短暂延迟后恢复样式
        QTimer.singleShot(500, button.setupStyle)

    def rebuildTabLayout(self):
        """根据frame_order重建Tab布局"""
        # 暂时移除所有Tab按钮（除了stretch）
        buttons_to_readd = []

        # 从后往前移除，避免索引问题
        for i in range(self.tab_layout.count() - 2, -1, -1):  # 跳过最后的stretch
            item = self.tab_layout.itemAt(i)
            if item and item.widget():
                widget = item.widget()
                self.tab_layout.removeWidget(widget)
                buttons_to_readd.append(widget)

        # 按照新顺序重新添加
        for frame in self.frame_order:
            if frame in self.tab_buttons:
                button = self.tab_buttons[frame]
                button_count = self.tab_layout.count() - 1  # 减去stretch
                self.tab_layout.insertWidget(button_count, button)

    def updateFrameStatus(self, frame, is_detached):
        """更新Frame状态显示"""
        if frame not in self.tab_buttons:
            return

        button = self.tab_buttons[frame]
        if is_detached:
            # 隐藏分离的Frame Tab
            button.hide()
            button.setText(f"○ {frame.title}")  # 空心圆表示分离
        else:
            # 显示附加的Frame Tab
            button.show()
            button.setText(f"● {frame.title}")  # 实心圆表示附加

        self.updateStatus()

    def updateStatus(self):
        """更新状态显示"""
        visible_count = sum(1 for button in self.tab_buttons.values() if button.isVisible())
        total_count = len(self.tab_buttons)

        if visible_count == total_count:
            self.status_label.setText(f"{total_count}个")
            self.status_label.setStyleSheet("""
                QLabel {
                    color: #28a745;
                    font-size: 10px;
                    font-weight: bold;
                    background: transparent;
                    border: none;
                    padding: 4px;
                }
            """)
        else:
            self.status_label.setText(f"{visible_count}/{total_count}个")
            self.status_label.setStyleSheet("""
                QLabel {
                    color: #ffc107;
                    font-size: 10px;
                    font-weight: bold;
                    background: transparent;
                    border: none;
                    padding: 4px;
                }
            """)


class DraggableFrame(ThemedFrame):
    """可拖拽的Frame组件 - 朴素样式"""
    frameDetached = pyqtSignal(object)
    frameAttached = pyqtSignal(object)

    def __init__(self, title="", content_widget=None, parent=None, enable_detach=True):
        super().__init__(parent)
        self.title = title
        self.content_widget = content_widget  # 自定义内容组件
        self.enable_detach = enable_detach
        self.is_detached = False
        self.detached_window = None
        self.original_parent = parent

        # 保存内容组件的原始布局信息
        self.original_layout = None
        self.content_widget_index = -1

        # 拖拽状态
        self.drag_start_position = QPoint()
        self.is_dragging = False
        self.drag_threshold = 30
        # 拖拽状态样式
        self.normal_style = """
                    QFrame {
                        background-color: #f8f9fa;
                        border: 2px dashed #dee2e6;
                        border-radius: 8px;
                        margin: 4px;
                    }
                    QLabel {
                        color: #6c757d;
                        font-size: 16px;
                        font-weight: bold;
                        background: transparent;
                     
                        border-radius: 2px;
                        padding: 4px;
                        letter-spacing: 1px;
                    }
                    QLabel:hover {
                        color: #495057;
                        background: #f8f9fa;
                     
                    }
                """

        self.dragging_style = """
                    QFrame {
                        background-color: #e3f2fd;
                        border: 2px dashed #2196f3;
                        border-radius: 8px;
                        margin: 4px;
                    }
                    QLabel {
                        color: #495057;
                        font-size: 16px;
                        font-weight: bold;
                        background: #dee2e6;
                      
                        border-radius: 2px;
                        padding: 4px;
                        letter-spacing: 1px;
                    }
                """
        self.setupUI()
        self.setMinimumSize(220, 280)

    def setupUI(self):
        self.setFrameStyle(QFrame.Shape.Box)
        self.setLineWidth(1)
        self.setStyleSheet("""
            QFrame {
               
                background-color: white;
                border-radius: 3px;
            }
        """)

        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # 朴素的标题栏
        self.title_bar = QFrame()
        self.title_bar.setFixedHeight(30)
        self.title_bar.setStyleSheet("""
            QFrame {
                background-color: #e9ecef;
                border: none;
                border-top-left-radius: 3px;
                border-top-right-radius: 3px;
            }
        """)
        self.title_bar.setCursor(Qt.CursorShape.OpenHandCursor)

        title_layout = QHBoxLayout(self.title_bar)
        title_layout.setContentsMargins(10, 0, 10, 0)
        # 朴素的拖拽图标 - 这是唯一触发拖拽检测的区域
        self.drag_icon = QLabel("⋮⋮⋮⋮拖拽处⋮⋮⋮⋮")
        self.drag_icon.setStyleSheet("""
                    QLabel {
                        color: #6c757d;

                        background: transparent;
                        border: none;
                        padding: 4px;
                        letter-spacing: 1px;
                    }
                """)
        self.drag_icon.setCursor(Qt.CursorShape.OpenHandCursor)
        self.drag_icon.setToolTip("拖拽此图标进行拖拽检测")
        title_layout.addWidget(self.drag_icon)
        if not self.enable_detach:
            self.drag_icon.hide()
            self.title_bar.setCursor(Qt.CursorShape.ArrowCursor)
        self.title_label = QLabel(self.title)
        self.title_label.setStyleSheet("color: #495057; font-weight: bold; font-size: 12px;")
        title_layout.addWidget(self.title_label)
        title_layout.addStretch()

        # 朴素的状态指示器
        self.status_indicator = QLabel("●")
        self.status_indicator.setStyleSheet("color: #28a745; font-size: 14px;")
        title_layout.addWidget(self.status_indicator)

        # 内容区域
        self.content_container = QWidget()
        self.content_container.setStyleSheet("""
            QWidget {
                background-color: #fdfdfd;
                border-bottom-left-radius: 3px;
                border-bottom-right-radius: 3px;
            }
        """)
        self.content_layout = QVBoxLayout(self.content_container)
        self.content_layout.setContentsMargins(12, 12, 12, 12)

        # 如果提供了自定义内容组件,使用它;否则使用默认内容
        if self.content_widget:
            self.content_layout.addWidget(self.content_widget)
        else:
            self.setupDefaultContent(self.content_layout)

        layout.addWidget(self.title_bar)
        layout.addWidget(self.content_container)
        self.setLayout(layout)

    def setupDefaultContent(self, layout):
        """设置朴素的默认内容"""
        layout.addWidget(QLabel(f"内容: {self.title}"))
        btn = QPushButton("测试按钮")
        btn.setMaximumWidth(100)
        btn.setStyleSheet("""
            QPushButton {
                background-color: #6c757d;
                color: white;
                border: none;
                padding: 5px 10px;
                border-radius: 2px;
                font-size: 11px;
            }
            QPushButton:hover {
                background-color: #5a6268;
            }
        """)
        btn.clicked.connect(lambda: logger.info(f"{self.title} 按钮被点击"))
        layout.addWidget(btn)
        layout.addStretch()

    def setContentWidget(self, widget):
        """设置自定义内容组件"""
        if self.content_widget:
            self.content_layout.removeWidget(self.content_widget)
            self.content_widget.setParent(None)

        self.content_widget = widget
        self.content_layout.addWidget(widget)

    def saveContentState(self):
        """保存内容组件的布局状态"""
        if self.content_widget and self.content_widget.parent():
            parent_layout = self.content_widget.parent().layout()
            if parent_layout:
                self.original_layout = parent_layout
                for i in range(parent_layout.count()):
                    if parent_layout.itemAt(i).widget() == self.content_widget:
                        self.content_widget_index = i
                        break

    def restoreContentToFrame(self):
        """将内容组件恢复到Frame中"""
        if self.content_widget:
            # 从当前父组件移除
            current_parent = self.content_widget.parent()
            if current_parent and current_parent.layout():
                current_parent.layout().removeWidget(self.content_widget)

            # 重新添加到Frame的内容容器中
            self.content_widget.setParent(self.content_container)
            self.content_layout.addWidget(self.content_widget)

            logger.info(f"内容组件已恢复到Frame: {self.title}")

    def mousePressEvent(self, event):
        if not self.enable_detach:
            return super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            # 检查是否点击在标题栏
            title_rect = QRect(0, 0, self.width(), 30)
            if title_rect.contains(event.pos()):
                self.drag_start_position = event.globalPosition().toPoint()
                self.is_dragging = True
                self.title_bar.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not self.enable_detach:
            return super().mouseMoveEvent(event)
        if (self.is_dragging and
                event.buttons() == Qt.MouseButton.LeftButton and
                not self.drag_start_position.isNull()):

            current_pos = event.globalPosition().toPoint()
            distance = (current_pos - self.drag_start_position).manhattanLength()

            if distance > self.drag_threshold and not self.is_detached:
                # 分离Frame
                self.detachFrame(current_pos)

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self.is_dragging:
            self.is_dragging = False
            self.drag_start_position = QPoint()
            self.title_bar.setCursor(Qt.CursorShape.OpenHandCursor)

        super().mouseReleaseEvent(event)

    def detachFrame(self, global_pos):
        """分离Frame为独立窗口"""
        if self.is_detached:
            return

        try:
            # 保存内容状态
            self.saveContentState()

            # 找到原Frame所在的父容器
            parent_widget = self.parent()

            # 创建独立窗口
            self.detached_window = DetachableWindow(self, parent_widget)
            # 确保新窗口的拖拽图标处于正常状态
            if hasattr(self.detached_window, 'custom_title_bar'):
                self.detached_window.custom_title_bar.resetToNormalState()
            # 设置位置 - 确保鼠标在标题栏合适位置
            # 设置窗口位置 - 以鼠标位置为中心
            window_width, window_height = self.detached_window.width(), self.detached_window.height()
            window_x = global_pos.x() - window_width // 2
            window_y = global_pos.y() - window_height // 2
            if window_x < 0:
                window_x = 0
            if window_y < 0:
                window_y = 0
            self.detached_window.move(window_x, window_y)
            self.detached_window.resize(self.width(),self.height())
            # 继承当前的拖拽状态到新窗口
            self.detached_window.start_dragging_from_detach(
                global_pos,
                self.drag_start_position
            )

            self.detached_window.show()
            # 清理拖拽状态
            self.is_dragging = False
            self.setCursor(Qt.CursorShape.ArrowCursor)
            # 隐藏原Frame
            self.hide()
            self.is_detached = True

            self.frameDetached.emit(self)

        except Exception as e:
            logger.error(f"分离Frame时出错: {e}")

    def attachFrame(self):
        """重新附加Frame"""
        if not self.is_detached:
            return

        try:
            # 先恢复内容组件到Frame
            self.restoreContentToFrame()

            # 关闭独立窗口
            if self.detached_window:
                self.detached_window.close_and_attach()
                self.detached_window = None

            # 显示原Frame
            self.show()
            self.is_detached = False

            # 重置拖拽状态
            self.is_dragging = False
            self.drag_start_position = QPoint()
            self.title_bar.setCursor(Qt.CursorShape.OpenHandCursor)

            self.frameAttached.emit(self)
            logger.info(f"Frame已重新附加: {self.title}")

        except Exception as e:
            logger.error(f"附加Frame时出错: {e}")

    def updateStatus(self, status):
        """更新朴素的状态显示"""
        if status == "detached":
            self.status_indicator.setStyleSheet("color: #dc3545; font-size: 14px;")
            self.title_bar.setStyleSheet("""
                QFrame {
                    background-color: #f8d7da;
                    border: none;
                    border-top-left-radius: 3px;
                    border-top-right-radius: 3px;
                }
            """)
        else:  # attached
            self.status_indicator.setStyleSheet("color: #28a745; font-size: 14px;")
            self.title_bar.setStyleSheet("""
                QFrame {
                    background-color: #e9ecef;
                    border: none;
                    border-top-left-radius: 3px;
                    border-top-right-radius: 3px;
                }
            """)


class CustomTitleBar(ThemedFrame):
    """朴素的自定义标题栏"""

    def __init__(self, window, title, parent=None):
        super().__init__(parent)
        self.window = window
        self.title = title
        self.is_maximized = False

        # 窗口拖拽相关 - 区分拖拽图标和标题栏拖拽
        self.drag_position = QPoint()
        self.is_icon_dragging = False  # 拖拽图标进行的拖拽
        self.is_title_bar_dragging = False  # 标题栏区域拖拽

        self.setupUI()
        # 确保初始状态正确
        self.resetToNormalState()

    def resetToNormalState(self):
        """重置到正常状态"""

        self.is_icon_dragging = False
        self.is_title_bar_dragging = False

        # 重置拖拽图标样式
        if hasattr(self, 'drag_icon'):
            self.drag_icon.setStyleSheet("""
                   QLabel {
                       color: #6c757d;
                       
                       background: transparent;
                      
                       border-radius: 2px;
                       padding: 4px;
                       letter-spacing: 1px;
                   }
                   QLabel:hover {
                       color: #495057;
                       background: #f8f9fa;
                      
                   }
               """)
            self.drag_icon.setCursor(Qt.CursorShape.OpenHandCursor)

        # 重置光标
        self.setCursor(Qt.CursorShape.ArrowCursor)
        # 确保停止拖拽检查定时器
        if hasattr(self.window, 'drop_check_timer'):
            self.window.drop_check_timer.stop()# 确保停止拖拽检查定时器

    def setupUI(self):
        self.setFixedHeight(35)
        self.setStyleSheet("""
            QFrame {
                background-color: #f1f3f4;
                border: none;
                border-top-left-radius: 4px;
                border-top-right-radius: 4px;
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 0, 4, 0)
        layout.setSpacing(8)

        # 朴素的拖拽图标 - 这是唯一触发拖拽检测的区域
        self.drag_icon = QLabel("⋮⋮⋮⋮拖拽处⋮⋮⋮⋮")
        self.drag_icon.setStyleSheet("""
            QLabel {
                color: #6c757d;
              
                background: transparent;
                border: none;
                padding: 4px;
                letter-spacing: 1px;
            }
        """)
        self.drag_icon.setCursor(Qt.CursorShape.OpenHandCursor)
        self.drag_icon.setToolTip("拖拽此图标进行拖拽检测")
        layout.addWidget(self.drag_icon)

        # 标题
        self.title_label = QLabel(self.title)
        self.title_label.setStyleSheet("""
            QLabel {
                margin-left: 10px;
                color: #495057;
                font-weight: bold;
                font-size: 13px;
                background: transparent;
                border: none;
            }
        """)
        self.title_label.setCursor(Qt.CursorShape.SizeAllCursor)  # 表示可以移动窗口
        self.title_label.setToolTip("拖拽此处移动窗口")
        layout.addWidget(self.title_label)

        layout.addStretch()

        # 朴素的状态指示器
        self.status_indicator = QLabel("●")
        self.status_indicator.setStyleSheet("color: #ffc107; font-size: 14px; background: transparent; border: none;")
        layout.addWidget(self.status_indicator)

        # 朴素的控制按钮
        button_style = """
            QPushButton {
                background-color: transparent;
                border: none;
                color: #6c757d;
                font-size: 14px;
                font-weight: bold;
                padding: 4px;
                min-width: 26px;
                min-height: 26px;
            }
            QPushButton:hover {
                background-color: #e9ecef;
                border-radius: 2px;
            }
        """

        # 最小化按钮
        self.minimize_btn = QPushButton("−")
        self.minimize_btn.setStyleSheet(button_style)
        self.minimize_btn.clicked.connect(self.window.showMinimized)
        self.minimize_btn.setToolTip("最小化")
        layout.addWidget(self.minimize_btn)

        # 最大化/还原按钮
        self.maximize_btn = QPushButton("□")
        self.maximize_btn.setStyleSheet(button_style)
        self.maximize_btn.clicked.connect(self.toggle_maximize)
        self.maximize_btn.setToolTip("最大化")
        layout.addWidget(self.maximize_btn)

        # 关闭按钮
        self.close_btn = QPushButton("×")
        close_button_style = button_style + """
            QPushButton:hover {
                background-color: #dc3545;
                color: white;
                border-radius: 2px;
            }
        """
        self.close_btn.setStyleSheet(close_button_style)
        self.close_btn.clicked.connect(self.closeWindow)
        self.close_btn.setToolTip("关闭 (重新附加)")
        layout.addWidget(self.close_btn)

    def closeWindow(self):
        """关闭窗口"""
        self.window.attach_to_main()

    def toggle_maximize(self):
        """切换最大化状态"""
        if self.window.isMaximized():
            self.window.showNormal()
            self.maximize_btn.setText("□")
            self.maximize_btn.setToolTip("最大化")
            self.is_maximized = False
        else:
            self.window.showMaximized()
            self.maximize_btn.setText("❐")
            self.maximize_btn.setToolTip("还原")
            self.is_maximized = True

    def getClickedWidget(self, pos):
        """获取点击位置对应的子控件"""
        for widget in [self.drag_icon, self.title_label, self.minimize_btn, self.maximize_btn, self.close_btn,
                       self.status_indicator]:
            widget_pos = widget.mapToParent(QPoint(0, 0))
            widget_rect = QRect(widget_pos, widget.size())
            if widget_rect.contains(pos):
                return widget
        return None

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            clicked_widget = self.getClickedWidget(event.pos())

            # 检查是否点击在控制按钮上 - 直接传递给按钮处理
            if clicked_widget in [self.minimize_btn, self.maximize_btn, self.close_btn]:
                super().mousePressEvent(event)
                return

            # 点击拖拽图标 - 开始拖拽检测模式
            if clicked_widget == self.drag_icon:
                self.drag_position = event.globalPosition().toPoint() - self.window.pos()
                self.is_icon_dragging = True
                self.drag_icon.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.drag_icon.setStyleSheet("""
                    QLabel {
                        color: #495057;
                       
                        background: #dee2e6;
                   
                        border-radius: 2px;
                        padding: 4px;
                        letter-spacing: 1px;
                    }
                """)
                # 启动拖拽检查定时器
                self.window.drop_check_timer.start(50)
                event.accept()
                return
            else:
                # 点击标题栏其他区域（包括标题文本） - 普通窗口移动
                self.is_icon_dragging = False
                self.drag_position = event.globalPosition().toPoint() - self.window.pos()
                self.is_title_bar_dragging = True
                self.setCursor(Qt.CursorShape.ClosedHandCursor)

                event.accept()

    def mouseMoveEvent(self, event):
        if event.buttons() == Qt.MouseButton.LeftButton:
            # 拖拽图标的移动 - 带拖拽检测
            if self.is_icon_dragging:
                # 如果窗口最大化,先还原
                if self.window.isMaximized():
                    self.toggle_maximize()
                    # 重新计算拖拽位置
                    ratio = event.pos().x() / self.width()
                    new_width = self.window.width()
                    self.drag_position = QPoint(int(new_width * ratio), event.pos().y())

                # 移动窗口
                new_pos = event.globalPosition().toPoint() - self.drag_position
                self.window.move(new_pos)
                event.accept()
                return

            # 标题栏其他区域的移动 - 纯窗口移动，无拖拽检测
            if self.is_title_bar_dragging:
                # 如果窗口最大化,先还原
                if self.window.isMaximized():
                    self.toggle_maximize()
                    # 重新计算拖拽位置
                    ratio = event.pos().x() / self.width()
                    new_width = self.window.width()
                    self.drag_position = QPoint(int(new_width * ratio), event.pos().y())

                # 移动窗口
                new_pos = event.globalPosition().toPoint() - self.drag_position
                self.window.move(new_pos)
                event.accept()

    def mouseReleaseEvent(self, event):
        # 拖拽图标释放
        if self.is_icon_dragging:
            self.is_icon_dragging = False
            self.drag_icon.setCursor(Qt.CursorShape.OpenHandCursor)
            self.drag_icon.setStyleSheet("""
                QLabel {
                    color: #6c757d;
                    
                    background: transparent;
                    border: none;
                    padding: 4px;
                    letter-spacing: 1px;
                }
            """)

            # 停止检查定时器
            if self.window.drop_check_timer.isActive():
                self.window.drop_check_timer.stop()

            # 检查是否在拖拽区域内
            attached = False
            valid_zones = []

            for drop_zone in self.window.drop_zones[:]:
                try:
                    if drop_zone is not None:
                        _ = drop_zone.isVisible()
                        valid_zones.append(drop_zone)

                        if self.window.is_in_drop_zone(drop_zone):
                            self.window.attach_to_main()
                            attached = True
                            break
                except (RuntimeError, AttributeError) as e:
                    logger.error(f"拖拽区域无效: {e}")
                    continue

            # 更新有效的拖拽区域列表
            self.window.drop_zones = valid_zones

            if not attached:
                # 清除所有有效拖拽区域的高亮
                for drop_zone in valid_zones:
                    try:
                        if hasattr(drop_zone, 'setHighlight'):
                            drop_zone.setHighlight(False)
                    except (RuntimeError, AttributeError):
                        continue

        # 标题栏拖拽释放
        if self.is_title_bar_dragging:
            self.is_title_bar_dragging = False
            self.setCursor(Qt.CursorShape.ArrowCursor)

        event.accept()

    def mouseDoubleClickEvent(self, event):
        """双击标题栏切换最大化"""
        if event.button() == Qt.MouseButton.LeftButton:
            clicked_widget = self.getClickedWidget(event.pos())

            # 只有点击标题文本或空白区域才切换最大化，不包括拖拽图标
            if clicked_widget not in [self.minimize_btn, self.maximize_btn, self.close_btn, self.drag_icon]:
                self.toggle_maximize()


class DetachableWindow(ThemedWindow):
    """可分离独立窗口 - 增强拖拽和调整大小功能"""

    def __init__(self, draggable_frame, *drop_zones):
        super().__init__()
        self.draggable_frame = draggable_frame
        # 过滤掉None值，确保只添加有效的拖拽区域
        self.drop_zones = [zone for zone in drop_zones if zone is not None]
        self.should_attach_on_close = True
        self.is_closing = False  # 添加关闭状态标志

        # 窗口调整大小相关变量
        self.resize_border_width = 6  # 调整大小的边框宽度
        self.resize_mode = None  # 当前调整大小模式
        self.resize_start_pos = QPoint()
        self.resize_start_geometry = QRect()

        # 用于检查拖拽区域的定时器 - 只在拖拽图标时启用
        self.drop_check_timer = QTimer()
        self.drop_check_timer.timeout.connect(self.checkDropZone)

        self.setupWindow()
        self.setupUI()
        # 重置拖拽图标状态 - 确保新窗口开始时处于正常状态
        self.resetDragIconState()

    def resetDragIconState(self):
        """重置拖拽图标状态"""
        if hasattr(self, 'custom_title_bar') and self.custom_title_bar:
            # 重置拖拽状态
            self.custom_title_bar.is_icon_dragging = False
            self.custom_title_bar.is_title_bar_dragging = False

            # 重置拖拽图标样式为正常状态
            self.custom_title_bar.drag_icon.setStyleSheet("""
                   QLabel {
                       color: #6c757d;
                    
                       background: transparent;
                     
                       border-radius: 2px;
                       padding: 4px;
                       letter-spacing: 1px;
                   }
                   QLabel:hover {
                       color: #495057;
                       background: #f8f9fa;
                    
                   }
               """)

            # 重置光标
            self.custom_title_bar.drag_icon.setCursor(Qt.CursorShape.OpenHandCursor)
            self.custom_title_bar.setCursor(Qt.CursorShape.ArrowCursor)

            # 停止拖拽检查定时器
            self.drop_check_timer.stop()
    def addDropZone(self, drop_zone):
        """添加拖拽区域 - 增加安全检查"""
        if drop_zone is not None and drop_zone not in self.drop_zones:
            try:
                _ = drop_zone.isVisible()
                self.drop_zones.append(drop_zone)
                logger.info(f"成功添加拖拽区域，当前共有 {len(self.drop_zones)} 个区域")
            except Exception as e:
                logger.error(f"无法添加拖拽区域: {e}")

    def setupWindow(self):
        """设置朴素的窗口属性"""
        # 移除默认标题栏
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)

        # 设置最小尺寸
        self.setMinimumSize(180, 140)
        self.resize(450, 350)

        # 朴素的窗口样式
        self.setStyleSheet("""
            QMainWindow {
                background-color: white;
             
                border-radius: 4px;
            }
        """)

        # 启用鼠标追踪以检测边缘
        self.setMouseTracking(True)

    def setupUI(self):
        """设置朴素的UI"""
        # 创建中央widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # 主布局
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 朴素的自定义标题栏
        self.setWindowTitle(f"{self.draggable_frame.title}")
        self.custom_title_bar = CustomTitleBar(self, f"{self.draggable_frame.title}")
        main_layout.addWidget(self.custom_title_bar)

        # 内容区域 - 直接使用原Frame的内容
        if self.draggable_frame.content_widget:
            # 移动内容组件到独立窗口
            content_widget = self.draggable_frame.content_widget
            # 先从原来的布局中移除
            if content_widget.parent() and content_widget.parent().layout():
                content_widget.parent().layout().removeWidget(content_widget)

            content_widget.setParent(central_widget)
            main_layout.addWidget(content_widget)
            logger.info(f"内容组件已移动到独立窗口: {self.draggable_frame.title}")
        else:
            # 创建朴素的默认内容
            self.setupDefaultContent(main_layout)

    def setupDefaultContent(self, layout):
        """设置朴素的默认内容"""
        content_frame = QFrame()
        content_frame.setStyleSheet("""
            QFrame {
                background-color: #fdfdfd;
                border: none;
                border-bottom-left-radius: 4px;
                border-bottom-right-radius: 4px;
            }
        """)

        content_layout = QVBoxLayout(content_frame)
        content_layout.setContentsMargins(16, 16, 16, 16)
        content_layout.setSpacing(12)

        # 朴素的窗口信息
        info_label = QLabel(f"独立窗口: {self.draggable_frame.title}")
        info_label.setStyleSheet("""
            QLabel {
                color: #495057;
                font-size: 14px;
                font-weight: bold;
                background-color: #f8f9fa;
                padding: 12px;
            
                border-radius: 3px;
            }
        """)
        content_layout.addWidget(info_label)

        # 朴素的功能按钮
        attach_btn = QPushButton("重新附加")
        attach_btn.setStyleSheet("""
            QPushButton {
                background-color: #28a745;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 3px;
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #218838;
            }
        """)
        attach_btn.clicked.connect(self.attach_to_main)
        content_layout.addWidget(attach_btn)

        # 朴素的使用说明
        help_text = QLabel(f"""使用说明：
• 拖拽 ⋮⋮ 图标可以进行拖拽检测
• 拖拽标题文本可以移动窗口
• 鼠标移到边缘可以调整大小
• 共有 {len(self.drop_zones)} 个拖拽区域
• 关闭窗口会自动重新附加""")
        help_text.setStyleSheet("""
            QLabel {
                color: #6c757d;
                font-size: 11px;
                background-color: #f8f9fa;
            
                border-radius: 3px;
                padding: 12px;
                line-height: 1.3;
            }
        """)
        help_text.setWordWrap(True)
        content_layout.addWidget(help_text)

        content_layout.addStretch()
        layout.addWidget(content_frame)

    def start_dragging_from_detach(self, current_pos, start_pos):
        """从分离操作开始拖拽"""
        # 设置为拖拽图标模式
        self.custom_title_bar.is_icon_dragging = True
        title_bar_global_pos = self.custom_title_bar.mapToGlobal(QPoint(0, 0))
        self.custom_title_bar.drag_position = current_pos - title_bar_global_pos
        self.custom_title_bar.drag_icon.setCursor(Qt.CursorShape.ClosedHandCursor)
        self.custom_title_bar.drag_icon.setStyleSheet("""
            QLabel {
                color: #495057;
             
                background: #dee2e6;
               
                border-radius: 2px;
                padding: 4px;
                letter-spacing: 1px;
            }
        """)
        # 启动拖拽检查
        self.drop_check_timer.start(50)

    def getResizeMode(self, pos):
        """根据鼠标位置获取调整大小模式"""
        rect = self.rect()
        border = self.resize_border_width

        # 检查是否在边缘
        left_edge = pos.x() <= border
        right_edge = pos.x() >= rect.width() - border
        top_edge = pos.y() <= border
        bottom_edge = pos.y() >= rect.height() - border

        # 检查是否在标题栏区域（不允许在标题栏区域进行调整大小）
        if pos.y() <= self.custom_title_bar.height():
            return None

        # 返回调整大小模式
        if top_edge and left_edge:
            return 'top_left'
        elif top_edge and right_edge:
            return 'top_right'
        elif bottom_edge and left_edge:
            return 'bottom_left'
        elif bottom_edge and right_edge:
            return 'bottom_right'
        elif left_edge:
            return 'left'
        elif right_edge:
            return 'right'
        elif top_edge:
            return 'top'
        elif bottom_edge:
            return 'bottom'

        return None

    def updateCursor(self, resize_mode):
        """根据调整大小模式更新鼠标指针"""
        if resize_mode == 'top_left' or resize_mode == 'bottom_right':
            self.setCursor(Qt.CursorShape.SizeFDiagCursor)
        elif resize_mode == 'top_right' or resize_mode == 'bottom_left':
            self.setCursor(Qt.CursorShape.SizeBDiagCursor)
        elif resize_mode == 'left' or resize_mode == 'right':
            self.setCursor(Qt.CursorShape.SizeHorCursor)
        elif resize_mode == 'top' or resize_mode == 'bottom':
            self.setCursor(Qt.CursorShape.SizeVerCursor)
        else:
            self.setCursor(Qt.CursorShape.ArrowCursor)

    def mouseMoveEvent(self, event):
        """处理鼠标移动事件"""
        if self.resize_mode:
            # 正在调整大小
            self.performResize(event.globalPosition().toPoint())
        else:
            # 检查是否在调整大小边缘
            resize_mode = self.getResizeMode(event.pos())
            self.updateCursor(resize_mode)
        # 如果是在标题栏拖拽,不进行拖拽区域检查
        if self.custom_title_bar.is_title_bar_dragging:
            return
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event):
        """处理鼠标按下事件"""
        if event.button() == Qt.MouseButton.LeftButton:
            resize_mode = self.getResizeMode(event.pos())
            if resize_mode:
                # 开始调整大小
                self.resize_mode = resize_mode
                self.resize_start_pos = event.globalPosition().toPoint()
                self.resize_start_geometry = self.geometry()
                event.accept()
                return

        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event):
        """处理鼠标释放事件"""
        if self.resize_mode:
            # 结束调整大小
            self.resize_mode = None
            self.updateCursor(None)
            event.accept()
            return

        super().mouseReleaseEvent(event)

    def performResize(self, global_pos):
        """执行窗口大小调整"""
        if not self.resize_mode:
            return

        delta = global_pos - self.resize_start_pos
        new_geometry = QRect(self.resize_start_geometry)

        # 根据调整大小模式计算新的几何形状
        if 'left' in self.resize_mode:
            new_width = max(self.minimumWidth(), new_geometry.width() - delta.x())
            new_geometry.setLeft(new_geometry.right() - new_width)
        elif 'right' in self.resize_mode:
            new_width = max(self.minimumWidth(), new_geometry.width() + delta.x())
            new_geometry.setWidth(new_width)

        if 'top' in self.resize_mode:
            new_height = max(self.minimumHeight(), new_geometry.height() - delta.y())
            new_geometry.setTop(new_geometry.bottom() - new_height)
        elif 'bottom' in self.resize_mode:
            new_height = max(self.minimumHeight(), new_geometry.height() + delta.y())
            new_geometry.setHeight(new_height)

        # 应用新的几何形状
        self.setGeometry(new_geometry)

    def checkDropZone(self):
        """检查是否在拖拽区域内 - 只在拖拽图标时检查"""
        if not self.drop_zones or self.is_closing:
            return

        # 只有在拖拽图标时才进行检查
        if not self.custom_title_bar.is_icon_dragging:
            return

        try:
            # 过滤出有效的拖拽区域
            valid_zones = []
            for drop_zone in self.drop_zones:
                try:
                    if drop_zone is not None and not drop_zone.isHidden():
                        _ = drop_zone.size()
                        valid_zones.append(drop_zone)
                except (RuntimeError, AttributeError) as e:
                    logger.error(f"拖拽区域无效，已跳过: {e}")
                    continue

            # 更新有效的拖拽区域列表
            self.drop_zones = valid_zones

            # 检查所有有效的拖拽区域
            for drop_zone in self.drop_zones:
                try:
                    in_zone = self.is_in_drop_zone(drop_zone)
                    if hasattr(drop_zone, 'setHighlight'):
                        drop_zone.setHighlight(in_zone)
                except Exception as e:
                    logger.error(f"检查拖拽区域时出错: {e}")
                    continue

        except Exception as e:
            logger.error(f"检查拖拽区域总体错误: {e}")
            if self.drop_check_timer.isActive():
                self.drop_check_timer.stop()

    def is_in_drop_zone(self, drop_zone):
        """检查窗口是否在指定拖拽区域内 - 增加安全检查"""
        if not drop_zone or self.is_closing:
            return False

        try:
            if not hasattr(drop_zone, 'mapToGlobal') or not hasattr(drop_zone, 'size'):
                return False

            window_center = self.geometry().center()
            drop_zone_global_pos = drop_zone.mapToGlobal(QPoint(0, 0))
            drop_zone_rect = QRect(drop_zone_global_pos, drop_zone.size())
            return drop_zone_rect.contains(window_center)

        except (RuntimeError, AttributeError) as e:
            logger.error(f"检查区域包含时出错: {e}")
            return False

    def attach_to_main(self):
        """附加到主窗口"""
        if not self.is_closing:
            self.is_closing = True
            self.should_attach_on_close = True
            self.draggable_frame.attachFrame()

    def close_and_attach(self):
        """关闭窗口并附加"""
        self.should_attach_on_close = False
        self.is_closing = True

        if self.drop_check_timer.isActive():
            self.drop_check_timer.stop()

        # 清除所有拖拽区域的高亮
        for drop_zone in self.drop_zones[:]:
            try:
                if drop_zone is not None and hasattr(drop_zone, 'setHighlight'):
                    drop_zone.setHighlight(False)
            except (RuntimeError, AttributeError):
                continue

        self.close()

    def closeEvent(self, event: QCloseEvent):
        """重写关闭事件 - 增加安全检查"""
        if self.should_attach_on_close and not self.is_closing:
            # 防止关闭，而是触发附加
            event.ignore()
            self.attach_to_main()
        else:
            # 真正关闭窗口
            self.is_closing = True

            if self.drop_check_timer.isActive():
                self.drop_check_timer.stop()

            # 安全地清除所有拖拽区域的高亮
            for drop_zone in self.drop_zones[:]:
                try:
                    if drop_zone is not None and hasattr(drop_zone, 'setHighlight'):
                        drop_zone.setHighlight(False)
                except (RuntimeError, AttributeError):
                    continue

            self.drop_zones.clear()
            event.accept()


# ========================= 辅助类 =========================

class DropZoneWidget(DraggableContainer):
    """朴素的拖拽区域组件"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumSize(280, 180)
        self.setAcceptDrops(True)

        self.setupUI()
        self.setupStyle()

    def setupUI(self):
        layout = QVBoxLayout()
        self.label = QLabel()
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.label.setStyleSheet("""
            QLabel {
                color: #6c757d;
                font-size: 13px;
                border: none;
                background: transparent;
            }
        """)
        layout.addWidget(self.label)
        self.setLayout(layout)
        self.updateLabelText()

    def setupStyle(self):
        """设置朴素的拖拽区域样式"""
        self.original_stylesheet = """
            QWidget {
                background-color: #f8f9fa;
                border: 2px dashed #dee2e6;
                border-radius: 4px;
            }
        """
        self.setStyleSheet(self.original_stylesheet)

    def setHighlight(self, highlight):
        """重写父类方法,同时更新标签文本"""
        if self.is_highlighted != highlight:
            self.is_highlighted = highlight
            self.updateHighlightStyle()
            self.updateLabelText()

    def updateLabelText(self):
        """更新标签文本"""
        if self.is_highlighted:
            self.label.setText("松开鼠标以重新附加")
            self.label.setStyleSheet("""
                QLabel {
                    color: #495057;
                    font-size: 14px;
                    font-weight: bold;
                    border: none;
                    background: transparent;
                }
            """)
        else:
            self.label.setText("拖拽区域\n拖拽 ⋮⋮ 图标到这里可以重新附加")
            self.label.setStyleSheet("""
                QLabel {
                    color: #6c757d;
                    font-size: 13px;
                    border: none;
                    background: transparent;
                }
            """)

    def updateHighlightStyle(self):
        """朴素的高亮样式"""
        if self.is_highlighted:
            # 朴素的高亮样式
            highlight_style = """
                QWidget {
                    background-color: #e9ecef;
                    border: 2px solid #6c757d;
                    border-radius: 5px;
                }
            """
            self.setStyleSheet(highlight_style)

            # 简单的闪烁动画
            self.animation_timer = QTimer()
            self.animation_timer.timeout.connect(self.animateHighlight)
            self.animation_timer.start(600)
            self.animation_state = False
        else:
            # 恢复原始样式
            if hasattr(self, 'animation_timer'):
                self.animation_timer.stop()
            self.setStyleSheet(self.original_stylesheet)

    def animateHighlight(self):
        """朴素的动画高亮效果"""
        if not self.is_highlighted:
            return

        self.animation_state = not self.animation_state

        if self.animation_state:
            # 强调样式
            style = """
                QWidget {
                    background-color: #dee2e6;
                    border: 3px solid #495057;
                    border-radius: 6px;
                }
            """
        else:
            # 正常高亮样式
            style = """
                QWidget {
                    background-color: #e9ecef;
                    border: 2px solid #6c757d;
                    border-radius: 5px;
                }
            """

        self.setStyleSheet(style)
