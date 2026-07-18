"""
PCCP Wire Break Monitoring Software - Main Entry Point

This is the main entry point for the PCCP (Prestressed Concrete Cylinder Pipe)
wire break monitoring software. The software processes fiber interferometer
signals for real-time intrusion detection and localization.

Phase 1 Implementation:
- Fiber Interferometer Processing (Tab1, Tab2)
- TCP data reception from LabVIEW RT
- Real-time signal processing and visualization
- Feature extraction and threshold detection

Author: Claude
Date: 2026-03-11
"""

import sys
import logging
import json
import time
import numpy as np
from pathlib import Path
from typing import Dict, Any
from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import QTimer

# Import application modules
from ui.main_window import MainWindow
from fip_tab1 import OptimizedTCPServer
from processing.phase_unwrap import PhaseUnwrapper
from processing.signal_filter import SignalFilter
from processing.downsampling import Downsampler
from fip_tab1 import OptimizedTab1ThreadManager, PSDCalculator, RawDataPacket
from fip_tab2 import FIPTab2Manager
from alignment import AlignedSessionCoordinator
from das_tab3 import DASTab3Manager

# Import system configuration
from config import (
    ORIGINAL_SAMPLE_RATE,
    SYSTEM_DOWNSAMPLE_FACTOR,
    EFFECTIVE_SAMPLE_RATE,
    TIME_DISPLAY_DOWNSAMPLE,
    TIME_DISPLAY_SAMPLE_RATE,
    PERFORMANCE_LOG_INTERVAL,
    get_sample_rate_info
)


class PCCPMonitorApp:
    """
    Main application controller for PCCP monitoring system.

    This class coordinates all subsystems including TCP communication,
    data processing, visualization, and user interface.
    """

    def __init__(self, args=None):
        """Initialize the PCCP monitoring application."""
        self.args = args
        # Setup logging
        self._setup_logging(args)
        self.logger = logging.getLogger(__name__)

        # 记录系统配置信息
        self.logger.info("Starting PCCP Wire Break Monitoring Software")
        sample_info = get_sample_rate_info()
        self.logger.info(f"Sample rate config: {sample_info['original_rate_mhz']:.1f}MHz -> "
                        f"{SYSTEM_DOWNSAMPLE_FACTOR}x -> {sample_info['effective_rate_khz']:.0f}kHz")
        self.logger.info(f"Time display: {sample_info['effective_rate_khz']:.0f}kHz -> "
                        f"{TIME_DISPLAY_DOWNSAMPLE}x -> {sample_info['time_display_rate_khz']:.0f}kHz")

        # Initialize Qt application
        self.app = QApplication(sys.argv)
        self.app.setApplicationName("PCCP监测软件")
        self.app.setApplicationVersion("1.0.0")

        # Load configuration
        self.config = self._load_configuration()

        # Initialize components
        self.main_window = MainWindow()
        self.tcp_server = None

        # 核心处理组件
        self.phase_unwrapper = None
        self.signal_filter = None
        self.downsampler = None

        # Tab1优化线程系统
        self.tab1_manager = None

        # Independent Tab2 manager
        self.fip_tab2_manager = None
        self.alignment_coordinator = None
        self.tab3_manager = None
        self.fip_monitoring_active = False
        self.das_monitoring_active = False
        self._fip_packet_sample_rate_override_hz = None
        self._last_fip_packet_shape_signature = None

        # 线程统计定时刷新计时器（X-01）：每 2 s 更新状态栏中的线程健康面板
        self._stats_timer = QTimer()
        self._stats_timer.setInterval(2000)
        self._stats_timer.timeout.connect(self._refresh_thread_stats)

        # Setup connections
        self._setup_connections()

        # Initialize processors
        self._initialize_processors()

    def _setup_logging(self, args=None):
        """Setup application logging.

        Normal mode uses INFO for low overhead. Debug mode is enabled by
        ``python run.py --debug`` and records per-node Tab3 diagnostics.
        """
        log_dir = Path(__file__).parent.parent / 'logs'
        log_dir.mkdir(exist_ok=True)

        debug_enabled = bool(getattr(args, 'debug', False))
        log_file = getattr(args, 'log', None)
        log_path = Path(log_file) if log_file else log_dir / 'pccp_monitor.log'
        if not log_path.is_absolute():
            log_path = Path.cwd() / log_path
        log_path.parent.mkdir(parents=True, exist_ok=True)

        log_level = logging.DEBUG if debug_enabled else logging.INFO
        logging.basicConfig(
            level=log_level,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_path, encoding='utf-8'),
                logging.StreamHandler()
            ],
            force=True,
        )
        logging.getLogger('matplotlib').setLevel(logging.WARNING)
        logging.getLogger(__name__).info(
            "Logging initialized: level=%s, file=%s",
            logging.getLevelName(log_level),
            log_path,
        )

    def _load_configuration(self) -> dict:
        """Load application configuration."""
        config_path = Path(__file__).parent.parent / 'config' / 'app_config.json'

        try:
            with open(config_path, 'r', encoding='utf-8') as f:
                config = json.load(f)
            logging.info(f"Configuration loaded from {config_path}")
            return config

        except FileNotFoundError:
            logging.warning(f"Configuration file not found: {config_path}")
            # Return default configuration
            return self._get_default_config()

        except json.JSONDecodeError as e:
            logging.error(f"Invalid JSON in configuration file: {e}")
            return self._get_default_config()

    def _get_default_config(self) -> dict:
        """Get default configuration."""
        return {
            "communication": {
                "ip": "127.0.0.1",
                "port": 3677,
                "reconnect_interval": 3,
                "buffer_size": 10000,
                "tcp_nodelay": True
            },
            "preprocessing": {
                "filter": {
                    "type": "bandpass",
                    "low_freq": 100,
                    "high_freq": 10000,
                    "order": 4
                },
                "downsample": {
                    "factor": 5,  # 默认5倍降采样：1MHz -> 200kHz
                    "method": "decimate"
                }
            },
            "features": {
                "enabled": ["short_energy"],
                "window_size": 0.05,
                "overlap_ratio": 0.5
            },
            "detection": {
                "threshold_factor": 3.0,
                "max_trigger_duration": 0.1,
                "baseline_update_interval": 10,
                "auto_update_baseline": True
            },
            "storage": {
                "realtime": {
                    "enabled": False,
                    "interval": 10,
                    "downsample_factor": 5,
                    "path": "D:/PCCP/FIPdata"
                },
                "trigger": {
                    "enabled": True,
                    "pre_trigger": 5,
                    "post_trigger": 10,
                    "path": "D:/PCCP/FIPmonitor"
                }
            },
            "visualization": {
                "time_window": 1,  # 改为1秒，与UI默认值一致
                "grid_enabled": True,
                "refresh_rate": 50
            }
        }

    def _setup_connections(self):
        """Setup signal connections between components."""
        # Connect main window signals
        self.main_window.start_monitoring.connect(self._start_monitoring)
        self.main_window.stop_monitoring.connect(self._stop_monitoring)
        self.main_window.config_changed.connect(self._update_configuration)

        # Connect visualization control signals (optimized thread system)
        self.main_window.time_plot_toggled.connect(self._toggle_time_plotting)
        self.main_window.psd_plot_toggled.connect(self._toggle_psd_plotting)

        # Connect parameter control signals
        self.main_window.time_settings_changed.connect(self._update_time_parameters)
        self.main_window.filter_settings_changed.connect(self._update_filter_parameters)

        # Connect storage control signals
        if hasattr(self.main_window, 'phase_storage_check'):
            self.main_window.phase_storage_check.toggled.connect(
                lambda enabled: self._update_storage_settings(
                    enabled,
                    self.main_window.storage_path_edit.text() if hasattr(self.main_window, 'storage_path_edit') else "D:/PCCP/FIPdata",
                    self.main_window.storage_interval_spin.value() if hasattr(self.main_window, 'storage_interval_spin') else 10,
                )
            )
        if hasattr(self.main_window, 'storage_interval_spin'):
            self.main_window.storage_interval_spin.valueChanged.connect(
                lambda interval: self._update_storage_settings(
                    self.main_window.phase_storage_check.isChecked() if hasattr(self.main_window, 'phase_storage_check') else False,
                    self.main_window.storage_path_edit.text() if hasattr(self.main_window, 'storage_path_edit') else "D:/PCCP/FIPdata",
                    interval,
                )
            )
        if hasattr(self.main_window, 'storage_path_edit'):
            self.main_window.storage_path_edit.textChanged.connect(
                lambda path: self._update_storage_settings(
                    self.main_window.phase_storage_check.isChecked() if hasattr(self.main_window, 'phase_storage_check') else False,
                    path,
                    self.main_window.storage_interval_spin.value() if hasattr(self.main_window, 'storage_interval_spin') else 10,
                )
            )

        # Connect downsampling control
        if hasattr(self.main_window, 'downsample_spin'):
            self.main_window.downsample_spin.valueChanged.connect(self._update_downsample_factor)
        if hasattr(self.main_window, 'fip_sensor_settings_changed'):
            self.main_window.fip_sensor_settings_changed.connect(self._update_fip_sensor_settings)

        if hasattr(self.main_window, 'tab2_settings_changed'):
            self.main_window.tab2_settings_changed.connect(self._sync_tab2_settings)
        if hasattr(self.main_window, 'tab2_clear_alarms_requested'):
            self.main_window.tab2_clear_alarms_requested.connect(self._clear_tab2_alarms)
        if hasattr(self.main_window, 'tab3_start_requested'):
            self.main_window.tab3_start_requested.connect(self._start_tab3_monitoring)
        if hasattr(self.main_window, 'tab3_stop_requested'):
            self.main_window.tab3_stop_requested.connect(self._stop_tab3_monitoring)
        if hasattr(self.main_window, 'tab3_settings_changed'):
            self.main_window.tab3_settings_changed.connect(self._sync_tab3_settings)

    def _initialize_processors(self):
        """Initialize data processing components."""
        try:
            # Initialize TCP server with optimized implementation
            comm_config = self.config['communication']
            self.tcp_server = OptimizedTCPServer(
                ip=comm_config['ip'],
                port=comm_config['port']
            )

            # Connect TCP server signals
            self.tcp_server.data_received.connect(self._process_data_packet)
            self.tcp_server.connection_status.connect(self.main_window.update_connection_status)
            self.tcp_server.error_occurred.connect(self._handle_tcp_error)

            # 调试：验证信号连接
            self.logger.info("TCP server signals connected successfully")

            # Initialize processing components
            self.phase_unwrapper = PhaseUnwrapper()

            # Initialize signal filter - 使用原始采样率，因为降采样在滤波之后
            self.signal_filter = SignalFilter(sample_rate=ORIGINAL_SAMPLE_RATE)
            filter_config = self.config['preprocessing']['filter']

            config_filter_type = self._map_filter_type_from_ui(filter_config.get('type', 'bandpass'))
            if config_filter_type in ('bandpass', 'bandstop'):
                config_cutoff = (filter_config.get('low_freq', 100), filter_config.get('high_freq', 10000))
            elif config_filter_type == 'lowpass':
                config_cutoff = filter_config.get('high_freq', 10000)
            elif config_filter_type == 'highpass':
                config_cutoff = filter_config.get('low_freq', 100)
            else:
                config_cutoff = filter_config.get('low_freq', 100)

            self.signal_filter.design_filter(
                config_filter_type,
                config_cutoff,
                filter_config.get('order', 4)
            )

            # Initialize downsampler
            downsample_config = self.config['preprocessing']['downsample']
            self.downsampler = Downsampler(
                method=downsample_config['method'],
                factor=downsample_config['factor']
            )

            self.logger.info(f"Downsampler initialized: method={downsample_config['method']}, factor={downsample_config['factor']}")

            # 同步UI设置到downsampler
            if hasattr(self.main_window, 'downsample_spin'):
                ui_factor = self.main_window.downsample_spin.value()
                if ui_factor != downsample_config['factor']:
                    self.downsampler.set_downsampling_factor(ui_factor)
                    self.logger.info(f"Synced downsampler factor from UI: {ui_factor}")

            # Initialize PSD calculator for Tab1
            psd_calculator = PSDCalculator(sample_rate=EFFECTIVE_SAMPLE_RATE)

            # Initialize Tab1 optimized multi-thread system
            processors = (self.phase_unwrapper, self.signal_filter, self.downsampler)
            self.tab1_manager = OptimizedTab1ThreadManager(processors, psd_calculator)

            # View tab owns all visible plots; keep legacy Tab1 plot workers detached.
            self.tab1_manager.set_plot_widgets(None, None)

            # 启动前先同步一次前面板预处理参数，确保处理链路与UI一致
            self._refresh_preprocessing_parameters(source="init")

            storage_path = self.main_window.get_tab2_storage_settings().get("path", "D:/PCCP/FIPmonitor")
            self.fip_tab2_manager = FIPTab2Manager(self.main_window, storage_path=storage_path)
            self.tab1_manager.data_processor.data_processed.connect(self._process_tab2_data)
            self._sync_tab2_settings()

            self.alignment_coordinator = AlignedSessionCoordinator(cache_seconds=10.0)
            self.tab3_manager = DASTab3Manager(self.main_window, coordinator=self.alignment_coordinator)
            self.tab1_manager.data_processor.data_processed.connect(self.tab3_manager.process_fip_processed_data)
            self._sync_tab3_settings()

            self.logger.info("All processors initialized successfully")

        except Exception as e:
            self.logger.error(f"Error initializing processors: {e}")
            self._show_error_message("初始化失败", f"处理器初始化失败: {e}")

    def _is_tab2_enabled(self) -> bool:
        """Return whether Tab2 is enabled from the UI master switch."""
        getter = getattr(self.main_window, "is_tab2_enabled", None)
        return bool(getter()) if callable(getter) else False

    def _is_tab2_running(self) -> bool:
        """Return whether any Tab2 worker thread is running."""
        if not self.fip_tab2_manager:
            return False
        workers = (
            self.fip_tab2_manager.feature_worker,
            self.fip_tab2_manager.detection_worker,
            self.fip_tab2_manager.plot_worker,
            self.fip_tab2_manager.storage_worker,
        )
        return any(worker.isRunning() for worker in workers)

    def _process_tab2_data(self, processed_data) -> None:
        """Forward processed FIP packets to Tab2 only when Tab2 is enabled and running."""
        if not self._is_tab2_enabled() or not self._is_tab2_running():
            return
        self.fip_tab2_manager.process_processed_data(processed_data)

    def _process_data_packet(self, packet):
        """
        Process received data packet - OPTIMIZED VERSION
        主线程仅负责数据分发，所有CPU密集型操作移至后台线程。

        日志节流策略：每 50 包打印一次 INFO，避免 5 Hz 高频日志
        造成 FileHandler I/O 拖慢 Qt 事件循环。

        Args:
            packet: DataPacket from optimized TCP server
        """
        try:
            if hasattr(self.main_window, 'record_fip_packet_receive'):
                try:
                    self.main_window.record_fip_packet_receive(packet.comm_count, time.time())
                except Exception as sync_exc:
                    self.logger.warning(
                        "Failed to update FIP/eDAS receive-time sync UI for FIP packet #%s: %s",
                        packet.comm_count,
                        sync_exc,
                    )

            # 每 50 包记录一次接收日志，避免高频 I/O 拖慢主线程
            if packet.comm_count % 50 == 0:
                self.logger.info(
                    f"Received packet #{packet.comm_count}: "
                    f"{len(packet.phase_data)} points, "
                    f"first={float(packet.phase_data[0]) if len(packet.phase_data) else float('nan'):.9g}, "
                    f"range=[{np.min(packet.phase_data):.9g}, {np.max(packet.phase_data):.9g}]"
                )
            if len(packet.phase_data) and abs(float(packet.phase_data[0])) <= 1e-12:
                self.logger.warning(
                    "FIP_MAIN_FIRST_SAMPLE_ZERO comm=%s first=%.9g points=%d",
                    packet.comm_count,
                    float(packet.phase_data[0]),
                    len(packet.phase_data),
                )

            fip_settings = (
                self.main_window.get_tab1_fip_settings()
                if hasattr(self.main_window, 'get_tab1_fip_settings')
                else {
                    "sensor_count": 1,
                    "selected_sensor": 1,
                    "packet_duration_seconds": 1.0,
                    "sample_rate_hz": ORIGINAL_SAMPLE_RATE,
                }
            )
            sensor_count = int(fip_settings.get("sensor_count", 1))
            selected_sensor = int(fip_settings.get("selected_sensor", 1))
            packet_duration_seconds = max(float(fip_settings.get("packet_duration_seconds", 1.0)), 1e-6)
            configured_sample_rate_hz = max(float(fip_settings.get("sample_rate_hz", ORIGINAL_SAMPLE_RATE)), 1.0)
            sample_rate_hz = self._resolve_fip_packet_sample_rate(
                point_count=len(packet.phase_data),
                sensor_count=sensor_count,
                selected_sensor=selected_sensor,
                packet_duration_seconds=packet_duration_seconds,
                configured_sample_rate_hz=configured_sample_rate_hz,
                comm_count=packet.comm_count,
            )

            # 简单的数据包格式转换
            raw_packet = RawDataPacket(
                timestamp=packet.timestamp,
                phase_data=packet.phase_data,
                comm_count=packet.comm_count,
                sensor_count=sensor_count,
                selected_sensor=selected_sensor,
                packet_duration_seconds=packet_duration_seconds,
                sample_rate_hz=sample_rate_hz,
            )

            # 仅将数据包发送到后台处理线程，主线程立即返回
            success = self.tab1_manager.process_raw_packet(raw_packet)

            if not success and packet.comm_count % 100 == 0:
                self.logger.warning(f"Failed to queue packet #{packet.comm_count} - processing thread busy")

            # 更新统计信息（保留原有功能）
            if packet.comm_count % 5 == 0:  # 每5个包更新一次统计
                tcp_stats = self.tcp_server.get_statistics()
                self.main_window.update_statistics(tcp_stats)


        except Exception as e:
            self.logger.error(f"Error processing data packet #{packet.comm_count}: {e}")

    def _resolve_fip_packet_sample_rate(
        self,
        point_count: int,
        sensor_count: int,
        selected_sensor: int,
        packet_duration_seconds: float,
        configured_sample_rate_hz: float,
        comm_count: int,
    ) -> float:
        """Use the actual FIP packet shape to keep runtime time axes honest."""
        safe_sensor_count = min(max(int(sensor_count), 1), 2)
        safe_duration = max(float(packet_duration_seconds), 1e-6)
        configured_sample_rate_hz = max(float(configured_sample_rate_hz), 1.0)
        if point_count <= 0:
            return configured_sample_rate_hz

        expected_points_per_sensor = max(1, int(round(configured_sample_rate_hz * safe_duration)))
        expected_total_points = expected_points_per_sensor * safe_sensor_count
        tolerance_points = max(safe_sensor_count, int(round(expected_total_points * 0.01)))
        if abs(point_count - expected_total_points) <= tolerance_points:
            if self._fip_packet_sample_rate_override_hz is not None:
                self.logger.info(
                    "FIP packet shape matches UI settings again at comm=%s; clearing inferred sample-rate override.",
                    comm_count,
                )
                self._fip_packet_sample_rate_override_hz = None
                self._last_fip_packet_shape_signature = None
                self._sync_signal_filter_sample_rate(configured_sample_rate_hz, source="fip_packet_shape_clear")
                self._refresh_preprocessing_parameters(source="fip_packet_shape_clear")
                if self.tab1_manager:
                    self.tab1_manager.update_fip_selection(
                        safe_sensor_count,
                        selected_sensor,
                        packet_duration_seconds=safe_duration,
                        sample_rate_hz=configured_sample_rate_hz,
                    )
            return configured_sample_rate_hz

        if point_count % safe_sensor_count != 0:
            signature = (safe_sensor_count, point_count, expected_total_points, "uneven")
            if signature != self._last_fip_packet_shape_signature or comm_count % 500 == 0:
                self._last_fip_packet_shape_signature = signature
                self.logger.warning(
                    "FIP_PACKET_SHAPE_MISMATCH_UNRESOLVED comm=%s sensor_count=%d duration=%.6fs "
                    "configured_sample_rate=%.1fHz expected_points=%d actual_points=%d; "
                    "actual points are not divisible by sensor_count, keeping configured sample rate.",
                    comm_count,
                    safe_sensor_count,
                    safe_duration,
                    configured_sample_rate_hz,
                    expected_total_points,
                    point_count,
                )
            return configured_sample_rate_hz

        actual_points_per_sensor = max(1, point_count // safe_sensor_count)
        inferred_sample_rate_hz = max(actual_points_per_sensor / safe_duration, 1.0)
        relative_delta = abs(inferred_sample_rate_hz - configured_sample_rate_hz) / configured_sample_rate_hz
        if relative_delta <= 0.01:
            return configured_sample_rate_hz

        signature = (
            safe_sensor_count,
            point_count,
            expected_total_points,
            round(configured_sample_rate_hz, 3),
            round(inferred_sample_rate_hz, 3),
        )
        should_log = signature != self._last_fip_packet_shape_signature or comm_count % 500 == 0
        if should_log:
            self._last_fip_packet_shape_signature = signature
            self.logger.warning(
                "FIP_PACKET_SHAPE_MISMATCH comm=%s sensor_count=%d selected=FIP%d duration=%.6fs "
                "configured_sample_rate=%.1fHz expected_points=%d actual_points=%d "
                "actual_points_per_sensor=%d inferred_sample_rate=%.1fHz; "
                "using inferred sample rate for processing and storage metadata.",
                comm_count,
                safe_sensor_count,
                selected_sensor,
                safe_duration,
                configured_sample_rate_hz,
                expected_total_points,
                point_count,
                actual_points_per_sensor,
                inferred_sample_rate_hz,
            )

        if (
            self._fip_packet_sample_rate_override_hz is None
            or abs(self._fip_packet_sample_rate_override_hz - inferred_sample_rate_hz) > 1e-6
        ):
            old_override = self._fip_packet_sample_rate_override_hz
            self._fip_packet_sample_rate_override_hz = inferred_sample_rate_hz
            self.logger.warning(
                "FIP runtime sample rate override applied: %s -> %.1fHz",
                "None" if old_override is None else f"{old_override:.1f}Hz",
                inferred_sample_rate_hz,
            )
            self._sync_signal_filter_sample_rate(inferred_sample_rate_hz, source="fip_packet_shape")
            self._refresh_preprocessing_parameters(source="fip_packet_shape")
            if self.tab1_manager:
                self.tab1_manager.update_fip_selection(
                    safe_sensor_count,
                    selected_sensor,
                    packet_duration_seconds=safe_duration,
                    sample_rate_hz=inferred_sample_rate_hz,
                )
        return inferred_sample_rate_hz

    def _start_monitoring(self):
        """Start the monitoring system."""
        try:
            self.logger.info("Starting monitoring system...")
            self._ensure_alignment_session_started()

            # Reset processor states
            self.phase_unwrapper.reset()
            if self.signal_filter is not None:
                self.signal_filter.reset_filter_state()
            self.downsampler.reset_state()
            if hasattr(self.main_window, 'get_tab1_fip_settings'):
                self._update_fip_sensor_settings(self.main_window.get_tab1_fip_settings())

            # View tab owns Curve1/Curve2/PSD. Detach the legacy Tab1 plot workers so
            # FIP start/stop cannot clear or overwrite the merged View plots.
            self.tab1_manager.set_plot_widgets(None, None)
            if hasattr(self.main_window, 'reset_tab3_views') and not self.das_monitoring_active:
                self.main_window.reset_tab3_views()

            tab2_enabled = self._is_tab2_enabled()
            if self.fip_tab2_manager and tab2_enabled:
                self.fip_tab2_manager.reset()
                self.fip_tab2_manager.sync_from_ui()
            elif self.fip_tab2_manager:
                self.main_window.clear_feature_displays()
                self.logger.info("Tab2 disabled by UI; Tab2 workers will not start.")

            self._sync_tab1_storage_settings()

            # Start TCP server
            if not self.tcp_server.start_server():
                raise RuntimeError("Failed to start TCP server")

            # Start optimized Tab1 thread system
            self.tab1_manager.start()
            if self.fip_tab2_manager and tab2_enabled:
                self.fip_tab2_manager.start()
                self.logger.info("Tab2 pipeline started by UI master switch.")

            # 启动后同步一次前面板绘图开关状态。
            # 说明：按钮初始状态不会主动触发toggled信号，
            # 若不显式同步，时域线程可能保持旧状态，表现为“需手动点一次才出波形”。
            if hasattr(self.main_window, 'time_plot_btn'):
                self._toggle_time_plotting(self.main_window.time_plot_btn.isChecked())
            if hasattr(self.main_window, 'psd_plot_btn'):
                self._toggle_psd_plotting(self.main_window.psd_plot_btn.isChecked())

            # 记录绘图状态（调试信息）
            plot_status = self.tab1_manager.get_plot_status()
            self.logger.info(f"Plot status after start: {plot_status}")
            self.fip_monitoring_active = True
            # 启动线程统计定时刷新（X-01）
            self._stats_timer.start()

            if hasattr(self.main_window, 'set_fip_monitoring_active'):
                self.main_window.set_fip_monitoring_active(True)

            self.logger.info("Monitoring system started successfully with optimized threads")

        except Exception as e:
            self.fip_monitoring_active = False
            if hasattr(self.main_window, 'set_fip_monitoring_active'):
                self.main_window.set_fip_monitoring_active(False)
            if hasattr(self.main_window, 'record_fip_comm_failure'):
                self.main_window.record_fip_comm_failure()
            self.logger.error(f"Failed to start monitoring: {e}")
            self._show_error_message("启动失败", f"监测系统启动失败: {e}")

    def _stop_monitoring(self):
        """Stop the monitoring system."""
        try:
            self.logger.info("Stopping monitoring system...")

            # Stop optimized Tab1 thread system
            if self.fip_tab2_manager and self._is_tab2_running():
                self.fip_tab2_manager.stop()
            if self.tab1_manager:
                self.tab1_manager.stop()

            # Stop TCP server
            if self.tcp_server:
                self.tcp_server.stop_server()

            # View plots remain owned by MainWindow; clear only when eDAS is not using them.
            if hasattr(self.main_window, 'reset_tab3_views') and not self.das_monitoring_active:
                self.main_window.reset_tab3_views()
            # 停止线程统计定时刷新（X-01）
            self._stats_timer.stop()
            self.fip_monitoring_active = False
            if hasattr(self.main_window, 'set_fip_monitoring_active'):
                self.main_window.set_fip_monitoring_active(False)
            self._maybe_stop_alignment_session()

            self.logger.info("Monitoring system stopped")

        except Exception as e:
            self.logger.error(f"Error stopping monitoring: {e}")

    def _update_configuration(self, new_config: dict):
        """Update system configuration."""
        try:
            self.logger.info("Updating configuration...")

            # Update configuration
            self.config.update(new_config)

            # Save configuration to file
            config_path = Path(__file__).parent.parent / 'config' / 'app_config.json'
            with open(config_path, 'w', encoding='utf-8') as f:
                json.dump(self.config, f, indent=2, ensure_ascii=False)

            # Update processors with new configuration
            self._update_processor_configs()

            self.logger.info("Configuration updated successfully")

        except Exception as e:
            self.logger.error(f"Error updating configuration: {e}")

    def _update_processor_configs(self):
        """Update processor configurations."""
        try:
            # Update signal filter
            if self.signal_filter:
                filter_config = self.config['preprocessing']['filter']
                if filter_config['type'] != 'none':
                    if filter_config['type'] == 'bandpass':
                        cutoff = (filter_config['low_freq'], filter_config['high_freq'])
                    else:
                        cutoff = filter_config.get('cutoff_freq', 1000)

                    self.signal_filter.design_filter(
                        filter_config['type'],
                        cutoff,
                        filter_config['order']
                    )

            # Update downsampler
            if self.downsampler:
                downsample_config = self.config['preprocessing']['downsample']
                self.downsampler.set_downsampling_factor(downsample_config['factor'])
                self.downsampler.set_method(downsample_config['method'])

            # 可视化设置现在通过优化的线程系统处理
            # 移除旧的wave_plotter引用

        except Exception as e:
            self.logger.error(f"Error updating processor configurations: {e}")

    def _refresh_thread_stats(self) -> None:
        """读取 Tab1 线程统计并刷新状态栏健康面板（X-01，每 2 s 调用一次）。

        仅在监测活跃时执行；若 tab1_manager 未启动则跳过。
        """
        if not self.fip_monitoring_active or self.tab1_manager is None:
            return
        try:
            stats = self.tab1_manager.get_thread_stats()
            self.main_window.update_thread_stats(stats)
        except Exception as e:
            self.logger.warning("Failed to refresh thread stats: %s", e)

    def _handle_tcp_error(self, error_message: str):
        """Handle TCP communication errors."""
        if hasattr(self.main_window, 'record_fip_comm_failure'):
            self.main_window.record_fip_comm_failure()
        self.logger.error(f"TCP Error: {error_message}")
        self._show_error_message("通信错误", f"TCP通信出现错误: {error_message}")

    def _show_error_message(self, title: str, message: str):
        """Show error message to user."""
        msg_box = QMessageBox()
        msg_box.setIcon(QMessageBox.Critical)
        msg_box.setWindowTitle(title)
        msg_box.setText(message)
        msg_box.exec_()

    def run(self):
        """Run the application."""
        try:
            # Show main window
            self.main_window.show()

            # Run event loop
            return self.app.exec_()

        except Exception as e:
            self.logger.error(f"Application runtime error: {e}")
            return -1

    # 数据存储功能已移至DataStorageThread线程中处理
    # 移除了旧的_save_phase_data_npz方法

    def _toggle_time_plotting(self, enabled: bool):
        """切换时域绘图 - 使用优化的线程系统"""
        try:
            if self.tab1_manager:
                # 每次重启“时域更新”按钮时，重新获取最新预处理参数
                if enabled:
                    self._refresh_preprocessing_parameters(source="time_plot_toggle")

                self.tab1_manager.toggle_time_plotting(enabled)
                if enabled:
                    self.logger.info("Time domain plotting enabled (optimized threads)")
                else:
                    self.logger.info("Time domain plotting disabled")
        except Exception as e:
            self.logger.error(f"Error toggling time plotting: {e}")

    def _toggle_psd_plotting(self, enabled: bool):
        """切换PSD绘图 - 使用优化的线程系统"""
        try:
            if self.tab1_manager:
                self.tab1_manager.toggle_psd_plotting(enabled)
                if enabled:
                    self.logger.info("PSD plotting enabled (optimized threads)")
                else:
                    self.logger.info("PSD plotting disabled")
        except Exception as e:
            self.logger.error(f"Error toggling PSD plotting: {e}")

    # 注意：PSD参数和滤波器参数更新现在在优化的线程系统中处理
    # 移除了旧的_update_psd_parameters和_update_filter_parameters方法

    def _update_time_parameters(self, settings: Dict[str, Any]):
        """更新时域显示参数 - 使用优化线程系统"""
        try:
            if self.tab1_manager and 'duration' in settings:
                self.tab1_manager.update_time_window(settings['duration'])
                self.logger.info(f"Updated time display duration: {settings['duration']}s (optimized threads)")

        except Exception as e:
            self.logger.error(f"Error updating time parameters: {e}")

    def _sync_tab1_storage_settings(self):
        """Sync current Tab1 storage settings from UI to the storage thread."""
        enabled = self.main_window.phase_storage_check.isChecked() if hasattr(self.main_window, 'phase_storage_check') else False
        path = self.main_window.storage_path_edit.text() if hasattr(self.main_window, 'storage_path_edit') else "D:/PCCP/FIPdata"
        interval_seconds = self.main_window.storage_interval_spin.value() if hasattr(self.main_window, 'storage_interval_spin') else 10
        self._update_storage_settings(enabled, path, interval_seconds)

    def _update_storage_settings(self, enabled: bool, path: str, interval_seconds: float):
        """Update Tab1 phase storage settings from the UI."""
        try:
            if self.tab1_manager:
                self.tab1_manager.toggle_storage(enabled)
                self.tab1_manager.update_storage_interval(interval_seconds)
                if path:
                    self.tab1_manager.update_storage_path(path)
                self.logger.info(
                    f"Storage {'enabled' if enabled else 'disabled'}, path: {path}, interval: {interval_seconds}s"
                )

        except Exception as e:
            self.logger.error(f"Error updating storage settings: {e}")

    def _update_fip_sensor_settings(self, settings: Dict[str, Any]):
        """Apply Tab1 FIP input and plotting setting changes."""
        try:
            sensor_count = int(settings.get("sensor_count", 1))
            selected_sensor = int(settings.get("selected_sensor", 1))
            packet_duration_seconds = max(float(settings.get("packet_duration_seconds", 1.0)), 1e-6)
            sample_rate_hz = max(float(settings.get("sample_rate_hz", ORIGINAL_SAMPLE_RATE)), 1.0)
            self._fip_packet_sample_rate_override_hz = None
            self._last_fip_packet_shape_signature = None
            sample_rate_changed = self._sync_signal_filter_sample_rate(sample_rate_hz, source="fip_input_settings")
            if sample_rate_changed:
                self._refresh_preprocessing_parameters(source="fip_input_settings")
            if self.tab1_manager:
                self.tab1_manager.update_fip_selection(
                    sensor_count,
                    selected_sensor,
                    packet_duration_seconds=packet_duration_seconds,
                    sample_rate_hz=sample_rate_hz,
                )
            if self.tab3_manager:
                self._sync_tab3_settings()
            self.logger.info(
                "FIP input settings updated: sensor_count=%d selected=FIP%d duration=%.6fs sample_rate=%.1fHz",
                sensor_count,
                selected_sensor,
                packet_duration_seconds,
                sample_rate_hz,
            )
        except Exception as e:
            self.logger.error(f"Error updating FIP sensor settings: {e}")

    def _start_tab3_monitoring(self):
        """Start the independent DAS monitoring pipeline."""
        try:
            self.logger.info("Starting Tab3 DAS monitoring...")
            self.logger.debug("TAB3_NODE main.start requested")
            self._ensure_alignment_session_started()
            if self.tab3_manager:
                self.tab3_manager.reset()
                self._sync_tab3_settings()
                if not self.tab3_manager.start():
                    raise RuntimeError("Failed to start DAS TCP server")
            self.das_monitoring_active = True
            if hasattr(self.main_window, 'set_tab3_monitoring_active'):
                self.main_window.set_tab3_monitoring_active(True)
        except Exception as e:
            self.logger.error(f"Failed to start Tab3 monitoring: {e}")
            self.das_monitoring_active = False
            if hasattr(self.main_window, 'set_tab3_monitoring_active'):
                self.main_window.set_tab3_monitoring_active(False)
            if hasattr(self.main_window, 'record_edas_comm_failure'):
                self.main_window.record_edas_comm_failure()
            if hasattr(self.main_window, 'show_tab3_error'):
                self.main_window.show_tab3_error(f"Failed to start DAS monitoring: {e}")

    def _stop_tab3_monitoring(self):
        """Stop the independent DAS monitoring pipeline."""
        try:
            self.logger.info("Stopping Tab3 DAS monitoring...")
            self.logger.debug("TAB3_NODE main.stop requested")
            if self.tab3_manager:
                self.tab3_manager.stop()
            self.das_monitoring_active = False
            if hasattr(self.main_window, 'set_tab3_monitoring_active'):
                self.main_window.set_tab3_monitoring_active(False)
            self._maybe_stop_alignment_session()
        except Exception as e:
            self.logger.error(f"Failed to stop Tab3 monitoring: {e}")

    def _ensure_alignment_session_started(self):
        """Start a shared alignment session if neither source is active yet."""
        if self.alignment_coordinator and not (self.fip_monitoring_active or self.das_monitoring_active):
            self.alignment_coordinator.start_session()

    def _maybe_stop_alignment_session(self):
        """Stop the shared alignment session once both sources are idle."""
        if self.alignment_coordinator and not self.fip_monitoring_active and not self.das_monitoring_active:
            self.alignment_coordinator.stop_session()

    def _sync_tab2_settings(self):
        """Push the latest Tab2 UI settings and master enable state into the manager."""
        try:
            if not self.fip_tab2_manager:
                return

            if not self._is_tab2_enabled():
                if self._is_tab2_running():
                    self.logger.info("Tab2 disabled by UI; stopping Tab2 pipeline.")
                    self.fip_tab2_manager.stop()
                self.main_window.clear_feature_displays()
                return

            self.fip_tab2_manager.sync_from_ui()
            if self.fip_monitoring_active and not self._is_tab2_running():
                self.fip_tab2_manager.reset()
                self.fip_tab2_manager.sync_from_ui()
                self.fip_tab2_manager.start()
                self.logger.info("Tab2 enabled by UI; Tab2 pipeline started.")
        except Exception as e:
            self.logger.error(f"Error syncing Tab2 settings: {e}")

    def _sync_tab3_settings(self):
        """Push the latest Tab3 UI settings into the independent Tab3 manager."""
        try:
            if self.tab3_manager:
                self.logger.debug("TAB3_NODE main.sync_settings")
                self.tab3_manager.sync_from_ui()
        except Exception as e:
            self.logger.error(f"Error syncing Tab3 settings: {e}")

    def _clear_tab2_alarms(self):
        """Clear the Tab2 alarm table."""
        if self.main_window:
            self.main_window.clear_alarm_table()

    # 滤波器参数更新已移至优化的线程系统中处理

    def _map_filter_type_from_ui(self, ui_filter_type: str) -> str:
        """将界面滤波类型映射为SignalFilter支持的类型。"""
        mapping = {
            "无滤波": "none",
            "低通": "lowpass",
            "高通": "highpass",
            "带通": "bandpass",
            "带阻": "bandstop",
            "none": "none",
            "lowpass": "lowpass",
            "highpass": "highpass",
            "bandpass": "bandpass",
            "bandstop": "bandstop",
        }
        return mapping.get(ui_filter_type, "bandpass")

    def _get_tab1_sample_rate_hz(self) -> float:
        if self._fip_packet_sample_rate_override_hz is not None:
            return max(float(self._fip_packet_sample_rate_override_hz), 1.0)
        if hasattr(self.main_window, 'get_tab1_fip_settings'):
            try:
                settings = self.main_window.get_tab1_fip_settings()
                return max(float(settings.get("sample_rate_hz", ORIGINAL_SAMPLE_RATE)), 1.0)
            except Exception:
                return ORIGINAL_SAMPLE_RATE
        return ORIGINAL_SAMPLE_RATE

    def _sync_signal_filter_sample_rate(self, sample_rate_hz: float, source: str = "runtime") -> bool:
        if not self.signal_filter:
            return False
        sample_rate_hz = max(float(sample_rate_hz), 1.0)
        old_sample_rate = float(getattr(self.signal_filter, 'sample_rate', ORIGINAL_SAMPLE_RATE))
        if abs(old_sample_rate - sample_rate_hz) <= 1e-6:
            return False
        self.signal_filter.sample_rate = sample_rate_hz
        self.logger.info(
            "[%s] FIP filter sample rate synced: %.1fHz -> %.1fHz",
            source,
            old_sample_rate,
            sample_rate_hz,
        )
        return True

    def _refresh_preprocessing_parameters(self, source: str = "runtime"):
        """从UI读取并应用最新预处理参数（滤波 + 降采样）。"""
        try:
            if not self.signal_filter or not self.downsampler:
                return

            self._sync_signal_filter_sample_rate(self._get_tab1_sample_rate_hz(), source=source)

            # 1) 降采样参数
            if hasattr(self.main_window, 'downsample_spin'):
                ui_factor = int(self.main_window.downsample_spin.value())
                old_factor = self.downsampler.get_current_factor()
                if self.downsampler.set_downsampling_factor(ui_factor):
                    if old_factor != ui_factor:
                        self.logger.info(
                            f"[{source}] Downsample factor synced: {old_factor}x -> {ui_factor}x"
                        )

            # 2) 滤波参数
            ui_type = self.main_window.filter_type_combo.currentText() if hasattr(self.main_window, 'filter_type_combo') else '带通'
            filter_type = self._map_filter_type_from_ui(ui_type)
            low_freq = self.main_window.low_freq_spin.value() if hasattr(self.main_window, 'low_freq_spin') else 100
            high_freq = self.main_window.high_freq_spin.value() if hasattr(self.main_window, 'high_freq_spin') else 10000
            order = self.main_window.filter_order_spin.value() if hasattr(self.main_window, 'filter_order_spin') else 4

            if filter_type in ('bandpass', 'bandstop'):
                cutoff = (low_freq, high_freq)
            elif filter_type == 'lowpass':
                cutoff = high_freq
            elif filter_type == 'highpass':
                cutoff = low_freq
            else:
                cutoff = low_freq

            success = self.signal_filter.design_filter(filter_type, cutoff, order)
            if success:
                self.signal_filter.reset_filter_state()
                self.logger.info(
                    f"[{source}] Preprocessing synced: filter={filter_type}, cutoff={cutoff}, order={order}, "
                    f"downsample={self.downsampler.get_current_factor()}x"
                )
            else:
                self.logger.error(f"[{source}] Failed to apply filter settings from UI")

        except Exception as e:
            self.logger.error(f"Error refreshing preprocessing parameters from UI: {e}")

    def _update_filter_parameters(self, settings: Dict[str, Any]):
        """响应UI滤波参数变化，立即同步到处理链路。"""
        try:
            if not settings:
                return

            if not self.signal_filter:
                return

            self._sync_signal_filter_sample_rate(self._get_tab1_sample_rate_hz(), source="filter_settings")

            ui_type = settings.get('type', '带通')
            filter_type = self._map_filter_type_from_ui(ui_type)
            low_freq = settings.get('low_freq', 100)
            high_freq = settings.get('high_freq', 10000)
            order = settings.get('order', 4)

            if filter_type in ('bandpass', 'bandstop'):
                cutoff = (low_freq, high_freq)
            elif filter_type == 'lowpass':
                cutoff = high_freq
            elif filter_type == 'highpass':
                cutoff = low_freq
            else:
                cutoff = low_freq

            success = self.signal_filter.design_filter(filter_type, cutoff, order)
            if success:
                self.signal_filter.reset_filter_state()
                self.logger.info(
                    f"Filter parameters updated from UI: filter={filter_type}, cutoff={cutoff}, order={order}"
                )
            else:
                self.logger.error(
                    f"Failed to update filter parameters from UI: filter={filter_type}, cutoff={cutoff}, order={order}"
                )

        except Exception as e:
            self.logger.error(f"Error updating filter parameters: {e}")

    def _update_downsample_factor(self, new_factor: int):
        """更新降采样因子 - 优化版本"""
        try:
            if self.downsampler:
                old_factor = self.downsampler.get_current_factor()
                success = self.downsampler.set_downsampling_factor(new_factor)
                if success:
                    current_input_rate = self._get_tab1_sample_rate_hz()
                    new_sample_rate = current_input_rate / new_factor
                    self.logger.info(f"Downsample factor updated: {old_factor}x -> {new_factor}x "
                                   f"({current_input_rate/1e6:.3f}MHz -> {new_sample_rate/1e3:.1f}kHz)")

                    # 通知线程系统清空缓冲区（通过重启实现）
                    if self.tab1_manager and hasattr(self.tab1_manager, 'data_processor'):
                        # 可以添加清空缓冲区的方法，这里暂时使用日志记录
                        self.logger.info("Downsample factor changed, buffers may need clearing")
                else:
                    self.logger.error(f"Failed to update downsample factor to {new_factor}")

        except Exception as e:
            self.logger.error(f"Error updating downsample factor: {e}")

    def cleanup(self):
        """Cleanup resources before exit."""
        try:
            if self.fip_tab2_manager and self._is_tab2_running():
                self.fip_tab2_manager.stop()

            if self.tab3_manager:
                self.tab3_manager.stop()

            if self.tcp_server:
                self.tcp_server.stop_server()

            self.logger.info("Application cleanup completed")

        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")


def main(args=None):
    """Main function."""
    try:
        # Create and run application
        app = PCCPMonitorApp(args)
        exit_code = app.run()

        # Cleanup
        app.cleanup()

        return exit_code

    except Exception as e:
        logging.error(f"Fatal error: {e}")
        return -1


if __name__ == '__main__':
    sys.exit(main())
