"""
Feature Calculator Module for Real-time Signal Feature Extraction

This module implements various feature extraction algorithms optimized for
real-time processing of fiber interferometer signals.

Features implemented:
- Short-time Energy (短时能量)
- Zero-Crossing Rate (短时过零率)
- Peak Factor (峰值因子)
- RMS (均方根值)

Author: Claude
Date: 2026-03-11
"""

import time
import logging
import numpy as np
from typing import Dict, List, Optional, Tuple, Any
from collections import deque
import threading

class FeatureWindow:
    """
    Sliding window for feature calculation.

    Manages a sliding window of signal data for computing time-domain features
    with configurable window size and overlap.
    """

    def __init__(self, window_size: int, overlap_ratio: float = 0.5):
        """
        Initialize feature window.

        Args:
            window_size: Number of samples in the window
            overlap_ratio: Overlap ratio between windows (0.0 - 1.0)
        """
        self.window_size = window_size
        self.overlap_ratio = overlap_ratio
        self.hop_size = int(window_size * (1 - overlap_ratio))

        self._buffer = deque(maxlen=window_size * 2)  # Double size for overlap
        self._lock = threading.RLock()

    def add_data(self, data: np.ndarray) -> List[np.ndarray]:
        """
        Add new data and return available windows.

        Args:
            data: New data samples to add

        Returns:
            List of windows ready for feature extraction
        """
        windows = []

        with self._lock:
            # Add new data to buffer
            self._buffer.extend(data)

            # Extract windows
            while len(self._buffer) >= self.window_size:
                # Get current window
                window = np.array(list(self._buffer)[:self.window_size])
                windows.append(window)

                # Advance by hop size
                for _ in range(min(self.hop_size, len(self._buffer))):
                    self._buffer.popleft()

                # Check if we have enough data for next window
                if len(self._buffer) < self.window_size:
                    break

        return windows

    def reset(self):
        """Reset the window buffer."""
        with self._lock:
            self._buffer.clear()


class FeatureCalculator:
    """
    Real-time feature calculator for signal analysis.

    This class provides efficient computation of various signal features
    using sliding window approach for real-time processing.
    """

    def __init__(self, sample_rate: float = 1000000.0, window_size_ms: float = 50.0,
                 overlap_ratio: float = 0.5):
        """
        Initialize feature calculator.

        Args:
            sample_rate: Signal sampling rate in Hz
            window_size_ms: Feature window size in milliseconds
            overlap_ratio: Overlap ratio between windows (0.0 - 1.0)
        """
        self.sample_rate = sample_rate
        self.window_size_ms = window_size_ms
        self.overlap_ratio = overlap_ratio

        # Calculate window size in samples
        self.window_size = int(sample_rate * window_size_ms / 1000.0)

        # Initialize sliding window
        self.feature_window = FeatureWindow(self.window_size, overlap_ratio)

        self.logger = logging.getLogger(__name__ + '.FeatureCalculator')

        # Feature computation enabled flags
        self.enabled_features = {
            'short_energy': True,
            'zero_crossing': True,
            'peak_factor': True,
            'rms': True
        }

        # Feature history for baseline calculation
        self.feature_history = {
            'short_energy': deque(maxlen=1000),
            'zero_crossing': deque(maxlen=1000),
            'peak_factor': deque(maxlen=1000),
            'rms': deque(maxlen=1000)
        }

        # Current baselines
        self.baselines = {
            'short_energy': 0.0,
            'zero_crossing': 0.0,
            'peak_factor': 1.0,
            'rms': 0.0
        }

        self.logger.info(f"FeatureCalculator initialized: window={window_size_ms}ms "
                        f"({self.window_size} samples), overlap={overlap_ratio}")

    def set_enabled_features(self, enabled_features: Dict[str, bool]):
        """
        Set which features to compute.

        Args:
            enabled_features: Dictionary mapping feature names to enabled status
        """
        self.enabled_features.update(enabled_features)
        self.logger.info(f"Updated enabled features: {self.enabled_features}")

    def process_data(self, data: np.ndarray) -> Dict[str, List[Tuple[float, float]]]:
        """
        Process signal data and extract features.

        Args:
            data: Signal data array

        Returns:
            Dictionary mapping feature names to list of (timestamp, value) tuples
        """
        start_time = time.time()

        # Get windows for feature computation
        windows = self.feature_window.add_data(data)

        if not windows:
            return {}

        features = {name: [] for name in self.enabled_features.keys()
                   if self.enabled_features[name]}

        # Process each window
        for i, window in enumerate(windows):
            # Calculate timestamp for this window (center of window)
            window_timestamp = time.time() - (len(windows) - i - 1) * \
                             (self.window_size * (1 - self.overlap_ratio) / self.sample_rate)

            # Compute enabled features
            if self.enabled_features.get('short_energy', False):
                energy = self._compute_short_energy(window)
                features['short_energy'].append((window_timestamp, energy))
                self.feature_history['short_energy'].append(energy)

            if self.enabled_features.get('zero_crossing', False):
                zcr = self._compute_zero_crossing_rate(window)
                features['zero_crossing'].append((window_timestamp, zcr))
                self.feature_history['zero_crossing'].append(zcr)

            if self.enabled_features.get('peak_factor', False):
                pf = self._compute_peak_factor(window)
                features['peak_factor'].append((window_timestamp, pf))
                self.feature_history['peak_factor'].append(pf)

            if self.enabled_features.get('rms', False):
                rms = self._compute_rms(window)
                features['rms'].append((window_timestamp, rms))
                self.feature_history['rms'].append(rms)

        processing_time = (time.time() - start_time) * 1000.0

        if processing_time > 10:  # Log slow processing
            self.logger.warning(f"Slow feature processing: {processing_time:.1f}ms "
                              f"for {len(windows)} windows")

        return features

    def _compute_short_energy(self, window: np.ndarray) -> float:
        """
        Compute short-time energy.

        Args:
            window: Signal window

        Returns:
            Short-time energy value
        """
        return float(np.sum(window ** 2) / len(window))

    def _compute_zero_crossing_rate(self, window: np.ndarray) -> float:
        """
        Compute zero-crossing rate.

        Args:
            window: Signal window

        Returns:
            Zero-crossing rate (crossings per sample)
        """
        # Count sign changes
        sign_changes = np.sum(np.diff(np.sign(window)) != 0)
        return float(sign_changes / (len(window) - 1))

    def _compute_peak_factor(self, window: np.ndarray) -> float:
        """
        Compute peak factor (crest factor).

        Args:
            window: Signal window

        Returns:
            Peak factor (peak / RMS)
        """
        rms = np.sqrt(np.mean(window ** 2))
        peak = np.max(np.abs(window))

        if rms > 1e-10:  # Avoid division by zero
            return float(peak / rms)
        else:
            return 1.0

    def _compute_rms(self, window: np.ndarray) -> float:
        """
        Compute RMS (Root Mean Square) value.

        Args:
            window: Signal window

        Returns:
            RMS value
        """
        return float(np.sqrt(np.mean(window ** 2)))

    def update_baselines(self) -> Dict[str, float]:
        """
        Update baseline values using recent feature history.

        Returns:
            Updated baseline values
        """
        for feature_name, history in self.feature_history.items():
            if len(history) >= 10:  # Need minimum samples
                # Use median for robust baseline estimation
                self.baselines[feature_name] = float(np.median(list(history)))

        self.logger.info(f"Updated baselines: {self.baselines}")
        return self.baselines.copy()

    def get_baselines(self) -> Dict[str, float]:
        """Get current baseline values."""
        return self.baselines.copy()

    def reset(self):
        """Reset feature calculator state."""
        self.feature_window.reset()

        for history in self.feature_history.values():
            history.clear()

        self.baselines = {
            'short_energy': 0.0,
            'zero_crossing': 0.0,
            'peak_factor': 1.0,
            'rms': 0.0
        }

        self.logger.info("Feature calculator reset")