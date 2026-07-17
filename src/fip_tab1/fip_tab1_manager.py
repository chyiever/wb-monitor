"""
优化的Tab1线程架构 - 完全分离的多线程系统

核心设计原则：
1. 主线程：仅负责UI控制和TCP数据接收分发
2. 数据处理线程：相位展开、滤波、降采样
3. 时域绘图线程：独立的时域数据处理和绘制
4. PSD绘图线程：独立的PSD计算和绘制
5. 存储线程：独立的数据存储操作

优化目标：
- 主线程响应性最大化
- 各功能模块完全解耦
- 高效的线程间通信
- 最小化代码复杂度

Author: Claude
Date: 2026-03-12
"""

import time
import logging
import math
import numpy as np
from collections import deque
from queue import Queue, Empty, Full
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, Tuple
from datetime import datetime, timedelta
from PyQt5.QtCore import QThread, pyqtSignal, QObject, QTimer
from PyQt5.QtWidgets import QApplication
import pyqtgraph as pg

from config import ORIGINAL_SAMPLE_RATE


DEFAULT_FIP_PACKET_DURATION_SECONDS = 1.0
DEFAULT_FIP_SAMPLE_RATE_HZ = ORIGINAL_SAMPLE_RATE
MAX_FIP_SENSOR_COUNT = 2


def normalize_fip_sensor_count(sensor_count: int) -> int:
    """Clamp the user-facing FIP sensor count to the supported range."""
    try:
        count = int(sensor_count)
    except (TypeError, ValueError):
        count = 1
    return min(max(count, 1), MAX_FIP_SENSOR_COUNT)


def normalize_fip_sensor_index(sensor_index: int, sensor_count: int) -> int:
    """Clamp a selected FIP sensor index to the active sensor count."""
    count = normalize_fip_sensor_count(sensor_count)
    try:
        index = int(sensor_index)
    except (TypeError, ValueError):
        index = 1
    return min(max(index, 1), count)


def normalize_fip_packet_duration(packet_duration_seconds: float) -> float:
    """Clamp FIP packet duration to a positive runtime value."""
    try:
        duration = float(packet_duration_seconds)
    except (TypeError, ValueError):
        duration = DEFAULT_FIP_PACKET_DURATION_SECONDS
    return max(duration, 1e-6)


def normalize_fip_sample_rate(sample_rate_hz: float) -> float:
    """Clamp FIP raw sample rate to a positive runtime value."""
    try:
        sample_rate = float(sample_rate_hz)
    except (TypeError, ValueError):
        sample_rate = DEFAULT_FIP_SAMPLE_RATE_HZ
    return max(sample_rate, 1.0)


def fip_points_per_sensor(
    packet_duration_seconds: float,
    sample_rate_hz: float = DEFAULT_FIP_SAMPLE_RATE_HZ,
) -> int:
    """Expected raw points per FIP sensor for one TCP packet."""
    duration = normalize_fip_packet_duration(packet_duration_seconds)
    sample_rate = normalize_fip_sample_rate(sample_rate_hz)
    return max(1, int(round(sample_rate * duration)))


def split_fip_sensor_data(
    phase_data: np.ndarray,
    sensor_count: int,
    packet_duration_seconds: float = DEFAULT_FIP_PACKET_DURATION_SECONDS,
    sample_rate_hz: float = DEFAULT_FIP_SAMPLE_RATE_HZ,
    logger: Optional[logging.Logger] = None,
    comm_count: Optional[int] = None,
) -> Dict[int, np.ndarray]:
    """Split one TCP payload into per-sensor FIP arrays.

    In one-sensor mode the payload is left untouched for exact compatibility.
    In two-sensor mode the expected split point is derived from
    packet_duration_seconds * sample_rate_hz, with an even-split fallback
    when the received packet length does not match the UI duration/rate.
    """
    data = np.asarray(phase_data)
    count = normalize_fip_sensor_count(sensor_count)
    if count == 1:
        return {1: data}

    points_per_sensor = fip_points_per_sensor(packet_duration_seconds, sample_rate_hz)
    expected_points = points_per_sensor * count
    if data.size < expected_points:
        if data.size >= count and data.size % count == 0:
            fallback_points = data.size // count
            if logger is not None:
                logger.warning(
                    "FIP packet #%s expected %d points for %d sensors at %.6fs, got %d; "
                    "sample_rate=%.1fHz; splitting evenly at %d point(s) per sensor.",
                    "-" if comm_count is None else comm_count,
                    expected_points,
                    count,
                    normalize_fip_packet_duration(packet_duration_seconds),
                    data.size,
                    normalize_fip_sample_rate(sample_rate_hz),
                    fallback_points,
                )
            return {
                1: data[:fallback_points],
                2: data[fallback_points:fallback_points * count],
            }
        if logger is not None:
            logger.warning(
                "FIP packet #%s expected %d points for %d sensors at %.6fs, got %d; "
                "sample_rate=%.1fHz; falling back to available FIP1 data only.",
                "-" if comm_count is None else comm_count,
                expected_points,
                count,
                normalize_fip_packet_duration(packet_duration_seconds),
                data.size,
                normalize_fip_sample_rate(sample_rate_hz),
            )
        return {1: data[: min(data.size, points_per_sensor)]}

    if data.size > expected_points and logger is not None:
        logger.warning(
            "FIP packet #%s has %d points for %d sensors at %.6fs, sample_rate=%.1fHz; "
            "ignoring %d trailing point(s).",
            "-" if comm_count is None else comm_count,
            data.size,
            count,
            normalize_fip_packet_duration(packet_duration_seconds),
            normalize_fip_sample_rate(sample_rate_hz),
            data.size - expected_points,
        )

    return {
        1: data[:points_per_sensor],
        2: data[points_per_sensor:expected_points],
    }


def _data_sample_count(data: np.ndarray) -> int:
    arr = np.asarray(data)
    if arr.ndim == 2:
        return int(arr.shape[1])
    return int(arr.size)


def _data_sensor_count(data: np.ndarray) -> int:
    arr = np.asarray(data)
    if arr.ndim == 2:
        return int(arr.shape[0])
    return 1


@dataclass
class RawDataPacket:
    """原始数据包"""
    timestamp: float
    phase_data: np.ndarray
    comm_count: int
    sensor_count: int = 1
    selected_sensor: int = 1
    packet_duration_seconds: float = DEFAULT_FIP_PACKET_DURATION_SECONDS
    sample_rate_hz: float = DEFAULT_FIP_SAMPLE_RATE_HZ


@dataclass
class ProcessedData:
    """处理后的数据包"""
    timestamp: float
    unwrapped_data: np.ndarray  # 相位展开后的数据（用于存储）
    filtered_data: np.ndarray   # 滤波后的数据
    downsampled_data: np.ndarray  # 降采样后的数据（用于绘图）
    psd_data: np.ndarray  # PSD专用数据：相位展开后、未滤波，再按系统降采样抽取
    effective_rate: float
    comm_count: int
    sensor_count: int = 1
    selected_sensor: int = 1
    packet_duration_seconds: float = DEFAULT_FIP_PACKET_DURATION_SECONDS
    raw_sample_rate_hz: float = DEFAULT_FIP_SAMPLE_RATE_HZ
    unwrapped_by_sensor: Dict[int, np.ndarray] = field(default_factory=dict)
    filtered_by_sensor: Dict[int, np.ndarray] = field(default_factory=dict)
    downsampled_by_sensor: Dict[int, np.ndarray] = field(default_factory=dict)
    psd_by_sensor: Dict[int, np.ndarray] = field(default_factory=dict)


@dataclass
class StorageRequest:
    """存储请求"""
    data: np.ndarray
    comm_count: int
    timestamp: float
    sample_rate: float
    data_type: str = "phase_unwrapped"
    sensor_count: int = 1
    selected_sensor: int = 1
    packet_duration_seconds: float = DEFAULT_FIP_PACKET_DURATION_SECONDS
    raw_sample_rate_hz: float = DEFAULT_FIP_SAMPLE_RATE_HZ


class DataProcessingThread(QThread):
    """Data processing worker for plotting-oriented signal processing."""

    data_processed = pyqtSignal(object)  # ProcessedData
    INPUT_QUEUE_MAXSIZE = 100

    def __init__(self, phase_unwrapper, signal_filter, downsampler):
        super().__init__()
        self.input_queue = Queue(maxsize=self.INPUT_QUEUE_MAXSIZE)
        self.running = False

        self.phase_unwrapper = phase_unwrapper
        self.signal_filter = signal_filter
        self.downsampler = downsampler
        self._phase_unwrappers: Dict[int, Any] = {1: phase_unwrapper}
        self._signal_filters: Dict[int, Any] = {1: signal_filter}
        self._downsamplers: Dict[int, Any] = {1: downsampler}

        # 上一包的 comm_count，用于检测缺包缺口（T1-15）
        self._last_comm_count: Optional[int] = None

        self.logger = logging.getLogger(f'{__name__}.DataProcessingThread')
        self.stats = {
            'queue_maxsize': self.INPUT_QUEUE_MAXSIZE,
            'queue_peak': 0,
            'packets_enqueued': 0,
            'packets_processed': 0,
            'queue_drop_count': 0,
            'processing_failure_count': 0,
            'phase_unwrap_failure_count': 0,
            'gap_count': 0,  # 检测到 comm_count 缺口的次数（T1-15）
        }

    def add_raw_packet(self, packet: RawDataPacket) -> bool:
        """Queue raw packets for plotting-oriented processing."""
        try:
            if self.input_queue.full():
                try:
                    discarded = self.input_queue.get_nowait()
                    self.stats['queue_drop_count'] += 1
                    self.logger.warning(
                        'Processing queue full, discarded old packet #%d to admit packet #%d',
                        discarded.comm_count,
                        packet.comm_count,
                    )
                except Empty:
                    pass

            self.input_queue.put(packet, block=False)
            self.stats['packets_enqueued'] += 1
            self.stats['queue_peak'] = max(self.stats['queue_peak'], self.input_queue.qsize())

            if packet.comm_count % 50 == 0:
                self.logger.info('DataProcessingThread received packet #%d', packet.comm_count)

            return True
        except Full:
            self.stats['queue_drop_count'] += 1
            self.logger.warning('Failed to queue packet #%d - queue full', packet.comm_count)
            return False

    def run(self):
        """Main processing loop."""
        self.running = True
        self.logger.info('Data processing thread started')

        while self.running:
            try:
                packet = self.input_queue.get(timeout=0.1)
                processed = self._process_packet(packet)
                if processed is not None:
                    self.stats['packets_processed'] += 1
                    self.data_processed.emit(processed)
            except Empty:
                continue
            except Exception as e:
                self.stats['processing_failure_count'] += 1
                self.logger.error(f'Processing error: {e}')

    def reset_state(self, clear_queue: bool = False):
        """Reset per-sensor streaming state, optionally discarding queued packets."""
        if clear_queue:
            while True:
                try:
                    self.input_queue.get_nowait()
                except Empty:
                    break
        self._last_comm_count = None
        for unwrapper in self._phase_unwrappers.values():
            if hasattr(unwrapper, 'reset'):
                unwrapper.reset()
        for signal_filter in self._signal_filters.values():
            if signal_filter is not None and hasattr(signal_filter, 'reset_filter_state'):
                signal_filter.reset_filter_state()
        for downsampler in self._downsamplers.values():
            if downsampler is not None and hasattr(downsampler, 'reset_state'):
                downsampler.reset_state()

    def _get_sensor_processors(self, sensor_index: int) -> Tuple[Any, Any, Any]:
        """Return independent processor objects for one FIP sensor."""
        if sensor_index not in self._phase_unwrappers:
            self._phase_unwrappers[sensor_index] = type(self.phase_unwrapper)()
        if sensor_index not in self._signal_filters:
            self._signal_filters[sensor_index] = self._clone_signal_filter()
        if sensor_index not in self._downsamplers:
            self._downsamplers[sensor_index] = self._clone_downsampler()

        signal_filter = self._signal_filters[sensor_index]
        downsampler = self._downsamplers[sensor_index]
        self._sync_signal_filter(signal_filter)
        self._sync_downsampler(downsampler)
        return self._phase_unwrappers[sensor_index], signal_filter, downsampler

    def _clone_signal_filter(self):
        if self.signal_filter is None:
            return None
        clone = type(self.signal_filter)(sample_rate=self.signal_filter.sample_rate)
        self._copy_signal_filter_config(clone, self.signal_filter)
        return clone

    def _clone_downsampler(self):
        source = self.downsampler
        return type(source)(method=source.method, factor=source.get_current_factor())

    def _sync_signal_filter(self, signal_filter) -> None:
        if signal_filter is None or signal_filter is self.signal_filter:
            return
        source = self.signal_filter
        if source is None:
            return
        same_config = (
            signal_filter.filter_type == source.filter_type
            and signal_filter.cutoff_freq == source.cutoff_freq
            and signal_filter.filter_order == source.filter_order
            and signal_filter.sample_rate == source.sample_rate
        )
        if not same_config:
            self._copy_signal_filter_config(signal_filter, source)

    def _copy_signal_filter_config(self, target, source) -> None:
        target.sample_rate = source.sample_rate
        if source.filter_type == 'none':
            target.design_filter('none', source.cutoff_freq, source.filter_order)
        else:
            target.design_filter(source.filter_type, source.cutoff_freq, source.filter_order)

    def _sync_downsampler(self, downsampler) -> None:
        if downsampler is None or downsampler is self.downsampler:
            return
        source = self.downsampler
        if downsampler.method != source.method:
            downsampler.set_method(source.method)
        if downsampler.get_current_factor() != source.get_current_factor():
            downsampler.set_downsampling_factor(source.get_current_factor())

    def _process_packet(self, packet: RawDataPacket) -> Optional[ProcessedData]:
        """处理单个原始数据包：缺口检测、相位展开、滤波、降采样。

        Args:
            packet: TCP 服务器传入的原始相位数据包。

        Returns:
            ProcessedData（处理成功）或 None（处理失败）。

        缺口处理（T1-15）：
            检测到 comm_count 不连续时重置相位展开器状态，
            防止缺口两侧数据被错误连续化（$2\\pi$ 偏移累积误差）。
        """
        try:
            if packet.comm_count % 50 == 0:
                self.logger.info(
                    'Processing packet #%d, data shape: %s',
                    packet.comm_count,
                    packet.phase_data.shape,
                )

            # --- 缺口检测（T1-15）---
            if (
                self._last_comm_count is not None
                and packet.comm_count != self._last_comm_count + 1
            ):
                gap = packet.comm_count - self._last_comm_count - 1
                self.logger.warning(
                    'comm_count gap in DataProcessingThread: last=%d, current=%d, missing=%d. '
                    'Resetting per-sensor processor state to prevent cross-gap phase error.',
                    self._last_comm_count,
                    packet.comm_count,
                    gap,
                )
                self.reset_state(clear_queue=False)
                self.stats['gap_count'] += 1
            self._last_comm_count = packet.comm_count

            sensor_inputs = split_fip_sensor_data(
                packet.phase_data,
                packet.sensor_count,
                packet_duration_seconds=packet.packet_duration_seconds,
                sample_rate_hz=packet.sample_rate_hz,
                logger=self.logger,
                comm_count=packet.comm_count,
            )
            if not sensor_inputs:
                self.logger.warning('No FIP sensor data available for packet #%d', packet.comm_count)
                return None

            unwrapped_by_sensor: Dict[int, np.ndarray] = {}
            filtered_by_sensor: Dict[int, np.ndarray] = {}
            downsampled_by_sensor: Dict[int, np.ndarray] = {}
            psd_by_sensor: Dict[int, np.ndarray] = {}
            effective_rate_by_sensor: Dict[int, float] = {}

            for sensor_index, phase_data in sensor_inputs.items():
                if phase_data.size == 0:
                    continue

                phase_data = np.asarray(phase_data)
                if np.max(np.abs(phase_data)) > 5:
                    phase_data = phase_data / np.pi

                phase_unwrapper, signal_filter, downsampler = self._get_sensor_processors(sensor_index)
                unwrapped, _ = phase_unwrapper.unwrap_phase(phase_data)
                if len(unwrapped) == 0:
                    self.stats['phase_unwrap_failure_count'] += 1
                    self.logger.warning(
                        'Phase unwrapping failed for packet #%d sensor FIP%d',
                        packet.comm_count,
                        sensor_index,
                    )
                    continue

                if signal_filter is not None:
                    filtered, _ = signal_filter.apply_filter(unwrapped)
                else:
                    filtered = unwrapped.copy()

                downsampled, _ = downsampler.downsample(filtered)
                downsample_factor = max(1, downsampler.get_current_factor())
                psd_data = unwrapped[::downsample_factor]
                effective_rate = normalize_fip_sample_rate(packet.sample_rate_hz) / downsample_factor

                unwrapped_by_sensor[sensor_index] = unwrapped
                filtered_by_sensor[sensor_index] = filtered
                downsampled_by_sensor[sensor_index] = downsampled
                psd_by_sensor[sensor_index] = psd_data
                effective_rate_by_sensor[sensor_index] = effective_rate

            if not downsampled_by_sensor:
                return None

            actual_sensor_count = max(downsampled_by_sensor.keys())
            selected_sensor = normalize_fip_sensor_index(packet.selected_sensor, actual_sensor_count)
            if selected_sensor not in downsampled_by_sensor:
                selected_sensor = min(downsampled_by_sensor.keys())

            unwrapped = unwrapped_by_sensor[selected_sensor]
            filtered = filtered_by_sensor[selected_sensor]
            downsampled = downsampled_by_sensor[selected_sensor]
            psd_data = psd_by_sensor[selected_sensor]
            effective_rate = effective_rate_by_sensor[selected_sensor]

            if packet.comm_count % 50 == 0:
                sensor_summary = ", ".join(
                    f"FIP{idx}:{len(unwrapped_by_sensor[idx])}->{len(filtered_by_sensor[idx])}->{len(downsampled_by_sensor[idx])}"
                    for idx in sorted(downsampled_by_sensor)
                )
                self.logger.info(
                    'Packet #%d sensors=%d selected=FIP%d %s',
                    packet.comm_count,
                    actual_sensor_count,
                    selected_sensor,
                    sensor_summary,
                )

            return ProcessedData(
                timestamp=packet.timestamp,
                unwrapped_data=unwrapped,
                filtered_data=filtered,
                downsampled_data=downsampled,
                psd_data=psd_data,
                effective_rate=effective_rate,
                comm_count=packet.comm_count,
                sensor_count=actual_sensor_count,
                selected_sensor=selected_sensor,
                packet_duration_seconds=normalize_fip_packet_duration(packet.packet_duration_seconds),
                raw_sample_rate_hz=normalize_fip_sample_rate(packet.sample_rate_hz),
                unwrapped_by_sensor=unwrapped_by_sensor,
                filtered_by_sensor=filtered_by_sensor,
                downsampled_by_sensor=downsampled_by_sensor,
                psd_by_sensor=psd_by_sensor,
            )
        except Exception as e:
            self.stats['processing_failure_count'] += 1
            self.logger.error(f'Error processing packet #{packet.comm_count}: {e}')
            return None

    def get_stats(self) -> Dict[str, Any]:
        return dict(self.stats)

    def stop(self):
        """Stop the thread."""
        self.running = False
        self.logger.info('Data processing thread stopping with stats: %s', self.get_stats())


class TimedomainPlotThread(QThread):
    """时域绘图线程"""

    plot_ready = pyqtSignal(np.ndarray, np.ndarray)  # timestamps, values

    def __init__(self):
        super().__init__()
        # 队列容量 200：5 Hz 包速率下约 40 s 缓冲。
        # 原始值 10 过小，主线程短暂忙碌（用户交互/菜单）即导致丢帧，
        # 采集期间发生的声音信号可能在实时波形中不可见。
        # 200 包 × 20000 样本 × 8 字节 ≈ 32 MB，内存开销可接受。
        self.input_queue = Queue(maxsize=200)
        self.running = False
        self.enabled = True

        # 时域显示缓冲区
        # 使用 deque 替代 list：
        # - append 为 O(1)，避免大时间窗口时 list 遍历裁剪的 O(N) 开销
        # - 窗口裁剪由 _update_maxlen() 动态调整 maxlen 实现，无需手动遍历
        # 初始容量按默认窗口 1 s、100 kHz 显示采样率预留 1.5 倍冗余
        self._display_sample_rate = 100000.0   # 初始估算，随数据包动态更新
        self.window_duration = 1.0             # 显示窗口1秒
        _initial_maxlen = int(self.window_duration * self._display_sample_rate * 1.5)
        self.data_buffer = deque(maxlen=_initial_maxlen)
        self.time_buffer = deque(maxlen=_initial_maxlen)
        self.update_interval = 5   # 每5个包更新一次
        self.packet_count = 0
        self.last_comm_count = None  # 用于丢弃重复/倒序数据包
        self.next_timestamp = 0.0    # 内部单调时间轴，避免外部时间戳抖动导致叠影

        self.logger = logging.getLogger(f'{__name__}.TimedomainPlotThread')

    def add_processed_data(self, data: ProcessedData):
        """添加处理后的数据"""
        if not self.enabled:
            return

        # 调试日志
        if data.comm_count % 50 == 0:
            self.logger.info(f"TimedomainPlotThread received processed packet #{data.comm_count}")

        try:
            if not self.input_queue.full():
                self.input_queue.put(data, block=False)
            else:
                self.logger.warning(f"TimePlotThread queue full, dropping packet #{data.comm_count}")
        except Full:
            pass  # 丢弃数据避免阻塞

    def set_enabled(self, enabled: bool):
        """启用/禁用时域绘图"""
        self.enabled = enabled
        if not enabled:
            # 清空缓冲区
            self.data_buffer.clear()
            self.time_buffer.clear()
            self.packet_count = 0
            self.last_comm_count = None
            self.next_timestamp = 0.0

    def set_window_duration(self, duration: float):
        """设置显示窗口时长，同时动态调整缓冲区容量。

        Args:
            duration: 显示窗口时长（秒）。

        deque 的 maxlen 不支持原地修改，通过重建 deque 实现更新。
        重建时保留现有数据，避免因窗口调整而清空已缓冲的波形。
        """
        self.window_duration = duration
        new_maxlen = int(duration * self._display_sample_rate * 1.5)
        new_maxlen = max(new_maxlen, 1000)  # 最小保留 1000 个样本
        # 重建 deque 以更新 maxlen，保留现有缓冲数据
        self.data_buffer = deque(self.data_buffer, maxlen=new_maxlen)
        self.time_buffer = deque(self.time_buffer, maxlen=new_maxlen)

    def _reset_stream_state(self):
        """重置时域流状态（用于通信计数器重置/重连场景）。"""
        self.data_buffer.clear()
        self.time_buffer.clear()
        self.packet_count = 0
        self.last_comm_count = None
        self.next_timestamp = 0.0

    def run(self):
        """绘图循环"""
        self.running = True
        # 每次启动都重置状态，避免停启后残留历史时序
        self._reset_stream_state()
        self.logger.info("Timedomain plot thread started")

        while self.running:
            try:
                data = self.input_queue.get(timeout=0.2)
                if self.enabled:
                    self._process_time_data(data)

            except Empty:
                continue
            except Exception as e:
                self.logger.error(f"Time plot error: {e}")

    def _process_time_data(self, data: ProcessedData):
        """处理时域数据"""
        try:
            # 丢弃重复或小范围倒序包，但允许“计数器重置/重连”后自动恢复绘图。
            if self.last_comm_count is not None:
                if data.comm_count == self.last_comm_count:
                    self.logger.warning(
                        f"Skipping duplicate packet in time plot thread: "
                        f"comm_count={data.comm_count}, last={self.last_comm_count}"
                    )
                    return

                if data.comm_count < self.last_comm_count:
                    # 例如 139 -> 0，判定为通信端计数器重置（重连或发送端复位）
                    backward_gap = self.last_comm_count - data.comm_count
                    if backward_gap >= 20:
                        self.logger.info(
                            f"Detected comm_count reset in time plot thread: "
                            f"last={self.last_comm_count}, current={data.comm_count}. "
                            "Resetting time-domain stream state."
                        )
                        self._reset_stream_state()
                    else:
                        self.logger.warning(
                            f"Skipping stale out-of-order packet in time plot thread: "
                            f"comm_count={data.comm_count}, last={self.last_comm_count}"
                        )
                        return

            # 调试日志
            if data.comm_count % 50 == 0:
                self.logger.info(f"TimedomainPlotThread processing packet #{data.comm_count}")

            # 时域显示降采样：200kHz -> 100kHz
            display_data = data.downsampled_data[::2]

            # 计算显示采样率和时间戳
            # 不能写死100kHz：effective_rate 会随前面板降采样倍数变化。
            # 若时间步长错误，会导致窗口内轨迹重叠，看起来像”多条曲线叠加”。
            display_sample_rate = max(data.effective_rate / 2.0, 1.0)
            dt = 1.0 / display_sample_rate

            # 同步更新显示采样率，用于 set_window_duration 计算 deque maxlen
            if self._display_sample_rate != display_sample_rate:
                self._display_sample_rate = display_sample_rate
                # 采样率变化时同步调整缓冲区容量
                new_maxlen = int(self.window_duration * display_sample_rate * 1.5)
                new_maxlen = max(new_maxlen, 1000)
                self.data_buffer = deque(self.data_buffer, maxlen=new_maxlen)
                self.time_buffer = deque(self.time_buffer, maxlen=new_maxlen)

            # 使用内部单调时间轴，避免外部timestamp抖动/重复导致X轴回退或重叠。
            start_time = self.next_timestamp
            timestamps = start_time + np.arange(len(display_data)) * dt
            self.next_timestamp = start_time + len(display_data) * dt

            # 更新缓冲区（deque 自动按 maxlen 丢弃最旧数据，无需手动裁剪）
            self.data_buffer.extend(display_data)
            self.time_buffer.extend(timestamps)

            self.packet_count += 1
            self.last_comm_count = data.comm_count

            # 定期更新绘图（减少UI负载）
            if self.packet_count % self.update_interval == 0:
                self.logger.info(f"Updating time plot - packet count: {self.packet_count}")
                if data.comm_count % 50 == 0:
                    packet_span = len(display_data) * dt
                    self.logger.debug(
                        f"Time axis info - effective_rate={data.effective_rate:.1f}Hz, "
                        f"display_rate={display_sample_rate:.1f}Hz, span={packet_span:.4f}s"
                    )
                self._update_plot()

        except Exception as e:
            self.logger.error(f"Error processing time data for packet #{data.comm_count}: {e}")

    def _update_plot(self):
        """更新时域绘图，将缓冲区数据发射给 UI 线程。

        由于 data_buffer 和 time_buffer 已改用 deque(maxlen=N)，
        超出窗口的旧数据由 deque 自动丢弃，此处无需 O(N) 遍历裁剪，
        直接转换为 numpy 数组后发射信号即可。
        """
        try:
            if len(self.time_buffer) == 0:
                return

            # deque 直接转 numpy 数组，O(N) 但只做一次，且 N 已受 maxlen 控制
            times_arr = np.array(self.time_buffer)
            values_arr = np.array(self.data_buffer)

            # 转换为相对时间（从 0 开始），便于绘图 X 轴显示
            times_rel = times_arr - times_arr[0]

            # 发射绘图信号
            self.plot_ready.emit(times_rel, values_arr)

        except Exception as e:
            self.logger.error(f"Error updating time plot: {e}")

    def stop(self):
        """停止线程"""
        self.running = False
        self.logger.info("Timedomain plot thread stopping")


class PSDPlotThread(QThread):
    """PSD绘图线程"""

    plot_ready = pyqtSignal(np.ndarray, np.ndarray)  # frequencies, psd_db

    def __init__(self, psd_calculator):
        super().__init__()
        # 队列容量 20：PSD 每 5 包计算一次（约 1 次/秒），20 包提供约 4 s 缓冲。
        # 原始值 5 在长窗口 PSD 计算（单次 50-100 ms）时容易堆满，
        # 导致 PSD 画面停止更新。
        self.input_queue = Queue(maxsize=20)
        self.running = False
        self.enabled = True

        self.psd_calculator = psd_calculator
        self.update_interval = 5  # 每5个包计算一次PSD
        self.packet_count = 0

        self.logger = logging.getLogger(f'{__name__}.PSDPlotThread')

    def add_processed_data(self, data: ProcessedData):
        """添加处理后的数据"""
        if not self.enabled:
            return

        try:
            if not self.input_queue.full():
                self.input_queue.put(data, block=False)
        except Full:
            pass

    def set_enabled(self, enabled: bool):
        """启用/禁用PSD绘图"""
        self.enabled = enabled
        if not enabled:
            self.packet_count = 0

    def reset_state(self, clear_queue: bool = False):
        """Reset PSD packet cadence and optionally discard queued packets."""
        self.packet_count = 0
        if clear_queue:
            while True:
                try:
                    self.input_queue.get_nowait()
                except Empty:
                    break

    def run(self):
        """PSD计算循环"""
        self.running = True
        self.logger.info("PSD plot thread started")

        while self.running:
            try:
                data = self.input_queue.get(timeout=0.5)
                if self.enabled:
                    self.packet_count += 1
                    # 降低PSD计算频率
                    if self.packet_count % self.update_interval == 0:
                        self._calculate_psd(data)

            except Empty:
                continue
            except Exception as e:
                self.logger.error(f"PSD plot error: {e}")

    def _calculate_psd(self, data: ProcessedData):
        """计算PSD"""
        try:
            # 使用相位展开后、未滤波的数据计算PSD（满足需求）
            # 采样率与psd_data保持一致：effective_rate = ORIGINAL_SAMPLE_RATE / downsample_factor
            self.psd_calculator.sample_rate = data.effective_rate
            frequencies, psd = self.psd_calculator.compute_psd(data.psd_data)

            if len(frequencies) > 0:
                # 转换为dB
                psd_safe = np.maximum(psd, 1e-15)
                psd_db = np.clip(10 * np.log10(psd_safe), -200, 100)

                # 发射PSD绘图信号
                self.plot_ready.emit(frequencies, psd_db)

        except Exception as e:
            self.logger.error(f"Error calculating PSD: {e}")

    def stop(self):
        """停止线程"""
        self.running = False
        self.logger.info("PSD plot thread stopping")


class DataStorageThread(QThread):
    """Persist exact storage windows using an independent raw-packet path."""

    STORAGE_DOWNSAMPLE_FACTOR = 5
    DEFAULT_STORAGE_SAMPLE_RATE = DEFAULT_FIP_SAMPLE_RATE_HZ / STORAGE_DOWNSAMPLE_FACTOR
    RAW_QUEUE_MAXSIZE = 2000

    def __init__(self, phase_unwrapper, storage_path: str = "D:/PCCP/FIPdata", storage_interval_seconds: float = 10.0):
        super().__init__()
        self.input_queue = Queue(maxsize=self.RAW_QUEUE_MAXSIZE)
        # 控制命令队列：UI 线程通过此队列向存储线程发送 enable/disable 指令，
        # 避免 UI 线程直接修改存储线程使用的缓冲区（T1-10 竞争问题）。
        self._ctrl_queue: Queue = Queue()
        self.running = False
        self.enabled = False
        # 排空模式标志：begin_drain() 后不再接受新包，run 循环排空后退出（T1-09）
        self._drain_mode = False

        self.phase_unwrapper = phase_unwrapper
        self._phase_unwrappers: Dict[int, Any] = {1: phase_unwrapper}
        self.raw_sample_rate_hz = DEFAULT_FIP_SAMPLE_RATE_HZ
        self.storage_sample_rate_hz = self.raw_sample_rate_hz / self.STORAGE_DOWNSAMPLE_FACTOR
        self.packet_duration_seconds = DEFAULT_FIP_PACKET_DURATION_SECONDS
        self.storage_path = storage_path
        self.storage_interval_seconds = float(storage_interval_seconds)
        self.target_chunk_samples = 0
        self.buffered_requests = []
        self.buffered_sample_count = 0
        self.current_chunk_start_comm_count = None
        self.current_chunk_last_comm_count = None
        self.current_chunk_sensor_count = None
        self.current_chunk_sample_rate = None
        self.current_chunk_packet_duration = None
        self.run_started_at = None
        self.saved_file_count = 0
        self.saved_sample_count = 0
        self.saved_duration_seconds = 0.0
        self.last_buffered_comm_count = None

        self.logger = logging.getLogger(f'{__name__}.DataStorageThread')
        self.stats = {
            'raw_queue_maxsize': self.RAW_QUEUE_MAXSIZE,
            'raw_queue_peak': 0,
            'raw_packets_enqueued': 0,
            'raw_packets_processed': 0,
            'raw_packets_missing': 0,
            'raw_packets_duplicate_or_out_of_order': 0,
            'phase_unwrap_failure_count': 0,
            'storage_failure_count': 0,   # 队列满丢包计数（磁盘过载时触发）
            'saved_file_count': 0,
            'saved_sample_count': 0,
        }
        self.set_storage_interval_seconds(storage_interval_seconds)

    def add_raw_packet(self, packet: RawDataPacket):
        """将原始数据包加入存储队列（主线程调用，必须非阻塞）。

        Args:
            packet: 来自 TCP 服务器的原始数据包。

        设计约束：
            本方法在 Qt 主线程的信号槽中调用（调用链：TCP data_received
            信号 → _process_data_packet → process_raw_packet → 此方法）。
            主线程绝对不能阻塞，否则 Qt 事件循环停转，UI 冻结。

            使用 put_nowait（非阻塞）：队列满时宁可丢包并记录告警，
            也不能阻塞主线程。队列满仅在磁盘严重过载时发生，此时继续
            阻塞主线程会导致更严重的全面卡死和更大范围的数据丢失。
        """
        if not self.enabled:
            return
        # 排空模式下不再接受新包，等待队列中已有包处理完毕后退出
        if self._drain_mode:
            return

        try:
            self.input_queue.put_nowait(packet)
            self.stats['raw_packets_enqueued'] += 1
            self.stats['raw_queue_peak'] = max(
                self.stats['raw_queue_peak'], self.input_queue.qsize()
            )
        except Full:
            # 队列满：记录丢包，绝不阻塞主线程
            self.stats['storage_failure_count'] += 1
            self.logger.error(
                'Storage queue full (size=%d), dropping packet #%d. '
                'Disk may be too slow. Check storage_failure_count in stats.',
                self.RAW_QUEUE_MAXSIZE,
                packet.comm_count,
            )

    def set_enabled(self, enabled: bool):
        """通过控制队列向存储线程发送启停指令（线程安全，T1-10）。

        原实现直接从 UI 线程修改 enabled 标志并清空缓冲区，与存储线程
        同时访问 buffered_requests 存在竞争条件。改为通过 _ctrl_queue
        发送命令，由存储线程在包边界处理，保证串行访问缓冲区。

        Args:
            enabled: True 表示启用存储，False 表示停用并刷新当前缓冲区。
        """
        self._ctrl_queue.put_nowait({'cmd': 'set_enabled', 'value': enabled})

    def set_storage_path(self, path: str):
        self.storage_path = path

    def set_storage_interval_seconds(self, interval_seconds: float):
        safe_seconds = max(float(interval_seconds), 0.1)
        self.storage_interval_seconds = safe_seconds
        self.target_chunk_samples = max(1, int(round(safe_seconds * self.storage_sample_rate_hz)))
        self.logger.info(
            'Storage interval set to %.1fs (%d samples at %.0fHz)',
            self.storage_interval_seconds,
            self.target_chunk_samples,
            self.storage_sample_rate_hz,
        )
        self._save_completed_chunks()

    def set_input_parameters(self, sample_rate_hz: float, packet_duration_seconds: float):
        """Update raw FIP sample rate and packet duration used by storage metadata."""
        sample_rate_hz = normalize_fip_sample_rate(sample_rate_hz)
        packet_duration_seconds = normalize_fip_packet_duration(packet_duration_seconds)
        changed = (
            abs(sample_rate_hz - self.raw_sample_rate_hz) > 1e-6
            or abs(packet_duration_seconds - self.packet_duration_seconds) > 1e-9
        )
        if self.buffered_requests and changed:
            self._flush_buffered_data()
            self._clear_buffer()
        self.raw_sample_rate_hz = sample_rate_hz
        self.storage_sample_rate_hz = sample_rate_hz / self.STORAGE_DOWNSAMPLE_FACTOR
        self.packet_duration_seconds = packet_duration_seconds
        self.target_chunk_samples = max(1, int(round(self.storage_interval_seconds * self.storage_sample_rate_hz)))
        self.logger.info(
            'FIP storage input parameters set: raw_sample_rate=%.1fHz, packet_duration=%.6fs, storage_rate=%.1fHz',
            self.raw_sample_rate_hz,
            self.packet_duration_seconds,
            self.storage_sample_rate_hz,
        )

    def begin_run_cycle(self):
        self.run_started_at = datetime.now()
        self.saved_file_count = 0
        self.saved_sample_count = 0
        self.saved_duration_seconds = 0.0
        self.last_buffered_comm_count = None
        for unwrapper in self._phase_unwrappers.values():
            if hasattr(unwrapper, 'reset'):
                unwrapper.reset()
        self._clear_buffer()

    def end_run_cycle(self):
        self._flush_buffered_data()
        self.run_started_at = None

    def _clear_buffer(self):
        self.buffered_requests = []
        self.buffered_sample_count = 0
        self.current_chunk_start_comm_count = None
        self.current_chunk_last_comm_count = None
        self.current_chunk_sensor_count = None
        self.current_chunk_sample_rate = None
        self.current_chunk_packet_duration = None
        self.last_buffered_comm_count = None

    def _get_phase_unwrapper(self, sensor_index: int):
        if sensor_index not in self._phase_unwrappers:
            self._phase_unwrappers[sensor_index] = type(self.phase_unwrapper)()
        return self._phase_unwrappers[sensor_index]

    def _build_storage_request(self, packet: RawDataPacket) -> Optional[StorageRequest]:
        sensor_inputs = split_fip_sensor_data(
            packet.phase_data,
            packet.sensor_count,
            packet_duration_seconds=packet.packet_duration_seconds,
            sample_rate_hz=packet.sample_rate_hz,
            logger=self.logger,
            comm_count=packet.comm_count,
        )
        if not sensor_inputs:
            return None

        storage_by_sensor: Dict[int, np.ndarray] = {}
        for sensor_index, phase_data in sensor_inputs.items():
            if phase_data.size == 0:
                continue
            phase_data = np.asarray(phase_data)
            if np.max(np.abs(phase_data)) > 5:
                phase_data = phase_data / np.pi

            phase_unwrapper = self._get_phase_unwrapper(sensor_index)
            unwrapped, _ = phase_unwrapper.unwrap_phase(phase_data)
            if len(unwrapped) == 0:
                self.stats['phase_unwrap_failure_count'] += 1
                self.logger.warning(
                    'Storage phase unwrapping failed for packet #%d FIP%d, storing wrapped phase fallback',
                    packet.comm_count,
                    sensor_index,
                )
                unwrapped = np.asarray(phase_data, dtype=np.float64) * np.pi

            storage_by_sensor[sensor_index] = np.asarray(
                unwrapped[::self.STORAGE_DOWNSAMPLE_FACTOR],
                dtype=np.float64,
            )

        if not storage_by_sensor:
            return None

        sensor_ids = sorted(storage_by_sensor)
        if len(sensor_ids) == 1:
            storage_data = storage_by_sensor[sensor_ids[0]]
        else:
            min_length = min(len(storage_by_sensor[sensor_id]) for sensor_id in sensor_ids)
            if min_length <= 0:
                return None
            storage_data = np.vstack([
                storage_by_sensor[sensor_id][:min_length]
                for sensor_id in sensor_ids
            ])

        return StorageRequest(
            data=storage_data,
            comm_count=packet.comm_count,
            timestamp=packet.timestamp,
            sample_rate=normalize_fip_sample_rate(packet.sample_rate_hz) / self.STORAGE_DOWNSAMPLE_FACTOR,
            data_type='phase_unwrapped_downsampled',
            sensor_count=len(sensor_ids),
            selected_sensor=normalize_fip_sensor_index(packet.selected_sensor, len(sensor_ids)),
            packet_duration_seconds=normalize_fip_packet_duration(packet.packet_duration_seconds),
            raw_sample_rate_hz=normalize_fip_sample_rate(packet.sample_rate_hz),
        )

    def _append_request(self, request: StorageRequest):
        request_sensor_count = _data_sensor_count(request.data)
        if (
            self.buffered_requests
            and self.current_chunk_sensor_count is not None
            and (
                request_sensor_count != self.current_chunk_sensor_count
                or (
                    self.current_chunk_sample_rate is not None
                    and abs(float(request.sample_rate) - float(self.current_chunk_sample_rate)) > 1e-6
                )
                or (
                    self.current_chunk_packet_duration is not None
                    and abs(float(request.packet_duration_seconds) - float(self.current_chunk_packet_duration)) > 1e-9
                )
            )
        ):
            self.logger.info(
                'FIP storage stream parameters changed: sensors %s -> %s, rate %s -> %s, duration %s -> %s; flushing current chunk',
                self.current_chunk_sensor_count,
                request_sensor_count,
                self.current_chunk_sample_rate,
                request.sample_rate,
                self.current_chunk_packet_duration,
                request.packet_duration_seconds,
            )
            self._flush_buffered_data()

        if self.current_chunk_start_comm_count is None:
            self.current_chunk_start_comm_count = request.comm_count
        if self.current_chunk_sensor_count is None:
            self.current_chunk_sensor_count = request_sensor_count
            self.current_chunk_sample_rate = float(request.sample_rate)
            self.current_chunk_packet_duration = float(request.packet_duration_seconds)

        if self.last_buffered_comm_count is not None:
            if request.comm_count > self.last_buffered_comm_count + 1:
                missing_packets = request.comm_count - self.last_buffered_comm_count - 1
                self.stats['raw_packets_missing'] += missing_packets
                self.logger.warning(
                    'Storage path detected missing packets between #%d and #%d (%d packet(s))',
                    self.last_buffered_comm_count,
                    request.comm_count,
                    missing_packets,
                )
            elif request.comm_count <= self.last_buffered_comm_count:
                self.stats['raw_packets_duplicate_or_out_of_order'] += 1
                self.logger.warning(
                    'Storage path received duplicate/out-of-order packet #%d after #%d',
                    request.comm_count,
                    self.last_buffered_comm_count,
                )

        self.current_chunk_last_comm_count = request.comm_count
        self.last_buffered_comm_count = request.comm_count
        self.buffered_requests.append(request)
        self.buffered_sample_count += _data_sample_count(request.data)

    def begin_drain(self):
        """停止接受新包，进入排空模式（T1-09 两阶段停止第一阶段）。

        调用后 add_raw_packet 不再接受新包，run 循环继续处理队列中
        已有的包直到队列空，再执行最终 flush 后退出。
        """
        self._drain_mode = True
        self.logger.info(
            'Storage thread entering drain mode, queue depth=%d',
            self.input_queue.qsize(),
        )

    def run(self):
        """存储循环，同时处理数据包和控制命令。

        控制命令处理（T1-10）：
            每次从 input_queue 取包后，先检查 _ctrl_queue 中的控制命令。
            命令由 set_enabled() 发送，由本线程串行处理，避免多线程竞争。

        两阶段排空（T1-09）：
            _drain_mode=True 时不再等待新包，队列清空后立即退出。
            这保证 stop() 前队列中的包都能落盘。
        """
        self.running = True
        self.logger.info('Data storage thread started')

        while self.running:
            # 处理控制命令队列（在包边界处理，保证串行访问缓冲区）
            self._process_ctrl_commands()

            try:
                # 排空模式下队列已空时退出循环
                if self._drain_mode and self.input_queue.empty():
                    self.logger.info('Storage drain complete, exiting run loop')
                    break

                packet = self.input_queue.get(timeout=0.2)
                if not self.enabled:
                    # 存储被禁用时丢弃包，但不阻塞
                    continue

                request = self._build_storage_request(packet)
                if request is None:
                    self.stats['storage_failure_count'] += 1
                    continue

                self.stats['raw_packets_processed'] += 1
                self._append_request(request)
                self._save_completed_chunks()
            except Empty:
                # 排空模式下超时即表示队列已空
                if self._drain_mode:
                    self.logger.info('Storage drain complete (timeout), exiting run loop')
                    break
                continue
            except Exception as e:
                self.stats['storage_failure_count'] += 1
                self.logger.error(f'Storage error: {e}')

        self._flush_buffered_data()

    def _process_ctrl_commands(self):
        """处理控制命令队列中的所有待处理命令（在存储线程中调用，串行安全）。

        命令格式：{'cmd': str, 'value': Any}
        支持的命令：
            set_enabled: 启停存储，禁用时刷新并清空缓冲区
        """
        while True:
            try:
                cmd = self._ctrl_queue.get_nowait()
                if cmd['cmd'] == 'set_enabled':
                    new_enabled = cmd['value']
                    if self.enabled == new_enabled:
                        continue
                    self.enabled = new_enabled
                    if not new_enabled:
                        # 禁用存储时在存储线程中安全地刷新和清空缓冲区
                        self._flush_buffered_data()
                        self._clear_buffer()
                        self.logger.info('Storage disabled, buffer flushed and cleared')
                    else:
                        self.logger.info('Storage enabled')
            except Empty:
                break

    def stop(self):
        """停止存储线程（T1-09 两阶段停止第二阶段）。

        设置 running=False 通知 run 循环退出。若已处于排空模式，
        run 循环会在队列清空后自行退出；否则直接退出（可能丢尾包）。
        建议先调用 begin_drain() 等待排空后再调用 stop()。
        """
        self.running = False
        self.logger.info('Data storage thread stopping with stats: %s', self.get_stats())

    def _save_completed_chunks(self):
        while self.buffered_sample_count >= self.target_chunk_samples:
            chunk_data, start_comm_count, end_comm_count, sample_rate, packet_duration, raw_sample_rate = self._extract_chunk(self.target_chunk_samples)
            self._save_chunk(chunk_data, start_comm_count, end_comm_count, sample_rate, packet_duration, raw_sample_rate)

    def _flush_buffered_data(self):
        if not self.buffered_requests or self.buffered_sample_count <= 0:
            return

        self.logger.info('Flushing partial storage buffer with %d sample(s)', self.buffered_sample_count)
        chunk_data, start_comm_count, end_comm_count, sample_rate, packet_duration, raw_sample_rate = self._extract_chunk(self.buffered_sample_count)
        self._save_chunk(chunk_data, start_comm_count, end_comm_count, sample_rate, packet_duration, raw_sample_rate)

    def _extract_chunk(self, target_samples: int):
        if target_samples <= 0 or self.buffered_sample_count < target_samples:
            raise ValueError('Insufficient buffered data for requested chunk size')

        chunk_parts = []
        samples_needed = target_samples
        start_comm_count = self.current_chunk_start_comm_count
        end_comm_count = self.current_chunk_last_comm_count
        chunk_sample_rate = float(self.buffered_requests[0].sample_rate)
        chunk_packet_duration = float(self.buffered_requests[0].packet_duration_seconds)
        chunk_raw_sample_rate = float(self.buffered_requests[0].raw_sample_rate_hz)

        while samples_needed > 0 and self.buffered_requests:
            request = self.buffered_requests[0]
            request_length = _data_sample_count(request.data)

            if request_length <= samples_needed:
                chunk_parts.append(request.data)
                end_comm_count = request.comm_count
                self.buffered_requests.pop(0)
                self.buffered_sample_count -= request_length
                samples_needed -= request_length
            else:
                if np.asarray(request.data).ndim == 2:
                    chunk_parts.append(request.data[:, :samples_needed])
                    remaining_data = request.data[:, samples_needed:]
                else:
                    chunk_parts.append(request.data[:samples_needed])
                    remaining_data = request.data[samples_needed:]
                end_comm_count = request.comm_count
                self.buffered_requests[0] = StorageRequest(
                    data=remaining_data,
                    comm_count=request.comm_count,
                    timestamp=request.timestamp,
                    sample_rate=request.sample_rate,
                    data_type=request.data_type,
                    sensor_count=request.sensor_count,
                    selected_sensor=request.selected_sensor,
                    packet_duration_seconds=request.packet_duration_seconds,
                    raw_sample_rate_hz=request.raw_sample_rate_hz,
                )
                self.buffered_sample_count -= samples_needed
                samples_needed = 0

        if samples_needed != 0:
            raise RuntimeError('Failed to extract the requested number of samples from storage buffer')

        if self.buffered_requests:
            self.current_chunk_start_comm_count = self.buffered_requests[0].comm_count
            self.current_chunk_last_comm_count = self.buffered_requests[-1].comm_count
            self.current_chunk_sensor_count = _data_sensor_count(self.buffered_requests[0].data)
            self.current_chunk_sample_rate = float(self.buffered_requests[0].sample_rate)
            self.current_chunk_packet_duration = float(self.buffered_requests[0].packet_duration_seconds)
        else:
            self.current_chunk_start_comm_count = None
            self.current_chunk_last_comm_count = None
            self.current_chunk_sensor_count = None
            self.current_chunk_sample_rate = None
            self.current_chunk_packet_duration = None

        axis = 1 if np.asarray(chunk_parts[0]).ndim == 2 else 0
        return (
            np.concatenate(chunk_parts, axis=axis),
            start_comm_count,
            end_comm_count,
            chunk_sample_rate,
            chunk_packet_duration,
            chunk_raw_sample_rate,
        )

    def _build_file_timestamp(self, sample_rate: float) -> datetime:
        base_time = self.run_started_at or datetime.now()
        return base_time + timedelta(seconds=self.saved_duration_seconds)

    def _save_chunk(
        self,
        phase_data: np.ndarray,
        start_comm_count: int,
        end_comm_count: int,
        sample_rate: float,
        packet_duration_seconds: float,
        raw_sample_rate_hz: float,
    ):
        try:
            from pathlib import Path

            if phase_data is None or _data_sample_count(phase_data) == 0:
                return

            sample_rate = normalize_fip_sample_rate(sample_rate)
            raw_sample_rate_hz = normalize_fip_sample_rate(raw_sample_rate_hz)
            packet_duration_seconds = normalize_fip_packet_duration(packet_duration_seconds)
            sensor_count = _data_sensor_count(phase_data)
            sample_count = _data_sample_count(phase_data)
            duration_seconds = 0.0 if sample_rate <= 0 else sample_count / sample_rate

            base_path = Path(self.storage_path)
            base_path.mkdir(parents=True, exist_ok=True)

            file_timestamp = self._build_file_timestamp(sample_rate)
            self.saved_file_count += 1
            timestamp_str = file_timestamp.strftime('%Y%m%dT%H%M%S.%f')[:-3]
            filename_prefix = 'FIP2' if sensor_count == 2 else 'FIP'
            sample_rate_label = self._format_sample_rate_label(sample_rate)
            filename = f'{self.saved_file_count:07d}-{filename_prefix}-{sample_rate_label}-{timestamp_str}.npz'
            file_path = base_path / filename
            data_info = {
                'type': 'phase_unwrapped_downsampled',
                'length': int(sample_count),
                'samples_per_sensor': int(sample_count),
                'total_values': int(np.asarray(phase_data).size),
                'sensor_count': int(sensor_count),
                'downsample_factor': self.STORAGE_DOWNSAMPLE_FACTOR,
                'packet_duration_seconds': float(packet_duration_seconds),
                'raw_sample_rate_hz': float(raw_sample_rate_hz),
                'packet_points_per_sensor': int(round(raw_sample_rate_hz * packet_duration_seconds)),
                'storage_points_per_packet_per_sensor': int(round(sample_rate * packet_duration_seconds)),
                'packet_count_estimate': int(math.ceil(sample_count / max(sample_rate * packet_duration_seconds, 1))),
                'start_comm_count': start_comm_count,
                'end_comm_count': end_comm_count,
                'duration_seconds': duration_seconds,
                'file_sequence': self.saved_file_count,
                'stream_start_time': file_timestamp.isoformat(timespec='milliseconds'),
                'save_time': datetime.now().isoformat(),
            }

            payload = {
                'phase_data': phase_data,
                'comm_count': end_comm_count,
                'timestamp': self.saved_duration_seconds,
                'sample_rate': sample_rate,
                'raw_sample_rate_hz': raw_sample_rate_hz,
                'packet_duration_seconds': packet_duration_seconds,
                'fip_sensor_count': np.int32(sensor_count),
                'data_info': data_info,
                'format_version': np.array('wb-monitor-tab1-fip-v2' if sensor_count == 2 else 'wb-monitor-tab1-fip-v1'),
            }
            if sensor_count == 2:
                payload['fip1_phase_data'] = np.asarray(phase_data[0], dtype=np.float64)
                payload['fip2_phase_data'] = np.asarray(phase_data[1], dtype=np.float64)
            else:
                payload['fip1_phase_data'] = np.asarray(phase_data, dtype=np.float64)

            np.savez_compressed(file_path, **payload)

            self.saved_sample_count += sample_count
            self.saved_duration_seconds += duration_seconds
            self.stats['saved_file_count'] = self.saved_file_count
            self.stats['saved_sample_count'] = self.saved_sample_count

            self.logger.info(
                'Saved data to %s (sensors=%d, samples_per_sensor=%d, duration=%.1fs, start_comm=%s, end_comm=%s)',
                filename,
                sensor_count,
                sample_count,
                duration_seconds,
                start_comm_count,
                end_comm_count,
            )
        except Exception as e:
            self.stats['storage_failure_count'] += 1
            self.logger.error(f'Error saving data: {e}')

    def _format_sample_rate_label(self, sample_rate: float) -> str:
        if sample_rate >= 1_000_000 and abs(sample_rate % 1_000_000) < 1e-6:
            return f'{int(sample_rate / 1_000_000)}M'
        if sample_rate >= 1000 and abs(sample_rate % 1000) < 1e-6:
            return f'{int(sample_rate / 1000)}K'
        return f'{int(round(sample_rate))}Hz'

    def get_stats(self) -> Dict[str, Any]:
        return dict(self.stats)

    def stop(self):
        self.running = False
        self.logger.info('Data storage thread stopping with stats: %s', self.get_stats())


class OptimizedTab1ThreadManager(QObject):
    """优化的Tab1线程管理器"""

    def __init__(self, processors, psd_calculator):
        super().__init__()
        self.logger = logging.getLogger(f'{__name__}.OptimizedTab1ThreadManager')

        # 创建线程
        phase_unwrapper, signal_filter, downsampler = processors
        self.data_processor = DataProcessingThread(phase_unwrapper, signal_filter, downsampler)
        self.time_plotter = TimedomainPlotThread()
        self.psd_plotter = PSDPlotThread(psd_calculator)
        self.storage_thread = DataStorageThread(phase_unwrapper=type(phase_unwrapper)())

        # 绘图控件引用
        self.time_plot_widget = None
        self.psd_plot_widget = None
        self.time_curve = None
        self.psd_curve = None
        self.fip_sensor_count = 1
        self.selected_fip_sensor = 1
        self.fip_packet_duration_seconds = DEFAULT_FIP_PACKET_DURATION_SECONDS
        self.fip_sample_rate_hz = DEFAULT_FIP_SAMPLE_RATE_HZ

        # 设置信号连接
        self._setup_connections()

    def _setup_connections(self):
        """设置线程间信号连接"""
        # 数据处理完成 -> 分发到各线程
        self.data_processor.data_processed.connect(self._distribute_processed_data)

        # 绘图线程 -> UI更新（使用优化的信号机制）
        self.time_plotter.plot_ready.connect(self._update_time_plot)
        self.psd_plotter.plot_ready.connect(self._update_psd_plot)

    def set_plot_widgets(self, time_plot, psd_plot):
        """设置绘图控件"""
        self.time_plot_widget = time_plot
        self.psd_plot_widget = psd_plot

        # 清空所有现有的曲线避免重复
        if time_plot:
            time_plot.clear()  # 清空所有现有项目
            self.time_curve = time_plot.plot(pen='b', name='时域信号')

        if psd_plot:
            psd_plot.clear()  # 清空所有现有项目
            self.psd_curve = psd_plot.plot(pen='r', name='PSD')

    def _ensure_plot_curves(self):
        """确保绘图曲线对象可用（处理clear()后对象失效的情况）"""
        self.time_curve = self._ensure_single_curve(
            plot_widget=self.time_plot_widget,
            current_curve=self.time_curve,
            pen='b',
            name='时域信号'
        )

        self.psd_curve = self._ensure_single_curve(
            plot_widget=self.psd_plot_widget,
            current_curve=self.psd_curve,
            pen='r',
            name='PSD'
        )

    def _ensure_single_curve(self, plot_widget, current_curve, pen, name):
        """确保每个PlotWidget仅保留一条曲线，避免曲线不断叠加。"""
        if plot_widget is None:
            return None

        data_items = plot_widget.listDataItems()

        # 如果当前曲线不可用，优先复用现有第一条曲线，避免重复创建。
        if current_curve is None or current_curve not in data_items:
            current_curve = data_items[0] if data_items else None

        if current_curve is None:
            current_curve = plot_widget.plot(pen=pen, name=name)
            data_items = plot_widget.listDataItems()

        # 删除多余曲线，保证只剩一条可更新曲线。
        for item in list(data_items):
            if item is not current_curve:
                try:
                    plot_widget.removeItem(item)
                except Exception:
                    pass

        return current_curve

    def start(self):
        """启动所有线程"""
        # 清空绘图数据
        self._clear_plots()
        self.data_processor.reset_state(clear_queue=True)
        self.time_plotter._reset_stream_state()
        self.psd_plotter.reset_state(clear_queue=True)
        self.storage_thread.begin_run_cycle()

        self.data_processor.start()
        self.time_plotter.start()
        self.psd_plotter.start()
        self.storage_thread.start()

        self.logger.info("All Tab1 threads started")

    def stop(self):
        """两阶段停止所有 Tab1 线程（T1-09）。

        阶段一：排空存储队列
            调用 storage_thread.begin_drain() 后，存储线程继续处理队列中已有的包，
            主线程等待排空完成（最多 10 s），保证停止前队列中的包全部落盘。

        阶段二：停止所有线程
            向各线程发送停止信号，最多等待 3 s。
        """
        # --- 阶段一：排空存储队列 ---
        self.storage_thread.begin_drain()
        # 等待存储线程排空（最多 10 s）
        drain_timeout_ms = 10000
        drain_wait_start = time.time()
        while self.storage_thread.isRunning():
            elapsed_ms = (time.time() - drain_wait_start) * 1000
            if elapsed_ms >= drain_timeout_ms:
                self.logger.warning(
                    'Storage drain timeout after %.1f s, remaining queue=%d. '
                    'Some tail packets may not be saved.',
                    elapsed_ms / 1000,
                    self.storage_thread.input_queue.qsize(),
                )
                break
            # 排空完成条件：队列为空且线程仍在运行（run 循环会自行退出 drain 模式）
            if self.storage_thread.input_queue.empty():
                # 给最后一个 flush 一点时间完成
                time.sleep(0.1)
                break
            time.sleep(0.05)

        self.logger.info(
            'Storage drain finished. Saved files=%d, saved samples=%d',
            self.storage_thread.saved_file_count,
            self.storage_thread.saved_sample_count,
        )

        # --- 阶段二：停止所有线程 ---
        threads = [self.data_processor, self.time_plotter, self.psd_plotter, self.storage_thread]

        for thread in threads:
            thread.stop()

        for thread in threads:
            if thread.isRunning():
                thread.wait(3000)

        # 重置排空标志，以便下次启动时可再次使用
        self.storage_thread._drain_mode = False

        # 清空绘图数据
        self._clear_plots()
        self.storage_thread.end_run_cycle()

        self.logger.info("All Tab1 threads stopped")

    def _clear_plots(self):
        """清空所有绘图数据"""
        if QApplication.instance():
            if self.time_curve:
                QTimer.singleShot(0, lambda: self.time_curve.setData([], []))
            if self.psd_curve:
                QTimer.singleShot(0, lambda: self.psd_curve.setData([], []))

    def process_raw_packet(self, packet):
        """Receive raw packets from TCP and fan them out to worker threads."""
        success = self.data_processor.add_raw_packet(packet)
        self.storage_thread.add_raw_packet(packet)

        if packet.comm_count % 50 == 0:
            self.logger.info(f"Tab1ThreadManager received packet #{packet.comm_count}, queued: {success}")

        return success

    def update_fip_selection(
        self,
        sensor_count: int,
        selected_sensor: int,
        packet_duration_seconds: float = DEFAULT_FIP_PACKET_DURATION_SECONDS,
        sample_rate_hz: float = DEFAULT_FIP_SAMPLE_RATE_HZ,
    ):
        """Apply Tab1 FIP input and plot selection changes."""
        sensor_count = normalize_fip_sensor_count(sensor_count)
        selected_sensor = normalize_fip_sensor_index(selected_sensor, sensor_count)
        packet_duration_seconds = normalize_fip_packet_duration(packet_duration_seconds)
        sample_rate_hz = normalize_fip_sample_rate(sample_rate_hz)
        count_changed = sensor_count != self.fip_sensor_count
        selected_changed = selected_sensor != self.selected_fip_sensor
        duration_changed = abs(packet_duration_seconds - self.fip_packet_duration_seconds) > 1e-9
        sample_rate_changed = abs(sample_rate_hz - self.fip_sample_rate_hz) > 1e-6
        if not count_changed and not selected_changed and not duration_changed and not sample_rate_changed:
            return

        self.fip_sensor_count = sensor_count
        self.selected_fip_sensor = selected_sensor
        self.fip_packet_duration_seconds = packet_duration_seconds
        self.fip_sample_rate_hz = sample_rate_hz
        self.storage_thread.set_input_parameters(sample_rate_hz, packet_duration_seconds)
        if count_changed or duration_changed or sample_rate_changed:
            self.data_processor.reset_state(clear_queue=True)
        self.time_plotter._reset_stream_state()
        self.psd_plotter.reset_state(clear_queue=True)
        self._clear_plots()
        self.logger.info(
            "Tab1 FIP settings updated: sensor_count=%d selected=FIP%d duration=%.6fs sample_rate=%.1fHz",
            sensor_count,
            selected_sensor,
            packet_duration_seconds,
            sample_rate_hz,
        )

    def _distribute_processed_data(self, processed_data: ProcessedData):
        """Distribute processed data to plotting threads."""
        if processed_data.comm_count % 50 == 0:
            self.logger.info(f"Distributing processed packet #{processed_data.comm_count}")

        self.time_plotter.add_processed_data(processed_data)
        self.psd_plotter.add_processed_data(processed_data)

    def _update_time_plot(self, times, values):
        """更新时域绘图 - 线程安全的UI更新"""
        if not QApplication.instance():
            return

        self._ensure_plot_curves()

        # 调试：输出当前PlotWidget中的曲线项数量，便于定位重复曲线问题
        time_items_count = len(self.time_plot_widget.listDataItems()) if self.time_plot_widget else 0
        psd_items_count = len(self.psd_plot_widget.listDataItems()) if self.psd_plot_widget else 0
        self.logger.debug(
            f"PlotDataItems count - time: {time_items_count}, psd: {psd_items_count}"
        )

        if not self.time_curve:
            return

        try:
            self.time_curve.setData(times, values)
        except RuntimeError:
            # 曲线对象可能在外部clear()后失效，重建后重试一次
            self.time_curve = None
            self._ensure_plot_curves()
            if self.time_curve:
                self.time_curve.setData(times, values)
        except Exception as e:
            self.logger.error(f"Error updating time curve: {e}")

    def _update_psd_plot(self, frequencies, psd_db):
        """更新PSD绘图 - 线程安全的UI更新"""
        if not QApplication.instance():
            return

        self._ensure_plot_curves()
        if not self.psd_curve:
            return

        try:
            self.psd_curve.setData(frequencies, psd_db)
        except RuntimeError:
            # 曲线对象可能在外部clear()后失效，重建后重试一次
            self.psd_curve = None
            self._ensure_plot_curves()
            if self.psd_curve:
                self.psd_curve.setData(frequencies, psd_db)
        except Exception as e:
            self.logger.error(f"Error updating PSD curve: {e}")

    # 控制接口
    def toggle_time_plotting(self, enabled: bool):
        """控制时域绘图"""
        self.time_plotter.set_enabled(enabled)

        # 如果禁用绘图，清空现有曲线
        if not enabled and self.time_curve and QApplication.instance():
            QTimer.singleShot(0, lambda: self.time_curve.setData([], []))

    def toggle_psd_plotting(self, enabled: bool):
        """控制PSD绘图"""
        self.psd_plotter.set_enabled(enabled)

        # 如果禁用绘图，清空现有曲线
        if not enabled and self.psd_curve and QApplication.instance():
            QTimer.singleShot(0, lambda: self.psd_curve.setData([], []))

    def toggle_storage(self, enabled: bool):
        """控制数据存储"""
        self.storage_thread.set_enabled(enabled)

    def update_time_window(self, duration: float):
        """更新时域显示窗口"""
        self.time_plotter.set_window_duration(duration)

    def update_storage_path(self, path: str):
        """更新原始数据存储目录路径。

        Args:
            path: 新的存储目录绝对路径（如 "D:/PCCP/FIPdata"）。
        """
        self.storage_thread.set_storage_path(path)

    def update_storage_interval(self, interval_seconds: float):
        """更新存储分块时长（每个 npz 文件对应的秒数）。

        Args:
            interval_seconds: 新的分块时长（秒），最小 1 s。
        """
        self.storage_thread.set_storage_interval_seconds(interval_seconds)

    def get_thread_stats(self):
        """Return processing and storage thread statistics for diagnostics."""
        return {
            'processing': self.data_processor.get_stats(),
            'storage': self.storage_thread.get_stats(),
        }

    def get_plot_status(self):
        """获取绘图状态（调试用）"""
        return {
            'time_curve_exists': self.time_curve is not None,
            'psd_curve_exists': self.psd_curve is not None,
            'time_plotting_enabled': self.time_plotter.enabled if hasattr(self.time_plotter, 'enabled') else 'unknown',
            'psd_plotting_enabled': self.psd_plotter.enabled if hasattr(self.psd_plotter, 'enabled') else 'unknown'
        }
