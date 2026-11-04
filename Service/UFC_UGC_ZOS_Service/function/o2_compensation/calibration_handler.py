from __future__ import annotations

import json
import math
import os
import statistics
import tempfile
import threading
from datetime import datetime, timezone
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
    """O2 v6 calibration with the host application's selectable channels."""

    def __init__(self, target_points=None, config_path=None, duration_s=None):
        del duration_s
        self.config_path = Path(config_path or get_default_config_path())
        self.lock = threading.RLock()
        self.channels = VALID_CHANNELS.copy()
        self.target_points = max(
            5,
            int(target_points or DEFAULT_TARGET_POINTS),
        )
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
            if not 15.0 < span_value < 21.0:
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
        self.validation_details = {}
        self.validation_status = "NOT_STARTED"
        self.target_span = None
        self.air_lock = AIR_NOMINAL
        self.air_measured = None

    def _load_config(self):
        with self.config_path.open(encoding="utf-8") as stream:
            config = json.load(stream)
        if str(config.get("version")) != "6.0":
            raise ValueError("v6 config required")
        if set(config.get("channels", {})) != set(VALID_CHANNELS):
            raise ValueError("v6 config requires REF and M1-M8")
        return config

    def _begin(self, mode, target_points=None, span_value=None):
        config = self._load_config()
        with self.lock:
            if target_points is not None:
                self.set_target_points(target_points)
            self.reset()
            self.cal_mode = mode
            self.air_lock = float(config["air_o2_pct"])
            self.target_span = (
                float(span_value)
                if span_value is not None
                else float(config["span_o2_pct"])
            )
            if not 15.0 < self.target_span < self.air_lock:
                raise ValueError("span must be lower than air")
            self.is_active = True
            self.span_done = mode == "air"
            self.last_phase = "air" if mode == "air" else None
            self.validation_status = "COLLECTING"
        return True

    def start_new_calibration(self, target_points=None, span_value=None):
        return self._begin("full", target_points, span_value)

    def start_span_o2(self, target_points=None, span_value=None):
        # The host decides whether the completed Span stage is followed by Air
        # immediately or is committed as a standalone PENDING_AIR result.
        return self._begin("full", target_points, span_value)

    def start_air_touchup(self, target_points=None):
        return self._begin("air", target_points)

    def _phase_ready(self, phase):
        return all(
            len(self.phase_data[channel][phase]) >= self.target_points
            for channel in self.channels
        )

    def _stable(self, channel, phase):
        values = self.phase_data[channel][phase]
        tail_start = int(len(values) * (1.0 - STABLE_FRAC))
        tail = values[tail_start:]
        median = float(statistics.median(tail))
        mad = float(statistics.median(abs(value - median) for value in tail))
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

        # The ZOS response exposes the humidity-sensor readings as
        # ``ZOS温度2/湿度``.  Some firmware/parser versions additionally expose
        # explicit SHT fields, while others do not.  Use the explicit SHT
        # readings when present and otherwise follow the normal experiment
        # compensation path, which uses ZOS温度2/湿度 as the SHT inputs.
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
            self.phase_data[channel][phase].append(dry_o2)
            self.last_phase = phase
            if not self._phase_ready(phase):
                return False
            if phase == "span" and self.cal_mode == "full":
                self.span_done = True
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
            prefix=".calib-v6-",
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

    def _fail(self, reason, details):
        self.is_active = False
        self.calibrated = False
        self.completed = True
        self.validation_status = "FAILED"
        self.last_error = str(reason)
        self.validation_details = details
        logger.error("O2 Air verification failed: {}", reason)
        return False

    def _commit_air_validation(self, config):
        channel_config = config["channels"]
        values = {
            channel: self._stable(channel, "air")
            for channel in self.channels
        }

        locked_air = float(config["air_o2_pct"])
        reference_config = channel_config["REF"]
        reference_dry, reference_mad = values["REF"]
        reference_calibrated = locked_air + float(reference_config["gain"]) * (
            reference_dry - float(reference_config["air_dry_pct"])
        )
        self.air_measured = reference_calibrated

        lower = float(config["air_valid_min_pct"])
        upper = float(config["air_valid_max_pct"])
        tolerance = float(config["air_alignment_tolerance_pct"])
        maximum_mad = float(config["air_stability_max_mad_pct"])
        details = {}
        failures = []

        for channel in self.channels:
            dry_o2, mad = values[channel]
            parameters = channel_config[channel]
            calibrated = locked_air + float(parameters["gain"]) * (
                dry_o2 - float(parameters["air_dry_pct"])
            )
            difference = calibrated - reference_calibrated
            details[channel] = {
                "dry_median": dry_o2,
                "dry_mad": mad,
                "before_alignment_pct": calibrated,
                "difference_from_REF_pct": difference,
            }
            if mad > maximum_mad:
                failures.append(f"{channel}:unstable")
            if abs(difference) > tolerance:
                failures.append(f"{channel}:not_aligned")

        if reference_mad > maximum_mad and "REF:unstable" not in failures:
            failures.append("REF:unstable")
        if not lower <= reference_calibrated <= upper:
            failures.append("REF:out_of_air_range")
        if failures:
            return self._fail(";".join(failures), details)

        for channel in self.channels:
            channel_config[channel]["air_dry_pct"] = values[channel][0]
            channel_config[channel]["basis"] = "air_checked_existing_span"
        config["air_o2_pct"] = reference_calibrated
        config["air_calibrated"] = True
        config["air_validation_status"] = "DAILY_AIR_PASSED"
        config["pending_span_validation"] = False
        config["calibration_method"] = "daily_air_verified_previous_span"
        config["calibration_time"] = datetime.now(timezone.utc).isoformat()
        self._save(config)

        self.air_lock = reference_calibrated
        self.validation_details = details
        self.validation_status = "PASSED"
        return True

    def _commit_span_only(self, config):
        channel_config = config["channels"]
        details = {}
        for channel in self.channels:
            new_span, span_mad = self._stable(channel, "span")
            parameters = channel_config[channel]
            delta = new_span - float(parameters["span_dry_pct"])
            parameters["span_dry_pct"] = new_span
            parameters["air_dry_pct"] = float(parameters["air_dry_pct"]) + delta
            parameters["basis"] = "span_one_point_pending_air_validation"
            details[channel] = {
                "span_dry_pct": new_span,
                "span_mad": span_mad,
                "air_dry_shift_pct": delta,
            }

        config["span_o2_pct"] = self.target_span
        config["pending_span_validation"] = True
        config["air_calibrated"] = False
        config["air_validation_status"] = "SPAN_CHANGED_AIR_CHECK_REQUIRED"
        config["calibration_method"] = "span_one_point_gain_unchanged"
        config["calibration_time"] = datetime.now(timezone.utc).isoformat()
        self._save(config)

        self.validation_details = details
        self.validation_status = "PENDING_AIR"
        self.is_active = False
        self.completed = True
        self.calibrated = False
        return True

    def _commit_full_two_point(self, config):
        channel_config = config["channels"]
        design_air = float(config.get("air_design_nominal_pct", AIR_NOMINAL))
        maximum_mad = float(config["air_stability_max_mad_pct"])
        details = {}
        failures = []

        for channel in self.channels:
            span_dry, span_mad = self._stable(channel, "span")
            air_dry, air_mad = self._stable(channel, "air")
            gap = air_dry - span_dry
            gain = (
                (design_air - self.target_span) / gap
                if gap
                else float("nan")
            )
            details[channel] = {
                "span_dry_pct": span_dry,
                "air_dry_pct": air_dry,
                "gain": gain,
                "span_mad": span_mad,
                "air_mad": air_mad,
            }
            if not math.isfinite(gain) or not 0.5 <= gain <= 1.5:
                failures.append(f"{channel}:bad_gain")
            if max(span_mad, air_mad) > maximum_mad:
                failures.append(f"{channel}:unstable")

        if failures:
            return self._fail(";".join(failures), details)

        for channel in self.channels:
            parameters = channel_config[channel]
            parameters.update(
                span_dry_pct=details[channel]["span_dry_pct"],
                air_dry_pct=details[channel]["air_dry_pct"],
                gain=details[channel]["gain"],
                basis="full_two_point_pending_independent_air_check",
            )
        config["span_o2_pct"] = self.target_span
        config["air_o2_pct"] = design_air
        config["air_calibrated"] = False
        config["pending_span_validation"] = True
        config["air_validation_status"] = "FULL_TWO_POINT_AIR_RECHECK_REQUIRED"
        config["calibration_method"] = "two_point_fitted_unverified"
        config["calibration_time"] = datetime.now(timezone.utc).isoformat()
        self._save(config)

        self.air_lock = design_air
        self.validation_details = details
        self.validation_status = "PENDING_AIR"
        return True

    def finish_span_only(self):
        with self.lock:
            if not self.is_active or not self.span_done:
                raise RuntimeError("O2 Span samples are incomplete")
            config = self._load_config()
            return self._commit_span_only(config)

    def _finish(self):
        config = self._load_config()
        if self.cal_mode == "air":
            success = self._commit_air_validation(config)
        else:
            success = self._commit_full_two_point(config)
        if self.validation_status != "FAILED":
            self.is_active = False
            self.completed = True
            self.calibrated = bool(config.get("air_calibrated", False))
        return success

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
                "phase_counts": phase_counts,
                "current_counts": current_counts,
                "all_ready": all_ready,
                "failed": self.validation_status == "FAILED",
                "failure_reason": self.last_error,
                "target_points": self.target_points,
                "validation_status": self.validation_status,
                "last_error": self.last_error,
                "validation_details": self.validation_details,
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
