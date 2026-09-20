import math
import time
import typing

from PyQt6.QtGui import QDoubleValidator
from loguru import logger

from Module.experiment_setting.config.experiment_default_config import get_default_config

from Module.experiment_setting.ui.tab7_window import Ui_tab7_window
from public.component.Guide_tutorial_interface.Tutorial_Manager import TutorialManager
from public.component.dialog.custom.InfoDialog import InfoDialog
from public.config_class.App_Setting import AppSettings

from public.config_class.global_setting import global_setting

from public.entity.MyQThread import MyQThread
from public.entity.enum.Public_Enum import AppState, Tutorial_Type
from public.entity.experiment_setting_entity import Experiment_setting_entity
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.function.Modbus import Modbus_Type

from public.function.Modbus.COM_Scan import scan_serial_ports_with_id
from public.function.Modbus.Modbus import ModbusRTUMaster
from public.function.Modbus.New_Mod_Bus import ModbusRTUMasterNew
from theme.ThemeQt6 import ThemedWindow
from PyQt6 import QtGui
from PyQt6.QtCore import QRect, Qt, pyqtSignal, QTimer, QEvent
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QScrollArea, QGroupBox, QLabel, QSlider, QRadioButton, \
    QGridLayout, QButtonGroup, QComboBox, QListWidget, QPushButton, QMessageBox, QHBoxLayout, QLineEdit, QDoubleSpinBox, \
    QCheckBox

from public.util.time_util import time_util

#logger = logger.bind(category="gui_logger")
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

            if message is not None and isinstance(message, ObjectQueueItem) and message.to== 'tab_7':
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


read_queue_data_thread = read_queue_data_Thread(name="tab_7_read_queue_data_thread")

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
                    origin="tab_7"
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
                    message_struct = ObjectQueueItem(to="tab_7",
                                                     data=parser_message,
                                                     origin='tab_7_send_thread')

                    global_setting.get_setting("send_message_queue").put(message_struct)
                    logger.debug(f"tab_7_send_thread将响应报文的解析数据返回源头：{message_struct}")
                    pass
                self.is_start = False
            except Exception as e:
                logger.error(f"{e}")
            finally:
                self.is_start = False
            time.sleep(1)
        pass

    pass


class Tab_7(ThemedWindow):
    update_status_main_signal_gui_update = pyqtSignal(str)



    def changeEvent(self, event: QEvent):
        """窗口状态改变事件"""
        if event.type() == QEvent.Type.ActivationChange:
            if self.isActiveWindow():
                if self.calibration_checkbox is not None:
                    self.calibration_checkbox.setChecked(global_setting.get_setting('is_auto_calibration', True))
                # print("窗口被激活")
            else:
                # if self.calibration_checkbox is not None:
                #     self.calibration_checkbox.setChecked(global_setting.get_setting('is_auto_calibration', True))
                pass
                # print("窗口失去焦点")

        super().changeEvent(event)
    def showEvent(self, a0: typing.Optional[QtGui.QShowEvent]) -> None:
        # 加载qss样式表
        logger.warning("tab7——show")
        if self.send_thread is not None and self.send_thread.isRunning():
            self.send_thread.resume()
        # 实例化自定义ui
        self._init_customize_ui()
        if self.calibration_checkbox is not None:
            self.calibration_checkbox.setChecked(global_setting.get_setting('is_auto_calibration',True))
        super().showEvent(a0)
    def hideEvent(self, a0: typing.Optional[QtGui.QHideEvent]) -> None:
        logger.warning("tab7--hide")
        if self.send_thread is not None and self.send_thread.isRunning():
            self.send_thread.pause()
        super().hideEvent(a0)
    def setup_tutorial(self):
        # 实例化提示引导器 下面式实例化模板
        if self.tutorial:
            self.tutorial.end_tutorial()

        self.tutorial = TutorialManager(self, "experiment_setting", Tutorial_Type.ARROW_GUIDE,
                                        global_setting.get_setting("app_setting", AppSettings()))

        # 连接教程完成信号
        self.tutorial.tutorial_completed.connect(self.on_tutorial_completed)

        self.tutorial.add_step(self.port_combox,
                               f"步骤1：\n选择正确的串口，这步非常的重要，不然无法联系到传感器。")
        self.tutorial.add_step(self.group_box if self.group_box else self.config_layout,
                               f"步骤2：\n可以对相关传感器进行实验前的相关配置。")
        self.tutorial.add_step(self.response_text,
                               f"Tips1：\n你可以查看传感器的响应信息。")
        self.tutorial.add_step(self.start_btn,
                               f"步骤3：\n最后单击该按钮完成配置。")
        self.tutorial.add_step(self.status_bar.tip_btn,
                               f"Tips2：\n如果还不会操作，可再次单击该按钮查看教程。")
    def __init__(self, parent=None, geometry: QRect = None, title=""):
        super().__init__()
        self.group_box = None
        self.port_combox=None
        self.response_text=None
        self.vr_desc_text:QDoubleSpinBox=None
        self.span_oxygen_desc_text:QDoubleSpinBox=None
        self.span_carbon_desc_text:QDoubleSpinBox=None
        # 创建是否校准复选框
        self.calibration_checkbox = None



        self.experiment_setting: Experiment_setting_entity =None
        # 发送报文线程
        self.send_thread:Send_thread = None
        # 发送的数据结构
        self.send_message = {
            'port': '',
            'data': '',
            'slave_id': 0,
            'function_code': 0,
            'timeout': 0
        }
        # 下拉框数据列表
        self.ports = []
        # 配置json对象
        self.config = None
        # 配置layout
        self.config_layout:QVBoxLayout = None
        # 确定设备配置按钮
        self.start_btn: QPushButton=None
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
        # 实例化提示器
        self.setup_tutorial()
        # 自动启动提示教程 如果有提示页面的话
        QTimer.singleShot(400, self.start_tutorial_if_exists)
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

        self.ui = Ui_tab7_window()

        self.ui.setupUi(self)

        self._retranslateUi()

        pass

    # 实例化自定义ui
    def _init_customize_ui(self):
        # 实例化下拉框
        self.init_port_combox()
        # 读取默认config json
        self.config = get_default_config()
        # logger.error(self.config)
        self.init_config_ui()
        super()._init_customize_ui()
        pass

    # 实例化下拉框
    def init_port_combox(self):
        self.port_combox: QComboBox = self.findChild(QComboBox, "tab_7_port_combox")
        if self.port_combox == None:
            logger.error("实例化端口下拉框失败！")
            return
        self.port_combox.clear()
        for port_obj in self.ports:
            self.port_combox.addItem(f"- 设备: {port_obj['device']}" + f" #{port_obj['description']}")
            pass
        if len(self.ports) != 0:
            # 默认下拉项
            self.send_message['port'] = self.ports[0]['device']
            global_setting.set_setting("port", self.send_message['port'])
            send_message_queue = global_setting.get_setting("send_message_queue")
            send_message_queue.put(ObjectQueueItem(origin='tab_7', to='main_monitor_data', title='set_port',data= self.send_message['port'],
                                                   time=time_util.get_format_from_time(time.time())))
            modbus:ModbusRTUMasterNew =global_setting.get_setting("modbus",None)
            if modbus is None:
                modbus = ModbusRTUMasterNew( self.send_message['port'], baudrate=115200,timeout=float(
                    global_setting.get_setting('monitor_data')['Serial']['timeout']),)
                global_setting.set_setting("modbus", modbus)
            else:
                modbus.close()
                modbus = ModbusRTUMasterNew(self.send_message['port'], baudrate=115200, timeout=float(
                    global_setting.get_setting('monitor_data')['Serial']['timeout']), )
                global_setting.set_setting("modbus", modbus)
            self.send_response_text(
                f"{time_util.get_format_from_time(time.time())}- 设备: {self.ports[0]['device']}" + f" #{self.ports[0]['description']}" + "  默认已被选中!")
        self.port_combox.disconnect()
        self.port_combox.currentIndexChanged.connect(self.selectionchange)

    def selectionchange(self, index):
        try:
            self.send_message['port'] = self.ports[index]['device']
            global_setting.set_setting("port", self.send_message['port'])
            send_message_queue = global_setting.get_setting("send_message_queue")
            send_message_queue.put(ObjectQueueItem(origin='MainWindow_Index', to='main_monitor_data', title='set_port',
                                                   data=self.send_message['port'],
                                                   time=time_util.get_format_from_time(time.time())))
            modbus: ModbusRTUMasterNew = global_setting.get_setting("modbus", None)
            if modbus is None:
                modbus = ModbusRTUMasterNew(self.send_message['port'], baudrate=115200, timeout=float(
                    global_setting.get_setting('monitor_data')['Serial']['timeout']), )
                global_setting.set_setting("modbus", modbus)
            else:
                modbus.close()
                modbus = ModbusRTUMasterNew(self.send_message['port'], baudrate=115200, timeout=float(
                    global_setting.get_setting('monitor_data')['Serial']['timeout']), )
                global_setting.set_setting("modbus", modbus)
            self.send_response_text(
                f"{time_util.get_format_from_time(time.time())}- 设备: {self.ports[index]['device']}" + f" #{self.ports[index]['description']}" + "  已被选中!")
        except Exception as e:
            logger.error(f"{e}")
        pass
    def send_response_text(self, text):
        # 往状态栏发消息
        self.response_text: QListWidget = self.findChild(QListWidget, "tab_7_responselist")
        if self.response_text == None:
            logger.error("response_text状态栏未找到！")
            return
        self.response_text.addItem(text)
        if self.main_gui is not None:
            self.main_gui.status_bar.update_tip(text)
        # 滑动滚动条到最底下
        scroll_bar = self.response_text.verticalScrollBar()
        if scroll_bar != None:
            scroll_bar.setValue(scroll_bar.maximum())
        pass
    def remove_layout_items(self, layout):
        # 遍历并移除布局中的所有项目
        while layout.count():
            item = layout.takeAt(0)  # 取出第一个项目
            if item is not None:
                widget = item.widget()  # 获取该项目的 widget，如果有
                if widget is not None:
                    widget.deleteLater()  # 删除 widget 的创建/显示
                else:
                    # 如果是其他布局，则递归调用
                    sub_layout = item.layout()
                    if sub_layout is not None:
                        self.remove_layout_items(sub_layout)  # 递归清空子布局
    def init_config_ui(self):
        #添加config ——ui
        self.experiment_setting: Experiment_setting_entity = global_setting.get_setting("experiment_setting", None)
        if self.experiment_setting is None:
            return
            # 找到layout
        self.config_layout :QVBoxLayout= self.findChild(QVBoxLayout,"content_layout")
        if self.config_layout is not None:
            # 清除所有子组件
            self.remove_layout_items(self.config_layout)
            # 添加scroll_area
            # 添加滚动区域
            self.scroll_area = QScrollArea()
            self.scroll_area.setObjectName("content_layout_scroll_area")
            self.scroll_area.setWidgetResizable(True)  # 使滚动区域填充空间
            # 创建一个容器部件，用于在滚动区域中放置内容
            self.scroll_area_content = QWidget()
            self.scroll_area_layout = QVBoxLayout(self.scroll_area_content)

            # 添加一些基本设置
            self.init_basic_config(self.scroll_area_layout)

            # 添加每个模块
            for module_key, module_value in self.config.items():
                if module_key  ==Modbus_Type.Modbus_Slave_Ids.UFC.value['name']:
                    pass
                elif module_key  ==Modbus_Type.Modbus_Slave_Ids.UGC.value['name']:
                    pass
                elif module_key  ==Modbus_Type.Modbus_Slave_Ids.ZOS.value['name']:
                    pass
                elif module_key  ==Modbus_Type.Modbus_Slave_Ids.ENM.value['name']:
                    self.init_enm_config_ui( module_key, module_value,self.scroll_area_layout)
                    pass
                elif module_key  ==Modbus_Type.Modbus_Slave_Ids.DWM.value['name']:

                    pass
                elif module_key  ==Modbus_Type.Modbus_Slave_Ids.EM.value['name']:
                    self.init_em_config_ui(module_key, module_value, self.scroll_area_layout)
                    pass
                elif module_key  ==Modbus_Type.Modbus_Slave_Ids.WM.value['name']:

                    pass
                pass
            pass
            # 将容器部件设置为滚动区域的主部件
            self.scroll_area.setWidget(self.scroll_area_content)
            self.config_layout.addWidget(self.scroll_area)
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
        pass

    # 实例化按钮信号槽绑定
    def init_btn_func(self):
        # 重新获取端口按钮
        refresh_port_btn: QPushButton = self.findChild(QPushButton, "tab_7_refresh_port_btn")

        refresh_port_btn.clicked.connect(self.refresh_port)
        # 确定设备配置按钮
        self.start_btn: QPushButton = self.findChild(QPushButton, "start_btn")
        self.start_btn.clicked.connect(self.start_device_config)

        pass

    def start_device_config(self):
        #确定设备配置按钮
        reply = QMessageBox.question(self, '确定设备配置',
                                     "确定该设备配置？",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            global_setting.set_setting("app_state", AppState.CONFIGURING)
            if self.main_gui is not None:
                self.main_gui.change_enable_component_app_state_signal.emit()
                pass
            send_message_queue = global_setting.get_setting("send_message_queue")
            send_message_queue.put(ObjectQueueItem(origin='tab_7', to='main_monitor_data', title='set_port',
                                                   data=self.send_message['port'],
                                                   time=time_util.get_format_from_time(time.time())))
            # 设置vr值
            basic_experiment_config = {}
            vr_value= self.vr_desc_text.value()
            if  vr_value:
                basic_experiment_config['vr_desc'] = float(vr_value)
                global_setting.set_setting("Vr",float(vr_value))
            # 设置span校准的标准气体的氧气浓度值 %
            span_oxygen_value = self.span_oxygen_desc_text.value()
            if span_oxygen_value:
                basic_experiment_config["span_standard_oxygen_value"]=float(span_oxygen_value)
                global_setting.set_setting("span_standard_oxygen_value", float(span_oxygen_value))
            # 设置span校准的标准气体的二氧化碳浓度值 %
            span_carbon_value = self.span_carbon_desc_text.value()
            if span_carbon_value:
                basic_experiment_config["span_standard_carbon_value"] = float(span_carbon_value)
                global_setting.set_setting("span_standard_carbon_value", float(span_carbon_value))

            # 给其他进程同步设置
            send_message_queue.put(ObjectQueueItem(origin='tab_7', to='main_monitor_data', title='set_experiment_basic_config',
                                                   data=basic_experiment_config,
                                                   time=time_util.get_format_from_time(time.time())))
            self.close()
            msg_box = InfoDialog(title="确定设备配置", info="确定该设备配置成功!",
                                 icon=QMessageBox.Icon.Information)
            msg_box.exec()

            reply_start_experiment = QMessageBox.question(self, '开始实验',
                                         "是否直接开始实验？",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                         QMessageBox.StandardButton.No)
            if reply_start_experiment == QMessageBox.StandardButton.Yes:
                self.main_gui.start_experiment()




    def stop_experiment(self):
        if self.main_gui is not None:
            self.main_gui.stop_experiment()
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
    def update_slider(self, address,mouse_cage_number,function_code,data_lists,slider:QSlider):
        value = slider.value()

        data_list = ['00', '00', '00', '00']
        if str(value) in data_lists:
            data_list=[hex_str[2:] for hex_str in data_lists[str(value)]['value']]
        self.send_message['data'] = data_list
        if mouse_cage_number ==0:
            self.send_message['slave_id'] = format(address, '02X')
        else:
            self.send_message['slave_id'] = format(address+mouse_cage_number*16, '02X')
        self.send_message['function_code'] = format(function_code, '02X')

        self.send_data()

    def update_slider_label(self,value,label):
        label.setText(f"当前值: {value}")  # 更新当前值标签的文本
        # 更新label
        pass
    def on_radio_button_clicked(self, button,address,mouse_cage_number,function_code,data_lists):
        btn_object_name:str = button.objectName()
        """处理按钮点击事件"""
        data_list = ['00','00','00','00']
        if "on" in btn_object_name.lower():
            data_list=[hex_str[2:] for hex_str in data_lists["0"]['value']]
        elif "off" in btn_object_name.lower():
            data_list = [hex_str[2:] for hex_str in data_lists["1"]['value']]

        # 二号笼子的食槽门开关电平与其他笼子相反，只交换食槽门控制字节。
        if (
            mouse_cage_number == 2
            and function_code == 5
            and len(data_list) >= 3
            and [byte.upper() for byte in data_list[:2]] == ['00', '70']
        ):
            if data_list[2].upper() == '00':
                data_list[2] = 'FF'
            elif data_list[2].upper() == 'FF':
                data_list[2] = '00'
        self.send_message['data'] = data_list
        if mouse_cage_number ==0:
            self.send_message['slave_id'] = format(address, '02X')
        else:
            self.send_message['slave_id'] = format(address+mouse_cage_number*16, '02X')
        self.send_message['function_code'] = format(function_code, '02X')

        self.send_data()


    def send_data(self):
        state = global_setting.get_setting("app_state",AppState.INITIALIZED)
        # 根据是否已经实验来发送到自己还是main_monitor_data
        if state is None or state !=AppState.MONITORING:
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
                                             origin='tab_7')

            global_setting.get_setting("send_message_queue").put(message_struct)
            logger.debug(f"tab_7开始发送消息:{message_struct}")

    def init_em_config_ui(self, module_key, module_value, scroll_area_layout):

        # 创建 GroupBox
        group_box = QGroupBox(f"{module_value['desc']}-{module_value['config'][0]['value'][0]['desc']}")
        group_box.setContentsMargins(10, 10, 10, 10)
        scroll_area_layout.addWidget(group_box)

        # 创建第一个 GridLayout
        grid_layout1 = QGridLayout()
        grid_layout1.setContentsMargins(10, 30, 10, 10)
        group_box.setLayout(grid_layout1)

        # 添加鼠笼和 radio buttons
        group_nums = len(self.experiment_setting.groups) if self.experiment_setting.groups is not None else 0
        columns = 2
        for i in range(math.ceil(group_nums/columns)):  # 行
            for j in range(columns):  # 列
                index = i * columns + j + 1  # 计算鼠笼编号
                if index<=group_nums:
                    label = QLabel(f"鼠笼 {index}")

                    radio_on = QRadioButton(f"{module_value['config'][0]['value'][0]['refer_value']['0']['desc']}")
                    radio_on.setObjectName("on")
                    radio_off = QRadioButton(f"{module_value['config'][0]['value'][0]['refer_value']['1']['desc']}")
                    radio_off.setObjectName("off")
                    radio_off.setChecked(True)
                    # 创建一个 ButtonGroup
                    button_group = QButtonGroup(grid_layout1)  # 绑定到主窗口以便于管理
                    # 添加到 ButtonGroup 中
                    button_group.addButton(radio_on)
                    button_group.addButton(radio_off)
                    # 连接信号
                    button_group.buttonClicked.connect(
                        lambda button, address=module_value['address'], mouse_cage_number=index,
                               function_code=module_value['config'][0]['function_code'],
                               data_lists=module_value['config'][0]['value'][0]['refer_value']:
                        self.on_radio_button_clicked(button, address, mouse_cage_number, function_code, data_lists))
                    # 这里的3代表组件数量  label  radio_on radio_off 3个
                    grid_layout1.addWidget(label, i, j * 3)  # 标签在 (i, j * 3)
                    grid_layout1.addWidget(radio_on, i, j * 3 + 1)  # ON 按钮在 (i, j * 3 + 1)
                    grid_layout1.addWidget(radio_off, i, j * 3 + 2)  # OFF 按钮在 (i, j * 3 + 2)
        pass
    def init_enm_config_ui(self, module_key, module_value,scroll_area_layout):
        # 创建 GroupBox
        group_box = QGroupBox(f"{module_value['desc']}-{module_value['config'][0]['value'][0]['desc']}")
        group_box.setContentsMargins(10,10,10,10)
        scroll_area_layout.addWidget(group_box)

        # 创建第一个 GridLayout
        grid_layout1 = QGridLayout()
        grid_layout1.setContentsMargins(10,30,10,10)
        self.group_box.setLayout(grid_layout1)

        group_nums = len(self.experiment_setting.groups) if self.experiment_setting.groups is not None else 0
        columns = 2
        # 添加鼠笼和 radio buttons
        for i in range(math.ceil(group_nums/columns)):  # 行
            for j in range(columns):  # 列
                index = i * 2 + j + 1  # 计算鼠笼编号
                if index <= group_nums:
                    label = QLabel(f"鼠笼 {index}")

                    radio_on = QRadioButton(f"{module_value['config'][0]['value'][0]['refer_value']['0']['desc']}")
                    radio_on.setObjectName("on")
                    radio_off = QRadioButton(f"{module_value['config'][0]['value'][0]['refer_value']['1']['desc']}")
                    radio_off.setObjectName("off")
                    radio_off.setChecked(True)
                    # 创建一个 ButtonGroup
                    button_group = QButtonGroup(grid_layout1)  # 绑定到主窗口以便于管理
                    # 添加到 ButtonGroup 中
                    button_group.addButton(radio_on)
                    button_group.addButton(radio_off)
                    # 连接信号
                    button_group.buttonClicked.connect(lambda button,address=module_value['address'],mouse_cage_number=index,function_code=module_value['config'][0]['function_code'],data_lists=module_value['config'][0]['value'][0]['refer_value']:
                                                       self.on_radio_button_clicked(button,address,mouse_cage_number,function_code,data_lists))
                    # 这里的3代表组件数量  label  radio_on radio_off 3个
                    grid_layout1.addWidget(label, i, j * 3)  # 标签在 (i, j * 3)
                    grid_layout1.addWidget(radio_on, i, j * 3 + 1)  # ON 按钮在 (i, j * 3 + 1)
                    grid_layout1.addWidget(radio_off, i, j * 3 + 2)  # OFF 按钮在 (i, j * 3 + 2)

        # 创建第2个 GridLayout
        group_box2 = QGroupBox(f"{module_value['desc']}-{module_value['config'][0]['value'][1]['desc']}")
        group_box2.setContentsMargins(10, 10, 10, 10)
        scroll_area_layout.addWidget(group_box2)
        grid_layout2 = QGridLayout()
        grid_layout2.setContentsMargins(10,30, 10, 10)
        group_box2.setLayout(grid_layout2)

        # 添加鼠笼和 radio buttons
        for i in range(math.ceil(group_nums/columns)):  # 行
            for j in range(columns):  # 列
                index = i * 2 + j + 1  # 计算鼠笼编号
                if index <= group_nums:
                    label = QLabel(f"鼠笼 {index}")

                    radio_on = QRadioButton(f"{module_value['config'][0]['value'][1]['refer_value']['0']['desc']}")
                    radio_on.setObjectName("on")
                    radio_off = QRadioButton(f"{module_value['config'][0]['value'][1]['refer_value']['1']['desc']}")
                    radio_off.setObjectName("off")
                    radio_off.setChecked(True)
                    # 创建一个 ButtonGroup
                    button_group = QButtonGroup(grid_layout2)  # 绑定到主窗口以便于管理
                    # 添加到 ButtonGroup 中
                    button_group.addButton(radio_on)
                    button_group.addButton(radio_off)
                    button_group.buttonClicked.connect(
                        lambda button, address=module_value['address'], mouse_cage_number=index,
                               function_code=module_value['config'][0]['function_code'],
                               data_lists=module_value['config'][0]['value'][1]['refer_value']:
                        self.on_radio_button_clicked(button, address, mouse_cage_number, function_code, data_lists))
                    grid_layout2.addWidget(label, i, j * 3)  # 标签在 (i, j * 3)
                    grid_layout2.addWidget(radio_on, i, j * 3 + 1)  # ON 按钮在 (i, j * 3 + 1)
                    grid_layout2.addWidget(radio_off, i, j * 3 + 2)  # OFF 按钮在 (i, j * 3 + 2)

        # 创建第3个 GridLayout
        group_box3 = QGroupBox(f"{module_value['desc']}-{module_value['config'][1]['value'][0]['desc']}")
        group_box3.setContentsMargins(10, 10, 10, 10)
        scroll_area_layout.addWidget(group_box3)
        # 创建第二个 GridLayout
        grid_layout3 = QGridLayout()
        grid_layout3.setContentsMargins(10, 30, 10, 10)
        group_box3.setLayout(grid_layout3)

        # 添加鼠笼和 sliders
        for i in range(math.ceil(group_nums/columns)):  # 行
            for j in range(columns):  # 列
                index = i * 2 + j + 1  # 计算鼠笼编号
                if index <= group_nums:
                    label = QLabel(f"鼠笼 {index}")
                    slider = QSlider()  # 创建滑块
                    slider.setOrientation(Qt.Orientation.Horizontal)  # 设置为横向
                    slider.setMinimum(1)  # 最小值
                    slider.setMaximum(9)  # 最大值
                    slider.setValue(1)  # 默认值

                    # 创建一个 label 来显示当前值
                    current_value_label = QLabel("当前值: 1")

                    # 连接滑块的值变化信号到更新标签的槽
                    slider.valueChanged.connect(lambda value, label=current_value_label:self.update_slider_label(value, label))
                    slider.sliderReleased.connect(lambda address=module_value['address'],mouse_cage_number=index,function_code=module_value['config'][1]['function_code'],data_lists=module_value['config'][1]['value'][0]['refer_value'], slider=slider
                                                : self.update_slider(address,mouse_cage_number,function_code,data_lists,slider))

                    # 添加到布局中
                    grid_layout3.addWidget(label, i, j * 3)  # 标签在 (i, j * 3)
                    grid_layout3.addWidget(slider, i, j * 3 + 1)  # 滑块在 (i, j * 3 + 1)
                    grid_layout3.addWidget(current_value_label, i, j * 3 + 2)  # 当前值标签在 (i, j * 3 + 2)
        # 创建第4个 GridLayout
        group_box4 = QGroupBox(f"{module_value['desc']}-{module_value['config'][1]['value'][1]['desc']}")
        group_box4.setContentsMargins(10, 10, 10, 10)
        scroll_area_layout.addWidget(group_box4)
        grid_layout4 = QGridLayout()
        grid_layout4.setContentsMargins(10, 30, 10, 10)
        group_box4.setLayout(grid_layout4)

        # 添加鼠笼和 sliders
        for i in range(math.ceil(group_nums/columns)):  # 行
            for j in range(columns):  # 列
                index = i * 2 + j + 1  # 计算鼠笼编号
                if index <= group_nums:
                    label = QLabel(f"鼠笼 {index}")
                    slider = QSlider()  # 创建滑块
                    slider.setOrientation(Qt.Orientation.Horizontal)  # 设置为横向
                    slider.setMinimum(1)  # 最小值
                    slider.setMaximum(9)  # 最大值
                    slider.setValue(1)  # 默认值

                    # 创建一个 label 来显示当前值
                    current_value_label = QLabel("当前值: 1")

                    # 连接滑块的值变化信号到更新标签的槽
                    slider.valueChanged.connect(
                        lambda value, label=current_value_label: self.update_slider_label(value, label))
                    slider.sliderReleased.connect(lambda address=module_value['address'], mouse_cage_number=index,
                                                         function_code=module_value['config'][1]['function_code'],
                                                         data_lists=module_value['config'][1]['value'][1]['refer_value'],
                                                         slider=slider
                                                  : self.update_slider(address, mouse_cage_number, function_code,
                                                                       data_lists, slider))

                    # 添加到布局中
                    grid_layout4.addWidget(label, i, j * 3)  # 标签在 (i, j * 3)
                    grid_layout4.addWidget(slider, i, j * 3 + 1)  # 滑块在 (i, j * 3 + 1)
                    grid_layout4.addWidget(current_value_label, i, j * 3 + 2)  # 当前值标签在 (i, j * 3 + 2)
        pass

    def init_basic_config(self, scroll_area_layout):
        """
        设置基础设置
        :param scroll_area_layout:
        :return:
        """
        # 创建 GroupBox
        self.group_box = QGroupBox(f"基本配置")
        self.group_box.setStyleSheet("""
        QGroupBox {
            font-size:17px;
            font-weight:bolder;
        }
        """)
        self.group_box.setContentsMargins(10, 10, 10, 10)
        scroll_area_layout.addWidget(self.group_box)
        main_layout =QVBoxLayout()
        hT_layout = QHBoxLayout()

        self.calibration_checkbox = QCheckBox("启动时校准气路值")
        self.calibration_checkbox.setStyleSheet("""
                QCheckBox {
                    margin-top:17px;
                    font-weight:bold;
                }
                """)
        self.calibration_checkbox.stateChanged.connect(self.calibration_gas_state_change)
        self.calibration_checkbox.setChecked( global_setting.get_setting("is_auto_calibration", True))
        hT_layout.addWidget(self.calibration_checkbox)
        main_layout.addLayout(hT_layout)


        h0_layout = QHBoxLayout()
        span_oxygen_desc_label = QLabel("span校准的标准气体的氧浓度数值（单位:%,例如20.9%，请输入20.9）：")
        span_oxygen_desc_label.setStyleSheet("""
                      QLabel {
                          font-weight:bold;
                      }
                      """)
        self.span_oxygen_desc_text = QDoubleSpinBox()
        self.span_oxygen_desc_text.setValue(global_setting.get_setting("span_standard_oxygen_value",20.9))

        h0_layout.addWidget(span_oxygen_desc_label)
        h0_layout.addWidget(self.span_oxygen_desc_text)
        main_layout.addLayout(h0_layout)

        h1_layout = QHBoxLayout()
        span_carbon_desc_label = QLabel("span校准的标准气体的CO2浓度数值（单位:%,例如0.03%，请输入0.03）：")
        span_carbon_desc_label.setStyleSheet("""
                              QLabel {
                                  font-weight:bold;
                              }
                              """)
        self.span_carbon_desc_text = QDoubleSpinBox()
        self.span_carbon_desc_text.setValue(global_setting.get_setting("span_standard_carbon_value",0.03))

        h1_layout.addWidget(span_carbon_desc_label)
        h1_layout.addWidget(self.span_carbon_desc_text)
        main_layout.addLayout(h1_layout)

        # 创建第2个 GridLayout
        h_layout = QHBoxLayout()
        vr_desc_label=QLabel("请输入Vr值[已弃用]（实际的标定气体，根据气瓶上的标识确定,单位:%,例如20.9%，请输入20.9）：")
        vr_desc_label.setStyleSheet("""
                              QLabel {
                                  font-weight:bold;
                              }
                              """)
        self.vr_desc_text=QDoubleSpinBox()
        self.vr_desc_text.setValue(global_setting.get_setting("Vr",20.9))

        h_layout.addWidget(vr_desc_label)
        h_layout.addWidget(self.vr_desc_text)
        main_layout.addLayout(h_layout)
        self.group_box.setLayout(main_layout)
        pass
    def calibration_gas_state_change(self,state):
        is_checked = bool(state)  # 直接转为布尔值
        # 是否自动校准气体给其他进程同步设置
        if self.main_gui is not None:
            for tool_bar_action in self.main_gui.tool_bar_actions:

                # 特殊按钮需要特殊配置
                if tool_bar_action['obj_name'] in ["calibration_gas"]:
                    tool_bar_action["action"].setChecked(is_checked)
                    break
        global_setting.set_setting("is_auto_calibration", is_checked)
        send_message_queue = global_setting.get_setting("send_message_queue")
        send_message_queue.put(
            ObjectQueueItem(origin='tab_7', to='main_monitor_data', title='set_experiment_basic_config',
                            data={"is_auto_calibration": is_checked},
                            time=time_util.get_format_from_time(time.time())))

