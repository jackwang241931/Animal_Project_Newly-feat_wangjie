import json
import re
import sys
import typing

from PyQt6 import QtGui
from PyQt6.QtCore import Qt, QModelIndex, QAbstractItemModel, QVariant, QRegularExpression, pyqtSignal
from PyQt6.QtGui import QStandardItemModel, QStandardItem
from PyQt6.QtWidgets import QListView, QVBoxLayout, QCheckBox, QMainWindow, QHBoxLayout, QPushButton, QScrollArea, \
    QWidget, QApplication, QListWidget, QListWidgetItem, QLabel, QLineEdit, QComboBox, QAbstractItemView, QStatusBar, \
    QFileDialog, QMessageBox, QInputDialog
from loguru import logger

from public.config.Data_Column import Data_column_list
from public.config_class.global_setting import global_setting
from public.entity.BaseWindow import BaseWindow
from public.entity.experiment_setting_entity import Experiment_setting_entity
from public.util.class_util import class_util
from theme.ThemeQt6 import ThemedWindow

class User_table_Column_check_list_view(BaseWindow):
    set_table_column_signal = pyqtSignal(dict)

    def showEvent(self, a0: typing.Optional[QtGui.QShowEvent]) -> None:
        # 加载数据
        if self.datas_type == 0:
            self.load_json()
        else:
            self.load_group()

        self.select_all()
        pass

    def __init__(self, ok_btn_text="", datas_type=0):
        super().__init__()
        self.ok_btn_text = ok_btn_text
        self.datas_type = datas_type
        self._init_ui()
        self._init_function()

    def _init_ui(self):
        self.setWindowTitle("PyQt6 -- 搜索/过滤 / 全选 / 反选 示例（已去除排序）")
        self.resize(820, 620)

        # 数据结构：列表，元素为 dict {'orig': original_element, 'display': str, 'checked': bool}
        self.items = []
        # 当前视图对应的原始索引列表（用于需要时参考）
        self.current_view_indices = []

        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        #提示信息区域
        tip_layout = QHBoxLayout()
        main_layout.addLayout(tip_layout)
        self.tip_label = QLabel("1.先选择通道几的数据查看.\n2.在单击<确定选择通道>按钮.")
        self.tip_label.setStyleSheet("""
        QLabel {
            font-size: 15px;
        }
        """)
        tip_layout.addWidget(self.tip_label)
        # 顶部操作按钮
        top_layout = QHBoxLayout()
        main_layout.addLayout(top_layout)

        self.select_all_btn = QPushButton("全选")
        self.invert_btn = QPushButton("反选 / 切换")
        self.show_selected_btn = QPushButton(self.ok_btn_text)
        top_layout.addWidget(self.select_all_btn)
        top_layout.addWidget(self.invert_btn)

        top_layout.addWidget(self.show_selected_btn)
        top_layout.addStretch()

        # 搜索控件（不含排序控件）
        control_layout = QHBoxLayout()
        main_layout.addLayout(control_layout)

        control_layout.addWidget(QLabel("搜索:"))
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("按显示文本过滤（不区分大小写）")
        control_layout.addWidget(self.search_edit)
        control_layout.addStretch()

        # 信息标签
        self.info_label = QLabel("尚未加载数据")
        main_layout.addWidget(self.info_label)

        # 列表视图
        self.list_view = QListView()
        # self.list_view.setSelectionMode(QAbstractItemView.SelectionMode.MultiSelection)
        main_layout.addWidget(self.list_view)

        # model
        self.model = QStandardItemModel()
        self.list_view.setModel(self.model)
        # 不使用多选的 selection，全部由 checkbox 控制
        self.list_view.setSelectionMode(QListView.SelectionMode.NoSelection)
        # 状态栏
        self.status = QStatusBar()
        self.setStatusBar(self.status)


    def _init_function(self):
        # 信号连接
        self.select_all_btn.clicked.connect(self.select_all)
        self.invert_btn.clicked.connect(self.invert_selection)
        self.show_selected_btn.clicked.connect(self.show_selected)

        self.search_edit.textChanged.connect(self.on_filter_changed)

        # 当界面上 item 的复选框被切换时，更新内部数据
        self.model.itemChanged.connect(self.on_model_item_changed)

        pass
    def _update_buttons_enabled(self, has_items: bool):
        self.select_all_btn.setEnabled(has_items)
        self.invert_btn.setEnabled(has_items)
        self.show_selected_btn.setEnabled(has_items)
        self.search_edit.setEnabled(has_items)
    def load_group(self):
        """

        如果 data 返回 None，表示用户取消或未处理。
        """
        try:
            settings:Experiment_setting_entity = global_setting.get_setting("experiment_setting",None)
            data = []
            if settings is not None:
                data =[class_util.to_dict(group) for group in settings.groups]
        except Exception as e:
            QMessageBox.critical(self, "导入错误", f"调用 fetch_json_list() 时发生异常：\n{e}")
            return

        if data is None:
            return

        ok = self._prepare_items_from_list(data)
        if not ok:
            return

        self.refresh_view()
        self._update_buttons_enabled(len(self.items) > 0)
        self.status.showMessage(f"已加载 {len(self.items)} 项", 5000)

    def _prepare_items_from_list(self, data_list):
        """
        将从 fetch_json_list 得到的 data_list 转换成 self.items 列表。
        自动优先使用 'column_text' 作为显示字段（若存在），否则回退到第一个键，
        若元素不是 dict 则序列化为字符串显示。返回 True 表示成功，False 表示失败/取消。
        """

        self.items = []
        if not data_list:
            # 空列表，仍然清空 items 并返回成功
            return True

        # 检测是否有 dict 元素
        has_dict = any(isinstance(x, dict) for x in data_list)
        display_key = None

        if has_dict:
            first_obj = next((x for x in data_list if isinstance(x, dict)), {})
            keys = list(first_obj.keys())
            # 优先使用 column_text，其次回退到 keys[0]（如果有）
            if "column_text" in keys:
                display_key = "column_text"
            elif "name" in keys:
                display_key = "name"
            elif keys:
                display_key = keys[0]
            else:
                display_key = None
        else:
            first_obj = next((x.value for x in data_list if isinstance(x.value, dict)), {})
            keys = list(first_obj.keys())
            # 优先使用 column_text，其次回退到 keys[0]（如果有）
            if "column_text" in keys:
                display_key = "column_text"
            elif "name" in keys:
                display_key = "name"
            elif keys:
                display_key = keys[0]
            else:
                display_key = None

        # 构造 items
        for elem in data_list:
            if isinstance(elem, dict) and display_key is not None:

                val = elem.get(display_key, "")
                # 确保是字符串
                disp_text = "" if val is None else "通道 "+str(val)
                self.items.append({'orig': elem, 'display': disp_text, 'checked': False, 'is_displayed': True})
            else:
                val = elem.value.get(display_key, "")
                # 确保是字符串
                disp_text = "" if val is None else str(val)
                self.items.append({'orig': elem.value, 'display': disp_text, 'checked': False, 'is_displayed': True})
                # # 非 dict 或未找到 display_key：序列化为 JSON 字符串作为显示
                # try:
                #     disp_text = json.dumps(elem.value, ensure_ascii=False)
                # except Exception:
                #     disp_text = str(elem.value)


        return True


    def load_json(self):
        """

        如果 data 返回 None，表示用户取消或未处理。
        """
        try:
            data =  Data_column_list.Data_list.value
        except Exception as e:
            QMessageBox.critical(self, "导入错误", f"调用 fetch_json_list() 时发生异常：\n{e}")
            return

        if data is None:
            return



        ok = self._prepare_items_from_list(data)
        if not ok:
            return

        self.refresh_view()
        self._update_buttons_enabled(len(self.items) > 0)
        self.status.showMessage(f"已加载 {len(self.items)} 项", 5000)





    def refresh_view(self):




        # 确保 model 存在

        self.model = QStandardItemModel(self.list_view)
        self.list_view.setModel(self.model)
        self.model.itemChanged.connect(self.on_model_item_changed)
        # 屏蔽信号
        self.model.blockSignals(True)
        self.model.clear()
        # print(self.items)
        for index,it in enumerate(self.items):
            if it["is_displayed"]:
                disp = it.get("display", "")
                item = QStandardItem(disp)
                item.setToolTip(it.get("orig", {}).get("desc", ""))
                item.setEditable(False)
                # 存储原始对象以便后续使用
                item.setData(index, Qt.ItemDataRole.UserRole)
                # 如果需要复选框
                if "checked" in it:
                    item.setCheckable(True)
                    item.setCheckState(Qt.CheckState.Checked if it.get("checked") else Qt.CheckState.Unchecked)
                self.model.appendRow(item)
        # 解除屏蔽信号
        self.model.blockSignals(False)
        # 更新 info_label（如果有）
        try:
            count = self.model.rowCount()
            self.info_label.setText(f"数据项：显示 {count} / 总计 {len(self.items)} 项")
        except Exception:
            pass

    def on_model_item_changed(self, item: QStandardItem):
        """
        当界面上的某项复选框被切换时，写回 self.items。
        """

        index = item.data(Qt.ItemDataRole.UserRole)
        if isinstance(index, int) :
            self.items[index]['checked'] = not self.items[index]['checked']
            if self.items[index]['checked']:
                self.status.showMessage(f"已选中{item.text()}", 3000)
            else:
                self.status.showMessage(f"已取消选中{item.text()}", 5000)


    def on_filter_changed(self, text: str):
        #搜索
        text = (text or "").strip()
        # 转义用户输入（把元字符当作普通字符）
        esc = re.escape(text)
        pattern = re.compile(esc, flags=re.IGNORECASE)
        if not text:
            # 清空过滤
            for item in self.items:
                item["is_displayed"] = True
        else:
            # 转义用户输入并做子串匹配（.*escaped.*），忽略大小写

            for item in self.items:
                disp = item['display'] or ""
                matched = pattern.search(disp)
                if matched:
                    item["is_displayed"] = True
                else:
                    item["is_displayed"] = False
        self.refresh_view()

    def select_all(self):
        # 选中所有项（包含当前未显示项）
        for it in self.items:
            it['checked'] = True
        self.refresh_view()
        self.status.showMessage("已全部选中", 3000)

    def invert_selection(self):
        for it in self.items:
            it['checked'] = not it['checked']
        self.refresh_view()
        self.status.showMessage("已切换选择状态", 3000)

    def show_selected(self):
        checkids_dict={}
        if self.datas_type == 0:
            checkids_dict['type']='column'
        else:
            checkids_dict['type']='group'
        checkids_dict['data']= [it['orig']['id'] for it in self.items if it['checked']]
        # logger.critical(f"table_column_check_list_view | checkids_dict:{checkids_dict}")
        self.set_table_column_signal.emit(checkids_dict)

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = User_table_Column_check_list_view()
    window.resize(300, 400)
    window.show()
    sys.exit(app.exec())