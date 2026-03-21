"""Shared types for the Tab3 DAS pipeline."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class DASPacketHeader:
    """Parsed DAS packet header."""

    comm_count: int
    sample_rate_hz: int
    channel_count: int
    data_bytes: int
    packet_duration_seconds: float


@dataclass
class DASRawPacket:
    """Raw DAS packet after TCP payload parsing."""

    header: DASPacketHeader
    data_1d: np.ndarray


@dataclass
class DASParsedPacket:
    """DAS packet mapped into a 2D matrix for plots and storage."""

    header: DASPacketHeader
    matrix: np.ndarray
    packet_start_time: float
    packet_end_time: float
