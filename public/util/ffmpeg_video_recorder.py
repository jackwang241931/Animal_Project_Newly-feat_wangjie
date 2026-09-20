import os
import shutil
import subprocess
import threading
import time
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import cv2
from loguru import logger

from public.entity.MyQThread import MyQThread


@dataclass(frozen=True)
class FFmpegVideoRecorderConfig:
    ffmpeg_path: str = ""
    output_dir_name: str = "video"
    fps: float = 10.0
    width: int = 480
    height: int = 270
    bitrate_kbps: int = 100
    maxrate_kbps: int = 120
    bufsize_kbps: int = 240
    preset: str = "ultrafast"
    encoder_threads: int = 1
    fragment_seconds: float = 2.0
    stats_interval_seconds: float = 5.0


@dataclass
class _WriterState:
    cage_number: int
    output_path: str
    recording_path: str
    process: subprocess.Popen
    started_monotonic: float
    next_frame_due: float
    last_frame: Any = None
    last_frame_timestamp: float = 0.0
    encoded_frames: int = 0
    write_seconds: float = 0.0


def find_ffmpeg_executable(configured_path: str = "") -> str:
    configured_path = str(configured_path or "").strip().strip('"')
    if configured_path and os.path.isfile(configured_path):
        return os.path.abspath(configured_path)

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return os.path.abspath(system_ffmpeg)

    try:
        import imageio_ffmpeg

        bundled_ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled_ffmpeg and os.path.isfile(bundled_ffmpeg):
            return os.path.abspath(bundled_ffmpeg)
    except Exception:
        pass
    return ""


class FFmpegVideoRecorderThread(MyQThread):
    def __init__(
        self,
        config: FFmpegVideoRecorderConfig,
        session_timestamp: float | None = None,
        diagnostic_callback: Callable[[str, str], None] | None = None,
        recovery_root: str = "",
    ):
        super().__init__(name="deep_camera_ffmpeg_video_recorder")
        self.config = config
        self.ffmpeg_executable = find_ffmpeg_executable(config.ffmpeg_path)
        self.session_timestamp = float(session_timestamp or time.time())
        self.diagnostic_callback = diagnostic_callback
        self.recovery_root = str(recovery_root or "")

        self.fps = max(float(config.fps), 0.1)
        self.width = max(2, int(config.width) // 2 * 2)
        self.height = max(2, int(config.height) // 2 * 2)
        self.bitrate_kbps = max(1, int(config.bitrate_kbps))
        self.maxrate_kbps = max(self.bitrate_kbps, int(config.maxrate_kbps))
        self.bufsize_kbps = max(self.maxrate_kbps, int(config.bufsize_kbps))
        self.encoder_threads = max(1, int(config.encoder_threads))
        self.fragment_seconds = max(0.5, float(config.fragment_seconds))
        self.stats_interval_seconds = max(float(config.stats_interval_seconds), 1.0)
        self.output_dir_name = str(config.output_dir_name or "video").strip("/\\") or "video"

        self.pending_lock = threading.Lock()
        self.pending_frames: dict[int, tuple[str, Any, float]] = {}
        self.writer_states: dict[int, _WriterState] = {}
        self.disabled_cages: set[int] = set()
        self.submitted_frames = defaultdict(int)
        self.replaced_frames = defaultdict(int)
        self.last_stats_time = time.monotonic()
        self.accepting_frames = bool(self.ffmpeg_executable)

    @property
    def available(self):
        return bool(self.ffmpeg_executable)

    def _emit_diagnostic(self, event: str, message: str):
        if self.diagnostic_callback is not None:
            try:
                self.diagnostic_callback(event, message)
            except Exception as error:
                logger.debug(f"video recorder diagnostic callback failed: {error}")

    def submit_frame(self, cage_number: int, cage_path: str, frame, timestamp: float):
        if not self.accepting_frames or frame is None:
            return False

        cage_number = int(cage_number)
        if cage_number in self.disabled_cages:
            return False

        with self.pending_lock:
            if cage_number in self.pending_frames:
                self.replaced_frames[cage_number] += 1
            # capture.read() returns a new array. Keeping that reference avoids a
            # full-resolution copy in the camera and trajectory path.
            self.pending_frames[cage_number] = (str(cage_path), frame, float(timestamp))
            self.submitted_frames[cage_number] += 1
        return True

    def stop(self):
        self.accepting_frames = False
        super().stop()

    def run(self):
        self._running = True
        self._stop_requested = False
        self._paused = False
        self._recover_existing_recordings()
        expected_mb_10h = self.bitrate_kbps * 1000 * 10 * 60 * 60 / 8 / 1_000_000
        logger.info(
            f"FFmpeg video recorder started: ffmpeg={self.ffmpeg_executable}, "
            f"size={self.width}x{self.height}, fps={self.fps:g}, "
            f"bitrate={self.bitrate_kbps}kbps, estimated_10h={expected_mb_10h:.1f}MB/cage"
        )
        self._emit_diagnostic(
            "video_record_started",
            f"ffmpeg={self.ffmpeg_executable}, size={self.width}x{self.height}, "
            f"fps={self.fps:g}, bitrate_kbps={self.bitrate_kbps}, "
            f"estimated_10h_mb_per_cage={expected_mb_10h:.1f}",
        )
        try:
            while self._running and not self.isInterruptionRequested():
                self.dosomething()
        finally:
            self.accepting_frames = False
            self._close_all_writers()
            self._running = False
            logger.info("FFmpeg video recorder thread released")

    def dosomething(self):
        self._consume_latest_frames()
        now = time.monotonic()
        frame_interval = 1.0 / self.fps

        for cage_number, state in list(self.writer_states.items()):
            if state.last_frame is None or now < state.next_frame_due:
                continue
            try:
                frame = cv2.resize(
                    state.last_frame,
                    (self.width, self.height),
                    interpolation=cv2.INTER_AREA,
                )
                if not frame.flags.c_contiguous:
                    frame = frame.copy(order="C")
                write_started = time.perf_counter()
                state.process.stdin.write(frame.tobytes())
                state.write_seconds += time.perf_counter() - write_started
                state.encoded_frames += 1
                state.next_frame_due += frame_interval
                if state.next_frame_due < now - frame_interval:
                    state.next_frame_due = now + frame_interval
            except Exception as error:
                logger.error(
                    f"FFmpeg video write failed: cage={cage_number}, "
                    f"output={state.recording_path}, reason={error}"
                )
                self._emit_diagnostic(
                    "video_record_failed",
                    f"cage={cage_number}, output={state.recording_path}, reason={error}",
                )
                self.disabled_cages.add(cage_number)
                self._close_writer(cage_number, terminate=True)

        if now - self.last_stats_time >= self.stats_interval_seconds:
            self._log_runtime_stats(now)
            self.last_stats_time = now
        time.sleep(0.002)

    def _consume_latest_frames(self):
        with self.pending_lock:
            pending = self.pending_frames
            self.pending_frames = {}

        for cage_number, (cage_path, frame, timestamp) in pending.items():
            state = self.writer_states.get(cage_number)
            if state is None:
                state = self._open_writer(cage_number, cage_path)
                if state is None:
                    self.disabled_cages.add(cage_number)
                    continue
                self.writer_states[cage_number] = state
            state.last_frame = frame
            state.last_frame_timestamp = timestamp

    def _open_writer(self, cage_number: int, cage_path: str):
        video_dir = Path(cage_path) / self.output_dir_name
        video_dir.mkdir(parents=True, exist_ok=True)
        session_name = datetime.fromtimestamp(self.session_timestamp).strftime("%Y_%m_%d_%H_%M_%S_%f")[:-3]
        base_stem = f"video_{session_name}_cage{cage_number}"
        output_path, recording_path = self._unique_output_paths(video_dir, base_stem)

        command = [
            self.ffmpeg_executable,
            "-hide_banner",
            "-loglevel",
            "warning",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s:v",
            f"{self.width}x{self.height}",
            "-r",
            f"{self.fps:g}",
            "-i",
            "-",
            "-an",
            "-c:v",
            "libx264",
            "-preset",
            str(self.config.preset or "ultrafast"),
            "-tune",
            "zerolatency",
            "-b:v",
            f"{self.bitrate_kbps}k",
            "-maxrate",
            f"{self.maxrate_kbps}k",
            "-bufsize",
            f"{self.bufsize_kbps}k",
            "-g",
            str(max(1, int(round(self.fps * self.fragment_seconds)))),
            "-keyint_min",
            str(max(1, int(round(self.fps * self.fragment_seconds)))),
            "-sc_threshold",
            "0",
            "-threads",
            str(self.encoder_threads),
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+frag_keyframe+empty_moov+default_base_moof",
            "-y",
            recording_path,
        ]
        creation_flags = (
            getattr(subprocess, "CREATE_NO_WINDOW", 0)
            | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
        )
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                bufsize=0,
                creationflags=creation_flags,
            )
        except Exception as error:
            logger.error(f"FFmpeg process start failed: cage={cage_number}, reason={error}")
            self._emit_diagnostic(
                "video_record_failed",
                f"cage={cage_number}, process_start_failed={error}",
            )
            return None

        logger.info(f"FFmpeg video output opened: cage={cage_number}, path={recording_path}")
        self._emit_diagnostic(
            "video_output_opened",
            f"cage={cage_number}, recording_path={recording_path}, final_path={output_path}",
        )
        now = time.monotonic()
        return _WriterState(
            cage_number=cage_number,
            output_path=output_path,
            recording_path=recording_path,
            process=process,
            started_monotonic=now,
            next_frame_due=now,
        )

    def _log_runtime_stats(self, now: float):
        with self.pending_lock:
            pending_count = len(self.pending_frames)
        for cage_number, state in self.writer_states.items():
            elapsed = max(now - state.started_monotonic, 0.001)
            file_size_mb = self._file_size_mb(state.recording_path)
            average_write_ms = (
                state.write_seconds * 1000 / state.encoded_frames
                if state.encoded_frames
                else 0.0
            )
            message = (
                f"cage={cage_number}, encoded_fps={state.encoded_frames / elapsed:.2f}, "
                f"encoded_frames={state.encoded_frames}, "
                f"submitted_frames={self.submitted_frames[cage_number]}, "
                f"replaced_frames={self.replaced_frames[cage_number]}, "
                f"write_avg_ms={average_write_ms:.2f}, pending_cages={pending_count}, "
                f"file_size_mb={file_size_mb:.2f}, output={state.recording_path}"
            )
            logger.debug(f"FFmpeg video runtime: {message}")
            self._emit_diagnostic("video_record_runtime", message)

    def _close_writer(self, cage_number: int, terminate=False):
        state = self.writer_states.pop(cage_number, None)
        if state is None:
            return
        try:
            if state.process.stdin is not None and not state.process.stdin.closed:
                state.process.stdin.close()
        except Exception:
            pass
        if terminate and state.process.poll() is None:
            try:
                state.process.terminate()
            except Exception:
                pass
        try:
            state.process.wait(timeout=3.0)
        except subprocess.TimeoutExpired:
            try:
                state.process.kill()
                state.process.wait(timeout=1.0)
            except Exception:
                pass
        self._finalize_recording(state.recording_path, state.output_path)
        self._log_final_state(state)

    def _close_all_writers(self):
        states = list(self.writer_states.values())
        self.writer_states = {}
        for state in states:
            try:
                if state.process.stdin is not None and not state.process.stdin.closed:
                    state.process.stdin.close()
            except Exception:
                pass

        deadline = time.monotonic() + 5.0
        for state in states:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                state.process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                try:
                    state.process.terminate()
                    state.process.wait(timeout=1.0)
                except Exception:
                    try:
                        state.process.kill()
                    except Exception:
                        pass
            self._finalize_recording(state.recording_path, state.output_path)
            self._log_final_state(state)

    def _log_final_state(self, state: _WriterState):
        elapsed = max(time.monotonic() - state.started_monotonic, 0.001)
        file_size_mb = self._file_size_mb(state.output_path)
        message = (
            f"cage={state.cage_number}, duration_seconds={elapsed:.1f}, "
            f"encoded_frames={state.encoded_frames}, file_size_mb={file_size_mb:.2f}, "
            f"output={state.output_path}"
        )
        logger.info(f"FFmpeg video finalized: {message}")
        self._emit_diagnostic("video_record_finalized", message)

    def _unique_output_paths(self, video_dir: Path, base_stem: str):
        part_number = 1
        while True:
            stem = base_stem if part_number == 1 else f"{base_stem}_part{part_number}"
            output_path = video_dir / f"{stem}.mp4"
            recording_path = video_dir / f"{stem}.recording.mp4"
            if not output_path.exists() and not recording_path.exists():
                return str(output_path), str(recording_path)
            part_number += 1

    def _recover_existing_recordings(self):
        if not self.recovery_root:
            return
        root = Path(self.recovery_root)
        if not root.exists():
            return

        video_directories = [
            path
            for path in root.rglob(self.output_dir_name)
            if path.is_dir() and path.name.lower() == self.output_dir_name.lower()
        ]
        for video_dir in video_directories:
            for recording_path in sorted(video_dir.glob("*.recording.mp4")):
                final_name = recording_path.name.replace(".recording.mp4", ".mp4")
                self._finalize_recording(str(recording_path), str(recording_path.with_name(final_name)))

            # Recover files produced by the first recorder version, which wrote
            # fragmented data directly to the final .mp4 name.
            for video_path in sorted(video_dir.glob("video_*.mp4")):
                if ".recording.mp4" in video_path.name:
                    continue
                if self._is_fragmented_mp4(str(video_path)):
                    self._finalize_recording(str(video_path), str(video_path))

    def _finalize_recording(self, recording_path: str, output_path: str):
        if not os.path.isfile(recording_path) or os.path.getsize(recording_path) <= 0:
            return False

        same_path = os.path.abspath(recording_path) == os.path.abspath(output_path)
        temp_output = f"{output_path}.recovering.mp4" if same_path else f"{output_path}.tmp.mp4"
        try:
            if os.path.exists(temp_output):
                os.remove(temp_output)
            command = [
                self.ffmpeg_executable,
                "-hide_banner",
                "-loglevel",
                "error",
                "-fflags",
                "+discardcorrupt",
                "-i",
                recording_path,
                "-map",
                "0:v:0",
                "-c",
                "copy",
                "-movflags",
                "+faststart",
                "-y",
                temp_output,
            ]
            creation_flags = (
                getattr(subprocess, "CREATE_NO_WINDOW", 0)
                | getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
            )
            result = subprocess.run(
                command,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=120,
                creationflags=creation_flags,
            )
            if result.returncode != 0 or not os.path.isfile(temp_output) or os.path.getsize(temp_output) <= 0:
                reason = result.stderr.decode(errors="replace").strip()
                logger.error(
                    f"FFmpeg video finalize failed: input={recording_path}, "
                    f"output={output_path}, reason={reason or result.returncode}"
                )
                self._emit_diagnostic(
                    "video_finalize_failed",
                    f"input={recording_path}, output={output_path}, reason={reason or result.returncode}",
                )
                return False

            warning = result.stderr.decode(errors="replace").strip()
            if warning:
                logger.warning(
                    f"FFmpeg recovered a truncated video tail: input={recording_path}, details={warning}"
                )
            os.replace(temp_output, output_path)
            if not same_path and os.path.exists(recording_path):
                os.remove(recording_path)
            logger.info(f"FFmpeg video index finalized: path={output_path}")
            self._emit_diagnostic(
                "video_index_finalized",
                f"input={recording_path}, output={output_path}, "
                f"file_size_mb={self._file_size_mb(output_path):.2f}",
            )
            return True
        except Exception as error:
            logger.error(
                f"FFmpeg video finalize exception: input={recording_path}, "
                f"output={output_path}, reason={error}"
            )
            self._emit_diagnostic(
                "video_finalize_failed",
                f"input={recording_path}, output={output_path}, reason={error}",
            )
            return False
        finally:
            if os.path.exists(temp_output):
                try:
                    os.remove(temp_output)
                except OSError:
                    pass

    @staticmethod
    def _is_fragmented_mp4(path: str):
        try:
            with open(path, "rb") as file:
                header = file.read(2 * 1024 * 1024)
            return b"mvex" in header or b"moof" in header
        except OSError:
            return False

    @staticmethod
    def _file_size_mb(path: str):
        try:
            return os.path.getsize(path) / 1_000_000
        except OSError:
            return 0.0
