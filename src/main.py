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
import os
import logging
import json
import time
import numpy as np
from pathlib import Path
from typing import Dict, List, Tuple, Any
from PyQt5.QtWidgets import QApplication, QMessageBox
from PyQt5.QtCore import QTimer

# Import application modules
from ui.main_window import MainWindow
from comm.tcp_server_optimized import OptimizedTCPServer
from processing.phase_unwrap import PhaseUnwrapper
from processing.signal_filter import SignalFilter
from processing.downsampling import Downsampler
from processing.tab1_optimized_threads import OptimizedTab1ThreadManager  # 优化的Tab1多线程系统
from visualization.wave_plotter import PSDCalculator  # 只需要PSD计算器
from features.feature_calculator import FeatureCalculator
from detection.threshold_detector import ThresholdDetector
from storage.detection_storage import DetectionStorage

# Import system configuration
from config import (
    ORIGINAL_SAMPLE_RATE,
    SYSTEM_DOWNSAMPLE_FACTOR,
    EFFECTIVE_SAMPLE_RATE,
    TIME_DISPLAY_DOWNSAMPLE,
    TIME_DISPLAY_SAMPLE_RATE,
    PACKET_DURATION,
    PERFORMANCE_LOG_INTERVAL,
    FEATURE_PROCESSING_INTERVAL,
    get_sample_rate_info
)


class PCCPMonitorApp:
    """
    Main application controller for PCCP monitoring system.

    This class coordinates all subsystems including TCP communication,
    data processing, visualization, and user interface.
    """

    def __init__(self):
        """Initialize the PCCP monitoring application."""
        # Setup logging
        self._setup_logging()
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

        # Tab2 components
        self.feature_calculator = None
        self.threshold_detector = None
        self.detection_storage = None

        # Setup connections
        self._setup_connections()

        # Initialize processors
        self._initialize_processors()

    def _setup_logging(self):
        """Setup application logging."""
        # Create logs directory if it doesn't exist
        log_dir = Path(__file__).parent.parent / 'logs'
        log_dir.mkdir(exist_ok=True)

        # Configure logging with DEBUG level for detailed output
        logging.basicConfig(
            level=logging.DEBUG,  # 改为DEBUG级别
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.FileHandler(log_dir / 'pccp_monitor.log'),
                logging.StreamHandler()  # 同时输出到控制台
            ]
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
                    "interval": 30,
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
                lambda enabled: self._update_storage_settings(enabled,
                    self.main_window.storage_path_edit.text() if hasattr(self.main_window, 'storage_path_edit') else "D:/PCCP/FIPdata"))

        # Connect downsampling control
        if hasattr(self.main_window, 'downsample_spin'):
            self.main_window.downsample_spin.valueChanged.connect(self._update_downsample_factor)

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

            # Set plot widgets
            self.tab1_manager.set_plot_widgets(
                self.main_window.time_plot,
                self.main_window.psd_plot
            )

            # 启动前先同步一次前面板预处理参数，确保处理链路与UI一致
            self._refresh_preprocessing_parameters(source="init")

            # Initialize Tab2 components - 使用原始采样率初始化
            self.feature_calculator = FeatureCalculator(
                sample_rate=ORIGINAL_SAMPLE_RATE,  # 运行时会根据实际降采样因子调整
                window_size_ms=50.0,
                overlap_ratio=0.5
            )

            self.threshold_detector = ThresholdDetector()

            # Initialize detection storage
            storage_path = self.config.get('storage', {}).get('path', 'D:/PCCP/FIPmonitor')
            self.detection_storage = DetectionStorage(storage_path=storage_path)

            self.logger.info("All processors initialized successfully")

        except Exception as e:
            self.logger.error(f"Error initializing processors: {e}")
            self._show_error_message("初始化失败", f"处理器初始化失败: {e}")

    def _process_data_packet(self, packet):
        """
        Process received data packet - OPTIMIZED VERSION
        主线程仅负责数据分发，所有CPU密集型操作移至后台线程

        Args:
            packet: DataPacket from optimized TCP server
        """
        # 立即记录数据包接收（确认信号连接正常）
        self.logger.info(f"MAIN THREAD: Received packet #{packet.comm_count}, data size: {len(packet.phase_data)}")

        try:
            # 记录数据包接收（用于调试）
            if packet.comm_count % 50 == 0:
                self.logger.info(
                    f"Received packet #{packet.comm_count}: "
                    f"{len(packet.phase_data)} points, "
                    f"range=[{np.min(packet.phase_data):.3f}, {np.max(packet.phase_data):.3f}]"
                )

            # 简单的数据包格式转换
            from processing.tab1_optimized_threads import RawDataPacket

            raw_packet = RawDataPacket(
                timestamp=packet.timestamp,
                phase_data=packet.phase_data,
                comm_count=packet.comm_count
            )

            # 仅将数据包发送到后台处理线程，主线程立即返回
            success = self.tab1_manager.process_raw_packet(raw_packet)

            if not success and packet.comm_count % 100 == 0:
                self.logger.warning(f"Failed to queue packet #{packet.comm_count} - processing thread busy")

            # 更新统计信息（保留原有功能）
            if packet.comm_count % 5 == 0:  # 每5个包更新一次统计
                tcp_stats = self.tcp_server.get_statistics()
                self.main_window.update_statistics(tcp_stats)

            # Tab2 feature processing (简化处理，使用已降采样的数据)
            if packet.comm_count % FEATURE_PROCESSING_INTERVAL == 0:
                # 这部分保留在主线程，因为Tab2不是本次优化重点
                # TODO: 将来可以进一步优化Tab2的线程架构
                pass

        except Exception as e:
            self.logger.error(f"Error processing data packet #{packet.comm_count}: {e}")

    def _start_monitoring(self):
        """Start the monitoring system."""
        try:
            self.logger.info("Starting monitoring system...")

            # Reset processor states
            self.phase_unwrapper.reset()
            if self.signal_filter is not None:
                self.signal_filter.reset_filter_state()
            self.downsampler.reset_state()

            # 清空绘图控件，确保从干净的状态开始
            self.main_window.time_plot.clear()
            self.main_window.psd_plot.clear()

            # 重新绑定绘图曲线引用。
            # 注意：clear() 会删除已有 PlotDataItem，线程管理器中旧引用会失效。
            self.tab1_manager.set_plot_widgets(
                self.main_window.time_plot,
                self.main_window.psd_plot
            )

            # Reset Tab2 components
            if self.feature_calculator:
                self.feature_calculator.reset()
            if self.threshold_detector:
                self.threshold_detector.reset()

            # Start TCP server
            if not self.tcp_server.start_server():
                raise RuntimeError("Failed to start TCP server")

            # Start optimized Tab1 thread system
            self.tab1_manager.start()

            # 记录绘图状态（调试信息）
            plot_status = self.tab1_manager.get_plot_status()
            self.logger.info(f"Plot status after start: {plot_status}")

            self.logger.info("Monitoring system started successfully with optimized threads")

        except Exception as e:
            self.logger.error(f"Failed to start monitoring: {e}")
            self._show_error_message("启动失败", f"监测系统启动失败: {e}")

    def _stop_monitoring(self):
        """Stop the monitoring system."""
        try:
            self.logger.info("Stopping monitoring system...")

            # Stop optimized Tab1 thread system
            if self.tab1_manager:
                self.tab1_manager.stop()

            # Stop TCP server
            if self.tcp_server:
                self.tcp_server.stop_server()

            # 清空绘图控件
            self.main_window.time_plot.clear()
            self.main_window.psd_plot.clear()

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

    def _handle_tcp_error(self, error_message: str):
        """Handle TCP communication errors."""
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

    def _update_tab2_features(self, features: Dict[str, List[Tuple[float, float]]]):
        """
        Update Tab2 with new feature data.

        Args:
            features: Dictionary mapping feature names to (timestamp, value) lists
        """
        try:
            # Update feature plots in Tab2 if available
            if hasattr(self.main_window, 'feature_plots'):
                for feature_name, feature_data in features.items():
                    if feature_data:  # Only process if there's data
                        # Find which plot widget should display this feature
                        # This will be connected to Tab2 UI controls later
                        pass

            # Update feature displays in Tab2
            if hasattr(self.main_window, 'update_feature_displays'):
                self.main_window.update_feature_displays(features)

        except Exception as e:
            self.logger.error(f"Error updating Tab2 features: {e}")

    def _update_tab2_detections(self, detections):
        """
        Update Tab2 with new detection results.

        Args:
            detections: List of DetectionResult objects
        """
        try:
            # Save detection results to storage
            for detection in detections:
                self.detection_storage.save_detection(detection)

            # Update detection table and alerts in Tab2
            if hasattr(self.main_window, 'add_detection_results'):
                self.main_window.add_detection_results(detections)

            # Log significant detections
            for detection in detections:
                self.logger.info(
                    f"Detection: {detection.feature_name} "
                    f"value={detection.feature_value:.3f} "
                    f"threshold={detection.threshold:.3f} "
                    f"seq={detection.sequence_number}"
                )

        except Exception as e:
            self.logger.error(f"Error updating Tab2 detections: {e}")

    def _sync_tab2_settings(self):
        """同步Tab2界面设置到处理模块"""
        try:
            # 获取Tab2启用的特征
            enabled_features = self.main_window.get_enabled_features()
            if enabled_features:
                self.feature_calculator.set_enabled_features(enabled_features)

            # 获取阈值设置
            threshold_factors = self.main_window.get_threshold_factors()
            if threshold_factors:
                self.threshold_detector.set_threshold_factors(threshold_factors)

            self.logger.info(f"Synced Tab2 settings: features={enabled_features}, "
                           f"thresholds={threshold_factors}")

        except Exception as e:
            self.logger.error(f"Error syncing Tab2 settings: {e}")

    def _update_tab2_baselines(self, baselines: Dict[str, float]):
        """更新Tab2的基线显示"""
        try:
            if hasattr(self.main_window, 'update_baselines'):
                self.main_window.update_baselines(baselines)
        except Exception as e:
            self.logger.error(f"Error updating Tab2 baselines: {e}")

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

    def _update_storage_settings(self, enabled: bool, path: str):
        """更新存储设置"""
        try:
            if self.tab1_manager:
                self.tab1_manager.toggle_storage(enabled)
                if path:
                    self.tab1_manager.update_storage_path(path)
                self.logger.info(f"Storage {'enabled' if enabled else 'disabled'}, path: {path}")

        except Exception as e:
            self.logger.error(f"Error updating storage settings: {e}")

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

    def _refresh_preprocessing_parameters(self, source: str = "runtime"):
        """从UI读取并应用最新预处理参数（滤波 + 降采样）。"""
        try:
            if not self.signal_filter or not self.downsampler:
                return

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
                    new_sample_rate = ORIGINAL_SAMPLE_RATE / new_factor
                    self.logger.info(f"Downsample factor updated: {old_factor}x -> {new_factor}x "
                                   f"({ORIGINAL_SAMPLE_RATE/1e6:.1f}MHz -> {new_sample_rate/1e3:.1f}kHz)")

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
            if self.tcp_server:
                self.tcp_server.stop_server()

            # Cleanup detection storage
            if self.detection_storage:
                self.detection_storage.cleanup()

            self.logger.info("Application cleanup completed")

        except Exception as e:
            self.logger.error(f"Error during cleanup: {e}")


def main(args=None):
    """Main function."""
    try:
        # Create and run application
        app = PCCPMonitorApp()
        exit_code = app.run()

        # Cleanup
        app.cleanup()

        return exit_code

    except Exception as e:
        logging.error(f"Fatal error: {e}")
        return -1


if __name__ == '__main__':
    sys.exit(main())