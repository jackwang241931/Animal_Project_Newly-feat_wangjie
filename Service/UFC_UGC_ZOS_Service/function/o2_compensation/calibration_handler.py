from __future__ import annotations

import json
import math
import os
import statistics
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from loguru import logger


VALID_CHANNELS = ["REF"] + [f"M{i}" for i in range(1, 9)]
DEFAULT_TARGET_POINTS = 25
STABLE_FRAC = 0.40
AIR_NOMINAL = 20.93


def get_default_config_path() -> Path:
    return Path(__file__).resolve().parents[4] / "config" / "calib_config.json"


def calc_dry_o2(wet_o2, gas_pressure, temperature, relative_humidity):
    try:
        from .o2_realtime_core import calc_dry_o2 as calculate
    except ImportError:
        from o2_realtime_core import calc_dry_o2 as calculate
    return calculate(wet_o2, gas_pressure, temperature, relative_humidity)


class CalibrationHandler:
    """V5 oxygen calibration adapted to the host application's workflows.

    The supplier algorithm calibrates channels independently, locks air to
    20.93%, and pairs standalone Span/Air results within a configurable time
    window.  The adapter also preserves the host's selectable-channel and
    combined Span -> Air handoff contract.
    """

    def __init__(self, target_points=None, config_path=None, duration_s=None):
        del duration_s
        self.config_path = Path(config_path or get_default_config_path())
        self.lock = threading.RLock()
        self.channels = VALID_CHANNELS.copy()
        self.target_points = max(5, int(target_points or DEFAULT_TARGET_POINTS))
        self.reset()

    def set_channels(self, channels):
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
                raise RuntimeError(
                    "cannot change O2 calibration channels while calibration is active"
                )
            if normalized != self.channels:
                self.channels = normalized
                self.reset()
        return self.channels.copy()

    def set_target_points(self, target_points):
        self.target_points = max(5, int(target_points))
        return self.target_points

    def set_target_span(self, span_value):
        if span_value is not None:
            span_value = float(span_value)
            if not 15.0 <= span_value < AIR_NOMINAL:
                raise ValueError("invalid Span gas")
            self.target_span = span_value
        return self.target_span

    def reset(self):
        self.phase_data = {
            channel: {"span": [], "air": []}
            for channel in self.channels
        }
        self.is_active = False
        self.calibrated = False
        self.completed = False
        self.cal_mode = "full"
        self.span_done = False
        self.last_phase = None
        self.last_error = None
        self.validation_warnings = []
        self.validation_details = {}
        self.validation_status = "NOT_STARTED"
        self.failed_channels = {}
        self.passed_channels = []
        self.target_span = None
        self.air_lock = AIR_NOMINAL
        self.air_measured = None

    def _load_config(self):
        with self.config_path.open(encoding="utf-8") as stream:
            config = json.load(stream)
        if str(config.get("version")) != "5.0":
            raise ValueError("v5 config required")
        if set(config.get("channels", {})) != set(VALID_CHANNELS):
            raise ValueError("v5 config requires REF and M1-M8")
        if abs(float(config.get("air_fixed_pct", AIR_NOMINAL)) - AIR_NOMINAL) > 1e-9:
            raise ValueError("v5 calibration requires air_fixed_pct=20.93")
        pending = config.get("pending_span_pairs", {})
        if not isinstance(pending, dict):
            raise ValueError("pending_span_pairs must be a dictionary")
        return config

    def _begin(self, mode, target_points=None, span_value=None):
        config = self._load_config()
        with self.lock:
            if target_points is not None:
                self.set_target_points(target_points)
            self.reset()
            self.cal_mode = mode
            self.air_lock = AIR_NOMINAL
            self.target_span = (
                float(span_value)
                if span_value is not None
                else float(config["span_o2_pct"])
            )
            if not 15.0 <= self.target_span < AIR_NOMINAL:
                raise ValueError("Span must be below fixed air reference")
            self.is_active = True
            self.span_done = mode == "air"
            self.last_phase = "air" if mode == "air" else "span"
            self.validation_status = "COLLECTING"
        return True

    def start_new_calibration(self, target_points=None, span_value=None):
        return self._begin("full", target_points, span_value)

    def start_span_o2(self, target_points=None, span_value=None):
        # The host decides after sampling whether to continue with Air or call
        # finish_span_only() and persist a standalone pending Span result.
        return self._begin("full", target_points, span_value)

    def start_air_touchup(self, target_points=None):
        return self._begin("air", target_points)

    def _phase_ready(self, phase):
        return all(
            len(self.phase_data[channel][phase]) >= self.target_points
            for channel in self.channels
        )

    def _stable(self, channel, phase, config):
        values = self.phase_data[channel][phase]
        count = max(3, math.ceil(len(values) * STABLE_FRAC))
        tail = values[-count:]
        median = float(statistics.median(tail))
        mad = float(statistics.median(abs(value - median) for value in tail))
        limit = float(config.get("air_stability_max_mad_pct", 0.02))
        if mad > limit:
            raise ValueError(f"unstable MAD={mad:.5f} > {limit:.5f}")
        return median, mad

    def add_data(
        self,
        channel,
        o2_partial,
        zos_temp,
        gas_pressure,
        o2_percent,
        zos_rh=None,
        sht_temp=None,
        sht_rh=None,
    ):
        del o2_partial
        channel = str(channel).upper()
        if channel not in self.channels:
            return False

        temperature = sht_temp if sht_temp is not None else zos_temp
        relative_humidity = sht_rh if sht_rh is not None else zos_rh
        dry_o2 = calc_dry_o2(
            o2_percent,
            gas_pressure,
            temperature,
            relative_humidity,
        )
        if not math.isfinite(dry_o2):
            return False

        with self.lock:
            if not self.is_active:
                return False
            phase = "air" if self.span_done else "span"
            if len(self.phase_data[channel][phase]) < self.target_points:
                self.phase_data[channel][phase].append(dry_o2)
            self.last_phase = phase
            if not self._phase_ready(phase):
                return False
            if phase == "span" and self.cal_mode == "full":
                self.span_done = True
                self.last_phase = "air"
                self.validation_status = "SPAN_COLLECTED"
                logger.info(
                    "O2 Span samples collected; waiting for host Air stage: channels={}",
                    self.channels,
                )
                return False
            return self._finish()

    def _save(self, config):
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_path = tempfile.mkstemp(
            dir=self.config_path.parent,
            prefix=".calib-v5-",
            suffix=".json",
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(config, stream, indent=2, ensure_ascii=False)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temp_path, self.config_path)
        finally:
            if os.path.exists(temp_path):
                os.unlink(temp_path)

    @staticmethod
    def _valid_gain(value):
        value = float(value)
        if not math.isfinite(value) or not 0.5 <= value <= 1.5:
            raise ValueError(f"gain={value!r} outside 0.5..1.5")
        return value

    @staticmethod
    def _parse_utc(value):
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _reset_outcome(self):
        self.validation_warnings = []
        self.validation_details = {}
        self.failed_channels = {}
        self.passed_channels = []

    def _record_failure(self, channel, exc):
        message = str(exc)
        self.failed_channels[channel] = message
        detail = self.validation_details.setdefault(channel, {})
        detail["status"] = "FAILED"
        detail["error"] = message

    def _complete_outcome(self, config, *, calibrated, pending_air=False):
        if self.passed_channels:
            config["version"] = "5.0"
            config["air_o2_pct"] = AIR_NOMINAL
            config["air_fixed_pct"] = AIR_NOMINAL
            config["air_lock_mode"] = "fixed"
            config["calibration_time"] = datetime.now(timezone.utc).isoformat()
            self._save(config)

        self.is_active = False
        self.completed = True
        self.calibrated = bool(self.passed_channels and calibrated)
        if self.failed_channels and self.passed_channels:
            self.validation_status = "PARTIAL_SUCCESS"
        elif self.failed_channels:
            self.validation_status = "FAILED"
        elif pending_air:
            self.validation_status = "PENDING_AIR"
        elif self.validation_warnings:
            self.validation_status = "PASSED_WITH_WARNINGS"
        else:
            self.validation_status = "PASSED"
        self.last_error = (
            "; ".join(f"{channel}:{reason}" for channel, reason in self.failed_channels.items())
            or None
        )
        logger.info(
            "O2 calibration mode={} status={}, passed={}, failed={}",
            self.cal_mode,
            self.validation_status,
            self.passed_channels,
            self.failed_channels,
        )
        return bool(self.passed_channels)

    def _commit_span_only(self, config):
        self._reset_outcome()
        now = datetime.now(timezone.utc)
        pending = config.setdefault("pending_span_pairs", {})
        max_shift = float(config.get("span_max_offset_change_pct", 0.60))

        for channel in self.channels:
            try:
                span_dry, span_mad = self._stable(channel, "span", config)
                original = config["channels"][channel]
                updated = dict(original)
                gain = self._valid_gain(updated["gain"])
                old_air = float(updated["air_dry_pct"])
                new_air = span_dry - (self.target_span - AIR_NOMINAL) / gain
                shift = new_air - old_air
                if abs(shift) > max_shift:
                    raise ValueError(f"span_offset_change={shift:+.5f}")

                updated.update(
                    span_dry_pct=span_dry,
                    air_dry_pct=new_air,
                    basis="independent_span_offset_pending_pair",
                )
                config["channels"][channel] = updated
                pending[channel] = {
                    "span_dry_pct": span_dry,
                    "span_o2_pct": self.target_span,
                    "time_utc": now.isoformat(),
                    "status": "pending",
                }
                self.validation_details[channel] = {
                    "status": "PASSED",
                    "raw_dry_median_pct": span_dry,
                    "tail_mad_pct": span_mad,
                    "air_anchor_shift_pct": shift,
                    "gain_action": "unchanged_waiting_air",
                }
                self.passed_channels.append(channel)
            except (ValueError, TypeError, KeyError, OverflowError, ZeroDivisionError) as exc:
                self._record_failure(channel, exc)

        if self.passed_channels:
            config["span_o2_pct"] = self.target_span
            config["last_span_calibration_time"] = now.isoformat()
            config["calibration_method"] = "independent_span_only"
        return self._complete_outcome(config, calibrated=False, pending_air=True)

    def _commit_air_validation(self, config):
        self._reset_outcome()
        now = datetime.now(timezone.utc)
        pending = config.setdefault("pending_span_pairs", {})
        pair_hours = float(config.get("span_air_pair_max_hours", 8))
        if not 0 < pair_hours <= 168:
            raise ValueError("span_air_pair_max_hours must be in (0, 168]")
        max_shift = float(config.get("air_max_anchor_shift_pct", 0.60))
        max_relative = float(config.get("pair_gain_max_relative_change", 0.50))

        for channel in self.channels:
            try:
                air_dry, air_mad = self._stable(channel, "air", config)
                original = config["channels"][channel]
                updated = dict(original)
                old_gain = self._valid_gain(updated["gain"])
                old_air = float(updated["air_dry_pct"])
                shift = air_dry - old_air
                if abs(shift) > max_shift:
                    raise ValueError(f"air_anchor_change={shift:+.5f}")

                detail = {
                    "status": "PASSED",
                    "raw_dry_median_pct": air_dry,
                    "tail_mad_pct": air_mad,
                    "air_anchor_shift_pct": shift,
                    "gain_action": "unchanged_air_drift_only",
                }
                pair = pending.get(channel)
                if isinstance(pair, dict) and pair.get("status") == "pending":
                    age = now - self._parse_utc(pair["time_utc"])
                    if timedelta(0) <= age <= timedelta(hours=pair_hours):
                        span_dry = float(pair["span_dry_pct"])
                        span_value = float(pair["span_o2_pct"])
                        gap = air_dry - span_dry
                        if not 0.30 <= gap <= 0.70:
                            raise ValueError(f"span_air_gap={gap:.5f} outside 0.30..0.70")
                        proposed_gain = self._valid_gain(
                            (AIR_NOMINAL - span_value) / gap
                        )
                        if abs(proposed_gain / old_gain - 1.0) > max_relative:
                            raise ValueError(f"gain change exceeds {max_relative:.0%}")
                        updated["gain"] = proposed_gain
                        updated["basis"] = "independent_span_air_paired"
                        detail.update(
                            gain_old=old_gain,
                            gain_new=proposed_gain,
                            gain_action="updated_from_span_air_pair",
                            span_air_pair_age_seconds=age.total_seconds(),
                        )
                    else:
                        warning = f"{channel}:span_pair_expired; air drift correction only"
                        self.validation_warnings.append(warning)
                        detail["warning"] = warning
                        updated["basis"] = "independent_air_drift"
                else:
                    updated["basis"] = "independent_air_drift"

                if abs(shift) > 0.08:
                    warning = f"{channel}:baseline_shift={shift:+.4f}pct"
                    self.validation_warnings.append(warning)
                    detail.setdefault("warning", warning)
                updated["air_dry_pct"] = air_dry
                config["channels"][channel] = updated
                pending.pop(channel, None)
                self.validation_details[channel] = detail
                self.passed_channels.append(channel)
            except (ValueError, TypeError, KeyError, OverflowError, ZeroDivisionError) as exc:
                self._record_failure(channel, exc)

        if self.passed_channels:
            config["last_air_calibration_time"] = now.isoformat()
            config["calibration_method"] = "independent_air"
        self.air_measured = AIR_NOMINAL
        return self._complete_outcome(config, calibrated=True)

    def _commit_full_two_point(self, config):
        self._reset_outcome()
        now = datetime.now(timezone.utc)
        pending = config.setdefault("pending_span_pairs", {})
        span_shift_limit = float(config.get("span_max_offset_change_pct", 0.60))
        air_shift_limit = float(config.get("air_max_anchor_shift_pct", 0.60))
        max_relative = float(config.get("pair_gain_max_relative_change", 0.50))

        for channel in self.channels:
            try:
                span_dry, span_mad = self._stable(channel, "span", config)
                air_dry, air_mad = self._stable(channel, "air", config)
                original = config["channels"][channel]
                updated = dict(original)
                old_gain = self._valid_gain(updated["gain"])
                old_air = float(updated["air_dry_pct"])

                span_predicted_air = span_dry - (
                    self.target_span - AIR_NOMINAL
                ) / old_gain
                span_shift = span_predicted_air - old_air
                if abs(span_shift) > span_shift_limit:
                    raise ValueError(f"span_offset_change={span_shift:+.5f}")

                air_shift = air_dry - span_predicted_air
                if abs(air_shift) > air_shift_limit:
                    raise ValueError(f"air_anchor_change={air_shift:+.5f}")
                gap = air_dry - span_dry
                if not 0.30 <= gap <= 0.70:
                    raise ValueError(f"span_air_gap={gap:.5f} outside 0.30..0.70")
                proposed_gain = self._valid_gain(
                    (AIR_NOMINAL - self.target_span) / gap
                )
                if abs(proposed_gain / old_gain - 1.0) > max_relative:
                    raise ValueError(f"gain change exceeds {max_relative:.0%}")

                updated.update(
                    span_dry_pct=span_dry,
                    air_dry_pct=air_dry,
                    gain=proposed_gain,
                    basis="combined_span_air_paired",
                )
                config["channels"][channel] = updated
                pending.pop(channel, None)
                self.validation_details[channel] = {
                    "status": "PASSED",
                    "span_dry_pct": span_dry,
                    "air_dry_pct": air_dry,
                    "span_mad_pct": span_mad,
                    "air_mad_pct": air_mad,
                    "span_anchor_shift_pct": span_shift,
                    "air_anchor_shift_pct": air_shift,
                    "gain_old": old_gain,
                    "gain_new": proposed_gain,
                    "gain_action": "updated_from_combined_span_air",
                }
                self.passed_channels.append(channel)
            except (ValueError, TypeError, KeyError, OverflowError, ZeroDivisionError) as exc:
                self._record_failure(channel, exc)

        if self.passed_channels:
            config["span_o2_pct"] = self.target_span
            config["last_span_calibration_time"] = now.isoformat()
            config["last_air_calibration_time"] = now.isoformat()
            config["calibration_method"] = "combined_span_air"
        self.air_measured = AIR_NOMINAL
        return self._complete_outcome(config, calibrated=True)

    def finish_span_only(self):
        with self.lock:
            if not self.is_active or not self.span_done:
                raise RuntimeError("O2 Span samples are incomplete")
            config = self._load_config()
            return self._commit_span_only(config)

    def _finish(self):
        config = self._load_config()
        if self.cal_mode == "air":
            return self._commit_air_validation(config)
        return self._commit_full_two_point(config)

    def get_status(self):
        with self.lock:
            phase = "air" if self.span_done else "span"
            current_counts = {
                channel: len(self.phase_data[channel][phase])
                for channel in self.channels
            }
            all_ready = all(
                count >= self.target_points
                for count in current_counts.values()
            )
            phase_counts = {
                item: {
                    channel: len(self.phase_data[channel][item])
                    for channel in self.channels
                }
                for item in ("span", "air")
            }
            pending_channels = []
            try:
                pending_channels = sorted(
                    channel
                    for channel, value in self._load_config()
                    .get("pending_span_pairs", {})
                    .items()
                    if isinstance(value, dict) and value.get("status") == "pending"
                )
            except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
                pass
            return {
                "is_active": self.is_active,
                "calibrated": self.calibrated,
                "completed": self.completed,
                "cal_mode": self.cal_mode,
                "last_phase": self.last_phase,
                "span_done": self.span_done,
                "need_air": (
                    self.cal_mode == "full"
                    and self.span_done
                    and self.is_active
                ),
                "skip_b_update": self.cal_mode == "air",
                "phase_counts": phase_counts,
                "current_counts": current_counts,
                "all_ready": all_ready,
                "failed": self.validation_status == "FAILED",
                "partial_success": self.validation_status == "PARTIAL_SUCCESS",
                "failure_reason": self.last_error,
                "target_points": self.target_points,
                "validation_status": self.validation_status,
                "last_error": self.last_error,
                "validation_warnings": list(self.validation_warnings),
                "validation_details": dict(self.validation_details),
                "passed_channels": list(self.passed_channels),
                "failed_channels": dict(self.failed_channels),
                "pending_air_channels": pending_channels,
                "config_path": str(self.config_path),
                "target_span": self.target_span,
                "air_lock": self.air_lock,
                "air_measured": self.air_measured,
                "channels": self.channels.copy(),
            }


_CALIBRATION_HANDLER_LOCK = threading.Lock()
_CALIBRATION_HANDLER = None


def get_o2_calibration_handler(target_points=None):
    global _CALIBRATION_HANDLER
    with _CALIBRATION_HANDLER_LOCK:
        if _CALIBRATION_HANDLER is None:
            _CALIBRATION_HANDLER = CalibrationHandler(target_points)
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
    return get_o2_calibration_handler().add_data(
        channel=channel,
        o2_partial=o2_partial,
        zos_temp=zos_temp,
        gas_pressure=gas_pressure,
        o2_percent=o2_percent,
        zos_rh=sht_rh,
        sht_temp=sht_temp,
        sht_rh=sht_rh,
    )


def get_o2_calibration_status():
    return get_o2_calibration_handler().get_status()
