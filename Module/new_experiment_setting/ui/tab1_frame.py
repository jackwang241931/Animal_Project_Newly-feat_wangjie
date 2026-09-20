# Module/new_experiment_setting/ui/tab1_frame.py
from PyQt6 import QtCore, QtGui, QtWidgets


class Ui_tab1_frame(object):
    def setupUi(self, tab1_frame):
        tab1_frame.setObjectName("tab1_frame")
        tab1_frame.resize(1272, 584)

        # 设置 centralwidget
        self.centralwidget = QtWidgets.QWidget(parent=tab1_frame)
        self.centralwidget.setObjectName("centralwidget")

        # 主布局
        self.main_layout = QtWidgets.QVBoxLayout(self.centralwidget)
        self.main_layout.setContentsMargins(7, 7, 7, 7)
        self.main_layout.setSpacing(8)
        self.main_layout.setObjectName("main_layout")

        # ========== 顶部布局（串口配置） ==========
        self.top_layout = QtWidgets.QHBoxLayout()
        self.top_layout.setContentsMargins(5, 5, 5, 5)
        self.top_layout.setSpacing(8)
        self.top_layout.setObjectName("top_layout")

        # 标签
        self.label_3 = QtWidgets.QLabel(parent=self.centralwidget)
        self.label_3.setStyleSheet("QLabel { color: red; }")
        self.label_3.setObjectName("label_3")
        self.label_3.setText("串口配置（串口配置决定了数据监控时响应的串口，一定要配置正确！！）：")
        self.top_layout.addWidget(self.label_3)

        # 串口下拉框
        self.tab_1_port_combox = QtWidgets.QComboBox(parent=self.centralwidget)
        self.tab_1_port_combox.setMinimumSize(QtCore.QSize(250, 0))
        self.tab_1_port_combox.setObjectName("tab_1_port_combox")
        self.top_layout.addWidget(self.tab_1_port_combox)

        # 刷新按钮
        self.tab_1_refresh_port_btn = QtWidgets.QPushButton(parent=self.centralwidget)
        self.tab_1_refresh_port_btn.setMinimumSize(QtCore.QSize(80, 35))
        self.tab_1_refresh_port_btn.setText("刷新串口")
        self.tab_1_refresh_port_btn.setObjectName("tab_1_refresh_port_btn")
        self.top_layout.addWidget(self.tab_1_refresh_port_btn)

        # 确认串口按钮
        self.tab_1_confirm_port_btn = QtWidgets.QPushButton(parent=self.centralwidget)
        self.tab_1_confirm_port_btn.setMinimumSize(QtCore.QSize(80, 35))
        self.tab_1_confirm_port_btn.setText("确认串口")
        self.tab_1_confirm_port_btn.setObjectName("tab_1_confirm_port_btn")
        self.top_layout.addWidget(self.tab_1_confirm_port_btn)

        # 刷新检测按钮
        self.tab_1_refresh_detection_btn = QtWidgets.QPushButton(parent=self.centralwidget)
        self.tab_1_refresh_detection_btn.setMinimumSize(QtCore.QSize(90, 35))
        self.tab_1_refresh_detection_btn.setText("刷新检测")
        self.tab_1_refresh_detection_btn.setObjectName("tab_1_refresh_detection_btn")
        self.top_layout.addWidget(self.tab_1_refresh_detection_btn)

        # 确定设备配置按钮
        self.start_btn = QtWidgets.QPushButton(parent=self.centralwidget)
        self.start_btn.setMinimumSize(QtCore.QSize(120, 35))
        self.start_btn.setText("确定设备配置")
        self.start_btn.setObjectName("start_btn")
        self.top_layout.addWidget(self.start_btn)

        # 弹性空间
        spacerItem = QtWidgets.QSpacerItem(
            40, 20,
            QtWidgets.QSizePolicy.Policy.Expanding,
            QtWidgets.QSizePolicy.Policy.Minimum
        )
        self.top_layout.addItem(spacerItem)

        self.main_layout.addLayout(self.top_layout, 0)


        # ========== 中间内容区域（左右两栏） ==========
        self.content_splitter = QtWidgets.QSplitter(QtCore.Qt.Orientation.Horizontal, parent=self.centralwidget)
        self.content_splitter.setObjectName("content_splitter")

        # ========== 左侧：上下两部分 ==========
        self.left_widget = QtWidgets.QWidget()
        self.left_widget.setObjectName("left_widget")
        self.left_widget.setMinimumWidth(300)
        self.left_widget.setMaximumWidth(400)

        self.left_layout = QtWidgets.QVBoxLayout(self.left_widget)
        self.left_layout.setContentsMargins(10, 10, 10, 10)
        self.left_layout.setSpacing(8)
        self.left_layout.setObjectName("left_layout")

        # ========== 左侧上部：模块检测状态 ==========
        self.module_detection_group = QtWidgets.QGroupBox("气路检测")
        self.module_detection_group.setStyleSheet("""
            QGroupBox {
                font-weight: bold;
                border: 1px solid #ddd;
                border-radius: 5px;
                margin-top: 10px;
                padding-top: 10px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 3px 0 3px;
            }
        """)
        self.module_detection_group.setFixedHeight(150)  # 固定高度
        self.module_detection_layout = QtWidgets.QVBoxLayout(self.module_detection_group)
        self.module_detection_layout.setContentsMargins(10, 10, 10, 10)
        self.module_detection_layout.setSpacing(8)
        self.module_detection_layout.setObjectName("module_detection_layout")

        self.left_layout.addWidget(self.module_detection_group, 0)  # 不拉伸

        # ========== 左侧下部：鼠笼列表（无边框） ==========
        self.cage_list_container = QtWidgets.QWidget()
        self.cage_list_container.setObjectName("cage_list_container")
        cage_list_container_layout = QtWidgets.QVBoxLayout(self.cage_list_container)
        cage_list_container_layout.setContentsMargins(0, 0, 0, 0)
        cage_list_container_layout.setSpacing(5)

        # 鼠笼列表
        self.cage_list_widget = QtWidgets.QListWidget()
        self.cage_list_widget.setObjectName("cage_list_widget")
        self.cage_list_widget.setStyleSheet("""
            QListWidget {
                background-color: #f5f5f5;
                border: 1px solid #ddd;
                border-radius: 5px;
                padding: 5px;
            }
            QListWidget::item {
                padding: 8px;
                border-bottom: 1px solid #eee;
                margin: 2px;
                border-radius: 3px;
            }
            QListWidget::item:hover {
                background-color: #e3f2fd;
            }
            QListWidget::item:selected {
                background-color: #2196f3;
                color: white;
            }
            QListWidget::item:disabled {
                background-color: #f5f5f5;
                color: #999;
            }
        """)
        cage_list_container_layout.addWidget(self.cage_list_widget)
        self.left_layout.addWidget(self.cage_list_container, 1)

        self.content_splitter.addWidget(self.left_widget)

        # ========== 右侧：配置区域 ==========
        self.right_widget = QtWidgets.QWidget()
        self.right_widget.setObjectName("right_widget")

        self.right_layout = QtWidgets.QVBoxLayout(self.right_widget)
        self.right_layout.setContentsMargins(10, 20, 10, 10)
        self.right_layout.setSpacing(8)
        self.right_layout.setObjectName("right_layout")

        # 右侧滚动区域（配置内容）
        self.config_scroll_area = QtWidgets.QScrollArea(parent=self.right_widget)
        self.config_scroll_area.setStyleSheet("""
            QScrollArea {
                background-color: white;
                border: 1px solid #ddd;
                border-radius: 5px;
            }
        """)
        self.config_scroll_area.setWidgetResizable(True)
        self.config_scroll_area.setObjectName("config_scroll_area")

        # 滚动区域内容
        self.config_scroll_widget = QtWidgets.QWidget()
        self.config_scroll_widget.setObjectName("config_scroll_widget")
        self.content_layout = QtWidgets.QVBoxLayout(self.config_scroll_widget)
        self.content_layout.setContentsMargins(15, 15, 15, 15)
        self.content_layout.setSpacing(10)
        self.content_layout.setObjectName("content_layout")

        self.config_scroll_area.setWidget(self.config_scroll_widget)
        self.right_layout.addWidget(self.config_scroll_area)

        self.content_splitter.addWidget(self.right_widget)

        # 设置分割器比例 (左侧30%, 右侧70%)
        self.content_splitter.setSizes([300, 700])

        self.main_layout.addWidget(self.content_splitter, 1)

        # ========== 设置 centralwidget ==========
        tab1_frame.setCentralWidget(self.centralwidget)

        self.menubar = QtWidgets.QMenuBar(parent=tab1_frame)
        self.menubar.setGeometry(QtCore.QRect(0, 0, 1272, 22))
        self.menubar.setObjectName("menubar")
        tab1_frame.setMenuBar(self.menubar)

        self.statusbar = QtWidgets.QStatusBar(parent=tab1_frame)
        self.statusbar.setObjectName("statusbar")
        tab1_frame.setStatusBar(self.statusbar)

        QtCore.QMetaObject.connectSlotsByName(tab1_frame)

    def retranslateUi(self, tab1_frame):
        _translate = QtCore.QCoreApplication.translate
        tab1_frame.setWindowTitle(_translate("tab1_frame", "设备信息"))
