from __future__ import annotations

import json
import math
from collections import deque
from pathlib import Path
from statistics import median
from typing import Any, Mapping


DEFAULT_TRAJECTORY_TUNING: dict[str, Any] = {
    # Spatial fusion is performed once by animal_box_calibration.  Keep the
    # bbox-size estimate as the primary depth source there, with calibrated
    # centre and floor measurements acting as corrections.
    "mouse_confidence": 0.45,
    "bbox_size_weight": 0.60,
    "volume_center_weight": 0.25,
    "bottom_grid_weight": 0.10,
    "bottom_grid_far_weight": 0.03,
    "center_disagreement_mm": 65.0,
    "bottom_acceptance_mm": 50.0,
    # Fitted from 59,669 valid observations in the two-day cage-2 run.
    # These three anchors reproduce the previous bbox-primary depth curve with
    # about 2.9 mm mean absolute error while retaining explicit non-linearity.
    "bbox_scale_near_px": 390.0,
    "bbox_scale_mid_px": 185.0,
    "bbox_scale_far_px": 85.0,
    "nominal_ground_center_height_mm": 28.0,
    "ground_center_acceptance_px": 60.0,
    # For a grounded mouse, the bbox bottom is a direct floor-position cue;
    # use it as the dominant Y source and keep bbox size only as a posture/
    # occlusion correction.  This is bypassed when the volume estimate
    # indicates climbing.
    "ground_y_anchor_weight": 0.80,
    "ground_y_anchor_max_volume_z_mm": 45.0,
    # The bottom-point floor KNN remains usable near the rear edge even when
    # the inverse homography extrapolates outside the four annotated corners.
    "ground_y_support_max_pixel_error_px": 80.0,
    "ground_y_anchor_ema_alpha": 0.75,
    # The inverse floor homography is allowed a small margin because bedding
    # and detection boxes can extend a few millimetres beyond the annotated
    # floor boundary.  A much larger excursion is evidence that bbox-bottom
    # is on a wall/occluder rather than on the floor.
    "ground_contact_margin_mm": 20.0,
    "wall_contact_margin_mm": 5.0,
    # The bbox bottom/ground homography is most reliable at the camera-facing
    # edge, where the projected grid is widest.  Use it as a contact anchor
    # only in that narrow strip; it remains low-weight deeper in the cage and
    # is disabled for unsupported wall-climbing observations.
    "front_contact_threshold_mm": 35.0,
    # X has a wider, separately validated camera-facing band than floor
    # contact/Y.  In this band bbox-bottom homography is still monotonic even
    # when the animal is standing or changing posture; 35 mm is too narrow
    # and lets the non-monotonic volume KNN decide the lateral coordinate.
    "front_x_anchor_threshold_mm": 90.0,
    # If the bottom-edge projection exceeds the physical cage width by more
    # than this tolerance, it is no longer a trustworthy lateral contact
    # point.  Use the interior centre estimate instead of saturating at
    # +/-90 mm, which creates a false wall-following plateau.
    "front_x_boundary_overrun_mm": 5.0,
    # Front-edge correction is opt-in.  A value of 1.0 preserves the calibrated
    # point exactly; lower Y retention blends towards bbox-bottom floor Y, and
    # X spread values above 1.0 expand the signed lateral coordinate.
    "front_y_scale_factor": 1.0,
    # Front lateral stretching is intentionally disabled. Front X is
    # anchored to validated bbox-bottom floor contact in the calibration
    # solver; multiplying it would recreate the side-expansion error.
    "front_x_spread_factor": 1.0,
    "front_contact_ema_alpha": 0.55,
    "pose_center_motion_px": 10.0,
    "pose_log_shape_threshold": 0.10,
    "position_prior_base_mm": 25.0,
    "position_prior_mm_per_px": 2.0,
    # After a long no-detection gap, a nearby image centre with a changed
    # bbox shape is usually re-acquisition/posture change, not a depth jump.
    # This only seeds the first observation; a genuinely displaced centre is
    # left to the normal spatial solver.
    "reacquire_center_match_px": 35.0,
    "reacquire_prior_alpha": 0.20,
    "climb_enter_height_mm": 50.0,
    "climb_exit_height_mm": 40.0,
    "climb_enter_frames": 2,
    "climb_exit_frames": 3,
    # Time-domain stabilization.  The real two-day run produced roughly one
    # valid observation per second, so a five-frame median caused excessive
    # lag and a 700 mm/s jump limit rejected almost nothing.
    "temporal_enabled": True,
    "median_window": 3,
    "ema_alpha_still": 0.12,
    "ema_alpha_moving": 0.40,
    "moving_speed_threshold_mm_s": 160.0,
    "max_speed_mm_s": 250.0,
    "jump_confirm_frames": 2,
    "height_ema_alpha": 0.25,
    "height_max_speed_mm_s": 300.0,
    # Matplotlib previews are generated asynchronously; three seconds keeps
    # tuning responsive without continuously occupying the plotting worker.
    "live_plot_interval_seconds": 3.0,
}


def _to_bool(value: Any, fallback: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return fallback
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on", "enabled"}:
        return True
    if text in {"0", "false", "no", "off", "disabled"}:
        return False
    return fallback


def _to_float(value: Any, fallback: float, low: float, high: float) -> float:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        parsed = float(fallback)
    if not math.isfinite(parsed):
        parsed = float(fallback)
    return max(low, min(high, parsed))


def _to_int(value: Any, fallback: int, low: int, high: int) -> int:
    try:
        parsed = int(round(float(value)))
    except (TypeError, ValueError):
        parsed = int(fallback)
    return max(low, min(high, parsed))


def normalize_trajectory_tuning(values: Mapping[str, Any] | None = None) -> dict[str, Any]:
    source = dict(DEFAULT_TRAJECTORY_TUNING)
    if values:
        source.update(dict(values))

    result = {
        "mouse_confidence": _to_float(source.get("mouse_confidence"), 0.50, 0.05, 0.95),
        "bbox_size_weight": _to_float(source.get("bbox_size_weight"), 0.60, 0.0, 10.0),
        "volume_center_weight": _to_float(source.get("volume_center_weight"), 0.25, 0.0, 10.0),
        "bottom_grid_weight": _to_float(source.get("bottom_grid_weight"), 0.15, 0.0, 10.0),
        "bottom_grid_far_weight": _to_float(source.get("bottom_grid_far_weight"), 0.05, 0.0, 10.0),
        "center_disagreement_mm": _to_float(source.get("center_disagreement_mm"), 120.0, 0.0, 360.0),
        "bottom_acceptance_mm": _to_float(source.get("bottom_acceptance_mm"), 80.0, 0.0, 360.0),
        "bbox_scale_near_px": _to_float(source.get("bbox_scale_near_px"), 390.0, 2.0, 2000.0),
        "bbox_scale_mid_px": _to_float(source.get("bbox_scale_mid_px"), 185.0, 1.0, 1999.0),
        "bbox_scale_far_px": _to_float(source.get("bbox_scale_far_px"), 85.0, 1.0, 1999.0),
        "nominal_ground_center_height_mm": _to_float(
            source.get("nominal_ground_center_height_mm"), 28.0, 5.0, 80.0
        ),
        "ground_center_acceptance_px": _to_float(
            source.get("ground_center_acceptance_px"), 60.0, 5.0, 300.0
        ),
        "ground_y_anchor_weight": _to_float(
            source.get("ground_y_anchor_weight"), 0.80, 0.0, 1.0
        ),
        "ground_y_anchor_max_volume_z_mm": _to_float(
            source.get("ground_y_anchor_max_volume_z_mm"), 45.0, 10.0, 120.0
        ),
        "ground_y_support_max_pixel_error_px": _to_float(
            source.get("ground_y_support_max_pixel_error_px"), 80.0, 10.0, 180.0
        ),
        "ground_y_anchor_ema_alpha": _to_float(
            source.get("ground_y_anchor_ema_alpha"), 0.75, 0.20, 1.0
        ),
        "ground_contact_margin_mm": _to_float(
            source.get("ground_contact_margin_mm"), 20.0, 0.0, 80.0
        ),
        "wall_contact_margin_mm": _to_float(
            source.get("wall_contact_margin_mm"), 5.0, 0.0, 30.0
        ),
        "front_contact_threshold_mm": _to_float(
            source.get("front_contact_threshold_mm"), 35.0, 0.0, 120.0
        ),
        "front_x_anchor_threshold_mm": _to_float(
            source.get("front_x_anchor_threshold_mm"), 90.0, 35.0, 150.0
        ),
        "front_x_boundary_overrun_mm": _to_float(
            source.get("front_x_boundary_overrun_mm"), 5.0, 0.0, 30.0
        ),
        "front_y_scale_factor": _to_float(
            source.get("front_y_scale_factor"), 1.0, 0.0, 1.0
        ),
        "front_x_spread_factor": _to_float(
            source.get("front_x_spread_factor"), 1.0, 1.0, 1.0
        ),
        "front_contact_ema_alpha": _to_float(
            source.get("front_contact_ema_alpha"), 0.55, 0.01, 1.0
        ),
        "pose_center_motion_px": _to_float(source.get("pose_center_motion_px"), 10.0, 1.0, 100.0),
        "pose_log_shape_threshold": _to_float(
            source.get("pose_log_shape_threshold"), 0.10, 0.01, 1.0
        ),
        "position_prior_base_mm": _to_float(
            source.get("position_prior_base_mm"), 25.0, 1.0, 180.0
        ),
        "position_prior_mm_per_px": _to_float(
            source.get("position_prior_mm_per_px"), 2.0, 0.0, 20.0
        ),
        "reacquire_center_match_px": _to_float(
            source.get("reacquire_center_match_px"), 35.0, 5.0, 100.0
        ),
        "reacquire_prior_alpha": _to_float(
            source.get("reacquire_prior_alpha"), 0.20, 0.0, 1.0
        ),
        "climb_enter_height_mm": _to_float(
            source.get("climb_enter_height_mm"), 50.0, 10.0, 140.0
        ),
        "climb_exit_height_mm": _to_float(
            source.get("climb_exit_height_mm"), 40.0, 0.0, 130.0
        ),
        "climb_enter_frames": _to_int(source.get("climb_enter_frames"), 2, 1, 10),
        "climb_exit_frames": _to_int(source.get("climb_exit_frames"), 3, 1, 15),
        "temporal_enabled": _to_bool(source.get("temporal_enabled"), True),
        "median_window": _to_int(source.get("median_window"), 5, 1, 15),
        "ema_alpha_still": _to_float(source.get("ema_alpha_still"), 0.20, 0.01, 1.0),
        "ema_alpha_moving": _to_float(source.get("ema_alpha_moving"), 0.50, 0.01, 1.0),
        "moving_speed_threshold_mm_s": _to_float(
            source.get("moving_speed_threshold_mm_s"), 120.0, 0.0, 5000.0
        ),
        "max_speed_mm_s": _to_float(source.get("max_speed_mm_s"), 700.0, 1.0, 5000.0),
        "jump_confirm_frames": _to_int(source.get("jump_confirm_frames"), 2, 1, 10),
        "height_ema_alpha": _to_float(source.get("height_ema_alpha"), 0.30, 0.01, 1.0),
        "height_max_speed_mm_s": _to_float(
            source.get("height_max_speed_mm_s"), 500.0, 1.0, 5000.0
        ),
        "live_plot_interval_seconds": _to_float(
            source.get("live_plot_interval_seconds"), 3.0, 0.5, 60.0
        ),
    }

    # A median window should be odd; this keeps the selected sample centered.
    if result["median_window"] > 1 and result["median_window"] % 2 == 0:
        result["median_window"] += 1
    result["median_window"] = min(result["median_window"], 15)

    if result["bbox_scale_far_px"] >= result["bbox_scale_near_px"]:
        result["bbox_scale_far_px"] = max(1.0, result["bbox_scale_near_px"] - 2.0)
    result["bbox_scale_mid_px"] = max(
        result["bbox_scale_far_px"] + 1.0,
        min(result["bbox_scale_near_px"] - 1.0, result["bbox_scale_mid_px"]),
    )
    if result["climb_exit_height_mm"] >= result["climb_enter_height_mm"]:
        result["climb_exit_height_mm"] = max(0.0, result["climb_enter_height_mm"] - 5.0)

    if (
        result["bbox_size_weight"]
        + result["volume_center_weight"]
        + result["bottom_grid_weight"]
        <= 0.0
    ):
        result["bbox_size_weight"] = DEFAULT_TRAJECTORY_TUNING["bbox_size_weight"]

    return result


def load_trajectory_tuning(path: Path) -> dict[str, Any]:
    if not path.exists():
        return normalize_trajectory_tuning()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return normalize_trajectory_tuning()
    if not isinstance(payload, dict):
        return normalize_trajectory_tuning()
    return normalize_trajectory_tuning(payload)


def save_trajectory_tuning(path: Path, values: Mapping[str, Any]) -> dict[str, Any]:
    normalized = normalize_trajectory_tuning(values)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)
    return normalized


class TrajectoryTemporalStabilizer:
    """Causal median + adaptive EMA stabilizer for one cage."""

    def __init__(self) -> None:
        self.history: deque[tuple[float, float, float]] = deque(maxlen=31)
        self.previous: tuple[float, float, float] | None = None
        self.previous_observation: tuple[float, float, float, float] | None = None
        self.previous_observation_timestamp: float | None = None
        self.last_timestamp: float | None = None
        self.pending_jump: tuple[float, float, float] | None = None
        self.pending_jump_count = 0
        self.climbing = False
        self.climb_enter_count = 0
        self.climb_exit_count = 0
        self.previous_front_contact = False
        self.reacquire_prior: tuple[float, float, float] | None = None
        self.reacquire_observation: tuple[float, float, float, float] | None = None

    def reset(self) -> None:
        self.history.clear()
        self.previous = None
        self.previous_observation = None
        self.previous_observation_timestamp = None
        self.last_timestamp = None
        self.pending_jump = None
        self.pending_jump_count = 0
        self.climbing = False
        self.climb_enter_count = 0
        self.climb_exit_count = 0
        self.previous_front_contact = False
        self.reacquire_prior = None
        self.reacquire_observation = None

    @staticmethod
    def _clamp_xyz(x: float, y: float, z: float) -> tuple[float, float, float]:
        return (
            max(-90.0, min(90.0, x)),
            max(0.0, min(360.0, y)),
            max(0.0, min(150.0, z)),
        )

    @staticmethod
    def _finite_float(value: Any) -> float | None:
        try:
            parsed = float(value)
        except (TypeError, ValueError):
            return None
        return parsed if math.isfinite(parsed) else None

    @staticmethod
    def _weighted_mean(values: list[tuple[float | None, float]]) -> tuple[float | None, float]:
        valid = [(float(value), max(0.0, float(weight))) for value, weight in values if value is not None and weight > 0.0]
        total = sum(weight for _value, weight in valid)
        if total <= 0.0:
            return None, 0.0
        return sum(value * weight for value, weight in valid) / total, total

    def apply(self, row: dict[str, Any], values: Mapping[str, Any]) -> None:
        if row.get("status") != "ok" or row.get("X") is None or row.get("Y") is None:
            return

        settings = normalize_trajectory_tuning(values)
        raw_x = float(row["X"])
        raw_y = float(row["Y"])
        raw_z = float(row.get("Z") or 0.0)
        raw_x, raw_y, raw_z = self._clamp_xyz(raw_x, raw_y, raw_z)
        row.setdefault("rawX", raw_x)
        row.setdefault("rawY", raw_y)
        row.setdefault("rawZ", raw_z)

        timestamp = float(row.get("timestamp", 0.0) or 0.0)
        if timestamp <= 0.0:
            timestamp = (self.last_timestamp or 0.0) + 0.1
        observation_gap = (
            timestamp - self.previous_observation_timestamp
            if self.previous_observation_timestamp is not None
            else None
        )
        if self.last_timestamp is not None and timestamp - self.last_timestamp > 5.0:
            # A long no-detection interval is a real trajectory gap.  Reacquire
            # from geometry instead of dragging the old state across it.
            if self.previous is not None:
                self.reacquire_prior = self.previous
                self.reacquire_observation = self.previous_observation
            self.history.clear()
            self.previous = None
            self.pending_jump = None
            self.pending_jump_count = 0
            self.previous_observation = None
            self.previous_observation_timestamp = None
            self.climbing = False
            self.climb_enter_count = 0
            self.climb_exit_count = 0
            self.previous_front_contact = False

        center_x = self._finite_float(row.get("mappedCenterX"))
        center_y = self._finite_float(row.get("mappedCenterY"))
        bbox_width = self._finite_float(row.get("mouseBoxWidth"))
        bbox_height = self._finite_float(row.get("mouseBoxHeight"))
        reacquire_prior_used = False
        if (
            self.previous is None
            and self.reacquire_prior is not None
            and self.reacquire_observation is not None
            and center_x is not None
            and center_y is not None
        ):
            prior_cx, prior_cy, _prior_w, _prior_h = self.reacquire_observation
            reacquire_motion = math.hypot(center_x - prior_cx, center_y - prior_cy)
            if reacquire_motion <= float(settings["reacquire_center_match_px"]):
                prior_x, prior_y, _prior_z = self.reacquire_prior
                prior_alpha = float(settings["reacquire_prior_alpha"])
                raw_x = prior_x + prior_alpha * (raw_x - prior_x)
                raw_y = prior_y + prior_alpha * (raw_y - prior_y)
                reacquire_prior_used = True
            self.reacquire_prior = None
            self.reacquire_observation = None
        row["reacquirePriorUsed"] = reacquire_prior_used
        center_motion = None
        log_geometry_change = 0.0
        log_aspect_change = 0.0
        if (
            self.previous_observation is not None
            and center_x is not None
            and center_y is not None
            and bbox_width is not None
            and bbox_height is not None
            and bbox_width > 0.0
            and bbox_height > 0.0
        ):
            prev_cx, prev_cy, prev_w, prev_h = self.previous_observation
            center_motion = math.hypot(center_x - prev_cx, center_y - prev_cy)
            if prev_w > 0.0 and prev_h > 0.0:
                log_geometry_change = abs(0.5 * math.log((bbox_width * bbox_height) / (prev_w * prev_h)))
                log_aspect_change = abs(math.log((bbox_width / bbox_height) / (prev_w / prev_h)))

        pose_change = bool(
            center_motion is not None
            and (observation_gap is None or observation_gap <= 2.5)
            and center_motion <= float(settings["pose_center_motion_px"])
            and max(log_geometry_change, log_aspect_change)
            >= float(settings["pose_log_shape_threshold"])
        )
        row["bboxCenterMotionPx"] = center_motion
        row["bboxLogGeometryChange"] = log_geometry_change
        row["bboxLogAspectChange"] = log_aspect_change
        row["poseChangeDetected"] = pose_change

        geometry_y = self._finite_float(row.get("groundCenterY"))
        volume_x = self._finite_float(row.get("volumeCenterX"))
        volume_y = self._finite_float(row.get("volumeCenterY"))
        bbox_y = self._finite_float(row.get("runSizeDepthY"))

        # Prefer the unclamped inverse-homography result.  The legacy
        # bottomGrid fields were clamped to the cage and therefore made a
        # wall point look like a valid floor contact.
        bottom_x_raw = self._finite_float(
            row.get("bottomGridRawX", row.get("bottomGridX"))
        )
        bottom_y_raw = self._finite_float(
            row.get("bottomGridRawY", row.get("bottomGridY"))
        )
        contact_margin = float(settings["ground_contact_margin_mm"])
        ground_contact_valid = bool(
            bottom_x_raw is not None
            and bottom_y_raw is not None
            and -90.0 - contact_margin <= bottom_x_raw <= 90.0 + contact_margin
            and -contact_margin <= bottom_y_raw <= 360.0 + contact_margin
        )
        bottom_x = (
            max(-90.0, min(90.0, float(bottom_x_raw)))
            if ground_contact_valid and bottom_x_raw is not None
            else None
        )
        bottom_y = (
            max(0.0, min(360.0, float(bottom_y_raw)))
            if ground_contact_valid and bottom_y_raw is not None
            else None
        )
        row["groundContactValid"] = ground_contact_valid
        row["bottomGridRawX"] = bottom_x_raw
        row["bottomGridRawY"] = bottom_y_raw
        footprint_left_raw = self._finite_float(row.get("groundFootprintRawLeftX"))
        footprint_right_raw = self._finite_float(row.get("groundFootprintRawRightX"))
        footprint_min_x = None
        footprint_max_x = None
        footprint_center_x = None
        if (
            ground_contact_valid
            and footprint_left_raw is not None
            and footprint_right_raw is not None
        ):
            footprint_min_x = max(
                -90.0, min(90.0, min(footprint_left_raw, footprint_right_raw))
            )
            footprint_max_x = max(
                -90.0, min(90.0, max(footprint_left_raw, footprint_right_raw))
            )
            footprint_center_x = (footprint_min_x + footprint_max_x) / 2.0
        wall_margin = float(settings["wall_contact_margin_mm"])
        row["footprintMinX"] = footprint_min_x
        row["footprintMaxX"] = footprint_max_x
        row["footprintCenterX"] = footprint_center_x
        row["touchesLeftWall"] = bool(
            footprint_left_raw is not None
            and footprint_right_raw is not None
            and min(footprint_left_raw, footprint_right_raw) <= -90.0 + wall_margin
        )
        row["touchesRightWall"] = bool(
            footprint_left_raw is not None
            and footprint_right_raw is not None
            and max(footprint_left_raw, footprint_right_raw) >= 90.0 - wall_margin
        )

        # Height evidence combines the unconstrained volume lookup and the
        # camera projection evaluated at the previous stable depth.  A valid
        # floor contact overrides a tall body box: standing/rearing changes
        # posture, not the animal's elevation above the floor.
        volume_z = self._finite_float(row.get("volumeCenterZ"))
        height_intercept = self._finite_float(row.get("heightProjectionIntercept"))
        height_slope = self._finite_float(row.get("heightProjectionSlope"))
        height_y = self.previous[1] if self.previous is not None else raw_y
        projected_z = (
            height_intercept + height_slope * height_y
            if height_intercept is not None and height_slope is not None
            else raw_z
        )
        projected_z = max(0.0, min(150.0, projected_z))
        height_evidence = max(raw_z, projected_z, volume_z or 0.0)
        if ground_contact_valid:
            self.climbing = False
            self.climb_enter_count = 0
            self.climb_exit_count = 0
        elif height_evidence >= float(settings["climb_enter_height_mm"]):
            self.climb_enter_count += 1
            self.climb_exit_count = 0
            if (
                self.climb_enter_count >= int(settings["climb_enter_frames"])
                or height_evidence >= float(settings["climb_enter_height_mm"]) + 25.0
            ):
                self.climbing = True
        elif height_evidence <= float(settings["climb_exit_height_mm"]):
            self.climb_exit_count += 1
            self.climb_enter_count = 0
            if self.climb_exit_count >= int(settings["climb_exit_frames"]):
                self.climbing = False
        else:
            self.climb_enter_count = 0
            self.climb_exit_count = 0
        row["climbingMode"] = self.climbing
        row["groundedMode"] = not self.climbing
        row["heightEvidenceMm"] = height_evidence
        front_contact = bool(
            ground_contact_valid
            and bottom_x is not None
            and bottom_y is not None
            and bottom_y <= float(settings["front_contact_threshold_mm"])
        )
        ground_y_anchor_used = bool(row.get("groundYAnchorUsed", False))
        ground_y_anchor_source = str(row.get("groundYAnchorSource") or "")
        ground_y_anchor_follow = bool(
            ground_y_anchor_used
            and ground_y_anchor_source in {"bottom_label1_knn", "bottom_homography"}
            and not self.climbing
            and not pose_change
        )
        row["groundYAnchorFollow"] = ground_y_anchor_follow
        front_contact_entered = bool(front_contact and not self.previous_front_contact)
        row["frontContactDetected"] = front_contact
        row["frontContactEntered"] = front_contact_entered
        # This is intentionally wider than floor contact.  It is only a
        # lateral-X cue; Y and height keep using the strict floor-contact
        # state above.  The calibration solver provides this flag after
        # validating the raw inverse-homography result against cage bounds.
        front_x_anchor = bool(
            row.get("frontXGroundAnchorUsed", False)
            and ground_contact_valid
            and bottom_x is not None
            and bottom_y is not None
            and bottom_y <= float(settings["front_x_anchor_threshold_mm"])
        )
        row["frontXAnchorDetected"] = front_x_anchor
        # animal_box_calibration has already fused bbox scale, calibrated
        # centre geometry and the low-weight floor cue.  Repeating that fusion
        # here changes both its weights and its meaning, and was the cause of
        # the comparison trajectory drifting away from the production result.
        # Normal ground motion therefore starts from the solver's final X/Y.
        measured_x = raw_x
        measured_y = raw_y
        spatial_measurement_source = "calibrated_fusion"
        if self.climbing:
            # Bbox scale and floor contact are not valid depth cues on a wall.
            # Only this state is allowed to replace the calibrated floor X/Y.
            if volume_x is not None:
                measured_x = volume_x
            if volume_y is not None:
                measured_y = volume_y
            spatial_measurement_source = "climb_volume"
        elif front_contact and bottom_y is not None:
            # Default retention is 1.0, i.e. no front-edge rewrite.  This keeps
            # the physically calibrated point used for distance and speed.
            # Operators may deliberately blend towards floor contact during a
            # calibration test without introducing an unconditional 0.25x Y.
            front_y_retention = float(settings["front_y_scale_factor"])
            measured_y = (
                raw_y * front_y_retention
                + bottom_y * (1.0 - front_y_retention)
            )
            spatial_measurement_source = "calibrated_front"

        # The footprint is diagnostic only (occupied span / wall contact).  It
        # is never used as the trajectory point because posture changes its
        # centre and width. Front X stretching is disabled in this version.
        x_contact_weight = 0.0
        front_x_stretch_factor = 1.0
        if front_contact and bottom_y is not None:
            front_threshold = max(
                1.0, float(settings["front_contact_threshold_mm"])
            )
            front_ratio = max(0.0, min(1.0, bottom_y / front_threshold))
            front_x_stretch_factor = 1.0 + (
                float(settings["front_x_spread_factor"]) - 1.0
            ) * (1.0 - front_ratio * front_ratio)
            measured_x *= front_x_stretch_factor

        # A small image-space motion cannot represent a full-cage depth jump.
        # The allowed state step expands with actual centre-pixel movement.
        has_spatial_sources = any(
            value is not None for value in (geometry_y, volume_y, bbox_y, bottom_y)
        )
        if (
            self.previous is not None
            and not front_contact
            and (center_motion is not None or has_spatial_sources)
        ):
            previous_x, previous_y, previous_z = self.previous
            motion_px = max(0.0, center_motion or 0.0)
            allowed_y_step = float(settings["position_prior_base_mm"]) + motion_px * float(
                settings["position_prior_mm_per_px"]
            )
            if pose_change:
                # Curling/extension with an almost fixed image centre is not a
                # full-cage translation.  Permit only the displacement implied
                # by centre-pixel motion, with a small floor for detector noise.
                allowed_y_step = min(
                    allowed_y_step,
                    max(
                        5.0,
                        motion_px * float(settings["position_prior_mm_per_px"]),
                    ),
                )
            if not ground_y_anchor_follow:
                measured_y = max(
                    previous_y - allowed_y_step,
                    min(previous_y + allowed_y_step, measured_y),
                )
            # A validated front-X anchor is allowed to correct the old KNN
            # lateral cell directly.  Clamping it to the previous point was
            # the reason a right-to-middle movement could remain on the wrong
            # side for several frames.  Y still receives the position prior.
            if not front_x_anchor:
                allowed_x_step = max(12.0, 12.0 + motion_px * 1.5)
                measured_x = max(
                    previous_x - allowed_x_step,
                    min(previous_x + allowed_x_step, measured_x),
                )

        projected_z = (
            height_intercept + height_slope * measured_y
            if height_intercept is not None and height_slope is not None
            else projected_z
        )
        projected_z = max(0.0, min(150.0, projected_z))
        if self.climbing:
            measured_z, _z_weight = self._weighted_mean(
                [
                    (projected_z, 0.60),
                    (volume_z, 0.40),
                ]
            )
            if measured_z is None:
                measured_z = raw_z
        else:
            # Z is the animal's elevation state, not bbox/body-centre height.
            # Standing, stretching and curling on the same floor therefore
            # share one calibrated ground height.
            measured_z = float(settings["nominal_ground_center_height_mm"])

        raw_x, raw_y, raw_z = self._clamp_xyz(measured_x, measured_y, measured_z)
        row["measurementX"] = raw_x
        row["measurementY"] = raw_y
        row["measurementZ"] = raw_z
        row["projectedHeightAtMeasurement"] = projected_z
        row["spatialMeasurementSource"] = spatial_measurement_source
        row["dynamicFusionTotalWeight"] = 1.0
        row["dynamicBBoxWeight"] = self._finite_float(row.get("fusionBBoxWeight"))
        row["dynamicCenterWeight"] = self._finite_float(row.get("fusionCenterWeight"))
        row["dynamicBottomWeight"] = self._finite_float(row.get("fusionBottomWeight"))
        row["groundContactXWeight"] = x_contact_weight
        row["frontYScaleFactor"] = (
            float(settings["front_y_scale_factor"]) if front_contact else 1.0
        )
        row["frontXStretchFactor"] = front_x_stretch_factor
        row["dynamicPriorWeight"] = 0.0
        row["dynamicFrontContactWeight"] = (
            1.0 - float(settings["front_y_scale_factor"]) if front_contact else 0.0
        )

        if not settings["temporal_enabled"]:
            # Turning off filtering exposes the solver's single spatial
            # measurement (plus only explicitly enabled front corrections).
            row["X"], row["Y"], row["Z"] = raw_x, raw_y, raw_z
            row["trajectorySmoothed"] = False
            row["trajectoryJumpRejected"] = False
            row["trajectoryFilterAlpha"] = 1.0
            row["trajectoryHeightAlpha"] = 1.0
            row["trajectoryFilterDt"] = None
            self.history.append((raw_x, raw_y, raw_z))
            self.previous = (raw_x, raw_y, raw_z)
            self.last_timestamp = timestamp
            self.pending_jump = None
            self.pending_jump_count = 0
            if None not in (center_x, center_y, bbox_width, bbox_height):
                self.previous_observation = (
                    float(center_x), float(center_y), float(bbox_width), float(bbox_height)
                )
                self.previous_observation_timestamp = timestamp
            self.previous_front_contact = front_contact
            return

        self.history.append((raw_x, raw_y, raw_z))
        window = min(int(settings["median_window"]), len(self.history))
        recent = list(self.history)[-window:]
        median_x = float(median(point[0] for point in recent))
        median_y = float(median(point[1] for point in recent))
        median_z = float(median(point[2] for point in recent))
        if not self.climbing:
            median_z = float(settings["nominal_ground_center_height_mm"])
        if (
            front_contact
            and center_motion is not None
            and center_motion > float(settings["pose_center_motion_px"])
        ):
            # A large centre displacement with a valid front-edge contact is
            # real locomotion, not bbox shape noise.  Do not let the causal
            # three-frame median hold it at the previous side of the cage.
            median_x = raw_x
            # Keep the corrected front-floor Y.  The bbox/center Y is the
            # source that caused the observed rearward front-point drift.
            median_y = measured_y
        row["medianX"] = median_x
        row["medianY"] = median_y
        row["medianZ"] = median_z

        if self.previous is None:
            filtered = self._clamp_xyz(median_x, median_y, median_z)
            self.previous = filtered
            self.last_timestamp = timestamp
            row["X"], row["Y"], row["Z"] = filtered
            row["trajectorySmoothed"] = window > 1
            row["trajectoryJumpRejected"] = False
            row["trajectoryFilterAlpha"] = 1.0
            row["trajectoryFilterDt"] = None
            if None not in (center_x, center_y, bbox_width, bbox_height):
                self.previous_observation = (
                    float(center_x), float(center_y), float(bbox_width), float(bbox_height)
                )
                self.previous_observation_timestamp = timestamp
            self.previous_front_contact = front_contact
            return

        dt = max(0.02, min(1.0, timestamp - float(self.last_timestamp or timestamp - 0.1)))
        previous_x, previous_y, previous_z = self.previous
        planar_step = math.hypot(median_x - previous_x, median_y - previous_y)
        planar_speed = planar_step / dt
        max_step = float(settings["max_speed_mm_s"]) * dt
        jump_rejected = False

        if (
            planar_step > max_step
            and int(settings["jump_confirm_frames"]) > 1
            and not front_contact
        ):
            pending = self.pending_jump
            consistency_radius = max(20.0, max_step * 0.75)
            if pending is None or math.hypot(median_x - pending[0], median_y - pending[1]) > consistency_radius:
                self.pending_jump = (median_x, median_y, median_z)
                self.pending_jump_count = 1
            else:
                self.pending_jump = (median_x, median_y, median_z)
                self.pending_jump_count += 1

            if self.pending_jump_count < int(settings["jump_confirm_frames"]):
                median_x, median_y, median_z = previous_x, previous_y, previous_z
                planar_speed = 0.0
                jump_rejected = True
            else:
                self.pending_jump = None
                self.pending_jump_count = 0
        else:
            self.pending_jump = None
            self.pending_jump_count = 0

        moving = planar_speed >= float(settings["moving_speed_threshold_mm_s"])
        alpha = float(settings["ema_alpha_moving"] if moving else settings["ema_alpha_still"])
        if front_contact and not jump_rejected:
            front_lateral_motion = bool(
                center_motion is not None
                and center_motion > float(settings["pose_center_motion_px"])
            )
            if front_lateral_motion:
                # The front strip is the widest and best-conditioned part of
                # the projection.  A confirmed large image displacement should
                # reach its calibrated X immediately instead of retaining an
                # EMA lag that looks like a lateral positioning error.
                alpha = 1.0
            else:
                alpha = max(alpha, float(settings["front_contact_ema_alpha"]))
            # Entering the first half of the front strip is a strong physical
            # boundary observation.  Snap once on entry so a stale far/middle
            # state cannot leave the point tens of millimetres behind the
            # camera-facing cage edge.  Subsequent samples remain EMA-filtered.
            if (
                front_contact_entered
                and bottom_y is not None
                and bottom_y <= float(settings["front_contact_threshold_mm"]) * 0.5
            ):
                alpha = 1.0
        if jump_rejected:
            alpha = 0.0

        alpha_x = alpha
        alpha_y = alpha
        if ground_y_anchor_follow and not jump_rejected and not front_contact:
            # Do not let a pure depth transition change the lateral smoothing
            # rate.  Otherwise the new, reliable Y anchor would make X look
            # more mobile even though the mouse stayed on the same side.
            x_motion_speed = abs(median_x - previous_x) / dt
            alpha_x = float(
                settings[
                    "ema_alpha_moving"
                    if x_motion_speed >= float(settings["moving_speed_threshold_mm_s"])
                    else "ema_alpha_still"
                ]
            )
        if ground_y_anchor_follow and not jump_rejected and moving:
            # A validated floor-grid Y is already a metric position cue across
            # the full cage.  Keep the anti-jitter median, but follow a real
            # depth transition faster so the trajectory does not remain tens
            # of millimetres in front of the mouse for the whole report.
            alpha_y = max(alpha_y, float(settings["ground_y_anchor_ema_alpha"]))

        filtered_x = previous_x + alpha_x * (median_x - previous_x)
        filtered_y = previous_y + alpha_y * (median_y - previous_y)

        if self.climbing:
            max_height_step = float(settings["height_max_speed_mm_s"]) * dt
            height_delta = max(-max_height_step, min(max_height_step, median_z - previous_z))
            height_alpha = 0.0 if jump_rejected else float(settings["height_ema_alpha"])
            filtered_z = previous_z + height_alpha * height_delta
        else:
            height_alpha = 1.0
            filtered_z = float(settings["nominal_ground_center_height_mm"])
        filtered = self._clamp_xyz(filtered_x, filtered_y, filtered_z)

        row["X"], row["Y"], row["Z"] = filtered
        row["trajectorySmoothed"] = True
        row["trajectoryJumpRejected"] = jump_rejected
        row["trajectoryFilterAlpha"] = alpha
        row["trajectoryFilterAlphaX"] = alpha_x
        row["trajectoryFilterAlphaY"] = alpha_y
        row["trajectoryHeightAlpha"] = height_alpha
        row["trajectoryFilterDt"] = dt
        row["trajectoryPlanarSpeed"] = planar_speed
        self.previous = filtered
        self.last_timestamp = timestamp
        if None not in (center_x, center_y, bbox_width, bbox_height):
            self.previous_observation = (
                float(center_x), float(center_y), float(bbox_width), float(bbox_height)
            )
            self.previous_observation_timestamp = timestamp
        self.previous_front_contact = front_contact

# Compatibility layer for the existing monitor-camera tuning entry point.
# The trajectory algorithm above remains the source of truth; this adapter only
# exposes the legacy names expected by the current UI.
DEFAULT_TUNING_PATH = Path(__file__).resolve().parents[2] / "config" / "mouse_trajectory_tuning.json"


class TrajectoryTuningStore:
    def __init__(self, path: Path = DEFAULT_TUNING_PATH) -> None:
        self.path = Path(path)
        self._settings = load_trajectory_tuning(self.path)

    def snapshot(self) -> dict[str, Any]:
        return dict(self._settings)

    def update(self, values: Mapping[str, Any] | None = None, *, save: bool = True) -> dict[str, Any]:
        self._settings = normalize_trajectory_tuning({**self._settings, **dict(values or {})})
        if save:
            self._settings = save_trajectory_tuning(self.path, self._settings)
        return dict(self._settings)

    def restore_recommended(self, *, save: bool = False) -> dict[str, Any]:
        return self.update(DEFAULT_TRAJECTORY_TUNING, save=save)


trajectory_tuning_store = TrajectoryTuningStore()


try:
    from PyQt6.QtCore import pyqtSignal
    from PyQt6.QtWidgets import (
        QCheckBox,
        QDialog,
        QDialogButtonBox,
        QDoubleSpinBox,
        QFormLayout,
        QLabel,
        QSpinBox,
        QVBoxLayout,
    )
except ImportError:
    class TrajectoryTuningDialog:  # type: ignore[no-redef]
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("PyQt6 is required to open trajectory tuning dialog")
else:
    class TrajectoryTuningDialog(QDialog):
        """Small adapter dialog for the existing trajectory tuning button."""

        settings_applied = pyqtSignal(dict)
        _FIELDS = (
            ("检测置信度", "mouse_confidence", 0.0, 1.0, 0.01, 3),
            ("框尺寸权重", "bbox_size_weight", 0.0, 10.0, 0.05, 3),
            ("体积中心权重", "volume_center_weight", 0.0, 10.0, 0.05, 3),
            ("底部网格权重", "bottom_grid_weight", 0.0, 10.0, 0.05, 3),
            ("地面锚点权重", "ground_y_anchor_weight", 0.0, 1.0, 0.01, 3),
            ("静止 EMA", "ema_alpha_still", 0.0, 1.0, 0.01, 3),
            ("运动 EMA", "ema_alpha_moving", 0.0, 1.0, 0.01, 3),
            ("最大速度", "max_speed_mm_s", 1.0, 2000.0, 1.0, 1),
            ("高度 EMA", "height_ema_alpha", 0.0, 1.0, 0.01, 3),
            ("实时绘图间隔", "live_plot_interval_seconds", 0.1, 60.0, 0.1, 1),
        )

        def __init__(self, parent: Any = None, store: TrajectoryTuningStore = trajectory_tuning_store) -> None:
            super().__init__(parent)
            self.store = store
            self.controls: dict[str, Any] = {}
            self.setWindowTitle("轨迹实时调参")
            self.setMinimumWidth(560)
            layout = QVBoxLayout(self)
            summary = QLabel("修改参数后点击应用，实时轨迹线程会使用新的轨迹算法参数。", self)
            summary.setWordWrap(True)
            layout.addWidget(summary)
            form = QFormLayout()
            for label, key, minimum, maximum, step, decimals in self._FIELDS:
                control = QDoubleSpinBox(self)
                control.setRange(minimum, maximum)
                control.setSingleStep(step)
                control.setDecimals(decimals)
                self.controls[key] = control
                form.addRow(label, control)
            self.temporal_enabled = QCheckBox("启用时序稳定", self)
            self.controls["temporal_enabled"] = self.temporal_enabled
            layout.addLayout(form)
            layout.addWidget(self.temporal_enabled)
            self.diagnostics = QLabel("暂无轨迹诊断数据", self)
            self.diagnostics.setWordWrap(True)
            layout.addWidget(self.diagnostics)
            buttons = QDialogButtonBox(
                QDialogButtonBox.StandardButton.Apply
                | QDialogButtonBox.StandardButton.RestoreDefaults
                | QDialogButtonBox.StandardButton.Close,
                parent=self,
            )
            buttons.button(QDialogButtonBox.StandardButton.Apply).clicked.connect(self._apply)
            buttons.button(QDialogButtonBox.StandardButton.RestoreDefaults).clicked.connect(self._restore)
            buttons.rejected.connect(self.close)
            layout.addWidget(buttons)
            self._load_values(self.store.snapshot())

        def _load_values(self, values: Mapping[str, Any]) -> None:
            for _label, key, *_rest in self._FIELDS:
                control = self.controls[key]
                control.setValue(float(values.get(key, DEFAULT_TRAJECTORY_TUNING[key])))
            self.temporal_enabled.setChecked(bool(values.get("temporal_enabled", True)))

        def _collect_values(self) -> dict[str, Any]:
            values = {key: self.controls[key].value() for _label, key, *_rest in self._FIELDS}
            values["temporal_enabled"] = self.temporal_enabled.isChecked()
            return values

        def _apply(self) -> None:
            settings = self.store.update(self._collect_values(), save=True)
            self.settings_applied.emit(settings)

        def _restore(self) -> None:
            settings = self.store.restore_recommended(save=False)
            self._load_values(settings)
            self.settings_applied.emit(settings)

        def set_diagnostics(self, cage_number: int | None, diagnostics: Mapping[str, Any] | None) -> None:
            if not diagnostics:
                self.diagnostics.setText(f"鼠笼{cage_number or '-'}：暂无轨迹诊断数据")
                return
            jump = bool(diagnostics.get("jumpRejected", False))
            speed = diagnostics.get("planarSpeed", diagnostics.get("trajectoryPlanarSpeed", "-"))
            self.diagnostics.setText(
                f"鼠笼{cage_number or '-'}：X={diagnostics.get('filteredX', diagnostics.get('X', '-'))}, "
                f"Y={diagnostics.get('filteredY', diagnostics.get('Y', '-'))}, "
                f"Z={diagnostics.get('filteredZ', diagnostics.get('Z', '-'))}；"
                f"平面速度={speed}；跳点拒绝={'是' if jump else '否'}"
            )