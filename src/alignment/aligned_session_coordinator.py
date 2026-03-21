"""Shared session coordinator for FIP and DAS packet alignment."""

from __future__ import annotations

import logging
import threading
from collections import deque
from typing import Deque, Dict, List, Optional

from PyQt5.QtCore import QObject, pyqtSignal

from .aligned_types import (
    AlignedPacketFrame,
    AlignmentStatusSnapshot,
    DASSessionPacket,
    FIPSessionPacket,
    MissingRange,
)


class AlignedSessionCoordinator(QObject):
    """Maintain a live comm_count keyed cache shared by Tab3 and Tab4."""

    alignment_status_changed = pyqtSignal(dict)

    def __init__(self, cache_seconds: float = 10.0) -> None:
        super().__init__()
        self.logger = logging.getLogger(f"{__name__}.AlignedSessionCoordinator")
        self.cache_seconds = max(5.0, float(cache_seconds))
        self._lock = threading.Lock()
        self._fip_packets: Dict[int, FIPSessionPacket] = {}
        self._das_packets: Dict[int, DASSessionPacket] = {}
        self._ordered_counts: Deque[int] = deque()
        self._missing_ranges: List[MissingRange] = []
        self._last_fip_comm_count: Optional[int] = None
        self._last_das_comm_count: Optional[int] = None
        self._fip_online = False
        self._das_online = False
        self._session_active = False

    def start_session(self) -> None:
        """Reset all state for a newly started monitoring session."""
        with self._lock:
            self._fip_packets.clear()
            self._das_packets.clear()
            self._ordered_counts.clear()
            self._missing_ranges.clear()
            self._last_fip_comm_count = None
            self._last_das_comm_count = None
            self._fip_online = False
            self._das_online = False
            self._session_active = True
        self._emit_status("waiting")

    def stop_session(self) -> None:
        """Mark the session stopped and clear online status."""
        with self._lock:
            self._fip_online = False
            self._das_online = False
            self._session_active = False
        self._emit_status("stopped")

    def update_online_state(self, source: str, online: bool) -> None:
        """Update one source online state without mutating packet caches."""
        with self._lock:
            if source == "fip":
                self._fip_online = online
            elif source == "das":
                self._das_online = online
        self._emit_status(self._derive_alignment_status())

    def push_fip_packet(self, packet: FIPSessionPacket) -> None:
        """Insert one FIP packet into the live session cache."""
        with self._lock:
            self._record_missing_ranges("fip", self._last_fip_comm_count, packet.comm_count)
            self._last_fip_comm_count = packet.comm_count
            self._fip_packets[packet.comm_count] = packet
            self._fip_online = True
            self._remember_count(packet.comm_count)
            self._trim_cache_locked()
        self._emit_status(self._derive_alignment_status())

    def push_das_packet(self, packet: DASSessionPacket) -> None:
        """Insert one DAS packet into the live session cache."""
        with self._lock:
            self._record_missing_ranges("das", self._last_das_comm_count, packet.comm_count)
            self._last_das_comm_count = packet.comm_count
            self._das_packets[packet.comm_count] = packet
            self._das_online = True
            self._remember_count(packet.comm_count)
            self._trim_cache_locked()
        self._emit_status(self._derive_alignment_status())

    def get_recent_frames(self, window_seconds: float) -> List[AlignedPacketFrame]:
        """Return recent aligned frames within the requested trailing time window."""
        window_seconds = max(0.2, float(window_seconds))
        with self._lock:
            if not self._ordered_counts:
                return []
            max_duration = self._latest_packet_duration_locked()
            frame_count = max(1, int(round(window_seconds / max_duration)))
            selected_counts = list(self._ordered_counts)[-frame_count:]
            frames = [self._build_frame_locked(comm_count) for comm_count in selected_counts]
        return frames

    def snapshot_status(self) -> AlignmentStatusSnapshot:
        """Return a copy of the latest status for storage or UI use."""
        with self._lock:
            return AlignmentStatusSnapshot(
                fip_last_comm_count=-1 if self._last_fip_comm_count is None else self._last_fip_comm_count,
                das_last_comm_count=-1 if self._last_das_comm_count is None else self._last_das_comm_count,
                fip_missing_count=sum(1 for item in self._missing_ranges if item.source == "fip"),
                das_missing_count=sum(1 for item in self._missing_ranges if item.source == "das"),
                missing_ranges=list(self._missing_ranges[-20:]),
                alignment_status=self._derive_alignment_status(),
                fip_online=self._fip_online,
                das_online=self._das_online,
            )

    def _record_missing_ranges(self, source: str, last_comm_count: Optional[int], new_comm_count: int) -> None:
        if last_comm_count is None:
            return
        if new_comm_count == 0 and last_comm_count >= 0:
            self.logger.warning("%s comm_count reset detected while session is active", source)
            return
        if new_comm_count <= last_comm_count + 1:
            return
        self._missing_ranges.append(
            MissingRange(
                source=source,
                start_comm_count=last_comm_count + 1,
                end_comm_count=new_comm_count - 1,
            )
        )

    def _remember_count(self, comm_count: int) -> None:
        if comm_count in self._ordered_counts:
            return
        self._ordered_counts.append(comm_count)

    def _trim_cache_locked(self) -> None:
        max_duration = self._latest_packet_duration_locked()
        max_frames = max(10, int(round(self.cache_seconds / max_duration)))
        while len(self._ordered_counts) > max_frames:
            old_count = self._ordered_counts.popleft()
            self._fip_packets.pop(old_count, None)
            self._das_packets.pop(old_count, None)

    def _latest_packet_duration_locked(self) -> float:
        durations = []
        if self._last_fip_comm_count is not None and self._last_fip_comm_count in self._fip_packets:
            durations.append(self._fip_packets[self._last_fip_comm_count].packet_duration_seconds)
        if self._last_das_comm_count is not None and self._last_das_comm_count in self._das_packets:
            durations.append(self._das_packets[self._last_das_comm_count].packet_duration_seconds)
        return max(durations) if durations else 0.2

    def _build_frame_locked(self, comm_count: int) -> AlignedPacketFrame:
        fip_packet = self._fip_packets.get(comm_count)
        das_packet = self._das_packets.get(comm_count)
        packet_duration = 0.2
        if das_packet is not None:
            packet_duration = das_packet.packet_duration_seconds
        elif fip_packet is not None:
            packet_duration = fip_packet.packet_duration_seconds
        return AlignedPacketFrame(
            comm_count=comm_count,
            packet_start_time=comm_count * packet_duration,
            packet_duration_seconds=packet_duration,
            fip_packet=fip_packet,
            das_packet=das_packet,
            fip_missing=fip_packet is None,
            das_missing=das_packet is None,
        )

    def _derive_alignment_status(self) -> str:
        if not self._session_active:
            return "stopped"
        if self._fip_online and self._das_online:
            return "aligned"
        if self._fip_online or self._das_online:
            return "single-source"
        return "waiting"

    def _emit_status(self, explicit_status: Optional[str] = None) -> None:
        snapshot = self.snapshot_status()
        payload = {
            "fip_last_comm_count": snapshot.fip_last_comm_count,
            "das_last_comm_count": snapshot.das_last_comm_count,
            "fip_missing_count": snapshot.fip_missing_count,
            "das_missing_count": snapshot.das_missing_count,
            "alignment_status": explicit_status or snapshot.alignment_status,
            "fip_online": snapshot.fip_online,
            "das_online": snapshot.das_online,
            "missing_ranges": [
                f"{item.source}:{item.start_comm_count}-{item.end_comm_count}"
                for item in snapshot.missing_ranges[-5:]
            ],
        }
        self.alignment_status_changed.emit(payload)
