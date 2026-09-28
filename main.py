import json
import configparser
import logging as diagnostic_logging
import multiprocessing
import atexit
import queue as queue_module
import sys
import tempfile
import threading
import traceback
from datetime import datetime
from logging.handlers import RotatingFileHandler
from multiprocessing import Process, freeze_support
import os
import time

# Initialize the native ML runtime before worker modules import Qt/OpenCV.
# This matches the legacy process-startup order and avoids WinError 1114
# when the trajectory service is loaded later by the GUI.
os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'TRUE')
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
import torch  # noqa: F401,E402
import psutil
from loguru import logger
from Service import main_response_Modbus, main_gui, main_monitor_data, main_deep_camera, main_infrared_camera
from public.config_class.Log_Config import LogConfig
from public.entity.queue.ObjectQueue import ObjectQueue
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.function.ProcessMonitor.ProcessMonitor import IntegratedProcessMonitor
from public.function.Crash_handle.CrashHandle import setup_crash_logging
from public.system_health import HealthReporter
from public.util.time_util import time_util



#进程监控器
monitor=None
runtime_diagnostics_queue = None
runtime_diagnostics_drop_lock = threading.Lock()
runtime_diagnostics_dropped = 0
system_health_snapshot_queue = None
process_health_reporters = {}
process_health_reporters_lock = threading.Lock()
single_instance_lock_handle = None
ufc_emergency_shutdown_request = None
ufc_emergency_shutdown_complete = None
ufc_emergency_shutdown_result = None
ufc_emergency_shutdown_lock = threading.Lock()
ufc_emergency_shutdown_requested = False


def request_emergency_ufc_shutdown(reason, timeout_seconds=12.0):
    """Ask the still-running monitor-data process to stop the UFC pump.

    A native crash in the GUI bypasses Qt/Python cleanup callbacks. The
    parent supervisor therefore requests this before terminating children.
    """
    global ufc_emergency_shutdown_requested
    request_event = ufc_emergency_shutdown_request
    complete_event = ufc_emergency_shutdown_complete
    result_value = ufc_emergency_shutdown_result
    if request_event is None or complete_event is None or result_value is None:
        logger.error(
            f"UFC emergency shutdown unavailable: reason={reason}, "
            "IPC channel is not initialized"
        )
        return False

    monitor_process = None
    if monitor is not None:
        monitor_info = monitor.processes.get("p_monitor_data", {})
        monitor_process = monitor_info.get("process")
    if monitor_process is None or not monitor_process.is_alive():
        logger.error(
            f"UFC emergency shutdown unavailable: reason={reason}, "
            "p_monitor_data is not running"
        )
        return False

    # Both the process-monitor thread and the controller loop can observe the
    # same GUI crash.  Only the first caller sends the request, but every
    # caller must wait for the same completion event; otherwise one caller can
    # tear down p_monitor_data while it is still executing the pump-off write.
    should_signal = False
    with ufc_emergency_shutdown_lock:
        if not ufc_emergency_shutdown_requested:
            complete_event.clear()
            with result_value.get_lock():
                result_value.value = 0
            ufc_emergency_shutdown_requested = True
            should_signal = True

    if should_signal:
        logger.critical(
            f"UFC emergency shutdown requested before child cleanup: reason={reason}"
        )
        request_event.set()
    else:
        logger.warning(
            "UFC emergency shutdown already in progress; waiting for the same "
            f"result: reason={reason}"
        )

    if not complete_event.wait(timeout=max(float(timeout_seconds), 0.1)):
        logger.critical(
            f"UFC emergency shutdown timed out after {timeout_seconds:.1f}s: "
            f"reason={reason}"
        )
        return False

    with result_value.get_lock():
        result = int(result_value.value)
    if result == 1:
        logger.critical(
            f"UFC emergency shutdown confirmed: pump is off, reason={reason}"
        )
        return True
    if result == 2:
        logger.critical(
            f"UFC emergency shutdown command acknowledged but state verification "
            f"was unavailable: reason={reason}"
        )
        return False

    logger.critical(f"UFC emergency shutdown failed: reason={reason}")
    return False


def get_main_program_dir():
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))


MAIN_PROGRAM_DIR = get_main_program_dir()


def resolve_main_program_path(*parts):
    return os.path.join(MAIN_PROGRAM_DIR, *parts)

def environment_module_only_enabled():
    config = configparser.ConfigParser()
    try:
        config.read(
            resolve_main_program_path("config", "monitor_datas_config.ini"),
            encoding="utf-8-sig",
        )
        return config.getboolean(
            "MODULE_TEST",
            "environment_module_only",
            fallback=False,
        )
    except (OSError, ValueError, configparser.Error):
        return False


def get_writable_log_root():
    candidates = [
        resolve_main_program_path("log"),
        os.path.join(
            os.environ.get("LOCALAPPDATA", ""),
            "AnimalProject",
            "log",
        ),
        os.path.join(tempfile.gettempdir(), "AnimalProject", "log"),
    ]
    checked = set()
    for candidate in candidates:    
        candidate = os.path.abspath(candidate)
        if candidate in checked:
            continue
        checked.add(candidate)
        try:
            os.makedirs(candidate, exist_ok=True)
            descriptor, probe_path = tempfile.mkstemp(
                prefix=".write_probe_",
                dir=candidate,
            )
            os.close(descriptor)
            os.remove(probe_path)
            return candidate
        except OSError:
            continue
    raise OSError("no writable log directory is available")


LOG_ROOT_DIR = get_writable_log_root()


def resolve_log_path(*parts):
    return os.path.join(LOG_ROOT_DIR, *parts)


def acquire_single_instance_lock():
    global single_instance_lock_handle
    lock_path = resolve_log_path("runtime", "animal_project_main.lock")
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    lock_handle = open(lock_path, "a+b")
    try:
        lock_handle.seek(0, os.SEEK_END)
        if lock_handle.tell() == 0:
            lock_handle.write(b"0")
            lock_handle.flush()
        lock_handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (OSError, ImportError):
        lock_handle.close()
        return False
    single_instance_lock_handle = lock_handle
    return True


def release_single_instance_lock():
    global single_instance_lock_handle
    lock_handle = single_instance_lock_handle
    single_instance_lock_handle = None
    if lock_handle is None:
        return
    try:
        lock_handle.seek(0)
        if os.name == "nt":
            import msvcrt

            msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
    except (OSError, ImportError):
        pass
    finally:
        lock_handle.close()


def close_multiprocessing_queue_now(queue_obj):
    """Release an IPC queue without blocking process shutdown on its feeder."""
    if queue_obj is None:
        return
    try:
        # At shutdown there are no consumers left to drain diagnostic or
        # health snapshots.  Do not let Queue.join_thread() hold the main
        # controller open while waiting for an already-closed pipe.
        queue_obj.cancel_join_thread()
    except (AttributeError, OSError, ValueError):
        pass
    try:
        queue_obj.close()
    except (AttributeError, OSError, ValueError):
        pass


def emit_runtime_diagnostic(source, event, message, pid=None):
    global runtime_diagnostics_dropped
    if runtime_diagnostics_queue is None:
        return
    with runtime_diagnostics_drop_lock:
        dropped_count = runtime_diagnostics_dropped
        record_message = str(message)
        if dropped_count:
            record_message = (
                f"{record_message}, "
                f"runtime_log_drops_since_last_success={dropped_count}"
            )
        record = {
            "timestamp": time.time(),
            "source": str(source),
            "event": str(event),
            "pid": int(os.getpid() if pid is None else pid),
            "process_name": multiprocessing.current_process().name,
            "thread_name": threading.current_thread().name,
            "message": record_message,
        }
        try:
            runtime_diagnostics_queue.put_nowait(record)
            runtime_diagnostics_dropped = 0
        except queue_module.Full:
            runtime_diagnostics_dropped = dropped_count + 1
        except (BrokenPipeError, EOFError, OSError):
            runtime_diagnostics_dropped = dropped_count + 1


def _get_process_health_reporter(process_id, scope="global"):
    """Return a parent-owned reporter for ProcessMonitor observations.

    The runtime diagnostic queue is intentionally not involved here: it has a
    dedicated log consumer, while this reporter writes only to the separate
    GUI health snapshot queue.
    """
    key = (str(process_id), str(scope))
    with process_health_reporters_lock:
        reporter = process_health_reporters.get(key)
        if reporter is None:
            reporter = HealthReporter(
                system_health_snapshot_queue,
                component=str(process_id),
                scope=str(scope),
            )
            process_health_reporters[key] = reporter
        return reporter


def _process_health_metrics(kwargs):
    return {
        "monitor_kind": "process",
        "pid": kwargs.get("pid"),
        "cpu_percent": kwargs.get("cpu_percent", kwargs.get("cpu_percent_normalized")),
        "memory_mb": kwargs.get("memory_mb"),
        "restart_count": kwargs.get("restart_count", 0),
        "status": kwargs.get("status", ""),
    }


def runtime_diagnostics_writer(diagnostics_queue):
    log_path = resolve_log_path(
        "runtime",
        "video_trajectory_runtime.log",
    )
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    diagnostics_logger = diagnostic_logging.getLogger(
        f"video_trajectory_runtime_writer_{os.getpid()}"
    )
    diagnostics_logger.setLevel(diagnostic_logging.INFO)
    diagnostics_logger.propagate = False
    diagnostics_logger.handlers.clear()
    file_handler = RotatingFileHandler(
        log_path,
        maxBytes=20 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setFormatter(
        diagnostic_logging.Formatter(
            "%(asctime)s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )
    diagnostics_logger.addHandler(file_handler)
    diagnostics_logger.info(
        f"process_monitor | diagnostics_started | pid={os.getpid()} | "
        f"process={multiprocessing.current_process().name} | "
        f"thread={threading.current_thread().name} | "
        f"path={log_path}"
    )

    try:
        while True:
            record = diagnostics_queue.get()
            if record is None:
                break
            diagnostics_logger.info(
                f"{record.get('source', 'unknown')} | "
                f"{record.get('event', 'runtime')} | "
                f"pid={record.get('pid', 0)} | "
                f"process={record.get('process_name', 'unknown')} | "
                f"thread={record.get('thread_name', 'unknown')} | "
                f"{record.get('message', '')}"
            )
    except (EOFError, OSError):
        pass
    finally:
        file_handler.flush()
        file_handler.close()
# ===================== 回调函数示例 =====================

def on_process_start(process_id, **kwargs):
    _get_process_health_reporter(process_id).heartbeat(
        metrics=_process_health_metrics(kwargs),
        message="Process monitor started the worker.",
    )
    emit_runtime_diagnostic(
        "process_monitor",
        "process_start",
        f"process={process_id}, critical={kwargs.get('is_critical', False)}",
        pid=kwargs.get("pid") or 0,
    )
    critical_info = "【关键进程】" if kwargs.get('is_critical') else "【普通进程】"
    logger.info(f"🎬 进程启动: {process_id} (PID: {kwargs.get('pid')}) {critical_info}")

def on_process_crash(process_id, **kwargs):
    _get_process_health_reporter(process_id).failure(
        "process_crashed",
        message="Worker process exited unexpectedly.",
        metrics=_process_health_metrics(kwargs),
        force_fault=True,
    )
    emit_runtime_diagnostic(
        "process_monitor",
        "process_crash",
        f"process={process_id}, exitcode={kwargs.get('exitcode')}, "
        f"runtime={kwargs.get('runtime', 0)}, error={kwargs.get('error_info')}",
    )
    critical_info = "【关键进程】" if kwargs.get('is_critical') else "【普通进程】"

    error_msg = (
            f"💥 进程崩溃: {process_id} {critical_info}\n"
            f"   退出码: {kwargs.get('exitcode')}\n"
            f"   运行时间: {kwargs.get('runtime', 0)}"
            + (f"\n   错误信息: {kwargs.get('error_info')}" if kwargs.get('error_info') else "")
    )
    logger.error(error_msg)
    # main_gui.on_crash(error_msg)
def on_process_complete(process_id, **kwargs):
    _get_process_health_reporter(process_id).unavailable(
        "process_completed",
        message="Worker process completed and is no longer providing live data.",
        metrics=_process_health_metrics(kwargs),
    )
    emit_runtime_diagnostic(
        "process_monitor",
        "process_complete",
        f"process={process_id}, runtime={kwargs.get('runtime', 0)}",
    )
    critical_info = "【关键进程】" if kwargs.get('is_critical') else "【普通进程】"
    completion_message = (
        f"进程完成: {process_id} {critical_info} "
        f"(运行时间: {kwargs.get('runtime', 0)})"
    )
    if kwargs.get('is_critical'):
        logger.critical(f"[SHUTDOWN_DIAGNOSTIC] {completion_message}")
    else:
        logger.info(completion_message)

def on_process_restart(process_id, **kwargs):
    _get_process_health_reporter(process_id).failure(
        "process_restarted",
        message="Worker process restarted; waiting for healthy observations.",
        metrics=_process_health_metrics(kwargs),
    )
    emit_runtime_diagnostic(
        "process_monitor",
        "process_restart",
        f"process={process_id}, new_pid={kwargs.get('new_pid')}, "
        f"restart_count={kwargs.get('restart_count')}",
        pid=kwargs.get("new_pid") or 0,
    )
    critical_info = "【关键进程】" if kwargs.get('is_critical') else "【普通进程】"
    logger.info(f"🔄 进程重启: {process_id} {critical_info}")
    logger.info(f"   新PID: {kwargs.get('new_pid')}")
    logger.info(f"   重启次数: {kwargs.get('restart_count')}")

def on_process_timeout(process_id, **kwargs):
    _get_process_health_reporter(process_id).failure(
        "process_timeout",
        message="Worker process exceeded its configured timeout.",
        metrics=_process_health_metrics(kwargs),
        force_fault=True,
    )
    emit_runtime_diagnostic(
        "process_monitor",
        "process_timeout",
        f"process={process_id}, timeout={kwargs.get('timeout')}, "
        f"runtime={kwargs.get('runtime', 0)}",
    )
    critical_info = "【关键进程】" if kwargs.get('is_critical') else "【普通进程】"
    logger.warning(f"⏰ 进程超时: {process_id} {critical_info}")
    logger.warning(f"   超时限制: {kwargs.get('timeout')}秒")
    logger.warning(f"   实际运行: {kwargs.get('runtime', 0)}")

def on_process_unresponsive(process_id, **kwargs):
    _get_process_health_reporter(process_id).failure(
        "process_heartbeat_timeout",
        message="Worker heartbeat did not arrive before the timeout.",
        metrics=_process_health_metrics(kwargs),
        force_fault=True,
    )
    emit_runtime_diagnostic(
        "process_monitor",
        "process_unresponsive",
        f"process={process_id}, heartbeat_age={kwargs.get('heartbeat_age', 0)}",
    )
    critical_info = "【关键进程】" if kwargs.get('is_critical') else "【普通进程】"
    error_msg = f"""😵 进程无响应: {process_id} {critical_info}
       心跳超时: {kwargs.get('heartbeat_age', 0)}"""
    logger.error(
        error_msg
    )
    # main_gui.on_crash(error_msg)
def on_high_cpu(process_id, **kwargs):
    _get_process_health_reporter(process_id, "resource").data_anomaly(
        "high_cpu",
        message="CPU usage exceeded the warning threshold.",
        metrics=_process_health_metrics(kwargs),
    )
    emit_runtime_diagnostic(
        "process_monitor",
        "high_cpu",
        f"process={process_id}, cpu_percent={kwargs.get('cpu_percent', 0):.1f}, "
        f"threshold={kwargs.get('threshold', 0):.1f}",
    )
    logger.warning(f"🔥 CPU使用率过高: {process_id}")
    logger.warning(f"   当前: {kwargs.get('cpu_percent', 0):.1f}%")
    logger.warning(f"   阈值: {kwargs.get('threshold', 0):.1f}%")

def on_high_memory(process_id, **kwargs):
    _get_process_health_reporter(process_id, "resource").data_anomaly(
        "high_memory",
        message="Memory usage exceeded the warning threshold.",
        metrics=_process_health_metrics(kwargs),
    )
    emit_runtime_diagnostic(
        "process_monitor",
        "high_memory",
        f"process={process_id}, memory_mb={kwargs.get('memory_mb', 0):.1f}, "
        f"threshold={kwargs.get('threshold', 0):.1f}",
    )
    logger.warning(f"🧠 内存使用过高: {process_id}")
    logger.warning(f"   当前: {kwargs.get('memory_mb', 0):.1f}MB")
    logger.warning(f"   阈值: {kwargs.get('threshold', 0):.1f}MB")

def on_process_metrics(process_id, **kwargs):
    metrics = _process_health_metrics(kwargs)
    status = str(kwargs.get("status", "")).upper()
    process_reporter = _get_process_health_reporter(process_id)
    if status in {"CRASHED", "UNRESPONSIVE", "ERROR"}:
        process_reporter.failure(
            "process_monitor_error",
            message=f"Process monitor reported status {status}.",
            metrics=metrics,
            force_fault=True,
        )
    else:
        process_reporter.heartbeat(
            metrics=metrics,
            message="Process heartbeat and metrics received.",
        )
        _get_process_health_reporter(process_id, "resource").success(
            metrics=metrics,
            message="Resource metrics are within the current monitor policy.",
        )
    emit_runtime_diagnostic(
        "process_monitor",
        "process_metrics",
        f"process={process_id}, status={kwargs.get('status')}, "
        f"cpu_raw={kwargs.get('cpu_percent_raw', 0):.1f}, "
        f"cpu_normalized={kwargs.get('cpu_percent_normalized', 0):.1f}, "
        f"memory_mb={kwargs.get('memory_mb', 0):.1f}, "
        f"uptime_seconds={kwargs.get('uptime_seconds', 0):.1f}, "
        f"restart_count={kwargs.get('restart_count', 0)}",
        pid=kwargs.get("pid") or 0,
    )


def on_critical_failure(process_id, **kwargs):
    _get_process_health_reporter(process_id).failure(
        "critical_process_failure",
        message="A critical worker failed; the supervisor is stopping the system.",
        metrics=_process_health_metrics(kwargs),
        force_fault=True,
    )
    emit_runtime_diagnostic(
        "process_monitor",
        "critical_failure",
        f"process={process_id}, exitcode={kwargs.get('exitcode')}",
    )
    error_msg = f"""🚨 关键进程失败: {process_id}
       退出码: {kwargs.get('exitcode')}
       系统将关闭所有其他进程"""
    logger.critical(
        error_msg
    )
    if process_id == "p_main_gui" and kwargs.get("exitcode") not in (None, 0):
        request_emergency_ufc_shutdown(
            f"critical process {process_id} exited with code {kwargs.get('exitcode')}"
        )
    # main_gui.on_crash(error_msg)
def on_shutdown_triggered(process_id, **kwargs):
    failed_processes = kwargs.get('failed_processes', [])
    emit_runtime_diagnostic(
        "process_monitor",
        "shutdown_triggered",
        f"process={process_id}, failed_processes={failed_processes}",
    )
    logger.critical(f"🛑 触发系统关闭，原因: 关键进程失败 {failed_processes}")
    logger.critical("   正在关闭所有进程...")
 # 注册异常回调
def on_any_exception(exception_info):
    emit_runtime_diagnostic(
        "process_monitor",
        "process_exception",
        f"process={exception_info.process_id}, "
        f"type={exception_info.exception_type}, "
        f"message={exception_info.exception_message}",
        pid=exception_info.pid,
    )
    logger.error(f"检测到异常: {exception_info.process_id} - {exception_info.exception_type}")
# 过滤日志

def kill_process_tree(pid, including_parent=True):
    """
    确认子进程没有启动其他子进程，如果有，必须递归管理或用系统命令杀死整个进程树。
    用 psutil 库递归杀死进程树
    multiprocessing.Process.terminate() 只会终止对应的单个进程，如果该进程启动了其他进程，这些“子进程”不会被自动终止，因而可能会在任务管理器中残留。
    """
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    children = parent.children(recursive=True)
    for child in children:
        child.terminate()
    gone, alive = psutil.wait_procs(children, timeout=5)
    for p in alive:
        p.kill()
    if including_parent:
        if psutil.pid_exists(pid):
            parent.terminate()
            parent.wait(5)



def test_integrated_monitor():
    freeze_support()
    setup_crash_logging()
    multiprocessing.set_start_method('spawn', force=True)
    environment_only = environment_module_only_enabled()
    if not acquire_single_instance_lock():
        print("Animal Project is already running; duplicate startup was blocked.")
        return False
    atexit.register(release_single_instance_lock)

    global runtime_diagnostics_queue, system_health_snapshot_queue, process_health_reporters
    global ufc_emergency_shutdown_request, ufc_emergency_shutdown_complete
    global ufc_emergency_shutdown_result, ufc_emergency_shutdown_requested
    runtime_diagnostics_queue = multiprocessing.Queue(maxsize=2048)
    # Health is deliberately a separate, bounded queue.  The runtime
    # diagnostics queue below has a single file-log consumer and must not be
    # consumed by the GUI.
    system_health_snapshot_queue = multiprocessing.Queue(maxsize=512)
    process_health_reporters = {}
    ufc_emergency_shutdown_request = multiprocessing.Event()
    ufc_emergency_shutdown_complete = multiprocessing.Event()
    ufc_emergency_shutdown_result = multiprocessing.Value('i', 0)
    ufc_emergency_shutdown_requested = False
    diagnostics_thread = threading.Thread(
        target=runtime_diagnostics_writer,
        args=(runtime_diagnostics_queue,),
        name="video_trajectory_runtime_writer",
        daemon=True,
    )
    diagnostics_thread.start()
    # 加载日志配置
    # 移除默认处理器
    # logger.remove()

    logger.add(
        resolve_log_path("main", "main_{time:YYYY-MM-DD}.log"),
        rotation="00:00",  # 日志文件转存
        retention="30 days",  # 多长时间之后清理
        enqueue=True,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}  |{process.name} | {thread.name} |  {name} : {module}:{line} | {message} </level>",

    )

    logger.info(f"{'-' * 40}main_start{'-' * 40}")
    logger.info(f"{__name__} | {os.path.basename(__file__)}|{os.getpid()}|{os.getppid()}")
    q = multiprocessing.Queue()  # 创建 Queue 消息传递
    send_message_q = multiprocessing.Queue()  # 发送查询报文的消息传递单独一个通道


    # 创建自定义的主进程日志配置
    main_config = LogConfig(
        log_dir=resolve_log_path("main"),
        log_level="DEBUG",
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level} |{process.name} | {thread.name} |  {name} :  {module}:{function}:{line} | {message} </level>",
        enable_console=True,
        console_level="DEBUG"
    )
    # 创建自定义的异常日志配置
    exception_config = LogConfig(
        log_dir=resolve_log_path("exceptions"),
        log_level="ERROR",
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level} | EXCEPTION |{process.name} | {thread.name} |  {name} :  {module}:{function}:{line} | {message} </level>",
        enable_console=True,
        console_level="ERROR"
    )
    # 创建监控器
    global monitor
    # 创建监控器
    monitor = IntegratedProcessMonitor(
        main_log_config=main_config,
        exception_log_config=exception_config
    )

    # 设置自定义阈值
    monitor.thresholds['cpu_percent'] = 70.0
    monitor.thresholds['memory_mb'] = 2000.0
    monitor.thresholds['heartbeat_timeout']=30.0

    # 注册所有回调
    monitor.register_callback('on_start', on_process_start)
    monitor.register_callback('on_crash', on_process_crash)
    monitor.register_callback('on_complete', on_process_complete)
    monitor.register_callback('on_restart', on_process_restart)
    monitor.register_callback('on_timeout', on_process_timeout)
    monitor.register_callback('on_unresponsive', on_process_unresponsive)
    monitor.register_callback('on_high_cpu', on_high_cpu)
    monitor.register_callback('on_high_memory', on_high_memory)
    monitor.register_callback('on_metrics', on_process_metrics)
    monitor.register_callback('on_critical_failure', on_critical_failure)
    monitor.register_callback('on_shutdown_triggered', on_shutdown_triggered)
    monitor.register_exception_callback(on_any_exception)

    p_response_comm_config = monitor.create_process_log_config(
        "p_response_comm",
        log_dir=resolve_log_path("processes", "p_response_comm"),
        log_level="DEBUG",
        custom_format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level} | p_response_comm | {module}:{function}:{line} | {message} </level>",
        enable_console=True
    )
    if not environment_only:
        monitor.start_worker(
            target_func=main_response_Modbus.main,
            args=(),
            name="p_response_comm",
            auto_restart=False,
            log_config=p_response_comm_config
        )
    else:
        logger.warning("Environment-only mode: p_response_comm is disabled")

    # 创建工作进程的日志配置 调试专用 记得注释
    p_monitor_data_config = monitor.create_process_log_config(
        "p_monitor_data",
        log_dir=resolve_log_path("processes", "p_monitor_data"),
        log_level="DEBUG",
        custom_format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level} | p_monitor_data |{process.name} | {thread.name} |  {name} :  {module}:{function}:{line} | {message} </level> ",
        enable_console=True
    )
    monitor.start_worker(
        target_func=main_monitor_data.main,
        restart_target_func=main_monitor_data.restart,
        args=(
            q,
            send_message_q,
            system_health_snapshot_queue,
            ufc_emergency_shutdown_request,
            ufc_emergency_shutdown_complete,
            ufc_emergency_shutdown_result,
        ),
        name="p_monitor_data",
        auto_restart=True,
        log_config=p_monitor_data_config
    )
    p_deep_camera_config = monitor.create_process_log_config(
        "p_deep_camera",
        log_dir=resolve_log_path("processes", "p_deep_camera"),
        log_level="DEBUG",
        custom_format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}  | p_deep_camera | {process.name} | {thread.name} |  {name} : {module}:{function}:{line} | {message} </level>",
        enable_console=True
    )

    if not environment_only:
        monitor.start_worker(
            target_func=main_deep_camera.main,
            restart_target_func=main_deep_camera.restart,
            args=(q, runtime_diagnostics_queue, False, system_health_snapshot_queue),
            name="p_deep_camera",
            auto_restart=True,
            log_config=p_deep_camera_config
        )
    else:
        logger.warning("Environment-only mode: p_deep_camera is disabled")

    p_infrared_camera_config = monitor.create_process_log_config(
        "p_infrared_camera",
        log_dir=resolve_log_path("processes", "p_infrared_camera"),
        log_level="DEBUG",
        custom_format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level} | p_infrared_camera | {process.name} | {thread.name} |  {name} :  {module}:{function}:{line} | {message} </level> ",
        enable_console=True
    )
    if not environment_only:
        monitor.start_worker(
            target_func=main_infrared_camera.main,
            restart_target_func=main_infrared_camera.restart,
            args=(q, system_health_snapshot_queue),
            name="p_infrared_camera",
            auto_restart=True,
            log_config=p_infrared_camera_config
        )
    else:
        logger.warning("Environment-only mode: p_infrared_camera is disabled")
    p_main_gui_config = monitor.create_process_log_config(
        "p_main_gui",
        log_dir=resolve_log_path("processes", "p_main_gui"),
        log_level="DEBUG",
        custom_format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level}  | p_main_gui | {process.name} | {thread.name} |  {name} :  {module}:{function}:{line} | {message}  </level>",
        enable_console=True
    )
    monitor.start_worker(
        target_func=main_gui.main,
        args=(q, send_message_q, runtime_diagnostics_queue, system_health_snapshot_queue),
        name="p_main_gui",
        auto_restart=False,
        is_critical=True,  # 标记为关键进程
        log_config = p_main_gui_config
    )


    # 开始监控
    monitor.start_monitoring(interval=5)
    emit_runtime_diagnostic(
        "process_monitor",
        "controller_running",
        "main controller is keeping child processes and runtime diagnostics alive",
    )

    try:
        last_controller_heartbeat = 0.0
        while True:
            now_monotonic = time.monotonic()
            if now_monotonic - last_controller_heartbeat >= 5.0:
                alive_processes = sum(
                    1
                    for process_info in monitor.processes.values()
                    if process_info.get("process") is not None
                    and process_info["process"].is_alive()
                )
                emit_runtime_diagnostic(
                    "process_monitor",
                    "controller_heartbeat",
                    f"alive_processes={alive_processes}, "
                    f"registered_processes={len(monitor.processes)}",
                )
                last_controller_heartbeat = now_monotonic
            gui_process_info = monitor.processes.get("p_main_gui", {})
            gui_process = gui_process_info.get("process")
            if gui_process is not None and not gui_process.is_alive():
                if gui_process.exitcode not in (None, 0):
                    request_emergency_ufc_shutdown(
                        f"controller observed p_main_gui exit code {gui_process.exitcode}"
                    )
                emit_runtime_diagnostic(
                    "process_monitor",
                    "controller_stopping",
                    f"p_main_gui exited with code={gui_process.exitcode}",
                )
                break
            monitor_thread = getattr(monitor, "monitor_thread", None)
            if monitor_thread is not None and not monitor_thread.is_alive():
                emit_runtime_diagnostic(
                    "process_monitor",
                    "controller_stopping",
                    "process monitor thread stopped",
                )
                break
            time.sleep(0.5)
    except KeyboardInterrupt:
        emit_runtime_diagnostic(
            "process_monitor",
            "controller_stopping",
            "keyboard interrupt",
        )
    finally:
        try:
            monitor.stop_monitoring()
        except Exception:
            logger.exception("failed to stop integrated process monitor")
        try:
            runtime_diagnostics_queue.put(None, timeout=2)
        except (queue_module.Full, BrokenPipeError, EOFError, OSError):
            pass
        diagnostics_thread.join(timeout=5)
        if diagnostics_thread.is_alive():
            close_multiprocessing_queue_now(runtime_diagnostics_queue)
            diagnostics_thread.join(timeout=1)
        else:
            close_multiprocessing_queue_now(runtime_diagnostics_queue)
        runtime_diagnostics_queue = None
        close_multiprocessing_queue_now(system_health_snapshot_queue)
        system_health_snapshot_queue = None
        close_multiprocessing_queue_now(getattr(monitor, "exception_queue", None))
        for process_info in getattr(monitor, "processes", {}).values():
            close_multiprocessing_queue_now(process_info.get("health_queue"))
        close_multiprocessing_queue_now(q)
        close_multiprocessing_queue_now(send_message_q)
        process_health_reporters = {}
        release_single_instance_lock()
    return True



if __name__ == "__main__" and os.path.basename(__file__) == "main.py":
    test_integrated_monitor()
