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

    # 两路 comm_count 差异超过此阈值时，对齐状态降级为 "lagging"（T3-03）
    # Keep a small packet-count drift tolerance; actual frame duration comes from packets.
    MAX_COMM_COUNT_DRIFT = 5
    DEFAULT_PACKET_DURATION_SECONDS = 1.0
    DEFAULT_CACHE_MEMORY_BUDGET_BYTES = 2048 * 1024 * 1024

    def __init__(self, cache_seconds: float = 10.0, max_cache_bytes: int = DEFAULT_CACHE_MEMORY_BUDGET_BYTES) -> None:
        super().__init__()
        self.logger = logging.getLogger(f"{__name__}.AlignedSessionCoordinator")
        self.cache_seconds = max(5.0, float(cache_seconds))
        self.max_cache_bytes = max(64 * 1024 * 1024, int(max_cache_bytes))
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

    def get_frames_since(self, last_comm_count: int) -> List[AlignedPacketFrame]:
        """返回 comm_count 严格大于 last_comm_count 的所有对齐帧（增量获取，T3-02）。

        与 get_recent_frames 的区别：
            get_recent_frames 每次返回最近 N 秒的全量快照，相邻调用之间
            约 90% 数据重叠，造成严重写放大。
            本方法仅返回上次存储结束点之后的新增帧，相邻文件之间无数据重叠。

        Args:
            last_comm_count: 上次存储的最后一帧的 comm_count。
                -1 表示首次调用，返回所有已缓存帧。

        Returns:
            按 comm_count 升序排列的新增对齐帧列表，可能为空。
        """
        with self._lock:
            if not self._ordered_counts:
                return []
            # 取 comm_count > last_comm_count 的帧
            new_counts = [c for c in self._ordered_counts if c > last_comm_count]
            frames = [self._build_frame_locked(c) for c in new_counts]
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
        max_frames_by_time = max(1, int(round(self.cache_seconds / max_duration)))
        latest_frame_bytes = max(1, self._latest_frame_bytes_locked())
        max_frames_by_bytes = max(1, int(self.max_cache_bytes // latest_frame_bytes))
        max_frames = max(1, min(max_frames_by_time, max_frames_by_bytes))
        if len(self._ordered_counts) > max_frames and max_frames_by_bytes < max_frames_by_time:
            self.logger.warning(
                "Alignment cache byte budget trimming active: cache_seconds=%.1f max_frames_by_time=%d "
                "max_frames_by_bytes=%d latest_frame_mb=%.1f budget_mb=%.1f",
                self.cache_seconds,
                max_frames_by_time,
                max_frames_by_bytes,
                latest_frame_bytes / (1024 * 1024),
                self.max_cache_bytes / (1024 * 1024),
            )
        while len(self._ordered_counts) > max_frames:
            old_count = self._ordered_counts.popleft()
            self._fip_packets.pop(old_count, None)
            self._das_packets.pop(old_count, None)

    def _latest_frame_bytes_locked(self) -> int:
        total = 0
        if self._last_fip_comm_count is not None and self._last_fip_comm_count in self._fip_packets:
            packet = self._fip_packets[self._last_fip_comm_count]
            for value in (packet.unwrapped_data, packet.display_data):
                total += int(getattr(value, "nbytes", 0) or 0)
            for mapping in (packet.unwrapped_by_sensor, packet.display_by_sensor):
                if isinstance(mapping, dict):
                    total += sum(int(getattr(value, "nbytes", 0) or 0) for value in mapping.values())
        if self._last_das_comm_count is not None and self._last_das_comm_count in self._das_packets:
            total += int(getattr(self._das_packets[self._last_das_comm_count].matrix, "nbytes", 0) or 0)
        return total

    def _latest_packet_duration_locked(self) -> float:
        durations = []
        if self._last_fip_comm_count is not None and self._last_fip_comm_count in self._fip_packets:
            durations.append(self._fip_packets[self._last_fip_comm_count].packet_duration_seconds)
        if self._last_das_comm_count is not None and self._last_das_comm_count in self._das_packets:
            durations.append(self._das_packets[self._last_das_comm_count].packet_duration_seconds)
        return max(durations) if durations else self.DEFAULT_PACKET_DURATION_SECONDS

    def _build_frame_locked(self, comm_count: int) -> AlignedPacketFrame:
        fip_packet = self._fip_packets.get(comm_count)
        das_packet = self._das_packets.get(comm_count)
        packet_duration = self.DEFAULT_PACKET_DURATION_SECONDS
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
        """推导当前对齐状态（T3-03 增加真实时序验证）。

        状态说明：
            stopped      — 会话未启动
            waiting      — 两路均未上线
            single-source — 仅一路在线
            lagging      — 两路均在线，但 comm_count 差异超过 MAX_COMM_COUNT_DRIFT
                           说明两路数据存在明显时序滞后，不适合联合分析
            aligned      — 两路均在线且 comm_count 差异在容忍窗口内

        Returns:
            对齐状态字符串。
        """
        if not self._session_active:
            return "stopped"
        if self._fip_online and self._das_online:
            # 验证两路最新包的 comm_count 偏差（T3-03）
            if (
                self._last_fip_comm_count is not None
                and self._last_das_comm_count is not None
            ):
                drift = abs(self._last_fip_comm_count - self._last_das_comm_count)
                if drift > self.MAX_COMM_COUNT_DRIFT:
                    return "lagging"
            return "aligned"
        if self._fip_online or self._das_online:
            return "single-source"
        return "waiting"

    def _emit_status(self, explicit_status: Optional[str] = None) -> None:
        snapshot = self.snapshot_status()
        # 计算两路 comm_count 偏差，供 UI 展示（T3-03）
        if snapshot.fip_last_comm_count >= 0 and snapshot.das_last_comm_count >= 0:
            comm_count_drift = abs(snapshot.fip_last_comm_count - snapshot.das_last_comm_count)
        else:
            comm_count_drift = -1
        payload = {
            "fip_last_comm_count": snapshot.fip_last_comm_count,
            "das_last_comm_count": snapshot.das_last_comm_count,
            "fip_missing_count": snapshot.fip_missing_count,
            "das_missing_count": snapshot.das_missing_count,
            "alignment_status": explicit_status or snapshot.alignment_status,
            "fip_online": snapshot.fip_online,
            "das_online": snapshot.das_online,
            # 两路 comm_count 差值（-1 表示任一路尚无数据）
            "comm_count_drift": comm_count_drift,
            "missing_ranges": [
                f"{item.source}:{item.start_comm_count}-{item.end_comm_count}"
                for item in snapshot.missing_ranges[-5:]
            ],
        }
        self.alignment_status_changed.emit(payload)
