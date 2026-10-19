from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np
import torch
from torch import nn
from torchvision.models import resnet18


INPUT_WIDTH = 256
INPUT_HEIGHT = 144
SAM_CROP_SIZE = 96


class CageMouseMaskYOLO6ResNet18(nn.Module):
    """Two-mask + YOLO6 ResNet18 used by the updated E2 XY checkpoint."""

    def __init__(self):
        super().__init__()
        backbone = resnet18(weights=None)
        old_conv = backbone.conv1
        backbone.conv1 = nn.Conv2d(
            2,
            old_conv.out_channels,
            kernel_size=old_conv.kernel_size,
            stride=old_conv.stride,
            padding=old_conv.padding,
            bias=False,
        )
        self.backbone = nn.Sequential(*list(backbone.children())[:-1])
        self.aux = nn.Sequential(
            nn.Linear(6, 96),
            nn.SiLU(),
            nn.Dropout(0.10),
            nn.Linear(96, 64),
            nn.SiLU(),
        )
        self.head = nn.Sequential(
            nn.Linear(512 + 64, 192),
            nn.SiLU(),
            nn.Dropout(0.20),
            nn.Linear(192, 2),
        )

    def forward(self, masks: torch.Tensor, yolo6: torch.Tensor) -> torch.Tensor:
        if masks.ndim != 4 or masks.shape[1:] != (2, INPUT_HEIGHT, INPUT_WIDTH):
            raise ValueError(
                f"Expected masks [N,2,{INPUT_HEIGHT},{INPUT_WIDTH}], "
                f"got {tuple(masks.shape)}"
            )
        if yolo6.ndim != 2 or yolo6.shape[1] != 6:
            raise ValueError(f"Expected YOLO features [N,6], got {tuple(yolo6.shape)}")
        visual = self.backbone(masks).flatten(1)
        fused = torch.cat((visual, self.aux(yolo6)), dim=1)
        raw = self.head(fused)
        return torch.stack((torch.tanh(raw[:, 0]), torch.sigmoid(raw[:, 1])), dim=1)


def resolve_device(value: str) -> torch.device:
    requested = str(value or "auto").strip().lower()
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    if requested.startswith("cuda") and not torch.cuda.is_available():
        raise RuntimeError(f"CUDA device requested but unavailable: {requested}")
    return torch.device(requested)


def _load_mapping(path: Path) -> dict[str, str]:
    document = json.loads(path.read_text(encoding="utf-8"))
    raw_mapping = document.get("cage_key_by_cage") if isinstance(document, dict) else None
    if not isinstance(raw_mapping, dict):
        raise ValueError(
            "Cage mapping must be a JSON object named 'cage_key_by_cage'"
        )
    mapping: dict[str, str] = {}
    for cage_number, key in raw_mapping.items():
        normalized_key = str(key or "").strip()
        if normalized_key:
            mapping[str(cage_number).strip()] = normalized_key
    return mapping


def _load_calibrations(path: Path) -> dict[str, dict[str, Any]]:
    document = json.loads(path.read_text(encoding="utf-8"))
    calibrations = document.get("calibrations") if isinstance(document, dict) else None
    if not isinstance(calibrations, dict) or not calibrations:
        raise ValueError(f"No cage calibrations found in {path}")
    return calibrations


def _scaled_cage_quad(
    calibration: dict[str, Any],
    frame_width: int,
    frame_height: int,
    cage_key: str,
) -> np.ndarray:
    quad = np.asarray(calibration["aligned_cage_quad_px"], dtype=np.float32).reshape(4, 2)
    calibrated_width, calibrated_height = map(int, calibration["image_size_px"])
    if calibrated_width < 2 or calibrated_height < 2 or not np.isfinite(quad).all():
        raise ValueError(f"Invalid cage calibration for {cage_key}")
    calibrated_ratio = calibrated_width / calibrated_height
    frame_ratio = frame_width / frame_height
    if abs(frame_ratio / calibrated_ratio - 1.0) > 0.01:
        raise ValueError(
            f"Frame aspect ratio {frame_width}x{frame_height} does not match "
            f"cage calibration {cage_key} ({calibrated_width}x{calibrated_height})"
        )
    scale = np.asarray(
        [frame_width / calibrated_width, frame_height / calibrated_height],
        dtype=np.float32,
    )
    return quad * scale


def _make_cage_mask(quad: np.ndarray, frame_width: int, frame_height: int) -> np.ndarray:
    scaled = quad * np.asarray(
        [INPUT_WIDTH / frame_width, INPUT_HEIGHT / frame_height],
        dtype=np.float32,
    )
    mask = np.zeros((INPUT_HEIGHT, INPUT_WIDTH), dtype=np.uint8)
    cv2.fillPoly(mask, [np.rint(scaled).astype(np.int32)], 255)
    if cv2.countNonZero(mask) < 10:
        raise ValueError("Cage calibration generated an empty mask")
    return mask


def _make_yolo6_features(
    box_xyxy: Sequence[float],
    confidence: float,
    cage_quad: np.ndarray,
) -> np.ndarray:
    unit_quad = np.asarray(
        [[0, 0], [1, 0], [1, 1], [0, 1]],
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(cage_quad.astype(np.float32), unit_quad)
    if not np.isfinite(transform).all() or abs(float(np.linalg.det(transform))) < 1e-12:
        raise ValueError("Cage quadrilateral has no valid perspective transform")

    x1, y1, x2, y2 = (float(value) for value in box_xyxy)
    if not np.isfinite([x1, y1, x2, y2, confidence]).all():
        raise ValueError("Mouse detection contains NaN or infinity")
    center = np.asarray(
        [[[0.5 * (x1 + x2), 0.5 * (y1 + y2)]]],
        dtype=np.float32,
    )
    corners = np.asarray(
        [[[x1, y1], [x2, y1], [x2, y2], [x1, y2]]],
        dtype=np.float32,
    )
    relative_center = cv2.perspectiveTransform(center, transform)[0, 0]
    relative_corners = cv2.perspectiveTransform(corners, transform)[0]
    relative_width = max(
        1e-6,
        float(relative_corners[:, 0].max() - relative_corners[:, 0].min()),
    )
    relative_height = max(
        1e-6,
        float(relative_corners[:, 1].max() - relative_corners[:, 1].min()),
    )
    features = np.asarray(
        [
            relative_center[0],
            relative_center[1],
            relative_width,
            relative_height,
            np.log(relative_width / relative_height),
            confidence,
        ],
        dtype=np.float32,
    )
    if not np.isfinite(features).all():
        raise ValueError("YOLO6 features contain NaN or infinity")
    return features


def _largest_component(binary: np.ndarray) -> np.ndarray:
    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        binary.astype(np.uint8),
        8,
    )
    if count <= 1:
        return np.zeros_like(binary, dtype=np.uint8)
    largest_label = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    return (labels == largest_label).astype(np.uint8) * 255


def _sam_mouse_crop(
    segmenter: Any,
    frame: np.ndarray,
    box_xyxy: Sequence[float],
    device_arg: str,
    imgsz: int,
) -> np.ndarray:
    frame_height, frame_width = frame.shape[:2]
    box = np.asarray(box_xyxy, dtype=np.float32)
    results = segmenter.predict(
        frame,
        bboxes=[box.astype(float).tolist()],
        imgsz=imgsz,
        verbose=False,
        save=False,
        device=device_arg,
    )
    if not results or results[0].masks is None or results[0].masks.data is None:
        raise RuntimeError("MobileSAM returned no mouse mask")

    full_mask = results[0].masks.data[0].detach().float().cpu().numpy()
    if full_mask.shape != (frame_height, frame_width):
        full_mask = cv2.resize(
            full_mask,
            (frame_width, frame_height),
            interpolation=cv2.INTER_NEAREST,
        )
    full_mask = (full_mask > 0).astype(np.uint8) * 255

    x1 = int(np.clip(np.floor(box[0]), 0, frame_width))
    y1 = int(np.clip(np.floor(box[1]), 0, frame_height))
    x2 = int(np.clip(np.ceil(box[2]), 0, frame_width))
    y2 = int(np.clip(np.ceil(box[3]), 0, frame_height))
    if x2 <= x1 or y2 <= y1:
        raise ValueError("Mouse detector returned an empty box")
    crop = (full_mask[y1:y2, x1:x2] > 0).astype(np.uint8)
    if not crop.size or not np.any(crop):
        raise RuntimeError("MobileSAM mask is empty inside the mouse detection box")
    crop = cv2.resize(
        crop,
        (SAM_CROP_SIZE, SAM_CROP_SIZE),
        interpolation=cv2.INTER_NEAREST,
    )
    crop = _largest_component(crop)
    if not np.any(crop):
        raise RuntimeError("MobileSAM mouse mask has no connected foreground")
    return crop


def _make_mouse_mask(
    crop_mask: np.ndarray,
    box_xyxy: Sequence[float],
    frame_width: int,
    frame_height: int,
) -> np.ndarray:
    x1, y1, x2, y2 = (float(value) for value in box_xyxy)
    left = int(np.clip(np.floor(x1 * INPUT_WIDTH / frame_width), 0, INPUT_WIDTH))
    top = int(np.clip(np.floor(y1 * INPUT_HEIGHT / frame_height), 0, INPUT_HEIGHT))
    right = int(np.clip(np.ceil(x2 * INPUT_WIDTH / frame_width), 0, INPUT_WIDTH))
    bottom = int(np.clip(np.ceil(y2 * INPUT_HEIGHT / frame_height), 0, INPUT_HEIGHT))
    if right <= left or bottom <= top:
        raise ValueError("Mouse detection box collapses at model resolution")
    resized = cv2.resize(
        crop_mask,
        (right - left, bottom - top),
        interpolation=cv2.INTER_LINEAR,
    )
    mask = np.zeros((INPUT_HEIGHT, INPUT_WIDTH), dtype=np.uint8)
    mask[top:bottom, left:right] = resized
    return mask


class E2XYPredictor:
    """Predict continuous XY from cage mask, MobileSAM mask, and YOLO6 features."""

    def __init__(
        self,
        weights_path: Path | str,
        sam_weights_path: Path | str,
        calibrations_path: Path | str,
        cage_mapping_path: Path | str,
        device: str = "auto",
        cage_width_mm: float = 180.0,
        cage_depth_mm: float = 360.0,
        sam_imgsz: int = 1024,
    ):
        self.weights_path = Path(weights_path)
        self.sam_weights_path = Path(sam_weights_path)
        self.calibrations_path = Path(calibrations_path)
        self.cage_mapping_path = Path(cage_mapping_path)
        for required_path in (
            self.weights_path,
            self.sam_weights_path,
            self.calibrations_path,
            self.cage_mapping_path,
        ):
            if not required_path.is_file():
                raise FileNotFoundError(required_path)

        self.device = resolve_device(device)
        self.sam_device_arg = (
            str(self.device.index or 0) if self.device.type == "cuda" else "cpu"
        )
        self.cage_width_mm = float(cage_width_mm)
        self.cage_depth_mm = float(cage_depth_mm)
        self.sam_imgsz = max(int(sam_imgsz), 32)
        if self.cage_width_mm <= 0 or self.cage_depth_mm <= 0:
            raise ValueError("Cage physical dimensions must be positive")

        self.calibrations = _load_calibrations(self.calibrations_path)
        self.cage_key_by_cage = _load_mapping(self.cage_mapping_path)
        unknown_keys = sorted(set(self.cage_key_by_cage.values()) - set(self.calibrations))
        if unknown_keys:
            raise ValueError(
                "Unknown cage calibration key(s) in mapping: "
                f"{', '.join(unknown_keys)}"
            )

        checkpoint = torch.load(
            self.weights_path,
            map_location="cpu",
            weights_only=False,
        )
        state = checkpoint.get("state_dict") if isinstance(checkpoint, dict) else None
        if state is None:
            raise ValueError(f"E2 XY checkpoint has no state_dict: {self.weights_path}")
        self.model = CageMouseMaskYOLO6ResNet18()
        self.model.load_state_dict(state, strict=True)
        self.model.to(self.device).eval()
        try:
            from ultralytics.models.sam import SAM
        except ImportError as error:
            raise RuntimeError(
                "The updated E2 XY mode requires an Ultralytics build that "
                "provides ultralytics.models.sam.SAM"
            ) from error
        self.segmenter = SAM(str(self.sam_weights_path))

    def predict_batch(
        self,
        frames_bgr: Sequence[np.ndarray],
        detections: Sequence[Any],
        cage_numbers: Sequence[int],
    ) -> list[dict[str, Any]]:
        if not (len(frames_bgr) == len(detections) == len(cage_numbers)):
            raise ValueError("E2 XY frame, detection, and cage counts do not match")
        if not frames_bgr:
            return []

        results: list[dict[str, Any] | None] = [None] * len(frames_bgr)
        valid_indexes: list[int] = []
        mask_tensors: list[torch.Tensor] = []
        yolo_tensors: list[torch.Tensor] = []

        for index, (frame, detection, cage_number) in enumerate(
            zip(frames_bgr, detections, cage_numbers)
        ):
            try:
                if frame is None or frame.ndim != 3 or frame.shape[2] < 3:
                    raise ValueError("E2 XY requires a three-channel BGR frame")
                frame_height, frame_width = frame.shape[:2]
                cage_key = self.cage_key_by_cage.get(str(int(cage_number)))
                if not cage_key:
                    raise KeyError(
                        f"No cage calibration mapped for cage {int(cage_number)}; "
                        f"set it in {self.cage_mapping_path}"
                    )
                calibration = self.calibrations[cage_key]
                cage_quad = _scaled_cage_quad(
                    calibration,
                    frame_width,
                    frame_height,
                    cage_key,
                )
                mouse_box = np.asarray(detection.xyxy, dtype=np.float32)
                if mouse_box.shape != (4,):
                    raise ValueError("Mouse detection box must contain four coordinates")

                crop_mask = _sam_mouse_crop(
                    self.segmenter,
                    frame,
                    mouse_box,
                    self.sam_device_arg,
                    self.sam_imgsz,
                )
                cage_mask = _make_cage_mask(cage_quad, frame_width, frame_height)
                mouse_mask = _make_mouse_mask(
                    crop_mask,
                    mouse_box,
                    frame_width,
                    frame_height,
                )
                mask_pair = np.stack((cage_mask, mouse_mask), axis=0)
                mask_tensors.append(
                    torch.from_numpy(mask_pair).to(dtype=torch.float32).div_(255.0)
                )
                yolo_features = _make_yolo6_features(
                    mouse_box,
                    float(detection.conf),
                    cage_quad,
                )
                yolo_tensors.append(torch.from_numpy(yolo_features))
                valid_indexes.append(index)
                results[index] = {"cage_key": cage_key}
            except Exception as error:
                results[index] = {"error": str(error)}

        if valid_indexes:
            mask_batch = torch.stack(mask_tensors).to(self.device)
            yolo_batch = torch.stack(yolo_tensors).to(self.device)
            with torch.inference_mode():
                relative_predictions = self.model(mask_batch, yolo_batch).detach().cpu().numpy()
            for result_index, prediction in zip(valid_indexes, relative_predictions):
                x_relative = float(prediction[0])
                y_relative = float(prediction[1])
                result = results[result_index] or {}
                result.update(
                    {
                        "x_relative": x_relative,
                        "y_relative": y_relative,
                        "x_mm": x_relative * self.cage_width_mm / 2.0,
                        "y_mm": y_relative * self.cage_depth_mm,
                    }
                )
                results[result_index] = result

        return [result or {"error": "XY prediction did not produce a result"} for result in results]
