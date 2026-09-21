import os
import multiprocessing
import queue
import secrets
import sys
import time
import threading
import traceback
from collections import deque
from pathlib import Path
from typing import Any

import cv2
from PyQt6.QtCore import QCoreApplication, QTimer
from loguru import logger

from public.config_class import global_load
from public.config_class.global_setting import global_setting
from public.entity.MyQThread import MyQThread
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.system_health import HealthPolicy, HealthReporter
from public.util.folder_util import folder_util
from public.util.ffmpeg_video_recorder import (
    FFmpegVideoRecorderConfig,
    FFmpegVideoRecorderThread,
)
from public.util.json_util import json_util
from public.util.project_paths import resolve_project_path
from public.util.shared_video_frames import shared_video_frame_store
from public.util.time_util import time_util
from public.util.uvc_camera_util import (
    camera_config_identity,
    camera_logical_index,
    enumerate_uvc_cameras,
    resolve_camera_config,
)
from public.util.video_program_instance_lock import VideoProgramInstanceLock


logged_errors = set()
delete_file_thread = None
save_frame_thread = None
video_recorder_thread = None
camera_list = []
frame_nums = 0
camera_instance_lock = None
camera_lock_retry_timer = None
camera_lock_state = "starting"
camera_lock_owner = {}
camera_session_state = "stopped"
camera_session_state_lock = threading.Lock()
lock = threading.Lock()
delete_process_lock = threading.Lock()
runtime_diagnostics_drop_lock = threading.Lock()
runtime_diagnostics_dropped = 0
camera_connect_lock = threading.Lock()
experiment_running = False
# Image files may be deleted by the cleanup thread while capture is still writing.
file_locks = {}


# Health snapshots use their own optional queue.  Do not put these records on
# the runtime-diagnostics queue: that queue is consumed by the process logger.
HEALTH_SUCCESS_REPORT_INTERVAL_SECONDS = 2.0
_health_snapshot_queue = None
_health_reporters = {}
_health_reporters_lock = threading.RLock()


def _set_health_snapshot_queue(health_snapshot_queue):
    global _health_snapshot_queue, _health_reporters
    with _health_reporters_lock:
        _health_snapshot_queue = health_snapshot_queue
        _health_reporters = {}


def _health_publish_interval_seconds():
    try:
        health_config = (global_setting.get_setting("configer", {}) or {}).get("system_health", {})
        return max(float(health_config.get("publish_interval_seconds", HEALTH_SUCCESS_REPORT_INTERVAL_SECONDS)), 0.1)
    except (AttributeError, TypeError, ValueError):
        return HEALTH_SUCCESS_REPORT_INTERVAL_SECONDS


def _health_policy():
    try:
        health_config = (global_setting.get_setting("configer", {}) or {}).get("system_health", {})
    except (AttributeError, TypeError):
        health_config = {}
    try:
        failure_threshold = max(int(float(health_config.get("failure_threshold", 3))), 1)
    except (TypeError, ValueError):
        failure_threshold = 3
    try:
        recovery_threshold = max(int(float(health_config.get("recovery_success_threshold", 2))), 1)
    except (TypeError, ValueError):
        recovery_threshold = 2
    return HealthPolicy(
        failure_threshold=failure_threshold,
        recovery_success_threshold=recovery_threshold,
    )


def _health_reporter(component, scope="global"):
    if _health_snapshot_queue is None:
        return None
    key = (str(component), str(scope))
    with _health_reporters_lock:
        reporter = _health_reporters.get(key)
        if reporter is None:
            reporter = HealthReporter(
                _health_snapshot_queue,
                component=key[0],
                scope=key[1],
                policy=_health_policy(),
            )
            _health_reporters[key] = reporter
        return reporter


def _channel_health_scope(cage_number):
    try:
        return f"CH{int(cage_number):02d}"
    except (TypeError, ValueError):
        return str(cage_number)


def _emit_runtime_diagnostic(event: str, message: str):
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
                    "source": "deep_camera",
                    "event": str(event),
                    "pid": os.getpid(),
                    "process_name": multiprocessing.current_process().name,
                    "thread_name": threading.current_thread().name,
                    "message": record_message,
                }
            )
            runtime_diagnostics_dropped = 0
        except queue.Full:
            runtime_diagnostics_dropped = dropped_count + 1
        except (BrokenPipeError, EOFError, OSError):
            runtime_diagnostics_dropped = dropped_count + 1


def _camera_config():
    return global_setting.get_setting("camera_config")


def _deep_camera_config():
    return _camera_config()["DEEP_CAMERA"]


def _as_bool(value, default=False):
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


def _save_raw_frames():

    config = _camera_config() or {}
    deep_config = config.get("DEEP_CAMERA", {}) if isinstance(config, dict) else {}
    trajectory_config = config.get("MOUSE_TRAJECTORY", {}) if isinstance(config, dict) else {}
    return _as_bool(
        deep_config.get("save_raw_frames", trajectory_config.get("save_raw_frames", False)),
        False,
    )


def _deep_delete_interval_seconds():
    deep_config = _deep_camera_config()
    return float(
        deep_config.get(
            "delete_interval_seconds",
            _camera_config()["DELETE"]["interval_seconds"],
        )
    )


def _deep_delete_delay():
    deep_config = _deep_camera_config()
    return float(
        deep_config.get(
            "delete_delay",
            _camera_config()["DELETE"]["delay"],
        )
    )


def _raw_save_interval_seconds():
    return float(_deep_camera_config().get("raw_save_interval_seconds", 1.0))


def _color_jpg_size():
    try:
        return max(int(float(_deep_camera_config().get("color_jpg_size", 640) or 640)), 1)
    except Exception:
        return 640


def _color_jpg_quality():
    try:
        return max(1, min(100, int(float(_deep_camera_config().get("color_jpg_quality", 85) or 85))))
    except Exception:
        return 85


def _record_video_enabled():

    return _as_bool(_deep_camera_config().get("record_video", False), False)


def _video_recorder_config():
    config = _deep_camera_config()
    return FFmpegVideoRecorderConfig(
        ffmpeg_path=str(config.get("ffmpeg_path", "") or ""),
        output_dir_name=str(config.get("video_dir", "video") or "video"),
        fps=float(config.get("video_fps", 10) or 10),
        width=int(float(config.get("video_width", 480) or 480)),
        height=int(float(config.get("video_height", 270) or 270)),
        bitrate_kbps=int(float(config.get("video_bitrate_kbps", 100) or 100)),
        maxrate_kbps=int(float(config.get("video_maxrate_kbps", 120) or 120)),
        bufsize_kbps=int(float(config.get("video_bufsize_kbps", 240) or 240)),
        preset=str(config.get("video_preset", "ultrafast") or "ultrafast"),
        encoder_threads=int(float(config.get("video_encoder_threads", 1) or 1)),
        fragment_seconds=float(config.get("video_fragment_seconds", 2) or 2),
        stats_interval_seconds=float(config.get("video_stats_interval_seconds", 5) or 5),
    )


def _storage_root():
    config = _camera_config()
    storage_path = config.get("STORAGE", {}).get("fold_path", "./data/")
    deep_camera_path = config.get("DEEP_CAMERA", {}).get("path", "deep_camera/")
    return str(resolve_project_path(storage_path) / str(deep_camera_path).strip("/\\"))


def _camera_base_path(mouse_cage_number):
    deep_config = _deep_camera_config()
    return os.path.join(
        _storage_root(),
        f"{deep_config['mouse_cage_prefix']}{mouse_cage_number}",
    ) + "/"


class read_queue_data_Thread(MyQThread):
    def __init__(self, name):
        super().__init__(name)
        self.queue = None
        self.camera_list = None

    def dosomething(self):
        global experiment_running
        # This thread only consumes messages addressed to main_deep_camera.
        if self.queue is None or self.queue.empty():
            time.sleep(0.05)
            return

        try:
            message: ObjectQueueItem = self.queue.get()
        except Exception as e:
            logger.error(f"{self.name} receive queue message failed: {e}")
            return

        if message is None or message.is_Empty():
            return

        if not isinstance(message, ObjectQueueItem) or message.to != "main_deep_camera":
            self.queue.put(message)
            time.sleep(0.02)
            return

        logger.info(f"{self.name} message: {message}")

        match message.title:
            case "stop_running_cameras":
                _stop_camera_threads(self.camera_list or [])
            case "start":
                data = message.data or {}
                global_setting.set_setting(
                    "behavior_feature_enabled", bool(data.get("behavior_enabled", False))
                )
                global_setting.set_setting(
                    "start_experiment_time",
                    data.get("start_experiment_time", time.time()),
                )
                global_setting.set_setting(
                    "pause_experiment_time",
                    data.get("pause_experiment_time", []),
                )
                global_setting.set_setting(
                    "relieve_pause_experiment_time",
                    data.get("relieve_pause_experiment_time", []),
                )
                experiment_running = True
                start()
            case "pause":
                pause()
            case "stop":
                data = message.data or {}
                global_setting.set_setting(
                    "stop_experiment_time",
                    data.get("stop_experiment_time", time.time()),
                )
                experiment_running = False
                stop()
            case "experiment_setting":
                data = message.data or {}
                global_setting.set_setting("experiment_setting", data.get("experiment_setting"))
                global_setting.set_setting(
                    "experiment_setting_file",
                    data.get("experiment_setting_file", ""),
                )
            case "camera_config":
                if experiment_running and message.data:
                    init_camera_and_image_handle_thread(message.data)
                else:
                    logger.info("deep camera config saved; camera startup deferred until experiment start")
            case _:
                pass


read_queue_data_thread = read_queue_data_Thread(name="main_deep_camera_read_queue_data_thread")


class Delete_file(MyQThread):
    def __init__(self, path, start_time):
        super().__init__(name="deep_camera_delete_file")
        self.path = path
        self.start_time = start_time

    def _is_raw_color_file(self, file_path):
        try:
            color_dir = str(_deep_camera_config().get("color_dir", "color") or "color").strip("/\\").lower()
            return os.path.basename(os.path.dirname(file_path)).lower() == color_dir
        except Exception:
            return False

    def _is_video_output_file(self, file_path):
        try:
            video_dir = str(_deep_camera_config().get("video_dir", "video") or "video").strip("/\\").lower()
            return os.path.basename(os.path.dirname(file_path)).lower() == video_dir
        except Exception:
            return False

    def get_and_delete_files(self):
        global file_locks, frame_nums

        total_size_gb = 0.0
        total_nums = 0
        location_filename = _deep_camera_config().get("location_filename", "")

        for root, _dirs, files in os.walk(self.path):
            for file_name in files:
                ext = os.path.splitext(file_name)[1].lower()
                if location_filename and location_filename in file_name:
                    continue

                file_path = os.path.join(root, file_name)
                if self._is_raw_color_file(file_path) or self._is_video_output_file(file_path):
                    continue

                try:
                    total_size_gb += os.path.getsize(file_path) / 1024 / 1024 / 1024
                    total_nums += 1

                    # Color frames are deleted only after YOLO history output has been saved.
                    if ext in [".bmp", ".png", ".jpg", ".jpeg"] and file_path in file_locks:
                        with file_locks[file_path]:
                            os.remove(file_path)
                        del file_locks[file_path]
                    else:
                        os.remove(file_path)
                except Exception as e:
                    logger.trace(
                        f"deep_camera delete file failed: {file_path}, reason: {e}, traceback: {traceback.format_exc()}"
                    )

        with lock:
            logger.warning(
                f"deep_camera delete files: total_size={total_size_gb:.3f}GB, total_count={total_nums}, captured_frames={frame_nums}"
            )
            frame_nums = 0

        return total_size_gb

    def dosomething(self):
        try:
            with delete_process_lock:
                current_time = time.time()
                elapsed = current_time - self.start_time
                if elapsed >= _deep_delete_interval_seconds():
                    self.get_and_delete_files()
                    logger.info("deep_camera delete files finished")
                    self.start_time = time.time()

            time.sleep(_deep_delete_delay())
        except Exception as e:
            logger.error(f"deep_camera delete thread failed: {e} | {traceback.format_exc()}")


class SaveRawFrameThread(MyQThread):
    def __init__(self, max_pending: int = 64):
        super().__init__(name="deep_camera_save_raw_frame")
        self.max_pending = max_pending
        self.pending_items: deque[tuple[str, str, Any]] = deque()
        self.pending_lock = threading.Lock()
        self.dropped_count = 0

    def submit_frame(self, output_path: str, file_name: str, frame):
        if not output_path or frame is None:
            return
        with self.pending_lock:
            if len(self.pending_items) >= self.max_pending:
                self.pending_items.popleft()
                self.dropped_count += 1
            self.pending_items.append((output_path, file_name, frame.copy()))

    def dosomething(self):
        item = None
        with self.pending_lock:
            if self.pending_items:
                item = self.pending_items.popleft()

        if item is None:
            time.sleep(0.01)
            return

        output_path, file_name, frame = item
        folder_path = os.path.dirname(output_path)
        if folder_path and not os.path.exists(folder_path):
            os.makedirs(folder_path, exist_ok=True)

        file_lock = file_locks.setdefault(output_path, threading.Lock())
        with file_lock:
            target_size = _color_jpg_size()
            quality = _color_jpg_quality()
            frame_to_save = cv2.resize(frame, (target_size, target_size), interpolation=cv2.INTER_AREA)
            ok, encoded = cv2.imencode(".jpg", frame_to_save, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
            if not ok:
                logger.error(f"deep_camera encode color jpg failed: {output_path}")
                return

            temp_path = f"{output_path}.tmp"
            with open(temp_path, "wb") as file:
                file.write(encoded.tobytes())
            os.replace(temp_path, output_path)


def parse_uvc_device_index(device_identifier):
    # Support both raw indices and the persisted "uvc_index_x" format.
    if isinstance(device_identifier, int):
        return device_identifier
    if device_identifier is None:
        return None

    value = str(device_identifier).strip()
    if value.isdigit():
        return int(value)
    if value.startswith("uvc_index_"):
        suffix = value.replace("uvc_index_", "", 1)
        if suffix.isdigit():
            return int(suffix)
    return None


class UVCCameraProcessor(MyQThread):
    def __init__(
        self,
        path="",
        id=1,
        serial_number="",
        device_index=None,
        logical_index=None,
        stable_id="",
        device_path="",
        backend=None,
    ):
        super().__init__(name=f"deep_camera_{id}")
        self.cage_number = int(id)
        self.serial_number = serial_number
        self.device_index = device_index if device_index is not None else parse_uvc_device_index(serial_number)
        parsed_logical_index = parse_uvc_device_index(serial_number)
        self.logical_index = (
            int(logical_index)
            if logical_index is not None
            else parsed_logical_index
        )
        try:
            self.capture_backend = int(backend) if backend is not None else None
        except (TypeError, ValueError):
            self.capture_backend = None
        self.binding_config = {
            "mouse_cage_number": self.cage_number,
            "serial": serial_number,
            "logical_index": self.logical_index,
            "stable_id": stable_id,
            "device_path": device_path,
            "instance_id": device_path,
            "device_index": self.device_index,
            "backend": self.capture_backend,
        }
        self.stable_identity = camera_config_identity(self.binding_config)
        self.id = id
        self.path = path
        self.capture = None
        self.fps = 30
        self.frame_width = 1280
        self.frame_height = 720
        self.frame_id = 0
        self.camera_session_id = 0
        self.last_raw_save_time = 0.0
        self.consecutive_read_failures = 0
        self.last_success_frame_time = 0.0
        self.last_failure_log_time = 0.0
        self.last_stats_log_time = time.time()
        self.last_health_report_time = 0.0
        self.frames_since_stats_log = 0
        self.init_state = False
        self.reconnect_delay_seconds = 1.0
        self.next_reconnect_time = 0.0
        self.health_reporter = _health_reporter(
            "deep_camera",
            _channel_health_scope(self.cage_number),
        )

    def _health_metrics(self, **extra_metrics):
        metrics = {
            "camera_id": self.id,
            "cage_number": self.cage_number,
            "device_index": self.device_index,
            "logical_index": self.logical_index,
            "frame_id": self.frame_id,
            "consecutive_read_failures": self.consecutive_read_failures,
            "sampling_interval_seconds": _health_publish_interval_seconds(),
        }
        metrics.update({key: value for key, value in extra_metrics.items() if value is not None})
        return metrics

    def _report_healthy_frame(self, message="Deep-camera frame received", force=False):
        reporter = self.health_reporter or _health_reporter(
            "deep_camera",
            _channel_health_scope(self.cage_number),
        )
        self.health_reporter = reporter
        if reporter is None:
            return
        now = time.monotonic()
        if not force and now - self.last_health_report_time < _health_publish_interval_seconds():
            return
        self.last_health_report_time = now
        reporter.success(metrics=self._health_metrics(), message=message)

    def _report_camera_failure(self, reason_code, message, **extra_metrics):
        reporter = self.health_reporter or _health_reporter(
            "deep_camera",
            _channel_health_scope(self.cage_number),
        )
        self.health_reporter = reporter
        if reporter is not None:
            reporter.failure(
                reason_code=reason_code,
                message=str(message)[:240],
                metrics=self._health_metrics(**extra_metrics),
            )

    def _resolve_current_device(self):
        if not self.stable_identity:
            return self.device_index is not None

        try:
            resolved = resolve_camera_config(
                self.binding_config,
                enumerate_uvc_cameras(probe=False),
            )
        except Exception as error:
            error_message = f"UVC camera enumeration failed: {error}"
            if error_message not in logged_errors:
                logger.error(error_message)
                logged_errors.add(error_message)
            return False

        if resolved is None:
            error_message = (
                f"UVC camera not connected: cage={self.cage_number}, "
                f"logical_index={self.logical_index}"
            )
            if error_message not in logged_errors:
                logger.error(error_message)
                _emit_runtime_diagnostic(
                    "camera_binding_not_found",
                    f"cage={self.cage_number}, logical_index={self.logical_index}, "
                    f"stable_id={self.stable_identity}",
                )
                logged_errors.add(error_message)
            return False

        self.binding_config.update(resolved)
        self.device_index = int(resolved["device_index"])
        backend = resolved.get("backend")
        self.capture_backend = int(backend) if backend is not None else None
        logged_errors.discard(
            f"UVC camera not connected: cage={self.cage_number}, "
            f"logical_index={self.logical_index}"
        )
        return True

    def _configure_capture(self, capture):
        if hasattr(cv2, "CAP_PROP_FOURCC"):
            capture.set(
                cv2.CAP_PROP_FOURCC,
                cv2.VideoWriter_fourcc(*"MJPG"),
            )
        capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.frame_width)
        capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.frame_height)
        capture.set(cv2.CAP_PROP_FPS, self.fps)
        if hasattr(cv2, "CAP_PROP_BUFFERSIZE"):
            capture.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        if hasattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC"):
            capture.set(cv2.CAP_PROP_READ_TIMEOUT_MSEC, 2000)

    def _open_capture(self, index):
        if self.capture_backend is not None:
            backends = [self.capture_backend]
        else:
            # Old configurations have no backend; retain the original fallback order.
            backends = []
            if hasattr(cv2, "CAP_DSHOW"):
                backends.append(cv2.CAP_DSHOW)
            if hasattr(cv2, "CAP_MSMF"):
                backends.append(cv2.CAP_MSMF)
            backends.append(None)

        for backend in backends:
            try:
                capture = (
                    cv2.VideoCapture(index, backend)
                    if backend is not None
                    else cv2.VideoCapture(index)
                )
            except cv2.error as error:
                logger.warning(
                    f"deep_camera_{self.id} open failed: index={index}, "
                    f"backend={backend}, reason={error}"
                )
                continue
            if capture is None or not capture.isOpened():
                if capture is not None:
                    capture.release()
                continue

            self._configure_capture(capture)
            frame_ok = False
            for _ in range(5):
                try:
                    frame_ok, _ = capture.read()
                except cv2.error as error:
                    logger.warning(f"deep_camera_{self.id} warmup failed: {error}")
                    frame_ok = False
                    break
                if frame_ok:
                    break
            if frame_ok:
                return capture
            else:
                capture.release()
        return None

    def init_camera(self):
        self._release_capture()

        with camera_connect_lock:
            device_resolved = self._resolve_current_device()
            if device_resolved:
                self.capture = self._open_capture(self.device_index)

        if not device_resolved:
            self._report_camera_failure(
                "camera_binding_not_found",
                "Configured deep camera was not found during device resolution.",
            )
            return False

        if self.device_index is None:
            error_message = f"UVC camera config is invalid, serial={self.serial_number}"
            if error_message not in logged_errors:
                logger.error(error_message)
                _emit_runtime_diagnostic(
                    "camera_config_invalid",
                    f"cage={self.cage_number}, camera_id={self.id}, "
                    f"serial={self.serial_number}",
                )
                logged_errors.add(error_message)
            self._report_camera_failure("camera_config_invalid", error_message)
            return False

        if self.capture is None:
            error_message = f"UVC camera open failed, index={self.device_index}"
            if error_message not in logged_errors:
                logger.error(error_message)
                _emit_runtime_diagnostic(
                    "camera_open_failed",
                    f"cage={self.cage_number}, camera_id={self.id}, "
                    f"device={self.device_index}",
                )
                logged_errors.add(error_message)
            self._report_camera_failure("camera_open_failed", error_message)
            return False

        self.camera_session_id = secrets.randbits(63)
        self.frame_id = 0
        self.reconnect_delay_seconds = 1.0
        self.next_reconnect_time = 0.0
        logged_errors.discard(f"UVC camera open failed, index={self.device_index}")
        shared_video_frame_store.clear_frame("deep_camera", self.cage_number)
        logger.info(
            f"deep_camera_{self.id} connected: logical_index={self.logical_index}, "
            f"current_index={self.device_index}, "
            f"camera_session_id={self.camera_session_id}"
        )
        _emit_runtime_diagnostic(
            "camera_connected",
            f"cage={self.cage_number}, camera_id={self.id}, "
            f"device={self.device_index}, session={self.camera_session_id}, "
            f"configured_fps={self.fps}, size={self.frame_width}x{self.frame_height}",
        )
        # A successful device open is not a usable camera health signal.  Only
        # an actual frame read may publish a healthy snapshot; this prevents a
        # no-camera/lock-only process from appearing as connected.
        return True

    def _release_capture(self):
        capture = self.capture
        self.capture = None
        if capture is not None:
            try:
                capture.release()
            except Exception as error:
                logger.error(f"deep_camera_{self.id} release failed: {error}")

    def _schedule_reconnect(self):
        self.next_reconnect_time = time.monotonic() + self.reconnect_delay_seconds
        self.reconnect_delay_seconds = min(self.reconnect_delay_seconds * 2.0, 10.0)

    def _handle_capture_read_failure(self, reason):
        failure_time = time.time()
        self.consecutive_read_failures += 1
        self._report_camera_failure(
            "camera_read_failed",
            reason,
            failure_time=failure_time,
        )
        if failure_time - self.last_failure_log_time >= 2.0:
            logger.error(
                f"deep_camera_{self.id} read failed: "
                f"logical_index={self.logical_index}, current_index={self.device_index}, "
                f"consecutive_failures={self.consecutive_read_failures}, reason={reason}"
            )
            _emit_runtime_diagnostic(
                "camera_read_failed",
                f"cage={self.cage_number}, logical_index={self.logical_index}, "
                f"current_index={self.device_index}, "
                f"consecutive_failures={self.consecutive_read_failures}, reason={reason}",
            )
            self.last_failure_log_time = failure_time
        shared_video_frame_store.clear_frame("deep_camera", self.cage_number)
        self._release_capture()
        self.init_state = False
        self._schedule_reconnect()

    def _ensure_dir(self, path):
        if not os.path.exists(path):
            os.makedirs(path)

    def img_save(self, image, *, timestamp: float):
        global save_frame_thread

        timestrf = time_util.get_format_file_from_time(timestamp)
        file_name = f"{timestrf}.jpg"
        deep_config = _deep_camera_config()
        color_dir = os.path.join(self.path, deep_config["color_dir"].strip("/\\"))
        self._ensure_dir(color_dir)
        color_path = os.path.join(color_dir, file_name)
        if save_frame_thread is not None and save_frame_thread.isRunning():
            save_frame_thread.submit_frame(color_path, file_name, image)
        return color_path, file_name

    def stop(self):
        shared_video_frame_store.clear_frame("deep_camera", self.cage_number)
        super().stop()

    def run(self):
        logger.warning(f"{self.name} thread has been started")
        self._running = True
        global frame_nums
        try:
            if _save_raw_frames():
                color_dir = os.path.join(self.path, _deep_camera_config()["color_dir"].strip("/\\"))
                self._ensure_dir(color_dir)

                with os.scandir(color_dir) as it:
                    for entry in it:
                        if entry.is_file():
                            with lock:
                                frame_nums += 1

            self.msleep(max(self.cage_number - 1, 0) * 120)
            while self._running:
                self.mutex.lock()
                if self._paused:
                    self.condition.wait(self.mutex)
                self.mutex.unlock()

                try:
                    self.dosomething()
                except Exception as e:
                    logger.error(f"deep_camera_{self.id} run failed: {e} | {traceback.format_exc()}")
                    self._report_camera_failure("camera_processing_failed", e)
                    self.msleep(100)
        finally:
            self._release_capture()
            self.init_state = False
            self._running = False
            shared_video_frame_store.clear_frame("deep_camera", self.cage_number)
            logger.info(f"deep_camera_{self.id} capture thread released")

    def dosomething(self):
        global frame_nums, video_recorder_thread

        # Reconnect lazily if the device dropped during runtime.
        if not self.init_state:
            now = time.monotonic()
            if now < self.next_reconnect_time:
                self.msleep(100)
                return
            self.init_state = self.init_camera()
            if not self.init_state:
                self._schedule_reconnect()
                return

        if self.capture is None:
            self._report_camera_failure(
                "camera_capture_missing",
                "Capture handle was unavailable after initialization.",
            )
            self.init_state = False
            self._schedule_reconnect()
            return

        start_time = time.time()
        try:
            ret, color_image = self.capture.read()
        except cv2.error as error:
            self._handle_capture_read_failure(f"OpenCV error: {error}")
            return
        except Exception as error:
            self._handle_capture_read_failure(error)
            return
        if not ret or color_image is None:
            self._handle_capture_read_failure("empty frame")
            return

        timestamp = time.time()
        capture_monotonic_ns = time.monotonic_ns()
        self.consecutive_read_failures = 0
        self.last_success_frame_time = timestamp
        self.frame_id += 1
        self._report_healthy_frame()
        if _save_raw_frames():
            raw_save_interval = _raw_save_interval_seconds()
            if raw_save_interval <= 0 or timestamp - self.last_raw_save_time >= raw_save_interval:
                self.img_save(color_image, timestamp=timestamp)
                self.last_raw_save_time = timestamp
        shared_video_frame_store.write_frame(
            "deep_camera",
            self.cage_number,
            color_image,
            frame_id=self.frame_id,
            timestamp=timestamp,
            camera_session_id=self.camera_session_id,
            capture_monotonic_ns=capture_monotonic_ns,
        )
        if video_recorder_thread is not None and video_recorder_thread.isRunning():
            video_recorder_thread.submit_frame(
                self.cage_number,
                self.path,
                color_image,
                timestamp,
            )
        with lock:
            frame_nums += 1

        elapsed = time.time() - start_time
        self.frames_since_stats_log += 1
        stats_elapsed = timestamp - self.last_stats_log_time
        if stats_elapsed >= 5.0:
            capture_fps = self.frames_since_stats_log / max(stats_elapsed, 0.001)
            logger.debug(
                f"deep_camera_{self.id} capture fps="
                f"{capture_fps:.2f}, "
                f"last_frame_cost={elapsed:.3f}s total_frames={frame_nums}"
            )
            _emit_runtime_diagnostic(
                "capture_runtime",
                f"cage={self.cage_number}, camera_id={self.id}, "
                f"device={self.device_index}, session={self.camera_session_id}, "
                f"capture_fps={capture_fps:.2f}, frame_sequence={self.frame_id}, "
                f"frame_cost_ms={elapsed * 1000.0:.2f}, "
                f"shape={color_image.shape[1]}x{color_image.shape[0]}, "
                f"consecutive_failures={self.consecutive_read_failures}",
            )
            self.last_stats_log_time = timestamp
            self.frames_since_stats_log = 0
        time.sleep(float(_deep_camera_config()["delay"]))


def load_global_setting():
    global_load.load_global_setting_without_Qt()
    start_time = time.time()
    global_setting.set_setting("start_time", start_time)
    global_setting.set_setting("last_delete_time", start_time)
    logger.info(f"deep_camera load settings at {time_util.get_format_from_time(start_time)}")


def _publish_camera_lock_state(state, owner=None):
    global camera_lock_state, camera_lock_owner
    camera_lock_state = str(state)
    camera_lock_owner = dict(owner or {})
    # The mutex only describes process ownership, not a connected physical
    # camera. Keep it separate so an acquired lock cannot make an absent
    # trajectory camera appear healthy in P1-C.
    health_reporter = _health_reporter("deep_camera_lock", "global")
    if health_reporter is not None:
        lock_metrics = {
            "instance_lock_state": camera_lock_state,
            "owner_pid": camera_lock_owner.get("pid"),
        }
        if camera_lock_state == "blocked":
            health_reporter.unavailable(
                "external_occupied",
                message="A standalone video program owns the deep-camera lock.",
                metrics=lock_metrics,
            )
        elif camera_lock_state == "ready":
            health_reporter.success(
                metrics=lock_metrics,
                message="Deep-camera instance lock is available.",
            )
    queue_obj = global_setting.get_setting("queue", None)
    if queue_obj is None:
        return
    queue_obj.put(
        ObjectQueueItem(
            origin="main_deep_camera",
            to="MainWindow_index",
            title="deep_camera_instance_state",
            data={"state": camera_lock_state, "owner": camera_lock_owner},
            time=time_util.get_format_from_time(time.time()),
        )
    )


def _report_camera_lock_ready_heartbeat():
    """Recover an external-occupancy state only after two independent lock checks."""
    health_reporter = _health_reporter("deep_camera_lock", "global")
    if health_reporter is not None:
        health_reporter.success(
            metrics={"instance_lock_state": "ready"},
            message="Deep-camera instance lock remains available.",
        )


def _try_acquire_camera_instance_lock():
    global camera_instance_lock
    if camera_instance_lock is None:
        camera_instance_lock = VideoProgramInstanceLock("host")
    if camera_instance_lock.acquire():
        if camera_lock_state != "ready":
            logger.info("deep camera instance lock acquired")
            _publish_camera_lock_state("ready")
            if experiment_running:
                logger.info("standalone video program exited; resuming upper-computer camera capture")
                start()
        else:
            _report_camera_lock_ready_heartbeat()
        return True

    owner = camera_instance_lock.read_owner()
    if camera_lock_state != "blocked" or owner != camera_lock_owner:
        logger.warning(f"deep camera unavailable: standalone video program owns camera lock, owner={owner}")
        _publish_camera_lock_state("blocked", owner)
    return False


def _release_camera_instance_lock():
    global camera_instance_lock, camera_lock_retry_timer
    if camera_lock_retry_timer is not None:
        camera_lock_retry_timer.stop()
        camera_lock_retry_timer = None
    if camera_instance_lock is not None:
        camera_instance_lock.release()
        camera_instance_lock = None


def _begin_camera_session():
    global camera_session_state
    with camera_session_state_lock:
        if camera_session_state in {"starting", "running", "stopping"}:
            return False
        camera_session_state = "starting"
        return True


def _set_camera_session_state(state):
    global camera_session_state
    with camera_session_state_lock:
        camera_session_state = str(state)


def check_setting_cameras_each_number():
    config_file_path = f"./{_deep_camera_config()['camera_to_mouse_cage_number_file_name']}"
    if folder_util.is_exist_file(config_file_path):
        serials = json_util.read_json_to_dict_list(config_file_path)
        serials = serials if isinstance(serials, list) else []
        if not serials:
            reporter = _health_reporter("deep_camera", "global")
            if reporter is not None:
                reporter.unavailable(
                    "not_configured",
                    message="No trajectory camera is configured for this standalone test.",
                    metrics={"configured_camera_count": 0},
                )
        init_camera_and_image_handle_thread(serials)
        return

    reporter = _health_reporter("deep_camera", "global")
    if reporter is not None:
        reporter.unavailable(
            "not_configured",
            message="No trajectory-camera mapping file is configured.",
            metrics={"configured_camera_count": 0},
        )
    try:
        # Ask the GUI to open the cage-to-camera mapping dialog when no config exists.
        logger.error("deep_camera config mapping file not found, request config dialog")
        queue = global_setting.get_setting("queue", None)
        if queue is not None:
            queue.put(
                ObjectQueueItem(
                    origin="main_deep_camera",
                    to="main_gui",
                    title="deep_camera_config_dialog",
                    time=time_util.get_format_from_time(time.time()),
                )
            )
    except Exception as e:
        logger.error(f"open deep camera config dialog failed: {e}")


def _stop_camera_threads(camera_structs, wait_ms=4000):
    threads = []
    all_stopped = True
    for camera_struct in list(camera_structs or []):
        for key in ("camera", "img_process"):
            thread = camera_struct.get(key)
            if thread is None:
                continue
            try:
                thread.stop()
                thread.requestInterruption()
                threads.append(thread)
            except Exception as error:
                all_stopped = False
                logger.error(f"stop old deep camera {key} failed: {error}")

    for thread in threads:
        try:
            if thread.isRunning() and not thread.wait(wait_ms):
                all_stopped = False
                logger.error(f"deep camera thread did not stop in {wait_ms}ms: {thread.objectName()}")
        except Exception as error:
            all_stopped = False
            logger.error(f"wait deep camera thread failed: {error}")
    return all_stopped


def init_camera_and_image_handle_thread(serials):
    global camera_list, read_queue_data_thread, delete_file_thread

    if camera_instance_lock is None or not camera_instance_lock.acquired:
        logger.warning("deep camera configuration deferred: standalone video program owns the cameras")
        return

    serials = serials if isinstance(serials, list) else []
    camera_nums = len(serials)
    camera_config_temp = _camera_config()
    camera_config_temp["DEEP_CAMERA"]["nums"] = camera_nums
    global_setting.set_setting("camera_config", camera_config_temp)

    if not _stop_camera_threads(camera_list):
        logger.error("deep camera reconfiguration cancelled: old camera threads are still running")
        return

    camera_list = []
    configured_identities = set()
    configured_logical_indices = set()

    for num in range(camera_nums):
        camera_struct = {}
        serial_config = serials[num]
        stable_identity = camera_config_identity(serial_config)
        logical_index = camera_logical_index(serial_config)
        if stable_identity and stable_identity in configured_identities:
            logger.error(f"skip duplicate deep camera identity: {serial_config}")
            continue
        if logical_index is not None and logical_index in configured_logical_indices:
            logger.error(f"skip duplicate deep camera logical index: {serial_config}")
            continue
        if stable_identity:
            configured_identities.add(stable_identity)
        if logical_index is not None:
            configured_logical_indices.add(logical_index)

        device_index = serial_config.get("device_index")
        if device_index is None:
            device_index = parse_uvc_device_index(serial_config.get("serial"))
        if device_index is None and not stable_identity:
            logger.error(f"skip invalid deep camera config: {serial_config}")
            continue

        try:
            # The logical index remains fixed; the current OpenCV index is resolved at runtime.
            camera = UVCCameraProcessor(
                path=_camera_base_path(serial_config["mouse_cage_number"]),
                id=serial_config["mouse_cage_number"],
                serial_number=serial_config.get("serial"),
                device_index=device_index,
                logical_index=logical_index,
                stable_id=serial_config.get("stable_id", ""),
                device_path=serial_config.get(
                    "device_path",
                    serial_config.get("instance_id", ""),
                ),
                backend=serial_config.get("backend"),
            )
        except Exception as e:
            logger.error(
                f"init deep camera failed, cage={serial_config['mouse_cage_number']}, reason={e} | {traceback.format_exc()}"
            )
            if delete_file_thread is not None:
                delete_file_thread.stop()
            for started_camera in camera_list:
                running_camera = started_camera.get("camera")
                if running_camera is not None:
                    running_camera.stop()
            continue

        camera.start()
        camera_struct["id"] = num + 1
        camera_struct["camera"] = camera
        camera_struct["img_process"] = None
        camera_list.append(camera_struct)

    logger.warning(f"{camera_list}")
    read_queue_data_thread.camera_list = camera_list


def main(
    q=None,
    runtime_diagnostics_queue=None,
    auto_start=False,
    health_snapshot_queue=None,
):
    global experiment_running, camera_lock_retry_timer
    _set_health_snapshot_queue(health_snapshot_queue)
    global_setting.set_setting("queue", q)
    global_setting.set_setting(
        "runtime_diagnostics_queue",
        runtime_diagnostics_queue,
    )

    app = QCoreApplication.instance()
    if app is None:
        app = QCoreApplication(sys.argv)

    logger.info(f"{'-' * 30}deep_camera_start{'-' * 30}")
    logger.info(f"{__name__} | {os.path.basename(__file__)} | {os.getpid()} | {os.getppid()}")

    load_global_setting()

    _try_acquire_camera_instance_lock()
    camera_lock_retry_timer = QTimer()
    camera_lock_retry_timer.setInterval(2000)
    camera_lock_retry_timer.timeout.connect(_try_acquire_camera_instance_lock)
    camera_lock_retry_timer.start()
    app.aboutToQuit.connect(_release_camera_instance_lock)

    read_queue_data_thread.queue = q
    if read_queue_data_thread.isRunning():
        read_queue_data_thread.stop()
    read_queue_data_thread.start()
    if auto_start:
        experiment_running = True
        logger.warning("deep_camera process restarted, resume camera capture automatically")
        start()

    return app.exec()


def start():
    global delete_file_thread, save_frame_thread, video_recorder_thread

    if camera_instance_lock is None or not camera_instance_lock.acquired:
        logger.warning("deep camera start deferred: standalone video program is running")
        _publish_camera_lock_state(
            "blocked",
            camera_instance_lock.read_owner() if camera_instance_lock is not None else {},
        )
        return

    if not _begin_camera_session():
        logger.info(f"deep camera start ignored: session state is {camera_session_state}")
        return

    try:
        logger.info(f"{'-' * 30}deep_camera_run{'-' * 30}")
        path = _storage_root()

        try:
            if delete_file_thread is not None and delete_file_thread.isRunning():
                delete_file_thread.stop()
        except Exception as e:
            logger.error(f"stop deep_camera_delete_file_thread failed: {e}")

        try:
            if save_frame_thread is not None and save_frame_thread.isRunning():
                save_frame_thread.stop()
                save_frame_thread.requestInterruption()
                save_frame_thread.wait(2000)
        except Exception as e:
            logger.error(f"stop deep_camera_save_raw_frame failed: {e}")

        if video_recorder_thread is not None:
            try:
                video_recorder_thread.stop()
                video_recorder_thread.requestInterruption()
                video_recorder_thread.wait(120000)
            except Exception as e:
                logger.error(f"stop old FFmpeg video recorder failed: {e}")
            video_recorder_thread = None

        if _save_raw_frames():
            save_frame_thread = SaveRawFrameThread()
            save_frame_thread.start()
        else:
            save_frame_thread = None
            logger.info("deep_camera raw frame saving disabled")

        if _record_video_enabled():
            recorder = FFmpegVideoRecorderThread(
                config=_video_recorder_config(),
                session_timestamp=global_setting.get_setting("start_experiment_time", time.time()),
                diagnostic_callback=_emit_runtime_diagnostic,
                recovery_root=_storage_root(),
            )
            if recorder.available:
                video_recorder_thread = recorder
                video_recorder_thread.start()
            else:
                video_recorder_thread = None
                logger.error(
                    "FFmpeg video recording disabled: ffmpeg executable was not found. "
                    "Install imageio-ffmpeg or set DEEP_CAMERA.ffmpeg_path."
                )
                _emit_runtime_diagnostic(
                    "video_record_unavailable",
                    "ffmpeg executable was not found; camera capture and trajectory continue normally",
                )
        else:
            video_recorder_thread = None
            logger.info("deep_camera FFmpeg video recording disabled by config")

        delete_file_thread = Delete_file(
            path=path,
            start_time=global_setting.get_setting("start_time", time.time()),
        )
        delete_file_thread.start()
        check_setting_cameras_each_number()
        _set_camera_session_state("running")
    except Exception as e:
        _set_camera_session_state("stopped")
        logger.error(f"deep_camera start failed: {e}")


def restart(
    q,
    runtime_diagnostics_queue=None,
    auto_start=False,
    health_snapshot_queue=None,
):
    # IntegratedProcessMonitor replays the original main() argument tuple when
    # it restarts this process.  A restarted capture process must still resume
    # the active session, irrespective of the initial-process auto_start flag.
    return main(
        q,
        runtime_diagnostics_queue,
        auto_start=True,
        health_snapshot_queue=health_snapshot_queue,
    )


def pause():
    logger.info(f"{'-' * 30}deep_camera_pause{'-' * 30}")


def stop():
    global delete_file_thread, camera_list, save_frame_thread, video_recorder_thread, experiment_running

    experiment_running = False
    _set_camera_session_state("stopping")
    logger.info(f"{'-' * 30}deep_camera_stop{'-' * 30}")
    logger.warning("stop_deep_camera_thread")

    queue = global_setting.get_setting("queue", None)

    if camera_instance_lock is None or not camera_instance_lock.acquired:
        if queue:
            queue.put(
                ObjectQueueItem(
                    origin="main_deep_camera",
                    to="MainWindow_index",
                    title="stop_deep_camera_return",
                    data="独立视频模块占用中，上位机轨迹相机未启动",
                    time=time_util.get_format_from_time(time.time()),
                )
            )
        shared_video_frame_store.close_writer()
        _set_camera_session_state("stopped")
        if queue:
            queue.put(ObjectQueueItem(
                origin="main_deep_camera", to="MainWindow_index", title="stop_deep_camera_complete",
                data={"success": True, "detail": "轨迹相机未由上位机启动，无需停止"},
                time=time_util.get_format_from_time(time.time()),
            ))
        return

    cameras_to_stop = list(camera_list)
    cameras_stopped = _stop_camera_threads(cameras_to_stop)
    stop_succeeded = bool(cameras_stopped)
    for i, _camera_struct in enumerate(cameras_to_stop, start=1):
        if queue:
            queue.put(
                ObjectQueueItem(
                    origin="main_deep_camera",
                    to="MainWindow_index",
                    title="stop_deep_camera_return",
                    data=(
                        f"deep camera {i} stopped"
                        if cameras_stopped
                        else f"deep camera {i} stop timed out"
                    ),
                    time=time_util.get_format_from_time(time.time()),
                )
            )

    if delete_file_thread is not None:
        try:
            delete_file_thread.stop()
            delete_file_thread.requestInterruption()
            if delete_file_thread.isRunning() and not delete_file_thread.wait(5000):
                logger.error("deep camera delete thread stop timed out")
                stop_succeeded = False
            if queue:
                queue.put(
                    ObjectQueueItem(
                        origin="main_deep_camera",
                        to="MainWindow_index",
                        title="stop_deep_camera_return",
                        data="deep camera delete thread stopped",
                        time=time_util.get_format_from_time(time.time()),
                    )
                )
            delete_file_thread = None
        except Exception as e:
            stop_succeeded = False
            logger.error(f"stop deep_camera_delete_file_thread failed: {e}")
            if queue:
                queue.put(
                    ObjectQueueItem(
                        origin="main_deep_camera",
                        to="MainWindow_index",
                        title="stop_deep_camera_return",
                        data=f"deep camera delete thread stop failed: {e}",
                        time=time_util.get_format_from_time(time.time()),
                    )
                )

    if save_frame_thread is not None:
        try:
            save_frame_thread.stop()
            save_frame_thread.requestInterruption()
            if save_frame_thread.isRunning() and not save_frame_thread.wait(2000):
                logger.error("deep camera save raw frame thread stop timed out")
                stop_succeeded = False
        except Exception as e:
            stop_succeeded = False
            logger.error(f"stop deep_camera_save_raw_frame failed: {e}")
        save_frame_thread = None

    if video_recorder_thread is not None:
        try:
            video_recorder_thread.stop()
            video_recorder_thread.requestInterruption()
            if video_recorder_thread.isRunning() and not video_recorder_thread.wait(120000):
                logger.error("FFmpeg video recorder stop timed out")
                stop_succeeded = False
        except Exception as e:
            stop_succeeded = False
            logger.error(f"stop FFmpeg video recorder failed: {e}")
        video_recorder_thread = None

    if cameras_stopped:
        camera_list = []
        read_queue_data_thread.camera_list = camera_list
    shared_video_frame_store.close_writer()
    _set_camera_session_state("stopped")
    if queue:
        queue.put(ObjectQueueItem(
            origin="main_deep_camera", to="MainWindow_index", title="stop_deep_camera_complete",
            data={"success": stop_succeeded, "detail": "轨迹相机停止完成" if stop_succeeded else "轨迹相机停止存在未释放的线程"},
            time=time_util.get_format_from_time(time.time()),
        ))


if __name__ == "__main__":
    main()
