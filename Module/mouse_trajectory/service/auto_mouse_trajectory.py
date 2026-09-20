# -*- coding: utf-8 -*-
"""Batch mouse trajectory estimation with dynamic box registration.

The script processes an image folder frame by frame:
1. Detect the animal box with a segmentation model.
2. Register the current frame back to the reference image using box corners.
3. Detect the mouse with a detection model.
4. Reuse the calibration math from ``标定测试代码.py`` to compute X/Y/Z.
5. Export CSV/JSON, box-shift logs, and trajectory plots.
"""

from __future__ import annotations

import csv
import importlib.util
import json
import math
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent
PACKAGE_ROOT = ROOT.parent
YOLO_ROOT = ROOT / "ultralytics-main-pose"
RUNS_DIR = ROOT / "runs"
# Ultralytics may mangle absolute paths containing non-ASCII characters on Windows.
# A relative ASCII config directory keeps settings/cache writes inside this project.
os.environ.setdefault("YOLO_CONFIG_DIR", str((PACKAGE_ROOT / "_ultralytics_config").resolve()))
os.environ.setdefault("KMP_DUPLICATE_LIB_OK", "TRUE")

import cv2
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.collections import LineCollection
from matplotlib.colors import Normalize
from matplotlib.patches import Polygon as MplPolygon
import numpy as np

sys.path.insert(0, str(YOLO_ROOT))
from ultralytics import YOLO


IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".tif", ".tiff", ".webp"}
CORNER_SMOOTH_ALPHA = 0.25
CORNER_MAX_STEP_PX = 18.0
TRAJECTORY_MEDIAN_WINDOW = 5
TRAJECTORY_SMOOTH_ALPHA = 0.38
TRAJECTORY_MAX_STEP_MM = 38.0
RUN_SIZE_Y_WEIGHT = 0.0


Point = Dict[str, Any]
Matrix = List[List[float]]


@dataclass
class DetectionBox:
    xyxy: Tuple[float, float, float, float]
    conf: float
    cls: float


@dataclass
class BoxCorners:
    corners: List[Point]
    conf: float
    source: str


def load_calibration_module() -> Any:
    module_path = ROOT / "标定测试代码.py"
    spec = importlib.util.spec_from_file_location("animal_box_calibration", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load calibration module: {module_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


from Module.mouse_trajectory.utils import animal_box_calibration as CAL


class HeadlessCalibration(CAL.AnimalBoxCalibrationApp):
    """Minimal non-GUI shell around the calibration app's compute methods."""

    def __init__(self, grid_data: Sequence[Point], image_meta: Dict[str, Any]) -> None:
        self.points: List[Point] = []
        self.computed_points: List[Point] = []
        self.grid_data: List[Point] = list(grid_data)
        self.yolo_boxes: List[Point] = []
        self.registration_pairs: List[Dict[str, Point]] = []
        self.registration: Dict[str, Any] = {}
        self.image_meta = dict(image_meta)
        self.test_image_meta: Dict[str, Any] = {}
        self.lines_map: Dict[str, Any] = {}


def read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return json.loads(path.read_text(encoding="gbk"))


def parse_grid_json(path: Path) -> Tuple[List[Point], Dict[str, Any]]:
    data = read_json(path)
    grid_data: List[Point] = []
    for shape in data.get("shapes", []):
        if shape.get("shape_type") != "point":
            continue
        pts = shape.get("points") or []
        if not pts:
            continue
        desc = str(shape.get("description") or "")
        if not desc.startswith("grid_"):
            continue
        parts = desc.split("_")
        if len(parts) < 3:
            continue
        point = CAL.make_point(float(pts[0][0]), float(pts[0][1]), str(shape.get("label") or "1"))
        point["c"] = CAL.to_int(parts[1])
        point["r"] = CAL.to_int(parts[2])
        if len(parts) > 3:
            point["l"] = CAL.to_int(parts[3])
        grid_data.append(point)

    if not grid_data:
        raise RuntimeError(f"No grid points found in {path}")

    image_meta = {
        "fileName": data.get("imagePath") or path.name,
        "width": data.get("imageWidth"),
        "height": data.get("imageHeight"),
    }
    return grid_data, image_meta


def parse_image_registration_json(path: Optional[Path]) -> Optional[Dict[str, Any]]:
    if path is None or not path.exists():
        return None

    data = read_json(path)
    if data.get("format") != "Image_Registration_Homography":
        raise RuntimeError(f"Unsupported image-registration file format: {path}")

    H_test_to_ref = data.get("H_test_to_ref")
    H_ref_to_test = data.get("H_ref_to_test")
    if not H_ref_to_test and not H_test_to_ref:
        raise RuntimeError(f"Registration file missing H_ref_to_test/H_test_to_ref: {path}")
    if not H_test_to_ref and H_ref_to_test:
        H_test_to_ref = matrix_inverse_homography(H_ref_to_test)

    return {
        "source": str(path),
        "H_test_to_ref": H_test_to_ref,
        "meanError": data.get("meanError"),
        "maxError": data.get("maxError"),
        "referenceImage": data.get("referenceImage"),
        "testImage": data.get("testImage"),
    }


def parse_topdown_instrument_polygons(path: Optional[Path]) -> Tuple[Optional[List[Point]], Optional[List[Point]]]:
    if path is None or not path.exists():
        return None, None

    data = read_json(path)
    polygons: List[List[Point]] = []
    points: List[Point] = []
    for shape in data.get("shapes", []):
        raw_pts = shape.get("points") or []
        if shape.get("shape_type") == "polygon" and len(raw_pts) >= 3:
            polygons.append([{"x": float(x), "y": float(y)} for x, y in raw_pts])
        elif shape.get("shape_type") == "point" and raw_pts:
            points.append({"x": float(raw_pts[0][0]), "y": float(raw_pts[0][1])})

    if len(polygons) >= 2:
        ordered = sorted(
            polygons,
            key=lambda poly: abs(float(cv2.contourArea(np.asarray([[p["x"], p["y"]] for p in poly], dtype=np.float32)))),
            reverse=True,
        )
        return order_corners(np.asarray([[p["x"], p["y"]] for p in ordered[0]], dtype=np.float32)), order_corners(
            np.asarray([[p["x"], p["y"]] for p in ordered[1]], dtype=np.float32)
        )

    # The provided top-view file stores a large box rectangle plus a small
    # instrument rectangle as points. The outer box is points 1,2,3,7; the
    # instrument region is the last four points.
    if len(points) >= 7:
        outer = order_corners(np.asarray([[points[i]["x"], points[i]["y"]] for i in (0, 1, 2, 6)], dtype=np.float32))
        instrument = order_corners(np.asarray([[p["x"], p["y"]] for p in points[-4:]], dtype=np.float32))
        return outer, instrument
    if len(points) >= 4:
        outer = order_corners(np.asarray([[p["x"], p["y"]] for p in points[:4]], dtype=np.float32))
        return outer, None
    return None, None


def map_topdown_polygon_to_physical(
    outer_polygon: Optional[Sequence[Point]],
    instrument_polygon: Optional[Sequence[Point]],
) -> Optional[List[Point]]:
    if not outer_polygon or not instrument_polygon:
        return None

    # order_corners returns TL, TR, BR, BL. The calibration ground convention
    # uses TL, TR, BL, BR mapped to X/Y physical coordinates.
    outer = list(outer_polygon)
    pix_corners = [outer[0], outer[1], outer[3], outer[2]]
    phys_corners = [
        {"x": -CAL.BOTTOM_W / 2.0, "y": CAL.BOTTOM_L},
        {"x": CAL.BOTTOM_W / 2.0, "y": CAL.BOTTOM_L},
        {"x": -CAL.BOTTOM_W / 2.0, "y": 0.0},
        {"x": CAL.BOTTOM_W / 2.0, "y": 0.0},
    ]
    H_topdown_to_xy = CAL.get_perspective_transform(pix_corners, phys_corners)
    if not H_topdown_to_xy:
        return None

    mapped: List[Point] = []
    for pt in instrument_polygon:
        phys = CAL.apply_homography(H_topdown_to_xy, pt)
        if not phys:
            return None
        mapped.append({"x": float(phys["x"]), "y": float(phys["y"])})
    return mapped


def order_corners(points: np.ndarray) -> List[Point]:
    pts = np.asarray(points, dtype=np.float32).reshape(-1, 2)
    if len(pts) != 4:
        raise ValueError("Exactly four points are required")
    sums = pts.sum(axis=1)
    diffs = pts[:, 0] - pts[:, 1]
    ordered = np.array(
        [
            pts[np.argmin(sums)],  # top-left
            pts[np.argmax(diffs)],  # top-right
            pts[np.argmax(sums)],  # bottom-right
            pts[np.argmin(diffs)],  # bottom-left
        ],
        dtype=np.float32,
    )
    return [{"x": float(x), "y": float(y)} for x, y in ordered]


def best_box_from_result(result: Any) -> Optional[DetectionBox]:
    boxes = getattr(result, "boxes", None)
    if boxes is None or len(boxes) == 0:
        return None
    confs = boxes.conf.cpu().numpy()
    idx = int(np.argmax(confs))
    xyxy = boxes.xyxy[idx].cpu().numpy().astype(float).tolist()
    cls = float(boxes.cls[idx].cpu().numpy()) if boxes.cls is not None else 0.0
    return DetectionBox(tuple(xyxy), float(confs[idx]), cls)


def approximate_mask_quadrilateral(poly: np.ndarray) -> Optional[np.ndarray]:
    pts = np.asarray(poly, dtype=np.float32).reshape(-1, 2)
    if len(pts) < 4:
        return None

    hull = cv2.convexHull(pts).reshape(-1, 2)
    if len(hull) < 4:
        return None

    perimeter = cv2.arcLength(hull.reshape(-1, 1, 2), True)
    if perimeter <= 0:
        return None

    best_quad: Optional[np.ndarray] = None
    best_area = -1.0
    for ratio in np.linspace(0.005, 0.08, 24):
        approx = cv2.approxPolyDP(hull.reshape(-1, 1, 2), ratio * perimeter, True)
        approx_pts = approx.reshape(-1, 2)
        if len(approx_pts) != 4:
            continue

        area = abs(float(cv2.contourArea(approx_pts)))
        if area > best_area:
            best_area = area
            best_quad = approx_pts.astype(np.float32)

    return best_quad


def extract_box_corners(result: Any) -> Optional[BoxCorners]:
    best_box = best_box_from_result(result)
    masks = getattr(result, "masks", None)
    if masks is not None and getattr(masks, "xy", None):
        candidates: List[Tuple[float, np.ndarray]] = []
        for polygon in masks.xy:
            poly = np.asarray(polygon, dtype=np.float32)
            if len(poly) >= 3:
                candidates.append((abs(float(cv2.contourArea(poly))), poly))
        if candidates:
            _area, poly = max(candidates, key=lambda item: item[0])
            quad = approximate_mask_quadrilateral(poly)
            if quad is not None:
                return BoxCorners(order_corners(quad), best_box.conf if best_box else 0.0, "mask_quad")

            rect = cv2.minAreaRect(poly)
            corners = cv2.boxPoints(rect)
            return BoxCorners(order_corners(corners), best_box.conf if best_box else 0.0, "mask_min_area_rect")

    return None


def mean_corner_distance(a: Sequence[Point], b: Sequence[Point]) -> float:
    if len(a) != len(b) or not a:
        return float("inf")
    distances = [
        math.hypot(float(pa["x"]) - float(pb["x"]), float(pa["y"]) - float(pb["y"]))
        for pa, pb in zip(a, b)
    ]
    return float(sum(distances) / len(distances))


def smooth_corners(previous: Sequence[Point], current: Sequence[Point]) -> List[Point]:
    smoothed: List[Point] = []
    for old, new in zip(previous, current):
        ox, oy = float(old["x"]), float(old["y"])
        dx = (float(new["x"]) - ox) * CORNER_SMOOTH_ALPHA
        dy = (float(new["y"]) - oy) * CORNER_SMOOTH_ALPHA
        step = math.hypot(dx, dy)
        if step > CORNER_MAX_STEP_PX:
            scale = CORNER_MAX_STEP_PX / step
            dx *= scale
            dy *= scale
        smoothed.append({"x": ox + dx, "y": oy + dy})
    return smoothed


def percentile(values: Sequence[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    pos = (len(ordered) - 1) * clamp(q, 0.0, 1.0)
    lo = int(math.floor(pos))
    hi = int(math.ceil(pos))
    if lo == hi:
        return float(ordered[lo])
    frac = pos - lo
    return float(ordered[lo] * (1.0 - frac) + ordered[hi] * frac)


def clamp(value: float, min_value: float, max_value: float) -> float:
    return max(min_value, min(max_value, value))


def estimate_y_from_run_scale(scale: float, near_scale: float, far_scale: float) -> Optional[float]:
    if scale <= 0.0 or near_scale <= far_scale * 1.05:
        return None
    near = max(near_scale, far_scale + 1.0)
    far = max(1.0, far_scale)
    scale = clamp(scale, far, near)
    denom = math.log(near) - math.log(far)
    if abs(denom) < 1e-9:
        return None
    ratio = (math.log(near) - math.log(scale)) / denom
    return clamp(ratio * CAL.BOTTOM_L, 0.0, CAL.BOTTOM_L)


def list_images(source_dir: Path) -> List[Path]:
    images = [p for p in source_dir.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_EXTS]
    def natural_key(path: Path) -> List[Any]:
        return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]

    return sorted(images, key=natural_key)


def resolve_mouse_weight(explicit: Optional[Path], box_weight: Path) -> Path:
    if explicit:
        return explicit
    preferred = YOLO_ROOT / "runs" / "train" / "exp-小鼠" / "weights" / "best.pt"
    if preferred.exists():
        return preferred
    candidates = sorted((YOLO_ROOT / "runs" / "train").glob("exp*/weights/best.pt"), key=lambda p: str(p))
    for candidate in candidates:
        if candidate.resolve() != box_weight.resolve():
            return candidate
    return preferred


def run_yolo_single(model: YOLO, image_source: Path | np.ndarray, imgsz: int, conf: float) -> Any:
    source = str(image_source) if isinstance(image_source, Path) else image_source
    results = model.predict(
        source=source,
        imgsz=imgsz,
        conf=conf,
        verbose=False,
        save=False,
        device="cpu",
        half=False,
    )
    if not results:
        raise RuntimeError(f"YOLO returned no results for {image_source}")
    return results[0]


def run_yolo_batch(
    model: YOLO,
    image_sources: Sequence[Path | np.ndarray],
    imgsz: int,
    conf: float,
    batch_size: int = 8,
) -> List[Any]:
    if not image_sources:
        return []
    sources = [str(source) if isinstance(source, Path) else source for source in image_sources]
    results = model.predict(
        source=sources,
        imgsz=imgsz,
        conf=conf,
        verbose=False,
        save=False,
        device="cpu",
        half=False,
        batch=max(int(batch_size), 1),
    )
    return list(results or [])


def solve_mouse_location(
    solver: HeadlessCalibration,
    frame_box: DetectionBox,
    frame_name: str,
) -> Optional[Point]:
    x1, y1, x2, y2 = frame_box.xyxy
    solver.yolo_boxes = [
        {
            "id": time.time_ns(),
            "startX": float(x1),
            "startY": float(y1),
            "endX": float(x2),
            "endY": float(y2),
            "sourceImage": "test",
            "frameName": frame_name,
            "confidence": frame_box.conf,
            "class": frame_box.cls,
        }
    ]
    solved = solver.compute_solved_yolo_boxes()
    return solved[0] if solved else None


def matrix_inverse_homography(H: Matrix) -> Optional[Matrix]:
    mat = np.asarray(H, dtype=np.float64)
    try:
        inv = np.linalg.inv(mat)
    except np.linalg.LinAlgError:
        return None
    inv = inv / inv[2, 2]
    return inv.tolist()


TRAJECTORY_CSV_FIELDS = [
        "frameIndex",
        "frameName",
        "frameId",
        "cameraSessionId",
        "frameSequence",
        "timestamp",
        "datetime",
        "captureTimestamp",
        "submitTimestamp",
        "processingStartTimestamp",
        "yoloStartTimestamp",
        "yoloEndTimestamp",
        "captureMonotonicNs",
        "submitMonotonicNs",
        "processingStartMonotonicNs",
        "yoloStartMonotonicNs",
        "yoloEndMonotonicNs",
        "frameAgeMs",
        "queueWaitMs",
        "sharedFrameReadMs",
        "inferenceLatencyMs",
        "yoloBatchSize",
        "windowIndex",
        "windowStart",
        "windowEnd",
        "status",
        "effectiveSubmitFps",
        "effectiveYoloFps",
        "effectiveProcessingFps",
        "effectiveCageFps",
        "droppedFramesForCage",
        "busySkipCount",
        "sourceFrameDropCount",
        "staleFrameCount",
        "duplicateFrameCount",
        "queueDropCount",
        "pendingFramesForCage",
        "activeFramesForCage",
        "recordQueueSize",
        "previewQueueSize",
        "plotQueueSize",
        "boxCornerSource",
        "boxConf",
        "mouseConf",
        "mouseX1",
        "mouseY1",
        "mouseX2",
        "mouseY2",
        "mouseBoxWidth",
        "mouseBoxHeight",
        "mouseBoxArea",
        "mouseBoxScale",
        "registrationApplied",
        "registrationSource",
        "rawBBoxCenterX",
        "rawBBoxCenterY",
        "rawBBoxBottomY",
        "mappedCenterX",
        "mappedCenterY",
        "mappedBottomX",
        "mappedBottomY",
        "mappedDeltaPx",
        "rawX",
        "rawY",
        "rawZ",
        "runSizeDepthY",
        "trajectorySmoothed",
        "X",
        "Y",
        "Z",
        "Z_base",
        "mouseHeight",
        "locatorMethod",
        "frontBackSource",
        "cornerShiftPx",
        "registrationMeanError",
        "centerMatchError",
]


def save_csv(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=TRAJECTORY_CSV_FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {field: row.get(field, "") for field in TRAJECTORY_CSV_FIELDS}
            )


def append_csv(path: Path, rows: Sequence[Dict[str, Any]]) -> None:
    if not rows:
        return
    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=TRAJECTORY_CSV_FIELDS)
        if write_header:
            writer.writeheader()
        for row in rows:
            writer.writerow(
                {field: row.get(field, "") for field in TRAJECTORY_CSV_FIELDS}
            )


def polygon_centroid(polygon: Sequence[Point]) -> Point:
    if not polygon:
        return {"x": 0.0, "y": 0.0}
    return {
        "x": float(sum(float(p["x"]) for p in polygon) / len(polygon)),
        "y": float(sum(float(p["y"]) for p in polygon) / len(polygon)),
    }


def draw_instrument_region(
    ax: Any,
    instrument_polygon: Optional[Sequence[Point]],
    *,
    show_fill: bool = False,
    show_label: bool = True,
) -> None:
    if not instrument_polygon:
        return
    xy = np.asarray([[float(p["x"]), float(p["y"])] for p in instrument_polygon], dtype=float)
    patch = MplPolygon(
        xy,
        closed=True,
        facecolor="#f8fafc" if show_fill else "none",
        edgecolor="#475569",
        alpha=0.28 if show_fill else 1.0,
        linewidth=1.4,
        linestyle="--",
        label="instrument area",
    )
    ax.add_patch(patch)
    if not show_label:
        return
    center = polygon_centroid(instrument_polygon)
    ax.text(
        center["x"],
        center["y"],
        "instrument",
        ha="center",
        va="center",
        fontsize=8,
        color="#334155",
        weight="bold",
    )


def occupancy_points(
    rows: Sequence[Dict[str, Any]],
    instrument_polygon: Optional[Sequence[Point]],
) -> Tuple[List[float], List[float]]:
    xs: List[float] = []
    ys: List[float] = []
    instrument_center = polygon_centroid(instrument_polygon or [])
    previous_valid = False
    in_instrument_gap = False

    for row in rows:
        is_valid = row.get("status") == "ok" and row.get("X") is not None and row.get("Y") is not None
        if is_valid:
            xs.append(float(row["X"]))
            ys.append(float(row["Y"]))
            previous_valid = True
            in_instrument_gap = False
        elif row.get("status") == "no_mouse" and instrument_polygon and (previous_valid or in_instrument_gap):
            xs.append(float(instrument_center["x"]))
            ys.append(float(instrument_center["y"]))
            in_instrument_gap = True

    return xs, ys


def save_plots(output_dir: Path, rows: Sequence[Dict[str, Any]], instrument_polygon: Optional[Sequence[Point]] = None) -> None:
    valid = [r for r in rows if r.get("status") == "ok" and r.get("X") is not None and r.get("Y") is not None]
    fig, ax = plt.subplots(figsize=(9.8, 6.2))
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#f8fafc")
    draw_instrument_region(ax, instrument_polygon, show_fill=False)
    instrument_center = polygon_centroid(instrument_polygon or [])
    previous_valid: Optional[Dict[str, Any]] = None
    waiting_from_instrument = False
    gap_started = False
    progress_denominator = max(1, len(rows) - 1)
    trajectory_segments: List[Tuple[Tuple[float, float], Tuple[float, float], float]] = []
    inferred_segments: List[Tuple[Tuple[float, float], Tuple[float, float], float]] = []
    for row in rows:
        is_valid = row.get("status") == "ok" and row.get("X") is not None and row.get("Y") is not None
        progress = (int(row.get("frameIndex", 1)) - 1) / progress_denominator
        if is_valid:
            x = float(row["X"])
            y = float(row["Y"])
            if waiting_from_instrument and instrument_polygon:
                inferred_segments.append(((instrument_center["x"], instrument_center["y"]), (x, y), progress))
            elif previous_valid:
                trajectory_segments.append(((float(previous_valid["X"]), float(previous_valid["Y"])), (x, y), progress))
            previous_valid = row
            waiting_from_instrument = False
            gap_started = False
        elif row.get("status") == "no_mouse" and previous_valid and instrument_polygon and not gap_started:
            inferred_segments.append(
                ((float(previous_valid["X"]), float(previous_valid["Y"])), (instrument_center["x"], instrument_center["y"]), progress)
            )
            waiting_from_instrument = True
            gap_started = True

    progress_norm = Normalize(vmin=0.0, vmax=1.0)
    if valid:
        xs = [float(r["X"]) for r in valid]
        ys = [float(r["Y"]) for r in valid]
        if trajectory_segments:
            segments = np.asarray([(start, end) for start, end, _progress in trajectory_segments], dtype=float)
            progress_values = np.asarray([progress for _start, _end, progress in trajectory_segments], dtype=float)
            line = LineCollection(segments, cmap="viridis", norm=progress_norm, linewidths=2.2, alpha=0.92, zorder=2)
            line.set_array(progress_values)
            ax.add_collection(line)
            cbar = fig.colorbar(line, ax=ax, pad=0.015, fraction=0.045)
            cbar.set_label("trajectory progress")
            cbar.outline.set_visible(False)
        ax.scatter(xs, ys, c="#0f172a", s=10, alpha=0.45, linewidths=0, zorder=3)
        ax.scatter(xs[0], ys[0], c="#16a34a", s=62, edgecolors="white", linewidths=1.3, label="start", zorder=4)
        ax.scatter(xs[-1], ys[-1], c="#dc2626", s=62, edgecolors="white", linewidths=1.3, label="end", zorder=4)

    if inferred_segments:
        segments = np.asarray([(start, end) for start, end, _progress in inferred_segments], dtype=float)
        progress_values = np.asarray([progress for _start, _end, progress in inferred_segments], dtype=float)
        inferred_line = LineCollection(
            segments,
            cmap="viridis",
            norm=progress_norm,
            linewidths=1.6,
            linestyles=(0, (4, 3)),
            alpha=0.9,
            label="missing through instrument",
            zorder=1,
        )
        inferred_line.set_array(progress_values)
        ax.add_collection(inferred_line)
    if valid or inferred_segments or instrument_polygon:
        handles, labels = ax.get_legend_handles_labels()
        unique: Dict[str, Any] = {}
        for handle, label in zip(handles, labels):
            if label and not label.startswith("_") and label not in unique:
                unique[label] = handle
        ax.legend(
            unique.values(),
            unique.keys(),
            loc="center left",
            bbox_to_anchor=(1.14, 0.5),
            frameon=False,
            borderaxespad=0.0,
        )
    ax.set_title("Mouse X-Y Trajectory")
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_xlim(-CAL.BOTTOM_W / 2.0, CAL.BOTTOM_W / 2.0)
    ax.set_ylim(0.0, CAL.BOTTOM_L)
    ax.grid(True, color="#cbd5e1", alpha=0.38, linewidth=0.8)
    for spine in ax.spines.values():
        spine.set_color("#cbd5e1")
    ax.set_aspect("equal", adjustable="box")
    fig.tight_layout(rect=[0.0, 0.0, 0.80, 1.0])
    fig.savefig(output_dir / "xy_trajectory.png", dpi=160)
    plt.close(fig)

    fig, ax = plt.subplots(figsize=(8.5, 6.2))
    occ_xs, occ_ys = occupancy_points(rows, instrument_polygon)
    if occ_xs:
        xs = np.asarray(occ_xs, dtype=float)
        ys = np.asarray(occ_ys, dtype=float)
        heatmap, xedges, yedges = np.histogram2d(
            xs,
            ys,
            bins=(120, 180),
            range=[[-CAL.BOTTOM_W / 2.0, CAL.BOTTOM_W / 2.0], [0.0, CAL.BOTTOM_L]],
        )
        heatmap = cv2.GaussianBlur(heatmap.astype(np.float32), (0, 0), sigmaX=2.2, sigmaY=2.2)
        image = ax.imshow(
            heatmap.T,
            origin="lower",
            extent=[-CAL.BOTTOM_W / 2.0, CAL.BOTTOM_W / 2.0, 0.0, CAL.BOTTOM_L],
            aspect="auto",
            cmap="viridis",
            interpolation="bicubic",
        )
        fig.colorbar(image, ax=ax, label="frames")
    draw_instrument_region(ax, instrument_polygon, show_fill=False, show_label=False)
    ax.set_title("Mouse Occupancy Heatmap")
    ax.set_xlabel("X")
    ax.set_ylabel("Y")
    ax.set_xlim(-CAL.BOTTOM_W / 2.0, CAL.BOTTOM_W / 2.0)
    ax.set_ylim(0.0, CAL.BOTTOM_L)
    ax.grid(False)
    fig.tight_layout()
    fig.savefig(output_dir / "occupancy_heatmap.png", dpi=160)
    plt.close(fig)

    valid_h = [
        r
        for r in rows
        if r.get("status") == "ok" and r.get("X") is not None and r.get("Z") is not None
    ]
    fig, ax = plt.subplots(figsize=(9.2, 5.2))
    fig.patch.set_facecolor("#ffffff")
    ax.set_facecolor("#f8fafc")
    if valid_h:
        xs = [float(r["X"]) for r in valid_h]
        heights = [float(r["Z"]) for r in valid_h]
        if len(xs) > 1:
            points = np.column_stack([xs, heights]).reshape(-1, 1, 2)
            segments = np.concatenate([points[:-1], points[1:]], axis=1)
            line = LineCollection(segments, cmap="plasma", linewidths=2.4, alpha=0.94, zorder=2)
            line.set_array(np.linspace(0.0, 1.0, len(segments)))
            ax.add_collection(line)
            cbar = fig.colorbar(line, ax=ax, pad=0.018, fraction=0.045)
            cbar.set_label("trajectory progress")
            cbar.outline.set_visible(False)
        ax.scatter(xs, heights, c="#0f172a", s=12, alpha=0.42, linewidths=0, zorder=3)
        ax.scatter(xs[0], heights[0], c="#16a34a", s=58, edgecolors="white", linewidths=1.3, label="start", zorder=4)
        ax.scatter(xs[-1], heights[-1], c="#dc2626", s=58, edgecolors="white", linewidths=1.3, label="end", zorder=4)
        raw_min = min(heights)
        raw_max = max(heights)
        value_range = 20.0 if abs(raw_max - raw_min) < 1e-8 else raw_max - raw_min
        min_h = max(0.0, raw_min - value_range * 0.2)
        max_h = min(CAL.HEIGHT_EST, raw_max + value_range * 0.2)
        if abs(max_h - min_h) < 1e-8:
            max_h = min(CAL.HEIGHT_EST, min_h + 20.0)
        ax.set_ylim(min_h, max_h)
        ax.legend(loc="upper right", frameon=False)
    ax.set_title("Mouse Height by X")
    ax.set_xlabel("X")
    ax.set_ylabel("Z total")
    ax.set_xlim(-CAL.BOTTOM_W / 2.0, CAL.BOTTOM_W / 2.0)
    ax.grid(True, color="#cbd5e1", alpha=0.38, linewidth=0.8)
    for spine in ax.spines.values():
        spine.set_color("#cbd5e1")
    fig.tight_layout()
    fig.savefig(output_dir / "height_trajectory.png", dpi=160)
    plt.close(fig)


def draw_polyline(image: np.ndarray, corners: Sequence[Point], color: Tuple[int, int, int]) -> None:
    pts = np.array([[int(p["x"]), int(p["y"])] for p in corners], dtype=np.int32)
    cv2.polylines(image, [pts], isClosed=True, color=color, thickness=2)
    for idx, p in enumerate(corners, 1):
        xy = (int(p["x"]), int(p["y"]))
        cv2.circle(image, xy, 4, color, -1)
        cv2.putText(image, str(idx), (xy[0] + 5, xy[1] - 5), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)


def render_annotation_image(
    image_source: Path | np.ndarray,
    corners: Optional[BoxCorners],
    mouse_box: Optional[DetectionBox],
    solved: Optional[Point],
    status: str,
) -> Optional[np.ndarray]:
    image: np.ndarray | None
    if isinstance(image_source, Path):
        image = cv2.imread(str(image_source))
    else:
        image = None if image_source is None else image_source.copy()
    if image is None:
        return None
    if corners:
        draw_polyline(image, corners.corners, (0, 255, 255))
    if mouse_box:
        x1, y1, x2, y2 = [int(v) for v in mouse_box.xyxy]
        cv2.rectangle(image, (x1, y1), (x2, y2), (0, 255, 0), 2)
    label = status
    if solved:
        label = f"X:{solved.get('X', 0):.1f} Y:{solved.get('Y', 0):.1f} Z:{solved.get('Z_total', solved.get('Z', 0)):.1f}"
    cv2.putText(image, label, (20, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 0, 255), 2)
    return image


def save_annotation(
    image_source: Path | np.ndarray,
    output_path: Path,
    corners: Optional[BoxCorners],
    mouse_box: Optional[DetectionBox],
    solved: Optional[Point],
    status: str,
) -> None:
    image = render_annotation_image(image_source, corners, mouse_box, solved, status)
    if image is None:
        return
    cv2.imwrite(str(output_path), image)


def build_row(
    frame_index: int,
    frame_path: Path,
    status: str,
    corners: Optional[BoxCorners],
    mouse_box: Optional[DetectionBox],
    solved: Optional[Point],
    corner_shift: Optional[float],
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "frameIndex": frame_index,
        "frameName": frame_path.name,
        "status": status,
        "boxCornerSource": corners.source if corners else "",
        "boxConf": corners.conf if corners else None,
        "mouseConf": mouse_box.conf if mouse_box else None,
        "cornerShiftPx": corner_shift,
    }
    if mouse_box:
        x1, y1, x2, y2 = mouse_box.xyxy
        box_w = abs(x2 - x1)
        box_h = abs(y2 - y1)
        box_area = box_w * box_h
        row.update(
            {
                "mouseX1": x1,
                "mouseY1": y1,
                "mouseX2": x2,
                "mouseY2": y2,
                "mouseBoxWidth": box_w,
                "mouseBoxHeight": box_h,
                "mouseBoxArea": box_area,
                "mouseBoxScale": math.sqrt(box_area) if box_area > 0 else None,
            }
        )
    if solved:
        raw_cx = solved.get("rawBBoxCenterX")
        raw_cy = solved.get("rawBBoxCenterY")
        mapped_cx = solved.get("mappedCenterX")
        mapped_cy = solved.get("mappedCenterY")
        mapped_delta = None
        if raw_cx is not None and raw_cy is not None and mapped_cx is not None and mapped_cy is not None:
            mapped_delta = math.hypot(float(mapped_cx) - float(raw_cx), float(mapped_cy) - float(raw_cy))
        row.update(
            {
                "registrationApplied": solved.get("registrationApplied"),
                "registrationSource": solved.get("registrationSource"),
                "rawBBoxCenterX": raw_cx,
                "rawBBoxCenterY": raw_cy,
                "rawBBoxBottomY": solved.get("rawBBoxBottomY"),
                "mappedCenterX": mapped_cx,
                "mappedCenterY": mapped_cy,
                "mappedBottomX": solved.get("mappedBottomX"),
                "mappedBottomY": solved.get("mappedBottomY"),
                "mappedDeltaPx": mapped_delta,
                "runSizeDepthY": solved.get("bboxScaleY") or (solved.get("center3D") or {}).get("sizeDepthY"),
                "X": solved.get("X"),
                "Y": solved.get("Y"),
                "Z": solved.get("Z_total", solved.get("Z")),
                "Z_base": solved.get("Z_base"),
                "mouseHeight": solved.get("mouseHeight"),
                "registrationMeanError": solved.get("registrationMeanError"),
                "centerMatchError": solved.get("centerMatchError"),
                "locatorMethod": solved.get("locatorMethod"),
                "frontBackSource": solved.get("frontBackSource"),
            }
        )
    if corners:
        row["boxCorners"] = [[p["x"], p["y"]] for p in corners.corners]
    return row


def median(values: Sequence[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return float((ordered[mid - 1] + ordered[mid]) / 2.0)


def stabilize_trajectory_rows(rows: Sequence[Dict[str, Any]]) -> None:
    for row in rows:
        if row.get("status") != "ok" or row.get("X") is None or row.get("Y") is None:
            continue
        raw_x = float(row["X"])
        raw_y = float(row["Y"])
        raw_z = float(row.get("Z") or 0.0)
        row.setdefault("rawX", raw_x)
        row.setdefault("rawY", raw_y)
        row.setdefault("rawZ", raw_z)
        row["X"] = clamp(raw_x, -CAL.BOTTOM_W / 2.0, CAL.BOTTOM_W / 2.0)
        row["Y"] = clamp(raw_y, 0.0, CAL.BOTTOM_L)
        row["Z"] = clamp(raw_z, 0.0, CAL.HEIGHT_EST)
        row["trajectorySmoothed"] = False


def main(
    source_dir: Path,
    output_dir: Path,
    grid_json: Path,
    ref_image: Path,
    box_weight: Path,
    mouse_weight: Optional[Path],
    conf_box: float = 0.4,
    conf_mouse: float = 0.4,
    imgsz: int = 640,
    shift_threshold_px: float = 10.0,
    save_annotated: bool = True,
    max_frames: Optional[int] = None,
    instrument_area_json: Optional[Path] = None,
    image_registration_json: Optional[Path] = None,
) -> None:
    if not source_dir.exists() or not source_dir.is_dir():
        raise FileNotFoundError(f"Source folder does not exist: {source_dir}")
    frames = list_images(source_dir)
    if max_frames is not None and max_frames > 0:
        frames = frames[:max_frames]
    if not frames:
        raise RuntimeError(f"No supported images found in {source_dir}")

    if mouse_weight is None:
        mouse_weight = resolve_mouse_weight(None, box_weight)
    for required in (grid_json, ref_image, box_weight, mouse_weight):
        if not required.exists():
            raise FileNotFoundError(f"Required file does not exist: {required}")

    run_dir = output_dir / time.strftime("%Y%m%d_%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=False)
    annotated_dir = run_dir / "annotated"
    if save_annotated:
        annotated_dir.mkdir(parents=True, exist_ok=True)

    grid_data, image_meta = parse_grid_json(grid_json)
    solver = HeadlessCalibration(grid_data, image_meta)
    static_registration = parse_image_registration_json(image_registration_json)
    instrument_outer_img, instrument_polygon_img = parse_topdown_instrument_polygons(instrument_area_json)
    instrument_polygon_phys = map_topdown_polygon_to_physical(instrument_outer_img, instrument_polygon_img)

    print(f"Loading box model: {box_weight}")
    box_model = YOLO(str(box_weight))
    print(f"Loading mouse model: {mouse_weight}")
    mouse_model = YOLO(str(mouse_weight))

    ref_result = run_yolo_single(box_model, ref_image, imgsz, conf_box)
    ref_corners = extract_box_corners(ref_result)
    if ref_corners is None:
        raise RuntimeError(f"Could not detect reference box corners from {ref_image}")

    rows: List[Dict[str, Any]] = []
    shift_log: List[Dict[str, Any]] = []
    previous_corners: Optional[List[Point]] = list(ref_corners.corners)

    for frame_index, frame_path in enumerate(frames, 1):
        print(f"[{frame_index}/{len(frames)}] {frame_path.name}")
        corners: Optional[BoxCorners] = None
        mouse_box: Optional[DetectionBox] = None
        solved: Optional[Point] = None
        corner_shift: Optional[float] = None
        status = "ok"
        H_test_to_ref: Optional[Matrix] = None

        try:
            box_result = run_yolo_single(box_model, frame_path, imgsz, conf_box)
            corners = extract_box_corners(box_result)
            if corners is None:
                if previous_corners is None:
                    status = "no_previous_mask"
                else:
                    corners = BoxCorners(
                        [dict(point) for point in previous_corners],
                        0.0,
                        "previous_mask",
                    )
                    corner_shift = 0.0
            else:
                if previous_corners is not None:
                    corner_shift = mean_corner_distance(corners.corners, previous_corners)
                    if corner_shift > shift_threshold_px:
                        shift_log.append(
                            {
                                "frameIndex": frame_index,
                                "frameName": frame_path.name,
                                "meanShiftPx": corner_shift,
                                "thresholdPx": shift_threshold_px,
                                "corners": [[p["x"], p["y"]] for p in corners.corners],
                            }
                        )
                previous_corners = list(corners.corners)

            registration_mean_error = None
            registration_max_error = None
            registration_source = ""
            if corners is not None:
                H_temp = CAL.get_perspective_transform(corners.corners, ref_corners.corners)
                if H_temp:
                    H_test_to_ref = H_temp
                    registration_source = "dynamic_box"

            if H_test_to_ref is None and static_registration:
                H_test_to_ref = static_registration.get("H_test_to_ref")
                registration_mean_error = static_registration.get("meanError")
                registration_max_error = static_registration.get("maxError")
                registration_source = "image_registration_json"
                if status in ("no_previous_mask", "registration_failed"):
                    status = "ok"

            if H_test_to_ref is None:
                status = "registration_failed"
            else:
                mouse_result = run_yolo_single(mouse_model, frame_path, imgsz, conf_mouse)
                mouse_box = best_box_from_result(mouse_result)
                if mouse_box is None:
                    status = "no_mouse"
                else:
                    solver.registration = {
                        "H_test_to_ref": H_test_to_ref,
                        "meanError": registration_mean_error,
                        "maxError": registration_max_error,
                        "source": registration_source,
                    }
                    solved = solve_mouse_location(solver, mouse_box, frame_path.name)
                    if solved is not None:
                        solved["registrationSource"] = registration_source
                    if solved is None:
                        status = "solve_failed"
        except Exception as exc:
            status = "error"
            rows.append(
                {
                    "frameIndex": frame_index,
                    "frameName": frame_path.name,
                    "status": status,
                    "error": str(exc),
                }
            )
            if save_annotated:
                save_annotation(
                    frame_path,
                    annotated_dir / frame_path.name,
                    corners,
                    mouse_box,
                    solved,
                    status,
                )
            continue

        row = build_row(frame_index, frame_path, status, corners, mouse_box, solved, corner_shift)
        rows.append(row)
        if save_annotated:
            save_annotation(
                frame_path,
                annotated_dir / frame_path.name,
                corners,
                mouse_box,
                solved,
                str(row.get("status") or status or ""),
            )

    stabilize_trajectory_rows(rows)

    metadata = {
        "sourceDir": str(source_dir),
        "gridJson": str(grid_json),
        "referenceImage": str(ref_image),
        "boxWeight": str(box_weight),
        "mouseWeight": str(mouse_weight),
        "referenceBoxCorners": [[p["x"], p["y"]] for p in ref_corners.corners],
        "referenceCornerSource": ref_corners.source,
        "instrumentAreaJson": str(instrument_area_json) if instrument_area_json else "",
        "instrumentAreaPhysical": (
            [[p["x"], p["y"]] for p in instrument_polygon_phys] if instrument_polygon_phys else []
        ),
        "imageRegistrationJson": str(image_registration_json) if image_registration_json else "",
        "imageRegistration": static_registration,
        "trajectoryPostprocess": "none_raw_per_frame",
        "frameCount": len(frames),
        "okCount": sum(1 for row in rows if row.get("status") == "ok"),
    }

    save_csv(run_dir / "trajectory.csv", rows)
    (run_dir / "trajectory.json").write_text(
        json.dumps({"metadata": metadata, "frames": rows}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (run_dir / "box_shift_log.json").write_text(
        json.dumps({"thresholdPx": shift_threshold_px, "events": shift_log}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if instrument_polygon_phys:
        (run_dir / "instrument_area.json").write_text(
            json.dumps(
                {
                    "source": str(instrument_area_json) if instrument_area_json else "",
                    "imagePolygon": instrument_polygon_img,
                    "physicalPolygon": instrument_polygon_phys,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    save_plots(run_dir, rows, instrument_polygon_phys)
    print(f"Done. Output: {run_dir}")


if __name__ == "__main__":
    # ------------------------- Path/config section -------------------------
    # Edit these values directly, then run: python auto_mouse_trajectory.py
    source_dir = Path(r"D:\boshi\工程-动物箱\阴面2")
    output_dir = Path(r"D:\boshi\工程-动物箱\runs\auto_trajectory")
    grid_json = Path(r"D:\boshi\工程-动物箱\底片&&json\WIN_20260419_15_45_44_Pro_grid_completed.json")
    ref_image = Path(r"D:\boshi\工程-动物箱\底片&&json\WIN_20260419_15_45_44_Pro.jpg")
    box_weight = Path(r"D:\boshi\工程-动物箱\ultralytics-main-pose\runs\train\exp\weights\best.pt")
    mouse_weight = Path(r"D:\boshi\工程-动物箱\ultralytics-main-pose\runs\train\exp-小鼠\weights\best.pt")

    instrument_area_json = ROOT / "底片&&json" / "仪器区域.json"
    image_registration_json = ROOT / "底片&&json" / "WIN_20260419_15_45_44_Pro_image_registration.json"

    conf_box = 0.4
    conf_mouse = 0.4
    imgsz = 640
    shift_threshold_px = 10.0
    save_annotated = True
    max_frames: Optional[int] = None  # Set to 1/10/etc. for quick testing; None processes all images.
    # ----------------------------------------------------------------------

    main(
        source_dir=source_dir,
        output_dir=output_dir,
        grid_json=grid_json,
        ref_image=ref_image,
        box_weight=box_weight,
        mouse_weight=mouse_weight,
        conf_box=conf_box,
        conf_mouse=conf_mouse,
        imgsz=imgsz,
        shift_threshold_px=shift_threshold_px,
        save_annotated=save_annotated,
        max_frames=max_frames,
        instrument_area_json=instrument_area_json,
        image_registration_json=image_registration_json,
    )
