import abc
import re
import time

from blinker.base import _PNamespaceSignal
from loguru import logger

from Service.UFC_UGC_ZOS_Service.function.Send_Message.Send_Message import Send_Message
from public.config_class.global_setting import global_setting
from public.util.number_util import number_util
from public.util.time_util import time_util

#logger = logger.bind(category="deep_camera_logger")
class Gas_State_Check:
    """
    气路状态检测
    """

    def __init__(self):
        # 更新主线程状态栏消息信号
        self.update_status_main_signal_gui_update: _PNamespaceSignal = None

        # 发送的数据结构
        self.send_message = {
            'port': '',
            'data': '',
            'slave_id': 0,
            'function_code': 0,
            'timeout': 0
        }
        # 发送报文线程
        self.send_thread: Send_Message = Send_Message(
            update_status_main_signal_gui_update=self.update_status_main_signal_gui_update,
            send_message=self.send_message)
    def update(self):
        self.send_thread.update_status_main_signal_gui_update=self.update_status_main_signal_gui_update
    @abc.abstractmethod
    def state_check(self,resolve,reject):
        """
        状态检测
        :return:
        """
        pass
class UFC_Gas_State_Check(Gas_State_Check):
    """
    UFC 状态检测
    """
    def __init__(self):
        super().__init__()
        pass
    def state_check(self,resolve,reject):
        """
        UFC 状态检测
        :return:
        """
        self.update_status_main_signal_gui_update.send(f"{time_util.get_format_from_time(time.time())} | UFC 状态检测 开始")
        # resolve()
        #1.端口输出状态是否正确，确认与程序逻辑是否一致，否则报错
        port = global_setting.get_setting("port", None)
        if port is None:
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} | 启动失败，未选择串口！")
            reject()
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("C"),
            'slave_id': '2',
            'function_code': '1',
            'timeout': 1
        }

        self.send_thread.send_message = self.send_message
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} |  UFC 状态检测 1.端口输出状态是否正确，确认与程序逻辑是否一致，否则报错")
        data,message = self.send_thread.Send_no_promise()

        setting_mouse_cages = global_setting.get_setting("mouse_cages", [0, 1, 2, 3, 4, 5, 6, 7])
        setting_mouse_cages_2byte_str = global_setting.get_setting("mouse_cages_2byte_str", "11111111")
        # print(f"{data},{message}")
        # print(f"原来的设置：{setting_mouse_cages},{setting_mouse_cages_2byte_str}")
        state_datas = [item for item in data['data'] if '鼠笼' in item['desc']]
        # 方法1：提取所有整数
        data_mouse_cages=[]
        for state_data in state_datas:
            integers = int(re.findall(r'\d+', state_data['desc'])[0])-1 if re.findall(r'\d+', state_data['desc']) else 0
            if state_data['value'] == 1:
                data_mouse_cages.append(integers)
        data_mouse_cages_2byte_str=""
        for i in range(8):
            if i in data_mouse_cages:
                data_mouse_cages_2byte_str = "1" + data_mouse_cages_2byte_str
            else:
                data_mouse_cages_2byte_str = "0" + data_mouse_cages_2byte_str
        pass
        # 报错
        if data_mouse_cages_2byte_str.strip() !=setting_mouse_cages_2byte_str.strip():
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} |  UFC 状态检测 1.1端口输出状态与程序逻辑不一致|端口响应状态：{data_mouse_cages},{data_mouse_cages_2byte_str}|软件设置的端口状态：{setting_mouse_cages},{setting_mouse_cages_2byte_str}")


        # 2.读取流量控制器状态，判断所运行的鼠笼的是否正常
        self.send_message = {
            'port': port,
            'data': number_util.set_int_to_4_bytes_list("8"),
            'slave_id': '2',
            'function_code': '2',
            'timeout': 1
        }

        self.send_thread.send_message = self.send_message
        self.update_status_main_signal_gui_update.send(
            f"{time_util.get_format_from_time(time.time())} |  UFC 状态检测 2.读取流量控制器状态，判断所运行的鼠笼的是否正常")
        flow_data, flow_message = self.send_thread.Send_no_promise()
        # print(f"flow:{flow_data},{flow_message}")
        flow_states = [item for item in flow_data['data'] if '流量传感器' in item['desc']]
        # 方法1：提取所有整数
        data_flow_numbers = []
        for flow_state in flow_states:
            integers = int(re.findall(r'\d+', flow_state['desc'])[0]) - 1 if re.findall(r'\d+',
                                                                                        flow_state['desc']) else 0
            if flow_state['value'] == 1:
                data_flow_numbers.append(integers)
        data_flows_2byte_str = ""
        for i in range(8):
            if i in data_flow_numbers:
                data_flows_2byte_str = "1" + data_flows_2byte_str
            else:
                data_flows_2byte_str = "0" + data_flows_2byte_str

        # 报错
        if data_flows_2byte_str.strip() != setting_mouse_cages_2byte_str.strip():
            self.update_status_main_signal_gui_update.send(
                f"{time_util.get_format_from_time(time.time())} |  UFC 状态检测 2.1 读取流量控制器状态，判断所运行的鼠笼的是否正常：{data_flow_numbers},{data_flows_2byte_str}|软件设置的端口状态：{setting_mouse_cages},{setting_mouse_cages_2byte_str}")

        resolve()

        pass

