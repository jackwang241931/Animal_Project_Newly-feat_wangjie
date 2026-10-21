import os
import sys
import time
import traceback
from multiprocessing import freeze_support
from queue import Empty

# Load the native ML runtime before Qt/OpenCV are imported. The legacy
# version initialized YOLO at worker-process startup; late loading from a
# Qt thread can make torch's c10.dll fail with WinError 1114 on Windows.
os.environ.setdefault('KMP_DUPLICATE_LIB_OK', 'TRUE')
os.environ.setdefault('OMP_NUM_THREADS', '1')
os.environ.setdefault('MKL_NUM_THREADS', '1')
import torch  # noqa: F401,E402
from PyQt6.QtCore import QThreadPool, QRect, QTimer, pyqtSignal
from PyQt6.QtWidgets import QApplication, QDialog, QMessageBox
from blinker import signal
from loguru import logger
from index.MainWindow_index import MainWindow_Index, stop_main_window_queue_thread
from index.Program_self_check import Program_self_check_index
from public.component.dialog.index.deep_camera_config_dialog_index import deep_camera_config_dialog
from public.component.dialog.index.infrared_camera_config_dialog_index import infrared_camera_config_dialog

from public.config_class import global_load
from public.config_class.global_setting import global_setting
from public.config_class.ini_parser import ini_parser
from public.entity.MyQThread import MyThread, MyQThread, stop_registered_qthreads
from public.entity.enum.Public_Enum import AppState
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.function.Crash_handle.CrashHandle import CrashHandler
from public.function.Modbus.New_Mod_Bus import ModbusRTUMasterNew
from public.util.native_crash_diagnostics import native_call
from public.util.shared_video_frames import shared_video_frame_store
from public.util.time_util import time_util
from theme.ThemeManager import ThemeManager
# 过滤日志
#logger = logger.bind(category="gui_logger")


infrared_camera_config_dialog_obj =None
deep_camera_config_dialog_obj =None
class read_queue_data_Thread(MyQThread):
    infrared_camera_scan_ready_signal = pyqtSignal(object)

    def __init__(self, name):
        super().__init__(name)
        self.queue = None
        self.camera_list = None
        self.infrared_camera_scan_ready_signal.connect(self.apply_infrared_camera_scan_ready)

    def dosomething(self):
        if not self.queue.empty():
            try:
                # ``empty()`` is only a hint for multiprocessing queues.  A
                # blocking get here can make the Qt thread survive window
                # destruction and race with SIP during interpreter shutdown.
                message: ObjectQueueItem = self.queue.get_nowait()
            except Empty:
                return
            except Exception as e:
                logger.error(f"{self.name}发生错误{e}")
                return
            if message is not None and message.is_Empty():
                return
            if message is not None and isinstance(message, ObjectQueueItem) and message.to=='main_gui':
                logger.error(f"{self.name}_get_message:{message}")
                match message.title:
                    case "deep_camera_config_dialog":
                        QTimer.singleShot(200,self.show_deep_camera_config_dialog)

                        pass
                    case "infrared_camera_config_dialog":
                        QTimer.singleShot(100, self.show_infrared_camera_config_dialog)
                        pass
                    case "infrared_camera_scan_ready":
                        self.infrared_camera_scan_ready_signal.emit(message.data or {})
                    case _:
                        pass

            else:
                # 把消息放回去
                self.queue.put(message)

    def show_infrared_camera_config_dialog(self):
        global infrared_camera_config_dialog_obj
        infrared_camera_config_dialog_obj = infrared_camera_config_dialog(title="红外相机配置")
        # dialog_frame.camera_config_finished_signal.connect(init_camera_and_image_handle_thread)
        infrared_camera_config_dialog_obj.show_frame()
        pass

    def show_deep_camera_config_dialog(self):
        global deep_camera_config_dialog_obj
        deep_camera_config_dialog_obj = deep_camera_config_dialog(title="深度相机配置")
        # dialog_frame.camera_config_finished_signal.connect(init_camera_and_image_handle_thread)
        deep_camera_config_dialog_obj.show_frame()
        pass

        pass

    def apply_infrared_camera_scan_ready(self, response):
        dialog = global_setting.get_setting("active_infrared_camera_config_dialog", None)
        if dialog is None:
            logger.warning("infrared camera scan acknowledgement ignored: config dialog is unavailable")
            return
        dialog.handle_camera_release_ack(response)


read_queue_data_thread = read_queue_data_Thread(name="main_gui_read_queue_data_thread")
_qt_shutdown_started = False


def quit_qt_application(main_window=None):
    """
    退出QT程序
    :return:
    """
    global _qt_shutdown_started
    if _qt_shutdown_started:
        return
    _qt_shutdown_started = True

    logger.info("Qt application is shutting down normally")
    # modbus: ModbusRTUMasterNew = global_setting.get_setting("modbus", None)
    # if modbus is not None:
    #     logger.error("stop_modbus_crash_application")
    #     modbus.close()
    # 不同进程的全局变量不同，所以让数据监控进程来关闭modbus
    send_message_queue = global_setting.get_setting("send_message_queue", None)
    run_state = getattr(main_window, "workflow_run_state", None)
    run_state_value = getattr(run_state, "value", run_state)
    stop_backend_completed = bool(
        main_window is not None
        and run_state_value in {"stopped", "stop_failed"}
        and getattr(main_window, "_stop_backend_complete", False)
        and not getattr(main_window, "_stop_operation_failed", False)
    )
    if send_message_queue and not stop_backend_completed:
        send_message_queue.put(
            ObjectQueueItem(origin="main_gui", to="main_monitor_data", title="stop_modbus",

                            time=time_util.get_format_from_time(time.time())))
    elif stop_backend_completed:
        # The experiment stop workflow already executed the full ordered gas
        # shutdown and closed its sender.  Avoid reconnecting COM3 here and
        # issuing a second, abbreviated pump/UFC shutdown sequence.
        logger.info("完整实验停止流程已完成，跳过退出时重复重连串口关泵")

    if main_window is not None:
        try:
            main_window.prepare_for_shutdown(timeout_ms=15000)
        except Exception as error:
            logger.exception(f"窗口退出清理失败: {error}")

    # aboutToQuit runs on Qt's GUI thread.  Stop every queue reader before
    # QWidget/QApplication destruction.  Do not call sys.exit() here.
    wait_ms = 15000 if main_window is not None else 2000
    if read_queue_data_thread.isRunning():
        read_queue_data_thread.blockSignals(True)
        read_queue_data_thread.stop()
        read_queue_data_thread.requestInterruption()
        if not read_queue_data_thread.wait(wait_ms):
            logger.warning("main GUI queue reader did not stop before Qt shutdown")
    stop_main_window_queue_thread(timeout_ms=wait_ms)
    # Hidden workflow pages have their own module-level queue readers.  They
    # survive MainWindow cleanup unless joined explicitly, and can emit into
    # destroyed QWidgets while SIP is tearing down the interpreter.
    stop_registered_qthreads(timeout_ms=wait_ms)
    shared_video_frame_store.close_reader()

def on_crash( error_msg):
    """处理崩溃事件"""
    logger.critical(f"Crash detected: {error_msg}...")
    # modbus: ModbusRTUMasterNew = global_setting.get_setting("modbus", None)
    # if modbus is not None:
    #     logger.error("stop_modbus_crash_application")
    #     modbus.close()
    # 不同进程的全局变量不同，所以让数据监控进程来关闭modbus
    queue = global_setting.get_setting("queue", None)
    if queue:
        queue.put(
            ObjectQueueItem(origin="main_gui", to="main_monitor_data", title="stop_modbus",

                            time=time_util.get_format_from_time(time.time())))
    # 显示崩溃对话框

    msg_box = QMessageBox()
    msg_box.setIcon(QMessageBox.Icon.Critical)
    msg_box.setWindowTitle("程序崩溃")
    msg_box.setText("程序发生了意外错误")
    msg_box.setDetailedText(error_msg)
    msg_box.setStandardButtons(
        QMessageBox.StandardButton.Ok |
        QMessageBox.StandardButton.Close
    )

    result = msg_box.exec()

    if result == QMessageBox.StandardButton.Close:
        logger.info("User chose to close application after crash")
        QApplication.quit()
def start_qt_application():
    """
    qt程序开始
    :return: 无
    """
    # 启动qt
    logger.info("start Qt")
    with native_call("gui", "qapplication_create"):
        app = QApplication(sys.argv)
    # 屏幕大小
    # 获取屏幕大小
    screen = app.primaryScreen()

    screen_rect :QRect= screen.availableGeometry()
    screen_rect.setHeight(screen_rect.height()-30)
    global_setting.set_setting("screen", screen_rect)
    # 绑定突出事件
    # 程序崩溃闪退监听
    crash_handler = CrashHandler()
    crash_handler.crash_signal.connect(on_crash)

    with native_call("gui", "program_self_check_dialog"):
        program_self_check_index_dialog = Program_self_check_index()
        program_self_check_index_dialog.activateWindow()
        return_Data = program_self_check_index_dialog.exec()
    if return_Data != QDialog.DialogCode.Accepted:
        # This dialog runs before app.exec(). Entering the event loop after a
        # rejection leaves a windowless GUI process alive forever, preventing
        # the parent controller from reclaiming the remaining child processes.
        logger.info("Startup cancelled by user; closing GUI process")
        quit_qt_application()
        app.closeAllWindows()
        return 0

    with native_call("gui", "main_window_create"):
        main_window = MainWindow_Index()
    main_window._gui_watchdog = crash_handler
    logger.info("Application start")
    with native_call("gui", "main_window_show"):
        main_window.show_frame()
    # Startup dialogs and MainWindow construction run before app.exec() and may
    # legitimately spend several seconds importing modules. Monitor only the
    # live GUI event loop so startup work is not reported as a runtime freeze.
    crash_handler.start_gui_watchdog(app, timeout_seconds=3.0)
    # Stop the watchdog before the slower device cleanup callback runs.
    app.aboutToQuit.connect(lambda: quit_qt_application(main_window))
    # 系统退出
    with native_call("gui", "qt_event_loop"):
        exit_code = app.exec()
    # Normally aboutToQuit has already done this.  The explicit idempotent
    # call also covers event-loop termination initiated by the OS/window.
    quit_qt_application(main_window)
    logger.info(f"Qt event loop returned | exit_code={exit_code}")
    return exit_code


def main(q, send_message_q, runtime_diagnostics_queue=None, system_health_snapshot_queue=None):
    freeze_support()

    # logger.remove(0)
    # logger.add(
    #     "./log/gui/gui_{time:YYYY-MM-DD}.log",
    #     rotation="00:00",  # 日志文件转存
    #     retention="30 days",  # 多长时间之后清理
    #     enqueue=True,
    #     format="{time:YYYY-MM-DD HH:mm:ss} | {level} |{process.name} | {thread.name} |  {name} : {module}:{line} | {message}",
    #
    # )
    logger.info(f"{'-' * 40}main_gui_start{'-' * 40}")
    logger.info(f"{__name__} | {os.path.basename(__file__)}|{os.getpid()}|{os.getppid()}")
    global_load.load_global_setting()
    global_setting.set_setting("queue", q)
    global_setting.set_setting("send_message_queue", send_message_q)
    global_setting.set_setting(
        "runtime_diagnostics_queue",
        runtime_diagnostics_queue,
    )
    # This queue is intentionally distinct from runtime diagnostics.  The
    # latter is consumed by the parent log writer, while this one is drained
    # only by MainWindow's GUI-side HealthStore.
    global_setting.set_setting(
        "system_health_snapshot_queue",
        system_health_snapshot_queue,
    )
    global read_queue_data_thread
    read_queue_data_thread.queue = q
    read_queue_data_thread.start()

    # qt程序开始
    start_qt_application()
