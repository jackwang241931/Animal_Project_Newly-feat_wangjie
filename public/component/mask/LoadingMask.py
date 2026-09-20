import sys
import time

from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QRect, pyqtSignal, QThread
from PyQt6.QtGui import QPainter, QColor, QFont, QMovie
from PyQt6.QtWidgets import QVBoxLayout, QProgressBar, QLabel, QApplication, QPushButton, QWidget, QMainWindow

from theme.ThemeQt6 import ThemedWidget


class LoadingMask(QWidget):
    """简单的加载遮罩"""

    def __init__(self, parent=None, text="加载中..."):
        super().__init__(parent)
        self.setText(text)
        self.setupUI()

    def setText(self, text):
        """设置加载文本"""
        self.loading_text = text

    def setupUI(self):
        """设置UI"""
        # 设置窗口属性
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # 设置样式
        self.setStyleSheet("""
            QWidget {
                background-color: rgba(0, 0, 0, 120);
                border-radius: 10px;
            }
            QLabel {
                color: white;
                background-color: transparent;
                font-size: 16px;
                font-weight: bold;
            }
        """)

        # 创建布局
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 创建进度条
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)  # 无限进度条
        self.progress_bar.setFixedSize(200, 20)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                border: 2px solid white;
                border-radius: 10px;
                background-color: transparent;
                text-align: center;
            }
            QProgressBar::chunk {
                background-color: #4CAF50;
                border-radius: 8px;
            }
        """)

        # 创建文本标签
        self.label = QLabel(self.loading_text)
        self.label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 添加到布局
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.label)

        self.setLayout(layout)

        # 设置固定大小
        self.setFixedSize(250, 100)

    def showEvent(self, event):
        """显示事件"""
        super().showEvent(event)
        if self.parent():
            # 居中显示
            parent_rect = self.parent().rect()
            self.move(
                parent_rect.center().x() - self.width() // 2,
                parent_rect.center().y() - self.height() // 2
            )

    def updateText(self, text):
        """更新加载文本"""
        self.label.setText(text)


class AnimatedLoadingMask(QWidget):
    """带动画效果的加载遮罩"""

    def __init__(self, parent=None, text="加载中..."):
        super().__init__(parent)
        self.loading_text = text
        self.rotation_angle = 0
        self.setupUI()
        self.setupAnimation()

    def setupUI(self):
        """设置UI"""
        # self.setWindowFlags(Qt.WindowType.FramelessWindowHint|Qt.WindowType.WindowStaysOnTopHint)
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # 如果有父窗口，设置为覆盖整个父窗口
        if self.parent():
            self.resize(self.parent().size())
        else:
            self.resize(400, 300)

    def setupAnimation(self):
        """设置动画"""
        self.timer = QTimer()
        self.timer.timeout.connect(self.rotate)
        self.timer.start(50)  # 50ms更新一次

        # 淡入动画
        self.fade_animation = QPropertyAnimation(self, b"windowOpacity")
        self.fade_animation.setDuration(300)
        self.fade_animation.setStartValue(0.0)
        self.fade_animation.setEndValue(1.0)
        self.fade_animation.setEasingCurve(QEasingCurve.Type.OutQuad)

    def rotate(self):
        """旋转动画"""
        self.rotation_angle = (self.rotation_angle + 10) % 360
        self.update()

    def paintEvent(self, event):
        """绘制事件"""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        # 绘制半透明背景
        painter.fillRect(self.rect(), QColor(0, 0, 0, 120))

        # 计算中心位置
        center_x = self.width() // 2
        center_y = self.height() // 2

        # 绘制旋转的加载圆圈
        painter.save()
        painter.translate(center_x, center_y - 20)
        painter.rotate(self.rotation_angle)

        # 绘制圆圈
        radius = 30
        painter.setPen(Qt.PenStyle.NoPen)
        for i in range(8):
            angle = i * 45
            opacity = 255 - (i * 30)
            color = QColor(255, 255, 255, max(50, opacity))
            painter.setBrush(color)

            x = radius * 0.7
            painter.drawEllipse(int(x - 4), -4, 8, 8)
            painter.rotate(45)

        painter.restore()

        # 绘制文本
        painter.setPen(QColor(255, 255, 255))
        font = QFont()
        font.setPointSize(14)
        font.setBold(True)
        painter.setFont(font)

        text_rect = QRect(0, center_y + 20, self.width(), 30)
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignCenter, self.loading_text)

    def show(self):
        """显示遮罩"""
        super().show()
        self.fade_animation.start()

    def hide(self):
        """隐藏遮罩"""
        self.timer.stop()
        super().hide()

    def updateText(self, text):
        """更新文本"""
        self.loading_text = text
        self.update()


class GifLoadingMask(QWidget):
    """使用GIF动画的加载遮罩"""

    def __init__(self, parent=None, text="加载中...", gif_path=None):
        super().__init__(parent)
        self.loading_text = text
        self.gif_path = gif_path
        self.setupUI()

    def setupUI(self):
        """设置UI"""
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)

        # 设置样式
        self.setStyleSheet("""
            QWidget {
                background-color: rgba(0, 0, 0, 150);
            }
            QLabel {
                color: white;
                background-color: transparent;
                font-size: 16px;
                font-weight: bold;
            }
        """)

        # 创建布局
        layout = QVBoxLayout()
        layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 创建GIF标签
        self.gif_label = QLabel()
        self.gif_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.gif_label.setFixedSize(80, 80)

        # 如果提供了GIF路径，使用GIF；否则创建简单的加载图标
        if self.gif_path:
            self.movie = QMovie(self.gif_path)
            self.gif_label.setMovie(self.movie)
        else:
            # 创建简单的加载文本
            self.gif_label.setText("⟳")
            self.gif_label.setStyleSheet("""
                QLabel {
                    font-size: 48px;
                    color: #4CAF50;
                }
            """)

        # 创建文本标签
        self.text_label = QLabel(self.loading_text)
        self.text_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 添加到布局
        layout.addWidget(self.gif_label)
        layout.addWidget(self.text_label)

        self.setLayout(layout)

        # 如果有父窗口，设置为覆盖整个父窗口
        if self.parent():
            self.resize(self.parent().size())
        else:
            self.resize(400, 300)

    def show(self):
        """显示遮罩"""
        super().show()
        if hasattr(self, 'movie'):
            self.movie.start()

    def hide(self):
        """隐藏遮罩"""
        if hasattr(self, 'movie'):
            self.movie.stop()
        super().hide()

    def updateText(self, text):
        """更新文本"""
        self.text_label.setText(text)
class LoadingContext:
    """加载上下文管理器"""

    def __init__(self, parent, text="加载中...", mask_type="simple"):
        self.parent = parent
        self.text = text
        self.mask_type = mask_type
        self.mask = None

    def __enter__(self):
        """进入上下文"""
        if self.mask_type == "simple":
            self.mask = LoadingMask(self.parent, self.text)
        elif self.mask_type == "animated":
            self.mask = AnimatedLoadingMask(self.parent, self.text)
        elif self.mask_type == "gif":
            self.mask = GifLoadingMask(self.parent, self.text)

        if self.mask:
            self.mask.show()

        return self.mask

    def __exit__(self, exc_type, exc_val, exc_tb):
        """退出上下文"""
        if self.mask:
            self.mask.hide()
            self.mask = None


# 使用示例
class ContextExample(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('上下文管理器示例')
        self.setGeometry(300, 300, 400, 300)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        layout = QVBoxLayout()
        central_widget.setLayout(layout)

        btn = QPushButton('使用上下文管理器')
        btn.clicked.connect(self.use_context_manager)
        layout.addWidget(btn)

    def use_context_manager(self):
        """使用上下文管理器"""
        # 在with语句中自动管理加载遮罩
        with LoadingContext(self, "正在处理...", "simple") as mask:
            # 模拟耗时操作
            time.sleep(3)
            mask.updateText("即将完成...")
            time.sleep(1)

        # 遮罩会自动关闭
        print("任务完成!")


class WorkerThread(QThread):
    """工作线程"""

    progress_updated = pyqtSignal(str)  # 进度更新信号
    finished = pyqtSignal(str)  # 完成信号

    def __init__(self, task_type="simple"):
        super().__init__()
        self.task_type = task_type

    def run(self):
        """执行任务"""
        try:
            if self.task_type == "simple":
                self.simple_task()
            elif self.task_type == "complex":
                self.complex_task()
            elif self.task_type == "download":
                self.download_task()

        except Exception as e:
            self.finished.emit(f"任务失败: {str(e)}")

    def simple_task(self):
        """简单任务"""
        for i in range(5):
            time.sleep(1)
            self.progress_updated.emit(f"处理步骤 {i + 1}/5")
        self.finished.emit("简单任务完成!")

    def complex_task(self):
        """复杂任务"""
        steps = ["初始化数据", "连接数据库", "处理数据", "生成报告", "保存结果"]

        for i, step in enumerate(steps):
            self.progress_updated.emit(f"{step}...")
            time.sleep(2)  # 模拟耗时操作

        self.finished.emit("复杂任务完成!")

    def download_task(self):
        """下载任务"""
        files = ["file1.txt", "file2.pdf", "file3.jpg", "file4.zip"]

        for i, file in enumerate(files):
            self.progress_updated.emit(f"下载 {file}...")
            time.sleep(1.5)  # 模拟下载时间

        self.finished.emit("所有文件下载完成!")
class MainWindow(QMainWindow):
    """主窗口"""

    def __init__(self):
        super().__init__()
        self.setupUI()
        self.loading_mask = None
        self.worker_thread = None

    def setupUI(self):
        """设置UI"""
        self.setWindowTitle('PyQt6 加载遮罩示例')
        self.setGeometry(300, 300, 600, 400)

        # 创建中央widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # 创建布局
        layout = QVBoxLayout()
        central_widget.setLayout(layout)

        # 创建控件
        self.status_label = QLabel('准备开始任务...')
        self.status_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.status_label.setStyleSheet("""
            QLabel {
                font-size: 16px;
                padding: 20px;
                background-color: #f0f0f0;
                border-radius: 5px;
                margin: 10px;
            }
        """)

        # 按钮
        self.simple_btn = QPushButton('简单加载遮罩')
        self.animated_btn = QPushButton('动画加载遮罩')
        self.gif_btn = QPushButton('GIF加载遮罩')
        self.complex_btn = QPushButton('复杂任务加载')
        self.download_btn = QPushButton('下载任务加载')

        # 设置按钮样式
        button_style = """
            QPushButton {
                font-size: 14px;
                padding: 10px;
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 5px;
                margin: 5px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:pressed {
                background-color: #3d8b40;
            }
        """

        for btn in [self.simple_btn, self.animated_btn, self.gif_btn,
                    self.complex_btn, self.download_btn]:
            btn.setStyleSheet(button_style)

        # 添加到布局
        layout.addWidget(self.status_label)
        layout.addWidget(self.simple_btn)
        layout.addWidget(self.animated_btn)
        layout.addWidget(self.gif_btn)
        layout.addWidget(self.complex_btn)
        layout.addWidget(self.download_btn)

        # 连接信号
        self.simple_btn.clicked.connect(self.show_simple_loading)
        self.animated_btn.clicked.connect(self.show_animated_loading)
        self.gif_btn.clicked.connect(self.show_gif_loading)
        self.complex_btn.clicked.connect(self.show_complex_loading)
        self.download_btn.clicked.connect(self.show_download_loading)

    def show_simple_loading(self):
        """显示简单加载遮罩"""
        self.loading_mask = LoadingMask(self, "正在处理简单任务...")
        self.loading_mask.show()

        self.start_task("simple")

    def show_animated_loading(self):
        """显示动画加载遮罩"""
        self.loading_mask = AnimatedLoadingMask(self, "正在执行动画任务...")
        self.loading_mask.show()

        self.start_task("simple")

    def show_gif_loading(self):
        """显示GIF加载遮罩"""
        self.loading_mask = GifLoadingMask(self, "正在加载GIF动画...")
        self.loading_mask.show()

        self.start_task("simple")

    def show_complex_loading(self):
        """显示复杂任务加载"""
        self.loading_mask = AnimatedLoadingMask(self, "初始化...")
        self.loading_mask.show()

        self.start_task("complex")

    def show_download_loading(self):
        """显示下载任务加载"""
        self.loading_mask = LoadingMask(self, "准备下载...")
        self.loading_mask.show()

        self.start_task("download")

    def start_task(self, task_type):
        """开始任务"""
        # 禁用所有按钮
        self.set_buttons_enabled(False)

        # 创建并启动工作线程
        self.worker_thread = WorkerThread(task_type)
        self.worker_thread.progress_updated.connect(self.on_progress_updated)
        self.worker_thread.finished.connect(self.on_task_finished)
        self.worker_thread.start()

    def on_progress_updated(self, message):
        """进度更新"""
        if self.loading_mask:
            self.loading_mask.updateText(message)

    def on_task_finished(self, message):
        """任务完成"""
        # 隐藏加载遮罩
        if self.loading_mask:
            self.loading_mask.hide()
            self.loading_mask = None

        # 更新状态
        self.status_label.setText(message)

        # 启用按钮
        self.set_buttons_enabled(True)

        # 清理线程
        if self.worker_thread:
            self.worker_thread.quit()
            self.worker_thread.wait()
            self.worker_thread = None

    def set_buttons_enabled(self, enabled):
        """设置按钮启用状态"""
        for btn in [self.simple_btn, self.animated_btn, self.gif_btn,
                    self.complex_btn, self.download_btn]:
            btn.setEnabled(enabled)

    def closeEvent(self, event):
        """关闭事件"""
        if self.worker_thread and self.worker_thread.isRunning():
            self.worker_thread.quit()
            self.worker_thread.wait()
        event.accept()
# 运行应用
def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())


if __name__ == '__main__':
    main()