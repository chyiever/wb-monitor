"""
Tab1 Waveform Plotter Module for Real-time Data Visualization

This module provides PyQtGraph-based plotting capabilities for Tab1 of the PCCP monitoring system.
Focused solely on time-domain signals and PSD frequency analysis.

Features:
- Real-time time-domain signal plotting (fixed 0.2s update interval)
- Power spectral density (PSD) analysis and visualization (auto-update interval)
- Interactive zoom, pan, and reset functionality
- Thread-safe data updates with circular buffers
- Optimized for 1MHz sampling rate with 200K display points

Author: Claude
Date: 2026-03-11
Updated: 2026-03-12 - Modularized for Tab1 only, removed feature processing
"""

import time
import logging
import numpy as np
from typing import Dict, Optional, Tuple, Any
from collections import deque
import threading

import pyqtgraph as pg
from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from scipy import signal


class PlotDataBuffer:
    """
    Thread-safe circular buffer for plot data storage.

    This class manages time-series data with automatic time stamping
    and provides efficient data retrieval for plotting operations.
    """

    def __init__(self, max_points: int = 100000):
        """
        Initialize the plot data buffer.

        Args:
            max_points: Maximum number of data points to store
        """
        self.max_points = max_points
        self._lock = threading.RLock()

        # Data storage
        self._timestamps = deque(maxlen=max_points)
        self._values = deque(maxlen=max_points)

        # Statistics
        # 时间戳已由通信序号计算得到（相对时间，秒），固定为 0 基准
        self._start_time = 0.0
        self._last_update = 0

    def append(self, value: float, timestamp: Optional[float] = None) -> None:
        """
        Append a new data point to the buffer.

        Args:
            value: Data value to append
            timestamp: Optional timestamp (uses current time if None)
        """
        if timestamp is None:
            timestamp = time.time()

        with self._lock:
            self._timestamps.append(timestamp)
            self._values.append(value)
            self._last_update = timestamp

    def append_array(self, values: np.ndarray, start_time: Optional[float] = None,
                    sample_rate: float = 1000000.0) -> None:
        """
        Append an array of values with calculated timestamps.

        Args:
            values: Array of data values
            start_time: Start timestamp for the array (seconds since session start,
                        derived from comm_count * COMM_INTERVAL; no wall-clock jitter)
            sample_rate: Sampling rate for timestamp calculation
        """
        if start_time is None:
            start_time = 0.0

        with self._lock:
            dt = 1.0 / sample_rate
            # 始终从 _last_update（实际累积样本末尾）开始延伸，保证时间轴严格连续。
            # start_time 仅用于首批数据（_last_update == 0）的初始锚定。
            # 断连重启时 clear() 会把 _last_update 重置为 0，下一批自动从 0 重新锚定。
            effective_start = self._last_update if self._last_update > 0 else start_time

            timestamps = effective_start + np.arange(len(values)) * dt

            self._timestamps.extend(timestamps)
            self._values.extend(values)

            self._last_update = effective_start + len(values) * dt

    def get_data(self, max_points: Optional[int] = None) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get time and value arrays for plotting.

        Args:
            max_points: Maximum number of points to return (None for all)

        Returns:
            Tuple of (timestamps, values) as numpy arrays
        """
        with self._lock:
            if not self._timestamps:
                return np.array([]), np.array([])

            if max_points is None or max_points >= len(self._timestamps):
                timestamps = np.array(self._timestamps)
                values = np.array(self._values)
            else:
                # Get the most recent points
                timestamps = np.array(list(self._timestamps)[-max_points:])
                values = np.array(list(self._values)[-max_points:])

            # Convert to relative time (seconds from start)
            if len(timestamps) > 0:
                timestamps = timestamps - self._start_time

            return timestamps, values

    def get_latest_window(self, window_duration: float) -> Tuple[np.ndarray, np.ndarray]:
        """
        Get data from the latest time window.

        Args:
            window_duration: Duration of the window in seconds

        Returns:
            Tuple of (timestamps, values) for the window
        """
        with self._lock:
            if not self._timestamps or self._last_update == 0:
                return np.array([]), np.array([])

            cutoff_time = self._last_update - window_duration

            timestamps = []
            values = []

            for t, v in zip(self._timestamps, self._values):
                if t >= cutoff_time:
                    timestamps.append(t - self._start_time)
                    values.append(v)

            return np.array(timestamps), np.array(values)

    def clear(self) -> None:
        """Clear all data from the buffer."""
        with self._lock:
            self._timestamps.clear()
            self._values.clear()
            self._start_time = 0.0
            self._last_update = 0

    def size(self) -> int:
        """Get the current number of data points."""
        with self._lock:
            return len(self._timestamps)


class ArraySegmentBuffer:
    """Thread-safe segment buffer optimized for retaining recent signal windows."""

    def __init__(self, max_duration: float, sample_rate: float = 1000000.0):
        self.sample_rate = sample_rate
        self.max_duration = max_duration
        self._lock = threading.RLock()
        self._segments = deque()
        self._total_samples = 0
        self._last_update = 0.0

    def append_array(self, values: np.ndarray, start_time: Optional[float] = None,
                    sample_rate: Optional[float] = None) -> None:
        """Append a signal segment and prune old data outside the retention window."""
        if start_time is None:
            start_time = time.time()

        if sample_rate is None:
            sample_rate = self.sample_rate

        if len(values) == 0:
            return

        # Optimize: avoid unnecessary data copy for large arrays
        # Use the original array directly if it's already the right dtype
        if values.dtype == np.float64:
            values_array = values
        else:
            values_array = np.asarray(values, dtype=np.float64)

        end_time = start_time + len(values) / sample_rate
        segment = (start_time, values_array, sample_rate, end_time)

        with self._lock:
            self._segments.append(segment)
            self._total_samples += len(values)
            self._last_update = end_time

            # Optimize: only prune when buffer becomes large
            # This reduces the frequency of expensive prune operations
            if len(self._segments) > 10:  # Only prune when we have many segments
                self._prune_locked()

    def get_latest_window(self, window_duration: float) -> Tuple[np.ndarray, np.ndarray]:
        """Get timestamps and values from the latest time window."""
        with self._lock:
            if not self._segments or self._last_update == 0:
                return np.array([]), np.array([])

            cutoff_time = self._last_update - window_duration
            timestamp_parts = []
            value_parts = []

            for start_time, values, sample_rate, end_time in self._segments:
                if end_time <= cutoff_time:
                    continue

                if start_time < cutoff_time:
                    start_index = int(np.ceil((cutoff_time - start_time) * sample_rate))
                else:
                    start_index = 0

                if start_index >= len(values):
                    continue

                sliced_values = values[start_index:]
                sliced_start_time = start_time + start_index / sample_rate
                sliced_timestamps = sliced_start_time + np.arange(len(sliced_values)) / sample_rate

                timestamp_parts.append(sliced_timestamps)
                value_parts.append(sliced_values)

            if not timestamp_parts:
                return np.array([]), np.array([])

            return np.concatenate(timestamp_parts), np.concatenate(value_parts)

    def set_max_duration(self, max_duration: float) -> None:
        """Update the retention duration and prune old segments if needed."""
        with self._lock:
            self.max_duration = max_duration
            self._prune_locked()

    def clear(self) -> None:
        """Clear all buffered segments."""
        with self._lock:
            self._segments.clear()
            self._total_samples = 0
            self._last_update = 0.0

    def size(self) -> int:
        """Get the current number of samples across retained segments."""
        with self._lock:
            return self._total_samples

    def _prune_locked(self) -> None:
        """Prune segments that fall completely outside the retention window."""
        if not self._segments or self._last_update == 0:
            return

        cutoff_time = self._last_update - self.max_duration
        pruned_count = 0

        while self._segments and self._segments[0][3] <= cutoff_time:
            _, values, _, _ = self._segments.popleft()
            self._total_samples -= len(values)
            pruned_count += 1

        # 更合理的修剪策略，进一步限制内存使用
        max_segments = 10  # 减少段数限制 (约2秒数据at 5Hz)
        max_total_samples = 500000  # 大幅减少总样本数为50万（约5秒at 100kHz）

        # 如果段数太多或总样本数太多，适度修剪
        while (len(self._segments) > max_segments or self._total_samples > max_total_samples):
            if not self._segments:
                break
            _, values, _, _ = self._segments.popleft()
            self._total_samples -= len(values)
            pruned_count += 1

        # Log if significant pruning occurred (performance monitoring)
        if pruned_count > 5:
            import logging
            logger = logging.getLogger(__name__)
            logger.warning(f"PSD buffer pruned {pruned_count} segments for memory management, remaining: {len(self._segments)}, "
                         f"total samples: {self._total_samples}")


class PSDCalculator:
    """
    Power Spectral Density calculator using Welch's method.

    This class provides efficient PSD calculation for real-time frequency analysis
    with configurable windowing and overlap parameters.

    The PSD calculation uses phase-unwrapped, unfiltered waveform data to preserve
    all frequency components present in the original signal.
    """

    def __init__(self, sample_rate: float = 1000000.0, nperseg: int = None,
                 overlap: float = 0.5, window: str = 'hann'):
        """
        Initialize the PSD calculator.

        Args:
            sample_rate: Sampling rate in Hz
            nperseg: Length of each segment (None for auto)
            overlap: Overlap ratio between segments (0.0 to 0.9)
            window: Window function name
        """
        self.sample_rate = sample_rate
        self.nperseg = nperseg
        self.overlap = overlap
        self.window = window

        self.logger = logging.getLogger(__name__ + '.PSDCalculator')
        # Remove DEBUG level logging to improve performance
        # self.logger.setLevel(logging.DEBUG)

    def compute_psd(self, data: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
        """
        Calculate power spectral density using Welch's method.

        Args:
            data: Input signal data

        Returns:
            Tuple of (frequencies, power_density) in Hz and linear scale
        """
        try:
            if len(data) < 256:  # Minimum data length for meaningful PSD
                return np.array([]), np.array([])

            # Data preprocessing for phase signals
            # Remove DC component (mean) which is common in phase data
            data_processed = data - np.mean(data)

            # Log data statistics periodically (only occasionally)
            if len(data_processed) > 0:
                data_std = np.std(data_processed)
                data_range = np.max(data_processed) - np.min(data_processed)
                # Only log every 10th PSD calculation to reduce overhead
                if hasattr(self, '_psd_call_count'):
                    self._psd_call_count += 1
                else:
                    self._psd_call_count = 0

                if self._psd_call_count % 10 == 0:
                    self.logger.info(f"PSD input: len={len(data_processed)}, std={data_std:.6f}, range={data_range:.6f}")

            # Auto-determine nperseg if not specified with performance limits
            nperseg = self.nperseg
            if nperseg is None:
                # 限制nperseg大小避免计算过度
                nperseg = min(len(data_processed) // 8, int(self.sample_rate), 50000)  # 最大5万样本

            # Ensure nperseg is reasonable with stricter limits
            nperseg = max(256, min(nperseg, len(data_processed) // 2, 50000))  # 强制上限5万样本

            # Calculate noverlap
            noverlap = int(nperseg * self.overlap)

            # Ensure noverlap is less than nperseg
            noverlap = min(noverlap, nperseg - 1)

            # Only log parameters occasionally
            if hasattr(self, '_psd_call_count') and self._psd_call_count % 10 == 0:
                self.logger.info(f"PSD params: nperseg={nperseg}, noverlap={noverlap}")

            # Calculate PSD with detrend to remove linear trends
            frequencies, psd = signal.welch(
                data_processed,
                fs=self.sample_rate,
                window=self.window,
                nperseg=nperseg,
                noverlap=noverlap,
                return_onesided=True,
                scaling='density',
                detrend='linear'  # Remove linear trends
            )

            # Log PSD statistics periodically (occasionally)
            if len(psd) > 0 and hasattr(self, '_psd_call_count') and self._psd_call_count % 10 == 0:
                psd_min = np.min(psd[psd > 0]) if np.any(psd > 0) else 0
                psd_max = np.max(psd)
                self.logger.info(f"PSD output: min={psd_min:.2e}, max={psd_max:.2e}")

            return frequencies, psd

        except Exception as e:
            self.logger.error(f"Error calculating PSD: {e}")
            import traceback
            self.logger.error(traceback.format_exc())
            return np.array([]), np.array([])

    def set_window_length(self, nperseg: int):
        """
        Set the window length for PSD calculation.

        Args:
            nperseg: Window length in samples
        """
        self.nperseg = nperseg
        self.logger.info(f"PSD window length set to {nperseg} samples")


class WaveformPlotter(QObject):
    """
    Tab1 waveform plotter class for real-time data visualization.

    This class manages time-domain and PSD plot widgets for Tab1 only.
    Completely separated from feature processing for optimal performance.
    """

    # Signals for plot updates
    plot_updated = pyqtSignal(str)  # Plot name

    def __init__(self, sample_rate: float = 1000000.0):
        """
        Initialize the waveform plotter.

        Args:
            sample_rate: Data sampling rate in Hz
        """
        super().__init__()

        self.sample_rate = sample_rate
        self.logger = logging.getLogger(__name__ + '.WaveformPlotter')

        # PSD计算参数 - 优化性能
        self.psd_window_duration = 0.4  # PSD计算窗口
        self.psd_buffer_retention = max(self.psd_window_duration * 1.25, 0.5)

        # Plot data buffers - 独立的缓冲区，针对200kHz采样率优化
        self.time_buffer = PlotDataBuffer(max_points=200000)  # 20万点，支持1.0秒@200kHz
        self.psd_buffer = ArraySegmentBuffer(
            max_duration=self.psd_buffer_retention,
            sample_rate=sample_rate
        )

        # PSD calculator with performance optimizations
        # 限制PSD窗口大小避免过度计算，针对200kHz采样率
        max_psd_samples = min(int(self.psd_window_duration * sample_rate), 80000)  # 最大8万样本@200kHz
        self.psd_calculator = PSDCalculator(
            sample_rate=sample_rate,
            nperseg=max_psd_samples // 4  # 使用1/4的数据作为窗口
        )

        # Plot widgets (to be set by external code) - 只保留Tab1相关
        self.time_plot: Optional[pg.PlotWidget] = None
        self.psd_plot: Optional[pg.PlotWidget] = None

        # Plot curves - 只保留Tab1相关
        self.time_curve: Optional[pg.PlotCurveItem] = None
        self.psd_curve: Optional[pg.PlotCurveItem] = None

        # Display settings - 分离时域和PSD的显示参数
        # 时域显示参数 - 针对200kHz有效数据，100kHz显示
        self.time_display_window = 1.0  # seconds - 时域专用显示窗口，默认1秒（与UI一致）
        self.time_max_display_points = 100000  # 时域专用最大显示点数 - 10万点适应100kHz显示采样率
        self.time_buffer_target_multiplier = 4

        # 时域显示专用降采样因子（在200kHz基础上再降采样至100kHz显示）
        self.time_display_downsample = 2  # 200kHz -> 100kHz

        self.logger.info(f"WaveformPlotter initialized for {sample_rate/1000:.0f}kHz sample rate")
        self.logger.info(f"Time display: {sample_rate/1000:.0f}kHz -> {self.time_display_downsample}x -> {sample_rate/self.time_display_downsample/1000:.0f}kHz")
        self.logger.info(f"Time display window: {self.time_display_window}s, PSD window: {self.psd_window_duration}s")

        # 保留向后兼容
        self.time_window = self.time_display_window  # 向后兼容
        self.max_display_points = self.time_max_display_points  # 向后兼容

        # 数据驱动更新机制 - 不再使用计时器
        # 时域图：每收到一个数据包（0.2s）就刷新一次
        # PSD图：每收到足够的数据包就刷新一次

        # 数据包计数器和更新控制
        self.time_packet_count = 0  # 已收到的数据包数量
        self.psd_packet_count = 0   # PSD用的数据包计数
        self.packet_duration = 0.2  # 每个数据包的时长（秒）

        # 时域更新：每10个数据包更新一次（即每2秒更新一次），进一步减少负载
        self.time_update_packet_interval = 10  # 每10个数据包更新一次时域图（10 * 0.2s = 2s）

        # PSD更新：根据PSD窗口长度计算需要多少个数据包，但至少4个包
        self.psd_update_packet_interval = max(4, int(self.psd_window_duration / self.packet_duration))  # 至少4个包

        # 绘图启用标志
        self.time_plot_enabled = True
        self.psd_plot_enabled = True

    def set_plot_widgets(self, time_plot: pg.PlotWidget, psd_plot: pg.PlotWidget):
        """
        Set the plot widget references for Tab1 only.

        Args:
            time_plot: Time domain plot widget
            psd_plot: PSD frequency plot widget
        """
        self.time_plot = time_plot
        self.psd_plot = psd_plot

        # Get curve references or create new ones
        if self.time_plot:
            curves = self.time_plot.listDataItems()
            if curves:
                self.time_curve = curves[0]
            else:
                # Create new curve for time domain
                self.time_curve = self.time_plot.plot(pen=pg.mkPen('b', width=2), name='时域信号')

        if self.psd_plot:
            curves = self.psd_plot.listDataItems()
            if curves:
                self.psd_curve = curves[0]
            else:
                # Create new curve for PSD
                self.psd_curve = self.psd_plot.plot(pen=pg.mkPen('r', width=2), name='功率谱密度')

    def start_plotting(self):
        """Enable data-driven plotting (no timers needed)."""
        self.time_plot_enabled = True
        self.psd_plot_enabled = True

        # 重置计数器
        self.time_packet_count = 0
        self.psd_packet_count = 0

        self.logger.info(f"Started data-driven plotting: time updates every {self.time_update_packet_interval} packet(s) "
                        f"({self.time_update_packet_interval * self.packet_duration}s), PSD updates every {self.psd_update_packet_interval} packet(s) "
                        f"({self.psd_window_duration}s)")

    def stop_plotting(self):
        """Disable data-driven plotting."""
        self.time_plot_enabled = False
        self.psd_plot_enabled = False

        self.logger.info("Stopped data-driven plotting")

    def add_time_domain_data(self, data: np.ndarray, timestamp: Optional[float] = None):
        """
        Add time domain data for plotting with data-driven update.
        数据输入为200kHz采样率，显示时再降采样至100kHz。

        Args:
            data: Signal data array (200kHz采样率)
            timestamp: Start timestamp (uses current time if None)
        """
        try:
            if len(data) == 0:
                return

            # 时域显示专用降采样：200kHz -> 100kHz（2倍降采样）
            # 使用简单抽取法，每2个点取1个
            downsampled_data = data[::self.time_display_downsample]

            # 计算显示用的有效采样率
            display_sample_rate = self.sample_rate / self.time_display_downsample

            # 添加到时域缓冲区（使用降采样后的数据和显示采样率）
            self.time_buffer.append_array(downsampled_data, timestamp, display_sample_rate)

            # 数据包计数器增加
            self.time_packet_count += 1

            # 检查是否需要更新时域图（数据驱动）
            if (self.time_plot_enabled and
                self.time_packet_count % self.time_update_packet_interval == 0):
                # 减少日志输出频率，只在每10次更新时输出
                if self.time_packet_count % 50 == 0:  # 每50个包输出一次
                    buffer_size = self.time_buffer.size()
                    self.logger.info(f"Updating time domain plot: packet #{self.time_packet_count}, "
                                   f"buffer size: {buffer_size} points, "
                                   f"display window: {self.time_display_window}s, "
                                   f"input: {len(data)}@{self.sample_rate/1000:.0f}kHz -> display: {len(downsampled_data)}@{display_sample_rate/1000:.0f}kHz")

                self._update_time_plot()
                self.plot_updated.emit("time_domain")

        except Exception as e:
            self.logger.error(f"Error adding time domain data: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

    def _get_time_buffer_downsample_factor(self) -> int:
        """Calculate a display-only downsample factor for the time-domain buffer."""
        target_points = max(
            self.time_max_display_points * self.time_buffer_target_multiplier,
            self.time_max_display_points
        )
        target_sample_rate = target_points / max(self.time_display_window, 0.1)

        if target_sample_rate >= self.sample_rate:
            return 1

        return max(1, int(np.ceil(self.sample_rate / target_sample_rate)))

    def add_psd_data(self, data: np.ndarray, timestamp: Optional[float] = None):
        """
        Add data for PSD calculation with data-driven update and performance optimization.
        PSD计算使用200kHz采样率的数据，保持更高的频率分辨率。

        Args:
            data: 200kHz采样率的信号数据，用于PSD计算
            timestamp: Start timestamp (uses current time if None)
        """
        try:
            # 检查PSD绘图是否启用
            if not self.psd_plot_enabled:
                return

            # 不进行自动下采样，保持原始数据完整性
            # 降采样应该由前面板"降采样倍数"参数控制，不在这里处理
            effective_sample_rate = self.sample_rate

            # 将数据添加到PSD专用缓冲区
            self.psd_buffer.append_array(data, timestamp, effective_sample_rate)

            # PSD数据包计数器增加
            self.psd_packet_count += 1

            # 检查是否需要更新PSD图（数据驱动）
            # 性能保护：在负载高时，降低PSD更新频率
            if hasattr(self, '_last_psd_update_time'):
                time_since_last_psd = time.time() - self._last_psd_update_time
                min_psd_interval = 1.0  # 最少1秒间隔
                if time_since_last_psd < min_psd_interval:
                    return  # 跳过这次PSD更新

            if self.psd_packet_count % self.psd_update_packet_interval == 0:
                self._update_psd_plot()
                self._last_psd_update_time = time.time()
                self.plot_updated.emit("psd")

        except Exception as e:
            self.logger.error(f"Error adding PSD data: {e}")
            # Remove detailed traceback to reduce log overhead

    def set_time_display_downsample(self, factor: int):
        """
        设置时域显示专用的降采样因子

        Args:
            factor: 时域显示降采样因子（在有效采样率基础上）
        """
        try:
            old_factor = self.time_display_downsample
            self.time_display_downsample = max(1, int(factor))

            if old_factor != self.time_display_downsample:
                # 清空时域缓冲区
                self.time_buffer.clear()
                self.logger.info(f"Time display downsample changed: {old_factor}x -> {self.time_display_downsample}x")

                # 更新显示采样率
                display_sample_rate = self.sample_rate / self.time_display_downsample
                self.logger.info(f"Time display sample rate: {display_sample_rate/1000:.0f}kHz")

        except Exception as e:
            self.logger.error(f"Error setting time display downsample: {e}")

    def set_display_settings(self, time_window: float = None, max_points: int = None):
        """
        Set display settings for time domain plotting.

        Args:
            time_window: Time window duration in seconds (for time domain)
            max_points: Maximum points to display (for time domain)
        """
        if time_window is not None:
            self.time_display_window = time_window
            self.time_window = time_window  # 向后兼容

        if max_points is not None:
            self.time_max_display_points = max_points
            self.max_display_points = max_points  # 向后兼容

    def set_psd_enabled(self, enabled: bool):
        """
        Enable or disable PSD plotting (data-driven).

        Args:
            enabled: True to enable PSD plotting, False to disable
        """
        self.psd_plot_enabled = enabled
        if enabled:
            # 重置计数器
            self.psd_packet_count = 0
            self.logger.info("PSD plotting enabled (data-driven)")
        else:
            # 清空PSD缓冲区和图表
            if hasattr(self, 'psd_buffer'):
                self.psd_buffer.clear()
            if hasattr(self, 'psd_curve') and self.psd_curve:
                self.psd_curve.setData([], [])
            self.logger.info("PSD plotting disabled")

    def clear_all_data(self):
        """Clear all data buffers and plots for Tab1 only."""
        try:
            self.time_buffer.clear()
            self.psd_buffer.clear()

            # 重置数据包计数器
            self.time_packet_count = 0
            self.psd_packet_count = 0

            # Clear plot curves
            if self.time_curve:
                self.time_curve.setData([], [])

            if self.psd_curve:
                self.psd_curve.setData([], [])

            self.logger.info("Cleared all plot data for Tab1")

        except Exception as e:
            self.logger.error(f"Error clearing plot data: {e}")

    def update_psd_parameters(self, settings: Dict[str, Any]):
        """
        Update PSD calculation parameters and automatically adjust data-driven update interval.

        Args:
            settings: Dictionary with PSD settings
                - window_length: Window length for PSD calculation (in samples)
                - window_duration: PSD data window duration in seconds
        """
        try:
            # Update PSD calculator parameters
            if 'window_length' in settings:
                window_length = int(settings['window_length'])
                if hasattr(self.psd_calculator, 'set_window_length'):
                    self.psd_calculator.set_window_length(window_length)
                    self.logger.info(f"PSD window length updated to {window_length} samples")

            # Update PSD data window duration
            if 'window_duration' in settings:
                old_duration = self.psd_window_duration
                self.psd_window_duration = float(settings['window_duration'])
                self.psd_buffer_retention = max(self.psd_window_duration * 1.25, 0.5)
                self.psd_buffer.set_max_duration(self.psd_buffer_retention)

                # 重要：自动调整PSD数据驱动更新间隔 = 需要多少个数据包
                old_packet_interval = self.psd_update_packet_interval
                self.psd_update_packet_interval = max(1, int(self.psd_window_duration / self.packet_duration))

                # 重置PSD计数器以应用新的更新间隔
                self.psd_packet_count = 0

                self.logger.info(
                    f"PSD window duration updated: {old_duration:.1f}s -> {self.psd_window_duration:.1f}s"
                )
                self.logger.info(
                    f"PSD update interval auto-adjusted: every {old_packet_interval} packets -> every {self.psd_update_packet_interval} packets "
                    f"({self.psd_update_packet_interval * self.packet_duration:.1f}s)"
                )

        except Exception as e:
            self.logger.error(f"Error updating PSD parameters: {e}")

    def update_time_display_parameters(self, settings: Dict[str, Any]):
        """
        Update time domain display parameters.
        Note: Update is now data-driven (every 5 packets = 1s), not timer-driven.

        Args:
            settings: Dictionary with time display settings
                - duration: Display duration in seconds
                Note: 'update_interval' is NO LONGER supported - updates are data-driven (every 1s)
        """
        try:
            if 'duration' in settings:
                # 更新时域专用显示窗口
                old_window = self.time_display_window
                self.time_display_window = float(settings['duration'])
                self.time_window = self.time_display_window  # 向后兼容
                self.logger.info(f"Time display duration updated: {old_window}s → {self.time_display_window}s")

            # 重要：时域更新现在是数据驱动的，每5个数据包（1s）触发一次
            if 'update_interval' in settings:
                self.logger.warning("Time domain updates are now DATA-DRIVEN (every 5 packets = 1s), not timer-driven. "
                                  "Ignoring update_interval parameter.")

        except Exception as e:
            self.logger.error(f"Error updating time display parameters: {e}")

    def _update_time_plot(self):
        """Update the time domain plot with independent parameters."""
        try:
            if not self.time_plot or not self.time_curve:
                return

            if self.time_buffer.size() == 0:
                return

            # 首先尝试获取请求的时间窗口数据
            timestamps, values = self.time_buffer.get_latest_window(self.time_display_window)

            # 如果获取的数据不够，尝试获取所有可用数据
            if len(timestamps) == 0 or (len(timestamps) > 0 and (timestamps[-1] - timestamps[0]) < self.time_display_window * 0.8):
                # 数据不够，获取所有可用数据
                all_timestamps, all_values = self.time_buffer.get_data()
                if len(all_timestamps) > 0:
                    timestamps, values = all_timestamps, all_values
                    self.logger.info(f"Time plot: insufficient data for {self.time_display_window}s window, "
                                   f"showing all available {len(timestamps)} points "
                                   f"(duration: {all_timestamps[-1] - all_timestamps[0]:.3f}s)")

            if len(timestamps) == 0:
                return

            # 添加详细的调试信息
            total_duration = timestamps[-1] - timestamps[0] if len(timestamps) > 1 else 0
            self.logger.info(f"Time plot update: got {len(timestamps)} points, "
                           f"duration: {total_duration:.3f}s, "
                           f"requested window: {self.time_display_window}s, "
                           f"time range: {timestamps[0]:.3f}s to {timestamps[-1]:.3f}s")

            # 使用时域专用的最大显示点数
            if len(timestamps) > self.time_max_display_points:
                step = len(timestamps) // self.time_max_display_points
                timestamps = timestamps[::step]
                values = values[::step]
                self.logger.debug(f"Time plot downsampled: step={step}, "
                               f"final points: {len(timestamps)}")

            # Update curve
            self.time_curve.setData(timestamps, values)

        except Exception as e:
            self.logger.error(f"Error updating time plot: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

    def _update_psd_plot(self):
        """Update the PSD frequency plot using data from PSD buffer with performance optimization."""
        try:
            if not self.psd_curve or self.psd_buffer.size() < 1024:
                return

            # Get recent data for PSD calculation from PSD buffer
            _, values = self.psd_buffer.get_latest_window(self.psd_window_duration)

            if len(values) < 256:
                return

            # 不进行自动下采样，保持PSD计算的准确性
            # 如果性能有问题，应该通过前面板"降采样倍数"参数来控制

            # Calculate PSD
            frequencies, psd = self.psd_calculator.compute_psd(values)

            if len(frequencies) == 0:
                return

            # Convert to dB scale with proper handling of small values
            # Add a small floor value to avoid -inf in log calculation
            psd_floor = 1e-15  # Very small value to set lower bound
            psd_safe = np.maximum(psd, psd_floor)

            # Convert to dB: 10*log10(PSD) for power spectral density
            psd_db = 10 * np.log10(psd_safe)

            # Optional: Limit the dynamic range to reasonable values for visualization
            # Clip extreme negative values that might indicate numerical issues
            psd_db_clipped = np.clip(psd_db, -200, 100)  # Limit range from -200dB to +100dB

            # Log some statistics periodically (reduce frequency)
            if len(psd_db_clipped) > 0:
                # Only log every 10th PSD update to reduce overhead
                if not hasattr(self, '_psd_plot_count'):
                    self._psd_plot_count = 0
                self._psd_plot_count += 1

                if self._psd_plot_count % 10 == 0:
                    self.logger.info(f"PSD dB: min={np.min(psd_db_clipped):.1f}, max={np.max(psd_db_clipped):.1f}, median={np.median(psd_db_clipped):.1f}")

            # Update curve
            self.psd_curve.setData(frequencies, psd_db_clipped)

        except Exception as e:
            self.logger.error(f"Error updating PSD plot: {e}")
            import traceback
            self.logger.error(traceback.format_exc())

    def get_plot_statistics(self) -> Dict[str, Any]:
        """
        Get plotting statistics for Tab1 only.

        Returns:
            Dictionary with Tab1 plot statistics
        """
        stats = {
            # 缓冲区大小
            'time_buffer_size': self.time_buffer.size(),
            'psd_buffer_size': self.psd_buffer.size(),

            # 数据驱动更新机制 - 新的逻辑
            'time_packet_count': self.time_packet_count,
            'psd_packet_count': self.psd_packet_count,
            'time_update_packet_interval': self.time_update_packet_interval,
            'psd_update_packet_interval': self.psd_update_packet_interval,
            'packet_duration': self.packet_duration,
            'time_plot_enabled': self.time_plot_enabled,
            'psd_plot_enabled': self.psd_plot_enabled,

            # 显示参数
            'time_display_window': self.time_display_window,
            'time_max_display_points': self.time_max_display_points,
            'psd_window_duration': self.psd_window_duration,

            # 向后兼容
            'time_window': self.time_window
        }

        return stats


__all__ = [
    "ArraySegmentBuffer",
    "PlotDataBuffer",
    "PSDCalculator",
    "WaveformPlotter",
]
