"""
Image canvas for the coord_3d module.

Supports:
- manual control point editing
- grid point rendering
- region polygon drawing
- manual YOLO-like box selection
- zoom / pan / move / delete workflows
"""
import math
import time

from PyQt6.QtCore import QPointF, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QBrush, QFont, QPainter, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import QWidget

LAYER_COLORS = {
    "1": QColor(34, 197, 94),
    "2": QColor(249, 115, 22),
    "3": QColor(236, 72, 153),
    "4": QColor(6, 182, 212),
}


class ImageCanvas(QWidget):
    point_added = pyqtSignal(dict)
    point_deleted = pyqtSignal(str)
    yolo_box_added = pyqtSignal(dict)
    yolo_box_deleted = pyqtSignal(str)
    region_point_added = pyqtSignal(dict)
    state_changed = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._pixmap: QPixmap | None = None
        self._scale = 1.0
        self._offset = QPointF(0, 0)
        self._panning = False
        self._last_pan_pos = QPointF()

        self.points: list = []
        self.grid_data: list = []
        self.yolo_boxes: list = []
        self.regions: list = []
        self.current_region_pts: list = []
        self.solved_yolo: list = []

        self._drawing_yolo: dict | None = None
        self._dragging_id: str | None = None
        self._drag_changed = False

        self.tool_mode = "add"
        self.active_label = "1"
        self.point_opacity = 0.8

    # ------------------------------------------------------------------ data
    def has_image(self) -> bool:
        return bool(self._pixmap and not self._pixmap.isNull())

    def get_pixmap(self) -> QPixmap | None:
        return self._pixmap

    def fit_to_view(self):
        if not self.has_image():
            return
        pixmap = self._pixmap
        sw = max(1.0, self.width() / max(1, pixmap.width()))
        sh = max(1.0, self.height() / max(1, pixmap.height()))
        self._scale = min(sw, sh, 1.0)
        self._offset = QPointF(
            (self.width() - pixmap.width() * self._scale) / 2,
            (self.height() - pixmap.height() * self._scale) / 2,
        )
        self.update()

    def set_image(self, pixmap: QPixmap, reset_view: bool = True):
        self._pixmap = pixmap
        if reset_view:
            self.fit_to_view()
        self.update()
        self.state_changed.emit()

    def clear_all(self, keep_image: bool = True):
        pixmap = self._pixmap if keep_image else None
        self.points.clear()
        self.grid_data.clear()
        self.yolo_boxes.clear()
        self.regions.clear()
        self.current_region_pts.clear()
        self.solved_yolo.clear()
        self._drawing_yolo = None
        self._dragging_id = None
        self._drag_changed = False
        self._pixmap = pixmap
        if keep_image and pixmap:
            self.fit_to_view()
        self.update()
        self.state_changed.emit()

    # ------------------------------------------------------------------ coords
    def _to_image(self, screen_pt: QPointF) -> QPointF:
        return QPointF(
            (screen_pt.x() - self._offset.x()) / self._scale,
            (screen_pt.y() - self._offset.y()) / self._scale,
        )

    def _to_screen(self, img_pt: QPointF) -> QPointF:
        return QPointF(
            img_pt.x() * self._scale + self._offset.x(),
            img_pt.y() * self._scale + self._offset.y(),
        )

    # ------------------------------------------------------------------ mouse
    def wheelEvent(self, e):
        factor = math.exp(-e.angleDelta().y() * 0.002)
        mouse_pos = e.position()
        img_x = (mouse_pos.x() - self._offset.x()) / self._scale
        img_y = (mouse_pos.y() - self._offset.y()) / self._scale
        self._scale = max(0.05, min(15.0, self._scale * factor))
        self._offset = QPointF(
            mouse_pos.x() - img_x * self._scale,
            mouse_pos.y() - img_y * self._scale,
        )
        self.update()

    def mousePressEvent(self, e):
        pos = e.position()
        img_pos = self._to_image(pos)
        pt = {"x": img_pos.x(), "y": img_pos.y()}

        if self.tool_mode == "pan" or e.button() == Qt.MouseButton.MiddleButton:
            self._panning = True
            self._last_pan_pos = pos
            return

        if e.button() != Qt.MouseButton.LeftButton:
            return

        if self.tool_mode == "region":
            self.current_region_pts.append({"x": pt["x"], "y": pt["y"]})
            self.region_point_added.emit({"x": pt["x"], "y": pt["y"]})
            self.update()
            self.state_changed.emit()
            return

        if self.tool_mode == "yolo":
            self._drawing_yolo = {
                "startX": pt["x"],
                "startY": pt["y"],
                "currentX": pt["x"],
                "currentY": pt["y"],
            }
            return

        if self.tool_mode == "move":
            threshold = 15 / self._scale
            for bucket in (self.points, self.grid_data):
                for point in bucket:
                    if point.get("label") != self.active_label:
                        continue
                    if math.hypot(point["x"] - pt["x"], point["y"] - pt["y"]) < threshold:
                        self._dragging_id = point["id"]
                        self._drag_changed = False
                        return
            return

        if self.tool_mode == "delete":
            threshold = 15 / self._scale
            changed = False

            new_points = []
            for point in self.points:
                if point.get("label") == self.active_label and math.hypot(point["x"] - pt["x"], point["y"] - pt["y"]) < threshold:
                    self.point_deleted.emit(point["id"])
                    changed = True
                    continue
                new_points.append(point)
            self.points = new_points

            new_grid = []
            for point in self.grid_data:
                if point.get("label") == self.active_label and math.hypot(point["x"] - pt["x"], point["y"] - pt["y"]) < threshold:
                    changed = True
                    continue
                new_grid.append(point)
            self.grid_data = new_grid

            new_boxes = []
            for box in self.yolo_boxes:
                inside = (
                    min(box["startX"], box["endX"]) <= pt["x"] <= max(box["startX"], box["endX"])
                    and min(box["startY"], box["endY"]) <= pt["y"] <= max(box["startY"], box["endY"])
                )
                if inside:
                    self.yolo_box_deleted.emit(box["id"])
                    changed = True
                    continue
                new_boxes.append(box)
            self.yolo_boxes = new_boxes

            if changed:
                self.update()
                self.state_changed.emit()
            return

        if self.tool_mode == "add":
            new_pt = {
                "id": str(time.time_ns()),
                "x": pt["x"],
                "y": pt["y"],
                "label": self.active_label,
            }
            self.points.append(new_pt)
            self.point_added.emit(new_pt)
            self.update()
            self.state_changed.emit()

    def mouseMoveEvent(self, e):
        pos = e.position()
        if self._panning:
            dx = pos.x() - self._last_pan_pos.x()
            dy = pos.y() - self._last_pan_pos.y()
            self._offset += QPointF(dx, dy)
            self._last_pan_pos = pos
            self.update()
            return

        img_pos = self._to_image(pos)
        pt = {"x": img_pos.x(), "y": img_pos.y()}

        if self._drawing_yolo:
            self._drawing_yolo["currentX"] = pt["x"]
            self._drawing_yolo["currentY"] = pt["y"]
            self.update()
            return

        if self._dragging_id and self.tool_mode == "move":
            for bucket in (self.points, self.grid_data):
                for point in bucket:
                    if point["id"] == self._dragging_id:
                        point["x"] = pt["x"]
                        point["y"] = pt["y"]
                        self._drag_changed = True
            self.update()

    def mouseReleaseEvent(self, e):
        if self._panning:
            self._panning = False
            return

        if self._drawing_yolo:
            box = self._drawing_yolo
            if abs(box["startX"] - box["currentX"]) > 5 and abs(box["startY"] - box["currentY"]) > 5:
                new_box = {
                    "id": str(time.time_ns()),
                    "startX": box["startX"],
                    "startY": box["startY"],
                    "endX": box["currentX"],
                    "endY": box["currentY"],
                }
                self.yolo_boxes.append(new_box)
                self.yolo_box_added.emit(new_box)
                self.state_changed.emit()
            self._drawing_yolo = None
            self.update()

        if self._dragging_id and self._drag_changed:
            self.state_changed.emit()

        self._dragging_id = None
        self._drag_changed = False

    # ------------------------------------------------------------------ paint
    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(15, 23, 42))

        if not self.has_image():
            self._draw_empty_state(painter)
            return

        painter.save()
        painter.translate(self._offset)
        painter.scale(self._scale, self._scale)
        painter.drawPixmap(0, 0, self._pixmap)

        self._draw_grid_lines(painter)
        self._draw_grid_points(painter)
        self._draw_manual_points(painter)
        self._draw_regions(painter)
        self._draw_current_region(painter)
        self._draw_yolo_boxes(painter)
        self._draw_temp_yolo(painter)

        painter.restore()
 
    def _draw_empty_state(self, painter: QPainter):
        center = self.rect().center()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(24, 35, 60))
        painter.drawEllipse(QPointF(center), 96, 96)

        icon_pen = QPen(QColor(84, 101, 132), 4)
        painter.setPen(icon_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        frame = QRectF(center.x() - 34, center.y() - 34, 68, 68)
        painter.drawRoundedRect(frame, 10, 10)
        painter.drawEllipse(QPointF(center.x() - 12, center.y() - 10), 8, 8)
        painter.drawLine(center.x() - 28, center.y() + 18, center.x() - 2, center.y() - 2)
        painter.drawLine(center.x() - 2, center.y() - 2, center.x() + 12, center.y() + 10)
        painter.drawLine(center.x() + 12, center.y() + 10, center.x() + 30, center.y() - 18)

        painter.setPen(QColor(219, 234, 254))
        painter.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        painter.drawText(
            QRectF(center.x() - 180, center.y() + 120, 360, 30),
            Qt.AlignmentFlag.AlignCenter,
            "等待数据源载入",
        )
        painter.setPen(QColor(123, 140, 171))
        painter.setFont(QFont("Arial", 10))
        painter.drawText(
            QRectF(center.x() - 180, center.y() + 148, 360, 24),
            Qt.AlignmentFlag.AlignCenter,
            "支持鼠标滚轮缩放",
        )

    def _draw_grid_points(self, painter: QPainter):
        for point in self.grid_data:
            if point.get("is_manual_covered"):
                continue
            color = QColor(LAYER_COLORS.get(point.get("label", "1"), QColor(255, 255, 255)))
            alpha = int(self.point_opacity * (255 if point.get("label") == self.active_label else 90))
            color.setAlpha(alpha)
            painter.setBrush(QBrush(color))
            painter.setPen(QPen(QColor(0, 0, 0, int(self.point_opacity * 128)), 1 / self._scale))
            radius = 3.5 / self._scale
            painter.drawEllipse(QPointF(point["x"], point["y"]), radius, radius)

    def _draw_manual_points(self, painter: QPainter):
        for point in self.points:
            color = QColor(LAYER_COLORS.get(point.get("label", "1"), QColor(255, 255, 255)))
            alpha = int(min(255, self.point_opacity * 255 + (51 if point.get("label") == self.active_label else 0)))
            color.setAlpha(alpha)
            painter.setBrush(QBrush(color))
            pen_alpha = 255 if point.get("label") == self.active_label else 153
            painter.setPen(QPen(QColor(255, 255, 255, pen_alpha), 1.5 / self._scale))
            radius = 4.8 / self._scale
            painter.drawEllipse(QPointF(point["x"], point["y"]), radius, radius)

    def _draw_yolo_boxes(self, painter: QPainter):
        solved_lookup = {box["id"]: box for box in self.solved_yolo if box.get("id")}
        for box in self.yolo_boxes:
            solved = solved_lookup.get(box["id"])
            x1 = min(box["startX"], box["endX"])
            y1 = min(box["startY"], box["endY"])
            w = abs(box["endX"] - box["startX"])
            h = abs(box["endY"] - box["startY"])

            painter.setPen(QPen(QColor(250, 204, 21), 2 / self._scale))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRect(QRectF(x1, y1, w, h))

            painter.setPen(QColor(254, 240, 138))
            painter.setFont(QFont("Arial", max(8, int(11 / self._scale))))
            if solved:
                painter.setBrush(QBrush(QColor(52, 211, 238)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(QPointF(solved.get("cx", 0), solved.get("cy", 0)), 5 / self._scale, 5 / self._scale)
                painter.setPen(QColor(254, 240, 138))
                painter.drawText(
                    QPointF(solved.get("cx", x1) + 8 / self._scale, solved.get("cy", y1) - 10 / self._scale),
                    f"高:{solved.get('mouseHeight', 0):.1f}mm  Z:{solved.get('Z_total', 0):.1f}",
                )
            else:
                painter.drawText(QPointF(x1 + 6 / self._scale, y1 - 6 / self._scale), "待解算")

    def _draw_regions(self, painter: QPainter):
        for region in self.regions:
            points = region.get("points", [])
            if len(points) < 3:
                continue
            polygon = QPolygonF([QPointF(p["x"], p["y"]) for p in points])
            painter.setBrush(QBrush(QColor(168, 85, 247, 38)))
            painter.setPen(QPen(QColor(168, 85, 247, 153), 2 / self._scale))
            painter.drawPolygon(polygon)
            painter.setPen(QColor(192, 132, 252))
            painter.setFont(QFont("Arial", max(8, int(12 / self._scale))))
            painter.drawText(
                QPointF(points[0]["x"], points[0]["y"] - 5 / self._scale),
                f"{region['name']} (Z:{region['height']}mm)",
            )

    def _draw_current_region(self, painter: QPainter):
        if not self.current_region_pts:
            return
        polygon = QPolygonF([QPointF(p["x"], p["y"]) for p in self.current_region_pts])
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.setPen(QPen(QColor(168, 85, 247, 204), 2 / self._scale))
        painter.drawPolyline(polygon)
        for point in self.current_region_pts:
            painter.setBrush(QBrush(QColor(168, 85, 247)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(point["x"], point["y"]), 4 / self._scale, 4 / self._scale)

    def _draw_temp_yolo(self, painter: QPainter):
        if not self._drawing_yolo:
            return
        box = self._drawing_yolo
        painter.setPen(QPen(QColor(254, 240, 138), 2 / self._scale, Qt.PenStyle.DashLine))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        x1 = min(box["startX"], box["currentX"])
        y1 = min(box["startY"], box["currentY"])
        painter.drawRect(QRectF(x1, y1, abs(box["currentX"] - box["startX"]), abs(box["currentY"] - box["startY"])))

    def _draw_grid_lines(self, painter: QPainter):
        from collections import defaultdict

        by_label = defaultdict(list)
        for point in self.grid_data:
            by_label[point.get("label", "1")].append(point)

        for label, points in by_label.items():
            color = QColor(LAYER_COLORS.get(label, QColor(255, 255, 255)))
            alpha = int(self.point_opacity * (179 if label == self.active_label else 89))
            color.setAlpha(alpha)
            width = (2 if label == self.active_label else 1) / self._scale
            painter.setPen(QPen(color, width))

            lookup = {(point["c"], point["r"]): point for point in points if point.get("c") is not None}
            for (col, row), p1 in lookup.items():
                for dc, dr in ((1, 0), (0, 1)):
                    p2 = lookup.get((col + dc, row + dr))
                    if p2:
                        painter.drawLine(QPointF(p1["x"], p1["y"]), QPointF(p2["x"], p2["y"]))
