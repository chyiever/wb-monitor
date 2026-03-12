"""
完整多线程架构设计

将所有数据处理和UI更新分离到不同的线程，实现真正的并行处理。

线程架构：
1. 主线程：只负责应用程序控制和轻量级协调
2. TCP通信线程：数据接收
3. 数据处理线程：相位展开、滤波、降采样、特征计算
4. 时域绘图线程：时域数据可视化
5. PSD绘图线程：PSD计算和可视化
6. Tab2更新线程：特征显示和检测结果
7. Tab3更新线程：DAS数据处理（预留）
8. Tab4更新线程：信号定位（预留）
9. 存储线程：数据存储

Author: Claude
Date: 2026-03-12
"""

import time
import logging
import numpy as np
from typing import Dict, Any, Optional, List
from queue import Queue, Empty
from dataclasses import dataclass
from threading import Lock
from PyQt5.QtCore import QThread, pyqtSignal, QObject, QTimer, QMutex
import pyqtgraph as pg


@dataclass
class RawDataPacket:
    """原始数据包"""
    packet_id: int
    timestamp: float
    phase_data: np.ndarray
    comm_count: int


@dataclass
class ProcessedDataPacket:
    """处理后的数据包"""
    packet_id: int
    timestamp: float

    # 处理后的数据
    unwrapped_phase: np.ndarray    # 相位展开数据 (1MHz)
    filtered_phase: np.ndarray     # 滤波数据 (1MHz)
    downsampled_phase: np.ndarray  # 降采样数据 (200kHz)

    # 特征数据
    features: Optional[Dict[str, float]] = None
    detections: Optional[list] = None

    # 性能信息
    processing_time: float = 0.0
    effective_sample_rate: float = 200000.0


@dataclass
class VisualizationData:
    """可视化数据包"""
    packet_id: int
    timestamp: float

    # 时域数据
    time_data: Optional[np.ndarray] = None
    time_timestamps: Optional[np.ndarray] = None

    # PSD数据
    psd_frequencies: Optional[np.ndarray] = None
    psd_values: Optional[np.ndarray] = None

    # Tab2数据
    features: Optional[Dict[str, float]] = None
    detections: Optional[list] = None


class DataProcessingThread(QThread):
    """数据处理线程"""

    data_processed = pyqtSignal(ProcessedDataPacket)
    processing_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__ + '.DataProcessingThread')

        self.input_queue = Queue(maxsize=20)
        self.running = False

        # 处理组件
        self.phase_unwrapper = None
        self.signal_filter = None
        self.downsampler = None
        self.feature_calculator = None
        self.threshold_detector = None

    def set_processors(self, phase_unwrapper, signal_filter, downsampler,
                      feature_calculator, threshold_detector):
        """设置处理组件"""
        self.phase_unwrapper = phase_unwrapper
        self.signal_filter = signal_filter
        self.downsampler = downsampler
        self.feature_calculator = feature_calculator
        self.threshold_detector = threshold_detector

    def add_data(self, raw_packet: RawDataPacket) -> bool:
        """添加原始数据"""
        try:
            if self.input_queue.full():
                self.input_queue.get_nowait()  # 丢弃最老的数据
                self.logger.warning("Dropped old data packet")

            self.input_queue.put(raw_packet, block=False)
            return True
        except Exception as e:
            self.logger.error(f"Error adding data: {e}")
            return False

    def stop_processing(self):
        """停止处理"""
        self.running = False

    def run(self):
        """线程主循环"""
        self.running = True
        self.logger.info("Data processing thread started")

        while self.running:
            try:
                # 获取数据包
                raw_packet = self.input_queue.get(timeout=0.1)

                # 处理数据
                processed = self._process_packet(raw_packet)
                if processed:
                    self.data_processed.emit(processed)

            except Empty:
                continue
            except Exception as e:
                self.logger.error(f"Processing error: {e}")
                self.processing_error.emit(str(e))

        self.logger.info("Data processing thread stopped")

    def _process_packet(self, raw_packet: RawDataPacket) -> Optional[ProcessedDataPacket]:
        """处理数据包"""
        start_time = time.time()

        try:
            phase_data = raw_packet.phase_data

            # 数据预处理
            if np.max(np.abs(phase_data)) > 5:
                normalized_phase = phase_data / np.pi
            else:
                normalized_phase = phase_data

            # 相位展开
            unwrapped_phase, _ = self.phase_unwrapper.unwrap_phase(normalized_phase)
            if len(unwrapped_phase) == 0:
                return None

            # 滤波
            if self.signal_filter is not None:
                filtered_phase, _ = self.signal_filter.apply_filter(unwrapped_phase)
            else:
                filtered_phase = unwrapped_phase.copy()

            # 降采样
            downsampled_phase, _ = self.downsampler.downsample(filtered_phase)

            # 特征计算
            features = None
            detections = None
            if raw_packet.comm_count % 3 == 0:  # 每3个包计算一次特征
                if self.feature_calculator:
                    features = self.feature_calculator.process_data(downsampled_phase)
                if self.threshold_detector and features:
                    detections = self.threshold_detector.process_features(features)

            processing_time = time.time() - start_time

            return ProcessedDataPacket(
                packet_id=raw_packet.packet_id,
                timestamp=raw_packet.timestamp,
                unwrapped_phase=unwrapped_phase,
                filtered_phase=filtered_phase,
                downsampled_phase=downsampled_phase,
                features=features,
                detections=detections,
                processing_time=processing_time,
                effective_sample_rate=200000.0 / self.downsampler.get_current_factor() if self.downsampler else 200000.0
            )

        except Exception as e:
            self.logger.error(f"Error processing packet {raw_packet.packet_id}: {e}")
            return None


class TimeDomainPlotThread(QThread):
    """时域绘图线程"""

    plot_updated = pyqtSignal()
    plot_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__ + '.TimeDomainPlotThread')

        self.input_queue = Queue(maxsize=10)
        self.running = False

        # 绘图组件（需要从主线程设置）
        self.plot_widget = None
        self.plot_curve = None

        # 数据缓冲
        self.data_buffer = []
        self.time_buffer = []
        self.max_display_points = 50000
        self.display_window = 1.0  # 1秒显示窗口

    def set_plot_widget(self, plot_widget, plot_curve):
        """设置绘图组件"""
        self.plot_widget = plot_widget
        self.plot_curve = plot_curve

    def add_data(self, processed_data: ProcessedDataPacket):
        """添加处理后的数据"""
        try:
            # 时域显示专用降采样：200kHz -> 100kHz
            time_data = processed_data.downsampled_phase[::2]

            viz_data = VisualizationData(
                packet_id=processed_data.packet_id,
                timestamp=processed_data.timestamp,
                time_data=time_data
            )

            if not self.input_queue.full():
                self.input_queue.put(viz_data, block=False)
            else:
                self.input_queue.get_nowait()  # 丢弃旧数据
                self.input_queue.put(viz_data, block=False)

        except Exception as e:
            self.logger.error(f"Error adding time domain data: {e}")

    def run(self):
        """线程主循环"""
        self.running = True
        self.logger.info("Time domain plot thread started")

        while self.running:
            try:
                viz_data = self.input_queue.get(timeout=0.1)
                self._update_plot(viz_data)

            except Empty:
                continue
            except Exception as e:
                self.logger.error(f"Time domain plot error: {e}")
                self.plot_error.emit(str(e))

        self.logger.info("Time domain plot thread stopped")

    def _update_plot(self, viz_data: VisualizationData):
        """更新绘图"""
        try:
            if not self.plot_curve or viz_data.time_data is None:
                return

            # 添加新数据到缓冲区
            time_data = viz_data.time_data
            start_time = viz_data.timestamp

            # 计算时间戳
            dt = 1.0 / 100000.0  # 100kHz显示采样率
            timestamps = start_time + np.arange(len(time_data)) * dt

            self.data_buffer.extend(time_data)
            self.time_buffer.extend(timestamps)

            # 保持缓冲区大小
            while len(self.data_buffer) > self.max_display_points * 2:
                del self.data_buffer[:1000]
                del self.time_buffer[:1000]

            # 获取显示窗口内的数据
            if len(self.time_buffer) > 0:
                current_end = self.time_buffer[-1]
                cutoff_time = current_end - self.display_window

                display_times = []
                display_values = []

                for t, v in zip(self.time_buffer, self.data_buffer):
                    if t >= cutoff_time:
                        display_times.append(t - self.time_buffer[0])  # 相对时间
                        display_values.append(v)

                # 降采样显示
                if len(display_times) > self.max_display_points:
                    step = len(display_times) // self.max_display_points
                    display_times = display_times[::step]
                    display_values = display_values[::step]

                # 使用QTimer.singleShot确保在主线程更新UI
                if len(display_times) > 0:
                    QTimer.singleShot(0, lambda: self._safe_update_curve(display_times, display_values))

        except Exception as e:
            self.logger.error(f"Error in time domain plot update: {e}")

    def _safe_update_curve(self, times, values):
        """线程安全的曲线更新"""
        try:
            if self.plot_curve:
                self.plot_curve.setData(times, values)
                self.plot_updated.emit()
        except Exception as e:
            self.logger.error(f"Error updating time curve: {e}")

    def stop_processing(self):
        """停止处理"""
        self.running = False


class PSDPlotThread(QThread):
    """PSD绘图线程"""

    plot_updated = pyqtSignal()
    plot_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__ + '.PSDPlotThread')

        self.input_queue = Queue(maxsize=5)  # PSD更新频率较低
        self.running = False

        # PSD计算组件
        self.psd_calculator = None

        # 绘图组件
        self.plot_widget = None
        self.plot_curve = None

    def set_plot_widget(self, plot_widget, plot_curve):
        """设置绘图组件"""
        self.plot_widget = plot_widget
        self.plot_curve = plot_curve

    def set_psd_calculator(self, psd_calculator):
        """设置PSD计算器"""
        self.psd_calculator = psd_calculator

    def add_data(self, processed_data: ProcessedDataPacket):
        """添加处理后的数据"""
        try:
            # PSD使用200kHz数据
            viz_data = VisualizationData(
                packet_id=processed_data.packet_id,
                timestamp=processed_data.timestamp,
                time_data=processed_data.downsampled_phase  # 用于PSD计算
            )

            if not self.input_queue.full():
                self.input_queue.put(viz_data, block=False)

        except Exception as e:
            self.logger.error(f"Error adding PSD data: {e}")

    def run(self):
        """线程主循环"""
        self.running = True
        self.logger.info("PSD plot thread started")

        while self.running:
            try:
                viz_data = self.input_queue.get(timeout=0.2)
                self._update_psd_plot(viz_data)

            except Empty:
                continue
            except Exception as e:
                self.logger.error(f"PSD plot error: {e}")
                self.plot_error.emit(str(e))

        self.logger.info("PSD plot thread stopped")

    def _update_psd_plot(self, viz_data: VisualizationData):
        """更新PSD绘图"""
        try:
            if not self.plot_curve or not self.psd_calculator or viz_data.time_data is None:
                return

            # 计算PSD
            frequencies, psd_power = self.psd_calculator.compute_psd(viz_data.time_data)

            if len(frequencies) > 0:
                # 转换为dB
                psd_floor = 1e-15
                psd_safe = np.maximum(psd_power, psd_floor)
                psd_db = np.clip(10 * np.log10(psd_safe), -200, 100)

                # 使用QTimer.singleShot确保在主线程更新UI
                QTimer.singleShot(0, lambda: self._safe_update_psd_curve(frequencies, psd_db))

        except Exception as e:
            self.logger.error(f"Error in PSD calculation: {e}")

    def _safe_update_psd_curve(self, frequencies, psd_values):
        """线程安全的PSD曲线更新"""
        try:
            if self.plot_curve:
                self.plot_curve.setData(frequencies, psd_values)
                self.plot_updated.emit()
        except Exception as e:
            self.logger.error(f"Error updating PSD curve: {e}")

    def stop_processing(self):
        """停止处理"""
        self.running = False


class Tab2UpdateThread(QThread):
    """Tab2更新线程"""

    features_updated = pyqtSignal(dict)
    detections_updated = pyqtSignal(list)

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__ + '.Tab2UpdateThread')

        self.input_queue = Queue(maxsize=10)
        self.running = False

    def add_data(self, processed_data: ProcessedDataPacket):
        """添加处理后的数据"""
        try:
            if processed_data.features or processed_data.detections:
                if not self.input_queue.full():
                    self.input_queue.put(processed_data, block=False)
        except Exception as e:
            self.logger.error(f"Error adding Tab2 data: {e}")

    def run(self):
        """线程主循环"""
        self.running = True
        self.logger.info("Tab2 update thread started")

        while self.running:
            try:
                processed_data = self.input_queue.get(timeout=0.1)

                if processed_data.features:
                    self.features_updated.emit(processed_data.features)

                if processed_data.detections:
                    self.detections_updated.emit(processed_data.detections)

            except Empty:
                continue
            except Exception as e:
                self.logger.error(f"Tab2 update error: {e}")

        self.logger.info("Tab2 update thread stopped")

    def stop_processing(self):
        """停止处理"""
        self.running = False


class StorageThread(QThread):
    """数据存储线程"""

    storage_completed = pyqtSignal(str)
    storage_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__ + '.StorageThread')

        self.input_queue = Queue(maxsize=20)
        self.running = False

        self.storage_enabled = False
        self.storage_path = "D:/PCCP/FIPdata"

    def add_data(self, processed_data: ProcessedDataPacket):
        """添加数据用于存储"""
        try:
            if self.storage_enabled and not self.input_queue.full():
                self.input_queue.put(processed_data, block=False)
        except Exception as e:
            self.logger.error(f"Error adding storage data: {e}")

    def set_storage_settings(self, enabled: bool, path: str):
        """设置存储参数"""
        self.storage_enabled = enabled
        self.storage_path = path

    def run(self):
        """线程主循环"""
        self.running = True
        self.logger.info("Storage thread started")

        while self.running:
            try:
                processed_data = self.input_queue.get(timeout=0.1)
                self._save_data(processed_data)

            except Empty:
                continue
            except Exception as e:
                self.logger.error(f"Storage error: {e}")
                self.storage_error.emit(str(e))

        self.logger.info("Storage thread stopped")

    def _save_data(self, processed_data: ProcessedDataPacket):
        """保存数据"""
        try:
            # 这里实现数据存储逻辑
            # 可以存储downsampled_phase或其他需要的数据
            pass

        except Exception as e:
            self.logger.error(f"Error saving data: {e}")

    def stop_processing(self):
        """停止处理"""
        self.running = False