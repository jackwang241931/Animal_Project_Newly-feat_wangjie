import json
import os
import threading
from datetime import datetime
from pathlib import Path
from uuid import uuid4


CALIBRATION_LOG_TYPES = {
    "ZERO_CALIBRATION": "zero",
    "RANGE_CALIBRATION": "span",
    "STARTUP_AIR_CALIBRATION": "air",
    "STARTUP_CO2_AIR_CALIBRATION": "co2_air",
}
START_EVENTS = {
    "set_start_zero_calibration_time",
    "set_start_span_calibration_time",
    "set_start_air_calibration_time",
}
STOP_EVENTS = {
    "set_stop_zero_calibration_time",
    "set_stop_span_calibration_time",
    "set_stop_air_calibration_time",
}
FAILURE_EVENT = "calibration_failed"


class CalibrationLogRecorder:
    """Persist acquisition-side messages independently of the GUI lifetime."""

    def __init__(self, directory=None, on_error=None, on_created=None):
        self.directory = (
            Path(directory) if directory is not None
            else Path(__file__).resolve().parents[3] / "log" / "calibration"
        )
        self.on_error = on_error
        self.on_created = on_created
        self._sessions = {}
        self._lock = threading.RLock()
        self._write_failed = False

    def record(self, log_type, message):
        if log_type not in CALIBRATION_LOG_TYPES or message in (None, ""):
            return None

        with self._lock:
            event = message.get("type") if isinstance(message, dict) else None
            session = self._sessions.get(log_type)
            if session is None or (event in START_EVENTS and session["started"]):
                now = datetime.now()
                name = (
                    f"calibration_logs_{now.strftime('%Y%m%d_%H%M%S_%f')}_"
                    f"{CALIBRATION_LOG_TYPES[log_type]}_{os.getpid()}_{uuid4().hex[:8]}.txt"
                )
                session = {"path": self.directory / name, "started": False}
                self._sessions[log_type] = session
            if event in START_EVENTS or event == FAILURE_EVENT:
                session["started"] = True

            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                path = session["path"]
                new_file = not path.exists()
                entry = self._format_message(message)
                with path.open("a", encoding="utf-8") as handle:
                    if new_file:
                        handle.write(
                            "标定系统自动保存日志\n"
                            f"Type: {log_type}\n"
                            f"Created: {datetime.now():%Y-%m-%d %H:%M:%S}\n"
                            f"Writer PID: {os.getpid()}\n"
                            + "=" * 50 + "\n\n"
                        )
                    if self._write_failed:
                        handle.write("[WARNING] Earlier log writes failed; some entries may be missing.\n")
                    handle.write(entry + "\n")
                    handle.flush()
                    if new_file or event in START_EVENTS or event in STOP_EVENTS or event == FAILURE_EVENT:
                        os.fsync(handle.fileno())
                self._write_failed = False
                if new_file and self.on_created is not None:
                    self.on_created(str(path))
                return path
            except Exception as error:
                if not self._write_failed:
                    self._write_failed = True
                    if self.on_error is not None:
                        self.on_error(f"{session['path']}: {error}")
                return None

    @staticmethod
    def _format_message(message):
        if isinstance(message, dict):
            event = message.get("type", "calibration_event")
            value = message.get("value")
            event_time = (
                value if event in START_EVENTS | STOP_EVENTS
                else datetime.now().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]
            )
            level = "ERROR" if event == FAILURE_EVENT else "EVENT"
            return (
                f"[{event_time}] [{level}] {event}: "
                + json.dumps(value, ensure_ascii=False, default=str)
            )
        text = str(message)
        first_part = text.split(" | ", 1)[0].strip().strip("[]")
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S"):
            try:
                datetime.strptime(first_part, fmt)
                return text
            except ValueError:
                pass
        return f"[{datetime.now():%Y-%m-%d %H:%M:%S.%f}] {text}"
