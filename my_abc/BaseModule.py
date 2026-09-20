# plugin_interface.py
import queue
from abc import abstractmethod, ABC

from PyQt6 import QtWidgets
from PyQt6.QtWidgets import QVBoxLayout, QWidget, QScrollArea, QHBoxLayout, QMainWindow, QApplication

from index import Content_index
from index.Content_index import content_index
from my_abc import BaseInterfaceWidget

from my_abc.BaseService import BaseService
from public.entity.BaseWindow import BaseWindow
from public.entity.enum.Public_Enum import Frame_state, BaseInterfaceType, AppState
from public.function.promise.AsyPromise import AsyPromise
from theme.ThemeQt6 import ThemedWindow
from loguru import logger

class BaseModule(ABC):

    def __init__(self):
        self.interface_widget:BaseInterfaceWidget =None
        self.name =None
        self.title=None
        self.menu_name=None
        self.service:BaseService =None
        self.main_gui:BaseWindow =None
        self.app_state :AppState =None
        pass
    @abstractmethod
    def get_app_state(self) -> AppState:
        """返回该组件在程序什么状态才能被点击"""
        pass
    @abstractmethod
    def get_menu_name(self):
        """返回组件所属菜单{id:,text:} 在./config/gui_config.ini文件查看"""
        pass
    @abstractmethod
    def get_name(self):
        """返回组件名称"""
        pass
    @abstractmethod
    def get_title(self):
        """获取组件title"""
        pass
    @abstractmethod
    def create_service(self) -> BaseService:
        """创建并返回组件的相关服务"""
        pass

    @abstractmethod
    def get_interface_widget(self) -> BaseInterfaceWidget:
        """返回自定义界面构建器"""
        pass
    def close(self):
        """关闭所有窗口 若有"""
        if self.interface_widget is not None:
            self.interface_widget.close()
    def show(self):
        """显示页面"""
        if self.interface_widget is not None:
            # 重新加载页面 而不是加载之前的页面 start
            # self.interface_widget.close()
            self.interface_widget = None
            self.interface_widget=self.get_interface_widget()
            self.set_main_gui_to_children()
            # 重新加载页面 而不是加载之前的页面 end
            self.interface_widget.show()

    def hide(self):
        """隐藏页面"""
        if self.interface_widget is not None:
            self.interface_widget.hide()
    def setParent(self, parent):
        """设置父界面"""
        if self.interface_widget is not None:
            self.interface_widget.setParent(parent)
    def set_main_gui(self,main_gui:BaseWindow=None) -> None:
        # 获取主界面变量
        self.main_gui=main_gui
        pass
    def sync_menu_with_module(self):
        """同步顶部菜单和工具栏到当前模块。"""
        if self.main_gui is None or self.menu_name is None:
            return

        menu_id = self.menu_name.get("id")
        if menu_id is None:
            return

        current_menu_id = getattr(self.main_gui, "current_active_menu_id", None)
        if current_menu_id == menu_id:
            return

        switch_toolbar_content = getattr(self.main_gui, "switch_toolbar_content", None)
        if callable(switch_toolbar_content):
            switch_toolbar_content(menu_id)
    def set_main_gui_to_children(self):
        # 设置父界面给所有子界面
        if self.interface_widget.frame_obj is not None:
            self.interface_widget.frame_obj.set_main_gui(self.main_gui)
            self.interface_widget.frame_obj_state = Frame_state.Opening
        if self.interface_widget.left_frame_obj is not None:
            self.interface_widget.left_frame_obj.set_main_gui(self.main_gui)
            self.interface_widget.left_frame_obj_state = Frame_state.Opening
        if self.interface_widget.right_frame_obj is not None:
            self.interface_widget.right_frame_obj.set_main_gui(self.main_gui)
            self.interface_widget.right_frame_obj_state = Frame_state.Opening
        if self.interface_widget.bottom_frame_obj is not None:
            self.interface_widget.bottom_frame_obj.set_main_gui(self.main_gui)
            self.interface_widget.bottom_frame_obj_state = Frame_state.Opening
        pass
    # 点击的方法
    def click_method(self):
        """导航到新模块"""
        self.sync_menu_with_module()
        sync_workflow_step = getattr(
            self.main_gui, "sync_workflow_step_for_legacy_module", None
        )
        if callable(sync_workflow_step):
            sync_workflow_step(self)
        # 第一步：安全地清空所有旧内容
        if self.main_gui and hasattr(self.main_gui, 'tab_widget'):
            # 获取所有当前的 widgets
            widgets_to_remove = []
            for i in range(self.main_gui.tab_widget.count()):
                widget = self.main_gui.tab_widget.widget(i)
                if widget:
                    widgets_to_remove.append(widget)

            # 清理前先断开所有信号
            for widget in widgets_to_remove:
                try:
                    # 递归断开所有子 widget 的信号
                    self._disconnect_all_signals(widget)
                except Exception as e:
                    logger.error(f"断开信号失败: {e}")

            # 然后删除 widgets
            while self.main_gui.tab_widget.count() > 0:
                widget = self.main_gui.tab_widget.widget(0)
                self.main_gui.tab_widget.removeTab(0)
                if widget:
                    widget.deleteLater()

            # 清空活动模块列表
            self.main_gui.active_module_widgets.clear()

            # 强制立即刷新界面
            QApplication.processEvents()

        # 第二步：重新创建 interface_widget
        self.interface_widget = None
        self.interface_widget = self.get_interface_widget()

        # 第三步：执行服务启动和界面调整
        AsyPromise(self.start_service).then(
            lambda r: AsyPromise(self.adjustGUIPolicy).then()
        )
        # self.start_service()
        # self.adjustGUIPolicy()

    def _disconnect_all_signals(self, widget):
        """递归断开 widget 及其所有子 widget 的所有信号"""
        if widget is None:
            return

        try:
            # 获取所有子 widgets
            children = widget.findChildren(QtWidgets.QWidget)
            for child in children:
                try:
                    # 断开该 widget 的所有信号连接
                    child.blockSignals(True)
                    # Qt will disconnect signals when the widget is deleted.
                except:
                    pass

            # 断开父 widget 的所有信号连接
            try:
                widget.blockSignals(True)
                # Qt will disconnect signals when the widget is deleted.
            except:
                pass

        except Exception as e:
            logger.error(f"清理信号错误: {e}")
    def start_service(self,resolve,reject):
        """开始服务"""
        if self.service is not None:
            AsyPromise(self.service.start).then(
                lambda r:resolve(r)
            ).catch( lambda e:reject(e))
        else:
            resolve(None)

    def adjustGUIPolicy(self,resolve,reject):
        if self.interface_widget is None or self.interface_widget.type is None or self.interface_widget.frame_obj is None or self.main_gui is None:
            reject(None)
            return

        self.set_main_gui_to_children()
        # 根据type来确定相关策略
        if self.interface_widget.type == BaseInterfaceType.WIDGET or self.interface_widget.type == BaseInterfaceType.FRAME:

            # 创建新内容容器
            tab_content = QWidget()
            tab_content.setObjectName(f"content_{self.menu_name['text']}_{self.name}")
            tab_layout = QVBoxLayout(tab_content)
            tab_layout.setContentsMargins(0, 0, 0, 0)

            # 创建内容框架
            tab_frame = content_index()



            # left_layout =tab_frame.findChild(QVBoxLayout, "left_layout")
            # right_layout = tab_frame.findChild(QVBoxLayout, "right_layout")
            bottom_layout = tab_frame.findChild(QVBoxLayout, "bottom_layout")
            middle_layout =tab_frame.findChild(QVBoxLayout, "middle_layout")

            # scroll_left_layout =    BaseWindow.add_scroll_area_if_not_exists( tab_frame.findChild(QVBoxLayout,"left_layout"))
            # scroll_right_layout =   BaseWindow.add_scroll_area_if_not_exists( tab_frame.findChild(QVBoxLayout,"right_layout"))
            scroll_bottom_layout =  BaseWindow.add_scroll_area_if_not_exists( tab_frame.findChild(QVBoxLayout,"bottom_layout"))
            scroll_middle_layout =   BaseWindow.add_scroll_area_if_not_exists(tab_frame.findChild(QVBoxLayout,"middle_layout"))

            scroll_middle_layout.addWidget(self.interface_widget.frame_obj)
            # scroll_left_layout.addWidget(self.interface_widget.left_frame_obj)
            # scroll_right_layout.addWidget(self.interface_widget.right_frame_obj)
            scroll_bottom_layout.addWidget(self.interface_widget.bottom_frame_obj)
            self.interface_widget.setMinimumSize()
            # 拉伸系数的layout
            main_layout:QVBoxLayout = tab_frame.findChild(QVBoxLayout,"main_layout")
            top_layout:QHBoxLayout = tab_frame.findChild(QHBoxLayout,"top_layout")
            if self.interface_widget.bottom_frame_obj is None:
                "没有bottomlayout"
                main_layout.setStretchFactor(bottom_layout,0)
                main_layout.setStretchFactor(top_layout,6)
            else:
                main_layout.setStretchFactor(bottom_layout,2)
                main_layout.setStretchFactor(top_layout, 4)
            # if self.interface_widget.left_frame_obj is None and self.interface_widget.right_frame_obj is None:
            #     top_layout.setStretchFactor(left_layout,0)
            #     top_layout.setStretchFactor(middle_layout,6)
            #     top_layout.setStretchFactor(right_layout,0)
            # elif self.interface_widget.left_frame_obj is None:
            #     top_layout.setStretchFactor(left_layout, 0)
            #     top_layout.setStretchFactor(middle_layout, 5)
            #     top_layout.setStretchFactor(right_layout, 1)
            # elif self.interface_widget.right_frame_obj is None:
            #     top_layout.setStretchFactor(left_layout, 1)
            #     top_layout.setStretchFactor(middle_layout, 5)
            #     top_layout.setStretchFactor(right_layout, 0)
            # else:
            #     top_layout.setStretchFactor(left_layout, 1)
            #     top_layout.setStretchFactor(right_layout, 1)
            #     top_layout.setStretchFactor(middle_layout, 4)
            size_factor = 0.9
            if self.interface_widget.frame_obj is not None:
                self.interface_widget.frame_obj.menuBar().hide()
                self.interface_widget.frame_obj.statusBar().hide()
                self.interface_widget.frame_obj.resize(int(middle_layout.geometry().width()),
                                                       int(middle_layout.geometry().height()*size_factor-self.main_gui.statusBar().height()))


            # if self.interface_widget.left_frame_obj is not None:
            #     self.interface_widget.left_frame_obj.menuBar().hide()
            #     self.interface_widget.left_frame_obj.statusBar().hide()
            #     self.interface_widget.left_frame_obj.resize(
            #         int(left_layout.geometry().width()),
            #         int(left_layout.geometry().height()*size_factor-self.main_gui.statusBar().height()))
            #
            #
            # if self.interface_widget.right_frame_obj is not None:
            #     self.interface_widget.right_frame_obj.menuBar().hide()
            #     self.interface_widget.right_frame_obj.statusBar().hide()
            #     self.interface_widget.right_frame_obj.resize(int(right_layout.geometry().width()),
            #                                                  int(right_layout.geometry().height()*size_factor-self.main_gui.statusBar().height()))
            #
            #
            if self.interface_widget.bottom_frame_obj is not None:
                self.interface_widget.bottom_frame_obj.menuBar().hide()
                self.interface_widget.bottom_frame_obj.statusBar().hide()
                self.interface_widget.bottom_frame_obj.resize(int(bottom_layout.geometry().width()),
                                                              int(bottom_layout.geometry().height()*size_factor-self.main_gui.statusBar().height()))





            # 将 scroll_area 添加进去
            tab_layout.addWidget(tab_frame)
            # 添加到主界面
            self.main_gui.tab_widget.addTab(tab_content, "")
            self.main_gui.tab_widget.tabBar().hide()  # 隐藏tab栏
            self.main_gui.tab_widget.setCurrentWidget(tab_content)

            # 添加到活动模块列表
            if self not in self.main_gui.active_module_widgets:
                self.main_gui.active_module_widgets.append(self)
            pass
        else:
            self.show()
            #WINDOW
            flag = 10
            # ，每部分layout占多少
            if self.interface_widget.bottom_frame_obj is None:
                "没有bottomlayout"
                v_stretch = {'top': 5, 'bottom': 0}
            else:
                v_stretch = {'top': 4, 'bottom': 1}
            if self.interface_widget.left_frame_obj is None and self.interface_widget.right_frame_obj is None:
                h_stretch = {'left': 0, 'middle': 5, 'right': 0}
            elif self.interface_widget.left_frame_obj is None:
                h_stretch = {'left': 0, 'middle': 4, 'right': 1}
            elif self.interface_widget.right_frame_obj is None:
                h_stretch = {'left': 1, 'middle': 4, 'right':0}
            else:
                h_stretch = {'left': 1, 'middle': 3, 'right': 1}

            h_all = h_stretch['left']+h_stretch['middle']+h_stretch['right']
            v_all = v_stretch['top']+v_stretch['bottom']
            h_each = self.main_gui.centralWidget().geometry().width()//h_all
            v_each = (self.main_gui.centralWidget().geometry().height()-self.main_gui.statusBar().height())//v_all

            self.interface_widget.setMinimumSize(0, 0)
            if self.interface_widget.left_frame_obj is not None:
                self.interface_widget.left_frame_obj.menuBar().show()
                self.interface_widget.left_frame_obj.statusBar().show()
                self.interface_widget.left_frame_obj.setWindowTitle(self.title + 'left')
                self.interface_widget.left_frame_obj.setGeometry(
                    0,
                    self.main_gui.centralWidget().geometry().top() + self.main_gui.toolbar.geometry().height() + flag,
                    h_each * (h_stretch['left']),
                    v_each * (v_stretch['top'])

                )
            if self.interface_widget.frame_obj is not None:
                self.interface_widget.frame_obj.menuBar().show()
                self.interface_widget.frame_obj.statusBar().show()
                self.interface_widget.frame_obj.setWindowTitle(self.title+'content')
                # 让中间窗口占满整个区域
                self.interface_widget.frame_obj.setGeometry(
                    0,
                    self.main_gui.centralWidget().geometry().top() + self.main_gui.toolbar.geometry().height() + 10,
                    self.main_gui.centralWidget().width(),
                    self.main_gui.centralWidget().geometry().height() - self.main_gui.statusBar().height() - self.main_gui.toolbar.geometry().height()
                )
                # self.interface_widget.frame_obj.setGeometry(h_each*(h_stretch['left']),
                #                                             self.main_gui.centralWidget().geometry().top() + self.main_gui.toolbar.geometry().height() + flag,
                #                                             h_each*(h_stretch['middle']),
                #                                             v_each*(v_stretch['top']),
                #                                             )
            # if self.interface_widget.right_frame_obj is not None:
            #     self.interface_widget.right_frame_obj.menuBar().show()
            #     self.interface_widget.right_frame_obj.statusBar().show()
            #     self.interface_widget.right_frame_obj.setWindowTitle(self.title+'right')
            #     self.interface_widget.right_frame_obj.setGeometry(
            #         h_each * (h_stretch['middle'] +h_stretch['left']),
            #         self.main_gui.centralWidget().geometry().top() + self.main_gui.toolbar.geometry().height() + flag,
            #         h_each * (h_stretch['right']),
            #         v_each * (v_stretch['top']) ,
            #     )
            if self.interface_widget.bottom_frame_obj is not None:
                self.interface_widget.bottom_frame_obj.menuBar().show()
                self.interface_widget.bottom_frame_obj.statusBar().show()
                self.interface_widget.bottom_frame_obj.setWindowTitle(self.title+'bottom')
                self.interface_widget.bottom_frame_obj.setGeometry(
                    0,
                    self.main_gui.centralWidget().geometry().top()+v_each * (v_stretch['top'])+ self.main_gui.toolbar.geometry().height() ,
                    self.main_gui.centralWidget().width(),
                    v_each * (v_stretch['bottom'])-self.main_gui.toolbar.geometry().height() ,

                )



            # 添加窗口
            if self not in  self.main_gui.open_windows:
                self.main_gui.open_windows.append(self)

            pass

        resolve()
        pass
