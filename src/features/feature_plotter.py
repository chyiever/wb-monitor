"""
Feature Calculation and Plotting Module for Tab2

This module handles all feature calculation and visualization for the PCCP monitoring system.
Completely separated from the main waveform plotting (Tab1) to ensure performance independence.

Features:
- Real-time feature calculation from filtered waveform data
- Feature curve visualization for Tab2
- Independent threading and timing
- Configurable feature assignment to plot slots

Author: Claude
Date: 2026-03-12
"""

import time
import logging
import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from collections import deque
import threading

import pyqtgraph as pg
from PyQt5.QtCore import QObject, QTimer, pyqtSignal
from PyQt5.QtWidgets import QWidget

from .feature_calculator import FeatureCalculator
from ..detection.threshold_detector import ThresholdDetector


class FeatureDataBuffer:
    """Thread-safe buffer for feature data storage."""

    def __init__(self, max_points: int = 50000):
        self.max_points = max_points
        self._lock = threading.RLock()
        self._timestamps = deque(maxlen=max_points)
        self._values = deque(maxlen=max_points)

    def append(self, value: float, timestamp: Optional[float] = None) -> None:
        if timestamp is None:
            timestamp = time.time()

        with self._lock:
            self._timestamps.append(timestamp)
            self._values.append(value)

    def get_latest_window(self, window_duration: float) -> Tuple[np.ndarray, np.ndarray]:
        with self._lock:
            if not self._timestamps:
                return np.array([]), np.array([])

            cutoff_time = self._timestamps[-1] - window_duration
            timestamps = []
            values = []

            for t, v in zip(self._timestamps, self._values):
                if t >= cutoff_time:
                    timestamps.append(t)
                    values.append(v)

            return np.array(timestamps), np.array(values)

    def clear(self) -> None:
        with self._lock:
            self._timestamps.clear()
            self._values.clear()

    def size(self) -> int:
        with self._lock:
            return len(self._timestamps)


class FeaturePlotter(QObject):
    """Feature calculation and plotting manager for Tab2."""

    # Signals
    features_updated = pyqtSignal(dict)  # Feature values
    baselines_updated = pyqtSignal(dict)  # Baseline values
    detections_occurred = pyqtSignal(list)  # Detection results
    plot_updated = pyqtSignal(str)  # Plot name

    def __init__(self, sample_rate: float = 1000000.0):
        super().__init__()

        self.sample_rate = sample_rate
        self.logger = logging.getLogger(__name__ + '.FeaturePlotter')

        # Feature calculation components
        self.feature_calculator = FeatureCalculator(
            sample_rate=sample_rate,
            window_size_ms=50.0,  # 50ms window
            overlap_ratio=0.5
        )
        self.threshold_detector = ThresholdDetector()

        # Feature data buffers
        self.feature_buffers: Dict[str, FeatureDataBuffer] = {}

        # Display settings - 独立于Tab1的显示参数
        self.feature_display_window = 10.0  # seconds
        self.feature_max_display_points = 5000  # 较少点数，特征数据更稀疏
        self.update_interval = 100  # ms - 较慢的更新频率

        # Plot widgets and curves for Tab2
        self.feature_plots: List[pg.PlotWidget] = []
        self.feature_curves: Dict[str, pg.PlotCurveItem] = {}

        # Feature assignment for Tab2 plots
        self.feature_assignment = ['', '', '']  # Three slots for features

        # Update timer - 独立的定时器
        self.update_timer = QTimer()
        self.update_timer.timeout.connect(self._update_feature_plots)

        # Processing control
        self._processing_enabled = False
        self.process_every_n_packets = 3  # 每3个包处理一次以减少负载

    def set_plot_widgets(self, feature_plots: List[pg.PlotWidget]):
        """
        Set the feature plot widget references for Tab2.

        Args:
            feature_plots: List of feature plot widgets for Tab2
        """
        self.feature_plots = feature_plots

    def start_processing(self):
        """Start feature processing and plotting."""
        if not self.update_timer.isActive():
            self.update_timer.start(self.update_interval)
            self._processing_enabled = True
            self.logger.info(f"Started feature processing with {self.update_interval}ms interval")

    def stop_processing(self):
        """Stop feature processing and plotting."""
        if self.update_timer.isActive():
            self.update_timer.stop()
            self._processing_enabled = False
            self.logger.info("Stopped feature processing")

    def process_packet_data(self, filtered_data: np.ndarray, packet_count: int) -> None:
        """
        Process filtered waveform data for feature calculation.

        Args:
            filtered_data: Filtered waveform data array
            packet_count: Packet sequence number
        """
        try:
            if not self._processing_enabled:
                return

            # Process every N packets to reduce computational load
            if packet_count % self.process_every_n_packets != 0:
                return

            # Calculate features
            features = self.feature_calculator.process_data(filtered_data)
            if features:
                current_time = time.time()

                # Add feature data to buffers
                for feature_name, value in features.items():
                    if feature_name not in self.feature_buffers:
                        self.feature_buffers[feature_name] = FeatureDataBuffer()
                    self.feature_buffers[feature_name].append(value, current_time)

                # Emit features signal
                self.features_updated.emit(features)

                # Perform detection
                detections = self.threshold_detector.detect_events(features)
                if detections:
                    self.detections_occurred.emit(detections)

                # Update baselines periodically
                if packet_count % 100 == 0:  # Every 100 packets
                    baselines = self.feature_calculator.update_baselines()
                    self.threshold_detector.set_baselines(baselines)
                    self.baselines_updated.emit(baselines)

        except Exception as e:
            self.logger.error(f"Error processing packet data: {e}")

    def set_feature_assignment(self, slot_index: int, feature_name: str):
        """
        Assign a feature to a specific plot slot in Tab2.

        Args:
            slot_index: Plot slot index (0, 1, 2)
            feature_name: Feature name to assign
        """
        if 0 <= slot_index < len(self.feature_assignment):
            self.feature_assignment[slot_index] = feature_name

            # Update plot title
            if slot_index < len(self.feature_plots):
                if feature_name:
                    self.feature_plots[slot_index].setTitle(f"短时特征曲线{slot_index+1}: {feature_name}")
                else:
                    self.feature_plots[slot_index].setTitle(f"短时特征曲线{slot_index+1}")

    def set_display_settings(self, window_duration: float = None, max_points: int = None):
        """
        Set feature display settings (independent from Tab1).

        Args:
            window_duration: Display window duration for features in seconds
            max_points: Maximum points to display for features
        """
        if window_duration is not None:
            self.feature_display_window = window_duration

        if max_points is not None:
            self.feature_max_display_points = max_points

    def clear_all_data(self):
        """Clear all feature data buffers and plots."""
        try:
            for buffer in self.feature_buffers.values():
                buffer.clear()

            for curve in self.feature_curves.values():
                curve.setData([], [])

            for plot in self.feature_plots:
                plot.clear()

            self.logger.info("Cleared all feature data")

        except Exception as e:
            self.logger.error(f"Error clearing feature data: {e}")

    def _update_feature_plots(self):
        """Update feature plots (called by timer)."""
        try:
            for i, (plot, feature_name) in enumerate(zip(self.feature_plots, self.feature_assignment)):
                if not plot or not feature_name or feature_name not in self.feature_buffers:
                    if plot:
                        plot.clear()
                    continue

                buffer = self.feature_buffers[feature_name]

                if buffer.size() == 0:
                    plot.clear()
                    continue

                timestamps, values = buffer.get_latest_window(self.feature_display_window)

                if len(timestamps) == 0:
                    plot.clear()
                    continue

                # Downsample if needed
                if len(timestamps) > self.feature_max_display_points:
                    step = len(timestamps) // self.feature_max_display_points
                    timestamps = timestamps[::step]
                    values = values[::step]

                # Clear and update plot
                plot.clear()
                curve = plot.plot(timestamps, values, pen=pg.mkPen('b', width=2))

            # Emit update signal
            self.plot_updated.emit("features")

        except Exception as e:
            self.logger.error(f"Error updating feature plots: {e}")

    def get_feature_statistics(self) -> Dict[str, Any]:
        """
        Get feature processing statistics.

        Returns:
            Dictionary with feature processing statistics
        """
        return {
            'processing_enabled': self._processing_enabled,
            'feature_buffer_sizes': {name: buf.size() for name, buf in self.feature_buffers.items()},
            'update_interval': self.update_interval,
            'display_window': self.feature_display_window,
            'max_display_points': self.feature_max_display_points,
            'feature_assignment': self.feature_assignment.copy(),
            'process_every_n_packets': self.process_every_n_packets
        }

    def update_feature_calculator_settings(self, settings: Dict[str, Any]):
        """Update feature calculator settings."""
        try:
            if self.feature_calculator:
                # Update settings as needed
                if 'window_size_ms' in settings:
                    self.feature_calculator.window_size_ms = settings['window_size_ms']
                if 'overlap_ratio' in settings:
                    self.feature_calculator.overlap_ratio = settings['overlap_ratio']

                self.logger.info(f"Updated feature calculator settings: {settings}")

        except Exception as e:
            self.logger.error(f"Error updating feature calculator settings: {e}")

    def update_detection_settings(self, settings: Dict[str, Any]):
        """Update threshold detector settings."""
        try:
            if self.threshold_detector:
                # Update detection thresholds as needed
                if 'thresholds' in settings:
                    self.threshold_detector.update_thresholds(settings['thresholds'])

                self.logger.info(f"Updated detection settings: {settings}")

        except Exception as e:
            self.logger.error(f"Error updating detection settings: {e}")