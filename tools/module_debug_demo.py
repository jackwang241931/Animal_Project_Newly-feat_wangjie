"""独立的环境/称重/饮食/饮水模块串口调试 demo。

运行方式：
    python tools/module_debug_demo.py

这个文件不依赖正式实验启动流程。它只打开用户选择的串口，按界面中的顺序
逐条发送请求，并在表格中记录完整的请求帧、响应帧和解析结果。
"""

from __future__ import annotations

import argparse
import struct
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable

import serial
from serial.tools import list_ports

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QDoubleSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)


DEFAULT_BAUDRATE = 115200
DEFAULT_TIMEOUT = 1.0
# 与 config/monitor_datas_config.ini 的 [SEND] delay 以及正式轮询线程一致。
DEFAULT_MODULE_INTERVAL = 0.52
DEFAULT_ROUND_INTERVAL = 0.0
MAX_LOG_ROWS = 2000


@dataclass(frozen=True)
class ModuleSpec:
    key: str
    label: str
    protocol_name: str
    base_address: int
    read_data: tuple[int, int, int, int]
    description: str


@dataclass(frozen=True)
class RequestConfig:
    cage_one_address: int
    function_code: int
    data: tuple[int, int, int, int]


# 地址和读取数据与正式程序 Modbus_Type.py 中的监控数据报文保持一致。
MODULES = (
    ModuleSpec("environment", "环境 ENM", "ENM", 0x01, (0x00, 0x00, 0x00, 0x07), "功能码04，13字节环境数据"),
    ModuleSpec("weight", "称重 WM", "WM", 0x04, (0x04, 0x01, 0x00, 0x02), "功能码04，单值/30点可切换"),
    ModuleSpec("food", "饮食 EM", "EM", 0x03, (0x04, 0x01, 0x00, 0x02), "功能码04，单个重量值"),
    ModuleSpec("water", "饮水 DWM", "DWM", 0x02, (0x04, 0x01, 0x00, 0x02), "功能码04，单个重量值"),
)
MODULE_BY_KEY = {module.key: module for module in MODULES}
DEFAULT_ORDER = [module.key for module in MODULES]
WEIGHT_30_DATA = (0x04, 0x01, 0x00, 0x3C)

DEFAULT_REQUEST_CONFIGS = {
    "environment": RequestConfig(0x11, 0x04, MODULE_BY_KEY["environment"].read_data),
    "weight_single": RequestConfig(0x14, 0x04, MODULE_BY_KEY["weight"].read_data),
    "weight_30": RequestConfig(0x14, 0x04, WEIGHT_30_DATA),
    "food": RequestConfig(0x13, 0x04, MODULE_BY_KEY["food"].read_data),
    "water": RequestConfig(0x12, 0x04, MODULE_BY_KEY["water"].read_data),
}

REQUEST_CONFIG_ROWS = (
    ("environment", "环境 ENM"),
    ("weight_single", "称重 WM（单值）"),
    ("weight_30", "称重 WM（30点）"),
    ("food", "饮食 EM"),
    ("water", "饮水 DWM"),
)


def calculate_crc(data: bytes) -> bytes:
    """计算 Modbus RTU CRC-16，小端返回。"""
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 0x0001:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return struct.pack("<H", crc)


def build_frame(slave_id: int, function_code: int, data: Iterable[int]) -> bytes:
    data_bytes = bytes(data)
    if len(data_bytes) != 4:
        raise ValueError("当前 demo 只支持4字节读取参数")
    body = bytes((slave_id, function_code)) + data_bytes
    return body + calculate_crc(body)


def cage_slave_id(cage_number: int, cage_one_address: int) -> int:
    if not 1 <= cage_number <= 8:
        raise ValueError("笼号必须在1到8之间")
    if not 0x10 <= cage_one_address <= 0x1F:
        raise ValueError("笼1地址必须在0x10到0x1F之间")
    return cage_one_address + (cage_number - 1) * 0x10


def parse_hex_byte(text: str, field_name: str) -> int:
    value = text.strip()
    if value.lower().startswith("0x"):
        value = value[2:]
    if not value or len(value) > 2:
        raise ValueError(f"{field_name}必须是1字节十六进制数，例如04")
    try:
        return int(value, 16)
    except ValueError as exc:
        raise ValueError(f"{field_name}不是有效的十六进制数") from exc


def parse_hex_data(text: str, field_name: str) -> tuple[int, int, int, int]:
    value = text.replace("0x", "").replace("0X", "")
    for separator in (" ", ",", "，", "-", "_"):
        value = value.replace(separator, "")
    if len(value) != 8:
        raise ValueError(f"{field_name}必须正好是4字节，例如04 01 00 02")
    try:
        return tuple(bytes.fromhex(value))
    except ValueError as exc:
        raise ValueError(f"{field_name}不是有效的十六进制数据") from exc


def parse_cages(text: str) -> list[int]:
    """支持 1,2,3 和 1-8 两种笼号写法。"""
    result: list[int] = []
    for part in text.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start_text, end_text = part.split("-", 1)
            start, end = int(start_text), int(end_text)
            result.extend(range(start, end + 1))
        else:
            result.append(int(part))
    result = sorted(set(result))
    if not result or any(cage < 1 or cage > 8 for cage in result):
        raise ValueError("笼号必须是1到8，例如：1 或 1,3,5 或 1-8")
    return result


def hex_text(data: bytes) -> str:
    return data.hex(" ").upper()


def signed_32(data: bytes) -> int:
    if len(data) != 4:
        raise ValueError("重量数据必须是4字节")
    return int.from_bytes(data, byteorder="big", signed=True)


def parse_measurement(module: ModuleSpec, payload: bytes) -> str:
    """只做调试展示用解析，原始响应始终完整保留在表格中。"""
    if module.key == "weight":
        if len(payload) == 4:
            return f"单值: {signed_32(payload) / 100:.2f} g"
        if len(payload) == 120:
            values = [signed_32(payload[index : index + 4]) / 100 for index in range(0, 120, 4)]
            packed = ",".join(f"{value:.2f}" for value in values)
            return f"30点: {packed} g"
        return f"称重数据长度异常: {len(payload)} 字节"

    if module.key in {"food", "water"}:
        if len(payload) == 4:
            return f"重量: {signed_32(payload) / 100:.2f} g"
        return f"重量数据长度异常: {len(payload)} 字节"

    return f"环境数据: {len(payload)} 字节"


def read_exact(ser: serial.Serial, size: int, deadline: float) -> bytes:
    chunks: list[bytes] = []
    received = 0
    while received < size and time.monotonic() < deadline:
        chunk = ser.read(size - received)
        if not chunk:
            continue
        chunks.append(chunk)
        received += len(chunk)
    return b"".join(chunks)


def read_modbus_response(ser: serial.Serial, timeout: float) -> bytes:
    """按 Modbus 返回字节数读取，兼容4字节单值和0x78字节30点。"""
    deadline = time.monotonic() + timeout
    header = read_exact(ser, 3, deadline)
    if len(header) != 3:
        raise TimeoutError(f"响应头不完整，仅收到{len(header)}字节")

    function_code = header[1]
    if function_code & 0x80:
        total_size = 5
    else:
        byte_count = header[2]
        if byte_count > 240:
            raise ValueError(f"响应数据长度异常: 0x{byte_count:02X}")
        total_size = 3 + byte_count + 2

    tail = read_exact(ser, total_size - 3, deadline)
    response = header + tail
    if len(response) != total_size:
        raise TimeoutError(f"响应不完整，期望{total_size}字节，实际{len(response)}字节")
    if calculate_crc(response[:-2]) != response[-2:]:
        raise ValueError("响应CRC校验失败")
    if function_code & 0x80:
        raise ValueError(f"设备异常响应: 功能码0x{function_code:02X}，异常码0x{response[2]:02X}")
    return response


def exchange_once(
    ser: serial.Serial,
    module: ModuleSpec,
    cage_number: int,
    request_config: RequestConfig,
    timeout: float,
) -> dict:
    slave_id = cage_slave_id(cage_number, request_config.cage_one_address)
    request = build_frame(slave_id, request_config.function_code, request_config.data)
    started = time.monotonic()
    result = {
        "timestamp": datetime.now().strftime("%H:%M:%S.%f")[:-3],
        "cage": cage_number,
        "module": module.label,
        "protocol": module.protocol_name,
        "request": hex_text(request),
        "response": "",
        "parsed": "",
        "status": "",
        "elapsed_ms": "",
    }
    try:
        ser.reset_input_buffer()
        ser.reset_output_buffer()
        ser.write(request)
        response = read_modbus_response(ser, timeout)
        payload = response[3:-2]
        result["response"] = hex_text(response)
        result["parsed"] = parse_measurement(module, payload)
        result["status"] = "正常"
    except Exception as exc:
        result["status"] = f"失败: {exc}"
    result["elapsed_ms"] = f"{(time.monotonic() - started) * 1000:.1f}"
    return result


class MonitorWorker(QThread):
    event = pyqtSignal(dict)
    state = pyqtSignal(str)

    def __init__(self, port: str, cages: list[int], order: list[str], weight_30: bool, request_configs: dict[str, RequestConfig], timeout: float, request_interval: float, interval: float, continuous: bool):
        super().__init__()
        self.port = port
        self.cages = cages
        self.order = order
        self.weight_30 = weight_30
        self.request_configs = request_configs
        self.timeout = timeout
        self.request_interval = request_interval
        self.interval = interval
        self.continuous = continuous
        self._stop_event = threading.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def run(self) -> None:
        round_number = 0
        try:
            with serial.Serial(self.port, DEFAULT_BAUDRATE, timeout=min(self.timeout, 0.2), write_timeout=self.timeout) as ser:
                self.state.emit(f"串口已连接: {self.port}")
                while not self._stop_event.is_set():
                    round_number += 1
                    for cage in self.cages:
                        for order_index, key in enumerate(self.order, start=1):
                            if self._stop_event.is_set():
                                break
                            module = MODULE_BY_KEY[key]
                            request_key = (
                                "weight_30"
                                if key == "weight" and self.weight_30
                                else "weight_single"
                                if key == "weight"
                                else key
                            )
                            record = exchange_once(
                                ser,
                                module,
                                cage,
                                self.request_configs[request_key],
                                self.timeout,
                            )
                            record["round"] = round_number
                            record["order"] = order_index
                            self.event.emit(record)
                            if self._stop_event.wait(self.request_interval):
                                break
                        if self._stop_event.is_set():
                            break
                    if not self.continuous:
                        break
                    self._stop_event.wait(self.interval)
                self.state.emit("监控已停止")
        except Exception as exc:
            self.state.emit(f"串口打开失败: {exc}")


class ModuleDebugWindow(QMainWindow):
    def __init__(self, initial_port: str | None = None):
        super().__init__()
        self.worker: MonitorWorker | None = None
        self.initial_port = initial_port
        self.setWindowTitle("模块独立串口监控 Demo")
        self.resize(1500, 850)
        self._build_ui()
        self.refresh_ports()

    def _build_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        settings_row = QHBoxLayout()

        connection_box = QGroupBox("连接与采集参数")
        connection_layout = QFormLayout(connection_box)
        self.port_combo = QComboBox()
        self.refresh_button = QPushButton("刷新串口")
        port_row = QHBoxLayout()
        port_row.addWidget(self.port_combo, 1)
        port_row.addWidget(self.refresh_button)
        connection_layout.addRow("串口", port_row)
        self.cages_edit = QLineEdit("1")
        self.cages_edit.setPlaceholderText("例如：1 或 1,3,5 或 1-8")
        connection_layout.addRow("笼号", self.cages_edit)
        self.timeout_spin = QDoubleSpinBox()
        self.timeout_spin.setRange(0.1, 30.0)
        self.timeout_spin.setSingleStep(0.1)
        self.timeout_spin.setValue(DEFAULT_TIMEOUT)
        self.timeout_spin.setSuffix(" s")
        connection_layout.addRow("单条超时", self.timeout_spin)
        self.interval_spin = QDoubleSpinBox()
        self.interval_spin.setRange(0.0, 3600.0)
        self.interval_spin.setSingleStep(0.1)
        self.interval_spin.setValue(DEFAULT_ROUND_INTERVAL)
        self.interval_spin.setSuffix(" s")
        connection_layout.addRow("轮次间隔", self.interval_spin)
        self.request_interval_spin = QDoubleSpinBox()
        self.request_interval_spin.setRange(0.0, 10.0)
        self.request_interval_spin.setSingleStep(0.05)
        self.request_interval_spin.setValue(DEFAULT_MODULE_INTERVAL)
        self.request_interval_spin.setSuffix(" s")
        connection_layout.addRow("模块报文间隔", self.request_interval_spin)
        self.weight_30_check = QCheckBox("称重读取30个数据（0401003C）；不勾选为单值（04010002）")
        connection_layout.addRow("称重模式", self.weight_30_check)
        settings_row.addWidget(connection_box, 3)

        request_box = QGroupBox("模块请求参数（十六进制，可修改，CRC自动计算）")
        request_layout = QGridLayout(request_box)
        request_layout.addWidget(QLabel("模块"), 0, 0)
        request_layout.addWidget(QLabel("笼1地址"), 0, 1)
        request_layout.addWidget(QLabel("功能码"), 0, 2)
        request_layout.addWidget(QLabel("数据区（4字节）"), 0, 3)
        request_layout.addWidget(QLabel("说明"), 0, 4)
        self.request_edits: dict[str, dict[str, QLineEdit]] = {}
        for row, (request_key, label) in enumerate(REQUEST_CONFIG_ROWS, start=1):
            address_edit = QLineEdit()
            address_edit.setMaximumWidth(90)
            function_edit = QLineEdit()
            function_edit.setMaximumWidth(90)
            data_edit = QLineEdit()
            data_edit.setMaximumWidth(220)
            self.request_edits[request_key] = {
                "address": address_edit,
                "function": function_edit,
                "data": data_edit,
            }
            request_layout.addWidget(QLabel(label), row, 0)
            request_layout.addWidget(address_edit, row, 1)
            request_layout.addWidget(function_edit, row, 2)
            request_layout.addWidget(data_edit, row, 3)
            request_layout.addWidget(
                QLabel("其他笼地址按笼号自动增加0x10"),
                row,
                4,
            )

        request_button_row = QHBoxLayout()
        reset_requests_button = QPushButton("恢复默认报文")
        reset_requests_button.clicked.connect(self._reset_request_configs)
        request_button_row.addWidget(reset_requests_button)
        request_button_row.addWidget(
            QLabel("示例：笼1地址11、功能码04、数据区00 00 00 07")
        )
        request_button_row.addStretch(1)
        request_layout.addLayout(
            request_button_row,
            len(REQUEST_CONFIG_ROWS) + 1,
            0,
            1,
            5,
        )
        self._reset_request_configs()
        settings_row.addWidget(request_box, 5)

        module_box = QGroupBox("模块启用与发送顺序（可拖拽调整）")
        module_layout = QHBoxLayout(module_box)
        self.module_checks: dict[str, QCheckBox] = {}
        check_layout = QVBoxLayout()
        check_layout.addWidget(QLabel("启用模块"))
        for module in MODULES:
            check = QCheckBox(module.label)
            check.setChecked(True)
            self.module_checks[module.key] = check
            check_layout.addWidget(check)
        module_layout.addLayout(check_layout)
        order_layout = QVBoxLayout()
        order_layout.addWidget(QLabel("发送顺序"))
        self.order_list = QListWidget()
        self.order_list.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.order_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self._reset_order()
        order_layout.addWidget(self.order_list)
        reset_order_button = QPushButton("恢复默认顺序")
        reset_order_button.clicked.connect(self._reset_order)
        order_layout.addWidget(reset_order_button)
        module_layout.addLayout(order_layout, 1)
        settings_row.addWidget(module_box, 4)
        layout.addLayout(settings_row)

        button_row = QHBoxLayout()
        self.once_button = QPushButton("发送单轮")
        self.start_button = QPushButton("开始连续监控")
        self.stop_button = QPushButton("停止")
        self.stop_button.setEnabled(False)
        button_row.addWidget(self.once_button)
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        button_row.addStretch(1)
        self.state_label = QLabel("未连接")
        button_row.addWidget(self.state_label)
        layout.addLayout(button_row)

        self.table = QTableWidget(0, 11)
        self.table.setHorizontalHeaderLabels([
            "时间", "轮次", "笼号", "顺序", "模块", "协议地址", "请求报文", "响应报文", "解析结果", "状态", "耗时(ms)"
        ])
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.setMinimumHeight(320)
        self.table.horizontalHeader().setStretchLastSection(True)
        layout.addWidget(self.table, 1)

        self.detail = QPlainTextEdit()
        self.detail.setReadOnly(True)
        self.detail.setMaximumHeight(90)
        layout.addWidget(self.detail)

        self.refresh_button.clicked.connect(self.refresh_ports)
        self.once_button.clicked.connect(lambda: self._start_monitor(False))
        self.start_button.clicked.connect(lambda: self._start_monitor(True))
        self.stop_button.clicked.connect(self.stop_monitor)

    def _reset_order(self) -> None:
        self.order_list.clear()
        for key in DEFAULT_ORDER:
            self.order_list.addItem(QListWidgetItem(MODULE_BY_KEY[key].label, self.order_list))
            self.order_list.item(self.order_list.count() - 1).setData(Qt.ItemDataRole.UserRole, key)

    def _reset_request_configs(self) -> None:
        for request_key, config in DEFAULT_REQUEST_CONFIGS.items():
            edits = self.request_edits[request_key]
            edits["address"].setText(f"{config.cage_one_address:02X}")
            edits["function"].setText(f"{config.function_code:02X}")
            edits["data"].setText(" ".join(f"{value:02X}" for value in config.data))

    def _request_configs(self) -> dict[str, RequestConfig]:
        configs: dict[str, RequestConfig] = {}
        labels = dict(REQUEST_CONFIG_ROWS)
        for request_key, edits in self.request_edits.items():
            label = labels[request_key]
            cage_one_address = parse_hex_byte(
                edits["address"].text(),
                f"{label}笼1地址",
            )
            if not 0x10 <= cage_one_address <= 0x1F:
                raise ValueError(f"{label}笼1地址必须在10到1F之间")
            function_code = parse_hex_byte(
                edits["function"].text(),
                f"{label}功能码",
            )
            if function_code == 0 or function_code >= 0x80:
                raise ValueError(f"{label}功能码必须在01到7F之间")
            configs[request_key] = RequestConfig(
                cage_one_address=cage_one_address,
                function_code=function_code,
                data=parse_hex_data(
                    edits["data"].text(),
                    f"{label}数据区",
                ),
            )
        return configs

    def refresh_ports(self) -> None:
        current = self.port_combo.currentData()
        self.port_combo.clear()
        ports = list(list_ports.comports())
        for port in ports:
            self.port_combo.addItem(f"{port.device}  {port.description}", port.device)
        if self.initial_port and self.port_combo.findData(self.initial_port) >= 0:
            self.port_combo.setCurrentIndex(self.port_combo.findData(self.initial_port))
        elif current and self.port_combo.findData(current) >= 0:
            self.port_combo.setCurrentIndex(self.port_combo.findData(current))
        self.state_label.setText(f"发现串口 {len(ports)} 个")

    def _selected_order(self) -> list[str]:
        enabled = {key for key, check in self.module_checks.items() if check.isChecked()}
        order: list[str] = []
        for index in range(self.order_list.count()):
            key = self.order_list.item(index).data(Qt.ItemDataRole.UserRole)
            if key in enabled:
                order.append(key)
        if not order:
            raise ValueError("至少启用一个模块")
        return order

    def _start_monitor(self, continuous: bool) -> None:
        if self.worker and self.worker.isRunning():
            return
        port = self.port_combo.currentData()
        if not port:
            QMessageBox.warning(self, "缺少串口", "请先刷新并选择可用串口")
            return
        try:
            cages = parse_cages(self.cages_edit.text())
            order = self._selected_order()
            request_configs = self._request_configs()
        except ValueError as exc:
            QMessageBox.warning(self, "参数错误", str(exc))
            return
        self.table.setRowCount(0)
        self.detail.clear()
        self.worker = MonitorWorker(
            port=port,
            cages=cages,
            order=order,
            weight_30=self.weight_30_check.isChecked(),
            request_configs=request_configs,
            timeout=self.timeout_spin.value(),
            request_interval=self.request_interval_spin.value(),
            interval=self.interval_spin.value(),
            continuous=continuous,
        )
        self.worker.event.connect(self._append_record)
        self.worker.state.connect(self._set_state)
        self.worker.finished.connect(self._worker_finished)
        self.once_button.setEnabled(False)
        self.start_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self._set_state(f"准备发送：笼号 {cages}，顺序 {[MODULE_BY_KEY[key].protocol_name for key in order]}")
        self.worker.start()

    def stop_monitor(self) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self._set_state("正在停止...")

    def _worker_finished(self) -> None:
        self.once_button.setEnabled(True)
        self.start_button.setEnabled(True)
        self.stop_button.setEnabled(False)

    def _set_state(self, text: str) -> None:
        self.state_label.setText(text)
        self.detail.appendPlainText(f"{datetime.now().strftime('%H:%M:%S')}  {text}")

    def _append_record(self, record: dict) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        values = [
            record.get("timestamp", ""),
            record.get("round", ""),
            record.get("cage", ""),
            record.get("order", ""),
            record.get("module", ""),
            f"0x{int(record.get('request', '00 00').split()[0], 16):02X}",
            record.get("request", ""),
            record.get("response", ""),
            record.get("parsed", ""),
            record.get("status", ""),
            record.get("elapsed_ms", ""),
        ]
        for column, value in enumerate(values):
            self.table.setItem(row, column, QTableWidgetItem(str(value)))
        if self.table.rowCount() > MAX_LOG_ROWS:
            self.table.removeRow(0)
        self.table.scrollToBottom()

    def closeEvent(self, event) -> None:
        if self.worker and self.worker.isRunning():
            self.worker.stop()
            self.worker.wait(3000)
        event.accept()


def main() -> int:
    parser = argparse.ArgumentParser(description="环境/称重/饮食/饮水模块独立监控 demo")
    parser.add_argument("--port", help="启动时优先选择的串口，例如 COM3")
    args = parser.parse_args()
    app = QApplication(sys.argv)
    window = ModuleDebugWindow(initial_port=args.port)
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
