import math
import multiprocessing
import os
import threading
import time
from typing import Any

from blinker.base import _PNamespaceSignal
from loguru import logger
from blinker import signal, Signal
from Service.UFC_UGC_ZOS_Service.function.calibration_log import CalibrationLogRecorder

from Service.UFC_UGC_ZOS_Service.function.co2_compensation import Startup_CO2_Air_Calibration
from Service.UFC_UGC_ZOS_Service.function.o2_compensation import initialize_reference_dry_oxygen_percent
from Service.UFC_UGC_ZOS_Service.function.gas_calibration.Gas_Carlibration import Zero_Carlibration, Range_Carlibration
from Service.UFC_UGC_ZOS_Service.function.gas_path_system.Gas_path_system import ZOS_gas_path_system, \
    UGC_gas_path_system, UFC_gas_path_system
from Service.UFC_UGC_ZOS_Service.function.gas_state_check.Gas_State_Check import UFC_Gas_State_Check
from Service.UFC_UGC_ZOS_Service.function.startup_air_calibration import Startup_Air_Calibration
from Service.UFC_UGC_ZOS_Service.function.startup_air_calibration.Startup_O2_Span_Air_Calibration import \
    Startup_O2_Span_Air_Calibration
from public.config_class.global_setting import global_setting
from public.config_class.ini_parser import ini_parser
from public.entity.MyQThread import MyQThread, MyThread
from public.entity.enum.Public_Enum import GapSystem_Running_Type
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.function.Timer.ProcederTimer import PeriodicTimer
from public.function.promise.AsyPromise import AsyPromise
from public.util.time_util import time_util

# 过滤日志

# logger = logger.bind(category="deep_camera_logger")
read_queue_data_Thread_Lock = threading.Lock()
auto_wait_event = threading.Event()
# 在气路模块运行之前被阻塞时 如果遇见停止实验则不进行运行及后续的操作
stop_flag = False

# 等待校0和span完成
wait_zero_calibration_finished_event= threading.Event()
wait_span_calibration_finished_event= threading.Event()

class read_queue_data_Thread(MyQThread):
    def __init__(self, name):
        super().__init__(name)
        self.queue = None
        self.update_status_main_signal_gui_update = None
        pass

    def dosomething(self):
        if not self.queue.empty():
            try:
                message: ObjectQueueItem = self.queue.get()
            except Exception as e:
                logger.error(f"{self.name}发生错误{e}")
                return
                # message 结构{'to'发往哪个线程，'data'数据，'from'从哪来}

            if message is not None and isinstance(message, ObjectQueueItem) and message.to == 'UFC_UGC_ZOS_index':
                # logger.error(f"{self.name}_get_message:{message}")
                match message.title:
                    case '':
                        if self.update_status_main_signal_gui_update is not None:
                            with read_queue_data_Thread_Lock:
                                self.update_status_main_signal_gui_update.send(message.data)
                            pass
                    case _:
                        pass

            else:
                # 把消息放回去
                self.queue.put(message)


read_queue_data_thread = read_queue_data_Thread(name="UFC_UGC_ZOS_index_read_queue_data_thread")


class Monitor_start_state_Thread(MyQThread):
    def __init__(self, name, UFC_gas_path_system_obj=None, UGC_gas_path_system_obj=None, ZOS_gas_path_system_obj=None,
                 update_start_state_signal=None):
        # UFC气路系统
        self.UFC_gas_path_system_obj: UFC_gas_path_system = UFC_gas_path_system_obj
        # UGC气路系统
        self.UGC_gas_path_system_obj: UGC_gas_path_system = UGC_gas_path_system_obj
        # ZOS气路系统
        self.ZOS_gas_path_system_obj: ZOS_gas_path_system = ZOS_gas_path_system_obj
        self.update_start_state_signal = update_start_state_signal

        super().__init__(name)

    def dosomething(self):
        # logger.critical(f"UFC:{self.UFC_gas_path_system_obj.ufc_start_time_state},ZOS:{self.ZOS_gas_path_system_obj.zos_start_status}" )
        if self.ZOS_gas_path_system_obj.zos_start_status:
            # The signal is synchronous. Stop this polling loop first, then let
            # the owner publish completion without waiting on this same thread.
            self.stop()
            if self.update_start_state_signal is not None:
                self.update_start_state_signal.send()
            return
        time.sleep(1)


class UFC_UGC_ZOS_index(MyQThread):
    STANDALONE_CALIBRATION_LABELS = {
        "co2_zero_span": "CO2校零和span",
        "o2_span": "O2校span",
        "air_o2_co2": "氧气和二氧化碳空气校准",
    }

    def __init__(self):
        super().__init__(name="UFC_UGC_ZOS_index")
        self.ispause = False
        # dosomething() is driven by MyQThread's loop, while the gas startup
        # chain itself is asynchronous.  Without this guard the loop starts a
        # second chain before the first one completes, producing many repeated
        # "ZOS 正在启动" commands and stale callbacks during shutdown.
        self._startup_chain_started = False
        # Keep experiment shutdown state separate from MyQThread's own
        # _stop_requested flag. The startup worker stops itself after the gas
        # paths enter run mode, but that does not mean the experiment is
        # stopping.
        self._experiment_stop_requested = False
        self._shutdown_errors = []
        self._runtime_air_thread = None
        self._runtime_air_stop_event = threading.Event()
        self._runtime_air_lock = threading.Lock()
        self._standalone_calibration_active = False
        self._standalone_calibration_kind = None
        self._standalone_calibration_chain = None
        self._standalone_calibration_lock = threading.Lock()
        self._standalone_shutdown_in_progress = False
        self._standalone_user_stop_requested = False
        self._standalone_air_calibration_started = False
        self._standalone_stop_watchdog_event = threading.Event()
        self._standalone_worker_thread = None

        self.send_message = {
            'port': '',
            'data': '',
            'slave_id': 0,
            'function_code': 0,
            'timeout': 0
        }

        self.UFC_gas_path_system_obj: UFC_gas_path_system = None
        self.UGC_gas_path_system_obj: UGC_gas_path_system = None
        self.ZOS_gas_path_system_obj: ZOS_gas_path_system = None
        self.Zero_carlibration_obj: Zero_Carlibration = None
        self.Range_carlibration_obj: Range_Carlibration = None
        self.Startup_air_calibration_obj: Startup_Air_Calibration = None
        self.Startup_o2_span_air_calibration_obj: Startup_O2_Span_Air_Calibration = None
        self.Startup_co2_air_calibration_obj: Startup_CO2_Air_Calibration = None
        self.UFC_gas_state_check_obj: UFC_Gas_State_Check = None

        self.zos_start_timer: PeriodicTimer = None

        self.gas_state_check_timer: PeriodicTimer = None
        self.monitor_start_state_Thread: MyQThread = None

        self.update_status_main_signal_gui_update: Signal = None
        self.calibration_log_recorder = CalibrationLogRecorder(
            on_error=lambda error: logger.error(f"校准日志自动保存失败: {error}"),
            on_created=lambda path: logger.info(f"校准日志自动保存文件: {path}"),
        )
        self._init_data()
        self._init_function()

    def _init_data(self):
        config_file_path = os.getcwd() + "./" + "config/UFC_UGC_ZOS_Test.ini"
        configer = ini_parser(config_file_path).read()
        if len(configer) != 0:
            logger.info("UFC_UGC_ZOS_config配置文件读取成功。")
        else:
            logger.error("UFC_UGC_ZOS_config配置文件读取失败。")
        global_setting.set_setting("UFC_UGC_ZOS_config", configer)
        reference_dry_oxygen = initialize_reference_dry_oxygen_percent(configer)
        logger.info(f"REF干基氧浓度启动值: {reference_dry_oxygen:.3f}%")
        serial_lock = threading.Lock()
        global_setting.set_setting("serial_lock", serial_lock)

    def logger_info(self, text, **kwargs):
        title = kwargs.get("title", GapSystem_Running_Type.DEFAULT)
        self.calibration_log_recorder.record(getattr(title, "name", ""), text)

        if text and "\n" not in text:

            # 除了日志需求，需要将响应信息放映出来

            match title:
                case GapSystem_Running_Type.ZERO_CALIBRATION | GapSystem_Running_Type.RANGE_CALIBRATION | GapSystem_Running_Type.STARTUP_AIR_CALIBRATION | GapSystem_Running_Type.STARTUP_CO2_AIR_CALIBRATION:
                    """此时text为{
                        'type':'set_start_zero_calibration_time'
                                |'set_stop_zero_calibration_time'
                                |'set_start_span_calibration_time'
                                |'set_stop_span_calibration_time'
                                |'set_calibration_values'
                                |None,
                        'value':''|{} 
                    }

                    """
                    queue = global_setting.get_setting("queue", None)
                    if queue:
                        if isinstance(text, dict):
                            match text.get("type", None):
                                case 'set_start_zero_calibration_time':
                                    queue.put(
                                        ObjectQueueItem(origin='UFC_UGC_ZOS_index', to='MainWindow_index',
                                                        title='set_start_zero_calibration_time',
                                                        data=text.get('value', ''),
                                                        time=time_util.get_format_from_time(time.time())))
                                    # 暂停正在运行的气路模块
                                    self.pause_running_gap_system()
                                    pass
                                case 'set_stop_zero_calibration_time':
                                    queue.put(
                                        ObjectQueueItem(origin='UFC_UGC_ZOS_index', to='MainWindow_index',
                                                        title='set_stop_zero_calibration_time',
                                                        data=text.get('value', ''),
                                                        time=time_util.get_format_from_time(time.time())))
                                    # 解除运行线程暂停
                                    self.resume_running_gap_system()
                                    global wait_zero_calibration_finished_event
                                    wait_zero_calibration_finished_event.set()
                                    pass
                                case 'set_start_span_calibration_time':
                                    queue.put(
                                        ObjectQueueItem(origin='UFC_UGC_ZOS_index', to='MainWindow_index',
                                                        title='set_start_span_calibration_time',
                                                        data=text.get('value', ''),
                                                        time=time_util.get_format_from_time(time.time())))
                                    # 暂停正在运行的气路模块
                                    self.pause_running_gap_system()
                                    pass
                                case 'set_stop_span_calibration_time':
                                    queue.put(
                                        ObjectQueueItem(origin='UFC_UGC_ZOS_index', to='MainWindow_index',
                                                        title='set_stop_span_calibration_time',
                                                        data=text.get('value', ''),
                                                        time=time_util.get_format_from_time(time.time())))
                                    # 解除运行线程暂停
                                    self.resume_running_gap_system()
                                    global wait_span_calibration_finished_event
                                    wait_span_calibration_finished_event.set()
                                    pass
                                case 'set_start_air_calibration_time':
                                    queue.put(
                                        ObjectQueueItem(origin='UFC_UGC_ZOS_index', to='MainWindow_index',
                                                        title='set_start_air_calibration_time',
                                                        data=text.get('value', ''),
                                                        time=time_util.get_format_from_time(time.time())))
                                    self.pause_running_gap_system()
                                    pass
                                case 'set_stop_air_calibration_time':
                                    queue.put(
                                        ObjectQueueItem(origin='UFC_UGC_ZOS_index', to='MainWindow_index',
                                                        title='set_stop_air_calibration_time',
                                                        data=text.get('value', ''),
                                                        time=time_util.get_format_from_time(time.time())))
                                    self.resume_running_gap_system()
                                    pass
                                case 'set_calibration_values':
                                    queue.put(
                                        ObjectQueueItem(origin='UFC_UGC_ZOS_index', to='MainWindow_index',
                                                        title='set_calibration_values',
                                                        data=text.get('value', {}),
                                                        time=time_util.get_format_from_time(time.time())))
                                    pass
                                case 'set_calibration_span_values':
                                    queue.put(
                                        ObjectQueueItem(origin='UFC_UGC_ZOS_index', to='MainWindow_index',
                                                        title='set_calibration_span_values',
                                                        data=text.get('value', {}),
                                                        time=time_util.get_format_from_time(time.time())))
                                    pass
                                case _:
                                    pass
                        else:
                            queue.put(ObjectQueueItem(origin='UFC_UGC_ZOS_index', to='MainWindow_index',
                                                      title='calibration_msg',
                                                      data=text,
                                                      time=time_util.get_format_from_time(time.time())))
                            # calibration_msg already feeds both the P3 runtime
                            # log and calibration detail history. Sending the
                            # same text through gap_system_running_state as well
                            # mirrors it from the hidden startup dialog a second time.
                    pass
                case GapSystem_Running_Type.DEFAULT:
                    queue = global_setting.get_setting("queue", None)
                    if queue:
                        queue.put(ObjectQueueItem(origin="UFC_UGC_ZOS_index", to="MainWindow_index",
                                                  title="gap_system_running_state", data=text,
                                                  time=time_util.get_format_from_time(time.time())))
                case _:
                    queue = global_setting.get_setting("queue", None)
                    if queue:
                        queue.put(ObjectQueueItem(origin="UFC_UGC_ZOS_index", to="MainWindow_index",
                                                  title="gap_system_running_state", data=text,
                                                  time=time_util.get_format_from_time(time.time())))

            logger.debug(text)

    def _init_function(self):
        self.update_status_main_signal_gui_update = signal('update_status_main_signal_gui_update')
        self.update_status_main_signal_gui_update.connect(self.logger_info)
        self.update_start_state_signal = signal('update_start_state')
        self.update_start_state_signal.connect(self.update_start_state)

        self.UFC_gas_path_system_obj = UFC_gas_path_system()
        self.UGC_gas_path_system_obj = UGC_gas_path_system()
        self.ZOS_gas_path_system_obj = ZOS_gas_path_system()
        self.UFC_gas_path_system_obj.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update
        self.UGC_gas_path_system_obj.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update
        self.ZOS_gas_path_system_obj.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update
        self.UFC_gas_path_system_obj.update()
        self.ZOS_gas_path_system_obj.update()
        self.UGC_gas_path_system_obj.update()
        self.Zero_carlibration_obj = Zero_Carlibration()
        self.Zero_carlibration_obj.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update
        self.Zero_carlibration_obj.update()
        self.Range_carlibration_obj = Range_Carlibration()
        self.Range_carlibration_obj.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update
        self.Range_carlibration_obj.update()
        self.Startup_air_calibration_obj = Startup_Air_Calibration()
        self.Startup_air_calibration_obj.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update
        self.Startup_air_calibration_obj.update()
        self.Startup_o2_span_air_calibration_obj = Startup_O2_Span_Air_Calibration()
        self.Startup_o2_span_air_calibration_obj.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update
        self.Startup_o2_span_air_calibration_obj.update()
        self.Startup_co2_air_calibration_obj = Startup_CO2_Air_Calibration()
        self.Startup_co2_air_calibration_obj.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update
        self.Startup_co2_air_calibration_obj.update()
        self.UFC_gas_state_check_obj = UFC_Gas_State_Check()
        self.UFC_gas_state_check_obj.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update
        self.UFC_gas_state_check_obj.update()
        global read_queue_data_thread
        read_queue_data_thread.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update
        read_queue_data_thread.queue = global_setting.get_setting("send_message_queue")
        if read_queue_data_thread is not None and not read_queue_data_thread.isRunning():
            read_queue_data_thread.start()

    def update_start_state(self, sender, **kwargs):
        if self.monitor_start_state_Thread is not None:
            self.monitor_start_state_Thread.stop()
        self.close_timers()

        self.update_status_main_signal_gui_update.send(f"{time_util.get_format_from_time(time.time())} | 气路启动完成")
        logger.info(f"更新启动完成状态{'-' * 100}")
        global auto_wait_event
        auto_wait_event.set()
        auto_wait_event.clear()
        queue = global_setting.get_setting("queue", None)
        if queue is not None:
            # 通知主界面将主界面的开始实验弹窗给关闭
            queue.put(ObjectQueueItem(origin='UFC_UGC_ZOS_index', to='MainWindow_index',
                                      title='close_start_experiment_dialog',

                                      time=time_util.get_format_from_time(time.time())))
            pass

    def _prepare_new_start(self):
        """Reset shutdown state shared by every experiment startup path."""
        global stop_flag, wait_zero_calibration_finished_event, wait_span_calibration_finished_event
        stop_flag = False
        self._experiment_stop_requested = False
        self._shutdown_errors = []
        global_setting.set_setting("calibration_cancel_requested", False)
        o2_calibration = getattr(self, "Startup_o2_span_air_calibration_obj", None)
        reset_o2_stop = getattr(o2_calibration, "reset_stop", None)
        if callable(reset_o2_stop):
            reset_o2_stop()
        # Do not let a previous run's completion signal release the next
        # calibration stage immediately.  The queue thread clears these after
        # publishing the UI event, but a new start can race that clear.
        wait_zero_calibration_finished_event.clear()
        wait_span_calibration_finished_event.clear()
        # A failed/aborted combined run must not leave the legacy workers in
        # CO2-only or keep-gas-path mode for the next experiment.
        for worker in (self.Zero_carlibration_obj, self.Range_carlibration_obj):
            if worker is not None:
                worker.co2_only_mode = False
                worker.keep_gas_path_open_after_calibration = False
                worker.gas_path_prepared_before_calibration = False

    def start_btn_handle_with_calibration(self):
        """先按流程完成调零+调span，再重新启动正常实验气路"""
        self._prepare_new_start()

        return AsyPromise(
            lambda resolve, reject: self._run_callback_steps(
                [
                    self.calibration_start,
                    self.ZOS_gas_path_system_obj.start,
                    self.UFC_gas_path_system_obj.start,
                    self.UGC_gas_path_system_obj.start,
                    self.finish_start,
                ],
                resolve,
                reject,
            )
        )

    def start_btn_handle_with_single_calibration(self, calibration):
        """执行选定的单项校准后，再按正常流程启动气路。"""
        self._prepare_new_start()
        calibration_handler = (
            self.zero_carlibration_obj_calibrate_wrapped
            if calibration == "zero"
            else self.span_carlibration_obj_calibrate_wrapped
        )
        return AsyPromise(
            lambda resolve, reject: self._run_callback_steps(
                [
                    calibration_handler,
                    self.ZOS_gas_path_system_obj.start,
                    self.UFC_gas_path_system_obj.start,
                    self.UGC_gas_path_system_obj.start,
                    self.finish_start,
                ],
                resolve,
                reject,
            )
        )

    def start_btn_handle_with_air_calibration(self):
        """启动气路，并在进入正常实验前执行 Air 空气校准。"""
        self._prepare_new_start()

        return (
            # 步骤0必须位于所有气路启动命令之前。
            AsyPromise(self.UFC_gas_state_check_obj.read_start_status)
            .then(self._remember_ufc_start_status)
            # 步骤1：打开 UGC Air 电磁阀。
            .then(lambda _: AsyPromise(self.UGC_gas_path_system_obj.start))
            # 步骤2：初始化 ZOS，并发送 Air 校准开始指令。
            .then(lambda _: AsyPromise(self.ZOS_gas_path_system_obj.start))
            .then(lambda _: AsyPromise(self.Startup_air_calibration_obj.start))
            # 步骤3、4：根据步骤0结果决定执行或跳过。
            .then(lambda _: AsyPromise(self.UFC_gas_path_system_obj.start))
            .then(lambda _: AsyPromise(
                self.Startup_air_calibration_obj.wait_air_calibration_prepare
            ))
            .then(lambda _: AsyPromise(self.Startup_air_calibration_obj.run))
            .then(lambda _: AsyPromise(self.Startup_air_calibration_obj.stop))
            .then(lambda _: AsyPromise(self.finish_start))
            .catch(self._reject_air_start)
        )

    def _reject_air_start(self, error):
        self.calibration_log_recorder.record(
            "STARTUP_AIR_CALIBRATION",
            {"type": "calibration_failed", "value": f"Air气路启动流程失败: {error}"},
        )
        return self._rollback_failed_calibrated_start(
            error,
            "Air气路启动流程失败",
        )

    def co2_zero_calibration_obj_calibrate_wrapped(self, resolve, reject):
        """Run the established CO2 zero-calibration worker without its legacy O2 zero stage."""
        worker = self.Zero_carlibration_obj
        previous_co2_only_mode = worker.co2_only_mode
        previous_keep_gas_path = getattr(
            worker, "keep_gas_path_open_after_calibration", False
        )
        previous_gas_path_prepared = getattr(
            worker, "gas_path_prepared_before_calibration", False
        )
        worker.co2_only_mode = True
        worker.keep_gas_path_open_after_calibration = True
        worker.gas_path_prepared_before_calibration = True

        def restore_worker_flags():
            worker.co2_only_mode = previous_co2_only_mode
            worker.keep_gas_path_open_after_calibration = previous_keep_gas_path
            worker.gas_path_prepared_before_calibration = previous_gas_path_prepared

        def on_resolve(*args):
            restore_worker_flags()
            resolve(*args)

        def on_reject(error=None):
            restore_worker_flags()
            reject(error)

        try:
            self.zero_carlibration_obj_calibrate_wrapped(on_resolve, on_reject)
        except Exception as exc:
            restore_worker_flags()
            reject(exc)

    def co2_span_calibration_obj_calibrate_wrapped(self, resolve, reject):
        """Run only the CO2 half of the established Span calibration worker."""
        worker = self.Range_carlibration_obj
        previous_co2_only_mode = worker.co2_only_mode
        previous_keep_gas_path = getattr(
            worker, "keep_gas_path_open_after_calibration", False
        )
        previous_gas_path_prepared = getattr(
            worker, "gas_path_prepared_before_calibration", False
        )
        worker.co2_only_mode = True
        worker.keep_gas_path_open_after_calibration = True
        worker.gas_path_prepared_before_calibration = True

        def restore_worker_flags():
            worker.co2_only_mode = previous_co2_only_mode
            worker.keep_gas_path_open_after_calibration = previous_keep_gas_path
            worker.gas_path_prepared_before_calibration = previous_gas_path_prepared

        def on_resolve(*args):
            restore_worker_flags()
            resolve(*args)

        def on_reject(error=None):
            restore_worker_flags()
            reject(error)

        try:
            self.span_carlibration_obj_calibrate_wrapped(on_resolve, on_reject)
        except Exception as exc:
            restore_worker_flags()
            reject(exc)

    def _co2_zero_then_read_ufc_start_status(self, resolve, reject):
        """Run CO2 zero first and forward the actual UFC status dictionary.

        Keep both callback-style steps explicitly connected so the UFC status
        dictionary is forwarded unchanged after CO2 zero finishes.
        """

        def read_ufc_status_after_zero(*_):
            try:
                self.UFC_gas_state_check_obj.read_start_status(resolve, reject)
            except Exception as exc:
                reject(exc)

        try:
            self.co2_zero_calibration_obj_calibrate_wrapped(
                read_ufc_status_after_zero,
                reject,
            )
        except Exception as exc:
            reject(exc)

    def _prepare_startup_calibration_cages(self):
        experiment_settings = global_setting.get_setting("experiment_setting", None)
        groups = getattr(experiment_settings, "groups", []) if experiment_settings is not None else []
        cage_ids = []
        for group in groups:
            try:
                cage_ids.append(int(group.id))
            except (AttributeError, TypeError, ValueError):
                continue
        if cage_ids:
            global_setting.set_setting("mouse_cages", cage_ids)

    def start_btn_handle_with_o2_span_air_calibration(self, include_co2_zero=False):
        """Run the documented startup calibration sequence before the experiment.

        The complete mode is CO2 zero -> CO2 Span -> O2 Span -> shared
        O2/CO2 Air acquisition.  The historical O2-only mode remains available
        for saved configurations and keeps its previous behavior.
        """
        self._prepare_new_start()
        self._prepare_startup_calibration_cages()
        if include_co2_zero:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动完整空气校准：前置气路准备 → CO₂校零 → CO₂ Span → O₂ Span → O₂/CO₂ Air同时采集",
                title=GapSystem_Running_Type.STARTUP_AIR_CALIBRATION,
            )
        else:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动O₂ Span和空气校准：正在按顺序启动气路",
                title=GapSystem_Running_Type.STARTUP_AIR_CALIBRATION,
            )

        # 文档A段只执行一次：先读取UFC/气泵状态，再打开空气气路、
        # 初始化ZOS、配置九路流量并按需开启UFC和气泵。完整校准必须在
        # 这套前置气路准备完成后才进入CO2零点，不能在零点结束后重启气路。
        chain = AsyPromise(self.UFC_gas_state_check_obj.read_start_status)

        chain = (
            chain
            .then(self._remember_ufc_start_status)
            .then(lambda _: AsyPromise(self.ZOS_gas_path_system_obj.start))
            .then(lambda _: AsyPromise(self.UGC_gas_path_system_obj.start))
            .then(lambda _: AsyPromise(self.UFC_gas_path_system_obj.start))
        )

        if include_co2_zero:
            chain = (
                chain
                .then(lambda _: AsyPromise(
                    self.co2_zero_calibration_obj_calibrate_wrapped
                ))
                .then(lambda _: AsyPromise(
                    self.co2_span_calibration_obj_calibrate_wrapped
                ))
                # CO2零点前后及CO2 Span阶段都保持UFC/气泵开启，
                # 按1011文档直接进入O2 Span，不再重复启动UFC。
                .then(lambda _: AsyPromise(
                    self.Startup_o2_span_air_calibration_obj.run_span_only_keep_span
                ))
                .then(self._handoff_o2_span_handler_to_full_air)
                .then(lambda _: AsyPromise(
                    self.Startup_o2_span_air_calibration_obj.prepare_air_path
                ))
            )
        else:
            chain = (
                chain
                .then(lambda _: AsyPromise(
                    self.Startup_o2_span_air_calibration_obj.run_span_only
                ))
                .then(self._handoff_o2_span_handler_to_full_air)
            )

        return (
            chain
            .then(lambda _: AsyPromise(self.Startup_air_calibration_obj.start))
            .then(lambda _: AsyPromise(
                self.Startup_air_calibration_obj.wait_air_calibration_prepare
            ))
            .then(lambda _: AsyPromise(self.Startup_air_calibration_obj.run))
            .then(lambda _: AsyPromise(self.Startup_air_calibration_obj.stop))
            .then(lambda _: AsyPromise(
                self.Startup_o2_span_air_calibration_obj.complete_after_full_air
            ))
            .then(lambda _: AsyPromise(self.finish_start))
            .catch(self._reject_o2_span_air_start)
        )

    def _handoff_o2_span_handler_to_full_air(self, handler):
        if isinstance(handler, AsyPromise) or not callable(
                getattr(handler, "get_status", None)
        ):
            raise TypeError(
                "O2 Span校准结果不是有效处理器，无法进入完整Air空气校准"
            )
        self.Startup_air_calibration_obj.continue_full_o2_calibration(
            handler,
            stop_checker=self.Startup_o2_span_air_calibration_obj.is_stop_requested,
        )
        return handler

    def _reject_o2_span_air_start(self, error):
        self.Startup_air_calibration_obj.clear_continued_o2_calibration()
        self.Startup_o2_span_air_calibration_obj.request_stop()
        self.Startup_o2_span_air_calibration_obj.abort_pending_calibration()

        # A stop-button cancellation is not a failed calibration.  The stop
        # handler owns the one documented hardware shutdown sequence; running
        # the startup-failure rollback here would race it and can reopen gas
        # paths after the pump has already been switched off.
        if self._experiment_stop_requested:
            message = f"实验停止请求已取消启动校准流程：{error}；继续执行统一结束流程"
            logger.info(message)
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | {message}",
                title=GapSystem_Running_Type.STARTUP_AIR_CALIBRATION,
            )
            return None

        try:
            self.Startup_o2_span_air_calibration_obj._restore_safe_gas_state()
        except Exception:
            logger.exception("O2 Span+空气校准失败后的安全气路恢复异常")
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | O₂ Span+空气启动流程失败：{error}",
            title=GapSystem_Running_Type.STARTUP_AIR_CALIBRATION,
        )
        self.calibration_log_recorder.record(
            "STARTUP_O2_SPAN_AIR_CALIBRATION",
            {"type": "calibration_failed", "value": f"O2 Span+空气校准启动流程失败: {error}"},
        )
        return self._rollback_failed_calibrated_start(
            error,
            "O2 Span+空气校准启动流程失败",
        )

    def _rollback_failed_calibrated_start(self, error, message_prefix):
        """Cancel calibration, report startup failure and close partial gas paths."""
        global stop_flag
        stop_flag = True
        global_setting.set_setting("calibration_cancel_requested", True)
        global_setting.set_setting("is_calibrating", False)
        global_setting.set_setting("current_calibration_type", None)

        message = f"{message_prefix}：{error}"
        logger.error(message)
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(ObjectQueueItem(
                origin="UFC_UGC_ZOS_index",
                to="MainWindow_index",
                title="start_experiment_failed",
                data=message,
                time=time_util.get_format_from_time(time.time()),
            ))

        # On abnormal startup, stop UFC first so the pump/flow controller can
        # no longer keep drawing gas, then close UGC and ZOS.
        return self._rollback_failed_start_paths(
            error,
            [
                ("UFC", self.UFC_gas_path_system_obj),
                ("UGC", self.UGC_gas_path_system_obj),
                ("ZOS", self.ZOS_gas_path_system_obj),
            ],
            0,
        )

    def _rollback_failed_start_paths(self, original_error, paths, index):
        if index >= len(paths):
            self.stop()
            return AsyPromise.reject_immediately(original_error)

        module_name, path_system = paths[index]
        if path_system is None:
            return self._rollback_failed_start_paths(
                original_error, paths, index + 1
            )

        try:
            stop_promise = AsyPromise(path_system.stop)
        except Exception as cleanup_error:
            logger.exception(f"启动失败后关闭{module_name}异常：{cleanup_error}")
            return self._rollback_failed_start_paths(
                original_error, paths, index + 1
            )

        return stop_promise.then(
            lambda _: self._rollback_failed_start_paths(
                original_error, paths, index + 1
            ),
            lambda cleanup_error: self._continue_failed_start_rollback(
                original_error,
                module_name,
                cleanup_error,
                paths,
                index + 1,
            ),
        )

    def _continue_failed_start_rollback(
            self, original_error, module_name, cleanup_error, paths, next_index
    ):
        logger.error(f"启动失败后关闭{module_name}未确认：{cleanup_error}")
        return self._rollback_failed_start_paths(
            original_error, paths, next_index
        )

    def _remember_ufc_start_status(self, startup_status):
        self.UFC_gas_path_system_obj.set_startup_status(startup_status)
        return startup_status

    @property
    def standalone_calibration_active(self):
        with self._standalone_calibration_lock:
            return self._standalone_calibration_active

    def reserve_standalone_calibration(self, kind):
        """Reserve standalone state before its long-running worker starts."""
        kind = str(kind or "").strip().lower()
        label = self.STANDALONE_CALIBRATION_LABELS.get(kind)
        if label is None:
            return {"success": False, "message": f"不支持的独立校准类型：{kind}"}
        if global_setting.get_setting("application_shutdown_requested", False):
            return {"success": False, "message": "应用正在关闭，无法启动独立校准"}
        if global_setting.get_setting("port", None) is None:
            return {"success": False, "message": "未选择串口，无法启动独立校准"}

        with self._standalone_calibration_lock:
            if self._standalone_calibration_active:
                active_label = self.STANDALONE_CALIBRATION_LABELS.get(
                    self._standalone_calibration_kind,
                    "其他独立校准",
                )
                return {
                    "success": False,
                    "message": f"{active_label}正在进行，请等待完成",
                }
            self._standalone_calibration_active = True
            self._standalone_calibration_kind = kind
            self._standalone_shutdown_in_progress = False
            self._standalone_user_stop_requested = False
            self._standalone_air_calibration_started = False
            self._standalone_stop_watchdog_event.clear()

        try:
            # Reset cancellation flags before the request is acknowledged.
            # The worker must not clear them later, otherwise an immediate
            # stop click can race startup and be lost.
            self._prepare_new_start()
            self._runtime_air_stop_event.clear()
        except Exception as exc:
            with self._standalone_calibration_lock:
                self._standalone_calibration_active = False
                self._standalone_calibration_kind = None
            return {
                "success": False,
                "message": f"{label}启动准备失败：{exc}",
            }

        return {
            "success": True,
            "phase": "accepted",
            "kind": kind,
            "label": label,
            "message": f"{label}已启动；完成后只关闭气路，不会启动实验",
        }

    def start_standalone_calibration(self, kind, pre_reserved=False):
        """Start one calibration stage without starting an experiment."""
        kind = str(kind or "").strip().lower()
        label = self.STANDALONE_CALIBRATION_LABELS.get(kind)
        if not pre_reserved:
            reservation = self.reserve_standalone_calibration(kind)
            if not reservation.get("success", False):
                return reservation
        else:
            with self._standalone_calibration_lock:
                reservation_is_valid = (
                    self._standalone_calibration_active
                    and self._standalone_calibration_kind == kind
                )
            if label is None or not reservation_is_valid:
                return {
                    "success": False,
                    "message": "独立校准启动预约已失效",
                }

        try:
            self._prepare_startup_calibration_cages()
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | "
                f"启动独立校准：{label}；本次不会启动实验",
                title=GapSystem_Running_Type.STARTUP_AIR_CALIBRATION,
            )

            chain = (
                self._run_standalone_step(
                    self.UFC_gas_state_check_obj.read_start_status
                )
                .then(self._remember_ufc_start_status)
                .then(lambda _: self._run_standalone_step(
                    self.ZOS_gas_path_system_obj.start
                ))
                .then(lambda _: self._run_standalone_step(
                    self.UGC_gas_path_system_obj.start
                ))
                .then(lambda _: self._run_standalone_step(
                    self.UFC_gas_path_system_obj.start
                ))
            )

            if kind == "co2_zero_span":
                chain = (
                    chain
                    .then(lambda _: self._run_standalone_step(
                        self.co2_zero_calibration_obj_calibrate_wrapped
                    ))
                    .then(lambda _: self._run_standalone_step(
                        self.co2_span_calibration_obj_calibrate_wrapped
                    ))
                )
            elif kind == "o2_span":
                self.Startup_o2_span_air_calibration_obj.reset_stop()
                chain = chain.then(
                    lambda _: self._run_standalone_step(
                        self.Startup_o2_span_air_calibration_obj.run_span_standalone
                    )
                )
            else:
                self.Startup_air_calibration_obj.clear_continued_o2_calibration()
                self.Startup_air_calibration_obj.set_external_stop_checker(
                    self._runtime_air_stop_event.is_set
                )
                chain = (
                    chain
                    .then(lambda _: self._run_standalone_step(
                        self._start_standalone_air_calibration
                    ))
                    .then(lambda _: self._run_standalone_step(
                        self.Startup_air_calibration_obj.wait_air_calibration_prepare
                    ))
                    .then(lambda _: self._run_standalone_step(
                        self.Startup_air_calibration_obj.run
                    ))
                    .then(lambda _: self._run_standalone_step(
                        self._stop_standalone_air_calibration
                    ))
                )

            self._standalone_calibration_chain = chain
            chain.then(
                lambda _: self._finish_standalone_calibration(True, None)
            ).catch(
                lambda error: self._finish_standalone_calibration(False, error)
            )
        except Exception as exc:
            logger.exception(f"启动独立校准{label}失败")
            self._finish_standalone_calibration(False, exc)
            return {"success": False, "message": f"{label}启动失败：{exc}"}

        return reservation if not pre_reserved else {
            "success": True,
            "phase": "accepted",
            "kind": kind,
            "label": label,
            "message": f"{label}已启动；完成后只关闭气路，不会启动实验",
        }

    def _run_standalone_step(self, operation):
        """Do not start another hardware step after a user stop request."""
        with self._standalone_calibration_lock:
            if self._standalone_user_stop_requested:
                raise RuntimeError("用户已请求停止独立校准")
        return AsyPromise(operation)

    def _start_standalone_air_calibration(self, resolve, reject):
        """Track whether standalone Air mode needs its explicit end command."""
        self._standalone_air_calibration_started = True
        self.Startup_air_calibration_obj.start(resolve, reject)

    def _stop_standalone_air_calibration(self, resolve, reject):
        """Send the ZOS Air-calibration end command before closing gas paths."""
        def finish(*args):
            self._standalone_air_calibration_started = False
            resolve(*args)

        self.Startup_air_calibration_obj.stop(finish, reject)

    def stop_standalone_calibration(self):
        """Cancel the active standalone stage and keep the normal safe shutdown."""
        with self._standalone_calibration_lock:
            if not self._standalone_calibration_active:
                return {
                    "success": False,
                    "phase": "rejected",
                    "message": "当前没有正在执行的独立校准",
                }
            kind = self._standalone_calibration_kind
            label = self.STANDALONE_CALIBRATION_LABELS.get(kind, kind)
            if self._standalone_user_stop_requested:
                return {
                    "success": True,
                    "phase": "stopping",
                    "kind": kind,
                    "label": label,
                    "message": f"{label}停止请求已发送，正在执行安全收尾",
                }
            self._standalone_user_stop_requested = True

        # Set every cancellation source immediately.  The active promise chain
        # will reject after its current serial operation unwinds, then the one
        # shared finish path sends the calibration-end command (when needed)
        # and closes ZOS/UFC/UGC in the documented order.
        self._experiment_stop_requested = True
        global_setting.set_setting("calibration_cancel_requested", True)
        self._runtime_air_stop_event.set()
        if self.Startup_o2_span_air_calibration_obj is not None:
            self.Startup_o2_span_air_calibration_obj.request_stop()
        for calibration in (self.Zero_carlibration_obj, self.Range_carlibration_obj):
            if calibration is not None:
                calibration.is_STOP = True

        if self.ZOS_gas_path_system_obj is not None:
            self.ZOS_gas_path_system_obj.is_stop = True
        if (
                self.UFC_gas_path_system_obj is not None
                and self.UFC_gas_path_system_obj.ufc_gas_path_system_start_thread is not None
        ):
            self.UFC_gas_path_system_obj.ufc_gas_path_system_start_thread.is_stop = True
        global stop_flag
        stop_flag = True

        status_message = (
            f"收到停止独立校准请求：{label}；正在终止当前步骤并执行安全收尾"
        )
        logger.warning(status_message)
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | {status_message}",
            title=GapSystem_Running_Type.STARTUP_AIR_CALIBRATION,
        )

        # The normal cancellation callback should enter _finish quickly.  This
        # watchdog only covers a driver/promise that never reports completion;
        # it prevents a stuck calibration from leaving the gas paths running.
        threading.Thread(
            target=self._standalone_stop_watchdog,
            args=(kind,),
            name="standalone-calibration-stop-watchdog",
            daemon=True,
        ).start()
        return {
            "success": True,
            "phase": "stopping",
            "kind": kind,
            "label": label,
            "message": f"{label}正在停止；随后将执行结束校准和安全关气路流程",
        }

    def _standalone_stop_watchdog(self, kind):
        if self._standalone_stop_watchdog_event.wait(15):
            return
        with self._standalone_calibration_lock:
            should_force_finish = (
                self._standalone_calibration_active
                and self._standalone_calibration_kind == kind
                and not self._standalone_shutdown_in_progress
            )
        if should_force_finish:
            logger.error("独立校准停止等待超时，强制进入安全收尾")
            self._finish_standalone_calibration(
                False,
                RuntimeError("独立校准停止等待超时"),
            )

    def _finish_standalone_calibration(self, stage_success, stage_error):
        """Always run the documented gas shutdown after one standalone stage."""
        with self._standalone_calibration_lock:
            if not self._standalone_calibration_active:
                return
            if self._standalone_shutdown_in_progress:
                return
            self._standalone_shutdown_in_progress = True
            kind = self._standalone_calibration_kind
            user_stopped = self._standalone_user_stop_requested
            self._standalone_stop_watchdog_event.set()
        pre_stop_errors = []

        def complete(_=None, stop_error=None):
            errors = []
            if stage_error is not None and not user_stopped:
                errors.append(str(stage_error))
            if stop_error is not None:
                errors.append(f"安全收尾异常：{stop_error}")
            errors.extend(pre_stop_errors)
            errors.extend(str(item) for item in self._shutdown_errors)
            label = self.STANDALONE_CALIBRATION_LABELS.get(kind, kind)
            if user_stopped and not errors:
                success = True
                phase = "stopped"
                message = f"{label}已停止，结束校准及气路安全收尾已完成"
            else:
                success = bool(stage_success and not errors)
                phase = "finished" if success else "failed"
                if user_stopped:
                    message = (
                        f"{label}已请求停止，但安全收尾未确认："
                        f"{'；'.join(errors) or '未知错误'}"
                    )
                else:
                    message = (
                        f"{label}完成，气路已安全关闭，实验未启动"
                        if success
                        else f"{label}失败或收尾未确认：{'；'.join(errors) or '未知错误'}"
                    )
            with self._standalone_calibration_lock:
                self._standalone_calibration_active = False
                self._standalone_calibration_kind = None
                self._standalone_calibration_chain = None
                self._standalone_shutdown_in_progress = False
                self._standalone_user_stop_requested = False
                self._standalone_air_calibration_started = False
            self._publish_standalone_calibration_result(
                success=success,
                kind=kind,
                message=message,
                phase=phase,
            )

        def stop_gas_paths():
            try:
                stop_promise = self.stop_btn_handle()
                stop_promise.then(
                    lambda _: complete()
                ).catch(
                    lambda error: complete(stop_error=error)
                )
            except Exception as exc:
                logger.exception("独立校准安全收尾启动失败")
                complete(stop_error=exc)

        # Air mode has a distinct ZOS "end calibration" command.  If the user
        # stops while waiting or sampling, the promise chain skips its normal
        # stop step, so send that command here before closing the gas paths.
        if kind == "air_o2_co2" and self._standalone_air_calibration_started:
            def air_stop_failed(error):
                self._standalone_air_calibration_started = False
                pre_stop_errors.append(f"Air校准结束指令未确认：{error}")
                self._record_shutdown_error("Air校准结束指令", error)
                stop_gas_paths()

            try:
                AsyPromise(self._stop_standalone_air_calibration).then(
                    lambda _: stop_gas_paths()
                ).catch(air_stop_failed)
            except Exception as exc:
                air_stop_failed(exc)
        else:
            stop_gas_paths()

    def _publish_standalone_calibration_result(
            self, success, kind, message, phase=None
    ):
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(ObjectQueueItem(
                origin="UFC_UGC_ZOS_index",
                to="MainWindow_index",
                title="standalone_calibration_response",
                data={
                    "success": bool(success),
                    "phase": phase or ("finished" if success else "failed"),
                    "kind": kind,
                    "label": self.STANDALONE_CALIBRATION_LABELS.get(kind, kind),
                    "message": str(message),
                },
                time=time_util.get_format_from_time(time.time()),
            ))

    def start_btn_handle_with_co2_air_calibration(self):
        """启动气路，并在进入正常实验前执行 CO2 Air 空气校准。"""
        self._prepare_new_start()

        return (
            AsyPromise(self.ZOS_gas_path_system_obj.start)
            .then(lambda _: AsyPromise(self.UGC_gas_path_system_obj.start))
            .then(lambda _: AsyPromise(self.UFC_gas_path_system_obj.start))
            .then(lambda _: AsyPromise(self.Startup_co2_air_calibration_obj.start))
            .then(lambda _: AsyPromise(
                self.Startup_co2_air_calibration_obj.wait_air_calibration_prepare
            ))
            .then(lambda _: AsyPromise(self.Startup_co2_air_calibration_obj.run))
            .then(lambda _: AsyPromise(self.Startup_co2_air_calibration_obj.stop))
            .then(lambda _: AsyPromise(self.finish_start))
            .catch(self._log_co2_air_failure)
        )

    def _log_co2_air_failure(self, error):
        self.calibration_log_recorder.record(
            "STARTUP_CO2_AIR_CALIBRATION",
            {"type": "calibration_failed", "value": f"CO2 Air气路启动流程失败: {error}"},
        )
        return self._rollback_failed_calibrated_start(
            error,
            "CO2 Air气路启动流程失败",
        )

    def calibration_start(self, resolve, reject):
        self._run_callback_steps(
            [
                self.zero_carlibration_obj_calibrate_wrapped,
                self.span_carlibration_obj_calibrate_wrapped,
            ],
            resolve,
            reject,
        )

    @staticmethod
    def _run_callback_steps(steps, resolve, reject):
        """Run callback-style startup steps strictly in order."""

        def run_step(index):
            if stop_flag or global_setting.get_setting(
                    "calibration_cancel_requested", False
            ):
                reject("实验停止，校准启动链已取消")
                return
            if index >= len(steps):
                resolve()
                return

            settled = False

            def step_resolved(*_):
                nonlocal settled
                if settled:
                    return
                settled = True
                run_step(index + 1)

            def step_rejected(error=None):
                nonlocal settled
                if settled:
                    return
                settled = True
                reject(error or f"启动步骤{index + 1}失败")

            try:
                steps[index](step_resolved, step_rejected)
            except Exception as exc:
                step_rejected(exc)

        run_step(0)

    def zero_carlibration_obj_calibrate_wrapped(self, resolve, reject):
        global wait_zero_calibration_finished_event
        wait_zero_calibration_finished_event.clear()
        worker = self.Zero_carlibration_obj
        completion_event = getattr(worker, "calibration_done_event", None)
        if completion_event is not None:
            completion_event.clear()
        startup_errors = []
        AsyPromise(worker.calibrate).catch(lambda error: startup_errors.append(error))
        if startup_errors:
            error = startup_errors[0]
            logger.error(f"零点标定启动失败: {error}")
            reject(error)
            return
        if completion_event is not None:
            completion_event.wait()
        else:
            wait_zero_calibration_finished_event.wait()
        if getattr(self, "_experiment_stop_requested", False):
            reject("实验停止，CO2校零已取消；阻止后续校准阶段启动")
            return
        runtime_error = getattr(worker, "calibration_error", None)
        if runtime_error:
            logger.error(f"零点标定运行失败: {runtime_error}")
            reject(runtime_error)
            return
        resolve()

    def span_carlibration_obj_calibrate_wrapped(self, resolve, reject):
        global wait_span_calibration_finished_event
        wait_span_calibration_finished_event.clear()
        worker = self.Range_carlibration_obj
        completion_event = getattr(worker, "calibration_done_event", None)
        if completion_event is not None:
            completion_event.clear()
        startup_errors = []
        AsyPromise(worker.calibrate).catch(lambda error: startup_errors.append(error))
        if startup_errors:
            error = startup_errors[0]
            logger.error(f"量程标定启动失败: {error}")
            reject(error)
            return
        if completion_event is not None:
            completion_event.wait()
        else:
            wait_span_calibration_finished_event.wait()
        if getattr(self, "_experiment_stop_requested", False):
            reject("实验停止，CO2 Span已取消；阻止后续校准阶段启动")
            return
        runtime_error = getattr(worker, "calibration_error", None)
        if runtime_error:
            logger.error(f"量程标定运行失败: {runtime_error}")
            reject(runtime_error)
            return
        resolve()

    def start_btn_handle(self):
        self._prepare_new_start()
        p = AsyPromise(self.ZOS_gas_path_system_obj.start).then(
            lambda _: AsyPromise(
                self.UFC_gas_path_system_obj.start,
            ).then(
                lambda _: AsyPromise(self.UGC_gas_path_system_obj.start).then(
                    # 添加UFC运行但是不读取数值 UGC运行但是不读取数值
                    # lambda _: AsyPromise(self.UFC_gas_path_system_obj.run_no_circulation_read).then(
                    #     lambda _: AsyPromise(self.UGC_gas_path_system_obj.run_no_circulation_read).then(
                    #         lambda _: AsyPromise(self.ZOS_gas_path_system_obj.start_zos_cage_pressure_init).then(
                    lambda _: AsyPromise(
                        self.finish_start).then(

                    ).catch(lambda e: logger.error(f"{e}"))
                    #         ).catch(lambda e: logger.error(f"{e}"))
                    #     ).catch(lambda e: logger.error(f"{e}"))
                    # ).catch(lambda e: logger.error(f"{e}"))
                ).catch(lambda e: logger.error(f"{e}"))
            ).catch(lambda e: logger.error(f"{e}"))
        ).catch(lambda e: logger.error(f"{e}"))

        return p

    def finish_start(self, resolve, reject):
        logger.info(f"结束气路启动{'-' * 100}")
        if self.monitor_start_state_Thread is None:
            self.monitor_start_state_Thread = Monitor_start_state_Thread(
                name="Monitor_start_state_Thread",
                UFC_gas_path_system_obj=self.UFC_gas_path_system_obj,
                UGC_gas_path_system_obj=self.UGC_gas_path_system_obj,
                ZOS_gas_path_system_obj=self.ZOS_gas_path_system_obj,
                update_start_state_signal=self.update_start_state_signal
            )
        self.monitor_start_state_Thread.start()
        resolve()

    def run_btn_handle(self):

        global auto_wait_event, stop_flag
        auto_wait_event.wait()
        barrier = global_setting.get_setting("barrier")
        if stop_flag:
            return AsyPromise.reject_immediately("run_btn_handle在启动过程中时遇到停止指令")
        #     让鼠笼内模块开始发送报文
        if barrier is not None and getattr(barrier, "parties", 1) != 4:
            barrier.set_parties(4)
        # wait_UFC_UGC_ZOS_start_event = global_setting.get_setting("wait_UFC_UGC_ZOS_start_event")
        # wait_UFC_UGC_ZOS_start_event.set()
        # wait_UFC_UGC_ZOS_start_event.clear()  # 重置事件
        # 每轮运行发送报文数量 赋值0
        global_setting.set_setting("messages_sent_epoch_for_running", 0)
        global_setting.set_setting("start_time_messages_sent_epoch_for_running", time.time())
        if self.zos_start_timer is not None:
            self.zos_start_timer.stop()
        p = AsyPromise(self.UFC_gas_path_system_obj.run).then(
            lambda v: AsyPromise(
                self.UGC_gas_path_system_obj.run
            ).then(
                lambda v2: AsyPromise(self.ZOS_gas_path_system_obj.run)
                # .then(
                #     lambda v3: AsyPromise(self.remove_waitting_ufc_ugc_zos_event)
                # ).catch( lambda e: logger.error(f"{e}"))
            ).catch(lambda e: logger.error(f"{e}"))
        ).catch(lambda e: logger.error(f"{e}"))

        return p

    # def remove_waitting_ufc_ugc_zos_event(self,resolve,reject):
    #     # 只是第一次执行完才通知鼠笼传感器解除阻塞开始运行 后面的执行不会进入
    #     wait_UFC_UGC_ZOS_start_event = global_setting.get_setting("wait_UFC_UGC_ZOS_start_event")
    #     wait_UFC_UGC_ZOS_start_event.set()
    #     wait_UFC_UGC_ZOS_start_event.clear()  # 重置事件
    #     resolve()
    def stop_btn_handle(self):
        # Mark the user intent first so a concurrently rejected calibration
        # chain is treated as cancellation, not as a second failure rollback.
        self._experiment_stop_requested = True
        # if self.monitor_start_state_Thread is not None:
        #     self.monitor_start_state_Thread.stop()
        #     self.monitor_start_state_Thread.deleteLater()
        #     self.monitor_start_state_Thread = None
        # 如果此时正在启动就关闭，就需要把启动的时候一直在循环的线程和被阻塞的线程给唤醒然后给stop掉
        if self.ZOS_gas_path_system_obj is not None:
            self.ZOS_gas_path_system_obj.is_stop = True
        if self.UFC_gas_path_system_obj is not None and self.UFC_gas_path_system_obj.ufc_gas_path_system_start_thread is not None:
            self.UFC_gas_path_system_obj.ufc_gas_path_system_start_thread.is_stop = True
        global auto_wait_event, stop_flag
        stop_flag = True
        global_setting.set_setting("calibration_cancel_requested", True)
        self._shutdown_errors = []
        self._request_stop_runtime_air_calibration()
        # 先同步设置标定线程的取消标志并等待其退出，再发关气路命令，
        # 避免校准流程在泵关闭后继续发开阀/开泵指令。
        self._request_stop_active_calibrations()
        if self.Startup_o2_span_air_calibration_obj is not None:
            self.Startup_o2_span_air_calibration_obj.request_stop()
            if self.Startup_o2_span_air_calibration_obj.is_running:
                if not self.Startup_o2_span_air_calibration_obj.wait_until_finished(30):
                    self._record_shutdown_error("启动校准", "取消等待超时")
        self._wait_runtime_air_calibration_stopped(timeout=30)
        auto_wait_event.set()
        auto_wait_event.clear()

        self.close_timers()
        # Documented hardware shutdown order:
        # ZOS -> cage, UFC pump off -> wait, UGC air off, UFC off.
        # The UFC close worker invokes the UGC hook after its pump wait and
        # immediately before sending the UFC-off command.
        if (
                self.UFC_gas_path_system_obj is not None
                and self.UFC_gas_path_system_obj.ufc_gas_path_system_close_thread is not None
                and self.UGC_gas_path_system_obj is not None
        ):
            self.UFC_gas_path_system_obj.ufc_gas_path_system_close_thread.before_close_ufc = (
                self.UGC_gas_path_system_obj.close_air_valve_for_shutdown
            )
        return self._stop_gas_paths_then_finish(
            [("ZOS", self.ZOS_gas_path_system_obj),
             ("UFC", self.UFC_gas_path_system_obj),
             ("UGC", self.UGC_gas_path_system_obj)],
            index=0,
        )

    def _record_shutdown_error(self, module_name, error):
        message = f"{module_name}关闭未确认：{error}"
        self._shutdown_errors.append(message)
        logger.error(message)
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(ObjectQueueItem(
                origin="UFC_UGC_ZOS_index",
                to="MainWindow_index",
                title="stop_show_info_except_status_counts",
                data=message,
                time=time_util.get_format_from_time(time.time()),
            ))

    def _request_stop_active_calibrations(self):
        current_type = global_setting.get_setting("current_calibration_type", None)
        active_calibrations = []
        for calibration in (self.Zero_carlibration_obj, self.Range_carlibration_obj):
            if calibration is None:
                continue
            is_active = calibration.isRunning() or current_type == calibration.name
            if not is_active:
                continue

            calibration.is_STOP = True
            try:
                # This method emits the existing stop-time/UI notifications. Its
                # cancellation finish path is hardware-free when is_STOP is set.
                calibration.stop_calibrate(
                    lambda *_: None,
                    lambda error=None: logger.warning(
                        f"{calibration.name}取消回调异常：{error}"
                    ),
                )
            except Exception as exc:
                logger.exception(f"请求取消{calibration.name}失败：{exc}")
                try:
                    calibration.set_calibration_running_state(False)
                except Exception:
                    pass
            try:
                calibration.stop()
            except Exception as exc:
                self._record_shutdown_error(calibration.name, exc)
            active_calibrations.append(calibration)
            logger.warning(f"实验停止：已请求取消{calibration.name}")

        if active_calibrations or global_setting.get_setting("is_calibrating", False):
            global_setting.set_setting("is_calibrating", False)
            global_setting.set_setting("current_calibration_type", None)

        # Do not issue shutdown writes while a calibration worker may still be
        # inside a serial transaction. Calibration steps poll is_STOP and
        # unwind on cancellation; wait for that unwind before touching paths.
        for calibration in active_calibrations:
            try:
                if calibration.isRunning() and not calibration.wait(30000):
                    self._record_shutdown_error(calibration.name, "线程退出等待超时")
            except Exception as exc:
                self._record_shutdown_error(calibration.name, exc)

    def _stop_gas_paths_then_finish(self, paths, index):
        if index >= len(paths):
            return self._stop_active_calibrations_then_finish()

        module_name, path_system = paths[index]
        if path_system is None:
            self._record_shutdown_error(module_name, "气路对象不存在")
            return self._stop_gas_paths_then_finish(paths, index + 1)

        try:
            stop_promise = AsyPromise(path_system.stop)
        except Exception as exc:
            self._record_shutdown_error(module_name, exc)
            return self._stop_gas_paths_then_finish(paths, index + 1)

        return stop_promise.then(
            lambda _: self._stop_gas_paths_then_finish(paths, index + 1)
        ).catch(
            lambda error: (
                self._record_shutdown_error(module_name, error),
                self._stop_gas_paths_then_finish(paths, index + 1),
            )[1]
        )

    def _stop_active_calibrations_then_finish(self):
        """Finish shutdown after the calibration cancellation and path stops."""
        self._request_stop_active_calibrations()
        return AsyPromise(self.finished_stop)

    def finished_stop(self, resolve, reject):
        """
        完成停止
        :return:
        """
        if self.UFC_gas_path_system_obj is not None and self.UFC_gas_path_system_obj.ufc_gas_path_system_close_thread is not None:
            self.UFC_gas_path_system_obj.ufc_gas_path_system_close_thread.stop()
            self.UFC_gas_path_system_obj.ufc_gas_path_system_close_thread.deleteLater()
            self.UFC_gas_path_system_obj.ufc_gas_path_system_close_thread = None
        # 将信号解绑
        if self.update_status_main_signal_gui_update is not None:
            self.update_status_main_signal_gui_update.disconnect(self.logger_info)
            self.update_status_main_signal_gui_update = None
        if self.update_start_state_signal is not None:
            self.update_start_state_signal.disconnect(self.update_start_state)
            self.update_start_state_signal = None

        if self.UFC_gas_path_system_obj is not None:
            self.UFC_gas_path_system_obj = None
        if self.UGC_gas_path_system_obj is not None:
            self.UGC_gas_path_system_obj = None
        if self.ZOS_gas_path_system_obj is not None:
            self.ZOS_gas_path_system_obj = None

        # 返回响应；若某个模块未确认关闭，明确将其作为异常说明回报。
        if self._shutdown_errors:
            stop_message = "气路停止流程已结束，但关闭未确认：" + "；".join(self._shutdown_errors)
            logger.error(stop_message)
        else:
            stop_message = "停止气路完成"
            logger.info(stop_message)
        queue = global_setting.get_setting("queue", None)
        if queue and not self._standalone_shutdown_in_progress:
            queue.put(
                ObjectQueueItem(origin="UFC_UGC_ZOS_index", to="MainWindow_index", title="stop_gap_system_return",
                                data=stop_message,
                                time=time_util.get_format_from_time(time.time())))

        resolve()

    def pause_running_gap_system(self):
        # 暂停正在运行的气路模块
        if self.UFC_gas_path_system_obj is not None and self.UFC_gas_path_system_obj.ufc_gas_path_system_run_thread is not None and not self.UFC_gas_path_system_obj.ufc_gas_path_system_run_thread.isPaused():
            self.UFC_gas_path_system_obj.ufc_gas_path_system_run_thread.pause()
        if self.UGC_gas_path_system_obj is not None and self.UGC_gas_path_system_obj.ugc_gas_path_system_run_thread is not None and not self.UGC_gas_path_system_obj.ugc_gas_path_system_run_thread.isPaused():
            self.UGC_gas_path_system_obj.ugc_gas_path_system_run_thread.pause()
        if self.ZOS_gas_path_system_obj is not None and self.ZOS_gas_path_system_obj.zos_gas_path_system_run_thread is not None and not self.ZOS_gas_path_system_obj.zos_gas_path_system_run_thread.isPaused():
            self.ZOS_gas_path_system_obj.zos_gas_path_system_run_thread.pause()

    def resume_running_gap_system(self):
        # 解除暂停正在运行的气路模块
        if self.UFC_gas_path_system_obj is not None and self.UFC_gas_path_system_obj.ufc_gas_path_system_run_thread is not None and self.UFC_gas_path_system_obj.ufc_gas_path_system_run_thread.isPaused():
            self.UFC_gas_path_system_obj.ufc_gas_path_system_run_thread.resume()
        if self.UGC_gas_path_system_obj is not None and self.UGC_gas_path_system_obj.ugc_gas_path_system_run_thread is not None and self.UGC_gas_path_system_obj.ugc_gas_path_system_run_thread.isPaused():
            self.UGC_gas_path_system_obj.ugc_gas_path_system_run_thread.resume()
        if self.ZOS_gas_path_system_obj is not None and self.ZOS_gas_path_system_obj.zos_gas_path_system_run_thread is not None and self.ZOS_gas_path_system_obj.zos_gas_path_system_run_thread.isPaused():
            self.ZOS_gas_path_system_obj.zos_gas_path_system_run_thread.resume()

    def range_calibration_handle(self):
        """
        量程标定
        :return:
        """

        global_setting.set_setting("calibration_cancel_requested", False)
        p = AsyPromise(
            self.span_carlibration_obj_calibrate_wrapped
        ).then().catch(lambda e: logger.error(f"{e}"))
        return p

    def air_calibration_handle(self):
        """Start the standalone Air workflow without blocking the command loop."""
        with self._runtime_air_lock:
            if self._runtime_air_thread is not None and self._runtime_air_thread.is_alive():
                return {"success": False, "message": "空气校准已在进行中"}
            if self._experiment_stop_requested:
                return {"success": False, "message": "实验正在停止，无法启动空气校准"}
            if self.Startup_air_calibration_obj is None:
                return {"success": False, "message": "空气校准服务尚未初始化"}
            run_threads = (
                getattr(self.UFC_gas_path_system_obj, "ufc_gas_path_system_run_thread", None),
                getattr(self.UGC_gas_path_system_obj, "ugc_gas_path_system_run_thread", None),
                getattr(self.ZOS_gas_path_system_obj, "zos_gas_path_system_run_thread", None),
            )
            if any(
                    thread is None
                    or not callable(getattr(thread, "isRunning", None))
                    or not thread.isRunning()
                    for thread in run_threads
            ):
                return {"success": False, "message": "气路尚未全部进入运行状态，无法启动空气校准"}
            if global_setting.get_setting("is_calibrating", False):
                calibration_type = global_setting.get_setting("current_calibration_type", "其他校准")
                return {
                    "success": False,
                    "message": f"{calibration_type}正在进行，请等待完成后再启动空气校准",
                }

            self._runtime_air_stop_event.clear()
            global_setting.set_setting("calibration_cancel_requested", False)
            global_setting.set_setting("is_calibrating", True)
            global_setting.set_setting("current_calibration_type", "实验中 Air 空气校准")
            self._runtime_air_thread = threading.Thread(
                target=self._run_runtime_air_calibration,
                name="RuntimeAirCalibration",
                daemon=True,
            )
            try:
                self._runtime_air_thread.start()
            except Exception as error:
                self._runtime_air_thread = None
                global_setting.set_setting("is_calibrating", False)
                global_setting.set_setting("current_calibration_type", None)
                logger.exception(f"启动实验中 Air 空气校准线程失败：{error}")
                return {"success": False, "message": f"空气校准线程启动失败：{error}"}
        return {"success": True, "message": "空气校准请求已接收"}

    @staticmethod
    def _run_runtime_air_step(step):
        outcome = {"resolved": False, "error": None, "value": None}

        def resolve(value=None):
            outcome["resolved"] = True
            outcome["value"] = value

        def reject(error=None):
            outcome["error"] = error or "未知错误"

        step(resolve, reject)
        if outcome["error"] is not None:
            error = outcome["error"]
            if isinstance(error, BaseException):
                raise error
            raise RuntimeError(str(error))
        if not outcome["resolved"]:
            raise RuntimeError("空气校准步骤未返回完成状态")
        return outcome["value"]

    def _run_runtime_air_calibration(self):
        air = self.Startup_air_calibration_obj
        original_name = air.name
        start_attempted = False
        stop_notified = False
        failure_message = None
        try:
            air.name = "实验中 Air 空气校准"
            air.clear_continued_o2_calibration()
            air.set_external_stop_checker(self._runtime_air_stop_event.is_set)
            start_attempted = True
            self._run_runtime_air_step(air.start)
            self._run_runtime_air_step(air.wait_air_calibration_prepare)
            self._run_runtime_air_step(air.run)
            self._run_runtime_air_step(air.stop)
            stop_notified = True
        except Exception as error:
            if self._runtime_air_stop_event.is_set():
                logger.warning(f"实验中空气校准已按停止请求退出：{error}")
            else:
                failure_message = f"实验中空气校准失败：{error}"
                logger.exception(failure_message)
                if self.update_status_main_signal_gui_update is not None:
                    self.update_status_main_signal_gui_update.send(
                        f"{time_util.get_format_from_time(time.time())} | {failure_message}",
                        title=GapSystem_Running_Type.STARTUP_AIR_CALIBRATION,
                    )
        finally:
            if start_attempted and not stop_notified:
                try:
                    self._run_runtime_air_step(air.stop)
                    stop_notified = True
                except Exception as stop_error:
                    logger.exception(f"实验中空气校准结束指令失败：{stop_error}")
                    # Keep GUI/thread state consistent even when the hardware
                    # acknowledgement failed; the failure remains in the log.
                    air._finish_stop(lambda: None)
                    stop_notified = True
            air.clear_continued_o2_calibration()
            air.name = original_name
            if global_setting.get_setting("current_calibration_type", None) == "实验中 Air 空气校准":
                global_setting.set_setting("is_calibrating", False)
                global_setting.set_setting("current_calibration_type", None)
            with self._runtime_air_lock:
                if self._runtime_air_thread is threading.current_thread():
                    self._runtime_air_thread = None
            if failure_message:
                self._publish_runtime_air_result(False, failure_message)

    def _publish_runtime_air_result(self, success, message):
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(ObjectQueueItem(
                origin="UFC_UGC_ZOS_index",
                to="MainWindow_index",
                title="runtime_air_calibration_response",
                data={"success": bool(success), "message": str(message)},
                time=time_util.get_format_from_time(time.time()),
            ))

    def _request_stop_runtime_air_calibration(self):
        # Use the same lock as the start path.  This prevents a nearly
        # simultaneous Air click from clearing the stop event and starting a
        # new calibration while the experiment is already shutting down.
        with self._runtime_air_lock:
            self._experiment_stop_requested = True
            self._runtime_air_stop_event.set()

    def _wait_runtime_air_calibration_stopped(self, timeout=30):
        with self._runtime_air_lock:
            thread = self._runtime_air_thread
        if thread is None or thread is threading.current_thread():
            return True
        thread.join(timeout=max(float(timeout), 0.0))
        if thread.is_alive():
            self._record_shutdown_error("实验中空气校准", "取消等待超时")
            return False
        return True

    def zero_calibration_handle(self):
        """
        零点标定
        :return:
        """

        global_setting.set_setting("calibration_cancel_requested", False)
        p = AsyPromise(
            self.zero_carlibration_obj_calibrate_wrapped
        ).then().catch(lambda e: logger.error(f"{e}"))
        return p

    def calibration_btn_start(self):
        """
        按钮点击的标定事件
        :return:
        """
        global_setting.set_setting("calibration_cancel_requested", False)
        return AsyPromise(self.zero_carlibration_obj_calibrate_wrapped).then(
            lambda _: AsyPromise(self.span_carlibration_obj_calibrate_wrapped)
        ).catch(lambda e: logger.error(f"{e}"))

    def stop_range_calibration_handle(self):
        """
        stop_ 量程标定
        :return:
        """

        self.Range_carlibration_obj.update()
        p = AsyPromise(
            self.Range_carlibration_obj.stop_calibrate
        ).then().catch(lambda e: logger.error(f"{e}"))
        return p

    def stop_zero_calibration_handle(self):
        """
        stop_零点标定
        :return:
        """

        self.Zero_carlibration_obj.update()
        p = AsyPromise(
            self.Zero_carlibration_obj.stop_calibrate
        ).then().catch(lambda e: logger.error(f"{e}"))
        return p

    def stop_calibration_btn_start(self):
        """
        stop_按钮点击的标定事件
        :return:
        """

        self.Range_carlibration_obj.update()
        self.Zero_carlibration_obj.update()
        p = AsyPromise(self.Zero_carlibration_obj.stop_calibrate).then(
            lambda _: AsyPromise(
                self.Range_carlibration_obj.stop_calibrate
            ).then(
                lambda r: r()
            ).catch(lambda e: logger.error(f"{e}"))
        ).catch(lambda e: logger.error(f"{e}"))
        return p

    def gas_state_check_handle(self):
        global stop_flag
        if stop_flag:
            return AsyPromise.reject_immediately("gas_state_check_handle 在启动过程中时遇到停止指令")
        self.set_gas_state_check_timer()
        p = AsyPromise(lambda r, e: r()).then().catch(lambda e: logger.error(f"{e}"))
        return p

    def gas_state_check(self):
        p = AsyPromise(self.UFC_gas_state_check_obj.state_check).then(
        ).catch(lambda e: logger.error(f"{e}"))
        return p
        # p = AsyPromise(lambda r, e: r()).then().catch( lambda e: logger.error(f"{e}"))
        # return p

    def dosomething(self):
        if self._startup_chain_started:
            time.sleep(0.05)
            return
        self._startup_chain_started = True
        global_setting.set_setting("calibration_cancel_requested", False)
        # 是否自动校准气路
        startup_calibration_mode = global_setting.get_setting("startup_calibration_mode", None)
        is_auto_calibration = global_setting.get_setting("is_auto_calibration", False)
        logger.critical(f"{self.name}<UNK>is_auto_calibration：{is_auto_calibration}")
        if startup_calibration_mode == "air_co2":
            startup_calibration_mode = "air"
        if startup_calibration_mode not in {"none", "air", "zero", "span", "full", "o2_span_air", "co2_o2_span_air"}:
            # Only an explicitly selected startup mode may enter calibration.
            startup_calibration_mode = "none"
        global_setting.set_setting("startup_calibration_mode", startup_calibration_mode)
        global_setting.set_setting("is_auto_calibration", startup_calibration_mode != "none")
        logger.critical(f"{self.name} startup_calibration_mode: {startup_calibration_mode}")
        if startup_calibration_mode == "full":
            self.start_btn_handle_with_calibration().then(
                lambda _: self.run_btn_handle().then(
                    lambda __: self.gas_state_check_handle().then(
                        self.stop
                    ).catch(lambda e: logger.error(f"{e}"))
                ).catch(lambda e: logger.error(f"{e}"))
            ).catch(lambda e: logger.error(f"{e}"))
        elif startup_calibration_mode == "zero":
            self.start_btn_handle_with_single_calibration("zero").then(
                lambda _: self.run_btn_handle().then(
                    lambda _: self.gas_state_check_handle().then(self.stop)
                ).catch(lambda e: logger.error(f"{e}"))
            ).catch(lambda e: logger.error(f"{e}"))
        elif startup_calibration_mode == "span":
            self.start_btn_handle_with_single_calibration("span").then(
                lambda _: self.run_btn_handle().then(
                    lambda _: self.gas_state_check_handle().then(self.stop)
                ).catch(lambda e: logger.error(f"{e}"))
            ).catch(lambda e: logger.error(f"{e}"))
        elif startup_calibration_mode == "air":
            self.start_btn_handle_with_air_calibration().then(
                lambda _: self.run_btn_handle().then(
                    lambda _: self.gas_state_check_handle().then(
                        self.stop
                    ).catch(lambda e: logger.error(f"{e}"))
                ).catch(lambda e: logger.error(f"{e}"))
            ).catch(lambda e: logger.error(f"{e}"))
        elif startup_calibration_mode in {"o2_span_air", "co2_o2_span_air"}:
            include_co2_zero = startup_calibration_mode == "co2_o2_span_air"
            self.start_btn_handle_with_o2_span_air_calibration(
                include_co2_zero=include_co2_zero
            ).then(
                lambda _: self.run_btn_handle().then(
                    lambda __: self.gas_state_check_handle().then(
                        self.stop
                    ).catch(lambda e: logger.error(f"{e}"))
                ).catch(lambda e: logger.error(f"{e}"))
            ).catch(lambda e: logger.error(f"{e}"))
        else:
            self.start_btn_handle().then(
                lambda _: self.run_btn_handle().then(
                    lambda _: self.gas_state_check_handle().then(
                        self.stop
                    ).catch(lambda e: logger.error(f"{e}"))
                ).catch(lambda e: logger.error(f"{e}"))
            ).catch(lambda e: logger.error(f"{e}"))

        # self.run_btn_handle().then(
        #
        #     self.gas_state_check_handle().then(
        #         self.stop()
        #     )
        #
        # )

    def pause(self):
        if self.UFC_gas_path_system_obj is not None and self.UFC_gas_path_system_obj.ufc_gas_path_system_run_thread is not None:
            self.UFC_gas_path_system_obj.ufc_gas_path_system_run_thread.pause()
        if self.UGC_gas_path_system_obj is not None and self.UGC_gas_path_system_obj.ugc_gas_path_system_run_thread is not None:
            self.UGC_gas_path_system_obj.ugc_gas_path_system_run_thread.pause()
        if self.ZOS_gas_path_system_obj is not None and self.ZOS_gas_path_system_obj.zos_gas_path_system_run_thread is not None:
            self.ZOS_gas_path_system_obj.zos_gas_path_system_run_thread.pause()

        if self.monitor_start_state_Thread is not None:
            self.monitor_start_state_Thread.pause()
        self.pause_timers()
        self.ispause = True

    def resume(self):
        if self.UFC_gas_path_system_obj is not None and self.UFC_gas_path_system_obj.ufc_gas_path_system_run_thread is not None:
            self.UFC_gas_path_system_obj.ufc_gas_path_system_run_thread.resume()
        if self.UGC_gas_path_system_obj is not None and self.UGC_gas_path_system_obj.ugc_gas_path_system_run_thread is not None:
            self.UGC_gas_path_system_obj.ugc_gas_path_system_run_thread.resume()
        if self.ZOS_gas_path_system_obj is not None and self.ZOS_gas_path_system_obj.zos_gas_path_system_run_thread is not None:
            self.ZOS_gas_path_system_obj.zos_gas_path_system_run_thread.resume()

        if self.monitor_start_state_Thread is not None:
            self.monitor_start_state_Thread.resume()
        self.resume_timers()
        self.ispause = False

    def set_start_timers(self, resolve, reject):
        # self.set_zos_start_timer()  # 新流程ZOS启动无需预热定时器
        # self.set_ufc_start_timer()
        resolve()

    def pause_timers(self):
        if self.zos_start_timer is not None and self.zos_start_timer.is_active():
            self.zos_start_timer.pause()

        if self.gas_state_check_timer is not None and self.gas_state_check_timer.is_active():
            self.gas_state_check_timer.pause()

    def resume_timers(self):
        if self.zos_start_timer is not None:
            self.zos_start_timer.resume()

        if self.gas_state_check_timer is not None:
            self.gas_state_check_timer.resume()

    def close_timers(self):
        if self.zos_start_timer is not None:
            self.zos_start_timer.stop()

        if self.gas_state_check_timer is not None:
            self.gas_state_check_timer.stop()

    def set_gas_state_check_timer(self):
        self.gas_state_check_timer = PeriodicTimer(
            interval_ms=float(
                global_setting.get_setting('UFC_UGC_ZOS_config')['Gas_State_Check']['start_time_delay']) * 1000,
            max_duration_ms=None,
            task=None,
            run_in_thread=True,
            run_immediately=True
        )
        self.gas_state_check_timer.set_task(self.gas_state_check)
        self.gas_state_check_timer.start()

    def set_zos_start_timer(self):
        self.zos_start_timer = PeriodicTimer(
            interval_ms=float(global_setting.get_setting('UFC_UGC_ZOS_config')['ZOS']['start_time_delay']) * 1000,
            max_duration_ms=float(global_setting.get_setting('UFC_UGC_ZOS_config')['ZOS']['start_time']) * 1000,
            task=None,
            run_in_thread=True,
            run_immediately=True
        )
        self.zos_start_timer.set_task(self.ZOS_gas_path_system_obj.zos_start_timer_task)
        self.zos_start_timer.start()
