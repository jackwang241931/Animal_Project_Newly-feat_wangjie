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
from Service.UFC_UGC_ZOS_Service.function.startup_air_calibration.Startup_O2_Span_Air_Calibration import Startup_O2_Span_Air_Calibration
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
        self.update_start_state_signal =update_start_state_signal

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
    def __init__(self):
        super().__init__(name="UFC_UGC_ZOS_index" )
        self.ispause = False
        # dosomething() is driven by MyQThread's loop, while the gas startup
        # chain itself is asynchronous.  Without this guard the loop starts a
        # second chain before the first one completes, producing many repeated
        # "ZOS 正在启动" commands and stale callbacks during shutdown.
        self._startup_chain_started = False

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

        self.update_status_main_signal_gui_update: Signal=None
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

    def logger_info(self, text,**kwargs):
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
                        if isinstance(text,dict):
                            match text.get("type",None):
                                case 'set_start_zero_calibration_time':
                                    queue.put(
                                        ObjectQueueItem(origin='UFC_UGC_ZOS_index', to='MainWindow_index',
                                                        title='set_start_zero_calibration_time',
                                                        data=text.get('value',''),
                                                        time=time_util.get_format_from_time(time.time())))
                                    # 暂停正在运行的气路模块
                                    self.pause_running_gap_system()
                                    pass
                                case 'set_stop_zero_calibration_time':
                                    queue.put(
                                        ObjectQueueItem(origin='UFC_UGC_ZOS_index', to='MainWindow_index',
                                                        title='set_stop_zero_calibration_time',
                                                        data=text.get('value',''),
                                                        time=time_util.get_format_from_time(time.time())))
                                    #解除运行线程暂停
                                    self.resume_running_gap_system()
                                    global wait_zero_calibration_finished_event
                                    wait_zero_calibration_finished_event.set()
                                    wait_zero_calibration_finished_event.clear()
                                    pass
                                case 'set_start_span_calibration_time':
                                    queue.put(
                                        ObjectQueueItem(origin='UFC_UGC_ZOS_index', to='MainWindow_index',
                                                        title='set_start_span_calibration_time',
                                                        data=text.get('value',''),
                                                        time=time_util.get_format_from_time(time.time())))
                                    # 暂停正在运行的气路模块
                                    self.pause_running_gap_system()
                                    pass
                                case 'set_stop_span_calibration_time':
                                    queue.put(
                                        ObjectQueueItem(origin='UFC_UGC_ZOS_index', to='MainWindow_index',
                                                        title='set_stop_span_calibration_time',
                                                        data=text.get('value',''),
                                                        time=time_util.get_format_from_time(time.time())))
                                    # 解除运行线程暂停
                                    self.resume_running_gap_system()
                                    global wait_span_calibration_finished_event
                                    wait_span_calibration_finished_event.set()
                                    wait_span_calibration_finished_event.clear()
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
                                                        data=text.get('value',{}),
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
                        queue.put(ObjectQueueItem(origin="UFC_UGC_ZOS_index", to="MainWindow_index", title="gap_system_running_state",data=text,
                                      time=time_util.get_format_from_time(time.time())))
                case _:
                    queue = global_setting.get_setting("queue", None)
                    if queue:
                        queue.put(ObjectQueueItem(origin="UFC_UGC_ZOS_index", to="MainWindow_index", title="gap_system_running_state",data=text,
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
        logger.info(f"更新启动完成状态{'-'*100 }")
        global auto_wait_event
        auto_wait_event.set()
        auto_wait_event.clear()
        queue = global_setting.get_setting("queue", None)
        if queue is not None :

            # 通知主界面将主界面的开始实验弹窗给关闭
            queue.put(ObjectQueueItem(origin='UFC_UGC_ZOS_index', to='MainWindow_index', title='close_start_experiment_dialog',

                                                   time=time_util.get_format_from_time(time.time())))
            pass
    def start_btn_handle_with_calibration(self):
        """先按流程完成调零+调span，再重新启动正常实验气路"""
        global stop_flag
        stop_flag = False

        p = AsyPromise(self.calibration_start).then(
            lambda _: AsyPromise(self.ZOS_gas_path_system_obj.start).then(
                lambda _: AsyPromise(self.UFC_gas_path_system_obj.start).then(
                    lambda _: AsyPromise(self.UGC_gas_path_system_obj.start).then(
                        lambda _: AsyPromise(self.finish_start).then(
                        ).catch(lambda e: logger.error(f"{e}"))
                    ).catch(lambda e: logger.error(f"{e}"))
                ).catch(lambda e: logger.error(f"{e}"))
            ).catch(lambda e: logger.error(f"{e}"))
        ).catch(lambda e: logger.error(f"{e}"))



        return p

    def start_btn_handle_with_single_calibration(self, calibration):
        """执行选定的单项校准后，再按正常流程启动气路。"""
        global stop_flag
        stop_flag = False
        calibration_handler = (
            self.zero_carlibration_obj_calibrate_wrapped
            if calibration == "zero"
            else self.span_carlibration_obj_calibrate_wrapped
        )
        return AsyPromise(calibration_handler).then(
            lambda _: AsyPromise(self.ZOS_gas_path_system_obj.start).then(
                lambda _: AsyPromise(self.UFC_gas_path_system_obj.start).then(
                    lambda _: AsyPromise(self.UGC_gas_path_system_obj.start).then(
                        lambda _: AsyPromise(self.finish_start)
                    ).catch(lambda e: logger.error(f"{e}"))
                ).catch(lambda e: logger.error(f"{e}"))
            ).catch(lambda e: logger.error(f"{e}"))
        ).catch(lambda e: logger.error(f"{e}"))

    def start_btn_handle_with_air_calibration(self):
        """启动气路，并在进入正常实验前执行 Air 空气校准。"""
        global stop_flag
        stop_flag = False

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
        return AsyPromise.log_and_reject(
            error, logger=logger, message_prefix="Air气路启动流程失败"
        )

    def co2_zero_calibration_obj_calibrate_wrapped(self, resolve, reject):
        """Run the established CO2 zero-calibration worker without its legacy O2 zero stage."""
        self.Zero_carlibration_obj.co2_only_mode = True
        try:
            self.zero_carlibration_obj_calibrate_wrapped(resolve, reject)
        finally:
            self.Zero_carlibration_obj.co2_only_mode = False

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
        """Start the normal gas path, run the supplied O2 Span→Air routine, then continue startup."""
        global stop_flag
        stop_flag = False
        self._prepare_startup_calibration_cages()
        if include_co2_zero:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动组合校准：先执行旧版CO₂校零，再执行O₂ Span和空气校准",
                title=GapSystem_Running_Type.STARTUP_AIR_CALIBRATION,
            )
            chain = AsyPromise(self.co2_zero_calibration_obj_calibrate_wrapped)
            chain = chain.then(
                lambda _: AsyPromise(self.UFC_gas_state_check_obj.read_start_status)
            )
        else:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动O₂ Span和空气校准：正在按顺序启动气路",
                title=GapSystem_Running_Type.STARTUP_AIR_CALIBRATION,
            )
            chain = AsyPromise(self.UFC_gas_state_check_obj.read_start_status)

        return (
            chain
            .then(self._remember_ufc_start_status)
            .then(lambda _: AsyPromise(self.UGC_gas_path_system_obj.start))
            .then(lambda _: AsyPromise(self.ZOS_gas_path_system_obj.start))
            .then(lambda _: AsyPromise(self.UFC_gas_path_system_obj.start))
            .then(lambda _: AsyPromise(self.Startup_o2_span_air_calibration_obj.run))
            .then(lambda _: AsyPromise(self.finish_start))
            .catch(self._reject_o2_span_air_start)
        )

    def _reject_o2_span_air_start(self, error):
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | O₂ Span+空气启动流程失败：{error}",
            title=GapSystem_Running_Type.STARTUP_AIR_CALIBRATION,
        )
        self.calibration_log_recorder.record(
            "STARTUP_O2_SPAN_AIR_CALIBRATION",
            {"type": "calibration_failed", "value": f"O2 Span+空气校准启动流程失败: {error}"},
        )
        return AsyPromise.log_and_reject(
            error, logger=logger, message_prefix="O2 Span+空气校准启动流程失败"
        )
    def _remember_ufc_start_status(self, startup_status):
        self.UFC_gas_path_system_obj.set_startup_status(startup_status)
        return startup_status

    def start_btn_handle_with_co2_air_calibration(self):
        """启动气路，并在进入正常实验前执行 CO2 Air 空气校准。"""
        global stop_flag
        stop_flag = False

        p = AsyPromise(self.ZOS_gas_path_system_obj.start).then(
            lambda _: AsyPromise(self.UGC_gas_path_system_obj.start).then(
                lambda _: AsyPromise(self.UFC_gas_path_system_obj.start).then(
                    lambda _: AsyPromise(self.Startup_co2_air_calibration_obj.start).then(
                        lambda _: AsyPromise(self.Startup_co2_air_calibration_obj.wait_air_calibration_prepare).then(
                            lambda _: AsyPromise(self.Startup_co2_air_calibration_obj.run).then(
                                lambda _: AsyPromise(self.Startup_co2_air_calibration_obj.stop).then(
                                    lambda _: AsyPromise(self.finish_start)
                                ).catch(self._log_co2_air_failure)
                            ).catch(self._log_co2_air_failure)
                        ).catch(self._log_co2_air_failure)
                    ).catch(self._log_co2_air_failure)
                ).catch(self._log_co2_air_failure)
            ).catch(self._log_co2_air_failure)
        ).catch(self._log_co2_air_failure)

        return p

    def _log_co2_air_failure(self, error):
        self.calibration_log_recorder.record(
            "STARTUP_CO2_AIR_CALIBRATION",
            {"type": "calibration_failed", "value": f"CO2 Air气路启动流程失败: {error}"},
        )
        logger.error(f"{error}")

    def calibration_start(self, resolve, reject):
        AsyPromise(self.zero_carlibration_obj_calibrate_wrapped).then(
            lambda _: AsyPromise(self.span_carlibration_obj_calibrate_wrapped)
        ).then(lambda _: resolve()).catch(lambda e: reject(e))

    def zero_carlibration_obj_calibrate_wrapped(self, resolve, reject):
        AsyPromise(self.Zero_carlibration_obj.calibrate).catch(lambda e: logger.error(f"{e}"))
        global wait_zero_calibration_finished_event
        wait_zero_calibration_finished_event.wait()
        resolve()
        pass

    def span_carlibration_obj_calibrate_wrapped(self, resolve, reject):
        AsyPromise(self.Range_carlibration_obj.calibrate).catch(lambda e: logger.error(f"{e}"))
        global wait_span_calibration_finished_event
        wait_span_calibration_finished_event.wait()
        resolve()
        pass

    def start_btn_handle(self):
        global stop_flag
        stop_flag = False
        p = AsyPromise(self.ZOS_gas_path_system_obj.start).then(
            lambda _:AsyPromise(
                self.UFC_gas_path_system_obj.start,
            ).then(
                lambda _:AsyPromise(self.UGC_gas_path_system_obj.start).then(
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
                ).catch( lambda e: logger.error(f"{e}"))
            ).catch( lambda e: logger.error(f"{e}"))
        ).catch( lambda e: logger.error(f"{e}"))





        return p
    def finish_start(self,resolve,reject):
        logger.info(f"结束气路启动{'-'*100}")
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

        global auto_wait_event,stop_flag
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
        #每轮运行发送报文数量 赋值0
        global_setting.set_setting("messages_sent_epoch_for_running", 0)
        global_setting.set_setting("start_time_messages_sent_epoch_for_running", time.time())
        if self.zos_start_timer is not None :
            self.zos_start_timer.stop()
        p = AsyPromise(self.UFC_gas_path_system_obj.run).then(
            lambda v: AsyPromise(
                self.UGC_gas_path_system_obj.run
            ).then(
                lambda v2: AsyPromise(self.ZOS_gas_path_system_obj.run)
                # .then(
                #     lambda v3: AsyPromise(self.remove_waitting_ufc_ugc_zos_event)
                # ).catch( lambda e: logger.error(f"{e}"))
            ).catch( lambda e: logger.error(f"{e}"))
        ).catch( lambda e: logger.error(f"{e}"))


        return p
    # def remove_waitting_ufc_ugc_zos_event(self,resolve,reject):
    #     # 只是第一次执行完才通知鼠笼传感器解除阻塞开始运行 后面的执行不会进入
    #     wait_UFC_UGC_ZOS_start_event = global_setting.get_setting("wait_UFC_UGC_ZOS_start_event")
    #     wait_UFC_UGC_ZOS_start_event.set()
    #     wait_UFC_UGC_ZOS_start_event.clear()  # 重置事件
    #     resolve()
    def stop_btn_handle(self):
        # if self.monitor_start_state_Thread is not None:
        #     self.monitor_start_state_Thread.stop()
        #     self.monitor_start_state_Thread.deleteLater()
        #     self.monitor_start_state_Thread = None
        # 如果此时正在启动就关闭，就需要把启动的时候一直在循环的线程和被阻塞的线程给唤醒然后给stop掉
        if self.ZOS_gas_path_system_obj is not None:
            self.ZOS_gas_path_system_obj.is_stop=True
        if self.UFC_gas_path_system_obj is not None and self.UFC_gas_path_system_obj.ufc_gas_path_system_start_thread is not None:
            self.UFC_gas_path_system_obj.ufc_gas_path_system_start_thread.is_stop=True
        global auto_wait_event,stop_flag
        stop_flag = True
        if self.Startup_o2_span_air_calibration_obj is not None:
            self.Startup_o2_span_air_calibration_obj.request_stop()
            if self.Startup_o2_span_air_calibration_obj.is_running:
                self.Startup_o2_span_air_calibration_obj.wait_until_finished(30)
        auto_wait_event.set()
        auto_wait_event.clear()

        self.close_timers()
        p = AsyPromise(self.UGC_gas_path_system_obj.stop).then(
            lambda v: AsyPromise(
                self.UFC_gas_path_system_obj.stop,
            ).then(
                lambda v2: AsyPromise(self.ZOS_gas_path_system_obj.stop).then(
                    lambda v22: self._stop_active_calibrations_then_finish()
                        ).catch(lambda e: logger.error(f"{e}"))
                    ).catch(lambda e: logger.error(f"{e}"))
                ).catch( lambda e: logger.error(f"{e}"))

        return p

    def _stop_active_calibrations_then_finish(self):
        """Only stop calibration workers that are actually running."""
        zero_running = (
            self.Zero_carlibration_obj is not None
            and self.Zero_carlibration_obj.isRunning()
        )
        range_running = (
            self.Range_carlibration_obj is not None
            and self.Range_carlibration_obj.isRunning()
        )

        if zero_running:
            return AsyPromise(self.Zero_carlibration_obj.stop_calibrate).then(
                lambda _: (
                    AsyPromise(self.Range_carlibration_obj.stop_calibrate).then(
                        lambda __: AsyPromise(self.finished_stop)
                    )
                    if range_running
                    else AsyPromise(self.finished_stop)
                )
            )
        if range_running:
            return AsyPromise(self.Range_carlibration_obj.stop_calibrate).then(
                lambda _: AsyPromise(self.finished_stop)
            )
        return AsyPromise(self.finished_stop)

    def finished_stop(self,resolve,reject):
        """
        完成停止
        :return:
        """
        if self.UFC_gas_path_system_obj is not None and self.UFC_gas_path_system_obj.ufc_gas_path_system_close_thread is not None:
            self.UFC_gas_path_system_obj.ufc_gas_path_system_close_thread.stop()
            self.UFC_gas_path_system_obj.ufc_gas_path_system_close_thread.deleteLater()
            self.UFC_gas_path_system_obj.ufc_gas_path_system_close_thread=None
        # 将信号解绑
        if self.update_status_main_signal_gui_update is not None:
            self.update_status_main_signal_gui_update.disconnect(self.logger_info)
            self.update_status_main_signal_gui_update = None
        if self.update_start_state_signal is not None:
            self.update_start_state_signal.disconnect(self.update_start_state)
            self.update_start_state_signal = None

        if self.UFC_gas_path_system_obj is not None:
            self.UFC_gas_path_system_obj=None
        if self.UGC_gas_path_system_obj is not None:
            self.UGC_gas_path_system_obj=None
        if self.ZOS_gas_path_system_obj is not None:
            self.ZOS_gas_path_system_obj=None

        #返回响应
        logger.error(f"停止气路完成")
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(
                ObjectQueueItem(origin="UFC_UGC_ZOS_index", to="MainWindow_index", title="stop_gap_system_return",
                                data="停止气路完成",
                                time=time_util.get_format_from_time(time.time())))

        resolve()

    def pause_running_gap_system(self):
        #暂停正在运行的气路模块
        if self.UFC_gas_path_system_obj is not None and self.UFC_gas_path_system_obj.ufc_gas_path_system_run_thread is not None and not self.UFC_gas_path_system_obj.ufc_gas_path_system_run_thread.isPaused():
            self.UFC_gas_path_system_obj.ufc_gas_path_system_run_thread.pause()
        if self.UGC_gas_path_system_obj is not None and self.UGC_gas_path_system_obj.ugc_gas_path_system_run_thread is not None and not self.UGC_gas_path_system_obj.ugc_gas_path_system_run_thread.isPaused():
            self.UGC_gas_path_system_obj.ugc_gas_path_system_run_thread.pause()
        if self.ZOS_gas_path_system_obj is not None and self.ZOS_gas_path_system_obj.zos_gas_path_system_run_thread is not None and not self.ZOS_gas_path_system_obj.zos_gas_path_system_run_thread.isPaused():
            self.ZOS_gas_path_system_obj.zos_gas_path_system_run_thread.pause()
    def resume_running_gap_system(self):
        #解除暂停正在运行的气路模块
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

        p = AsyPromise(
            self.span_carlibration_obj_calibrate_wrapped
        ).then().catch( lambda e: logger.error(f"{e}"))
        return p
    def zero_calibration_handle(self):
        """
        零点标定
        :return:
        """

        p = AsyPromise(
            self.zero_carlibration_obj_calibrate_wrapped
        ).then().catch( lambda e: logger.error(f"{e}"))
        return p
    def calibration_btn_start(self):
        """
        按钮点击的标定事件
        :return:
        """
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
        p = AsyPromise(lambda r,e:r()).then().catch( lambda e: logger.error(f"{e}"))
        return p
    def gas_state_check(self):
        p = AsyPromise(self.UFC_gas_state_check_obj.state_check).then(
        ).catch( lambda e: logger.error(f"{e}"))
        return p
        # p = AsyPromise(lambda r, e: r()).then().catch( lambda e: logger.error(f"{e}"))
        # return p



    def dosomething(self):
        if self._startup_chain_started:
            time.sleep(0.05)
            return
        self._startup_chain_started = True
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
                        self.stop()
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
                        self.stop()
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
                        self.stop()
                    ).catch(lambda e: logger.error(f"{e}"))
                ).catch(lambda e: logger.error(f"{e}"))
            ).catch(lambda e: logger.error(f"{e}"))
        else:
            self.start_btn_handle().then(
                lambda _: self.run_btn_handle().then(
                    lambda _: self.gas_state_check_handle().then(
                        self.stop()
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
        if self.zos_start_timer is not None :
            self.zos_start_timer.stop()


        if self.gas_state_check_timer is not None  :
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
