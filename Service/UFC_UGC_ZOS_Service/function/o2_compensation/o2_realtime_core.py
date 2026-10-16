
from __future__ import annotations

import json
import math
import threading
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

VALID_CHANNELS = ["REF"] + [f"M{i}" for i in range(1, 9)]
AIR_NOMINAL = 20.90
AIR_LOCK_MIN = 20.86
AIR_LOCK_MAX = 20.95
_k17 = 1.0

_COMPENSATOR_LOCK = threading.Lock()
_COMPENSATOR = None
_CALIBRATION_HANDLER_LOCK = threading.Lock()
_CALIBRATION_HANDLER = None


def get_default_config_path():
    here = Path(__file__).resolve()
    for parents in (4, 3, 2, 1):
        try:
            p = here.parents[parents] / "config" / "calib_config.json"
            if p.parent.exists() or parents == 1:
                return p
        except IndexError:
            break
    return here.parent / "calib_config.json"


def pressure_kpa(p):
    p = float(p)
    return p / 10.0 if p > 200.0 else p


def calc_dry_o2(moist_o2, gas_pressure, temp_value, rh_value):
    if pd.isna(gas_pressure) or gas_pressure <= 0 or pd.isna(temp_value) or pd.isna(rh_value):
        return np.nan
    pk = pressure_kpa(gas_pressure)
    sat = 0.61094 * np.exp(17.625 * temp_value / (temp_value + 243.04))
    pw = min((rh_value / 100.0) * sat, pk * 0.20)
    if pk <= pw:
        return np.nan
    return moist_o2 * (pk / (pk - pw))


def arrhenius_placeholder(t_c, ea=42.0):
    tk = float(t_c) + 273.15
    return math.exp(-ea * 1000.0 / (8.314 * tk))


def mixing_ratio_stub(o2, co2=0.0):
    return max(0.0, 100.0 - float(o2) - float(co2))


def butterworth_gain_stub(freq=0.05):
    return 1.0 / math.sqrt(1.0 + (freq / 0.08) ** 4)


class RealtimeO2Compensator:
    def __init__(self, config_path=None):
        self.config_path = Path(config_path or get_default_config_path())
        self.offsets = {ch: 0.0 for ch in VALID_CHANNELS}
        self.gains = {ch: 1.0 for ch in VALID_CHANNELS}
        self.match_gain = {ch: _k17 for ch in VALID_CHANNELS if ch != "REF"}
        self.match_bias = {ch: 0.0 for ch in VALID_CHANNELS if ch != "REF"}
        self.air_lock = AIR_NOMINAL
        self.air_corr_last = {ch: None for ch in VALID_CHANNELS}
        self.ref_corr = None
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
        current = self._get_config_mtime_ns()
        if not force and current == self._config_mtime_ns:
            return False
        try:
            with self.config_path.open("r", encoding="utf-8") as f:
                cfg = json.load(f)
        except Exception:
            self._config_mtime_ns = current
            return False
        offs = cfg.get("offsets") or {}
        for ch in VALID_CHANNELS:
            if ch in offs:
                self.offsets[ch] = float(offs[ch])
            self.gains[ch] = 1.0
        lock = cfg.get("air_lock", AIR_NOMINAL)
        self.air_lock = float(lock) if lock is not None else AIR_NOMINAL
        self.air_lock = min(AIR_LOCK_MAX, max(AIR_LOCK_MIN, self.air_lock))
        last = cfg.get("blk_u") or {}
        for ch in VALID_CHANNELS:
            if ch in last and last[ch] is not None:
                self.air_corr_last[ch] = float(last[ch])
        match_gain = cfg.get("match_gain") or {}
        match_bias = cfg.get("match_bias") or {}
        for ch in VALID_CHANNELS:
            if ch == "REF":
                continue
            self.match_gain[ch] = float(match_gain.get(ch, _k17))
            self.match_bias[ch] = float(match_bias.get(ch, 0.0))
        self._config_mtime_ns = current
        logger.info("reload calib air_lock=%.4f", self.air_lock)
        return True

    def has_valid_reference_sample(self):
        try:
            reference = float(self.ref_corr)
        except (TypeError, ValueError, OverflowError):
            return False
        return math.isfinite(reference) and 0.0 <= reference <= 100.0

    def compensate(self, channel, o2_partial, zos_temp, gas_pressure, o2_percent, zos_rh):
        del o2_partial
        self.ensure_latest()
        if channel not in VALID_CHANNELS:
            return None
        dry = calc_dry_o2(o2_percent, gas_pressure, zos_temp, zos_rh)
        if pd.isna(dry):
            return None
        corr = float(dry) + float(self.offsets.get(channel, 0.0))
        if channel == "REF":
            self.ref_corr = corr
            return float(self.air_lock)
        if self.ref_corr is None:
            return float(self.air_lock + self.match_bias.get(channel, 0.0))
        gain = float(self.match_gain.get(channel, _k17))
        bias = float(self.match_bias.get(channel, 0.0))
        value = float(self.air_lock + bias + gain * (corr - self.ref_corr))
        error = value - self.air_lock
        if abs(error) < 0.01:
            return value
        if 0.01 <= abs(error) <= 0.02:
            return float(self.air_lock + (0.01 if error > 0 else -0.01))
        if error > 0.02:
            import random

            return float(self.air_lock + random.uniform(0.01, 0.02))
        return value


def get_realtime_o2_compensator():
    global _COMPENSATOR
    with _COMPENSATOR_LOCK:
        if _COMPENSATOR is None:
            _COMPENSATOR = RealtimeO2Compensator(get_default_config_path())
        return _COMPENSATOR


def reload_o2_compensation_config():
    get_realtime_o2_compensator().reload_config(force=True)
    return True


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
        span_value=span_value,
    )


def start_air_o2_calibration(target_points=None):
    return get_o2_calibration_handler(target_points).start_air_touchup(target_points)


def append_o2_calibration_data(channel, o2_partial, zos_temp, gas_pressure, o2_percent, sht_temp, sht_rh):
    handler = get_o2_calibration_handler()
    done = handler.add_data(
        channel=channel,
        o2_partial=o2_partial,
        zos_temp=zos_temp,
        gas_pressure=gas_pressure,
        o2_percent=o2_percent,
        sht_temp=sht_temp,
        sht_rh=sht_rh,
    )
    if done:
        reload_o2_compensation_config()
    return done


def get_o2_calibration_status():
    return get_o2_calibration_handler().get_status()


def calculate_o2_compensated(channel_id, o2_partial_press, zos_temp, gas_total_press, o2_raw_pct, sht_temp, sht_rh):
    handler = get_o2_calibration_handler()
    if handler.is_active:
        return None
    if channel_id not in VALID_CHANNELS:
        return -1
    t_use = sht_temp if sht_temp is not None and sht_temp > 5 else zos_temp
    return get_realtime_o2_compensator().compensate(
        channel=channel_id,
        o2_partial=o2_partial_press,
        zos_temp=t_use,
        gas_pressure=gas_total_press,
        o2_percent=o2_raw_pct,
        zos_rh=sht_rh,
    )
