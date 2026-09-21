"""Background SQLite snapshots for the live analysis view."""

from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from PyQt6.QtCore import QThread, pyqtSignal


class LiveDatabaseSnapshotLoader(QThread):
    """Copy a running experiment database without blocking the GUI thread."""

    loaded = pyqtSignal(int, str, str, float)
    failed = pyqtSignal(int, str, str)

    def __init__(self, generation: int, source_path: str, snapshot_path: str):
        super().__init__()
        self.generation = int(generation)
        self.source_path = str(source_path)
        self.snapshot_path = str(snapshot_path)

    def run(self) -> None:  # pragma: no cover - exercised through Qt signals
        temporary_path = f"{self.snapshot_path}.tmp-{os.getpid()}-{self.generation}"
        source = None
        target = None
        try:
            source_file = Path(self.source_path)
            if not source_file.is_file():
                raise FileNotFoundError(self.source_path)
            Path(self.snapshot_path).parent.mkdir(parents=True, exist_ok=True)
            source = sqlite3.connect(str(source_file), timeout=5.0)
            source.execute("PRAGMA busy_timeout=5000")
            target = sqlite3.connect(temporary_path, timeout=5.0)
            source.backup(target, pages=128, sleep=0.05)
            target.commit()
            target.close()
            target = None
            source.close()
            source = None
            os.replace(temporary_path, self.snapshot_path)
            if not self.isInterruptionRequested():
                self.loaded.emit(
                    self.generation,
                    self.source_path,
                    self.snapshot_path,
                    time.time(),
                )
        except InterruptedError:
            return
        except Exception as error:
            if not self.isInterruptionRequested():
                self.failed.emit(self.generation, self.source_path, str(error))
        finally:
            if target is not None:
                try:
                    target.close()
                except Exception:
                    pass
            if source is not None:
                try:
                    source.close()
                except Exception:
                    pass
            try:
                Path(temporary_path).unlink(missing_ok=True)
            except OSError:
                pass
