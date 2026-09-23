import time
import typing

from PyQt6 import QtGui
from PyQt6.QtCore import QRect, Qt, QThread, QTimer, pyqtSignal
from PyQt6.QtWidgets import QComboBox, QDialog, QDialogButtonBox, QLabel, QPushButton
from loguru import logger

from public.component.dialog.deep_camera_config_dialog import Ui_deep_camera_config_dialog
from public.component.dialog.index.camera_mapping_dialog_style import CAMERA_MAPPING_DIALOG_STYLE
from public.config_class.global_setting import global_setting
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.util.folder_util import folder_util
from public.util.json_util import json_util
from public.util.time_util import time_util
from public.util.uvc_camera_util import (
    assign_logical_camera_indices,
    camera_config_identity,
    camera_logical_index,
    enumerate_uvc_cameras,
    resolve_camera_config,
    set_camera_logical_index,
)


class UVCCameraScanThread(QThread):
    scan_finished = pyqtSignal(list, str)

    def run(self):
        try:
            cameras = []
            for scan_round in range(2):
                cameras = self._scan_once()
                if cameras or scan_round == 1:
                    break
                self.msleep(750)
            self.scan_finished.emit(cameras, "")
        except Exception as error:
            logger.exception(f"scan UVC cameras failed: {error}")
            self.scan_finished.emit([], str(error))

    @staticmethod
    def _scan_once():
        cameras = enumerate_uvc_cameras(probe=True)
        for camera in cameras:
            logger.info(
                "Found UVC camera: "
                f"current_index={camera.get('device_index')}, "
                f"stable_id={camera.get('stable_id')}, "
                f"size={camera.get('width')}x{camera.get('height')}"
            )
        logger.info(f"UVC camera scan completed: count={len(cameras)}")
        return cameras


class deep_camera_config_dialog(QDialog):
    def __init__(self, parent=None, geometry: QRect = None, title="", tip=""):
        super().__init__()
        self.tip = tip
        self.camera_series_list = []
        self.camera_series_list_select_need = []
        self.camera_series_choose_list = [None for _ in range(8)]
        self.rebind_logical_indices: dict[int, int] = {}

        self.combo_boxes: dict[int, QComboBox] = {}
        self.checked_labels: dict[int, QLabel] = {}
        self.clear_buttons: dict[int, QPushButton] = {}

        self.refresh_btn: QPushButton | None = None
        self.ok_btn: QPushButton | None = None
        self.scan_thread: UVCCameraScanThread | None = None
        self._close_when_scan_finishes = False
        self._base_window_title = ""

        self._init_ui(parent, geometry, title)
        self._init_customize_ui()
        self.init_data()
        self._base_window_title = self.windowTitle()

    def showEvent(self, a0: typing.Optional[QtGui.QShowEvent]) -> None:
        logger.warning("deep_config_dialog-show")
        super().showEvent(a0)

    def hideEvent(self, a0: typing.Optional[QtGui.QHideEvent]) -> None:
        logger.warning("deep_config_dialog-hide")
        super().hideEvent(a0)

    def _init_ui(self, parent=None, geometry: QRect = None, title=""):
        if parent is not None and geometry is not None:
            self.setParent(parent)
            self.setGeometry(geometry)

        self.ui = Ui_deep_camera_config_dialog()
        self.ui.setupUi(self)
        self.setStyleSheet(CAMERA_MAPPING_DIALOG_STYLE)
        self.setParent(None)
        # 保留右上角关闭按钮。
        self.setWindowFlag(Qt.WindowType.WindowCloseButtonHint, True)
        self.setModal(True)
        self.setWindowTitle(title)

    def _init_customize_ui(self):
        tip_label: QLabel = self.findChild(QLabel, "tip_label")
        if tip_label is not None:
            tip_label.setText(f"{tip_label.text()}{self.tip}")

        for cage_num in range(1, 9):
            combo = self.findChild(QComboBox, f"d_mouse_cage_{cage_num}_select")
            label = self.findChild(QLabel, f"d_mouse_cage_{cage_num}_checked_value")
            btn = self.findChild(QPushButton, f"d_mouse_cage_{cage_num}_checked_value_btn")
            if combo is not None:
                self.combo_boxes[cage_num] = combo
            if label is not None:
                self.checked_labels[cage_num] = label
            if btn is not None:
                self.clear_buttons[cage_num] = btn

        button_box = self.findChild(QDialogButtonBox, "dialog_btn")
        if button_box is not None:
            try:
                button_box.accepted.disconnect()
            except TypeError:
                pass
            self.ok_btn = button_box.button(QDialogButtonBox.StandardButton.Ok)
        self.refresh_btn = self.findChild(QPushButton, "refresh")

        self.init_combox()
        self.bind_events()

    def bind_events(self):
        for cage_num, btn in self.clear_buttons.items():
            btn.clicked.connect(lambda checked=False, n=cage_num: self.label_btn_func(n))

        for cage_num, combo in self.combo_boxes.items():
            combo.activated.connect(lambda data_index, n=cage_num: self.selection_change_combox(n, data_index))

        if self.refresh_btn is not None:
            self.refresh_btn.clicked.connect(self.refresh_func)
        if self.ok_btn is not None:
            self.ok_btn.clicked.connect(self.ok_func)

    def init_data(self):
        config_file_path = f"./{global_setting.get_setting('camera_config')['DEEP_CAMERA']['camera_to_mouse_cage_number_file_name']}"
        if not folder_util.is_exist_file(config_file_path):
            return

        serials = json_util.read_json_to_dict_list(config_file_path)
        for data in serials:
            cage_num = data.get("mouse_cage_number")
            if not isinstance(cage_num, int) or cage_num < 1 or cage_num > 8:
                continue

            self.camera_series_choose_list[cage_num - 1] = data
            if cage_num in self.checked_labels:
                self.checked_labels[cage_num].setText(data.get("display_name") or data.get("serial", "未选中"))

        selected_serials = {
            item.get("serial")
            for item in self.camera_series_choose_list
            if item is not None
        }
        self.camera_series_list_select_need = [
            item for item in self.camera_series_list_select_need if item.get("serial") not in selected_serials
        ]
        self.init_combox()

    def init_combox(self):
        for combo in self.combo_boxes.values():
            combo.blockSignals(True)
            combo.clear()
            for camera_obj in self.camera_series_list_select_need:
                combo.addItem(f"-相机: {camera_obj.get('display_name', camera_obj.get('serial', ''))}")
            combo.blockSignals(False)

    def _return_camera_to_available(self, camera):
        if camera is None:
            return
        current = resolve_camera_config(camera, self.camera_series_list)
        if current is None:
            return
        current.pop("mouse_cage_number", None)
        current_identity = camera_config_identity(current)
        current_logical_index = camera_logical_index(current)
        already_available = any(
            camera_config_identity(item) == current_identity
            if current_identity
            else camera_logical_index(item) == current_logical_index
            for item in self.camera_series_list_select_need
        )
        if not already_available:
            self.camera_series_list_select_need.append(current)

    def label_btn_func(self, cage_num):
        old_value = self.camera_series_choose_list[cage_num - 1]
        old_logical_index = camera_logical_index(old_value)
        if old_logical_index is not None:
            self.rebind_logical_indices[cage_num] = old_logical_index
        self._return_camera_to_available(old_value)

        self.camera_series_choose_list[cage_num - 1] = None
        if cage_num in self.checked_labels:
            self.checked_labels[cage_num].setText("未选中")
        self.init_combox()

    def selection_change_combox(self, cage_num, data_index):
        if data_index < 0 or data_index >= len(self.camera_series_list_select_need):
            return

        selected_camera = self.camera_series_list_select_need[data_index]
        old_value = self.camera_series_choose_list[cage_num - 1]
        old_logical_index = camera_logical_index(old_value)
        if old_logical_index is None:
            old_logical_index = self.rebind_logical_indices.pop(cage_num, None)
        else:
            self.rebind_logical_indices.pop(cage_num, None)
        selected_logical_index = camera_logical_index(selected_camera)
        if old_logical_index is not None:
            if (
                old_value is None
                and selected_logical_index is not None
                and selected_logical_index != old_logical_index
            ):
                for index, available_camera in enumerate(self.camera_series_list_select_need):
                    if index == data_index:
                        continue
                    if camera_logical_index(available_camera) == old_logical_index:
                        self.camera_series_list_select_need[index] = set_camera_logical_index(
                            available_camera,
                            selected_logical_index,
                        )
                        break
            if selected_logical_index is not None:
                if old_value is not None:
                    old_value = set_camera_logical_index(
                        old_value,
                        selected_logical_index,
                    )
            selected_camera = set_camera_logical_index(
                selected_camera,
                old_logical_index,
            )
        self._return_camera_to_available(old_value)

        selected_value = dict(selected_camera)
        selected_value["mouse_cage_number"] = cage_num
        self.camera_series_choose_list[cage_num - 1] = selected_value

        self.camera_series_list_select_need = [
            item for idx, item in enumerate(self.camera_series_list_select_need) if idx != data_index
        ]

        if cage_num in self.checked_labels:
            self.checked_labels[cage_num].setText(
                self.camera_series_choose_list[cage_num - 1].get("display_name")
                or self.camera_series_choose_list[cage_num - 1].get("serial", "未选中")
            )
        self.init_combox()

    def refresh_func(self):
        if self.scan_thread is not None and self.scan_thread.isRunning():
            return

        self._set_scan_controls_enabled(False)

        self.scan_thread = UVCCameraScanThread(self)
        self.scan_thread.scan_finished.connect(self._apply_scan_result)
        self.scan_thread.finished.connect(self._on_scan_thread_finished)
        self.scan_thread.finished.connect(self.scan_thread.deleteLater)
        self.scan_thread.start()

    def _apply_scan_result(self, cameras, error):
        if error:
            logger.error(f"deep camera refresh failed: {error}")

        if self._close_when_scan_finishes:
            return

        raw_cameras = [item for item in cameras if isinstance(item, dict)]
        self.camera_series_list = assign_logical_camera_indices(
            raw_cameras,
            self.camera_series_choose_list,
        )

        for cage_index, selected in enumerate(self.camera_series_choose_list):
            if selected is None:
                continue
            resolved = resolve_camera_config(selected, self.camera_series_list)
            if resolved is None:
                continue
            cage_num = cage_index + 1
            resolved["mouse_cage_number"] = cage_num
            self.camera_series_choose_list[cage_index] = resolved
            if cage_num in self.checked_labels:
                self.checked_labels[cage_num].setText(
                    resolved.get("display_name") or resolved.get("serial", "未选中")
                )

        selected_identities = {
            camera_config_identity(item)
            for item in self.camera_series_choose_list
            if item is not None and camera_config_identity(item)
        }
        selected_logical_indices = {
            camera_logical_index(item)
            for item in self.camera_series_choose_list
            if item is not None and not camera_config_identity(item)
        }
        self.camera_series_list_select_need = [
            item
            for item in self.camera_series_list
            if camera_config_identity(item) not in selected_identities
            and camera_logical_index(item) not in selected_logical_indices
        ]
        self.init_combox()

    def _set_scan_controls_enabled(self, enabled: bool):
        if self.refresh_btn is not None:
            self.refresh_btn.setEnabled(enabled)
        if self.ok_btn is not None:
            self.ok_btn.setEnabled(enabled)

    def _on_scan_thread_finished(self):
        finished_thread = self.sender()
        if finished_thread is self.scan_thread:
            self.scan_thread = None

        if self._close_when_scan_finishes:
            self._close_when_scan_finishes = False
            self.setWindowTitle(self._base_window_title)
            super().reject()
            return

        self._set_scan_controls_enabled(True)

    def _defer_close_for_scan(self) -> bool:
        if self.scan_thread is None or not self.scan_thread.isRunning():
            return False

        self._close_when_scan_finishes = True
        self._set_scan_controls_enabled(False)
        self.setWindowTitle(f"{self._base_window_title}（正在结束相机检测…）")
        return True

    def reject(self):
        if self._defer_close_for_scan():
            return
        super().reject()

    def closeEvent(self, event: QtGui.QCloseEvent) -> None:
        if self._defer_close_for_scan():
            event.ignore()
            return
        super().closeEvent(event)

    def ok_func(self):
        choose_data = [item for item in self.camera_series_choose_list if item is not None]
        config_file_path = f"./{global_setting.get_setting('camera_config')['DEEP_CAMERA']['camera_to_mouse_cage_number_file_name']}"
        json_util.store_json_from_dict_list(filename=config_file_path, data=choose_data)

        queue = global_setting.get_setting("queue", None)
        if queue is not None:
            queue.put(
                ObjectQueueItem(
                    origin="deep_camera_config_dialog_index",
                    to="main_deep_camera",
                    data=choose_data,
                    title="camera_config",
                    time=time_util.get_format_from_time(time.time()),
                )
            )

        self.accept()

    def show_frame(self):
        QTimer.singleShot(0, self.refresh_func)
        self.exec()
