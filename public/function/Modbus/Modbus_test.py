import serial
import time
import struct

from COM_Scan import scan_serial_ports_with_id


def calculate_crc(data: bytes) -> bytes:
    '''计算Modbus RTU CRC-16，返回低字节在前的两个字节'''
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if (crc & 0x0001):
                crc >>= 1
                crc ^= 0xA001
            else:
                crc >>= 1
    return struct.pack('<H', crc)  # 小端序：低位在前


port_infos = scan_serial_ports_with_id()
if port_infos:
    print("可用串口及其信息：")
    for info in port_infos:
        print(f"- 设备: {info['device']}" + f" #{info['description']}")
        print()
else:
    print("未发现可用串口。")

# 设置串口参数
sendser = serial.Serial(
    port='COM5',  # 改为串口选择
    baudrate=9600,
    bytesize=8,
    parity='N',
    stopbits=1,
    timeout=1
)
getser = serial.Serial(
    port='COM3',  # 改为串口选择
    baudrate=9600,
    bytesize=8,
    parity='N',
    stopbits=1,
    timeout=1
)
# 数据帧，需要输入
slave_id = 0xA1  # 设备id
function_code = 0xAA  # 功能码

# 构造报文（无CRC）
request_pdu = struct.pack('>B B', slave_id, function_code)
frame = request_pdu

print("发送报文：", frame.hex())

# 发送指令
sendser.write(frame)

# 接收响应
time.sleep(0.2)
response = getser.read(256)  # 最多读取256字节
print("接收响应：", response)

getser.close()
sendser.close()
