"""
Tab1多线程架构 - 简洁版本

专注于解决Tab1卡死问题的最小化多线程实现。

线程分工：
1. 主线程：UI控制 + 轻量级更新
2. 数据处理线程：相位展开 + 滤波 + 降采样
3. 时域绘图线程：时域数据处理和绘制
4. PSD绘图线程：PSD计算和绘制

Author: Claude
Date: 2026-03-12
"""

import time
import logging
import numpy as np
from queue import Queue, Empty
from dataclasses import dataclass
from PyQt5.QtCore import QThread, pyqtSignal, QTimer
import pyqtgraph as pg


@dataclass
class ProcessedData:
    """处理后的数据"""
    timestamp: float
    downsampled_data: np.ndarray  # 200kHz数据
    effective_rate: float = 200000.0


class DataProcessor(QThread):
    """数据处理线程"""
    data_ready = pyqtSignal(ProcessedData)

    def __init__(self, phase_unwrapper, signal_filter, downsampler):
        super().__init__()
        self.input_queue = Queue(maxsize=10)
        self.running = False

        # 处理组件
        self.phase_unwrapper = phase_unwrapper
        self.signal_filter = signal_filter
        self.downsampler = downsampler

        self.logger = logging.getLogger('DataProcessor')

    def add_packet(self, packet):
        """添加数据包"""
        try:
            if self.input_queue.full():
                self.input_queue.get_nowait()  # 丢弃旧数据
            self.input_queue.put(packet, block=False)
            return True
        except:
            return False

    def run(self):
        """处理循环"""
        self.running = True
        while self.running:
            try:
                packet = self.input_queue.get(timeout=0.1)
                processed = self._process(packet)
                if processed:
                    self.data_ready.emit(processed)
            except Empty:
                continue
            except Exception as e:
                self.logger.error(f"Processing error: {e}")

    def _process(self, packet):
        """处理数据"""
        try:
            # 数据预处理
            phase_data = packet.phase_data
            if np.max(np.abs(phase_data)) > 5:
                phase_data = phase_data / np.pi

            # 处理流程：展开 -> 滤波 -> 降采样
            unwrapped, _ = self.phase_unwrapper.unwrap_phase(phase_data)
            if len(unwrapped) == 0:
                return None

            filtered, _ = self.signal_filter.apply_filter(unwrapped)
            downsampled, _ = self.downsampler.downsample(filtered)

            return ProcessedData(
                timestamp=packet.timestamp,
                downsampled_data=downsampled,
                effective_rate=1000000.0 / self.downsampler.get_current_factor()
            )

        except Exception as e:
            self.logger.error(f"Process error: {e}")
            return None

    def stop(self):
        """停止线程"""
        self.running = False


class TimePlotter(QThread):
    """时域绘图线程"""
    plot_ready = pyqtSignal(np.ndarray, np.ndarray)  # times, values

    def __init__(self):
        super().__init__()
        self.input_queue = Queue(maxsize=5)
        self.running = False
        self.data_buffer = []
        self.time_buffer = []

    def add_data(self, processed_data: ProcessedData):
        """添加数据"""
        try:
            if not self.input_queue.full():
                self.input_queue.put(processed_data, block=False)
        except:
            pass

    def run(self):
        """绘图循环"""
        self.running = True
        while self.running:
            try:
                data = self.input_queue.get(timeout=0.2)
                self._prepare_plot_data(data)
            except Empty:
                continue

    def _prepare_plot_data(self, data: ProcessedData):
        """准备绘图数据"""
        try:
            # 时域显示降采样：200kHz -> 100kHz
            display_data = data.downsampled_data[::2]

            # 计算时间戳
            dt = 1.0 / 100000.0  # 100kHz显示
            timestamps = data.timestamp + np.arange(len(display_data)) * dt

            # 更新缓冲区
            self.data_buffer.extend(display_data)
            self.time_buffer.extend(timestamps)

            # 保持1秒窗口
            if len(self.time_buffer) > 0:
                cutoff = self.time_buffer[-1] - 1.0
                keep_indices = [i for i, t in enumerate(self.time_buffer) if t >= cutoff]

                if keep_indices:
                    start_idx = keep_indices[0]
                    self.time_buffer = self.time_buffer[start_idx:]
                    self.data_buffer = self.data_buffer[start_idx:]

                    # 发射绘图信号
                    times = np.array(self.time_buffer) - self.time_buffer[0]
                    values = np.array(self.data_buffer)
                    self.plot_ready.emit(times, values)

        except Exception as e:
            logging.error(f"Time plot error: {e}")

    def stop(self):
        """停止线程"""
        self.running = False


class PSDPlotter(QThread):
    """PSD绘图线程"""
    plot_ready = pyqtSignal(np.ndarray, np.ndarray)  # frequencies, psd_db

    def __init__(self, psd_calculator):
        super().__init__()
        self.input_queue = Queue(maxsize=3)
        self.running = False
        self.psd_calculator = psd_calculator

    def add_data(self, processed_data: ProcessedData):
        """添加数据"""
        try:
            if not self.input_queue.full():
                self.input_queue.put(processed_data, block=False)
        except:
            pass

    def run(self):
        """PSD计算循环"""
        self.running = True
        while self.running:
            try:
                data = self.input_queue.get(timeout=0.5)
                self._calculate_psd(data)
            except Empty:
                continue

    def _calculate_psd(self, data: ProcessedData):
        """计算PSD"""
        try:
            # 使用200kHz数据计算PSD
            frequencies, psd = self.psd_calculator.compute_psd(data.downsampled_data)

            if len(frequencies) > 0:
                # 转换为dB
                psd_safe = np.maximum(psd, 1e-15)
                psd_db = np.clip(10 * np.log10(psd_safe), -200, 100)

                self.plot_ready.emit(frequencies, psd_db)

        except Exception as e:
            logging.error(f"PSD calculation error: {e}")

    def stop(self):
        """停止线程"""
        self.running = False


class Tab1ThreadManager:
    """Tab1线程管理器"""

    def __init__(self, processors, psd_calculator):
        """
        初始化Tab1多线程系统

        Args:
            processors: (phase_unwrapper, signal_filter, downsampler)
            psd_calculator: PSD计算器
        """
        self.logger = logging.getLogger('Tab1ThreadManager')

        # 创建线程
        phase_unwrapper, signal_filter, downsampler = processors
        self.data_processor = DataProcessor(phase_unwrapper, signal_filter, downsampler)
        self.time_plotter = TimePlotter()
        self.psd_plotter = PSDPlotter(psd_calculator)

        # 绘图控件
        self.time_curve = None
        self.psd_curve = None

        # 连接信号
        self._setup_connections()

    def _setup_connections(self):
        """设置信号连接"""
        # 数据处理 -> 绘图线程
        self.data_processor.data_ready.connect(self._distribute_data)

        # 绘图线程 -> UI更新
        self.time_plotter.plot_ready.connect(self._update_time_plot)
        self.psd_plotter.plot_ready.connect(self._update_psd_plot)

    def set_plot_widgets(self, time_plot, psd_plot):
        """设置绘图控件"""
        # 获取或创建曲线
        time_curves = time_plot.listDataItems()
        self.time_curve = time_curves[0] if time_curves else time_plot.plot(pen='b')

        psd_curves = psd_plot.listDataItems()
        self.psd_curve = psd_curves[0] if psd_curves else psd_plot.plot(pen='r')

    def start(self):
        """启动所有线程"""
        self.data_processor.start()
        self.time_plotter.start()
        self.psd_plotter.start()
        self.logger.info("Tab1 threads started")

    def stop(self):
        """停止所有线程"""
        threads = [self.data_processor, self.time_plotter, self.psd_plotter]

        for thread in threads:
            thread.stop()

        for thread in threads:
            if thread.isRunning():
                thread.wait(2000)

        self.logger.info("Tab1 threads stopped")

    def process_packet(self, packet):
        """处理数据包"""
        return self.data_processor.add_packet(packet)

    def _distribute_data(self, processed_data: ProcessedData):
        """分发处理后的数据"""
        self.time_plotter.add_data(processed_data)

        # PSD更新频率较低
        if int(processed_data.timestamp * 5) % 5 == 0:  # 每秒更新一次PSD
            self.psd_plotter.add_data(processed_data)

    def _update_time_plot(self, times, values):
        """更新时域绘图"""
        if self.time_curve:
            # 使用QTimer确保在主线程更新
            QTimer.singleShot(0, lambda: self.time_curve.setData(times, values))

    def _update_psd_plot(self, frequencies, psd_db):
        """更新PSD绘图"""
        if self.psd_curve:
            # 使用QTimer确保在主线程更新
            QTimer.singleShot(0, lambda: self.psd_curve.setData(frequencies, psd_db))