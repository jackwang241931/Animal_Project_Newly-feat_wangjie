#sqlite db文件转成excel文件
import re
import time

from typing import List, Tuple

import pandas as pd
from loguru import logger

from public.config_class.global_setting import global_setting
from public.dao.SQLite.Monitor_Datas_Handle import Monitor_Datas_Handle
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.function.Modbus.Modbus_Type import Modbus_Slave_Ids, Modbus_Slave_Type
from public.function.weight.weight_series_formatter import format_weight_series_for_storage
from public.util.time_util import time_util


class DbTransferExcel():
    INVALID_SHEET_CHARS = r'[:\\/*?\[\]]'
    MAX_SHEET_LEN = 31
    EPOCH_HIDDEN_COLUMNS = {"UGC_CO2_origin_num"}
    EPOCH_DISPLAY_COLUMN_ORDER = [
        "UGC_flow_num_1",
        "UGC_CO2_num",
        "UGC_air_pressure",
    ]
    EPOCH_DISPLAY_DESCRIPTIONS = {
        "ENM_running_wheel_num": "当前计量周期内跑轮距离测量值(m)",
        "UGC_flow_num_1": "传感器状态码",
        "UGC_CO2_num": "气压补偿后CO2",
        "UGC_air_pressure": "对齐后CO2",
    }

    def __init__(self,db_name=None):
        self.handler = Monitor_Datas_Handle(db_name)
        pass
    def stop(self):
        self.handler.stop()


    def sanitize_sheet_name(self,name: str, used: set) -> str:
        # 删除非法字符，替换为空格，截断到 31 字符，确保唯一（添加数字后缀）
        s = re.sub(self.INVALID_SHEET_CHARS, " ", name)
        s = s.strip()
        if not s:
            s = "sheet"
        if len(s) > self.MAX_SHEET_LEN:
            s = s[:self.MAX_SHEET_LEN]
        base = s
        i = 1
        while s in used:
            suffix = f"_{i}"
            allowed_len = self.MAX_SHEET_LEN - len(suffix)
            s = (base[:allowed_len] + suffix) if len(base) > allowed_len else (base + suffix)
            i += 1
        used.add(s)
        return s

    def get_table_list(self) -> List[Tuple[str, str]]:
        """Return only the user-facing epoch tables for enabled cages.

        The database also contains raw module tables, the reference/total epoch
        tables, camera/trajectory tables, and metadata tables.  Those tables
        remain available in SQLite, but they are not part of the user-facing
        experiment workbook.
        """
        tables = self.handler.sqlite_manager.get_tables_with_time_sql_results(
            select_column_name=["name", "type"],
            exclude_substr=["sqlite_", "meta"],
        )
        enabled_cages = self._get_enabled_cage_ids()
        filtered_tables = []
        for table_name, table_type in tables:
            match = re.fullmatch(r"Epoch_data_cage_(\d+)", str(table_name))
            if match is None:
                continue
            cage_number = int(match.group(1))
            if enabled_cages is not None and cage_number not in enabled_cages:
                continue
            filtered_tables.append((table_name, table_type))
        return filtered_tables

    @staticmethod
    def _get_enabled_cage_ids():
        """Read enabled cage IDs from the current experiment setting.

        ``experiment_setting`` is the already-filtered setting sent to the
        monitoring process.  Returning ``None`` when it is unavailable keeps
        exporting older standalone databases backward-compatible by allowing
        all discovered epoch cage tables through.
        """
        setting = global_setting.get_setting("experiment_setting", None)
        groups = getattr(setting, "groups", None) if setting is not None else None
        if groups is None:
            logger.warning(
                "导出时未找到当前实验配置，将按数据库中已有的 Epoch_data_cage_N 表导出"
            )
            return None

        enabled_cages = set()
        for group in groups:
            if not getattr(group, "is_selected", True):
                continue
            cage_id = getattr(group, "id", None)
            if cage_id is None:
                cage_id = getattr(group, "name", None)
            try:
                enabled_cages.add(int(cage_id))
            except (TypeError, ValueError):
                logger.warning(f"忽略无法识别的实验笼号: {cage_id!r}")

        # 参考笼用于普通笼的 CO2/O2 等计算，即使用户没有勾选它，
        # 也必须随本次实验一起导出，供结果复核和后续分析使用。
        try:
            configer = global_setting.get_setting("configer", {}) or {}
            mouse_cage_config = configer.get("mouse_cage", {}) or {}
            reference_cage = mouse_cage_config.get("reference")
            if reference_cage not in (None, ""):
                enabled_cages.add(int(reference_cage))
        except (AttributeError, TypeError, ValueError):
            logger.warning("导出时未能解析配置中的参考笼号")
        return enabled_cages

    @staticmethod
    def _get_reference_cage_id(default=0):
        """Return the configured reference cage without making export fragile."""
        try:
            configer = global_setting.get_setting("configer", {}) or {}
            mouse_cage_config = configer.get("mouse_cage", {}) or {}
            reference_cage = mouse_cage_config.get("reference", default)
            if reference_cage in (None, ""):
                return int(default)
            return int(reference_cage)
        except (AttributeError, TypeError, ValueError):
            logger.warning(f"导出时参考笼配置无效，按参考笼{default}处理")
            return int(default)




    def convert_bytes_columns(self,df: pd.DataFrame) -> pd.DataFrame:
        # 将 bytes/bytearray/memoryview 转为 hex 字符串（可读）
        for col in df.columns:
            # 查找列中是否存在 bytes-like
            sample = df[col].dropna()
            if sample.empty:
                continue
            first = sample.iloc[0]
            if isinstance(first, (bytes, bytearray, memoryview)):
                df[col] = df[col].apply(lambda x: x.hex() if isinstance(x, (bytes, bytearray, memoryview)) else x)
        return df

    @staticmethod
    def _normalize_sensor_status_code(value):
        """Convert the UGC sensor state to the public 1=normal, 0=fault code."""
        try:
            if pd.isna(value):
                return value
        except (TypeError, ValueError):
            pass
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            if value == 1:
                return 1
            if value == 0:
                return 0
        normalized = str(value).strip().lower()
        if normalized in {"1", "正常", "ok", "normal", "true"}:
            return 1
        if normalized in {"0", "故障", "错误", "fault", "error", "false"}:
            return 0
        return value

    def _prepare_epoch_export_frame(self, df: pd.DataFrame, table_name: str,
                                    col_mapping: dict) -> Tuple[pd.DataFrame, dict]:
        """Prepare only user-facing Epoch sheets while retaining raw DB columns."""
        if not table_name.startswith("Epoch_data_"):
            return df, col_mapping

        df = df.copy()
        export_col_mapping = dict(col_mapping)
        export_col_mapping.update(self.EPOCH_DISPLAY_DESCRIPTIONS)
        if "WM_weight_num" in df.columns:
            df["WM_weight_num"] = df["WM_weight_num"].map(
                format_weight_series_for_storage
            )
        if "UGC_flow_num_1" in df.columns:
            df["UGC_flow_num_1"] = df["UGC_flow_num_1"].map(
                self._normalize_sensor_status_code
            )

        current_columns = list(df.columns)
        target_columns = [
            column for column in self.EPOCH_DISPLAY_COLUMN_ORDER
            if column in current_columns
        ]
        if not target_columns:
            return df, export_col_mapping

        visible_columns = [
            column for column in current_columns
            if column not in self.EPOCH_HIDDEN_COLUMNS
            and column not in self.EPOCH_DISPLAY_COLUMN_ORDER
        ]
        first_target_index = min(
            (index for index, column in enumerate(current_columns)
             if column in self.EPOCH_DISPLAY_COLUMN_ORDER),
            default=len(current_columns),
        )
        insert_at = sum(
            1 for column in current_columns[:first_target_index]
            if column not in self.EPOCH_HIDDEN_COLUMNS
            and column not in self.EPOCH_DISPLAY_COLUMN_ORDER
        )
        ordered_columns = (
            visible_columns[:insert_at]
            + target_columns
            + visible_columns[insert_at:]
        )
        df = df.loc[:, ordered_columns]
        return df, export_col_mapping

    @staticmethod
    def _format_epoch_excel_sheet(writer: pd.ExcelWriter, sheet_name: str):
        """Keep the two public CO2 columns at exactly four decimal places."""
        worksheet = writer.sheets.get(sheet_name)
        if worksheet is None:
            return

        title_to_column = {
            str(cell.value).strip(): cell.column
            for cell in worksheet[1]
            if cell.value is not None
        }
        for title in ("气压补偿后CO2", "对齐后CO2"):
            column = title_to_column.get(title)
            if column is None:
                continue
            for row in range(2, worksheet.max_row + 1):
                worksheet.cell(row=row, column=column).number_format = "0.0000"

    def export_db_to_excel(self,writer: pd.ExcelWriter, combine_mode: bool, sheet_used: set,
                           chunksize: int = None):

        try:
            tables = self.get_table_list()
            # logger.critical(f"tables: {tables}")
            if not tables:

                return

            reference_cage = self._get_reference_cage_id()
            for name, typ in tables:
                # 构造 sheet 名

                raw_sheet = name
                sheet_name = self.sanitize_sheet_name(raw_sheet, sheet_used)
                sheet_name_split = sheet_name.split("_")
                # 将数据库表英文名字转成中文名字
                module_name_str = sheet_name_split[0]
                cage_number_str = sheet_name_split[len(sheet_name_split) - 1]
                if cage_number_str.isdigit():
                    cage_number = int(cage_number_str)
                else:
                    cage_number=None
                sheet_name_CN= ""
                for modbus_type in Modbus_Slave_Type.Not_Each_Mouse_Cage.value+Modbus_Slave_Type.Each_Mouse_Cage.value+Modbus_Slave_Type.Calibrations.value+Modbus_Slave_Type.Epochs.value+Modbus_Slave_Type.Cameras.value:
                    if module_name_str == modbus_type.value['name']:
                        sheet_name_CN+=modbus_type.value['description']
                        break

                sheet_name_CN+="监控数据"
                if cage_number is not None:
                    sheet_name_CN+=f"_通道{cage_number} {'参考气路' if cage_number == reference_cage else ''}"
                    pass

                # 读取 'xxx_meta' 表，它包含字段名称和中文描述
                col_mapping = {}
                meta_table_name = f"{name}_meta"
                if self.handler.sqlite_manager.check_table_exists(meta_table_name):
                    quoted_meta_table = self.handler.sqlite_manager.quote_ident(
                        meta_table_name
                    )
                    meta_query = (
                        f"SELECT item_name, description FROM {quoted_meta_table}"
                    )
                else:
                    logger.warning(
                        f"数据表{name}缺少元数据表{meta_table_name}，"
                        "将使用原字段名导出"
                    )
                    meta_query = None
                # 正确的调用方式
                if meta_query is not None:
                    with self.handler.sqlite_manager.get_connection() as conn:
                        meta_df = pd.read_sql_query(meta_query, conn)
                # logger.critical(f"meta_df: {meta_df}")
                # 创建列名到中文描述的映射字典
                if meta_query is not None:
                    col_mapping = dict(
                        zip(meta_df['item_name'], meta_df['description'])
                    )

                quoted_table = self.handler.sqlite_manager.quote_ident(name)
                sql = f"SELECT * FROM {quoted_table}"
                logger.info(f"[INFO] 从 db 的 {typ} {name} 导出到 sheet '{sheet_name}|{sheet_name_CN}' ...")

                if chunksize and chunksize > 0:
                    startrow = 0
                    # 使用 pandas.read_sql_query 的 chunksize 返回迭代器
                    with self.handler.sqlite_manager.get_connection() as conn:
                        for i, chunk in enumerate(pd.read_sql_query(sql, conn, chunksize=chunksize)):
                            # logger.critical(f"chunk: {chunk}")
                            df = self.convert_bytes_columns(chunk)
                            df, export_col_mapping = self._prepare_epoch_export_frame(
                                df, name, col_mapping
                            )
                            # 替换列名
                            df.rename(columns=export_col_mapping, inplace=True)
                            # header 仅写入第一块
                            header = (startrow == 0)
                            df.to_excel(writer, sheet_name=sheet_name_CN, index=False, startrow=startrow, header=header)
                            startrow += len(df)
                    self._format_epoch_excel_sheet(writer, sheet_name_CN)
                else:
                    with self.handler.sqlite_manager.get_connection() as conn:
                        df = pd.read_sql_query(sql, conn,)
                        df = self.convert_bytes_columns(df)
                        df, export_col_mapping = self._prepare_epoch_export_frame(
                            df, name, col_mapping
                        )
                        # 替换列名
                        df.rename(columns=export_col_mapping, inplace=True)
                        df.to_excel(writer, sheet_name=sheet_name_CN, index=False)
                    self._format_epoch_excel_sheet(writer, sheet_name_CN)
        finally:
            # # 返回响应
            # queue = global_setting.get_setting("queue", None)
            # if queue:
            #     queue.put(
            #         ObjectQueueItem(origin="DbTransferExcel", to="MainWindow_index",
            #                         title="stop_store_data_return",
            #                         data=f"成功导出数据",
            #                         time=time_util.get_format_from_time(time.time())))
            self.handler.stop()
    pass
