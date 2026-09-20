"""
2D dashboard for trajectory analysis.
"""
import math

from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QBrush, QFont, QPainter, QPen
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QSizePolicy, QVBoxLayout, QWidget

DEFAULT_SCENE = {
    "width": 180.0,
    "depth": 310.0,
    "height": 150.0,
}


class TrajectoryView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.data: list = []
        self.scene_config = dict(DEFAULT_SCENE)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_scene_config(self, scene_config: dict | None):
        self.scene_config = dict(DEFAULT_SCENE)
        if scene_config:
            self.scene_config.update(scene_config)
        self.update()

    def set_data(self, data: list):
        self.data = data or []
        self.update()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(15, 23, 42))

        width = self.scene_config["width"]
        depth = self.scene_config["depth"]
        w, h = self.width(), self.height()
        pad_x, pad_y = 55, 35
        min_x, max_x = -width / 2.0 - 10, width / 2.0 + 10
        min_y, max_y = -10, depth + 10

        def map_x(val): return pad_x + (val - min_x) / (max_x - min_x) * (w - 2 * pad_x)
        def map_y(val): return h - pad_y - (val - min_y) / (max_y - min_y) * (h - 2 * pad_y)

        painter.setPen(QPen(QColor(30, 41, 59), 1))
        for x in (-width / 2.0, -width / 4.0, 0, width / 4.0, width / 2.0):
            painter.drawLine(QPointF(map_x(x), map_y(min_y)), QPointF(map_x(x), map_y(max_y)))
        for y in (0, depth / 3.0, depth * 2.0 / 3.0, depth):
            painter.drawLine(QPointF(map_x(min_x), map_y(y)), QPointF(map_x(max_x), map_y(y)))

        painter.setPen(QColor(100, 116, 139))
        painter.setFont(QFont("Arial", 9))
        for x in (-width / 2.0, 0, width / 2.0):
            painter.drawText(QPointF(map_x(x) - 12, map_y(min_y) + 15), f"{x:.0f}")
        for y in (0, depth / 2.0, depth):
            painter.drawText(QPointF(map_x(min_x) - 40, map_y(y) + 4), f"{y:.0f}")

        if not self.data:
            painter.setPen(QColor(148, 163, 184))
            painter.setFont(QFont("Arial", 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无轨迹数据\n请先框选目标并完成解算")
            return

        painter.setPen(QPen(QColor(34, 211, 238, 190), 2))
        for index in range(len(self.data) - 1):
            cur, nxt = self.data[index], self.data[index + 1]
            painter.drawLine(QPointF(map_x(cur["X"]), map_y(cur["Y"])), QPointF(map_x(nxt["X"]), map_y(nxt["Y"])))

        for index, point in enumerate(self.data, start=1):
            painter.setBrush(QBrush(QColor(250, 204, 21)))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawEllipse(QPointF(map_x(point["X"]), map_y(point["Y"])), 5, 5)
            painter.setPen(QColor(254, 240, 138))
            painter.setFont(QFont("Arial", 9, QFont.Weight.Bold))
            painter.drawText(QPointF(map_x(point["X"]) + 8, map_y(point["Y"]) - 8), str(index))

        painter.setPen(QColor(34, 211, 238))
        painter.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        painter.drawText(QPointF(pad_x, 20), "俯视轨迹 (X-Y)")


class HeightCurveView(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.data: list = []
        self.scene_config = dict(DEFAULT_SCENE)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

    def set_scene_config(self, scene_config: dict | None):
        self.scene_config = dict(DEFAULT_SCENE)
        if scene_config:
            self.scene_config.update(scene_config)
        self.update()

    def set_data(self, data: list):
        self.data = data or []
        self.update()

    def paintEvent(self, e):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor(15, 23, 42))

        w, h = self.width(), self.height()
        pad_x, pad_y = 55, 35
        scene_height = self.scene_config["height"]
        max_index = max(2, len(self.data))

        if self.data:
            heights = [point["mouseHeight"] for point in self.data]
            raw_max, raw_min = max(heights), min(heights)
            spread = max(10.0, raw_max - raw_min)
            max_h = min(scene_height, raw_max + spread * 0.2)
            min_h = max(0.0, raw_min - spread * 0.2)
        else:
            max_h, min_h = scene_height, 0.0

        def map_x(idx): return pad_x + (idx - 1) / max(max_index - 1, 1) * (w - 2 * pad_x)
        def map_h(val): return h - pad_y - (val - min_h) / max(max_h - min_h, 1.0) * (h - 2 * pad_y)

        painter.setPen(QPen(QColor(30, 41, 59), 1))
        for tick in range(6):
            h_val = min_h + tick * (max_h - min_h) / 5.0
            painter.drawLine(QPointF(pad_x, map_h(h_val)), QPointF(w - pad_x, map_h(h_val)))
            painter.setPen(QColor(100, 116, 139))
            painter.setFont(QFont("Arial", 8))
            painter.drawText(QPointF(pad_x - 42, map_h(h_val) + 4), f"{h_val:.1f}")
            painter.setPen(QPen(QColor(30, 41, 59), 1))

        for index in range(1, max_index + 1):
            x = map_x(index)
            painter.drawLine(QPointF(x, pad_y), QPointF(x, h - pad_y))
            painter.setPen(QColor(100, 116, 139))
            painter.setFont(QFont("Arial", 8))
            painter.drawText(QPointF(x - 6, h - pad_y + 15), str(index))
            painter.setPen(QPen(QColor(30, 41, 59), 1))

        if not self.data:
            painter.setPen(QColor(148, 163, 184))
            painter.setFont(QFont("Arial", 12))
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "暂无高度数据")
            return

        points = [QPointF(map_x(index), map_h(item["mouseHeight"])) for index, item in enumerate(self.data, start=1)]
        painter.setPen(QPen(QColor(250, 204, 21), 3))
        for index in range(len(points) - 1):
            painter.drawLine(points[index], points[index + 1])

        for point, item in zip(points, self.data):
            painter.setBrush(QBrush(QColor(52, 211, 238)))
            painter.setPen(QPen(QColor(15, 23, 42), 2))
            painter.drawEllipse(point, 5, 5)
            painter.setPen(QColor(207, 250, 254))
            painter.setFont(QFont("Arial", 9, QFont.Weight.Bold))
            painter.drawText(QPointF(point.x() - 15, point.y() - 12), f"{item['mouseHeight']:.1f}")

        painter.setPen(QColor(250, 204, 21))
        painter.setFont(QFont("Arial", 10, QFont.Weight.Bold))
        painter.drawText(QPointF(pad_x, 20), "高度变化曲线")


class Dashboard2D(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.scene_config = dict(DEFAULT_SCENE)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        self.summary_label = QLabel("等待轨迹数据")
        self.summary_label.setObjectName("summaryLabel")
        self.summary_label.setWordWrap(True)
        root.addWidget(self.summary_label)

        layout = QHBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.trajectory_view = TrajectoryView()
        self.height_view = HeightCurveView()
        layout.addWidget(self.trajectory_view)
        layout.addWidget(self.height_view)
        root.addLayout(layout)

        self.setStyleSheet(
            """
            QLabel#summaryLabel {
                color: #cbd5e1;
                background: #0f172a;
                border: 1px solid #1e293b;
                border-radius: 8px;
                padding: 8px 10px;
            }
            """
        )

    def set_scene_config(self, scene_config: dict | None):
        self.scene_config = dict(DEFAULT_SCENE)
        if scene_config:
            self.scene_config.update(scene_config)
        self.trajectory_view.set_scene_config(self.scene_config)
        self.height_view.set_scene_config(self.scene_config)

    def set_data(self, data: list):
        self.trajectory_view.set_data(data)
        self.height_view.set_data(data)
        self.summary_label.setText(self._build_summary(data or []))

    def _build_summary(self, data: list) -> str:
        if not data:
            return "当前没有可展示的轨迹结果。请先在图像页使用 YOLO 工具框选目标。"

        path_length = 0.0
        for index in range(len(data) - 1):
            dx = data[index + 1]["X"] - data[index]["X"]
            dy = data[index + 1]["Y"] - data[index]["Y"]
            path_length += math.hypot(dx, dy)

        heights = [item["mouseHeight"] for item in data]
        return (
            f"共 {len(data)} 个轨迹采样点，"
            f"轨迹长度约 {path_length:.1f} mm，"
            f"高度范围 {min(heights):.1f} - {max(heights):.1f} mm。"
        )
