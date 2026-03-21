"""Shared alignment data models for FIP and DAS packets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import numpy as np


@dataclass
class MissingRange:
    """One missing comm_count interval detected within a live session."""

    source: str
    start_comm_count: int
    end_comm_count: int


@dataclass
class FIPSessionPacket:
    """FIP payload shared with the alignment layer."""

    comm_count: int
    packet_duration_seconds: float
    sample_rate_hz: float
    unwrapped_data: np.ndarray
    display_data: np.ndarray


@dataclass
class DASSessionPacket:
    """DAS payload shared with the alignment layer."""

    comm_count: int
    packet_duration_seconds: float
    sample_rate_hz: float
    channel_count: int
    matrix: np.ndarray


@dataclass
class AlignedPacketFrame:
    """One aligned or partially aligned frame keyed by comm_count."""

    comm_count: int
    packet_start_time: float
    packet_duration_seconds: float
    fip_packet: Optional[FIPSessionPacket] = None
    das_packet: Optional[DASSessionPacket] = None
    fip_missing: bool = False
    das_missing: bool = False


@dataclass
class AlignmentStatusSnapshot:
    """UI-facing live status summary for one alignment session."""

    fip_last_comm_count: int = -1
    das_last_comm_count: int = -1
    fip_missing_count: int = 0
    das_missing_count: int = 0
    missing_ranges: List[MissingRange] = field(default_factory=list)
    alignment_status: str = "idle"
    fip_online: bool = False
    das_online: bool = False
