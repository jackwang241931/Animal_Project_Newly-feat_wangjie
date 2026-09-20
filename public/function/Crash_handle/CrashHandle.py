import sys
import traceback
import threading
import time
from datetime import datetime
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import QTimer, pyqtSignal, QObject
import signal
import os
from loguru import logger


_crash_log_handler_id = None
_crash_log_handler_lock = threading.Lock()


def setup_crash_logging():
    """Configure the shared crash log sink once in the current process."""
    global _crash_log_handler_id
    with _crash_log_handler_lock:
        if _crash_log_handler_id is None:
            _crash_log_handler_id = logger.add(
                "./log/crash/crash_{time:YYYY-MM-DD}.log",
                format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | "
                       "{process.name} | {thread.name} | "
                       "{name}:{function}:{line} - {message}",
                level="ERROR",
                rotation="1 day",
                retention="90 days",
                encoding="utf-8",
                filter=lambda record: record["level"].name in ["ERROR", "CRITICAL"],
            )
    return _crash_log_handler_id


class CrashHandler(QObject):
    crash_signal = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self._last_gui_heartbeat = time.monotonic()
        self._watchdog_timeout_seconds = 3.0
        self._watchdog_stop_event = threading.Event()
        self._watchdog_thread = None
        self._watchdog_timer = None
        self.setup_logging()
        self.setup_exception_handling()
        self.setup_signal_handling()

    def setup_logging(self):
        """设置loguru日志记录"""
        setup_crash_logging()
        logger.info("CrashHandler initialized")

    def setup_exception_handling(self):
        """设置异常处理"""
        # Python异常处理
        sys.excepthook = self.handle_exception
        threading.excepthook = self.handle_thread_exception

        # Qt异常处理
        if hasattr(sys, '_excepthook'):
            sys._excepthook = sys.excepthook

    def setup_signal_handling(self):
        """设置信号处理"""
        # 处理SIGTERM, SIGINT等信号
        signal.signal(signal.SIGTERM, self.handle_signal)
        signal.signal(signal.SIGINT, self.handle_signal)

        # Windows下的特殊处理
        if os.name == 'nt':
            try:
                signal.signal(signal.SIGBREAK, self.handle_signal)
            except AttributeError:
                pass

    def handle_exception(self, exc_type, exc_value, exc_traceback):
        """处理Python异常"""
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        error_msg = ''.join(traceback.format_exception(exc_type, exc_value, exc_traceback))

        # 使用loguru记录异常
        logger.exception(f"Unhandled exception occurred: {exc_value}")

        # 发送信号
        self.crash_signal.emit(error_msg)

        # 保存详细的崩溃信息
        self.save_crash_dump(error_msg, exc_type, exc_value)

    def handle_thread_exception(self, args):
        """Record uncaught background-thread exceptions without changing UI flow."""
        if args.exc_type is SystemExit:
            return

        error_msg = ''.join(
            traceback.format_exception(args.exc_type, args.exc_value, args.exc_traceback)
        )
        logger.opt(
            exception=(args.exc_type, args.exc_value, args.exc_traceback)
        ).critical(
            f"Unhandled thread exception | thread={args.thread.name} "
            f"| ident={args.thread.ident}"
        )
        self.save_crash_dump(error_msg, args.exc_type, args.exc_value)

    def start_gui_watchdog(self, app, timeout_seconds=3.0):
        """Log all thread stacks when the Qt event loop stops responding."""
        self._watchdog_timeout_seconds = max(1.0, float(timeout_seconds))
        self._last_gui_heartbeat = time.monotonic()
        self._watchdog_stop_event.clear()

        self._watchdog_timer = QTimer(self)
        self._watchdog_timer.setInterval(500)
        self._watchdog_timer.timeout.connect(self._update_gui_heartbeat)
        self._watchdog_timer.start()

        app.aboutToQuit.connect(self.stop_gui_watchdog)
        self._watchdog_thread = threading.Thread(
            target=self._watch_gui_heartbeat,
            name="gui_freeze_watchdog",
            daemon=True,
        )
        self._watchdog_thread.start()

    def stop_gui_watchdog(self):
        self._watchdog_stop_event.set()
        if self._watchdog_timer is not None:
            self._watchdog_timer.stop()

    def _update_gui_heartbeat(self):
        self._last_gui_heartbeat = time.monotonic()

    def _watch_gui_heartbeat(self):
        freeze_reported = False
        while not self._watchdog_stop_event.wait(0.5):
            delay = time.monotonic() - self._last_gui_heartbeat
            if delay >= self._watchdog_timeout_seconds and not freeze_reported:
                freeze_reported = True
                self.log_all_thread_stacks(
                    f"GUI event loop unresponsive for {delay:.1f}s"
                )
            elif freeze_reported and delay < 1.5:
                logger.error(
                    f"GUI event loop recovered | previous heartbeat delay={delay:.1f}s"
                )
                freeze_reported = False

    @staticmethod
    def log_all_thread_stacks(reason):
        frames = sys._current_frames()
        stack_sections = []
        for thread in threading.enumerate():
            frame = frames.get(thread.ident)
            if frame is None:
                continue
            stack_sections.append(
                f"\n--- thread={thread.name} ident={thread.ident} "
                f"daemon={thread.daemon} ---\n"
                + ''.join(traceback.format_stack(frame))
            )

        logger.error(
            f"[RUNTIME_DIAGNOSTIC] {reason}\n"
            f"Captured threads: {len(stack_sections)}"
            + ''.join(stack_sections)
        )

    def handle_signal(self, signum, frame):
        """处理系统信号"""
        signal_names = {
            signal.SIGTERM: "SIGTERM",
            signal.SIGINT: "SIGINT"
        }

        signal_name = signal_names.get(signum, f"Signal {signum}")
        error_msg = f"Application received {signal_name}"

        signal_stack = ''.join(traceback.format_stack(frame)) if frame is not None else "unavailable"
        logger.critical(
            f"Received signal: {signal_name}\nSignal stack:\n{signal_stack}"
        )
        self.crash_signal.emit(error_msg)

        # 清理并退出
        QApplication.quit()

    def save_crash_dump(self, error_msg, exc_type=None, exc_value=None):
        """保存详细的崩溃转储"""
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"/crash_dumps/crash_dump_{timestamp}.txt"

        try:
            # 确保目录存在
            os.makedirs("crash_dumps", exist_ok=True)

            with open(os.getcwd()+filename, 'w', encoding='utf-8') as f:
                f.write(f"Crash Time: {datetime.now()}\n")
                f.write(f"Python Version: {sys.version}\n")
                f.write(f"Platform: {sys.platform}\n")
                if exc_type:
                    f.write(f"Exception Type: {exc_type.__name__}\n")
                if exc_value:
                    f.write(f"Exception Value: {exc_value}\n")
                f.write("-" * 50 + "\n")
                f.write(error_msg)

            logger.info(f"Crash dump saved to: {filename}")

        except Exception as e:
            logger.error(f"Failed to save crash dump: {e}")
