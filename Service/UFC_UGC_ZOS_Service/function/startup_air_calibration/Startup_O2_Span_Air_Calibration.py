import threading
import time

from loguru import logger

from Service.UFC_UGC_ZOS_Service.function.Send_Message.Send_Message import Send_Message
from Service.UFC_UGC_ZOS_Service.function.o2_compensation import (
    get_o2_calibration_handler,
    reload_o2_compensation_config,
)
from public.config_class.global_setting import global_setting
from public.entity.enum.Public_Enum import GapSystem_Running_Type
from public.util.number_util import number_util
from public.util.time_util import time_util


class Startup_O2_Span_Air_Calibration:
    """Run the supplied O2 Span-then-Air calibration sequence before experiment sampling."""

    CHANNELS = tuple(range(9))
    SPAN_PURGE_SECONDS = 300
    AIR_PURGE_SECONDS = 300

    def __init__(self, title=GapSystem_Running_Type.STARTUP_AIR_CALIBRATION):
        self.title = title
        self.name = "O2 Span+空气校准"
        self.update_status_main_signal_gui_update = None
        self.send_message = {
            "port": "",
            "data": "",
            "slave_id": 0,
            "function_code": 0,
            "timeout": 1,
        }
        self.send_thread = Send_Message(
            update_status_main_signal_gui_update=None,
            send_message=self.send_message,
            update_status_main_signal_gui_update_type=title,
        )
        self._stop_event = threading.Event()
        self._finished_event = threading.Event()
        self._state_lock = threading.Lock()
        self._running = False
        self._handler = None

    def update(self):
        self.send_thread.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update

    @property
    def is_running(self):
        with self._state_lock:
            return self._running

    def request_stop(self):
        self._stop_event.set()

    def wait_until_finished(self, timeout=None):
        return self._finished_event.wait(timeout)

    def _send_text(self, message):
        logger.info(f"{self.name}: {message}")
        if self.update_status_main_signal_gui_update is not None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | {self.name}：{message}",
                title=self.title,
            )

    @staticmethod
    def _calibration_config():
        config = global_setting.get_setting("UFC_UGC_ZOS_config", {}) or {}
        return config.get("Calibration", {}) or {}

    def _target_points(self):
        raw = self._calibration_config().get("o2_span_air_target_points", 25)
        try:
            return max(5, int(float(raw)))
        except (TypeError, ValueError):
            return 25

    def _sample_interval(self):
        raw = self._calibration_config().get("startup_air_calibration_channel_interval", 2)
        try:
            return max(0.5, float(raw))
        except (TypeError, ValueError):
            return 2.0

    @staticmethod
    def _channel_name(channel):
        return "REF" if channel == 8 else f"M{channel + 1}"

    @staticmethod
    def _extract_value(items, predicate):
        for item in items or []:
            description = str(item.get("desc", ""))
            if predicate(description):
                return item.get("value")
        return None

    def _read_channel(self, port, channel):
        self.send_message = {
            "port": port,
            "data": number_util.set_int_to_4_bytes_list(f"00{channel:02X}000E"),
            "slave_id": "4",
            "function_code": "4",
            "timeout": 1,
        }
        self.send_thread.send_message = self.send_message
        result, _ = self.send_thread.Send_no_promise()
        if not result or "data" not in result:
            return None

        items = result.get("data", [])
        values = {
            "o2_partial": self._extract_value(items, lambda text: "氧分压" in text),
            "gas_pressure": self._extract_value(items, lambda text: "气体压力" in text or "气压" in text),
            "o2_percent": self._extract_value(items, lambda text: "氧浓度" in text),
            "zos_temp": self._extract_value(items, lambda text: "ZOS温度2" in text),
            "zos_rh": self._extract_value(items, lambda text: "ZOS湿度" in text),
            "sht_temp": self._extract_value(
                items, lambda text: "SHT" in text.upper() and "温度" in text
            ),
            "sht_rh": self._extract_value(
                items, lambda text: "SHT" in text.upper() and ("湿度" in text or "RH" in text.upper())
            ),
        }
        required = ("gas_pressure", "o2_percent", "zos_temp", "zos_rh")
        if any(values[key] is None for key in required):
            return None
        try:
            for key, value in values.items():
                if value is not None:
                    values[key] = float(value)
            if values["o2_percent"] > 100:
                values["o2_percent"] /= 100.0
            if values["o2_partial"] is None:
                values["o2_partial"] = 0.0
            return values
        except (TypeError, ValueError, OverflowError):
            return None

    def _send_command(self, slave_id, function_code, data_hex, description):
        port = global_setting.get_setting("port", None)
        if port is None:
            raise RuntimeError("未选择串口")
        self.send_message = {
            "port": port,
            "data": number_util.set_int_to_4_bytes_list(data_hex),
            "slave_id": str(slave_id),
            "function_code": str(function_code),
            "timeout": 1,
        }
        self.send_thread.send_message = self.send_message
        result = {}
        errors = []
        self.send_thread.Send(result.update, errors.append)
        if errors:
            raise RuntimeError(f"{description}失败：{errors[0]}")
        if result.get("send_state") is not True:
            raise RuntimeError(f"{description}未收到设备确认")
        self._send_text(f"{description}完成")

    def _set_coil(self, slave_id, address, enabled, description):
        value = "FF00" if enabled else "0000"
        self._send_command(slave_id, "5", f"{address:04X}{value}", description)

    def _emit_calibration_time(self, event_type):
        if self.update_status_main_signal_gui_update is not None:
            self.update_status_main_signal_gui_update.send(
                {"type": event_type, "value": time_util.get_format_from_time(time.time())},
                title=self.title,
            )

    def _wait_for_gas_flush(self, phase, seconds):
        self._send_text(f"{phase}气体置换等待 {seconds} 秒")
        deadline = time.monotonic() + seconds
        while True:
            if self._stop_event.is_set():
                raise RuntimeError("用户停止校准")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            waited = seconds - int(remaining)
            if waited == 0 or waited % 30 == 0:
                self._send_text(f"{phase}置换中 {waited}/{seconds} 秒")
            self._stop_event.wait(min(1.0, remaining))

    def _sample_phase(self, phase, target_points, sample_interval):
        port = global_setting.get_setting("port", None)
        if port is None:
            raise RuntimeError("未选择串口")
        timeout = max(target_points * sample_interval * len(self.CHANNELS) * 4, 900)
        started = time.monotonic()
        last_log = 0.0

        while True:
            if self._stop_event.is_set():
                raise RuntimeError("用户停止校准")
            for channel in self.CHANNELS:
                if self._stop_event.is_set():
                    raise RuntimeError("用户停止校准")
                sample = self._read_channel(port, channel)
                if sample is not None:
                    self._handler.add_data(
                        channel=self._channel_name(channel),
                        o2_partial=sample["o2_partial"],
                        zos_temp=sample["zos_temp"],
                        gas_pressure=sample["gas_pressure"],
                        o2_percent=sample["o2_percent"],
                        zos_rh=sample["zos_rh"],
                        sht_temp=sample["sht_temp"],
                        sht_rh=sample["sht_rh"],
                    )

                status = self._handler.get_status()
                if phase == "span" and status.get("need_air"):
                    return
                if phase == "air" and status.get("completed"):
                    return

                elapsed = time.monotonic() - started
                if elapsed > timeout:
                    counts = status.get("phase_counts", {}).get(phase, {})
                    raise TimeoutError(
                        f"{phase}采样超时；各通道点数：{counts}"
                    )
                if elapsed - last_log >= 10:
                    counts = status.get("phase_counts", {}).get(phase, {})
                    progress = "，".join(
                        f"{self._channel_name(ch)}={counts.get(self._channel_name(ch), 0)}/{target_points}"
                        for ch in self.CHANNELS
                    )
                    self._send_text(f"{phase}采样进度：{progress}")
                    last_log = elapsed
                self._stop_event.wait(sample_interval)

    def _restore_safe_gas_state(self):
        first_error = None
        for args in (
            (4, 9, False, "退出ZOS校准态"),
            (3, 2, False, "关闭UGC Span阀"),
            (3, 1, False, "关闭UGC N2阀"),
            (3, 0, True, "恢复UGC空气阀"),
        ):
            try:
                self._set_coil(*args)
            except Exception as exc:
                logger.exception(f"{self.name}: 安全收尾命令失败")
                first_error = first_error or exc
        return first_error

    def run(self, resolve, reject):
        with self._state_lock:
            if self._running:
                reject("O2 Span+空气校准正在运行")
                return
            self._running = True
        self._finished_event.clear()
        self._stop_event.clear()
        failure = None
        with_co2 = bool(global_setting.get_setting("startup_calibration_mode") == "co2_o2_span_air")
        try:
            port = global_setting.get_setting("port", None)
            if port is None:
                raise RuntimeError("未选择串口，无法执行O2 Span+空气校准")
            target_points = self._target_points()
            sample_interval = self._sample_interval()
            self._handler = get_o2_calibration_handler(target_points=target_points)
            self._handler.start_new_calibration(target_points)

            global_setting.set_setting("is_calibrating", True)
            global_setting.set_setting("current_calibration_type", self.name)
            self._send_text(
                f"开始压缩包流程；每路目标 {target_points} 点，通道间隔 {sample_interval:g} 秒"
            )

            # Span phase: enable manifold/ZOS calibration mode, then use 20.50% gas.
            self._set_coil(4, 9, True, "切换ZOS校准采样状态")
            self._set_coil(3, 2, True, "开启UGC Span标准气")
            self._set_coil(3, 0, False, "关闭UGC空气阀")
            self._set_coil(3, 1, False, "关闭UGC N2阀")
            self._emit_calibration_time("set_start_span_calibration_time")
            self._wait_for_gas_flush("Span", self.SPAN_PURGE_SECONDS)
            self._sample_phase("span", target_points, sample_interval)
            self._emit_calibration_time("set_stop_span_calibration_time")
            self._send_text("Span采样完成，切换空气阶段")

            # Air phase starts only after every channel has enough Span points.
            self._set_coil(3, 0, True, "开启UGC空气阀")
            self._set_coil(3, 2, False, "关闭UGC Span阀")
            self._set_coil(3, 1, False, "关闭UGC N2阀")
            self._emit_calibration_time("set_start_air_calibration_time")
            self._wait_for_gas_flush("空气", self.AIR_PURGE_SECONDS)
            self._sample_phase("air", target_points, sample_interval)

            status = self._handler.get_status()
            if not status.get("completed"):
                raise RuntimeError("O2校准未完成，配置文件未更新")
            reload_o2_compensation_config()
            self._emit_calibration_time("set_stop_air_calibration_time")
            self._send_text(
                f"Span+空气校准完成，干基空气锁定值 {status.get('air_lock')}%"
            )
        except Exception as exc:
            failure = exc
            logger.exception(f"{self.name}失败")
        finally:
            cleanup_error = self._restore_safe_gas_state()
            global_setting.set_setting("is_calibrating", False)
            global_setting.set_setting("current_calibration_type", None)
            with self._state_lock:
                self._running = False
            self._finished_event.set()
            if cleanup_error is not None and failure is None:
                failure = RuntimeError(f"校准安全收尾失败：{cleanup_error}")

        if failure is not None:
            reject(failure)
        else:
            resolve(True)