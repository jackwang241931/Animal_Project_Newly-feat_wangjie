from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


LOCK_FILE_NAME = "video_program.lock"


def default_lock_path() -> Path:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        root = Path(base) / "AnimalProject" / "runtime"
    else:
        root = Path(tempfile.gettempdir()) / "AnimalProject" / "runtime"
    return root / LOCK_FILE_NAME


class VideoProgramInstanceLock:
    """Crash-safe camera ownership lock shared with the standalone program."""

    def __init__(self, mode: str, path: Path | str | None = None):
        self.mode = str(mode)
        self.path = Path(path) if path is not None else default_lock_path()
        self.guard_path = self.path.with_name(f"{self.path.name}.guard")
        self._handle = None

    @property
    def acquired(self) -> bool:
        return self._handle is not None

    def acquire(self) -> bool:
        if self._handle is not None:
            return True

        self.path.parent.mkdir(parents=True, exist_ok=True)
        handle = open(self.guard_path, "a+b")
        try:
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, ImportError):
            handle.close()
            return False

        self._handle = handle
        try:
            self._write_owner_metadata()
        except OSError:
            self.release()
            return False
        return True

    def _write_owner_metadata(self) -> None:
        payload = json.dumps(
            {"pid": os.getpid(), "mode": self.mode},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        with open(self.path, "wb") as owner_file:
            owner_file.write(payload)
            owner_file.flush()

    def read_owner(self) -> dict[str, Any]:
        try:
            with open(self.path, "rb") as owner_file:
                raw = owner_file.read()
            data = json.loads(raw.decode("utf-8")) if raw else {}
            return data if isinstance(data, dict) else {}
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            return {}

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        if handle is None:
            return
        try:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except (OSError, ImportError):
            pass
        finally:
            handle.close()
