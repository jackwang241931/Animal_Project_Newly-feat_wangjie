"""Formatting helpers for the raw 30-point weighing series."""

from __future__ import annotations

import math
from numbers import Real


WEIGHT_SERIES_LENGTH = 30


def _to_finite_float(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        numeric_value = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric_value):
        return None
    return numeric_value


def _format_point(value):
    numeric_value = _to_finite_float(value)
    if numeric_value is None:
        return None
    return f"{numeric_value:.2f}"


def _is_series(value):
    return isinstance(value, (list, tuple)) or (
        isinstance(value, str) and "," in value
    )


def format_weight_series(value, length: int = WEIGHT_SERIES_LENGTH) -> str:
    """Return a fixed-length, comma-separated display value for WM data."""
    if value is None:
        return "None"

    if _is_series(value):
        items = value.split(",") if isinstance(value, str) else list(value)
        items = items[:length]
        items.extend([None] * (length - len(items)))
        formatted = [_format_point(item) or "None" for item in items]
        return ",".join(formatted)

    formatted_value = _format_point(value)
    return formatted_value or "None"


def format_weight_series_for_storage(value, length: int = WEIGHT_SERIES_LENGTH):
    """Normalize a WM value before SQLite insertion or Excel export."""
    if value is None:
        return None
    if _is_series(value):
        return format_weight_series(value, length=length)
    if isinstance(value, Real) and not isinstance(value, bool):
        return format_weight_series(value, length=length)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "none", "null", "nan"}:
            return None
        return format_weight_series(value, length=length)
    return None
