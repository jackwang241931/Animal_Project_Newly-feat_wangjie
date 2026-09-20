from typing import Dict

from PyQt6.QtWidgets import QFormLayout, QDialog, QLabel, QPushButton, QComboBox, QHBoxLayout, QDoubleSpinBox

from Module.new_monitor_data.ui.custom.button.color_button import ColorButton


class SeriesSettingsDialog(QDialog):
    """数据系列设置对话框"""

    def __init__(self, series_name: str, series_info: Dict, parent=None):
        super().__init__(parent)
        self.series_name = series_name
        self.series_info = series_info
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
        self.setWindowTitle(f"设置 - {self.series_name}")
        self.setGeometry(100, 100, 450, 400)

        layout = QFormLayout(self)

        # 系列名称
        name_label = QLabel("系列名称:")
        self.name_input = QComboBox()
        self.name_input.setEditable(True)
        self.name_input.lineEdit().setText(self.series_name)
        layout.addRow(name_label, self.name_input)

        # 颜色设置
        color_label = QLabel("线条颜色:")
        self.color_btn = ColorButton(self.series_info.get("color", "#000000"))
        layout.addRow(color_label, self.color_btn)

        # 线条宽度
        width_label = QLabel("线条宽度:")
        self.width_spin = QDoubleSpinBox()
        self.width_spin.setValue(self.series_info.get("linewidth", 2.0))
        self.width_spin.setRange(0.5, 10)
        self.width_spin.setSingleStep(0.5)
        layout.addRow(width_label, self.width_spin)

        # 标记大小
        marker_label = QLabel("标记大小:")
        self.marker_spin = QDoubleSpinBox()
        self.marker_spin.setValue(self.series_info.get("markersize", 6.0))
        self.marker_spin.setRange(2, 20)
        self.marker_spin.setSingleStep(1)
        layout.addRow(marker_label, self.marker_spin)

        # 透明度
        alpha_label = QLabel("透明度 (0-1):")
        self.alpha_spin = QDoubleSpinBox()
        self.alpha_spin.setValue(self.series_info.get("alpha", 1.0))
        self.alpha_spin.setRange(0, 1)
        self.alpha_spin.setSingleStep(0.1)
        layout.addRow(alpha_label, self.alpha_spin)

        # 标记样式
        marker_style_label = QLabel("标记样式:")
        self.marker_combo = QComboBox()
        self.marker_combo.addItems(['o', 's', '^', 'v', 'D', '*', '+', 'x', 'None'])
        current_marker = self.series_info.get("marker", "o")
        self.marker_combo.setCurrentText(current_marker)
        layout.addRow(marker_style_label, self.marker_combo)

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
            "color": self.color_btn.get_color(),
            "linewidth": self.width_spin.value(),
            "markersize": self.marker_spin.value(),
            "alpha": self.alpha_spin.value(),
            "marker": self.marker_combo.currentText()
        }

