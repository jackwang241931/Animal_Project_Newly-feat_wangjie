import copy

import multiprocessing
import os
import queue
import shutil
import sys
import threading
import time

from datetime import datetime


from PyQt6.QtCore import QThread, QTimer, QCoreApplication

from loguru import logger

from Service.UFC_UGC_ZOS_Service.function.gas_path_system.Gas_path_system import O2CorrectionManager
from Service.UFC_UGC_ZOS_Service.index.UFC_UGC_ZOS_index import UFC_UGC_ZOS_index

from public.config_class import global_load
from public.config_class.global_setting import global_setting

from public.dao.SQLite.Monitor_Datas_Handle import Monitor_Datas_Handle
from public.entity.MyQThread import MyQThread, MyThread
from public.entity.barrier.ActionCompleteBarrier import ActionCompleteBarrier
from public.entity.dict.AdvancedFuzzyDict import FuzzyDict
from public.entity.experiment_setting_entity import Experiment_setting_entity
from public.entity.queue.ObjectQueueItem import ObjectQueueItem

from public.function.Modbus.Modbus_Type import Modbus_Slave_Type, Modbus_Slave_Send_Messages_Senior_Data, Others_Tables
from public.function.Modbus.New_Mod_Bus import ModbusRTUMasterNew
from public.function.Monitor_data_storage.DataStorage import StorageResult, store_data_with_result, DataItem
from public.util.custom_data_file_util import custom_data_file_util
from public.util.number_util import number_util
from public.util.time_util import time_util

# 全局变量
# 实现主线程发一整轮消息，当从线程响应完全部的消息后，主线程在发一整轮消息
MESSAGE_BATCH_SIZE = 0
total_messages_processed = 1
lock = threading.Lock()
batch_complete_event = threading.Event()
#等待气路启动之后在一起运行发送
wait_UFC_UGC_ZOS_start_event = threading.Event()
global_setting.set_setting("wait_UFC_UGC_ZOS_start_event",wait_UFC_UGC_ZOS_start_event)
# 通道
experiment_settings = global_setting.get_setting("experiment_setting",None)
gids = [group.id for group in experiment_settings.groups] if experiment_settings is not None else []
#气路之间也需要顺序run ufc run->ugc run->zos run
wait_UFC_run_finish_event = threading.Event()
wait_UGC_run_finish_event = threading.Event()
wait_ZOS_run_finish_event = threading.Event()
global_setting.set_setting("wait_UFC_run_finish_event",wait_UFC_run_finish_event)
global_setting.set_setting("wait_UGC_run_finish_event",wait_UGC_run_finish_event)
global_setting.set_setting("wait_ZOS_run_finish_event",wait_ZOS_run_finish_event)

#使用端口
port_use=None

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
        if not self.queue.empty():
            # logger.error(f"{self.queue.qsize()}")
            try:
                message: ObjectQueueItem = self.queue.get()
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
                        global port_use
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
                    case 'start':
                        data = message.data
                        if data is not None:
                            global_setting.set_setting("start_experiment_time", data.get("start_experiment_time",time.time()))
                            global_setting.set_setting("pause_experiment_time", data.get("pause_experiment_time",[]))
                            global_setting.set_setting("relieve_pause_experiment_time", data.get("relieve_pause_experiment_time",[]))
                        start()
                    case 'start_zero_calibration':
                        start_zero_calibration()
                        pass
                    case 'start_span_calibration':
                        start_range_calibration()
                        pass
                    case 'start_calibration':
                        start_calibration()
                        pass
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
                            global_setting.set_setting("experiment_setting", data.get("experiment_setting", None))
                            global_setting.set_setting("experiment_setting_file",
                                                       data.get("experiment_setting_file", ""))

                        pass
                    case 'stop_modbus':
                        logger.critical(f"{self.name},stop_modbus")
                        stop_modbus()
                    case _:
                        pass




            else:
                # 把消息放回去
                self.queue.put(message)

        pass


read_queue_data_thread = read_queue_data_Thread(name="main_monitor_data_read_queue_data_thread")

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

    def dosomething(self):
        global store_Q, store_Q_lock
        # 队列中有数据在存储 且接收数据线程存活 才存数据
        if not store_Q.empty():
            data_item:DataItem = None
            try:
                # 加锁
                with store_Q_lock:
                    data_item:DataItem  = store_Q.get()  # 获取DataItem对象
                # 解锁会在with块结束后自动处理
            except queue.Empty:
                logger.error(f"数据队列Q为空，获取数据失败！")
                return

            # logger.info(f"存储数据线程开始存储数据: {data_item.data}")

            # 存储到文件里并获取结果
            success, error = self.store_to_data_base(data_item.data)

            # 发送存储结果
            self._send_storage_result(data_item, success, error)

        time.sleep(float(global_setting.get_setting('monitor_data')['STORAGE']['delay']))

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

            # [新增] 存储前最后检查和修正
            data = self._apply_o2_correction_before_storage(data)

            success,error = self.handle.insert_data(data)


        except Exception as e:
            success = False
            error = str(e)
            logger.error(f"{self.name}存储错误：{e}")

        return success, error
    def _apply_o2_correction_before_storage(self, data):
        """
        存储前对氧气值进行最后检查和修正
        """
        try:
            if isinstance(data, dict):
                # 检查是否是ZOS相关数据
                if data.get('module_name') == 'ZOS' and data.get('data') is not None:
                    for item in data['data']:
                        if isinstance(item, dict):
                            desc = item.get('desc', '')
                            value = item.get('value')

                            # 如果是氧气测量值，进行模型修正
                            if '氧气' in desc and '修正前' not in desc and value is not None:
                                corrected_value = apply_o2_model_correction(value)
                                item['value'] = corrected_value
                                logger.info(f"存储前O2修正: {value} → {corrected_value}")

        except Exception as e:
            logger.error(f"存储前O2修正失败: {e}")

        return data

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
        if self.handle is not None:
            self.handle.stop()
            self.handle = None
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
        # if self.modbus is not None:
        #     self.modbus.close()
        super().stop()

    def set_modbus(self, modbus):
        self.modbus = modbus

    def run(self):
        logger.warning(f"{self.name} thread has been started！")
        self._running=True

        global lock, total_messages_processed, store_Q, store_Q_lock
        global MESSAGE_BATCH_SIZE
        while self._running:
            self.mutex.lock()
            if self._paused:
                self.condition.wait(self.mutex)  # 等待条件变量
            self.mutex.unlock()
            self.priority_queue_empty = False
            self.normal_queue_empty=False
            send_message =None
            try:
                # logger.info(self.send_messages)

                try:
                    with self.priority_queue_lock:
                        message = self.priority_queue.get_nowait()
                    send_message = message['message']

                    logger.debug(f"{self.name}接收到查询报文。正在发送查询报文：{send_message}")
                    response, response_hex, send_state, return_data= self.modbus.send_command(
                        slave_id=send_message['slave_id'],
                        function_code=send_message['function_code'],
                        data_hex_list=send_message['data'],

                        is_parse_response=False
                    )
                    # 响应报文是正确的，即发送状态时正确的 进行解析响应报文

                    if send_state:
                        return_data, parser_message = self.modbus.parse_response(response=response,
                                                                                 response_hex=response.hex(),
                                                                                 send_state=True,
                                                                                 slave_id=
                                                                                 send_message['slave_id'],
                                                                                 function_code=
                                                                                 send_message['function_code'], )

                        # 如果为1104 环境模块 存储大气压值
                        if response.hex()[:4] =="1104" and  return_data.get("data") and len(return_data.get("data"))>1:
                            datas = return_data.get("data")
                            for data in datas:
                                desc = data.get("desc")
                                if desc and desc == "大气压测量值(KPa)":
                                    global_setting.set_setting("air_pressure_1104", float(data.get("value")))
                                    break
                        # 把返回数据返回给源头
                        message_struct = ObjectQueueItem(to=message['origin'],
                                                         data=parser_message,
                                                         origin='main_monitor_data')

                        global_setting.get_setting("send_message_queue").put(message_struct)
                        logger.debug(f"main_monitor_data将响应报文的解析数据返回源头：{message_struct}")
                        pass
                except queue.Empty:
                    self.priority_queue_empty=True
                    pass
                send_message=None
                # 处理普通消息
                try:
                    #!
                    with self.normal_queue_lock:
                        message = self.normal_queue.get(timeout=0.1)
                    send_message = message['message']
                    # logger.critical(f"send_thread:{self.name}<UNK>{message}")
                    # 消息没带type则当前不为参考气路，则进行鼠笼内传感器值获取 否则不获取
                    if message and message.get('type',None) is None:
                        start_time=time.time()
                        response, response_hex, send_state,return_data = self.modbus.send_command(
                            slave_id=send_message['slave_id'],
                            function_code=send_message['function_code'],
                            data_hex_list=send_message['data'],
                            is_parse_response=False
                        )
                        end_time = time.time()
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
                            return_data = self._apply_gas_model_correction(return_data)
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
                logger.error(f"{e}")
            finally:
                if self.normal_queue_empty:
                    continue
                if send_message is not None:
                    logger.info(f"响应报文{total_messages_processed}/{MESSAGE_BATCH_SIZE}响应结束")
                    with lock:
                        if total_messages_processed % MESSAGE_BATCH_SIZE == 0:
                            barrier: threading.Barrier = global_setting.get_setting("barrier")
                            if barrier is not None:
                                logger.debug(f"barrier_鼠笼内部传感器 run one batch done ! ")
                                barrier.wait()
                            total_messages_processed = 1
                            MESSAGE_BATCH_SIZE = 0

                            batch_complete_event.set()  # 通知主线程当前批次完成
                            batch_complete_event.clear()  # 重置事件
                        else:
                            total_messages_processed += 1
                else:
                    #如果遇到未知错误，则跳过这条报文
                    logger.error(f"响应报文{total_messages_processed}/{MESSAGE_BATCH_SIZE}响应遇到未知错误，直接跳过这条报文并结束")
                    with lock:

                        if MESSAGE_BATCH_SIZE == 0 or total_messages_processed % MESSAGE_BATCH_SIZE == 0:
                            barrier: threading.Barrier = global_setting.get_setting("barrier")
                            if barrier is not None:
                                logger.debug(f"barrier_鼠笼内部传感器 run one batch done ! ")
                                barrier.wait()

                            total_messages_processed = 1
                            MESSAGE_BATCH_SIZE = 0
                            batch_complete_event.set()  # 通知主线程当前批次完成
                            batch_complete_event.clear()  # 重置事件
                        else:
                            total_messages_processed += 1

            time.sleep(float(global_setting.get_setting('monitor_data')['SEND']['delay']))

    def _apply_gas_model_correction(self, return_data):
        """修正氧气值（鼠笼内传感器）"""
        try:
            if isinstance(return_data, dict) and return_data.get('data'):
                for item in return_data['data']:
                    if isinstance(item, dict):
                        desc = item.get('desc', '')
                        value = item.get('value')

                        # 修正氧气
                        if '氧' in desc and '修正前' not in desc and value is not None:
                            try:
                                # ===== 使用统一的O2修正管理器 =====
                                corrected = O2CorrectionManager.calibrate_and_compensate(value)
                                corrected = O2CorrectionManager.apply_model_correction(corrected)
                                item['value'] = corrected
                                logger.info(f"鼠笼内O2修正: {value} → {corrected}")
                            except Exception as e:
                                logger.error(f"鼠笼内O2修正失败: {e}")

        except Exception as e:
            logger.error(f"气体模型修正异常: {e}")

        return return_data


class Add_message_thread(MyQThread):
    def __init__(self,name,send_thread, port):
        super().__init__(name=name)
        self.send_thread = send_thread
        self.port=port
        self.mouse_cage_index=None
        pass
    def run(self):
        logger.warning(f"{self.name} thread has been started！")
        self._running=True
        # 发送消息
        global MESSAGE_BATCH_SIZE,gids

        # 等待气路启动
        wait_UFC_UGC_ZOS_start_event.wait()
        while self._running:
            self.mutex.lock()
            if self._paused:
                self.condition.wait(self.mutex)  # 等待条件变量
            self.mutex.unlock()

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
                """debugger专用 需要哪个模块的数据监控就放进去"""
                # logger.critical(f"data type : {data_type}")
                if data_type in [
                                Modbus_Slave_Send_Messages_Senior_Data.ENM,
                                  Modbus_Slave_Send_Messages_Senior_Data.EM,
                                  Modbus_Slave_Send_Messages_Senior_Data.DWM,
                                  Modbus_Slave_Send_Messages_Senior_Data.WM
                ]:
                    # 所有消息
                    for message_struct in data_type.value['send_messages']:

                        message_temp = copy.deepcopy(message_struct.message)
                        message_temp['port'] =  self.port

                        # logger.critical(f"add_message_thread_mouse_cage_index:{self.mouse_cage_index}")
                        if self.mouse_cage_index is not None:

                            mouse_cage = gids[self.mouse_cage_index] if gids else 1
                            message_temp['slave_id'] =copy.copy(format(int(message_temp['slave_id'], 16)+16*mouse_cage, '02X'))
                            send_messages.append({'message': message_temp})
                        else:
                            #参考气路则没有发送鼠笼内传感器
                            send_messages.append({'message': message_temp,'type':'reference'})
                        MESSAGE_BATCH_SIZE += 1


                pass
            for msg in send_messages:
                self.send_thread.add_message(message=msg, urgent=False)
            if self.mouse_cage_index is not None:
                mouse_cage = gids[self.mouse_cage_index] if gids else 1
            else:
                mouse_cage = None
            #     # 等待从线程处理完当前批次
            logo_text = f"{time_util.get_format_from_time(time.time())} | 鼠笼{mouse_cage if mouse_cage is not None else '参考气'}发送鼠笼内模块数据请求报文：一共{len([msg for msg in send_messages if msg.get('type') is None])}条报文！"
            logger.info(logo_text)
            queue = global_setting.get_setting("queue", None)
            if queue:
                queue.put(
                    ObjectQueueItem(origin="main_monitor_data", to="MainWindow_index", title="mouse_cage_inner_module_running_state",
                                    data=logo_text,
                                    time=time_util.get_format_from_time(time.time())))
            # print(f"send_messages:{send_messages}")
            # 将鼠笼下标循环前移动
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
            batch_complete_event.wait()

            logger.info(f"从线程已处理完上批消息，主线程继续发送下一批\n")

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
        folder_path_copy = os.getcwd() + global_setting.get_setting('monitor_data')['STORAGE']['fold_path'] + os.path.join(
            global_setting.get_setting('monitor_data')['STORAGE']['sub_fold_path'],f"{file_name_without_extension}_{time_util.get_format_file_from_time(global_setting.get_setting('start_experiment_time',time.time()))}","experiment_setting")

        # 创建文件夹（如果不存在）
        os.makedirs(folder_path_copy, exist_ok=True)

        source_file = experiment_setting_file
        destination_file=os.path.join(folder_path_copy,f"experiment_setting{file_name_extension}")
        # 复制文件并保留元数据
        shutil.copy2(source_file, destination_file)
        pass
    pass
# 线程
ufc_ugc_zos:UFC_UGC_ZOS_index=None
ufc_ugc_zos_thread =None
# 存储线程
store_thread:Store_Thread = None
# 发送报文线程
send_thread :Send_thread= None
add_message_thread:Add_message_thread = None
def apply_o2_model_correction(oxygen_value):
    """
    在barrier_action和其他地方使用的O2修正函数

    :param oxygen_value: 输入的氧气值
    :return: 修正后的氧气值
    """
    try:
        # ===== 使用统一的O2修正管理器 =====
        corrected = O2CorrectionManager.apply_model_correction(oxygen_value)
        return corrected
    except Exception as e:
        logger.error(f"O2模型修正异常: {e}，使用原值")
        return oxygen_value
#一轮模块发送报文结束
def barrier_action():
    end_time = time.time()
    mouse_cages_inc: list = global_setting.get_setting("mouse_cages", None)
    mouse_cage_index = global_setting.get_setting("cage_number_list_index", None)
    # logger.critical(f"barrier action run :mouse_cage_index before:{mouse_cage_index}")
    # 因为在zos运行完之后就更新了mouse_cage_index,所以现在得到的index是比上一轮多1的，所以需要往回退1
    if mouse_cage_index is  None:
        mouse_cage_index= len(mouse_cages_inc)-1
        pass
    elif mouse_cage_index ==0:
        mouse_cage_index=None
        pass
    else:
        mouse_cage_index=mouse_cage_index-1
        pass
    # logger.critical(f"barrier action  run :mouse_cage_index after:{mouse_cage_index}")
    mouse_cage_number = mouse_cages_inc[mouse_cage_index] if mouse_cage_index is not None else int(global_setting.get_setting('configer')['mouse_cage']['reference'])


    start_time = global_setting.get_setting("start_time_messages_sent_epoch_for_running", time.time())

    logo_text =f"{time_util.get_format_from_time(time.time())} | 一轮结束|结束时间：{time_util.get_format_from_time(end_time)}|开始时间：{time_util.get_format_from_time(start_time)}|用时：{time_util.format_timedelta(a= datetime.fromtimestamp(end_time),b= datetime.fromtimestamp(start_time),signed=True,zero_pad=True)}|一轮传感器发送报文结束|期间一共发送{ global_setting.get_setting('messages_sent_epoch_for_running', 0)}条报文。"
    logger.warning(logo_text)
    q = global_setting.get_setting("queue", None)
    if q:
        q.put(
            ObjectQueueItem(origin="main_monitor_data", to="MainWindow_index",
                            title="epoch_running_state",
                            data=logo_text,
                            time=time_util.get_format_from_time(time.time())))
    handle = Monitor_Datas_Handle()  # # 创建数据库操作器
    # 去数据库里查询 所有的在这个时间段的数据
    results, columns=handle.query_data_in_line_with_epoch_data(start_time,end_time)
    # logger.critical(f"{results}")
    store_Datas =[]
    # store_Datas.append({'desc':'序号','value':None})
    store_Datas.append({'desc':'鼠笼号','value':mouse_cage_number})
    # 为什么还要判断一下，因为有时候results.get('xxxx')的值为[]
    store_Datas.append({'desc': '氧浓度0点校准值',
                        'value': results.get('ZeroCalibration_data__oxygen_calibration_zero_value') if results.get(
                            'ZeroCalibration_data__oxygen_calibration_zero_value') is not None else None}  )
    store_Datas.append({'desc': '氧浓传感器span数值',
                        'value': results.get('SpanCalibration_data__oxygen_calibration_span_value') if results.get(
                            'SpanCalibration_data__oxygen_calibration_span_value') is not None else None})

    store_Datas.append({'desc': 'ufc_流量计测量值(sccm)', 'value': results.get(f'UFC_monitor_data_cage_{mouse_cage_number}__flow_num') if results.get(
                            f'UFC_monitor_data_cage_{mouse_cage_number}__flow_num') is not None else None   })
    store_Datas.append({'desc': 'ugc_流量计1', 'value': results.get(f'UGC_monitor_data_cage_{mouse_cage_number}__flow_num_1') if results.get(
                            f'UGC_monitor_data_cage_{mouse_cage_number}__flow_num_1')  is not None else None })
    store_Datas.append({'desc': '气压(KPa)',
                        'value': results.get(f'UGC_monitor_data_cage_{mouse_cage_number}__air_pressure') if results.get(
                            f'UGC_monitor_data_cage_{mouse_cage_number}__air_pressure') is not None else None})
    store_Datas.append(
        {'desc': '补偿前CO2(%)', 'value': results.get(f'UGC_monitor_data_cage_{mouse_cage_number}__CO2_origin_num') if results.get(
            f'UGC_monitor_data_cage_{mouse_cage_number}__CO2_origin_num') is not None else None})
    store_Datas.append({'desc': 'CO2(%)', 'value': results.get(f'UGC_monitor_data_cage_{mouse_cage_number}__CO2_num') if results.get(
                            f'UGC_monitor_data_cage_{mouse_cage_number}__CO2_num') is not None else None })
    # 获取原始氧气值
    oxygen_origin = results.get(f'ZOS_monitor_data_cage_{mouse_cage_number}__oxygen_origin_num')
    oxygen_measured = results.get(f'ZOS_monitor_data_cage_{mouse_cage_number}__oxygen_num')

    # ===== 使用统一的O2修正管理器 =====
    oxygen_corrected = O2CorrectionManager.apply_model_correction(
        oxygen_measured) if oxygen_measured is not None else None

    # 存储数据
    store_Datas.append({'desc': '补偿前氧气传感器测量值(%)', 'value': oxygen_origin})
    store_Datas.append({'desc': '氧气传感器测量值(%)', 'value': oxygen_corrected})
    store_Datas.append({'desc': '模型修正前氧气值(%)', 'value': oxygen_measured})
    # 非参考气
    remarks_reference=""
    if mouse_cage_number != int(global_setting.get_setting('configer')['mouse_cage']['reference']):
        # 获取参考气轮次的数据：
        reference_data = handle.query_current_one_data(table_name=f"Epoch_data_cage_{int(global_setting.get_setting('configer')['mouse_cage']['reference'])}")
        if reference_data is not None:
            # 获得所有参考备注
            remarks_reference = ";"
            remarks_reference += reference_data.get('remarks')  if reference_data.get('remarks') is not None else ""
            # logger.critical(f"reference_data:{reference_data}")
            store_Datas.append(
                {'desc': 'ufc_参考气流量计测量值(sccm)',
                 'value': reference_data.get(f'UFC_flow_num') if reference_data.get(
                            f'UFC_flow_num') is not None else None })
            store_Datas.append(
                {'desc': '参考气CO2(%)',
                 'value': reference_data.get(f'UGC_CO2_num') if reference_data.get(
                            f'UGC_CO2_num') is not None else None })
            # 参考气氧气值也需要修正
            reference_oxygen_raw = reference_data.get(f'ZOS_oxygen_num')
            reference_oxygen_corrected = apply_o2_model_correction(
                reference_oxygen_raw) if reference_oxygen_raw is not None else None

            store_Datas.append(
                {'desc': '参考气氧气测量值(%)',
                 'value': reference_oxygen_corrected})  # 使用修正后的参考气氧气值

            # 计算CO2生产量和耗氧量时，使用修正后的氧气值
            if results.get(f'UGC_monitor_data_cage_{mouse_cage_number}__CO2_num') is not None and reference_data.get(
                    f'UGC_CO2_num') is not None:
                store_Datas.append(
                    {'desc': 'CO2生产量(%)',
                     'value': results.get(f'UGC_monitor_data_cage_{mouse_cage_number}__CO2_num') - reference_data.get(
                         f'UGC_CO2_num')})

            # 耗氧量计算使用修正后的值
            if oxygen_corrected is not None and reference_oxygen_corrected is not None:
                store_Datas.append(
                    {'desc': '耗氧量(%)',
                     'value': reference_oxygen_corrected - oxygen_corrected})

            #求红外温度的平均值
            temp_values = results.get(f'MouseInfrared_data_cage_{mouse_cage_number}__tmp_hs_mean',None)
            if temp_values is not None:
                infrared_temp_average = None
                #过滤掉None值
                filter_temp_values = [x for x in results.get(f'MouseInfrared_data_cage_{mouse_cage_number}__tmp_hs_mean') if x is not None]
                if len(filter_temp_values) !=0:
                    infrared_temp_average = round(sum(filter_temp_values) / len(filter_temp_values),4)
                store_Datas.append({'desc': '鼠笼红外温度(°C)',
                                    'value': infrared_temp_average})
            pass

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
            {'desc': '饮水重量测量值(g)', 'value': results.get(f'DWM_monitor_data_cage_{mouse_cage_number}__weight_num')  if results.get(
                            f'DWM_monitor_data_cage_{mouse_cage_number}__weight_num') is not None else None})
        store_Datas.append(
            {'desc': '食物重量测量值(g)', 'value':results.get(f'EM_monitor_data_cage_{mouse_cage_number}__weight_num') if results.get(
                            f'EM_monitor_data_cage_{mouse_cage_number}__weight_num') is not None else None })
        store_Datas.append(
            {'desc': '称重重量测量值(g)', 'value': results.get(f'WM_monitor_data_cage_{mouse_cage_number}__weight_num')  if results.get(
                            f'WM_monitor_data_cage_{mouse_cage_number}__weight_num') is not None else None })

    store_Datas.append({'desc':'轮次开始时间','value':datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]})
    store_Datas.append({'desc':'轮次结束时间','value':datetime.fromtimestamp(end_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]})
    # 获得所有备注
    # logger.critical(f"rs:{results}")
    remarks = "".join(f" {key}: {value}; " for key, value in results.items()
                            if "remarks" in key and value is not None and value != [])
    store_Datas.append({'desc':'备注','value':remarks+remarks_reference})
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
    # 存到分表
    result = store_data_with_result(return_data_struct, need_result=True, timeout=5)
    if result and result.success:
        logger.info(f"epoch_result数据存储成功，ID: {result.item_id}")
    else:
        logger.error(f"epoch_result数据存储失败: {result.error if result else '未知错误'}")
    # 存到总表
    return_data_struct_all = copy.deepcopy(return_data_struct)
    return_data_struct_all['table_name'] = f"{next(iter(Others_Tables.Epoch_Data.value.keys()))}_all"
    return_data_struct_all['mouse_cage_number'] =-1
    result_all = store_data_with_result(return_data_struct_all, need_result=True, timeout=5)
    if result_all and result_all.success:
        logger.info(f"epoch_result_all数据存储成功，ID: {result_all.item_id}")
    else:
        logger.error(f"epoch_result_all数据存储成功: {result_all.error if result_all else '未知错误'}")
    global_setting.set_setting("start_time_messages_sent_epoch_for_running", end_time+0.1)
    global_setting.set_setting("messages_sent_epoch_for_running",
                               0)


def after_run_of_ufc_ugc_zos_barrier_action():
    #ufc ugc zos run完后在鼠笼内run
    # 通知鼠笼传感器解除阻塞开始运行
    wait_UFC_UGC_ZOS_start_event = global_setting.get_setting("wait_UFC_UGC_ZOS_start_event")
    if wait_UFC_UGC_ZOS_start_event is not None:
        wait_UFC_UGC_ZOS_start_event.set()
        wait_UFC_UGC_ZOS_start_event.clear()  # 重置事件
def main(q,send_message_q):

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
    app = QCoreApplication(sys.argv)
    # 设置全局变量
    global_load.load_global_setting_without_Qt()
    # 当前鼠笼号列表的下标 参考气的下标为None 注意区分
    global_setting.set_setting("cage_number_list_index", None)
    global_setting.set_setting("queue", q)
    global_setting.set_setting("send_message_queue", send_message_q)
    #设置线程屏障，等待4个线程 ufc ugc zos的run还有鼠笼内的模块线程的send_message_thread
    # barrier专门用于多个线程需要在某个点同步等待的场景。每个线程执行完自己的工作后调用
    # barrier.wait()，当所有线程都到达这个同步点时，它们会同时继续执行下一轮循环。
    # barrier = ActionCompleteBarrier(4,action=barrier_action)
    barrier = threading.Barrier(4,action=barrier_action)
    global_setting.set_setting("barrier", barrier)
    #专属于ufc ugc zos 的run的barrier
    # ufc_ugc_zos_barrier =ActionCompleteBarrier(3,action=after_run_of_ufc_ugc_zos_barrier_action)
    # global_setting.set_setting("ufc_ugc_zos_barrier", ufc_ugc_zos_barrier)
    #每轮运行发送报文数量 总的 在气路启动后和一轮结束后会重新赋值0
    global_setting.set_setting("messages_sent_epoch_for_running",0)
    #每轮运行开始的时间 在气路启动后和一轮结束后会重新赋值
    global_setting.set_setting("start_time_messages_sent_epoch_for_running",time.time())
    global read_queue_data_thread

    read_queue_data_thread.queue = send_message_q

    read_queue_data_thread.start()
    # return store_thread,send_thread,read_queue_data_thread,add_message_thread,ufc_ugc_zos,ufc_ugc_zos_thread
    # 系统退出
    return app.exec()
def start():
    logger.info(f"{'-' * 30}monitor_data_run{'-' * 30}")
    global MESSAGE_BATCH_SIZE, total_messages_processed,gids
    MESSAGE_BATCH_SIZE = 0
    total_messages_processed = 1
    # 当前鼠笼号列表的下标 参考气的下标为None 注意区分
    global_setting.set_setting("cage_number_list_index", None)
    # 通道
    experiment_settings = global_setting.get_setting("experiment_setting", None)
    gids = [group.id for group in experiment_settings.groups] if experiment_settings is not None else []
    # UFC_UGC_ZOS
    global ufc_ugc_zos_thread, ufc_ugc_zos,store_thread,send_thread,add_message_thread
    ufc_ugc_zos_thread = None
    ufc_ugc_zos = None
    try:
        # ufc_ugc_zos = UFC_UGC_ZOS_index()
        # ufc_ugc_zos.auto_btn_handle()
        ufc_ugc_zos_thread= UFC_UGC_ZOS_index()
        ufc_ugc_zos_thread.start()

    except Exception as ex:
        print(ex)
        logger.error(f"<UNK>{ex}")

    # 存储线程
    store_thread = Store_Thread(name="monitor_data_store_message")
    store_thread.start()

    # 发送报文线程
    send_thread = Send_thread(name="monitor_data_send_message")
    send_thread.start()
    read_queue_data_thread.send_thread = send_thread

    global port_use
    add_message_thread = Add_message_thread("monitor_data_add_message", send_thread, port_use)
    add_message_thread.start()

    # 将实验配置存储到该实验的文件夹中去
    copy_experiment_setting_file()
def start_zero_calibration():
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
    if ufc_ugc_zos_thread is not None:
        ufc_ugc_zos_thread.range_calibration_handle()
    pass
def start_calibration():
    """
      校0 和span
    :return:
    """
    if ufc_ugc_zos_thread is not None:
        ufc_ugc_zos_thread.calibration_btn_start()
    pass
def restart(q,send_message_q):
    main(q,send_message_q)
    start()
def pause():
    logger.info(f"{'-' * 30}monitor_data_pause{'-' * 30}")
    pass

def stop():
    logger.info(f"{'-' * 30}monitor_data_stop{'-' * 30}")
    global ufc_ugc_zos_thread, ufc_ugc_zos,store_thread,send_thread,add_message_thread
    try:
        logger.error("stop_ufc_ugc_zos")

        if ufc_ugc_zos_thread is not None:
            ufc_ugc_zos_thread.stop_btn_handle()
            ufc_ugc_zos_thread.stop()
            ufc_ugc_zos_thread=None
    except Exception as e:
        logger.error(f"关闭实验监测ufc_ugc_zos错误，原因：{e}")
    pass
    try:
        logger.error("stop_store_thread")
        if store_thread is not None and store_thread.isRunning():
            store_thread.stop()
            # 返回响应
            queue = global_setting.get_setting("queue", None)
            if queue:
                queue.put(
                    ObjectQueueItem(origin="main_monitor_data", to="MainWindow_index", title="stop_monitor_data_return",
                                    data=" 存储线程 已关闭",
                                    time=time_util.get_format_from_time(time.time())))
            store_thread=None
    except Exception as e:
        logger.error(f"关闭实验监测store_thread错误，原因：{e}")
        # 返回响应
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(
                ObjectQueueItem(origin="main_monitor_data", to="MainWindow_index", title="stop_gap_system_return",
                                data=f"关闭气路模块错误，原因：{e}",
                                time=time_util.get_format_from_time(time.time())))
    try:
        logger.error("stop_add_message_thread")
        if add_message_thread is not None and add_message_thread.isRunning():
            add_message_thread.stop()
            add_message_thread=None
            # 返回响应
            queue = global_setting.get_setting("queue", None)
            if queue:
                queue.put(
                    ObjectQueueItem(origin="main_monitor_data", to="MainWindow_index", title="stop_monitor_data_return",
                                    data=" 鼠笼内模块报文装载线程 已关闭",
                                    time=time_util.get_format_from_time(time.time())))
    except Exception as e:
        logger.error(f"关闭实验监测add_message_thread错误，原因：{e}")
        # 返回响应
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(
                ObjectQueueItem(origin="main_monitor_data", to="MainWindow_index", title="stop_gap_system_return",
                                data=f"关闭鼠笼内模块报文装载线程错误，原因：{e}",
                                time=time_util.get_format_from_time(time.time())))
    try:
        logger.error("stop_send_thread")
        if send_thread is not None and send_thread.isRunning():
            send_thread.stop()
            send_thread=None
            # 返回响应
            queue = global_setting.get_setting("queue", None)
            if queue:
                queue.put(
                    ObjectQueueItem(origin="main_monitor_data", to="MainWindow_index", title="stop_monitor_data_return",
                                    data="鼠笼内模块报文发送线程 已关闭",
                                    time=time_util.get_format_from_time(time.time())))
    except Exception as e:
        logger.error(f"关闭实验监测send_thread错误，原因：{e}")
        # 返回响应
        queue = global_setting.get_setting("queue", None)
        if queue:
            queue.put(
                ObjectQueueItem(origin="main_monitor_data", to="MainWindow_index", title="stop_gap_system_return",
                                data=f"关闭鼠笼内模块报文发送线程错误，原因：{e}",
                                time=time_util.get_format_from_time(time.time())))


def stop_modbus():
    modbus: ModbusRTUMasterNew = global_setting.get_setting("modbus", None)
    if modbus is not None:
        logger.error("stop_modbus_stop_experiment")
        modbus.close()
    pass
if __name__ == "__main__":
    q = multiprocessing.Queue()
    send_message_q = multiprocessing.Queue()
    main("COM5",q,send_message_q)
