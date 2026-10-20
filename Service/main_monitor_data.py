import copy
import math

import multiprocessing
import os
import queue
import re
import shutil
import sys
import threading
import time

from datetime import datetime


from PyQt6.QtCore import QThread, QTimer, QCoreApplication

from loguru import logger


from Service.UFC_UGC_ZOS_Service.function.co2_compensation import CO2RealtimeCompensator
from Service.UFC_UGC_ZOS_Service.index.UFC_UGC_ZOS_index import UFC_UGC_ZOS_index

from public.config_class import global_load
from public.config_class.global_setting import global_setting

from public.dao.SQLite.Monitor_Datas_Handle import Monitor_Datas_Handle
from public.entity.MyQThread import MyQThread, MyThread
from public.entity.barrier.ActionCompleteBarrier import ActionCompleteBarrier
from public.entity.barrier.DynamicBarrier import DynamicBarrier
from public.entity.dict.AdvancedFuzzyDict import FuzzyDict
from public.entity.experiment_setting_entity import Experiment_setting_entity
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.entity.send_message import Send_Message

from public.function.Modbus.Modbus_Type import Modbus_Slave_Type, Modbus_Slave_Send_Messages_Senior_Data, Others_Tables, Modbus_Slave_Ids
from public.function.Modbus.New_Mod_Bus import ModbusRTUMasterNew
from public.function.cage_control_commands import build_cage_control_command
from public.function.Monitor_data_storage.DataStorage import StorageResult, store_data_with_result, DataItem
from public.function.Monitor_data_storage.metabolism import (
    METABOLISM_COLUMNS,
    adjust_reference_co2,
    calculate_epoch_metabolism,
)
from public.function.promise.AsyPromise import AsyPromise
from public.system_health import HealthPolicy, HealthReporter
from public.function.weight.weight_series_formatter import format_weight_series_for_storage
from public.function.weight.weight_window_assembler import (
    WEIGHT_POINTS_NEWEST_FIRST,
    WEIGHT_POINTS_OLDEST_FIRST,
    WeightWindowAssembler,
)
from public.util.cage_light_state_util import (
    build_cage_light_commands,
    build_cage_light_state_change_commands,
    force_save_cage_lights_off,
    save_cage_light_state,
)
from public.util.custom_data_file_util import custom_data_file_util
from public.util.number_util import number_util
from public.util.string_util import String_util
from public.util.time_util import time_util
from public.util.lighting_schedule import normalize_lighting_schedule, resolve_lighting_state

# 全局变量
# 实现主线程发一整轮消息，当从线程响应完全部的消息后，主线程在发一整轮消息
MESSAGE_BATCH_SIZE = 0
total_messages_processed = 1
lock = threading.Lock()
COLLECTION_BARRIER_TIMEOUT_SECONDS = 45.0
COLLECTION_SENSOR_MAX_ATTEMPTS = 3
COLLECTION_SENSOR_RETRY_DELAY_SECONDS = 0.1
WEIGHT_SINGLE_DATA = ["04", "01", "00", "02"]
WEIGHT_30_POINT_DATA = ["04", "01", "00", "3C"]
WEIGHT_SINGLE_RESPONSE_BYTES = 0x04
WEIGHT_30_POINT_RESPONSE_BYTES = 0x78
_weight_window_assembler = None
_weight_window_assembler_lock = threading.RLock()
FOOD_TROUGH_CURRENT_OFF_COMMAND = {
    "function_code": "05",
    "data": ["00", "71", "00", "00"],
    "no_response": True,
    "command_name": "food_trough_current_off",
}
FOOD_TROUGH_CURRENT_OFF_BASE_SLAVE_ID = 0x02
_shutdown_food_trough_current_off_done = False
send_thread = None


# The health queue is deliberately independent from the existing GUI/log queues.
# It carries small snapshots only and must never block the collection path.
HEALTH_SUCCESS_REPORT_INTERVAL_SECONDS = 2.0
_health_snapshot_queue = None
_health_reporters = {}
_health_reporters_lock = threading.RLock()
_health_last_success_report_at = {}
_ufc_emergency_shutdown_thread = None
ufc_ugc_zos_thread = None


def _stop_ufc_activity_before_emergency_command():
    """Prevent an in-flight UFC startup worker from reopening the pump."""
    controller = ufc_ugc_zos_thread
    if controller is None:
        return
    ufc = getattr(controller, "UFC_gas_path_system_obj", None)
    if ufc is None:
        return

    start_thread = getattr(ufc, "ufc_gas_path_system_start_thread", None)
    if start_thread is not None:
        start_thread.is_stop = True
    run_thread = getattr(ufc, "ufc_gas_path_system_run_thread", None)
    if run_thread is not None and run_thread.isRunning():
        run_thread.stop()


def _extract_ufc_pump_state(parsed_response):
    if not isinstance(parsed_response, dict):
        return None
    for item in parsed_response.get("data") or []:
        if isinstance(item, dict) and item.get("desc") == "气泵":
            try:
                value = int(item.get("value"))
            except (TypeError, ValueError):
                return None
            return value if value in (0, 1) else None
    return None


def emergency_stop_ufc_pump():
    """Synchronously stop and verify the UFC pump after a GUI crash.

    Returns 1 when pump-off was verified, 2 when the write was acknowledged
    but state verification was unavailable, and 0 when the write failed.
    """
    # From this point onward no asynchronous gas-path/calibration callback may
    # issue another command.  The emergency write below bypasses the regular
    # Send_Message wrapper, so it remains allowed.
    global_setting.set_setting("ufc_emergency_shutdown_active", True)

    port = global_setting.get_setting("port", None)
    modbus = global_setting.get_setting("modbus", None)
    if port is None or modbus is None:
        logger.critical(
            "UFC emergency shutdown failed: serial port or Modbus instance is missing"
        )
        return 0

    _stop_ufc_activity_before_emergency_command()
    serial_lock = global_setting.get_setting("serial_lock", None)
    if serial_lock is None:
        logger.critical("UFC emergency shutdown failed: serial lock is missing")
        return 0
    if not serial_lock.acquire(timeout=5.0):
        logger.critical("UFC emergency shutdown failed: serial lock timeout")
        return 0

    try:
        # A crash can be detected while another UFC request is finishing.  In
        # that case the first response may belong to the old request (the
        # field log contained exactly this situation).  Keep the serial lock
        # and retry the safety command before allowing process cleanup.
        pump_off_acknowledged = False
        for write_attempt in range(1, 4):
            logger.critical(
                "UFC emergency shutdown: sending pump-off command "
                f"attempt={write_attempt}/3, slave=2 function=5 data=000A0000"
            )
            _, response_hex, send_state, _ = modbus.send_command(
                slave_id="2",
                function_code="5",
                data_hex_list=number_util.set_int_to_4_bytes_list("000A0000"),
                is_parse_response=False,
            )
            if send_state:
                pump_off_acknowledged = True
                logger.critical(
                    "UFC emergency shutdown pump-off command acknowledged: "
                    f"attempt={write_attempt}, response={response_hex}"
                )
                break
            logger.critical(
                "UFC emergency shutdown pump-off attempt failed: "
                f"attempt={write_attempt}, response={response_hex}"
            )
            time.sleep(0.3)

        if not pump_off_acknowledged:
            logger.critical(
                "UFC emergency shutdown failed: pump-off command was not "
                "acknowledged after 3 attempts"
            )
            return 0

        for attempt in range(1, 4):
            time.sleep(0.2)
            state_response, state_hex, state_ok, _ = modbus.send_command(
                slave_id="2",
                function_code="1",
                data_hex_list=number_util.set_int_to_4_bytes_list("C"),
                is_parse_response=False,
            )
            if not state_ok or state_response is None:
                logger.warning(
                    "UFC emergency shutdown state read failed: "
                    f"attempt={attempt}, response={state_hex}"
                )
                continue
            try:
                parsed, _ = modbus.parse_response(
                    response=state_response,
                    response_hex=state_response.hex(),
                    send_state=True,
                    slave_id="2",
                    function_code="1",
                )
            except Exception as error:
                logger.warning(
                    "UFC emergency shutdown state parse failed: "
                    f"attempt={attempt}, error={error}"
                )
                continue
            pump_state = _extract_ufc_pump_state(parsed)
            logger.critical(
                "UFC emergency shutdown state read: "
                f"attempt={attempt}, air_pump={pump_state}"
            )
            if pump_state == 0:
                global_setting.set_setting("ufc_start_time_state", False)
                return 1

        logger.critical(
            "UFC emergency shutdown command was acknowledged, "
            "but pump-off state was not verified"
        )
        return 2
    except Exception as error:
        logger.exception(f"UFC emergency shutdown raised an exception: {error}")
        return 0
    finally:
        serial_lock.release()


def _watch_ufc_emergency_shutdown(request_event, complete_event, result_value):
    logger.info("UFC emergency shutdown watcher started")
    while True:
        request_event.wait()
        request_event.clear()
        result = 0
        try:
            result = emergency_stop_ufc_pump()
        except BaseException as error:
            # This thread is the last software safety path before the worker is
            # terminated.  Never leave the parent waiting just because an
            # unexpected library/native-wrapper exception escaped.
            logger.exception(
                f"UFC emergency shutdown watcher failed unexpectedly: {error}"
            )
        finally:
            with result_value.get_lock():
                result_value.value = int(result)
            complete_event.set()


def _start_ufc_emergency_shutdown_watcher(
        request_event, complete_event, result_value):
    global _ufc_emergency_shutdown_thread
    if request_event is None or complete_event is None or result_value is None:
        return
    _ufc_emergency_shutdown_thread = threading.Thread(
        target=_watch_ufc_emergency_shutdown,
        args=(request_event, complete_event, result_value),
        name="ufc_emergency_shutdown_watcher",
        daemon=True,
    )
    _ufc_emergency_shutdown_thread.start()


def _set_health_snapshot_queue(health_snapshot_queue):
    """Configure the optional, dedicated health channel for this process."""
    global _health_snapshot_queue, _health_reporters, _health_last_success_report_at
    with _health_reporters_lock:
        _health_snapshot_queue = health_snapshot_queue
        _health_reporters = {}
        _health_last_success_report_at = {}


def _health_publish_interval_seconds():
    try:
        health_config = (global_setting.get_setting("configer", {}) or {}).get("system_health", {})
        return max(float(health_config.get("publish_interval_seconds", HEALTH_SUCCESS_REPORT_INTERVAL_SECONDS)), 0.1)
    except (AttributeError, TypeError, ValueError):
        return HEALTH_SUCCESS_REPORT_INTERVAL_SECONDS


def _health_policy_for(component):
    try:
        health_config = (global_setting.get_setting("configer", {}) or {}).get("system_health", {})
    except (AttributeError, TypeError):
        health_config = {}
    try:
        default_threshold = max(int(float(health_config.get("failure_threshold", 3))), 1)
    except (TypeError, ValueError):
        default_threshold = 3
    try:
        storage_threshold = max(int(float(health_config.get("storage_failure_threshold", default_threshold))), 1)
    except (TypeError, ValueError):
        storage_threshold = default_threshold
    try:
        recovery_threshold = max(int(float(health_config.get("recovery_success_threshold", 2))), 1)
    except (TypeError, ValueError):
        recovery_threshold = 2
    return HealthPolicy(
        failure_threshold=storage_threshold if component == "data_storage" else default_threshold,
        recovery_success_threshold=recovery_threshold,
    )


def _health_reporter(component, scope="global"):
    if _health_snapshot_queue is None:
        return None
    key = (str(component), str(scope))
    with _health_reporters_lock:
        reporter = _health_reporters.get(key)
        if reporter is None:
            reporter = HealthReporter(
                _health_snapshot_queue,
                component=key[0],
                scope=key[1],
                policy=_health_policy_for(key[0]),
            )
            _health_reporters[key] = reporter
        return reporter


def _health_scope_from_message(send_message):
    """Keep physical-cage health independent instead of claiming all channels are fine."""
    if not isinstance(send_message, dict):
        return "global"
    cage_number = send_message.get("mouse_cage_number")
    if cage_number is None:
        return "reference" if send_message.get("type") == "reference_enm" else "global"
    try:
        return f"CH{int(cage_number):02d}"
    except (TypeError, ValueError):
        return str(cage_number)


def _monitor_sampling_interval_seconds():
    try:
        monitor_config = global_setting.get_setting("monitor_data", {}) or {}
        return max(float((monitor_config.get("SEND", {}) or {}).get("delay", 0.0)), 0.1)
    except (TypeError, ValueError, AttributeError):
        return 1.0


def _health_metrics_from_message(send_message, **extra_metrics):
    message = send_message if isinstance(send_message, dict) else {}
    metrics = {
        "sampling_interval_seconds": _monitor_sampling_interval_seconds(),
        "module": message.get("module_name", ""),
        "slave_id": message.get("slave_id", ""),
        "function_code": message.get("function_code", ""),
    }
    metrics.update({key: value for key, value in extra_metrics.items() if value is not None})
    return metrics


def _coerce_health_number(value):
    """Return a display-safe numeric sensor value, or ``None``.

    Parsed Modbus values are normally numeric, but this guard keeps a textual
    status flag or a multi-value response from being presented as a measured
    environmental value on P1.
    """
    if isinstance(value, bool) or isinstance(value, (list, tuple, dict)):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _environment_health_metrics_from_return_data(return_data):
    """Extract only directly observed environment/gas values for P1.

    This is attached to the existing low-frequency collection health snapshot.
    It does not calculate RER/EE or invent a missing sensor value.
    """
    if not isinstance(return_data, dict):
        return {}
    module_name = str(return_data.get("module_name", "")).strip().upper()
    data_items = return_data.get("data", [])
    if not isinstance(data_items, list):
        return {}

    environment = {}
    observed_at = time.time()

    def record(key, value, unit):
        numeric_value = _coerce_health_number(value)
        if numeric_value is not None:
            environment[key] = {
                "value": numeric_value,
                "unit": unit,
                "source": module_name,
                "updated_at": observed_at,
            }

    for item in data_items:
        if not isinstance(item, dict):
            continue
        desc = str(item.get("desc", ""))
        value = item.get("value")
        # ENM is the cage-environment module.  Do not mislabel a ZOS gas-path
        # temperature as cage ambient temperature.
        if module_name == "ENM":
            if "温度测量值" in desc:
                record("temperature", value, "°C")
            elif "湿度测量值" in desc:
                record("humidity", value, "%RH")
            elif "噪声测量值" in desc:
                record("noise", value, "dB")
        if "光照亮度" in desc or "光照强度" in desc:
            record("light", value, "lx")
        # P1 displays the latest direct gas concentration only.  It does not
        # use derived consumption/production columns as a substitute.
        if module_name == "ZOS" and ("氧浓度" in desc or "氧气浓度" in desc):
            record("oxygen", value, "%")
        if module_name == "UGC" and ("CO2(" in desc or "二氧化碳浓度" in desc):
            record("co2", value, "%")

    return {"environment": environment} if environment else {}


def _channel_measurement_health_metrics_from_return_data(return_data):
    """Keep directly observed per-cage measurements in the health snapshot.

    The P1 channel cards are read-only and must not query SQLite on every
    refresh.  Preserve the parsed Modbus values in the low-frequency health
    message so the GUI can render the latest real sample for that cage.
    Derived RER/EE values are intentionally not created here; the GUI derives
    them only when both persisted VO2 and VCO2 inputs are available.
    """
    if not isinstance(return_data, dict):
        return {}
    data_items = return_data.get("data", [])
    if not isinstance(data_items, list):
        return {}
    measurements = {}
    for item in data_items:
        if not isinstance(item, dict):
            continue
        desc = str(item.get("desc", "")).strip()
        value = item.get("value")
        if not desc or value is None or isinstance(value, (dict, list, tuple)):
            continue
        measurements[desc] = value
    return {"measurements": measurements} if measurements else {}


def _report_gas_path_health(return_data, base_metrics):
    """Report an explicit UFC/UGC/ZOS fault code as a gas-path fault.

    A non-zero device fault code is the one rule that immediately produces a
    fault, rather than waiting for three communication failures.  No fault
    code means only that the current response was healthy; recovery still
    follows the shared two-success rule.
    """
    if not isinstance(return_data, dict):
        return
    module_name = str(return_data.get("module_name", "")).strip().upper()
    if module_name not in {"UFC", "UGC", "ZOS"}:
        return

    fault_codes = []
    for item in return_data.get("data", []) if isinstance(return_data.get("data"), list) else []:
        if not isinstance(item, dict) or "故障码" not in str(item.get("desc", "")):
            continue
        numeric_value = _coerce_health_number(item.get("value"))
        if numeric_value is not None and numeric_value != 0:
            fault_codes.append(numeric_value)

    metrics = dict(base_metrics or {})
    metrics.update({"gas_module": module_name, "device_fault_codes": fault_codes})
    if fault_codes:
        _report_health_failure(
            "gas_path",
            module_name,
            reason_code="gas_device_fault_code",
            message=f"{module_name} reported non-zero device fault code(s): {fault_codes}",
            metrics=metrics,
            force_fault=True,
        )
    else:
        _report_health_success(
            "gas_path",
            module_name,
            metrics=metrics,
            message=f"{module_name} response contains no non-zero device fault code",
        )


def _report_health_success(component, scope="global", *, metrics=None, message=""):
    """Publish healthy observations at a low rate while retaining recovery semantics."""
    reporter = _health_reporter(component, scope)
    if reporter is None:
        return
    key = (str(component), str(scope))
    now = time.monotonic()
    with _health_reporters_lock:
        previous_at = _health_last_success_report_at.get(key, 0.0)
        if now - previous_at < _health_publish_interval_seconds():
            return
        _health_last_success_report_at[key] = now
    reporter.success(metrics=metrics, message=message)


def _report_health_failure(
    component,
    scope="global",
    *,
    reason_code="operation_failed",
    message="",
    metrics=None,
    force_fault=False,
):
    reporter = _health_reporter(component, scope)
    if reporter is not None:
        reporter.failure(
            reason_code=reason_code,
            message=message,
            metrics=metrics,
            force_fault=force_fault,
        )


def _environment_module_only_enabled():
    value = global_setting.get_setting("environment_module_only", None)
    if value is None:
        monitor_config = global_setting.get_setting("monitor_data", {}) or {}
        value = (monitor_config.get("MODULE_TEST", {}) or {}).get(
            "environment_module_only", False
        )
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _weight_read_30_points_enabled():
    monitor_config = global_setting.get_setting("monitor_data", {}) or {}
    value = (monitor_config.get("WEIGHT_PROTOCOL", {}) or {}).get(
        "read_30_points", False
    )
    if isinstance(value, bool):
        return value

    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized not in {"", "0", "false", "no", "off", "none"}:
        logger.warning(
            f"称重30点配置值无效：{value!r}，已按默认单值模式处理"
        )
    return False


def _weight_points_order():
    monitor_config = global_setting.get_setting("monitor_data", {}) or {}
    value = str(
        (monitor_config.get("WEIGHT_PROTOCOL", {}) or {}).get(
            "points_order", WEIGHT_POINTS_OLDEST_FIRST
        )
    ).strip().lower()
    if value in {WEIGHT_POINTS_OLDEST_FIRST, WEIGHT_POINTS_NEWEST_FIRST}:
        return value
    logger.warning(
        f"称重30点顺序配置无效：{value!r}，已按旧数据在前处理"
    )
    return WEIGHT_POINTS_OLDEST_FIRST


def _reset_weight_window_assembler():
    global _weight_window_assembler

    now_wall = time.time()
    now_monotonic = time.monotonic()
    try:
        origin_time = float(
            global_setting.get_setting("start_experiment_time", now_wall)
        )
    except (TypeError, ValueError):
        origin_time = now_wall
    origin_time = min(origin_time, now_wall)
    origin_monotonic = now_monotonic - max(0.0, now_wall - origin_time)

    points_order = _weight_points_order()
    with _weight_window_assembler_lock:
        _weight_window_assembler = WeightWindowAssembler(
            origin_time=origin_time,
            origin_monotonic=origin_monotonic,
            points_order=points_order,
        )
    logger.info(
        "称重30点时间窗已重置："
        f"起点={datetime.fromtimestamp(origin_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]}，"
        f"数据顺序={points_order}"
    )


def _get_weight_window_assembler():
    with _weight_window_assembler_lock:
        assembler = _weight_window_assembler
    if assembler is None:
        _reset_weight_window_assembler()
        with _weight_window_assembler_lock:
            assembler = _weight_window_assembler
    return assembler


def _ingest_weight_packet(cage_number, values, packet_monotonic):
    assembler = _get_weight_window_assembler()
    result = assembler.add_packet(cage_number, values, packet_monotonic)
    logger.warning(
        "称重30点时间窗处理："
        f"笼子{result.cage_number}，实际新增跨度={result.elapsed_points}s，"
        f"重叠={result.overlap_points}点，"
        f"重叠不一致={result.overlap_mismatch_points}点，"
        f"新增={result.appended_points}点，"
        f"缺失补None={result.missing_points}点，"
        f"完成窗口={len(result.completed_windows)}个，"
        f"结转下一窗口={result.carried_points}点"
    )
    if result.overlap_mismatch_points:
        logger.error(
            "称重30点滚动缓存校验不一致："
            f"笼子{result.cage_number}，"
            f"重叠={result.overlap_points}点，"
            f"不一致={result.overlap_mismatch_points}点；"
            "请核对points_order配置及设备是否返回最近30个滚动点"
        )
    if result.elapsed_points > 0:
        store_data_with_result({
            "module_name": "WeightBackfill",
            "cage_number": int(cage_number),
            "origin_time": assembler.origin_time,
            "sample_interval_seconds": assembler.sample_interval_seconds,
            "packet_tick": assembler._packet_tick(packet_monotonic),
            "values": assembler._normalize_packet(values),
        }, need_result=False)
    for window in result.completed_windows:
        logger.info(
            "称重30秒窗口完成："
            f"笼子{window.cage_number}，"
            f"窗口={datetime.fromtimestamp(window.start_time).strftime('%Y-%m-%d %H:%M:%S')}"
            f"~{datetime.fromtimestamp(window.end_time).strftime('%Y-%m-%d %H:%M:%S')}，"
            f"真实={len(window.values) - window.missing_points}点，"
            f"None={window.missing_points}点"
        )
    return result


def _pop_epoch_weight_window(cage_number):
    assembler = _get_weight_window_assembler()
    window = assembler.pop_epoch_window(cage_number)
    backlog = assembler.completed_window_count(cage_number)
    return window, backlog


def _is_weight_read_message(message):
    return isinstance(message, dict) and message.get("module_name") == "WM"


def _configure_weight_read_message(message, cage_number, read_30_points):
    message["mouse_cage_number"] = cage_number
    message["module_name"] = "WM"
    message["weight_read_30_points"] = bool(read_30_points)
    if read_30_points:
        message["data"] = list(WEIGHT_30_POINT_DATA)
        message["expected_response_byte_count"] = WEIGHT_30_POINT_RESPONSE_BYTES
    else:
        message["data"] = list(WEIGHT_SINGLE_DATA)
        message["expected_response_byte_count"] = WEIGHT_SINGLE_RESPONSE_BYTES


def _get_response_byte_count(response):
    if not isinstance(response, (bytes, bytearray)) or len(response) < 3:
        return None
    return response[2]


def _is_expected_weight_response(response, expected_byte_count):
    if not isinstance(response, (bytes, bytearray)) or len(response) < 5:
        return False
    return (
        response[1] == 0x04
        and response[2] == expected_byte_count
        and len(response) == 3 + expected_byte_count + 2
    )


def _cage_data_type_enabled(data_type):
    if not _environment_module_only_enabled():
        return True
    return getattr(data_type, "name", "") == "ENM"


_CAGE_MODULE_KEYS = {
    "WM": "weight",
    "EM": "water",
    "DWM": "food",
    "ENM": "environment",
}


def _channel_module_enabled(cage_number, data_type):
    """Return whether a per-cage acquisition module is enabled.

    The configuration page stores switches in ``experiment_setting.channel_modules``
    keyed by cage number.  Old experiment files do not contain this mapping, so
    they retain the historical behaviour (enabled by default).
    """
    module_key = _CAGE_MODULE_KEYS.get(getattr(data_type, "name", ""))
    if not module_key:
        return True
    setting = global_setting.get_setting("experiment_setting", None)
    modules = getattr(setting, "channel_modules", None) if setting is not None else None
    if not isinstance(modules, dict):
        return True
    candidates = [str(cage_number)]
    try:
        candidates.append(str(int(cage_number)))
    except (TypeError, ValueError):
        pass
    values = None
    for key in candidates:
        if key in modules:
            values = modules[key]
            break
    if values is None:
        try:
            values = modules.get(int(cage_number))
        except (TypeError, ValueError):
            values = None
    if not isinstance(values, dict):
        return True
    return bool(values.get(module_key, True))


def _cage_control_allowed(cage_number, control):
    setting = global_setting.get_setting("experiment_setting", None)
    if setting is None:
        return False
    selected = set()
    for group in getattr(setting, "groups", []) or []:
        if not bool(getattr(group, "is_selected", False)):
            continue
        try:
            selected.add(int(group.id))
        except (TypeError, ValueError):
            continue
    if cage_number not in selected:
        return False
    module_key = "food" if control == "food_door" else "environment"
    if control == "food_door" and _environment_module_only_enabled():
        return False
    modules = getattr(setting, "channel_modules", {}) or {}
    values = modules.get(str(cage_number), modules.get(cage_number, {})) if isinstance(modules, dict) else {}
    return not isinstance(values, dict) or bool(values.get(module_key, True))


def _send_main_window_status(title, data):
    message_queue = global_setting.get_setting("queue", None)
    if message_queue:
        message_queue.put(
            ObjectQueueItem(
                origin="main_monitor_data",
                to="MainWindow_index",
                title=title,
                data=data,
                time=time_util.get_format_from_time(time.time()),
            )
        )



def _send_cage_control_response(send_message, send_state, response_hex=None, detail=None):
    """Publish the real device result for a P3 cage-control command."""
    if not isinstance(send_message, dict) or not send_message.get("cage_control"):
        return
    response_text = str(response_hex or "").replace(" ", "").upper()
    message = "设备已成功响应" if send_state else str(detail or "设备未返回有效响应")
    _send_main_window_status(
        "cage_control_response",
        {
            "success": bool(send_state),
            "cage_number": send_message.get("mouse_cage_number"),
            "control": send_message.get("cage_control_name"),
            "value": send_message.get("cage_control_value"),
            "request_id": send_message.get("cage_control_request_id"),
            "message": message,
            "response_hex": response_text,
        },
    )


def _notify_internal_command_result(send_message, send_state, response_hex=None, detail=None):
    """Return a queued control command's device result to its owner thread."""
    if not isinstance(send_message, dict):
        return
    result_queue = send_message.get("_internal_result_queue")
    if result_queue is None:
        return
    try:
        result_queue.put_nowait({
            "success": bool(send_state),
            "response_hex": str(response_hex or ""),
            "detail": str(detail or ""),
        })
    except queue.Full:
        pass
    except Exception as error:
        logger.warning(f"内部控制指令回执发送失败: {error}")


def _notify_food_trough_current_off_success(response_hex=None, cage_number=None):
    """Show the success notice after the current-off command is acknowledged."""
    frame_hex = str(response_hex or "未返回报文").replace(" ", "").upper()
    cage_text = f"笼子{cage_number}" if cage_number is not None else "食槽门"
    _send_main_window_status(
        "mouse_cage_inner_module_running_state",
        f"{cage_text}驱动电流关闭指令发送成功\n发送报文：{frame_hex}",
    )


def _build_food_trough_current_off_command(cage_number, port=None):
    """Build the per-cage 0x0071 current-off command.

    Cage 1..8 map to Modbus slave IDs 0x12, 0x22, ..., 0x82.
    """
    cage_number = int(cage_number)
    if not 1 <= cage_number <= 8:
        raise ValueError(f"食槽门驱动电流关闭命令的笼号无效: {cage_number}")

    command = copy.deepcopy(FOOD_TROUGH_CURRENT_OFF_COMMAND)
    command["slave_id"] = format(
        FOOD_TROUGH_CURRENT_OFF_BASE_SLAVE_ID + 0x10 * cage_number,
        "02X",
    )
    command["mouse_cage_number"] = cage_number
    if port is not None:
        command["port"] = port
    return command


def _get_food_trough_cage_number(command):
    """Resolve the cage number from an explicit field or its slave ID."""
    if not isinstance(command, dict):
        return None

    explicit_cage = command.get("mouse_cage_number")
    try:
        explicit_cage = int(explicit_cage)
    except (TypeError, ValueError):
        explicit_cage = None
    if explicit_cage is not None and 1 <= explicit_cage <= 8:
        return explicit_cage

    try:
        slave_id = int(str(command.get("slave_id", "")), 16)
    except (TypeError, ValueError):
        return None
    cage_number = slave_id // 0x10
    return cage_number if 1 <= cage_number <= 8 else None


def _queue_food_trough_current_off_commands(cages=None, wait_seconds=None, reason=""):
    """Queue one current-off command for every enabled mouse cage."""
    global send_thread

    if _environment_module_only_enabled():
        logger.info("仅环境模块模式：跳过食槽驱动电流关闭命令")
        return []

    if cages is None:
        cages = _get_opened_mouse_cages_for_light_off()
    else:
        cages = _parse_mouse_cage_numbers(cages)
    cages = sorted(set(cages))
    if not cages:
        logger.warning("食槽驱动电流关闭命令跳过：没有已开启的笼子")
        return []

    if send_thread is None or not send_thread.isRunning():
        logger.critical("食槽驱动电流关闭命令未发送：串口发送线程未运行")
        return []

    port = global_setting.get_setting("port", port_use)
    queued_cages = []
    for cage_number in cages:
        try:
            command = _build_food_trough_current_off_command(
                cage_number,
                port=port,
            )
        except (TypeError, ValueError) as error:
            logger.error(f"构造笼子{cage_number}驱动电流关闭命令失败: {error}")
            continue
        send_thread.add_message(
            message={"message": command},
            urgent=True,
            origin="main_monitor_data",
        )
        queued_cages.append(cage_number)
        logger.warning(
            f"{reason or '食槽门操作'}：已排队关闭笼子{cage_number}驱动电流命令 "
            f"{command['slave_id']} 05 00 71 00 00"
        )

    if wait_seconds is None:
        wait_seconds = min(3.0, 0.25 * len(queued_cages) + 0.5)
    if queued_cages and wait_seconds > 0:
        time.sleep(wait_seconds)
    return queued_cages


def _is_food_trough_door_command(send_message):
    """识别食槽门控制报文，不把其他写报文误判为门控。"""
    if not isinstance(send_message, dict):
        return False

    function_code = send_message.get("function_code")
    try:
        function_code = int(str(function_code).strip().lower().replace("0x", ""), 16)
    except (TypeError, ValueError):
        return False
    if function_code != 0x05:
        return False

    data = send_message.get("data") or []
    if len(data) < 2:
        return False
    try:
        address_high = int(str(data[0]).strip().lower().replace("0x", ""), 16)
        address_low = int(str(data[1]).strip().lower().replace("0x", ""), 16)
    except (TypeError, ValueError):
        return False
    return address_high == 0x00 and address_low == 0x70


def _notify_skipped_gas_stop_complete():
    reason = "仅环境模块模式" if _environment_module_only_enabled() else "气路未启动"
    statuses = (
        ("stop_gap_system_return", f"{reason}：气路无需停止"),
        ("stop_ufc_gap_system_return", f"{reason}：UFC无需停止"),
        ("stop_ugc_gap_system_return", f"{reason}：UGC无需停止"),
        ("stop_zos_gap_system_return", f"{reason}：ZOS无需停止"),
    )
    for title, text in statuses:
        _send_main_window_status(title, text)


batch_complete_event = threading.Event()
#等待气路启动之后在一起运行发送
wait_UFC_UGC_ZOS_start_event = threading.Event()
global_setting.set_setting("wait_UFC_UGC_ZOS_start_event",wait_UFC_UGC_ZOS_start_event)
# 通道
experiment_settings = global_setting.get_setting("experiment_setting",None)
gids = [group.id for group in experiment_settings.groups if group.is_selected == 1] if experiment_settings is not None else []
#气路之间也需要顺序run ufc run->ugc run->zos run
wait_UFC_run_finish_event = threading.Semaphore(0)
wait_UGC_run_finish_event = threading.Semaphore(0)
wait_ZOS_run_finish_event = threading.Semaphore(0)
global_setting.set_setting("wait_UFC_run_finish_event",wait_UFC_run_finish_event)
global_setting.set_setting("wait_UGC_run_finish_event",wait_UGC_run_finish_event)
global_setting.set_setting("wait_ZOS_run_finish_event",wait_ZOS_run_finish_event)


def _reset_collection_sync_signals():
    """Install empty counting signals for a new experiment run."""
    global batch_complete_event
    global wait_UFC_run_finish_event, wait_UGC_run_finish_event, wait_ZOS_run_finish_event

    batch_complete_event = threading.Event()
    wait_UFC_run_finish_event = threading.Semaphore(0)
    wait_UGC_run_finish_event = threading.Semaphore(0)
    wait_ZOS_run_finish_event = threading.Semaphore(0)
    global_setting.set_setting("wait_UFC_run_finish_event", wait_UFC_run_finish_event)
    global_setting.set_setting("wait_UGC_run_finish_event", wait_UGC_run_finish_event)
    global_setting.set_setting("wait_ZOS_run_finish_event", wait_ZOS_run_finish_event)


def _release_collection_sync_waiters():
    """Wake collection workers promptly while an experiment is stopping."""
    batch_complete_event.set()
    for signal_name in (
        "wait_UFC_run_finish_event",
        "wait_UGC_run_finish_event",
        "wait_ZOS_run_finish_event",
    ):
        signal = global_setting.get_setting(signal_name, None)
        if signal is not None and hasattr(signal, "release"):
            signal.release()


def _drain_collection_stage_signals():
    """Discard notifications left over from a completed or failed round."""
    for signal_name in (
        "wait_UFC_run_finish_event",
        "wait_UGC_run_finish_event",
        "wait_ZOS_run_finish_event",
    ):
        signal = global_setting.get_setting(signal_name, None)
        if signal is None or not hasattr(signal, "acquire"):
            continue
        while signal.acquire(blocking=False):
            pass

#使用端口
port_use=None
_co2_realtime_compensator = CO2RealtimeCompensator()


def _reference_co2_subtract_value():
    """Return the live reference-CO2 correction in percentage points."""
    value = global_setting.get_setting("reference_co2_subtract_value", None)
    if value is None:
        config = global_setting.get_setting("UFC_UGC_ZOS_config", {}) or {}
        value = (config.get("Calibration", {}) or {}).get(
            "reference_co2_subtract_value", 0.0125
        )
    try:
        value = float(value)
    except (TypeError, ValueError):
        return 0.0125
    return value if math.isfinite(value) else 0.0125


_epoch_carry_forward_handle = None
_epoch_last_valid_cache = {}
_epoch_missing_streak_cache = {}
_epoch_missing_fill_limit = 10
_epoch_carry_forward_desc_to_column = {
    "ufc_流量计测量值(sccm)": "UFC_flow_num",
    "ufc_参考气流量计测量值(sccm)": "reference_flow_num",
    "传感器状态码": "UGC_flow_num_1",
    "传感器状态": "UGC_flow_num_1",
    "对齐后CO2": "UGC_air_pressure",
    "拟合后CO2": "UGC_air_pressure",
    "补偿前CO2": "UGC_CO2_origin_num",
    "补偿前CO2(%)": "UGC_CO2_origin_num",
    "气压补偿后CO2": "UGC_CO2_num",
    "参考气CO2(%)": "reference_CO2_num",
    "CO2生产量(%)": "CO2_output_num",
    "氧分压(hPa)": "ZOS_oxygen_partial_pressure",
    "ZOS温度测量值(°C)": "ZOS_temperature_num",
    "气体压力(hPa)": "ZOS_gas_pressure",
    "氧浓度(%)": "ZOS_oxygen_num",
    "干基氧浓度(%)": "ZOS_dry_basis_oxygen_num",
    "ZOS故障码": "ZOS_fault_code",
    "ZOS温度2测量值(°C)": "ZOS_oxygen_temperature_2_num",
    "ZOS湿度测量值(%RH)": "ZOS_oxygen_humidity_num",
    "参考气氧浓度(%)": "reference_oxygen_num",
    "耗氧量(%)": "oxygen_consumption_num",
    "温度测量值(°C)": "ENM_temperature_num",
    "湿度测量值(%RH)": "ENM_humidity_num",
    "噪声测量值(dB)": "ENM_noise_num",
    "大气压测量值(KPa)": "ENM_barometer_num",
    "当前计量周期内跑轮圈数测量值": "ENM_running_wheel_num",
    "食物重量测量值(g)": "DWM_weight_num",
    "饮水重量测量值(g)": "EM_weight_num",
    "称重重量测量值(g)": "WM_weight_num",
}


def _normalize_ugc_sensor_status(value):
    """Convert the UGC sensor state to the public 1=normal, 0=fault code."""
    if value is None:
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (int, float)):
        if value == 1:
            return 1
        if value == 0:
            return 0
    normalized = str(value).strip().lower()
    if normalized in {"1", "正常", "ok", "normal", "true"}:
        return 1
    if normalized in {"0", "故障", "错误", "fault", "error", "false"}:
        return 0
    return None


def _channel_number_to_co2_channel_name(mouse_cage_number):
    reference_cage_number = int(global_setting.get_setting('configer')['mouse_cage']['reference'])
    if int(mouse_cage_number) == reference_cage_number:
        return "REF"
    return f"M{int(mouse_cage_number)}"


def _is_epoch_missing_value(value):
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if isinstance(value, str):
        normalized = value.strip().lower()
        return normalized in {"", "none", "nan"}
    return False


def _get_epoch_payload_cage_number(payload):
    mouse_cage_number = payload.get("mouse_cage_number")
    if mouse_cage_number not in (None, -1):
        return int(mouse_cage_number)

    for item in payload.get("data", []):
        if item.get("desc") == "鼠笼号":
            try:
                return int(item.get("value"))
            except (TypeError, ValueError):
                return None
    return None


def _get_epoch_previous_row(cage_number):
    global _epoch_carry_forward_handle

    if cage_number is None:
        return None

    if _epoch_carry_forward_handle is None:
        _epoch_carry_forward_handle = Monitor_Datas_Handle()

    return _epoch_carry_forward_handle.query_current_one_data(f"Epoch_data_cage_{int(cage_number)}")


def _apply_epoch_carry_forward(payload):
    if payload.get("module_name") != "Epoch":
        return payload

    cage_number = _get_epoch_payload_cage_number(payload)
    if cage_number is None:
        return payload

    should_update_state = payload.get("mouse_cage_number") != -1
    previous_row = None

    for item in payload.get("data", []):
        desc = str(item.get("desc", "")).strip()
        column_name = _epoch_carry_forward_desc_to_column.get(desc)
        if not column_name:
            continue

        if column_name == "WM_weight_num" and _weight_read_30_points_enabled():
            # 30点模式用真实时间窗和None表达缺失，不能复制上一整包数据。
            continue

        cache_key = (int(cage_number), column_name)
        current_value = item.get("value")

        if _is_epoch_missing_value(current_value):
            streak = _epoch_missing_streak_cache.get(cache_key, 0)
            if should_update_state:
                streak += 1
                _epoch_missing_streak_cache[cache_key] = streak

            previous_value = _epoch_last_valid_cache.get(cache_key)
            if previous_value is None:
                if previous_row is None:
                    previous_row = _get_epoch_previous_row(cage_number)
                if previous_row is not None:
                    previous_value = previous_row.get(column_name)
                    if not _is_epoch_missing_value(previous_value):
                        _epoch_last_valid_cache[cache_key] = previous_value

            if not _is_epoch_missing_value(previous_value) and streak < _epoch_missing_fill_limit:
                item["value"] = previous_value
        else:
            if should_update_state:
                _epoch_last_valid_cache[cache_key] = current_value
                _epoch_missing_streak_cache[cache_key] = 0

    return payload

# 过滤日志
#logger = logger.bind(category="deep_camera_logger")

class read_queue_data_Thread(MyQThread):
    def __init__(self, name):
        super().__init__(name)
        self.queue = None
        self.send_thread: Send_thread = None
        pass
    def stop(self):
        if self.send_thread is not None and self.send_thread.isRunning():
            self.send_thread.stop()
        super().stop()


    def dosomething(self):
        global gids
        if not self.queue.empty():
            # logger.error(f"{self.queue.qsize()}")
            try:
                message: ObjectQueueItem = self.queue.get()
                # logger.error(f"{self.name}_get_message:{message}|")
            except Exception as e:
                logger.error(f"{self.name}发生错误{e}")
                return
            # logger.error(f"{self.name}_get_message:{message}|")
            if message is not None and message.is_Empty():
                return
            if message is not None and isinstance(message, ObjectQueueItem) and message.to == 'main_monitor_data':
                # logger.error(f"{self.name}_get_message:{message}")
                match message.title:
                    case '':
                        if self.send_thread is not None and self.send_thread.isRunning():
                            # 发送优先级高的报文
                            self.send_thread.add_message(message=message.data, urgent=True, origin=message.origin)
                            pass
                    case 'set_port':
                        global port_use,send_thread
                        port_use=message.data
                        global_setting.set_setting("port", port_use)
                        modbus: ModbusRTUMasterNew = global_setting.get_setting("modbus", None)
                        if modbus is None:
                            modbus = ModbusRTUMasterNew(port_use, baudrate=115200, timeout=float(
                                global_setting.get_setting('monitor_data')['Serial']['timeout']), )
                            global_setting.set_setting("modbus", modbus)
                        else:
                            modbus.close()
                            modbus = ModbusRTUMasterNew(port_use, baudrate=115200, timeout=float(
                                 global_setting.get_setting('monitor_data')['Serial']['timeout']), )
                            global_setting.set_setting("modbus", modbus)
                        if send_thread is not None:
                            send_thread.set_modbus(modbus)
                    case 'set_experiment_basic_config':
                        data: dict = message.data
                        if data is not None and isinstance(data, dict):
                            for key, value in data.items():
                                # logger.critical(f"{self.name}<UNK>{key}<UNK>{value}")
                                global_setting.set_setting(key, value)
                    case 'lighting_schedule':
                        schedule = normalize_lighting_schedule(message.data)
                        global_setting.set_setting("lighting_schedule", schedule)
                        experiment_setting = global_setting.get_setting("experiment_setting", None)
                        if experiment_setting is not None:
                            experiment_setting.lighting_schedule = copy.deepcopy(schedule)
                        if lighting_schedule_thread is not None and lighting_schedule_thread.isRunning():
                            lighting_schedule_thread.set_schedule(schedule, apply_now=True)
                    case 'cage_control':
                        request = message.data if isinstance(message.data, dict) else {}
                        cage_number = request.get("cage_number")
                        control = request.get("control")
                        request_id = request.get("request_id")
                        try:
                            cage_number = int(cage_number)
                            control = str(control or "")
                            if not _cage_control_allowed(cage_number, control):
                                raise ValueError("笼子未启用，或对应测量模块未启用")
                            if self.send_thread is None or not self.send_thread.isRunning():
                                raise RuntimeError("串口发送线程未运行")
                            command = build_cage_control_command(
                                cage_number, control, request.get("value"),
                                global_setting.get_setting("port", None),
                            )
                            command.update({
                                "cage_control_name": control,
                                "cage_control_value": request.get("value"),
                                "cage_control_request_id": request_id,
                            })
                            self.send_thread.add_message(
                                message={"message": command}, urgent=True,
                                origin="MainWindow_index",
                            )
                            logger.info(f"笼子{cage_number}硬件操作已排队: {control}={request.get('value')}")
                        except (TypeError, ValueError, RuntimeError, KeyError) as error:
                            logger.warning(f"笼内硬件操作未发送: {error}")
                            _send_main_window_status(
                                "cage_control_response",
                                {
                                    "success": False,
                                    "cage_number": cage_number,
                                    "control": control,
                                    "value": request.get("value"),
                                    "request_id": request_id,
                                    "message": f"指令未发送：{error}",
                                },
                            )
                    case 'start':
                        data = message.data
                        if data is not None:
                            global_setting.set_setting("start_experiment_time", data.get("start_experiment_time",time.time()))
                            global_setting.set_setting("experiment_root_path", data.get("experiment_root_path", None))
                            global_setting.set_setting("pause_experiment_time", data.get("pause_experiment_time",[]))
                            global_setting.set_setting("relieve_pause_experiment_time", data.get("relieve_pause_experiment_time",[]))
                            startup_calibration_mode = data.get(
                                "startup_calibration_mode",
                                global_setting.get_setting("startup_calibration_mode", "none"),
                            )
                            if startup_calibration_mode == "air_co2":
                                startup_calibration_mode = "air"
                            valid_startup_modes = {
                                "none", "air", "zero", "span", "full",
                                "o2_span_air", "co2_o2_span_air",
                            }
                            if startup_calibration_mode not in valid_startup_modes:
                                error = f"启动校准模式无效: {startup_calibration_mode}"
                                logger.error(error)
                                _send_main_window_status("start_experiment_failed", error)
                                return
                            global_setting.set_setting(
                                "startup_calibration_mode", startup_calibration_mode
                            )
                            global_setting.set_setting(
                                "is_auto_calibration",
                                startup_calibration_mode != "none",
                            )
                            logger.critical(
                                f"启动命令确认校准模式: {startup_calibration_mode}"
                            )
                        try:
                            start()
                        except Exception as e:
                            logger.exception(f"{self.name}启动实验失败：{e}")
                            _send_main_window_status("start_experiment_failed", str(e))
                    case 'start_zero_calibration':
                        start_zero_calibration()
                        pass
                    case 'start_span_calibration':
                        start_range_calibration()
                        pass
                    case 'start_air_calibration':
                        start_air_calibration()
                        pass
                    case 'start_calibration':
                        start_calibration()
                        pass
                    case 'stop_zero_calibration':

                        stop_zero_calibration()
                        pass
                    case 'stop_span_calibration':
                        stop_range_calibration()
                        pass
                    case 'stop_calibration':
                        stop_calibration()
                    case 'pause':
                        pause()
                    case 'stop':
                        data = message.data
                        if data is not None:
                            global_setting.set_setting("stop_experiment_time",
                                                       data.get("stop_experiment_time", time.time()))
                        stop()
                    case 'experiment_setting':
                        data = message.data
                        if data is not None:
                            # 将实验设置存入全局变量
                            received_setting = data.get("experiment_setting", None)
                            enabled_setting = copy.deepcopy(received_setting)
                            enabled_groups = [
                                group for group in getattr(received_setting, "groups", [])
                                if bool(getattr(group, "is_selected", False))
                            ]
                            gids = [int(group.id) for group in enabled_groups]
                            if enabled_setting is not None:
                                enabled_setting.groups = [
                                    group for group in enabled_setting.groups
                                    if int(group.id) in gids
                                ]
                                enabled_setting.animalGroupRecords = [
                                    record
                                    for record in getattr(enabled_setting, "animalGroupRecords", [])
                                    if int(record.gid) in gids
                                ]
                            global_setting.set_setting("experiment_setting", enabled_setting)
                            global_setting.set_setting("experiment_setting_file",
                                                       data.get("experiment_setting_file", ""))

                            # 将鼠笼号存进全局变量来使用
                            global_setting.set_setting("mouse_cages", gids)
                            global_setting.set_setting("mouse_cages_2byte_str",
                                                       String_util.array_to_binary_string(gids))
                            logger.info(f"experiment setting applied: enabled mouse cages={gids}")

                        pass
                    case 'stop_modbus':
                        logger.critical(f"{self.name},stop_modbus")
                        stop_modbus()
                    case "detect_air_modules_only":
                        port = global_setting.get_setting("port")
                        detection_session_id = message.data.get("detection_session_id")
                        all_modules_check_online_state_Not_Each_Mouse_Cage(port, None, detection_session_id)
                    case "detect_cage_modules_only":
                        port = global_setting.get_setting("port")
                        gids_from_message = message.data.get("gids", [])
                        detection_session_id = message.data.get("detection_session_id")

                        # 使用传入的笼号进行检测
                        if gids_from_message and len(gids_from_message) > 0:
                            # 逐个笼子检测
                            for cage_num in gids_from_message:
                                cage_num_int = int(cage_num)
                                logger.info(f"开始检测笼子 {cage_num_int}")
                                all_modules_check_online_state_Each_Mouse_Cage(port, cage_num_int, detection_session_id)
                        else:
                            logger.warning("未收到有效的笼号信息")
                    case "start_all_modules_detection":
                        """
                        开始检测所有模块是否在线
                        """
                        all_modules_check_online_state()
                    case _:
                        pass




            else:
                # 把消息放回去
                self.queue.put(message)

        pass


read_queue_data_thread = read_queue_data_Thread(name="main_monitor_data_read_queue_data_thread")


class PeriodicXlsxExportThread(MyQThread):
    """Periodically exports the current experiment database to xlsx."""

    def __init__(self, name, interval_minutes):
        super().__init__(name)
        self.interval_seconds = max(float(interval_minutes) * 60, 1.0)
        self.next_run_time = time.time() + self.interval_seconds

    def dosomething(self):
        now = time.time()
        if now < self.next_run_time:
            time.sleep(min(1.0, self.next_run_time - now))
            return

        db_file_path = get_monitor_data_db_file_path()
        export_file_path = f"{get_monitor_data_export_base_path()}.xlsx"

        if not os.path.exists(db_file_path):
            logger.warning(f"定时导出xlsx跳过，数据库文件不存在: {db_file_path}")
            self.next_run_time = time.time() + self.interval_seconds
            return

        try:
            success = custom_data_file_util.export_data_to_csv(
                export_file_path=export_file_path,
                file_path=db_file_path,
                show_success_message=False,
                use_atomic_replace=True
            )
            if success:
                logger.info(f"定时导出xlsx成功: {export_file_path}")
            else:
                logger.error(f"定时导出xlsx失败: {export_file_path}")
        except Exception as e:
            logger.error(f"定时导出xlsx异常: {e}")
        finally:
            self.next_run_time = time.time() + self.interval_seconds


def  all_modules_check_online_state():
    port = global_setting.get_setting("port")
    global gids,send_thread
    experiment_settings = global_setting.get_setting("experiment_setting", None)
    gids = [group.id for group in experiment_settings.groups if
            group.is_selected == 1] if experiment_settings is not None else []
    mouse_cage_index =None
    for i in range(len(gids)+1):
        # 鼠笼内的模块 参考气路则不运行：
        if mouse_cage_index is not None:
            all_modules_check_online_state_Each_Mouse_Cage(port,mouse_cage_index)
        #气路
        all_modules_check_online_state_Not_Each_Mouse_Cage(port,mouse_cage_index)
        # print(f"send_messages:{send_messages}")
        # 将鼠笼下标循环前移动
        if mouse_cage_index is not None:
            if mouse_cage_index == len(gids) - 1:
                # 最后一个鼠笼 则下一个为参考气路
                mouse_cage_index = None
            else:
                mouse_cage_index = mouse_cage_index + 1
            pass
        else:
            # 当前为参考气 则下一个为第一个鼠笼
            mouse_cage_index = 0
            pass
    pass


def all_modules_check_online_state_Each_Mouse_Cage(port, mouse_cage_index, detection_session_id=None):
    """
    检测单个笼子的笼内模块
    关键修复：mouse_cage_index 直接代表笼子号（不是数组索引）
    """
    global gids, send_thread

    send_messages = []

    # ==================== 获取该笼子对应的从站ID偏移 ====================
    if mouse_cage_index is None:
        logger.error("笼子索引不能为None")
        return

    # 直接使用笼子号作为偏移量计算从站ID
    slave_id_offset = int(mouse_cage_index)

    for data_type in Modbus_Slave_Type.Each_Mouse_Cage_Message_Module_Info.value:
        if not _cage_data_type_enabled(data_type):
            continue
        if not _channel_module_enabled(mouse_cage_index, data_type):
            continue
        """获取该笼子的所有传感器模块"""
        for message_struct in data_type.value['send_messages']:
            message_temp = copy.deepcopy(message_struct.message)
            message_temp['port'] = port

            # 根据笼子号计算从站ID
            base_slave_id = int(message_temp['slave_id'], 16)
            new_slave_id = base_slave_id + 16 * slave_id_offset
            message_temp['slave_id'] = format(new_slave_id, '02X')

            # 添加笼号信息用于响应时识别
            message_temp['mouse_cage_number'] = mouse_cage_index
            message_temp['detection_session_id'] = detection_session_id

            send_messages.append({'message': message_temp})

            # logger.debug(
            #     f"  准备报文: 模块={data_type}, 从站ID={message_temp['slave_id']}, "
            #     f"笼子={mouse_cage_index}"
            # )

    # ==================== 发送所有报文 ====================
    for msg in send_messages:
        send_thread.add_message(message=msg, urgent=True, origin="New_main_experiment_setting")

    # logger.critical(
    #     f"笼子 {mouse_cage_index} 共发送 {len(send_messages)} 条报文\n"
    #     f"{'=' * 80}\n"
    # )


def all_modules_check_online_state_Not_Each_Mouse_Cage(port, mouse_cage_index, detection_session_id=None):
    """
    检测气路模块的在线状态
    """
    if _environment_module_only_enabled():
        logger.info("Environment-only mode: skip UFC/UGC/ZOS online detection")
        return

    send_messages = []
    # ==================== 获取鼠笼号 ====================
    if mouse_cage_index is not None:
        logger.debug(f"笼子 {mouse_cage_index} 跳过气路检测，仅参考气路需要检测")
        return
    else:
        cage_label = "参考气路"

    # ==================== 只做状态查询 ====================
    # 查询从站 02 (UFC)
    status_msg_02 = Send_Message(
        slave_address='2',
        slave_desc="02-UFC状态查询",
        function_code=4,
        function_desc="读取UFC模块状态",
        message={
            'port': port,
            'data': ['00', '00', '00', '08'],
            'slave_id': '2',
            'function_code': '11',
            'timeout': 1,
            'module_type': 'status_read',
            'device_type': 'UFC',
            'cage_index': mouse_cage_index,
            'mouse_cage_number': None,
            'detection_session_id': detection_session_id
        }
    )
    send_messages.append(status_msg_02)

    # 查询从站 03 (UGC)
    status_msg_03 = Send_Message(
        slave_address='3',
        slave_desc="03-UGC状态查询",
        function_code=2,
        function_desc="读取UGC模块状态",
        message={
            'port': port,
            'data': ['00', '00', '00', '05'],
            'slave_id': '3',
            'function_code': '5',
            'timeout': 1,
            'module_type': 'status_read',
            'device_type': 'UGC',
            'cage_index': mouse_cage_index,
            'mouse_cage_number': None,
            'detection_session_id': detection_session_id
        }
    )
    send_messages.append(status_msg_03)

    # 查询从站 04 (ZOS) - 改用功能码01读传感器状态
    status_msg_04 = Send_Message(
        slave_address='4',
        slave_desc="04-ZOS状态查询",
        function_code=1,
        function_desc="读取ZOS传感器状态",
        message={
            'port': port,
            'data': ['00', '08', '00', '01'],  # 读参考气(08)状态
            'slave_id': '4',
            'function_code': '1',  # 改为功能码01
            'timeout': 1,
            'module_type': 'status_read',
            'device_type': 'ZOS',
            'cage_index': mouse_cage_index,
            'mouse_cage_number': None,
            'detection_session_id': detection_session_id
        }
    )
    send_messages.append(status_msg_04)

    # ==================== 发送所有报文 ====================
    for send_msg in send_messages:
        send_thread.add_message(
            message={'message': send_msg.message},
            urgent=True,
            origin="New_main_experiment_setting"
        )

    # ==================== 日志记录（只记录状态查询）====================
    # 只统计状态查询的消息数量，不记录气路切换操作
    status_count = sum(1 for msg in send_messages if msg.message.get('module_type') == 'status_read')

    # 使用 debug 级别记录日志，只显示状态查询
    logger.debug(
        f"气路模块状态检测 | {cage_label} | "
        f"发送{status_count}条状态查询报文"
    )


"""
数据存储区域 start
"""
# 存储数据锁
store_Q_lock = threading.Lock()
store_Q = queue.Queue()
result_queues = {}  # 存储各个数据项的结果队列 {queue,queue...}
result_queues_lock = threading.Lock()
#  放进全局变量中
global_setting.set_setting("store_Q_lock",store_Q_lock)
global_setting.set_setting("store_Q",store_Q)
global_setting.set_setting("result_queues", result_queues)
global_setting.set_setting("result_queues_lock", result_queues_lock)



class Store_Thread(MyQThread):
    """
    存储请求线程发来的数据到sqlite中
    """

    def __init__(self, name):
        self.handle = None
        super().__init__(name)

    def run(self):
        try:
            super().run()
        finally:
            if self.handle is not None:
                self.handle.stop()
                self.handle = None

    def dosomething(self):
        global store_Q
        try:
            first_item = store_Q.get(timeout=0.2)
        except queue.Empty:
            return

        # 合并当前已经排队的数据，降低高频单条事务对读写线程的影响。
        batch = [first_item]
        while len(batch) < 100:
            try:
                batch.append(store_Q.get_nowait())
            except queue.Empty:
                break

        if self.handle is None:
            self.handle = Monitor_Datas_Handle()

        outcomes = []
        transaction_failed = False
        try:
            with self.handle.sqlite_manager.batch_transaction():
                for data_item in batch:
                    outcomes.append((data_item, *self.store_to_data_base(data_item.data)))
        except Exception as exc:
            logger.exception(f"{self.name}批量存储事务失败: {exc}")
            outcomes = [(data_item, False, str(exc)) for data_item in batch]
            transaction_failed = True

        if transaction_failed:
            _report_health_failure(
                "data_storage",
                reason_code="sqlite_transaction_failed",
                message=str(outcomes[0][2] if outcomes else "SQLite transaction failed")[:240],
                metrics={"batch_size": len(batch), "storage": "sqlite"},
            )

        for data_item, success, error in outcomes:
            if success:
                _report_health_success(
                    "data_storage",
                    metrics={"batch_size": len(batch), "storage": "sqlite"},
                    message="SQLite write succeeded",
                )
            elif not transaction_failed:
                _report_health_failure(
                    "data_storage",
                    reason_code="sqlite_write_failed",
                    message=str(error or "SQLite write failed")[:240],
                    metrics={"batch_size": len(batch), "storage": "sqlite"},
                )
            self._send_storage_result(data_item, success, error)

    def store_to_data_base(self, data):
        """
        存储数据到数据库
        返回: (success: bool, error: str)
        """
        success = True
        error = None

        try:
            if self.handle is None:
                self.handle = Monitor_Datas_Handle()  # 创建数据库




            success,error = self.handle.insert_data(data)


        except Exception as e:
            success = False
            error = str(e)
            logger.error(f"{self.name}存储错误：{e}")

        return success, error

    def _send_storage_result(self, data_item, success, error):
        """发送存储结果到对应的结果队列"""
        if data_item.result_queue is not None:
            try:
                result = StorageResult(
                    item_id=data_item.id,
                    success=success,
                    error=error,
                    timestamp=time.time()
                )
                data_item.result_queue.put(result)

                # 清理结果队列引用
                result_queues_lock_q = global_setting.get_setting("result_queues_lock")
                result_queues_q = global_setting.get_setting("result_queues")
                with result_queues_lock_q:
                    if data_item.id in result_queues_q:
                        del result_queues_q[data_item.id]

            except Exception as e:
                logger.error(f"发送存储结果失败: {e}")

    def stop(self):
        super().stop()
"""
数据存储区域 end
"""

class Send_thread(MyQThread):
    """
    请求数据线程
    """

    def __init__(self, name=None, modbus=None,
                 ):
        super().__init__(name)

        self.modbus: ModbusRTUMasterNew= global_setting.get_setting("modbus", None)
        # 正常队列和紧急队列 紧急队列的消息立即处理
        self.normal_queue = queue.Queue()
        self.priority_queue = queue.Queue()
        self.lock = threading.Lock()
        self.normal_queue_lock = threading.Lock()
        self.priority_queue_lock = threading.Lock()
        self.normal_queue_empty=False
        self.priority_queue_empty=False
        pass

#!
    def add_message(self, message, urgent=False, origin=""):
        # origin 为源头
        if urgent:
            with self.priority_queue_lock:
                self.priority_queue.put({'origin': origin, 'message': message})
        else:
            with self.normal_queue_lock:
                self.normal_queue.put(message)


    def stop(self):
        if self.modbus is not None:
            self.modbus.close()
        super().stop()

    def set_modbus(self, modbus):
        self.modbus = modbus

    def _send_food_trough_current_off_after_door_command(self, door_command):
        """食槽门控制成功后立即关断驱动电流，避免门动作结束后持续放电。"""
        if _environment_module_only_enabled():
            logger.info("仅环境模块模式：跳过食槽门后的驱动电流关闭命令")
            return False
        if self.modbus is None:
            logger.critical("食槽门控制后未发送关电流命令：Modbus未初始化")
            return False

        cage_number = _get_food_trough_cage_number(door_command)
        if cage_number is None:
            logger.critical(
                "食槽门控制后未发送关电流命令：无法从门控报文识别笼号，"
                f"door_command={door_command}"
            )
            return False

        command = _build_food_trough_current_off_command(
            cage_number,
            port=door_command.get("port", global_setting.get_setting("port", port_use)),
        )
        response, response_hex, send_state, _ = self.modbus.send_command(
            slave_id=command["slave_id"],
            function_code=command["function_code"],
            data_hex_list=command["data"],
            is_parse_response=False,
        )
        if send_state:
            logger.warning(
                "食槽门控制成功后已关闭驱动电流："
                f"{response_hex} | {command['command_name']}"
            )
            _notify_food_trough_current_off_success(response_hex, cage_number)
        else:
            logger.critical(
                "食槽门控制已发送，但关闭驱动电流命令发送失败："
                f"{response_hex or response}"
            )
        return bool(send_state)

    def _send_sensor_read_with_retry(self, send_message):
        is_weight = _is_weight_read_message(send_message)
        expected_byte_count = send_message.get("expected_response_byte_count")
        result = (None, None, False, None)
        for attempt in range(1, COLLECTION_SENSOR_MAX_ATTEMPTS + 1):
            result = self.modbus.send_command(
                slave_id=send_message['slave_id'],
                function_code=send_message['function_code'],
                data_hex_list=send_message['data'],
                is_parse_response=False,
            )
            if result[2]:
                if (
                    is_weight
                    and expected_byte_count in {
                        WEIGHT_SINGLE_RESPONSE_BYTES,
                        WEIGHT_30_POINT_RESPONSE_BYTES,
                    }
                    and not _is_expected_weight_response(
                        result[0], expected_byte_count
                    )
                ):
                    actual_byte_count = _get_response_byte_count(result[0])
                    logger.warning(
                        "称重响应长度与配置模式不匹配："
                        f"笼子{send_message.get('mouse_cage_number')}，"
                        f"slave_id={send_message.get('slave_id')}，"
                        f"期望字节数=0x{expected_byte_count:02X}，"
                        f"实际字节数={actual_byte_count!r}，"
                        f"attempt={attempt}/{COLLECTION_SENSOR_MAX_ATTEMPTS}"
                    )
                    return_data = result[3]
                    if isinstance(return_data, dict):
                        return_data["response_state"] = False
                        return_data["response_msg"] = "称重响应长度与配置模式不匹配"
                    result = (result[0], result[1], False, return_data)
                else:
                    if attempt > 1:
                        logger.warning(
                            "sensor read recovered after retry: "
                            f"slave_id={send_message['slave_id']}, "
                            f"function_code={send_message['function_code']}, "
                            f"attempt={attempt}"
                        )
                    return result

            logger.warning(
                "sensor read failed; retrying current request: "
                f"slave_id={send_message['slave_id']}, "
                f"function_code={send_message['function_code']}, "
                f"attempt={attempt}/{COLLECTION_SENSOR_MAX_ATTEMPTS}"
            )
            if attempt < COLLECTION_SENSOR_MAX_ATTEMPTS:
                time.sleep(COLLECTION_SENSOR_RETRY_DELAY_SECONDS)
        return result

    def run(self):
        logger.warning(f"{self.name} thread has been started！")
        self._running=True
        self._stop_requested = False
        self._paused = False  # 启动时重置状态
        global lock, total_messages_processed, store_Q, store_Q_lock
        global MESSAGE_BATCH_SIZE
        try:
            self.before_Runing_work()

            while not self._stop_requested and not self.isInterruptionRequested():
                _report_health_success(
                    "monitor_data_service",
                    metrics={"monitor_kind": "process"},
                    message="Monitor-data service loop is active",
                )
                self.mutex.lock()

                # 如果被暂停，就等待
                while self._paused and not self._stop_requested and not self.isInterruptionRequested():
                    self.condition.wait(self.mutex, 1000)  # 1秒超时

                # 检查是否需要退出
                should_exit = self._stop_requested or self.isInterruptionRequested()
                self.mutex.unlock()

                if should_exit:
                    break
                # 执行实际工作
                try:
                    self.priority_queue_empty = False
                    self.normal_queue_empty = False
                    send_message = None
                    try:
                        # logger.info(self.send_messages)

                        try:
                            with self.priority_queue_lock:
                                message = self.priority_queue.get_nowait()
                            send_message = message['message']['message']

                            logger.debug(f"{self.name}接收到查询报文。正在发送查询报文：{send_message}")
                            is_write_only = bool(send_message.get('write_only', False))
                            if is_write_only:
                                response, response_hex, send_state, return_data = (
                                    self.modbus.send_command_without_response(
                                        slave_id=send_message['slave_id'],
                                        function_code=send_message['function_code'],
                                        data_hex_list=send_message['data'],
                                    )
                                )
                            else:
                                response, response_hex, send_state, return_data = self.modbus.send_command(
                                    slave_id=send_message['slave_id'],
                                    function_code=send_message['function_code'],
                                    data_hex_list=send_message['data'],
                                    is_parse_response=False
                                )
                            response_return_data =copy.deepcopy(return_data)
                            final_return_data = response_return_data
                            # 响应报文是正确的，即发送状态时正确的 进行解析响应报文
                            parser_message=""
                            if send_state and not is_write_only:
                                return_data, parser_message = self.modbus.parse_response(response=response,
                                                                                         response_hex=response.hex(),
                                                                                         send_state=True,
                                                                                         slave_id=
                                                                                         send_message['slave_id'],
                                                                                         function_code=
                                                                                         send_message['function_code'], )
                                if return_data is not None:
                                    final_return_data = {**response_return_data, **return_data}
                                # 如果为1104 环境模块 存储大气压值
                                if response.hex()[:4] =="1104" and  final_return_data.get("data") and len(final_return_data.get("data"))>1:
                                    datas = final_return_data.get("data")
                                    for data in datas:
                                        desc = data.get("desc")
                                        if desc and desc == "大气压测量值(KPa)":
                                            global_setting.set_setting("air_pressure_1104", float(data.get("value")))
                                            break
                                if send_message.get("command_name") == "food_trough_current_off":
                                    _notify_food_trough_current_off_success(
                                        response_hex,
                                        _get_food_trough_cage_number(send_message),
                                    )
                                if _is_food_trough_door_command(send_message):
                                    self._send_food_trough_current_off_after_door_command(
                                        send_message
                                    )
                            elif send_state and is_write_only:
                                parser_message = send_message.get(
                                    'command_name', '无需响应报文发送成功'
                                )
                                logger.warning(
                                    f"安全控制命令发送成功: {response_hex} | {parser_message}"
                                )
                                if send_message.get("command_name") == "food_trough_current_off":
                                    _notify_food_trough_current_off_success(
                                        response_hex,
                                        _get_food_trough_cage_number(send_message),
                                    )
                                if _is_food_trough_door_command(send_message):
                                    self._send_food_trough_current_off_after_door_command(
                                        send_message
                                    )
                            elif is_write_only:
                                logger.critical(
                                    "安全控制命令发送失败: "
                                    f"{send_message.get('command_name', send_message)}"
                                )

                            if 'detection_session_id' in send_message:
                                final_return_data['detection_session_id'] = send_message.get('detection_session_id')

                            if send_message.get("cage_control"):
                                _send_cage_control_response(
                                    send_message,
                                    send_state,
                                    response_hex,
                                    parser_message if not send_state else None,
                                )
                            # 把返回数据返回给源头
                            message_struct = None

                            # 检查是否需要返回给GUI
                            should_send_to_gui = not send_message.get('no_response', False)
                            if should_send_to_gui and message['origin'] == "New_main_experiment_setting":
                                # ==================== 判断是否为气路模块 ====================
                                is_air_path_module = final_return_data.get('module_name') in ['UFC', 'UGC', 'ZOS']

                                if is_air_path_module:
                                    # 气路模块响应
                                    message_struct = ObjectQueueItem(
                                        to=message['origin'],
                                        data=final_return_data,
                                        title="Not_Each_Mouse_Cage_detect_finished",  # 气路模块标题
                                        origin='main_monitor_data'
                                    )
                                    logger.debug(f"气路模块 {final_return_data.get('module_name')} 检测完成")
                                else:
                                    # 鼠笼内模块响应
                                    message_struct = ObjectQueueItem(
                                        to=message['origin'],
                                        data=final_return_data,
                                        title="Each_Mouse_Cage_detect_finished",  # 鼠笼内模块标题
                                        origin='main_monitor_data'
                                    )
                                    logger.debug(f"鼠笼内模块 {final_return_data.get('module_name')} 检测完成")


                            elif should_send_to_gui and send_state:
                                message_struct = ObjectQueueItem(
                                    to=message['origin'],
                                    data=parser_message,
                                    origin='main_monitor_data'
                                )

                            if message_struct is not None:
                                global_setting.get_setting("send_message_queue").put(message_struct)
                                logger.debug(f"main_monitor_data将响应报文的解析数据返回源头：{message_struct}")
                            elif not should_send_to_gui:
                                # logger.debug(
                                #     f"气路切换操作完成 | 模块: {send_message.get('module_type')} | 操作: {send_message.get('switch_step')}")
                                pass
                            _notify_internal_command_result(
                                send_message,
                                send_state,
                                response_hex=response_hex,
                                detail=parser_message if not send_state else None,
                            )
                        except queue.Empty:
                            self.priority_queue_empty=True
                            pass
                        except Exception as error:
                            if isinstance(send_message, dict) and send_message.get("cage_control"):
                                _send_cage_control_response(
                                    send_message,
                                    False,
                                    detail=f"发送异常：{error}",
                                )
                            _notify_internal_command_result(
                                send_message,
                                False,
                                detail=f"发送异常：{error}",
                            )
                            logger.exception(f"优先级控制指令处理失败: {error}")
                        send_message=None
                        # 处理普通消息
                        try:
                            #!
                            with self.normal_queue_lock:
                                message = self.normal_queue.get(timeout=0.1)
                            send_message = message['message']
                            # logger.critical(f"send_thread:{self.name}<UNK>{message}")
                            # 普通鼠笼消息直接发送；参考分支只放行显式允许的 reference_enm
                            if message and message.get('type', None) in (None, 'reference_enm'):
                                start_time=time.time()
                                response, response_hex, send_state, return_data = (
                                    self._send_sensor_read_with_retry(send_message)
                                )
                                end_time = time.time()
                                end_monotonic = time.monotonic()
                                if response is not None:
                                    logger.critical(f"报文{response.hex()}发收时间：{(end_time - start_time):.3f}秒")
                                else:
                                    logger.critical(f"报文{send_message['slave_id']}{send_message['function_code']}{send_message['data']},出现问题！：发收时间：{(end_time - start_time):.3f}秒")
                                # 响应报文是正确的，即发送状态时正确的 进行解析响应报文
                                if send_state:
                                    # start_time =time.time()
                                    return_data, parser_message = self.modbus.parse_response(response=response,
                                                                                             response_hex=response.hex(),
                                                                                             send_state=True,
                                                                                             slave_id=
                                                                                             send_message['slave_id'],
                                                                                             function_code=
                                                                                             send_message['function_code'], )
                                    health_metrics = _health_metrics_from_message(
                                        send_message,
                                        response_seconds=round(end_time - start_time, 4),
                                        **_environment_health_metrics_from_return_data(return_data),
                                        **_channel_measurement_health_metrics_from_return_data(return_data),
                                    )
                                    _report_health_success(
                                        "monitor_collection",
                                        _health_scope_from_message(send_message),
                                        metrics=health_metrics,
                                        message="Modbus sensor read succeeded",
                                    )
                                    _report_gas_path_health(return_data, health_metrics)

                                    # 门控报文由普通采集队列发送；门动作完成后仍需立即关断
                                    # 驱动电流，不能只依赖优先队列中的写入分支。
                                    if _is_food_trough_door_command(send_message):
                                        self._send_food_trough_current_off_after_door_command(
                                            send_message
                                        )

                                    if (
                                        _is_weight_read_message(send_message)
                                        and send_message.get("weight_read_30_points")
                                    ):
                                        weight_values = next(
                                            (
                                                item.get("value")
                                                for item in return_data.get("data", [])
                                                if item.get("desc") == "重量测量值(g)"
                                            ),
                                            None,
                                        )
                                        if isinstance(weight_values, list) and len(weight_values) == 30:
                                            _ingest_weight_packet(
                                                send_message.get("mouse_cage_number"),
                                                weight_values,
                                                end_monotonic,
                                            )
                                        else:
                                            logger.error(
                                                "称重30点报文解析结果无效："
                                                f"笼子{send_message.get('mouse_cage_number')}，"
                                                f"数据类型={type(weight_values).__name__}，"
                                                f"点数={len(weight_values) if isinstance(weight_values, list) else 0}"
                                            )

                                    # end_time = time.time()
                                    # logger.critical(f"报文{response.hex()}解析时间：{(end_time - start_time):.3f}秒")
                                    return_data['data'].append({'desc': '备注', 'value': None})
                                    logo_text = f"{time_util.get_format_from_time(time.time())} | {parser_message}"
                                    q = global_setting.get_setting("queue", None)
                                    if q:
                                        q.put(
                                            ObjectQueueItem(origin="main_monitor_data", to="MainWindow_index",
                                                            title="mouse_cage_inner_module_running_state",
                                                            data=logo_text,
                                                            time=time_util.get_format_from_time(time.time())))
                                else:
                                    _report_health_failure(
                                        "monitor_collection",
                                        _health_scope_from_message(send_message),
                                        reason_code="modbus_read_failed",
                                        message=(
                                            f"slave_id={send_message.get('slave_id')}, "
                                            f"function_code={send_message.get('function_code')}"
                                        ),
                                        metrics=_health_metrics_from_message(
                                            send_message,
                                            response_seconds=round(end_time - start_time, 4),
                                        ),
                                    )
                                    # 将错误信息返回给主菜单
                                    if return_data:
                                        for data in return_data['data']:
                                            if data and data.get('desc') and data.get('desc') == '备注':
                                                q = global_setting.get_setting("queue", None)
                                                if q:
                                                    q.put(
                                                        ObjectQueueItem(origin="main_monitor_data", to="MainWindow_index",
                                                                        title="mouse_cage_inner_module_running_state",
                                                                        data=f"{time_util.get_format_from_time(time.time())} | {data.get('value')}",
                                                                        time=time_util.get_format_from_time(time.time())))
                                                break
                                    pass
                                return_data['time']= datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

                                result = store_data_with_result(return_data, need_result=True, timeout=5)
                                if result and result.success:
                                    logger.debug(f"数据存储成功{response_hex}，ID: {result.item_id}")
                                else:
                                    logger.error(f"数据{response_hex}存储失败: {result.error if result else '未知错误'}")
                                # logger.info(f"{total_messages_processed}|{return_data}")
                                    pass

                        except queue.Empty:
                            self.normal_queue_empty=True
                            break
                    except Exception as e:
                        logger.error(f"{AsyPromise._format_error_message('',e)}")
                    finally:
                        if self.normal_queue_empty or MESSAGE_BATCH_SIZE ==0:
                            continue
                        if send_message is not None:
                            logger.info(f"响应报文{total_messages_processed}/{MESSAGE_BATCH_SIZE}响应结束")
                            with lock:
                                if total_messages_processed % MESSAGE_BATCH_SIZE == 0:
                                    # barrier: threading.Barrier = global_setting.get_setting("barrier")
                                    # if barrier is not None:
                                    #     logger.debug(f"barrier_鼠笼内部传感器 run one batch done ! ")
                                    #     barrier.wait()
                                    total_messages_processed = 1
                                    MESSAGE_BATCH_SIZE = 0

                                    batch_complete_event.set()
                                else:
                                    total_messages_processed += 1
                        else:
                            #如果遇到未知错误，则跳过这条报文
                            logger.error(f"响应报文{total_messages_processed}/{MESSAGE_BATCH_SIZE}响应遇到未知错误，直接跳过这条报文并结束")
                            with lock:

                                if MESSAGE_BATCH_SIZE == 0 or total_messages_processed % MESSAGE_BATCH_SIZE == 0:
                                    # barrier: threading.Barrier = global_setting.get_setting("barrier")
                                    # if barrier is not None:
                                    #     logger.debug(f"barrier_鼠笼内部传感器 run one batch done ! ")
                                    #     barrier.wait()

                                    total_messages_processed = 1
                                    MESSAGE_BATCH_SIZE = 0
                                    batch_complete_event.set()
                                else:
                                    total_messages_processed += 1

                    time.sleep(float(global_setting.get_setting('monitor_data')['SEND']['delay']))
                except Exception as e:
                    error_msg = [f"{self.name} dosomething error: {e}"]
                    # 错误处理代码...
                    logger.error("\n".join(error_msg))
                    break

        except Exception as e:
            logger.error(f"{self.name} run() exception: {e}")
        finally:
            self._running = False
            logger.warning(f"{self.name} thread run() ended")


class Add_message_thread(MyQThread):
    def __init__(self,name,send_thread, port):
        super().__init__(name=name)
        self.send_thread = send_thread
        self.port=port
        self.mouse_cage_index=None
        self.weight_read_30_points = _weight_read_30_points_enabled()
        pass
    def run(self):
        logger.warning(f"{self.name} thread has been started！")
        logger.warning(
            "称重采集模式："
            f"{'30点' if self.weight_read_30_points else '单值'}，"
            "WM地址使用0x14系列"
        )
        self._running=True
        # 发送消息
        global MESSAGE_BATCH_SIZE,gids

        # 等待气路启动
        # wait_UFC_UGC_ZOS_start_event.wait()
        while self._running:
            self.mutex.lock()
            if self._paused:
                self.condition.wait(self.mutex)  # 等待条件变量
            self.mutex.unlock()

            if not self._running:
                break

            # Clear the previous notification before enqueueing the next batch.
            # The response worker sets it for both successful and failed
            # requests, so the collector can wait without an artificial timeout.
            batch_complete_event.clear()

            barrier = global_setting.get_setting("barrier")
            #拿到气路启动是否启动的状态
            sync_with_gas = barrier is not None and getattr(barrier, "parties", 1) > 1
            #如果气路 启动了就拿全局下标，否则就 拿自己的内部下标
            # current_mouse_cage_index = global_setting.get_setting("cage_number_list_index", None) if sync_with_gas else self.mouse_cage_index
            current_mouse_cage_index =copy.deepcopy(self.mouse_cage_index)
            #如果气路还没启动
            # if not sync_with_gas:
            #     global_setting.set_setting("cage_number_list_index", current_mouse_cage_index)


            send_messages = []
            # # 公共传感器数据的send_messages  现在只发传感器数值查询报文DEBUGGER
            # for data_type in Modbus_Slave_Type.Not_Each_Mouse_Cage_Message_Senior_Data.value:
            #     """debugger专用 需要哪个模块的数据监控就放进去"""
            #     if data_type in [
            #         Modbus_Slave_Send_Messages_Senior_Data.UFC,
            #         Modbus_Slave_Send_Messages_Senior_Data.UGC,
            #         Modbus_Slave_Send_Messages_Senior_Data.ZOS
            #     ]:
            #         # 所有消息
            #         for message_struct in data_type.value['send_messages']:
            #             message_temp = message_struct.message
            #             message_temp['port'] =  self.port
            #             self.send_thread.add_message(message=message_temp, urgent=False)
            #             send_messages.append(message_temp)
            #             MESSAGE_BATCH_SIZE += 1
            # 每个笼子里的传感器的send_messages
            for data_type in Modbus_Slave_Type.Each_Mouse_Cage_Message_Senior_Data.value:
                if not _cage_data_type_enabled(data_type):
                    continue
                """debugger专用 需要哪个模块的数据监控就放进去"""
                # logger.critical(f"data type : {data_type}")
                if data_type in [
                                Modbus_Slave_Send_Messages_Senior_Data.ENM,
                                  Modbus_Slave_Send_Messages_Senior_Data.EM,
                                  Modbus_Slave_Send_Messages_Senior_Data.DWM,
                                  Modbus_Slave_Send_Messages_Senior_Data.WM
                ]:
                    if current_mouse_cage_index is not None:
                        cage_number = gids[current_mouse_cage_index] if gids else 1
                        if not _channel_module_enabled(cage_number, data_type):
                            continue
                    # 所有消息
                    for message_struct in data_type.value['send_messages']:

                        message_temp = copy.deepcopy(message_struct.message)
                        message_temp['port'] =  self.port

                        # logger.critical(f"add_message_thread_mouse_cage_index:{self.mouse_cage_index}")
                        if current_mouse_cage_index is not None:

                            mouse_cage = gids[current_mouse_cage_index] if gids else 1
                            if data_type == Modbus_Slave_Send_Messages_Senior_Data.WM:
                                _configure_weight_read_message(
                                    message_temp,
                                    mouse_cage,
                                    self.weight_read_30_points,
                                )
                            message_temp['slave_id'] =copy.copy(format(int(message_temp['slave_id'], 16)+16*mouse_cage, '02X'))
                            send_messages.append({'message': message_temp})
                        else:
                            # Reference branch only sends ENM to avoid misrouting other cage modules.
                            if data_type == Modbus_Slave_Send_Messages_Senior_Data.ENM:
                                send_messages.append({'message': message_temp, 'type': 'reference_enm'})
                            else:
                                continue
                        MESSAGE_BATCH_SIZE += 1


                pass
            for msg in send_messages:
                self.send_thread.add_message(message=msg, urgent=False)
            if current_mouse_cage_index is not None:
                mouse_cage = gids[current_mouse_cage_index] if gids else 1
            else:
                mouse_cage = None
            #     # 等待从线程处理完当前批次
            logo_text = f"{time_util.get_format_from_time(time.time())} | 鼠笼{mouse_cage if mouse_cage is not None else '参考气'}发送鼠笼内模块数据请求报文：一共{len(send_messages)}条报文！"
            logger.info(logo_text)
            queue = global_setting.get_setting("queue", None)
            if queue:
                queue.put(
                    ObjectQueueItem(origin="main_monitor_data", to="MainWindow_index", title="mouse_cage_inner_module_running_state",
                                    data=logo_text,
                                    time=time_util.get_format_from_time(time.time())))
            # print(f"send_messages:{send_messages}")
            # 将鼠笼下标循环前移动
            # ★ 关键修复：在移动笼子索引之前，先把当前笼子索引同步给 barrier_action ？？？？？
            # if not sync_with_gas:
            #     global_setting.set_setting("cage_number_list_index", self.mouse_cage_index)
            #     if self.mouse_cage_index is not None:
            #         if self.mouse_cage_index == len(gids) - 1:
            #         # 最后一个鼠笼 则下一个为参考气路
            #             self.mouse_cage_index = None
            #         else:
            #             self.mouse_cage_index = self.mouse_cage_index + 1
            #         pass
            #     else:
            #     # 当前为参考气 则下一个为第一个鼠笼
            #         self.mouse_cage_index = 0
            #         pass

            global_setting.set_setting("cage_number_list_index", current_mouse_cage_index)
            if self.mouse_cage_index is not None:
                if self.mouse_cage_index == len(gids) - 1:
                # 最后一个鼠笼 则下一个为参考气路
                    self.mouse_cage_index = None
                else:
                    self.mouse_cage_index = self.mouse_cage_index + 1
                pass
            else:
            # 当前为参考气 则下一个为第一个鼠笼
                self.mouse_cage_index = 0
                pass
            if send_messages:
                batch_complete_event.wait()
                # Keep the Event reusable for the next batch.
                batch_complete_event.clear()
            # 在这里手动触发 barrier（只有自己一个线程，立刻触发 barrier_action）
            try:
                barrier = global_setting.get_setting("barrier")
                if barrier is not None:
                    barrier.wait(timeout=COLLECTION_BARRIER_TIMEOUT_SECONDS)
            except threading.BrokenBarrierError as exc:
                logger.critical(
                    "collection barrier failed; workers were released for recovery: "
                    f"cage_index={current_mouse_cage_index}, error={exc}"
                )
                try:
                    _drain_collection_stage_signals()
                    barrier.reset()
                    logger.warning(
                        "collection barrier reset completed: "
                        f"cage_index={current_mouse_cage_index}"
                    )
                except Exception as reset_error:
                    logger.critical(
                        f"collection barrier reset failed: {reset_error}"
                    )

            logger.info(f"从线程已处理完上批消息，主线程继续发送下一批\n")

            # time.sleep(5)









DEFAULT_EXPERIMENT_CONFIG_STEM = "last_experiment_config"


def get_monitor_data_experiment_name(experiment_setting_file):
    if experiment_setting_file is not None and os.path.exists(experiment_setting_file):
        experiment_name = os.path.splitext(os.path.basename(experiment_setting_file))[0]
        if experiment_name == DEFAULT_EXPERIMENT_CONFIG_STEM:
            return ""
        return experiment_name
    return ""


def build_monitor_data_experiment_folder_name(experiment_setting_file, start_time):
    experiment_name = get_monitor_data_experiment_name(experiment_setting_file)
    timestamp = time_util.get_format_file_from_time(start_time)
    return f"{experiment_name}_{timestamp}" if experiment_name else timestamp


def _get_monitor_data_experiment_name():
    return get_monitor_data_experiment_name(
        global_setting.get_setting("experiment_setting_file", None)
    )


def get_monitor_data_experiment_dir():
    experiment_root_path = global_setting.get_setting("experiment_root_path", None)
    if experiment_root_path:
        return os.path.abspath(experiment_root_path)
    monitor_data_config = global_setting.get_setting('monitor_data')
    storage_config = monitor_data_config['STORAGE']
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    folder_name = build_monitor_data_experiment_folder_name(
        global_setting.get_setting("experiment_setting_file", None),
        global_setting.get_setting('start_experiment_time', time.time()),
    )
    return os.path.abspath(
        os.path.join(
            project_root,
            storage_config['fold_path'],
            str(storage_config['sub_fold_path']).strip("/\\"),
            folder_name
        )
    )


def get_monitor_data_db_file_path():
    return os.path.join(get_monitor_data_experiment_dir(), "data", "data.db")


def get_monitor_data_export_base_path():
    experiment_dir = get_monitor_data_experiment_dir()
    return os.path.join(os.path.dirname(experiment_dir), os.path.basename(experiment_dir))


def copy_experiment_setting_file():
    #将实验配置存储到该实验的文件夹中去
    # 将实验设置存入全局变量
    experiment_setting_file=global_setting.get_setting("experiment_setting_file", None)
    if experiment_setting_file is not None and  os.path.exists(experiment_setting_file):
        # 获取文件所在的文件夹路径
        folder_path = os.path.dirname(experiment_setting_file)
        # 获取文件名称
        file_name = os.path.basename(experiment_setting_file)
        # 不带扩展名的文件名称
        file_name_without_extension = os.path.splitext(file_name)[0]
        #获取文件的扩展名
        file_name_extension = os.path.splitext(file_name)[1]
        # 定义文件夹路径
        folder_path_copy = os.path.join(get_monitor_data_experiment_dir(), "experiment_setting")

        # 创建文件夹（如果不存在）
        os.makedirs(folder_path_copy, exist_ok=True)

        source_file = experiment_setting_file
        destination_file=os.path.join(folder_path_copy,f"experiment_setting{file_name_extension}")
        # 复制文件并保留元数据
        shutil.copy2(source_file, destination_file)
        pass
    pass
# 线程
ufc_ugc_zos=None
ufc_ugc_zos_thread:UFC_UGC_ZOS_index =None
# 存储线程
store_thread:Store_Thread = None
periodic_xlsx_export_thread: PeriodicXlsxExportThread = None


LIGHTING_COMMAND_MAX_ATTEMPTS = 3
LIGHTING_COMMAND_RESULT_TIMEOUT_SECONDS = 5.0
LIGHTING_COMMAND_RETRY_DELAY_MS = 200


class LightingScheduleThread(QThread):
    def __init__(self, schedule, cages, sender, parent=None):
        super().__init__(parent)
        self._lock = threading.RLock()
        self._schedule = normalize_lighting_schedule(schedule)
        self._cages = _parse_mouse_cage_numbers(cages)
        self._sender = sender
        self._running = True
        self._force_apply = True
        self._last_signature = None
        self._applied_states = {}

    def set_schedule(self, schedule, apply_now=False):
        with self._lock:
            self._schedule = normalize_lighting_schedule(schedule)
            if apply_now:
                self._force_apply = True
                self._last_signature = None

    def stop(self):
        with self._lock:
            self._running = False
        self.requestInterruption()
        self.wait(3000)

    def run(self):
        logger.info(f"lighting schedule started: cages={self._cages}")
        while not self.isInterruptionRequested():
            with self._lock:
                if not self._running:
                    break
                schedule = copy.deepcopy(self._schedule)
                force_apply = self._force_apply
                self._force_apply = False
            try:
                state = resolve_lighting_state(schedule, datetime.now())
                if state is not None:
                    signature = (
                        state["stage_key"],
                        state["power"],
                        state["color_temperature"],
                        state["brightness"],
                    )
                    if force_apply or signature != self._last_signature:
                        # A partial device failure is considered handled for
                        # this stage after its bounded retries.  Do not retry
                        # the entire stage every second and starve acquisition.
                        if self._apply_state(state):
                            self._last_signature = signature
            except Exception:
                logger.exception("lighting schedule evaluation failed")
            self.msleep(1000)
        logger.info("lighting schedule stopped")

    def _apply_state(self, state):
        if self._sender is None or not self._sender.isRunning():
            logger.warning("lighting schedule skipped: send thread unavailable")
            return False
        if getattr(self._sender, "modbus", None) is None:
            logger.warning("lighting schedule waiting: modbus unavailable")
            return False
        target_state = {
            "power": bool(state["power"]),
            "color_temperature": int(state["color_temperature"]),
            "brightness": int(state["brightness"]),
        }
        attempted_sends = 0
        successful_cages = []
        failed_cages = []
        for cage in self._cages:
            commands = build_cage_light_state_change_commands(
                cage,
                state["power"],
                state["color_temperature"],
                state["brightness"],
                port=global_setting.get_setting("port", None),
                previous_state=self._applied_states.get(cage),
            )
            cage_success = True
            for command in commands:
                command_success, attempts = self._send_command_with_retry(cage, command)
                attempted_sends += attempts
                if not command_success:
                    cage_success = False
                    break
            if cage_success:
                self._applied_states[cage] = dict(target_state)
                save_cage_light_state(
                    [cage],
                    state["power"],
                    state["color_temperature"],
                    state["brightness"],
                )
                successful_cages.append(cage)
            else:
                # Some earlier steps may already have reached the hardware;
                # mark it unknown so the next stage sends a complete sequence.
                self._applied_states.pop(cage, None)
                failed_cages.append(cage)
        logger.info(
            "lighting schedule applied: "
            f"stage={state['stage_key']}, cages={self._cages}, power={state['power']}, "
            f"color_temperature={state['color_temperature']}, brightness={state['brightness']}, "
            f"successful_cages={successful_cages}, failed_cages={failed_cages}, "
            f"attempted_sends={attempted_sends}"
        )
        return True

    def _send_command_with_retry(self, cage, command):
        step = command.get("switch_step", "unknown")
        for attempt in range(1, LIGHTING_COMMAND_MAX_ATTEMPTS + 1):
            if self.isInterruptionRequested():
                return False, attempt - 1
            result_queue = queue.Queue(maxsize=1)
            queued_command = dict(command)
            queued_command["data"] = list(command.get("data", []))
            queued_command["_internal_result_queue"] = result_queue
            self._sender.add_message(
                message={"message": queued_command},
                urgent=True,
                origin="lighting_schedule",
            )
            deadline = time.monotonic() + LIGHTING_COMMAND_RESULT_TIMEOUT_SECONDS
            result = None
            while not self.isInterruptionRequested():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    result = result_queue.get(timeout=min(0.1, remaining))
                    break
                except queue.Empty:
                    continue

            if result is not None and result.get("success"):
                logger.info(
                    "lighting command succeeded: "
                    f"cage={cage}, step={step}, attempt={attempt}, "
                    f"response={result.get('response_hex', '')}"
                )
                return True, attempt

            failure_detail = "等待设备回执超时" if result is None else (
                result.get("detail") or result.get("response_hex") or "设备未返回有效响应"
            )
            logger.warning(
                "lighting command failed: "
                f"cage={cage}, step={step}, attempt={attempt}/{LIGHTING_COMMAND_MAX_ATTEMPTS}, "
                f"detail={failure_detail}"
            )
            if attempt < LIGHTING_COMMAND_MAX_ATTEMPTS:
                self.msleep(LIGHTING_COMMAND_RETRY_DELAY_MS)

        logger.error(
            "lighting command abandoned after bounded retries: "
            f"cage={cage}, step={step}, attempts={LIGHTING_COMMAND_MAX_ATTEMPTS}"
        )
        return False, LIGHTING_COMMAND_MAX_ATTEMPTS


lighting_schedule_thread: LightingScheduleThread = None
# 发送报文线程
send_thread :Send_thread= None
add_message_thread:Add_message_thread = None
_shutdown_lights_off_done = False














#一轮模块发送报文结束
def get_epoch_mouse_cage_index():
    mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
    mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
    barrier = global_setting.get_setting("barrier")
    parties = getattr(barrier, "parties", 1) if barrier is not None else 1

    if not mouse_cages_inc:
        return mouse_cage_index

    if parties <= 1:
        return mouse_cage_index

    if mouse_cage_index is None:
        return len(mouse_cages_inc) - 1
    if mouse_cage_index == 0:
        return None
    return mouse_cage_index - 1


def _normalize_epoch_query_values(results):
    """Collapse repeated sensor readings to the latest real value per epoch.

    The query returns a list when a module wrote more than one row in the
    interval. Infrared needs the whole series for its maximum, and weighing
    needs the whole series for its existing 30-point storage format.
    """
    normalized = {}
    for key, value in results.items():
        if not isinstance(value, list):
            normalized[key] = value
        elif key.endswith(("__tmp_hs_max", "__tmp_hs_mean")):
            normalized[key] = value
        elif key.startswith("WM_monitor_data_cage_") and key.endswith("__weight_num"):
            normalized[key] = format_weight_series_for_storage(value)
        else:
            normalized[key] = next((item for item in reversed(value) if item is not None), None)
    return normalized


def barrier_action():
    _drain_collection_stage_signals()
    query_end_limit = time.time()
    mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
    mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
    logger.critical(f"barrier action run :mouse_cage_index before:{mouse_cage_index}")
    # 因为在zos运行完之后就更新了mouse_cage_index,所以现在得到的index是比上一轮多1的，所以需要往回退1
    # if mouse_cage_index is  None:
    #     mouse_cage_index= len(mouse_cages_inc)-1
    #     pass
    # elif mouse_cage_index ==0:
    #     mouse_cage_index=None
    #     pass
    # else:
    #     mouse_cage_index=mouse_cage_index-1
    #     pass
    # logger.critical(f"barrier action  run :mouse_cage_index after:{mouse_cage_index}")
    mouse_cage_number = mouse_cages_inc[mouse_cage_index] if mouse_cage_index is not None else int(global_setting.get_setting('configer')['mouse_cage']['reference'])


    handle = Monitor_Datas_Handle()  # # 创建数据库操作器
    epoch_query_plan = handle.get_epoch_query_plan(mouse_cage_number)
    start_time = global_setting.get_setting("start_time_messages_sent_epoch_for_running", time.time())
    actual_end_time = handle.query_epoch_actual_end_time(
        start_time,
        query_end_limit,
        table_names=epoch_query_plan,
    )
    end_time = actual_end_time if actual_end_time is not None else start_time

    logo_text =f"{time_util.get_format_from_time(time.time())} | 一轮结束|结束时间：{time_util.get_format_from_time(end_time)}|开始时间：{time_util.get_format_from_time(start_time)}|用时：{time_util.format_timedelta(a= datetime.fromtimestamp(end_time),b= datetime.fromtimestamp(start_time),signed=True,zero_pad=True)}|一轮传感器发送报文结束|期间一共发送{ global_setting.get_setting('messages_sent_epoch_for_running', 0)}条报文。"
    logger.warning(logo_text)
    q = global_setting.get_setting("queue", None)
    if q:
        q.put(
            ObjectQueueItem(origin="main_monitor_data", to="MainWindow_index",
                            title="epoch_running_state",
                            data=logo_text,
                            time=time_util.get_format_from_time(time.time())))
    # 去数据库里查询 所有的在这个时间段的数据
    results, columns = handle.query_data_in_line_with_epoch_data(
        start_time,
        end_time,
        start_exclusive=True,
        table_columns=epoch_query_plan,
    )
    results = _normalize_epoch_query_values(results)
    weight_window = None
    if _weight_read_30_points_enabled():
        weight_window, weight_window_backlog = _pop_epoch_weight_window(mouse_cage_number)
        if weight_window is not None:
            epoch_weight_value = format_weight_series_for_storage(weight_window.values)
            weight_window_start_text = datetime.fromtimestamp(weight_window.start_time).strftime(
                '%Y-%m-%d %H:%M:%S'
            )
            weight_window_end_text = datetime.fromtimestamp(weight_window.end_time).strftime(
                '%Y-%m-%d %H:%M:%S'
            )
            logger.info(
                "Epoch写入称重30秒窗口："
                f"笼子{mouse_cage_number}，窗口={weight_window_start_text}~{weight_window_end_text}，"
                f"真实={len(weight_window.values) - weight_window.missing_points}点，"
                f"缺失={weight_window.missing_points - weight_window.padding_points}点，"
                f"尾部待下一轮回填={weight_window.padding_points}点"
            )
            if weight_window_backlog:
                logger.warning(
                    "称重30秒窗口存在积压："
                    f"笼子{mouse_cage_number}，待写入={weight_window_backlog}个窗口"
                )
        else:
            epoch_weight_value = None
            logger.debug(f"Epoch暂无新增称重数据：笼子{mouse_cage_number}")
    else:
        epoch_weight_value = results.get(
            f'WM_monitor_data_cage_{mouse_cage_number}__weight_num'
        )
    # logger.critical(f"{results}")
    store_Datas =[]
    # store_Datas.append({'desc':'序号','value':None})
    store_Datas.append({'desc':'鼠笼号','value':mouse_cage_number})
    # 为什么还要判断一下，因为有时候results.get('xxxx')的值为[]
    store_Datas.append({'desc': '氧浓度0点校准值',
                        'value': results.get('ZeroCalibration_data__oxygen_calibration_zero_value') if results.get(
                            'ZeroCalibration_data__oxygen_calibration_zero_value') is not None else None}  )
    store_Datas.append({'desc': 'ZOS压力0点校准值',
                        'value': results.get('ZeroCalibration_data__zos_pressure_calibration_zero_value') if results.get(
                            'ZeroCalibration_data__zos_pressure_calibration_zero_value') is not None else None})
    store_Datas.append({'desc': '氧浓传感器span数值',
                        'value': results.get('SpanCalibration_data__oxygen_calibration_span_value') if results.get(
                            'SpanCalibration_data__oxygen_calibration_span_value') is not None else None})
    store_Datas.append({'desc': 'ZOS压力span数值',
                        'value': results.get('SpanCalibration_data__zos_pressure_calibration_span_value') if results.get(
                            'SpanCalibration_data__zos_pressure_calibration_span_value') is not None else None})
    store_Datas.append({'desc': '二氧化碳浓传感器span数值',
                        'value': results.get(
                            'SpanCalibration_data__carbon_calibration_span_value') if results.get(
                            'SpanCalibration_data__carbon_calibration_span_value') is not None else None})
    store_Datas.append({'desc': 'ufc_流量计测量值(sccm)', 'value': results.get(f'UFC_monitor_data_cage_{mouse_cage_number}__flow_num') if results.get(
                            f'UFC_monitor_data_cage_{mouse_cage_number}__flow_num') is not None else None   })
    store_Datas.append({'desc': '传感器状态', 'value': results.get(
        f'UGC_monitor_data_cage_{mouse_cage_number}__flow_num_1') if results.get(
        f'UGC_monitor_data_cage_{mouse_cage_number}__flow_num_1') is not None else None})
    co2_origin_num = results.get(f'UGC_monitor_data_cage_{mouse_cage_number}__CO2_num') if results.get(
        f'UGC_monitor_data_cage_{mouse_cage_number}__CO2_num') is not None else None
    gas_pressure = results.get(f'ZOS_monitor_data_cage_{mouse_cage_number}__gas_pressure') if results.get(
        f'ZOS_monitor_data_cage_{mouse_cage_number}__gas_pressure') is not None else None
    standard_atmospheric_pressure = float(
        global_setting.get_setting('UFC_UGC_ZOS_config')['PARAM']['standard_atmospheric_pressure'])

    co2_num = co2_origin_num
    if co2_origin_num is not None and gas_pressure is not None and gas_pressure != 0:
        co2_num = round(co2_origin_num * standard_atmospheric_pressure / gas_pressure, 4)

    fitted_co2_num = None
    if co2_num is not None:
        fitted_co2_ppm = _co2_realtime_compensator.compensate(
            _channel_number_to_co2_channel_name(mouse_cage_number),
            float(co2_num) * 10000
        )
        fitted_co2_num = round(float(fitted_co2_ppm) / 10000, 4)

    store_Datas.append({'desc': '拟合后CO2', 'value': fitted_co2_num})
    store_Datas.append({'desc': '补偿前CO2', 'value': co2_origin_num})
    store_Datas.append({'desc': '气压补偿后CO2', 'value': co2_num})
    store_Datas.append({'desc': '氧分压(hPa)',
                        'value': results.get(
                            f'ZOS_monitor_data_cage_{mouse_cage_number}__oxygen_partial_pressure') if results.get(
                            f'ZOS_monitor_data_cage_{mouse_cage_number}__oxygen_partial_pressure') is not None else None})
    store_Datas.append({'desc': 'ZOS温度测量值(°C)',
                        'value': results.get(
                            f'ZOS_monitor_data_cage_{mouse_cage_number}__zos_temperature_num') if results.get(
                            f'ZOS_monitor_data_cage_{mouse_cage_number}__zos_temperature_num') is not None else None})
    store_Datas.append({'desc': '气体压力(hPa)',
                        'value': results.get(f'ZOS_monitor_data_cage_{mouse_cage_number}__gas_pressure') if results.get(
                            f'ZOS_monitor_data_cage_{mouse_cage_number}__gas_pressure') is not None else None})
    store_Datas.append({'desc': '氧浓度(%)',
                        'value': results.get(f'ZOS_monitor_data_cage_{mouse_cage_number}__oxygen_num') if results.get(
                            f'ZOS_monitor_data_cage_{mouse_cage_number}__oxygen_num') is not None else None})
    store_Datas.append({'desc': '干基氧浓度(%)',
                        'value': results.get(f'ZOS_monitor_data_cage_{mouse_cage_number}__dry_basis_oxygen_num') if results.get(
                            f'ZOS_monitor_data_cage_{mouse_cage_number}__dry_basis_oxygen_num') is not None else None})
    store_Datas.append({'desc': 'ZOS故障码',
                        'value': results.get(f'ZOS_monitor_data_cage_{mouse_cage_number}__fault_code') if results.get(
                            f'ZOS_monitor_data_cage_{mouse_cage_number}__fault_code') is not None else None})
    store_Datas.append({'desc': 'ZOS温度2测量值(°C)',
                        'value': results.get(f'ZOS_monitor_data_cage_{mouse_cage_number}__oxygen_temperature_2_num') if results.get(
                            f'ZOS_monitor_data_cage_{mouse_cage_number}__oxygen_temperature_2_num') is not None else None})
    store_Datas.append({'desc': 'ZOS湿度测量值(%RH)',
                        'value': results.get(f'ZOS_monitor_data_cage_{mouse_cage_number}__oxygen_humidity_num') if results.get(
                            f'ZOS_monitor_data_cage_{mouse_cage_number}__oxygen_humidity_num') is not None else None})




    reference_data = None
    adjusted_reference_co2 = None
    if mouse_cage_number != int(global_setting.get_setting('configer')['mouse_cage']['reference']):
        # 获取参考气轮次的数据：
        reference_data = handle.query_current_one_data(table_name=f"Epoch_data_cage_{int(global_setting.get_setting('configer')['mouse_cage']['reference'])}")
        if reference_data is not None:
            adjusted_reference_co2 = adjust_reference_co2(
                reference_data.get('UGC_CO2_num'),
                _reference_co2_subtract_value(),
            )
            if adjusted_reference_co2 is not None:
                adjusted_reference_co2 = round(adjusted_reference_co2, 4)
            store_Datas.append(
                {'desc': 'ufc_参考气流量计测量值(sccm)',
                 'value': reference_data.get(f'UFC_flow_num') if reference_data.get(
                            f'UFC_flow_num') is not None else None })
            store_Datas.append(
                {'desc': '参考气CO2(%)',
                 'value': adjusted_reference_co2})

            store_Datas.append(
                {'desc': '参考气氧浓度(%)',
                 'value': reference_data.get(f'ZOS_oxygen_num') if reference_data.get(
                     f'ZOS_oxygen_num') is not None else None})
            store_Datas.append(
                {'desc': '参考气干基氧浓度(%)',
                 'value': reference_data.get('ZOS_dry_basis_oxygen_num')})

            if fitted_co2_num is not None and adjusted_reference_co2 is not None:


                store_Datas.append(
                    {'desc': 'CO2生产量(%)',
                     'value': round(fitted_co2_num - adjusted_reference_co2, 4)})
            sample_dry_basis_oxygen = results.get(
                f'ZOS_monitor_data_cage_{mouse_cage_number}__dry_basis_oxygen_num'
            )
            reference_dry_basis_oxygen = reference_data.get('ZOS_dry_basis_oxygen_num')
            if sample_dry_basis_oxygen is not None and reference_dry_basis_oxygen is not None:



                store_Datas.append(
                    {'desc': '耗氧量(%)',
                     'value': round(reference_dry_basis_oxygen - sample_dry_basis_oxygen, 4)})


            pass

    metabolism_values = calculate_epoch_metabolism({
        'UFC_flow_num': results.get(f'UFC_monitor_data_cage_{mouse_cage_number}__flow_num'),
        'reference_dry_basis_oxygen_num': (
            reference_data.get('ZOS_dry_basis_oxygen_num') if reference_data else None
        ),
        'reference_CO2_num': adjusted_reference_co2,
        'ZOS_dry_basis_oxygen_num': results.get(
            f'ZOS_monitor_data_cage_{mouse_cage_number}__dry_basis_oxygen_num'
        ),
        # VCO2 的当前笼出口 CO2 使用 Air 校准后的拟合值。
        'UGC_CO2_num': fitted_co2_num,
    })
    for column_name, description in METABOLISM_COLUMNS:
        store_Datas.append({'desc': description, 'value': metabolism_values[column_name]})

    # 本笼自己的环境/红外/称重类数据，参考笼和普通笼都应写入总表
    temp_values = results.get(f'MouseInfrared_data_cage_{mouse_cage_number}__tmp_hs_max', None)
    if temp_values is None:
        temp_values = results.get(f'MouseInfrared_data_cage_{mouse_cage_number}__tmp_hs_mean', None)
    if temp_values is not None:
        infrared_temp_max = None
        # 过滤掉None值
        if type(temp_values) is list:
            filter_temp_values = [x for x in temp_values if x is not None]
            if len(filter_temp_values) != 0:
                infrared_temp_max = round(max(filter_temp_values), 4)
        else:
            infrared_temp_max = temp_values
        store_Datas.append({'desc': '鼠笼红外温度(°C)',
                            'value': infrared_temp_max})

    store_Datas.append(
        {'desc': '温度测量值(°C)', 'value': results.get(f'ENM_monitor_data_cage_{mouse_cage_number}__temperature_num') if results.get(
                        f'ENM_monitor_data_cage_{mouse_cage_number}__temperature_num') is not None else None })
    store_Datas.append(
        {'desc': '湿度测量值(%RH)', 'value': results.get(f'ENM_monitor_data_cage_{mouse_cage_number}__humidity_num') if results.get(
                        f'ENM_monitor_data_cage_{mouse_cage_number}__humidity_num') is not None else None })
    store_Datas.append(
        {'desc': '噪声测量值(dB)', 'value': results.get(f'ENM_monitor_data_cage_{mouse_cage_number}__noise_num')  if results.get(
                        f'ENM_monitor_data_cage_{mouse_cage_number}__noise_num') is not None else None})
    store_Datas.append({'desc': '大气压测量值(KPa)',
                        'value': results.get(f'ENM_monitor_data_cage_{mouse_cage_number}__barometer_num') if results.get(
                        f'ENM_monitor_data_cage_{mouse_cage_number}__barometer_num') is not None else None})
    store_Datas.append({'desc': '当前计量周期内跑轮圈数测量值',
                        'value': results.get(f'ENM_monitor_data_cage_{mouse_cage_number}__running_wheel_num')  if results.get(
                        f'ENM_monitor_data_cage_{mouse_cage_number}__running_wheel_num') is not None else None})
    store_Datas.append(
        {'desc': '食物重量测量值(g)', 'value': results.get(f'DWM_monitor_data_cage_{mouse_cage_number}__weight_num')  if results.get(
                        f'DWM_monitor_data_cage_{mouse_cage_number}__weight_num') is not None else None})
    store_Datas.append(
        {'desc': '饮水重量测量值(g)', 'value':results.get(f'EM_monitor_data_cage_{mouse_cage_number}__weight_num') if results.get(
                        f'EM_monitor_data_cage_{mouse_cage_number}__weight_num') is not None else None })
    store_Datas.append(
        {'desc': '称重重量测量值(g)', 'value': epoch_weight_value})

    store_Datas.append({'desc':'轮次开始时间','value':datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]})
    store_Datas.append({'desc':'轮次结束时间','value':datetime.fromtimestamp(end_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]})
    # 获得所有备注
    # logger.critical(f"rs:{results}")
    remarks = "".join(f" {key}: {value}; " for key, value in results.items()
                            if "remarks" in key and value is not None and value != [])
    store_Datas.append({'desc':'备注','value':remarks})
    # logger.critical(f"sd:{store_Datas}")
    # store_Datas.append({'desc':'获取时间','value':datetime.now().fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M:%S')})
    handle.stop()
    # 装载数据
    # 存储值----------------------------------------------------

    return_data_struct = {}
    return_data_struct['module_name'] = 'Epoch'
    return_data_struct['table_name'] = next(iter(Others_Tables.Epoch_Data.value.keys()))
    return_data_struct['mouse_cage_number'] = mouse_cage_number
    return_data_struct['data'] = store_Datas
    return_data_struct['slave_id'] = 0
    return_data_struct['time']=datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
    return_data_struct['function_code'] = 0
    if weight_window is not None:
        assembler = _get_weight_window_assembler()
        return_data_struct['_weight_window_state'] = {
            'cage_number': int(mouse_cage_number),
            'origin_time': assembler.origin_time,
            'start_time': weight_window.start_time,
            'end_time': weight_window.end_time,
            'resolved_points': len(weight_window.values) - weight_window.padding_points,
        }
    # 存到分表
    result = store_data_with_result(return_data_struct, need_result=True, timeout=5)
    if result and result.success:
        logger.info(f"epoch_result数据存储成功，ID: {result.item_id}")
    else:
        logger.error(f"epoch_result数据存储失败: {result.error if result else '未知错误'}")
    # 存到总表
    return_data_struct_all = copy.deepcopy(return_data_struct)
    # The aggregate row uses cage_number=-1, so only the per-cage row can own
    # this backfill marker. Carrying it into the aggregate insert would make
    # the metadata lookup fail and reject the aggregate write.
    return_data_struct_all.pop('_weight_window_state', None)
    return_data_struct_all['table_name'] = f"{next(iter(Others_Tables.Epoch_Data.value.keys()))}_all"
    return_data_struct_all['mouse_cage_number'] =-1
    result_all = store_data_with_result(return_data_struct_all, need_result=True, timeout=5)
    if result_all and result_all.success:
        logger.info(f"epoch_result_all数据存储成功，ID: {result_all.item_id}")
    else:
        logger.error(f"epoch_result_all数据存储失败: {result_all.error if result_all else '未知错误'}")
    global_setting.set_setting("start_time_messages_sent_epoch_for_running", end_time)
    global_setting.set_setting("messages_sent_epoch_for_running",
                               0)


_raw_store_data_with_result = store_data_with_result


def _patch_epoch_store_payload(return_data_struct):
    if return_data_struct.get("module_name") != "Epoch":
        return return_data_struct

    payload = copy.deepcopy(return_data_struct)
    data = payload.get("data")
    if not isinstance(data, list) or len(data) < 8:
        return payload

    ufc_flow_index = None
    oxygen_partial_index = None
    for index, item in enumerate(data):
        desc = str(item.get("desc", ""))
        if ufc_flow_index is None and "sccm)" in desc and "参考" not in desc:
            ufc_flow_index = index
        if ufc_flow_index is not None and index > ufc_flow_index and "hPa)" in desc:
            oxygen_partial_index = index
            break

    if ufc_flow_index is None or oxygen_partial_index is None or oxygen_partial_index <= ufc_flow_index:
        return payload

    ugc_block = data[ufc_flow_index + 1:oxygen_partial_index]
    status_value = None
    status_raw_value = None
    raw_co2 = None
    pressure_compensated_co2 = None
    fitted_co2 = None
    numeric_values = []
    for item in ugc_block:
        desc = str(item.get("desc", "")).strip()
        value = item.get("value")
        if desc in {"传感器状态码", "传感器状态"}:
            status_raw_value = value
            status_value = value
        elif desc == "补偿前CO2":
            raw_co2 = value
        elif desc == "气压补偿后CO2":
            pressure_compensated_co2 = value
        elif desc in {"对齐后CO2", "拟合后CO2"}:
            fitted_co2 = value
        elif isinstance(value, (int, float)):
            numeric_values.append(value)

    # Compatibility fallback for payloads created by older versions.
    if status_value is None:
        for item in ugc_block:
            value = item.get("value")
            if isinstance(value, str):
                status_raw_value = value
                status_value = value
                break
    if pressure_compensated_co2 is None and numeric_values:
        pressure_compensated_co2 = numeric_values[-1]
    if raw_co2 is None and len(numeric_values) >= 2:
        raw_co2 = numeric_values[-2]

    channel_number = payload.get("mouse_cage_number")
    if channel_number in (None, -1) and data:
        channel_number = data[0].get("value")

    if fitted_co2 is None and pressure_compensated_co2 is not None and channel_number is not None:
        fitted_ppm = _co2_realtime_compensator.compensate(
            _channel_number_to_co2_channel_name(channel_number),
            float(pressure_compensated_co2) * 10000
        )
        fitted_co2 = round(float(fitted_ppm) / 10000, 4)

    rebuilt_data = list(data[:ufc_flow_index + 1])
    rebuilt_data.extend([
        {"desc": "传感器状态", "value": status_raw_value if status_raw_value is not None else status_value},
        {"desc": "拟合后CO2", "value": fitted_co2},
        {"desc": "补偿前CO2", "value": raw_co2},
        {"desc": "气压补偿后CO2", "value": pressure_compensated_co2},
    ])
    rebuilt_data.extend(data[oxygen_partial_index:])
    payload["data"] = rebuilt_data
    return _apply_epoch_carry_forward(payload)


def store_data_with_result(return_data_struct, need_result=False, timeout=None):
    payload = _patch_epoch_store_payload(return_data_struct)
    return _raw_store_data_with_result(payload, need_result=need_result, timeout=timeout)


# def after_run_of_ufc_ugc_zos_barrier_action():
#     #ufc ugc zos run完后在鼠笼内run
#     # 通知鼠笼传感器解除阻塞开始运行
#     wait_UFC_UGC_ZOS_start_event = global_setting.get_setting("wait_UFC_UGC_ZOS_start_event")
#     if wait_UFC_UGC_ZOS_start_event is not None:
#         wait_UFC_UGC_ZOS_start_event.set()
#         wait_UFC_UGC_ZOS_start_event.clear()  # 重置事件
def main(q, send_message_q, health_snapshot_queue=None,
         emergency_shutdown_request=None,
         emergency_shutdown_complete=None,
         emergency_shutdown_result=None):

    # logger.remove(0)
    # 加载日志配置
    # logger.add(
    #     "./log/monitor_data/monitor_{time:YYYY-MM-DD}.log",
    #     rotation="00:00",
    #     retention="30 days",
    #     enqueue=True,
    #     format="{time:YYYY-MM-DD HH:mm:ss} | {level} |{process.name} | {thread.name} |  {name} : {module}:{line} | {message}",
    #
    # )
    logger.info(f"{'-' * 30}monitor_data_start{'-' * 30}")
    logger.info(f"{__name__} | {os.path.basename(__file__)}|{os.getpid()}|{os.getppid()}")
    _set_health_snapshot_queue(health_snapshot_queue)
    app = QCoreApplication(sys.argv)
    # 设置全局变量
    global_load.load_global_setting_without_Qt()
    # 初始化串口锁
    global_setting.set_setting("serial_lock", threading.Lock())
    # 当前鼠笼号列表的下标 参考气的下标为None 注意区分
    global_setting.set_setting("cage_number_list_index", None)
    global_setting.set_setting("queue", q)
    global_setting.set_setting("send_message_queue", send_message_q)
    global_setting.set_setting("ufc_emergency_shutdown_active", False)
    _start_ufc_emergency_shutdown_watcher(
        emergency_shutdown_request,
        emergency_shutdown_complete,
        emergency_shutdown_result,
    )
    #设置线程屏障，等待4个线程 ufc ugc zos的run还有鼠笼内的模块线程的send_message_thread
    # barrier专门用于多个线程需要在某个点同步等待的场景。每个线程执行完自己的工作后调用
    # barrier.wait()，当所有线程都到达这个同步点时，它们会同时继续执行下一轮循环。
    # barrier = ActionCompleteBarrier(4,action=barrier_action)
    barrier = DynamicBarrier(
        1,
        action=barrier_action,
        timeout=COLLECTION_BARRIER_TIMEOUT_SECONDS,
    )
    global_setting.set_setting("barrier", barrier)
    #专属于ufc ugc zos 的run的barrier
    # ufc_ugc_zos_barrier =ActionCompleteBarrier(3,action=after_run_of_ufc_ugc_zos_barrier_action)
    # global_setting.set_setting("ufc_ugc_zos_barrier", ufc_ugc_zos_barrier)
    #每轮运行发送报文数量 总的 在气路启动后和一轮结束后会重新赋值0
    global_setting.set_setting("messages_sent_epoch_for_running",0)
    #每轮运行开始的时间 在气路启动后和一轮结束后会重新赋值
    global_setting.set_setting("start_time_messages_sent_epoch_for_running",time.time())
    global read_queue_data_thread,send_thread, _shutdown_lights_off_done
    _shutdown_lights_off_done = False

    read_queue_data_thread.queue = send_message_q

    read_queue_data_thread.start()

    # 发送报文线程
    send_thread = Send_thread(name="monitor_data_send_message")
    send_thread.start()
    read_queue_data_thread.send_thread = send_thread

    # return store_thread,send_thread,read_queue_data_thread,add_message_thread,ufc_ugc_zos,ufc_ugc_zos_thread
    # 系统退出
    return app.exec()
def start():
    logger.info(f"{'-' * 30}monitor_data_run{'-' * 30}")
    global MESSAGE_BATCH_SIZE, total_messages_processed,gids
    MESSAGE_BATCH_SIZE = 0
    total_messages_processed = 1
    _reset_collection_sync_signals()
    _reset_weight_window_assembler()
    # 当前鼠笼号列表的下标 参考气的下标为None 注意区分
    global_setting.set_setting("cage_number_list_index", None)
    # 通道
    experiment_settings = global_setting.get_setting("experiment_setting", None)
    gids = [group.id for group in experiment_settings.groups if
            group.is_selected == 1] if experiment_settings is not None else []
    # UFC_UGC_ZOS
    global ufc_ugc_zos_thread, ufc_ugc_zos,store_thread,send_thread,add_message_thread, periodic_xlsx_export_thread, lighting_schedule_thread, _shutdown_lights_off_done, _shutdown_food_trough_current_off_done
    _shutdown_lights_off_done = False
    _shutdown_food_trough_current_off_done = False
    ufc_ugc_zos_thread = None
    ufc_ugc_zos = None

    # 实验开始时，按实际启用的笼子逐个关闭食槽门驱动电流。
    _queue_food_trough_current_off_commands(
        cages=gids,
        reason="实验启动",
    )

    try:
        # ufc_ugc_zos = UFC_UGC_ZOS_index()
        # ufc_ugc_zos.auto_btn_handle()
        if _environment_module_only_enabled():
            logger.warning(
                "Environment-only mode enabled: skip UFC/UGC/ZOS gas path startup"
            )
        else:
            ufc_ugc_zos_thread= UFC_UGC_ZOS_index()
            ufc_ugc_zos_thread.start()

    except Exception as ex:
        logger.exception(f"气路模块进程启动失败：{ex}")
        _send_main_window_status("start_experiment_failed", f"气路模块启动失败：{ex}")
        return

    # 存储线程
    store_thread = Store_Thread(name="monitor_data_store_message")
    store_thread.start()

    export_config = global_setting.get_setting('monitor_data').get('EXPORT', {})
    periodic_interval = float(export_config.get('periodic_xlsx_interval_minutes', 0) or 0)
    if periodic_interval > 0:
        if periodic_xlsx_export_thread is not None and periodic_xlsx_export_thread.isRunning():
            periodic_xlsx_export_thread.stop()
            periodic_xlsx_export_thread.deleteLater()
        periodic_xlsx_export_thread = PeriodicXlsxExportThread(
            name="monitor_data_periodic_xlsx_export",
            interval_minutes=periodic_interval
        )
        periodic_xlsx_export_thread.start()
        logger.info(f"已启用定时xlsx快照导出，间隔: {periodic_interval} 分钟")
    else:
        periodic_xlsx_export_thread = None
        logger.info("未启用定时xlsx快照导出")



    global port_use
    add_message_thread = Add_message_thread("monitor_data_add_message", send_thread, port_use)
    add_message_thread.start()

    schedule = normalize_lighting_schedule(
        getattr(experiment_settings, "lighting_schedule", None) if experiment_settings is not None else None
    )
    global_setting.set_setting("lighting_schedule", schedule)
    if lighting_schedule_thread is not None and lighting_schedule_thread.isRunning():
        lighting_schedule_thread.stop()
        lighting_schedule_thread.deleteLater()
    lighting_schedule_thread = LightingScheduleThread(schedule, gids, send_thread)
    lighting_schedule_thread.start()

    if _environment_module_only_enabled():
        _send_main_window_status(
            "gap_system_running_state",
            "仅环境模块模式：UFC、UGC、ZOS及其他笼内模块已禁用",
        )

    # 将实验配置存储到该实验的文件夹中去
    copy_experiment_setting_file()
    if _environment_module_only_enabled():
        _send_main_window_status(
            "close_start_experiment_dialog",
            "环境模块采集已启动",
        )
def start_zero_calibration():
    if _environment_module_only_enabled():
        logger.warning("Environment-only mode: zero calibration request ignored")
        return
    global ufc_ugc_zos_thread, ufc_ugc_zos
    if ufc_ugc_zos_thread is not None:
        ufc_ugc_zos_thread.zero_calibration_handle()
    """
    校0
    :return:
    """
    pass
def start_range_calibration():
    """
      校span
    :return:
    """
    if _environment_module_only_enabled():
        logger.warning("Environment-only mode: span calibration request ignored")
        return
    if ufc_ugc_zos_thread is not None:
        ufc_ugc_zos_thread.range_calibration_handle()
    pass
def start_air_calibration():
    """Start a standalone Air calibration while the experiment is running."""
    if _environment_module_only_enabled():
        message = "仅环境模块模式不运行气路，空气校准请求已拒绝"
        logger.warning(message)
        _send_main_window_status(
            "runtime_air_calibration_response",
            {"success": False, "message": message},
        )
        return
    if ufc_ugc_zos_thread is None:
        message = "气路服务未运行，无法启动空气校准"
        logger.warning(message)
        _send_main_window_status(
            "runtime_air_calibration_response",
            {"success": False, "message": message},
        )
        return
    result = ufc_ugc_zos_thread.air_calibration_handle()
    if not result.get("success", False):
        logger.warning(result.get("message", "空气校准请求被拒绝"))
        _send_main_window_status(
            "calibration_msg",
            result.get("message", "空气校准请求被拒绝"),
        )
    _send_main_window_status("runtime_air_calibration_response", result)
def start_calibration():
    """
      校0 和span
    :return:
    """
    if _environment_module_only_enabled():
        logger.warning("Environment-only mode: calibration request ignored")
        return
    if ufc_ugc_zos_thread is not None:
        ufc_ugc_zos_thread.calibration_btn_start()
    pass
def stop_zero_calibration():
    global ufc_ugc_zos_thread, ufc_ugc_zos
    if ufc_ugc_zos_thread is not None:
        ufc_ugc_zos_thread.stop_zero_calibration_handle()
    """
    stop 校0
    :return:
    """
    pass
def stop_range_calibration():
    """
      stop 校span
    :return:
    """
    if ufc_ugc_zos_thread is not None:
        ufc_ugc_zos_thread.stop_range_calibration_handle()
    pass
def stop_calibration():
    """
      stop 校0 和span
    :return:
    """
    if ufc_ugc_zos_thread is not None:
        ufc_ugc_zos_thread.stop_calibration_btn_start()
    pass
def restart(q, send_message_q, health_snapshot_queue=None,
            emergency_shutdown_request=None,
            emergency_shutdown_complete=None,
            emergency_shutdown_result=None):
    main(
        q,
        send_message_q,
        health_snapshot_queue=health_snapshot_queue,
        emergency_shutdown_request=emergency_shutdown_request,
        emergency_shutdown_complete=emergency_shutdown_complete,
        emergency_shutdown_result=emergency_shutdown_result,
    )
    start()
def pause():
    logger.info(f"{'-' * 30}monitor_data_pause{'-' * 30}")
    pass

def _parse_mouse_cage_numbers(raw_cages):
    if raw_cages is None:
        return []
    if isinstance(raw_cages, dict):
        iterable = raw_cages.values()
    elif isinstance(raw_cages, (list, tuple, set)):
        iterable = raw_cages
    else:
        iterable = [raw_cages]

    cages = []
    for cage in iterable:
        if isinstance(cage, str):
            match = re.search(r"\d+", cage)
            if not match:
                continue
            cage = match.group(0)
        try:
            cage_number = int(cage)
        except (TypeError, ValueError):
            continue
        if cage_number > 0 and cage_number not in cages:
            cages.append(cage_number)
    return cages

def _get_reference_mouse_cage_number():
    configer = global_setting.get_setting("configer", None) or {}
    if not isinstance(configer, dict):
        return None
    mouse_cage_config = configer.get("mouse_cage", {}) or {}
    if not isinstance(mouse_cage_config, dict):
        return None
    try:
        return int(mouse_cage_config.get("reference"))
    except (TypeError, ValueError):
        return None

def _get_opened_mouse_cages_for_light_off():
    return _parse_mouse_cage_numbers(global_setting.get_setting("mouse_cages", None))

def _build_light_off_message(mouse_cage_number):
    command = build_cage_light_commands(
        mouse_cage_number,
        power=False,
        port=global_setting.get_setting("port", None),
    )[0]
    command["switch_step"] = "shutdown_light_off"
    return command

def _send_opened_mouse_cages_light_off(wait_seconds=None, cages=None):
    global send_thread
    if cages is None:
        cages = _get_opened_mouse_cages_for_light_off()
    else:
        cages = _parse_mouse_cage_numbers(cages)
    if not cages:
        logger.warning("shutdown light off skipped: no opened mouse cages found")
        return []
    if send_thread is None or not send_thread.isRunning():
        logger.warning(f"shutdown light off skipped: send_thread unavailable, cages={cages}")
        return []

    queued_cages = []
    for cage in cages:
        command = _build_light_off_message(cage)
        send_thread.add_message(
            message={'message': command},
            urgent=True,
            origin="monitor_data_stop"
        )
        queued_cages.append(cage)
        logger.info(
            f"shutdown light off queued: cage={cage}, slave_id={command['slave_id']}, data={command['data']}"
        )

    if wait_seconds is None:
        wait_seconds = min(3.0, 0.25 * len(cages) + 0.5)
    time.sleep(wait_seconds)
    return queued_cages


def _force_shutdown_lights_off_and_sync_state():
    global _shutdown_lights_off_done
    cages = _get_opened_mouse_cages_for_light_off()
    if not cages:
        logger.warning("shutdown light off skipped: no opened mouse cages found")
        return
    if _shutdown_lights_off_done:
        logger.info(f"shutdown light off skipped: already completed for this experiment, cages={cages}")
        return
    _shutdown_lights_off_done = True

    queued_cages = []
    try:
        queued_cages = _send_opened_mouse_cages_light_off(cages=cages)
    except Exception as e:
        logger.error(f"shutdown light off send failed: {e}")

    try:
        saved_cages = force_save_cage_lights_off(cages)
        logger.info(
            f"shutdown light off completed: cages={cages}, queued={queued_cages}, saved={saved_cages}"
        )
    except Exception as e:
        logger.error(f"shutdown light state sync failed: {e}")


def _force_shutdown_food_trough_current_off():
    """Send one current-off command per enabled cage before stopping the sender."""
    global _shutdown_food_trough_current_off_done

    cages = _get_opened_mouse_cages_for_light_off()
    if not cages:
        logger.warning("shutdown food trough current-off skipped: no enabled cages found")
        return
    if _shutdown_food_trough_current_off_done:
        logger.info(
            "shutdown food trough current-off skipped: already completed, "
            f"cages={cages}"
        )
        return

    queued_cages = _queue_food_trough_current_off_commands(
        cages=cages,
        reason="实验结束",
    )
    if queued_cages:
        _shutdown_food_trough_current_off_done = True
        logger.info(
            "shutdown food trough current-off queued: "
            f"cages={queued_cages}"
        )


def stop():
    logger.info(f"{'-' * 30}monitor_data_stop{'-' * 30}")
    global lighting_schedule_thread
    monitor_stop_succeeded = True
    try:
        if lighting_schedule_thread is not None and lighting_schedule_thread.isRunning():
            lighting_schedule_thread.stop()
            lighting_schedule_thread.deleteLater()
        lighting_schedule_thread = None
    except Exception as e:
        monitor_stop_succeeded = False
        logger.error(f"stop lighting schedule failed: {e}")
    try:
        _force_shutdown_lights_off_and_sync_state()
    except Exception as e:
        monitor_stop_succeeded = False
        logger.error(f"shutdown light off failed: {e}")

    if global_setting.get_setting("is_calibrating", False):
        calibration_type = global_setting.get_setting("current_calibration_type", "校准")
        logger.warning(
            f"experiment stop requested while {calibration_type} is running; "
            "cancel calibration first, then continue full shutdown"
        )
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(
                ObjectQueueItem(origin="main_monitor_data", to="MainWindow_index",
                                title="stop_show_info_except_status_counts",
                                data=f"{calibration_type}进行中，正在先取消校准并继续关闭实验",
                                time=time_util.get_format_from_time(time.time())))

    # 发送线程关闭前，按实际启用的笼子逐个关闭食槽门驱动电流。
    try:
        _force_shutdown_food_trough_current_off()
    except Exception as e:
        monitor_stop_succeeded = False
        logger.error(f"shutdown food trough current-off failed: {e}")

    # 重置barrier回1，下次实验从1开始
    barrier = global_setting.get_setting("barrier")
    _release_collection_sync_waiters()
    if barrier is not None:
        barrier.reset(parties=1)
    global ufc_ugc_zos_thread, ufc_ugc_zos,store_thread,send_thread,add_message_thread, periodic_xlsx_export_thread
    gas_path_was_skipped = ufc_ugc_zos_thread is None
    try:
        logger.error("stop_ufc_ugc_zos")

        if ufc_ugc_zos_thread is not None:
            gas_controller = ufc_ugc_zos_thread
            gas_controller.stop_btn_handle()
            shutdown_errors = list(getattr(gas_controller, "_shutdown_errors", []) or [])
            ufc_shutdown_unconfirmed = any(
                "UFC" in str(error) or "气泵" in str(error)
                for error in shutdown_errors
            )
            if ufc_shutdown_unconfirmed:
                # 正常E流程未能确认关泵时，立即使用独立串口锁再执行一次
                # 安全关泵。原始失败回执仍然保留，不能把兜底动作伪装成
                # 整个UFC收尾成功。
                fallback_result = emergency_stop_ufc_pump()
                logger.critical(
                    "normal shutdown UFC fallback completed: "
                    f"result={fallback_result}, errors={shutdown_errors}"
                )
                queue = global_setting.get_setting("queue", None)
                if queue:
                    queue.put(ObjectQueueItem(
                        origin="main_monitor_data",
                        to="MainWindow_index",
                        title="stop_show_info_except_status_counts",
                        data=(
                            "UFC正常收尾未确认，安全兜底关泵已回读确认"
                            if fallback_result == 1
                            else "UFC正常收尾未确认，安全兜底关泵仍未确认，请立即检查设备"
                        ),
                        time=time_util.get_format_from_time(time.time()),
                    ))
            gas_controller.stop()
            gas_controller.deleteLater()
            ufc_ugc_zos_thread=None
    except Exception as e:
        monitor_stop_succeeded = False
        logger.error(f"关闭实验监测ufc_ugc_zos错误，原因：{e}")
        if not gas_path_was_skipped:
            fallback_result = emergency_stop_ufc_pump()
            logger.critical(
                "gas shutdown raised unexpectedly; emergency pump-off fallback "
                f"result={fallback_result}"
            )
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(
                ObjectQueueItem(origin="main_monitor_data", to="MainWindow_index", title="stop_gap_system_return",
                                data=f"关闭气路模块错误，原因：{e}",
                                time=time_util.get_format_from_time(time.time())))
            queue.put(
                ObjectQueueItem(origin="main_monitor_data", to="MainWindow_index", title="stop_ufc_gap_system_return",
                                data=f"关闭ufc气路模块错误，原因：{e}",
                                time=time_util.get_format_from_time(time.time())))
            queue.put(
                ObjectQueueItem(origin="main_monitor_data", to="MainWindow_index", title="stop_ugc_gap_system_return",
                                data=f"关闭ugc气路模块错误，原因：{e}",
                                time=time_util.get_format_from_time(time.time())))
            queue.put(
                ObjectQueueItem(origin="main_monitor_data", to="MainWindow_index", title="stop_zos_gap_system_return",
                                data=f"关闭zos气路模块错误，原因：{e}",
                                time=time_util.get_format_from_time(time.time())))

    if gas_path_was_skipped:
        _notify_skipped_gas_stop_complete()

    try:
        logger.error("stop_periodic_xlsx_export_thread")
        if periodic_xlsx_export_thread is not None and periodic_xlsx_export_thread.isRunning():
            periodic_xlsx_export_thread.stop()
            periodic_xlsx_export_thread.deleteLater()
        periodic_xlsx_export_thread = None
    except Exception as e:
        monitor_stop_succeeded = False
        logger.error(f"关闭实验监测periodic_xlsx_export_thread错误，原因：{e}")

    try:
        logger.error("stop_store_thread")
        if store_thread is not None and store_thread.isRunning():
            store_thread.stop()
            store_thread.deleteLater()
            # 返回响应
            queue = global_setting.get_setting("queue", None)
            if queue:
                queue.put(
                    ObjectQueueItem(origin="main_monitor_data", to="MainWindow_index",
                                    title="stop_show_info_except_status_counts",
                                    data=" 存储线程 已关闭",
                                    time=time_util.get_format_from_time(time.time())))
            store_thread = None
    except Exception as e:
        monitor_stop_succeeded = False
        logger.error(f"关闭实验监测store_thread错误，原因：{e}")
        # 返回响应
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(
                ObjectQueueItem(origin="main_monitor_data", to="MainWindow_index",
                                title="stop_show_info_except_status_counts",
                                data=f"关闭实验监测store_thread错误，原因：{e}",
                                time=time_util.get_format_from_time(time.time())))
    try:
        logger.error("stop_add_message_thread")
        if add_message_thread is not None and add_message_thread.isRunning():
            add_message_thread.stop()
            add_message_thread.deleteLater()
            add_message_thread = None
            # 返回响应
            queue = global_setting.get_setting("queue", None)
            if queue:
                queue.put(
                    ObjectQueueItem(origin="main_monitor_data", to="MainWindow_index",
                                    title="stop_show_info_except_status_counts",
                                    data=" 鼠笼内模块报文装载线程 已关闭",
                                    time=time_util.get_format_from_time(time.time())))
    except Exception as e:
        monitor_stop_succeeded = False
        logger.error(f"关闭实验监测add_message_thread错误，原因：{e}")
        # 返回响应
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(
                ObjectQueueItem(origin="main_monitor_data", to="MainWindow_index",
                                title="stop_show_info_except_status_counts",
                                data=f"关闭鼠笼内模块报文装载线程错误，原因：{e}",
                                time=time_util.get_format_from_time(time.time())))
    try:
        logger.error("stop_send_thread")
        if send_thread is not None and send_thread.isRunning():
            send_thread.stop()
            send_thread.deleteLater()
            send_thread = None
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(ObjectQueueItem(
                origin="main_monitor_data", to="MainWindow_index", title="stop_monitor_data_return",
                data="鼠笼内模块报文发送线程 已关闭",
                time=time_util.get_format_from_time(time.time()),
            ))
    except Exception as e:
        logger.error(f"关闭实验监测send_thread错误，原因：{e}")
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(ObjectQueueItem(
                origin="main_monitor_data", to="MainWindow_index", title="stop_gap_system_return",
                data=f"关闭鼠笼内模块报文发送线程错误，原因：{e}",
                time=time_util.get_format_from_time(time.time()),
            ))


def stop_modbus():
    modbus: ModbusRTUMasterNew = global_setting.get_setting("modbus", None)
    if modbus is not None:
        logger.error("stop_modbus_stop_experiment")
        modbus.close()
    pass
if __name__ == "__main__":
    q = multiprocessing.Queue()
    send_message_q = multiprocessing.Queue()
    main(q, send_message_q)
    start(q, send_message_q)
