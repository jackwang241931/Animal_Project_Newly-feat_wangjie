# -*- coding: utf-8 -*-
"""
动物箱标定测试代码

这个文件由 `gemini动物箱标定代码.txt` 中的 React/JavaScript 标定工具改写而来。
它保留了核心数学逻辑，并用 Tkinter 提供一个可直接运行的 Python 桌面界面。

主要功能：
- 载入底图，在画布上打点、移动、删除、拖拽、缩放。
- 根据标定点自动补全 2D 网格。
- 根据底面和立面点构建 3D 点结构与 DLT 摄像机投影矩阵。
- Map YOLO boxes to animal-box physical coordinates.
- Import/export LabelMe-style JSON and 3D point JSON.

依赖：
- Python 3.9+
- Pillow 用于显示 jpg/png 等图片：pip install pillow
"""

from __future__ import annotations

import json
import math
import time
from itertools import combinations
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

try:
    from PIL import Image, ImageTk
except Exception:  # pragma: no cover - 运行环境没有 Pillow 时给出友好提示
    Image = None
    ImageTk = None


Point = Dict[str, Any]
Matrix = List[List[float]]

BOTTOM_W = 180.0
BOTTOM_L = 360.0
TOP_W = 180.0
TOP_L = 360.0
HEIGHT_EST = 150.0
MAX_LABEL5_PREVIEW_POINTS = 2500
MAX_LABEL5_PREVIEW_LINES = 4500
MAX_LABEL5_3D_POINTS = 900
MAX_LABEL5_3D_LINES = 1800
VOLUME_KNN = 8
BOTTOM_CONTACT_MAX_PIXEL_ERROR = 25.0
Y_BOTTOM_FUSION_WEIGHT = 0.10
Y_BOTTOM_FUSION_MAX_DELTA = 120.0
Y_BOTTOM_FUSION_FAR_WEIGHT = 0.03
BBOX_SCALE_NEAR_PX = 560.0
BBOX_SCALE_FAR_PX = 125.0
Y_SIZE_FUSION_WEIGHT = 4.0
Y_CENTER_FUSION_WEIGHT = 0.25

LABEL_COLORS = {
    "1": "#22c55e",  # bottom plane
    "2": "#f97316",  # near vertical plane
    "3": "#ec4899",  # middle vertical plane
    "4": "#06b6d4",  # far vertical plane
    "5": "#a855f7",  # 3D volume
}

LAYER_NAMES = {
    "1": "底部水平平面 (Z=0)",
    "2": "最近端垂直面 (Y=0)",
    "3": "中间垂直面 (Y=180)",
    "4": "最远端垂直面 (Y=360)",
    "5": "全空间立体体积",
}


def layer_title(label: Any) -> str:
    label_text = str(label or "1")
    return f"{label_text} - {LAYER_NAMES.get(label_text, '未命名图层')}"


def layer_from_display(value: Any) -> str:
    text = str(value or "1").strip()
    for label in LAYER_NAMES:
        if text == label or text.startswith(f"{label} ") or text.startswith(f"{label}-") or text.startswith(f"{label} -"):
            return label
    return text[:1] if text[:1] in LAYER_NAMES else "1"


def clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def estimate_y_from_bbox_scale(width: float, height: float) -> Optional[float]:
    if width <= 0.0 or height <= 0.0:
        return None
    area_scale = math.sqrt(width * height)
    width_scale = width * 1.15
    scale = max(width_scale, area_scale * 0.85)
    near = max(BBOX_SCALE_NEAR_PX, BBOX_SCALE_FAR_PX + 1.0)
    far = max(1.0, BBOX_SCALE_FAR_PX)
    scale = clamp(scale, far, near)
    # Apparent size changes roughly multiplicatively with depth, so interpolate in log-space.
    denom = math.log(near) - math.log(far)
    if abs(denom) < 1e-9:
        return None
    depth_ratio = (math.log(near) - math.log(scale)) / denom
    return clamp(depth_ratio * BOTTOM_L, 0.0, BOTTOM_L)


def blend_hex_color(color: str, alpha: float, bg: str = "#020617") -> str:
    color = str(color or "").strip()
    bg = str(bg or "#020617").strip()
    if not color.startswith("#"):
        return color

    def parse_hex(value: str) -> Tuple[int, int, int]:
        value = value.lstrip("#")
        if len(value) == 3:
            value = "".join(ch * 2 for ch in value)
        if len(value) != 6:
            return (255, 255, 255)
        return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)

    alpha = clamp(float(alpha), 0.0, 1.0)
    r, g, b = parse_hex(color)
    br, bgc, bb = parse_hex(bg)
    rr = round(r * alpha + br * (1.0 - alpha))
    gg = round(g * alpha + bgc * (1.0 - alpha))
    bb2 = round(b * alpha + bb * (1.0 - alpha))
    return f"#{rr:02x}{gg:02x}{bb2:02x}"


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return default


def make_point(x: float, y: float, label: str = "1", **extra: Any) -> Point:
    point: Point = {
        "id": time.time_ns(),
        "x": float(x),
        "y": float(y),
        "label": str(label),
    }
    point.update(extra)
    return point


def export_location3d(location: Optional[Point]) -> Optional[Dict[str, Any]]:
    if not location:
        return None
    data: Dict[str, Any] = {
        "X": round(float(location.get("X", 0.0)), 2),
        "Y": round(float(location.get("Y", 0.0)), 2),
        "Z": round(float(location.get("Z", 0.0)), 2),
        "pixelError": round(float(location.get("pixelError", 0.0)), 2),
        "method": location.get("method", ""),
    }
    if location.get("col") is not None:
        data["col"] = location.get("col")
    if location.get("row") is not None:
        data["row"] = location.get("row")
    if location.get("layer") is not None:
        data["layer"] = location.get("layer")
    if location.get("matchedPixelX") is not None and location.get("matchedPixelY") is not None:
        data["matchedPixel"] = [
            round(float(location["matchedPixelX"]), 2),
            round(float(location["matchedPixelY"]), 2),
        ]
    return data


def solve_linear_system(A: Sequence[Sequence[float]], b: Sequence[float]) -> Optional[List[float]]:
    """Gaussian elimination with partial pivoting."""
    n = len(A)
    mat = [list(map(float, row)) for row in A]
    rhs = list(map(float, b))
    if n == 0 or any(len(row) != n for row in mat) or len(rhs) != n:
        return None

    for i in range(n):
        max_row = i
        max_el = abs(mat[i][i])
        for k in range(i + 1, n):
            if abs(mat[k][i]) > max_el:
                max_el = abs(mat[k][i])
                max_row = k

        if max_el < 1e-10:
            return None

        mat[i], mat[max_row] = mat[max_row], mat[i]
        rhs[i], rhs[max_row] = rhs[max_row], rhs[i]

        for k in range(i + 1, n):
            c = -mat[k][i] / mat[i][i]
            for j in range(i, n):
                mat[k][j] = 0.0 if i == j else mat[k][j] + c * mat[i][j]
            rhs[k] += c * rhs[i]

    x = [0.0] * n
    for i in range(n - 1, -1, -1):
        if abs(mat[i][i]) < 1e-10:
            return None
        x[i] = rhs[i] / mat[i][i]
        for k in range(i - 1, -1, -1):
            rhs[k] -= mat[k][i] * x[i]
    return x


def get_perspective_transform(src: Sequence[Point], dst: Sequence[Point]) -> Optional[Matrix]:
    if len(src) != 4 or len(dst) != 4:
        return None

    A: List[List[float]] = []
    B: List[float] = []
    for i in range(4):
        x = float(src[i]["x"])
        y = float(src[i]["y"])
        u = float(dst[i]["x"])
        v = float(dst[i]["y"])
        A.append([x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y])
        A.append([0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y])
        B.extend([u, v])

    h = solve_linear_system(A, B)
    if h is None:
        return None

    return [
        [h[0], h[1], h[2]],
        [h[3], h[4], h[5]],
        [h[6], h[7], 1.0],
    ]


def get_perspective_transform_fit(src: Sequence[Point], dst: Sequence[Point]) -> Optional[Matrix]:
    if len(src) != len(dst) or len(src) < 4:
        return None
    if len(src) == 4:
        return get_perspective_transform(src, dst)

    A: List[List[float]] = []
    B: List[float] = []
    for s, d in zip(src, dst):
        x = float(s["x"])
        y = float(s["y"])
        u = float(d["x"])
        v = float(d["y"])
        A.append([x, y, 1.0, 0.0, 0.0, 0.0, -u * x, -u * y])
        B.append(u)
        A.append([0.0, 0.0, 0.0, x, y, 1.0, -v * x, -v * y])
        B.append(v)

    ATA = [[0.0 for _ in range(8)] for _ in range(8)]
    ATb = [0.0 for _ in range(8)]
    for row, b in zip(A, B):
        for i in range(8):
            ATb[i] += row[i] * b
            for j in range(8):
                ATA[i][j] += row[i] * row[j]

    h = solve_linear_system(ATA, ATb)
    if h is None:
        return None

    return [
        [h[0], h[1], h[2]],
        [h[3], h[4], h[5]],
        [h[6], h[7], 1.0],
    ]


def apply_homography(H: Optional[Matrix], pt: Point) -> Optional[Point]:
    if H is None:
        return None
    x = float(pt["x"])
    y = float(pt["y"])
    w = H[2][0] * x + H[2][1] * y + H[2][2]
    if abs(w) < 1e-8:
        w = -1e-8 if w < 0 else 1e-8
    return {
        "x": (H[0][0] * x + H[0][1] * y + H[0][2]) / w,
        "y": (H[1][0] * x + H[1][1] * y + H[1][2]) / w,
    }


def solve_camera_matrix(pts: Sequence[Point]) -> Optional[Matrix]:
    if len(pts) < 6:
        return None

    N = float(len(pts))
    cu = sum(float(p["x"]) for p in pts) / N
    cv = sum(float(p["y"]) for p in pts) / N
    cX = sum(float(p["x3"]) for p in pts) / N
    cY = sum(float(p["y3"]) for p in pts) / N
    cZ = sum(float(p["z3"]) for p in pts) / N

    s2d = 0.0
    s3d = 0.0
    for p in pts:
        s2d += math.hypot(float(p["x"]) - cu, float(p["y"]) - cv)
        s3d += math.sqrt(
            (float(p["x3"]) - cX) ** 2
            + (float(p["y3"]) - cY) ** 2
            + (float(p["z3"]) - cZ) ** 2
        )

    s2d = math.sqrt(2.0) * N / (s2d or 1.0)
    s3d = math.sqrt(3.0) * N / (s3d or 1.0)

    A: List[List[float]] = []
    b: List[float] = []
    for pt in pts:
        u = (float(pt["x"]) - cu) * s2d
        v = (float(pt["y"]) - cv) * s2d
        X = (float(pt["x3"]) - cX) * s3d
        Y = (float(pt["y3"]) - cY) * s3d
        Z = (float(pt["z3"]) - cZ) * s3d

        A.append([X, Y, Z, 1.0, 0.0, 0.0, 0.0, 0.0, -u * X, -u * Y, -u * Z])
        b.append(u)
        A.append([0.0, 0.0, 0.0, 0.0, X, Y, Z, 1.0, -v * X, -v * Y, -v * Z])
        b.append(v)

    ATA = [[0.0 for _ in range(11)] for _ in range(11)]
    ATb = [0.0 for _ in range(11)]
    for k, row in enumerate(A):
        for i in range(11):
            ATb[i] += row[i] * b[k]
            for j in range(11):
                ATA[i][j] += row[i] * row[j]

    pn = solve_linear_system(ATA, ATb)
    if pn is None:
        return None

    P_norm = [
        [pn[0], pn[1], pn[2], pn[3]],
        [pn[4], pn[5], pn[6], pn[7]],
        [pn[8], pn[9], pn[10], 1.0],
    ]

    P_tmp = [[0.0 for _ in range(4)] for _ in range(3)]
    for i in range(3):
        P_tmp[i][0] = P_norm[i][0] * s3d
        P_tmp[i][1] = P_norm[i][1] * s3d
        P_tmp[i][2] = P_norm[i][2] * s3d
        P_tmp[i][3] = (
            P_norm[i][0] * (-s3d * cX)
            + P_norm[i][1] * (-s3d * cY)
            + P_norm[i][2] * (-s3d * cZ)
            + P_norm[i][3]
        )

    P = [[0.0 for _ in range(4)] for _ in range(3)]
    for j in range(4):
        P[0][j] = (1.0 / s2d) * P_tmp[0][j] + cu * P_tmp[2][j]
        P[1][j] = (1.0 / s2d) * P_tmp[1][j] + cv * P_tmp[2][j]
        P[2][j] = P_tmp[2][j]
    return P


def project_point(P: Optional[Matrix], X: float, Y: float, Z: float) -> Optional[Point]:
    if P is None:
        return None
    w = P[2][0] * X + P[2][1] * Y + P[2][2] * Z + P[2][3]
    if abs(w) < 1e-8:
        w = 1e-8
    return {
        "x": (P[0][0] * X + P[0][1] * Y + P[0][2] * Z + P[0][3]) / w,
        "y": (P[1][0] * X + P[1][1] * Y + P[1][2] * Z + P[1][3]) / w,
    }


def solve_x_with_yz(P: Optional[Matrix], u: float, Y: float, Z: float) -> Optional[float]:
    if P is None:
        return None
    num = P[0][1] * Y + P[0][2] * Z + P[0][3] - u * (P[2][1] * Y + P[2][2] * Z + P[2][3])
    den = u * P[2][0] - P[0][0]
    if abs(den) < 1e-8:
        return None
    return num / den


def solve_xy_with_z(P: Optional[Matrix], u: float, v: float, Z: float) -> Optional[Point]:
    if P is None:
        return None

    A = u * P[2][0] - P[0][0]
    B = u * P[2][1] - P[0][1]
    C = (P[0][2] - u * P[2][2]) * Z + (P[0][3] - u * P[2][3])

    D = v * P[2][0] - P[1][0]
    E = v * P[2][1] - P[1][1]
    F = (P[1][2] - v * P[2][2]) * Z + (P[1][3] - v * P[2][3])

    det = A * E - B * D
    if abs(det) < 1e-8:
        return None

    return {
        "x": (C * E - B * F) / det,
        "y": (A * F - C * D) / det,
    }


def solve_height_with_xyzv(P: Optional[Matrix], X: float, Y: float, Z_base: float, v_2d: float) -> float:
    if P is None:
        return 0.0

    num_fixed = P[1][0] * X + P[1][1] * Y + P[1][3]
    den_fixed = P[2][0] * X + P[2][1] * Y + P[2][3]
    a = v_2d * P[2][2] - P[1][2]
    b = num_fixed - v_2d * den_fixed
    if abs(a) < 1e-8:
        return 0.0
    return max(0.0, (b / a) - Z_base)


def is_point_in_polygon(point: Point, polygon: Sequence[Point]) -> bool:
    if len(polygon) < 3:
        return False

    x = float(point["x"])
    y = float(point["y"])
    inside = False
    j = len(polygon) - 1
    for i in range(len(polygon)):
        xi = float(polygon[i]["x"])
        yi = float(polygon[i]["y"])
        xj = float(polygon[j]["x"])
        yj = float(polygon[j]["y"])
        if (yi > y) != (yj > y):
            denom = (yj - yi) or 1e-12
            intersect_x = (xj - xi) * (y - yi) / denom + xi
            if x < intersect_x:
                inside = not inside
        j = i
    return inside


def any_point_in_polygon(points: Sequence[Point], polygon: Sequence[Point]) -> bool:
    return any(is_point_in_polygon(point, polygon) for point in points)


def all_points_in_polygon(points: Sequence[Point], polygon: Sequence[Point]) -> bool:
    return bool(points) and all(is_point_in_polygon(point, polygon) for point in points)


def convex_hull(points: Sequence[Point]) -> List[Point]:
    if len(points) <= 1:
        return list(points)

    sorted_pts = sorted(points, key=lambda p: (float(p["x"]), float(p["y"])))

    def cross(o: Point, a: Point, b: Point) -> float:
        return (float(a["x"]) - float(o["x"])) * (float(b["y"]) - float(o["y"])) - (
            float(a["y"]) - float(o["y"])
        ) * (float(b["x"]) - float(o["x"]))

    lower: List[Point] = []
    for p in sorted_pts:
        while len(lower) >= 2 and cross(lower[-2], lower[-1], p) <= 0:
            lower.pop()
        lower.append(p)

    upper: List[Point] = []
    for p in reversed(sorted_pts):
        while len(upper) >= 2 and cross(upper[-2], upper[-1], p) <= 0:
            upper.pop()
        upper.append(p)

    hull = lower[:-1] + upper[:-1]
    result: List[Point] = []
    seen: set[int] = set()
    for p in hull:
        ident = int(p.get("id", id(p)))
        if ident not in seen:
            result.append(p)
            seen.add(ident)
    return result


def quad_area(quad: Sequence[Point]) -> float:
    if len(quad) != 4:
        return 0.0
    total = 0.0
    for i, p in enumerate(quad):
        q = quad[(i + 1) % 4]
        total += float(p["x"]) * float(q["y"]) - float(p["y"]) * float(q["x"])
    return abs(total) * 0.5


def order_quad_by_screen_position(quad: Sequence[Point]) -> Tuple[Point, Point, Point, Point]:
    best_quad = sorted(quad, key=lambda p: float(p["x"]))
    lefts = sorted(best_quad[:2], key=lambda p: float(p["y"]))
    rights = sorted(best_quad[2:], key=lambda p: float(p["y"]))
    pTL = lefts[0]
    pBL = lefts[1]
    pTR = rights[0]
    pBR = rights[1]
    return pTL, pTR, pBL, pBR


def robust_grid_count(values: Iterable[float]) -> int:
    vals = sorted(float(v) for v in values)
    if len(vals) < 2:
        return 10

    clusters = [vals[0]]
    for value in vals[1:]:
        if value - clusters[-1] > 15:
            clusters.append(value)

    if len(clusters) < 2:
        return 10

    diffs = sorted(clusters[i] - clusters[i - 1] for i in range(1, len(clusters)))
    median_step = diffs[len(diffs) // 2] or 1.0
    count = round(1000.0 / median_step) + 1
    return max(2, min(100, count))


class AnimalBoxCalibrationApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("动物箱标定测试工具")
        self.root.geometry("1320x820")
        self.root.minsize(1000, 680)

        self.image = None
        self.tk_image = None
        self.image_path: Optional[Path] = None
        self.image_meta: Dict[str, Any] = {"fileName": "", "width": 0, "height": 0}
        self.test_image = None
        self.test_tk_image = None
        self.registration_ref_tk_image = None
        self.test_image_path: Optional[Path] = None
        self.test_image_meta: Dict[str, Any] = {"fileName": "", "width": 0, "height": 0}

        self.points: List[Point] = []
        self.computed_points: List[Point] = []
        self.grid_data: List[Point] = []
        self.lines_map: Dict[str, List[Tuple[Point, Point]]] = {}
        self.yolo_boxes: List[Point] = []
        self.registration_pairs: List[Dict[str, Point]] = []
        self.registration: Dict[str, Any] = {
            "H_test_to_ref": None,
            "H_ref_to_test": None,
            "errors": [],
            "meanError": None,
            "maxError": None,
        }

        self.view_mode = tk.StringVar(value="perspective")
        self.current_label = tk.StringVar(value=layer_title("1"))
        self.tool_mode = tk.StringVar(value="add")
        self.cols = tk.IntVar(value=18)
        self.rows = tk.IntVar(value=36)
        self.opacity = tk.DoubleVar(value=0.8)

        self.draw_scale = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.is_panning = False
        self.dragging_point_id: Optional[int] = None
        self.drawing_yolo_box: Optional[Point] = None
        self.last_mouse = (0, 0)
        self.cam_yaw = math.pi / 4
        self.cam_pitch = math.pi / 6
        self.sidebar_mouse_inside = False

        self._build_ui()
        self.root.after(100, self.redraw)

    # ------------------------------------------------------------------
    # UI
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        self.root.configure(bg="#020617")

        self.sidebar_shell = tk.Frame(self.root, width=350, bg="#0f172a")
        self.sidebar_shell.pack(side=tk.LEFT, fill=tk.Y)
        self.sidebar_shell.pack_propagate(False)

        self.sidebar_canvas = tk.Canvas(
            self.sidebar_shell,
            bg="#0f172a",
            highlightthickness=0,
            bd=0,
        )
        self.sidebar_scrollbar = tk.Scrollbar(
            self.sidebar_shell,
            orient=tk.VERTICAL,
            command=self.sidebar_canvas.yview,
        )
        self.sidebar_canvas.configure(yscrollcommand=self.sidebar_scrollbar.set)
        self.sidebar_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.sidebar_scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.sidebar = tk.Frame(self.sidebar_canvas, bg="#0f172a", padx=14, pady=14)
        self.sidebar_window = self.sidebar_canvas.create_window((0, 0), window=self.sidebar, anchor="nw")
        self.sidebar.bind("<Configure>", self._on_sidebar_frame_configure)
        self.sidebar_canvas.bind("<Configure>", self._on_sidebar_canvas_configure)
        self.sidebar_canvas.bind("<Enter>", lambda _event: self._set_sidebar_mouse_inside(True))
        self.sidebar_canvas.bind("<Leave>", lambda _event: self._set_sidebar_mouse_inside(False))
        self.sidebar.bind("<Enter>", lambda _event: self._set_sidebar_mouse_inside(True))
        self.sidebar.bind("<Leave>", lambda _event: self._set_sidebar_mouse_inside(False))
        self.root.bind_all("<MouseWheel>", self._on_global_mousewheel, add="+")
        self.root.bind_all("<Button-4>", self._on_global_mousewheel, add="+")
        self.root.bind_all("<Button-5>", self._on_global_mousewheel, add="+")

        self.canvas = tk.Canvas(self.root, bg="#020617", highlightthickness=0)
        self.canvas.pack(side=tk.RIGHT, fill=tk.BOTH, expand=True)

        self.canvas.bind("<ButtonPress-1>", self.on_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_mouse_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)
        self.canvas.bind("<MouseWheel>", self.on_mouse_wheel)
        self.canvas.bind("<Button-4>", lambda _event: self.zoom(1.1))
        self.canvas.bind("<Button-5>", lambda _event: self.zoom(1 / 1.1))
        self.canvas.bind("<Configure>", lambda _event: self.redraw())

        title = tk.Label(
            self.sidebar,
            text="3D 点位构建系统",
            bg="#0f172a",
            fg="#e5e7eb",
            font=("Microsoft YaHei UI", 15, "bold"),
            anchor="w",
        )
        title.pack(fill=tk.X)
        subtitle = tk.Label(
            self.sidebar,
            text="2D Inference to 3D Map",
            bg="#0f172a",
            fg="#94a3b8",
            font=("Microsoft YaHei UI", 9),
            anchor="w",
        )
        subtitle.pack(fill=tk.X, pady=(0, 12))

        self._section("快捷工具")
        self._tool_buttons()

        self._section("项目文件")
        self._button("载入新视角的底图", self.load_image, "#1d4ed8")
        self._button("导入工程 JSON", self.import_json, "#047857")
        self._button("载入测试图", self.load_test_image, "#7c3aed")
        self._button("导入配准 JSON", self.import_registration_json, "#6d28d9")
        self._button("清空项目", self.reset_project, "#9f1239")

        self._section("全景工作流")
        mode_row = tk.Frame(self.sidebar, bg="#0f172a")
        mode_row.pack(fill=tk.X, pady=2)
        self._button("1. 俯视", lambda: self.set_view_mode("topdown"), "#334155", parent=mode_row).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4)
        )
        self._button("2. 侧视", lambda: self.set_view_mode("perspective"), "#334155", parent=mode_row).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0)
        )
        self._button("4. 3D 预览", lambda: self.set_view_mode("3d"), "#4f46e5")
        self._button("5. 2D 轨迹分析", lambda: self.set_view_mode("chart"), "#be185d")
        self._button("6. 双图配准", lambda: self.set_view_mode("registration"), "#7c2d12")

        self.layer_shell = tk.Frame(self.sidebar, bg="#0f172a")
        self.layer_shell.pack(fill=tk.X)
        self._section("图层与参数", parent=self.layer_shell)
        label_row = tk.Frame(self.layer_shell, bg="#0f172a")
        label_row.pack(fill=tk.X, pady=3)
        tk.Label(label_row, text="图层", bg="#0f172a", fg="#cbd5e1").pack(side=tk.LEFT)
        self.label_box = ttk.Combobox(
            label_row,
            textvariable=self.current_label,
            values=tuple(layer_title(label) for label in LAYER_NAMES),
            state="readonly",
            width=25,
        )
        self.label_box.pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(8, 0))
        self.label_box.bind("<<ComboboxSelected>>", lambda _event: self.on_label_selected())
        self._layer_legend(parent=self.layer_shell)

        self.physical_param_shell = tk.Frame(self.layer_shell, bg="#0f172a")
        self.physical_param_shell.pack(fill=tk.X)
        self._entry_row("物理列数", self.cols, "X", parent=self.physical_param_shell)
        self._entry_row("物理行数", self.rows, "Y", parent=self.physical_param_shell)


        self._section("双图配准")
        self._button("计算配准矩阵", self.solve_registration, "#7c3aed")
        self._button("清空配准点", self.clear_registration_points, "#475569")

        self._section("缩放")
        zoom_row = tk.Frame(self.sidebar, bg="#0f172a")
        zoom_row.pack(fill=tk.X, pady=(8, 2))
        self._button("缩小", lambda: self.zoom(1 / 1.2), "#334155", parent=zoom_row).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4)
        )
        self._button("放大", lambda: self.zoom(1.2), "#334155", parent=zoom_row).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0)
        )

        self._section("显示")
        self._opacity_controls()

        self.status_label = tk.Label(
            self.sidebar,
            text="就绪",
            bg="#0f172a",
            fg="#94a3b8",
            wraplength=295,
            justify=tk.LEFT,
            anchor="w",
            font=("Microsoft YaHei UI", 9),
        )
        self.status_label.pack(fill=tk.X, pady=(12, 0))
        self._bottom_action_bar()

    def _on_sidebar_frame_configure(self, _event: tk.Event) -> None:
        self.sidebar_canvas.configure(scrollregion=self.sidebar_canvas.bbox("all"))

    def _on_sidebar_canvas_configure(self, event: tk.Event) -> None:
        self.sidebar_canvas.itemconfigure(self.sidebar_window, width=event.width)

    def _set_sidebar_mouse_inside(self, is_inside: bool) -> None:
        self.sidebar_mouse_inside = is_inside

    def _on_global_mousewheel(self, event: tk.Event) -> Optional[str]:
        if not self.sidebar_mouse_inside:
            return None

        if getattr(event, "num", None) == 4:
            units = -3
        elif getattr(event, "num", None) == 5:
            units = 3
        else:
            delta = getattr(event, "delta", 0)
            units = -int(delta / 120) if delta else 0
            if units == 0 and delta:
                units = -1 if delta > 0 else 1

        if units:
            self.sidebar_canvas.yview_scroll(units, "units")
        return "break"

    def _section(self, text: str, parent: Optional[tk.Widget] = None) -> tk.Label:
        parent = parent or self.sidebar
        label = tk.Label(
            parent,
            text=text,
            bg="#0f172a",
            fg="#94a3b8",
            font=("Microsoft YaHei UI", 9, "bold"),
            anchor="w",
        )
        label.pack(fill=tk.X, pady=(14, 5))
        return label

    def _tool_buttons(self) -> None:
        tool_grid = tk.Frame(self.sidebar, bg="#0f172a")
        tool_grid.pack(fill=tk.X, pady=2)
        tools = [
            ("打点", "add"),
            ("移动", "move"),
            ("删点", "delete"),
            ("拖动", "pan"),
            ("YOLO", "yolo"),
        ]
        for index, (text, mode) in enumerate(tools):
            btn = tk.Button(
                tool_grid,
                text=text,
                command=lambda m=mode: self.set_tool_mode(m),
                relief=tk.FLAT,
                bg="#1e293b",
                fg="#e5e7eb",
                activebackground="#334155",
                activeforeground="#ffffff",
                font=("Microsoft YaHei UI", 9, "bold"),
                padx=2,
                pady=8,
                cursor="hand2",
            )
            btn.grid(row=0, column=index, sticky="ew", padx=2)
            tool_grid.columnconfigure(index, weight=1)

    def _layer_legend(self, parent: Optional[tk.Widget] = None) -> None:
        parent = parent or self.sidebar
        legend = tk.Frame(parent, bg="#0f172a")
        legend.pack(fill=tk.X, pady=(6, 0))
        for label, name in LAYER_NAMES.items():
            row = tk.Frame(legend, bg="#0f172a")
            row.pack(fill=tk.X, pady=1)
            swatch = tk.Label(row, text="  ", bg=LABEL_COLORS.get(label, "#e5e7eb"), width=2)
            swatch.pack(side=tk.LEFT, padx=(0, 6))
            tk.Label(
                row,
                text=f"标签 {label}：{name}",
                bg="#0f172a",
                fg="#cbd5e1",
                font=("Microsoft YaHei UI", 8),
                anchor="w",
            ).pack(side=tk.LEFT, fill=tk.X, expand=True)

    def _opacity_controls(self) -> None:
        row = tk.Frame(self.sidebar, bg="#0f172a")
        row.pack(fill=tk.X, pady=3)
        tk.Label(row, text="全局透明度", bg="#0f172a", fg="#cbd5e1", anchor="w").pack(side=tk.LEFT)
        self.opacity_label = tk.Label(
            row,
            text=f"{round(float(self.opacity.get()) * 100)}%",
            bg="#0f172a",
            fg="#94a3b8",
            font=("Consolas", 9),
            width=5,
            anchor="e",
        )
        self.opacity_label.pack(side=tk.RIGHT)

        scale = tk.Scale(
            self.sidebar,
            from_=0.0,
            to=1.0,
            resolution=0.05,
            orient=tk.HORIZONTAL,
            variable=self.opacity,
            command=self.on_opacity_change,
            bg="#0f172a",
            fg="#cbd5e1",
            troughcolor="#1e293b",
            activebackground="#10b981",
            highlightthickness=0,
            showvalue=False,
        )
        scale.pack(fill=tk.X, pady=(0, 3))

    def _bottom_action_bar(self) -> None:
        action_bar = tk.Frame(self.sidebar, bg="#0f172a")
        action_bar.pack(fill=tk.X, pady=(8, 0))

        tk.Label(
            action_bar,
            text="生成与导出",
            bg="#0f172a",
            fg="#94a3b8",
            font=("Microsoft YaHei UI", 9, "bold"),
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 5))

        primary = tk.Button(
            action_bar,
            text="补全此图层",
            command=self.handle_auto_complete,
            relief=tk.FLAT,
            bg="#2563eb",
            fg="#f8fafc",
            activebackground="#1d4ed8",
            activeforeground="#ffffff",
            font=("Microsoft YaHei UI", 10, "bold"),
            padx=10,
            pady=9,
            cursor="hand2",
        )
        primary.pack(fill=tk.X, pady=(0, 5))

        row = tk.Frame(action_bar, bg="#0f172a")
        row.pack(fill=tk.X)
        self._button("清除图层", self.clear_current_layer, "#475569", parent=row).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4)
        )
        self._button("导出 JSON", self.export_json, "#059669", parent=row).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0)
        )

    def _button(
        self,
        text: str,
        command: Any,
        color: str,
        parent: Optional[tk.Widget] = None,
    ) -> tk.Button:
        widget_parent = parent or self.sidebar
        btn = tk.Button(
            widget_parent,
            text=text,
            command=command,
            relief=tk.FLAT,
            bg=color,
            fg="#f8fafc",
            activebackground=color,
            activeforeground="#ffffff",
            font=("Microsoft YaHei UI", 9, "bold"),
            padx=10,
            pady=8,
            cursor="hand2",
        )
        if parent is None:
            btn.pack(fill=tk.X, pady=3)
        return btn

    def _entry_row(
        self,
        label: str,
        variable: tk.Variable,
        unit: str,
        parent: Optional[tk.Widget] = None,
    ) -> tk.Frame:
        parent = parent or self.sidebar
        row = tk.Frame(parent, bg="#0f172a")
        row.pack(fill=tk.X, pady=3)
        tk.Label(row, text=label, bg="#0f172a", fg="#cbd5e1", width=9, anchor="w").pack(side=tk.LEFT)
        entry = tk.Entry(
            row,
            textvariable=variable,
            bg="#020617",
            fg="#e5e7eb",
            insertbackground="#e5e7eb",
            relief=tk.FLAT,
            width=14,
        )
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(4, 6))
        entry.bind("<Return>", lambda _event: self.redraw())
        entry.bind("<FocusOut>", lambda _event: self.redraw())
        tk.Label(row, text=unit, bg="#0f172a", fg="#64748b", width=4, anchor="w").pack(side=tk.RIGHT)
        return row

    # ------------------------------------------------------------------
    # State helpers
    # ------------------------------------------------------------------
    def show_message(self, msg: str, is_error: bool = False) -> None:
        self.status_label.configure(text=msg, fg="#fca5a5" if is_error else "#86efac")
        if is_error:
            self.root.bell()

    def on_label_selected(self) -> None:
        self.update_sidebar_visibility()
        self.redraw()

    def update_sidebar_visibility(self) -> None:
        if self.view_mode.get() == "perspective":
            if not self.layer_shell.winfo_ismapped():
                self.layer_shell.pack(fill=tk.X)
            if self.get_current_label() == "1":
                if not self.physical_param_shell.winfo_ismapped():
                    self.physical_param_shell.pack(fill=tk.X)
            else:
                self.physical_param_shell.pack_forget()
        else:
            self.layer_shell.pack_forget()

    def on_opacity_change(self, _value: str) -> None:
        if hasattr(self, "opacity_label"):
            self.opacity_label.configure(text=f"{round(float(self.opacity.get()) * 100)}%")
        self.redraw()

    def ui_color(self, color: str, factor: float = 1.0, bg: str = "#020617") -> str:
        return blend_hex_color(color, clamp(float(self.opacity.get()) * factor, 0.0, 1.0), bg)

    def ui_alpha(self, factor: float = 1.0) -> float:
        return clamp(float(self.opacity.get()) * factor, 0.0, 1.0)

    def set_view_mode(self, mode: str) -> None:
        if mode == "3d" and not (self.points or self.grid_data or self.computed_points):
            self.show_message("请先在 2D 模式下打点并补全网格。", True)
            return
        if mode == "registration" and not self.image_meta.get("width"):
            self.show_message("请先载入图1底图或导入坐标 JSON。", True)
            return
        self.view_mode.set(mode)
        if mode == "registration":
            if self.tool_mode.get() not in ("add", "delete"):
                self.tool_mode.set("add")
        elif mode == "topdown":
            self.current_label.set(layer_title("1"))
        elif mode == "perspective":
            self.current_label.set(layer_title("1"))
        self.update_sidebar_visibility()
        self.redraw()

    def set_tool_mode(self, mode: str) -> None:
        current_view = self.view_mode.get()
        if mode == "yolo" and current_view not in ("perspective", "registration"):
            self.view_mode.set("perspective")
            self.current_label.set(layer_title("1"))
        self.tool_mode.set(mode)
        if mode == "yolo":
            if self.view_mode.get() == "registration":
                self.show_message("双图配准 YOLO 模式：在右侧图2拖拽画框；结果会映射到图1坐标系定位。")
            else:
                self.show_message("YOLO 模式：拖拽画框；第 5 层体积网格会优先用于高处相对定位。")
        else:
            self.show_message(f"当前工具：{mode}")
        self.redraw()

    def screen_to_image(self, x: float, y: float) -> Point:
        return {
            "x": (x - self.pan_x) / (self.draw_scale or 1.0),
            "y": (y - self.pan_y) / (self.draw_scale or 1.0),
        }

    def image_to_screen(self, point: Point) -> Tuple[float, float]:
        return (
            float(point["x"]) * self.draw_scale + self.pan_x,
            float(point["y"]) * self.draw_scale + self.pan_y,
        )

    def image_source_size(self, source: str) -> Tuple[float, float]:
        meta = self.test_image_meta if source == "test" else self.image_meta
        return float(meta.get("width") or 0.0), float(meta.get("height") or 0.0)

    def get_current_label(self) -> str:
        if self.view_mode.get() == "topdown":
            return "1"
        return layer_from_display(self.current_label.get())

    def get_current_layer_title(self) -> str:
        return layer_title(self.get_current_label())

    def active_points(self, label: Optional[str] = None) -> List[Point]:
        lbl = label or self.get_current_label()
        return [p for p in self.points if str(p.get("label", "1")) == lbl]

    # ------------------------------------------------------------------
    # Files
    # ------------------------------------------------------------------
    def load_image(self) -> None:
        if Image is None or ImageTk is None:
            messagebox.showerror("缺少依赖", "请先安装 Pillow：pip install pillow")
            return

        filename = filedialog.askopenfilename(
            title="选择底图",
            filetypes=[
                ("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff"),
                ("All files", "*.*"),
            ],
        )
        if not filename:
            return

        try:
            img = Image.open(filename).convert("RGB")
        except Exception as exc:
            messagebox.showerror("载入失败", f"图片无法打开：\n{exc}")
            return

        self.image = img
        self.image_path = Path(filename)
        self.image_meta = {
            "fileName": self.image_path.name,
            "width": img.width,
            "height": img.height,
        }
        self.registration_pairs.clear()
        self.registration = {
            "H_test_to_ref": None,
            "H_ref_to_test": None,
            "errors": [],
            "meanError": None,
            "maxError": None,
        }

        self.root.update_idletasks()
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        self.draw_scale = min((cw - 80) / img.width, (ch - 80) / img.height, 1.0)
        self.draw_scale = max(0.05, self.draw_scale)
        self.pan_x = (cw - img.width * self.draw_scale) / 2
        self.pan_y = (ch - img.height * self.draw_scale) / 2

        self.show_message("成功载入新图层。")
        self.redraw()

    def load_test_image(self) -> None:
        if Image is None or ImageTk is None:
            messagebox.showerror("缺少依赖", "请先安装 Pillow：pip install pillow")
            return

        filename = filedialog.askopenfilename(
            title="选择测试图",
            filetypes=[
                ("Images", "*.png *.jpg *.jpeg *.bmp *.gif *.tif *.tiff"),
                ("All files", "*.*"),
            ],
        )
        if not filename:
            return

        try:
            img = Image.open(filename).convert("RGB")
        except Exception as exc:
            messagebox.showerror("载入失败", f"测试图无法打开：\n{exc}")
            return

        self.test_image = img
        self.test_image_path = Path(filename)
        self.test_image_meta = {
            "fileName": self.test_image_path.name,
            "width": img.width,
            "height": img.height,
        }
        self.yolo_boxes = [box for box in self.yolo_boxes if str(box.get("sourceImage", "ref")) != "test"]
        self.registration_pairs.clear()
        self.registration = {
            "H_test_to_ref": None,
            "H_ref_to_test": None,
            "errors": [],
            "meanError": None,
            "maxError": None,
        }
        self.view_mode.set("registration")
        self.tool_mode.set("add")
        self.show_message("成功载入测试图；请在左右两图按相同顺序点箱体角点。")
        self.redraw()

    def import_json(self) -> None:
        filename = filedialog.askopenfilename(
            title="导入工程 JSON",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not filename:
            return

        try:
            data = json.loads(Path(filename).read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            try:
                data = json.loads(Path(filename).read_text(encoding="gbk"))
            except Exception as exc:
                messagebox.showerror("解析失败", f"JSON 解析失败：\n{exc}")
                return
        except Exception as exc:
            messagebox.showerror("解析失败", f"JSON 解析失败：\n{exc}")
            return

        parsed_points: List[Point] = []
        parsed_grid_data: List[Point] = []

        for shape in data.get("shapes", []):
            if shape.get("shape_type") != "point":
                continue
            pts = shape.get("points") or []
            if not pts:
                continue
            label = str(shape.get("label") or "1")
            pt = make_point(float(pts[0][0]), float(pts[0][1]), label)
            desc = str(shape.get("description") or "")
            if desc.startswith("grid_"):
                parts = desc.split("_")
                if len(parts) >= 3:
                    pt["c"] = to_int(parts[1])
                    pt["r"] = to_int(parts[2])
                    if len(parts) > 3:
                        pt["l"] = to_int(parts[3])
                    parsed_grid_data.append(pt)
                else:
                    parsed_points.append(pt)
            elif desc != "auto_filled":
                parsed_points.append(pt)

        self.points = parsed_points
        self.computed_points = []
        self.grid_data = parsed_grid_data
        self.yolo_boxes = []
        self.lines_map = self.rebuild_lines_map_from_grid(self.grid_data)

        if data.get("imageWidth") and not self.image_meta.get("width"):
            self.image_meta["width"] = data.get("imageWidth")
            self.image_meta["height"] = data.get("imageHeight")
            self.image_meta["fileName"] = data.get("imagePath") or Path(filename).name

        label5_count = sum(1 for p in self.grid_data if str(p.get("label")) == "5")
        if label5_count:
            self.show_message(f"已成功载入工程；检测到第 5 层体积点 {label5_count} 个。")
        else:
            self.show_message("已成功载入工程。")
        self.redraw()

    def import_registration_json(self) -> None:
        filename = filedialog.askopenfilename(
            title="导入配准 JSON",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not filename:
            return

        try:
            data = json.loads(Path(filename).read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            try:
                data = json.loads(Path(filename).read_text(encoding="gbk"))
            except Exception as exc:
                messagebox.showerror("解析失败", f"配准 JSON 解析失败：\n{exc}")
                return
        except Exception as exc:
            messagebox.showerror("解析失败", f"配准 JSON 解析失败：\n{exc}")
            return

        if data.get("format") != "Image_Registration_Homography":
            self.show_message("这不是双图配准 JSON。", True)
            return

        pairs: List[Dict[str, Point]] = []
        for item in data.get("pairs", []):
            ref = item.get("ref") or item.get("reference")
            test = item.get("test")
            if not ref or not test or len(ref) < 2 or len(test) < 2:
                continue
            pairs.append(
                {
                    "ref": {"x": float(ref[0]), "y": float(ref[1])},
                    "test": {"x": float(test[0]), "y": float(test[1])},
                }
            )

        H_test_to_ref = data.get("H_test_to_ref")
        H_ref_to_test = data.get("H_ref_to_test")
        if not H_test_to_ref or not H_ref_to_test:
            self.show_message("配准 JSON 缺少透视矩阵。", True)
            return

        self.registration_pairs = pairs
        self.registration = {
            "H_test_to_ref": H_test_to_ref,
            "H_ref_to_test": H_ref_to_test,
            "errors": data.get("errors", []),
            "meanError": data.get("meanError"),
            "maxError": data.get("maxError"),
        }
        ref_meta = data.get("referenceImage", {})
        test_meta = data.get("testImage", {})
        if ref_meta.get("width") and not self.image_meta.get("width"):
            self.image_meta.update(
                {
                    "fileName": ref_meta.get("fileName", self.image_meta.get("fileName", "")),
                    "width": ref_meta.get("width"),
                    "height": ref_meta.get("height"),
                }
            )
        if test_meta.get("width") and not self.test_image_meta.get("width"):
            self.test_image_meta.update(
                {
                    "fileName": test_meta.get("fileName", self.test_image_meta.get("fileName", "")),
                    "width": test_meta.get("width"),
                    "height": test_meta.get("height"),
                }
            )
        self.view_mode.set("registration")
        mean_err = self.registration.get("meanError")
        suffix = f" 平均误差 {float(mean_err):.2f}px。" if mean_err is not None else ""
        self.show_message(f"已导入配准 JSON，共 {len(pairs)} 对点。{suffix}")
        self.redraw()

    def complete_registration_pairs(self) -> List[Dict[str, Point]]:
        return [
            pair
            for pair in self.registration_pairs
            if pair.get("ref") is not None and pair.get("test") is not None
        ]

    def solve_registration(self) -> None:
        pairs = self.complete_registration_pairs()
        if len(pairs) < 4:
            self.show_message("配准至少需要 4 对图1/图2对应点。", True)
            return

        ref_pts = [pair["ref"] for pair in pairs]
        test_pts = [pair["test"] for pair in pairs]
        H_test_to_ref = get_perspective_transform_fit(test_pts, ref_pts)
        H_ref_to_test = get_perspective_transform_fit(ref_pts, test_pts)
        if not H_test_to_ref or not H_ref_to_test:
            self.show_message("配准矩阵求解失败，请检查点位是否共线或顺序是否错乱。", True)
            return

        errors: List[Dict[str, Any]] = []
        distances: List[float] = []
        for index, pair in enumerate(pairs, 1):
            projected = apply_homography(H_test_to_ref, pair["test"])
            if not projected:
                continue
            err = math.hypot(projected["x"] - pair["ref"]["x"], projected["y"] - pair["ref"]["y"])
            distances.append(err)
            errors.append(
                {
                    "index": index,
                    "error": err,
                    "projectedRef": [projected["x"], projected["y"]],
                    "ref": [pair["ref"]["x"], pair["ref"]["y"]],
                    "test": [pair["test"]["x"], pair["test"]["y"]],
                }
            )

        mean_error = sum(distances) / len(distances) if distances else None
        max_error = max(distances) if distances else None
        self.registration = {
            "H_test_to_ref": H_test_to_ref,
            "H_ref_to_test": H_ref_to_test,
            "errors": errors,
            "meanError": mean_error,
            "maxError": max_error,
        }
        if mean_error is None:
            self.show_message("配准完成。")
        else:
            self.show_message(f"配准完成：{len(pairs)} 对点，平均误差 {mean_error:.2f}px，最大误差 {max_error:.2f}px。")
        self.redraw()

    def clear_registration_points(self) -> None:
        self.registration_pairs.clear()
        self.registration = {
            "H_test_to_ref": None,
            "H_ref_to_test": None,
            "errors": [],
            "meanError": None,
            "maxError": None,
        }
        self.show_message("已清空双图配准点。")
        self.redraw()

    def registration_export_data(self) -> Dict[str, Any]:
        return {
            "format": "Image_Registration_Homography",
            "referenceImage": {
                "fileName": self.image_meta.get("fileName", ""),
                "width": self.image_meta.get("width"),
                "height": self.image_meta.get("height"),
            },
            "testImage": {
                "fileName": self.test_image_meta.get("fileName", ""),
                "width": self.test_image_meta.get("width"),
                "height": self.test_image_meta.get("height"),
            },
            "pairs": [
                {
                    "index": index,
                    "ref": [round(float(pair["ref"]["x"]), 3), round(float(pair["ref"]["y"]), 3)],
                    "test": [round(float(pair["test"]["x"]), 3), round(float(pair["test"]["y"]), 3)],
                }
                for index, pair in enumerate(self.complete_registration_pairs(), 1)
            ],
            "H_test_to_ref": self.registration.get("H_test_to_ref"),
            "H_ref_to_test": self.registration.get("H_ref_to_test"),
            "meanError": self.registration.get("meanError"),
            "maxError": self.registration.get("maxError"),
            "errors": self.registration.get("errors", []),
        }

    def export_json(self) -> None:
        mode = self.view_mode.get()
        default_name = (Path(self.image_meta.get("fileName") or "export").stem or "export")

        if mode == "registration":
            if not self.registration.get("H_test_to_ref"):
                self.show_message("请先计算配准矩阵，再导出配准 JSON。", True)
                return
            self.write_json(default_name + "_image_registration.json", self.registration_export_data())
            return

        if mode == "3d":
            p3d, _lines3d, _box_lines = self.compute_p3d_bundle()
            if not p3d:
                self.show_message("没有 3D 数据可导出。", True)
                return

            export_data: Dict[str, Any] = {
                "format": "3D_point_structure_corrected",
                "box_parameters": {
                    "bottom_width_mm": BOTTOM_W,
                    "bottom_length_mm": BOTTOM_L,
                    "top_width_mm": TOP_W,
                    "top_length_mm": TOP_L,
                    "estimated_height_mm": HEIGHT_EST,
                    "unit": "mm",
                },
                "layers": {},
            }
            for p in p3d:
                z_key = f"z_{float(p.get('z3', 0.0)):.1f}"
                export_data["layers"].setdefault(z_key, [])
                item = {
                    "x": round(float(p["x3"]), 2),
                    "y": round(float(p["y3"]), 2),
                    "z": round(float(p["z3"]), 2),
                    "label": p.get("label", "1"),
                }
                if "c" in p:
                    item.update({"col": p.get("c"), "row": p.get("r")})
                    if p.get("l") is not None:
                        item["layer"] = p.get("l")
                else:
                    item["type"] = "manual"
                export_data["layers"][z_key].append(item)

            self.write_json(default_name + "_3d_points.json", export_data)
            return


        if mode == "chart":
            data = self.bottom_contact_trajectory_points()
            if not data:
                self.show_message("No trajectory data available. Please draw or import YOLO boxes first.", True)
                return
            export_data = {
                "format": "YOLO_3D_Relative_Trajectory_Analysis",
                "unit": "mm",
                "count": len(data),
                "points": [
                    {
                        "index": index,
                        "X": round(float(item["X"]), 2),
                        "Y": round(float(item["Y"]), 2),
                        "Z_base": round(float(item.get("Z_base", 0.0)), 2),
                        "mouseHeight": round(float(item.get("mouseHeight", 0.0)), 2),
                        "Z_total": round(float(item.get("Z_total", item.get("Z", 0.0))), 2),
                        "locatorMethod": item.get("locatorMethod", ""),
                        "frontBackSource": item.get("frontBackSource", ""),
                        "center3D": export_location3d(item.get("center3D")),
                        "support3D": export_location3d(item.get("support3D")),
                        "legacy3D": export_location3d(item.get("legacy3D")),
                        "sourceImage": item.get("sourceImage", "ref"),
                        "registrationApplied": bool(item.get("registrationApplied")),
                        "registrationMeanError": item.get("registrationMeanError"),
                        "registrationMaxError": item.get("registrationMaxError"),
                        "bbox": {
                            "rawCenter": [
                                round(float(item.get("rawBBoxCenterX", item.get("bboxCenterX", item.get("cx", 0.0)))), 2),
                                round(float(item.get("rawBBoxCenterY", item.get("bboxCenterY", item.get("cy", 0.0)))), 2),
                            ],
                            "center": [
                                round(float(item.get("bboxCenterX", item.get("cx", 0.0))), 2),
                                round(float(item.get("bboxCenterY", item.get("cy", 0.0))), 2),
                            ],
                            "width": round(float(item.get("rawBBoxWidth", 0.0)), 2),
                            "height": round(float(item.get("rawBBoxHeight", 0.0)), 2),
                            "area": round(float(item.get("rawBBoxArea", 0.0)), 2),
                            "sizeDepthY": (
                                round(float(item.get("bboxScaleY")), 2)
                                if item.get("bboxScaleY") is not None
                                else None
                            ),
                        },
                    }
                    for index, item in enumerate(data, 1)
                ],
            }
            self.write_json(default_name + "_trajectory.json", export_data)
            return

        if not self.image_meta.get("width"):
            self.show_message("没有图像可导出。", True)
            return

        shapes: List[Dict[str, Any]] = []
        completed_labels = {str(p.get("label", "1")) for p in self.grid_data}
        for p in self.grid_data:
            desc = f"grid_{p.get('c')}_{p.get('r')}"
            if p.get("l") is not None:
                desc += f"_{p.get('l')}"
            shapes.append(
                {
                    "label": str(p.get("label", "1")),
                    "points": [[round(float(p["x"]), 2), round(float(p["y"]), 2)]],
                    "group_id": None,
                    "description": desc,
                    "shape_type": "point",
                    "flags": {},
                }
            )

        for p in self.points:
            label = str(p.get("label", "1"))
            if label not in completed_labels:
                shapes.append(
                    {
                        "label": label,
                        "points": [[round(float(p["x"]), 2), round(float(p["y"]), 2)]],
                        "group_id": None,
                        "description": "manual",
                        "shape_type": "point",
                        "flags": {},
                    }
                )

        export_data = {
            "version": "5.0.1",
            "flags": {},
            "shapes": shapes,
            "imagePath": self.image_meta.get("fileName") or "image.png",
            "imageData": None,
            "imageHeight": self.image_meta.get("height"),
            "imageWidth": self.image_meta.get("width"),
        }
        self.write_json(default_name + "_grid_completed.json", export_data)

    def write_json(self, suggested_name: str, data: Dict[str, Any]) -> None:
        filename = filedialog.asksaveasfilename(
            title="保存 JSON",
            initialfile=suggested_name,
            defaultextension=".json",
            filetypes=[("JSON", "*.json"), ("All files", "*.*")],
        )
        if not filename:
            return
        Path(filename).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        self.show_message(f"已导出：{Path(filename).name}")

    def reset_project(self) -> None:
        if not messagebox.askyesno("确认清空项目", "此操作将删除所有打点数据、网格和底图，是否继续？"):
            return
        self.image = None
        self.tk_image = None
        self.image_path = None
        self.image_meta = {"fileName": "", "width": 0, "height": 0}
        self.test_image = None
        self.test_tk_image = None
        self.registration_ref_tk_image = None
        self.test_image_path = None
        self.test_image_meta = {"fileName": "", "width": 0, "height": 0}
        self.points.clear()
        self.computed_points.clear()
        self.grid_data.clear()
        self.lines_map.clear()
        self.yolo_boxes.clear()
        self.registration_pairs.clear()
        self.registration = {
            "H_test_to_ref": None,
            "H_ref_to_test": None,
            "errors": [],
            "meanError": None,
            "maxError": None,
        }
        self.draw_scale = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.show_message("项目数据已清空。")
        self.redraw()

    # ------------------------------------------------------------------
    # Geometry pipelines
    # ------------------------------------------------------------------
    def rebuild_lines_map_from_grid(self, grid: Sequence[Point]) -> Dict[str, List[Tuple[Point, Point]]]:
        result: Dict[str, List[Tuple[Point, Point]]] = {}
        for label in ("1", "2", "3", "4", "5"):
            grid_pts = [p for p in grid if str(p.get("label", "1")) == label]
            if not grid_pts:
                continue
            result[label] = self.build_lines_for_label(grid_pts, label)
        return result

    def build_lines_for_label(self, grid: Sequence[Point], label: str) -> List[Tuple[Point, Point]]:
        lines: List[Tuple[Point, Point]] = []
        grid_index_2d = {
            (to_int(p.get("c")), to_int(p.get("r"))): p
            for p in grid
            if p.get("c") is not None and p.get("r") is not None
        }
        if label == "5":
            max_c = max((to_int(p.get("c")) for p in grid), default=0)
            max_r = max((to_int(p.get("r")) for p in grid), default=0)
            max_l = max((to_int(p.get("l")) for p in grid), default=0)
            grid_index_3d = {
                (to_int(p.get("c")), to_int(p.get("r")), to_int(p.get("l"))): p
                for p in grid
                if p.get("c") is not None and p.get("r") is not None
            }

            for c in range(max_c + 1):
                for r in range(max_r + 1):
                    for l in range(max_l + 1):
                        p1 = grid_index_3d.get((c, r, l))
                        if not p1:
                            continue
                        for p2 in (
                            grid_index_3d.get((c + 1, r, l)),
                            grid_index_3d.get((c, r + 1, l)),
                            grid_index_3d.get((c, r, l + 1)),
                        ):
                            if p2:
                                lines.append((p1, p2))
            return lines

        max_c = max((to_int(p.get("c")) for p in grid), default=0)
        max_r = max((to_int(p.get("r")) for p in grid), default=0)

        for r in range(max_r + 1):
            for c in range(max_c):
                p1 = grid_index_2d.get((c, r))
                p2 = grid_index_2d.get((c + 1, r))
                if p1 and p2:
                    lines.append((p1, p2))

        for c in range(max_c + 1):
            for r in range(max_r):
                p1 = grid_index_2d.get((c, r))
                p2 = grid_index_2d.get((c, r + 1))
                if p1 and p2:
                    lines.append((p1, p2))
        return lines

    def generate_lines(self, grid: Sequence[Point], c_count: int, r_count: int, label_key: str) -> None:
        lines: List[Tuple[Point, Point]] = []
        grid_index = {
            (to_int(p.get("c")), to_int(p.get("r"))): p
            for p in grid
            if p.get("c") is not None and p.get("r") is not None
        }

        for r in range(r_count):
            for c in range(c_count - 1):
                p1 = grid_index.get((c, r))
                p2 = grid_index.get((c + 1, r))
                if p1 and p2:
                    lines.append((p1, p2))

        for c in range(c_count):
            for r in range(r_count - 1):
                p1 = grid_index.get((c, r))
                p2 = grid_index.get((c, r + 1))
                if p1 and p2:
                    lines.append((p1, p2))

        self.lines_map[label_key] = lines

    def compute_p3d_bundle(
        self,
        include_lines: bool = True,
    ) -> Tuple[List[Point], List[Tuple[Point, Point]], List[Tuple[Point, Point]]]:
        completed_labels = {str(p.get("label", "1")) for p in self.grid_data}
        all_pts = [p for p in self.points if str(p.get("label", "1")) not in completed_labels] + list(self.grid_data)

        maxes: Dict[str, Dict[str, int]] = {}
        for label in ("1", "2", "3", "4", "5"):
            l_pts = [p for p in all_pts if str(p.get("label", "1")) == label and "c" in p]
            if l_pts:
                maxes[label] = {
                    "maxC": max(1, max(to_int(p.get("c")) for p in l_pts)),
                    "maxR": max(1, max(to_int(p.get("r")) for p in l_pts)),
                    "maxL": max(1, max(to_int(p.get("l")) for p in l_pts)),
                }

        base_map_234: Dict[str, Point] = {}
        base_map_5: Dict[str, Point] = {}
        for pt in all_pts:
            label = str(pt.get("label", "1"))
            m = maxes.get(label)
            if not m:
                continue
            if label in ("2", "3", "4") and to_int(pt.get("r")) == m["maxR"]:
                base_map_234[f"{label}_{to_int(pt.get('c'))}"] = {"x": float(pt["x"]), "y": float(pt["y"])}
            elif label == "5" and to_int(pt.get("l")) == m["maxL"]:
                base_map_5[f"5_{to_int(pt.get('c'))}_{to_int(pt.get('r'))}"] = {
                    "x": float(pt["x"]),
                    "y": float(pt["y"]),
                }

        pts3d: List[Point] = []
        for p in all_pts:
            if "c" not in p or "r" not in p:
                continue
            label = str(p.get("label", "1"))
            m = maxes.get(label)
            if not m:
                continue

            c = to_int(p.get("c"))
            r = to_int(p.get("r"))
            l = to_int(p.get("l"))
            x3 = y3 = z3 = 0.0
            bx = float(p["x"])
            by = float(p["y"])

            if label == "5":
                y_ratio = 1.0 - r / m["maxR"]
                current_width = BOTTOM_W + (TOP_W - BOTTOM_W) * y_ratio
                x3 = (c / m["maxC"]) * current_width - current_width / 2.0
                y3 = y_ratio * BOTTOM_L
                z3 = (1.0 - l / m["maxL"]) * HEIGHT_EST
                base = base_map_5.get(f"5_{c}_{r}")
                if base:
                    bx = float(base["x"])
                    by = float(base["y"])
            elif label == "1":
                y_ratio = 1.0 - r / m["maxR"]
                current_width = BOTTOM_W + (TOP_W - BOTTOM_W) * y_ratio
                x3 = (c / m["maxC"]) * current_width - current_width / 2.0
                y3 = y_ratio * BOTTOM_L
                z3 = 0.0
            elif label in ("2", "3", "4"):
                if label == "2":
                    current_width = BOTTOM_W
                    y3 = 0.0
                elif label == "3":
                    current_width = (BOTTOM_W + TOP_W) / 2.0
                    y3 = BOTTOM_L / 2.0
                else:
                    current_width = TOP_W
                    y3 = BOTTOM_L
                x3 = (c / m["maxC"]) * current_width - current_width / 2.0
                z3 = (1.0 - r / m["maxR"]) * HEIGHT_EST
                base = base_map_234.get(f"{label}_{c}")
                if base:
                    bx = float(base["x"])
                    by = float(base["y"])
            else:
                continue

            new_p = dict(p)
            new_p.update({"x3": x3, "y3": y3, "z3": z3, "bx": bx, "by": by})
            pts3d.append(new_p)

        box_lines = self.build_box_lines()
        if not include_lines:
            return pts3d, [], box_lines

        lines3d: List[Tuple[Point, Point]] = []
        for label in ("1", "2", "3", "4", "5"):
            grid_pts = [p for p in pts3d if str(p.get("label", "1")) == label]
            if not grid_pts:
                continue
            m = maxes.get(label)
            if not m:
                continue

            if label == "5":
                grid_index_3d = {
                    (to_int(p.get("c")), to_int(p.get("r")), to_int(p.get("l"))): p
                    for p in grid_pts
                    if p.get("c") is not None and p.get("r") is not None
                }

                for c in range(m["maxC"] + 1):
                    for r in range(m["maxR"] + 1):
                        for l in range(m["maxL"] + 1):
                            p1 = grid_index_3d.get((c, r, l))
                            if not p1:
                                continue
                            for p2 in (
                                grid_index_3d.get((c + 1, r, l)),
                                grid_index_3d.get((c, r + 1, l)),
                                grid_index_3d.get((c, r, l + 1)),
                            ):
                                if p2:
                                    lines3d.append((p1, p2))
            else:
                grid_index_2d = {
                    (to_int(p.get("c")), to_int(p.get("r"))): p
                    for p in grid_pts
                    if p.get("c") is not None and p.get("r") is not None
                }

                for r in range(m["maxR"] + 1):
                    for c in range(m["maxC"]):
                        p1 = grid_index_2d.get((c, r))
                        p2 = grid_index_2d.get((c + 1, r))
                        if p1 and p2:
                            lines3d.append((p1, p2))

                for c in range(m["maxC"] + 1):
                    for r in range(m["maxR"]):
                        p1 = grid_index_2d.get((c, r))
                        p2 = grid_index_2d.get((c, r + 1))
                        if p1 and p2:
                            lines3d.append((p1, p2))

        return pts3d, lines3d, box_lines

    def build_box_lines(self) -> List[Tuple[Point, Point]]:
        box_lines: List[Tuple[Point, Point]] = []
        bw = BOTTOM_W / 2.0
        bl = BOTTOM_L
        tw = TOP_W / 2.0
        dy = (TOP_L - BOTTOM_L) / 2.0
        bottom = [
            {"x3": -bw, "y3": 0.0, "z3": 0.0},
            {"x3": bw, "y3": 0.0, "z3": 0.0},
            {"x3": bw, "y3": bl, "z3": 0.0},
            {"x3": -bw, "y3": bl, "z3": 0.0},
        ]
        top = [
            {"x3": -tw, "y3": -dy, "z3": HEIGHT_EST},
            {"x3": tw, "y3": -dy, "z3": HEIGHT_EST},
            {"x3": tw, "y3": bl + dy, "z3": HEIGHT_EST},
            {"x3": -tw, "y3": bl + dy, "z3": HEIGHT_EST},
        ]
        for i in range(4):
            box_lines.append((bottom[i], bottom[(i + 1) % 4]))
            box_lines.append((top[i], top[(i + 1) % 4]))
            box_lines.append((bottom[i], top[i]))
        return box_lines

    def knn_locate_3d(
        self,
        candidates: Sequence[Point],
        x: float,
        y: float,
        k: int,
        method: str,
    ) -> Optional[Point]:
        ranked: List[Tuple[float, Point]] = []
        for p in candidates:
            if p.get("x") is None or p.get("y") is None or p.get("x3") is None:
                continue
            dx = float(p["x"]) - x
            dy = float(p["y"]) - y
            ranked.append((dx * dx + dy * dy, p))
        if not ranked:
            return None

        ranked.sort(key=lambda item: item[0])
        nearest_d2, nearest = ranked[0]
        neighbours = ranked[: max(1, min(k, len(ranked)))]
        if nearest_d2 < 1e-9:
            weights = [(1.0, nearest)]
        else:
            weights = [(1.0 / max(d2, 1e-6), p) for d2, p in neighbours]

        total_w = sum(w for w, _p in weights) or 1.0
        X = sum(w * float(p["x3"]) for w, p in weights) / total_w
        Y = sum(w * float(p["y3"]) for w, p in weights) / total_w
        Z = sum(w * float(p["z3"]) for w, p in weights) / total_w
        weighted_x = sum(w * float(p["x"]) for w, p in weights) / total_w
        weighted_y = sum(w * float(p["y"]) for w, p in weights) / total_w

        return {
            "X": X,
            "Y": Y,
            "Z": Z,
            "queryPixelX": x,
            "queryPixelY": y,
            "matchedPixelX": float(nearest["x"]),
            "matchedPixelY": float(nearest["y"]),
            "weightedPixelX": weighted_x,
            "weightedPixelY": weighted_y,
            "pixelError": math.sqrt(nearest_d2),
            "method": method,
            "knn": len(weights),
            "label": str(nearest.get("label", "")),
            "col": nearest.get("c"),
            "row": nearest.get("r"),
            "layer": nearest.get("l"),
        }

    def volume_locator_candidates(self) -> List[Point]:
        p3d, _lines3d, _box_lines = self.compute_p3d_bundle(include_lines=False)
        return [p for p in p3d if str(p.get("label")) == "5"]

    def ground_locator_candidates(self) -> List[Point]:
        p3d, _lines3d, _box_lines = self.compute_p3d_bundle(include_lines=False)
        return [p for p in p3d if str(p.get("label")) == "1"]

    def build_3d_preview_lines(self, p3d: Sequence[Point]) -> List[Tuple[Point, Point]]:
        lines: List[Tuple[Point, Point]] = []
        for label in ("1", "2", "3", "4"):
            grid_pts = [p for p in p3d if str(p.get("label")) == label]
            if not grid_pts:
                continue
            max_c = max((to_int(p.get("c")) for p in grid_pts), default=0)
            max_r = max((to_int(p.get("r")) for p in grid_pts), default=0)
            grid_index = {
                (to_int(p.get("c")), to_int(p.get("r"))): p
                for p in grid_pts
                if p.get("c") is not None and p.get("r") is not None
            }
            for r in range(max_r + 1):
                for c in range(max_c):
                    p1 = grid_index.get((c, r))
                    p2 = grid_index.get((c + 1, r))
                    if p1 and p2:
                        lines.append((p1, p2))
            for c in range(max_c + 1):
                for r in range(max_r):
                    p1 = grid_index.get((c, r))
                    p2 = grid_index.get((c, r + 1))
                    if p1 and p2:
                        lines.append((p1, p2))

        label5_pts = [p for p in p3d if str(p.get("label")) == "5"]
        if not label5_pts:
            return lines

        max_c = max((to_int(p.get("c")) for p in label5_pts), default=0)
        max_r = max((to_int(p.get("r")) for p in label5_pts), default=0)
        max_l = max((to_int(p.get("l")) for p in label5_pts), default=0)
        estimated = (
            max_c * (max_r + 1) * (max_l + 1)
            + (max_c + 1) * max_r * (max_l + 1)
            + (max_c + 1) * (max_r + 1) * max_l
        )
        step = max(1, math.ceil(estimated / MAX_LABEL5_3D_LINES))
        counter = 0
        grid_index_3d = {
            (to_int(p.get("c")), to_int(p.get("r")), to_int(p.get("l"))): p
            for p in label5_pts
            if p.get("c") is not None and p.get("r") is not None
        }
        for c in range(max_c + 1):
            for r in range(max_r + 1):
                for l in range(max_l + 1):
                    p1 = grid_index_3d.get((c, r, l))
                    if not p1:
                        continue
                    for p2 in (
                        grid_index_3d.get((c + 1, r, l)),
                        grid_index_3d.get((c, r + 1, l)),
                        grid_index_3d.get((c, r, l + 1)),
                    ):
                        if not p2:
                            continue
                        if counter % step == 0:
                            lines.append((p1, p2))
                        counter += 1
        return lines

    def compute_camera_matrix(self) -> Optional[Matrix]:
        p3d, _lines3d, _box_lines = self.compute_p3d_bundle(include_lines=False)
        valid = [
            p
            for p in p3d
            if p.get("x") is not None and p.get("x3") is not None and str(p.get("label")) in ("1", "2", "3", "4")
        ]
        if len(valid) >= 12:
            return solve_camera_matrix(valid)
        return None

    def compute_ground_homography(self) -> Optional[Dict[str, Any]]:
        l1_pts = [p for p in self.grid_data if str(p.get("label")) == "1"] + [
            p for p in self.computed_points if str(p.get("label")) == "1"
        ]
        if not l1_pts:
            return None

        max_c = max(to_int(p.get("c")) for p in l1_pts if "c" in p)
        max_r = max(to_int(p.get("r")) for p in l1_pts if "r" in p)

        def get_pt(c: int, r: int) -> Optional[Point]:
            return next((p for p in l1_pts if to_int(p.get("c")) == c and to_int(p.get("r")) == r), None)

        pTL = get_pt(0, 0)
        pTR = get_pt(max_c, 0)
        pBL = get_pt(0, max_r)
        pBR = get_pt(max_c, max_r)
        if not all((pTL, pTR, pBL, pBR)):
            return None

        phys_corners = [
            {"x": -90.0, "y": 360.0},
            {"x": 90.0, "y": 360.0},
            {"x": -90.0, "y": 0.0},
            {"x": 90.0, "y": 0.0},
        ]
        pix_corners = [pTL, pTR, pBL, pBR]
        H_XY_to_Pix = get_perspective_transform(phys_corners, pix_corners)
        H_Pix_to_XY = get_perspective_transform(pix_corners, phys_corners)
        if not H_XY_to_Pix or not H_Pix_to_XY:
            return None

        return {
            "H_XY_to_Pix": H_XY_to_Pix,
            "H_Pix_to_XY": H_Pix_to_XY,
            "physCorners": phys_corners,
        }

    def compute_solved_yolo_boxes(self) -> List[Point]:
        ground = self.compute_ground_homography()
        P = self.compute_camera_matrix()
        volume_candidates = self.volume_locator_candidates()
        ground_candidates = self.ground_locator_candidates()
        if not ground and not volume_candidates and not ground_candidates:
            return []

        solved: List[Point] = []
        for box in self.yolo_boxes:
            raw_x_min = min(float(box["startX"]), float(box["endX"]))
            raw_x_max = max(float(box["startX"]), float(box["endX"]))
            raw_y_min = min(float(box["startY"]), float(box["endY"]))
            raw_y_max = max(float(box["startY"]), float(box["endY"]))
            raw_u = (float(box["startX"]) + float(box["endX"])) / 2.0
            raw_v_bottom = raw_y_max
            raw_v_center = (float(box["startY"]) + float(box["endY"])) / 2.0
            source_image = str(box.get("sourceImage", "ref"))
            mapped_center: Optional[Point] = None
            mapped_bottom: Optional[Point] = None
            mapped_box_corners: Optional[List[Point]] = None
            registration_applied = False
            if source_image == "test":
                H_test_to_ref = self.registration.get("H_test_to_ref")
                if not H_test_to_ref:
                    continue
                raw_box_corners = [
                    {"x": raw_x_min, "y": raw_y_min},
                    {"x": raw_x_max, "y": raw_y_min},
                    {"x": raw_x_max, "y": raw_y_max},
                    {"x": raw_x_min, "y": raw_y_max},
                ]
                mapped_box_corners = [apply_homography(H_test_to_ref, point) for point in raw_box_corners]
                mapped_center = apply_homography(H_test_to_ref, {"x": raw_u, "y": raw_v_center})
                mapped_bottom = apply_homography(H_test_to_ref, {"x": raw_u, "y": raw_v_bottom})
                if not mapped_center or any(point is None for point in mapped_box_corners):
                    continue
                mapped_box_corners = [dict(point) for point in mapped_box_corners if point is not None]
                registration_applied = True
                u = float(mapped_center["x"])
                v_center = float(mapped_center["y"])
                bottom_point = mapped_bottom or mapped_center
                u_bottom = float(bottom_point["x"])
                v_bottom = float(bottom_point["y"])
            else:
                u = raw_u
                v_center = raw_v_center
                u_bottom = raw_u
                v_bottom = raw_v_bottom
            raw_bbox_width = max(0.0, raw_x_max - raw_x_min)
            raw_bbox_height = max(0.0, raw_y_max - raw_y_min)
            raw_bbox_area = raw_bbox_width * raw_bbox_height
            bbox_width_for_scale = raw_bbox_width
            bbox_height_for_scale = raw_bbox_height
            mapped_bbox_width = None
            mapped_bbox_height = None
            mapped_bbox_area = None
            if mapped_box_corners:
                top_width = math.hypot(
                    float(mapped_box_corners[1]["x"]) - float(mapped_box_corners[0]["x"]),
                    float(mapped_box_corners[1]["y"]) - float(mapped_box_corners[0]["y"]),
                )
                bottom_width = math.hypot(
                    float(mapped_box_corners[2]["x"]) - float(mapped_box_corners[3]["x"]),
                    float(mapped_box_corners[2]["y"]) - float(mapped_box_corners[3]["y"]),
                )
                left_height = math.hypot(
                    float(mapped_box_corners[3]["x"]) - float(mapped_box_corners[0]["x"]),
                    float(mapped_box_corners[3]["y"]) - float(mapped_box_corners[0]["y"]),
                )
                right_height = math.hypot(
                    float(mapped_box_corners[2]["x"]) - float(mapped_box_corners[1]["x"]),
                    float(mapped_box_corners[2]["y"]) - float(mapped_box_corners[1]["y"]),
                )
                mapped_bbox_width = max(0.0, (top_width + bottom_width) / 2.0)
                mapped_bbox_height = max(0.0, (left_height + right_height) / 2.0)
                mapped_bbox_area = mapped_bbox_width * mapped_bbox_height
                bbox_width_for_scale = mapped_bbox_width
                bbox_height_for_scale = mapped_bbox_height
            bbox_scale_y = estimate_y_from_bbox_scale(bbox_width_for_scale, bbox_height_for_scale)
            center3d = self.knn_locate_3d(volume_candidates, u, v_center, VOLUME_KNN, "label5_volume_knn")
            support3d = self.knn_locate_3d(
                ground_candidates,
                u_bottom,
                v_bottom,
                min(4, VOLUME_KNN),
                "label1_bottom_knn",
            )

            Z_base = 0.0

            raw_phys = apply_homography(ground["H_Pix_to_XY"], {"x": u_bottom, "y": v_bottom}) if ground else None
            X_base = float(raw_phys["x"]) if raw_phys else float((support3d or center3d or {}).get("X", 0.0))
            Y_base = float(raw_phys["y"]) if raw_phys else float((support3d or center3d or {}).get("Y", 0.0))
            front_back_source = "bbox_bottom_ground" if raw_phys else ("bbox_bottom_label1_knn" if support3d else "volume_center")

            X_final = clamp(X_base, -BOTTOM_W / 2.0, BOTTOM_W / 2.0)
            Y_final = clamp(Y_base, 0.0, BOTTOM_L)
            Y_for_height = clamp(Y_base, 0.0, BOTTOM_L)
            mouse_height = solve_height_with_xyzv(P, X_final, Y_for_height, Z_base, v_center)
            mouse_height = clamp(mouse_height, 0.0, max(0.0, HEIGHT_EST - Z_base))
            Z_total = clamp(Z_base + mouse_height, 0.0, HEIGHT_EST)
            legacy3d = {
                "X": X_final,
                "Y": Y_final,
                "Z": Z_total,
                "Z_base": Z_base,
                "mouseHeight": mouse_height,
                "method": "ground_dlt" if ground else "fallback",
            }

            if center3d:
                X_final = clamp(float(center3d["X"]), -BOTTOM_W / 2.0, BOTTOM_W / 2.0)
                raw_volume_y = float(center3d["Y"])
                if False:
                    # 底边有约束时，融合中心值（主）和底边值（辅）
                    # 而不是强制用底边值覆盖中心值
                    delta_y = abs(raw_volume_y - Y_base)
                    if delta_y <= Y_BOTTOM_FUSION_MAX_DELTA:
                        # 变化在合理范围内，进行加权融合
                        # 底端（Y < 180）使用较高底边权重，远端使用较低权重以避免靠后
                        fusion_weight = Y_BOTTOM_FUSION_WEIGHT if raw_volume_y <= 180 else Y_BOTTOM_FUSION_FAR_WEIGHT
                        Y_final = clamp(
                            raw_volume_y * (1 - fusion_weight) + Y_base * fusion_weight,
                            0.0,
                            BOTTOM_L
                        )
                    else:
                        # 变化过大（可能是异常跳变），只用中心值，拒绝底边值
                        Y_final = clamp(raw_volume_y, 0.0, BOTTOM_L)
                else:
                    # KNN成功但无底部接触：直接使用KNN的Y值
                    Y_final = clamp(raw_volume_y, 0.0, BOTTOM_L)
                y_values: List[Tuple[float, float]] = []
                y_fusion_parts: Dict[str, Any] = {"center": raw_volume_y}
                if bbox_scale_y is not None:
                    y_values.append((bbox_scale_y, Y_SIZE_FUSION_WEIGHT))
                    y_fusion_parts["bboxSizePrimary"] = bbox_scale_y
                    center_delta = abs(raw_volume_y - bbox_scale_y)
                    center_weight = Y_CENTER_FUSION_WEIGHT
                    if center_delta > Y_BOTTOM_FUSION_MAX_DELTA:
                        center_weight *= 0.35
                    y_values.append((raw_volume_y, center_weight))
                    y_fusion_parts["centerWeight"] = center_weight
                else:
                    y_values.append((raw_volume_y, 1.0))
                if front_back_source != "volume_center":
                    reference_y = bbox_scale_y if bbox_scale_y is not None else raw_volume_y
                    delta_y = abs(reference_y - Y_base)
                    bottom_limit = 60.0 if bbox_scale_y is not None else Y_BOTTOM_FUSION_MAX_DELTA
                    if delta_y <= bottom_limit:
                        bottom_weight = Y_BOTTOM_FUSION_WEIGHT
                        if reference_y > BOTTOM_L / 2.0:
                            bottom_weight = Y_BOTTOM_FUSION_FAR_WEIGHT
                        if bbox_scale_y is not None:
                            bottom_weight *= max(0.15, 1.0 - delta_y / max(bottom_limit, 1.0))
                        bottom_weight = clamp(bottom_weight, 0.0, Y_BOTTOM_FUSION_WEIGHT)
                        if bottom_weight > 0.0:
                            y_values.append((Y_base, bottom_weight))
                            y_fusion_parts["bottom"] = Y_base
                            y_fusion_parts["bottomWeight"] = bottom_weight
                    else:
                        y_fusion_parts["bottomRejected"] = Y_base
                total_y_weight = sum(weight for _value, weight in y_values) or 1.0
                Y_final = clamp(
                    sum(value * weight for value, weight in y_values) / total_y_weight,
                    0.0,
                    BOTTOM_L,
                )
                Z_total = clamp(float(center3d["Z"]), 0.0, HEIGHT_EST)
                Z_base = clamp(Z_base, 0.0, HEIGHT_EST)
                mouse_height = max(0.0, Z_total - Z_base)
                X_base = X_final
                Y_base = Y_final
                center3d = dict(center3d)
                center3d.update(
                    {
                        "Y": Y_final,
                        "rawVolumeY": raw_volume_y,
                        "sizeDepthY": bbox_scale_y,
                        "yFusion": y_fusion_parts,
                        "frontBackSource": front_back_source,
                        "method": "bbox_size_depth_primary",
                    }
                )
            new_box = dict(box)
            new_box.update(
                {
                    "cx": u,
                    "cy": v_center,
                    "bboxCenterX": u,
                    "bboxCenterY": v_center,
                    "rawBBoxCenterX": raw_u,
                    "rawBBoxCenterY": raw_v_center,
                    "rawBBoxBottomY": raw_v_bottom,
                    "rawBBoxWidth": raw_bbox_width,
                    "rawBBoxHeight": raw_bbox_height,
                    "rawBBoxArea": raw_bbox_area,
                    "mappedBBoxWidth": mapped_bbox_width,
                    "mappedBBoxHeight": mapped_bbox_height,
                    "mappedBBoxArea": mapped_bbox_area,
                    "bboxScaleY": bbox_scale_y,
                    "bboxBottomX": u_bottom,
                    "bboxBottomY": v_bottom,
                    "sourceImage": source_image,
                    "registrationApplied": registration_applied,
                    "mappedCenterX": u,
                    "mappedCenterY": v_center,
                    "mappedBottomX": u_bottom,
                    "mappedBottomY": v_bottom,
                    "X": X_final,
                    "Y": Y_final,
                    "Z": Z_total,
                    "Z_base": Z_base,
                    "mouseHeight": mouse_height,
                    "Z_total": Z_total,
                    "center3D": center3d,
                    "support3D": support3d,
                    "legacy3D": legacy3d,
                    "frontBackSource": front_back_source,
                    "locatorMethod": str(center3d.get("method")) if center3d else "ground_dlt",
                    "centerMatchError": float(center3d.get("pixelError", 0.0)) if center3d else None,
                    "registrationMeanError": self.registration.get("meanError") if registration_applied else None,
                    "registrationMaxError": self.registration.get("maxError") if registration_applied else None,
                    "isShifted": bool(center3d)
                    and math.hypot(
                        float(center3d.get("matchedPixelX", u)) - u,
                        float(center3d.get("matchedPixelY", v_center)) - v_center,
                    )
                    > 2,
                }
            )
            solved.append(new_box)

        return solved

    def is_bottom_layer_contact(self, item: Point) -> bool:
        if abs(float(item.get("Z_base", 0.0))) > 1e-6:
            return False

        support3d = item.get("support3D")
        if not support3d:
            return False

        if str(support3d.get("label", "1")) != "1":
            return False

        return float(support3d.get("pixelError", float("inf"))) <= BOTTOM_CONTACT_MAX_PIXEL_ERROR

    def is_trajectory_analysis_point(self, item: Point) -> bool:
        return True

    def bottom_contact_trajectory_points(self) -> List[Point]:
        return self.compute_solved_yolo_boxes()

    # ------------------------------------------------------------------
    # Auto completion
    # ------------------------------------------------------------------
    def handle_auto_complete(self) -> None:
        target_label = self.get_current_label()
        active_points = self.active_points(target_label)

        if target_label == "5":
            self.autocomplete_volume()
            self.redraw()
            return

        if len(active_points) < 4:
            self.show_message(f"当前图层 [{layer_title(target_label)}] 绿点不足，至少需要 4 个角点。", True)
            return

        hull = convex_hull(active_points)
        if len(hull) < 4:
            min_x = min(float(p["x"]) for p in active_points)
            max_x = max(float(p["x"]) for p in active_points)
            min_y = min(float(p["y"]) for p in active_points)
            max_y = max(float(p["y"]) for p in active_points)
            pTL = {"x": min_x, "y": min_y}
            pTR = {"x": max_x, "y": min_y}
            pBL = {"x": min_x, "y": max_y}
            pBR = {"x": max_x, "y": max_y}
        else:
            best_quad = max(combinations(hull, 4), key=quad_area)
            pTL, pTR, pBL, pBR = order_quad_by_screen_position(best_quad)

        area = quad_area([pTL, pTR, pBR, pBL])
        if area < 1:
            self.show_message("点位几乎共线，无法计算透视。", True)
            return

        src_corners = [
            {"x": 0.0, "y": 0.0},
            {"x": 1000.0, "y": 0.0},
            {"x": 0.0, "y": 1000.0},
            {"x": 1000.0, "y": 1000.0},
        ]
        dst_corners = [pTL, pTR, pBL, pBR]
        H_inv = get_perspective_transform(dst_corners, src_corners)
        H_fwd = get_perspective_transform(src_corners, dst_corners)
        if not H_inv or not H_fwd:
            self.show_message("透视矩阵构建失败，请检查点位。", True)
            return

        straight_pts: List[Point] = []
        for p in active_points:
            sp = apply_homography(H_inv, p)
            if sp is None:
                continue
            np = dict(p)
            np.update({"sx": sp["x"], "sy": sp["y"]})
            straight_pts.append(np)

        n_cols = max(2, to_int(self.cols.get(), 18))
        n_rows = max(2, to_int(self.rows.get(), 36))

        if self.view_mode.get() != "perspective" or target_label != "1":
            n_cols = robust_grid_count(p["sx"] for p in straight_pts)
            n_rows = robust_grid_count(p["sy"] for p in straight_pts)

        if n_cols > 150 or n_rows > 300:
            self.show_message("推断行列数异常庞大，请检查打点密度。", True)
            return

        full_grid: List[Point] = []
        new_pts: List[Point] = []

        if self.view_mode.get() == "topdown" or (self.view_mode.get() == "perspective" and target_label == "1"):
            col_sxs: List[List[float]] = [[] for _ in range(n_cols)]
            row_sys: List[List[float]] = [[] for _ in range(n_rows)]

            for p in straight_pts:
                c = round((float(p["sx"]) / 1000.0) * (n_cols - 1))
                r = round((float(p["sy"]) / 1000.0) * (n_rows - 1))
                c = int(clamp(c, 0, n_cols - 1))
                r = int(clamp(r, 0, n_rows - 1))
                col_sxs[c].append(float(p["sx"]))
                row_sys[r].append(float(p["sy"]))

            actual_x = [None for _ in range(n_cols)]
            actual_y = [None for _ in range(n_rows)]
            actual_x[0] = 0.0
            actual_x[n_cols - 1] = 1000.0
            actual_y[0] = 0.0
            actual_y[n_rows - 1] = 1000.0

            for i in range(1, n_cols - 1):
                if col_sxs[i]:
                    actual_x[i] = sum(col_sxs[i]) / len(col_sxs[i])
            for i in range(1, n_rows - 1):
                if row_sys[i]:
                    actual_y[i] = sum(row_sys[i]) / len(row_sys[i])

            self.interpolate_missing(actual_x)
            self.interpolate_missing(actual_y)

            green_map = self.make_green_map(straight_pts, H_inv, n_cols, n_rows)
            for r in range(n_rows):
                for c in range(n_cols):
                    ideal = apply_homography(H_fwd, {"x": actual_x[c] or 0.0, "y": actual_y[r] or 0.0})
                    if not ideal:
                        continue
                    pt = make_point(ideal["x"], ideal["y"], target_label, c=c, r=r)
                    full_grid.append(pt)
                    if f"{c},{r}" not in green_map:
                        new_pts.append(pt)
        else:
            green_map = self.make_green_map(straight_pts, H_inv, n_cols, n_rows)
            for r in range(n_rows):
                for c in range(n_cols):
                    sx = c * (1000.0 / ((n_cols - 1) or 1))
                    sy = r * (1000.0 / ((n_rows - 1) or 1))
                    ideal = apply_homography(H_fwd, {"x": sx, "y": sy})
                    if not ideal:
                        continue
                    pt = make_point(ideal["x"], ideal["y"], target_label, c=c, r=r)
                    full_grid.append(pt)
                    if f"{c},{r}" not in green_map:
                        new_pts.append(pt)

        self.computed_points = [p for p in self.computed_points if str(p.get("label")) != target_label] + new_pts
        self.grid_data = [p for p in self.grid_data if str(p.get("label")) != target_label] + full_grid
        self.generate_lines(full_grid, n_cols, n_rows, target_label)
        self.show_message(f"[{layer_title(target_label)}] 补全成功，共 {len(full_grid)} 个网格点。")
        self.redraw()

    def autocomplete_volume(self) -> None:
        l1_pts = [p for p in self.grid_data if str(p.get("label")) == "1"] + [
            p for p in self.computed_points if str(p.get("label")) == "1"
        ]
        if not l1_pts:
            self.show_message("请先生成底部（标签 1）网格。", True)
            return

        max_c1 = max(to_int(p.get("c")) for p in l1_pts)
        max_r1 = max(to_int(p.get("r")) for p in l1_pts)
        max_c1_safe = max(1, max_c1)
        max_r1_safe = max(1, max_r1)
        source_points = self.grid_data + self.computed_points
        point_index_2d = {
            (str(p.get("label")), to_int(p.get("c")), to_int(p.get("r"))): p
            for p in source_points
            if p.get("c") is not None and p.get("r") is not None
        }

        def get_pt(label: str, c: int, r: int) -> Optional[Point]:
            return point_index_2d.get((label, c, r))

        vertical_pts = [
            p
            for p in self.grid_data + self.computed_points
            if str(p.get("label")) in ("2", "3", "4") and "r" in p
        ]
        if not vertical_pts:
            self.show_message("请先生成至少一个垂直面（标签 2/3/4）的网格。", True)
            return

        final_max_l = max(1, max(to_int(p.get("r")) for p in vertical_pts))

        p3d, _lines3d, _box_lines = self.compute_p3d_bundle(include_lines=False)
        valid = [
            p
            for p in p3d
            if p.get("x") is not None and p.get("x3") is not None and str(p.get("label")) in ("1", "2", "3", "4")
        ]
        if len(valid) < 12:
            self.show_message("空间数据不足，无法建立 3D 摄像机参考系。", True)
            return

        P = solve_camera_matrix(valid)
        if P is None:
            self.show_message("摄像机投影矩阵求解失败，请检查各平面贴合度。", True)
            return

        full_grid: List[Point] = []
        new_pts: List[Point] = []

        for c in range(max_c1 + 1):
            for r in range(max_r1 + 1):
                floor_pt = get_pt("1", c, r)
                if not floor_pt:
                    continue

                x3 = (c / max_c1_safe) * BOTTOM_W - BOTTOM_W / 2.0
                y3 = (1.0 - r / max_r1_safe) * BOTTOM_L
                proj_base = project_point(P, x3, y3, 0.0)
                if proj_base is None:
                    continue

                for l in range(final_max_l + 1):
                    z3 = (1.0 - l / final_max_l) * HEIGHT_EST
                    proj_l = project_point(P, x3, y3, z3)
                    if proj_l is None:
                        continue
                    final_x = float(floor_pt["x"]) + (float(proj_l["x"]) - float(proj_base["x"]))
                    final_y = float(floor_pt["y"]) + (float(proj_l["y"]) - float(proj_base["y"]))
                    pt = make_point(final_x, final_y, "5", c=c, r=r, l=l, x3=x3, y3=y3, z3=z3)
                    full_grid.append(pt)
                    new_pts.append(pt)

        self.computed_points = [p for p in self.computed_points if str(p.get("label")) != "5"] + new_pts
        self.grid_data = [p for p in self.grid_data if str(p.get("label")) != "5"] + full_grid
        self.lines_map["5"] = self.build_lines_for_label(full_grid, "5")
        self.show_message(f"全空间体积补全成功，共 {len(full_grid)} 个体积点。")

    def interpolate_missing(self, values: List[Optional[float]]) -> None:
        last_known = 0
        for i in range(1, len(values)):
            if values[i] is not None:
                gap = i - last_known
                start = values[last_known] or 0.0
                step = (float(values[i]) - start) / gap
                for j in range(last_known + 1, i):
                    values[j] = start + step * (j - last_known)
                last_known = i

    def make_green_map(self, points: Sequence[Point], H_inv: Matrix, n_cols: int, n_rows: int) -> set[str]:
        green_map: set[str] = set()
        for pt in points:
            sp = apply_homography(H_inv, pt)
            if not sp:
                continue
            c = round((float(sp["x"]) / 1000.0) * (n_cols - 1))
            r = round((float(sp["y"]) / 1000.0) * (n_rows - 1))
            c = int(clamp(c, 0, n_cols - 1))
            r = int(clamp(r, 0, n_rows - 1))
            green_map.add(f"{c},{r}")
        return green_map

    def clear_current_layer(self) -> None:
        label = self.get_current_label()
        self.computed_points = [p for p in self.computed_points if str(p.get("label")) != label]
        self.grid_data = [p for p in self.grid_data if str(p.get("label")) != label]
        self.lines_map[label] = []
        self.show_message(f"已清除 {layer_title(label)} 的补全点。")
        self.redraw()

    def registration_layouts(self) -> Dict[str, Dict[str, float]]:
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        gap = 18.0
        top = 70.0
        pad = 18.0
        panel_w = max(100.0, (cw - pad * 2 - gap) / 2.0)
        panel_h = max(100.0, ch - top - pad)

        def fit(x0: float, y0: float, width: float, height: float) -> Dict[str, float]:
            scale = min(panel_w / max(1.0, width), panel_h / max(1.0, height))
            disp_w = width * scale
            disp_h = height * scale
            return {
                "x": x0 + (panel_w - disp_w) / 2.0,
                "y": y0 + (panel_h - disp_h) / 2.0,
                "w": disp_w,
                "h": disp_h,
                "scale": scale,
                "imgW": width,
                "imgH": height,
                "panelX": x0,
                "panelY": y0,
                "panelW": panel_w,
                "panelH": panel_h,
            }

        ref_w = float(self.image_meta.get("width") or 1.0)
        ref_h = float(self.image_meta.get("height") or 1.0)
        test_w = float(self.test_image_meta.get("width") or ref_w or 1.0)
        test_h = float(self.test_image_meta.get("height") or ref_h or 1.0)
        return {
            "ref": fit(pad, top, ref_w, ref_h),
            "test": fit(pad + panel_w + gap, top, test_w, test_h),
        }

    def registration_image_to_screen(self, side: str, point: Point) -> Tuple[float, float]:
        layout = self.registration_layouts()[side]
        return (
            layout["x"] + float(point["x"]) * layout["scale"],
            layout["y"] + float(point["y"]) * layout["scale"],
        )

    def registration_screen_to_image(self, x: float, y: float) -> Optional[Tuple[str, Point]]:
        for side, layout in self.registration_layouts().items():
            if layout["x"] <= x <= layout["x"] + layout["w"] and layout["y"] <= y <= layout["y"] + layout["h"]:
                return (
                    side,
                    {
                        "x": (x - layout["x"]) / (layout["scale"] or 1.0),
                        "y": (y - layout["y"]) / (layout["scale"] or 1.0),
                    },
                )
        return None

    def add_registration_point(self, side: str, point: Point) -> None:
        self.registration = {
            "H_test_to_ref": None,
            "H_ref_to_test": None,
            "errors": [],
            "meanError": None,
            "maxError": None,
        }
        if side == "ref":
            self.registration_pairs.append({"ref": {"x": point["x"], "y": point["y"]}})
            self.show_message(f"已添加图1点 #{len(self.registration_pairs)}；请在图2点同一角点。")
            return

        pending = next((pair for pair in reversed(self.registration_pairs) if pair.get("test") is None), None)
        if not pending:
            self.show_message("请先在左侧图1点一个对应角点。", True)
            return
        pending["test"] = {"x": point["x"], "y": point["y"]}
        self.show_message(f"已完成配准点对 #{len(self.complete_registration_pairs())}。")

    def delete_registration_at(self, side: str, point: Point) -> bool:
        threshold = 12.0
        layout = self.registration_layouts()[side]
        image_threshold = threshold / (layout["scale"] or 1.0)
        best_index: Optional[int] = None
        best_dist = float("inf")
        for index, pair in enumerate(self.registration_pairs):
            p = pair.get(side)
            if not p:
                continue
            dist = math.hypot(float(p["x"]) - point["x"], float(p["y"]) - point["y"])
            if dist < best_dist:
                best_dist = dist
                best_index = index
        if best_index is None or best_dist > image_threshold:
            return False
        del self.registration_pairs[best_index]
        self.registration = {
            "H_test_to_ref": None,
            "H_ref_to_test": None,
            "errors": [],
            "meanError": None,
            "maxError": None,
        }
        self.show_message("已删除配准点对。")
        return True

    def delete_yolo_box_at(self, point: Point, source_image: Optional[str] = None) -> bool:
        for index in range(len(self.yolo_boxes) - 1, -1, -1):
            box = self.yolo_boxes[index]
            box_source = str(box.get("sourceImage", "ref"))
            if source_image is not None and box_source != source_image:
                continue
            min_x = min(float(box["startX"]), float(box["endX"]))
            max_x = max(float(box["startX"]), float(box["endX"]))
            min_y = min(float(box["startY"]), float(box["endY"]))
            max_y = max(float(box["startY"]), float(box["endY"]))
            if min_x <= point["x"] <= max_x and min_y <= point["y"] <= max_y:
                del self.yolo_boxes[index]
                label = "图2" if box_source == "test" else "图1"
                self.show_message(f"已删除{label} YOLO 测试框。")
                return True
        return False

    # ------------------------------------------------------------------
    # Mouse operations
    # ------------------------------------------------------------------

    def on_mouse_down(self, event: tk.Event) -> None:
        if self.view_mode.get() == "3d":
            self.is_panning = True
            self.last_mouse = (event.x, event.y)
            return

        if self.view_mode.get() == "registration":
            hit = self.registration_screen_to_image(event.x, event.y)
            if not hit:
                return
            side, pos = hit
            mode = self.tool_mode.get()
            if mode == "delete":
                source_image = "test" if side == "test" else "ref"
                if self.delete_yolo_box_at(pos, source_image) or self.delete_registration_at(side, pos):
                    self.redraw()
                return
            if mode == "yolo":
                if side != "test":
                    self.show_message("YOLO 模式请在右侧图2拖拽画框；左侧图1用于显示映射后的定位点。")
                    return
                if not self.test_image_meta.get("width"):
                    self.show_message("请先载入测试图2。")
                    return
                if not self.registration.get("H_test_to_ref"):
                    self.show_message("请先完成双图配准并点击“计算配准矩阵”，再在图2画YOLO框。")
                    return
                self.drawing_yolo_box = {
                    "sourceImage": "test",
                    "startX": pos["x"],
                    "startY": pos["y"],
                    "currentX": pos["x"],
                    "currentY": pos["y"],
                }
                return
            if side == "test" and self.registration.get("H_test_to_ref") and self.test_image_meta.get("width"):
                self.drawing_yolo_box = {
                    "sourceImage": "test",
                    "startX": pos["x"],
                    "startY": pos["y"],
                    "currentX": pos["x"],
                    "currentY": pos["y"],
                }
                return
            self.add_registration_point(side, pos)
            self.redraw()
            return

        if not (self.image or self.image_meta.get("width")):
            return

        if self.tool_mode.get() == "pan":
            self.is_panning = True
            self.last_mouse = (event.x, event.y)
            return

        pos = self.screen_to_image(event.x, event.y)
        mode = self.tool_mode.get()

        if mode == "yolo":
            self.drawing_yolo_box = {
                "startX": pos["x"],
                "startY": pos["y"],
                "currentX": pos["x"],
                "currentY": pos["y"],
            }
            return

        if mode == "add":
            label = "1" if self.view_mode.get() == "topdown" else self.get_current_label()
            self.points.append(make_point(pos["x"], pos["y"], label))
            self.redraw()
            return

        if mode == "move":
            label = self.get_current_label()
            threshold = 15.0 / (self.draw_scale or 1.0)
            target = next(
                (
                    p
                    for p in self.points
                    if str(p.get("label", "1")) == label
                    and math.hypot(float(p["x"]) - pos["x"], float(p["y"]) - pos["y"]) < threshold
                ),
                None,
            )
            self.dragging_point_id = target.get("id") if target else None
            return

        if mode == "delete":
            self.delete_at(pos)
            self.redraw()

    def on_mouse_move(self, event: tk.Event) -> None:
        if self.is_panning:
            dx = event.x - self.last_mouse[0]
            dy = event.y - self.last_mouse[1]
            if self.view_mode.get() == "3d":
                self.cam_yaw -= dx * 0.01
                self.cam_pitch = clamp(self.cam_pitch - dy * 0.01, -math.pi / 2, math.pi / 2)
            else:
                self.pan_x += dx
                self.pan_y += dy
            self.last_mouse = (event.x, event.y)
            self.redraw()
            return

        if self.view_mode.get() == "registration":
            if self.drawing_yolo_box and str(self.drawing_yolo_box.get("sourceImage")) == "test":
                hit = self.registration_screen_to_image(event.x, event.y)
                if hit and hit[0] == "test":
                    pos = hit[1]
                    self.drawing_yolo_box["currentX"] = pos["x"]
                    self.drawing_yolo_box["currentY"] = pos["y"]
                    self.redraw()
            return

        pos = self.screen_to_image(event.x, event.y)

        if self.tool_mode.get() == "yolo" and self.drawing_yolo_box:
            self.drawing_yolo_box["currentX"] = pos["x"]
            self.drawing_yolo_box["currentY"] = pos["y"]
            self.redraw()
            return

        if self.tool_mode.get() == "move" and self.dragging_point_id is not None:
            for p in self.points:
                if p.get("id") == self.dragging_point_id:
                    p["x"] = pos["x"]
                    p["y"] = pos["y"]
                    break
            self.redraw()

    def on_mouse_up(self, _event: tk.Event) -> None:
        if self.view_mode.get() == "registration" and self.drawing_yolo_box:
            box = self.drawing_yolo_box
            if str(box.get("sourceImage")) == "test" and abs(float(box["startX"]) - float(box["currentX"])) > 10 and abs(
                float(box["startY"]) - float(box["currentY"])
            ) > 10:
                self.yolo_boxes.append(
                    {
                        "id": time.time_ns(),
                        "sourceImage": "test",
                        "startX": float(box["startX"]),
                        "startY": float(box["startY"]),
                        "endX": float(box["currentX"]),
                        "endY": float(box["currentY"]),
                    }
                )
                self.show_message("图2 YOLO 测试框已添加，将按配准矩阵映射到图1坐标系。")
            elif str(box.get("sourceImage")) == "test":
                self.add_registration_point("test", {"x": float(box["startX"]), "y": float(box["startY"])})
            self.drawing_yolo_box = None
            self.redraw()
            return

        if self.tool_mode.get() == "yolo" and self.drawing_yolo_box:
            box = self.drawing_yolo_box
            if abs(float(box["startX"]) - float(box["currentX"])) > 10 and abs(
                float(box["startY"]) - float(box["currentY"])
            ) > 10:
                self.yolo_boxes.append(
                    {
                        "id": time.time_ns(),
                        "startX": float(box["startX"]),
                        "startY": float(box["startY"]),
                        "endX": float(box["currentX"]),
                        "endY": float(box["currentY"]),
                    }
                )
                volume_candidates = self.volume_locator_candidates()
                if volume_candidates:
                    self.show_message("YOLO 测试框已添加，坐标将优先按第 5 层体积网格解算。")
                elif self.compute_ground_homography():
                    self.show_message("YOLO 测试框已添加，坐标已按当前底面网格解算。")
                else:
                    self.show_message("YOLO 测试框已添加；请先补全标签 1 底面网格或导入第 5 层体积网格。", True)
            self.drawing_yolo_box = None

        self.dragging_point_id = None
        self.is_panning = False
        self.redraw()

    def on_mouse_wheel(self, event: tk.Event) -> None:
        factor = 1.1 if event.delta > 0 else 1 / 1.1
        self.zoom(factor, center=(event.x, event.y))

    def delete_at(self, pos: Point) -> None:
        if self.delete_yolo_box_at(pos):
            return

        label = self.get_current_label()
        threshold = 15.0 / (self.draw_scale or 1.0)
        before = len(self.points)
        self.points = [
            p
            for p in self.points
            if not (
                str(p.get("label", "1")) == label
                and math.hypot(float(p["x"]) - pos["x"], float(p["y"]) - pos["y"]) < threshold
            )
        ]
        if len(self.points) != before:
            self.show_message("已删除标定点。")

    def zoom(self, factor: float, center: Optional[Tuple[float, float]] = None) -> None:
        if self.view_mode.get() == "3d":
            self.draw_scale = clamp(self.draw_scale * factor, 0.5, 5.0)
            self.redraw()
            return
        if self.view_mode.get() == "registration":
            return

        if not (self.image or self.image_meta.get("width")):
            return

        if center is None:
            center = (self.canvas.winfo_width() / 2.0, self.canvas.winfo_height() / 2.0)
        cx, cy = center
        img_x = (cx - self.pan_x) / (self.draw_scale or 1.0)
        img_y = (cy - self.pan_y) / (self.draw_scale or 1.0)
        self.draw_scale = clamp(self.draw_scale * factor, 0.05, 20.0)
        self.pan_x = cx - img_x * self.draw_scale
        self.pan_y = cy - img_y * self.draw_scale
        self.redraw()

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------
    def redraw(self) -> None:
        self.update_sidebar_visibility()
        self.canvas.delete("all")
        if self.view_mode.get() == "chart":
            self.draw_trajectory_dashboard()
            return
        if self.view_mode.get() == "3d":
            self.draw_3d_view()
            return
        if self.view_mode.get() == "registration":
            self.draw_registration_view()
            if self.drawing_yolo_box:
                self.draw_live_yolo_box()
            return

        self.draw_image_layer()
        self.draw_grid_lines()
        self.draw_points()
        self.draw_yolo_boxes()

        if self.drawing_yolo_box:
            self.draw_live_yolo_box()

        if self.tool_mode.get() == "yolo" and self.view_mode.get() == "perspective":
            self.draw_yolo_hint()

        mode_text = {
            "topdown": "俯视平面标定",
            "perspective": "侧视透视标定",
        }.get(self.view_mode.get(), self.view_mode.get())
        self.canvas.create_text(
            16,
            16,
            text=f"{mode_text} | 工具：{self.tool_mode.get()} | 图层：{self.get_current_layer_title()}",
            anchor="nw",
            fill="#cbd5e1",
            font=("Microsoft YaHei UI", 10, "bold"),
        )

    def draw_yolo_hint(self) -> None:
        text = "YOLO 目标映射测试模式：拖拽画框"
        if not self.compute_ground_homography():
            text += "；建议导入含第 5 层的完整体积网格"
        self.canvas.create_text(
            self.canvas.winfo_width() / 2,
            46,
            text=text,
            anchor="n",
            fill="#a5f3fc",
            font=("Microsoft YaHei UI", 11, "bold"),
        )

    def draw_registration_view(self) -> None:
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        self.canvas.create_rectangle(0, 0, cw, ch, fill="#020617", outline="")
        layouts = self.registration_layouts()

        self.canvas.create_text(
            16,
            16,
            text="双图配准 | 左侧图1/右侧图2按相同顺序点角点；计算配准后，在右侧图2拖框测试 YOLO",
            anchor="nw",
            fill="#cbd5e1",
            font=("Microsoft YaHei UI", 10, "bold"),
        )
        pair_count = len(self.complete_registration_pairs())
        mean_err = self.registration.get("meanError")
        status = f"点对：{pair_count}"
        if mean_err is not None:
            status += f" | 平均误差：{float(mean_err):.2f}px | 最大误差：{float(self.registration.get('maxError') or 0.0):.2f}px"
        self.canvas.create_text(16, 40, text=status, anchor="nw", fill="#a5b4fc", font=("Consolas", 10, "bold"))

        def draw_panel(side: str, title: str, img: Any, meta: Dict[str, Any], tk_attr: str) -> None:
            layout = layouts[side]
            px, py = layout["panelX"], layout["panelY"]
            pw, ph = layout["panelW"], layout["panelH"]
            self.canvas.create_rectangle(px, py, px + pw, py + ph, fill="#0f172a", outline="#334155", width=1)
            self.canvas.create_text(px + 10, py + 8, text=title, anchor="nw", fill="#e5e7eb", font=("Microsoft YaHei UI", 10, "bold"))

            if img is not None and Image is not None and ImageTk is not None:
                disp_w = max(1, int(layout["w"]))
                disp_h = max(1, int(layout["h"]))
                resampling = getattr(Image, "Resampling", Image).LANCZOS
                resized = img.resize((disp_w, disp_h), resampling)
                setattr(self, tk_attr, ImageTk.PhotoImage(resized))
                self.canvas.create_image(layout["x"], layout["y"], image=getattr(self, tk_attr), anchor="nw")
            elif meta.get("width") and meta.get("height"):
                self.canvas.create_rectangle(layout["x"], layout["y"], layout["x"] + layout["w"], layout["y"] + layout["h"], fill="#111827", outline="#475569")
                self.canvas.create_text(
                    layout["x"] + layout["w"] / 2.0,
                    layout["y"] + layout["h"] / 2.0,
                    text="已载入尺寸/点位，未载入图片",
                    fill="#94a3b8",
                    font=("Microsoft YaHei UI", 10),
                )
            else:
                self.canvas.create_text(
                    px + pw / 2.0,
                    py + ph / 2.0,
                    text="未载入图片",
                    fill="#64748b",
                    font=("Microsoft YaHei UI", 12, "bold"),
                )

        draw_panel("ref", f"图1 坐标图：{self.image_meta.get('fileName') or '未命名'}", self.image, self.image_meta, "registration_ref_tk_image")
        draw_panel("test", f"图2 测试图：{self.test_image_meta.get('fileName') or '未载入'}", self.test_image, self.test_image_meta, "test_tk_image")

        for index, pair in enumerate(self.registration_pairs, 1):
            ref = pair.get("ref")
            test = pair.get("test")
            if ref:
                sx, sy = self.registration_image_to_screen("ref", ref)
                self.canvas.create_oval(sx - 5, sy - 5, sx + 5, sy + 5, fill="#22c55e", outline="#ffffff", width=1)
                self.canvas.create_text(sx + 8, sy - 8, text=str(index), anchor="w", fill="#bbf7d0", font=("Consolas", 10, "bold"))
            if test:
                sx, sy = self.registration_image_to_screen("test", test)
                self.canvas.create_oval(sx - 5, sy - 5, sx + 5, sy + 5, fill="#f97316", outline="#ffffff", width=1)
                self.canvas.create_text(sx + 8, sy - 8, text=str(index), anchor="w", fill="#fed7aa", font=("Consolas", 10, "bold"))

                if ref:
                    projected = apply_homography(self.registration.get("H_test_to_ref"), test)
                    if projected:
                        px, py = self.registration_image_to_screen("ref", projected)
                        rx, ry = self.registration_image_to_screen("ref", ref)
                        self.canvas.create_line(rx, ry, px, py, fill="#c084fc", dash=(3, 3), width=2)
                        self.canvas.create_oval(px - 4, py - 4, px + 4, py + 4, fill="#c084fc", outline="")

        solved_boxes = self.compute_solved_yolo_boxes() if self.yolo_boxes else []
        solved_by_id = {box.get("id"): box for box in solved_boxes}
        for raw_box in self.yolo_boxes:
            source = str(raw_box.get("sourceImage", "ref"))
            side = "test" if source == "test" else "ref"
            if not self.image_source_size(side)[0]:
                continue
            x0, y0 = self.registration_image_to_screen(
                side,
                {
                    "x": min(float(raw_box["startX"]), float(raw_box["endX"])),
                    "y": min(float(raw_box["startY"]), float(raw_box["endY"])),
                },
            )
            x1, y1 = self.registration_image_to_screen(
                side,
                {
                    "x": max(float(raw_box["startX"]), float(raw_box["endX"])),
                    "y": max(float(raw_box["startY"]), float(raw_box["endY"])),
                },
            )
            self.canvas.create_rectangle(x0, y0, x1, y1, outline="#facc15", width=2)

            box = solved_by_id.get(raw_box.get("id"))
            if not box:
                continue
            center_ref = {"x": float(box.get("bboxCenterX", box.get("cx", 0.0))), "y": float(box.get("bboxCenterY", box.get("cy", 0.0)))}
            cx, cy = self.registration_image_to_screen("ref", center_ref)
            self.canvas.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill="#facc15", outline="#ffffff", width=1)
            center3d = box.get("center3D")
            if center3d:
                mx, my = self.registration_image_to_screen(
                    "ref",
                    {
                        "x": float(center3d.get("matchedPixelX", center_ref["x"])),
                        "y": float(center3d.get("matchedPixelY", center_ref["y"])),
                    },
                )
                if math.hypot(mx - cx, my - cy) > 2.0:
                    self.canvas.create_line(cx, cy, mx, my, fill="#c084fc", dash=(3, 3), width=2)
                self.canvas.create_oval(mx - 4, my - 4, mx + 4, my + 4, fill="#c084fc", outline="")
                cx, cy = mx, my
            err = box.get("centerMatchError")
            err_text = f" E:{float(err):.1f}px" if err is not None else ""
            self.canvas.create_text(
                cx + 8,
                cy - 10,
                text=f"[X:{box['X']:.1f} Y:{box['Y']:.1f} Z:{box.get('Z_total', box['Z']):.1f}{err_text}]",
                anchor="w",
                fill="#fef08a",
                font=("Consolas", 10, "bold"),
            )

    def draw_trajectory_dashboard(self) -> None:
        all_data = self.compute_solved_yolo_boxes()
        data = [item for item in all_data if self.is_trajectory_analysis_point(item)]
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        self.canvas.create_rectangle(0, 0, cw, ch, fill="#020617", outline="")

        if not data:
            self.canvas.create_text(
                cw / 2,
                ch / 2 - 24,
                text="暂无分析数据",
                fill="#94a3b8",
                font=("Microsoft YaHei UI", 22, "bold"),
            )
            self.canvas.create_text(
                cw / 2,
                ch / 2 + 12,
                text="Please draw or import YOLO boxes first",
                fill="#64748b",
                font=("Microsoft YaHei UI", 11),
            )
            return

        outer_pad = 24
        gap = 24
        if cw >= 900:
            card_w = (cw - outer_pad * 2 - gap) / 2
            cards = [
                (outer_pad, outer_pad, outer_pad + card_w, ch - outer_pad),
                (outer_pad + card_w + gap, outer_pad, cw - outer_pad, ch - outer_pad),
            ]
        else:
            card_h = (ch - outer_pad * 2 - gap) / 2
            cards = [
                (outer_pad, outer_pad, cw - outer_pad, outer_pad + card_h),
                (outer_pad, outer_pad + card_h + gap, cw - outer_pad, ch - outer_pad),
            ]

        self.draw_xy_trajectory_panel(cards[0], data)
        self.draw_height_curve_panel(cards[1], data)
        filtered_count = len(all_data) - len(data)
        if filtered_count > 0:
            self.canvas.create_text(
                cw - 24,
                18,
                text=f"已过滤空中点: {filtered_count}",
                anchor="ne",
                fill="#fbbf24",
                font=("Microsoft YaHei UI", 10, "bold"),
            )

    def draw_panel_frame(self, box: Tuple[float, float, float, float], title: str, title_color: str) -> Tuple[float, float, float, float]:
        x0, y0, x1, y1 = box
        self.canvas.create_rectangle(x0, y0, x1, y1, fill="#0f172a", outline="#1e293b", width=1)
        self.canvas.create_text(
            x0 + 18,
            y0 + 18,
            text=title,
            anchor="nw",
            fill=title_color,
            font=("Microsoft YaHei UI", 11, "bold"),
        )
        inner = (x0 + 16, y0 + 52, x1 - 16, y1 - 16)
        self.canvas.create_rectangle(*inner, fill="#020617", outline="#1e293b", width=1)
        return inner

    def draw_xy_trajectory_panel(self, box: Tuple[float, float, float, float], data: Sequence[Point]) -> None:
        x0, y0, x1, y1 = self.draw_panel_frame(box, "二维俯视轨迹 (X-Y 平面)", "#22d3ee")
        pad_x = 54
        pad_y = 36
        min_x, max_x = -BOTTOM_W / 2.0 - 10.0, BOTTOM_W / 2.0 + 10.0
        min_y, max_y = -10.0, BOTTOM_L + 10.0
        x_ticks = [-BOTTOM_W / 2.0, -BOTTOM_W / 4.0, 0.0, BOTTOM_W / 4.0, BOTTOM_W / 2.0]
        y_ticks = [0.0, BOTTOM_L / 3.0, BOTTOM_L * 2.0 / 3.0, BOTTOM_L]

        def map_x(value: float) -> float:
            return x0 + pad_x + ((value - min_x) / (max_x - min_x)) * max(1.0, (x1 - x0 - 2 * pad_x))

        def map_y(value: float) -> float:
            return y1 - pad_y - ((value - min_y) / (max_y - min_y)) * max(1.0, (y1 - y0 - 2 * pad_y))

        for value in x_ticks:
            sx = map_x(value)
            self.canvas.create_line(sx, map_y(min_y), sx, map_y(max_y), fill="#1e293b", width=1)
        for value in y_ticks:
            sy = map_y(value)
            self.canvas.create_line(map_x(min_x), sy, map_x(max_x), sy, fill="#1e293b", width=1)

        for value in (-BOTTOM_W / 2.0, 0.0, BOTTOM_W / 2.0):
            self.canvas.create_text(map_x(value), map_y(min_y) + 15, text=f"{value:.0f}", fill="#475569", font=("Consolas", 9))
        for value in y_ticks:
            self.canvas.create_text(map_x(min_x) - 8, map_y(value), text=f"{value:.0f}", fill="#475569", font=("Consolas", 9), anchor="e")

        for idx in range(len(data) - 1):
            p = data[idx]
            nxt = data[idx + 1]
            self.canvas.create_line(
                map_x(float(p["X"])),
                map_y(float(p["Y"])),
                map_x(float(nxt["X"])),
                map_y(float(nxt["Y"])),
                fill="#22d3ee",
                width=2,
                arrow=tk.LAST,
            )

        for idx, item in enumerate(data, 1):
            sx = map_x(float(item["X"]))
            sy = map_y(float(item["Y"]))
            self.canvas.create_oval(sx - 5, sy - 5, sx + 5, sy + 5, fill="#facc15", outline="")
            self.canvas.create_text(sx + 8, sy - 8, text=str(idx), anchor="w", fill="#fef08a", font=("Consolas", 10, "bold"))

    def draw_height_curve_panel(self, box: Tuple[float, float, float, float], data: Sequence[Point]) -> None:
        x0, y0, x1, y1 = self.draw_panel_frame(box, "Height by trajectory X (Z-X plane)", "#facc15")
        pad_x = 54
        pad_y = 38
        min_x, max_x = -BOTTOM_W / 2.0 - 10.0, BOTTOM_W / 2.0 + 10.0
        x_ticks = [-BOTTOM_W / 2.0, -BOTTOM_W / 4.0, 0.0, BOTTOM_W / 4.0, BOTTOM_W / 2.0]
        heights = [float(item.get("Z_total", item.get("mouseHeight", item.get("Z", 0.0)))) for item in data]
        raw_min = min(heights)
        raw_max = max(heights)
        value_range = 20.0 if abs(raw_max - raw_min) < 1e-8 else raw_max - raw_min
        min_h = max(0.0, raw_min - value_range * 0.2)
        max_h = min(150.0, raw_max + value_range * 0.2)
        if abs(max_h - min_h) < 1e-8:
            max_h = min(150.0, min_h + 20.0)

        def map_x(value: float) -> float:
            return x0 + pad_x + ((value - min_x) / (max_x - min_x)) * max(1.0, (x1 - x0 - 2 * pad_x))

        def map_h(value: float) -> float:
            return y1 - pad_y - ((value - min_h) / (max_h - min_h)) * max(1.0, (y1 - y0 - 2 * pad_y))

        for value in x_ticks:
            sx = map_x(value)
            self.canvas.create_line(sx, y0 + pad_y, sx, y1 - pad_y, fill="#1e293b", width=1)
        for value in (-BOTTOM_W / 2.0, 0.0, BOTTOM_W / 2.0):
            self.canvas.create_text(map_x(value), y1 - pad_y + 16, text=f"{value:.0f}", fill="#475569", font=("Consolas", 9))

        for i in range(6):
            value = min_h + i * (max_h - min_h) / 5
            sy = map_h(value)
            self.canvas.create_line(x0 + pad_x, sy, x1 - pad_x, sy, fill="#1e293b", width=1)
            self.canvas.create_text(x0 + pad_x - 8, sy, text=f"{value:.1f}", anchor="e", fill="#475569", font=("Consolas", 9))

        self.canvas.create_text((x0 + x1) / 2, y1 - 8, text="X", fill="#94a3b8", font=("Microsoft YaHei UI", 9))

        for idx in range(len(data) - 1):
            p = data[idx]
            nxt = data[idx + 1]
            self.canvas.create_line(
                map_x(float(p["X"])),
                map_h(heights[idx]),
                map_x(float(nxt["X"])),
                map_h(heights[idx + 1]),
                fill="#facc15",
                width=3,
                arrow=tk.LAST,
            )

        for idx, (item, height) in enumerate(zip(data, heights), 1):
            sx = map_x(float(item["X"]))
            sy = map_h(height)
            self.canvas.create_oval(sx - 5, sy - 5, sx + 5, sy + 5, fill="#34d3ee", outline="#0f172a", width=2)
            self.canvas.create_text(sx, sy - 14, text=f"{height:.1f}", fill="#cffafe", font=("Consolas", 9, "bold"))
            self.canvas.create_text(sx + 8, sy - 8, text=str(idx), anchor="w", fill="#fef08a", font=("Consolas", 10, "bold"))
    def draw_image_layer(self) -> None:
        width = to_int(self.image_meta.get("width"))
        height = to_int(self.image_meta.get("height"))
        if not width or not height:
            self.canvas.create_text(
                self.canvas.winfo_width() / 2,
                self.canvas.winfo_height() / 2,
                text="请从左侧载入底图开始",
                fill="#64748b",
                font=("Microsoft YaHei UI", 16, "bold"),
            )
            return

        if self.image is not None and Image is not None and ImageTk is not None:
            disp_w = max(1, int(width * self.draw_scale))
            disp_h = max(1, int(height * self.draw_scale))
            resampling = getattr(Image, "Resampling", Image).LANCZOS
            resized = self.image.resize((disp_w, disp_h), resampling)
            self.tk_image = ImageTk.PhotoImage(resized)
            self.canvas.create_image(self.pan_x, self.pan_y, image=self.tk_image, anchor="nw")
        else:
            x0, y0 = self.pan_x, self.pan_y
            x1, y1 = self.pan_x + width * self.draw_scale, self.pan_y + height * self.draw_scale
            self.canvas.create_rectangle(x0, y0, x1, y1, fill="#111827", outline="#334155")
            self.canvas.create_text(
                (x0 + x1) / 2,
                (y0 + y1) / 2,
                text="已导入点位，但未载入底图",
                fill="#94a3b8",
                font=("Microsoft YaHei UI", 12),
            )


    def draw_grid_lines(self) -> None:
        for label, lines in self.lines_map.items():
            if self.view_mode.get() == "topdown" and str(label) != "1":
                continue
            active = str(label) == self.get_current_label()
            alpha_factor = 1.0 if active else 0.5
            if self.ui_alpha(alpha_factor) <= 0.01:
                continue
            color = self.ui_color(LABEL_COLORS.get(str(label), "#e5e7eb"), alpha_factor)
            width = 2 if active else 1
            draw_lines = lines
            if str(label) == "5" and len(lines) > MAX_LABEL5_PREVIEW_LINES:
                step = max(1, math.ceil(len(lines) / MAX_LABEL5_PREVIEW_LINES))
                draw_lines = lines[::step]
            for p1, p2 in draw_lines:
                x1, y1 = self.image_to_screen(p1)
                x2, y2 = self.image_to_screen(p2)
                self.canvas.create_line(x1, y1, x2, y2, fill=color, width=width)

    def draw_points(self) -> None:
        current = self.get_current_label()
        computed_points = self.computed_points
        if self.view_mode.get() == "topdown":
            computed_points = [p for p in computed_points if str(p.get("label", "1")) == "1"]
        label5_points = [p for p in computed_points if str(p.get("label")) == "5"]
        if len(label5_points) > MAX_LABEL5_PREVIEW_POINTS:
            step = max(1, math.ceil(len(label5_points) / MAX_LABEL5_PREVIEW_POINTS))
            label5_keep_ids = {id(p) for p in label5_points[::step]}
            computed_points = [
                p
                for p in computed_points
                if str(p.get("label")) != "5" or id(p) in label5_keep_ids
            ]
        for p in computed_points:
            x, y = self.image_to_screen(p)
            label = str(p.get("label", current))
            active = label == current
            alpha_factor = 0.8 if active else 0.35
            if self.ui_alpha(alpha_factor) <= 0.01:
                continue
            color = self.ui_color(LABEL_COLORS.get(label, "#ef4444"), alpha_factor)
            radius = 2 if str(p.get("label")) == "5" else 3
            self.canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=color, outline="")

        for p in self.points:
            if self.view_mode.get() == "topdown" and str(p.get("label", "1")) != "1":
                continue
            x, y = self.image_to_screen(p)
            label = str(p.get("label", "1"))
            active = label == current
            base_color = LABEL_COLORS.get(label, "#22c55e") if active else "#64748b"
            alpha_factor = 1.0 if active else 0.6
            if self.ui_alpha(alpha_factor) <= 0.01:
                continue
            color = self.ui_color(base_color, alpha_factor)
            radius = 5 if active else 3
            self.canvas.create_oval(x - radius, y - radius, x + radius, y + radius, fill=color, outline=self.ui_color("#020617", 1.0))
            if p.get("id") == self.dragging_point_id:
                self.canvas.create_oval(x - 8, y - 8, x + 8, y + 8, outline=self.ui_color("#ffffff", 1.0), width=2)

    def draw_yolo_boxes(self) -> None:
        solved_boxes = self.compute_solved_yolo_boxes()
        solved_by_id = {box.get("id"): box for box in solved_boxes}
        volume_candidates = self.volume_locator_candidates()
        ground_ready = self.compute_ground_homography() is not None or bool(volume_candidates)

        for raw_box in self.yolo_boxes:
            box = solved_by_id.get(raw_box.get("id"))
            draw_box = box or raw_box
            if str(raw_box.get("sourceImage", "ref")) == "test":
                if not box:
                    continue
                center_px = {
                    "x": float(box.get("bboxCenterX", box.get("cx", 0.0))),
                    "y": float(box.get("bboxCenterY", box.get("cy", 0.0))),
                }
                cx, cy = self.image_to_screen(center_px)
                self.canvas.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill="#eab308", outline="")
                center3d = box.get("center3D")
                text_x, text_y = cx, cy
                if center3d:
                    mx, my = self.image_to_screen(
                        {
                            "x": float(center3d.get("matchedPixelX", center_px["x"])),
                            "y": float(center3d.get("matchedPixelY", center_px["y"])),
                        }
                    )
                    if math.hypot(mx - cx, my - cy) > 2.0:
                        self.canvas.create_line(cx, cy, mx, my, fill="#c084fc", dash=(3, 3), width=2)
                    self.canvas.create_oval(mx - 4, my - 4, mx + 4, my + 4, fill="#c084fc", outline="")
                    text_x, text_y = mx, my
                err = box.get("centerMatchError")
                err_text = f" E:{float(err):.1f}px" if err is not None else ""
                self.canvas.create_text(
                    text_x + 8,
                    text_y - 10,
                    text=f"[图2映射 X:{box['X']:.1f} Y:{box['Y']:.1f} Z:{box.get('Z_total', box['Z']):.1f}{err_text}]",
                    anchor="w",
                    fill="#fef08a",
                    font=("Consolas", 10, "bold"),
                )
                continue

            x0, y0 = self.image_to_screen(
                {
                    "x": min(float(draw_box["startX"]), float(draw_box["endX"])),
                    "y": min(float(draw_box["startY"]), float(draw_box["endY"])),
                }
            )
            x1, y1 = self.image_to_screen(
                {
                    "x": max(float(draw_box["startX"]), float(draw_box["endX"])),
                    "y": max(float(draw_box["startY"]), float(draw_box["endY"])),
                }
            )
            self.canvas.create_rectangle(x0, y0, x1, y1, outline="#facc15", width=2)

            if not box:
                bc_x = (float(raw_box["startX"]) + float(raw_box["endX"])) / 2.0
                bc_y = (float(raw_box["startY"]) + float(raw_box["endY"])) / 2.0
                cx, cy = self.image_to_screen({"x": bc_x, "y": bc_y})
                self.canvas.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill="#eab308", outline="")
                hint = "先补全标签1底面网格或第5层体积网格" if not ground_ready else "无法解算"
                self.canvas.create_text(
                    cx + 8,
                    cy - 10,
                    text=hint,
                    anchor="w",
                    fill="#fef08a",
                    font=("Microsoft YaHei UI", 10, "bold"),
                )
                continue

            center_px = {
                "x": float(box.get("bboxCenterX", box.get("cx", 0.0))),
                "y": float(box.get("bboxCenterY", box.get("cy", 0.0))),
            }
            cx, cy = self.image_to_screen(center_px)
            text_x, text_y = cx, cy

            center3d = box.get("center3D")
            if center3d:
                mx, my = self.image_to_screen(
                    {
                        "x": float(center3d.get("matchedPixelX", center_px["x"])),
                        "y": float(center3d.get("matchedPixelY", center_px["y"])),
                    }
                )
                if math.hypot(mx - cx, my - cy) > 2.0:
                    self.canvas.create_line(cx, cy, mx, my, fill="#c084fc", dash=(3, 3), width=2)
                self.canvas.create_oval(mx - 4, my - 4, mx + 4, my + 4, fill="#c084fc", outline="")
                text_x, text_y = mx, my

            self.canvas.create_oval(cx - 5, cy - 5, cx + 5, cy + 5, fill="#eab308", outline="")
            err = box.get("centerMatchError")
            err_text = f" E:{float(err):.1f}px" if err is not None else ""
            self.canvas.create_text(
                text_x + 8,
                text_y - 10,
                text=f"[X:{box['X']:.1f} Y:{box['Y']:.1f} Z:{box.get('Z_total', box['Z']):.1f}{err_text}]",
                anchor="w",
                fill="#fef08a",
                font=("Consolas", 10, "bold"),
            )

    def draw_live_yolo_box(self) -> None:
        box = self.drawing_yolo_box
        if not box:
            return
        p0 = {"x": min(float(box["startX"]), float(box["currentX"])), "y": min(float(box["startY"]), float(box["currentY"]))}
        p1 = {"x": max(float(box["startX"]), float(box["currentX"])), "y": max(float(box["startY"]), float(box["currentY"]))}
        if self.view_mode.get() == "registration" and str(box.get("sourceImage")) == "test":
            x0, y0 = self.registration_image_to_screen("test", p0)
            x1, y1 = self.registration_image_to_screen("test", p1)
        else:
            x0, y0 = self.image_to_screen(p0)
            x1, y1 = self.image_to_screen(p1)
        self.canvas.create_rectangle(x0, y0, x1, y1, outline="#fef08a", dash=(5, 5), width=2)

    def draw_3d_view(self) -> None:
        self.canvas.create_rectangle(0, 0, self.canvas.winfo_width(), self.canvas.winfo_height(), fill="#0f172a", outline="")
        p3d, _lines3d, box_lines = self.compute_p3d_bundle(include_lines=False)
        lines3d = self.build_3d_preview_lines(p3d)
        all_solved_boxes = self.compute_solved_yolo_boxes() if self.yolo_boxes else []
        solved_boxes = [box for box in all_solved_boxes if self.is_trajectory_analysis_point(box)]
        filtered_boxes = len(all_solved_boxes) - len(solved_boxes)
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        cx = cw / 2.0
        cy = ch / 2.0 + 90

        def project_physical(X: float, Y: float, Z: float) -> Dict[str, float]:
            x = X / BOTTOM_L
            y = (Y - BOTTOM_L / 2.0) / BOTTOM_L
            z = (Z - HEIGHT_EST / 2.0) / BOTTOM_L

            x1 = x * math.cos(self.cam_yaw) - y * math.sin(self.cam_yaw)
            y1 = x * math.sin(self.cam_yaw) + y * math.cos(self.cam_yaw)

            y2 = y1 * math.cos(self.cam_pitch) + z * math.sin(self.cam_pitch)
            z2 = -y1 * math.sin(self.cam_pitch) + z * math.cos(self.cam_pitch)

            s = self.draw_scale * min(cw, ch) * 0.8
            return {"px": cx + x1 * s, "py": cy - y2 * s, "depth": z2}

        for p1, p2 in box_lines:
            a = project_physical(float(p1["x3"]), float(p1["y3"]), float(p1["z3"]))
            b = project_physical(float(p2["x3"]), float(p2["y3"]), float(p2["z3"]))
            self.canvas.create_line(a["px"], a["py"], b["px"], b["py"], fill=self.ui_color("#64748b", 0.5), width=1)

        projected_lines = []
        for p1, p2 in lines3d:
            a = project_physical(float(p1["x3"]), float(p1["y3"]), float(p1["z3"]))
            b = project_physical(float(p2["x3"]), float(p2["y3"]), float(p2["z3"]))
            projected_lines.append((a, b, (a["depth"] + b["depth"]) / 2.0, str(p1.get("label", "1"))))

        for a, b, _depth, label in sorted(projected_lines, key=lambda item: item[2]):
            self.canvas.create_line(
                a["px"],
                a["py"],
                b["px"],
                b["py"],
                fill=self.ui_color(LABEL_COLORS.get(label, "#e5e7eb"), 0.7),
                width=2,
            )

        label5_points = [p for p in p3d if str(p.get("label")) == "5"]
        if len(label5_points) > MAX_LABEL5_3D_POINTS:
            step = max(1, math.ceil(len(label5_points) / MAX_LABEL5_3D_POINTS))
            label5_keep_ids = {id(p) for p in label5_points[::step]}
        else:
            label5_keep_ids = {id(p) for p in label5_points}
        preview_points = [
            p
            for p in p3d
            if str(p.get("label")) != "5" or id(p) in label5_keep_ids
        ]

        projected_points = []
        for p in preview_points:
            proj = project_physical(float(p["x3"]), float(p["y3"]), float(p["z3"]))
            projected_points.append((proj, p))

        for proj, p in sorted(projected_points, key=lambda item: item[0]["depth"]):
            label = str(p.get("label", "1"))
            radius = 2 if label == "5" else 4
            self.canvas.create_oval(
                proj["px"] - radius,
                proj["py"] - radius,
                proj["px"] + radius,
                proj["py"] + radius,
                fill=self.ui_color(LABEL_COLORS.get(label, "#e5e7eb"), 0.8),
                outline="",
            )

        for box in solved_boxes:
            z_total = float(box.get("Z_total", box.get("Z", 0.0)))
            proj = project_physical(float(box["X"]), float(box["Y"]), z_total)
            self.canvas.create_oval(
                proj["px"] - 6,
                proj["py"] - 6,
                proj["px"] + 6,
                proj["py"] + 6,
                fill=self.ui_color("#fde047", 1.0),
                outline=self.ui_color("#ffffff", 1.0),
            )
            err = box.get("centerMatchError")
            err_text = f" E:{float(err):.1f}px" if err is not None else ""
            self.canvas.create_text(
                proj["px"] + 10,
                proj["py"] - 10,
                text=f"[X:{box['X']:.1f} Y:{box['Y']:.1f} Z:{z_total:.1f}{err_text}]",
                anchor="w",
                fill="#fef08a",
                font=("Consolas", 10, "bold"),
            )

        axes = [
            ((100.0, 0.0, 0.0), "#ef4444", "X"),
            ((0.0, 100.0, 0.0), "#22c55e", "Y"),
            ((0.0, 0.0, 100.0), "#3b82f6", "Z"),
        ]
        origin = project_physical(0.0, 0.0, 0.0)
        for (x, y, z), color, label in axes:
            end = project_physical(x, y, z)
            self.canvas.create_line(origin["px"], origin["py"], end["px"], end["py"], fill=color, width=3)
            self.canvas.create_text(end["px"] + 6, end["py"], text=label, fill=color, font=("Consolas", 12, "bold"))

        self.canvas.create_text(
            16,
            16,
            text=f"3D 预览 | 总点数：{len(p3d)} | 显示点线：{len(preview_points)}/{len(lines3d)} | 底面目标：{len(solved_boxes)} | 已过滤空中点：{filtered_boxes}",
            anchor="nw",
            fill="#cbd5e1",
            font=("Microsoft YaHei UI", 10, "bold"),
        )


def main() -> None:
    root = tk.Tk()
    app = AnimalBoxCalibrationApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
