###数据计算类
from loguru import logger


from public.dao.SQLite.SQliteManager import SQLiteManager


class DataCaculation:
    def __init__(self,sqlite_manager):
        self.sqlite_manager:SQLiteManager =sqlite_manager
        pass
    def stop(self):
        try:
            self.sqlite_manager.close()
        except Exception as e:
            logger.error(f"{e}")
    def caculate_data(self,columns:list = [],datas=[]):
        """
        计算数据
        :param columns:为用户选择的列
        :param datas: 为获取的统一time列的全表数据
        dict 把传入的表按 time 字段联立并分页返回结果，返回字典包含:
          - total_items: 总行数（time 的并集大小）
          - total_pages
          - page (实际返回页，1-based)
          - page_size
          - columns: 列名列表（与 rows 中 dict 的 key 对应）
          - rows: 列表，每行为 dict（key=列名, value=值）
        :return: [{表名:数据}....]
        """
        retur_datas = []
        if len(datas) == 0 or len(columns) == 0:
            return retur_datas

