import csv
import os
import sys
import sqlite3
import time

from PyQt6 import QtWidgets, QtCore
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QTableWidgetItem, QVBoxLayout, QScrollArea, QSizePolicy, QWidget, QHeaderView
from loguru import logger

from public.dao.SQLite.Monitor_Datas_Handle import Monitor_Datas_Handle
from public.entity.MyQThread import MyQThread
from theme.ThemeManager import ThemeManager
from theme.ThemeQt6 import ThemedWindow, ThemedWidget
#logger = logger.bind(category="gui_logger")

class DataFetcher(MyQThread):
    data_fetched = pyqtSignal(list)  # 信号传递时间和值

    def __init__(self, name, table_name,rows_per_page,start_row):
        super().__init__(name=name)
        self.table_name = table_name
        self.rows_per_page = rows_per_page
        self.start_row = start_row

        # 数据库操作类
        self.handle: Monitor_Datas_Handle = None

    def stop(self):
        if self.handle is not None:
            self.handle.stop()
            self.handle=None
        super().stop()
        # if self.handle is not None:
        #     self.handle.stop()

    def pause(self):
        super().pause()
        # if self.handle is not None:
        #     self.handle.stop()

    def dosomething(self):
        # if self.handle is not None:
        #     self.handle.stop()
        if self.handle is None:
            self.handle = Monitor_Datas_Handle()  # # 创建数据库
        data =[]
        # 可能会有多个数据源
        """[
               (),
               () 
            ]
        """


        datas = self.handle.query_data_paging(table_name=self.table_name,rows_per_page=self.rows_per_page,start_row=self.start_row)
        if datas is None:
            datas = []
        # logger.error(f"get_data:{datas}")
        self.data_fetched.emit(datas)

        time.sleep(0.3)  # 每秒获取一次数据

class TableWidgetPaging(ThemedWidget):
    def __init__(self,parent: QVBoxLayout = None, object_name: str = "",rows_per_page=10,type=None, data_type="", mouse_cage_number=0):
        """

        :param parent:父组件
        :param object_name:图表objectName
        :param rows_per_page 每一页几行
        :param type:模块类型 UFC,UGC等 枚举类 Modbus_Slave_Ids.UFC
        :param data_type: 数据类型 比如monitor_data senior_state等
        """
        super().__init__()
        self.setSizePolicy(QSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding))

        self.parent_layout = parent
        self.mouse_cage_number = mouse_cage_number
        self.type = type
        self.data_type = data_type
        # 数据库操作类
        self.handle: Monitor_Datas_Handle = None
        if self.mouse_cage_number == 0:
            self.table_name = f"{self.type.value['name']}_{self.data_type}"
        else:
            self.table_name = f"{self.type.value['name']}_{self.data_type}_cage_{self.mouse_cage_number}"
        # 获取数据线程
        self.data_fetcher_thread=None

        # 表格和分页控制
        self.table_widget = QtWidgets.QTableWidget()
        table_layout = QtWidgets.QVBoxLayout()
        table_layout.addWidget(self.table_widget)
        self.current_page = 0
        # 每一页几行
        self.rows_per_page = rows_per_page
        self.total_rows = 0

        # 设置几列
        self.total_columns = 0
        # 设置表格数量
        self.set_table_setting()
        # 设置布局
        widget = QWidget()
        widget.setObjectName("tableWidget")
        layout = QVBoxLayout(widget)
        # 添加滚动区域
        self.scroll_area = QScrollArea()

        self.scroll_area.setWidgetResizable(True)  # 使滚动区域填充空间


        # 控制按钮
        self.first_button = QtWidgets.QPushButton("第一页")
        self.prev_button = QtWidgets.QPushButton("上一页")
        self.next_button = QtWidgets.QPushButton("下一页")
        self.last_button = QtWidgets.QPushButton("尾页")
        self.export_button = QtWidgets.QPushButton("导出当页CSV")

        # 分页指示器
        self.page_info_label = QtWidgets.QLabel()

        # 连接信号
        self.first_button.clicked.connect(self.go_to_first_page)
        self.prev_button.clicked.connect(self.prev_page)
        self.next_button.clicked.connect(self.next_page)
        self.last_button.clicked.connect(self.go_to_last_page)
        self.export_button.clicked.connect(self.export_to_csv)

        # 布局设置


        # 加载控件到布局
        self.pagination_layout = QtWidgets.QHBoxLayout()
        self.pagination_layout.addWidget(self.first_button)
        self.pagination_layout.addWidget(self.prev_button)
        self.pagination_layout.addWidget(self.page_info_label)
        self.pagination_layout.addWidget(self.next_button)
        self.pagination_layout.addWidget(self.last_button)
        self.pagination_layout.addWidget(self.export_button)


        layout.addLayout(self.pagination_layout)
        layout.addLayout(table_layout)  # 将滚动区域添加到布局
        self.scroll_area.setWidget(widget)  # 将表格放入滚动区域

        self.parent_layout.addWidget( self.scroll_area)

        # 获取数据并填充表格
        if self.data_fetcher_thread is not None and self.data_fetcher_thread.isRunning():
            self.data_fetcher_thread.stop()
        # 启动新线程来获取新的数据类型
        start_row = self.current_page * self.rows_per_page
        self.data_fetcher_thread = DataFetcher(name="tab_2_tab_0_table_data_fetch_thread", table_name=self.table_name,start_row=start_row,rows_per_page=self.rows_per_page )
        self.data_fetcher_thread.data_fetched.connect(self.update_table)
        self.data_fetcher_thread.start()
    def set_table_setting(self):
        self.handle = Monitor_Datas_Handle()  # # 创建数据库
        # 获取表结构
        if self.mouse_cage_number == 0:
            self.table_name = f"{self.type.value['name']}_{self.data_type}"
        else:
            self.table_name = f"{self.type.value['name']}_{self.data_type}_cage_{self.mouse_cage_number}"
        """
        [
        {
            "desc":温度数据
            "name":temperature
        }
        ]
        """
        self.table_columns_data =  self.handle.query_meta_table_data_all(self.table_name)
        if self.table_columns_data is not None:
            # 总列数
            self.total_columns = len(self.table_columns_data)+1  # 设置列数

            # 设置表头
            self.table_widget.setColumnCount(self.total_columns)
            tableHeaderLabel =[column['desc'] for column in self.table_columns_data if 'desc' in column]
            tableHeaderLabel.append("操作")
            self.table_widget.setHorizontalHeaderLabels(tableHeaderLabel )
            # 表头填满整个tablewidget的宽度
            self.table_widget.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)

        pass



    def update_table(self,datas:list):
        """从数据库更新表格以显示当前页面数据"""


        # 如果没有新数据，保持当前表格数据不变
        if not datas or len(datas)==0:
            self.update_page_info()
            return

        # 清空表格并插入获取的数据
        self.table_widget.setRowCount(0)  # 清空表格
        try:
            for row in datas:
                self.table_widget.insertRow(self.table_widget.rowCount())
                for col in range(self.total_columns):
                    if col !=self.total_columns-1:
                        item = QTableWidgetItem(str(row[col]))
                    else:
                        item = QTableWidgetItem("操作按钮预置")
                    item.setTextAlignment(
                        QtCore.Qt.AlignmentFlag.AlignHCenter | QtCore.Qt.AlignmentFlag.AlignVCenter)  # 水平和垂直居中
                    self.table_widget.setItem(self.table_widget.rowCount() - 1, col, item)
        except Exception as e:
            logger.error(f"{e}")

        # 更新分页信息
        self.update_page_info()
        # 调整列宽，以适应内容
        # self.table_widget.resizeColumnsToContents()
        # self.table_widget.resizeRowsToContents()
        self.table_widget.setCornerButtonEnabled(True)


    def update_page_info(self):
        """更新分页信息显示"""

        self.total_rows = self.handle.query_data_counts(table_name=self.table_name)


        total_pages = (self.total_rows + self.rows_per_page - 1) // self.rows_per_page  # 计算总页数
        self.first_button.setEnabled(self.current_page > 0)
        self.prev_button.setEnabled(self.current_page > 0)
        self.next_button.setEnabled(self.current_page < total_pages - 1)
        self.last_button.setEnabled(self.current_page < total_pages - 1)

        # 更新分页信息标签
        self.page_info_label.setText(f"页 {self.current_page + 1} / {total_pages}, 总数据数量: {self.total_rows}")

    def go_to_first_page(self):
        """切换到第一页"""
        self.current_page = 0
        start_row = self.current_page * self.rows_per_page
        if self.data_fetcher_thread is not None:
            self.data_fetcher_thread.start_row = start_row

    def prev_page(self):
        """切换到上一页"""
        if self.current_page > 0:
            self.current_page -= 1
            start_row = self.current_page * self.rows_per_page
            if self.data_fetcher_thread is not None:
                self.data_fetcher_thread.start_row = start_row

    def next_page(self):
        """切换到下一页"""
        if (self.current_page + 1) * self.rows_per_page < self.total_rows:
            self.current_page += 1
            start_row = self.current_page * self.rows_per_page
            if self.data_fetcher_thread is not None:
                self.data_fetcher_thread.start_row = start_row

    def go_to_last_page(self):
        """切换到最后一页"""
        self.current_page = (self.total_rows + self.rows_per_page - 1) // self.rows_per_page - 1
        start_row = self.current_page * self.rows_per_page
        if self.data_fetcher_thread is not None:
            self.data_fetcher_thread.start_row = start_row

    def export_to_csv(self):
        """导出表格数据到 CSV 文件"""
        """导出表格数据到 CSV 文件"""
        file_path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Save CSV", "", "CSV Files (*.csv);;All Files (*)")

        if file_path:
            with open(file_path, mode='w', newline='', encoding='utf-8') as file:
                writer = csv.writer(file)
                # 写入表头
                headers = [self.table_widget.horizontalHeaderItem(i).text() for i in range(self.total_columns)][:-1]
                writer.writerow(headers)

                # 写入数据
                for row in range(self.table_widget.rowCount()):
                    row_data = []
                    for column in range(self.total_columns):
                        item = self.table_widget.item(row, column)
                        row_data.append(item.text() if item is not None else "")
                    writer.writerow(row_data[:-1])





