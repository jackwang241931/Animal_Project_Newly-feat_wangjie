import json
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.signal import savgol_filter
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import PolynomialFeatures
from loguru import logger


VALID_CHANNELS = ["REF"] + [f"M{i}" for i in range(1, 9)]

EMPIRICAL_OFFSETS = {
    "REF": 0.0,
    "M1": 0.0,
    "M2": -0.05,   
    "M3": 0.05,
    "M4": -0.05,   
    "M5": -0.25,  
    "M6": 0.0,
    "M7": -0.10,   
    "M8": 0.0,
}


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


class CalibrationHandler:
    def __init__(self, target_points=60, config_path=None):
        self.channels = VALID_CHANNELS.copy()
        self.target_points = int(target_points)
        self.config_path = Path(config_path or get_default_config_path())
        self.lock = threading.Lock()
        self.target_o2 = 20.93
        self.reset()

    def reset(self):
        self.data = defaultdict(list)
        self.is_active = False
        self.calibrated = False
        self.completed = False
        self.offsets = EMPIRICAL_OFFSETS.copy() 
        self.gains = {}
        self.secondary_models = {}

    def start_new_calibration(self):
        with self.lock:
            self.reset()
            self.is_active = True
        return True

    def add_data(self, channel, o2_partial, zos_temp, gas_pressure, o2_percent, zos_rh):
        del o2_partial

        with self.lock:
            if channel not in self.channels:
                return False
            if self.calibrated:
                return True
            if not self.is_active or self.completed:
                return False

            self.data[channel].append({
                "gas_pressure": float(gas_pressure),
                "o2_percent": float(o2_percent),
                "zos_temp": float(zos_temp),
                "zos_rh": float(zos_rh),
            })

            all_ready = all(len(self.data[item]) >= self.target_points for item in self.channels)
            if not all_ready:
                return False

            logger.info(
                f"O2 Air calibration points ready, starting coefficient calculation: "
                f"{ {item: len(self.data[item]) for item in self.channels} }"
            )
            success = self._perform_enhanced_calibration()
            if success:
                logger.info(f"O2 Air calibration coefficients saved to {self.config_path}")
                self.calibrated = True
                self.completed = True
                self.is_active = False
            else:
                logger.error("O2 Air calibration coefficient calculation failed")
            return success

    def _perform_enhanced_calibration(self):
        processed = {}
        thresholds = {
            "o2_percent": 5.0, 
            "gas_pressure": 5.0,
            "zos_temp": 5.0,
            "zos_rh": 10.0,
        }

        try:
            for channel in self.channels:
                frame = pd.DataFrame(self.data[channel][: self.target_points])
                for key, threshold in thresholds.items():
                    if key in frame.columns:
                        frame[key] = sequential_jump_clean(frame[key], threshold)
                processed[channel] = self._enhanced_process(frame, channel)

            for channel in self.channels:
                mean_val = float(processed[channel]["final"].mean())
                y = float(self.offsets.get(channel, 0.0))

                denominator = mean_val + y
                if pd.isna(denominator) or abs(denominator) < 0.1:
                    self.gains[channel] = 1.0
                else:
                    self.gains[channel] = float(self.target_o2 / denominator)

            self._save_config()
            return True
        except Exception as e:
            logger.exception(f"Calibration failed: {e}")
            return False

    def _enhanced_process(self, frame, channel):
        frame["dry_raw"] = frame.apply(
            lambda row: calc_dry_o2(
                row["o2_percent"],
                row["gas_pressure"],
                row["zos_temp"],
                row["zos_rh"],
            ),
            axis=1,
        )

        dry_values = frame["dry_raw"].ffill().bfill().values
        if len(dry_values) >= 11:
            frame["dry_sg"] = savgol_filter(dry_values, window_length=11, polyorder=2)
        else:
            frame["dry_sg"] = dry_values

        mask = (
            frame["dry_sg"].notna()
            & frame["zos_rh"].notna()
            & frame["zos_temp"].notna()
        )
        if mask.sum() > 50:
            features = np.column_stack((
                frame.loc[mask, "zos_rh"].values,
                frame.loc[mask, "zos_temp"].values,
            ))
            labels = frame.loc[mask, "dry_sg"].values

            poly = PolynomialFeatures(degree=2, include_bias=False)
            features_poly = poly.fit_transform(features)
            model = LinearRegression().fit(features_poly, labels)

            self.secondary_models[channel] = {
                "coef": model.coef_.tolist(),
                "intercept": float(model.intercept_),
            }
            frame["dry_sec"] = frame["dry_sg"]  
        else:
            frame["dry_sec"] = frame["dry_sg"]

        frame["final"] = frame["dry_sg"]  
        return frame

    def _save_config(self):
        config = {
            "version": "2.3",
            "temperature_source": "ZOS_temperature_2",
            "humidity_source": "ZOS_humidity",
            "calibration_time": datetime.now().isoformat(),
            "target_o2": self.target_o2,
            "target_points": self.target_points,
            "offsets": self.offsets,          
            "gains": self.gains,
            "secondary_models": self.secondary_models,
        }
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        with self.config_path.open("w", encoding="utf-8") as file:
            json.dump(config, file, indent=2, ensure_ascii=False)

    def get_status(self):
        with self.lock:
            points_received = {channel: len(records) for channel, records in self.data.items()}
            current_counts = {channel: points_received.get(channel, 0) for channel in self.channels}
            return {
                "is_active": self.is_active,
                "calibrated": self.calibrated,
                "completed": self.completed,
                "points_received": points_received,
                "current_counts": current_counts,
                "all_ready": all(count >= self.target_points for count in current_counts.values()),
                "target_points": self.target_points,
                "config_path": str(self.config_path),
            }
