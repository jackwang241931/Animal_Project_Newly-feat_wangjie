from typing import Dict

from PyQt6.QtWidgets import QPushButton, QHBoxLayout, QSpinBox, QLabel, QDoubleSpinBox, QComboBox, QCheckBox, \
    QFormLayout, QDialog

from Module.User_monitor.ui.custom.button.color_button import ColorButton


class LegendSettingsDialog(QDialog):
    """图例设置对话框"""

    def __init__(self, legend_config: Dict, parent=None):
        super().__init__(parent)
        self.legend_config = legend_config.copy()
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
        self.setWindowTitle("图例设置")
        self.setGeometry(100, 100, 400, 350)

        layout = QFormLayout(self)

        # 显示图例
        self.visible_check = QCheckBox("显示图例")
        self.visible_check.setChecked(self.legend_config.get("visible", True))
        layout.addRow(self.visible_check)

        # 位置
        position_label = QLabel("位置:")
        self.position_combo = QComboBox()
        positions = ['upper left', 'upper center', 'upper right',
                     'center left', 'center', 'center right',
                     'lower left', 'lower center', 'lower right']
        self.position_combo.addItems(positions)
        current_pos = self.legend_config.get("position", "upper left")
        index = self.position_combo.findText(current_pos)
        if index >= 0:
            self.position_combo.setCurrentIndex(index)
        layout.addRow(position_label, self.position_combo)

        # 字体大小
        fontsize_label = QLabel("字体大小:")
        self.fontsize_spin = QSpinBox()
        self.fontsize_spin.setValue(int(self.legend_config.get("fontsize", 10)))
        self.fontsize_spin.setRange(6, 30)
        layout.addRow(fontsize_label, self.fontsize_spin)

        # 背景颜色
        bg_color_label = QLabel("背景颜色:")
        self.bg_color_btn = ColorButton(self.legend_config.get("bg_color", "#FFFFFF"))
        layout.addRow(bg_color_label, self.bg_color_btn)

        # 边框颜色
        edge_color_label = QLabel("边框颜色:")
        self.edge_color_btn = ColorButton(self.legend_config.get("edge_color", "#000000"))
        layout.addRow(edge_color_label, self.edge_color_btn)

        # 背景透明度
        framealpha_label = QLabel("背景透明度 (0-1):")
        self.framealpha_spin = QDoubleSpinBox()
        self.framealpha_spin.setValue(float(self.legend_config.get("framealpha", 0.9)))
        self.framealpha_spin.setRange(0, 1)
        self.framealpha_spin.setSingleStep(0.1)
        layout.addRow(framealpha_label, self.framealpha_spin)

        # 边框宽度
        edgewidth_label = QLabel("边框宽度:")
        self.edgewidth_spin = QDoubleSpinBox()
        self.edgewidth_spin.setValue(float(self.legend_config.get("edgewidth", 1.0)))
        self.edgewidth_spin.setRange(0, 5)
        self.edgewidth_spin.setSingleStep(0.5)
        layout.addRow(edgewidth_label, self.edgewidth_spin)

        # 列数
        ncol_label = QLabel("列数:")
        self.ncol_spin = QSpinBox()
        self.ncol_spin.setValue(int(self.legend_config.get("ncol", 1)))
        self.ncol_spin.setRange(1, 5)
        layout.addRow(ncol_label, self.ncol_spin)

        # 按钮
        button_layout = QHBoxLayout()
        ok_btn = QPushButton("确定")
        cancel_btn = QPushButton("取消")
        ok_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        button_layout.addWidget(ok_btn)
        button_layout.addWidget(cancel_btn)
        layout.addRow(button_layout)

    def get_settings(self):
        """获取设置"""
        return {
            "visible": self.visible_check.isChecked(),
            "position": self.position_combo.currentText(),
            "fontsize": self.fontsize_spin.value(),
            "bg_color": self.bg_color_btn.get_color(),
            "edge_color": self.edge_color_btn.get_color(),
            "framealpha": self.framealpha_spin.value(),
            "edgewidth": self.edgewidth_spin.value(),
            "ncol": self.ncol_spin.value()
        }