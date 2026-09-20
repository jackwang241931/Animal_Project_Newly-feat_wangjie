import os
import time
from datetime import datetime
from typing import Dict, Any, List

from loguru import logger


from public.config_class.global_setting import global_setting
from public.dao.SQLite.SQliteManager import SQLiteManager
from public.entity.experiment_setting_entity import Experiment_setting_entity
from public.function.DataCaculation import Data_Caculation
from public.function.DataCaculation.Data_Caculation import DataCaculation
from public.function.Modbus.Modbus_Type import Modbus_Slave_Type
# 监控数据操作类
from public.util.time_util import time_util
#logger = logger.bind(category="deep_camera_logger")

class Monitor_Datas_Handle():
    def __init__(self,db_name=None):
        # 实验设置
        self.experiment_setting: Experiment_setting_entity = global_setting.get_setting("experiment_setting", None)
        self.experiment_setting_file = global_setting.get_setting("experiment_setting_file", None)
        self.sqlite_manager: SQLiteManager = None
        self.init_construct(db_name)

    def init_construct(self,db_name):
        if db_name is None:
            self.db_name = self.create_db_not_time()
        else:
            self.db_name = db_name
        if self.sqlite_manager is not None:
            self.stop()
        self.sqlite_manager = SQLiteManager(db_name=self.db_name)
        self.create_tables()
        pass

    def stop(self):
        self.sqlite_manager.close()
    def create_db_not_time(self):
        """创建数据库 不按时间分库，直接一个实验一个库"""
        # 获取实验配置文件名称
        file_name_without_extension = ""
        experiment_setting_file = global_setting.get_setting("experiment_setting_file", None)
        if experiment_setting_file is not None and os.path.exists(experiment_setting_file):
            # 获取文件名称
            file_name = os.path.basename(experiment_setting_file)
            # 不带扩展名的文件名称
            file_name_without_extension = os.path.splitext(file_name)[0]
        # 定义文件夹路径
        folder_path = os.getcwd() + global_setting.get_setting('monitor_data')['STORAGE']['fold_path'] + os.path.join(

            global_setting.get_setting('monitor_data')['STORAGE']['sub_fold_path'],
            f"{file_name_without_extension}_{time_util.get_format_file_from_time(global_setting.get_setting('start_experiment_time', time.time()))}",
            f'data'
        )

        # 创建文件夹（如果不存在）
        os.makedirs(folder_path, exist_ok=True)
        db_name = f"data.db"
        db_file_path = os.path.join(folder_path, db_name)
        return db_file_path
    def create_db(self):
        """
        创建数据库
        :return:
        """
        year, month, day, hour, minute, second = time_util.get_current_times_info()
        # 获取实验配置文件名称
        file_name_without_extension =""
        experiment_setting_file = global_setting.get_setting("experiment_setting_file", None)
        if experiment_setting_file is not None and os.path.exists(experiment_setting_file):
            # 获取文件名称
            file_name = os.path.basename(experiment_setting_file)
            # 不带扩展名的文件名称
            file_name_without_extension = os.path.splitext(file_name)[0]
        # 定义文件夹路径
        folder_path =os.getcwd()+ global_setting.get_setting('monitor_data')['STORAGE']['fold_path'] + os.path.join(

            global_setting.get_setting('monitor_data')['STORAGE']['sub_fold_path'], f"{file_name_without_extension}_{time_util.get_format_file_from_time(global_setting.get_setting('start_experiment_time',time.time()))}",f'{year}', f'{month}')

        # 创建文件夹（如果不存在）
        os.makedirs(folder_path, exist_ok=True)
        db_name = f"{day}.db"
        db_file_path = os.path.join(folder_path, db_name)
        return db_file_path

    def create_tables(self):
        # 实例化每轮次数据表
        for data_type in Modbus_Slave_Type.Epochs.value:
            for table_name_short in data_type.value['table']:
                # 列
                columns = {item[0]: item[2] for item in data_type.value['table'][table_name_short]['column']}
                # 表名称
                table_name = f"{data_type.value['name']}_{table_name_short}"
                # 创建表
                if not self.sqlite_manager.is_exist_table(table_name):
                    self.sqlite_manager.create_table(table_name,
                                                     columns)
                    # logger.info(f"数据库{self.db_name}创建数据表{table_name}成功！")
                # 创建该表描述的表
                table_meta_name = f"{table_name}_meta"
                # 不存在则创建和插入
                if not self.sqlite_manager.is_exist_table(table_meta_name):
                    self.sqlite_manager.create_meta_table(table_meta_name)
                    # logger.info(f"数据库{self.db_name}创建表结构描述数据表{table_meta_name}成功！")
                    # 插入描述信息
                    for item in data_type.value['table'][table_name_short]['column']:
                        self.sqlite_manager.insert(table_meta_name, item_name=item[0], item_struct=item[2],
                                                   description=item[1])
        # 实例化其他数据项的数据表
        for data_type in Modbus_Slave_Type.Calibrations.value:
            for table_name_short in data_type.value['table']:
                # 列
                columns = {item[0]: item[2] for item in data_type.value['table'][table_name_short]['column']}
                # 表名称
                table_name = f"{data_type.value['name']}_{table_name_short}"
                # 创建表
                if not self.sqlite_manager.is_exist_table(table_name):
                    self.sqlite_manager.create_table(table_name,
                                                     columns)
                    # logger.info(f"数据库{self.db_name}创建数据表{table_name}成功！")
                # 创建该表描述的表
                table_meta_name = f"{table_name}_meta"
                # 不存在则创建和插入
                if not self.sqlite_manager.is_exist_table(table_meta_name):
                    self.sqlite_manager.create_meta_table(table_meta_name)
                    # logger.info(f"数据库{self.db_name}创建表结构描述数据表{table_meta_name}成功！")
                    # 插入描述信息
                    for item in data_type.value['table'][table_name_short]['column']:
                        self.sqlite_manager.insert(table_meta_name, item_name=item[0], item_struct=item[2],
                                                   description=item[1])
        # 实例化公共传感器数据的数据表
        for data_type in Modbus_Slave_Type.Not_Each_Mouse_Cage.value:
            for table_name_short in data_type.value['table']:
                # 列
                columns = {item[0]: item[2] for item in data_type.value['table'][table_name_short]['column']}
                # 表名称
                table_name = f"{data_type.value['name']}_{table_name_short}"
                # 创建表
                if not self.sqlite_manager.is_exist_table(table_name):
                    self.sqlite_manager.create_table(table_name,
                                                     columns)
                    # logger.info(f"数据库{self.db_name}创建数据表{table_name}成功！")
                # 创建该表描述的表
                table_meta_name = f"{table_name}_meta"
                # 不存在则创建和插入
                if not self.sqlite_manager.is_exist_table(table_meta_name):
                    self.sqlite_manager.create_meta_table(table_meta_name)
                    # logger.info(f"数据库{self.db_name}创建表结构描述数据表{table_meta_name}成功！")
                    # 插入描述信息
                    for item in data_type.value['table'][table_name_short]['column']:
                        self.sqlite_manager.insert(table_meta_name, item_name=item[0], item_struct=item[2],
                                                   description=item[1])
            pass
        # 实例化每个笼子里的传感器的数据表
        if self.experiment_setting is not None:
            for data_type in Modbus_Slave_Type.Each_Mouse_Cage.value:
                for carge_number in range(1, len(self.experiment_setting.groups) + 1 ):
                    for table_name_short in data_type.value['table']:
                        # 列
                        columns = {item[0]: item[2] for item in data_type.value['table'][table_name_short]['column']}
                        # 表名称
                        table_name = f"{data_type.value['name']}_{table_name_short}_cage_{carge_number}"
                        # 创建表
                        if not self.sqlite_manager.is_exist_table(table_name):
                            self.sqlite_manager.create_table(table_name,
                                                             columns)
                            # logger.info(f"数据库{self.db_name}创建数据表{table_name}成功！")
                        # 创建该表描述的表
                        table_meta_name = f"{table_name}_meta"
                        # 不存在则创建和插入
                        if not self.sqlite_manager.is_exist_table(table_meta_name):
                            # logger.info(f"数据库{self.db_name}创建表结构描述数据表{table_meta_name}成功！")
                            self.sqlite_manager.create_meta_table(table_meta_name)
                            # 插入描述信息
                            for item in data_type.value['table'][table_name_short]['column']:
                                self.sqlite_manager.insert(table_meta_name, item_name=item[0], item_struct=item[2],
                                                           description=item[1])
        pass

    def insert_data(self, data):
        """

        :param data:
        :return: success ：是否成功, error 错误信息
        """
        # 添加数据到表里
        if data is not None:

            # 公共传感器：
            if data['mouse_cage_number'] == 0:
                # 获取该表名称
                table_name = f"{data['module_name']}_{data['table_name']}"
                # # 获取该表的column项
                table_name_meta = f"{table_name}_meta"
                columns_query = self.sqlite_manager.query(table_name_meta)

                # 如果数据空 或者为UFC的 空
                if len(data['data']) ==0 or (len(data['data']) == 1 and data['data'][0]['desc']=='鼠笼号'):
                    columns = [i[0] for i in columns_query if i[0] != 'id' and i[0] != 'time']
                    if columns:
                        # 因为UFC也分鼠笼 所以单独处理
                        if "UFC" in table_name and len(data['data']) == 1:
                            # 去除首列mouse_cage_num
                            columns.pop(0)
                            #data['data'][0]['value']为mouse_cage_num
                            datas = [None, data['data'][0]['value']]
                            for i in range(len(columns)):
                                datas.append(None)
                            datas.append(data['time'])
                            result = self.sqlite_manager.insert_not_columns(table_name, datas)
                            if result == 1:
                                logger.info(f"数据插入表{table_name}成功！")
                                return True,None
                            else:
                                logger.info(f"数据插入表{table_name}失败！")
                                return False,f"数据插入表{table_name}失败！"
                            pass
                        else:
                            datas = [None]
                            for i in range(len(columns)):
                                datas.append(None)

                            datas.append(data['time'])
                            result = self.sqlite_manager.insert_not_columns(table_name, datas)
                            if result == 1:
                                logger.info(f"数据插入表{table_name}成功！")
                                return True,None
                            else:
                                logger.info(f"数据插入表{table_name}失败！")
                                return False,f"数据插入表{table_name}失败！"
                            pass

                            pass
                    else:
                        logger.info(f"数据插入表{table_name}失败！列为空")
                        return False,f"数据插入表{table_name}失败！列为空"

                else:
                    columns = [i[0] for i in columns_query if i[0] != 'id']
                    datas = [i['value'] for i in data['data']]

                    datas.append(data['time'])
                    # logger.error(f"{columns} | {datas}")
                    result = self.sqlite_manager.insert_2(table_name, columns, datas)
                    if result == 1:
                        logger.info(f"数据插入表{table_name}成功！")
                        return True,None
                    else:
                        logger.info(f"数据插入表{table_name}失败！")
                        return False,f"数据插入表{table_name}失败！"
            else:  # 每个鼠笼传感器：
                # 获取该表名称
                table_name = f"{data['module_name']}_{data['table_name']}_cage_{data['mouse_cage_number']}"
                # # 获取该表的column项
                table_name_meta = f"{table_name}_meta"
                columns_query = self.sqlite_manager.query(table_name_meta)
                # 如果数据非空
                if len(data['data']) != 0:
                    columns = [i[0] for i in columns_query if i[0] != 'id']
                    datas = [i['value'] for i in data['data']]

                    datas.append(data['time'])
                    result = self.sqlite_manager.insert_2(table_name, columns, datas)
                    if result == 1:
                        logger.info(f"数据插入表{table_name}成功！")
                        return True,None
                    else:
                        logger.info(f"数据插入表{table_name}失败！")
                        return False,f"数据插入表{table_name}失败！"
                    pass
                else:
                    columns = [i[0] for i in columns_query if i[0] != 'id' and i[0] != 'time']
                    datas = [None]
                    for i in range(len(columns)):
                        datas.append(None)

                    datas.append(data['time'])
                    result = self.sqlite_manager.insert_not_columns(table_name, datas)
                    if result == 1:
                        logger.info(f"数据插入表{table_name}成功！")
                        return True,None
                    else:
                        logger.info(f"数据插入表{table_name}失败！")
                        return False,f"数据插入表{table_name}失败！"
                    pass
                    pass
        else:
            return False
            pass

    def query_data(self, table_name):
        """
        获取数据库的数据表的单行最新的数据
        :param table_name:
        :return:
        """
        results_query = self.sqlite_manager.query_current_Data(table_name)
        columns_query = self.sqlite_manager.query(f"{table_name}_meta")
        return_data = []
        results = []
        columns = []
        if results_query is not None and len(results_query) > 0:
            # 不要id 和time
            results = list(results_query[0])[1:-1]
        if columns_query is not None and len(columns_query) > 0:
            # 不要id 和time
            columns = [i[2] for i in columns_query][1:-1]
        for value, description in zip(results, columns):
            return_data.append({'desc': description, 'value': value})
        return return_data
        pass
    def query_data_all(self, table_name):
        """
        获取数据库的数据表的所有的数据
        :param table_name:
        :return:
        """
        results_query = self.sqlite_manager.query(table_name)

        results = []

        if results_query is not None and len(results_query) > 0:
            results = results_query
        return results
        pass
    def query_data_counts(self, table_name):
        """
        获取数据库的数据表的所有的数据的数量
        :param table_name:
        :return:
        """
        results_query = self.sqlite_manager.query_counts_conditions(table_name)

        results = 0

        if results_query is not None:
            results = results_query
        return results
        pass
    def query_data_paging(self, table_name,rows_per_page,start_row):
        """
        获取数据库的数据表的分页的数据
        :param table_name:
        :param rows_per_page:每一页几行
        :param start_row:第几行开始
        :return:
        """
        results_query = self.sqlite_manager.query_paging(table_name,rows_per_page,start_row)

        results = []

        if results_query is not None and len(results_query) > 0:
            results = results_query
        return results
        pass
    def query_data_one_column_current(self, table_name, columns_flag):
        """
        获取指定列的单个最新的数据
        :param table_name:
        :param columns_flag:
        :return:
        """
        if len(columns_flag) > 0:
            conditions = "  where "
        else:
            conditions = ""
        for columns_signle in columns_flag:
            if columns_signle == columns_flag[-1]:
                conditions += f" item_name = '{columns_signle}' "
            else:
                conditions += f" item_name = '{columns_signle}' or "
        columns_query = self.sqlite_manager.query_conditions(f"{table_name}_meta", conditions)

        results_query = self.sqlite_manager.query_current_Data_columns(table_name, [i[0] for i in columns_query])
        return_data = {}
        results = []
        columns_name = []
        columns_desc = []
        if results_query is not None and len(results_query) > 0:
            results = list(results_query[0])
        if columns_query is not None and len(columns_query) > 0:
            columns_name = [i[0] for i in columns_query]
            columns_desc = [i[2] for i in columns_query]
            pass
        for value, column_name, column_desc in zip(results, columns_name, columns_desc):
            return_data[column_name] = {'desc': column_desc, 'value': value}
        """\
        return_data
        {
            'temperature':{
            'desc':'温度',
            'value':1
            },
            .....
        }
        """
        return [return_data]

    def query_meta_table_data(self, table_name):
        """
        获取表结构数据 item_desc
        :param table_name:
        :return:
        """
        columns_query = self.sqlite_manager.query(f"{table_name}_meta")
        columns_desc = []
        if columns_query is not None and len(columns_query) > 0:
            columns_desc = [{'desc':i[2],'name':i[0]} for i in columns_query][1:-1]
        return columns_desc

    def query_meta_table_data_all(self, table_name):
        """
        获取表结构数据 item_desc
        :param table_name:
        :return:
        """
        columns_query = self.sqlite_manager.query(f"{table_name}_meta")
        columns_desc = []
        if columns_query is not None and len(columns_query) > 0:
            columns_desc = [{'desc': i[2], 'name': i[0]} for i in columns_query]
        return columns_desc

    def query_monitor_data_all_tables_paging(self, page: int = 1,
            page_size: int = 100,all_column_datas=[])-> dict:
        """
        :param page 第几页
        :param page_size 每页多少
        :param all_column_datas 用户选择的列数据
        :return:把传入的表按 time 字段联立并分页返回结果，返回字典包含:
          - total_items: 总行数（time 的并集大小）
          - total_pages
          - page (实际返回页，1-based)
          - page_size
          - columns: 列名列表（与 rows 中 dict 的 key 对应）
          - rows: 列表，每行为 dict（key=列名, value=值）

        从 SQLite 数据库中找出所有表名（排除名称中包含 "meta" 的表，大小写不敏感）；
        仅保留包含 time 字段的表；
        将这些表按 time 联立（以 time 的并集为主），把各表的其它字段并列（别名为 表名__列名，避免重名）；
        支持分页（page 1-based，page_size），并返回总条数、总页数、当前页数据等分页信息。
        注意事项：

            SQLite 没有 FULL OUTER JOIN，所以用 UNION 把各表的 time 合并成一个派生表 all_times，然后 LEFT JOIN 每个表；
            为防止 SQL 注入与标识符冲突，对表名与列名使用双引号转义（quote_ident）；
            统计总条数时使用 SELECT COUNT(*) FROM ( ... UNION ... )；
            LIMIT / OFFSET 用于分页（page 从 1 开始）。如果数据量很大，COUNT 与 UNION 可能较慢，建议为 time 列建索引或在后端分片

        """
        if len(all_column_datas) == 0:
            return {}
        tables = self.sqlite_manager.get_tables_with_time(exclude_substr=["meta","Epoch_data"],columns=['time'])
        result = self.sqlite_manager.query_joined_by_time( tables, page=page, page_size=page_size, order_asc=True)

        result_title = ["时间"]

        # 找到中文列名
        for columns in result["columns"][1:]:
            columns_split = columns.split('__')
            table_name = columns_split[0]
            column_name = columns_split[1]
            columns_query =self.sqlite_manager.query_conditions(table_name=f"{table_name}_meta", conditions=f" where item_name='{column_name}'")
            print(columns_query,table_name,column_name)
            result_title.append(columns_query[0][2])
        result["columns_title"]=result_title
        # print("参与联立的表:", tables)
        # print(
        #     f"总条数: {result['total_items']}, 总页数: {result['total_pages']}, 当前页: {result['page']}, 每页: {result['page_size']}")
        # print("列:", result["columns"])
        # print("示例行（最多 10 行）:")
        # for i, row in enumerate(result["rows"][:10]):
        #     print(i + 1, row)

        if len(result) == 0:
            return {}
        # caculation_handle = DataCaculation(sqlite_manager = self.sqlite_manager)
        #
        # return_results = caculation_handle.caculate_data(columns=all_column_datas,datas=result)


        return result
        pass
    def query_epoch_data_all_tables_paging(self, page: int = 1,
            page_size: int = 100,all_column_datas=[])-> dict:
        """
        :param page 第几页
        :param page_size 每页多少
        :param all_column_datas 用户选择的列数据
        :return:把传入的表按 time 字段联立并分页返回结果，返回字典包含:
          - total_items: 总行数（time 的并集大小）
          - total_pages
          - page (实际返回页，1-based)
          - page_size
          - columns: 列名列表（与 rows 中 dict 的 key 对应）
          - rows: 列表，每行为 dict（key=列名, value=值）

        从 SQLite 数据库中找出epoch_Data；

        支持分页（page 1-based，page_size），并返回总条数、总页数、当前页数据等分页信息。
        注意事项：

            SQLite 没有 FULL OUTER JOIN，所以用 UNION 把各表的 time 合并成一个派生表 all_times，然后 LEFT JOIN 每个表；
            为防止 SQL 注入与标识符冲突，对表名与列名使用双引号转义（quote_ident）；
            统计总条数时使用 SELECT COUNT(*) FROM ( ... UNION ... )；
            LIMIT / OFFSET 用于分页（page 从 1 开始）。如果数据量很大，COUNT 与 UNION 可能较慢，建议为 time 列建索引或在后端分片

        """
        if len(all_column_datas) == 0:
            return {}
        result = self.sqlite_manager.query_Epoch_datas( "Epoch_data", page=page, page_size=page_size, order_asc=True )
        result_title = []

        # 找到中文列名
        for columns in result["columns"]:

            columns_query =self.sqlite_manager.query_conditions(table_name=f"Epoch_data_meta", conditions=f" where item_name='{columns}'")
            result_title.append(columns_query[0][2])
        result["columns_title"]=result_title
        # print("参与联立的表:", tables)
        # print(
        #     f"总条数: {result['total_items']}, 总页数: {result['total_pages']}, 当前页: {result['page']}, 每页: {result['page_size']}")
        # print("列:", result["columns"])
        # print("示例行（最多 10 行）:")
        # for i, row in enumerate(result["rows"][:10]):
        #     print(i + 1, row)
        # logger.critical(result)
        if len(result) == 0:
            return {}
        # caculation_handle = DataCaculation(sqlite_manager = self.sqlite_manager)
        #
        # return_results = caculation_handle.caculate_data(columns=all_column_datas,datas=result)

        return result

        pass
    def query_monitor_data_all_tables(self, all_column_datas=[]) -> dict:
        pass


    def query_data_in_line_with_epoch_data(self,start_time,end_time):
        tables = self.sqlite_manager.get_tables_with_time(exclude_substr=["meta","Epoch_data"], columns=['time'])
        # logger.critical(tables)
        # 执行查询
        results, columns = self.sqlite_manager.get_multi_table_data(tables,
            start_time, end_time, join_type="separate"
        )
        # logger.critical(results)
        # logger.critical(columns)
        return results, columns

        pass