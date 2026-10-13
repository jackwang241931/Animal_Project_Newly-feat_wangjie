import copy
import time

from blinker.base import _PNamespaceSignal
from loguru import logger

from Service.UFC_UGC_ZOS_Service.function.Send_Message.Send_Message import Send_Message
from Service.UFC_UGC_ZOS_Service.function.calibration_channels import (
    get_enabled_calibration_channels,
    hardware_channel_to_handler_name,
)
from public.config_class.global_setting import global_setting
from public.entity.enum.Public_Enum import GapSystem_Running_Type
from public.function.promise.AsyPromise import AsyPromise
from public.util.number_util import number_util
from public.util.time_util import time_util
from Service.UFC_UGC_ZOS_Service.function.o2_compensation.host_wet_o2_guard import (
    WetOxygenReferenceFilter,
)


class Startup_Air_Calibration:
    def __init__(self, title=GapSystem_Running_Type.STARTUP_AIR_CALIBRATION):
        self.title = title
        self.name = "运行前 Air 空气校准"
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
        self.o2_calibration_handler = None
        self.co2_calibration_handler = None
        self.o2_calibration_filter = WetOxygenReferenceFilter()
        self.previous_valid_snapshots = {}
        self.calculation_started_logged = False
        self.o2_failure_notified = False
        self.o2_failure_reason = None
        self._pending_full_o2_handler = None
        self._external_stop_checker = None

    def update(self):
        self.send_thread.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update

    def continue_full_o2_calibration(self, handler, stop_checker=None):
        """Continue the Air phase with the active handler that collected Span data."""
        if handler is None:
            raise ValueError("缺少O2 Span校准处理器")
        status = handler.get_status()
        if not status.get("is_active") or not status.get("need_air"):
            raise ValueError("O2 Span数据未完成，不能进入完整Air空气校准")
        self._pending_full_o2_handler = handler
        self._external_stop_checker = stop_checker

    def clear_continued_o2_calibration(self):
        self._pending_full_o2_handler = None
        self._external_stop_checker = None

    def set_external_stop_checker(self, checker):
        """Attach a stop checker for a standalone runtime Air calibration."""
        self._external_stop_checker = checker

    def _is_external_stop_requested(self):
        checker = self._external_stop_checker
        if not callable(checker):
            return False
        try:
            return bool(checker())
        except Exception:
            logger.exception(f"{self.name}: 检查组合校准停止状态失败")
            return True

    def _send_text(self, text):
        if self.update_status_main_signal_gui_update is not None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | {text}",
                title=self.title,
            )

    def _record_nonblocking_o2_failure(self, reason):
        """Record an O2 calibration failure without stopping the Air sequence."""
        reason = str(reason or "拟合失败")
        self.o2_failure_reason = reason
        if self.o2_failure_notified:
            return

        self.o2_failure_notified = True
        logger.warning(
            f"{self.name} O2 校准失败（非阻断）：{reason}；"
            "保留原有 O2 配置，继续执行 Air 校准"
        )
        self._send_text(
            f"{self.name}提示：O2 校准失败（{reason}）；"
            "保留原有 O2 配置，继续采集其它数据，不终止 Air 校准"
        )

    def _send_state(self, state_type, value):
        if self.update_status_main_signal_gui_update is not None:
            self.update_status_main_signal_gui_update.send(
                {"type": state_type, "value": value},
                title=self.title,
            )

    def push_calibration_values_to_ui(self, oxygen_value=None, carbon_value=None, oxygen_pressure_value=None):
        if oxygen_value is not None:
            self.current_calibration_values["oxygen_value"] = oxygen_value
        if carbon_value is not None:
            self.current_calibration_values["carbon_value"] = carbon_value
        if oxygen_pressure_value is not None:
            self.current_calibration_values["oxygen_pressure_value"] = oxygen_pressure_value

        if self.update_status_main_signal_gui_update is not None:
            self.update_status_main_signal_gui_update.send(
                {
                    "type": "set_calibration_values",
                    "value": copy.deepcopy(self.current_calibration_values),
                },
                title=self.title,
            )

    @staticmethod
    def _is_zero_value(value):
        try:
            return float(value) == 0.0
        except (TypeError, ValueError):
            return False

    def _normalize_snapshot_with_previous(self, channel, snapshot):
        if snapshot is None:
            return None

        fields = (
            "o2_partial",
            "zos_temp",
            "zos_rh",
            "gas_pressure",
            "o2_percent",
            "sht_temp",
            "sht_rh",
        )
        previous_snapshot = self.previous_valid_snapshots.get(channel)
        normalized_snapshot = dict(snapshot)

        for field in fields:
            current_value = normalized_snapshot.get(field)
            if not self._is_zero_value(current_value):
                continue

            previous_value = previous_snapshot.get(field) if previous_snapshot else None
            if previous_value not in (None, 0, 0.0):
                normalized_snapshot[field] = previous_value
                logger.warning(
                    f"{self.name}: channel {channel} field {field} returned 0, fallback to previous value {previous_value}"
                )

        merged_snapshot = dict(previous_snapshot) if previous_snapshot else {}
        for field in fields:
            current_value = normalized_snapshot.get(field)
            if not self._is_zero_value(current_value):
                merged_snapshot[field] = current_value
        if merged_snapshot:
            self.previous_valid_snapshots[channel] = merged_snapshot

        return normalized_snapshot

    @staticmethod
    def _normalize_startup_wait_seconds():
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
            sample_interval = float(calibration_config.get("startup_air_calibration_channel_interval", 2))
        except Exception:
            sample_interval = 2.0
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
    def _normalize_run_timeout(target_points, sample_interval, channel_count):
        calibration_config = global_setting.get_setting("UFC_UGC_ZOS_config", {}).get("Calibration", {})
        # Every channel performs two serial transactions (UGC + ZOS) before
        # the configured interval.  Counting only sample_interval made the
        # 9-channel/120-point run expire around 81 points on real hardware.
        serial_read_budget = 3.0
        default_timeout = max(
            int(target_points * (sample_interval + serial_read_budget) * channel_count + 300),
            300,
        )
        try:
            max_timeout = int(float(calibration_config.get("startup_air_calibration_max_timeout", default_timeout)))
        except Exception:
            max_timeout = default_timeout
        return max(max_timeout, default_timeout)

    @staticmethod
    def _normalize_oxygen_percent(value):
        value = float(value)
        if value > 100:
            value = value / 100
        return value

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
    def _extract_data_value_matching(data_items, predicate):
        for item in data_items or []:
            desc = str(item.get("desc", ""))
            if predicate(desc):
                return item.get("value")
        return None

    @staticmethod
    def _channel_to_handler_name(channel):
        return hardware_channel_to_handler_name(channel)

    @staticmethod
    def _channel_to_display_name(channel):
        if int(channel) == 8:
            return "参考气"
        return f"{int(channel) + 1}号鼠笼"

    def _get_port(self):
        return global_setting.get_setting("port", None)

    def _get_active_channels(self):
        return get_enabled_calibration_channels()

    @staticmethod
    def _reference_first_channels(channels):
        """Poll REF first so each cage CO2 sample has a current-cycle peer."""
        channels = list(channels or [])
        reference_channels = [channel for channel in channels if int(channel) == 8]
        other_channels = [channel for channel in channels if int(channel) != 8]
        return reference_channels + other_channels

    def _build_calibration_handlers(
        self,
        sample_interval=None,
        active_channel_count=None,
        active_channels=None,
    ):
        try:
            from Service.UFC_UGC_ZOS_Service.function.o2_compensation import (
                create_o2_calibration_handler,
            )
            from Service.UFC_UGC_ZOS_Service.function.co2_compensation.co2_calibration_handler import (
                CO2CalibrationHandler,
            )
        except Exception as exc:
            raise RuntimeError(f"加载 Air 校准处理器失败: {exc}")

        target_points = self._normalize_target_points()
        active_channels = (
            list(active_channels)
            if active_channels is not None
            else self._get_active_channels()
        )
        handler_channels = [
            self._channel_to_handler_name(channel)
            for channel in active_channels
        ]
        if not handler_channels:
            raise RuntimeError("空气校准缺少已启用通道")
        continued_handler = self._pending_full_o2_handler
        self._pending_full_o2_handler = None
        if continued_handler is not None:
            status = continued_handler.get_status()
            if not status.get("is_active") or not status.get("need_air"):
                raise RuntimeError("O2 Span校准状态已失效，无法继续完整Air空气校准")
            if set(getattr(continued_handler, "channels", [])) != set(handler_channels):
                raise RuntimeError("O2 Span与Air阶段的已启用通道不一致")
            continued_handler.set_target_points(target_points)
            self.o2_calibration_handler = continued_handler
            self._send_text(
                f"{self.name}继续使用已完成Span采样的O2处理器，Air阶段目标点数 {target_points}"
            )
        else:
            self.o2_calibration_handler = create_o2_calibration_handler(
                target_points=target_points,
                channels=handler_channels,
            )
        self.co2_calibration_handler = CO2CalibrationHandler(target_points=target_points)
        set_co2_channels = getattr(self.co2_calibration_handler, "set_channels", None)
        if callable(set_co2_channels):
            set_co2_channels(handler_channels)
        if sample_interval is not None and active_channel_count is not None:
            channel_gap_window = max(
                float(sample_interval) * max(int(active_channel_count) - 1, 1) + 2.0,
                self.co2_calibration_handler.time_sync_tolerance,
            )
            self.co2_calibration_handler.time_sync_tolerance = channel_gap_window
            logger.info(
                f"{self.name}: CO2 time sync tolerance adjusted to {channel_gap_window:.1f}s "
                f"for {active_channel_count} sequential channels"
            )
        if continued_handler is None:
            self.o2_calibration_handler.start_air_touchup()
        self.o2_calibration_filter = WetOxygenReferenceFilter()
        self.co2_calibration_handler.start_new_calibration()
        self.previous_valid_snapshots = {}
        self.calculation_started_logged = False
        self.o2_failure_notified = False
        self.o2_failure_reason = None

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

    @staticmethod
    def _calculate_pressure_compensated_co2(raw_co2_ppm, gas_pressure):
        if raw_co2_ppm is None or gas_pressure in (None, 0, 0.0):
            return None
        try:
            standard_pressure = float(
                global_setting.get_setting("UFC_UGC_ZOS_config")["PARAM"]["standard_atmospheric_pressure"]
            )
        except Exception:
            standard_pressure = 1013.25
        return round(float(raw_co2_ppm) * standard_pressure / float(gas_pressure), 4)

    def _read_zos_channel_snapshot(self, channel):
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
        o2_partial = self._extract_data_value(data_items, "氧分压")
        gas_pressure = self._extract_data_value(data_items, "气体压力")
        o2_percent = self._extract_data_value(data_items, "氧浓度")
        zos_temp = self._extract_data_value(data_items, "ZOS温度2测量值")
        zos_rh = self._extract_data_value(data_items, "ZOS湿度测量值")
        sht_temp = self._extract_data_value_matching(
            data_items, lambda desc: "SHT" in desc.upper() and "温度" in desc
        )
        sht_rh = self._extract_data_value_matching(
            data_items,
            lambda desc: "SHT" in desc.upper() and ("湿度" in desc or "RH" in desc.upper()),
        )

        if None in [o2_partial, gas_pressure, o2_percent, zos_temp, zos_rh]:
            return None

        o2_percent = self._normalize_oxygen_percent(o2_percent)
        return {
            "o2_partial": float(o2_partial),
            "zos_temp": float(zos_temp),
            "zos_rh": float(zos_rh),
            "gas_pressure": float(gas_pressure),
            "o2_percent": float(o2_percent),
            "sht_temp": float(sht_temp) if sht_temp is not None else None,
            "sht_rh": float(sht_rh) if sht_rh is not None else None,
        }

    def start(self, resolve, reject):
        port = self._get_port()
        if port is None:
            reject("未选择串口，无法执行运行前 Air 空气校准")
            return

        start_time_text = time_util.get_format_from_time(time.time())
        self._send_state("set_start_air_calibration_time", start_time_text)
        self._send_text(f"{self.name}开始：发送 ZOS Air 校准开始指令")
        self.send_message = {
            "port": port,
            "data": number_util.set_int_to_4_bytes_list("0009FF00"),
            "slave_id": "4",
            "function_code": "5",
            "timeout": 1,
        }
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            lambda _: resolve()
        ).catch(lambda e: reject(e))

    def wait_air_calibration_prepare(self, resolve, reject):
        wait_seconds = self._normalize_startup_wait_seconds()
        if wait_seconds == 0:
            self._send_text(f"{self.name}等待阶段已跳过")
            resolve()
            return

        for second in range(wait_seconds):
            if self._is_external_stop_requested():
                reject(f"{self.name}已收到停止请求")
                return
            if second == 0 or (second + 1) % 5 == 0 or second == wait_seconds - 1:
                self._send_text(
                    f"{self.name}等待稳定：当前等待 {second + 1}/{wait_seconds} 秒"
                )
            time.sleep(1)
        resolve()

    def run(self, resolve, reject):
        port = self._get_port()
        if port is None:
            reject("未选择串口，无法执行运行前 Air 空气校准")
            return

        active_channels = self._reference_first_channels(self._get_active_channels())
        if len(active_channels) == 0:
            reject("未配置有效通道，无法执行运行前 Air 空气校准")
            return

        sample_interval = self._normalize_sample_interval()
        target_points = self._normalize_target_points()
        try:
            self._build_calibration_handlers(
                sample_interval=sample_interval,
                active_channel_count=len(active_channels),
                active_channels=active_channels,
            )
        except Exception as exc:
            reject(str(exc))
            return
        max_timeout = self._normalize_run_timeout(target_points, sample_interval, len(active_channels))
        last_progress_log_time = 0.0
        start_time = time.time()
        co2_failure_notified = False

        self._send_text(
            f"{self.name}开始采集 {len(active_channels)} 路 O2 / CO2 数据并行校准，"
            f"每 {sample_interval:g} 秒读取 1 个通道，目标点数 {target_points}"
        )

        channel_index = 0
        while True:
            if self._is_external_stop_requested():
                reject(f"{self.name}已收到停止请求")
                return
            elapsed_time = time.time() - start_time
            if elapsed_time > max_timeout:
                reject(f"{self.name}超时，已运行 {int(elapsed_time)} 秒")
                return

            channel = active_channels[channel_index]
            display_name = self._channel_to_display_name(channel)
            co2_snapshot = self._read_ugc_channel_snapshot(channel)

            snapshot = self._read_zos_channel_snapshot(channel)
            if snapshot is not None:
                snapshot = self._normalize_snapshot_with_previous(channel, snapshot)
            if snapshot is None and co2_snapshot is None:
                logger.warning(f"{self.name}读取 {display_name} O2 / CO2 数据失败")
            else:
                if snapshot is not None:
                    self.push_calibration_values_to_ui(
                        oxygen_value=snapshot["o2_percent"],
                        oxygen_pressure_value=snapshot["gas_pressure"],
                    )
                    channel_name = self._channel_to_handler_name(channel)
                    o2_filter_result = self.o2_calibration_filter.filter_with_status(
                        channel_name,
                        snapshot["o2_percent"],
                    )
                    cleaned_o2_percent = o2_filter_result["value"]
                    if cleaned_o2_percent is None:
                        logger.warning(
                            f"{self.name}跳过 {display_name} 本次 O2 点："
                            "首次采样没有可用参考值"
                        )
                    else:
                        if self.o2_failure_reason is None:
                            try:
                                self.o2_calibration_handler.add_data(
                                    channel_name,
                                    snapshot["o2_partial"],
                                    snapshot["zos_temp"],
                                    snapshot["gas_pressure"],
                                    cleaned_o2_percent,
                                    snapshot["zos_rh"],
                                    sht_temp=snapshot.get("sht_temp"),
                                    sht_rh=snapshot.get("sht_rh"),
                                )
                            except Exception as exc:
                                logger.exception(
                                    f"{self.name} O2 CalibrationHandler 异常（非阻断）"
                                )
                                self._record_nonblocking_o2_failure(
                                    f"{type(exc).__name__}: {exc}"
                                )
                else:
                    logger.warning(f"{self.name}读取 {display_name} O2 数据失败")

                if co2_snapshot is not None and snapshot is not None:
                    compensated_co2 = self._calculate_pressure_compensated_co2(
                        co2_snapshot.get("raw_co2_ppm"),
                        snapshot.get("gas_pressure"),
                    )
                    if compensated_co2 is not None:
                        self.push_calibration_values_to_ui(
                            carbon_value=compensated_co2,
                            oxygen_pressure_value=snapshot["gas_pressure"],
                        )
                        try:
                            self.co2_calibration_handler.add_data(
                                self._channel_to_handler_name(channel),
                                compensated_co2,
                            )
                        except Exception as exc:
                            logger.exception(f"{self.name} CO2 拟合异常（非阻断）")
                            self.co2_calibration_handler.mark_failed(
                                f"{type(exc).__name__}: {exc}"
                            )
                elif co2_snapshot is None:
                    logger.warning(f"{self.name}读取 {display_name} CO2 数据失败")
                else:
                    logger.warning(f"{self.name}读取 {display_name} 气压数据失败，CO2 无法补偿")

                o2_status = self.o2_calibration_handler.get_status()
                co2_status = self.co2_calibration_handler.get_status()
                if o2_status.get("failed"):
                    self._record_nonblocking_o2_failure(
                        f"{o2_status.get('failure_reason') or '拟合失败'}"
                    )

                # CO2 拟合失败只影响本次 CO2 系数更新，不阻断 O2 或整轮 Air 校准。
                # CO2CalibrationHandler 只在成功时写入配置，因此失败时会自然保留旧配置。
                if co2_status.get("failed") and not co2_failure_notified:
                    failure_reason = co2_status.get("failure_reason") or "拟合失败"
                    logger.warning(
                        f"{self.name} CO2 拟合失败（非阻断）：{failure_reason}；"
                        "CO2 配置保持不变，继续等待 O2 校准完成"
                    )
                    self._send_text(
                        f"{self.name}提示：CO2 已收满 {target_points} 点，但拟合失败（{failure_reason}）；"
                        "保留原有 CO2 配置，不影响本轮 Air 校准"
                    )
                    co2_failure_notified = True

                co2_ready = all(
                    count >= target_points for count in co2_status.get("current_counts", {}).values()
                )
                if not self.calculation_started_logged and o2_status.get("all_ready") and co2_ready:
                    self.calculation_started_logged = True
                    self._send_text(
                        f"{self.name}全部通道已收满 {target_points} 点，开始计算 O2 / CO2 补偿系数"
                    )

                o2_calibrated = o2_status.get("calibrated", getattr(self.o2_calibration_handler, "calibrated", False))
                co2_calibrated = co2_status.get("calibrated", getattr(self.co2_calibration_handler, "calibrated", False))
                o2_failed_nonblocking = bool(
                    o2_status.get("failed") or self.o2_failure_reason
                )
                co2_failed_nonblocking = bool(co2_status.get("failed"))
                co2_fit_failed_after_collection = co2_failed_nonblocking and co2_ready
                if (o2_calibrated or o2_failed_nonblocking) and (
                    co2_calibrated or co2_failed_nonblocking
                ):
                    if o2_failed_nonblocking and co2_failed_nonblocking:
                        self._send_text(
                            f"{self.name}采集完成：O2 校准失败，CO2 也未更新系数；"
                            "保留原有 O2 / CO2 配置，继续后续流程"
                        )
                    elif o2_failed_nonblocking:
                        self._send_text(
                            f"{self.name}采集完成：O2 校准失败，CO2 校准成功；"
                            "保留原有 O2 配置，继续后续流程"
                        )
                    elif co2_fit_failed_after_collection:
                        self._send_text(
                            f"{self.name}采集完成：O2 校准成功，CO2 已收满 {target_points} 点但拟合失败；"
                            "本轮不更新 CO2 配置，继续后续流程"
                        )
                    elif co2_failed_nonblocking:
                        self._send_text(
                            f"{self.name}采集完成：O2 校准成功，CO2 处理异常且未完成 {target_points} 点；"
                            "本轮不更新 CO2 配置，继续后续流程"
                        )
                    else:
                        self._send_text(f"{self.name}判定完成，O2 / CO2 校准配置已输出")
                    resolve()
                    return

            channel_index = (channel_index + 1) % len(active_channels)

            current_time = time.time()
            if current_time - last_progress_log_time >= 5:
                o2_status = self.o2_calibration_handler.get_status()
                co2_status = self.co2_calibration_handler.get_status()
                o2_counts = o2_status.get("current_counts", {})
                co2_counts = co2_status.get("current_counts", {})
                o2_progress_parts = [f"REF={o2_counts.get('REF', 0)}/{target_points}"]
                co2_progress_parts = [f"REF={co2_counts.get('REF', 0)}/{target_points}"]
                for idx in range(1, 9):
                    channel_name = f"M{idx}"
                    o2_progress_parts.append(f"{channel_name}={o2_counts.get(channel_name, 0)}/{target_points}")
                    co2_progress_parts.append(f"{channel_name}={co2_counts.get(channel_name, 0)}/{target_points}")
                self._send_text(
                    f"{self.name}采集中：O2[{'，'.join(o2_progress_parts)}] | "
                    f"CO2[{'，'.join(co2_progress_parts)}]，已运行 {int(elapsed_time)}/{int(max_timeout)} 秒"
                )
                last_progress_log_time = current_time

            time.sleep(sample_interval)

    def stop(self, resolve, reject):
        port = self._get_port()
        if port is None:
            reject("未选择串口，无法执行运行前 Air 空气校准")
            return

        self._send_text(f"{self.name}结束：发送 ZOS Air 校准结束指令")
        self.send_message = {
            "port": port,
            "data": number_util.set_int_to_4_bytes_list("00090000"),
            "slave_id": "4",
            "function_code": "5",
            "timeout": 1,
        }
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            lambda _: self._finish_stop(resolve)
        ).catch(lambda e: reject(e))

    def _finish_stop(self, resolve):
        stop_time_text = time_util.get_format_from_time(time.time())
        self._send_state("set_stop_air_calibration_time", stop_time_text)
        self._external_stop_checker = None
        resolve()
