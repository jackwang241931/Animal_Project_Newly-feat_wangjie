from __future__ import annotations

from pathlib import Path
from typing import Any, Sequence

import cv2
import numpy as np
import torch
from torch import nn
from torchvision.models import resnet18


IMAGE_WIDTH = 256
IMAGE_HEIGHT = 144
X_SCALE_MM = 90.0
RGB_MEAN = torch.tensor([0.485, 0.456, 0.406], dtype=torch.float32).view(
    1, 3, 1, 1
)
RGB_STD = torch.tensor([0.229, 0.224, 0.225], dtype=torch.float32).view(
    1, 3, 1, 1
)
EXPECTED_AUXILIARY_FEATURES = [
    "mouse_box_center_x_norm",
    "mouse_box_center_y_norm",
    "mouse_box_width_norm",
    "mouse_box_height_norm",
    "mouse_box_log_aspect",
    "mouse_yolo_confidence",
]


class RGB3YOLO6ResNet18(nn.Module):
    """Network architecture used by the E2 RGB + YOLO6 checkpoint."""

    def __init__(self, bin_count: int):
        super().__init__()
        backbone = resnet18(weights=None)
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
            nn.Linear(192, 1 + bin_count),
        )

    def forward(
        self,
        image_rgb: torch.Tensor,
        yolo_features: torch.Tensor,
    ) -> tuple[torch.Tensor, torch.Tensor]:
        visual = self.backbone(image_rgb).flatten(1)
        fused = torch.cat((visual, self.aux(yolo_features)), dim=1)
        raw = self.head(fused)
        return torch.tanh(raw[:, 0]), raw[:, 1:]


def resolve_device(value: str) -> torch.device:
    requested = str(value or "auto").strip().lower()
    if requested == "auto":
        return torch.device("cuda" if torch.cuda.is_available() else "cpu")
    return torch.device(requested)


def prepare_rgb(frame_bgr: np.ndarray) -> torch.Tensor:
    if frame_bgr is None or frame_bgr.ndim != 3 or frame_bgr.shape[2] < 3:
        raise ValueError("E2 XY predictor requires a three-channel BGR frame")
    resized = cv2.resize(
        frame_bgr[:, :, :3],
        (IMAGE_WIDTH, IMAGE_HEIGHT),
        interpolation=cv2.INTER_AREA,
    )
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    tensor = torch.from_numpy(
        np.ascontiguousarray(rgb.transpose(2, 0, 1))
    ).float().unsqueeze(0)
    tensor.div_(255.0)
    return (tensor - RGB_MEAN) / RGB_STD


def make_yolo_features(
    box_xyxy: Sequence[float],
    confidence: float,
    image_width: int,
    image_height: int,
) -> torch.Tensor:
    if image_width <= 0 or image_height <= 0:
        raise ValueError("Invalid source frame size for E2 XY prediction")
    x1, y1, x2, y2 = (float(value) for value in box_xyxy)
    box_width = max(1.0, x2 - x1)
    box_height = max(1.0, y2 - y1)
    return torch.tensor(
        [[
            (x1 + x2) * 0.5 / image_width,
            (y1 + y2) * 0.5 / image_height,
            box_width / image_width,
            box_height / image_height,
            float(np.log(box_width / box_height)),
            float(confidence),
        ]],
        dtype=torch.float32,
    )


class E2XYPredictor:
    """Batch predictor that emits X/Y only. Z is intentionally unavailable."""

    def __init__(self, weights_path: Path | str, device: str = "auto"):
        self.weights_path = Path(weights_path)
        if not self.weights_path.exists():
            raise FileNotFoundError(self.weights_path)
        self.device = resolve_device(device)
        checkpoint = torch.load(
            self.weights_path,
            map_location="cpu",
            weights_only=False,
        )
        if not isinstance(checkpoint, dict) or "state_dict" not in checkpoint:
            raise ValueError(f"Not an E2 checkpoint with state_dict: {self.weights_path}")
        if checkpoint.get("input_shape_per_sample") != [
            3,
            IMAGE_HEIGHT,
            IMAGE_WIDTH,
        ]:
            raise ValueError(
                "Unexpected E2 image input shape: "
                f"{checkpoint.get('input_shape_per_sample')}"
            )
        if checkpoint.get("auxiliary_features") != EXPECTED_AUXILIARY_FEATURES:
            raise ValueError("E2 checkpoint auxiliary feature order does not match")
        if checkpoint.get("output_kind") != "y_distribution":
            raise ValueError(
                "Expected E2 y_distribution checkpoint, got "
                f"{checkpoint.get('output_kind')}"
            )
        centers = np.asarray(
            checkpoint.get("y_bin_centers_mm"),
            dtype=np.float32,
        )
        if centers.ndim != 1 or len(centers) != 36:
            raise ValueError("E2 checkpoint must contain 36 Y-bin centers")

        self.model = RGB3YOLO6ResNet18(len(centers))
        self.model.load_state_dict(checkpoint["state_dict"], strict=True)
        self.model.to(self.device).eval()
        self.y_centers = torch.as_tensor(
            centers,
            dtype=torch.float32,
            device=self.device,
        ).view(1, -1)

    def predict_batch(
        self,
        frames_bgr: Sequence[np.ndarray],
        detections: Sequence[Any],
    ) -> list[dict[str, float]]:
        if len(frames_bgr) != len(detections):
            raise ValueError("E2 frame and detection counts do not match")
        if not frames_bgr:
            return []

        image_tensors: list[torch.Tensor] = []
        auxiliary_tensors: list[torch.Tensor] = []
        for frame, detection in zip(frames_bgr, detections):
            height, width = frame.shape[:2]
            image_tensors.append(prepare_rgb(frame))
            auxiliary_tensors.append(
                make_yolo_features(
                    detection.xyxy,
                    detection.conf,
                    width,
                    height,
                )
            )

        image_batch = torch.cat(image_tensors, dim=0).to(self.device)
        auxiliary_batch = torch.cat(auxiliary_tensors, dim=0).to(self.device)
        with torch.inference_mode():
            x_normalized, y_logits = self.model(image_batch, auxiliary_batch)
            y_probabilities = torch.softmax(y_logits, dim=1)
            y_prediction = (y_probabilities * self.y_centers).sum(dim=1)
            top_probability, top_index = y_probabilities.max(dim=1)
            x_prediction = x_normalized * X_SCALE_MM

        results: list[dict[str, float]] = []
        centers_flat = self.y_centers.view(-1)
        for index in range(len(frames_bgr)):
            results.append(
                {
                    "x_mm": float(x_prediction[index].item()),
                    "y_mm": float(y_prediction[index].item()),
                    "y_top_bin_center_mm": float(
                        centers_flat[int(top_index[index].item())].item()
                    ),
                    "y_top_bin_probability": float(
                        top_probability[index].item()
                    ),
                }
            )
        return results
