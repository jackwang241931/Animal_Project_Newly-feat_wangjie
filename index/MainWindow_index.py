import importlib
import copy
import json
import math
import os
import re
import sqlite3
import threading
import time
from json import JSONDecodeError
from queue import Empty

from PyQt6.QtGui import QFont
from PyQt6 import QtCore
from PyQt6.QtCore import QEvent, QTimer, QObject, QThread, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import QMessageBox, QVBoxLayout, QToolBar, QTabWidget, QDialog, QMenu, QMenuBar, QWidget, \
    QApplication, QAbstractSpinBox, QCheckBox, QComboBox, QDial, QFileDialog, QProgressDialog, QRadioButton, QSlider
from loguru import logger

from PyQt6.QtCore import Qt
from Service import main_monitor_data, main_deep_camera, main_infrared_camera
from Service.UFC_UGC_ZOS_Service.function.gas_path_system.Gas_path_system import ZOS_gas_path_system, \
    UFC_gas_path_system, UGC_gas_path_system, UFC_FLOW_SETPOINT_DEFAULTS
from Service.UFC_UGC_ZOS_Service.index.UFC_UGC_ZOS_index import UFC_UGC_ZOS_index
from my_abc.BaseModule import BaseModule
from public.component.Guide_tutorial_interface.Tutorial_Manager import TutorialManager
from public.component.custom_status_bar import CustomStatusBar
from public.component.system_status import SystemStatusPage, is_ignored_alert_reason
from public.component.experiment_config.experiment_config_page import ExperimentConfigPage
from public.component.workflow_pages import (
    DataAnalysisPage,
    DataViewPage,
    ExperimentControlPage,
    MachineVisionPage,
)
from public.component.workflow_shell import WorkflowShell, resolve_legacy_workflow_step
from public.component.dialog.custom.calibration_detail_Dialog import CalibrationDialog
from public.component.dialog.custom.loading_dialog_seconds import AnimatedLoadingDialog
from public.component.mask.LoadingMask import LoadingContext
from public.config_class.App_Setting import AppSettings
from public.config_class.global_setting import global_setting
from public.entity.MyQThread import MyQThread
from public.entity.enum.Public_Enum import BaseInterfaceType, AppState, Tutorial_Type
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.function.Modbus.New_Mod_Bus import ModbusRTUMasterNew
from public.function.promise.AsyPromise import AsyPromise
from public.system_health import ExperimentRunState, HealthPolicy, HealthState, HealthStore
from public.util.custom_data_file_util import custom_data_file_util
from public.util.time_util import time_util
from theme.ThemeQt6 import ThemedWindow
from ui.MainWindow import Ui_MainWindow
#logger = logger.bind(category="gui_logger")


class Start_experiment_thread(MyQThread):
    def __init__(self,name,window):
        super().__init__(name=name)
        self.window:MainWindow_Index = window
    def dosomething(self):
        self.window.start_experiment_handle()
        self.stop()
        pass
class Stop_experiment_thread(MyQThread):
    def __init__(self,name,window):
        super().__init__(name)
        self.window:MainWindow_Index = window
    def dosomething(self):
        self.window.stop_experiment_handle()
        self.stop()


class StopExperimentExportThread(QThread):
    export_finished = pyqtSignal(bool, str)
    export_status = pyqtSignal(str)

    def __init__(self, folder_path, parent=None):
        super().__init__(parent)
        self.folder_path = folder_path

    def run(self):
        try:
            export_path = custom_data_file_util.save_folder_contents_as_custom_file(
                self.folder_path,
                show_success_message=False,
            )
            if export_path:
                self.export_status.emit("原始数据导出成功")
                self.export_finished.emit(True, str(export_path))
            else:
                detail = custom_data_file_util.get_last_export_error()
                self.export_finished.emit(
                    False,
                    detail or "data export failed"
                )
        except Exception as error:
            logger.exception(f"stop experiment export failed: {error}")
            self.export_finished.emit(False, str(error))


class read_queue_data_Thread(MyQThread):
    gas_path_start_completed_signal = pyqtSignal()
    start_failed_signal = pyqtSignal(str)
    operation_progress_signal = pyqtSignal(int, str)
    calibration_event_signal = pyqtSignal(str)
    # Calibration detail widgets are QWidgets and must only be touched from
    # the GUI thread.  The queue reader runs in a worker thread, so it emits a
    # small description of the update instead of calling the dialog directly.
    calibration_detail_update_signal = pyqtSignal(object)
    status_tip_signal = pyqtSignal(str)
    stop_backend_completed_signal = pyqtSignal()
    stop_backend_issue_signal = pyqtSignal(str)
    stop_progress_signal = pyqtSignal(str)
    cage_control_response_signal = pyqtSignal(object)

    def __init__(self, name,window=None):
        super().__init__(name)
        self.queue = None
        self.camera_list = None
        self.window:MainWindow_Index = window

        # 停止实验用到的 返回状态，当深度相机、红外相机、整体气路、UFC气路、ugc气路、zos气路、鼠笼内、存储数据都发过返回消息则关闭关闭实验窗口
        self.old_Stop_experiment_status_text_reTurn =None
        self.old_stop_status_counts = 0
        self.old_stop_status_max = 7
        self._stop_ack_titles = set()
        self._required_stop_ack_titles = {
            "stop_deep_camera_complete", "stop_infrared_camera_complete",
            "stop_gap_system_return", "stop_ufc_gap_system_return",
            "stop_ugc_gap_system_return", "stop_zos_gap_system_return",
            "stop_monitor_data_return",
        }
        pass

    def dosomething(self):
        if self.queue and  not self.queue.empty():
            try:
                # multiprocessing.Queue.empty() is only a hint.  Never use a
                # blocking get in the GUI reader: shutdown must be able to
                # join this thread before Qt destroys MainWindow widgets.
                message: ObjectQueueItem = self.queue.get_nowait()
            except Empty:
                return
            except Exception as e:
                logger.error(f"{self.name}发生错误{e}")
                return

            if message is not None and message.is_Empty():
                return
            if message is not None and isinstance(message, ObjectQueueItem) and message.to=='MainWindow_index':
                # logger.error(f"{self.name}_get_message:{message}")
                match message.title:
                    case "deep_camera_instance_state":
                        state_data = message.data if isinstance(message.data, dict) else {}
                        state = str(state_data.get("state", "starting"))
                        owner = state_data.get("owner") or {}
                        previous_state = global_setting.get_setting(
                            "deep_camera_instance_state",
                            "starting",
                        )
                        global_setting.set_setting("deep_camera_instance_state", state)
                        global_setting.set_setting(
                            "deep_camera_instance_owner",
                            owner,
                        )
                        if self.window:
                            self.window.deep_camera_instance_state_changed_signal.emit(
                                state,
                                str(previous_state),
                                owner,
                            )
                    case "gap_system_running_state":
                        if message.data  and self.window:
                            # Zero/span shutdown messages are also published on
                            # the general gas-state channel.  Consume them once
                            # here on a direct run so they cannot leak into the
                            # hidden start dialog and be mirrored a second time.
                            if not self.window._is_uncalibrated_stop_housekeeping(message.data):
                                #  更新气路运行消息
                                # 将运行信息放入status栏中
                                self.status_tip_signal.emit(str(message.data))
                                if self.window.workflow_run_state == ExperimentRunState.STOPPING:
                                    self.window._append_workflow_runtime_log(message.data, "停止")
                                    return
                                match = re.search(r"已运行\s*(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)\s*秒", str(message.data))
                                if match:
                                    elapsed, total = float(match.group(1)), float(match.group(2))
                                    if total > 0:
                                        self.operation_progress_signal.emit(min(99, int(elapsed / total * 100)), "空气校准进行中")
                                # Zero/span calibration emits numbered backend stages.
                                stage = re.search(r"(?:校准|鏍囧畾)\D*(\d+)\s*[.。]", str(message.data))
                                if stage and any(token in str(message.data) for token in ("校准", "鏍囧畾")):
                                    self.operation_progress_signal.emit(
                                        min(99, int(int(stage.group(1)) / 9 * 100)),
                                        "校准阶段 %s/9" % stage.group(1),
                                    )
                                if self.window.start_dialog is not None :
                                    self.window.start_dialog.insert_data_signal.emit(f"{message.data} ")
                                    # self.window.start_dialog.update_progress_value(1)
                            pass


                        pass
                    case "cage_control_response":
                        if self.window:
                            self.cage_control_response_signal.emit(message.data if isinstance(message.data, dict) else {})
                    case "mouse_cage_inner_module_running_state":
                        #鼠笼环境内部模块运行情况
                        if message.data and self.window:
                            # 将运行信息放入status栏中
                            self.status_tip_signal.emit(str(message.data))
                            pass
                    case "epoch_running_state":
                        # 一轮模块数据运行情况
                        if message.data and self.window:
                            # 将运行信息放入status栏中
                            self.status_tip_signal.emit(str(message.data))
                            pass
                    case 'close_start_experiment_dialog':
                        self.gas_path_start_completed_signal.emit()
                    case 'start_experiment_failed':
                        self.start_failed_signal.emit(str(message.data or "后台启动失败"))
                    case "stop_deep_camera_return" | "stop_infrared_camera_return" | "stop_deep_camera_complete" | "stop_infrared_camera_complete" | "stop_gap_system_return" | "stop_ufc_gap_system_return" | "stop_ugc_gap_system_return" | "stop_zos_gap_system_return" | "stop_monitor_data_return" | "stop_show_info_except_status_counts":
                        if message.data and self.window:
                            #  更新气路运行消息
                            # 将运行信息放入status栏中
                            stop_detail = message.data.get("detail", "") if isinstance(message.data, dict) else str(message.data)
                            self.stop_progress_signal.emit(stop_detail)
                                # self.window.start_dialog.update_progress_value(1)
                            if message.title in self._required_stop_ack_titles:
                                self._stop_ack_titles.add(message.title)
                                if isinstance(message.data, dict):
                                    failed = not message.data.get("success", True)
                                else:
                                    failed = any(word in stop_detail for word in ("错误", "失败", "超时"))
                                if failed:
                                    self.stop_backend_issue_signal.emit(stop_detail or message.title)
                            if self._required_stop_ack_titles.issubset(self._stop_ack_titles):
                                self._stop_ack_titles.clear()
                                self.stop_backend_completed_signal.emit()

                        pass
                    case 'close_stop_experiment_dialog':
                        # 停止完成，关闭停止实验窗口
                        self.close_stop_experiment_dialog()
                    case 'calibration_msg':
                        """
                        标定的消息
                        """
                        if self.window is not None:
                            self.calibration_detail_update_signal.emit({
                                "kind": "message",
                                "message": message.data,
                                "has_time": True,
                            })
                    case 'set_start_zero_calibration_time':
                        """
                        设置开始校准零点
                        data为time
                        """
                        if self.window is not None:
                            self.operation_progress_signal.emit(5, "校零进行中")
                            self.calibration_event_signal.emit("zero_started")
                            self.calibration_detail_update_signal.emit({
                                "kind": "zero_start",
                                "time": message.data,
                            })

                    case 'set_stop_zero_calibration_time':
                        """
                        设置结束校准零点
                        data为time
                        """
                        if self.window is not None:
                            self.operation_progress_signal.emit(100, "校零完成")
                            self.calibration_event_signal.emit("zero_finished")
                            self.calibration_detail_update_signal.emit({
                                "kind": "zero_end",
                                "time": message.data,
                            })
                    case 'set_start_span_calibration_time':
                        """
                        设置开始校准span
                        data为time
                        """
                        if self.window is not None:
                            self.operation_progress_signal.emit(5, "校量程进行中")
                            self.calibration_event_signal.emit("span_started")
                            self.calibration_detail_update_signal.emit({
                                "kind": "span_start",
                                "time": message.data,
                            })
                    case 'set_stop_span_calibration_time':
                        """
                        设置结束校准span
                        data为time
                        """
                        if self.window is not None:
                            self.operation_progress_signal.emit(100, "校量程完成")
                            self.calibration_event_signal.emit("span_finished")
                            self.calibration_detail_update_signal.emit({
                                "kind": "span_end",
                                "time": message.data,
                            })
                    case 'set_start_air_calibration_time':
                        if self.window is not None:
                            self.operation_progress_signal.emit(5, "空气校准进行中")
                            self.calibration_detail_update_signal.emit({
                                "kind": "air_start",
                                "time": message.data,
                            })
                    case 'set_stop_air_calibration_time':
                        if self.window is not None:
                            self.operation_progress_signal.emit(100, "空气校准完成")
                            self.calibration_detail_update_signal.emit({
                                "kind": "air_end",
                                "time": message.data,
                            })
                    case 'set_calibration_values':
                        """
                        设置校准窗口显示值
                        data:{"oxygen_value":0,"carbon_value":0,"oxygen_pressure_value":0}
                        """
                        if self.window is not None and message.data is not None:
                            self.calibration_detail_update_signal.emit({
                                "kind": "values",
                                "values": dict(message.data) if isinstance(message.data, dict) else {},
                                "time": message.time,
                            })
                    case _:
                        pass

            else:
                # 把消息放回去
                self.queue.put(message)
    def close_stop_experiment_dialog(self):
        if self.window is None:
            return
        self.stop_backend_completed_signal.emit()


class PeriodicExcelExportThread(threading.Thread):
    def __init__(self, db_path, export_file_path, callback=None):
        super().__init__(name="periodic_excel_export_thread", daemon=True)
        self.db_path = db_path
        self.export_file_path = export_file_path
        self.callback = callback

    @staticmethod
    def _create_snapshot(source_db_path, snapshot_db_path):
        source_conn = None
        snapshot_conn = None
        try:
            source_conn = sqlite3.connect(source_db_path, timeout=30.0)
            snapshot_conn = sqlite3.connect(snapshot_db_path, timeout=30.0)
            source_conn.backup(snapshot_conn)
        finally:
            if snapshot_conn is not None:
                snapshot_conn.close()
            if source_conn is not None:
                source_conn.close()

    def run(self):
        snapshot_db_path = f"{self.db_path}.{int(time.time() * 1000)}.snapshot.db"
        success = False
        message = ""
        try:
            if not os.path.exists(self.db_path):
                raise FileNotFoundError(f"数据库文件不存在: {self.db_path}")

            export_dir = os.path.dirname(self.export_file_path)
            if export_dir:
                os.makedirs(export_dir, exist_ok=True)

            self._create_snapshot(self.db_path, snapshot_db_path)
            success = custom_data_file_util.export_data_to_csv(
                export_file_path=self.export_file_path,
                file_path=snapshot_db_path,
                show_success_message=False,
                use_atomic_replace=True
            )
            message = f"定时导出实验数据{'成功' if success else '失败'}: {os.path.basename(self.export_file_path)}"
        except Exception as e:
            message = f"定时导出实验数据失败: {e}"
            logger.error(message)
        finally:
            if os.path.exists(snapshot_db_path):
                try:
                    os.remove(snapshot_db_path)
                except OSError as remove_error:
                    logger.error(f"删除数据库快照失败: {remove_error}")

            if self.callback is not None:
                self.callback(success, message)


class ManualExcelExportThread(QThread):
    """在后台完成手动 Excel 导出，避免阻塞 Qt 主线程。"""

    export_finished = pyqtSignal(bool, str)

    def __init__(self, db_path, export_file_path, parent=None):
        super().__init__(parent)
        self.db_path = db_path
        self.export_file_path = export_file_path

    def run(self):
        snapshot_db_path = f"{self.db_path}.{time.time_ns()}.manual_export.snapshot.db"
        try:
            if not os.path.exists(self.db_path):
                raise FileNotFoundError(f"数据库文件不存在: {self.db_path}")

            export_dir = os.path.dirname(self.export_file_path)
            if export_dir:
                os.makedirs(export_dir, exist_ok=True)

            # 先复制出一致性快照，Excel 导出只读快照，减少和实时写库的互相影响。
            PeriodicExcelExportThread._create_snapshot(self.db_path, snapshot_db_path)
            success = custom_data_file_util.export_data_to_csv(
                export_file_path=self.export_file_path,
                file_path=snapshot_db_path,
                show_success_message=False,
                use_atomic_replace=True,
            )
            if success:
                self.export_finished.emit(True, self.export_file_path)
            else:
                detail = custom_data_file_util.get_last_export_error()
                self.export_finished.emit(False, detail or "data export failed")
        except Exception as error:
            logger.exception(f"手动导出实验数据失败: {error}")
            self.export_finished.emit(False, str(error))
        finally:
            if os.path.exists(snapshot_db_path):
                try:
                    os.remove(snapshot_db_path)
                except OSError as remove_error:
                    logger.error(f"删除手动导出数据库快照失败: {remove_error}")


read_queue_data_thread = read_queue_data_Thread(name="MainWindow_index_read_queue_data_thread")


def stop_main_window_queue_thread(timeout_ms=5000):
    """Stop the GUI queue reader before Qt starts destroying widgets.

    The reader keeps a reference to ``MainWindow_Index`` and emits Qt signals
    from its worker thread.  Letting the QApplication tear down the window
    first leaves a small race in SIP/QtWidgets (the crash dump showed exactly
    that race).  This helper is intentionally idempotent and does not call
    ``deleteLater`` while the event loop is being shut down.
    """
    thread = read_queue_data_thread
    if thread is None:
        return True

    try:
        thread.blockSignals(True)
        if thread.isRunning():
            thread.stop()
            thread.requestInterruption()
            if not thread.wait(timeout_ms):
                logger.error(
                    f"MainWindow queue reader did not stop within {timeout_ms} ms"
                )
                return False

        # Do this only after the native QThread has ended.  The worker must
        # never dereference a QWidget or a multiprocessing queue during Qt
        # object destruction.
        thread.window = None
        thread.queue = None
        return True
    except Exception as error:
        logger.exception(f"停止 MainWindow queue reader 失败: {error}")
        return False


class MainWindow_Index(ThemedWindow):
    # 根据程序状态来改变是否可以点击的组件
    change_enable_component_app_state_signal = QtCore.pyqtSignal()
    # 设备配置页校准选择变化信号 (是否已选择, 是否校准)
    calibration_selection_changed_signal = QtCore.pyqtSignal(bool, str)
    # 显示校准详情dialog的信号
    show_calibration_window_signal = QtCore.pyqtSignal(dict)
    # 释放校准详情dialog的信号
    release_calibration_window_signal = QtCore.pyqtSignal()
    # 线程安全的状态栏提示信号
    update_status_tip_signal = QtCore.pyqtSignal(str)
    # 临时覆盖状态栏中间文字的信号 (message, color, duration_ms)
    show_temp_status_tip_signal = QtCore.pyqtSignal(str, str, int)
    # 深度相机互斥状态必须回到 GUI 线程更新，避免初始化期间直接访问空控件。
    deep_camera_instance_state_changed_signal = QtCore.pyqtSignal(str, str, object)
    def close_window_handle(self):
        """
        关闭窗口执行的事件
        :return:
        """
        state = global_setting.get_setting("app_state", None)
        # 如果在开始实验期间关闭窗口
        if state is not None and state ==AppState.MONITORING:
            # 停止实验
            self.stop_experiment()
        # 关闭所有串口

    def prepare_for_shutdown(self, timeout_ms=5000):
        """Quiesce GUI-owned workers/timers before QApplication exits.

        This is separate from the experiment stop workflow.  It is only the
        final Qt teardown barrier, so it does not send any UFC/gas command.
        """
        if getattr(self, "_shutdown_prepared", False):
            return
        self._shutdown_prepared = True
        logger.info("开始 Qt GUI 退出清理：先停止窗口线程和导出任务")

        # Do not allow queued timer callbacks to touch widgets while the
        # parent window is being destroyed.
        for timer in self.findChildren(QTimer):
            try:
                timer.stop()
            except RuntimeError:
                pass

        # A periodic export is a Python thread.  Detach its GUI callback first
        # so a late completion cannot call into a half-destroyed MainWindow.
        periodic_thread = self.periodic_export_thread
        if periodic_thread is not None:
            periodic_thread.callback = None
            if periodic_thread.is_alive():
                periodic_thread.join(timeout=timeout_ms / 1000.0)
                if periodic_thread.is_alive():
                    logger.error("定时 Excel 导出线程未能在退出前结束")

        # Export QThreads are parented to this window.  Block their signals
        # before waiting; their work is allowed to finish, but no completion
        # callback may update widgets after shutdown has started.
        for name in ("stop_export_thread", "manual_export_thread"):
            thread = getattr(self, name, None)
            if thread is None:
                continue
            try:
                thread.blockSignals(True)
                if thread.isRunning():
                    thread.requestInterruption()
                    thread.quit()
                    if not thread.wait(timeout_ms):
                        logger.error(f"{name} 未能在退出前结束")
            except RuntimeError as error:
                logger.warning(f"退出时清理 {name} 跳过已销毁对象: {error}")
            except Exception as error:
                logger.exception(f"退出时清理 {name} 失败: {error}")

        # The module-level queue reader is shared by the workflow page and
        # must be stopped before this window is released.
        stop_main_window_queue_thread(timeout_ms=timeout_ms)

    def _experiment_needs_stop_before_close(self):
        """Return whether closing must wait for the experiment stop/export flow."""
        app_state = global_setting.get_setting("app_state", AppState.INITIALIZED)
        return (
            app_state == AppState.MONITORING
            or self.workflow_run_state in {
                ExperimentRunState.RUNNING,
                ExperimentRunState.STOPPING,
            }
        )

    def _close_after_stop_if_requested(self):
        """Close the main window only after stop and data export have completed."""
        if not self._close_after_stop or self.workflow_run_state != ExperimentRunState.STOPPED:
            return
        self._close_after_stop = False
        self._close_without_confirmation = True
        QTimer.singleShot(0, self.close)

    def _prepare_close_stop_display(self):
        """Show the stopping state before the window is disabled for close."""
        show_page = getattr(self, "show_experiment_control_page", None)
        if callable(show_page):
            try:
                show_page()
            except Exception as error:
                logger.debug(f"unable to show experiment control page while stopping: {error}")

    def closeEvent(self, event):
        if self._close_without_confirmation:
            self._close_without_confirmation = False
            event.accept()
            return

        if self.workflow_run_state == ExperimentRunState.STOPPING:
            self._prepare_close_stop_display()
            self._close_after_stop = True
            self.setEnabled(False)
            event.ignore()
            return

        if len(self.open_windows) != 0:
            reply = QMessageBox.question(
                self,
                '关闭窗口',
                "当前还有其他子窗口未关闭，你确定要退出程序吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                needs_stop = self._experiment_needs_stop_before_close()
                if needs_stop:
                    self._prepare_close_stop_display()
                self.close_window_handle()
                for window in list(self.open_windows):
                    window.close()
                if needs_stop:
                    self._close_after_stop = True
                    self.setEnabled(False)
                    event.ignore()
                else:
                    event.accept()
            else:
                event.ignore()
        else:
            reply = QMessageBox.question(
                self,
                '关闭窗口',
                "你确定要退出程序吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                needs_stop = self._experiment_needs_stop_before_close()
                if needs_stop:
                    self._prepare_close_stop_display()
                self.close_window_handle()
                if needs_stop:
                    self._close_after_stop = True
                    self.setEnabled(False)
                    event.ignore()
                else:
                    event.accept()
            else:
                event.ignore()
        pass
    def setup_tutorial(self):
        # The redesigned main window no longer exposes the legacy menu and
        # toolbar tutorial targets. Leaving the old arrow overlay enabled
        # creates a blank floating popup when the workflow shell changes
        # pages, so tutorials are intentionally disabled for this window.
        if self.tutorial is not None:
            self.tutorial.end_tutorial()
        self.tutorial = None
        for widget in self.findChildren(QObject, "temp_deleted_widget"):
            widget.hide()
        return

        #实例化提示引导器 下面式实例化模板
        if self.tutorial:
            self.tutorial.end_tutorial()

        self.tutorial = TutorialManager(self, "MainWindow_index", Tutorial_Type.ARROW_GUIDE, global_setting.get_setting("app_setting", AppSettings()))

        # 连接教程完成信号
        self.tutorial.tutorial_completed.connect(self.on_tutorial_completed)

        # 添加更详细的引导步骤
        actions = self.menuBar().actions()
        widgets = self.findChildren(QObject, "temp_deleted_widget")
        for action in actions:
            action:QAction
            menu:QMenu = action.menu()
            if menu:
                # 获取菜单栏中的按钮控件
                geometry = self.menuBar().actionGeometry(action)
                if not widgets:
                    widget =QWidget()
                    widget.setParent(self)
                    widget.setGeometry(geometry)
                    widget.setObjectName("temp_deleted_widget")

                    # 对 file_menu 进行操作
                    self.tutorial.add_step(widget,
                                           f"单击此按钮是{action.text()}\n{menu.toolTip()}")
                else:
                    widget =widgets.pop(0)
                    widget.show()
                    self.tutorial.add_step(widget,
                                           f"单击此按钮是{action.text()}\n{menu.toolTip()}")
        for tool_bar_action in self.tool_bar_actions:
            if isinstance(tool_bar_action['action'], QAction):
                self.tutorial.add_step(tool_bar_action['action'].associatedObjects()[1],
                                       f"单击此按钮是{tool_bar_action['text']}\n{tool_bar_action['tip']}")
            else:
                self.tutorial.add_step(tool_bar_action['action'],
                                       f"单击此是{tool_bar_action['text']}\n{tool_bar_action['tip']}")
        # 状态栏提示
        self.tutorial.add_step(self.status_bar.time_label,
                               f"显示当前时间。")
        self.tutorial.add_step(self.status_bar.app_status_label,
                               f"显示当前程序状态 。\n 1.INITIALIZED: 初始化状态\n 2.APPLYING: 应用实验状态\n 3.CONFIGURING: 设备配置状态\n 4.MONITORING: 开始监测数据状态")
        self.tutorial.add_step(self.status_bar.status_label,
                               f"显示当前实验状态。\n1.未开始实验。2.运行中。3.停止中或已停止。")
        self.tutorial.add_step(self.status_bar.tip_label,
                               f"显示当前帮助消息。")
        self.tutorial.add_step(self.status_bar.setting_file_name_label,
                               f"显示当前实验设置文件路径。")

        self.tutorial.add_step(self.status_bar.progress_bar,
                               f"显示进度条。")
        #步骤提示
        widgets = self.findChildren(QObject, "temp_deleted_widget")
        for action in actions:
            action: QAction
            menu: QMenu = action.menu()
            if menu:
                # 获取菜单栏中的按钮控件
                geometry = self.menuBar().actionGeometry(action)
                if not widgets:
                    widget = QWidget()
                    widget.setParent(self)
                    widget.setGeometry(geometry)
                    widget.setObjectName("temp_deleted_widget")

                    # 对 file_menu 进行操作
                    self.tutorial.add_step(widget,
                                           f"不知道怎么操作？请跟着步骤指引\n1.单击{action.text()}菜单\n2.在单击打开或导入")
                else:
                    widget = widgets.pop(0)
                    widget.show()
                    self.tutorial.add_step(widget,
                                           f"不知道怎么操作？请跟着步骤指引\n1.单击{action.text()}菜单\n2.在单击打开或导入")
            break
        self.tutorial.add_step(self.status_bar.tip_btn,
                               f"Tips：\n如果还不会操作，可再次单击该按钮查看教程。")
    def __init__(self):
        super().__init__()
        self._install_wheel_guard()
        # 开始实验dialog
        self.start_dialog:AnimatedLoadingDialog=None
        # 停止实验dialog
        self.stop_dialog:AnimatedLoadingDialog=None
        # ★ 新增：气路成功标志 & 备用超时计时器
        self._gas_path_success = False
        self._gas_path_timeout_timer = None
        self._startup_progress_timer = None
        self._startup_progress_started_at = None
        self._startup_progress_duration = None
        self._stop_progress_started_at = None
        self._stop_progress_duration = None
        self._stop_progress_timer = None
        self._stop_timeout_timer = None
        self._stop_backend_complete = False
        self._stop_backend_grace_complete = False
        self._stop_operation_failed = False
        self._stop_cleanup_started = False
        # Calibration values can arrive faster than matplotlib can redraw.
        # Keep data ingestion immediate but coalesce chart repaints on the GUI
        # thread so the queue reader never blocks on QWidget work.
        self._calibration_chart_refresh_timer = QTimer(self)
        self._calibration_chart_refresh_timer.setSingleShot(True)
        self._calibration_chart_refresh_timer.setInterval(200)
        self._calibration_chart_refresh_timer.timeout.connect(
            self._flush_calibration_chart_refresh
        )
        read_queue_data_thread.gas_path_start_completed_signal.connect(
            self._on_gas_path_start_success
        )
        read_queue_data_thread.start_failed_signal.connect(
            self._on_start_experiment_failed
        )
        read_queue_data_thread.operation_progress_signal.connect(
            self._on_operation_progress
        )
        read_queue_data_thread.calibration_event_signal.connect(
            self._on_calibration_event
        )
        read_queue_data_thread.calibration_detail_update_signal.connect(
            self._on_calibration_detail_update
        )
        read_queue_data_thread.status_tip_signal.connect(
            self.update_status_tip_signal.emit
        )
        read_queue_data_thread.cage_control_response_signal.connect(
            self._on_cage_control_response
        )
        read_queue_data_thread.stop_backend_completed_signal.connect(
            self._on_stop_backend_complete
        )
        read_queue_data_thread.stop_backend_issue_signal.connect(
            self._on_stop_backend_issue
        )
        read_queue_data_thread.stop_progress_signal.connect(
            self._on_stop_progress
        )
        #暂停实验标志位
        self.is_paused = False
        # 点击开始实验 接受数据和存储数据的线程
        self.store_thread_sub=None
        self.send_thread_sub=None
        self.read_queue_data_thread_sub=None
        self.add_message_thread_sub=None
        self.ufc_ugc_zos:UFC_UGC_ZOS_index=None
        self.ufc_ugc_zos_thread=None
        # 深度相机线程
        self.deep_camera_thread_sub_list=[]
        self.deep_camera_read_queue_data_thread_sub=None
        self.deep_camera_delete_file_thread_sub=None
        # 红外相机线程
        self.infrared_camera_thread_sub_list = []
        self.infrared_camera_read_queue_data_thread_sub = None
        self.infrared_camera_delete_file_thread_sub = None
        # tool——bar-action 工具栏的action [{'obj_name':'','name';",'action':QAction,'tip':''}]
        self.tool_bar_actions = []
        self.menu_bar_actions = []
        # 添加动态工具栏相关属性
        self.dynamic_tool_bar_actions = []
        self.dynamic_toolbar_separators = []
        self.static_toolbar_actions = []
        self.current_active_menu_id = None
        # 模块
        self.modules =[]
        # 正在显示的Widget
        self.active_module_widgets:[BaseModule]=[]
        # 打开的窗口
        self.open_windows:[BaseModule]=[]
        # 校准气路的窗口
        self.calibration_details_windows: CalibrationDialog = None
        self.calibration_detail_log_buffer = []
        # GUI-owned lifecycle log.  The legacy start/stop dialogs stay hidden,
        # but every line they receive is mirrored here for the workflow page.
        self.workflow_runtime_log_buffer = []
        self.calibration_detail_status_text = None
        self._active_calibration_channels = set()
        # Snapshot the route used by the current experiment.  The backend
        # always sends zero/span stop commands as part of its legacy shutdown
        # sequence, even when this experiment did not request calibration.
        # Keeping the route here lets the GUI hide those housekeeping messages
        # without changing the proven hardware shutdown order.
        self._experiment_calibration_mode = "none"
        self.calibration_detail_zero_start_time = None
        self.calibration_detail_zero_end_time = None
        self.calibration_detail_span_start_time = None
        self.calibration_detail_span_end_time = None
        # 工具栏
        self.toolbar = None
        #状态栏
        self.status_bar:CustomStatusBar = None
        # 内容layout
        self.content_layout :QVBoxLayout =None
        # tab_widget
        self.tab_widget :QTabWidget =None
        # Persistent workflow shell and its GUI-local health state.  These
        # objects intentionally sit outside dynamically replaced module tabs.
        self.workflow_shell: WorkflowShell = None
        self.system_status_page: SystemStatusPage = None
        self.health_store: HealthStore = None
        self.health_queue_drain_timer: QTimer = None
        self.workflow_run_state = ExperimentRunState.NOT_STARTED
        self._system_status_summary_cache = {}
        self._system_status_next_file_probe_at = 0.0
        # The initial P1 route is queued after the GUI is built.  If a user
        # clicks another workflow card before that queued callback runs, do
        # not let the late P1 callback replace the requested page.
        self._workflow_initial_route_pending = True
        # 实例化ui
        self._init_ui()
        self.periodic_export_lock = threading.Lock()
        self.periodic_export_in_progress = False
        self.periodic_export_thread = None
        self.manual_export_thread = None
        self.manual_export_progress = None
        self.stop_export_thread = None
        self.stop_export_finished = True
        self.stop_dialog_close_pending = False
        self.pending_stop_export_success_path = None
        self._close_after_stop = False
        self._close_without_confirmation = False
        self.periodic_export_timer = QTimer(self)
        self.periodic_export_timer.setSingleShot(False)
        self.periodic_export_timer.setInterval(self._get_periodic_export_interval_ms())
        self.periodic_export_timer.timeout.connect(self.trigger_periodic_excel_export)
        # 实例化自定义ui
        self._init_customize_ui()
        # 实例化功能
        self._init_function()
        # 加载qss样式表
        self._init_custom_style_sheet()
        self._retranslateUi()
        # 实例化提示器
        self.setup_tutorial()
        # 自动启动提示教程 如果有提示页面的话
        QTimer.singleShot(400, self.start_tutorial_if_exists)
        pass
    # 实例化ui
    def _init_ui(self, title=""):
        # 将ui文件转成py文件后 直接实例化该py文件里的类对象  uic工具转换之后就是这一段代码
        self.ui = Ui_MainWindow()
        self.ui.setupUi(self)
        # 设置窗口大小为屏幕大小
        self.setGeometry(global_setting.get_setting("screen"))
        self.setObjectName("mainWindow_Index")
        pass
    def _init_customize_ui(self):
        global read_queue_data_thread
        read_queue_data_thread.queue = global_setting.get_setting("queue",None)
        read_queue_data_thread.window=self
        self.content_layout = self.findChild(QVBoxLayout,"content_layout")
        self.tab_widget:QTabWidget = self.findChild(QTabWidget,"tab_widget")
        self.content_layout.setSpacing(6)
        self._init_workflow_shell()
        # 启用标签关闭按钮
        self.tab_widget.setTabsClosable(True)
        # 允许标签拖动重新排序
        self.tab_widget.setMovable(True)
        # 连接标签关闭信号
        self.tab_widget.tabCloseRequested.connect(self.close_tab)
        # 加载模块
        self.modules = self.load_modules()
        # Legacy modules eagerly construct top-level windows. Keep those
        # compatibility objects hidden while the persistent workflow is used.
        self._hide_legacy_module_windows()
        # 实例化菜单
        # [{id:0,text:"文件"},....]
        menu_name=global_setting.get_setting("configer")['menu']['menu_name']
        self.menu_name = None
        if menu_name is not None and menu_name != "":
            try:
                self.menu_name =  json.loads(menu_name)
            except JSONDecodeError  as e:
                logger.error(f"读取菜单json字符串解析错误：{e}")
                self.menu_name = None
            except Exception as e:
                logger.error(f"{e}")
                self.menu_name = None
            if self.menu_name is not None:
                # 创建菜单栏
                # Legacy menu actions are no longer created; navigation is
                # provided by the persistent six-step workflow shell.
                self.menuBar().setStyleSheet("""
                    QMenuBar{
                        font-size:15px;
                    }
                    QMenuBar::item {
                        font-size: 15px;
                        padding: 4px 10px;
                    }
                """)
            pass
        # 创建工具栏
        self.create_tool_bar()
        # 底部旧状态栏已从新版界面移除。保留一个隐藏的兼容对象，
        # 让旧模块发送的 update_tip/update_status 调用不会中断实验流程。
        self.status_bar = CustomStatusBar(self)
        self.setStatusBar(self.status_bar)
        self.status_bar.hide()
        super()._init_customize_ui()
        pass
    def _install_wheel_guard(self):
        """Prevent accidental value changes when scrolling over form controls."""
        application = QApplication.instance()
        if application is None or getattr(self, "_wheel_guard_installed", False):
            return
        application.installEventFilter(self)
        self._wheel_guard_installed = True

    def eventFilter(self, watched, event):
        # Legacy modules may still create a parentless BaseWindow when their
        # service is initialised.  Block only those unnamed/python QMainWindow
        # instances at the Show event; do not intercept popup containers,
        # dialogs, menus, or ordinary controls so their buttons remain usable.
        if event.type() == QEvent.Type.Show and self._is_legacy_top_level_window(watched):
            try:
                watched.hide()
            except RuntimeError:
                pass
            QTimer.singleShot(0, self._hide_legacy_module_windows)
            return True
        if event.type() == QEvent.Type.Wheel and isinstance(
            watched,
            (QAbstractSpinBox, QComboBox, QDial, QSlider, QCheckBox, QRadioButton),
        ):
            event.ignore()
            return True
        return super().eventFilter(watched, event)

    def _is_legacy_top_level_window(self, widget):
        """Return whether *widget* is an unnamed legacy module window."""
        if widget is None or widget is self or not isinstance(widget, QWidget):
            return False
        try:
            if not widget.isWindow() or widget.parentWidget() is not None:
                return False
            if isinstance(widget, (QDialog, QMenu)):
                return False
            window_type = widget.windowType()
            if window_type in {
                Qt.WindowType.Popup,
                Qt.WindowType.ToolTip,
                Qt.WindowType.Tool,
                Qt.WindowType.SplashScreen,
            }:
                return False
            title = str(widget.windowTitle() or "").strip().lower()
            # Legacy Qt widgets often are plain QFrame/QWidget objects, but
            # Windows still gives their parentless window the "python" title.
            # Restrict the cleanup to unnamed windows so real standalone
            # dialogs/tools remain usable.
            return title in {"", "python"}
        except RuntimeError:
            return False
    def _init_function(self):
        # 改变组件是否被点击
        self.change_enable_component_app_state()
        # 连接信号
        self.change_enable_component_app_state_signal.connect(self.change_enable_component_app_state)
        self.show_calibration_window_signal.connect(self.show_calibration_windows)
        self.release_calibration_window_signal.connect(self.release_calibration_windows)
        # 新增连接
        self.update_status_tip_signal.connect(self.status_bar.update_tip)
        self.show_temp_status_tip_signal.connect(self.status_bar.show_temp_tip)
        self.deep_camera_instance_state_changed_signal.connect(
            self._on_deep_camera_instance_state_changed
        )
        # 消息可能在窗口构造前已经进入队列。所有 GUI 控件和信号准备好后再消费，
        # 否则一次早到的状态消息就会让整个消息线程永久退出。
        if not read_queue_data_thread.isRunning():
            read_queue_data_thread.start()
        self._start_health_queue_drain()
        # The persistent system-status page is the default landing page.  It
        # is inserted after the event loop can process widget geometry.
        QTimer.singleShot(0, self._show_initial_workflow_page)
        pass

    def _show_initial_workflow_page(self):
        """Render the default P1 page once, without overriding early clicks."""
        if not self._workflow_initial_route_pending:
            return
        self._workflow_initial_route_pending = False
        self.show_system_status_page()

    @staticmethod
    def _as_positive_int(value, default):
        try:
            parsed = int(float(value))
            return parsed if parsed > 0 else default
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _as_positive_float(value, default):
        try:
            parsed = float(value)
            return parsed if parsed > 0 else default
        except (TypeError, ValueError):
            return default

    def _build_health_policy(self):
        health_config = global_setting.get_setting("configer", {}).get("system_health", {})
        return HealthPolicy(
            failure_threshold=self._as_positive_int(health_config.get("failure_threshold"), 3),
            recovery_success_threshold=self._as_positive_int(
                health_config.get("recovery_success_threshold"), 2
            ),
            default_stale_after_seconds=self._as_positive_float(
                health_config.get("camera_stale_seconds"), 10.0
            ),
            process_heartbeat_timeout_seconds=self._as_positive_float(
                health_config.get("process_heartbeat_timeout_seconds"), 30.0
            ),
        )

    def _init_workflow_shell(self):
        self.health_store = HealthStore(policy=self._build_health_policy())
        self.health_store.set_expected_components(
            {
                "monitor_collection",
                "gas_path",
                "data_storage",
                "deep_camera",
                "infrared_camera",
                "trajectory",
            }
        )
        self.health_store.set_experiment_state(self.workflow_run_state)
        self.workflow_shell = WorkflowShell(self)
        self.workflow_shell.workflow_requested.connect(self.navigate_workflow_step)
        self.workflow_shell.alert_requested.connect(self.show_system_status_alerts)
        self.content_layout.insertWidget(0, self.workflow_shell)
        self._refresh_global_workflow_status()

    def sync_workflow_step_for_legacy_menu(self, menu_id):
        """Keep a legacy menu selection aligned with the persistent shell."""

        step = resolve_legacy_workflow_step(menu_id=menu_id)
        if step is not None and self.workflow_shell is not None:
            self.workflow_shell.set_active_step(step)

    def sync_workflow_step_for_legacy_module(self, module):
        """Keep a specific legacy module aligned with P2--P6.

        Some historical modules share menu ``2``.  Resolve their stable name
        here so data view and machine vision do not appear as experiment
        control merely because they use the same old menu group.
        """

        menu = getattr(module, "menu_name", None)
        menu_id = menu.get("id") if isinstance(menu, dict) else None
        step = resolve_legacy_workflow_step(
            module_name=getattr(module, "name", None),
            menu_id=menu_id,
        )
        if step is not None and self.workflow_shell is not None:
            self.workflow_shell.set_active_step(step)
        if step is not None and step != 1:
            # A legacy module will replace the tab content below.  Do not keep
            # a pointer to an about-to-be-deleted P1 widget.
            self.system_status_page = None

    def _start_health_queue_drain(self):
        if self.health_queue_drain_timer is None:
            self.health_queue_drain_timer = QTimer(self)
            self.health_queue_drain_timer.setInterval(250)
            self.health_queue_drain_timer.timeout.connect(self._drain_health_snapshot_queue)
        self.health_queue_drain_timer.start()
        self._drain_health_snapshot_queue()

    def _drain_health_snapshot_queue(self):
        if self.health_store is None:
            return
        health_queue = global_setting.get_setting("system_health_snapshot_queue", None)
        if health_queue is not None:
            try:
                self.health_store.drain_queue(health_queue, max_items=128)
            except (OSError, EOFError, ValueError) as error:
                logger.debug(f"health snapshot queue unavailable: {error}")
        self.health_store.check_staleness(experiment_state=self.workflow_run_state)
        self._refresh_global_workflow_status()

    def _set_workflow_run_state(self, state):
        try:
            self.workflow_run_state = (
                state if isinstance(state, ExperimentRunState) else ExperimentRunState(str(state))
            )
        except ValueError:
            self.workflow_run_state = ExperimentRunState.STOPPED
        if self.workflow_run_state in {ExperimentRunState.NOT_STARTED, ExperimentRunState.STOPPED}:
            self._active_calibration_channels.clear()
        if self.health_store is not None:
            self.health_store.set_experiment_state(self.workflow_run_state)
        self._refresh_global_workflow_status()

    def _mark_workflow_operation_failed(self, phase, error):
        """Show a lifecycle failure without relabelling it as device damage."""
        if str(phase).lower() == "start":
            self._stop_startup_progress()
        error_text = str(error or "unknown operation error")
        log_type = "启动" if str(phase).lower() == "start" else "停止"
        self._append_workflow_runtime_log(
            f"{log_type}流程失败：{error_text}",
            log_type,
        )
        logger.error(f"{phase} experiment operation failed: {error_text}")
        if str(phase).lower() == "start":
            self._set_workflow_run_state(ExperimentRunState.START_FAILED)
            status_text = "启动实验失败，请检查配置和连接后重试。"
        else:
            self._stop_stop_progress()
            self._stop_operation_failed = True
            self._set_workflow_run_state(ExperimentRunState.STOP_FAILED)
            status_text = "停止流程存在错误，请查看系统健康与运行日志。"
        if self.status_bar is not None:
            self.status_bar.update_tip(status_text)
        page = getattr(self, "experiment_control_page", None)
        if page is not None:
            page.set_operation_progress(0, "启动失败" if str(phase).lower() == "start" else "终止失败")
            page.operation_hint.setText(f"{status_text} 原因：{error_text}")
            page.refresh()
        self.setEnabled(True)

    def _on_operation_progress(self, value: int, label: str) -> None:
        """Apply worker progress on the GUI thread to the current P3 page."""
        page = getattr(self, "experiment_control_page", None)
        if page is not None:
            # Stop status owns the shared operation area once termination begins.
            # Late zero/span replies must not replace "stopping/stopped" text.
            if self.workflow_run_state in {
                ExperimentRunState.STOPPING,
                ExperimentRunState.STOPPED,
                ExperimentRunState.STOP_FAILED,
            }:
                return
            # A calibration phase has its own 0..100 scale. During startup the
            # shared bar follows the legacy dialog's overall elapsed-time scale.
            if self.workflow_run_state == ExperimentRunState.STARTING:
                page.set_operation_progress(page.operation_progress.value(), label)
            else:
                page.set_operation_progress(value, label)

    def _stop_startup_progress(self):
        timer = getattr(self, "_startup_progress_timer", None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
            self._startup_progress_timer = None

    def _stop_stop_progress(self):
        timer = getattr(self, "_stop_progress_timer", None)
        if timer is not None:
            timer.stop()
            timer.deleteLater()
            self._stop_progress_timer = None

    def _advance_stop_progress(self):
        if self.workflow_run_state != ExperimentRunState.STOPPING:
            self._stop_stop_progress()
            return
        page = getattr(self, "experiment_control_page", None)
        if page is None or self._stop_progress_started_at is None:
            return
        duration = max(float(self._stop_progress_duration or 1), 1.0)
        percent = min(99, int((time.monotonic() - self._stop_progress_started_at) / duration * 100))
        if percent > page.operation_progress.value():
            page.set_operation_progress(percent, "正在停止实验")

    def _advance_startup_progress(self):
        if self.workflow_run_state != ExperimentRunState.STARTING:
            self._stop_startup_progress()
            return
        page = getattr(self, "experiment_control_page", None)
        if page is None or self._startup_progress_started_at is None:
            return
        duration = max(float(self._startup_progress_duration or 1), 1)
        percent = min(99, int((time.monotonic() - self._startup_progress_started_at) / duration * 100))
        if percent > page.operation_progress.value():
            page.set_operation_progress(percent)

    def _on_calibration_event(self, event: str) -> None:
        channel, _, phase = event.partition("_")
        if channel in {"zero", "span"}:
            if phase == "started":
                self._active_calibration_channels.add(channel)
            elif phase == "finished":
                self._active_calibration_channels.discard(channel)
        page = getattr(self, "experiment_control_page", None)
        if page is not None and self.workflow_run_state not in {
            ExperimentRunState.STOPPING,
            ExperimentRunState.STOPPED,
            ExperimentRunState.STOP_FAILED,
        }:
            page.on_calibration_event(event)

    def _on_calibration_detail_update(self, update) -> None:
        """Apply calibration-detail updates on the GUI thread only.

        ``read_queue_data_Thread`` is a worker thread.  Calling the dialog or
        its matplotlib canvas directly from that thread was the source of the
        long GUI stalls seen during Air calibration and could race with dialog
        destruction during stop/close.  This slot is connected to the worker
        signal, so Qt queues it to this MainWindow's thread.
        """
        if not isinstance(update, dict):
            return

        kind = update.get("kind")
        if kind == "message":
            message = update.get("message")
            if self.status_bar is not None:
                self.status_bar.update_tip(message)
            self.cache_calibration_detail_log(
                message,
                has_time=bool(update.get("has_time", True)),
            )
            return

        event_time = update.get("time")
        if kind == "zero_start":
            self.calibration_detail_zero_start_time = event_time
            self.calibration_detail_status_text = "零点标定"
            if self.calibration_details_windows is not None:
                self.calibration_details_windows.updateZeroStartTime(
                    event_time, event_time=event_time
                )
                self.calibration_details_windows.updateStatus(
                    "零点标定", event_time=event_time
                )
            return
        if kind == "zero_end":
            self.calibration_detail_zero_end_time = event_time
            self.calibration_detail_status_text = "零点标定完成"
            if self.calibration_details_windows is not None:
                self.calibration_details_windows.updateZeroEndTime(
                    event_time, event_time=event_time
                )
                self.calibration_details_windows.updateStatus(
                    "零点标定完成", event_time=event_time
                )
            return
        if kind == "span_start":
            self.calibration_detail_span_start_time = event_time
            self.calibration_detail_status_text = "量程标定"
            if self.calibration_details_windows is not None:
                self.calibration_details_windows.updateSpanStartTime(
                    event_time, event_time=event_time
                )
                self.calibration_details_windows.updateStatus(
                    "量程标定", event_time=event_time
                )
            return
        if kind == "span_end":
            self.calibration_detail_span_end_time = event_time
            self.calibration_detail_status_text = "量程标定完成"
            if self.calibration_details_windows is not None:
                self.calibration_details_windows.updateSpanEndTime(
                    event_time, event_time=event_time
                )
                self.calibration_details_windows.updateStatus(
                    "量程标定完成", event_time=event_time
                )
            return
        if kind == "air_start":
            self.calibration_detail_zero_start_time = event_time
            self.calibration_detail_status_text = "Air空气校准"
            if self.calibration_details_windows is not None:
                self.calibration_details_windows.updateAirStartTime(
                    event_time, event_time=event_time
                )
                self.calibration_details_windows.updateStatus(
                    "Air空气校准", event_time=event_time
                )
            return
        if kind == "air_end":
            self.calibration_detail_zero_end_time = event_time
            self.calibration_detail_status_text = "Air空气校准完成"
            if self.calibration_details_windows is not None:
                self.calibration_details_windows.updateAirEndTime(
                    event_time, event_time=event_time
                )
                self.calibration_details_windows.updateStatus(
                    "Air空气校准完成", event_time=event_time
                )
            return
        if kind != "values":
            return

        # A stop can release the dialog while queued values are still being
        # delivered.  Ignore those late values instead of dereferencing a
        # closing QWidget.
        if self.workflow_run_state in {
            ExperimentRunState.STOPPING,
            ExperimentRunState.STOPPED,
            ExperimentRunState.STOP_FAILED,
        }:
            return
        dialog = self.calibration_details_windows
        if dialog is None:
            return
        values = update.get("values") or {}
        dialog.updateCalibrationValues(
            values.get("oxygen_value", 0),
            values.get("carbon_value", 0),
            values.get("oxygen_pressure_value", 0),
            event_time=event_time,
            redraw=False,
        )
        if not self._calibration_chart_refresh_timer.isActive():
            self._calibration_chart_refresh_timer.start()

    def _flush_calibration_chart_refresh(self) -> None:
        """Redraw the calibration chart at most five times per second."""
        dialog = self.calibration_details_windows
        if dialog is None:
            return
        chart = getattr(dialog, "chart_widget", None)
        if chart is not None:
            chart.updateChart()

    def _on_cage_control_response(self, response) -> None:
        """Forward the real device acknowledgement to the visible P3 page."""
        page = getattr(self, "experiment_control_page", None)
        if page is not None:
            page.on_cage_control_response(response)

    def _on_start_experiment_failed(self, reason: str) -> None:
        if self._gas_path_timeout_timer is not None:
            self._gas_path_timeout_timer.stop()
        self._mark_workflow_operation_failed("start", reason)

    def _reject_experiment_start(self, reason: str) -> None:
        self._stop_startup_progress()
        self._append_workflow_runtime_log(f"启动未执行：{reason}", "启动")
        page = getattr(self, "experiment_control_page", None)
        if page is not None:
            page.set_operation_progress(0, "启动未执行")
            page.operation_hint.setText(reason)
            page.start_button.setEnabled(True)
        if self.status_bar is not None:
            self.status_bar.update_tip(f"启动未执行：{reason}")
        self._set_workflow_run_state(ExperimentRunState.CONFIGURING)
        self.setEnabled(True)

    def _reset_workflow_runtime_log(self) -> None:
        self.workflow_runtime_log_buffer = []

    def _append_workflow_runtime_log(self, message, event_type="运行") -> None:
        """Store legacy dialog lines for the P3 runtime-log table."""
        text = str(message or "").strip()
        if not text:
            return
        buffer = getattr(self, "workflow_runtime_log_buffer", None)
        if not isinstance(buffer, list):
            buffer = []
            self.workflow_runtime_log_buffer = buffer
        stamp = time.strftime("%H:%M:%S")
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            # The legacy dialog line often already contains a full timestamp;
            # P3 has its own time column, so keep only the actual event text.
            display_line = re.sub(
                r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}(?:\.\d+)?\s*\|\s*",
                "",
                line,
            ).strip()
            kind = str(event_type or "运行")
            if buffer:
                previous = buffer[-1]
                previous_base = previous.get("base_message", previous.get("message", ""))
                if previous_base == display_line and previous.get("time") == stamp and previous.get("type") != kind:
                    if kind == "校准":
                        previous["type"] = kind
                    continue
                if previous.get("type") == kind and previous_base == display_line:
                    repeat_count = int(previous.get("repeat_count", 1)) + 1
                    previous["repeat_count"] = repeat_count
                    previous["time"] = stamp
                    previous["message"] = f"{display_line}（重复 {repeat_count} 次）"
                    continue
            buffer.append({
                "time": stamp,
                "type": kind,
                "message": display_line,
                "base_message": display_line,
                "repeat_count": 1,
            })
        if len(buffer) > 1000:
            del buffer[:-1000]

    def _attach_legacy_dialog_runtime_log(self, dialog, event_type: str) -> None:
        """Mirror a hidden legacy dialog's message signal into the P3 log."""
        if dialog is None or getattr(dialog, "_workflow_log_forwarding_connected", False):
            return
        dialog.insert_data_signal.connect(
            lambda message, kind=event_type: self._append_workflow_runtime_log(message, kind)
        )
        dialog._workflow_log_forwarding_connected = True

    def _refresh_global_workflow_status(self):
        if self.workflow_shell is None or self.health_store is None:
            return
        self.workflow_shell.set_experiment_state(self.workflow_run_state)
        raw_issues = self.health_store.active_issues()
        actionable_issues = [
            issue
            for issue in raw_issues
            if not is_ignored_alert_reason(issue.reason_code)
        ]
        if actionable_issues:
            summary_state = max(
                (issue.state for issue in actionable_issues),
                key=lambda state: state.severity,
            )
        elif raw_issues:
            summary_state = HealthState.NORMAL
        else:
            summary_state = self.health_store.overall_state()
        self.workflow_shell.set_health_summary(
            summary_state,
            len(actionable_issues),
        )

    def _system_health_view(self):
        snapshots = self.health_store.snapshots() if self.health_store is not None else []
        components = []
        alerts = []
        for snapshot in snapshots:
            item = {
                "component": snapshot.component,
                "scope": snapshot.scope,
                "status": snapshot.state.value,
                "reason_code": snapshot.reason_code,
                "message": snapshot.message,
                "last_success_at": snapshot.last_success_at,
                "consecutive_failures": snapshot.consecutive_failures,
                "consecutive_successes": snapshot.consecutive_successes,
                "observed_at": snapshot.observed_at,
                "metrics": dict(snapshot.metrics),
                "enabled": snapshot.enabled,
            }
            components.append(item)
            if snapshot.enabled and snapshot.state in {
                HealthState.WARNING,
                HealthState.FAULT,
                HealthState.UNAVAILABLE,
            }:
                alerts.append(item)
        return {"components": components, "alerts": alerts}

    def _system_status_environment(self, snapshots):
        keys = ("temperature", "humidity", "noise", "light", "oxygen", "co2")
        environment = {key: {"value": None} for key in keys}
        updated_at = {key: float("-inf") for key in keys}
        for snapshot in snapshots:
            metrics = dict(snapshot.metrics or {})
            snapshot_time = snapshot.observed_at or snapshot.last_success_at or 0.0
            source = metrics.get("environment")
            if isinstance(source, dict):
                for key in keys:
                    candidate = source.get(key)
                    if candidate is None:
                        continue
                    if isinstance(candidate, dict):
                        if candidate.get("value") is None:
                            continue
                        candidate_time = candidate.get("updated_at", snapshot_time)
                    else:
                        candidate_time = snapshot_time
                    try:
                        candidate_time = float(candidate_time)
                    except (TypeError, ValueError):
                        candidate_time = float(snapshot_time or 0.0)
                    if candidate_time >= updated_at[key]:
                        environment[key] = candidate
                        updated_at[key] = candidate_time
            for key in keys:
                if (
                    key in metrics
                    and metrics[key] is not None
                    and float(snapshot_time or 0.0) >= updated_at[key]
                ):
                    environment[key] = metrics[key]
                    updated_at[key] = float(snapshot_time or 0.0)
        return environment

    def _system_status_file_summary(self, experiment_root_path, enabled_channels, now):
        """Read real experiment totals at a low rate for the P1 summary.

        The page must not invent counters.  Epoch rows are the persisted,
        per-channel measurement points, while the folder walk includes every
        generated experiment artifact (database, CSV/XLSX, images and video).
        A missing database/folder therefore remains ``None`` rather than being
        rendered as zero.
        """
        if now < self._system_status_next_file_probe_at:
            return {
                "storage_size": self._system_status_summary_cache.get("storage_size"),
                "data_points_total": self._system_status_summary_cache.get("data_points_total"),
            }

        self._system_status_next_file_probe_at = now + 10.0
        summary = {"storage_size": None, "data_points_total": None}
        if not experiment_root_path or not os.path.isdir(experiment_root_path):
            self._system_status_summary_cache.update(summary)
            return summary

        total_size = 0
        try:
            for directory, _subdirectories, filenames in os.walk(experiment_root_path):
                for filename in filenames:
                    path = os.path.join(directory, filename)
                    try:
                        if not os.path.islink(path):
                            total_size += os.path.getsize(path)
                    except OSError:
                        continue
            # Periodic XLSX/CSV exports are stored next to the experiment
            # directory by the legacy exporter; include them in the same
            # experiment total when present.
            for suffix in (".xlsx", ".csv", ".zip"):
                export_path = f"{experiment_root_path}{suffix}"
                if os.path.isfile(export_path):
                    try:
                        total_size += os.path.getsize(export_path)
                    except OSError:
                        continue
            summary["storage_size"] = total_size
        except OSError as error:
            logger.warning(f"system status file-size probe failed: {error}")

        db_path = os.path.join(experiment_root_path, "data", "data.db")
        if os.path.isfile(db_path):
            connection = None
            try:
                connection = sqlite3.connect(db_path, timeout=0.25)
                table_rows = connection.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type = 'table' AND name LIKE 'Epoch_data_cage_%' "
                    "AND name NOT LIKE '%_meta'"
                ).fetchall()
                configured = {
                    str(channel).upper()
                    for channel in (enabled_channels or [])
                    if str(channel).upper().startswith("CH")
                }
                total_points = 0
                for (table_name,) in table_rows:
                    suffix = str(table_name).rsplit("_", 1)[-1]
                    try:
                        channel_name = f"CH{int(suffix):02d}"
                    except (TypeError, ValueError):
                        channel_name = ""
                    if configured and channel_name not in configured:
                        continue
                    quoted = '"' + str(table_name).replace('"', '""') + '"'
                    row = connection.execute(f"SELECT COUNT(*) FROM {quoted}").fetchone()
                    total_points += int(row[0] or 0) if row else 0
                summary["data_points_total"] = total_points
            except (OSError, sqlite3.Error, ValueError) as error:
                logger.warning(f"system status data-point probe failed: {error}")
            finally:
                if connection is not None:
                    connection.close()

        self._system_status_summary_cache.update(summary)
        return summary

    def _system_status_context(self):
        now = time.time()
        setting_file = global_setting.get_setting("experiment_setting_file", "") or ""
        experiment_id = os.path.splitext(os.path.basename(setting_file))[0] if setting_file else "未加载实验"
        start_time = global_setting.get_setting("start_experiment_time", None)
        try:
            start_time = float(start_time) if start_time is not None else None
        except (TypeError, ValueError):
            start_time = None

        elapsed_seconds = None
        if start_time and start_time > 0:
            end_time = now
            if self.workflow_run_state in {
                ExperimentRunState.STOPPING,
                ExperimentRunState.STOPPED,
                ExperimentRunState.STOP_FAILED,
            }:
                try:
                    end_time = float(global_setting.get_setting("stop_experiment_time", now))
                except (TypeError, ValueError):
                    end_time = now
            elapsed_seconds = max(end_time - start_time, 0.0)

        plan_config = global_setting.get_setting("configer", {}).get("experiment_plan", {})
        planned_duration_hours = self._as_positive_float(
            plan_config.get("planned_duration_hours"), 0.0
        )
        planned_duration_seconds = planned_duration_hours * 3600 if planned_duration_hours else None
        remaining_seconds = (
            max(planned_duration_seconds - elapsed_seconds, 0.0)
            if planned_duration_seconds is not None and elapsed_seconds is not None
            else None
        )
        progress_percent = (
            min(100.0, 100.0 * elapsed_seconds / planned_duration_seconds)
            if planned_duration_seconds and elapsed_seconds is not None
            else None
        )

        operation_countdown_seconds = None
        operation_countdown_phase = None
        if self.workflow_run_state == ExperimentRunState.STARTING:
            countdown_started_at = self._startup_progress_started_at
            countdown_duration = self._startup_progress_duration
            operation_countdown_phase = "启动"
        elif self.workflow_run_state == ExperimentRunState.STOPPING:
            countdown_started_at = self._stop_progress_started_at
            countdown_duration = self._stop_progress_duration
            operation_countdown_phase = "停止"
        else:
            countdown_started_at = None
            countdown_duration = None
        if countdown_started_at is not None and countdown_duration is not None:
            operation_countdown_seconds = max(
                0,
                int(math.ceil(float(countdown_duration) - (time.monotonic() - countdown_started_at))),
            )

        snapshots = self.health_store.snapshots() if self.health_store is not None else []
        # Prefer the applied experiment setting: a channel is enabled because
        # it was selected, not because it happened to emit one health sample.
        enabled_channels = []
        experiment_setting = global_setting.get_setting("experiment_setting", None)
        for group in getattr(experiment_setting, "groups", []) or []:
            if not bool(getattr(group, "is_selected", False)):
                continue
            try:
                enabled_channels.append(f"CH{int(getattr(group, 'id')):02d}")
            except (TypeError, ValueError):
                enabled_channels.append(str(getattr(group, "id", "")).strip())
        enabled_channels = sorted({item for item in enabled_channels if item})
        if not enabled_channels:
            enabled_channels = sorted(
                {
                    snapshot.scope
                    for snapshot in snapshots
                    if snapshot.enabled and str(snapshot.scope).upper().startswith("CH")
                }
            )
        storage_snapshot = None
        for snapshot in snapshots:
            if snapshot.component in {"storage", "database", "data_storage"}:
                storage_snapshot = snapshot

        experiment_root_path = None
        if start_time is not None and start_time > 0:
            experiment_root_path = self._get_experiment_root_path()
        file_summary = self._system_status_file_summary(
            experiment_root_path,
            enabled_channels,
            now,
        )

        configured_channel_set = {str(item).upper() for item in enabled_channels}
        online_channels = sorted(
            {
                str(snapshot.scope).upper()
                for snapshot in snapshots
                if snapshot.enabled
                and snapshot.state == HealthState.NORMAL
                and snapshot.component in {
                    "monitor_collection",
                    "monitor_data",
                    "data_collection",
                }
                and str(snapshot.scope).upper().startswith("CH")
                and (
                    not configured_channel_set
                    or str(snapshot.scope).upper() in configured_channel_set
                )
            }
        )
        health_state = (
            self.health_store.overall_state().value
            if self.health_store is not None
            else HealthState.UNKNOWN.value
        )

        channel_individuals = {}
        if experiment_setting is not None:
            animals_by_id = {
                getattr(animal, "id", None): animal
                for animal in (getattr(experiment_setting, "animals", []) or [])
            }
            for record in (getattr(experiment_setting, "animalGroupRecords", []) or []):
                group_id = getattr(record, "gid", None)
                animal = animals_by_id.get(getattr(record, "aid", None))
                if group_id is None or animal is None:
                    continue
                try:
                    channel_key = f"CH{int(group_id):02d}"
                except (TypeError, ValueError):
                    continue
                individual_id = getattr(animal, "id_write", None)
                if individual_id in (None, ""):
                    individual_id = getattr(animal, "id", None)
                channel_individuals[channel_key] = {
                    "id": individual_id,
                    "name": getattr(animal, "name", None),
                }
                # Keep the metadata needed by P6's selected-cage bio card in
                # the GUI context.  Runtime measurements still come from
                # health snapshots/SQLite; these are configuration fields only.
                configured_info = getattr(experiment_setting, "channel_animal_info", {}) or {}
                if isinstance(configured_info, dict):
                    saved_info = configured_info.get(channel_key, {})
                    if isinstance(saved_info, dict):
                        channel_individuals[channel_key].update({
                            "strain": saved_info.get("strain"),
                            "sex": saved_info.get("sex"),
                            "group": saved_info.get("group"),
                        })

        return {
            "experiment_id": experiment_id,
            # The legacy experiment setting does not contain a separately
            # persisted title.  Keep this unknown instead of presenting a
            # filename as a scientific title.
            "experiment_title": None,
            "experiment_status": self.workflow_run_state.value,
            "can_stop_experiment": global_setting.get_setting("app_state", AppState.INITIALIZED) == AppState.MONITORING,
            "started_at": start_time,
            "planned_duration_seconds": planned_duration_seconds,
            "elapsed_seconds": elapsed_seconds,
            "remaining_seconds": remaining_seconds,
            "progress_percent": progress_percent,
            "operation_countdown_seconds": operation_countdown_seconds,
            "operation_countdown_phase": operation_countdown_phase,
            "enabled_channels": enabled_channels if enabled_channels else None,
            "online_channels": online_channels if online_channels else [],
            "health_state": health_state,
            "channel_individuals": channel_individuals,
            "channel_data_config": (
                copy.deepcopy(getattr(experiment_setting, "channel_data_config", {}) or {})
                if experiment_setting is not None else {}
            ),
            "data_points_total": file_summary.get("data_points_total"),
            "last_database_write": (
                storage_snapshot.last_success_at if storage_snapshot is not None else None
            ),
            "storage_size": file_summary.get("storage_size"),
            "experiment_root_path": experiment_root_path,
            "experiment_db_path": (
                os.path.join(experiment_root_path, "data", "data.db")
                if experiment_root_path else None
            ),
            "environment": self._system_status_environment(snapshots),
            # P3 reads a GUI-owned copy of lifecycle messages.  It never
            # consumes the backend queues, so displaying logs cannot steal
            # start/stop acknowledgements from their original consumers.
            "main_logs": list(
                getattr(self, "workflow_runtime_log_buffer", [])
            )[-200:],
        }

    def _clear_main_tab_content(self):
        widgets_to_remove = []
        for index in range(self.tab_widget.count()):
            widget = self.tab_widget.widget(index)
            if widget is not None:
                widgets_to_remove.append(widget)
        for widget in widgets_to_remove:
            try:
                widget.blockSignals(True)
            except RuntimeError:
                pass
        while self.tab_widget.count() > 0:
            widget = self.tab_widget.widget(0)
            self.tab_widget.removeTab(0)
            if widget is not None:
                stop_refresh = getattr(widget, "stop_refresh", None)
                if callable(stop_refresh):
                    stop_refresh()
                widget.deleteLater()
        for attr in ("system_status_page", "experiment_config_page", "experiment_control_page",
                     "data_view_page", "machine_vision_page", "data_analysis_page"):
            setattr(self, attr, None)
        self.active_module_widgets.clear()

    def _hide_legacy_module_windows(self):
        """Hide orphaned legacy windows without touching named dialogs."""
        # Some legacy module frames are top-level QMainWindow instances even
        # though they are owned by a module. Keep their identities so the
        # orphan sweep below does not close a live device-detection window
        # when the user navigates to another workflow page mid-detection.
        protected_legacy_widget_ids = set()
        for module in getattr(self, "modules", []) or []:
            interface = getattr(module, "interface_widget", None)
            if interface is None:
                continue
            try:
                interface.hide()
            except Exception as error:
                logger.debug(f"failed to hide legacy interface: {error}")
            for attr in ("frame_obj", "left_frame_obj", "right_frame_obj", "bottom_frame_obj"):
                widget = getattr(interface, attr, None)
                if widget is None:
                    continue
                protected_legacy_widget_ids.add(id(widget))
                try:
                    widget.hide()
                except Exception as error:
                    logger.debug(f"failed to hide legacy frame {attr}: {error}")

        app = QApplication.instance()
        if app is None:
            return
        for widget in list(app.topLevelWidgets()):
            if widget is self:
                continue
            if id(widget) in protected_legacy_widget_ids:
                continue
            try:
                if self._is_legacy_top_level_window(widget):
                    widget.hide()
                    widget.close()
                    widget.deleteLater()
            except RuntimeError:
                continue
            except Exception as error:
                logger.debug(f"failed to close orphaned top-level window: {error}")

    def show_system_status_page(self):
        if self.tab_widget is None:
            return
        page = self._cached_workflow_page("system_status_page")
        if page is None:
            page = SystemStatusPage(
                health_source=self._system_health_view,
                context_provider=self._system_status_context,
                refresh_interval_ms=1000,
                parent=self.tab_widget,
            )
            self.system_status_page = page
        self._show_workflow_page(page, 1)
        page.refresh()

    def show_system_status_alerts(self):
        if self.system_status_page is None or self.tab_widget.currentWidget() is not self.system_status_page:
            self.show_system_status_page()
        focus_alerts = getattr(self.system_status_page, "focus_alerts", None)
        if callable(focus_alerts):
            focus_alerts()

    def show_experiment_config_page(self):
        """Show the redesigned P2 configuration shell."""
        if self.tab_widget is None:
            return
        page = self._cached_workflow_page("experiment_config_page")
        if page is None:
            page = ExperimentConfigPage(
                health_source=self._system_health_view,
                parent=self.tab_widget,
            )
            self.experiment_config_page = page
        self._show_workflow_page(page, 2)

    def _cached_workflow_page(self, name):
        page = getattr(self, name, None)
        if page is None:
            return None
        try:
            if self.tab_widget.indexOf(page) >= 0:
                return page
        except RuntimeError:
            pass
        setattr(self, name, None)
        return None

    def _show_workflow_page(self, page: QWidget, step: int):
        """Switch workflow pages without destroying their controls or selections."""
        if self.tab_widget is None:
            return
        previous = self.tab_widget.currentWidget()
        if previous is not None and previous is not page:
            stop_refresh = getattr(previous, "stop_refresh", None)
            if callable(stop_refresh):
                stop_refresh()
        if self.tab_widget.indexOf(page) < 0:
            self.tab_widget.addTab(page, "")
        self.tab_widget.tabBar().hide()
        self.tab_widget.setCurrentWidget(page)
        self.workflow_shell.set_active_step(step)
        page.show()
        start_refresh = getattr(page, "start_refresh", None)
        if callable(start_refresh):
            start_refresh()
        # A legacy module can finish constructing an orphan window in the
        # same event-loop turn as this page switch. Run the narrow cleanup
        # once after the switch as a second line of defence.
        QTimer.singleShot(0, self._hide_legacy_module_windows)

    def show_experiment_control_page(self):
        self._hide_legacy_module_windows()
        page = self._cached_workflow_page("experiment_control_page")
        if page is None:
            page = ExperimentControlPage(
                main_window=self,
                context_provider=self._system_status_context,
                health_source=self._system_health_view,
                parent=self.tab_widget,
            )
            self.experiment_control_page = page
            for channel in self._active_calibration_channels:
                page.on_calibration_event(f"{channel}_started")
        self._show_workflow_page(page, 3)
        QTimer.singleShot(0, self._hide_legacy_module_windows)

    def show_data_view_page(self):
        page = self._cached_workflow_page("data_view_page")
        if page is None:
            page = DataViewPage(
                context_provider=self._system_status_context,
                health_source=self._system_health_view,
                parent=self.tab_widget,
            )
            self.data_view_page = page
        self._show_workflow_page(page, 4)

    def show_machine_vision_page(self):
        page = self._cached_workflow_page("machine_vision_page")
        if page is None:
            page = MachineVisionPage(
                context_provider=self._system_status_context,
                health_source=self._system_health_view,
                parent=self.tab_widget,
            )
            self.machine_vision_page = page
        self._show_workflow_page(page, 5)

    def show_data_analysis_page(self):
        page = self._cached_workflow_page("data_analysis_page")
        if page is None:
            page = DataAnalysisPage(
                context_provider=self._system_status_context,
                health_source=self._system_health_view,
                parent=self.tab_widget,
            )
            self.data_analysis_page = page
        self._show_workflow_page(page, 6)

    def navigate_workflow_step(self, step, allow_pre_run=False):
        # Any explicit navigation wins over the queued startup landing-page
        # callback.  This prevents P3 from briefly appearing and then being
        # replaced by P1 when the user clicks during startup.
        self._workflow_initial_route_pending = False
        self._hide_legacy_module_windows()
        if int(step) == 1:
            self.show_system_status_page()
            return
        if int(step) == 2:
            self.show_experiment_config_page()
            return
        if int(step) == 3:
            self.show_experiment_control_page()
            return
        if int(step) == 4:
            self.show_data_view_page()
            return
        if int(step) == 5:
            self.show_machine_vision_page()
            return
        if int(step) == 6:
            self.show_data_analysis_page()
            return
        fallback_modules = {
            2: "New_main_experiment_setting",
            3: "Main_New_Monitor_data",
            4: "Main_User_monitor_data",
            5: "Main_Video_image",
            6: "MainMouseTrajectoryAnalysis",
        }
        target_name = fallback_modules.get(int(step))
        target_module = next((item for item in self.modules if item.name == target_name), None)
        if target_module is None:
            QMessageBox.information(self, "功能迁移中", "该步骤的原有入口暂不可用。")
            return
        current_state = global_setting.get_setting("app_state", AppState.INITIALIZED)
        if (
            target_module.app_state is not None
            and target_module.app_state > current_state
            and not (int(step) == 3 and allow_pre_run)
        ):
            QMessageBox.information(
                self,
                "当前不可用",
                f"请先完成前置步骤后再进入“{self.workflow_shell.STEPS[int(step) - 1][1]}”。",
            )
            return
        self.workflow_shell.set_active_step(int(step))
        # The legacy module navigation destroys tab content.  Do not retain a
        # stale pointer to the P1 widget after it has been deleted.
        self.system_status_page = None
        target_module.click_method()

    def _on_deep_camera_instance_state_changed(self, state, previous_state, owner):
        if state == "blocked":
            message = "独立视频模块正在运行，上位机视频功能暂不可用"
            self.status_bar.update_tip(message)
            if self.start_dialog is not None:
                self.start_dialog.insert_data_signal.emit(
                    "独立视频模块占用相机，本次实验跳过上位机视频采集；其他功能继续启动"
                )
            if self.stop_dialog is not None:
                self.stop_dialog.insert_data_signal.emit(
                    "独立视频模块占用相机，上位机轨迹相机未启动，无需停止"
                )
        elif state == "ready" and previous_state == "blocked":
            self.status_bar.update_tip("独立视频模块已关闭，上位机视频功能已恢复")
            if self.start_dialog is not None:
                self.start_dialog.insert_data_signal.emit(
                    "独立视频模块已关闭，上位机视频采集正在自动恢复"
                )
    # 创建工具栏
    def create_tool_bar(self):
        # 创建 QToolBar
        self.toolbar = QToolBar("Toolbar")

        # 设置工具栏样式为图标在左，文字在右
        self.toolbar.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.toolbar.setIconSize(QtCore.QSize(48, 48))
        self.toolbar.setMinimumHeight(70)

        self.addToolBar(self.toolbar)

        # 初始化动态内容相关列表
        self.dynamic_tool_bar_actions = []
        self.dynamic_toolbar_separators = []
        self.current_active_menu_id = None

        # ==================== 创建通用功能按钮 ====================
        # 定义工具栏按钮数据
        static_buttons = [
            {
                "name": "窗口变换",
                "short_name": "变换",
                "obj_name": "window_exchange",
                "type": "button",
                "icon": "⇄",  # 或使用图标文件: ":/icons/exchange.png"
                "callback": self.exchange_widget_and_window,
                "app_state": AppState.INITIALIZED,
                "tip": "单击此按钮会将打开的窗口变成内嵌抽屉页。",
                "disabled": False,
                "separator_before": False,
                "separator_after": True
            },
            {
                "name": "更改主题颜色",
                "short_name": "主题",
                "obj_name": "toggle_mode",
                "type": "button",
                "icon": "🌓",
                "callback": self.toggle_theme,
                "app_state": AppState.INITIALIZED,
                "tip": "单击此按钮会将程序的主题颜色变换黑色和白色",
                "disabled": False,
                "separator_before": False,
                "separator_after": True
            },

            {
                "name": "开始实验",
                "short_name": "开始",
                "obj_name": "start_experiment",
                "type": "button",
                "icon": "▶",
                "callback": self.start_experiment,
                "app_state": AppState.CONFIGURING,
                "tip": "单击此按钮会将开始实验，但是必须等待配置完成才能单击该按钮。",
                "disabled": False,
                "separator_before": False,
                "separator_after": False
            },
            {
                "name": "开始实验时校准气体",
                "short_name": "校准",
                "obj_name": "calibration_gas",
                "type": "button",
                "icon": "📏",
                "callback": self.calibration_gas_state_change,
                "app_state": AppState.INITIALIZED,
                "tip": "单击此按钮会将选择开始实验时是否校准气体。",
                "disabled": True,
                "separator_before": False,
                "separator_after": False
            },
            {
                "name": "停止实验",
                "short_name": "停止",
                "obj_name": "stop_experiment",
                "type": "button",
                "icon": "⏹",
                "callback": self.stop_experiment,
                "app_state": AppState.CONFIGURING,
                "tip": "单击此按钮会将停止实验，并将实验数据保存。",
                "disabled": True,
                "separator_before": False,
                "separator_after": True
            },
            {
                "name": "导出实验数据",
                "short_name": "导出",
                "obj_name": "export_experiment_datas",
                "type": "button",
                "icon": "💾",
                "callback": self.export_experiment_datas,
                "app_state": AppState.MONITORING,
                "tip": "单击此按钮会将将实验数据保存。",
                "disabled": True,
                "separator_before": False,
                "separator_after": True
            },
            {
                "name": "重置教程页",
                "short_name": "重置教程",
                "obj_name": "reset_guidance",
                "type": "button",
                "icon": "🔄",
                "callback": self.reset_guidance,
                "app_state": AppState.INITIALIZED,
                "tip": "单击此按钮会将重置教程。",
                "disabled": True,
                "separator_before": False,
                "separator_after": True
            }
        ]

        # 创建按钮，图标在前，文字在后
        for i, button_config in enumerate(static_buttons):
            if button_config["separator_before"]:
                self.toolbar.addSeparator()
            if button_config["type"] == "button":
                action = QAction(button_config["short_name"], self)
                action.setObjectName(button_config["obj_name"])
                action.setToolTip(button_config["tip"])
                action.triggered.connect(button_config["callback"])
                # 设置图标
                if button_config["icon"]:
                    action.setText(button_config["icon"] + " " + button_config["short_name"])

                if button_config["disabled"]:
                    action.setDisabled(True)

                self.tool_bar_actions.append({
                    "text": button_config["name"],
                    "obj_name": button_config["obj_name"],
                    "action": action,
                    "app_state": button_config["app_state"],
                    "tip": button_config["tip"]
                })

                self.toolbar.addAction(action)
                # 记录静态按钮（用于插入动态按钮时定位）
                if i == 0:
                    self.static_toolbar_actions.append(action)
            elif button_config["type"] == "checkbox":
                if button_config["icon"]:
                    checkBox = QCheckBox(button_config["icon"] + " " + button_config["name"])
                else:
                    checkBox = QCheckBox(button_config["name"])
                checkBox.setChecked(True)
                checkBox.stateChanged.connect(button_config["callback"])
                checkBox.setObjectName(button_config["obj_name"])
                if button_config["disabled"]:
                    checkBox.setDisabled(True)
                self.tool_bar_actions.append({
                    "text": button_config["name"],
                    "obj_name": button_config["obj_name"],
                    "action": checkBox,
                    "app_state": button_config["app_state"],
                    "tip": button_config["tip"]
                })

                self.toolbar.addWidget(checkBox)
            if button_config["separator_after"]:
                self.toolbar.addSeparator()


            # # 在某些按钮后添加分隔符
            # if button_config["obj_name"] in ["window_exchange", "toggle_mode", "stop_experiment",
            #                                  "export_experiment_datas", "reset_guidance"]:
            #     self.toolbar.addSeparator()

        self.initialize_toolbar_visibility()

    def initialize_toolbar_visibility(self):
        """初始化工具栏可见性 - 根据当前菜单配置"""
        if self.menu_name and len(self.menu_name) > 0:
            first_menu = self.menu_name[0]
            hide_common_tools = first_menu.get('hide_common_tools', False)

            if hide_common_tools:
                self.hide_common_tools()
            else:
                self.show_common_tools()
        else:
            self.show_common_tools()
    # 创建文本图标的方法
    def create_text_icon(self, text, size=18):
        """创建文本图标"""
        from PyQt6.QtGui import QPixmap, QPainter, QFont, QIcon, QColor
        from PyQt6.QtCore import Qt

        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)

        # 设置字体
        font = QFont()
        font.setPixelSize(size - 2)  # emoji 稍小一点
        painter.setFont(font)

        # 设置文字颜色
        painter.setPen(QColor("#333333"))

        # 绘制文本
        painter.drawText(pixmap.rect(), Qt.AlignmentFlag.AlignCenter, text)
        painter.end()

        return QIcon(pixmap)

    def create_menu_bar(self):
        """创建菜单栏 - 触发工具栏切换"""
        for menu_dict in self.menu_name:
            # 创建菜单动作
            action = QAction(menu_dict['text'], self)
            action.setObjectName(f"menu_{menu_dict['id']}")
            action.setToolTip(menu_dict.get('tip', ""))

            # 连接到工具栏切换方法
            action.triggered.connect(lambda checked, menu_id=menu_dict['id']: self.switch_toolbar_content(menu_id))

            # 添加到菜单栏
            self.menuBar().addAction(action)

            # 从菜单配置中获取 required_app_state，转换为枚举
            required_state_str = menu_dict.get('required_app_state', 'INITIALIZED')
            try:
                required_app_state = AppState[required_state_str]
            except KeyError:
                required_app_state = AppState.INITIALIZED

            # 存储菜单动作信息
            self.menu_bar_actions.append({
                "name": menu_dict['text'],
                "obj_name": f"menu_{menu_dict['id']}",
                "action": action,
                "menu_id": menu_dict['id'],
                "app_state": required_app_state  # 使用正确的状态
            })

    def switch_toolbar_content(self, menu_id):
        """根据菜单ID切换工具栏内容"""
        # 清除当前工具栏的动态内容
        self.clear_dynamic_toolbar_content()

        # 记录当前激活的菜单
        self.current_active_menu_id = menu_id
        self.sync_workflow_step_for_legacy_menu(menu_id)

        # 为所有模块设置 main_gui
        for module in self.modules:
            if module.main_gui is None:
                module.set_main_gui(main_gui=self)
        # 找到对应的菜单配置
        current_menu_config = None
        for menu_dict in self.menu_name:
            if menu_dict.get('id') == menu_id:
                current_menu_config = menu_dict
                break

        # 检查是否需要隐藏通用工具
        hide_common_tools = current_menu_config.get('hide_common_tools', False) if current_menu_config else False

        # 控制通用工具的显示/隐藏
        if hide_common_tools:
            self.hide_common_tools()
        else:
            self.show_common_tools()

        if menu_id == 1:
            global_setting.set_setting("device_config_calibration_selected", False)
            global_setting.set_setting("startup_calibration_mode", "none")
            global_setting.set_setting("is_auto_calibration", False)
            global_setting.set_setting(
                "air_modules_all_valid",
                bool(global_setting.get_setting("environment_module_only", False)),
            )
            self.calibration_selection_changed_signal.emit(
                False,
                global_setting.get_setting("startup_calibration_mode", "none")
            )

        # 找到属于这个菜单的所有模块
        menu_modules = []
        for module in self.modules:
            module: BaseModule
            module_menu_name = module.menu_name
            if (module_menu_name is not None and
                    "id" in module_menu_name and
                    module_menu_name["id"] == menu_id):
                menu_modules.append(module)

        # 按模块顺序和标题排序
        menu_modules.sort(key=lambda x: (getattr(x, "toolbar_order", 999), x.title))

        # 确定插入位置
        insert_position = self.get_dynamic_content_insert_position(hide_common_tools)

        # 为每个模块创建工具栏按钮
        for module in menu_modules:
            module.set_main_gui(main_gui=self)
            if hasattr(module, "refresh_display_text"):
                module.refresh_display_text()
            name = module.title
            obj_name = f"dynamic_{module.name}"

            # 图标映射表
            MODULE_ICON_MAP = {
                "新建实验": "🧪",
                "打开实验文件": "📂",
                "设置设备": "🔧",
                "校准": "🎯",
                "设备配置": "🖥️",
                "老鼠轨迹监测": "🐭",
                "相机监控": "📷",
                "红外相机": "📷",
                "视频图像": "📹",
                "轨迹相机对应鼠笼配置": "🧭",
                "红外相机对应鼠笼配置": "🌡",
                "硬件配置": "🗂️",
                "光照时间表": "💡",
                "用户界面": "👤",
                "数据监控": "📊",
                "运动轨迹分析": "📈",
                "行为规律": "📈",
                "数据对比": "📊",
                "串口调试":"🔌",
                "坐标标定":"📍"
            }

            # 根据模块名匹配图标
            icon_char = None
            for key, icon in MODULE_ICON_MAP.items():
                if key in name:
                    icon_char = icon
                    break

            # 用 QToolButton 替代 QAction
            from PyQt6.QtWidgets import QToolButton
            btn = QToolButton()
            btn.setObjectName(obj_name)
            btn.setToolTip(name)
            btn.clicked.connect(module.click_method)
            if obj_name == "dynamic_New_main_experiment_calibration":
                btn.setEnabled(
                    not global_setting.get_setting("environment_module_only", False)
                    and (
                        bool(global_setting.get_setting("air_modules_all_valid", False))
                        or bool(global_setting.get_setting("allow_test_calibration_without_air_validation", False))
                    )
                )

            # # 文字竖排
            # btn.setText("\n".join(name))
            btn.setText(name)

            # 图标在上，文字在下
            btn.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)

            # 图标：优先模块自带，其次映射表，最后首字
            if hasattr(module, 'icon') and module.icon:
                from PyQt6.QtGui import QIcon
                btn.setIcon(QIcon(module.icon))
            elif icon_char:
                btn.setIcon(self.create_text_icon(icon_char, size=28))
            else:
                btn.setIcon(self.create_text_icon(name[0] if name else "●", size=28))
            btn.setIconSize(QtCore.QSize(28, 28))

            # 字体
            font = QFont()
            font.setPixelSize(12)
            btn.setFont(font)

            # 固定宽度
            btn.setFixedWidth(190 if len(name) >= 8 else 150)

            # 样式
            btn.setStyleSheet("""
                            QToolButton {
                                border: none;
                                padding: 0px 2px;
                                background: transparent;
                            }
                            QToolButton:hover {
                                background: rgba(128,128,128,0.15);
                                border-radius: 6px;
                            }
                            QToolButton:pressed {
                                background: rgba(128,128,128,0.3);
                                border-radius: 6px;
                            }
                        """)

            self.dynamic_tool_bar_actions.append({
                "name": name,
                "obj_name": obj_name,
                "action": btn,  # 存btn
                "app_state": module.app_state,
                "menu_id": menu_id
            })

            # 用insertWidget/addWidget
            if insert_position:
                self.toolbar.insertWidget(insert_position, btn)
            else:
                self.toolbar.addWidget(btn)

        # 添加分隔符（只有在显示通用工具且有动态内容时才添加）
        if menu_modules and not hide_common_tools and self.static_toolbar_actions:
            separator = self.toolbar.insertSeparator(self.static_toolbar_actions[0])
            self.dynamic_toolbar_separators.append(separator)

        # 更新菜单栏按钮的激活状态
        self.update_menu_bar_active_state(menu_id)
        self._sync_toolbar_container_visibility()
        self.change_enable_component_app_state()

    def clear_dynamic_toolbar_content(self):
        """清除工具栏中的动态内容"""
        from PyQt6.QtWidgets import QToolButton
        for action_dict in self.dynamic_tool_bar_actions:
            widget_or_action = action_dict["action"]
            if isinstance(widget_or_action, QAction):
                self.toolbar.removeAction(widget_or_action)
            else:
                # QToolButton 直接隐藏并销毁
                widget_or_action.hide()
                widget_or_action.deleteLater()

        for separator in self.dynamic_toolbar_separators:
            self.toolbar.removeAction(separator)

        self.dynamic_tool_bar_actions.clear()
        self.dynamic_toolbar_separators.clear()
        self.show_common_tools()

    def hide_common_tools(self):
        """隐藏通用工具按钮"""
        # 隐藏所有工具栏中的 action
        for action in self.toolbar.actions():
            if not action.isSeparator():
                action.setVisible(False)
            elif action not in self.dynamic_toolbar_separators:
                # 隐藏非动态分隔符
                action.setVisible(False)

        # 同时隐藏通过 addWidget 添加的 checkbox 等 widget
        for i in range(self.toolbar.layout().count()):
            widget = self.toolbar.layout().itemAt(i).widget()
            if widget is not None and isinstance(widget, QCheckBox):
                widget.setVisible(False)
        self._sync_toolbar_container_visibility()


    def show_common_tools(self):
        """显示通用工具按钮"""
        # 显示所有 action
        for action in self.toolbar.actions():
            if not action.isSeparator():
                action.setVisible(True)
            elif action not in self.dynamic_toolbar_separators:
                action.setVisible(True)

        # 显示通过 addWidget 添加的 checkbox 等 widget
        for i in range(self.toolbar.layout().count()):
            widget = self.toolbar.layout().itemAt(i).widget()
            if widget is not None and isinstance(widget, QCheckBox):
                widget.setVisible(True)
        self._sync_toolbar_container_visibility()


    def _sync_toolbar_container_visibility(self):
        """Collapse the legacy toolbar when all of its controls are hidden."""

        has_visible_action = False
        for action in self.toolbar.actions():
            if action.isSeparator():
                continue
            # QWidgetAction children report ``isVisible() == False`` while
            # their parent toolbar is hidden.  The action visibility is the
            # reliable signal for deciding whether the toolbar should reopen.
            if action.isVisible():
                has_visible_action = True
                break
        self.toolbar.setVisible(has_visible_action)


    def get_dynamic_content_insert_position(self, hide_common_tools):
        """获取动态内容插入位置"""
        if hide_common_tools:
            # 如果隐藏通用工具，插入到工具栏开始位置（第一个动作）
            actions = self.toolbar.actions()
            return actions[0] if actions else None
        else:
            # 如果显示通用工具，插入到第一个静态按钮之前
            if self.static_toolbar_actions:
                return self.static_toolbar_actions[0]
            return None

    def update_menu_bar_active_state(self, active_menu_id):
        """更新菜单栏按钮的激活状态"""
        for action_dict in self.menu_bar_actions:
            action = action_dict["action"]
            menu_id = action_dict.get("menu_id")

            if menu_id == active_menu_id:
                # 设置为激活状态（可以通过样式表来显示不同的外观）
                action.setCheckable(True)
                action.setChecked(True)
            else:
                action.setCheckable(True)
                action.setChecked(False)

    def _retranslateUi(self, **kwargs):
        _translate = QtCore.QCoreApplication.translate
        self.setWindowTitle(_translate(self.objectName(), global_setting.get_setting("configer")["window"]["title"]))
    pass
    def load_modules(self):
        # 动态加载模块
        load_started = time.perf_counter()
        loaded_file_count = 0
        modules = []
        module_dir = 'Module'  # 插件目录
        # 递归遍历指定目录
        for dirpath, dirnames, filenames in os.walk(module_dir):
            for filename in filenames:
                if filename.endswith('.py'):
                    module_name = filename[:-3]  # 去掉 .py 后缀
                    if module_name.startswith("main"):
                        file_path = os.path.join(dirpath, filename)
                        module_started = time.perf_counter()
                        # 动态加载模块
                        spec = importlib.util.spec_from_file_location(module_name, file_path)
                        module = importlib.util.module_from_spec(spec)
                        spec.loader.exec_module(module)  # 装载module
                        loaded_file_count += 1
                        # 查找到实现 BasePlugin 的类
                        for name, obj in module.__dict__.items():
                            if name == "BaseModule":
                                # 抽象类跳过
                                continue
                            if (
                                isinstance(obj, type)
                                and issubclass(obj, BaseModule)
                                and not obj.__dict__.get("__module_loader_skip__", False)
                            ):
                                modules.append(obj())
                        module_elapsed = time.perf_counter() - module_started
                        if module_elapsed >= 0.5:
                            logger.warning(
                                f"slow startup module: path={file_path}, "
                                f"elapsed={module_elapsed:.2f}s"
                            )
        logger.info(
            f"startup modules loaded: files={loaded_file_count}, "
            f"instances={len(modules)}, "
            f"elapsed={time.perf_counter() - load_started:.2f}s"
        )
        return modules
        pass

    def exchange_widget_and_window(self):
        """widget和window相互转换"""
        # 将module的显示方式改变
        for module in self.modules:
            if module.interface_widget.type == BaseInterfaceType.WIDGET or module.interface_widget.type == BaseInterfaceType.FRAME:
                module.interface_widget.type = BaseInterfaceType.WINDOW

            else:
                module.interface_widget.type = BaseInterfaceType.WIDGET
        new_open_windows = []
        new_active_module_widgets = []
        # 将正在显示的方式进行改变
        if self.open_windows is not None and len(self.open_windows) != 0:
            # 窗口-》frame
            # 将正在显示的方式进行改变
            index = 0
            last_module = None
            while index < len(self.open_windows) or len(self.open_windows) == 1:
                if index >= len(self.open_windows):
                    index = 0
                module = self.open_windows[index]
                if last_module is module:
                    break
                last_module = module
                module.close()
                if module not in self.active_module_widgets:
                    new_active_module_widgets.append(module)
                index += 1
        if self.active_module_widgets is not None and len(self.active_module_widgets) != 0:
            # 从初始布局中移除 label
            # frame-》窗口
            index = 0
            last_module = None
            while index < len(self.active_module_widgets) or len(self.active_module_widgets) == 1:
                if index >= len(self.active_module_widgets):
                    index = 0
                module = self.active_module_widgets[index]
                if last_module is module:
                    break
                last_module = module
                module.setParent(None)
                module.hide()
                if module not in self.open_windows:
                    new_open_windows.append(module)
                index += 1
        # 删除所有标签页和widgets
        while self.tab_widget.count() > 0:  # 直到没有标签页
            self.tab_widget.removeTab(0)  # 删除第一个标签页
        self.open_windows.extend(new_open_windows)
        self.active_module_widgets.extend(new_active_module_widgets)
        for module in self.open_windows:
            module.adjustGUIPolicy()
            module.interface_widget.setMinimumSize(0, 0)
            module.interface_widget.show()
        for module in self.active_module_widgets:
            module.adjustGUIPolicy()
            module.interface_widget.setMinimumSize()
            module.interface_widget.show()

    # 切换白天黑夜主题功能
    def toggle_theme(self):
        # 根据当前主题变换主题
        new_theme = "dark" if global_setting.get_setting("theme_manager").current_theme == "light" else "light"
        # 将新主题关键字赋值回去
        global_setting.set_setting('style', new_theme)
        global_setting.get_setting("theme_manager").current_theme = new_theme
        # 更改样式
        self.setStyleSheet(global_setting.get_setting("theme_manager").get_style_sheet())
        pass

    def start_update_gui(self, resolve, reject):
        # 更新main_gui组件显示
        self.change_enable_component_app_state_signal.emit()
        self.status_bar.update_status()
        self.status_bar.update_tip("启动请求已发送，等待后台确认...")
        page = getattr(self, "experiment_control_page", None)
        if page is not None:
            page.set_operation_progress(0, "启动请求已发送，等待后台确认")
            page.operation_hint.setText("已向采集服务发送启动请求，正在等待启动完成通知。")
            page.refresh()
        for action_dict in self.tool_bar_actions:
            if action_dict["obj_name"] == "start_experiment":
                action_dict["action"]: QAction
                action_dict["action"].setDisabled(True)
            if action_dict["obj_name"] == "stop_experiment":
                action_dict["action"]: QAction
                action_dict["action"].setDisabled(False)
            if action_dict["obj_name"] == "pause_experiment":
                action_dict["action"]: QAction
                action_dict["action"].setDisabled(False)
        self.setEnabled(True)
        resolve()

    def _get_experiment_root_path(self):
        experiment_root_path = global_setting.get_setting("experiment_root_path", None)
        if experiment_root_path:
            return os.path.abspath(experiment_root_path)

        experiment_setting_file = global_setting.get_setting("experiment_setting_file", None)
        start_time = global_setting.get_setting("start_experiment_time", None)
        try:
            start_time = float(start_time)
        except (TypeError, ValueError):
            return None
        if start_time <= 0:
            return None

        storage = global_setting.get_setting('monitor_data')['STORAGE']
        experiment_setting = (
            global_setting.get_setting("experiment_setting", None)
            or global_setting.get_setting("experiment_setting_new", None)
        )
        selected_storage_path = str(getattr(experiment_setting, "storage_path", "") or "").strip()
        project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
        storage_root = (
            os.path.abspath(os.path.expanduser(selected_storage_path))
            if selected_storage_path
            else os.path.abspath(os.path.join(
                project_root,
                storage['fold_path'],
                str(storage['sub_fold_path']).strip('/\\'),
            ))
        )
        return os.path.abspath(os.path.join(
            storage_root,
            main_monitor_data.build_monitor_data_experiment_folder_name(
                experiment_setting_file,
                start_time,
            )
        ))

    def _get_experiment_db_path(self):
        experiment_root_path = self._get_experiment_root_path()
        if experiment_root_path is None:
            return None
        return os.path.join(experiment_root_path, "data", "data.db")

    def _get_experiment_excel_path(self):
        experiment_root_path = self._get_experiment_root_path()
        if experiment_root_path is None:
            return None
        return f"{experiment_root_path}.xlsx"

    def _get_periodic_export_interval_ms(self):
        export_config = global_setting.get_setting("monitor_data", {}).get("EXPORT", {})
        interval_minutes_raw = export_config.get("periodic_xlsx_interval_minutes", 30)
        try:
            interval_minutes = float(interval_minutes_raw)
        except (TypeError, ValueError):
            logger.warning(f"定时导出xlsx配置无效，使用默认值30分钟: {interval_minutes_raw}")
            interval_minutes = 30.0

        if interval_minutes <= 0:
            return 0
        return int(interval_minutes * 60 * 1000)

    def _start_periodic_export_timer(self):
        interval_ms = self._get_periodic_export_interval_ms()
        if self.periodic_export_timer.isActive():
            self.periodic_export_timer.stop()
        if interval_ms <= 0:
            logger.info("定时导出xlsx已关闭：EXPORT.periodic_xlsx_interval_minutes <= 0")
            return
        self.periodic_export_timer.setInterval(interval_ms)
        self.periodic_export_timer.start()
        logger.info(f"定时导出xlsx已启动，周期: {interval_ms / 60000:.2f} 分钟")

    def _stop_periodic_export_timer(self):
        if self.periodic_export_timer.isActive():
            self.periodic_export_timer.stop()
            logger.info("定时导出xlsx已停止")

    def _is_periodic_export_running(self):
        with self.periodic_export_lock:
            return self.periodic_export_in_progress

    def _on_periodic_export_finished(self, success, message):
        with self.periodic_export_lock:
            self.periodic_export_in_progress = False
            self.periodic_export_thread = None

        if getattr(self, "_shutdown_prepared", False):
            return

        if success:
            logger.info(message)
        else:
            logger.error(message)
            self.update_status_tip_signal.emit(message)

    def trigger_periodic_excel_export(self):
        if global_setting.get_setting("app_state", AppState.INITIALIZED) != AppState.MONITORING:
            return

        db_path = self._get_experiment_db_path()
        export_file_path = self._get_experiment_excel_path()
        if db_path is None or export_file_path is None:
            logger.warning("定时导出xlsx跳过：实验路径尚未准备好")
            return
        if not os.path.exists(db_path):
            logger.warning(f"定时导出xlsx跳过：数据库不存在 {db_path}")
            return

        with self.periodic_export_lock:
            if self.periodic_export_in_progress:
                logger.warning("上一次定时导出xlsx尚未完成，跳过本轮导出")
                return
            self.periodic_export_in_progress = True

        logger.info(f"开始定时导出xlsx: {export_file_path}")
        self.periodic_export_thread = PeriodicExcelExportThread(
            db_path=db_path,
            export_file_path=export_file_path,
            callback=self._on_periodic_export_finished
        )
        self.periodic_export_thread.start()

    @staticmethod
    def _experiment_feature_enabled(feature_key: str) -> bool:
        """Return whether any selected cage enables a runtime feature.

        Missing per-cage module data is treated as enabled for backwards
        compatibility with old templates that predate the module switches.
        """
        setting = global_setting.get_setting("experiment_setting", None)
        groups = [group for group in (getattr(setting, "groups", []) or [])
                  if bool(getattr(group, "is_selected", False))]
        if not groups:
            return False
        modules = getattr(setting, "channel_modules", {}) or {}
        for group in groups:
            key = str(getattr(group, "id", ""))
            values = modules.get(key, {}) if isinstance(modules, dict) else {}
            if not values and isinstance(modules, dict):
                try:
                    values = modules.get(int(getattr(group, "id", 0) or 0), {})
                except (TypeError, ValueError):
                    values = {}
            if not values:
                try:
                    values = modules.get(str(int(getattr(group, "id", 0) or 0)), {})
                except (TypeError, ValueError):
                    values = {}
            if not isinstance(values, dict) or bool(values.get(feature_key, True)):
                return True
        return False

    def _confirm_workflow_device_config(self, port, queue):
        """Mirror the legacy confirmation messages before the start command."""
        mode = global_setting.get_setting("startup_calibration_mode", None)
        if mode == "air_co2":
            mode = "air"
        if mode not in {"none", "air", "zero", "span", "full", "o2_span_air", "co2_o2_span_air"}:
            mode = "none"
        if global_setting.get_setting("environment_module_only", False):
            mode = "none"
        config = {"startup_calibration_mode": mode, "is_auto_calibration": mode != "none"}
        for key in ("Vr", "span_standard_oxygen_value", "span_standard_carbon_value"):
            value = global_setting.get_setting(key, None)
            if value is not None:
                config["vr_desc" if key == "Vr" else key] = float(value)
        global_setting.set_setting("startup_calibration_mode", mode)
        global_setting.set_setting("is_auto_calibration", mode != "none")
        now = time_util.get_format_from_time(time.time())
        queue.put(ObjectQueueItem(origin="MainWindow_Index", to="main_monitor_data",
                                  title="set_port", data=port, time=now))
        queue.put(ObjectQueueItem(origin="MainWindow_Index", to="main_monitor_data",
                                  title="set_experiment_basic_config", data=config, time=now))

    def _resolve_startup_calibration_mode_for_start(self):
        """Preserve the calibration mode selected after the device check."""
        mode = global_setting.get_setting("startup_calibration_mode", "none")
        if mode == "air_co2":
            mode = "air"
        if mode not in {"none", "air", "zero", "span", "full", "o2_span_air", "co2_o2_span_air"}:
            mode = "none"
        if global_setting.get_setting("environment_module_only", False):
            mode = "none"
        global_setting.set_setting("startup_calibration_mode", mode)
        global_setting.set_setting("is_auto_calibration", mode != "none")
        if mode == "none":
            global_setting.set_setting("device_config_calibration_selected", False)
        return mode

    def start_experiment(self):

        # ★ 重置上一次实验残留的临时提示状态，确保从绿色开始
        MainWindow_Index._reset_workflow_runtime_log(self)
        MainWindow_Index._append_workflow_runtime_log(
            self, "正在检查实验配置和后台连接...", "启动"
        )
        self.status_bar._temp_tip_active = False
        self.old_Stop_experiment_status_text_reTurn = None
        self.old_stop_status_counts = 0
        self._gas_path_success = False
        if self._gas_path_timeout_timer is not None:
            self._gas_path_timeout_timer.stop()
            self._gas_path_timeout_timer = None

        port = global_setting.get_setting("port")
        if port is None or port == "":
            self._reject_experiment_start("未配置串口，请先在实验配置中选择串口。")
            return
        setting = global_setting.get_setting("experiment_setting", None)
        if (global_setting.get_setting("confirmed_serial_setting", None) is not setting
                or global_setting.get_setting("confirmed_serial_port", None) != port):
            self._reject_experiment_start("当前实验尚未确认串口，请先在实验配置中确认并检测。")
            return
        if not any(bool(getattr(group, "is_selected", False)) for group in (getattr(setting, "groups", []) or [])):
            self._reject_experiment_start("未选择实验笼位，请先在实验配置中选择并应用笼位。")
            return
        send_message_queue = global_setting.get_setting("send_message_queue", None)
        if send_message_queue is None:
            self._reject_experiment_start("后台采集服务未连接，请检查服务状态后重试。")
            return
        startup_calibration_mode = self._resolve_startup_calibration_mode_for_start()
        self._reset_calibration_detail_state(startup_calibration_mode)
        behavior_enabled = self._experiment_feature_enabled("behavior")
        infrared_enabled = self._experiment_feature_enabled("infrared")
        camera_queue = global_setting.get_setting("queue", None)
        if not global_setting.get_setting("environment_module_only", False) and camera_queue is None:
            self._reject_experiment_start("相机控制服务未连接，请检查服务状态后重试。")
            return

        try:
            self._confirm_workflow_device_config(port, send_message_queue)
        except Exception as error:
            self._reject_experiment_start(f"确认设备配置未发送成功：{error}")
            return

        self.setEnabled(False)
        self._set_workflow_run_state(ExperimentRunState.STARTING)
        self.status_bar.update_tip("正在开启实验监测...")
        try:
            modbus: ModbusRTUMasterNew = global_setting.get_setting("modbus", None)
            if modbus is None:
                modbus = ModbusRTUMasterNew(port, baudrate=115200, timeout=float(
                    global_setting.get_setting('monitor_data')['Serial']['timeout']), )
                global_setting.set_setting("modbus", modbus)
            else:
                modbus.close()
                modbus = ModbusRTUMasterNew(port, baudrate=115200, timeout=float(
                    global_setting.get_setting('monitor_data')['Serial']['timeout']), )
                global_setting.set_setting("modbus", modbus)
        except Exception as error:
            self._mark_workflow_operation_failed("start", error)
            return
        # 开始实验
        global_setting.set_setting("app_state", AppState.MONITORING)
        # The legacy app state must switch now to unlock its existing control
        # paths, but the new header stays blue until startup confirmation.
        self._set_workflow_run_state(ExperimentRunState.STARTING)
        global_setting.set_setting("start_experiment_time", time.time())
        global_setting.set_setting("experiment_root_path", None)
        experiment_root_path = self._get_experiment_root_path()
        global_setting.set_setting("experiment_root_path", experiment_root_path)
        global_setting.set_setting("pause_experiment_time", [])
        global_setting.set_setting("relieve_pause_experiment_time", [])
        self._start_periodic_export_timer()
        # self.start_thread = Start_experiment_thread(name="start_thread",window=self)
        # self.start_thread.start()

        try:
            send_message_queue.put(ObjectQueueItem(origin='MainWindow_Index', to='main_monitor_data', title='start',
                                               data={
                                                   'start_experiment_time': global_setting.get_setting(
                                                       "start_experiment_time"),
                                                   'experiment_root_path': experiment_root_path,
                                                   'pause_experiment_time': global_setting.get_setting(
                                                       "pause_experiment_time"),
                                                   'relieve_pause_experiment_time': global_setting.get_setting(
                                                       "relieve_pause_experiment_time")
                                               },
                                                time=time_util.get_format_from_time(time.time())))
        except Exception as error:
            self._mark_workflow_operation_failed("start", error)
            return
        message_structs = []
        if not global_setting.get_setting("environment_module_only", False):
            camera_data = {
                'start_experiment_time': global_setting.get_setting("start_experiment_time"),
                'pause_experiment_time': global_setting.get_setting("pause_experiment_time"),
                'relieve_pause_experiment_time': global_setting.get_setting("relieve_pause_experiment_time"),
            }
            # Match the baseline: both camera services receive a start command.
            # The feature flags still control optional recording and history.
            message_structs.append(ObjectQueueItem(
                origin='MainWindow_Index', to='main_infrared_camera', title='start',
                data={**camera_data, 'infrared_enabled': infrared_enabled},
                time=time_util.get_format_from_time(time.time()),
            ))
            message_structs.append(ObjectQueueItem(
                origin='MainWindow_Index', to='main_deep_camera', title='start',
                data={**camera_data, 'behavior_enabled': behavior_enabled},
                time=time_util.get_format_from_time(time.time()),
            ))
        for message_struct in message_structs:
            try:
                camera_queue.put(message_struct)
            except Exception as error:
                self._mark_workflow_operation_failed("start", error)
                return
        AsyPromise(self.start_update_gui).then(
            lambda _: AsyPromise(self.show_open_dialog).then(
                lambda _: AsyPromise(self.start_open_window).then(

                ).catch(lambda e: self._mark_workflow_operation_failed("start", e))
            ).catch(lambda e: self._mark_workflow_operation_failed("start", e))
        ).catch(lambda e: self._mark_workflow_operation_failed("start", e))
        # AsyPromise(self.start_open_window).then().catch(lambda e: logger.error(f"{e}"))
        pass

    def show_open_dialog(self, resolve, reject):
        environment_only = bool(
            global_setting.get_setting("environment_module_only", False)
        )
        start_wait_times = float(global_setting.get_setting('configer')['dialog_timeout']['timeout']) + float(
            global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['wait_time']) + float(
            global_setting.get_setting('UFC_UGC_ZOS_config')['ZOS']['start_read_pressure_all_time']) / float(
            global_setting.get_setting('UFC_UGC_ZOS_config')['ZOS']['start_read_pressure_delay']) * 2 * 8 + 20
        if not environment_only:
            try:
                flow_delay = float(
                    global_setting.get_setting('UFC_UGC_ZOS_config')['UFC'].get('flow_setpoint_delay', 2)
                )
                if not math.isfinite(flow_delay) or flow_delay < 0:
                    raise ValueError("invalid flow setpoint delay")
            except (KeyError, TypeError, ValueError):
                flow_delay = 2
            start_wait_times += len(UFC_FLOW_SETPOINT_DEFAULTS) * (flow_delay + 1)
        start_message = "正在启动环境模块..." if environment_only else "正在启动气路..."

        if self.start_dialog is None:
            self.start_dialog = AnimatedLoadingDialog(countdown_seconds=start_wait_times, title="开始实验",
                                                      message=start_message)
        else:
            self.start_dialog.reset_progress()
            self.start_dialog.clear_list_data()
            self.start_dialog.deleteLater()
            self.start_dialog = None
            self.start_dialog = AnimatedLoadingDialog(
                countdown_seconds=start_wait_times,
                title="开始实验", message=start_message)

        self._attach_legacy_dialog_runtime_log(self.start_dialog, "启动")
        self._append_workflow_runtime_log(start_message, "启动")

        if global_setting.get_setting("deep_camera_instance_state", "starting") == "blocked":
            self.start_dialog.insert_data_signal.emit(
                "独立视频模块占用相机，本次实验跳过上位机视频采集；其他功能继续启动"
            )

        startup_calibration_mode = global_setting.get_setting('startup_calibration_mode', None)
        if environment_only:
            startup_calibration_mode = "none"
        if startup_calibration_mode == "air_co2":
            startup_calibration_mode = "air"
        if startup_calibration_mode not in {"none", "air", "zero", "span", "full", "o2_span_air", "co2_o2_span_air"}:
            startup_calibration_mode = "none"

        calibration_config = global_setting.get_setting('UFC_UGC_ZOS_config', {}).get('Calibration', {})
        if startup_calibration_mode == "air":
            try:
                start_wait_times += max(float(calibration_config.get('startup_air_calibration_wait_time', 1800)), 0)
            except Exception:
                start_wait_times += 1800
            try:
                start_wait_times += max(float(calibration_config.get('startup_air_calibration_max_timeout', 900)), 0)
            except Exception:
                start_wait_times += 900
            if self.start_dialog is not None:
                self.start_dialog.countdown_seconds = start_wait_times
                self.start_dialog.current_seconds = start_wait_times
        elif startup_calibration_mode in {"zero", "span"}:
            try:
                calibration_timeout = max(float(calibration_config.get('calibration_max_timeout', 300)), 0)
            except (TypeError, ValueError):
                calibration_timeout = 300
            start_wait_times += calibration_timeout
            self.start_dialog.countdown_seconds = start_wait_times
            self.start_dialog.current_seconds = start_wait_times
        elif startup_calibration_mode in {"o2_span_air", "co2_o2_span_air"}:
            try:
                target_points = max(
                    int(float(calibration_config.get("o2_span_air_target_points", 25))), 5
                )
            except (TypeError, ValueError):
                target_points = 25
            try:
                sample_interval = max(
                    float(calibration_config.get("startup_air_calibration_channel_interval", 2)), 0.5
                )
            except (TypeError, ValueError):
                sample_interval = 2.0
            # Two five-minute gas flushes plus up to nine channels × target points.
            calibration_wait = 2 * (300 + target_points * sample_interval * 9)
            if startup_calibration_mode == "co2_o2_span_air":
                try:
                    calibration_wait += max(
                        float(calibration_config.get("calibration_max_timeout", 300)), 300
                    ) + 120
                except (TypeError, ValueError):
                    calibration_wait += 420
            start_wait_times += calibration_wait
            self.start_dialog.countdown_seconds = start_wait_times
            self.start_dialog.current_seconds = start_wait_times
        elif startup_calibration_mode == "full":
            try:
                # Zero and span each have their own bounded calibration window.
                calibration_timeout = max(float(calibration_config.get('calibration_max_timeout', 300)), 0)
            except (TypeError, ValueError):
                calibration_timeout = 300
            start_wait_times += 2 * calibration_timeout
            self.start_dialog.countdown_seconds = start_wait_times
            self.start_dialog.current_seconds = start_wait_times

        if startup_calibration_mode in {"air", "zero", "span", "full", "o2_span_air", "co2_o2_span_air"}:
            self.init__calibration_windows()
            self.start_dialog.insert_calibration_dialog(self.calibration_details_windows)

        self.start_dialog.timeout_signal.connect(self._on_start_experiment_timeout)
        if environment_only:
            self.start_dialog.insert_data_signal.emit(
                "仅环境模块模式：气路、相机、饮水、食物和称重模块已跳过"
            )
        # Startup progress is rendered in the workflow control page.  Keep the
        # legacy dialog object only as an internal message sink; never open a
        # modal window over the page.
        self.start_dialog.hide()
        self.release_calibration_windows()
        if self.workflow_run_state == ExperimentRunState.STARTING:
            self._stop_startup_progress()
            self._startup_progress_started_at = time.monotonic()
            self._startup_progress_duration = max(float(start_wait_times), 1)
            self._startup_progress_timer = QTimer(self)
            self._startup_progress_timer.timeout.connect(self._advance_startup_progress)
            self._startup_progress_timer.start(100)
            if self._gas_path_timeout_timer is not None:
                self._gas_path_timeout_timer.stop()
            self._gas_path_timeout_timer = QTimer(self)
            self._gas_path_timeout_timer.setSingleShot(True)
            self._gas_path_timeout_timer.timeout.connect(self._on_gas_path_final_timeout)
            self._gas_path_timeout_timer.start(max(int(start_wait_times * 1000), 1000))
        resolve()

    def _on_start_experiment_timeout(self):
        """dialog内部倒计时结束触发（已由force_entered接管，此处留空）"""
        pass

    def _on_gas_path_final_timeout(self):
        """A missing completion receipt is an unknown backend state, not a device fault."""
        if not self._gas_path_success and self.workflow_run_state == ExperimentRunState.STARTING:
            self._mark_workflow_operation_failed("start", "启动等待超时，后台状态未确认；请检查运行日志或执行停止。")

    def _on_gas_path_start_success(self):
        """在GUI线程确认气路启动完成并取消备用失败计时器。"""
        if self.workflow_run_state != ExperimentRunState.STARTING:
            return
        self._stop_startup_progress()
        self._gas_path_success = True
        self._set_workflow_run_state(ExperimentRunState.RUNNING)
        if self._gas_path_timeout_timer is not None:
            self._gas_path_timeout_timer.stop()
            self._gas_path_timeout_timer.deleteLater()
            self._gas_path_timeout_timer = None
        if self.start_dialog is not None:
            self.start_dialog.update_progress_value(self.start_dialog.progress_max)
        success_message = (
            "环境模块采集已启动！"
            if global_setting.get_setting("environment_module_only", False)
            else "气路启动成功！"
        )
        self._append_workflow_runtime_log(success_message, "启动")
        page = getattr(self, "experiment_control_page", None)
        if page is not None:
            page.set_operation_progress(100, "实验已启动")
            page.operation_hint.setText(success_message)
        self.show_temp_status_tip_signal.emit(success_message, "#00aa00", 3000)
        QTimer.singleShot(3100, self.status_bar.update_status)

    def _reset_calibration_detail_state(self, mode="none"):
        """Start a new experiment with a clean, route-specific calibration view."""
        self.release_calibration_windows()
        self.calibration_detail_log_buffer.clear()
        self.calibration_detail_status_text = None
        self._active_calibration_channels.clear()
        self.calibration_detail_zero_start_time = None
        self.calibration_detail_zero_end_time = None
        self.calibration_detail_span_start_time = None
        self.calibration_detail_span_end_time = None
        self._experiment_calibration_mode = mode if mode in {"air", "zero", "span", "full"} else "none"

    def _is_uncalibrated_stop_housekeeping(self, message):
        """Return True for legacy zero/span shutdown chatter on a direct run."""
        if self.workflow_run_state != ExperimentRunState.STOPPING:
            return False
        if getattr(self, "_experiment_calibration_mode", "none") != "none":
            return False
        text = str(message or "").lower()
        return any(token in text for token in (
            "zero_carlibration_thread",
            "range_carlibration_thread",
            "停止零点量程标定",
            "停止span量程标定",
            "停止span量程",
            "停止校零",
            "停止量程标定",
        ))

    def cache_calibration_detail_log(self, message, has_time=True):
        # The old backend always closes zero/span workers during shutdown.  On
        # a direct-start experiment those are internal cleanup messages, not a
        # calibration result, and the legacy stop dialog did not expose them.
        if self._is_uncalibrated_stop_housekeeping(message):
            return
        self.calibration_detail_log_buffer.append((message, has_time))
        self._append_workflow_runtime_log(message, "校准")
        if len(self.calibration_detail_log_buffer) > 1000:
            self.calibration_detail_log_buffer = self.calibration_detail_log_buffer[-1000:]
        if self.calibration_details_windows is not None:
            self.calibration_details_windows.addLog(message, has_time=has_time)

    def restore_calibration_detail_window_state(self):
        if self.calibration_details_windows is None:
            return
        for message, has_time in self.calibration_detail_log_buffer:
            self.calibration_details_windows.addLog(message, has_time=has_time)
        is_air_calibration = (
            self.calibration_detail_status_text is not None
            and "Air空气校准" in self.calibration_detail_status_text
        )
        if self.calibration_detail_zero_start_time is not None:
            update_start_time = (
                self.calibration_details_windows.updateAirStartTime
                if is_air_calibration
                else self.calibration_details_windows.updateZeroStartTime
            )
            update_start_time(self.calibration_detail_zero_start_time, log_event=False)
        if self.calibration_detail_zero_end_time is not None:
            update_end_time = (
                self.calibration_details_windows.updateAirEndTime
                if is_air_calibration
                else self.calibration_details_windows.updateZeroEndTime
            )
            update_end_time(self.calibration_detail_zero_end_time, log_event=False)
        if self.calibration_detail_span_start_time is not None:
            self.calibration_details_windows.updateSpanStartTime(self.calibration_detail_span_start_time, log_event=False)
        if self.calibration_detail_span_end_time is not None:
            self.calibration_details_windows.updateSpanEndTime(self.calibration_detail_span_end_time, log_event=False)
        if self.calibration_detail_status_text is not None:
            self.calibration_details_windows.updateStatus(self.calibration_detail_status_text, log_event=False)

    def init__calibration_windows(self):
        #初始化标定窗口
        if self.calibration_details_windows is None:
            self.calibration_details_windows = CalibrationDialog(main_gui=self)
            self.calibration_details_windows.log_history = []
            self.calibration_details_windows.log_sequence = 0
            self.calibration_details_windows.log_list.clear()
            self.calibration_details_windows.updateO2Span(global_setting.get_setting('span_standard_oxygen_value',0), log_event=False)
            self.calibration_details_windows.updateCO2Span(global_setting.get_setting('span_standard_carbon_value',0), log_event=False)
            self.calibration_details_windows.updatePressureSpan(float(global_setting.get_setting('UFC_UGC_ZOS_config')['ZOS']['pressure_steady_default']), log_event=False)
            self.restore_calibration_detail_window_state()
    def show_calibration_windows(self,data):
        # A delayed signal must never reopen the calibration dialog while the
        # experiment is stopping or after it has stopped.
        if self.workflow_run_state in {
            ExperimentRunState.STOPPING,
            ExperimentRunState.STOPPED,
            ExperimentRunState.STOP_FAILED,
        }:
            return
        self.init__calibration_windows()
        if data:
            match data.get("type",''):
                case "zero_calibration"|"all_calibration":
                    self.calibration_details_windows.updateStatus("零点标定", log_event=False)
                    self.calibration_details_windows.updateZeroStartTime(data.get("time"), log_event=False)
                    pass
                case "span_calibration":
                    self.calibration_details_windows.updateStatus("量程标定", log_event=False)
                    self.calibration_details_windows.updateSpanStartTime(data.get("time"), log_event=False)
                    pass
                case _:
                    pass

        self.calibration_details_windows.show()
    def release_calibration_windows(self):
        # 释放标定窗口
        if hasattr(self, "_calibration_chart_refresh_timer"):
            self._calibration_chart_refresh_timer.stop()
        if self.calibration_details_windows is not None:
            self.calibration_details_windows.hide()
            self.calibration_details_windows.close()
            self.calibration_details_windows.deleteLater()
            self.calibration_details_windows = None

    #   延遲打開窗口
    def start_open_window(self, resolve, reject):
        # Keep the new workflow control page visible so its startup status and
        # real progress remain readable. Preserve legacy auto-navigation.
        page = getattr(self, "experiment_control_page", None)
        if page is None or self.tab_widget.currentWidget() is not page:
            QTimer.singleShot(1 * 1000, self.open_monitor_data_window)
        resolve()

    def open_monitor_data_window(self):
        """
        打開監控數據界面
        :return:
        """
        # 打開窗口
        for module in self.modules:
            module: BaseModule
            if module.name == "Main_New_Monitor_data":
                module.click_method()
                return

    def pause_experiment(self):
        # 在with语句中自动管理加载遮罩
        with LoadingContext(self, "正在暂停...", "animated") as mask:
            self.setEnabled(False)
            try:
                if self.ufc_ugc_zos is not None and not self.ufc_ugc_zos.ispause:
                    self.ufc_ugc_zos.pause()
                else:
                    self.ufc_ugc_zos.resume()
                if self.ufc_ugc_zos_thread is not None and not self.ufc_ugc_zos_thread.isPaused():
                    self.ufc_ugc_zos.disabled_auto_btn_handle()
            except Exception as e:
                logger.error(f"暂停实验监测ufc_ugc_zos错误，原因：{e}")
            try:
                if self.store_thread_sub is not None and not self.store_thread_sub.isPaused():
                    self.store_thread_sub.pause()
                else:
                    self.store_thread_sub.resume()
            except Exception as e:
                logger.error(f"暂停实验监测store_thread_sub错误，原因：{e}")
                self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
            try:
                if self.add_message_thread_sub is not None and not self.add_message_thread_sub.isPaused():
                    self.add_message_thread_sub.pause()
                else:
                    self.add_message_thread_sub.resume()
            except Exception as e:
                logger.error(f"暂停实验监测add_message_thread_sub错误，原因：{e}")
                self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
            try:
                if self.send_thread_sub is not None and not self.send_thread_sub.isPaused():
                    self.send_thread_sub.pause()
                else:
                    self.send_thread_sub.resume()
            except Exception as e:
                logger.error(f"暂停实验监测send_thread_sub错误，原因：{e}")
                self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
            try:
                if self.read_queue_data_thread_sub is not None and not self.read_queue_data_thread_sub.isPaused():
                    self.read_queue_data_thread_sub.pause()
                else:
                    self.read_queue_data_thread_sub.resume()
            except Exception as e:
                logger.error(f"暂停实验监测read_queue_data_thread_sub错误，原因：{e}")
                self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
            # 所有红外相机线程停止
            for camera_struct_l in self.infrared_camera_thread_sub_list:
                if len(camera_struct_l) != 0 and 'camera' in camera_struct_l:
                    try:
                        if camera_struct_l['camera'] is not None and not camera_struct_l['camera'].isPaused():
                            camera_struct_l['camera'].pause()
                        else:
                            camera_struct_l['camera'].resume()
                    except Exception as e:
                        logger.error(f"暂停实验监测infrared_camera_thread_sub_list错误，原因：{e}")
                        self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
            try:
                if self.infrared_camera_delete_file_thread_sub is not None and not self.infrared_camera_delete_file_thread_sub.isPaused():
                    self.infrared_camera_delete_file_thread_sub.pause()
                else:
                    self.infrared_camera_delete_file_thread_sub.resume()
            except Exception as e:
                logger.error(f"暂停实验监测infrared_camera_delete_file_thread_sub错误，原因：{e}")
                self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
            try:
                if self.infrared_camera_read_queue_data_thread_sub is not None and not self.infrared_camera_read_queue_data_thread_sub.isPaused():
                    self.infrared_camera_read_queue_data_thread_sub.pause()
                else:
                    self.infrared_camera_read_queue_data_thread_sub.resume()
            except Exception as e:
                logger.error(f"暂停实验监测infrared_camera_read_queue_data_thread_sub错误，原因：{e}")
                self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
            # 所有深度相机线程停止
            for camera_struct_l in self.deep_camera_thread_sub_list:
                if len(camera_struct_l) != 0 and 'camera' in camera_struct_l:
                    try:
                        if camera_struct_l['camera'] is not None and not camera_struct_l['camera'].isPaused():
                            camera_struct_l['camera'].pause()
                        else:
                            camera_struct_l['camera'].resume()
                    except Exception as e:
                        logger.error(f"暂停实验监测deep_camera_thread_sub_list错误，原因：{e}")
                        self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
                if len(camera_struct_l) != 0 and 'img_process' in camera_struct_l:
                    try:
                        if camera_struct_l['img_process'] is not None and not camera_struct_l['img_process'].isPaused():
                            camera_struct_l['img_process'].pause()
                        else:
                            camera_struct_l['img_process'].resume()
                    except Exception as e:
                        logger.error(f"暂停实验监测deep_camera_thread_sub_list_img_process错误，原因：{e}")
                        self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
            try:
                if self.deep_camera_delete_file_thread_sub is not None and not self.deep_camera_delete_file_thread_sub.isPaused():
                    self.deep_camera_delete_file_thread_sub.pause()
                else:
                    self.deep_camera_delete_file_thread_sub.resume()
            except Exception as e:
                logger.error(f"暂停实验监测deep_camera_delete_file_thread_sub错误，原因：{e}")
                self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
            try:
                if self.deep_camera_read_queue_data_thread_sub is not None and not self.deep_camera_read_queue_data_thread_sub.isPaused():
                    self.deep_camera_read_queue_data_thread_sub.pause()
                else:
                    self.deep_camera_read_queue_data_thread_sub.resume()
            except Exception as e:
                logger.error(f"暂停实验监测deep_camera_read_queue_data_thread_sub错误，原因：{e}")
                self.status_bar.update_tip(f"暂停实验监测错误，原因：{e}")
            pass

            self.is_paused = not self.is_paused
            if self.is_paused:
                pause_experiment_time = global_setting.get_setting("pause_experiment_time", [])
                pause_experiment_time.append(time.time())
                global_setting.set_setting("pause_experiment_time", pause_experiment_time)
                self.status_bar.update_tip(f"暂停实验监测成功！")
            else:
                relieve_pause_experiment_time = global_setting.get_setting("relieve_pause_experiment_time", [])
                relieve_pause_experiment_time.append(time.time())
                global_setting.set_setting("relieve_pause_experiment_time", relieve_pause_experiment_time)
                self.status_bar.update_tip(f"解除暂停实验监测成功！")
            self.status_bar.update_status(is_paused=self.is_paused)

            for action_dict in self.tool_bar_actions:
                if action_dict["obj_name"] == "pause_experiment":
                    action_dict["action"]: QAction
                    if self.is_paused:
                        action_dict["name"] = "解除暂停实验"
                    else:
                        action_dict["name"] = "暂停实验"
                    action_dict["action"].setToolTip(action_dict["name"])
                    action_dict["action"].setText(action_dict["name"])
            self.setEnabled(True)
        pass

    def stop_experiment(self):
        if global_setting.get_setting("app_state", AppState.INITIALIZED) != AppState.MONITORING:
            return
        if self.workflow_run_state == ExperimentRunState.STOPPING:
            return
        self._append_workflow_runtime_log("正在停止实验...", "停止")
        self._stop_startup_progress()
        send_message_queue = global_setting.get_setting("send_message_queue", None)
        camera_queue = global_setting.get_setting("queue", None)
        if send_message_queue is None:
            self._mark_workflow_operation_failed("stop", "采集服务控制队列不可用，未发送停止指令。")
            return

        # Match the legacy user-visible flow: stopping uses the stop-progress
        # view only.  Zero/span workers are still stopped by the backend, but
        # their detail window must not remain open or be raised during stop.
        self.release_calibration_windows()

            # ★ 停止实验时清掉所有临时提示，状态栏恢复正常
        self.status_bar._temp_tip_active = False
        self.old_Stop_experiment_status_text_reTurn = None
        self.old_stop_status_counts = 0
        self.stop_export_finished = False
        self.stop_dialog_close_pending = False
        self._stop_backend_complete = False
        self._stop_backend_grace_complete = False
        self._stop_cleanup_started = False
        read_queue_data_thread._stop_ack_titles.clear()
        self.pending_stop_export_success_path = None
        self._stop_operation_failed = False
        if self._gas_path_timeout_timer is not None:
            self._gas_path_timeout_timer.stop()
            self._gas_path_timeout_timer = None
        self._stop_periodic_export_timer()

        # Record the click time before refreshing STOPPING so elapsed time is
        # frozen immediately, matching the legacy stop-dialog semantics.
        global_setting.set_setting("stop_experiment_time", time.time())
        self._set_workflow_run_state(ExperimentRunState.STOPPING)
        page = getattr(self, "experiment_control_page", None)
        if page is not None:
            page.set_operation_progress(0, "正在停止实验")
            page.operation_hint.setText("停止请求已发送，正在等待后台模块停止并保存实验数据。")
            page.refresh()
        self.status_bar.update_tip(f"正在关闭实验监测...")
        # self.stop_experiment_thread = Stop_experiment_thread(name="stop_experiment_thread",window=self)
        # self.stop_experiment_thread.start()
        try:
            send_message_queue.put(ObjectQueueItem(origin='MainWindow_Index', to='main_monitor_data', title='stop',
                                                   data={
                                                       'stop_experiment_time': global_setting.get_setting(
                                                           "stop_experiment_time"),
                                                   },
                                                   time=time_util.get_format_from_time(time.time())))
        except Exception as error:
            self._mark_workflow_operation_failed("stop", error)
            return
        if camera_queue is None:
            self._mark_workflow_operation_failed("stop", "相机/状态回执队列不可用；采集服务停止指令已发出，结果未确认。")
            return
        message_structs = []
        if global_setting.get_setting("environment_module_only", False):
            for title, data in (
                ("stop_deep_camera_complete", {"success": True, "detail": "仅环境模块模式：轨迹相机未启动"}),
                ("stop_infrared_camera_complete", {"success": True, "detail": "仅环境模块模式：红外相机未启动"}),
            ):
                try:
                    camera_queue.put(
                        ObjectQueueItem(
                            origin="MainWindow_Index",
                            to="MainWindow_index",
                            title=title,
                            data=data,
                            time=time_util.get_format_from_time(time.time()),
                        )
                    )
                except Exception as error:
                    self._mark_workflow_operation_failed("stop", error)
                    return
        else:
            message_structs = [
            ObjectQueueItem(origin='MainWindow_Index', to='main_deep_camera', title='stop', data={
                'stop_experiment_time': global_setting.get_setting("stop_experiment_time"),
            },
                            time=time_util.get_format_from_time(time.time())),
            ObjectQueueItem(origin='MainWindow_Index', to='main_infrared_camera', title='stop', data={
                'stop_experiment_time': global_setting.get_setting("stop_experiment_time"),
            },
                            time=time_util.get_format_from_time(time.time())),

            ]
        for message_struct in message_structs:
            try:
                camera_queue.put(message_struct)
            except Exception as error:
                self._mark_workflow_operation_failed("stop", error)
                return

        # 旧版收尾顺序：停止命令与数据归档并行；后台停止、5秒收尾和
        # 数据导出全部完成后再恢复界面。硬件异常只写运行日志，不把
        # 整个实验判成“停止失败”。
        QTimer.singleShot(0, self.start_stop_cleanup_async)
        stop_wait_seconds = self.show_stop_dialog_sync()
        self._stop_progress_started_at = time.monotonic()
        self._stop_progress_duration = max(float(stop_wait_seconds), 1.0)
        self._stop_stop_progress()
        self._stop_progress_timer = QTimer(self)
        self._stop_progress_timer.timeout.connect(self._advance_stop_progress)
        self._stop_progress_timer.start(100)
        if page is not None:
            page.refresh()
        if self._stop_timeout_timer is not None:
            self._stop_timeout_timer.stop()
        self._stop_timeout_timer = QTimer(self)
        self._stop_timeout_timer.setSingleShot(True)
        self._stop_timeout_timer.timeout.connect(self._on_stop_experiment_timeout)
        self._stop_timeout_timer.start(max(int(stop_wait_seconds * 1000), 1000))

    def _on_stop_backend_complete(self):
        if self.workflow_run_state != ExperimentRunState.STOPPING:
            return
        if self._stop_backend_complete:
            return
        self._stop_backend_complete = True
        if self._stop_operation_failed:
            warning = "部分后台模块停止回执异常，继续按旧版流程保存并导出已有实验数据。"
            self._append_workflow_runtime_log(warning, "停止")
            logger.warning(warning)
        self.stop_dialog_close_pending = True
        if self.stop_dialog is not None:
            self.stop_dialog.insert_data_signal.emit("后台停止回执已齐全，等待5秒完成收尾...")
        if self._stop_timeout_timer is not None:
            self._stop_timeout_timer.stop()
        QTimer.singleShot(5000, self._on_stop_backend_grace_elapsed)

    def _on_stop_backend_grace_elapsed(self):
        if self.workflow_run_state != ExperimentRunState.STOPPING:
            return
        self._stop_backend_grace_complete = True
        self._maybe_finalize_stop()

    def _on_stop_backend_issue(self, detail):
        logger.warning(f"backend stop warning: {detail}")
        self._append_workflow_runtime_log(str(detail), "停止")

    def _on_stop_progress(self, detail):
        if self.workflow_run_state != ExperimentRunState.STOPPING:
            return
        self.status_bar.update_tip(detail)
        if self.stop_dialog is not None:
            self.stop_dialog.insert_data_signal.emit(f"{detail} ")

    def _maybe_finalize_stop(self):
        if self.workflow_run_state != ExperimentRunState.STOPPING:
            return
        if (
            not self._stop_backend_complete
            or not self._stop_backend_grace_complete
            or not self.stop_export_finished
        ):
            return
        if self._stop_timeout_timer is not None:
            self._stop_timeout_timer.stop()
        self.stop_update_gui_sync()
        # Export success is independent from device-stop warnings.  The old
        # workflow always showed the successfully saved workbook.
        self._show_pending_stop_export_success_message()
        close_after_stop = getattr(self, "_close_after_stop_if_requested", None)
        if callable(close_after_stop):
            close_after_stop()

    def _on_stop_experiment_timeout(self):
        if self.workflow_run_state == ExperimentRunState.STOPPING:
            warning = "停止回执等待已到旧版倒计时上限；继续等待数据导出完成并保留已有数据。"
            self._stop_backend_complete = True
            self._stop_backend_grace_complete = True
            self._append_workflow_runtime_log(warning, "停止")
            logger.warning(warning)
            self._maybe_finalize_stop()

    def start_stop_cleanup_async(self):
        if self._stop_cleanup_started:
            return
        self._stop_cleanup_started = True
        AsyPromise(self.close_monitor_data_window).then(
            lambda _: AsyPromise(self.stop_store_info_Qtimer)
        ).catch(self._on_stop_cleanup_failed)

    def _on_stop_cleanup_failed(self, error):
        logger.error(f"{error}")
        self.stop_export_finished = True
        if self.workflow_run_state == ExperimentRunState.STOPPING:
            self._maybe_finalize_stop()
        if self.stop_dialog_close_pending and self.stop_dialog is not None:
            self.stop_dialog.update_progress_value(self.stop_dialog.progress_max)

    def stop_update_gui_sync(self):
        self._stop_stop_progress()
        logger.error("stop_update_gui")
        global_setting.set_setting("app_state", AppState.CONFIGURING)
        self._set_workflow_run_state(ExperimentRunState.STOPPED)

        # 更新main_gui组件显示
        self.change_enable_component_app_state_signal.emit()
        self.status_bar.update_status()
        self.status_bar.update_tip("关闭实验监测成功！")
        self._append_workflow_runtime_log("关闭实验监测成功！", "停止")
        page = getattr(self, "experiment_control_page", None)
        if page is not None:
            page.set_operation_progress(100, "实验已停止")
            page.operation_hint.setText("后台模块已停止，实验数据已保存。")
        for action_dict in self.tool_bar_actions:
            if action_dict["obj_name"] == "start_experiment":
                action_dict["action"]: QAction
                action_dict["action"].setDisabled(False)
            if action_dict["obj_name"] == "stop_experiment":
                action_dict["action"]: QAction
                action_dict["action"].setDisabled(True)

        self.setEnabled(True)

    def stop_update_gui(self, resolve, reject):
        self.stop_update_gui_sync()
        resolve()
        pass

    def show_stop_dialog_sync(self):
        stop_wait_times = float(global_setting.get_setting('configer')['dialog_timeout']['timeout']) + float(
            global_setting.get_setting('UFC_UGC_ZOS_config')['UFC']['wait_time']) + 30
        if self.stop_dialog is None:
            self.stop_dialog = AnimatedLoadingDialog(countdown_seconds=stop_wait_times, title="停止实验",
                                                     message="正在停止实验...")
        else:
            self.stop_dialog.reset_progress()
            self.stop_dialog.clear_list_data()
            self.stop_dialog.deleteLater()
            self.stop_dialog = AnimatedLoadingDialog(
                countdown_seconds=stop_wait_times,
                title="停止实验", message="正在停止实验...")

        self._attach_legacy_dialog_runtime_log(self.stop_dialog, "停止")

        if global_setting.get_setting("deep_camera_instance_state", "starting") == "blocked":
            self.stop_dialog.insert_data_signal.emit(
                "独立视频模块占用相机，上位机轨迹相机未启动，无需停止"
            )

        # self.start_dialog.set_progress_range(0, ZOS_gas_path_system.process_nums+UFC_gas_path_system.process_nums+UGC_gas_path_system.process_nums)
        # Stop progress is rendered by the workflow control page as well; the
        # legacy dialog remains hidden to avoid a second progress window.
        self.stop_dialog.hide()
        return stop_wait_times

    def show_stop_dialog(self, resolve, reject):
        self.show_stop_dialog_sync()
        resolve()

    def stop_store_info_Qtimer(self, resolve, reject):
        QTimer.singleShot(100, self.stop_store_info)
        resolve()

    def stop_store_info(self):
        if self._is_periodic_export_running():
            if self.stop_dialog is not None:
                self.stop_dialog.insert_data_signal.emit("等待定时导出完成.... ")
            QTimer.singleShot(1000, self.stop_store_info)
            return

        # 停止实验 将文件夹的数据合并成一个数据文件
        folder_path_data = self._get_experiment_root_path()
        if folder_path_data is not None and os.path.exists(folder_path_data):
            if self.stop_dialog is not None:
                self.stop_dialog.insert_data_signal.emit(f"正在导出数据.... ")
            self._start_stop_export_thread(folder_path_data)
            return

        self.stop_export_finished = True
        if self.stop_dialog_close_pending and self.stop_dialog is not None:
            self.stop_dialog.update_progress_value(self.stop_dialog.progress_max)
        self._maybe_finalize_stop()

    def _start_stop_export_thread(self, folder_path):
        if self.stop_export_thread is not None:
            return
        self.stop_export_thread = StopExperimentExportThread(
            folder_path,
            parent=self,
        )
        self.stop_export_thread.export_status.connect(self._on_stop_export_status)
        self.stop_export_thread.export_finished.connect(self._on_stop_export_finished)
        self.stop_export_thread.finished.connect(self._on_stop_export_thread_ended)
        self.stop_export_thread.start()

    def _on_stop_export_status(self, message):
        logger.info(message)
        if self.stop_dialog is not None:
            self.stop_dialog.insert_data_signal.emit(message)

    def _on_stop_export_finished(self, success, message):
        self.stop_export_finished = True
        if success:
            logger.info(f"stop experiment data export succeeded: {message}")
            self.pending_stop_export_success_path = message
            if self.stop_dialog is not None:
                self.stop_dialog.insert_data_signal.emit(f"数据导出成功: {message}")
        else:
            logger.error(f"stop experiment data export failed: {message}")
            if self.stop_dialog is not None:
                self.stop_dialog.insert_data_signal.emit(f"数据导出失败: {message}")

        if self.stop_dialog_close_pending and self.stop_dialog is not None:
            self.stop_dialog.update_progress_value(self.stop_dialog.progress_max)
        self._maybe_finalize_stop()
        if self.workflow_run_state == ExperimentRunState.STOPPED:
            QTimer.singleShot(0, self._show_pending_stop_export_success_message)

    def _on_stop_export_thread_ended(self):
        export_thread = self.stop_export_thread
        self.stop_export_thread = None
        if export_thread is not None:
            export_thread.deleteLater()

    def _show_pending_stop_export_success_message(self):
        export_path = self.pending_stop_export_success_path
        self.pending_stop_export_success_path = None
        if export_path:
            custom_data_file_util.show_export_success_message(
                export_path,
            )

    def _finalize_mouse_trajectory_views(self) -> bool:
        trajectory_views = []
        seen_widget_ids = set()
        for widget in QApplication.allWidgets():
            stop_trajectory = getattr(widget, "stop_trajectory_thread", None)
            if not callable(stop_trajectory) or id(widget) in seen_widget_ids:
                continue
            seen_widget_ids.add(id(widget))
            trajectory_views.append((widget, stop_trajectory))

        all_finalized = True
        for widget, stop_trajectory in trajectory_views:
            try:
                logger.info(
                    f"finalizing mouse trajectory before experiment archive: "
                    f"widget={type(widget).__name__}"
                )
                if stop_trajectory() is False:
                    all_finalized = False
            except RuntimeError as error:
                logger.warning(
                    f"mouse trajectory widget was already destroyed during finalization: {error}"
                )
            except Exception as error:
                all_finalized = False
                logger.exception(f"mouse trajectory finalization failed: {error}")

        return all_finalized

    def close_monitor_data_window_sync(self):
        """
        关闭監控數據界面
        :return:
        """
        # 关闭窗口
        if not self._finalize_mouse_trajectory_views():
            raise RuntimeError(
                "mouse trajectory finalization did not finish; experiment archive was not started"
            )

        for module in self.modules:
            module: BaseModule
            if module.name == "Main_New_Monitor_data":
                try:
                    module.close()
                except RuntimeError as e:
                    logger.warning(f"关闭监控数据窗口时窗口对象已销毁，跳过关闭：{e}")
                return
        return

    def close_monitor_data_window(self, resolve, reject):
        try:
            self.close_monitor_data_window_sync()
        except Exception as error:
            reject(error)
            return
        resolve()

    def export_experiment_datas(self):
        """
        导出实验数据按钮函数
        :return:
        """
        if self.manual_export_thread is not None and self.manual_export_thread.isRunning():
            QMessageBox.information(self, "正在导出", "已有一个导出任务正在进行，请稍候。")
            return

        db_path = self._get_experiment_db_path()
        if db_path is None or not os.path.exists(db_path):
            QMessageBox.warning(self, "导出失败", "当前实验数据库不存在，无法导出数据。")
            logger.warning(f"手动导出跳过：数据库不存在 {db_path}")
            return

        default_export_path = self._get_experiment_excel_path()
        if default_export_path is None:
            default_export_path = os.path.join(os.getcwd(), "monitor_data.xlsx")
        export_file_path, _ = QFileDialog.getSaveFileName(
            self,
            "保存监测数据",
            default_export_path,
            "Excel文件 (*.xlsx);;所有文件 (*)",
        )
        if not export_file_path:
            return
        if not export_file_path.lower().endswith(".xlsx"):
            export_file_path += ".xlsx"

        logger.info(f"开始手动导出实验数据: {export_file_path}")
        self.manual_export_progress = QProgressDialog(
            "正在导出实验数据，请稍候...",
            "",
            0,
            0,
            self,
        )
        self.manual_export_progress.setWindowTitle("导出实验数据")
        self.manual_export_progress.setCancelButton(None)
        self.manual_export_progress.setMinimumDuration(0)
        self.manual_export_progress.setWindowModality(Qt.WindowModality.WindowModal)
        self.manual_export_progress.show()

        self.manual_export_thread = ManualExcelExportThread(
            db_path=db_path,
            export_file_path=export_file_path,
            parent=self,
        )
        self.manual_export_thread.export_finished.connect(self._on_manual_export_finished)
        self.manual_export_thread.finished.connect(self._on_manual_export_thread_finished)
        self.manual_export_thread.start()

    def _on_manual_export_finished(self, success, message):
        if self.manual_export_progress is not None:
            self.manual_export_progress.close()
            self.manual_export_progress.deleteLater()
            self.manual_export_progress = None

        if success:
            logger.info(f"手动导出实验数据成功: {message}")
            custom_data_file_util.show_export_success_message(message)
        else:
            logger.error(f"手动导出实验数据失败: {message}")
            QMessageBox.critical(self, "导出失败", f"数据导出失败:\n{message}")

    def _on_manual_export_thread_finished(self):
        thread = self.manual_export_thread
        self.manual_export_thread = None
        if thread is not None:
            thread.deleteLater()

    def close_tab(self, index):
        """关闭标签页"""
        self.tab_widget.widget(index).hide()
        self.tab_widget.removeTab(index)

    def _set_module_interface_enabled(self, module: BaseModule, enabled: bool):
        if module is None or module.interface_widget is None:
            return

        widgets = [
            module.interface_widget.frame_obj,
            module.interface_widget.left_frame_obj,
            module.interface_widget.right_frame_obj,
            module.interface_widget.bottom_frame_obj,
        ]
        for widget in widgets:
            if widget is None:
                continue
            try:
                widget.setEnabled(enabled)
            except RuntimeError as e:
                logger.warning(f"同步页面控件可用状态时跳过已销毁窗口：{e}")

    def sync_module_interface_enabled_state(self):
        current_app_state = global_setting.get_setting("app_state", AppState.INITIALIZED)
        handled_modules = set()

        for module in list(self.active_module_widgets) + list(self.open_windows):
            if module is None:
                continue
            module_key = id(module)
            if module_key in handled_modules:
                continue
            handled_modules.add(module_key)
            module_enabled = True if module.app_state is None else module.app_state <= current_app_state
            self._set_module_interface_enabled(module, module_enabled)

    def change_enable_component_app_state(self):
        # 更新程序状态值
        self.status_bar.update_app_state()
        current_app_state = global_setting.get_setting("app_state", AppState.INITIALIZED)
        # Mirror only stable legacy application states into the new header.
        # Transitional failures/stopping are owned by the explicit start/stop
        # paths and must not be overwritten by this compatibility refresh.
        if current_app_state == AppState.MONITORING and self.workflow_run_state not in {
            ExperimentRunState.STARTING,
            ExperimentRunState.STOPPING,
        }:
            self._set_workflow_run_state(ExperimentRunState.RUNNING)
        # ``AppState`` implements equality without a hash method, so use a
        # tuple instead of a set here.  This path runs during GUI startup.
        elif current_app_state in (AppState.APPLYING, AppState.CONFIGURING) and self.workflow_run_state == ExperimentRunState.NOT_STARTED:
            self._set_workflow_run_state(ExperimentRunState.CONFIGURING)
        # 根据程序状态来改变是否可以点击的组件'
        # 设置是否可以点击 menu_bar
        for menu_bar_action in self.menu_bar_actions:
            if menu_bar_action["app_state"] > current_app_state:
                menu_bar_action["action"].setEnabled(False)
            else:
                menu_bar_action["action"].setEnabled(True)
                # 特殊情况
                if current_app_state == AppState.MONITORING \
                        and menu_bar_action['obj_name'] in ["Main_New_experiment", "Main_New_experiment_open"]:
                    menu_bar_action["action"].setEnabled(False)
        # 设置是否可以点击 tool_bar
        for tool_bar_action in self.tool_bar_actions:
            if tool_bar_action["app_state"] > current_app_state:
                tool_bar_action["action"].setEnabled(False)
            else:
                tool_bar_action["action"].setEnabled(True)
            # 特殊按钮需要特殊配置
            obj_name = tool_bar_action["obj_name"]
            if obj_name in ["stop_experiment", "pause_experiment"]:
                tool_bar_action["action"].setEnabled(current_app_state == AppState.MONITORING)
            elif obj_name == "calibration_gas" and global_setting.get_setting(
                "environment_module_only", False
            ):
                tool_bar_action["action"].setEnabled(False)
            elif obj_name in ["toggle_mode", "window_exchange"]:
                tool_bar_action["action"].setEnabled(False)

        # 设置是否可以点击 dynamic tool_bar
        for dynamic_action in self.dynamic_tool_bar_actions:
            if dynamic_action["app_state"] > current_app_state:
                dynamic_action["action"].setEnabled(False)
            else:
                dynamic_action["action"].setEnabled(True)

            if dynamic_action["obj_name"] == "dynamic_New_main_experiment_calibration":
                dynamic_action["action"].setEnabled(
                    not global_setting.get_setting("environment_module_only", False)
                    and (
                        bool(global_setting.get_setting("air_modules_all_valid", False))
                        or bool(global_setting.get_setting("allow_test_calibration_without_air_validation", False))
                    )
                )

        self.sync_module_interface_enabled_state()

        pass

    def reset_guidance(self):
        """重置教程"""
        reply = QMessageBox.question(
            self,
            "确认重置",
            "这将重置所有页面的首次访问状态，下次进入各个页面时会再次显示引导教程。\n\n确定要继续吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            # 重置程序首次运行状态
            self.tutorial.settings_manager.settings["first_run"] = True
            self.tutorial.settings_manager.settings["tutorial_completed"] = False

            # 获取所有以 "first_visit_" 开头的设置项并重置为 True
            keys_to_reset = []
            for key in self.tutorial.settings_manager.settings.keys():
                if key.startswith("first_visit_"):
                    keys_to_reset.append(key)

            # 重置所有页面的首次访问状态
            for key in keys_to_reset:
                self.tutorial.settings_manager.settings[key] = True

            # 也可以直接重置特定页面（如果已知页面名称）
            page_names = ["main_page", "project_page", "settings_page", "help_page"]  # 可根据实际页面名称调整
            for page_name in page_names:
                self.tutorial.settings_manager.settings[f"first_visit_{page_name}"] = True

            self.tutorial.settings_manager.save_settings()

            # 显示重置的页面信息
            reset_pages = [key.replace("first_visit_", "") for key in keys_to_reset]
            if reset_pages:
                pages_info = "、".join(reset_pages)
                message = f"所有状态已重置。\n\n已重置的页面: {pages_info}\n\n重新进入这些页面时将显示引导教程。"
            else:
                message = "首次运行状态已重置。\n重新启动程序或进入页面时将显示引导教程。"

            QMessageBox.information(
                self,
                "重置完成",
                message
            )

            self.status_bar.update_tip("✅ 所有页面的首次访问状态已重置")
# 监听是否自动校准气路模块的框选
    def calibration_gas_state_change(self, state=None):
        current_mode = global_setting.get_setting("startup_calibration_mode", None)
        if current_mode not in {"none", "air", "zero", "span", "full", "o2_span_air", "co2_o2_span_air"}:
            current_mode = "full" if global_setting.get_setting("is_auto_calibration", False) else "none"

        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Icon.Question)
        msg_box.setWindowTitle("启动前校准模式")
        msg_box.setText("请选择校准方式，选择后请点击“开始实验”进行校准。")

        air_button = msg_box.addButton("空气校准", QMessageBox.ButtonRole.ActionRole)
        full_button = msg_box.addButton("校零+校span", QMessageBox.ButtonRole.ActionRole)
        msg_box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
        msg_box.setDefaultButton({
            "air": air_button,
            "full": full_button
        }.get(current_mode, air_button))
        msg_box.exec()

        clicked_button = msg_box.clickedButton()
        mode = None
        if clicked_button == air_button:
            mode = "air"
        elif clicked_button == full_button:
            mode = "full"

        if mode is None:
            return

        global_setting.set_setting("startup_calibration_mode", mode)
        global_setting.set_setting("is_auto_calibration", mode == "full")
        global_setting.set_setting("device_config_calibration_selected", True)

        send_message_queue = global_setting.get_setting("send_message_queue")
        if send_message_queue is not None:
            send_message_queue.put(
                ObjectQueueItem(
                    origin='MainWindow_Index',
                    to='main_monitor_data',
                    title='set_experiment_basic_config',
                    data={
                        "startup_calibration_mode": mode,
                        "is_auto_calibration": mode == "full"
                    },
                    time=time_util.get_format_from_time(time.time())
                )
            )

        self.calibration_selection_changed_signal.emit(True, mode)
        return
        is_checked = bool(state)  # 直接转为布尔值
        # 是否自动校准气体给其他进程同步设置
        global_setting.set_setting("is_auto_calibration", is_checked)
        send_message_queue = global_setting.get_setting("send_message_queue")
        send_message_queue.put(
            ObjectQueueItem(origin='MainWindow_Index', to='main_monitor_data', title='set_experiment_basic_config',
                            data={"is_auto_calibration": is_checked},
                            time=time_util.get_format_from_time(time.time())))


def _patched_calibration_gas_state_change(self, state=None):
    current_mode = global_setting.get_setting("startup_calibration_mode", None)
    if current_mode == "air_co2":
        current_mode = "air"
    if current_mode not in {"none", "air", "zero", "span", "full", "o2_span_air", "co2_o2_span_air"}:
        current_mode = "full" if global_setting.get_setting("is_auto_calibration", False) else "none"

    msg_box = QMessageBox(self)
    msg_box.setIcon(QMessageBox.Icon.Question)
    msg_box.setWindowTitle("启动前校准模式")
    msg_box.setText("请选择校准方式，选择后请点击“开始实验”进行校准。")

    air_button = msg_box.addButton("空气校准O2", QMessageBox.ButtonRole.ActionRole)
    air_co2_button = msg_box.addButton("空气校准CO2", QMessageBox.ButtonRole.ActionRole)
    full_button = msg_box.addButton("校零+校span", QMessageBox.ButtonRole.ActionRole)
    msg_box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    msg_box.setDefaultButton({
        "air": air_button,
        "air_co2": air_co2_button,
        "full": full_button,
    }.get(current_mode, air_button))
    msg_box.exec()

    clicked_button = msg_box.clickedButton()
    mode = None
    if clicked_button == air_button:
        mode = "air"
    elif clicked_button == air_co2_button:
        mode = "air_co2"
    elif clicked_button == full_button:
        mode = "full"

    if mode is None:
        return

    global_setting.set_setting("startup_calibration_mode", mode)
    global_setting.set_setting("is_auto_calibration", mode != "none")
    global_setting.set_setting("device_config_calibration_selected", True)

    send_message_queue = global_setting.get_setting("send_message_queue")
    if send_message_queue is not None:
        send_message_queue.put(
            ObjectQueueItem(
                origin='MainWindow_Index',
                to='main_monitor_data',
                title='set_experiment_basic_config',
                data={
                    "startup_calibration_mode": mode,
                    "is_auto_calibration": mode != "none",
                },
                time=time_util.get_format_from_time(time.time()),
            )
        )

    self.calibration_selection_changed_signal.emit(True, mode)


MainWindow_Index.calibration_gas_state_change = _patched_calibration_gas_state_change


def _patched_calibration_gas_state_change_v2(self, state=None):
    current_mode = global_setting.get_setting("startup_calibration_mode", None)
    if current_mode == "air_co2":
        current_mode = "air"
    if current_mode not in {"none", "air", "zero", "span", "full", "o2_span_air", "co2_o2_span_air"}:
        current_mode = "full" if global_setting.get_setting("is_auto_calibration", False) else "none"

    msg_box = QMessageBox(self)
    msg_box.setIcon(QMessageBox.Icon.Question)
    msg_box.setWindowTitle("启动前校准模式")
    msg_box.setText("请选择校准方式，选择后请点击“开始实验”进行校准。")

    air_button = msg_box.addButton("空气校准", QMessageBox.ButtonRole.ActionRole)
    full_button = msg_box.addButton("校零+校span", QMessageBox.ButtonRole.ActionRole)
    msg_box.addButton("取消", QMessageBox.ButtonRole.RejectRole)
    msg_box.setDefaultButton({
        "air": air_button,
        "full": full_button,
    }.get(current_mode, air_button))
    msg_box.exec()

    clicked_button = msg_box.clickedButton()
    mode = None
    if clicked_button == air_button:
        mode = "air"
    elif clicked_button == full_button:
        mode = "full"

    if mode is None:
        return

    global_setting.set_setting("startup_calibration_mode", mode)
    global_setting.set_setting("is_auto_calibration", mode != "none")
    global_setting.set_setting("device_config_calibration_selected", True)

    send_message_queue = global_setting.get_setting("send_message_queue")
    if send_message_queue is not None:
        send_message_queue.put(
            ObjectQueueItem(
                origin='MainWindow_Index',
                to='main_monitor_data',
                title='set_experiment_basic_config',
                data={
                    "startup_calibration_mode": mode,
                    "is_auto_calibration": mode != "none",
                },
                time=time_util.get_format_from_time(time.time()),
            )
        )

    self.calibration_selection_changed_signal.emit(True, mode)
    selected_label = "空气校准" if mode == "air" else "校零+校span"
    QMessageBox.information(
        self, "校准方式已选择",
        f"已选择{selected_label}，请点击“开始实验”进行校准。",
    )


MainWindow_Index.calibration_gas_state_change = _patched_calibration_gas_state_change_v2
