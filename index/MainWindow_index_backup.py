import importlib
import json
import os
import threading
import time
from json import JSONDecodeError
from PyQt6 import QtCore
from PyQt6.QtCore import QThread
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMessageBox, QVBoxLayout, QToolBar, QTabWidget
from loguru import logger
from Service import main_monitor_data, main_deep_camera, main_infrared_camera
from Service.UFC_UGC_ZOS_Service.index.UFC_UGC_ZOS_index import UFC_UGC_ZOS_index
from my_abc.BaseModule import BaseModule
from public.component.custom_status_bar import CustomStatusBar
from public.component.mask.LoadingMask import AnimatedLoadingMask
from public.config_class.global_setting import global_setting
from public.entity.enum.Public_Enum import BaseInterfaceType, AppState
from public.util.custom_data_file_util import custom_data_file_util
from public.util.time_util import time_util
from theme.ThemeQt6 import ThemedWindow
from ui.MainWindow import Ui_MainWindow
#logger = logger.bind(category="gui_logger")
class MainWindow_Index(ThemedWindow):
    # 根据程序状态来改变是否可以点击的组件
    change_enable_component_app_state_signal = QtCore.pyqtSignal()
    def close_window_handle(self):
        """
        关闭窗口执行的事件
        :return:
        """
        state = global_setting.get_setting("app_state", None)
        # 如果在开始实验期间关闭窗口
        if state is not None and state ==AppState.MONITORING:
            # 停止实验
            self.stop_experiment()
    def closeEvent(self, event):
        if len(self.open_windows)!=0:
            # 可选择使用 QMessageBox 来确认是否关闭
            reply = QMessageBox.question(self, '关闭窗口',
                                         "当前还有其他子窗口未关闭，你确定要退出程序吗？",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                         QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                for window in self.open_windows:
                    self.close_window_handle()
                    window.close()
                event.accept()  # 关闭窗口
            else:
                event.ignore()  # 忽略关闭事件
        else:
            # 可选择使用 QMessageBox 来确认是否关闭
            reply = QMessageBox.question(self, '关闭窗口',
                                         "你确定要退出程序吗？",
                                         QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                         QMessageBox.StandardButton.No)
            if reply == QMessageBox.StandardButton.Yes:
                self.close_window_handle()
                event.accept()  # 关闭窗口
            else:
                event.ignore()  # 忽略关闭事件
        pass
    def __init__(self):
        super().__init__()
        #暂停实验标志位
        self.is_paused = False
        # 点击开始实验 接受数据和存储数据的线程
        self.store_thread_sub=None
        self.send_thread_sub=None
        self.read_queue_data_thread_sub=None
        self.add_message_thread_sub=None
        self.ufc_ugc_zos:UFC_UGC_ZOS_index=None
        self.ufc_ugc_zos_thread=None
        # 深度相机线程
        self.deep_camera_thread_sub_list=[]
        self.deep_camera_read_queue_data_thread_sub=None
        self.deep_camera_delete_file_thread_sub=None
        # 红外相机线程
        self.infrared_camera_thread_sub_list = []
        self.infrared_camera_read_queue_data_thread_sub = None
        self.infrared_camera_delete_file_thread_sub = None
        # tool——bar-action 工具栏的action [{'obj_name':'','name';",'action':QAction}]
        self.tool_bar_actions = []
        self.menu_bar_actions = []
        # 模块
        self.modules =[]
        # 正在显示的Widget
        self.active_module_widgets:[BaseModule]=[]
        # 打开的窗口
        self.open_windows:[BaseModule]=[]
        # 工具栏
        self.toolbar = None
        #状态栏
        self.status_bar = None
        # 内容layout
        self.content_layout :QVBoxLayout =None
        # tab_widget
        self.tab_widget :QTabWidget =None
        # 实例化ui
        self._init_ui()
        # 实例化自定义ui
        self._init_customize_ui()
        # 实例化功能
        self._init_function()
        # 加载qss样式表
        self._init_custom_style_sheet()
        self._retranslateUi()
        pass
    # 实例化ui
    def _init_ui(self, title=""):
        # 将ui文件转成py文件后 直接实例化该py文件里的类对象  uic工具转换之后就是这一段代码
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        # 设置窗口大小为屏幕大小
        self.setGeometry(global_setting.get_setting("screen"))
        self.setObjectName("mainWindow_Index")
        pass
    def _init_customize_ui(self):
        self.content_layout = self.findChild(QVBoxLayout,"content_layout")
        self.tab_widget:QTabWidget = self.findChild(QTabWidget,"tab_widget")
        # 启用标签关闭按钮
        self.tab_widget.setTabsClosable(True)
        # 允许标签拖动重新排序
        self.tab_widget.setMovable(True)
        # 连接标签关闭信号
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        # 加载模块
        self.modules = self.load_modules()
        # 实例化菜单
        # [{id:0,text:"文件"},....]
        menu_name=global_setting.get_setting("configer")['menu']['menu_name']
        self.menu_name = None
        if menu_name is not None and menu_name != "":
            try:
                self.menu_name =  json.loads(menu_name)
            except JSONDecodeError  as e:
                logger.error(f"读取菜单json字符串解析错误：{e}")
                self.menu_name = None
            except Exception as e:
                logger.error(f"{e}")
                self.menu_name = None
            if self.menu_name is not None:
                # 创建菜单栏
                self.create_menu_bar()
            pass
        # 创建工具栏
        self.create_tool_bar()
        # 初始化自定义状态栏
        self.status_bar = CustomStatusBar()
        self.setStatusBar(self.status_bar)
        pass
    def _init_function(self):
        # 改变组件是否被点击
        self.change_enable_component_app_state()
        # 连接信号
        self.change_enable_component_app_state_signal.connect(self.change_enable_component_app_state)
        pass
    # 创建工具栏
    def create_tool_bar(self):
        # 创建 QToolBar
        self.toolbar = QToolBar("Toolbar")
        self.addToolBar(self.toolbar)
        # 创建动作（Action）
        name ="窗口变换"
        obj_name ="window_exchange"
        action_one = QAction(name, self)
        action_one.setObjectName(obj_name)
        action_one.setToolTip(name)
        action_one.triggered.connect(self.exchange_widget_and_window)
        self.tool_bar_actions.append({"name":name,"obj_name":obj_name,"action":action_one,"app_state":AppState.INITIALIZED})
        name = "更改主题颜色"
        obj_name = "toggle_mode"
        action_two= QAction(name, self)
        action_two.setObjectName(obj_name)
        action_two.setToolTip(name)
        action_two.triggered.connect(self.toggle_theme)
        self.tool_bar_actions.append({"name":name,"obj_name":obj_name,"action":action_two,"app_state":AppState.INITIALIZED})
        name = "开始实验"
        obj_name = "start_experiment"
        action_three = QAction(name, self)
        action_three.setObjectName(obj_name)
        action_three.setToolTip(name)
        action_three.triggered.connect(self.start_experiment)
        self.tool_bar_actions.append({"name": name,"obj_name":obj_name, "action": action_three,"app_state":AppState.CONFIGURING})
        name = "暂停实验"
        obj_name = "pause_experiment"
        action_four = QAction(name, self)
        action_four.setObjectName(obj_name)
        action_four.setToolTip(name)
        action_four.setDisabled(True)
        action_four.triggered.connect(self.pause_experiment)
        self.tool_bar_actions.append({"name": name,"obj_name":obj_name, "action": action_four,"app_state":AppState.CONFIGURING})

        name = "停止实验"
        obj_name = "stop_experiment"
        action_five = QAction(name, self)
        action_five.setObjectName(obj_name)
        action_five.setToolTip(name)
        action_five.triggered.connect(self.stop_experiment)
        action_five.setDisabled(True)
        self.tool_bar_actions.append({"name": name,"obj_name":obj_name, "action": action_five,"app_state":AppState.CONFIGURING})
        # 将动作添加到工具栏
        self.toolbar.addAction(action_one)
        self.toolbar.addSeparator()
        self.toolbar.addAction(action_two)
        self.toolbar.addSeparator()
        self.toolbar.addAction(action_three)
        self.toolbar.addAction(action_four)
        self.toolbar.addAction(action_five)
        self.toolbar.addSeparator()
    def create_menu_bar(self):
    # 创建菜单
        for menu_dict in self.menu_name:
            # 创建文件菜单
            menu = self.menuBar().addMenu(menu_dict['text'])
            # 从module加载组件...
            for module in self.modules:
                module:BaseModule
                module_menu_name = module.menu_name
                module_title = module.title
                if module_menu_name is not None and module_menu_name != "" and "id" in module_menu_name and "id" in menu_dict and menu_dict["id"] == module_menu_name["id"]:
                    # 创建menu action
                    module.set_main_gui(main_gui=self)
                    action = QAction(module_title, self)
                    action.setObjectName(f"{module.name}_menu_action")
                    # 创建点击事件
                    # action.triggered.connect(module.start_service)
                    action.triggered.connect(module.click_method)
                    # action.triggered.connect( module.adjustGUIPolicy)
                    # action.triggered.connect( module.interface_widget.show)
                    self.menu_bar_actions.append(
                        {"name": module_title, "obj_name": f"{module.name}_menu_action", "action": action, "app_state": module.app_state})
                    # 将操作添加到文件菜单
                    menu.addAction(action)
                    menu.addSeparator()  # 添加分隔线
        pass
    def _retranslateUi(self, **kwargs):
        _translate = QtCore.QCoreApplication.translate
        self.setWindowTitle(_translate(self.objectName(),global_setting.get_setting("configer")["window"]["title"]))
    pass
    def load_modules(self):
        #动态加载模块
        modules = []
        module_dir = 'Module'  # 插件目录
        # 递归遍历指定目录
        for dirpath, dirnames, filenames in os.walk(module_dir):
            for filename in filenames:
                if filename.endswith('.py'):
                    module_name = filename[:-3]# 去掉 .py 后缀
                    if module_name.startswith("main"):
                        file_path = os.path.join(dirpath, filename)
                        # 动态加载模块
                        spec = importlib.util.spec_from_file_location(module_name, file_path)
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)#装载module
                        # 查找到实现 BasePlugin 的类
                        for name, obj in module.__dict__.items():
                            if name =="BaseModule":
                                # 抽象类跳过
                                continue
                            if isinstance(obj, type) and issubclass(obj, BaseModule):
                                modules.append(obj())
        return modules
        pass
    def exchange_widget_and_window(self):
        """widget和window相互转换"""
        # 将module的显示方式改变
        for module in self.modules:
            if module.interface_widget.type == BaseInterfaceType.WIDGET or module.interface_widget.type == BaseInterfaceType.FRAME:
                module.interface_widget.type=BaseInterfaceType.WINDOW

            else:
                module.interface_widget.type=BaseInterfaceType.WIDGET
        new_open_windows = []
        new_active_module_widgets=[]
        # 将正在显示的方式进行改变
        if self.open_windows is not None and len(self.open_windows)!=0:
            #窗口-》frame
            # 将正在显示的方式进行改变
            index = 0
            last_module=None
            while index<len(self.open_windows) or len(self.open_windows)==1:
                if index>=len(self.open_windows):
                    index=0
                module = self.open_windows[index]
                if last_module is module:
                    break
                last_module = module
                module.close()
                if module not in self.active_module_widgets:
                    new_active_module_widgets.append(module)
                index+=1
        if self.active_module_widgets is not None and len(self.active_module_widgets)!=0:
            # 从初始布局中移除 label
            # frame-》窗口
            index = 0
            last_module = None
            while index<len(self.active_module_widgets) or len(self.active_module_widgets)==1:
                if index>=len(self.active_module_widgets):
                    index=0
                module = self.active_module_widgets[index]
                if last_module is module:
                    break
                last_module = module
                module.setParent(None)
                module.hide()
                if module not in self.open_windows:
                    new_open_windows.append(module)
                index+=1
        # 删除所有标签页和widgets
        while self.tab_widget.count() > 0:  # 直到没有标签页
            self.tab_widget.removeTab(0)  # 删除第一个标签页
        self.open_windows.extend(new_open_windows)
        self.active_module_widgets.extend(new_active_module_widgets)
        for module in self.open_windows:
            module.adjustGUIPolicy()
            module.interface_widget.setMinimumSize(0,0)
            module.interface_widget.show()
        for module in self.active_module_widgets:
            module.adjustGUIPolicy()
            module.interface_widget.setMinimumSize()
            module.interface_widget.show()
    # 切换白天黑夜主题功能
    def toggle_theme(self):
        # 根据当前主题变换主题
        new_theme = "dark" if global_setting.get_setting("theme_manager").current_theme == "light" else "light"
        # 将新主题关键字赋值回去
        global_setting.set_setting('style', new_theme)
        global_setting.get_setting("theme_manager").current_theme = new_theme
        # 更改样式
        self.setStyleSheet(global_setting.get_setting("theme_manager").get_style_sheet())
        pass
    def start_experiment(self):
        # 开启遮罩

        self.setEnabled(False)
        self.status_bar.update_tip(f"正在开启实验监测...")
        port = global_setting.get_setting("port")
        if port is None or port == "":
            reply = QMessageBox.question(self, '注意',
                                         "未设置串口，请去实验配置配置串口!",
                                         QMessageBox.StandardButton.Cancel,
                                         QMessageBox.StandardButton.No)
            self.setEnabled(True)
            return
        # 开始实验
        global_setting.set_setting("app_state", AppState.MONITORING)
        global_setting.set_setting("start_experiment_time", time.time())
        global_setting.set_setting("pause_experiment_time", [])
        global_setting.set_setting("relieve_pause_experiment_time", [])

        try:
            self.store_thread_sub, self.send_thread_sub, self.read_queue_data_thread_sub, self.add_message_thread_sub,self.ufc_ugc_zos,self.ufc_ugc_zos_thread = main_monitor_data.main(
                port=port, q=global_setting.get_setting("queue"),
                send_message_q=global_setting.get_setting("send_message_queue"))
        except Exception as e:
            logger.error(f"开启数据监测线程错误，原因：{e}")
            self.status_bar.update_tip(f"开启数据监测线程错误，原因：{e}")
        try:
            self.deep_camera_thread_sub_list, self.deep_camera_read_queue_data_thread_sub, self.deep_camera_delete_file_thread_sub = main_deep_camera.main(
                q=global_setting.get_setting("queue"))
        except Exception as e:
            logger.error(f"开启深度相机监测线程错误，原因：{e}")
            self.status_bar.update_tip(f"开启深度相机数据监测线程错误，原因：{e}")
        try:
            self.infrared_camera_thread_sub_list,self.infrared_camera_read_queue_data_thread_sub,self.infrared_camera_delete_file_thread_sub = main_infrared_camera.main(q=global_setting.get_setting("queue"))
        except Exception as e:
            logger.error(f"开启红外相机监测线程错误，原因：{e}")
            self.status_bar.update_tip(f"开启红外相机数据监测线程错误，原因：{e}")
        # 更新main_gui组件显示
        self.change_enable_component_app_state_signal.emit()
        self.status_bar.update_status()
        self.status_bar.update_tip(f"开启实验监测成功！")
        for action_dict in self.tool_bar_actions:
            if action_dict["obj_name"] == "start_experiment":
                action_dict["action"]: QAction
                action_dict["action"].setDisabled(True)
            if action_dict["obj_name"] == "stop_experiment":
                action_dict["action"]: QAction
                action_dict["action"].setDisabled(False)
            if action_dict["obj_name"] == "pause_experiment":
                action_dict["action"]: QAction
                action_dict["action"].setDisabled(False)
        self.setEnabled(True)

        pass
    def pause_experiment(self):
        self.setEnabled(False)
        try:
            if self.store_thread_sub is not None and not self.store_thread_sub.isPaused():
                self.store_thread_sub.pause()
            else:
                self.store_thread_sub.resume()
        except Exception as e:
            logger.error(f"暂停实验监测store_thread_sub错误，原因：{e}")
            self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
        try:
            if self.add_message_thread_sub is not None and not self.add_message_thread_sub.isPaused():
                self.add_message_thread_sub.pause()
            else:
                self.add_message_thread_sub.resume()
        except Exception as e:
            logger.error(f"暂停实验监测add_message_thread_sub错误，原因：{e}")
            self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
        try:
            if self.send_thread_sub is not None and not self.send_thread_sub.isPaused():
                self.send_thread_sub.pause()
            else:
                self.send_thread_sub.resume()
        except Exception as e:
            logger.error(f"暂停实验监测send_thread_sub错误，原因：{e}")
            self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
        try:
            if self.read_queue_data_thread_sub is not None and not self.read_queue_data_thread_sub.isPaused():
                self.read_queue_data_thread_sub.pause()
            else:
                self.read_queue_data_thread_sub.resume()
        except Exception as e:
            logger.error(f"暂停实验监测read_queue_data_thread_sub错误，原因：{e}")
            self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
        # 所有红外相机线程停止
        for camera_struct_l in self.infrared_camera_thread_sub_list:
            if len(camera_struct_l) != 0 and 'camera' in camera_struct_l:
                try:
                    if camera_struct_l['camera'] is not None and not camera_struct_l['camera'].isPaused():
                        camera_struct_l['camera'].pause()
                    else:
                        camera_struct_l['camera'].resume()
                except Exception as e:
                    logger.error(f"暂停实验监测infrared_camera_thread_sub_list错误，原因：{e}")
                    self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
        try:
            if self.infrared_camera_delete_file_thread_sub is not None and not self.infrared_camera_delete_file_thread_sub.isPaused():
                self.infrared_camera_delete_file_thread_sub.pause()
            else:
                self.infrared_camera_delete_file_thread_sub.resume()
        except Exception as e:
            logger.error(f"暂停实验监测infrared_camera_delete_file_thread_sub错误，原因：{e}")
            self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
        try:
            if self.infrared_camera_read_queue_data_thread_sub is not None and not self.infrared_camera_read_queue_data_thread_sub.isPaused():
                self.infrared_camera_read_queue_data_thread_sub.pause()
            else:
                self.infrared_camera_read_queue_data_thread_sub.resume()
        except Exception as e:
            logger.error(f"暂停实验监测infrared_camera_read_queue_data_thread_sub错误，原因：{e}")
            self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
        # 所有深度相机线程停止
        for camera_struct_l in self.deep_camera_thread_sub_list:
            if len(camera_struct_l) != 0 and 'camera' in camera_struct_l:
                try:
                    if camera_struct_l['camera'] is not None and not camera_struct_l['camera'].isPaused():
                        camera_struct_l['camera'].pause()
                    else:
                        camera_struct_l['camera'].resume()
                except Exception as e:
                    logger.error(f"暂停实验监测deep_camera_thread_sub_list错误，原因：{e}")
                    self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
            if len(camera_struct_l) != 0 and 'img_process' in camera_struct_l:
                try:
                    if camera_struct_l['img_process'] is not None and not camera_struct_l['img_process'].isPaused():
                        camera_struct_l['img_process'].pause()
                    else:
                        camera_struct_l['img_process'].resume()
                except Exception as e:
                    logger.error(f"暂停实验监测deep_camera_thread_sub_list_img_process错误，原因：{e}")
                    self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
        try:
            if self.deep_camera_delete_file_thread_sub is not None and not self.deep_camera_delete_file_thread_sub.isPaused():
                self.deep_camera_delete_file_thread_sub.pause()
            else:
                self.deep_camera_delete_file_thread_sub.resume()
        except Exception as e:
            logger.error(f"暂停实验监测deep_camera_delete_file_thread_sub错误，原因：{e}")
            self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
        try:
            if self.deep_camera_read_queue_data_thread_sub is not None and not self.deep_camera_read_queue_data_thread_sub.isPaused():
                self.deep_camera_read_queue_data_thread_sub.pause()
            else:
                self.deep_camera_read_queue_data_thread_sub.resume()
        except Exception as e:
            logger.error(f"暂停实验监测deep_camera_read_queue_data_thread_sub错误，原因：{e}")
            self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
        pass


        self.is_paused = not self.is_paused
        if self.is_paused:
            pause_experiment_time=global_setting.get_setting("pause_experiment_time",[])
            pause_experiment_time.append(time.time())
            global_setting.set_setting("pause_experiment_time",pause_experiment_time )
            self.status_bar.update_tip(f"暂停实验监测成功！")
        else:
            relieve_pause_experiment_time = global_setting.get_setting("relieve_pause_experiment_time", [])
            relieve_pause_experiment_time.append(time.time())
            global_setting.set_setting("relieve_pause_experiment_time", relieve_pause_experiment_time)
            self.status_bar.update_tip(f"解除暂停实验监测成功！")
        self.status_bar.update_status(is_paused=self.is_paused)


        for action_dict in self.tool_bar_actions:
            if action_dict["obj_name"] == "pause_experiment":
                action_dict["action"]: QAction
                if self.is_paused:
                    action_dict["name"]="解除暂停实验"
                else:
                    action_dict["name"] = "暂停实验"
                action_dict["action"].setToolTip(action_dict["name"])
                action_dict["action"].setText(action_dict["name"])
        self.setEnabled(True)
        pass
    def stop_experiment(self):
        self.setEnabled(False)
        self.status_bar.update_tip(f"正在关闭实验监测...")
        try:
            if self.store_thread_sub is not None and self.store_thread_sub.isRunning():
                self.store_thread_sub.stop()
        except Exception as e:
            logger.error(f"关闭实验监测store_thread_sub错误，原因：{e}")
            self.status_bar.update_tip(f"关闭实验监测错误，原因：{e}")
        try:
            if self.add_message_thread_sub is not None and self.add_message_thread_sub.isRunning():
                self.add_message_thread_sub.stop()
        except Exception as e:
            logger.error(f"关闭实验监测add_message_thread_sub错误，原因：{e}")
            self.status_bar.update_tip(f"关闭实验监测错误，原因：{e}")
        try:
            if self.send_thread_sub is not None and self.send_thread_sub.isRunning():
                self.send_thread_sub.stop()
        except Exception as e:
            logger.error(f"关闭实验监测send_thread_sub错误，原因：{e}")
            self.status_bar.update_tip(f"关闭实验监测错误，原因：{e}")
        try:
            if self.read_queue_data_thread_sub is not None and self.read_queue_data_thread_sub.isRunning():
                self.read_queue_data_thread_sub.stop()
        except Exception as e:
            logger.error(f"关闭实验监测read_queue_data_thread_sub错误，原因：{e}")
            self.status_bar.update_tip(f"关闭实验监测错误，原因：{e}")
        try:
            if self.ufc_ugc_zos is not None and self.ufc_ugc_zos_thread is not None :
                self.ufc_ugc_zos.disabled_auto_btn_handle()
                self.ufc_ugc_zos_thread:threading.Thread.join()
        except Exception as e:
            logger.error(f"关闭实验监测ufc_ugc_zos错误，原因：{e}")
            self.status_bar.update_tip(f"关闭实验监测错误，原因：{e}")
        # 所有红外相机线程停止
        for camera_struct_l in self.infrared_camera_thread_sub_list:
            if len(camera_struct_l) != 0 and 'camera' in camera_struct_l:
                try:
                    if camera_struct_l['camera'] is not None and camera_struct_l['camera'].isRunning():
                        camera_struct_l['camera'].stop()
                except Exception as e:
                    logger.error(f"关闭实验监测infrared_camera_thread_sub_list错误，原因：{e}")
                    self.status_bar.update_tip(f"关闭实验监测错误，原因：{e}")
        try:
            if self.infrared_camera_delete_file_thread_sub is not None and self.infrared_camera_delete_file_thread_sub.isRunning():
                self.infrared_camera_delete_file_thread_sub.stop()
        except Exception as e:
            logger.error(f"关闭实验监测infrared_camera_delete_file_thread_sub错误，原因：{e}")
            self.status_bar.update_tip(f"关闭实验监测错误，原因：{e}")
        try:
            if self.infrared_camera_read_queue_data_thread_sub is not None and self.infrared_camera_read_queue_data_thread_sub.isRunning():
                self.infrared_camera_read_queue_data_thread_sub.stop()
        except Exception as e:
            logger.error(f"关闭实验监测infrared_camera_read_queue_data_thread_sub错误，原因：{e}")
            self.status_bar.update_tip(f"关闭实验监测错误，原因：{e}")
        # 所有深度相机线程停止
        for camera_struct_l in self.deep_camera_thread_sub_list:
            if len(camera_struct_l) != 0 and 'camera' in camera_struct_l:
                try:
                    if camera_struct_l['camera'] is not None and camera_struct_l['camera'].isRunning():
                        camera_struct_l['camera'].stop()
                except Exception as e:
                    logger.error(f"关闭实验监测deep_camera_thread_sub_list错误，原因：{e}")
                    self.status_bar.update_tip(f"关闭实验监测错误，原因：{e}")
            if len(camera_struct_l) != 0 and 'img_process' in camera_struct_l:
                try:
                    if camera_struct_l['img_process'] is not None and camera_struct_l['img_process'].isRunning():
                        camera_struct_l['img_process'].stop()
                except Exception as e:
                    logger.error(f"关闭实验监测deep_camera_thread_sub_list_img_process错误，原因：{e}")
                    self.status_bar.update_tip(f"关闭实验监测错误，原因：{e}")
        try:
            if self.deep_camera_delete_file_thread_sub is not None and self.deep_camera_delete_file_thread_sub.isRunning():
                self.deep_camera_delete_file_thread_sub.stop()
        except Exception as e:
            logger.error(f"关闭实验监测deep_camera_delete_file_thread_sub错误，原因：{e}")
            self.status_bar.update_tip(f"关闭实验监测错误，原因：{e}")
        try:
            if self.deep_camera_read_queue_data_thread_sub is not None and self.deep_camera_read_queue_data_thread_sub.isRunning():
                self.deep_camera_read_queue_data_thread_sub.stop()
        except Exception as e:
            logger.error(f"关闭实验监测deep_camera_read_queue_data_thread_sub错误，原因：{e}")
            self.status_bar.update_tip(f"关闭实验监测错误，原因：{e}")

        global_setting.set_setting("app_state", AppState.CONFIGURING)
        global_setting.set_setting("stop_experiment_time", time.time())

        # 停止实验 将文件夹的数据合并成一个数据文件
        # 读取实验设置文件路径
        experiment_setting_file = global_setting.get_setting("experiment_setting_file", None)
        if experiment_setting_file is not None and os.path.exists(experiment_setting_file):
            # 获取文件所在的文件夹路径
            folder_path = os.path.dirname(experiment_setting_file)
            # 获取文件名称
            file_name = os.path.basename(experiment_setting_file)
            # 不带扩展名的文件名称
            file_name_without_extension = os.path.splitext(file_name)[0]
            # 获取文件的扩展名
            file_name_extension = os.path.splitext(file_name)[1]
            # 定义文件夹路径
            folder_path_data = os.getcwd() + global_setting.get_setting('monitor_data')['STORAGE'][
                'fold_path'] + os.path.join(
                global_setting.get_setting('monitor_data')['STORAGE']['sub_fold_path'],
                f"{file_name_without_extension}_{time_util.get_format_file_from_time(global_setting.get_setting('start_experiment_time', time.time()))}")
            custom_data_file_util.save_folder_contents_as_custom_file(folder_path_data)

        # 更新main_gui组件显示
        self.change_enable_component_app_state_signal.emit()
        self.status_bar.update_status()
        self.status_bar.update_tip(f"关闭实验监测成功！")
        for action_dict in self.tool_bar_actions:
            if action_dict["obj_name"] == "start_experiment":
                action_dict["action"]: QAction
                action_dict["action"].setDisabled(False)
            if action_dict["obj_name"] == "stop_experiment":
                action_dict["action"]: QAction
                action_dict["action"].setDisabled(True)



        self.setEnabled(True)
        pass
    def close_tab(self, index):
        """关闭标签页"""
        self.tab_widget.widget(index).hide()
        self.tab_widget.removeTab(index)
    def change_enable_component_app_state(self):
        # 更新程序状态值
        self.status_bar.update_app_state()
        #根据程序状态来改变是否可以点击的组件'
        #设置是否可以点击 menu_bar
        for menu_bar_action in self.menu_bar_actions:
            if menu_bar_action["app_state"] > global_setting.get_setting("app_state",AppState.INITIALIZED):
                menu_bar_action["action"].setEnabled(False)
            else:
                menu_bar_action["action"].setEnabled(True)

        # 设置是否可以点击 tool_bar
        for tool_bar_action in self.tool_bar_actions:
            if tool_bar_action["app_state"] > global_setting.get_setting("app_state",AppState.INITIALIZED):
                tool_bar_action["action"].setEnabled(False)
            else:
                tool_bar_action["action"].setEnabled(True)
            # 特殊按钮需要特殊配置
            if tool_bar_action['obj_name'] in ["stop_experiment", "pause_experiment"]:
                tool_bar_action["action"].setEnabled(False)
        pass


        pass