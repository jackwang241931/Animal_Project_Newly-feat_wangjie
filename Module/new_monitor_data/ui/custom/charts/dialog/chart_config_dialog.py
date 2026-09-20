from PyQt6.QtWidgets import QMessageBox, QFileDialog, QPushButton, QVBoxLayout, QLabel, QDialog


class ChartConfigDialog(QDialog):
    """图表配置管理对话框"""

    def __init__(self, chart_widget, parent=None):
        super().__init__(parent)
        self.chart_widget = chart_widget
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
        self.setWindowTitle("图表配置管理")
        self.setGeometry(150, 150, 500, 300)

        layout = QVBoxLayout(self)

        # 说明
        label = QLabel("图表配置管理：\n"
                       "• 设置默认配置：将当前配置保存为默认值\n"
                       "• 导出配置：将当前配置导出为JSON文件\n"
                       "• 导入配置：从JSON文件导入配置\n"
                       "• 恢复默认：恢复到系统默认配置")
        layout.addWidget(label)

        # 按钮
        button_layout = QVBoxLayout()

        # 设置默认配置
        set_default_btn = QPushButton("设置为默认配置")
        set_default_btn.clicked.connect(self.set_default_config)
        button_layout.addWidget(set_default_btn)

        # 导出配置
        export_btn = QPushButton("导出配置到文件")
        export_btn.clicked.connect(self.export_config)
        button_layout.addWidget(export_btn)

        # 导入配置
        import_btn = QPushButton("从文件导入配置")
        import_btn.clicked.connect(self.import_config)
        button_layout.addWidget(import_btn)

        # 恢复默认
        restore_btn = QPushButton("恢复系统默认配置")
        restore_btn.clicked.connect(self.restore_default_config)
        button_layout.addWidget(restore_btn)

        layout.addLayout(button_layout)

        # 关闭按钮
        close_btn = QPushButton("关闭")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def set_default_config(self):
        """设置当前配置为默认配置"""
        try:
            config = self.chart_widget.get_all_config()
            self.chart_widget.save_default_config(config)
            QMessageBox.information(self, "成功", "当前配置已设置为默认配置！")
        except Exception as e:
            QMessageBox.warning(self, "失败", f"设置默认配置失败：{str(e)}")

    def export_config(self):
        """导出配置"""
        try:
            file_path, _ = QFileDialog.getSaveFileName(
                self,
                "导出图表配置",
                "",
                "JSON文件 (*.json);;所有文件 (*)"
            )

            if file_path:
                config = self.chart_widget.get_all_config()
                self.chart_widget.export_config(config, file_path)
                QMessageBox.information(self, "成功", f"配置已导出到：{file_path}")
        except Exception as e:
            QMessageBox.warning(self, "失败", f"导出配置失败：{str(e)}")

    def import_config(self):
        """导入配置"""
        try:
            file_path, _ = QFileDialog.getOpenFileName(
                self,
                "导入图表配置",
                "",
                "JSON文件 (*.json);;所有文件 (*)"
            )

            if file_path:
                config = self.chart_widget.import_config(file_path)
                self.chart_widget.load_config(config)
                self.chart_widget.refresh_chart()
                QMessageBox.information(self, "成功", "配置已导入！")
        except Exception as e:
            QMessageBox.warning(self, "失败", f"导入配置失败：{str(e)}")

    def restore_default_config(self):
        """恢复默认配置"""
        reply = QMessageBox.question(
            self,
            "确认恢复",
            "确定要恢复系统默认配置吗？",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )

        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.chart_widget.restore_default_config()
                self.chart_widget.refresh_chart()
                QMessageBox.information(self, "成功", "已恢复系统默认配置！")
            except Exception as e:
                QMessageBox.warning(self, "失败", f"恢复默认配置失败：{str(e)}")
