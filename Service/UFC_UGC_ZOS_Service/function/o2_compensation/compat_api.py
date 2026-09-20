"""Stable public API for replaceable oxygen compensation implementations.

The oxygen algorithm files are supplied independently and may be replaced by
another compatible implementation.  This module keeps the host application's
contract stable and applies project-wide safety rules at the boundary.
"""

from __future__ import annotations

import configparser
import inspect
import math
import threading
from pathlib import Path
from typing import Any

try:
    from loguru import logger
except Exception:  # pragma: no cover - logging must never break the API
    import logging

    logger = logging.getLogger(__name__)

from . import o2_realtime_core as _core


_WARNED_MISSING_REFERENCE: set[str] = set()
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_POINTS_CONFIG = _PROJECT_ROOT / "config" / "UFC_UGC_ZOS_Test.ini"
_CALIBRATION_HANDLER_LOCK = threading.RLock()
_CALIBRATION_HANDLER: Any | None = None
_CALIBRATION_HANDLER_TARGET_POINTS: int | None = None

_CALIBRATION_HANDLER_METHODS = (
    "start_new_calibration",
    "add_data",
    "get_status",
)


def _configured_target_points() -> int:
    """Read the shared calibration count instead of trusting vendor defaults."""
    parser = configparser.ConfigParser()
    try:
        parser.read(_POINTS_CONFIG, encoding="utf-8")
        value = parser.getint("Calibration", "startup_air_calibration_target_points", fallback=120)
        return max(1, value)
    except (OSError, ValueError, configparser.Error) as exc:
        logger.warning(
            f"Unable to read O2 calibration point count from {_POINTS_CONFIG}: {exc}; using 120"
        )
        return 120


def _valid_channels() -> set[str]:
    return set(getattr(_core, "VALID_CHANNELS", ["REF"] + [f"M{i}" for i in range(1, 9)]))


def _load_calibration_handler_class():
    """Load and validate the replaceable supplier calibration handler."""
    from .calibration_handler import CalibrationHandler

    missing = [
        name
        for name in _CALIBRATION_HANDLER_METHODS
        if not callable(getattr(CalibrationHandler, name, None))
    ]
    if missing:
        raise TypeError(
            "O2 CalibrationHandler supplier contract is incomplete; "
            f"missing methods: {', '.join(missing)}"
        )
    return CalibrationHandler


def create_o2_calibration_handler(
    target_points: int | None = None,
    config_path: str | Path | None = None,
):
    """Create a supplier handler while keeping project settings authoritative."""
    points = _configured_target_points() if target_points is None else int(target_points)
    if points <= 0:
        raise ValueError(f"O2 calibration target_points must be positive, got {points}")

    handler_class = _load_calibration_handler_class()
    try:
        signature = inspect.signature(handler_class)
        parameters = signature.parameters
        accepts_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
    except (TypeError, ValueError):
        parameters = {}
        accepts_kwargs = True

    if "target_points" not in parameters and not accepts_kwargs:
        raise TypeError(
            "O2 CalibrationHandler supplier contract requires a target_points argument"
        )

    kwargs = {"target_points": points}
    if config_path is not None and ("config_path" in parameters or accepts_kwargs):
        kwargs["config_path"] = str(config_path)

    return handler_class(**kwargs)


def _has_reference_sample(compensator: Any) -> bool:
    checker = getattr(compensator, "has_valid_reference_sample", None)
    if callable(checker):
        try:
            return bool(checker())
        except Exception as exc:
            logger.warning(f"O2 reference sample check failed: {exc}")
            return False

    # Older supplier implementations expose only the buffer.
    buffer = getattr(compensator, "dry_ref_buffer", None)
    if buffer is None:
        return False
    try:
        return any(math.isfinite(float(value)) for value in buffer)
    except (TypeError, ValueError, OverflowError):
        return False


def get_realtime_o2_compensator():
    return _core.get_realtime_o2_compensator()


def has_valid_reference_dry_oxygen_sample() -> bool:
    """Return True only when a real REF dry-oxygen sample is available."""
    return _has_reference_sample(get_realtime_o2_compensator())


def reload_o2_compensation_config():
    return _core.reload_o2_compensation_config()


def get_o2_calibration_handler(target_points: int | None = None):
    global _CALIBRATION_HANDLER, _CALIBRATION_HANDLER_TARGET_POINTS
    points = _configured_target_points() if target_points is None else int(target_points)
    with _CALIBRATION_HANDLER_LOCK:
        if _CALIBRATION_HANDLER is None or _CALIBRATION_HANDLER_TARGET_POINTS != points:
            _CALIBRATION_HANDLER = create_o2_calibration_handler(
                target_points=points,
                config_path=_PROJECT_ROOT / "config" / "calib_config.json",
            )
            _CALIBRATION_HANDLER_TARGET_POINTS = points
        return _CALIBRATION_HANDLER


def start_new_o2_calibration(target_points: int | None = None):
    handler = get_o2_calibration_handler(target_points=target_points)
    return bool(handler.start_new_calibration())


def append_o2_calibration_data(channel, o2_partial, zos_temp, gas_pressure, o2_percent, zos_rh):
    handler = get_o2_calibration_handler()
    success = bool(handler.add_data(
        channel=channel,
        o2_partial=o2_partial,
        zos_temp=zos_temp,
        gas_pressure=gas_pressure,
        o2_percent=o2_percent,
        zos_rh=zos_rh,
    ))
    if success:
        reload_o2_compensation_config()
    return success


def get_o2_calibration_status():
    status = get_o2_calibration_handler().get_status()
    if not isinstance(status, dict):
        raise TypeError("O2 CalibrationHandler.get_status() must return a dictionary")
    return status


def validate_o2_supplier_contract() -> dict[str, Any]:
    """Validate replaceable O2 files without starting an experiment."""
    required_core_functions = (
        "calculate_o2_compensated",
        "get_realtime_o2_compensator",
        "reload_o2_compensation_config",
    )
    missing_core = [
        name for name in required_core_functions if not callable(getattr(_core, name, None))
    ]
    _load_calibration_handler_class()
    if missing_core:
        raise TypeError(
            "O2 realtime supplier contract is incomplete; "
            f"missing functions: {', '.join(missing_core)}"
        )
    return {
        "valid": True,
        "target_points": _configured_target_points(),
        "channels": sorted(_valid_channels()),
        "realtime_functions": list(required_core_functions),
        "calibration_methods": list(_CALIBRATION_HANDLER_METHODS),
    }


def calculate_o2_compensated(
    channel_id,
    o2_partial_press,
    zos_temp,
    gas_total_press,
    o2_raw_pct,
    zos_rh,
):
    """Call the replaceable implementation behind the stable host contract."""
    if channel_id not in _valid_channels():
        return -1

    if channel_id != "REF" and not has_valid_reference_dry_oxygen_sample():
        if channel_id not in _WARNED_MISSING_REFERENCE:
            logger.warning(f"O2 compensation skipped for {channel_id}: no valid REF sample")
            _WARNED_MISSING_REFERENCE.add(channel_id)
        return -1

    try:
        result = _core.calculate_o2_compensated(
            channel_id,
            o2_partial_press,
            zos_temp,
            gas_total_press,
            o2_raw_pct,
            zos_rh,
        )
        numeric_result = float(result)
        if not math.isfinite(numeric_result):
            return -1
        return numeric_result
    except (TypeError, ValueError, OverflowError, ZeroDivisionError) as exc:
        logger.warning(f"O2 compensation rejected {channel_id} sample: {exc}")
        return -1


__all__ = [
    "append_o2_calibration_data",
    "create_o2_calibration_handler",
    "calculate_o2_compensated",
    "get_o2_calibration_handler",
    "get_o2_calibration_status",
    "get_realtime_o2_compensator",
    "has_valid_reference_dry_oxygen_sample",
    "reload_o2_compensation_config",
    "start_new_o2_calibration",
    "validate_o2_supplier_contract",
]
