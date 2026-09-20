#气泡提示式引导
import math

from PyQt6.QtCore import Qt, QTimer, QEvent, QPoint, QRect
from PyQt6.QtGui import QPainter, QColor, QPen
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QHBoxLayout, QPushButton, QApplication


class ResponsiveBubbleTooltip(QWidget):
    """优化的响应式气泡提示 - 添加指向线和高亮"""

    def __init__(self, target_widget, text, main_window):
        super().__init__()
        self.target_widget = target_widget
        self.text = text
        self.main_window = main_window

        # self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # 创建背景遮罩
        self.overlay_background = QWidget(main_window)
        self.overlay_background.setWindowFlags(Qt.WindowType.FramelessWindowHint )
        # self.overlay_background.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.overlay_background.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.overlay_background.resize(main_window.size())
        self.overlay_background.move(0, 0)
        self.overlay_background.paintEvent = self.paint_background
        self.overlay_background.show()

        self.setup_ui()
        self.position_tooltip()

        # 监听窗口大小变化
        main_window.installEventFilter(self)

    def paint_background(self, event):
        """绘制背景遮罩和高亮区域"""
        painter = QPainter(self.overlay_background)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 绘制半透明背景
        painter.fillRect(self.overlay_background.rect(), QColor(0, 0, 0, 60))

        if not self.target_widget or not self.target_widget.isVisible():
            return

        # 获取目标控件位置
        target_rect = self.target_widget.geometry()
        global_pos = self.target_widget.mapToGlobal(QPoint(0, 0))
        local_pos = self.overlay_background.mapFromGlobal(global_pos)
        highlight_rect = QRect(local_pos, target_rect.size())

        # 绘制高亮区域
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)

        # 高亮背景
        expanded_rect = highlight_rect.adjusted(-8, -8, 8, 8)
        painter.fillRect(expanded_rect, QColor(255, 255, 255, 40))

        # 发光效果
        for i in range(8, 0, -1):
            alpha = max(0, min(80, 30 - i * 2))
            pen = QPen(QColor(0, 150, 255, alpha), i)
            painter.setPen(pen)
            glow_rect = highlight_rect.adjusted(-i // 2, -i // 2, i // 2, i // 2)
            painter.drawRoundedRect(glow_rect, 6, 6)

        # 主高亮边框
        pen = QPen(QColor(0, 150, 255, 255), 3)
        painter.setPen(pen)
        painter.drawRoundedRect(highlight_rect, 6, 6)

        # 绘制指向线
        # self.draw_pointer_line(painter, highlight_rect)

    def draw_pointer_line(self, painter, highlight_rect):
        """绘制从目标控件到气泡的指向线"""
        try:
            # 获取气泡位置
            bubble_global = self.mapToGlobal(QPoint(0, 0))
            bubble_local = self.overlay_background.mapFromGlobal(bubble_global)
            bubble_rect = QRect(bubble_local, self.size())

            # 计算连接点
            target_center = highlight_rect.center()
            bubble_center = bubble_rect.center()

            # 找到目标控件边缘的最近点
            target_point = self.get_edge_point(highlight_rect, bubble_center)
            bubble_point = self.get_edge_point(bubble_rect, target_center)

            # 绘制指向线
            pen = QPen(QColor(0, 150, 255, 200), 3)
            pen.setStyle(Qt.PenStyle.DashLine)
            painter.setPen(pen)
            painter.drawLine(target_point, bubble_point)

            # 绘制箭头
            self.draw_arrow_head(painter, target_point, bubble_point)

        except Exception as e:
            print(f"指向线绘制错误: {e}")

    def get_edge_point(self, rect, external_point):
        """获取矩形边缘上离外部点最近的点"""
        center = rect.center()

        # 计算方向
        dx = external_point.x() - center.x()
        dy = external_point.y() - center.y()

        # 计算与矩形边缘的交点
        if abs(dx) > abs(dy):
            # 左右边缘
            if dx > 0:
                return QPoint(rect.right(), center.y())
            else:
                return QPoint(rect.left(), center.y())
        else:
            # 上下边缘
            if dy > 0:
                return QPoint(center.x(), rect.bottom())
            else:
                return QPoint(center.x(), rect.top())

    def draw_arrow_head(self, painter, start_point, end_point):
        """绘制箭头头部"""
        try:
            # 计算箭头方向
            dx = end_point.x() - start_point.x()
            dy = end_point.y() - start_point.y()

            if dx == 0 and dy == 0:
                return

            # 箭头长度和角度
            arrow_length = 12
            arrow_angle = math.pi / 6  # 30度

            # 计算箭头线的角度
            angle = math.atan2(dy, dx)

            # 计算箭头的两个端点
            x1 = end_point.x() - arrow_length * math.cos(angle - arrow_angle)
            y1 = end_point.y() - arrow_length * math.sin(angle - arrow_angle)

            x2 = end_point.x() - arrow_length * math.cos(angle + arrow_angle)
            y2 = end_point.y() - arrow_length * math.sin(angle + arrow_angle)

            # 绘制箭头
            pen = QPen(QColor(0, 150, 255, 255), 3)
            painter.setPen(pen)
            painter.drawLine(end_point, QPoint(int(x1), int(y1)))
            painter.drawLine(end_point, QPoint(int(x2), int(y2)))

        except Exception as e:
            print(f"箭头绘制错误: {e}")

    def eventFilter(self, obj, event):
        if obj == self.main_window and event.type() == QEvent.Type.Resize:
            if hasattr(self, 'overlay_background'):
                self.overlay_background.resize(self.main_window.size())
                self.overlay_background.update()
            QTimer.singleShot(10, self.position_tooltip)
        return super().eventFilter(obj, event)

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(15)

        # 文本标签 - 增大尺寸
        self.label = QLabel(self.text)
        self.label.setWordWrap(True)
        self.label.setMaximumWidth(400)  # 增大最大宽度
        self.label.setMinimumWidth(250)  # 增大最小宽度
        self.label.setMinimumHeight(80)  # 设置最小高度
        self.label.setStyleSheet("""
            QLabel {
                background-color: rgba(255, 255, 255, 250);
                color: #333333;
                padding: 20px;
                border-radius: 12px;
                font-size: 13px;
                line-height: 1.6;
                border: 2px solid rgba(0, 150, 255, 180);
                box-shadow: 0 4px 15px rgba(0, 0, 0, 0.2);
            }
        """)

        # 按钮布局
        button_layout = QHBoxLayout()
        button_layout.setSpacing(12)

        self.skip_btn = QPushButton("跳过教程")
        self.next_btn = QPushButton("下一步 →")

        for btn in [self.skip_btn, self.next_btn]:
            btn.setFixedSize(80, 35)  # 增大按钮尺寸
            btn.setStyleSheet("""
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    border: none;
                    border-radius: 6px;
                    font-size: 12px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #45a049;
                    transform: translateY(-1px);
                }
                QPushButton:pressed {
                    background-color: #3d8b40;
                }
            """)

        # 跳过按钮使用不同颜色
        self.skip_btn.setStyleSheet("""
            QPushButton {
                background-color: #757575;
                color: white;
                border: none;
                border-radius: 6px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: #616161;
            }
            QPushButton:pressed {
                background-color: #424242;
            }
        """)

        button_layout.addStretch()
        button_layout.addWidget(self.skip_btn)
        button_layout.addWidget(self.next_btn)

        layout.addWidget(self.label)
        layout.addLayout(button_layout)

        # 设置整体样式
        self.setStyleSheet("""
            ResponsiveBubbleTooltip {
                background-color: transparent;
            }
        """)

    def position_tooltip(self):
        """响应式定位提示框"""
        if not self.target_widget.isVisible():
            return

        target_rect = self.target_widget.geometry()
        target_global = self.target_widget.mapToGlobal(QPoint(0, 0))

        # 调整自身大小以适应内容
        self.adjustSize()

        tooltip_size = self.size()

        # 获取屏幕几何信息
        screen = QApplication.primaryScreen().geometry()

        # 计算最佳位置的候选项
        margin = 30  # 增大边距避免重叠
        positions = [
            ("bottom", target_global.x(), target_global.y() + target_rect.height() + margin),
            ("top", target_global.x(), target_global.y() - tooltip_size.height() - margin),
            ("right", target_global.x() + target_rect.width() + margin, target_global.y()),
            ("left", target_global.x() - tooltip_size.width() - margin, target_global.y())
        ]

        # 选择最适合的位置
        for position, x, y in positions:
            test_rect = QRect(x, y, tooltip_size.width(), tooltip_size.height())
            if screen.contains(test_rect):
                self.move(x, y)
                if hasattr(self, 'overlay_background'):
                    self.overlay_background.update()
                return

        # 如果所有位置都不合适，居中显示
        center_x = max(20, min(target_global.x() + target_rect.width() // 2 - tooltip_size.width() // 2,
                               screen.width() - tooltip_size.width() - 20))
        center_y = max(20, min(target_global.y() + target_rect.height() // 2 - tooltip_size.height() // 2,
                               screen.height() - tooltip_size.height() - 20))
        self.move(center_x, center_y)
        if hasattr(self, 'overlay_background'):
            self.overlay_background.update()

    def close(self):
        """关闭气泡提示时也关闭背景遮罩"""
        if hasattr(self, 'overlay_background'):
            self.overlay_background.close()
        super().close()

    def mousePressEvent(self, event):
        """处理鼠标点击事件"""
        pass