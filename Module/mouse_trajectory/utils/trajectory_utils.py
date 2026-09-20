import csv
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np


def read_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return json.loads(path.read_text(encoding="gbk"))


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def write_csv(path: Path, rows: list[dict[str, Any]], field_names: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=field_names)
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in field_names})


def apply_homography(matrix: list[list[float]] | None, x: float, y: float) -> tuple[float, float] | None:
    if not matrix:
        return None

    mat = np.asarray(matrix, dtype=np.float64)
    denominator = mat[2, 0] * x + mat[2, 1] * y + mat[2, 2]
    if abs(denominator) < 1e-12:
        return None

    mapped_x = (mat[0, 0] * x + mat[0, 1] * y + mat[0, 2]) / denominator
    mapped_y = (mat[1, 0] * x + mat[1, 1] * y + mat[1, 2]) / denominator
    return float(mapped_x), float(mapped_y)


def load_canvas_image(reference_image_path: Path | None, fallback_width: int = 1280, fallback_height: int = 720) -> np.ndarray:
    if reference_image_path is not None and reference_image_path.exists():
        image = cv2.imread(str(reference_image_path))
        if image is not None:
            return image
    return np.full((fallback_height, fallback_width, 3), 255, dtype=np.uint8)
