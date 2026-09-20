from typing import Dict

from PyQt6.QtWidgets import QPushButton, QHBoxLayout, QLabel, QCheckBox, QSpinBox, QDoubleSpinBox, QComboBox, \
    QFormLayout, QDialog

from Module.new_monitor_data.ui.custom.button.color_button import ColorButton


class AxisSettingsDialog(QDialog):
    """坐标轴设置对话框"""

    def __init__(self, axis_name: str, axis_config: Dict, parent=None, data_type_hint: str = None):
        super().__init__(parent)
        self.axis_name = axis_name
        self.axis_config = axis_config.copy()
        self.data_type_hint = data_type_hint  # 从父窗口传入的数据类型提示
        self.init_ui()
        # 应用样式表
        self.apply_styles()

    def apply_styles(self):
        """应用样式表以确保复选框正确显示"""
        style_sheet = """
                   QListWidget {
                       background-color: #FFFFFF;
                       color: #000000;
                       border: 1px solid #CCCCCC;
                       border-radius: 4px;
                       padding: 5px;
                   }

                   QListWidget::item {
                       padding: 5px;
                       margin: 2px 0px;
                   }

                   QListWidget::item:hover {
                       background-color: #E8F4F8;
                   }

                   QListWidget::item:selected {
                       background-color: #B3D9E8;
                       color: #000000;
                   }

                   QCheckBox {
                       spacing: 8px;
                   }

                   QCheckBox::indicator {
                       width: 18px;
                       height: 18px;
                   }

                   QCheckBox::indicator:unchecked {
                       image: url(:/icons/checkbox_unchecked.png);
                   }

                   QCheckBox::indicator:checked {
                       image: url(:/icons/checkbox_checked.png);
                   }

                   QPushButton {
                       background-color: #0078D4;
                       color: white;
                       border: none;
                       border-radius: 4px;
                       padding: 6px 15px;
                       font-weight: bold;
                   }

                   QPushButton:hover {
                       background-color: #005A9E;
                   }

                   QPushButton:pressed {
                       background-color: #004578;
                   }
               """
        self.setStyleSheet(style_sheet)
    def init_ui(self):
        """初始化UI"""
        self.setWindowTitle(f"{self.axis_name}轴 设置")
        self.setGeometry(100, 100, 450, 550)

        layout = QFormLayout(self)

        # 轴标签
        label_label = QLabel("轴标签:")
        self.label_input = QComboBox()
        self.label_input.setEditable(True)
        self.label_input.lineEdit().setText(self.axis_config.get("label", ""))
        layout.addRow(label_label, self.label_input)

        # 标签字体大小
        label_size_label = QLabel("标签字体大小:")
        self.label_size_spin = QSpinBox()
        self.label_size_spin.setValue(int(self.axis_config.get("label_fontsize", 10)))
        self.label_size_spin.setRange(6, 30)
        layout.addRow(label_size_label, self.label_size_spin)

        # 标签颜色
        label_color_label = QLabel("标签颜色:")
        self.label_color_btn = ColorButton(self.axis_config.get("label_color", "#000000"))
        layout.addRow(label_color_label, self.label_color_btn)

        # 数据类型
        dtype_label = QLabel("数据类型:")
        self.dtype_combo = QComboBox()
        self.dtype_combo.addItems(['自动检测', '整数', '浮点数', '日期', '时间'])
        current_dtype = self.axis_config.get("data_type", "自动检测")
        index = self.dtype_combo.findText(current_dtype)
        if index >= 0:
            self.dtype_combo.setCurrentIndex(index)
        else:
            self.dtype_combo.setCurrentIndex(0)

        # 如果有数据类型提示，自动设置
        if self.data_type_hint:
            hint_index = self.dtype_combo.findText(self.data_type_hint)
            if hint_index >= 0:
                self.dtype_combo.setCurrentIndex(hint_index)

        self.dtype_combo.currentTextChanged.connect(self.on_data_type_changed)
        layout.addRow(dtype_label, self.dtype_combo)

        # 自动刻度
        self.auto_ticks_check = QCheckBox("自动刻度")
        self.auto_ticks_check.setChecked(self.axis_config.get("auto_ticks", True))
        self.auto_ticks_check.stateChanged.connect(self.toggle_auto_ticks)
        layout.addRow(self.auto_ticks_check)

        # 刻度最小值 - 创建容器用于显示/隐藏
        ticks_min_label = QLabel("刻度最小值:")
        self.ticks_min_container = QHBoxLayout()
        self.ticks_min_spin = QDoubleSpinBox()
        self.ticks_min_spin.setValue(float(self.axis_config.get("ticks_min", 0)))
        self.ticks_min_spin.setRange(-1000000, 1000000)
        self.ticks_min_spin.setSingleStep(1)
        self.ticks_min_spin.setDecimals(2)
        self.ticks_min_container.addWidget(self.ticks_min_spin)

        self.ticks_min_unit_label = QLabel("")
        self.ticks_min_container.addWidget(self.ticks_min_unit_label)
        self.ticks_min_container.addStretch()

        layout.addRow(ticks_min_label, self.ticks_min_container)

        # 刻度最大值
        ticks_max_label = QLabel("刻度最大值:")
        self.ticks_max_container = QHBoxLayout()
        self.ticks_max_spin = QDoubleSpinBox()
        self.ticks_max_spin.setValue(float(self.axis_config.get("ticks_max", 100)))
        self.ticks_max_spin.setRange(-1000000, 1000000)
        self.ticks_max_spin.setSingleStep(1)
        self.ticks_max_spin.setDecimals(2)
        self.ticks_max_container.addWidget(self.ticks_max_spin)

        self.ticks_max_unit_label = QLabel("")
        self.ticks_max_container.addWidget(self.ticks_max_unit_label)
        self.ticks_max_container.addStretch()

        layout.addRow(ticks_max_label, self.ticks_max_container)

        # 刻度间隔
        ticks_step_label = QLabel("刻度间隔:")
        self.ticks_step_container = QHBoxLayout()
        self.ticks_step_spin = QDoubleSpinBox()
        self.ticks_step_spin.setValue(float(self.axis_config.get("ticks_step", 10)))
        self.ticks_step_spin.setRange(0.01, 1000000)
        self.ticks_step_spin.setSingleStep(1)
        self.ticks_step_spin.setDecimals(2)
        self.ticks_step_container.addWidget(self.ticks_step_spin)

        self.ticks_step_unit_label = QLabel("")
        self.ticks_step_container.addWidget(self.ticks_step_unit_label)
        self.ticks_step_container.addStretch()

        layout.addRow(ticks_step_label, self.ticks_step_container)

        # 刻度标签字体大小
        tick_label_size_label = QLabel("刻度字体大小:")
        self.tick_label_size_spin = QSpinBox()
        self.tick_label_size_spin.setValue(int(self.axis_config.get("tick_labelsize", 9)))
        self.tick_label_size_spin.setRange(6, 20)
        layout.addRow(tick_label_size_label, self.tick_label_size_spin)

        # 刻度标签颜色
        tick_color_label = QLabel("刻度颜色:")
        self.tick_color_btn = ColorButton(self.axis_config.get("tick_color", "#000000"))
        layout.addRow(tick_color_label, self.tick_color_btn)

        # 显示/隐藏轴线
        self.axis_visible_check = QCheckBox("显示轴线")
        self.axis_visible_check.setChecked(self.axis_config.get("visible", True))
        layout.addRow(self.axis_visible_check)

        # 提示信息标签
        self.tips_label = QLabel("")
        self.tips_label.setWordWrap(True)
        self.tips_label.setStyleSheet("color: #666666; font-size: 10px;")
        layout.addRow(self.tips_label)

        # 初始化状态
        self.toggle_auto_ticks()
        self.on_data_type_changed(self.dtype_combo.currentText())

        # 按钮
        button_layout = QHBoxLayout()
        ok_btn = QPushButton("确定")
        cancel_btn = QPushButton("取消")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(ok_btn)
        button_layout.addWidget(cancel_btn)
        layout.addRow(button_layout)

    def on_data_type_changed(self, data_type: str):
        """数据类型改变时，更新刻度设置的默认值和单位标签"""
        if data_type == "时间":
            # 时间格式：秒 (0-86400秒 = 0-24小时)
            self.ticks_min_spin.setValue(0)
            self.ticks_max_spin.setValue(86400)
            self.ticks_step_spin.setValue(3600)  # 1小时 = 3600秒

            # 更新范围和小数位
            self.ticks_min_spin.setRange(0, 86400)
            self.ticks_max_spin.setRange(0, 86400)
            self.ticks_step_spin.setRange(1, 86400)

            self.ticks_min_spin.setDecimals(0)
            self.ticks_max_spin.setDecimals(0)
            self.ticks_step_spin.setDecimals(0)

            # 单位标签
            self.ticks_min_unit_label.setText("秒 (0-86400)")
            self.ticks_max_unit_label.setText("秒 (0-86400)")
            self.ticks_step_unit_label.setText("秒 (常用: 3600=1小时, 1800=30分)")

            self.tips_label.setText("💡 时间格式提示: 86400秒=24小时, 3600秒=1小时, 60秒=1分钟")

        elif data_type == "日期":
            # 日期格式：天数
            self.ticks_min_spin.setValue(0)
            self.ticks_max_spin.setValue(30)
            self.ticks_step_spin.setValue(5)

            self.ticks_min_spin.setRange(0, 365)
            self.ticks_max_spin.setRange(0, 365)
            self.ticks_step_spin.setRange(1, 365)

            self.ticks_min_spin.setDecimals(0)
            self.ticks_max_spin.setDecimals(0)
            self.ticks_step_spin.setDecimals(0)

            self.ticks_min_unit_label.setText("天")
            self.ticks_max_unit_label.setText("天")
            self.ticks_step_unit_label.setText("天")

            self.tips_label.setText("💡 日期格式提示: 输入天数")

        elif data_type == "整数":
            # 整数格式
            self.ticks_min_spin.setValue(0)
            self.ticks_max_spin.setValue(100)
            self.ticks_step_spin.setValue(10)

            self.ticks_min_spin.setRange(-1000000, 1000000)
            self.ticks_max_spin.setRange(-1000000, 1000000)
            self.ticks_step_spin.setRange(1, 1000000)

            self.ticks_min_spin.setDecimals(0)
            self.ticks_max_spin.setDecimals(0)
            self.ticks_step_spin.setDecimals(0)

            self.ticks_min_unit_label.setText("")
            self.ticks_max_unit_label.setText("")
            self.ticks_step_unit_label.setText("")

            self.tips_label.setText("💡 整数格式提示: 使用整数值")

        elif data_type == "浮点数":
            # 浮点数格式
            self.ticks_min_spin.setValue(0)
            self.ticks_max_spin.setValue(100)
            self.ticks_step_spin.setValue(10)

            self.ticks_min_spin.setRange(-1000000, 1000000)
            self.ticks_max_spin.setRange(-1000000, 1000000)
            self.ticks_step_spin.setRange(0.01, 1000000)

            self.ticks_min_spin.setDecimals(2)
            self.ticks_max_spin.setDecimals(2)
            self.ticks_step_spin.setDecimals(2)

            self.ticks_min_unit_label.setText("")
            self.ticks_max_unit_label.setText("")
            self.ticks_step_unit_label.setText("")

            self.tips_label.setText("💡 浮点数格式提示: 支持小数点")

        else:  # 自动检测
            self.ticks_min_spin.setValue(0)
            self.ticks_max_spin.setValue(100)
            self.ticks_step_spin.setValue(10)

            self.ticks_min_spin.setRange(-1000000, 1000000)
            self.ticks_max_spin.setRange(-1000000, 1000000)
            self.ticks_step_spin.setRange(0.01, 1000000)

            self.ticks_min_spin.setDecimals(2)
            self.ticks_max_spin.setDecimals(2)
            self.ticks_step_spin.setDecimals(2)

            self.ticks_min_unit_label.setText("")
            self.ticks_max_unit_label.setText("")
            self.ticks_step_unit_label.setText("")

            self.tips_label.setText("💡 自动检测: 系统将根据实际数据自动设置刻度")

    def toggle_auto_ticks(self):
        """切换自动刻度"""
        is_auto = self.auto_ticks_check.isChecked()
        self.ticks_min_spin.setEnabled(not is_auto)
        self.ticks_max_spin.setEnabled(not is_auto)
        self.ticks_step_spin.setEnabled(not is_auto)

        # 自动刻度时，隐藏单位标签
        self.ticks_min_unit_label.setVisible(not is_auto)
        self.ticks_max_unit_label.setVisible(not is_auto)
        self.ticks_step_unit_label.setVisible(not is_auto)

    def get_settings(self):
        """获取设置"""
        return {
            "label": self.label_input.currentText(),
            "label_fontsize": self.label_size_spin.value(),
            "label_color": self.label_color_btn.get_color(),
            "data_type": self.dtype_combo.currentText(),
            "auto_ticks": self.auto_ticks_check.isChecked(),
            "ticks_min": self.ticks_min_spin.value(),
            "ticks_max": self.ticks_max_spin.value(),
            "ticks_step": self.ticks_step_spin.value(),
            "tick_labelsize": self.tick_label_size_spin.value(),
            "tick_color": self.tick_color_btn.get_color(),
            "visible": self.axis_visible_check.isChecked()
        }
