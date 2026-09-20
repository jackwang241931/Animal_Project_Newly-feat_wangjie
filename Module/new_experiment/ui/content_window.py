import copy
import os
import sys
import time
import typing
from datetime import datetime

from PyQt6 import QtGui
from PyQt6.QtGui import QStandardItemModel, QCursor, QStandardItem
from PyQt6.QtWidgets import (
    QApplication, QVBoxLayout, QHBoxLayout, QPushButton,
    QWidget, QTreeView, QScrollArea, QMessageBox, QMenu, QInputDialog, QFileDialog, QLabel
)
from PyQt6.QtCore import Qt, QPoint, QModelIndex, pyqtSignal

from my_abc.BaseModule import BaseModule
from public.component.dialog.custom.InfoDialog import InfoDialog
from public.component.dialog.custom.save_experiment_dialog import Save_Experiment_Dialog, Save_Experiment_Dialog_TYPE
from public.config_class.global_setting import global_setting
from public.dao.SQLite.Experiment_Setting_DAO_Handle import Experiment_Setting_DAO_Handle
from public.entity.BaseWindow import BaseWindow
from public.entity.enum.Public_Enum import AnimalGender, AppState
from public.entity.experiment_setting_entity import Experiment_setting_entity, Group, Animal, AnimalGroupRecord, \
    AnimalGroupRecord_View
from public.entity.queue.ObjectQueueItem import ObjectQueueItem
from public.util.custom_data_file_util import custom_template_file_util
from public.util.time_util import time_util
from theme.ThemeQt6 import ThemedWindow
from public.util.class_util import class_util

from loguru import logger




class ContentWindow(ThemedWindow):
    # 更新group页面信号
    update_group_signal = pyqtSignal(bool)
    # 更新animal界面信号
    update_animal_signal = pyqtSignal(bool)
    def showEvent(self, a0: typing.Optional[QtGui.QShowEvent]) -> None:

        self.setting_data: Experiment_setting_entity = global_setting.get_setting("experiment_setting_new", None)
        if self.setting_data is not None and not self.setting_data.is_emtpy():
            self.is_import = True
            self.import_file_path =  global_setting.get_setting("experiment_setting_file_open", "")
            self.setting_file_path = global_setting.get_setting("experiment_setting_file_open", "")
            self.is_update=False
            self.template_file_path_label.setText(f"当前模板文件：{self.setting_file_path}")
        super().showEvent(a0)
    def __init__(self,main_gui :BaseWindow=None):
        super().__init__()
        #主界面
        self.main_gui:BaseWindow = main_gui
        # 实验配置数据
        self.setting_data: Experiment_setting_entity = None
        #是否修改模板
        self.is_update=False
        # 是否导入模板
        self.is_import=False
        # 导入的文件路径
        self.import_file_path=""
        # 文件设置路径
        self.setting_file_path = ""
        self._init_ui()
        self.init_content()
    def _init_ui(self):
        # 设置主窗口的布局

        self.central_widget = QWidget(self)
        self.setCentralWidget(self.central_widget)

        main_layout = QVBoxLayout(self.central_widget)

        # 创建顶部布局
        top_layout = QHBoxLayout()
        self.template_file_path_label = QLabel("未导入实验模板文件")
        top_layout.addWidget(self.template_file_path_label)
        # 添加顶部布局到主布局
        main_layout.addLayout(top_layout)

        # 创建次顶部布局
        sub_top_layout = QHBoxLayout()
        self.clear_button = QPushButton("清空实验模板")
        self.import_button = QPushButton("导入实验模板")
        self.save_button = QPushButton("保存实验模板")
        self.apply_button = QPushButton("应用实验")

        sub_top_layout.addWidget(self.clear_button)
        sub_top_layout.addWidget(self.import_button)
        sub_top_layout.addWidget(self.save_button)
        sub_top_layout.addWidget(self.apply_button)

        # 添加顶部布局到主布局
        main_layout.addLayout(sub_top_layout)

        # 创建内容布局
        self.content_layout = QVBoxLayout()
        self.tree_view = QTreeView()
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidget(self.tree_view)
        self.scroll_area.setWidgetResizable(True)
        self.content_layout.addWidget(self.scroll_area)

        # 添加内容布局到主布局
        main_layout.addLayout(self.content_layout)

        # 设置树形模型
        self.model = QStandardItemModel()
        self.model.setHorizontalHeaderLabels(["组/通道"])
        self.tree_view.setModel(self.model)

        # 连接右键菜单
        self.tree_view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.tree_view.customContextMenuRequested.connect(self.open_menu)
        # 不允许任何节点编辑
        self.tree_view.setEditTriggers(QTreeView.EditTrigger.NoEditTriggers)

        # 添加双击事件
        # 连接双击信号
        self.tree_view.doubleClicked.connect(self.on_item_double_clicked)
        # 添加测试组
        # self.add_group("实验组 1")
        # self.add_group("实验组 2")

        # 连接按钮信号（示例）
        self.clear_button.clicked.connect(self.clear_template)
        self.import_button.clicked.connect(self.import_template)
        self.save_button.clicked.connect(self.save_template)
        self.apply_button.clicked.connect(self.apply_experiment)
    def init_content(self,is_update=True):
        """

        :param is_update:是否触发group等其他界面的数据更新
        :return:
        """
        # 里面装的是Experiment_setting_entity
        self.setting_data: Experiment_setting_entity = global_setting.get_setting("experiment_setting_new", None)
        # 清空treeview
        self.clear_tree()
        if self.setting_data is not None:
            #添加group
            if len(self.setting_data.groups) > 0:
                for index, group in enumerate(self.setting_data.groups):
                    group: Group
                    status_text = "已启用" if group.is_selected else "未启用"
                    self.add_group_view(f"动物分组/通道: {group.name} {status_text}", group)
                    pass
                self.model.setHorizontalHeaderLabels([f"一共 {len(self.setting_data.groups)}个分组/通道"])
            else:
                self.model.setHorizontalHeaderLabels([f"请新建分组/通道"])
            pass
            #添加动物与组的关系
            if len(self.setting_data.animalGroupRecords)>0:
                for index, animalGroupRecord in enumerate(self.setting_data.animalGroupRecords):
                    animalGroupRecord: AnimalGroupRecord
                    #  从groups和animals寻找到关系表中的类
                    animalGroupRecord_View = AnimalGroupRecord_View()
                    if len(self.setting_data.groups)>0:
                        for group in self.setting_data.groups:
                            group: Group
                            if group.id == animalGroupRecord.gid:
                                animalGroupRecord_View.group = group
                                break
                        pass
                    if len(self.setting_data.animals)>0:
                        for animal in self.setting_data.animals:
                            animal: Animal
                            if animal.id == animalGroupRecord.aid:
                                animalGroupRecord_View.animal = animal
                                break
                        pass
                    animalGroupRecord_View.id = animalGroupRecord.aid
                    animalGroupRecord_View.note = animalGroupRecord.note
                    animalGroupRecord_View.create_time = animalGroupRecord.create_time
                    animalGroupRecord_View.update_time = animalGroupRecord.update_time
                    # 添加显示组件
                    animal_item = QStandardItem(
                        f"序号:{animalGroupRecord_View.animal.id},动物名称: {animalGroupRecord_View.animal.name}, ID: {animalGroupRecord_View.animal.id_write}, 性别: {'雌性' if animalGroupRecord_View.animal.sex == AnimalGender.FEMALE.value else '雄性'}, 重量: {animalGroupRecord_View.animal.weight} {animalGroupRecord_View.animal.weight_unit}, 备注: {animalGroupRecord_View.animal.note}")

                    animal_item.setToolTip(
                        f"序号:{animalGroupRecord_View.animal.id},动物名称: {animalGroupRecord_View.animal.name}, ID: {animalGroupRecord_View.animal.id_write}, 性别: {'雌性' if animalGroupRecord_View.animal.sex == AnimalGender.FEMALE.value else '雄性'}, 重量: {animalGroupRecord_View.animal.weight} {animalGroupRecord_View.animal.weight_unit}, 备注: {animalGroupRecord_View.animal.note}")

                    # 存储动物信息
                    animal_item.setData(animalGroupRecord_View.animal,Qt.ItemDataRole.UserRole)

                    # 将动物项添加到组项中
                    # 找到group-item
                    for row in range(self.model.rowCount()):
                        group_item:QStandardItem=self.model.item(row)
                        group = group_item.data(Qt.ItemDataRole.UserRole)
                        if group.id ==animalGroupRecord_View.group.id:
                            group_item.appendRow(animal_item)
                            # 自动展开组节点
                            self.tree_view.expand(group_item.index())
                            break
        # 找到每个group里的动物数量
        for row in range(self.model.rowCount()):
            group_item: QStandardItem = self.model.item(row)
            group_item.setText(f"{group_item.text()} {'共'+str(group_item.rowCount())+'个动物' if group_item.rowCount()!=0 else '无动物'}")

        # 更新group和animal页面
        if is_update:
            self.update_group_signal.emit(False)
            self.update_animal_signal.emit(False)
        pass

    def on_item_double_clicked(self, index):
        """每个子节点的双击事件"""
        if index.isValid():
            text = ""
            item:QStandardItem = self.model.itemFromIndex(index)
            data = item.data(Qt.ItemDataRole.UserRole)
            # 根据类的属性备注来放详情信息
            item_attr = class_util.get_public_attributes_with_notes(data)
            for key, value in item_attr.items():
                text += f"{value['note']}:{value['value']}\n"
            msg_box = InfoDialog(title="详情信息", info=text, icon=QMessageBox.Icon.Information)
            msg_box.exec()


    def add_group_view(self, group_name,group_data):
        """添加组界面"""
        group_item = QStandardItem(group_name)
        group_item.setData(group_data,Qt.ItemDataRole.UserRole)
        group_item.setToolTip(group_name)
        self.model.appendRow(group_item)
    def clear_tree(self):
        """清空树视图"""
        self.model.clear()
        self.model.setHorizontalHeaderLabels(["组/通道"])  # 可选: 重新设置列标题
    def open_menu(self, position: QPoint):
        """打开右键菜单"""
        index = self.tree_view.indexAt(position)
        menu = QMenu()

        if index.isValid():
            item = self.model.itemFromIndex(index)

            if item.parent() is None:  # 组节点
                add_animal_action = menu.addAction("添加动物")
                copy_group_action = menu.addAction("复制该分组/通道")
                delete_group_action = menu.addAction("删除分组/通道")

                action = menu.exec(self.tree_view.viewport().mapToGlobal(position))
                if action == add_animal_action:
                    # 如果没有动物 就跳出弹窗提醒用户没有动物，去添加动物
                    if self.setting_data is not None and  self.setting_data.animals is not None and len(self.setting_data.animals) > 0:
                        self.add_animal(index)
                        pass
                    else:
                        msg_box = InfoDialog(title="注意",info="尚未添加动物，请前往添加动物！",icon=QMessageBox.Icon.Warning)
                        msg_box.exec()

                        pass
                elif action == copy_group_action:
                    #复制该分组
                    self.copy_group(index)
                    pass
                elif action == delete_group_action:
                    # 删除分组
                    self.delete_group(index)
            else:  # 动物节点
                copy_animal_action = menu.addAction("复制该动物")
                delete_animal_action = menu.addAction("删除动物")

                action = menu.exec(self.tree_view.viewport().mapToGlobal(position))
                if action == delete_animal_action:
                    # 删除动物
                    self.delete_animal(index)
                elif action == copy_animal_action:
                    #复制动物
                    self.copy_animal(index)
                    pass
        else:
            # 如果点击在无效位置，可以选择其他操作，比如只显示添加组的选项
            add_group_action = menu.addAction("添加组")
            action = menu.exec(self.tree_view.viewport().mapToGlobal(position))
            if action == add_group_action:
                self.add_group()

    def add_group(self):
        """添加组"""
        group_nums, ok = QInputDialog.getInt(self, "添加分组", "请输入分组/通道个数:",1)
        if ok and group_nums:
            init_index = 1
            # 取最大name的那一个
            if self.setting_data is not None and len(self.setting_data.groups) > 0:
                int_group_names = [int(group.name) for group in self.setting_data.groups]
                init_index = max(int_group_names) + 1
            for i in range(int(group_nums)):
                if self.setting_data is not None:
                    self.setting_data.groups.append(
                        Group(id=init_index+i, name=str(init_index+i), create_time=datetime.now(),
                              update_time=datetime.now(), is_selected=False)
                    )

            global_setting.set_setting("experiment_setting_new", self.setting_data)
            # 修改模板修改状态
            self.update_status()
            self.init_content()
    def add_animal(self, group_index: QModelIndex):
        """添加动物"""

        animal_menu = QMenu()
        for animal in self.setting_data.animals:
            animal:Animal
            animal_menu.addAction(f"序号: {animal.id},动物名称: {animal.name}, ID: {animal.id_write}, 性别: {'雌性' if animal.sex ==AnimalGender.FEMALE.value else '雄性'}, 重量: {animal.weight} {animal.weight_unit}, 备注: {animal.note}",
                                  lambda group_index=group_index, a=animal: self.add_animal_to_group(group_index, a))

        animal_menu.exec(QCursor.pos())

    def add_animal_to_group(self, group_index: QModelIndex, animal: Animal):
        """将动物添加到组/通道"""
        animal_nums , ok = QInputDialog.getInt(self, "添加动物数量", f"请输入动物\n序号: {animal.id},动物名称: {animal.name}, ID: {animal.id_write}, 性别: {'雌性' if animal.sex ==AnimalGender.FEMALE.value else '雄性'}, 重量: {animal.weight} {animal.weight_unit}, 备注: {animal.note}\n数量:",1)
        if ok and animal_nums:
            group_item:QStandardItem = self.model.itemFromIndex(group_index)
            group  = group_item.data(Qt.ItemDataRole.UserRole)
            ## 如果动物-组关系不存在则添加
            # if len( self.setting_data.animalGroupRecords)==0 or (group.id,animal.id) not in [(animalGroupRecord.gid,animalGroupRecord.aid) for animalGroupRecord in self.setting_data.animalGroupRecords] :

            if self.setting_data is not None :
                init_index = 1
                # 取最大name的那一个
                if self.setting_data is not None and len(self.setting_data.animalGroupRecords) > 0:
                    int_animalGroupRecords_ids = [int(animalGroupRecords.id) for animalGroupRecords in self.setting_data.animalGroupRecords]
                    init_index = max(int_animalGroupRecords_ids) + 1
                for animal_num in range(animal_nums):
                    self.setting_data.animalGroupRecords.append(AnimalGroupRecord(id=init_index+animal_num,aid=animal.id,gid=group.id,note="无",create_time=datetime.now(),update_time=datetime.now()))
            global_setting.set_setting("experiment_setting_new", self.setting_data)
            # 修改模板修改状态
            self.update_status()
            # 获取滚动条位置
            # 获取垂直滚动条的位置
            vertical_scroll_position = self.scroll_area.verticalScrollBar().value()
            self.init_content()
            # 更新界面后直接滑动到之前选中的位置
            self.scroll_area.verticalScrollBar().setValue(vertical_scroll_position)
            # else:
            #     pass



    def delete_group(self, group_index: QModelIndex):
        """删除组 关联关系一并删除"""
        group_item: QStandardItem = self.model.itemFromIndex(group_index)
        group:Group = group_item.data(Qt.ItemDataRole.UserRole)
        # 先删除关联关系
        newAnimalGroupRecords=[]
        for animalGroupRecord in self.setting_data.animalGroupRecords:
            animalGroupRecord:AnimalGroupRecord
            if animalGroupRecord.gid != group.id :
                newAnimalGroupRecords.append(animalGroupRecord)
        self.setting_data.animalGroupRecords = newAnimalGroupRecords
        # 删除组
        newGroups= []
        for group_item in self.setting_data.groups:
            group_item:Group
            if group_item.id!=group.id:
                newGroups.append(group_item)
        self.setting_data.groups = newGroups
        global_setting.set_setting("experiment_setting_new", self.setting_data)
        # 修改模板修改状态
        self.update_status()
        # 获取垂直滚动条的位置
        vertical_scroll_position = self.scroll_area.verticalScrollBar().value()
        self.init_content()
        self.scroll_area.verticalScrollBar().setValue(vertical_scroll_position)

    def copy_group(self, group_index: QModelIndex):
        """复制组"""
        group_item: QStandardItem = self.model.itemFromIndex(group_index)
        group: Group = group_item.data(Qt.ItemDataRole.UserRole)
        group_2 = copy.deepcopy(group)
        init_index = 1
        # 取最大name的那一个
        if self.setting_data is not None and len(self.setting_data.groups) > 0:
            int_group_names = [int(group.name) for group in self.setting_data.groups]
            init_index = max(int_group_names) + 1
        group_2.id = init_index
        group_2.name = init_index
        self.setting_data.groups.append(group_2)



        # 复制该组里的所有动物关系
        animalGroupRecords_init_index = 1
        # 取最大name的那一个
        if self.setting_data is not None and len(self.setting_data.animalGroupRecords) > 0:
            int_animalGroupRecords_ids = [int(animalGroupRecords.id) for animalGroupRecords in
                                          self.setting_data.animalGroupRecords]
            animalGroupRecords_init_index = max(int_animalGroupRecords_ids) + 1
        for animal_item_index in range(group_item.rowCount()):
            animal_item : QStandardItem= group_item.child(animal_item_index)
            animal:Animal = animal_item.data(Qt.ItemDataRole.UserRole)
            animalGroupRecord=AnimalGroupRecord(id=animalGroupRecords_init_index+animal_item_index,
                                                aid=animal.id,
                                                gid=group_2.id,
                                                note="无",
                                                create_time=datetime.now(),
                                                update_time=datetime.now()
                                                )
            self.setting_data.animalGroupRecords.append(animalGroupRecord)

        global_setting.set_setting("experiment_setting_new", self.setting_data)
        # 修改模板修改状态
        self.update_status()
        # 获取垂直滚动条的位置
        vertical_scroll_position = self.scroll_area.verticalScrollBar().value()
        self.init_content()
        self.scroll_area.verticalScrollBar().setValue(vertical_scroll_position)

    def copy_animal(self, animal_index: QModelIndex):
        """复制动物"""
        animal_item: QStandardItem = self.model.itemFromIndex(animal_index)
        animal: Animal = animal_item.data(Qt.ItemDataRole.UserRole)
        group_item: QStandardItem = self.model.itemFromIndex(animal_index.parent())
        group: Group = group_item.data(Qt.ItemDataRole.UserRole)

        animal_nums, ok = QInputDialog.getInt(self, "复制动物数量",
                                              f"请输入动物\n序号: {animal.id},动物名称: {animal.name}, ID: {animal.id_write}, 性别: {'雌性' if animal.sex == AnimalGender.FEMALE.value else '雄性'}, 重量: {animal.weight} {animal.weight_unit}, 备注: {animal.note}\n数量:",1)
        if ok and animal_nums:
            init_index = 1
            # 取最大name的那一个
            if self.setting_data is not None and len(self.setting_data.animalGroupRecords) > 0:
                int_animalGroupRecords_ids = [int(animalGroupRecords.id) for animalGroupRecords in
                                              self.setting_data.animalGroupRecords]
                init_index = max(int_animalGroupRecords_ids) + 1

            for animal_num in range(animal_nums):
                self.setting_data.animalGroupRecords.append(
                    AnimalGroupRecord(id=init_index + animal_num, aid=animal.id, gid=group.id, note="无",
                                      create_time=datetime.now(), update_time=datetime.now()))


            global_setting.set_setting("experiment_setting_new", self.setting_data)
            # 修改模板修改状态
            self.update_status()
            # 获取垂直滚动条的位置
            vertical_scroll_position = self.scroll_area.verticalScrollBar().value()
            self.init_content()
            self.scroll_area.verticalScrollBar().setValue(vertical_scroll_position)
    def delete_animal(self, animal_index: QModelIndex):
        """删除动物 其实就是删除动物与组的关系"""
        animal_item: QStandardItem = self.model.itemFromIndex(animal_index)
        animal:Animal = animal_item.data(Qt.ItemDataRole.UserRole)
        group_item: QStandardItem = self.model.itemFromIndex(animal_index.parent())
        group: Group = group_item.data(Qt.ItemDataRole.UserRole)
        # 删除关联关系
        newAnimalGroupRecords = []
        for animalGroupRecord in self.setting_data.animalGroupRecords:
            animalGroupRecord: AnimalGroupRecord
            if animalGroupRecord.gid != group.id and animalGroupRecord.aid!=animal.id:
                newAnimalGroupRecords.append(animalGroupRecord)
        self.setting_data.animalGroupRecords = newAnimalGroupRecords
        global_setting.set_setting("experiment_setting_new", self.setting_data)
        # 修改模板修改状态
        self.update_status()
        # 获取垂直滚动条的位置
        vertical_scroll_position = self.scroll_area.verticalScrollBar().value()
        self.init_content()
        self.scroll_area.verticalScrollBar().setValue(vertical_scroll_position)

    def clear_template(self):
        """清空实验模板"""
        # 确认用户的行为
        reply = QMessageBox.question(self, '确认', '你确定要清空模板吗？',
                                     QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No)

        if reply == QMessageBox.StandardButton.Yes:
            # 如果用户确定，
            self.setting_data = Experiment_setting_entity()
            self.is_import = False  # 新增：重置导入状态
            self.import_file_path = ""  # 新增：重置导入文件路径
            self.setting_file_path = ""  # 新增：重置设置文件路径
            self.is_update = False  # 新增：重置修改状态
            global_setting.set_setting("experiment_setting_new", self.setting_data)
            self.template_file_path_label.setText("未导入实验模板文件")  # 新增：重置标签文本
            self.init_content(is_update=True)
            pass
        else:
            pass

    def import_template(self):
        """导入实验模板"""
        file_path, _ = QFileDialog.getOpenFileName(self, "导入实验模板", "", f"template Files (*.{custom_template_file_util.extension_name});")
        if file_path:
            self.is_import=True
            self.import_file_path=file_path
            self.setting_file_path=file_path
            self.is_update=False
            self.template_file_path_label.setText(f"当前模板文件：{file_path}")
            db_file_path = custom_template_file_util.load_template_contents_from_custom_file(file_path)
            # 获取文件所在的文件夹路径
            folder_path = os.path.dirname(db_file_path)
            # 获取文件名称
            file_name = os.path.basename(db_file_path)
            handle =Experiment_Setting_DAO_Handle(db_fold_path=folder_path, db_name=file_name)
            setting_data = handle.query_data_database_all()
            handle.stop()
            self.setting_data = setting_data
            global_setting.set_setting("experiment_setting_new", self.setting_data)
            self.init_content()
            # 检查文件是否存在
            if os.path.isfile(db_file_path):
                os.remove(db_file_path)  # 删除文件
    def save_template(self):
        if not self.setting_data.is_emtpy():
            # 导入了且修改了模板
            if self.is_update and self.is_import:
                dialog = Save_Experiment_Dialog(title="保存模板",text="当前模板存在修改操作，请选择：")
                result = dialog.exec()  # 显示对话框并等待用户响应
                if result ==Save_Experiment_Dialog_TYPE.SAVE_SELF:
                    """将修改保存到原模板文件"""
                    db_file_path = custom_template_file_util.load_template_contents_from_custom_file(self.import_file_path)
                    # 获取文件所在的文件夹路径
                    folder_path = os.path.dirname(db_file_path)
                    # 获取文件名称
                    file_name = os.path.basename(db_file_path)
                    handle = Experiment_Setting_DAO_Handle(db_fold_path=folder_path, db_name=file_name)
                    delete_state =handle.remove_data_database_all_not_include_metaDB()
                    state = handle.insert_data(data=self.setting_data)
                    handle.stop()
                    if all([delete_state,state]):
                        custom_file_path = custom_template_file_util.save_template_contents_as_custom_file(db_file_path)
                        self.setting_file_path=self.import_file_path
                        self.template_file_path_label.setText(self.template_file_path_label.text()[:-1])
                        msg_box = InfoDialog(title="保存模板", info="保存实验模板成功!",
                                             icon=QMessageBox.Icon.Information)
                        msg_box.exec()
                    else:
                        msg_box = InfoDialog(title="保存模板", info="保存实验模板失败!", icon=QMessageBox.Icon.Warning)
                        msg_box.exec()
                    pass
                elif result == Save_Experiment_Dialog_TYPE.SAVE_NEW:
                    """另存为新的模板文件"""
                    self.save_experiment_file()
                    pass
                else:
                    """关闭了窗口"""
                    return
            #未导入模板
            else:
                self.save_experiment_file()
                pass
            self.is_update = False
        else:
            msg_box = InfoDialog(title="保存模板", info="模板不能为空!", icon=QMessageBox.Icon.Warning)
            msg_box.exec()
            pass


    def apply_experiment(self):
        """应用实验"""
        if not self.setting_data.is_emtpy():
            # 新增：检查是否有启用的通道
            enabled_groups = [g for g in self.setting_data.groups if g.is_selected]
            if len(enabled_groups) == 0:
                msg_box = InfoDialog(title="应用实验", info="请至少启用一个分组/通道!", icon=QMessageBox.Icon.Warning)
                msg_box.exec()
                return
            # 导入了且修改了模板 先保存模板
            if self.is_update and self.is_import:
                dialog = Save_Experiment_Dialog(title="保存模板",text="当前模板存在修改操作，请选择：")
                result = dialog.exec()  # 显示对话框并等待用户响应
                if result == Save_Experiment_Dialog_TYPE.SAVE_SELF:
                    """将修改保存到原模板文件"""
                    db_file_path = custom_template_file_util.load_template_contents_from_custom_file(
                        self.import_file_path)
                    self.setting_file_path=self.import_file_path
                    # 获取文件所在的文件夹路径
                    folder_path = os.path.dirname(db_file_path)
                    # 获取文件名称
                    file_name = os.path.basename(db_file_path)
                    handle = Experiment_Setting_DAO_Handle(db_fold_path=folder_path, db_name=file_name)
                    delete_state =handle.remove_data_database_all_not_include_metaDB()
                    state = handle.insert_data(data=self.setting_data)
                    handle.stop()
                    if all([delete_state,state]):
                        custom_file_path = custom_template_file_util.save_template_contents_as_custom_file(
                            db_file_path=db_file_path)
                        self.template_file_path_label.setText(self.template_file_path_label.text()[:-1])
                        msg_box = InfoDialog(title="保存模板", info="保存实验模板成功!",
                                             icon=QMessageBox.Icon.Information)
                        msg_box.exec()
                    else:
                        msg_box = InfoDialog(title="保存模板", info="保存实验模板失败!", icon=QMessageBox.Icon.Warning)
                        msg_box.exec()
                        return
                    pass
                elif result == Save_Experiment_Dialog_TYPE.SAVE_NEW:
                    """另存为新的模板文件"""
                    if not self.save_experiment_file():
                        return
                else :
                    """关闭了窗口"""
                    return
                #未导入模板
            elif  self.is_update :
                if not self.save_experiment_file():
                    return
                pass
            self.is_update = False
            # 将实验设置存入全局变量
            self.setting_data.groups = self.setting_data.groups
            # 修改：只保存启用的通道
            enabled_setting_data = copy.deepcopy(self.setting_data)
            enabled_group_ids = [g.id for g in enabled_groups]
            enabled_setting_data.groups = [
                group for group in enabled_setting_data.groups
                if group.id in enabled_group_ids
            ]
            enabled_setting_data.animalGroupRecords = [
                record for record in self.setting_data.animalGroupRecords
                if record.gid in enabled_group_ids
            ]
            global_setting.set_setting("experiment_setting", enabled_setting_data)
            global_setting.set_setting("experiment_setting_file", self.setting_file_path)
            send_message_queue = global_setting.get_setting("send_message_queue")
            send_message_queue.put(ObjectQueueItem(origin='Main_New_experiment_cotent_index_apply_experiment', to='main_monitor_data', title='experiment_setting',data={'experiment_setting':enabled_setting_data,"experiment_setting_file":self.setting_file_path},
                                                   time=time_util.get_format_from_time(time.time())))
            message_structs = []
            if not global_setting.get_setting("environment_module_only", False):
                message_structs = [
                    ObjectQueueItem(origin='Main_New_experiment_cotent_index_apply_experiment', to='main_infrared_camera', title='experiment_setting',data={'experiment_setting':enabled_setting_data,"experiment_setting_file":self.setting_file_path},
                                    time=time_util.get_format_from_time(time.time())),
                    ObjectQueueItem(origin='Main_New_experiment_cotent_index_apply_experiment', to='main_deep_camera', title='experiment_setting',data={'experiment_setting':enabled_setting_data,"experiment_setting_file":self.setting_file_path},
                                    time=time_util.get_format_from_time(time.time())),
                ]
            for message_struct in message_structs:
                queue = global_setting.get_setting("queue")
                queue.put(message_struct)

            global_setting.set_setting("app_state", AppState.APPLYING)

            # 更新main_gui组件显示
            self.main_gui.change_enable_component_app_state_signal.emit()
            self.main_gui.status_bar.update_setting_file_name(f"当前实验文件: {self.setting_file_path}")
            msg_box = InfoDialog(title="应用实验", info="应用成功!", icon=QMessageBox.Icon.Information)
            msg_box.exec()
            # # 關閉窗口
            # self.parent().close()
            # # 跳轉窗口
            # for module in self.main_gui.modules:
            #     module: BaseModule
            #     if module.name =="Main_experiment_setting":
            #         module.click_method()
            #         return
            # ✅ 【修改】只做导航，不做关闭操作
            self._navigate_to_experiment_setting()


        else:
            msg_box = InfoDialog(title="应用实验", info="模板不能为空!", icon=QMessageBox.Icon.Warning)
            msg_box.exec()
            pass
    def save_experiment_file(self):
        file_path, _ = QFileDialog.getSaveFileName(self, "保存实验模板", "", f"template Files (*.{custom_template_file_util.extension_name});")

        if file_path:
            db_file_path =custom_template_file_util.get_db_extension_file(file_path)
            # 获取文件所在的文件夹路径
            folder_path = os.path.dirname(db_file_path)
            # 获取文件名称
            file_name = os.path.basename(db_file_path)
            handle = Experiment_Setting_DAO_Handle(db_fold_path=folder_path, db_name=file_name)
            state = handle.insert_data(data=self.setting_data)
            handle.stop()
            if state:
                # 转换文件格式
                custom_file_path = custom_template_file_util.save_template_contents_as_custom_file(db_file_path=db_file_path)
                self.setting_file_path=custom_file_path
                self.template_file_path_label.setText(f"当前模板文件: {custom_file_path}")
                msg_box = InfoDialog(title="保存模板", info="保存实验模板成功!", icon=QMessageBox.Icon.Information)
                msg_box.exec()
                return True
            else:
                msg_box = InfoDialog(title="保存模板", info="保存实验模板失败!", icon=QMessageBox.Icon.Warning)
                msg_box.exec()
                return False
            pass
        else:
            return False
    def update_status(self):
        """模板修改事件"""
        if self.setting_data.is_emtpy():
            self.template_file_path_label.setText(self.template_file_path_label.text()[:-1])
            self.is_update = False
            return
        # 更改label文字 增加*显示修改未保存
        if not self.is_update:
            self.template_file_path_label.setText(self.template_file_path_label.text() + "*")
            self.is_update = True

    def _navigate_to_experiment_setting(self):
        """导航到实验设置界面"""
        # 找到目标模块
        target_module = None
        for module in self.main_gui.modules:
            if module.name == "New_main_experiment_setting":
                target_module = module
                break

        if target_module and hasattr(target_module, 'click_method'):
            # 直接调用 click_method（已改进的版本会自动清理信号）
            target_module.click_method()
        else:
            logger.warning(f"警告：未找到 Main_experiment_setting 模块")
if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ContentWindow()
    window.setWindowTitle("实验管理系统")
    window.resize(600, 400)
    window.show()
    sys.exit(app.exec())
