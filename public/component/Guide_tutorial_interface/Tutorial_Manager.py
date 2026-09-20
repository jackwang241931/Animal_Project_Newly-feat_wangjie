
from PyQt6.QtCore import QObject, QEvent, QTimer, pyqtSignal
from PyQt6.QtWidgets import QMainWindow
from loguru import logger

from public.component.Guide_tutorial_interface.Arrow_guidance import ArrowOverlayWidget
from public.component.Guide_tutorial_interface.Bubble_guidance import ResponsiveBubbleTooltip
from public.component.Guide_tutorial_interface.Mask_guidance import OverlayWidget
from public.entity.enum.Public_Enum import Tutorial_Type


class TutorialManager(QObject):
    """教程管理器 - 支持首次运行检测"""


    # 定义信号
    tutorial_completed = pyqtSignal(str)  # 教程完成信号

    def __init__(self, main_window,page_name, guide_type=Tutorial_Type.OVERLAY_GUIDE, settings_manager=None):
        super().__init__()
        self.main_window:QMainWindow = main_window
        self.page_name = page_name
        self.current_step = 0
        self.steps = []
        self.overlay = None
        self.guide_type = guide_type
        self.settings_manager = settings_manager

        # 监听主窗口的移动和大小变化
        self.main_window.installEventFilter(self)

    def eventFilter(self, obj, event):
        """监听主窗口事件"""
        if hasattr(self, 'main_window') and self.main_window is not None:
            if obj == self.main_window and self.overlay:
                if event.type() in [QEvent.Type.Resize, QEvent.Type.Move]:
                    self.update_overlay_geometry()
        return super().eventFilter(obj, event)

    def update_overlay_geometry(self):
        """更新遮罩层的几何属性"""
        if self.overlay and self.main_window:
            if self.guide_type == Tutorial_Type.BUBBLE_GUIDE:
                # 气泡提示需要重新定位
                if hasattr(self.overlay, 'position_tooltip'):
                    QTimer.singleShot(10, self.overlay.position_tooltip)
            else:
                # 其他类型需要调整大小
                self.overlay.resize(self.main_window.size())
                self.overlay.move(0, 0)
                self.overlay.update()

    def add_step(self, widget, text):
        self.steps.append((widget, text))

    def set_guide_type(self, guide_type):
        """设置引导类型"""
        self.guide_type = guide_type

    def start_tutorial(self):
        """开始教程"""
        self.current_step = 0
        self.show_current_step()
    def clear(self):
        """
        清空
        :return:
        """
        self.current_step = 0
        self.steps = []

        if self.overlay:
            self.overlay.close()
            self.overlay=None
    def show_current_step(self):
        if self.current_step >= len(self.steps):
            self.end_tutorial()
            return

        widget, text = self.steps[self.current_step]

        if self.overlay:
            self.overlay.close()

        try:
            if self.guide_type == Tutorial_Type.OVERLAY_GUIDE:
                self.overlay = OverlayWidget(widget, text, self.main_window)
            elif self.guide_type == Tutorial_Type.BUBBLE_GUIDE:
                self.overlay = ResponsiveBubbleTooltip(widget, text, self.main_window)
                # 为气泡提示设置点击事件
                self.overlay.mousePressEvent = self.next_step
                # 为按钮设置点击事件
                if hasattr(self.overlay, 'next_btn'):
                    self.overlay.next_btn.clicked.connect(lambda: self.next_step(None))
                if hasattr(self.overlay, 'skip_btn'):
                    self.overlay.skip_btn.clicked.connect(self.end_tutorial)
            elif self.guide_type == Tutorial_Type.ARROW_GUIDE:
                self.overlay = ArrowOverlayWidget(widget, text, self.main_window)

            # 设置初始大小和位置
            self.update_overlay_geometry()
            self.overlay.show()

            # 为非气泡类型添加点击事件
            if self.guide_type != Tutorial_Type.BUBBLE_GUIDE:
                self.overlay.mousePressEvent = self.next_step

        except Exception as e:
            logger.error(f"显示引导步骤错误: {e}")
            self.end_tutorial()

    def next_step(self, event):
        self.current_step += 1
        self.show_current_step()

    def end_tutorial(self):
        """结束教程"""
        if self.overlay:
            try:
                self.overlay.close()
            except Exception as e:
                logger.error(f"关闭引导错误: {e}")
            finally:
                self.overlay = None

        # 标记页面已访问
        if self.settings_manager:
            self.settings_manager.mark_page_visited(self.page_name)

        # 发出教程完成信号
        self.tutorial_completed.emit(self.page_name)