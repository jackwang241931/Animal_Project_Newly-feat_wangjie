import json
import threading
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression
from loguru import logger


VALID_CHANNELS = ["REF"] + [f"M{i}" for i in range(1, 9)]


def get_default_config_path():
    return Path(__file__).resolve().parents[4] / "config" / "co2_calib_config.json"


class CO2CalibrationHandler:
    def __init__(self, target_points=120, config_path=None):
        self.channels = VALID_CHANNELS.copy()
        self.target_points = int(target_points)
        self.config_path = Path(config_path or get_default_config_path())
        self.data = {channel: [] for channel in self.channels}
        self.calibrated = False
        self.is_active = False
        self.failed = False
        self.failure_reason = None
        self.lock = threading.Lock()

        self.valid_range = (0, 5000)
        self.min_valid_ratio = 0.8
        self.k_limit = (0.80, 1.20)
        self.time_sync_tolerance = 2.0

    def start_new_calibration(self):
        with self.lock:
            self.data = {channel: [] for channel in self.channels}
            self.calibrated = False
            self.is_active = True
            self.failed = False
            self.failure_reason = None
        return True

    def add_data(self, channel, co2_std, timestamp=None):
        with self.lock:
            if channel not in self.channels or not self.is_active or self.calibrated or self.failed:
                return self.calibrated
            if len(self.data[channel]) >= self.target_points:
                return False

            if timestamp is None:
                ts = datetime.now()
            elif isinstance(timestamp, (int, float)):
                ts = datetime.fromtimestamp(timestamp)
            else:
                ts = pd.to_datetime(timestamp)

            self.data[channel].append({
                "co2_std": float(co2_std),
                "timestamp": ts,
            })

            all_ready = all(len(self.data[item]) >= self.target_points for item in self.channels)
            if not all_ready:
                return False

            logger.info(
                f"CO2 Air calibration points ready, starting coefficient calculation: "
                f"{ {item: len(self.data[item]) for item in self.channels} }"
            )
            success = self._perform_calibration()
            if success:
                logger.info(
                    f"CO2 Air calibration coefficients saved to {self.config_path} "
                    f"(time_sync_tolerance={self.time_sync_tolerance}s)"
                )
                self.calibrated = True
                self.is_active = False
            else:
                self.failed = True
                self.is_active = False
                logger.error(
                    f"CO2 Air calibration coefficient calculation failed; "
                    f"existing configuration retained: {self.failure_reason or 'unknown reason'}"
                )
            return success

    def mark_failed(self, reason):
        """记录不可用的 CO2 拟合结果，但不负责决定外层校准是否终止。"""
        with self.lock:
            self.failed = True
            self.is_active = False
            self.failure_reason = str(reason)

    def _preprocess_channel_data(self, channel):
        frame = pd.DataFrame(self.data[channel])
        if frame.empty:
            return frame

        frame["is_valid"] = (
            (frame["co2_std"] >= self.valid_range[0])
            & (frame["co2_std"] <= self.valid_range[1])
        )
        valid_median = frame.loc[frame["is_valid"], "co2_std"].median()
        frame["co2_clean"] = frame["co2_std"].where(frame["is_valid"], valid_median)

        if frame["co2_clean"].isna().all():
            return pd.DataFrame()

        return frame[["timestamp", "co2_clean", "is_valid"]]

    def _align_data_by_time(self, ref_frame, channel_frame):
        ref_frame = ref_frame.sort_values("timestamp").reset_index(drop=True)
        channel_frame = channel_frame.sort_values("timestamp").reset_index(drop=True)

        aligned = pd.merge_asof(
            channel_frame,
            ref_frame,
            on="timestamp",
            direction="nearest",
            tolerance=pd.Timedelta(seconds=self.time_sync_tolerance),
            suffixes=("_ch", "_ref"),
        )
        return aligned.dropna(subset=["co2_clean_ref", "co2_clean_ch"])

    def _perform_calibration(self):
        try:
            ref_raw = self._preprocess_channel_data("REF")
            if ref_raw.empty:
                self.failure_reason = "REF has no usable data"
                return False

            valid_count_ref = int(ref_raw["is_valid"].sum())
            if valid_count_ref < self.target_points * self.min_valid_ratio:
                self.failure_reason = (
                    f"REF valid points {valid_count_ref}/{self.target_points} "
                    f"below minimum ratio {self.min_valid_ratio:.0%}"
                )
                return False

            ref_raw = ref_raw.sort_values("timestamp").reset_index(drop=True)
            ref_raw["co2_smooth"] = (
                ref_raw["co2_clean"]
                .rolling(window=11, center=True, min_periods=5)
                .mean()
                .ffill()
                .bfill()
            )

            coefficient_items = []
            for channel in self.channels:
                if channel == "REF":
                    coefficient_items.append({
                        "Channel": "REF",
                        "Coef_K": 1.0,
                        "Coef_B": 0.0,
                        "R2": 1.0,
                        "Valid_Points": len(ref_raw),
                    })
                    continue

                channel_raw = self._preprocess_channel_data(channel)
                if channel_raw.empty:
                    continue

                valid_count_channel = int(channel_raw["is_valid"].sum())
                if valid_count_channel < self.target_points * self.min_valid_ratio:
                    continue

                aligned = self._align_data_by_time(ref_raw, channel_raw)
                if len(aligned) < self.target_points * self.min_valid_ratio:
                    continue

                x_values = aligned[["co2_clean_ch"]].values
                y_values = aligned["co2_smooth"].values

                model = LinearRegression()
                model.fit(x_values, y_values)

                coef_k = float(model.coef_[0])
                coef_b = float(model.intercept_)
                r2 = float(model.score(x_values, y_values))

                x_mean = x_values.mean()
                y_mean = y_values.mean()
                coef_k_limited = max(self.k_limit[0], min(self.k_limit[1], coef_k))
                if coef_k_limited != coef_k:
                    coef_b = y_mean - coef_k_limited * x_mean

                coefficient_items.append({
                    "Channel": channel,
                    "Coef_K": round(coef_k_limited, 6),
                    "Coef_B": round(coef_b, 4),
                    "R2": round(r2, 4),
                    "Valid_Points": len(aligned),
                })

            if len(coefficient_items) < 2:
                self.failure_reason = (
                    "fewer than one measurement channel produced a valid coefficient"
                )
                return False

            self._write_config(coefficient_items)
            return True
        except Exception as exc:
            self.failure_reason = f"{type(exc).__name__}: {exc}"
            logger.exception("CO2 Air calibration coefficient calculation raised an exception")
            return False

    def _write_config(self, coefficient_items):
        config = {
            "version": "2.1",
            "sensor_model": "GS5T",
            "calibration_info": {
                "calibration_date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "calibration_points": self.target_points,
                "method": "Air_Consistency_Alignment_TimeSync",
            },
            "channels": {},
        }

        for item in coefficient_items:
            channel = item["Channel"]
            config["channels"][channel] = {
                "coef_k": item["Coef_K"],
                "coef_b": item["Coef_B"],
                "r2": item.get("R2", 1.0),
                "valid_points": item.get("Valid_Points", 0),
            }

        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config_path.open("w", encoding="utf-8") as file:
            json.dump(config, file, indent=2, ensure_ascii=False)

    def get_status(self):
        with self.lock:
            counts = {channel: len(self.data[channel]) for channel in self.channels}
            valid_counts = {}

            for channel in self.channels:
                if self.data[channel]:
                    frame = self._preprocess_channel_data(channel)
                    valid_counts[channel] = int(frame["is_valid"].sum()) if not frame.empty else 0
                else:
                    valid_counts[channel] = 0

            return {
                "is_active": self.is_active,
                "calibrated": self.calibrated,
                "failed": self.failed,
                "failure_reason": self.failure_reason,
                "current_counts": counts,
                "valid_counts": valid_counts,
                "all_ready": all(count >= self.target_points for count in counts.values()),
                "target_points": self.target_points,
                "min_valid_ratio": self.min_valid_ratio,
                "config_path": str(self.config_path),
            }

    def get_calibration_quality(self):
        try:
            with self.config_path.open("r", encoding="utf-8") as file:
                config = json.load(file)

            report = {
                "calibration_date": config["calibration_info"]["calibration_date"],
                "channels": [],
            }

            for channel, data in config.get("channels", {}).items():
                if channel == "REF":
                    continue
                report["channels"].append({
                    "channel": channel,
                    "k": data["coef_k"],
                    "b": data["coef_b"],
                    "r2": data.get("r2", "N/A"),
                    "quality": self._evaluate_quality(data["coef_k"], data.get("r2", 0)),
                })

            return report
        except Exception as exc:
            return {"error": str(exc)}

    @staticmethod
    def _evaluate_quality(coef_k, r2):
        if coef_k < 0.85 or coef_k > 1.15:
            return "较差"
        if coef_k < 0.95 or coef_k > 1.05:
            return "一般"
        if r2 < 0.95:
            return "一般"
        return "良好"
