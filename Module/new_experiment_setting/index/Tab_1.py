import copy
import json
import math
import time
import typing
import threading
from functools import partial
from pathlib import Path

from PyQt6.QtGui import QDoubleValidator, QColor
from loguru import logger

from Module.new_experiment_setting.config.new_experiment_default_config import get_default_config
from Module.new_experiment_setting.ui.tab1_frame import Ui_tab1_frame
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
from theme.ThemeQt6 import ThemedWindow
from PyQt6 import QtGui, QtCore, QtWidgets
from PyQt6.QtCore import QRect, Qt, pyqtSignal, QTimer, QEvent, pyqtSlot
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QScrollArea, QGroupBox, QLabel, QSlider, QRadioButton,
    QGridLayout, QButtonGroup, QComboBox, QPushButton, QMessageBox, QHBoxLayout,
    QLineEdit, QDoubleSpinBox, QListWidget, QListWidgetItem, QCheckBox, QApplication
)
from public.util.time_util import time_util


# ========== 线程相关 ==========
class read_queue_data_Thread(MyQThread):
    """读取队列数据的线程"""

    def __init__(self, name):
        super().__init__(name)
        self.queue = None
        self.Each_Mouse_Cage_detect_finished_signal: pyqtSignal = None
        self.Not_Each_Mouse_Cage_detect_finished_signal: pyqtSignal = None

    def dosomething(self):
        if not self.queue.empty():
            try:
                message: ObjectQueueItem = self.queue.get()
            except Exception as e:
                logger.error(f"{self.name}发生错误{e}")
                return

            if message is not None and isinstance(message,
                                                  ObjectQueueItem) and message.to == 'New_main_experiment_setting':
                # logger.info(f"{self.name}_get_message: {message.title}")
                match message.title:
                    case "Each_Mouse_Cage_detect_finished":
                        if self.Each_Mouse_Cage_detect_finished_signal is not None:
                            self.Each_Mouse_Cage_detect_finished_signal.emit(message.data)
                    case "Not_Each_Mouse_Cage_detect_finished":
                        if self.Not_Each_Mouse_Cage_detect_finished_signal is not None:
                            self.Not_Each_Mouse_Cage_detect_finished_signal.emit(message.data)
                    case _:
                        pass
            else:
                if message:
                    self.queue.put(message)


read_queue_data_thread = read_queue_data_Thread(name="new_experiment_setting_tab_1_read_queue_data_thread")


# ==================== 第一步：修复元类 ====================
class SafeSingletonMeta(type(QtWidgets.QWidget)):
    """
    安全的单例元类 - 解决 super().__init__() 冲突

    关键改进：
    1. 追踪初始化状态，不在初始化中途返回
    2. 完整的初始化流程
    3. 线程安全的状态检查
    """
    _instances = {}
    _lock = threading.RLock()
    _init_in_progress = {}  # 追踪正在初始化的类
    _init_completed = {}  # 追踪已完成初始化的类

    def __call__(cls, *args, **kwargs):
        """重写调用方法"""

        if cls not in cls._instances:
            with cls._lock:
                # 双重检查
                if cls not in cls._instances:
                    # 第一次创建：允许完整的 __init__ 执行
                    cls._init_in_progress[cls] = True
                    try:
                        # 直接调用父元类的 __call__，执行完整的 __init__
                        instance = super(SafeSingletonMeta, cls).__call__(*args, **kwargs)
                        cls._instances[cls] = instance
                        cls._init_completed[cls] = True
                        # logger.warning(f"创建单例: {cls.__name__} (ID: {id(instance)})")
                    finally:
                        del cls._init_in_progress[cls]

                    return instance
                else:
                    # 在初始化过程中再次调用，返回现有实例
                    # logger.debug(f"单例已存在，返回现有实例")
                    return cls._instances[cls]

        # 已经初始化过，直接返回
        # logger.debug(f"返回已缓存的单例: {cls.__name__}")
        return cls._instances[cls]


# ==================== 第二步：修复 Tab_1 __init__ ====================
class Tab_1(ThemedWindow, metaclass=SafeSingletonMeta):
    """
    超级健壮的 Tab_1 实现

    核心改进：
    1. 使用安全的元类
    2. 在 super().__init__() 之前做最少的检查
    3. 完整的异常处理和恢复机制
    """

    # ==================== 原有信号 ====================
    update_group_activation_signal = pyqtSignal(dict)
    Each_Mouse_Cage_detect_finished_signal = pyqtSignal(dict)
    Not_Each_Mouse_Cage_detect_finished_signal = pyqtSignal(dict)
    air_module_ui_update_signal = pyqtSignal(str, bool)

    # ==================== 新增：跨线程安全的信号 ====================
    signal_air_module_update = pyqtSignal(str, bool)
    signal_detection_status_update = pyqtSignal(str)
    signal_force_ui_refresh = pyqtSignal()

    # ==================== 类级别的初始化跟踪 ====================
    _instance_lock = threading.RLock()
    _initialization_state = {}  # {instance_id: 'pending'|'init_started'|'completed'|'failed'}
    _failed_instances = set()  # 记录失败的实例

    def __init__(self, parent=None, geometry: QRect = None, title=""):
        """
        超级健壮的初始化方法
        """
        instance_id = id(self)

        # ==================== 第一层：防止重复初始化 ====================
        with Tab_1._instance_lock:
            if instance_id in Tab_1._initialization_state:
                state = Tab_1._initialization_state[instance_id]

                if state == 'completed':
                    logger.warning(f"实例 {instance_id} 已完成初始化，跳过")
                    return
                elif state == 'init_started':
                    logger.warning(f"实例 {instance_id} 正在初始化中，跳过")
                    return
                elif state == 'failed':
                    logger.error(f"实例 {instance_id} 之前初始化失败，重试")
                    Tab_1._failed_instances.discard(instance_id)
                    Tab_1._initialization_state[instance_id] = 'pending'

            # 标记初始化开始
            Tab_1._initialization_state[instance_id] = 'init_started'

        # ==================== 第二层：调用父类初始化（关键！） ====================
        try:
            super().__init__()  # 必须在最前面调用，不能有任何 hasattr() 或属性访问
            # logger.info(f"父类初始化完成")
        except Exception as e:
            logger.error(f"父类初始化失败: {e}", exc_info=True)
            Tab_1._initialization_state[instance_id] = 'failed'
            Tab_1._failed_instances.add(instance_id)
            raise

        # ==================== 第三层：初始化属性 ====================
        try:
            self._init_attributes()
            # logger.info(f"属性初始化完成")
        except Exception as e:
            logger.error(f"属性初始化失败: {e}", exc_info=True)
            Tab_1._initialization_state[instance_id] = 'failed'
            Tab_1._failed_instances.add(instance_id)
            raise

        # ==================== 第四层：初始化UI ====================
        try:
            self._init_ui(parent, geometry, title)
            # logger.info(f"UI初始化完成")
        except Exception as e:
            logger.error(f"UI初始化失败: {e}", exc_info=True)
            Tab_1._initialization_state[instance_id] = 'failed'
            Tab_1._failed_instances.add(instance_id)
            raise

        # ==================== 第五层：缓存UI组件 ====================
        try:
            self._cache_ui_components()
            # logger.info(f"UI组件缓存完成")
        except Exception as e:
            logger.error(f"UI组件缓存失败: {e}", exc_info=True)
            Tab_1._initialization_state[instance_id] = 'failed'
            Tab_1._failed_instances.add(instance_id)
            raise

        # ==================== 第六层：初始化数据 ====================
        try:
            self._init_data()
            # logger.info(f"数据初始化完成")
        except Exception as e:
            logger.error(f"数据初始化失败: {e}", exc_info=True)
            Tab_1._initialization_state[instance_id] = 'failed'
            Tab_1._failed_instances.add(instance_id)
            raise

        # ==================== 第七层：加载配置 ====================
        try:
            self.experiment_setting = global_setting.get_setting("experiment_setting", None)
            self.config = get_default_config()
            # logger.info(f"配置加载完成")
        except Exception as e:
            logger.error(f"配置加载失败: {e}", exc_info=True)
            Tab_1._initialization_state[instance_id] = 'failed'
            Tab_1._failed_instances.add(instance_id)
            raise

        # ==================== 第八层：自定义UI初始化 ====================
        try:
            self._init_customize_ui()
            # logger.info(f"自定义UI初始化完成")
        except Exception as e:
            logger.error(f"自定义UI初始化失败: {e}", exc_info=True)
            Tab_1._initialization_state[instance_id] = 'failed'
            Tab_1._failed_instances.add(instance_id)
            raise

        # ==================== 第九层：连接信号 ====================
        try:
            self._connect_air_module_signals()
            # logger.info(f"信号连接完成")
        except Exception as e:
            logger.error(f"信号连接失败: {e}", exc_info=True)
            Tab_1._initialization_state[instance_id] = 'failed'
            Tab_1._failed_instances.add(instance_id)
            raise

        # ==================== 第十层：函数初始化 ====================
        try:
            self._init_function()
            # logger.info(f"函数初始化完成")
        except Exception as e:
            logger.error(f"函数初始化失败: {e}", exc_info=True)
            Tab_1._initialization_state[instance_id] = 'failed'
            Tab_1._failed_instances.add(instance_id)
            raise

        # ==================== 第十一层：样式表 ====================
        try:
            self._init_style_sheet()
            # logger.info(f"样式表初始化完成")
        except Exception as e:
            logger.error(f"样式表初始化失败: {e}", exc_info=True)
            Tab_1._initialization_state[instance_id] = 'failed'
            Tab_1._failed_instances.add(instance_id)
            raise

        # ==================== 第十二层：教程设置 ====================
        try:
            self.setup_tutorial()
        except Exception as e:
            logger.error(f"教程设置失败: {e}", exc_info=True)
            # 这里不中止，因为教程失败不影响主体功能
            logger.warning(f"继续执行，教程功能暂时禁用")

        if parent is None:
            QTimer.singleShot(400, self.start_tutorial_if_exists)

        # ==================== 最后：标记完成 ====================
        with Tab_1._instance_lock:
            Tab_1._initialization_state[instance_id] = 'completed'


    @staticmethod
    def _environment_module_only_enabled():
        value = global_setting.get_setting("environment_module_only", False)
        if isinstance(value, bool):
            return value
        return str(value).strip().lower() in {"1", "true", "yes", "on"}

    def _required_cage_modules(self):
        if self._environment_module_only_enabled():
            return {"ENM"}
        return {"ENM", "EM", "DWM", "WM"}

    def _init_attributes(self):
        """初始化所有属性 - 必须在 super().__init__() 之后调用"""

        # ==================== 配置管理相关 ====================
        self.current_cage_config = {}
        self.user_config_dir = Path.home() / ".mouse_experiment_config" / "cage_configs"
        self.user_config_dir.mkdir(parents=True, exist_ok=True)

        # ==================== 线程安全相关 ====================
        self.response_lock = threading.Lock()

        # ==================== 气路检测相关属性 ====================
        self.air_module_detection_lock = threading.RLock()
        self._air_detection_finished = True
        self._air_detection_final_result_cached = None
        self._air_ui_has_been_updated = False

        # 气路模块状态字典
        self.air_modules_completed = {}
        self.air_modules_detected = {}
        self.air_modules_valid = {}
        if self._environment_module_only_enabled():
            self.air_modules_to_detect = []
            self.required_air_modules = set()
        else:
            self.air_modules_to_detect = ['UFC', 'UGC', 'ZOS']
            self.required_air_modules = {'UFC', 'UGC', 'ZOS'}

        self.air_module_detection_results = {
            'UFC': {'detected': False, 'valid': False},
            'UGC': {'detected': False, 'valid': False},
            'ZOS': {'detected': False, 'valid': False}
        }

        # 气路检测完成事件
        self.air_detection_complete_event = threading.Event()
        self.air_detection_complete_event.clear()

        # ==================== 检测相关属性 ====================
        self.port_confirmed = False
        self.detection_in_progress = False
        self.cage_list_to_detect = []
        self.current_detecting_index = 0
        self.cage_detection_timers = {}
        self._completed_cages = {}
        self._cage_detection_finished = True
        self.current_detection_session_id = 0
        self._module_labels_initialized = False
        self._module_labels_object_ids = {}

        # ==================== UI 组件初始化为 None ====================
        self.port_combox = None
        self.cage_list_widget = None
        self.detection_status_label = None
        self.right_title = None
        self.vr_desc_text = None
        self.experiment_setting = None
        self.span_oxygen_desc_text = None
        self.span_carbon_desc_text = None
        self.calibration_checkbox = None
        self.confirm_port_btn = None
        self.refresh_detection_btn = None
        self.config_btn = None
        self.config_layout = None
        self.content_layout = None
        self.start_btn = None
        self.module_status_labels = {}
        self.group_box = None
        self.config_scroll_area = None
        self.basic_config_layout = None
        self.module_detection_layout = None

        # ==================== 数据相关 ====================
        self.send_message = {
            'port': '',
            'data': '',
            'slave_id': 0,
            'function_code': 0,
            'timeout': 0,
            'group_id': None
        }

        self.ports = []
        self.config = None
        self.cage_enabled_status = {}
        self.calibration_selected = global_setting.get_setting("device_config_calibration_selected", False)
        self.device_config_ready = False
        self._calibration_signal_connected = False

    def _init_ui(self, parent=None, geometry: QRect = None, title=""):
        """初始化UI"""
        if parent is not None:
            self.setParent(parent)
            if geometry is not None:
                self.setGeometry(geometry)

        self.ui = Ui_tab1_frame()
        self.ui.setupUi(self)
        self._retranslateUi()

    def _cache_ui_components(self):
        """缓存所有 UI 组件"""
        try:
            self.cage_list_widget = getattr(self.ui, 'cage_list_widget', None) or self.findChild(QListWidget,
                                                                                                 "cage_list_widget")
            self.config_scroll_area = getattr(self.ui, 'config_scroll_area', None) or self.findChild(QScrollArea,
                                                                                                     "config_scroll_area")

            if self.config_scroll_area:
                widget = self.config_scroll_area.widget()
                if widget:
                    self.content_layout = widget.layout()

            self.basic_config_layout = getattr(self.ui, 'basic_config_layout', None) or self.findChild(QVBoxLayout,
                                                                                                       "basic_config_layout")
            self.module_detection_layout = getattr(self.ui, 'module_detection_layout', None) or self.findChild(
                QVBoxLayout, "module_detection_layout")
            self.port_combox = getattr(self.ui, 'tab_1_port_combox', None) or self.findChild(QComboBox,
                                                                                             "tab_1_port_combox")
            self.confirm_port_btn = getattr(self.ui, 'tab_1_confirm_port_btn', None) or self.findChild(QPushButton,
                                                                                                        "tab_1_confirm_port_btn")
            self.refresh_detection_btn = getattr(self.ui, 'tab_1_refresh_detection_btn', None) or self.findChild(
                QPushButton, "tab_1_refresh_detection_btn")
            self.start_btn = getattr(self.ui, 'start_btn', None) or self.findChild(QPushButton, "start_btn")

            self.right_title = self.findChild(QLabel, "right_title_label")
            self.detection_status_label = self.findChild(QLabel, "detection_status_label")
            self.config_btn = self.findChild(QPushButton, "config_btn")


        except Exception as e:
            logger.error(f"缓存UI组件失败: {e}", exc_info=True)

    def _init_data(self):
        """获得相关数据"""
        self.ports = scan_serial_ports_with_id()

    def _init_customize_ui(self):
        """初始化自定义UI"""
        if self.experiment_setting is None:
            self.experiment_setting = global_setting.get_setting("experiment_setting", None)

        self.init_port_combox()
        self.config = get_default_config()

        # 只在第一次调用时初始化模块显示
        self.init_module_detection_display()

        QTimer.singleShot(200, self.init_cage_list)
        self.init_config_ui()
        super()._init_customize_ui()

    def init_module_detection_display(self):
        """初始化气路模块检测显示 - 修复版（只初始化一次）"""



        if self.module_detection_layout is None:
            logger.error("module_detection_layout 未找到！")
            return

        self.remove_layout_items(self.module_detection_layout)
        self.module_detection_layout.setSpacing(15)
        self.module_detection_layout.setContentsMargins(10, 10, 10, 10)

        self.module_status_labels = {}

        if self._environment_module_only_enabled():
            status_label = QtWidgets.QLabel(
                "仅环境模块测试：UFC、UGC、ZOS 检测已跳过"
            )
            status_label.setStyleSheet(
                "QLabel { color: #28764f; font-weight: bold; padding: 6px; }"
            )
            self.module_detection_layout.addWidget(status_label)
            self.module_detection_layout.addStretch()
            return

        modules_to_detect = [
            {'name': 'UFC', 'key': 'UFC'},
            {'name': 'UGC', 'key': 'UGC'},
            {'name': 'ZOS', 'key': 'ZOS'},
        ]

        for module_info in modules_to_detect:
            module_name = module_info['name']
            module_key = module_info['key']

            h_layout = QtWidgets.QHBoxLayout()
            h_layout.setSpacing(10)
            h_layout.setContentsMargins(5, 1, 5, 1)

            # 模块名称标签
            name_label = QtWidgets.QLabel(f"{module_name}:")
            name_label.setMinimumWidth(45)  # 稍微减小宽度
            name_label.setMaximumWidth(55)
            name_label.setMinimumHeight(24)  # 适中的高度
            name_label.setMaximumHeight(24)
            name_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
            name_label.setStyleSheet("""
                QLabel {
                    font-weight: bold;
                    font-size: 12px;
                    color: #333;
                }
            """)

            # 状态标签
            status_label = QtWidgets.QLabel("待检测")
            status_label.setMinimumWidth(110)  # 稍微减小宽度
            status_label.setMaximumWidth(180)
            status_label.setMinimumHeight(24)  # 匹配名称标签高度
            status_label.setMaximumHeight(24)
            status_label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            status_label.setWordWrap(False)
            status_label.setStyleSheet("""
                QLabel {
                    font-size: 11px;
                    color: #666;
                    padding: 2px;
                    border-radius: 3px;
                    background-color: #f5f5f5;
                    border: 1px solid #ddd;
                }
            """)

            h_layout.addWidget(name_label)
            h_layout.addWidget(status_label)
            h_layout.addStretch()

            self.module_detection_layout.addLayout(h_layout)
            self.module_status_labels[module_key] = status_label

            # 记录对象ID
            self._module_labels_object_ids[module_key] = id(status_label)

        #     logger.debug(f"✓ 初始化 {module_name} 显示区域 (ID: {id(status_label)})")
        # for i,j in self.module_status_labels.items():
        #     logger.critical(f"{i}:{j.text()}:{j}")
        self.module_detection_layout.addStretch()



    # ==================== 连接信号槽 ====================
    def _connect_air_module_signals(self):
        """连接信号槽 - 确保连接"""
        try:
            # 改用 QueuedConnection，让槽在主线程执行
            self.signal_air_module_update.connect(
                self._slot_on_air_module_ui_update,
                Qt.ConnectionType.QueuedConnection  # 改成 QueuedConnection
            )

            self.signal_detection_status_update.connect(
                self._slot_on_detection_status_update,
                Qt.ConnectionType.QueuedConnection
            )

        except Exception as e:
            logger.error(f"连接信号槽失败: {e}", exc_info=True)

    # ==================== 槽函数 ====================
    @pyqtSlot(str, bool)
    def _slot_on_air_module_ui_update(self, module_name: str, is_valid: bool):
        """更新气路模块UI"""
        try:

            if module_name not in  self.module_status_labels.keys():
                return

            status_label = self.module_status_labels[module_name]

            new_text = "✓ 有效" if is_valid else "✗ 无效"
            status_label.setText(new_text)

            # 设置样式
            if is_valid:
                status_label.setStyleSheet("""
                    QLabel {
                        font-size: 12px;
                        color: #228B22;
                        padding: 5px;
                        border-radius: 3px;
                        background-color: #F0FFF0;
                        border: 1px solid #90EE90;
                        font-weight: bold;
                    }
                """)
            else:
                status_label.setStyleSheet("""
                    QLabel {
                        font-size: 12px;
                        color: #DC143C;
                        padding: 5px;
                        border-radius: 3px;
                        background-color: #FFF0F5;
                        border: 1px solid #FFB6C1;
                        font-weight: bold;
                    }
                """)

            # logger.info(f"{module_name}: {new_text}:{status_label.text()}:{status_label}")

        except Exception as e:
            logger.error(f"异常: {e}", exc_info=True)

    @pyqtSlot(str)
    def _slot_on_detection_status_update(self, status_text: str):
        """更新检测状态"""
        try:
            if self.detection_status_label:
                self.detection_status_label.setText(status_text)
                self.detection_status_label.update()
                self.detection_status_label.repaint()

                # logger.info(f"检测状态已更新: {status_text}")

                QApplication.processEvents()

        except Exception as e:
            logger.error(f"更新检测状态失败: {e}", exc_info=True)

    def _init_function(self):
        """初始化功能"""
        self.init_btn_func()

        self.Each_Mouse_Cage_detect_finished_signal.connect(
            self.each_Mouse_Cage_detect_update_state,
            Qt.ConnectionType.QueuedConnection
        )
        self.Not_Each_Mouse_Cage_detect_finished_signal.connect(
            self.not_each_Mouse_Cage_detect_update_state,
            Qt.ConnectionType.QueuedConnection
        )

        global read_queue_data_thread

        if read_queue_data_thread is not None:
            # 无论线程是否在跑，都重新绑定信号到当前实例
            read_queue_data_thread.queue = global_setting.get_setting("send_message_queue")
            read_queue_data_thread.Each_Mouse_Cage_detect_finished_signal = self.Each_Mouse_Cage_detect_finished_signal
            read_queue_data_thread.Not_Each_Mouse_Cage_detect_finished_signal = self.Not_Each_Mouse_Cage_detect_finished_signal
            read_queue_data_thread.update_group_activation_signal = self.update_group_activation_signal

            if not read_queue_data_thread.isRunning():
                read_queue_data_thread.start()

    def set_main_gui(self, main_gui):
        super().set_main_gui(main_gui)
        if (self.main_gui is not None and
                hasattr(self.main_gui, "calibration_selection_changed_signal") and
                not self._calibration_signal_connected):
            self.main_gui.calibration_selection_changed_signal.connect(self.on_calibration_selection_changed)
            self._calibration_signal_connected = True

        self.calibration_selected = global_setting.get_setting("device_config_calibration_selected", False)
        self.update_calibration_button_text()
        self.update_device_config_button_state()

    def setup_tutorial(self):
        # The redesigned six-step shell replaces the legacy floating tutorial.
        if self.tutorial:
            self.tutorial.end_tutorial()
        self.tutorial = None
        return
        """设置教程"""
        if self.tutorial:
            self.tutorial.end_tutorial()

        self.tutorial = TutorialManager(
            self, "experiment_setting", Tutorial_Type.ARROW_GUIDE,
            global_setting.get_setting("app_setting", AppSettings())
        )

        self.tutorial.tutorial_completed.connect(self.on_tutorial_completed)

        self.tutorial.add_step(
            self.port_combox,
            f"步骤1：选择正确的串口，这步非常的重要，不然无法联系到传感器。"
        )
        self.tutorial.add_step(
            self.cage_list_widget if hasattr(self,
                                             'cage_list_widget') and self.cage_list_widget else self.config_layout,
            f"步骤2：可以对相关传感器进行实验前的相关配置。"
        )
        self.tutorial.add_step(
            self.start_btn,
            f"步骤3：最后单击该按钮完成配置。"
        )
        self.tutorial.add_step(
            self.status_bar.tip_btn,
            f"Tips：如果还不会操作，可再次单击该按钮查看教程。"
        )

    # ==========UI初始化相关==========
    def init_port_combox(self):
        """初始化端口下拉框"""
        if self.port_combox is None:
            logger.error("实例化端口下拉框失败！")
            return

        self.port_combox.clear()
        for port_obj in self.ports:
            self.port_combox.addItem(f"设备: {port_obj['device']} - {port_obj['description']}")

        if len(self.ports) != 0:
            self.send_message['port'] = self.ports[0]['device']
            logger.info(
                f"{time_util.get_format_from_time(time.time())}- 设备: {self.ports[0]['device']} "
                f"({self.ports[0]['description']}) - 待用户确认"
            )

        try:
            self.port_combox.disconnect()
        except:
            pass
        self.port_combox.currentIndexChanged.connect(self.on_port_selection_changed)

    def init_cage_list(self):
        """初始化鼠笼列表（已启用的笼子）"""
        if self.experiment_setting is None:
            self.experiment_setting = global_setting.get_setting("experiment_setting", None)

        if self.experiment_setting is None:
            # logger.error("experiment_setting 仍未加载，无法初始化笼子列表")
            return

        if self.cage_list_widget is None:
            logger.error("cage_list_widget 为 None")
            return

        self.cage_list_widget.clear()
        self.cage_enabled_status.clear()

        try:
            self.cage_list_widget.itemClicked.disconnect()
        except TypeError:
            pass
        self.cage_list_widget.itemClicked.connect(self._on_cage_clicked)

        if self.experiment_setting.groups:
            enabled_groups = [g for g in self.experiment_setting.groups if g.is_selected == 1]

            for group in enabled_groups:
                group_id = group.id
                item_text = f"鼠笼 {group_id} - 待检测"
                item = QListWidgetItem(item_text)
                item.setData(Qt.ItemDataRole.UserRole, group_id)
                item.setFlags(Qt.ItemFlag.NoItemFlags)

                self.cage_list_widget.addItem(item)
                self.cage_enabled_status[group_id] = group

    def init_config_ui(self):
        """初始化配置UI - 显示默认配置"""
        if self.experiment_setting is None:
            self.experiment_setting: Experiment_setting_entity = global_setting.get_setting("experiment_setting", None)

        if self.content_layout is None:
            logger.error("content_layout 未找到！")
            return

        self.remove_layout_items(self.content_layout)

        tip_label = QLabel("请先确认串口，然后选择要配置的鼠笼")
        tip_label.setStyleSheet("color: #666; font-style: italic;")
        self.content_layout.addWidget(tip_label)

        self.init_basic_config(self.content_layout)

        if self.config:
            for module_key, module_value in self.config.items():
                if module_key == Modbus_Type.Modbus_Slave_Ids.ENM.value['name']:
                    self.init_enm_config_ui_default(module_key, module_value, self.content_layout)
                elif (
                    not self._environment_module_only_enabled()
                    and module_key == Modbus_Type.Modbus_Slave_Ids.EM.value['name']
                ):
                    self.init_em_config_ui_default(module_key, module_value, self.content_layout)

        self.content_layout.addStretch()

    def init_basic_config(self, scroll_area_layout):
        """设置基础设置"""
        self.group_box = QGroupBox(f"基本配置")
        self.group_box.setStyleSheet("""
        QGroupBox {
            font-size:17px;
            font-weight:bolder;
        }
        """)
        self.group_box.setContentsMargins(10, 10, 10, 10)
        scroll_area_layout.addWidget(self.group_box)
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(10, 18, 10, 10)
        main_layout.setSpacing(12)
        self.calibration_checkbox = None

        h0_layout = QHBoxLayout()
        span_oxygen_desc_label = QLabel("span校准的标准气体的氧浓度数值（单位:%,例如20.9%，请输入20.9）：")
        span_oxygen_desc_label.setStyleSheet("""
                      QLabel {
                          font-weight:bold;
                      }
                      """)
        self.span_oxygen_desc_text = QDoubleSpinBox()
        self.span_oxygen_desc_text.setValue(global_setting.get_setting("span_standard_oxygen_value", 20.9))

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
        self.span_carbon_desc_text.setValue(global_setting.get_setting("span_standard_carbon_value", 0.03))

        h1_layout.addWidget(span_carbon_desc_label)
        h1_layout.addWidget(self.span_carbon_desc_text)
        main_layout.addLayout(h1_layout)

        h_layout = QHBoxLayout()
        vr_desc_label = QLabel("请输入Vr值[已弃用]（实际的标定气体，根据气瓶上的标识确定,单位:%,例如20.9%，请输入20.9）：")
        vr_desc_label.setStyleSheet("""
                              QLabel {
                                  font-weight:bold;
                              }
                              """)
        self.vr_desc_text = QDoubleSpinBox()
        self.vr_desc_text.setValue(global_setting.get_setting("Vr", 20.9))

        h_layout.addWidget(vr_desc_label)
        h_layout.addWidget(self.vr_desc_text)
        main_layout.addLayout(h_layout)

        self.group_box.setLayout(main_layout)

    def calibration_gas_state_change(self, state):
        legacy_mode = "full" if bool(state) else "none"
        self.set_startup_calibration_mode(legacy_mode, sync_checkbox=False)
        return
        """校准气体状态变化处理"""
        self.set_calibration_mode(bool(state), sync_checkbox=False)

    def set_calibration_mode(self, is_checked, sync_checkbox=True, sync_toolbar=True, notify_monitor=True):
        legacy_mode = "full" if bool(is_checked) else "none"
        self.set_startup_calibration_mode(
            legacy_mode,
            sync_checkbox=sync_checkbox,
            sync_toolbar=sync_toolbar,
            notify_monitor=notify_monitor
        )
        return
        """统一处理是否启用校准配置"""
        if sync_checkbox and self.calibration_checkbox is not None:
            self.calibration_checkbox.blockSignals(True)
            self.calibration_checkbox.setChecked(is_checked)
            self.calibration_checkbox.blockSignals(False)

        if sync_toolbar and self.main_gui is not None:
            for tool_bar_action in self.main_gui.tool_bar_actions:
                if tool_bar_action['obj_name'] in ["calibration_gas"]:
                    tool_bar_action["action"].blockSignals(True)
                    tool_bar_action["action"].setChecked(is_checked)
                    tool_bar_action["action"].blockSignals(False)
                    break

        global_setting.set_setting("is_auto_calibration", is_checked)
        self.update_calibration_button_text()

        if notify_monitor:
            send_message_queue = global_setting.get_setting("send_message_queue")
            if send_message_queue is not None:
                send_message_queue.put(
                    ObjectQueueItem(origin='tab_7', to='main_monitor_data', title='set_experiment_basic_config',
                                    data={"is_auto_calibration": is_checked},
                                    time=time_util.get_format_from_time(time.time())))

    @staticmethod
    def normalize_startup_calibration_mode(mode):
        if mode in {"none", "air", "air_co2", "full"}:
            return mode
        return "none"

    def get_startup_calibration_mode(self):
        mode = global_setting.get_setting("startup_calibration_mode", None)
        if mode not in {"none", "air", "air_co2", "full"}:
            mode = "none"
        return mode

    def set_startup_calibration_mode(self, mode, sync_checkbox=True, sync_toolbar=True, notify_monitor=True):
        mode = self.normalize_startup_calibration_mode(mode)
        is_checked = mode != "none"

        if sync_checkbox and self.calibration_checkbox is not None:
            self.calibration_checkbox.blockSignals(True)
            self.calibration_checkbox.setChecked(is_checked)
            self.calibration_checkbox.blockSignals(False)

        if sync_toolbar and self.main_gui is not None:
            for tool_bar_action in self.main_gui.tool_bar_actions:
                if tool_bar_action['obj_name'] in ["calibration_gas"]:
                    action = tool_bar_action["action"]
                    if hasattr(action, "setChecked"):
                        action.blockSignals(True)
                        action.setChecked(is_checked)
                        action.blockSignals(False)
                    break

        global_setting.set_setting("startup_calibration_mode", mode)
        global_setting.set_setting("is_auto_calibration", is_checked)
        self.update_calibration_button_text()

        if notify_monitor:
            send_message_queue = global_setting.get_setting("send_message_queue")
            if send_message_queue is not None:
                send_message_queue.put(
                    ObjectQueueItem(
                        origin='tab_7',
                        to='main_monitor_data',
                        title='set_experiment_basic_config',
                        data={
                            "startup_calibration_mode": mode,
                            "is_auto_calibration": is_checked
                        },
                        time=time_util.get_format_from_time(time.time())
                    )
                )

    def update_calibration_button_text(self):
        """同步红框区域里的校准按钮文案"""
        if self.main_gui is None:
            return

        for module in getattr(self.main_gui, "modules", []):
            if getattr(module, "name", "") == "New_main_experiment_calibration":
                if hasattr(module, "refresh_display_text"):
                    module.refresh_display_text()
                if hasattr(module, "sync_action_enabled_state"):
                    module.sync_action_enabled_state()
                break

    def reset_calibration_selection_state(self, emit_signal=True):
        """开始新实验或重新检测时，重置校准选择状态。"""
        self.calibration_selected = False
        global_setting.set_setting("device_config_calibration_selected", False)
        self.set_startup_calibration_mode("none", sync_checkbox=True, sync_toolbar=True, notify_monitor=False)

        if emit_signal and self.main_gui is not None and hasattr(self.main_gui, "calibration_selection_changed_signal"):
            self.main_gui.calibration_selection_changed_signal.emit(
                False,
                global_setting.get_setting("startup_calibration_mode", "none")
            )

        self.update_calibration_button_text()
        self.update_device_config_button_state()

    def update_device_config_button_state(self):
        """根据检测状态和校准选择状态控制确认设备配置按钮"""
        can_config = True

        if self.start_btn is not None:
            self.start_btn.setEnabled(can_config)

        if self.config_btn is not None:
            self.config_btn.setEnabled(can_config)

    def on_calibration_selection_changed(self, selection_made, startup_calibration_mode):
        self.calibration_selected = bool(selection_made)
        global_setting.set_setting("device_config_calibration_selected", self.calibration_selected)
        self.set_startup_calibration_mode(
            startup_calibration_mode,
            sync_checkbox=True,
            sync_toolbar=True,
            notify_monitor=False
        )
        self.update_device_config_button_state()
        return
        """接收主窗口红框区域校准按钮的选择结果"""
        self.calibration_selected = bool(selection_made)
        global_setting.set_setting("device_config_calibration_selected", self.calibration_selected)
        self.set_calibration_mode(is_auto_calibration, sync_checkbox=True, sync_toolbar=True, notify_monitor=False)
        self.update_device_config_button_state()

    def init_btn_func(self):
        """初始化按钮功能"""
        refresh_port_btn: QPushButton = self.findChild(QPushButton, "tab_1_refresh_port_btn")
        if refresh_port_btn:
            refresh_port_btn.clicked.connect(self.refresh_port)

        if self.confirm_port_btn:
            self.confirm_port_btn.clicked.connect(self.confirm_port)
            self.confirm_port_btn.setEnabled(True)

        if self.refresh_detection_btn:
            self.refresh_detection_btn.clicked.connect(self.refresh_detection)
            self.refresh_detection_btn.setEnabled(False)

        self.config_btn: QPushButton = self.findChild(QPushButton, "config_btn")
        if self.config_btn:
            self.config_btn.clicked.connect(self.start_device_config)
            self.config_btn.setEnabled(True)

        if self.start_btn:
            self.start_btn.clicked.connect(self.start_device_config)
            self.start_btn.setEnabled(True)

        self.update_device_config_button_state()
        self._update_refresh_detection_button_state()

    def _update_refresh_detection_button_state(self):
        """同步刷新检测按钮状态，防止检测轮次重叠。"""
        if self.refresh_detection_btn is None:
            return

        is_busy = self.detection_in_progress or not self._air_detection_finished or not self._cage_detection_finished
        can_refresh = self.port_confirmed and not is_busy
        self.refresh_detection_btn.setEnabled(can_refresh)
        self.refresh_detection_btn.setText("检测中..." if self.port_confirmed and is_busy else "刷新检测")

    def _reset_air_module_status_labels(self, status_text="待检测"):
        """重置气路模块显示，保证每轮检测从干净状态开始。"""
        for module_name in self.air_modules_to_detect:
            status_label = self.module_status_labels.get(module_name)
            if status_label is None:
                logger.error(f"模块 {module_name} 不在标签字典中！")
                continue

            status_label.blockSignals(True)
            status_label.setText(status_text)
            status_label.setStyleSheet("""
                QLabel {
                    font-size: 11px;
                    color: #666;
                    padding: 2px;
                    border-radius: 3px;
                    background-color: #f5f5f5;
                    border: 1px solid #ddd;
                }
            """)
            status_label.blockSignals(False)
            status_label.update()
            status_label.repaint()

    def _new_detection_session_id(self):
        """生成新的检测轮次ID，用于过滤旧回包。"""
        self.current_detection_session_id += 1
        return self.current_detection_session_id

    def _is_current_detection_session(self, state_data):
        """只处理当前轮次的检测结果，避免旧回包污染新一轮检测。"""
        session_id = state_data.get("detection_session_id")
        if session_id != self.current_detection_session_id:
            logger.warning(
                f"忽略过期检测结果: session={session_id}, current={self.current_detection_session_id}, "
                f"module={state_data.get('module_name')}, cage={state_data.get('mouse_cage_number')}"
            )
            return False
        return True

    def _start_detection_cycle(self):
        """启动一轮新的模块检测，支持重复点击刷新。"""
        if self.experiment_setting is None:
            self.experiment_setting = global_setting.get_setting("experiment_setting", None)
            if self.experiment_setting is None:
                self.show_warning("错误", "实验设置未加载，请稍候...")
                return False

        send_message_queue = global_setting.get_setting("send_message_queue", None)
        if not send_message_queue:
            logger.error("send_message_queue 未找到，无法发送报文")
            self.show_warning("错误", "消息队列未找到，请重启应用")
            return False

        self.reset_calibration_selection_state()
        self.device_config_ready = False
        self.update_device_config_button_state()
        global_setting.set_setting(
            "air_modules_all_valid",
            self._environment_module_only_enabled(),
        )
        if self.main_gui is not None:
            self.main_gui.change_enable_component_app_state_signal.emit()

        self._cleanup_all_timers()
        self.init_cage_list()
        self.init_config_ui()
        self._reset_air_module_status_labels()

        with self.air_module_detection_lock:
            self.air_detection_complete_event.clear()
            self._air_detection_finished = self._environment_module_only_enabled()
            self._air_ui_has_been_updated = False
            self._air_detection_final_result_cached = None
            self.air_modules_completed = {module_name: False for module_name in self.air_modules_to_detect}
            self.air_modules_detected = {module_name: False for module_name in self.air_modules_to_detect}
            self.air_modules_valid = {module_name: False for module_name in self.air_modules_to_detect}

        self._completed_cages.clear()
        self.cage_list_to_detect = [int(cage_id) for cage_id in self.cage_enabled_status.keys()]
        self.current_detecting_index = 0
        self.cage_detection_timers.clear()
        self._cage_detection_finished = False
        for cage_id in self.cage_list_to_detect:
            self._completed_cages[int(cage_id)] = False

        mouse_cage_detect_dict = {}
        for cage_id in self.cage_list_to_detect:
            cage_id_int = int(cage_id)
            mouse_cage_detect_dict[cage_id_int] = {
                'cage_modules': {},
                'air_modules': {},
                'cage_is_valid': False,
                'detection_complete': False,
                'update_time': time_util.get_format_from_time(time.time())
            }
        global_setting.set_setting("mouse_cage_detect_state_dict", mouse_cage_detect_dict)

        if self.detection_status_label:
            self.detection_status_label.setText("检测中...")

        self.detection_in_progress = True
        detection_session_id = self._new_detection_session_id()
        self._update_refresh_detection_button_state()

        send_message_queue.put(ObjectQueueItem(
            origin='New_main_experiment_setting',
            to='main_monitor_data',
            title='set_port',
            data=self.send_message['port'],
            time=time_util.get_format_from_time(time.time())
        ))

        if not self._environment_module_only_enabled():
            send_message_queue.put(ObjectQueueItem(
                origin="New_main_experiment_setting",
                to="main_monitor_data",
                title="detect_air_modules_only",
                data={
                    'port': self.send_message['port'],
                    'mouse_cage_index': None,
                    'detection_session_id': detection_session_id
                },
                time=time_util.get_format_from_time(time.time())
            ))

        logger.info(
            f"启动检测轮次 session={detection_session_id} | "
            f"Port={self.send_message['port']} | Cages={self.cage_list_to_detect}"
        )

        QtWidgets.QApplication.processEvents()
        detection_delay_ms = 0 if self._environment_module_only_enabled() else 3000
        QTimer.singleShot(detection_delay_ms, self._detect_next_cage)
        return True

    # ==========配置UI创建==========
    def init_em_config_ui_default(self, module_key, module_value, scroll_area_layout):
        """初始化 EM 配置UI - 默认显示（不含具体鼠笼）"""
        group_box = QGroupBox(
            f"{module_value['desc']}-{module_value['config'][0]['value'][0]['desc']}"
        )
        group_box.setContentsMargins(5, 5, 5, 5)
        group_box.setMinimumHeight(70)
        group_box.setMaximumHeight(100)
        scroll_area_layout.addWidget(group_box)

        grid_layout1 = QGridLayout()
        grid_layout1.setContentsMargins(5, 10, 5, 5)
        grid_layout1.setSpacing(5)
        group_box.setLayout(grid_layout1)

        label = QLabel("选择鼠笼后将显示具体配置")
        label.setStyleSheet("color: #999;")

        radio_on = QRadioButton(
            f"{module_value['config'][0]['value'][0]['refer_value']['0']['desc']}"
        )
        radio_on.setObjectName("on")
        radio_on.setEnabled(False)
        radio_off = QRadioButton(
            f"{module_value['config'][0]['value'][0]['refer_value']['1']['desc']}"
        )
        radio_off.setObjectName("off")
        radio_off.setChecked(True)
        radio_off.setEnabled(False)

        grid_layout1.addWidget(label, 0, 0)
        grid_layout1.addWidget(radio_on, 0, 1)
        grid_layout1.addWidget(radio_off, 0, 2)

    def init_em_config_ui_for_group(self, module_key, module_value, scroll_area_layout, group_num, saved_config=None):
        """初始化 EM 配置UI（针对特定鼠笼）"""
        if saved_config is None:
            saved_config = {}

        group_box = QGroupBox(
            f"{module_value['desc']}-{module_value['config'][0]['value'][0]['desc']}"
        )
        group_box.setContentsMargins(5, 5, 5, 5)
        group_box.setMinimumHeight(70)
        group_box.setMaximumHeight(100)
        scroll_area_layout.addWidget(group_box)

        grid_layout1 = QGridLayout()
        grid_layout1.setContentsMargins(5, 10, 5, 5)
        grid_layout1.setSpacing(5)
        group_box.setLayout(grid_layout1)

        label = QLabel(f"鼠笼 {group_num}")

        radio_on = QRadioButton(
            f"{module_value['config'][0]['value'][0]['refer_value']['0']['desc']}"
        )
        radio_on.setObjectName("on")
        radio_off = QRadioButton(
            f"{module_value['config'][0]['value'][0]['refer_value']['1']['desc']}"
        )
        radio_off.setObjectName("off")

        saved_value = saved_config.get('config_0', 'off')
        if saved_value == 'on':
            radio_on.setChecked(True)
        else:
            radio_off.setChecked(True)

        button_group = QButtonGroup(grid_layout1)
        button_group.addButton(radio_on)
        button_group.addButton(radio_off)
        button_group.buttonClicked.connect(
            lambda button, address=module_value['address'], mouse_cage_number=group_num,
                   function_code=module_value['config'][0]['function_code'],
                   data_lists=module_value['config'][0]['value'][0]['refer_value'],
                   config_key='EM_config_0':
            self.on_radio_button_clicked(button, address, mouse_cage_number, function_code, data_lists, config_key)
        )

        grid_layout1.addWidget(label, 0, 0)
        grid_layout1.addWidget(radio_on, 0, 1)
        grid_layout1.addWidget(radio_off, 0, 2)

    def init_enm_config_ui_default(self, module_key, module_value, scroll_area_layout):
        """初始化 ENM 配置UI - 默认显示（不含具体鼠笼）"""
        # 第1个 GroupBox
        group_box = QGroupBox(
            f"{module_value['desc']}-{module_value['config'][0]['value'][0]['desc']}"
        )
        group_box.setContentsMargins(5, 5, 5, 5)
        group_box.setMinimumHeight(70)
        group_box.setMaximumHeight(100)
        scroll_area_layout.addWidget(group_box)

        grid_layout1 = QGridLayout()
        grid_layout1.setContentsMargins(5, 10, 5, 5)
        grid_layout1.setSpacing(5)
        group_box.setLayout(grid_layout1)

        label = QLabel("选择鼠笼后将显示具体配置")
        label.setStyleSheet("color: #999;")

        radio_on = QRadioButton(
            f"{module_value['config'][0]['value'][0]['refer_value']['0']['desc']}"
        )
        radio_on.setObjectName("on")
        radio_on.setEnabled(False)
        radio_off = QRadioButton(
            f"{module_value['config'][0]['value'][0]['refer_value']['1']['desc']}"
        )
        radio_off.setObjectName("off")
        radio_off.setChecked(True)
        radio_off.setEnabled(False)

        grid_layout1.addWidget(label, 0, 0)
        grid_layout1.addWidget(radio_on, 0, 1)
        grid_layout1.addWidget(radio_off, 0, 2)

        # 第2个 GroupBox
        group_box2 = QGroupBox(
            f"{module_value['desc']}-{module_value['config'][0]['value'][1]['desc']}"
        )
        group_box2.setContentsMargins(10, 10, 10, 10)
        scroll_area_layout.addWidget(group_box2)

        grid_layout2 = QGridLayout()
        grid_layout2.setContentsMargins(10, 30, 10, 10)
        group_box2.setLayout(grid_layout2)

        label = QLabel("选择鼠笼后将显示具体配置")
        label.setStyleSheet("color: #999;")

        radio_on = QRadioButton(
            f"{module_value['config'][0]['value'][1]['refer_value']['0']['desc']}"
        )
        radio_on.setObjectName("on")
        radio_on.setEnabled(False)
        radio_off = QRadioButton(
            f"{module_value['config'][0]['value'][1]['refer_value']['1']['desc']}"
        )
        radio_off.setObjectName("off")
        radio_off.setChecked(True)
        radio_off.setEnabled(False)

        grid_layout2.addWidget(label, 0, 0)
        grid_layout2.addWidget(radio_on, 0, 1)
        grid_layout2.addWidget(radio_off, 0, 2)

        # 第3个 GroupBox（滑块）
        group_box3 = QGroupBox(
            f"{module_value['desc']}-{module_value['config'][1]['value'][0]['desc']}"
        )
        group_box3.setContentsMargins(10, 10, 10, 10)
        scroll_area_layout.addWidget(group_box3)

        grid_layout3 = QGridLayout()
        grid_layout3.setContentsMargins(10, 30, 10, 10)
        group_box3.setLayout(grid_layout3)

        label = QLabel("选择鼠笼后将显示具体配置")
        label.setStyleSheet("color: #999;")
        slider = QSlider()
        slider.setOrientation(Qt.Orientation.Horizontal)
        slider.setMinimum(1)
        slider.setMaximum(9)
        slider.setValue(1)
        slider.setEnabled(False)

        current_value_label = QLabel("当前值: 1")

        grid_layout3.addWidget(label, 0, 0)
        grid_layout3.addWidget(slider, 0, 1)
        grid_layout3.addWidget(current_value_label, 0, 2)

        # 第4个 GroupBox（滑块）
        group_box4 = QGroupBox(
            f"{module_value['desc']}-{module_value['config'][1]['value'][1]['desc']}"
        )
        group_box4.setContentsMargins(10, 10, 10, 10)
        scroll_area_layout.addWidget(group_box4)

        grid_layout4 = QGridLayout()
        grid_layout4.setContentsMargins(10, 30, 10, 10)
        group_box4.setLayout(grid_layout4)

        label = QLabel("选择鼠笼后将显示具体配置")
        label.setStyleSheet("color: #999;")
        slider = QSlider()
        slider.setOrientation(Qt.Orientation.Horizontal)
        slider.setMinimum(1)
        slider.setMaximum(9)
        slider.setValue(1)
        slider.setEnabled(False)

        current_value_label = QLabel("当前值: 1")

        grid_layout4.addWidget(label, 0, 0)
        grid_layout4.addWidget(slider, 0, 1)
        grid_layout4.addWidget(current_value_label, 0, 2)

    def init_enm_config_ui_for_group(self, module_key, module_value, scroll_area_layout, group_num, saved_config=None):
        """初始化 ENM 配置UI（针对特定鼠笼）"""
        if saved_config is None:
            saved_config = {}

        # 第1个 GroupBox
        group_box = QGroupBox(
            f"{module_value['desc']}-{module_value['config'][0]['value'][0]['desc']}"
        )
        group_box.setContentsMargins(10, 10, 10, 10)
        scroll_area_layout.addWidget(group_box)

        grid_layout1 = QGridLayout()
        grid_layout1.setContentsMargins(10, 30, 10, 10)
        group_box.setLayout(grid_layout1)

        label = QLabel(f"鼠笼 {group_num}")

        radio_on = QRadioButton(
            f"{module_value['config'][0]['value'][0]['refer_value']['0']['desc']}"
        )
        radio_on.setObjectName("on")
        radio_off = QRadioButton(
            f"{module_value['config'][0]['value'][0]['refer_value']['1']['desc']}"
        )
        radio_off.setObjectName("off")

        saved_value = saved_config.get('config_0_value_0', 'off')
        if saved_value == 'on':
            radio_on.setChecked(True)
        else:
            radio_off.setChecked(True)

        button_group = QButtonGroup(grid_layout1)
        button_group.addButton(radio_on)
        button_group.addButton(radio_off)
        button_group.buttonClicked.connect(
            lambda button, address=module_value['address'], mouse_cage_number=group_num,
                   function_code=module_value['config'][0]['function_code'],
                   data_lists=module_value['config'][0]['value'][0]['refer_value'],
                   config_key='ENM_config_0_value_0':
            self.on_radio_button_clicked(button, address, mouse_cage_number, function_code, data_lists, config_key)
        )

        grid_layout1.addWidget(label, 0, 0)
        grid_layout1.addWidget(radio_on, 0, 1)
        grid_layout1.addWidget(radio_off, 0, 2)

        # 第2个 GroupBox
        group_box2 = QGroupBox(
            f"{module_value['desc']}-{module_value['config'][0]['value'][1]['desc']}"
        )
        group_box2.setContentsMargins(10, 10, 10, 10)
        scroll_area_layout.addWidget(group_box2)

        grid_layout2 = QGridLayout()
        grid_layout2.setContentsMargins(10, 30, 10, 10)
        group_box2.setLayout(grid_layout2)

        label = QLabel(f"鼠笼 {group_num}")

        radio_on = QRadioButton(
            f"{module_value['config'][0]['value'][1]['refer_value']['0']['desc']}"
        )
        radio_on.setObjectName("on")
        radio_off = QRadioButton(
            f"{module_value['config'][0]['value'][1]['refer_value']['1']['desc']}"
        )
        radio_off.setObjectName("off")

        saved_value = saved_config.get('config_0_value_1', 'off')
        if saved_value == 'on':
            radio_on.setChecked(True)
        else:
            radio_off.setChecked(True)

        button_group = QButtonGroup(grid_layout2)
        button_group.addButton(radio_on)
        button_group.addButton(radio_off)
        button_group.buttonClicked.connect(
            lambda button, address=module_value['address'], mouse_cage_number=group_num,
                   function_code=module_value['config'][0]['function_code'],
                   data_lists=module_value['config'][0]['value'][1]['refer_value'],
                   config_key='ENM_config_0_value_1':
            self.on_radio_button_clicked(button, address, mouse_cage_number, function_code, data_lists, config_key)
        )

        grid_layout2.addWidget(label, 0, 0)
        grid_layout2.addWidget(radio_on, 0, 1)
        grid_layout2.addWidget(radio_off, 0, 2)

        # 第3个 GroupBox（滑块）
        group_box3 = QGroupBox(
            f"{module_value['desc']}-{module_value['config'][1]['value'][0]['desc']}"
        )
        group_box3.setContentsMargins(10, 10, 10, 10)
        scroll_area_layout.addWidget(group_box3)

        grid_layout3 = QGridLayout()
        grid_layout3.setContentsMargins(10, 30, 10, 10)
        group_box3.setLayout(grid_layout3)

        label = QLabel(f"鼠笼 {group_num}")
        slider = QSlider()
        slider.setOrientation(Qt.Orientation.Horizontal)
        slider.setMinimum(1)
        slider.setMaximum(9)

        saved_slider_value = saved_config.get('config_1_value_0', 1)
        try:
            slider.setValue(int(saved_slider_value))
        except (ValueError, TypeError):
            slider.setValue(1)

        current_value_label = QLabel(f"当前值: {slider.value()}")

        slider.valueChanged.connect(
            lambda value, label=current_value_label: self.update_slider_label(value, label)
        )
        slider.sliderReleased.connect(
            lambda address=module_value['address'], mouse_cage_number=group_num,
                   function_code=module_value['config'][1]['function_code'],
                   data_lists=module_value['config'][1]['value'][0]['refer_value'],
                   slider=slider, config_key='ENM_config_1_value_0':
            self.update_slider(address, mouse_cage_number, function_code, data_lists, slider, config_key)
        )

        grid_layout3.addWidget(label, 0, 0)
        grid_layout3.addWidget(slider, 0, 1)
        grid_layout3.addWidget(current_value_label, 0, 2)

        # 第4个 GroupBox（滑块）
        group_box4 = QGroupBox(
            f"{module_value['desc']}-{module_value['config'][1]['value'][1]['desc']}"
        )
        group_box4.setContentsMargins(10, 10, 10, 10)
        scroll_area_layout.addWidget(group_box4)

        grid_layout4 = QGridLayout()
        grid_layout4.setContentsMargins(10, 30, 10, 10)
        group_box4.setLayout(grid_layout4)

        label = QLabel(f"鼠笼 {group_num}")
        slider = QSlider()
        slider.setOrientation(Qt.Orientation.Horizontal)
        slider.setMinimum(1)
        slider.setMaximum(9)

        saved_slider_value = saved_config.get('config_1_value_1', 1)
        try:
            slider.setValue(int(saved_slider_value))
        except (ValueError, TypeError):
            slider.setValue(1)

        current_value_label = QLabel(f"当前值: {slider.value()}")

        slider.valueChanged.connect(
            lambda value, label=current_value_label: self.update_slider_label(value, label)
        )
        slider.sliderReleased.connect(
            lambda address=module_value['address'], mouse_cage_number=group_num,
                   function_code=module_value['config'][1]['function_code'],
                   data_lists=module_value['config'][1]['value'][1]['refer_value'],
                   slider=slider, config_key='ENM_config_1_value_1':
            self.update_slider(address, mouse_cage_number, function_code, data_lists, slider, config_key)
        )

        grid_layout4.addWidget(label, 0, 0)
        grid_layout4.addWidget(slider, 0, 1)
        grid_layout4.addWidget(current_value_label, 0, 2)

    # ==========事件处理==========
    def changeEvent(self, event: QEvent):
        """窗口状态改变事件"""
        if event.type() == QEvent.Type.ActivationChange:
            if self.isActiveWindow():
                if self.calibration_checkbox is not None:
                    self.calibration_checkbox.setChecked(self.get_startup_calibration_mode() != "none")
                self.calibration_selected = global_setting.get_setting("device_config_calibration_selected", False)
                self.update_calibration_button_text()
                self.update_device_config_button_state()

        super().changeEvent(event)

    def showEvent(self, a0: typing.Optional[QtGui.QShowEvent]) -> None:
        """窗口显示事件"""
        logger.warning("tab1——show")

        if self.calibration_checkbox is not None:
            self.calibration_checkbox.setChecked(self.get_startup_calibration_mode() != "none")
        self.calibration_selected = global_setting.get_setting("device_config_calibration_selected", False)
        self.update_calibration_button_text()
        self.update_device_config_button_state()

        if not hasattr(self, '_first_show_done'):
            self._init_customize_ui()
            self._first_show_done = True

        super().showEvent(a0)

    def hideEvent(self, a0: typing.Optional[QtGui.QHideEvent]) -> None:
        """窗口隐藏事件"""
        logger.warning("tab1--hide")
        super().hideEvent(a0)

    def on_port_selection_changed(self, index):
        """端口下拉框选择变化"""
        try:
            if index < 0 or index >= len(self.ports):
                logger.warning(f"无效的端口索引: {index}")
                return

            self.send_message['port'] = self.ports[index]['device']
            logger.info(
                f"{time_util.get_format_from_time(time.time())}- 设备: {self.ports[index]['device']} "
                f"({self.ports[index]['description']}) - 待用户确认"
            )
        except Exception as e:
            logger.error(e)

    def _notify_main_monitor_data_set_port(self, show_error=False):
        """通知 main_monitor_data 更新当前串口"""
        send_message_queue = global_setting.get_setting("send_message_queue", None)
        if send_message_queue is None:
            logger.error("send_message_queue 未找到，无法同步串口到 main_monitor_data")
            if show_error:
                self.show_warning("错误", "主监测进程未就绪，无法同步串口，请重启应用后重试。")
            return False

        send_message_queue.put(ObjectQueueItem(
            origin='New_main_experiment_setting', to='main_monitor_data', title='set_port',
            data=self.send_message['port'],
            time=time_util.get_format_from_time(time.time())
        ))
        return True

    def _on_cage_clicked(self, item):
        """
        鼠笼列表项被点击时触发
        只有笼内模块检测通过的笼子才能被选中进行配置
        """
        try:
            if not item:
                return

            group_id = item.data(Qt.ItemDataRole.UserRole)

            mouse_cage_detect_dict = global_setting.get_setting("mouse_cage_detect_state_dict", {})

            if group_id not in mouse_cage_detect_dict:
                logger.warning(f"笼子 {group_id} 未在检测字典中")
                return

            cage_data = mouse_cage_detect_dict[group_id]
            cage_is_valid = cage_data.get('cage_is_valid', False)

            if not cage_is_valid:
                logger.warning(f"笼子 {group_id} 笼内模块检测未通过，不能配置")
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                return

            # logger.info(f"✓ 笼子 {group_id} 笼内模块检测通过，允许配置")
            self.load_cage_config(group_id)

            if self.right_title:
                group = self.cage_enabled_status.get(group_id)
                if group:
                    self.right_title.setText(f"配置: 鼠笼 {group_id} - {group.name}")

        except Exception as e:
            logger.error(f"处理笼子点击出错: {e}", exc_info=True)

    # ========== 气路检测流程（修改版 - 改用信号槽） ==========
    def confirm_port(self):
        """
        确认串口并启动气路检测（完全修复版）
        """
        if not self.port_combox or self.port_combox.currentIndex() < 0:
            self.show_warning("错误", "请先选择有效的串口")
            return False

        previous_port = global_setting.get_setting("port", None)
        self.send_message['port'] = self.ports[self.port_combox.currentIndex()]['device']
        global_setting.set_setting("port", self.send_message['port'])
        self.port_confirmed = True
        self.port_combox.setEnabled(False)
        if self.confirm_port_btn:
            self.confirm_port_btn.setEnabled(False)
            self.confirm_port_btn.setText("串口已确认")

        refresh_port_btn = self.findChild(QPushButton, "tab_1_refresh_port_btn")
        if refresh_port_btn:
            refresh_port_btn.setEnabled(False)

        if self.config_btn:
            self.config_btn.setEnabled(True)
        if self.start_btn:
            self.start_btn.setEnabled(True)

        try:
            started = self._start_detection_cycle()
        except Exception:
            started = False
            logger.exception("确认串口后启动设备检测失败")
        if not started:
            self.port_confirmed = False
            self.detection_in_progress = False
            global_setting.set_setting("port", previous_port)
            self.port_combox.setEnabled(True)
            if self.confirm_port_btn:
                self.confirm_port_btn.setEnabled(True)
                self.confirm_port_btn.setText("确认串口")
            if refresh_port_btn:
                refresh_port_btn.setEnabled(True)
            self.update_device_config_button_state()
            return False
        global_setting.set_setting("confirmed_serial_setting", self.experiment_setting)
        global_setting.set_setting("confirmed_serial_port", self.send_message['port'])
        return True

    def refresh_detection(self):
        """在已确认串口的前提下，重新执行一轮完整检测。"""
        if not self.port_confirmed:
            self.show_warning("提示", "请先确认串口，再执行刷新检测。")
            return

        if self.detection_in_progress or not self._air_detection_finished or not self._cage_detection_finished:
            self.show_warning("提示", "当前检测尚未结束，请等待本轮检测完成后再刷新。")
            return

        if not self.port_combox or self.port_combox.currentIndex() < 0:
            self.show_warning("错误", "当前串口无效，请重新选择串口。")
            return

        self.send_message['port'] = self.ports[self.port_combox.currentIndex()]['device']
        self._start_detection_cycle()

    def not_each_Mouse_Cage_detect_update_state(self, state_data):
        """修复版 - 确保立即发射信号"""
        try:
            if not self._is_current_detection_session(state_data):
                return

            module_name = state_data.get('module_name', '')

            # ==================== 判定有效性 ====================
            module_is_valid = state_data.get('response_state', False)

            # logger.critical(f"[判定] {module_name}: {'✓ 有效' if module_is_valid else '✗ 无效'}")

            with self.air_module_detection_lock:
                if self._air_detection_finished:
                    # logger.debug(f"气路检测已结束，忽略 {module_name}")
                    return

                if self.air_modules_completed.get(module_name, False):
                    # logger.debug(f"{module_name} 已处理过")
                    return

                # 用 emit 发射信号！
                # logger.info(f"📡 [发射信号] {module_name}: {module_is_valid}")
                self.signal_air_module_update.emit(module_name, module_is_valid)  # ← 这里改成 emit

                self.air_modules_completed[module_name] = True
                self.air_modules_valid[module_name] = module_is_valid
                # UFC/UGC/ZOS are shared by all enabled cages.  Record the
                # single physical check on each cage without probing again.
                detection = global_setting.get_setting("mouse_cage_detect_state_dict", {}) or {}
                for cage_id in self.cage_list_to_detect:
                    cage_state = detection.get(int(cage_id))
                    if isinstance(cage_state, dict):
                        cage_state.setdefault("air_modules", {})[module_name] = bool(module_is_valid)
                global_setting.set_setting("mouse_cage_detect_state_dict", detection)

                received_count = sum(1 for v in self.air_modules_completed.values() if v)
                total_count = len(self.air_modules_to_detect)

                if received_count >= total_count:
                    # 所有齐了，触发结算
                    self._process_air_detection_final_results()

        except Exception as e:
            logger.error(f"异常: {e}", exc_info=True)

    def _process_air_detection_final_results(self):
        """
        气路检测结算
        在这里才是发射信号更新UI的地方
        """
        try:
            with self.air_module_detection_lock:
                # 防重复检查
                if self._air_detection_finished:
                    # logger.debug("[防重复] 已结算过，直接返回")
                    return

                # 标记为已完成
                self._air_detection_finished = True
                self.air_detection_complete_event.set()

                # logger.critical("[结算] 气路检测结算开始")

                # 创建快照
                air_modules_valid_snapshot = dict(self.air_modules_valid)
                air_modules_completed_snapshot = dict(self.air_modules_completed)

                # 补全缺失的键
                for module_name in ['UFC', 'UGC', 'ZOS']:
                    if module_name not in air_modules_valid_snapshot:
                        air_modules_valid_snapshot[module_name] = False
                    if module_name not in air_modules_completed_snapshot:
                        air_modules_completed_snapshot[module_name] = False

            # ==================== 统计结果 ====================
            final_valid_list = []
            final_invalid_list = []
            final_no_response_list = []

            for module_name in self.air_modules_to_detect:
                has_responded = air_modules_completed_snapshot.get(module_name, False)
                is_valid = air_modules_valid_snapshot.get(module_name, False)

                if not has_responded:
                    logger.warning(f"✗ [未响应] 模块 {module_name}")
                    # 发射信号更新UI - 无效
                    self.signal_air_module_update.emit(module_name, False)
                    final_no_response_list.append(module_name)
                elif is_valid:
                    # logger.info(f"✓ [有效] 模块 {module_name}")
                    # 发射信号更新UI - 有效
                    self.signal_air_module_update.emit(module_name, True)
                    final_valid_list.append(module_name)
                else:
                    # logger.warning(f"[无效] 模块 {module_name}")
                    # 发射信号更新UI - 无效
                    self.signal_air_module_update.emit(module_name, False)
                    final_invalid_list.append(module_name)

            detection = global_setting.get_setting("mouse_cage_detect_state_dict", {}) or {}
            for cage_id in self.cage_list_to_detect:
                cage_state = detection.get(int(cage_id))
                if isinstance(cage_state, dict):
                    shared = cage_state.setdefault("air_modules", {})
                    for module_name in self.air_modules_to_detect:
                        shared[module_name] = bool(air_modules_valid_snapshot[module_name])
            global_setting.set_setting("mouse_cage_detect_state_dict", detection)

            # ==================== 缓存结果 ====================
            self._air_detection_final_result_cached = {
                'valid': final_valid_list,
                'invalid': final_invalid_list,
                'no_response': final_no_response_list
            }

            all_air_modules_valid = (
                set(final_valid_list) >= self.required_air_modules and
                not final_invalid_list and
                not final_no_response_list
            )
            global_setting.set_setting("air_modules_all_valid", all_air_modules_valid)
            if self.main_gui is not None:
                self.main_gui.change_enable_component_app_state_signal.emit()

            logger.critical(
                f"\n{'=' * 80}\n"
                f"[气路检测完成]\n"
                f"有效模块: {final_valid_list}\n"
                f"无效模块: {final_invalid_list}\n"
                f"未响应: {final_no_response_list}\n"
                f"{'=' * 80}\n"
            )

            # ==================== 【最后】更新总体检测状态 ====================
            status_text = "✓ 检测完成，可选择笼子进行配置" if self._cage_detection_finished else "✓ 气路检测完成，继续检测鼠笼模块..."
            self.signal_detection_status_update.emit(status_text)
            self._update_refresh_detection_button_state()

        except Exception as e:
            logger.error(f"[结算异常] {e}", exc_info=True)
            with self.air_module_detection_lock:
                self._air_detection_finished = True
            self._update_refresh_detection_button_state()

    # ========== 笼内检测流程 ==========
    def _detect_next_cage(self):
        """
        检测笼内模块（修复版：完全分离索引推进逻辑）
        确保笼号正确传递给报文
        """
        try:
            # ==================== 1. 边界检查 ====================
            if self.current_detecting_index >= len(self.cage_list_to_detect):
                # logger.critical(
                #     f"\n{'=' * 80}\n"
                #     f"[检测完成] 所有 {len(self.cage_list_to_detect)} 个笼子已检测完毕\n"
                #     f"{'=' * 80}\n"
                # )
                self._cleanup_all_timers()
                self.detection_in_progress = False
                self._cage_detection_finished = True

                if self.detection_status_label:
                    status_text = "✓ 检测完成，可选择笼子进行配置" if self._air_detection_finished else "✓ 鼠笼检测完成，等待气路结果..."
                    self.detection_status_label.setText(status_text)

                self.device_config_ready = True
                self.update_device_config_button_state()
                self._update_refresh_detection_button_state()
                return

            # ==================== 2. 获取当前笼子（修复：确保类型一致） ====================
            cage_number = int(self.cage_list_to_detect[self.current_detecting_index])

            # logger.critical(
            #     f"\n{'=' * 80}\n"
            #     f"[开始检测笼子] {cage_number}\n"
            #     f"索引: {self.current_detecting_index}/{len(self.cage_list_to_detect) - 1}\n"
            #     f"笼子列表: {self.cage_list_to_detect}\n"
            #     f"{'=' * 80}\n"
            # )

            # ==================== 3. 防重复检测 ====================
            if self._completed_cages.get(cage_number, False) is True:
                logger.warning(f"笼子 {cage_number} 已处理完成，跳过")
                return

            # ==================== 4. 标记为检测中并更新UI ====================
            self._update_cage_detecting(cage_number)

            # ==================== 5. 发送检测报文 ====================
            send_message_queue = global_setting.get_setting("send_message_queue", None)

            if not send_message_queue:
                logger.error(f"send_message_queue 未找到")
                self._completed_cages[cage_number] = True
                return

            try:
                # 确保笼号正确传入
                detect_item = ObjectQueueItem(
                    origin="New_main_experiment_setting",
                    to="main_monitor_data",
                    title="detect_cage_modules_only",
                    data={
                        'gids': [cage_number],  # 单个笼子的笼号
                        'cage_index': cage_number,  # 笼子索引（与gids一致）
                        'detection_session_id': self.current_detection_session_id
                    },
                    time=time_util.get_format_from_time(time.time())
                )
                send_message_queue.put(detect_item)

                # logger.critical(
                #     f"笼子 {cage_number} 检测报文已发送\n"
                #     f"报文内容: gids=[{cage_number}], cage_index={cage_number}"
                # )

            except Exception as queue_error:
                logger.error(f"笼子 {cage_number} 检测报文发送异常: {queue_error}")
                self._completed_cages[cage_number] = True
                return

            # ==================== 6. 设置超时（15秒） ====================
            self._set_cage_detection_timeout(cage_number, timeout_seconds=15)

        except Exception as e:
            logger.error(f"笼内检测流程异常: {e}", exc_info=True)

    def _set_cage_detection_timeout(self, cage_number, timeout_seconds=10):
        """为单个笼子设置检测超时"""
        if cage_number in self.cage_detection_timers:
            self.cage_detection_timers[cage_number].stop()

        timer = QTimer()
        timer.setSingleShot(True)
        timer.timeout.connect(lambda: self._on_cage_detection_timeout(cage_number))
        timer.start(timeout_seconds * 1000)

        self.cage_detection_timers[cage_number] = timer
        # logger.debug(f"✓ 笼 {cage_number} 的 {timeout_seconds}秒检测超时已设置")

    def _on_cage_detection_timeout(self, cage_number):
        """笼子检测超时处理（修复版：确保索引稳定推进）"""
        try:
            cage_number_int = int(cage_number)

            # 清理该笼子的计时器
            if cage_number_int in self.cage_detection_timers:
                self.cage_detection_timers.pop(cage_number_int)

            # ==================== 防止重复处理 ====================
            if self._completed_cages.get(cage_number_int, False) is True:
                logger.warning(f"笼子 {cage_number_int} 已处理过，跳过超时处理")
                return

            # logger.warning(
            #     f"\n{'=' * 80}\n"
            #     f"[超时] 笼子 {cage_number_int} 检测超时（15秒）\n"
            #     f"{'=' * 80}\n"
            # )

            # ==================== 标记完成 ====================
            self._completed_cages[cage_number_int] = True
            detection = global_setting.get_setting("mouse_cage_detect_state_dict", {}) or {}
            if isinstance(detection.get(cage_number_int), dict):
                detection[cage_number_int]["detection_complete"] = True
                global_setting.set_setting("mouse_cage_detect_state_dict", detection)
            self._update_cage_detection_complete(cage_number_int)

            # ==================== 【关键】推进索引到下一个笼子 ====================
            prev_index = self.current_detecting_index
            self.current_detecting_index += 1

            # logger.critical(
            #     f"索引推进: {prev_index} → {self.current_detecting_index}\n"
            #     f"当前完成笼子: {cage_number_int}\n"
            #     f"下一个将检测: {self.cage_list_to_detect[self.current_detecting_index] if self.current_detecting_index < len(self.cage_list_to_detect) else '无'}\n"
            # )

            # ==================== 触发下一个笼子检测 ====================
            QTimer.singleShot(300, self._detect_next_cage)

        except Exception as e:
            logger.error(f"处理笼子 {cage_number} 超时异常: {e}", exc_info=True)
            # 异常兜底
            try:
                self.current_detecting_index += 1
            except:
                pass
            QTimer.singleShot(300, self._detect_next_cage)

    def _update_cage_detecting(self, cage_number):
        """更新笼子为检测中状态"""
        try:
            cage_number_int = int(cage_number)
            cage_list_widget = self._get_cage_list_widget()
            if not cage_list_widget:
                return

            for i in range(cage_list_widget.count()):
                item = cage_list_widget.item(i)
                if not item:
                    continue

                item_cage_id = int(item.data(Qt.ItemDataRole.UserRole))
                if item_cage_id != cage_number_int:
                    continue

                item_text = f"鼠笼 {cage_number_int} - 检测中..."
                item.setText(item_text)
                item.setFlags(Qt.ItemFlag.NoItemFlags)
                item.setBackground(QtGui.QColor(255, 255, 240))
                item.setForeground(QtGui.QColor(184, 134, 11))

                cage_list_widget.viewport().update()
                # logger.debug(f"✓ 笼子 {cage_number_int} UI已更新为检测中")
                break

        except Exception as e:
            logger.error(f"更新笼子 {cage_number} 为检测中状态失败: {e}", exc_info=True)

    def _cleanup_all_timers(self):
        """清理所有计时器"""
        for cage_num in list(self.cage_detection_timers.keys()):
            timer = self.cage_detection_timers.pop(cage_num)
            if timer and timer.isActive():
                timer.stop()

    # ========== 状态更新回调 ==========

    def each_Mouse_Cage_detect_update_state(self, state_data):
        """
        更新鼠笼内模块检测状态

        只负责收集数据，不负责推进索引
        """
        try:
            if not self._is_current_detection_session(state_data):
                return

            mouse_cage_number = state_data.get('mouse_cage_number')
            module_name = state_data.get('module_name', 'UNKNOWN')
            module_is_valid = state_data.get('response_state', False)

            if mouse_cage_number is None:
                logger.warning(f"收到缺少鼠笼号的检测结果: {state_data}")
                return

            if self._completed_cages.get(int(mouse_cage_number), False):
                logger.warning(f"忽略笼 {mouse_cage_number} 的迟到检测结果: {module_name}")
                return

            # logger.critical(
            #     f"[笼内模块] 笼 {mouse_cage_number} - {module_name}: "
            #     f"{'✓ 有效' if module_is_valid else '✗ 无效'}"
            # )

            # ==================== 更新全局检测字典 ====================
            mouse_cage_detect_dict = global_setting.get_setting("mouse_cage_detect_state_dict", {})

            if mouse_cage_number not in mouse_cage_detect_dict:
                mouse_cage_detect_dict[mouse_cage_number] = {
                    'cage_modules': {},
                    'air_modules': {},
                    'cage_is_valid': False,
                    'update_time': time_util.get_format_from_time(time.time())
                }

            # ==================== 记录模块状态 ====================
            mouse_cage_detect_dict[mouse_cage_number]['cage_modules'][module_name] = module_is_valid
            mouse_cage_detect_dict[mouse_cage_number]['update_time'] = time_util.get_format_from_time(time.time())

            cage_modules = mouse_cage_detect_dict[mouse_cage_number]['cage_modules']
            required_modules = self._required_cage_modules()

            # ==================== 检查笼子是否完整 ====================
            all_received = required_modules.issubset(set(cage_modules.keys()))
            all_valid = all(cage_modules.get(name, False) for name in required_modules)
            cage_is_valid = all_received and all_valid

            mouse_cage_detect_dict[mouse_cage_number]['cage_is_valid'] = cage_is_valid

            logger.critical(
                f"笼 {mouse_cage_number} 笼内模块统计:\n"
                f"  已收到: {list(cage_modules.keys())}\n"
                f"  模块状态: {dict([(k, v) for k, v in cage_modules.items()])}\n"
                f"  当前收到: {all_received} | 全部有效: {all_valid} | 最终判定: {cage_is_valid}"
            )

            global_setting.set_setting("mouse_cage_detect_state_dict", mouse_cage_detect_dict)

            # ==================== 实时更新UI ====================
            self._update_cage_list_display(mouse_cage_number, mouse_cage_detect_dict[mouse_cage_number])

            # ==================== 【重要】只在笼子完整时才触发完成 ====================
            if all_received:
                # 这里调用完成处理
                self._on_cage_complete(mouse_cage_number, cage_is_valid)

        except Exception as e:
            logger.error(f"更新笼内模块状态异常: {e}", exc_info=True)

    def _on_cage_complete(self, cage_number, cage_is_valid):
        """
        笼子检测完成处理（完全修复版）
        """
        if threading.current_thread() != threading.main_thread():
            QTimer.singleShot(0, lambda: self._on_cage_complete(cage_number, cage_is_valid))
            return

        try:
            cage_number_int = int(cage_number)

            # logger.critical(
            #     f"\n{'=' * 80}\n"
            #     f"[笼子完成] {cage_number_int}\n"
            #     f"有效性: {'✓ 通过' if cage_is_valid else '✗ 失败'}\n"
            #     f"当前索引: {self.current_detecting_index}\n"
            #     f"笼子列表: {self.cage_list_to_detect}\n"
            #     f"{'=' * 80}\n"
            # )

            # ==================== 1. 停止计时器 ====================
            if cage_number_int in self.cage_detection_timers:
                timer = self.cage_detection_timers.pop(cage_number_int)
                timer.stop()
                timer.deleteLater()
                # logger.debug(f"✓ 笼 {cage_number_int} 的超时计时器已停止")

            # ==================== 2. 防止重复处理 ====================
            if self._completed_cages.get(cage_number_int, False) is True:
                logger.warning(f"笼子 {cage_number_int} 已处理过，跳过")
                return

            # ==================== 3. 标记为已完成 ====================
            self._completed_cages[cage_number_int] = True
            detection = global_setting.get_setting("mouse_cage_detect_state_dict", {}) or {}
            if isinstance(detection.get(cage_number_int), dict):
                detection[cage_number_int]["detection_complete"] = True
                global_setting.set_setting("mouse_cage_detect_state_dict", detection)
            # logger.debug(f"✓ 笼子 {cage_number_int} 标记为已完成")

            # ==================== 4. 更新UI显示最终状态 ====================
            self._update_cage_detection_complete(cage_number_int)

            # ==================== 【关键】5. 推进索引 ====================
            prev_index = self.current_detecting_index
            self.current_detecting_index += 1

            # logger.critical(
            #     f"[索引推进]\n"
            #     f"之前: {prev_index}\n"
            #     f"现在: {self.current_detecting_index}\n"
            #     f"下一个笼子: {self.cage_list_to_detect[self.current_detecting_index] if self.current_detecting_index < len(self.cage_list_to_detect) else '无'}\n"
            # )

            # ==================== 【关键】6. 触发下一个笼子检测 ====================
            QTimer.singleShot(2000, self._detect_next_cage)

        except Exception as e:
            logger.error(f"笼子 {cage_number} 完成处理异常: {e}", exc_info=True)
            try:
                # 异常兜底：确保索引推进
                self.current_detecting_index += 1
                logger.warning(f"异常兜底：索引已推进到 {self.current_detecting_index}")
                QTimer.singleShot(2000, self._detect_next_cage)
            except:
                pass

    def _update_cage_list_display(self, cage_number, cage_data):
        """实时更新笼子列表显示 - 每收到一个模块就更新"""
        try:
            cage_list_widget = self._get_cage_list_widget()
            if not cage_list_widget:
                return

            for i in range(cage_list_widget.count()):
                item = cage_list_widget.item(i)
                if not item or item.data(Qt.ItemDataRole.UserRole) != cage_number:
                    continue

                cage_modules = cage_data.get('cage_modules', {})
                cage_is_valid = cage_data.get('cage_is_valid', False)
                air_modules = cage_data.get('air_modules', {})

                required_cage_modules = self._required_cage_modules()
                required_count = len(required_cage_modules)
                received_count = len(cage_modules)
                missing_modules = required_cage_modules - set(cage_modules.keys())
                failed_modules = [name for name, status in cage_modules.items() if not status]

                group = self.cage_enabled_status.get(cage_number)
                group_name = f"[{group.name}]" if group else ""

                # ==================== 修复逻辑 ====================

                # 情况1：所有4个模块都收到了，且全部有效
                if received_count >= required_count and all(
                    cage_modules.get(name, False) for name in required_cage_modules
                ):
                    status_text = "✓ 检测完成（可配置）"
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    item.setBackground(QtGui.QColor(240, 255, 240))
                    item.setForeground(QtGui.QColor(34, 139, 34))
                    # logger.info(f"笼 {cage_number} 检测通过")

                # 情况2：所有4个模块都收到了，但有无效的
                elif received_count >= required_count and not all(
                    cage_modules.get(name, False) for name in required_cage_modules
                ):
                    failed_str = ", ".join(sorted(failed_modules))
                    status_text = f"✗ 检测异常 - 失败模块: {failed_str}"
                    item.setFlags(Qt.ItemFlag.NoItemFlags)
                    item.setBackground(QtGui.QColor(255, 240, 245))
                    item.setForeground(QtGui.QColor(220, 20, 60))
                    logger.error(f"笼{cage_number}检测失败: {failed_str}")

                # 情况3：还在检测中（少于4个或有缺失）
                elif received_count > 0:
                    modules_str = ", ".join(sorted(cage_modules.keys()))
                    status_text = f"检测中... ({received_count}/{required_count} 模块: {modules_str})"
                    item.setFlags(Qt.ItemFlag.NoItemFlags)
                    item.setBackground(QtGui.QColor(255, 255, 240))
                    item.setForeground(QtGui.QColor(184, 134, 11))
                    # logger.debug(f"笼 {cage_number} 检测中: {status_text}")

                # 情况4：还未收到任何模块
                else:
                    status_text = "检测中..."
                    item.setFlags(Qt.ItemFlag.NoItemFlags)
                    item.setBackground(QtGui.QColor(255, 255, 240))
                    item.setForeground(QtGui.QColor(184, 134, 11))
                    # logger.debug(f"笼 {cage_number} 等待模块响应")

                item_text = f"鼠笼 {cage_number} {group_name} - {status_text}"
                item.setText(item_text)

                cage_list_widget.viewport().update()
                cage_list_widget.repaint()
                break

        except Exception as e:
            logger.error(f"更新笼 {cage_number} 列表显示失败: {e}", exc_info=True)

    def _update_cage_detection_complete(self, cage_number):
        """更新笼子UI为最终检测完成状态"""
        try:
            cage_list_widget = self._get_cage_list_widget()
            if not cage_list_widget:
                return

            for i in range(cage_list_widget.count()):
                item = cage_list_widget.item(i)
                if not item or item.data(Qt.ItemDataRole.UserRole) != cage_number:
                    continue

                group = self.cage_enabled_status.get(cage_number)
                if not group:
                    break

                mouse_cage_detect_dict = global_setting.get_setting("mouse_cage_detect_state_dict", {})
                cage_data = mouse_cage_detect_dict.get(cage_number, {})

                cage_modules = cage_data.get('cage_modules', {})
                cage_is_valid = cage_data.get('cage_is_valid', False)

                group_name = f"[{group.name}]" if group else ""

                if cage_is_valid and len(cage_modules) >= len(self._required_cage_modules()):
                    status_text = "✓ 检测完成（可配置）"
                    item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsSelectable)
                    item.setBackground(QtGui.QColor(240, 255, 240))
                    item.setForeground(QtGui.QColor(34, 139, 34))
                    # for j,k in self.module_status_labels.items():
                    #     logger.critical(f"{j}:{k.text()}:{k}")
                    # logger.critical(f"笼 {cage_number} 最终状态：通过")

                else:
                    received_modules = list(cage_modules.keys())
                    failed_modules = [name for name, status in cage_modules.items() if not status]

                    if received_modules:
                        if failed_modules:
                            failed_str = ", ".join(sorted(failed_modules))
                            status_text = f"✗ 检测异常 - 失败模块: {failed_str}"
                        else:
                            received_str = ", ".join(sorted(received_modules))
                            missing = self._required_cage_modules() - set(received_modules)
                            missing_str = ", ".join(sorted(missing))
                            status_text = f"检测不完整 - 已收到: {received_str}, 缺少: {missing_str}"
                    else:
                        status_text = f"✗ 检测失败 - 无响应"

                    item.setFlags(Qt.ItemFlag.NoItemFlags)
                    item.setBackground(QtGui.QColor(255, 240, 245))
                    item.setForeground(QtGui.QColor(220, 20, 60))
                    logger.error(f"笼 {cage_number} 最终状态：失败 - {status_text}")

                item_text = f"鼠笼 {cage_number} {group_name} - {status_text}"
                item.setText(item_text)

                cage_list_widget.viewport().update()
                cage_list_widget.repaint()
                break

        except Exception as e:
            logger.error(f"更新笼 {cage_number} 最终状态失败: {e}", exc_info=True)

    # ==========配置管理==========
    def _get_cage_config_path(self, cage_id: int) -> Path:
        """获取笼子配置文件路径"""
        return self.user_config_dir / f"cage_{cage_id}_config.json"

    def _save_cage_config_to_json(self, cage_id: int, config_data: dict) -> bool:
        """保存笼子配置到用户本地JSON文件"""
        try:
            config_data['timestamp'] = time_util.get_format_from_time(time.time())
            config_data['cage_id'] = cage_id

            config_path = self._get_cage_config_path(cage_id)
            self.user_config_dir.mkdir(parents=True, exist_ok=True)

            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(config_data, f, indent=2, ensure_ascii=False)

            # logger.info(f"✓ 笼子 {cage_id} 配置已保存到: {config_path}")
            return True
        except Exception as e:
            logger.error(f"保存笼子 {cage_id} 配置失败: {e}")
            return False

    def _load_cage_config_from_json(self, cage_id: int) -> dict:
        """从用户本地JSON文件加载笼子配置"""
        try:
            config_path = self._get_cage_config_path(cage_id)

            if not config_path.exists():
                # logger.debug(f"笼子 {cage_id} 配置文件不存在，使用默认配置")
                return {}

            with open(config_path, 'r', encoding='utf-8') as f:
                config_data = json.load(f)

            # logger.info(f"✓ 笼子 {cage_id} 配置已从本地加载")
            return config_data
        except Exception as e:
            logger.error(f"加载笼子 {cage_id} 配置失败: {e}")
            return {}

    def load_cage_config(self, group_num):
        """加载指定鼠笼的配置界面"""
        try:
            if self.content_layout is None:
                logger.error("content_layout 未找到！")
                return

            self.remove_layout_items(self.content_layout)

            self.init_basic_config(self.content_layout)

            saved_config = self._load_cage_config_from_json(group_num)
            self.current_cage_config = saved_config.copy()

            if saved_config:
                pass
                # logger.info(f"✓ 已加载笼子 {group_num} 的本地保存配置")
            else:
                pass
                # logger.info(f"笼子 {group_num} 首次配置，将创建新配置文件")

            for module_key, module_value in self.config.items():
                if module_key == Modbus_Type.Modbus_Slave_Ids.ENM.value['name']:
                    module_config = saved_config.get('ENM', {})
                    self.init_enm_config_ui_for_group(module_key, module_value, self.content_layout, group_num,
                                                      module_config)
                elif (
                    not self._environment_module_only_enabled()
                    and module_key == Modbus_Type.Modbus_Slave_Ids.EM.value['name']
                ):
                    module_config = saved_config.get('EM', {})
                    self.init_em_config_ui_for_group(module_key, module_value, self.content_layout, group_num,
                                                     module_config)

            self.content_layout.addStretch()

        except Exception as e:
            logger.error(f"加载笼子配置出错: {e}", exc_info=True)

    def on_radio_button_clicked(self, button, address, mouse_cage_number, function_code, data_lists, config_key=''):
        """处理按钮点击事件"""
        btn_object_name: str = button.objectName()
        data_list = ['00', '00', '00', '00']

        if "on" in btn_object_name.lower():
            data_list = [hex_str[2:] for hex_str in data_lists["0"]['value']]
            state_value = 'on'
        elif "off" in btn_object_name.lower():
            data_list = [hex_str[2:] for hex_str in data_lists["1"]['value']]
            state_value = 'off'

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

        if config_key:
            if config_key.startswith('ENM_'):
                module_type = 'ENM'
                config_key_clean = config_key.replace('ENM_', '')
            elif config_key.startswith('EM_'):
                module_type = 'EM'
                config_key_clean = config_key.replace('EM_', '')
            else:
                module_type = 'UNKNOWN'
                config_key_clean = config_key

            if module_type not in self.current_cage_config:
                self.current_cage_config[module_type] = {}

            self.current_cage_config[module_type][config_key_clean] = state_value
            self._save_cage_config_to_json(mouse_cage_number, self.current_cage_config)

        self.send_message['data'] = data_list
        if mouse_cage_number == 0:
            self.send_message['slave_id'] = format(address, '02X')
        else:
            self.send_message['slave_id'] = format(address + mouse_cage_number * 16, '02X')
        self.send_message['function_code'] = format(function_code, '02X')

        self.send_data()

    def update_slider(self, address, mouse_cage_number, function_code, data_lists, slider: QSlider, config_key=''):
        """更新滑块值"""
        value = slider.value()
        data_list = ['00', '00', '00', '00']

        if str(value) in data_lists:
            data_list = [hex_str[2:] for hex_str in data_lists[str(value)]['value']]

        if config_key:
            if config_key.startswith('ENM_'):
                module_type = 'ENM'
                config_key_clean = config_key.replace('ENM_', '')
            elif config_key.startswith('EM_'):
                module_type = 'EM'
                config_key_clean = config_key.replace('EM_', '')
            else:
                module_type = 'UNKNOWN'
                config_key_clean = config_key

            if module_type not in self.current_cage_config:
                self.current_cage_config[module_type] = {}

            self.current_cage_config[module_type][config_key_clean] = value
            self._save_cage_config_to_json(mouse_cage_number, self.current_cage_config)

        self.send_message['data'] = data_list
        if mouse_cage_number == 0:
            self.send_message['slave_id'] = format(address, '02X')
        else:
            self.send_message['slave_id'] = format(address + mouse_cage_number * 16, '02X')
        self.send_message['function_code'] = format(function_code, '02X')

        self.send_data()
        # logger.info(f"✓ 笼子 {mouse_cage_number} 滑块配置已保存: {config_key}={value}")

    def update_slider_label(self, value, label):
        """更新滑块标签"""
        label.setText(f"当前值: {value}")

    # ==========数据通信==========
    def send_data(self):
        """发送数据"""
        if not self.port_confirmed:
            self.show_warning("提示", "请先确认串口，再进行笼子配置。")
            return

        if not self.send_message.get('port'):
            self.show_warning("错误", "当前未选择有效串口，请重新选择后再试。")
            return

        send_message_queue = global_setting.get_setting("send_message_queue", None)
        if send_message_queue is None:
            logger.error("send_message_queue 未找到，无法发送配置命令")
            self.show_warning("错误", "主监测进程未启动，无法发送配置命令，请重启应用后重试。")
            return

        send_message = copy.deepcopy(self.send_message)
        send_message["no_response"] = True
        queue_message = {"message": send_message}
        message_struct = ObjectQueueItem(
            to="main_monitor_data",
            data=queue_message,
            origin='Tab_1'
        )
        send_message_queue.put(message_struct)
        # logger.debug(f"Tab_1开始发送消息:{message_struct}")

    # ==========操作按钮==========
    def refresh_port(self):
        """重新获取端口"""
        self.ports = []
        self._init_data()
        self.init_port_combox()

    def start_device_config(self):
        """开始设备配置"""
        # if not self.device_config_ready:
        #     self.show_warning("提示", "请先完成串口确认和设备检测。")
        #     return

        startup_calibration_mode = self.get_startup_calibration_mode()
        is_auto_calibration = startup_calibration_mode != "none"
        reply = QMessageBox.question(self, '确定设备配置',
                                     "确定该设备配置？",
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                     QMessageBox.StandardButton.No)
        if reply == QMessageBox.StandardButton.Yes:
            global_setting.set_setting("app_state", AppState.CONFIGURING)
            if self.main_gui is not None:
                self.main_gui.change_enable_component_app_state_signal.emit()

            send_message_queue = global_setting.get_setting("send_message_queue")
            send_message_queue.put(ObjectQueueItem(origin='tab_7', to='main_monitor_data', title='set_port',
                                                   data=self.send_message['port'],
                                                   time=time_util.get_format_from_time(time.time())))
            basic_experiment_config = {}
            basic_experiment_config["startup_calibration_mode"] = startup_calibration_mode
            basic_experiment_config["is_auto_calibration"] = is_auto_calibration
            global_setting.set_setting("startup_calibration_mode", startup_calibration_mode)
            global_setting.set_setting("is_auto_calibration", is_auto_calibration)
            vr_value = self.vr_desc_text.value()
            if vr_value:
                basic_experiment_config['vr_desc'] = float(vr_value)
                global_setting.set_setting("Vr", float(vr_value))

            span_oxygen_value = self.span_oxygen_desc_text.value()
            if span_oxygen_value:
                basic_experiment_config["span_standard_oxygen_value"] = float(span_oxygen_value)
                global_setting.set_setting("span_standard_oxygen_value", float(span_oxygen_value))

            span_carbon_value = self.span_carbon_desc_text.value()
            if span_carbon_value:
                basic_experiment_config["span_standard_carbon_value"] = float(span_carbon_value)
                global_setting.set_setting("span_standard_carbon_value", float(span_carbon_value))

            send_message_queue.put(
                ObjectQueueItem(origin='tab_7', to='main_monitor_data', title='set_experiment_basic_config',
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
        """停止实验"""
        if self.main_gui is not None:
            self.main_gui.stop_experiment()

    # ==========工具函数==========
    def _get_cage_list_widget(self):
        """安全获取 cage_list_widget"""
        try:
            if self.cage_list_widget is None:
                self.cage_list_widget = self.findChild(QListWidget, "cage_list_widget")
                if self.cage_list_widget is None:
                    logger.error("无法找到 cage_list_widget")
                    return None

            return self.cage_list_widget

        except (RuntimeError, AttributeError) as e:
            logger.error(f"获取 cage_list_widget 失败: {e}")
            self.cage_list_widget = None
            return None

    def remove_layout_items(self, layout):
        """清空布局中的所有控件"""
        while layout.count():
            child = layout.takeAt(0)
            if child.widget():
                child.widget().deleteLater()

    def show_warning(self, title: str, message: str):
        """显示警告对话框"""
        QMessageBox.warning(self, title, message)

    def show_success(self, title: str, message: str):
        """显示成功对话框"""
        QMessageBox.information(self, title, message)

    def closeEvent(self, event):
        """关闭时清除单例缓存，允许下次重新创建"""
        cls = type(self)
        instance_id = id(self)

        with SafeSingletonMeta._lock:
            if cls in SafeSingletonMeta._instances:
                del SafeSingletonMeta._instances[cls]
            if cls in SafeSingletonMeta._init_completed:
                del SafeSingletonMeta._init_completed[cls]

        with Tab_1._instance_lock:
            if instance_id in Tab_1._initialization_state:
                del Tab_1._initialization_state[instance_id]
            Tab_1._failed_instances.discard(instance_id)

        super().closeEvent(event)
