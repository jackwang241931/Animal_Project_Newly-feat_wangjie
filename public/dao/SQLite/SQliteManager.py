import datetime
import math
import sqlite3
import threading
import time
from typing import List, Dict, Any, Optional, Generator
from contextlib import contextmanager

from loguru import logger


class SQLiteManager:
    TIME_COLUMN_NAME = 'time'

    def __init__(self, db_name: str, timeout: float = 30.0):
        """初始化数据库管理器，每个调用线程复用自己的 SQLite 连接。"""
        self.db_name = db_name
        self.timeout = timeout
        self._connections = {}
        self._active_connection_counts = {}
        self._connections_lock = threading.RLock()
        self._closed = False
        self._thread_state = threading.local()
        self._schema_cache = {}
        self._schema_cache_lock = threading.RLock()
        self._ensured_time_indexes = set()

    def _create_connection(self, isolation_level=None):
        conn = sqlite3.connect(
            self.db_name,
            timeout=self.timeout,
            check_same_thread=False,
            isolation_level=isolation_level
        )
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(f"PRAGMA busy_timeout={max(1000, int(self.timeout * 1000))}")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA temp_store=MEMORY")
        conn.execute("PRAGMA cache_size=-32768")
        return conn

    @contextmanager
    def get_connection(self,
                       row_factory: Optional[callable] = None,
                       isolation_level: Optional[str] = None) -> Generator[sqlite3.Connection, None, None]:
        """获取当前线程复用的数据库连接。"""
        thread_id = threading.get_ident()
        with self._connections_lock:
            if self._closed:
                raise RuntimeError("SQLiteManager is closed")
            conn = self._connections.get(thread_id)
            if conn is None:
                conn = self._create_connection(isolation_level=isolation_level)
                self._connections[thread_id] = conn
            self._active_connection_counts[thread_id] = (
                self._active_connection_counts.get(thread_id, 0) + 1
            )
        try:
            conn.row_factory = row_factory
            yield conn
        except Exception as e:
            if not getattr(self._thread_state, "in_batch_transaction", False):
                conn.rollback()
            logger.error(f"Database error: {e}")
            raise
        finally:
            connection_to_close = None
            with self._connections_lock:
                active_count = self._active_connection_counts.get(thread_id, 1) - 1
                if active_count <= 0:
                    self._active_connection_counts.pop(thread_id, None)
                    if self._closed:
                        connection_to_close = self._connections.pop(thread_id, None)
                else:
                    self._active_connection_counts[thread_id] = active_count
            if connection_to_close is not None:
                connection_to_close.close()

    @contextmanager
    def execute_transaction(self, auto_commit: bool = True) -> Generator[sqlite3.Cursor, None, None]:
        """执行事务的上下文管理器"""
        with self.get_connection() as conn:
            cursor = conn.cursor()
            try:
                yield cursor
                if auto_commit and not getattr(self._thread_state, "in_batch_transaction", False):
                    conn.commit()
            except Exception as e:
                if not getattr(self._thread_state, "in_batch_transaction", False):
                    conn.rollback()
                logger.error(f"Database error: {e}")
                raise
            finally:
                cursor.close()

    @contextmanager
    def batch_transaction(self):
        """将同一线程中的多次写操作合并为一个事务。"""
        if getattr(self._thread_state, "in_batch_transaction", False):
            yield
            return

        with self.get_connection() as conn:
            self._thread_state.in_batch_transaction = True
            try:
                conn.execute("BEGIN IMMEDIATE")
                yield
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                self._thread_state.in_batch_transaction = False

    def quote_ident(self, name: str) -> str:
        """用双引号安全引用 SQLite 标识符（表名或列名）。"""
        return '"' + name.replace('"', '""') + '"'

    def invalidate_schema_cache(self, table_name: str = None):
        with self._schema_cache_lock:
            if table_name is None:
                self._schema_cache.clear()
                self._ensured_time_indexes.clear()
            else:
                self._schema_cache.pop(table_name, None)
                self._ensured_time_indexes.discard(table_name)

    def get_table_info(self, table_name: str) -> List[tuple]:
        """返回并缓存 PRAGMA table_info 结果。"""
        with self._schema_cache_lock:
            cached = self._schema_cache.get(table_name)
            if cached is not None:
                return list(cached)

        with self.execute_transaction(auto_commit=False) as cursor:
            cursor.execute(f"PRAGMA table_info({self.quote_ident(table_name)})")
            info = cursor.fetchall()

        with self._schema_cache_lock:
            self._schema_cache[table_name] = tuple(info)
        return list(info)

    def ensure_time_index(self, table_name: str):
        """确保按时间分页的表使用复合索引，旧实验数据库也会自动补齐。"""
        with self._schema_cache_lock:
            if table_name in self._ensured_time_indexes:
                return

        column_names = {row[1] for row in self.get_table_info(table_name)}
        if self.TIME_COLUMN_NAME not in column_names:
            return

        index_name = f"idx_{table_name}_time_id"
        indexed_columns = [self.TIME_COLUMN_NAME]
        if "id" in column_names:
            indexed_columns.append("id")
        columns_sql = ", ".join(f"{self.quote_ident(name)} DESC" for name in indexed_columns)
        sql = (
            f"CREATE INDEX IF NOT EXISTS {self.quote_ident(index_name)} "
            f"ON {self.quote_ident(table_name)} ({columns_sql})"
        )
        with self.execute_transaction() as cursor:
            cursor.execute(sql)

        with self._schema_cache_lock:
            self._ensured_time_indexes.add(table_name)
    def get_tables_with_time_sql_results(self,select_column_name:list=None, exclude_substr: list = None, columns: list = None):
        """
                返回数据库中不包含 exclude_substr（不区分大小写）的真实数据表。

                Args:
                    select_column_name:要查找该表的字段默认为['name']
                    exclude_substr: 要排除的子字符串列表，默认为["meta", "Epoch_data"]
                    columns: 必须包含的列名列表，默认为['time']

                Returns:
                    符合条件的表名的数据库查询结果
                """
        # 设置默认值
        if select_column_name is None:
            select_column_name = ['name']
        if exclude_substr is None:
            exclude_substr = ["meta", "Epoch_data"]
        if columns is None:
            columns = ['time']

        with self.execute_transaction(auto_commit=True) as cursor:
            allowed_columns = {"name", "type", "tbl_name", "rootpage", "sql"}
            if any(column not in allowed_columns for column in select_column_name):
                raise ValueError("Unsupported sqlite_master column")
            select_column_name_sql = " , ".join(select_column_name)
            conditions = ["type = 'table'"]
            params = []
            if exclude_substr:
                for substr in exclude_substr:
                    conditions.append("lower(name) NOT LIKE ?")
                    params.append(f"%{substr.lower()}%")

            query = (
                f"SELECT {select_column_name_sql} FROM sqlite_master "
                f"WHERE {' AND '.join(conditions)}"
            )
            cursor.execute(query, params)
            rows = cursor.fetchall()
            return rows
    def get_tables_with_time(self,select_column_name:list=None, exclude_substr: list = None, columns: list = None) -> List[str]:
        """
        返回数据库中不包含 exclude_substr（不区分大小写）的表名，并且该表具有 columns列。

        Args:
            select_column_name:要查找该表的字段默认为['name']
            exclude_substr: 要排除的子字符串列表，默认为["meta", "Epoch_data"]
            columns: 必须包含的列名列表，默认为['time']

        Returns:
            符合条件的表名列表
        """
        # 设置默认值
        if select_column_name is None:
            select_column_name = ['name']
        if exclude_substr is None:
            exclude_substr = ["meta", "Epoch_data"]
        if columns is None:
            columns = ['time']
        rows = self.get_tables_with_time_sql_results(select_column_name=select_column_name,exclude_substr=exclude_substr,columns=columns)
        with self.execute_transaction(auto_commit=True) as cursor:
            tables = [r[0] for r in rows]

            good = []
            for table_name in tables:
                try:
                    q = self.quote_ident(table_name)
                    cursor.execute(f"PRAGMA table_info({q})")
                    table_info = cursor.fetchall()
                    cols = [r[1] for r in table_info]

                    # 检查是否包含所有required columns（不区分大小写）
                    cols_lower = [col.lower() for col in cols]
                    if all([column.lower() in cols_lower for column in columns]):
                        good.append(table_name)

                except Exception as e:
                    # 记录错误但继续处理其他表
                    print(f"检查表 {table_name} 时出错: {e}")
                    continue

            return good

    def get_latest_time_across_tables(self, table_names: List[str], start_time: Optional[float] = None,
                                      end_time: Optional[float] = None) -> Optional[float]:
        """获取多个表在指定时间范围内的最新时间戳。"""
        if not table_names:
            return None

        start_time_f = datetime.datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] if start_time is not None else None
        end_time_f = datetime.datetime.fromtimestamp(end_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3] if end_time is not None else None
        latest_time_text = None

        valid_tables = []
        for table_name in table_names:
            try:
                columns = {row[1] for row in self.get_table_info(table_name)}
                if self.TIME_COLUMN_NAME not in columns:
                    continue
                self.ensure_time_index(table_name)
                valid_tables.append(table_name)
            except sqlite3.OperationalError as e:
                logger.error(f"检查表 {table_name} 时出错: {e}")

        with self.execute_transaction(auto_commit=True) as cursor:
            for table_name in valid_tables:
                try:
                    q = self.quote_ident(table_name)
                    where_clauses = []
                    params = []
                    if start_time_f is not None:
                        where_clauses.append(f"{self.quote_ident(self.TIME_COLUMN_NAME)} >= ?")
                        params.append(start_time_f)
                    if end_time_f is not None:
                        where_clauses.append(f"{self.quote_ident(self.TIME_COLUMN_NAME)} <= ?")
                        params.append(end_time_f)

                    query = f"SELECT MAX({self.quote_ident(self.TIME_COLUMN_NAME)}) FROM {q}"
                    if where_clauses:
                        query += " WHERE " + " AND ".join(where_clauses)

                    cursor.execute(query, params)
                    row = cursor.fetchone()
                    table_latest_time = row[0] if row and row[0] else None
                    if table_latest_time and (latest_time_text is None or table_latest_time > latest_time_text):
                        latest_time_text = table_latest_time
                except sqlite3.OperationalError as e:
                    logger.error(f"查询表 {table_name} 的最新时间失败: {e}")
                    continue

        if latest_time_text is None:
            return None

        try:
            return datetime.datetime.fromisoformat(latest_time_text).timestamp()
        except ValueError:
            try:
                return datetime.datetime.strptime(latest_time_text, '%Y-%m-%d %H:%M:%S').timestamp()
            except ValueError:
                logger.error(f"无法解析最新时间文本: {latest_time_text}")
                return None

    def build_all_times_sql(self, tables: List[str]) -> str:
        """构造用于 all_times 的子查询 SQL（UNION 去重）。"""
        selects = [f"SELECT time FROM {self.quote_ident(t)}" for t in tables]
        return " UNION ".join(selects)

    def count_all_times(self, all_times_sql: str) -> int:
        """统计 all_times 的行数（即所有表 time 的并集大小）。"""
        count_sql = f"SELECT COUNT(*) FROM ({all_times_sql}) AS _all_times_count"
        with self.execute_transaction(auto_commit=True) as cursor:
            cursor.execute(count_sql)
            return cursor.fetchone()[0] or 0

    def query_counts_conditions(self, table_name: str, conditions: str = "") -> int:
        """查询数据条数"""
        sql = f"""SELECT COUNT(*) FROM "{table_name}" """
        sql += conditions
        with self.execute_transaction(auto_commit=True) as cursor:
            cursor.execute(sql)
            return cursor.fetchone()[0]

    def query_Epoch_datas(
            self,
            table: str,
            page: int = 1,
            page_size: int = 100,
            order_asc: bool = True,
            columns: Optional[List[str]] = None
    ) -> Dict[str, Any]:
        """查询 Epoch 数据分页"""
        query_started_at = time.perf_counter()
        if page_size <= 0:
            raise ValueError("page_size must be > 0")
        if not table:
            return {
                "total_items": 0,
                "total_pages": 0,
                "page": 1,
                "page_size": page_size,
                "columns": [],
                "rows": []
            }

        self.ensure_time_index(table)
        table_info = self.get_table_info(table)
        available_columns = [row[1] for row in table_info]
        if columns:
            requested = set(columns)
            selected_columns = [name for name in available_columns if name in requested]
        else:
            selected_columns = available_columns
        if not selected_columns:
            selected_columns = available_columns

        selected_sql = ", ".join(self.quote_ident(name) for name in selected_columns)
        order = "DESC" if order_asc else "ASC"
        secondary_order = f", {self.quote_ident('id')} {order}" if "id" in available_columns else ""
        final_sql = f"""
           SELECT {selected_sql}
           FROM {self.quote_ident(table)}
           ORDER BY {self.quote_ident(self.TIME_COLUMN_NAME)} {order}{secondary_order}
           LIMIT ? OFFSET ?
        """

        with self.execute_transaction(auto_commit=False) as cursor:
            cursor.execute(f"SELECT COUNT(*) FROM {self.quote_ident(table)}")
            total_items = cursor.fetchone()[0] or 0
            total_pages = max(1, math.ceil(total_items / page_size)) if total_items > 0 else 0
            page = 1 if total_pages == 0 else max(1, min(page, total_pages))
            offset = (page - 1) * page_size
            cursor.execute(final_sql, (page_size, offset))
            rows = cursor.fetchall()
            colnames = [desc[0] for desc in cursor.description]

        result_rows = [dict(zip(colnames, r)) for r in rows]

        result = {
            "total_items": total_items,
            "total_pages": total_pages,
            "page": page,
            "page_size": page_size,
            "columns": colnames,
            "rows": result_rows
        }
        elapsed_ms = (time.perf_counter() - query_started_at) * 1000
        if elapsed_ms >= 200:
            logger.warning(
                f"Slow Epoch query: table={table}, page={page}, "
                f"page_size={page_size}, rows={len(result_rows)}, elapsed_ms={elapsed_ms:.1f}"
            )
        return result

    def query_joined_by_time(self, tables: List[str], page: int = 1, page_size: int = 100, order_asc: bool = True) -> \
    Dict[str, Any]:
        """把传入的表按 time 字段联立并分页返回结果"""
        if page_size <= 0:
            raise ValueError("page_size must be > 0")
        if not tables:
            return {
                "total_items": 0,
                "total_pages": 0,
                "page": 1,
                "page_size": page_size,
                "columns": [],
                "rows": []
            }

        table_columns = {}
        for table_name in tables:
            self.ensure_time_index(table_name)
            table_columns[table_name] = [
                row[1] for row in self.get_table_info(table_name)
            ]

        all_times_sql = self.build_all_times_sql(tables)
        total_items = self.count_all_times(all_times_sql)
        total_pages = max(1, math.ceil(total_items / page_size)) if total_items > 0 else 0

        if total_pages == 0:
            page = 1
        else:
            page = max(1, min(page, total_pages))

        offset = (page - 1) * page_size

        with self.execute_transaction(auto_commit=True) as cursor:
            # 构造 SELECT 列与 JOIN 子句
            select_cols = [f"all_times.time AS {self.quote_ident('time')}"]
            join_clauses = []

            for t in tables:
                q_t = self.quote_ident(t)
                col_names = table_columns[t]

                for col in col_names:
                    if col == "time":
                        continue
                    alias = f"{t}__{col}"
                    select_cols.append(f"{q_t}.{self.quote_ident(col)} AS {self.quote_ident(alias)}")

                join_clauses.append(f"LEFT JOIN {q_t} ON {q_t}.time = all_times.time")

            select_clause = ",\n  ".join(select_cols)
            join_clause = "\n  ".join(join_clauses)
            order = "DESC" if order_asc else "ASC"

            final_sql = f"""
            SELECT
              {select_clause}
            FROM
              ({all_times_sql}) AS all_times
              {join_clause}
            ORDER BY all_times.time {order}
            LIMIT ? OFFSET ?
            """

            cursor.execute(final_sql, (page_size, offset))
            rows = cursor.fetchall()
            colnames = [desc[0] for desc in cursor.description]

        result_rows = [dict(zip(colnames, r)) for r in rows]

        return {
            "total_items": total_items,
            "total_pages": total_pages,
            "page": page,
            "page_size": page_size,
            "columns": colnames,
            "rows": result_rows
        }

    def convert_to_foreign_key_sql(self, foreign_key_dict: dict) -> str:
        """将 foreign_key_dict 转换成 SQL 外键约束语句"""
        foreign_keys = []
        keys = foreign_key_dict["key"]
        reference_keys = foreign_key_dict["reference_key"]

        if len(keys) != len(reference_keys):
            return ""

        for key, reference in zip(keys, reference_keys):
            table_ref, column_ref = reference.split('(')
            column_ref = column_ref.strip(') ')
            foreign_keys.append(f"FOREIGN KEY ({key}) REFERENCES {table_ref.strip()}({column_ref})")

        return ",\n".join(foreign_keys)

    def get_multi_table_data(self, table_names: List[str], start_time: float, end_time: float,
                             join_type: str = "union", start_exclusive: bool = False,
                             selected_columns: Optional[Dict[str, List[str]]] = None):
        """从多个SQLite表中获取指定时间范围的数据"""
        try:
            valid_tables = []
            table_columns = {}

            with self.execute_transaction(auto_commit=True) as cursor:
                for table in table_names:
                    try:
                        # 检查表是否存在
                        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
                        if not cursor.fetchone():
                            continue

                        # 获取表的列信息
                        cursor.execute(f"PRAGMA table_info({self.quote_ident(table)})")
                        columns_info = cursor.fetchall()
                        columns = [col[1] for col in columns_info]

                        if self.TIME_COLUMN_NAME not in columns:
                            continue

                        if selected_columns is None:
                            other_columns = [col for col in columns if col not in ['id', 'time']]
                        else:
                            requested_columns = selected_columns.get(table, [])
                            other_columns = [
                                col for col in requested_columns
                                if col in columns and col not in ['id', 'time']
                            ]

                        if other_columns:
                            valid_tables.append(table)
                            table_columns[table] = other_columns

                    except sqlite3.OperationalError as e:
                        logger.error(f"检查表 {table} 时出错: {e}")
                        continue

            if not valid_tables:
                return [], []

            for table in valid_tables:
                self.ensure_time_index(table)

            if join_type.lower() == "union":
                return self._union_query(valid_tables, table_columns, start_time, end_time, start_exclusive=start_exclusive)
            elif join_type.lower() == "separate":
                return self._separate_queries(valid_tables, table_columns, start_time, end_time, start_exclusive=start_exclusive)
            else:
                return self._join_query(valid_tables, table_columns, start_time, end_time, start_exclusive=start_exclusive)

        except Exception as e:
            logger.error(f"查询过程中出现错误: {e}")
            return [], []

    def _separate_queries(self, tables: List[str], table_columns: Dict[str, List[str]], start_time: float,
                          end_time: float, start_exclusive: bool = False):
        """分别查询每个表，返回字典格式的结果"""
        results_dict = {}
        all_columns = ['time']
        start_time_f = datetime.datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        end_time_f = datetime.datetime.fromtimestamp(end_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

        with self.execute_transaction(auto_commit=True) as cursor:
            for table in tables:
                table_cols = table_columns[table]
                quoted_time = self.quote_ident(self.TIME_COLUMN_NAME)
                quoted_table = self.quote_ident(table)
                time_condition = (
                    f"{quoted_time} > ? AND {quoted_time} <= ?"
                    if start_exclusive
                    else f"{quoted_time} BETWEEN ? AND ?"
                )
                column_selects = [quoted_time] + [
                    f"{self.quote_ident(col)} AS {self.quote_ident(f'{table}__{col}')}"
                    for col in table_cols
                ]
                query = f"""
                SELECT {', '.join(column_selects)}
                FROM {quoted_table}
                WHERE {time_condition}
                ORDER BY {quoted_time}
                """

                cursor.execute(query, (start_time_f, end_time_f))
                table_results = cursor.fetchall()
                table_column_names = [desc[0] for desc in cursor.description]

                if len(table_results) != 0 or table == 'ZeroCalibration_data' or table == 'SpanCalibration_data':
                    results_dict[table] = {
                        'data': table_results,
                        'columns': table_column_names
                    }

                    for col in table_column_names:
                        if col not in all_columns:
                            all_columns.append(col)

        merged_results = self.process_data_to_dict(results_dict)
        all_columns.pop(0)
        return merged_results, all_columns

    def _union_query(self, tables: List[str], table_columns: Dict[str, List[str]], start_time: float, end_time: float,
                     start_exclusive: bool = False):
        """使用UNION ALL合并多个表的数据"""
        select_parts = []
        start_time_f = datetime.datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        end_time_f = datetime.datetime.fromtimestamp(end_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        time_condition = "time > ? AND time <= ?" if start_exclusive else "time BETWEEN ? AND ?"

        for table in tables:
            columns = table_columns[table]
            column_selects = [f"{col} AS {table}__{col}" for col in columns]

            select_part = f"""
            SELECT 
                '{table}' AS source_table,
                time,
                {', '.join(column_selects)}
            FROM {table}
            WHERE {time_condition}
            """
            select_parts.append(select_part)

        final_query = " UNION ALL ".join(select_parts) + " ORDER BY time"
        params = []
        for _ in tables:
            params.extend([start_time_f, end_time_f])

        with self.execute_transaction(auto_commit=True) as cursor:
            cursor.execute(final_query, params)
            results = cursor.fetchall()
            column_names = [desc[0] for desc in cursor.description]

        return results, column_names

    def _join_query(self, tables: List[str], table_columns: Dict[str, List[str]], start_time: float, end_time: float,
                    start_exclusive: bool = False):
        """使用JOIN合并多个表的数据（基于time字段）"""
        start_time_f = datetime.datetime.fromtimestamp(start_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        end_time_f = datetime.datetime.fromtimestamp(end_time).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

        if len(tables) == 1:
            table = tables[0]
            columns = table_columns[table]
            column_selects = [f"{table}.{col} AS {table}__{col}" for col in columns]
            time_condition = f"{table}.time > ? AND {table}.time <= ?" if start_exclusive else f"{table}.time BETWEEN ? AND ?"

            query = f"""
            SELECT 
                {table}.time,
                {', '.join(column_selects)}
            FROM {table}
            WHERE {time_condition}
            ORDER BY {table}.time
            """

            with self.execute_transaction(auto_commit=True) as cursor:
                cursor.execute(query, (start_time_f, end_time_f))
                results = cursor.fetchall()
                column_names = [desc[0] for desc in cursor.description]
            return results, column_names

        # 多个表时使用JOIN - 实现省略，与原代码逻辑相同
        # ... 这里需要完整实现JOIN逻辑
        pass

    def process_data_to_dict(self, data_dict: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
        """将数据转换为 {'column': data} 的字典格式"""
        result_dict = {}

        for table_name, table_info in data_dict.items():
            columns = [col for col in table_info['columns'] if col != 'time']
            data_rows = table_info['data']

            for i, column in enumerate(columns):
                column_data = [row[i + 1] for row in data_rows]
                if len(column_data) == 1:
                    result_dict[column] = column_data[0]
                else:
                    result_dict[column] = column_data

        return result_dict

    def is_exist_table(self, table_name: str) -> bool:
        """查询数据表是否存在"""
        sql = "SELECT name FROM sqlite_master WHERE type='table' AND name=?"
        with self.execute_transaction(auto_commit=True) as cursor:
            cursor.execute(sql, (table_name,))
            result = cursor.fetchone()
            return result is not None

    def create_table(self, table_name: str, columns: Dict[str, str], foreign_key_dict: Optional[dict] = None):
        """创建表"""
        columns_with_types = ', '.join(f"{name} {datatype}" for name, datatype in columns.items())
        if foreign_key_dict:
            foreign_key_sqls = ",\n" + self.convert_to_foreign_key_sql(foreign_key_dict)
        else:
            foreign_key_sqls = ""

        sql = f"""CREATE TABLE IF NOT EXISTS "{table_name}" (
                        {columns_with_types}{foreign_key_sqls}
                    );"""

        with self.execute_transaction() as cursor:
            cursor.execute(sql)
            if self.TIME_COLUMN_NAME in columns:
                index_name = f"idx_{table_name}_time_id"
                indexed_columns = [self.TIME_COLUMN_NAME]
                if "id" in columns:
                    indexed_columns.append("id")
                columns_sql = ", ".join(
                    f"{self.quote_ident(name)} DESC" for name in indexed_columns
                )
                cursor.execute(
                    f"CREATE INDEX IF NOT EXISTS {self.quote_ident(index_name)} "
                    f"ON {self.quote_ident(table_name)} ({columns_sql})"
                )
        self.invalidate_schema_cache(table_name)

    def create_meta_table(self, table_name: str):
        """创建描述表"""
        sql = f"""
        CREATE TABLE IF NOT EXISTS "{table_name}" (
            item_name TEXT PRIMARY KEY,
            item_struct TEXT,
            description TEXT
        );   
        """
        with self.execute_transaction() as cursor:
            cursor.execute(sql)

    def insert(self, table_name: str, **kwargs) -> int:
        """插入数据，防止 SQL 注入"""
        columns = ', '.join(kwargs.keys())
        placeholders = ', '.join('?' * len(kwargs))
        sql = f"""INSERT INTO "{table_name}" ({columns}) VALUES ({placeholders});"""

        with self.execute_transaction() as cursor:
            cursor.execute(sql, tuple(kwargs.values()))
            return cursor.rowcount

    def insert_or_ignore(self, table_name: str, **kwargs) -> int:
        """插入数据重复就忽略，防止 SQL 注入"""
        columns = ', '.join(kwargs.keys())
        placeholders = ', '.join('?' * len(kwargs))
        sql = f"""INSERT OR IGNORE INTO "{table_name}" ({columns}) VALUES ({placeholders});"""

        with self.execute_transaction() as cursor:
            cursor.execute(sql, tuple(kwargs.values()))
            return cursor.rowcount

    def insert_2(self, table_name: str, columns_flag: List[str], datas: List[Any]) -> int:
        """插入数据，防止 SQL 注入"""
        columns = ', '.join(columns_flag)
        placeholders = ', '.join('?' * len(datas))
        sql = f"""INSERT INTO "{table_name}" ({columns}) VALUES ({placeholders});"""

        with self.execute_transaction() as cursor:
            cursor.execute(sql, tuple(datas))
            return cursor.rowcount

    def insert_not_columns(self, table_name: str, datas: List[Any]) -> int:
        """插入数据不指定列名"""
        placeholders = ', '.join('?' * len(datas))
        sql = f"""INSERT INTO "{table_name}" VALUES ({placeholders});"""

        with self.execute_transaction() as cursor:
            cursor.execute(sql, tuple(datas))
            return cursor.rowcount

    def query_conditions(self, table_name: str, conditions: str = "") -> List[tuple]:
        """查询数据"""
        sql = f"""SELECT * FROM "{table_name}" """
        sql += conditions

        with self.execute_transaction(auto_commit=True) as cursor:
            cursor.execute(sql)
            return cursor.fetchall()

    def query(self, table_name: str, **kwargs) -> List[tuple]:
        """查询数据，防止 SQL 注入"""
        sql = f"""SELECT * FROM "{table_name}" """
        if kwargs:
            conditions = ' AND '.join(f"{key} = ?" for key in kwargs.keys())
            sql += f" WHERE {conditions};"

            with self.execute_transaction(auto_commit=True) as cursor:
                cursor.execute(sql, tuple(kwargs.values()))
                return cursor.fetchall()
        else:
            with self.execute_transaction(auto_commit=True) as cursor:
                cursor.execute(sql)
                return cursor.fetchall()

    def query_current_Data(self, table_name: str, **kwargs) -> List[tuple]:
        """查询最新数据"""
        self.ensure_time_index(table_name)
        sql = f"""SELECT * FROM "{table_name}" """
        if kwargs:
            conditions = ' AND '.join(f"{key} = ?" for key in kwargs.keys())
            sql += f" WHERE {conditions} ORDER BY time DESC LIMIT 1;"

            with self.execute_transaction(auto_commit=True) as cursor:
                cursor.execute(sql, tuple(kwargs.values()))
                return cursor.fetchall()
        else:
            sql += f" ORDER BY time DESC LIMIT 1;"
            with self.execute_transaction(auto_commit=True) as cursor:
                cursor.execute(sql)
                return cursor.fetchall()

    def query_current_Data_columns(self, table_name: str, columns: List[str], **kwargs) -> List[tuple]:
        """查询最新数据的指定列"""
        self.ensure_time_index(table_name)
        columns_sql = ', '.join(self.quote_ident(column) for column in columns)
        sql = f"""SELECT {columns_sql} FROM "{table_name}" """
        if kwargs:
            conditions = ' AND '.join(f"{key} = ?" for key in kwargs.keys())
            sql += f" WHERE {conditions} ORDER BY time DESC LIMIT 1;"

            with self.execute_transaction(auto_commit=True) as cursor:
                cursor.execute(sql, tuple(kwargs.values()))
                return cursor.fetchall()
        else:
            sql += f" ORDER BY time DESC LIMIT 1;"
            with self.execute_transaction(auto_commit=True) as cursor:
                cursor.execute(sql)
                return cursor.fetchall()

    def query_paging(self, table_name: str, rows_per_page: int, start_row: int, conditions: str = "") -> List[tuple]:
        """查询数据分页"""
        sql = f"""SELECT * FROM "{table_name}" """
        sql += conditions
        sql += f" ORDER BY id DESC LIMIT {rows_per_page} OFFSET {start_row}"

        with self.execute_transaction(auto_commit=True) as cursor:
            cursor.execute(sql)
            return cursor.fetchall()

    def update(self, table_name: str, criteria: Dict[str, Any], **kwargs) -> int:
        """更新数据，防止 SQL 注入"""
        set_clause = ', '.join(f"{key} = ?" for key in kwargs.keys())
        conditions = ' AND '.join(f"{key} = ?" for key in criteria.keys())
        sql = f"""UPDATE "{table_name}" SET {set_clause} WHERE {conditions};"""

        with self.execute_transaction() as cursor:
            cursor.execute(sql, tuple(kwargs.values()) + tuple(criteria.values()))
            return cursor.rowcount

    def delete(self, table_name: str, **kwargs) -> int:
        """删除数据，防止 SQL 注入"""
        conditions = ' AND '.join(f"{key} = ?" for key in kwargs.keys())
        if len(kwargs) > 0:
            conditions = f" WHERE {conditions} "
        sql = f"""DELETE FROM "{table_name}" {conditions};"""

        with self.execute_transaction() as cursor:
            cursor.execute(sql, tuple(kwargs.values()))
            return cursor.rowcount

    def close(self):
        """关闭该管理器创建的全部线程连接。"""
        with self._connections_lock:
            self._closed = True
            idle_thread_ids = [
                thread_id
                for thread_id in self._connections
                if self._active_connection_counts.get(thread_id, 0) == 0
            ]
            connections = [
                self._connections.pop(thread_id) for thread_id in idle_thread_ids
            ]
        for conn in connections:
            try:
                conn.close()
            except sqlite3.Error:
                pass
    """
    @author wangjie
    @create_time 2025-11-27
    @start
    """
    def get_trajectory_xyz_data_by_table(self, table_name, start_time=None, end_time=None, limit=None):
        """
        从指定表获取时间和XYZ坐标数据

        Args:
            table_name (str): 表名
            start_time (str, optional): 开始时间
            end_time (str, optional): 结束时间
            limit (int, optional): 限制返回条数

        Returns:
            list: 包含 [time, x, y, z] 的数据列表
        """
        try:
            conditions = []
            params = []

            if start_time:
                conditions.append("time >= ?")
                params.append(start_time)

            if end_time:
                conditions.append("time <= ?")
                params.append(end_time)

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            limit_clause = ""
            if limit:
                limit_clause = f"LIMIT {limit}"

            sql = f'SELECT time, x, y, z FROM "{table_name}" {where_clause} ORDER BY time {limit_clause}'

            with self.execute_transaction() as cursor:
                cursor.execute(sql, params)
                return cursor.fetchall()

        except Exception as e:
            logger.error(f"从表 {table_name} 获取XYZ轨迹数据失败: {e}")
            return []

    def get_table_data_count(self, table_name, start_time=None, end_time=None):
        """
        获取指定表的数据条数

        Args:
            table_name (str): 表名
            start_time (str, optional): 开始时间
            end_time (str, optional): 结束时间

        Returns:
            int: 数据条数
        """
        try:
            conditions = []
            params = []

            if start_time:
                conditions.append("time >= ?")
                params.append(start_time)

            if end_time:
                conditions.append("time <= ?")
                params.append(end_time)

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            sql = f'SELECT COUNT(*) FROM "{table_name}" {where_clause}'

            with self.execute_transaction() as cursor:
                cursor.execute(sql, params)
                result = cursor.fetchone()
                return result[0] if result else 0

        except Exception as e:
            logger.error(f"获取表 {table_name} 数据条数失败: {e}")
            return 0

    def check_table_exists(self, table_name):
        """
        检查表是否存在

        Args:
            table_name (str): 表名

        Returns:
            bool: 表是否存在
        """
        try:
            with self.execute_transaction() as cursor:
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table_name,))
                return cursor.fetchone() is not None
        except Exception as e:
            logger.error(f"检查表 {table_name} 是否存在失败: {e}")
            return False


    def  get_all_tables(self):
        """
        获取数据库中所有表名

        Returns:
            list: 表名列表
        """
        try:
            with self.execute_transaction() as cursor:
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
                tables = cursor.fetchall()
                return [table[0] for table in tables]
        except Exception as e:
            logger.error(f"获取所有表名失败: {e}")
            return []
    """
    @author wangjie
    @create_time 2025-11-27
    @end
    """


    """
    @author wangjie
    @create_time 2025-12-01
    @start
    """

    def get_trajectory_xyz_data(self, table_name, limit=None, valid_only=True):
        """
        获取指定表的XYZ轨迹数据

        Args:
            table_name (str): 表名
            limit (int, optional): 限制返回的数据条数
            valid_only (bool): 是否只返回有效数据（XYZ都不为null）

        Returns:
            list: XYZ轨迹数据列表
        """
        try:
            conditions = []

            # 只有在valid_only为True时才添加有效性条件
            if valid_only:
                conditions.extend([
                    '"X (m)" IS NOT NULL',
                    '"Y (m)" IS NOT NULL',
                    '"Z (m)" IS NOT NULL'
                ])

            where_clause = ""
            if conditions:
                where_clause = "WHERE " + " AND ".join(conditions)

            limit_clause = ""
            if limit:
                limit_clause = f"LIMIT {limit}"

            # 只查询存在的列，不查询time列
            sql = f'SELECT image_name, "X (m)", "Y (m)", "Z (m)" FROM "{table_name}" {where_clause} ORDER BY image_name {limit_clause}'

            logger.info(f"🔍 执行SQL: {sql}")

            with self.execute_transaction() as cursor:
                cursor.execute(sql)
                result = cursor.fetchall()
                logger.info(f"📊 查询到 {len(result)} 条数据")

                # 确保返回的是列表，即使是空的
                return result if result is not None else []

        except Exception as e:
            logger.error(f"从表 {table_name} 获取XYZ轨迹数据失败: {e}")
            import traceback
            traceback.print_exc()
            # 确保始终返回空列表而不是None
            return []
    """
    @author wangjie
    @create_time 2025-12-02
    @end
    """

    """
    @author wangjie
    @create_time 2026-1-5
    @start
    """

    def get_all_cage_ids(self) -> List[int]:
        """
        获取笼子编号
        从数据库表名字中提取笼子编号ID
        :return: 笼子编号列表，已排序
        """
        try:
            with self.execute_transaction(auto_commit=True) as cursor:
                # 查询所有匹配 DetectionResults_data_cage_* 的表
                # 但排除 *_meta 表
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' "
                    "AND name LIKE 'DetectionResults_data_cage_%' "
                    "AND name NOT LIKE '%_meta'"
                )
                tables = cursor.fetchall()

                logger.info(f"查询到的表: {[t[0] for t in tables]}")

                cage_ids = []
                for table_tuple in tables:
                    table_name = table_tuple[0]
                    try:
                        # 从表名 DetectionResults_data_cage_1 中提取数字 1
                        # 使用正则表达式更安全
                        import re
                        match = re.search(r'cage_(\d+)$', table_name)
                        if match:
                            cage_id = int(match.group(1))
                            cage_ids.append(cage_id)
                            logger.info(f"提取笼子ID: {cage_id} 来自表: {table_name}")
                        else:
                            logger.warning(f"无法从表名 {table_name} 提取笼子ID")
                    except (ValueError, IndexError) as e:
                        logger.error(f"无法从表名 {table_name} 提取笼子ID: {e}")
                        continue

                # 去重排序
                cage_ids = sorted(set(cage_ids))
                logger.info(f"获取笼子列表成功: {cage_ids}")
                return cage_ids

        except Exception as e:
            logger.error(f"获取笼子ID列表失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []

    def get_trajectory_data_by_cage(self, cage_id: int) -> List[Dict[str, Any]]:
        """
        获取指定笼子的轨迹数据(center_x, center_y, center_z)
        :param cage_id: 笼子编号
        :return: 轨迹数据xyz
        """
        table_name = f"DetectionResults_data_cage_{cage_id}"

        # 检查表是否存在
        if not self.is_exist_table(table_name):
            logger.warning(f"表 {table_name} 不存在")
            return []

        try:
            with self.execute_transaction(auto_commit=True) as cursor:
                # SQL查询语句 - 获取 center_x, center_y, center_z
                sql = f"""
                    SELECT center_x, center_y, center_z
                    FROM "{table_name}"
                    WHERE center_x IS NOT NULL
                      AND center_y IS NOT NULL
                      AND center_z IS NOT NULL
                """

                cursor.execute(sql)
                rows = cursor.fetchall()

                # 将查询结果转换为字典列表
                result = []
                for row in rows:
                    try:
                        result.append({
                            'center_x': float(row[0]) if row[0] is not None else 0.0,
                            'center_y': float(row[1]) if row[1] is not None else 0.0,
                            'center_z': float(row[2]) if row[2] is not None else 0.0,
                        })
                    except (ValueError, TypeError) as e:
                        logger.warning(f"跳过无效数据: {row}, 错误: {e}")
                        continue

                logger.info(f"笼子 {cage_id} 查询到 {len(result)} 个轨迹点")
                return result

        except Exception as e:
            logger.error(f"查询笼子 {cage_id} 的轨迹数据失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return []
    """
    @author wangjie
    @create_time 2026-1-5
    @end
    """
# 权限控制类也需要相应修改
class ReadOnlyUser(SQLiteManager):
    """读取用户类"""

    def insert(self, *args, **kwargs):
        raise PermissionError("该用户没有写入权限。")

    def insert_or_ignore(self, *args, **kwargs):
        raise PermissionError("该用户没有写入权限。")

    def insert_2(self, *args, **kwargs):
        raise PermissionError("该用户没有写入权限。")

    def insert_not_columns(self, *args, **kwargs):
        raise PermissionError("该用户没有写入权限。")

    def update(self, *args, **kwargs):
        raise PermissionError("该用户没有写入权限。")

    def delete(self, *args, **kwargs):
        raise PermissionError("该用户没有写入权限。")

    def create_table(self, *args, **kwargs):
        raise PermissionError("该用户没有写入权限。")

    def create_meta_table(self, *args, **kwargs):
        raise PermissionError("该用户没有写入权限。")


class WriteOnlyUser(SQLiteManager):
    """写入用户类"""

    def query(self, *args, **kwargs):
        raise PermissionError("该用户没有读取权限。")

    def query_conditions(self, *args, **kwargs):
        raise PermissionError("该用户没有读取权限。")

    def query_current_Data(self, *args, **kwargs):
        raise PermissionError("该用户没有读取权限。")

    def query_current_Data_columns(self, *args, **kwargs):
        raise PermissionError("该用户没有读取权限。")

    def query_paging(self, *args, **kwargs):
        raise PermissionError("该用户没有读取权限。")

    def get_multi_table_data(self, *args, **kwargs):
        raise PermissionError("该用户没有读取权限。")


def test():
    db = SQLiteManager('example.db')

    # 创建表
    db.create_table('users', {'id': 'INTEGER PRIMARY KEY AUTOINCREMENT', 'name': 'TEXT', 'age': 'INTEGER'})

    # 插入数据
    db.insert('users', name='Alice', age=30)
    db.insert('users', name='Bob', age=25)

    # 查询数据
    print("所有用户:", db.query('users'))
    print("查询年龄为30的用户:", db.query('users', age=30))

    # 更新数据
    db.update('users', {'name': 'Alice'}, age=31)

    # 查询更新后的数据
    print("更新后所有用户:", db.query('users'))

    # 删除数据
    db.delete('users', name='Bob')

    # 查询删除后的数据
    print("删除后所有用户:", db.query('users'))

    # 测试分页查询
    epoch_data = db.query_Epoch_datas('users', page=1, page_size=10)
    print("分页查询结果:", epoch_data)

    # 读取用户示例
    read_user = ReadOnlyUser('example.db')
    try:
        read_user.insert('users', name='Charlie', age=40)
    except PermissionError as e:
        print(e)

    # 写入用户示例
    write_user = WriteOnlyUser('example.db')
    try:
        print(write_user.query('users'))
    except PermissionError as e:
        print(e)


if __name__ == "__main__":
    test()
