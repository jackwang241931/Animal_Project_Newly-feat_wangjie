"""Hardware-free checks for per-frame E2 XY segmentation archives."""

import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import cv2
import numpy as np
import torch

from Module.mouse_trajectory.paths import get_cage_segmentation_dir
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

    def test_prediction_retains_actual_sam_and_model_input_masks(self):
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        sam_mask = np.zeros((48, 64), dtype=np.uint8)
        sam_mask[18:27, 26:35] = 1
        predictor = self._make_predictor(sam_mask)
        detection = SimpleNamespace(xyxy=(20, 12, 40, 36), conf=0.9)
        corners = np.array([[4, 4], [60, 4], [60, 44], [4, 44]], dtype=np.float32)

        selected = predictor.predict_batch(
            [frame], [detection], [corners], debug_mask_indexes={0}
        )[0]
        self.assertEqual(selected["x_mm"], 22.5)
        self.assertEqual(selected["y_mm"], 180.0)
        np.testing.assert_array_equal(selected["debug_masks"]["sam_mouse"], sam_mask * 255)
        self.assertEqual(selected["debug_masks"]["model_mouse"].shape, (144, 256))
        self.assertEqual(selected["debug_masks"]["model_cage"].shape, (144, 256))

        unsampled = predictor.predict_batch([frame], [detection], [corners])[0]
        self.assertNotIn("debug_masks", unsampled)

    def test_batch_requests_masks_for_every_inferred_frame(self):
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        detection = SimpleNamespace(xyxy=(20, 12, 40, 36), conf=0.9)
        corners = np.array([[4, 4], [60, 4], [60, 44], [4, 44]], dtype=np.float32)
        predictor = SimpleNamespace(predict_batch=Mock(return_value=[{}, {}]))
        fake_thread = SimpleNamespace(
            xy_predictor=predictor,
            xy_save_segmentation_debug=True,
            e2_cage_corners_by_cage={3: corners, 4: corners},
            _record_stage_timing=Mock(),
        )
        prepared_items = [
            {"cage_number": cage, "mouse_result": object(), "source_frame": frame}
            for cage in (3, 4)
        ]

        with patch(
            "Module.mouse_trajectory.service.live_mouse_trajectory_thread.best_box_from_result",
            return_value=detection,
        ):
            MouseTrajectoryThread._run_e2_xy_batch(fake_thread, prepared_items, [0, 1])

        self.assertEqual(predictor.predict_batch.call_args.kwargs["debug_mask_indexes"], {0, 1})

    def test_every_frame_is_archived_separately_for_each_cage(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as directory:
            target = Path(directory)
            frame = np.full((48, 64, 3), 40, dtype=np.uint8)
            sam_mask = np.zeros((48, 64), dtype=np.uint8)
            sam_mask[18:27, 26:35] = 255
            mask = np.zeros((144, 256), dtype=np.uint8)
            mask[50:80, 100:140] = 255
            fake_thread = SimpleNamespace(
                _get_cage_segmentation_dir=lambda cage_number: get_cage_segmentation_dir(
                    cage_number, export_root=target
                ),
                _write_encoded_jpg_atomic=MouseTrajectoryThread._write_encoded_jpg_atomic,
                mouse_annotated_jpg_quality=85,
            )
            masks = {"sam_mouse": sam_mask, "model_mouse": mask, "model_cage": mask}
            for cage_number, frame_id in ((3, 1), (3, 2), (4, 1)):
                MouseTrajectoryThread._save_segmentation_debug_images(
                    fake_thread, cage_number, frame_id, 1792410000.0, frame, masks
                )

            cage_three = target / "cage_3" / "segmentation"
            cage_four = target / "cage_4" / "segmentation"
            self.assertEqual(len(list(cage_three.iterdir())), 8)
            self.assertEqual(len(list(cage_four.iterdir())), 4)
            self.assertEqual(len(list(cage_three.glob("*_sam_mouse_mask.png"))), 2)
            saved_sam_mask = cv2.imread(
                str(next(cage_three.glob("*_sam_mouse_mask.png"))),
                cv2.IMREAD_GRAYSCALE,
            )
            np.testing.assert_array_equal(np.squeeze(saved_sam_mask), sam_mask)
            saved_overlay = cv2.imread(
                str(next(cage_three.glob("*_segmentation_overlay.jpg")))
            )
            self.assertEqual(saved_overlay.shape, frame.shape)
            self.assertGreater(int(saved_overlay[22, 30, 1]), int(saved_overlay[0, 0, 1]))

    def test_debug_output_error_does_not_escape_into_tracking(self):
        fake_thread = SimpleNamespace(
            _get_cage_segmentation_dir=lambda cage_number: Path("unneeded"),
            _write_encoded_jpg_atomic=Mock(side_effect=OSError("disk full")),
            mouse_annotated_jpg_quality=85,
        )
        frame = np.zeros((48, 64, 3), dtype=np.uint8)
        sam_mask = np.zeros((48, 64), dtype=np.uint8)
        model_mask = np.zeros((144, 256), dtype=np.uint8)

        MouseTrajectoryThread._save_segmentation_debug_images(
            fake_thread,
            3,
            1,
            1792410000.0,
            frame,
            {
                "sam_mouse": sam_mask,
                "model_mouse": model_mask,
                "model_cage": model_mask,
            },
        )

    def test_busy_writer_keeps_both_segmentation_frames(self):
        first_started = threading.Event()
        release_first = threading.Event()
        written = []

        def write_frame(_cage, frame_id, _timestamp, _frame, _masks):
            if frame_id == 1:
                first_started.set()
                self.assertTrue(release_first.wait(3))
            written.append(frame_id)

        with ThreadPoolExecutor(max_workers=1) as executor:
            fake_thread = SimpleNamespace(
                async_job_lock=threading.RLock(),
                async_jobs={},
                async_executors_shutdown=False,
                segmentation_executor=executor,
                segmentation_write_slots=threading.BoundedSemaphore(2),
                _save_segmentation_debug_images=write_frame,
            )
            fake_thread._submit_async_job = lambda *args, **kwargs: (
                MouseTrajectoryThread._submit_async_job(fake_thread, *args, **kwargs)
            )
            MouseTrajectoryThread._schedule_segmentation_debug_images(
                fake_thread, 3, 1, 1792410000.0, None, {}
            )
            self.assertTrue(first_started.wait(3))
            MouseTrajectoryThread._schedule_segmentation_debug_images(
                fake_thread, 3, 2, 1792410000.1, None, {}
            )
            release_first.set()
            MouseTrajectoryThread._wait_for_async_outputs(fake_thread)

        self.assertEqual(written, [1, 2])


if __name__ == "__main__":
    unittest.main()
