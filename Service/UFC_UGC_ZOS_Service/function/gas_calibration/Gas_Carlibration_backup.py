import abc
import copy
import queue
import threading
import time
from datetime import datetime
from enum import Enum

from blinker.base import _PNamespaceSignal
from loguru import logger

from Service.UFC_UGC_ZOS_Service.function.Send_Message.Send_Message import Send_Message


from public.config_class.global_setting import global_setting
from public.entity.MyQThread import MyQThread
from public.entity.enum.Public_Enum import GapSystem_Running_Type
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.function.Modbus.Modbus_Type import Others_Tables
from public.function.Monitor_data_storage.DataStorage import store_data_with_result

from public.function.promise.AsyPromise import AsyPromise
from public.util.number_util import number_util
from public.util.time_util import time_util
# 前面测量的氧气值
last_oxygen_value = 0
# 前面测量的ZOS气压值
last_pressure_value = 0
# 前面测量的二氧化碳值
last_carbon_value = 0
#logger = logger.bind(category="deep_camera_logger")
# 气路校准类型
class Gas_Carlibration_Type(Enum):
    # 校0
    ZERO = 0
    #校span
    SPAN = 1
    def __lt__(self, other):
        if other is None:
            return False
        return self.value < other.value

    def __le__(self, other):
        if other is None:
            return False
        return self.value <= other.value

    def __gt__(self, other):
        if other is None:
            return False
        return self.value > other.value

    def __ge__(self, other):
        if other is None:
            return False
        return self.value >= other.value
    def __eq__(self, other):
        if other is None:
            return False
        return self.value == other.value
    def __ne__(self, other):
        if other is None:
            return False
        return self.value != other.value
class Gas_Carlibration:
    """
    气路标定 零点标定和量程标定的父类
    """

    def __init__(self,title=GapSystem_Running_Type.DEFAULT,type =Gas_Carlibration_Type.ZERO):
        # 更新主线程状态栏消息信号
        self.update_status_main_signal_gui_update: _PNamespaceSignal = None
        #校0还是校span
        self.type =type
        match self.type:
            case Gas_Carlibration_Type.ZERO:
                self.name="零点标定"
            case Gas_Carlibration_Type.SPAN:
                self.name="SPan量程标定"
            case _:
                self.name="默认标定"
        # 是否  停止标定
        self.is_STOP = False
        # 发送的数据结构
        self.send_message = {
            'port': '',
            'data': '',
            'slave_id': 0,
            'function_code': 0,
            'timeout': 0
        }
        # 发送报文线程
        self.send_thread: Send_Message = Send_Message(update_status_main_signal_gui_update=self.update_status_main_signal_gui_update,send_message=self.send_message,update_status_main_signal_gui_update_type=title)
    def update(self):
        self.send_thread.update_status_main_signal_gui_update=self.update_status_main_signal_gui_update
        self.is_STOP = self.is_STOP
    @abc.abstractmethod
    def calibrate(self,resolve,reject):
        """
        标定
        :return:
        """
        pass
    """start   start"""
    def start_calibration_common(self,resolve,reject,port,next_function):
        """
        开始标定时 共有的报文
        :param resolve:
        :param reject:
        :param port:
        :param next_function:
        :return:
        """
        AsyPromise(self.close_ufc_current_cage_valve, port=port).then(
            lambda r: AsyPromise(next_function, port=port).then(lambda r1: resolve()).catch(lambda e: logger.error(e))
        ).catch(lambda e: logger.error(e))
    # 0.关闭UFC当前鼠笼阀门
    def close_ufc_current_cage_valve(self,resolve,reject,port):
        mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
        # 一开始index是None则是从参考气开始
        mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
        if mouse_cages_inc is not None and len(mouse_cages_inc) > 0:
            if mouse_cage_index is not None:
                mouse_cage_number_addr_single = mouse_cages_inc[mouse_cage_index] - 1
            else:
                # 下标为None 则为参考气
                mouse_cage_number_addr_single = 8
            if self.is_STOP:
                reject("stop")
            self.send_message = {
                'port': port,
                'data': number_util.set_int_to_4_bytes_list(f"000{mouse_cage_number_addr_single}0000"),
                'slave_id': '2',
                'function_code': '5',
                'timeout': 1
            }
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | {self.name}标定：0.关闭ufc当前{mouse_cage_number_addr_single if mouse_cage_number_addr_single!=8 else '参考气路'}号鼠笼阀门")
            self.send_thread.send_message = self.send_message
            AsyPromise(self.send_thread.Send).then(
                #1. 打开ugc调零或者SPan阀门
               lambda r:AsyPromise(self.open_ugc_zero_or_span_valve,port=port).then(lambda r1:resolve()).catch(lambda e: logger.error(e))
            ).catch(lambda e:logger.error(e))
        reject("关闭UFC当前鼠笼阀门获取鼠笼数据失败")
    def open_ugc_zero_or_span_valve(self,resolve,reject,port):
        # 1. 打开ugc调零或者SPan阀门
        if self.is_STOP:
            reject("stop")
        match self.type:
            case Gas_Carlibration_Type.ZERO:
                self.send_message = {
                    'port': port,
                    'data': number_util.set_int_to_4_bytes_list(f"0001FF00"),
                    'slave_id': '3',
                    'function_code': '5',
                    'timeout': 1
                }
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} | {self.name}标定： 1. 打开ugc调零阀门")
            case Gas_Carlibration_Type.SPAN:
                self.send_message = {
                    'port': port,
                    'data': number_util.set_int_to_4_bytes_list(f"0002FF00"),
                    'slave_id': '3',
                    'function_code': '5',
                    'timeout': 1
                }
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} | {self.name}标定： 1. 打开ugc SPan阀门")
            case _:
                reject("默认标定")
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            # 2. 关闭ugc采样阀
            lambda r: AsyPromise(self.close_ugc_sample_valve, port=port).then(lambda r1: resolve()).catch(
                lambda e: logger.error(e))
        ).catch(lambda e: logger.error(e))
    def close_ugc_sample_valve(self,resolve,reject,port):
        # 2. 关闭ugc采样阀
        if self.is_STOP:
            reject("stop")
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"0"),
            'slave_id': '3',
            'function_code': '5',
            'timeout': 1
        }
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | {self.name}标定：2. 关闭ugc采样阀")
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            # 3. 关闭ugc 校零阀门或校span阀门
            lambda r: AsyPromise(self.close_ugc_zero_or_span_valve, port=port).then(lambda r1: resolve()).catch(
                lambda e: logger.error(e))
        ).catch(lambda e: logger.error(e))
    def close_ugc_zero_or_span_valve(self,resolve,reject,port):
        # 3. 关闭ugc 校0阀门或校span阀门
        if self.is_STOP:
            reject("stop")
        match self.type:
            case Gas_Carlibration_Type.ZERO:
                self.send_message = {
                    'port': port,
                    'data': number_util.set_int_to_4_bytes_list(f"00020000"),
                    'slave_id': '3',
                    'function_code': '5',
                    'timeout': 1
                }
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} | {self.name}标定： 3. 关闭ugc 校span阀门")
            case Gas_Carlibration_Type.SPAN:
                self.send_message = {
                    'port': port,
                    'data': number_util.set_int_to_4_bytes_list(f"00010000"),
                    'slave_id': '3',
                    'function_code': '5',
                    'timeout': 1
                }
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} | {self.name}标定： 3. 关闭ugc 校0阀门")
            case _:
                reject("默认标定")
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            # 4. 启动zos 校0通道或校span通道
            lambda r: AsyPromise(self.open_zos_zero_or_span_valve, port=port).then(lambda r1: resolve()).catch(
                lambda e: logger.error(e))
        ).catch(lambda e: logger.error(e))
    def open_zos_zero_or_span_valve(self,resolve,reject,port):
        # 4. 启动zos 校0通道或校span通道
        if self.is_STOP:
            reject("stop")
        match self.type:
            case Gas_Carlibration_Type.ZERO:
                self.send_message = {
                    'port': port,
                    'data': number_util.set_int_to_4_bytes_list(f"0009FF00"),
                    'slave_id': '4',
                    'function_code': '5',
                    'timeout': 1
                }
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} | {self.name}标定： 4. 启动zos 校0通道")
            case Gas_Carlibration_Type.SPAN:
                self.send_message = {
                    'port': port,
                    'data': number_util.set_int_to_4_bytes_list(f"000AFF00"),
                    'slave_id': '4',
                    'function_code': '5',
                    'timeout': 1
                }
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} | {self.name}标定： 4. 启动zos 校span通道")
            case _:
                reject("默认标定")
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            # 5.关闭zos当前运行鼠笼通道
            lambda r: AsyPromise(self.close_zos_current_cage_valve, port=port).then(lambda r1: resolve()).catch(
                lambda e: logger.error(e))
        ).catch(lambda e: logger.error(e))

    # 5.关闭zos当前运行鼠笼通道
    def close_zos_current_cage_valve(self, resolve, reject, port):
        mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
        # 一开始index是None则是从参考气开始
        mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
        if mouse_cages_inc is not None and len(mouse_cages_inc) > 0:
            if mouse_cage_index is not None:
                mouse_cage_number_addr_single = mouse_cages_inc[mouse_cage_index] - 1
            else:
                # 下标为None 则为参考气
                mouse_cage_number_addr_single = 8
            if self.is_STOP:
                reject("stop")
            self.send_message = {
                'port': port,
                'data': number_util.set_int_to_4_bytes_list(f"000{mouse_cage_number_addr_single}0000"),
                'slave_id': '4',
                'function_code': '5',
                'timeout': 1
            }
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | {self.name}标定：5.关闭zos当前{mouse_cage_number_addr_single+1 if mouse_cage_number_addr_single!=8 else '参考气路'}号鼠笼通道")
            self.send_thread.send_message = self.send_message
            AsyPromise(self.send_thread.Send).then(
                lambda r1: resolve()
            ).catch(lambda e: logger.error(e))
        reject("关闭ZOS当前鼠笼通道获取鼠笼数据失败")
    """start   end"""



    """exit  start"""
    def return_to_running_state(self, resolve, reject, port):
        """
        返回系统工作运行状态
        :return:
        """
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | {self.name}标定  开始返回系统工作运行状态",
            title=self.title)
        #1. ugc sample电磁阀打开
        AsyPromise(self.ugc_sample_open,port=port).then(
            lambda r:resolve()
        ).catch(lambda e:logger.error(e))


    def ugc_sample_open(self, resolve, reject, port):
        #1. ugc sample电磁阀打开
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("0000FF00"),
            'slave_id': '3',
            'function_code': '5',
            'timeout': 1
        }
        self.send_thread.send_message = self.send_message
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | {'停止' if self.is_STOP else ''} {self.name}标定 1. ugc sample电磁阀打开",
            title=self.title)
        # 2.校零气路（Zero气）电磁阀关闭
        AsyPromise(self.solenoid_valve_of_zero_gas_close,port=port).then(
            lambda _: resolve()
            ).catch(lambda e: reject(e))
        pass
    def solenoid_valve_of_zero_gas_close(self, resolve, reject, port):
        #2.校零气路（Zero气）电磁阀关闭
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("00010000"),
            'slave_id': '3',
            'function_code': '5',
            'timeout': 1
        }

        self.send_thread.send_message = self.send_message
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | {'停止' if self.is_STOP else ''} {self.name}标定 2.校零气路（Zero气）电磁阀关闭",
            title=self.title)
        AsyPromise(self.send_thread.Send).then(
            # 3.ugc span电磁阀关闭
            lambda r: AsyPromise(self.ugc_span_close, port=port).then(lambda r2: resolve()).catch(
                lambda e: logger.error(f"{e}"))
        ).catch(lambda e: reject(e))
        pass

    def ugc_span_close(self, resolve, reject, port):
        # 3.ugc span电磁阀关闭。
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("00020000"),
            'slave_id': '3',
            'function_code': '5',
            'timeout': 1
        }

        self.send_thread.send_message = self.send_message
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} {'停止' if self.is_STOP else ''} {self.name}标定 3.ugc span电磁阀关闭",
            title=self.title)
        AsyPromise(self.send_thread.Send).then(
            # 4. 关闭zos凋零通道
            lambda r: AsyPromise(self.zos_zero_close, port=port).then(lambda r2: resolve()).catch(
                lambda e: logger.error(f"{e}"))
        ).catch(lambda e: logger.error(f"{e}"))
        pass


    def zos_zero_close(self, resolve, reject, port):
        # 4. 关闭zos凋零通道
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("00090000"),
            'slave_id': '4',
            'function_code': '5',
            'timeout': 1
        }

        self.send_thread.send_message = self.send_message
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} {'停止' if self.is_STOP else ''} {self.name}标定 4. 关闭zos凋零通道",
            title=self.title)
        AsyPromise(self.send_thread.Send).then(
            # 5. 关闭zos凋span通道
            lambda r: AsyPromise(self.zos_span_close, port=port).then(lambda r2: resolve()).catch(
                lambda e: logger.error(f"{e}"))
        ).catch(lambda e: logger.error(f"{e}"))
    def zos_span_close(self, resolve, reject, port):
        # 5. 关闭zos凋span通道
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("000A0000"),
            'slave_id': '4',
            'function_code': '5',
            'timeout': 1
        }

        self.send_thread.send_message = self.send_message
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} {'停止' if self.is_STOP else ''} {self.name}标定 5. 关闭zos凋span通道",
            title=self.title)
        AsyPromise(self.send_thread.Send).then(
            # 6. UFC切换回工作时的鼠笼
            lambda r: AsyPromise(self.switch_return_mouse_cage_UFC, port=port).then(lambda r2: resolve()).catch(
                lambda e: logger.error(f"{e}"))
        ).catch(lambda e: logger.error(f"{e}"))
    def switch_return_mouse_cage_UFC(self, resolve, reject, port):
        # 6. UFC切换回工作时的鼠笼
        mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
        # 一开始index是None则是从参考气开始
        mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
        if mouse_cages_inc is not None and len(mouse_cages_inc) > 0:
            if mouse_cage_index is not None:
                mouse_cage_number_addr_single = mouse_cages_inc[mouse_cage_index] - 1
            else:
                # 下标为None 则为参考气
                mouse_cage_number_addr_single = 8
            self.send_message = {
                'port': port,
                'data': number_util.set_int_to_4_bytes_list(f"000{mouse_cage_number_addr_single}00FF"),
                'slave_id': '2',
                'function_code': '5',
                'timeout': 1
            }

            self.send_thread.send_message = self.send_message
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} {'停止' if self.is_STOP else ''} {self.name}标定 6. UFC切换回工作时的鼠笼{mouse_cage_number_addr_single+1 if mouse_cage_number_addr_single!=8 else '参考气路'}",
                title=self.title)
            AsyPromise(self.send_thread.Send).then(
                # 7. ZOS切换回工作时的鼠笼
                lambda r: AsyPromise(self.switch_return_mouse_cage_ZOS, port=port).then(lambda r2: resolve()).catch(
                    lambda e: logger.error(f"{e}"))
            ).catch(lambda e: logger.error(f"{e}"))
        reject("切换UFC没有获取到鼠笼数据")
    def switch_return_mouse_cage_ZOS(self, resolve, reject, port):
        # 7. ZOS切换回工作时的鼠笼
        mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
        # 一开始index是None则是从参考气开始
        mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
        if mouse_cages_inc is not None and len(mouse_cages_inc) > 0:
            if mouse_cage_index is not None:
                mouse_cage_number_addr_single = mouse_cages_inc[mouse_cage_index] - 1
            else:
                # 下标为None 则为参考气
                mouse_cage_number_addr_single = 8
            self.send_message = {
                'port': port,
                'data': number_util.set_int_to_4_bytes_list(f"000{mouse_cage_number_addr_single}FF00"),
                'slave_id': '4',
                'function_code': '5',
                'timeout': 1
            }

            self.send_thread.send_message = self.send_message
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} {'停止' if self.is_STOP else ''} {self.name}标定 7. ZOS切换回工作时的鼠笼{mouse_cage_number_addr_single+1 if mouse_cage_number_addr_single!=8 else '参考气路'}",
                title=self.title)
            if self.is_STOP:
                AsyPromise(self.send_thread.Send).then(
                    # 停止标定完成。
                    lambda _: AsyPromise(self.stop_finish_calibration).then(
                        lambda r: resolve()
                    ).catch(lambda e: reject(e))

                ).catch(lambda e: reject(e))
            else:
                AsyPromise(self.send_thread.Send).then(
                    # 9 标定完成。
                    lambda _: AsyPromise(self.finish_calibration).then(
                        lambda r: resolve()
                    ).catch(lambda e: reject(e))

                ).catch(lambda e: reject(e))
        reject("切换ZOS没有获取到鼠笼数据")
    def finish_calibration(self, resolve, reject):
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} |  {self.name}标定 8. 标定完成", title=self.title)
        # 标定完成通知
        send_message_queue = global_setting.get_setting("send_message_queue")
        match self.type:
            case Gas_Carlibration_Type.SPAN:
                # 给界面进程发送消息
                send_message_queue.put(ObjectQueueItem(origin='Gas_Carlibration', to='monitor_data_new_index',
                                               title='range_calibration_finish',
                                               data=None,
                                               time=time_util.get_format_from_time(time.time())))
                # 发送完成标定消息
                self.update_status_main_signal_gui_update.send(
                    {'type': 'set_stop_span_calibration_time',
                     'value': f'{time_util.get_format_from_time(time.time())}'}, title=self.title
                )
            case Gas_Carlibration_Type.ZERO:
                send_message_queue.put(ObjectQueueItem(origin='Gas_Carlibration', to='monitor_data_new_index',
                                                       title='zero_calibration_finish',
                                                       data=None,
                                                       time=time_util.get_format_from_time(time.time())))
                # 发送完成标定消息
                self.update_status_main_signal_gui_update.send(
                    {'type': 'set_stop_zero_calibration_time',
                     'value': f'{time_util.get_format_from_time(time.time())}'}, title=self.title
                )
            case _:
                pass
        resolve()

    def stop_finish_calibration(self, resolve, reject):
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} |   {self.name} 停止标定完成", title=self.title)
        # # 停止标定完成通知
        send_message_queue = global_setting.get_setting("send_message_queue")
        match self.type:
            case Gas_Carlibration_Type.SPAN:
                send_message_queue.put(ObjectQueueItem(origin='Gas_Carlibration', to='monitor_data_new_index',
                                                       title='stop_range_calibration_finish',
                                                       data=None,
                                                       time=time_util.get_format_from_time(time.time())))
                # 发送完成标定消息
                self.update_status_main_signal_gui_update.send(
                    {'type': 'set_stop_span_calibration_time',
                     'value': f'{time_util.get_format_from_time(time.time())}'}, title=self.title
                )
            case Gas_Carlibration_Type.ZERO:
                send_message_queue.put(ObjectQueueItem(origin='Gas_Carlibration', to='monitor_data_new_index',
                                                       title='stop_zero_calibration_finish',
                                                       data=None,
                                                       time=time_util.get_format_from_time(time.time())))
                # 发送完成标定消息
                self.update_status_main_signal_gui_update.send(
                    {'type': 'set_stop_zero_calibration_time',
                     'value': f'{time_util.get_format_from_time(time.time())}'}, title=self.title
                )
            case _:
                pass

        resolve()
    """exit  end"""
class Zero_Carlibration(Gas_Carlibration,MyQThread):
    """
    零点标定
    """
    def __init__(self):
        self.title = GapSystem_Running_Type.ZERO_CALIBRATION
        self.port=None
        Gas_Carlibration.__init__(self, title=self.title,type =Gas_Carlibration_Type.ZERO)
        MyQThread.__init__(self,name='Zero_Carlibration_thread')
    def dosomething(self):
        AsyPromise(self.start_calibration_common,port=self.port,next_function =self.cyclic_sampling_of_ugc_carbon_sensor_and_zos_oxygen_sensor ).then(
            lambda r:self.stop()
        ).catch(lambda e:self.stop())
        pass
    def calibrate(self,resolve,reject):
        """零点标定"""
        time.sleep(0.01)
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} |  零点标定 开始{'.' * 100}",title=self.title )
        # 发送开始标定消息
        self.update_status_main_signal_gui_update.send(
            {'type': 'set_start_zero_calibration_time', 'value': f'{time_util.get_format_from_time(time.time())}'},
            title=self.title
        )
        self.is_STOP = False
        # resolve()
        self.port = global_setting.get_setting("port", None)
        if self.port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！",title=self.title )
            reject()
        if self.is_STOP:
            reject()
        self.start()
        resolve()
        pass
    def stop_calibrate(self,resolve,reject):
        """
        取消零点标定
        :param resolve:
        :param reject:
        :return:
        """
        self.is_STOP=True
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} |  停止零点量程标定 开始{'.' * 100}", title=self.title)
        # resolve()
        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！", title=self.title)
            reject()
        AsyPromise(self.return_to_running_state, port=port).then(lambda r: resolve()).catch(lambda e: logger.error(f"{e}"))
        resolve()
    # 6.循环采样ugc二氧化碳传感器浓度和zos氧浓度。
    def cyclic_sampling_of_ugc_carbon_sensor_and_zos_oxygen_sensor(self, resolve, reject, port):
        if self.is_STOP:
            reject()
        global last_carbon_value,last_oxygen_value,last_pressure_value
        #现在测量的氧气值
        now_oxygen_value = None
        now_pressure_value = None
        #现在测量的二氧化碳值
        now_carbon_value = None

        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} |  零点标定 6.循环采样ugc二氧化碳传感器浓度和zos氧浓度。",title=self.title )
        start_time = time.time()
        end_time = None
        #小于阈值稳定0 或者 至少循环60秒
        while (
                (
                        (now_oxygen_value is  None or now_carbon_value is  None) or
                        (last_carbon_value is None or last_oxygen_value is None) or

                        (
                                abs(now_oxygen_value - last_oxygen_value) > float(
                            global_setting.get_setting("UFC_UGC_ZOS_config")['Calibration'][
                                'zero_calibration_oxygen_threshold'])
                                or
                                abs(now_carbon_value - last_carbon_value) > float(
                            global_setting.get_setting("UFC_UGC_ZOS_config")['Calibration'][
                                'zero_calibration_carbon_threshold'])
                        )
                        or
                        (
                                now_carbon_value != 0 or now_oxygen_value != 0
                        )


                ) and
                (
                      end_time is None or int(end_time - start_time) <= float(
                  global_setting.get_setting('UFC_UGC_ZOS_config')['Calibration']['zero_calibration_circular_times'])
                )

        ):
            if self.is_STOP:
                break
            # 循环读取CO2浓度
            self.send_message = {
                'port': port,
                'data': number_util.set_int_to_4_bytes_list("5"),
                'slave_id': '3',
                'function_code': '4',
                'timeout': 1
            }
            self.send_thread.send_message = self.send_message
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} |  零点标定  6.循环采样ugc二氧化碳传感器浓度和zos氧浓度。1）采集二氧化碳浓度，is_STOP={self.is_STOP}",title=self.title )
            carbon_data, carbon_message =self.send_thread.Send_no_promise()
            now_carbon_values = [item['value'] for item in carbon_data['data'] if "CO2" in item['desc']]
            last_carbon_value = copy.deepcopy(now_carbon_value)
            now_carbon_value =now_carbon_values[0] if  now_carbon_values else None
            # 采集氧气
            self.send_message = {
                'port': port,
                'data': number_util.set_int_to_4_bytes_list(f"00000003"),
                'slave_id': '4',
                'function_code': '65',
                'timeout': 1
            }
            self.send_thread.send_message = self.send_message
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} |  零点标定  6.循环采样ugc二氧化碳传感器浓度和zos氧浓度。2）采集氧气浓度",title=self.title )
            oxygen_data,oxygen_message =  self.send_thread.Send_no_promise()
            now_oxygen_values = [item['value'] for item in oxygen_data['data'] if "氧气浓度(%)" in item['desc']]
            last_oxygen_value = copy.deepcopy(now_oxygen_value)
            now_oxygen_value = now_oxygen_values[0] if now_oxygen_values else None

            now_pressure_values = [item['value'] for item in oxygen_data['data'] if "气压力(kPa)" in item['desc']]
            last_pressure_value = copy.deepcopy(now_pressure_value)
            now_pressure_value = now_pressure_values[0] if now_pressure_values else None
            end_time = time.time()
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} |  零点标定 6.循环采样ugc二氧化碳传感器浓度和zos氧浓度。3）现在氧气浓度、zos气压（{now_oxygen_value}，{now_pressure_value}）之前氧气浓度、zos气压（{last_oxygen_value}，{last_pressure_value}）|现在co2浓度（{now_carbon_value}）之前co2浓度（{last_carbon_value}），已经循环{time_util.format_timedelta(a=datetime.fromtimestamp(end_time),b=datetime.fromtimestamp(start_time),zero_pad=True,signed=True)}/{float(global_setting.get_setting('UFC_UGC_ZOS_config')['Calibration']['zero_calibration_circular_times'])}秒",title=self.title )
            # 发送标定数据消息
            self.update_status_main_signal_gui_update.send(
                {'type': 'set_calibration_values',
                 'value': {
                            'oxygen_value':now_oxygen_value,
                            'carbon_value':now_carbon_value,
                            'oxygen_pressure_value':now_pressure_value,
                            }

                 }, title=self.title
            )
            time.sleep(1)
            pass
        last_carbon_value, last_oxygen_value, last_pressure_value =None, None, None
        if self.is_STOP:
            reject()
        #7.二氧化碳零点设置。
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("00100000"),
            'slave_id': '3',
            'function_code': '6',
            'timeout': 1
        }
        self.send_thread.send_message = self.send_message
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} |  零点标定 7.二氧化碳零点设置",title=self.title )
        AsyPromise(self.send_thread.Send).then(
            # 8.氧浓传感器零点记录。
            lambda r: AsyPromise(self.zero_point_recording_of_oxygen_sensor, port=port).then(lambda r2:resolve()).catch(lambda e: logger.error(f"{e}"))
        ).catch(lambda e: reject(e))
        pass
    # 8.氧浓传感器零点记录。
    def zero_point_recording_of_oxygen_sensor(self,resolve,reject,port):
        # 采集氧气
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("3"),
            'slave_id': '4',
            'function_code': '65',
            'timeout': 1
        }
        self.send_thread.send_message = self.send_message
        if self.is_STOP:
            reject()
        oxygen_data, oxygen_message = self.send_thread.Send_no_promise()
        now_oxygen_values = [item['value'] for item in oxygen_data['data'] if "氧气浓度(%)" in item['desc']]
        now_oxygen_value = now_oxygen_values[0] if now_oxygen_values else None

        now_pressure_values = [item['value'] for item in oxygen_data['data'] if "气压力(kPa)" in item['desc']]
        now_pressure_value = now_pressure_values[0] if now_pressure_values else None
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} |  零点标定 8.氧浓传感器零点记录值 zos气压：{now_pressure_value}，氧气浓度：{now_oxygen_values}",title=self.title )
        # 存储值----------------------------------------------------
        return_data_struct={}
        return_data_struct['module_name']='ZeroCalibration'
        return_data_struct['time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        return_data_struct['table_name'] = next(iter(Others_Tables.Zero_Carlibration_Data.value.keys()))
        return_data_struct['mouse_cage_number']=-1
        # 添加Vzero参数到全局变量 方便氧传感器的值校准
        try:
            if now_oxygen_value is None:
                now_oxygen_value=[data['value'] for data in oxygen_data['data'] if data['desc'] =="备注"]
                if len(now_oxygen_value)==0:
                    now_oxygen_value=None
                else:
                    now_oxygen_value=now_oxygen_value[0]
                logger.critical(f"zero_calibration_None:{now_oxygen_value}")
                return_data_struct['data'] =oxygen_data['data']+ [{'desc': '氧浓度0点校准值', 'value':now_oxygen_value}]

            else:
                logger.critical(f"zero_calibration:{now_oxygen_value}")
                global_setting.set_setting("Vzero", now_oxygen_value)
                return_data_struct['data']=[{'desc':'氧浓度0点校准值','value':now_oxygen_value},{'desc':'ZOS压力0点校准值','value':now_pressure_value}]
        except Exception as e:
            return_data_struct['data']=[{'desc':'氧浓度0点校准值','value':now_oxygen_value}]
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} |  零点标定 8.出错，错误：{e} |氧浓传感器零点记录值{now_oxygen_value}，zos压力：{now_pressure_value},oxygen_data：{oxygen_data}，now_oxygen_values：{now_oxygen_values}",title=self.title )
        return_data_struct['slave_id']=0
        return_data_struct['function_code']=0
        result = store_data_with_result(return_data_struct, need_result=True, timeout=5)
        if result and result.success:
            logger.info(f"数据存储成功，ID: {result.item_id}")
        else:
            logger.error(f"数据存储失败: {result.error if result else '未知错误'}")

        if self.is_STOP:
            reject()

        else:
            AsyPromise(self.send_thread.Send).then(
                # 9.校零返回系统工作运行状态
                lambda r: AsyPromise(self.return_to_running_state, port=port
                                     ).then(lambda r2: resolve()).catch(lambda e: logger.error(f"{e}"))
            ).catch(lambda e: reject(e))
        pass


class Range_Carlibration(Gas_Carlibration,MyQThread):
    """
    量程标定
    """
    def __init__(self):
        self.title = GapSystem_Running_Type.RANGE_CALIBRATION
        Gas_Carlibration.__init__(self,title=self.title,type =Gas_Carlibration_Type.SPAN)
        MyQThread.__init__(self, name='Range_Carlibration_thread')
        self.port =None
        pass
    def dosomething(self):
        AsyPromise(self.start_calibration_common,port=self.port,next_function=self.cyclic_sampling_of_zos_oxygen_sensor).then(
            lambda r:self.stop()
        ).catch(lambda e:self.stop())
        pass
    def calibrate(self,resolve,reject):
        """量程标定"""
        self.update_status_main_signal_gui_update.send(f"{time_util.get_format_from_time(time.time())} |  SPan量程标定 开始{'.' * 100}",title=self.title )
        # 发送开始标定消息
        self.update_status_main_signal_gui_update.send(
            {'type':'set_start_span_calibration_time','value':f'{time_util.get_format_from_time(time.time())}'},title=self.title
        )

        self.is_STOP=False
        # resolve()
        self.port = global_setting.get_setting("port", None)
        if self.port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！",title=self.title )
            reject()
        if self.is_STOP:
            reject()
        self.start()
        resolve()
        pass
    def stop_calibrate(self,resolve,reject):
        """
        取消量程标定
        :param resolve:
        :param reject:
        :return:
        """
        self.is_STOP=True
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} |  停止SPan量程标定 开始{'.' * 100}", title=self.title)
        # resolve()
        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！", title=self.title)
            reject()
        AsyPromise(self.return_to_running_state, port=port).then(lambda r: resolve()).catch(lambda e: logger.error(f"{e}"))
        resolve()

    def cyclic_sampling_of_zos_oxygen_sensor(self,resolve,reject,port):
        # 6.循环采样zos氧浓度 和 co2浓度
        global last_oxygen_value,last_carbon_value,last_pressure_value
        # 现在测量的氧气值
        now_oxygen_value = None
        now_carbon_value = None
        now_pressure_value = None
        if self.is_STOP:
            reject()
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} |  SPan量程标定 6.循环采样zos氧浓度和co2浓度。",title=self.title )
        start_time = time.time()
        end_time = None
        # 小于阈值稳定 或者 至少循环60秒
        while (
                (
                        (now_oxygen_value is None or now_carbon_value is None) or
                        (last_carbon_value is None or last_oxygen_value is None) or

                        (
                                abs(now_oxygen_value - last_oxygen_value) > float(
                            global_setting.get_setting("UFC_UGC_ZOS_config")['Calibration'][
                                'span_calibration_oxygen_threshold'])
                                or
                                abs(now_carbon_value - last_carbon_value) > float(
                            global_setting.get_setting("UFC_UGC_ZOS_config")['Calibration'][
                                'span_calibration_carbon_threshold'])
                        )
                        # or
                        # (
                        #         now_carbon_value != 0 or now_oxygen_value != 0
                        # )

                ) and
                (
                        end_time is None or int(end_time - start_time) <= float(
                    global_setting.get_setting('UFC_UGC_ZOS_config')['Calibration']['span_calibration_circular_times'])
                )

        ):
            if self.is_STOP:
                break
            # 循环读取CO2浓度
            self.send_message = {
                'port': port,
                'data': number_util.set_int_to_4_bytes_list("5"),
                'slave_id': '3',
                'function_code': '4',
                'timeout': 1
            }
            self.send_thread.send_message = self.send_message
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} |  SPan量程标定  6.循环采样ugc二氧化碳传感器浓度和zos氧浓度。1）采集二氧化碳浓度，is_STOP={self.is_STOP}",
                title=self.title)
            carbon_data, carbon_message = self.send_thread.Send_no_promise()
            now_carbon_values = [item['value'] for item in carbon_data['data'] if "CO2" in item['desc']]
            last_carbon_value = copy.deepcopy(now_carbon_value)
            now_carbon_value = now_carbon_values[0] if now_carbon_values else None
            # 采集氧气
            self.send_message = {
                'port': port,
                'data': number_util.set_int_to_4_bytes_list(f"00000003"),
                'slave_id': '4',
                'function_code': '65',
                'timeout': 1
            }
            self.send_thread.send_message = self.send_message
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} |  SPan量程标定  6.循环采样ugc二氧化碳传感器浓度和zos氧浓度。2）采集氧气浓度",
                title=self.title)
            oxygen_data, oxygen_message = self.send_thread.Send_no_promise()
            now_oxygen_values = [item['value'] for item in oxygen_data['data'] if "氧气浓度(%)" in item['desc']]
            last_oxygen_value = copy.deepcopy(now_oxygen_value)
            now_oxygen_value = now_oxygen_values[0] if now_oxygen_values else None

            now_pressure_values = [item['value'] for item in oxygen_data['data'] if "气压力(kPa)" in item['desc']]
            last_pressure_value = copy.deepcopy(now_pressure_value)
            now_pressure_value = now_pressure_values[0] if now_pressure_values else None
            end_time = time.time()
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} |  SPan量程标定 6.循环采样ugc二氧化碳传感器浓度和zos氧浓度。3）现在氧气浓度、zos气压（{now_oxygen_value}，{now_pressure_value}）之前氧气浓度、zos气压（{last_oxygen_value}，{last_pressure_value}）|现在co2浓度（{now_carbon_value}）之前co2浓度（{last_carbon_value}），已经循环{time_util.format_timedelta(a=datetime.fromtimestamp(end_time), b=datetime.fromtimestamp(start_time), zero_pad=True, signed=True)}/{float(global_setting.get_setting('UFC_UGC_ZOS_config')['Calibration']['zero_calibration_circular_times'])}秒",
                title=self.title)
            # 发送标定数据消息
            self.update_status_main_signal_gui_update.send(
                {'type': 'set_calibration_values',
                 'value': {
                     'oxygen_value': now_oxygen_value,
                     'carbon_value': now_carbon_value,
                     'oxygen_pressure_value': now_pressure_value,
                 }

                 }, title=self.title
            )
            time.sleep(1)
            pass
        last_carbon_value, last_oxygen_value, last_pressure_value = None, None, None
        if self.is_STOP:
            reject()
        # 7. 氧浓传感器span数值记录。
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"00000003"),
            'slave_id': '4',
            'function_code': '65',
            'timeout': 1
        }
        self.send_thread.send_message = self.send_message

        oxygen_data, oxygen_message = self.send_thread.Send_no_promise()
        now_oxygen_values = [item['value'] for item in oxygen_data['data'] if "氧气浓度(%)" in item['desc']]
        now_oxygen_value = now_oxygen_values[0] if now_oxygen_values else None

        now_pressure_values = [item['value'] for item in oxygen_data['data'] if "气压力(kPa)" in item['desc']]
        now_pressure_value = now_pressure_values[0] if now_pressure_values else None
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} |  SPan量程标定 7.氧浓传感器span数值记录。氧气浓度：{now_oxygen_value}%，zos气压：{now_pressure_value}KPa，CO2：{now_carbon_value}%",title=self.title )
        # 存储值----------------------------------------------------
        return_data_struct = {}
        return_data_struct['module_name'] = 'SpanCalibration'
        return_data_struct['time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        return_data_struct['table_name'] = next(iter(Others_Tables.SPan_Carlibration_Data.value.keys()))
        return_data_struct['mouse_cage_number'] = -1
        # 添加K参数到全局变量 方便氧传感器的值校准 Vr默认是20.9%
        #K=（Vs-Vzero）/（Vr-Vzero）
        if now_oxygen_value is not None:
            K =(now_oxygen_value-global_setting.get_setting("Vzero",0))/(global_setting.get_setting("Vr",20.9)-global_setting.get_setting("Vzero",0))
            logger.warning(f"{time_util.get_format_from_time(time.time())} |  SPan量程标定 7.量程标定的K值为：{K},Vs值为：{now_oxygen_value}，Vr值为：{global_setting.get_setting('Vr',20.9)},Vzero值为：{global_setting.get_setting('Vzero',0)}")
            self.update_status_main_signal_gui_update.send(f"{time_util.get_format_from_time(time.time())} |  SPan量程标定 7.量程标定的K值为：{K},Vs值为：{now_oxygen_value}，Vr值为：{global_setting.get_setting('Vr',20.9)},Vzero值为：{global_setting.get_setting('Vzero',0)}",title=self.title )
            global_setting.set_setting("K",K )
            return_data_struct['data'] = [{'desc': '氧浓传感器span数值', 'value': now_oxygen_value},{'desc': 'ZOS压力span数值', 'value': now_pressure_value},{'desc': '二氧化碳浓传感器span数值', 'value': now_carbon_value}]
        else:
            now_oxygen_value = [data['value'] for data in oxygen_data['data'] if data['desc'] == "备注"]
            if len(now_oxygen_value) == 0:
                now_oxygen_value = None
            else:
                now_oxygen_value = now_oxygen_value[0]
            logger.critical(f"span_calibration:{now_oxygen_value}")
            return_data_struct['data'] =oxygen_data['data']+ [{'desc': '氧浓传感器span数值', 'value': now_oxygen_value}]
        return_data_struct['slave_id'] = 0
        return_data_struct['function_code'] = 0
        result = store_data_with_result(return_data_struct, need_result=True, timeout=5)
        if result and result.success:
            logger.info(f"数据存储成功，ID: {result.item_id}")
        else:
            logger.error(f"数据存储失败: {result.error if result else '未知错误'}")
        if self.is_STOP:
            reject()
        else:
            AsyPromise(self.send_thread.Send).then(
                #8.校SPAN返回系统工作运行状态
                lambda r: AsyPromise(self.return_to_running_state, port=port
                                     ).then(lambda r2:resolve()).catch(lambda e: logger.error(f"{e}"))
            ).catch(lambda e: reject(e))

    pass

