"""Crash-safe experiment marker and emergency Excel recovery helpers."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
import time

from loguru import logger


MARKER_VERSION = 1


def write_active_experiment_marker(
        marker_path: str,
        db_path: str,
        excel_path: str,
        started_at: float | None = None,
) -> dict:
    """Atomically record the files needed to recover an interrupted experiment."""
    marker_path = os.path.abspath(marker_path)
    marker_dir = os.path.dirname(marker_path)
    os.makedirs(marker_dir, exist_ok=True)
    payload = {
        "version": MARKER_VERSION,
        "active": True,
        "db_path": os.path.abspath(db_path),
        "excel_path": os.path.abspath(excel_path),
        "started_at": float(started_at if started_at is not None else time.time()),
        "updated_at": time.time(),
    }
    descriptor, temp_path = tempfile.mkstemp(
        prefix=f".{os.path.basename(marker_path)}.",
        suffix=".tmp",
        dir=marker_dir,
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as marker_file:
            json.dump(payload, marker_file, ensure_ascii=False, indent=2)
            marker_file.flush()
            os.fsync(marker_file.fileno())
        os.replace(temp_path, marker_path)
    except Exception:
        try:
            os.close(descriptor)
        except OSError:
            pass
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise
    return payload


def read_active_experiment_marker(marker_path: str) -> dict | None:
    if not marker_path or not os.path.exists(marker_path):
        return None
    try:
        with open(marker_path, "r", encoding="utf-8") as marker_file:
            payload = json.load(marker_file)
    except (OSError, ValueError) as error:
        logger.error(f"读取活动实验恢复标记失败: {error}")
        return None
    if not isinstance(payload, dict) or not payload.get("active"):
        return None
    if not payload.get("db_path") or not payload.get("excel_path"):
        logger.error("活动实验恢复标记缺少数据库或Excel路径")
        return None
    return payload


def clear_active_experiment_marker(marker_path: str) -> None:
    try:
        os.remove(marker_path)
    except FileNotFoundError:
        pass


def create_sqlite_snapshot(source_db_path: str, snapshot_db_path: str) -> None:
    """Create a consistent online backup without copying WAL sidecars directly."""
    source_connection = None
    snapshot_connection = None
    try:
        source_connection = sqlite3.connect(source_db_path, timeout=30.0)
        source_connection.execute("PRAGMA busy_timeout=30000")
        snapshot_connection = sqlite3.connect(snapshot_db_path, timeout=30.0)
        source_connection.backup(snapshot_connection, pages=1024, sleep=0.05)
    finally:
        if snapshot_connection is not None:
            snapshot_connection.close()
        if source_connection is not None:
            source_connection.close()


def export_experiment_snapshot(db_path: str, excel_path: str) -> str:
    """Export one live experiment database through a temporary SQLite snapshot."""
    db_path = os.path.abspath(db_path)
    excel_path = os.path.abspath(excel_path)
    if not os.path.exists(db_path):
        raise FileNotFoundError(f"实验数据库不存在: {db_path}")

    export_dir = os.path.dirname(excel_path)
    if export_dir:
        os.makedirs(export_dir, exist_ok=True)

    snapshot_dir = os.path.dirname(db_path) or tempfile.gettempdir()
    descriptor, snapshot_path = tempfile.mkstemp(
        prefix=".emergency_export_",
        suffix=".snapshot.db",
        dir=snapshot_dir,
    )
    os.close(descriptor)
    try:
        create_sqlite_snapshot(db_path, snapshot_path)

        from public.config_class import global_load
        from public.util.custom_data_file_util import custom_data_file_util

        global_load.load_global_setting()
        success = custom_data_file_util.export_data_to_csv(
            export_file_path=excel_path,
            file_path=snapshot_path,
            show_success_message=False,
            use_atomic_replace=True,
        )
        if not success:
            detail = custom_data_file_util.get_last_export_error()
            raise RuntimeError(detail or "紧急Excel导出失败")
        return excel_path
    finally:
        for candidate in (snapshot_path, f"{snapshot_path}-wal", f"{snapshot_path}-shm"):
            try:
                os.remove(candidate)
            except FileNotFoundError:
                pass
            except OSError as error:
                logger.error(f"删除紧急导出快照失败: {candidate}: {error}")
