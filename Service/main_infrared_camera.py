# 加载日志配置
import csv
import multiprocessing
import sys
import threading
import traceback
from collections import deque
from datetime import datetime
from pathlib import Path

import numpy as np
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QApplication
from loguru import logger
import cv2 as cv

from public.component.dialog.index.infrared_camera_config_dialog_index import infrared_camera_config_dialog
from public.config_class import global_load
from public.config_class.global_setting import global_setting
from public.config_class.ini_parser import ini_parser
from public.dao.SQLite.Monitor_Datas_Handle import Monitor_Datas_Handle
from public.entity.MyQThread import MyQThread, MyThread
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.function.Modbus.Modbus_Type import Others_Tables
from public.system_health import HealthPolicy, HealthReporter

from public.util.folder_util import folder_util
from public.util.json_util import json_util
from public.util.time_util import time_util
import argparse
import time
import os

from equipment.infrared_camera.senxor.utils import connect_senxor, data_to_frame, remap, \
    cv_filter, cv_render, \
    RollingAverageFilter, Display
from equipment.infrared_camera.senxor.utils import CVSegment
from imutils.video import VideoStream

# 删除线程
delete_file_thread = None
camera_list = []
# 被其他红外相机已经使用的串口
is_used_ports = []
infrared_connect_lock = threading.Lock()
experiment_running = False
np.set_printoptions(precision=1)

# global constants
# 删除线程和图像处理线程锁 保证同步
delete_process_lock = threading.Lock()
TIP_SEGM_PARAM = {
    # threshold-based segmentation
    # ----------------------------
    # supported: simple, otsu, adaptive
    'threshold_type': 'simple',
    # threshold value for simple thresholding
    'threshold': 170,
    'contour_minArea': -3,

    # contour analysis
    # ----------------
    # absolute value of the area of the smallest contour
    'min_contourarea': 5,
    # extention of the bounding box of the target contour
    # for estimating background temperature
    'bbox_extension': 10,
}

THERMAL_DISPLAY_FILTER_PARAM = {
    'temporal_window': 10,
    'blur_ks': 3,
    'd': 7,
    'sigmaColor': 20,
    'sigmaSpace': 20,
}

ROI_EFFECTIVE_MAX_TOP_N = 100

# 总图像帧数量
frame_nums = 0
lock = threading.Lock()


# This queue is independent from the existing GUI command queue.  Health
# updates must be best-effort and must never consume or delay camera commands.
HEALTH_SUCCESS_REPORT_INTERVAL_SECONDS = 2.0
_health_snapshot_queue = None
_health_reporters = {}
_health_reporters_lock = threading.RLock()


def _set_health_snapshot_queue(health_snapshot_queue):
    global _health_snapshot_queue, _health_reporters
    with _health_reporters_lock:
        _health_snapshot_queue = health_snapshot_queue
        _health_reporters = {}


def _health_publish_interval_seconds():
    try:
        health_config = (global_setting.get_setting("configer", {}) or {}).get("system_health", {})
        return max(float(health_config.get("publish_interval_seconds", HEALTH_SUCCESS_REPORT_INTERVAL_SECONDS)), 0.1)
    except (AttributeError, TypeError, ValueError):
        return HEALTH_SUCCESS_REPORT_INTERVAL_SECONDS


def _health_policy():
    try:
        health_config = (global_setting.get_setting("configer", {}) or {}).get("system_health", {})
    except (AttributeError, TypeError):
        health_config = {}
    try:
        failure_threshold = max(int(float(health_config.get("failure_threshold", 3))), 1)
    except (TypeError, ValueError):
        failure_threshold = 3
    try:
        recovery_threshold = max(int(float(health_config.get("recovery_success_threshold", 2))), 1)
    except (TypeError, ValueError):
        recovery_threshold = 2
    return HealthPolicy(
        failure_threshold=failure_threshold,
        recovery_success_threshold=recovery_threshold,
    )


def _health_reporter(component, scope="global"):
    if _health_snapshot_queue is None:
        return None
    key = (str(component), str(scope))
    with _health_reporters_lock:
        reporter = _health_reporters.get(key)
        if reporter is None:
            reporter = HealthReporter(
                _health_snapshot_queue,
                component=key[0],
                scope=key[1],
                policy=_health_policy(),
            )
            _health_reporters[key] = reporter
        return reporter


def _channel_health_scope(cage_number):
    try:
        return f"CH{int(cage_number):02d}"
    except (TypeError, ValueError):
        return str(cage_number)


def load_infrared_ellipse_mask_config(cage_id):
    config_path = str(resolve_project_path("config/infrared_ellipse_mask_config.json"))
    camera_config = global_setting.get_setting("camera_config")["INFRARED_CAMERA"]
    fallback = {
        "enabled": int(camera_config.get("ellipse_mask_enabled", 1)),
        "center_x_ratio": float(camera_config.get("ellipse_center_x_ratio", 0.50)),
        "center_y_ratio": float(camera_config.get("ellipse_center_y_ratio", 0.54)),
        "axis_x_ratio": float(camera_config.get("ellipse_axis_x_ratio", 0.36)),
        "axis_y_ratio": float(camera_config.get("ellipse_axis_y_ratio", 0.34)),
        "angle": float(camera_config.get("ellipse_angle", 0.0)),
    }
    if not os.path.exists(config_path):
        return fallback

    try:
        raw = json_util.read_json_to_dict_list(config_path)
    except Exception as e:
        logger.error(f"load infrared ellipse mask config failed: {e}")
        return fallback

    merged = dict(fallback)
    merged.update(raw.get("default", {}))
    merged.update(raw.get("cages", {}).get(str(cage_id), {}))
    return merged

# 过滤日志
# logger = logger.bind(category="infrared_camera_logger")
class read_queue_data_Thread(MyQThread):
    def __init__(self, name):
        super().__init__(name)
        self.queue = None
        self.camera_list = None
        pass

    def dosomething(self):
        global experiment_running, camera_list
        if not self.queue.empty():
            try:
                message: ObjectQueueItem = self.queue.get()
            except Exception as e:
                logger.error(f"{self.name}发生错误{e}")
                return
            if message is not None and message.is_Empty():
                return
            if message is not None and isinstance(message, ObjectQueueItem) and message.to=='main_infrared_camera':
                logger.error(f"{self.name}_get_message:{message}")
                match message.title:
                    case 'stop_running_cameras':
                        _stop_infrared_camera_threads(self.camera_list or [])
                    case 'prepare_camera_scan':
                        request_data = message.data if isinstance(message.data, dict) else {}
                        released = _stop_infrared_camera_threads(self.camera_list or [])
                        if released:
                            self.camera_list = []
                            camera_list = []
                        self.queue.put(
                            ObjectQueueItem(
                                origin="main_infrared_camera",
                                to="main_gui",
                                title="infrared_camera_scan_ready",
                                data={
                                    "request_id": str(request_data.get("request_id", "")),
                                    "released": released,
                                },
                                time=time_util.get_format_from_time(time.time()),
                            )
                        )
                    case 'start':
                        data = message.data
                        if data is not None:
                            global_setting.set_setting(
                                "infrared_feature_enabled",
                                bool(data.get("infrared_enabled", False)),
                            )
                            global_setting.set_setting("start_experiment_time", data.get("start_experiment_time",time.time()))
                            global_setting.set_setting("pause_experiment_time", data.get("pause_experiment_time",[]))
                            global_setting.set_setting("relieve_pause_experiment_time", data.get("relieve_pause_experiment_time",[]))
                        experiment_running = True
                        start()
                    case 'pause':
                        pause()
                    case 'stop':
                        data = message.data
                        if data is not None:
                            global_setting.set_setting("stop_experiment_time",
                                                       data.get("stop_experiment_time", time.time()))
                        experiment_running = False
                        stop()
                    case 'experiment_setting':
                        data = message.data
                        if data is not None:
                            # 将实验设置存入全局变量
                            global_setting.set_setting("experiment_setting", data.get("experiment_setting", None))
                            global_setting.set_setting("experiment_setting_file",
                                                       data.get("experiment_setting_file", ""))

                        pass
                    case 'camera_config':
                        data = message.data
                        if experiment_running and data:
                            init_camera_and_image_handle_thread(data)
                        else:
                            logger.info("infrared camera config saved; camera startup deferred until experiment start")
                    case _:
                        pass

            else:
                # 把消息放回去
                self.queue.put(message)

        pass


read_queue_data_thread = read_queue_data_Thread(name="main_infrared_camera_read_queue_data_thread")


import msvcrt
import shutil
from public.util.project_paths import resolve_project_path


def get_infrared_history_config():
    if not global_setting.get_setting("infrared_feature_enabled", False):
        return {
            'enabled': False,
            'root_path': '',
            'keep_days': 0,
        }
    camera_config = global_setting.get_setting("camera_config")
    infrared_config = camera_config.get('INFRARED_CAMERA', {})
    storage_root = resolve_project_path(camera_config.get('STORAGE', {}).get('fold_path', './data/'))
    history_root = os.path.normpath(
        str(storage_root / str(infrared_config.get('history_path', 'infrared_camera_history/')).strip('/\\'))
    )
    return {
        'enabled': bool(int(infrared_config.get('history_enabled', 0))),
        'root_path': history_root,
        'keep_days': int(infrared_config.get('history_keep_days', 0)),
    }



# class coordinate_writing:
#     """
#     将处理的温度数据写入csv文件，支持Windows下多设备并发访问
#     """
#
#     def __init__(self, path, camera_id):
#         self.path = path
#         self.camera_id = camera_id
#         self.csv_file = None
#         self.csv_writer = None
#         self.folder_path = self.path
#         self.original_filename = self.folder_path + global_setting.get_setting("camera_config")['INFRARED_CAMERA'][
#             'tmp_filename'] + f"_{time_util.get_format_file_from_time(global_setting.get_setting('start_experiment_time', time.time()))}.{global_setting.get_setting('camera_config')['INFRARED_CAMERA']['tmp_extension']}"
#         self.filename = self.original_filename  # 当前使用的文件名
#         self.max_retries = 10  # 最大重试次数
#         self.retry_delay = 0.1  # 重试间隔（秒）
#         self.permission_counter = 0  # 权限文件计数器
#
#     def _lock_file(self, file):
#         """锁定文件"""
#         try:
#             msvcrt.locking(file.fileno(), msvcrt.LK_LOCK, 1)
#         except PermissionError:
#             return False
#         return True
#
#     def _unlock_file(self, file):
#         """解锁文件"""
#         try:
#             msvcrt.locking(file.fileno(), msvcrt.LK_UNLCK, 1)
#         except PermissionError:
#             return False
#         return True
#
#     def _get_permission_filename(self, counter):
#         """
#         生成带有permission后缀的文件名
#         :param counter: 计数器
#         :return: 新文件名
#         """
#         base_name, ext = os.path.splitext(self.original_filename)
#         return f"{base_name}_permission_{counter}{ext}"
#
#     def _copy_to_permission_file(self):
#         """
#         复制当前文件到新的permission文件
#         :return: 新文件名
#         """
#         self.permission_counter += 1
#         new_filename = self._get_permission_filename(self.permission_counter)
#
#         try:
#             # 如果原文件存在，复制它
#             if os.path.exists(self.filename):
#                 shutil.copy2(self.filename, new_filename)
#                 logger.info(f"文件被占用，已复制到新文件: {new_filename}")
#             else:
#                 logger.info(f"原文件不存在，创建新文件: {new_filename}")
#
#             # 更新当前使用的文件名
#             self.filename = new_filename
#             return new_filename
#
#         except Exception as e:
#             logger.error(f"复制文件失败: {e}")
#             raise
#
#     def _safe_file_operation(self, operation, mode='a'):
#         """
#         安全的文件操作，带重试机制和文件复制备份
#         :param operation: 要执行的操作函数
#         :param mode: 文件打开模式
#         :return: 操作结果
#         """
#         for attempt in range(self.max_retries):
#             try:
#                 if not os.path.exists(self.folder_path):
#                     os.makedirs(self.folder_path)
#
#                 with open(self.filename, mode=mode, newline='', encoding='utf-8') as file:
#                     # 获取文件锁
#                     if not self._lock_file(file):
#                         if attempt < self.max_retries - 1:
#                             logger.info(
#                                 f"文件被占用，{self.retry_delay}秒后重试... (尝试 {attempt + 1}/{self.max_retries})")
#                             time.sleep(self.retry_delay)
#                             continue
#                         else:
#                             logger.error(f"文件访问失败，已重试{self.max_retries}次，尝试复制到新文件")
#                             # 达到最大重试次数，复制文件
#                             self._copy_to_permission_file()
#                             # 使用新文件重试一次
#                             with open(self.filename, mode=mode, newline='', encoding='utf-8') as new_file:
#                                 if self._lock_file(new_file):
#                                     try:
#                                         result = operation(new_file)
#                                         return result
#                                     finally:
#                                         self._unlock_file(new_file)
#                                 else:
#                                     logger.error(f"新文件 {self.filename} 也被占用，递归复制")
#                                     # 如果新文件也被占用，递归调用自己
#                                     return self._safe_file_operation(operation, mode)
#
#                     try:
#                         # 执行操作
#                         result = operation(file)
#                         return result
#                     finally:
#                         # 释放文件锁
#                         self._unlock_file(file)
#
#             except PermissionError as e:
#                 if attempt < self.max_retries - 1:
#                     logger.info(f"文件被占用，{self.retry_delay}秒后重试... (尝试 {attempt + 1}/{self.max_retries})")
#                     time.sleep(self.retry_delay)
#                 else:
#                     logger.error(f"文件访问失败，已重试{self.max_retries}次: {e}，尝试复制到新文件")
#                     # 达到最大重试次数，复制文件并重试
#                     self._copy_to_permission_file()
#                     return self._safe_file_operation(operation, mode)
#
#             except Exception as e:
#                 logger.error(f"文件操作出错: {e}")
#
#
#     def csv_create(self):
#         """创建CSV文件并写入表头"""
#
#         def create_operation(file):
#             csv_writer = csv.writer(file)
#             csv_writer.writerow(["Timestamp", "tmp_hs_mean(摄氏度)"])
#
#         self._safe_file_operation(create_operation, mode='w')
#
#     def csv_write(self, file_base_name, t):
#         """写入一行温度数据到CSV"""
#
#         def write_operation(file):
#             csv_writer = csv.writer(file)
#             csv_writer.writerow([file_base_name, t])
#
#         self._safe_file_operation(write_operation, mode='a')
#
#     def csv_write_batch(self, data_list):
#         """
#         批量写入温度数据，提高效率
#         :param data_list: 列表，每个元素是 (timestamp, temperature) 的元组
#         """
#
#         def batch_write_operation(file):
#             csv_writer = csv.writer(file)
#             csv_writer.writerows(data_list)
#
#         self._safe_file_operation(batch_write_operation, mode='a')
#
#     def csv_close(self):
#         """
#         关闭CSV文件（由于使用with语句，实际上不需要显式关闭）
#         保留此方法以保持向后兼容
#         """
#         if self.csv_file is not None:
#             self.csv_file.close()
#             self.csv_file = None
#             self.csv_writer = None
#
#     def get_current_filename(self):
#         """
#         获取当前正在使用的文件名
#         :return: 当前文件名
#         """
#         return self.filename


class TIP:
    """Thermal Image Pipeline"""

    def __init__(self, param):
        # 图像尺寸设置
        self.image_scale = param.get('image_scale', 1)
        self.ncol_nrow = param.get('fpa_ncol_nrow', (80, 62))
        self.image_size = (self.image_scale * self.ncol_nrow[0],
                           self.image_scale * self.ncol_nrow[1])
        # 伪彩色
        self.colormap = param.get('colormap', 'rainbow2')
        self.ellipse_mask_enabled = bool(param.get('ellipse_mask_enabled', 1))
        self.ellipse_center_x_ratio = float(param.get('ellipse_center_x_ratio', 0.50))
        self.ellipse_center_y_ratio = float(param.get('ellipse_center_y_ratio', 0.54))
        self.ellipse_axis_x_ratio = float(param.get('ellipse_axis_x_ratio', 0.36))
        self.ellipse_axis_y_ratio = float(param.get('ellipse_axis_y_ratio', 0.34))
        self.ellipse_angle = float(param.get('ellipse_angle', 0.0))

        # 图像分割器初始化
        self.segment = CVSegment(param)

    def _ellipse_geometry(self, image_shape):
        height, width = image_shape[:2]
        center = (
            int(width * self.ellipse_center_x_ratio),
            int(height * self.ellipse_center_y_ratio),
        )
        axes = (
            max(1, int(width * self.ellipse_axis_x_ratio)),
            max(1, int(height * self.ellipse_axis_y_ratio)),
        )
        return center, axes, self.ellipse_angle

    def _build_ellipse_mask(self, frame_shape):
        if not self.ellipse_mask_enabled:
            return None

        mask = np.zeros(frame_shape[:2], dtype=np.uint8)
        center, axes, angle = self._ellipse_geometry(frame_shape)
        cv.ellipse(mask, center, axes, angle, 0, 360, 255, -1)
        return mask

    def _measure_roi_temperature(self, frame):
        mask = self._build_ellipse_mask(frame.shape)
        if mask is None:
            return None, None, None

        roi_pixels = frame[mask > 0]
        if roi_pixels.size == 0:
            return mask, None, None

        roi_max = float(np.max(roi_pixels))
        roi_background = float(np.median(roi_pixels))
        return mask, roi_max, roi_background

    def _draw_ellipse_overlay(self, origin_image):
        if not self.ellipse_mask_enabled:
            return origin_image

        center, axes, angle = self._ellipse_geometry(origin_image.shape)
        cv.ellipse(origin_image, center, axes, angle, 0, 360, (0, 0, 255), 2)
        return origin_image

    def set_max_temp_to_img(self, origin_image, max_temp):
        """
        将最大温度放到图片上
        :return: image 更改之后的图片
        """
        # 定义文字内容
        if max_temp is None:
            text = "roi_max: None degree C"
        else:
            text = f"roi_max: {float(max_temp):.4f} degree C"
        # 定义文字位置（左上角坐标）
        position = (30, 30)

        # 定义字体
        font = cv.FONT_HERSHEY_SIMPLEX

        # 定义字体大小
        font_scale = 0.5

        # 定义颜色 (B, G, R)
        color = (0, 0, 0)

        # 定义线宽
        thickness = 1

        # 在图片上添加文字
        cv.putText(origin_image, text, position, font, font_scale, color, thickness, cv.LINE_AA)
        return origin_image

    def execute(self, frame, display_frame=None):
        """Thermal data processing pipeline; produces image and stats/metrics"""
        render_frame = frame if display_frame is None else display_frame
        # 把热图数据转成 8-bit 显示图像（通常是灰度或伪彩色图像）
        frame_uint8 = remap(render_frame)

        # 图像滤波并进行热点分割 对图像做滤波（如中值/双边滤波），然后用分割器提取热图中最热的区域（hotspot）。
        filtered_ui8 = cv_filter(
            frame_uint8,
            parameters={
                'blur_ks': THERMAL_DISPLAY_FILTER_PARAM['blur_ks'],
                'd': THERMAL_DISPLAY_FILTER_PARAM['d'],
                'sigmaColor': THERMAL_DISPLAY_FILTER_PARAM['sigmaColor'],
                'sigmaSpace': THERMAL_DISPLAY_FILTER_PARAM['sigmaSpace'],
            },
            use_median=True,
            use_bilat=True,
            use_nlm=False)
        # 生成滤波图像
        self.img_filtered = cv_render(filtered_ui8, resize=self.image_size,
                                      colormap=self.colormap,
                                      interpolation=cv.INTER_CUBIC, display=False)
        self.segment(frame=frame, frui8=filtered_ui8)

        # 渲染热点掩膜图像
        try:
            hs = self.segment.hotspots[0]
            hs_mask = hs.out_frames['hs_mask']
            hs_osd = hs.osd
        except IndexError:
            hs = None
            hs_mask = np.zeros(self.image_size, dtype='uint8')
            hs_osd = {}
        # self.img_hs_mask = cv_render(hs_mask, resize=self.image_size,
        #                              colormap='parula',
        #                              interpolation=cv.INTER_NEAREST, display=False)
        _, roi_max, roi_mean = self._measure_roi_temperature(render_frame)
        output_struct = {
            'hs_max': roi_max if roi_max is not None else hs_osd.get('max', None),
            'hs_mean': roi_mean,
        }
        self.img_filtered = self._draw_ellipse_overlay(self.img_filtered)
        self.img_filtered = self.set_max_temp_to_img(
            self.img_filtered,
            output_struct['hs_max'],
        )
        # 返回处理后图像和温度统计数据。
        images = {
            'display': self.img_filtered,
            # 'hotspot_mask': self.img_hs_mask,
        }

        return images, output_struct

    def __call__(self, thermal_data, display_frame=None):
        return self.execute(thermal_data, display_frame=display_frame)


class Thermal_process(MyQThread):
    """
    温度处理线程
    """

    def __init__(self, path, id, serial_number):
        super().__init__(name=f"infrared_camera_img_process_{id}")
        self.serial_number = serial_number
        self.path = path
        self.id = id


        #数据库
        self.datasave:Monitor_Datas_Handle=None

        self.mi48 = None
        self.RA_Tmin = None
        self.RA_Tmax = None
        self.tip = None
        self.display = None
        self.vs = None
        self.test_frame = None
        self.init_state = False
        self.connected_port = None
        self.reconnect_delay_seconds = 1.0
        self.next_reconnect_time = 0.0
        self.consecutive_read_failures = 0
        self.last_success_frame_time = 0.0
        self.last_health_report_time = 0.0
        self.frame_buffer = deque(maxlen=THERMAL_DISPLAY_FILTER_PARAM['temporal_window'])
        # 显示初始化失败的日志的控制变量
        self.init_error_log_show = False
        self.health_reporter = _health_reporter(
            "infrared_camera",
            _channel_health_scope(self.id),
        )

    def _health_metrics(self, **extra_metrics):
        metrics = {
            "camera_id": self.id,
            "serial_number": self.serial_number,
            "connected_port": self.connected_port,
            "consecutive_read_failures": self.consecutive_read_failures,
            "sampling_interval_seconds": _health_publish_interval_seconds(),
        }
        metrics.update({key: value for key, value in extra_metrics.items() if value is not None})
        return metrics

    def _report_healthy_frame(self, message="Infrared-camera frame received", force=False):
        reporter = self.health_reporter or _health_reporter(
            "infrared_camera",
            _channel_health_scope(self.id),
        )
        self.health_reporter = reporter
        if reporter is None:
            return
        now = time.monotonic()
        if not force and now - self.last_health_report_time < _health_publish_interval_seconds():
            return
        self.last_health_report_time = now
        reporter.success(metrics=self._health_metrics(), message=message)

    def _report_camera_failure(self, reason_code, message, **extra_metrics):
        reporter = self.health_reporter or _health_reporter(
            "infrared_camera",
            _channel_health_scope(self.id),
        )
        self.health_reporter = reporter
        if reporter is not None:
            reporter.failure(
                reason_code=reason_code,
                message=str(message)[:240],
                metrics=self._health_metrics(**extra_metrics),
            )

    def parse_args(self):
        """
        参数解析
        :return:
        """
        parser = argparse.ArgumentParser()
        parser.add_argument('-tis', '--thermal-image-source', default=None, dest='tis_id',
                            help='Comport name (str) or thermal video source  ID (int)')
        parser.add_argument('-cis', '--color-image-source', type=int, default=None,
                            dest='cis_id',
                            help='Video source ID: 0=laptop cam, 1=USB webcam')
        parser.add_argument('-fps', '--framerate', default=5, type=int, dest='fps',
                            help='Frame rate per second')
        parser.add_argument('-c', '--colormap', default='rainbow2', type=str,
                            help='Colormap for the thermogram')
        parser.add_argument('--data_file', default=None, type=str,
                            help='file instead of camera stream')
        parser.add_argument('-v', '--video-record', default=False, dest='record_video',
                            action='store_true', help='Record a video of what is shown')
        parser.add_argument('-e', '--emissivity', type=float, default=0.95,
                            dest='emissivity', help='target emissivity')
        parser.add_argument('-histo', '--show-histogram', default=False, action='store_true',
                            dest='show_histogram', help='Show thermal image histogram')
        parser.add_argument('-plots', '--show-plots', default=False, action='store_true',
                            dest='show_plots',
                            help='Show plots of measured temperatures')
        parser.add_argument('-scale', '--thermal-image-scale-factor', default=4, type=int,
                            dest='img_scale', help='Scale factor for thermogram')

        args = parser.parse_args()
        return args

    def senxor_init(self):
        """
        温度传感器初始化
        :return:True or False
        """
        global is_used_ports
        self._release_camera_connection()
        args = self.parse_args()
        save_dir = args.save_dir if hasattr(args, 'save_dir') else './data'
        os.makedirs(save_dir, exist_ok=True)

        with infrared_connect_lock:
            self.mi48, connected_port, port_names = connect_senxor(
                src=args.tis_id,
                name=f"infrared_camera_{self.id}",
                is_used_ports=is_used_ports,
                serial_number=self.serial_number,
            )
            if (
                self.mi48 is not None
                and self.serial_number
                and self.mi48.camera_id != self.serial_number
            ):
                logger.error(
                    f"infrared camera {self.id} serial mismatch: "
                    f"expected={self.serial_number}, actual={self.mi48.camera_id}"
                )
                try:
                    self.mi48.stop()
                except Exception:
                    pass
                self._report_camera_failure(
                    "camera_serial_mismatch",
                    f"Expected serial {self.serial_number}, got {self.mi48.camera_id}.",
                )
                self.mi48 = None
                connected_port = None
            if self.mi48 is not None and connected_port is not None:
                self.connected_port = connected_port
                if connected_port not in is_used_ports:
                    is_used_ports.append(connected_port)
        if self.mi48 is None:
            if not self.init_error_log_show:
                logger.error(
                    f'infrared camera_{self.id} | Cannot connect to SenXor | The following ports have SenXor attached {port_names}')
                self.init_error_log_show = True
            self._report_camera_failure(
                "camera_connect_failed",
                "Unable to connect to the configured SenXor infrared camera.",
            )
            return False
            # sys.exit(1)
        else:
            logger.info(f'infrared camera_{self.id} | {self.mi48.sn} connected to {connected_port}')
            self.init_error_log_show = False
        logger.info(f'infrared camera_{self.id} | camera_info: {self.mi48.camera_info}')
        try:
            configured_ports = set()
            for interface in self.mi48.interfaces:
                serial_port = getattr(interface, 'port', None)
                if serial_port is None or id(serial_port) in configured_ports:
                    continue
                configured_ports.add(id(serial_port))
                serial_port.timeout = 0.5
                serial_port.write_timeout = 0.5
            self.mi48.set_fps(args.fps)
            self.mi48.regwrite(0xD0, 0x00)
            self.mi48.disable_filter(f1=True, f2=True, f3=True)
            self.mi48.enable_filter(f1=True, f2=True, f3=True)
            self.mi48.regwrite(0xC2, 0x64)
            self.mi48.set_emissivity(args.emissivity)
            self.mi48.set_offset_corr(3)
            self.mi48.start(stream=True, with_header=True)
            self.frame_buffer.clear()

            self.RA_Tmin = RollingAverageFilter(N=10)
            self.RA_Tmax = RollingAverageFilter(N=10)

            tip_param = {
                'colormap': args.colormap,
                'fpa_ncol_nrow': (self.mi48.cols, self.mi48.rows),
                'image_scale': args.img_scale,
            }
            ellipse_mask_config = load_infrared_ellipse_mask_config(self.id)
            tip_param.update(
                {
                    'ellipse_mask_enabled': ellipse_mask_config.get('enabled', 1),
                    'ellipse_center_x_ratio': ellipse_mask_config.get('center_x_ratio', 0.50),
                    'ellipse_center_y_ratio': ellipse_mask_config.get('center_y_ratio', 0.54),
                    'ellipse_axis_x_ratio': ellipse_mask_config.get('axis_x_ratio', 0.36),
                    'ellipse_axis_y_ratio': ellipse_mask_config.get('axis_y_ratio', 0.34),
                    'ellipse_angle': ellipse_mask_config.get('angle', 0.0),
                }
            )

            tip_param.update(TIP_SEGM_PARAM)
            self.tip = TIP(tip_param)

            if args.cis_id is not None:
                self.vs = VideoStream(src=args.cis_id).start()
                self.test_frame = self.vs.read()
            else:
                self.vs = None
                self.test_frame = None

            if self.test_frame is None:
                self.vs = None

            display_options = {
                'window_coord': (0, 0),
                'window_title': f'{self.mi48.camera_id} ({self.mi48.name}), {args.cis_id}',
                'directory': r'data'
            }
            self.display = Display(display_options)

            self.images = {'thermal': {}}
            self.struct = {'thermal': {}}

            # 数据库
            if self.datasave is None:
                self.datasave = Monitor_Datas_Handle()  # 创建数据库
        except Exception as e:
            logger.error(f"infrared camera {self.id} initialization failed: {e}")
            self._report_camera_failure("camera_initialization_failed", e)
            self._release_camera_connection()
            return False
        self.reconnect_delay_seconds = 1.0
        self.next_reconnect_time = 0.0
        self.consecutive_read_failures = 0
        # Report healthy only after a real thermal frame is received.
        return True

    def _release_camera_connection(self):
        global is_used_ports
        camera = self.mi48
        self.mi48 = None
        if camera is not None:
            try:
                camera.stop()
            except Exception as error:
                logger.error(f"infrared camera {self.id} release failed: {error}")

        with infrared_connect_lock:
            if self.connected_port in is_used_ports:
                is_used_ports.remove(self.connected_port)
        self.connected_port = None
        self.init_state = False

    def _schedule_reconnect(self):
        self.next_reconnect_time = time.monotonic() + self.reconnect_delay_seconds
        self.reconnect_delay_seconds = min(self.reconnect_delay_seconds * 2.0, 10.0)

    def stop(self):
        super().stop()
        camera = self.mi48
        if camera is None:
            return
        cancelled_ports = set()
        for interface in camera.interfaces:
            serial_port = getattr(interface, 'port', None)
            if serial_port is None or id(serial_port) in cancelled_ports:
                continue
            cancelled_ports.add(id(serial_port))
            cancel_read = getattr(serial_port, 'cancel_read', None)
            if callable(cancel_read):
                try:
                    cancel_read()
                except Exception as error:
                    logger.debug(f"infrared camera {self.id} cancel read failed: {error}")

    def smooth_frame_temporally(self, frame):
        self.frame_buffer.append(frame.copy())
        if len(self.frame_buffer) == 1:
            return frame
        return np.mean(self.frame_buffer, axis=0, dtype=np.float32)

    def run(self) -> None:
        logger.warning(f"{self.name} thread has been started！")
        self._running=True
        # 图片保存路径 如果不存在则创建
        pic_save_path = self.path + global_setting.get_setting("camera_config")['INFRARED_CAMERA']['pic_dir']
        if not os.path.exists(pic_save_path):
            os.makedirs(pic_save_path)
        global frame_nums
        # 读取之前存储了多少图像
        with os.scandir(pic_save_path) as it:
            for entry in it:
                if entry.is_file():
                    with lock:
                        frame_nums += 1
        try:
            self.msleep(max(int(self.id) - 1, 0) * 150)
            while self._running:
                self.mutex.lock()
                if self._paused:
                    self.condition.wait(self.mutex)
                self.mutex.unlock()

                if not self._running or self.isInterruptionRequested():
                    break
                self.dosomething(pic_save_path)
        finally:
            self._release_camera_connection()
            if self.datasave is not None:
                try:
                    self.datasave.stop()
                except Exception as error:
                    logger.error(f"infrared camera {self.id} data writer stop failed: {error}")
                self.datasave = None
            if self.vs is not None:
                try:
                    self.vs.stop()
                except Exception as error:
                    logger.error(f"infrared camera {self.id} video stream stop failed: {error}")
                self.vs = None
            cv.destroyAllWindows()
            self._running = False
            logger.info(f"infrared camera {self.id} capture thread released")

    def dosomething(self, pic_save_path):
        global frame_nums
        if not self.init_state:
            now = time.monotonic()
            if now < self.next_reconnect_time:
                self.msleep(100)
                return
            self.init_state = self.senxor_init()
            if not self.init_state:
                self._schedule_reconnect()
                return

        start_time = time.time()
        try:
            raw_data, header = self.mi48.read()
        except Exception as error:
            self.consecutive_read_failures += 1
            self._report_camera_failure("camera_read_failed", error)
            if self._running:
                logger.error(
                    f"infrared camera {self.id} read failed: {error} | {traceback.format_exc()}"
                )
                self.init_error_log_show = False
                self._schedule_reconnect()
            self._release_camera_connection()
            return

        if not self._running or self.isInterruptionRequested():
            return
        if raw_data is None:
            self.consecutive_read_failures += 1
            self._report_camera_failure(
                "camera_empty_frame",
                "Infrared camera returned no frame data.",
            )
            if self.consecutive_read_failures >= 3:
                logger.error(f"infrared camera {self.id} returned no data 3 consecutive times")
                self._release_camera_connection()
                self.init_error_log_show = False
                self._schedule_reconnect()
            else:
                self.msleep(100)
            return
        self.consecutive_read_failures = 0
        self.last_success_frame_time = time.time()
        self._report_healthy_frame()

        try:
            frame = data_to_frame(
                raw_data,
                (self.mi48.cols, self.mi48.rows),
                hflip=False,
            )
            Tmin, Tmax = self.RA_Tmin(frame.min()), self.RA_Tmax(frame.max())
            frame = np.clip(frame, Tmin, Tmax)
            display_frame = self.smooth_frame_temporally(frame)
            _imgs, _struct = self.tip(frame, display_frame=display_frame)
            self.images['thermal'].update(_imgs)
            self.struct['thermal'].update(_struct)
            self.display.img = self.display.composer([self.images['thermal']['display']])

            self.display.dir = Path(pic_save_path)
            file_base_name = time_util.get_format_file_from_time(time.time())
            with delete_process_lock:
                self.display.save(f'{file_base_name}.bmp')
            with lock:
                frame_nums += 1

            if self.datasave is None:
                self.datasave = Monitor_Datas_Handle()
            return_data_struct = {
                'module_name': 'MouseInfrared',
                'time': datetime.now().strftime('%Y-%m-%d %H:%M:%S.%f')[:-3],
                'table_name': next(iter(Others_Tables.Mouse_infrared_Data.value.keys())),
                'mouse_cage_number': self.id,
                'data': [
                    {'desc': '识别时间', 'value': file_base_name},
                    {
                        'desc': '最大值温度(摄氏度)',
                        'value': round(float(self.struct['thermal']['hs_max']), 4)
                        if self.struct['thermal']['hs_max'] is not None
                        else None,
                    },
                ],
                'slave_id': 0,
                'function_code': 0,
            }
            status, msg = self.datasave.insert_data(return_data_struct)
            if not status:
                logger.error(f"红外相机{self.id}存储数据错误：{msg}")

            key = cv.waitKey(1) & 0xFF
            if key == ord('q') or key == 27:
                self.stop()
            logger.debug(
                f"infrared_camera_{self.id}| image_process | "
                f"图像处理线程一次处理时间：{time.time() - start_time}秒 | "
                f"此时总图像帧数量:{frame_nums}"
            )
        except Exception as error:
            logger.error(
                f"红外相机_{self.id} |运行出现错误:{error} | {traceback.format_exc()}"
            )
            self._report_camera_failure("camera_processing_failed", error)

        delay_ms = max(
            1,
            int(float(global_setting.get_setting('camera_config')['INFRARED_CAMERA']['delay']) * 1000),
        )
        self.msleep(delay_ms)

    pass


class Delete_file(MyQThread):
    """
    清除文件线程
    """

    def __init__(self, path, start_time):
        super().__init__(name=f"infrared_camera_delete_file")
        self.path = path
        self.start_time = start_time
        self.history_config = get_infrared_history_config()

    def _archive_bmp_file(self, file_path):
        if not self.history_config['enabled']:
            return False
        if os.path.splitext(file_path)[1].lower() != '.bmp':
            return False

        history_root = self.history_config['root_path']
        source_root = os.path.abspath(self.path)
        archive_root = os.path.abspath(history_root)
        if os.path.commonpath([source_root, archive_root]) == source_root:
            logger.error(f"红外历史归档目录不能放在待清理目录内部: {archive_root}")
            return False

        relative_path = os.path.relpath(file_path, self.path)
        archive_dir = os.path.join(history_root, datetime.now().strftime('%Y-%m-%d'))
        archive_path = os.path.join(archive_dir, relative_path)
        os.makedirs(os.path.dirname(archive_path), exist_ok=True)
        if os.path.exists(archive_path):
            stem, ext = os.path.splitext(archive_path)
            archive_path = f"{stem}_{int(time.time() * 1000)}{ext}"
        shutil.move(file_path, archive_path)
        return True

    def _cleanup_history_files(self):
        keep_days = self.history_config['keep_days']
        if not self.history_config['enabled'] or keep_days <= 0:
            return

        history_root = self.history_config['root_path']
        if not os.path.exists(history_root):
            return

        expire_before = time.time() - keep_days * 24 * 60 * 60
        for root, dirs, files in os.walk(history_root, topdown=False):
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    if os.path.getmtime(file_path) < expire_before:
                        os.remove(file_path)
                except Exception as e:
                    logger.error(f"清理红外历史文件失败: {file_path}, reason:{e}")
            for directory in dirs:
                dir_path = os.path.join(root, directory)
                try:
                    if not os.listdir(dir_path):
                        os.rmdir(dir_path)
                except Exception as e:
                    logger.error(f"清理红外历史目录失败: {dir_path}, reason:{e}")

    # 获得删除文件的大小
    def get_and_delete_files(self):

        total_size = 0
        total_nums = 0
        archived_nums = 0
        for root, dirs, files in os.walk(self.path):
            # logger.warning(f"infrared {root} | {dirs} | {files}")
            for file in files:
                # 将记录数据的csv 文件不删除
                if global_setting.get_setting("camera_config")['INFRARED_CAMERA']['tmp_filename'] in file:
                    continue
                file_path = os.path.join(root, file)
                try:
                    size = os.path.getsize(file_path)  # 获取文件大小（字节）
                    size = float(size / 1204 / 1024 / 1024)  # 将字节B转成GB
                    total_size += size
                    total_nums += 1
                    if self._archive_bmp_file(file_path):
                        archived_nums += 1
                    else:
                        os.remove(file_path)  # 删除文件
                except Exception as e:
                    logger.error(
                        f"infrared_camera Failed to delete {file_path}: reason:{e} |  异常堆栈跟踪：{traceback.print_exc()}")
        global frame_nums
        with lock:
            logger.warning(
                f"红外相机 | 清理文件总大小: {total_size} G-bytes | 清理文件总数量： {total_nums} | "
                f"归档bmp数量：{archived_nums} | 此时相机拍摄的图像数量：{frame_nums}"
            )
            frame_nums = 0
        self._cleanup_history_files()
        return total_size


    def dosomething(self) :
        try:
            with delete_process_lock:
                self.history_config = get_infrared_history_config()
                # 获取现在时间与上次删除时间之差
                current_time = time.time()
                elapsed = current_time - self.start_time
                if elapsed >= float(global_setting.get_setting("camera_config")['DELETE']['interval_seconds']):
                    # 尝试删除文件
                    # 获取删除文件内的所有文件大小
                    self.get_and_delete_files()

                    logger.warning(f"infrared_camera 删除文件成功")
                    self.start_time = time.time()

                    pass
                # logger.info(f"时间差{time_util.get_format_minute_from_time(elapsed)}")
            time.sleep(float(global_setting.get_setting("camera_config")['DELETE']['delay']))
        except Exception as e:
            logger.error(f"红外相机删除文件线程运行异常，异常原因：{e} |  异常堆栈跟踪：{traceback.print_exc()}")
        pass


def load_global_setting():
    global_load.load_global_setting_without_Qt()
    # 记录运行时间的开始时间
    start_time = time.time()
    global_setting.set_setting("start_time", start_time)
    logger.info(f"相机连接开始时间：{time_util.get_format_from_time(start_time)}")
    # 记录运行时上一次删除文件时间
    last_delete_time = time.time()
    global_setting.set_setting("last_delete_time", last_delete_time)



def check_setting_cameras_each_number():
    """
    检测是否有相机与鼠笼编号一一对应的文件，如果没有就显示界面让用户选完在进行相机连接，如果有文件则根据文件来一一对应
    :return:
    """
    config_file_path = f"./{global_setting.get_setting('camera_config')['INFRARED_CAMERA']['camera_to_mouse_cage_number_file_name']}"
    if folder_util.is_exist_file(
            config_file_path):
        # 存在配置文件
        # 读取配置文件
        serials = json_util.read_json_to_dict_list(config_file_path)
        serials = serials if isinstance(serials, list) else []
        init_camera_and_image_handle_thread(serials)
        pass
    else:
        reporter = _health_reporter("infrared_camera", "global")
        if reporter is not None:
            reporter.unavailable(
                "not_configured",
                message="No infrared-camera mapping file is configured.",
                metrics={"configured_camera_count": 0},
            )
        # 不存在配置文件

        try:
            logger.error("发送红外相机弹窗")
            queue = global_setting.get_setting("queue",None)
            if queue is not None:
                queue.put(ObjectQueueItem(origin="main_infrared_camera",to="main_gui",title="infrared_camera_config_dialog",time=time_util.get_format_from_time(time.time())))
        except Exception as e:
            logger.error(f"<UNK>{e} |  <UNK>{traceback.print_exc()}")

        pass


def _stop_infrared_camera_threads(camera_structs, wait_ms=5000):
    threads = []
    all_stopped = True
    for camera_struct in camera_structs:
        if not isinstance(camera_struct, dict):
            continue
        camera = camera_struct.get('camera')
        if camera is None:
            continue
        threads.append(camera)
        try:
            camera.stop()
            camera.requestInterruption()
        except Exception as error:
            all_stopped = False
            logger.error(f"infrared camera stop request failed: {error}")

    deadline = time.monotonic() + max(wait_ms, 0) / 1000.0
    for camera in threads:
        remaining_ms = max(0, int((deadline - time.monotonic()) * 1000))
        if camera.isRunning() and (remaining_ms <= 0 or not camera.wait(remaining_ms)):
            all_stopped = False
            logger.error(f"infrared camera thread release timed out: {camera.objectName()}")
    return all_stopped


def init_camera_and_image_handle_thread(serials):
    global camera_list, read_queue_data_thread, is_used_ports
    serials = [item for item in (serials or []) if isinstance(item, dict)]
    if not serials:
        reporter = _health_reporter("infrared_camera", "global")
        if reporter is not None:
            reporter.unavailable(
                "not_configured",
                message="No infrared camera is configured for this standalone test.",
                metrics={"configured_camera_count": 0},
            )

    if not _stop_infrared_camera_threads(camera_list):
        logger.error("infrared camera reconfiguration cancelled: old threads are still running")
        return False

    camera_list = []
    read_queue_data_thread.camera_list = camera_list
    with infrared_connect_lock:
        is_used_ports.clear()

    path = (
        str(resolve_project_path(global_setting.get_setting("camera_config")['STORAGE']['fold_path']))
        + global_setting.get_setting("camera_config")['INFRARED_CAMERA']['path']
    )
    camera_config_temp = global_setting.get_setting("camera_config")
    camera_config_temp['INFRARED_CAMERA']['nums'] = len(serials)
    global_setting.set_setting("camera_config", camera_config_temp)

    mouse_cage_prefix = camera_config_temp['INFRARED_CAMERA']['mouse_cage_prefix']
    for index, camera_config in enumerate(serials, start=1):
        serial_number = str(camera_config.get('serial', '')).strip()
        mouse_cage_number = camera_config.get('mouse_cage_number')
        if not serial_number or mouse_cage_number in (None, ''):
            logger.error(
                f"infrared camera config ignored: index={index}, "
                f"serial={serial_number!r}, mouse_cage_number={mouse_cage_number!r}"
            )
            continue

        try:
            camera = Thermal_process(
                path=path + f"{mouse_cage_prefix}{mouse_cage_number}/",
                id=mouse_cage_number,
                serial_number=serial_number,
            )
            camera_list.append({'id': index, 'camera': camera})
            logger.info(
                f"infrared camera capture thread created: "
                f"index={index}, cage={mouse_cage_number}, serial={serial_number}"
            )
        except Exception as error:
            logger.error(
                f"infrared camera {index} thread creation failed: "
                f"{error} | {traceback.format_exc()}"
            )

    read_queue_data_thread.camera_list = camera_list
    for camera_struct in camera_list:
        camera_struct['camera'].start()
    return True


def main(q, health_snapshot_queue=None):

    app = QCoreApplication(sys.argv)
    # logger.remove(0)
    # logger.add(
    #     "./log/infrared_camera/i_camera_{time:YYYY-MM-DD}.log",
    #     rotation="00:00",
    #     retention="30 days",
    #     enqueue=True,
    #     format="{time:YYYY-MM-DD HH:mm:ss} | {level} |{process.name} | {thread.name} |  {name} : {module}:{line} | {message}",
    #
    # )
    logger.info(f"{'-' * 30}infrared_camera_start{'-' * 30}")
    logger.info(f"{__name__} | {os.path.basename(__file__)}|{os.getpid()}|{os.getppid()}")
    _set_health_snapshot_queue(health_snapshot_queue)
    # 设置全局变量
    load_global_setting()
    # 读取共享信息线程
    global read_queue_data_thread
    read_queue_data_thread.queue = q
    read_queue_data_thread.start()
    global_setting.set_setting("queue", q)

    return app.exec()
    # global camera_list
    # return camera_list, read_queue_data_thread, delete_file_thread,
def start():
    logger.info(f"{'-' * 30}infrared_camera_run{'-' * 30}")
    # 初始化保存路径
    camera_config = global_setting.get_setting("camera_config")
    path = str(resolve_project_path(camera_config['STORAGE']['fold_path']) / \
               str(camera_config['INFRARED_CAMERA']['path']).strip('/\\'))
    global delete_file_thread
    # 删除文件线程
    delete_file_thread = Delete_file(path=path, start_time=global_setting.get_setting("start_time"))
    delete_file_thread.start()

    # 根据设置的相机数量来连接
    check_setting_cameras_each_number()
def restart(q, health_snapshot_queue=None):
    main(q, health_snapshot_queue=health_snapshot_queue)
    start()
def pause():
    logger.info(f"{'-' * 30}infrared_camera_pause{'-' * 30}")
    pass
def stop():
    global camera_list, delete_file_thread, experiment_running, is_used_ports
    logger.info(f"{'-' * 30}infrared_camera_stop{'-' * 30}")
    experiment_running = False
    queue = global_setting.get_setting("queue", None)

    current_cameras = list(camera_list)
    cameras_stopped = _stop_infrared_camera_threads(current_cameras)
    stop_succeeded = bool(cameras_stopped)
    for camera_struct in current_cameras:
        camera = camera_struct.get('camera') if isinstance(camera_struct, dict) else None
        cage_number = getattr(camera, 'id', camera_struct.get('id', '')) if camera is not None else ''
        message = (
            f"红外相机{cage_number}已停止"
            if cameras_stopped
            else f"红外相机{cage_number}停止超时"
        )
        if queue:
            queue.put(
                ObjectQueueItem(
                    origin="main_infrared_camera",
                    to="MainWindow_index",
                    title="stop_infrared_camera_return",
                    data=message,
                    time=time_util.get_format_from_time(time.time()),
                )
            )

    if cameras_stopped:
        camera_list = []
        read_queue_data_thread.camera_list = camera_list
        with infrared_connect_lock:
            is_used_ports.clear()

    try:
        if delete_file_thread is not None:
            delete_file_thread.stop()
            delete_file_thread.requestInterruption()
            if delete_file_thread.isRunning() and not delete_file_thread.wait(5000):
                logger.error("infrared camera delete-file thread release timed out")
                stop_succeeded = False
            else:
                delete_file_thread = None
            if queue:
                queue.put(
                    ObjectQueueItem(
                        origin="main_infrared_camera",
                        to="MainWindow_index",
                        title="stop_infrared_camera_return",
                        data="红外相机-文件删除线程已停止",
                        time=time_util.get_format_from_time(time.time()),
                    )
                )
    except Exception as error:
        stop_succeeded = False
        logger.error(f"infrared camera delete-file thread stop failed: {error}")
        if queue:
            queue.put(
                ObjectQueueItem(
                    origin="main_infrared_camera",
                    to="MainWindow_index",
                    title="stop_infrared_camera_return",
                    data=f"红外相机-文件删除线程停止失败，原因：{error}",
                    time=time_util.get_format_from_time(time.time()),
                )
            )
    if queue:
        queue.put(ObjectQueueItem(
            origin="main_infrared_camera", to="MainWindow_index", title="stop_infrared_camera_complete",
            data={"success": stop_succeeded, "detail": "红外相机停止完成" if stop_succeeded else "红外相机停止存在未释放的线程"},
            time=time_util.get_format_from_time(time.time()),
        ))
    return stop_succeeded
if __name__ == "__main__":
    q = multiprocessing.Queue()
    main(q)
    start()
