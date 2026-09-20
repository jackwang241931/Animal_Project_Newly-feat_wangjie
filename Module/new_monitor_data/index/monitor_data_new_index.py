import time
import typing

from PyQt6 import QtGui
from PyQt6.QtCore import QRect, QSize, Qt, QTimer
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QSizePolicy, QSplitter
from loguru import logger

from Module.new_monitor_data.ui.custom.table.Table_select_columns_paging_bottom import Table_select_columns_paging_bottom
from Module.new_monitor_data.ui.monitor_data_new import Ui_monitor_data_new
from Module.new_monitor_data.index.left_top_widget.monitor_data_windows import MonitorDataWindows
from Module.new_monitor_data.index.right_top_widget.table_column_check_list_view import Table_Column_check_list_view
from public.component.Guide_tutorial_interface.Tutorial_Manager import TutorialManager
from public.config_class.App_Setting import AppSettings
from public.config_class.global_setting import global_setting
from public.entity.MyQThread import MyQThread
from public.entity.enum.Public_Enum import Tutorial_Type
from public.entity.experiment_setting_entity import Experiment_setting_entity
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.util.time_util import time_util
from theme.ThemeQt6 import ThemedWindow
class read_queue_data_Thread(MyQThread):
    def __init__(self, name,window=None):
        super().__init__(name)
        self.queue = None
        self.window:Monitor_data_new_index = window
        pass

    def stop(self):

        super().stop()
    def dosomething(self):
        if not self.queue.empty():
            # logger.error(f"{self.queue.qsize()}")
            try:
                message: ObjectQueueItem = self.queue.get()
            except Exception as e:
                logger.error(f"{self.name}发生错误{e}")
                return
                # logger.error(f"{self.name}_get_message:{message}|")
            if message is not None and message.is_Empty():
                return
            if message is not None and isinstance(message, ObjectQueueItem) and message.to == 'monitor_data_new_index':
                # logger.error(f"{self.name}_get_message:{message}")
                match message.title:
                    case 'zero_calibration_finish':
                        """
                        零点标定结束
                        """
                        if self.window is not None and self.window.left_top_widget_content is not None:
                            self.window.left_top_widget_content.enabled_zero_calibration_btn_signal.emit()
                            self.close_calibration_window()
                    case 'range_calibration_finish':
                        """
                        量程标定结束
                        """
                        if self.window is not None and self.window.left_top_widget_content is not None:
                            self.window.left_top_widget_content.enabled_range_calibration_btn_signal.emit()
                            self.close_calibration_window()
                    case 'stop_zero_calibration_finish':
                        """
                        stop零点标定结束
                        """
                        if self.window is not None and self.window.left_top_widget_content is not None:
                            self.window.left_top_widget_content.enabled_stop_zero_calibration_btn_signal.emit()
                            self.close_calibration_window()

                    case 'stop_range_calibration_finish':
                        """
                        stop量程标定结束
                        """
                        if self.window is not None and self.window.left_top_widget_content is not None:
                            self.window.left_top_widget_content.enabled_stop_range_calibration_btn_signal.emit()
                            self.close_calibration_window()
                    case 'calibration_msg':
                        """
                        标定的消息
                        """
                        if self.window is not None and self.window.left_top_widget_content is not None:

                            self.window.left_top_widget_content.logger_calibration_msg_signal.emit(message.data)
                    case 'open_calibration_window':
                        """
                        打开标定详情弹窗
                        """
                        if self.window is not None and self.window.main_gui is not None:
                            # logger.critical('open_calibration_window')
                            self.window.main_gui.show_calibration_window_signal.emit({'type':message.data,'time':message.time})

                    case _:
                        pass




            else:
                # 把消息放回去
                self.queue.put(message)

        pass
    def close_calibration_window(self):
        """
        关闭标定详情弹窗
        :return:
        """
        # if self.window is not None and self.window.main_gui is not None:
        #     self.window.main_gui.release_calibration_window_signal.emit()
        pass

read_queue_data_thread = read_queue_data_Thread(name="monitor_data_new_index_read_queue_data_thread")

class Monitor_data_new_index(ThemedWindow):
    def hideEvent(self, a0: typing.Optional[QtGui.QHideEvent]) -> None:
        for widget in self.left_top_widget_content._docks_widget:
            widget: Table_select_columns_paging_bottom
            if widget is not None and hasattr(widget,"data_fetcher_thread") and widget.data_fetcher_thread is not None and widget.data_fetcher_thread.isRunning():
                widget.data_fetcher_thread.pause()
        pass
    def closeEvent(self, a0: typing.Optional[QtGui.QCloseEvent]):
        global read_queue_data_thread
        if read_queue_data_thread is not None:
            read_queue_data_thread.stop()
    def showEvent(self, a0: typing.Optional[QtGui.QShowEvent]) -> None:
        for widget in self.left_top_widget_content._docks_widget:
            widget: Table_select_columns_paging_bottom
            if widget is not None and hasattr(widget,"data_fetcher_thread")  and  widget.data_fetcher_thread is not None and widget.data_fetcher_thread.isRunning():
                widget.data_fetcher_thread.resume()
            elif widget is not None and hasattr(widget,"data_fetcher_thread")  and  widget.data_fetcher_thread is not None and not widget.data_fetcher_thread.isRunning():
                widget.data_fetcher_thread.start()
    def closeEvent(self, a0: typing.Optional[QtGui.QCloseEvent]) -> None:
        pass

    def resizeEvent(self, a0: typing.Optional[QtGui.QResizeEvent]):
        new_size: QSize = a0.size()

        # 使用QSplitter后，不需要手动调整大小，QSplitter会自动处理
        # 可以设置初始比例
        if hasattr(self, 'main_splitter') and self.main_splitter:
            # 左右分割：左边80%，右边20%
            left_width = int(new_size.width() * 0.8)
            right_width = int(new_size.width() * 0.2)
            self.main_splitter.setSizes([left_width, right_width])

        if hasattr(self, 'left_splitter') and self.left_splitter:
            # 上下分割：上边60%，下边40%
            top_height = int(new_size.height() * 0.6)
            bottom_height = int(new_size.height() * 0.4)
            self.left_splitter.setSizes([top_height, bottom_height])

        if hasattr(self, 'right_splitter') and self.right_splitter:
            # 上下分割：上边40%，下边60%
            top_height = int(new_size.height() * 0.4)
            bottom_height = int(new_size.height() * 0.6)
            self.right_splitter.setSizes([top_height, bottom_height])

        self.setMinimumSize(0, 0)

    def setup_tutorial(self):
        # 实例化提示引导器 下面式实例化模板
        if self.tutorial:
            self.tutorial.end_tutorial()

        self.tutorial = TutorialManager(self, "monitor_data_new_index", Tutorial_Type.ARROW_GUIDE,
                                        global_setting.get_setting("app_setting", AppSettings()))

        # 连接教程完成信号
        self.tutorial.tutorial_completed.connect(self.on_tutorial_completed)

        self.tutorial.add_step(self.right_top_widget_content.list_view,
                               f"步骤1：\n勾选通道x的数据项。")
        self.tutorial.add_step(self.right_top_widget_content.show_selected_btn,
                               f"步骤2：\n单击该按钮。")
        self.tutorial.add_step(self.left_top_widget,
                               f"步骤3：\n你可以在这边查看监控数据。")
        self.tutorial.add_step(self.status_bar.tip_btn,
                               f"Tips：\n如果还不会操作，可再次单击该按钮查看教程。")

    def __init__(self, parent=None, geometry: QRect = None, title=""):
        super().__init__()
        self.setMinimumSize(0, 0)

        # 用QWidget替代QDockWidget
        self.left_top_widget: QWidget = None
        self.left_top_widget_content: MonitorDataWindows = None
        self.left_bottom_widget: QWidget = None
        # self.left_bottom_widget_content: MonitorDataChartsWindows = None
        self.right_top_widget: QWidget = None
        self.right_top_widget_content: Table_Column_check_list_view = None
        self.right_bottom_widget: QWidget = None
        self.right_bottom_widget_content: Table_Column_check_list_view = None

        # Splitter组件
        self.main_splitter: QSplitter = None
        self.left_splitter: QSplitter = None
        self.right_splitter: QSplitter = None




        # 实例化ui
        self._init_ui(parent, geometry, title)
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

    def _init_ui(self, parent=None, geometry: QRect = None, title=""):
        # 将ui文件转成py文件后 直接实例化该py文件里的类对象  uic工具转换之后就是这一段代码
        # 有父窗口添加父窗口
        if parent != None and geometry != None:
            self.setParent(parent)
            self.setGeometry(geometry)
        else:
            pass

        self.ui = Ui_monitor_data_new()
        self.ui.setupUi(self)
        self._retranslateUi()
    def _init_function(self):
        global read_queue_data_thread
        read_queue_data_thread.window = self
        read_queue_data_thread.queue = global_setting.get_setting("send_message_queue")
        if read_queue_data_thread is not None and not read_queue_data_thread.isRunning():
            read_queue_data_thread.start()
    def _init_customize_ui(self) -> None:
        # 删除原有的中央widget
        self.delete_central_widget()

        # 创建主布局
        main_layout = QVBoxLayout()
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 创建中央widget
        central_widget = QWidget()
        central_widget.setLayout(main_layout)
        self.setCentralWidget(central_widget)

        # 创建主分割器（左右分割）
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(3)
        main_layout.addWidget(self.main_splitter)

        # 创建左侧分割器（上下分割）
        self.left_splitter = QSplitter(Qt.Orientation.Vertical)
        self.left_splitter.setChildrenCollapsible(False)
        self.left_splitter.setHandleWidth(3)

        # 创建右侧分割器（上下分割）
        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.right_splitter.setChildrenCollapsible(False)
        self.right_splitter.setHandleWidth(3)

        # 添加左右分割器到主分割器
        self.main_splitter.addWidget(self.left_splitter)
        self.main_splitter.addWidget(self.right_splitter)

        # 创建左上widget
        self.left_top_widget = QWidget()
        self.left_top_widget.setMinimumSize(600, 400)
        self.left_top_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        # 为左上widget创建布局
        left_top_layout = QVBoxLayout(self.left_top_widget)
        left_top_layout.setContentsMargins(0, 0, 0, 0)

        # 创建DemoDraggableDockWidget
        self.left_top_widget_content = MonitorDataWindows()
        left_top_layout.addWidget(self.left_top_widget_content)

        self.left_splitter.addWidget(self.left_top_widget)

        # 创建左下widget （暂时隐藏）
        self.left_bottom_widget = QWidget()
        self.left_bottom_widget.hide()
        # self.left_bottom_widget.setMinimumSize(600, 400)
        # self.left_bottom_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        #
        # # 为左下widget创建布局
        # left_bottom_layout = QVBoxLayout(self.left_bottom_widget)
        # left_bottom_layout.setContentsMargins(0, 0, 0, 0)
        #
        # # 创建widget
        # self.left_bottom_widget_content = MonitorDataChartsWindows()
        # left_bottom_layout.addWidget(self.left_bottom_widget_content)

        self.left_splitter.addWidget(self.left_bottom_widget)

        # 创建右上widget
        self.right_top_widget = QWidget()
        right_top_layout = QVBoxLayout(self.right_top_widget)
        right_top_layout.setContentsMargins(0, 0, 0, 0)

        self.right_top_widget_content = Table_Column_check_list_view(ok_btn_text="确定选择通道", datas_type=1)
        right_top_layout.addWidget(self.right_top_widget_content)

        self.right_splitter.addWidget(self.right_top_widget)

        # 连接信号
        self.right_top_widget_content.set_table_column_signal.connect(self.create_table)

        # 创建右下widget（暂时隐藏）
        self.right_bottom_widget = QWidget()
        self.right_bottom_widget.hide()
        self.right_splitter.addWidget(self.right_bottom_widget)

        # 设置初始大小比例
        # 主分割器：左边95%，右边5%
        self.main_splitter.setSizes([950, 50])
        self.main_splitter.handle(1).setEnabled(False)

        # 左侧分割器：上边100%（因为下边隐藏了）
        self.left_splitter.setSizes([1000, 0])

        # 右侧分割器：上边100%（因为下边隐藏了）
        self.right_splitter.setSizes([1000, 0])

        # 设置分割器样式
        splitter_style = """
            QSplitter::handle {
                background-color: #cccccc;
               
            }
            QSplitter::handle:hover {
                background-color: #bbbbbb;
            }
            QSplitter::handle:horizontal {
                width: 3px;
            }
            QSplitter::handle:vertical {
                height: 3px;
            }
        """
        self.main_splitter.setStyleSheet(splitter_style)
        self.left_splitter.setStyleSheet(splitter_style)
        self.right_splitter.setStyleSheet(splitter_style)

        super()._init_customize_ui()

    def create_table(self, dict_ids: dict):
        if dict_ids is not None:
            # logger.critical(f"monitor_data_new_index | checkids_dict:{dict_ids}")
            type = dict_ids.get('type', "")
            # 选择数据项
            if type == "column":
                for widget in self._docks_widget:
                    widget: Table_select_columns_paging_bottom
                    widget.on_replace_headers(dict_ids['data'])
                pass
            # 选择通道
            elif type == "group":
                settings: Experiment_setting_entity = global_setting.get_setting("experiment_setting", None)
                if settings:
                    gids = [int(global_setting.get_setting('configer')['mouse_cage']['reference'])] + [
                        group.id for group in settings.groups if group.id in dict_ids['data']
                    ]

                    current_gids = [int(gid) for gid in getattr(self.left_top_widget_content, "gids", [])]

                    if current_gids == gids:
                        return

                    self.left_top_widget_content.create_tiled_docks(n=len(gids), gids=gids)
                pass
            else:
                pass



    def show_left_bottom_widget(self):
        """显示左下widget"""
        self.left_bottom_widget.show()
        # 重新设置比例
        total_height = self.left_splitter.height()
        self.left_splitter.setSizes([int(total_height * 0.6), int(total_height * 0.4)])

    def hide_left_bottom_widget(self):
        """隐藏左下widget"""
        self.left_bottom_widget.hide()
        self.left_splitter.setSizes([1000, 0])

    def show_right_bottom_widget(self):
        """显示右下widget"""
        self.right_bottom_widget.show()
        # 重新设置比例
        total_height = self.right_splitter.height()
        self.right_splitter.setSizes([int(total_height * 0.4), int(total_height * 0.6)])

    def hide_right_bottom_widget(self):
        """隐藏右下widget"""
        self.right_bottom_widget.hide()
        self.right_splitter.setSizes([1000, 0])
