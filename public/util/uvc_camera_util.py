from __future__ import annotations

from typing import Any, Iterable

import cv2

try:
    from cv2_enumerate_cameras import enumerate_cameras
except ImportError:  # pragma: no cover - surfaced to the configuration UI.
    enumerate_cameras = None


STABLE_ID_PREFIX = "uvc_path:"


def normalize_device_path(value: Any) -> str:
    return str(value or "").strip().replace("/", "\\").lower()


def stable_id_from_path(device_path: Any) -> str:
    normalized = normalize_device_path(device_path)
    return f"{STABLE_ID_PREFIX}{normalized}" if normalized else ""


def camera_device_path(camera: dict[str, Any] | None) -> str:
    if not isinstance(camera, dict):
        return ""
    path = str(camera.get("device_path") or "").strip()
    if path:
        return path
    instance_id = str(camera.get("instance_id") or "").strip()
    if instance_id.startswith(("\\\\?\\", "\\\\.\\")):
        return instance_id
    return ""


def camera_config_identity(camera: dict[str, Any] | None) -> str:
    if not isinstance(camera, dict):
        return ""
    stable_id = str(camera.get("stable_id") or "").strip().lower()
    if stable_id:
        return stable_id
    return stable_id_from_path(camera_device_path(camera))


def camera_logical_index(camera: dict[str, Any] | None) -> int | None:
    if not isinstance(camera, dict):
        return None
    value = camera.get("logical_index")
    if value is not None:
        try:
            logical_index = int(value)
            return logical_index if logical_index >= 0 else None
        except (TypeError, ValueError):
            pass

    serial = str(camera.get("serial") or "").strip().lower()
    if serial.startswith("uvc_index_"):
        suffix = serial.replace("uvc_index_", "", 1)
        if suffix.isdigit():
            return int(suffix)

    device_index = camera.get("device_index")
    try:
        device_index = int(device_index)
        return device_index if device_index >= 0 else None
    except (TypeError, ValueError):
        return None


def _short_device_id(device_path: str) -> str:
    parts = str(device_path or "").split("#")
    if len(parts) >= 3 and parts[2]:
        return parts[2][-16:].upper()
    normalized = normalize_device_path(device_path)
    return normalized[-16:].upper() if normalized else "UNKNOWN"


def _camera_info_to_dict(camera_info: Any, *, probe: bool) -> dict[str, Any]:
    device_index = int(camera_info.index)
    backend = int(camera_info.backend)
    device_path = str(camera_info.path or "")
    name = str(camera_info.name or f"UVC Camera {device_index}")
    width = 0
    height = 0
    available = True

    if probe:
        capture = None
        available = False
        try:
            capture = cv2.VideoCapture(device_index, backend)
            if capture is not None and capture.isOpened():
                for _ in range(3):
                    try:
                        ok, _frame = capture.read()
                    except cv2.error:
                        ok = False
                    if ok:
                        available = True
                        break
                width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH) or 0)
                height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT) or 0)
        except cv2.error:
            available = False
        finally:
            if capture is not None:
                capture.release()

    return {
        "stable_id": stable_id_from_path(device_path),
        "instance_id": device_path,
        "device_path": device_path,
        "device_name": name,
        "device_index": device_index,
        "backend": backend,
        "width": width,
        "height": height,
        "available": available,
        "vid": int(camera_info.vid) if camera_info.vid is not None else None,
        "pid": int(camera_info.pid) if camera_info.pid is not None else None,
    }


def enumerate_uvc_cameras(*, probe: bool = False) -> list[dict[str, Any]]:
    if enumerate_cameras is None:
        raise RuntimeError(
            "缺少cv2-enumerate-cameras，请先根据requirements.txt安装依赖"
        )

    backends = []
    if hasattr(cv2, "CAP_DSHOW"):
        backends.append(cv2.CAP_DSHOW)
    if hasattr(cv2, "CAP_MSMF"):
        backends.append(cv2.CAP_MSMF)

    last_error: Exception | None = None
    for backend in backends:
        try:
            cameras = [
                _camera_info_to_dict(camera_info, probe=probe)
                for camera_info in enumerate_cameras(backend)
            ]
            if cameras:
                return cameras
        except Exception as error:
            last_error = error

    if last_error is not None:
        raise RuntimeError(f"枚举UVC相机失败: {last_error}") from last_error
    return []


def set_camera_logical_index(camera: dict[str, Any], logical_index: int) -> dict[str, Any]:
    result = dict(camera)
    width = int(result.get("width") or 0)
    height = int(result.get("height") or 0)
    size_text = f" ({width}x{height})" if width and height else ""
    device_name = str(result.get("device_name") or "UVC Camera")
    device_path = camera_device_path(result)
    result.update(
        {
            "logical_index": logical_index,
            "serial": f"uvc_index_{logical_index}",
            "instance_id": device_path,
            "device_path": device_path,
            "stable_id": stable_id_from_path(device_path),
            "display_name": (
                f"UVC Camera {logical_index}{size_text} - "
                f"{device_name} [{_short_device_id(device_path)}]"
            ),
        }
    )
    return result


def assign_logical_camera_indices(
    current_cameras: Iterable[dict[str, Any]],
    saved_cameras: Iterable[dict[str, Any]],
) -> list[dict[str, Any]]:
    saved_items = [dict(item) for item in saved_cameras if isinstance(item, dict)]
    reserved_indices = {
        logical_index
        for item in saved_items
        if (logical_index := camera_logical_index(item)) is not None
    }
    saved_by_identity = {
        identity: item
        for item in saved_items
        if (identity := camera_config_identity(item))
    }
    legacy_by_device_index = {
        int(item["device_index"]): item
        for item in saved_items
        if not camera_config_identity(item)
        and item.get("device_index") is not None
        and str(item.get("device_index")).isdigit()
    }

    assigned_indices = set()
    pending = []
    result = []
    for camera in current_cameras:
        camera = dict(camera)
        saved = saved_by_identity.get(camera_config_identity(camera))
        if saved is None:
            saved = legacy_by_device_index.get(int(camera.get("device_index", -1)))
        logical_index = camera_logical_index(saved)
        if logical_index is None or logical_index in assigned_indices:
            pending.append(camera)
            continue
        assigned_indices.add(logical_index)
        result.append(set_camera_logical_index(camera, logical_index))

    next_index = max(reserved_indices | assigned_indices, default=-1) + 1
    for camera in pending:
        while next_index in reserved_indices or next_index in assigned_indices:
            next_index += 1
        assigned_indices.add(next_index)
        result.append(set_camera_logical_index(camera, next_index))
        next_index += 1

    result.sort(key=lambda item: int(item["logical_index"]))
    return result


def resolve_camera_config(
    camera_config: dict[str, Any],
    current_cameras: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    configured_identity = camera_config_identity(camera_config)
    configured_logical_index = camera_logical_index(camera_config)

    for camera in current_cameras:
        if configured_identity:
            matches = camera_config_identity(camera) == configured_identity
        else:
            matches = camera_logical_index(camera) == configured_logical_index
        if not matches:
            continue

        logical_index = configured_logical_index
        if logical_index is None:
            logical_index = camera_logical_index(camera)
        resolved = set_camera_logical_index(camera, logical_index)
        if "mouse_cage_number" in camera_config:
            resolved["mouse_cage_number"] = camera_config["mouse_cage_number"]
        return resolved
    return None
