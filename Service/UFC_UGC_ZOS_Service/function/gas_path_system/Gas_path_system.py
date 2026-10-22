import abc
import math

import re
import threading

import time
from datetime import datetime
from threading import Event, Barrier

import numpy as np
from blinker.base import _PNamespaceSignal
from loguru import logger


from Service.UFC_UGC_ZOS_Service.function.o2_compensation import (
    calculate_o2_compensated,
    get_reference_dry_oxygen_percent,
    has_valid_reference_dry_oxygen_sample,
)
from Service.UFC_UGC_ZOS_Service.function.o2_compensation.host_wet_o2_guard import (
    WetOxygenAnomalyGuard,
)
from Service.UFC_UGC_ZOS_Service.function.Send_Message.Send_Message import Send_Message

from public.config_class.global_setting import global_setting
from public.dao.SQLite.Monitor_Datas_Handle import Monitor_Datas_Handle
from public.entity.MyQThread import MyQThread
from public.entity.barrier.ActionCompleteBarrier import ActionCompleteBarrier
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.function.Monitor_data_storage.DataStorage import store_data_with_result
from public.function.promise.AsyPromise import AsyPromise
from public.function.Modbus.response_match import matches_ufc_status_response
from public.util.number_util import number_util
from public.util.string_util import String_util
from public.util.time_util import time_util
# 等待ufc启动完
wait_UFC_start_finish_event = threading.Event()
# 等待ufc 停止完
wait_UFC_stop_finish_event = threading.Event()
logger = logger.bind(category="monitor_data_logger")

COLLECTION_STAGE_TIMEOUT_SECONDS = 15.0
COLLECTION_BARRIER_TIMEOUT_SECONDS = 45.0
UFC_FLOW_SETPOINT_DEFAULTS = (870, 870, 990, 800, 940, 870, 810, 920, 920)


def _notify_collection_stage(signal_name, participant):
    signal = global_setting.get_setting(signal_name, None)
    if signal is None:
        logger.critical(
            f"collection stage signal missing: participant={participant}, "
            f"signal={signal_name}"
        )
        return False

    if hasattr(signal, "release"):
        signal.release()
    else:
        signal.set()
    logger.debug(
        f"collection stage notified: participant={participant}, signal={signal_name}"
    )
    return True


def _wait_collection_stage(signal_name, participant):
    signal = global_setting.get_setting(signal_name, None)
    if signal is None:
        logger.critical(
            f"collection stage signal missing: participant={participant}, "
            f"signal={signal_name}"
        )
        return False

    if hasattr(signal, "acquire"):
        completed = signal.acquire(timeout=COLLECTION_STAGE_TIMEOUT_SECONDS)
    else:
        completed = signal.wait(timeout=COLLECTION_STAGE_TIMEOUT_SECONDS)
        if completed:
            signal.clear()

    if not completed:
        logger.critical(
            "collection stage timeout; continuing to avoid deadlock: "
            f"participant={participant}, waiting_for={signal_name}, "
            f"timeout={COLLECTION_STAGE_TIMEOUT_SECONDS:.1f}s"
        )
    return completed


def _wait_collection_barrier(participant):
    barrier = global_setting.get_setting("barrier", None)
    if barrier is None:
        return True
    try:
        barrier.wait(timeout=COLLECTION_BARRIER_TIMEOUT_SECONDS)
        return True
    except threading.BrokenBarrierError as exc:
        logger.critical(
            "collection barrier failed; participant released for recovery: "
            f"participant={participant}, error={exc}"
        )
        return False


class Gas_path_system:
    """
    气路系统 三个气路模块的父类
    """
    def __init__(self):
        # 更新主线程状态栏消息信号
        self.update_status_main_signal_gui_update: _PNamespaceSignal = None

        # 发送的数据结构
        self.send_message = {
            'port': '',
            'data': '',
            'slave_id': 0,
            'function_code': 0,
            'timeout': 0
        }
        # 发送报文线程
        self.send_thread: Send_Message = Send_Message(update_status_main_signal_gui_update=self.update_status_main_signal_gui_update,send_message=self.send_message)
        pass
    def update(self):
        self.send_thread.update_status_main_signal_gui_update=self.update_status_main_signal_gui_update


    @abc.abstractmethod
    def start(self,resolve,reject):
        """
        启动气路
        :return:
        """
        pass
    @abc.abstractmethod
    def run(self,resolve,reject):
        """
        气路运行
        :return:
        """
        pass
    @abc.abstractmethod
    def stop(self,resolve,reject):
        """
        停止气路
        :return:
        """
        pass
class UFC_gas_path_system_start_thread(MyQThread):
    """
    UFC 气路系统开启线程
    """
    def __init__(self,name,update_status_main_signal_gui_update,parent_class):
        #基类
        self.parent_class = parent_class
        #停止
        self.is_stop=False
        # None 表示旧启动流程；步骤0执行后保存 0/1 状态。
        self.startup_status = None
        # 更新主线程状态栏消息信号
        self.update_status_main_signal_gui_update: _PNamespaceSignal = update_status_main_signal_gui_update
        # 发送的数据结构
        self.send_message = {
            'port': '',
            'data': '',
            'slave_id': 0,
            'function_code': 0,
            'timeout': 0
        }
        # 发送报文线程
        self.send_thread: Send_Message = Send_Message(
            update_status_main_signal_gui_update=self.update_status_main_signal_gui_update,
            send_message=self.send_message)
        super().__init__(name=name)

    def set_startup_status(self, startup_status):
        self.startup_status = (
            dict(startup_status) if isinstance(startup_status, dict) else None
        )

    def _get_startup_state(self, name):
        if not isinstance(self.startup_status, dict):
            return None
        value = self.startup_status.get(name)
        try:
            value = int(value)
        except (TypeError, ValueError):
            return None
        return value if value in (0, 1) else None

    def _startup_cancelled(self):
        """Return whether this startup chain is no longer allowed to send commands."""
        return bool(
            self.is_stop
            or global_setting.get_setting("application_shutdown_requested", False)
            or global_setting.get_setting("ufc_emergency_shutdown_active", False)
        )

    def _reject_if_startup_cancelled(self, reject):
        if not self._startup_cancelled():
            return False
        reject("UFC启动已取消")
        return True

    @staticmethod
    def _require_send_success(result, step_name):
        if not isinstance(result, dict) or not result.get("send_state", False):
            raise RuntimeError(f"{step_name}指令发送失败，气路启动已中止")
        return result

    @staticmethod
    def _flow_setpoint_config():
        ufc_config = (global_setting.get_setting("UFC_UGC_ZOS_config", {}) or {}).get("UFC", {})
        setpoints = []
        for channel, default in enumerate(UFC_FLOW_SETPOINT_DEFAULTS, start=1):
            raw_value = ufc_config.get(f"flow_setpoint_channel_{channel}", default)
            try:
                value = int(raw_value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"通道{channel}流量设定值无效: {raw_value!r}") from exc
            if isinstance(raw_value, bool) or not 0 <= value <= 0xFFFF:
                raise ValueError(f"通道{channel}流量设定值无效: {raw_value!r}")
            setpoints.append(value)
        raw_delay = ufc_config.get("flow_setpoint_delay", 2)
        try:
            delay = float(raw_delay)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"流量设定间隔无效: {raw_delay!r}") from exc
        if not math.isfinite(delay) or delay < 0:
            raise ValueError(f"流量设定间隔无效: {raw_delay!r}")
        return setpoints, delay

    @staticmethod
    def _require_flow_setpoint_response(result, channel, value):
        UFC_gas_path_system_start_thread._require_send_success(
            result, f"步骤3.1 通道{channel}流量设定"
        )
        expected = bytes((2, 6, 0, 0x0F + channel, value >> 8, value & 0xFF))
        try:
            response = bytes.fromhex(result.get("response_hex") or "")
        except (TypeError, ValueError) as exc:
            raise RuntimeError(f"步骤3.1 通道{channel}流量设定回包无效") from exc
        if len(response) != 8 or response[:6] != expected:
            raise RuntimeError(
                f"步骤3.1 通道{channel}流量设定回包不匹配: "
                f"期望{expected.hex()}, 收到{response.hex()}"
            )
        return result

    def before_Runing_work(self):
        pass
    def dosomething(self):
        port = global_setting.get_setting("port", None)
        if port is None:
            self._finish_with_error("启动失败，未选择串口！")
            return
        AsyPromise(self.ufc_start).catch(self._finish_with_error)
        self.stop()

    def _finish_with_error(self, error):
        """Record a startup failure and release the caller waiting on the thread."""
        error_message = str(error)
        self.parent_class.ufc_start_error = error_message
        logger.error(f"UFC启动失败: {error_message}")
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UFC启动失败: {error_message}"
        )
        global wait_UFC_start_finish_event
        wait_UFC_start_finish_event.set()
        return None

    def ufc_start(self, resolve, reject):
        # is_stop is reset only by UFC_gas_path_system.start() when a new
        # experiment is explicitly started.  Resetting it here can revive a
        # stale asynchronous callback after the user has stopped the
        # experiment or closed the application.
        if self._reject_if_startup_cancelled(reject):
            return
        time.sleep(0.01)
        if self._reject_if_startup_cancelled(reject):
            return
        ufc_status = self._get_startup_state('ufc_status')
        if ufc_status == 1:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | "
                "UFC 启动-步骤3.UFC已运行，跳过启动指令"
            )
            logger.info("气路启动步骤3跳过：步骤0检测到UFC已运行")
            self._configure_flow_then_start_pump(resolve, reject)
            return

        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | "
            "UFC 启动-步骤3.发送UFC启动指令"
        )
        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！")
            reject("步骤3失败：未选择串口")
            return
        # 步骤3：仅当步骤0读到 ufc_status=0 时启动；未提供状态时兼容旧流程。
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("000b00ff"),
            'slave_id': '2',
            'function_code': '5',
            'timeout': 1
        }
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            lambda result: self._require_send_success(result, "步骤3 UFC启动")
        ).then(
            lambda _: self._configure_flow_then_start_pump(resolve, reject)
        ).catch(reject)

    def _configure_flow_then_start_pump(self, resolve, reject):
        if self._reject_if_startup_cancelled(reject):
            return

        def start_pump(_):
            if self._reject_if_startup_cancelled(reject):
                return
            AsyPromise(self.gas_and_flow_rate_start).then(resolve).catch(reject)

        AsyPromise(self.configure_flow_controllers).then(start_pump).catch(reject)

    def configure_flow_controllers(self, resolve, reject):
        """Set all eight cage channels and the reference channel before the pump starts."""
        if self._reject_if_startup_cancelled(reject):
            return
        port = global_setting.get_setting("port", None)
        if port is None:
            reject("步骤3.1失败：未选择串口")
            return
        try:
            setpoints, delay = self._flow_setpoint_config()
        except ValueError as exc:
            reject(exc)
            return

        def set_channel(index):
            if self._reject_if_startup_cancelled(reject):
                return
            if index == len(setpoints):
                resolve()
                return

            channel = index + 1
            value = setpoints[index]
            payload = f"{0x0F + channel:04x}{value:04x}"
            self.send_thread.send_message = {
                "port": port,
                "data": number_util.set_int_to_4_bytes_list(payload),
                "slave_id": "2",
                "function_code": "6",
                "timeout": 1,
            }
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | "
                f"UFC 启动-步骤3.1.正在设定通道{channel}流量为{value}，寄存器0x{0x0F + channel:04X}"
            )

            def after_send(result):
                self._require_flow_setpoint_response(result, channel, value)
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} | "
                    f"UFC 启动-步骤3.1.通道{channel}流量设定成功：{value}"
                )
                if self._startup_cancelled():
                    raise RuntimeError("Stop")
                time.sleep(delay)
                set_channel(index + 1)

            AsyPromise(self.send_thread.Send).then(after_send).catch(reject)

        set_channel(0)

    def gas_and_flow_rate_start(self, resolve, reject):
        if self._reject_if_startup_cancelled(reject):
            return
        time.sleep(0.01)
        if self._reject_if_startup_cancelled(reject):
            return
        air_pump_status = self._get_startup_state('air_pump')
        if air_pump_status == 1:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | "
                "UFC 启动-步骤4.气泵已打开，跳过开启指令"
            )
            logger.info("气路启动步骤4跳过：步骤0检测到气泵已打开")
            AsyPromise(self.wait_flow_config_auto_config).then(
                lambda result: resolve(result)
            ).catch(lambda error: reject(error))
            return

        # 步骤4：仅当步骤0读到 air_pump=0 时开启；未提供状态时兼容旧流程。
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | "
            "UFC 启动-步骤4.发送气泵和流量控制器开启指令"
        )
        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！")
            reject("步骤4失败：未选择串口")
            return
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("000a00ff"),
            'slave_id': '2',
            'function_code': '5',
            'timeout': 1
        }
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            lambda result: self._require_send_success(result, "步骤4 气泵启动")
        ).then(
            lambda _: AsyPromise(self.wait_flow_config_auto_config)
        ).then(
            lambda result: resolve(result)
        ).catch(lambda e: reject(e))
    def wait_flow_config_auto_config(self,resolve, reject):
        """
        UFC-启动 2.1 等待气泵和流量控制器开启  一般60秒
        :param resolve:
        :param reject:
        :return:
        """
        if self._reject_if_startup_cancelled(reject):
            return
        # 等待时间
        time_index = 0
        while time_index < float(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['wait_time']):
            if self._reject_if_startup_cancelled(reject):
                return
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | UFC-启动 步骤5.等待气泵和流量控制器稳定，此过程需{time_index}/{float(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['wait_time'])}秒")
            time_index += 1
            time.sleep(float(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['wait_time_delay']))
        AsyPromise(self.finsh_start).then(
            lambda r: resolve(r)
        ).catch(lambda e: reject(e))

    def finsh_start(self,resolve, reject):
        if self.is_stop:
            reject("Stop")
            return
        self.parent_class.ufc_start_time_state = True
        global_setting.set_setting("ufc_start_time_state", True)
        # logger.critical(f"ufc_finish_start:{self.parent_class.ufc_start_time_state}")
        # 释放 正在等待ufc启动的地方
        global wait_UFC_start_finish_event
        wait_UFC_start_finish_event.set()

        resolve()
class UFC_gas_path_system_close_thread(MyQThread):
    """
    UFC 气路系统关闭线程
    """
    def __init__(self,name,update_status_main_signal_gui_update):
        # 更新主线程状态栏消息信号
        self.update_status_main_signal_gui_update: _PNamespaceSignal = update_status_main_signal_gui_update
        # 发送的数据结构
        self.send_message = {
            'port': '',
            'data': '',
            'slave_id': 0,
            'function_code': 0,
            'timeout': 0
        }
        # 发送报文线程
        self.send_thread: Send_Message = Send_Message(
            update_status_main_signal_gui_update=self.update_status_main_signal_gui_update,
            send_message=self.send_message)
        self.close_error = None
        # Optional shutdown hook injected by the service coordinator.  The
        # documented order is pump off -> wait -> air valve off -> UFC off.
        self.before_close_ufc = None
        super().__init__(name=name)
    def before_Runing_work(self):
        pass
    def dosomething(self):
        port = global_setting.get_setting("port", None)
        if port is None:
            self._finish_close_failure("未选择串口，无法关闭UFC")
            return
        AsyPromise(self.close_Gas_flow_rate_valve, port=port).then(
            lambda _: self.stop()
        ).catch(self._finish_close_failure)

    def _finish_close_failure(self, error):
        self.close_error = str(error)
        self.before_close_ufc = None
        logger.error(f"UFC关泵/关闭未确认，请检查实体气泵：{error}")
        global wait_UFC_stop_finish_event
        wait_UFC_stop_finish_event.set()
        self.stop()

    def close_Gas_flow_rate_valve(self,resolve,reject,port):
        """1.气泵及设定鼠笼流量控制器关闭（新流程：直接关气泵，无需先关鼠笼和ZOS采样阀）"""
        time.sleep(0.01)
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"000A0000"),
            'slave_id': '2',
            'function_code': '5',
            'timeout': 1,
            'require_success': True,
            'require_matching_response': True,
        }
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UFC-停止 1.气泵及设定鼠笼流量控制器关闭")
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(
                ObjectQueueItem(origin="Gas_path_system", to="MainWindow_index",
                                title="stop_show_info_except_status_counts",
                                data=f"{time_util.get_format_from_time(time.time())} | UFC-停止 1.气泵及设定鼠笼流量控制器关闭",
                                time=time_util.get_format_from_time(time.time())))
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            lambda _: AsyPromise(self.close_UFC_valve, port=port)
        ).then(
            lambda _: resolve()
        ).catch(reject)
    def close_UFC_valve(self,resolve,reject,port):
        """Wait for flow shutdown, close the air valve hook, then close UFC."""
        queue = global_setting.get_setting("queue", None)
        # 等待时间
        time_index = 0
        while time_index < float(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['wait_time']):
            if queue:
                queue.put(
                    ObjectQueueItem(origin="Gas_path_system", to="MainWindow_index", title="stop_show_info_except_status_counts",
                                    data=f"{time_util.get_format_from_time(time.time())} | UFC-停止 1.1 等待气泵及设定鼠笼流量控制器关闭，此过程需{time_index}/{float(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['wait_time'])}秒，等待流量控制器自动关闭",
                                    time=time_util.get_format_from_time(time.time())))
            time_index += 1
            time.sleep(float(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['wait_time_delay']))
        try:
            self._read_ufc_shutdown_status()
        except Exception as error:
            reject(f"关泵状态未确认，未发送0B=00：{error}")
            return

        hook = self.before_close_ufc
        if callable(hook):
            AsyPromise(hook).then(
                lambda _: self._send_close_ufc_command(resolve, reject, port)
            ).catch(reject)
            return
        self._send_close_ufc_command(resolve, reject, port)

    def _read_ufc_shutdown_status(self):
        """Confirm the reported pump state before disabling UFC control."""
        modbus = self.send_thread.modbus
        serial_lock = global_setting.get_setting("serial_lock", None)
        if modbus is None or serial_lock is None:
            raise RuntimeError("串口或通信锁不可用")

        last_error = "未收到有效状态回包"
        for attempt in range(1, 4):
            with serial_lock:
                response, response_hex, send_state, _ = modbus.send_command(
                    slave_id="2",
                    function_code="1",
                    data_hex_list=number_util.set_int_to_4_bytes_list("C"),
                    is_parse_response=False,
                )
                if not send_state or not matches_ufc_status_response(response):
                    last_error = f"状态回包无效：{response_hex}"
                else:
                    parsed, _ = modbus.parse_response(
                        response=response,
                        response_hex=response.hex(),
                        send_state=True,
                        slave_id="2",
                        function_code="1",
                    )
                    pump_state = None
                    if isinstance(parsed, dict):
                        for item in parsed.get("data") or []:
                            if isinstance(item, dict) and item.get("desc") == "气泵":
                                try:
                                    pump_state = int(item.get("value"))
                                except (TypeError, ValueError):
                                    pass
                                break
                    logger.warning(
                        f"UFC停止状态确认 attempt={attempt}/3, "
                        f"response={response_hex}, air_pump={pump_state}"
                    )
                    if pump_state == 0:
                        return
                    last_error = f"气泵状态不是关闭：{pump_state}"
            if attempt < 3:
                time.sleep(0.2)
        raise RuntimeError(last_error)

    def _send_close_ufc_command(self, resolve, reject, port):
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"000B0000"),
            'slave_id': '2',
            'function_code': '5',
            'timeout': 1,
            'require_success': True,
            'require_matching_response': True,
        }
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UFC-停止 2.UFC阀门关闭")
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(
                ObjectQueueItem(origin="Gas_path_system", to="MainWindow_index",
                                title="stop_show_info_except_status_counts",
                                data=f"{time_util.get_format_from_time(time.time())} | UFC-停止 2.UFC阀门关闭",
                                time=time_util.get_format_from_time(time.time())))
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            lambda _:AsyPromise(self.finish_close).then(
                lambda r:resolve()
            ).catch(reject)
        ).catch(reject)

    def finish_close(self,resolve,reject):
        self.before_close_ufc = None
        global_setting.set_setting("ufc_start_time_state", False)
        # 释放 正在等待ufc启动的地方
        global wait_UFC_stop_finish_event
        wait_UFC_stop_finish_event.set()
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UFC 已关闭")
        # 返回响应

        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(
                ObjectQueueItem(origin="Gas_path_system_ufc", to="MainWindow_index", title="stop_ufc_gap_system_return",
                                data=f"{time_util.get_format_from_time(time.time())} | UFC 已关闭",
                                time=time_util.get_format_from_time(time.time())))
        resolve()
class UFC_gas_path_system_run_thread(MyQThread):
    """
    UFC 气路系统运行线程
    """
    def __init__(self,name,update_status_main_signal_gui_update):
        # 更新主线程状态栏消息信号
        self.update_status_main_signal_gui_update: _PNamespaceSignal = update_status_main_signal_gui_update
        # 发送的数据结构
        self.send_message = {
            'port': '',
            'data': '',
            'slave_id': 0,
            'function_code': 0,
            'timeout': 0
        }
        # 发送报文线程
        self.send_thread: Send_Message = Send_Message(
            update_status_main_signal_gui_update=self.update_status_main_signal_gui_update,
            send_message=self.send_message)

        super().__init__(name=name)
    def before_Runing_work(self):
        pass
    def dosomething(self):
        mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
        port = global_setting.get_setting("port", None)
        if port is None:
            logger.error("UFC运行失败，未选择串口！")
            return

        # time.sleep(int(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['run_time']))
        mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
        mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
        cage_addr = mouse_cages_inc[mouse_cage_index] - 1 if mouse_cage_index is not None else 8
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"00{cage_addr}0002"),
            'slave_id': '2',
            'function_code': '4',
            'timeout': 1
        }
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UFC-运行 3. 读取流量值")
        self.send_thread.send_message = self.send_message
        result_data, message = self.send_thread.Send_no_promise()

        result_data['time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        result_data['mouse_cage_number'] = mouse_cages_inc[mouse_cage_index] if mouse_cage_index is not None else int(global_setting.get_setting('configer')['mouse_cage']['reference'])
        logger.error(f"---------------UFC流量值：{result_data}")
        result = store_data_with_result(result_data, need_result=True, timeout=5)
        if result and result.success:
            logger.info(f"数据存储成功，ID: {result.item_id}")
        else:
            logger.error(f"数据存储失败: {result.error if result else '未知错误'}")
        # 让ugc开始运行
        _notify_collection_stage("wait_UFC_run_finish_event", "UFC")
        logger.debug("barrier_UFC run one batch done ! ")
        _wait_collection_barrier("UFC")
        time.sleep(float(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['run_time_delay']))

    def read_flow_rate_value_circulation(self, resolve, reject, port, mouse_cages_inc):
        """
        读取流量值
        """
        # time.sleep(int(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['run_time']))
        mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
        mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
        cage_addr = mouse_cages_inc[mouse_cage_index] - 1 if mouse_cage_index is not None else 8
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"00{cage_addr}0002"),
            'slave_id': '2',
            'function_code': '4',
            'timeout': 1
        }
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UFC-运行 3. 读取流量值")
        self.send_thread.send_message = self.send_message
        result_data, message = self.send_thread.Send_no_promise()

        result_data['time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        result_data['mouse_cage_number'] = mouse_cages_inc[mouse_cage_index] if mouse_cage_index is not None else int(global_setting.get_setting('configer')['mouse_cage']['reference'])
        logger.error(f"---------------UFC流量值：{result_data}")
        result = store_data_with_result(result_data, need_result=True, timeout=5)
        if result and result.success:
            logger.info(f"数据存储成功，ID: {result.item_id}")
        else:
            logger.error(f"数据存储失败: {result.error if result else '未知错误'}")

        AsyPromise(self.finsh_one_batch, port=port, mouse_cages_inc=mouse_cages_inc).then(
            lambda r: resolve()
        ).catch(lambda e: logger.error(f"{e}"))

    def finsh_one_batch(self,resolve,reject,port,mouse_cages_inc):
        """
        完成一轮次后做的事情
        :param resolve:
        :param reject:
        :param port:
        :param mouse_cages_inc:
        :return:
        """


        # # 让鼠笼内的传感器开始运行 要等待ufc ugc zos 都wait
        # ufc_ugc_zos_barrier=global_setting.get_setting("ufc_ugc_zos_barrier")
        # if ufc_ugc_zos_barrier is not None :
        #     logger.debug(f"ufc_ugc_zos_barrier_UFC run one batch done ! ")
        #     ufc_ugc_zos_barrier.wait()
        logger.debug("barrier_UFC run one batch done ! ")
        _wait_collection_barrier("UFC-circular-read")
        time.sleep(float(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['run_time_delay']))
        resolve()
class UFC_gas_path_system(Gas_path_system):
    """
    UFC 气路系统
    """
    # UFC开始的操作数
    process_nums =3+1
    def __init__(self):
        super().__init__()
        self.ufc_start_time_state = False
        self.ufc_start_error = None
        self.startup_status = None


        #开启线程
        self.ufc_gas_path_system_start_thread = UFC_gas_path_system_start_thread(
            name="UFC_gas_path_system_start_thread",
            update_status_main_signal_gui_update=self.update_status_main_signal_gui_update,
            parent_class=self

        )
        #运行线程
        self.ufc_gas_path_system_run_thread = UFC_gas_path_system_run_thread(
            name="UFC_gas_path_system_run_thread",
            update_status_main_signal_gui_update=self.update_status_main_signal_gui_update,

        )
        #关闭线程
        self.ufc_gas_path_system_close_thread = UFC_gas_path_system_close_thread(
            name="UFC_gas_path_system_close_thread",
            update_status_main_signal_gui_update=self.update_status_main_signal_gui_update,

        )
        pass

    def set_startup_status(self, startup_status):
        """保存步骤0结果，供步骤3和步骤4使用。"""
        if not isinstance(startup_status, dict):
            raise ValueError("UFC启动状态必须是字典")
        ufc_status = int(startup_status.get('ufc_status'))
        air_pump = int(startup_status.get('air_pump'))
        if ufc_status not in (0, 1) or air_pump not in (0, 1):
            raise ValueError(
                f"UFC启动状态无效: ufc_status={ufc_status}, air_pump={air_pump}"
            )
        self.startup_status = {
            'ufc_status': ufc_status,
            'air_pump': air_pump,
        }
    def update(self):
        super().update()
        # 开启线程
        self.ufc_gas_path_system_start_thread.update_status_main_signal_gui_update=self.update_status_main_signal_gui_update
        self.ufc_gas_path_system_start_thread.send_thread.update_status_main_signal_gui_update=self.update_status_main_signal_gui_update
        self.ufc_gas_path_system_run_thread.update_status_main_signal_gui_update=self.update_status_main_signal_gui_update
        self.ufc_gas_path_system_run_thread.send_thread.update_status_main_signal_gui_update=self.update_status_main_signal_gui_update
        self.ufc_gas_path_system_close_thread.update_status_main_signal_gui_update=self.update_status_main_signal_gui_update
        self.ufc_gas_path_system_close_thread.send_thread.update_status_main_signal_gui_update=self.update_status_main_signal_gui_update


    """start start"""
    def start(self,resolve,reject):
        """
        启动气路
        :return:
        """
        time.sleep(0.01)
        if (
                global_setting.get_setting("application_shutdown_requested", False)
                or global_setting.get_setting("ufc_emergency_shutdown_active", False)
        ):
            reject("应用正在关闭，禁止重新启动UFC气路")
            return
        # A new experiment is the only valid place to clear the previous
        # experiment's stop request.
        self.ufc_gas_path_system_start_thread.is_stop = False
        self.update_status_main_signal_gui_update.send(f"{time_util.get_format_from_time(time.time())} | UFC 开始启动{'.'*100}")

        # dlg = RunningCagesDialog(None, total_cages=8)
        # data = ""
        # if dlg.exec() == QDialog.DialogCode.Accepted:
        #     res = dlg.result_data
        #     if res['all_selected']:
        #         # 全部选择
        #         data = "11111111"
        #         pass
        #     else:
        #         for i in range(8):
        #             if i in res['selected_indices']:
        #                 data += "1"
        #             else:
        #                 data += "0"
        #         pass
        # global_setting.set_setting("mouse_cages", res['selected_indices'])
        experiment_settings = global_setting.get_setting("experiment_setting",None)

        gids = [group.id for group in experiment_settings.groups] if experiment_settings is not None else []
        global_setting.set_setting("mouse_cages",gids)
        # global_setting.set_setting("mouse_cages_2byte_str",data)
        global_setting.set_setting("mouse_cages_2byte_str", String_util.array_to_binary_string(gids))

        global wait_UFC_start_finish_event
        wait_UFC_start_finish_event.clear()
        self.ufc_start_error = None
        startup_status = self.startup_status
        # 步骤0结果只用于本次启动，避免后续其他启动模式误用旧状态。
        self.startup_status = None
        self.ufc_gas_path_system_start_thread.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update
        self.ufc_gas_path_system_start_thread.set_startup_status(startup_status)
        self.ufc_gas_path_system_start_thread.start()
        # 等待开始线程 把ufc启动完成
        wait_UFC_start_finish_event.wait()
        if self.ufc_start_error is not None:
            reject(self.ufc_start_error)
            return
        resolve()

    """start end"""

    """run start"""
    def run(self,resolve,reject):
        time.sleep(0.01)
        self.update_status_main_signal_gui_update.send(f"{time_util.get_format_from_time(time.time())} | UFC 开始运行{'.'*100}")
        AsyPromise(self.run_no_circulation_read).then(lambda r: resolve(r)).catch(lambda e: logger.error(f"{e}"))
    def run_no_circulation_read(self,resolve,reject):
        """
        气路运行不读取数据（新流程：无需打开ZOS采样阀，直接完成）
        :param resolve:
        :param reject:
        :return:
        """
        time.sleep(0.01)
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UFC 开始运行(不读取数据){'.' * 100}")
        AsyPromise(self.circular_running).then(
            lambda _: resolve()
        ).catch(lambda e: logger.error(f"{e}"))
    def circular_running(self,resolve,reject):
        # 循环运行
        time.sleep(0.01)
        self.ufc_gas_path_system_run_thread.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update
        self.ufc_gas_path_system_run_thread.start()


        resolve()
        pass
    """run end"""
    def stop(self,resolve,reject):
        """
        停止气路
        :return:
        """
        port = global_setting.get_setting("port", None)
        if port is None:
            reject("未选择串口，无法确认UFC关闭")
            return
        time.sleep(0.01)
        self.update_status_main_signal_gui_update.send(f"{time_util.get_format_from_time(time.time())} | UFC 正在停止{'.'*100}")
        if self.ufc_gas_path_system_run_thread is not None:
            self.ufc_gas_path_system_run_thread.stop()
            self.ufc_gas_path_system_run_thread.deleteLater()
            self.ufc_gas_path_system_run_thread=None
        if self.ufc_gas_path_system_start_thread is not None:
            self.ufc_gas_path_system_start_thread.is_stop=True
            self.ufc_gas_path_system_start_thread.stop()
            self.ufc_gas_path_system_start_thread.deleteLater()
            self.ufc_gas_path_system_start_thread=None
        close_thread = self.ufc_gas_path_system_close_thread
        if close_thread is None:
            reject("UFC关闭线程不存在")
            return
        global wait_UFC_stop_finish_event
        if not close_thread.isRunning():
            close_thread.close_error = None
            wait_UFC_stop_finish_event.clear()
            close_thread.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update
            close_thread.start()

        try:
            ufc_config = (global_setting.get_setting("UFC_UGC_ZOS_config", {}) or {}).get("UFC", {})
            wait_time = max(0.0, float(ufc_config.get("wait_time", 60)))
            wait_delay = max(0.01, float(ufc_config.get("wait_time_delay", 1)))
            shutdown_timeout = max(20.0, wait_time * wait_delay + 15.0)
        except (TypeError, ValueError, AttributeError):
            shutdown_timeout = 90.0

        if not wait_UFC_stop_finish_event.wait(shutdown_timeout):
            close_thread.stop()
            reject(f"等待UFC关泵/关阀回执超时（{shutdown_timeout:.0f}秒）")
            return

        wait_UFC_stop_finish_event.clear()
        if close_thread.close_error:
            reject(f"UFC关闭失败：{close_thread.close_error}")
            return
        resolve()
    pass


class UGC_gas_path_system_run_thread(MyQThread):
    """
    UGC 气路系统运行线程
    """

    def __init__(self, name, update_status_main_signal_gui_update):
        # 更新主线程状态栏消息信号
        self.update_status_main_signal_gui_update: _PNamespaceSignal = update_status_main_signal_gui_update

        # 发送的数据结构
        self.send_message = {
            'port': '',
            'data': '',
            'slave_id': 0,
            'function_code': 0,
            'timeout': 0
        }
        # 发送报文线程
        self.send_thread: Send_Message = Send_Message(
            update_status_main_signal_gui_update=self.update_status_main_signal_gui_update,
            send_message=self.send_message)
        self.data_handle = None
        self.last_ugc_channel_read_time = 0.0
        super().__init__(name=name)
    def before_Runing_work(self):
        pass
    @staticmethod
    def _get_data_value(data_items, desc):
        for item in data_items or []:
            if item.get("desc") == desc:
                return item.get("value")
        return None

    @staticmethod
    def _set_data_value(data_items, desc, value):
        for item in data_items or []:
            if item.get("desc") == desc:
                item["value"] = value
                return
        data_items.append({"desc": desc, "value": value})

    @staticmethod
    def _is_zero_value(value):
        try:
            return float(value) == 0.0
        except Exception:
            return value == 0

    def _wait_ugc_channel_read_interval(self):
        min_read_interval = 1.0
        try:
            ugc_config = global_setting.get_setting('UFC_UGC_ZOS_config')['UGC']
            min_read_interval = max(1.0, float(ugc_config.get('min_channel_read_interval', 1)))
        except Exception:
            pass

        elapsed = time.time() - self.last_ugc_channel_read_time
        if elapsed < min_read_interval:
            time.sleep(min_read_interval - elapsed)
        self.last_ugc_channel_read_time = time.time()

    def _replace_zero_ugc_values_with_previous(self, result_data):
        mouse_cage_number = result_data.get("mouse_cage_number")
        if mouse_cage_number is None:
            return result_data

        if self.data_handle is None:
            self.data_handle = Monitor_Datas_Handle()

        previous_data = self.data_handle.query_current_one_data(
            f"UGC_monitor_data_cage_{int(mouse_cage_number)}"
        )
        if not previous_data:
            return result_data

        zero_replace_columns = {
            "气压(KPa)": "air_pressure",
            "CO2(%)": "CO2_num",
        }

        data_items = result_data.get("data", [])
        for desc, column_name in zero_replace_columns.items():
            current_value = self._get_data_value(data_items, desc)
            previous_value = previous_data.get(column_name)
            if self._is_zero_value(current_value) and previous_value not in (None, 0, 0.0):
                self._set_data_value(data_items, desc, previous_value)
                logger.warning(
                    f"UGC运行：{mouse_cage_number}号通道 {desc} 返回0，已使用前值覆盖：{previous_value}"
                )

        return result_data
    def dosomething(self):
        if not _wait_collection_stage("wait_UFC_run_finish_event", "UGC"):
            logger.critical(
                "UGC skipped this collection round because UFC did not finish"
            )
            _wait_collection_barrier("UGC-stage-timeout")
            time.sleep(float(
                global_setting.get_setting('UFC_UGC_ZOS_config')['UGC']['run_time_delay']
            ))
            return
        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！")
            return
        # 4.循环读取CO2浓度
        mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
        mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
        cage_addr = mouse_cages_inc[mouse_cage_index] - 1 if mouse_cage_index is not None else 8
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"000{cage_addr}0005"),
            'slave_id': '3',
            'function_code': '4',
            'timeout': 1
        }

        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UGC-运行 2. 循环读取{'鼠笼'+str(mouse_cages_inc[mouse_cage_index]) if mouse_cage_index is not None else '参考气'}的CO2浓度")
        self.send_thread.send_message = self.send_message
        self._wait_ugc_channel_read_interval()
        result_data, message = self.send_thread.Send_no_promise()
        logger.error(f"ugc:{result_data}")

        result_data['time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        result_data['mouse_cage_number'] = mouse_cages_inc[mouse_cage_index] if mouse_cage_index is not None else int(global_setting.get_setting('configer')['mouse_cage']['reference'])
        result_data = self._replace_zero_ugc_values_with_previous(result_data)
        result = store_data_with_result(result_data, need_result=True, timeout=5)
        if result and result.success:
            logger.info(f"数据存储成功，ID: {result.item_id}")
        else:
            logger.error(f"数据存储失败: {result.error if result else '未知错误'}")

        # 通知zos 运行
        _notify_collection_stage("wait_UGC_run_finish_event", "UGC")
        logger.debug("barrier_UGC run one batch done ! ")
        _wait_collection_barrier("UGC")
        time.sleep(float(global_setting.get_setting('UFC_UGC_ZOS_config')['UGC']['run_time_delay']))
class UGC_gas_path_system(Gas_path_system):
    """
    UGC 气路系统
    """
    # UgC开始的操作数
    process_nums =1
    def __init__(self):
        super().__init__()
        self._air_closed_for_shutdown = False
        # 运行线程
        self.ugc_gas_path_system_run_thread = UGC_gas_path_system_run_thread(
            name="UGC_gas_path_system_run_thread",
            update_status_main_signal_gui_update=self.update_status_main_signal_gui_update,

        )
        pass
    def update(self):
        super().update()
        self.ugc_gas_path_system_run_thread.update_status_main_signal_gui_update=self.update_status_main_signal_gui_update
        self.ugc_gas_path_system_run_thread.send_thread.update_status_main_signal_gui_update=self.update_status_main_signal_gui_update
    """start start"""
    def start(self,resolve,reject):
        """
        启动气路
        :return:
        """

        time.sleep(0.01)
        self.update_status_main_signal_gui_update.send(f"{time_util.get_format_from_time(time.time())} | UGC 正在启动")

        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！")
            reject()
        # 1.打开reference气电磁阀（空气伐）
        self._air_closed_for_shutdown = False
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("0000FF00"),
            'slave_id': '3',
            'function_code': '5',
            'timeout': 1
        }
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UGC 正在启动-1.打开reference气电磁阀（空气伐）")
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            lambda r: AsyPromise(self.read_sensor_status, port=port).then(
                lambda _:resolve()
            ).catch(lambda e: reject(e))
        ).catch(lambda e: reject(e))

        pass

    def read_sensor_status(self, resolve, reject, port):
        """2.读取各路传感器状态（x=0~9，0-7鼠笼，8参考气）"""
        mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
        status_map = {0: "正常", 1: "故障", 2: "超量程", 3: "预热中"}

        # 构建要检查的笼子列表：所有配置的鼠笼 + 参考气
        cages_to_check = []
        if mouse_cages_inc and len(mouse_cages_inc) > 0:
            cages_to_check = [cage - 1 for cage in mouse_cages_inc]
        cages_to_check.append(8)

        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UGC 正在启动-2.读取各路传感器状态")

        # 循环读取每个笼子的状态
        for cage_addr in cages_to_check:
            cage_name = f"{cage_addr + 1}号鼠笼" if cage_addr != 8 else "参考气"

            self.send_message = {
                'port': port,
                'data': number_util.set_int_to_4_bytes_list(f"000{cage_addr}0002"),
                'slave_id': '3',
                'function_code': '2',
                'timeout': 1
            }
            self.send_thread.send_message = self.send_message
            result_data, _ = self.send_thread.Send_no_promise()

            if result_data is not None:
                datas = result_data.get("data", [])
                if datas and len(datas) > 0:
                    for data in datas:
                        desc = data.get("desc")
                        value = data.get("value")
                        if desc == "传感器状态" and value is not None:
                            status_text = status_map.get(int(value), f"未知状态({value})")
                            status_msg = f"{cage_name}传感器状态: {status_text}"

                            logger.info(status_msg)
                            self.update_status_main_signal_gui_update.send(
                                f"{time_util.get_format_from_time(time.time())} | {status_msg}")

                            if int(value) == 1:
                                logger.error(f"{cage_name}传感器故障！")
                            elif int(value) == 2:
                                logger.warning(f"{cage_name}传感器超量程！")
                            elif int(value) == 3:
                                logger.warning(f"{cage_name}传感器预热中...")
                else:
                    logger.warning(f"{cage_name}传感器状态数据为空")
            else:
                logger.error(f"读取{cage_name}传感器状态失败")

            time.sleep(0.01)

        resolve()

        pass
        pass
    """start end"""
    """run start"""
    def run(self,resolve,reject):
        """
        气路运行
        :return:
        """
        self.update_status_main_signal_gui_update.send(f"{time_util.get_format_from_time(time.time())} | UGC 开始运行{'.'*100}")
        time.sleep(0.01)
        AsyPromise(self.circular_running).then(lambda r: resolve(r)).catch(lambda e: reject(e))
        pass

    def circular_running(self, resolve, reject):
        # 循环读取CO2浓度
        time.sleep(0.01)
        self.ugc_gas_path_system_run_thread.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update
        self.ugc_gas_path_system_run_thread.start()
        resolve()
        pass

    """run end"""
    """
    run_no_circulation_read start
    """

    def run_no_circulation_read(self, resolve, reject):
        """
        气路运行不读取数据
        :return:
        """
        time.sleep(0.01)
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UGC 开始运行{'.' * 100}")
        resolve()

    """
    run_no_circulation_read end
    """
    def close_air_valve_for_shutdown(self, resolve, reject):
        """Close the UGC air valve between the pump wait and UFC shutdown."""
        if self._air_closed_for_shutdown:
            resolve()
            return
        port = global_setting.get_setting("port", None)
        if port is None:
            reject("未选择串口，无法关闭UGC空气阀")
            return
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("00000000"),
            'slave_id': '3',
            'function_code': '5',
            'timeout': 1,
            'require_success': True,
        }
        self.send_thread.send_message = self.send_message
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UGC-停止 关闭空气阀"
        )

        def finish(_):
            self._air_closed_for_shutdown = True
            resolve()

        AsyPromise(self.send_thread.Send).then(finish).catch(reject)

    def stop(self, resolve, reject):
        """
        停止气路；若统一停止流程尚未关闭空气阀，则在这里补关。
        :return:
        """
        self.update_status_main_signal_gui_update.send(f"{time_util.get_format_from_time(time.time())} | UGC 正在停止{'.'*100}")
        if self.ugc_gas_path_system_run_thread is not None:
            self.ugc_gas_path_system_run_thread.stop()
            self.ugc_gas_path_system_run_thread.deleteLater()
            self.ugc_gas_path_system_run_thread = None
        AsyPromise(self.close_air_valve_for_shutdown).then(
            lambda _: AsyPromise(self.stop_finished)
        ).then(lambda _: resolve()).catch(lambda e: reject(e))

    pass
    def stop_finished(self,resolve,reject):
        # 返回响应
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(
                ObjectQueueItem(origin="Gas_path_system_ugc", to="MainWindow_index",
                                title="stop_ugc_gap_system_return",
                                data=f"{time_util.get_format_from_time(time.time())} |  UGC 已停止",
                                time=time_util.get_format_from_time(time.time())))

        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UGC 已停止{'.' * 100}")
        resolve()
class ZOS_gas_path_system_run_thread(MyQThread):
    """
    ZOS 气路系统运行线程
    """

    def __init__(self, name, update_status_main_signal_gui_update):
        # 更新主线程状态栏消息信号
        self.update_status_main_signal_gui_update: _PNamespaceSignal = update_status_main_signal_gui_update
        # 发送的数据结构
        self.send_message = {
            'port': '',
            'data': '',
            'slave_id': 0,
            'function_code': 0,
            'timeout': 0
        }
        # 发送报文线程
        self.send_thread: Send_Message = Send_Message(
            update_status_main_signal_gui_update=self.update_status_main_signal_gui_update,
            send_message=self.send_message)
        self.data_handle = None
        self.last_valid_dry_oxygen_values = {}
        self.dry_oxygen_history_checked_channels = set()
        self.dry_oxygen_fallback_warning_channels = set()
        self.last_zos_channel_read_time = 0.0
        o2_config = global_setting.get_setting("UFC_UGC_ZOS_config", {})
        self.wet_o2_guard = WetOxygenAnomalyGuard.from_config(
            o2_config,
            log=lambda message: logger.warning(f"ZOS运行：{message}"),
        )
        self._last_zos_cage_index = None
        self._has_last_zos_cage_index = False
        self._zos_cage_signature = None
        super().__init__(name=name)

    def before_Runing_work(self):
        self.reset_wet_o2_anomaly_guard("ZOS运行启动")

    def reset_wet_o2_anomaly_guard(self, reason="状态切换"):
        self.wet_o2_guard.reset()
        self._last_zos_cage_index = None
        self._has_last_zos_cage_index = False
        self._zos_cage_signature = None
        logger.info(f"ZOS运行：已重置湿基氧异常保护（{reason}，预热{self.wet_o2_guard.warmup_cycles}轮）")

    def _observe_zos_cycle(self, cage_index, mouse_cages):
        """Count full rounds from the actual REF/selected-cage round robin."""
        cage_signature = tuple(mouse_cages or [])
        if cage_signature != self._zos_cage_signature:
            self._zos_cage_signature = cage_signature
            self.wet_o2_guard.reset()
            self._last_zos_cage_index = None
            self._has_last_zos_cage_index = False
            logger.info(
                f"ZOS运行：检测到采集笼列表变化，重置湿基氧异常保护：{cage_signature}"
            )

        previous_index = self._last_zos_cage_index
        if (
            self._has_last_zos_cage_index
            and previous_index is not None
            and cage_index is None
            and len(cage_signature) > 0
            and previous_index == len(cage_signature) - 1
        ):
            cycle = self.wet_o2_guard.complete_cycle()
            logger.debug(f"ZOS运行：湿基氧异常保护完成第{cycle}个完整采集轮次")
        self._last_zos_cage_index = cage_index
        self._has_last_zos_cage_index = True

    def _protect_wet_o2_before_compensation(self, result_data, cage_index, mouse_cages):
        self._observe_zos_cycle(cage_index, mouse_cages)
        mouse_cage_number = result_data.get("mouse_cage_number")
        if mouse_cage_number is None:
            return result_data

        reference_cage_number = int(global_setting.get_setting('configer')['mouse_cage']['reference'])
        if int(mouse_cage_number) == reference_cage_number:
            channel_id = "REF"
        elif 1 <= int(mouse_cage_number) <= 8:
            channel_id = f"M{int(mouse_cage_number)}"
        else:
            return result_data

        data_items = result_data.get("data", [])
        current_value = self._get_data_value(data_items, "氧浓度(%)")
        filtered_value, replaced = self.wet_o2_guard.filter(channel_id, current_value)
        if replaced:
            self._set_data_value(data_items, "氧浓度(%)", filtered_value)
        return result_data

    @staticmethod
    def _get_data_value(data_items, desc):
        for item in data_items or []:
            if item.get("desc") == desc:
                return item.get("value")
        return None

    @staticmethod
    def _set_data_value(data_items, desc, value):
        for item in data_items or []:
            if item.get("desc") == desc:
                item["value"] = value
                return
        data_items.append({"desc": desc, "value": value})

    @staticmethod
    def _is_zero_value(value):
        try:
            return float(value) == 0.0
        except Exception:
            return value == 0

    @staticmethod
    def _is_valid_dry_oxygen_value(value):
        try:
            value = float(value)
            return np.isfinite(value) and 0.0 <= value <= 100.0
        except (TypeError, ValueError):
            return False

    def _get_last_valid_dry_oxygen_value(self, mouse_cage_number):
        cage_number = int(mouse_cage_number)
        cached_value = self.last_valid_dry_oxygen_values.get(cage_number)
        if self._is_valid_dry_oxygen_value(cached_value):
            return round(float(cached_value), 3)
        if cage_number in self.dry_oxygen_history_checked_channels:
            return None

        if self.data_handle is None:
            self.data_handle = Monitor_Datas_Handle()

        table_name = f"ZOS_monitor_data_cage_{cage_number}"
        column_name = "dry_basis_oxygen_num"
        try:
            rows = self.data_handle.sqlite_manager.query_conditions(
                table_name,
                f' WHERE "{column_name}" IS NOT NULL '
                f'ORDER BY time DESC LIMIT 1',
            )
            meta_rows = self.data_handle.sqlite_manager.query(f"{table_name}_meta")
            if rows and meta_rows:
                previous_data = dict(zip([item[0] for item in meta_rows], rows[0]))
                previous_value = previous_data.get(column_name)
                if self._is_valid_dry_oxygen_value(previous_value):
                    previous_value = round(float(previous_value), 3)
                    self.last_valid_dry_oxygen_values[cage_number] = previous_value
                    return previous_value
        except Exception as exc:
            logger.warning(
                f"ZOS运行：{cage_number}号通道读取最近有效干基氧浓度失败：{exc}"
            )
        finally:
            self.dry_oxygen_history_checked_channels.add(cage_number)
        return None

    def _set_or_reuse_dry_oxygen_value(
            self, data_items, mouse_cage_number, compensation_value,
            allow_default=False):
        cage_number = int(mouse_cage_number)
        if self._is_valid_dry_oxygen_value(compensation_value):
            compensation_value = round(float(compensation_value), 3)
            self.last_valid_dry_oxygen_values[cage_number] = compensation_value
            self.dry_oxygen_fallback_warning_channels.discard(cage_number)
            self._set_data_value(data_items, "干基氧浓度(%)", compensation_value)
            return True

        previous_value = self._get_last_valid_dry_oxygen_value(cage_number)
        if previous_value is None:
            if allow_default:
                default_value = round(get_reference_dry_oxygen_percent(), 3)
                self.last_valid_dry_oxygen_values[cage_number] = default_value
                self._set_data_value(data_items, "干基氧浓度(%)", default_value)
                logger.warning(
                    f"ZOS运行：{cage_number}号通道补偿结果异常且无历史有效值，"
                    f"使用初始默认干基氧浓度：{default_value:.3f}"
                )
                return True
            if cage_number not in self.dry_oxygen_fallback_warning_channels:
                logger.warning(
                    f"ZOS运行：{cage_number}号通道本轮无有效REF，且无历史有效干基氧浓度"
                )
                self.dry_oxygen_fallback_warning_channels.add(cage_number)
            return False

        self._set_data_value(data_items, "干基氧浓度(%)", previous_value)
        if cage_number not in self.dry_oxygen_fallback_warning_channels:
            logger.warning(
                f"ZOS运行：{cage_number}号通道本轮无有效REF，"
                f"已沿用最近有效干基氧浓度：{previous_value:.3f}"
            )
            self.dry_oxygen_fallback_warning_channels.add(cage_number)
        return True

    def _wait_zos_channel_read_interval(self):
        min_read_interval = 1.0
        try:
            zos_config = global_setting.get_setting('UFC_UGC_ZOS_config')['ZOS']
            min_read_interval = max(1.0, float(zos_config.get('min_channel_read_interval', 1)))
        except Exception:
            pass

        elapsed = time.time() - self.last_zos_channel_read_time
        if elapsed < min_read_interval:
            time.sleep(min_read_interval - elapsed)
        self.last_zos_channel_read_time = time.time()

    def _replace_zero_zos_values_with_previous(self, result_data):
        mouse_cage_number = result_data.get("mouse_cage_number")
        if mouse_cage_number is None:
            return result_data

        if self.data_handle is None:
            self.data_handle = Monitor_Datas_Handle()

        previous_data = self.data_handle.query_current_one_data(
            f"ZOS_monitor_data_cage_{int(mouse_cage_number)}"
        )
        if not previous_data:
            return result_data

        zero_replace_columns = {
            "氧分压(hPa)": "oxygen_partial_pressure",
            "ZOS温度测量值(°C)": "zos_temperature_num",
            "气体压力(hPa)": "gas_pressure",
            "氧浓度(%)": "oxygen_num",
            "ZOS温度2测量值(°C)": "oxygen_temperature_2_num",
            "ZOS湿度测量值(%RH)": "oxygen_humidity_num",
        }

        data_items = result_data.get("data", [])
        for desc, column_name in zero_replace_columns.items():
            current_value = self._get_data_value(data_items, desc)
            previous_value = previous_data.get(column_name)
            if self._is_zero_value(current_value) and previous_value not in (None, 0, 0.0):
                self._set_data_value(data_items, desc, previous_value)
                logger.warning(
                    f"ZOS运行：{mouse_cage_number}号通道 {desc} 返回0，已使用前值覆盖：{previous_value}"
                )

        return result_data

    def _append_o2_compensation(self, result_data):
        mouse_cage_number = result_data.get("mouse_cage_number")
        if mouse_cage_number is None:
            return result_data

        reference_cage_number = int(global_setting.get_setting('configer')['mouse_cage']['reference'])
        is_reference_cage = int(mouse_cage_number) == reference_cage_number
        if is_reference_cage:
            channel_id = "REF"
        elif 1 <= int(mouse_cage_number) <= 8:
            channel_id = f"M{int(mouse_cage_number)}"
        else:
            return result_data

        data_items = result_data.get("data", [])
        o2_partial_press = self._get_data_value(data_items, "氧分压(hPa)")
        gas_total_press = self._get_data_value(data_items, "气体压力(hPa)")
        o2_raw_pct = self._get_data_value(data_items, "氧浓度(%)")

        zos_temp = self._get_data_value(data_items, "ZOS温度2测量值(°C)")
        zos_rh = self._get_data_value(data_items, "ZOS湿度测量值(%RH)")
        inputs_complete = None not in (
            o2_partial_press,
            zos_temp,
            gas_total_press,
            o2_raw_pct,
            zos_rh,
        )

        compensation_value = -1
        if inputs_complete:
            compensation_value = calculate_o2_compensated(
                channel_id,
                o2_partial_press,
                zos_temp,
                gas_total_press,
                o2_raw_pct,
                zos_rh,
            )

        # 只有REF已经建立真实基线后，普通通道的异常结果才允许使用
        # 最近有效值或初始默认值；没有REF时继续保持None，避免伪造结果。
        allow_default_fallback = (
            not is_reference_cage
            and inputs_complete
            and compensation_value == -1
            and has_valid_reference_dry_oxygen_sample()
        )

        if is_reference_cage:
            self._set_data_value(
                data_items,
                "干基氧浓度(%)",
                get_reference_dry_oxygen_percent(),
            )
            return result_data

        self._set_or_reuse_dry_oxygen_value(
            data_items,
            mouse_cage_number,
            compensation_value,
            allow_default=allow_default_fallback,
        )
        return result_data

    def stop(self):
        if self.data_handle is not None:
            self.data_handle.stop()
            self.data_handle = None
        super().stop()

    def dosomething(self):
        if not _wait_collection_stage("wait_UGC_run_finish_event", "ZOS"):
            logger.critical(
                "ZOS skipped this collection round because UGC did not finish"
            )
            self._finish_batch()
            return

        mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
        mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)

        if not mouse_cages_inc or len(mouse_cages_inc) == 0:
            logger.error("ZOS运行失败，未选择实例化实验设置的mouse_cages！")
            self._finish_batch()
            return

        port = global_setting.get_setting("port", None)
        if port is None:
            logger.error("ZOS运行失败，未选择串口！")
            self._finish_batch()
            return

        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | ZOS-运行 1. 读取{'鼠笼' + str(mouse_cages_inc[mouse_cage_index]) if mouse_cage_index is not None else '参考气'}的氧浓度")
        cage_addr = mouse_cages_inc[mouse_cage_index] - 1 if mouse_cage_index is not None else 8
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"000{cage_addr}000E"),
            'slave_id': '4',
            'function_code': '4',
            'timeout': 1
        }
        self.send_thread.send_message = self.send_message
        self._wait_zos_channel_read_interval()
        result_data, message = self.send_thread.Send_no_promise()

        if not result_data or 'data' not in result_data:
            logger.warning(f"ZOS运行：读取失败，跳过本次采样")
            self._finish_batch()
            return

        result_data['mouse_cage_number'] = mouse_cages_inc[mouse_cage_index] if mouse_cage_index is not None else int(global_setting.get_setting('configer')['mouse_cage']['reference'])
        result_data['time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        result_data = self._replace_zero_zos_values_with_previous(result_data)
        result_data = self._protect_wet_o2_before_compensation(
            result_data, mouse_cage_index, mouse_cages_inc
        )
        result_data = self._append_o2_compensation(result_data)
        logger.error(f"zos:{result_data}")

        if len(result_data.get('data', [])) > 0:
            oxygen_val = next((d['value'] for d in result_data['data'] if '氧浓度' in d.get('desc', '')), None)
            logger.warning(f'氧浓度原始值:{oxygen_val}')

        result = store_data_with_result(result_data, need_result=True, timeout=5)
        if result and result.success:
            logger.info(f"数据存储成功，ID: {result.item_id}")
        else:
            logger.error(f"数据存储失败: {result.error if result else '未知错误'}")

        self._finish_batch()

    def _finish_batch(self):
        logger.debug("barrier_ZOS run one batch done ! ")
        _wait_collection_barrier("ZOS")
        time.sleep(float(global_setting.get_setting('UFC_UGC_ZOS_config')['ZOS']['run_time_delay']))







    def circular_read(self,resolve,reject,port,mouse_cages_inc):
        """
        ZOS-运行 1. 读取氧浓度（新协议：04 04 00 0X 00 0E，返回氧分压/温度1/气体总压/氧浓度/故障码/温度2/湿度）
        """
        mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | ZOS-运行 1. 读取{'鼠笼' + str(mouse_cages_inc[mouse_cage_index]) if mouse_cage_index is not None else '参考气'}的氧浓度")
        cage_addr = mouse_cages_inc[mouse_cage_index] - 1 if mouse_cage_index is not None else 8
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"000{cage_addr}000E"),
            'slave_id': '4',
            'function_code': '4',
            'timeout': 1
        }
        self.send_thread.send_message = self.send_message
        self._wait_zos_channel_read_interval()
        result_data, message = self.send_thread.Send_no_promise()

        # 检查数据有效性
        if not result_data or 'data' not in result_data:
            logger.warning(f"ZOS运行：读取失败，跳过本次采样")
            AsyPromise(self.finsh_one_batch, port=port, mouse_cages_inc=mouse_cages_inc).then(
                lambda r: resolve()
            ).catch(lambda e: logger.error(f"{e}"))
            return

        AsyPromise(self.handle_oxygen_value, port=port, r={'data': result_data}).then(
            lambda r: resolve()
        ).catch(lambda e: logger.error(f"{e}"))

    def handle_oxygen_value(self,resolve,reject,port,r):
        mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
        mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
        result_data = r['data']
        result_data['mouse_cage_number'] = mouse_cages_inc[mouse_cage_index] if mouse_cage_index is not None else int(global_setting.get_setting('configer')['mouse_cage']['reference'])
        result_data['time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        result_data = self._replace_zero_zos_values_with_previous(result_data)
        result_data = self._protect_wet_o2_before_compensation(
            result_data, mouse_cage_index, mouse_cages_inc
        )
        result_data = self._append_o2_compensation(result_data)
        logger.error(f"zos:{result_data}")

        if len(result_data.get('data', [])) > 0:
            oxygen_val = next((d['value'] for d in result_data['data'] if '氧浓度' in d.get('desc', '')), None)
            logger.warning(f'氧浓度原始值:{oxygen_val}')

        logger.info(f"result_data:{result_data}")
        result = store_data_with_result(result_data, need_result=True, timeout=5)
        if result and result.success:
            logger.info(f"数据存储成功，ID: {result.item_id}")
        else:
            logger.error(f"数据存储失败: {result.error if result else '未知错误'}")

        AsyPromise(self.finsh_one_batch, port=None, mouse_cages_inc=mouse_cages_inc).then(
            lambda r: resolve()
        ).catch(lambda e: logger.error(f"{e}"))

    def finsh_one_batch(self,resolve,reject,port,mouse_cages_inc):
        """
        完成一轮次后做的事情
        :param resolve:
        :param reject:
        :param port:
        :param mouse_cages_inc:
        :return:
        """
        # # 让鼠笼内的传感器开始运行
        # ufc_ugc_zos_barrier = global_setting.get_setting("ufc_ugc_zos_barrier")
        # if ufc_ugc_zos_barrier is not None:
        #     logger.debug(f"ufc_ugc_zos_barrier_ZOS run one batch done ! ")
        #     ufc_ugc_zos_barrier.wait()

        # 将鼠笼下标循环前移动
        mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
        # logger.critical(f"zos run :mouse_cage_index before:{mouse_cage_index}")
        mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
        if mouse_cage_index is not None:
            if mouse_cage_index == len(mouse_cages_inc) - 1:
                # 最后一个鼠笼 则下一个为参考气路
                mouse_cage_index = None
            else:
                mouse_cage_index = mouse_cage_index + 1
            pass
        else:
            # 当前为参考气 则下一个为第一个鼠笼
            mouse_cage_index = 0
            pass
        global_setting.set_setting("cage_number_list_index", mouse_cage_index)
        # logger.critical(f"zos run :mouse_cage_index after:{mouse_cage_index}")
        logger.debug("barrier_ZOS run one batch done ! ")
        _wait_collection_barrier("ZOS-circular-read")
        pass
        time.sleep(float(global_setting.get_setting('UFC_UGC_ZOS_config')['ZOS']['run_time_delay']))
        resolve()

class ZOS_gas_path_system(Gas_path_system):
    """
    ZOS 气路系统
    """
    # UFC开始的操作数
    process_nums =2+2+2
    def __init__(self):
        super().__init__()
        # zos启动状态
        self.zos_start_status = False
        # 运行线程
        self.zos_gas_path_system_run_thread = ZOS_gas_path_system_run_thread(
            name="ZOS_gas_path_system_run_thread",
            update_status_main_signal_gui_update=self.update_status_main_signal_gui_update,

        )
        # 是否停止
        self.is_stop = False
        pass
    def update(self):
        super().update()
        self.zos_gas_path_system_run_thread. update_status_main_signal_gui_update=self.update_status_main_signal_gui_update
        self.zos_gas_path_system_run_thread.send_thread.update_status_main_signal_gui_update=self.update_status_main_signal_gui_update
    """start start"""
    def start_success(self,resolve,reject):
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | ZOS 启动完成。")
        self.zos_start_status = True
        if self.is_stop:
            reject("Stop")
        resolve()
    def start(self,resolve,reject):
        time.sleep(0.01)
        self.update_status_main_signal_gui_update.send(f"{time_util.get_format_from_time(time.time())} | ZOS 正在启动")
        self.zos_gas_path_system_run_thread.reset_wet_o2_anomaly_guard("ZOS启动/状态切换")
        # 文档A-1：校准前先明确切到抽鼠笼状态，不能只更新软件状态。
        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！")
            reject("未选择串口，无法将ZOS切到抽鼠笼状态")
            return
        self.is_stop = False
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("00090000"),
            'slave_id': '4',
            'function_code': '5',
            'timeout': 1,
            'require_success': True,
        }
        self.send_thread.send_message = self.send_message
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | ZOS启动-1.切换为抽鼠笼采样状态"
        )

        def require_cage_switch_success(result):
            if not isinstance(result, dict) or not result.get("send_state", False):
                raise RuntimeError("ZOS切换抽鼠笼指令未获得有效响应")
            return result

        AsyPromise(self.send_thread.Send).then(
            require_cage_switch_success
        ).then(
            lambda _: AsyPromise(self.start_success)
        ).then(lambda _: resolve()).catch(reject)

        pass
    """start end"""
    """run start"""
    def run(self,resolve,reject):
        """
        气路运行
        :return:
        """
        time.sleep(0.01)
        self.update_status_main_signal_gui_update.send(f"{time_util.get_format_from_time(time.time())} | ZOS 开始运行{'.'*100}")
        #1.循环读取氧浓度
        self.zos_gas_path_system_run_thread.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update
        self.zos_gas_path_system_run_thread.start()

        resolve()
        pass
    """run end"""
    def stop(self,resolve,reject):
        """
        停止气路：先把ZOS切回鼠笼采样状态，再完成线程收尾。
        :return:
        """
        self.update_status_main_signal_gui_update.send(f"{time_util.get_format_from_time(time.time())} | ZOS 正在停止{'.'*100}")
        if self.zos_gas_path_system_run_thread is not None:
            self.zos_gas_path_system_run_thread.stop()
            self.zos_gas_path_system_run_thread.deleteLater()
            self.zos_gas_path_system_run_thread=None
        self.is_stop = True
        port = global_setting.get_setting("port", None)
        if port is None:
            reject("未选择串口，无法将ZOS切回鼠笼")
            return
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("00090000"),
            'slave_id': '4',
            'function_code': '5',
            'timeout': 1,
            'require_success': True,
        }
        self.send_thread.send_message = self.send_message
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | ZOS-停止 切换为鼠笼采样状态")
        AsyPromise(self.send_thread.Send).then(
            lambda _: self._finish_stop_after_cage_switch(resolve)
        ).catch(reject)

    def _finish_stop_after_cage_switch(self, resolve):
        self.zos_start_status = False
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | ZOS 已停止{'.' * 100}")
        # 返回响应
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(
                ObjectQueueItem(origin="Gas_path_system_zos", to="MainWindow_index", title="stop_zos_gap_system_return",
                                data=" ZOS 已停止",
                                time=time_util.get_format_from_time(time.time())))
        resolve()
