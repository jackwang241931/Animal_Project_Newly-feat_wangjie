import sys
from PyQt6.QtWidgets import (QApplication, QDialog, QVBoxLayout, QHBoxLayout,
                             QLabel, QProgressBar, QPushButton, QListView, QMessageBox, QWidget, QFrame, QScrollArea,
                             QGridLayout, QSplitter)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal, QObject
from PyQt6.QtGui import QFont, QStandardItemModel, QStandardItem


class AnimatedLoadingDialog(QDialog):
    # 定义信号，确保线程安全
    progress_updated = pyqtSignal(int)
    insert_data_signal = pyqtSignal(str)
    task_completed_signal = pyqtSignal()
    timeout_signal = pyqtSignal()  # 超时信号

    def __init__(self, countdown_seconds=10, message="正在加载数据...", title="系统加载中",
                 show_listview=True, calibration_dialog=None):
        super().__init__()
        self.countdown_seconds = countdown_seconds
        self.current_seconds = countdown_seconds
        self.message = message
        self.title = title
        self.show_listview = show_listview
        self.calibration_dialog = calibration_dialog

        self.manual_progress = 0
        self.progress_max = 100
        self.use_manual_progress = False
        self.task_completed = False
        self.is_closing = False

        # 60秒强制进入标志
        self.force_enter_seconds = 60
        self.force_entered = False  # 是否已经强制进入过

        self.init_ui()
        self.init_listview()
        self.connect_signals()
        self.start_countdown()
        if not self.use_manual_progress:
            self.start_progress_animation()

    def connect_signals(self):
        """连接信号到槽函数，确保线程安全"""
        self.progress_updated.connect(self._update_progress_ui)
        self.insert_data_signal.connect(self._insert_data_ui)
        self.task_completed_signal.connect(self._complete_task_ui)

    def init_ui(self):
        self.setWindowTitle(self.title)

        # 根据当前屏幕大小动态调整窗口尺寸
        self._adjust_window_size()

        self.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.FramelessWindowHint)
        self.setStyleSheet("""
            QDialog {
                background-color: #f0f0f0;
                border: 2px solid #007acc;
                border-radius: 10px;
            }
            QLabel {
                color: #333;
            }
            QProgressBar {
                border: 2px solid #007acc;
                border-radius: 5px;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #007acc;
                border-radius: 3px;
            }
            QListView {
                border: 1px solid #ccc;
                border-radius: 5px;
                background-color: white;
                selection-background-color: #007bff;
            }
            QSplitter::handle {
                background-color: #007acc;
                border: 1px solid #005a9f;
                border-radius: 2px;
            }
           
            QSplitter::handle:hover {
                background-color: #0099ff;
            }
            QSplitter::handle:pressed {
                background-color: #004d7a;
            }
        """)

        # 主布局
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(20, 20, 20, 20)

        # 创建主分割器
        self.main_splitter = QSplitter(Qt.Orientation.Horizontal)
        self.main_splitter.setChildrenCollapsible(False)  # 防止子部件被完全折叠

        # ==================== 左侧区域（原来的加载对话框）====================
        left_widget = self._create_left_panel()

        # 添加左侧面板到分割器
        self.main_splitter.addWidget(left_widget)

        # ==================== 右侧区域（CalibrationDialog）====================
        if self.calibration_dialog:
            right_widget = self._create_calibration_panel()
            if right_widget:
                self.main_splitter.addWidget(right_widget)
                # 设置初始分割比例 (左:右 = 1:1)
                self.main_splitter.setSizes([500, 500])

        # 将分割器添加到主布局
        main_layout.addWidget(self.main_splitter)

        self.center_on_screen()

    def _adjust_window_size(self):
        """
        根据屏幕大小动态调整窗口尺寸
        """
        # 获取当前屏幕信息
        screen = QApplication.primaryScreen()
        screen_geometry = screen.availableGeometry()
        screen_width = screen_geometry.width()
        screen_height = screen_geometry.height()

        # 设置窗口大小占屏幕的比例
        if self.calibration_dialog:
            # 有CalibrationDialog时使用更大的尺寸
            if self.show_listview:
                # 占屏幕宽度的80%，高度的70%
                width = int(screen_width * 0.8)
                height = int(screen_height * 0.7)
            else:
                # 占屏幕宽度的70%，高度的50%
                width = int(screen_width * 0.7)
                height = int(screen_height * 0.5)
        else:
            # 没有CalibrationDialog时使用较小尺寸
            if self.show_listview:
                # 占屏幕宽度的40%，高度的60%
                width = int(screen_width * 0.4)
                height = int(screen_height * 0.6)
            else:
                # 占屏幕宽度的25%，高度的25%
                width = int(screen_width * 0.25)
                height = int(screen_height * 0.25)

        # 设置最小和最大尺寸限制
        min_width = 400
        min_height = 200
        max_width = int(screen_width * 0.9)
        max_height = int(screen_height * 0.8)

        # 应用尺寸限制
        width = max(min_width, min(width, max_width))
        height = max(min_height, min(height, max_height))

        self.setFixedSize(width, height)

    # 添加一个获取屏幕信息的方法
    def get_screen_info(self):
        """
        获取当前屏幕信息

        Returns:
            dict: 包含屏幕信息的字典
        """
        screen = QApplication.primaryScreen()
        screen_geometry = screen.availableGeometry()

        return {
            'width': screen_geometry.width(),
            'height': screen_geometry.height(),
            'dpi': screen.logicalDotsPerInch(),
            'device_pixel_ratio': screen.devicePixelRatio(),
            'name': screen.name()
        }

    # 添加一个支持多屏幕的方法
    def _get_current_screen(self):
        """
        获取当前窗口所在的屏幕

        Returns:
            QScreen: 当前屏幕对象
        """
        # 获取所有屏幕
        screens = QApplication.screens()

        if len(screens) == 1:
            return screens[0]

        # 如果有多个屏幕，找到包含窗口中心点的屏幕
        window_center = self.frameGeometry().center()

        for screen in screens:
            if screen.geometry().contains(window_center):
                return screen

        # 如果没有找到，返回主屏幕
        return QApplication.primaryScreen()

    def _adjust_window_size_multi_screen(self):
        """
        支持多屏幕的窗口大小调整方法
        """
        # 获取当前屏幕
        current_screen = self._get_current_screen()
        screen_geometry = current_screen.availableGeometry()
        screen_width = screen_geometry.width()
        screen_height = screen_geometry.height()

        # 考虑DPI缩放
        device_pixel_ratio = current_screen.devicePixelRatio()

        # 设置窗口大小占屏幕的比例
        if self.calibration_dialog:
            if self.show_listview:
                width = int(screen_width * 0.8)
                height = int(screen_height * 0.7)
            else:
                width = int(screen_width * 0.7)
                height = int(screen_height * 0.5)
        else:
            if self.show_listview:
                width = int(screen_width * 0.4)
                height = int(screen_height * 0.6)
            else:
                width = int(screen_width * 0.25)
                height = int(screen_height * 0.25)

        # 应用DPI缩放
        width = int(width / device_pixel_ratio)
        height = int(height / device_pixel_ratio)

        # 设置最小和最大尺寸限制
        min_width = int(400 / device_pixel_ratio)
        min_height = int(200 / device_pixel_ratio)
        max_width = int(screen_width * 0.9 / device_pixel_ratio)
        max_height = int(screen_height * 0.8 / device_pixel_ratio)

        # 应用尺寸限制
        width = max(min_width, min(width, max_width))
        height = max(min_height, min(height, max_height))

        self.setFixedSize(width, height)

    def _create_left_panel(self):
        """
        创建左侧面板（原loading dialog内容）

        Returns:
            QWidget: 左侧面板
        """
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)
        left_layout.setSpacing(15)
        left_layout.setContentsMargins(10, 10, 10, 10)

        # 标题
        self.title_label = QLabel(self.title)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title_font = QFont()
        title_font.setPointSize(16)
        title_font.setBold(True)
        self.title_label.setFont(title_font)

        # 消息
        self.message_label = QLabel(self.message)
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        message_font = QFont()
        message_font.setPointSize(10)
        self.message_label.setFont(message_font)

        # 进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setMinimum(0)
        self.progress_bar.setMaximum(100)
        self.progress_bar.setValue(0)

        # 添加基本控件到布局
        left_layout.addWidget(self.title_label)
        left_layout.addWidget(self.message_label)
        left_layout.addWidget(self.progress_bar)

        # QListView（可控制显示/隐藏）
        self.list_view = QListView()
        self.list_view.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.list_view.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)

        if self.show_listview:
            left_layout.addWidget(self.list_view)
        else:
            self.list_view.hide()

        # 倒计时和取消按钮的水平布局
        bottom_layout = QHBoxLayout()

        self.countdown_label = QLabel(f"剩余时间: {self.current_seconds}s")
        countdown_font = QFont()
        countdown_font.setPointSize(10)
        self.countdown_label.setFont(countdown_font)

        self.cancel_button = QPushButton("取消")
        self.cancel_button.setFixedSize(60, 30)
        self.cancel_button.clicked.connect(self.reject)

        bottom_layout.addWidget(self.countdown_label)
        bottom_layout.addStretch()
        bottom_layout.addWidget(self.cancel_button)

        # 添加底部布局
        left_layout.addLayout(bottom_layout)

        return left_widget

    def _create_calibration_panel(self):
        """
        从CalibrationDialog创建右侧面板

        Returns:
            QWidget: 包含标定对话框的面板
        """
        if not self.calibration_dialog:
            return None

        # 创建容器
        panel = QWidget()
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(10, 10, 10, 10)
        panel_layout.setSpacing(0)

        # 直接将CalibrationDialog添加到面板中
        # 移除CalibrationDialog的窗口属性，使其成为面板的子widget
        self.calibration_dialog.setWindowFlags(Qt.WindowType.Widget)
        self.calibration_dialog.setParent(panel)
        panel_layout.addWidget(self.calibration_dialog)

        return panel





    def init_listview(self):
        """初始化ListView的数据模型"""
        self.list_model = QStandardItemModel()
        self.list_view.setModel(self.list_model)

    def center_on_screen(self):
        """
        将窗口居中显示在屏幕上
        """
        screen = QApplication.primaryScreen()
        screen_geometry = screen.availableGeometry()
        dialog_geometry = self.frameGeometry()
        center_point = screen_geometry.center()
        dialog_geometry.moveCenter(center_point)
        self.move(dialog_geometry.topLeft())

    def start_countdown(self):
        self.timer = QTimer()
        self.timer.timeout.connect(self.update_countdown)
        self.timer.start(1000)

    def start_progress_animation(self):
        """启动进度条动画（基于倒计时）"""
        if not self.use_manual_progress:
            self.progress_timer = QTimer()
            self.progress_timer.timeout.connect(self.update_progress)
            self.progress_timer.start(100)  # 每100ms更新一次进度条

    def update_countdown(self):
        if self.is_closing:
            return

        self.current_seconds -= 1
        self.countdown_label.setText(f"剩余时间: {self.current_seconds}s")

        # 计算已经过去了多少秒
        elapsed = self.countdown_seconds - self.current_seconds

        # 超过60秒且任务未完成 → 强制进入主界面
        if elapsed >= self.force_enter_seconds and not self.task_completed and not self.force_entered:
            self.force_entered = True
            self.message_label.setText("后台继续启动中，正在进入监控页面...")
            QTimer.singleShot(500, self._safe_accept)
            return

        # 根据剩余时间更新消息
        if self.current_seconds <= 3 and not self.task_completed:
            self.message_label.setText("即将超时...")
        elif self.current_seconds <= 5 and not self.task_completed:
            self.message_label.setText("正在处理最后步骤...")

        # 倒计时结束处理（原逻辑不变）
        if self.current_seconds <= 0:
            self.timer.stop()
            if hasattr(self, 'progress_timer'):
                self.progress_timer.stop()

            if not self.task_completed:
                self.show_timeout_error()
            else:
                self.progress_bar.setValue(100)
                self.message_label.setText("加载完成！")
                QTimer.singleShot(500, self._safe_accept)

    def update_progress(self):
        """更新进度条（基于倒计时）"""
        if not self.use_manual_progress and not self.is_closing:
            elapsed_time = self.countdown_seconds - self.current_seconds
            progress = int((elapsed_time / self.countdown_seconds) * 100)
            self.progress_bar.setValue(min(progress, 100))

    def show_timeout_error(self):
        """显示超时错误弹窗"""
        if self.is_closing:
            return

        # 创建消息框
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Icon.Critical)
        msg_box.setWindowTitle(f"{self.title}超时")
        msg_box.setText(f"{self.message}超时！")
        msg_box.setInformativeText(f"{self.title}未能在规定时间内完成。")
        msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
        msg_box.setDefaultButton(QMessageBox.StandardButton.Ok)

        # 确保消息框显示在前面
        msg_box.setWindowFlags(Qt.WindowType.Dialog | Qt.WindowType.WindowStaysOnTopHint)

        # 设置弹窗样式
        msg_box.setStyleSheet("""
            QMessageBox {
                background-color: #f0f0f0;
            }
            QMessageBox QLabel {
                color: #333;
                font-size: 12px;
            }
            QPushButton {
                background-color: #007acc;
                color: white;
                border: none;
                padding: 8px 16px;
                border-radius: 4px;
                min-width: 60px;
                font-size: 12px;
            }
            QPushButton:hover {
                background-color: #005c99;
            }
        """)

        # 显示弹窗并等待用户点击
        result = msg_box.exec()
        self.timeout_signal.emit()
        self._safe_reject()

    def _safe_accept(self):
        """安全地接受对话框"""
        if not self.is_closing:
            self.is_closing = True
            self.accept()

    def _safe_reject(self):
        """安全地拒绝对话框"""
        if not self.is_closing:
            self.is_closing = True
            self.reject()
    #--------------------------分隔器的相关方法-----------------------------
    def set_splitter_sizes(self, left_ratio=1, right_ratio=1):
        """
        设置分割器的分割比例

        Args:
            left_ratio (int): 左侧比例
            right_ratio (int): 右侧比例
        """
        if self.main_splitter.count() >= 2:
            total_width = self.width() - 40  # 减去边距
            left_width = int(total_width * left_ratio / (left_ratio + right_ratio))
            right_width = total_width - left_width
            self.main_splitter.setSizes([left_width, right_width])

    def get_splitter_sizes(self):
        """
        获取当前分割器的尺寸

        Returns:
            list: 分割器各部分的尺寸列表
        """
        return self.main_splitter.sizes()

    def save_splitter_state(self):
        """
        保存分割器状态

        Returns:
            bytes: 分割器状态数据
        """
        return self.main_splitter.saveState()

    def restore_splitter_state(self, state):
        """
        恢复分割器状态

        Args:
            state (bytes): 分割器状态数据
        """
        return self.main_splitter.restoreState(state)

    def set_splitter_collapsible(self, index, collapsible=True):
        """
        设置分割器中某个部件是否可折叠

        Args:
            index (int): 部件索引
            collapsible (bool): 是否可折叠
        """
        widget = self.main_splitter.widget(index)
        if widget:
            self.main_splitter.setCollapsible(index, collapsible)

    # ==================== 插入CalibrationDialog的方法 ====================

    def insert_calibration_dialog(self, calibration_dialog):
        """
        动态插入CalibrationDialog到右侧面板

        Args:
            calibration_dialog: CalibrationDialog实例
        """
        if not self.calibration_dialog:
            self.calibration_dialog = calibration_dialog

            # 创建右侧面板
            right_widget = self._create_calibration_panel()

            if right_widget:
                # 添加到分割器
                self.main_splitter.addWidget(right_widget)

                # 设置分割比例 (左:右 = 1:1)
                self.main_splitter.setSizes([500, 500])

            # 重新调整窗口大小
            self._adjust_window_size()
            self.center_on_screen()

    # ==================== QListView 相关接口 ====================

    def show_listview(self):
        """显示QListView"""
        if not self.show_listview:
            self.show_listview = True
            self.list_view.show()
            # 重新调整窗口大小
            self._adjust_window_size()
            self.center_on_screen()

    def hide_listview(self):
        """隐藏QListView"""
        if self.show_listview:
            self.show_listview = False
            self.list_view.hide()
            # 重新调整窗口大小
            self._adjust_window_size()
            self.center_on_screen()

    def insert_list_data(self, data):
        """
        插入数据到QListView最前面（线程安全）

        Args:
            data (str): 要插入的数据
        """
        self.insert_data_signal.emit(str(data))

    def _insert_data_ui(self, data):
        """在主线程中执行UI更新"""
        if self.is_closing:
            return

        item = QStandardItem(data)
        self.list_model.insertRow(0, item)
        self.list_view.scrollToTop()

    def insert_multiple_list_data(self, data_list):
        """
        批量插入多个数据到QListView

        Args:
            data_list (list): 要插入的数据列表
        """
        for data in reversed(data_list):
            self.insert_list_data(data)

    def clear_list_data(self):
        """清空QListView中的所有数据"""
        if not self.is_closing:
            self.list_model.clear()

    def get_all_list_data(self):
        """
        获取QListView中的所有数据

        Returns:
            list: 包含所有数据的列表
        """
        data_list = []
        for row in range(self.list_model.rowCount()):
            item = self.list_model.item(row)
            if item:
                data_list.append(item.text())
        return data_list

    # ==================== 进度条手动控制接口 ====================

    def set_progress_range(self, minimum=0, maximum=100):
        """
        设置进度条的范围

        Args:
            minimum (int): 最小值
            maximum (int): 最大值
        """
        self.progress_bar.setMinimum(minimum)
        self.progress_bar.setMaximum(maximum)
        self.progress_max = maximum
        self.use_manual_progress = True

        if hasattr(self, 'progress_timer'):
            self.progress_timer.stop()

    def set_progress_value(self, value):
        """
        设置进度条的当前值（线程安全）

        Args:
            value (int): 进度值
        """
        if self.is_closing:
            return

        self.manual_progress = value
        self.use_manual_progress = True
        self.progress_updated.emit(value)

    def _update_progress_ui(self, value):
        """在主线程中更新进度条UI"""
        if self.is_closing:
            return

        self.progress_bar.setValue(value)

        if value >= self.progress_max and not self.task_completed:
            self.task_completed_signal.emit()

    def update_progress_value(self, increment=1):
        """
        更新进度条的值（增加指定数量）（线程安全）

        Args:
            increment (int): 增加的数量，默认为1
        """
        if self.is_closing:
            return

        self.manual_progress += increment
        self.manual_progress = min(self.manual_progress, self.progress_max)
        self.use_manual_progress = True
        self.progress_updated.emit(self.manual_progress)

    def complete_task(self):
        """手动标记任务完成（线程安全）"""
        if not self.is_closing and not self.task_completed:
            self.task_completed_signal.emit()

    def _complete_task_ui(self):
        """在主线程中执行任务完成的UI更新"""
        if self.is_closing or self.task_completed:
            return

        self.task_completed = True
        self.message_label.setText("任务完成！")

        if hasattr(self, 'timer'):
            self.timer.stop()

        QTimer.singleShot(500, self._safe_accept)

    def get_progress_value(self):
        """
        获取当前进度条的值

        Returns:
            int: 当前进度值
        """
        return self.progress_bar.value()

    def reset_progress(self):
        """重置进度条到0"""
        if not self.is_closing:
            self.manual_progress = 0
            self.progress_bar.setValue(0)
            self.task_completed = False

    def is_task_completed(self):
        """
        检查任务是否完成

        Returns:
            bool: 任务完成状态
        """
        return self.task_completed

    def closeEvent(self, event):
        """重写关闭事件"""
        self.is_closing = True

        if hasattr(self, 'timer'):
            self.timer.stop()
        if hasattr(self, 'progress_timer'):
            self.progress_timer.stop()
        super().closeEvent(event)


# 测试用例
def main():
    app = QApplication(sys.argv)

    # 测试场景1：超时情况（不调用complete_task）

    dialog = AnimatedLoadingDialog(
         countdown_seconds=5,  # 短倒计时用于测试
        message="正在处理数据...",
        title="数据处理中",
        show_listview=True
    )

    # 设置手动进度控制但不完成任务
    dialog.set_progress_range(0, 100)
    dialog.set_progress_value(50)  # 只完成50%

    dialog.insert_list_data("开始处理...")
    dialog.insert_list_data("进行中...")

    # 不调用 complete_task()，让其超时

    result = dialog.exec()


    sys.exit(app.exec())


if __name__ == "__main__":
    main()
