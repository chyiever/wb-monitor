"""
Main Window Module for PCCP Monitoring Software

This module implements the main GUI window using PyQt5,
including Tab1 (FIP processing) and Tab2 (Signal detection) layouts.

Author: Claude
Date: 2026-03-11
"""

import sys
import os
import logging
from typing import Dict, Any, List, Tuple
from PyQt5.QtWidgets import (
    QMainWindow, QApplication, QTabWidget, QWidget, QVBoxLayout,
    QHBoxLayout, QGridLayout, QGroupBox, QLabel, QPushButton,
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QTextEdit, QTableWidget, QTableWidgetItem, QSplitter,
    QFrame, QStatusBar, QMenuBar, QAction
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QIcon
import pyqtgraph as pg

# 设置PyQtGraph的样式
pg.setConfigOptions(antialias=True)
pg.setConfigOption('background', 'w')
pg.setConfigOption('foreground', 'k')


class MainWindow(QMainWindow):
    """主窗口类"""

    # 信号定义
    start_monitoring = pyqtSignal()
    stop_monitoring = pyqtSignal()
    config_changed = pyqtSignal(dict)

    # 绘图控制信号
    time_plot_toggled = pyqtSignal(bool)
    psd_plot_toggled = pyqtSignal(bool)
    psd_settings_changed = pyqtSignal(dict)
    time_settings_changed = pyqtSignal(dict)
    filter_settings_changed = pyqtSignal(dict)  # 新增滤波器设置变化信号

    def __init__(self):
        super().__init__()
        self.setWindowTitle("PCCP断丝监测软件 v1.0")
        self.setGeometry(100, 100, 1600, 900)

        # 设置全局字体大小（调大2号）
        font = QFont()
        font.setPointSize(14)  # 从12号调整为14号
        self.setFont(font)

        # 初始化组件
        self._init_ui()
        self._init_menu()
        self._init_status_bar()
        self._setup_connections()

        # 状态变量
        self.monitoring_active = False

        # 应用默认的PSD设置范围（解决问题3）
        self._apply_initial_psd_settings()

    def _apply_initial_psd_settings(self):
        """应用初始的PSD设置范围"""
        try:
            # 强制重置PSD参数为正确的默认值
            self.psd_window_length_spin.setValue(0.4)  # 0.4秒

            # 调用PSD设置更新函数，应用默认值
            self._update_psd_settings()
        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error applying initial PSD settings: {e}")

    def _init_ui(self):
        """初始化用户界面"""
        # 创建中央控件
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # 创建主布局
        main_layout = QVBoxLayout(central_widget)

        # 创建标题栏
        header_widget = self._create_header()
        main_layout.addWidget(header_widget)

        # 创建标签页控件
        self.tab_widget = QTabWidget()
        # 设置Tab字体大小
        self.tab_widget.setStyleSheet("""
            QTabWidget::pane {
                border: 1px solid #cccccc;
            }
            QTabBar::tab {
                font-size: 16px;
                padding: 8px 16px;
                margin: 2px;
            }
            QTabBar::tab:selected {
                background-color: #e3f2fd;
                border-bottom: 2px solid #2196f3;
            }
        """)
        main_layout.addWidget(self.tab_widget)

        # 创建各个标签页
        self._create_tab1()  # FIP数据处理
        self._create_tab2()  # 信号检测
        self._create_tab3()  # DAS数据（暂时禁用）
        self._create_tab4()  # 信号定位（暂时禁用）

        # 禁用Tab3和Tab4
        self.tab_widget.setTabEnabled(2, False)
        self.tab_widget.setTabEnabled(3, False)

    def _create_header(self) -> QWidget:
        """创建标题栏"""
        header_widget = QWidget()
        header_widget.setFixedHeight(80)
        header_widget.setStyleSheet("background-color: #f8f9fa; border-bottom: 2px solid #dee2e6;")

        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(20, 10, 20, 10)

        # 左侧Logo
        logo_label = QLabel()
        # 尝试加载logo，如果文件不存在则显示默认文本
        logo_path = "resources/logo.png"
        if os.path.exists(logo_path):
            pixmap = QIcon(logo_path).pixmap(60, 60)
            logo_label.setPixmap(pixmap)
        else:
            logo_label.setText("LOGO")
            logo_label.setStyleSheet("font-size: 16px; font-weight: bold; color: #666;")

        logo_label.setFixedSize(60, 60)
        header_layout.addWidget(logo_label)

        # 中央标题
        title_label = QLabel("融合型光纤PCCP断丝监测软件")
        title_label.setAlignment(Qt.AlignCenter)
        title_label.setStyleSheet("""
            font-size: 26px;
            font-weight: bold;
            color: #2c3e50;
            font-family: 'Microsoft YaHei', '微软雅黑';
        """)
        header_layout.addWidget(title_label, stretch=1)

        # 右侧空白区域（保持对称）
        spacer_label = QLabel()
        spacer_label.setFixedSize(60, 60)
        header_layout.addWidget(spacer_label)

        return header_widget

    def _create_tab1(self):
        """创建Tab1 - FIP数据处理界面"""
        tab1 = QWidget()
        self.tab_widget.addTab(tab1, "FIP")

        # 主布局
        main_layout = QHBoxLayout(tab1)

        # 创建左侧参数配置区域
        self.param_widget = self._create_parameter_panel()
        main_layout.addWidget(self.param_widget, stretch=1)

        # 创建右侧波形显示区域
        self.plot_widget = self._create_plot_panel()
        main_layout.addWidget(self.plot_widget, stretch=3)

    def _create_parameter_panel(self) -> QWidget:
        """创建参数配置面板 - Tab1简化版本"""
        widget = QWidget()
        widget.setMaximumWidth(350)
        layout = QVBoxLayout(widget)

        # 通信设置组
        comm_group = self._create_communication_group()
        layout.addWidget(comm_group)

        # 预处理设置组
        processing_group = self._create_processing_group()
        layout.addWidget(processing_group)

        # 数据存储设置组（仅保留相位数据存储）
        storage_group = self._create_simple_storage_group()
        layout.addWidget(storage_group)

        # 可视化参数设置组
        visualization_group = self._create_visualization_group()
        layout.addWidget(visualization_group)

        # 控制按钮
        control_group = self._create_control_group()
        layout.addWidget(control_group)

        # 添加弹性空间
        layout.addStretch()

        return widget

    def _create_communication_group(self) -> QGroupBox:
        """创建通信设置组"""
        group = QGroupBox("通信设置")
        layout = QGridLayout(group)

        # IP地址
        layout.addWidget(QLabel("IP地址:"), 0, 0)
        self.ip_edit = QLineEdit("0.0.0.0")
        layout.addWidget(self.ip_edit, 0, 1)

        # 端口
        layout.addWidget(QLabel("端口:"), 1, 0)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(3677)
        layout.addWidget(self.port_spin, 1, 1)

        # 连接状态
        layout.addWidget(QLabel("连接状态:"), 2, 0)
        self.conn_status_label = QLabel("未连接")
        self.conn_status_label.setStyleSheet("color: red; font-weight: bold;")
        layout.addWidget(self.conn_status_label, 2, 1)

        # 统计信息
        layout.addWidget(QLabel("接收数据包:"), 3, 0)
        self.packet_count_label = QLabel("0")
        layout.addWidget(self.packet_count_label, 3, 1)

        layout.addWidget(QLabel("丢包率:"), 4, 0)
        self.loss_rate_label = QLabel("0%")
        layout.addWidget(self.loss_rate_label, 4, 1)

        return group

    def _create_processing_group(self) -> QGroupBox:
        """创建预处理设置组"""
        group = QGroupBox("信号预处理")
        layout = QGridLayout(group)

        # 滤波类型
        layout.addWidget(QLabel("滤波类型:"), 0, 0)
        self.filter_type_combo = QComboBox()
        self.filter_type_combo.addItems(["无滤波", "低通", "高通", "带通", "带阻"])
        self.filter_type_combo.setCurrentText("带通")
        layout.addWidget(self.filter_type_combo, 0, 1)

        # 低频截止
        layout.addWidget(QLabel("低频截止(Hz):"), 1, 0)
        self.low_freq_spin = QSpinBox()
        self.low_freq_spin.setRange(1, 100000)
        self.low_freq_spin.setValue(100)
        layout.addWidget(self.low_freq_spin, 1, 1)

        # 高频截止
        layout.addWidget(QLabel("高频截止(Hz):"), 2, 0)
        self.high_freq_spin = QSpinBox()
        self.high_freq_spin.setRange(1, 500000)
        self.high_freq_spin.setValue(10000)
        layout.addWidget(self.high_freq_spin, 2, 1)

        # 滤波阶数
        layout.addWidget(QLabel("滤波阶数:"), 3, 0)
        self.filter_order_spin = QSpinBox()
        self.filter_order_spin.setRange(1, 10)
        self.filter_order_spin.setValue(4)
        layout.addWidget(self.filter_order_spin, 3, 1)

        # 降采样倍数 - 默认值改为5倍
        layout.addWidget(QLabel("降采样倍数:"), 4, 0)
        self.downsample_spin = QSpinBox()
        self.downsample_spin.setRange(1, 100)
        self.downsample_spin.setValue(5)  # 默认5倍降采样：1MHz -> 200kHz
        layout.addWidget(self.downsample_spin, 4, 1)

        return group

    def _create_feature_group(self) -> QGroupBox:
        """创建特征计算设置组"""
        group = QGroupBox("特征计算")
        layout = QGridLayout(group)

        # 特征选择
        self.feature_checkboxes = {}
        features = [
            ("短时能量", "short_energy"),
            ("短时过零率", "zero_crossing"),
            ("峰值因子", "peak_factor"),
            ("均方根", "rms")
        ]

        for i, (name, key) in enumerate(features):
            checkbox = QCheckBox(name)
            if key == "short_energy":
                checkbox.setChecked(True)  # 默认选中短时能量
            self.feature_checkboxes[key] = checkbox
            layout.addWidget(checkbox, i, 0, 1, 2)

        # 时间窗口
        layout.addWidget(QLabel("时间窗口(s):"), len(features), 0)
        self.window_size_spin = QDoubleSpinBox()
        self.window_size_spin.setRange(0.01, 1.0)
        self.window_size_spin.setValue(0.05)
        self.window_size_spin.setSingleStep(0.01)
        layout.addWidget(self.window_size_spin, len(features), 1)

        # 重叠率
        layout.addWidget(QLabel("重叠率(%):"), len(features)+1, 0)
        self.overlap_spin = QSpinBox()
        self.overlap_spin.setRange(0, 90)
        self.overlap_spin.setValue(50)
        layout.addWidget(self.overlap_spin, len(features)+1, 1)

        return group

    def _create_detection_group(self) -> QGroupBox:
        """创建检测设置组"""
        group = QGroupBox("信号检测")
        layout = QGridLayout(group)

        # 阈值系数
        layout.addWidget(QLabel("阈值系数:"), 0, 0)
        self.threshold_spin = QDoubleSpinBox()
        self.threshold_spin.setRange(1.0, 10.0)
        self.threshold_spin.setValue(3.0)
        self.threshold_spin.setSingleStep(0.1)
        layout.addWidget(self.threshold_spin, 0, 1)

        # 最大触发时长
        layout.addWidget(QLabel("最大触发时长(s):"), 1, 0)
        self.max_trigger_spin = QDoubleSpinBox()
        self.max_trigger_spin.setRange(0.01, 3.0)
        self.max_trigger_spin.setValue(0.1)
        self.max_trigger_spin.setSingleStep(0.01)
        layout.addWidget(self.max_trigger_spin, 1, 1)

        # 基线更新
        self.auto_baseline_check = QCheckBox("自动更新基线")
        self.auto_baseline_check.setChecked(True)
        layout.addWidget(self.auto_baseline_check, 2, 0, 1, 2)

        # 基线更新间隔
        layout.addWidget(QLabel("基线更新间隔(s):"), 3, 0)
        self.baseline_interval_spin = QSpinBox()
        self.baseline_interval_spin.setRange(5, 300)
        self.baseline_interval_spin.setValue(10)
        layout.addWidget(self.baseline_interval_spin, 3, 1)

        # 手动更新基线按钮
        self.update_baseline_btn = QPushButton("手动更新基线")
        layout.addWidget(self.update_baseline_btn, 4, 0, 1, 2)

        return group

    def _create_simple_storage_group(self) -> QGroupBox:
        """创建简化的存储设置组 - 仅相位数据存储"""
        group = QGroupBox("数据存储")
        layout = QGridLayout(group)

        # 相位数据存储
        self.phase_storage_check = QCheckBox("保存相位数据")
        layout.addWidget(self.phase_storage_check, 0, 0, 1, 2)

        # 存储间隔
        layout.addWidget(QLabel("存储间隔(s):"), 1, 0)
        self.storage_interval_spin = QSpinBox()
        self.storage_interval_spin.setRange(10, 300)
        self.storage_interval_spin.setValue(30)
        layout.addWidget(self.storage_interval_spin, 1, 1)

        # 存储路径
        layout.addWidget(QLabel("存储路径:"), 2, 0, 1, 2)
        self.storage_path_edit = QLineEdit("D:/PCCP/FIPdata")
        layout.addWidget(self.storage_path_edit, 3, 0, 1, 2)

        return group
        """创建存储设置组"""
        group = QGroupBox("数据存储")
        layout = QGridLayout(group)

        # 实时存储
        self.realtime_storage_check = QCheckBox("实时存储")
        layout.addWidget(self.realtime_storage_check, 0, 0, 1, 2)

        # 存储间隔
        layout.addWidget(QLabel("存储间隔(s):"), 1, 0)
        self.storage_interval_spin = QSpinBox()
        self.storage_interval_spin.setRange(10, 300)
        self.storage_interval_spin.setValue(30)
        layout.addWidget(self.storage_interval_spin, 1, 1)

        # 触发存储
        self.trigger_storage_check = QCheckBox("触发存储")
        self.trigger_storage_check.setChecked(True)
        layout.addWidget(self.trigger_storage_check, 2, 0, 1, 2)

        # 存储路径
        layout.addWidget(QLabel("存储路径:"), 3, 0, 1, 2)
        self.storage_path_edit = QLineEdit("D:/PCCP/FIPdata")
        layout.addWidget(self.storage_path_edit, 4, 0, 1, 2)

        return group

    def _create_visualization_group(self) -> QGroupBox:
        """创建可视化参数设置组"""
        group = QGroupBox("可视化参数")
        layout = QGridLayout(group)

        # PSD计算参数
        layout.addWidget(QLabel("PSD窗口长度:"), 0, 0)
        self.psd_window_length_spin = QDoubleSpinBox()
        self.psd_window_length_spin.setRange(0.1, 10.0)  # 0.1秒到10秒
        self.psd_window_length_spin.setValue(0.4)  # 默认0.4秒
        self.psd_window_length_spin.setSingleStep(0.1)
        self.psd_window_length_spin.setDecimals(1)
        self.psd_window_length_spin.setSuffix(" s")  # 单位：秒
        layout.addWidget(self.psd_window_length_spin, 0, 1)

        # 时域数据时长
        layout.addWidget(QLabel("时域数据时长:"), 1, 0)
        self.time_display_duration_spin = QDoubleSpinBox()
        self.time_display_duration_spin.setRange(0.1, 60.0)
        self.time_display_duration_spin.setValue(1.0)
        self.time_display_duration_spin.setSingleStep(0.1)
        self.time_display_duration_spin.setSuffix(" s")
        layout.addWidget(self.time_display_duration_spin, 1, 1)

        # 绘图控制按钮
        layout.addWidget(QLabel("绘图控制:"), 2, 0, 1, 2)

        plot_control_layout = QHBoxLayout()

        self.time_plot_btn = QPushButton("停止时域")
        self.time_plot_btn.setCheckable(True)
        self.time_plot_btn.setChecked(True)  # 默认开启
        plot_control_layout.addWidget(self.time_plot_btn)

        self.psd_plot_btn = QPushButton("停止PSD")
        self.psd_plot_btn.setCheckable(True)
        self.psd_plot_btn.setChecked(True)  # 默认开启
        plot_control_layout.addWidget(self.psd_plot_btn)

        layout.addLayout(plot_control_layout, 3, 0, 1, 2)

        return group

    def _create_control_group(self) -> QGroupBox:
        """创建控制按钮组"""
        group = QGroupBox("系统控制")
        layout = QVBoxLayout(group)

        # 开始/停止按钮
        self.start_stop_btn = QPushButton("开始监测")
        self.start_stop_btn.setStyleSheet("""
            QPushButton {
                font-size: 16px;
                font-weight: bold;
                padding: 8px;
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:pressed {
                background-color: #3d8b40;
            }
        """)
        layout.addWidget(self.start_stop_btn)

        # 配置按钮
        config_layout = QHBoxLayout()

        self.save_config_btn = QPushButton("保存配置")
        self.load_config_btn = QPushButton("加载配置")
        self.reset_config_btn = QPushButton("重置配置")

        config_layout.addWidget(self.save_config_btn)
        config_layout.addWidget(self.load_config_btn)
        config_layout.addWidget(self.reset_config_btn)

        layout.addLayout(config_layout)

        return group

    def _create_plot_panel(self) -> QWidget:
        """创建波形显示面板 - Tab1简化版本（只包含时域和PSD图）"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 创建分割器用于两个图表（垂直布局）
        splitter = QSplitter(Qt.Vertical)
        layout.addWidget(splitter)

        # 时域波形图
        self.time_plot = pg.PlotWidget(title="滤波后信号时域波形")
        self.time_plot.setLabel('left', '幅值', **{'font-size': '16px'})
        self.time_plot.setLabel('bottom', '时间', units='s', **{'font-size': '16px'})
        self.time_plot.showGrid(True, True)
        # 设置标题字体
        self.time_plot.setTitle("滤波后信号时域波形", **{'font-size': '18px'})
        splitter.addWidget(self.time_plot)

        # PSD功率谱图（对数横轴）
        self.psd_plot = pg.PlotWidget(title="功率谱密度")
        self.psd_plot.setLabel('left', '幅值', units='dB', **{'font-size': '16px'})
        self.psd_plot.setLabel('bottom', '频率', units='Hz', **{'font-size': '16px'})
        self.psd_plot.setLogMode(x=True, y=False)  # 设置横轴为对数
        self.psd_plot.showGrid(True, True)
        # 禁用坐标轴的自动单位缩放，强制使用Hz单位
        bottom_axis = self.psd_plot.getAxis('bottom')
        bottom_axis.enableAutoSIPrefix(False)
        # 设置标题字体
        self.psd_plot.setTitle("功率谱密度", **{'font-size': '18px'})
        splitter.addWidget(self.psd_plot)

        # 设置图表大小比例
        splitter.setSizes([500, 500])

        return widget

    def _create_tab2(self):
        """创建Tab2 - 信号检测界面"""
        tab2 = QWidget()
        self.tab_widget.addTab(tab2, "SigID")

        # 主布局
        main_layout = QHBoxLayout(tab2)

        # 左侧参数配置区域
        left_panel = self._create_detection_params_panel()
        main_layout.addWidget(left_panel, stretch=1)

        # 右侧特征显示区域
        right_panel = self._create_feature_display_panel()
        main_layout.addWidget(right_panel, stretch=3)

    def _create_detection_params_panel(self) -> QWidget:
        """创建检测参数面板"""
        widget = QWidget()
        widget.setMaximumWidth(350)
        layout = QVBoxLayout(widget)

        # 特征选择组
        feature_group = QGroupBox("特征选择")
        feature_layout = QVBoxLayout(feature_group)

        # 特征检测勾选框（与Tab1同步）
        self.detection_feature_checkboxes = {}
        features = [
            ("短时能量检测", "short_energy"),
            ("短时过零率检测", "zero_crossing"),
            ("峰值因子检测", "peak_factor"),
            ("均方根检测", "rms")
        ]

        for name, key in features:
            checkbox = QCheckBox(name)
            if key == "short_energy":
                checkbox.setChecked(True)
            self.detection_feature_checkboxes[key] = checkbox
            feature_layout.addWidget(checkbox)

        layout.addWidget(feature_group)

        # 检测参数组
        detection_group = QGroupBox("检测参数")
        detection_layout = QGridLayout(detection_group)

        # 每个特征的阈值设置
        detection_layout.addWidget(QLabel("特征"), 0, 0)
        detection_layout.addWidget(QLabel("阈值系数"), 0, 1)
        detection_layout.addWidget(QLabel("基线值"), 0, 2)

        self.threshold_controls = {}
        for i, (name, key) in enumerate(features):
            # 特征名称
            detection_layout.addWidget(QLabel(name.replace("检测", "")), i+1, 0)

            # 阈值系数控制
            threshold_spin = QDoubleSpinBox()
            threshold_spin.setRange(1.0, 10.0)
            threshold_spin.setValue(3.0)
            threshold_spin.setSingleStep(0.1)
            detection_layout.addWidget(threshold_spin, i+1, 1)

            # 基线值显示
            baseline_label = QLabel("0.000")
            baseline_label.setStyleSheet("background-color: #f0f0f0; padding: 2px;")
            detection_layout.addWidget(baseline_label, i+1, 2)

            self.threshold_controls[key] = {
                'threshold': threshold_spin,
                'baseline': baseline_label
            }

        layout.addWidget(detection_group)

        # 告警信息组
        alarm_group = QGroupBox("告警信息")
        alarm_layout = QVBoxLayout(alarm_group)

        # 告警统计
        stats_layout = QGridLayout()
        stats_layout.addWidget(QLabel("总告警次数:"), 0, 0)
        self.total_alarms_label = QLabel("0")
        stats_layout.addWidget(self.total_alarms_label, 0, 1)

        stats_layout.addWidget(QLabel("今日告警次数:"), 1, 0)
        self.today_alarms_label = QLabel("0")
        stats_layout.addWidget(self.today_alarms_label, 1, 1)

        alarm_layout.addLayout(stats_layout)

        # 最近告警列表
        self.alarm_table = QTableWidget()
        self.alarm_table.setColumnCount(3)
        self.alarm_table.setHorizontalHeaderLabels(["时间", "持续时间", "触发特征"])
        self.alarm_table.setMaximumHeight(200)
        alarm_layout.addWidget(self.alarm_table)

        layout.addWidget(alarm_group)

        # 清空告警按钮
        self.clear_alarms_btn = QPushButton("清空告警记录")
        layout.addWidget(self.clear_alarms_btn)

        layout.addStretch()
        return widget

    def _create_feature_display_panel(self) -> QWidget:
        """创建特征显示面板"""
        widget = QWidget()
        layout = QVBoxLayout(widget)

        # 创建三个特征曲线显示区域
        self.feature_plots = []
        self.feature_plot_curves = {}

        for i in range(3):
            plot_widget = pg.PlotWidget(title=f"短时特征曲线{i+1}")
            plot_widget.setLabel('left', '特征值')
            plot_widget.setLabel('bottom', '时间', units='s')
            plot_widget.showGrid(x=True, y=True)

            # 为每个图添加阈值线
            threshold_line = pg.InfiniteLine(
                pos=0, angle=0, pen=pg.mkPen('r', width=2, style=Qt.DashLine),
                label="阈值", labelOpts={'position': 0.9}
            )
            plot_widget.addItem(threshold_line)

            self.feature_plots.append(plot_widget)
            layout.addWidget(plot_widget)

        return widget

    def _create_tab3(self):
        """创建Tab3 - eDAS数据界面（暂时禁用）"""
        tab3 = QWidget()
        self.tab_widget.addTab(tab3, "eDAS")

        layout = QVBoxLayout(tab3)
        placeholder = QLabel("DAS数据处理模块\n（暂未开发）")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setStyleSheet("font-size: 24px; color: gray;")
        layout.addWidget(placeholder)

    def _create_tab4(self):
        """创建Tab4 - 信号定位界面（暂时禁用）"""
        tab4 = QWidget()
        self.tab_widget.addTab(tab4, "SigLoc")

        layout = QVBoxLayout(tab4)
        placeholder = QLabel("信号定位模块\n（暂未开发）")
        placeholder.setAlignment(Qt.AlignCenter)
        placeholder.setStyleSheet("font-size: 24px; color: gray;")
        layout.addWidget(placeholder)

    def _init_menu(self):
        """初始化菜单栏"""
        menubar = self.menuBar()

        # 文件菜单
        file_menu = menubar.addMenu("文件")

        open_config = QAction("打开配置", self)
        save_config = QAction("保存配置", self)
        exit_action = QAction("退出", self)

        file_menu.addAction(open_config)
        file_menu.addAction(save_config)
        file_menu.addSeparator()
        file_menu.addAction(exit_action)

        # 工具菜单
        tools_menu = menubar.addMenu("工具")

        data_viewer = QAction("数据查看器", self)
        log_viewer = QAction("日志查看器", self)

        tools_menu.addAction(data_viewer)
        tools_menu.addAction(log_viewer)

        # 帮助菜单
        help_menu = menubar.addMenu("帮助")

        about_action = QAction("关于", self)
        help_action = QAction("使用帮助", self)

        help_menu.addAction(about_action)
        help_menu.addAction(help_action)

        # 可视化参数变化
        self.psd_window_length_spin.valueChanged.connect(self._update_psd_settings)
        self.time_display_duration_spin.valueChanged.connect(self._update_time_display_settings)

    def _toggle_monitoring(self):
        """切换监测状态"""
        if self.monitoring_active:
            self._stop_monitoring()
        else:
            self._start_monitoring()

    def _start_monitoring(self):
        """开始监测"""
        self.monitoring_active = True
        self.start_stop_btn.setText("停止监测")
        self.start_stop_btn.setStyleSheet("""
            QPushButton {
                font-size: 16px;
                font-weight: bold;
                padding: 8px;
                background-color: #f44336;
                color: white;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #da190b;
            }
            QPushButton:pressed {
                background-color: #c1130d;
            }
        """)

        self.start_monitoring.emit()
        self.status_bar.showMessage("监测中...", 0)

    def _stop_monitoring(self):
        """停止监测"""
        self.monitoring_active = False
        self.start_stop_btn.setText("开始监测")
        self.start_stop_btn.setStyleSheet("""
            QPushButton {
                font-size: 16px;
                font-weight: bold;
                padding: 8px;
                background-color: #4CAF50;
                color: white;
                border: none;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:pressed {
                background-color: #3d8b40;
            }
        """)

        self.stop_monitoring.emit()
        self.status_bar.showMessage("就绪", 0)

    def _save_configuration(self):
        """保存配置"""
        config = self.get_current_config()
        self.config_changed.emit(config)

    def _load_configuration(self):
        """加载配置"""
        # TODO: 实现配置加载对话框
        pass

    def _reset_configuration(self):
        """重置配置为默认值"""
        # TODO: 实现配置重置
        pass

    def _manual_update_baseline(self):
        """手动更新基线"""
        # TODO: 触发基线更新信号
        pass

    def _clear_alarm_history(self):
        """清空告警历史"""
        self.alarm_table.setRowCount(0)
        self.total_alarms_label.setText("0")
        self.today_alarms_label.setText("0")

    def get_current_config(self) -> Dict[str, Any]:
        """获取当前配置 - Tab1简化版本"""
        config = {
            "communication": {
                "ip": self.ip_edit.text(),
                "port": self.port_spin.value()
            },
            "preprocessing": {
                "filter": {
                    "type": self.filter_type_combo.currentText(),
                    "low_freq": self.low_freq_spin.value(),
                    "high_freq": self.high_freq_spin.value(),
                    "order": self.filter_order_spin.value()
                },
                "downsample": {
                    "factor": self.downsample_spin.value()
                }
            },
            "storage": {
                "realtime": {
                    "enabled": self.realtime_storage_check.isChecked(),
                    "interval": self.storage_interval_spin.value()
                },
                "path": self.storage_path_edit.text()
            }
        }

        # 如果Tab2控件存在，添加特征和检测配置
        if hasattr(self, 'detection_feature_checkboxes'):
            config["features"] = {
                "enabled": [key for key, checkbox in self.detection_feature_checkboxes.items() if checkbox.isChecked()],
            }

        if hasattr(self, 'threshold_controls'):
            config["detection"] = {
                "thresholds": {key: ctrl['threshold'].value()
                              for key, ctrl in self.threshold_controls.items()}
            }

        return config

    def update_connection_status(self, connected: bool, message: str):
        """更新连接状态"""
        if connected:
            self.conn_status_label.setText("已连接")
            self.conn_status_label.setStyleSheet("color: green; font-weight: bold;")
        else:
            self.conn_status_label.setText("未连接")
            self.conn_status_label.setStyleSheet("color: red; font-weight: bold;")

        self.status_bar.showMessage(message, 3000)

    def update_statistics(self, stats: Dict[str, Any]):
        """更新统计信息"""
        self.packet_count_label.setText(str(stats.get('packets_received', 0)))
        self.loss_rate_label.setText(f"{stats.get('loss_rate', 0):.2f}%")

    def update_feature_displays(self, features: Dict[str, List[Tuple[float, float]]]):
        """
        更新Tab2特征显示

        Args:
            features: 特征数据字典，格式为 {特征名: [(时间戳, 值)]}
        """
        try:
            # 更新基线值显示
            for feature_name, feature_data in features.items():
                if feature_name in self.threshold_controls and feature_data:
                    # 取最新值作为当前特征值的参考
                    latest_value = feature_data[-1][1]
                    # 这里应该显示基线值，但我们可以先显示当前值作为参考
                    # 实际的基线值会通过其他机制更新
                    pass

            # TODO: 添加特征曲线绘制到Tab2的图表控件

        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error updating feature displays: {e}")

    def add_detection_results(self, detections):
        """
        添加检测结果到Tab2

        Args:
            detections: DetectionResult对象列表
        """
        try:
            from datetime import datetime

            for detection in detections:
                # 添加到告警表格
                row_count = self.alarm_table.rowCount()
                self.alarm_table.insertRow(row_count)

                # 时间列
                time_str = datetime.fromtimestamp(detection.timestamp).strftime("%H:%M:%S")
                self.alarm_table.setItem(row_count, 0, QTableWidgetItem(time_str))

                # 持续时间列 (如果检测还在进行，显示为进行中)
                if detection.duration is not None:
                    duration_str = f"{detection.duration:.2f}s"
                else:
                    duration_str = "进行中"
                self.alarm_table.setItem(row_count, 1, QTableWidgetItem(duration_str))

                # 触发特征列
                feature_display_names = {
                    'short_energy': '短时能量',
                    'zero_crossing': '过零率',
                    'peak_factor': '峰值因子',
                    'rms': 'RMS'
                }
                feature_name = feature_display_names.get(detection.feature_name, detection.feature_name)
                self.alarm_table.setItem(row_count, 2, QTableWidgetItem(feature_name))

                # 滚动到最新行
                self.alarm_table.scrollToBottom()

            # 更新告警统计
            total_alarms = self.alarm_table.rowCount()
            self.total_alarms_label.setText(str(total_alarms))

            # 计算今日告警数（简化实现，实际应该基于日期判断）
            # 这里假设程序运行期间的所有告警都是今日告警
            self.today_alarms_label.setText(str(total_alarms))

        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error adding detection results: {e}")

    def update_baselines(self, baselines: Dict[str, float]):
        """
        更新基线值显示

        Args:
            baselines: 基线值字典
        """
        try:
            for feature_name, baseline_value in baselines.items():
                if feature_name in self.threshold_controls:
                    baseline_label = self.threshold_controls[feature_name]['baseline']
                    baseline_label.setText(f"{baseline_value:.3f}")

        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error updating baselines: {e}")

    def get_enabled_features(self) -> Dict[str, bool]:
        """
        获取Tab2中启用的特征

        Returns:
            启用特征的字典
        """
        if hasattr(self, 'detection_feature_checkboxes'):
            return {key: checkbox.isChecked()
                   for key, checkbox in self.detection_feature_checkboxes.items()}
        return {}

    def get_threshold_factors(self) -> Dict[str, float]:
        """
        获取Tab2中设置的阈值系数

        Returns:
            阈值系数字典
        """
        if hasattr(self, 'threshold_controls'):
            return {key: ctrl['threshold'].value()
                   for key, ctrl in self.threshold_controls.items()}
        return {}

    def _toggle_time_plot(self, enabled: bool):
        """切换时域绘图"""
        # 发送信号给主程序
        if hasattr(self, 'time_plot_toggled'):
            self.time_plot_toggled.emit(enabled)

        # 更新按钮文本
        if enabled:
            self.time_plot_btn.setText("停止时域")
        else:
            self.time_plot_btn.setText("时域绘图")

    def _toggle_psd_plot(self, enabled: bool):
        """切换PSD绘图"""
        # 发送信号给主程序
        if hasattr(self, 'psd_plot_toggled'):
            self.psd_plot_toggled.emit(enabled)

        # 更新按钮文本
        if enabled:
            self.psd_plot_btn.setText("停止PSD")
        else:
            self.psd_plot_btn.setText("PSD绘图")

    def _update_psd_settings(self):
        """更新PSD设置"""
        # 将时间单位转换为采样点数（PSD使用有效采样率，如200kHz）
        window_duration_sec = self.psd_window_length_spin.value()
        # 获取当前前面板的降采样因子来计算有效采样率
        current_downsample_factor = self.downsample_spin.value()
        # 计算有效采样率：默认5倍降采样 1MHz -> 200kHz
        ORIGINAL_SAMPLE_RATE = 1000000.0  # 1MHz
        effective_sample_rate = ORIGINAL_SAMPLE_RATE / current_downsample_factor
        window_length_samples = int(window_duration_sec * effective_sample_rate)

        psd_settings = {
            'window_length': window_length_samples,
            'window_duration': window_duration_sec,  # 也传递秒数供记录用
            'effective_sample_rate': effective_sample_rate,  # PSD使用有效采样率（如200kHz）
            'downsample_factor': current_downsample_factor  # 添加降采样因子
        }

        # 发送信号给主程序
        if hasattr(self, 'psd_settings_changed'):
            self.psd_settings_changed.emit(psd_settings)

    def _update_time_display_settings(self):
        """更新时域显示设置 - 仅支持显示时长调整，更新间隔固定为0.2s"""
        time_settings = {
            'duration': self.time_display_duration_spin.value()
            # 注意：更新间隔固定为0.2s，不再从UI获取
        }

        # 发送信号给主程序
        if hasattr(self, 'time_settings_changed'):
            self.time_settings_changed.emit(time_settings)

    def _update_filter_settings(self):
        """更新滤波器设置"""
        filter_settings = {
            'type': self.filter_type_combo.currentText(),
            'low_freq': self.low_freq_spin.value(),
            'high_freq': self.high_freq_spin.value(),
            'order': self.filter_order_spin.value()
        }

        # 发送信号给主程序
        if hasattr(self, 'filter_settings_changed'):
            self.filter_settings_changed.emit(filter_settings)

    def _init_status_bar(self):
        """初始化状态栏"""
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)

        # 添加软件版本信息到右侧，包含研究所名称
        version_label = QLabel("融合型光纤PCCP断丝监测软件 v1.0 - 中国科学院半导体研究所")
        version_label.setStyleSheet("color: #666; font-size: 12px;")
        self.status_bar.addPermanentWidget(version_label)

    def _setup_connections(self):
        """设置信号连接"""
        try:
            # 开始/停止监测按钮 - 最重要的信号连接！
            if hasattr(self, 'start_stop_btn'):
                self.start_stop_btn.clicked.connect(self._toggle_monitoring)

            # 配置按钮
            if hasattr(self, 'save_config_btn'):
                self.save_config_btn.clicked.connect(self._save_configuration)
            if hasattr(self, 'load_config_btn'):
                self.load_config_btn.clicked.connect(self._load_configuration)
            if hasattr(self, 'reset_config_btn'):
                self.reset_config_btn.clicked.connect(self._reset_configuration)

            # 绘图控制按钮信号连接
            if hasattr(self, 'time_plot_btn'):
                self.time_plot_btn.toggled.connect(self._toggle_time_plot)
            if hasattr(self, 'psd_plot_btn'):
                self.psd_plot_btn.toggled.connect(self._toggle_psd_plot)

            # PSD参数变化信号连接
            if hasattr(self, 'psd_window_length_spin'):
                self.psd_window_length_spin.valueChanged.connect(self._update_psd_settings)

            # 时域显示参数变化信号连接
            if hasattr(self, 'time_display_duration_spin'):
                self.time_display_duration_spin.valueChanged.connect(self._update_time_display_settings)

            # 滤波参数变化信号连接
            if hasattr(self, 'filter_type_combo'):
                self.filter_type_combo.currentTextChanged.connect(self._update_filter_settings)
            if hasattr(self, 'low_freq_spin'):
                self.low_freq_spin.valueChanged.connect(self._update_filter_settings)
            if hasattr(self, 'high_freq_spin'):
                self.high_freq_spin.valueChanged.connect(self._update_filter_settings)
            if hasattr(self, 'filter_order_spin'):
                self.filter_order_spin.valueChanged.connect(self._update_filter_settings)

            # 降采样参数变化时，也需要更新PSD设置（因为PSD计算依赖采样率）
            if hasattr(self, 'downsample_spin'):
                self.downsample_spin.valueChanged.connect(self._update_psd_settings)

        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error setting up connections: {e}")