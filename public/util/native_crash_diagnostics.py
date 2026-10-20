"""Crash-safe diagnostics for failures inside native extension modules.

The application uses the Windows ``spawn`` multiprocessing start method, so
each worker must enable ``faulthandler`` for itself.  Loguru's queued sinks are
excellent for normal logging but their last records can still be waiting in a
queue when the process receives an access violation.  This module therefore
also keeps a small, unbuffered, per-process breadcrumb log around native calls.

The module intentionally imports no Qt, OpenCV, Torch, camera SDK, or project
service module.  It is safe to initialize before those native runtimes load.
"""

from __future__ import annotations

import atexit
import faulthandler
import importlib.metadata
import itertools
import json
import multiprocessing
import os
import platform
import sys
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator


_STATE_LOCK = threading.RLock()
_WRITE_LOCK = threading.Lock()
_FAULT_FILE = None
_STEP_FILE = None
_STEP_PATH: Path | None = None
_PROCESS_LABEL = None
_SEQUENCE = itertools.count(1)
_THREAD_MONITOR_STOP = None
_THREAD_MONITOR = None

_MAX_STEP_LOG_BYTES = 16 * 1024 * 1024
_PACKAGE_NAMES = (
    "PyQt6",
    "PyQt6-Qt6",
    "PyQt6-sip",
    "opencv-python",
    "torch",
    "torchvision",
    "ultralytics",
    "pyrealsense2",
    "numpy",
)


def _safe_name(value: Any) -> str:
    text = str(value or "unknown").strip() or "unknown"
    return "".join(
        character if character.isalnum() or character in "-_." else "_"
        for character in text
    )[:80]


def _current_process_label(process_id: str | None = None) -> str:
    if process_id:
        return _safe_name(process_id)
    try:
        name = multiprocessing.current_process().name
    except Exception:
        name = None
    return _safe_name(name or Path(sys.argv[0]).stem or "python")


def _git_revision(project_root: Path) -> str | None:
    """Read the current revision without starting another process."""
    try:
        git_dir = project_root / ".git"
        if git_dir.is_file():
            marker = git_dir.read_text(encoding="utf-8", errors="replace").strip()
            if marker.lower().startswith("gitdir:"):
                git_dir = (project_root / marker.split(":", 1)[1].strip()).resolve()
        head = (git_dir / "HEAD").read_text(encoding="ascii", errors="replace").strip()
        if head.startswith("ref:"):
            ref_path = git_dir / head.split(":", 1)[1].strip()
            return ref_path.read_text(encoding="ascii", errors="replace").strip()
        return head or None
    except Exception:
        return None


def _package_versions() -> dict[str, str]:
    versions: dict[str, str] = {}
    for package_name in _PACKAGE_NAMES:
        try:
            versions[package_name] = importlib.metadata.version(package_name)
        except importlib.metadata.PackageNotFoundError:
            versions[package_name] = "not-installed"
        except Exception as error:
            versions[package_name] = f"unavailable:{type(error).__name__}"
    return versions


def _json_bytes(record: dict[str, Any]) -> bytes:
    return (
        json.dumps(record, ensure_ascii=False, default=str, separators=(",", ":"))
        + "\n"
    ).encode("utf-8", errors="replace")


def _rotate_step_log_if_needed() -> None:
    global _STEP_FILE
    if _STEP_FILE is None or _STEP_PATH is None:
        return
    try:
        if _STEP_FILE.tell() < _MAX_STEP_LOG_BYTES:
            return
        _STEP_FILE.close()
        backup_path = _STEP_PATH.with_suffix(_STEP_PATH.suffix + ".1")
        try:
            backup_path.unlink(missing_ok=True)
        except TypeError:  # Python < 3.8 compatibility for utility reuse.
            if backup_path.exists():
                backup_path.unlink()
        os.replace(_STEP_PATH, backup_path)
        _STEP_FILE = open(_STEP_PATH, "ab", buffering=0)
    except Exception:
        try:
            _STEP_FILE = open(_STEP_PATH, "ab", buffering=0)
        except Exception:
            _STEP_FILE = None


def _write_record(record: dict[str, Any]) -> None:
    if _STEP_FILE is None:
        return
    try:
        with _WRITE_LOCK:
            _rotate_step_log_if_needed()
            if _STEP_FILE is not None:
                _STEP_FILE.write(_json_bytes(record))
    except Exception:
        # Diagnostics must never become a new application failure.
        pass


def _base_record(component: str, operation: str, state: str) -> dict[str, Any]:
    native_id = getattr(threading, "get_native_id", lambda: None)()
    return {
        "sequence": next(_SEQUENCE),
        "time": datetime.now().astimezone().isoformat(timespec="microseconds"),
        "monotonic_ns": time.monotonic_ns(),
        "pid": os.getpid(),
        "ppid": os.getppid(),
        "process": _PROCESS_LABEL or _current_process_label(),
        "thread": threading.current_thread().name,
        "python_tid": threading.get_ident(),
        "native_tid": native_id,
        "native_tid_hex": f"0x{native_id:X}" if native_id is not None else None,
        "component": str(component),
        "operation": str(operation),
        "state": str(state),
    }


def crash_breadcrumb(
    component: str,
    operation: str,
    state: str,
    **fields: Any,
) -> None:
    """Write one unbuffered native-call breadcrumb; never raise to the caller."""
    if _STEP_FILE is None:
        setup_native_crash_diagnostics()
    record = _base_record(component, operation, state)
    if fields:
        record["fields"] = fields
    _write_record(record)


@contextmanager
def native_call(component: str, operation: str, **fields: Any) -> Iterator[None]:
    """Mark entry and completion around a potentially unsafe native call.

    If the process dies inside the wrapped call, the last durable record is an
    ``ENTER`` without a matching ``EXIT``.  Python exceptions are marked
    separately before they are re-raised.
    """
    call_id = f"{os.getpid()}-{threading.get_ident()}-{time.monotonic_ns()}"
    started_ns = time.monotonic_ns()
    crash_breadcrumb(
        component,
        operation,
        "ENTER",
        call_id=call_id,
        **fields,
    )
    try:
        yield
    except BaseException as error:
        crash_breadcrumb(
            component,
            operation,
            "PY_EXCEPTION",
            call_id=call_id,
            duration_ms=(time.monotonic_ns() - started_ns) / 1_000_000.0,
            exception_type=type(error).__name__,
            exception_message=str(error),
        )
        raise
    else:
        crash_breadcrumb(
            component,
            operation,
            "EXIT",
            call_id=call_id,
            duration_ms=(time.monotonic_ns() - started_ns) / 1_000_000.0,
        )


def _monitor_thread_lifecycle(stop_event: threading.Event) -> None:
    known: dict[int, dict[str, Any]] = {}
    while not stop_event.wait(1.0):
        current = {
            thread.ident: {
                "name": thread.name,
                "ident": thread.ident,
                "native_id": getattr(thread, "native_id", None),
                "daemon": thread.daemon,
            }
            for thread in threading.enumerate()
            if thread.ident is not None
        }
        for ident, details in current.items():
            if ident not in known:
                crash_breadcrumb("thread", details["name"], "STARTED", **details)
        for ident, details in known.items():
            if ident not in current:
                crash_breadcrumb("thread", details["name"], "EXITED", **details)
        known = current


def _close_native_crash_diagnostics() -> None:
    global _FAULT_FILE, _STEP_FILE
    stop_event = _THREAD_MONITOR_STOP
    if stop_event is not None:
        stop_event.set()
    crash_breadcrumb("process", "shutdown", "EXIT")
    try:
        faulthandler.disable()
    except Exception:
        pass
    for file_object_name in ("_FAULT_FILE", "_STEP_FILE"):
        file_object = globals().get(file_object_name)
        globals()[file_object_name] = None
        if file_object is not None:
            try:
                file_object.close()
            except Exception:
                pass


def setup_native_crash_diagnostics(
    process_id: str | None = None,
    crash_root: str | os.PathLike[str] | None = None,
) -> dict[str, str] | None:
    """Enable per-process faulthandler and unbuffered native breadcrumbs."""
    global _FAULT_FILE, _STEP_FILE, _STEP_PATH, _PROCESS_LABEL
    global _THREAD_MONITOR_STOP, _THREAD_MONITOR

    with _STATE_LOCK:
        if _FAULT_FILE is not None and _STEP_FILE is not None:
            return {
                "process": str(_PROCESS_LABEL),
                "fault_log": str(getattr(_FAULT_FILE, "name", "")),
                "step_log": str(_STEP_PATH or ""),
            }
        try:
            _PROCESS_LABEL = _current_process_label(process_id)
            root_path = Path(crash_root) if crash_root else Path.cwd() / "crash_dumps"
            process_dir = root_path.resolve() / _PROCESS_LABEL
            process_dir.mkdir(parents=True, exist_ok=True)
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            fault_path = process_dir / (
                f"python_fault_{timestamp}_{_PROCESS_LABEL}_pid{os.getpid()}.log"
            )
            _STEP_PATH = process_dir / (
                f"native_steps_{timestamp}_{_PROCESS_LABEL}_pid{os.getpid()}.jsonl"
            )
            _FAULT_FILE = open(fault_path, "ab", buffering=0)
            _STEP_FILE = open(_STEP_PATH, "ab", buffering=0)

            startup = _base_record("process", "startup", "ENTER")
            startup["fields"] = {
                "python_executable": sys.executable,
                "python_version": sys.version,
                "platform": platform.platform(),
                "argv": list(sys.argv),
                "cwd": str(Path.cwd()),
                "git_revision": _git_revision(Path.cwd()),
                "package_versions": _package_versions(),
            }
            _write_record(startup)
            _FAULT_FILE.write(
                (
                    "NATIVE CRASH DIAGNOSTICS\n"
                    f"process={_PROCESS_LABEL}\n"
                    f"pid={os.getpid()} ppid={os.getppid()}\n"
                    f"python={sys.executable}\n"
                    f"version={sys.version}\n"
                    f"breadcrumbs={_STEP_PATH}\n"
                    + "-" * 80
                    + "\n"
                ).encode("utf-8", errors="replace")
            )
            faulthandler.enable(file=_FAULT_FILE, all_threads=True)

            _THREAD_MONITOR_STOP = threading.Event()
            _THREAD_MONITOR = threading.Thread(
                target=_monitor_thread_lifecycle,
                args=(_THREAD_MONITOR_STOP,),
                name="native_crash_thread_monitor",
                daemon=True,
            )
            _THREAD_MONITOR.start()
            atexit.register(_close_native_crash_diagnostics)
            return {
                "process": _PROCESS_LABEL,
                "fault_log": str(fault_path),
                "step_log": str(_STEP_PATH),
            }
        except Exception as error:
            try:
                sys.stderr.write(
                    "native crash diagnostics setup failed: "
                    f"{type(error).__name__}: {error}\n"
                )
                sys.stderr.flush()
            except Exception:
                pass
            return None


def dump_python_thread_stacks(reason: str) -> None:
    """Append all current Python stacks to the process fault file on demand."""
    crash_breadcrumb("process", "dump_python_thread_stacks", "ENTER", reason=reason)
    try:
        if _FAULT_FILE is not None:
            _FAULT_FILE.write(
                f"\nMANUAL STACK DUMP: {reason}\n".encode(
                    "utf-8", errors="replace"
                )
            )
            faulthandler.dump_traceback(file=_FAULT_FILE, all_threads=True)
    except Exception:
        pass


def diagnostic_paths() -> dict[str, str] | None:
    if _FAULT_FILE is None:
        return None
    return {
        "process": str(_PROCESS_LABEL),
        "fault_log": str(getattr(_FAULT_FILE, "name", "")),
        "step_log": str(_STEP_PATH or ""),
    }
