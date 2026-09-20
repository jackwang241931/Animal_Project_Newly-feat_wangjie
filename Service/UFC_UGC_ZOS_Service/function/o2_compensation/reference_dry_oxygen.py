from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from threading import RLock


DEFAULT_REFERENCE_DRY_OXYGEN_PERCENT = 20.930

_THREE_DECIMAL_PLACES = Decimal("0.001")
_value_lock = RLock()
_startup_reference_dry_oxygen_percent = DEFAULT_REFERENCE_DRY_OXYGEN_PERCENT
_reference_dry_oxygen_percent = DEFAULT_REFERENCE_DRY_OXYGEN_PERCENT


def _normalize_percent(value):
    try:
        normalized = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Reference dry oxygen value must be numeric") from exc

    if not normalized.is_finite() or not Decimal("0") <= normalized <= Decimal("100"):
        raise ValueError("Reference dry oxygen value must be between 0 and 100")

    return float(normalized.quantize(_THREE_DECIMAL_PLACES, rounding=ROUND_HALF_UP))


def get_reference_dry_oxygen_percent():
    with _value_lock:
        return _reference_dry_oxygen_percent


def get_reference_dry_oxygen_percent_text():
    return f"{get_reference_dry_oxygen_percent():.3f}"


def set_reference_dry_oxygen_percent(value):
    """Update the runtime REF dry oxygen value for a future span calibration."""
    normalized = _normalize_percent(value)
    with _value_lock:
        global _reference_dry_oxygen_percent
        _reference_dry_oxygen_percent = normalized
        return _reference_dry_oxygen_percent


def initialize_reference_dry_oxygen_percent(config):
    """Load the process-start value once from the ZOS configuration."""
    try:
        configured_value = config.get("ZOS", {}).get(
            "reference_dry_oxygen_percent",
            DEFAULT_REFERENCE_DRY_OXYGEN_PERCENT,
        )
        normalized = _normalize_percent(configured_value)
    except (AttributeError, ValueError):
        normalized = DEFAULT_REFERENCE_DRY_OXYGEN_PERCENT

    with _value_lock:
        global _startup_reference_dry_oxygen_percent
        global _reference_dry_oxygen_percent
        _startup_reference_dry_oxygen_percent = normalized
        _reference_dry_oxygen_percent = normalized
        return _reference_dry_oxygen_percent


def reset_reference_dry_oxygen_percent():
    """Restore the value loaded when the monitoring process started."""
    with _value_lock:
        global _reference_dry_oxygen_percent
        _reference_dry_oxygen_percent = _startup_reference_dry_oxygen_percent
        return _reference_dry_oxygen_percent
