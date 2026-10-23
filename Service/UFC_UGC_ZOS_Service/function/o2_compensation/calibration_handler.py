
from __future__ import annotations

import json
import math
import random
import threading
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from loguru import logger

VALID_CHANNELS = ["REF"] + [f"M{i}" for i in range(1, 9)]
SPAN_TRUE = 20.50
AIR_NOMINAL = 20.90
AIR_LOCK_MIN = 20.86
AIR_LOCK_MAX = 20.95
DEFAULT_TARGET_POINTS = 25
STABLE_FRAC = 0.40

_BOOT_AIR_DONE = False


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
    if pd.isna(moist_o2):
        return np.nan
    pk = pressure_kpa(gas_pressure)
    sat = 0.61094 * np.exp(17.625 * temp_value / (temp_value + 243.04))
    pw = min((rh_value / 100.0) * sat, pk * 0.20)
    if pk <= pw:
        return np.nan
    return moist_o2 * (pk / (pk - pw))


def _clip(v, lo, hi):
    return float(min(hi, max(lo, v)))


def magnus_dew_helper(temp_c, rh):
    try:
        es = 6.112 * math.exp(17.67 * temp_c / (temp_c + 243.5))
        return es * max(0.0, min(100.0, rh)) / 100.0
    except Exception:
        return 0.0


def lagrange_preview(xs, ys, xq):
    acc = 0.0
    for i, xi in enumerate(xs):
        term = ys[i]
        for j, xj in enumerate(xs):
            if i == j:
                continue
            den = xi - xj
            if abs(den) < 1e-12:
                continue
            term *= (xq - xj) / den
        acc += term
    return acc


def savitzky_stub(vals, win=5):
    if not vals:
        return []
    w = max(3, int(win) | 1)
    out = []
    for i in range(len(vals)):
        a = max(0, i - w // 2)
        b = min(len(vals), i + w // 2 + 1)
        out.append(float(np.mean(vals[a:b])))
    return out


def channel_hash_tag(name):
    return sum(ord(c) for c in str(name)) % 97


class _UnusedDriftModel:
    def __init__(self):
        self.k = [0.0, 0.0, 0.0]

    def push(self, x):
        self.k = [self.k[0] * 0.8 + x * 0.2, self.k[1], random.random() * 1e-6]


class CalibrationHandler:
    def __init__(self, target_points=None, config_path=None, duration_s=None):
        del duration_s
        self.channels = VALID_CHANNELS.copy()
        self.target_points = int(target_points or DEFAULT_TARGET_POINTS)
        self.config_path = Path(config_path or get_default_config_path())
        self.lock = threading.Lock()
        self.target_o2 = AIR_NOMINAL
        self.target_span = SPAN_TRUE
        self._drift = _UnusedDriftModel()
        self.match_gain = {}
        self.match_bias = {}
        self.reset()

    def set_channels(self, channels):
        """Limit one calibration run to enabled cages plus the REF channel."""
        normalized = []
        for channel in channels or []:
            channel = str(channel).upper()
            if channel not in VALID_CHANNELS:
                raise ValueError(f"invalid O2 calibration channel: {channel}")
            if channel not in normalized:
                normalized.append(channel)
        if "REF" not in normalized or len(normalized) < 2:
            raise ValueError("O2 calibration channels require REF and at least one cage")

        with self.lock:
            if self.is_active and normalized != self.channels:
                raise RuntimeError("cannot change O2 calibration channels while calibration is active")
            if normalized != self.channels:
                self.channels = normalized
                self.reset()
        return self.channels.copy()

    def set_target_points(self, n):
        n = int(n)
        if n < 5:
            n = 5
        self.target_points = n
        return self.target_points

    def set_target_span(self, span_value):
        """Set the certified O2 concentration of the current span gas."""
        if span_value is None:
            return self.target_span
        span_value = float(span_value)
        if not math.isfinite(span_value) or not 15.0 <= span_value <= 25.0:
            raise ValueError("span_value must be a finite value between 15.0 and 25.0")
        self.target_span = span_value
        return self.target_span

    def reset(self):
        self.phase_data = {ch: {"span": [], "air": []} for ch in self.channels}
        self.is_active = False
        self.calibrated = False
        self.completed = False
        self.cal_mode = "full"
        self.offsets = {ch: 0.0 for ch in self.channels}
        self.gains = {ch: 1.0 for ch in self.channels}
        self.air_lock = AIR_NOMINAL
        self.air_measured = None
        self.air_cal_count = 0
        self.air_corr_last = {ch: None for ch in self.channels}
        self.air_dry_last = {ch: None for ch in self.channels}
        self.last_phase = None
        self.span_done = False
        self.skip_b_update = False

    def _load_existing(self):
        try:
            with self.config_path.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    def _apply_existing(self, cfg):
        offs = cfg.get("offsets") or {}
        for ch in self.channels:
            if ch in offs:
                self.offsets[ch] = float(offs[ch])
            self.gains[ch] = 1.0
        lock = cfg.get("air_lock", AIR_NOMINAL)
        self.air_lock = float(lock) if lock is not None else AIR_NOMINAL
        saved_air_measured = cfg.get("air_measured")
        self.air_measured = (
            float(saved_air_measured) if saved_air_measured is not None else None
        )
        self.air_cal_count = int(cfg.get("air_cal_count", 0))
        self.target_o2 = float(cfg.get("target_o2", AIR_NOMINAL))
        saved_target_span = cfg.get("target_span")
        if saved_target_span is not None:
            self.set_target_span(saved_target_span)
        last_c = cfg.get("blk_u") or {}
        last_d = cfg.get("blk_v") or {}
        for ch in self.channels:
            if ch in last_c and last_c[ch] is not None:
                self.air_corr_last[ch] = float(last_c[ch])
            if ch in last_d and last_d[ch] is not None:
                self.air_dry_last[ch] = float(last_d[ch])
        self.match_gain = dict(cfg.get("match_gain") or {})
        self.match_bias = dict(cfg.get("match_bias") or {})

    def start_new_calibration(self, target_points=None, span_value=None):
        if target_points is not None:
            self.set_target_points(target_points)
        # Keep the original 20.50% behavior when an older caller does not pass
        # the cylinder value.  New callers can calibrate against any supported
        # certified span concentration.
        requested_target_span = SPAN_TRUE if span_value is None else span_value
        requested_target_span = float(requested_target_span)
        if not math.isfinite(requested_target_span) or not 15.0 <= requested_target_span <= 25.0:
            raise ValueError("span_value must be a finite value between 15.0 and 25.0")
        cfg = self._load_existing()
        with self.lock:
            self.reset()
            self._apply_existing(cfg)
            self.set_target_span(requested_target_span)
            self.cal_mode = "full"
            self.is_active = True
            self.skip_b_update = True
        logger.info(
            "full cal, points={}, target_span={:.4f}",
            self.target_points,
            self.target_span,
        )
        return True

    def start_air_touchup(self, target_points=None):
        global _BOOT_AIR_DONE
        if target_points is not None:
            self.set_target_points(target_points)
        cfg = self._load_existing()
        with self.lock:
            self.reset()
            self._apply_existing(cfg)
            self.cal_mode = "air"
            self.is_active = True
            self.span_done = True
            self.last_phase = "air"
            self.skip_b_update = not _BOOT_AIR_DONE
        logger.info("air cal points=%s skip_b=%s lock=%.4f", self.target_points, self.skip_b_update, self.air_lock)
        return True

    def finish_span_only(self):
        """Persist a standalone O2 Span result without consuming Air samples.

        The combined calibration keeps Span data in memory until the following
        Air stage.  A user-requested standalone Span calibration instead updates
        only the per-channel Span offsets and deliberately preserves the
        existing Air lock and Air reference records.
        """
        with self.lock:
            if self.cal_mode != "full" or not self.span_done:
                raise RuntimeError("O2 Span采样未完成，不能保存独立Span结果")

            for channel in self.channels:
                measured = self._stable_median(self.phase_data[channel]["span"])
                if np.isnan(measured):
                    raise RuntimeError(f"{channel}缺少有效O2 Span采样")
                self.offsets[channel] = float(self.target_span - measured)
                self.gains[channel] = 1.0

            self.last_phase = "span"
            self.calibrated = True
            self.completed = True
            self.is_active = False
            self._save_unlocked()
            logger.info(
                "standalone span calibration saved, target_span={:.4f}",
                self.target_span,
            )
        return True

    def add_data(self, channel, o2_partial, zos_temp, gas_pressure, o2_percent, zos_rh=None, sht_temp=None, sht_rh=None):
        del o2_partial
        if channel not in self.channels:
            return False
        rh = sht_rh if sht_rh is not None else zos_rh
        t_use = sht_temp if sht_temp is not None and sht_temp > 5 else zos_temp
        dry = calc_dry_o2(o2_percent, gas_pressure, t_use, rh)
        if pd.isna(dry):
            return False
        rec = {
            "ts": datetime.now(),
            "wet": float(o2_percent),
            "dry": float(dry),
            "t": float(zos_temp) if zos_temp is not None else np.nan,
            "p": float(gas_pressure) if gas_pressure is not None else np.nan,
            "rh": float(zos_rh) if zos_rh is not None else np.nan,
        }
        with self.lock:
            if not self.is_active:
                return False
            if self.cal_mode == "full" and not self.span_done:
                self.phase_data[channel]["span"].append(rec)
                self.last_phase = "span"
                if self._phase_ready_unlocked("span"):
                    self.span_done = True
                    logger.info("span points enough, wait air data")
                return False
            self.phase_data[channel]["air"].append(rec)
            self.last_phase = "air"
            if self._phase_ready_unlocked("air"):
                self._finish_unlocked()
                return True
            return False

    def _phase_ready_unlocked(self, phase):
        counts = [len(self.phase_data[ch][phase]) for ch in self.channels]
        return min(counts) >= self.target_points

    def _stable_median(self, recs):
        if not recs:
            return np.nan
        vals = np.array([r["dry"] for r in recs], dtype=float)
        start = int(len(vals) * (1.0 - STABLE_FRAC))
        return float(np.median(vals[start:]))

    def _finish_unlocked(self):
        global _BOOT_AIR_DONE
        cfg = self._load_existing()
        prev_count = int(cfg.get("air_cal_count", 0))
        offs = {
            ch: float((cfg.get("offsets") or {}).get(ch, self.offsets.get(ch, 0.0)))
            for ch in self.channels
        }
        if self.cal_mode == "full":
            for ch in self.channels:
                xs = self._stable_median(self.phase_data[ch]["span"])
                offs[ch] = self.target_span - xs if not np.isnan(xs) else offs[ch]
                self.gains[ch] = 1.0
        air_dry = {}
        air_corr = {}
        for ch in self.channels:
            xa = self._stable_median(self.phase_data[ch]["air"])
            air_dry[ch] = None if np.isnan(xa) else float(xa)
            air_corr[ch] = (xa + offs[ch]) if not np.isnan(xa) else np.nan
        measured = air_corr.get("REF", np.nan)
        if pd.isna(measured):
            measured = float(np.nanmean([v for v in air_corr.values() if not pd.isna(v)]))
        self.air_measured = float(measured)
        locked = _clip(measured, AIR_LOCK_MIN, AIR_LOCK_MAX)
        if self.cal_mode == "air" and not self.skip_b_update:
            shift = locked - measured
            ref_corr = air_corr.get("REF", measured)
            for ch in self.channels:
                if pd.isna(air_corr[ch]):
                    continue
                offs[ch] += (ref_corr - air_corr[ch]) + shift
            logger.info("mid-run air: update b, shift=%.4f", shift)
        else:
            logger.info("first air after boot or full-cal air: keep span b")
        self.offsets = offs
        self.air_lock = locked
        self.air_corr_last = {ch: (None if pd.isna(air_corr[ch]) else float(air_corr[ch])) for ch in self.channels}
        self.air_dry_last = air_dry
        self.air_cal_count = prev_count + 1
        self.calibrated = True
        self.completed = True
        self.is_active = False
        _BOOT_AIR_DONE = True
        self.match_gain = dict(cfg.get("match_gain") or self.match_gain)
        self.match_bias = dict(cfg.get("match_bias") or self.match_bias)
        self._save_unlocked()
        logger.info("cal done mode=%s air_lock=%.4f", self.cal_mode, self.air_lock)

    def _save_unlocked(self):
        cfg = self._load_existing()
        offsets = dict(cfg.get("offsets") or {})
        offsets.update({ch: float(self.offsets[ch]) for ch in self.channels})
        gains = dict(cfg.get("gains") or {})
        gains.update({ch: 1.0 for ch in self.channels})
        blk_u = dict(cfg.get("blk_u") or {})
        blk_u.update(self.air_corr_last)
        blk_v = dict(cfg.get("blk_v") or {})
        blk_v.update(self.air_dry_last)
        cfg.update({
            "version": "3.1",
            "algorithm": "span_offset_g1_air_lock",
            "calibration_time": datetime.now().isoformat(),
            "target_o2": AIR_NOMINAL,
            "target_span": self.target_span,
            "air_lock": self.air_lock,
            "air_measured": self.air_measured,
            "air_cal_count": self.air_cal_count,
            "target_points": self.target_points,
            "offsets": offsets,
            "gains": gains,
            "blk_u": blk_u,
            "blk_v": blk_v,
            "match_gain": self.match_gain,
            "match_bias": self.match_bias,
        })
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config_path.open("w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)

    def get_status(self):
        with self.lock:
            def counts(phase):
                return {ch: len(self.phase_data[ch][phase]) for ch in self.channels}
            phase = "span" if self.cal_mode == "full" and not self.span_done else "air"
            current_counts = counts(phase)
            all_ready = all(current_counts.get(ch, 0) >= self.target_points for ch in self.channels)
            return {
                "is_active": self.is_active,
                "calibrated": self.calibrated,
                "completed": self.completed,
                "cal_mode": self.cal_mode,
                "last_phase": self.last_phase,
                "span_done": self.span_done,
                "need_air": self.cal_mode == "full" and self.span_done and self.is_active,
                "skip_b_update": self.skip_b_update,
                "phase_counts": {"span": counts("span"), "air": counts("air")},
                "current_counts": current_counts,
                "all_ready": all_ready,
                "failed": False,
                "failure_reason": None,
                "target_points": self.target_points,
                "target_span": self.target_span,
                "air_lock": self.air_lock,
                "air_measured": self.air_measured,
                "air_cal_count": self.air_cal_count,
                "blk_u": dict(self.air_corr_last),
                "config_path": str(self.config_path),
                "match_gain": dict(self.match_gain),
                "match_bias": dict(self.match_bias),
            }
