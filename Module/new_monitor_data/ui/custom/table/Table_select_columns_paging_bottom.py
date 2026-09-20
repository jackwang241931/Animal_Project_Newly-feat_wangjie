"""
监控数据分页视图。
数据库查询在后台线程执行，界面使用虚拟化模型按需绘制可见单元格。
"""
import sys
import threading

from PyQt6.QtWidgets import (
    QApplication, QWidget, QVBoxLayout, QPushButton,
    QScrollArea, QMessageBox, QLabel, QSpinBox, QHBoxLayout
)
from PyQt6.QtCore import pyqtSignal

from Module.new_monitor_data.ui.custom.table.Virtualized_table import (
    VirtualizedFrozenTable,
    prepare_co2_display_result,
)
from public.config.Data_Column import Data_column_list
from public.config_class.global_setting import global_setting
from public.dao.SQLite.Monitor_Datas_Handle import Monitor_Datas_Handle
from public.entity.MyQThread import MyQThread
from public.function.weight.weight_postprocessor import (
    EPOCH_WEIGHT_COLUMN_KEY,
    fit_epoch_weight_rows,
    load_weight_postprocess_config,
    weight_row_key,
)
from theme.ThemeQt6 import ThemedWindow


class DataFetcher(MyQThread):
    data_fetched = pyqtSignal(dict)  # 信号传递值
    fetch_failed = pyqtSignal(int, str)

    def __init__(self, name, gid, page_size, page, all_column_datas=None):
        super().__init__(name=name)
        self.gid = gid
        self.page_size = page_size
        self.page = page
        self.all_column_datas = list(all_column_datas or [])
        self.refresh_interval_ms = 3000
        self._state_lock = threading.Lock()
        self._request_id = 0
        self._force_refresh = True
        self._last_signature = None

        # 数据库操作类
        self.handle: Monitor_Datas_Handle = None
        self.weight_config = load_weight_postprocess_config()

    @staticmethod
    def _get_initial_weights():
        """读取实验设置中按笼号保存的人工初始体重。"""
        experiment_setting = global_setting.get_setting("experiment_setting", None)
        configured = dict(
            getattr(experiment_setting, "pre_experiment_weights", {}) or {}
        )
        for group in getattr(experiment_setting, "groups", []) or []:
            group_id = getattr(group, "id", None)
            group_name = str(getattr(group, "name", "") or "").strip()
            weight = configured.get(str(group_id), configured.get(group_id))
            if weight is not None and group_name:
                configured[group_name] = weight
        return configured

    def _apply_weight_display(self, datas):
        """用全历史拟合结果替换当前页称重列，数据库原始值不变。"""
        if not self.weight_config.display_fitted_weight:
            return
        if EPOCH_WEIGHT_COLUMN_KEY not in datas.get("columns", []):
            return

        history_rows = self.handle.query_epoch_weight_rows(self.gid)
        if not history_rows:
            return

        fitted_by_key = fit_epoch_weight_rows(
            history_rows,
            initial_weights=self._get_initial_weights(),
            config=self.weight_config,
            fallback_cage=self.gid if self.gid != -1 else None,
        )
        for row in datas.get("rows", []):
            key = weight_row_key(
                row,
                fallback_cage=self.gid if self.gid != -1 else None,
            )
            if key in fitted_by_key:
                row[EPOCH_WEIGHT_COLUMN_KEY] = fitted_by_key[key]

    @property
    def request_id(self):
        with self._state_lock:
            return self._request_id

    def request_refresh(self, page=None, page_size=None, all_column_datas=None):
        with self._state_lock:
            if page is not None:
                self.page = page
            if page_size is not None:
                self.page_size = page_size
            if all_column_datas is not None:
                self.all_column_datas = list(all_column_datas)
            self._request_id += 1
            self._force_refresh = True
            request_id = self._request_id

        self.mutex.lock()
        self.condition.wakeAll()
        self.mutex.unlock()
        return request_id

    def stop(self):
        super().stop()

    def run(self):
        try:
            super().run()
        finally:
            if self.handle is not None:
                self.handle.stop()
                self.handle = None

    def dosomething(self):
        if self.handle is None:
            self.handle = Monitor_Datas_Handle()

        with self._state_lock:
            request_id = self._request_id
            page = self.page
            page_size = self.page_size
            all_column_datas = list(self.all_column_datas)
            force_refresh = self._force_refresh

        try:
            datas = self.handle.query_epoch_data_all_tables_paging(
                gid=self.gid,
                page=page,
                page_size=page_size,
                all_column_datas=all_column_datas
            )
        except Exception as exc:
            self.fetch_failed.emit(request_id, str(exc))
            self.mutex.lock()
            self.condition.wait(self.mutex, self.refresh_interval_ms)
            self.mutex.unlock()
            return
        if datas is None:
            datas = {}

        self._apply_weight_display(datas)

        rows = datas.get("rows", [])
        signature = (
            page,
            page_size,
            datas.get("total_items", 0),
            tuple(
                (row.get('id'), row.get('WM_weight_num'), row.get('_weight_resolved_points'))
                for row in rows
            ),
            tuple(datas.get("columns", [])),
        )
        with self._state_lock:
            is_current = request_id == self._request_id
            should_emit = is_current and (force_refresh or signature != self._last_signature)
            if is_current:
                self._force_refresh = False
                self._last_signature = signature

        if should_emit:
            datas["_request_id"] = request_id
            self.data_fetched.emit(datas)

        with self._state_lock:
            request_changed = request_id != self._request_id
        if not request_changed:
            self.mutex.lock()
            self.condition.wait(self.mutex, self.refresh_interval_ms)
            self.mutex.unlock()


class Table_select_columns_paging_bottom(ThemedWindow):
    def __init__(self,gid):
        super().__init__()
        self.gid = gid
        # 获取数据线程
        self.data_fetcher_thread:DataFetcher = None
        self._init_ui()

        self._init_function()
    def _init_ui(self):
        self.setWindowTitle("带分页器的 QTableWidget（PyQt6）")


        # ---- 数据和列定义 ----
        self.all_columns = ["时间"]
        self.all_column_datas = []
        # 数据
        self.total_items = 0  # 总条数
        #[{表头1:数据，表头2:数据，}....]
        self.data = []

        # 分页参数（默认）
        self.page_size = 200
        self.current_page = 1  # 1-based page index
        self._latest_request_id = 0

        # ---- 主界面布局 ----
        central = QWidget()
        self.setCentralWidget(central)
        main_vbox = QVBoxLayout(central)

        self.setMinimumWidth(1100)

        self.page_scroll_area = QScrollArea()
        self.page_scroll_area.setWidgetResizable(True)
        pager_widget = QWidget()
        pager_layout = QHBoxLayout(pager_widget)
        pager_layout.setContentsMargins(0, 0, 0, 0)
        pager_layout.setSpacing(8)
        self.page_scroll_area.setWidget(pager_widget)
        # 分页控件：首页/上一页/下一页/尾页
        self.first_btn = QPushButton("首页")
        self.prev_btn = QPushButton("上一页")
        self.next_btn = QPushButton("下一页")
        self.last_btn = QPushButton("尾页")
        self.first_btn.clicked.connect(lambda: self.go_to_page(1))
        self.prev_btn.clicked.connect(lambda: self.go_to_page(self.current_page - 1))
        self.next_btn.clicked.connect(lambda: self.go_to_page(self.current_page + 1))
        self.last_btn.clicked.connect(lambda: self.go_to_page(self.total_pages))

        for w in (self.first_btn, self.prev_btn, self.next_btn, self.last_btn):
            pager_layout.addWidget(w)

        # 分页跳转：页号输入 + 跳转按钮
        pager_layout.addWidget(QLabel(" 跳转到第"))
        self.page_spin = QSpinBox()
        self.page_spin.setMinimum(1)
        self.page_spin.setValue(1)
        self.page_spin.setFixedWidth(80)
        pager_layout.addWidget(self.page_spin)
        self.goto_btn = QPushButton("跳转")
        self.goto_btn.clicked.connect(lambda: self.go_to_page(self.page_spin.value()))
        pager_layout.addWidget(self.goto_btn)






        # 每页显示条数：SpinBox 或 ComboBox
        pager_layout.addWidget(QLabel(" 每页显示"))
        self.page_size_spin = QSpinBox()
        self.page_size_spin.setMinimum(1)
        self.page_size_spin.setMaximum(1000)
        self.page_size_spin.setValue(self.page_size)
        self.page_size_spin.setFixedWidth(80)
        self.page_size_spin.valueChanged.connect(self.on_page_size_changed)
        pager_layout.addWidget(self.page_size_spin)
        pager_layout.addWidget(QLabel("条"))

        # 显示总条数与总页数
        self.info_label = QLabel(self._info_text())
        pager_layout.addStretch()
        pager_layout.addWidget(self.info_label)

        main_vbox.addWidget(self.page_scroll_area)

        self.table = VirtualizedFrozenTable()
        # 动态改变冻结列
        self.table.set_frozen_columns_by_headers(
            left_headers=["序号","鼠笼号"],  # 左侧冻结
            right_headers=["获取时间"]  # 右侧冻结
        )
        main_vbox.addWidget(self.table, stretch=7)



        # 初始时分页器不可用，直到表头被置换
        self.set_pager_enabled(False)
    def _init_function(self):

        pass

    # ---------- 属性辅助 ----------
    @property
    def total_pages(self) -> int:
        if self.page_size <= 0:
            return 1
        return (self.total_items + self.page_size - 1) // self.page_size

    # ---------- UI 与逻辑 ----------
    def _info_text(self) -> str:
        return f"共 {self.total_items} 条 | 共 {self.total_pages} 页 | 当前第 {self.current_page} 页"

    def set_pager_enabled(self, enabled: bool):
        """启用/禁用分页器控件（除置换表头按钮外）"""
        for w in (
            self.first_btn, self.prev_btn, self.next_btn, self.last_btn,
            self.page_spin, self.goto_btn, self.page_size_spin
        ):
            w.setEnabled(enabled)

    def set_init_value(self):
        """
        设置初始值
        :return:
        """
        # ---- 数据和列定义 ----
        self.all_columns = ["时间"]
        self.all_column_datas = []
        # 数据
        self.total_items = 0  # 总条数
        # [{表头1:数据，表头2:数据，}....]
        self.data = []



    def find_columns_by_id(self, ids):
        """根据ids寻找data ——column数据"""
        if ids is None or len(ids) == 0:
            return []
        for id in ids:
            id =id-1
            if id >=0 and id < len(Data_column_list.Data_list.value):
                self.all_column_datas.append(Data_column_list.Data_list.value[id])
                self.all_columns.append(Data_column_list.Data_list.value[id].value["column_text"])
            pass
    def on_replace_headers(self,ids:list):
        """信号触发：置换表头并加载第一页"""
        self.set_init_value()
        self.find_columns_by_id(ids)


        # 重置分页状态到第一页
        self.current_page = 1
        self.page_size = self.page_size_spin.value()
        self.page_spin.setValue(1)
        self.page_spin.setMaximum(max(1, self.total_pages))
        self.info_label.setText(self._info_text())

        # 启用分页器
        self.set_pager_enabled(True)
        #
        # 加载第一页
        # self.update_page()

        if self.data_fetcher_thread is None :
            self.data_fetcher_thread = DataFetcher(
                name=f"new_monitor_data_table_mouse_cage_{self.gid}_data_fetch_thread",
                gid=self.gid,
                page=self.current_page,
                page_size=self.page_size,
                all_column_datas=self.all_column_datas
            )
            self.data_fetcher_thread.data_fetched.connect(self.update_page)
            self.data_fetcher_thread.fetch_failed.connect(self.on_fetch_failed)
        self._latest_request_id = self.data_fetcher_thread.request_refresh(
            page=self.current_page,
            page_size=self.page_size,
            all_column_datas=self.all_column_datas
        )
        if not self.data_fetcher_thread.isRunning():
            self.data_fetcher_thread.start()
    def on_page_size_changed(self, new_page_size: int):
        """当每页显示数改变时，重新计算总页数并跳转到合理页码（保持在相同数据范围尽可能）"""
        if new_page_size <= 0:
            return
        old_page_size = self.page_size
        old_first_item_index = (self.current_page - 1) * old_page_size  # 0-based index of first item on current page

        self.page_size = new_page_size
        # 计算新的页数并更新 page_spin 的范围
        new_total_pages = self.total_pages
        self.page_spin.setMaximum(max(1, new_total_pages))

        # 计算新的 current_page，使得 old_first_item_index 仍然在新页中
        new_current_page = (old_first_item_index // self.page_size) + 1
        new_current_page = max(1, min(new_current_page, new_total_pages))
        self.current_page = new_current_page
        self.page_spin.setValue(self.current_page)

        self.info_label.setText(self._info_text())
        self._request_page()

    def go_to_page(self, page: int):
        """跳转到指定页（1-based），并刷新表格"""
        if not (1 <= page <= max(1, self.total_pages)):
            QMessageBox.warning(self, "页码错误", f"请输入有效页码：1 到 {max(1, self.total_pages)}")
            return
        self.current_page = page
        # 保证 page_spin 与 info_label 同步
        self.page_spin.setValue(self.current_page)
        self.info_label.setText(self._info_text())
        self._request_page()

    def _request_page(self):
        if self.data_fetcher_thread is None:
            return
        self.set_pager_enabled(False)
        self.info_label.setText(f"正在加载第 {self.current_page} 页...")
        self._latest_request_id = self.data_fetcher_thread.request_refresh(
            page=self.current_page,
            page_size=self.page_size,
            all_column_datas=self.all_column_datas
        )
        if not self.data_fetcher_thread.isRunning():
            self.data_fetcher_thread.start()

    def update_page(self,result:dict):
        """
        根据 current_page 与 page_size 刷新表格显示（只显示当前页数据）
        :param result: [{表名:数据}....]
        :return:
        """
        if not result or result.get("_request_id", -1) < self._latest_request_id:
            return

        display_columns, display_titles, display_rows = prepare_co2_display_result(
            result.get("columns", []),
            result.get("columns_title", []),
            result.get("rows", []),
        )
        self.all_columns = display_titles
        self.table.set_result(
            display_columns,
            self.all_columns,
            display_rows,
        )
        self.current_page = result.get("page", self.current_page)
        self.total_items = result.get("total_items", 0)
        # 更新分页信息与按钮状态
        self.page_spin.setValue(self.current_page)
        self.info_label.setText(self._info_text())
        self.page_spin.setMaximum(max(1, self.total_pages))
        self.set_pager_enabled(True)
        self._update_nav_buttons()

        # 如果页内行较少导致表格高度不足以出现滚动，可以选择调整最低高度或不做处理（这里不强制加载更多）
        # 若需要自动扩展以填满视图，可以在这里考虑加载更多或调整策略。

    def on_fetch_failed(self, request_id, error_message):
        if request_id < self._latest_request_id:
            return
        self.set_pager_enabled(True)
        self._update_nav_buttons()
        self.info_label.setText(f"数据加载失败：{error_message}")



    def _update_nav_buttons(self):
        """根据 current_page 与 total_pages 更新上一页/下一页按钮可用性"""
        tp = max(1, self.total_pages)
        self.first_btn.setEnabled(self.current_page > 1)
        self.prev_btn.setEnabled(self.current_page > 1)
        self.next_btn.setEnabled(self.current_page < tp)
        self.last_btn.setEnabled(self.current_page < tp)

    def showEvent(self, event):
        super().showEvent(event)
        if self.data_fetcher_thread is not None:
            if self.data_fetcher_thread.isRunning():
                self.data_fetcher_thread.resume()
            self._request_page()

    def hideEvent(self, event):
        if self.data_fetcher_thread is not None and self.data_fetcher_thread.isRunning():
            self.data_fetcher_thread.pause()
        super().hideEvent(event)

    def shutdown(self, wait_ms=1000):
        thread = self.data_fetcher_thread
        if thread is None:
            return
        thread.stop()
        if wait_ms and thread.isRunning():
            thread.wait(wait_ms)

    def closeEvent(self, event):
        self.shutdown(wait_ms=0)
        super().closeEvent(event)

    # 如果需要对外提供刷新数据的接口，可以添加方法从数据源重新加载 self.data / self.total_items
    # 并在替换表头后或数据变化时调用 self.go_to_page(1) 或 self.update_page() 来刷新界面。



def main():
    app = QApplication(sys.argv)
    w = Table_select_columns_paging_bottom(gid=1)
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
