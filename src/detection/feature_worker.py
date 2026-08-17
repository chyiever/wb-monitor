"""Feature extraction worker for the independent FIP Tab2 pipeline."""

from __future__ import annotations

import logging
from queue import Empty, Full, Queue
from typing import Dict

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal
from scipy.signal import butter, sosfilt, sosfilt_zi

from .types import FIPFeatureFrame, FIPTab2InputPacket


class FIPFeatureWorker(QThread):
    """Compute sliding-window features from Tab1 downsampled data."""

    feature_frame_ready = pyqtSignal(object)
    packet_filtered = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(f"{__name__}.FIPFeatureWorker")
        self.input_queue: "Queue[FIPTab2InputPacket]" = Queue(maxsize=32)
        self.running = False

        self.sample_rate = 200000.0
        self.packet_duration_seconds = 1.0
        self.filter_enabled = True
        self.filter_low_hz = 100.0
        self.filter_high_hz = 10000.0
        self.filter_order = 4

        self.window_seconds = 0.2
        self.overlap_ratio = 0.5
        self.compute_enabled = {
            "short_energy": True,
            "zero_crossing": False,
            "peak_factor": False,
            "rms": False,
        }

        self._stream_time_origin = 0.0
        self._stream_sample_index = 0
        self._window_index = 0
        self._signal_buffer = np.array([], dtype=np.float64)
        self._buffer_start_sample_index = 0
        self._next_window_start_index = 0
        # 上一包的 comm_count，用于检测缺包缺口（T2-03）
        self._last_comm_count: int | None = None
        self._sos = None
        self._zi = None
        self._rebuild_filter_state(reset_buffers=False)

    def enqueue_packet(self, packet: FIPTab2InputPacket) -> bool:
        """Add a packet for feature processing."""
        try:
            if self.input_queue.full():
                try:
                    self.input_queue.get_nowait()
                except Empty:
                    pass
            self.input_queue.put(packet, block=False)
            return True
        except Full:
            return False

    def update_compute_enabled(self, enabled: Dict[str, bool]) -> None:
        """Update which features should be computed."""
        self.compute_enabled.update(enabled)

    def update_window_settings(self, settings: Dict[str, float]) -> None:
        """Update sliding-window settings."""
        self.window_seconds = max(0.02, float(settings.get("window_seconds", self.window_seconds)))
        self.overlap_ratio = min(0.95, max(0.0, float(settings.get("overlap_ratio", self.overlap_ratio))))

    def update_preprocess_settings(self, settings: Dict[str, float]) -> None:
        """Update Tab2-specific band-pass parameters."""
        self.filter_enabled = bool(settings.get("enabled", self.filter_enabled))
        self.filter_low_hz = float(settings.get("low_hz", self.filter_low_hz))
        self.filter_high_hz = float(settings.get("high_hz", self.filter_high_hz))
        self.filter_order = int(settings.get("order", self.filter_order))
        self._rebuild_filter_state(reset_buffers=False)

    def reset_state(self) -> None:
        """清除窗口状态，在启停或采样率变化后调用。"""
        self._stream_time_origin = 0.0
        self._stream_sample_index = 0
        self._window_index = 0
        self._signal_buffer = np.array([], dtype=np.float64)
        self._buffer_start_sample_index = 0
        self._next_window_start_index = 0
        # 重置缺口检测状态
        self._last_comm_count = None
        self._rebuild_filter_state(reset_buffers=False)

    def run(self) -> None:
        """Consume packets and emit feature frames."""
        self.running = True
        self.logger.info("FIP feature worker started")
        while self.running:
            try:
                packet = self.input_queue.get(timeout=0.2)
                self._process_packet(packet)
            except Empty:
                continue
            except Exception as exc:  # pragma: no cover
                self.logger.error("Feature worker error: %s", exc)

    def stop(self) -> None:
        """Stop the worker loop."""
        self.running = False

    def _process_packet(self, packet: FIPTab2InputPacket) -> None:
        """处理单个数据包：检测缺口、带通滤波、划分特征窗口。

        Args:
            packet: 来自 Tab1 数据处理线程的降采样数据包。

        缺口处理策略（T2-03）：
            当检测到 comm_count 不连续时（当前包号 != 上一包号+1），
            说明中间有数据包丢失。此时：
            1. 重置信号缓冲区，避免将缺口两侧数据拼入同一滑动窗口；
            2. 将流时间原点推进到缺口结束后的正确位置，
               防止后续特征帧的时间戳出现系统偏移。
        """
        packet_rate = max(float(packet.sample_rate), 1.0)
        packet_duration = max(float(getattr(packet, "packet_duration_seconds", self.packet_duration_seconds)), 1e-6)
        if (
            abs(packet_rate - self.sample_rate) > 1e-6
            or abs(packet_duration - self.packet_duration_seconds) > 1e-9
        ):
            # 采样率或包时长变化时需要重建滤波器和滑窗状态
            self.sample_rate = packet_rate
            self.packet_duration_seconds = packet_duration
            self.reset_state()

        if self._last_comm_count is None and self._stream_sample_index == 0:
            self._stream_time_origin = packet.comm_count * self.packet_duration_seconds

        # --- 缺口检测（T2-03）---
        if self._last_comm_count is not None and packet.comm_count != self._last_comm_count + 1:
            gap = packet.comm_count - self._last_comm_count - 1
            self.logger.warning(
                "comm_count gap detected in Tab2: last=%d, current=%d, missing=%d packets. "
                "Resetting signal buffer to prevent cross-gap false windows.",
                self._last_comm_count,
                packet.comm_count,
                gap,
            )
            # 将时间原点推进到当前包应有的理论起始时间
            self._stream_time_origin = packet.comm_count * self.packet_duration_seconds
            self._stream_sample_index = 0
            self._signal_buffer = np.array([], dtype=np.float64)
            self._buffer_start_sample_index = 0
            self._next_window_start_index = 0
            self._zi = None  # 重置滤波器状态，避免缺口两侧滤波器瞬态相互污染

        self._last_comm_count = packet.comm_count

        filtered = self._apply_bandpass(packet.data.astype(np.float64, copy=False))

        # Tab2 uses a sample-driven relative timeline.
        # The filtered packet and the later feature windows must share the same time base.
        packet_start_time = self._stream_time_origin + (self._stream_sample_index / self.sample_rate)
        self.packet_filtered.emit(
            FIPTab2InputPacket(
                timestamp=packet_start_time,
                comm_count=packet.comm_count,
                sample_rate=self.sample_rate,
                data=filtered,
                packet_duration_seconds=self.packet_duration_seconds,
            )
        )

        self._signal_buffer = np.concatenate((self._signal_buffer, filtered))
        self._emit_available_windows()
        self._trim_signal_buffer()
        self._stream_sample_index += len(filtered)

    def _emit_available_windows(self) -> None:
        window_samples = max(1, int(round(self.window_seconds * self.sample_rate)))
        hop_samples = max(1, int(round(window_samples * (1.0 - self.overlap_ratio))))
        available_end = self._buffer_start_sample_index + len(self._signal_buffer)

        while self._next_window_start_index + window_samples <= available_end:
            local_start = self._next_window_start_index - self._buffer_start_sample_index
            local_end = local_start + window_samples
            window = self._signal_buffer[local_start:local_end]
            feature_values = self._compute_feature_values(window)
            if feature_values:
                start_time = self._stream_time_origin + self._window_index * (hop_samples / self.sample_rate)
                frame = FIPFeatureFrame(
                    window_index=self._window_index,
                    start_time=start_time,
                    center_time=start_time + (window_samples / self.sample_rate) / 2.0,
                    end_time=start_time + (window_samples / self.sample_rate),
                    feature_values=feature_values,
                    sample_rate=self.sample_rate,
                    window_size_seconds=window_samples / self.sample_rate,
                    hop_seconds=hop_samples / self.sample_rate,
                )
                self.feature_frame_ready.emit(frame)

            self._next_window_start_index += hop_samples
            self._window_index += 1

    def _trim_signal_buffer(self) -> None:
        window_samples = max(1, int(round(self.window_seconds * self.sample_rate)))
        keep_from = max(self._buffer_start_sample_index, self._next_window_start_index - window_samples)
        trim = keep_from - self._buffer_start_sample_index
        if trim > 0:
            self._signal_buffer = self._signal_buffer[trim:]
            self._buffer_start_sample_index = keep_from

    def _apply_bandpass(self, data: np.ndarray) -> np.ndarray:
        if not self.filter_enabled or self._sos is None:
            return data.copy()

        if self._zi is None:
            self._rebuild_filter_state(reset_buffers=False)

        filtered, self._zi = sosfilt(self._sos, data, zi=self._zi)
        return filtered

    def _rebuild_filter_state(self, reset_buffers: bool = True) -> None:
        nyquist = max(self.sample_rate * 0.5, 1.0)
        low = max(0.1, min(self.filter_low_hz, nyquist * 0.95))
        high = max(low + 0.1, min(self.filter_high_hz, nyquist * 0.98))

        try:
            if self.filter_enabled and low < high:
                self._sos = butter(
                    self.filter_order,
                    [low, high],
                    btype="bandpass",
                    fs=self.sample_rate,
                    output="sos",
                )
                self._zi = sosfilt_zi(self._sos) * 0.0
            else:
                self._sos = None
                self._zi = None
        except ValueError:
            self._sos = None
            self._zi = None

        if reset_buffers:
            self.reset_state()

    def _compute_feature_values(self, window: np.ndarray) -> Dict[str, float]:
        values: Dict[str, float] = {}

        if self.compute_enabled.get("short_energy", False):
            values["short_energy"] = float(np.mean(window ** 2))

        if self.compute_enabled.get("zero_crossing", False):
            denominator = max(len(window) - 1, 1)
            values["zero_crossing"] = float(np.sum(np.diff(np.signbit(window)) != 0) / denominator)

        if self.compute_enabled.get("peak_factor", False):
            rms = np.sqrt(np.mean(window ** 2))
            peak = np.max(np.abs(window))
            values["peak_factor"] = float(peak / rms) if rms > 1e-12 else 0.0

        if self.compute_enabled.get("rms", False):
            values["rms"] = float(np.sqrt(np.mean(window ** 2)))

        return values
