import typing

from PyQt6 import QtGui
from PyQt6.QtCore import QRect, QSize, QTimer
from PyQt6.QtWidgets import QDockWidget, QSizePolicy

from Module.new_monitor_data.ui.custom.table.Table_select_columns_paging_bottom import Table_select_columns_paging_bottom
from Module.new_monitor_data.ui.monitor_data_new import Ui_monitor_data_new
from Module.new_monitor_data.index.right_top_widget.table_column_check_list_view import Table_Column_check_list_view
from public.component.Guide_tutorial_interface.Tutorial_Manager import TutorialManager
from public.component.dock_widget.DraggableWindow import DemoDraggableDockWidget
from public.config_class.App_Setting import AppSettings
from public.config_class.global_setting import global_setting
from public.entity.enum.Public_Enum import Tutorial_Type
from public.entity.experiment_setting_entity import Experiment_setting_entity
from theme.ThemeQt6 import ThemedWindow


class Monitor_data_new_index(ThemedWindow):
    def hideEvent(self, a0: typing.Optional[QtGui.QHideEvent]) -> None:
        for widget in self._docks_widget:
            widget: Table_select_columns_paging_bottom
            if widget is not None and widget.data_fetcher_thread is not None and widget.data_fetcher_thread.isRunning():
                widget.data_fetcher_thread.pause()

        pass
    def showEvent(self, a0: typing.Optional[QtGui.QShowEvent]) -> None:
        for widget in self._docks_widget:
            widget: Table_select_columns_paging_bottom
            if widget is not None and widget.data_fetcher_thread is not None and  widget.data_fetcher_thread.isRunning():

                widget.data_fetcher_thread.resume()
            elif widget.data_fetcher_thread is not None and not widget.data_fetcher_thread.isRunning():

                widget.data_fetcher_thread.start()
        # pass
        pass
    def closeEvent(self, a0: typing.Optional[QtGui.QCloseEvent]) -> None:
        pass
    def resizeEvent(self, a0 :typing.Optional[QtGui.QResizeEvent]):
        new_size:QSize = a0.size()
        #


        if self.left_bottom_dock_widget is not None:
            self.left_bottom_dock_widget.widget().resize(int(new_size.width() * 0.8), int(new_size.height() * 0.4))
            self.left_bottom_dock_widget.resize(int(new_size.width() * 0.8), int(new_size.height() * 0.4))

        if self.left_top_dock_widget is not None:
            self.left_top_dock_widget.widget().resize(int(new_size.width() * 0.8), int(new_size.height() * 0.6))
            self.left_top_dock_widget.resize(int(new_size.width() * 0.8), int(new_size.height()* 0.6))

        if self.right_top_dock_widget is not None:
            self.right_top_dock_widget.widget().resize(int(new_size.width() * 0.2), int(new_size.height()*0.4))
            self.right_top_dock_widget.resize(int(new_size.width() * 0.2), int(new_size.height()*0.4))
        if self.right_bottom_dock_widget is not None:
            self.right_bottom_dock_widget.widget().resize(int(new_size.width() * 0.2), int(new_size.height()*0.6))
            self.right_bottom_dock_widget.resize(int(new_size.width() * 0.2), int(new_size.height()*0.6))

        self.setMinimumSize(0, 0)
    def setup_tutorial(self):
        # 实例化提示引导器 下面式实例化模板
        if self.tutorial:
            self.tutorial.end_tutorial()

        self.tutorial = TutorialManager(self, "monitor_data_new_index", Tutorial_Type.ARROW_GUIDE,
                                        global_setting.get_setting("app_setting", AppSettings()))

        # 连接教程完成信号
        self.tutorial.tutorial_completed.connect(self.on_tutorial_completed)

        self.tutorial.add_step(self.right_top_dock_widget_content.list_view,
                               f"步骤1：\n勾选通道x的数据项。")
        self.tutorial.add_step(self.right_top_dock_widget_content.show_selected_btn,
                               f"步骤2：\n单击该按钮。")
        self.tutorial.add_step(self.left_top_dock_widget,
                               f"步骤3：\n你可以在这边查看监控数据。")
        self.tutorial.add_step(self.status_bar.tip_btn,
                               f"Tips：\n如果还不会操作，可再次单击该按钮查看教程。")
    def __init__(self, parent=None, geometry: QRect = None, title=""):
        super().__init__()
        self.setMinimumSize(0,0)
        self.left_top_dock_widget:QDockWidget = None
        self.left_top_dock_widget_content:DemoDraggableDockWidget=None
        self.left_bottom_dock_widget:QDockWidget = None
        self.right_top_dock_widget:QDockWidget = None
        self.right_top_dock_widget_content: Table_Column_check_list_view = None
        self.right_bottom_dock_widget:QDockWidget = None
        self.right_bottom_dock_widget_content: Table_Column_check_list_view = None

        # 装载left_top_dock_widget_content里的各个widget
        self._docks_widget = []
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

        self.ui = Ui_monitor_data_new()

        self.ui.setupUi(self)

        self._retranslateUi()

        pass
    def _init_customize_ui(self) -> None:
        self.delete_central_widget()
        self.left_top_dock_widget:QDockWidget = self.findChild(QDockWidget, "left_top_dock_widget")


        self.left_top_dock_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)

        self.left_top_dock_widget.setMinimumSize(600, 400)
        self.left_top_dock_widget_content: DemoDraggableDockWidget = DemoDraggableDockWidget()
        self.left_top_dock_widget.setWidget( self.left_top_dock_widget_content)

        self.left_bottom_dock_widget:QDockWidget = self.findChild(QDockWidget, "left_bottom_dock_widget")
        self.left_bottom_dock_widget.hide()

        self.right_top_dock_widget:QDockWidget = self.findChild(QDockWidget, "right_top_dock_widget")

        self.right_top_dock_widget_content = Table_Column_check_list_view(ok_btn_text="确定选择通道",datas_type=1)
        self.right_top_dock_widget.setWidget(self.right_top_dock_widget_content)

        self.right_top_dock_widget_content.set_table_column_signal.connect(self.create_table)




        # 这部分挪到数据处理区域
        self.right_bottom_dock_widget: QDockWidget = self.findChild(QDockWidget, "right_bottom_dock_widget")
        self.right_bottom_dock_widget.hide()
        # self.right_bottom_dock_widget_content = Table_Column_check_list_view(ok_btn_text="生成图表",datas_type=0)
        # self.right_bottom_dock_widget.setWidget(self.right_bottom_dock_widget_content)
        #
        # self.right_bottom_dock_widget_content.set_table_column_signal.connect(
        #     self.create_table)
        super()._init_customize_ui()
        pass
    def create_table(self,dict_ids:dict):
        if dict_ids is not None:
            # logger.critical(f"monitor_data_new_index | checkids_dict:{dict_ids}")
            type = dict_ids.get('type',"")
            # 选择数据项
            if type == "column":
                for widget in self.left_top_dock_widget_content._docks_widget:
                    widget: Table_select_columns_paging_bottom
                    widget.on_replace_headers(dict_ids['data'])
                pass
            # 选择通道
            elif type == "group":
                settings: Experiment_setting_entity = global_setting.get_setting("experiment_setting", None)
                if settings:
                    # logger.critical(f"monitor_data_new_index | experiment_setting:{settings}")
                    # 将参考气也放进去
                    gids =[0]+ [group.id  for group in settings.groups if group.id in dict_ids['data']]
                    # logger.critical(f"monitor_data_new_index | gids{gids}")
                    self.create_tiled_docks(n=len(gids),gids=gids)
                pass
            else:

                pass
        pass
    def clear_existing_docks(self):
        self.left_top_dock_widget_content.remove_all()
        for d in self._docks_widget:
            d.hide()
            d.deleteLater()
        self._docks_widget = []

    def create_tiled_docks(self, n=8, gids=[]):
        self.clear_existing_docks()

        for gid in gids:
            widget = Table_select_columns_paging_bottom(gid=gid)
            # ！！！！！！！！！！！！！！！！！！！！！！！！！！临时添加！！！！！！！！！！！！！！！！！！
            widget.on_replace_headers([1])

            self._docks_widget.append(widget)
        self.left_top_dock_widget_content.addFrames(self._docks_widget)
