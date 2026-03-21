"""Plot preparation worker for Tab3 DAS displays."""

from __future__ import annotations

import logging
from queue import Empty, Full, Queue
from typing import Dict

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal
from scipy.signal import butter, sosfiltfilt

from .das_types import DASParsedPacket


class DASPlotWorker(QThread):
    """Transform parsed DAS packets into plot-ready payloads."""

    plot_payload_ready = pyqtSignal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(f"{__name__}.DASPlotWorker")
        self.input_queue: "Queue[DASParsedPacket]" = Queue(maxsize=8)
        self.running = False
        self.settings: Dict[str, object] = {
            "das_channel": 0,
            "display_seconds": 1.0,
            "time_downsample": 1,
            "space_downsample": 1,
            "channel_start": 0,
            "channel_end": 199,
            "low_hz": 1.0,
            "high_hz": 2000.0,
            "apply_filter": False,
        }
        self._history: list[DASParsedPacket] = []

    def enqueue_packet(self, packet: DASParsedPacket) -> bool:
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

    def update_settings(self, settings: Dict[str, object]) -> None:
        self.settings.update(settings)

    def reset_state(self) -> None:
        self._history.clear()

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

    def stop(self) -> None:
        self.running = False

    def _process_packet(self, packet: DASParsedPacket) -> None:
        self._history.append(packet)
        duration = max(0.2, float(self.settings.get("display_seconds", 1.0)))
        cutoff_time = packet.packet_end_time - duration
        self._history = [item for item in self._history if item.packet_end_time >= cutoff_time]

        curve_channel = int(self.settings.get("das_channel", 0))
        channel_start = int(self.settings.get("channel_start", 0))
        channel_end = int(self.settings.get("channel_end", max(0, packet.header.channel_count - 1)))
        time_downsample = max(1, int(self.settings.get("time_downsample", 1)))
        space_downsample = max(1, int(self.settings.get("space_downsample", 1)))

        curve_values = []
        curve_times = []
        for item in self._history:
            safe_channel = min(max(curve_channel, 0), item.header.channel_count - 1)
            samples = item.matrix[safe_channel]
            sample_rate = float(item.header.sample_rate_hz)
            times = item.packet_start_time + np.arange(len(samples), dtype=np.float64) / sample_rate
            curve_values.append(samples)
            curve_times.append(times)

        das_curve = np.concatenate(curve_values) if curve_values else np.array([], dtype=np.float64)
        das_times = np.concatenate(curve_times) if curve_times else np.array([], dtype=np.float64)
        das_curve = self._maybe_filter(das_curve, packet.header.sample_rate_hz)
        if time_downsample > 1:
            das_curve = das_curve[::time_downsample]
            das_times = das_times[::time_downsample]

        latest_matrix = packet.matrix
        start = min(max(channel_start, 0), latest_matrix.shape[0] - 1)
        end = min(max(channel_end, start), latest_matrix.shape[0] - 1)
        sliced = latest_matrix[start:end + 1:space_downsample, ::time_downsample]
        x_axis = packet.packet_start_time + np.arange(sliced.shape[1], dtype=np.float64) * time_downsample / packet.header.sample_rate_hz
        y_axis = np.arange(start, end + 1, space_downsample, dtype=np.int32)

        payload = {
            "das_curve_time": das_times,
            "das_curve_values": das_curve,
            "space_time_matrix": sliced,
            "space_time_x": x_axis,
            "space_time_y": y_axis,
            "header": {
                "comm_count": packet.header.comm_count,
                "sample_rate_hz": packet.header.sample_rate_hz,
                "channel_count": packet.header.channel_count,
                "data_bytes": packet.header.data_bytes,
                "packet_duration_seconds": packet.header.packet_duration_seconds,
            },
        }
        self.plot_payload_ready.emit(payload)

    def _maybe_filter(self, data: np.ndarray, sample_rate_hz: float) -> np.ndarray:
        if len(data) == 0 or not bool(self.settings.get("apply_filter", False)):
            return data
        low_hz = float(self.settings.get("low_hz", 1.0))
        high_hz = float(self.settings.get("high_hz", sample_rate_hz * 0.45))
        nyquist = sample_rate_hz * 0.5
        low_hz = max(0.1, min(low_hz, nyquist * 0.95))
        high_hz = max(low_hz + 0.1, min(high_hz, nyquist * 0.98))
        try:
            sos = butter(4, [low_hz, high_hz], btype="bandpass", fs=sample_rate_hz, output="sos")
            return sosfiltfilt(sos, data)
        except ValueError:
            return data
