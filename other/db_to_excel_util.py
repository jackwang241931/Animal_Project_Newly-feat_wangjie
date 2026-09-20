import os
import sqlite3
from typing import List, Tuple

import pandas as pd
import re

from loguru import logger

from public.function.Modbus.Modbus_Type import Modbus_Slave_Type
from public.function.Tansfer.DbTransferExcel import DbTransferExcel


# # 连接数据库
# conn = sqlite3.connect('data.db')
#
# # 获取所有表名（排除系统表）
# cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
# all_tables = [row[0] for row in cursor.fetchall() if not row[0].startswith('sqlite_')]
#
# # 分离数据表和元数据表
# all_data_tables = [table for table in all_tables if not table.endswith('_meta')]
# meta_tables = [table for table in all_tables if table.endswith('_meta')]
#
# # 排除末尾数字为2-8的数据表
# data_tables = []
# for table in all_data_tables:
#     # 检查表名是否以数字2-8结尾
#     if re.search(r'[2-8]$', table):
#         print(f"跳过表: {table} (末尾数字为2-8)")
#         continue
#     data_tables.append(table)
#
# print(f"发现数据表: {data_tables}")
# print(f"发现元数据表: {meta_tables}")
#
# # 导出到Excel
# with pd.ExcelWriter('data_export_7.xlsx', engine='xlsxwriter') as writer:
#     # 导出数据表
#     for table_name in data_tables:
#         try:
#             df = pd.read_sql_query(f"SELECT * FROM {table_name}", conn)
#             df.to_excel(writer, sheet_name=table_name, index=False)
#             print(f"已导出数据表: {table_name}")
#         except Exception as e:
#             print(f"导出数据表 {table_name} 时出错: {e}")
#
#
# conn.close()
# print("数据导出完成!")

INVALID_SHEET_CHARS = r'[:\\/*?\[\]]'
MAX_SHEET_LEN = 31
def sanitize_sheet_name( name: str, used: set) -> str:
    # 删除非法字符，替换为空格，截断到 31 字符，确保唯一（添加数字后缀）
    s = re.sub(INVALID_SHEET_CHARS, " ", name)
    s = s.strip()
    if not s:
        s = "sheet"
    if len(s) > MAX_SHEET_LEN:
        s = s[:MAX_SHEET_LEN]
    base = s
    i = 1
    while s in used:
        suffix = f"_{i}"
        allowed_len =MAX_SHEET_LEN - len(suffix)
        s = (base[:allowed_len] + suffix) if len(base) > allowed_len else (base + suffix)
        i += 1
    used.add(s)
    return s
def convert_bytes_columns(df: pd.DataFrame) -> pd.DataFrame:
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
# 连接数据库
conn = sqlite3.connect('data.db')

# # 获取所有表名（排除系统表）
# cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
# all_tables = [row[0] for row in cursor.fetchall() if not row[0].startswith('sqlite_')]
#
# # 分离数据表和元数据表
# all_data_tables = [table for table in all_tables if not table.endswith('_meta')]
# meta_tables = [table for table in all_tables if table.endswith('_meta')]
#
# # 排除末尾数字为2-8的数据表
# data_tables = []
# for table in all_data_tables:
#     # 检查表名是否以数字2-8结尾
#     if re.search(r'[2-8]$', table):
#         print(f"跳过表: {table} (末尾数字为2-8)")
#         continue
#     data_tables.append(table)
def get_table_list() -> List[Tuple[str, str]]:
    # 返回 (name, type) 列表，排除 sqlite_ 开头的内部表

    cursor = conn.execute(
        "SELECT name, type FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' AND name NOT LIKE '%meta%' ORDER BY LENGTH(name) ASC"
    )
    return cursor.fetchall()
for name, typ in get_table_list():
    # 构造 sheet 名

    raw_sheet = name
    sheet_name = sanitize_sheet_name(raw_sheet, set())
    sheet_name_split = sheet_name.split("_")
    # 将数据库表英文名字转成中文名字
    module_name_str = sheet_name_split[0]
    cage_number_str = sheet_name_split[len(sheet_name_split) - 1]
    if cage_number_str.isdigit():
        cage_number = int(cage_number_str)
    else:
        cage_number = None
    sheet_name_CN = ""
    for modbus_type in Modbus_Slave_Type.Not_Each_Mouse_Cage.value + Modbus_Slave_Type.Each_Mouse_Cage.value + Modbus_Slave_Type.Calibrations.value + Modbus_Slave_Type.Epochs.value:
        if module_name_str == modbus_type.value['name']:
            sheet_name_CN += modbus_type.value['description']
            break

    sheet_name_CN += "监控数据"
    if cage_number is not None:
        sheet_name_CN += f"_通道{cage_number - 1}"
        pass

    # 读取 'xxx_meta' 表，它包含字段名称和中文描述
    meta_query = f"SELECT item_name, description FROM {name}_meta"
    meta_df = pd.read_sql_query(meta_query,conn)
    # 创建列名到中文描述的映射字典
    col_mapping = dict(zip(meta_df['item_name'], meta_df['description']))

    sql = f"SELECT * FROM {name}"
    logger.info(f"[INFO] 从 db 的 {typ} {name} 导出到 sheet '{sheet_name}|{sheet_name_CN}' ...")
    chunksize = (5000 or None)
    with pd.ExcelWriter('data_export_7.xlsx', engine="openpyxl") as writer:
        if chunksize and chunksize > 0:
            startrow = 0
            # 使用 pandas.read_sql_query 的 chunksize 返回迭代器
            for i, chunk in enumerate(pd.read_sql_query(sql, conn, chunksize=chunksize)):
                df = convert_bytes_columns(chunk)
                # 替换列名
                df.rename(columns=col_mapping, inplace=True)
                # header 仅写入第一块
                header = (startrow == 0)
                df.to_excel(writer, sheet_name=sheet_name_CN, index=False, startrow=startrow, header=header)
                startrow += len(df)
        else:
            df = pd.read_sql_query(sql, conn, )
            df =convert_bytes_columns(df)
            # 替换列名
            df.rename(columns=col_mapping, inplace=True)
            df.to_excel(writer, sheet_name=sheet_name_CN, index=False)
conn.close()