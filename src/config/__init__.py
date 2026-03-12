"""Configuration package boundary.

Responsibilities:
- Provide immutable runtime constants and helper accessors for sampling/layout config.

In scope:
- Shared system rates, derived timing values, and config utility functions.

Out of scope:
- Stateful business logic, IO side effects, and UI wiring.
"""

from .system_config import (
    ORIGINAL_SAMPLE_RATE,
    SYSTEM_DOWNSAMPLE_FACTOR,
    EFFECTIVE_SAMPLE_RATE,
    TIME_DISPLAY_DOWNSAMPLE,
    TIME_DISPLAY_SAMPLE_RATE,
    PACKET_DURATION,
    PACKETS_PER_SECOND,
    MAX_TIME_DISPLAY_POINTS,
    MAX_PSD_SAMPLES,
    TIME_BUFFER_MAX_POINTS,
    PERFORMANCE_LOG_INTERVAL,
    FEATURE_PROCESSING_INTERVAL,
    get_sample_rate_info,
    log_sample_rate_info
)

__all__ = [
    'ORIGINAL_SAMPLE_RATE',
    'SYSTEM_DOWNSAMPLE_FACTOR',
    'EFFECTIVE_SAMPLE_RATE',
    'TIME_DISPLAY_DOWNSAMPLE',
    'TIME_DISPLAY_SAMPLE_RATE',
    'PACKET_DURATION',
    'PACKETS_PER_SECOND',
    'MAX_TIME_DISPLAY_POINTS',
    'MAX_PSD_SAMPLES',
    'TIME_BUFFER_MAX_POINTS',
    'PERFORMANCE_LOG_INTERVAL',
    'FEATURE_PROCESSING_INTERVAL',
    'get_sample_rate_info',
    'log_sample_rate_info'
]