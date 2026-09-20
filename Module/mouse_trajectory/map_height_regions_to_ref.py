# -*- coding: utf-8 -*-
"""Reverse-map height-region polygons from reference-image coordinates back to test-image coordinates.

Edit the paths in the config section and run:
    python map_height_regions_to_ref.py
"""

from __future__ import annotations

import json
import re
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

import numpy as np


ROOT = Path(__file__).resolve().parent
Point = Dict[str, float]
Matrix = List[List[float]]


def read_json(path: Path) -> Dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError:
        return json.loads(path.read_text(encoding="gbk"))


def apply_homography(H: Sequence[Sequence[float]], point: Point) -> Optional[Point]:
    mat = np.asarray(H, dtype=np.float64)
    x = float(point["x"])
    y = float(point["y"])
    w = mat[2, 0] * x + mat[2, 1] * y + mat[2, 2]
    if abs(w) < 1e-12:
        return None
    return {
        "x": float((mat[0, 0] * x + mat[0, 1] * y + mat[0, 2]) / w),
        "y": float((mat[1, 0] * x + mat[1, 1] * y + mat[1, 2]) / w),
    }


def inverse_homography(H: Sequence[Sequence[float]]) -> Matrix:
    mat = np.asarray(H, dtype=np.float64)
    inv = np.linalg.inv(mat)
    inv = inv / inv[2, 2]
    return inv.tolist()


def choose_transform(registration_data: Dict[str, Any], direction: str) -> Tuple[Matrix, Matrix, str, str, str]:
    H_test_to_ref = registration_data.get("H_test_to_ref")
    H_ref_to_test = registration_data.get("H_ref_to_test")

    if direction == "test_to_ref":
        if not H_test_to_ref:
            if not H_ref_to_test:
                raise RuntimeError("Registration file missing both H_test_to_ref and H_ref_to_test")
            H_test_to_ref = inverse_homography(H_ref_to_test)
            matrix_source = "inverse_H_ref_to_test"
        else:
            matrix_source = "H_test_to_ref"
        return H_test_to_ref, inverse_homography(H_test_to_ref), "testImage", "referenceImage", matrix_source

    if direction == "ref_to_test":
        if H_ref_to_test:
            matrix_source = "H_ref_to_test"
        elif H_test_to_ref:
            # Fall back to the inverse when the reverse matrix is not stored.
            H_ref_to_test = inverse_homography(H_test_to_ref)
            matrix_source = "inverse_H_test_to_ref"
        else:
            raise RuntimeError("Registration file missing both H_test_to_ref and H_ref_to_test")
        return H_ref_to_test, inverse_homography(H_ref_to_test), "referenceImage", "testImage", matrix_source

    raise RuntimeError("direction must be 'test_to_ref' or 'ref_to_test'")


def point_list(raw_points: Sequence[Any]) -> List[List[float]]:
    points: List[List[float]] = []
    for raw_point in raw_points:
        if len(raw_point) >= 2:
            points.append([float(raw_point[0]), float(raw_point[1])])
    return points


def round_trip_errors(
    source_points: Sequence[Sequence[float]],
    mapped_points: Sequence[Sequence[float]],
    H_back: Sequence[Sequence[float]],
) -> List[float]:
    errors: List[float] = []
    for source, mapped in zip(source_points, mapped_points):
        restored = apply_homography(H_back, {"x": float(mapped[0]), "y": float(mapped[1])})
        if restored is None:
            continue
        errors.append(math.hypot(float(source[0]) - restored["x"], float(source[1]) - restored["y"]))
    return errors


def map_height_regions(
    height_regions_json: Path,
    image_registration_json: Path,
    output_json: Path,
    direction: str = "ref_to_test",
    strict_image_match: bool = False,
    prefer_stored_source_points: bool = True,
) -> None:
    height_data = read_json(height_regions_json)
    registration_data = read_json(image_registration_json)

    if height_data.get("format") != "Height_Constraint_Regions":
        raise RuntimeError(f"Unsupported height-region format: {height_regions_json}")
    if registration_data.get("format") != "Image_Registration_Homography":
        raise RuntimeError(f"Unsupported registration format: {image_registration_json}")

    H, H_back, source_coordinate, target_coordinate, matrix_source = choose_transform(registration_data, direction)

    expected_test_image = (registration_data.get("testImage") or {}).get("fileName")
    inferred_source_image = re.sub(r"_height_regions(?:_ref)?\.json$", ".jpg", height_regions_json.name)
    if strict_image_match and expected_test_image and inferred_source_image != expected_test_image:
        raise RuntimeError(
            "Registration test image does not match height-region source image: "
            f"{expected_test_image} != {inferred_source_image}. "
            "Please export a registration JSON for the image where the height region was drawn."
        )

    mapped_data = dict(height_data)
    mapped_data["sourceCoordinate"] = source_coordinate
    mapped_data["targetCoordinate"] = target_coordinate
    mapped_data["mappingDirection"] = direction
    mapped_data["matrixSource"] = matrix_source
    mapped_data["sourceHeightRegionsJson"] = str(height_regions_json)
    mapped_data["imageRegistrationJson"] = str(image_registration_json)
    mapped_data["referenceImage"] = registration_data.get("referenceImage")
    mapped_data["testImage"] = registration_data.get("testImage")

    mapped_regions: List[Dict[str, Any]] = []
    all_errors: List[float] = []
    for region in height_data.get("regions", []):
        mapped_region = dict(region)
        mapped_points: List[List[float]] = []

        raw_points = point_list(region.get("polygon_points", []))
        if direction == "ref_to_test" and prefer_stored_source_points and region.get("source_polygon_points"):
            # If this JSON was previously generated by this tool, these are the
            # original test-image coordinates and are more accurate than another
            # homography pass.
            mapped_points = [[round(p[0], 3), round(p[1], 3)] for p in point_list(region.get("source_polygon_points", []))]
        else:
            for raw_point in raw_points:
                mapped = apply_homography(H, {"x": float(raw_point[0]), "y": float(raw_point[1])})
                if mapped is None:
                    continue
                mapped_points.append([round(mapped["x"], 3), round(mapped["y"], 3)])

        for raw_point in raw_points:
            mapped = apply_homography(H, {"x": float(raw_point[0]), "y": float(raw_point[1])})
            if mapped is None:
                continue
        all_errors.extend(round_trip_errors(raw_points, mapped_points, H_back))

        if len(mapped_points) < 3:
            raise RuntimeError(f"Region has fewer than 3 mapped points: {region.get('name', region.get('id'))}")
        mapped_region["polygon_points"] = mapped_points
        mapped_region["source_polygon_points"] = region.get("polygon_points", [])
        mapped_regions.append(mapped_region)

    mapped_data["regions"] = mapped_regions
    mapped_data["roundTripErrorPx"] = {
        "mean": float(sum(all_errors) / len(all_errors)) if all_errors else None,
        "max": float(max(all_errors)) if all_errors else None,
    }
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(mapped_data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Reverse-mapped {len(mapped_regions)} height region(s) to: {output_json}")
    if all_errors:
        print(f"Round-trip error: mean={sum(all_errors) / len(all_errors):.6f}px max={max(all_errors):.6f}px")


if __name__ == "__main__":
    # ------------------------- Path/config section -------------------------
    height_regions_json = ROOT / "底片&&json" / "Infrared_Cam1_1776670391100_height_regions.json"
    image_registration_json = ROOT / "底片&&json" / "WIN_20260419_15_45_44_Pro_image_registration.json"
    output_json = ROOT / "底片&&json" / "Infrared_Cam1_1776670391100_height_regions_unmapped.json"
    direction = "test_to_ref"  # Use "test_to_ref" for the opposite direction.
    # ----------------------------------------------------------------------

    map_height_regions(
        height_regions_json=height_regions_json,
        image_registration_json=image_registration_json,
        output_json=output_json,
        direction=direction,
        strict_image_match=False,
    )
