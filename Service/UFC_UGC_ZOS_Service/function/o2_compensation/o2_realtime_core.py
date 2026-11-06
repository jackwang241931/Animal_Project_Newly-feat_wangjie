from __future__ import annotations

import json
import math
import statistics
import threading
import time
from collections import deque
from pathlib import Path

from loguru import logger


VALID_CHANNELS = ["REF"] + [f"M{i}" for i in range(1, 9)]
AIR_NOMINAL = 20.93

_COMPENSATOR_LOCK = threading.Lock()
_COMPENSATOR = None
_CALIBRATION_HANDLER_LOCK = threading.Lock()
_CALIBRATION_HANDLER = None

# Supplier model: convert Delta CO2 (percentage points) into an estimated Delta O2.
_MODEL_SLOPE = 1.042868
_MODEL_INTERCEPT = 0.008752
_MODEL_WEIGHT = 0.60
_DCO2_MAX = 0.40


def get_default_config_path() -> Path:
    """Return the project-level v5 oxygen calibration configuration."""
    return Path(__file__).resolve().parents[4] / "config" / "calib_config.json"


def pressure_kpa(value):
    pressure = float(value)
    if not math.isfinite(pressure) or pressure <= 0:
        raise ValueError("invalid pressure")
    pressure = pressure / 10.0 if pressure > 200.0 else pressure
    if not 50.0 <= pressure <= 120.0:
        raise ValueError("pressure expected kPa or hPa")
    return pressure


def calc_dry_o2(moist_o2, gas_pressure, temp_value, rh_value):
    try:
        wet_o2 = float(moist_o2)
        temperature = float(temp_value)
        relative_humidity = float(rh_value)
        pressure = pressure_kpa(gas_pressure)
        if not all(
            math.isfinite(value)
            for value in (wet_o2, temperature, relative_humidity)
        ):
            return float("nan")
        if not (
            0.0 <= wet_o2 <= 25.0
            and -20.0 <= temperature <= 75.0
            and 0.0 <= relative_humidity <= 100.0
        ):
            return float("nan")
        saturated_pressure = 0.61094 * math.exp(
            17.625 * temperature / (temperature + 243.04)
        )
        water_pressure = relative_humidity / 100.0 * saturated_pressure
        if water_pressure >= pressure:
            return float("nan")
        return wet_o2 * pressure / (pressure - water_pressure)
    except (ValueError, TypeError, ZeroDivisionError, OverflowError):
        return float("nan")


def _model_do2(delta_co2_pct):
    """Supplier V5 direct Delta-CO2 to Delta-O2 model."""
    return _MODEL_SLOPE * float(delta_co2_pct) + _MODEL_INTERCEPT


class _ReferenceFilter:
    def __init__(self):
        self.recent = deque(maxlen=5)
        self.filtered = None
        self.last_time = None
        self.last_input = None
        self.last_rejected = False

    def update(self, value, now=None):
        now = time.monotonic() if now is None else float(now)
        value = float(value)
        self.last_input = value
        if not math.isfinite(value):
            return self.filtered

        history = list(self.recent)
        if len(history) >= 3:
            median = statistics.median(history)
            mad = statistics.median(abs(item - median) for item in history)
            jump_limit = max(0.025, 6 * 1.4826 * mad)
            if abs(value - median) > jump_limit and not self.last_rejected:
                self.last_rejected = True
                return self.filtered

        self.last_rejected = False
        self.recent.append(value)
        median = statistics.median(self.recent)
        if self.filtered is None:
            self.filtered = median
        else:
            elapsed = (
                1.0
                if self.last_time is None
                else max(0.01, min(300.0, now - self.last_time))
            )
            alpha = 1.0 - math.exp(-elapsed / 30.0)
            self.filtered += alpha * (median - self.filtered)
        self.last_time = now
        return self.filtered


class RealtimeO2Compensator:
    def __init__(self, config_path=None):
        self.config_path = Path(config_path or get_default_config_path())
        self.lock = threading.RLock()
        self.cfg = None
        self._config_mtime_ns = None
        self._reference_filter = _ReferenceFilter()
        self.last_observations = {}
        self.reload_config(force=True)

    @property
    def air_lock(self):
        """Compatibility value used by the existing host UI and logs."""
        with self.lock:
            if self.cfg is None:
                return AIR_NOMINAL
            return float(self.cfg.get("air_o2_pct", AIR_NOMINAL))

    def has_valid_reference_sample(self):
        with self.lock:
            reference = self._reference_filter.filtered
            return reference is not None and math.isfinite(float(reference))

    def reload_config(self, force=False):
        with self.lock:
            try:
                config_mtime_ns = self.config_path.stat().st_mtime_ns
                if not force and config_mtime_ns == self._config_mtime_ns:
                    return False

                with self.config_path.open(encoding="utf-8") as stream:
                    config = json.load(stream)
                if str(config.get("version")) != "5.0":
                    raise ValueError("requires v5 config")
                if set(config["channels"]) != set(VALID_CHANNELS):
                    raise ValueError("requires nine channels")

                air_o2 = float(config["air_o2_pct"])
                fixed_air = float(config.get("air_fixed_pct", AIR_NOMINAL))
                if abs(air_o2 - AIR_NOMINAL) > 1e-9 or abs(fixed_air - AIR_NOMINAL) > 1e-9:
                    raise ValueError("v5 air reference must be fixed at 20.93%")
                span_o2 = float(config["span_o2_pct"])
                if not 15.0 <= span_o2 < air_o2:
                    raise ValueError("invalid Span gas value")

                for channel in VALID_CHANNELS:
                    channel_config = config["channels"][channel]
                    gain = float(channel_config["gain"])
                    air_dry = float(channel_config["air_dry_pct"])
                    if not (0.5 <= gain <= 1.5 and 10.0 <= air_dry <= 25.0):
                        raise ValueError(f"bad calibration {channel}")

                self.cfg = config
                self._config_mtime_ns = config_mtime_ns
                self._reference_filter = _ReferenceFilter()
                logger.info(
                    "O2 v5 config reloaded: path={}, fixed_air_o2_pct={:.4f}",
                    self.config_path,
                    air_o2,
                )
                return True
            except Exception as exc:
                logger.error(
                    "O2 v5 config reload refused: path={}, error={}",
                    self.config_path,
                    exc,
                )
                return False

    def ensure_latest(self):
        self.reload_config(force=False)

    def compensate(
        self,
        channel,
        o2_partial,
        delta_co2_pct,
        gas_pressure,
        o2_percent,
        relative_humidity,
        *,
        sht_temp,
    ):
        del o2_partial
        with self.lock:
            self.ensure_latest()
            if channel not in VALID_CHANNELS or self.cfg is None:
                return None
            dry_o2 = calc_dry_o2(
                o2_percent,
                gas_pressure,
                sht_temp,
                relative_humidity,
            )
            if not math.isfinite(dry_o2):
                return None

            air_o2 = float(self.cfg["air_o2_pct"])
            channel_config = self.cfg["channels"][channel]
            independent_o2 = air_o2 + float(channel_config["gain"]) * (
                dry_o2 - float(channel_config["air_dry_pct"])
            )
            if not 15.0 <= independent_o2 <= 22.0:
                return None

            diagnostics = {
                "raw_dry_pct": dry_o2,
                "independent_o2_pct": independent_o2,
                "air_locked_pct": air_o2,
            }
            if channel == "REF":
                filtered_reference = self._reference_filter.update(independent_o2)
                diagnostics.update(
                    ref_filtered_pct=filtered_reference,
                    ref_drift_pct=filtered_reference - air_o2,
                    dco2_pct=0.0,
                )
                self.last_observations[channel] = diagnostics
                return air_o2

            filtered_reference = self._reference_filter.filtered
            reference_drift = (
                filtered_reference - air_o2
                if filtered_reference is not None
                else 0.0
            )
            # V5 keeps the measured channel physically independent.  REF is
            # still observed for health diagnostics, but is not subtracted as
            # an unvalidated common-mode correction.
            measured_o2 = independent_o2
            measured_do2 = air_o2 - measured_o2
            diagnostics.update(
                ref_filtered_pct=filtered_reference,
                ref_drift_pct=reference_drift,
                physical_o2_pct=measured_o2,
                do2_measured_pct=measured_do2,
            )

            try:
                delta_co2 = float(delta_co2_pct)
            except (TypeError, ValueError, OverflowError):
                delta_co2 = float("nan")

            if not math.isfinite(delta_co2) or not -0.005 <= delta_co2 <= _DCO2_MAX:
                diagnostics["co2_quality"] = "INVALID_FALLBACK"
                output_o2 = measured_o2
            else:
                delta_co2 = max(0.0, delta_co2)
                predicted_do2 = _model_do2(delta_co2)
                fused_do2 = (
                    (1.0 - _MODEL_WEIGHT) * measured_do2
                    + _MODEL_WEIGHT * predicted_do2
                )
                output_o2 = air_o2 - fused_do2
                diagnostics.update(
                    dco2_pct=delta_co2,
                    co2_predicted_do2_pct=predicted_do2,
                    fused_do2_pct=fused_do2,
                    co2_quality="OK_MODEL_ESTIMATE",
                )

            diagnostics["output_o2_pct"] = output_o2
            self.last_observations[channel] = diagnostics
            return float(output_o2) if 15.0 <= output_o2 <= 22.0 else None


def get_realtime_o2_compensator():
    global _COMPENSATOR
    with _COMPENSATOR_LOCK:
        if _COMPENSATOR is None:
            _COMPENSATOR = RealtimeO2Compensator(get_default_config_path())
        return _COMPENSATOR


def reload_o2_compensation_config():
    return get_realtime_o2_compensator().reload_config(force=True)


def get_o2_calibration_handler(target_points=None):
    global _CALIBRATION_HANDLER
    with _CALIBRATION_HANDLER_LOCK:
        if _CALIBRATION_HANDLER is None:
            try:
                from .calibration_handler import CalibrationHandler
            except ImportError:
                from calibration_handler import CalibrationHandler
            _CALIBRATION_HANDLER = CalibrationHandler(
                target_points=target_points,
                config_path=get_default_config_path(),
            )
        elif target_points is not None:
            _CALIBRATION_HANDLER.set_target_points(target_points)
        return _CALIBRATION_HANDLER


def start_new_o2_calibration(target_points=None, span_value=None):
    return get_o2_calibration_handler(target_points).start_new_calibration(
        target_points,
        span_value,
    )


def start_span_o2_calibration(target_points=None, span_value=None):
    return get_o2_calibration_handler(target_points).start_span_o2(
        target_points,
        span_value,
    )


def start_air_o2_calibration(target_points=None):
    return get_o2_calibration_handler(target_points).start_air_touchup(target_points)


def append_o2_calibration_data(
    channel,
    o2_partial,
    zos_temp,
    gas_pressure,
    o2_percent,
    sht_temp,
    sht_rh,
):
    completed = get_o2_calibration_handler().add_data(
        channel=channel,
        o2_partial=o2_partial,
        zos_temp=zos_temp,
        gas_pressure=gas_pressure,
        o2_percent=o2_percent,
        zos_rh=sht_rh,
        sht_temp=sht_temp,
        sht_rh=sht_rh,
    )
    if completed:
        reload_o2_compensation_config()
    return completed


def get_o2_calibration_status():
    return get_o2_calibration_handler().get_status()


def calculate_o2_compensated(
    channel_id,
    o2_partial_press,
    delta_co2_pct,
    gas_total_press,
    o2_raw_pct,
    sht_temp,
    sht_rh,
):
    handler = get_o2_calibration_handler()
    if handler.is_active:
        return None
    if channel_id not in VALID_CHANNELS:
        return -1
    if sht_temp is None or sht_rh is None:
        return None
    return get_realtime_o2_compensator().compensate(
        channel_id,
        o2_partial_press,
        0.0 if channel_id == "REF" else delta_co2_pct,
        gas_total_press,
        o2_raw_pct,
        sht_rh,
        sht_temp=sht_temp,
    )
