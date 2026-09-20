"""
Simple interactive 3D preview canvas for coord_3d.
"""
import math

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QBrush, QFont, QPainter, QPen
from PyQt6.QtWidgets import QWidget

LAYER_COLORS = {
    "1": (34, 197, 94),
    "2": (249, 115, 22),
    "3": (236, 72, 153),
    "4": (6, 182, 212),
}

DEFAULT_SCENE = {
    "width": 180.0,
    "depth": 310.0,
    "height": 150.0,
}


class Canvas3D(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)

        self.p3d: list = []
        self.lines3d: list = []
        self.solved_yolo: list = []
        self.scene_config = dict(DEFAULT_SCENE)

        self._scale = 1.0
        self._cam_yaw = math.pi / 4
        self._cam_pitch = math.pi / 6
        self._rotating = False
        self._last_pos = QPointF()

    def set_scene_config(self, scene_config: dict | None):
        self.scene_config = dict(DEFAULT_SCENE)
        if scene_config:
            self.scene_config.update(scene_config)
        self.update()

    def reset_camera(self):
        self._scale = 1.0
        self._cam_yaw = math.pi / 4
        self._cam_pitch = math.pi / 6
        self.update()

    def set_data(self, p3d: list, lines3d: list, solved_yolo: list | None = None):
        self.p3d = p3d or []
        self.lines3d = lines3d or []
        self.solved_yolo = solved_yolo or []
        self.update()

    # ------------------------------------------------------------------ utils
    def _half_width(self) -> float:
        return max(1.0, self.scene_config.get("width", DEFAULT_SCENE["width"])) / 2.0

    def _depth(self) -> float:
        return max(1.0, self.scene_config.get("depth", DEFAULT_SCENE["depth"]))

    def _height(self) -> float:
        return max(1.0, self.scene_config.get("height", DEFAULT_SCENE["height"]))

    def _box_vertices(self):
        half_width = self._half_width()
        depth = self._depth()
        height = self._height()
        bottom = [(-half_width, 0, 0), (half_width, 0, 0), (half_width, depth, 0), (-half_width, depth, 0)]
        top = [(-half_width, 0, height), (half_width, 0, height), (half_width, depth, height), (-half_width, depth, height)]
        return bottom, top

    def _project(self, x: float, y: float, z: float) -> QPointF:
        width = self.scene_config.get("width", DEFAULT_SCENE["width"])
        depth = self._depth()
        height = self._height()

        cx = self.width() / 2
        cy = self.height() / 2 + 90

        nx = x / max(width * 0.9, 1.0)
        ny = (y - depth / 2.0) / max(depth * 0.95, 1.0)
        nz = (z - height / 2.0) / max(height * 1.1, 1.0)

        x1 = nx * math.cos(self._cam_yaw) - ny * math.sin(self._cam_yaw)
        y1 = nx * math.sin(self._cam_yaw) + ny * math.cos(self._cam_yaw)
        y2 = y1 * math.cos(self._cam_pitch) + nz * math.sin(self._cam_pitch)

        scale = self._scale * min(self.width(), self.height()) * 0.82
        return QPointF(cx + x1 * scale, cy - y2 * scale)

    # ------------------------------------------------------------------ mouse
    def wheelEvent(self, e):
        factor = math.exp(-e.angleDelta().y() * 0.002)
        self._scale = max(0.05, min(15.0, self._scale * factor))
        self.update()

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._rotating = True
            self._last_pos = e.position()

    def mouseMoveEvent(self, e):
        if not self._rotating:
            return
        dx = e.position().x() - self._last_pos.x()
        dy = e.position().y() - self._last_pos.y()
        self._cam_yaw += dx * 0.005
        self._cam_pitch = max(-1.35, min(1.35, self._cam_pitch + dy * 0.005))
        self._last_pos = e.position()
        self.update()

    def mouseReleaseEvent(self, e):
        self._rotating = False

    # ------------------------------------------------------------------ paint
    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(15, 23, 42))

        bottom, top = self._box_vertices()
        self._draw_axes(painter)
        self._draw_box(painter, bottom, top)
        self._draw_lines(painter)
        self._draw_points(painter)
        self._draw_solved_boxes(painter)
        self._draw_hud(painter)

        if not self.p3d and not self.solved_yolo:
            painter.setPen(QColor(148, 163, 184))
            painter.setFont(QFont("Arial", 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无 3D 数据\n请先完成图像标定或框选目标")

    def _draw_box(self, painter: QPainter, bottom: list, top: list):
        painter.setPen(QPen(QColor(255, 255, 255, 28), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for index in range(4):
            b1 = self._project(*bottom[index])
            b2 = self._project(*bottom[(index + 1) % 4])
            t1 = self._project(*top[index])
            t2 = self._project(*top[(index + 1) % 4])
            painter.drawLine(b1, b2)
            painter.drawLine(t1, t2)
            painter.drawLine(b1, t1)

    def _draw_axes(self, painter: QPainter):
        half_width = self._half_width()
        depth = self._depth()
        height = self._height()
        origin = (-half_width, 0, 0)
        x_end = (half_width, 0, 0)
        y_end = (-half_width, depth, 0)
        z_end = (-half_width, 0, height)

        axis_defs = [
            (origin, x_end, QColor(248, 113, 113), "X"),
            (origin, y_end, QColor(34, 197, 94), "Y"),
            (origin, z_end, QColor(56, 189, 248), "Z"),
        ]
        for start, end, color, label in axis_defs:
            painter.setPen(QPen(color, 2))
            p1 = self._project(*start)
            p2 = self._project(*end)
            painter.drawLine(p1, p2)
            painter.setFont(QFont("Arial", 9, QFont.Weight.Bold))
            painter.drawText(QPointF(p2.x() + 6, p2.y() - 4), label)

    def _draw_lines(self, painter: QPainter):
        for p1, p2 in self.lines3d:
            r, g, b = LAYER_COLORS.get(p1.get("label", "1"), (255, 255, 255))
            painter.setPen(QPen(QColor(r, g, b, 178), 1))
            pt1 = self._project(p1["x3"], p1["y3"], p1["z3"])
            pt2 = self._project(p2["x3"], p2["y3"], p2["z3"])
            painter.drawLine(pt1, pt2)

    def _draw_points(self, painter: QPainter):
        for point in self.p3d:
            r, g, b = LAYER_COLORS.get(point.get("label", "1"), (255, 255, 255))
            pt = self._project(point["x3"], point["y3"], point["z3"])
            if point.get("is_manual_covered"):
                painter.setBrush(QBrush(QColor(r, g, b, 230)))
                painter.setPen(QPen(QColor(255, 255, 255, 204), 1.5))
                painter.drawEllipse(pt, 3.8, 3.8)
            else:
                painter.setBrush(QBrush(QColor(r, g, b, 180)))
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(pt, 2.2, 2.2)

    def _draw_solved_boxes(self, painter: QPainter):
        for point in self.solved_yolo:
            b_pt = self._project(point["X"], point["Y"], point["Z_base"])
            t_pt = self._project(point["X"], point["Y"], point["Z_total"])
            painter.setPen(QPen(QColor(254, 240, 138, 153), 2, Qt.PenStyle.DashLine))
            painter.drawLine(b_pt, t_pt)
            painter.setBrush(QBrush(QColor(250, 204, 21)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(t_pt, 4.5, 4.5)
            painter.setPen(QColor(254, 240, 138))
            painter.setFont(QFont("Arial", 9))
            painter.drawText(QPointF(t_pt.x() + 10, t_pt.y() - 5), f"H:{point.get('mouseHeight', 0):.1f}mm")

    def _draw_hud(self, painter: QPainter):
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(15, 23, 42, 220))
        painter.drawRoundedRect(12, 12, 300, 62, 10, 10)
        painter.setPen(QColor(226, 232, 240))
        painter.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        painter.drawText(24, 34, "3D 预览")
        painter.setPen(QColor(148, 163, 184))
        painter.setFont(QFont("Arial", 9))
        painter.drawText(
            24,
            54,
            f"W {self.scene_config['width']:.0f}mm  D {self.scene_config['depth']:.0f}mm  H {self.scene_config['height']:.0f}mm",
        )
