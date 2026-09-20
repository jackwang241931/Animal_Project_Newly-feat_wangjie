import serial.tools.list_ports


# 选项框显示print(f"- 设备: {info['device']}"+f" #{info['description']}")内容，但是选中以后返回"device"串口名即可
def scan_serial_ports_with_id():
    ports = serial.tools.list_ports.comports()
    results = []
    id = 0
    for port in ports:
        results.append({
            "id": id,
            "device": port.device,  # 串口名（如 COM3 或 /dev/ttyUSB0）
            "description": port.description,  # 描述（如 USB-SERIAL CH340）
        })
        id += 1

    return results


# 示例用法
if __name__ == "__main__":
    port_infos = scan_serial_ports_with_id()
    if port_infos:
        print("可用串口及其信息：")
        for info in port_infos:
            print(f"- 设备: {info['device']}" + f" #{info['description']}")
            print()
    else:
        print("未发现可用串口。")
