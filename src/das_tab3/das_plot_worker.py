"""Plot preparation worker for Tab3 DAS displays."""

from __future__ import annotations

import logging
import time
from queue import Empty, Full, Queue
from typing import Dict, Optional, Tuple

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal
from scipy.signal import butter, sosfiltfilt

from .das_types import DASParsedPacket


class DASPlotWorker(QThread):
    """Transform parsed DAS packets into bounded, plot-ready payloads."""

    plot_payload_ready = pyqtSignal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(f"{__name__}.DASPlotWorker")
        self.input_queue: "Queue[DASParsedPacket]" = Queue(maxsize=4)
        self.running = False
        self.settings: Dict[str, object] = {
            "das_channel": 0,
            "curve1_das_channel": 10,
            "curve2_das_channel": 10,
            "display_seconds": 1.0,
            "time_downsample": 1,
            "space_downsample": 1,
            "channel_start": 0,
            "channel_end": 199,
            "low_hz": 1.0,
            "high_hz": 2000.0,
            "apply_filter": False,
            "curve1_low_hz": 1.0,
            "curve1_high_hz": 2000.0,
            "curve1_apply_filter": False,
            "curve2_low_hz": 1.0,
            "curve2_high_hz": 2000.0,
            "curve2_apply_filter": False,
            "curve_max_points": 20000,
            "space_time_max_pixels": 300000,
        }
        self._history: list[DASParsedPacket] = []
        self._space_time_buffer: Optional[np.ndarray] = None
        self._space_time_valid_cols = 0
        self._space_time_signature: Optional[Tuple[int, int, int, int, int, int]] = None
        self._process_times_ms: list[float] = []
        self._last_stats_time = time.monotonic()
        self._stats_packets_at_last_log = 0
        self.stats: Dict[str, int] = {
            "packets_enqueued": 0,
            "packets_processed": 0,
            "packets_dropped": 0,
            "queue_peak": 0,
            "slow_frames": 0,
        }

    def enqueue_packet(self, packet: DASParsedPacket) -> bool:
        """Queue one parsed packet without blocking the receiver path."""
        try:
            if self.input_queue.full():
                try:
                    dropped = self.input_queue.get_nowait()
                    self.stats["packets_dropped"] += 1
                    self.logger.warning(
                        "TAB3_NODE plot_worker.enqueue drop_oldest comm=%s queue_size=%d",
                        dropped.header.comm_count,
                        self.input_queue.qsize(),
                    )
                except Empty:
                    pass
            self.input_queue.put(packet, block=False)
            self.stats["packets_enqueued"] += 1
            self.stats["queue_peak"] = max(self.stats["queue_peak"], self.input_queue.qsize())
            self.logger.debug(
                "TAB3_NODE plot_worker.enqueue comm=%s queue_size=%d enqueued=%d dropped=%d",
                packet.header.comm_count,
                self.input_queue.qsize(),
                self.stats["packets_enqueued"],
                self.stats["packets_dropped"],
            )
            return True
        except Full:
            self.stats["packets_dropped"] += 1
            self.logger.warning(
                "TAB3_NODE plot_worker.enqueue full_reject comm=%s dropped=%d",
                packet.header.comm_count,
                self.stats["packets_dropped"],
            )
            return False

    def update_settings(self, settings: Dict[str, object]) -> None:
        old_signature = self._settings_signature(self.settings)
        self.settings.update(settings)
        if self._settings_signature(self.settings) != old_signature:
            self._reset_space_time_buffer()
        self.logger.debug("TAB3_NODE plot_worker.settings %s", self._settings_for_log())

    def reset_state(self) -> None:
        self._history.clear()
        self._reset_space_time_buffer()
        while not self.input_queue.empty():
            try:
                self.input_queue.get_nowait()
            except Empty:
                break
        self._process_times_ms.clear()
        self._last_stats_time = time.monotonic()
        self._stats_packets_at_last_log = 0
        for key in self.stats:
            self.stats[key] = 0
        self.logger.debug("TAB3_NODE plot_worker.reset")

    def run(self) -> None:
        self.running = True
        self.logger.info("DAS plot worker started")
        while self.running:
            try:
                packet = self.input_queue.get(timeout=0.2)
                self._process_packet(packet)
            except Empty:
                continue
            except Exception as exc:
                self.logger.error("DAS plot worker error: %s", exc)
        self.logger.info("DAS plot worker stopped, stats=%s", self.stats)

    def stop(self) -> None:
        self.running = False

    def _process_packet(self, packet: DASParsedPacket) -> None:
        started = time.perf_counter()
        self._history.append(packet)
        duration = max(0.2, float(self.settings.get("display_seconds", 1.0)))
        cutoff_time = packet.packet_end_time - duration
        self._history = [item for item in self._history if item.packet_end_time >= cutoff_time]

        legacy_curve_channel = int(self.settings.get("das_channel", 0))
        curve1_channel = int(self.settings.get("curve1_das_channel", legacy_curve_channel))
        curve2_channel = int(self.settings.get("curve2_das_channel", legacy_curve_channel))
        channel_start = int(self.settings.get("channel_start", 0))
        channel_end = int(self.settings.get("channel_end", max(0, packet.header.channel_count - 1)))
        time_downsample = max(1, int(self.settings.get("time_downsample", 1)))
        space_downsample = max(1, int(self.settings.get("space_downsample", 1)))

        curve1_filter = self._curve_filter_settings(1)
        curve2_filter = self._curve_filter_settings(2)
        das_times1, das_curve1 = self._build_curve_payload(
            packet,
            curve1_channel,
            time_downsample,
            curve1_filter,
        )
        if curve2_channel == curve1_channel and curve2_filter == curve1_filter:
            das_times2, das_curve2 = das_times1, das_curve1
        else:
            das_times2, das_curve2 = self._build_curve_payload(
                packet,
                curve2_channel,
                time_downsample,
                curve2_filter,
            )
        space_time_matrix, x_axis, y_axis = self._build_space_time_payload(
            packet=packet,
            channel_start=channel_start,
            channel_end=channel_end,
            time_downsample=time_downsample,
            space_downsample=space_downsample,
        )

        payload = {
            "curve1_das_time": das_times1,
            "curve1_das_values": das_curve1,
            "curve2_das_time": das_times2,
            "curve2_das_values": das_curve2,
            "das_curve_time": das_times2,
            "das_curve_values": das_curve2,
            "space_time_matrix": space_time_matrix,
            "space_time_x": x_axis,
            "space_time_y": y_axis,
            "header": {
                "comm_count": packet.header.comm_count,
                "sample_rate_hz": packet.header.sample_rate_hz,
                "channel_count": packet.header.channel_count,
                "data_bytes": packet.header.data_bytes,
                "packet_duration_seconds": packet.header.packet_duration_seconds,
            },
            "plot_stats": dict(self.stats),
        }
        self.plot_payload_ready.emit(payload)

        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.stats["packets_processed"] += 1
        self._process_times_ms.append(elapsed_ms)
        if len(self._process_times_ms) > 200:
            self._process_times_ms = self._process_times_ms[-200:]
        if elapsed_ms > 150.0:
            self.stats["slow_frames"] += 1
            self.logger.warning(
                "TAB3_NODE plot_worker.slow comm=%s elapsed_ms=%.2f curve_points=%d matrix_shape=%s queue_size=%d",
                packet.header.comm_count,
                elapsed_ms,
                max(len(das_curve1), len(das_curve2)),
                tuple(space_time_matrix.shape),
                self.input_queue.qsize(),
            )
        self.logger.debug(
            "TAB3_NODE plot_worker.payload comm=%s elapsed_ms=%.2f curve_points=%d matrix_shape=%s queue_size=%d processed=%d dropped=%d",
            packet.header.comm_count,
            elapsed_ms,
            max(len(das_curve1), len(das_curve2)),
            tuple(space_time_matrix.shape),
            self.input_queue.qsize(),
            self.stats["packets_processed"],
            self.stats["packets_dropped"],
        )
        self._log_performance_stats()

    def _build_curve_payload(
        self,
        packet: DASParsedPacket,
        curve_channel: int,
        time_downsample: int,
        filter_settings: Dict[str, object],
    ) -> tuple[np.ndarray, np.ndarray]:
        samples_by_packet = []
        total_points = 0
        for item in self._history:
            safe_channel = min(max(curve_channel, 0), item.header.channel_count - 1)
            samples = np.asarray(item.matrix[safe_channel])
            if samples.size == 0:
                continue
            samples_by_packet.append((item, samples))
            total_points += int(samples.size)
        if total_points <= 0 or not samples_by_packet:
            return np.array([], dtype=np.float64), np.array([], dtype=np.float32)

        sample_rate = float(packet.header.sample_rate_hz)
        curve_step = self._curve_display_step(total_points, sample_rate, time_downsample)
        if bool(filter_settings.get("apply_filter", False)):
            full_curve = np.concatenate([samples for _, samples in samples_by_packet])
            full_curve = self._maybe_filter(full_curve, sample_rate, filter_settings)
            first_packet = samples_by_packet[0][0]
            selected_indexes = np.arange(0, len(full_curve), curve_step, dtype=np.int64)
            das_times = first_packet.packet_start_time + selected_indexes.astype(np.float64) / max(sample_rate, 1.0)
            das_curve = full_curve[selected_indexes]
            return das_times, np.asarray(das_curve, dtype=np.float32)

        value_chunks = []
        time_chunks = []
        for item, samples in samples_by_packet:
            selected_indexes = np.arange(0, len(samples), curve_step, dtype=np.int64)
            value_chunks.append(samples[selected_indexes])
            item_sample_rate = max(float(item.header.sample_rate_hz), 1.0)
            time_chunks.append(
                item.packet_start_time + selected_indexes.astype(np.float64) / item_sample_rate
            )
        das_curve = np.concatenate(value_chunks) if value_chunks else np.array([], dtype=np.float32)
        das_times = np.concatenate(time_chunks) if time_chunks else np.array([], dtype=np.float64)
        return das_times, np.asarray(das_curve, dtype=np.float32)

    def _curve_display_step(self, point_count: int, sample_rate_hz: float, time_downsample: int) -> int:
        """Limit curve density without changing the packet stream or storage data."""
        if point_count <= 0:
            return 1
        configured_step = max(1, int(time_downsample))
        max_points = max(1000, int(self.settings.get("curve_max_points", 20000)))
        density_step = max(1, int(np.ceil(point_count / max_points)))
        return max(configured_step, density_step)

    def _build_space_time_payload(
        self,
        packet: DASParsedPacket,
        channel_start: int,
        channel_end: int,
        time_downsample: int,
        space_downsample: int,
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Append the latest packet into a fixed rolling display buffer."""
        latest_matrix = packet.matrix
        if latest_matrix.ndim != 2 or latest_matrix.size == 0:
            return np.empty((0, 0), dtype=np.float32), np.array([], dtype=np.float64), np.array([], dtype=np.int32)

        start = min(max(channel_start, 0), latest_matrix.shape[0] - 1)
        end = min(max(channel_end, start), latest_matrix.shape[0] - 1)
        row_count = len(range(start, end + 1, max(1, space_downsample)))
        if row_count <= 0:
            return np.empty((0, 0), dtype=np.float32), np.array([], dtype=np.float64), np.array([], dtype=np.int32)

        display_seconds = max(0.2, float(self.settings.get("display_seconds", 1.0)))
        sample_rate = max(float(packet.header.sample_rate_hz), 1.0)
        max_pixels = max(50000, int(self.settings.get("space_time_max_pixels", 300000)))
        effective_time_downsample = max(1, int(time_downsample))
        estimated_cols = max(1, int(np.ceil(display_seconds * sample_rate / effective_time_downsample)))
        if row_count * estimated_cols > max_pixels:
            extra_step = int(np.ceil((row_count * estimated_cols) / max_pixels))
            effective_time_downsample *= max(1, extra_step)
            estimated_cols = max(1, int(np.ceil(display_seconds * sample_rate / effective_time_downsample)))

        block = latest_matrix[start:end + 1:space_downsample, ::effective_time_downsample]
        if block.size == 0:
            return np.empty((0, 0), dtype=np.float32), np.array([], dtype=np.float64), np.array([], dtype=np.int32)
        block = np.ascontiguousarray(block, dtype=np.float32)
        row_count = int(block.shape[0])
        block_cols = int(block.shape[1])
        dt = effective_time_downsample / sample_rate
        max_cols_by_pixels = max(1, max_pixels // max(row_count, 1))
        max_cols_by_window = max(block_cols, int(np.ceil(display_seconds / max(dt, 1e-12))))
        max_cols = max(1, min(max_cols_by_window, max_cols_by_pixels))
        if block_cols > max_cols:
            block = block[:, -max_cols:]
            block_cols = int(block.shape[1])

        signature = (row_count, start, end, int(space_downsample), int(effective_time_downsample), int(max_cols))
        if self._space_time_signature != signature or self._space_time_buffer is None:
            self._space_time_buffer = np.zeros((row_count, max_cols), dtype=np.float32)
            self._space_time_valid_cols = 0
            self._space_time_signature = signature
            self.logger.debug(
                "TAB3_NODE plot_worker.space_buffer_reset rows=%d max_cols=%d dt=%.9f signature=%s",
                row_count,
                max_cols,
                dt,
                signature,
            )

        if self._space_time_buffer is None:
            return np.empty((0, 0), dtype=np.float32), np.array([], dtype=np.float64), np.array([], dtype=np.int32)

        if block_cols >= max_cols:
            self._space_time_buffer[:, :] = block[:, -max_cols:]
            self._space_time_valid_cols = max_cols
        else:
            if self._space_time_valid_cols + block_cols > max_cols:
                overflow = self._space_time_valid_cols + block_cols - max_cols
                remaining_cols = self._space_time_valid_cols - overflow
                if remaining_cols > 0:
                    self._space_time_buffer[:, :remaining_cols] = self._space_time_buffer[:, overflow:self._space_time_valid_cols]
                self._space_time_valid_cols = max(0, remaining_cols)
            insert_at = self._space_time_valid_cols
            self._space_time_buffer[:, insert_at:insert_at + block_cols] = block
            self._space_time_valid_cols += block_cols

        visible_cols = max(0, self._space_time_valid_cols)
        matrix = np.ascontiguousarray(self._space_time_buffer[:, :visible_cols])
        x_axis = np.arange(visible_cols, dtype=np.float64) * dt
        y_axis = np.arange(start, end + 1, space_downsample, dtype=np.int32)[:row_count]
        return matrix, x_axis, y_axis

    def _reset_space_time_buffer(self) -> None:
        self._space_time_buffer = None
        self._space_time_valid_cols = 0
        self._space_time_signature = None

    def _settings_signature(self, settings: Dict[str, object]) -> tuple:
        return (
            int(settings.get("channel_start", 0)),
            int(settings.get("channel_end", 0)),
            int(settings.get("time_downsample", 1)),
            int(settings.get("space_downsample", 1)),
            float(settings.get("display_seconds", 1.0)),
            int(settings.get("space_time_max_pixels", 300000)),
        )

    def _settings_for_log(self) -> Dict[str, object]:
        keys = [
            "das_channel",
            "curve1_das_channel",
            "curve2_das_channel",
            "display_seconds",
            "time_downsample",
            "space_downsample",
            "channel_start",
            "channel_end",
            "apply_filter",
            "curve1_apply_filter",
            "curve1_low_hz",
            "curve1_high_hz",
            "curve2_apply_filter",
            "curve2_low_hz",
            "curve2_high_hz",
            "curve_max_points",
            "space_time_max_pixels",
        ]
        return {key: self.settings.get(key) for key in keys}

    def _curve_filter_settings(self, curve_index: int) -> Dict[str, object]:
        """Resolve per-curve DAS filter controls with legacy setting fallback."""
        return {
            "apply_filter": bool(
                self.settings.get(
                    f"curve{curve_index}_apply_filter",
                    self.settings.get("apply_filter", False),
                )
            ),
            "low_hz": float(
                self.settings.get(
                    f"curve{curve_index}_low_hz",
                    self.settings.get("low_hz", 1.0),
                )
            ),
            "high_hz": float(
                self.settings.get(
                    f"curve{curve_index}_high_hz",
                    self.settings.get("high_hz", 2000.0),
                )
            ),
        }

    def _maybe_filter(self, data: np.ndarray, sample_rate_hz: float, filter_settings: Dict[str, object]) -> np.ndarray:
        if len(data) == 0 or not bool(filter_settings.get("apply_filter", False)):
            return data
        low_hz = float(filter_settings.get("low_hz", 1.0))
        high_hz = float(filter_settings.get("high_hz", sample_rate_hz * 0.45))
        nyquist = sample_rate_hz * 0.5
        low_hz = max(0.1, min(low_hz, nyquist * 0.95))
        high_hz = max(low_hz + 0.1, min(high_hz, nyquist * 0.98))
        try:
            sos = butter(4, [low_hz, high_hz], btype="bandpass", fs=sample_rate_hz, output="sos")
            return sosfiltfilt(sos, data)
        except ValueError:
            return data

    def _log_performance_stats(self) -> None:
        processed = self.stats["packets_processed"]
        if processed <= 0 or processed % 50 != 0 or processed == self._stats_packets_at_last_log:
            return
        now = time.monotonic()
        elapsed = max(now - self._last_stats_time, 1e-9)
        interval_packets = processed - self._stats_packets_at_last_log
        avg_ms = float(np.mean(self._process_times_ms)) if self._process_times_ms else 0.0
        max_ms = float(np.max(self._process_times_ms)) if self._process_times_ms else 0.0
        self.logger.info(
            "TAB3_NODE plot_worker.stats processed=%d interval_packets=%d rate=%.2f pkt/s avg_ms=%.2f max_ms=%.2f queue_peak=%d dropped=%d slow=%d",
            processed,
            interval_packets,
            interval_packets / elapsed,
            avg_ms,
            max_ms,
            self.stats["queue_peak"],
            self.stats["packets_dropped"],
            self.stats["slow_frames"],
        )
        self._process_times_ms.clear()
        self._last_stats_time = now
        self._stats_packets_at_last_log = processed
