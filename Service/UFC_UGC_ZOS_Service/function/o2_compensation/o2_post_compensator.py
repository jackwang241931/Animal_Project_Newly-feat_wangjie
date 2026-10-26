"""Optional second-stage compensation for dry-basis cage oxygen values.

The supplier coefficients describe the oxygen-consumption difference
``REF - cage``.  This module deliberately sits behind a configuration switch
so that introducing the code does not silently rewrite production data before
the coefficients have been verified against real experiments.
"""

from __future__ import annotations

import json
import math
import threading
from pathlib import Path

try:
    from loguru import logger
except Exception:  # pragma: no cover - logging must never break acquisition
    import logging

    logger = logging.getLogger(__name__)


VALID_CAGE_CHANNELS = tuple(f"M{i}" for i in range(1, 9))
DEFAULT_DO2_GAIN = {
    "M1": 0.500,
    "M2": 0.250,
    "M3": 0.600,
    "M4": 0.580,
    "M5": 0.971,
    "M6": 0.700,
    "M7": 0.700,
    "M8": 0.550,
}
DEFAULT_DO2_BIAS = {
    "M1": 0.000,
    "M2": 0.000,
    "M3": 0.000,
    "M4": 0.010,
    "M5": -0.005,
    "M6": 0.000,
    "M7": 0.000,
    "M8": 0.000,
}
DEFAULT_MIN_DO2 = 0.03

_POST_COMPENSATOR_LOCK = threading.Lock()
_POST_COMPENSATOR = None


def get_default_config_path() -> Path:
    """Return the project-level calibration config used by the host app."""
    return Path(__file__).resolve().parents[4] / "config" / "calib_config.json"


def _normalize_channel(channel) -> str | None:
    if isinstance(channel, bool):
        return None
    if isinstance(channel, int):
        channel = f"M{channel}"
    else:
        channel = str(channel).strip().upper()
        if channel.isdigit():
            channel = f"M{channel}"
    return channel if channel in VALID_CAGE_CHANNELS else None


def _finite_percent(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(value) or not 0.0 <= value <= 100.0:
        return None
    return value


def _as_enabled(value) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


class O2PostCompensator:
    """Load post-compensation settings and apply them safely."""

    def __init__(self, config_path=None):
        self.config_path = Path(config_path or get_default_config_path())
        self._lock = threading.RLock()
        self._config_mtime_ns = None
        self.enabled = False
        self.min_do2 = DEFAULT_MIN_DO2
        self.gain = dict(DEFAULT_DO2_GAIN)
        self.bias = dict(DEFAULT_DO2_BIAS)
        self.reload_config(force=True)

    def _get_config_mtime_ns(self):
        try:
            return self.config_path.stat().st_mtime_ns
        except OSError:
            return None

    def ensure_latest(self):
        self.reload_config(force=False)

    def reload_config(self, force=False):
        current_mtime = self._get_config_mtime_ns()
        if not force and current_mtime == self._config_mtime_ns:
            return False

        try:
            with self.config_path.open("r", encoding="utf-8") as stream:
                config = json.load(stream)
            section = config.get("post_compensation") or {}
            enabled = _as_enabled(section.get("enabled", False))
            min_do2 = float(section.get("min_do2", DEFAULT_MIN_DO2))
            if not math.isfinite(min_do2) or min_do2 < 0.0:
                raise ValueError("post_compensation.min_do2 must be finite and non-negative")

            configured_gain = section.get("gain") or {}
            configured_bias = section.get("bias") or {}
            gain = {}
            bias = {}
            for channel in VALID_CAGE_CHANNELS:
                channel_gain = float(configured_gain.get(channel, DEFAULT_DO2_GAIN[channel]))
                channel_bias = float(configured_bias.get(channel, DEFAULT_DO2_BIAS[channel]))
                if not math.isfinite(channel_gain) or channel_gain < 0.0:
                    raise ValueError(f"post_compensation.gain.{channel} is invalid")
                if not math.isfinite(channel_bias):
                    raise ValueError(f"post_compensation.bias.{channel} is invalid")
                gain[channel] = channel_gain
                bias[channel] = channel_bias
        except Exception as exc:
            self._config_mtime_ns = current_mtime
            logger.warning(
                f"O2二次补偿配置读取失败，继续使用上一次有效配置："
                f"path={self.config_path}, error={exc}"
            )
            return False

        with self._lock:
            self.enabled = enabled
            self.min_do2 = min_do2
            self.gain = gain
            self.bias = bias
            self._config_mtime_ns = current_mtime
        logger.info(
            f"O2二次补偿配置已加载：enabled={enabled}, min_do2={min_do2:.4f}"
        )
        return True

    def get_settings(self, channel=None):
        self.ensure_latest()
        normalized = _normalize_channel(channel) if channel is not None else None
        with self._lock:
            settings = {
                "enabled": self.enabled,
                "min_do2": self.min_do2,
            }
            if normalized is not None:
                settings.update({
                    "channel": normalized,
                    "gain": self.gain[normalized],
                    "bias": self.bias[normalized],
                })
            return settings

    def compensate(self, channel, o2_dry, ref_o2):
        """Return a post-compensated value, or the original value when unsafe.

        A non-positive ``REF - cage`` difference is preserved.  Applying the
        minimum-DO2 floor in that case would manufacture oxygen consumption
        from sensor noise or an inverted reading.
        """
        self.ensure_latest()
        original_value = o2_dry
        dry_value = _finite_percent(o2_dry)
        reference_value = _finite_percent(ref_o2)
        normalized = _normalize_channel(channel)
        if dry_value is None or reference_value is None:
            return original_value

        with self._lock:
            if not self.enabled or normalized is None:
                return dry_value
            gain = self.gain[normalized]
            bias = self.bias[normalized]
            min_do2 = self.min_do2

        do2_raw = reference_value - dry_value
        if do2_raw <= 0.0:
            return dry_value

        do2_calibrated = gain * do2_raw + bias
        if not math.isfinite(do2_calibrated):
            return dry_value
        do2_calibrated = max(do2_calibrated, min_do2)
        result = reference_value - do2_calibrated
        if not 0.0 <= result <= 100.0:
            return dry_value
        return float(result)


def get_o2_post_compensator():
    global _POST_COMPENSATOR
    with _POST_COMPENSATOR_LOCK:
        if _POST_COMPENSATOR is None:
            _POST_COMPENSATOR = O2PostCompensator(get_default_config_path())
        return _POST_COMPENSATOR


def reload_o2_post_compensation_config():
    return get_o2_post_compensator().reload_config(force=True)


def post_compensate_o2(channel, o2_dry, ref_o2):
    return get_o2_post_compensator().compensate(channel, o2_dry, ref_o2)


def get_o2_post_compensation_settings(channel=None):
    return get_o2_post_compensator().get_settings(channel)


__all__ = [
    "DEFAULT_DO2_BIAS",
    "DEFAULT_DO2_GAIN",
    "DEFAULT_MIN_DO2",
    "O2PostCompensator",
    "VALID_CAGE_CHANNELS",
    "get_o2_post_compensation_settings",
    "get_o2_post_compensator",
    "post_compensate_o2",
    "reload_o2_post_compensation_config",
]
