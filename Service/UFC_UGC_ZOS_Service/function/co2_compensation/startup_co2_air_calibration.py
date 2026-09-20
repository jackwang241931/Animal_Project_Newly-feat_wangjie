import copy
import time

from blinker.base import _PNamespaceSignal
from loguru import logger

from Service.UFC_UGC_ZOS_Service.function.Send_Message.Send_Message import Send_Message
from Service.UFC_UGC_ZOS_Service.function.co2_compensation.co2_calibration_handler import (
    CO2CalibrationHandler,
)
from public.config_class.global_setting import global_setting
from public.entity.enum.Public_Enum import GapSystem_Running_Type
from public.function.promise.AsyPromise import AsyPromise
from public.util.number_util import number_util
from public.util.time_util import time_util


class Startup_CO2_Air_Calibration:
    def __init__(self, title=GapSystem_Running_Type.STARTUP_CO2_AIR_CALIBRATION):
        self.title = title
        self.name = "运行前 CO2 Air 空气校准"
        self.update_status_main_signal_gui_update: _PNamespaceSignal = None
        self.send_message = {
            "port": "",
            "data": "",
            "slave_id": 0,
            "function_code": 0,
            "timeout": 0,
        }
        self.send_thread = Send_Message(
            update_status_main_signal_gui_update=self.update_status_main_signal_gui_update,
            send_message=self.send_message,
            update_status_main_signal_gui_update_type=title,
        )
        self.current_calibration_values = {
            "oxygen_value": None,
            "carbon_value": None,
            "oxygen_pressure_value": None,
        }
        self.calibration_handler = None

    def update(self):
        self.send_thread.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update

    def _send_text(self, text):
        if self.update_status_main_signal_gui_update is not None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | {text}",
                title=self.title,
            )

    def _send_state(self, state_type, value):
        if self.update_status_main_signal_gui_update is not None:
            self.update_status_main_signal_gui_update.send(
                {"type": state_type, "value": value},
                title=self.title,
            )

    def push_calibration_values_to_ui(self, carbon_value=None, oxygen_pressure_value=None):
        if carbon_value is not None:
            self.current_calibration_values["carbon_value"] = carbon_value
        if oxygen_pressure_value is not None:
            self.current_calibration_values["oxygen_pressure_value"] = oxygen_pressure_value

        if self.update_status_main_signal_gui_update is not None:
            self.update_status_main_signal_gui_update.send(
                {"type": "set_calibration_values", "value": copy.deepcopy(self.current_calibration_values)},
                title=self.title,
            )

    @staticmethod
    def _normalize_wait_seconds():
        calibration_config = global_setting.get_setting("UFC_UGC_ZOS_config", {}).get("Calibration", {})
        try:
            wait_seconds = int(float(calibration_config.get("startup_air_calibration_wait_time", 1800)))
        except Exception:
            wait_seconds = 1800
        return max(wait_seconds, 0)

    @staticmethod
    def _normalize_sample_interval():
        calibration_config = global_setting.get_setting("UFC_UGC_ZOS_config", {}).get("Calibration", {})
        try:
            sample_interval = float(calibration_config.get("calibration_sample_interval", 1))
        except Exception:
            sample_interval = 1.0
        return max(sample_interval, 0.5)

    @staticmethod
    def _normalize_target_points():
        calibration_config = global_setting.get_setting("UFC_UGC_ZOS_config", {}).get("Calibration", {})
        try:
            target_points = int(float(calibration_config.get("startup_air_calibration_target_points", 120)))
        except Exception:
            target_points = 120
        return max(target_points, 1)

    @staticmethod
    def _normalize_run_timeout(target_points, sample_interval):
        calibration_config = global_setting.get_setting("UFC_UGC_ZOS_config", {}).get("Calibration", {})
        default_timeout = max(int(target_points * sample_interval * 3), 300)
        try:
            max_timeout = int(float(calibration_config.get("startup_air_calibration_max_timeout", default_timeout)))
        except Exception:
            max_timeout = default_timeout
        return max(max_timeout, default_timeout)

    @staticmethod
    def _normalize_co2_to_ppm(value):
        value = float(value)
        if value < 100:
            value = value * 10000
        return value

    @staticmethod
    def _extract_data_value(data_items, keyword):
        for item in data_items or []:
            desc = str(item.get("desc", ""))
            if keyword in desc:
                return item.get("value")
        return None

    @staticmethod
    def _channel_to_handler_name(channel):
        if int(channel) == 8:
            return "REF"
        return f"M{int(channel) + 1}"

    @staticmethod
    def _channel_to_display_name(channel):
        if int(channel) == 8:
            return "参考气"
        return f"{int(channel) + 1}号鼠笼"

    def _get_port(self):
        return global_setting.get_setting("port", None)

    def _get_active_channels(self):
        return list(range(9))

    def _build_calibration_handler(self):
        self.calibration_handler = CO2CalibrationHandler(target_points=self._normalize_target_points())
        self.calibration_handler.start_new_calibration()

    def _read_ugc_channel_snapshot(self, channel):
        port = self._get_port()
        self.send_message = {
            "port": port,
            "data": number_util.set_int_to_4_bytes_list(f"00{int(channel):02X}0005"),
            "slave_id": "3",
            "function_code": "4",
            "timeout": 1,
        }
        self.send_thread.send_message = self.send_message
        result_data, _ = self.send_thread.Send_no_promise()
        if not result_data or "data" not in result_data:
            return None

        data_items = result_data.get("data", [])
        raw_co2 = self._extract_data_value(data_items, "CO2")
        if raw_co2 is None:
            return None

        try:
            raw_co2 = self._normalize_co2_to_ppm(raw_co2)
        except Exception:
            return None

        return {"raw_co2_ppm": raw_co2}

    def _read_zos_pressure(self, channel):
        port = self._get_port()
        self.send_message = {
            "port": port,
            "data": number_util.set_int_to_4_bytes_list(f"00{int(channel):02X}000E"),
            "slave_id": "4",
            "function_code": "4",
            "timeout": 1,
        }
        self.send_thread.send_message = self.send_message
        result_data, _ = self.send_thread.Send_no_promise()
        if not result_data or "data" not in result_data:
            return None

        data_items = result_data.get("data", [])
        pressure = self._extract_data_value(data_items, "气体压力")
        oxygen = self._extract_data_value(data_items, "氧浓度")
        try:
            pressure = float(pressure)
        except Exception:
            pressure = None
        try:
            oxygen = float(oxygen)
            if oxygen > 100:
                oxygen = oxygen / 100
        except Exception:
            oxygen = None
        return {"pressure": pressure, "oxygen": oxygen}

    def _calculate_pressure_compensated_co2(self, raw_co2_ppm, gas_pressure):
        if raw_co2_ppm is None or gas_pressure in (None, 0, 0.0):
            return None
        try:
            standard_pressure = float(
                global_setting.get_setting("UFC_UGC_ZOS_config")["PARAM"]["standard_atmospheric_pressure"]
            )
        except Exception:
            standard_pressure = 1013.25
        return round(float(raw_co2_ppm) * standard_pressure / float(gas_pressure), 4)

    def start(self, resolve, reject):
        if self._get_port() is None:
            reject("未选择串口，无法执行运行前 CO2 Air 空气校准")
            return
        start_time_text = time_util.get_format_from_time(time.time())
        self._send_state("set_start_air_calibration_time", start_time_text)
        self._send_text(f"{self.name}开始")
        resolve()

    def wait_air_calibration_prepare(self, resolve, reject):
        wait_seconds = self._normalize_wait_seconds()
        if wait_seconds == 0:
            resolve()
            return

        for second in range(wait_seconds):
            if second == 0 or (second + 1) % 5 == 0 or second == wait_seconds - 1:
                self._send_text(f"{self.name}等待稳定：当前等待 {second + 1}/{wait_seconds} 秒")
            time.sleep(1)
        resolve()

    def run(self, resolve, reject):
        if self._get_port() is None:
            reject("未选择串口，无法执行运行前 CO2 Air 空气校准")
            return

        active_channels = self._get_active_channels()
        if not active_channels:
            reject("未配置有效通道，无法执行运行前 CO2 Air 空气校准")
            return

        try:
            self._build_calibration_handler()
        except Exception as e:
            reject(str(e))
            return

        sample_interval = self._normalize_sample_interval()
        target_points = self._normalize_target_points()
        max_timeout = self._normalize_run_timeout(target_points, sample_interval)
        last_progress_log_time = 0.0
        start_time = time.time()
        co2_failure_notified = False

        self._send_text(f"{self.name}开始采集 9 路 CO2 数据并执行拟合，目标点数 {target_points}")

        while True:
            elapsed_time = time.time() - start_time
            if elapsed_time > max_timeout:
                reject(f"{self.name}超时，已运行 {int(elapsed_time)} 秒")
                return

            for channel in active_channels:
                ugc_snapshot = self._read_ugc_channel_snapshot(channel)
                zos_snapshot = self._read_zos_pressure(channel)
                if ugc_snapshot is None or zos_snapshot is None:
                    logger.warning(f"{self.name}读取 {self._channel_to_display_name(channel)} 数据失败")
                    continue

                compensated_co2 = self._calculate_pressure_compensated_co2(
                    ugc_snapshot.get("raw_co2_ppm"),
                    zos_snapshot.get("pressure"),
                )
                if compensated_co2 is None:
                    continue

                self.push_calibration_values_to_ui(
                    carbon_value=compensated_co2,
                    oxygen_pressure_value=zos_snapshot.get("pressure"),
                )

                try:
                    finished = self.calibration_handler.add_data(
                        self._channel_to_handler_name(channel),
                        compensated_co2,
                    )
                except Exception as e:
                    logger.exception(f"{self.name} CO2 拟合异常（非阻断）")
                    self.calibration_handler.mark_failed(
                        f"{type(e).__name__}: {e}"
                    )
                    finished = False

                if finished:
                    self._send_text(f"{self.name}判定完成，CO2 拟合系数已输出")
                    resolve()
                    return

                status = self.calibration_handler.get_status()
                if status.get("failed") and status.get("all_ready"):
                    if not co2_failure_notified:
                        failure_reason = status.get("failure_reason") or "拟合失败"
                        logger.warning(
                            f"{self.name} CO2 拟合失败（非阻断）：{failure_reason}；"
                            "CO2 配置保持不变，继续后续流程"
                        )
                        self._send_text(
                            f"{self.name}提示：已收满 {target_points} 点，但 CO2 拟合失败（{failure_reason}）；"
                            "保留原有 CO2 配置，不影响后续流程"
                        )
                        co2_failure_notified = True
                    resolve()
                    return

            current_time = time.time()
            if current_time - last_progress_log_time >= 5:
                status = self.calibration_handler.get_status()
                counts = status.get("current_counts", {})
                progress_parts = [f"REF={counts.get('REF', 0)}/{target_points}"]
                for idx in range(1, 9):
                    progress_parts.append(f"M{idx}={counts.get(f'M{idx}', 0)}/{target_points}")
                self._send_text(
                    f"{self.name}采集中：{'，'.join(progress_parts)}，已运行 {int(elapsed_time)}/{int(max_timeout)} 秒"
                )
                last_progress_log_time = current_time

            time.sleep(sample_interval)

    def stop(self, resolve, reject):
        stop_time_text = time_util.get_format_from_time(time.time())
        self._send_state("set_stop_air_calibration_time", stop_time_text)
        self._send_text(f"{self.name}结束")
        resolve()
