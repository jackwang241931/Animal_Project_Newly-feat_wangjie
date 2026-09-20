import datetime
import math
import sys

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QDialog, QFormLayout, QLineEdit, QDialogButtonBox, QMainWindow, QWidget, QVBoxLayout, \
    QHBoxLayout, QPushButton, QListWidget, QScrollArea, QMenu, QLabel, QApplication, QComboBox, QRadioButton, \
    QListWidgetItem, QMessageBox, QTableWidget, QTableWidgetItem, QAbstractItemView, QHeaderView

from public.component.dialog.custom.InfoDialog import InfoDialog
from public.config_class.global_setting import global_setting
from public.entity.enum.Public_Enum import AnimalGender
from public.entity.experiment_setting_entity import Experiment_setting_entity, Animal, AnimalGroupRecord, Group
from theme.ThemeQt6 import ThemedWindow



class AnimalDialog(QDialog):
    def __init__(self, animal=None):
        super().__init__()
        self.setting_data:Experiment_setting_entity = None
        self.setWindowTitle("动物信息")
        # self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        # 创建表单布局
        self.layout = QFormLayout(self)

        # 动物名称输入框
        self.name_edit = QLineEdit()
        self.name_edit.setPlaceholderText("输入动物名称")
        self.layout.addRow("动物名称:", self.name_edit)

        # 动物ID输入框
        self.id_edit = QLineEdit()
        self.id_edit.setPlaceholderText("输入动物ID")
        self.layout.addRow("动物ID:", self.id_edit)

        # 动物性别单选按钮
        self.gender_group = {}
        male_radio = QRadioButton("雄性")
        female_radio = QRadioButton("雌性")
        self.gender_group['Male'] = male_radio
        self.gender_group['Female'] = female_radio
        self.layout.addRow(QLabel("性别:"), male_radio)
        self.layout.addRow(QLabel(""), female_radio)  # 添加空行以放置第二个单选按钮

        # 动物重量和单位输入框
        self.weight_edit = QLineEdit()
        self.weight_edit.setPlaceholderText("输入动物重量")
        self.unit_combo = QComboBox()
        self.unit_combo.addItems(["kg", "g", "lb"])  # 重量单位下拉框
        self.layout.addRow("动物重量:", self.weight_edit)
        self.layout.addRow("单位:", self.unit_combo)

        # 动物备注输入框
        self.notes_edit = QLineEdit()
        self.notes_edit.setPlaceholderText("输入备注")
        self.layout.addRow("备注:", self.notes_edit)

        # 按钮
        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        self.layout.addWidget(button_box)

        if animal:
            self.populate_fields(animal)

    def populate_fields(self, animal):
        self.name_edit.setText(animal['name'])
        self.id_edit.setText(animal['id'])
        if animal['gender'] == 'Male':
            self.gender_group['Male'].setChecked(True)
        else:
            self.gender_group['Female'].setChecked(True)
        self.weight_edit.setText(animal['weight'])
        self.unit_combo.setCurrentText(animal['unit'])
        self.notes_edit.setText(animal['notes'])

    def get_animal_info(self):

        gender = 'Male' if self.gender_group['Male'].isChecked() else 'Female'
        return {
            'name': self.name_edit.text(),
            'id': self.id_edit.text(),
            'gender': gender,
            'weight': self.weight_edit.text(),
            'unit': self.unit_combo.currentText(),
            'notes': self.notes_edit.text(),
        }

class AnimalWindow(ThemedWindow):
    # 更新content页面信号
    update_content_signal = pyqtSignal(bool)
    def __init__(self):
        super().__init__()

        self._init_ui()
        self.init_animal()
    def _init_ui(self):

        # 创建中心部件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # 创建主垂直布局
        main_layout = QVBoxLayout()
        central_widget.setLayout(main_layout)

        # 创建顶部布局
        self.top_layout = QHBoxLayout()
        main_layout.addLayout(self.top_layout)

        import_template_btn =QPushButton("从模板导入动物")
        import_template_btn.setEnabled(False)
        # 添加创建动物按钮
        create_animal_button = QPushButton("创建动物")
        create_animal_button.clicked.connect(self.add_animal)

        export_template_btn = QPushButton("保存为动物模板")
        export_template_btn.setEnabled(False)
        self.top_layout.addWidget(import_template_btn)
        self.top_layout.addWidget(create_animal_button)
        self.top_layout.addWidget(export_template_btn)
        # 第二顶布布局
        self.sub_top_layout = QVBoxLayout()
        self.title_label = QLabel("无分组/通道")
        self.sub_top_layout.addWidget(self.title_label)
        main_layout.addLayout(self.sub_top_layout)

        # 体重记录按左侧已开启笼子保存，不要求先创建动物。
        self.pre_weight_title = QLabel("已开启笼子实验前体重（单位：g；填写后请点击“保存实验模板”）")
        self.pre_weight_table = QTableWidget(0, 2)
        self.pre_weight_table.setHorizontalHeaderLabels(["笼号/通道", "实验前体重(g)"])
        self.pre_weight_table.setAlternatingRowColors(True)
        self.pre_weight_table.setMinimumHeight(145)
        self.pre_weight_table.setMaximumHeight(240)
        self.pre_weight_table.setEditTriggers(
            QAbstractItemView.EditTrigger.DoubleClicked
            | QAbstractItemView.EditTrigger.SelectedClicked
            | QAbstractItemView.EditTrigger.EditKeyPressed
        )
        header = self.pre_weight_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.pre_weight_table.cellChanged.connect(self._on_pre_weight_changed)
        main_layout.addWidget(self.pre_weight_title)
        main_layout.addWidget(self.pre_weight_table)

        # 创建内容布局
        self.content_layout = QVBoxLayout()
        main_layout.addLayout(self.content_layout)

        # 添加滚动区域
        self.scroll_area = QScrollArea()
        self.content_layout.addWidget(self.scroll_area)

        # 创建列表控件并设置为滚动区域的内容
        self.list_widget = QListWidget()
        self.scroll_area.setWidget(self.list_widget)
        self.scroll_area.setWidgetResizable(True)  # 允许滚动区域大小可调整

        # 启用多选
        self.list_widget.setSelectionMode(QListWidget.SelectionMode.MultiSelection)

        # 连接右键菜单事件
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self.show_context_menu)

        # 连接双击事件
        self.list_widget.itemDoubleClicked.connect(self.edit_animal_info)

    @staticmethod
    def _weight_to_grams(animal):
        """把模板中的动物重量统一换算成克，仅用于体重记录表显示。"""
        try:
            value = float(animal.weight)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(value):
            return None
        unit = str(animal.weight_unit or "g").strip().lower()
        factors = {"g": 1.0, "kg": 1000.0, "lb": 453.59237}
        return value * factors.get(unit, 1.0)

    @staticmethod
    def _group_sort_key(group):
        try:
            return 0, int(group.name)
        except (TypeError, ValueError):
            return 1, str(group.name or "")

    def _refresh_pre_weight_table(self):
        """只显示左侧已开启的笼子，并保留每个笼子的已录入体重。"""
        self.pre_weight_table.blockSignals(True)
        try:
            self.pre_weight_table.setRowCount(0)
            if self.setting_data is None:
                return

            enabled_groups = sorted(
                (group for group in self.setting_data.groups if group.is_selected),
                key=self._group_sort_key,
            )
            weights = getattr(self.setting_data, "pre_experiment_weights", {}) or {}
            for group in enabled_groups:
                row = self.pre_weight_table.rowCount()
                self.pre_weight_table.insertRow(row)

                cage_item = QTableWidgetItem(str(group.name or group.id or ""))
                cage_item.setData(Qt.ItemDataRole.UserRole, group)
                cage_item.setFlags(cage_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                self.pre_weight_table.setItem(row, 0, cage_item)

                weight = weights.get(str(group.id))
                if weight is None:
                    # 兼容旧模板：若该笼子曾绑定动物，暂时沿用动物重量作为初始显示值。
                    for record in self.setting_data.animalGroupRecords:
                        if record.gid != group.id:
                            continue
                        animal = next(
                            (item for item in self.setting_data.animals if item.id == record.aid),
                            None,
                        )
                        weight = self._weight_to_grams(animal) if animal is not None else None
                        if weight is not None:
                            break
                weight_item = QTableWidgetItem("" if weight is None else f"{weight:.3f}")
                weight_item.setData(Qt.ItemDataRole.UserRole, group)
                weight_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.pre_weight_table.setItem(row, 1, weight_item)
        finally:
            self.pre_weight_table.blockSignals(False)

    def refresh_pre_weight_table(self, _is_update=False):
        """供左侧笼子勾选信号调用，刷新当前已开启笼子。"""
        self.setting_data = global_setting.get_setting("experiment_setting_new", self.setting_data)
        self._refresh_pre_weight_table()

    def _on_pre_weight_changed(self, row, column):
        if column != 1:
            return
        item = self.pre_weight_table.item(row, column)
        if item is None:
            return
        group = item.data(Qt.ItemDataRole.UserRole)
        if group is None:
            return

        text = item.text().strip()
        weights = getattr(self.setting_data, "pre_experiment_weights", None)
        if weights is None:
            weights = {}
            self.setting_data.pre_experiment_weights = weights
        if not text:
            weights.pop(str(group.id), None)
        else:
            try:
                value = float(text)
            except ValueError:
                return
            if not math.isfinite(value) or value < 0:
                return
            weights[str(group.id)] = round(value, 3)
        # 已建立动物关系时同步旧字段，保证旧模板/旧界面仍能看到同一数值。
        for record in self.setting_data.animalGroupRecords:
            if record.gid != group.id:
                continue
            for animal in self.setting_data.animals:
                if animal.id == record.aid and text:
                    animal.weight = round(float(text), 3)
                    animal.weight_unit = "g"
                    animal.update_time = datetime.datetime.now()
        global_setting.set_setting("experiment_setting_new", self.setting_data)
        # 让现有“保存实验模板”按钮感知修改，不改变实时实验逻辑。
        self.update_content_signal.emit(False)
    def init_animal(self,is_update=True):
        """

        :param is_update:是否触发cotent等其他界面的数据更新
        :return:
        """
        # 里面装的是Experiment_setting_entity
        self.setting_data: Experiment_setting_entity = global_setting.get_setting("experiment_setting_new", None)
        self.list_widget.clear()
        if self.setting_data is not None:
            if len(self.setting_data.animals) > 0:
                self.title_label.setText(f"一共 {len(self.setting_data.animals)}条动物")
                for index, animal in enumerate(self.setting_data.animals):
                    animal: Animal
                    item = QListWidgetItem(f"序号:{animal.id},动物名称: {animal.name}, ID: {animal.id_write}, 性别: {'雌性' if animal.sex ==AnimalGender.FEMALE.value else '雄性'}, 重量: {animal.weight} {animal.weight_unit}, 备注: {animal.note}")
                    item.setToolTip(f"序号:{animal.id},动物名称: {animal.name}, ID: {animal.id_write}, 性别: {'雌性' if animal.sex ==AnimalGender.FEMALE.value else '雄性'}, 重量: {animal.weight} {animal.weight_unit}, 备注: {animal.note}")
                    item.setData(Qt.ItemDataRole.UserRole, animal)  # 设置自定义数据
                    self.list_widget.addItem(item)
                    pass
            else:
                self.title_label.setText("暂无动物；可直接在上方已开启笼子表录入体重")
            pass
        self._refresh_pre_weight_table()
        pass
        # 更新content页面
        if is_update:
            self.update_content_signal.emit(False)
        pass
    # 添加动物
    def add_animal(self):
        dialog = AnimalDialog()
        if dialog.exec() == QDialog.DialogCode.Accepted:  # 如果用户点击了OK按钮
            animal_info = dialog.get_animal_info()
            init_index = 1
            # 取最大name的那一个
            if self.setting_data is not None and len(self.setting_data.animals) > 0:
                int_animal_ids = [int(animal.id) for animal in self.setting_data.animals]
                init_index = max(int_animal_ids) + 1
            self.setting_data.animals.append(Animal(id=init_index,name=animal_info['name'],id_write=animal_info['id'],sex= animal_info['gender'],weight=animal_info['weight'],weight_unit=animal_info['unit'],note=animal_info['notes'],create_time=datetime.datetime.now(),update_time=datetime.datetime.now()))
        global_setting.set_setting("experiment_setting_new", self.setting_data)
        self.init_animal()

    def show_context_menu(self, pos):
        # 创建右键菜单
        context_menu = QMenu(self)

        # 创建删除菜单项
        delete_action = context_menu.addAction("删除选项")
        delete_action.triggered.connect(self.delete_items)

        # 显示菜单
        context_menu.exec(self.list_widget.mapToGlobal(pos))

    def delete_items(self):
        if len(self.list_widget.selectedItems()) ==0:
            msg_box = InfoDialog(title="删除动物", info="未选中动物",
                                 icon=QMessageBox.Icon.Warning)
            msg_box.exec()
            return
        # 删除选中的项
        for item in self.list_widget.selectedItems():
            item_data: Animal = item.data(Qt.ItemDataRole.UserRole)
            # 删除animal
            for index, animal in enumerate(self.setting_data.animals):
                animal: Animal
                if item_data is animal:
                    self.setting_data.animals.remove(animal)
            # 删除groups和animals关系
            for index, group_animal_record in enumerate(self.setting_data.animalGroupRecords):
                group_animal_record: AnimalGroupRecord
                if item_data.id == group_animal_record.aid:
                    self.setting_data.animalGroupRecords.remove(group_animal_record)
        global_setting.set_setting("experiment_setting_new", self.setting_data)
        self.init_animal()

    def edit_animal_info(self, item:QListWidgetItem):
        # 获取动物信息
        animal_info = self.parse_animal_info(item.text())
        dialog = AnimalDialog(animal_info)
        if dialog.exec() == QDialog.DialogCode.Accepted:  # 如果用户点击了OK按钮
            updated_info = dialog.get_animal_info()
            item_data:Animal = item.data(Qt.ItemDataRole.UserRole)
            for index, animal in enumerate(self.setting_data.animals):
                if animal.id == item_data.id:
                    self.setting_data.animals[index].name = updated_info['name']
                    self.setting_data.animals[index].id_write = updated_info['id']
                    self.setting_data.animals[index].sex = updated_info['gender']
                    self.setting_data.animals[index].weight = updated_info['weight']
                    self.setting_data.animals[index].weight_unit = updated_info['unit']
                    self.setting_data.animals[index].note = updated_info['notes']
                    self.setting_data.animals[index].update_time= datetime.datetime.now()

            global_setting.set_setting("experiment_setting_new", self.setting_data)
            self.init_animal()

    def parse_animal_info(self, text):
        # 解析动物信息的文本以供编辑
        parts = text.split(", ")
        name = parts[0].split(": ")[1]
        animal_id = parts[1].split(": ")[1]
        gender = parts[2].split(": ")[1]
        weight_unit = parts[3].split(": ")[1].split(" ")
        weight = weight_unit[0]
        unit = weight_unit[1]
        notes = parts[4].split(": ")[1]
        return {
            'name': name,
            'id': animal_id,
            'gender': gender,
            'weight': weight,
            'unit': unit,
            'notes': notes,
        }

if __name__ == "__main__":
    app = QApplication(sys.argv)

    main_window = AnimalWindow()
    main_window.show()

    sys.exit(app.exec())
