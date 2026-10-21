"""
`src/main_window.py` 是当前工程的业务编排中心，也是整个上位机最重要的模块。

这个文件不只是“画界面”。最新版本里，它同时承担参数采集与校验、设备初始化、采集线程启停、最新显示快照消费、频谱分析触发、Time-Space 控件协同、异步落盘、Tab3 TCP 发送、系统状态监控、自动恢复与本地参数持久化等职责，因此它本质上是桌面实时采集应用的总控层。

当前代码特别值得后续项目参考的点有三项：完整采集数据与 GUI 显示数据已经拆成两条链路；相位转弧度只发生在显示侧，落盘仍保存原始 `int32`；STOP、自动恢复和旧线程延迟信号的处理都围绕“不要误伤下一轮采集”来设计。
"""
import sys
import os
import json
import time
import numpy as np
import psutil  # For CPU and disk monitoring
import shutil  # For disk space monitoring
from dataclasses import asdict, fields, is_dataclass
from pathlib import Path
from typing import Any, Dict, Optional
from PyQt5.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QGroupBox, QLabel, QLineEdit, QComboBox, QPushButton, QCheckBox,
    QRadioButton, QButtonGroup, QSpinBox, QDoubleSpinBox, QFileDialog,
    QMessageBox, QStatusBar, QSplitter, QFrame, QSizePolicy, QProgressBar,
    QTabWidget
)
from PyQt5.QtCore import Qt, QTimer, pyqtSlot
from PyQt5.QtGui import QFont, QColor, QPalette, QPixmap, QFontDatabase, QIcon
import pyqtgraph as pg

from .config import (
    AllParams, BasicParams, UploadParams, PhaseDemodParams, DisplayParams, SaveParams,
    ClockSource, TriggerDirection, DataSource, DisplayMode,
    CHANNEL_NUM_OPTIONS, DATA_SOURCE_OPTIONS, DATA_RATE_OPTIONS, RATE2PHASE_OPTIONS,
    validate_point_num, calculate_fiber_length, calculate_data_rate_mbps,
    calculate_phase_point_num, calculate_cropped_point_count,
    OPTIMIZED_BUFFER_SIZES, MONITOR_UPDATE_INTERVALS,
    STORAGE_FORMAT_BIN, STORAGE_FORMAT_BITSHUFFLE_ZSTD, STORAGE_FORMAT_OPTIONS
)
from .pcie7821_api import PCIe7821API, PCIe7821Error
from .acquisition_thread import AcquisitionThread, SimulatedAcquisitionThread
from .data_saver import (
    BitshuffleZstdFileSaver,
    BlockBasedFileSaver,
    calculate_storage_queue_capacities,
)
from .spectrum_analyzer import RealTimeSpectrumAnalyzer
from .realtime_filter import FilterSpecError, RealtimeTimeAxisFilter, parse_filter_spec
from .time_space_plot import create_time_space_widget
from .shared_stream import SharedStreamManager as TCPTab3Manager
from .logger import get_logger
from .plot_interaction import ZoomablePlotViewBox

# Module logger
log = get_logger("gui")

ACQ_STALL_TIMEOUT_S = 8.0
ACQ_RECOVERY_COOLDOWN_S = 20.0
APP_DISPLAY_VERSION = "eDAS-pt1g-gh-26.6.6"


def get_bundle_root() -> Path:
    """Return the runtime bundle root for source and frozen builds."""
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parents[2]


def get_logo_path() -> Path:
    """Return the preferred window/taskbar icon path."""
    bundle_root = get_bundle_root()
    primary = bundle_root / "resources" / "eDAS-LOGO.png"
    if primary.exists():
        return primary
    return bundle_root / "resources" / "logo.png"


def get_header_logo_path() -> Path:
    """Return the preferred page header logo path."""
    bundle_root = get_bundle_root()
    primary = bundle_root / "resources" / "logo.png"
    if primary.exists():
        return primary
    return bundle_root / "resources" / "eDAS-LOGO.png"


# ----- MAIN APPLICATION WINDOW -----

class MainWindow(QMainWindow):
    """Main application window"""

    def __init__(self, simulation_mode: bool = False):
        """
        Initialize main window.

        Args:
            simulation_mode: If True, use simulated data without hardware
        """
        super().__init__()
        log.info(f"MainWindow initializing (simulation_mode={simulation_mode})")
        self.simulation_mode = simulation_mode

        # Initialize components
        self.api: Optional[PCIe7821API] = None
        self.acq_thread: Optional[AcquisitionThread] = None
        self.data_saver: Optional[Any] = None
        self.spectrum_analyzer = RealTimeSpectrumAnalyzer()
        self._tab1_phase_filter = RealtimeTimeAxisFilter(order=2)
        self._tab1_phase_filter_signature = None
        self._tab1_phase_filter_error_text = ""
        self._filter_enabled = False
        self._filter_spec_text = "1-"
        self._filter_error_text = ""
        self.time_space_widget = None
        self.tcp_tab3_manager = TCPTab3Manager()
        self._interactive_plot_widgets: Dict[str, pg.PlotWidget] = {}
        self._plot_zoom_locked: Dict[str, bool] = {}
        self._time_plot_axis_kind: Optional[str] = None
        self._time_plot_pending_auto_range = False
        self._time_plot_auto_range_frames_remaining = 0
        self._settings_path = self._get_settings_path()

        # Parameters
        self.params = AllParams()

        # Data storage for display
        self._phase_data_buffer = []
        self._raw_data_buffer = []
        self._current_monitor_data = None

        # Performance tracking
        self._last_data_time = 0
        self._last_phase_callback_at = 0.0
        self._last_gui_interval_ms = 0.0
        self._max_gui_interval_ms = 0.0
        self._data_count = 0
        self._gui_update_count = 0
        self._raw_data_count = 0  # Counter for raw data callbacks
        self._last_raw_display_time = 0  # Last raw display update timestamp
        self._last_storage_queue_log_time = 0.0
        self._last_acq_snapshot_log_time = 0.0
        self._recovery_in_progress = False
        self._last_recovery_time = 0.0
        self._fatal_acq_error_stop_pending = False
        self._full_data_count = 0
        self._save_file_count_this_run = 0
        self._last_save_enqueue_ms = 0.0
        self._max_save_enqueue_ms = 0.0
        self._last_tcp_enqueue_ms = 0.0
        self._max_tcp_enqueue_ms = 0.0
        self._tcp_settings_snapshot: Dict[str, Any] = {}

        # System monitoring
        self._last_system_update = 0
        self._cpu_percent = 0.0
        self._disk_free_gb = 0.0

        # Setup UI
        self.setWindowTitle(APP_DISPLAY_VERSION)
        icon_path = get_logo_path()
        if icon_path.exists():
            self.setWindowIcon(QIcon(str(icon_path)))
        self.setMinimumSize(1400, 950)  # Slightly increased height to accommodate all content

        log.debug("Setting up UI...")
        self._setup_ui()
        self._setup_plots()
        self._connect_signals()
        self._connect_tcp_tab3_manager()
        self._load_local_params()
        self._tcp_settings_snapshot = self.get_tab3_comm_settings()
        self._sync_tcp_tab3_availability()
        self._update_phase_crop_controls()

        # Status timers
        self._status_timer = QTimer(self)
        self._status_timer.timeout.connect(self._update_status)
        self._status_timer.start(MONITOR_UPDATE_INTERVALS['buffer_status_ms'])

        # Display snapshots normally wake the GUI by signal. This timer is only a watchdog.
        self._display_timer = QTimer(self)
        self._display_timer.timeout.connect(self._drain_latest_display_data)
        self._display_timer_interval_ms = 0
        self._configure_display_timer(self.params)

        # System monitoring timer (slower update)
        self._system_timer = QTimer(self)
        self._system_timer.timeout.connect(self._update_system_status)
        self._system_timer.start(MONITOR_UPDATE_INTERVALS['system_status_s'] * 1000)

        # Initialize system monitoring
        self._last_system_update = 0
        self._cpu_percent = 0.0
        self._disk_free_gb = 0.0

        # Initialize psutil CPU monitoring (first call to establish baseline)
        try:
            psutil.cpu_percent(interval=None)  # Initialize CPU monitoring
        except Exception as e:
            log.warning(f"Failed to initialize CPU monitoring: {e}")

        # Initialize file estimates
        self._update_file_estimates()

        # Initialize device
        if not simulation_mode:
            self._init_device()
        else:
            self._update_device_status(True)

        log.info("MainWindow initialized")

    # ----- UI LAYOUT AND WIDGETS -----

    def _setup_ui(self):
        """Setup the user interface"""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)

        # Main vertical layout
        main_vertical_layout = QVBoxLayout(central_widget)
        main_vertical_layout.setContentsMargins(10, 10, 10, 10)

        # Header with logo and title
        header_widget = self._create_header()
        main_vertical_layout.addWidget(header_widget)

        # Content area (horizontal splitter)
        content_layout = QHBoxLayout()

        # Left panel - Parameters (two-column layout needs more width)
        left_panel = self._create_parameter_panel()
        left_panel.setMaximumWidth(380)
        left_panel.setMinimumWidth(340)

        # Right panel - Plots and controls
        right_panel = self._create_plot_panel()

        # Add to splitter
        splitter = QSplitter(Qt.Horizontal)
        splitter.addWidget(left_panel)
        splitter.addWidget(right_panel)
        splitter.setSizes([360, 1040])

        main_vertical_layout.addWidget(splitter)

        # Status bar
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self._device_status_label = QLabel("Device: Disconnected")
        self._data_rate_label = QLabel("Data Rate: 0 MB/s")
        self._fiber_length_label = QLabel("Fiber Length: 0 m")
        self._point_num_label = QLabel("Point num: 0")  # Added point num display
        self._institute_label = QLabel("中国科学院半导体研究所")
        self._institute_label.setFont(QFont("等线", 10))

        # Add separators between status items
        self.statusBar.addWidget(self._device_status_label)
        self.statusBar.addPermanentWidget(QLabel("  |  "))  # Separator
        self.statusBar.addWidget(self._data_rate_label)
        self.statusBar.addPermanentWidget(QLabel("  |  "))  # Separator
        self.statusBar.addWidget(self._fiber_length_label)
        self.statusBar.addPermanentWidget(QLabel("  |  "))  # Separator
        self.statusBar.addWidget(self._point_num_label)
        self.statusBar.addPermanentWidget(self._institute_label)

    def _create_header(self) -> QWidget:
        """Create header with logo and title"""
        header = QFrame()
        header.setFrameStyle(QFrame.StyledPanel)
        header.setFixedHeight(50)

        layout = QHBoxLayout(header)
        layout.setContentsMargins(10, 3, 10, 3)

        # Logo
        logo_label = QLabel()
        logo_path = get_header_logo_path()
        if logo_path.exists():
            pixmap = QPixmap(str(logo_path))
            # Scale logo to fit header height
            scaled_pixmap = pixmap.scaledToHeight(40, Qt.SmoothTransformation)
            logo_label.setPixmap(scaled_pixmap)
        else:
            logo_label.setText("[LOGO]")
            log.warning(f"Logo file not found: {logo_path}")

        layout.addWidget(logo_label)

        # Title - Arial bold 28pt
        title_label = QLabel("Enhanced Distributed Acoustic Sensing (eDAS)")
        title_font = QFont("Arial", 28, QFont.Bold)
        title_label.setFont(title_font)
        title_label.setAlignment(Qt.AlignCenter)

        layout.addWidget(title_label, 1)  # stretch factor 1 to center
        layout.addStretch()

        return header

    def _create_parameter_panel(self) -> QWidget:
        """Create the parameter configuration panel"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(6)
        layout.setContentsMargins(5, 5, 5, 5)

        # Minimum height for input widgets
        INPUT_MIN_HEIGHT = 22
        INPUT_MAX_WIDTH = 80

        # Apply stylesheet for fonts - Times New Roman for English text, SimHei for Chinese
        panel.setStyleSheet("""
            QGroupBox {
                font-family: 'Arial';
                font-size: 12px;
                font-weight: bold;
            }
            QLabel {
                font-family: 'Times New Roman', 'SimHei';
                font-size: 11px;
            }
            QSpinBox, QDoubleSpinBox, QComboBox, QLineEdit {
                font-family: 'Times New Roman';
                font-size: 11px;
                max-height: 22px;
            }
            QComboBox {
                max-width: 85px;
            }
            QRadioButton, QCheckBox {
                font-family: 'Times New Roman', 'SimHei';
                font-size: 10px;
            }
            QPushButton {
                font-family: 'Times New Roman', 'SimHei';
                font-size: 12px;
            }
        """)

        # Basic Parameters Group - keep high-frequency field controls on Tab1.
        basic_group = QGroupBox("Basic Parameters")
        basic_layout = QGridLayout(basic_group)
        basic_layout.setSpacing(4)
        basic_layout.setContentsMargins(8, 12, 8, 8)

        basic_layout.addWidget(QLabel("Scan(Hz):"), 0, 0)
        self.scan_rate_spin = QSpinBox()
        self.scan_rate_spin.setRange(1, 1000000)
        self.scan_rate_spin.setValue(2000)
        self.scan_rate_spin.setMinimumHeight(INPUT_MIN_HEIGHT)
        self.scan_rate_spin.setMaximumWidth(INPUT_MAX_WIDTH)
        basic_layout.addWidget(self.scan_rate_spin, 0, 1)

        basic_layout.addWidget(QLabel("Pulse(ns):"), 0, 2)
        self.pulse_width_spin = QSpinBox()
        self.pulse_width_spin.setRange(10, 1000000)
        self.pulse_width_spin.setValue(100)
        self.pulse_width_spin.setMinimumHeight(INPUT_MIN_HEIGHT)
        self.pulse_width_spin.setMaximumWidth(INPUT_MAX_WIDTH)
        basic_layout.addWidget(self.pulse_width_spin, 0, 3)

        basic_layout.addWidget(QLabel("Points:"), 1, 0)
        self.point_num_spin = QSpinBox()
        self.point_num_spin.setRange(512, 10000000)
        self.point_num_spin.setValue(20480)
        self.point_num_spin.setSingleStep(512)
        self.point_num_spin.setMinimumHeight(INPUT_MIN_HEIGHT)
        self.point_num_spin.setMaximumWidth(INPUT_MAX_WIDTH)
        basic_layout.addWidget(self.point_num_spin, 1, 1)

        layout.addWidget(basic_group)

        # Upload Parameters Group - primary data stream selection.
        upload_group = QGroupBox("Upload Parameters")
        upload_layout = QGridLayout(upload_group)
        upload_layout.setSpacing(4)
        upload_layout.setContentsMargins(8, 12, 8, 8)

        upload_layout.addWidget(QLabel("Channels:"), 0, 0)
        self.channel_combo = QComboBox()
        for label, value in CHANNEL_NUM_OPTIONS:
            self.channel_combo.addItem(label, value)
        self.channel_combo.setMinimumHeight(INPUT_MIN_HEIGHT)
        upload_layout.addWidget(self.channel_combo, 0, 1)

        upload_layout.addWidget(QLabel("Source:"), 0, 2)
        self.data_source_combo = QComboBox()
        for label, value in DATA_SOURCE_OPTIONS:
            self.data_source_combo.addItem(label, value)
        self.data_source_combo.setCurrentIndex(3)  # Default to Phase
        self.data_source_combo.setMinimumHeight(INPUT_MIN_HEIGHT)
        upload_layout.addWidget(self.data_source_combo, 0, 3)

        layout.addWidget(upload_group)

        # Phase Demodulation Parameters Group - algorithm parameters used frequently in the field.
        phase_group = QGroupBox("Phase Demod Parameters")
        phase_layout = QGridLayout(phase_group)
        phase_layout.setSpacing(4)
        phase_layout.setContentsMargins(8, 12, 8, 8)

        phase_layout.addWidget(QLabel("SpaceAvg:"), 0, 0)
        self.space_avg_spin = QSpinBox()
        self.space_avg_spin.setRange(1, 64)
        self.space_avg_spin.setValue(25)
        self.space_avg_spin.setMinimumHeight(INPUT_MIN_HEIGHT)
        self.space_avg_spin.setMaximumWidth(INPUT_MAX_WIDTH)
        phase_layout.addWidget(self.space_avg_spin, 0, 1)

        phase_layout.addWidget(QLabel("Merge:"), 0, 2)
        self.merge_points_spin = QSpinBox()
        self.merge_points_spin.setRange(1, 64)
        self.merge_points_spin.setValue(25)
        self.merge_points_spin.setMinimumHeight(INPUT_MIN_HEIGHT)
        self.merge_points_spin.setMaximumWidth(INPUT_MAX_WIDTH)
        phase_layout.addWidget(self.merge_points_spin, 0, 3)

        phase_layout.addWidget(QLabel("DiffOrder:"), 1, 0)
        self.diff_order_spin = QSpinBox()
        self.diff_order_spin.setRange(0, 4)
        self.diff_order_spin.setValue(1)
        self.diff_order_spin.setMinimumHeight(INPUT_MIN_HEIGHT)
        self.diff_order_spin.setMaximumWidth(INPUT_MAX_WIDTH)
        phase_layout.addWidget(self.diff_order_spin, 1, 1)

        phase_layout.addWidget(QLabel("Detrend(Hz):"), 1, 2)
        self.detrend_bw_spin = QDoubleSpinBox()
        self.detrend_bw_spin.setRange(0.0, 1000000.0)
        self.detrend_bw_spin.setValue(10.0)
        self.detrend_bw_spin.setSingleStep(0.1)
        self.detrend_bw_spin.setMinimumHeight(INPUT_MIN_HEIGHT)
        self.detrend_bw_spin.setMaximumWidth(INPUT_MAX_WIDTH)
        phase_layout.addWidget(self.detrend_bw_spin, 1, 3)

        phase_layout.addWidget(QLabel("CropStart:"), 2, 0)
        self.crop_distance_start_spin = QSpinBox()
        self.crop_distance_start_spin.setRange(0, 10000000)
        self.crop_distance_start_spin.setValue(0)
        self.crop_distance_start_spin.setMinimumHeight(INPUT_MIN_HEIGHT)
        self.crop_distance_start_spin.setMaximumWidth(INPUT_MAX_WIDTH)
        self.crop_distance_start_spin.setToolTip("Single-channel PHASE only. 0 with CropEnd=0 keeps the full range.")
        phase_layout.addWidget(self.crop_distance_start_spin, 2, 1)

        phase_layout.addWidget(QLabel("CropEnd:"), 2, 2)
        self.crop_distance_end_spin = QSpinBox()
        self.crop_distance_end_spin.setRange(0, 10000000)
        self.crop_distance_end_spin.setValue(0)
        self.crop_distance_end_spin.setMinimumHeight(INPUT_MIN_HEIGHT)
        self.crop_distance_end_spin.setMaximumWidth(INPUT_MAX_WIDTH)
        self.crop_distance_end_spin.setToolTip("Single-channel PHASE only. End is exclusive; values above total points are clamped.")
        phase_layout.addWidget(self.crop_distance_end_spin, 2, 3)

        layout.addWidget(phase_group)

        # Display Control Group - display-only switches and view selection.
        display_group = QGroupBox("Display Control")
        display_layout = QGridLayout(display_group)
        display_layout.setSpacing(4)
        display_layout.setContentsMargins(8, 12, 8, 8)

        display_layout.addWidget(QLabel("Mode:"), 0, 0)
        self.mode_time_radio = QRadioButton("Time")
        self.mode_space_radio = QRadioButton("Space")
        self.mode_time_radio.setChecked(True)
        mode_group = QButtonGroup(self)
        mode_group.addButton(self.mode_time_radio, 0)
        mode_group.addButton(self.mode_space_radio, 1)
        mode_layout = QHBoxLayout()
        mode_layout.setSpacing(2)
        mode_layout.addWidget(self.mode_time_radio)
        mode_layout.addWidget(self.mode_space_radio)
        display_layout.addLayout(mode_layout, 0, 1)

        display_layout.addWidget(QLabel("Region:"), 0, 2)
        self.region_index_spin = QSpinBox()
        self.region_index_spin.setRange(0, 10000000)
        self.region_index_spin.setValue(0)
        self.region_index_spin.setMinimumHeight(INPUT_MIN_HEIGHT)
        self.region_index_spin.setMaximumWidth(60)
        display_layout.addWidget(self.region_index_spin, 0, 3)

        display_switch_layout = QHBoxLayout()
        display_switch_layout.setSpacing(24)
        display_switch_layout.setContentsMargins(0, 0, 0, 0)

        self.waveform_enable_check = QCheckBox("Waveform")
        self.waveform_enable_check.setToolTip("Enable time/space waveform plot updates")
        self.waveform_enable_check.setChecked(False)
        display_switch_layout.addWidget(self.waveform_enable_check)

        self.spectrum_enable_check = QCheckBox("PSD")
        self.spectrum_enable_check.setToolTip("Enable PSD plot updates")
        self.spectrum_enable_check.setChecked(True)
        display_switch_layout.addWidget(self.spectrum_enable_check)

        self.monitor_enable_check = QCheckBox("Monitor")
        self.monitor_enable_check.setToolTip("Enable monitor plot updates")
        self.monitor_enable_check.setChecked(False)
        display_switch_layout.addWidget(self.monitor_enable_check)

        self.rad_check = QCheckBox("rad")
        self.rad_check.setToolTip("Convert phase data to radians for display only: display = data / 32767 * pi\n(Storage always saves original int32 data)")
        self.rad_check.setChecked(True)
        display_switch_layout.addWidget(self.rad_check)
        display_switch_layout.addStretch(1)
        display_layout.addLayout(display_switch_layout, 1, 0, 1, 4)

        self.analysis_type_label = QLabel("PSD")
        self.analysis_type_label.setVisible(False)

        filter_layout = QHBoxLayout()
        filter_layout.setSpacing(8)
        filter_layout.setContentsMargins(0, 0, 0, 0)
        filter_label = QLabel("Filter:")
        filter_label.setFont(QFont("Times New Roman", 8))
        filter_layout.addWidget(filter_label)

        self.filter_spec_edit = QLineEdit(self._filter_spec_text)
        self.filter_spec_edit.setMaximumWidth(86)
        self.filter_spec_edit.setMinimumHeight(INPUT_MIN_HEIGHT)
        self.filter_spec_edit.setFont(QFont("Times New Roman", 8))
        self.filter_spec_edit.setToolTip("Examples: 1- high-pass, -10 low-pass, 2-10 band-pass")
        self.filter_spec_edit.editingFinished.connect(self._on_filter_spec_changed)
        filter_layout.addWidget(self.filter_spec_edit)

        self.filter_btn = QPushButton("FILTER")
        self.filter_btn.setFont(QFont("Times New Roman", 8, QFont.Bold))
        self.filter_btn.setMaximumWidth(76)
        self.filter_btn.setMinimumHeight(INPUT_MIN_HEIGHT)
        self.filter_btn.setCheckable(True)
        self.filter_btn.clicked.connect(self._on_filter_button_clicked)
        self._update_shared_filter_button_style()
        filter_layout.addWidget(self.filter_btn)
        filter_layout.addStretch(1)
        display_layout.addLayout(filter_layout, 2, 0, 1, 4)

        layout.addWidget(display_group)

        # Save Control Group - operational controls only. Storage parameters live on Tab4.
        save_group = QGroupBox("Data Save")
        save_layout = QGridLayout(save_group)
        save_layout.setSpacing(4)
        save_layout.setContentsMargins(8, 12, 8, 8)

        self.save_enable_check = QPushButton("SAVE")
        self.save_enable_check.setCheckable(True)
        self.save_enable_check.setFont(QFont("Times New Roman", 8, QFont.Bold))
        self.save_enable_check.setMinimumHeight(INPUT_MIN_HEIGHT)
        self.save_enable_check.setMaximumWidth(78)
        self.save_enable_check.setToolTip("Toggle data storage. Storage packet and file lengths are configured on Tab4.")
        self._update_save_button_style()
        save_layout.addWidget(self.save_enable_check, 0, 0)

        save_layout.addWidget(QLabel("Path:"), 0, 1)
        path_layout = QHBoxLayout()
        path_layout.setSpacing(4)
        self.save_path_edit = QLineEdit(self.params.save.path)
        self.save_path_edit.setMinimumHeight(INPUT_MIN_HEIGHT)
        self.browse_btn = QPushButton("...")
        self.browse_btn.setMaximumWidth(25)
        self.browse_btn.setMinimumHeight(INPUT_MIN_HEIGHT)
        self.browse_btn.clicked.connect(self._browse_save_path)
        path_layout.addWidget(self.save_path_edit, 1)
        path_layout.addWidget(self.browse_btn)
        save_layout.addLayout(path_layout, 0, 2, 1, 3)

        save_layout.addWidget(QLabel("Est. Size:"), 1, 0)
        self.file_size_label = QLabel("~-- MB/file")
        self.file_size_label.setStyleSheet("font-weight: normal; color: #666666;")
        save_layout.addWidget(self.file_size_label, 1, 1, 1, 2)

        self.saved_file_count_label = QLabel("Files: 0")
        self.saved_file_count_label.setStyleSheet("font-weight: normal; color: #666666;")
        save_layout.addWidget(self.saved_file_count_label, 1, 3, 1, 2)
        layout.addWidget(save_group)

        # Control Buttons
        control_layout = QHBoxLayout()

        # START button - green when ready, gray when running
        self.start_btn = QPushButton("START")
        self.start_btn.setMinimumHeight(38)
        self._set_start_btn_ready()

        # STOP button - gray when disabled, red when enabled
        self.stop_btn = QPushButton("STOP")
        self.stop_btn.setMinimumHeight(38)
        self._set_stop_btn_disabled()

        control_layout.addWidget(self.start_btn)
        control_layout.addWidget(self.stop_btn)
        layout.addLayout(control_layout)

        layout.addStretch()

        return panel

    def _set_start_btn_ready(self):
        """Set START button to ready state (green)"""
        self.start_btn.setEnabled(True)
        self.start_btn.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-weight: bold;
                font-size: 14px;
                border: none;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #45a049;
            }
            QPushButton:pressed {
                background-color: #3d8b40;
            }
        """)

    def _set_start_btn_running(self):
        """Set START button to running state (gray, disabled)"""
        self.start_btn.setEnabled(False)
        self.start_btn.setStyleSheet("""
            QPushButton {
                background-color: #9E9E9E;
                color: #666666;
                font-weight: bold;
                font-size: 14px;
                border: none;
                border-radius: 5px;
            }
        """)

    def _set_stop_btn_disabled(self):
        """Set STOP button to disabled state (gray)"""
        self.stop_btn.setEnabled(False)
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background-color: #BDBDBD;
                color: #757575;
                font-weight: bold;
                font-size: 14px;
                border: none;
                border-radius: 5px;
            }
        """)

    def _set_stop_btn_enabled(self):
        """Set STOP button to enabled state (red)"""
        self.stop_btn.setEnabled(True)
        self.stop_btn.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                font-weight: bold;
                font-size: 14px;
                border: none;
                border-radius: 5px;
            }
            QPushButton:hover {
                background-color: #da190b;
            }
            QPushButton:pressed {
                background-color: #c41508;
            }
        """)

    def _create_plot_panel(self) -> QWidget:
        """Create the plot display panel with tab widget"""
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setSpacing(5)
        layout.setContentsMargins(5, 5, 5, 5)

        # Configure pyqtgraph
        pg.setConfigOptions(antialias=True)

        # Create tab widget
        self.plot_tabs = QTabWidget()
        self.plot_tabs.setTabPosition(QTabWidget.North)

        # Set tab titles font style
        self.plot_tabs.setStyleSheet("""
            QTabWidget::tab-bar {
                alignment: left;
            }
            QTabBar::tab {
                font-family: 'Arial';
                font-size: 12px;
                font-weight: normal;
                padding: 6px 15px;
                margin: 1px;
                min-width: 90px;
            }
            QTabBar::tab:selected {
                font-weight: bold;
            }
        """)

        # Tab 1: Traditional plots (Time/Space + FFT + Monitor)
        self._create_traditional_plots_tab()

        # Tab 2: Time-Space plot
        self._create_time_space_tab()

        # Tab 3: TCP communication
        self._create_tcp_comm_tab()

        # Tab 4: storage and compression settings
        self._create_settings_tab()

        layout.addWidget(self.plot_tabs)

        # System Monitoring Panel - Single row layout
        monitor_frame = QFrame()
        monitor_frame.setFrameStyle(QFrame.StyledPanel)
        monitor_frame.setMaximumHeight(40)  # Reduced height for single row
        monitor_layout = QHBoxLayout(monitor_frame)  # Changed to horizontal layout
        monitor_layout.setSpacing(15)  # Add spacing between sections

        # Buffer Status section
        monitor_layout.addWidget(QLabel("Status:"))

        # Hardware Buffer
        self.hw_buffer_label = QLabel("HW: 0/50")
        self.hw_buffer_bar = QProgressBar()
        self.hw_buffer_bar.setMaximumWidth(80)  # Reduced width
        self.hw_buffer_bar.setMaximumHeight(16)  # Reduced height
        monitor_layout.addWidget(self.hw_buffer_label)
        monitor_layout.addWidget(self.hw_buffer_bar)

        # Signal Queue
        self.signal_queue_label = QLabel("SIG: 0/20")
        self.signal_queue_bar = QProgressBar()
        self.signal_queue_bar.setMaximumWidth(80)
        self.signal_queue_bar.setMaximumHeight(16)
        monitor_layout.addWidget(self.signal_queue_label)
        monitor_layout.addWidget(self.signal_queue_bar)

        # Storage Queue
        self.storage_queue_label = QLabel("STO: 0/200")
        self.storage_queue_bar = QProgressBar()
        self.storage_queue_bar.setMaximumWidth(80)
        self.storage_queue_bar.setMaximumHeight(16)
        monitor_layout.addWidget(self.storage_queue_label)
        monitor_layout.addWidget(self.storage_queue_bar)

        # Add separator
        separator = QLabel("|")
        separator.setStyleSheet("color: gray;")
        monitor_layout.addWidget(separator)

        # System Status section
        self.cpu_label = QLabel("CPU: 0%")
        self.disk_label = QLabel("Disk: 0GB free")
        self.polling_label = QLabel("Poll: 1ms")
        monitor_layout.addWidget(self.cpu_label)
        monitor_layout.addWidget(self.disk_label)
        monitor_layout.addWidget(self.polling_label)

        # Add separator
        separator2 = QLabel("|")
        separator2.setStyleSheet("color: gray;")
        monitor_layout.addWidget(separator2)

        # Additional status section
        self.buffer_label = QLabel("Buffer: 0 MB")
        self.frames_label = QLabel("Frames: 0")
        self.save_status_label = QLabel("Save: Off")
        monitor_layout.addWidget(self.buffer_label)
        monitor_layout.addWidget(self.frames_label)
        monitor_layout.addWidget(self.save_status_label)

        monitor_layout.addStretch()  # Push everything to the left

        layout.addWidget(monitor_frame)

        return panel

    def _create_traditional_plots_tab(self):
        """Create the traditional plots tab with existing functionality"""
        tab1_widget = QWidget()
        tab1_layout = QVBoxLayout(tab1_widget)
        tab1_layout.setSpacing(10)
        tab1_layout.setContentsMargins(5, 5, 5, 10)

        # Create plots with custom titles and styling
        self.plot_widget_1 = self._create_interactive_plot_widget("plot1")
        self.plot_widget_2 = self._create_interactive_plot_widget("plot2")
        self.plot_widget_3 = self._create_interactive_plot_widget("plot3")

        # Configure plot styles - white background and custom title styling
        plot_titles = ["Time Domain Data", "FFT Spectrum", "Monitor (Fiber End Detection)"]
        self.plot_widgets = [self.plot_widget_1, self.plot_widget_2, self.plot_widget_3]

        for i, pw in enumerate(self.plot_widgets):
            pw.setBackground('w')  # White background

            # Set custom title with New Roman font and dark blue color
            title_label = pw.setLabel('top', plot_titles[i])

            # Force dark blue color for title - multiple methods to ensure it works
            title_item = pw.getPlotItem().titleLabel.item
            title_item.setFont(QFont("Times New Roman", 9))

            # Method 1: Set default text color
            title_item.setDefaultTextColor(QColor(0, 0, 139))  # Dark blue

            # Method 2: Set HTML color (backup method)
            blue_title = f'<span style="color: rgb(0,0,139); font-family: Times New Roman; font-size: 9pt">{plot_titles[i]}</span>'
            pw.setLabel('top', blue_title)

            # Method 3: Force color via stylesheet if available
            try:
                title_item.document().setDefaultStyleSheet("color: rgb(0,0,139);")
            except:
                pass

            # Configure axes - keep top axis for title but hide its ticks
            x_axis = pw.getAxis('bottom')
            y_axis = pw.getAxis('left')
            top_axis = pw.getAxis('top')
            right_axis = pw.getAxis('right')

            # Show top axis (for title) but hide its ticks and values
            pw.showAxis('top', show=True)   # Keep for title
            pw.showAxis('right', show=False) # Hide completely

            # Hide top axis ticks and values but keep the title
            top_axis.setStyle(showValues=False, tickLength=0)

            # Hide right axis completely
            # (already done with showAxis above)

            # Grid and tick configuration with smaller fonts
            pw.showGrid(x=True, y=True, alpha=0.6)

            # Set fonts for axes - increase tick font size by 2 units
            axis_font = QFont("Times New Roman", 8)      # 轴标签保持 8 pt
            tick_font = QFont("Times New Roman", 8)      # 刻度值使用 8 pt

            # Configure tick style with reduced spacing
            x_axis.setStyle(showValues=True, tickLength=4, tickTextOffset=6)  # Reduced offset
            y_axis.setStyle(showValues=True, tickLength=4, tickTextOffset=4)  # Reduced offset

            # Set tick fonts (smaller)
            x_axis.setTickFont(tick_font)
            y_axis.setTickFont(tick_font)

            # Set axis colors
            x_axis.setPen('k')
            y_axis.setPen('k')
            x_axis.setTextPen('k')
            y_axis.setTextPen('k')

        # Set specific labels for each plot with consistent smaller fonts
        # Plot 1: Time Domain
        self._set_time_plot_axis('Distance (m)', 'distance')
        self.plot_widget_1.setLabel('left', 'Amp.',
                                   color='k', **{'font-family': 'Times New Roman', 'font-size': '8pt'})

        # Plot 2: FFT Spectrum (consistent font size)
        self.plot_widget_2.setLabel('bottom', 'Frequency (Hz)',
                                   color='k', **{'font-family': 'Times New Roman', 'font-size': '8pt'})
        self.plot_widget_2.setLabel('left', 'Amp. (dB)',
                                   color='k', **{'font-family': 'Times New Roman', 'font-size': '8pt'})

        # Plot 3: Monitor (consistent font size)
        self.plot_widget_3.setLabel('bottom', 'Point Index',
                                   color='k', **{'font-family': 'Times New Roman', 'font-size': '8pt'})
        self.plot_widget_3.setLabel('left', 'Amp.',
                                   color='k', **{'font-family': 'Times New Roman', 'font-size': '8pt'})

        # Plot curves setup - labels already set above
        # Plot 1 - Time Domain
        self.plot_curve_1 = []

        # Plot 2 - Spectrum
        # Linear scale for both axes (dB values already in log scale)
        self.plot_widget_2.setLogMode(x=False, y=False)
        self.spectrum_curve = self.plot_widget_2.plot(pen=pg.mkPen('#9467bd', width=1.5))  # Purple

        # Plot 3 - Monitor
        self.monitor_curves = []

        # Add plots to layout with balanced heights and proper scaling
        # Set both minimum and maximum heights to prevent over-stretching in fullscreen
        self.plot_widget_1.setMinimumHeight(180)  # Time Domain plot - increased
        self.plot_widget_1.setMaximumHeight(210)  # Controlled height range
        self.plot_widget_1.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        # Adjust plot heights - increase since text is smaller
        self.plot_widget_1.setMinimumHeight(200)  # Time Domain - increased
        self.plot_widget_1.setMaximumHeight(250)
        self.plot_widget_1.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.plot_widget_2.setMinimumHeight(200)  # FFT Spectrum - increased
        self.plot_widget_2.setMaximumHeight(250)
        self.plot_widget_2.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        self.plot_widget_3.setMinimumHeight(150)  # Monitor plot - increased
        self.plot_widget_3.setMaximumHeight(180)
        self.plot_widget_3.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Preferred)

        # Reduce spacing between plots
        tab1_layout.setSpacing(5)  # Reduced from default spacing
        tab1_layout.addWidget(self.plot_widget_1)
        tab1_layout.addWidget(self.plot_widget_2)
        tab1_layout.addWidget(self.plot_widget_3)

        # Add a flexible spacer that will absorb extra space in fullscreen mode
        tab1_layout.addStretch(1)

        self.plot_tabs.addTab(tab1_widget, "Time Plot")

    def _create_time_space_tab(self):
        """Create the time-space plot tab"""
        tab2_widget = QWidget()
        tab2_layout = QVBoxLayout(tab2_widget)
        tab2_layout.setSpacing(5)
        tab2_layout.setContentsMargins(5, 5, 5, 5)

        # Create time-space plot widget using intelligent selector
        self.time_space_widget = create_time_space_widget()
        tab2_layout.addWidget(self.time_space_widget)

        self.plot_tabs.addTab(tab2_widget, "Time-Space Plot")

        # Connect signals after widget creation
        self._connect_time_space_signals()

    def _create_tcp_comm_tab(self):
        """Create the communication-only Tab3."""
        tab3_widget = QWidget()
        tab3_layout = QVBoxLayout(tab3_widget)
        tab3_layout.setSpacing(10)
        tab3_layout.setContentsMargins(10, 10, 10, 10)

        settings_group = QGroupBox("TCP Communication")
        settings_layout = QGridLayout(settings_group)
        settings_layout.setContentsMargins(10, 12, 10, 10)
        settings_layout.setHorizontalSpacing(10)
        settings_layout.setVerticalSpacing(6)

        self.tab3_comm_enable_check = QCheckBox("Enable communication")
        self.tab3_comm_enable_check.setChecked(False)
        settings_layout.addWidget(self.tab3_comm_enable_check, 0, 0, 1, 2)

        settings_layout.addWidget(QLabel("Server IP:"), 1, 0)
        self.tab3_server_ip_edit = QLineEdit("169.255.1.2")
        settings_layout.addWidget(self.tab3_server_ip_edit, 1, 1)

        settings_layout.addWidget(QLabel("Server Port:"), 2, 0)
        self.tab3_server_port_spin = QSpinBox()
        self.tab3_server_port_spin.setRange(1, 65535)
        self.tab3_server_port_spin.setValue(3678)
        settings_layout.addWidget(self.tab3_server_port_spin, 2, 1)

        settings_layout.addWidget(QLabel("Channel Start (0-based):"), 3, 0)
        self.tab3_channel_start_spin = QSpinBox()
        self.tab3_channel_start_spin.setRange(0, 1000000)
        self.tab3_channel_start_spin.setValue(50)
        self.tab3_channel_start_spin.setToolTip("位置点序号从 0 开始（0 表示第一个空间点）")
        settings_layout.addWidget(self.tab3_channel_start_spin, 3, 1)

        settings_layout.addWidget(QLabel("Channel End (0-based):"), 4, 0)
        self.tab3_channel_end_spin = QSpinBox()
        self.tab3_channel_end_spin.setRange(0, 1000000)
        self.tab3_channel_end_spin.setValue(100)
        self.tab3_channel_end_spin.setToolTip("位置点序号从 0 开始（0 表示第一个空间点）")
        settings_layout.addWidget(self.tab3_channel_end_spin, 4, 1)

        settings_layout.addWidget(QLabel("Time Downsample:"), 5, 0)
        self.tab3_time_downsample_spin = QSpinBox()
        self.tab3_time_downsample_spin.setRange(1, 100000)
        self.tab3_time_downsample_spin.setValue(1)
        settings_layout.addWidget(self.tab3_time_downsample_spin, 5, 1)

        settings_layout.addWidget(QLabel("Space Downsample:"), 6, 0)
        self.tab3_space_downsample_spin = QSpinBox()
        self.tab3_space_downsample_spin.setRange(1, 100000)
        self.tab3_space_downsample_spin.setValue(1)
        settings_layout.addWidget(self.tab3_space_downsample_spin, 6, 1)

        self.tab3_length_comm_label = QLabel("Length/Comm:")
        self.tab3_length_comm_label.setToolTip("Seconds per outgoing TCP packet. Must be an integer multiple of Length/Load. Default 1 s.")
        settings_layout.addWidget(self.tab3_length_comm_label, 7, 0)
        self.tab3_length_comm_spin = QDoubleSpinBox()
        self.tab3_length_comm_spin.setDecimals(3)
        self.tab3_length_comm_spin.setRange(0.001, 86400.0)
        self.tab3_length_comm_spin.setSingleStep(0.1)
        self.tab3_length_comm_spin.setValue(1.0)
        self.tab3_length_comm_spin.setSuffix(" s")
        self.tab3_length_comm_spin.setToolTip(self.tab3_length_comm_label.toolTip())
        settings_layout.addWidget(self.tab3_length_comm_spin, 7, 1)

        tab3_layout.addWidget(settings_group)

        status_group = QGroupBox("Communication Status")
        status_layout = QGridLayout(status_group)
        status_layout.setContentsMargins(10, 12, 10, 10)
        status_layout.setHorizontalSpacing(10)
        status_layout.setVerticalSpacing(6)

        self.tab3_availability_label = QLabel("Waiting for acquisition parameters")
        self.tab3_comm_state_label = QLabel("Idle")
        self.tab3_comm_state_label.setStyleSheet("color: #555; font-weight: bold;")
        self.tab3_comm_message_label = QLabel("-")
        self.tab3_comm_message_label.setWordWrap(True)
        self.tab3_comm_last_error_label = QLabel("-")
        self.tab3_comm_last_error_label.setWordWrap(True)
        self.tab3_acquired_packets_label = QLabel("0")
        self.tab3_queued_packets_label = QLabel("0")
        self.tab3_sent_packets_label = QLabel("0")
        self.tab3_dropped_packets_label = QLabel("0")
        self.tab3_last_comm_count_label = QLabel("-")
        self.tab3_bytes_sent_label = QLabel("0")
        self.tab3_comm_channel_count_label = QLabel("-")
        self.tab3_comm_sample_rate_label = QLabel("-")
        self.tab3_comm_duration_label = QLabel("-")
        self.tab3_comm_data_bytes_label = QLabel("-")

        status_layout.addWidget(QLabel("Availability:"), 0, 0)
        status_layout.addWidget(self.tab3_availability_label, 0, 1, 1, 3)
        status_layout.addWidget(QLabel("State:"), 1, 0)
        status_layout.addWidget(self.tab3_comm_state_label, 1, 1)
        status_layout.addWidget(QLabel("Message:"), 2, 0)
        status_layout.addWidget(self.tab3_comm_message_label, 2, 1, 1, 3)
        status_layout.addWidget(QLabel("Last Error:"), 3, 0)
        status_layout.addWidget(self.tab3_comm_last_error_label, 3, 1, 1, 3)
        status_layout.addWidget(QLabel("Acquired:"), 4, 0)
        status_layout.addWidget(self.tab3_acquired_packets_label, 4, 1)
        status_layout.addWidget(QLabel("Queued:"), 4, 2)
        status_layout.addWidget(self.tab3_queued_packets_label, 4, 3)
        status_layout.addWidget(QLabel("Sent:"), 5, 0)
        status_layout.addWidget(self.tab3_sent_packets_label, 5, 1)
        status_layout.addWidget(QLabel("Dropped:"), 5, 2)
        status_layout.addWidget(self.tab3_dropped_packets_label, 5, 3)
        status_layout.addWidget(QLabel("Last Comm:"), 6, 0)
        status_layout.addWidget(self.tab3_last_comm_count_label, 6, 1)
        status_layout.addWidget(QLabel("Bytes Sent:"), 6, 2)
        status_layout.addWidget(self.tab3_bytes_sent_label, 6, 3)
        status_layout.addWidget(QLabel("Channels:"), 7, 0)
        status_layout.addWidget(self.tab3_comm_channel_count_label, 7, 1)
        status_layout.addWidget(QLabel("Sample Rate:"), 7, 2)
        status_layout.addWidget(self.tab3_comm_sample_rate_label, 7, 3)
        status_layout.addWidget(QLabel("Packet Duration:"), 8, 0)
        status_layout.addWidget(self.tab3_comm_duration_label, 8, 1)
        status_layout.addWidget(QLabel("Data Bytes:"), 8, 2)
        status_layout.addWidget(self.tab3_comm_data_bytes_label, 8, 3)

        tab3_layout.addWidget(status_group)
        tab3_layout.addStretch(1)

        self.plot_tabs.addTab(tab3_widget, "TCP Comm")


    def _create_settings_tab(self):
        """Create Tab4 with timing, hardware detail, and storage settings."""
        tab4_widget = QWidget()
        tab4_layout = QVBoxLayout(tab4_widget)
        tab4_layout.setSpacing(10)
        tab4_layout.setContentsMargins(10, 10, 10, 10)

        INPUT_MIN_HEIGHT = 22
        INPUT_MAX_WIDTH = 90

        def make_length_spin(value: float, *, step: float = 0.1, maximum: float = 86400.0) -> QDoubleSpinBox:
            spin = QDoubleSpinBox()
            spin.setDecimals(3)
            spin.setRange(0.001, maximum)
            spin.setSingleStep(step)
            spin.setValue(float(value))
            spin.setMinimumHeight(INPUT_MIN_HEIGHT)
            spin.setMaximumWidth(INPUT_MAX_WIDTH)
            spin.setSuffix(" s")
            return spin

        timing_group = QGroupBox("Acquisition Length")
        timing_layout = QGridLayout(timing_group)
        timing_layout.setContentsMargins(10, 12, 10, 10)
        timing_layout.setHorizontalSpacing(10)
        timing_layout.setVerticalSpacing(6)

        self.length_load_label = QLabel("Length/Load:")
        self.length_load_label.setToolTip("Seconds per DLL read block. Converted to frames by Scan(Hz). Default 0.2 s.")
        timing_layout.addWidget(self.length_load_label, 0, 0)
        self.length_load_spin = make_length_spin(self.params.display.length_load_s, step=0.1)
        self.length_load_spin.setToolTip(self.length_load_label.toolTip())
        timing_layout.addWidget(self.length_load_spin, 0, 1)

        self.length_plot_label = QLabel("Length/Plot:")
        self.length_plot_label.setToolTip("Seconds per waveform/PSD display update and retained display window. Must be an integer multiple of Length/Load. Default 1 s.")
        timing_layout.addWidget(self.length_plot_label, 0, 2)
        self.length_plot_spin = make_length_spin(self.params.display.length_plot_s, step=0.1)
        self.length_plot_spin.setToolTip(self.length_plot_label.toolTip())
        timing_layout.addWidget(self.length_plot_spin, 0, 3)

        self.length_load_hint_label = QLabel("Load: --")
        self.length_load_hint_label.setStyleSheet("color: #666666;")
        timing_layout.addWidget(self.length_load_hint_label, 1, 0, 1, 4)
        tab4_layout.addWidget(timing_group)

        hardware_group = QGroupBox("Hardware Detail")
        hardware_layout = QGridLayout(hardware_group)
        hardware_layout.setContentsMargins(10, 12, 10, 10)
        hardware_layout.setHorizontalSpacing(10)
        hardware_layout.setVerticalSpacing(6)

        hardware_layout.addWidget(QLabel("Clock:"), 0, 0)
        self.clk_internal_radio = QRadioButton("Int")
        self.clk_external_radio = QRadioButton("Ext")
        self.clk_internal_radio.setChecked(True)
        clk_group = QButtonGroup(self)
        clk_group.addButton(self.clk_internal_radio, 0)
        clk_group.addButton(self.clk_external_radio, 1)
        clk_layout = QHBoxLayout()
        clk_layout.setSpacing(2)
        clk_layout.addWidget(self.clk_internal_radio)
        clk_layout.addWidget(self.clk_external_radio)
        hardware_layout.addLayout(clk_layout, 0, 1)

        hardware_layout.addWidget(QLabel("Trig:"), 0, 2)
        self.trig_in_radio = QRadioButton("In")
        self.trig_out_radio = QRadioButton("Out")
        self.trig_out_radio.setChecked(True)
        trig_group = QButtonGroup(self)
        trig_group.addButton(self.trig_in_radio, 0)
        trig_group.addButton(self.trig_out_radio, 1)
        trig_layout = QHBoxLayout()
        trig_layout.setSpacing(2)
        trig_layout.addWidget(self.trig_in_radio)
        trig_layout.addWidget(self.trig_out_radio)
        hardware_layout.addLayout(trig_layout, 0, 3)

        hardware_layout.addWidget(QLabel("Bypass:"), 1, 0)
        self.bypass_spin = QSpinBox()
        self.bypass_spin.setRange(0, 10000000)
        self.bypass_spin.setValue(60)
        self.bypass_spin.setMinimumHeight(INPUT_MIN_HEIGHT)
        self.bypass_spin.setMaximumWidth(INPUT_MAX_WIDTH)
        hardware_layout.addWidget(self.bypass_spin, 1, 1)

        hardware_layout.addWidget(QLabel("CenterFreq(MHz):"), 1, 2)
        self.center_freq_spin = QSpinBox()
        self.center_freq_spin.setRange(1, 100000)
        self.center_freq_spin.setValue(200)
        self.center_freq_spin.setMinimumHeight(INPUT_MIN_HEIGHT)
        self.center_freq_spin.setMaximumWidth(INPUT_MAX_WIDTH)
        hardware_layout.addWidget(self.center_freq_spin, 1, 3)

        hardware_layout.addWidget(QLabel("DataRate:"), 2, 0)
        self.data_rate_combo = QComboBox()
        for label, value in DATA_RATE_OPTIONS:
            self.data_rate_combo.addItem(label, value)
        self.data_rate_combo.setMinimumHeight(INPUT_MIN_HEIGHT)
        hardware_layout.addWidget(self.data_rate_combo, 2, 1)

        hardware_layout.addWidget(QLabel("Rate2Phase:"), 2, 2)
        self.rate2phase_combo = QComboBox()
        for label, value in RATE2PHASE_OPTIONS:
            self.rate2phase_combo.addItem(label, value)
        self.rate2phase_combo.setCurrentIndex(0)
        self.rate2phase_combo.setMinimumHeight(INPUT_MIN_HEIGHT)
        hardware_layout.addWidget(self.rate2phase_combo, 2, 3)

        self.polar_div_check = QCheckBox("PolarDiv")
        self.polar_div_check.setChecked(True)
        hardware_layout.addWidget(self.polar_div_check, 3, 0, 1, 2)
        tab4_layout.addWidget(hardware_group)

        storage_group = QGroupBox("Storage Setting")
        storage_layout = QGridLayout(storage_group)
        storage_layout.setContentsMargins(10, 12, 10, 10)
        storage_layout.setHorizontalSpacing(10)
        storage_layout.setVerticalSpacing(6)

        storage_layout.addWidget(QLabel("Format:"), 0, 0)
        self.storage_format_combo = QComboBox()
        for label, value in STORAGE_FORMAT_OPTIONS:
            self.storage_format_combo.addItem(label, value)
        self.storage_format_combo.setToolTip("Select raw .bin storage or packetized Bitshuffle+Zstd .bz storage")
        storage_layout.addWidget(self.storage_format_combo, 0, 1)

        self.length_save_label = QLabel("Length/Save:")
        self.length_save_label.setToolTip("Seconds per stored packet/write unit for both .bin and .bz. Must be an integer multiple of Length/Load. Default 1 s.")
        storage_layout.addWidget(self.length_save_label, 1, 0)
        self.length_save_spin = make_length_spin(self.params.save.length_save_s, step=0.1)
        self.length_save_spin.setToolTip(self.length_save_label.toolTip())
        storage_layout.addWidget(self.length_save_spin, 1, 1)

        self.length_file_label = QLabel("Length/File:")
        self.length_file_label.setToolTip("Seconds of data per output file for both .bin and .bz. Must be an integer multiple of Length/Save. Default 10 s.")
        storage_layout.addWidget(self.length_file_label, 1, 2)
        self.length_file_spin = make_length_spin(self.params.save.length_file_s, step=1.0)
        self.length_file_spin.setToolTip(self.length_file_label.toolTip())
        storage_layout.addWidget(self.length_file_spin, 1, 3)

        self.save_downsample_label = QLabel("Save DS:")
        self.save_downsample_label.setToolTip("Storage-only downsample factor: save every Nth point without filtering.")
        storage_layout.addWidget(self.save_downsample_label, 2, 0)
        self.save_downsample_spin = QSpinBox()
        self.save_downsample_spin.setRange(1, 100000)
        self.save_downsample_spin.setValue(self.params.save.storage_downsample_factor)
        self.save_downsample_spin.setMinimumHeight(INPUT_MIN_HEIGHT)
        self.save_downsample_spin.setMaximumWidth(INPUT_MAX_WIDTH)
        self.save_downsample_spin.setToolTip("Storage-only downsample factor. 10 means keep 1 point from every 10 points.")
        storage_layout.addWidget(self.save_downsample_spin, 2, 1)

        self.overwrite_oldest_check = QCheckBox("Overwrite oldest when disk is full")
        self.overwrite_oldest_check.setChecked(
            bool(getattr(self.params.save, "overwrite_oldest_when_full", False))
        )
        self.overwrite_oldest_check.setToolTip(
            "When free space is insufficient, delete the oldest closed .bin/.bz files "
            "in the current save directory so the newest data can continue to be stored."
        )
        storage_layout.addWidget(self.overwrite_oldest_check, 2, 2, 1, 2)

        self.bz_zstd_level_label = QLabel("Zstd Level:")
        self.bz_zstd_level_label.setToolTip("Zstd compression level for .bz packets. Higher values may compress smaller but cost more CPU.")
        storage_layout.addWidget(self.bz_zstd_level_label, 3, 0)
        self.bz_zstd_level_spin = QSpinBox()
        self.bz_zstd_level_spin.setRange(1, 22)
        self.bz_zstd_level_spin.setValue(self.params.save.bz_zstd_level)
        self.bz_zstd_level_spin.setToolTip(self.bz_zstd_level_label.toolTip())
        storage_layout.addWidget(self.bz_zstd_level_spin, 3, 1)

        self.bz_bitshuffle_block_label = QLabel("Bitshuffle Block:")
        self.bz_bitshuffle_block_label.setToolTip("Bitshuffle compression block size in int32 values, not acquisition frames.")
        storage_layout.addWidget(self.bz_bitshuffle_block_label, 3, 2)
        self.bz_bitshuffle_block_spin = QSpinBox()
        self.bz_bitshuffle_block_spin.setRange(1, 16777216)
        self.bz_bitshuffle_block_spin.setSingleStep(1024)
        self.bz_bitshuffle_block_spin.setValue(self.params.save.bz_bitshuffle_block_values)
        self.bz_bitshuffle_block_spin.setToolTip(self.bz_bitshuffle_block_label.toolTip())
        storage_layout.addWidget(self.bz_bitshuffle_block_spin, 3, 3)

        self.bz_compression_workers_label = QLabel("BZ Workers:")
        self.bz_compression_workers_label.setToolTip("Parallel compression worker count for .bz storage. Higher values improve throughput but use more CPU.")
        storage_layout.addWidget(self.bz_compression_workers_label, 4, 0)
        self.bz_compression_workers_spin = QSpinBox()
        self.bz_compression_workers_spin.setRange(1, 16)
        self.bz_compression_workers_spin.setValue(max(1, int(getattr(self.params.save, "bz_compression_workers", 4) or 4)))
        self.bz_compression_workers_spin.setToolTip(self.bz_compression_workers_label.toolTip())
        storage_layout.addWidget(self.bz_compression_workers_spin, 4, 1)

        self.bz_packet_hint_label = QLabel("Save: --")
        self.bz_packet_hint_label.setWordWrap(True)
        self.bz_packet_hint_label.setStyleSheet("color: #666666;")
        storage_layout.addWidget(self.bz_packet_hint_label, 5, 0, 1, 4)

        self.bz_realtime_status_label = QLabel("BZ: idle")
        self.bz_realtime_status_label.setWordWrap(True)
        self.bz_realtime_status_label.setStyleSheet("color: #666666;")
        storage_layout.addWidget(self.bz_realtime_status_label, 6, 0, 1, 4)

        tab4_layout.addWidget(storage_group)
        tab4_layout.addStretch(1)

        self._set_combo_to_data(self.storage_format_combo, self.params.save.storage_format)
        self._update_storage_format_control_states()
        self._update_bz_setting_hints()
        self.plot_tabs.addTab(tab4_widget, "setting")

    def _create_interactive_plot_widget(self, plot_key: str) -> pg.PlotWidget:
        """Create a PlotWidget with unified rectangle zoom behavior."""
        view_box = ZoomablePlotViewBox()
        plot_widget = pg.PlotWidget(viewBox=view_box)
        self._interactive_plot_widgets[plot_key] = plot_widget
        self._plot_zoom_locked[plot_key] = False
        view_box.sigManualRangeChange.connect(
            lambda key=plot_key: self._on_plot_manual_range_change(key)
        )
        view_box.sigViewAllRequested.connect(
            lambda key=plot_key: self._restore_plot_auto_range(key)
        )
        return plot_widget

    def _configure_realtime_curve(self, curve: pg.PlotDataItem):
        """Use pyqtgraph fast-path settings for large realtime curves."""
        curve.setClipToView(True)
        curve.setDownsampling(auto=True, method="peak")
        curve.setSkipFiniteCheck(True)

    def _configure_time_plot_curves_for_axis(self, axis_kind: str) -> None:
        """Tune Tab1 curve rendering for distance or time-axis data density."""
        if not hasattr(self, "plot_curve_1"):
            return
        for curve in self.plot_curve_1:
            try:
                if axis_kind == "time":
                    curve.setClipToView(False)
                    curve.setDownsampling(auto=False)
                    curve.setSkipFiniteCheck(True)
                else:
                    self._configure_realtime_curve(curve)
            except Exception:
                pass

    def _on_plot_manual_range_change(self, plot_key: str):
        plot_widget = self._interactive_plot_widgets.get(plot_key)
        if plot_widget is None:
            return
        self._plot_zoom_locked[plot_key] = True
        plot_widget.getViewBox().disableAutoRange()

    def _restore_plot_auto_range(self, plot_key: str):
        plot_widget = self._interactive_plot_widgets.get(plot_key)
        if plot_widget is None:
            return
        self._plot_zoom_locked[plot_key] = False
        view_box = plot_widget.getViewBox()
        view_box.enableAutoRange(x=True, y=True)
        view_box.autoRange(padding=0.0)

    def _curve_data_range(self, curves) -> Optional[tuple[float, float, float, float]]:
        """Return finite x/y bounds for the currently populated curves."""
        x_min = y_min = np.inf
        x_max = y_max = -np.inf
        has_data = False

        for curve in curves:
            x_data, y_data = curve.getData()
            if x_data is None or y_data is None:
                continue
            x_arr = np.asarray(x_data)
            y_arr = np.asarray(y_data)
            if x_arr.size == 0 or y_arr.size == 0:
                continue

            count = min(x_arr.size, y_arr.size)
            x_arr = x_arr[:count]
            y_arr = y_arr[:count]
            finite_mask = np.isfinite(x_arr) & np.isfinite(y_arr)
            if not finite_mask.any():
                continue

            finite_x = x_arr[finite_mask]
            finite_y = y_arr[finite_mask]
            x_min = min(x_min, float(finite_x.min()))
            x_max = max(x_max, float(finite_x.max()))
            y_min = min(y_min, float(finite_y.min()))
            y_max = max(y_max, float(finite_y.max()))
            has_data = True

        if not has_data:
            return None

        if x_min == x_max:
            pad = max(abs(x_min) * 0.01, 1.0)
            x_min -= pad
            x_max += pad
        if y_min == y_max:
            pad = max(abs(y_min) * 0.01, 1.0)
            y_min -= pad
            y_max += pad

        return x_min, x_max, y_min, y_max

    def _force_plot_range_to_curve_data(self, plot_key: str, curves, padding: float = 0.02) -> bool:
        """Set a plot range from curve data instead of relying on cached item bounds."""
        plot_widget = self._interactive_plot_widgets.get(plot_key)
        if plot_widget is None:
            return False

        data_range = self._curve_data_range(curves)
        if data_range is None:
            return False

        x_min, x_max, y_min, y_max = data_range
        view_box = plot_widget.getViewBox()
        self._plot_zoom_locked[plot_key] = False
        view_box.enableAutoRange(x=True, y=True)
        try:
            view_box.setRange(
                xRange=(x_min, x_max),
                yRange=(y_min, y_max),
                padding=padding,
                disableAutoRange=False,
            )
        except TypeError:
            view_box.setRange(xRange=(x_min, x_max), yRange=(y_min, y_max), padding=padding)
            view_box.enableAutoRange(x=True, y=True)
        self._refresh_plot_curve_items(plot_widget, curves)
        return True

    def _refresh_plot_curve_items(self, plot_widget: pg.PlotWidget, curves) -> None:
        """Refresh curve drawing paths after a forced range change."""
        for curve in curves:
            try:
                curve.updateItems()
            except Exception:
                pass
        try:
            plot_widget.getPlotItem().update()
            plot_widget.getViewBox().update()
        except Exception:
            pass

    def _setup_plots(self):
        """Initialize plot curves"""
        # Colors suitable for white background
        colors = ['#1f77b4', '#ff7f0e', '#2ca02c', '#d62728']  # Blue, Orange, Green, Red

        # Time domain curves (up to 4 frames)
        for i in range(4):
            curve = self.plot_widget_1.plot(pen=pg.mkPen(colors[i], width=1.5))
            self._configure_realtime_curve(curve)
            self.plot_curve_1.append(curve)

        # Monitor curves (up to 2 channels)
        for i in range(2):
            curve = self.plot_widget_3.plot(pen=pg.mkPen(colors[i], width=1.5))
            self._configure_realtime_curve(curve)
            self.monitor_curves.append(curve)

    # ----- SIGNAL-SLOT CONNECTIONS -----

    def _connect_signals(self):
        """Connect UI signals to slots"""
        self.start_btn.clicked.connect(self._on_start)
        self.stop_btn.clicked.connect(self._on_stop)

        self.data_source_combo.currentIndexChanged.connect(self._on_data_source_changed)
        self.channel_combo.currentIndexChanged.connect(self._on_channel_changed)
        self.point_num_spin.valueChanged.connect(self._update_calculated_values)
        self.scan_rate_spin.valueChanged.connect(self._update_calculated_values)
        self.merge_points_spin.valueChanged.connect(self._update_calculated_values)
        self.crop_distance_start_spin.valueChanged.connect(self._update_calculated_values)
        self.crop_distance_end_spin.valueChanged.connect(self._update_calculated_values)
        self.rate2phase_combo.currentIndexChanged.connect(self._update_calculated_values)
        self.length_load_spin.valueChanged.connect(self._on_length_settings_changed)
        self.length_plot_spin.valueChanged.connect(self._on_length_settings_changed)
        self.length_save_spin.valueChanged.connect(self._on_storage_settings_changed)
        self.length_file_spin.valueChanged.connect(self._on_storage_settings_changed)
        self.save_downsample_spin.valueChanged.connect(self._on_storage_downsample_changed)
        self.overwrite_oldest_check.toggled.connect(self._on_storage_overwrite_changed)
        self.save_enable_check.toggled.connect(self._on_save_enable_toggled)
        self.save_path_edit.editingFinished.connect(self._on_save_path_edited)
        self.storage_format_combo.currentIndexChanged.connect(self._on_storage_settings_changed)
        self.bz_zstd_level_spin.valueChanged.connect(self._on_storage_settings_changed)
        self.bz_bitshuffle_block_spin.valueChanged.connect(self._on_storage_settings_changed)
        self.bz_compression_workers_spin.valueChanged.connect(self._on_storage_settings_changed)
        self.scan_rate_spin.valueChanged.connect(self._on_length_settings_changed)
        self.data_rate_combo.currentIndexChanged.connect(self._update_calculated_values)
        self.data_source_combo.currentIndexChanged.connect(self._sync_tcp_tab3_availability)
        self.channel_combo.currentIndexChanged.connect(self._sync_tcp_tab3_availability)
        self.point_num_spin.valueChanged.connect(self._sync_tcp_tab3_availability)
        self.scan_rate_spin.valueChanged.connect(self._sync_tcp_tab3_availability)
        self.merge_points_spin.valueChanged.connect(self._sync_tcp_tab3_availability)
        self.crop_distance_start_spin.valueChanged.connect(self._sync_tcp_tab3_availability)
        self.crop_distance_end_spin.valueChanged.connect(self._sync_tcp_tab3_availability)

        # Connect display mode signals.
        self.mode_time_radio.toggled.connect(self._on_mode_changed)
        self.mode_space_radio.toggled.connect(self._on_mode_changed)
        self.waveform_enable_check.toggled.connect(self._on_waveform_display_toggled)
        self.monitor_enable_check.toggled.connect(self._on_monitor_display_toggled)
        self.rad_check.toggled.connect(self._on_rad_display_toggled)

        # Connect region index changes.
        self.region_index_spin.valueChanged.connect(self._on_region_changed)
        self.plot_tabs.currentChanged.connect(self._on_plot_tab_changed)

        # 初始化分析类型标签
        self._initialize_analysis_type_label()
        self.tab3_comm_enable_check.toggled.connect(self._on_tcp_tab3_settings_changed)
        self.tab3_server_ip_edit.textChanged.connect(self._on_tcp_tab3_settings_changed)
        self.tab3_server_port_spin.valueChanged.connect(self._on_tcp_tab3_settings_changed)
        self.tab3_channel_start_spin.valueChanged.connect(self._on_tcp_tab3_settings_changed)
        self.tab3_channel_end_spin.valueChanged.connect(self._on_tcp_tab3_settings_changed)
        self.tab3_time_downsample_spin.valueChanged.connect(self._on_tcp_tab3_settings_changed)
        self.tab3_space_downsample_spin.valueChanged.connect(self._on_tcp_tab3_settings_changed)
        self.tab3_length_comm_spin.valueChanged.connect(self._on_tcp_tab3_settings_changed)

    def _connect_tcp_tab3_manager(self):
        """Connect the communication manager to the Tab3 UI."""
        self.tcp_tab3_manager.status_changed.connect(self.update_tab3_comm_status)
        self.tcp_tab3_manager.statistics_changed.connect(self.update_tab3_comm_statistics)
        self.tcp_tab3_manager.availability_changed.connect(self.update_tab3_comm_availability)
        self.tcp_tab3_manager.error_occurred.connect(self._on_tcp_tab3_error)

    def _clear_waveform_plot(self):
        """Clear all waveform curves on plot 1."""
        if not hasattr(self, 'plot_curve_1'):
            return
        for curve in self.plot_curve_1:
            curve.setData([])

    def _clear_monitor_plot(self):
        """Clear all monitor curves on plot 3."""
        if not hasattr(self, 'monitor_curves'):
            return
        for curve in self.monitor_curves:
            curve.setData([])

    @pyqtSlot(bool)
    def _on_waveform_display_toggled(self, enabled: bool):
        """Enable or disable waveform rendering on plot 1."""
        self._reset_tab1_phase_filter()
        if not enabled:
            self._clear_waveform_plot()

    @pyqtSlot(bool)
    def _on_rad_display_toggled(self, enabled: bool):
        """Enable or disable display-only phase-to-radian conversion."""
        self.params.display.rad_enable = bool(enabled)
        self._sync_acquisition_display_transform()

    @pyqtSlot(bool)
    def _on_monitor_display_toggled(self, enabled: bool):
        """Enable or disable monitor rendering on plot 3."""
        self.params.display.monitor_plot_enabled = bool(enabled)
        if self.acq_thread is not None and hasattr(self.acq_thread, "set_monitor_read_enabled"):
            self.acq_thread.set_monitor_read_enabled(
                bool(enabled) and self.params.upload.data_source == DataSource.PHASE
            )
        if not enabled:
            self._clear_monitor_plot()
        elif self._current_monitor_data is not None:
            try:
                channel_num = self.params.upload.channel_num or 1
                self._update_monitor_display(self._current_monitor_data, channel_num)
            except Exception as e:
                log.warning(f"Failed to refresh monitor display: {e}")

    def _sync_display_control_states(self):
        """Keep display switches consistent with the current data source."""
        is_phase = self.data_source_combo.currentData() == DataSource.PHASE
        self.plot_widget_3.setEnabled(is_phase)
        self.mode_space_radio.setEnabled(is_phase)
        self.monitor_enable_check.setEnabled(is_phase)
        if not is_phase:
            self._clear_monitor_plot()

    def _initialize_analysis_type_label(self):
        """Initialize the analysis type label based on the current data source."""
        # 根据当前数据源设置分析类型标签
        data_source = self.data_source_combo.currentData() or DataSource.PHASE
        is_phase = (data_source == DataSource.PHASE)

        self.analysis_type_label.setText("PSD")
        if is_phase:
            self.analysis_type_label.setToolTip("Phase data: PSD analysis using scipy.welch")
            self.spectrum_enable_check.setToolTip("Enable phase PSD plot updates")
        else:
            self.analysis_type_label.setToolTip("Raw data: power spectrum analysis")
            self.spectrum_enable_check.setToolTip("Enable raw power spectrum plot updates")

    def _connect_time_space_signals(self):
        """Connect time-space widget signals after widget is created"""
        if hasattr(self, 'time_space_widget') and self.time_space_widget is not None:
            self.time_space_widget.parametersChanged.connect(self._on_time_space_params_changed)
            self.time_space_widget.pointCountChanged.connect(self._on_point_count_changed)
            # 连接 PLOT 按钮状态变化信号
            if hasattr(self.time_space_widget, 'plotStateChanged'):
                self.time_space_widget.plotStateChanged.connect(self._on_plot_state_changed)
            log.debug("Time-space widget signals connected")

    def _init_device(self):
        """Initialize the PCIe-7821 device"""
        log.info("Initializing device...")
        try:
            self.api = PCIe7821API()
            result = self.api.open()
            if result == 0:
                self._update_device_status(True)
                log.info("Device initialized successfully")
            else:
                self._update_device_status(False)
                log.error(f"Failed to open device: error code {result}")
                QMessageBox.warning(self, "Warning", f"Failed to open device: error code {result}")
        except FileNotFoundError as e:
            self._update_device_status(False)
            log.error(f"DLL not found: {e}")
            QMessageBox.warning(self, "Warning", f"DLL not found: {e}")
        except Exception as e:
            self._update_device_status(False)
            log.exception(f"Failed to initialize device: {e}")
            QMessageBox.warning(self, "Warning", f"Failed to initialize device: {e}")

    def _update_device_status(self, connected: bool):
        """Update device connection status display"""
        if connected:
            self._device_status_label.setText("Device: Connected")
            self._device_status_label.setStyleSheet("color: green;")
        else:
            self._device_status_label.setText("Device: Disconnected")
            self._device_status_label.setStyleSheet("color: red;")

    def _get_settings_path(self) -> Path:
        """Return the local settings file path for source and frozen builds."""
        if getattr(sys, "frozen", False):
            return Path(sys.executable).resolve().parent / "last_params.json"
        return Path(__file__).resolve().parents[2] / "last_params.json"

    def _merge_dict_into_dataclass(self, target, values: Dict[str, Any]):
        """Best-effort dataclass merge used by local settings restore."""
        if not isinstance(values, dict):
            return
        for field in fields(target):
            if field.name not in values:
                continue
            current_value = getattr(target, field.name)
            new_value = values[field.name]
            if is_dataclass(current_value) and isinstance(new_value, dict):
                self._merge_dict_into_dataclass(current_value, new_value)
            else:
                setattr(target, field.name, new_value)

    def _set_combo_to_data(self, combo: QComboBox, value: Any):
        """Set combo-box current item by user data when available."""
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return


    @staticmethod
    def _length_seconds_to_frames(length_s: float, scan_rate: int) -> int:
        """Convert a positive time length in seconds to runtime frame count."""
        frames = int(round(float(length_s) * max(1, int(scan_rate))))
        return max(1, frames)

    @staticmethod
    def _length_has_integer_frames(length_s: float, scan_rate: int) -> tuple[bool, int]:
        """Return whether length_s maps exactly to an integer frame count."""
        frames_float = float(length_s) * max(1, int(scan_rate))
        frames = int(round(frames_float))
        return frames > 0 and abs(frames_float - frames) <= 1e-6, max(1, frames)

    @staticmethod
    def _display_refresh_interval_ms(params: AllParams) -> int:
        """Return the GUI display consumption interval derived from Length/Plot."""
        scan_rate = max(1, int(getattr(params.basic, "scan_rate", 1) or 1))
        frame_plot_num = int(getattr(params.display, "frame_plot_num", 0) or 0)
        if frame_plot_num <= 0:
            length_plot_s = float(getattr(params.display, "length_plot_s", 1.0) or 1.0)
            frame_plot_num = max(1, int(round(length_plot_s * scan_rate)))
        interval_ms = int(round(frame_plot_num / float(scan_rate) * 1000.0))
        return max(50, interval_ms)

    def _configure_display_timer(self, params: AllParams) -> None:
        """Apply the current Length/Plot value to the latest-snapshot GUI timer."""
        if not hasattr(self, "_display_timer"):
            return
        interval_ms = self._display_refresh_interval_ms(params)
        previous = getattr(self, "_display_timer_interval_ms", None)
        self._display_timer_interval_ms = interval_ms
        self._display_timer.start(max(1000, interval_ms * 2))
        if previous != interval_ms:
            length_plot_s = frame_plot_num = None
            try:
                length_plot_s = float(getattr(params.display, "length_plot_s", 0.0) or 0.0)
                frame_plot_num = int(getattr(params.display, "frame_plot_num", 0) or 0)
            except Exception:
                pass
            log.info(
                "GUI display target cadence set to %d ms; watchdog=%d ms (length_plot_s=%s, plot_frames=%s)",
                interval_ms,
                max(1000, interval_ms * 2),
                f"{length_plot_s:.3f}" if length_plot_s is not None else "?",
                frame_plot_num if frame_plot_num is not None else "?",
            )

    def _sync_length_frame_fields(self, params: AllParams) -> None:
        """Derive legacy/runtime frame fields from the Length/... second settings."""
        scan_rate = max(1, int(params.basic.scan_rate))
        load_frames = self._length_seconds_to_frames(params.display.length_load_s, scan_rate)
        plot_frames = self._length_seconds_to_frames(params.display.length_plot_s, scan_rate)
        save_frames = self._length_seconds_to_frames(params.save.length_save_s, scan_rate)
        file_frames = self._length_seconds_to_frames(params.save.length_file_s, scan_rate)
        comm_frames = self._length_seconds_to_frames(params.comm.length_comm_s, scan_rate)

        params.display.frame_load_num = load_frames
        params.display.frame_plot_num = plot_frames
        params.comm.comm_frame_num = comm_frames

    def _length_frame_summary(self, params: Optional[AllParams] = None) -> tuple[int, int, int, int, int]:
        """Return derived frame counts: load, plot, save, file, comm."""
        params = params or self._collect_params()
        return (
            max(1, int(params.display.frame_load_num)),
            max(1, int(params.display.frame_plot_num)),
            self._length_seconds_to_frames(params.save.length_save_s, params.basic.scan_rate),
            self._length_seconds_to_frames(params.save.length_file_s, params.basic.scan_rate),
            max(1, int(params.comm.comm_frame_num)),
        )

    def _on_length_settings_changed(self, *_args):
        """Refresh dependent hints after acquisition length settings change."""
        if not self._is_acquisition_running():
            try:
                self.params = self._collect_params()
                self._configure_display_timer(self.params)
            except Exception:
                pass
        self._update_bz_setting_hints()
        self._update_file_estimates()
        self._sync_tcp_tab3_availability()

    def _apply_params_to_ui(self, params: AllParams):
        """Apply restored parameters back to the UI controls."""
        if params.basic.clk_src == ClockSource.EXTERNAL:
            self.clk_external_radio.setChecked(True)
        else:
            self.clk_internal_radio.setChecked(True)

        if params.basic.trig_dir == TriggerDirection.INPUT:
            self.trig_in_radio.setChecked(True)
        else:
            self.trig_out_radio.setChecked(True)

        self.scan_rate_spin.setValue(params.basic.scan_rate)
        self.pulse_width_spin.setValue(params.basic.pulse_width_ns)
        self.point_num_spin.setValue(params.basic.point_num_per_scan)
        self.bypass_spin.setValue(params.basic.bypass_point_num)
        self.center_freq_spin.setValue(params.basic.center_freq_mhz)

        self._set_combo_to_data(self.channel_combo, params.upload.channel_num)
        self._set_combo_to_data(self.data_source_combo, params.upload.data_source)
        self._set_combo_to_data(self.data_rate_combo, params.upload.data_rate)

        self._set_combo_to_data(self.rate2phase_combo, params.phase_demod.rate2phase)
        self.space_avg_spin.setValue(params.phase_demod.space_avg_order)
        self.merge_points_spin.setValue(params.phase_demod.merge_point_num)
        self.crop_distance_start_spin.setValue(params.phase_demod.crop_distance_start)
        self.crop_distance_end_spin.setValue(params.phase_demod.crop_distance_end)
        self.diff_order_spin.setValue(params.phase_demod.diff_order)
        self.detrend_bw_spin.setValue(params.phase_demod.detrend_bw)
        self.polar_div_check.setChecked(params.phase_demod.polarization_diversity)

        if params.display.mode == DisplayMode.SPACE:
            self.mode_space_radio.setChecked(True)
        else:
            self.mode_time_radio.setChecked(True)
        self.region_index_spin.setValue(params.display.region_index)
        self.length_load_spin.setValue(float(getattr(params.display, "length_load_s", 0.2) or 0.2))
        self.length_plot_spin.setValue(float(getattr(params.display, "length_plot_s", 1.0) or 1.0))
        self.spectrum_enable_check.setChecked(params.display.spectrum_enable)
        self.rad_check.setChecked(params.display.rad_enable)
        self.waveform_enable_check.setChecked(params.display.waveform_plot_enabled)
        self.monitor_enable_check.setChecked(params.display.monitor_plot_enabled)
        self._filter_spec_text = str(getattr(params.time_space, "filter_spec", "1-")).strip()
        self._filter_enabled = bool(getattr(params.time_space, "filter_enabled", False))
        self.filter_spec_edit.setText(self._filter_spec_text)
        self.filter_btn.setChecked(self._filter_enabled)
        self._set_shared_filter_error("")

        if self.time_space_widget is not None:
            self.time_space_widget.set_parameters(
                {
                    "window_frames": params.time_space.window_frames,
                    "distance_range_start": params.time_space.distance_range_start,
                    "distance_range_end": params.time_space.distance_range_end,
                    "time_downsample": params.time_space.time_downsample,
                    "space_downsample": params.time_space.space_downsample,
                    "colormap_type": params.time_space.colormap_type,
                    "vmin": params.time_space.vmin,
                    "vmax": params.time_space.vmax,
                    "filter_enabled": getattr(params.time_space, "filter_enabled", False),
                    "filter_spec": getattr(params.time_space, "filter_spec", "1-"),
                }
            )
            self.time_space_widget.set_scan_rate(params.basic.scan_rate)
            self._sync_shared_filter_settings(reset_tab1=False)

        self.save_path_edit.setText(params.save.path)
        self.length_save_spin.setValue(float(getattr(params.save, "length_save_s", 1.0) or 1.0))
        self.length_file_spin.setValue(float(getattr(params.save, "length_file_s", 10.0) or 10.0))
        self.save_downsample_spin.setValue(max(1, int(getattr(params.save, "storage_downsample_factor", 1) or 1)))
        self.overwrite_oldest_check.setChecked(
            bool(getattr(params.save, "overwrite_oldest_when_full", False))
        )
        self._set_combo_to_data(self.storage_format_combo, getattr(params.save, "storage_format", STORAGE_FORMAT_BIN))
        self.bz_zstd_level_spin.setValue(max(1, int(getattr(params.save, "bz_zstd_level", 3) or 3)))
        self.bz_bitshuffle_block_spin.setValue(max(1, int(getattr(params.save, "bz_bitshuffle_block_values", 65536) or 65536)))
        self.bz_compression_workers_spin.setValue(max(1, int(getattr(params.save, "bz_compression_workers", 4) or 4)))
        if hasattr(params, "comm"):
            self.tab3_length_comm_spin.setValue(float(getattr(params.comm, "length_comm_s", 1.0) or 1.0))
            self.tab3_comm_enable_check.setChecked(bool(getattr(params.comm, "enabled", False)))
            self.tab3_server_ip_edit.setText(str(getattr(params.comm, "server_ip", "169.255.1.2") or "169.255.1.2"))
            self.tab3_server_port_spin.setValue(int(getattr(params.comm, "server_port", 3678) or 3678))
            self.tab3_channel_start_spin.setValue(int(getattr(params.comm, "channel_start", 50) or 0))
            self.tab3_channel_end_spin.setValue(int(getattr(params.comm, "channel_end", 100) or 0))
            self.tab3_time_downsample_spin.setValue(max(1, int(getattr(params.comm, "time_downsample", 1) or 1)))
            self.tab3_space_downsample_spin.setValue(max(1, int(getattr(params.comm, "space_downsample", 1) or 1)))
        self._update_storage_format_control_states()
        self._update_bz_setting_hints()
        self._set_save_enable_checked(params.save.enable)
        self._sync_display_control_states()
        self._update_phase_crop_controls()
        self._update_calculated_values()
        self._update_file_estimates()

    def _save_local_params(self):
        """Persist the current UI parameters to last_params.json."""
        try:
            params = self._collect_params()
            payload = {
                "version": 1,
                "params": asdict(params),
            }
            self._settings_path.parent.mkdir(parents=True, exist_ok=True)
            self._settings_path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            self.params = params
            log.info(f"Saved local parameters to {self._settings_path}")
        except Exception as e:
            log.warning(f"Failed to save local parameters: {e}")

    def _load_local_params(self):
        """Restore the last saved UI parameters when available."""
        if not self._settings_path.exists():
            log.info(f"Local parameter file not found, using defaults: {self._settings_path}")
            self._sync_display_control_states()
            self._update_calculated_values()
            self._update_file_estimates()
            return

        try:
            payload = json.loads(self._settings_path.read_text(encoding="utf-8"))
            params_data = payload.get("params", payload)
            params = AllParams()
            self._merge_dict_into_dataclass(params, params_data)
            self._apply_params_to_ui(params)
            self.params = self._collect_params()
            log.info(f"Loaded local parameters from {self._settings_path}")
        except Exception as e:
            log.warning(f"Failed to load local parameters, using defaults: {e}")
            self._sync_display_control_states()
            self._update_calculated_values()
            self._update_file_estimates()

    def _collect_params(self) -> AllParams:
        """Collect current parameter values from UI"""
        params = AllParams()

        # Basic params
        params.basic.clk_src = ClockSource.EXTERNAL if self.clk_external_radio.isChecked() else ClockSource.INTERNAL
        params.basic.trig_dir = TriggerDirection.INPUT if self.trig_in_radio.isChecked() else TriggerDirection.OUTPUT
        params.basic.scan_rate = self.scan_rate_spin.value()
        params.basic.pulse_width_ns = self.pulse_width_spin.value()
        params.basic.point_num_per_scan = self.point_num_spin.value()
        params.basic.bypass_point_num = self.bypass_spin.value()
        params.basic.center_freq_mhz = self.center_freq_spin.value()

        # Upload params
        params.upload.channel_num = self.channel_combo.currentData()
        params.upload.data_source = self.data_source_combo.currentData()
        params.upload.data_rate = self.data_rate_combo.currentData()

        # Phase demod params
        params.phase_demod.rate2phase = self.rate2phase_combo.currentData()
        params.phase_demod.space_avg_order = self.space_avg_spin.value()
        params.phase_demod.merge_point_num = self.merge_points_spin.value()
        params.phase_demod.crop_distance_start = self.crop_distance_start_spin.value()
        params.phase_demod.crop_distance_end = self.crop_distance_end_spin.value()
        params.phase_demod.diff_order = self.diff_order_spin.value()
        params.phase_demod.detrend_bw = self.detrend_bw_spin.value()
        params.phase_demod.polarization_diversity = self.polar_div_check.isChecked()

        # Display params
        # Display mode selection (绉婚櫎TIME_SPACE閫夐」锛岀敱PLOT鎸夐挳鎺у埗)
        if self.mode_space_radio.isChecked():
            params.display.mode = DisplayMode.SPACE
        else:
            params.display.mode = DisplayMode.TIME

        params.display.region_index = self.region_index_spin.value()
        params.display.length_load_s = self.length_load_spin.value()
        params.display.length_plot_s = self.length_plot_spin.value()
        params.display.spectrum_enable = self.spectrum_enable_check.isChecked()
        # Note: PSD mode now automatically determined by data_type (removed psd_enable)
        params.display.rad_enable = self.rad_check.isChecked()
        params.display.waveform_plot_enabled = self.waveform_enable_check.isChecked()
        params.display.monitor_plot_enabled = self.monitor_enable_check.isChecked()

        # Time-Space parameters (get from widget if available)
        if self.time_space_widget is not None:
            ts_params = self.time_space_widget.get_parameters()
            params.time_space.window_frames = ts_params['window_frames']
            params.time_space.distance_range_start = ts_params['distance_range_start']
            params.time_space.distance_range_end = ts_params['distance_range_end']
            params.time_space.time_downsample = ts_params['time_downsample']
            params.time_space.space_downsample = ts_params['space_downsample']
            params.time_space.colormap_type = ts_params['colormap_type']
            params.time_space.vmin = ts_params['vmin']
            params.time_space.vmax = ts_params['vmax']

        filter_enabled, filter_spec = self._get_shared_filter_settings()
        params.time_space.filter_enabled = filter_enabled
        params.time_space.filter_spec = filter_spec

        # Save params
        params.save.enable = self.save_enable_check.isChecked()
        params.save.path = self.save_path_edit.text()
        params.save.length_save_s = self.length_save_spin.value()
        params.save.length_file_s = self.length_file_spin.value()
        params.save.storage_downsample_factor = self.save_downsample_spin.value()
        params.save.storage_format = self._get_selected_storage_format()
        params.save.overwrite_oldest_when_full = self.overwrite_oldest_check.isChecked()
        params.save.bz_zstd_level = self.bz_zstd_level_spin.value()
        params.save.bz_bitshuffle_block_values = self.bz_bitshuffle_block_spin.value()
        params.save.bz_compression_workers = self.bz_compression_workers_spin.value()
        params.comm.length_comm_s = self.tab3_length_comm_spin.value()
        params.comm.enabled = self.tab3_comm_enable_check.isChecked()
        params.comm.server_ip = self.tab3_server_ip_edit.text().strip()
        params.comm.server_port = self.tab3_server_port_spin.value()
        params.comm.channel_start = self.tab3_channel_start_spin.value()
        params.comm.channel_end = self.tab3_channel_end_spin.value()
        params.comm.time_downsample = self.tab3_time_downsample_spin.value()
        params.comm.space_downsample = self.tab3_space_downsample_spin.value()
        self._sync_length_frame_fields(params)

        return params

    def _validate_params(self, params: AllParams) -> tuple[bool, str]:
        """Validate parameters before starting"""
        # Validate point number
        valid, msg = validate_point_num(
            params.basic.point_num_per_scan,
            params.upload.channel_num
        )
        if not valid:
            return False, msg

        length_checks = [
            ("Length/Load", params.display.length_load_s),
            ("Length/Plot", params.display.length_plot_s),
            ("Length/Save", params.save.length_save_s),
            ("Length/File", params.save.length_file_s),
            ("Length/Comm", params.comm.length_comm_s),
        ]
        derived_frames: Dict[str, int] = {}
        for name, length_s in length_checks:
            if float(length_s) <= 0:
                return False, f"{name} must be greater than 0 s."
            exact, frames = self._length_has_integer_frames(length_s, params.basic.scan_rate)
            if not exact:
                return False, f"{name} must map to an integer frame count at Scan={params.basic.scan_rate} Hz."
            derived_frames[name] = frames

        load_frames = derived_frames["Length/Load"]
        if derived_frames["Length/Plot"] % load_frames != 0:
            return False, "Length/Plot must be an integer multiple of Length/Load."
        if derived_frames["Length/Save"] % load_frames != 0:
            return False, "Length/Save must be an integer multiple of Length/Load."
        if derived_frames["Length/Comm"] % load_frames != 0:
            return False, "Length/Comm must be an integer multiple of Length/Load."
        if derived_frames["Length/File"] % derived_frames["Length/Save"] != 0:
            return False, "Length/File must be an integer multiple of Length/Save."

        # Raw data source with 4 channels not supported
        if params.upload.data_source != DataSource.PHASE and params.upload.channel_num == 4:
            return False, "Raw data source does not support 4 channels"

        if params.upload.data_source == DataSource.PHASE and params.upload.channel_num == 1:
            total_points = calculate_phase_point_num(
                params.basic.point_num_per_scan,
                params.phase_demod.merge_point_num,
            )
            crop_start = params.phase_demod.crop_distance_start
            crop_end = params.phase_demod.crop_distance_end
            if crop_start < 0 or crop_end < 0:
                return False, "CropStart/CropEnd must be >= 0"
            if not (crop_start == 0 and crop_end == 0) and crop_start >= total_points:
                return False, f"CropStart must be smaller than total PHASE points ({total_points})"
            if crop_end > 0 and crop_end <= crop_start:
                return False, "CropEnd must be greater than CropStart"

        if params.save.storage_downsample_factor <= 0:
            return False, "Save DS must be greater than 0."

        if params.save.storage_format not in {STORAGE_FORMAT_BIN, STORAGE_FORMAT_BITSHUFFLE_ZSTD}:
            return False, "Storage format must be BIN or Bitshuffle+Zstd."
        if params.save.bz_zstd_level < 1 or params.save.bz_zstd_level > 22:
            return False, "Zstd Level must be between 1 and 22."
        if params.save.bz_bitshuffle_block_values <= 0:
            return False, "Bitshuffle Block must be greater than 0."
        if params.save.bz_compression_workers < 1 or params.save.bz_compression_workers > 16:
            return False, "BZ Workers must be between 1 and 16."

        return True, ""

    def _is_phase_spatial_crop_active(self, params: Optional[AllParams] = None) -> bool:
        """Return whether single-channel PHASE spatial crop should be applied."""
        params = params or self.params
        return (
            params.upload.data_source == DataSource.PHASE
            and params.upload.channel_num == 1
        )

    def _get_phase_point_count_after_merge(self, params: Optional[AllParams] = None) -> int:
        """Return PHASE points per frame before software crop."""
        params = params or self.params
        return calculate_phase_point_num(
            params.basic.point_num_per_scan,
            params.phase_demod.merge_point_num,
        )

    def _get_effective_phase_point_count(self, params: Optional[AllParams] = None) -> int:
        """Return PHASE points per frame after software crop."""
        params = params or self.params
        base_count = self._get_phase_point_count_after_merge(params)
        if not self._is_phase_spatial_crop_active(params):
            return base_count
        return calculate_cropped_point_count(
            base_count,
            params.phase_demod.crop_distance_start,
            params.phase_demod.crop_distance_end,
        )

    def get_tab3_comm_settings(self) -> Dict[str, Any]:
        """Return the current TCP communication settings."""
        return {
            "enabled": self.tab3_comm_enable_check.isChecked(),
            "server_ip": self.tab3_server_ip_edit.text().strip(),
            "server_port": self.tab3_server_port_spin.value(),
            "channel_start": self.tab3_channel_start_spin.value(),
            "channel_end": self.tab3_channel_end_spin.value(),
            "time_downsample": self.tab3_time_downsample_spin.value(),
            "space_downsample": self.tab3_space_downsample_spin.value(),
            "comm_frames": self._length_seconds_to_frames(self.tab3_length_comm_spin.value(), self.scan_rate_spin.value()),
            "reconnect_interval_s": 1.0,
            "queue_max_packets": 8,
        }

    def _on_tcp_tab3_settings_changed(self, *_args):
        """Refresh Tab3 availability and static field hints after one setting change."""
        self._tcp_settings_snapshot = self.get_tab3_comm_settings()
        self._sync_tcp_tab3_availability()

    def _sync_tcp_tab3_availability(self, *_args):
        """Publish current communication availability using the latest acquisition params."""
        try:
            params = self._collect_params()
        except Exception:
            return
        self.tcp_tab3_manager.update_enabled(self.tab3_comm_enable_check.isChecked(), params)
        self._update_tab3_comm_hints(params)

    def _update_tab3_comm_hints(self, params: AllParams):
        """Update read-only protocol hints shown on Tab3."""
        point_num_after_merge = max(1, self._get_effective_phase_point_count(params))
        channel_start = max(0, min(self.tab3_channel_start_spin.value(), point_num_after_merge - 1))
        channel_end = max(channel_start, min(self.tab3_channel_end_spin.value(), point_num_after_merge - 1))
        selected_count = len(range(channel_start, channel_end + 1, max(1, self.tab3_space_downsample_spin.value())))

        sample_rate_text = "Invalid"
        duration_text = "-"
        data_bytes_text = "-"
        if params.basic.scan_rate > 0 and params.basic.scan_rate % max(1, self.tab3_time_downsample_spin.value()) == 0:
            sample_rate_hz = params.basic.scan_rate // self.tab3_time_downsample_spin.value()
            comm_frames = max(1, int(getattr(params.comm, "comm_frame_num", params.display.frame_load_num)))
            samples_per_channel = len(range(0, comm_frames, max(1, self.tab3_time_downsample_spin.value())))
            sample_rate_text = f"{sample_rate_hz} Hz"
            packet_duration = samples_per_channel / float(sample_rate_hz)
            duration_text = f"{packet_duration:.6f} s"
            data_bytes = selected_count * samples_per_channel * 8
            data_bytes_text = str(data_bytes)

        self.tab3_comm_channel_count_label.setText(str(selected_count))
        self.tab3_comm_sample_rate_label.setText(sample_rate_text)
        self.tab3_comm_duration_label.setText(duration_text)
        self.tab3_comm_data_bytes_label.setText(data_bytes_text)

    def update_tab3_comm_availability(self, payload: Dict[str, Any]):
        """Update whether communication is currently allowed."""
        available = bool(payload.get("available", False))
        reason = str(payload.get("reason", ""))
        self.tab3_availability_label.setText(reason)
        self.tab3_availability_label.setStyleSheet(
            "color: green; font-weight: bold;" if available else "color: #b36b00; font-weight: bold;"
        )

    def update_tab3_comm_status(self, payload: Dict[str, Any]):
        """Update connection state and human-readable status text."""
        state = str(payload.get("state", "idle")).capitalize()
        connected = bool(payload.get("connected", False))
        self.tab3_comm_state_label.setText(state)
        self.tab3_comm_state_label.setStyleSheet(
            "color: green; font-weight: bold;" if connected else "color: #555; font-weight: bold;"
        )
        self.tab3_comm_message_label.setText(str(payload.get("message", "-")))

    def update_tab3_comm_statistics(self, payload: Dict[str, Any]):
        """Update Tab3 packet counters and the latest outgoing header summary."""
        self.tab3_acquired_packets_label.setText(str(payload.get("acquired_packets", 0)))
        self.tab3_queued_packets_label.setText(str(payload.get("queued_packets", 0)))
        self.tab3_sent_packets_label.setText(str(payload.get("sent_packets", 0)))
        self.tab3_dropped_packets_label.setText(str(payload.get("dropped_packets", 0)))
        last_comm = payload.get("last_comm_count", -1)
        self.tab3_last_comm_count_label.setText("-" if int(last_comm) < 0 else str(last_comm))
        self.tab3_bytes_sent_label.setText(str(payload.get("bytes_sent", 0)))
        self.tab3_comm_channel_count_label.setText(str(payload.get("channel_count", self.tab3_comm_channel_count_label.text())))
        sample_rate = payload.get("sample_rate_hz", 0)
        self.tab3_comm_sample_rate_label.setText("-" if not sample_rate else f"{sample_rate} Hz")
        duration = float(payload.get("packet_duration_seconds", 0.0))
        self.tab3_comm_duration_label.setText("-" if duration <= 0 else f"{duration:.6f} s")
        data_bytes = int(payload.get("data_bytes", 0))
        self.tab3_comm_data_bytes_label.setText("-" if data_bytes <= 0 else str(data_bytes))
        last_error = str(payload.get("last_error", "")).strip()
        if last_error:
            self.tab3_comm_last_error_label.setText(last_error)

    def _on_tcp_tab3_error(self, message: str):
        """Show the latest communication error without interrupting acquisition."""
        self.tab3_comm_last_error_label.setText(message)
        self.statusBar.showMessage(f"TCP Comm: {message}", 5000)

    def _configure_device(self, params: AllParams) -> bool:
        """Configure device with parameters"""
        if self.api is None:
            return False

        log.info(
            "Configuring device: clk=%s, trig=%s, scan_rate=%d, pulse_ns=%d, points=%d, "
            "bypass=%d, center_mhz=%d, channels=%d, data_source=%d, data_rate=%d, "
            "rate2phase=%d, space_avg=%d, merge=%d, diff=%d, detrend_bw=%.3f, polar_div=%d",
            params.basic.clk_src, params.basic.trig_dir, params.basic.scan_rate,
            params.basic.pulse_width_ns, params.basic.point_num_per_scan,
            params.basic.bypass_point_num, params.basic.center_freq_mhz,
            params.upload.channel_num, params.upload.data_source, params.upload.data_rate,
            params.phase_demod.rate2phase, params.phase_demod.space_avg_order,
            params.phase_demod.merge_point_num, params.phase_demod.diff_order,
            params.phase_demod.detrend_bw, int(params.phase_demod.polarization_diversity),
        )
        try:
            self.api.set_clk_src(params.basic.clk_src)
            self.api.set_trig_dir(params.basic.trig_dir)
            self.api.set_scan_rate(params.basic.scan_rate)
            self.api.set_pulse_width(params.basic.pulse_width_ns)
            self.api.set_point_num_per_scan(params.basic.point_num_per_scan)
            self.api.set_bypass_point_num(params.basic.bypass_point_num)
            self.api.set_center_freq(params.basic.center_freq_mhz * 1000000)

            self.api.set_upload_data_param(
                params.upload.channel_num,
                params.upload.data_source,
                params.upload.data_rate
            )

            self.api.set_phase_dem_param(
                params.phase_demod.rate2phase,
                params.phase_demod.space_avg_order,
                params.phase_demod.merge_point_num,
                params.phase_demod.diff_order,
                params.phase_demod.detrend_bw,
                params.phase_demod.polarization_diversity
            )

            # Allocate buffers
            self.api.allocate_buffers(
                params.basic.point_num_per_scan,
                params.upload.channel_num,
                params.display.frame_load_num,
                params.phase_demod.merge_point_num,
                params.upload.data_source == DataSource.PHASE
            )

            log.info("Device configured successfully")
            return True

        except PCIe7821Error as e:
            log.error(f"Failed to configure device: {e}")
            QMessageBox.critical(self, "Error", f"Failed to configure device: {e}")
            return False

    # ----- ACQUISITION CONTROL (START / STOP) -----

    def _is_acquisition_running(self) -> bool:
        """Return True while the active acquisition thread is running."""
        thread = self.acq_thread
        return thread is not None and bool(getattr(thread, "is_running", False))

    @staticmethod
    def _downsampled_point_count(point_count: int, factor: int) -> int:
        """Return the number of points kept by storage-only point picking."""
        point_count = max(0, int(point_count))
        factor = max(1, int(factor))
        if point_count <= 0:
            return 0
        return (point_count + factor - 1) // factor

    def _get_storage_downsample_factor(self, params: Optional[AllParams] = None) -> int:
        """Return the storage-only downsample factor from captured parameters."""
        params = params or self.params
        return max(1, int(getattr(params.save, "storage_downsample_factor", 1) or 1))

    def _get_save_source_points_per_frame(self, params: Optional[AllParams] = None) -> int:
        """Return saved-data frame width before the storage-only downsample."""
        params = params or self.params
        if params.upload.data_source == DataSource.PHASE:
            return self._get_effective_phase_point_count(params)
        return params.basic.point_num_per_scan

    def _get_save_points_per_frame(self, params: Optional[AllParams] = None) -> int:
        """Return the frame width written to disk and encoded in saved-data filenames."""
        params = params or self.params
        return self._downsampled_point_count(
            self._get_save_source_points_per_frame(params),
            self._get_storage_downsample_factor(params),
        )


    def _get_selected_storage_format(self) -> str:
        """Return the storage format selected on Tab4."""
        if hasattr(self, "storage_format_combo"):
            value = self.storage_format_combo.currentData()
            if value:
                return str(value)
        return str(getattr(self.params.save, "storage_format", STORAGE_FORMAT_BIN) or STORAGE_FORMAT_BIN)

    def _update_storage_format_control_states(self):
        """Enable storage parameters according to format and active save state."""
        if not hasattr(self, "storage_format_combo"):
            return
        active = self.data_saver is not None and self.data_saver.is_running
        editable = not active
        storage_format = self._get_selected_storage_format()
        bz_selected = storage_format == STORAGE_FORMAT_BITSHUFFLE_ZSTD
        self.storage_format_combo.setEnabled(editable)
        for widget in [
            self.length_save_spin,
            self.length_file_spin,
            self.save_downsample_spin,
            self.overwrite_oldest_check,
        ]:
            widget.setEnabled(editable)
        for label in [
            getattr(self, "length_save_label", None),
            getattr(self, "length_file_label", None),
            getattr(self, "save_downsample_label", None),
        ]:
            if label is not None:
                label.setEnabled(editable)
        for widget in [
            self.bz_zstd_level_spin,
            self.bz_bitshuffle_block_spin,
            self.bz_compression_workers_spin,
        ]:
            widget.setEnabled(editable and bz_selected)
        for label in [
            getattr(self, "bz_zstd_level_label", None),
            getattr(self, "bz_bitshuffle_block_label", None),
            getattr(self, "bz_compression_workers_label", None),
        ]:
            if label is not None:
                label.setEnabled(editable and bz_selected)

    def _update_bz_setting_hints(self):
        """Refresh resolved Length/... frame-count hints."""
        if not hasattr(self, "bz_packet_hint_label"):
            return
        try:
            params = self._collect_params()
            load_frames, plot_frames, save_frames, file_frames, comm_frames = self._length_frame_summary(params)
            scan_rate = max(1, int(params.basic.scan_rate))
            save_packets = max(1, file_frames // max(1, save_frames))
            self.length_load_hint_label.setText(
                f"Load: {load_frames}fr/{params.display.length_load_s:.3f}s, "
                f"Plot: {plot_frames}fr/{params.display.length_plot_s:.3f}s, "
                f"Comm: {comm_frames}fr/{params.comm.length_comm_s:.3f}s"
            )
            self.bz_packet_hint_label.setText(
                f"Save: {save_frames}fr/{params.save.length_save_s:.3f}s, "
                f"File: {file_frames}fr/{params.save.length_file_s:.3f}s (~{save_packets} packets), "
                f"Scan={scan_rate}Hz, BZ workers={params.save.bz_compression_workers}"
            )
        except Exception:
            self.length_load_hint_label.setText("Load: --")
            self.bz_packet_hint_label.setText("Save: --")

    def _on_storage_settings_changed(self, *_args):
        """Keep pending save settings synchronized with Tab4."""
        if hasattr(self, "storage_format_combo"):
            self.params.save.storage_format = self._get_selected_storage_format()
            self.params.save.length_save_s = self.length_save_spin.value()
            self.params.save.length_file_s = self.length_file_spin.value()
            self.params.save.bz_zstd_level = self.bz_zstd_level_spin.value()
            self.params.save.bz_bitshuffle_block_values = self.bz_bitshuffle_block_spin.value()
            self.params.save.bz_compression_workers = self.bz_compression_workers_spin.value()
            self._sync_length_frame_fields(self.params)
        self._update_storage_format_control_states()
        self._update_bz_setting_hints()
        self._update_file_estimates()

    def _set_save_enable_checked(self, checked: bool):
        """Update the save toggle button without recursively starting/stopping storage."""
        previous = self.save_enable_check.blockSignals(True)
        self.save_enable_check.setChecked(checked)
        self.save_enable_check.blockSignals(previous)
        self._update_save_button_style()

    def _set_storage_downsample_enabled(self, enabled: bool):
        """Keep storage format and downsampling fixed while a save file is open."""
        if hasattr(self, "save_downsample_spin"):
            self.save_downsample_spin.setEnabled(enabled)
        if hasattr(self, "save_downsample_label"):
            self.save_downsample_label.setEnabled(enabled)
        self._update_storage_format_control_states()

    def _update_save_button_style(self):
        """Style the save toggle like the other small action buttons."""
        if not hasattr(self, "save_enable_check"):
            return

        saver = self.data_saver
        active = saver is not None and saver.is_running
        checked = self.save_enable_check.isChecked()
        if active:
            self.save_enable_check.setText("SAVING")
            self.save_enable_check.setStyleSheet(
                """
                QPushButton {
                    background-color: #1976D2;
                    color: white;
                    border: 1px solid #1565C0;
                    border-radius: 3px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #1565C0;
                }
                """
            )
        elif checked:
            self.save_enable_check.setText("SAVE ON")
            self.save_enable_check.setStyleSheet(
                """
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    border: 1px solid #3d8b40;
                    border-radius: 3px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #45a049;
                }
                """
            )
        else:
            self.save_enable_check.setText("SAVE")
            self.save_enable_check.setStyleSheet(
                """
                QPushButton {
                    background-color: #9E9E9E;
                    color: white;
                    border: 1px solid #757575;
                    border-radius: 3px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #757575;
                }
                """
            )

    def _refresh_save_status_display(self):
        """Refresh save status text and this-run file count."""
        saver = self.data_saver
        active = saver is not None and saver.is_running
        if saver is not None:
            files_created = int(getattr(saver, "total_files_created", 0) or 0)
            self._save_file_count_this_run = files_created
        else:
            files_created = int(getattr(self, "_save_file_count_this_run", 0) or 0)

        if hasattr(self, "saved_file_count_label"):
            self.saved_file_count_label.setText(f"Files: {files_created}")

        if hasattr(self, "save_status_label"):
            if active:
                filename = getattr(saver, "current_filename", "")
                self.save_status_label.setText(f"Save: {filename}" if filename else "Save: On")
            elif self.params.save.enable:
                self.save_status_label.setText("Save: Ready")
            else:
                self.save_status_label.setText("Save: Off")

        if hasattr(self, "bz_realtime_status_label"):
            if active and saver is not None and hasattr(saver, "get_diagnostics_snapshot"):
                snapshot = saver.get_diagnostics_snapshot()
                if snapshot.get("format") == "bz":
                    worker_alive = snapshot.get("compression_threads_alive", 0)
                    worker_total = snapshot.get("compression_workers", 0)
                    slow_packets = snapshot.get('slow_compression_packet_count', 0)
                    not_rt = snapshot['compression_not_realtime_count']
                    self.bz_realtime_status_label.setText(
                        f"BZ: raw={snapshot['raw_queue_size']}/{snapshot['buffer_size']} "
                        f"pkt={snapshot.get('packet_queue_size', 0)}/{snapshot.get('packet_queue_size_max', 0)} "
                        f"cmp={snapshot['compressed_queue_size']}/{snapshot['compressed_queue_size_max']} "
                        f"w={worker_alive}/{worker_total} drop={snapshot['dropped_blocks']} "
                        f"slow={slow_packets} notRT={not_rt}"
                    )
                    has_queue_fault = (
                        snapshot["dropped_blocks"]
                        or not_rt
                        or snapshot.get("packet_queue_full_count", 0)
                        or snapshot.get("compressed_queue_full_count", 0)
                    )
                    color = "#b00020" if has_queue_fault else ("#b26a00" if slow_packets else "green")
                    self.bz_realtime_status_label.setStyleSheet(f"color: {color};")
                else:
                    self.bz_realtime_status_label.setText("BZ: inactive")
                    self.bz_realtime_status_label.setStyleSheet("color: #666666;")
            else:
                self.bz_realtime_status_label.setText("BZ: idle")
                self.bz_realtime_status_label.setStyleSheet("color: #666666;")

        self._update_save_button_style()

    def _start_data_saver(self, params: Optional[AllParams] = None) -> bool:
        """Start storage immediately, creating the target directory if needed."""
        if self.data_saver is not None and self.data_saver.is_running:
            return True

        params = params or self.params
        save_path = self.save_path_edit.text().strip() or params.save.path
        storage_downsample_factor = self.save_downsample_spin.value()
        storage_format = self._get_selected_storage_format()
        length_save_s = self.length_save_spin.value()
        length_file_s = self.length_file_spin.value()
        bz_zstd_level = self.bz_zstd_level_spin.value()
        bz_bitshuffle_block_values = self.bz_bitshuffle_block_spin.value()
        bz_compression_workers = self.bz_compression_workers_spin.value()
        overwrite_oldest_when_full = self.overwrite_oldest_check.isChecked()

        params.save.enable = True
        params.save.path = save_path
        params.save.length_save_s = length_save_s
        params.save.length_file_s = length_file_s
        params.save.storage_downsample_factor = storage_downsample_factor
        params.save.storage_format = storage_format
        params.save.overwrite_oldest_when_full = overwrite_oldest_when_full
        params.save.bz_zstd_level = bz_zstd_level
        params.save.bz_bitshuffle_block_values = bz_bitshuffle_block_values
        params.save.bz_compression_workers = bz_compression_workers
        params.comm.length_comm_s = self.tab3_length_comm_spin.value()
        self._sync_length_frame_fields(params)
        save_packet_frames = self._length_seconds_to_frames(length_save_s, params.basic.scan_rate)
        file_frames = self._length_seconds_to_frames(length_file_s, params.basic.scan_rate)
        packets_per_file = max(1, file_frames // save_packet_frames)
        source_points = self._get_save_source_points_per_frame(params)
        save_points = self._get_save_points_per_frame(params)
        channel_num = max(1, int(params.upload.channel_num or 1))
        input_item_bytes = 4 if params.upload.data_source == DataSource.PHASE else 2
        block_bytes = (
            max(1, int(params.display.frame_load_num))
            * source_points
            * channel_num
            * input_item_bytes
        )
        packet_bytes = save_packet_frames * save_points * channel_num * 4
        queue_caps = calculate_storage_queue_capacities(
            block_bytes,
            packet_bytes,
            OPTIMIZED_BUFFER_SIZES['storage_queue_frames'],
            psutil.virtual_memory().available,
        )
        input_mib_s = (
            block_bytes / 1024 / 1024 / max(0.001, float(params.display.length_load_s))
        )
        raw_backlog_s = queue_caps['raw_blocks'] * float(params.display.length_load_s)
        worker_working_set_mb = (
            bz_compression_workers * packet_bytes * 2 / 1024 / 1024
            if storage_format == STORAGE_FORMAT_BITSHUFFLE_ZSTD
            else 0.0
        )
        log.info(
            f"Storage queue sizing: block_mb={block_bytes / 1024 / 1024:.2f}, "
            f"packet_mb={packet_bytes / 1024 / 1024:.2f}, input_mib_s={input_mib_s:.1f}, "
            f"raw_blocks={queue_caps['raw_blocks']}, raw_backlog_s={raw_backlog_s:.1f}, "
            f"packet_items={queue_caps['packet_items']}, compressed_items={queue_caps['compressed_items']}, "
            f"queue_budget_mb={queue_caps['memory_budget_bytes'] / 1024 / 1024:.0f}, "
            f"bz_worker_working_set_est_mb={worker_working_set_mb:.0f}"
        )
        self._save_file_count_this_run = 0

        if storage_format == STORAGE_FORMAT_BITSHUFFLE_ZSTD:
            log.info(
                f"Starting Bitshuffle+Zstd data saver to {save_path}, length_save_s={length_save_s:.3f}, "
                f"length_file_s={length_file_s:.3f}, packet_frames={save_packet_frames}, "
                f"file_frames={file_frames}, load_frames={params.display.frame_load_num}, "
                f"zstd_level={bz_zstd_level}, bitshuffle_block={bz_bitshuffle_block_values}, "
                f"bz_workers={bz_compression_workers}, save_ds={storage_downsample_factor}, "
                f"overwrite_oldest={overwrite_oldest_when_full}"
            )
            saver = BitshuffleZstdFileSaver(
                save_path,
                file_duration_s=length_file_s,
                packet_frames=save_packet_frames,
                file_frames_per_file=file_frames,
                zstd_level=bz_zstd_level,
                bitshuffle_block_values=bz_bitshuffle_block_values,
                compression_workers=bz_compression_workers,
                buffer_size=queue_caps['raw_blocks'],
                packet_queue_size=queue_caps['packet_items'],
                compressed_queue_size=queue_caps['compressed_items'],
                overwrite_oldest_when_full=overwrite_oldest_when_full,
            )
        else:
            log.info(
                f"Starting packetized .bin data saver to {save_path}, length_save_s={length_save_s:.3f}, "
                f"length_file_s={length_file_s:.3f}, packet_frames={save_packet_frames}, "
                f"file_frames={file_frames}, packets_per_file={packets_per_file}, "
                f"load_frames={params.display.frame_load_num}, save_ds={storage_downsample_factor}, "
                f"overwrite_oldest={overwrite_oldest_when_full}"
            )
            saver = BlockBasedFileSaver(
                save_path,
                packet_frames=save_packet_frames,
                file_duration_s=length_file_s,
                file_frames_per_file=file_frames,
                buffer_size=queue_caps['raw_blocks'],
                overwrite_oldest_when_full=overwrite_oldest_when_full,
            )

        try:
            if storage_format == STORAGE_FORMAT_BITSHUFFLE_ZSTD:
                filename = saver.start(
                    scan_rate=params.basic.scan_rate,
                    points_per_frame=self._get_save_points_per_frame(params),
                    channel_num=params.upload.channel_num,
                    data_source=params.upload.data_source,
                    storage_downsample_factor=storage_downsample_factor,
                    source_points_per_frame=self._get_save_source_points_per_frame(params),
                )
            else:
                filename = saver.start(
                    scan_rate=params.basic.scan_rate,
                    points_per_frame=self._get_save_points_per_frame(params),
                    channel_num=params.upload.channel_num,
                    data_source=params.upload.data_source,
                    storage_downsample_factor=storage_downsample_factor,
                    source_points_per_frame=self._get_save_source_points_per_frame(params),
                )
        except Exception as exc:
            log.exception(f"Failed to start data saver: {exc}")
            try:
                saver.stop()
            except Exception as stop_exc:
                log.warning(f"Error cleaning up failed data saver: {stop_exc}")
            self.data_saver = None
            self.params.save.enable = False
            self._set_storage_downsample_enabled(True)
            self._set_save_enable_checked(False)
            self._refresh_save_status_display()
            QMessageBox.critical(self, "Storage Error", f"Failed to start data saving:\n{exc}")
            return False

        self.data_saver = saver
        self.params.save.enable = True
        self.params.save.path = save_path
        self.params.save.length_save_s = length_save_s
        self.params.save.length_file_s = length_file_s
        self.params.save.storage_downsample_factor = storage_downsample_factor
        self.params.save.storage_format = storage_format
        self.params.save.bz_zstd_level = bz_zstd_level
        self.params.save.bz_bitshuffle_block_values = bz_bitshuffle_block_values
        self.params.save.bz_compression_workers = bz_compression_workers
        self.params.save.overwrite_oldest_when_full = overwrite_oldest_when_full
        log.info(f"Data saver active: format={storage_format}, file={filename}")
        self._set_storage_downsample_enabled(False)
        self._refresh_save_status_display()
        return True

    def _stop_data_saver(self):
        """Stop the active storage worker and close the current file."""
        saver = self.data_saver
        if saver is None:
            self._set_storage_downsample_enabled(True)
            self._refresh_save_status_display()
            return

        log.debug("Stopping data saver...")
        self.data_saver = None
        try:
            saver.stop()
        except Exception as exc:
            log.warning(f"Error stopping data saver: {exc}")
        self._save_file_count_this_run = int(
            getattr(saver, "total_files_created", self._save_file_count_this_run) or 0
        )
        self._set_storage_downsample_enabled(True)
        self._refresh_save_status_display()

    @pyqtSlot(bool)
    def _on_save_enable_toggled(self, enabled: bool):
        """Apply save-toggle changes immediately when acquisition is already running."""
        self.params.save.enable = enabled
        self.params.save.path = self.save_path_edit.text().strip()
        self.params.save.length_save_s = self.length_save_spin.value()
        self.params.save.length_file_s = self.length_file_spin.value()
        self.params.save.storage_downsample_factor = self.save_downsample_spin.value()
        self.params.save.storage_format = self._get_selected_storage_format()
        self.params.save.overwrite_oldest_when_full = self.overwrite_oldest_check.isChecked()
        self.params.save.bz_zstd_level = self.bz_zstd_level_spin.value()
        self.params.save.bz_bitshuffle_block_values = self.bz_bitshuffle_block_spin.value()
        self.params.save.bz_compression_workers = self.bz_compression_workers_spin.value()
        self.params.comm.length_comm_s = self.tab3_length_comm_spin.value()
        self._sync_length_frame_fields(self.params)

        if enabled:
            if not self._is_acquisition_running():
                self._save_file_count_this_run = 0
            if self._is_acquisition_running():
                self._start_data_saver(self.params)
            self._refresh_save_status_display()
            return

        self.params.save.enable = False
        self._stop_data_saver()
        self._refresh_save_status_display()

    @pyqtSlot()
    def _on_save_path_edited(self):
        """Keep the pending save path synchronized with the UI."""
        self.params.save.path = self.save_path_edit.text().strip()

    @pyqtSlot(int)
    def _on_storage_downsample_changed(self, value: int):
        """Keep the pending storage-only downsample factor synchronized with the UI."""
        self.params.save.storage_downsample_factor = max(1, int(value))
        self._update_bz_setting_hints()
        self._update_file_estimates()

    @pyqtSlot(bool)
    def _on_storage_overwrite_changed(self, enabled: bool):
        """Keep the pending disk-overwrite policy synchronized with the UI."""
        self.params.save.overwrite_oldest_when_full = bool(enabled)

    @pyqtSlot()
    def _on_start(self):
        """Handle start button click"""
        log.info("=== START button clicked ===")

        # Collect and validate parameters
        params = self._collect_params()
        valid, msg = self._validate_params(params)
        if not valid:
            log.warning(f"Invalid parameters: {msg}")
            QMessageBox.warning(self, "Invalid Parameters", msg)
            return

        self.params = params
        self._configure_display_timer(params)
        self._save_local_params()
        if self.time_space_widget is not None:
            self.time_space_widget.set_scan_rate(params.basic.scan_rate)
        if params.upload.data_source == DataSource.PHASE:
            points_per_frame = self._get_effective_phase_point_count(params)
            bytes_per_point = 4
        else:
            points_per_frame = params.basic.point_num_per_scan
            bytes_per_point = 2
        block_bytes = points_per_frame * params.display.frame_load_num * params.upload.channel_num * bytes_per_point
        block_duration_ms = params.display.frame_load_num / max(params.basic.scan_rate, 1) * 1000.0
        log.info(f"Parameters: scan_rate={params.basic.scan_rate}, points={params.basic.point_num_per_scan}, "
                 f"channels={params.upload.channel_num}, data_source={params.upload.data_source}, "
                 f"length_load_s={params.display.length_load_s:.3f}, length_plot_s={params.display.length_plot_s:.3f}, "
                 f"load_frames={params.display.frame_load_num}, plot_frames={params.display.frame_plot_num}, "
                 f"length_save_s={params.save.length_save_s:.3f}, length_file_s={params.save.length_file_s:.3f}, "
                 f"length_comm_s={params.comm.length_comm_s:.3f}, block_bytes={block_bytes / 1024 / 1024:.2f}MB, "
                 f"block_duration={block_duration_ms:.1f}ms")

        # Configure device (if not simulation mode)
        if not self.simulation_mode:
            if not self._configure_device(params):
                return

            # Start device
            log.info("Starting device acquisition...")
            try:
                self.api.start()
            except PCIe7821Error as e:
                log.error(f"Failed to start acquisition: {e}")
                QMessageBox.critical(self, "Error", f"Failed to start acquisition: {e}")
                return

        # Start data saver if enabled (block-based). This also creates missing directories.
        if params.save.enable:
            if not self._start_data_saver(params):
                if not self.simulation_mode and self.api is not None:
                    try:
                        self.api.stop()
                    except Exception as exc:
                        log.warning(f"Error stopping device after storage start failure: {exc}")
                return
        else:
            self._save_file_count_this_run = 0
            self._refresh_save_status_display()

        # Reset counters
        self._data_count = 0
        self._gui_update_count = 0
        self._raw_data_count = 0
        self._full_data_count = 0
        self._last_save_enqueue_ms = 0.0
        self._max_save_enqueue_ms = 0.0
        self._last_tcp_enqueue_ms = 0.0
        self._max_tcp_enqueue_ms = 0.0
        self._last_data_time = time.time()
        self._last_phase_callback_at = 0.0
        self._last_gui_interval_ms = 0.0
        self._max_gui_interval_ms = 0.0
        self._recovery_in_progress = False
        self._last_raw_display_time = 0  # Force immediate first update
        self._reset_tab1_phase_filter()

        # Create and start acquisition thread
        log.info("Creating acquisition thread...")
        if self.simulation_mode:
            self.acq_thread = SimulatedAcquisitionThread(self)
        else:
            self.acq_thread = AcquisitionThread(self.api, self)

        self.acq_thread.configure(params)
        self._sync_acquisition_display_request()
        if hasattr(self.acq_thread, "set_monitor_read_enabled"):
            self.acq_thread.set_monitor_read_enabled(
                self.monitor_enable_check.isChecked() and params.upload.data_source == DataSource.PHASE
            )
        self._tcp_settings_snapshot = self.get_tab3_comm_settings()
        self.acq_thread.set_full_data_handler(self._handle_full_data_block)

        # Only small control/monitor signals enter the GUI queue.
        log.debug("Connecting acquisition thread signals...")
        self.acq_thread.display_snapshot_ready.connect(self._drain_latest_display_data)
        self.acq_thread.monitor_data_ready.connect(self._on_monitor_data)
        self.acq_thread.buffer_status.connect(self._on_buffer_status)
        self.acq_thread.error_occurred.connect(self._on_error)
        self.acq_thread.acquisition_stopped.connect(self._on_acquisition_stopped)

        self.tcp_tab3_manager.start_session(params)

        log.info("Starting acquisition thread...")
        self.acq_thread.start()

        # Update UI state - button colors change
        self._set_start_btn_running()
        self._set_stop_btn_enabled()
        self._set_params_enabled(False)

        # Reset spectrum analyzer
        self.spectrum_analyzer.reset()

        log.info("Acquisition started successfully")

    @pyqtSlot()
    def _on_stop(self):
        """Handle stop button click"""
        log.info("=== STOP button clicked ===")
        self._fatal_acq_error_stop_pending = False

        # Stop display consumption and restore controls immediately. Hardware cleanup follows.
        self._set_start_btn_ready()
        self._set_stop_btn_disabled()
        self._set_params_enabled(True)
        self.stop_btn.setText("Stopping...")

        thread_stopped = True
        stopping_thread = self.acq_thread
        if stopping_thread is not None:
            log.debug("Requesting acquisition thread stop...")
            stopping_thread.stop()

        self._log_acquisition_diagnostics("manual_stop_before_api_stop", force=True)

        if not self.simulation_mode and self.api is not None:
            log.debug("Stopping device...")
            try:
                self.api.stop()
            except PCIe7821Error as e:
                log.warning(f"Error stopping device: {e}")
            except Exception as e:
                log.warning(f"Unexpected error stopping device: {e}")

        if stopping_thread is not None:
            thread_stopped = stopping_thread.wait_until_stopped(5000)
            if not thread_stopped:
                log.error("Acquisition thread is still running after stop; skip force terminate to avoid driver corruption")
            stopping_thread.set_full_data_handler(None)
            stopping_thread.clear_latest_display_data()

        self._stop_data_saver()

        self.tcp_tab3_manager.stop_session()

        self._refresh_save_status_display()
        if self.acq_thread is stopping_thread:
            self.acq_thread = None
        log.info(
            f"Stopped (thread_stopped={thread_stopped}). "
            f"Total data callbacks: {self._data_count}, GUI updates: {self._gui_update_count}"
        )

        self.stop_btn.setText("STOP")

    @pyqtSlot()
    def _on_acquisition_stopped(self):
        """Handle acquisition stopped signal"""
        if self.sender() is not self.acq_thread:
            log.debug("Ignoring delayed acquisition_stopped signal from an inactive thread")
            return
        log.info("Acquisition stopped signal received")
        # Restore button colors
        self._set_start_btn_ready()
        self._set_stop_btn_disabled()
        self._set_params_enabled(True)

    def _set_params_enabled(self, enabled: bool):
        """Enable/disable parameter controls"""
        for widget in [self.clk_internal_radio, self.clk_external_radio,
                       self.trig_in_radio, self.trig_out_radio,
                       self.scan_rate_spin, self.pulse_width_spin,
                       self.point_num_spin, self.bypass_spin, self.center_freq_spin,
                       self.channel_combo, self.data_source_combo, self.data_rate_combo,
                       self.rate2phase_combo, self.space_avg_spin, self.merge_points_spin,
                       self.diff_order_spin, self.detrend_bw_spin, self.polar_div_check,
                       self.length_load_spin, self.length_plot_spin,
                       self.tab3_length_comm_spin]:
            widget.setEnabled(enabled)
        self._update_storage_format_control_states()

    # ----- DATA HANDLERS -----
    # Complete blocks stay off the GUI event queue. The GUI consumes only the latest snapshot.

    def _handle_full_data_block(self, data: np.ndarray, data_source: int, channel_num: int):
        """Run in the acquisition thread and hand complete blocks to background consumers."""
        self._full_data_count += 1

        # Storage is the highest-priority consumer. Keep this path to one queue put;
        # storage-only downsampling now happens inside the saver thread.
        saver = self.data_saver
        if saver is not None and saver.is_running:
            save_start = time.perf_counter()
            save_ok = saver.save_block(data)
            self._last_save_enqueue_ms = (time.perf_counter() - save_start) * 1000.0
            self._max_save_enqueue_ms = max(self._max_save_enqueue_ms, self._last_save_enqueue_ms)
            if not save_ok:
                log.warning(f"Save enqueue failed at full block #{self._full_data_count}")
            elif self._last_save_enqueue_ms > 5.0:
                log.warning(
                    f"Slow save enqueue: {self._last_save_enqueue_ms:.1f}ms, "
                    f"block={self._full_data_count}, queue={saver.queue_size}/{getattr(saver, 'buffer_size', 0)}"
                )

        if data_source == DataSource.PHASE:
            tcp_start = time.perf_counter()
            self.tcp_tab3_manager.enqueue_phase_data(data, self.params, self._tcp_settings_snapshot)
            self._last_tcp_enqueue_ms = (time.perf_counter() - tcp_start) * 1000.0
            self._max_tcp_enqueue_ms = max(self._max_tcp_enqueue_ms, self._last_tcp_enqueue_ms)
            if self._last_tcp_enqueue_ms > 5.0:
                log.warning(
                    f"Slow TCP ingest enqueue: {self._last_tcp_enqueue_ms:.1f}ms, "
                    f"block={self._full_data_count}"
                )

    def _downsample_data_for_storage(self, data: np.ndarray, data_source: int, channel_num: int) -> np.ndarray:
        """Apply storage-only point picking without changing display, filter, or TCP data."""
        factor = self._get_storage_downsample_factor(self.params)
        if factor <= 1:
            return data

        points_per_frame = self._get_save_source_points_per_frame(self.params)
        if points_per_frame <= 0:
            return data

        channel_num = max(1, int(channel_num or 1))
        arr = np.asarray(data)
        try:
            if channel_num == 1:
                flat = arr.reshape(-1)
                frame_count = flat.size // points_per_frame
                if frame_count <= 0:
                    return np.ascontiguousarray(flat[::factor])

                valid_points = frame_count * points_per_frame
                sampled = flat[:valid_points].reshape(frame_count, points_per_frame)[:, ::factor].reshape(-1)
                if valid_points < flat.size:
                    tail = flat[valid_points::factor]
                    if tail.size:
                        sampled = np.concatenate((sampled, tail))
                return np.ascontiguousarray(sampled)

            matrix = arr.reshape(-1, channel_num)
            frame_count = matrix.shape[0] // points_per_frame
            if frame_count <= 0:
                return np.ascontiguousarray(matrix[::factor, :])

            valid_rows = frame_count * points_per_frame
            sampled = (
                matrix[:valid_rows, :]
                .reshape(frame_count, points_per_frame, channel_num)[:, ::factor, :]
                .reshape(-1, channel_num)
            )
            if valid_rows < matrix.shape[0]:
                tail = matrix[valid_rows::factor, :]
                if tail.size:
                    sampled = np.concatenate((sampled, tail), axis=0)
            return np.ascontiguousarray(sampled)
        except Exception as exc:
            log.warning(f"Storage downsample failed; saving original block instead: {exc}")
            return data

    @pyqtSlot()
    def _drain_latest_display_data(self):
        """Consume the newest display snapshot after a wakeup or watchdog tick."""
        thread = self.acq_thread
        if thread is None or not thread.is_running:
            return

        latest = thread.take_latest_display_data()
        if latest is None:
            return

        data, data_source, channel_num, snapshot_kind, display_meta = latest
        if data_source == DataSource.PHASE:
            self._on_phase_data(data, channel_num, snapshot_kind, display_meta)
        else:
            self._on_raw_data(data, data_source, channel_num)

    def _on_phase_data(
        self,
        data: np.ndarray,
        channel_num: int,
        snapshot_kind: int = 0,
        display_meta: Optional[tuple] = None,
    ):
        """Handle one full or Tab1-optimized phase display snapshot."""
        self._data_count += 1
        self._last_data_time = time.time()
        start_time = time.perf_counter()
        if self._last_phase_callback_at > 0.0:
            self._last_gui_interval_ms = (start_time - self._last_phase_callback_at) * 1000.0
            self._max_gui_interval_ms = max(self._max_gui_interval_ms, self._last_gui_interval_ms)
        self._last_phase_callback_at = start_time
        rad_ms = 0.0
        display_ms = 0.0

        if self._data_count % 10 == 0:
            log.debug(f"Phase data received #{self._data_count}: shape={data.shape}, channels={channel_num}")

        # rad conversion is now applied only to the arrays actually rendered.
        # Storage and TCP continue to receive the original acquisition block.
        phase_scale = np.float32(np.pi / 32767.0) if self.params.display.rad_enable else None

        try:
            display_start = time.perf_counter()
            self._update_phase_display(data, channel_num, phase_scale, snapshot_kind, display_meta)
            display_ms = (time.perf_counter() - display_start) * 1000
            self._gui_update_count += 1
        except Exception as e:
            log.exception(f"Error in _update_phase_display: {e}")

        if self.acq_thread is not None:
            self.frames_label.setText(f"Frames: {self.acq_thread.frames_acquired}")

        elapsed = (time.perf_counter() - start_time) * 1000
        if self._data_count <= 3 or self._data_count % 10 == 0:
            queue_size = self.data_saver.queue_size if self.data_saver is not None and self.data_saver.is_running else 0
            log.debug(
                f"Phase callback #{self._data_count}: bytes={data.nbytes / 1024 / 1024:.2f}MB, "
                f"snapshot_kind={snapshot_kind}, gui_interval_ms={self._last_gui_interval_ms:.1f}, "
                f"rad_ms={rad_ms:.1f}, display_ms={display_ms:.1f}, total_ms={elapsed:.1f}, "
                f"queue={queue_size}"
            )
        if elapsed > 50:
            log.warning(
                f"Slow _on_phase_data: {elapsed:.1f}ms "
                f"(rad={rad_ms:.1f}, display={display_ms:.1f}, "
                f"gui_interval_ms={self._last_gui_interval_ms:.1f}, "
                f"snapshot_mb={data.nbytes / 1024 / 1024:.2f}, snapshot_kind={snapshot_kind})"
            )

    @pyqtSlot(np.ndarray, int, int)
    def _on_raw_data(self, data: np.ndarray, data_type: int, channel_num: int):
        """Handle raw data from acquisition thread"""
        self._data_count += 1
        self._raw_data_count += 1
        self._last_data_time = time.time()
        start_time = time.perf_counter()
        display_ms = 0.0

        if self._data_count % 10 == 0:
            log.debug(f"Raw data received #{self._data_count}: shape={data.shape}, type={data_type}, channels={channel_num}")

        # Throttle raw display to 1 Hz to reduce GPU load (raw data is high volume)
        current_time = time.time()
        if (current_time - self._last_raw_display_time) >= 1.0:
            # Update display
            try:
                display_start = time.perf_counter()
                self._update_raw_display(data, channel_num)
                display_ms = (time.perf_counter() - display_start) * 1000
                self._gui_update_count += 1
                log.debug(f"Raw display updated #{self._raw_data_count}: interval={current_time - self._last_raw_display_time:.1f}s")
                self._last_raw_display_time = current_time
            except Exception as e:
                log.exception(f"Error in _update_raw_display: {e}")

        if self.acq_thread is not None:
            self.frames_label.setText(f"Frames: {self.acq_thread.frames_acquired}")

        elapsed = (time.perf_counter() - start_time) * 1000
        if self._raw_data_count <= 3 or self._raw_data_count % 10 == 0:
            queue_size = self.data_saver.queue_size if self.data_saver is not None and self.data_saver.is_running else 0
            log.debug(
                f"Raw callback #{self._raw_data_count}: bytes={data.nbytes / 1024 / 1024:.2f}MB, "
                f"display_ms={display_ms:.1f}, total_ms={elapsed:.1f}, queue={queue_size}"
            )
        if elapsed > 50:
            log.warning(f"Slow _on_raw_data: {elapsed:.1f}ms (display={display_ms:.1f})")

    @pyqtSlot(np.ndarray, int)
    def _on_monitor_data(self, data: np.ndarray, channel_num: int):
        """Handle monitor data from acquisition thread"""
        self._current_monitor_data = data
        if not self.monitor_enable_check.isChecked():
            return
        try:
            self._update_monitor_display(data, channel_num)
        except Exception as e:
            log.exception(f"Error in _update_monitor_display: {e}")

    @pyqtSlot(int, int)
    def _on_buffer_status(self, points: int, mb: int):
        """Handle buffer status update"""
        self.buffer_label.setText(f"Buffer: {mb} MB")

    @pyqtSlot(str)
    def _on_error(self, message: str):
        """Handle error from acquisition thread"""
        sender = self.sender()
        if sender is not None and sender is not self.acq_thread:
            log.debug("Ignoring delayed acquisition error from an inactive thread")
            return

        log.error(f"Acquisition error: {message}")
        self.statusBar.showMessage(f"Error: {message}", 5000)

        if self._is_fatal_acquisition_error(message):
            self._schedule_fatal_acquisition_stop(message)

    def _is_fatal_acquisition_error(self, message: str) -> bool:
        """Return True for acquisition errors that require a full stop/cleanup."""
        lowered = message.lower()
        return (
            "fatal buffer query error" in lowered
            or "0xffffffff" in lowered
            or "driver/device state" in lowered
        )

    def _schedule_fatal_acquisition_stop(self, message: str):
        """Stop after fatal acquisition errors without restarting a stale device state."""
        if self._fatal_acq_error_stop_pending:
            return
        if self.acq_thread is None:
            return

        self._fatal_acq_error_stop_pending = True
        self._recovery_in_progress = False
        log.error(
            "Fatal acquisition error requires device/driver reset before restart. "
            f"Scheduling stop. error={message}"
        )
        QTimer.singleShot(0, self._stop_after_fatal_acquisition_error)

    def _stop_after_fatal_acquisition_error(self):
        """Run the existing stop path for a fatal acquisition-thread error."""
        if not self._fatal_acq_error_stop_pending:
            return
        if self.acq_thread is None:
            self._fatal_acq_error_stop_pending = False
            return

        self.statusBar.showMessage(
            "Fatal acquisition error: reset PCIe device/driver before restarting",
            10000,
        )
        try:
            self._on_stop()
        except Exception as exc:
            self._fatal_acq_error_stop_pending = False
            log.exception(f"Fatal acquisition stop failed: {exc}")

    # ----- DISPLAY UPDATE METHODS -----
    # Time mode: overlay multiple frames on one plot
    # Space mode: extract single spatial point across frames (temporal trace)

    def _set_time_plot_bottom_label(self, label: str) -> None:
        """Set the bottom axis label for the Tab1 time-domain plot."""
        self.plot_widget_1.setLabel(
            'bottom',
            label,
            color='k',
            **{'font-family': 'Times New Roman', 'font-size': '8pt'},
        )

    def _set_time_plot_axis(self, label: str, axis_kind: str) -> None:
        """Set Tab1 axis semantics and reset view when the x-axis unit changes."""
        previous_axis_kind = self._time_plot_axis_kind
        self._set_time_plot_bottom_label(label)
        self._configure_time_plot_curves_for_axis(axis_kind)
        if previous_axis_kind == axis_kind:
            return

        self._time_plot_axis_kind = axis_kind
        if previous_axis_kind is None:
            return

        self._clear_waveform_plot()
        self._time_plot_pending_auto_range = True
        self._time_plot_auto_range_frames_remaining = 8
        self._restore_plot_auto_range("plot1")
        log.debug(f"Tab1 time plot axis changed: {previous_axis_kind} -> {axis_kind}")

    def _apply_pending_time_plot_auto_range(self) -> None:
        """Restore Tab1 view after new data has been written for a changed x-axis."""
        if (
            not self._time_plot_pending_auto_range
            and self._time_plot_auto_range_frames_remaining <= 0
        ):
            return

        if self._plot_zoom_locked.get("plot1", False):
            self._time_plot_pending_auto_range = False
            self._time_plot_auto_range_frames_remaining = 0
            return

        self._time_plot_pending_auto_range = False
        self._restore_plot_auto_range("plot1")
        forced = self._force_plot_range_to_curve_data("plot1", self.plot_curve_1)
        if forced and self._time_plot_auto_range_frames_remaining > 0:
            self._time_plot_auto_range_frames_remaining -= 1
        expected_axis = self._time_plot_axis_kind
        QTimer.singleShot(0, lambda axis=expected_axis: self._retry_time_plot_auto_range(axis))
        QTimer.singleShot(50, lambda axis=expected_axis: self._retry_time_plot_auto_range(axis))
        QTimer.singleShot(150, lambda axis=expected_axis: self._retry_time_plot_auto_range(axis))
        QTimer.singleShot(300, lambda axis=expected_axis: self._retry_time_plot_auto_range(axis))

    def _retry_time_plot_auto_range(self, expected_axis_kind: Optional[str]) -> None:
        """Repeat Tab1 range restoration after Qt has processed the latest curve update."""
        if expected_axis_kind != self._time_plot_axis_kind:
            return
        if self._plot_zoom_locked.get("plot1", False):
            self._time_plot_auto_range_frames_remaining = 0
            return
        self._restore_plot_auto_range("plot1")
        self._force_plot_range_to_curve_data("plot1", self.plot_curve_1)

    def _raw_distance_axis(self, point_count: int) -> np.ndarray:
        """Return Raw distance coordinates in meters, using 1-based point positions."""
        spacing_m = 0.1 * float(self.params.upload.data_rate or 1)
        return np.arange(1, int(point_count) + 1, dtype=float) * spacing_m

    def _phase_distance_axis(self, point_count: int) -> np.ndarray:
        """Return PHASE distance coordinates in meters, using rate2phase and merge."""
        rate2phase = max(1, int(self.params.phase_demod.rate2phase or 1))
        merge_points = max(1, int(self.params.phase_demod.merge_point_num or 1))
        spacing_m = 0.4 * rate2phase * merge_points
        return np.arange(1, int(point_count) + 1, dtype=float) * spacing_m

    def _phase_time_axis(self, frame_count: int) -> np.ndarray:
        """Return PHASE temporal coordinates in seconds, using Scan(Hz)."""
        scan_rate = max(1.0, float(self.params.basic.scan_rate or 1))
        return np.arange(1, int(frame_count) + 1, dtype=float) / scan_rate

    @staticmethod
    def _scale_phase_for_display(data: np.ndarray, phase_scale: Optional[np.float32]) -> np.ndarray:
        """Apply display-only radian scaling to the smallest renderable array."""
        arr = np.asarray(data)
        if phase_scale is None:
            return arr
        scaled = arr.astype(np.float32, copy=True)
        scaled *= phase_scale
        return scaled

    def _reset_tab1_phase_filter(self) -> None:
        """Reset the independent Tab1 phase waveform filter state."""
        self._tab1_phase_filter.reset_design()
        self._tab1_phase_filter_signature = None
        self._tab1_phase_filter_error_text = ""

    def _get_shared_filter_settings(self) -> tuple[bool, str]:
        """Read the shared FILTER switch and cutoff text from the main control panel."""
        if hasattr(self, "filter_spec_edit"):
            self._filter_spec_text = self.filter_spec_edit.text().strip()
        else:
            self._filter_spec_text = str(self._filter_spec_text).strip()
        return bool(self._filter_enabled), self._filter_spec_text

    def _validate_shared_filter_spec(self, spec_text: str) -> None:
        """Validate the shared filter text against the current scan rate."""
        filter_spec = parse_filter_spec(spec_text)
        validator = RealtimeTimeAxisFilter(order=2)
        validator.configure(filter_spec, float(self.params.basic.scan_rate or 0.0))

    def _set_shared_filter_error(self, message: str) -> None:
        self._filter_error_text = message
        if hasattr(self, "filter_spec_edit"):
            self.filter_spec_edit.setToolTip(
                message or "Examples: 1- high-pass, -10 low-pass, 2-10 band-pass"
            )
        self._update_shared_filter_button_style()

    def _update_shared_filter_button_style(self) -> None:
        if not hasattr(self, "filter_btn"):
            return
        if self._filter_enabled and self._filter_error_text:
            self.filter_btn.setStyleSheet(
                """
                QPushButton {
                    background-color: #F57C00;
                    color: white;
                    border: 1px solid #E65100;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background-color: #EF6C00;
                }
                """
            )
        elif self._filter_enabled:
            self.filter_btn.setStyleSheet(
                """
                QPushButton {
                    background-color: #1976D2;
                    color: white;
                    border: 1px solid #1565C0;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background-color: #1565C0;
                }
                """
            )
        else:
            self.filter_btn.setStyleSheet(
                """
                QPushButton {
                    background-color: #9E9E9E;
                    color: white;
                    border: 1px solid #757575;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background-color: #757575;
                }
                """
            )

    def _sync_shared_filter_settings(self, reset_tab1: bool = True) -> None:
        enabled, spec_text = self._get_shared_filter_settings()
        self.params.time_space.filter_enabled = enabled
        self.params.time_space.filter_spec = spec_text
        if self.time_space_widget is not None and hasattr(self.time_space_widget, "set_filter_settings"):
            self.time_space_widget.set_filter_settings(enabled, spec_text)
        if reset_tab1:
            self._reset_tab1_phase_filter()

    def _on_filter_spec_changed(self):
        self._filter_spec_text = self.filter_spec_edit.text().strip()
        if self._filter_enabled:
            try:
                self._validate_shared_filter_spec(self._filter_spec_text)
                self._set_shared_filter_error("")
            except FilterSpecError as exc:
                self._set_shared_filter_error(str(exc))
                log.warning("Invalid shared filter parameter: %s", exc)
        else:
            self._set_shared_filter_error("")
        self._sync_shared_filter_settings()
        self._sync_acquisition_display_transform()

    def _on_filter_button_clicked(self, checked: bool):
        self._filter_enabled = bool(checked)
        self._filter_spec_text = self.filter_spec_edit.text().strip()
        if self._filter_enabled:
            try:
                self._validate_shared_filter_spec(self._filter_spec_text)
                self._set_shared_filter_error("")
            except FilterSpecError as exc:
                self._set_shared_filter_error(str(exc))
                log.warning("Shared filter enabled with invalid parameter: %s", exc)
        else:
            self._set_shared_filter_error("")
        self._sync_shared_filter_settings()
        self._sync_acquisition_display_request()

    def _apply_tab1_phase_waveform_filter(
        self,
        display_data: np.ndarray,
        frame_num: int,
        point_num: int,
        channel_num: int,
    ) -> np.ndarray:
        """Filter only the Tab1 phase waveform data using the shared FILTER settings."""
        enabled, spec_text = self._get_shared_filter_settings()
        if not enabled:
            if self._tab1_phase_filter_signature is not None or self._tab1_phase_filter_error_text:
                self._reset_tab1_phase_filter()
            return display_data

        try:
            filter_spec = parse_filter_spec(spec_text)
            sample_rate_hz = float(self.params.basic.scan_rate or 0.0)
            filter_signature = (spec_text, sample_rate_hz)
            if self._tab1_phase_filter_signature != filter_signature:
                self._tab1_phase_filter.reset_design()
                self._tab1_phase_filter_signature = filter_signature

            expected_rows = int(frame_num) * int(point_num)
            if expected_rows <= 0:
                return display_data

            if channel_num == 1:
                flat = np.asarray(display_data).reshape(-1)
                if flat.size < expected_rows:
                    return display_data
                source_matrix = flat[-expected_rows:].reshape(frame_num, point_num)
                filtered_matrix = self._tab1_phase_filter.process(
                    source_matrix,
                    filter_spec,
                    sample_rate_hz,
                )
                if flat.size == expected_rows:
                    result = filtered_matrix.reshape(-1)
                else:
                    result = np.asarray(flat, dtype=np.float64).copy()
                    result[-expected_rows:] = filtered_matrix.reshape(-1)
                self._tab1_phase_filter_error_text = ""
                return result

            matrix = np.asarray(display_data)
            if matrix.ndim == 1:
                matrix = matrix.reshape(-1, channel_num)
            if matrix.shape[0] < expected_rows:
                return display_data

            source_cube = matrix[-expected_rows:, :].reshape(frame_num, point_num, channel_num)
            filter_input = source_cube.reshape(frame_num, point_num * channel_num)
            filtered = self._tab1_phase_filter.process(
                filter_input,
                filter_spec,
                sample_rate_hz,
            )
            filtered_matrix = filtered.reshape(frame_num, point_num, channel_num).reshape(
                expected_rows,
                channel_num,
            )
            if matrix.shape[0] == expected_rows:
                result = filtered_matrix
            else:
                result = np.asarray(matrix, dtype=np.float64).copy()
                result[-expected_rows:, :] = filtered_matrix
            self._tab1_phase_filter_error_text = ""
            return result
        except FilterSpecError as exc:
            message = str(exc)
            if message != self._tab1_phase_filter_error_text:
                log.warning("Tab1 phase waveform filter skipped: %s", message)
            self._tab1_phase_filter_error_text = message
            self._tab1_phase_filter.reset_design()
            self._tab1_phase_filter_signature = None
            return display_data

    def _update_phase_display(
        self,
        data: np.ndarray,
        channel_num: int,
        phase_scale: Optional[np.float32] = None,
        snapshot_kind: int = 0,
        display_meta: Optional[tuple] = None,
    ):
        """Update phase displays without converting the whole GUI window to radians."""
        point_num = self._get_effective_phase_point_count()
        waveform_enabled = self.waveform_enable_check.isChecked()
        spectrum_enabled = bool(self.params.display.spectrum_enable)
        compact_space = snapshot_kind == 1
        compact_time = snapshot_kind == 2
        incremental_time_space = snapshot_kind == 3
        if incremental_time_space:
            if display_meta is not None:
                source_frame_count, block_duration_s, distance_start, distance_end = display_meta[:4]
                self.time_space_widget.set_scan_rate(self.params.basic.scan_rate)
                self.time_space_widget.append_prepared_block(
                    data, block_duration_s, source_frame_count, (distance_start, distance_end)
                )
                return
            # Legacy fallback: raw full-width snapshot when the acquisition thread
            # could not pre-transform the block (e.g. multi-channel PHASE).
            display_data, frame_num = self._select_latest_display_frames(
                data, point_num, channel_num, max(1, int(np.asarray(data).size // (point_num * channel_num))),
            )
            if frame_num <= 0:
                return
            self.time_space_widget.set_scan_rate(self.params.basic.scan_rate)
            if channel_num == 1:
                reshaped_data = np.asarray(display_data).reshape(frame_num, point_num)
            else:
                matrix = np.asarray(display_data).reshape(-1, channel_num)
                reshaped_data = matrix[-frame_num * point_num:, 0].reshape(frame_num, point_num)
            self.time_space_widget.update_data(reshaped_data, display_scale=phase_scale)
            return
        if compact_space:
            compact = np.asarray(data)
            frame_num = min(int(self.params.display.frame_plot_num), int(compact.shape[0]))
            display_data = compact[-frame_num:]
        else:
            target_frames = 4 if compact_time else self.params.display.frame_plot_num
            display_data, frame_num = self._select_latest_display_frames(
                data,
                point_num,
                channel_num,
                target_frames,
            )
        if frame_num <= 0:
            return

        log.debug(f"Display mode: {self.params.display.mode}, Region index: {self.params.display.region_index}")

        filter_active = bool(waveform_enabled and self._filter_enabled)
        waveform_display_data = None
        if filter_active:
            filter_input = self._scale_phase_for_display(display_data, phase_scale)
            if compact_space:
                waveform_display_data = self._apply_tab1_phase_waveform_filter(
                    filter_input,
                    frame_num,
                    1,
                    channel_num,
                )
            else:
                waveform_display_data = self._apply_tab1_phase_waveform_filter(
                    filter_input,
                    frame_num,
                    point_num,
                    channel_num,
                )

        if compact_space:
            self._set_time_plot_axis('Time (s)', 'time')
            time_axis = self._phase_time_axis(frame_num)
            matrix = np.asarray(display_data).reshape(frame_num, channel_num)
            scaled_matrix = self._scale_phase_for_display(matrix, phase_scale)
            if waveform_enabled:
                waveform_matrix = (
                    np.asarray(waveform_display_data).reshape(frame_num, channel_num)
                    if waveform_display_data is not None
                    else scaled_matrix
                )
                for ch in range(min(channel_num, 2)):
                    self.plot_curve_1[ch].setData(time_axis, waveform_matrix[:, ch])
                self._apply_pending_time_plot_auto_range()
                for i in range(min(channel_num, 2), 4):
                    self.plot_curve_1[i].setData([])
            if spectrum_enabled and scaled_matrix.size:
                self._update_spectrum(
                    scaled_matrix[:, 0],
                    self.params.basic.scan_rate,
                    psd_mode=False,
                    data_type='int',
                )
            return

        if self.params.display.mode == DisplayMode.SPACE:
            self._set_time_plot_axis('Time (s)', 'time')
            time_axis = self._phase_time_axis(frame_num)
            region_idx = min(self.params.display.region_index, point_num - 1)

            if channel_num == 1:
                matrix = np.asarray(display_data).reshape(frame_num, point_num)
                raw_trace = matrix[:, region_idx]
                scaled_trace = self._scale_phase_for_display(raw_trace, phase_scale)
                if waveform_enabled:
                    if waveform_display_data is not None:
                        waveform_trace = np.asarray(waveform_display_data).reshape(frame_num, point_num)[:, region_idx]
                    else:
                        waveform_trace = scaled_trace
                    self.plot_curve_1[0].setData(time_axis[:waveform_trace.size], waveform_trace)
                    self._apply_pending_time_plot_auto_range()
                    for i in range(1, 4):
                        self.plot_curve_1[i].setData([])

                if spectrum_enabled and scaled_trace.size > 0:
                    self._update_spectrum(scaled_trace, self.params.basic.scan_rate, psd_mode=False, data_type='int')
            else:
                matrix = np.asarray(display_data)
                if matrix.ndim == 1:
                    matrix = matrix.reshape(-1, channel_num)
                rows = frame_num * point_num
                cube = matrix[-rows:, :].reshape(frame_num, point_num, channel_num)
                waveform_cube = None
                if waveform_display_data is not None:
                    wf_matrix = np.asarray(waveform_display_data)
                    if wf_matrix.ndim == 1:
                        wf_matrix = wf_matrix.reshape(-1, channel_num)
                    waveform_cube = wf_matrix[-rows:, :].reshape(frame_num, point_num, channel_num)

                if waveform_enabled:
                    for ch in range(min(channel_num, 2)):
                        if waveform_cube is not None:
                            trace = waveform_cube[:, region_idx, ch]
                        else:
                            trace = self._scale_phase_for_display(cube[:, region_idx, ch], phase_scale)
                        self.plot_curve_1[ch].setData(time_axis[:trace.size], trace)
                    self._apply_pending_time_plot_auto_range()
                    for i in range(channel_num, 4):
                        self.plot_curve_1[i].setData([])

        else:
            self._set_time_plot_axis('Distance (m)', 'distance')
            distance_axis = self._phase_distance_axis(point_num)

            if channel_num == 1:
                matrix = np.asarray(display_data).reshape(frame_num, point_num)
                waveform_matrix = (
                    np.asarray(waveform_display_data).reshape(frame_num, point_num)
                    if waveform_display_data is not None
                    else None
                )
                frames_to_show = min(4, frame_num)
                if waveform_enabled:
                    for i in range(4):
                        if i < frames_to_show:
                            frame_index = frame_num - frames_to_show + i
                            if waveform_matrix is not None:
                                y_data = waveform_matrix[frame_index]
                            else:
                                y_data = self._scale_phase_for_display(matrix[frame_index], phase_scale)
                            self.plot_curve_1[i].setData(distance_axis, y_data)
                            if i == 0:
                                self._apply_pending_time_plot_auto_range()
                        else:
                            self.plot_curve_1[i].setData([])

                if spectrum_enabled:
                    spectrum_data = self._scale_phase_for_display(matrix[-1], phase_scale)
                    self._update_spectrum(spectrum_data, self.params.basic.scan_rate, psd_mode=False, data_type='int')
            else:
                matrix = np.asarray(display_data)
                if matrix.ndim == 1:
                    matrix = matrix.reshape(-1, channel_num)
                rows = frame_num * point_num
                cube = matrix[-rows:, :].reshape(frame_num, point_num, channel_num)
                waveform_cube = None
                if waveform_display_data is not None:
                    wf_matrix = np.asarray(waveform_display_data)
                    if wf_matrix.ndim == 1:
                        wf_matrix = wf_matrix.reshape(-1, channel_num)
                    waveform_cube = wf_matrix[-rows:, :].reshape(frame_num, point_num, channel_num)

                if waveform_enabled:
                    for ch in range(min(channel_num, 4)):
                        if waveform_cube is not None:
                            y_data = waveform_cube[-1, :, ch]
                        else:
                            y_data = self._scale_phase_for_display(cube[-1, :, ch], phase_scale)
                        self.plot_curve_1[ch].setData(distance_axis, y_data)
                    self._apply_pending_time_plot_auto_range()
                    for i in range(channel_num, 4):
                        self.plot_curve_1[i].setData([])

        if (
            self.time_space_widget is not None
            and hasattr(self.time_space_widget, 'is_plot_enabled')
            and self.time_space_widget.is_plot_enabled()
            and self.plot_tabs.currentIndex() == 1
        ):
            self.time_space_widget.set_scan_rate(self.params.basic.scan_rate)
            if channel_num == 1:
                reshaped_data = np.asarray(display_data).reshape(frame_num, point_num)
            else:
                matrix = np.asarray(display_data)
                if matrix.ndim == 1:
                    matrix = matrix.reshape(-1, channel_num)
                rows = frame_num * point_num
                reshaped_data = matrix[-rows:, 0].reshape(frame_num, point_num)

            success = self.time_space_widget.update_data(reshaped_data, display_scale=phase_scale)
            if not success:
                log.debug("Time-space plot update skipped (plot disabled)")

    def _update_raw_display(self, data: np.ndarray, channel_num: int):
        """Update display for raw IQ data"""
        point_num = self.params.basic.point_num_per_scan
        waveform_enabled = self.waveform_enable_check.isChecked()
        display_data, frame_num = self._select_latest_display_frames(
            data,
            point_num,
            channel_num,
            self.params.display.frame_plot_num,
        )
        if frame_num <= 0:
            return

        self._set_time_plot_axis('Distance (m)', 'distance')
        distance_axis = self._raw_distance_axis(point_num)

        if channel_num == 1:
            # Show full-resolution frames; pyqtgraph handles view clipping/downsampling.
            for i in range(min(4, frame_num)):
                start = i * point_num
                end = start + point_num
                if waveform_enabled and end <= len(display_data):
                    self.plot_curve_1[i].setData(distance_axis, display_data[start:end])
                    if i == 0:
                        self._apply_pending_time_plot_auto_range()
                elif waveform_enabled:
                    self.plot_curve_1[i].setData([])

            # Spectrum: use full-resolution data (Raw data: automatically uses Power Spectrum)
            if self.params.display.spectrum_enable and point_num <= len(display_data):
                sample_rate = 1e9 / self.params.upload.data_rate
                self._update_spectrum(display_data[-point_num:], sample_rate,
                                     psd_mode=False, data_type='short')  # psd_mode ignored for raw data
        else:
            if len(display_data.shape) == 1:
                display_data = display_data.reshape(-1, channel_num)

            for ch in range(min(channel_num, 4)):
                if waveform_enabled and point_num <= len(display_data):
                    self.plot_curve_1[ch].setData(distance_axis, display_data[:point_num, ch])
            if waveform_enabled:
                self._apply_pending_time_plot_auto_range()

            # Spectrum: full-resolution data (Raw data: automatically uses Power Spectrum)
            if self.params.display.spectrum_enable and point_num <= len(display_data):
                sample_rate = 1e9 / self.params.upload.data_rate
                # Use first channel for spectrum computation
                self._update_spectrum(display_data[-point_num:, 0], sample_rate,
                                     psd_mode=False, data_type='short')  # psd_mode ignored for raw data

    def _update_monitor_display(self, data: np.ndarray, channel_num: int):
        """Update monitor plot"""
        if not self.monitor_enable_check.isChecked():
            return

        point_num = self._get_effective_phase_point_count()

        if channel_num == 1:
            self.monitor_curves[0].setData(data[:point_num])
            self.monitor_curves[1].setData([])
        else:
            if len(data.shape) == 1:
                data = data.reshape(-1, channel_num)

            for ch in range(min(channel_num, 2)):
                self.monitor_curves[ch].setData(data[:point_num, ch])

    def _update_spectrum(self, data: np.ndarray, sample_rate: float, psd_mode: bool, data_type: str):
        """Update spectrum plot"""
        try:
            freq, spectrum, df = self.spectrum_analyzer.update(
                data, sample_rate, psd_mode, data_type
            )

            # Linear axes: Y is already in dB, X is linear frequency
            self.plot_widget_2.setLogMode(x=False, y=False)

            # Filter frequency range:
            # Phase data starts from 1Hz (exclude DC) since phase is relative.
            # Raw IQ data includes 0Hz.
            nyquist = sample_rate / 2
            if data_type == 'int':  # Phase data
                # Phase: X-axis [1, fs/2], skip DC component
                valid_indices = (freq >= 1.0) & (freq <= nyquist)
            else:  # Raw IQ data
                # Raw data: include 0Hz (DC)
                valid_indices = (freq >= 0) & (freq <= nyquist)

            freq_filtered = freq[valid_indices]
            spectrum_filtered = spectrum[valid_indices]

            if len(freq_filtered) > 0:
                # Frequency unit: phase data in Hz, raw data in MHz
                if data_type == 'int':  # Phase data: Hz range (scan rate based)
                    freq_display = freq_filtered
                else:  # Raw data: convert Hz to MHz (high-speed ADC sampling)
                    freq_display = freq_filtered / 1e6

                self.spectrum_curve.setData(freq_display, spectrum_filtered)

                # Set X-axis range
                if not self._plot_zoom_locked.get("plot2", False):
                    spectrum_view_box = self.plot_widget_2.getViewBox()
                    spectrum_view_box.enableAutoRange(y=True)
                    if data_type == 'int':  # Phase data: explicit range [1, fs/2]
                        nyquist_display = nyquist
                        spectrum_view_box.enableAutoRange(x=False)
                        self.plot_widget_2.setXRange(1.0, nyquist_display, padding=0.02)
                    else:  # Raw data: auto range
                        spectrum_view_box.enableAutoRange(x=True)

                # Set axis labels with explicit unit text (bypasses pyqtgraph auto-scaling)
                if data_type == 'int':  # Phase data
                    self.plot_widget_2.setLabel('bottom', 'Frequency (Hz)',
                                              **{'font-family': 'Times New Roman', 'font-size': '8pt'})
                else:  # Raw data
                    self.plot_widget_2.setLabel('bottom', 'Frequency (MHz)',
                                              **{'font-family': 'Times New Roman', 'font-size': '8pt'})

            # Y-axis label: Raw data = Power (dB), Phase data = PSD (dB)
            if data_type == 'int':  # Phase data: Always PSD
                self.plot_widget_2.setLabel('left', 'PSD (dB)',
                                          **{'font-family': 'Times New Roman', 'font-size': '8pt'})
            else:  # Raw data: Always Power Spectrum
                self.plot_widget_2.setLabel('left', 'Power (dB)',
                                          **{'font-family': 'Times New Roman', 'font-size': '8pt'})
        except Exception as e:
            log.warning(f"Spectrum update error: {e}")

    def _select_latest_display_frames(
        self,
        data: np.ndarray,
        points_per_frame: int,
        channel_num: int,
        target_frames: int,
    ) -> tuple[np.ndarray, int]:
        """Extract the most recent frames needed for one GUI update."""
        if points_per_frame <= 0 or target_frames <= 0:
            return np.asarray(data), 0

        if channel_num == 1:
            flat = np.asarray(data).reshape(-1)
            available_frames = flat.size // points_per_frame
            frame_num = min(target_frames, available_frames)
            if frame_num <= 0:
                return flat[:0], 0
            keep_points = frame_num * points_per_frame
            return flat[-keep_points:], frame_num

        matrix = np.asarray(data)
        if matrix.ndim == 1:
            matrix = matrix.reshape(-1, channel_num)

        available_frames = matrix.shape[0] // points_per_frame
        frame_num = min(target_frames, available_frames)
        if frame_num <= 0:
            return matrix[:0], 0
        keep_rows = frame_num * points_per_frame
        return matrix[-keep_rows:, :], frame_num

    # ----- STATUS MONITORING -----

    def _update_status(self):
        """Periodic status update"""
        try:
            # Check if widgets still exist (window might be closing)
            if not hasattr(self, 'frames_label'):
                return

            self._update_calculated_values()

            # Update acquisition status
            if self.acq_thread is not None and self.acq_thread.is_running:
                frames = self.acq_thread.frames_acquired
                if hasattr(self, 'frames_label'):
                    self.frames_label.setText(f"Frames: {frames}")

                # Update buffer status with estimated values
                if hasattr(self.acq_thread, '_current_polling_interval'):
                    polling_ms = self.acq_thread._current_polling_interval * 1000
                    if hasattr(self, 'polling_label'):
                        self.polling_label.setText(f"Poll: {polling_ms:.1f}ms")

                # Update buffer status displays (with estimated values)
                self._update_buffer_status()
                self._check_acquisition_stall()
            else:
                if hasattr(self, 'frames_label'):
                    self.frames_label.setText("Frames: 0")
                if hasattr(self, 'polling_label'):
                    self.polling_label.setText("Poll: --ms")

            # Update file size estimates and storage run count
            self._update_file_estimates()
            self._refresh_save_status_display()
            self._log_acquisition_diagnostics("periodic")
            self._log_storage_queue_status()

        except Exception as e:
            log.warning(f"Error in _update_status: {e}")

    def _check_acquisition_stall(self):
        """Detect acquisition-thread read stalls, independent of GUI callback timing."""
        if self._recovery_in_progress:
            return
        if self.acq_thread is None or not self.acq_thread.is_running:
            return

        now = time.time()
        if now - self._last_recovery_time < ACQ_RECOVERY_COOLDOWN_S:
            return

        snapshot = self.acq_thread.get_diagnostics_snapshot()
        silent_s = snapshot["last_successful_read_age_s"]
        if silent_s < ACQ_STALL_TIMEOUT_S:
            return

        self._recovery_in_progress = True
        self._last_recovery_time = now
        self._log_acquisition_diagnostics("stall_detected", force=True)
        log.error(
            f"Acquisition stall detected: no successful reads for {silent_s:.1f}s. "
            "Triggering auto-recovery (stop/start)."
        )

        try:
            self._on_stop()
        except Exception as e:
            log.error(f"Auto-recovery stop failed: {e}")

        QTimer.singleShot(800, self._recover_start)

    def _recover_start(self):
        """Second stage of auto-recovery: restart acquisition."""
        try:
            self._on_start()
            log.info("Auto-recovery restart issued")
        except Exception as e:
            log.error(f"Auto-recovery start failed: {e}")
        finally:
            self._recovery_in_progress = False

    def _log_storage_queue_status(self):
        """Periodically log storage queue occupancy for field diagnostics."""
        if not self.data_saver or not self.data_saver.is_running:
            return

        now = time.time()
        if now - self._last_storage_queue_log_time < 5.0:
            return

        if hasattr(self.data_saver, "get_diagnostics_snapshot"):
            snapshot = self.data_saver.get_diagnostics_snapshot()
        else:
            snapshot = {}

        if snapshot.get("format") == "bz":
            log.info(
                "Storage queue: format=bz, "
                f"raw={snapshot['raw_queue_size']}/{snapshot['buffer_size']}, "
                f"packet={snapshot.get('packet_queue_size', 0)}/{snapshot.get('packet_queue_size_max', 0)}, "
                f"compressed={snapshot['compressed_queue_size']}/{snapshot['compressed_queue_size_max']}, "
                f"queue_mb={snapshot.get('raw_queue_estimated_bytes', 0) / 1024 / 1024:.1f}/"
                f"{snapshot.get('packet_queue_estimated_bytes', 0) / 1024 / 1024:.1f}/"
                f"{snapshot.get('compressed_queue_estimated_bytes', 0) / 1024 / 1024:.1f}, "
                f"workers={snapshot.get('compression_threads_alive', 0)}/{snapshot.get('compression_workers', 0)}, "
                f"pending_frames={snapshot['pending_frames']}/{snapshot['packet_frames']}, "
                f"cache={snapshot['has_cache']}, dropped={snapshot['dropped_blocks']}, "
                f"overwritten={snapshot.get('overwritten_files', 0)}, "
                f"slow_compress={snapshot.get('slow_compression_packet_count', 0)}, "
                f"not_realtime={snapshot['compression_not_realtime_count']}, "
                f"packet_full={snapshot.get('packet_queue_full_count', 0)}, "
                f"compressed_full={snapshot.get('compressed_queue_full_count', 0)}, "
                f"last_compress_ms={snapshot['last_compress_ms']:.1f}, "
                f"last_write_ms={snapshot['last_write_ms']:.1f}, "
                f"enqueue_ms={snapshot.get('last_enqueue_ms', 0.0):.2f}/{snapshot.get('max_enqueue_ms', 0.0):.2f}"
            )
        else:
            queue_size = self.data_saver.queue_size
            queue_max = getattr(self.data_saver, 'buffer_size', OPTIMIZED_BUFFER_SIZES['storage_queue_frames'])
            dropped = self.data_saver.dropped_blocks
            snapshot = self.data_saver.get_diagnostics_snapshot() if hasattr(self.data_saver, "get_diagnostics_snapshot") else {}
            log.info(
                f"Storage queue: {queue_size}/{queue_max}, "
                f"queue_mb={snapshot.get('estimated_queue_bytes', 0) / 1024 / 1024:.1f}, "
                f"backlog_s={queue_size * float(self.params.display.length_load_s):.1f}, "
                f"dropped={dropped}, "
                f"overwritten={snapshot.get('overwritten_files', 0)}, "
                f"enqueue_ms={snapshot.get('last_enqueue_ms', 0.0):.2f}/{snapshot.get('max_enqueue_ms', 0.0):.2f}"
            )
        self._last_storage_queue_log_time = now

    def _log_acquisition_diagnostics(self, reason: str, force: bool = False):
        """Emit a consolidated acquisition snapshot for field diagnostics."""
        if self.acq_thread is None:
            return
        if not force and not self.acq_thread.is_running:
            return

        now = time.time()
        if not force and now - self._last_acq_snapshot_log_time < 5.0:
            return

        snapshot = self.acq_thread.get_diagnostics_snapshot()
        parts = [
            f"reason={reason}",
            f"stage={snapshot['current_stage']}",
            f"stage_ms={snapshot['stage_elapsed_ms']:.1f}",
            f"loop={snapshot['loop_count']}",
            f"frames={snapshot['frames_acquired']}",
            f"configured_fps={snapshot.get('configured_fps', 0)}",
            f"measured_fps={snapshot.get('measured_fps', 0.0):.1f}",
            f"fps_ratio={snapshot.get('measured_fps_ratio', 0.0):.3f}",
            f"driver_pending_frames={snapshot.get('driver_pending_frames', 0.0):.0f}",
            f"buffer={snapshot['last_buffer_points']}/{snapshot['last_expected_points']}",
            f"buffer_ratio={snapshot.get('buffer_ratio', 0.0):.2f}",
            f"backlog_s={snapshot.get('backlog_s', 0.0):.2f}",
            f"waits={snapshot['last_wait_iterations']}",
            f"query_ms={snapshot['last_query_ms']:.1f}",
            f"query_errors={snapshot.get('consecutive_buffer_query_errors', 0)}/{snapshot.get('buffer_query_error_count', 0)}",
            f"read_ms={snapshot['last_read_ms']:.1f}",
            f"api_read_ms={snapshot.get('last_api_read_ms', 0.0):.1f}",
            f"crop_ms={snapshot.get('last_crop_ms', 0.0):.1f}",
            f"dispatch_ms={snapshot.get('last_dispatch_ms', 0.0):.1f}",
            f"display_pub_ms={snapshot.get('last_display_publish_ms', 0.0):.1f}",
            f"save_enqueue_ms={self._last_save_enqueue_ms:.2f}/{self._max_save_enqueue_ms:.2f}",
            f"tcp_enqueue_ms={self._last_tcp_enqueue_ms:.2f}/{self._max_tcp_enqueue_ms:.2f}",
            f"read_age_s={snapshot['last_successful_read_age_s']:.1f}",
            f"monitor_enabled={int(bool(snapshot.get('monitor_read_enabled', False)))}",
            f"monitor_ms={snapshot['last_monitor_read_ms']:.1f}",
            f"block_mb={snapshot['last_block_bytes'] / 1024 / 1024:.2f}",
            f"poll_ms={snapshot['polling_interval_ms']:.1f}",
            f"emit_phase={snapshot['phase_emit_count']}",
            f"emit_raw={snapshot['raw_emit_count']}",
            f"gui_skips={snapshot['gui_skip_count']}",
            f"gui_interval_ms={self._last_gui_interval_ms:.1f}/{self._max_gui_interval_ms:.1f}",
        ]
        detail = snapshot.get("current_stage_detail")
        if detail:
            parts.append(f"detail={detail}")
        query_error = snapshot.get("last_buffer_query_error")
        if query_error:
            parts.append(f"query_error={query_error}")

        if hasattr(self.tcp_tab3_manager, "get_diagnostics_snapshot"):
            tcp_diag = self.tcp_tab3_manager.get_diagnostics_snapshot()
            parts.extend([
                f"tcp_ingest_queue={tcp_diag.get('tcp_ingest_queue', 0)}/{tcp_diag.get('tcp_ingest_queue_max', 0)}",
                f"tcp_ingest_dropped={tcp_diag.get('tcp_ingest_dropped', 0)}",
                f"tcp_ingest_ms={tcp_diag.get('tcp_ingest_enqueue_ms', 0.0):.2f}/{tcp_diag.get('tcp_ingest_max_enqueue_ms', 0.0):.2f}",
                f"tcp_process_ms={tcp_diag.get('tcp_ingest_process_ms', 0.0):.1f}/{tcp_diag.get('tcp_ingest_max_process_ms', 0.0):.1f}",
            ])

        if self.data_saver is not None and hasattr(self.data_saver, "get_diagnostics_snapshot"):
            saver = self.data_saver.get_diagnostics_snapshot()
            parts.extend([
                f"save_queue={saver['queue_size']}/{saver['buffer_size']}",
                f"save_max_queue={saver['max_queue_size_seen']}",
                f"save_dropped={saver['dropped_blocks']}",
                f"save_written={saver['blocks_written']}",
                f"save_last_write_ms={saver['last_write_ms']:.1f}",
                f"save_frames_received={saver.get('frames_received', 0)}",
                f"save_frames_written={saver.get('frames_written', 0)}",
                f"save_continuity_gap={saver.get('continuity_gap', 0)}",
                f"save_enqueue_internal_ms={saver.get('last_enqueue_ms', 0.0):.2f}/{saver.get('max_enqueue_ms', 0.0):.2f}",
            ])
            if saver.get("format") == "bz":
                parts.extend([
                    f"save_format=bz",
                    f"save_raw_queue={saver['raw_queue_size']}/{saver['buffer_size']}",
                    f"save_packet_queue={saver.get('packet_queue_size', 0)}/{saver.get('packet_queue_size_max', 0)}",
                    f"save_compressed_queue={saver['compressed_queue_size']}/{saver['compressed_queue_size_max']}",
                    f"save_workers={saver.get('compression_threads_alive', 0)}/{saver.get('compression_workers', 0)}",
                    f"save_pending_frames={saver['pending_frames']}/{saver['packet_frames']}",
                    f"save_cache={saver['has_cache']}",
                    f"save_slow_compress={saver.get('slow_compression_packet_count', 0)}",
                    f"save_not_realtime={saver['compression_not_realtime_count']}",
                    f"save_packet_full={saver.get('packet_queue_full_count', 0)}",
                    f"save_compressed_full={saver.get('compressed_queue_full_count', 0)}",
                    f"save_last_compress_ms={saver['last_compress_ms']:.1f}",
                ])

        log.info("Acq snapshot: " + ", ".join(parts))
        self._last_acq_snapshot_log_time = now

    def _update_calculated_values(self):
        """Update calculated display values"""
        point_num = self.point_num_spin.value()
        scan_rate = self.scan_rate_spin.value()
        channel_num = self.channel_combo.currentData() or 1
        data_source = self.data_source_combo.currentData() or DataSource.PHASE
        data_rate = self.data_rate_combo.currentData() or 1
        rate2phase = self.rate2phase_combo.currentData() or 4

        # Data rate
        data_rate_mbps = calculate_data_rate_mbps(scan_rate, point_num, channel_num)
        self._data_rate_label.setText(f"Data Rate: {data_rate_mbps:.1f} MB/s")

        # Fiber length
        fiber_length = calculate_fiber_length(point_num, data_rate, data_source, rate2phase)
        self._fiber_length_label.setText(f"Fiber Length: {fiber_length:.1f} m")

        # Point num (actual data points after merging)
        if data_source == DataSource.PHASE:
            total_points = calculate_phase_point_num(point_num, self.merge_points_spin.value())
            if channel_num == 1:
                actual_point_num = calculate_cropped_point_count(
                    total_points,
                    self.crop_distance_start_spin.value(),
                    self.crop_distance_end_spin.value(),
                )
            else:
                actual_point_num = total_points
        else:
            actual_point_num = point_num
        self._point_num_label.setText(f"Point num: {actual_point_num}")

    def _time_space_full_window_required(self) -> bool:
        return bool(
            self.time_space_widget is not None
            and hasattr(self.time_space_widget, 'is_plot_enabled')
            and self.time_space_widget.is_plot_enabled()
            and self.plot_tabs.currentIndex() == 1
        )

    def _sync_acquisition_display_request(self) -> None:
        thread = self.acq_thread
        if thread is None or not hasattr(thread, 'set_display_request'):
            return
        time_space_active = self._time_space_full_window_required()
        thread.set_display_request(
            int(self.params.display.mode),
            int(self.params.display.region_index),
            (
                int(self.params.display.mode) == int(DisplayMode.TIME)
                and bool(self._filter_enabled)
            ),
            incremental_full_width=time_space_active,
        )
        self._sync_acquisition_display_transform()

    def _sync_acquisition_display_transform(self) -> None:
        """Forward the display-side rad/crop/downsample transform to the acquisition thread."""
        thread = self.acq_thread
        if thread is None or not hasattr(thread, 'set_display_transform'):
            return
        tw = self.time_space_widget
        ts_params = tw.get_parameters() if tw is not None else {}
        rad_enabled = (
            bool(self.rad_check.isChecked())
            if hasattr(self, 'rad_check')
            else bool(self.params.display.rad_enable)
        )
        thread.set_display_transform(
            rad_enabled,
            int(ts_params.get("distance_range_start", 0)),
            int(ts_params.get("distance_range_end", 0)),
            int(ts_params.get("space_downsample", 1)),
            int(ts_params.get("time_downsample", 1)),
            bool(ts_params.get("filter_enabled", False)),
            str(ts_params.get("filter_spec", "")),
        )

    @pyqtSlot(int)
    def _on_plot_tab_changed(self, _index: int) -> None:
        self._sync_acquisition_display_request()

    @pyqtSlot(bool)
    def _on_mode_changed(self, checked):
        """Handle mode radio button changes"""
        if checked:  # Only respond to the checked button to avoid duplicate calls
            # 仅更新显示模式参数，避免运行时重新收集全部参数
            try:
                if hasattr(self, 'params') and self.params is not None:
                    # 只更新显示模式相关参数
                    if self.mode_space_radio.isChecked():
                        self.params.display.mode = DisplayMode.SPACE
                        self._set_time_plot_axis('Time (s)', 'time')
                        log.debug("Display mode changed to SPACE")
                    else:
                        self.params.display.mode = DisplayMode.TIME
                        self._set_time_plot_axis('Distance (m)', 'distance')
                        log.debug("Display mode changed to TIME")

                    # Update region index.
                    self.params.display.region_index = self.region_index_spin.value()
                    self._sync_acquisition_display_request()
                else:
                    log.warning("Params not initialized, mode change ignored")
            except Exception as e:
                log.warning(f"Error updating mode parameters: {e}")

    @pyqtSlot(int)
    def _on_region_changed(self, value):
        """Handle region index changes"""
        try:
            if hasattr(self, 'params') and self.params is not None:
                self.params.display.region_index = value
                self._sync_acquisition_display_request()
                log.debug(f"Region index changed to: {value}")
        except Exception as e:
            log.warning(f"Error updating region index: {e}")

    def _on_data_source_changed(self, index: int):
        """Handle data source change"""
        data_source = self.data_source_combo.currentData()
        is_phase = (data_source == DataSource.PHASE)

        self._sync_display_control_states()

        # Update spectrum option tooltip. The visible option text stays PSD.
        self.analysis_type_label.setText("PSD")
        if is_phase:
            self.analysis_type_label.setToolTip("Phase data: PSD analysis using scipy.welch")
            self.spectrum_enable_check.setToolTip("Enable phase PSD plot updates")
        else:
            self.analysis_type_label.setToolTip("Raw data: power spectrum analysis")
            self.spectrum_enable_check.setToolTip("Enable raw power spectrum plot updates")

        if not is_phase:
            self.mode_time_radio.setChecked(True)

        self._update_phase_crop_controls()
        self._update_calculated_values()

    def _on_channel_changed(self, index: int):
        """Handle channel count change"""
        self._update_phase_crop_controls()
        self._update_calculated_values()

    def _update_phase_crop_controls(self):
        """Enable crop controls only when they are applicable."""
        enabled = (
            self.data_source_combo.currentData() == DataSource.PHASE
            and self.channel_combo.currentData() == 1
        )
        self.crop_distance_start_spin.setEnabled(enabled)
        self.crop_distance_end_spin.setEnabled(enabled)

    def _browse_save_path(self):
        """Open file dialog to select save path"""
        path = QFileDialog.getExistingDirectory(self, "Select Save Directory", self.save_path_edit.text())
        if path:
            self.save_path_edit.setText(path)

    # ----- APPLICATION LIFECYCLE -----

    def closeEvent(self, event):
        """Handle window close - must release hardware and threads gracefully"""
        log.info("Window closing...")

        # Stop all timers first to prevent interference
        log.debug("Stopping timers...")
        if hasattr(self, '_status_timer'):
            self._status_timer.stop()
        if hasattr(self, '_system_timer'):
            self._system_timer.stop()
        if hasattr(self, '_display_timer'):
            self._display_timer.stop()

        self._save_local_params()

        # Stop acquisition (request thread stop -> stop hardware -> wait thread)
        if self.acq_thread is not None and self.acq_thread.isRunning():
            log.debug("Requesting acquisition thread stop...")
            self.acq_thread.stop()

            if not self.simulation_mode and self.api is not None:
                log.debug("Stopping device during close...")
                try:
                    self.api.stop()
                except Exception as e:
                    log.warning(f"Error stopping device during close: {e}")

            if not self.acq_thread.wait_until_stopped(5000):
                log.error("Acquisition thread still running during close; continue shutdown without force terminate")
            self.acq_thread.set_full_data_handler(None)
            self.acq_thread.clear_latest_display_data()

        # Stop data saver
        if self.data_saver is not None:
            log.debug("Stopping data saver...")
            try:
                self.data_saver.stop()
            except Exception as e:
                log.warning(f"Error stopping data saver: {e}")

        try:
            self.tcp_tab3_manager.shutdown()
        except Exception as e:
            log.warning(f"Error stopping TCP Tab3 manager: {e}")

        # Close device
        if self.api is not None:
            log.debug("Closing device...")
            try:
                self.api.close()
            except Exception as e:
                log.warning(f"Error closing device: {e}")

        log.info("Window closed")
        event.accept()

    def _update_file_estimates(self):
        """Update storage file size estimates after storage-only downsampling."""
        try:
            point_num = self.point_num_spin.value()
            merge_points = self.merge_points_spin.value()
            channel_num = self.channel_combo.currentData() or 1
            data_source = self.data_source_combo.currentData() or DataSource.PHASE
            storage_downsample_factor = self.save_downsample_spin.value()
            length_file_s = self.length_file_spin.value() if hasattr(self, "length_file_spin") else 10.0

            if data_source == DataSource.PHASE and channel_num == 1:
                total_points = calculate_phase_point_num(point_num, merge_points)
                source_points_per_frame = calculate_cropped_point_count(
                    total_points,
                    self.crop_distance_start_spin.value(),
                    self.crop_distance_end_spin.value(),
                )
            elif data_source == DataSource.PHASE:
                source_points_per_frame = calculate_phase_point_num(point_num, merge_points)
            else:
                source_points_per_frame = point_num

            points_per_frame = self._downsampled_point_count(
                source_points_per_frame,
                storage_downsample_factor,
            )
            raw_file_size_mb = (
                points_per_frame
                * max(1, channel_num)
                * max(1, self.scan_rate_spin.value())
                * float(length_file_s)
                * 4
                / (1024 * 1024)
            )
            self.file_size_label.setText(f"~{raw_file_size_mb:.1f}MB/{length_file_s:.3g}s")
            self._update_bz_setting_hints()

        except Exception as e:
            log.warning(f"Error updating file estimates: {e}")
            self.file_size_label.setText("~-- MB/file")

    def _on_time_space_params_changed(self):
        """Handle time-space plot parameters change"""
        # Update the main parameters with current time-space values
        try:
            if self.time_space_widget is not None:
                self.params = self._collect_params()
                self._reset_tab1_phase_filter()
                self._sync_acquisition_display_transform()
                log.debug("Time-space parameters updated")
        except Exception as e:
            log.warning(f"Error updating time-space parameters: {e}")

    @pyqtSlot(int)
    def _on_point_count_changed(self, point_count: int):
        """Handle actual data point count change from time-space widget"""
        try:
            self._point_num_label.setText(f"Point num: {point_count}")
            log.debug(f"Updated point count display: {point_count}")
        except Exception as e:
            log.warning(f"Error updating point count display: {e}")

    @pyqtSlot(bool)
    def _on_plot_state_changed(self, enabled: bool):
        """Handle time-space plot button state changes."""
        try:
            log.info(f"Time-space plot state changed: {'Enabled' if enabled else 'Disabled'}")
            self._sync_acquisition_display_request()
        except Exception as e:
            log.warning(f"Error handling plot state change: {e}")

    def _update_system_status(self):
        """Update system monitoring information (CPU, disk, etc.)"""
        try:
            current_time = time.time()
            if current_time - self._last_system_update < MONITOR_UPDATE_INTERVALS['system_status_s']:
                return

            self._last_system_update = current_time

            # Update CPU usage (non-blocking version)
            # Use interval=None for non-blocking call (returns value from last call)
            self._cpu_percent = psutil.cpu_percent(interval=None)
            if hasattr(self, 'cpu_label'):  # Check if widget still exists
                self.cpu_label.setText(f"CPU: {self._cpu_percent:.1f}%")

            # Update disk space for save path
            if self.data_saver and self.data_saver.is_running:
                save_path = self.save_path_edit.text()
                if os.path.exists(save_path):
                    _, _, free_bytes = shutil.disk_usage(save_path)
                    self._disk_free_gb = free_bytes / (1024**3)
                    if hasattr(self, 'disk_label'):  # Check if widget still exists
                        self.disk_label.setText(f"Disk: {self._disk_free_gb:.1f}GB free")

            # Update polling interval display (if acquisition is running)
            if self.acq_thread and self.acq_thread.is_running:
                polling_ms = getattr(self.acq_thread, '_current_polling_interval', 0.001) * 1000
                if hasattr(self, 'polling_label'):  # Check if widget still exists
                    self.polling_label.setText(f"Poll: {polling_ms:.1f}ms")

        except Exception as e:
            log.warning(f"Error updating system status: {e}")

    def _update_buffer_status(self, hw_count=0, hw_max=50, signal_count=0, signal_max=20,
                            storage_count=0, storage_max=200, display_count=0, display_max=30):
        """Update buffer status displays"""
        try:
            # Update hardware buffer
            hw_percent = min(100, int(hw_count / hw_max * 100)) if hw_max > 0 else 0
            self.hw_buffer_bar.setValue(hw_percent)
            self.hw_buffer_label.setText(f"HW: {hw_count}/{hw_max}")
            self._set_progress_bar_color(self.hw_buffer_bar, hw_percent)

            # Update signal queue
            signal_percent = min(100, int(signal_count / signal_max * 100)) if signal_max > 0 else 0
            self.signal_queue_bar.setValue(signal_percent)
            self.signal_queue_label.setText(f"SIG: {signal_count}/{signal_max}")
            self._set_progress_bar_color(self.signal_queue_bar, signal_percent)

            # Update storage queue
            storage_percent = min(100, int(storage_count / storage_max * 100)) if storage_max > 0 else 0
            self.storage_queue_bar.setValue(storage_percent)
            self.storage_queue_label.setText(f"STO: {storage_count}/{storage_max}")
            self._set_progress_bar_color(self.storage_queue_bar, storage_percent)

        except Exception as e:
            log.warning(f"Error updating buffer status: {e}")

    def _set_progress_bar_color(self, progress_bar: QProgressBar, percentage: int):
        """Set progress bar color based on usage percentage"""
        if percentage >= 90:
            progress_bar.setStyleSheet("QProgressBar::chunk { background-color: red; }")
        elif percentage >= 70:
            progress_bar.setStyleSheet("QProgressBar::chunk { background-color: orange; }")
        else:
            progress_bar.setStyleSheet("QProgressBar::chunk { background-color: green; }")
