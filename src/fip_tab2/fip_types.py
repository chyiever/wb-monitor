"""Shared types for the independent FIP Tab2 pipeline."""

from dataclasses import dataclass, field
from typing import Dict, List

import numpy as np


FEATURE_DISPLAY_NAMES = {
    "short_energy": "Short Energy",
    "zero_crossing": "Zero Crossing",
    "peak_factor": "Peak Factor",
    "rms": "RMS",
}


@dataclass
class FIPTab2InputPacket:
    """Downsampled Tab1 packet forwarded to Tab2."""

    timestamp: float
    comm_count: int
    sample_rate: float
    data: np.ndarray


@dataclass
class FIPFeatureFrame:
    """Feature values computed from one sliding window."""

    window_index: int
    start_time: float
    center_time: float
    end_time: float
    feature_values: Dict[str, float]
    sample_rate: float
    window_size_seconds: float
    hop_seconds: float


@dataclass
class FIPWindowDetectionResult:
    """Per-window threshold decision across all computed features."""

    frame: FIPFeatureFrame
    triggered_features: List[str]
    thresholds: Dict[str, float]
    baselines: Dict[str, float]


@dataclass
class FIPAlarmEvent:
    """Aggregated alarm event built from consecutive abnormal windows."""

    event_id: int
    start_time: float
    end_time: float
    duration: float
    trigger_feature_names: List[str]
    trigger_feature_count: int
    first_window_index: int
    last_window_index: int
    window_results: List[FIPWindowDetectionResult] = field(default_factory=list)


@dataclass
class FIPTriggerSaveRequest:
    """Trigger save request emitted by the detection worker."""

    event: FIPAlarmEvent
    pre_trigger_seconds: float
    post_trigger_seconds: float
    enabled_feature_names: List[str]
