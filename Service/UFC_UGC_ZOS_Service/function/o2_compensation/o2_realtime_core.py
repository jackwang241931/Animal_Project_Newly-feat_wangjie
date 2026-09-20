import json
import threading
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger
from scipy.signal import savgol_filter

VALID_CHANNELS = ["REF"] + [f"M{i}" for i in range(1, 9)]
CALIBRATION_TEMPERATURE_SOURCE = "ZOS_temperature_2"
CALIBRATION_HUMIDITY_SOURCE = "ZOS_humidity"
_COMPENSATOR_LOCK = threading.Lock()
_COMPENSATOR = None
_CALIBRATION_HANDLER_LOCK = threading.Lock()
_CALIBRATION_HANDLER = None


def get_default_config_path():
    return Path(__file__).resolve().parents[4] / "config" / "calib_config.json"


def sequential_jump_clean(series, threshold):
    cleaned = series.copy().astype(float)
    for index in range(1, len(cleaned)):
        if abs(cleaned.iloc[index] - cleaned.iloc[index - 1]) > threshold:
            cleaned.iloc[index] = cleaned.iloc[index - 1]
    return cleaned


def calc_dry_o2(moist_o2, gas_pressure, temp_value, rh_value):
    if pd.isna(gas_pressure) or gas_pressure <= 0 or pd.isna(temp_value) or pd.isna(rh_value):
        return np.nan
    sat_pressure = 0.61094 * np.exp(17.625 * temp_value / (temp_value + 243.04))
    water_pressure = min((rh_value / 100.0) * sat_pressure, gas_pressure * 0.99)
    return moist_o2 * (gas_pressure / (gas_pressure - water_pressure))


def _coerce_float(value):
    try:
        if value is None or pd.isna(value):
            return np.nan
        return float(value)
    except Exception:
        return np.nan


class RealtimeO2Compensator:
    def __init__(self, config_path=None):
        self.config_path = Path(config_path or get_default_config_path())
        self.offsets = {}
        self.gains = {}
        self.secondary_models = {}
        self.target_o2 = 20.93
        self.dry_buffer = defaultdict(list)
        self.rh_buffer = defaultdict(list)
        self.ref_rh_buffer = []
        self.dry_ref_buffer = []
        self.last_values = {}
        self._config_mtime_ns = None
        self.reload_config(force=True)

    def _get_config_mtime_ns(self):
        try:
            return self.config_path.stat().st_mtime_ns
        except OSError:
            return None

    def ensure_latest(self):
        self.reload_config(force=False)

    def reload_config(self, force=False):
        current_mtime_ns = self._get_config_mtime_ns()
        if not force and current_mtime_ns == self._config_mtime_ns:
            return True

        self._config_mtime_ns = current_mtime_ns
        config = self._load_config()
        source_is_valid = (
            config.get("temperature_source") == CALIBRATION_TEMPERATURE_SOURCE
            and config.get("humidity_source") == CALIBRATION_HUMIDITY_SOURCE
        )
        if source_is_valid:
            self.offsets = config.get("offsets", {})
            self.gains = config.get("gains", {})
            self.secondary_models = config.get("secondary_models", {})
        else:
            self.offsets = {}
            self.gains = {}
            self.secondary_models = {}
            if config.get("offsets") or config.get("gains"):
                logger.warning(
                    "O2 calibration config ignored because it was not generated "
                    "with ZOS temperature 2 and ZOS humidity; run Air calibration again"
                )
        self.target_o2 = float(config.get("target_o2", 20.93))
        return True

    def _load_config(self):
        try:
            with self.config_path.open("r", encoding="utf-8") as file:
                return json.load(file)
        except Exception:
            return {"offsets": {}, "gains": {}, "secondary_models": {}, "target_o2": 20.93}

    def compensate(self, channel, o2_partial, zos_temp, gas_pressure, o2_percent, zos_rh):
        del o2_partial

        self.ensure_latest()

        o2_value = _coerce_float(o2_percent)
        gas_pressure_value = _coerce_float(gas_pressure)
        zos_temp_value = _coerce_float(zos_temp)
        rh_value = _coerce_float(zos_rh)

        if channel not in self.last_values:
            self.last_values[channel] = {
                "o2": o2_value,
                "p": gas_pressure_value,
                "t": zos_temp_value,
                "rh": rh_value,
            }

        last = self.last_values[channel]
        if not pd.isna(o2_value) and not pd.isna(last["o2"]) and abs(o2_value - last["o2"]) > 5.0:
            o2_value = last["o2"]
        if (
            not pd.isna(gas_pressure_value)
            and not pd.isna(last["p"])
            and abs(gas_pressure_value - last["p"]) > 5.0
        ):
            gas_pressure_value = last["p"]
        if not pd.isna(zos_temp_value) and not pd.isna(last["t"]) and abs(zos_temp_value - last["t"]) > 5.0:
            zos_temp_value = last["t"]
        if not pd.isna(rh_value) and not pd.isna(last["rh"]) and abs(rh_value - last["rh"]) > 10.0:
            rh_value = last["rh"]

        self.last_values[channel] = {
            "o2": o2_value,
            "p": gas_pressure_value,
            "t": zos_temp_value,
            "rh": rh_value,
        }

        dry_raw = calc_dry_o2(o2_value, gas_pressure_value, zos_temp_value, rh_value)

        self.dry_buffer[channel].append(dry_raw)
        self.rh_buffer[channel].append(rh_value)
        if channel == "REF":
            self.ref_rh_buffer.append(rh_value)
            self.dry_ref_buffer.append(dry_raw)

        for buffer_item in (self.dry_buffer[channel], self.rh_buffer[channel]):
            if len(buffer_item) > 50:
                buffer_item.pop(0)
        if len(self.ref_rh_buffer) > 50:
            self.ref_rh_buffer.pop(0)
            self.dry_ref_buffer.pop(0)

        if len(self.dry_buffer[channel]) >= 11:
            dry_sg = savgol_filter(self.dry_buffer[channel][-11:], window_length=11, polyorder=2)[-1]
        else:
            dry_sg = dry_raw

        dry_sec = self._apply_secondary(dry_sg, rh_value, zos_temp_value, channel)

        offset = float(self.offsets.get(channel, 0.0))
        gain_ch = float(self.gains.get(channel, 1.0))
        gain_ref = float(self.gains.get("REF", 1.0))

        dry_corrected = (dry_sec + offset) * gain_ch
        ref_dry = self.dry_ref_buffer[-1] if self.dry_ref_buffer else dry_sec
        ref_corrected = (ref_dry + float(self.offsets.get("REF", 0.0))) * gain_ref

        final_value = dry_corrected - (ref_corrected - self.target_o2)

        mode = self._detect_mode_4points(channel)
        if mode == "empty":
            output = self.target_o2 
        else:
            output = final_value

        output = min(output, self.target_o2)
        return round(float(output), 3)

    def _apply_secondary(self, dry_value, rh_value, temp_value, channel):
        del rh_value, temp_value
        return dry_value

    def _detect_mode_4points(self, channel):        #空笼
        if len(self.dry_buffer[channel]) < 4:
            return "unknown"

        recent = np.array(self.dry_buffer[channel][-4:], dtype=float)
        mean_val = float(np.mean(recent))
        std_val = float(np.std(recent))

        close_to_target = abs(mean_val - self.target_o2) < 0.05
        low_fluctuation = std_val < 0.02
        all_close = np.all(np.abs(recent - self.target_o2) < 0.08)

        if close_to_target and low_fluctuation and all_close:
            return "empty"
        return "metabolic"

    def get_mode(self, channel):
        return self._detect_mode_4points(channel)


def get_realtime_o2_compensator():
    global _COMPENSATOR
    with _COMPENSATOR_LOCK:
        if _COMPENSATOR is None:
            _COMPENSATOR = RealtimeO2Compensator()
        else:
            _COMPENSATOR.ensure_latest()
        return _COMPENSATOR


def reload_o2_compensation_config():
    compensator = get_realtime_o2_compensator()
    compensator.reload_config(force=True)
    return True


def get_o2_calibration_handler(target_points=None):
    global _CALIBRATION_HANDLER
    with _CALIBRATION_HANDLER_LOCK:
        if target_points is not None:
            target_points = int(target_points)

        if (
            _CALIBRATION_HANDLER is None
            or (target_points is not None and _CALIBRATION_HANDLER.target_points != target_points)
        ):
            from .calibration_handler import CalibrationHandler

            _CALIBRATION_HANDLER = CalibrationHandler(
                target_points=target_points or 120,
                config_path=get_default_config_path(),
            )
        return _CALIBRATION_HANDLER


def start_new_o2_calibration(target_points=60):
    handler = get_o2_calibration_handler(target_points=target_points)
    return handler.start_new_calibration()


def append_o2_calibration_data(channel, o2_partial, zos_temp, gas_pressure, o2_percent, zos_rh):
    handler = get_o2_calibration_handler()
    success = handler.add_data(
        channel=channel,
        o2_partial=o2_partial,
        zos_temp=zos_temp,
        gas_pressure=gas_pressure,
        o2_percent=o2_percent,
        zos_rh=zos_rh,
    )
    if success:
        reload_o2_compensation_config()
    return success


def get_o2_calibration_status():
    handler = get_o2_calibration_handler()
    return handler.get_status()


def calculate_o2_compensated(
        channel_id,
        o2_partial_press,
        zos_temp,
        gas_total_press,
        o2_raw_pct,
        zos_rh
):
    if channel_id not in VALID_CHANNELS:
        return -1

    compensator = get_realtime_o2_compensator()
    return compensator.compensate(
        channel=channel_id,
        o2_partial=o2_partial_press,
        zos_temp=zos_temp,
        gas_pressure=gas_total_press,
        o2_percent=o2_raw_pct,
        zos_rh=zos_rh,
    )
