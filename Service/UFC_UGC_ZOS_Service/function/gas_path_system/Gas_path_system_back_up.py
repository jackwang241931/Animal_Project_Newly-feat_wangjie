import abc

import re
import threading

import time
from datetime import datetime
from threading import Event, Barrier

import numpy as np
from blinker.base import _PNamespaceSignal
from loguru import logger


from Service.UFC_UGC_ZOS_Service.function.Send_Message.Send_Message import Send_Message
from Service.UFC_UGC_ZOS_Service.function.prdictor.o2_steady_predictor import predict_steady_o2

from public.config_class.global_setting import global_setting
from public.entity.MyQThread import MyQThread
from public.entity.barrier.ActionCompleteBarrier import ActionCompleteBarrier
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.function.Monitor_data_storage.DataStorage import store_data_with_result
from public.function.promise.AsyPromise import AsyPromise
from public.util.number_util import number_util
from public.util.string_util import String_util
from public.util.time_util import time_util
# 等待ufc启动完
wait_UFC_start_finish_event = threading.Event()
# 等待ufc 停止完
wait_UFC_stop_finish_event = threading.Event()
logger = logger.bind(category="monitor_data_logger")
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
        # 1.设定运行鼠笼（默认8个鼠笼都运行）
        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！")
            return
        # mouse_cages_2byte_str: str = global_setting.get_setting("mouse_cages_2byte_str", "11111111")

        # self.send_message = {
        #     'port': port,
        #     'data': number_util.set_int_to_4_bytes_list(str(int(mouse_cages_2byte_str, 2))),
        #     'slave_id': '2',
        #     'function_code': '6',
        #     'timeout': 1
        # }
        # self.update_status_main_signal_gui_update.send(
        #     f"{time_util.get_format_from_time(time.time())} | UFC 启动-1.设定运行鼠笼")
        # self.send_thread.send_message = self.send_message
        # AsyPromise(self.send_thread.Send).then(
        #     # 2UFC启动
        #     lambda r: AsyPromise(self.ufc_start).then(
        #         self.stop()
        #     )
        # ).catch(lambda e: logger.error(f"{e}"))
        AsyPromise(self.ufc_start).then(

        ).catch(lambda e: logger.error(f"{e}"))
        self.stop()
        pass
        pass

    def ufc_start(self, resolve, reject):
        self.is_stop=False
        time.sleep(0.01)
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UFC 启动-2.UFC启动")
        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！")
            reject()
        # 2 UFC 启动
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("000b00ff"),
            'slave_id': '2',
            'function_code': '5',
            'timeout': 1
        }
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            # 3气泵和流量控制器开启
            lambda r: AsyPromise(self.gas_and_flow_rate_start)
        ).catch(lambda e: reject(e))
        pass

    def gas_and_flow_rate_start(self, resolve, reject):
        if self.is_stop:
            reject("Stop")
        time.sleep(0.01)
        # 3气泵和流量控制器开启
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UFC 启动-3.气泵和流量控制器开启")
        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！")
            reject()
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("000a00ff"),
            'slave_id': '2',
            'function_code': '5',
            'timeout': 1
        }
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            lambda r:AsyPromise(self.wait_flow_config_auto_config).then(
                lambda r:resolve(r)
            ).catch(lambda e: reject(e))
        ).catch(lambda e: reject(e))


        pass
        pass
    def wait_flow_config_auto_config(self,resolve, reject):
        """
        UFC-启动 3.1 等待气泵和流量控制器开启  一般60秒
        :param resolve:
        :param reject:
        :return:
        """
        if self.is_stop:
            reject("Stop")
        # 等待时间
        time_index = 0
        while time_index < float(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['wait_time']):
            if self.is_stop:
                reject("Stop")
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | UFC-启动 3.1 等待气泵和流量控制器开启，此过程需{time_index}/{float(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['wait_time'])}秒，等待流量控制器自动配置及运行")
            time_index += 1
            time.sleep(float(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['wait_time_delay']))
        AsyPromise(self.finsh_start).then(
            lambda r: resolve(r)
        ).catch(lambda e: reject(e))

    def finsh_start(self,resolve, reject):
        if self.is_stop:
            reject("Stop")
        self.parent_class.ufc_start_time_state = True
        # logger.critical(f"ufc_finish_start:{self.parent_class.ufc_start_time_state}")
        # 释放 正在等待ufc启动的地方
        global wait_UFC_start_finish_event
        wait_UFC_start_finish_event.set()
        wait_UFC_start_finish_event.clear()

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
        super().__init__(name=name)
    def before_Runing_work(self):
        pass
    def dosomething(self):
        queue = global_setting.get_setting("queue", None)

        # 1.关闭正在运行的鼠笼
        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！")

            return
        mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
        if mouse_cages_inc is not None and len(mouse_cages_inc) > 0:
            for addr in mouse_cages_inc:
                self.send_message = {
                    'port': port,
                    'data': number_util.set_int_to_4_bytes_list(f"000{addr-1}0000"),
                    'slave_id': '2',
                    'function_code': '5',
                    'timeout': 1
                }
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} | UFC-停止 1.关闭{addr}号鼠笼气路")
                if queue:
                    queue.put(
                        ObjectQueueItem(origin="Gas_path_system", to="MainWindow_index",
                                        title="stop_show_info_except_status_counts",
                                        data=f"{time_util.get_format_from_time(time.time())} | UFC-停止 1.关闭{addr }号鼠笼气路",
                                        time=time_util.get_format_from_time(time.time())))
                self.send_thread.send_message = self.send_message
                AsyPromise(self.send_thread.Send).then()
                # self.send_thread.Send_no_promise()
            else:
                # 1.关闭参考气
                self.send_message = {
                    'port': port,
                    'data': number_util.set_int_to_4_bytes_list(f"00080000"),
                    'slave_id': '2',
                    'function_code': '5',
                    'timeout': 1
                }
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} | UFC-停止 1.关闭参考气")
                if queue:
                    queue.put(
                        ObjectQueueItem(origin="Gas_path_system", to="MainWindow_index",
                                        title="stop_show_info_except_status_counts",
                                        data=f"{time_util.get_format_from_time(time.time())} | UFC-停止 1.关闭参考气",
                                        time=time_util.get_format_from_time(time.time())))
                self.send_thread.send_message = self.send_message
                AsyPromise(self.send_thread.Send).then(
                    lambda r: AsyPromise(self.close_ZOS_valve, port=port)
                )
        self.stop()
        pass
        pass

    def close_ZOS_valve(self,resolve,reject,port):
        """2.关闭zos采样阀门"""
        time.sleep(0.01)
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"00090000"),
            'slave_id': '2',
            'function_code': '5',
            'timeout': 1
        }
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UFC-停止 2.关闭zos采样阀门")
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(
                ObjectQueueItem(origin="Gas_path_system", to="MainWindow_index",
                                title="stop_show_info_except_status_counts",
                                data=f"{time_util.get_format_from_time(time.time())} | UFC-停止 2.关闭zos采样阀门",
                                time=time_util.get_format_from_time(time.time())))
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            lambda r: AsyPromise(self.close_Gas_flow_rate_valve, port=port), resolve()
        )
    def close_Gas_flow_rate_valve(self,resolve,reject,port):
        """3.气泵及设定鼠笼流量控制器关闭"""
        time.sleep(0.01)
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"000A0000"),
            'slave_id': '2',
            'function_code': '5',
            'timeout': 1
        }
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UFC-停止 3.气泵及设定鼠笼流量控制器关闭")
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(
                ObjectQueueItem(origin="Gas_path_system", to="MainWindow_index",
                                title="stop_show_info_except_status_counts",
                                data=f"{time_util.get_format_from_time(time.time())} | UFC-停止 3.气泵及设定鼠笼流量控制器关闭",
                                time=time_util.get_format_from_time(time.time())))
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            lambda r: AsyPromise(self.close_UFC_valve, port=port), resolve()
        )
    def close_UFC_valve(self,resolve,reject,port):
        """4.UFC阀门关闭 让步骤3延迟1分钟在关闭"""
        queue = global_setting.get_setting("queue", None)
        # 等待时间
        time_index = 0
        while time_index < float(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['wait_time']):
            if queue:
                queue.put(
                    ObjectQueueItem(origin="Gas_path_system", to="MainWindow_index", title="stop_show_info_except_status_counts",
                                    data=f"{time_util.get_format_from_time(time.time())} | UFC-停止 3.1 等待气泵及设定鼠笼流量控制器关闭，此过程需{time_index}/{float(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['wait_time'])}秒，等待流量控制器自动关闭",
                                    time=time_util.get_format_from_time(time.time())))
            time_index += 1
            time.sleep(float(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['wait_time_delay']))
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"000B0000"),
            'slave_id': '2',
            'function_code': '5',
            'timeout': 1
        }
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UFC-停止 4.UFC阀门关闭")
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(
                ObjectQueueItem(origin="Gas_path_system", to="MainWindow_index",
                                title="stop_show_info_except_status_counts",
                                data=f"{time_util.get_format_from_time(time.time())} | UFC-停止 4.UFC阀门关闭",
                                time=time_util.get_format_from_time(time.time())))
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            lambda _:AsyPromise(self.finish_close).then(
                lambda r:resolve()
            ).catch(lambda e: logger.error(f"{e}"))
        ).catch(lambda e: logger.error(f"{e}"))

    def finish_close(self,resolve,reject):
        # 释放 正在等待ufc启动的地方
        global wait_UFC_stop_finish_event
        wait_UFC_stop_finish_event.set()
        wait_UFC_stop_finish_event.clear()
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

        mouse_cages_inc:list=global_setting.get_setting("mouse_cages",None)
        if mouse_cages_inc is not None and len(mouse_cages_inc) > 0:
            port = global_setting.get_setting("port", None)
            if port is None:
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} | UFC运行失败，未选择串口！")
                logger.error("UFC运行失败，未选择串口！")
                AsyPromise(self.finsh_one_batch, port=None, mouse_cages_inc=mouse_cages_inc)  .then(

                ).catch(lambda e: logger.error(f"{e}"))
                return
            # 从我们之前选择的运行鼠笼拿出来 每次循环访问一个
            AsyPromise(self.switch_mouse_cage_gas_UFC,port=port,mouse_cages_inc=mouse_cages_inc).then(

            ).catch(lambda e: logger.error(f"{e}"))
            pass
        else:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | UFC运行失败，未选择实例化实验设置的mouse_cages！")
            logger.error("UFC运行失败，未选择实例化实验设置的mouse_cages！")
            AsyPromise(self.finsh_one_batch,port=None, mouse_cages_inc=mouse_cages_inc).then(

            ).catch(lambda e: logger.error(f"{e}"))



        pass
    def switch_mouse_cage_gas_UFC(self,resolve,reject,port,mouse_cages_inc):
        """
        UFC切换鼠笼气路
        :param resolve:
        :param reject:
        :param port:
        :param mouse_cages_inc:
        :return:
        """
        mouse_cage_index = global_setting.get_setting("cage_number_list_index",None)
        logger.critical(f"mouse_cages_inc:{mouse_cages_inc},mouse_cage_index:{mouse_cage_index}")
        if mouse_cage_index is not None:
            mouse_cage_number_addr_single = mouse_cages_inc[mouse_cage_index]-1
        else:
            # 下标为None 则为参考气
            mouse_cage_number_addr_single=8
        # 1 切换x号鼠笼

        time.sleep(0.01)
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"000{mouse_cage_number_addr_single}00ff"),
            'slave_id': '2',
            'function_code': '5',
            'timeout': 1
        }

        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UFC-运行 1. UFC切换{str(mouse_cage_number_addr_single ) + '号鼠笼' if mouse_cage_number_addr_single !=8 else global_setting.get_setting('configer')['mouse_cage']['reference'] + '(参考气)'}")
        self.send_thread.send_message = self.send_message
        # UFC切换指令发出后立即通知UGC/ZOS可以开始（它们通过指令中的鼠笼号自行切换）
        wait_UFC_run_finish_event = global_setting.get_setting("wait_UFC_run_finish_event", None)
        if wait_UFC_run_finish_event:
            wait_UFC_run_finish_event.set()
            wait_UFC_run_finish_event.clear()
        AsyPromise(self.send_thread.Send).then(
            lambda r: AsyPromise(self.close_last_mouse_cage_gas_UFC, port=port, mouse_cages_inc=mouse_cages_inc).then(
                lambda r: resolve()
            ).catch(lambda e: logger.error(f"{e}"))
         ).catch(lambda e: logger.error(f"{e}"))

    def switch_mouse_cage_gas_by_zos_start(self, resolve, reject, port, mouse_cages_inc):
        """
        ZOS 切换鼠笼气路
        :param resolve:
        :param reject:
        :param port:
        :param mouse_cages_inc:
        :return:
        """
        mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
        if mouse_cage_index is not None:
            mouse_cage_number_addr_single = mouse_cages_inc[mouse_cage_index] - 1
        else:
            # 下标为None 则为参考气
            mouse_cage_number_addr_single = 8
        # 1 切换x号鼠笼

        time.sleep(0.01)
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"000{mouse_cage_number_addr_single}ff00"),
            'slave_id': '4',
            'function_code': '5',
            'timeout': 1
        }

        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UFC-运行  1.1 ZOS切换鼠笼气路 切换{str(mouse_cage_number_addr_single+1) + '号鼠笼' if mouse_cage_number_addr_single != 8 else global_setting.get_setting('configer')['mouse_cage']['reference'] + '(参考气)'}")
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            # 2. 关闭ufc上个鼠笼的气路或者关闭参考气
            lambda r: AsyPromise(self.close_last_mouse_cage_gas_UFC, port=port, mouse_cages_inc=mouse_cages_inc).then(
                lambda r: resolve()
            ).catch(lambda e: logger.error(f"{e}"))
        ).catch(lambda e: logger.error(f"{e}"))
    def close_last_mouse_cage_gas_UFC(self,resolve,reject,port,mouse_cages_inc):
        """
         UFC 2. ufc关闭上个鼠笼的气路或者关闭参考气
        如果i≠0，关闭i-1号，i++；
        如果i=0，关闭8号，i++
        :param resolve:
        :param reject:
        :param port:
        :return:
        """
        time.sleep(0.01)
        mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
        # 当前为参考气 则关闭最后一个鼠笼
        if mouse_cage_index is None :
            mouse_cage_number_addr_single = mouse_cages_inc[len(mouse_cages_inc) - 1 ]-1
        elif mouse_cage_index==0:
        #当前为鼠笼列表的第一个鼠笼 则关闭参考气
            mouse_cage_number_addr_single=8
        else:
            mouse_cage_number_addr_single=mouse_cages_inc[mouse_cage_index-1]-1
        #2.关闭上个鼠笼号，如果当前鼠笼号为0，则关闭参考气体
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UFC-运行 2. 关闭UFC{str(mouse_cage_number_addr_single )+'号鼠笼' if mouse_cage_number_addr_single!=8 else '参考气'}")


        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"000{mouse_cage_number_addr_single}0000"),
            'slave_id': '2',
            'function_code': '5',
            'timeout': 1
        }

        self.send_thread.send_message = self.send_message
        self.send_thread.Send_no_promise()
        # 继续到读流量值
        AsyPromise(self.read_flow_rate_value_circulation, port=port, mouse_cages_inc=mouse_cages_inc).then(
            lambda r: resolve()
        ).catch(lambda e: logger.error(f"{e}"))

        pass
    def close_last_mouse_cage_gas_by_zos_start(self,resolve,reject,port,mouse_cages_inc):
        """
       UFC 2.1 ZOS关闭上个鼠笼的气路或者关闭参考气
        如果i≠0，关闭i-1号，i++；
        如果i=0，关闭8号，i++
        :param mouse_cages_inc:
        :param resolve:
        :param reject:
        :param port:
        :return:
        """
        time.sleep(0.01)
        mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
        # 当前为参考气 则关闭最后一个鼠笼
        if mouse_cage_index is None :
            mouse_cage_number_addr_single = mouse_cages_inc[len(mouse_cages_inc) - 1 ]-1
        elif mouse_cage_index==0:
        #当前为鼠笼列表的第一个鼠笼 则关闭参考气
            mouse_cage_number_addr_single=8
        else:
            mouse_cage_number_addr_single=mouse_cages_inc[mouse_cage_index-1]-1
        #2.关闭上个鼠笼号，如果当前鼠笼号为0，则关闭参考气体
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UFC-运行 2.1 关闭ZOS{str(mouse_cage_number_addr_single+1) + '号鼠笼' if mouse_cage_number_addr_single != 8 else '参考气'}")

        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"000{mouse_cage_number_addr_single}0000"),
            'slave_id': '4',
            'function_code': '5',
            'timeout': 1
        }

        self.send_thread.send_message = self.send_message
        self.send_thread.Send_no_promise()
        # 3 循环读取流量值 （推荐每2秒读取一次）（原定为15秒）
        AsyPromise(self.read_flow_rate_value_circulation, port=port, mouse_cages_inc=mouse_cages_inc).then(
            lambda r: resolve()
        ).catch(lambda e: logger.error(f"{e}"))


    def read_flow_rate_value_circulation(self,resolve,reject,port,mouse_cages_inc):
        """
        循环读取流量值 （推荐每2秒读取一次）（原定为15秒）
        """
        time.sleep(int(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['run_time']))
        # 让ugc开始运行
        # wait_UFC_run_finish_event = global_setting.get_setting("wait_UFC_run_finish_event", None)
        # if wait_UFC_run_finish_event:
        #     wait_UFC_run_finish_event.set()
        #     wait_UFC_run_finish_event.clear()
        mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)

        # 3 循环读取流量值 （推荐每2秒读取一次）（原定为15秒） ！弃用
        index = 0
        # while (index< int(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['run_time'])):
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"00000006"),
            'slave_id': '2',
            'function_code': '4',
            'timeout': 1
        }
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UFC-运行 3. 循环读取流量值（推荐每{int(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['run_time_delay'])}秒读取一次），当前{index}s/{int(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['run_time'])}s")
        self.send_thread.send_message = self.send_message
        result_data,message = self.send_thread.Send_no_promise()


        result_data['time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        result_data['mouse_cage_number']= mouse_cages_inc[mouse_cage_index] if mouse_cage_index is not None else int(global_setting.get_setting('configer')['mouse_cage']['reference'])
        logger.error(f"---------------鼠笼气路值：{result_data}")
        result = store_data_with_result(result_data, need_result=True, timeout=5)
        if result and result.success:
            logger.info(f"数据存储成功，ID: {result.item_id}")
        else:
            logger.error(f"数据存储失败: {result.error if result else '未知错误'}")
        # index+=int(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['run_time_delay'])
        # time.sleep(float(global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['run_time_delay']))
#       #while end
#       #等待60秒 ！弃用
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
        barrier = global_setting.get_setting("barrier")
        if barrier is not None:
            logger.debug(f"barrier_UFC run one batch done ! ")
            barrier.wait()
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

        self.ufc_gas_path_system_start_thread.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update
        self.ufc_gas_path_system_start_thread.start()
        # 等待开始线程 把ufc启动完成
        global wait_UFC_start_finish_event
        wait_UFC_start_finish_event.wait()
        resolve()

    """start end"""

    """run start"""
    def run(self,resolve,reject):
        """
        气路运行
        :return:
        """
        time.sleep(0.01)
        self.update_status_main_signal_gui_update.send(f"{time_util.get_format_from_time(time.time())} | UFC 开始运行{'.'*100}")

        AsyPromise(self.open_zos_valve).then(lambda r:resolve(r)).catch(lambda e: logger.error(f"{e}"))
        pass
    def run_no_circulation_read(self,resolve,reject):
        """
        气路运行不读取数据
        :param resolve:
        :param reject:
        :return:
        """
        time.sleep(0.01)
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UFC 开始运行(不读取数据){'.' * 100}")

        AsyPromise(self.open_zos_valve_no_circulation_read).then(lambda r: resolve(r)).catch(
            lambda e: logger.error(f"{e}"))
    def open_zos_valve_no_circulation_read(self,resolve, reject):
        #  UFC-运行 0）打开ZOS采样阀
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UFC-运行(不读取数据) 0）打开ZOS采样阀")
        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！")
            reject()
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("000900ff"),
            'slave_id': '2',
            'function_code': '5',
            'timeout': 1
        }
        self.send_thread.send_message = self.send_message

        AsyPromise(self.send_thread.Send).then(
            lambda r: resolve(r)
        ).catch(lambda e: logger.error(f"{e}"))
    def open_zos_valve(self,resolve, reject):
        #  UFC-运行 0）打开ZOS采样阀
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UFC-运行 0）打开ZOS采样阀")
        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！")
            reject()
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("000900ff"),
            'slave_id': '2',
            'function_code': '5',
            'timeout': 1
        }
        self.send_thread.send_message = self.send_message

        AsyPromise(self.send_thread.Send).then(
            lambda _:AsyPromise(self.circular_running).then(lambda r: resolve(r)).catch(lambda e: logger.error(f"{e}"))
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
        self.ufc_gas_path_system_close_thread.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update
        self.ufc_gas_path_system_close_thread.start()
        # 等待开始线程 把ufc停止完成
        global wait_UFC_stop_finish_event
        wait_UFC_stop_finish_event.wait()
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
        super().__init__(name=name)
    def before_Runing_work(self):
        pass
    def dosomething(self):
        wait_UFC_run_finish_event=global_setting.get_setting("wait_UFC_run_finish_event",None )
        if wait_UFC_run_finish_event:
            # 阻塞 等待ufc运行完在运行
            wait_UFC_run_finish_event.wait()
        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！")
            return
        #3.循环读取CO2浓度
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
        result_data, message = self.send_thread.Send_no_promise()
        logger.error(f"ugc:{result_data}")
        datas = result_data.get("data")
        # 数据不为TIME OUT 就进行补偿
        if datas and len(datas)>1 :
            co2 =None
            for data in datas:
                desc = data.get("desc")
                if desc and desc=="气压(KPa)":
                    # 获得气压的数据
                    air_pressure =float(data.get("value"))
                    global_setting.set_setting("air_pressure", air_pressure)

                elif desc and desc =="CO2(%)" :
                    # 获得co2的数据
                    co2 = data.get("value")


            # 进行压力补偿
            air_pressure=global_setting.get_setting("air_pressure", None)
            # 如果没有压力数据则补偿前后数据一样
            result_data["data"].append({"desc": "补偿前CO2(%)", "value": co2})
            if air_pressure is not None:
                # 当前co2数值/（1104测出来的气压值（没有就为标准大气压值）+当前读出大气压值） =co2补偿/标准大气压值 =》 co2补偿=（标准大气压值*当前co2数值）/（1104测出来的气压值（没有就为标准大气压值）+当前读出大气压值）
                co2_compensation=(float(global_setting.get_setting("UFC_UGC_ZOS_config")['PARAM']['standard_atmospheric_pressure'])*co2)/(global_setting.get_setting("air_pressure_1104",None) if global_setting.get_setting("air_pressure_1104",None) is not None else float(global_setting.get_setting("UFC_UGC_ZOS_config")['PARAM']['standard_atmospheric_pressure'])  +air_pressure)
                for i in range(len(result_data["data"])):
                    if result_data['data'][i]['desc']=="CO2(%)":
                        result_data['data'][i]['value'] = co2_compensation
                        break
                pass

        result_data['time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        result_data['mouse_cage_number'] =mouse_cages_inc[mouse_cage_index] if mouse_cage_index is not  None else int(global_setting.get_setting('configer')['mouse_cage']['reference'])
        result = store_data_with_result(result_data, need_result=True, timeout=5)
        if result and result.success:
            logger.info(f"数据存储成功，ID: {result.item_id}")
        else:
            logger.error(f"数据存储失败: {result.error if result else '未知错误'}")

        #通知zos 运行
        wait_UGC_run_finish_event=global_setting.get_setting("wait_UGC_run_finish_event",None )
        if wait_UGC_run_finish_event:
            wait_UGC_run_finish_event.set()
            wait_UGC_run_finish_event.clear()

        # # 让鼠笼内的传感器开始运行
        # ufc_ugc_zos_barrier = global_setting.get_setting("ufc_ugc_zos_barrier")
        # if ufc_ugc_zos_barrier is not None:
        #     logger.debug(f"ufc_ugc_zos_barrier_UGC run one batch done ! ")
        #     ufc_ugc_zos_barrier.wait()
        barrier = global_setting.get_setting("barrier")
        if barrier is not None:
            logger.debug(f"barrier_UGC run one batch done ! ")
            barrier.wait()
        time.sleep(float(global_setting.get_setting('UFC_UGC_ZOS_config')['UGC']['run_time_delay']))
class UGC_gas_path_system(Gas_path_system):
    """
    UGC 气路系统
    """
    # UgC开始的操作数
    process_nums =1
    def __init__(self):
        super().__init__()
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
        # 1.读取系统状态
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("00080002"),
            'slave_id': '3',
            'function_code': '2',
            'timeout': 1
        }
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UGC 正在启动-1.读取系统状态")
        self.send_thread.send_message = self.send_message
        result_data,parser_message=self.send_thread.Send_no_promise()
        if result_data is not None:
            datas = result_data.get("data")
            if datas and len(datas) > 1:
                state_value = None
                for data in datas:
                    desc = data.get("desc")
                    if desc and desc == "CO2阀门状态":
                        # 获得气压的数据
                        state_value = int(data.get("value"))
                        if state_value ==0:
                            error_data= "UGC阀门状态：OFF"
                            logger.error(error_data)
                            # reject(error_data)
                        else:
                            ok_data = "UGC阀门状态：ON"
                            logger.info(ok_data)
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
        # 1.鼠笼气电磁阀开(sample 气)(开机默认打开)
        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！")
            reject()
        # 修改命令反了 FF->00，11月1日改回，现和文档一致
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("0000FF00"),
            'slave_id': '3',
            'function_code': '5',
            'timeout': 1
        }
        time.sleep(0.01)
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UGC-运行 1.鼠笼气电磁阀开(sample 气)(开机默认打开)")
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            # 2.开泵抽气（正式开机）
            lambda r: AsyPromise(self.open_valve_remove_gas,port=port).then(lambda r1:resolve()
            ).catch(lambda e: reject(e))
        ).catch(lambda e: reject(e))
        pass
    def close_mouse_cage_valve(self,resolve,reject,port):
        # 2.鼠笼气电磁阀关(sample 气)
        time.sleep(0.01)
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("00000000"),
            'slave_id': '3',
            'function_code': '5',
            'timeout': 1
        }
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UGC-停止 2.鼠笼气电磁阀关(sample 气)")
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            # 3.完成关闭
            lambda r: AsyPromise(self.stop_finished).then(lambda r1:resolve()
            ).catch(lambda e: reject(e))
        ).catch(lambda e: reject(e))

        pass
    def open_valve_remove_gas(self,resolve,reject,port):
        # 2.开泵抽气（正式开机）
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("0004FF00"),
            'slave_id': '3',
            'function_code': '5',
            'timeout': 1
        }
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UGC 运行-2.开泵抽气（正式开机）")
        self.send_thread.send_message = self.send_message

        AsyPromise(self.send_thread.Send).then(
            # 3.循环读取CO2浓度
            lambda r: AsyPromise(self.circular_running).then(lambda r1: resolve()
                                                             ).catch(lambda e: reject(e))
        ).catch(lambda e: reject(e))

    def circular_running(self, resolve, reject):
        # 3.循环读取CO2浓度
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
        气路运行
        :return:
        """
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UGC 开始运行(不读取数据){'.' * 100}")
        # 1.鼠笼气电磁阀开(sample 气)(开机默认打开)
        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！")
            reject()
        # 修改命令反了 FF->00，11月1日改回，现和文档一致
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("0000FF00"),
            'slave_id': '3',
            'function_code': '5',
            'timeout': 1
        }
        time.sleep(0.01)
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UGC-运行(不读取数据) 1.鼠笼气电磁阀开(sample 气)(开机默认打开)")
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            # 2.开泵抽气（正式开机）
            lambda r: AsyPromise(self.open_valve_remove_gas_no_circulation_read, port=port).then(lambda r1: resolve()
                                                                             ).catch(lambda e: reject(e))
        ).catch(lambda e: reject(e))
        pass

    def open_valve_remove_gas_no_circulation_read(self, resolve, reject, port):
        # 2.开泵抽气（正式开机）
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("0004FF00"),
            'slave_id': '3',
            'function_code': '5',
            'timeout': 1
        }
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UGC 运行(不读取数据)-2.开泵抽气（正式开机）")
        self.send_thread.send_message = self.send_message

        AsyPromise(self.send_thread.Send).then(
            lambda r1: resolve()
        ).catch(lambda e: reject(e))
    """
    run_no_circulation_read end
    """
    def stop(self,resolve,reject):
        """
        停止气路
        :return:
        """
        self.update_status_main_signal_gui_update.send(f"{time_util.get_format_from_time(time.time())} | UGC 正在停止{'.'*100}")
        if self.ugc_gas_path_system_run_thread is not None:
            self.ugc_gas_path_system_run_thread.stop()
            self.ugc_gas_path_system_run_thread.deleteLater()
            self.ugc_gas_path_system_run_thread=None
        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！")
            reject()
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("00040000"),
            'slave_id': '3',
            'function_code': '5',
            'timeout': 1
        }
        time.sleep(0.01)
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | UGC-停止 1.停止UGC閥門")
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(
                ObjectQueueItem(origin="Gas_path_system", to="MainWindow_index",
                                title="stop_show_info_except_status_counts",
                                data=f"{time_util.get_format_from_time(time.time())} | UGC-停止 1.停止UGC閥門",
                                time=time_util.get_format_from_time(time.time())))
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            # 2.鼠笼气电磁阀关(sample 气)
            lambda r:AsyPromise(self.close_mouse_cage_valve,port=port).then(lambda r1:resolve()).catch(lambda e: reject(e))
        ).catch(lambda e: reject(e))
        pass

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
        #氧气预测值的预测因子
        self.factor = float(global_setting.get_setting("UFC_UGC_ZOS_config")['ZOS']['factor'])
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

        wait_UGC_run_finish_event = global_setting.get_setting("wait_UGC_run_finish_event", None)
        if wait_UGC_run_finish_event:
            # 阻塞 等待UGC运行完在运行
            wait_UGC_run_finish_event.wait()

        mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
        mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
        if mouse_cages_inc is not None and len(mouse_cages_inc) > 0:
            port = global_setting.get_setting("port", None)
            if port is None:
                self.update_status_main_signal_gui_update.send(
                    f"{time_util.get_format_from_time(time.time())} | ZOS运行失败，未选择串口！")
                logger.error("ZOS运行失败，未选择串口！")
                AsyPromise(self.finsh_one_batch, port=None, mouse_cages_inc=mouse_cages_inc).then(

                ).catch(lambda e: logger.error(f"{e}"))
                return
            # 因为UFC运行的时候同步切换了ZOS的通道，所以到ZOS运行时就不用在切换了，直接读
            AsyPromise(self.circular_read,port=port,mouse_cages_inc=mouse_cages_inc).then(

            ).catch(lambda e: logger.error(f"{e}"))
            pass
        else:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | ZOS运行失败，未选择实例化实验设置的mouse_cages！")
            logger.error("ZOS运行失败，未选择实例化实验设置的mouse_cages！")
            AsyPromise(self.finsh_one_batch,port=None, mouse_cages_inc=mouse_cages_inc).then(

            ).catch(lambda e: logger.error(f"{e}"))







    def circular_read(self,resolve,reject,port,mouse_cages_inc):
        """
        ZOS-运行 1. 循环读氧气值
        :param resolve:
        :param reject:
        :param port:
        :param mouse_cages_inc:
        :return:
        """
        # 等待15秒在读 UFC那边一开始已经等了15秒 已经弃用！！！
        # time.sleep(int(global_setting.get_setting('UFC_UGC_ZOS_config')['ZOS']['run_time']))
        mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | ZOS-运行 1. 循环读取{'鼠笼' + str(mouse_cages_inc[mouse_cage_index]) if mouse_cage_index is not None else '参考气'}的氧浓度")
        # 3.循环读取CO2浓度
        cage_addr = mouse_cages_inc[mouse_cage_index] - 1 if mouse_cage_index is not None else 8
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list(f"000{cage_addr}000A"),
            'slave_id': '4',
            'function_code': '4',
            'timeout': 1
        }
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            # 2.处理15秒的氧气值
            lambda r: AsyPromise(self.handle_oxygen_value, port=port, r=r)
        ).catch(lambda e: logger.error(f"{e}"))
    def handle_oxygen_value(self,resolve,reject,port,r):
        mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
        mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
        #存储值
        result_data = r['data']
        result_data['mouse_cage_number'] = mouse_cages_inc[mouse_cage_index] if mouse_cage_index is not None else int(global_setting.get_setting('configer')['mouse_cage']['reference'])
        result_data['time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        logger.error(f"zos:{result_data}")
        if len(result_data['data'])>0:
            values = [data_struct['value']  for data_struct in result_data['data'] if data_struct['desc']=='预测前氧气传感器测量值(15秒数值,包括压力)(氧气数值,压力数值)']
            flow_nums =[data_struct['value']  for data_struct in result_data['data'] if data_struct['desc']=='流量(sccm)']
            if len(values)>0 and len(flow_nums)>0:
                # # 氧浓度V应校准为（V-Vzero）* K
                # oxygen_and_pressure_values = [(round((float(oxygen) - global_setting.get_setting("Vzero", 0)) * global_setting.get_setting("K", 1), 6), pressure) for oxygen, pressure in values[0]]
                oxygen_and_pressure_values=values[0]
                # 流量计值
                flow_num = flow_nums[0]
                result_data["data"].append({"desc": "流量(sccm)", "value": flow_num})
                result_data["data"].append({"desc": "预测前氧气传感器测量值(15秒数值,包括压力)(氧气数值,压力数值)", "value": str(oxygen_and_pressure_values)})
                # 得到15秒的氧气值和压力值
                oxygen__values, pressure_values = map(list, zip(*oxygen_and_pressure_values))

                if mouse_cage_index is not None:
                    mouse_cage_number_addr_single = mouse_cages_inc[mouse_cage_index] - 1
                    pred = predict_steady_o2(oxygen__values, pressure_values, is_reference=False,
                                                    calibration_factor=self.factor)
                    logger.warning(f'鼠笼{mouse_cage_number_addr_single}的氧气传感器测量值(%)经过校准后得:{pred}，用于计算的预测因子为：{self.factor}')
                else:
                    # 下标为None 则为参考气
                    pred, factor = predict_steady_o2( oxygen__values, pressure_values, is_reference=True)
                    logger.warning(f'参考气的氧气传感器测量值(%)经过校准后得:{pred}，得到的预测因子为：{factor}')
                    # 更新预测因子
                    self.factor = factor

                for i in range(len(result_data['data'])):
                    if result_data['data'][i]['desc'] == '氧气传感器测量值(%)':
                        result_data['data'][i]['value'] =pred
                        break
                else:
                    result_data["data"].append({"desc": "氧气传感器测量值(%)",
                                                "value": pred})

        logger.info(f"result_data:{result_data}")
        result = store_data_with_result(result_data, need_result=True, timeout=5)
        if result and result.success:
            logger.info(f"数据存储成功，ID: {result.item_id}")
        else:
            logger.error(f"数据存储失败: {result.error if result else '未知错误'}")

        AsyPromise(self.finsh_one_batch, port=None, mouse_cages_inc=mouse_cages_inc).then(
            lambda r:resolve()
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
        barrier = global_setting.get_setting("barrier")
        if barrier is not None:
            logger.debug(f"barrier_ZOS run one batch done ! ")
            barrier.wait()
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
    def judge_zos_start_status(self,resolve,reject,r):
        if r is None or r.get('message',"") is None:
            reject("报文响应为 None")
        if self.is_stop:
            reject("Stop")
        if "ZOS状态状态：运行" in r.get('message',""):
            if not self.zos_start_status:
                # 3）ZOS启动 只运行一次
                AsyPromise(self.start_zos).then(
                    lambda r2: resolve()
                ).catch(lambda e: logger.error(f"{e}"))
            self.zos_start_status = True
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | ZOS 启动 2）状态:{'运行' if self.zos_start_status else '停止（预热）'}{r}-end.")

        else:
            self.zos_start_status = False
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | ZOS 启动 2）状态:{'运行' if self.zos_start_status else '停止（预热）'}{r}-end.")
            resolve()
    def start_zos(self,resolve,reject):
        # 3）ZOS启动
        if self.is_stop:
            reject("Stop")
        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！")
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("000BFF00"),
            'slave_id': '4',
            'function_code': '5',
            'timeout': 1
        }
        self.send_thread.send_message = self.send_message
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | 正在启动 ZOS: 3）ZOS 启动...")
        AsyPromise(self.send_thread.Send).then(
            #4) 完成启动
            lambda r: AsyPromise(self.start_success).then(lambda r: resolve()
                                                               ).catch(lambda e: logger.error(f"{e}"))
        ).catch(lambda e: logger.error(f"{e}"))
    def start_success(self,resolve,reject):
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | ZOS 启动完成。")
        self.zos_start_status = True
        if self.is_stop:
            reject("Stop")
        resolve()

    def start(self,resolve,reject):
        """
        启动气路
        :return:
        """


        time.sleep(0.01)
        self.update_status_main_signal_gui_update.send(f"{time_util.get_format_from_time(time.time())} | ZOS 正在启动")
        #1.读取系统状态
        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！")
            reject()
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("1"),
            'slave_id': '4',
            'function_code': '1',
            'timeout': 1
        }

        self.send_thread.send_message = self.send_message
        self.update_status_main_signal_gui_update.send(f"{time_util.get_format_from_time(time.time())} | ZOS 正在启动 1.读取系统状态")
        AsyPromise(self.send_thread.Send).then(
                lambda r:AsyPromise(self.judge_zos_start_status,r=r).then(lambda r:resolve()
            ).catch(lambda e: logger.error(f"{e}"))
        ).catch(lambda e:reject(e))

        pass

    def zos_start_timer_task(self,elapsed_ms):
        #zos启动之后需要预热

        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} | ZOS 正在预热时间为{int(global_setting.get_setting('UFC_UGC_ZOS_config')['ZOS']['start_time'])}s(当前{elapsed_ms//1000}s)，循环判断zos状态是否完成，预热完成进入运行状态-start.")

        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！")
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("1"),
            'slave_id': '4',
            'function_code': '1',
            'timeout': 1
        }
        self.send_thread.send_message = self.send_message
        AsyPromise(self.send_thread.Send).then(
            lambda r: AsyPromise(self.judge_zos_start_status, r=r)
        )
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
        停止气路
        :return:
        """
        self.update_status_main_signal_gui_update.send(f"{time_util.get_format_from_time(time.time())} | ZOS 正在停止{'.'*100}")
        if self.zos_gas_path_system_run_thread is not None:
            self.zos_gas_path_system_run_thread.stop()
            self.zos_gas_path_system_run_thread.deleteLater()
            self.zos_gas_path_system_run_thread=None
        self.is_stop = True
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
