import json
import math
from pathlib import Path

import pandas as pd


def get_default_config_path():
    return Path(__file__).resolve().parents[4] / "config" / "co2_calib_config.json"


class CO2RealtimeCompensator:
    def __init__(self, config_path=None):
        self.config_path = Path(config_path or get_default_config_path())
        self.channels = ["REF"] + [f"M{i}" for i in range(1, 9)]
        self.last_valid = {channel: None for channel in self.channels}
        self.valid_range = (0, 5000)
        self.max_consecutive_invalid = 10
        self.consecutive_invalid = {channel: 0 for channel in self.channels}
        self._config_mtime = None
        self.config = {"channels": {}}
        self.coefs = {}
        self.reload_config(force=True)

    def _load_config(self):
        try:
            if not self.config_path.exists():
                return {"channels": {}}
            with self.config_path.open("r", encoding="utf-8") as file:
                return json.load(file)
        except Exception:
            return {"channels": {}}

    def _load_coefficients(self):
        coefficients = {}
        for channel, data in self.config.get("channels", {}).items():
            try:
                coefficients[channel] = {
                    "coef_k": float(data["coef_k"]),
                    "coef_b": float(data["coef_b"]),
                    "r2": float(data.get("r2", 1.0)),
                }
            except (KeyError, ValueError, TypeError):
                continue
        return coefficients

    def reload_config(self, force=False):
        try:
            current_mtime = self.config_path.stat().st_mtime
        except OSError:
            current_mtime = None

        if not force and current_mtime == self._config_mtime:
            return True

        self._config_mtime = current_mtime
        self.config = self._load_config()
        self.coefs = self._load_coefficients()
        return True

    def compensate(self, channel, co2_std):
        self.reload_config()

        if channel not in self.channels:
            return float("nan")

        value = float(co2_std) if not pd.isna(co2_std) else float("nan")
        is_valid = not pd.isna(value) and self.valid_range[0] <= value <= self.valid_range[1]

        if not is_valid:
            self.consecutive_invalid[channel] += 1
            if self.last_valid[channel] is not None:
                value = self.last_valid[channel]
            else:
                return float("nan")
        else:
            self.consecutive_invalid[channel] = 0
            self.last_valid[channel] = value

        if channel == "REF":
            return round(value, 2)

        if channel not in self.coefs:
            return float("nan")

        coef = self.coefs[channel]
        compensated = coef["coef_k"] * value + coef["coef_b"]

        if math.isnan(compensated):
            return float("nan")
        if compensated < 0:
            compensated = 0.0
        elif compensated > 10000:
            compensated = 10000.0

        return round(compensated, 2)

    def batch_compensate(self, data_dict):
        return {
            channel: self.compensate(channel, co2_std)
            for channel, co2_std in data_dict.items()
        }

    def get_calibration_status(self):
        status = {}
        for channel in self.channels:
            if channel == "REF":
                status[channel] = {"calibrated": True, "source": "reference"}
            elif channel in self.coefs:
                status[channel] = {
                    "calibrated": True,
                    "k": self.coefs[channel]["coef_k"],
                    "b": self.coefs[channel]["coef_b"],
                    "r2": self.coefs[channel].get("r2", "N/A"),
                }
            else:
                status[channel] = {"calibrated": False}
        return status

    def get_statistics(self):
        return {
            "last_valid_values": {k: v for k, v in self.last_valid.items() if v is not None},
            "consecutive_invalid_counts": self.consecutive_invalid,
            "valid_range": self.valid_range,
            "calibrated_channels": list(self.coefs.keys()),
        }
