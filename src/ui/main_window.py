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
import numpy as np
from PyQt5.QtWidgets import (
    QMainWindow, QApplication, QTabWidget, QWidget, QVBoxLayout,
    QHBoxLayout, QGridLayout, QGroupBox, QLabel, QPushButton,
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QTextEdit, QTableWidget, QTableWidgetItem, QSplitter,
    QFrame, QStatusBar, QMenuBar, QAction, QMessageBox
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
    tab2_settings_changed = pyqtSignal()
    tab2_clear_alarms_requested = pyqtSignal()
    tab3_start_requested = pyqtSignal()
    tab3_stop_requested = pyqtSignal()
    tab3_settings_changed = pyqtSignal()

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
        """Create the Tab2 parameter panel."""
        widget = QWidget()
        widget.setMaximumWidth(420)
        layout = QVBoxLayout(widget)

        features = [
            ("Short Energy", "short_energy"),
            ("Zero Crossing", "zero_crossing"),
            ("Peak Factor", "peak_factor"),
            ("RMS", "rms"),
        ]
        self.tab2_feature_order = [key for _, key in features]

        feature_group = QGroupBox("Feature Selection")
        feature_layout = QGridLayout(feature_group)
        feature_layout.addWidget(QLabel("Feature"), 0, 0)
        feature_layout.addWidget(QLabel("Compute"), 0, 1)
        feature_layout.addWidget(QLabel("Plot"), 0, 2)
        self.detection_feature_checkboxes = {}
        for row, (name, key) in enumerate(features, start=1):
            compute_checkbox = QCheckBox(name)
            plot_checkbox = QCheckBox()
            if key == "short_energy":
                compute_checkbox.setChecked(True)
                plot_checkbox.setChecked(True)
            plot_checkbox.toggled.connect(self._handle_tab2_plot_checkbox_change)
            self.detection_feature_checkboxes[key] = {
                "compute": compute_checkbox,
                "plot": plot_checkbox,
            }
            feature_layout.addWidget(compute_checkbox, row, 0)
            feature_layout.addWidget(plot_checkbox, row, 2)
        layout.addWidget(feature_group)

        preprocess_group = QGroupBox("Tab2 Preprocess")
        preprocess_layout = QGridLayout(preprocess_group)
        self.tab2_filter_enable_check = QCheckBox("Enable band-pass")
        self.tab2_filter_enable_check.setChecked(True)
        preprocess_layout.addWidget(self.tab2_filter_enable_check, 0, 0, 1, 2)
        preprocess_layout.addWidget(QLabel("Low cutoff (Hz)"), 1, 0)
        self.tab2_low_freq_spin = QSpinBox()
        self.tab2_low_freq_spin.setRange(1, 100000)
        self.tab2_low_freq_spin.setValue(100)
        preprocess_layout.addWidget(self.tab2_low_freq_spin, 1, 1)
        preprocess_layout.addWidget(QLabel("High cutoff (Hz)"), 2, 0)
        self.tab2_high_freq_spin = QSpinBox()
        self.tab2_high_freq_spin.setRange(2, 100000)
        self.tab2_high_freq_spin.setValue(10000)
        preprocess_layout.addWidget(self.tab2_high_freq_spin, 2, 1)
        preprocess_layout.addWidget(QLabel("Filter order"), 3, 0)
        self.tab2_filter_order_spin = QSpinBox()
        self.tab2_filter_order_spin.setRange(1, 10)
        self.tab2_filter_order_spin.setValue(4)
        preprocess_layout.addWidget(self.tab2_filter_order_spin, 3, 1)
        layout.addWidget(preprocess_group)

        window_group = QGroupBox("Window Settings")
        window_layout = QGridLayout(window_group)
        window_layout.addWidget(QLabel("Window (s)"), 0, 0)
        self.tab2_window_spin = QDoubleSpinBox()
        self.tab2_window_spin.setRange(0.05, 2.0)
        self.tab2_window_spin.setSingleStep(0.05)
        self.tab2_window_spin.setValue(0.2)
        self.tab2_window_spin.setDecimals(2)
        window_layout.addWidget(self.tab2_window_spin, 0, 1)
        window_layout.addWidget(QLabel("Overlap (%)"), 1, 0)
        self.tab2_overlap_spin = QDoubleSpinBox()
        self.tab2_overlap_spin.setRange(0.0, 95.0)
        self.tab2_overlap_spin.setSingleStep(5.0)
        self.tab2_overlap_spin.setValue(50.0)
        self.tab2_overlap_spin.setDecimals(1)
        window_layout.addWidget(self.tab2_overlap_spin, 1, 1)
        window_layout.addWidget(QLabel("Plot span (s)"), 2, 0)
        self.tab2_plot_duration_spin = QSpinBox()
        self.tab2_plot_duration_spin.setRange(10, 300)
        self.tab2_plot_duration_spin.setValue(60)
        window_layout.addWidget(self.tab2_plot_duration_spin, 2, 1)
        layout.addWidget(window_group)

        detection_group = QGroupBox("Threshold Detection")
        detection_layout = QGridLayout(detection_group)
        detection_layout.addWidget(QLabel("Feature"), 0, 0)
        detection_layout.addWidget(QLabel("Threshold"), 0, 1)
        detection_layout.addWidget(QLabel("Baseline"), 0, 2)
        self.threshold_controls = {}
        for row, (name, key) in enumerate(features, start=1):
            detection_layout.addWidget(QLabel(name), row, 0)
            threshold_spin = QDoubleSpinBox()
            threshold_spin.setRange(1.0, 20.0)
            threshold_spin.setValue(3.0)
            threshold_spin.setSingleStep(0.1)
            detection_layout.addWidget(threshold_spin, row, 1)
            baseline_label = QLabel("0.000")
            baseline_label.setStyleSheet("background-color: #f0f0f0; padding: 2px;")
            detection_layout.addWidget(baseline_label, row, 2)
            self.threshold_controls[key] = {"threshold": threshold_spin, "baseline": baseline_label}
        layout.addWidget(detection_group)

        storage_group = QGroupBox("Trigger Storage")
        storage_layout = QGridLayout(storage_group)
        self.tab2_trigger_storage_check = QCheckBox("Enable trigger storage")
        self.tab2_trigger_storage_check.setChecked(True)
        storage_layout.addWidget(self.tab2_trigger_storage_check, 0, 0, 1, 2)
        storage_layout.addWidget(QLabel("Pre-trigger (s)"), 1, 0)
        self.tab2_pre_trigger_spin = QDoubleSpinBox()
        self.tab2_pre_trigger_spin.setRange(0.1, 30.0)
        self.tab2_pre_trigger_spin.setValue(1.0)
        self.tab2_pre_trigger_spin.setDecimals(1)
        storage_layout.addWidget(self.tab2_pre_trigger_spin, 1, 1)
        storage_layout.addWidget(QLabel("Post-trigger (s)"), 2, 0)
        self.tab2_post_trigger_spin = QDoubleSpinBox()
        self.tab2_post_trigger_spin.setRange(0.1, 30.0)
        self.tab2_post_trigger_spin.setValue(3.0)
        self.tab2_post_trigger_spin.setDecimals(1)
        storage_layout.addWidget(self.tab2_post_trigger_spin, 2, 1)
        storage_layout.addWidget(QLabel("Storage path"), 3, 0, 1, 2)
        self.tab2_storage_path_edit = QLineEdit("D:/PCCP/FIPmonitor")
        storage_layout.addWidget(self.tab2_storage_path_edit, 4, 0, 1, 2)
        layout.addWidget(storage_group)

        alarm_group = QGroupBox("Alarm Summary")
        alarm_layout = QVBoxLayout(alarm_group)
        stats_layout = QGridLayout()
        stats_layout.addWidget(QLabel("Total alarms"), 0, 0)
        self.total_alarms_label = QLabel("0")
        stats_layout.addWidget(self.total_alarms_label, 0, 1)
        stats_layout.addWidget(QLabel("Today"), 1, 0)
        self.today_alarms_label = QLabel("0")
        stats_layout.addWidget(self.today_alarms_label, 1, 1)
        alarm_layout.addLayout(stats_layout)
        self.alarm_table = QTableWidget()
        self.alarm_table.setColumnCount(3)
        self.alarm_table.setHorizontalHeaderLabels(["Time", "Duration", "Feature Count"])
        self.alarm_table.setMaximumHeight(220)
        alarm_layout.addWidget(self.alarm_table)
        layout.addWidget(alarm_group)

        self.clear_alarms_btn = QPushButton("Clear alarm history")
        layout.addWidget(self.clear_alarms_btn)
        layout.addStretch()
        return widget

    def _create_feature_display_panel(self) -> QWidget:
        """Create the Tab2 feature plotting area."""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        self.feature_plots = []
        self.feature_plot_curves = []
        self.feature_threshold_lines = []
        for index in range(4):
            plot_widget = pg.PlotWidget(title=f"Feature Plot {index + 1}")
            plot_widget.setLabel('left', 'Value')
            plot_widget.setLabel('bottom', 'Time', units='s')
            plot_widget.showGrid(x=True, y=True)
            curve = plot_widget.plot(pen=pg.mkPen(width=2))
            threshold_line = pg.InfiniteLine(pos=0, angle=0, pen=pg.mkPen('r', width=1, style=Qt.DashLine))
            plot_widget.addItem(threshold_line)
            self.feature_plots.append(plot_widget)
            self.feature_plot_curves.append(curve)
            self.feature_threshold_lines.append(threshold_line)
            layout.addWidget(plot_widget)
        return widget

    def _handle_tab2_plot_checkbox_change(self, checked: bool) -> None:
        """Keep the number of plotted features within four."""
        if checked and len([1 for item in self.detection_feature_checkboxes.values() if item["plot"].isChecked()]) > 4:
            sender = self.sender()
            if sender is not None:
                sender.blockSignals(True)
                sender.setChecked(False)
                sender.blockSignals(False)
            return
        self._emit_tab2_settings_changed()

    def _create_tab3(self):
        """Create Tab3 - DAS receive, alignment, plots, and raw storage."""
        tab3 = QWidget()
        self.tab_widget.addTab(tab3, "eDAS")

        main_layout = QHBoxLayout(tab3)

        left_panel = QWidget()
        left_panel.setMaximumWidth(420)
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(6)

        self._tab3_space_time_levels_locked = False
        self._tab3_colormap_options = [
            ("Jet", "jet"),
            ("Viridis", "viridis"),
            ("Plasma", "plasma"),
            ("Inferno", "inferno"),
            ("Magma", "magma"),
            ("Seismic", "seismic"),
            ("Gray", "gray"),
            ("Hot", "hot"),
            ("Cool", "cool"),
        ]

        comm_group = QGroupBox("DAS Communication")
        comm_layout = QGridLayout(comm_group)
        comm_layout.setHorizontalSpacing(6)
        comm_layout.addWidget(QLabel("IP"), 0, 0)
        self.tab3_ip_edit = QLineEdit("0.0.0.0")
        comm_layout.addWidget(self.tab3_ip_edit, 0, 1)
        comm_layout.addWidget(QLabel("Port"), 0, 2)
        self.tab3_port_spin = QSpinBox()
        self.tab3_port_spin.setRange(1024, 65535)
        self.tab3_port_spin.setValue(3678)
        comm_layout.addWidget(self.tab3_port_spin, 0, 3)
        comm_layout.addWidget(QLabel("Status"), 1, 0)
        self.tab3_conn_status_label = QLabel("Disconnected")
        self.tab3_conn_status_label.setStyleSheet("color: red; font-weight: bold;")
        comm_layout.addWidget(self.tab3_conn_status_label, 1, 1, 1, 3)
        comm_layout.addWidget(QLabel("Packets"), 2, 0)
        self.tab3_packet_count_label = QLabel("0")
        comm_layout.addWidget(self.tab3_packet_count_label, 2, 1)
        comm_layout.addWidget(QLabel("Last Comm"), 2, 2)
        self.tab3_last_comm_label = QLabel("-")
        comm_layout.addWidget(self.tab3_last_comm_label, 2, 3)
        left_layout.addWidget(comm_group)

        header_group = QGroupBox("Live Header")
        header_layout = QGridLayout(header_group)
        header_layout.setHorizontalSpacing(6)
        header_layout.setVerticalSpacing(4)
        header_layout.addWidget(QLabel("Channels"), 0, 0)
        self.tab3_channel_count_label = QLabel("-")
        header_layout.addWidget(self.tab3_channel_count_label, 0, 1)
        header_layout.addWidget(QLabel("Sample Rate"), 0, 2)
        self.tab3_sample_rate_label = QLabel("-")
        header_layout.addWidget(self.tab3_sample_rate_label, 0, 3)
        header_layout.addWidget(QLabel("Data Bytes"), 1, 0)
        self.tab3_data_bytes_label = QLabel("-")
        header_layout.addWidget(self.tab3_data_bytes_label, 1, 1)
        header_layout.addWidget(QLabel("Packet Duration"), 1, 2)
        self.tab3_packet_duration_label = QLabel("-")
        header_layout.addWidget(self.tab3_packet_duration_label, 1, 3)
        left_layout.addWidget(header_group)

        align_group = QGroupBox("Alignment Status")
        align_layout = QGridLayout(align_group)
        align_layout.addWidget(QLabel("FIP Comm"), 0, 0)
        self.tab3_align_fip_comm_label = QLabel("-")
        align_layout.addWidget(self.tab3_align_fip_comm_label, 0, 1)
        align_layout.addWidget(QLabel("DAS Comm"), 1, 0)
        self.tab3_align_das_comm_label = QLabel("-")
        align_layout.addWidget(self.tab3_align_das_comm_label, 1, 1)
        align_layout.addWidget(QLabel("Status"), 2, 0)
        self.tab3_alignment_status_label = QLabel("waiting")
        align_layout.addWidget(self.tab3_alignment_status_label, 2, 1)
        align_layout.addWidget(QLabel("FIP Missing"), 3, 0)
        self.tab3_fip_missing_label = QLabel("0")
        align_layout.addWidget(self.tab3_fip_missing_label, 3, 1)
        align_layout.addWidget(QLabel("DAS Missing"), 4, 0)
        self.tab3_das_missing_label = QLabel("0")
        align_layout.addWidget(self.tab3_das_missing_label, 4, 1)
        align_layout.addWidget(QLabel("Recent Gaps"), 5, 0, 1, 2)
        self.tab3_missing_ranges_label = QLabel("-")
        self.tab3_missing_ranges_label.setWordWrap(True)
        align_layout.addWidget(self.tab3_missing_ranges_label, 6, 0, 1, 2)
        left_layout.addWidget(align_group)

        curve_group = QGroupBox("Curve Controls")
        curve_layout = QGridLayout(curve_group)
        curve_layout.addWidget(QLabel("Curve 1"), 0, 0)
        self.tab3_curve1_combo = QComboBox()
        self.tab3_curve1_combo.addItems(["Off", "DAS Channel", "FIP"])
        self.tab3_curve1_combo.setCurrentText("DAS Channel")
        curve_layout.addWidget(self.tab3_curve1_combo, 0, 1)
        curve_layout.addWidget(QLabel("Curve 2"), 1, 0)
        self.tab3_curve2_combo = QComboBox()
        self.tab3_curve2_combo.addItems(["Off", "DAS Channel", "FIP"])
        self.tab3_curve2_combo.setCurrentText("FIP")
        curve_layout.addWidget(self.tab3_curve2_combo, 1, 1)
        curve_layout.addWidget(QLabel("DAS Channel"), 2, 0)
        self.tab3_das_channel_spin = QSpinBox()
        self.tab3_das_channel_spin.setRange(0, 4000)
        curve_layout.addWidget(self.tab3_das_channel_spin, 2, 1)
        curve_layout.addWidget(QLabel("Display Seconds"), 3, 0)
        self.tab3_display_seconds_spin = QDoubleSpinBox()
        self.tab3_display_seconds_spin.setRange(0.2, 10.0)
        self.tab3_display_seconds_spin.setValue(1.0)
        self.tab3_display_seconds_spin.setDecimals(1)
        curve_layout.addWidget(self.tab3_display_seconds_spin, 3, 1)
        self.tab3_filter_enable_check = QCheckBox("Apply DAS band-pass")
        curve_layout.addWidget(self.tab3_filter_enable_check, 4, 0, 1, 2)
        curve_layout.addWidget(QLabel("Low Hz"), 5, 0)
        self.tab3_low_freq_spin = QSpinBox()
        self.tab3_low_freq_spin.setRange(1, 500000)
        self.tab3_low_freq_spin.setValue(100)
        curve_layout.addWidget(self.tab3_low_freq_spin, 5, 1)
        curve_layout.addWidget(QLabel("High Hz"), 6, 0)
        self.tab3_high_freq_spin = QSpinBox()
        self.tab3_high_freq_spin.setRange(2, 500000)
        self.tab3_high_freq_spin.setValue(2000)
        curve_layout.addWidget(self.tab3_high_freq_spin, 6, 1)
        left_layout.addWidget(curve_group)

        space_group = QGroupBox("Space-Time Controls")
        space_layout = QGridLayout(space_group)
        space_layout.setHorizontalSpacing(6)
        space_layout.addWidget(QLabel("Channel Start"), 0, 0)
        self.tab3_channel_start_spin = QSpinBox()
        self.tab3_channel_start_spin.setRange(0, 4000)
        self.tab3_channel_start_spin.setValue(0)
        space_layout.addWidget(self.tab3_channel_start_spin, 0, 1)
        space_layout.addWidget(QLabel("Channel End"), 0, 2)
        self.tab3_channel_end_spin = QSpinBox()
        self.tab3_channel_end_spin.setRange(0, 4000)
        self.tab3_channel_end_spin.setValue(199)
        space_layout.addWidget(self.tab3_channel_end_spin, 0, 3)
        space_layout.addWidget(QLabel("Time Downsample"), 1, 0)
        self.tab3_time_downsample_spin = QSpinBox()
        self.tab3_time_downsample_spin.setRange(1, 100)
        self.tab3_time_downsample_spin.setValue(1)
        space_layout.addWidget(self.tab3_time_downsample_spin, 1, 1)
        space_layout.addWidget(QLabel("Space Downsample"), 1, 2)
        self.tab3_space_downsample_spin = QSpinBox()
        self.tab3_space_downsample_spin.setRange(1, 100)
        self.tab3_space_downsample_spin.setValue(1)
        space_layout.addWidget(self.tab3_space_downsample_spin, 1, 3)
        left_layout.addWidget(space_group)

        storage_group = QGroupBox("Joint Raw Storage")
        storage_layout = QGridLayout(storage_group)
        self.tab3_storage_toggle_btn = QPushButton()
        self.tab3_storage_toggle_btn.setCheckable(True)
        storage_layout.addWidget(self.tab3_storage_toggle_btn, 0, 0, 1, 2)
        storage_layout.addWidget(QLabel("Path"), 1, 0, 1, 2)
        self.tab3_storage_path_edit = QLineEdit("D:/PCCP/FIPeDASDATA")
        storage_layout.addWidget(self.tab3_storage_path_edit, 2, 0, 1, 2)
        storage_layout.addWidget(QLabel("Interval(s)"), 3, 0)
        self.tab3_storage_interval_spin = QDoubleSpinBox()
        self.tab3_storage_interval_spin.setRange(1.0, 60.0)
        self.tab3_storage_interval_spin.setValue(10.0)
        self.tab3_storage_interval_spin.setDecimals(1)
        storage_layout.addWidget(self.tab3_storage_interval_spin, 3, 1)
        storage_layout.addWidget(QLabel("Cache(s)"), 4, 0)
        self.tab3_cache_seconds_spin = QDoubleSpinBox()
        self.tab3_cache_seconds_spin.setRange(5.0, 60.0)
        self.tab3_cache_seconds_spin.setValue(10.0)
        self.tab3_cache_seconds_spin.setDecimals(1)
        storage_layout.addWidget(self.tab3_cache_seconds_spin, 4, 1)
        storage_layout.addWidget(QLabel("Last File"), 5, 0, 1, 2)
        self.tab3_last_storage_label = QLabel("-")
        self.tab3_last_storage_label.setWordWrap(True)
        storage_layout.addWidget(self.tab3_last_storage_label, 6, 0, 1, 2)
        left_layout.addWidget(storage_group)

        control_group = QGroupBox("Tab3 Control")
        control_layout = QVBoxLayout(control_group)
        control_layout.setSpacing(8)
        self.tab3_start_stop_btn = QPushButton("Start DAS Monitoring")
        self.tab3_start_stop_btn.setCheckable(True)
        control_layout.addWidget(self.tab3_start_stop_btn)
        self.tab3_plot_toggle_btn = QPushButton()
        self.tab3_plot_toggle_btn.setCheckable(True)
        self.tab3_plot_toggle_btn.setChecked(True)
        control_layout.addWidget(self.tab3_plot_toggle_btn)
        left_layout.addWidget(control_group)
        left_layout.addStretch()

        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(6)
        splitter = QSplitter(Qt.Vertical)
        right_layout.addWidget(splitter)

        self.tab3_curve1_plot = pg.PlotWidget(title="Curve 1")
        self.tab3_curve1_plot.showGrid(x=True, y=True)
        self.tab3_curve1_plot.setLabel("bottom", "Time", units="s")
        self.tab3_curve1_plot.setLabel("left", "Amplitude")
        self.tab3_curve1_das_curve = self.tab3_curve1_plot.plot(pen=pg.mkPen("#1f77b4", width=2))
        self.tab3_curve1_fip_curve = self.tab3_curve1_plot.plot(pen=pg.mkPen("#d62728", width=2))
        splitter.addWidget(self.tab3_curve1_plot)

        self.tab3_curve2_plot = pg.PlotWidget(title="Curve 2")
        self.tab3_curve2_plot.showGrid(x=True, y=True)
        self.tab3_curve2_plot.setLabel("bottom", "Time", units="s")
        self.tab3_curve2_plot.setLabel("left", "Amplitude")
        self.tab3_curve2_das_curve = self.tab3_curve2_plot.plot(pen=pg.mkPen("#1f77b4", width=2))
        self.tab3_curve2_fip_curve = self.tab3_curve2_plot.plot(pen=pg.mkPen("#d62728", width=2))
        splitter.addWidget(self.tab3_curve2_plot)

        tab3_space_time_panel = QWidget()
        tab3_space_time_layout = QVBoxLayout(tab3_space_time_panel)
        tab3_space_time_layout.setContentsMargins(0, 0, 0, 0)
        tab3_space_time_layout.setSpacing(4)

        tab3_space_time_controls = QWidget()
        tab3_space_time_controls_layout = QHBoxLayout(tab3_space_time_controls)
        tab3_space_time_controls_layout.setContentsMargins(0, 0, 0, 0)
        tab3_space_time_controls_layout.setSpacing(6)
        tab3_space_time_controls_layout.addWidget(QLabel("Colormap"))
        self.tab3_colormap_combo = QComboBox()
        for text, value in self._tab3_colormap_options:
            self.tab3_colormap_combo.addItem(text, value)
        self.tab3_colormap_combo.setCurrentText("Jet")
        tab3_space_time_controls_layout.addWidget(self.tab3_colormap_combo)
        tab3_space_time_controls_layout.addWidget(QLabel("Vmin"))
        self.tab3_vmin_spin = QDoubleSpinBox()
        self.tab3_vmin_spin.setRange(-1e9, 1e9)
        self.tab3_vmin_spin.setDecimals(6)
        self.tab3_vmin_spin.setSingleStep(0.01)
        self.tab3_vmin_spin.setValue(-0.1)
        self.tab3_vmin_spin.setMinimumWidth(95)
        tab3_space_time_controls_layout.addWidget(self.tab3_vmin_spin)
        tab3_space_time_controls_layout.addWidget(QLabel("Vmax"))
        self.tab3_vmax_spin = QDoubleSpinBox()
        self.tab3_vmax_spin.setRange(-1e9, 1e9)
        self.tab3_vmax_spin.setDecimals(6)
        self.tab3_vmax_spin.setSingleStep(0.01)
        self.tab3_vmax_spin.setValue(0.1)
        self.tab3_vmax_spin.setMinimumWidth(95)
        tab3_space_time_controls_layout.addWidget(self.tab3_vmax_spin)
        tab3_space_time_controls_layout.addStretch()
        tab3_space_time_layout.addWidget(tab3_space_time_controls)

        tab3_space_time_plot_row = QWidget()
        tab3_space_time_plot_layout = QHBoxLayout(tab3_space_time_plot_row)
        tab3_space_time_plot_layout.setContentsMargins(0, 0, 0, 0)
        tab3_space_time_plot_layout.setSpacing(6)

        self.tab3_space_time_plot = pg.PlotWidget(title="DAS Space-Time")
        self.tab3_space_time_plot.setLabel("bottom", "Time", units="s")
        self.tab3_space_time_plot.setLabel("left", "Channel")
        self.tab3_space_time_image = pg.ImageItem(axisOrder="row-major")
        self.tab3_space_time_plot.addItem(self.tab3_space_time_image)
        tab3_space_time_plot_layout.addWidget(self.tab3_space_time_plot, 1)

        self.tab3_space_time_histogram = pg.HistogramLUTWidget()
        self.tab3_space_time_histogram.setMinimumWidth(120)
        self.tab3_space_time_histogram.setMaximumWidth(140)
        self.tab3_space_time_histogram.setImageItem(self.tab3_space_time_image)
        tab3_space_time_plot_layout.addWidget(self.tab3_space_time_histogram)

        tab3_space_time_layout.addWidget(tab3_space_time_plot_row, 1)
        splitter.addWidget(tab3_space_time_panel)
        splitter.setSizes([250, 250, 400])

        main_layout.addWidget(left_panel, stretch=1)
        main_layout.addWidget(right_panel, stretch=3)

        self._update_tab3_monitor_button_state(False)
        self._update_tab3_plot_button_state(True)
        self._update_tab3_storage_button_state(False)
        self._apply_tab3_space_time_colormap()
        self._apply_tab3_space_time_levels()

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
                    "enabled": self.phase_storage_check.isChecked(),
                    "interval": self.storage_interval_spin.value()
                },
                "path": self.storage_path_edit.text()
            }
        }

        # 如果Tab2控件存在，添加特征和检测配置
        if hasattr(self, 'detection_feature_checkboxes'):
            config["tab2"] = {
                "compute_features": self.get_tab2_compute_enabled_features(),
                "plot_features": self.get_tab2_plot_enabled_features(),
                "preprocess": self.get_tab2_preprocess_settings(),
                "window": self.get_tab2_window_settings(),
                "thresholds": self.get_threshold_factors(),
                "trigger_storage": self.get_tab2_storage_settings(),
            }

        if hasattr(self, 'tab3_ip_edit'):
            config["tab3"] = self.get_tab3_settings()

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

    def update_feature_displays(self, features: Dict[str, Dict[str, Any]]):
        """Update the Tab2 feature plots."""
        feature_names = list(features.keys())[:4]
        for index, plot_widget in enumerate(self.feature_plots):
            if index >= len(feature_names):
                self.feature_plot_curves[index].setData([], [])
                self.feature_threshold_lines[index].setValue(0.0)
                plot_widget.setTitle(f"Feature Plot {index + 1}")
                continue

            feature_name = feature_names[index]
            payload = features[feature_name]
            self.feature_plot_curves[index].setData(payload.get("times", []), payload.get("values", []))
            self.feature_threshold_lines[index].setValue(float(payload.get("threshold", 0.0)))
            plot_widget.setTitle(feature_name)

    def add_alarm_event(self, event):
        """Add one aggregated alarm event to the UI table."""
        row_count = self.alarm_table.rowCount()
        self.alarm_table.insertRow(row_count)
        self.alarm_table.setItem(row_count, 0, QTableWidgetItem(f"{event.start_time:.3f}s"))
        self.alarm_table.setItem(row_count, 1, QTableWidgetItem(f"{event.duration:.3f}s"))
        self.alarm_table.setItem(row_count, 2, QTableWidgetItem(str(event.trigger_feature_count)))
        self.alarm_table.scrollToBottom()
        total_alarms = self.alarm_table.rowCount()
        self.total_alarms_label.setText(str(total_alarms))
        self.today_alarms_label.setText(str(total_alarms))

    def update_baselines(self, baselines: Dict[str, float]):
        """Update baseline labels shown in the Tab2 parameter panel."""
        try:
            for feature_name, baseline_value in baselines.items():
                if feature_name in self.threshold_controls:
                    self.threshold_controls[feature_name]['baseline'].setText(f"{baseline_value:.3f}")
        except Exception as e:
            logger = logging.getLogger(__name__)
            logger.error(f"Error updating baselines: {e}")

    def get_tab2_compute_enabled_features(self) -> Dict[str, bool]:
        """Return the compute-enabled features from Tab2."""
        return {
            key: controls["compute"].isChecked()
            for key, controls in self.detection_feature_checkboxes.items()
        }

    def get_tab2_plot_enabled_features(self) -> Dict[str, bool]:
        """Return the plot-enabled features from Tab2."""
        return {
            key: controls["plot"].isChecked()
            for key, controls in self.detection_feature_checkboxes.items()
        }

    def get_threshold_factors(self) -> Dict[str, float]:
        """Return the current per-feature threshold multipliers."""
        return {key: ctrl['threshold'].value() for key, ctrl in self.threshold_controls.items()}

    def get_tab2_preprocess_settings(self) -> Dict[str, Any]:
        """Return the current Tab2 preprocess settings."""
        return {
            "enabled": self.tab2_filter_enable_check.isChecked(),
            "low_hz": self.tab2_low_freq_spin.value(),
            "high_hz": self.tab2_high_freq_spin.value(),
            "order": self.tab2_filter_order_spin.value(),
        }

    def get_tab2_window_settings(self) -> Dict[str, float]:
        """Return the current Tab2 window and display settings."""
        return {
            "window_seconds": self.tab2_window_spin.value(),
            "overlap_ratio": self.tab2_overlap_spin.value() / 100.0,
            "display_duration_seconds": float(self.tab2_plot_duration_spin.value()),
        }

    def get_tab2_storage_settings(self) -> Dict[str, Any]:
        """Return the current trigger storage settings."""
        return {
            "enabled": self.tab2_trigger_storage_check.isChecked(),
            "pre_trigger_seconds": self.tab2_pre_trigger_spin.value(),
            "post_trigger_seconds": self.tab2_post_trigger_spin.value(),
            "path": self.tab2_storage_path_edit.text(),
        }

    def get_tab3_settings(self) -> Dict[str, Any]:
        """Return the current Tab3 DAS settings."""
        return {
            "communication": {
                "ip": self.tab3_ip_edit.text(),
                "port": self.tab3_port_spin.value(),
            },
            "plot": {
                "curve1_type": self.tab3_curve1_combo.currentText(),
                "curve2_type": self.tab3_curve2_combo.currentText(),
                "das_channel": self.tab3_das_channel_spin.value(),
                "display_seconds": self.tab3_display_seconds_spin.value(),
                "apply_filter": self.tab3_filter_enable_check.isChecked(),
                "low_hz": self.tab3_low_freq_spin.value(),
                "high_hz": self.tab3_high_freq_spin.value(),
                "channel_start": self.tab3_channel_start_spin.value(),
                "channel_end": self.tab3_channel_end_spin.value(),
                "time_downsample": self.tab3_time_downsample_spin.value(),
                "space_downsample": self.tab3_space_downsample_spin.value(),
                "plot_enabled": self.is_tab3_plot_enabled(),
                "colormap": self.tab3_colormap_combo.currentData(),
                "vmin": self.tab3_vmin_spin.value(),
                "vmax": self.tab3_vmax_spin.value(),
            },
            "storage": {
                "enabled": self.tab3_storage_toggle_btn.isChecked(),
                "path": self.tab3_storage_path_edit.text(),
                "interval_seconds": self.tab3_storage_interval_spin.value(),
                "cache_seconds": self.tab3_cache_seconds_spin.value(),
            },
        }

    def update_tab3_connection_status(self, connected: bool, message: str):
        """Update Tab3 DAS connection state."""
        self.tab3_conn_status_label.setText("Connected" if connected else "Disconnected")
        self.tab3_conn_status_label.setStyleSheet(
            "color: green; font-weight: bold;" if connected else "color: red; font-weight: bold;"
        )
        self.status_bar.showMessage(message, 3000)

    def update_tab3_header_status(self, payload: Dict[str, Any]):
        """Update Tab3 header labels."""
        self.tab3_channel_count_label.setText(str(payload.get("channel_count", "-")))
        sample_rate_hz = payload.get("sample_rate_hz", "-")
        self.tab3_sample_rate_label.setText(f"{sample_rate_hz} Hz")
        self.tab3_data_bytes_label.setText(str(payload.get("data_bytes", "-")))
        duration = payload.get("packet_duration_seconds", "-")
        self.tab3_packet_duration_label.setText(f"{duration} s")
        self.tab3_last_comm_label.setText(str(payload.get("comm_count", "-")))

    def update_tab3_packet_statistics(self, stats: Dict[str, Any]):
        """Update Tab3 packet counters."""
        self.tab3_packet_count_label.setText(str(stats.get("packets_received", 0)))

    def update_tab3_alignment_status(self, payload: Dict[str, Any]):
        """Update Tab3 alignment summary labels."""
        self.tab3_align_fip_comm_label.setText(str(payload.get("fip_last_comm_count", -1)))
        self.tab3_align_das_comm_label.setText(str(payload.get("das_last_comm_count", -1)))
        self.tab3_alignment_status_label.setText(str(payload.get("alignment_status", "waiting")))
        self.tab3_fip_missing_label.setText(str(payload.get("fip_missing_count", 0)))
        self.tab3_das_missing_label.setText(str(payload.get("das_missing_count", 0)))
        gaps = payload.get("missing_ranges", [])
        self.tab3_missing_ranges_label.setText(", ".join(gaps) if gaps else "-")

    def update_tab3_storage_status(self, path: str):
        """Show the latest Tab3 storage file path."""
        self.tab3_last_storage_label.setText(path)

    def update_tab3_fip_curve(self, comm_count: int, values, sample_rate_hz: float):
        """Update cached FIP comparison curves shown in Tab3."""
        if len(values) == 0 or not self.is_tab3_plot_enabled():
            return
        times = (comm_count * 0.2) + np.arange(len(values), dtype=np.float64) / max(sample_rate_hz, 1.0)
        self._render_tab3_curve(self.tab3_curve1_fip_curve, self.tab3_curve1_combo.currentText(), times, values, "FIP")
        self._render_tab3_curve(self.tab3_curve2_fip_curve, self.tab3_curve2_combo.currentText(), times, values, "FIP")

    def update_tab3_plot_payload(self, payload: Dict[str, Any]):
        """Apply the latest DAS plot payload to Tab3 widgets."""
        self.update_tab3_header_status(payload.get("header", {}))
        if not self.is_tab3_plot_enabled():
            return
        das_times = payload.get("das_curve_time", [])
        das_values = payload.get("das_curve_values", [])
        self._render_tab3_curve(self.tab3_curve1_das_curve, self.tab3_curve1_combo.currentText(), das_times, das_values, "DAS Channel")
        self._render_tab3_curve(self.tab3_curve2_das_curve, self.tab3_curve2_combo.currentText(), das_times, das_values, "DAS Channel")

        matrix = payload.get("space_time_matrix")
        x_axis = payload.get("space_time_x")
        y_axis = payload.get("space_time_y")
        if matrix is None or len(np.shape(matrix)) != 2 or matrix.size == 0:
            self._reset_tab3_space_time_image()
            return
        matrix = np.asarray(matrix, dtype=np.float64)
        levels = self._compute_tab3_space_time_levels(matrix)
        x_scale = 1.0
        x_offset = 0.0
        if x_axis is not None and len(x_axis) > 1:
            x_scale = float(x_axis[1] - x_axis[0])
            x_offset = float(x_axis[0])
        y_scale = 1.0
        y_offset = 0.0
        if y_axis is not None and len(y_axis) > 1:
            y_scale = float(y_axis[1] - y_axis[0])
            y_offset = float(y_axis[0])
        x_width = max(x_scale, 1e-12) * matrix.shape[1]
        y_height = max(y_scale, 1e-12) * matrix.shape[0]
        if not self._tab3_space_time_levels_locked:
            self._set_tab3_space_time_levels(levels[0], levels[1], lock=False)
            levels = (self.tab3_vmin_spin.value(), self.tab3_vmax_spin.value())
        else:
            levels = (self.tab3_vmin_spin.value(), self.tab3_vmax_spin.value())
        self.tab3_space_time_image.setImage(matrix, autoLevels=False, levels=levels)
        self.tab3_space_time_image.setRect(x_offset, y_offset, x_width, y_height)
        self._apply_tab3_space_time_levels()

    def reset_tab3_views(self):
        """Clear Tab3 plots and status labels."""
        self.tab3_curve1_das_curve.setData([], [])
        self.tab3_curve1_fip_curve.setData([], [])
        self.tab3_curve2_das_curve.setData([], [])
        self.tab3_curve2_fip_curve.setData([], [])
        self._reset_tab3_space_time_image()
        self.tab3_last_storage_label.setText("-")
        self.tab3_packet_count_label.setText("0")
        self.tab3_last_comm_label.setText("-")
        self.tab3_missing_ranges_label.setText("-")

    def show_tab3_error(self, message: str):
        """Show one Tab3-specific error popup."""
        self.status_bar.showMessage(message, 5000)
        msg_box = QMessageBox(self)
        msg_box.setIcon(QMessageBox.Warning)
        msg_box.setWindowTitle("Tab3 Warning")
        msg_box.setText(message)
        msg_box.exec_()

    def _compute_tab3_space_time_levels(self, matrix: np.ndarray) -> Tuple[float, float]:
        """Build stable display levels for the Tab3 space-time float image."""
        finite_values = matrix[np.isfinite(matrix)]
        if finite_values.size == 0:
            return (0.0, 1.0)
        low = float(np.percentile(finite_values, 1.0))
        high = float(np.percentile(finite_values, 99.0))
        if not np.isfinite(low) or not np.isfinite(high) or low >= high:
            low = float(np.min(finite_values))
            high = float(np.max(finite_values))
        if not np.isfinite(low) or not np.isfinite(high) or low >= high:
            center = float(finite_values[0])
            return (center - 0.5, center + 0.5)
        return (low, high)

    def _create_tab3_custom_colormap(self, name: str):
        """Create a small set of custom colormaps used by Tab3."""
        gradients = {
            "jet": np.array([
                [0, 0, 127],
                [0, 0, 255],
                [0, 127, 255],
                [0, 255, 255],
                [127, 255, 127],
                [255, 255, 0],
                [255, 127, 0],
                [255, 0, 0],
                [127, 0, 0],
            ]),
            "hot": np.array([
                [0, 0, 0],
                [120, 0, 0],
                [220, 0, 0],
                [255, 80, 0],
                [255, 180, 0],
                [255, 255, 0],
                [255, 255, 180],
                [255, 255, 255],
            ]),
            "cool": np.array([
                [0, 255, 255],
                [80, 200, 255],
                [120, 150, 255],
                [180, 100, 255],
                [255, 0, 255],
            ]),
            "gray": np.array([
                [0, 0, 0],
                [255, 255, 255],
            ]),
            "seismic": np.array([
                [0, 0, 90],
                [0, 0, 255],
                [180, 180, 255],
                [255, 255, 255],
                [255, 150, 150],
                [255, 0, 0],
                [90, 0, 0],
            ]),
        }
        colors = gradients.get(name)
        if colors is None:
            return None
        return pg.ColorMap(np.linspace(0.0, 1.0, len(colors)), colors)

    def _get_tab3_colormap(self):
        """Get the selected Tab3 colormap object."""
        name = self.tab3_colormap_combo.currentData()
        try:
            return pg.colormap.get(name)
        except Exception:
            return self._create_tab3_custom_colormap(name)

    def _apply_tab3_space_time_colormap(self):
        """Apply the selected colormap to the image and histogram."""
        colormap = self._get_tab3_colormap()
        if colormap is None:
            return
        self.tab3_space_time_image.setColorMap(colormap)
        if hasattr(self, "tab3_space_time_histogram"):
            self.tab3_space_time_histogram.gradient.setColorMap(colormap)

    def _set_tab3_space_time_levels(self, vmin: float, vmax: float, lock: bool = True):
        """Update Tab3 vmin/vmax controls safely."""
        if vmin >= vmax:
            center = (vmin + vmax) * 0.5
            vmin = center - 0.5
            vmax = center + 0.5
        self.tab3_vmin_spin.blockSignals(True)
        self.tab3_vmax_spin.blockSignals(True)
        self.tab3_vmin_spin.setValue(vmin)
        self.tab3_vmax_spin.setValue(vmax)
        self.tab3_vmin_spin.blockSignals(False)
        self.tab3_vmax_spin.blockSignals(False)
        self._tab3_space_time_levels_locked = lock

    def _apply_tab3_space_time_levels(self):
        """Apply the current vmin/vmax settings to the Tab3 image and histogram."""
        vmin = self.tab3_vmin_spin.value()
        vmax = self.tab3_vmax_spin.value()
        if vmin >= vmax:
            vmax = vmin + 1e-6
            self._set_tab3_space_time_levels(vmin, vmax)
            vmin = self.tab3_vmin_spin.value()
            vmax = self.tab3_vmax_spin.value()
        self.tab3_space_time_image.setLevels((vmin, vmax))
        if hasattr(self, "tab3_space_time_histogram"):
            self.tab3_space_time_histogram.setLevels(vmin, vmax)

    def _build_tab3_toggle_button_style(self, checked: bool, active_color: str, inactive_color: str) -> str:
        """Return a shared stylesheet for Tab3 checkable buttons."""
        background = active_color if checked else inactive_color
        hover = active_color if checked else inactive_color
        return f"""
            QPushButton {{
                font-size: 16px;
                font-weight: bold;
                padding: 10px 12px;
                background-color: {background};
                color: white;
                border: none;
                border-radius: 6px;
            }}
            QPushButton:hover {{
                background-color: {hover};
            }}
            QPushButton:pressed {{
                background-color: {background};
            }}
        """

    def _update_tab3_monitor_button_state(self, active: bool):
        """Refresh Tab3 monitoring button text and style."""
        self.tab3_start_stop_btn.blockSignals(True)
        self.tab3_start_stop_btn.setChecked(active)
        self.tab3_start_stop_btn.blockSignals(False)
        self.tab3_start_stop_btn.setText("Stop DAS Monitoring" if active else "Start DAS Monitoring")
        self.tab3_start_stop_btn.setStyleSheet(
            self._build_tab3_toggle_button_style(active, "#d9534f", "#2e8b57")
        )

    def _update_tab3_plot_button_state(self, enabled: bool):
        """Refresh the plot enable button text and style."""
        self.tab3_plot_toggle_btn.blockSignals(True)
        self.tab3_plot_toggle_btn.setChecked(enabled)
        self.tab3_plot_toggle_btn.blockSignals(False)
        self.tab3_plot_toggle_btn.setText("Plot Updates: ON" if enabled else "Plot Updates: OFF")
        self.tab3_plot_toggle_btn.setStyleSheet(
            self._build_tab3_toggle_button_style(enabled, "#1f77b4", "#6c757d")
        )

    def _update_tab3_storage_button_state(self, enabled: bool):
        """Refresh the joint storage button text and style."""
        self.tab3_storage_toggle_btn.blockSignals(True)
        self.tab3_storage_toggle_btn.setChecked(enabled)
        self.tab3_storage_toggle_btn.blockSignals(False)
        self.tab3_storage_toggle_btn.setText("Joint Storage: ON" if enabled else "Joint Storage: OFF")
        self.tab3_storage_toggle_btn.setStyleSheet(
            self._build_tab3_toggle_button_style(enabled, "#9467bd", "#6c757d")
        )

    def _on_tab3_colormap_changed(self, _text: str):
        """Handle Tab3 space-time colormap changes."""
        self._apply_tab3_space_time_colormap()
        self._emit_tab3_settings_changed()

    def _on_tab3_vmin_changed(self, value: float):
        """Handle manual Tab3 vmin changes."""
        self._tab3_space_time_levels_locked = True
        if value >= self.tab3_vmax_spin.value():
            self._set_tab3_space_time_levels(value, value + 1e-6)
        self._apply_tab3_space_time_levels()
        self._emit_tab3_settings_changed()

    def _on_tab3_vmax_changed(self, value: float):
        """Handle manual Tab3 vmax changes."""
        self._tab3_space_time_levels_locked = True
        if value <= self.tab3_vmin_spin.value():
            self._set_tab3_space_time_levels(value - 1e-6, value)
        self._apply_tab3_space_time_levels()
        self._emit_tab3_settings_changed()

    def is_tab3_plot_enabled(self) -> bool:
        """Return whether Tab3 plot widgets should update."""
        return self.tab3_plot_toggle_btn.isChecked()

    def _reset_tab3_space_time_image(self):
        """Restore the Tab3 space-time image to a known empty state."""
        empty = np.zeros((1, 1), dtype=np.float64)
        self.tab3_space_time_image.setImage(
            empty,
            autoLevels=False,
            levels=(self.tab3_vmin_spin.value(), self.tab3_vmax_spin.value()),
        )
        self.tab3_space_time_image.setRect(0.0, 0.0, 1.0, 1.0)
        self._apply_tab3_space_time_levels()

    def _render_tab3_curve(self, curve_item, curve_mode: str, times, values, expected_mode: str):
        """Render one Tab3 line only when the current UI mode matches."""
        if curve_mode != expected_mode:
            curve_item.setData([], [])
            return
        curve_item.setData(times, values)

    def clear_alarm_table(self):
        """Clear the alarm table and counters."""
        self.alarm_table.setRowCount(0)
        self.total_alarms_label.setText("0")
        self.today_alarms_label.setText("0")

    def clear_feature_displays(self):
        """Clear all feature plots."""
        for index, plot_widget in enumerate(self.feature_plots):
            self.feature_plot_curves[index].setData([], [])
            self.feature_threshold_lines[index].setValue(0.0)
            plot_widget.setTitle(f"Feature Plot {index + 1}")

    def _emit_tab2_settings_changed(self):
        """Emit a unified Tab2 settings-changed signal."""
        if hasattr(self, 'tab2_settings_changed'):
            self.tab2_settings_changed.emit()

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

            tab2_widgets = [
                getattr(self, 'tab2_filter_enable_check', None),
                getattr(self, 'tab2_low_freq_spin', None),
                getattr(self, 'tab2_high_freq_spin', None),
                getattr(self, 'tab2_filter_order_spin', None),
                getattr(self, 'tab2_window_spin', None),
                getattr(self, 'tab2_overlap_spin', None),
                getattr(self, 'tab2_plot_duration_spin', None),
                getattr(self, 'tab2_trigger_storage_check', None),
                getattr(self, 'tab2_pre_trigger_spin', None),
                getattr(self, 'tab2_post_trigger_spin', None),
                getattr(self, 'tab2_storage_path_edit', None),
            ]
            for controls in getattr(self, 'detection_feature_checkboxes', {}).values():
                tab2_widgets.append(controls.get('compute'))
                tab2_widgets.append(controls.get('plot'))
            for ctrl in getattr(self, 'threshold_controls', {}).values():
                tab2_widgets.append(ctrl.get('threshold'))

            for widget in tab2_widgets:
                if widget is None:
                    continue
                if hasattr(widget, 'valueChanged'):
                    widget.valueChanged.connect(self._emit_tab2_settings_changed)
                elif hasattr(widget, 'toggled'):
                    widget.toggled.connect(self._emit_tab2_settings_changed)
                elif hasattr(widget, 'textChanged'):
                    widget.textChanged.connect(self._emit_tab2_settings_changed)

            if hasattr(self, 'clear_alarms_btn'):
                self.clear_alarms_btn.clicked.connect(self.tab2_clear_alarms_requested.emit)

            if hasattr(self, 'tab3_start_stop_btn'):
                self.tab3_start_stop_btn.toggled.connect(self._update_tab3_monitor_button_state)
                self.tab3_start_stop_btn.clicked.connect(self._toggle_tab3_monitoring)
            if hasattr(self, 'tab3_plot_toggle_btn'):
                self.tab3_plot_toggle_btn.toggled.connect(self._update_tab3_plot_button_state)
                self.tab3_plot_toggle_btn.toggled.connect(self._emit_tab3_settings_changed)
            if hasattr(self, 'tab3_storage_toggle_btn'):
                self.tab3_storage_toggle_btn.toggled.connect(self._update_tab3_storage_button_state)
                self.tab3_storage_toggle_btn.toggled.connect(self._emit_tab3_settings_changed)
            if hasattr(self, 'tab3_colormap_combo'):
                self.tab3_colormap_combo.currentTextChanged.connect(self._on_tab3_colormap_changed)
            if hasattr(self, 'tab3_vmin_spin'):
                self.tab3_vmin_spin.valueChanged.connect(self._on_tab3_vmin_changed)
            if hasattr(self, 'tab3_vmax_spin'):
                self.tab3_vmax_spin.valueChanged.connect(self._on_tab3_vmax_changed)

            tab3_widgets = [
                getattr(self, 'tab3_ip_edit', None),
                getattr(self, 'tab3_port_spin', None),
                getattr(self, 'tab3_curve1_combo', None),
                getattr(self, 'tab3_curve2_combo', None),
                getattr(self, 'tab3_das_channel_spin', None),
                getattr(self, 'tab3_display_seconds_spin', None),
                getattr(self, 'tab3_filter_enable_check', None),
                getattr(self, 'tab3_low_freq_spin', None),
                getattr(self, 'tab3_high_freq_spin', None),
                getattr(self, 'tab3_channel_start_spin', None),
                getattr(self, 'tab3_channel_end_spin', None),
                getattr(self, 'tab3_time_downsample_spin', None),
                getattr(self, 'tab3_space_downsample_spin', None),
                getattr(self, 'tab3_storage_path_edit', None),
                getattr(self, 'tab3_storage_interval_spin', None),
                getattr(self, 'tab3_cache_seconds_spin', None),
            ]
            for widget in tab3_widgets:
                if widget is None:
                    continue
                if hasattr(widget, 'valueChanged'):
                    widget.valueChanged.connect(self._emit_tab3_settings_changed)
                elif hasattr(widget, 'currentTextChanged'):
                    widget.currentTextChanged.connect(self._emit_tab3_settings_changed)
                elif hasattr(widget, 'toggled'):
                    widget.toggled.connect(self._emit_tab3_settings_changed)
                elif hasattr(widget, 'textChanged'):
                    widget.textChanged.connect(self._emit_tab3_settings_changed)

        except Exception as e:
            import logging
            logger = logging.getLogger(__name__)
            logger.error(f"Error setting up connections: {e}")

    def _toggle_tab3_monitoring(self):
        """Toggle the independent Tab3 DAS monitoring state."""
        if self.tab3_start_stop_btn.isChecked():
            self.tab3_start_requested.emit()
        else:
            self.tab3_stop_requested.emit()

    def set_tab3_monitoring_active(self, active: bool):
        """Sync the Tab3 monitoring button with runtime state."""
        self._update_tab3_monitor_button_state(active)

    def _emit_tab3_settings_changed(self):
        """Emit a unified Tab3 settings-changed signal."""
        if hasattr(self, 'tab3_settings_changed'):
            self.tab3_settings_changed.emit()
