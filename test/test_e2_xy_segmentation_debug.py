"""Hardware-free checks for sampled E2 XY segmentation snapshots."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import torch

from Module.mouse_trajectory.service.e2_xy_predictor import E2XYPredictor
from Module.mouse_trajectory.service.live_mouse_trajectory_thread import MouseTrajectoryThread


class E2XYSegmentationDebugTests(unittest.TestCase):
    @staticmethod
    def _make_predictor(sam_mask):
        predictor = object.__new__(E2XYPredictor)
        predictor.segmenter = SimpleNamespace(
            predict=Mock(return_value=[
                SimpleNamespace(masks=SimpleNamespace(data=torch.from_numpy(sam_mask[None])))
            ])
        )
        predictor.sam_device_arg = "cpu"
        predictor.sam_imgsz = 64
        predictor.cage_width_mm = 180.0
        predictor.cage_depth_mm = 360.0
        predictor.device = torch.device("cpu")
        predictor.model = Mock(return_value=torch.tensor([[0.25, 0.5]]))
        return predictor

    def test_sampled_prediction_retains_actual_sam_and_model_input_masks(self):
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        sam_mask = np.zeros((48, 64), dtype=np.uint8)
        sam_mask[18:27, 26:35] = 1
        predictor = self._make_predictor(sam_mask)
        detection = SimpleNamespace(xyxy=(20, 12, 40, 36), conf=0.9)
        corners = np.array([[4, 4], [60, 4], [60, 44], [4, 44]], dtype=np.float32)

        sampled = predictor.predict_batch(
            [frame], [detection], [corners], debug_mask_indexes={0}
        )[0]
        self.assertEqual(sampled["x_mm"], 22.5)
        self.assertEqual(sampled["y_mm"], 180.0)
        np.testing.assert_array_equal(sampled["debug_masks"]["sam_mouse"], sam_mask * 255)
        self.assertEqual(sampled["debug_masks"]["model_mouse"].shape, (144, 256))
        self.assertEqual(sampled["debug_masks"]["model_cage"].shape, (144, 256))

        unsampled = predictor.predict_batch([frame], [detection], [corners])[0]
        self.assertNotIn("debug_masks", unsampled)

    def test_debug_images_are_written_as_masks_and_overlay(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            target = Path(directory)
            frame = np.full((48, 64, 3), 40, dtype=np.uint8)
            sam_mask = np.zeros((48, 64), dtype=np.uint8)
            sam_mask[18:27, 26:35] = 255
            mask = np.zeros((144, 256), dtype=np.uint8)
            mask[50:80, 100:140] = 255
            fake_thread = SimpleNamespace(
                _get_cage_annotated_latest_dir=lambda cage_number: target,
                _write_encoded_jpg_atomic=MouseTrajectoryThread._write_encoded_jpg_atomic,
                mouse_annotated_jpg_quality=85,
            )

            MouseTrajectoryThread._save_segmentation_debug_images(
                fake_thread,
                3,
                frame,
                {"sam_mouse": sam_mask, "model_mouse": mask, "model_cage": mask},
            )

            for name in (
                "sam_mouse_mask_latest.png",
                "model_mouse_mask_latest.png",
                "model_cage_mask_latest.png",
                "segmentation_overlay_latest.jpg",
            ):
                self.assertTrue((target / name).is_file(), name)
            saved_sam_mask = cv2.imread(str(target / "sam_mouse_mask_latest.png"), cv2.IMREAD_GRAYSCALE)
            np.testing.assert_array_equal(np.squeeze(saved_sam_mask), sam_mask)
            saved_overlay = cv2.imread(str(target / "segmentation_overlay_latest.jpg"))
            self.assertEqual(saved_overlay.shape, frame.shape)
            self.assertGreater(int(saved_overlay[22, 30, 1]), int(saved_overlay[0, 0, 1]))

    def test_debug_output_error_does_not_escape_into_tracking(self):
        fake_thread = SimpleNamespace(
            _get_cage_annotated_latest_dir=lambda cage_number: Path("unneeded"),
            _write_encoded_jpg_atomic=Mock(side_effect=OSError("disk full")),
            mouse_annotated_jpg_quality=85,
        )
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        sam_mask = np.zeros((48, 64), dtype=np.uint8)
        model_mask = np.zeros((144, 256), dtype=np.uint8)

        MouseTrajectoryThread._save_segmentation_debug_images(
            fake_thread,
            3,
            frame,
            {
                "sam_mouse": sam_mask,
                "model_mouse": model_mask,
                "model_cage": model_mask,
            },
        )


if __name__ == "__main__":
    unittest.main()
