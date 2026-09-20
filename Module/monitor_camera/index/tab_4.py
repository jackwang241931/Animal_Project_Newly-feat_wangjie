from __future__ import annotations

import os
import multiprocessing
import queue
import re
import threading
import time
import traceback
import typing
import weakref
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
import cv2
import numpy as np
from PyQt6 import QtGui
from PyQt6.QtCharts import QChart, QChartView, QDateTimeAxis, QScatterSeries, QValueAxis
from PyQt6.QtCore import (
    QDateTime,
    QEventLoop,
    QMargins,
    QPointF,
    QRect,
    QRectF,
    QThread,
    QTimer,
    Qt,
    pyqtSignal,
)
from PyQt6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QGraphicsScene,
    QGraphicsView,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)
from loguru import logger

from Module.monitor_camera.ui.tab4_window import Ui_tab4_window
from public.component.dialog.index.infrared_camera_read_SN_dialog_index import infrared_camera_read_SN_dialog
from public.config_class.global_setting import global_setting
from public.dao.SQLite.Monitor_Datas_Handle import Monitor_Datas_Handle
from public.entity.MyQThread import MyQThread
from public.util.folder_util import folder_util
from public.util.shared_video_frames import shared_video_frame_store
from theme.ThemeQt6 import ThemedWindow


runtime_diagnostics_drop_lock = threading.Lock()
runtime_diagnostics_dropped = 0
trajectory_service_lock = threading.RLock()
shared_trajectory_thread = None
shared_trajectory_submitter_thread = None
trajectory_service_consumers = weakref.WeakSet()
trajectory_module_loader_lock = threading.RLock()
trajectory_module_loader = None
mouse_trajectory_thread_class = None

if typing.TYPE_CHECKING:
    from Module.mouse_trajectory.service import MouseTrajectoryThread


class TrajectoryModuleLoaderThread(QThread):
    loaded = pyqtSignal(object)
    failed = pyqtSignal(str)

    def run(self):
        global mouse_trajectory_thread_class
        try:
            from Module.mouse_trajectory.service import MouseTrajectoryThread

            with trajectory_module_loader_lock:
                mouse_trajectory_thread_class = MouseTrajectoryThread
            self.loaded.emit(MouseTrajectoryThread)
        except Exception:
            error_message = traceback.format_exc()
            logger.error(f"mouse trajectory module load failed: {error_message}")
            self.failed.emit(error_message)


def _emit_runtime_diagnostic(
    event: str,
    message: str,
    logical_thread_name: str | None = None,
):
    global runtime_diagnostics_dropped
    diagnostics_queue = global_setting.get_setting(
        "runtime_diagnostics_queue",
        None,
    )
    if diagnostics_queue is None:
        return
    with runtime_diagnostics_drop_lock:
        dropped_count = runtime_diagnostics_dropped
        record_message = str(message)
        if dropped_count:
            record_message = (
                f"{record_message}, "
                f"runtime_log_drops_since_last_success={dropped_count}"
            )
        try:
            diagnostics_queue.put_nowait(
                {
                    "timestamp": time.time(),
                    "source": "video_gui",
                    "event": str(event),
                    "pid": os.getpid(),
                    "process_name": multiprocessing.current_process().name,
                    "thread_name": (
                        logical_thread_name or threading.current_thread().name
                    ),
                    "message": record_message,
                }
            )
            runtime_diagnostics_dropped = 0
        except queue.Full:
            runtime_diagnostics_dropped = dropped_count + 1
        except (BrokenPipeError, EOFError, OSError):
            runtime_diagnostics_dropped = dropped_count + 1


class ImageLoaderThread(MyQThread):
    image_loaded = pyqtSignal(dict)

    def __init__(self, display_mode: str = "infrared"):
        super().__init__(name="tab4_image_loader")
        self.display_mode = display_mode
        self.target_cage_number: int | None = None
        self.cached_target_cages: set[int] = set()
        self.last_target_cage_refresh_time = 0.0
        self.target_cage_refresh_interval = 1.0
        self.display_max_age_seconds = 2.0
        self.video_stats_interval_seconds = 5.0
        self.video_stats_last_monotonic = time.monotonic()
        self.video_read_attempt_count = 0
        self.video_read_success_count = 0
        self.video_fresh_frame_count = 0
        self.video_read_total_ms = 0.0
        self.video_last_frame_id = 0
        self.video_last_frame_age_ms = 0.0
        self.video_last_seen_key_by_cage: dict[int, tuple[int, int]] = {}
        self.refresh_camera_paths()

    def set_display_mode(self, display_mode: str):
        self.display_mode = display_mode

    def set_target_cage_number(self, cage_number: int | None):
        self.target_cage_number = None if cage_number is None else int(cage_number)

    def refresh_camera_paths(self):
        camera_config = global_setting.get_setting("camera_config")
        self.infrared_camera_nums = int(camera_config["INFRARED_CAMERA"]["nums"])
        trajectory_config = camera_config.get("MOUSE_TRAJECTORY", {})
        self.display_max_age_seconds = max(
            float(
                trajectory_config.get(
                    "display_max_age_seconds",
                    self.display_max_age_seconds,
                )
                or self.display_max_age_seconds
            ),
            0.1,
        )

        self.infrared_path = []
        if self.display_mode != "video":
            infrared_folder_list = folder_util.list_directories(
                camera_config["STORAGE"]["fold_path"] + camera_config["INFRARED_CAMERA"]["path"]
            )

            self.infrared_path = [
                camera_config["STORAGE"]["fold_path"]
                + camera_config["INFRARED_CAMERA"]["path"]
                + f"{folder_name}/"
                + camera_config["INFRARED_CAMERA"]["pic_dir"]
                for folder_name in infrared_folder_list
            ]
        self.images = {"deep_camera_frames": {}, "trajectory_frames": {}, "infrared_camera": []}
        self.running = True

    def _get_sleep_delay(self, configer) -> float:
        if self.display_mode == "video":
            return 0.03
        return float(configer["monitor_camera_pic"]["delay"])

    def _get_target_cages(self) -> set[int]:
        now = time.time()
        if self.cached_target_cages and now - self.last_target_cage_refresh_time < self.target_cage_refresh_interval:
            return set(self.cached_target_cages)

        target_cages = set(int(cage) for cage in (global_setting.get_setting("mouse_cages", []) or []))
        experiment_setting = global_setting.get_setting("experiment_setting", None)
        if experiment_setting is not None and getattr(experiment_setting, "groups", None):
            for group in experiment_setting.groups:
                if getattr(group, "is_selected", 0) == 1:
                    target_cages.add(int(group.id))

        self.cached_target_cages = set(target_cages)
        self.last_target_cage_refresh_time = now
        return target_cages

    @staticmethod
    def parse_filename_datetime(filename):
        base = os.path.splitext(filename)[0]
        try:
            return datetime.strptime(base, "%Y_%m_%d_%H_%M_%S_%f")
        except ValueError:
            return None

    def filter_files_earlier_than(self, folder, delta_seconds=10):
        now = datetime.now()
        threshold = now - timedelta(seconds=delta_seconds)

        result_files = []
        for file_name in os.listdir(folder):
            dt = self.parse_filename_datetime(file_name)
            if dt and dt < threshold:
                result_files.append((dt, file_name))

        if not result_files:
            return None

        result_files.sort(key=lambda item: item[0])
        return result_files[-1][1]

    def _record_video_read(
        self,
        cage_number: int,
        frame_payload: dict[str, typing.Any] | None,
        read_ms: float,
    ):
        self.video_read_attempt_count += 1
        self.video_read_total_ms += max(float(read_ms), 0.0)
        if frame_payload is not None:
            self.video_read_success_count += 1
            frame_key = (
                int(frame_payload.get("camera_session_id", 0) or 0),
                int(frame_payload.get("frame_sequence", frame_payload.get("frame_id", 0)) or 0),
            )
            if self.video_last_seen_key_by_cage.get(int(cage_number)) != frame_key:
                self.video_fresh_frame_count += 1
                self.video_last_seen_key_by_cage[int(cage_number)] = frame_key
            self.video_last_frame_id = frame_key[1]
            self.video_last_frame_age_ms = (
                float(frame_payload.get("frame_age_seconds", 0.0) or 0.0) * 1000.0
            )

        now_monotonic = time.monotonic()
        stats_elapsed = now_monotonic - self.video_stats_last_monotonic
        if stats_elapsed < self.video_stats_interval_seconds:
            return

        average_read_ms = (
            self.video_read_total_ms / self.video_read_attempt_count
            if self.video_read_attempt_count
            else 0.0
        )
        _emit_runtime_diagnostic(
            "video_read_runtime",
            f"selected_cage={self.target_cage_number}, "
            f"poll_fps={self.video_read_attempt_count / max(stats_elapsed, 0.001):.2f}, "
            f"fresh_frame_fps={self.video_fresh_frame_count / max(stats_elapsed, 0.001):.2f}, "
            f"read_success={self.video_read_success_count}/"
            f"{self.video_read_attempt_count}, "
            f"read_avg_ms={average_read_ms:.2f}, "
            f"frame_age_ms={self.video_last_frame_age_ms:.1f}, "
            f"frame_id={self.video_last_frame_id}",
            logical_thread_name=self.name,
        )
        self.video_stats_last_monotonic = now_monotonic
        self.video_read_attempt_count = 0
        self.video_read_success_count = 0
        self.video_fresh_frame_count = 0
        self.video_read_total_ms = 0.0

    def dosomething(self):
        try:
            if self.display_mode != "video":
                self.refresh_camera_paths()
            configer = global_setting.get_setting("configer")
            infrared_camera_list = []

            if self.display_mode != "video":
                for path in self.infrared_path:
                    if not os.path.exists(path):
                        os.makedirs(path)
                    file_name = self.filter_files_earlier_than(
                        folder=path,
                        delta_seconds=float(configer["monitor_camera_pic"]["data_delay"]),
                    )
                    infrared_camera_list.append("" if file_name is None else os.path.join(path, file_name))

            deep_camera_frames = {}
            trajectory_frames = {}
            target_cages = self._get_target_cages()

            display_cages = target_cages
            if self.display_mode == "video" and self.target_cage_number is not None:
                display_cages = {self.target_cage_number}

            for cage_number in sorted(display_cages):
                read_start = time.perf_counter()
                frame_payload = shared_video_frame_store.read_frame(
                    "deep_camera",
                    cage_number,
                    max_age_seconds=self.display_max_age_seconds,
                )
                read_ms = (time.perf_counter() - read_start) * 1000.0
                if self.display_mode == "video":
                    self._record_video_read(cage_number, frame_payload, read_ms)
                if frame_payload is not None:
                    deep_camera_frames[cage_number] = frame_payload

            self.images["deep_camera_frames"] = deep_camera_frames
            self.images["trajectory_frames"] = trajectory_frames
            self.images["infrared_camera"] = infrared_camera_list
            self.image_loaded.emit(self.images)
            time.sleep(self._get_sleep_delay(configer))
        except Exception as e:
            logger.error(f"tab4线程异常，原因: {e} | 堆栈: {traceback.format_exc()}")


class TrajectoryFrameSubmitterThread(MyQThread):
    def __init__(self, trajectory_thread: MouseTrajectoryThread):
        super().__init__(name="trajectory_frame_submitter")
        self.trajectory_thread = trajectory_thread
        self.submitted_file_keys: dict[int, set[str]] = {}
        self.last_submitted_frame_ids: dict[int, int] = {}
        self.last_considered_frame_versions: dict[int, tuple[int, int]] = {}
        self.next_submit_time_by_cage: dict[int, float] = {}
        self.round_robin_offset = 0
        self.cached_target_cages: set[int] = set()
        self.last_target_cage_refresh_time = 0.0
        self.target_cage_refresh_interval = 1.0
        self.poll_interval_seconds = 0.05
        self.scan_limit_per_cage = 200
        self.file_min_age_seconds = 0.2
        self.input_mode = "memory"
        self.enable_disk_backfill = False
        self.target_fps = 10.0
        self.trajectory_max_age_seconds = 0.75
        self.online_timeout_seconds = 2.0
        self.last_online_seen_by_cage: dict[int, float] = {}
        self.storage_base_dir = Path.cwd() / "data"
        self.deep_camera_path = "deep_camera"
        self.mouse_cage_prefix = "mouse_cage_"
        self.color_dir = "color"
        self.submitted_since_debug_log = 0
        self.last_submit_debug_log_monotonic = time.monotonic()
        self._load_runtime_config()

    @staticmethod
    def _as_bool(value, default=False) -> bool:
        if value is None:
            return bool(default)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        text = str(value).strip().lower()
        if text in ("1", "true", "yes", "y", "on"):
            return True
        if text in ("0", "false", "no", "n", "off"):
            return False
        return bool(default)

    @staticmethod
    def _frame_version(frame_payload: dict[str, Any]) -> tuple[int, int]:
        frame_sequence = int(
            frame_payload.get("frame_sequence", frame_payload.get("frame_id", 0)) or 0
        )
        camera_session_id = int(frame_payload.get("camera_session_id", 0) or 0)
        if camera_session_id > 0:
            return camera_session_id, frame_sequence
        timestamp = float(frame_payload.get("timestamp", 0.0) or 0.0)
        return int(timestamp * 1_000_000_000), frame_sequence

    @staticmethod
    def _is_stale_frame_version(
        frame_version: tuple[int, int],
        previous_version: tuple[int, int] | None,
    ) -> bool:
        return (
            previous_version is not None
            and frame_version[0] == previous_version[0]
            and frame_version[1] <= previous_version[1]
        )

    def _load_runtime_config(self):
        try:
            camera_config = global_setting.get_setting("camera_config")
            trajectory_config = {}
            storage_config = {}
            deep_config = {}
            if camera_config and "MOUSE_TRAJECTORY" in camera_config:
                trajectory_config = camera_config["MOUSE_TRAJECTORY"]
            if camera_config and "STORAGE" in camera_config:
                storage_config = camera_config["STORAGE"]
            if camera_config and "DEEP_CAMERA" in camera_config:
                deep_config = camera_config["DEEP_CAMERA"]
            self.poll_interval_seconds = max(
                float(
                    trajectory_config.get(
                        "trajectory_submit_interval_seconds",
                        self.poll_interval_seconds,
                    )
                    or self.poll_interval_seconds
                ),
                0.005,
            )
            self.input_mode = str(
                trajectory_config.get("trajectory_input_mode", self.input_mode) or self.input_mode
            ).strip().lower()
            self.enable_disk_backfill = self._as_bool(
                trajectory_config.get("enable_disk_backfill", self.input_mode == "disk"),
                self.input_mode == "disk",
            )
            self.target_fps = max(
                float(trajectory_config.get("trajectory_target_fps", self.target_fps) or self.target_fps),
                0.1,
            )
            self.trajectory_max_age_seconds = max(
                float(
                    trajectory_config.get(
                        "trajectory_max_age_seconds",
                        self.trajectory_max_age_seconds,
                    )
                    or self.trajectory_max_age_seconds
                ),
                0.1,
            )
            self.online_timeout_seconds = max(
                float(
                    trajectory_config.get(
                        "trajectory_online_timeout_seconds",
                        self.online_timeout_seconds,
                    )
                    or self.online_timeout_seconds
                ),
                self.trajectory_max_age_seconds,
            )
            self.scan_limit_per_cage = max(
                int(float(trajectory_config.get("trajectory_scan_limit_per_cage", self.scan_limit_per_cage) or self.scan_limit_per_cage)),
                1,
            )
            self.file_min_age_seconds = max(
                float(trajectory_config.get("trajectory_file_min_age_seconds", self.file_min_age_seconds) or self.file_min_age_seconds),
                0.0,
            )
            fold_path = str(storage_config.get("fold_path", "./data/") or "./data/")
            storage_base_dir = Path(fold_path)
            if not storage_base_dir.is_absolute():
                storage_base_dir = Path.cwd() / storage_base_dir
            self.storage_base_dir = storage_base_dir
            self.deep_camera_path = str(deep_config.get("path", self.deep_camera_path) or self.deep_camera_path).strip("/\\")
            self.mouse_cage_prefix = str(deep_config.get("mouse_cage_prefix", self.mouse_cage_prefix) or self.mouse_cage_prefix)
            self.color_dir = str(deep_config.get("color_dir", self.color_dir) or self.color_dir).strip("/\\")
        except Exception as error:
            logger.warning(f"load trajectory submitter config failed, use defaults: {error}")

    def _effective_submit_fps(self, active_cage_count: int) -> float:
        active_cage_count = max(int(active_cage_count), 1)
        recommended_fps = self.target_fps
        getter = getattr(self.trajectory_thread, "get_recommended_submit_fps", None)
        if callable(getter):
            try:
                recommended_fps = float(getter(active_cage_count, self.target_fps))
            except TypeError:
                recommended_fps = float(getter(active_cage_count))
            except Exception as error:
                logger.debug(f"get recommended trajectory submit fps failed: {error}")
        return max(0.1, min(float(self.target_fps), float(recommended_fps)))

    def _active_online_cage_count(self, target_cages: list[int], now: float) -> int:
        active_count = sum(
            1
            for cage_number in target_cages
            if now - self.last_online_seen_by_cage.get(int(cage_number), 0.0)
            <= self.online_timeout_seconds
        )
        return active_count if active_count > 0 else max(len(target_cages), 1)

    def _get_target_cages(self) -> set[int]:
        now = time.time()
        if self.cached_target_cages and now - self.last_target_cage_refresh_time < self.target_cage_refresh_interval:
            return set(self.cached_target_cages)

        target_cages = set(int(cage) for cage in (global_setting.get_setting("mouse_cages", []) or []))
        experiment_setting = global_setting.get_setting("experiment_setting", None)
        if experiment_setting is not None and getattr(experiment_setting, "groups", None):
            for group in experiment_setting.groups:
                if getattr(group, "is_selected", 0) == 1:
                    target_cages.add(int(group.id))

        self.cached_target_cages = set(target_cages)
        self.last_target_cage_refresh_time = now
        return target_cages

    def _get_cage_color_dir(self, cage_number: int) -> Path:
        return (
            self.storage_base_dir
            / self.deep_camera_path
            / f"{self.mouse_cage_prefix}{int(cage_number)}"
            / self.color_dir
        )

    @staticmethod
    def _file_key(image_path: Path) -> str:
        try:
            return str(image_path.resolve()).lower()
        except OSError:
            return str(image_path.absolute()).lower()

    def _iter_unsubmitted_images(self, cage_number: int) -> list[Path]:
        color_dir = self._get_cage_color_dir(cage_number)
        if not color_dir.exists():
            return []

        submitted_keys = self.submitted_file_keys.setdefault(int(cage_number), set())
        now = time.time()
        candidates: list[Path] = []
        try:
            for pattern in ("*.jpg", "*.jpeg"):
                candidates.extend(color_dir.glob(pattern))
        except OSError as error:
            logger.debug(f"scan trajectory color dir failed cage={cage_number}: {error}")
            return []

        image_stats: list[tuple[float, str, Path]] = []
        for image_path in candidates:
            try:
                image_stats.append((image_path.stat().st_mtime, image_path.name, image_path))
            except OSError:
                continue

        ordered_images: list[Path] = []
        for mtime, _name, image_path in sorted(image_stats, key=lambda item: (item[0], item[1])):
            file_key = self._file_key(image_path)
            if file_key in submitted_keys:
                continue
            if now - mtime < self.file_min_age_seconds:
                continue
            ordered_images.append(image_path)
            if len(ordered_images) >= self.scan_limit_per_cage:
                break
        return ordered_images

    def _submit_disk_frames(self, target_cages: list[int]) -> int:
        submitted_count = 0
        for cage_number in target_cages:
            for image_path in self._iter_unsubmitted_images(cage_number):
                try:
                    stat_result = image_path.stat()
                except OSError:
                    continue
                payload = {
                    "file_path": str(image_path),
                    "image_path": str(image_path),
                    "frame_name": image_path.name,
                    "frame_key": image_path.name,
                    "frame_id": 0,
                    "timestamp": float(stat_result.st_mtime),
                    "submit_timestamp": time.time(),
                    "submit_monotonic_ns": time.monotonic_ns(),
                    "trajectory_priority": 5,
                    "source": "disk",
                }
                if self.trajectory_thread.submit_frame(cage_number, payload):
                    self.submitted_file_keys.setdefault(int(cage_number), set()).add(self._file_key(image_path))
                    submitted_count += 1
                break
        return submitted_count

    def _submit_memory_frames(self, target_cages: list[int]) -> int:
        if not target_cages:
            return 0

        submitted_count = 0
        now = time.time()
        submit_fps = self._effective_submit_fps(
            self._active_online_cage_count(target_cages, now)
        )
        submit_interval = 1.0 / max(submit_fps, 0.1)
        start_index = self.round_robin_offset % len(target_cages)
        ordered_cages = target_cages[start_index:] + target_cages[:start_index]
        self.round_robin_offset = (start_index + 1) % len(target_cages)

        for cage_number in ordered_cages:
            if now < self.next_submit_time_by_cage.get(int(cage_number), 0.0):
                continue

            metadata = shared_video_frame_store.peek_metadata(
                "deep_camera",
                cage_number,
            )
            if metadata is None:
                continue
            self.last_online_seen_by_cage[int(cage_number)] = now

            frame_id = int(metadata.get("frame_id", 0) or 0)
            timestamp = float(metadata.get("timestamp", now) or now)
            frame_version = self._frame_version(metadata)
            if frame_id > 0 and self._is_stale_frame_version(
                frame_version,
                self.last_considered_frame_versions.get(int(cage_number)),
            ):
                self.trajectory_thread.record_duplicate_frame(cage_number)
                self.next_submit_time_by_cage[int(cage_number)] = now + submit_interval
                continue
            if float(metadata.get("frame_age_seconds", 0.0) or 0.0) > self.trajectory_max_age_seconds:
                self.trajectory_thread.record_stale_frame(cage_number)
                self.last_considered_frame_versions[int(cage_number)] = frame_version
                self.next_submit_time_by_cage[int(cage_number)] = now + submit_interval
                continue

            if not self.trajectory_thread.try_reserve_cage_slot(cage_number):
                self.last_considered_frame_versions[int(cage_number)] = frame_version
                self.next_submit_time_by_cage[int(cage_number)] = now + submit_interval
                continue

            slot_reserved = True
            submitted = False
            try:
                read_start = time.perf_counter()
                frame_payload = shared_video_frame_store.read_frame(
                    "deep_camera",
                    cage_number,
                    max_age_seconds=self.trajectory_max_age_seconds,
                    expected_frame_key=(
                        int(metadata.get("camera_session_id", 0) or 0),
                        frame_id,
                    ),
                )
                shared_frame_read_ms = (time.perf_counter() - read_start) * 1000.0
                if frame_payload is None:
                    continue

                frame = frame_payload.get("frame")
                if frame is None:
                    continue

                frame_name = (
                    frame_payload.get("frame_name")
                    or f"cage{int(cage_number)}_{int(timestamp * 1000)}_f{frame_id}.jpg"
                )
                payload = dict(frame_payload)
                payload.update(
                    {
                        "frame": frame,
                        "frame_name": str(frame_name),
                        "frame_key": (
                            f"memory_{int(cage_number)}_"
                            f"s{int(frame_payload.get('camera_session_id', 0) or 0)}_"
                            f"f{frame_id}"
                        ),
                        "frame_id": frame_id,
                        "timestamp": timestamp,
                        "submit_timestamp": time.time(),
                        "submit_monotonic_ns": time.monotonic_ns(),
                        "trajectory_priority": 1,
                        "source": "memory",
                        "effective_submit_fps": submit_fps,
                        "shared_frame_read_ms": shared_frame_read_ms,
                    }
                )

                submitted = self.trajectory_thread.submit_reserved_frame(
                    cage_number,
                    payload,
                )
                slot_reserved = False
                if submitted:
                    if frame_id > 0:
                        self.last_submitted_frame_ids[int(cage_number)] = frame_id
                    submitted_count += 1
            finally:
                if slot_reserved:
                    self.trajectory_thread.cancel_reserved_cage_slot(cage_number)
                if frame_id > 0:
                    self.last_considered_frame_versions[int(cage_number)] = frame_version
                self.next_submit_time_by_cage[int(cage_number)] = now + submit_interval
        return submitted_count

    def dosomething(self):
        try:
            if self.trajectory_thread is None or not self.trajectory_thread.isRunning():
                return

            target_cages = sorted(self._get_target_cages())
            submitted_count = 0
            if self.input_mode == "memory":
                submitted_count += self._submit_memory_frames(target_cages)
                if self.enable_disk_backfill:
                    submitted_count += self._submit_disk_frames(target_cages)
            else:
                submitted_count += self._submit_disk_frames(target_cages)

            self.submitted_since_debug_log += submitted_count
            now_monotonic = time.monotonic()
            if (
                self.submitted_since_debug_log
                and now_monotonic - self.last_submit_debug_log_monotonic >= 5.0
            ):
                logger.debug(
                    f"submitted trajectory frames: count={self.submitted_since_debug_log}, "
                    f"interval_seconds={now_monotonic - self.last_submit_debug_log_monotonic:.1f}, "
                    f"mode={self.input_mode}, "
                    f"disk_backfill={self.enable_disk_backfill}"
                )
                self.submitted_since_debug_log = 0
                self.last_submit_debug_log_monotonic = now_monotonic
        except Exception as error:
            logger.error(
                f"trajectory submitter failed: {error} | {traceback.format_exc()}"
            )
        finally:
            time.sleep(self.poll_interval_seconds)


class AutoFitGraphicsView(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setResizeAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorViewCenter)
        self.setDragMode(QGraphicsView.DragMode.NoDrag)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_scene()

    def fit_scene(self):
        scene = self.scene()
        if scene is None:
            return

        rect = scene.sceneRect()
        if rect.isNull():
            rect = scene.itemsBoundingRect()
        if rect.isNull():
            return

        self.resetTransform()
        self.fitInView(rect, Qt.AspectRatioMode.KeepAspectRatio)
        self.centerOn(rect.center())


class DisplayPanel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 10)
        layout.setSpacing(8)

        self.group_box = QGroupBox("", self)
        self.group_box.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        group_layout = QVBoxLayout(self.group_box)
        group_layout.setContentsMargins(10, 10, 10, 10)
        group_layout.setSpacing(8)

        self.title_label = QLabel("", self.group_box)
        group_layout.addWidget(self.title_label)

        self.content_widget = QWidget(self.group_box)
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 0)

        self.graphics_view = AutoFitGraphicsView(self.content_widget)
        self.graphics_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.content_layout.addWidget(self.graphics_view)
        self._scene = QGraphicsScene(self.graphics_view)
        self.graphics_view.setScene(self._scene)
        self._pixmap_item = None
        self._last_pixmap_size: tuple[int, int] | None = None

        self.placeholder_label = QLabel("", self.content_widget)
        self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder_label.setWordWrap(True)
        self.placeholder_label.hide()
        self.content_layout.addWidget(self.placeholder_label)

        self.custom_widget = None

        group_layout.addWidget(self.content_widget, 1)
        layout.addWidget(self.group_box, 1)

    def set_title(self, text):
        self.title_label.setText(text)

    def _detach_custom_widget(self):
        if self.custom_widget is None:
            return

        widget = self.custom_widget
        widget.hide()
        self.content_layout.removeWidget(widget)
        widget.setParent(None)
        self.custom_widget = None

    def show_custom_widget(self, widget: QWidget):
        if widget is None:
            return

        self.custom_widget = widget
        if widget.parent() is not self.content_widget:
            widget.setParent(self.content_widget)
        if self.content_layout.indexOf(widget) == -1:
            self.content_layout.insertWidget(0, widget, 1)

        self.graphics_view.hide()
        self.placeholder_label.hide()
        widget.show()

    def _set_pixmap(self, pixmap: QPixmap):
        if self._pixmap_item is None:
            self._pixmap_item = self._scene.addPixmap(pixmap)
        else:
            self._pixmap_item.setPixmap(pixmap)

        pixmap_size = (pixmap.width(), pixmap.height())
        if pixmap_size != self._last_pixmap_size:
            self._scene.setSceneRect(QRectF(pixmap.rect()))
            self.graphics_view.fit_scene()
            self._last_pixmap_size = pixmap_size

    def _clear_pixmap(self):
        if self._pixmap_item is not None:
            self._pixmap_item.setPixmap(QPixmap())
        self._last_pixmap_size = None

    def _resize_frame_for_view(self, frame):
        if frame is None:
            return None
        viewport = self.graphics_view.viewport()
        max_width = max(int(viewport.width()), 1)
        max_height = max(int(viewport.height()), 1)
        if max_width <= 1 or max_height <= 1:
            max_width, max_height = 960, 540

        height, width = frame.shape[:2]
        scale = min(max_width / max(width, 1), max_height / max(height, 1), 1.0)
        if scale >= 0.98:
            return frame
        target_size = (max(int(width * scale), 1), max(int(height * scale), 1))
        return cv2.resize(frame, target_size, interpolation=cv2.INTER_AREA)

    def show_image(self, image_path):
        self._detach_custom_widget()
        self.placeholder_label.hide()
        self.graphics_view.show()

        if image_path:
            pixmap = QPixmap()
            try:
                with open(image_path, "rb") as image_file:
                    image_data = image_file.read()
                loaded = pixmap.loadFromData(image_data)
            except OSError:
                loaded = False
            if loaded and not pixmap.isNull():
                self._set_pixmap(pixmap)
                return

        self._clear_pixmap()
        self._scene.setSceneRect(
            0,
            0,
            max(self.graphics_view.viewport().width(), 1),
            max(self.graphics_view.viewport().height(), 1),
        )
        self.placeholder_label.setText("暂无画面")
        self.placeholder_label.show()
        self.graphics_view.hide()

    def show_frame(self, frame):
        timings = {
            "resize_ms": 0.0,
            "convert_ms": 0.0,
            "display_ms": 0.0,
        }
        self._detach_custom_widget()
        self.placeholder_label.hide()
        self.graphics_view.show()

        if frame is not None:
            try:
                stage_start = time.perf_counter()
                frame = self._resize_frame_for_view(frame)
                timings["resize_ms"] = (
                    time.perf_counter() - stage_start
                ) * 1000.0
                stage_start = time.perf_counter()
                if not frame.flags["C_CONTIGUOUS"]:
                    frame = np.ascontiguousarray(frame)

                height, width = frame.shape[:2]
                bytes_per_line = frame.strides[0]
                if len(frame.shape) == 2:
                    image = QImage(
                        frame.data,
                        width,
                        height,
                        bytes_per_line,
                        QImage.Format.Format_Grayscale8,
                    )
                else:
                    bgr_format = getattr(QImage.Format, "Format_BGR888", None)
                    if bgr_format is not None:
                        image = QImage(
                            frame.data,
                            width,
                            height,
                            bytes_per_line,
                            bgr_format,
                        )
                    else:
                        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
                        image = QImage(
                            rgb_frame.data,
                            width,
                            height,
                            rgb_frame.strides[0],
                            QImage.Format.Format_RGB888,
                        )

                pixmap = QPixmap.fromImage(image)
                timings["convert_ms"] = (
                    time.perf_counter() - stage_start
                ) * 1000.0
                if not pixmap.isNull():
                    stage_start = time.perf_counter()
                    self._set_pixmap(pixmap)
                    timings["display_ms"] = (
                        time.perf_counter() - stage_start
                    ) * 1000.0
                    return timings
            except (MemoryError, SystemError) as error:
                logger.warning(f"skip video frame display due to memory pressure: {error}")
                return timings
            except Exception as error:
                logger.debug(f"show video frame failed: {error}")

        self.show_placeholder("暂无画面")

        return timings

    def show_placeholder(self, text):
        self._detach_custom_widget()
        self._clear_pixmap()
        self._scene.setSceneRect(0, 0, 1, 1)
        self.graphics_view.hide()
        self.placeholder_label.setText(text)
        self.placeholder_label.show()


class TemperatureTrendWidget(QWidget):
    def __init__(self, parent=None, max_points: int = 120):
        super().__init__(parent)
        self.max_points = max_points
        self.handle: Monitor_Datas_Handle | None = None
        self.all_points: list[QPointF] = []
        self.view_start_index = 0
        self.current_cage_number: int | None = None
        self.auto_save_enabled = True
        self.last_auto_save_bucket_by_cage: dict[int, str] = {}
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.chart = QChart()
        self.chart.legend().hide()
        self.chart.setBackgroundRoundness(0)
        self.chart.setMargins(QMargins(8, 4, 8, 50))

        self.series = QScatterSeries()
        self.series.setName("红外最大温度")
        self.series.setMarkerShape(QScatterSeries.MarkerShape.MarkerShapeCircle)
        self.series.setMarkerSize(8.0)
        self.series.setColor(QColor("#ff6b35"))
        self.series.setBorderColor(QColor("#ff6b35"))
        self.series.setPen(QPen(QColor("#ff6b35"), 1))
        self.chart.addSeries(self.series)

        self.x_axis = QDateTimeAxis()
        self.x_axis.setFormat("HH:mm:ss")
        self.x_axis.setTitleText("时间")
        self.x_axis.setTickCount(6)

        self.y_axis = QValueAxis()
        self.y_axis.setLabelFormat("%.2f")
        self.y_axis.setTitleText("温度 (°C)")
        self.y_axis.setTickCount(6)

        self.chart.addAxis(self.x_axis, Qt.AlignmentFlag.AlignBottom)
        self.chart.addAxis(self.y_axis, Qt.AlignmentFlag.AlignLeft)
        self.series.attachAxis(self.x_axis)
        self.series.attachAxis(self.y_axis)

        self.chart_view = QChartView(self.chart, self)
        self.chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.chart_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.chart_view.setContentsMargins(0, 0, 0, 0)
        self.chart_view.setViewportMargins(0, 0, 0, 12)
        self.chart_view.setMinimumHeight(260)

        self.slider_row = QWidget(self)
        slider_row_layout = QHBoxLayout(self.slider_row)
        slider_row_layout.setContentsMargins(0, 0, 0, 0)
        slider_row_layout.setSpacing(8)

        self.slider_label = QLabel("时间范围", self.slider_row)
        self.slider_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        self.time_slider = QSlider(Qt.Orientation.Horizontal, self.slider_row)
        self.time_slider.setObjectName("temperature_time_slider")
        self.time_slider.setRange(0, 0)
        self.time_slider.setSingleStep(1)
        self.time_slider.setPageStep(max(1, self.max_points // 2))
        self.time_slider.setMinimumHeight(22)
        self.time_slider.setStyleSheet(
            """
            QSlider#temperature_time_slider::groove:horizontal {
                border: 1px solid #c7c7c7;
                height: 8px;
                background: #ececec;
                border-radius: 4px;
            }
            QSlider#temperature_time_slider::sub-page:horizontal {
                background: #ffb08f;
                border-radius: 4px;
            }
            QSlider#temperature_time_slider::handle:horizontal {
                background: #ff6b35;
                border: 1px solid #d95b2c;
                width: 18px;
                margin: -6px 0;
                border-radius: 9px;
            }
            QSlider#temperature_time_slider::handle:horizontal:hover {
                background: #ff814f;
            }
            """
        )
        self.time_slider.valueChanged.connect(self._on_slider_changed)

        self.slider_status_label = QLabel("", self.slider_row)
        self.slider_status_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)

        slider_row_layout.addWidget(self.slider_label)
        slider_row_layout.addWidget(self.time_slider, 1)
        slider_row_layout.addWidget(self.slider_status_label)
        self.slider_row.hide()

        self.placeholder_label = QLabel("暂无温度数据", self)
        self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder_label.hide()

        layout.addWidget(self.chart_view, 1)
        layout.addWidget(self.slider_row)
        layout.addWidget(self.placeholder_label, 1)
        self.setLayout(layout)
        self.clear_chart()

    def stop(self):
        if self.handle is not None:
            self.handle.stop()
            self.handle = None

    def _ensure_handle(self):
        if self.handle is None:
            self.handle = Monitor_Datas_Handle()

    @staticmethod
    def _parse_time(value):
        if not value:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(str(value), fmt)
            except ValueError:
                continue
        return None

    def clear_chart(self, text: str = "暂无温度数据"):
        self.series.clear()
        self.all_points = []
        self.view_start_index = 0
        self.current_cage_number = None
        self.chart.setTitle("")
        self.chart_view.hide()
        self.time_slider.setEnabled(False)
        self.slider_status_label.clear()
        self.slider_row.hide()
        self.placeholder_label.setText(text)
        self.placeholder_label.show()

    def _build_points(self, meta_data, rows):
        column_names = [item["name"] for item in meta_data]
        points = []
        for row in rows:
            row_data = dict(zip(column_names, row))
            time_value = self._parse_time(row_data.get("time"))
            temp_value = row_data.get("tmp_hs_max")
            if temp_value is None:
                temp_value = row_data.get("tmp_hs_mean")
            if time_value is None or temp_value is None:
                continue
            try:
                points.append(QPointF(time_value.timestamp() * 1000, float(temp_value)))
            except (TypeError, ValueError):
                continue

        points.sort(key=lambda point: point.x())
        return points

    def _configure_slider(self, keep_latest: bool):
        total_points = len(self.all_points)
        max_start = max(total_points - self.max_points, 0)
        start_index = max_start if keep_latest else min(self.view_start_index, max_start)

        self.time_slider.blockSignals(True)
        self.time_slider.setRange(0, max_start)
        self.time_slider.setPageStep(max(1, min(self.max_points, max_start if max_start > 0 else 1)))
        self.time_slider.setValue(start_index)
        self.time_slider.setEnabled(total_points > 0)
        self.time_slider.blockSignals(False)

        self.view_start_index = start_index
        self.slider_status_label.setText(
            f"{min(total_points, start_index + 1)}-{min(total_points, start_index + self.max_points)} / {total_points}"
        )
        if max_start > 0:
            self.time_slider.setToolTip("拖动这里查看完整温度趋势")
        else:
            self.time_slider.setToolTip("当前数据量还没有超过单屏展示范围")
        self.slider_status_label.setToolTip("当前显示区间 / 总数据点数")
        self.slider_row.setVisible(total_points > 0)

    def _update_axes(self, points):
        x_min = int(points[0].x())
        x_max = int(points[-1].x())
        if x_min == x_max:
            x_min -= 1000
            x_max += 1000
        self.x_axis.setRange(
            QDateTime.fromMSecsSinceEpoch(x_min),
            QDateTime.fromMSecsSinceEpoch(x_max),
        )

        y_values = [point.y() for point in points]
        y_min = min(y_values)
        y_max = max(y_values)
        padding = max((y_max - y_min) * 0.15, 0.5)
        if y_min == y_max:
            padding = max(padding, 1.0)
        self.y_axis.setRange(y_min - padding, y_max + padding)

    def _render_current_window(self):
        if not self.all_points:
            self.clear_chart("暂无温度数据")
            return

        end_index = self.view_start_index + self.max_points
        visible_points = self.all_points[self.view_start_index:end_index]
        if not visible_points:
            visible_points = self.all_points[-self.max_points:]
            self.view_start_index = max(len(self.all_points) - len(visible_points), 0)

        self.series.clear()
        for point in visible_points:
            self.series.append(point)

        self._update_axes(visible_points)
        if self.current_cage_number is not None:
            self.chart.setTitle(f"鼠笼{self.current_cage_number}红外最大温度趋势")
        self.placeholder_label.hide()
        self.chart_view.show()
        self.slider_row.show()

    def _get_auto_save_dir(self, cage_number: int) -> str:
        experiment_setting_file = global_setting.get_setting("experiment_setting_file", None)
        experiment_name = "experiment"
        if experiment_setting_file is not None and os.path.exists(experiment_setting_file):
            experiment_name = os.path.splitext(os.path.basename(experiment_setting_file))[0]

        storage_setting = global_setting.get_setting("monitor_data")["STORAGE"]
        experiment_start_time = global_setting.get_setting("start_experiment_time", time.time())
        experiment_folder = (
            f"{experiment_name}_{datetime.fromtimestamp(experiment_start_time).strftime('%Y_%m_%d_%H_%M_%S_%f')}"
        )
        save_dir = os.path.join(
            os.getcwd() + storage_setting["fold_path"],
            storage_setting["sub_fold_path"],
            experiment_folder,
            "temperature_charts",
            f"cage_{cage_number}",
        )
        os.makedirs(save_dir, exist_ok=True)
        return save_dir

    def _auto_save_chart(self):
        if not self.auto_save_enabled or self.current_cage_number is None or not self.all_points:
            return

        try:
            save_bucket = datetime.now().strftime("%Y_%m_%d_%H_%M")
            if self.last_auto_save_bucket_by_cage.get(self.current_cage_number) == save_bucket:
                return

            save_dir = self._get_auto_save_dir(self.current_cage_number)
            file_name = (
                f"temperature_trend_cage_{self.current_cage_number}_"
                f"{datetime.now().strftime('%Y_%m_%d_%H_%M_%S_%f')}.png"
            )
            file_path = os.path.join(save_dir, file_name)
            if self.chart_view.grab().save(file_path, "PNG"):
                self.last_auto_save_bucket_by_cage[self.current_cage_number] = save_bucket
        except Exception as e:
            logger.debug(f"auto save temperature chart failed for cage {self.current_cage_number}: {e}")

    def _on_slider_changed(self, value: int):
        self.view_start_index = value
        self._render_current_window()

    def refresh_data(self, cage_number: int | None):
        if cage_number is None:
            self.clear_chart("请选择已开启笼子")
            return

        keep_latest = (
            cage_number != self.current_cage_number
            or not self.all_points
            or self.time_slider.value() >= self.time_slider.maximum()
        )
        self.current_cage_number = cage_number

        table_name = f"MouseInfrared_data_cage_{cage_number}"
        try:
            self._ensure_handle()
            meta_data = self.handle.query_meta_table_data_all(table_name)
            rows = self.handle.query_data_all(table_name)
        except Exception as e:
            logger.debug(f"读取鼠笼{cage_number}红外温度趋势失败: {e}")
            self.clear_chart("暂无温度数据")
            return

        if not meta_data or not rows:
            self.clear_chart("暂无温度数据")
            return

        self.all_points = self._build_points(meta_data, rows)
        if not self.all_points:
            self.clear_chart("暂无温度数据")
            return

        self._configure_slider(keep_latest)
        self._render_current_window()
        self._auto_save_chart()


class TemperatureTrendWidgetV2(QWidget):
    def __init__(self, parent=None, max_points: int = 120):
        super().__init__(parent)
        self.max_points = max_points
        self.handle: Monitor_Datas_Handle | None = None
        self.all_points: list[QPointF] = []
        self.view_start_index = 0
        self.current_cage_number: int | None = None
        self.is_following_latest = True
        self.auto_save_enabled = True
        self.last_auto_save_bucket_by_cage: dict[int, str] = {}
        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        self.chart = QChart()
        self.chart.legend().hide()
        self.chart.setBackgroundRoundness(0)
        self.chart.setMargins(QMargins(8, 4, 8, 50))

        self.series = QScatterSeries()
        self.series.setName("红外最大温度")
        self.series.setMarkerShape(QScatterSeries.MarkerShape.MarkerShapeCircle)
        self.series.setMarkerSize(8.0)
        self.series.setColor(QColor("#ff6b35"))
        self.series.setBorderColor(QColor("#ff6b35"))
        self.series.setPen(QPen(QColor("#ff6b35"), 1))
        self.chart.addSeries(self.series)

        self.x_axis = QDateTimeAxis()
        self.x_axis.setFormat("HH:mm:ss")
        self.x_axis.setTitleText("时间")
        self.x_axis.setTickCount(6)

        self.y_axis = QValueAxis()
        self.y_axis.setLabelFormat("%.2f")
        self.y_axis.setTitleText("温度 (°C)")
        self.y_axis.setTickCount(6)

        self.chart.addAxis(self.x_axis, Qt.AlignmentFlag.AlignBottom)
        self.chart.addAxis(self.y_axis, Qt.AlignmentFlag.AlignLeft)
        self.series.attachAxis(self.x_axis)
        self.series.attachAxis(self.y_axis)

        self.chart_view = QChartView(self.chart, self)
        self.chart_view.setRenderHint(QPainter.RenderHint.Antialiasing)
        self.chart_view.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.chart_view.setContentsMargins(0, 0, 0, 0)
        self.chart_view.setViewportMargins(0, 0, 0, 24)
        self.chart_view.setMinimumHeight(260)

        self.slider_row = QWidget(self)
        self.slider_row.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.slider_row.setMinimumHeight(34)
        slider_row_layout = QHBoxLayout(self.slider_row)
        slider_row_layout.setContentsMargins(0, 0, 0, 0)
        slider_row_layout.setSpacing(8)

        self.slider_label = QLabel("历史窗口", self.slider_row)
        self.slider_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)

        self.time_slider = QSlider(Qt.Orientation.Horizontal, self.slider_row)
        self.time_slider.setObjectName("temperature_time_slider")
        self.time_slider.setRange(0, 0)
        self.time_slider.setSingleStep(1)
        self.time_slider.setPageStep(max(1, self.max_points // 2))
        self.time_slider.setMinimumHeight(22)
        self.time_slider.setStyleSheet(
            """
            QSlider#temperature_time_slider::groove:horizontal {
                border: 1px solid #c7c7c7;
                height: 8px;
                background: #ececec;
                border-radius: 4px;
            }
            QSlider#temperature_time_slider::sub-page:horizontal {
                background: #ffb08f;
                border-radius: 4px;
            }
            QSlider#temperature_time_slider::add-page:horizontal {
                background: #ececec;
                border-radius: 4px;
            }
            QSlider#temperature_time_slider::handle:horizontal {
                background: #ff6b35;
                border: 1px solid #d95b2c;
                width: 18px;
                margin: -6px 0;
                border-radius: 9px;
            }
            QSlider#temperature_time_slider::handle:horizontal:hover {
                background: #ff814f;
            }
            """
        )
        self.time_slider.valueChanged.connect(self._on_slider_changed)

        self.slider_status_label = QLabel("", self.slider_row)
        self.slider_status_label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)

        slider_row_layout.addWidget(self.slider_label)
        slider_row_layout.addWidget(self.time_slider, 1)
        slider_row_layout.addWidget(self.slider_status_label)
        self.slider_row.hide()

        self.placeholder_label = QLabel("暂无温度数据", self)
        self.placeholder_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder_label.hide()

        layout.addWidget(self.chart_view, 1)
        layout.addWidget(self.slider_row)
        layout.addWidget(self.placeholder_label, 1)
        self.setLayout(layout)
        self.clear_chart()

    def stop(self):
        if self.handle is not None:
            self.handle.stop()
            self.handle = None

    def _ensure_handle(self):
        if self.handle is None:
            self.handle = Monitor_Datas_Handle()

    @staticmethod
    def _parse_time(value):
        if not value:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                return datetime.strptime(str(value), fmt)
            except ValueError:
                continue
        return None

    def clear_chart(self, text: str = "暂无温度数据"):
        self.series.clear()
        self.all_points = []
        self.view_start_index = 0
        self.current_cage_number = None
        self.is_following_latest = True
        self.chart.setTitle("")
        self.chart_view.hide()
        self.time_slider.blockSignals(True)
        self.time_slider.setRange(0, 0)
        self.time_slider.setValue(0)
        self.time_slider.blockSignals(False)
        self.time_slider.setEnabled(False)
        self.slider_status_label.clear()
        self.slider_row.hide()
        self.placeholder_label.setText(text)
        self.placeholder_label.show()

    def _build_points(self, meta_data, rows):
        column_names = [item["name"] for item in meta_data]
        points = []
        for row in rows:
            row_data = dict(zip(column_names, row))
            time_value = self._parse_time(row_data.get("time"))
            temp_value = row_data.get("tmp_hs_max")
            if temp_value is None:
                temp_value = row_data.get("tmp_hs_mean")
            if time_value is None or temp_value is None:
                continue
            try:
                points.append(QPointF(time_value.timestamp() * 1000, float(temp_value)))
            except (TypeError, ValueError):
                continue

        points.sort(key=lambda point: point.x())
        return points

    def _current_window_bounds(self):
        total_points = len(self.all_points)
        if total_points == 0:
            return 0, 0

        if total_points <= self.max_points:
            return 0, total_points

        start_index = min(max(self.view_start_index, 0), total_points - self.max_points)
        end_index = start_index + self.max_points
        return start_index, end_index

    def _update_slider_status(self):
        total_points = len(self.all_points)
        if total_points == 0:
            self.slider_status_label.clear()
            return

        start_index, end_index = self._current_window_bounds()
        self.slider_status_label.setText(f"{start_index + 1}-{end_index} / {total_points}")

    def _configure_slider(self, keep_latest: bool):
        total_points = len(self.all_points)
        has_history = total_points > self.max_points
        max_start = max(total_points - self.max_points, 0)

        if not has_history:
            self.view_start_index = 0
            self.is_following_latest = True
        elif keep_latest:
            self.view_start_index = max_start
            self.is_following_latest = True
        else:
            self.view_start_index = min(self.view_start_index, max_start)
            self.is_following_latest = self.view_start_index >= max_start

        self.time_slider.blockSignals(True)
        self.time_slider.setRange(0, max_start)
        self.time_slider.setPageStep(max(1, self.max_points // 2))
        self.time_slider.setValue(self.view_start_index)
        self.time_slider.blockSignals(False)

        self.time_slider.setEnabled(has_history)
        self.time_slider.setToolTip("拖动这里查看更早的温度数据" if has_history else "当前数据量未超过 120 个点")
        self.slider_status_label.setToolTip("当前显示区间 / 总数据点数")
        self._update_slider_status()
        self.slider_row.setVisible(has_history)

    def _update_axes(self, points):
        x_min = int(points[0].x())
        x_max = int(points[-1].x())
        if x_min == x_max:
            x_min -= 1000
            x_max += 1000
        self.x_axis.setRange(
            QDateTime.fromMSecsSinceEpoch(x_min),
            QDateTime.fromMSecsSinceEpoch(x_max),
        )

        y_values = [point.y() for point in points]
        y_min = min(y_values)
        y_max = max(y_values)
        padding = max((y_max - y_min) * 0.15, 0.5)
        if y_min == y_max:
            padding = max(padding, 1.0)
        self.y_axis.setRange(y_min - padding, y_max + padding)

    def _render_current_window(self):
        if not self.all_points:
            self.clear_chart("暂无温度数据")
            return

        start_index, end_index = self._current_window_bounds()
        visible_points = self.all_points[start_index:end_index]
        if not visible_points:
            self.clear_chart("暂无温度数据")
            return

        self.view_start_index = start_index
        self.series.clear()
        for point in visible_points:
            self.series.append(point)

        self._update_axes(visible_points)
        self._update_slider_status()
        if self.current_cage_number is not None:
            self.chart.setTitle(f"鼠笼{self.current_cage_number}红外最大温度趋势")
        self.placeholder_label.hide()
        self.chart_view.show()
        self.slider_row.setVisible(len(self.all_points) > self.max_points)

    def _get_auto_save_dir(self, cage_number: int) -> str:
        experiment_setting_file = global_setting.get_setting("experiment_setting_file", None)
        experiment_name = "experiment"
        if experiment_setting_file is not None and os.path.exists(experiment_setting_file):
            experiment_name = os.path.splitext(os.path.basename(experiment_setting_file))[0]

        storage_setting = global_setting.get_setting("monitor_data")["STORAGE"]
        experiment_start_time = global_setting.get_setting("start_experiment_time", time.time())
        experiment_folder = (
            f"{experiment_name}_{datetime.fromtimestamp(experiment_start_time).strftime('%Y_%m_%d_%H_%M_%S_%f')}"
        )
        save_dir = os.path.join(
            os.getcwd() + storage_setting["fold_path"],
            storage_setting["sub_fold_path"],
            experiment_folder,
            "temperature_charts",
            f"cage_{cage_number}",
        )
        os.makedirs(save_dir, exist_ok=True)
        return save_dir

    def _auto_save_chart(self):
        if not self.auto_save_enabled or self.current_cage_number is None or not self.all_points:
            return

        try:
            save_bucket = datetime.now().strftime("%Y_%m_%d_%H_%M")
            if self.last_auto_save_bucket_by_cage.get(self.current_cage_number) == save_bucket:
                return

            save_dir = self._get_auto_save_dir(self.current_cage_number)
            file_name = (
                f"temperature_trend_cage_{self.current_cage_number}_"
                f"{datetime.now().strftime('%Y_%m_%d_%H_%M_%S_%f')}.png"
            )
            file_path = os.path.join(save_dir, file_name)
            if self.chart_view.grab().save(file_path, "PNG"):
                self.last_auto_save_bucket_by_cage[self.current_cage_number] = save_bucket
        except Exception as e:
            logger.debug(f"auto save temperature chart failed for cage {self.current_cage_number}: {e}")

    def _on_slider_changed(self, value: int):
        self.view_start_index = value
        self.is_following_latest = value >= self.time_slider.maximum()
        self._render_current_window()

    def refresh_data(self, cage_number: int | None):
        if cage_number is None:
            self.clear_chart("请选择已开启笼子")
            return

        keep_latest = cage_number != self.current_cage_number or self.is_following_latest or not self.all_points
        self.current_cage_number = cage_number

        table_name = f"MouseInfrared_data_cage_{cage_number}"
        try:
            self._ensure_handle()
            meta_data = self.handle.query_meta_table_data_all(table_name)
            rows = self.handle.query_data_all(table_name)
        except Exception as e:
            logger.debug(f"读取鼠笼{cage_number}红外温度趋势失败: {e}")
            self.clear_chart("暂无温度数据")
            return

        if not meta_data or not rows:
            self.clear_chart("暂无温度数据")
            return

        self.all_points = self._build_points(meta_data, rows)
        if not self.all_points:
            self.clear_chart("暂无温度数据")
            return

        self._configure_slider(keep_latest)
        self._render_current_window()
        self._auto_save_chart()


TemperatureTrendWidget = TemperatureTrendWidgetV2


class Tab_4(ThemedWindow):
    MODE_INFRARED = "infrared"
    MODE_VIDEO = "video"

    def __init__(self, parent=None, geometry: QRect = None, title="", display_mode: str = MODE_INFRARED):
        super().__init__()
        self.charts_list = []
        self.loader_thread: ImageLoaderThread | None = None
        self.trajectory_thread: MouseTrajectoryThread | None = None
        self.trajectory_submitter_thread: TrajectoryFrameSubmitterThread | None = None
        self.connected_trajectory_signal_thread: MouseTrajectoryThread | None = None
        self.trajectory_module_loader: TrajectoryModuleLoaderThread | None = None
        self.infrared_camera_read_SN_dialog_frame = None
        self.current_cage_number: int | None = None
        self.current_mode = display_mode if display_mode in {self.MODE_INFRARED, self.MODE_VIDEO} else self.MODE_INFRARED
        self.latest_image_paths = {"infrared_camera": {}}
        self.latest_video_frames: dict[int, dict[str, typing.Any]] = {}
        self.last_good_video_frames: dict[int, dict[str, typing.Any]] = {}
        self.latest_trajectory_plot_paths: dict[int, dict[str, str]] = {}
        self.last_good_trajectory_plot_paths: dict[int, dict[str, str]] = {}
        self.latest_trajectory_plot_revision: dict[int, int] = {}
        self.latest_trajectory_overlays: dict[int, dict[str, typing.Any]] = {}
        self.latest_trajectory_status: dict[int, str] = {}
        self.latest_trajectory_titles: dict[int, str] = {}
        self.latest_trajectory_progress_text: dict[int, str] = {}
        self.displayed_trajectory_plot_signatures: dict[tuple[int, str], tuple[str, float]] = {}
        self.current_right_plot_signature: tuple[str, float] | None = None
        self.last_good_corners: dict[int, list[dict[str, typing.Any]]] = {}
        self.last_mouse_boxes: dict[int, tuple[float, float, float, float]] = {}
        self.last_valid_mouse_overlays: dict[int, dict[str, typing.Any]] = {}
        self.last_valid_mouse_overlay_times: dict[int, float] = {}
        self.mouse_overlay_hold_seconds = 1.5
        self.current_trajectory_plot_key = "xy_trajectory"
        self.temperature_widget: TemperatureTrendWidget | None = None
        self.last_enabled_cages: list[int] = []
        self.last_selector_refresh_time = 0.0
        self.selector_refresh_interval = 1.0
        self.video_dirty = False
        self.last_video_render_time = 0.0
        self.video_render_interval_seconds = 0.05
        self.video_render_stats_interval_seconds = 5.0
        self.video_render_stats_last_monotonic = time.monotonic()
        self.video_render_count = 0
        self.video_render_total_ms = 0.0
        self.video_build_total_ms = 0.0
        self.video_resize_total_ms = 0.0
        self.video_convert_total_ms = 0.0
        self.video_display_total_ms = 0.0
        self.video_last_render_frame_id = 0
        self.video_last_render_frame_age_ms = 0.0

        self._init_ui(parent, geometry, title)
        self._init_customize_ui()
        self._init_function()
        self._init_style_sheet()
        self.video_render_timer = QTimer(self)
        self.video_render_timer.setInterval(33)
        self.video_render_timer.timeout.connect(self.render_video_if_dirty)
        self.video_render_timer.start()
        if self.current_mode == self.MODE_VIDEO:
            QTimer.singleShot(0, self.start_trajectory_thread)

    def showEvent(self, a0: typing.Optional[QtGui.QShowEvent]) -> None:
        logger.warning("tab4-show")
        self.start_loader_thread()
        self.refresh_cage_selector()
        self.render_selected_content()
        super().showEvent(a0)

    def hideEvent(self, a0: typing.Optional[QtGui.QHideEvent]) -> None:
        logger.warning("tab4-hide")
        if self.loader_thread is not None and self.loader_thread.isRunning():
            self.loader_thread.pause()
        super().hideEvent(a0)

    def closeEvent(self, a0: typing.Optional[QtGui.QCloseEvent]) -> None:
        logger.warning("tab4-close")
        self.stop_loader_thread()
        self.stop_trajectory_thread()
        if self.temperature_widget is not None:
            self.temperature_widget.stop()
        super().closeEvent(a0)

    def _init_ui(self, parent=None, geometry: QRect = None, title=""):
        if parent is not None and geometry is not None:
            self.setParent(parent)
            self.setGeometry(geometry)

        self.ui = Ui_tab4_window()
        self.ui.setupUi(self)

    def _init_customize_ui(self):
        self.init_auto_connect_ui()
        self.init_header_selectors()
        self.init_display_area()

    def init_auto_connect_ui(self):
        start_btn: QPushButton = self.findChild(QPushButton, "start_btn")
        stop_btn: QPushButton = self.findChild(QPushButton, "stop_btn")
        state_label: QLabel = self.findChild(QLabel, "state_label")
        for widget in (state_label, start_btn, stop_btn):
            if widget is not None:
                widget.hide()
        self.update_mode_specific_controls()

    def update_mode_specific_controls(self):
        infrared_camera_setting_btn: QPushButton = self.findChild(QPushButton, "infrared_camera_setting")
        if infrared_camera_setting_btn is None:
            return

        infrared_camera_setting_btn.setVisible(self.current_mode == self.MODE_INFRARED)
        if hasattr(self, "trajectory_plot_selector_label"):
            is_video_mode = self.current_mode == self.MODE_VIDEO
            self.trajectory_plot_selector_label.setVisible(is_video_mode)
            self.trajectory_plot_selector.setVisible(is_video_mode)

    def init_header_selectors(self):
        self.cage_selector_label = QLabel("已开启笼子:", self.ui.verticalLayoutWidget)
        self.cage_selector = QComboBox(self.ui.verticalLayoutWidget)
        self.cage_selector.setObjectName("enabled_cage_selector")
        self.cage_selector.setMinimumWidth(140)
        self.cage_selector.currentIndexChanged.connect(self.on_cage_changed)

        self.current_cage_label = QLabel("当前显示: 未选择", self.ui.verticalLayoutWidget)

        insert_index = max(self.ui.horizontalLayout.count() - 1, 0)
        self.ui.horizontalLayout.insertWidget(insert_index, self.cage_selector_label)
        self.ui.horizontalLayout.insertWidget(insert_index + 1, self.cage_selector)
        self.trajectory_plot_selector_label = QLabel("轨迹图", self.ui.verticalLayoutWidget)
        self.trajectory_plot_selector = QComboBox(self.ui.verticalLayoutWidget)
        self.trajectory_plot_selector.setObjectName("trajectory_plot_selector")
        self.trajectory_plot_selector.setMinimumWidth(150)
        self.trajectory_plot_selector.addItem("X-Y轨迹", "xy_trajectory")
        self.trajectory_plot_selector.addItem("高度轨迹", "height_trajectory")
        self.trajectory_plot_selector.addItem("停留热力图", "occupancy_heatmap")
        self.trajectory_plot_selector.currentIndexChanged.connect(self.on_trajectory_plot_changed)

        self.ui.horizontalLayout.insertWidget(insert_index + 2, self.current_cage_label)
        self.ui.horizontalLayout.insertWidget(insert_index + 3, self.trajectory_plot_selector_label)
        self.ui.horizontalLayout.insertWidget(insert_index + 4, self.trajectory_plot_selector)
        self.update_mode_specific_controls()

    def init_display_area(self):
        if hasattr(self.ui, "scrollArea"):
            self.ui.verticalLayout.removeWidget(self.ui.scrollArea)
            self.ui.scrollArea.setParent(None)

        self.monitor_content_widget = QWidget(self.ui.verticalLayoutWidget)
        self.monitor_content_widget.setObjectName("single_cage_monitor_content")
        self.monitor_content_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        content_layout = QHBoxLayout(self.monitor_content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)
        content_layout.setSpacing(12)

        self.left_panel = DisplayPanel(self.monitor_content_widget)
        self.right_panel = DisplayPanel(self.monitor_content_widget)
        self.temperature_widget = TemperatureTrendWidget()
        self.temperature_widget.hide()

        content_layout.addWidget(self.left_panel, 1)
        content_layout.addWidget(self.right_panel, 1)
        self.ui.verticalLayout.addWidget(self.monitor_content_widget, 1)

        self.refresh_cage_selector()
        self.render_selected_content()

    def start_loader_thread(self):
        self.refresh_cage_selector()
        if self.current_mode == self.MODE_VIDEO:
            self.start_trajectory_thread()
        if self.loader_thread is not None and self.loader_thread.isRunning():
            self.loader_thread.set_display_mode(self.current_mode)
            self.loader_thread.set_target_cage_number(self.current_cage_number)
            if self.loader_thread.isPaused():
                self.loader_thread.resume()
            return

        self.loader_thread = ImageLoaderThread(display_mode=self.current_mode)
        self.loader_thread.set_target_cage_number(self.current_cage_number)
        self.loader_thread.image_loaded.connect(self.update_image)
        self.loader_thread.start()

    def stop_loader_thread(self):
        if self.loader_thread is None:
            return

        if self.loader_thread.isRunning():
            self.loader_thread.stop()
            self.loader_thread.requestInterruption()
            self.loader_thread.wait(2000)

        self.loader_thread = None

    def _request_trajectory_module_load(self):
        global trajectory_module_loader

        should_start = False
        with trajectory_module_loader_lock:
            trajectory_class = mouse_trajectory_thread_class
            if trajectory_class is None:
                loader = trajectory_module_loader
                if loader is None or loader.isFinished():
                    loader = TrajectoryModuleLoaderThread()
                    trajectory_module_loader = loader
                    should_start = True

                if self.trajectory_module_loader is not loader:
                    loader.loaded.connect(self._on_trajectory_module_loaded)
                    loader.failed.connect(self._on_trajectory_module_load_failed)
                    self.trajectory_module_loader = loader
            else:
                loader = None

        if trajectory_class is not None:
            self.start_trajectory_thread()
            return

        if should_start:
            logger.info("loading mouse trajectory module in background")
            _emit_runtime_diagnostic(
                "trajectory_module_load",
                "action=start",
                logical_thread_name="gui_main",
            )
            loader.start()

    def _on_trajectory_module_loaded(self, _trajectory_class):
        self.trajectory_module_loader = None
        logger.info("mouse trajectory module loaded in background")
        _emit_runtime_diagnostic(
            "trajectory_module_load",
            "action=ready",
            logical_thread_name="gui_main",
        )
        if self.current_mode == self.MODE_VIDEO:
            self.start_trajectory_thread()

    def _on_trajectory_module_load_failed(self, error_message: str):
        self.trajectory_module_loader = None
        logger.error(f"mouse trajectory module unavailable: {error_message}")
        _emit_runtime_diagnostic(
            "trajectory_module_load",
            f"action=failed, error={error_message}",
            logical_thread_name="gui_main",
        )

    def _connect_trajectory_signal(self, trajectory_thread: MouseTrajectoryThread):
        if self.connected_trajectory_signal_thread is trajectory_thread:
            return
        if self.connected_trajectory_signal_thread is not None:
            try:
                self.connected_trajectory_signal_thread.trajectory_ready.disconnect(
                    self.update_trajectory_result
                )
            except (TypeError, RuntimeError):
                pass
        trajectory_thread.trajectory_ready.connect(self.update_trajectory_result)
        self.connected_trajectory_signal_thread = trajectory_thread

    def _disconnect_trajectory_signal(self):
        if self.connected_trajectory_signal_thread is None:
            return
        try:
            self.connected_trajectory_signal_thread.trajectory_ready.disconnect(
                self.update_trajectory_result
            )
        except (TypeError, RuntimeError):
            pass
        self.connected_trajectory_signal_thread = None

    def start_trajectory_thread(self):
        global shared_trajectory_thread
        global shared_trajectory_submitter_thread

        with trajectory_module_loader_lock:
            trajectory_class = mouse_trajectory_thread_class
        with trajectory_service_lock:
            existing_thread = shared_trajectory_thread
            needs_new_service = (
                existing_thread is None or not existing_thread.isRunning()
            )

        if needs_new_service and trajectory_class is None:
            self._request_trajectory_module_load()
            return

        with trajectory_service_lock:
            trajectory_thread = shared_trajectory_thread
            if trajectory_thread is not None and trajectory_thread.isRunning():
                is_finishing = (
                    not getattr(trajectory_thread, "accepting_frames", True)
                    or getattr(trajectory_thread, "finish_after_drain", False)
                    or getattr(trajectory_thread, "final_outputs_done", False)
                )
                if is_finishing:
                    logger.warning(
                        "shared mouse trajectory service is finishing; start is deferred"
                    )
                    return
                if trajectory_thread.isPaused():
                    trajectory_thread.resume()
            else:
                stale_submitter = shared_trajectory_submitter_thread
                if stale_submitter is not None and stale_submitter.isRunning():
                    stale_submitter.stop()
                    stale_submitter.requestInterruption()
                    stale_submitter.wait(2000)
                shared_trajectory_submitter_thread = None
                if trajectory_class is None:
                    QTimer.singleShot(0, self._request_trajectory_module_load)
                    return
                trajectory_thread = trajectory_class()
                shared_trajectory_thread = trajectory_thread
                trajectory_thread.start()
                logger.info(
                    f"shared mouse trajectory service started: object_id={id(trajectory_thread)}"
                )
                _emit_runtime_diagnostic(
                    "trajectory_service",
                    f"action=start, object_id={id(trajectory_thread)}",
                    logical_thread_name="gui_main",
                )

            trajectory_service_consumers.add(self)
            self.trajectory_thread = trajectory_thread
            self._connect_trajectory_signal(trajectory_thread)

            submitter = shared_trajectory_submitter_thread
            if submitter is None or not submitter.isRunning():
                submitter = TrajectoryFrameSubmitterThread(trajectory_thread)
                shared_trajectory_submitter_thread = submitter
                submitter.start()
            elif submitter.isPaused():
                submitter.resume()
            self.trajectory_submitter_thread = submitter
            _emit_runtime_diagnostic(
                "trajectory_service",
                f"action=attach_view, object_id={id(trajectory_thread)}, "
                f"consumer_count={len(trajectory_service_consumers)}",
                logical_thread_name="gui_main",
            )

    def start_trajectory_submitter_thread(self):
        self.start_trajectory_thread()

    def stop_trajectory_submitter_thread(self):
        global shared_trajectory_submitter_thread

        with trajectory_service_lock:
            other_consumers = [
                consumer
                for consumer in trajectory_service_consumers
                if consumer is not self
            ]
            if other_consumers:
                self.trajectory_submitter_thread = None
                return
            submitter = shared_trajectory_submitter_thread
            shared_trajectory_submitter_thread = None

        if submitter is not None and submitter.isRunning():
            submitter.stop()
            submitter.requestInterruption()
            self._wait_for_thread_responsive(submitter, 2000)
        self.trajectory_submitter_thread = None

    @staticmethod
    def _wait_for_thread_responsive(thread, timeout_ms: int) -> bool:
        if thread is None or not thread.isRunning():
            return True

        app = QApplication.instance()
        if app is None or QThread.currentThread() != app.thread():
            return thread.wait(timeout_ms)

        wait_loop = QEventLoop()
        poll_timer = QTimer()
        timeout_timer = QTimer()
        timeout_timer.setSingleShot(True)

        def stop_wait_if_finished():
            if not thread.isRunning():
                wait_loop.quit()

        poll_timer.timeout.connect(stop_wait_if_finished)
        timeout_timer.timeout.connect(wait_loop.quit)
        thread.finished.connect(wait_loop.quit)
        poll_timer.start(20)
        timeout_timer.start(max(int(timeout_ms), 0))
        wait_loop.exec(QEventLoop.ProcessEventsFlag.ExcludeUserInputEvents)
        poll_timer.stop()
        timeout_timer.stop()
        try:
            thread.finished.disconnect(wait_loop.quit)
        except (TypeError, RuntimeError):
            pass
        return not thread.isRunning()

    def stop_trajectory_thread(self) -> bool:
        global shared_trajectory_thread
        global shared_trajectory_submitter_thread

        with trajectory_service_lock:
            trajectory_thread = self.trajectory_thread
            trajectory_service_consumers.discard(self)
            self._disconnect_trajectory_signal()
            other_consumers = [
                consumer
                for consumer in trajectory_service_consumers
                if getattr(consumer, "trajectory_thread", None) is trajectory_thread
            ]
            if trajectory_thread is None or other_consumers:
                self.trajectory_thread = None
                self.trajectory_submitter_thread = None
                return True

            submitter = shared_trajectory_submitter_thread
            shared_trajectory_submitter_thread = None

        if submitter is not None and submitter.isRunning():
            submitter.stop()
            submitter.requestInterruption()
            self._wait_for_thread_responsive(submitter, 2000)

        if trajectory_thread.isRunning():
            trajectory_thread.request_finish_after_drain()
            camera_config = global_setting.get_setting("camera_config") or {}
            trajectory_config = (
                camera_config.get("MOUSE_TRAJECTORY", {})
                if isinstance(camera_config, dict)
                else {}
            )
            finalize_timeout_seconds = max(
                float(
                    trajectory_config.get(
                        "trajectory_finalize_timeout_seconds",
                        60,
                    )
                    or 60
                ),
                2.0,
            )
            self._wait_for_thread_responsive(
                trajectory_thread,
                int(finalize_timeout_seconds * 1000),
            )
        if trajectory_thread.isRunning():
            logger.error(
                "mouse trajectory finalization timed out; final trajectory output may be incomplete"
            )
            return False

        trajectory_thread.finalize_outputs()
        with trajectory_service_lock:
            if shared_trajectory_thread is trajectory_thread:
                shared_trajectory_thread = None
        self.trajectory_thread = None
        self.trajectory_submitter_thread = None
        logger.info(
            f"shared mouse trajectory service stopped: object_id={id(trajectory_thread)}"
        )
        _emit_runtime_diagnostic(
            "trajectory_service",
            f"action=stop, object_id={id(trajectory_thread)}",
            logical_thread_name="gui_main",
        )
        return True

    def pause_loader_thread(self):
        if self.loader_thread is None:
            return

        if self.loader_thread.isRunning() and not self.loader_thread.isPaused():
            self.loader_thread.pause()

    @staticmethod
    def _box_dict_to_xyxy(box_dict: dict[str, typing.Any] | None) -> tuple[float, float, float, float] | None:
        if not box_dict:
            return None
        xyxy = box_dict.get("xyxy")
        if not xyxy or len(xyxy) != 4:
            return None
        try:
            x1, y1, x2, y2 = (float(value) for value in xyxy)
        except (TypeError, ValueError):
            return None
        if x2 <= x1 or y2 <= y1:
            return None
        return x1, y1, x2, y2

    def _draw_cage_corners(self, frame, corners: list[dict[str, typing.Any]] | None):
        if frame is None or not corners or len(corners) < 4:
            return
        try:
            points = np.array([[float(point["x"]), float(point["y"])] for point in corners[:4]], dtype=np.int32)
            cv2.polylines(frame, [points], True, (0, 255, 255), 2)
        except Exception as error:
            logger.debug(f"draw cage corners failed: {error}")

    def _draw_mouse_box(
        self,
        frame,
        box_xyxy: tuple[float, float, float, float] | None,
        overlay: dict[str, typing.Any] | None,
    ):
        if frame is None or box_xyxy is None:
            return
        try:
            height, width = frame.shape[:2]
            x1, y1, x2, y2 = box_xyxy
            x1 = max(0, min(width - 1, int(round(x1))))
            y1 = max(0, min(height - 1, int(round(y1))))
            x2 = max(0, min(width - 1, int(round(x2))))
            y2 = max(0, min(height - 1, int(round(y2))))
            if x2 <= x1 or y2 <= y1:
                return
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
            solved = (overlay or {}).get("solved") or {}
            if solved:
                z_value = solved.get("Z_total", solved.get("Z", 0))
                label = f"X:{float(solved.get('X', 0)):.1f} Y:{float(solved.get('Y', 0)):.1f} Z:{float(z_value):.1f}"
            else:
                center_x = (x1 + x2) / 2
                center_y = (y1 + y2) / 2
                label = f"X:{center_x:.1f} Y:{center_y:.1f}"
            cv2.putText(frame, label, (x1, max(16, y1 - 6)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)
        except Exception as error:
            logger.debug(f"draw mouse box failed: {error}")

    def _get_display_mouse_overlay(
        self,
        cage_number: int,
        frame,
        frame_id: int,
    ) -> tuple[dict[str, typing.Any], tuple[float, float, float, float] | None]:
        # The trajectory pipeline consumes sampled in-memory camera frames asynchronously.
        # Do not project an old YOLO result onto the newest live preview frame.
        overlay = self.latest_trajectory_overlays.get(cage_number, {}) or {}
        overlay_frame_id = int(overlay.get("frame_id", 0) or 0)
        if frame_id > 0 and overlay_frame_id == frame_id:
            box_xyxy = self._box_dict_to_xyxy(overlay.get("mouse_box"))
            if box_xyxy is not None:
                return overlay, box_xyxy

        return {}, None

    def _build_video_display_frame(self, cage_number: int, frame_payload: dict[str, typing.Any] | None):
        if not frame_payload:
            return None
        frame = frame_payload.get("frame")
        if frame is None:
            return None
        try:
            display_frame = frame.copy()
        except (MemoryError, SystemError) as error:
            logger.warning(f"skip video overlay due to memory pressure: {error}")
            return frame
        overlay = self.latest_trajectory_overlays.get(cage_number, {}) or {}
        corners = overlay.get("corners") or self.last_good_corners.get(cage_number)
        self._draw_cage_corners(display_frame, corners)
        return display_frame

    def _show_trajectory_plot_if_changed(self, cage_number: int, plot_key: str, image_path: str) -> bool:
        if not image_path or not os.path.exists(image_path):
            return False
        try:
            signature = (image_path, os.path.getmtime(image_path))
        except OSError:
            return False
        cache_key = (int(cage_number), str(plot_key))
        if (
            self.displayed_trajectory_plot_signatures.get(cache_key) == signature
            and self.current_right_plot_signature == signature
        ):
            return True
        self.right_panel.show_image(image_path)
        self.displayed_trajectory_plot_signatures[cache_key] = signature
        self.current_right_plot_signature = signature
        return True

    def update_trajectory_result(self, result_dict):
        if not result_dict:
            return

        cage_number = int(result_dict.get("cage_number", 0) or 0)
        if cage_number <= 0:
            return

        try:
            plot_revision = int(result_dict.get("plot_revision", -1))
        except (TypeError, ValueError):
            plot_revision = -1
        current_plot_revision = self.latest_trajectory_plot_revision.get(
            cage_number,
            -1,
        )
        accept_plot_state = plot_revision >= current_plot_revision
        plot_paths = result_dict.get("plot_paths", {}) or {}
        plot_title = str(result_dict.get("plot_title", "") or "")
        if accept_plot_state:
            self.latest_trajectory_plot_revision[cage_number] = plot_revision
            self.latest_trajectory_plot_paths[cage_number] = plot_paths
            if plot_paths:
                self.last_good_trajectory_plot_paths[cage_number] = plot_paths
            if plot_title:
                self.latest_trajectory_titles[cage_number] = plot_title
        else:
            plot_title = self.latest_trajectory_titles.get(cage_number, "")
        if result_dict.get("event_type") == "plot_ready":
            if accept_plot_state and plot_title:
                self.latest_trajectory_progress_text[cage_number] = plot_title
            self.latest_trajectory_status[cage_number] = "plot_ready"
            if (
                self.current_mode == self.MODE_VIDEO
                and self.current_cage_number == cage_number
            ):
                self.video_dirty = True
            return
        pending_frames = int(result_dict.get("pending_frames", 0) or 0)
        processed_frames = int(
            result_dict.get("processed_frames_for_cage", result_dict.get("processed_frames", 0)) or 0
        )
        eta_seconds = result_dict.get("estimated_remaining_seconds")
        progress_text = plot_title or "轨迹绘制中"
        if pending_frames > 0:
            progress_text = f"{progress_text} | 待处理 {pending_frames} 帧 | 已处理 {processed_frames} 张"
            if eta_seconds is not None:
                try:
                    eta_seconds = max(float(eta_seconds), 0.0)
                    minutes = int(eta_seconds // 60)
                    seconds = int(eta_seconds % 60)
                    progress_text = f"{progress_text} | 预计 {minutes}分{seconds}秒"
                except (TypeError, ValueError):
                    pass
        elif processed_frames > 0:
            progress_text = f"{progress_text} | 已处理 {processed_frames} 张"
        dropped_frames = int(result_dict.get("dropped_frames_for_cage", 0) or 0)
        if dropped_frames > 0:
            progress_text = f"{progress_text} | dropped {dropped_frames}"
        self.latest_trajectory_progress_text[cage_number] = progress_text
        corners = result_dict.get("corners")
        if corners:
            self.last_good_corners[cage_number] = corners
        overlay = {
            "status": result_dict.get("status", ""),
            "frame_id": int(result_dict.get("frame_id", 0) or 0),
            "mouse_box": result_dict.get("mouse_box"),
            "corners": corners or self.last_good_corners.get(cage_number),
            "solved": result_dict.get("solved"),
        }
        self.latest_trajectory_overlays[cage_number] = overlay
        box_xyxy = self._box_dict_to_xyxy(overlay.get("mouse_box"))
        if box_xyxy is not None:
            self.last_valid_mouse_overlays[cage_number] = overlay
            self.last_valid_mouse_overlay_times[cage_number] = time.time()
        self.latest_trajectory_status[cage_number] = result_dict.get("status", "")

        if self.current_mode == self.MODE_VIDEO and self.current_cage_number == cage_number:
            self.video_dirty = True

    def _build_cage_image_map(self, image_paths, prefix):
        cage_image_map = {}
        for image_path in image_paths:
            cage_number = self._extract_cage_number(image_path, prefix)
            if cage_number is not None:
                cage_image_map[cage_number] = image_path
        return cage_image_map

    @staticmethod
    def _extract_cage_number(image_path, prefix):
        if not image_path:
            return None

        match = re.search(rf"{re.escape(prefix)}(\d+)", image_path)
        if match is None:
            return None

        try:
            return int(match.group(1))
        except ValueError:
            return None

    def get_enabled_cages(self):
        mouse_cages = global_setting.get_setting("mouse_cages", None)
        if mouse_cages:
            return sorted({int(cage) for cage in mouse_cages})

        experiment_setting = global_setting.get_setting("experiment_setting", None)
        if experiment_setting is not None and getattr(experiment_setting, "groups", None):
            enabled_cages = [
                int(group.id)
                for group in experiment_setting.groups
                if getattr(group, "is_selected", 0) == 1
            ]
            if enabled_cages:
                return sorted(set(enabled_cages))

        available_cages = set(self.latest_video_frames.keys())
        available_cages.update(self.latest_image_paths["infrared_camera"].keys())
        return sorted(available_cages)

    def _get_selector_cages(self) -> list[int]:
        cages: list[int] = []
        for index in range(self.cage_selector.count()):
            cage_number = self.cage_selector.itemData(index)
            if cage_number is None:
                continue
            cages.append(int(cage_number))
        return cages

    def refresh_cage_selector(self):
        enabled_cages = self.get_enabled_cages()
        previous_cage = self.current_cage_number

        if not enabled_cages:
            self.current_cage_number = None
            self.last_enabled_cages = []
            self.cage_selector.blockSignals(True)
            self.cage_selector.clear()
            self.cage_selector.blockSignals(False)
            self.cage_selector.setEnabled(False)
            self.current_cage_label.setText("当前显示: 未选择")
            return

        if self.cage_selector.view().isVisible():
            return

        current_selector_cages = self._get_selector_cages()
        if current_selector_cages != enabled_cages:
            self.cage_selector.blockSignals(True)
            self.cage_selector.clear()
            for cage_number in enabled_cages:
                self.cage_selector.addItem(f"鼠笼{cage_number}", cage_number)
            self.cage_selector.blockSignals(False)
            self.last_enabled_cages = list(enabled_cages)

        self.cage_selector.setEnabled(True)
        target_cage = previous_cage if previous_cage in enabled_cages else enabled_cages[0]
        index = self.cage_selector.findData(target_cage)
        if index >= 0 and index != self.cage_selector.currentIndex():
            self.cage_selector.setCurrentIndex(index)
        self.current_cage_number = target_cage
        if self.loader_thread is not None:
            self.loader_thread.set_target_cage_number(self.current_cage_number)

    def refresh_cage_selector_if_due(self):
        now = time.time()
        if now - self.last_selector_refresh_time < self.selector_refresh_interval:
            return
        self.last_selector_refresh_time = now
        self.refresh_cage_selector()

    def render_video_if_dirty(self):
        if self.current_mode != self.MODE_VIDEO or not self.video_dirty:
            return
        now = time.time()
        if now - self.last_video_render_time < self.video_render_interval_seconds:
            return
        self.last_video_render_time = now
        self.video_dirty = False
        self.render_selected_content()

    def on_cage_changed(self, index):
        if index < 0:
            return

        self.current_cage_number = self.cage_selector.itemData(index)
        if self.loader_thread is not None:
            self.loader_thread.set_target_cage_number(self.current_cage_number)
        self.render_selected_content()

    def on_trajectory_plot_changed(self, index):
        if index < 0:
            return

        self.current_trajectory_plot_key = self.trajectory_plot_selector.itemData(index) or "xy_trajectory"
        self.render_selected_content()

    def set_display_mode(self, mode: str):
        if mode not in {self.MODE_INFRARED, self.MODE_VIDEO}:
            return

        self.current_mode = mode
        if self.current_mode == self.MODE_VIDEO:
            self.start_trajectory_thread()
        if self.loader_thread is not None:
            self.loader_thread.set_display_mode(mode)
            self.loader_thread.set_target_cage_number(self.current_cage_number)
        self.update_mode_specific_controls()
        self.render_selected_content()

    def update_image(self, pixmap_path_dict):
        if pixmap_path_dict is None or "infrared_camera" not in pixmap_path_dict:
            logger.error("未获取到图片数据")
            return

        video_frames = dict(pixmap_path_dict.get("deep_camera_frames", {}) or {})
        if video_frames:
            self.latest_video_frames.update(video_frames)
            self.last_good_video_frames.update(video_frames)
        self.latest_image_paths = {
            "infrared_camera": self._build_cage_image_map(
                pixmap_path_dict["infrared_camera"],
                global_setting.get_setting("camera_config")["INFRARED_CAMERA"]["mouse_cage_prefix"],
            ),
        }

        self.refresh_cage_selector_if_due()
        if self.current_mode == self.MODE_VIDEO:
            self.video_dirty = True
        else:
            self.render_selected_content()

    def _record_video_render_runtime(
        self,
        cage_number: int,
        frame_payload: dict[str, typing.Any] | None,
        render_ms: float,
        build_ms: float = 0.0,
        display_timings: dict[str, float] | None = None,
    ):
        self.video_render_count += 1
        self.video_render_total_ms += max(float(render_ms), 0.0)
        self.video_build_total_ms += max(float(build_ms), 0.0)
        display_timings = display_timings or {}
        self.video_resize_total_ms += max(
            float(display_timings.get("resize_ms", 0.0) or 0.0),
            0.0,
        )
        self.video_convert_total_ms += max(
            float(display_timings.get("convert_ms", 0.0) or 0.0),
            0.0,
        )
        self.video_display_total_ms += max(
            float(display_timings.get("display_ms", 0.0) or 0.0),
            0.0,
        )
        if frame_payload:
            self.video_last_render_frame_id = int(
                frame_payload.get("frame_sequence", frame_payload.get("frame_id", 0)) or 0
            )
            self.video_last_render_frame_age_ms = (
                float(frame_payload.get("frame_age_seconds", 0.0) or 0.0) * 1000.0
            )

        now_monotonic = time.monotonic()
        stats_elapsed = now_monotonic - self.video_render_stats_last_monotonic
        if stats_elapsed < self.video_render_stats_interval_seconds:
            return

        average_render_ms = (
            self.video_render_total_ms / self.video_render_count
            if self.video_render_count
            else 0.0
        )
        sample_count = max(self.video_render_count, 1)
        _emit_runtime_diagnostic(
            "video_render_runtime",
            f"selected_cage={cage_number}, "
            f"render_fps={self.video_render_count / max(stats_elapsed, 0.001):.2f}, "
            f"render_avg_ms={average_render_ms:.2f}, "
            f"overlay_build_avg_ms={self.video_build_total_ms / sample_count:.2f}, "
            f"resize_avg_ms={self.video_resize_total_ms / sample_count:.2f}, "
            f"qimage_convert_avg_ms={self.video_convert_total_ms / sample_count:.2f}, "
            f"graphics_display_avg_ms={self.video_display_total_ms / sample_count:.2f}, "
            f"frame_age_ms={self.video_last_render_frame_age_ms:.1f}, "
            f"frame_id={self.video_last_render_frame_id}",
            logical_thread_name="gui_main",
        )
        self.video_render_stats_last_monotonic = now_monotonic
        self.video_render_count = 0
        self.video_render_total_ms = 0.0
        self.video_build_total_ms = 0.0
        self.video_resize_total_ms = 0.0
        self.video_convert_total_ms = 0.0
        self.video_display_total_ms = 0.0

    def render_selected_content(self):
        if self.current_cage_number is None:
            self.left_panel.set_title("左侧")
            self.right_panel.set_title("右侧")
            self.left_panel.show_placeholder("请选择已开启笼子")
            self.right_panel.show_placeholder("请选择已开启笼子")
            return

        cage_number = int(self.current_cage_number)
        self.current_cage_label.setText(f"当前显示: 鼠笼{cage_number}")

        if self.current_mode == self.MODE_VIDEO:
            render_start = time.perf_counter()
            self.left_panel.set_title(f"视频检测效果 - 鼠笼{cage_number}")
            frame_payload = self.latest_video_frames.get(cage_number) or self.last_good_video_frames.get(cage_number)
            build_start = time.perf_counter()
            display_frame = self._build_video_display_frame(cage_number, frame_payload)
            build_ms = (time.perf_counter() - build_start) * 1000.0
            display_timings = {}
            if display_frame is not None:
                display_timings = self.left_panel.show_frame(display_frame) or {}
            else:
                self.left_panel.show_placeholder("暂无画面")

            self._record_video_render_runtime(
                cage_number,
                frame_payload,
                (time.perf_counter() - render_start) * 1000.0,
                build_ms,
                display_timings,
            )

            plot_paths = (
                self.latest_trajectory_plot_paths.get(cage_number, {})
                or self.last_good_trajectory_plot_paths.get(cage_number, {})
            )
            selected_plot_path = plot_paths.get(self.current_trajectory_plot_key, "")
            plot_label = self.trajectory_plot_selector.currentText() if hasattr(self, "trajectory_plot_selector") else "X-Y轨迹"
            trajectory_title = (
                self.latest_trajectory_progress_text.get(cage_number)
                or self.latest_trajectory_titles.get(cage_number, "轨迹绘制中")
            )
            self.right_panel.set_title(f"{plot_label} - 鼠笼{cage_number} | {trajectory_title}")
            if not self._show_trajectory_plot_if_changed(
                cage_number,
                self.current_trajectory_plot_key,
                selected_plot_path,
            ):
                self.right_panel.show_placeholder(
                    self.latest_trajectory_progress_text.get(cage_number, "轨迹绘制中")
                )
            return

        self.left_panel.set_title(f"红外相机 - 鼠笼{cage_number}")
        self.left_panel.show_image(
            self.latest_image_paths["infrared_camera"].get(cage_number, "")
        )
        self.right_panel.set_title(f"温度 - 鼠笼{cage_number}")
        if self.temperature_widget is not None:
            self.temperature_widget.refresh_data(cage_number)
            self.right_panel.show_custom_widget(self.temperature_widget)
        else:
            self.right_panel.show_placeholder("暂无温度数据")

