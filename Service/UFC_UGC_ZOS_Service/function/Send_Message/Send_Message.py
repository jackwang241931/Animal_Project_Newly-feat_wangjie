import threading
import time

from PyQt6.QtCore import pyqtSignal
from blinker.base import _PNamespaceSignal
from loguru import logger

from public.config_class.global_setting import global_setting
from public.entity.enum.Public_Enum import GapSystem_Running_Type
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.function.Modbus.Modbus_Type import Modbus_Slave_Type
from public.function.Modbus.New_Mod_Bus import ModbusRTUMasterNew
from public.util.time_util import time_util


#logger = logger.bind(category="deep_camera_logger")
import time


class Send_Message:
    def __init__(self, update_status_main_signal_gui_update=None, send_message=None, modbus=None,update_status_main_signal_gui_update_type=GapSystem_Running_Type.DEFAULT):
        # 更新主线程状态栏消息信号
        self.update_status_main_signal_gui_update: _PNamespaceSignal = update_status_main_signal_gui_update
        self.send_message = send_message
        self.modbus: ModbusRTUMasterNew = global_setting.get_setting("modbus", None)
        self.update_status_main_signal_gui_update_type = update_status_main_signal_gui_update_type

    def _send_with_retry(self, max_retries=3):
        """带重试机制的发送方法"""
        for attempt in range(max_retries + 1):  # 包含初次发送 + 3次重试
            try:
                logger.info(f"发送尝试 {attempt + 1}/{max_retries + 1}: {self.send_message}")
                start_time = time.time()

                response, response_hex, send_state, return_data = self.modbus.send_command(
                    slave_id=self.send_message['slave_id'],
                    function_code=self.send_message['function_code'],
                    data_hex_list=self.send_message['data'],
                    is_parse_response=False
                )

                end_time = time.time()

                if response is not None:
                    logger.critical(f"报文{response.hex()}发收时间：{(end_time - start_time):.3f}秒")
                else:
                    logger.critical(
                        f"报文{self.send_message['slave_id']}{self.send_message['function_code']}{self.send_message['data']},出现问题！：发收时间：{(end_time - start_time):.3f}秒")

                # 如果发送状态为True，返回成功结果
                if send_state:
                    logger.info(f"第 {attempt + 1} 次尝试发送报文{self.send_message['slave_id']}{self.send_message['function_code']}{self.send_message['data']}成功")
                    return response, response_hex, send_state, return_data
                else:
                    logger.warning(f"第 {attempt + 1} 次尝试发送报文{self.send_message['slave_id']}{self.send_message['function_code']}{self.send_message['data']}失败，send_state=False")
                    if attempt < max_retries:
                        logger.info(f"准备进行第 {attempt + 2} 次重试发送报文{self.send_message['slave_id']}{self.send_message['function_code']}{self.send_message['data']}...")
                        time.sleep(0.1)  # 重试前短暂延迟
                    else:
                        # 最后一次尝试也失败了
                        logger.error(f"经过 {max_retries + 1} 次尝试，发送报文{self.send_message['slave_id']}{self.send_message['function_code']}{self.send_message['data']}仍然失败")
                        return response, response_hex, send_state, return_data

            except Exception as e:
                logger.error(f"第 {attempt + 1} 次发送报文{self.send_message['slave_id']}{self.send_message['function_code']}{self.send_message['data']}尝试出现异常: {e}")
                if attempt < max_retries:
                    logger.info(f"准备进行第 {attempt + 2} 次重试报文{self.send_message['slave_id']}{self.send_message['function_code']}{self.send_message['data']}...")
                    time.sleep(0.1)  # 重试前短暂延迟
                else:
                    # 重新抛出异常
                    raise e

        # 理论上不应该到这里
        raise Exception(f"发送报文失败，已重试 {max_retries} 次")

    def Send(self, resolve, reject):
        serial_lock = global_setting.get_setting('serial_lock', threading.Lock())
        with serial_lock:
            return_data = None
            parser_message = None
            response_hex = None
            send_state = False

            try:
                # 使用带重试的发送方法
                response, response_hex, send_state, return_data = self._send_with_retry()

                # # 如果最终send_state仍为False，抛出异常
                # if not send_state:
                #     error_msg = "发送报文失败，经过重试后send_state仍为False"
                #     logger.error(error_msg)
                #     raise Exception(error_msg)

                # 响应报文是正确的，即发送状态时正确的 进行解析响应报文
                if send_state:
                    return_data, parser_message = self.modbus.parse_response(
                        response=response,
                        response_hex=response.hex(),
                        send_state=True,
                        slave_id=self.send_message['slave_id'],
                        function_code=self.send_message['function_code']
                    )

                    # 将解析数据返回给主菜单
                    self.update_status_main_signal_gui_update.send(
                        f"{time_util.get_format_from_time(time.time())} | {parser_message}",title=self.update_status_main_signal_gui_update_type)
                    return_data['data'].append({'desc': '备注', 'value': None})
                else:
                    # 将错误信息返回给主菜单
                    if return_data:
                        for data in return_data['data']:
                            if data and data.get('desc') and data.get('desc') == '备注':
                                self.update_status_main_signal_gui_update.send(
                                    f"{time_util.get_format_from_time(time.time())} |{data.get('value')}",title=self.update_status_main_signal_gui_update_type)
                                break

                # 把返回数据返回给源头
                message_struct = ObjectQueueItem(
                    to="UFC_UGC_ZOS_index",
                    data=parser_message,
                    origin='UFC_UGC_ZOS_index_send_thread'
                )
                global_setting.get_setting("send_message_queue").put(message_struct)

            except Exception as e:
                logger.error(f"Send方法异常: {e}")
                reject(e)
                return
            finally:
                resolve({
                    "data": return_data,
                    "message": parser_message,
                    "send_state": send_state,
                    "response_hex": response_hex,
                })

    def Send_no_promise(self):
        serial_lock = global_setting.get_setting('serial_lock', threading.Lock())
        with serial_lock:
            return_data = None
            parser_message = None

            try:
                # 使用带重试的发送方法
                response, response_hex, send_state, return_data = self._send_with_retry()

                # # 如果最终send_state仍为False，抛出异常
                # if not send_state:
                #     error_msg = "发送报文失败，经过重试后send_state仍为False"
                #     logger.error(error_msg)
                #     raise Exception(error_msg)

                # 响应报文是正确的，即发送状态时正确的 进行解析响应报文
                if send_state:
                    return_data, parser_message = self.modbus.parse_response(
                        response=response,
                        response_hex=response.hex(),
                        send_state=True,
                        slave_id=self.send_message['slave_id'],
                        function_code=self.send_message['function_code']
                    )

                    # 将解析数据返回给主菜单
                    self.update_status_main_signal_gui_update.send(
                        f"{time_util.get_format_from_time(time.time())} | {parser_message}",title=self.update_status_main_signal_gui_update_type)
                    return_data['data'].append({'desc': '备注', 'value': None})
                else:
                    # 将错误信息返回给主菜单
                    if return_data:
                        for data in return_data['data']:
                            if data and data.get('desc') and data.get('desc') == '备注':
                                self.update_status_main_signal_gui_update.send(
                                    f"{time_util.get_format_from_time(time.time())} | {data.get('value')}",title=self.update_status_main_signal_gui_update_type)
                                break

                # 把返回数据返回给源头
                message_struct = ObjectQueueItem(
                    to="UFC_UGC_ZOS_index",
                    data=parser_message,
                    origin='UFC_UGC_ZOS_index_send_thread'
                )
                global_setting.get_setting("send_message_queue").put(message_struct)

            except Exception as e:
                logger.error(f"Send_no_promise方法异常: {e}")
                return_data = None
                parser_message = None
            finally:
                return return_data, parser_message
