from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QDialog, QWidget, QGridLayout, QVBoxLayout, QLabel, QCheckBox, QDialogButtonBox
from loguru import logger
#logger = logger.bind(category="deep_camera_logger")
class RunningCagesDialog(QDialog):
    """
    弹出框：设定运行的笼笼（默认 8 个鼠笼均运行）。
    返回结果示例:
        {
            "selected_indices": [0,1,2,3,4,5,6,7],  # 运行的笼笼索引，0-based
            "count": 8,                               # 运行的笼笼数量
            "all_selected": True                       # 是否所有笼笼都选中
        }
    调用：
        dlg = RunningCagesDialog(parent)
        if dlg.exec() == QDialog.DialogCode.Accepted:
            result = dlg.result_data  # 获取结果字典
    """
    def __init__(self, parent: QWidget | None = None, total_cages: int = 8):
        super().__init__(parent)
        self.setWindowTitle("UFC启动-设定运行的鼠笼")
        # self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint)
        self.setModal(True)
        self.total_cages = max(1, int(total_cages))
        self._result_data = None

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)

        info_label = QLabel("选择要运行的鼠笼（默认勾选全部）。")
        info_label.setWordWrap(True)
        layout.addWidget(info_label)

        # 网格布局放置 8 个复选框
        grid = QGridLayout()
        self._checks = []
        cols = 4  # 每行 4 个
        for i in range(self.total_cages):
            cb = QCheckBox(f"鼠笼 {i+1}")
            cb.setChecked(True)  # 默认全部勾选
            self._checks.append(cb)
            row = i // cols
            col = i % cols
            grid.addWidget(cb, row, col)
        layout.addLayout(grid)

        # 底部按钮
        self.button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.button_box.accepted.connect(self._on_accept)
        self.button_box.rejected.connect(self.reject)
        layout.addWidget(self.button_box)

        self.setLayout(layout)

    def _on_accept(self):
        selected = [idx for idx, cb in enumerate(self._checks) if cb.isChecked()]
        result = {
            "selected_indices": selected,
            "count": len(selected),
            "all_selected": len(selected) == self.total_cages
        }
        self._result_data = result
        self.accept()

    @property
    def result_data(self):
        return self._result_data