"""
自定义文件的创建和解析
将文件夹全部转成一个文件格式
"""
import base64
import json
import os
import tempfile
import threading

import pandas as pd
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFileDialog, QMessageBox
from loguru import logger

from public.function.Tansfer.DbTransferExcel import DbTransferExcel
from public.util.folder_util import folder_util
class custom_template_file_util:
    """自定义模板文件"""
    encoding = "utf-8-sig"
    extension_name = "template"
    db_extension_name="db"
    pass
    @classmethod
    def save_template_contents_as_custom_file(cls,db_file_path):
        content=None
        with open(db_file_path, 'rb') as f:
            #将二进制内容编码为 Base64 的字符串
            content = base64.b64encode(f.read()).decode(cls.encoding)  #  转成base64字符串格式

        # 获取上层路径
        parent_directory = os.path.dirname(db_file_path)
        folder_name = os.path.basename(db_file_path)
        # 分离扩展名
        file_name_without_extension, _ = os.path.splitext(folder_name)
        custom_file_path = os.path.join(parent_directory, f'{file_name_without_extension}.{cls.extension_name}')
        # 将内容写入自定义格式文件
        with open(custom_file_path, 'w', encoding=cls.encoding) as custom_file:
            json.dump(content, custom_file, ensure_ascii=False, indent=4)
        #删除该DB文件
        # 检查文件是否存在
        if os.path.isfile(db_file_path):
            os.remove(db_file_path)  # 删除文件
        return custom_file_path
    @classmethod
    def load_template_contents_from_custom_file(cls,custom_file_path):
        # 读取自定义格式文件
        with open(custom_file_path, 'r', encoding=cls.encoding) as custom_file:
            content = json.load(custom_file)

        # 获取文件所在的文件夹路径
        folder_path = os.path.dirname(custom_file_path)
        # 从路径中获取文件名（带扩展名）
        file_name_with_extension = os.path.basename(custom_file_path)
        # 分离扩展名
        file_name_without_extension, _ = os.path.splitext(file_name_with_extension)
        target_file = os.path.join(folder_path, f"{file_name_without_extension}.{cls.db_extension_name}")

        # 将内容写入文件
        with open(target_file, 'wb') as f:
            f.write(base64.b64decode(content))
        return target_file
    @classmethod
    def get_db_extension_file(cls,file_path):
        # 获取文件所在的文件夹路径
        folder_path = os.path.dirname(file_path)
        # 从路径中获取文件名（带扩展名）
        file_name_with_extension = os.path.basename(file_path)
        # 分离扩展名
        file_name_without_extension, _ = os.path.splitext(file_name_with_extension)
        return os.path.join(folder_path, f"{file_name_without_extension}.{cls.db_extension_name}")

    @classmethod
    def get_template_extension_file(cls, file_path):
        # 获取文件所在的文件夹路径
        folder_path = os.path.dirname(file_path)
        # 从路径中获取文件名（带扩展名）
        file_name_with_extension = os.path.basename(file_path)
        # 分离扩展名
        file_name_without_extension, _ = os.path.splitext(file_name_with_extension)
        return os.path.join(folder_path, f"{file_name_without_extension}.{cls.extension_name}")
class custom_data_file_util:
    """自定义数据文件"""
    encoding = "utf-8-sig"
    extension_name = "Mdata"
    _export_state = threading.local()

    @classmethod
    def _set_last_export_error(cls, message=""):
        cls._export_state.last_error = str(message or "")

    @classmethod
    def get_last_export_error(cls):
        return getattr(cls._export_state, "last_error", "")

    @classmethod
    def show_export_success_message(cls, export_file_path, additional_file_paths=None):
        additional_file_paths = [
            str(path) for path in (additional_file_paths or []) if path
        ]
        exported_files = [str(export_file_path), *additional_file_paths]
        if additional_file_paths:
            file_details = (
                f"原始数据: {export_file_path}\n"
                + "\n".join(
                    f"称重拟合数据: {path}" for path in additional_file_paths
                )
            )
        else:
            file_details = f"数据已导出到: {export_file_path}"
        msg_box = QMessageBox(
            QMessageBox.Icon.Information,
            "导出成功",
            f"{file_details}\n\n点击'open'按钮可以打开保存的文件以及文件夹。",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Open
        )
        msg_box.setDefaultButton(QMessageBox.StandardButton.Open)
        msg_box.setWindowFlags(msg_box.windowFlags())
        if QMessageBox.StandardButton.Open == msg_box.exec():
            folder_util.open_folder(os.path.dirname(export_file_path))
            for exported_file in exported_files:
                folder_util.open_folder(exported_file)

    @classmethod
    def save_folder_contents_as_custom_file(
            cls,
            folder_path,
            is_delete_original_data_file=True,
            show_success_message=True):
        contents = {}

        # 遍历文件夹
        for root, _, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                try:
                    # 读取文件内容
                    with open(file_path, 'rb') as f:
                        #将二进制内容编码为 Base64 的字符串
                        contents[os.path.relpath(file_path, folder_path)] = base64.b64encode(f.read()).decode(cls.encoding)  #  转成base64字符串格式
                except Exception as e:
                    logger.error(f"{e}")
        # 获取上层路径
        parent_directory = os.path.dirname(folder_path)
        folder_name = os.path.basename(folder_path)
        custom_file_path = os.path.join(parent_directory, f'{folder_name}.{cls.extension_name}')
        # 将内容写入自定义格式文件
        with open(custom_file_path, 'w', encoding=cls.encoding) as custom_file:
            json.dump(contents, custom_file, ensure_ascii=False, indent=4)
        # 将数据db文件转成excel文件
        excel_file_path=os.path.join(parent_directory, f'{folder_name}.xlsx')
        experiment_db_file_path = os.path.join(folder_path, "data", "data.db")
        if not os.path.exists(experiment_db_file_path):
            error = f"实验数据库不存在: {experiment_db_file_path}"
            cls._set_last_export_error(error)
            logger.error(error)
            return False
        export_success = cls.export_data_to_csv(
            export_file_path=excel_file_path,
            file_name=folder_name,
            file_path=experiment_db_file_path,
            show_success_message=show_success_message,
            use_atomic_replace=True,
        )


        #删除该文件夹
        if is_delete_original_data_file and export_success:
            folder_util.remove_non_empty_folder(folder_path)
        elif is_delete_original_data_file:
            logger.warning(
                f"数据导出失败，保留原始实验数据目录：{folder_path}"
            )
        return excel_file_path if export_success else False
    @classmethod
    def export_data_to_csv(cls, export_file_path=None, file_name=None, file_path=None,
                           show_success_message=True, use_atomic_replace=False):
        cls._set_last_export_error()
        # 将数据db文件转成excel文件
        if file_path is None:
            transfer_handle = DbTransferExcel()
        else:
            transfer_handle = DbTransferExcel(db_name=file_path)
        if export_file_path is None:
            try:
                file_path, _ = QFileDialog.getSaveFileName(
                    None,
                    "保存监测数据",
                    f"{file_name}.xlsx",
                    "excel文件 (*.xlsx);;所有文件 (*)"
                )

                if file_path:
                    export_file_path=file_path
                else:
                    QMessageBox.warning(None, "错误", f"选择保存路径失败")
                    return False
            except Exception as e:
                QMessageBox.warning(None, "错误", f"选择保存路径失败: {str(e)}")
                return False
        used_sheet_names = set()
        actual_export_file_path = export_file_path
        temp_export_file_path = None
        if use_atomic_replace and export_file_path is not None:
            temp_dir = os.path.dirname(export_file_path) or "."
            export_extension = os.path.splitext(export_file_path)[1] or ".xlsx"
            temp_fd, temp_export_file_path = tempfile.mkstemp(
                prefix=f".{os.path.basename(export_file_path)}.",
                suffix=export_extension,
                dir=temp_dir
            )
            os.close(temp_fd)
            actual_export_file_path = temp_export_file_path
        try:
            import openpyxl
            with pd.ExcelWriter(actual_export_file_path, engine="openpyxl") as writer:
                transfer_handle.export_db_to_excel(writer, combine_mode=True, sheet_used=used_sheet_names,
                                                   chunksize=(5000 or None))
            if temp_export_file_path is not None:
                os.replace(temp_export_file_path, export_file_path)
            if show_success_message:
                msg_box = QMessageBox(
                    QMessageBox.Icon.Information,
                    "导出成功",
                      f"数据已导出到: {export_file_path}\n\n点击'open'按钮可以打开保存的文件以及文件夹。",
                    QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Open
                )
                msg_box.setDefaultButton(QMessageBox.StandardButton.Open)
                # msg_box.setWindowFlags(msg_box.windowFlags() | Qt.WindowType.WindowStaysOnTopHint)
                msg_box.setWindowFlags(msg_box.windowFlags() )

                if QMessageBox.StandardButton.Open == msg_box.exec():
                    # 打开保存的文件夹和文件
                    folder_util.open_folder(os.path.dirname(export_file_path))
                    folder_util.open_folder(export_file_path)
            return True
        except Exception as e:
            cls._set_last_export_error(e)
            logger.exception("数据导出失败")
            if temp_export_file_path is not None and os.path.exists(temp_export_file_path):
                try:
                    os.remove(temp_export_file_path)
                except OSError as remove_error:
                    logger.error(f"删除临时导出文件失败: {remove_error}")
            return False
    @classmethod
    def save_folder_contents_as_custom_file_for_user_choose(cls, folder_path, is_delete_original_data_file=True):
        contents = {}

        # 遍历文件夹
        for root, _, files in os.walk(folder_path):
            for file in files:
                file_path = os.path.join(root, file)
                # 读取文件内容
                with open(file_path, 'rb') as f:
                    # 将二进制内容编码为 Base64 的字符串
                    contents[os.path.relpath(file_path, folder_path)] = base64.b64encode(f.read()).decode(
                        cls.encoding)  # 转成base64字符串格式

        # 获取上层路径
        parent_directory = os.path.dirname(folder_path)
        folder_name = os.path.basename(folder_path)
        custom_file_path = os.path.join(parent_directory, f'{folder_name}.{cls.extension_name}')
        # 将内容写入自定义格式文件
        with open(custom_file_path, 'w', encoding=cls.encoding) as custom_file:
            json.dump(contents, custom_file, ensure_ascii=False, indent=4)

        # 将数据db文件转成excel文件
        transfer_handle = DbTransferExcel()
        excel_file_path = os.path.join(parent_directory, f'{folder_name}.xlsx')
        used_sheet_names = set()
        try:
            import openpyxl
            with pd.ExcelWriter(excel_file_path, engine="openpyxl") as writer:
                transfer_handle.export_db_to_excel(writer, combine_mode=True, sheet_used=used_sheet_names,
                                                   chunksize=(5000 or None))
        except Exception as e:
            logger.error(f"{e}")
        # 删除该文件夹
        if is_delete_original_data_file:
            folder_util.remove_non_empty_folder(folder_path)
    @classmethod
    def load_folder_contents_from_custom_file(cls,custom_file_path):
        # 读取自定义格式文件
        with open(custom_file_path, 'r', encoding=cls.encoding) as custom_file:
            contents = json.load(custom_file)

        # 获取文件所在的文件夹路径
        folder_path = os.path.dirname(custom_file_path)
        # 从路径中获取文件名（带扩展名）
        file_name_with_extension = os.path.basename(custom_file_path)
        # 分离扩展名
        file_name_without_extension, _ = os.path.splitext(file_name_with_extension)
        target_folder = os.path.join(folder_path, file_name_without_extension)
        # 将内容写入指定文件夹
        for relative_path, content in contents.items():
            # 创建目标文件夹（如果不存在）
            target_file_path = os.path.join(target_folder, relative_path)
            os.makedirs(os.path.dirname(target_file_path), exist_ok=True)
            # 将内容写入文件
            with open(target_file_path, 'wb') as f:
                f.write(base64.b64decode(content))
        return target_folder
