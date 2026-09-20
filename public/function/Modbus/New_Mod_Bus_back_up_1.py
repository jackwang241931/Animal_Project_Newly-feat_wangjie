from datetime import datetime

import serial
import struct
import time
import threading
from typing import Optional, Tuple, List, Union
from contextlib import contextmanager

from loguru import logger

from public.config_class.global_setting import global_setting
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.function.Modbus.Modbus_Response_Parser import Modbus_Response_Parser, get_module_name
from public.function.Modbus.Modbus_Type import Modbus_Slave_Type
from public.util.time_util import time_util

#logger = logger.bind(category="deep_camera_logger")
class ModbusRTUMasterNew:
    """
    可随时随处调用的Modbus RTU通信类
    支持连接复用、自动重连、线程安全
    """

    def __init__(self, port='COM1', baudrate=115200, timeout=1, origin=None):
        """
        初始化Modbus RTU Master
        """
        # 基本参数
        self.sport = port
        self.baudrate = baudrate
        self.timeout = timeout
        self.origin = origin

        # 连接管理
        self.ser: Optional[serial.Serial] = None
        self.is_connected = False

        # 使用RLock支持重入，避免死锁
        self._lock = threading.RLock()

        # 自动重连参数
        self.auto_reconnect = True
        self.max_reconnect_attempts = 3

    @contextmanager
    def _safe_lock(self):
        """安全的锁管理器"""
        acquired = False
        try:
            acquired = self._lock.acquire(timeout=10)  # 10秒超时
            if not acquired:
                raise TimeoutError("获取锁超时")
            yield
        finally:
            if acquired:
                self._lock.release()

    def connect(self) -> bool:
        """建立串口连接"""
        try:
            # 使用安全锁管理器
            with self._safe_lock():
                if self.is_connected and self.ser and self.ser.is_open:
                    return True

                # 关闭现有连接
                self._close_connection_unsafe()

                # 建立新连接
                logger.info(f"正在连接串口 {self.sport}...")
                self.ser = serial.Serial(
                    port=self.sport,
                    baudrate=self.baudrate,
                    bytesize=8,
                    parity='N',
                    stopbits=1,
                    timeout=self.timeout
                )

                self.is_connected = True
                self._send_status_message(f"连接成功")
                logger.info(f"{self.sport}-连接成功")
                return True

        except TimeoutError as e:
            logger.error(f"{self.sport}-获取锁超时: {e}")
            return False
        except Exception as e:
            self.is_connected = False
            self._send_status_message(f"连接失败: {e}")
            logger.error(f"{self.sport}-连接失败: {e}")
            return False

    def _close_connection_unsafe(self):
        """内部方法：关闭连接（不加锁版本）"""
        try:
            if self.ser and self.ser.is_open:
                self.ser.close()
        except:
            pass
        finally:
            self.ser = None
            self.is_connected = False

    def close(self):
        """公共方法：关闭连接"""
        try:
            with self._safe_lock():
                self._close_connection_unsafe()
                logger.info(f"{self.sport}-连接已关闭")
        except Exception as e:
            logger.error(f"{self.sport}-关闭连接时出错: {e}")

    def _ensure_connection(self) -> bool:
        """确保连接可用，支持自动重连"""
        if self.is_connected and self.ser and self.ser.is_open:
            try:
                # 简单测试连接是否正常
                if hasattr(self.ser, 'in_waiting'):
                    _ = self.ser.in_waiting  # 测试连接
                return True
            except:
                self.is_connected = False

        if not self.auto_reconnect:
            return False

        # 尝试重连（不使用锁，因为已经在锁中）
        for attempt in range(self.max_reconnect_attempts):
            try:
                # 使用安全锁管理器
                with self._safe_lock():
                    logger.info(f"尝试重连 {attempt + 1}/{self.max_reconnect_attempts}")

                    # 关闭现有连接
                    self._close_connection_unsafe()

                    # 建立新连接
                    self.ser = serial.Serial(
                        port=self.sport,
                        baudrate=self.baudrate,
                        bytesize=8,
                        parity='N',
                        stopbits=1,
                        timeout=self.timeout
                    )

                    self.is_connected = True
                    logger.info(f"{self.sport}-重连成功")
                    return True

            except Exception as e:
                logger.error(f"{self.sport}-重连尝试 {attempt + 1} 失败: {e}")
                time.sleep(0.5)  # 重连间隔

        return False

    def _send_status_message(self, message: str):
        """发送状态消息到队列（无锁版本）"""
        return
        try:
            if self.origin is not None:

                message_struct = ObjectQueueItem(to=self.origin,
                                                 data=f"{time_util.get_format_from_time(time.time())}-{self.sport}-{message}",
                                                 origin='main_monitor_data')
                global_setting.get_setting("send_message_queue").put(message_struct)
        except Exception as e:
            logger.error(f"发送状态消息失败: {e}")

    def calculate_crc(self, data: bytes) -> bytes:
        """计算Modbus RTU CRC-16，小端返回"""
        crc = 0xFFFF
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 0x0001:
                    crc >>= 1
                    crc ^= 0xA001
                else:
                    crc >>= 1
        return struct.pack('<H', crc)

    def build_frame(self, slave_id: Union[str, int], function_code: Union[str, int],
                    data_hex_list: List[str]) -> Optional[bytes]:
        """构造完整 Modbus RTU 报文（包含CRC）"""
        try:
            # 统一转换为整数
            if isinstance(slave_id, str):
                slave_id = int(slave_id, 16)
            if isinstance(function_code, str):
                function_code = int(function_code, 16)

            data_bytes = [int(x, 16) for x in data_hex_list]

            # 组装帧
            frame = struct.pack('>B B B B B B', slave_id, function_code, *data_bytes)
            crc = self.calculate_crc(frame)

            logger.info(f"构造发送报文frame: {frame.hex()}|crc: {crc.hex()}")
            return frame + crc

        except Exception as e:
            error_msg = f"构造报文出错: {e}"
            self._send_status_message(error_msg)
            logger.error(f"{time_util.get_format_from_time(time.time())}-{self.sport}-{error_msg}")
            return None
    def get_table_name(self,slave_id):
        slave_id_int = int(slave_id, 16)
        # print(f"slave_id_int:{slave_id_int}")
        if slave_id_int > 16:
            mouse_cage_number = slave_id_int // 16
            # 鼠笼内传感器
            for type in Modbus_Slave_Type.Each_Mouse_Cage.value:
                if type.value['int'] == (slave_id_int % 16):
                    return next(iter(type.value['table'].keys()))
        else:
            # 非鼠笼内传感器
            for type in Modbus_Slave_Type.Not_Each_Mouse_Cage.value:
                if type.value['int'] == (slave_id_int % 16):
                    # logger.info(f"type.value['name'] Not_Each:{type.value['name']}")
                    return next(iter(type.value['table'].keys()))
                    break
        return ""
        pass
    def send_command(self, slave_id: Union[str, int], function_code: Union[str, int],
                     data_hex_list: List[str], is_parse_response: bool = True) -> Tuple[
        Optional[bytes], Optional[str], bool,dict]:
        """
        发送Modbus RTU命令并获取响应（主要方法）
        """
        return_data = {}
        return_data['module_name'] = get_module_name(slave_id)
        return_data['table_name'] = self.get_table_name(slave_id)
        return_data['time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        return_data['mouse_cage_number'] = int(slave_id, 16)// 16 if int(slave_id, 16) > 16 else 0
        return_data['data'] = []
        return_data['slave_id'] = slave_id
        return_data['function_code'] = function_code
        try:
            with self._safe_lock():

                # logger.info(f"开始发送命令到 {self.sport}")
                # 每轮运行报文加1
                global_setting.set_setting("messages_sent_epoch_for_running", global_setting.get_setting("messages_sent_epoch_for_running", 0)+1)
                # 确保连接可用
                if not self._ensure_connection():
                    return_data['data'].append({'desc':'备注', 'value':f"{self.sport}-无法建立连接"})
                    logger.error(f"{self.sport}-无法建立连接")
                    return None, None, False,return_data

                # 构造报文
                frame = self.build_frame(slave_id, function_code, data_hex_list)
                if frame is None:
                    return_data['data'].append({'desc': '备注', 'value': f"构造发送报文 frame 为空"})
                    return None, None, False,return_data

                # 发送数据
                self._send_status_message(f"发送数据帧{frame.hex()}")
                logger.info(f"{time_util.get_format_from_time(time.time())}-{self.sport}-发送数据帧{frame.hex()}")

                self.ser.reset_input_buffer()
                self.ser.reset_output_buffer()
                self.ser.write(frame)
                # 读取响应
                response = self.ser.read(256)

                # 验证响应
                return self._validate_response(response, slave_id, function_code, is_parse_response,frame,return_data)

        except TimeoutError as e:
            logger.error(f"{self.sport}-操作超时: {e}")
            return_data['data'].append({'desc': '备注', 'value': f"{self.sport}-操作超时: {e}"})
            return None, None, False,return_data
        except Exception as e:
            error_msg = f"串口通信异常: {e}"
            self._send_status_message(f"❗ {error_msg}")
            logger.error(f"{time_util.get_format_from_time(time.time())}-{self.sport}-❗ {error_msg}")
            return_data['data'].append({'desc': '备注', 'value': f"{error_msg}"})
            # 通信异常时断开连接
            self.is_connected = False
            return None, None, False,return_data

    def _validate_response(self, response: bytes, slave_id: Union[str, int],
                           function_code: Union[str, int], is_parse_response: bool,send_frame,return_data) -> Tuple[
        Optional[bytes], Optional[str], bool,dict]:
        """验证响应数据"""
        # 超时判断
        if not response:
            self._send_status_message(f"{time_util.get_format_from_time(time.time())}-{self.sport}-请求报文{send_frame.hex()}响应-Time OUT1-未获取到响应数据")
            logger.error(f"{time_util.get_format_from_time(time.time())}-{self.sport}-请求报文{send_frame.hex()}响应-Time OUT1-未获取到响应数据")
            return_data['data'].append({'desc': '备注', 'value': f"请求报文{send_frame.hex()}-Time OUT1-未获取到响应数据"})
            return None, None, False,return_data

        # 数据长度检查
        if len(response) < 5:
            self._send_status_message(f"{time_util.get_format_from_time(time.time())}-{self.sport}-请求报文{send_frame.hex()}响应报文{response.hex()}-Time OUT2-返回数据位数错误")
            logger.error(f"{time_util.get_format_from_time(time.time())}-{self.sport}-请求报文{send_frame.hex()}响应报文{response.hex()}-Time OUT2-返回数据位数错误")
            return_data['data'].append({'desc': '备注', 'value': f"请求报文{send_frame.hex()}-响应报文{response.hex()}-Time OUT2-返回数据位数错误"})
            return response, response.hex(), False,return_data

        # CRC校验
        data_part = response[:-2]
        crc_received = response[-2:]
        crc_expected = self.calculate_crc(data_part)

        if crc_received != crc_expected:
            self._send_status_message(f"{time_util.get_format_from_time(time.time())}-{self.sport}-请求报文{send_frame.hex()}响应报文{response.hex()}-Time OUT3-数据错误，CRC验证失败")
            logger.error(f"{time_util.get_format_from_time(time.time())}-{self.sport}-请求报文{send_frame.hex()}响应报文{response.hex()}-Time OUT3-数据错误，CRC验证失败")
            return_data['data'].append({'desc': '备注', 'value': f"请求报文{send_frame.hex()}-响应报文{response.hex()}-Time OUT3-数据错误，CRC验证失败"})
            return response, response.hex(), False,return_data

        # 检查异常响应
        function_code_response = response[1]
        if function_code_response & 0x80:
            exception_code = response[2]
            error_msg = f"{time_util.get_format_from_time(time.time())}-{self.sport}-请求报文{send_frame.hex()}响应报文{response.hex()}-异常：功能码=0x{function_code_response:02X}, 异常码=0x{exception_code:02X}"
            self._send_status_message(error_msg)
            logger.error(f"{error_msg}")
            return_data['data'].append({'desc': '备注', 'value': f"请求报文{send_frame.hex()}-响应报文{response.hex()}-异常：功能码=0x{function_code_response:02X}, 异常码=0x{exception_code:02X}"})
            return response, response.hex(), False,return_data

        # 响应正常
        self._send_status_message(f"{time_util.get_format_from_time(time.time())}-{self.sport}-请求报文{send_frame.hex()}响应报文{response.hex()}-CRC校验通过，正常响应")
        logger.info(f"{time_util.get_format_from_time(time.time())}-{self.sport}-请求报文{send_frame.hex()}响应报文{response.hex()}-CRC校验通过，正常响应")

        self._send_status_message(f"{time_util.get_format_from_time(time.time())}-{self.sport}-请求报文{send_frame.hex()}响应-收到响应消息-{response.hex()}-数据部分{data_part.hex()}")
        logger.info(
            f"{time_util.get_format_from_time(time.time())}-{self.sport}-请求报文{send_frame.hex()}响应-收到响应消息-{response.hex()}-数据部分{data_part.hex()}")

        # 解析响应 一般都不在这里直接解析响应！！！！要不在main_monitor_data 里或者就在send_message里
        if is_parse_response:
            self.parse_response(response, response.hex(), True, slave_id, function_code)
        # 等待响应
        delay = float(global_setting.get_setting('monitor_data')['SEND']['get_response_delay'])
        # delay=0.5
        time.sleep(delay)
        return response, response.hex(), True,return_data

    def parse_response(self, response: bytes, response_hex: str, send_state: bool,
                       slave_id: Union[str, int], function_code: Union[str, int]):
        """解析响应报文"""
        logger.info(f"response[0](slave_id)-{response[0]}|slave_id:{slave_id}|response-{response}|response_hex-{response_hex}|send_state-{send_state}|response[1](FUNC_CODE)-{response[1]}|function_code:{function_code}")
        if send_state:
            logger.info("开始解析报文")
            try:
                # 为了线程安全只能去response里的数值
                modbus_response_parser = Modbus_Response_Parser(
                    slave_id=f"{response[0]:x}",
                    function_code=response[1],
                    response=response,
                    response_hex=response_hex
                )
                return modbus_response_parser.parser()
            except Exception as e:
                logger.error(f"解析响应报文失败response[0](slave_id)-{response[0]}|slave_id:{slave_id}|response-{response}|response_hex-{response_hex}|send_state-{send_state}|response[1](FUNC_CODE)-{response[1]}|function_code:{function_code}: {e}")
                return None, None
        return None, None

    def is_alive(self) -> bool:
        """检查连接是否正常"""
        try:
            return self.is_connected and self.ser and self.ser.is_open
        except:
            return False

    def __enter__(self):
        """支持with语句"""
        if self.connect():
            return self
        else:
            raise ConnectionError(f"无法连接到串口 {self.sport}")

    def __exit__(self, exc_type, exc_val, exc_tb):
        """支持with语句"""
        self.close()
