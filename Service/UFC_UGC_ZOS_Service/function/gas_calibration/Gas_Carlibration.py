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
        self.send_thread: Send_Message = Send_Message(
            update_status_main_signal_gui_update=self.update_status_main_signal_gui_update,
            send_message=self.send_message, update_status_main_signal_gui_update_type=title)
        self.ufc_started_by_calibration = False
        # span 标定时，本次下发给设备的目标值
        self.ugc_span_target_co2_ppm = None
        self.zos_span_target_o2_percent = None
        # 标定详情窗口当前显示值缓存
        self.current_calibration_values = {
            'oxygen_value': None,
            'carbon_value': None,
            'oxygen_pressure_value': None,
        }
        self.last_zos_channel_read_time = 0.0

    def update(self):
        self.send_thread.update_status_main_signal_gui_update=self.update_status_main_signal_gui_update
        self.is_STOP = self.is_STOP

    def wait_zos_channel_read_interval(self):
        min_read_interval = 1.0
        try:
            calibration_config = global_setting.get_setting("UFC_UGC_ZOS_config")['Calibration']
            min_read_interval = max(1.0, float(calibration_config.get('zos_channel_read_interval', 1)))
        except Exception:
            pass

        elapsed = time.time() - self.last_zos_channel_read_time
        if elapsed < min_read_interval:
            time.sleep(min_read_interval - elapsed)
        self.last_zos_channel_read_time = time.time()

    def set_calibration_running_state(self, is_running: bool):
        global_setting.set_setting("is_calibrating", is_running)
        global_setting.set_setting("current_calibration_type", self.name if is_running else None)

    def push_calibration_values_to_ui(self, oxygen_value=None, carbon_value=None, oxygen_pressure_value=None):
        if oxygen_value is not None:
            self.current_calibration_values['oxygen_value'] = oxygen_value
        if carbon_value is not None:
            self.current_calibration_values['carbon_value'] = carbon_value
        if oxygen_pressure_value is not None:
            self.current_calibration_values['oxygen_pressure_value'] = oxygen_pressure_value

        self.update_status_main_signal_gui_update.send(
            {
                'type': 'set_calibration_values',
                'value': copy.deepcopy(self.current_calibration_values)
            },
            title=self.title
        )
    @abc.abstractmethod
    def calibrate(self,resolve,reject):
        """
        标定
        :return:
        """
        pass
    """start   start"""

    def start_calibration_common(self, resolve, reject, port, next_function):
        """
        开始标定时 共有的报文
        :param resolve:
        :param reject:
        :param port:
        :param next_function:
        :return:
        """
        AsyPromise(self.open_ugc_zero_or_span_valve, port=port).then(
            lambda r: AsyPromise(next_function, port=port).then(lambda r1: resolve()).catch(lambda e: logger.error(e))
        # ).catch(lambda e: logger.error(e))
        ).catch(lambda e: reject(e))

    def open_ugc_zero_or_span_valve(self, resolve, reject, port):
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
                self.send_thread.send_message = self.send_message
                AsyPromise(self.send_thread.Send).then(
                  lambda _:resolve()
                ).catch(lambda e: logger.error(e))
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
                self.send_thread.send_message = self.send_message
                AsyPromise(self.send_thread.Send).then(
                    lambda _: resolve()
                ).catch(lambda e: logger.error(e))
            case _:
                reject("默认标定")

    def set_ugc_standard_gas_co2(self, resolve, reject, port):
        # 设置UGC标准气体CO2浓度
        if self.is_STOP:
            reject("stop")
            return

        config = global_setting.get_setting("UFC_UGC_ZOS_config")['Calibration']

        # 优先用实验设置页面里填的值：单位 %
        co2_percent = global_setting.get_setting("span_standard_carbon_value", None)
        if co2_percent is not None:
            standard_co2 = int(round(float(co2_percent) * 10000))  # 0.53 -> 5300 ppm
        else:
            # 没有页面值时，走配置兜底，单位按 ppm 处理
            fallback = config.get('standard_co2_concentration', config.get('standard_gas_concentration', 5300))
            standard_co2 = int(round(float(fallback)))


        high_byte = (standard_co2 >> 8) & 0xFF
        low_byte = standard_co2 & 0xFF

        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"0012{high_byte:02X}{low_byte:02X}"),
            'slave_id': '3',
            'function_code': '6',
            'timeout': 1
        }
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | {self.name}标定：2. 设置UGC标准气体CO2浓度={standard_co2}ppm"
        )

        self.send_thread.send_message = self.send_message
        def send_success(r):
            target_co2 = standard_co2
            try:
                if r and r.get("data") and r["data"].get("data"):
                    for item in r["data"]["data"]:
                        if item.get("desc") == "标准气体浓度":
                            target_co2 = int(item.get("value"))
                            break
            except Exception as e:
                logger.warning(f"UGC标准气体浓度取回包失败，使用用户设置值兜底: {e}")

            self.ugc_span_target_co2_ppm = target_co2
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 校span成功："
                f"UGC已成功写入CO2标准气体浓度={target_co2}ppm",
                title=self.title
            )
            resolve()

        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            send_success
        ).catch(lambda e: reject(e))

    def close_ugc_zero_or_span_valve(self, resolve, reject, port):
        # 关闭ugc 校0阀门或校span阀门
        if self.is_STOP:
            reject("stop")
        match self.type:
            case Gas_Carlibration_Type.ZERO:
                self.send_message = {
                    'port': port,
                    'data': number_util.set_int_to_4_bytes_list(f"00010000"),
                    'slave_id': '3',
                    'function_code': '5',
                    'timeout': 1
                }
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} | {self.name}标定： 4. 关闭ugc 校0阀门")
            case Gas_Carlibration_Type.SPAN:
                self.send_message = {
                    'port': port,
                    'data': number_util.set_int_to_4_bytes_list(f"00020000"),
                    'slave_id': '3',
                    'function_code': '5',
                    'timeout': 1
                }
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} | {self.name}标定： 4. 关闭ugc 校span阀门")
            case _:
                reject("默认标定")
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            # 启动zos校0或校span通道
            lambda r: resolve()
        ).catch(lambda e: reject(e))

    def open_zos_zero_or_span_valve(self, resolve, reject, port):
        # 启动zos 校0通道或校span通道
        if self.is_STOP:
            reject("stop")
            return

        match self.type:
            case Gas_Carlibration_Type.ZERO:
                self.send_message = {
                    'port': port,
                    'data': number_util.set_int_to_4_bytes_list("0009FF00"),
                    'slave_id': '4',
                    'function_code': '5',
                    'timeout': 1
                }
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} | {self.name}标定：2. 启动ZOS校0通道"
                )
                self.send_thread.send_message = self.send_message
                AsyPromise(self.send_thread.Send).then(
                    lambda _: resolve()
                ).catch(lambda e: reject(e))

            case Gas_Carlibration_Type.SPAN:
                self.send_message = {
                    'port': port,
                    'data': number_util.set_int_to_4_bytes_list("000AFF00"),
                    'slave_id': '4',
                    'function_code': '5',
                    'timeout': 1
                }
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} | {self.name}标定：2. 启动ZOS校span通道"
                )
                self.send_thread.send_message = self.send_message

                # 先发开始span，再发标准气浓度设置
                AsyPromise(self.send_thread.Send).then(
                    lambda _: resolve()
                ).catch(lambda e: reject(e))

            case _:
                reject("默认标定")

    def set_standard_gas_concentration(self, resolve, reject, port):
        # 设置ZOS标准气体浓度
        if self.is_STOP:
            reject("stop")
            return

        config = global_setting.get_setting("UFC_UGC_ZOS_config")['Calibration']

        # 优先用实验设置页面里填的值：单位 %
        oxygen_percent = global_setting.get_setting("span_standard_oxygen_value", None)
        if oxygen_percent is not None:
            standard_oxygen = int(round(float(oxygen_percent) * 100))  # 20.93 -> 2093
        else:
            fallback = config.get('standard_oxygen_concentration', 2090)
            standard_oxygen = int(round(float(fallback)))

        # 先留一个备用值
        send_o2_percent = standard_oxygen / 100


        high_byte = (standard_oxygen >> 8) & 0xFF
        low_byte = standard_oxygen & 0xFF

        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"0000{high_byte:02X}{low_byte:02X}"),
            'slave_id': '4',
            'function_code': '6',
            'timeout': 1
        }

        self.send_thread.send_message = self.send_message
        def send_success(r):
            target_o2_percent = send_o2_percent
            try:
                if r and r.get("data") and r["data"].get("data"):
                    for item in r["data"]["data"]:
                        if item.get("desc") == "标准气体浓度":
                            target_o2_percent = float(item.get("value"))
                            break
            except Exception as e:
                logger.warning(f"ZOS标准气体浓度取回包失败，使用用户设置值兜底: {e}")

            self.zos_span_target_o2_percent = target_o2_percent
            global_setting.set_setting("Vr", target_o2_percent)
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | {self.name}标定：6. 设置ZOS标准气体浓度={target_o2_percent}%"
            )
            resolve()

        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            send_success
        ).catch(lambda e: reject(e))

    def close_zos_zero_or_span_valve(self, resolve, reject, port):
        # 关闭zos 校0阀门或校span阀门
        if self.is_STOP:
            reject("stop")
        match self.type:
            case Gas_Carlibration_Type.ZERO:
                self.send_message = {
                    'port': port,
                    'data': number_util.set_int_to_4_bytes_list(f"00090000"),
                    'slave_id': '4',
                    'function_code': '5',
                    'timeout': 1
                }
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} | {self.name}标定： 8. 关闭zos 校0阀门")
            case Gas_Carlibration_Type.SPAN:
                self.send_message = {
                    'port': port,
                    'data': number_util.set_int_to_4_bytes_list(f"000A0000"),
                    'slave_id': '4',
                    'function_code': '5',
                    'timeout': 1
                }
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} | {self.name}标定： 9. 关闭zos 校span阀门")
            case _:
                reject("默认标定")
        self.send_thread.send_message = self.send_message
        # 9.完成标定
        AsyPromise(self.send_thread.Send).then(
            lambda r: resolve()
        ).catch(
            lambda e: self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} |  {self.name}标定 关闭ZOS阀门超时，按当前标定完成继续后续流程",
                title=self.title
            ) or AsyPromise(self.finish_calibration).then(
                lambda r1: resolve()
            ).catch(lambda e1: reject(e1))
        )

    """start   end"""


    def finish_calibration(self, resolve, reject):
        self.set_calibration_running_state(False)
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} |  {self.name}标定 9. 标定完成", title=self.title)
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
    def close_reference_valve(self, resolve, reject, port):
        if self.is_STOP:
            reject("stop")
            return

        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("00000000"),
            'slave_id': '3',
            'function_code': '5',
            'timeout': 1
        }
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | 零点标定前关闭reference气电磁阀（空气阀）",
            title=self.title
        )
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            lambda _: resolve()
        ).catch(lambda e: reject(e))

    """exit  end"""


class Zero_Carlibration(Gas_Carlibration, MyQThread):
    """
    零点标定
    """

    def __init__(self):
        self.title = GapSystem_Running_Type.ZERO_CALIBRATION
        self.port = None
        Gas_Carlibration.__init__(self, title=self.title, type=Gas_Carlibration_Type.ZERO)
        MyQThread.__init__(self, name='Zero_Carlibration_thread')

    def dosomething(self):
        AsyPromise(self.close_reference_valve, port=self.port).then(
            lambda _: AsyPromise(
                self.start_calibration_common,
                port=self.port,
                next_function=self.cyclic_sampling_of_ugc_carbon_sensor
            ).then(
                lambda __: AsyPromise(self.cyclic_sampling_of_zos_oxygen_sensor, port=self.port).then(
                    lambda ___: self.stop()
                ).catch(lambda e: self.stop())
            ).catch(lambda e: self.stop())
        ).catch(lambda e: self.stop())
        pass
    def calibrate(self, resolve, reject):
        """零点标定"""
        time.sleep(0.01)
        self.set_calibration_running_state(True)
        # 发送开始标定消息
        self.update_status_main_signal_gui_update.send(
            {'type': 'set_start_zero_calibration_time', 'value': f'{time_util.get_format_from_time(time.time())}'},
            title=self.title
        )
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} |  零点标定 开始{'.' * 100}", title=self.title)
        self.is_STOP = False
        self.current_calibration_values = {
            'oxygen_value': None,
            'carbon_value': None,
            'oxygen_pressure_value': None,
        }
        self.push_calibration_values_to_ui()
        # resolve()
        self.port = global_setting.get_setting("port", None)
        if self.port is None:
            self.set_calibration_running_state(False)
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！", title=self.title)
            reject()
        if self.is_STOP:
            self.set_calibration_running_state(False)
            reject()
        self.start()
        resolve()
        pass

    def stop_calibrate(self, resolve, reject):
        """
        取消零点标定
        :param resolve:
        :param reject:
        :return:
        """
        self.is_STOP = True
        self.set_calibration_running_state(False)
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} |  停止零点量程标定 开始{'.' * 100}", title=self.title)
        # resolve()
        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！", title=self.title)
            reject()
        AsyPromise(self.finish_calibration, port=port).then(lambda r: resolve()).catch(
            lambda e: logger.error(f"{e}"))
        resolve()

    # 2.循环采样ugc二氧化碳传感器浓度
    def cyclic_sampling_of_ugc_carbon_sensor(self, resolve, reject, port):
        if self.is_STOP:
            reject()
            return

        # 获取实际开启的笼子列表，参考气也要算
        mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
        if mouse_cages_inc is None or len(mouse_cages_inc) == 0:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} |  零点标定 错误：未配置笼子列表",
                title=self.title)
            reject("未配置笼子列表")
            return

        active_channels = [8]

        total_channels = len(active_channels)

        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} |  零点标定 2.循环采样ugc二氧化碳传感器浓度（{total_channels}路都稳定，包含参考气）",
            title=self.title)

        config = global_setting.get_setting("UFC_UGC_ZOS_config")['Calibration']
        start_time = time.time()

        max_timeout = float(config.get('calibration_max_timeout', 300))
        stable_duration = float(config.get('reference_channel_stable_duration', 30))
        sample_interval = float(config.get('calibration_sample_interval', 1))
        threshold = float(config.get('zero_calibration_carbon_threshold', 5))
        min_range = float(config.get('zero_calibration_co2_min', 0))
        max_range = float(config.get('zero_calibration_co2_max', 25))

        channels_data = {ch: [] for ch in active_channels}
        channels_stable_start = {ch: None for ch in active_channels}
        channels_finish_state = {ch: False for ch in active_channels}
        all_stable = False

        while not self.is_STOP and not all_stable:
            current_time = time.time()
            elapsed_time = current_time - start_time

            if elapsed_time > max_timeout:
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} |  零点标定 UGC已超时：已运行{int(elapsed_time)}秒，跳过UGC零点设置，继续ZOS零点标定",
                    title=self.title)

                AsyPromise(self.close_ugc_zero_or_span_valve, port=port).then(
                    lambda r: resolve()
                ).catch(lambda e: reject(e))
                return

            # 只处理还没有稳定完成的通道
            pending_channels = [ch for ch in active_channels if not channels_finish_state[ch]]
            if len(pending_channels) == 0:
                all_stable = True
                break

            for channel in pending_channels:
                if self.is_STOP:
                    reject()
                    return

                cage_name = f"{channel + 1}号鼠笼" if channel != 8 else "参考气"

                self.send_message = {
                    'port': port,
                    'data': number_util.set_int_to_4_bytes_list(f"00{channel:02X}0005"),
                    'slave_id': '3',
                    'function_code': '4',
                    'timeout': 1
                }
                self.send_thread.send_message = self.send_message

                carbon_data, carbon_message = self.send_thread.Send_no_promise()
                now_carbon_values = [item['value'] for item in carbon_data['data'] if "CO2" in item['desc']]
                now_carbon_value = now_carbon_values[0] if now_carbon_values else None

                if now_carbon_value is None:
                    channels_stable_start[channel] = None
                    channels_data[channel] = []
                    continue

                # 如果返回的是百分比，这里转成ppm判断
                try:
                    now_carbon_value = float(now_carbon_value)
                    if now_carbon_value < 100:
                        now_carbon_value = now_carbon_value * 10000
                    self.push_calibration_values_to_ui(carbon_value=now_carbon_value)
                except Exception:
                    channels_stable_start[channel] = None
                    channels_data[channel] = []
                    continue

                if not (min_range <= now_carbon_value <= max_range):
                    self.update_status_main_signal_gui_update.send(
                        f"{time_util.get_format_from_time(time.time())} |  零点标定 {cage_name}超出范围[{min_range},{max_range}]ppm，当前={now_carbon_value}ppm",
                        title=self.title)
                    channels_stable_start[channel] = None
                    channels_data[channel] = []
                    continue

                channels_data[channel].append({'time': current_time, 'value': now_carbon_value})
                channels_data[channel] = [
                    d for d in channels_data[channel]
                    if current_time - d['time'] <= stable_duration
                ]

                if len(channels_data[channel]) >= 2:
                    values = [d['value'] for d in channels_data[channel]]
                    variation = max(values) - min(values)

                    if variation < threshold:
                        if channels_stable_start[channel] is None:
                            channels_stable_start[channel] = channels_data[channel][0]['time']

                        stable_time = current_time - channels_stable_start[channel]
                        if stable_time >= stable_duration:
                            channels_finish_state[channel] = True
                            self.update_status_main_signal_gui_update.send(
                                f"{time_util.get_format_from_time(time.time())} |  零点标定 {cage_name}已稳定{int(stable_duration)}秒，CO2={now_carbon_value}ppm，变化={variation:.2f}ppm",
                                title=self.title)
                    else:
                        if channels_stable_start[channel] is not None:
                            self.update_status_main_signal_gui_update.send(
                                f"{time_util.get_format_from_time(time.time())} |  零点标定 {cage_name}波动，重新计时，变化={variation:.2f}ppm",
                                title=self.title)
                        channels_stable_start[channel] = None
                        channels_data[channel] = []

            stable_count = sum(1 for ch in active_channels if channels_finish_state[ch])
            all_stable = stable_count == total_channels

            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} |  零点标定 已稳定{stable_count}/{total_channels}路，已运行{int(time.time() - start_time)}/{int(max_timeout)}秒",
                title=self.title)

            if all_stable:
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} |  零点标定 {total_channels}路全部稳定成功",
                    title=self.title)
                break

            time.sleep(sample_interval)

        if self.is_STOP:
            reject()
            return

        if not all_stable:
            reject("零点标定UGC失败")
            return

        # 3.二氧化碳零点设置
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("00100000"),
            'slave_id': '3',
            'function_code': '6',
            'timeout': 1
        }
        self.send_thread.send_message = self.send_message
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} |  零点标定 3.二氧化碳零点设置", title=self.title)
        AsyPromise(self.send_thread.Send).then(
            lambda r: AsyPromise(self.close_ugc_zero_or_span_valve, port=port).then(
                lambda r2: resolve()).catch(lambda e: logger.error(f"{e}"))
        ).catch(lambda e: reject(e))

    # 6.循环采样zos氧浓度
    def cyclic_sampling_of_zos_oxygen_sensor(self, resolve, reject, port):
        if self.is_STOP:
            reject()
            return

        # 获取实际开启的笼子列表，参考气也要算
        mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
        if mouse_cages_inc is None or len(mouse_cages_inc) == 0:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} |  零点标定 错误：未配置笼子列表",
                title=self.title)
            reject("未配置笼子列表")
            return

        active_channels = [8]

        total_channels = len(active_channels)

        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} |  零点标定 6.循环采样zos氧浓度（{total_channels}路都稳定，包含参考气）",
            title=self.title)

        config = global_setting.get_setting("UFC_UGC_ZOS_config")['Calibration']
        start_time = time.time()

        max_timeout = float(config.get('calibration_max_timeout', 300))
        stable_duration = float(config.get('reference_channel_stable_duration', 30))
        sample_interval = float(config.get('calibration_sample_interval', 1))
        threshold = float(config.get('zero_calibration_oxygen_threshold', 0.1))

        channels_data = {ch: [] for ch in active_channels}
        channels_stable_start = {ch: None for ch in active_channels}
        channels_finish_state = {ch: False for ch in active_channels}
        all_stable = False

        while not self.is_STOP and not all_stable:
            current_time = time.time()
            elapsed_time = current_time - start_time

            if elapsed_time > max_timeout:
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} |  零点标定 氧气超时失败：已运行{int(elapsed_time)}秒",
                    title=self.title)
                reject("零点标定ZOS超时失败")
                return

            pending_channels = [ch for ch in active_channels if not channels_finish_state[ch]]
            if len(pending_channels) == 0:
                all_stable = True
                break

            for channel in pending_channels:
                if self.is_STOP:
                    reject()
                    return

                cage_name = f"{channel + 1}号鼠笼" if channel != 8 else "参考气"

                self.send_message = {
                    'port': port,
                    'data': number_util.set_int_to_4_bytes_list(f"00{channel:02X}000E"),
                    'slave_id': '4',
                    'function_code': '4',
                    'timeout': 1
                }
                self.send_thread.send_message = self.send_message
                self.wait_zos_channel_read_interval()
                oxygen_data, oxygen_message = self.send_thread.Send_no_promise()
                now_oxygen_values = [
                    item['value'] for item in oxygen_data['data']
                    if "氧浓度" in item.get('desc', '')
                ]
                now_oxygen_value = now_oxygen_values[0] if now_oxygen_values else None

                now_pressure_values = [
                    item['value'] for item in oxygen_data['data']
                    if "气体压力" in item.get('desc', '') or "气压" in item.get('desc', '')
                ]
                now_pressure_value = now_pressure_values[0] if now_pressure_values else None
                if now_oxygen_value is None:
                    channels_stable_start[channel] = None
                    channels_data[channel] = []
                    continue

                try:
                    now_oxygen_value = float(now_oxygen_value)
                    if now_oxygen_value > 100:
                        now_oxygen_value = now_oxygen_value / 100
                    self.push_calibration_values_to_ui(
                        oxygen_value=now_oxygen_value,
                        oxygen_pressure_value=now_pressure_value
                    )
                except Exception:
                    channels_stable_start[channel] = None
                    channels_data[channel] = []
                    continue

                sample_time = time.time()
                channels_data[channel].append({'time': sample_time, 'value': now_oxygen_value})
                channels_data[channel] = [
                    d for d in channels_data[channel]
                    if sample_time - d['time'] <= stable_duration
                ]

                if len(channels_data[channel]) >= 2:
                    values = [d['value'] for d in channels_data[channel]]
                    variation = max(values) - min(values)

                    if variation < threshold:
                        if channels_stable_start[channel] is None:
                            channels_stable_start[channel] = channels_data[channel][0]['time']

                        stable_time = sample_time - channels_stable_start[channel]
                        if stable_time >= stable_duration:
                            channels_finish_state[channel] = True
                            self.update_status_main_signal_gui_update.send(
                                f"{time_util.get_format_from_time(time.time())} |  零点标定 {cage_name}氧气已稳定{int(stable_duration)}秒，O2={now_oxygen_value}%，变化={variation:.4f}%",
                                title=self.title)
                    else:
                        if channels_stable_start[channel] is not None:
                            self.update_status_main_signal_gui_update.send(
                                f"{time_util.get_format_from_time(time.time())} |  零点标定 {cage_name}氧气波动，重新计时，变化={variation:.4f}%",
                                title=self.title)
                        channels_stable_start[channel] = None
                        channels_data[channel] = []

            stable_count = sum(1 for ch in active_channels if channels_finish_state[ch])
            all_stable = stable_count == total_channels

            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} |  零点标定 氧气已稳定{stable_count}/{total_channels}路，已运行{int(time.time() - start_time)}/{int(max_timeout)}秒",
                title=self.title)

            if all_stable:
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} |  零点标定 氧气{total_channels}路全部稳定成功",
                    title=self.title)
                break

            time.sleep(sample_interval)

        if self.is_STOP:
            reject()
            return

        if not all_stable:
            reject("零点标定ZOS失败")
            return
        # 7.氧浓传感器零点记录。
        AsyPromise(self.zero_point_recording_of_oxygen_sensor, port=port).then(
            lambda r2: resolve()
        ).catch(lambda e: reject(e))

    # 7.氧浓传感器零点记录。
    def zero_point_recording_of_oxygen_sensor(self, resolve, reject, port):
        # 采集氧气
        mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
        mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
        cage_addr = mouse_cages_inc[mouse_cage_index] - 1 if mouse_cage_index is not None else 8
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"00{cage_addr}000E"),
            'slave_id': '4',
            'function_code': '4',
            'timeout': 1
        }
        self.send_thread.send_message = self.send_message
        if self.is_STOP:
            reject()
        self.wait_zos_channel_read_interval()
        oxygen_data, oxygen_message = self.send_thread.Send_no_promise()
        now_oxygen_values = [item['value'] for item in oxygen_data['data'] if "氧气浓度(%)" in item['desc']]
        now_oxygen_value = now_oxygen_values[0] if now_oxygen_values else None

        now_pressure_values = [item['value'] for item in oxygen_data['data'] if "气压力(kPa)" in item['desc']]
        now_pressure_value = now_pressure_values[0] if now_pressure_values else None
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} |  零点标定 7.氧浓传感器零点记录值 zos气压：{now_pressure_value}，氧气浓度：{now_oxygen_values}",
            title=self.title)
        # 存储值----------------------------------------------------
        return_data_struct = {}
        return_data_struct['module_name'] = 'ZeroCalibration'
        return_data_struct['time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        return_data_struct['table_name'] = next(iter(Others_Tables.Zero_Carlibration_Data.value.keys()))
        return_data_struct['mouse_cage_number'] = -1
        # 添加Vzero参数到全局变量 方便氧传感器的值校准
        try:
            if now_oxygen_value is None:
                now_oxygen_value = [data['value'] for data in oxygen_data['data'] if data['desc'] == "备注"]
                if len(now_oxygen_value) == 0:
                    now_oxygen_value = None
                else:
                    now_oxygen_value = now_oxygen_value[0]
                logger.critical(f"zero_calibration_None:{now_oxygen_value}")
                return_data_struct['data'] = oxygen_data['data'] + [
                    {'desc': '氧浓度0点校准值', 'value': now_oxygen_value}]

            else:
                logger.critical(f"zero_calibration:{now_oxygen_value}")
                global_setting.set_setting("Vzero", now_oxygen_value)
                return_data_struct['data'] = [{'desc': '氧浓度0点校准值', 'value': now_oxygen_value},
                                              {'desc': 'ZOS压力0点校准值', 'value': now_pressure_value}]
        except Exception as e:
            return_data_struct['data'] = [{'desc': '氧浓度0点校准值', 'value': now_oxygen_value}]
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} |  零点标定 7.出错，错误：{e} |氧浓传感器零点记录值{now_oxygen_value}，zos压力：{now_pressure_value},oxygen_data：{oxygen_data}，now_oxygen_values：{now_oxygen_values}",
                title=self.title)
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
            # 8.关闭zos阀门
             AsyPromise(self.close_zos_zero_or_span_valve, port=port).then(
                   lambda _:resolve()
                ).catch(lambda e: logger.error(f"{e}"))
        pass


class Range_Carlibration(Gas_Carlibration, MyQThread):
    """
    量程标定
    """

    def __init__(self):
        self.title = GapSystem_Running_Type.RANGE_CALIBRATION
        Gas_Carlibration.__init__(self, title=self.title, type=Gas_Carlibration_Type.SPAN)
        MyQThread.__init__(self, name='Range_Carlibration_thread')
        self.port = None
        pass

    def dosomething(self):
        AsyPromise(self.start_calibration_common, port=self.port,next_function=self.cyclic_sampling_of_ugc_carbon_sensor).then(
                lambda _:AsyPromise(self.cyclic_sampling_of_zos_oxygen_sensor,port=self.port).then(
                    lambda __: self.stop()
        ).catch(lambda e: self.stop()))
        pass

    def calibrate(self, resolve, reject):
        """量程标定"""
        self.set_calibration_running_state(True)
        # 发送开始标定消息
        self.update_status_main_signal_gui_update.send(
            {'type': 'set_start_span_calibration_time', 'value': f'{time_util.get_format_from_time(time.time())}'},
            title=self.title
        )
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} |  SPan量程标定 开始{'.' * 100}", title=self.title)

        self.is_STOP = False
        self.current_calibration_values = {
            'oxygen_value': None,
            'carbon_value': None,
            'oxygen_pressure_value': None,
        }
        self.push_calibration_values_to_ui()
        # resolve()
        self.port = global_setting.get_setting("port", None)
        if self.port is None:
            self.set_calibration_running_state(False)
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！", title=self.title)
            reject()
        if self.is_STOP:
            self.set_calibration_running_state(False)
            reject()
        self.start()
        resolve()
        pass

    def stop_calibrate(self, resolve, reject):
        """
        取消量程标定
        :param resolve:
        :param reject:
        :return:
        """
        self.is_STOP = True
        self.set_calibration_running_state(False)
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} |  停止SPan量程标定 开始{'.' * 100}", title=self.title)
        # resolve()
        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！", title=self.title)
            reject()
        AsyPromise(self.finish_calibration, port=port).then(lambda r: resolve()).catch(
            lambda e: logger.error(f"{e}"))
        resolve()

    def cyclic_sampling_of_ugc_carbon_sensor(self, resolve, reject, port):
        if self.is_STOP:
            reject()
            return

        mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
        if mouse_cages_inc is None or len(mouse_cages_inc) == 0:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | SPan量程标定 错误：未配置笼子列表",
                title=self.title)
            reject("未配置笼子列表")
            return

        active_channels = [cage - 1 for cage in mouse_cages_inc]
        if 8 not in active_channels:
            active_channels.append(8)

        total_channels = len(active_channels)

        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | SPan量程标定 3.循环采样CO2浓度（{total_channels}路都稳定，包含参考气）",
            title=self.title)

        config = global_setting.get_setting("UFC_UGC_ZOS_config")['Calibration']
        start_time = time.time()

        max_timeout = float(config.get('calibration_max_timeout', 300))
        stable_duration = float(config.get('calibration_stable_duration', 15))
        sample_interval = float(config.get('calibration_sample_interval', 1))
        threshold = float(config.get('span_calibration_carbon_threshold', 15))
        tolerance = float(config.get('span_calibration_co2_tolerance', 25))

        target_co2 = self.ugc_span_target_co2_ppm
        if target_co2 is None:
            co2_percent = global_setting.get_setting("span_standard_carbon_value", None)
            if co2_percent is not None:
                target_co2 = int(round(float(co2_percent) * 10000))
            else:
                target_co2 = int(round(
                    float(config.get('standard_co2_concentration', config.get('standard_gas_concentration', 5300)))))

        min_range = target_co2 - tolerance
        max_range = target_co2 + tolerance

        channels_data = {ch: [] for ch in active_channels}
        channels_stable_start = {ch: None for ch in active_channels}
        channels_finish_state = {ch: False for ch in active_channels}
        all_stable = False

        while not self.is_STOP and not all_stable:
            current_time = time.time()
            elapsed_time = current_time - start_time

            if elapsed_time > max_timeout:
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} | SPan量程标定 UGC已超时：已运行{int(elapsed_time)}秒，跳过UGC量程设置，继续ZOS量程标定",
                    title=self.title)
                AsyPromise(self.close_ugc_zero_or_span_valve, port=port).then(
                    lambda _: resolve()
                ).catch(lambda e: reject(e))
                return

            pending_channels = [ch for ch in active_channels if not channels_finish_state[ch]]
            if len(pending_channels) == 0:
                all_stable = True
                break

            for channel in pending_channels:
                if self.is_STOP:
                    reject()
                    return

                cage_name = f"{channel + 1}号鼠笼" if channel != 8 else "参考气"

                self.send_message = {
                    'port': port,
                    'data': number_util.set_int_to_4_bytes_list(f"00{channel:02X}0005"),
                    'slave_id': '3',
                    'function_code': '4',
                    'timeout': 1
                }
                self.send_thread.send_message = self.send_message

                carbon_data, carbon_message = self.send_thread.Send_no_promise()
                if not carbon_data or 'data' not in carbon_data:
                    channels_stable_start[channel] = None
                    channels_data[channel] = []
                    continue

                now_carbon_values = [
                    item['value'] for item in carbon_data['data']
                    if 'CO2' in item.get('desc', '') and '标准气' not in item.get('desc', '')
                ]
                now_carbon_value = now_carbon_values[0] if now_carbon_values else None

                if now_carbon_value is None:
                    channels_stable_start[channel] = None
                    channels_data[channel] = []
                    continue

                try:
                    now_carbon_value = float(now_carbon_value)
                    if now_carbon_value < 100:
                        now_carbon_value = now_carbon_value * 10000
                    self.push_calibration_values_to_ui(carbon_value=now_carbon_value)
                except Exception:
                    channels_stable_start[channel] = None
                    channels_data[channel] = []
                    continue

                if not (min_range <= now_carbon_value <= max_range):
                    self.update_status_main_signal_gui_update.send(
                        f"{time_util.get_format_from_time(time.time())} | SPan量程标定 {cage_name}超出范围[{min_range},{max_range}]ppm，当前{now_carbon_value}ppm",
                        title=self.title)
                    channels_stable_start[channel] = None
                    channels_data[channel] = []
                    continue

                channels_data[channel].append({'time': current_time, 'value': now_carbon_value})
                channels_data[channel] = [
                    d for d in channels_data[channel]
                    if current_time - d['time'] <= stable_duration
                ]

                if len(channels_data[channel]) >= 2:
                    values = [d['value'] for d in channels_data[channel]]
                    variation = max(values) - min(values)

                    if variation < threshold:
                        if channels_stable_start[channel] is None:
                            channels_stable_start[channel] = channels_data[channel][0]['time']

                        stable_time = current_time - channels_stable_start[channel]
                        if stable_time >= stable_duration:
                            channels_finish_state[channel] = True
                            self.update_status_main_signal_gui_update.send(
                                f"{time_util.get_format_from_time(time.time())} | SPan量程标定 {cage_name}已稳定{int(stable_duration)}秒，CO2={now_carbon_value}ppm，变化={variation:.2f}ppm",
                                title=self.title)
                    else:
                        channels_stable_start[channel] = None
                        channels_data[channel] = []

            stable_count = sum(1 for ch in active_channels if channels_finish_state[ch])
            all_stable = stable_count == total_channels

            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | SPan量程标定 UGC已稳定{stable_count}/{total_channels}路，已运行{int(time.time() - start_time)}/{int(max_timeout)}秒",
                title=self.title)

            if all_stable:
                break

            time.sleep(sample_interval)

        if self.is_STOP:
            reject()
            return

        if not all_stable:
            reject("SPan量程标定UGC失败")
            return

        # 全部稳定后，发送 span 标定指令
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("00110000"),
            'slave_id': '3',
            'function_code': '6',
            'timeout': 1
        }
        self.send_thread.send_message = self.send_message
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | SPan量程标定 4.发送UGC span标定指令",
            title=self.title)

        AsyPromise(self.send_thread.Send).then(
            lambda _: AsyPromise(self.close_ugc_zero_or_span_valve, port=port).then(
                lambda __: resolve()
            ).catch(lambda e: reject(e))
        ).catch(lambda e: reject(e))

    def cyclic_sampling_of_zos_oxygen_sensor(self, resolve, reject, port):
        if self.is_STOP:
            reject()
            return

        mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
        if mouse_cages_inc is None or len(mouse_cages_inc) == 0:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | SPan量程标定 错误：未配置笼子列表",
                title=self.title
            )
            reject("未配置笼子列表")
            return

        active_channels = [cage - 1 for cage in mouse_cages_inc]
        if 8 not in active_channels:
            active_channels.append(8)

        total_channels = len(active_channels)

        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | SPan量程标定 7.循环采样ZOS氧浓度（{total_channels}路都稳定，包含参考气）",
            title=self.title
        )

        config = global_setting.get_setting("UFC_UGC_ZOS_config")['Calibration']
        start_time = time.time()

        max_timeout = float(config.get('calibration_max_timeout', 300))
        stable_duration = float(config.get('calibration_stable_duration', 15))
        sample_interval = float(config.get('calibration_sample_interval', 1))
        threshold = float(config.get('span_calibration_oxygen_threshold', 0.1))

        channels_data = {ch: [] for ch in active_channels}
        channels_stable_start = {ch: None for ch in active_channels}
        channels_finish_state = {ch: False for ch in active_channels}
        all_stable = False

        while not self.is_STOP and not all_stable:
            current_time = time.time()
            elapsed_time = current_time - start_time

            if elapsed_time > max_timeout:
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} | SPan量程标定 ZOS超时失败：已运行{int(elapsed_time)}秒",
                    title=self.title
                )
                reject("SPan量程标定ZOS超时失败")
                return

            pending_channels = [ch for ch in active_channels if not channels_finish_state[ch]]
            if len(pending_channels) == 0:
                all_stable = True
                break

            for channel in pending_channels:
                if self.is_STOP:
                    reject()
                    return

                cage_name = f"{channel + 1}号鼠笼" if channel != 8 else "参考气"

                self.send_message = {
                    'port': port,
                    'data': number_util.set_int_to_4_bytes_list(f"00{channel:02X}000E"),
                    'slave_id': '4',
                    'function_code': '4',
                    'timeout': 1
                }
                self.send_thread.send_message = self.send_message
                self.wait_zos_channel_read_interval()
                oxygen_data, oxygen_message = self.send_thread.Send_no_promise()
                if not oxygen_data or 'data' not in oxygen_data:
                    channels_stable_start[channel] = None
                    channels_data[channel] = []
                    continue

                now_oxygen_values = [
                    item['value'] for item in oxygen_data['data']
                    if "氧浓度" in item.get('desc', '')
                ]
                now_oxygen_value = now_oxygen_values[0] if now_oxygen_values else None

                now_pressure_values = [
                    item['value'] for item in oxygen_data['data']
                    if "气体压力" in item.get('desc', '') or "气压" in item.get('desc', '')
                ]
                now_pressure_value = now_pressure_values[0] if now_pressure_values else None
                if now_oxygen_value is None:
                    channels_stable_start[channel] = None
                    channels_data[channel] = []
                    continue

                try:
                    now_oxygen_value = float(now_oxygen_value)
                    if now_oxygen_value > 100:
                        now_oxygen_value = now_oxygen_value / 100
                    self.push_calibration_values_to_ui(
                        oxygen_value=now_oxygen_value,
                        oxygen_pressure_value=now_pressure_value
                    )
                except Exception:
                    channels_stable_start[channel] = None
                    channels_data[channel] = []
                    continue

                sample_time = time.time()
                channels_data[channel].append({
                    'time': sample_time,
                    'value': now_oxygen_value
                })
                channels_data[channel] = [
                    d for d in channels_data[channel]
                    if sample_time - d['time'] <= stable_duration
                ]

                if len(channels_data[channel]) >= 2:
                    values = [d['value'] for d in channels_data[channel]]
                    variation = max(values) - min(values)

                    if variation < threshold:
                        if channels_stable_start[channel] is None:
                            channels_stable_start[channel] = channels_data[channel][0]['time']

                        stable_time = sample_time - channels_stable_start[channel]
                        if stable_time >= stable_duration:
                            channels_finish_state[channel] = True
                            self.update_status_main_signal_gui_update.send(
                                f"{time_util.get_format_from_time(time.time())} | SPan量程标定 {cage_name}氧浓度已稳定{int(stable_duration)}秒，O2={now_oxygen_value}%，变化={variation:.4f}%",
                                title=self.title
                            )
                    else:
                        channels_stable_start[channel] = None
                        channels_data[channel] = []

            stable_count = sum(1 for ch in active_channels if channels_finish_state[ch])
            all_stable = stable_count == total_channels

            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | SPan量程标定 ZOS已稳定{stable_count}/{total_channels}路，已运行{int(time.time() - start_time)}/{int(max_timeout)}秒",
                title=self.title
            )

            if all_stable:
                break

            time.sleep(sample_interval)

        if self.is_STOP:
            reject()
            return

        if not all_stable:
            reject("SPan量程标定ZOS失败")
            return

        # 全部稳定后，读取一次当前值做记录
        mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
        mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
        cage_addr = mouse_cages_inc[mouse_cage_index] - 1 if mouse_cage_index is not None else 8

        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"00{cage_addr}000E"),
            'slave_id': '4',
            'function_code': '4',
            'timeout': 1
        }
        self.send_thread.send_message = self.send_message
        self.wait_zos_channel_read_interval()
        oxygen_data, oxygen_message = self.send_thread.Send_no_promise()
        now_oxygen_values = [item['value'] for item in oxygen_data['data'] if "氧浓度" in item.get('desc', '')]
        now_oxygen_value = now_oxygen_values[0] if now_oxygen_values else None

        now_pressure_values = [
            item['value'] for item in oxygen_data['data']
            if "气体压力" in item.get('desc', '') or "气压" in item.get('desc', '')
        ]
        now_pressure_value = now_pressure_values[0] if now_pressure_values else None

        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | SPan量程标定 8.氧浓传感器span数值记录。氧气浓度：{now_oxygen_value}%，ZOS气压：{now_pressure_value}",
            title=self.title
        )

        return_data_struct = {}
        return_data_struct['module_name'] = 'SpanCalibration'
        return_data_struct['time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        return_data_struct['table_name'] = next(iter(Others_Tables.SPan_Carlibration_Data.value.keys()))
        return_data_struct['mouse_cage_number'] = -1

        if now_oxygen_value is not None:
            vr_value = global_setting.get_setting("Vr",
                                                  self.zos_span_target_o2_percent if self.zos_span_target_o2_percent is not None else 20.9)
            K = (now_oxygen_value - global_setting.get_setting("Vzero", 0)) / (
                    vr_value - global_setting.get_setting("Vzero", 0)
            )
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | SPan量程标定 8.量程标定K值为：{K}, Vs={now_oxygen_value}, Vr={vr_value}, Vzero={global_setting.get_setting('Vzero', 0)}",
                title=self.title
            )
            global_setting.set_setting("K", K)
            return_data_struct['data'] = [
                {'desc': '氧浓传感器span数值', 'value': now_oxygen_value},
                {'desc': 'ZOS压力span数值', 'value': now_pressure_value}
            ]
        else:
            return_data_struct['data'] = [
                {'desc': '氧浓传感器span数值', 'value': now_oxygen_value}
            ]

        return_data_struct['slave_id'] = 0
        return_data_struct['function_code'] = 0
        store_data_with_result(return_data_struct, need_result=True, timeout=5)

        if self.is_STOP:
            reject()
        else:
            AsyPromise(self.close_zos_zero_or_span_valve, port=port).then(
                lambda _: resolve()
            ).catch(lambda e: reject(e))


def _patched_normalize_co2_to_ppm(self, co2_value):
    if co2_value is None:
        return None

    co2_value = float(co2_value)
    if co2_value < 100:
        return co2_value * 10000
    return co2_value


def _patched_normalize_oxygen_percent(self, oxygen_value):
    if oxygen_value is None:
        return None

    oxygen_value = float(oxygen_value)
    if oxygen_value > 100:
        oxygen_value = oxygen_value / 100
    return oxygen_value


def _patched_read_zos_channel_snapshot(self, port, channel):
    self.send_message = {
        'port': port,
        'data': number_util.set_int_to_4_bytes_list(f"00{channel:02X}000E"),
        'slave_id': '4',
        'function_code': '4',
        'timeout': 1
    }
    self.send_thread.send_message = self.send_message
    self.wait_zos_channel_read_interval()
    zos_data, _ = self.send_thread.Send_no_promise()
    if not zos_data or 'data' not in zos_data:
        return None

    pressure_value = None
    oxygen_value = None
    for item in zos_data.get('data', []):
        desc = item.get('desc', '')
        if pressure_value is None and ("气体压力" in desc or "气压" in desc):
            pressure_value = item.get('value')
        if oxygen_value is None and "氧浓度" in desc:
            oxygen_value = item.get('value')

    if pressure_value is not None:
        pressure_value = float(pressure_value)
    oxygen_value = self._normalize_oxygen_percent(oxygen_value)
    return {
        "pressure": pressure_value,
        "oxygen": oxygen_value
    }


def _patched_calculate_compensated_co2_ppm(self, co2_ppm, zos_gas_pressure):
    if co2_ppm is None:
        return None
    if zos_gas_pressure is None or float(zos_gas_pressure) == 0:
        return None

    standard_atmospheric_pressure = float(
        global_setting.get_setting("UFC_UGC_ZOS_config")['PARAM']['standard_atmospheric_pressure']
    )
    return round(float(co2_ppm) * standard_atmospheric_pressure / float(zos_gas_pressure), 4)


def _patched_close_ugc_zero_or_span_valve(self, resolve, reject, port):
    if self.is_STOP:
        reject("stop")
        return

    match self.type:
        case Gas_Carlibration_Type.ZERO:
            self.send_message = {
                'port': port,
                'data': number_util.set_int_to_4_bytes_list("00010000"),
                'slave_id': '3',
                'function_code': '5',
                'timeout': 1
            }
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | {self.name}校准：关闭UGC零点阀门"
            )
        case Gas_Carlibration_Type.SPAN:
            self.send_message = {
                'port': port,
                'data': number_util.set_int_to_4_bytes_list("00020000"),
                'slave_id': '3',
                'function_code': '5',
                'timeout': 1
            }
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | {self.name}校准：关闭UGC span阀门"
            )
        case _:
            reject("默认标定")
            return

    self.send_thread.send_message = self.send_message
    AsyPromise(self.send_thread.Send).then(
        lambda _: resolve()
    ).catch(
        lambda e: self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | {self.name}校准：关闭UGC阀门失败，继续当前流程",
            title=self.title
        ) or resolve()
    )


def _log_ugc_calibration_command_result(
        self,
        result,
        command_name,
        calibration_name,
        error=None,
):
    response_hex = ""
    send_state = False
    if isinstance(result, dict):
        send_state = result.get("send_state") is True
        response_hex = "".join(str(result.get("response_hex") or "").split()).lower()

    expected_prefix = "".join(
        f"{int(value, 16):02x}" if isinstance(value, str) else f"{int(value):02x}"
        for value in [
            self.send_message["slave_id"],
            self.send_message["function_code"],
            *self.send_message["data"],
        ]
    )
    response_matches = response_hex.startswith(expected_prefix)
    success = send_state and response_matches
    status = "成功" if success else "失败"
    response_text = response_hex or "无响应"
    error_text = f"，错误={error}" if error is not None else ""

    self.update_status_main_signal_gui_update.send(
        f"{time_util.get_format_from_time(time.time())} | {self.name} {command_name}响应核对{status}，"
        f"{calibration_name}{status}，响应报文={response_text}{error_text}",
        title=self.title,
    )
    return success


def _patched_zero_cyclic_sampling_of_ugc_carbon_sensor(self, resolve, reject, port):
    if self.is_STOP:
        reject()
        return

    mouse_cages_inc = global_setting.get_setting("mouse_cages", None)
    if mouse_cages_inc is None or len(mouse_cages_inc) == 0:
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | 零点标定 错误：未配置鼠笼列表",
            title=self.title)
        reject("未配置鼠笼列表")
        return

    active_channels = [8]

    total_channels = len(active_channels)
    self.update_status_main_signal_gui_update.send(
        f"{time_util.get_format_from_time(time.time())} | 零点标定 采样UGC CO2并按ZOS气体压力补偿（{total_channels}路都需稳定，包含参考气）",
        title=self.title)

    config = global_setting.get_setting("UFC_UGC_ZOS_config")['Calibration']
    start_time = time.time()
    max_timeout = float(config.get('calibration_max_timeout', 300))
    stable_duration = float(config.get('reference_channel_stable_duration', 30))
    sample_interval = float(config.get('calibration_sample_interval', 1))
    threshold = float(config.get('zero_calibration_carbon_threshold', 5))

    channels_data = {ch: [] for ch in active_channels}
    channels_stable_start = {ch: None for ch in active_channels}
    channels_finish_state = {ch: False for ch in active_channels}
    all_stable = False

    while not self.is_STOP and not all_stable:
        current_time = time.time()
        elapsed_time = current_time - start_time

        if elapsed_time > max_timeout:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 零点标定 UGC已超时：已运行{int(elapsed_time)}秒，进入统一收尾流程",
                title=self.title)
            resolve()
            return

        pending_channels = [ch for ch in active_channels if not channels_finish_state[ch]]
        if len(pending_channels) == 0:
            all_stable = True
            break

        for channel in pending_channels:
            if self.is_STOP:
                reject()
                return

            cage_name = f"{channel + 1}号鼠笼" if channel != 8 else "参考气"
            self.send_message = {
                'port': port,
                'data': number_util.set_int_to_4_bytes_list(f"00{channel:02X}0005"),
                'slave_id': '3',
                'function_code': '4',
                'timeout': 1
            }
            self.send_thread.send_message = self.send_message

            carbon_data, _ = self.send_thread.Send_no_promise()
            now_carbon_values = [item['value'] for item in carbon_data['data'] if "CO2" in item['desc']]
            now_carbon_value = now_carbon_values[0] if now_carbon_values else None
            if now_carbon_value is None:
                channels_stable_start[channel] = None
                channels_data[channel] = []
                continue

            try:
                now_carbon_value = self._normalize_co2_to_ppm(now_carbon_value)
            except Exception:
                channels_stable_start[channel] = None
                channels_data[channel] = []
                continue

            zos_snapshot = self._read_zos_channel_snapshot(port, channel)
            zos_pressure_value = zos_snapshot.get("pressure") if zos_snapshot else None
            compensated_carbon_value = self._calculate_compensated_co2_ppm(now_carbon_value, zos_pressure_value)
            if compensated_carbon_value is None:
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} | 零点标定 {cage_name} 未获取到有效ZOS气体压力，无法进行CO2补偿",
                    title=self.title)
                channels_stable_start[channel] = None
                channels_data[channel] = []
                continue

            self.push_calibration_values_to_ui(
                oxygen_value=zos_snapshot.get("oxygen") if zos_snapshot else None,
                carbon_value=compensated_carbon_value,
                oxygen_pressure_value=zos_pressure_value
            )


            channels_data[channel].append({'time': current_time, 'value': compensated_carbon_value})
            channels_data[channel] = [
                d for d in channels_data[channel]
                if current_time - d['time'] <= stable_duration
            ]

            if len(channels_data[channel]) >= 2:
                values = [d['value'] for d in channels_data[channel]]
                variation = max(values) - min(values)
                if variation < threshold:
                    if channels_stable_start[channel] is None:
                        channels_stable_start[channel] = channels_data[channel][0]['time']

                    stable_time = current_time - channels_stable_start[channel]
                    if stable_time >= stable_duration:
                        channels_finish_state[channel] = True
                        self.update_status_main_signal_gui_update.send(
                            f"{time_util.get_format_from_time(time.time())} | 零点标定 {cage_name}补偿后CO2已稳定{int(stable_duration)}秒，补偿后={compensated_carbon_value}ppm，原始值={now_carbon_value}ppm，ZOS气压={zos_pressure_value}，变化={variation:.2f}ppm",
                            title=self.title)
                else:
                    if channels_stable_start[channel] is not None:
                        self.update_status_main_signal_gui_update.send(
                            f"{time_util.get_format_from_time(time.time())} | 零点标定 {cage_name}补偿后CO2波动，重新计时，变化={variation:.2f}ppm",
                            title=self.title)
                    channels_stable_start[channel] = None
                    channels_data[channel] = []

        stable_count = sum(1 for ch in active_channels if channels_finish_state[ch])
        all_stable = stable_count == total_channels

        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | 零点标定 UGC已稳定{stable_count}/{total_channels}路，已运行{int(time.time() - start_time)}/{int(max_timeout)}秒",
            title=self.title)

        if all_stable:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 零点标定 UGC {total_channels}路全部稳定成功",
                title=self.title)
            break

        time.sleep(sample_interval)

    if self.is_STOP:
        reject()
        return

    if not all_stable:
        reject("零点标定UGC失败")
        return

    self.send_message = {
        'port': port,
        'data': number_util.set_int_to_4_bytes_list("00100000"),
        'slave_id': '3',
        'function_code': '6',
        'timeout': 1
    }
    self.send_thread.send_message = self.send_message
    self.update_status_main_signal_gui_update.send(
        f"{time_util.get_format_from_time(time.time())} | 零点标定 发送UGC零点设置指令",
        title=self.title)
    def handle_result(result):
        _log_ugc_calibration_command_result(
            self,
            result,
            command_name="UGC零点设置指令",
            calibration_name="校0",
        )
        resolve()

    def handle_error(error):
        _log_ugc_calibration_command_result(
            self,
            None,
            command_name="UGC零点设置指令",
            calibration_name="校0",
            error=error,
        )
        reject(error)

    AsyPromise(self.send_thread.Send).then(handle_result).catch(handle_error)


def _patched_range_cyclic_sampling_of_ugc_carbon_sensor(self, resolve, reject, port):
    if self.is_STOP:
        reject()
        return

    mouse_cages_inc = global_setting.get_setting("mouse_cages", None)
    if mouse_cages_inc is None or len(mouse_cages_inc) == 0:
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | SPan量程标定 错误：未配置鼠笼列表",
            title=self.title)
        reject("未配置鼠笼列表")
        return

    active_channels = [8]

    total_channels = len(active_channels)
    self.update_status_main_signal_gui_update.send(
        f"{time_util.get_format_from_time(time.time())} | SPan量程标定 采样UGC CO2并按ZOS气体压力补偿（{total_channels}路都需稳定，包含参考气）",
        title=self.title)

    config = global_setting.get_setting("UFC_UGC_ZOS_config")['Calibration']
    start_time = time.time()
    max_timeout = float(config.get('calibration_max_timeout', 300))
    stable_duration = float(config.get('reference_channel_stable_duration', 30))
    sample_interval = float(config.get('calibration_sample_interval', 1))
    threshold = float(config.get('span_calibration_carbon_threshold', 15))
    tolerance = float(config.get('span_calibration_co2_tolerance', 25))

    target_co2 = self.ugc_span_target_co2_ppm
    if target_co2 is None:
        co2_percent = global_setting.get_setting("span_standard_carbon_value", None)
        if co2_percent is not None:
            target_co2 = int(round(float(co2_percent) * 10000))
        else:
            target_co2 = int(round(
                float(config.get('standard_co2_concentration', config.get('standard_gas_concentration', 5300)))
            ))


    channels_data = {ch: [] for ch in active_channels}
    channels_stable_start = {ch: None for ch in active_channels}
    channels_finish_state = {ch: False for ch in active_channels}
    all_stable = False

    while not self.is_STOP and not all_stable:
        current_time = time.time()
        elapsed_time = current_time - start_time

        if elapsed_time > max_timeout:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | SPan量程标定 UGC已超时：已运行{int(elapsed_time)}秒，进入统一收尾流程",
                title=self.title)
            resolve()
            return

        pending_channels = [ch for ch in active_channels if not channels_finish_state[ch]]
        if len(pending_channels) == 0:
            all_stable = True
            break

        for channel in pending_channels:
            if self.is_STOP:
                reject()
                return

            cage_name = f"{channel + 1}号鼠笼" if channel != 8 else "参考气"
            self.send_message = {
                'port': port,
                'data': number_util.set_int_to_4_bytes_list(f"00{channel:02X}0005"),
                'slave_id': '3',
                'function_code': '4',
                'timeout': 1
            }
            self.send_thread.send_message = self.send_message

            carbon_data, _ = self.send_thread.Send_no_promise()
            if not carbon_data or 'data' not in carbon_data:
                channels_stable_start[channel] = None
                channels_data[channel] = []
                continue

            now_carbon_values = [
                item['value'] for item in carbon_data['data']
                if 'CO2' in item.get('desc', '') and '标准气' not in item.get('desc', '')
            ]
            now_carbon_value = now_carbon_values[0] if now_carbon_values else None
            if now_carbon_value is None:
                channels_stable_start[channel] = None
                channels_data[channel] = []
                continue

            try:
                now_carbon_value = self._normalize_co2_to_ppm(now_carbon_value)
            except Exception:
                channels_stable_start[channel] = None
                channels_data[channel] = []
                continue

            zos_snapshot = self._read_zos_channel_snapshot(port, channel)
            zos_pressure_value = zos_snapshot.get("pressure") if zos_snapshot else None
            compensated_carbon_value = self._calculate_compensated_co2_ppm(now_carbon_value, zos_pressure_value)
            if compensated_carbon_value is None:
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} | SPan量程标定 {cage_name} 未获取到有效ZOS气体压力，无法进行CO2补偿",
                    title=self.title)
                channels_stable_start[channel] = None
                channels_data[channel] = []
                continue

            self.push_calibration_values_to_ui(
                oxygen_value=zos_snapshot.get("oxygen") if zos_snapshot else None,
                carbon_value=compensated_carbon_value,
                oxygen_pressure_value=zos_pressure_value
            )


            channels_data[channel].append({'time': current_time, 'value': compensated_carbon_value})
            channels_data[channel] = [
                d for d in channels_data[channel]
                if current_time - d['time'] <= stable_duration
            ]

            if len(channels_data[channel]) >= 2:
                values = [d['value'] for d in channels_data[channel]]
                variation = max(values) - min(values)
                if variation < threshold:
                    if channels_stable_start[channel] is None:
                        channels_stable_start[channel] = channels_data[channel][0]['time']

                    stable_time = current_time - channels_stable_start[channel]
                    if stable_time >= stable_duration:
                        channels_finish_state[channel] = True
                        self.update_status_main_signal_gui_update.send(
                            f"{time_util.get_format_from_time(time.time())} | SPan量程标定 {cage_name}补偿后CO2已稳定{int(stable_duration)}秒，补偿后={compensated_carbon_value}ppm，原始值={now_carbon_value}ppm，ZOS气压={zos_pressure_value}，变化={variation:.2f}ppm",
                            title=self.title)
                else:
                    channels_stable_start[channel] = None
                    channels_data[channel] = []

        stable_count = sum(1 for ch in active_channels if channels_finish_state[ch])
        all_stable = stable_count == total_channels

        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | SPan量程标定 UGC已稳定{stable_count}/{total_channels}路，已运行{int(time.time() - start_time)}/{int(max_timeout)}秒",
            title=self.title)

        if all_stable:
            break

        time.sleep(sample_interval)

    if self.is_STOP:
        reject()
        return

    if not all_stable:
        reject("SPan量程标定UGC失败")
        return

    self.send_message = {
        'port': port,
        'data': number_util.set_int_to_4_bytes_list("00110000"),
        'slave_id': '3',
        'function_code': '6',
        'timeout': 1
    }
    self.send_thread.send_message = self.send_message
    self.update_status_main_signal_gui_update.send(
        f"{time_util.get_format_from_time(time.time())} | SPan量程标定 发送UGC span标定指令",
        title=self.title)
    def handle_result(result):
        _log_ugc_calibration_command_result(
            self,
            result,
            command_name="UGC span标定指令",
            calibration_name="校span",
        )
        resolve()

    def handle_error(error):
        _log_ugc_calibration_command_result(
            self,
            None,
            command_name="UGC span标定指令",
            calibration_name="校span",
            error=error,
        )
        reject(error)

    AsyPromise(self.send_thread.Send).then(handle_result).catch(handle_error)


def _patched_force_close_zos_zero_or_span_valve_v2(self, resolve, reject, port):
    self.defer_zos_close_until_finalize = False

    if self.is_STOP:
        reject("stop")
        return

    match self.type:
        case Gas_Carlibration_Type.ZERO:
            self.send_message = {
                'port': port,
                'data': number_util.set_int_to_4_bytes_list("00090000"),
                'slave_id': '4',
                'function_code': '5',
                'timeout': 1
            }
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | {self.name}校准：UGC完成后再关闭ZOS零点阀门"
            )
        case Gas_Carlibration_Type.SPAN:
            self.send_message = {
                'port': port,
                'data': number_util.set_int_to_4_bytes_list("000A0000"),
                'slave_id': '4',
                'function_code': '5',
                'timeout': 1
            }
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | {self.name}校准：UGC完成后再关闭ZOS span阀门"
            )
        case _:
            reject("default calibration")
            return

    self.send_thread.send_message = self.send_message
    AsyPromise(self.send_thread.Send).then(
        lambda _: resolve()
    ).catch(
        lambda e: self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | {self.name}校准：关闭ZOS阀门失败，继续当前收尾流程",
            title=self.title
        ) or resolve()
    )


def _patched_close_zos_zero_or_span_valve_v2(self, resolve, reject, port):
    if getattr(self, "defer_zos_close_until_finalize", False):
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | {self.name}校准：当前先保持ZOS校准状态，暂不发送结束指令",
            title=self.title
        )
        resolve()
        return

    _patched_force_close_zos_zero_or_span_valve_v2(self, resolve, reject, port)


def _patched_finish_calibration_core_v2(self, resolve, reject):
    self.set_calibration_running_state(False)
    self.update_status_main_signal_gui_update.send(
        f"{time_util.get_format_from_time(time.time())} |  {self.name}标定 9. 标定完成", title=self.title)
    send_message_queue = global_setting.get_setting("send_message_queue")
    match self.type:
        case Gas_Carlibration_Type.SPAN:
            send_message_queue.put(ObjectQueueItem(origin='Gas_Carlibration', to='monitor_data_new_index',
                                           title='range_calibration_finish',
                                           data=None,
                                           time=time_util.get_format_from_time(time.time())))
            self.update_status_main_signal_gui_update.send(
                {'type': 'set_stop_span_calibration_time',
                 'value': f'{time_util.get_format_from_time(time.time())}'}, title=self.title
            )
        case Gas_Carlibration_Type.ZERO:
            send_message_queue.put(ObjectQueueItem(origin='Gas_Carlibration', to='monitor_data_new_index',
                                                   title='zero_calibration_finish',
                                                   data=None,
                                                   time=time_util.get_format_from_time(time.time())))
            self.update_status_main_signal_gui_update.send(
                {'type': 'set_stop_zero_calibration_time',
                 'value': f'{time_util.get_format_from_time(time.time())}'}, title=self.title
            )
        case _:
            pass
    resolve()


def _patched_finish_calibration_v2(self, resolve, reject, port=None):
    if self.is_STOP:
        _patched_finish_calibration_core_v2(self, resolve, reject)
        return

    if getattr(self, "defer_zos_close_until_finalize", False):
        if port is None:
            port = getattr(self, "port", None) or global_setting.get_setting("port", None)
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | {self.name}校准：主流程完成，准备结束ZOS校准态",
            title=self.title
        )
        AsyPromise(self.force_close_zos_zero_or_span_valve, port=port).then(
            lambda _: _patched_finish_calibration_core_v2(self, resolve, reject)
        ).catch(lambda e: reject(e))
        return

    _patched_finish_calibration_core_v2(self, resolve, reject)


def _patched_is_ufc_started_for_zero_v2(self):
    return bool(global_setting.get_setting("ufc_start_time_state", False))


def _patched_start_ufc_if_needed_for_zero_v2(self, resolve, reject, port):
    if self.is_STOP:
        reject("stop")
        return

    if False and self.is_ufc_started_for_zero():
        self.ufc_started_by_calibration = False
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | {self.name}校准：当前UFC已开启，跳过启动指令",
            title=self.title
        )
        resolve()
        return

    self.send_message = {
        'port': port,
        'data': number_util.set_int_to_4_bytes_list("000B00FF"),
        'slave_id': '2',
        'function_code': '5',
        'timeout': 1
    }
    self.update_status_main_signal_gui_update.send(
        f"{time_util.get_format_from_time(time.time())} | {self.name}校准：启动UFC",
        title=self.title
    )
    self.send_thread.send_message = self.send_message

    def _send_success(_):
        global_setting.set_setting("ufc_start_time_state", True)
        self.ufc_started_by_calibration = True
        resolve()

    AsyPromise(self.send_thread.Send).then(
        _send_success
    ).catch(lambda e: reject(e))


def _patched_open_gas_pump_and_flow_for_zero_v2(self, resolve, reject, port):
    if self.is_STOP:
        reject("stop")
        return

    self.send_message = {
        'port': port,
        'data': number_util.set_int_to_4_bytes_list("000A00FF"),
        'slave_id': '2',
        'function_code': '5',
        'timeout': 1
    }
    self.update_status_main_signal_gui_update.send(
        f"{time_util.get_format_from_time(time.time())} | {self.name}校准：开启气泵及设定流量控制器",
        title=self.title
    )
    self.send_thread.send_message = self.send_message
    AsyPromise(self.send_thread.Send).then(
        lambda _: resolve()
    ).catch(lambda e: reject(e))


def _patched_wait_one_minute_for_zero_v2(self, resolve, reject, step_desc):
    if self.is_STOP:
        reject("stop")
        return

    ufc_config = global_setting.get_setting("UFC_UGC_ZOS_config")['UFC']
    wait_time = float(ufc_config.get('wait_time', 60))
    wait_time_delay = float(ufc_config.get('wait_time_delay', 1))
    waited = 0.0

    while waited < wait_time:
        if self.is_STOP:
            reject("stop")
            return

        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | {self.name}校准：{step_desc}，当前等待 {int(waited)}/{int(wait_time)} 秒",
            title=self.title
        )
        time.sleep(wait_time_delay)
        waited += wait_time_delay

    resolve()


def _patched_close_gas_pump_and_flow_for_zero_v2(self, resolve, reject, port):
    if self.is_STOP:
        reject("stop")
        return

    self.send_message = {
        'port': port,
        'data': number_util.set_int_to_4_bytes_list("000A0000"),
        'slave_id': '2',
        'function_code': '5',
        'timeout': 1
    }
    self.update_status_main_signal_gui_update.send(
        f"{time_util.get_format_from_time(time.time())} | {self.name}校准：关闭气泵及设定流量控制器",
        title=self.title
    )
    self.send_thread.send_message = self.send_message
    AsyPromise(self.send_thread.Send).then(
        lambda _: resolve()
    ).catch(lambda e: reject(e))


def _patched_close_ufc_if_open_for_zero_v2(self, resolve, reject, port):
    if self.is_STOP:
        reject("stop")
        return

    if not getattr(self, "ufc_started_by_calibration", False):
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | {self.name}校准：本次标定未启动UFC，跳过关闭指令",
            title=self.title
        )
        resolve()
        return

    self.send_message = {
        'port': port,
        'data': number_util.set_int_to_4_bytes_list("000B0000"),
        'slave_id': '2',
        'function_code': '5',
        'timeout': 1
    }
    self.update_status_main_signal_gui_update.send(
        f"{time_util.get_format_from_time(time.time())} | {self.name}校准：关闭UFC",
        title=self.title
    )
    self.send_thread.send_message = self.send_message

    def _send_success(_):
        global_setting.set_setting("ufc_start_time_state", False)
        self.ufc_started_by_calibration = False
        resolve()

    AsyPromise(self.send_thread.Send).then(
        _send_success
    ).catch(lambda e: reject(e))


def _patched_zero_finalize_flow_v2(self, resolve, reject, port):
    AsyPromise(self.close_gas_pump_and_flow_for_zero, port=port).then(
        lambda _: AsyPromise(self.wait_one_minute_for_zero, step_desc="关闭气泵后等待一分钟").then(
            lambda __: AsyPromise(self.close_ugc_zero_or_span_valve, port=port).then(
                lambda ___: (
                    setattr(self, "defer_zos_close_until_finalize", False),
                    AsyPromise(self.close_zos_zero_or_span_valve, port=port).then(
                        lambda ____: AsyPromise(self.close_ufc_if_open_for_zero, port=port).then(
                            lambda _____: AsyPromise(self.finish_calibration, port=port).then(
                                lambda ______: resolve()
                            ).catch(lambda e: reject(e))
                        ).catch(lambda e: reject(e))
                    ).catch(lambda e: reject(e))
                )[1]
            ).catch(lambda e: reject(e))
        ).catch(lambda e: reject(e))
    ).catch(lambda e: reject(e))


def _patched_span_finalize_flow_v2(self, resolve, reject, port):
    AsyPromise(self.close_gas_pump_and_flow_for_zero, port=port).then(
        lambda _: AsyPromise(self.wait_one_minute_for_zero, step_desc="关闭气泵后等待一分钟").then(
            lambda __: AsyPromise(self.close_ugc_zero_or_span_valve, port=port).then(
                lambda ___: (
                    setattr(self, "defer_zos_close_until_finalize", False),
                    AsyPromise(self.close_zos_zero_or_span_valve, port=port).then(
                        lambda ____: AsyPromise(self.close_ufc_if_open_for_zero, port=port).then(
                            lambda _____: AsyPromise(self.finish_calibration, port=port).then(
                                lambda ______: resolve()
                            ).catch(lambda e: reject(e))
                        ).catch(lambda e: reject(e))
                    ).catch(lambda e: reject(e))
                )[1]
            ).catch(lambda e: reject(e))
        ).catch(lambda e: reject(e))
    ).catch(lambda e: reject(e))


def _patched_zero_dosomething_v2(self):
    self.ufc_started_by_calibration = False
    self.defer_zos_close_until_finalize = True
    self.update_status_main_signal_gui_update.send(
        f"{time_util.get_format_from_time(time.time())} | 零点标定前关闭reference气电磁阀（空气阀）已临时跳过",
        title=self.title
    )
    AsyPromise(self.open_ugc_zero_or_span_valve, port=self.port).then(
        lambda _: AsyPromise(self.open_zos_zero_or_span_valve, port=self.port).then(
            lambda __: AsyPromise(self.start_ufc_if_needed_for_zero, port=self.port).then(
                lambda ___: AsyPromise(self.open_gas_pump_and_flow_for_zero, port=self.port).then(
                    lambda ____: AsyPromise(self.wait_one_minute_for_zero, step_desc="开启气泵后等待一分钟").then(
                        lambda _____: AsyPromise(self.cyclic_sampling_of_zos_oxygen_sensor, port=self.port).then(
                            lambda ______: AsyPromise(self.cyclic_sampling_of_ugc_carbon_sensor, port=self.port).then(
                                lambda _______: AsyPromise(self.zero_finalize_flow, port=self.port).then(
                                    lambda __________: self.stop()
                                ).catch(lambda e: self.stop())
                            ).catch(lambda e: self.stop())
                        ).catch(lambda e: self.stop())
                    ).catch(lambda e: self.stop())
                ).catch(lambda e: self.stop())
            ).catch(lambda e: self.stop())
        ).catch(lambda e: self.stop())
    ).catch(lambda e: self.stop())


def _patched_range_dosomething_v2(self):
    self.ufc_started_by_calibration = False
    self.defer_zos_close_until_finalize = True
    AsyPromise(self.open_ugc_zero_or_span_valve, port=self.port).then(
        lambda _: AsyPromise(self.open_zos_zero_or_span_valve, port=self.port).then(
            lambda __: AsyPromise(self.start_ufc_if_needed_for_zero, port=self.port).then(
                lambda ___: AsyPromise(self.open_gas_pump_and_flow_for_zero, port=self.port).then(
                    lambda ____: AsyPromise(self.wait_one_minute_for_zero, step_desc="开启气泵后等待一分钟").then(
                        lambda _____: AsyPromise(self.cyclic_sampling_of_zos_oxygen_sensor, port=self.port).then(
                            lambda ______: AsyPromise(self.set_ugc_standard_gas_co2, port=self.port).then(
                                lambda _______: AsyPromise(self.cyclic_sampling_of_ugc_carbon_sensor, port=self.port).then(
                                    lambda __________: AsyPromise(self.span_finalize_flow, port=self.port).then(
                                        lambda ___________: self.stop()
                                    ).catch(lambda e: self.stop())
                                ).catch(lambda e: self.stop())
                            ).catch(lambda e: self.stop())
                        ).catch(lambda e: self.stop())
                    ).catch(lambda e: self.stop())
                ).catch(lambda e: self.stop())
            ).catch(lambda e: self.stop())
        ).catch(lambda e: self.stop())
    ).catch(lambda e: self.stop())


Gas_Carlibration._normalize_co2_to_ppm = _patched_normalize_co2_to_ppm
Gas_Carlibration._normalize_oxygen_percent = _patched_normalize_oxygen_percent
Gas_Carlibration._read_zos_channel_snapshot = _patched_read_zos_channel_snapshot
Gas_Carlibration._calculate_compensated_co2_ppm = _patched_calculate_compensated_co2_ppm
Gas_Carlibration.finish_calibration = _patched_finish_calibration_v2
Gas_Carlibration.close_ugc_zero_or_span_valve = _patched_close_ugc_zero_or_span_valve
Gas_Carlibration.force_close_zos_zero_or_span_valve = _patched_force_close_zos_zero_or_span_valve_v2
Gas_Carlibration.close_zos_zero_or_span_valve = _patched_close_zos_zero_or_span_valve_v2
Gas_Carlibration.is_ufc_started_for_zero = _patched_is_ufc_started_for_zero_v2
Gas_Carlibration.start_ufc_if_needed_for_zero = _patched_start_ufc_if_needed_for_zero_v2
Gas_Carlibration.open_gas_pump_and_flow_for_zero = _patched_open_gas_pump_and_flow_for_zero_v2
Gas_Carlibration.wait_one_minute_for_zero = _patched_wait_one_minute_for_zero_v2
Gas_Carlibration.close_gas_pump_and_flow_for_zero = _patched_close_gas_pump_and_flow_for_zero_v2
Gas_Carlibration.close_ufc_if_open_for_zero = _patched_close_ufc_if_open_for_zero_v2
Gas_Carlibration.zero_finalize_flow = _patched_zero_finalize_flow_v2
Gas_Carlibration.span_finalize_flow = _patched_span_finalize_flow_v2
Zero_Carlibration.dosomething = _patched_zero_dosomething_v2
Zero_Carlibration.cyclic_sampling_of_ugc_carbon_sensor = _patched_zero_cyclic_sampling_of_ugc_carbon_sensor
Range_Carlibration.dosomething = _patched_range_dosomething_v2
Range_Carlibration.cyclic_sampling_of_ugc_carbon_sensor = _patched_range_cyclic_sampling_of_ugc_carbon_sensor
