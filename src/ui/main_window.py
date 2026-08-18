"""
Main Window Module for PCCP Monitoring Software

This module implements the main GUI window using PyQt5,
including Tab1 (FIP processing) and Tab2 (Signal detection) layouts.

Author: Claude
Date: 2026-03-11
"""

import sys
import os
import json
import logging
import re
import time
from collections import deque
from pathlib import Path
from typing import Dict, Any, List, Tuple, Optional
import numpy as np
from PyQt5.QtWidgets import (
    QMainWindow, QApplication, QTabWidget, QWidget, QVBoxLayout,
    QHBoxLayout, QGridLayout, QGroupBox, QLabel, QPushButton,
    QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QCheckBox,
    QTextEdit, QTableWidget, QTableWidgetItem, QSplitter,
    QFrame, QStatusBar, QMenuBar, QAction, QMessageBox, QSizePolicy
)
from PyQt5.QtCore import Qt, QTimer, pyqtSignal
from PyQt5.QtGui import QFont, QIcon
import pyqtgraph as pg
from scipy import signal

# 设置PyQtGraph的样式
pg.setConfigOptions(antialias=False)
pg.setConfigOption('background', 'w')
pg.setConfigOption('foreground', 'k')


class MainWindow(QMainWindow):
    """主窗口类"""

    # View 页参数区宽度边界（用户可通过水平 QSplitter 手动调整）。
    VIEW_PARAM_PANEL_MIN_WIDTH = 360
    VIEW_PARAM_PANEL_MAX_WIDTH = 720
    # 默认宽度：较旧版 560px 缩小约 20%（560 * 0.8 = 448）。
    VIEW_PARAM_PANEL_DEFAULT_WIDTH = 448

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
    fip_sensor_settings_changed = pyqtSignal(dict)
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
        font.setPointSize(8)
        self.setFont(font)

        # 去除控件获得焦点时显示的虚线矩形框（tab/下拉列表/输入框等）
        self._apply_global_focus_style()

        # 运行状态先于界面创建，便于按钮和指示灯在构造阶段读取。
        self.monitoring_active = False
        self._edas_monitoring_active = False
        self._both_comm_requested = False
        self._fip_comm_failure_count = 0
        self._edas_comm_failure_count = 0
        self._fip_storage_success_count = 0
        self._fip_storage_failure_count = 0
        self._edas_storage_success_count = 0
        self._edas_storage_failure_count = 0
        self._sync_fip_receive_times: Dict[int, float] = {}
        self._sync_edas_receive_times: Dict[int, float] = {}
        self._sync_deltas: List[float] = []
        self._sync_first_pair: Optional[Tuple[int, float]] = None
        self._sync_latest_pair: Optional[Tuple[int, float]] = None
        self._sync_delta_sum = 0.0
        self._sync_delta_count = 0
        self._sync_matched_counts = set()
        self._persist_logger = logging.getLogger(f"{__name__}.GuiPersistence")
        self._persist_config_path = Path(__file__).resolve().parents[2] / "config" / "gui_last_state.json"
        self._persist_loading = False
        self._auto_save_timer = QTimer(self)
        self._auto_save_timer.setSingleShot(True)
        self._auto_save_timer.setInterval(700)
        self._auto_save_timer.timeout.connect(self._auto_save_configuration)

        # 初始化组件
        self._init_ui()
        self._init_menu()
        self._init_status_bar()
        self._setup_connections()
        self._apply_global_display_runtime_settings()

        # 应用默认的PSD设置范围（解决问题3）
        self._apply_initial_psd_settings()
        self._default_gui_config = self.get_current_config()
        self._load_persisted_configuration()
        self._connect_auto_persist_signals()

    def _apply_global_focus_style(self) -> None:
        """Remove the dashed focus rectangle that appears on tabs and dropdowns."""
        app = QApplication.instance()
        if app is None:
            return
        app.setStyleSheet(
            app.styleSheet()
            + """
            QTabBar::tab:focus,
            QComboBox:focus,
            QComboBox QAbstractItemView,
            QPushButton:focus,
            QCheckBox:focus,
            QSpinBox:focus,
            QDoubleSpinBox:focus,
            QLineEdit:focus {
                outline: none;
            }
            QTabBar::tab:focus {
                border: none;
            }
            """
        )

    def _apply_initial_psd_settings(self):
        """应用初始的PSD设置范围"""
        try:
            # 强制重置PSD参数为正确的默认值
            self.psd_window_length_spin.setValue(0.4)  # 0.4秒

            # 调用PSD设置更新函数，应用默认值
            self._update_psd_settings()
            self._apply_view_refresh_settings()
            self._update_data_comm_buttons()
            self._update_data_storage_buttons()
            self._update_view_psd_curves(force=True)

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
                font-size: 18px;
                min-width: 128px;
                padding: 8px 22px;
                margin: 2px;
            }
            QTabBar::tab:selected {
                background-color: #e3f2fd;
                border-bottom: 2px solid #2196f3;
            }
        """)
        main_layout.addWidget(self.tab_widget)

        # 创建各个标签页。新结构：View / Data / Tab3 / Setting。
        self._create_tab1()  # View：曲线、PSD、空间时间图
        self._create_tab2()  # Data：通信、同步检验、存储
        self._create_tab3()  # 旧 Tab2 信号检测，默认不启动
        self._create_tab4()  # Setting：全局 GUI 和图件字体
        self._update_fip_sensor_controls(emit=False)

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
        # Tab1 is now View: plot controls on the left, live plots on the right.
        tab1 = QWidget()
        self.tab_widget.addTab(tab1, "View")
        self._init_view_runtime_state()

        main_layout = QHBoxLayout(tab1)
        main_layout.setContentsMargins(8, 8, 8, 8)
        main_layout.setSpacing(0)

        # 参数区与绘图区之间用水平 QSplitter 分隔，允许用户拖拽调整参数区宽度。
        self.view_param_splitter = QSplitter(Qt.Horizontal)
        self.view_param_splitter.setChildrenCollapsible(False)
        self.view_param_splitter.setHandleWidth(6)
        self.param_widget = self._create_parameter_panel()
        self.plot_widget = self._create_view_plot_panel()
        self.view_param_splitter.addWidget(self.param_widget)
        self.view_param_splitter.addWidget(self.plot_widget)
        self.view_param_splitter.setStretchFactor(0, 0)
        self.view_param_splitter.setStretchFactor(1, 1)
        self.view_param_splitter.setSizes([self.VIEW_PARAM_PANEL_DEFAULT_WIDTH, 1100])
        main_layout.addWidget(self.view_param_splitter)

    def _init_view_runtime_state(self) -> None:
        # Keep the old tab3 runtime field names because the controller already uses them.
        self._tab3_logger = logging.getLogger(f"{__name__}.ViewUI")
        self._tab3_last_fip_plot_monotonic = 0.0
        self._tab3_last_das_plot_monotonic = 0.0
        self._tab3_fip_plot_min_interval_seconds = 0.5
        self._tab3_das_plot_min_interval_seconds = 0.5
        self._tab3_curve_max_points = 5000
        self._tab3_space_time_max_pixels = 120000
        self._tab3_ui_slow_threshold_ms = 80.0
        self._tab3_last_space_time_rect = None
        self._tab3_space_time_levels_locked = True
        self._view_curve_cache: Dict[int, Dict[str, Any]] = {1: {}, 2: {}}
        self._fip_curve_rolling: Dict[Tuple[int, int], Dict[str, Any]] = {}
        self._view_user_range_active = False
        self._view_psd_update_interval_seconds = 1.0
        self._view_last_psd_update_monotonic = 0.0
        self._view_psd_update_pending = False
        self._view_psd_eps = 1e-24
        self._tab3_colormap_options = [
            ("Jet", "jet"), ("Viridis", "viridis"), ("Plasma", "plasma"),
            ("Inferno", "inferno"), ("Magma", "magma"), ("Seismic", "seismic"),
            ("Gray", "gray"), ("Hot", "hot"), ("Cool", "cool"),
        ]

    def _create_parameter_panel(self) -> QWidget:
        widget = QWidget()
        # 参数区宽度可通过 QSplitter 手动调整；默认宽度约 448px（较旧版 560px 缩小约 20%）。
        widget.setMinimumWidth(self.VIEW_PARAM_PANEL_MIN_WIDTH)
        widget.setMaximumWidth(self.VIEW_PARAM_PANEL_MAX_WIDTH)
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.setSpacing(8)
        layout.addWidget(self._create_view_curve_group())
        layout.addWidget(self._create_das_plot_group())
        layout.addWidget(self._create_processing_group())
        layout.addWidget(self._create_visualization_group())
        layout.addWidget(self._create_view_psd_group())
        layout.addWidget(self._create_view_axis_group())
        layout.addWidget(self._create_space_time_group())
        layout.addStretch()
        return widget

    def _create_view_curve_group(self) -> QGroupBox:
        group = QGroupBox("曲线选择")
        layout = QGridLayout(group)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)

        layout.addWidget(QLabel("Curve1"), 0, 0)
        self.tab3_curve1_combo = QComboBox()
        self.tab3_curve1_combo.addItems(["Off", "DAS Channel", "FIP1", "FIP2"])
        self.tab3_curve1_combo.setCurrentText("FIP1")
        layout.addWidget(self.tab3_curve1_combo, 0, 1)

        layout.addWidget(QLabel("Curve2"), 0, 2)
        self.tab3_curve2_combo = QComboBox()
        self.tab3_curve2_combo.addItems(["Off", "DAS Channel", "FIP1", "FIP2"])
        self.tab3_curve2_combo.setCurrentText("DAS Channel")
        layout.addWidget(self.tab3_curve2_combo, 0, 3)

        layout.addWidget(QLabel("C1 DAS通道"), 1, 0)
        self.tab3_curve1_das_channel_spin = QSpinBox()
        self.tab3_curve1_das_channel_spin.setRange(0, 4000)
        self.tab3_curve1_das_channel_spin.setValue(10)
        layout.addWidget(self.tab3_curve1_das_channel_spin, 1, 1)

        layout.addWidget(QLabel("C2 DAS通道"), 1, 2)
        self.tab3_curve2_das_channel_spin = QSpinBox()
        self.tab3_curve2_das_channel_spin.setRange(0, 4000)
        self.tab3_curve2_das_channel_spin.setValue(10)
        layout.addWidget(self.tab3_curve2_das_channel_spin, 1, 3)

        # Backward-compatible aliases used by older manager code and saved snapshots.
        self.tab3_das_channel_spin = self.tab3_curve2_das_channel_spin
        return group

    def _create_das_plot_group(self) -> QGroupBox:
        group = QGroupBox("DAS绘图")
        layout = QGridLayout(group)
        layout.setColumnStretch(2, 1)

        self.tab3_das_filter_enable_check = QCheckBox("滤波")
        layout.addWidget(self.tab3_das_filter_enable_check, 0, 0)

        layout.addWidget(QLabel("滤波参数(Hz)"), 0, 1)
        self.tab3_das_filter_range_edit = QLineEdit("500-6000")
        self.tab3_das_filter_range_edit.setPlaceholderText("100- / -1000 / 500-6000")
        layout.addWidget(self.tab3_das_filter_range_edit, 0, 2)

        layout.addWidget(QLabel("滤波阶数"), 0, 3)
        self.tab3_das_filter_order_spin = QSpinBox()
        self.tab3_das_filter_order_spin.setRange(1, 10)
        self.tab3_das_filter_order_spin.setValue(4)
        layout.addWidget(self.tab3_das_filter_order_spin, 0, 4)

        self.tab3_filter_enable_check = self.tab3_das_filter_enable_check
        return group

    def _create_processing_group(self) -> QGroupBox:
        group = QGroupBox("FIP绘图")
        layout = QGridLayout(group)
        layout.setColumnStretch(4, 1)

        self.fip_filter_enable_check = QCheckBox("滤波")
        self.fip_filter_enable_check.setChecked(False)
        layout.addWidget(self.fip_filter_enable_check, 0, 0)
        layout.addWidget(QLabel("滤波阶数"), 0, 1)
        self.filter_order_spin = QSpinBox()
        self.filter_order_spin.setRange(1, 10)
        self.filter_order_spin.setValue(4)
        layout.addWidget(self.filter_order_spin, 0, 2)
        layout.addWidget(QLabel("滤波参数(Hz)"), 0, 3)
        self.fip_filter_range_edit = QLineEdit("500-6000")
        self.fip_filter_range_edit.setPlaceholderText("100- / -1000 / 500-6000")
        layout.addWidget(self.fip_filter_range_edit, 0, 4)

        self.fip_phase_unwrap_check = QCheckBox("unwrap")
        self.fip_phase_unwrap_check.setChecked(False)
        layout.addWidget(self.fip_phase_unwrap_check, 1, 0)
        layout.addWidget(QLabel("时域显示降采样"), 1, 1)
        self.downsample_spin = QSpinBox()
        self.downsample_spin.setRange(1, 100)
        self.downsample_spin.setValue(1)
        layout.addWidget(self.downsample_spin, 1, 2)
        layout.addWidget(QLabel("FIP处理目标"), 1, 3)
        self.fip_plot_sensor_combo = QComboBox()
        self.fip_plot_sensor_combo.addItem("FIP1", 1)
        self.fip_plot_sensor_combo.setEnabled(False)
        layout.addWidget(self.fip_plot_sensor_combo, 1, 4)
        return group

    def _create_visualization_group(self) -> QGroupBox:
        group = QGroupBox("刷新参数")
        layout = QGridLayout(group)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)
        layout.addWidget(QLabel("时域窗口(s)"), 0, 0)
        self.time_display_duration_spin = QDoubleSpinBox()
        self.time_display_duration_spin.setToolTip("时域图滚动窗口长度：显示最近多少秒的数据")
        self.time_display_duration_spin.setRange(0.1, 60.0)
        self.time_display_duration_spin.setValue(1.0)
        self.time_display_duration_spin.setSingleStep(0.1)
        self.time_display_duration_spin.setSuffix(" s")
        layout.addWidget(self.time_display_duration_spin, 0, 1)
        layout.addWidget(QLabel("FIP刷新(s)"), 0, 2)
        self.view_fip_refresh_spin = QDoubleSpinBox()
        self.view_fip_refresh_spin.setToolTip("FIP时域图最小刷新间隔（实际受 1 包/秒数据速率限制）")
        self.view_fip_refresh_spin.setRange(0.2, 5.0)
        self.view_fip_refresh_spin.setDecimals(2)
        self.view_fip_refresh_spin.setSingleStep(0.1)
        self.view_fip_refresh_spin.setValue(self._tab3_fip_plot_min_interval_seconds)
        layout.addWidget(self.view_fip_refresh_spin, 0, 3)
        layout.addWidget(QLabel("eDAS刷新(s)"), 1, 0)
        self.view_edas_refresh_spin = QDoubleSpinBox()
        self.view_edas_refresh_spin.setToolTip("eDAS时域图最小刷新间隔")
        self.view_edas_refresh_spin.setRange(0.5, 5.0)
        self.view_edas_refresh_spin.setDecimals(2)
        self.view_edas_refresh_spin.setSingleStep(0.1)
        self.view_edas_refresh_spin.setValue(self._tab3_das_plot_min_interval_seconds)
        layout.addWidget(self.view_edas_refresh_spin, 1, 1)
        layout.addWidget(QLabel("单曲线点数"), 1, 2)
        self.view_curve_max_points_spin = QSpinBox()
        self.view_curve_max_points_spin.setToolTip("每条时域曲线最多绘制的点数（超出自动降采样，用于限制绘图开销）")
        self.view_curve_max_points_spin.setRange(1000, 50000)
        self.view_curve_max_points_spin.setSingleStep(1000)
        self.view_curve_max_points_spin.setValue(self._tab3_curve_max_points)
        layout.addWidget(self.view_curve_max_points_spin, 1, 3)
        plot_control_layout = QGridLayout()
        for column in range(3):
            plot_control_layout.setColumnStretch(column, 1)
        self.time_plot_btn = QPushButton("时域 ON")
        self.time_plot_btn.setCheckable(True)
        self.time_plot_btn.setChecked(True)
        plot_control_layout.addWidget(self.time_plot_btn, 0, 0)
        self.psd_plot_btn = QPushButton("PSD ON")
        self.psd_plot_btn.setCheckable(True)
        self.psd_plot_btn.setChecked(True)
        plot_control_layout.addWidget(self.psd_plot_btn, 0, 1)

        self.tab3_plot_toggle_btn = QPushButton("刷新 ON")
        self.tab3_plot_toggle_btn.setCheckable(True)
        self.tab3_plot_toggle_btn.setChecked(True)
        plot_control_layout.addWidget(self.tab3_plot_toggle_btn, 0, 2)
        for button in (self.time_plot_btn, self.psd_plot_btn, self.tab3_plot_toggle_btn):
            self._style_toggle_button(button, True, min_width=96)
        layout.addLayout(plot_control_layout, 2, 0, 1, 4)
        return group

    def _create_view_psd_group(self) -> QGroupBox:
        group = QGroupBox("PSD设置")
        layout = QGridLayout(group)

        self.view_psd1_check = QCheckBox("PSD1")
        self.view_psd1_check.setChecked(True)
        layout.addWidget(self.view_psd1_check, 0, 0)
        self.view_psd2_check = QCheckBox("PSD2")
        self.view_psd2_check.setChecked(True)
        layout.addWidget(self.view_psd2_check, 0, 1)

        layout.addWidget(QLabel("Welch窗长(s)"), 0, 2)
        self.psd_window_length_spin = QDoubleSpinBox()
        self.psd_window_length_spin.setRange(0.05, 10.0)
        self.psd_window_length_spin.setValue(0.4)
        self.psd_window_length_spin.setSingleStep(0.05)
        self.psd_window_length_spin.setDecimals(2)
        self.psd_window_length_spin.setSuffix(" s")
        layout.addWidget(self.psd_window_length_spin, 0, 3)

        layout.addWidget(QLabel("重叠率(%)"), 0, 4)
        self.psd_overlap_spin = QDoubleSpinBox()
        self.psd_overlap_spin.setRange(0.0, 95.0)
        self.psd_overlap_spin.setValue(50.0)
        self.psd_overlap_spin.setSingleStep(5.0)
        self.psd_overlap_spin.setDecimals(1)
        layout.addWidget(self.psd_overlap_spin, 0, 5)
        return group

    def _create_view_axis_group(self) -> QGroupBox:
        group = QGroupBox("坐标轴")
        layout = QGridLayout(group)
        layout.setColumnStretch(2, 1)
        layout.setColumnStretch(4, 1)
        layout.setColumnStretch(6, 1)

        self.view_axis_enable_check = QCheckBox("手动范围")
        self.view_axis_enable_check.setToolTip("勾选后手动控制时域图 X/Y 与 PSD 图 Y 轴范围")
        layout.addWidget(self.view_axis_enable_check, 0, 0)

        layout.addWidget(QLabel("X范围"), 0, 1)
        self.view_x_range_edit = QLineEdit("0-1")
        self.view_x_range_edit.setPlaceholderText("0-1")
        self.view_x_range_edit.setToolTip("两个时域图横轴范围")
        layout.addWidget(self.view_x_range_edit, 0, 2)

        layout.addWidget(QLabel("Y范围"), 0, 3)
        self.view_y_range_edit = QLineEdit("-1-1")
        self.view_y_range_edit.setPlaceholderText("-1-1")
        self.view_y_range_edit.setToolTip("两个时域图纵轴范围")
        layout.addWidget(self.view_y_range_edit, 0, 4)

        layout.addWidget(QLabel("PSD Y范围"), 0, 5)
        self.view_psd_y_range_edit = QLineEdit("-160-20")
        self.view_psd_y_range_edit.setPlaceholderText("-160-20")
        self.view_psd_y_range_edit.setToolTip("两个PSD图纵轴范围")
        layout.addWidget(self.view_psd_y_range_edit, 0, 6)

        self.view_apply_axis_btn = QPushButton("应用")
        self.view_auto_axis_btn = QPushButton("自动")
        self._style_secondary_button(self.view_apply_axis_btn, min_width=52)
        self._style_secondary_button(self.view_auto_axis_btn, min_width=52)
        layout.addWidget(self.view_apply_axis_btn, 0, 7)
        layout.addWidget(self.view_auto_axis_btn, 0, 8)
        return group

    def _create_space_time_group(self) -> QGroupBox:
        group = QGroupBox("Space-Time")
        layout = QGridLayout(group)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)
        layout.setColumnStretch(5, 1)

        layout.addWidget(QLabel("通道范围"), 0, 0)
        self.tab3_channel_range_edit = QLineEdit("0-199")
        self.tab3_channel_range_edit.setPlaceholderText("0-199")
        layout.addWidget(self.tab3_channel_range_edit, 0, 1)

        layout.addWidget(QLabel("总时间长度(s)"), 0, 2)
        self.tab3_space_time_total_seconds_spin = QDoubleSpinBox()
        self.tab3_space_time_total_seconds_spin.setRange(0.5, 120.0)
        self.tab3_space_time_total_seconds_spin.setValue(5.0)
        self.tab3_space_time_total_seconds_spin.setDecimals(1)
        self.tab3_space_time_total_seconds_spin.setSingleStep(0.5)
        layout.addWidget(self.tab3_space_time_total_seconds_spin, 0, 3)

        layout.addWidget(QLabel("单次平移(s)"), 0, 4)
        self.tab3_space_time_shift_seconds_spin = QDoubleSpinBox()
        self.tab3_space_time_shift_seconds_spin.setRange(0.1, 60.0)
        self.tab3_space_time_shift_seconds_spin.setValue(1.0)
        self.tab3_space_time_shift_seconds_spin.setDecimals(1)
        self.tab3_space_time_shift_seconds_spin.setSingleStep(0.1)
        layout.addWidget(self.tab3_space_time_shift_seconds_spin, 0, 5)

        layout.addWidget(QLabel("时间降采样"), 1, 0)
        self.tab3_time_downsample_spin = QSpinBox()
        self.tab3_time_downsample_spin.setRange(1, 100)
        self.tab3_time_downsample_spin.setValue(1)
        layout.addWidget(self.tab3_time_downsample_spin, 1, 1)

        layout.addWidget(QLabel("空间降采样"), 1, 2)
        self.tab3_space_downsample_spin = QSpinBox()
        self.tab3_space_downsample_spin.setRange(1, 100)
        self.tab3_space_downsample_spin.setValue(1)
        layout.addWidget(self.tab3_space_downsample_spin, 1, 3)

        layout.addWidget(QLabel("颜色"), 1, 4)
        self.tab3_colormap_combo = QComboBox()
        for text, value in self._tab3_colormap_options:
            self.tab3_colormap_combo.addItem(text, value)
        self.tab3_colormap_combo.setCurrentText("Seismic")
        layout.addWidget(self.tab3_colormap_combo, 1, 5)

        layout.addWidget(QLabel("V范围"), 2, 0)
        self.tab3_v_range_edit = QLineEdit("-0.3-0.3")
        self.tab3_v_range_edit.setPlaceholderText("-0.3-0.3")
        layout.addWidget(self.tab3_v_range_edit, 2, 1, 1, 5)
        return group

    def _create_view_plot_panel(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        view_splitter = QSplitter(Qt.Vertical)
        view_splitter.setChildrenCollapsible(False)
        layout.addWidget(view_splitter)
        self.view_vertical_splitter = view_splitter

        # 用单一 QGridLayout 承载 Curve1/PSD1 与 Curve2/PSD2：
        # 左右时域图与 PSD 图列对齐，两行时域图等高，且两行高度之和与下方 Space-Time 等高。
        upper_widget = QWidget()
        upper_layout = QGridLayout(upper_widget)
        upper_layout.setContentsMargins(0, 0, 0, 0)
        upper_layout.setSpacing(6)
        upper_layout.setColumnStretch(0, 7)
        upper_layout.setColumnStretch(1, 3)
        upper_layout.setRowStretch(0, 1)
        upper_layout.setRowStretch(1, 1)
        upper_widget.setMinimumHeight(300)
        view_splitter.addWidget(upper_widget)

        self.tab3_curve1_plot = pg.PlotWidget()
        self.tab3_curve1_plot.showGrid(x=True, y=True)
        self.tab3_curve1_plot.setLabel("bottom", "Time", units="s")
        self.tab3_curve1_plot.setLabel("left", "Amplitude")
        self.tab3_curve1_plot.addLegend(offset=(8, 8))
        self._configure_interactive_plot(self.tab3_curve1_plot)
        self.tab3_curve1_das_curve = self.tab3_curve1_plot.plot(pen=pg.mkPen("#1f77b4", width=2))
        self.tab3_curve1_fip_curve = self.tab3_curve1_plot.plot(pen=pg.mkPen("#d62728", width=2))
        self._configure_tab3_curve_item(self.tab3_curve1_das_curve)
        self._configure_tab3_curve_item(self.tab3_curve1_fip_curve)
        upper_layout.addWidget(self.tab3_curve1_plot, 0, 0)

        self.view_psd1_plot = pg.PlotWidget()
        self.view_psd1_plot.showGrid(x=True, y=True)
        self.view_psd1_plot.setLabel("bottom", "Frequency", units="Hz")
        self.view_psd1_plot.setLabel("left", "PSD", units="dB")
        self.view_psd1_plot.setLogMode(x=True, y=False)
        self.view_psd1_plot.getAxis("bottom").enableAutoSIPrefix(False)
        self.view_psd1_plot.addLegend(offset=(8, 8))
        self._configure_interactive_plot(self.view_psd1_plot)
        self.view_psd1_curve = self.view_psd1_plot.plot(pen=pg.mkPen("#d62728", width=2), name="PSD1")
        upper_layout.addWidget(self.view_psd1_plot, 0, 1)

        self.tab3_curve2_plot = pg.PlotWidget()
        self.tab3_curve2_plot.showGrid(x=True, y=True)
        self.tab3_curve2_plot.setLabel("bottom", "Time", units="s")
        self.tab3_curve2_plot.setLabel("left", "Amplitude")
        self.tab3_curve2_plot.addLegend(offset=(8, 8))
        self._configure_interactive_plot(self.tab3_curve2_plot)
        self.tab3_curve2_das_curve = self.tab3_curve2_plot.plot(pen=pg.mkPen("#2ca02c", width=2))
        self.tab3_curve2_fip_curve = self.tab3_curve2_plot.plot(pen=pg.mkPen("#ff7f0e", width=2))
        self._configure_tab3_curve_item(self.tab3_curve2_das_curve)
        self._configure_tab3_curve_item(self.tab3_curve2_fip_curve)
        upper_layout.addWidget(self.tab3_curve2_plot, 1, 0)

        self.view_psd2_plot = pg.PlotWidget()
        self.view_psd2_plot.showGrid(x=True, y=True)
        self.view_psd2_plot.setLabel("bottom", "Frequency", units="Hz")
        self.view_psd2_plot.setLabel("left", "PSD", units="dB")
        self.view_psd2_plot.setLogMode(x=True, y=False)
        self.view_psd2_plot.getAxis("bottom").enableAutoSIPrefix(False)
        self.view_psd2_plot.addLegend(offset=(8, 8))
        self._configure_interactive_plot(self.view_psd2_plot)
        self.view_psd2_curve = self.view_psd2_plot.plot(pen=pg.mkPen("#1f77b4", width=2), name="PSD2")
        upper_layout.addWidget(self.view_psd2_plot, 1, 1)

        self.time_plot = self.tab3_curve1_plot
        self.psd_plot = self.view_psd1_plot
        self.view_psd_plot = self.view_psd1_plot

        tab3_space_time_panel = QWidget()
        tab3_space_time_panel.setMinimumHeight(280)
        tab3_space_time_layout = QHBoxLayout(tab3_space_time_panel)
        tab3_space_time_layout.setContentsMargins(0, 0, 0, 0)
        tab3_space_time_layout.setSpacing(6)
        self.tab3_space_time_plot = pg.PlotWidget(title="DAS Space-Time")
        self.tab3_space_time_plot.setLabel("bottom", "Time", units="s")
        self.tab3_space_time_plot.setLabel("left", "Channel")
        self._configure_interactive_plot(self.tab3_space_time_plot)
        self.tab3_space_time_image = pg.ImageItem(axisOrder="row-major")
        self.tab3_space_time_plot.addItem(self.tab3_space_time_image)
        tab3_space_time_layout.addWidget(self.tab3_space_time_plot, 1)
        self.tab3_space_time_histogram = pg.HistogramLUTWidget(orientation="vertical", gradientPosition="right")
        self.tab3_space_time_histogram.setMinimumWidth(84)
        self.tab3_space_time_histogram.setMaximumWidth(120)
        self.tab3_space_time_histogram.setImageItem(self.tab3_space_time_image)
        tab3_space_time_layout.addWidget(self.tab3_space_time_histogram, 0)
        view_splitter.addWidget(tab3_space_time_panel)
        view_splitter.setStretchFactor(0, 1)
        view_splitter.setStretchFactor(1, 1)
        view_splitter.setSizes([1, 1])
        self._apply_tab3_space_time_colormap()
        self._apply_tab3_space_time_levels()
        self._align_view_axis_widths()
        self._apply_psd_axis_tick_limit()
        self._update_view_psd_curves(force=True)
        return widget

    def _align_view_axis_widths(self) -> None:
        """Give paired plots a fixed left-axis width so tick labels align vertically."""
        for plot, width in (
            (getattr(self, "tab3_curve1_plot", None), 88),
            (getattr(self, "tab3_curve2_plot", None), 88),
            (getattr(self, "view_psd1_plot", None), 64),
            (getattr(self, "view_psd2_plot", None), 64),
        ):
            if plot is None:
                continue
            try:
                plot.getAxis("left").setWidth(width)
            except Exception:
                pass

    def _apply_psd_axis_tick_limit(self, max_ticks: int = 6) -> None:
        """Cap the PSD log-frequency axis to a fixed number of tick labels."""
        for plot in (getattr(self, "view_psd1_plot", None), getattr(self, "view_psd2_plot", None)):
            if plot is None:
                continue
            try:
                plot.getViewBox().sigRangeChanged.connect(
                    lambda _vb, _ranges, p=plot, n=max_ticks: self._cap_psd_x_ticks(p, n)
                )
            except Exception:
                pass

    def _cap_psd_x_ticks(self, plot, max_ticks: int = 6) -> None:
        try:
            xmin, xmax = plot.getViewBox().viewRange()[0]
        except Exception:
            return
        if not np.isfinite(xmin) or not np.isfinite(xmax) or xmin >= xmax:
            return
        try:
            log_ticks = np.linspace(xmin, xmax, max_ticks)
            linear_ticks = np.power(10.0, log_ticks)
            ticks = [
                (float(lt), self._format_freq_tick(lin))
                for lt, lin in zip(log_ticks, linear_ticks)
            ]
            plot.getAxis("bottom").setTicks([ticks])
        except Exception:
            pass

    @staticmethod
    def _format_freq_tick(value: float) -> str:
        if value >= 1e6:
            return f"{value / 1e6:.3g}M"
        if value >= 1e3:
            return f"{value / 1e3:.3g}k"
        return f"{value:.3g}"

    def _create_tab2(self):
        # Tab2 is now Data: communication, synchronization, and storage only.
        tab2 = QWidget()
        self.tab_widget.addTab(tab2, "Data")
        main_layout = QHBoxLayout(tab2)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(10)
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(10)
        left_layout.addWidget(self._create_data_comm_control_group())
        left_layout.addWidget(self._create_fip_communication_group())
        left_layout.addWidget(self._create_das_communication_group())
        left_layout.addWidget(self._create_data_comm_status_group())
        left_layout.addWidget(self._create_data_sync_group())
        left_layout.addWidget(self._create_config_group())
        left_layout.addStretch()
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setSpacing(10)
        right_layout.addWidget(self._create_data_storage_group())
        right_layout.addStretch()
        main_layout.addWidget(left_panel, stretch=1)
        main_layout.addWidget(right_panel, stretch=1)
        self._update_data_comm_buttons()
        self._update_data_storage_buttons()

    def _create_fip_communication_group(self) -> QGroupBox:
        group = QGroupBox("FIP通信参数")
        layout = QGridLayout(group)
        layout.setColumnStretch(1, 1)

        layout.addWidget(QLabel("监听地址"), 0, 0)
        self.ip_edit = QLineEdit("0.0.0.0")
        self.ip_edit.setPlaceholderText("0.0.0.0 或本机网卡IP")
        self.ip_edit.setToolTip("本软件作为服务端时绑定本机地址；推荐 0.0.0.0 监听所有网卡。客户端应连接本机实际IP。")
        layout.addWidget(self.ip_edit, 0, 1)

        layout.addWidget(QLabel("端口"), 0, 2)
        self.port_spin = QSpinBox()
        self.port_spin.setRange(1024, 65535)
        self.port_spin.setValue(3677)
        layout.addWidget(self.port_spin, 0, 3)

        layout.addWidget(QLabel("单包时长(s)"), 0, 4)
        self.fip_packet_duration_spin = QDoubleSpinBox()
        self.fip_packet_duration_spin.setRange(0.001, 60.0)
        self.fip_packet_duration_spin.setDecimals(3)
        self.fip_packet_duration_spin.setSingleStep(0.1)
        self.fip_packet_duration_spin.setValue(1.0)
        layout.addWidget(self.fip_packet_duration_spin, 0, 5)

        layout.addWidget(QLabel("采样率(MHz)"), 0, 6)
        self.fip_sample_rate_mhz_spin = QDoubleSpinBox()
        self.fip_sample_rate_mhz_spin.setRange(0.001, 100.0)
        self.fip_sample_rate_mhz_spin.setDecimals(3)
        self.fip_sample_rate_mhz_spin.setSingleStep(0.1)
        self.fip_sample_rate_mhz_spin.setValue(1.0)
        layout.addWidget(self.fip_sample_rate_mhz_spin, 0, 7)

        layout.addWidget(QLabel("FIP数量"), 0, 8)
        self.fip_sensor_count_combo = QComboBox()
        self.fip_sensor_count_combo.addItem("1个", 1)
        self.fip_sensor_count_combo.addItem("2个", 2)
        self.fip_sensor_count_combo.setCurrentIndex(1)
        layout.addWidget(self.fip_sensor_count_combo, 0, 9)

        layout.addWidget(QLabel("连接状态"), 1, 0)
        self.conn_status_label = QLabel("未连接")
        self.conn_status_label.setStyleSheet("color: #9aa0a6; font-weight: bold;")
        layout.addWidget(self.conn_status_label, 1, 1)
        layout.addWidget(QLabel("接收包"), 1, 2)
        self.packet_count_label = QLabel("0")
        layout.addWidget(self.packet_count_label, 1, 3)
        layout.addWidget(QLabel("丢包率"), 1, 4)
        self.loss_rate_label = QLabel("0.00%")
        layout.addWidget(self.loss_rate_label, 1, 5)
        hint_label = QLabel("服务端监听本机地址；不确定网卡时使用 0.0.0.0")
        hint_label.setStyleSheet("color: #5f6b7a; font-size: 11px;")
        layout.addWidget(hint_label, 1, 6, 1, 4)
        return group

    def _create_das_communication_group(self) -> QGroupBox:
        group = QGroupBox("eDAS通信参数")
        layout = QGridLayout(group)
        layout.setColumnStretch(1, 1)

        layout.addWidget(QLabel("监听地址"), 0, 0)
        self.tab3_ip_edit = QLineEdit("0.0.0.0")
        self.tab3_ip_edit.setPlaceholderText("0.0.0.0 或本机网卡IP")
        self.tab3_ip_edit.setToolTip("本软件作为服务端时绑定本机地址；推荐 0.0.0.0 监听所有网卡。eDAS客户端应连接本机实际IP。")
        layout.addWidget(self.tab3_ip_edit, 0, 1)
        layout.addWidget(QLabel("端口"), 0, 2)
        self.tab3_port_spin = QSpinBox()
        self.tab3_port_spin.setRange(1024, 65535)
        self.tab3_port_spin.setValue(3678)
        layout.addWidget(self.tab3_port_spin, 0, 3)

        layout.addWidget(QLabel("连接状态"), 1, 0)
        self.tab3_conn_status_label = QLabel("Disconnected")
        self.tab3_conn_status_label.setStyleSheet("color: #9aa0a6; font-weight: bold;")
        layout.addWidget(self.tab3_conn_status_label, 1, 1)
        layout.addWidget(QLabel("接收包"), 1, 2)
        self.tab3_packet_count_label = QLabel("0")
        layout.addWidget(self.tab3_packet_count_label, 1, 3)
        layout.addWidget(QLabel("缺包"), 1, 4)
        self.tab3_missing_packet_label = QLabel("0")
        layout.addWidget(self.tab3_missing_packet_label, 1, 5)

        layout.addWidget(QLabel("通道"), 2, 0)
        self.tab3_channel_count_label = QLabel("-")
        layout.addWidget(self.tab3_channel_count_label, 2, 1)
        layout.addWidget(QLabel("采样率"), 2, 2)
        self.tab3_sample_rate_label = QLabel("-")
        layout.addWidget(self.tab3_sample_rate_label, 2, 3)
        layout.addWidget(QLabel("字节"), 2, 4)
        self.tab3_data_bytes_label = QLabel("-")
        layout.addWidget(self.tab3_data_bytes_label, 2, 5)
        layout.addWidget(QLabel("包长"), 3, 0)
        self.tab3_packet_duration_label = QLabel("-")
        layout.addWidget(self.tab3_packet_duration_label, 3, 1)
        self.tab3_last_comm_label = QLabel("-")
        layout.addWidget(QLabel("最近Comm"), 3, 2)
        layout.addWidget(self.tab3_last_comm_label, 3, 3)

        hint_label = QLabel("服务端监听本机地址；不确定网卡时使用 0.0.0.0")
        hint_label.setStyleSheet("color: #5f6b7a; font-size: 11px;")
        layout.addWidget(hint_label, 4, 0, 1, 6)
        return group

    def _create_data_comm_control_group(self) -> QGroupBox:
        group = QGroupBox("通信控制")
        layout = QGridLayout(group)
        for column in range(3):
            layout.setColumnStretch(column, 1)
        self.data_comm_both_btn = QPushButton("同时启动")
        self.data_comm_both_btn.setCheckable(True)
        self.data_comm_both_btn.setMinimumHeight(44)
        layout.addWidget(self.data_comm_both_btn, 0, 0)
        self.start_stop_btn = QPushButton("启动FIP")
        self.start_stop_btn.setCheckable(True)
        self.start_stop_btn.setMinimumHeight(44)
        layout.addWidget(self.start_stop_btn, 0, 1)
        self.tab3_start_stop_btn = QPushButton("启动eDAS")
        self.tab3_start_stop_btn.setCheckable(True)
        self.tab3_start_stop_btn.setMinimumHeight(44)
        layout.addWidget(self.tab3_start_stop_btn, 0, 2)
        return group

    def _create_data_comm_status_group(self) -> QGroupBox:
        group = QGroupBox("通信统计")
        layout = QGridLayout(group)
        for column in range(6):
            layout.setColumnStretch(column, 1 if column else 0)
        headers = ["模块", "状态", "接收包", "缺包/失败", "丢包率", "最近Comm"]
        for column, header in enumerate(headers):
            label = QLabel(header)
            label.setStyleSheet("font-weight: bold;")
            layout.addWidget(label, 0, column)
        layout.addWidget(QLabel("FIP"), 1, 0)
        self.data_fip_status_label = QLabel("未连接")
        self.data_fip_status_label.setStyleSheet("color: #9aa0a6; font-weight: bold;")
        self.data_fip_success_label = QLabel("0")
        self.data_fip_failure_label = QLabel("0")
        self.data_fip_loss_rate_label = QLabel("0.00%")
        self.data_fip_last_comm_label = QLabel("-")
        layout.addWidget(self.data_fip_status_label, 1, 1)
        layout.addWidget(self.data_fip_success_label, 1, 2)
        layout.addWidget(self.data_fip_failure_label, 1, 3)
        layout.addWidget(self.data_fip_loss_rate_label, 1, 4)
        layout.addWidget(self.data_fip_last_comm_label, 1, 5)
        layout.addWidget(QLabel("eDAS"), 2, 0)
        self.data_edas_status_label = QLabel("未连接")
        self.data_edas_status_label.setStyleSheet("color: #9aa0a6; font-weight: bold;")
        self.data_edas_success_label = QLabel("0")
        self.data_edas_failure_label = QLabel("0")
        self.data_edas_loss_rate_label = QLabel("0.00%")
        layout.addWidget(self.data_edas_status_label, 2, 1)
        layout.addWidget(self.data_edas_success_label, 2, 2)
        layout.addWidget(self.data_edas_failure_label, 2, 3)
        layout.addWidget(self.data_edas_loss_rate_label, 2, 4)
        layout.addWidget(self.tab3_last_comm_label, 2, 5)
        return group

    def _create_data_sync_group(self) -> QGroupBox:
        group = QGroupBox("FIP/eDAS时间同步检验")
        layout = QGridLayout(group)
        layout.setColumnStretch(1, 1)
        layout.setColumnStretch(3, 1)
        layout.setColumnStretch(5, 1)

        def add_pair(label_text, attr, row, col):
            layout.addWidget(QLabel(label_text), row, col)
            value_label = QLabel("-")
            value_label.setStyleSheet("font-weight: bold;")
            setattr(self, attr, value_label)
            layout.addWidget(value_label, row, col + 1)
            return value_label

        add_pair("首包时间差（FIP-eDAS）(s)", "data_sync_first_delta_label", 0, 0)
        add_pair("最新同序号时间差（FIP-eDAS）(s)", "data_sync_latest_delta_label", 0, 2)
        add_pair("平均时间差（FIP-eDAS）(s)", "data_sync_avg_delta_label", 0, 4)
        add_pair("匹配包数", "data_sync_match_count_label", 1, 0)
        # Compatibility aliases used by the synchronization refresh helpers.
        self.data_sync_average_delta_label = self.data_sync_avg_delta_label
        self.data_sync_pair_count_label = self.data_sync_match_count_label

        self.tab3_align_fip_comm_label = add_pair("FIP Comm", "tab3_align_fip_comm_label", 1, 2)
        self.tab3_align_das_comm_label = add_pair("eDAS Comm", "tab3_align_das_comm_label", 1, 4)
        self.tab3_alignment_status_label = add_pair("对齐状态", "tab3_alignment_status_label", 2, 0)
        self.tab3_fip_missing_label = add_pair("FIP缺包", "tab3_fip_missing_label", 2, 2)
        self.tab3_das_missing_label = add_pair("eDAS缺包", "tab3_das_missing_label", 2, 4)
        self.tab3_alignment_status_label.setText("waiting")
        self.tab3_fip_missing_label.setText("0")
        self.tab3_das_missing_label.setText("0")

        layout.addWidget(QLabel("缺口范围"), 3, 0)
        self.tab3_missing_ranges_label = QLabel("-")
        self.tab3_missing_ranges_label.setWordWrap(False)
        layout.addWidget(self.tab3_missing_ranges_label, 3, 1, 1, 5)
        return group

    def _create_data_storage_group(self) -> QGroupBox:
        group = QGroupBox("存储控制与日志")
        layout = QGridLayout(group)
        for column in range(6):
            layout.setColumnStretch(column, 1)
        self.tab3_joint_storage_toggle_btn = QPushButton("同时存储: OFF")
        self.tab3_joint_storage_toggle_btn.setCheckable(True)
        self.tab3_joint_storage_toggle_btn.setMinimumHeight(44)
        layout.addWidget(self.tab3_joint_storage_toggle_btn, 0, 0, 1, 2)
        self.phase_storage_check = QPushButton("FIP存储: OFF")
        self.phase_storage_check.setCheckable(True)
        self.phase_storage_check.setMinimumHeight(44)
        layout.addWidget(self.phase_storage_check, 0, 2, 1, 2)
        self.tab3_edas_storage_toggle_btn = QPushButton("eDAS存储: OFF")
        self.tab3_edas_storage_toggle_btn.setCheckable(True)
        self.tab3_edas_storage_toggle_btn.setMinimumHeight(44)
        layout.addWidget(self.tab3_edas_storage_toggle_btn, 0, 4, 1, 2)
        self.tab3_storage_toggle_btn = self.tab3_joint_storage_toggle_btn

        layout.addWidget(QLabel("联合路径"), 1, 0)
        self.tab3_storage_path_edit = QLineEdit("D:/PCCP/FIPeDASDATA")
        layout.addWidget(self.tab3_storage_path_edit, 1, 1, 1, 5)
        layout.addWidget(QLabel("FIP路径"), 2, 0)
        self.storage_path_edit = QLineEdit("D:/PCCP/FIPdata")
        layout.addWidget(self.storage_path_edit, 2, 1, 1, 5)
        layout.addWidget(QLabel("eDAS路径"), 3, 0)
        self.tab3_edas_storage_path_edit = QLineEdit("D:/PCCP/eDASDATA")
        layout.addWidget(self.tab3_edas_storage_path_edit, 3, 1, 1, 5)
        layout.addWidget(QLabel("FIP间隔(s)"), 4, 0)
        self.storage_interval_spin = QSpinBox()
        self.storage_interval_spin.setRange(10, 300)
        self.storage_interval_spin.setValue(10)
        layout.addWidget(self.storage_interval_spin, 4, 1)
        layout.addWidget(QLabel("联合间隔(s)"), 4, 2)
        self.tab3_storage_interval_spin = QDoubleSpinBox()
        self.tab3_storage_interval_spin.setRange(1.0, 60.0)
        self.tab3_storage_interval_spin.setValue(10.0)
        self.tab3_storage_interval_spin.setDecimals(1)
        layout.addWidget(self.tab3_storage_interval_spin, 4, 3)
        layout.addWidget(QLabel("缓存(s)"), 4, 4)
        self.tab3_cache_seconds_spin = QDoubleSpinBox()
        self.tab3_cache_seconds_spin.setRange(5.0, 120.0)
        self.tab3_cache_seconds_spin.setValue(10.0)
        self.tab3_cache_seconds_spin.setDecimals(1)
        layout.addWidget(self.tab3_cache_seconds_spin, 4, 5)
        layout.addWidget(QLabel("eDAS块/文件"), 5, 0)
        self.tab3_edas_blocks_per_file_spin = QSpinBox()
        self.tab3_edas_blocks_per_file_spin.setRange(1, 100000)
        self.tab3_edas_blocks_per_file_spin.setValue(50)
        layout.addWidget(self.tab3_edas_blocks_per_file_spin, 5, 1)
        layout.addWidget(QLabel("eDAS队列"), 5, 2)
        self.tab3_edas_queue_packets_spin = QSpinBox()
        self.tab3_edas_queue_packets_spin.setRange(1, 4096)
        self.tab3_edas_queue_packets_spin.setValue(200)
        layout.addWidget(self.tab3_edas_queue_packets_spin, 5, 3)
        layout.addWidget(QLabel("FIP降采样"), 5, 4)
        self.storage_downsample_spin = QSpinBox()
        self.storage_downsample_spin.setRange(1, 100)
        self.storage_downsample_spin.setValue(1)
        layout.addWidget(self.storage_downsample_spin, 5, 5)
        layout.addWidget(QLabel("FIP成功/失败"), 6, 0)
        self.data_fip_storage_count_label = QLabel("0 / 0")
        layout.addWidget(self.data_fip_storage_count_label, 6, 1, 1, 2)
        layout.addWidget(QLabel("eDAS成功/失败"), 6, 3)
        self.data_edas_storage_count_label = QLabel("0 / 0")
        layout.addWidget(self.data_edas_storage_count_label, 6, 4, 1, 2)
        layout.addWidget(QLabel("联合Last"), 7, 0)
        self.tab3_last_storage_label = QLabel("-")
        self.tab3_last_storage_label.setWordWrap(False)
        layout.addWidget(self.tab3_last_storage_label, 7, 1, 1, 5)
        layout.addWidget(QLabel("eDAS Last"), 8, 0)
        self.tab3_edas_last_storage_label = QLabel("-")
        self.tab3_edas_last_storage_label.setWordWrap(False)
        layout.addWidget(self.tab3_edas_last_storage_label, 8, 1, 1, 5)
        return group

    def _create_config_group(self) -> QGroupBox:
        group = QGroupBox("配置")
        layout = QHBoxLayout(group)
        self.save_config_btn = QPushButton("保存配置")
        self.load_config_btn = QPushButton("加载配置")
        self.reset_config_btn = QPushButton("重置配置")
        for button in (self.save_config_btn, self.load_config_btn, self.reset_config_btn):
            self._style_secondary_button(button, min_width=88)
        layout.addWidget(self.save_config_btn)
        layout.addWidget(self.load_config_btn)
        layout.addWidget(self.reset_config_btn)
        return group

    def _create_status_light(self) -> QFrame:
        light = QFrame()
        light.setFixedSize(18, 18)
        self._set_status_light(light, "gray")
        return light

    def _set_status_light(self, light: QFrame, color_name: str) -> None:
        colors = {"gray": "#9aa0a6", "green": "#2e8b57", "red": "#d9534f"}
        color = colors.get(color_name, colors["gray"])
        light.setStyleSheet(f"background-color: {color}; border-radius: 9px; border: 1px solid #666;")

    def _create_status_bar_indicator(self, title: str, light_attr: str, label_attr: str) -> QWidget:
        """Build one compact left-aligned module indicator for the status bar."""
        widget = QWidget()
        layout = QHBoxLayout(widget)
        layout.setContentsMargins(6, 0, 6, 0)
        layout.setSpacing(5)
        light = self._create_status_light()
        label = QLabel(f"{title}: 成功 0")
        label.setStyleSheet("color: #394150; font-size: 8pt;")
        setattr(self, light_attr, light)
        setattr(self, label_attr, label)
        layout.addWidget(light)
        layout.addWidget(label)
        return widget

    def _create_tab3(self):
        # Old Tab2 signal detection is now the third tab and remains off by default.
        tab3 = QWidget()
        self.tab_widget.addTab(tab3, "Tab3")
        main_layout = QHBoxLayout(tab3)
        left_panel = self._create_detection_params_panel()
        main_layout.addWidget(left_panel, stretch=1)
        right_panel = self._create_feature_display_panel()
        main_layout.addWidget(right_panel, stretch=3)

    def _create_detection_params_panel(self) -> QWidget:
        """Create the Tab2 parameter panel."""
        widget = QWidget()
        widget.setMaximumWidth(420)
        layout = QVBoxLayout(widget)

        self.tab2_enable_btn = QPushButton("Start Tab2")
        self.tab2_enable_btn.setCheckable(True)
        self.tab2_enable_btn.setChecked(False)
        self.tab2_enable_btn.setMinimumHeight(42)
        self._update_tab2_enable_button_state(False)
        layout.addWidget(self.tab2_enable_btn)

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
        self._style_secondary_button(self.clear_alarms_btn, min_width=160)
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

    def _create_tab4(self):
        # Setting centralizes global GUI and plot font controls.
        tab4 = QWidget()
        self.tab_widget.addTab(tab4, "Setting")
        layout = QVBoxLayout(tab4)
        layout.setContentsMargins(24, 24, 24, 24)
        layout.setSpacing(12)
        group = QGroupBox("全局显示设置")
        grid = QGridLayout(group)
        grid.setColumnStretch(1, 1)
        grid.addWidget(QLabel("GUI字体(pt)"), 0, 0)
        self.setting_gui_font_spin = QSpinBox()
        self.setting_gui_font_spin.setRange(8, 28)
        self.setting_gui_font_spin.setValue(8)
        grid.addWidget(self.setting_gui_font_spin, 0, 1)
        grid.addWidget(QLabel("图标题字体(px)"), 1, 0)
        self.setting_plot_title_font_spin = QSpinBox()
        self.setting_plot_title_font_spin.setRange(10, 32)
        self.setting_plot_title_font_spin.setValue(18)
        grid.addWidget(self.setting_plot_title_font_spin, 1, 1)
        grid.addWidget(QLabel("坐标标签字体(px)"), 2, 0)
        self.setting_axis_label_font_spin = QSpinBox()
        self.setting_axis_label_font_spin.setRange(8, 28)
        self.setting_axis_label_font_spin.setValue(12)
        grid.addWidget(self.setting_axis_label_font_spin, 2, 1)
        grid.addWidget(QLabel("刻度字体(pt)"), 3, 0)
        self.setting_tick_font_spin = QSpinBox()
        self.setting_tick_font_spin.setRange(7, 24)
        self.setting_tick_font_spin.setValue(11)
        grid.addWidget(self.setting_tick_font_spin, 3, 1)
        grid.addWidget(QLabel("PSD降采样"), 4, 0)
        self.setting_psd_downsample_spin = QSpinBox()
        self.setting_psd_downsample_spin.setRange(1, 100)
        self.setting_psd_downsample_spin.setValue(1)
        grid.addWidget(self.setting_psd_downsample_spin, 4, 1)
        self.setting_apply_btn = QPushButton("应用并保存全局设置")
        self.setting_apply_btn.setMinimumHeight(42)
        self._style_action_button(self.setting_apply_btn, False, min_height=42, min_width=190, font_size=15)
        grid.addWidget(self.setting_apply_btn, 5, 0, 1, 2)
        layout.addWidget(group)
        layout.addStretch()

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
        open_config.triggered.connect(self._load_configuration)
        save_config.triggered.connect(self._save_configuration)
        exit_action.triggered.connect(self.close)

        # 可视化参数变化
    def _toggle_monitoring(self):
        """切换监测状态"""
        if self.monitoring_active:
            self._stop_monitoring()
        else:
            self._start_monitoring()


    def _start_monitoring(self):
        # Start FIP communication from the Data tab and immediately reflect the requested state.
        if self.monitoring_active:
            self._update_data_comm_buttons()
            return
        self.monitoring_active = True
        self._update_data_comm_buttons()
        self.start_monitoring.emit()
        self.status_bar.showMessage("FIP通信启动中...", 3000)


    def _stop_monitoring(self):
        # Stop FIP communication while preserving eDAS state when it is running independently.
        if not self.monitoring_active:
            self._update_data_comm_buttons()
            return
        self.monitoring_active = False
        if not self._edas_monitoring_active:
            self._both_comm_requested = False
        self._update_data_comm_buttons()
        self.stop_monitoring.emit()
        self.status_bar.showMessage("FIP通信已停止", 3000)

    def _save_configuration(self):
        """保存配置"""
        config = self.get_current_config()
        self._write_persisted_configuration(config, show_status=True)

    def _load_configuration(self):
        """加载最近一次自动保存的本地 GUI 参数。"""
        if self._load_persisted_configuration(show_status=True):
            self._schedule_auto_save()

    def _reset_configuration(self):
        """重置配置为默认值，并清除本地自动保存快照。"""
        self._apply_gui_config(getattr(self, "_default_gui_config", {}))
        try:
            if self._persist_config_path.exists():
                self._persist_config_path.unlink()
        except OSError as exc:
            self._persist_logger.warning("Failed to remove persisted GUI state: %s", exc)
        self._write_persisted_configuration(self.get_current_config(), show_status=True)

    def get_tab1_fip_settings(self) -> Dict[str, Any]:
        """Return Tab1 FIP input and selected plotting settings."""
        sensor_count = self._combo_current_data_int(
            getattr(self, 'fip_sensor_count_combo', None),
            1,
        )
        sensor_count = min(max(sensor_count, 1), 2)
        target_combo = getattr(self, 'fip_plot_sensor_combo', None)
        target_data = target_combo.currentData() if target_combo is not None else 1
        plot_target = "both" if str(target_data) == "both" else str(target_data)
        if plot_target == "both" and sensor_count == 2:
            selected_sensor = 1
            selected_sensors = [1, 2]
        else:
            selected_sensor = self._combo_current_data_int(target_combo, 1)
            selected_sensor = min(max(selected_sensor, 1), sensor_count)
            selected_sensors = [selected_sensor]
        packet_duration_seconds = self._spin_float_value(
            getattr(self, 'fip_packet_duration_spin', None),
            1.0,
        )
        sample_rate_mhz = self._spin_float_value(
            getattr(self, 'fip_sample_rate_mhz_spin', None),
            1.0,
        )
        return {
            "sensor_count": sensor_count,
            "selected_sensor": selected_sensor,
            "selected_sensors": selected_sensors,
            "plot_target": plot_target if plot_target == "both" else f"FIP{selected_sensor}",
            "packet_duration_seconds": max(packet_duration_seconds, 0.001),
            "sample_rate_hz": max(sample_rate_mhz, 0.001) * 1_000_000.0,
            "phase_unwrap_enabled": bool(
                getattr(self, 'fip_phase_unwrap_check', None)
                and self.fip_phase_unwrap_check.isChecked()
            ),
        }

    def _combo_current_data_int(self, combo, default: int) -> int:
        if combo is None:
            return default
        data = combo.currentData()
        if data is not None:
            try:
                return int(data)
            except (TypeError, ValueError):
                pass
        text = combo.currentText()
        digits = "".join(ch for ch in text if ch.isdigit())
        return int(digits) if digits else default

    def _spin_float_value(self, spin, default: float) -> float:
        if spin is None:
            return default
        try:
            return float(spin.value())
        except (TypeError, ValueError):
            return default

    def _parse_range_text(
        self,
        edit,
        default_min: float,
        default_max: float,
        *,
        integer: bool = False,
    ) -> Tuple[float, float]:
        text = str(edit.text()).strip() if edit is not None else ""
        pattern = (
            r"^\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
            r"\s*-\s*([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s*$"
        )
        match = re.match(pattern, text)
        if not match:
            return (default_min, default_max)
        try:
            low = float(match.group(1))
            high = float(match.group(2))
        except (TypeError, ValueError):
            return (default_min, default_max)
        if integer:
            return (int(round(low)), int(round(high)))
        return (low, high)

    def _set_range_text(self, edit, low: Any, high: Any) -> None:
        if edit is None or low is None or high is None:
            return
        edit.blockSignals(True)
        try:
            edit.setText(f"{low:g}-{high:g}")
        except (TypeError, ValueError):
            edit.setText(f"{low}-{high}")
        finally:
            edit.blockSignals(False)

    def get_fip_filter_range(self) -> Tuple[float, float]:
        return self._parse_range_text(getattr(self, "fip_filter_range_edit", None), 500.0, 6000.0)

    def _parse_filter_spec_text(
        self,
        edit,
        default_low: float,
        default_high: float,
    ) -> Dict[str, Any]:
        text = str(edit.text()).strip() if edit is not None else ""
        number = r"(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"
        bandpass_match = re.match(rf"^\s*({number})\s*-\s*({number})\s*$", text)
        highpass_match = re.match(rf"^\s*({number})\s*-\s*$", text)
        lowpass_match = re.match(rf"^\s*-\s*({number})\s*$", text)
        try:
            if bandpass_match:
                low = float(bandpass_match.group(1))
                high = float(bandpass_match.group(2))
                if high <= low:
                    low, high = default_low, default_high
                return {
                    "type": "bandpass",
                    "low_freq": low,
                    "high_freq": high,
                    "cutoff_freq": (low, high),
                    "range": text,
                }
            if highpass_match:
                cutoff = max(0.0, float(highpass_match.group(1)))
                return {
                    "type": "highpass",
                    "low_freq": cutoff,
                    "high_freq": default_high,
                    "cutoff_freq": cutoff,
                    "range": text,
                }
            if lowpass_match:
                cutoff = max(0.0, float(lowpass_match.group(1)))
                return {
                    "type": "lowpass",
                    "low_freq": default_low,
                    "high_freq": cutoff,
                    "cutoff_freq": cutoff,
                    "range": text,
                }
        except (TypeError, ValueError):
            pass
        return {
            "type": "bandpass",
            "low_freq": default_low,
            "high_freq": default_high,
            "cutoff_freq": (default_low, default_high),
            "range": text or f"{default_low:g}-{default_high:g}",
        }

    def get_fip_filter_settings(self) -> Dict[str, Any]:
        spec = self._parse_filter_spec_text(getattr(self, "fip_filter_range_edit", None), 500.0, 6000.0)
        enabled = bool(getattr(self, "fip_filter_enable_check", None) and self.fip_filter_enable_check.isChecked())
        spec.update({
            "enabled": enabled,
            "type": spec["type"] if enabled else "none",
            "order": self.filter_order_spin.value() if hasattr(self, "filter_order_spin") else 4,
        })
        return spec

    def get_view_x_range(self) -> Tuple[float, float]:
        return self._parse_range_text(getattr(self, "view_x_range_edit", None), 0.0, 1.0)

    def get_view_y_range(self) -> Tuple[float, float]:
        return self._parse_range_text(getattr(self, "view_y_range_edit", None), -1.0, 1.0)

    def get_view_psd_y_range(self) -> Tuple[float, float]:
        return self._parse_range_text(getattr(self, "view_psd_y_range_edit", None), -160.0, 20.0)

    def get_tab3_channel_range(self) -> Tuple[int, int]:
        low, high = self._parse_range_text(getattr(self, "tab3_channel_range_edit", None), 0, 199, integer=True)
        return int(low), int(high)

    def get_tab3_das_filter_range(self) -> Tuple[float, float]:
        spec = self.get_tab3_das_filter_settings()
        return float(spec.get("low_freq", 500.0)), float(spec.get("high_freq", 6000.0))

    def get_tab3_das_filter_settings(self) -> Dict[str, Any]:
        spec = self._parse_filter_spec_text(getattr(self, "tab3_das_filter_range_edit", None), 500.0, 6000.0)
        enabled = bool(
            getattr(self, "tab3_das_filter_enable_check", None)
            and self.tab3_das_filter_enable_check.isChecked()
        )
        spec.update({
            "enabled": enabled,
            "type": spec["type"] if enabled else "none",
            "order": self.tab3_das_filter_order_spin.value()
            if hasattr(self, "tab3_das_filter_order_spin")
            else 4,
        })
        return spec

    def get_tab3_v_range(self) -> Tuple[float, float]:
        return self._parse_range_text(getattr(self, "tab3_v_range_edit", None), -0.3, 0.3)

    def get_psd_downsample_factor(self) -> int:
        spin = getattr(self, "setting_psd_downsample_spin", None)
        return max(1, int(spin.value())) if spin is not None else 1

    def _on_fip_sensor_count_changed(self):
        """Refresh dependent controls after switching between one/two FIP sensors."""
        self._update_fip_sensor_controls(emit=True)

    def _on_fip_plot_sensor_changed(self):
        """Notify the controller that Tab1 should plot another FIP sensor."""
        if hasattr(self, 'fip_sensor_settings_changed'):
            self.fip_sensor_settings_changed.emit(self.get_tab1_fip_settings())

    def _on_fip_input_settings_changed(self):
        """Notify dependent pipelines after FIP packet duration or sample rate changes."""
        self._update_psd_settings()
        if hasattr(self, 'fip_sensor_settings_changed'):
            self.fip_sensor_settings_changed.emit(self.get_tab1_fip_settings())
        if hasattr(self, 'tab3_settings_changed'):
            self.tab3_settings_changed.emit()

    def _on_fip_phase_unwrap_changed(self, _checked: bool):
        """Notify dependent pipelines after toggling FIP phase unwrapping."""
        if hasattr(self, 'fip_sensor_settings_changed'):
            self.fip_sensor_settings_changed.emit(self.get_tab1_fip_settings())
        self._update_view_psd_curves(force=True)

    def _update_fip_sensor_controls(self, emit: bool = True):
        settings = self.get_tab1_fip_settings()
        sensor_count = settings["sensor_count"]
        selected_sensor = settings["selected_sensor"]

        if hasattr(self, 'fip_plot_sensor_combo'):
            combo = self.fip_plot_sensor_combo
            previous_data = combo.currentData()
            previous_was_initial = combo.count() <= 1
            combo.blockSignals(True)
            combo.clear()
            combo.addItem("FIP1", 1)
            if sensor_count == 2:
                combo.addItem("FIP2", 2)
                combo.addItem("1和2", "both")
            target_index = combo.findData(previous_data)
            if sensor_count == 2 and (previous_was_initial or previous_data is None or target_index < 0):
                target_index = combo.findData("both")
            if target_index < 0:
                target_index = max(0, selected_sensor - 1)
            combo.setCurrentIndex(target_index)
            combo.setEnabled(sensor_count == 2)
            combo.blockSignals(False)

        self._update_tab3_fip_curve_options(sensor_count)

        if emit and hasattr(self, 'fip_sensor_settings_changed'):
            self.fip_sensor_settings_changed.emit(self.get_tab1_fip_settings())
            if hasattr(self, 'tab3_settings_changed'):
                self.tab3_settings_changed.emit()

    def _update_tab3_fip_curve_options(self, sensor_count: int):
        if not hasattr(self, 'tab3_curve1_combo') or not hasattr(self, 'tab3_curve2_combo'):
            return
        options = ["Off", "DAS Channel", "FIP1"]
        if sensor_count == 2:
            options.append("FIP2")
        for combo, default_value in ((self.tab3_curve1_combo, "FIP1"), (self.tab3_curve2_combo, "DAS Channel")):
            current = combo.currentText()
            if current == "FIP":
                current = "FIP1"
            elif sensor_count == 1 and current == "FIP2":
                current = "FIP1"
            if current not in options:
                current = default_value

            combo.blockSignals(True)
            combo.clear()
            combo.addItems(options)
            combo.setCurrentText(current)
            combo.blockSignals(False)

    def get_current_config(self) -> Dict[str, Any]:
        """获取当前配置 - Tab1简化版本"""
        fip_filter_settings = self.get_fip_filter_settings()
        config = {
            "communication": {
                "ip": self.ip_edit.text(),
                "port": self.port_spin.value(),
                "fip": self.get_tab1_fip_settings(),
            },
            "preprocessing": {
                "filter": {
                    "enabled": fip_filter_settings["enabled"],
                    "type": fip_filter_settings["type"],
                    "low_freq": fip_filter_settings["low_freq"],
                    "high_freq": fip_filter_settings["high_freq"],
                    "cutoff_freq": fip_filter_settings["cutoff_freq"],
                    "range": fip_filter_settings["range"],
                    "order": fip_filter_settings["order"]
                },
                "downsample": {
                    "factor": self.downsample_spin.value()
                }
            },
            "storage": {
                "realtime": {
                    "enabled": self.phase_storage_check.isChecked(),
                    "interval": self.storage_interval_spin.value(),
                    "downsample_factor": self.storage_downsample_spin.value(),
                },
                "path": self.storage_path_edit.text()
            },
            "view": self.get_view_settings(),
            "setting": self.get_global_display_settings(),
        }

        # 如果Tab2控件存在，添加特征和检测配置
        if hasattr(self, 'detection_feature_checkboxes'):
            config["tab2"] = {
                "enabled": self.is_tab2_enabled(),
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

    def _current_param_panel_width(self) -> int:
        """Return the current View parameter-panel width in pixels."""
        try:
            if hasattr(self, "view_param_splitter"):
                sizes = self.view_param_splitter.sizes()
                if sizes:
                    return int(sizes[0])
        except Exception:
            pass
        return self.VIEW_PARAM_PANEL_DEFAULT_WIDTH

    def _apply_param_panel_width(self, width: Any) -> None:
        """Apply a persisted View parameter-panel width to the horizontal splitter."""
        try:
            if not hasattr(self, "view_param_splitter"):
                return
            total = sum(self.view_param_splitter.sizes())
            if total <= 0:
                total = int(width) + 1100
            clamped = max(self.VIEW_PARAM_PANEL_MIN_WIDTH, min(int(width), self.VIEW_PARAM_PANEL_MAX_WIDTH))
            self.view_param_splitter.setSizes([clamped, max(total - clamped, 200)])
        except (TypeError, ValueError):
            return

    def get_view_settings(self) -> Dict[str, Any]:
        """Return View-tab display, PSD, and axis settings for persistence."""
        x_min, x_max = self.get_view_x_range()
        y_min, y_max = self.get_view_y_range()
        psd_y_min, psd_y_max = self.get_view_psd_y_range()
        return {
            "param_panel_width": self._current_param_panel_width(),
            "time_plot_enabled": self.time_plot_btn.isChecked(),
            "psd_plot_enabled": self.psd_plot_btn.isChecked(),
            "view_update_enabled": self.tab3_plot_toggle_btn.isChecked(),
            "time_display_seconds": self.time_display_duration_spin.value(),
            "fip_refresh_seconds": self.view_fip_refresh_spin.value(),
            "edas_refresh_seconds": self.view_edas_refresh_spin.value(),
            "curve_max_points": self.view_curve_max_points_spin.value(),
            "psd": {
                "psd1_enabled": self.view_psd1_check.isChecked(),
                "psd2_enabled": self.view_psd2_check.isChecked(),
                "window_seconds": self.psd_window_length_spin.value(),
                "overlap_percent": self.psd_overlap_spin.value(),
            },
            "axis": {
                "manual_enabled": self.view_axis_enable_check.isChecked(),
                "x_range": self.view_x_range_edit.text(),
                "x_min": x_min,
                "x_max": x_max,
                "y_range": self.view_y_range_edit.text(),
                "y_min": y_min,
                "y_max": y_max,
                "psd_y_range": self.view_psd_y_range_edit.text(),
                "psd_y_min": psd_y_min,
                "psd_y_max": psd_y_max,
            },
        }

    def get_global_display_settings(self) -> Dict[str, Any]:
        """Return Setting-tab global font controls for persistence."""
        return {
            "gui_font_pt": self.setting_gui_font_spin.value(),
            "plot_title_px": self.setting_plot_title_font_spin.value(),
            "axis_label_px": self.setting_axis_label_font_spin.value(),
            "tick_font_pt": self.setting_tick_font_spin.value(),
            "psd_downsample_factor": self.get_psd_downsample_factor(),
        }

    def _persist_payload(self, config: Dict[str, Any]) -> Dict[str, Any]:
        """Wrap GUI parameters with metadata before writing to disk."""
        return {
            "schema_version": 1,
            "saved_at": time.strftime("%Y-%m-%d %H:%M:%S %z"),
            "config": config,
        }

    def _read_persisted_configuration(self) -> Optional[Dict[str, Any]]:
        """Read the local GUI parameter snapshot if it exists."""
        try:
            if not self._persist_config_path.exists():
                return None
            with open(self._persist_config_path, "r", encoding="utf-8") as handle:
                payload = json.load(handle)
            if isinstance(payload, dict) and isinstance(payload.get("config"), dict):
                return payload["config"]
            return payload if isinstance(payload, dict) else None
        except (OSError, json.JSONDecodeError) as exc:
            self._persist_logger.warning("Failed to read persisted GUI state: %s", exc)
            return None

    def _load_persisted_configuration(self, show_status: bool = False) -> bool:
        """Apply the latest local GUI parameter snapshot to the current controls."""
        config = self._read_persisted_configuration()
        if not config:
            if show_status:
                self.status_bar.showMessage("未找到本地参数快照", 3000)
            return False
        self._apply_gui_config(config)
        if show_status:
            self.status_bar.showMessage(f"已加载本地参数: {self._persist_config_path}", 3000)
        return True

    def _write_persisted_configuration(self, config: Dict[str, Any], show_status: bool = False) -> bool:
        """Write GUI parameters atomically to the local UTF-8 JSON snapshot."""
        try:
            self._persist_config_path.parent.mkdir(parents=True, exist_ok=True)
            temp_path = self._persist_config_path.with_suffix(self._persist_config_path.suffix + ".tmp")
            with open(temp_path, "w", encoding="utf-8") as handle:
                json.dump(self._persist_payload(config), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            temp_path.replace(self._persist_config_path)
            if show_status:
                self.status_bar.showMessage(f"参数已保存: {self._persist_config_path}", 3000)
            return True
        except OSError as exc:
            self._persist_logger.error("Failed to write persisted GUI state: %s", exc)
            if show_status:
                self.status_bar.showMessage(f"参数保存失败: {exc}", 5000)
            return False

    def _auto_save_configuration(self) -> None:
        """Debounced auto-save entry point used by parameter controls."""
        if self._persist_loading:
            return
        self._write_persisted_configuration(self.get_current_config())

    def _schedule_auto_save(self) -> None:
        """Schedule a local parameter save after the current burst of edits settles."""
        if self._persist_loading or not hasattr(self, "_auto_save_timer"):
            return
        self._auto_save_timer.start()

    def _set_spin_value(self, spin, value: Any) -> None:
        if spin is None or value is None:
            return
        try:
            spin.blockSignals(True)
            if isinstance(spin, QDoubleSpinBox):
                spin.setValue(float(value))
            elif isinstance(spin, QSpinBox):
                spin.setValue(int(round(float(value))))
            else:
                spin.setValue(value)
        except (TypeError, ValueError):
            return
        finally:
            try:
                spin.blockSignals(False)
            except RuntimeError:
                pass

    def _set_line_text(self, edit, value: Any) -> None:
        if edit is None or value is None:
            return
        edit.blockSignals(True)
        edit.setText(str(value))
        edit.blockSignals(False)

    def _set_checked(self, widget, value: Any) -> None:
        if widget is None or value is None:
            return
        widget.blockSignals(True)
        widget.setChecked(bool(value))
        widget.blockSignals(False)

    def _set_combo_value(self, combo, value: Any) -> None:
        if combo is None or value is None:
            return
        combo.blockSignals(True)
        try:
            index = combo.findData(value)
            if index < 0:
                index = combo.findText(str(value))
            if index < 0:
                alias = {
                    "none": "无滤波",
                    "lowpass": "低通",
                    "highpass": "高通",
                    "bandpass": "带通",
                    "bandstop": "带阻",
                }.get(str(value).strip().lower())
                if alias:
                    index = combo.findText(alias)
            if index >= 0:
                combo.setCurrentIndex(index)
        finally:
            combo.blockSignals(False)

    def _normalize_view_curve_source(self, value: Any) -> Any:
        """Map legacy View curve source names to the current FIP1/FIP2 labels."""
        return "FIP1" if value == "FIP" else value

    def _restore_fip_settings(self, config: Dict[str, Any]) -> None:
        communication = config.get("communication", {})
        self._set_line_text(getattr(self, "ip_edit", None), communication.get("ip"))
        self._set_spin_value(getattr(self, "port_spin", None), communication.get("port"))
        fip_settings = communication.get("fip", {})
        self._set_combo_value(getattr(self, "fip_sensor_count_combo", None), fip_settings.get("sensor_count"))
        self._set_spin_value(getattr(self, "fip_packet_duration_spin", None), fip_settings.get("packet_duration_seconds"))
        sample_rate_hz = fip_settings.get("sample_rate_hz")
        if sample_rate_hz is not None:
            self._set_spin_value(getattr(self, "fip_sample_rate_mhz_spin", None), float(sample_rate_hz) / 1_000_000.0)
        self._set_checked(getattr(self, "fip_phase_unwrap_check", None), fip_settings.get("phase_unwrap_enabled", False))
        self._update_fip_sensor_controls(emit=False)
        plot_target = fip_settings.get("plot_target")
        if plot_target is None and fip_settings.get("sensor_count", 1) == 2:
            plot_target = "both"
        self._set_combo_value(
            getattr(self, "fip_plot_sensor_combo", None),
            plot_target if plot_target is not None else fip_settings.get("selected_sensor"),
        )

    def _restore_preprocess_settings(self, config: Dict[str, Any]) -> None:
        preprocessing = config.get("preprocessing", {})
        filter_config = preprocessing.get("filter", {})
        filter_enabled = filter_config.get("enabled")
        if filter_enabled is None:
            filter_enabled = str(filter_config.get("type", "none")).lower() not in ("none", "无滤波")
        self._set_checked(getattr(self, "fip_filter_enable_check", None), filter_enabled)
        if filter_config.get("range"):
            self._set_line_text(getattr(self, "fip_filter_range_edit", None), filter_config.get("range"))
        else:
            self._set_range_text(
                getattr(self, "fip_filter_range_edit", None),
                filter_config.get("low_freq", 500),
                filter_config.get("high_freq", 6000),
            )
        self._set_spin_value(getattr(self, "filter_order_spin", None), filter_config.get("order"))
        self._set_spin_value(getattr(self, "downsample_spin", None), preprocessing.get("downsample", {}).get("factor"))

    def _restore_storage_settings(self, config: Dict[str, Any]) -> None:
        storage = config.get("storage", {})
        realtime = storage.get("realtime", {})
        self._set_checked(getattr(self, "phase_storage_check", None), realtime.get("enabled"))
        self._set_spin_value(getattr(self, "storage_interval_spin", None), realtime.get("interval"))
        self._set_spin_value(getattr(self, "storage_downsample_spin", None), realtime.get("downsample_factor"))
        self._set_line_text(getattr(self, "storage_path_edit", None), storage.get("path"))

    def _restore_view_settings(self, config: Dict[str, Any]) -> None:
        view = config.get("view", {})
        tab3_plot = config.get("tab3", {}).get("plot", {})
        if view.get("param_panel_width"):
            self._apply_param_panel_width(view.get("param_panel_width"))
        self._set_checked(getattr(self, "time_plot_btn", None), view.get("time_plot_enabled"))
        self._set_checked(getattr(self, "psd_plot_btn", None), view.get("psd_plot_enabled"))
        self._set_checked(getattr(self, "tab3_plot_toggle_btn", None), view.get("view_update_enabled", tab3_plot.get("plot_enabled")))
        self._set_spin_value(getattr(self, "time_display_duration_spin", None), view.get("time_display_seconds"))
        self._set_spin_value(getattr(self, "view_fip_refresh_spin", None), view.get("fip_refresh_seconds"))
        self._set_spin_value(getattr(self, "view_edas_refresh_spin", None), view.get("edas_refresh_seconds"))
        self._set_spin_value(getattr(self, "view_curve_max_points_spin", None), view.get("curve_max_points", tab3_plot.get("curve_max_points")))
        psd = view.get("psd", {})
        self._set_checked(getattr(self, "view_psd1_check", None), psd.get("psd1_enabled"))
        self._set_checked(getattr(self, "view_psd2_check", None), psd.get("psd2_enabled"))
        self._set_spin_value(getattr(self, "psd_window_length_spin", None), psd.get("window_seconds"))
        self._set_spin_value(getattr(self, "psd_overlap_spin", None), psd.get("overlap_percent"))
        axis = view.get("axis", {})
        self._set_checked(getattr(self, "view_axis_enable_check", None), axis.get("manual_enabled"))
        self._set_line_text(getattr(self, "view_x_range_edit", None), axis.get("x_range"))
        if not axis.get("x_range"):
            self._set_range_text(getattr(self, "view_x_range_edit", None), axis.get("x_min", 0.0), axis.get("x_max", 1.0))
        self._set_line_text(getattr(self, "view_y_range_edit", None), axis.get("y_range"))
        if not axis.get("y_range"):
            self._set_range_text(getattr(self, "view_y_range_edit", None), axis.get("y_min", -1.0), axis.get("y_max", 1.0))
        self._set_line_text(getattr(self, "view_psd_y_range_edit", None), axis.get("psd_y_range"))
        if not axis.get("psd_y_range"):
            self._set_range_text(getattr(self, "view_psd_y_range_edit", None), axis.get("psd_y_min", -160.0), axis.get("psd_y_max", 20.0))

    def _restore_tab2_settings(self, config: Dict[str, Any]) -> None:
        tab2 = config.get("tab2", {})
        if not tab2:
            return
        # Restore parameters but do not auto-enable the detection pipeline at startup.
        self._set_checked(getattr(self, "tab2_enable_btn", None), False)
        for key, enabled in tab2.get("compute_features", {}).items():
            controls = self.detection_feature_checkboxes.get(key, {})
            self._set_checked(controls.get("compute"), enabled)
        for key, enabled in tab2.get("plot_features", {}).items():
            controls = self.detection_feature_checkboxes.get(key, {})
            self._set_checked(controls.get("plot"), enabled)
        preprocess = tab2.get("preprocess", {})
        self._set_checked(getattr(self, "tab2_filter_enable_check", None), preprocess.get("enabled"))
        self._set_spin_value(getattr(self, "tab2_low_freq_spin", None), preprocess.get("low_hz"))
        self._set_spin_value(getattr(self, "tab2_high_freq_spin", None), preprocess.get("high_hz"))
        self._set_spin_value(getattr(self, "tab2_filter_order_spin", None), preprocess.get("order"))
        window = tab2.get("window", {})
        self._set_spin_value(getattr(self, "tab2_window_spin", None), window.get("window_seconds"))
        overlap_ratio = window.get("overlap_ratio")
        if overlap_ratio is not None:
            self._set_spin_value(getattr(self, "tab2_overlap_spin", None), float(overlap_ratio) * 100.0)
        self._set_spin_value(getattr(self, "tab2_plot_duration_spin", None), window.get("display_duration_seconds"))
        for key, threshold in tab2.get("thresholds", {}).items():
            controls = self.threshold_controls.get(key, {})
            self._set_spin_value(controls.get("threshold"), threshold)
        trigger = tab2.get("trigger_storage", {})
        self._set_checked(getattr(self, "tab2_trigger_storage_check", None), trigger.get("enabled"))
        self._set_spin_value(getattr(self, "tab2_pre_trigger_spin", None), trigger.get("pre_trigger_seconds"))
        self._set_spin_value(getattr(self, "tab2_post_trigger_spin", None), trigger.get("post_trigger_seconds"))
        self._set_line_text(getattr(self, "tab2_storage_path_edit", None), trigger.get("path"))

    def _restore_tab3_settings(self, config: Dict[str, Any]) -> None:
        tab3 = config.get("tab3", {})
        if not tab3:
            return
        communication = tab3.get("communication", {})
        self._set_line_text(getattr(self, "tab3_ip_edit", None), communication.get("ip"))
        self._set_spin_value(getattr(self, "tab3_port_spin", None), communication.get("port"))
        plot = tab3.get("plot", {})
        self._set_combo_value(getattr(self, "tab3_curve1_combo", None), self._normalize_view_curve_source(plot.get("curve1_type")))
        self._set_combo_value(getattr(self, "tab3_curve2_combo", None), self._normalize_view_curve_source(plot.get("curve2_type")))
        legacy_das_channel = plot.get("das_channel")
        self._set_spin_value(getattr(self, "tab3_curve1_das_channel_spin", None), plot.get("curve1_das_channel", legacy_das_channel))
        self._set_spin_value(getattr(self, "tab3_curve2_das_channel_spin", None), plot.get("curve2_das_channel", legacy_das_channel))
        if plot.get("display_seconds") is not None:
            self._set_spin_value(getattr(self, "time_display_duration_spin", None), plot.get("display_seconds"))
        legacy_apply_filter = plot.get("apply_filter")
        legacy_low_hz = plot.get("low_hz")
        legacy_high_hz = plot.get("high_hz")
        self._set_checked(getattr(self, "tab3_das_filter_enable_check", None), plot.get("das_apply_filter", plot.get("curve2_apply_filter", legacy_apply_filter)))
        if plot.get("das_filter_range"):
            self._set_line_text(getattr(self, "tab3_das_filter_range_edit", None), plot.get("das_filter_range"))
        else:
            self._set_range_text(
                getattr(self, "tab3_das_filter_range_edit", None),
                plot.get("low_hz", plot.get("curve2_low_hz", legacy_low_hz if legacy_low_hz is not None else 500)),
                plot.get("high_hz", plot.get("curve2_high_hz", legacy_high_hz if legacy_high_hz is not None else 6000)),
            )
        self._set_spin_value(
            getattr(self, "tab3_das_filter_order_spin", None),
            plot.get("das_filter_order", plot.get("filter_order", 4)),
        )
        if plot.get("channel_range"):
            self._set_line_text(getattr(self, "tab3_channel_range_edit", None), plot.get("channel_range"))
        else:
            self._set_range_text(
                getattr(self, "tab3_channel_range_edit", None),
                plot.get("channel_start", 0),
                plot.get("channel_end", 199),
            )
        self._set_spin_value(getattr(self, "tab3_time_downsample_spin", None), plot.get("time_downsample"))
        self._set_spin_value(getattr(self, "tab3_space_downsample_spin", None), plot.get("space_downsample"))
        self._set_spin_value(getattr(self, "tab3_space_time_total_seconds_spin", None), plot.get("space_time_total_seconds"))
        self._set_spin_value(getattr(self, "tab3_space_time_shift_seconds_spin", None), plot.get("space_time_shift_seconds"))
        self._set_combo_value(getattr(self, "tab3_colormap_combo", None), plot.get("colormap"))
        if plot.get("v_range"):
            self._set_line_text(getattr(self, "tab3_v_range_edit", None), plot.get("v_range"))
        else:
            self._set_range_text(getattr(self, "tab3_v_range_edit", None), plot.get("vmin", -0.3), plot.get("vmax", 0.3))
        storage = tab3.get("storage", {})
        self._set_checked(getattr(self, "tab3_joint_storage_toggle_btn", None), storage.get("joint_enabled", storage.get("enabled")))
        self._set_line_text(getattr(self, "tab3_storage_path_edit", None), storage.get("path"))
        self._set_spin_value(getattr(self, "tab3_storage_interval_spin", None), storage.get("interval_seconds"))
        self._set_spin_value(getattr(self, "tab3_cache_seconds_spin", None), storage.get("cache_seconds"))
        self._set_checked(getattr(self, "tab3_edas_storage_toggle_btn", None), storage.get("edas_enabled"))
        self._set_line_text(getattr(self, "tab3_edas_storage_path_edit", None), storage.get("edas_path"))
        self._set_spin_value(getattr(self, "tab3_edas_blocks_per_file_spin", None), storage.get("edas_blocks_per_file"))
        self._set_spin_value(getattr(self, "tab3_edas_queue_packets_spin", None), storage.get("edas_queue_packets"))

    def _restore_global_display_settings(self, config: Dict[str, Any]) -> None:
        setting = config.get("setting", {})
        self._set_spin_value(getattr(self, "setting_gui_font_spin", None), setting.get("gui_font_pt"))
        self._set_spin_value(getattr(self, "setting_plot_title_font_spin", None), setting.get("plot_title_px"))
        self._set_spin_value(getattr(self, "setting_axis_label_font_spin", None), setting.get("axis_label_px"))
        self._set_spin_value(getattr(self, "setting_tick_font_spin", None), setting.get("tick_font_pt"))
        self._set_spin_value(getattr(self, "setting_psd_downsample_spin", None), setting.get("psd_downsample_factor"))

    def _apply_gui_config(self, config: Dict[str, Any]) -> None:
        """Restore saved GUI parameters while keeping communication stopped."""
        if not isinstance(config, dict):
            return
        self._persist_loading = True
        try:
            self._restore_fip_settings(config)
            self._restore_preprocess_settings(config)
            self._restore_storage_settings(config)
            self._restore_view_settings(config)
            self._restore_tab2_settings(config)
            self._restore_tab3_settings(config)
            self._restore_global_display_settings(config)
        finally:
            self._persist_loading = False
        self.monitoring_active = False
        self._edas_monitoring_active = False
        self._both_comm_requested = False
        self._apply_restored_gui_state()

    def _apply_restored_gui_state(self) -> None:
        """Refresh dependent styles, plots, and derived runtime fields after restore."""
        self._update_fip_sensor_controls(emit=False)
        self._apply_view_refresh_settings()
        self._update_psd_settings()
        self._update_filter_settings()
        self._update_time_display_settings()
        self._toggle_time_plot(self.time_plot_btn.isChecked())
        self._toggle_psd_plot(self.psd_plot_btn.isChecked())
        self._update_tab3_plot_button_state(self.tab3_plot_toggle_btn.isChecked())
        self._update_data_comm_buttons()
        self._update_data_storage_buttons()
        self._update_tab2_enable_button_state(False)
        self._apply_tab3_space_time_colormap()
        self._apply_tab3_space_time_levels()
        self._apply_global_display_runtime_settings()
        if self.view_axis_enable_check.isChecked():
            self._apply_view_axes()
        if hasattr(self, 'fip_sensor_settings_changed'):
            self.fip_sensor_settings_changed.emit(self.get_tab1_fip_settings())
        if hasattr(self, 'tab2_settings_changed'):
            self.tab2_settings_changed.emit()
        if hasattr(self, 'tab3_settings_changed'):
            self.tab3_settings_changed.emit()

    def _auto_persist_widgets(self) -> List[Any]:
        """Return user-editable parameter widgets that should trigger auto-save."""
        widgets = [
            self.ip_edit, self.port_spin, self.fip_packet_duration_spin,
            self.fip_sample_rate_mhz_spin, self.fip_sensor_count_combo, self.fip_plot_sensor_combo,
            self.fip_filter_enable_check, self.fip_filter_range_edit,
            self.filter_order_spin, self.downsample_spin, self.fip_phase_unwrap_check,
            self.time_plot_btn, self.psd_plot_btn, self.tab3_plot_toggle_btn,
            self.time_display_duration_spin, self.view_fip_refresh_spin,
            self.view_edas_refresh_spin, self.view_curve_max_points_spin,
            self.view_psd1_check, self.view_psd2_check,
            self.psd_window_length_spin, self.psd_overlap_spin,
            self.view_axis_enable_check, self.view_x_range_edit,
            self.view_y_range_edit, self.view_psd_y_range_edit,
            self.phase_storage_check, self.storage_path_edit, self.storage_interval_spin, self.storage_downsample_spin,
            self.tab2_enable_btn, self.tab2_filter_enable_check, self.tab2_low_freq_spin,
            self.tab2_high_freq_spin, self.tab2_filter_order_spin, self.tab2_window_spin,
            self.tab2_overlap_spin, self.tab2_plot_duration_spin, self.tab2_trigger_storage_check,
            self.tab2_pre_trigger_spin, self.tab2_post_trigger_spin, self.tab2_storage_path_edit,
            self.tab3_ip_edit, self.tab3_port_spin, self.tab3_curve1_combo, self.tab3_curve2_combo,
            self.tab3_curve1_das_channel_spin, self.tab3_curve2_das_channel_spin,
            self.tab3_das_filter_enable_check, self.tab3_das_filter_range_edit, self.tab3_das_filter_order_spin,
            self.tab3_channel_range_edit, self.tab3_space_time_total_seconds_spin,
            self.tab3_space_time_shift_seconds_spin, self.tab3_time_downsample_spin, self.tab3_space_downsample_spin,
            self.tab3_colormap_combo, self.tab3_v_range_edit,
            self.tab3_joint_storage_toggle_btn, self.tab3_storage_path_edit,
            self.tab3_storage_interval_spin, self.tab3_cache_seconds_spin,
            self.tab3_edas_storage_toggle_btn, self.tab3_edas_storage_path_edit,
            self.tab3_edas_blocks_per_file_spin, self.tab3_edas_queue_packets_spin,
            self.setting_gui_font_spin, self.setting_plot_title_font_spin,
            self.setting_axis_label_font_spin, self.setting_tick_font_spin,
            self.setting_psd_downsample_spin,
        ]
        for controls in getattr(self, "detection_feature_checkboxes", {}).values():
            widgets.extend([controls.get("compute"), controls.get("plot")])
        for controls in getattr(self, "threshold_controls", {}).values():
            widgets.append(controls.get("threshold"))
        return [widget for widget in widgets if widget is not None]

    def _connect_auto_persist_signals(self) -> None:
        """Connect all editable parameter widgets to debounced local auto-save."""
        if getattr(self, "_auto_persist_connected", False):
            return
        self._auto_persist_connected = True
        for widget in self._auto_persist_widgets():
            if hasattr(widget, "valueChanged"):
                widget.valueChanged.connect(lambda *_args: self._schedule_auto_save())
            elif hasattr(widget, "currentIndexChanged"):
                widget.currentIndexChanged.connect(lambda *_args: self._schedule_auto_save())
            elif hasattr(widget, "toggled"):
                widget.toggled.connect(lambda *_args: self._schedule_auto_save())
            elif hasattr(widget, "textChanged"):
                widget.textChanged.connect(lambda *_args: self._schedule_auto_save())

    def closeEvent(self, event):
        """Flush the latest GUI parameters before the window closes."""
        if hasattr(self, "_auto_save_timer") and self._auto_save_timer.isActive():
            self._auto_save_timer.stop()
        self._write_persisted_configuration(self.get_current_config())
        super().closeEvent(event)


    def update_connection_status(self, connected: bool, message: str):
        # Update FIP communication state shown on the Data tab.
        if connected:
            self.conn_status_label.setText("已连接")
            self.conn_status_label.setStyleSheet("color: green; font-weight: bold;")
            if hasattr(self, 'data_fip_status_label'):
                self.data_fip_status_label.setText("已连接")
                self.data_fip_status_label.setStyleSheet("color: green; font-weight: bold;")
        else:
            self.conn_status_label.setText("未连接")
            self.conn_status_label.setStyleSheet("color: red; font-weight: bold;")
            if hasattr(self, 'data_fip_status_label'):
                self.data_fip_status_label.setText("未连接")
                self.data_fip_status_label.setStyleSheet("color: red; font-weight: bold;")
        self._refresh_comm_lights()
        self.status_bar.showMessage(message, 3000)


    def update_statistics(self, stats: Dict[str, Any]):
        # Mirror FIP packet statistics into the unified Data tab.
        packets_received = int(stats.get('packets_received', 0) or 0)
        loss_rate = float(stats.get('loss_rate', 0.0) or 0.0)
        derived_failures = int(stats.get('packets_lost', stats.get('missing_packets', 0)) or 0)
        self._fip_comm_failure_count = max(self._fip_comm_failure_count, derived_failures)
        self.packet_count_label.setText(str(packets_received))
        self.loss_rate_label.setText(f"{loss_rate:.2f}%")
        if hasattr(self, 'data_fip_success_label'):
            self.data_fip_success_label.setText(str(packets_received))
            self.data_fip_failure_label.setText(str(self._fip_comm_failure_count))
            self.data_fip_loss_rate_label.setText(f"{loss_rate:.2f}%")
            try:
                last_comm = int(stats.get('last_comm_count', -1))
            except (TypeError, ValueError):
                last_comm = -1
            self.data_fip_last_comm_label.setText("-" if last_comm < 0 else str(last_comm))
        self._refresh_comm_lights()

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

    def is_tab2_enabled(self) -> bool:
        """Return whether the independent Tab2 pipeline should run."""
        return bool(getattr(self, "tab2_enable_btn", None) and self.tab2_enable_btn.isChecked())

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
        curve1_das_channel = self.tab3_curve1_das_channel_spin.value()
        curve2_das_channel = self.tab3_curve2_das_channel_spin.value()
        das_filter = self.get_tab3_das_filter_settings()
        das_filter_enabled = bool(das_filter["enabled"])
        das_low_hz = float(das_filter["low_freq"])
        das_high_hz = float(das_filter["high_freq"])
        channel_start, channel_end = self.get_tab3_channel_range()
        vmin, vmax = self.get_tab3_v_range()
        return {
            "communication": {
                "ip": self.tab3_ip_edit.text(),
                "port": self.tab3_port_spin.value(),
            },
            "plot": {
                "curve1_type": self.tab3_curve1_combo.currentText(),
                "curve2_type": self.tab3_curve2_combo.currentText(),
                "curve1_das_channel": curve1_das_channel,
                "curve2_das_channel": curve2_das_channel,
                "das_channel": curve2_das_channel,
                "display_seconds": self.time_display_duration_spin.value(),
                "das_apply_filter": das_filter_enabled,
                "das_filter_range": das_filter["range"],
                "das_filter_type": das_filter["type"],
                "das_filter_order": das_filter["order"],
                "curve1_apply_filter": das_filter_enabled,
                "curve1_filter_type": das_filter["type"],
                "curve1_filter_order": das_filter["order"],
                "curve1_low_hz": das_low_hz,
                "curve1_high_hz": das_high_hz,
                "curve2_apply_filter": das_filter_enabled,
                "curve2_filter_type": das_filter["type"],
                "curve2_filter_order": das_filter["order"],
                "curve2_low_hz": das_low_hz,
                "curve2_high_hz": das_high_hz,
                "apply_filter": das_filter_enabled,
                "filter_type": das_filter["type"],
                "filter_order": das_filter["order"],
                "low_hz": das_low_hz,
                "high_hz": das_high_hz,
                "channel_range": self.tab3_channel_range_edit.text(),
                "channel_start": channel_start,
                "channel_end": channel_end,
                "space_time_total_seconds": self.tab3_space_time_total_seconds_spin.value(),
                "space_time_shift_seconds": self.tab3_space_time_shift_seconds_spin.value(),
                "time_downsample": self.tab3_time_downsample_spin.value(),
                "space_downsample": self.tab3_space_downsample_spin.value(),
                "plot_enabled": self.is_tab3_plot_enabled(),
                "colormap": self.tab3_colormap_combo.currentData(),
                "v_range": self.tab3_v_range_edit.text(),
                "vmin": vmin,
                "vmax": vmax,
                "curve_max_points": self._tab3_curve_max_points,
                "space_time_max_pixels": self._tab3_space_time_max_pixels,
            },
            "storage": {
                "enabled": self.tab3_joint_storage_toggle_btn.isChecked(),
                "joint_enabled": self.tab3_joint_storage_toggle_btn.isChecked(),
                "path": self.tab3_storage_path_edit.text(),
                "interval_seconds": self.tab3_storage_interval_spin.value(),
                "cache_seconds": self.tab3_cache_seconds_spin.value(),
                "edas_enabled": self.tab3_edas_storage_toggle_btn.isChecked(),
                "edas_path": self.tab3_edas_storage_path_edit.text(),
                "edas_blocks_per_file": self.tab3_edas_blocks_per_file_spin.value(),
                "edas_queue_packets": self.tab3_edas_queue_packets_spin.value(),
            },
        }


    def update_tab3_connection_status(self, connected: bool, message: str):
        # Update eDAS communication state shown on the Data tab.
        self.tab3_conn_status_label.setText("已连接" if connected else "未连接")
        self.tab3_conn_status_label.setStyleSheet(
            "color: green; font-weight: bold;" if connected else "color: red; font-weight: bold;"
        )
        if hasattr(self, 'data_edas_status_label'):
            self.data_edas_status_label.setText("已连接" if connected else "未连接")
            self.data_edas_status_label.setStyleSheet(
                "color: green; font-weight: bold;" if connected else "color: red; font-weight: bold;"
            )
        self._refresh_comm_lights()
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
        # Mirror eDAS packet counters into the unified Data tab.
        packets_received = int(stats.get("packets_received", 0) or 0)
        missing_packets = int(stats.get("missing_packets", 0) or 0)
        total_packets = packets_received + missing_packets
        loss_rate = (missing_packets / total_packets * 100.0) if total_packets > 0 else 0.0
        self._edas_comm_failure_count = max(self._edas_comm_failure_count, missing_packets)
        self.tab3_packet_count_label.setText(str(packets_received))
        self.tab3_missing_packet_label.setText(str(missing_packets))
        if hasattr(self, 'data_edas_success_label'):
            self.data_edas_success_label.setText(str(packets_received))
            self.data_edas_failure_label.setText(str(self._edas_comm_failure_count))
            self.data_edas_loss_rate_label.setText(f"{loss_rate:.2f}%")
        self._refresh_comm_lights()

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
        # Joint FIP+eDAS storage emits this after completed files and during waiting states.
        text = str(path)
        self.tab3_last_storage_label.setText(text)
        lowered = text.lower()
        last_text = getattr(self, '_last_joint_storage_status_seen', None)
        if text and text != last_text:
            self._last_joint_storage_status_seen = text
            if any(token in lowered for token in ("error", "fail", "失败", "fallback")):
                self._fip_storage_failure_count += 1
                self._edas_storage_failure_count += 1
            elif any(token in lowered for token in (".npz", ".npy", ".bin")) and not lowered.startswith("started"):
                self._fip_storage_success_count += 1
                self._edas_storage_success_count += 1
        self._update_data_storage_buttons()


    def update_tab3_edas_storage_status(self, path: str):
        # eDAS-only storage reports block writes through status text containing blocks=.
        text = str(path)
        self.tab3_edas_last_storage_label.setText(text)
        lowered = text.lower()
        last_text = getattr(self, '_last_edas_storage_status_seen', None)
        if text and text != last_text:
            self._last_edas_storage_status_seen = text
            if any(token in lowered for token in ("error", "fail", "失败", "dropped")):
                self._edas_storage_failure_count += 1
            elif "blocks=" in lowered or (any(token in lowered for token in (".bin", ".dat")) and not lowered.startswith("started")):
                self._edas_storage_success_count += 1
        self._update_data_storage_buttons()

    def update_tab3_fip_curve(
        self,
        comm_count: int,
        values,
        sample_rate_hz: float,
        sensor_count: int = 1,
        values_by_sensor: Dict[int, Any] = None,
        packet_duration_seconds: float = 1.0,
        psd_values_by_sensor: Dict[int, Any] = None,
        psd_sample_rate_hz: float = None,
    ):
        """Update cached FIP comparison curves shown in Tab3."""
        curve1_mode = self.tab3_curve1_combo.currentText()
        curve2_mode = self.tab3_curve2_combo.currentText()
        fip_modes = ("FIP", "FIP1", "FIP2")
        if curve1_mode not in fip_modes:
            self._render_tab3_curve(self.tab3_curve1_fip_curve, curve1_mode, [], [], "FIP")
        if curve2_mode not in fip_modes:
            self._render_tab3_curve(self.tab3_curve2_fip_curve, curve2_mode, [], [], "FIP")
        if curve1_mode not in fip_modes and curve2_mode not in fip_modes:
            return
        if not self.is_tab3_plot_enabled():
            return
        if hasattr(self, 'tab_widget') and self.tab_widget.currentIndex() != 0:
            return
        now = time.monotonic()
        if now - self._tab3_last_fip_plot_monotonic < self._tab3_fip_plot_min_interval_seconds:
            values_arr = np.asarray(values)
            self._tab3_logger.debug(
                "TAB3_NODE ui.fip_curve skip_throttle comm=%s points=%d",
                comm_count,
                values_arr.size,
            )
            return
        self._tab3_last_fip_plot_monotonic = now
        started = time.perf_counter()
        rendered_points = 0
        source_points = 0
        for curve_item, curve_mode in (
            (self.tab3_curve1_fip_curve, curve1_mode),
            (self.tab3_curve2_fip_curve, curve2_mode),
        ):
            if curve_mode not in fip_modes:
                continue
            sensor_index = 2 if curve_mode == "FIP2" else 1
            sensor_values = None
            if isinstance(values_by_sensor, dict):
                sensor_values = values_by_sensor.get(sensor_index)
            if sensor_values is None and (curve_mode == "FIP" or sensor_count == 1 or sensor_index == 1):
                sensor_values = values
            values_arr = np.asarray(sensor_values) if sensor_values is not None else np.asarray([])
            if values_arr.size == 0:
                self._render_tab3_curve(curve_item, curve_mode, [], [], curve_mode)
                continue

            step = max(1, int(np.ceil(values_arr.size / max(1, self._tab3_curve_max_points))))
            selected_indexes = np.arange(0, values_arr.size, step, dtype=np.float64)
            safe_packet_duration = max(float(packet_duration_seconds), 1e-6)
            times = (comm_count * safe_packet_duration) + selected_indexes / max(float(sample_rate_hz), 1.0)
            plot_values = np.ascontiguousarray(values_arr[::step], dtype=np.float32)
            window_times, window_values = self._accumulate_fip_rolling(
                curve_index=self._curve_index_for_item(curve_item),
                sensor_index=sensor_index,
                times=times,
                values=plot_values,
            )
            psd_values = None
            if isinstance(psd_values_by_sensor, dict):
                psd_values = psd_values_by_sensor.get(sensor_index)
            if psd_values is None:
                psd_values = sensor_values
            psd_values_arr = np.asarray(psd_values) if psd_values is not None else np.asarray([])
            psd_rate = max(float(psd_sample_rate_hz or sample_rate_hz), 1.0)
            source_points = max(source_points, int(values_arr.size))
            rendered_points = max(rendered_points, int(window_values.size))
            if plot_values.size and abs(float(plot_values[0])) <= 1e-12 and comm_count % 50 == 0:
                self._tab3_logger.warning(
                    "TAB3_NODE ui.fip_curve_first_zero comm=%s curve=%s sensor=FIP%s "
                    "source_first=%.9g plot_first=%.9g source_points=%d plot_points=%d step=%d",
                    comm_count,
                    curve_mode,
                    sensor_index,
                    float(values_arr.flat[0]),
                    float(plot_values[0]),
                    int(values_arr.size),
                    int(plot_values.size),
                    step,
                )
            self._render_tab3_curve(
                curve_item,
                curve_mode,
                window_times,
                window_values,
                curve_mode,
                cache_values=psd_values_arr,
                cache_sample_rate=psd_rate,
            )
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self._tab3_logger.debug(
            "TAB3_NODE ui.fip_curve comm=%s sensors=%s source_points=%d plot_points=%d elapsed_ms=%.2f",
            comm_count,
            sensor_count,
            source_points,
            rendered_points,
            elapsed_ms,
        )
        if elapsed_ms > self._tab3_ui_slow_threshold_ms:
            self._tab3_logger.warning(
                "TAB3_NODE ui.fip_curve_slow comm=%s elapsed_ms=%.2f plot_points=%d",
                comm_count,
                elapsed_ms,
                rendered_points,
            )

    def update_tab3_plot_payload(self, payload: Dict[str, Any]):
        """Apply the latest DAS plot payload to Tab3 widgets."""
        header = payload.get("header", {})
        self.update_tab3_header_status(header)
        if not self.is_tab3_plot_enabled():
            return
        if hasattr(self, 'tab_widget') and self.tab_widget.currentIndex() != 0:
            return
        now = time.monotonic()
        comm_count = header.get("comm_count", "-")
        if now - self._tab3_last_das_plot_monotonic < self._tab3_das_plot_min_interval_seconds:
            self._tab3_logger.debug("TAB3_NODE ui.das_payload skip_throttle comm=%s", comm_count)
            return
        self._tab3_last_das_plot_monotonic = now
        started = time.perf_counter()

        das_times1 = payload.get("curve1_das_time", payload.get("das_curve_time", []))
        das_values1 = payload.get("curve1_das_values", payload.get("das_curve_values", []))
        das_times2 = payload.get("curve2_das_time", payload.get("das_curve_time", []))
        das_values2 = payload.get("curve2_das_values", payload.get("das_curve_values", []))
        self._render_tab3_curve(self.tab3_curve1_das_curve, self.tab3_curve1_combo.currentText(), das_times1, das_values1, "DAS Channel")
        self._render_tab3_curve(self.tab3_curve2_das_curve, self.tab3_curve2_combo.currentText(), das_times2, das_values2, "DAS Channel")

        matrix = payload.get("space_time_matrix")
        x_axis = payload.get("space_time_x")
        y_axis = payload.get("space_time_y")
        if matrix is None or len(np.shape(matrix)) != 2 or matrix.size == 0:
            self._reset_tab3_space_time_image()
            return
        matrix = np.ascontiguousarray(matrix, dtype=np.float32)
        levels = self.get_tab3_v_range()
        if levels[0] >= levels[1]:
            self._set_tab3_space_time_levels(levels[0], levels[0] + 1e-6)
            levels = self.get_tab3_v_range()
        x_scale = 1.0
        x_offset = 0.0
        if x_axis is not None and len(x_axis) > 0:
            x_offset = float(x_axis[0])
            if len(x_axis) > 1:
                x_scale = float(x_axis[1] - x_axis[0])
        y_scale = 1.0
        y_offset = 0.0
        if y_axis is not None and len(y_axis) > 0:
            y_offset = float(y_axis[0])
            if len(y_axis) > 1:
                y_scale = float(y_axis[1] - y_axis[0])
        x_width = max(x_scale, 1e-12) * matrix.shape[1]
        y_height = max(y_scale, 1e-12) * matrix.shape[0]
        self.tab3_space_time_image.setImage(matrix, autoLevels=False, levels=levels)
        rect = (x_offset, y_offset, x_width, y_height)
        if self._tab3_last_space_time_rect != rect:
            self.tab3_space_time_image.setRect(*rect)
            self._tab3_last_space_time_rect = rect
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self._tab3_logger.debug(
            "TAB3_NODE ui.das_payload comm=%s curve_points=%d matrix_shape=%s elapsed_ms=%.2f",
            comm_count,
            max(
                len(das_values1) if hasattr(das_values1, "__len__") else 0,
                len(das_values2) if hasattr(das_values2, "__len__") else 0,
            ),
            tuple(matrix.shape),
            elapsed_ms,
        )
        if elapsed_ms > self._tab3_ui_slow_threshold_ms:
            self._tab3_logger.warning(
                "TAB3_NODE ui.das_payload_slow comm=%s elapsed_ms=%.2f matrix_shape=%s",
                comm_count,
                elapsed_ms,
                tuple(matrix.shape),
            )


    def reset_tab3_views(self):
        # Clear View tab plots, cached PSD inputs, and runtime labels.
        self.tab3_curve1_das_curve.setData([], [])
        self.tab3_curve1_fip_curve.setData([], [])
        self.tab3_curve2_das_curve.setData([], [])
        self.tab3_curve2_fip_curve.setData([], [])
        for curve_item in (
            self.tab3_curve1_das_curve,
            self.tab3_curve1_fip_curve,
            self.tab3_curve2_das_curve,
            self.tab3_curve2_fip_curve,
        ):
            self._set_curve_legend_name(curve_item, None)
        if hasattr(self, 'view_psd1_curve'):
            self.view_psd1_curve.setData([], [])
            self.view_psd2_curve.setData([], [])
        self._view_curve_cache = {
            1: {'source': None, 'times': np.asarray([]), 'values': np.asarray([])},
            2: {'source': None, 'times': np.asarray([]), 'values': np.asarray([])},
        }
        self._fip_curve_rolling.clear()
        self._reset_tab3_space_time_image()
        self._tab3_last_fip_plot_monotonic = 0.0
        self._tab3_last_das_plot_monotonic = 0.0
        self._view_psd_update_pending = False
        self.tab3_last_storage_label.setText("-")
        self.tab3_edas_last_storage_label.setText("-")
        self.tab3_packet_count_label.setText("0")
        self.tab3_missing_packet_label.setText("0")
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
        self._set_range_text(getattr(self, "tab3_v_range_edit", None), vmin, vmax)
        self._tab3_space_time_levels_locked = lock

    def _apply_tab3_space_time_levels(self):
        """Apply the current vmin/vmax settings to the Tab3 image and histogram."""
        vmin, vmax = self.get_tab3_v_range()
        if vmin >= vmax:
            vmax = vmin + 1e-6
            self._set_tab3_space_time_levels(vmin, vmax)
            vmin, vmax = self.get_tab3_v_range()
        self.tab3_space_time_image.setLevels((vmin, vmax))
        if hasattr(self, "tab3_space_time_histogram"):
            self.tab3_space_time_histogram.setLevels(vmin, vmax)

    def _update_tab3_space_time_histogram_range(self, matrix: np.ndarray, levels: Tuple[float, float]):
        """Keep the colorbar range fixed to the manual vmin/vmax controls."""
        if not hasattr(self, "tab3_space_time_histogram") or matrix.size == 0:
            return
        histogram_min = float(levels[0])
        histogram_max = float(levels[1])
        if histogram_min >= histogram_max:
            histogram_max = histogram_min + 1e-6
        histogram_item = getattr(self.tab3_space_time_histogram, "item", None)
        if histogram_item is not None and hasattr(histogram_item, "setHistogramRange"):
            histogram_item.setHistogramRange(histogram_min, histogram_max, padding=0.0)

    def _build_tab3_toggle_button_style(
        self,
        checked: bool,
        active_color: str,
        inactive_color: str,
        font_size: int = 16,
        padding: str = "10px 12px",
    ) -> str:
        """Return a shared stylesheet for Tab3 checkable buttons."""
        background = active_color if checked else inactive_color
        hover = active_color if checked else inactive_color
        return f"""
            QPushButton {{
                font-size: {font_size}px;
                font-weight: bold;
                padding: {padding};
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
        # Refresh the eDAS communication button text/style and Data-tab indicators.
        self._edas_monitoring_active = bool(active)
        self.tab3_start_stop_btn.blockSignals(True)
        self.tab3_start_stop_btn.setChecked(active)
        self.tab3_start_stop_btn.blockSignals(False)
        self.tab3_start_stop_btn.setText("停止eDAS通信" if active else "启动eDAS通信")
        self._style_action_button(self.tab3_start_stop_btn, active)
        if not active and not self.monitoring_active:
            self._both_comm_requested = False
        self._update_data_comm_buttons()


    def _update_tab3_plot_button_state(self, enabled: bool):
        # Refresh the View update toggle.
        self.tab3_plot_toggle_btn.blockSignals(True)
        self.tab3_plot_toggle_btn.setChecked(enabled)
        self.tab3_plot_toggle_btn.blockSignals(False)
        self.tab3_plot_toggle_btn.setText("刷新 ON" if enabled else "刷新 OFF")
        self._style_toggle_button(self.tab3_plot_toggle_btn, enabled, min_width=96)


    def _update_tab3_storage_button_state(self, enabled: bool):
        # Refresh joint storage button and module-level storage indicators.
        self.tab3_joint_storage_toggle_btn.blockSignals(True)
        self.tab3_joint_storage_toggle_btn.setChecked(enabled)
        self.tab3_joint_storage_toggle_btn.blockSignals(False)
        self._update_data_storage_buttons()


    def _update_tab3_edas_storage_button_state(self, enabled: bool):
        # Refresh eDAS-only storage button and module-level storage indicators.
        self.tab3_edas_storage_toggle_btn.blockSignals(True)
        self.tab3_edas_storage_toggle_btn.setChecked(enabled)
        self.tab3_edas_storage_toggle_btn.blockSignals(False)
        self._update_data_storage_buttons()


    def route_tab3_joint_storage_fallback(self, target: str, message: str):
        # Joint storage can fall back to the single module that is currently online.
        if hasattr(self, 'tab3_joint_storage_toggle_btn'):
            self.tab3_joint_storage_toggle_btn.setChecked(False)
        if target == "edas" and hasattr(self, 'tab3_edas_storage_toggle_btn'):
            self.tab3_edas_storage_toggle_btn.setChecked(True)
        elif target == "fip" and hasattr(self, 'phase_storage_check'):
            self.phase_storage_check.setChecked(True)
        if hasattr(self, 'tab_widget'):
            self.tab_widget.setCurrentIndex(1)
        self._update_data_storage_buttons()
        self.status_bar.showMessage(message, 5000)

    def _on_tab3_colormap_changed(self, _text: str):
        """Handle Tab3 space-time colormap changes."""
        self._apply_tab3_space_time_colormap()
        self._emit_tab3_settings_changed()

    def _on_tab3_v_range_changed(self):
        """Handle manual Tab3 color range changes."""
        self._tab3_space_time_levels_locked = True
        self._apply_tab3_space_time_levels()
        self._emit_tab3_settings_changed()

    def is_tab3_plot_enabled(self) -> bool:
        """Return whether Tab3 plot widgets should update."""
        return self.tab3_plot_toggle_btn.isChecked()

    def _reset_tab3_space_time_image(self):
        """Restore the Tab3 space-time image to a known empty state."""
        empty = np.zeros((1, 1), dtype=np.float32)
        self.tab3_space_time_image.setImage(
            empty,
            autoLevels=False,
            levels=self.get_tab3_v_range(),
        )
        self.tab3_space_time_image.setRect(0.0, 0.0, 1.0, 1.0)
        self._tab3_last_space_time_rect = (0.0, 0.0, 1.0, 1.0)
        self._apply_tab3_space_time_levels()

    def _configure_tab3_curve_item(self, curve_item):
        """Use pyqtgraph fast paths for long DAS/FIP comparison curves."""
        if hasattr(curve_item, "setClipToView"):
            curve_item.setClipToView(True)
        if hasattr(curve_item, "setDownsampling"):
            curve_item.setDownsampling(auto=True, method="subsample")
        if hasattr(curve_item, "setSkipFiniteCheck"):
            curve_item.setSkipFiniteCheck(True)

    def _configure_interactive_plot(self, plot: pg.PlotWidget) -> None:
        """Enable pan/zoom tools and left-drag rectangle zoom on a pyqtgraph plot."""
        if plot is None:
            return
        try:
            view_box = plot.getViewBox()
            view_box.setMouseEnabled(x=True, y=True)
            view_box.setMouseMode(pg.ViewBox.RectMode)
            if hasattr(view_box, "sigRangeChangedManually"):
                view_box.sigRangeChangedManually.connect(
                    lambda *_args, plot_widget=plot: self._handle_plot_manual_range_change(plot_widget)
                )
            plot.setMenuEnabled(True)
            if hasattr(plot, "showButtons"):
                plot.showButtons()
        except Exception as exc:
            self._tab3_logger.debug("TAB3_NODE ui.plot_interaction_setup_failed %s", exc)

    def _handle_plot_manual_range_change(self, plot: pg.PlotWidget) -> None:
        """Keep live setData calls from fighting user pan/zoom interactions."""
        self._view_user_range_active = True
        try:
            plot.enableAutoRange(axis=pg.ViewBox.XYAxes, enable=False)
        except Exception:
            pass

    def _plot_for_curve_item(self, curve_item) -> Optional[pg.PlotWidget]:
        if curve_item in (getattr(self, 'tab3_curve1_das_curve', None), getattr(self, 'tab3_curve1_fip_curve', None)):
            return getattr(self, 'tab3_curve1_plot', None)
        if curve_item in (getattr(self, 'tab3_curve2_das_curve', None), getattr(self, 'tab3_curve2_fip_curve', None)):
            return getattr(self, 'tab3_curve2_plot', None)
        return None

    def _legend_name_for_curve(self, curve_index: int, curve_mode: str) -> str:
        """Return the active legend text for one View time-domain curve."""
        if curve_mode == "DAS Channel":
            spin = getattr(self, f"tab3_curve{curve_index}_das_channel_spin", None)
            channel = int(spin.value()) if spin is not None else 0
            return f"eDAS ch={channel}"
        if curve_mode in ("FIP", "FIP1"):
            return "FIP1"
        if curve_mode == "FIP2":
            return "FIP2"
        return str(curve_mode or "")

    def _set_curve_legend_name(self, curve_item, legend_name: Optional[str]) -> None:
        """Keep only the currently visible curve item in each time-domain legend."""
        plot = self._plot_for_curve_item(curve_item)
        legend = plot.plotItem.legend if plot is not None else None
        previous_name = getattr(curve_item, "_view_legend_name", None)
        if legend is None:
            return
        if previous_name and previous_name != legend_name:
            try:
                legend.removeItem(previous_name)
            except Exception:
                pass
        if legend_name and previous_name != legend_name:
            try:
                legend.addItem(curve_item, legend_name)
            except Exception:
                pass
        setattr(curve_item, "_view_legend_name", legend_name)

    def _downsample_tab3_curve(self, times, values) -> Tuple[np.ndarray, np.ndarray]:
        """Bound one UI curve to a fixed point budget before setData."""
        values_arr = np.asarray(values)
        times_arr = np.asarray(times)
        point_count = min(values_arr.size, times_arr.size)
        if point_count <= 0:
            return np.array([], dtype=np.float64), np.array([], dtype=np.float32)
        values_arr = values_arr[:point_count]
        times_arr = times_arr[:point_count]
        step = max(1, int(np.ceil(point_count / max(1, self._tab3_curve_max_points))))
        return (
            np.ascontiguousarray(times_arr[::step], dtype=np.float64),
            np.ascontiguousarray(values_arr[::step], dtype=np.float32),
        )

    def _time_window_seconds(self) -> float:
        """Return the rolling time-domain display window in seconds."""
        spin = getattr(self, "time_display_duration_spin", None)
        if spin is not None:
            try:
                return max(0.2, float(spin.value()))
            except (TypeError, ValueError):
                pass
        return 1.0

    def _accumulate_fip_rolling(
        self,
        curve_index: int,
        sensor_index: int,
        times: np.ndarray,
        values: np.ndarray,
    ) -> Tuple[np.ndarray, np.ndarray]:
        """Append one FIP packet to its rolling window and trim to the display duration.

        The View time-domain curves receive only one packet per update. Without a
        rolling buffer the x-axis uses cumulative packet time, so the trace scrolls
        right and falls out of view. Keeping a per-curve/per-sensor deque and
        trimming to the display window makes the latest waveform stay on screen.
        """
        key = (int(curve_index), int(sensor_index))
        state = self._fip_curve_rolling.get(key)
        if state is None:
            state = {"times": deque(), "values": deque()}
            self._fip_curve_rolling[key] = state
        times_arr = np.asarray(times, dtype=np.float64)
        values_arr = np.asarray(values, dtype=np.float32)
        if times_arr.size != values_arr.size:
            count = min(times_arr.size, values_arr.size)
            times_arr = times_arr[:count]
            values_arr = values_arr[:count]
        if times_arr.size == 0:
            return np.array([], dtype=np.float64), np.array([], dtype=np.float32)
        state["times"].extend(times_arr.tolist())
        state["values"].extend(values_arr.tolist())
        window = self._time_window_seconds()
        latest = float(times_arr[-1])
        cutoff = latest - window
        while state["times"] and state["times"][0] < cutoff:
            state["times"].popleft()
            state["values"].popleft()
        return np.asarray(state["times"], dtype=np.float64), np.asarray(state["values"], dtype=np.float32)

    def _follow_time_axis(self, curve_item, times: np.ndarray) -> None:
        """Slide the time-domain x-axis to keep the newest window in view."""
        if getattr(self, "_view_user_range_active", False):
            return
        if hasattr(self, "view_axis_enable_check") and self.view_axis_enable_check.isChecked():
            return
        if times is None or np.asarray(times).size == 0:
            return
        plot = self._plot_for_curve_item(curve_item)
        if plot is None:
            return
        window = self._time_window_seconds()
        latest = float(np.asarray(times)[-1])
        window_start = latest - window
        try:
            plot.setXRange(window_start, latest, padding=0.0)
        except Exception:
            pass


    def _render_tab3_curve(
        self,
        curve_item,
        curve_mode: str,
        times,
        values,
        expected_mode: str,
        cache_times=None,
        cache_values=None,
        cache_sample_rate=None,
    ):
        # Render one View time-domain curve only when the selected source matches this item.
        if curve_mode != expected_mode:
            if getattr(curve_item, "_tab3_has_data", False):
                curve_item.setData([], [])
                setattr(curve_item, "_tab3_has_data", False)
            self._set_curve_legend_name(curve_item, None)
            self._cache_view_curve_data(curve_item, curve_mode, [], [], sample_rate=cache_sample_rate)
            self._request_view_psd_update()
            return
        plot_times, plot_values = self._downsample_tab3_curve(times, values)
        cache_values_arr = np.asarray(plot_values if cache_values is None else cache_values)
        if cache_sample_rate is None:
            cache_times_arr = np.asarray(plot_times if cache_times is None else cache_times)
        else:
            cache_times_arr = np.asarray([], dtype=np.float64)
        if plot_values.size == 0:
            if getattr(curve_item, "_tab3_has_data", False):
                curve_item.setData([], [])
                setattr(curve_item, "_tab3_has_data", False)
            self._set_curve_legend_name(curve_item, None)
            self._cache_view_curve_data(curve_item, curve_mode, cache_times_arr, cache_values_arr, sample_rate=cache_sample_rate)
            self._request_view_psd_update()
            return
        curve_item.setData(plot_times, plot_values)
        setattr(curve_item, "_tab3_has_data", True)
        self._follow_time_axis(curve_item, plot_times)
        self._set_curve_legend_name(
            curve_item,
            self._legend_name_for_curve(self._curve_index_for_item(curve_item), curve_mode),
        )
        self._cache_view_curve_data(curve_item, curve_mode, cache_times_arr, cache_values_arr, sample_rate=cache_sample_rate)
        self._request_view_psd_update()

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

    def _update_tab2_enable_button_state(self, enabled: bool):
        """Refresh the Tab2 master control button text and style."""
        if not hasattr(self, 'tab2_enable_btn'):
            return
        self.tab2_enable_btn.blockSignals(True)
        self.tab2_enable_btn.setChecked(enabled)
        self.tab2_enable_btn.blockSignals(False)
        self.tab2_enable_btn.setText("Stop Tab2" if enabled else "Start Tab2")
        self._style_action_button(self.tab2_enable_btn, enabled, min_height=42, min_width=120, font_size=15)

    def _emit_tab2_settings_changed(self):
        """Emit a unified Tab2 settings-changed signal."""
        if hasattr(self, 'tab2_settings_changed'):
            self.tab2_settings_changed.emit()


    def _toggle_time_plot(self, enabled: bool):
        # Toggle visibility of the two View time-domain panes and preserve legacy signal delivery.
        if hasattr(self, 'time_plot_toggled'):
            self.time_plot_toggled.emit(enabled)
        if hasattr(self, 'tab3_curve1_plot'):
            self.tab3_curve1_plot.setVisible(enabled)
            self.tab3_curve2_plot.setVisible(enabled)
        self.time_plot_btn.setText("时域 ON" if enabled else "时域 OFF")
        self._style_toggle_button(self.time_plot_btn, enabled, min_width=96)


    def _toggle_psd_plot(self, enabled: bool):
        # Toggle both PSD panes and recompute when they are re-enabled.
        if hasattr(self, 'psd_plot_toggled'):
            self.psd_plot_toggled.emit(enabled)
        for plot_name in ('view_psd1_plot', 'view_psd2_plot'):
            plot = getattr(self, plot_name, None)
            if plot is not None:
                plot.setVisible(enabled)
        self.psd_plot_btn.setText("PSD ON" if enabled else "PSD OFF")
        self._style_toggle_button(self.psd_plot_btn, enabled, min_width=96)
        self._update_view_psd_curves(force=True)


    def _update_psd_settings(self):
        # Update both the legacy FIP PSD worker settings and the new View Welch PSD controls.
        window_duration_sec = self.psd_window_length_spin.value()
        current_downsample_factor = self.get_psd_downsample_factor()
        fip_settings = self.get_tab1_fip_settings() if hasattr(self, 'get_tab1_fip_settings') else {}
        original_sample_rate = float(fip_settings.get('sample_rate_hz', 1_000_000.0))
        effective_sample_rate = max(original_sample_rate, 1.0) / current_downsample_factor
        window_length_samples = int(window_duration_sec * effective_sample_rate)
        psd_settings = {
            'window_length': window_length_samples,
            'window_duration': window_duration_sec,
            'effective_sample_rate': effective_sample_rate,
            'downsample_factor': current_downsample_factor,
            'overlap_ratio': (self.psd_overlap_spin.value() / 100.0) if hasattr(self, 'psd_overlap_spin') else 0.5,
        }
        if hasattr(self, 'psd_settings_changed'):
            self.psd_settings_changed.emit(psd_settings)
        self._update_view_psd_curves(force=True)

    def _update_time_display_settings(self):
        """更新时域显示设置 - 仅支持显示时长调整，刷新节奏由后台线程控制"""
        time_settings = {
            'duration': self.time_display_duration_spin.value()
            # 注意：刷新节奏不再从UI获取
        }

        # 发送信号给主程序
        if hasattr(self, 'time_settings_changed'):
            self.time_settings_changed.emit(time_settings)

    def _update_filter_settings(self):
        """更新滤波器设置"""
        filter_settings = self.get_fip_filter_settings()

        # 发送信号给主程序
        if hasattr(self, 'filter_settings_changed'):
            self.filter_settings_changed.emit(filter_settings)


    def _button_hover_color(self, color: str, factor: float = 0.84) -> str:
        """Return a slightly darker hover color for a hex button background."""
        try:
            value = color.lstrip("#")
            red = max(0, min(255, int(int(value[0:2], 16) * factor)))
            green = max(0, min(255, int(int(value[2:4], 16) * factor)))
            blue = max(0, min(255, int(int(value[4:6], 16) * factor)))
            return f"#{red:02x}{green:02x}{blue:02x}"
        except Exception:
            return color

    def _set_button_metrics(self, button: QPushButton, min_height: int = 36, min_width: int = 92) -> None:
        """Keep command buttons visually aligned across tabs."""
        if not button:
            return
        button.setMinimumHeight(min_height)
        button.setMinimumWidth(min_width)
        button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def _style_action_button(
        self,
        button: QPushButton,
        active: bool,
        active_color: str = "#b23b3b",
        idle_color: str = "#2f6fed",
        min_height: int = 38,
        min_width: int = 104,
        font_size: int = 15,
    ) -> None:
        # Primary action buttons share size, radius, and start/stop color semantics.
        if not button:
            return
        self._set_button_metrics(button, min_height=min_height, min_width=min_width)
        color = active_color if active else idle_color
        hover_color = self._button_hover_color(color)
        qss = "\n".join([
            "QPushButton {",
            f"    font-size: {font_size}px;",
            "    padding: 8px 10px;",
            "    font-weight: 600;",
            "    color: white;",
            f"    background-color: {color};",
            "    border: none;",
            "    border-radius: 6px;",
            "}",
            f"QPushButton:hover {{ background-color: {hover_color}; }}",
            f"QPushButton:checked {{ background-color: {active_color}; }}",
            "QPushButton:disabled { background-color: #9aa0a6; }",
        ])
        button.setStyleSheet(qss)

    def _style_toggle_button(self, button: QPushButton, enabled: bool, min_width: int = 92) -> None:
        # View toggles use the same geometry as command buttons but gray out when disabled.
        self._style_action_button(
            button,
            enabled,
            active_color="#2f6fed",
            idle_color="#6f7a86",
            min_height=34,
            min_width=min_width,
            font_size=14,
        )

    def _style_secondary_button(self, button: QPushButton, min_height: int = 34, min_width: int = 88) -> None:
        # Secondary actions stay neutral so they do not compete with start/stop commands.
        if not button:
            return
        self._set_button_metrics(button, min_height=min_height, min_width=min_width)
        button.setStyleSheet("""
            QPushButton {
                font-size: 13px;
                font-weight: 600;
                padding: 7px 10px;
                color: #1f2a37;
                background-color: #eef2f7;
                border: 1px solid #c9d3df;
                border-radius: 6px;
            }
            QPushButton:hover {
                background-color: #e1e8f0;
            }
            QPushButton:pressed {
                background-color: #d5dee9;
            }
            QPushButton:disabled {
                color: #7d8793;
                background-color: #f2f4f7;
            }
        """)

    def _label_int_value(self, label: QLabel) -> int:
        # Status labels are intentionally human-readable; this helper keeps counters reusable.
        if not label:
            return 0
        match = re.search(r"-?\d+", label.text())
        return int(match.group(0)) if match else 0

    def _is_connected_text(self, label: QLabel) -> bool:
        if not label:
            return False
        text = label.text().lower()
        has_success = any(token in text for token in ("已连接", "连接", "connected", "active"))
        has_failure = any(token in text for token in ("未连接", "断开", "disconnected", "error", "错误"))
        return has_success and not has_failure

    def _refresh_comm_lights(self) -> None:
        # Gray means idle, red means started without confirmed data, green means a successful path exists.
        fip_success = self._label_int_value(getattr(self, 'data_fip_success_label', None))
        edas_success = self._label_int_value(getattr(self, 'data_edas_success_label', None))
        if hasattr(self, 'status_fip_comm_count_label'):
            self.status_fip_comm_count_label.setText(f"FIP通信: 成功 {fip_success}")
        if hasattr(self, 'status_edas_comm_count_label'):
            self.status_edas_comm_count_label.setText(f"eDAS通信: 成功 {edas_success}")
        if hasattr(self, 'data_fip_comm_light'):
            if not self.monitoring_active:
                self._set_status_light(self.data_fip_comm_light, 'gray')
            elif fip_success > 0 or self._is_connected_text(getattr(self, 'conn_status_label', None)):
                self._set_status_light(self.data_fip_comm_light, 'green')
            else:
                self._set_status_light(self.data_fip_comm_light, 'red')
        if hasattr(self, 'data_edas_comm_light'):
            if not self._edas_monitoring_active:
                self._set_status_light(self.data_edas_comm_light, 'gray')
            elif edas_success > 0 or self._is_connected_text(getattr(self, 'tab3_conn_status_label', None)):
                self._set_status_light(self.data_edas_comm_light, 'green')
            else:
                self._set_status_light(self.data_edas_comm_light, 'red')

    def _update_data_comm_buttons(self) -> None:
        # Keep all three communication buttons synchronized with controller-owned state.
        if hasattr(self, 'data_comm_both_btn'):
            both_active = self.monitoring_active and self._edas_monitoring_active and self._both_comm_requested
            self.data_comm_both_btn.blockSignals(True)
            self.data_comm_both_btn.setChecked(both_active)
            self.data_comm_both_btn.setText("停止同时通信" if both_active else "同时启动FIP+eDAS")
            self.data_comm_both_btn.blockSignals(False)
            self._style_action_button(self.data_comm_both_btn, both_active, min_height=44, min_width=132)
        if hasattr(self, 'start_stop_btn'):
            self.start_stop_btn.blockSignals(True)
            self.start_stop_btn.setChecked(self.monitoring_active)
            self.start_stop_btn.setText("停止FIP通信" if self.monitoring_active else "启动FIP通信")
            self.start_stop_btn.blockSignals(False)
            self._style_action_button(self.start_stop_btn, self.monitoring_active, min_height=44, min_width=120)
        if hasattr(self, 'tab3_start_stop_btn'):
            self.tab3_start_stop_btn.blockSignals(True)
            self.tab3_start_stop_btn.setChecked(self._edas_monitoring_active)
            self.tab3_start_stop_btn.setText("停止eDAS通信" if self._edas_monitoring_active else "启动eDAS通信")
            self.tab3_start_stop_btn.blockSignals(False)
            self._style_action_button(self.tab3_start_stop_btn, self._edas_monitoring_active, min_height=44, min_width=120)
        self._refresh_comm_lights()



    def _toggle_both_communication(self, _checked: bool = False) -> None:
        # Unified simultaneous communication start/stop used by the Data tab.
        start_both = not (self.monitoring_active and self._edas_monitoring_active)
        was_fip_active = self.monitoring_active
        was_edas_active = self._edas_monitoring_active
        self._both_comm_requested = start_both
        if start_both:
            self._reset_sync_tracking()
            self.monitoring_active = True
            self._edas_monitoring_active = True
            self._update_data_comm_buttons()
            if not was_fip_active:
                self.start_monitoring.emit()
            if not was_edas_active:
                self.tab3_start_requested.emit()
        else:
            self.monitoring_active = False
            self._edas_monitoring_active = False
            self._update_data_comm_buttons()
            if was_fip_active:
                self.stop_monitoring.emit()
            if was_edas_active:
                self.tab3_stop_requested.emit()

    def set_fip_monitoring_active(self, active: bool) -> None:
        # Controller callback for the authoritative FIP communication state.
        self.monitoring_active = bool(active)
        if not active and not self._edas_monitoring_active:
            self._both_comm_requested = False
        self._update_data_comm_buttons()

    def record_fip_comm_failure(self) -> None:
        self._fip_comm_failure_count += 1
        if hasattr(self, 'data_fip_failure_label'):
            self.data_fip_failure_label.setText(str(self._fip_comm_failure_count))
        self._refresh_comm_lights()

    def record_edas_comm_failure(self) -> None:
        self._edas_comm_failure_count += 1
        if hasattr(self, 'data_edas_failure_label'):
            self.data_edas_failure_label.setText(str(self._edas_comm_failure_count))
        self._refresh_comm_lights()

    def _reset_sync_tracking(self) -> None:
        self._sync_fip_receive_times.clear()
        self._sync_edas_receive_times.clear()
        self._sync_deltas.clear()
        self._sync_first_pair = None
        self._sync_latest_pair = None
        self._sync_delta_sum = 0.0
        self._sync_delta_count = 0
        self._sync_matched_counts.clear()
        if hasattr(self, 'data_sync_first_delta_label'):
            self.data_sync_first_delta_label.setText("-- s")
            self.data_sync_latest_delta_label.setText("-- s")
            self.data_sync_average_delta_label.setText("-- s")
            self.data_sync_pair_count_label.setText("0")

    def record_fip_packet_receive(self, comm_count: int, receive_time: float) -> None:
        try:
            key = int(comm_count)
        except (TypeError, ValueError):
            return
        self._sync_fip_receive_times[key] = float(receive_time)
        self._update_sync_pair_for_key(key)
        self._trim_sync_tracking()
        self._refresh_sync_delta_labels()
        self._refresh_comm_lights()

    def record_edas_packet_receive(self, comm_count: int, receive_time: float) -> None:
        try:
            key = int(comm_count)
        except (TypeError, ValueError):
            return
        self._sync_edas_receive_times[key] = float(receive_time)
        self._update_sync_pair_for_key(key)
        self._trim_sync_tracking()
        self._refresh_sync_delta_labels()
        self._refresh_comm_lights()

    def _trim_sync_tracking(self, limit: int = 2000) -> None:
        for mapping in (self._sync_fip_receive_times, self._sync_edas_receive_times):
            if len(mapping) <= limit:
                continue
            for key in sorted(mapping)[:len(mapping) - limit]:
                mapping.pop(key, None)

    def _update_sync_pair_for_key(self, key: int) -> None:
        if key in self._sync_matched_counts:
            return
        if key not in self._sync_fip_receive_times or key not in self._sync_edas_receive_times:
            return
        delta = self._sync_fip_receive_times[key] - self._sync_edas_receive_times[key]
        if not np.isfinite(delta):
            return
        self._sync_matched_counts.add(key)
        self._sync_deltas.append(delta)
        if len(self._sync_deltas) > 2000:
            self._sync_deltas = self._sync_deltas[-2000:]
        self._sync_delta_sum += delta
        self._sync_delta_count += 1
        if self._sync_first_pair is None or key < self._sync_first_pair[0]:
            self._sync_first_pair = (key, delta)
        if self._sync_latest_pair is None or key >= self._sync_latest_pair[0]:
            self._sync_latest_pair = (key, delta)

    def _refresh_sync_delta_labels(self) -> None:
        # Pair packets by communication count; delta direction is FIP receive time minus eDAS receive time.
        if not hasattr(self, 'data_sync_first_delta_label'):
            return
        if self._sync_delta_count <= 0:
            self.data_sync_first_delta_label.setText("-- s")
            self.data_sync_latest_delta_label.setText("-- s")
            self.data_sync_average_delta_label.setText("-- s")
            self.data_sync_pair_count_label.setText("0")
            return
        first_index, first_delta = self._sync_first_pair or (-1, 0.0)
        latest_index, latest_delta = self._sync_latest_pair or (-1, 0.0)
        average_delta = self._sync_delta_sum / max(1, self._sync_delta_count)
        self.data_sync_first_delta_label.setText(f"{first_delta:+.6f} s (序号 {first_index})")
        self.data_sync_latest_delta_label.setText(f"{latest_delta:+.6f} s (序号 {latest_index})")
        self.data_sync_average_delta_label.setText(f"{average_delta:+.6f} s")
        self.data_sync_pair_count_label.setText(str(self._sync_delta_count))

    def _storage_counter_text(self, prefix: str, success_count: int, failure_count: int) -> str:
        return f"{prefix}: 成功 {success_count} / 失败 {failure_count}"

    def _storage_light_color(self, active: bool, success_count: int, failure_count: int) -> str:
        if not active:
            return 'gray'
        if success_count > 0 and failure_count <= success_count:
            return 'green'
        return 'red'

    def _update_data_storage_buttons(self) -> None:
        # Storage indicators mirror module-level persistence, independent of plotting.
        fip_active = bool(hasattr(self, 'phase_storage_check') and self.phase_storage_check.isChecked())
        edas_active = bool(hasattr(self, 'tab3_edas_storage_toggle_btn') and self.tab3_edas_storage_toggle_btn.isChecked())
        joint_active = bool(hasattr(self, 'tab3_joint_storage_toggle_btn') and self.tab3_joint_storage_toggle_btn.isChecked())
        if hasattr(self, 'phase_storage_check'):
            self.phase_storage_check.setText("FIP存储: ON" if fip_active else "FIP存储: OFF")
            self._style_action_button(self.phase_storage_check, fip_active, min_height=44, min_width=120)
        if hasattr(self, 'tab3_edas_storage_toggle_btn'):
            self.tab3_edas_storage_toggle_btn.setText("eDAS存储: ON" if edas_active else "eDAS存储: OFF")
            self._style_action_button(self.tab3_edas_storage_toggle_btn, edas_active, min_height=44, min_width=120)
        if hasattr(self, 'tab3_joint_storage_toggle_btn'):
            self.tab3_joint_storage_toggle_btn.setText("停止同时存储" if joint_active else "同时存储FIP+eDAS")
            self._style_action_button(self.tab3_joint_storage_toggle_btn, joint_active, min_height=44, min_width=132)
        if hasattr(self, 'data_fip_storage_count_label'):
            self.data_fip_storage_count_label.setText(self._storage_counter_text("FIP存储", self._fip_storage_success_count, self._fip_storage_failure_count))
            self.data_edas_storage_count_label.setText(self._storage_counter_text("eDAS存储", self._edas_storage_success_count, self._edas_storage_failure_count))
        if hasattr(self, 'status_fip_storage_count_label'):
            self.status_fip_storage_count_label.setText(f"FIP存储: 成功 {self._fip_storage_success_count}")
        if hasattr(self, 'status_edas_storage_count_label'):
            self.status_edas_storage_count_label.setText(f"eDAS存储: 成功 {self._edas_storage_success_count}")
        if hasattr(self, 'data_fip_storage_light'):
            self._set_status_light(self.data_fip_storage_light, self._storage_light_color(fip_active or joint_active, self._fip_storage_success_count, self._fip_storage_failure_count))
        if hasattr(self, 'data_edas_storage_light'):
            self._set_status_light(self.data_edas_storage_light, self._storage_light_color(edas_active or joint_active, self._edas_storage_success_count, self._edas_storage_failure_count))

    def record_fip_storage_failure(self) -> None:
        self._fip_storage_failure_count += 1
        self._update_data_storage_buttons()

    def record_edas_storage_failure(self) -> None:
        self._edas_storage_failure_count += 1
        self._update_data_storage_buttons()

    def _apply_view_refresh_settings(self) -> None:
        # The visual refresh controls tune only the UI update cadence, not packet acquisition.
        if hasattr(self, 'view_fip_refresh_spin'):
            self._tab3_fip_plot_min_interval_seconds = max(0.2, float(self.view_fip_refresh_spin.value()))
        if hasattr(self, 'view_edas_refresh_spin'):
            self._tab3_das_plot_min_interval_seconds = max(0.5, float(self.view_edas_refresh_spin.value()))
        if hasattr(self, 'view_curve_max_points_spin'):
            self._tab3_curve_max_points = min(50000, max(1000, int(self.view_curve_max_points_spin.value())))

    def _curve_index_for_item(self, curve_item) -> int:
        if curve_item in (getattr(self, 'tab3_curve1_das_curve', None), getattr(self, 'tab3_curve1_fip_curve', None)):
            return 1
        if curve_item in (getattr(self, 'tab3_curve2_das_curve', None), getattr(self, 'tab3_curve2_fip_curve', None)):
            return 2
        return 0

    def _cache_view_curve_data(self, curve_item, source_name: str, times: np.ndarray, values: np.ndarray, sample_rate: Optional[float] = None) -> None:
        curve_index = self._curve_index_for_item(curve_item)
        if curve_index not in (1, 2):
            return
        if values is None or len(values) < 2:
            self._view_curve_cache[curve_index] = {'source': source_name, 'times': np.asarray([]), 'values': np.asarray([]), 'sample_rate': sample_rate}
            return
        # 已知采样率时不再存储全量时间轴（FIP 每包 1M 点，全量时间轴数组
        # 每包分配 + 拷贝约 16 MB，是时域图慢帧的主要来源），仅保留采样率。
        stored_times = np.asarray([], dtype=np.float64) if sample_rate is not None else np.asarray(times, dtype=float)
        self._view_curve_cache[curve_index] = {
            'source': str(source_name),
            'times': stored_times,
            'values': np.asarray(values, dtype=float).copy(),
            'sample_rate': sample_rate,
        }

    def _estimate_sample_rate_from_times(self, times: np.ndarray) -> float:
        times = np.asarray(times, dtype=float)
        if times.size < 2:
            return 1.0
        diffs = np.diff(times)
        diffs = diffs[np.isfinite(diffs) & (diffs > 0)]
        if diffs.size == 0:
            return 1.0
        return max(1e-9, 1.0 / float(np.median(diffs)))

    def _compute_view_welch_psd(self, times: np.ndarray, values: np.ndarray, sample_rate: Optional[float] = None) -> Tuple[np.ndarray, np.ndarray]:
        values = np.asarray(values, dtype=float)
        finite_mask = np.isfinite(values)
        if sample_rate is None or not np.isfinite(sample_rate) or sample_rate <= 0:
            times = np.asarray(times, dtype=float)
            if times.size == values.size:
                finite_mask &= np.isfinite(times)
            values = values[finite_mask]
            times = times[finite_mask] if times.size == finite_mask.size else np.arange(values.size, dtype=float)
            if values.size < 4:
                return np.asarray([]), np.asarray([])
            sample_rate = self._estimate_sample_rate_from_times(times)
        else:
            values = values[finite_mask]
            if values.size < 4:
                return np.asarray([]), np.asarray([])
        window_seconds = float(self.psd_window_length_spin.value()) if hasattr(self, 'psd_window_length_spin') else 1.0
        overlap_ratio = (float(self.psd_overlap_spin.value()) / 100.0) if hasattr(self, 'psd_overlap_spin') else 0.5
        nperseg = int(max(8, min(values.size, 50000, round(sample_rate * window_seconds))))
        noverlap = int(min(nperseg - 1, max(0, round(nperseg * overlap_ratio))))
        if nperseg > values.size:
            nperseg = values.size
            noverlap = min(noverlap, nperseg - 1)
        if nperseg < 4:
            return np.asarray([]), np.asarray([])
        freq, power = signal.welch(values, fs=sample_rate, nperseg=nperseg, noverlap=noverlap, detrend='constant')
        mask = np.isfinite(freq) & np.isfinite(power) & (freq > 0)
        return freq[mask], 10.0 * np.log10(power[mask] + np.finfo(float).eps)

    def _request_view_psd_update(self) -> None:
        if getattr(self, "_view_psd_update_pending", False):
            return
        self._view_psd_update_pending = True
        QTimer.singleShot(80, self._run_deferred_view_psd_update)

    def _run_deferred_view_psd_update(self) -> None:
        self._view_psd_update_pending = False
        self._update_view_psd_curves()

    def _update_view_psd_curves(self, force: bool = False) -> None:
        if not hasattr(self, 'view_psd1_curve'):
            return
        if hasattr(self, 'psd_plot_btn') and not self.psd_plot_btn.isChecked():
            self.view_psd1_curve.setData([], [])
            self.view_psd2_curve.setData([], [])
            return
        now = time.monotonic()
        if not force and now - self._view_last_psd_update_monotonic < self._view_psd_update_interval_seconds:
            return
        self._view_last_psd_update_monotonic = now
        for curve_index, checkbox_name, curve_name in ((1, 'view_psd1_check', 'view_psd1_curve'), (2, 'view_psd2_check', 'view_psd2_curve')):
            checkbox = getattr(self, checkbox_name, None)
            plot_curve = getattr(self, curve_name, None)
            if checkbox is not None and not checkbox.isChecked():
                plot_curve.setData([], [])
                continue
            cached = self._view_curve_cache.get(curve_index, {})
            freq, psd_db = self._compute_view_welch_psd(
                cached.get('times', np.asarray([])),
                cached.get('values', np.asarray([])),
                cached.get('sample_rate'),
            )
            plot_curve.setData(freq, psd_db)
        self._apply_view_axes()

    def _apply_view_axes(self) -> None:
        if not hasattr(self, 'view_axis_enable_check') or not self.view_axis_enable_check.isChecked():
            return
        x_min, x_max = self.get_view_x_range()
        y_min, y_max = self.get_view_y_range()
        psd_y_min, psd_y_max = self.get_view_psd_y_range()
        if x_max > x_min and y_max > y_min:
            for plot in (getattr(self, 'tab3_curve1_plot', None), getattr(self, 'tab3_curve2_plot', None)):
                if plot:
                    plot.setRange(xRange=(x_min, x_max), yRange=(y_min, y_max), padding=0.0)
        if psd_y_max > psd_y_min:
            for plot in (getattr(self, 'view_psd1_plot', None), getattr(self, 'view_psd2_plot', None)):
                if plot:
                    plot.setYRange(psd_y_min, psd_y_max, padding=0.0)

    def _reset_view_axes(self) -> None:
        if hasattr(self, 'view_axis_enable_check'):
            self.view_axis_enable_check.setChecked(False)
        self._view_user_range_active = False
        for plot in self._iter_plot_widgets():
            plot.enableAutoRange(axis=pg.ViewBox.XYAxes, enable=True)
            plot.autoRange()

    def _iter_plot_widgets(self) -> List[pg.PlotWidget]:
        names = (
            'tab3_curve1_plot', 'tab3_curve2_plot', 'view_psd1_plot', 'view_psd2_plot',
            'tab3_space_time_plot', 'original_plot', 'processed_plot', 'correlation_plot', 'detection_plot',
        )
        return [getattr(self, name) for name in names if hasattr(self, name) and getattr(self, name) is not None]

    def _apply_plot_font_settings(self) -> None:
        if not hasattr(self, 'setting_axis_label_font_spin'):
            return
        title_size = int(self.setting_plot_title_font_spin.value())
        axis_size = int(self.setting_axis_label_font_spin.value())
        tick_size = int(self.setting_tick_font_spin.value())
        tick_font = QFont()
        tick_font.setPointSize(tick_size)
        for plot in self._iter_plot_widgets():
            item = plot.getPlotItem()
            try:
                if item.titleLabel.text:
                    item.titleLabel.item.setFont(QFont('', title_size))
            except Exception:
                pass
            for axis_name in ('left', 'bottom', 'right', 'top'):
                axis = item.getAxis(axis_name)
                axis.setStyle(tickFont=tick_font)
                try:
                    axis.label.setFont(QFont('', axis_size))
                except Exception:
                    pass
        histogram = getattr(self, "tab3_space_time_histogram", None)
        histogram_item = getattr(histogram, "item", None)
        histogram_axis = getattr(histogram_item, "axis", None)
        if histogram_axis is not None:
            try:
                histogram_axis.setStyle(tickFont=tick_font)
            except Exception:
                pass

    def _apply_global_display_runtime_settings(self) -> None:
        if hasattr(self, 'setting_gui_font_spin'):
            gui_font = QFont()
            gui_font.setPointSize(int(self.setting_gui_font_spin.value()))
            self.setFont(gui_font)
            app = QApplication.instance()
            if app:
                app.setFont(gui_font)
        self._apply_global_control_metrics()
        self._apply_plot_font_settings()
        self._apply_view_axes()

    def _apply_global_control_metrics(self) -> None:
        """Apply control heights and layout spacing that match the active GUI font."""
        try:
            font_pt = int(self.setting_gui_font_spin.value()) if hasattr(self, 'setting_gui_font_spin') else 8
        except Exception:
            font_pt = 8
        control_height = max(26, int(round(font_pt * 2.0)))
        label_height = max(22, int(round(font_pt * 1.7)))
        button_height = max(34, int(round(font_pt * 2.4)))
        spacing = max(6, int(round(font_pt * 0.55)))

        for widget_class in (QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit):
            for widget in self.findChildren(widget_class):
                widget.setMinimumHeight(control_height)
                widget.setSizePolicy(widget.sizePolicy().horizontalPolicy(), QSizePolicy.Fixed)
        for checkbox in self.findChildren(QCheckBox):
            checkbox.setMinimumHeight(label_height)
        for button in self.findChildren(QPushButton):
            if button.minimumHeight() < button_height:
                button.setMinimumHeight(button_height)
        for label in self.findChildren(QLabel):
            label.setMinimumHeight(label_height)
            label.setWordWrap(False)

        self._apply_layout_spacing(getattr(self.centralWidget(), "layout", lambda: None)(), spacing)

    def _apply_layout_spacing(self, layout, spacing: int) -> None:
        if layout is None:
            return
        try:
            layout.setSpacing(max(layout.spacing(), spacing))
        except Exception:
            pass
        for attr in ("setVerticalSpacing", "setHorizontalSpacing"):
            if hasattr(layout, attr):
                try:
                    getattr(layout, attr)(spacing)
                except Exception:
                    pass
        for index in range(layout.count()):
            item = layout.itemAt(index)
            child_layout = item.layout()
            if child_layout is not None:
                self._apply_layout_spacing(child_layout, spacing)
                continue
            child_widget = item.widget()
            if child_widget is not None and child_widget.layout() is not None:
                self._apply_layout_spacing(child_widget.layout(), spacing)

    def _save_global_settings_for_restart(self) -> None:
        self._apply_global_display_runtime_settings()
        self._write_persisted_configuration(self.get_current_config())
        self.status_bar.showMessage("全局显示设置已应用并保存", 5000)

    def _init_status_bar(self):
        """初始化状态栏，含线程健康统计面板（X-01）。"""
        self.status_bar = QStatusBar()
        self.status_bar.setMinimumHeight(30)
        self.setStatusBar(self.status_bar)

        for indicator in (
            self._create_status_bar_indicator("FIP通信", "data_fip_comm_light", "status_fip_comm_count_label"),
            self._create_status_bar_indicator("eDAS通信", "data_edas_comm_light", "status_edas_comm_count_label"),
            self._create_status_bar_indicator("FIP存储", "data_fip_storage_light", "status_fip_storage_count_label"),
            self._create_status_bar_indicator("eDAS存储", "data_edas_storage_light", "status_edas_storage_count_label"),
        ):
            self.status_bar.addWidget(indicator)

        # 线程健康统计标签（X-01）：展示存储队列积压、丢包数、缺口数等关键指标
        # 由 main.py 的 QTimer 每 2 s 调用 update_thread_stats() 刷新
        self.thread_stats_label = QLabel("线程: 等待启动")
        self.thread_stats_label.setStyleSheet("color: #444; font-size: 8pt; padding: 0 8px;")
        self.status_bar.addPermanentWidget(self.thread_stats_label)

        # 添加软件版本信息到右侧，包含研究所名称
        version_label = QLabel("PCCP v1.0 | 中国科学院半导体研究所")
        version_label.setToolTip("融合型光纤PCCP断丝监测软件 v1.0 - 中国科学院半导体研究所")
        version_label.setStyleSheet("color: #666; font-size: 8pt;")
        self.status_bar.addPermanentWidget(version_label)
        self._refresh_comm_lights()
        self._update_data_storage_buttons()

    def update_thread_stats(self, stats: dict) -> None:
        """更新状态栏中的线程健康统计信息（X-01）。

        Args:
            stats: OptimizedTab1ThreadManager.get_thread_stats() 的返回值，
                   包含 processing（DataProcessingThread.stats）和
                   storage（DataStorageThread.stats）两个子字典。
        """
        proc = stats.get("processing", {})
        stor = stats.get("storage", {})
        proc_drop = proc.get("queue_drop_count", 0)
        proc_gap = proc.get("gap_count", 0)
        stor_queue = stor.get("raw_queue_current_size", stor.get("raw_queue_peak", 0))
        stor_fail = stor.get("storage_failure_count", 0)
        stor_saved = stor.get("saved_file_count", 0)
        text = (
            f"绘图丢包:{proc_drop} 缺包:{proc_gap} "
            f"存储队列:{stor_queue} 存储失败:{stor_fail} 文件:{stor_saved}"
        )
        # 存储失败时用红色高亮提醒
        color = "#c00" if stor_fail > 0 or proc_drop > 50 else "#444"
        self.thread_stats_label.setStyleSheet(
            f"color: {color}; font-size: 8pt; padding: 0 8px;"
        )
        self.thread_stats_label.setText(f"线程: {text}")

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
            if hasattr(self, 'psd_overlap_spin'):
                self.psd_overlap_spin.valueChanged.connect(self._update_psd_settings)
            if hasattr(self, 'view_psd1_check'):
                self.view_psd1_check.toggled.connect(lambda _checked: self._update_view_psd_curves(force=True))
            if hasattr(self, 'view_psd2_check'):
                self.view_psd2_check.toggled.connect(lambda _checked: self._update_view_psd_curves(force=True))
            if hasattr(self, 'view_fip_refresh_spin'):
                self.view_fip_refresh_spin.valueChanged.connect(lambda _value: self._apply_view_refresh_settings())
            if hasattr(self, 'view_edas_refresh_spin'):
                self.view_edas_refresh_spin.valueChanged.connect(lambda _value: self._apply_view_refresh_settings())
            if hasattr(self, 'view_curve_max_points_spin'):
                self.view_curve_max_points_spin.valueChanged.connect(lambda _value: self._apply_view_refresh_settings())
            if hasattr(self, 'view_apply_axis_btn'):
                self.view_apply_axis_btn.clicked.connect(self._apply_view_axes)
            if hasattr(self, 'view_auto_axis_btn'):
                self.view_auto_axis_btn.clicked.connect(self._reset_view_axes)
            if hasattr(self, 'setting_apply_btn'):
                self.setting_apply_btn.clicked.connect(self._save_global_settings_for_restart)
            for setting_spin in (
                getattr(self, 'setting_gui_font_spin', None),
                getattr(self, 'setting_plot_title_font_spin', None),
                getattr(self, 'setting_axis_label_font_spin', None),
                getattr(self, 'setting_tick_font_spin', None),
            ):
                if setting_spin is not None:
                    setting_spin.valueChanged.connect(lambda _value: self._apply_global_display_runtime_settings())
            if hasattr(self, 'setting_psd_downsample_spin'):
                self.setting_psd_downsample_spin.valueChanged.connect(self._update_psd_settings)
            if hasattr(self, 'data_comm_both_btn'):
                self.data_comm_both_btn.clicked.connect(self._toggle_both_communication)
            if hasattr(self, 'phase_storage_check'):
                self.phase_storage_check.toggled.connect(lambda _checked: self._update_data_storage_buttons())

            # 时域显示参数变化信号连接
            if hasattr(self, 'time_display_duration_spin'):
                self.time_display_duration_spin.valueChanged.connect(self._update_time_display_settings)

            # 滤波参数变化信号连接
            if hasattr(self, 'fip_filter_enable_check'):
                self.fip_filter_enable_check.toggled.connect(self._update_filter_settings)
            if hasattr(self, 'fip_filter_range_edit'):
                self.fip_filter_range_edit.editingFinished.connect(self._update_filter_settings)
                self.fip_filter_range_edit.returnPressed.connect(self._update_filter_settings)
            if hasattr(self, 'filter_order_spin'):
                self.filter_order_spin.valueChanged.connect(self._update_filter_settings)

            # 降采样参数变化时，也需要更新PSD设置（因为PSD计算依赖采样率）
            if hasattr(self, 'fip_phase_unwrap_check'):
                self.fip_phase_unwrap_check.toggled.connect(self._on_fip_phase_unwrap_changed)

            if hasattr(self, 'fip_packet_duration_spin'):
                self.fip_packet_duration_spin.valueChanged.connect(self._on_fip_input_settings_changed)
            if hasattr(self, 'fip_sample_rate_mhz_spin'):
                self.fip_sample_rate_mhz_spin.valueChanged.connect(self._on_fip_input_settings_changed)
            if hasattr(self, 'fip_sensor_count_combo'):
                self.fip_sensor_count_combo.currentIndexChanged.connect(self._on_fip_sensor_count_changed)
            if hasattr(self, 'fip_plot_sensor_combo'):
                self.fip_plot_sensor_combo.currentIndexChanged.connect(self._on_fip_plot_sensor_changed)

            if hasattr(self, 'tab2_enable_btn'):
                self.tab2_enable_btn.toggled.connect(self._update_tab2_enable_button_state)
                self.tab2_enable_btn.toggled.connect(self._emit_tab2_settings_changed)

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
            if hasattr(self, 'tab3_joint_storage_toggle_btn'):
                self.tab3_joint_storage_toggle_btn.toggled.connect(self._update_tab3_storage_button_state)
                self.tab3_joint_storage_toggle_btn.toggled.connect(self._emit_tab3_settings_changed)
            if hasattr(self, 'tab3_edas_storage_toggle_btn'):
                self.tab3_edas_storage_toggle_btn.toggled.connect(self._update_tab3_edas_storage_button_state)
                self.tab3_edas_storage_toggle_btn.toggled.connect(self._emit_tab3_settings_changed)
            if hasattr(self, 'tab3_colormap_combo'):
                self.tab3_colormap_combo.currentTextChanged.connect(self._on_tab3_colormap_changed)
            if hasattr(self, 'tab3_v_range_edit'):
                self.tab3_v_range_edit.editingFinished.connect(self._on_tab3_v_range_changed)
                self.tab3_v_range_edit.returnPressed.connect(self._on_tab3_v_range_changed)

            tab3_widgets = [
                getattr(self, 'tab3_ip_edit', None),
                getattr(self, 'tab3_port_spin', None),
                getattr(self, 'tab3_curve1_combo', None),
                getattr(self, 'tab3_curve2_combo', None),
                getattr(self, 'tab3_curve1_das_channel_spin', None),
                getattr(self, 'tab3_curve2_das_channel_spin', None),
                getattr(self, 'tab3_das_filter_enable_check', None),
                getattr(self, 'tab3_das_filter_range_edit', None),
                getattr(self, 'tab3_das_filter_order_spin', None),
                getattr(self, 'tab3_channel_range_edit', None),
                getattr(self, 'tab3_space_time_total_seconds_spin', None),
                getattr(self, 'tab3_space_time_shift_seconds_spin', None),
                getattr(self, 'tab3_time_downsample_spin', None),
                getattr(self, 'tab3_space_downsample_spin', None),
                getattr(self, 'tab3_v_range_edit', None),
                getattr(self, 'tab3_storage_path_edit', None),
                getattr(self, 'tab3_storage_interval_spin', None),
                getattr(self, 'tab3_cache_seconds_spin', None),
                getattr(self, 'tab3_edas_storage_path_edit', None),
                getattr(self, 'tab3_edas_blocks_per_file_spin', None),
                getattr(self, 'tab3_edas_queue_packets_spin', None),
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
        # Controller callback for the authoritative eDAS communication state.
        self._update_tab3_monitor_button_state(bool(active))

    def _emit_tab3_settings_changed(self):
        """Emit a unified Tab3 settings-changed signal."""
        if hasattr(self, 'tab3_settings_changed'):
            self.tab3_settings_changed.emit()
