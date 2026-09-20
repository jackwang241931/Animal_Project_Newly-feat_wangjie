import copy
import time
import typing
from enum import Enum

from PyQt6 import QtGui
from PyQt6.QtCore import QTimer, pyqtSignal, Qt
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel, QHBoxLayout, QButtonGroup, QRadioButton, \
    QPushButton, QListWidget, QScrollArea, QFileDialog, QMessageBox, QFrame, QSplitter, QSizePolicy
from loguru import logger

from Module.new_monitor_data.ui.custom.table.Table_select_columns_paging_bottom import Table_select_columns_paging_bottom
from public.component.dialog.custom.InfoDialog import InfoDialog
from public.component.dock_widget.DraggableWindow import DemoDraggableDockWidget
from public.config_class.global_setting import global_setting
from public.entity.BaseFrame import BaseFrame
from public.entity.BaseWidget import BaseWidget
from public.entity.BaseWindow import BaseWindow
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.util.time_util import time_util
from theme.ThemeQt6 import ThemedWidget

class Show_Type(Enum):
    ALL = 0
    EACH = 1

    def __lt__(self, other):
        if other is None:
            return False
        return self.value < other.value

    def __le__(self, other):
        if other is None:
            return False
        return self.value <= other.value

    def __gt__(self, other):
        if other is None:
            return False
        return self.value > other.value

    def __ge__(self, other):
        if other is None:
            return False
        return self.value >= other.value

    def __eq__(self, other):
        if other is None:
            return False
        return self.value == other.value

    def __ne__(self, other):
        if other is None:
            return False
        return self.value != other.value
class MonitorDataWindows(ThemedWidget):
    logger_calibration_msg_signal =pyqtSignal(str)
    enabled_zero_calibration_btn_signal =pyqtSignal()
    enabled_range_calibration_btn_signal =pyqtSignal()
    enabled_stop_zero_calibration_btn_signal = pyqtSignal()
    enabled_stop_range_calibration_btn_signal = pyqtSignal()
    def resizeEvent(self, a0: typing.Optional[QtGui.QResizeEvent]):
        super().resizeEvent(a0)
        self.setMinimumSize(0, 0)
        if hasattr(self, "main_splitter") and hasattr(self, "content_layout_widget"):
            QTimer.singleShot(0, self._update_table_height)

    def _update_table_height(self):
        """Reduce the table height only when the current window cannot fit 475 px."""
        if not hasattr(self, "main_splitter") or not hasattr(self, "content_layout_widget"):
            return

        splitter_height = self.main_splitter.height()
        if splitter_height <= 0:
            return

        handle_height = self.main_splitter.handleWidth() * (
            self.main_splitter.count() - 1
        )
        available_height = max(0, splitter_height - handle_height)
        top_height = 175
        table_height = min(
            475,
            max(0, available_height - top_height),
        )

        if table_height == self._table_height:
            return

        self._table_height = table_height
        self.content_layout_widget.setMaximumHeight(table_height)
        self.main_splitter.setSizes([top_height, table_height, 0])

    @property
    def is_zero_calibration(self):
        return self._is_zero_calibration

    @property
    def is_range_calibration(self):
        return self._is_range_calibration

    @is_zero_calibration.setter
    def is_zero_calibration(self, value):
        old_value = self._is_zero_calibration
        self._is_zero_calibration = value
        if not value:
            self._check_both_calibration_status()

    @is_range_calibration.setter
    def is_range_calibration(self, value):
        old_value = self._is_range_calibration
        self._is_range_calibration = value
        if not value:
            self._check_both_calibration_status()

    def _check_both_calibration_status(self):
        """检查两个标定状态是否都为False"""
        # 如果是按了一起标定的按钮
        logger.critical(f"is_all_calibration:{self.is_all_calibration}|_is_zero_calibration:{self._is_zero_calibration}|_is_range_calibration:{self._is_range_calibration}")
        if self.is_all_calibration:
            if not self._is_zero_calibration and not self._is_range_calibration:
                if self.calibration_btn is not None:
                    self.is_all_calibration=False
                    self.calibration_btn.setDisabled(False)
        else:
            if not self._is_zero_calibration or not  self._is_range_calibration:
                if self.calibration_btn is not None:
                    self.calibration_btn.setDisabled(False)
    def __init__(self):
        super().__init__()
        self.gids = []
        self.n = 0
        # 存放创建的 dock 引用
        self._docks_widget = []
        self._docks_widget_calibration = []
        self._retired_data_widgets = []
        # 是否正在零点标定 和量程标定
        self.is_all_calibration = False
        # 正在零点标定
        self._is_zero_calibration = False
        # 正在量程标定
        self._is_range_calibration =False
        # 默认看总表
        self.default_show = Show_Type.ALL
        # 当前看
        self.current_show = copy.deepcopy(self.default_show)
        self.show_radio_data = [
            {"text": "总表查看", "value": Show_Type.ALL},
            {"text": "分表查看", "value":  Show_Type.EACH}
        ]

        self.main_layout = QVBoxLayout()
        self.main_splitter = QSplitter(Qt.Orientation.Vertical)
        self.main_splitter.setChildrenCollapsible(False)
        self.main_splitter.setHandleWidth(3)

        self.top_layout_scroll=QScrollArea()
        self.top_layout_widget = QWidget()
        self.top_layout = QHBoxLayout(self.top_layout_widget)

        btn_layout = QVBoxLayout()
        btn_left_layout = QHBoxLayout()
        self.button_group = QButtonGroup()
        self.show_radios = []
        for i, data in enumerate(self.show_radio_data):
            radio = QRadioButton(data["text"])
            if i ==0:
                radio.setStyleSheet("""
                QRadioButton{
                    margin-left:15px;
                }
                """)
            radio.setProperty("value", data["value"])  # 存储数据值
            # 设置默认选择
            if data["value"]==self.default_show:
                radio.setChecked(True)
            self.show_radios.append(radio)
            self.button_group.addButton(radio, i)
            btn_left_layout.addWidget(radio)
        btn_right_layout = QHBoxLayout()
        self.zero_calibration_btn = QPushButton("校零")
        self.range_calibration_btn = QPushButton("校量程")
        self.calibration_btn = QPushButton("校零且量程")
        self.show_calibration_btn = QPushButton("显示校准信息详情")
        btn_right_layout.addWidget(self.zero_calibration_btn)
        btn_right_layout.addWidget(self.range_calibration_btn)
        btn_right_layout.addWidget(self.calibration_btn)
        btn_right_layout.addWidget( self.show_calibration_btn )

        btn_bottom_layout = QHBoxLayout()
        self.stop_zero_calibration_btn = QPushButton("停止校零")
        self.stop_range_calibration_btn = QPushButton("停止校量程")
        self.stop_calibration_btn = QPushButton("停止校零且量程")
        # self.stop_range_calibration_btn.setDisabled(True)
        # self.stop_zero_calibration_btn.setDisabled(True)
        # self.stop_calibration_btn.setDisabled(True)
        btn_bottom_layout.addWidget(self.stop_zero_calibration_btn)
        btn_bottom_layout.addWidget(self.stop_range_calibration_btn)
        btn_bottom_layout.addWidget(self.stop_calibration_btn)

        btn_layout.addLayout(btn_left_layout)
        btn_layout.addLayout(btn_right_layout)
        btn_layout.addLayout(btn_bottom_layout)
        self.top_layout.addLayout(btn_layout)
        # Scroll area2 包含 QListView
        opera_layout_widget =QWidget()
        opera_layout_widget.setWindowTitle(f"校准信息页")
        opera_layout =QVBoxLayout(opera_layout_widget)
        h_layout = QHBoxLayout()
        tip_label = QLabel("操作（操作必须手动导出数据，否则停止实验和关闭程序不会导出操作数据！）:")
        tip_label.setStyleSheet("""
                        QLabel{
                            margin-left:15px;
                        }
                        """)
        # 创建导出按钮
        self.export_button = QPushButton("导出操作")
        self.export_button.setMaximumHeight(40)
        h_layout.addWidget(tip_label)
        h_layout.addStretch(7)
        h_layout.addWidget(self.export_button)

        opera_layout.addLayout(h_layout)
        h_layout_2 =QHBoxLayout()
        self.scroll_area_2 = QScrollArea()
        self.scroll_area_2.setWidgetResizable(True)
        # 创建滚动区域内的内容窗口部件
        scroll_content = QWidget()
        scroll_layout = QVBoxLayout(scroll_content)

        # 创建 QListWidget
        self.list_widget = QListWidget()

        # self.list_widget.setMinimumHeight(300)

        # 添加组件到滚动布局
        scroll_layout.addWidget(self.list_widget)

        # 设置滚动区域的内容
        self.scroll_area_2.setWidget(scroll_content)
        h_layout_2.addWidget(self.scroll_area_2)
        opera_layout.addLayout(h_layout_2)
        opera_layout.addStretch(7)


        self.opera_layout_widget = DemoDraggableDockWidget(is_showt_tab=False, enable_detach=False)
        self._docks_widget_calibration.append( opera_layout_widget)
        self.opera_layout_widget.addFrames(self._docks_widget_calibration)

        self.top_layout.addWidget(self.opera_layout_widget)
        self.top_layout_scroll.setLayout(self.top_layout)
        self.main_splitter.addWidget(self.top_layout_scroll)
        # self.main_layout.addLayout(self.top_layout, stretch=1)
        # # 添加横向分割线
        # line = QFrame()
        # line.setFrameShape(QFrame.Shape.HLine)  # 设置为横线
        # line.setFrameShadow(QFrame.Shadow.Sunken)  # 设置阴影效果
        # line.setStyleSheet("height: 3px; background-color: gray;")  # 设置粗细和颜色
        # self.main_layout.addWidget(line, stretch=1)
        # 连接信号
        self.enabled_zero_calibration_btn_signal.connect(self.enabled_zero_calibration_btn)
        self.enabled_range_calibration_btn_signal.connect(self.enabled_range_calibration_btn)
        self.enabled_stop_zero_calibration_btn_signal.connect(self.enabled_stop_zero_calibration_btn)
        self.enabled_stop_range_calibration_btn_signal.connect(self.enabled_stop_range_calibration_btn)
        self.logger_calibration_msg_signal.connect(self.logger_calibration_msg)
        # 绑定按钮事件
        self.zero_calibration_btn.clicked.connect(self.zero_calibration_start)
        self.range_calibration_btn.clicked.connect(self.range_calibration_start)
        self.calibration_btn.clicked.connect(self.calibration_start)
        self.show_calibration_btn.clicked.connect(self.show_calibration_window)
        self.stop_zero_calibration_btn.clicked.connect(self.zero_calibration_stop)
        self.stop_range_calibration_btn.clicked.connect(self.range_calibration_stop)
        self.stop_calibration_btn.clicked.connect(self.calibration_stop)
        self.export_button.clicked.connect(self.export_opera_data)
        self.button_group.buttonClicked.connect(self.on_show_selection_changed)

        # 表格区域
        self.content_layout_widget = QWidget()
        # 表格区保留紧凑高度，确保底部总横向滚动条在窗口内可见。
        self._table_height = 475
        self.content_layout_widget.setMaximumHeight(self._table_height)
        self.content_layout = QVBoxLayout( self.content_layout_widget)
        self.content_widget = DemoDraggableDockWidget(
            is_showt_tab=False,
            enable_detach=False,
            disable_vertical_wheel=True,
        )
        # 外层只需要横向切换通道，隐藏不需要的纵向滚动条。
        self.content_widget.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_layout.addWidget(self.content_widget)

        self.main_splitter.addWidget(self.content_layout_widget)
        # self.main_layout.addLayout(self.content_layout,stretch=8)

        # 表格缩短后，把剩余高度留在表格下方，避免反向撑大上方操作区。
        self.content_bottom_spacer = QWidget()
        self.content_bottom_spacer.setSizePolicy(
            QSizePolicy.Policy.Preferred,
            QSizePolicy.Policy.Expanding,
        )
        self.main_splitter.addWidget(self.content_bottom_spacer)


        # 设置拉伸因子 (索引, 拉伸因子)
        self.main_splitter.setStretchFactor(0, 0)
        self.main_splitter.setStretchFactor(1, 0)
        self.main_splitter.setStretchFactor(2, 1)
        self.main_splitter.setSizes([175, self._table_height, 0])
        for i in range(1, self.main_splitter.count()):
            self.main_splitter.handle(i).setEnabled(False)
        self.main_layout.addWidget(self.main_splitter)

        self.setLayout(self.main_layout)
        QTimer.singleShot(0, self._update_table_height)

    def clear_existing_docks(self):
        all_data_widgets = list(self._docks_widget)
        for widget in all_data_widgets:
            if hasattr(widget, "shutdown"):
                widget.shutdown(wait_ms=0)

        for widget in all_data_widgets:
            widget: BaseWindow | BaseWidget | BaseFrame
            widget.get_ancestor()
            if hasattr(widget.ancestor, "detached_window"):
                if widget.ancestor.detached_window is not None:
                    widget.ancestor.detached_window.close()
            widget.hide()
            widget.setParent(None)
            self._retire_data_widget(widget)

        self.content_widget.remove_all()
        self._docks_widget = []

    def _retire_data_widget(self, widget):
        """查询线程退出后再销毁控件，切页过程不阻塞 GUI。"""
        thread = getattr(widget, "data_fetcher_thread", None)
        if thread is None or not thread.isRunning():
            widget.deleteLater()
            return

        self._retired_data_widgets.append(widget)
        thread.finished.connect(
            lambda retired_widget=widget: self._finalize_retired_widget(retired_widget)
        )

    def _finalize_retired_widget(self, widget):
        if widget in self._retired_data_widgets:
            self._retired_data_widgets.remove(widget)
        widget.deleteLater()
    def create_tiled_docks(self, n=8, gids=None):
        gids = list(gids or [])
        if n !=0:
            self.n=n
        if len(gids)>0:
            self.gids = gids
        self.clear_existing_docks()
        if self.current_show == Show_Type.EACH:
            for gid in gids:
                widget = Table_select_columns_paging_bottom(gid=gid)
                widget.setWindowTitle(f"通道/鼠笼 {gid} {'(参考气)' if gid==int(global_setting.get_setting('configer')['mouse_cage']['reference']) else ''}")
                # ！！！！！！！！！！！！！！！！！！！！！！！！！！临时添加！！！！！！！！！！！！！！！！！！
                widget.on_replace_headers([1])
                widget.setWindowTitle(
                    f"通道/鼠笼 {'参考笼' if gid == int(global_setting.get_setting('configer')['mouse_cage']['reference']) else gid}"
                )

                self._docks_widget.append(widget)
            self.content_widget.addFrames(self._docks_widget)
        else:
            widget = Table_select_columns_paging_bottom(gid=-1)
            widget.setWindowTitle(f"通道/鼠笼 总表")
            # ！！！！！！！！！！！！！！！！！！！！！！！！！！临时添加！！！！！！！！！！！！！！！！！！
            widget.on_replace_headers([1])
            self._docks_widget.append(widget)
            self.content_widget.addFrames(self._docks_widget)
    def on_show_selection_changed(self, button):
        # 获取自定义数据值
        self.setRadiosEnable(enable=False)
        value = button.property("value")
        if self.current_show != value:
            self.current_show =value
            if self.current_show == Show_Type.EACH:
                self.create_tiled_docks(n=self.n, gids=self.gids)
            else:
                self.create_tiled_docks()
        QTimer.singleShot(300, lambda: self.setRadiosEnable(enable=True))
    def setRadiosEnable(self,enable = True):
        for radios in self.show_radios:
            radios.setEnabled(enable)
    def export_opera_data(self):
        """导出所有操作数据功能"""
        try:
            # 获取文件保存路径
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "保存所有数据",
                "all_data.txt",
                "文本文件 (*.txt);;所有文件 (*)"
            )

            if file_path:
                # 获取列表中的所有数据
                all_items = []
                for i in range(self.list_widget.count()):
                    item = self.list_widget.item(i)
                    all_items.append(item.text())

                # 写入文件
                with open(file_path, 'w', encoding='utf-8') as file:
                    for item_text in all_items:
                        file.write(item_text + '\n')

                QMessageBox.information(
                    self,
                    "导出成功",
                    f"已导出 {len(all_items)} 项数据到:\n{file_path}"
                )

        except Exception as e:
            QMessageBox.critical(
                self,
                "导出失败",
                f"导出过程中发生错误:\n{str(e)}"
            )
        pass
    def show_calibration_window(self):
        """
        显示校准详情页
        :return:
        """
        # 校0按钮事件
        send_message_queue = global_setting.get_setting("send_message_queue")
        #  打开标定详情界面

        send_message_queue.put(ObjectQueueItem(origin='monitor_data_windows', to='monitor_data_new_index',
                                               title='open_calibration_window',
                                               data=None,
                                               time=time_util.get_format_from_time(time.time())))
    def zero_calibration_start(self):
        self.disabled_zero_calibration_btn()


        # 校0按钮事件
        send_message_queue = global_setting.get_setting("send_message_queue")
        #  打开标定详情界面

        send_message_queue.put(ObjectQueueItem(origin='monitor_data_windows', to='monitor_data_new_index',
                                               title='open_calibration_window',
                                               data="zero_calibration",
                                               time=time_util.get_format_from_time(time.time())))

        send_message_queue.put(ObjectQueueItem(origin='monitor_data_windows', to='main_monitor_data',
                                               title='start_zero_calibration',
                                               data=None,
                                               time=time_util.get_format_from_time(time.time())))
        self.list_widget.insertItem(0, f"{time_util.get_format_from_time(time.time())}-校0按钮被点击时间")
        # msg_box = InfoDialog(title="校0", info=f"确认校0开始，校准完成还需要至少6-8轮次时间，请耐心等待",
        #                      icon=QMessageBox.Icon.Information)
        # msg_box.exec()

        pass

    def range_calibration_start(self):
        self.disabled_range_calibration_btn()
        # 校span按钮事件
        # 校0按钮事件
        send_message_queue = global_setting.get_setting("send_message_queue")
        #  打开标定详情界面

        send_message_queue.put(ObjectQueueItem(origin='monitor_data_windows', to='monitor_data_new_index',
                                               title='open_calibration_window',
                                               data="span_calibration",
                                               time=time_util.get_format_from_time(time.time())))

        send_message_queue.put(ObjectQueueItem(origin='monitor_data_windows', to='main_monitor_data',
                                               title='start_span_calibration',
                                               data=None,
                                               time=time_util.get_format_from_time(time.time())))
        self.list_widget.insertItem(0, f"{time_util.get_format_from_time(time.time())}-校span按钮被点击时间")
        # msg_box = InfoDialog(title="校span", info=f"确认校span开始，校准完成还需要至少6-8轮次时间，请耐心等待",
        #                      icon=QMessageBox.Icon.Information)
        # msg_box.exec()

        pass

    def calibration_start(self):
        self.is_all_calibration = True
        self.disabled_range_calibration_btn()
        self.disabled_zero_calibration_btn()

        # 校0校span按钮事件
        #
        send_message_queue = global_setting.get_setting("send_message_queue")
        #  打开标定详情界面

        send_message_queue.put(ObjectQueueItem(origin='monitor_data_windows', to='monitor_data_new_index',
                                               title='open_calibration_window',
                                               data="all_calibration",
                                               time=time_util.get_format_from_time(time.time())))

        send_message_queue.put(ObjectQueueItem(origin='monitor_data_windows', to='main_monitor_data',
                                               title='start_calibration',
                                               data=None,
                                               time=time_util.get_format_from_time(time.time())))
        self.list_widget.insertItem(0, f"{time_util.get_format_from_time(time.time())}-校0和校span按钮被点击时间")
        # msg_box = InfoDialog(title="校0和校span", info=f"确认校0和校span开始，校准完成还需要至少12-16轮次时间，请耐心等待",
        #                      icon=QMessageBox.Icon.Information)
        # msg_box.exec()

        pass
    def zero_calibration_stop(self):
        self.disabled_stop_zero_calibration_btn()
        # 校0按钮事件
        send_message_queue = global_setting.get_setting("send_message_queue")
        send_message_queue.put(ObjectQueueItem(origin='monitor_data_windows', to='main_monitor_data',
                                               title='stop_zero_calibration',
                                               data=None,
                                               time=time_util.get_format_from_time(time.time())))
        self.list_widget.insertItem(0, f"{time_util.get_format_from_time(time.time())}-停止校0按钮被点击时间")
        # msg_box = InfoDialog(title="停止校0", info=f"停止校0开始，请耐心等待",
        #                      icon=QMessageBox.Icon.Information)
        # msg_box.exec()

        pass

    def range_calibration_stop(self):
        self.disabled_stop_range_calibration_btn()
        # 校span按钮事件
        # 校0按钮事件
        send_message_queue = global_setting.get_setting("send_message_queue")
        send_message_queue.put(ObjectQueueItem(origin='monitor_data_windows', to='main_monitor_data',
                                               title='stop_span_calibration',
                                               data=None,
                                               time=time_util.get_format_from_time(time.time())))
        self.list_widget.insertItem(0, f"{time_util.get_format_from_time(time.time())}-停止校span按钮被点击时间")
        # msg_box = InfoDialog(title="停止校span", info=f"停止校span开始，请耐心等待",
        #                      icon=QMessageBox.Icon.Information)
        # msg_box.exec()

        pass

    def calibration_stop(self):
        self.is_all_calibration = False
        self.disabled_stop_range_calibration_btn()
        self.disabled_stop_zero_calibration_btn()

        # 校0校span按钮事件
        # 校0按钮事件
        send_message_queue = global_setting.get_setting("send_message_queue")
        send_message_queue.put(ObjectQueueItem(origin='monitor_data_windows', to='main_monitor_data',
                                               title='stop_calibration',
                                               data=None,
                                               time=time_util.get_format_from_time(time.time())))
        self.list_widget.insertItem(0, f"{time_util.get_format_from_time(time.time())}-停止校0和校span按钮被点击时间")
        # msg_box = InfoDialog(title="停止校0和校span", info=f"停止校0和校span开始，请耐心等待",
        #                      icon=QMessageBox.Icon.Information)
        # msg_box.exec()

        pass
    def disabled_zero_calibration_btn(self):
        self.calibration_btn.setDisabled(True)
        self.zero_calibration_btn.setDisabled(True)

        self.stop_calibration_btn.setDisabled(False)
        self.stop_zero_calibration_btn.setDisabled(False)

        self.is_zero_calibration=True
        self.zero_calibration_btn.setText("正在校零中")
    def disabled_range_calibration_btn(self):
        self.calibration_btn.setDisabled(True)
        self.range_calibration_btn.setDisabled(True)

        self.stop_calibration_btn.setDisabled(False)
        self.stop_range_calibration_btn.setDisabled(False)

        self.is_range_calibration=True
        self.range_calibration_btn.setText("正在校量程中")

    def disabled_stop_zero_calibration_btn(self):
        self.stop_calibration_btn.setDisabled(True)
        self.stop_zero_calibration_btn.setDisabled(True)

        self.is_zero_calibration = False
        self.stop_zero_calibration_btn.setText("正在停止校零中")
        self.zero_calibration_btn.setText("校零")
    def disabled_stop_range_calibration_btn(self):
        self.stop_calibration_btn.setDisabled(True)
        self.stop_range_calibration_btn.setDisabled(True)
        self.is_range_calibration = False
        self.stop_range_calibration_btn.setText("正在停止校量程中")
        self.range_calibration_btn.setText("校量程")
    def enabled_zero_calibration_btn(self):

        self.zero_calibration_btn.setDisabled(False)
        self.is_zero_calibration = False
        self.zero_calibration_btn.setText("校零")
        self.list_widget.insertItem(0,
                                                                   f"{time_util.get_format_from_time(time.time())}-校零完成时间")
        # msg_box_3 = InfoDialog(title="校零完成", info=f"校0已经完成，完成时间{time_util.get_format_from_time(time.time())}",
        #                      icon=QMessageBox.Icon.Information)
        # msg_box_3.exec()
    def enabled_range_calibration_btn(self):
        self.range_calibration_btn.setDisabled(False)
        self.is_range_calibration = False
        self.range_calibration_btn.setText("校量程")
        self.list_widget.insertItem(0,
                                                                   f"{time_util.get_format_from_time(time.time())}-校量程完成时间")
        # msg_box_2 = InfoDialog(title="校量程完成", info=f"校量程已经完成，完成时间{time_util.get_format_from_time(time.time())}",
        #                      icon=QMessageBox.Icon.Information)
        # msg_box_2.exec()


    def enabled_stop_zero_calibration_btn(self):

        self.zero_calibration_btn.setDisabled(False)
        self.is_zero_calibration = False
        self.zero_calibration_btn.setText("校零")
        self.stop_zero_calibration_btn.setText("停止校零")

        self.list_widget.insertItem(0,
                                                                   f"{time_util.get_format_from_time(time.time())}-停止校零完成时间")
        # msg_box_3 = InfoDialog(title="停止校零完成", info=f"停止校0已经完成，完成时间{time_util.get_format_from_time(time.time())}",
        #                      icon=QMessageBox.Icon.Information)
        # msg_box_3.exec()
    def enabled_stop_range_calibration_btn(self):
        self.range_calibration_btn.setDisabled(False)
        self.is_range_calibration = False
        self.range_calibration_btn.setText("校量程")
        self.stop_range_calibration_btn.setText("停止校量程")
        self.list_widget.insertItem(0,
                                                                   f"{time_util.get_format_from_time(time.time())}-停止校量程完成时间")
        # msg_box_2 = InfoDialog(title="停止校量程完成", info=f"停止校量程已经完成，完成时间{time_util.get_format_from_time(time.time())}",
        #                      icon=QMessageBox.Icon.Information)
        # msg_box_2.exec()
    def logger_calibration_msg(self,msg):
        self.list_widget.insertItem(0,msg)
