import sys
import math
from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import *

import sys
import math
from PyQt6.QtWidgets import *
from PyQt6.QtCore import *
from PyQt6.QtGui import *


class OverlayWidget(QWidget):
    """基础高亮遮罩引导 - 优化版本"""

    def __init__(self, target_widget, text, parent=None):
        super().__init__(parent)
        self.target_widget = target_widget
        self.text = text
        self.parent_window = parent
        # self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # 监听父窗口的大小变化
        if parent:
            parent.installEventFilter(self)

    def eventFilter(self, obj, event):
        """监听父窗口事件"""
        if obj == self.parent_window and event.type() == QEvent.Type.Resize:
            self.resize(self.parent_window.size())
            self.move(0, 0)
            self.update()
        return super().eventFilter(obj, event)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 绘制更透明的遮罩 (从150改为80)
        painter.fillRect(self.rect(), QColor(0, 0, 0,100))

        # 检查目标控件是否仍然可见
        if not self.target_widget.isVisible():
            return

        # 获取目标控件的位置和大小
        target_rect = self.target_widget.geometry()
        global_pos = self.target_widget.mapToGlobal(QPoint(0, 0))
        local_pos = self.mapFromGlobal(global_pos)
        highlight_rect = QRect(local_pos, target_rect.size())

        # 确保高亮区域在窗口范围内
        highlight_rect = highlight_rect.intersected(self.rect())

        if highlight_rect.isEmpty():
            return

        # 清除高亮区域 - 扩大清除范围
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        expanded_highlight = highlight_rect.adjusted(-8, -8, 8, 8)
        painter.fillRect(expanded_highlight, Qt.GlobalColor.transparent)

        # 绘制高亮边框
        painter.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceOver)
        pen = QPen(QColor(0, 150, 255), 3)
        painter.setPen(pen)
        painter.drawRoundedRect(highlight_rect, 6, 6)

        # 智能计算文字位置 - 增大文字区域
        text_pos = self.calculate_text_position(highlight_rect)
        text_rect = QRect(text_pos.x(), text_pos.y(), 320, 120)  # 增大尺寸

        # 确保文字区域在窗口内
        text_rect = self.adjust_text_rect_to_window(text_rect)

        # 绘制文字背景 - 更好的视觉效果
        painter.fillRect(text_rect, QColor(255, 255, 255, 250))
        painter.setPen(QPen(QColor(220, 220, 220), 1))
        painter.drawRoundedRect(text_rect, 8, 8)

        # 绘制阴影效果
        shadow_rect = text_rect.adjusted(2, 2, 2, 2)
        painter.fillRect(shadow_rect, QColor(0, 0, 0, 30))
        painter.fillRect(text_rect, QColor(255, 255, 255, 250))
        painter.setPen(QPen(QColor(200, 200, 200), 1))
        painter.drawRoundedRect(text_rect, 8, 8)

        # 绘制说明文字 - 优化布局
        self.draw_text_content(painter, text_rect)

    def draw_text_content(self, painter, text_rect):
        """绘制优化的文字内容"""
        painter.setPen(QColor(50, 50, 50))

        # 计算文字区域（增大边距）
        content_rect = text_rect.adjusted(15, 15, -15, -15)

        # 分割文字内容
        lines = self.text.split('\n')

        if lines:
            # 绘制主要内容
            content_font = QFont()
            content_font.setPointSize(11)
            painter.setFont(content_font)

            # 计算文字高度
            metrics = QFontMetrics(content_font)
            line_height = metrics.height()

            y_offset = 0
            for i, line in enumerate(lines):
                if line.strip():  # 跳过空行
                    line_rect = QRect(content_rect.x(), content_rect.y() + y_offset,
                                      content_rect.width(), line_height + 5)
                    painter.drawText(line_rect, Qt.AlignmentFlag.AlignLeft | Qt.TextFlag.TextWordWrap, line)
                    y_offset += line_height + 5

        # 绘制"点击继续"提示 - 放在底部
        hint_font = QFont()
        hint_font.setPointSize(10)
        hint_font.setItalic(True)
        painter.setFont(hint_font)
        painter.setPen(QColor(100, 100, 100))

        hint_rect = QRect(text_rect.x() + 15, text_rect.bottom() - 30,
                          text_rect.width() - 30, 25)
        painter.drawText(hint_rect, Qt.AlignmentFlag.AlignRight, "💡 点击任意位置继续")

    def calculate_text_position(self, highlight_rect):
        """智能计算文字显示位置"""
        window_rect = self.rect()
        text_width, text_height = 320, 120  # 增大尺寸

        # 尝试在高亮区域下方显示
        if highlight_rect.bottom() + text_height + 20 < window_rect.bottom():
            return QPoint(highlight_rect.x(), highlight_rect.bottom() + 15)

        # 尝试在高亮区域上方显示
        if highlight_rect.top() - text_height - 20 > 0:
            return QPoint(highlight_rect.x(), highlight_rect.top() - text_height - 15)

        # 尝试在高亮区域右侧显示
        if highlight_rect.right() + text_width + 20 < window_rect.right():
            return QPoint(highlight_rect.right() + 15, highlight_rect.y())

        # 尝试在高亮区域左侧显示
        if highlight_rect.left() - text_width - 20 > 0:
            return QPoint(highlight_rect.left() - text_width - 15, highlight_rect.y())

        # 如果都不行，显示在窗口中央
        return QPoint(window_rect.width() // 2 - text_width // 2,
                      window_rect.height() // 2 - text_height // 2)

    def adjust_text_rect_to_window(self, text_rect):
        """确保文字区域在窗口范围内"""
        window_rect = self.rect()

        # 调整X坐标
        if text_rect.right() > window_rect.right():
            text_rect.moveRight(window_rect.right() - 15)
        if text_rect.left() < 0:
            text_rect.moveLeft(15)

        # 调整Y坐标
        if text_rect.bottom() > window_rect.bottom():
            text_rect.moveBottom(window_rect.bottom() - 15)
        if text_rect.top() < 0:
            text_rect.moveTop(15)

        return text_rect