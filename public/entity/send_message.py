class Send_Message:
    """
    数据监控发送查询报文的结构体
    """

    def __init__(self, slave_address, slave_desc, function_code, function_desc, message, mouse_cage_number=1):
        """

        :param slave_address:  模块地址
        :param slave_desc: 模块描述
        :param function_code: 功能码
        :param function_desc: 功能码描述
        :param message: 数据，格式为 {
            'port': '',
            'data': '',
            'slave_id': 0,
            'function_code': 0,
        }
        :param mouse_cage_number:鼠笼编号
        """
        self.slave_address = slave_address
        self.slave_desc = slave_desc
        self.function_code = function_code
        self.function_desc = function_desc
        self.message = message
        self.mouse_cage_number = mouse_cage_number
        pass
