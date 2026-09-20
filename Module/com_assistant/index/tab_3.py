import re
import time
import typing

from loguru import logger

from Module.com_assistant.ui.tab3_window import Ui_tab3_window
from public.config_class.global_setting import global_setting
from public.entity.MyQThread import MyQThread
from public.entity.enum.Public_Enum import AppState
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.function.Modbus.COM_Scan import scan_serial_ports_with_id
from public.function.Modbus.Modbus import ModbusRTUMaster
from theme.ThemeQt6 import ThemedWindow

from PyQt6 import QtGui
from PyQt6.QtCore import QRect, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QListWidget, QPushButton, QLineEdit

from public.util.number_util import number_util
from public.util.time_util import time_util


class read_queue_data_Thread(MyQThread):
    def __init__(self, name):
        super().__init__(name)
        self.queue = None
        self.update_status_main_signal_gui_update: pyqtSignal(str) = None
        pass

    def dosomething(self):
        if not self.queue.empty():
            try:
                message: ObjectQueueItem = self.queue.get()
            except Exception as e:
                logger.error(f"{self.name}发生错误{e}")
                return
            # message 结构{'to'发往哪个线程，'data'数据，‘from'从哪来}

            if message is not None and isinstance(message, ObjectQueueItem) and message.to == 'tab_3':
                logger.error(f"{self.name}_get_message:{message}")
                match message.title:
                    case '':
                        if self.update_status_main_signal_gui_update is not None:
                            self.update_status_main_signal_gui_update.emit(message.data)
                            pass
                    case _:
                        pass

            else:
                # 把消息放回去
                self.queue.put(message)

        pass


read_queue_data_thread = read_queue_data_Thread(name="tab_3_read_queue_data_thread")
class Send_thread(MyQThread):
    # 线程信号

    def __init__(self, name=None,  modbus=None, send_message=None):
        super().__init__(name)

        self.modbus = modbus
        self.send_message = send_message
        self.is_start = True
        pass

    def __del__(self):
        logger.debug(f"线程{self.name}被销毁!")

    def init_modBus(self):
        try:

                self.modbus = ModbusRTUMaster(
                    port=self.send_message['port'],
                    timeout=float(
                        global_setting.get_setting('monitor_data')['Serial']['timeout']),
                    origin="tab_3"
                                              )
        except:
            pass
        pass

    def set_send_message(self, send_message):
        self.send_message = send_message

    def set_modbus(self, modbus):
        self.modbus = modbus

    def dosomething(self):
        if self.is_start:
            self.init_modBus()
            try:
                logger.info(self.send_message)
                response, response_hex, send_state = self.modbus.send_command(
                    slave_id=self.send_message['slave_id'],
                    function_code=self.send_message['function_code'],
                    data_hex_list=self.send_message['data']
                    ,is_parse_response=False
                )
                # 响应报文是正确的，即发送状态时正确的 进行解析响应报文
                if send_state:
                    return_data, parser_message = self.modbus.parse_response(response=response,
                                                                             response_hex=response.hex(),
                                                                             send_state=True,
                                                                             slave_id=
                                                                             self.send_message['slave_id'],
                                                                             function_code=
                                                                             self.send_message['function_code'], )

                    # 把返回数据返回给源头

                    message_struct = ObjectQueueItem(to="tab_3",
                                                     data=parser_message,
                                                     origin='tab_3_send_thread')
                    global_setting.get_setting("send_message_queue").put(message_struct)
                    logger.debug(f"tab_3_send_thread将响应报文的解析数据返回源头：{message_struct}")
                    pass
                self.is_start = False
            except Exception as e:
                logger.error(f"{e}")
            finally:
                self.is_start = False
            time.sleep(1)
        pass

    pass


class Tab_3(ThemedWindow):
    update_status_main_signal_gui_update = pyqtSignal(str)

    def showEvent(self, a0: typing.Optional[QtGui.QShowEvent]) -> None:
        logger.warning(f"tab3——show")
        if self.send_thread is not None and self.send_thread.isRunning():
            self.send_thread.resume()

    def hideEvent(self, a0: typing.Optional[QtGui.QHideEvent]) -> None:
        logger.warning(f"tab3——hidden")
        if self.send_thread is not None and self.send_thread.isRunning():
            self.send_thread.pause()

    def __init__(self, parent=None, geometry: QRect = None, title=""):
        super().__init__()

        # 下拉框数据列表
        self.ports = []
        # 发送的数据结构
        self.send_message = {
            'port': '',
            'data': '',
            'slave_id': 0,
            'function_code': 0,
            'timeout': 0
        }
        self.modbus = None
        # 发送报文线程
        self.send_thread: Send_thread = None
        # 实例化ui
        self._init_ui(parent, geometry, title)
        # 获得相关数据
        self._init_data()
        # 实例化自定义ui
        self._init_customize_ui()

        # 实例化功能
        self._init_function()
        # 加载qss样式表
        self._init_style_sheet()
        pass

        # 实例化ui

    def _init_ui(self, parent=None, geometry: QRect = None, title=""):
        # 将ui文件转成py文件后 直接实例化该py文件里的类对象  uic工具转换之后就是这一段代码
        # 有父窗口添加父窗口
        if parent != None and geometry != None:
            self.setParent(parent)
            self.setGeometry(geometry)
        else:
            pass
        self.ui = Ui_tab3_window()
        self.ui.setupUi(self)

        self._retranslateUi()
        pass

    # 实例化自定义ui
    def _init_customize_ui(self):
        # 实例化下拉框
        self.init_port_combox()
        # 实例化图表
        # chart_layout_frame: QFrame = self.findChild(QFrame, "chart_layout_frame")
        # charts = tab2_tab0_charts(datas=None,
        #                           parent=chart_layout_frame,
        #                           title=f"数据情况",
        #                           object_name=chart_layout_frame.objectName(),
        #                           )
        pass

    # 实例化下拉框
    def init_port_combox(self):
        port_combox: QComboBox = self.findChild(QComboBox, "tab_3_port_combox")
        if port_combox == None:
            logger.error("实例化端口下拉框失败！")
            return
        port_combox.clear()
        for port_obj in self.ports:
            port_combox.addItem(f"- 设备: {port_obj['device']}" + f" #{port_obj['description']}")
            pass
        if len(self.ports) != 0:
            # 默认下拉项
            self.send_message['port'] = self.ports[0]['device']
            self.send_response_text(
                f"{time_util.get_format_from_time(time.time())}- 设备: {self.ports[0]['device']}" + f" #{self.ports[0]['description']}" + "  默认已被选中!")
        port_combox.disconnect()
        port_combox.currentIndexChanged.connect(self.selectionchange)

    def selectionchange(self, index):
        try:
            self.send_message['port'] = self.ports[index]['device']

            self.send_response_text(
                f"{time_util.get_format_from_time(time.time())}- 设备: {self.ports[index]['device']}" + f" #{self.ports[index]['description']}" + "  已被选中!")
        except Exception as e:
            logger.error(f"{e}")
        pass

    def send_response_text(self, text):
        # 往状态栏发消息
        response_text: QListWidget = self.findChild(QListWidget, "tab_3_responselist")
        if response_text == None:
            logger.error("response_text状态栏未找到！")
            return
        response_text.addItem(text)
        # 滑动滚动条到最底下
        scroll_bar = response_text.verticalScrollBar()
        if scroll_bar != None:
            scroll_bar.setValue(scroll_bar.maximum())
        pass

    # 实例化功能
    def _init_function(self):
        # 实例化按钮信号槽绑定
        self.init_btn_func()
        # 实例化信号
        # 将更新status信号绑定更新status界面函数
        self.update_status_main_signal_gui_update.connect(self.send_response_text)
        global read_queue_data_thread
        read_queue_data_thread.update_status_main_signal_gui_update = self.update_status_main_signal_gui_update
        read_queue_data_thread.queue = global_setting.get_setting("send_message_queue")
        if read_queue_data_thread is not None and not read_queue_data_thread.isRunning():
            read_queue_data_thread.start()
            pass

    # 实例化按钮信号槽绑定
    def init_btn_func(self):
        # 重新获取端口按钮
        refresh_port_btn: QPushButton = self.findChild(QPushButton, "tab_3_refresh_port_btn")

        refresh_port_btn.clicked.connect(self.refresh_port)
        # 发送数据按钮
        send_btn: QPushButton = self.findChild(QPushButton, "send_btn")

        send_btn.clicked.connect(self.send_data)
        pass

    # 发送数据
    def send_data(self):

        # 验证数据有效性
        try:
            if self.validate_data():
                state = global_setting.get_setting("app_state")
                # 根据是否已经实验来发送到自己还是main_monitor_data
                if state !=AppState.MONITORING:
                    # 发送数据
                    try:
                        if self.send_thread is None:
                            logger.info("初始化串口")
                            self.send_thread = None
                            self.send_thread = Send_thread(name="tab_3_COM_Send_Thread",
                                                           modbus=None, send_message=self.send_message)

                            self.send_thread.is_start = True
                            self.send_thread.start()

                            return
                            # 发送
                        logger.info("未初始化串口对象,使用之前串口实例化对象")
                        self.send_thread.set_send_message(self.send_message)
                        self.send_thread.is_start = True
                    except Exception as e:
                        logger.error(f"{e}")
                else:
                    message_struct = ObjectQueueItem(to="main_monitor_data",
                                                     data=self.send_message,
                                                     origin='tab_3')
                    global_setting.get_setting("send_message_queue").put(message_struct)
                    logger.debug(f"tab_3开始发送消息:{message_struct}")
                pass
        except Exception as e:
            logger.error(f"{e}")
            self.send_response_text(e)

    # 判断是否是16进制
    def is_hex(self, s, allow_prefix=False):
        if allow_prefix:
            pattern = r"^(0[xX])?[0-9A-Fa-f]+$"  # 允许可选前缀
        else:
            pattern = r"^[0-9A-Fa-f]+$"  # 纯十六进制字符
        return bool(re.match(pattern, s))

    # 验证发送数据
    def validate_data(self):
        # 获取输入控件
        data_line: QLineEdit = self.findChild(QLineEdit, "data_line")
        timeout_line: QLineEdit = self.findChild(QLineEdit, "timeout_line")
        slave_line: QLineEdit = self.findChild(QLineEdit, "slave_line")
        function_line: QLineEdit = self.findChild(QLineEdit, "function_line")

        if len(data_line.text().strip()) != 0 and (not self.is_hex(data_line.text().strip(),
                                                                   allow_prefix=True) or len(
            data_line.text().strip()) > 8):
            self.send_response_text(f"{time_util.get_format_from_time(time.time())}- 数据必须为16进制 且数据小于等于8位!")
            return False
        if len(slave_line.text().strip()) == 0 or not self.is_hex(slave_line.text().strip(),
                                                                  allow_prefix=True) or len(
            slave_line.text().strip()) > 2:
            self.send_response_text(f"{time_util.get_format_from_time(time.time())}-不能设置空设备地址或者 必须为16进制 或小于等于2位!")
            return False
        if len(function_line.text().strip()) == 0 or not self.is_hex(function_line.text().strip(),
                                                                     allow_prefix=True) or len(
            function_line.text().strip()) > 2:
            self.send_response_text(f"{time_util.get_format_from_time(time.time())}-不能设置空操作码或者 必须为16进制 或小于等于2位!")
            return False
        if len(timeout_line.text().strip()) == 0 or not bool(
                re.match(r'^[0-9]+$', timeout_line.text().strip())):
            self.send_response_text(f"{time_util.get_format_from_time(time.time())}-不能设置空Timeout或必须为数字!")
            return False

        if self.send_message['port'].strip() == '':
            self.send_response_text(f"{time_util.get_format_from_time(time.time())}-未选择端口!")
            return False
        logger.info(data_line.text().strip())
        logger.info(type(data_line.text().strip()))

        self.send_message['data'] = number_util.set_int_to_4_bytes_list(data_line.text().strip())

        logger.info(self.send_message['data'])
        self.send_message['slave_id'] = format(int(slave_line.text().strip(), 16), '02X')
        self.send_message['function_code'] = format(int(function_line.text().strip(), 16), '02X')
        self.send_message['timeout'] = float(timeout_line.text().strip())
        return True
        pass

    # 重新获取端口
    def refresh_port(self):
        self.ports = []
        self._init_data()
        self.init_port_combox()

    # 获得相关数据
    def _init_data(self):
        # 获得下拉框数据
        self.ports = scan_serial_ports_with_id()
        pass
