"""
Threshold-based Signal Detector for PCCP Monitoring

This module implements threshold-based detection algorithms for identifying
significant signal events that may indicate wire breaks or other anomalies.

Author: Claude
Date: 2026-03-11
"""

import time
import logging
from typing import Dict, List, Optional, Tuple, Any, NamedTuple
from collections import deque
import numpy as np

class DetectionResult(NamedTuple):
    """Detection result data structure."""
    sequence_number: int
    timestamp: float
    feature_name: str
    feature_value: float
    threshold: float
    baseline: float
    duration: Optional[float] = None  # Will be set when event ends

class ThresholdDetector:
    """
    Threshold-based signal detector for real-time monitoring.

    This class monitors feature values against dynamic thresholds and
    generates detection events when thresholds are exceeded.
    """

    def __init__(self):
        """Initialize threshold detector."""
        self.logger = logging.getLogger(__name__ + '.ThresholdDetector')

        # Threshold multipliers for each feature
        self.threshold_factors = {
            'short_energy': 3.0,
            'zero_crossing': 3.0,
            'peak_factor': 3.0,
            'rms': 3.0
        }

        # Current baselines for each feature
        self.baselines = {
            'short_energy': 0.0,
            'zero_crossing': 0.0,
            'peak_factor': 1.0,
            'rms': 0.0
        }

        # Detection state tracking
        self.active_detections = {}  # feature_name -> DetectionResult
        self.detection_history = deque(maxlen=1000)  # Recent detections
        self.sequence_counter = 0

        # Detection parameters
        self.min_detection_duration = 0.01  # 10ms minimum
        self.max_detection_duration = 10.0  # 10s maximum
        self.baseline_update_interval = 10.0  # Update every 10 seconds
        self.auto_update_baseline = True

        self.last_baseline_update = time.time()

        self.logger.info("ThresholdDetector initialized")

    def set_threshold_factors(self, threshold_factors: Dict[str, float]):
        """
        Set threshold multiplication factors.

        Args:
            threshold_factors: Dict mapping feature names to threshold factors
        """
        self.threshold_factors.update(threshold_factors)
        self.logger.info(f"Updated threshold factors: {self.threshold_factors}")

    def set_baselines(self, baselines: Dict[str, float]):
        """
        Set baseline values for each feature.

        Args:
            baselines: Dict mapping feature names to baseline values
        """
        self.baselines.update(baselines)
        self.logger.info(f"Updated baselines: {self.baselines}")

    def process_features(self, features: Dict[str, List[Tuple[float, float]]]) -> List[DetectionResult]:
        """
        Process feature values and detect threshold crossings.

        Args:
            features: Dict mapping feature names to (timestamp, value) lists

        Returns:
            List of new detection results
        """
        new_detections = []

        for feature_name, feature_data in features.items():
            if feature_name not in self.threshold_factors:
                continue

            for timestamp, value in feature_data:
                detection = self._check_threshold(feature_name, value, timestamp)
                if detection:
                    new_detections.append(detection)

        # Check for ended detections
        self._update_detection_durations()

        # Auto-update baselines if enabled
        if self.auto_update_baseline:
            current_time = time.time()
            if current_time - self.last_baseline_update >= self.baseline_update_interval:
                self._auto_update_baselines(features)
                self.last_baseline_update = current_time

        return new_detections

    def _check_threshold(self, feature_name: str, value: float, timestamp: float) -> Optional[DetectionResult]:
        """
        Check if feature value exceeds threshold.

        Args:
            feature_name: Name of the feature
            value: Feature value
            timestamp: Timestamp of the measurement

        Returns:
            DetectionResult if threshold exceeded, None otherwise
        """
        baseline = self.baselines.get(feature_name, 0.0)
        factor = self.threshold_factors.get(feature_name, 3.0)

        # Calculate threshold based on baseline and factor
        if feature_name in ['short_energy', 'rms', 'zero_crossing']:
            threshold = baseline + factor * np.sqrt(baseline) if baseline > 0 else factor
        else:  # peak_factor
            threshold = baseline * factor

        # Check if threshold is exceeded
        exceeds_threshold = value > threshold

        # Handle detection state
        if exceeds_threshold:
            if feature_name not in self.active_detections:
                # Start new detection
                self.sequence_counter += 1
                detection = DetectionResult(
                    sequence_number=self.sequence_counter,
                    timestamp=timestamp,
                    feature_name=feature_name,
                    feature_value=value,
                    threshold=threshold,
                    baseline=baseline
                )
                self.active_detections[feature_name] = detection

                self.logger.info(f"Detection started: {feature_name} "
                               f"value={value:.3f} > threshold={threshold:.3f}")

                return detection
            else:
                # Update existing detection with higher value
                active = self.active_detections[feature_name]
                if value > active.feature_value:
                    updated = active._replace(
                        feature_value=value,
                        threshold=threshold,
                        timestamp=timestamp
                    )
                    self.active_detections[feature_name] = updated

        else:
            # Value below threshold
            if feature_name in self.active_detections:
                # End active detection
                active = self.active_detections[feature_name]
                duration = timestamp - active.timestamp

                if duration >= self.min_detection_duration:
                    # Valid detection - add to history
                    completed = active._replace(duration=duration)
                    self.detection_history.append(completed)

                    self.logger.info(f"Detection ended: {feature_name} "
                                   f"duration={duration:.3f}s")

                # Remove from active detections
                del self.active_detections[feature_name]

        return None

    def _update_detection_durations(self):
        """Update durations of active detections and timeout long detections."""
        current_time = time.time()
        to_remove = []

        for feature_name, detection in self.active_detections.items():
            duration = current_time - detection.timestamp

            # Timeout detections that are too long
            if duration > self.max_detection_duration:
                completed = detection._replace(duration=duration)
                self.detection_history.append(completed)

                self.logger.warning(f"Detection timed out: {feature_name} "
                                  f"duration={duration:.3f}s")
                to_remove.append(feature_name)

        # Remove timed out detections
        for feature_name in to_remove:
            del self.active_detections[feature_name]

    def _auto_update_baselines(self, recent_features: Dict[str, List[Tuple[float, float]]]):
        """
        Automatically update baseline values using recent feature data.

        Args:
            recent_features: Recent feature data for baseline calculation
        """
        updated_baselines = {}

        for feature_name, feature_data in recent_features.items():
            if len(feature_data) >= 5:  # Need minimum data
                values = [value for _, value in feature_data]
                # Use median for robust baseline estimation
                new_baseline = float(np.median(values))
                updated_baselines[feature_name] = new_baseline

        if updated_baselines:
            self.set_baselines(updated_baselines)

    def get_detection_summary(self) -> Dict[str, Any]:
        """
        Get summary of detection statistics.

        Returns:
            Dictionary with detection statistics
        """
        total_detections = len(self.detection_history)
        active_count = len(self.active_detections)

        # Count by feature type
        feature_counts = {}
        for detection in self.detection_history:
            feature_name = detection.feature_name
            feature_counts[feature_name] = feature_counts.get(feature_name, 0) + 1

        return {
            'total_detections': total_detections,
            'active_detections': active_count,
            'feature_counts': feature_counts,
            'thresholds': self._get_current_thresholds(),
            'baselines': self.baselines.copy()
        }

    def _get_current_thresholds(self) -> Dict[str, float]:
        """Calculate current threshold values."""
        thresholds = {}
        for feature_name, factor in self.threshold_factors.items():
            baseline = self.baselines.get(feature_name, 0.0)
            if feature_name in ['short_energy', 'rms', 'zero_crossing']:
                threshold = baseline + factor * np.sqrt(baseline) if baseline > 0 else factor
            else:  # peak_factor
                threshold = baseline * factor
            thresholds[feature_name] = threshold
        return thresholds

    def get_recent_detections(self, limit: int = 100) -> List[DetectionResult]:
        """
        Get recent detection results.

        Args:
            limit: Maximum number of results to return

        Returns:
            List of recent detection results
        """
        return list(self.detection_history)[-limit:]

    def clear_detection_history(self):
        """Clear detection history."""
        self.detection_history.clear()
        self.logger.info("Detection history cleared")

    def reset(self):
        """Reset detector state."""
        self.active_detections.clear()
        self.detection_history.clear()
        self.sequence_counter = 0
        self.last_baseline_update = time.time()
        self.logger.info("ThresholdDetector reset")