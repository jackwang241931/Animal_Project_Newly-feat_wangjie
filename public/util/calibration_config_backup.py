from __future__ import annotations

import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path


VALID_CALIBRATION_TYPES = {"span", "air"}
VALID_MODULES = {"o2", "co2"}
DEFAULT_MAX_BACKUPS_PER_GROUP = 100


def _safe_label(value: str) -> str:
    label = re.sub(r"[^0-9A-Za-z_-]+", "_", str(value)).strip("_")
    if not label:
        raise ValueError("calibration backup label cannot be empty")
    return label


def get_calibration_backup_root(config_path) -> Path:
    """Return the runtime backup directory beside the project's config/data dirs."""
    source = Path(config_path).resolve()
    if source.parent.name.lower() == "config":
        project_root = source.parent.parent
    else:
        project_root = source.parent
    return project_root / "data" / "calibration_backups"


def snapshot_calibration_config(
        config_path,
        calibration_type,
        module,
        snapshot_label,
        *,
        backup_root=None,
        max_backups=DEFAULT_MAX_BACKUPS_PER_GROUP,
):
    """Copy a calibration JSON file to its timestamped runtime history folder.

    The source is copied byte-for-byte so any snapshot can be restored directly.
    A missing source is valid for the first calibration and returns ``None``.
    """
    source = Path(config_path).resolve()
    if not source.is_file():
        return None

    calibration_type = str(calibration_type).lower()
    module = str(module).lower()
    if calibration_type not in VALID_CALIBRATION_TYPES:
        raise ValueError(f"unsupported calibration type: {calibration_type}")
    if module not in VALID_MODULES:
        raise ValueError(f"unsupported calibration module: {module}")

    label = _safe_label(snapshot_label)
    root = (
        Path(backup_root).resolve()
        if backup_root is not None
        else get_calibration_backup_root(source)
    )
    destination_dir = root / calibration_type / module
    destination_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    suffix = source.suffix or ".json"
    destination = destination_dir / (
        f"{source.stem}_{label}_{timestamp}{suffix}"
    )
    descriptor, temporary_name = tempfile.mkstemp(
        dir=destination_dir,
        prefix=f".{source.stem}_{label}_",
        suffix=".tmp",
    )
    os.close(descriptor)
    temporary = Path(temporary_name)
    try:
        shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        temporary.unlink(missing_ok=True)

    _prune_group(destination_dir, max_backups)
    return destination


def _prune_group(destination_dir: Path, max_backups) -> None:
    try:
        limit = max(2, int(max_backups))
    except (TypeError, ValueError):
        limit = DEFAULT_MAX_BACKUPS_PER_GROUP
    snapshots = sorted(
        (
            path for path in destination_dir.glob("*.json")
            if path.is_file()
        ),
        key=lambda path: (path.stat().st_mtime_ns, path.name),
        reverse=True,
    )
    for stale_path in snapshots[limit:]:
        stale_path.unlink(missing_ok=True)
