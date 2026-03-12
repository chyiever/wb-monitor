"""
多线程数据处理模块

将数据处理从主线程分离，避免UI阻塞。

Author: Claude
Date: 2026-03-12
"""

import time
import logging
import numpy as np
from typing import Dict, Any, Optional
from queue import Queue, Empty
from dataclasses import dataclass
from threading import Lock
from PyQt5.QtCore import QThread, pyqtSignal, QObject, QTimer

from config import (
    ORIGINAL_SAMPLE_RATE,
    EFFECTIVE_SAMPLE_RATE,
    PERFORMANCE_LOG_INTERVAL,
    FEATURE_PROCESSING_INTERVAL
)


@dataclass
class ProcessedDataPacket:
    """处理后的数据包"""
    packet_id: int
    timestamp: float

    # 原始数据信息
    original_length: int

    # 处理后的数据
    filtered_phase: np.ndarray     # 滤波后数据 (200kHz)
    downsampled_phase: np.ndarray  # 降采样后数据 (200kHz)

    # 可视化数据 (预处理好的)
    time_display_data: np.ndarray  # 时域显示数据 (100kHz)
    psd_frequencies: Optional[np.ndarray] = None  # PSD频率
    psd_values: Optional[np.ndarray] = None       # PSD功率谱

    # 特征数据
    features: Optional[Dict[str, float]] = None
    detections: Optional[list] = None

    # 性能统计
    processing_time: float = 0.0
    effective_sample_rate: float = EFFECTIVE_SAMPLE_RATE


class DataProcessingWorker(QObject):
    """数据处理工作线程"""

    # 信号定义
    data_processed = pyqtSignal(ProcessedDataPacket)
    processing_stats = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__ + '.DataProcessingWorker')

        # 处理队列
        self.input_queue = Queue(maxsize=10)  # 限制队列大小避免内存积压
        self.running = False

        # 处理组件（将在主线程初始化后传入）
        self.phase_unwrapper = None
        self.signal_filter = None
        self.downsampler = None
        self.feature_calculator = None
        self.threshold_detector = None

        # PSD计算组件
        self.psd_calculator = None

        # 统计信息
        self.stats = {
            'packets_processed': 0,
            'packets_dropped': 0,
            'avg_processing_time': 0.0,
            'queue_size': 0
        }
        self._stats_lock = Lock()

    def set_processors(self, phase_unwrapper, signal_filter, downsampler,
                      feature_calculator, threshold_detector, psd_calculator):
        """设置处理组件（从主线程传入）"""
        self.phase_unwrapper = phase_unwrapper
        self.signal_filter = signal_filter
        self.downsampler = downsampler
        self.feature_calculator = feature_calculator
        self.threshold_detector = threshold_detector
        self.psd_calculator = psd_calculator

        self.logger.info("Processing components set successfully")

    def add_packet(self, packet) -> bool:
        """添加数据包到处理队列"""
        try:
            if not self.running:
                return False

            # 检查队列是否已满
            if self.input_queue.full():
                # 丢弃最老的数据包
                try:
                    self.input_queue.get_nowait()
                    with self._stats_lock:
                        self.stats['packets_dropped'] += 1
                    self.logger.warning("Dropped packet due to full queue")
                except Empty:
                    pass

            self.input_queue.put(packet, block=False)
            return True

        except Exception as e:
            self.logger.error(f"Error adding packet to queue: {e}")
            return False

    def start_processing(self):
        """开始处理数据"""
        self.running = True
        self.logger.info("Data processing worker started")

        # 启动处理循环
        QTimer.singleShot(0, self._processing_loop)

    def stop_processing(self):
        """停止处理数据"""
        self.running = False
        self.logger.info("Data processing worker stopped")

    def _processing_loop(self):
        """主处理循环"""
        if not self.running:
            return

        try:
            # 检查是否有数据包需要处理
            packet = self.input_queue.get(timeout=0.001)  # 1ms超时

            # 处理数据包
            processed = self._process_packet(packet)
            if processed:
                self.data_processed.emit(processed)

                # 更新统计
                with self._stats_lock:
                    self.stats['packets_processed'] += 1
                    self.stats['queue_size'] = self.input_queue.qsize()

                    # 每50个包发送一次统计信息
                    if self.stats['packets_processed'] % PERFORMANCE_LOG_INTERVAL == 0:
                        self.processing_stats.emit(self.stats.copy())

        except Empty:
            # 队列为空，正常情况
            pass
        except Exception as e:
            self.logger.error(f"Error in processing loop: {e}")
            self.error_occurred.emit(str(e))

        # 继续处理循环
        if self.running:
            QTimer.singleShot(1, self._processing_loop)  # 1ms延迟

    def _process_packet(self, packet) -> Optional[ProcessedDataPacket]:
        """处理单个数据包"""
        process_start = time.time()

        try:
            # 提取相位数据
            phase_data = packet.phase_data

            # 数据预处理
            if np.max(np.abs(phase_data)) > 5:
                normalized_phase = phase_data / np.pi
            else:
                normalized_phase = phase_data

            # 1. 相位展开
            unwrapped_phase, _ = self.phase_unwrapper.unwrap_phase(normalized_phase)
            if len(unwrapped_phase) == 0:
                self.logger.warning(f"Phase unwrapping failed for packet #{packet.comm_count}")
                return None

            # 2. 信号滤波 (1MHz)
            if self.signal_filter is not None:
                filtered_phase, _ = self.signal_filter.apply_filter(unwrapped_phase)
            else:
                filtered_phase = unwrapped_phase.copy()

            # 3. 降采样 (1MHz -> 200kHz)
            downsampled_phase, _ = self.downsampler.downsample(filtered_phase)
            effective_sample_rate = ORIGINAL_SAMPLE_RATE / self.downsampler.get_current_factor()

            # 4. 时域显示数据预处理 (200kHz -> 100kHz)
            time_display_data = downsampled_phase[::2]  # 简单2倍降采样用于显示

            # 5. 特征计算 (每3个包处理一次)
            features = None
            detections = None
            if packet.comm_count % FEATURE_PROCESSING_INTERVAL == 0:
                if self.feature_calculator:
                    features = self.feature_calculator.process_data(downsampled_phase)
                if self.threshold_detector and features:
                    detections = self.threshold_detector.process_features(features)

            # 6. PSD计算 (每5个包计算一次，减少负载)
            psd_frequencies = None
            psd_values = None
            if packet.comm_count % 5 == 0 and self.psd_calculator:
                try:
                    psd_frequencies, psd_power = self.psd_calculator.compute_psd(downsampled_phase)
                    if len(psd_frequencies) > 0:
                        # 转换为dB并限制范围
                        psd_floor = 1e-15
                        psd_safe = np.maximum(psd_power, psd_floor)
                        psd_values = np.clip(10 * np.log10(psd_safe), -200, 100)
                except Exception as e:
                    self.logger.warning(f"PSD calculation failed: {e}")

            # 创建处理结果
            processing_time = time.time() - process_start

            result = ProcessedDataPacket(
                packet_id=packet.comm_count,
                timestamp=packet.timestamp,
                original_length=len(phase_data),
                filtered_phase=filtered_phase,
                downsampled_phase=downsampled_phase,
                time_display_data=time_display_data,
                psd_frequencies=psd_frequencies,
                psd_values=psd_values,
                features=features,
                detections=detections,
                processing_time=processing_time,
                effective_sample_rate=effective_sample_rate
            )

            # 性能日志
            if packet.comm_count % PERFORMANCE_LOG_INTERVAL == 0:
                self.logger.info(
                    f"Processed packet #{packet.comm_count}: "
                    f"{len(phase_data)} -> {len(downsampled_phase)} points, "
                    f"time={processing_time*1000:.1f}ms, "
                    f"queue={self.input_queue.qsize()}"
                )

            return result

        except Exception as e:
            self.logger.error(f"Error processing packet #{packet.comm_count}: {e}")
            return None


class ThreadedDataProcessor(QObject):
    """多线程数据处理管理器"""

    # 信号
    data_ready = pyqtSignal(ProcessedDataPacket)
    stats_updated = pyqtSignal(dict)
    processing_error = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__ + '.ThreadedDataProcessor')

        # 创建工作线程
        self.worker_thread = QThread()
        self.worker = DataProcessingWorker()

        # 将worker移动到线程
        self.worker.moveToThread(self.worker_thread)

        # 连接信号
        self.worker.data_processed.connect(self.data_ready.emit)
        self.worker.processing_stats.connect(self.stats_updated.emit)
        self.worker.error_occurred.connect(self.processing_error.emit)

        # 启动工作线程
        self.worker_thread.start()

        self.logger.info("Threaded data processor initialized")

    def set_processors(self, phase_unwrapper, signal_filter, downsampler,
                      feature_calculator, threshold_detector, psd_calculator):
        """设置处理组件"""
        self.worker.set_processors(
            phase_unwrapper, signal_filter, downsampler,
            feature_calculator, threshold_detector, psd_calculator
        )

    def start_processing(self):
        """开始处理"""
        self.worker.start_processing()

    def stop_processing(self):
        """停止处理"""
        self.worker.stop_processing()

    def add_packet(self, packet) -> bool:
        """添加数据包"""
        return self.worker.add_packet(packet)

    def shutdown(self):
        """关闭处理器"""
        self.logger.info("Shutting down threaded data processor...")

        self.worker.stop_processing()

        # 等待线程结束
        if self.worker_thread.isRunning():
            self.worker_thread.quit()
            if not self.worker_thread.wait(5000):  # 5秒超时
                self.logger.warning("Worker thread did not quit gracefully, terminating...")
                self.worker_thread.terminate()
                self.worker_thread.wait()

        self.logger.info("Threaded data processor shut down")