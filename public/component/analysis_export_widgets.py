"""Reusable image preview/export helpers for analysis charts."""

from __future__ import annotations

import re
from pathlib import Path

from PyQt6.QtCore import QPoint, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QPainter, QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class _InteractiveImageLabel(QLabel):
    """Image canvas with wheel zoom and left-button panning."""

    zoom_requested = pyqtSignal(float)
    pan_requested = pyqtSignal(QPoint)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._dragging = False
        self._last_position = QPoint()
        self.setCursor(Qt.CursorShape.OpenHandCursor)

    def wheelEvent(self, event) -> None:  # noqa: N802 - Qt API
        delta = event.angleDelta().y()
        if delta:
            self.zoom_requested.emit(1.15 if delta > 0 else 1 / 1.15)
            event.accept()
            return
        event.ignore()

    def mousePressEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._last_position = event.position().toPoint()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # noqa: N802 - Qt API
        if self._dragging:
            current = event.position().toPoint()
            self.pan_requested.emit(current - self._last_position)
            self._last_position = current
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802 - Qt API
        if event.button() == Qt.MouseButton.LeftButton:
            self._dragging = False
            self.setCursor(Qt.CursorShape.OpenHandCursor)
            event.accept()
            return
        super().mouseReleaseEvent(event)

class ChartImageDialog(QDialog):
    """Show a chart at selectable sizes and save the displayed image.

    The source widget is temporarily resized before capture, so custom-painted
    charts are redrawn at the selected export size instead of merely being
    stretched from a small screenshot.
    """

    def __init__(self, source_widget: QWidget, title: str, parent=None):
        super().__init__(parent)
        self.source_widget = source_widget
        self.title_text = str(title)
        self.current_scale = 1.0
        self.current_pixmap = QPixmap()
        self.export_size = QSize()
        self.saved_path = ""
        self.setWindowTitle(f"放大查看：{self.title_text}")
        self.resize(1100, 760)

        layout = QVBoxLayout(self)
        hint = QLabel("滚动鼠标滚轮放大/缩小，按住鼠标左键拖动查看；保存时按当前尺寸导出 PNG。")
        hint.setProperty("hint", True)
        layout.addWidget(hint)

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)
        self.image_label = _InteractiveImageLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.zoom_requested.connect(self._zoom_by)
        self.image_label.pan_requested.connect(self._pan_by)
        self.scroll_area.setWidget(self.image_label)
        layout.addWidget(self.scroll_area, 1)

        controls = QHBoxLayout()
        self.scale_label = QLabel()
        controls.addWidget(self.scale_label)
        controls.addStretch(1)
        save = QPushButton("保存当前尺寸 PNG")
        save.clicked.connect(self._save_image)
        controls.addWidget(save)
        close = QPushButton("关闭")
        close.clicked.connect(self.accept)
        controls.addWidget(close)
        layout.addLayout(controls)
        self._set_scale(1.0)

    def _source_size(self) -> QSize:
        size = self.source_widget.size()
        if size.width() < 2 or size.height() < 2:
            size = self.source_widget.sizeHint()
        return QSize(max(1, size.width()), max(1, size.height()))

    def _capture(self, size: QSize) -> QPixmap:
        """Render at a larger canvas without resizing the live source widget."""
        target = QSize(max(1, size.width()), max(1, size.height()))
        source_size = self._source_size()
        pixmap = QPixmap(target)
        pixmap.fill(Qt.GlobalColor.white)
        from PyQt6.QtGui import QPainter

        painter = QPainter(pixmap)
        painter.scale(target.width() / source_size.width(), target.height() / source_size.height())
        self.source_widget.render(painter)
        painter.end()
        return pixmap
    def _base_size(self) -> QSize:
        size = self._source_size()
        return QSize(max(640, size.width()), max(360, size.height()))
    def _set_scale(self, scale: float) -> None:
        self.current_scale = max(0.75, min(3.0, float(scale)))
        base = self._base_size()
        target = QSize(int(base.width() * self.current_scale), int(base.height() * self.current_scale))
        self.current_pixmap = self._capture(target)
        self.export_size = self.current_pixmap.size()
        self.image_label.setPixmap(self.current_pixmap)
        self.image_label.resize(self.current_pixmap.size())
        self.scale_label.setText(f"当前尺寸：{self.export_size.width()} × {self.export_size.height()} px（{self.current_scale:.2g}×）")

    def _zoom_by(self, factor: float) -> None:
        old_horizontal = self.scroll_area.horizontalScrollBar()
        old_vertical = self.scroll_area.verticalScrollBar()
        horizontal_ratio = old_horizontal.value() / max(1, old_horizontal.maximum())
        vertical_ratio = old_vertical.value() / max(1, old_vertical.maximum())
        self._set_scale(self.current_scale * float(factor))
        horizontal = self.scroll_area.horizontalScrollBar()
        vertical = self.scroll_area.verticalScrollBar()
        horizontal.setValue(int(horizontal_ratio * horizontal.maximum()))
        vertical.setValue(int(vertical_ratio * vertical.maximum()))

    def _pan_by(self, delta: QPoint) -> None:
        horizontal = self.scroll_area.horizontalScrollBar()
        vertical = self.scroll_area.verticalScrollBar()
        horizontal.setValue(horizontal.value() - delta.x())
        vertical.setValue(vertical.value() - delta.y())
    def _save_image(self) -> None:
        safe_title = re.sub(r"[^0-9A-Za-z一-龥_-]+", "_", self.title_text).strip("_") or "analysis_chart"
        default_name = f"{safe_title}_{self.export_size.width()}x{self.export_size.height()}.png"
        file_path, _ = QFileDialog.getSaveFileName(self, "保存当前图像", default_name, "PNG 图片 (*.png)")
        if not file_path:
            return
        if self.current_pixmap.save(str(Path(file_path)), "PNG"):
            self.saved_path = str(file_path)
