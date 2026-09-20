"""
模拟响应串口
"""
import random
import struct
import threading
import time
import traceback

import serial
from loguru import logger
from serial.tools import list_ports

from public.util.time_util import time_util

# 过滤日志
logger = logger.bind(category="response_modbus_logger")
class communication(threading.Thread):
    """
    串口类 子线程
    """

    def __init__(self, category="receive", index=0):
        """
        类实例化函数
        :param category 通讯串口类别 receive接收串口 send发送串口
        :param index 第几个串口的index 配置文件中的receive_port的下标
        :param config_file_path 串口配置文件地址 "./communicate_config.ini"
        :return 无返回值
        """
        super().__init__()
        self.index = index
        self.category = category

        self.port = "COM5"
        self.ser = None  # 串口类
        self.running = True  # 控制线程运行的标志
        # 串口接收实例化
        self.serial_init()
        pass

    def is_port_occupied(self):
        """
        检测我们的配置端口是否被占用
        :return: True False
        """
        # 获取可用的端口
        available_ports = self.list_available_ports()
        # 判断配置的端口是否可用
        if not available_ports:
            logger.error(f"{self.category}串口{str(self.port)}连接--当前设备无可用的端口")
            return True
        if str(self.port) not in available_ports:
            logger.error(
                f"{self.category}串口{str(self.port)}连接--配置文件中的端口{str(self.port)}无法使用，请换个端口！")
            logger.error(f"串口{str(self.port)}连接--当前可用端口:{available_ports}")
            return True
        logger.info(f"{self.category}串口{str(self.port)}连接--当前可用端口:{available_ports}")
        return False

    def list_available_ports(self):
        """
        获取可用的端口
        :return:[port,...]
        """
        ports = list_ports.comports()
        return [port.device for port in ports]

    def serial_init(self):
        """
        串口实例化 读取ini的配置文件进行配置
        :return:无
        """
        if self.is_port_occupied():
            logger.error(
                f"{self.category}串口{str(self.port)}连接--当前设备所设置端口{str(self.port)}错误！")
            logger.error(f"{self.category}串口{str(self.port)}实例化失败！")
            self.running = False
        else:

            try:
                self.ser = serial.Serial(
                    port=self.port,
                    baudrate=115200,
                    bytesize=8,
                    parity='N',
                    stopbits=1,
                    timeout=0.05
                )
                logger.info(f"{self.category}串口{str(self.port)}连接成功")
            except KeyboardInterrupt:
                logger.error(f"{self.category}串口{str(self.port)}连接--程序被用户终止！")
                self.running = False
            except Exception as e:
                logger.error(
                    f"{self.category}串口{str(self.port)}连接--发生错误: {e} |  异常堆栈跟踪：{traceback.print_exc()}！")
                self.running = False
            finally:
                pass
                # if self.ser and self.ser.is_open:
                #     self.ser.close()
                #     print("串口已关闭")

    def run(self):
        # 接收数据
        if self.running:
            # 接收串口介绍数据
            if self.category == "receive":
                # 全局变量
                self.receive()
            # 发送串口发送数据
            else:
                self.send()
        pass

    def stop(self):

        self.running = False
        logger.warning(f"{self.category}串口{str(self.port)}连接线程停止")
        self.join()  # 等待线程结束

    def send(self):
        """
        发送数据
        :return:
        """
        logger.info(f"{self.category}串口{str(self.port)}开始发送数据")
        # while self.running:  # 循环接收数据
        #
        #     # date = datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        #     # 生成一个指定范围内的浮点数，例如 1.5 到 5.5
        #     datas = {}
        #     data = ""
        #     data = str(random.uniform(-100, 100))
        #     datas['data'] = data
        #     datas['index'] = self.index
        #     datas_str = json.dumps(datas)
        #     logger.info(f"{self.category}串口{str(self.port)}发送数据: {datas_str}")
        #     self.ser.write(datas_str.encode())
        #     time.sleep(float(self.config['Serial']['delay']))

    def binary_to_hex_for_each_4(self, binary_str):
        """
        根据二进制字符串的位数转成相应个数的16进制数
        :param binary_str:
        :return:
        """
        # 检查输入是否为有效的二进制字符串
        if not all(bit in '01' for bit in binary_str):
            raise ValueError("Input must be a binary string.")

        # 计算需要补充的零的数量
        padding_length = (4 - (len(binary_str) % 4)) % 4

        # 补零
        padded_str = '0' * padding_length + binary_str

        # 结果列表
        hex_values = []

        # 每4位分一组
        for i in range(0, len(padded_str), 4):
            # 取出当前的4位二进制数
            chunk = padded_str[i:i + 4]

            # 将4位二进制字符串转换为整数
            hex_value = int(chunk, 2)

            # 转换为16进制并去掉 '0x' 前缀，并转换为大写
            hex_values.append(hex(hex_value)[2:].upper())

        return hex_values

    def binary_to_hex_for_all(self, binary_str):
        """
        将8位二进制数转成二位16进制数
        :param binary_str:
        :return:
        """
        # 检查输入是否为有效的二进制字符串
        if not all(bit in '01' for bit in binary_str):
            raise ValueError("Input must be a binary string.")

        # 确保字符串长度为8位，前面补零
        binary_string = binary_str.zfill(8)

        # 将二进制字符串转换为整数
        decimal_value = int(binary_string, 2)

        # 将整数转换为两位的十六进制字符串
        # 使用 zfill(2) 确保输出为两位，如果是单个十六进制数字则前面补零
        hex_value = hex(decimal_value)[2:].zfill(2).upper()  # 将十六进制转换为大写并保持两位数
        return hex_value

    def calculate_crc(self, data: bytes) -> bytes:
        '''计算Modbus RTU CRC-16，小端返回'''
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

    def build_frame(self, slave_id, function_code, return_bytes_nums, data_hex_list, struct_type):
        '''
        构造完整 Modbus RTU 报文（包含CRC）
        return_bytes_nums:为16进制字符  返回字节数 :有时候响应报文会不带返回字节数，这时候使用data_bytes的长度充当return_bytes_nums,并且不pack return_bytes_nums
        data_hex_list: 4个字节数据，十六进制字符串，如 ['00', '00', '00', 'FF']
        struct_type B是一个字节 H是两个字节
        返回: 完整的 bytes 报文
        '''
        try:
            # 字符串转整数
            slave_id = int(slave_id, 16)
            function_code = int(function_code, 16)
            if return_bytes_nums is not None:
                return_bytes_nums = int(return_bytes_nums, 16)
            # logger.info(f"slave_id：{slave_id}|function_code：{function_code}|data_hex_list: {data_hex_list}")
            data_bytes = [int(x, 16) for x in data_hex_list]
            # logger.info(f"data_hex_list: {data_hex_list}|data_bytes: {data_bytes}")
            # 组装帧
            if return_bytes_nums is not None:
                pack_struct = ">B B B"
                is_pack_return_bytes_nums = True
            else:
                pack_struct = ">B B"
                return_bytes_nums = len(data_bytes)
                is_pack_return_bytes_nums = False

            if isinstance(struct_type, str) and struct_type.upper() == "B":

                for i in range(return_bytes_nums):
                    pack_struct += " B"
                pass
                # logger.info(
                #     f"struct_type: {struct_type}|struct_type is B | pack_struct: {pack_struct}| return_bytes_nums: {return_bytes_nums}")
            else:
                # logger.info(f"struct_type: {struct_type}|struct_type is H")
                for i in range(return_bytes_nums // 2):
                    pack_struct += " H"
                pass
                # logger.info(
                #     f"struct_type: {struct_type}|struct_type is H | pack_struct: {pack_struct} |  return_bytes_nums: {return_bytes_nums}")
            if is_pack_return_bytes_nums:
                frame = struct.pack(pack_struct, slave_id, function_code, return_bytes_nums, *data_bytes)
            else:
                frame = struct.pack(pack_struct, slave_id, function_code, *data_bytes)
            crc = self.calculate_crc(frame)
            str_frame = frame.hex()
            str_crc = crc.hex()
            logger.info(f"构造响应报文：frame: {frame} , {str_frame}|crc: {crc} , {str_crc}")
            return frame + crc
        except Exception as e:
            logger.error(f"{time_util.get_format_from_time(time.time())}-{self.port}-构造报文出错: {e}")
            return None

    def receive(self):
        """
        接收数据
        :return:
        """

        logger.info(f"{self.category}串口{str(self.port)}开始接收数据")
        while self.running:
            # 读取数据（非阻塞方式）
            count = self.ser.inWaiting()
            if count != 0:
                # data = self.ser.readline()

                data = self.ser.read(count)
                if data:
                    try:

                        unpack_send = struct.unpack("> B B B B B B B B", data)
                        send_struct = {}
                        send_struct['slave_id'] = unpack_send[0]
                        send_struct['function_code'] = unpack_send[1]
                        send_struct['data'] = [unpack_send[2], unpack_send[3], unpack_send[4], unpack_send[5]]
                        send_struct['crc'] = [unpack_send[-2], unpack_send[-1]]
                        logger.info(f"{self.category}串口{str(self.port)}接收数据: {data}|未转义：{data.hex()}|{send_struct}")
                        function_code_int = int(send_struct['function_code'], 16) if isinstance(
                            send_struct['function_code'], str) else \
                            send_struct['function_code']
                        slave_id_int = int(send_struct['slave_id'], 16) if isinstance(send_struct['slave_id'], str) else \
                            send_struct['slave_id']
                        # logger.info(f"slave_id_handle:{slave_id_int}")
                        return_bytes = 0
                        if slave_id_int > 16:
                            # 鼠笼内传感器
                            mouse_cage_number = slave_id_int // 16
                            match (slave_id_int % 16):
                                case 0:
                                    # 通讯HUB1模块
                                    pass
                                case 1:
                                    # ENM 鼠笼环境监控模块
                                    # 功能码
                                    match function_code_int:
                                        case 1:
                                            """
                                            11 01 X
                                            读输出端口状态信息
                                            参数长度：2
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='1',
                                                                            data_hex_list=[
                                                                                self.binary_to_hex_for_all("00000010")
                                                                            ],
                                                                            struct_type="B"
                                                                            )
                                            pass
                                        case 2:
                                            """
                                            11 02 X
                                            读传感器状态信息
                                            参数长度：2
                                            """

                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='1',
                                                                            data_hex_list=[
                                                                                self.binary_to_hex_for_all("00000110")

                                                                            ],
                                                                            struct_type="B"
                                                                            )
                                            pass
                                        case 3:
                                            """
                                            11 03 X
                                            读配置寄存器
                                            参数长度：7
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='6',

                                                                            data_hex_list=[
                                                                                "0xB0A1",
                                                                                "0xC0F1",
                                                                                "0xA0DC",
                                                                            ],
                                                                            struct_type="H"

                                                                            )
                                            pass
                                        case 4:
                                            """
                                            11 04 X
                                            读传感器测量值
                                            参数长度：13
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='C',

                                                                            data_hex_list=random.choice([
                                                                                [
                                                                                #    有符号数 可以是负数
                                                                                self.binary_to_hex_for_all("10001001"),
                                                                                "0x05",
                                                                                "0x1F",
                                                                                "0x01",
                                                                                "0xAC",
                                                                                "0x03",

                                                                                "0x04",
                                                                                "0x1a",
                                                                                "0x01",

                                                                                "0x14",
                                                                                "0x21",
                                                                                "0x07",

                                                                                ],
                                                                                [
                                                                                    self.binary_to_hex_for_all(
                                                                                        "00001001"),
                                                                                    "0x02",
                                                                                    "0x3d",
                                                                                    "0x06",
                                                                                    "0xbd",
                                                                                    "0x07",

                                                                                    "0x05",
                                                                                    "0x2c",
                                                                                    "0x09",

                                                                                    "0x17",
                                                                                    "0x3a",
                                                                                    "0x06",

                                                                                ],
                                                                                [
                                                                                    self.binary_to_hex_for_all(
                                                                                        "10101011"),
                                                                                    "0x09",
                                                                                    "0x5d",
                                                                                    "0x09",
                                                                                    "0xbe",
                                                                                    "0x01",

                                                                                    "0x08",
                                                                                    "0xe3",
                                                                                    "0x06",

                                                                                    "0x21",
                                                                                    "0x35",
                                                                                    "0x01",

                                                                                ],
                                                                                [
                                                                                    self.binary_to_hex_for_all(
                                                                                        "00101011"),
                                                                                    "0x03",
                                                                                    "0x5d",
                                                                                    "0x02",
                                                                                    "0xee",
                                                                                    "0x07",

                                                                                    "0x08",
                                                                                    "0x42",
                                                                                    "0x05",

                                                                                    "0x31",
                                                                                    "0x15",
                                                                                    "0x08",

                                                                                ],
                                                                                [
                                                                                    self.binary_to_hex_for_all(
                                                                                        "10111011"),
                                                                                    "0x01",
                                                                                    "0x11",
                                                                                    "0x03",
                                                                                    "0x21",
                                                                                    "0x06",

                                                                                    "0x03",
                                                                                    "0x2d",
                                                                                    "0x06",

                                                                                    "0x35",
                                                                                    "0x27",
                                                                                    "0x02",

                                                                                ],
                                                                            ]),
                                                                            struct_type="B"
                                                                            )
                                            pass
                                        case 5:
                                            """
                                            11 05 X
                                            写从机单个开关量输出（ON/OFF）
                                            参数长度：4
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums=None,
                                                                            data_hex_list=[
                                                                                "0x00",
                                                                                "0x01",
                                                                                "0xFF",
                                                                                "0x00",
                                                                            ],
                                                                            struct_type="B"
                                                                            )
                                            pass
                                        case 6:
                                            """
                                            11 06 X
                                            写单个保持寄存器
                                            参数长度：4
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums=None,
                                                                            data_hex_list=[
                                                                                "0x00",
                                                                                "0x01",
                                                                                "0x00",
                                                                                "0x2F",
                                                                            ],
                                                                            struct_type="B"
                                                                            )
                                            pass
                                        case 17:
                                            """
                                            11 11 X
                                            读取模块ID信息等
                                            参数长度：17
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='11',
                                                                            data_hex_list=[
                                                                                "0xAFCF",
                                                                                "0x2311",
                                                                                "0xFF32",
                                                                                "0xABCD",
                                                                                "0xE21F",
                                                                                "0xDDDD",
                                                                                "0x1234",
                                                                                "0x1111",
                                                                            ],
                                                                            struct_type="H")
                                            pass
                                        case _:
                                            """
                                            11 _ X
                                            没有该function_code
                                            """
                                            logger.warning(
                                                f"传感器slave_id:{slave_id_int:X}没有该function_code{function_code_int:X},把接收的报文直接传回去。")
                                            return_bytes = data
                                            pass
                                    pass
                                case 2:
                                    # DWM 饮水监控模块
                                    # 功能码
                                    match function_code_int:

                                        case 2:
                                            """
                                            12 02 X
                                            读传感器状态信息
                                            参数长度：2
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='1',
                                                                            data_hex_list=[
                                                                                self.binary_to_hex_for_all("00000001")

                                                                            ],
                                                                            struct_type="B"
                                                                            )
                                            pass

                                        case 4:
                                            """
                                            12 04 X
                                            读传感器测量值
                                            参数长度：5
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='4',

                                                                            data_hex_list=[

                                                                                "0x00",
                                                                                "0x00",
                                                                                "0x00",
                                                                                "0xAC",

                                                                            ],
                                                                            struct_type="B"
                                                                            )
                                            pass

                                        case 17:
                                            """
                                            12 11 X
                                            读取模块ID信息等
                                            参数长度：17
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='11',
                                                                            data_hex_list=[
                                                                                "0xAFCF",
                                                                                "0x2311",
                                                                                "0xFF32",
                                                                                "0xABCD",
                                                                                "0xE21F",
                                                                                "0xDDDD",
                                                                                "0x1234",
                                                                                "0x1111",
                                                                            ],
                                                                            struct_type="H")
                                            pass
                                        case _:
                                            """
                                            12 _ X
                                            没有该function_code
                                            """
                                            logger.warning(
                                                f"传感器slave_id:{slave_id_int:X}没有该function_code{function_code_int:X},把接收的报文直接传回去。")
                                            return_bytes = data
                                            pass
                                    pass
                                case 3:
                                    # EM 进食监控模块
                                    # 功能码
                                    match function_code_int:
                                        case 1:
                                            """
                                            13 01 X
                                            读输出端口状态信息
                                            参数长度：2
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='1',
                                                                            data_hex_list=[
                                                                                self.binary_to_hex_for_all("00000001")

                                                                            ],
                                                                            struct_type="B"
                                                                            )
                                            pass
                                        case 2:
                                            """
                                            13 02 X
                                            读传感器状态信息
                                            参数长度：2
                                            """

                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='1',
                                                                            data_hex_list=[
                                                                                self.binary_to_hex_for_all("00000010")

                                                                            ],
                                                                            struct_type="B"
                                                                            )
                                            pass

                                        case 4:
                                            """
                                            13 04 X
                                            读传感器测量值
                                            参数长度：5
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='4',

                                                                            data_hex_list=[
                                                                                "0x00",
                                                                                "0x00",
                                                                                "0x00",
                                                                                "0xBD",

                                                                            ],
                                                                            struct_type="B"
                                                                            )
                                            pass
                                        case 5:
                                            """
                                            13 05 X
                                            写从机单个开关量输出（ON/OFF）
                                            参数长度：4
                                                                                          """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums=None,
                                                                            data_hex_list=[
                                                                                "0x00",
                                                                                "0x01",
                                                                                "0xFF",
                                                                                "0x00",
                                                                            ],
                                                                            struct_type="B"
                                                                            )
                                            pass
                                        case 17:
                                            """
                                            13 11 X
                                            读取模块ID信息等
                                            参数长度：17
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='11',
                                                                            data_hex_list=[
                                                                                "0xAFCF",
                                                                                "0x2311",
                                                                                "0xFF32",
                                                                                "0xABCD",
                                                                                "0xE21F",
                                                                                "0xDDDD",
                                                                                "0x1234",
                                                                                "0x1111",
                                                                            ],
                                                                            struct_type="H")
                                            pass
                                        case _:
                                            """
                                            13 _ X
                                            没有该function_code
                                            """
                                            logger.warning(
                                                f"传感器slave_id:{slave_id_int:X}没有该function_code{function_code_int:X},把接收的报文直接传回去。")
                                            return_bytes = data
                                            pass
                                    pass

                                case 4:
                                    # WM 称重模块
                                    # 功能码
                                    match function_code_int:

                                        case 2:
                                            """
                                            14 02 X
                                            读传感器状态信息
                                            参数长度：2
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='1',
                                                                            data_hex_list=[
                                                                                self.binary_to_hex_for_all("00000001")

                                                                            ],
                                                                            struct_type="B"
                                                                            )
                                            pass

                                        case 4:
                                            """
                                            14 04 X
                                            读传感器测量值
                                            单值参数长度：4；30点参数长度：0x78
                                            """
                                            request_data = [
                                                f"{item:02X}" if isinstance(item, int) else
                                                str(item).strip().lower().replace("0x", "").upper().zfill(2)
                                                for item in send_struct.get('data', [])
                                            ]
                                            if request_data == ["04", "01", "00", "3C"]:
                                                return_bytes = self.build_frame(
                                                    slave_id=f"{slave_id_int:X}",
                                                    function_code=f"{function_code_int:X}",
                                                    return_bytes_nums='78',
                                                    data_hex_list=["0x00", "0x00", "0x12", "0x34"] * 30,
                                                    struct_type="B"
                                                )
                                            else:
                                                return_bytes = self.build_frame(
                                                    slave_id=f"{slave_id_int:X}",
                                                    function_code=f"{function_code_int:X}",
                                                    return_bytes_nums='4',
                                                    data_hex_list=[
                                                        "0x00",
                                                        "0x00",
                                                        "0x00",
                                                        "0xCE",
                                                    ],
                                                    struct_type="B"
                                                )
                                            pass

                                        case 17:
                                            """
                                            14 11 X
                                            读取模块ID信息等
                                            参数长度：17
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='11',
                                                                            data_hex_list=[
                                                                                "0xAFCF",
                                                                                "0x2311",
                                                                                "0xFF32",
                                                                                "0xABCD",
                                                                                "0xE21F",
                                                                                "0xDDDD",
                                                                                "0x1234",
                                                                                "0x1111",
                                                                            ],
                                                                            struct_type="H")
                                            pass

                                        case _:
                                            """
                                            14 _ X
                                            没有该function_code
                                            """
                                            logger.warning(
                                                f"传感器slave_id:{slave_id_int:X}没有该function_code{function_code_int:X},把接收的报文直接传回去。")
                                            return_bytes = data
                                            pass
                                    pass
                                case _:
                                    logger.warning(f"没有该鼠笼内传感器地址slave_id:{slave_id_int:X},把接收的报文直接传回去。")
                                    return_bytes = data
                                    pass
                            pass
                        else:
                            # 非鼠笼内传感器
                            match (slave_id_int % 16):
                                case 1:
                                    # 通讯模块	通讯CI-BUS
                                    pass
                                case 2:
                                    # UFC	气流控制模块
                                    # 功能码
                                    match function_code_int:
                                        case 1:
                                            """
                                            02 01 X
                                            读输出端口状态信息
                                            参数长度：3
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='2',
                                                                            data_hex_list=random.choice([
                                                                                [
                                                                                    self.binary_to_hex_for_all(
                                                                                        "00000010"),
                                                                                    self.binary_to_hex_for_all(
                                                                                        "10011001")
                                                                                ],
                                                                                [
                                                                                    self.binary_to_hex_for_all(
                                                                                        "00000001"),
                                                                                    self.binary_to_hex_for_all(
                                                                                        "11100011")
                                                                                ],
                                                                                [
                                                                                    self.binary_to_hex_for_all(
                                                                                        "00000000"),
                                                                                    self.binary_to_hex_for_all(
                                                                                        "00101100")
                                                                                ],
                                                                            ]),
                                                                            struct_type="B"
                                                                            )
                                            pass
                                        case 2:
                                            """
                                            02 02 X
                                            读传感器状态信息
                                            参数长度：2
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='1',
                                                                            data_hex_list=random.choice([
                                                                                [
                                                                                    self.binary_to_hex_for_all(
                                                                                        "00011111")
                                                                                ],
                                                                                [
                                                                                    self.binary_to_hex_for_all(
                                                                                        "00001010")
                                                                                ],
                                                                            ]),
                                                                            struct_type="B"
                                                                            )
                                            pass
                                        case 3:
                                            """
                                            02 03 X
                                            读配置寄存器
                                            参数长度：7
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='6',

                                                                            data_hex_list=random.choice([
                                                                                [
                                                                                    "0x0000",
                                                                                    "0x0021",
                                                                                    "0x003d",
                                                                                ],
                                                                                [
                                                                                    "0x0001",
                                                                                    "0x0063",
                                                                                    "0x0048",
                                                                                ],
                                                                            ]),
                                                                            struct_type="H"

                                                                            )
                                        case 4:
                                            """
                                            02 04 X
                                            读传感器测量值
                                            参数长度：13
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='C',

                                                                            data_hex_list=random.choice([
                                                                                [
                                                                                    "0x11",
                                                                                    "0x3d",
                                                                                    "0x08",
                                                                                    "0x02",
                                                                                    "0x3a",
                                                                                    "0x01",
                                                                                    "0x2d",
                                                                                    "0x05",
                                                                                    "0xde",
                                                                                    "0xac",
                                                                                    "0x2b",
                                                                                    "0x32",
                                                                                ],
                                                                                [
                                                                                    "0x01",
                                                                                    "0x1F",
                                                                                    "0x05",
                                                                                    "0x08",
                                                                                    "0x1F",
                                                                                    "0x04",
                                                                                    "0x1a",
                                                                                    "0x01",
                                                                                    "0xAC",
                                                                                    "0xB1",
                                                                                    "0xC2",
                                                                                    "0x41",
                                                                                ],
                                                                                [
                                                                                    "0x03",
                                                                                    "0x2F",
                                                                                    "0x07",
                                                                                    "0x11",
                                                                                    "0x2f",
                                                                                    "0x07",
                                                                                    "0x2d",
                                                                                    "0x06",
                                                                                    "0xbe",
                                                                                    "0xad",
                                                                                    "0xcd",
                                                                                    "0xa1",
                                                                                ],

                                                                            ]),
                                                                            struct_type="B"
                                                                            )
                                            pass
                                        case 5:
                                            """
                                               02 05 X
                                               写从机单个开关量输出（ON/OFF）
                                               参数长度：4
                                             """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums=None,
                                                                            data_hex_list=[
                                                                                "0x00",
                                                                                "0x02",
                                                                                "0x00",
                                                                                "0xFF",
                                                                            ],
                                                                            struct_type="B"
                                                                            )
                                            pass
                                        case 6:
                                            """
                                            02 06 X
                                                写单个保持寄存器
                                                参数长度：4
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums=None,
                                                                            data_hex_list=[
                                                                                "0x00",
                                                                                "0x00",
                                                                                "0x00",
                                                                                "0x2F",
                                                                            ],
                                                                            struct_type="B"
                                                                            )

                                            pass
                                        case 17:
                                            """
                                            02 11 X
                                            读取模块ID信息等
                                            参数长度：17
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='11',
                                                                            data_hex_list=[
                                                                                "0xAFCF",
                                                                                "0x2311",
                                                                                "0xFF32",
                                                                                "0xABCD",
                                                                                "0xE21F",
                                                                                "0xDDDD",
                                                                                "0x1234",
                                                                                "0x1111",
                                                                            ],
                                                                            struct_type="H"
                                                                            )
                                            pass
                                        case _:
                                            """
                                            02 _ X
                                            没有该function_code
                                            """
                                            logger.warning(
                                                f"传感器slave_id:{slave_id_int:X}没有该function_code{function_code_int:X},把接收的报文直接传回去。")
                                            return_bytes = data
                                            pass

                                    pass
                                case 3:
                                    # UGC	二氧化碳含量模块
                                    # 功能码
                                    match function_code_int:
                                        case 1:
                                            """
                                            03 01 X
                                            读输出端口状态信息
                                            参数长度：2
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums=None,
                                                                            data_hex_list=[
                                                                                self.binary_to_hex_for_all("00000001"),
                                                                                self.binary_to_hex_for_all("10101110")
                                                                            ],
                                                                            struct_type="B"
                                                                            )
                                            pass
                                        case 2:
                                            """
                                            03 02 X
                                            读输传感器状态信息
                                            参数长度：3
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='2',
                                                                            data_hex_list=[
                                                                                self.binary_to_hex_for_all("00000001"),
                                                                                self.binary_to_hex_for_all("11000111")
                                                                            ],
                                                                            struct_type="B"
                                                                            )
                                            pass
                                        case 3:
                                            """
                                                   03 03 X
                                                   读配置寄存器
                                                   参数长度：9
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='8',

                                                                            data_hex_list=[
                                                                                "0x0001",
                                                                                "0x0063",
                                                                                "0x0048",
                                                                                "0x0028",
                                                                            ],
                                                                            struct_type="H"

                                                                            )
                                            pass
                                        case 4:
                                            """
                                           03 04 X
                                           读传感器测量值
                                            参数长度：11
                                           """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='A',#14的16进制就是20 现在改为10的16进制就是A

                                                                            data_hex_list=[
                                                                                "0x01",
                                                                                "0x1F",
                                                                                "0x05",
                                                                                "0x08",
                                                                                "0x1F",
                                                                                "0x04",
                                                                                "0x1a",
                                                                                "0x01",
                                                                                "0x3D",
                                                                                "0x02",
                                                                            ],
                                                                            struct_type="B"
                                                                            )
                                            pass
                                        case 5:
                                            """
                                           03 05 X
                                           写从机单个开关量输出（ON/OFF）
                                           参数长度：4
                                         """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums=None,
                                                                            data_hex_list=[
                                                                                "0x00",
                                                                                "0x02",
                                                                                "0xFF",
                                                                                "0x00",
                                                                            ],
                                                                            struct_type="B"
                                                                            )
                                            pass
                                        case 6:
                                            """
                                            03 06 X
                                                写单个保持寄存器
                                                参数长度：4
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums=None,
                                                                            data_hex_list=[
                                                                                "0x00",
                                                                                "0x13",
                                                                                "0x00",
                                                                                "0x01",
                                                                            ],
                                                                            struct_type="B"
                                                                            )
                                            pass
                                        case 17:
                                            """
                                           03 11 X
                                               读取模块ID信息等
                                            参数长度：17
                                           """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='11',
                                                                            data_hex_list=[
                                                                                "0xAFCF",
                                                                                "0x2311",
                                                                                "0xFF32",
                                                                                "0xABCD",
                                                                                "0xE21F",
                                                                                "0xDDDD",
                                                                                "0x1234",
                                                                                "0x1111",
                                                                            ],
                                                                            struct_type="H")

                                            pass
                                        case _:
                                            """
                                            03 _ X
                                            没有该function_code
                                            """
                                            logger.warning(
                                                f"传感器slave_id:{slave_id_int:X}没有该function_code{function_code_int:X},把接收的报文直接传回去。")
                                            return_bytes = data
                                            pass
                                    pass
                                case 4:
                                    # ZOS	氧气含量测量模块
                                    # 功能码
                                    match function_code_int:
                                        case 1:
                                            """
                                            04 01 X
                                            读输出端口状态信息
                                            参数长度：3
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='1',
                                                                            data_hex_list=
                                                                            random.choice([
                                                                                [
                                                                                    self.binary_to_hex_for_all(
                                                                                        "00000010")
                                                                                ],
                                                                                [
                                                                                    self.binary_to_hex_for_all(
                                                                                        "00000001")
                                                                                ],
                                                                                [
                                                                                    self.binary_to_hex_for_all(
                                                                                        "00000011")
                                                                                ],
                                                                            ]),
                                                                            struct_type="B"
                                                                            )

                                            pass
                                        case 2:
                                            """
                                           04 02 X
                                           读输传感器状态信息
                                           参数长度：2
                                           """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='1',
                                                                            data_hex_list=[
                                                                                self.binary_to_hex_for_all("00000010")

                                                                            ],
                                                                            struct_type="B"
                                                                            )
                                            pass
                                        case 3:
                                            """
                                                   04 03 X
                                                   读配置寄存器
                                                   参数长度：3
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='2',

                                                                            data_hex_list=[
                                                                                "0x0001",

                                                                            ],
                                                                            struct_type="H"

                                                                            )

                                            pass
                                        case 4:
                                            """
                                            04 04 X
                                            读传感器测量值 - 新协议28字节
                                            """
                                            return_bytes = self.build_frame(
                                                slave_id=f"{slave_id_int:X}",
                                                function_code=f"{function_code_int:X}",
                                                return_bytes_nums='1C',  # 28字节 = 0x1C
                                                data_hex_list=random.choice([
                                                    [
                                                        # 氧分压 198 → 19.8
                                                        "0x00", "0x00", "0x00", "0xC6",
                                                        # 温度1 264 → 26.4 °C
                                                        "0x00", "0x00", "0x01", "0x08",
                                                        # 气体压力 10010 → 100.10
                                                        "0x00", "0x00", "0x27", "0x1A",
                                                        # 氧浓度 197700 → 19.7700 %
                                                        "0x00", "0x03", "0x04", "0x44",
                                                        # 故障码 0
                                                        "0x00", "0x00", "0x00", "0x00",
                                                        # 温度2 252 → 25.2 °C
                                                        "0x00", "0x00", "0x00", "0xFC",
                                                        # 湿度 567 → 56.7 %RH
                                                        "0x00", "0x00", "0x02", "0x37",
                                                    ],
                                                    [
                                                        # 氧分压 205 → 20.5
                                                        "0x00", "0x00", "0x00", "0xCD",
                                                        # 温度1 271 → 27.1 °C
                                                        "0x00", "0x00", "0x01", "0x0F",
                                                        # 气体压力 10085 → 100.85
                                                        "0x00", "0x00", "0x27", "0x65",
                                                        # 氧浓度 205316 → 20.5316 %
                                                        "0x00", "0x03", "0x21", "0xF4",
                                                        # 故障码 0
                                                        "0x00", "0x00", "0x00", "0x00",
                                                        # 温度2 258 → 25.8 °C
                                                        "0x00", "0x00", "0x01", "0x02",
                                                        # 湿度 552 → 55.2 %RH
                                                        "0x00", "0x00", "0x02", "0x28",
                                                    ],
                                                ]),
                                                struct_type="B"
                                            )
                                        case 5:
                                            """
                                             04 05 X
                                             写从机单个开关量输出（ON/OFF）
                                             参数长度：4
                                                                                   """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums=None,
                                                                            data_hex_list=[
                                                                                "0x00",
                                                                                "0x00",
                                                                                "0xFF",
                                                                                "0x00",
                                                                            ],
                                                                            struct_type="B"
                                                                            )
                                            pass
                                        case 6:
                                            """
                                            04 06 X
                                                写单个保持寄存器
                                                参数长度：4
                                            """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums=None,
                                                                            data_hex_list=[
                                                                                "0x00",
                                                                                "0x01",
                                                                                "0x00",
                                                                                "0x2F",
                                                                            ],
                                                                            struct_type="B"
                                                                            )
                                            pass
                                            pass
                                        case 17:
                                            """
                                           04 11 X
                                               读取模块ID信息等
                                            参数长度：17
                                           """
                                            return_bytes = self.build_frame(slave_id=f"{slave_id_int:X}",
                                                                            function_code=f"{function_code_int:X}",
                                                                            return_bytes_nums='11',
                                                                            data_hex_list=[
                                                                                "0xAFCF",
                                                                                "0x2311",
                                                                                "0xFF32",
                                                                                "0xABCD",
                                                                                "0xE21F",
                                                                                "0xDDDD",
                                                                                "0x1234",
                                                                                "0x1111",
                                                                            ],
                                                                            struct_type="H")

                                            pass

                                        case _:
                                            """
                                            04 _ X
                                            没有该function_code
                                            """
                                            logger.warning(
                                                f"传感器slave_id:{slave_id_int:X}没有该function_code{function_code_int:X},把接收的报文直接传回去。")
                                            return_bytes = data
                                            pass
                                    pass
                                case _:
                                    logger.warning(f"没有该鼠笼外传感器地址slave_id:{slave_id_int:X},把接收的报文直接传回去。")
                                    return_bytes = data
                            pass
                            pass

                        # 根据发送来的数据进行响应
                        #测试timeout
                        # if return_bytes!=data:
                        # 发送数据
                        # logger.critical(f"{return_bytes}")
                        self.ser.write(return_bytes)

                    except UnicodeDecodeError:
                        logger.info(f"{self.category}串口{str(self.port)}接收原始字节: {data.hex()}")
                    except Exception as e:
                        logger.error(f"{self.category}串口{str(self.port)}<接收原始字节>: {data.hex()},错误原因：{e}")
                time.sleep(1)  # 避免CPU占用过高


def main():
    # 加载日志配置
    # logger.add(
    #     "./log/comm/COM_{time:YYYY-MM-DD}.log",
    #     rotation="00:00",
    #     retention="30 days",
    #     enqueue=True,
    #     format="{time:YYYY-MM-DD HH:mm:ss} | {level} |{process.name} | {thread.name} |  {name} : {module}:{line} | {message}",
    # filter = lambda record: record["extra"].get("category") == "response_modbus_logger"
    # )
    logger.info(f"{'-' * 30}response_start{'-' * 30}")
    try:
        communication_received_thread = communication(category="receive", index=0)
        communication_received_thread.start()
    except Exception as e:
        logger.error(f"模拟响应串口出错！:{e}")
    pass

