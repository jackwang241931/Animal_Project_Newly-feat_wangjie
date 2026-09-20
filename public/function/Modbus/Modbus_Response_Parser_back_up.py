"""
响应报文解析
"""
import struct
import time

from loguru import logger

from public.function.Modbus.Modbus_Type import Modbus_Slave_Ids, Modbus_Slave_Type
from public.util.time_util import time_util

#logger = logger.bind(category="deep_camera_logger")
def get_module_name(slave_id):
    """
    根据slave_id寻找模块名称 例如02为UFC 03为UGC
    :param slave_id:
    :return:
    """

    slave_id_int = int(slave_id, 16)
    # print(f"slave_id_int:{slave_id_int}")
    if slave_id_int > 16:
        mouse_cage_number = slave_id_int // 16
        # 鼠笼内传感器
        for type in Modbus_Slave_Type.Each_Mouse_Cage.value:
            if type.value['int'] == (slave_id_int % 16):
                # logger.info(f"type.value['name'] Each:{type.value['name']}|mouse_cage_number:{mouse_cage_number}")
               return type.value['name']

        else:
            logger.info(f"type.value['name'] Each:{'ERROR'}|mouse_cage_number:{mouse_cage_number}")
            return None
        pass
    else:
        # 非鼠笼内传感器
        for type in Modbus_Slave_Type.Not_Each_Mouse_Cage.value:
            if type.value['int'] == (slave_id_int % 16):
                # logger.info(f"type.value['name'] Not_Each:{type.value['name']}")
                return type.value['name']
        else:
            logger.info(f"type.value['name'] Not_Each:{'ERROR'}")
            return None
        pass
class Modbus_Response_Parser():
    """
响应报文解析
    """

    def __init__(self, slave_id, function_code, response, response_hex,
                 ):
        """

        :param slave_id: 地址码
        :param function_code: 功能码
        :param response: 响应报文
        :param response._hex: 响应报文16进制
        """

        self.slave_id = slave_id
        self.function_code = function_code
        self.response = response
        self.response_hex = response_hex

        self.response_parser: Modbus_Response_Diffent_Type_Each_Mouse_Cage | Modbus_Response_Diffent_Type_Not_Each_Mouse_Cage = None
        self.init_self()
        pass

    def init_self(self):
        self.response_parser = self.get_reponse_parser()

    def parser(self):
        return self.response_parser.specific_response.function_code_parser()

    def get_reponse_parser(self):
        """
        因为鼠笼传感器的基准与前面几个传感器的基准一致，所以需要根据前面第三位来辨别。
        :return:
        """
        response_parser = None
        slave_id_int = int(self.slave_id, 16)
        # print(f"slave_id_int:{slave_id_int}")
        if slave_id_int > 16:
            mouse_cage_number = slave_id_int // 16
            # 鼠笼内传感器
            for type in Modbus_Slave_Type.Each_Mouse_Cage.value:
                if type.value['int'] == (slave_id_int % 16):
                    # logger.info(f"type.value['name'] Each:{type.value['name']}|mouse_cage_number:{mouse_cage_number}")
                    response_parser = Modbus_Response_Diffent_Type_Each_Mouse_Cage(name=type.value['name'],
                                                                                   origin_slave_id=self.slave_id,
                                                                                   mouse_cage_number=mouse_cage_number,
                                                                                   slave_id=f"{(slave_id_int % 16):02X}",
                                                                                   response=self.response,
                                                                                   response_hex=self.response_hex,
                                                                                   function_code=self.function_code,

                                                                                   )
                    break
            else:
                logger.info(f"type.value['name'] Each:{'ERROR'}|mouse_cage_number:{mouse_cage_number}")
                response_parser = Modbus_Response_Diffent_Type_Each_Mouse_Cage(name='ERROR',
                                                                               origin_slave_id=self.slave_id,
                                                                               mouse_cage_number=mouse_cage_number,
                                                                               slave_id=f"{(slave_id_int % 16):02X}",
                                                                               response=self.response,
                                                                               response_hex=self.response_hex,
                                                                               function_code=self.function_code,

                                                                               )
                pass
            pass
        else:
            # 非鼠笼内传感器
            for type in Modbus_Slave_Type.Not_Each_Mouse_Cage.value:
                if type.value['int'] == (slave_id_int % 16):
                    # logger.info(f"type.value['name'] Not_Each:{type.value['name']}")
                    response_parser = Modbus_Response_Diffent_Type_Not_Each_Mouse_Cage(name=type.value['name'],
                                                                                       slave_id=self.slave_id,
                                                                                       response=self.response,
                                                                                       response_hex=self.response_hex,
                                                                                       function_code=self.function_code,

                                                                                       )
                    break
            else:
                logger.info(f"type.value['name'] Not_Each:{'ERROR'}")
                response_parser = Modbus_Response_Diffent_Type_Not_Each_Mouse_Cage(name='ERROR',
                                                                                   slave_id=self.slave_id,
                                                                                   response=self.response,
                                                                                   response_hex=self.response_hex,
                                                                                   function_code=self.function_code,

                                                                                   )
            pass
        return response_parser
        pass


class Modbus_Response_Diffent_Type_Each_Mouse_Cage():
    """
    在寻找到Each_Mouse_Cage不同类别的传感器的类
    """

    def __init__(self, name, origin_slave_id, mouse_cage_number, slave_id, response,
                 response_hex, function_code):

        self.name = name
        self.origin_slave_id = origin_slave_id
        self.mouse_cage_number = mouse_cage_number

        self.slave_id = slave_id
        self.response = response
        self.response_hex = response_hex
        self.function_code = function_code
        self.specific_response = None
        self.init_modbus()

    def init_modbus(self):
        """
        根据传感器名称实例化具体的那个传感器的解析类
        :return:
        """
        if self.name == Modbus_Slave_Ids.ENM.value['name']:

            self.specific_response = Modbus_Response_ENM(
                origin_slave_id=self.origin_slave_id,
                mouse_cage_number=self.mouse_cage_number,
                slave_id=self.slave_id,
                response=self.response,
                response_hex=self.response_hex,
                function_code=self.function_code,

            )
            pass
        elif self.name == Modbus_Slave_Ids.EM.value['name']:
            self.specific_response = Modbus_Response_EM(
                origin_slave_id=self.origin_slave_id,
                mouse_cage_number=self.mouse_cage_number,
                slave_id=self.slave_id,
                response=self.response,
                response_hex=self.response_hex,
                function_code=self.function_code,

            )
            pass
            pass
        elif self.name == Modbus_Slave_Ids.DWM.value['name']:
            self.specific_response = Modbus_Response_DWM(
                origin_slave_id=self.origin_slave_id,
                mouse_cage_number=self.mouse_cage_number,
                slave_id=self.slave_id,
                response=self.response,
                response_hex=self.response_hex,
                function_code=self.function_code,

            )
            pass
            pass
        elif self.name == Modbus_Slave_Ids.WM.value['name']:
            self.specific_response = Modbus_Response_WM(
                origin_slave_id=self.origin_slave_id,
                mouse_cage_number=self.mouse_cage_number,
                slave_id=self.slave_id,
                response=self.response,
                response_hex=self.response_hex,
                function_code=self.function_code,

            )
            pass
            pass
        else:
            self.specific_response = Modbus_Response_NOT_EXISTENCE(
                origin_slave_id=self.origin_slave_id,
                mouse_cage_number=self.mouse_cage_number,
                slave_id=self.slave_id,
                response=self.response,
                response_hex=self.response_hex,
                function_code=self.function_code,

            )
            pass

    pass


class Modbus_Response_Diffent_Type_Not_Each_Mouse_Cage():
    """
    在寻找到Not_Each_Mouse_Cage不同类别的传感器的类
    """

    def __init__(self, name, slave_id, response,
                 response_hex, function_code

                 ):

        self.name = name
        self.slave_id = slave_id
        self.response = response
        self.response_hex = response_hex
        self.function_code = function_code
        self.specific_response = None
        self.init_modbus()

    def init_modbus(self):
        """
        根据传感器名称实例化具体的那个传感器的解析类
        :return:
        """
        if self.name == Modbus_Slave_Ids.UFC.value['name']:

            self.specific_response = Modbus_Response_UFC(
                slave_id=self.slave_id,
                response=self.response,
                response_hex=self.response_hex,
                function_code=self.function_code,

            )
            pass
        elif self.name == Modbus_Slave_Ids.UGC.value['name']:
            self.specific_response = Modbus_Response_UGC(
                slave_id=self.slave_id,
                response=self.response,
                response_hex=self.response_hex,
                function_code=self.function_code,

            )
            pass
            pass
        elif self.name == Modbus_Slave_Ids.ZOS.value['name']:
            self.specific_response = Modbus_Response_ZOS(
                slave_id=self.slave_id,
                response=self.response,
                response_hex=self.response_hex,
                function_code=self.function_code,

            )
            pass
            pass
        else:
            self.specific_response = Modbus_Response_NOT_EXISTENCE(
                origin_slave_id=self.slave_id,
                mouse_cage_number=0,
                slave_id=self.slave_id,
                response=self.response,
                response_hex=self.response_hex,
                function_code=self.function_code,

            )
            pass

    pass


class Modbus_Response_Parents():
    def __init__(self, slave_id, response,
                 response_hex, function_code):
        self.slave_id = slave_id
        self.response = response
        self.response_hex = response_hex
        self.function_code = function_code

        self.pack_struct_slave_id_and_function_code = "> B B "
        self.pack_struct_crc = " B B"
        self.response_struct = {
            "slave_id": 0,
            "function_code": 0,
            "return_bytes_nums": 0,
            "data": list(),
            "crc": [0, 0]
        }

        pass

    def parser_response_pack(self, pack_struct, struct_type, is_pack_return_bytes_nums):
        """
        :param pack_struct: example: self.pack_struct_slave_id_and_function_code + pack_struct + self.pack_struct_crc= '> B B B B B B B'
        :return:
        """
        response_unpack = struct.unpack(
            self.pack_struct_slave_id_and_function_code + pack_struct + self.pack_struct_crc, self.response)
        # logger.info(f"响应报文{self.response}-{self.response_hex}-解析结构:{response_unpack}")
        self.response_struct['slave_id'] = response_unpack[0]
        # 返回字节数 :有时候响应报文会不带返回字节数，这时候改变数据比特的index,并且不unpack return_bytes_nums,这时候使用直接计算data长度充当return_bytes_nums
        if is_pack_return_bytes_nums:
            data_start_index = 3
            self.response_struct['return_bytes_nums'] = response_unpack[2]
            pass
        else:
            data_start_index = 2
            # 减去slave_id,function_code 和crc2位
            self.response_struct['return_bytes_nums'] = len(response_unpack) - 4
            pass
        self.response_struct['function_code'] = response_unpack[1]

        self.response_struct['crc'] = [response_unpack[-2], response_unpack[-1]]
        """
        给的数据位数和我们整个数据解析的个数对不对
        """
        # if len(response_unpack) != 5 + int(self.response_struct['return_bytes_nums']):
        #     logger.error(
        #         f"响应报文{self.response}-{self.response_hex}的数据位数{5 + int(self.response_struct['return_bytes_nums'])}和解析的总位数{len(response_unpack)}不一致！")
        #     return
        if isinstance(struct_type, str) and struct_type.upper() == "B":
            for i in range(int(self.response_struct['return_bytes_nums'])):
                self.response_struct['data'].append(response_unpack[data_start_index + i])
        else:
            for i in range(int(self.response_struct['return_bytes_nums']) // 2):
                self.response_struct['data'].append(response_unpack[data_start_index + i])

    def get_signed_int(self, bin_str):
        """
        根据8位二进制数获得有符号的整数
        :return:
        """
        # 确保 bin_str 是 8 位二进制数
        if len(bin_str) != 8 or not all(bit in '01' for bit in bin_str):
            raise ValueError("输入必须是一个8位二进制字符串")

        # 先将其转换为无符号整数
        unsigned_int = int(bin_str, 2)

        # 如果最高位是 1，则说明它是负数，计算补码
        if unsigned_int >= 128:  # 对于8位补码，数字 >= 128 是负数
            signed_int = unsigned_int - 256  # 转换为负数
        else:
            signed_int = unsigned_int

        return signed_int

    def int_to_8bit_binary(self, num_list):
        """
        将多个数字转换成多个8位二进制
        :param num:
        :return:
        """
        binary_str_list = []
        # 确保输入是一个整数
        for num in num_list:
            if not isinstance(num, int):
                raise ValueError("Input must be an integer")

            # 处理负数，使用补码表示：将数值加上256（2^8）
            if num < 0:
                num = (1 << 8) + num  # 256 + num

            # 将整数转换为二进制字符串，并填充为8位
            binary_str = bin(num & 0xFF)[2:].zfill(8)
            binary_str_list.append(binary_str)
        return binary_str_list
    def ieee754_single_to_float(self,binary_str):

        if len(binary_str) != 32:
            raise ValueError(f"{len(binary_str)}输入必须是 32 位二进制字符串")

        sign_bit = int(binary_str[0])
        exponent_bits = binary_str[1:9]

        mantissa_bits = binary_str[9:]


        exponent = int(exponent_bits, 2)

        if exponent == 0:
            if mantissa_bits == '0' * 23:
                return 0.0 if sign_bit == 0 else -0.0
            else:
                exponent = -126
                mantissa = int(mantissa_bits, 2) / (2 ** 23)
        elif exponent == 255:
            if mantissa_bits == '0' * 23:
                return float('inf') if sign_bit == 0 else float('-inf')
            else:
                return float('nan')
        else:
            exponent -= 127
            mantissa = 1 + int(mantissa_bits, 2) / (2 ** 23)
        value = (-1) ** sign_bit * (2 ** exponent) * mantissa
        return round(value,3)

    def function_code_parser(self):
        pass


class Modbus_Response_NOT_EXISTENCE(Modbus_Response_Parents):
    def __init__(self, origin_slave_id,
                 mouse_cage_number,
                 slave_id,
                 response,
                 response_hex,
                 function_code,

                 ):
        super().__init__(slave_id, response,
                         response_hex, function_code)
        self.origin_slave_id = origin_slave_id
        self.mouse_cage_number = mouse_cage_number

    def function_code_parser(self):
        if self.mouse_cage_number is None or self.origin_slave_id is None:
            parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-传感器地址不存在：{self.slave_id}"
            logger.error(parser_message)

            pass
        else:
            parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-传感器地址不存在：{self.origin_slave_id}"
            logger.error(parser_message)
            pass
        pass

        return None, parser_message


class Modbus_Response_ENM(Modbus_Response_Parents):
    def __init__(self, origin_slave_id,
                 mouse_cage_number,
                 slave_id,
                 response,
                 response_hex,
                 function_code,

                 ):
        super().__init__(slave_id, response,
                         response_hex, function_code)
        self.origin_slave_id = origin_slave_id
        self.mouse_cage_number = mouse_cage_number
        self.type = Modbus_Slave_Ids.ENM

    def function_code_parser(self):
        if isinstance(self.function_code, str):
            function_code = int(self.function_code, 16)
        else:
            function_code = self.function_code
        logger.info(f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析功能码：{self.function_code}")
        return_data = None
        table_name = ""
        match function_code:
            case 1:
                return_data, parser_message = self.parser_function_code_1()
                table_name = "out_port_state"
                pass
            case 2:
                return_data, parser_message = self.parser_function_code_2()
                table_name = "sensor_status"

            case 3:
                return_data, parser_message = self.parser_function_code_3()
                table_name = "sensor_config"
            case 4:
                return_data, parser_message = self.parser_function_code_4()
                table_name = "monitor_data"
            case 5:
                return_data, parser_message = self.parser_function_code_5()

            case 6:
                return_data, parser_message = self.parser_function_code_6()

            case 17:
                return_data, parser_message = self.parser_function_code_17()
                table_name = "module_information"
            case _:
                return_data, parser_message = self.parser_function_code_others()
        return_data_struct = {}
        return_data_struct['table_name'] = table_name
        return_data_struct['module_name'] = self.type.value['name']
        return_data_struct['mouse_cage_number'] = self.mouse_cage_number
        return_data_struct['data'] = return_data
        return_data_struct['slave_id'] = self.origin_slave_id
        return_data_struct['function_code'] = function_code

        return return_data_struct, parser_message

    pass
    """
    11 01 X
    """

    def parser_function_code_1(self):
        function_desc = """
                       读输出端口状态信息
                       参数长度：2
                       """
        pack_struct = "B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        data_binary_str_list = self.int_to_8bit_binary(num_list=self.response_struct['data'])
        data_binary_str_list_all = "".join(data_binary_str_list)
        return_datas = []
        port_types = ['跑轮刹车', '光照控制']
        index = 0
        for str_single in data_binary_str_list_all:
            if index == 6:
                return_datas.append({
                    "desc": port_types[index - 6],
                    'value': '已刹车' if int(str_single) == 1 else '未刹车'
                }
                )
            if index == 7:
                return_datas.append({
                    "desc": port_types[index - 6],
                    'value': '已开灯' if int(str_single) == 1 else '未开灯'
                }
                )
            index += 1

        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}状态：{return_data['value']} | "
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)

        return return_datas, parser_message
        pass

    """
        11 02 X
        """

    def parser_function_code_2(self):
        function_desc = """
                               读传感器状态信息
                               参数长度：2
                               """
        pack_struct = "B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        data_binary_str_list = self.int_to_8bit_binary(num_list=self.response_struct['data'])
        data_binary_str_list_all = "".join(data_binary_str_list)
        return_datas = []
        port_types = ['湿度传感器状态', '气压传感器状态', '噪声传感器状态']

        index = 0
        for str_single in data_binary_str_list_all:
            if index >= 5:
                return_datas.append({
                    "desc": port_types[index - 5],
                    'value': int(str_single)
                }
                )
            index += 1

        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}状态：{'正常' if return_data['value'] == 1 else '故障'} | "
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass
        pass

    """
        11 03 X
        """

    def parser_function_code_3(self):
        function_desc = """
                       读配置寄存器
                       参数长度：7
                      """
        pack_struct = "B H H H"
        self.parser_response_pack(pack_struct, struct_type="H", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types = ['光照亮度', '光照色温', '模块地址']
        index = 0
        for data_single in self.response_struct['data']:
            return_datas.append({
                "desc": port_types[index],
                'value': int(data_single)
            }
            )
            index += 1
        return_data_str = ""
        index = 0
        for return_data in return_datas:
            if index == 2:
                return_data_str += f"{return_data['desc']}:0x{return_data['value']:04X} | "
            else:
                return_data_str += f"{return_data['desc']}:{return_data['value']} | "
            index += 1
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    """
        11 04 X
        """

    def parser_function_code_4(self):
        function_desc = """
                                       读传感器测量值
                                       参数长度：13
                                        """
        pack_struct = "B " * 13
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types = ['温度测量值(°C)', '湿度测量值(%RH)', '噪声测量值(dB)', '大气压测量值(KPa)', '当前计量周期内跑轮圈数测量值']
        j = 0
        for i in range(len(self.response_struct['data'])):
            match i:
                case 1:
                    return_datas.append({
                        "desc": port_types[j],
                        'value': float(
                            str(self.get_signed_int(
                                "".join(self.int_to_8bit_binary([self.response_struct['data'][i - 1]]))
                            )) + "." + str(
                                self.response_struct['data'][i]))
                    }
                    )
                    j += 1
                case 3:
                    return_datas.append({
                        "desc": port_types[j],
                        'value': float(
                            str(self.response_struct['data'][i - 1]) + "." + str(self.response_struct['data'][i]))
                    }
                    )
                    j += 1
                    pass
                case 5:
                    return_datas.append({
                        "desc": port_types[j],
                        'value': float(
                            str(self.response_struct['data'][i - 1]) + "." + str(self.response_struct['data'][i]))
                    }
                    )
                    j += 1
                    pass
                case 8:
                    return_datas.append({
                        "desc": port_types[j],
                        'value': float(
                            str(int("".join(self.int_to_8bit_binary(
                                num_list=[self.response_struct['data'][i - 2], self.response_struct['data'][i - 1]])),
                                2)) + "." + str(self.response_struct['data'][i]))
                    }
                    )
                    j += 1
                    pass
                case 11:
                    return_datas.append({
                        "desc": port_types[j],
                        'value': float(
                            str(int("".join(self.int_to_8bit_binary(
                                num_list=[self.response_struct['data'][i - 2], self.response_struct['data'][i - 1]])),
                                2)+ round(float(self.response_struct['data'][i])/36, 3)))
                    }
                    )
                    j += 1
                    pass
                case _:
                    pass
        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} | "
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    """
        11 05 X
        """

    def parser_function_code_5(self):
        function_desc = """
                                      写从机单个开关量输出（ON/OFF）
                                      参数长度：4
                                      """
        pack_struct = "B B B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=False)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port0_types = ['跑轮刹车地址', '跑轮刹车控制']
        port1_types = ['光照控制地址', '光照控制']
        j = 0
        for i in range(len(self.response_struct['data'])):
            match i:
                case 1:
                    if self.response_struct['data'][i] == 0:
                        return_datas.append({
                            "desc": port0_types[j],
                            'value': f"0X{self.response_struct['data'][i - 1]:02X}{self.response_struct['data'][i]:02X}"
                        }
                        )
                        pass
                    elif self.response_struct['data'][i] == 1:
                        return_datas.append({
                            "desc": port1_types[j],
                            'value': f"0X{self.response_struct['data'][i - 1]:02X}{self.response_struct['data'][i]:02X}"
                        }
                        )
                    j += 1
                    pass
                case 2:
                    if self.response_struct['data'][i - 1] == 0:
                        return_datas.append({
                            "desc": port0_types[j],
                            'value': "刹车" if int(self.response_struct['data'][i]) == 255 else "解除刹车"
                        }
                        )
                        pass
                    elif self.response_struct['data'][i - 1] == 1:
                        return_datas.append({
                            "desc": port1_types[j],
                            'value': " 开光照" if int(self.response_struct['data'][i]) == 255 else "关光照"
                        }
                        )

                    j += 1
                    pass
                case _:
                    pass

        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} |"
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message

        pass

    """
        11 06 X
        """

    def parser_function_code_6(self):
        function_desc = """
                       写单个保持寄存器
                       参数长度：4
                                       """
        pack_struct = "B B B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=False)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types_0 = ['模块地址地址', '新分配的模块地址值']
        port_types_1 = ['光照色温地址', '光照色温数据']
        port_types_2 = ['光照亮度地址', '光照亮度数据']
        j = 0
        for i in range(len(self.response_struct['data'])):
            match i:
                case 1:
                    match self.response_struct['data'][i]:
                        case 0:
                            return_datas.append({
                                "desc": port_types_0[j],
                                'value': f"0X{self.response_struct['data'][i - 1]:02X}{self.response_struct['data'][i]:02X}"
                            }
                            )
                            pass
                        case 1:
                            return_datas.append({
                                "desc": port_types_1[j],
                                'value': f"0X{self.response_struct['data'][i - 1]:02X}{self.response_struct['data'][i]:02X}"
                            }
                            )
                            pass
                        case 2:
                            return_datas.append({
                                "desc": port_types_2[j],
                                'value': f"0X{self.response_struct['data'][i - 1]:02X}{self.response_struct['data'][i]:02X}"
                            }
                            )
                            pass
                        case _:
                            pass
                    j += 1
                    pass
                case 3:
                    match self.response_struct['data'][1]:
                        case 0:
                            return_datas.append({
                                "desc": port_types_0[j],
                                'value': f"0X{self.response_struct['data'][i - 1]:02X}{self.response_struct['data'][i]:02X}"
                            }
                            )
                            pass
                        case 1:
                            return_datas.append({
                                "desc": port_types_1[j],
                                'value': f"{int(''.join(self.int_to_8bit_binary(num_list=[self.response_struct['data'][i - 1], self.response_struct['data'][i]])), 2)}"
                            }
                            )
                            pass
                        case 2:
                            return_datas.append({
                                "desc": port_types_1[j],
                                'value': f"{int(''.join(self.int_to_8bit_binary(num_list=[self.response_struct['data'][i - 1], self.response_struct['data'][i]])), 2)}"
                            }
                            )
                            pass
                        case _:
                            pass
                    j += 1
                    pass
                case _:
                    pass

        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} |"
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    """
        11 11 X
        """

    def parser_function_code_17(self):
        function_desc = """
               读取模块ID信息等
               参数长度：17
               """
        pack_struct = "B H H H H H H H H"
        self.parser_response_pack(pack_struct, struct_type="H", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types = ['生产厂商', '硬件版本', '软件版本', '出厂地址', '当前地址', '预留1', '预留2', '预留3']
        index = 0
        for data_single in self.response_struct['data']:
            return_datas.append({
                "desc": port_types[index],
                'value': f"{data_single:04X}"
            }
            )
            index += 1
        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} | "
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    def parser_function_code_others(self):
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-无法解析功能码：{self.function_code}"
        logger.error(parser_message)
        return None, parser_message
        pass


class Modbus_Response_DWM(Modbus_Response_Parents):
    def __init__(self, origin_slave_id,
                 mouse_cage_number,
                 slave_id,
                 response,
                 response_hex,
                 function_code,

                 ):
        super().__init__(slave_id, response,
                         response_hex, function_code)
        self.origin_slave_id = origin_slave_id
        self.mouse_cage_number = mouse_cage_number
        self.type = Modbus_Slave_Ids.DWM

    def function_code_parser(self):
        if isinstance(self.function_code, str):
            function_code = int(self.function_code, 16)
        else:
            function_code = self.function_code
        logger.info(f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析功能码：{self.function_code}")
        return_data = None
        table_name = ""
        match function_code:
            case 2:
                return_data, parser_message = self.parser_function_code_2()
                table_name = "sensor_status"

            case 4:
                return_data, parser_message = self.parser_function_code_4()
                table_name = "monitor_data"

            case 17:
                return_data, parser_message = self.parser_function_code_17()
                table_name = "module_information"

            case _:
                return_data, parser_message = self.parser_function_code_others()
        return_data_struct = {}
        return_data_struct['table_name'] = table_name
        return_data_struct['module_name'] = self.type.value['name']
        return_data_struct['mouse_cage_number'] = self.mouse_cage_number
        return_data_struct['data'] = return_data
        return_data_struct['slave_id'] = self.origin_slave_id
        return_data_struct['function_code'] = function_code

        return return_data_struct, parser_message

    pass
    """
           12 02 X
           """

    def parser_function_code_2(self):
        function_desc = """
                                       读传感器状态信息
                                       参数长度：2
                                       """
        pack_struct = "B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        data_binary_str_list = self.int_to_8bit_binary(num_list=self.response_struct['data'])
        data_binary_str_list_all = "".join(data_binary_str_list)
        return_datas = []
        port_types = ['重量传感器状态']

        index = 0
        for str_single in data_binary_str_list_all:
            if index >= 7:
                return_datas.append({
                    "desc": port_types[index - 7],
                    'value': int(str_single)
                }
                )
            index += 1

        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}状态：{'正常' if return_data['value'] == 1 else '故障'} | "
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    """
           12 04 X
           """

    def parser_function_code_4(self):
        function_desc = """
                   读传感器测量值
                   参数长度：5
                                                        """
        pack_struct = "B " * 5
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types = ['重量测量值(g)']
        j = 0
        for i in range(len(self.response_struct['data'])):
            match i:
                case 3:
                    return_datas.append({
                        "desc": port_types[j],
                        'value': round(int("".join(self.int_to_8bit_binary(
                            num_list=[self.response_struct['data'][i - 3], self.response_struct['data'][i - 2],
                                      self.response_struct['data'][i - 1], self.response_struct['data'][i]])), 2) / 100,
                                       2)
                    }
                    )
                    j += 1

                case _:
                    pass
        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} | "
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    """
           12 11 X
           """

    def parser_function_code_17(self):
        function_desc = """
                               读取模块ID信息等
                               参数长度：17
                               """
        pack_struct = "B H H H H H H H H"
        self.parser_response_pack(pack_struct, struct_type="H", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types = ['生产厂商', '硬件版本', '软件版本', '出厂地址', '当前地址', '预留1', '预留2', '预留3']
        index = 0
        for data_single in self.response_struct['data']:
            return_datas.append({
                "desc": port_types[index],
                'value': f"{data_single:04X}"
            }
            )
            index += 1
        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} | "
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    def parser_function_code_others(self):
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-{self.response_hex}-响应报文-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-无法解析功能码：{self.function_code}"
        logger.error(parser_message)
        return None, parser_message


class Modbus_Response_EM(Modbus_Response_Parents):
    def __init__(self, origin_slave_id,
                 mouse_cage_number,
                 slave_id,
                 response,
                 response_hex,
                 function_code,

                 ):
        super().__init__(slave_id, response,
                         response_hex, function_code)
        self.origin_slave_id = origin_slave_id
        self.mouse_cage_number = mouse_cage_number
        self.type = Modbus_Slave_Ids.EM

    def function_code_parser(self):
        if isinstance(self.function_code, str):
            function_code = int(self.function_code, 16)
        else:
            function_code = self.function_code
        logger.info(f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析功能码：{self.function_code}")
        return_data = None
        table_name = ""
        match function_code:
            case 1:
                return_data, parser_message = self.parser_function_code_1()
                table_name = "out_port_state"
                pass
            case 2:
                return_data, parser_message = self.parser_function_code_2()
                table_name = "sensor_status"

            case 4:
                return_data, parser_message = self.parser_function_code_4()
                table_name = "monitor_data"
            case 5:
                return_data, parser_message = self.parser_function_code_5()

            case 17:
                return_data, parser_message = self.parser_function_code_17()
                table_name = "module_information"

            case _:
                return_data, parser_message = self.parser_function_code_others()
        return_data_struct = {}
        return_data_struct['table_name'] = table_name
        return_data_struct['module_name'] = self.type.value['name']
        return_data_struct['mouse_cage_number'] = self.mouse_cage_number
        return_data_struct['data'] = return_data
        return_data_struct['slave_id'] = self.origin_slave_id
        return_data_struct['function_code'] = function_code

        return return_data_struct, parser_message

    pass
    """
                13 01 X
        """

    def parser_function_code_1(self):
        function_desc = """
                                               读输出端口状态信息
                                               参数长度：2
                                               """
        pack_struct = "B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        data_binary_str_list = self.int_to_8bit_binary(num_list=self.response_struct['data'])
        data_binary_str_list_all = "".join(data_binary_str_list)
        return_datas = []
        port_types = ['食物开关']

        index = 0
        for str_single in data_binary_str_list_all:
            if index == 7:
                return_datas.append({
                    "desc": port_types[index - 7],
                    'value': int(str_single)
                }
                )
            index += 1

        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}状态：{'打开' if return_data['value'] == 1 else '关闭'} | "
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    """
            13 02 X
    """

    def parser_function_code_2(self):
        function_desc = """
               读传感器状态信息
               参数长度：2
                               """
        pack_struct = "B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        data_binary_str_list = self.int_to_8bit_binary(num_list=self.response_struct['data'])
        data_binary_str_list_all = "".join(data_binary_str_list)
        return_datas = []
        port_types = ['舵机', '重量传感器']

        index = 0
        for str_single in data_binary_str_list_all:
            if index >= 6:
                return_datas.append({
                    "desc": port_types[index - 6],
                    'value': int(str_single)
                }
                )
            index += 1

        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}状态：{'正常' if return_data['value'] == 1 else '故障'} | "
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    """
                13 04 X
        """

    def parser_function_code_4(self):
        function_desc = """
           读传感器测量值
           参数长度：5
                                                """
        pack_struct = "B " * 5
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types = ['重量测量值(g)']
        j = 0
        for i in range(len(self.response_struct['data'])):
            match i:
                case 3:
                    return_datas.append({
                        "desc": port_types[j],
                        'value': round(int("".join(self.int_to_8bit_binary(
                            num_list=[self.response_struct['data'][i - 3], self.response_struct['data'][i - 2],
                                      self.response_struct['data'][i - 1], self.response_struct['data'][i]])), 2) / 100,
                                       2)
                    }
                    )
                    j += 1

                case _:
                    pass
        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} | "
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    """
        13 05 X
            """

    def parser_function_code_5(self):
        function_desc = """
                                              写从机单个开关量输出（ON/OFF）
                                              参数长度：4
                                              """
        pack_struct = "B B B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=False)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types = ['食槽控制起始地址值', '食槽控制']

        j = 0
        for i in range(len(self.response_struct['data'])):
            match i:
                case 1:

                    return_datas.append({
                        "desc": port_types[j],
                        'value': f"0X{self.response_struct['data'][i - 1]:02X}{self.response_struct['data'][i]:02X}"
                    }
                    )
                    j += 1
                    pass
                case 2:

                    return_datas.append({
                        "desc": port_types[j],
                        'value': "开食槽" if int(self.response_struct['data'][i]) == 255 else "关食槽"
                    }
                    )
                    j += 1
                    pass
                case _:
                    pass

        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} |"
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    """
                13 11 X
        """

    def parser_function_code_17(self):
        function_desc = """
                       读取模块ID信息等
                       参数长度：17
                       """
        pack_struct = "B H H H H H H H H"
        self.parser_response_pack(pack_struct, struct_type="H", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types = ['生产厂商', '硬件版本', '软件版本', '出厂地址', '当前地址', '预留1', '预留2', '预留3']
        index = 0
        for data_single in self.response_struct['data']:
            return_datas.append({
                "desc": port_types[index],
                'value': f"{data_single:04X}"
            }
            )
            index += 1
        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} | "
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    def parser_function_code_others(self):
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-无法解析功能码：{self.function_code}"
        logger.error(parser_message)
        return None, parser_message


class Modbus_Response_WM(Modbus_Response_Parents):
    def __init__(self, origin_slave_id,
                 mouse_cage_number,
                 slave_id,
                 response,
                 response_hex,
                 function_code,

                 ):
        super().__init__(slave_id, response,
                         response_hex, function_code)
        self.origin_slave_id = origin_slave_id
        self.mouse_cage_number = mouse_cage_number
        self.type = Modbus_Slave_Ids.WM

    def function_code_parser(self):
        if isinstance(self.function_code, str):
            function_code = int(self.function_code, 16)
        else:
            function_code = self.function_code
        logger.info(f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析功能码：{self.function_code}")
        return_data = None
        table_name = ""
        match function_code:

            case 2:
                return_data, parser_message = self.parser_function_code_2()
                table_name = "sensor_status"

            case 4:
                return_data, parser_message = self.parser_function_code_4()
                table_name = "monitor_data"

            case 17:
                return_data, parser_message = self.parser_function_code_17()
                table_name = "module_information"

            case _:
                return_data, parser_message = self.parser_function_code_others()
        return_data_struct = {}
        return_data_struct['table_name'] = table_name
        return_data_struct['module_name'] = self.type.value['name']
        return_data_struct['mouse_cage_number'] = self.mouse_cage_number
        return_data_struct['data'] = return_data
        return_data_struct['slave_id'] = self.origin_slave_id
        return_data_struct['function_code'] = function_code

        return return_data_struct, parser_message

    pass
    """
              14 02 X
              """

    def parser_function_code_2(self):
        function_desc = """
                                          读传感器状态信息
                                          参数长度：2
                                          """
        pack_struct = "B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        data_binary_str_list = self.int_to_8bit_binary(num_list=self.response_struct['data'])
        data_binary_str_list_all = "".join(data_binary_str_list)
        return_datas = []
        port_types = ['重量传感器状态']

        index = 0
        for str_single in data_binary_str_list_all:
            if index >= 7:
                return_datas.append({
                    "desc": port_types[index - 7],
                    'value': int(str_single)
                }
                )
            index += 1

        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}状态：{'正常' if return_data['value'] == 1 else '故障'} | "
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    """
           14 04 X
           """

    def parser_function_code_4(self):
        function_desc = """
                      读传感器测量值
                      参数长度：5
                                                           """
        pack_struct = "B " * 5
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types = ['重量测量值(g)']
        j = 0
        for i in range(len(self.response_struct['data'])):
            match i:
                case 3:
                    return_datas.append({
                        "desc": port_types[j],
                        'value': round(int("".join(self.int_to_8bit_binary(
                            num_list=[self.response_struct['data'][i - 3], self.response_struct['data'][i - 2],
                                      self.response_struct['data'][i - 1], self.response_struct['data'][i]])), 2) / 100,
                                       2)
                    }
                    )
                    j += 1

                case _:
                    pass
        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} | "
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    """
           14 11 X
           """

    def parser_function_code_17(self):
        function_desc = """
                                  读取模块ID信息等
                                  参数长度：17
                                  """
        pack_struct = "B H H H H H H H H"
        self.parser_response_pack(pack_struct, struct_type="H", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types = ['生产厂商', '硬件版本', '软件版本', '出厂地址', '当前地址', '预留1', '预留2', '预留3']
        index = 0
        for data_single in self.response_struct['data']:
            return_datas.append({
                "desc": port_types[index],
                'value': f"{data_single:04X}"
            }
            )
            index += 1
        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} | "
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    def parser_function_code_others(self):
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文-鼠笼{self.mouse_cage_number}-{self.type.value['name']}-{self.type.value['description']}-无法解析功能码：{self.function_code}"
        logger.error(parser_message)
        return None, parser_message

    pass


class Modbus_Response_ZOS(Modbus_Response_Parents):
    def __init__(self, slave_id, response,
                 response_hex, function_code
                 ):
        super().__init__(slave_id, response,
                         response_hex, function_code)
        self.type = Modbus_Slave_Ids.ZOS

    def function_code_parser(self):
        if isinstance(self.function_code, str):
            function_code = int(self.function_code, 16)
        else:
            function_code = self.function_code
        logger.info(f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析功能码：{self.function_code}")
        return_data = None
        table_name = ""
        match function_code:
            case 1:
                return_data, parser_message = self.parser_function_code_1()
                table_name = "out_port_state"
                pass
            case 2:
                return_data, parser_message = self.parser_function_code_2()
                table_name = "sensor_status"

            case 3:
                return_data, parser_message = self.parser_function_code_3()
                table_name = "sensor_config"
            case 4:
                return_data, parser_message = self.parser_function_code_4()
                table_name = "monitor_data"
            case 5:
                return_data, parser_message = self.parser_function_code_5()

            case 6:
                return_data, parser_message = self.parser_function_code_6()

            case 17:
                return_data, parser_message = self.parser_function_code_17()
                table_name = "module_information"

            case _:
                return_data, parser_message = self.parser_function_code_others()
        return_data_struct = {}
        return_data_struct['table_name'] = table_name
        return_data_struct['module_name'] = self.type.value['name']
        return_data_struct['mouse_cage_number'] = 0
        return_data_struct['data'] = return_data
        return_data_struct['slave_id'] = self.slave_id
        return_data_struct['function_code'] = function_code

        return return_data_struct, parser_message

    """
           04 01 X
           """

    def parser_function_code_1(self):
        function_desc = """
               读输出端口状态信息
               参数长度：2
               """
        pack_struct = "B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        data_binary_str_list = self.int_to_8bit_binary(num_list=self.response_struct['data'])
        data_binary_str_list_all = "".join(data_binary_str_list)
        return_datas = []
        port_types = ['ZOS状态', '氧传感器']
        index = 0
        for str_single in data_binary_str_list_all:
            if index >= 6:
                return_datas.append({
                    "desc": port_types[index - 6],
                    'value': int(str_single)
                }
                )
            index += 1

        return_data_str = ""
        for return_data in return_datas:
            if return_data['desc'] == port_types[1]:
                return_data_str += f"{return_data['desc']}状态：{'打开' if return_data['value'] == 1 else '关闭'} | "
                pass
            else:
                return_data_str += f"{return_data['desc']}状态：{'运行' if return_data['value'] == 1 else '停止(预热)'} | "
                pass

        parser_message = f"{time_util.get_format_from_time(time.time())} | {self.response_hex}-响应报文解析-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message

        pass

    """
               04 02 X
               """

    def parser_function_code_2(self):
        function_desc = """
                       读输出端口状态信息
                       参数长度：2
                       """
        pack_struct = "B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        data_binary_str_list = self.int_to_8bit_binary(num_list=self.response_struct['data'])
        data_binary_str_list_all = "".join(data_binary_str_list)
        return_datas = []
        port_types = ['氧传感器状态']

        index = 0
        for str_single in data_binary_str_list_all:
            if index == 7:
                return_datas.append({
                    "desc": port_types[index - 7],
                    'value': int(str_single)
                }
                )
            index += 1

        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}状态：{'正常' if return_data['value'] == 1 else '故障'} | "
        parser_message = f"{time_util.get_format_from_time(time.time())} | {self.response_hex}-响应报文解析-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    """
               04 03 X
               """

    def parser_function_code_3(self):
        function_desc = """
                读配置寄存器
                参数长度：3
               """
        pack_struct = "B H"
        self.parser_response_pack(pack_struct, struct_type="H", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types = ['氧传感器配置（预留）']
        index = 0
        for data_single in self.response_struct['data']:
            return_datas.append({
                "desc": port_types[index],
                'value': int(data_single)
            }
            )
            index += 1
        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} | "
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    """
               04 04 X
               """

    def parser_function_code_4(self):
        function_desc = """
                               读传感器测量值
                               参数长度：5
                                """
        pack_struct = "B " * 5
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types = ['氧气传感器测量值(%)']
        j = 0
        for i in range(len(self.response_struct['data'])):
            match i:
                case 1:
                    return_datas.append({
                        "desc": port_types[j],
                        'value': float(
                            str(self.response_struct['data'][i - 1]) + "." + str(self.response_struct['data'][i]))
                    }
                    )
                    j += 1

                case _:
                    pass
        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} | "
        parser_message = f"{time_util.get_format_from_time(time.time())} | {self.response_hex}-响应报文解析-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    """
               04 05 X
               """

    def parser_function_code_5(self):
        function_desc = """
                              写从机单个开关量输出（ON/OFF）
                              参数长度：4
                              """
        pack_struct = "B B B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=False)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types = ['氧传感器起始地址值', '氧传感器开、关控制']
        j = 0
        for i in range(len(self.response_struct['data'])):
            match i:
                case 1:
                    return_datas.append({
                        "desc": port_types[j],
                        'value': f"0X{self.response_struct['data'][i - 1]:02X}{self.response_struct['data'][i]:02X}"
                    }
                    )
                    j += 1
                    pass
                case 2:
                    return_datas.append({
                        "desc": port_types[j],
                        'value': "ON" if int(self.response_struct['data'][i]) == 255 else "OFF"
                    }
                    )
                    j += 1
                    pass
                case _:
                    pass

        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} |"

        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message

        pass

    """
               04 06 X
               """

    def parser_function_code_6(self):
        function_desc = """
                写单个保持寄存器
                参数长度：4
                                """
        pack_struct = "B B B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=False)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types_0 = ['模块地址地址', '新分配的模块地址值']
        port_types_1 = ['寄存器地址', '氧传感器设置（预留）']
        j = 0
        for i in range(len(self.response_struct['data'])):
            match i:
                case 1:
                    match self.response_struct['data'][i]:
                        case 0:
                            return_datas.append({
                                "desc": port_types_0[j],
                                'value': f"0X{self.response_struct['data'][i - 1]:02X}{self.response_struct['data'][i]:02X}"
                            }
                            )
                            pass
                        case 1:
                            return_datas.append({
                                "desc": port_types_1[j],
                                'value': f"0X{self.response_struct['data'][i - 1]:02X}{self.response_struct['data'][i]:02X}"
                            }
                            )
                            pass

                        case _:
                            pass
                    j += 1
                    pass
                case 3:
                    match self.response_struct['data'][1]:
                        case 0:
                            return_datas.append({
                                "desc": port_types_0[j],
                                'value': f"0X{self.response_struct['data'][i - 1]:02X}{self.response_struct['data'][i]:02X}"
                            }
                            )
                            pass
                        case 1:
                            return_datas.append({
                                "desc": port_types_1[j],
                                'value': f"0X{self.response_struct['data'][i - 1]:02X}{self.response_struct['data'][i]:02X}"
                            }
                            )
                            pass

                        case _:
                            pass
                    j += 1
                    pass
                case _:
                    pass

        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} |"
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message

    """
               04 11 X
               """

    def parser_function_code_17(self):
        function_desc = """
                                       读取模块ID信息等
                                       参数长度：17
                                       """
        pack_struct = "B H H H H H H H H"
        self.parser_response_pack(pack_struct, struct_type="H", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types = ['生产厂商', '硬件版本', '软件版本', '出厂地址', '当前地址', '预留1', '预留2', '预留3']
        index = 0
        for data_single in self.response_struct['data']:
            return_datas.append({
                "desc": port_types[index],
                'value': f"{data_single:04X}"
            }
            )
            index += 1
        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} | "
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    def parser_function_code_others(self):
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文-{self.type.value['name']}-{self.type.value['description']}-无法解析功能码：{self.function_code}"
        logger.error(parser_message)
        return None, parser_message
        pass


class Modbus_Response_UGC(Modbus_Response_Parents):
    def __init__(self, slave_id, response,
                 response_hex, function_code
                 ):
        super().__init__(slave_id, response,
                         response_hex, function_code)
        self.type = Modbus_Slave_Ids.UGC

    def function_code_parser(self):
        if isinstance(self.function_code, str):
            function_code = int(self.function_code, 16)
        else:
            function_code = self.function_code
        logger.info(f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析功能码：{self.function_code}")
        return_data = None
        table_name = ""
        match function_code:
            case 1:
                return_data, parser_message = self.parser_function_code_1()
                table_name = "out_port_state"
                pass
            case 2:
                return_data, parser_message = self.parser_function_code_2()
                table_name = "sensor_status"

            case 3:
                return_data, parser_message = self.parser_function_code_3()
                table_name = "sensor_config"
            case 4:
                return_data, parser_message = self.parser_function_code_4()
                table_name = "monitor_data"
            case 5:
                return_data, parser_message = self.parser_function_code_5()

            case 6:
                return_data, parser_message = self.parser_function_code_6()

            case 17:
                return_data, parser_message = self.parser_function_code_17()
                table_name = "module_information"

            case _:
                return_data, parser_message = self.parser_function_code_others()
        return_data_struct = {}
        return_data_struct['table_name'] = table_name
        return_data_struct['module_name'] = self.type.value['name']
        return_data_struct['mouse_cage_number'] = 0
        return_data_struct['data'] = return_data
        return_data_struct['slave_id'] = self.slave_id
        return_data_struct['function_code'] = function_code

        return return_data_struct, parser_message

    """
           03 01 X
           """

    def parser_function_code_1(self):
        function_desc = """
               读输出端口状态信息
               参数长度：2
               """
        pack_struct = "B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=False)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        data_binary_str_list = self.int_to_8bit_binary(num_list=self.response_struct['data'])[1]
        data_binary_str_list_all = "".join(data_binary_str_list)
        return_datas = []
        port_types = ['CO2阀门状态']
        index = 0
        for str_single in data_binary_str_list_all:
            if index == 7:
                return_datas.append({
                    "desc": port_types[index - 7],
                    'value': int(str_single)
                }
                )
            index += 1

        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}状态：{'ON' if return_data['value'] == 1 else 'OFF'} | "
        parser_message = f"{time_util.get_format_from_time(time.time())} | {self.response_hex}-响应报文解析-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    """
               03 02 X
               """

    def parser_function_code_2(self):
        function_desc = """
                       读输出端口状态信息
                       参数长度：3
                       """
        pack_struct = "B B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        data_binary_str_list = self.int_to_8bit_binary(num_list=self.response_struct['data'])
        data_binary_str_list_all = "".join(data_binary_str_list)
        return_datas = []
        port_types = ['预留7', '预留6', '预留5', '预留4', '预留3', '预留2', '预留1', 'CO2',
                      '气压2', '气压1', '湿度2', '湿度1', '温度2', '温度1', '流量2', '流量1']
        index = 0
        for str_single in data_binary_str_list_all:
            return_datas.append({
                "desc": port_types[index],
                'value': int(str_single)
            }
            )
            index += 1

        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}状态：{'ON' if return_data['value'] == 1 else 'OFF'} | "
        parser_message = f"{time_util.get_format_from_time(time.time())} | {self.response_hex}-响应报文解析-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass
        pass

    """
               03 03 X
               """

    def parser_function_code_3(self):
        function_desc = """
                读配置寄存器
                参数长度：9
               """
        pack_struct = "B H H H H"
        self.parser_response_pack(pack_struct, struct_type="H", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types = ['调节阀1开度', '调节阀2开度', '调节阀3开度', '调节阀4开度']
        index = 0
        for data_single in self.response_struct['data']:
            return_datas.append({
                "desc": port_types[index],
                'value': int(data_single)
            }
            )
            index += 1
        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']}% | "
        parser_message = f"{time_util.get_format_from_time(time.time())} | {self.response_hex}-响应报文解析-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    """
               03 04 X
               """

    def parser_function_code_4(self):
        function_desc = """
                               读传感器测量值
                               参数长度：21
                                """
        pack_struct = "B " * 21
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types = ['流量计1', 'CO2(%)', '保留']
        j = 0
        for i in range(len(self.response_struct['data'])):
            match i:
                case 1:
                    return_datas.append({
                        "desc": port_types[j],
                        'value': int("".join(self.int_to_8bit_binary(
                            num_list=[self.response_struct['data'][i - 1], self.response_struct['data'][i]])), 2)
                    }
                    )
                    j += 1
                    pass
                case 17:
                    return_datas.append({
                        "desc": port_types[j],
                        'value': round((int("".join(self.int_to_8bit_binary(
                            num_list=[self.response_struct['data'][i - 1], self.response_struct['data'][i]])),
                            2) / 1000000)*100,5)
                    }
                    )
                    j += 1
                    pass
                case 19:
                    return_datas.append({
                        "desc": port_types[j],
                        'value': float(
                            str(self.response_struct['data'][i - 1]) + "." + str(self.response_struct['data'][i]))
                    }
                    )
                    j += 1
                    pass
                case _:
                    pass
        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} | "
        parser_message = f"{time_util.get_format_from_time(time.time())} | {self.response_hex}-响应报文解析-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    """
               03 05 X
               """

    def parser_function_code_5(self):
        function_desc = """
                              写从机单个开关量输出（ON/OFF）
                              参数长度：4
                              """
        pack_struct = "B B B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=False)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types = ['电磁阀名称', '电磁阀开、关控制']
        valve_desc = ['鼠笼气电磁阀(sample 气)', '校零气路(Zero气)电磁阀', '量程标定气路电磁阀', '', '抽气泵']
        valve_index = 0
        j = 0
        for i in range(len(self.response_struct['data'])):
            match i:
                case 1:
                    valve_address = f"0X{self.response_struct['data'][i - 1]:02X}{self.response_struct['data'][i]:02X}"
                    valve_index = int(valve_address, 16)
                    return_datas.append({
                        "desc": port_types[j],
                        'value': valve_desc[valve_index]
                    }
                    )
                    j += 1
                    pass
                case 2:
                    return_datas.append({
                        "desc": port_types[j],
                        'value': "开" if int(self.response_struct['data'][i]) == 255 else "关"
                    }
                    )
                    j += 1
                    pass
                case _:
                    pass

        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} |"
        parser_message = f"{time_util.get_format_from_time(time.time())} | {self.response_hex}-响应报文解析-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message

    """
               03 06 X
               """

    def parser_function_code_6(self):
        function_desc = """
                       写单个保持寄存器
                       参数长度：4
                               """
        pack_struct = "B B B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=False)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types_1 = ['寄存器地址', '调节阀1开度']
        port_types_2 = ['寄存器地址', '调节阀2开度']
        port_types_3 = ['寄存器地址', '调节阀3开度']
        port_types_4 = ['寄存器地址', '调节阀4开度']
        port_types_16 = ['设置', '设置状态']
        port_types_19 = ['设置', '设置状态']

        co2_desc = ['CO2零点设置', 'CO2零点恢复']
        j = 0
        for i in range(len(self.response_struct['data'])):
            match i:
                case 1:
                    match self.response_struct['data'][1]:
                        case 1 | 2 | 3 | 4:
                            return_datas.append({
                                "desc": port_types_1[j],
                                'value': f"0X{self.response_struct['data'][i - 1]:02X}{self.response_struct['data'][i]:02X}"
                            })
                            pass
                        case 16:
                            return_datas.append({
                                "desc": port_types_16[j],
                                'value': co2_desc[0]
                            })
                            pass
                        case 19:
                            return_datas.append({
                                "desc": port_types_19[j],
                                'value': co2_desc[1]
                            })
                            pass
                        case _:
                            pass
                    j += 1
                    pass
                case 3:
                    match self.response_struct['data'][1]:
                        case 1:
                            return_datas.append({
                                "desc": port_types_1[j],
                                'value': f"{int(''.join(self.int_to_8bit_binary(num_list=[self.response_struct['data'][i - 1], self.response_struct['data'][i]])), 2)}%"
                            }
                            )
                            pass
                        case 2:
                            return_datas.append({
                                "desc": port_types_2[j],
                                'value': f"{int(''.join(self.int_to_8bit_binary(num_list=[self.response_struct['data'][i - 1], self.response_struct['data'][i]])), 2)}%"
                            }
                            )
                            pass
                        case 3:
                            return_datas.append({
                                "desc": port_types_3[j],
                                'value': f"{int(''.join(self.int_to_8bit_binary(num_list=[self.response_struct['data'][i - 1], self.response_struct['data'][i]])), 2)}%"
                            }
                            )
                            pass
                        case 4:
                            return_datas.append({
                                "desc": port_types_4[j],
                                'value': f"{int(''.join(self.int_to_8bit_binary(num_list=[self.response_struct['data'][i - 1], self.response_struct['data'][i]])), 2)}%"
                            }
                            )
                            pass
                        case 16:
                            return_datas.append({
                                "desc": port_types_16[j],
                                'value': f"{'设置成功' if int(''.join(self.int_to_8bit_binary(num_list=[self.response_struct['data'][i - 1], self.response_struct['data'][i]])), 2) == 0 else '设置失败'}"
                            })
                            pass
                        case 19:
                            return_datas.append({
                                "desc": port_types_19[j],
                                'value': f"{'设置成功' if int(''.join(self.int_to_8bit_binary(num_list=[self.response_struct['data'][i - 1], self.response_struct['data'][i]])), 2) == 0 else '设置失败'}"
                            })
                            pass
                        case _:
                            pass
                    j += 1
                    pass
                case _:
                    pass

        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} |"
        parser_message = f"{time_util.get_format_from_time(time.time())} | {self.response_hex}-响应报文解析-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    """
               03 11 X
               """

    def parser_function_code_17(self):
        function_desc = """
                                       读取模块ID信息等
                                       参数长度：17
                                       """
        pack_struct = "B H H H H H H H H"
        self.parser_response_pack(pack_struct, struct_type="H", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types = ['生产厂商', '硬件版本', '软件版本', '出厂地址', '当前地址', '预留1', '预留2', '预留3']
        index = 0
        for data_single in self.response_struct['data']:
            return_datas.append({
                "desc": port_types[index],
                'value': f"{data_single:04X}"
            }
            )
            index += 1
        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} | "
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message
        pass

    def parser_function_code_others(self):
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文-{self.type.value['name']}-{self.type.value['description']}-无法解析功能码：{self.function_code}"
        logger.error(parser_message)
        return None, parser_message


class Modbus_Response_UFC(Modbus_Response_Parents):
    def __init__(self, slave_id, response,
                 response_hex, function_code
                 ):
        super().__init__(slave_id, response,
                         response_hex, function_code)
        self.type = Modbus_Slave_Ids.UFC

    def function_code_parser(self):
        if isinstance(self.function_code, str):
            function_code = int(self.function_code, 16)
        else:
            function_code = self.function_code
        logger.info(f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析功能码：{self.function_code}")
        return_data = None
        table_name = ""
        match function_code:
            case 1:
                return_data, parser_message = self.parser_function_code_1()
                table_name = "out_port_state"
                pass
            case 2:
                return_data, parser_message = self.parser_function_code_2()
                table_name = "sensor_status"

            case 3:
                return_data, parser_message = self.parser_function_code_3()
                table_name = "sensor_config"
            case 4:
                return_data, parser_message = self.parser_function_code_4()
                table_name = "monitor_data"
            case 5:
                return_data, parser_message = self.parser_function_code_5()

            case 6:
                return_data, parser_message = self.parser_function_code_6()

            case 17:
                return_data, parser_message = self.parser_function_code_17()
                table_name = "module_information"

            case _:
                return_data, parser_message = self.parser_function_code_others()
        return_data_struct = {}
        return_data_struct['table_name'] = table_name
        return_data_struct['module_name'] = self.type.value['name']
        return_data_struct['mouse_cage_number'] = 0
        return_data_struct['data'] = return_data
        return_data_struct['slave_id'] = self.slave_id
        return_data_struct['function_code'] = function_code

        return return_data_struct, parser_message

    def parser_function_code_others(self):
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-{self.response_hex}-响应报文-{self.type.value['name']}-{self.type.value['description']}-无法解析功能码：{self.function_code}"
        logger.error(parser_message)
        return None, parser_message

    """
          02 01 X
          """

    def parser_function_code_1(self):
        function_desc = """
        读输出端口状态信息
        参数长度：3
        """
        pack_struct = "B B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        data_binary_str_list = self.int_to_8bit_binary(num_list=self.response_struct['data'])
        data_binary_str_list_all = "".join(data_binary_str_list)
        return_datas = []
        port_types = ['机器状态', '气泵', '采样阀', '参考气阀门', '鼠笼8的电磁阀', '鼠笼7的电磁阀', '鼠笼6的电磁阀',
                      '鼠笼5的电磁阀', '鼠笼4的电磁阀', '鼠笼3的电磁阀', '鼠笼2的电磁阀',
                      '鼠笼1的电磁阀']
        index = 0
        for str_single in data_binary_str_list_all:
            if index >= 4:
                return_datas.append({
                    "desc": port_types[index - 4],
                    'value': int(str_single)
                }
                )
            index += 1

        return_data_str = ""
        for index, return_data in enumerate(return_datas):
            if return_data['desc'] == port_types[3]:
                return_data_str += f"{return_data['desc']}：{'运行' if return_data['value'] == 1 else '停止'} | "
                pass
            else:
                return_data_str += f"{return_data['desc']}状态：{'打开' if return_data['value'] == 1 else '关闭'} | "
                pass
            if index % 3 == 0:
                return_data_str += "\n"
        parser_message = f"{time_util.get_format_from_time(time.time())} | {self.response_hex}-响应报文解析-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message

    pass
    """
        02 02 X
        """

    def parser_function_code_2(self):
        function_desc = """
                读传感器状态信息
                参数长度：2
                """
        pack_struct = "B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        data_binary_str = self.int_to_8bit_binary(num_list=self.response_struct['data'])[0]
        return_datas = []
        sensor_types = ['流量传感器8', '流量传感器7', '流量传感器6', '流量传感器5', '流量传感器4', '流量传感器3',
                        '流量传感器2', '流量传感器1']
        index = 0
        for str_single in data_binary_str:
            return_datas.append({
                "desc": sensor_types[index],
                'value': int(str_single)
            }
            )
            index += 1

        return_data_str = ""
        for index, return_data in enumerate(return_datas):
            return_data_str += f"{return_data['desc']}传感器状态：{'正常' if return_data['value'] == 1 else '故障'} | "
            if index % 3 == 0:
                return_data_str += "\n"
        parser_message = f"{time_util.get_format_from_time(time.time())} | {self.response_hex}-响应报文解析-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message

    """
        02 03 X
        """

    def parser_function_code_3(self):
        function_desc = """
                        读配置寄存器
                        参数长度：7
                        """
        pack_struct = "B B B B B B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        data_binary_str = self.int_to_8bit_binary(num_list=self.response_struct['data'])[1]
        return_datas = []
        port_types = ['流量计测量范围']
        index = 0
        for str_single in data_binary_str:
            # 可能是第三个 也可能是第7个！！！！！
            if index == 7:
                return_datas.append({
                    "desc": port_types[index - 7],
                    'value': int(str_single)
                }
                )
            index += 1
        return_data_str = ""
        index = 0
        for return_data in return_datas:
            match index:
                case 0:
                    return_data_str += f"{return_data['desc']}:{'0-4500sccm' if return_data['value'] == 1 else '0-2000sccm'} | "
                    pass
                case _:
                    pass

            index += 1
        parser_message = f"{time_util.get_format_from_time(time.time())} | {self.response_hex}-响应报文解析-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message

    """
        02 04 X
        """

    def parser_function_code_4(self):
        function_desc = """
                        读传感器测量值
                        参数长度：13
                         """
        pack_struct = "B B B B B B B B B B B B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types = ['流量计测量值(sccm)', '备用1高字节', '备用1低字节', '备用2高字节', '备用2低字节']
        j = 0
        for i in range(len(self.response_struct['data'])):
            match i:
                case 3:
                    # 流量测量值 四字节IEEE754码
                    # 首先将其展开为二进制数，
                    data_str = "".join(self.int_to_8bit_binary(
                        num_list=[self.response_struct['data'][i - 3], self.response_struct['data'][i - 2],
                                  self.response_struct['data'][i - 1], self.response_struct['data'][i]]))

                    # # 最高位为符号位s，从高位向下8位为阶码位E,剩余的位23为有效数字M。
                    # sign_bit = int(data_str[0], 2)
                    # exponent_bit = int(data_str[1:9], 2)
                    # M_bit = int(data_str[9:], 2)
                    # # V = (-1)^s *（1+M）* 2^(E-127)
                    # logger.critical(f"{data_str},{data_str[0]},{sign_bit},{data_str[1:9]},{exponent_bit},{data_str[9:]},{M_bit}")
                    # value = ((-1) ** sign_bit )* (1 + M_bit) * (2 ** (exponent_bit - 127))
                    value = self.ieee754_single_to_float(data_str)
                    return_datas.append({
                        "desc": port_types[j],
                        'value': value
                    }
                    )
                    j += 1
                    pass

                case 4:
                    return_datas.append({
                        "desc": port_types[j],
                        'value':
                            f"0X{self.response_struct['data'][i]:02x}"

                    }
                    )
                    j += 1
                    pass
                case 5:
                    return_datas.append({
                        "desc": port_types[j],
                        'value':
                            f"0X{self.response_struct['data'][i]:02x}"
                    }
                    )
                    j += 1
                    pass
                case 6:
                    return_datas.append({
                        "desc": port_types[j],
                        'value':
                            f"0X{self.response_struct['data'][i]:02x}"
                    }
                    )
                    j += 1
                    pass
                    pass
                case 7:
                    return_datas.append({
                        "desc": port_types[j],
                        'value':
                            f"0X{self.response_struct['data'][i]:02x}"
                    }
                    )
                    j += 1
                    pass
                case _:
                    pass
        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} | "
        parser_message = f"{time_util.get_format_from_time(time.time())} | {self.response_hex}-响应报文解析-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message

    """
        02 05 X
        """

    def parser_function_code_5(self):
        function_desc = """
                       写从机单个开关量输出（ON/OFF）
                       参数长度：4
                       """
        pack_struct = "B B B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=False)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types = ['电磁阀名称', '电磁阀状态']
        valve_desc = ['鼠笼1气路', '鼠笼1气路', '鼠笼2气路', '鼠笼3气路', '鼠笼4气路', '鼠笼5气路', '鼠笼6气路',
                      '鼠笼7气路', '鼠笼8气路', '参考气气路', 'ZOS采样阀气路', '气泵', 'UFC']
        valve_state_desc = [['闭合', '断开'], ['闭合', '断开'], ['闭合', '断开'], ['闭合', '断开'], ['闭合', '断开'],
                            ['闭合', '断开'], ['闭合', '断开'], ['闭合', '断开'], ['闭合', '断开'], ['闭合', '断开'],
                            ['运行', '断开'], ['运行', '停止']]
        valve_index = 0
        j = 0
        for i in range(len(self.response_struct['data'])):
            match i:
                case 1:
                    valve_address = f"0X{self.response_struct['data'][i - 1]:02X}{self.response_struct['data'][i]:02X}"
                    valve_index = int(valve_address, 16)

                    return_datas.append({
                        "desc": port_types[j],
                        'value': valve_desc[valve_index],
                    }
                    )
                    j += 1
                    pass
                case 3:
                    return_datas.append({
                        "desc": port_types[j],
                        'value': valve_state_desc[valve_index][0] if int(self.response_struct['data'][i]) == 255 else
                        valve_state_desc[valve_index][1]
                    }
                    )
                    j += 1
                    pass
                case _:
                    pass

        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} |"
        parser_message = f"{time_util.get_format_from_time(time.time())} | {self.response_hex}-响应报文解析-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message

    """
    02 06 X
    """

    def parser_function_code_6(self):
        function_desc = """
                            写单个保持寄存器
                            参数长度：4
                        """
        pack_struct = "B B B B"
        self.parser_response_pack(pack_struct, struct_type="B", is_pack_return_bytes_nums=False)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types_0 = ['鼠笼阀门操作', '运行状态']
        port_types_0_value = [['鼠笼8', '鼠笼7', '鼠笼6', '鼠笼5', '鼠笼4', '鼠笼3', '鼠笼2', '鼠笼1'],
                              ['运行', '不运行']]
        port_types_1 = ['流量计操作', '值']
        port_types_1_value = [['设定流量计测量范围'],
                              ['4500ml', '2000ml']
                              ]

        j = 0
        for i in range(len(self.response_struct['data'])):
            match i:
                case 1:
                    match self.response_struct['data'][i]:
                        case 0:

                            return_datas.append({
                                "desc": "操作类型",
                                'value': port_types_0[j]
                            }
                            )
                            pass
                        case 1:
                            return_datas.append({
                                "desc": "操作类型",
                                'value': port_types_1[j]
                            }
                            )
                            pass

                        case _:
                            pass
                    j += 1
                    pass
                case 3:
                    match self.response_struct['data'][1]:
                        case 0:

                            value_desc = ""
                            data_list_bit_str = self.int_to_8bit_binary(num_list=[self.response_struct['data'][i]])[0]
                            for data_str_index in range(len(data_list_bit_str)):
                                value_desc += f"{port_types_0_value[0][data_str_index]}:{port_types_0_value[1][1] if int(data_list_bit_str[data_str_index]) == 0 else port_types_0_value[1][0]} , "
                                if data_str_index % 3 == 0:
                                    value_desc += "\n"
                            return_datas.append({
                                "desc": port_types_0[j],
                                'value': value_desc
                            }
                            )
                            pass
                        case 1:
                            return_datas.append({
                                "desc": port_types_1[j],
                                'value': port_types_1_value[0] + ":" + port_types_1_value[1][1] if int(
                                    "".join(self.int_to_8bit_binary(
                                        num_list=[self.response_struct['data'][i - 1],
                                                  self.response_struct['data'][i]])),
                                    2) == 0 else port_types_1_value[1][0]
                            }
                            )
                            pass

                        case _:
                            pass
                    j += 1
                    pass
                case _:
                    pass

        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} |"
        parser_message = f"{time_util.get_format_from_time(time.time())} | {self.response_hex}-响应报文解析-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message

    """
        02 11 X
    """

    def parser_function_code_17(self):
        function_desc = """
                                  读取模块ID信息等
                                  参数长度：17
                                  """
        pack_struct = "B H H H H H H H H"
        self.parser_response_pack(pack_struct, struct_type="H", is_pack_return_bytes_nums=True)
        logger.info(
            f"响应报文-{self.type.value['name']}-{self.type.value['description']}-开始解析报文：{self.response_hex}|{self.response_struct}")
        return_datas = []
        port_types = ['生产厂商', '硬件版本', '软件版本', '出厂地址', '当前地址', '预留1', '预留2', '预留3']
        index = 0
        for data_single in self.response_struct['data']:
            return_datas.append({
                "desc": port_types[index],
                'value': f"{data_single:04X}"
            }
            )
            index += 1
        return_data_str = ""
        for return_data in return_datas:
            return_data_str += f"{return_data['desc']}:{return_data['value']} | "
        parser_message = f"{time_util.get_format_from_time(time.time())}-{self.response_hex}-响应报文解析-{self.type.value['name']}-{self.type.value['description']}-{function_desc}-{return_data_str}"
        logger.info(parser_message)
        return return_datas, parser_message