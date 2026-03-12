"""Plot worker for the independent FIP Tab2 pipeline."""

from __future__ import annotations

import logging
from collections import deque
from queue import Empty, Full, Queue
from typing import Deque, Dict, Tuple

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal

from .fip_types import FIPFeatureFrame, FIPWindowDetectionResult


class FIPFeaturePlotWorker(QThread):
    """Maintain short-time feature history for UI plotting."""

    plot_payload_ready = pyqtSignal(dict)

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(f"{__name__}.FIPFeaturePlotWorker")
        self.feature_queue: "Queue[FIPFeatureFrame]" = Queue(maxsize=128)
        self.detection_queue: "Queue[FIPWindowDetectionResult]" = Queue(maxsize=128)
        self.running = False

        self.plot_enabled = {
            "short_energy": True,
            "zero_crossing": False,
            "peak_factor": False,
            "rms": False,
        }
        self.display_duration_seconds = 60.0

        self.series: Dict[str, Deque[Tuple[float, float]]] = {
            "short_energy": deque(),
            "zero_crossing": deque(),
            "peak_factor": deque(),
            "rms": deque(),
        }
        self.latest_thresholds: Dict[str, float] = {}

    def enqueue_feature_frame(self, frame: FIPFeatureFrame) -> bool:
        """Queue one feature frame for plotting."""
        return self._enqueue(self.feature_queue, frame)

    def enqueue_detection_result(self, result: FIPWindowDetectionResult) -> bool:
        """Queue one detection result for threshold-line updates."""
        return self._enqueue(self.detection_queue, result)

    def update_plot_enabled(self, enabled: Dict[str, bool]) -> None:
        """Update which features should be plotted."""
        self.plot_enabled.update(enabled)

    def update_display_settings(self, settings: Dict[str, float]) -> None:
        """Update the visible time span of short-time feature curves."""
        self.display_duration_seconds = max(10.0, float(settings.get("duration_seconds", self.display_duration_seconds)))

    def reset_state(self) -> None:
        """Clear cached plot samples and thresholds."""
        for series in self.series.values():
            series.clear()
        self.latest_thresholds.clear()

    def run(self) -> None:
        """Consume feature frames and emit UI-ready plot payloads."""
        self.running = True
        self.logger.info("FIP plot worker started")
        while self.running:
            handled = False
            try:
                result = self.detection_queue.get(timeout=0.1)
                self.latest_thresholds.update(result.thresholds)
                handled = True
            except Empty:
                pass

            try:
                frame = self.feature_queue.get(timeout=0.1)
                self._process_frame(frame)
                handled = True
            except Empty:
                if not handled:
                    continue
            except Exception as exc:  # pragma: no cover
                self.logger.error("Plot worker error: %s", exc)

    def stop(self) -> None:
        """Stop the worker loop."""
        self.running = False

    def _process_frame(self, frame: FIPFeatureFrame) -> None:
        for feature_name, value in frame.feature_values.items():
            self.series[feature_name].append((frame.center_time, value))
            self._trim_series(self.series[feature_name], frame.center_time)

        payload = {}
        selected_names = [name for name, enabled in self.plot_enabled.items() if enabled][:4]
        for feature_name in selected_names:
            points = list(self.series[feature_name])
            if not points:
                payload[feature_name] = {
                    "times": np.array([], dtype=np.float64),
                    "values": np.array([], dtype=np.float64),
                    "threshold": self.latest_thresholds.get(feature_name, 0.0),
                }
                continue

            times = np.array([time_point for time_point, _ in points], dtype=np.float64)
            times = times - times[0]
            values = np.array([value for _, value in points], dtype=np.float64)
            payload[feature_name] = {
                "times": times,
                "values": values,
                "threshold": self.latest_thresholds.get(feature_name, 0.0),
            }

        self.plot_payload_ready.emit(payload)

    def _trim_series(self, feature_series: Deque[Tuple[float, float]], latest_time: float) -> None:
        cutoff = latest_time - self.display_duration_seconds
        while feature_series and feature_series[0][0] < cutoff:
            feature_series.popleft()

    @staticmethod
    def _enqueue(queue_obj: Queue, item: object) -> bool:
        try:
            if queue_obj.full():
                try:
                    queue_obj.get_nowait()
                except Empty:
                    pass
            queue_obj.put(item, block=False)
            return True
        except Full:
            return False
