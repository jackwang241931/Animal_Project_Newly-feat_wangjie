import copy
import json
import os
from collections import defaultdict

from PyQt6.QtCore import QByteArray, QBuffer, QIODevice, QPointF, QRectF, QSize, Qt
from PyQt6.QtGui import QColor, QIcon, QPainter, QPainterPath, QPen, QPixmap, QPolygonF
from PyQt6.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)
from loguru import logger

from Module.coord_3d.core.math_engine import (
    apply_homography,
    compute_grid_3d_coords,
    get_perspective_transform,
    interpolate_grid,
    is_point_in_polygon,
    normalize_scene_config,
    solve_camera_matrix,
    solve_height_with_xyz,
    solve_x_with_yz,
    solve_xy_with_z,
)
from Module.coord_3d.ui.canvas_3d import Canvas3D
from Module.coord_3d.ui.dashboard_2d import Dashboard2D
from Module.coord_3d.ui.image_canvas import ImageCanvas
from theme.ThemeQt6 import ThemedWindow


class Coord3DIndex(ThemedWindow):
    HISTORY_LIMIT = 30

    def __init__(self, parent=None):
        super().__init__()
        self._view_mode = "topdown"
        self._tool_mode = "add"
        self._image_meta = {"fileName": "", "width": 0, "height": 0, "sourcePath": ""}
        self._scene_config = normalize_scene_config()
        self._near_comp = 0.0
        self._depth_comp = 40.0
        self._history: list[dict] = []
        self._suspend_history = False
        self._dirty = False
        self._last_project_path = ""

        self._init_ui()
        self._init_customize_ui()
        self._init_function()
        self._init_style_sheet()

    # ------------------------------------------------------------------ UI
    def _init_ui(self):
        self.setWindowTitle("三维坐标标定")

        central = QWidget()
        self.setCentralWidget(central)
        root = QHBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(10)

        self._sidebar = QFrame()
        self._sidebar.setObjectName("sidebar")
        self._sidebar.setFixedWidth(342)
        sidebar_root = QVBoxLayout(self._sidebar)
        sidebar_root.setContentsMargins(0, 0, 0, 0)
        sidebar_root.setSpacing(0)

        self._sidebar_scroll = QScrollArea()
        self._sidebar_scroll.setObjectName("sidebarScroll")
        self._sidebar_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._sidebar_scroll.setWidgetResizable(True)
        self._sidebar_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sidebar_root.addWidget(self._sidebar_scroll)

        self._sidebar_content = QWidget()
        self._sidebar_scroll.setWidget(self._sidebar_content)
        sidebar_layout = QVBoxLayout(self._sidebar_content)
        sidebar_layout.setContentsMargins(14, 18, 14, 16)
        sidebar_layout.setSpacing(10)
        self._sidebar_layout = sidebar_layout

        header = QFrame()
        header.setObjectName("headerCard")
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(12, 12, 12, 12)
        header_layout.setSpacing(10)
        self._brand_icon = QLabel()
        self._brand_icon.setObjectName("brandIcon")
        brand_text = QVBoxLayout()
        brand_text.setContentsMargins(0, 0, 0, 0)
        brand_text.setSpacing(2)
        brand_text.addWidget(self._title_label("3D 标定构建系统"))
        brand_text.addWidget(self._subtitle_label("DLT + 2D Interp Hybrid"))
        header_layout.addWidget(self._brand_icon)
        header_layout.addLayout(brand_text, 1)
        sidebar_layout.addWidget(header)

        project_card = self._create_card(sidebar_layout, "")
        project_head = QHBoxLayout()
        project_head.setContentsMargins(0, 0, 0, 0)
        project_head.setSpacing(8)
        project_head.addWidget(self._section_label("工程文件"), 1)
        self._btn_clear_all = QPushButton("清空项目")
        self._btn_clear_all.setObjectName("dangerMiniBtn")
        project_head.addWidget(self._btn_clear_all)
        project_card.addLayout(project_head)

        self._btn_load_image = QPushButton("载入底图")
        self._btn_load_image.setObjectName("bigActionBtn")
        self._btn_import_json = QPushButton("导入 JSON")
        self._btn_import_json.setObjectName("bigActionBtn")
        self._btn_import_project = QPushButton("导入项目")
        self._btn_import_project.setObjectName("subtleActionBtn")
        self._btn_export_project = QPushButton("导出项目")
        self._btn_export_project.setObjectName("subtleActionBtn")
        for btn in (
            self._btn_load_image,
            self._btn_import_json,
            self._btn_import_project,
            self._btn_export_project,
        ):
            project_card.addWidget(btn)
        self._btn_import_project.setVisible(False)
        self._btn_export_project.setVisible(False)

        mode_card = self._create_card(sidebar_layout, "工作模式")
        self._mode_btns = {}
        mode_switch = QHBoxLayout()
        mode_switch.setContentsMargins(0, 0, 0, 0)
        mode_switch.setSpacing(5)
        for key, text in (("topdown", "1. 俯视"), ("perspective", "2. 侧视")):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setObjectName("segmentBtn")
            self._mode_btns[key] = btn
            mode_switch.addWidget(btn)
        mode_card.addLayout(mode_switch)

        for key, text, obj_name in (
            ("region", "3. 区域高度设置", "regionModeBtn"),
            ("3d", "4. 构建 3D 预览", "previewModeBtn"),
            ("chart", "5. 2D 轨迹分析面板", "chartModeBtn"),
        ):
            btn = QPushButton(text)
            btn.setCheckable(True)
            btn.setObjectName(obj_name)
            self._mode_btns[key] = btn
            mode_card.addWidget(btn)

        self._layer_widget = QFrame()
        self._layer_widget.setObjectName("card")
        layer_layout = QVBoxLayout(self._layer_widget)
        layer_layout.setContentsMargins(12, 10, 12, 12)
        layer_layout.setSpacing(8)
        self._layer_combo = QComboBox()
        self._layer_combo.setObjectName("layerCombo")
        self._layer_combo.addItems(["标签 1 (地面)", "标签 2 (近端)", "标签 3 (中段)", "标签 4 (远端)"])
        layer_layout.addWidget(self._layer_combo)

        layer_row = QHBoxLayout()
        layer_row.setSpacing(8)
        self._spin_cols = QSpinBox()
        self._spin_rows = QSpinBox()
        self._spin_cols.setRange(2, 200)
        self._spin_rows.setRange(2, 200)
        self._spin_cols.setValue(18)
        self._spin_rows.setValue(31)
        self._spin_cols.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        self._spin_rows.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        layer_row.addLayout(self._labeled_field("物理列数 (X)", self._spin_cols), 1)
        layer_row.addLayout(self._labeled_field("物理行数 (Y)", self._spin_rows), 1)
        layer_layout.addLayout(layer_row)
        sidebar_layout.addWidget(self._layer_widget)

        tool_card = self._create_card(sidebar_layout, "补全与工具")
        tool_grid = QGridLayout()
        tool_grid.setContentsMargins(0, 0, 0, 0)
        tool_grid.setHorizontalSpacing(6)
        tool_grid.setVerticalSpacing(8)
        self._tool_btns = {}
        for key, text, icon_key in (
            ("add", "打点", "add"),
            ("move", "移动", "move"),
            ("delete", "删除", "delete"),
            ("pan", "平移", "pan"),
            ("yolo", "YOLO", "target"),
        ):
            self._tool_btns[key] = self._create_tool_button(text, icon_key)
        order = ["add", "move", "delete", "pan", "yolo"]
        for index, key in enumerate(order):
            tool_grid.addWidget(self._tool_btns[key], 0, index)
        tool_card.addLayout(tool_grid)

        layer_action = QHBoxLayout()
        layer_action.setContentsMargins(0, 6, 0, 0)
        layer_action.setSpacing(6)
        self._btn_complete = QPushButton("补全图层 1")
        self._btn_complete.setObjectName("accentBtn")
        self._btn_clear_layer = QPushButton("⌫")
        self._btn_clear_layer.setObjectName("miniGhostBtn")
        layer_action.addWidget(self._btn_complete, 1)
        layer_action.addWidget(self._btn_clear_layer)
        tool_card.addLayout(layer_action)

        self._comp_widget = QFrame()
        self._comp_widget.setObjectName("cyanCard")
        comp_layout = QVBoxLayout(self._comp_widget)
        comp_layout.setContentsMargins(12, 12, 12, 12)
        comp_layout.setSpacing(8)
        self._spin_near = QDoubleSpinBox()
        self._spin_depth_comp = QDoubleSpinBox()
        self._spin_near.setRange(-500, 500)
        self._spin_depth_comp.setRange(-500, 500)
        self._spin_near.setDecimals(1)
        self._spin_depth_comp.setDecimals(1)
        self._spin_near.setValue(self._near_comp)
        self._spin_depth_comp.setValue(self._depth_comp)
        self._spin_near.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self._spin_depth_comp.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self._near_label = QLabel("近端 Y 轴补偿 (向前:-Y)")
        self._near_label.setObjectName("cyanTitle")
        self._depth_label = QLabel("远端 Y 轴补偿 (向深:+Y)")
        self._depth_label.setObjectName("cyanTitle")
        comp_layout.addWidget(self._near_label)
        comp_layout.addWidget(self._spin_near)
        comp_layout.addWidget(self._depth_label)
        comp_layout.addWidget(self._spin_depth_comp)
        sidebar_layout.addWidget(self._comp_widget)

        self._region_widget = QFrame()
        self._region_widget.setObjectName("card")
        region_layout = QVBoxLayout(self._region_widget)
        region_layout.setContentsMargins(12, 12, 12, 12)
        region_layout.setSpacing(8)
        region_layout.addWidget(self._section_label("区域高度设置"))
        self._region_name = QLineEdit("测试区域")
        self._region_height = QDoubleSpinBox()
        self._region_height.setRange(0, 5000)
        self._region_height.setDecimals(1)
        self._region_height.setValue(50.0)
        self._region_height.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        self._region_y = QLineEdit("180")
        self._btn_finish_region = QPushButton("保存并闭合区域")
        self._btn_finish_region.setObjectName("subtleActionBtn")
        region_layout.addWidget(QLabel("名称"))
        region_layout.addWidget(self._region_name)
        region_layout.addWidget(QLabel("基准高度 Z(mm)"))
        region_layout.addWidget(self._region_height)
        region_layout.addWidget(QLabel("锁定 Y(mm，可选)"))
        region_layout.addWidget(self._region_y)
        region_layout.addWidget(self._btn_finish_region)
        sidebar_layout.addWidget(self._region_widget)

        self._scene_card = self._create_card(sidebar_layout, "物理尺寸")
        scene_row = QHBoxLayout()
        scene_row.setContentsMargins(0, 0, 0, 0)
        scene_row.setSpacing(8)
        self._spin_width = QDoubleSpinBox()
        self._spin_depth = QDoubleSpinBox()
        self._spin_height = QDoubleSpinBox()
        for spin, value, max_val in (
            (self._spin_width, self._scene_config["width"], 2000),
            (self._spin_depth, self._scene_config["depth"], 4000),
            (self._spin_height, self._scene_config["height"], 2000),
        ):
            spin.setRange(10, max_val)
            spin.setDecimals(1)
            spin.setValue(value)
            spin.setButtonSymbols(QDoubleSpinBox.ButtonSymbols.NoButtons)
        scene_row.addLayout(self._labeled_field("宽", self._spin_width), 1)
        scene_row.addLayout(self._labeled_field("深", self._spin_depth), 1)
        scene_row.addLayout(self._labeled_field("高", self._spin_height), 1)
        self._scene_card.addLayout(scene_row)
        self._scene_card.parentWidget().setVisible(False)

        view_card = self._create_card(sidebar_layout, "")
        opacity_head = QHBoxLayout()
        opacity_head.setContentsMargins(0, 0, 0, 0)
        opacity_head.addWidget(QLabel("全局透明度"), 1)
        self._opacity_value = QLabel("80%")
        self._opacity_value.setObjectName("tinyValue")
        opacity_head.addWidget(self._opacity_value)
        view_card.addLayout(opacity_head)
        self._opacity_slider = QSlider(Qt.Orientation.Horizontal)
        self._opacity_slider.setRange(0, 100)
        self._opacity_slider.setValue(80)
        view_card.addWidget(self._opacity_slider)
        self._btn_refresh_view = QPushButton("刷新预览")
        self._btn_reset_3d = QPushButton("重置 3D 视角")
        self._btn_undo = QPushButton("撤销上一步")
        for btn in (self._btn_refresh_view, self._btn_reset_3d, self._btn_undo):
            btn.setObjectName("subtleActionBtn")
            view_card.addWidget(btn)
        self._btn_refresh_view.setVisible(False)
        self._btn_reset_3d.setVisible(False)
        self._btn_undo.setVisible(False)

        self._btn_export_current = QPushButton("导出点位 JSON (标签 1)")
        self._btn_export_current.setObjectName("exportBtn")
        sidebar_layout.addSpacing(6)
        sidebar_layout.addWidget(self._btn_export_current)
        sidebar_layout.addSpacing(56)
        sidebar_layout.addStretch()

        root.addWidget(self._sidebar)

        self._stack = QStackedWidget()
        self._stack.setObjectName("workspace")
        self._canvas = ImageCanvas()
        self._canvas3d = Canvas3D()
        self._dashboard2d = Dashboard2D()
        self._stack.addWidget(self._canvas)
        self._stack.addWidget(self._canvas3d)
        self._stack.addWidget(self._dashboard2d)
        root.addWidget(self._stack, 1)

    def _title_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sidebarTitle")
        return label

    def _create_tool_button(self, text: str, icon_key: str) -> QToolButton:
        button = QToolButton()
        button.setText(text)
        button.setCheckable(True)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setIcon(self._build_icon(icon_key, "#8ea1bf", 18))
        button.setIconSize(QSize(18, 18))
        button.setObjectName("toolBtn")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def _subtitle_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sidebarSub")
        label.setWordWrap(True)
        return label

    def _section_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("sectionLabel")
        return label

    def _create_card(self, parent_layout: QVBoxLayout, title: str) -> QVBoxLayout:
        frame = QFrame()
        frame.setObjectName("card")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(12, 10, 12, 12)
        layout.setSpacing(8)
        if title:
            layout.addWidget(self._section_label(title))
        parent_layout.addWidget(frame)
        return layout

    def _labeled_field(self, text: str, widget) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(text)
        label.setObjectName("tinyLabel")
        layout.addWidget(label)
        layout.addWidget(widget)
        return layout

    def _init_customize_ui(self):
        self._apply_icons()
        self._canvas.point_opacity = self._opacity_slider.value() / 100.0
        self._canvas.active_label = "1"
        self._canvas3d.set_scene_config(self._scene_config)
        self._dashboard2d.set_scene_config(self._scene_config)
        self._set_mode("topdown")
        self._set_tool("add")
        self._update_dynamic_button_text()
        self._capture_snapshot()
        self._mark_dirty(False)
        self._refresh_ui_state()

    def _init_function(self):
        for key, button in self._mode_btns.items():
            button.clicked.connect(lambda _, mode_key=key: self._set_mode(mode_key))
        for key, button in self._tool_btns.items():
            button.clicked.connect(lambda _, tool_key=key: self._set_tool(tool_key))

        self._btn_load_image.clicked.connect(self._load_image)
        self._btn_import_project.clicked.connect(self._load_project_json)
        self._btn_import_json.clicked.connect(self._load_json)
        self._btn_export_project.clicked.connect(self._export_project)
        self._btn_export_current.clicked.connect(self._export_json)
        self._btn_complete.clicked.connect(self._auto_complete)
        self._btn_clear_layer.clicked.connect(self._clear_layer)
        self._btn_clear_all.clicked.connect(self._clear_all)
        self._btn_undo.clicked.connect(self._undo_last_action)
        self._btn_finish_region.clicked.connect(self._finish_region)
        self._btn_refresh_view.clicked.connect(self._refresh_views)
        self._btn_reset_3d.clicked.connect(self._reset_3d_view)

        self._opacity_slider.valueChanged.connect(self._on_opacity)
        self._layer_combo.currentIndexChanged.connect(self._on_layer_changed)
        self._spin_cols.valueChanged.connect(lambda _: self._on_parameter_changed())
        self._spin_rows.valueChanged.connect(lambda _: self._on_parameter_changed())
        self._spin_near.valueChanged.connect(self._on_compensation_changed)
        self._spin_depth_comp.valueChanged.connect(self._on_compensation_changed)
        self._spin_width.valueChanged.connect(self._on_scene_changed)
        self._spin_depth.valueChanged.connect(self._on_scene_changed)
        self._spin_height.valueChanged.connect(self._on_scene_changed)
        self._canvas.state_changed.connect(self._on_canvas_state_changed)

    def _init_style_sheet(self):
        self.setStyleSheet(
            """
            QWidget {
                font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
            }
            QFrame#sidebar {
                background: #10182b;
                border: 1px solid #202a3f;
                border-radius: 18px;
            }
            QScrollArea#sidebarScroll {
                background: transparent;
                border: none;
            }
            QStackedWidget#workspace {
                background: #04091a;
                border: 1px solid #1a2440;
                border-radius: 18px;
            }
            QFrame#headerCard {
                background: #121c31;
                border: 1px solid #233252;
                border-radius: 16px;
            }
            QFrame#card {
                background: #131d33;
                border: 1px solid #24304a;
                border-radius: 14px;
            }
            QFrame#cyanCard {
                background: #12273d;
                border: 1px solid #0e5a86;
                border-radius: 14px;
            }
            QLabel#brandIcon {
                min-width: 44px;
                max-width: 44px;
                min-height: 44px;
                max-height: 44px;
                border-radius: 11px;
                background: #1f3564;
                qproperty-alignment: AlignCenter;
            }
            QLabel#sidebarTitle {
                color: #f4f7ff;
                font-size: 14px;
                font-weight: 700;
            }
            QLabel#sidebarSub {
                color: #7b8cab;
                font-size: 10px;
            }
            QLabel#sectionLabel {
                color: #7083a6;
                font-size: 11px;
                font-weight: 600;
            }
            QLabel#tinyLabel {
                color: #667a9f;
                font-size: 10px;
            }
            QLabel#tinyValue {
                color: #d5e2ff;
                font-size: 10px;
                font-weight: 700;
            }
            QLabel#cyanTitle {
                color: #2fdcff;
                font-size: 11px;
                font-weight: 700;
            }
            QLabel {
                color: #cbd7ef;
            }
            QPushButton {
                background: #243148;
                color: #dce7fb;
                border: 1px solid #31405c;
                border-radius: 12px;
                padding: 8px 10px;
            }
            QPushButton:hover {
                background: #2a3955;
            }
            QPushButton:checked {
                background: #51627d;
                border-color: #657690;
                color: #ffffff;
            }
            QPushButton#bigActionBtn {
                min-height: 34px;
                text-align: left;
                padding-left: 12px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton#subtleActionBtn {
                min-height: 30px;
                font-size: 10px;
            }
            QPushButton#dangerMiniBtn {
                min-height: 26px;
                padding: 4px 8px;
                border-radius: 8px;
                background: #3a2030;
                border: 1px solid #5f3147;
                color: #ff8ba5;
                font-size: 10px;
            }
            QPushButton#dangerMiniBtn:hover {
                background: #48263a;
            }
            QPushButton#segmentBtn {
                min-height: 32px;
                border-radius: 10px;
                font-size: 11px;
            }
            QPushButton#regionModeBtn {
                min-height: 34px;
                background: #2a2147;
                border: 1px solid #4a3586;
                color: #cf8cff;
                font-weight: 700;
                text-align: left;
                padding-left: 14px;
            }
            QPushButton#regionModeBtn:checked {
                background: #42316f;
                color: #f2dcff;
            }
            QPushButton#previewModeBtn {
                min-height: 34px;
                background: #21294e;
                border: 1px solid #33458f;
                color: #7f93ff;
                font-weight: 700;
                text-align: left;
                padding-left: 14px;
            }
            QPushButton#previewModeBtn:checked {
                background: #33417c;
                color: #d8e0ff;
            }
            QPushButton#chartModeBtn {
                min-height: 34px;
                background: #341f3f;
                border: 1px solid #67346d;
                color: #ff70b4;
                font-weight: 700;
                text-align: left;
                padding-left: 14px;
            }
            QPushButton#chartModeBtn:checked {
                background: #4a2d58;
                color: #ffd0e6;
            }
            QPushButton#toolBtn {
                min-width: 50px;
                min-height: 50px;
                max-width: 50px;
                max-height: 50px;
                border-radius: 11px;
                font-size: 10px;
                font-weight: 700;
            }
            QToolButton#toolBtn {
                min-width: 50px;
                min-height: 50px;
                max-width: 50px;
                max-height: 50px;
                border-radius: 11px;
                font-size: 10px;
                font-weight: 700;
                color: #dce7fb;
                background: #243148;
                border: 1px solid #31405c;
                padding: 4px 1px 3px 1px;
            }
            QToolButton#toolBtn:hover {
                background: #2a3955;
            }
            QToolButton#toolBtn:checked {
                background: #1e4785;
                border-color: #3b82f6;
                color: #eef5ff;
            }
            QPushButton#accentBtn {
                min-height: 34px;
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #842df3, stop:1 #b14fff);
                border: 1px solid #8a48e2;
                color: white;
                font-size: 11px;
                font-weight: 700;
            }
            QPushButton#miniGhostBtn {
                min-width: 38px;
                max-width: 38px;
                min-height: 34px;
                max-height: 34px;
                background: #2f2337;
                border: 1px solid #7a3248;
                color: #ff728c;
                font-size: 14px;
                font-weight: 700;
            }
            QPushButton#exportBtn {
                min-height: 36px;
                background: #14a06f;
                border: 1px solid #1cbc83;
                border-radius: 14px;
                color: white;
                font-size: 12px;
                font-weight: 700;
            }
            QPushButton#exportBtn:hover {
                background: #17b27c;
            }
            QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {
                background: #182338;
                color: #63a6ff;
                border: 1px solid #30415f;
                border-radius: 8px;
                padding: 5px 8px;
                min-height: 22px;
            }
            QComboBox#layerCombo {
                min-height: 32px;
                font-weight: 700;
                color: #67b0ff;
                border-radius: 12px;
                padding-left: 10px;
            }
            QFrame#sidebar QWidget {
                background-clip: padding;
            }
            QSlider::groove:horizontal {
                background: #2a3751;
                height: 6px;
                border-radius: 3px;
            }
            QSlider::handle:horizontal {
                background: #17bf95;
                width: 18px;
                margin: -6px 0;
                border-radius: 9px;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 10px;
                margin: 6px 0 6px 0;
            }
            QScrollBar::handle:vertical {
                background: #67758f;
                border-radius: 5px;
                min-height: 24px;
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            """
        )

    def _apply_icons(self):
        self._brand_icon.setPixmap(self._build_icon_pixmap("grid", "#76a8ff", 24))

        self._btn_load_image.setIcon(self._build_icon("image", "#8ea1bf", 18))
        self._btn_import_json.setIcon(self._build_icon("json", "#8ea1bf", 18))
        self._btn_import_project.setIcon(self._build_icon("folder", "#8ea1bf", 18))
        self._btn_export_project.setIcon(self._build_icon("save", "#8ea1bf", 18))
        self._btn_clear_all.setIcon(self._build_icon("delete", "#ff8ba5", 14))

        self._mode_btns["region"].setIcon(self._build_icon("region", "#cf8cff", 16))
        self._mode_btns["3d"].setIcon(self._build_icon("cube", "#8ea2ff", 16))
        self._mode_btns["chart"].setIcon(self._build_icon("chart", "#ff70b4", 16))

        self._btn_complete.setIcon(self._build_icon("spark", "#ffffff", 16))
        self._btn_clear_layer.setIcon(self._build_icon("eraser", "#ff728c", 16))
        self._btn_clear_layer.setText("")
        self._btn_export_current.setIcon(self._build_icon("export", "#ffffff", 18))

    def _build_icon(self, kind: str, color: str | QColor, size: int) -> QIcon:
        return QIcon(self._build_icon_pixmap(kind, color, size))

    def _build_icon_pixmap(self, kind: str, color: str | QColor, size: int) -> QPixmap:
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)

        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        pen = QPen(QColor(color), max(1.5, size / 12.0), Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if kind == "grid":
            rect = QRectF(3, 3, size - 6, size - 6)
            painter.drawRoundedRect(rect, 3, 3)
            step = rect.width() / 3.0
            for i in range(1, 3):
                x = rect.left() + step * i
                y = rect.top() + step * i
                painter.drawLine(QPointF(x, rect.top()), QPointF(x, rect.bottom()))
                painter.drawLine(QPointF(rect.left(), y), QPointF(rect.right(), y))
        elif kind == "image":
            frame = QRectF(3, 4, size - 6, size - 8)
            painter.drawRoundedRect(frame, 2.5, 2.5)
            painter.drawEllipse(QPointF(size * 0.38, size * 0.38), size * 0.09, size * 0.09)
            painter.drawLine(QPointF(size * 0.26, size * 0.72), QPointF(size * 0.47, size * 0.5))
            painter.drawLine(QPointF(size * 0.47, size * 0.5), QPointF(size * 0.62, size * 0.62))
            painter.drawLine(QPointF(size * 0.62, size * 0.62), QPointF(size * 0.78, size * 0.34))
        elif kind == "json":
            frame = QRectF(4, 3, size - 8, size - 6)
            painter.drawRoundedRect(frame, 2.5, 2.5)
            painter.drawLine(QPointF(size * 0.4, size * 0.22), QPointF(size * 0.58, size * 0.22))
            painter.drawLine(QPointF(size * 0.6, size * 0.22), QPointF(size * 0.78, size * 0.4))
            painter.drawLine(QPointF(size * 0.63, size * 0.22), QPointF(size * 0.63, size * 0.4))
            painter.drawLine(QPointF(size * 0.26, size * 0.35), QPointF(size * 0.18, size * 0.5))
            painter.drawLine(QPointF(size * 0.18, size * 0.5), QPointF(size * 0.26, size * 0.65))
            painter.drawLine(QPointF(size * 0.74, size * 0.35), QPointF(size * 0.82, size * 0.5))
            painter.drawLine(QPointF(size * 0.82, size * 0.5), QPointF(size * 0.74, size * 0.65))
        elif kind == "folder":
            path = QPainterPath()
            path.moveTo(size * 0.12, size * 0.35)
            path.lineTo(size * 0.35, size * 0.35)
            path.lineTo(size * 0.44, size * 0.22)
            path.lineTo(size * 0.88, size * 0.22)
            path.lineTo(size * 0.88, size * 0.78)
            path.lineTo(size * 0.12, size * 0.78)
            path.closeSubpath()
            painter.drawPath(path)
        elif kind == "save":
            rect = QRectF(3, 3, size - 6, size - 6)
            painter.drawRoundedRect(rect, 2.5, 2.5)
            painter.drawLine(QPointF(size * 0.28, size * 0.3), QPointF(size * 0.72, size * 0.3))
            painter.drawLine(QPointF(size * 0.3, size * 0.3), QPointF(size * 0.3, size * 0.55))
            painter.drawLine(QPointF(size * 0.7, size * 0.3), QPointF(size * 0.7, size * 0.55))
            painter.drawRect(QRectF(size * 0.34, size * 0.56, size * 0.32, size * 0.18))
        elif kind == "region":
            poly = QPolygonF(
                [
                    QPointF(size * 0.22, size * 0.25),
                    QPointF(size * 0.45, size * 0.18),
                    QPointF(size * 0.77, size * 0.32),
                    QPointF(size * 0.72, size * 0.75),
                    QPointF(size * 0.34, size * 0.82),
                    QPointF(size * 0.18, size * 0.48),
                ]
            )
            painter.drawPolygon(poly)
        elif kind == "cube":
            front = QPolygonF(
                [
                    QPointF(size * 0.28, size * 0.36),
                    QPointF(size * 0.52, size * 0.22),
                    QPointF(size * 0.76, size * 0.36),
                    QPointF(size * 0.52, size * 0.5),
                ]
            )
            back = QPolygonF(
                [
                    QPointF(size * 0.28, size * 0.36),
                    QPointF(size * 0.28, size * 0.64),
                    QPointF(size * 0.52, size * 0.78),
                    QPointF(size * 0.52, size * 0.5),
                ]
            )
            side = QPolygonF(
                [
                    QPointF(size * 0.76, size * 0.36),
                    QPointF(size * 0.76, size * 0.64),
                    QPointF(size * 0.52, size * 0.78),
                    QPointF(size * 0.52, size * 0.5),
                ]
            )
            painter.drawPolygon(front)
            painter.drawPolygon(back)
            painter.drawPolygon(side)
        elif kind == "chart":
            painter.drawLine(QPointF(size * 0.16, size * 0.62), QPointF(size * 0.34, size * 0.62))
            painter.drawLine(QPointF(size * 0.34, size * 0.62), QPointF(size * 0.48, size * 0.28))
            painter.drawLine(QPointF(size * 0.48, size * 0.28), QPointF(size * 0.64, size * 0.74))
            painter.drawLine(QPointF(size * 0.64, size * 0.74), QPointF(size * 0.84, size * 0.42))
        elif kind == "add":
            painter.drawEllipse(QRectF(3, 3, size - 6, size - 6))
            painter.drawLine(QPointF(size * 0.5, size * 0.28), QPointF(size * 0.5, size * 0.72))
            painter.drawLine(QPointF(size * 0.28, size * 0.5), QPointF(size * 0.72, size * 0.5))
        elif kind == "move":
            path = QPainterPath()
            path.moveTo(size * 0.26, size * 0.2)
            path.lineTo(size * 0.74, size * 0.44)
            path.lineTo(size * 0.52, size * 0.52)
            path.lineTo(size * 0.46, size * 0.78)
            path.closeSubpath()
            painter.drawPath(path)
        elif kind == "delete":
            painter.drawLine(QPointF(size * 0.3, size * 0.24), QPointF(size * 0.7, size * 0.24))
            painter.drawLine(QPointF(size * 0.38, size * 0.18), QPointF(size * 0.62, size * 0.18))
            painter.drawRoundedRect(QRectF(size * 0.26, size * 0.28, size * 0.48, size * 0.48), 2.5, 2.5)
            painter.drawLine(QPointF(size * 0.42, size * 0.38), QPointF(size * 0.42, size * 0.66))
            painter.drawLine(QPointF(size * 0.58, size * 0.38), QPointF(size * 0.58, size * 0.66))
        elif kind == "pan":
            center = QPointF(size * 0.5, size * 0.5)
            painter.drawLine(QPointF(center.x(), size * 0.18), QPointF(center.x(), size * 0.82))
            painter.drawLine(QPointF(size * 0.18, center.y()), QPointF(size * 0.82, center.y()))
            painter.drawLine(QPointF(center.x(), size * 0.18), QPointF(size * 0.4, size * 0.3))
            painter.drawLine(QPointF(center.x(), size * 0.18), QPointF(size * 0.6, size * 0.3))
            painter.drawLine(QPointF(center.x(), size * 0.82), QPointF(size * 0.4, size * 0.7))
            painter.drawLine(QPointF(center.x(), size * 0.82), QPointF(size * 0.6, size * 0.7))
            painter.drawLine(QPointF(size * 0.18, center.y()), QPointF(size * 0.3, size * 0.4))
            painter.drawLine(QPointF(size * 0.18, center.y()), QPointF(size * 0.3, size * 0.6))
            painter.drawLine(QPointF(size * 0.82, center.y()), QPointF(size * 0.7, size * 0.4))
            painter.drawLine(QPointF(size * 0.82, center.y()), QPointF(size * 0.7, size * 0.6))
        elif kind == "target":
            painter.drawEllipse(QRectF(3, 3, size - 6, size - 6))
            painter.drawEllipse(QRectF(size * 0.28, size * 0.28, size * 0.44, size * 0.44))
            painter.drawEllipse(QRectF(size * 0.44, size * 0.44, size * 0.12, size * 0.12))
        elif kind == "eraser":
            poly = QPolygonF(
                [
                    QPointF(size * 0.28, size * 0.62),
                    QPointF(size * 0.48, size * 0.82),
                    QPointF(size * 0.8, size * 0.5),
                    QPointF(size * 0.6, size * 0.3),
                ]
            )
            painter.drawPolygon(poly)
            painter.drawLine(QPointF(size * 0.2, size * 0.72), QPointF(size * 0.54, size * 0.72))
        elif kind == "export":
            painter.drawLine(QPointF(size * 0.5, size * 0.18), QPointF(size * 0.5, size * 0.64))
            painter.drawLine(QPointF(size * 0.34, size * 0.48), QPointF(size * 0.5, size * 0.64))
            painter.drawLine(QPointF(size * 0.66, size * 0.48), QPointF(size * 0.5, size * 0.64))
            painter.drawLine(QPointF(size * 0.24, size * 0.78), QPointF(size * 0.76, size * 0.78))
            painter.drawLine(QPointF(size * 0.24, size * 0.78), QPointF(size * 0.24, size * 0.62))
            painter.drawLine(QPointF(size * 0.76, size * 0.78), QPointF(size * 0.76, size * 0.62))
        elif kind == "spark":
            painter.drawLine(QPointF(size * 0.18, size * 0.5), QPointF(size * 0.82, size * 0.5))
            painter.drawLine(QPointF(size * 0.5, size * 0.18), QPointF(size * 0.5, size * 0.82))
            painter.drawLine(QPointF(size * 0.28, size * 0.28), QPointF(size * 0.72, size * 0.72))
            painter.drawLine(QPointF(size * 0.72, size * 0.28), QPointF(size * 0.28, size * 0.72))

        painter.end()
        return pixmap

    def calculate_minimum_suggested_size(self):
        # 侧边栏本身可滚动，嵌入主界面时不应把整个页面最小高度抬到 1000+。
        return QSize(1100, 720)

    # ------------------------------------------------------------------ mode
    def _set_mode(self, mode: str):
        self._view_mode = mode
        for key, button in self._mode_btns.items():
            button.setChecked(key == mode)

        self._layer_widget.setVisible(mode in ("topdown", "perspective", "3d", "chart"))
        self._comp_widget.setVisible(mode in ("perspective", "3d", "chart"))
        self._region_widget.setVisible(mode == "region")
        self._canvas.tool_mode = "region" if mode == "region" else self._tool_mode

        if mode == "3d":
            self._stack.setCurrentIndex(1)
            self._refresh_3d()
        elif mode == "chart":
            self._stack.setCurrentIndex(2)
            self._dashboard2d.set_data(self._canvas.solved_yolo)
        else:
            self._stack.setCurrentIndex(0)

        self._refresh_ui_state()

    def _set_tool(self, tool: str):
        self._tool_mode = tool
        for key, button in self._tool_btns.items():
            button.setChecked(key == tool)
        if self._view_mode != "region":
            self._canvas.tool_mode = tool
        self._refresh_ui_state()

    # ------------------------------------------------------------------ state
    def _mark_dirty(self, dirty: bool = True):
        self._dirty = dirty
        title = "三维坐标标定"
        if self._last_project_path:
            title += f" - {os.path.basename(self._last_project_path)}"
        if dirty:
            title += " *"
        self.setWindowTitle(title)

    def _current_state(self, include_image: bool = True) -> dict:
        state = {
            "format": "Coord3D_Project_State",
            "version": 2,
            "view_mode": self._view_mode,
            "tool_mode": self._tool_mode,
            "active_label": str(self._layer_combo.currentIndex() + 1),
            "rows": self._spin_rows.value(),
            "cols": self._spin_cols.value(),
            "opacity": self._opacity_slider.value(),
            "near_comp": self._near_comp,
            "depth_comp": self._depth_comp,
            "scene": copy.deepcopy(self._scene_config),
            "image_meta": copy.deepcopy(self._image_meta),
            "points": copy.deepcopy(self._canvas.points),
            "grid_data": copy.deepcopy(self._canvas.grid_data),
            "regions": copy.deepcopy(self._canvas.regions),
            "current_region_pts": copy.deepcopy(self._canvas.current_region_pts),
            "yolo_boxes": copy.deepcopy(self._canvas.yolo_boxes),
            "solved_yolo": copy.deepcopy(self._canvas.solved_yolo),
        }
        if include_image:
            state["image_png_base64"] = self._encode_pixmap(self._canvas.get_pixmap())
        return state

    def _capture_snapshot(self):
        if self._suspend_history:
            return
        state = self._current_state(include_image=True)
        if self._history and self._history[-1] == state:
            return
        self._history.append(state)
        if len(self._history) > self.HISTORY_LIMIT:
            self._history = self._history[-self.HISTORY_LIMIT :]
        self._mark_dirty(True)

    def _restore_state(self, state: dict, from_history: bool = False):
        self._suspend_history = True
        try:
            self._scene_config = normalize_scene_config(state.get("scene"))
            self._near_comp = float(state.get("near_comp", 0.0))
            self._depth_comp = float(state.get("depth_comp", 40.0))
            self._image_meta = copy.deepcopy(state.get("image_meta", self._image_meta))

            self._spin_cols.setValue(int(state.get("cols", 18)))
            self._spin_rows.setValue(int(state.get("rows", 31)))
            self._opacity_slider.setValue(int(state.get("opacity", 80)))
            self._spin_near.setValue(self._near_comp)
            self._spin_depth_comp.setValue(self._depth_comp)
            self._spin_width.setValue(self._scene_config["width"])
            self._spin_depth.setValue(self._scene_config["depth"])
            self._spin_height.setValue(self._scene_config["height"])

            pixmap = self._decode_pixmap(state.get("image_png_base64", ""))
            if pixmap and not pixmap.isNull():
                self._canvas.set_image(pixmap, reset_view=True)
            else:
                self._canvas._pixmap = None

            self._canvas.points = copy.deepcopy(state.get("points", []))
            self._canvas.grid_data = copy.deepcopy(state.get("grid_data", []))
            self._canvas.regions = copy.deepcopy(state.get("regions", []))
            self._canvas.current_region_pts = copy.deepcopy(state.get("current_region_pts", []))
            self._canvas.yolo_boxes = copy.deepcopy(state.get("yolo_boxes", []))
            self._canvas.solved_yolo = copy.deepcopy(state.get("solved_yolo", []))
            self._canvas.point_opacity = self._opacity_slider.value() / 100.0

            active_label = state.get("active_label", "1")
            active_index = max(0, min(3, int(active_label) - 1 if str(active_label).isdigit() else 0))
            self._layer_combo.setCurrentIndex(active_index)
            self._canvas.active_label = str(active_index + 1)

            self._set_tool(state.get("tool_mode", "add"))
            self._set_mode(state.get("view_mode", "topdown"))
            self._refresh_views()
            self._canvas.update()
        finally:
            self._suspend_history = False

        if not from_history:
            self._mark_dirty(False)

    def _undo_last_action(self):
        if len(self._history) <= 1:
            QMessageBox.information(self, "提示", "当前没有可撤销的操作。")
            return
        self._history.pop()
        self._restore_state(copy.deepcopy(self._history[-1]), from_history=True)
        self._mark_dirty(True)

    # ------------------------------------------------------------------ files
    def _load_image(self):
        path, _ = QFileDialog.getOpenFileName(self, "载入底图", "", "图片 (*.png *.jpg *.bmp *.jpeg)")
        if not path:
            return

        pixmap = QPixmap(path)
        if pixmap.isNull():
            QMessageBox.warning(self, "错误", "图片加载失败。")
            return

        self._canvas.clear_all(keep_image=False)
        self._canvas.set_image(pixmap, reset_view=True)
        self._image_meta = {
            "fileName": path.replace("\\", "/").split("/")[-1],
            "width": pixmap.width(),
            "height": pixmap.height(),
            "sourcePath": path,
        }
        self._last_project_path = ""
        self._capture_snapshot()
        self._refresh_ui_state()

    def _load_project_json(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入项目", "", "JSON (*.json)")
        if not path:
            return
        try:
            data = self._read_json(path)
            if data.get("format") != "Coord3D_Project_State":
                QMessageBox.warning(self, "提示", "该文件不是 coord_3d 项目文件。")
                return
            self._last_project_path = path
            self._restore_state(data)
            self._history = [copy.deepcopy(self._current_state(include_image=True))]
            self._mark_dirty(False)
        except Exception as exc:
            logger.error(f"导入项目失败: {exc}")
            QMessageBox.warning(self, "错误", f"项目文件解析失败: {exc}")

    def _load_json(self):
        path, _ = QFileDialog.getOpenFileName(self, "导入 JSON", "", "JSON (*.json)")
        if not path:
            return
        try:
            data = self._read_json(path)
            self._import_data_object(data)
            self._capture_snapshot()
            self._refresh_ui_state()
        except Exception as exc:
            logger.error(f"导入 JSON 失败: {exc}")
            QMessageBox.warning(self, "错误", f"JSON 解析失败: {exc}")

    def _import_data_object(self, data: dict):
        fmt = data.get("format", "")
        if fmt == "Coord3D_Project_State":
            self._restore_state(data)
            return

        if "shapes" in data:
            self._canvas.clear_all(keep_image=True)
            points = []
            grid = []
            for shape in data.get("shapes", []):
                point = {
                    "id": shape.get("id") or f"pt_{len(points) + len(grid) + 1}",
                    "x": shape["points"][0][0],
                    "y": shape["points"][0][1],
                    "label": str(shape.get("label", "1")),
                }
                desc = shape.get("description", "")
                if desc.startswith("grid_"):
                    _, col, row = desc.split("_")
                    point["c"] = int(col)
                    point["r"] = int(row)
                    point["is_manual_covered"] = False
                    grid.append(point)
                else:
                    points.append(point)
            self._canvas.points = points
            self._canvas.grid_data = grid
            self._image_meta["width"] = int(data.get("imageWidth", self._image_meta["width"]))
            self._image_meta["height"] = int(data.get("imageHeight", self._image_meta["height"]))
            self._recompute_yolo()
            return

        if fmt == "Height_Constraint_Regions":
            self._canvas.regions = [
                {
                    "id": region.get("id", f"region_{index}"),
                    "name": region.get("name", f"区域 {index + 1}"),
                    "height": float(region.get("height_z", region.get("height", 0))),
                    "y_val": str(region.get("fixed_y", "")),
                    "points": [{"x": pt[0], "y": pt[1]} for pt in region.get("polygon_points", [])],
                }
                for index, region in enumerate(data.get("regions", []))
            ]
            self._set_mode("region")
            self._recompute_yolo()
            return

        if fmt == "2D_Trajectory_Analysis":
            self._canvas.solved_yolo = [
                {
                    "id": f"import_{index + 1}",
                    "X": float(point.get("X", 0)),
                    "Y": float(point.get("Y", 0)),
                    "Z_base": float(point.get("Z_base", 0)),
                    "mouseHeight": float(point.get("mouseHeight", 0)),
                    "Z_total": float(point.get("Z_total", 0)),
                    "cx": float(point.get("X", 0)),
                    "cy": float(point.get("Y", 0)),
                }
                for index, point in enumerate(data.get("points", []))
            ]
            self._dashboard2d.set_data(self._canvas.solved_yolo)
            self._set_mode("chart")
            return

        raise ValueError("暂不支持该 JSON 格式。")

    def _export_project(self):
        base_name = self._image_meta["fileName"].rsplit(".", 1)[0] or "coord3d_project"
        path, _ = QFileDialog.getSaveFileName(self, "导出项目", base_name + "_project.json", "JSON (*.json)")
        if not path:
            return
        try:
            payload = self._current_state(include_image=True)
            with open(path, "w", encoding="utf-8") as file:
                json.dump(payload, file, ensure_ascii=False, indent=2)
            self._last_project_path = path
            self._mark_dirty(False)
        except Exception as exc:
            logger.error(f"导出项目失败: {exc}")
            QMessageBox.warning(self, "错误", f"导出项目失败: {exc}")

    def _export_json(self):
        mode = self._view_mode
        label_index = self._layer_combo.currentIndex() + 1
        if mode == "chart":
            data = {
                "format": "2D_Trajectory_Analysis",
                "unit": "mm",
                "points": [
                    {
                        "index": index + 1,
                        "X": round(item["X"], 2),
                        "Y": round(item["Y"], 2),
                        "Z_base": round(item["Z_base"], 2),
                        "mouseHeight": round(item["mouseHeight"], 2),
                        "Z_total": round(item["Z_total"], 2),
                    }
                    for index, item in enumerate(self._canvas.solved_yolo)
                ],
            }
            suffix = "_trajectory.json"
        elif mode == "region":
            data = {
                "format": "Height_Constraint_Regions",
                "unit": "mm",
                "regions": [
                    {
                        "id": region["id"],
                        "name": region["name"],
                        "height_z": region["height"],
                        "fixed_y": region["y_val"],
                        "polygon_points": [[pt["x"], pt["y"]] for pt in region["points"]],
                    }
                    for region in self._canvas.regions
                ],
            }
            suffix = "_height_regions.json"
        elif mode == "3d":
            p3d = compute_grid_3d_coords(self._canvas.points, self._canvas.grid_data, self._scene_config)
            data = {
                "format": "3D_Point_Structure",
                "unit": "mm",
                "scene": self._scene_config,
                "count": len(p3d),
                "points": [
                    {
                        "label": point["label"],
                        "col": point.get("c"),
                        "row": point.get("r"),
                        "x": round(point["x3"], 2),
                        "y": round(point["y3"], 2),
                        "z": round(point["z3"], 2),
                    }
                    for point in p3d
                ],
            }
            suffix = "_3d_points.json"
        else:
            shapes = []
            active_label = str(label_index)
            for point in self._canvas.grid_data:
                if point.get("label") != active_label:
                    continue
                shapes.append(
                    {
                        "label": point["label"],
                        "points": [[round(point["x"], 2), round(point["y"], 2)]],
                        "description": f"grid_{point.get('c', 0)}_{point.get('r', 0)}",
                        "shape_type": "point",
                    }
                )
            for point in self._canvas.points:
                if point.get("label") != active_label:
                    continue
                shapes.append(
                    {
                        "label": point.get("label", "1"),
                        "points": [[round(point["x"], 2), round(point["y"], 2)]],
                        "description": "manual",
                        "shape_type": "point",
                    }
                )
            data = {
                "version": "5.0.1",
                "shapes": shapes,
                "imageWidth": self._image_meta["width"],
                "imageHeight": self._image_meta["height"],
            }
            suffix = f"_label_{label_index}_grid_points.json"

        base_name = self._image_meta["fileName"].rsplit(".", 1)[0] or "coord3d_export"
        path, _ = QFileDialog.getSaveFileName(self, "导出结果", base_name + suffix, "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
        except Exception as exc:
            logger.error(f"导出结果失败: {exc}")
            QMessageBox.warning(self, "错误", f"导出结果失败: {exc}")

    # ------------------------------------------------------------------ encode
    def _encode_pixmap(self, pixmap: QPixmap | None) -> str:
        if not pixmap or pixmap.isNull():
            return ""
        byte_array = QByteArray()
        buffer = QBuffer(byte_array)
        buffer.open(QIODevice.OpenModeFlag.WriteOnly)
        pixmap.save(buffer, "PNG")
        buffer.close()
        return bytes(byte_array.toBase64()).decode("ascii")

    def _decode_pixmap(self, payload: str) -> QPixmap | None:
        if not payload:
            return None
        data = QByteArray.fromBase64(payload.encode("ascii"))
        pixmap = QPixmap()
        pixmap.loadFromData(bytes(data), "PNG")
        return pixmap

    def _read_json(self, path: str) -> dict:
        for encoding in ("utf-8", "utf-8-sig", "gb18030", "gbk"):
            try:
                with open(path, "r", encoding=encoding) as file:
                    return json.load(file)
            except UnicodeDecodeError:
                continue
        with open(path, "r", encoding="utf-8", errors="ignore") as file:
            return json.load(file)

    # ------------------------------------------------------------------ actions
    def _on_layer_changed(self, index: int):
        self._canvas.active_label = str(index + 1)
        self._update_dynamic_button_text()
        self._refresh_ui_state()

    def _on_opacity(self, value: int):
        self._canvas.point_opacity = value / 100.0
        self._canvas.update()
        self._opacity_value.setText(f"{value}%")
        self._capture_snapshot()
        self._refresh_ui_state()

    def _on_compensation_changed(self):
        self._near_comp = self._spin_near.value()
        self._depth_comp = self._spin_depth_comp.value()
        self._recompute_yolo()
        self._refresh_views()
        self._capture_snapshot()

    def _on_scene_changed(self):
        self._scene_config = normalize_scene_config(
            {
                "width": self._spin_width.value(),
                "depth": self._spin_depth.value(),
                "height": self._spin_height.value(),
            }
        )
        self._canvas3d.set_scene_config(self._scene_config)
        self._dashboard2d.set_scene_config(self._scene_config)
        self._recompute_yolo()
        self._refresh_views()
        self._capture_snapshot()

    def _on_parameter_changed(self):
        self._update_dynamic_button_text()
        self._capture_snapshot()
        self._refresh_ui_state()

    def _on_canvas_state_changed(self):
        if self._suspend_history:
            return
        self._recompute_yolo()
        self._refresh_views()
        self._capture_snapshot()

    def _update_dynamic_button_text(self):
        label_index = self._layer_combo.currentIndex() + 1
        self._btn_complete.setText(f"补全图层 {label_index}")
        self._btn_export_current.setText(f"导出点位 JSON (标签 {label_index})")

    def _auto_complete(self):
        label = str(self._layer_combo.currentIndex() + 1)
        active_points = [point for point in self._canvas.points if point.get("label") == label]
        if len(active_points) < 4:
            QMessageBox.warning(self, "提示", f"图层 {label} 至少需要 4 个控制点。")
            return

        grid = interpolate_grid(active_points, self._spin_cols.value(), self._spin_rows.value(), label)
        self._canvas.grid_data = [point for point in self._canvas.grid_data if point.get("label") != label] + grid
        self._recompute_yolo()
        self._refresh_views()
        self._capture_snapshot()

    def _clear_layer(self):
        label = str(self._layer_combo.currentIndex() + 1)
        self._canvas.points = [point for point in self._canvas.points if point.get("label") != label]
        self._canvas.grid_data = [point for point in self._canvas.grid_data if point.get("label") != label]
        self._recompute_yolo()
        self._canvas.update()
        self._refresh_views()
        self._capture_snapshot()

    def _clear_all(self):
        self._canvas.clear_all(keep_image=False)
        self._image_meta = {"fileName": "", "width": 0, "height": 0, "sourcePath": ""}
        self._last_project_path = ""
        self._refresh_views()
        self._capture_snapshot()

    def _finish_region(self):
        if len(self._canvas.current_region_pts) < 3:
            QMessageBox.warning(self, "提示", "请至少绘制 3 个点后再保存区域。")
            return

        self._canvas.regions.append(
            {
                "id": f"region_{len(self._canvas.regions) + 1}",
                "name": self._region_name.text().strip() or f"区域 {len(self._canvas.regions) + 1}",
                "height": self._region_height.value(),
                "y_val": self._region_y.text().strip(),
                "points": copy.deepcopy(self._canvas.current_region_pts),
            }
        )
        self._canvas.current_region_pts = []
        self._recompute_yolo()
        self._canvas.update()
        self._refresh_views()
        self._capture_snapshot()

    def _reset_3d_view(self):
        self._canvas3d.reset_camera()

    # ------------------------------------------------------------------ solve
    def _refresh_views(self):
        self._canvas3d.set_scene_config(self._scene_config)
        self._dashboard2d.set_scene_config(self._scene_config)
        self._refresh_3d()
        self._dashboard2d.set_data(self._canvas.solved_yolo)
        self._refresh_ui_state()

    def _refresh_3d(self):
        points_3d = compute_grid_3d_coords(self._canvas.points, self._canvas.grid_data, self._scene_config)
        lines_3d = []
        by_label = defaultdict(list)
        for point in points_3d:
            by_label[point.get("label", "1")].append(point)
        for _, points in by_label.items():
            lookup = {(point["c"], point["r"]): point for point in points if point.get("c") is not None}
            for (col, row), point in lookup.items():
                for dc, dr in ((1, 0), (0, 1)):
                    neighbor = lookup.get((col + dc, row + dr))
                    if neighbor:
                        lines_3d.append((point, neighbor))
        self._canvas3d.set_data(points_3d, lines_3d, self._canvas.solved_yolo)

    def _recompute_yolo(self):
        if not self._canvas.yolo_boxes:
            self._canvas.solved_yolo = []
            self._canvas.update()
            return

        points_3d = compute_grid_3d_coords(self._canvas.points, self._canvas.grid_data, self._scene_config)
        valid = [point for point in points_3d if point.get("x3") is not None and point.get("label") in ("1", "2", "3", "4")]
        camera_matrix = solve_camera_matrix(valid) if len(valid) >= 12 else None

        half_width = self._scene_config["width"] / 2.0
        depth = self._scene_config["depth"]
        height = self._scene_config["height"]

        ground_points = [point for point in self._canvas.grid_data + self._canvas.points if point.get("label") == "1"]
        ground_h = None
        if ground_points:
            max_col = max((point.get("c", 0) for point in ground_points), default=0)
            max_row = max((point.get("r", 0) for point in ground_points), default=0)
            lookup = {(point["c"], point["r"]): point for point in ground_points if point.get("c") is not None}
            p_tl = lookup.get((0, 0))
            p_tr = lookup.get((max_col, 0))
            p_bl = lookup.get((0, max_row))
            p_br = lookup.get((max_col, max_row))
            if p_tl and p_tr and p_bl and p_br:
                phys = [
                    {"x": -half_width, "y": depth},
                    {"x": half_width, "y": depth},
                    {"x": -half_width, "y": 0},
                    {"x": half_width, "y": 0},
                ]
                pix = [p_tl, p_tr, p_bl, p_br]
                p2xy = get_perspective_transform(pix, phys)
                xy2p = get_perspective_transform(phys, pix)
                if p2xy and xy2p:
                    ground_h = {"Pix_to_XY": p2xy, "XY_to_Pix": xy2p}

        solved = []
        for box in self._canvas.yolo_boxes:
            u_center = (box["startX"] + box["endX"]) / 2.0
            v_bottom = max(box["startY"], box["endY"])
            v_top = min(box["startY"], box["endY"])

            base_z = 0.0
            fixed_y = None
            for region in self._canvas.regions:
                if is_point_in_polygon({"x": u_center, "y": v_bottom}, region.get("points", [])):
                    base_z = float(region.get("height", 0))
                    fixed_y = region.get("y_val", "")
                    break

            x_pos, y_pos = 0.0, 0.0
            if ground_h:
                raw_xy = apply_homography(ground_h["Pix_to_XY"], {"x": u_center, "y": v_bottom})
                if raw_xy:
                    x_pos, y_pos = raw_xy["x"], raw_xy["y"]

            if camera_matrix and base_z > 0:
                if fixed_y not in (None, ""):
                    try:
                        y_pos = float(fixed_y)
                        solved_x = solve_x_with_yz(camera_matrix, u_center, y_pos, base_z)
                        if solved_x is not None:
                            x_pos = solved_x
                    except ValueError:
                        pass
                else:
                    xy = solve_xy_with_z(camera_matrix, u_center, v_bottom, base_z)
                    if xy:
                        x_pos, y_pos = xy["x"], xy["y"]

            uncompensated_y = y_pos
            if not (base_z > 0 and fixed_y not in (None, "")):
                ratio = max(0.0, min(1.0, y_pos / max(depth, 1.0)))
                y_pos = y_pos - self._near_comp * (1 - ratio) + self._depth_comp * ratio

            x_pos = max(-half_width, min(half_width, x_pos))
            y_pos = max(0.0, min(depth, y_pos))
            uncompensated_y = max(0.0, min(depth, uncompensated_y))

            mouse_height = solve_height_with_xyz(camera_matrix, x_pos, uncompensated_y, base_z, v_top) if camera_matrix else 0.0
            mouse_height = max(0.0, min(mouse_height, height - base_z))
            shadow = apply_homography(ground_h["XY_to_Pix"], {"x": x_pos, "y": y_pos}) if ground_h else None

            solved.append(
                {
                    **box,
                    "cx": u_center,
                    "cy": v_bottom,
                    "shadow_x": shadow["x"] if shadow else u_center,
                    "shadow_y": shadow["y"] if shadow else v_bottom,
                    "X": x_pos,
                    "Y": y_pos,
                    "Z_base": base_z,
                    "mouseHeight": mouse_height,
                    "Z_total": base_z + mouse_height,
                }
            )

        self._canvas.solved_yolo = solved
        self._canvas.update()

    # ------------------------------------------------------------------ ui refresh
    def _refresh_ui_state(self):
        self._opacity_value.setText(f"{self._opacity_slider.value()}%")
        self._update_dynamic_button_text()

        is_image_mode = self._view_mode in ("topdown", "perspective", "region")
        for key in self._tool_btns:
            self._tool_btns[key].setVisible(is_image_mode)

        self._btn_complete.setVisible(self._view_mode in ("topdown", "perspective"))
        self._btn_clear_layer.setVisible(self._view_mode in ("topdown", "perspective"))
        self._comp_widget.setVisible(self._view_mode in ("perspective", "3d", "chart"))
        self._region_widget.setVisible(self._view_mode == "region")
        self._btn_reset_3d.setVisible(False)
        self._btn_refresh_view.setVisible(False)
        self._btn_undo.setVisible(False)

        if self._view_mode == "region":
            self._btn_export_current.setText("导出区域 JSON")
        elif self._view_mode == "3d":
            self._btn_export_current.setText("导出 3D 点位 JSON")
        elif self._view_mode == "chart":
            self._btn_export_current.setText("导出轨迹 JSON")
        else:
            self._btn_export_current.setText(f"导出点位 JSON (标签 {self._layer_combo.currentIndex() + 1})")
