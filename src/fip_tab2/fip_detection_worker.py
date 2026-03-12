"""Detection worker for the independent FIP Tab2 pipeline."""

from __future__ import annotations

import logging
from collections import deque
from queue import Empty, Full, Queue
from typing import Deque, Dict, List

from PyQt5.QtCore import QThread, pyqtSignal

from .fip_types import FIPAlarmEvent, FIPFeatureFrame, FIPTriggerSaveRequest, FIPWindowDetectionResult


class FIPDetectionWorker(QThread):
    """Run per-feature thresholding and aggregate abnormal windows."""

    alarm_event_ready = pyqtSignal(object)
    window_detection_ready = pyqtSignal(object)
    baselines_updated = pyqtSignal(dict)
    trigger_save_requested = pyqtSignal(object)

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(f"{__name__}.FIPDetectionWorker")
        self.input_queue: "Queue[FIPFeatureFrame]" = Queue(maxsize=64)
        self.running = False

        self.threshold_factors = {
            "short_energy": 3.0,
            "zero_crossing": 3.0,
            "peak_factor": 3.0,
            "rms": 3.0,
        }
        self.baseline_window_points = 100
        self.baseline_histories: Dict[str, Deque[float]] = {
            "short_energy": deque(maxlen=self.baseline_window_points),
            "zero_crossing": deque(maxlen=self.baseline_window_points),
            "peak_factor": deque(maxlen=self.baseline_window_points),
            "rms": deque(maxlen=self.baseline_window_points),
        }
        self.current_baselines = {
            "short_energy": 0.0,
            "zero_crossing": 0.0,
            "peak_factor": 0.0,
            "rms": 0.0,
        }

        self.trigger_storage_enabled = True
        self.pre_trigger_seconds = 1.0
        self.post_trigger_seconds = 3.0
        self.enabled_feature_names: List[str] = ["short_energy"]

        self._event_counter = 0
        self._active_window_results: List[FIPWindowDetectionResult] = []

    def enqueue_frame(self, frame: FIPFeatureFrame) -> bool:
        """Add a feature frame to the detection queue."""
        try:
            if self.input_queue.full():
                try:
                    self.input_queue.get_nowait()
                except Empty:
                    pass
            self.input_queue.put(frame, block=False)
            return True
        except Full:
            return False

    def update_threshold_factors(self, factors: Dict[str, float]) -> None:
        """Update per-feature threshold multipliers."""
        self.threshold_factors.update(factors)

    def update_storage_settings(self, settings: Dict[str, float]) -> None:
        """Update trigger storage settings used when alarms are emitted."""
        self.trigger_storage_enabled = bool(settings.get("enabled", self.trigger_storage_enabled))
        self.pre_trigger_seconds = float(settings.get("pre_trigger_seconds", self.pre_trigger_seconds))
        self.post_trigger_seconds = float(settings.get("post_trigger_seconds", self.post_trigger_seconds))

    def update_enabled_feature_names(self, enabled_names: List[str]) -> None:
        """Remember which features should be saved during trigger storage."""
        self.enabled_feature_names = list(enabled_names)

    def reset_state(self) -> None:
        """Reset internal histories and active event state."""
        self._event_counter = 0
        self._active_window_results.clear()
        for history in self.baseline_histories.values():
            history.clear()
        for key in self.current_baselines:
            self.current_baselines[key] = 0.0

    def run(self) -> None:
        """Consume feature frames and emit alarm events."""
        self.running = True
        self.logger.info("FIP detection worker started")
        while self.running:
            try:
                frame = self.input_queue.get(timeout=0.2)
                self._process_frame(frame)
            except Empty:
                continue
            except Exception as exc:  # pragma: no cover
                self.logger.error("Detection worker error: %s", exc)

    def stop(self) -> None:
        """Stop the worker loop."""
        self.running = False

    def flush_active_event(self) -> None:
        """Flush a pending alarm before application shutdown."""
        if self._active_window_results:
            self._finalize_active_event()

    def _process_frame(self, frame: FIPFeatureFrame) -> None:
        baselines: Dict[str, float] = {}
        thresholds: Dict[str, float] = {}
        triggered_features: List[str] = []

        for feature_name, value in frame.feature_values.items():
            history = self.baseline_histories[feature_name]
            baseline = self.current_baselines.get(feature_name, 0.0)
            factor = max(1.0, self.threshold_factors.get(feature_name, 3.0))
            threshold = factor if baseline <= 1e-12 else baseline * factor

            if value > threshold:
                triggered_features.append(feature_name)
            else:
                history.append(value)
                if history:
                    baseline = float(sum(history) / len(history))
                    self.current_baselines[feature_name] = baseline
                    threshold = factor if baseline <= 1e-12 else baseline * factor

            baselines[feature_name] = self.current_baselines.get(feature_name, baseline)
            thresholds[feature_name] = threshold

        self.baselines_updated.emit(self.current_baselines.copy())

        result = FIPWindowDetectionResult(
            frame=frame,
            triggered_features=triggered_features,
            thresholds=thresholds,
            baselines=baselines,
        )
        self.window_detection_ready.emit(result)

        if triggered_features:
            self._active_window_results.append(result)
        elif self._active_window_results:
            self._finalize_active_event()

    def _finalize_active_event(self) -> None:
        window_results = list(self._active_window_results)
        self._active_window_results.clear()
        if not window_results:
            return

        self._event_counter += 1
        unique_features = sorted({name for result in window_results for name in result.triggered_features})
        event = FIPAlarmEvent(
            event_id=self._event_counter,
            start_time=window_results[0].frame.start_time,
            end_time=window_results[-1].frame.end_time,
            duration=window_results[-1].frame.end_time - window_results[0].frame.start_time,
            trigger_feature_names=unique_features,
            trigger_feature_count=len(unique_features),
            first_window_index=window_results[0].frame.window_index,
            last_window_index=window_results[-1].frame.window_index,
            window_results=window_results,
        )
        self.alarm_event_ready.emit(event)

        if self.trigger_storage_enabled:
            request = FIPTriggerSaveRequest(
                event=event,
                pre_trigger_seconds=self.pre_trigger_seconds,
                post_trigger_seconds=self.post_trigger_seconds,
                enabled_feature_names=list(self.enabled_feature_names),
            )
            self.trigger_save_requested.emit(request)
