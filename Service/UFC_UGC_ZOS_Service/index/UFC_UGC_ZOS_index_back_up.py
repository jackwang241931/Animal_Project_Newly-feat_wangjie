
import math
import multiprocessing
import os
import pdb
import threading
import time
from typing import Any

from loguru import logger









from PyQt6.QtCore import QRect, Qt, pyqtSignal, QObject, pyqtBoundSignal, QMetaObject, Q_ARG

from Service.UFC_UGC_ZOS_Service.function.gas_calibration.Gas_Carlibration import Zero_Carlibration, Range_Carlibration
from Service.UFC_UGC_ZOS_Service.function.gas_path_system.Gas_path_system import ZOS_gas_path_system, \
    UGC_gas_path_system, UFC_gas_path_system
from Service.UFC_UGC_ZOS_Service.function.gas_state_check.Gas_State_Check import UFC_Gas_State_Check
from public.config_class.global_setting import global_setting
from public.config_class.ini_parser import ini_parser
from public.entity.MyQThread import MyQThread, MyThread
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.function.Timer.ProcederTimer import PeriodicTimer
from public.function.promise.AsyPromise import AsyPromise
from theme.ThemeQt6 import ThemedWindow

# 过滤日志

#logger = logger.bind(category="deep_camera_logger")
read_queue_data_Thread_Lock = threading.Lock()
auto_wait_event = threading.Event()


class auto_run_Thread(MyQThread):
    #開始回調信號
    start_finish_signal: pyqtBoundSignal=pyqtSignal()
    #運行回調信號
    run_finish_signal: pyqtBoundSignal = pyqtSignal()
    #標定回調信號
    carlibration_finish_signal: pyqtBoundSignal = pyqtSignal()
    #狀態檢測回調信號
    check_finish_signal: pyqtBoundSignal = pyqtSignal()
    def __init__(self,name,start_signal,run_signal,carlibration_signal,gas_state_check_signal,auto_finish_signal):
        super().__init__(name=name)
        # 開始信號
        self.start_signal =start_signal
        # 運行信號
        self.run_signal = run_signal
        # 标定信號
        self.carlibration_signal = carlibration_signal

        # 状态检测信號
        self.gas_state_check_signal =gas_state_check_signal

        #自動運行結束信號
        self.auto_finish_signal = auto_finish_signal

        self.before_start_flag =True
        self.start_finish_flag =False
        self.run_finish_flag =False
        self.carlibration_finish_flag =False
        self.check_finish_flag =False

        self.start_finish_signal.connect(self.start_finish)
        self.run_finish_signal.connect(self.run_finish)
        self.carlibration_finish_signal.connect(self.carlibration_finish)
        self.check_finish_signal.connect(self.check_finish)

    def start_finish(self):
        self.start_finish_flag=True
        pass
    def run_finish(self):
        self.run_finish_flag=True
    def carlibration_finish(self):
        self.carlibration_finish_flag=True
    def check_finish(self):
        self.check_finish_flag=True

    def run(self):
        logger.warning(f"{self.name} thread {threading.get_ident()} has been started！")
        self._running = True
        self.before_Runing_work()
        while self._running:
            self.mutex.lock()
            if self._paused:
                self.condition.wait(self.mutex)  # 等待条件变量
            self.mutex.unlock()

            # 执行一些工作（替代为你需要的任务）
            self.dosomething()
    def dosomething(self):
        if self.before_start_flag:
            self.start_signal.emit()
            self.before_start_flag=False



        if self.start_finish_flag:
            self.start_finish_flag = False


            global auto_wait_event
            auto_wait_event.wait()

            self.run_signal.emit()

        if self.run_finish_flag:

            self.run_finish_flag = False
            self.carlibration_signal.emit()

        if self.carlibration_finish_flag:
            self.carlibration_finish_flag = False
            self.gas_state_check_signal.emit()

        if self.check_finish_flag:
            self.check_finish_flag = False
            self.auto_finish_signal.emit()

            self.stop()

        pass
class read_queue_data_Thread(MyQThread):
    def __init__(self, name):
        super().__init__(name)
        self.queue = None
        self.update_status_main_signal_gui_update: pyqtSignal(str) = None
        pass

    def dosomething(self):
        if not self.queue.empty():
            try:
                message: ObjectQueueItem = self.queue.get()
            except Exception as e:
                logger.error(f"{self.name}发生错误{e}")
                return
                # message 结构{'to'发往哪个线程，'data'数据，‘from'从哪来}

            if message is not None and isinstance(message, ObjectQueueItem) and message.to=='UFC_UGC_ZOS_index':
                logger.error(f"{self.name}_get_message:{message}")
                match message.title:
                    case '':
                        if self.update_status_main_signal_gui_update is not None:
                            with read_queue_data_Thread_Lock:
                                self.update_status_main_signal_gui_update.emit(message.data)
                            pass
                    case _:
                        pass

            else:
                # 把消息放回去
                self.queue.put(message)

        pass


read_queue_data_thread = read_queue_data_Thread(name="UFC_UGC_ZOS_index_read_queue_data_thread")

class Monitor_start_state_Thread(MyQThread):
    def __init__(self, name,UFC_gas_path_system_obj=None,UGC_gas_path_system_obj=None,ZOS_gas_path_system_obj=None,update_start_state_signal=None):
        # UFC气路系统
        self.UFC_gas_path_system_obj: UFC_gas_path_system = UFC_gas_path_system_obj
        # UGC气路系统
        self.UGC_gas_path_system_obj: UGC_gas_path_system = UGC_gas_path_system_obj
        # ZOS气路系统
        self.ZOS_gas_path_system_obj: ZOS_gas_path_system = ZOS_gas_path_system_obj
        self.update_start_state_signal:pyqtSignal = update_start_state_signal

        super().__init__(name)
    def dosomething(self):
        print(self.UFC_gas_path_system_obj.ufc_start_time_state,self.ZOS_gas_path_system_obj.zos_start_status)
        if self.UFC_gas_path_system_obj.ufc_start_time_state and self.ZOS_gas_path_system_obj.zos_start_status:
            logger.error(f"{'True'*1000}")
            self.update_start_state_signal.emit()
        time.sleep(1)

        pass


class UFC_UGC_ZOS_index(MyQThread):
    update_status_main_signal_gui_update = pyqtSignal(str)
    #更新开始状态的信号
    update_start_state_signal=pyqtSignal()

    # 開始信號
    start_signal: pyqtBoundSignal =pyqtSignal()
    #運行信號
    run_signal : pyqtBoundSignal=pyqtSignal()
    # 标定信號
    carlibration_signal: pyqtBoundSignal =pyqtSignal()
    #自動運行結束信號
    auto_finish_signal: pyqtBoundSignal =pyqtSignal()

    # 状态检测信號
    gas_state_check_signal: pyqtBoundSignal =pyqtSignal()

    def __init__(self, parent=None, geometry: QRect = None, title=""):
        super().__init__(name="UFC_UGC_ZOS_index")
        # 暂停
        self.ispause = False

        # 发送的数据结构
        self.send_message = {
            'port': '',
            'data': '',
            'slave_id': 0,
            'function_code': 0,
            'timeout': 0
        }



        #UFC气路系统
        self.UFC_gas_path_system_obj:UFC_gas_path_system =None
        #UGC气路系统
        self.UGC_gas_path_system_obj:UGC_gas_path_system =None
        #ZOS气路系统
        self.ZOS_gas_path_system_obj:ZOS_gas_path_system =None
        #零点标定
        self.Zero_carlibration_obj:Zero_Carlibration =None
        #量程标定
        self.Range_carlibration_obj:Range_Carlibration =None
        #UFC状态检测
        self.UFC_gas_state_check_obj:UFC_Gas_State_Check=None

        #ZOS预热定时器
        self.zos_start_timer :PeriodicTimer=None
        self.ufc_start_timer :PeriodicTimer=None
        self.calibration_start_timer: PeriodicTimer = None
        self.gas_state_check_timer: PeriodicTimer = None
        #监测开始状态的线程
        self.monitor_start_state_Thread:MyQThread = None
        #自動運行按鈕綫程
        self.auto_run_thread:auto_run_Thread=None

        # 获得相关数据
        self._init_data()

        # 实例化功能
        self._init_function()

        pass


    # 获得相关数据
    def _init_data(self):



        #读取config ini文件
        # 加载配置 如果ini文件在最外层要去除module+
        config_file_path =os.getcwd() + "./"+"config/UFC_UGC_ZOS_Test.ini"
        # 串口配置数据{"section":{"key1":value1,"key2":value2,....}，...}
        configer = ini_parser(config_file_path).read()
        if (len(configer) != 0):
            logger.info("UFC_UGC_ZOS_config配置文件读取成功。")
        else:
            logger.error("UFC_UGC_ZOS_config配置文件读取失败。")
        global_setting.set_setting("UFC_UGC_ZOS_config", configer)
        # q = multiprocessing.Queue()  # 创建 Queue 消息传递
        # send_message_q = multiprocessing.Queue()  # 发送查询报文的消息传递单独一个通道
        # global_setting.set_setting("queue", q)
        # global_setting.set_setting("send_message_queue", send_message_q)
        # 串口的线程锁 确保同时只能一个线程访问资源
        serial_lock = threading.Lock()
        global_setting.set_setting("serial_lock", serial_lock)

        pass




    def logger_info(self,text):
        logger.debug(text)
        if "\n" not in text:
            logger.info(text)
    # 实例化功能
    def _init_function(self):
        # 将更新status信号绑定更新status界面函数
        self.update_status_main_signal_gui_update.connect(self.logger_info)
        self.update_start_state_signal.connect(self.update_start_state)

        self.start_signal.connect(self.start_btn_handle)
        self.run_signal.connect(self.run_btn_handle)
        self.carlibration_signal.connect(self.calibration_handle)
        self.gas_state_check_signal.connect(self.gas_state_check_handle)
        self.auto_finish_signal.connect(self.auto_finish_handle)

        #实例化气路
        self.UFC_gas_path_system_obj:UFC_gas_path_system = UFC_gas_path_system()
        self.UGC_gas_path_system_obj:UGC_gas_path_system = UGC_gas_path_system()
        self.ZOS_gas_path_system_obj:ZOS_gas_path_system = ZOS_gas_path_system()
        self.UFC_gas_path_system_obj.update_status_main_signal_gui_update=self.update_status_main_signal_gui_update
        self.UGC_gas_path_system_obj.update_status_main_signal_gui_update=self.update_status_main_signal_gui_update
        self.ZOS_gas_path_system_obj.update_status_main_signal_gui_update=self.update_status_main_signal_gui_update

        # 实例化标定
        self.Zero_carlibration_obj:Zero_Carlibration = Zero_Carlibration()
        self.Zero_carlibration_obj.update_status_main_signal_gui_update=self.update_status_main_signal_gui_update
        self.Range_carlibration_obj:Range_Carlibration = Range_Carlibration()
        self.Range_carlibration_obj.update_status_main_signal_gui_update=self.update_status_main_signal_gui_update

        #实例化状态检测
        self.UFC_gas_state_check_obj=UFC_Gas_State_Check()
        self.UFC_gas_state_check_obj.update_status_main_signal_gui_update=self.update_status_main_signal_gui_update

        # 实例化信号

        global read_queue_data_thread
        read_queue_data_thread.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update
        read_queue_data_thread.queue = global_setting.get_setting("send_message_queue")
        if read_queue_data_thread is not None and not read_queue_data_thread.isRunning():
            read_queue_data_thread.start()
            pass
        pass



    def auto_finish_handle(self):
        pass
    def update_start_state(self):
        self.monitor_start_state_Thread.stop()
        self.close_timers()

        #預熱完之後取消自動運行的阻塞
        global auto_wait_event
        auto_wait_event.set()
        auto_wait_event.clear()
    #启动按钮事件 启动气路
    def start_btn_handle(self):
        p= AsyPromise(self.ZOS_gas_path_system_obj.start).then(
            lambda _:AsyPromise(
                self.UFC_gas_path_system_obj.start,
            ).then(
                lambda _:AsyPromise(self.UGC_gas_path_system_obj.start,).then(
                    lambda _:AsyPromise(self.set_start_timers)
                ).catch(lambda e:logger.error(f"{e}"))
            ).catch( lambda e: logger.error(f"{e}"))
        ).catch( lambda e: logger.error(f"{e}"))

        #等开始状态都结束
        if self.monitor_start_state_Thread is  None:
            self.monitor_start_state_Thread = Monitor_start_state_Thread(name="Monitor_start_state_Thread",UFC_gas_path_system_obj=self.UFC_gas_path_system_obj,UGC_gas_path_system_obj=self.UGC_gas_path_system_obj,ZOS_gas_path_system_obj=self.ZOS_gas_path_system_obj,update_start_state_signal=self.update_start_state_signal)
        self.monitor_start_state_Thread.start()

        # #開始結束回調
        # if self.auto_run_thread is not None and self.auto_run_thread.isRunning():
        #     try:
        #         # 方法1：使用 QTimer 延迟发射
        #         auto_wait_event.set()
        #         auto_wait_event.clear()
        #         self.auto_run_thread.start_finish_signal.emit()
        #     except Exception as e:
        #         logger.error(f"{e}")


        return p

        pass

    #运行按钮事件 运行气路
    def run_btn_handle(self):
        logger.error("12333333333333333333333333333333333333333333333333333333333333333333333333")
        global auto_wait_event
        auto_wait_event.wait()
        logger.error("444444444444444444424444444444444444444444444444444444444444444444444444")
        p=AsyPromise(self.UFC_gas_path_system_obj.run).then(
            lambda v: AsyPromise(
                self.UGC_gas_path_system_obj.run,
            ).then(
                lambda v2: AsyPromise(self.ZOS_gas_path_system_obj.run)
            ).catch( lambda e: logger.error(f"{e}"))
        ).catch( lambda e: logger.error(f"{e}"))

        # 運行結束回調
        if self.auto_run_thread is not None and self.auto_run_thread.isRunning():
            self.auto_run_thread.run_finish_signal.emit()
        return p
        pass
    #停止按钮事件 停止气路
    def stop_btn_handle(self):

        if self.monitor_start_state_Thread is not None:
            self.monitor_start_state_Thread.stop()
        if self.UFC_gas_path_system_obj is not None and self.UFC_gas_path_system_obj.ufc_gas_path_system_run_thread is not None:
            self.UFC_gas_path_system_obj.ufc_gas_path_system_run_thread.stop()
        if self.UGC_gas_path_system_obj is not None and self.UGC_gas_path_system_obj.ugc_gas_path_system_run_thread is not None:
            self.UGC_gas_path_system_obj.ugc_gas_path_system_run_thread.stop()
        if self.ZOS_gas_path_system_obj is not None and self.ZOS_gas_path_system_obj.zos_gas_path_system_run_thread is not None:
            self.ZOS_gas_path_system_obj.zos_gas_path_system_run_thread.stop()
        self.close_timers()
        p=AsyPromise(self.UGC_gas_path_system_obj.stop).then(
            lambda v: AsyPromise(
                self.UFC_gas_path_system_obj.stop,
            ).then(
                lambda v2: AsyPromise(self.ZOS_gas_path_system_obj.stop)
            ).catch( lambda e: logger.error(f"{e}"))
        ).catch( lambda e: logger.error(f"{e}"))

        return p
        pass

    def calibration_handle(self):
        # 标定主函数 启动timer
        self.set_calibration_start_timer()
        # 標定結束回調
        if self.auto_run_thread is not None and self.auto_run_thread.isRunning():
            self.auto_run_thread.carlibration_finish_signal.emit()
        pass
    #标定
    def carlibation(self):
        p=AsyPromise(self.Zero_carlibration_obj.calibrate).then(
            lambda v: AsyPromise(
                self.Range_carlibration_obj.calibrate
            ).then().catch( lambda e: logger.error(f"{e}"))
        ).catch( lambda e: logger.error(f"{e}"))

        return p
        pass
    # 状态检测timer
    def gas_state_check_handle(self):
        self.set_gas_state_check_timer()
        # 狀態結束回調
        if self.auto_run_thread is not None and self.auto_run_thread.isRunning():
            self.auto_run_thread.check_finish_signal.emit()
        pass
    #状态检测
    def gas_state_check(self):
        p= AsyPromise(self.UFC_gas_state_check_obj.state_check).then(
        ).catch( lambda e: logger.error(f"{e}"))

        return p
        pass

    #自动执行按钮事件
    def auto_btn_handle(self):

        if self.auto_run_thread is None:
            self.auto_run_thread = auto_run_Thread(name="auto_run_thread",
                                                   start_signal=self.start_signal,
                                                   run_signal=self.run_signal,
                                                   carlibration_signal=self.carlibration_signal,
                                                   gas_state_check_signal=self.gas_state_check_signal,
                                                   auto_finish_signal=self.auto_finish_signal,
                                                   )
        self.auto_run_thread.start()
        pass
    def dosomething(self):
        self.start_btn_handle().then(
            self.run_btn_handle().then(
                self.carlibation().then(
                    self.gas_state_check().then(
                        self.stop()
                    )
                )
            )
        )
        pass
    #解除自动执行按钮事件
    def disabled_auto_btn_handle(self):
        if self.auto_run_thread is not None:
            self.auto_run_thread.stop()

        self.stop_btn_handle().then(

        )
        pass
    def pause(self):
        if self.UFC_gas_path_system_obj is not None and self.UFC_gas_path_system_obj.ufc_gas_path_system_run_thread is not None:
            self.UFC_gas_path_system_obj.ufc_gas_path_system_run_thread.pause()
        if self.UGC_gas_path_system_obj is not None and self.UGC_gas_path_system_obj.ugc_gas_path_system_run_thread is not None:
            self.UGC_gas_path_system_obj.ugc_gas_path_system_run_thread.pause()
        if self.ZOS_gas_path_system_obj is not None and self.ZOS_gas_path_system_obj.zos_gas_path_system_run_thread is not None:
            self.ZOS_gas_path_system_obj.zos_gas_path_system_run_thread.pause()
        if self.auto_run_thread is not None:
            self.auto_run_thread.pause()
        if self.monitor_start_state_Thread is not None:
            self.monitor_start_state_Thread.pause()
        self.pause_timers()
        self.ispause=True
    def resume(self):
        if self.UFC_gas_path_system_obj is not None and self.UFC_gas_path_system_obj.ufc_gas_path_system_run_thread is not None:
            self.UFC_gas_path_system_obj.ufc_gas_path_system_run_thread.resume()
        if self.UGC_gas_path_system_obj is not None and self.UGC_gas_path_system_obj.ugc_gas_path_system_run_thread is not None:
            self.UGC_gas_path_system_obj.ugc_gas_path_system_run_thread.resume()
        if self.ZOS_gas_path_system_obj is not None and self.ZOS_gas_path_system_obj.zos_gas_path_system_run_thread is not None:
            self.ZOS_gas_path_system_obj.zos_gas_path_system_run_thread.resume()
        if self.auto_run_thread is not None:
            self.auto_run_thread.resume()
        if self.monitor_start_state_Thread is not None:
            self.monitor_start_state_Thread.resume()
        self.resume_timers()
        self.ispause=False
    #设置启动阶段的定时器
    def set_start_timers(self,resolve, reject):
        logger.error("ttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttttt")
        self.set_zos_start_timer()
        self.set_ufc_start_timer()
        resolve()
    def pause_timers(self):
        if self.zos_start_timer is not None and (self.zos_start_timer.is_active()):
            self.zos_start_timer.pause()
        if self.ufc_start_timer is not None and (self.ufc_start_timer.is_active()):
            self.ufc_start_timer.pause()
        if self.calibration_start_timer is not None and (
                self.calibration_start_timer.is_active() ):
            self.calibration_start_timer.pause()
        if self.gas_state_check_timer is not None and (
                self.gas_state_check_timer.is_active() ):
            self.gas_state_check_timer.pause()
        pass
    def resume_timers(self):
        if self.zos_start_timer is not None:
            self.zos_start_timer.resume()
        if self.ufc_start_timer is not None :
            self.ufc_start_timer.resume()
        if self.calibration_start_timer is not None:
            self.calibration_start_timer.resume()
        if self.gas_state_check_timer is not None:
            self.gas_state_check_timer.resume()
        pass
    def close_timers(self):
        # 关闭timers
        # 关闭窗口时确保停止定时器
        if self.zos_start_timer is not None and (self.zos_start_timer.is_active() or self.zos_start_timer._is_paused):
            self.zos_start_timer.stop()
        if self.ufc_start_timer is not None and (self.ufc_start_timer.is_active() or self.ufc_start_timer._is_paused):
            self.ufc_start_timer.stop()
        if self.calibration_start_timer is not None and (
                self.calibration_start_timer.is_active() or self.calibration_start_timer._is_paused):
            self.calibration_start_timer.stop()
        if self.gas_state_check_timer is not None and (
                self.gas_state_check_timer.is_active() or self.gas_state_check_timer._is_paused):
            self.gas_state_check_timer.stop()
        pass

    # 设置状态检测计时器
    def set_gas_state_check_timer(self):
        # logger.error(f"1:{float(global_setting.get_setting('UFC_UGC_ZOS_config')['Gas_State_Check']['start_time_delay']) * 1000}")
        self.gas_state_check_timer = PeriodicTimer(
            interval_ms=float(
                global_setting.get_setting('UFC_UGC_ZOS_config')['Gas_State_Check']['start_time_delay']) * 1000,
            max_duration_ms=None,
            task=None,  # 先不传，后面用 set_task 注入
            run_in_thread=True,  # 若你的任务耗时，设为 True

            run_immediately=True
        )
        self.gas_state_check_timer.set_task(self.gas_state_check)
        self.gas_state_check_timer.start()
        pass
    # 设置标定计时器
    def set_calibration_start_timer(self):
        self.calibration_start_timer = PeriodicTimer(
            interval_ms=float(
                global_setting.get_setting('UFC_UGC_ZOS_config')['Calibration']['start_time_delay']) * 1000,
            max_duration_ms=None,
            task=None,  # 先不传，后面用 set_task 注入
            run_in_thread=True,  # 若你的任务耗时，设为 True

            run_immediately=True
        )
        self.calibration_start_timer.set_task(self.carlibation)
        self.calibration_start_timer.start()
    # 设置zos预热定时器
    def set_zos_start_timer(self):
            # 构造 PeriodicTimer（2秒间隔，20分钟上限）

            self.zos_start_timer = PeriodicTimer(
                interval_ms=float(global_setting.get_setting('UFC_UGC_ZOS_config')['ZOS']['start_time_delay']) * 1000,
                max_duration_ms=float(global_setting.get_setting('UFC_UGC_ZOS_config')['ZOS']['start_time']) * 1000,
                task=None,  # 先不传，后面用 set_task 注入
                run_in_thread=True,  # 若你的任务耗时，设为 True

                run_immediately=True
            )
            self.zos_start_timer.set_task(self.ZOS_gas_path_system_obj.zos_start_timer_task)
            self.zos_start_timer.start()
    #设置UFC 等待流量控制器自动配置及运行 定时器
    def  set_ufc_start_timer(self):
        try:
            self.ufc_start_timer = PeriodicTimer(
                interval_ms=float(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['start_wait_time_delay']) * 1000,
                max_duration_ms=float(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['start_wait_time']) * 1000,
                task=None,  # 先不传，后面用 set_task 注入
                run_in_thread=True,  # 若你的任务耗时，设为 True
                task_done_callback=self.UFC_gas_path_system_obj.check_ufc_start_time_state,
                run_immediately=True
            )
        except Exception as e:
            logger.error(f"{e}")
        self.ufc_start_timer.set_task(self.UFC_gas_path_system_obj.ufc_start_timer_task)
        # self.ufc_start_timer.finished.connect(self.UFC_gas_path_system_obj.check_ufc_start_time_state)
        self.ufc_start_timer.start()








