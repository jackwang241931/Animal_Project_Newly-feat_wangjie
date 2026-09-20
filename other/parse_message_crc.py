def calculate_crc16_modbus(data):
    """
    计算Modbus CRC16校验码
    """
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc >>= 1
                crc ^= 0xA001
            else:
                crc >>= 1
    return crc


def parse_and_verify_crc(hex_string):
    """
    解析16进制字符串并验证CRC
    """
    # 去除可能的空格和转换为大写
    hex_string = hex_string.replace(" ", "").upper()

    print(f"原始报文: {hex_string}")

    # 将16进制字符串转换为字节数组
    if len(hex_string) % 2 != 0:
        raise ValueError("16进制字符串长度必须为偶数")

    # 分离数据部分和CRC部分
    data_hex = hex_string[:-4]  # 除了最后4位的数据部分
    crc_hex = hex_string[-4:]  # 最后4位CRC

    print(f"数据部分: {data_hex}")
    print(f"报文自带CRC: {crc_hex}")

    # 转换为字节数组
    data_bytes = bytes.fromhex(data_hex)
    reported_crc = int(crc_hex, 16)

    # 计算CRC
    calculated_crc = calculate_crc16_modbus(data_bytes)

    # 由于Modbus CRC是小端序，需要交换字节顺序来比较
    calculated_crc_swapped = ((calculated_crc & 0xFF) << 8) | ((calculated_crc >> 8) & 0xFF)

    print(f"计算得到的CRC: {calculated_crc:04X}")
    print(f"CRC字节序调整小端序后: {calculated_crc_swapped:04X}")
    print(f"报文中的CRC: {reported_crc:04X}")

    # 比较CRC
    if calculated_crc_swapped == reported_crc:
        print("✓ CRC校验通过！")
        return True
    else:
        print("✗ CRC校验失败！")
        return False


# 测试你的报文
hex_message = "03040a01f400190114000440fa8da0"
hex_message_2 = "03040a01f400fa011e00030184b261"
hex_message_3 = "03040a01f400f9011f0084a8fa8da0"
print(f"{'-'*5000}")
parse_and_verify_crc(hex_message)
print(f"{'-'*5000}")
parse_and_verify_crc(hex_message_2)
print(f"{'-'*5000}")
parse_and_verify_crc(hex_message_3)