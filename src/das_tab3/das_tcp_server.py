"""TCP server for DAS packet reception."""

from __future__ import annotations

import logging
import socket
import struct
import threading
import time
from typing import Optional, Tuple

import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal

from .das_types import DASPacketHeader, DASRawPacket


class DASTCPServer(QObject):
    """Receive DAS packets over TCP using the Tab3 protocol."""

    packet_received = pyqtSignal(object)
    connection_status = pyqtSignal(bool, str)
    error_occurred = pyqtSignal(str)
    header_updated = pyqtSignal(dict)
    statistics_updated = pyqtSignal(dict)

    HEADER_STRUCT = struct.Struct(">IIIId")
    MAX_PAYLOAD_BYTES = 512 * 1024 * 1024

    def __init__(self, ip: str = "0.0.0.0", port: int = 3678):
        super().__init__()
        self.ip = ip
        self.port = port
        self.server_socket: Optional[socket.socket] = None
        self.client_socket: Optional[socket.socket] = None
        self.client_address: Optional[Tuple[str, int]] = None
        self._server_thread: Optional[threading.Thread] = None
        self._running = False
        self._connected = False
        self._last_data_time = 0.0
        self._last_comm_count: Optional[int] = None
        self.missing_packets = 0
        self.packets_received = 0
        self.total_data_received = 0
        self.last_stats_time = time.monotonic()
        self._stats_packets_at_last_log = 0
        self._receive_times_ms: list[float] = []
        self._last_packet_rate = 0.0
        self._last_data_rate_mbps = 0.0
        self._last_avg_receive_time_ms = 0.0
        self.logger = logging.getLogger(f"{__name__}.DASTCPServer")

    def start_server(self) -> bool:
        """Start the TCP server."""
        try:
            if self._running:
                return True
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 * 1024 * 1024)
            bind_ip = self.ip
            try:
                self.server_socket.bind((bind_ip, self.port))
            except OSError as bind_exc:
                if bind_ip not in ("", "0.0.0.0"):
                    self.logger.warning(
                        "DAS bind failed on %s:%s (%s); falling back to 0.0.0.0:%s",
                        bind_ip,
                        self.port,
                        bind_exc,
                        self.port,
                    )
                    bind_ip = "0.0.0.0"
                    self.server_socket.bind((bind_ip, self.port))
                else:
                    raise
            self.server_socket.listen(1)
            self._running = True
            self._server_thread = threading.Thread(target=self._server_loop, daemon=True)
            self._server_thread.start()
            self.logger.info("DAS TCP server started on %s:%s", bind_ip, self.port)
            self.logger.debug("TAB3_NODE das_tcp.start ip=%s bind_ip=%s port=%s", self.ip, bind_ip, self.port)
            self.connection_status.emit(False, f"DAS server started on {bind_ip}:{self.port}")
            return True
        except Exception as exc:
            self._running = False
            if self.server_socket:
                try:
                    self.server_socket.close()
                except OSError:
                    pass
                self.server_socket = None
            self.error_occurred.emit(f"Failed to start DAS server: {exc}")
            self.logger.error("Failed to start DAS server: %s", exc)
            return False

    def stop_server(self) -> None:
        """Stop the TCP server."""
        self._running = False
        self._connected = False
        if self.client_socket:
            try:
                self.client_socket.close()
            except OSError:
                pass
            self.client_socket = None
        if self.server_socket:
            try:
                self.server_socket.close()
            except OSError:
                pass
            self.server_socket = None
        self.logger.debug("TAB3_NODE das_tcp.stop packets=%d missing=%d", self.packets_received, self.missing_packets)
        self.connection_status.emit(False, "DAS server stopped")

    def is_connected(self) -> bool:
        """Return whether a DAS client is currently connected."""
        return self._connected

    def last_data_age_seconds(self) -> float:
        """Return the age of the last complete packet."""
        if self._last_data_time <= 0.0:
            return float("inf")
        return max(0.0, time.time() - self._last_data_time)

    def _server_loop(self) -> None:
        while self._running and self.server_socket:
            try:
                self.connection_status.emit(False, "Waiting for DAS connection...")
                self.logger.debug("TAB3_NODE das_tcp.waiting ip=%s port=%s", self.ip, self.port)
                client_socket, client_address = self.server_socket.accept()
                self.client_socket = client_socket
                self.client_address = client_address
                self.client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 16 * 1024 * 1024)
                self.client_socket.settimeout(1.0)
                self._connected = True
                self._reset_connection_stats()
                self.logger.info("DAS client connected from %s", client_address)
                self.connection_status.emit(True, f"DAS connected to {client_address}")
                self._receive_loop()
            except Exception as exc:
                if self._running:
                    self.logger.error("DAS server loop error: %s", exc)
        self._connected = False

    def _receive_loop(self) -> None:
        while self._running and self._connected and self.client_socket:
            packet_started = time.perf_counter()
            try:
                header_bytes = self._recv_exact(self.HEADER_STRUCT.size)
                if not header_bytes:
                    continue
                comm_count, sample_rate_hz, channel_count, data_bytes, packet_duration_seconds = self.HEADER_STRUCT.unpack(header_bytes)
                self.logger.debug(
                    "TAB3_NODE das_tcp.header comm=%s sample_rate=%s channels=%s data_bytes=%s duration=%.9f",
                    comm_count,
                    sample_rate_hz,
                    channel_count,
                    data_bytes,
                    packet_duration_seconds,
                )
                if sample_rate_hz <= 0 or channel_count <= 0:
                    self.error_occurred.emit(
                        f"Invalid DAS header: sample_rate={sample_rate_hz}, channels={channel_count}"
                    )
                    continue
                if data_bytes <= 0 or data_bytes > self.MAX_PAYLOAD_BYTES or data_bytes % 8 != 0:
                    self.error_occurred.emit(f"Invalid DAS data_bytes: {data_bytes}")
                    continue
                payload = self._recv_exact(data_bytes)
                if not payload:
                    continue
                packet_receive_time = time.time()
                data = np.frombuffer(payload, dtype=">f8").astype(np.float64, copy=False)
                total_points = int(data_bytes // 8)
                if total_points % channel_count != 0:
                    self.error_occurred.emit(
                        f"DAS payload/channel mismatch: points={total_points}, channels={channel_count}"
                    )
                    continue
                samples_per_channel = total_points // channel_count
                actual_duration = samples_per_channel / float(sample_rate_hz)
                if packet_duration_seconds <= 0.0:
                    packet_duration_seconds = actual_duration
                duration_error = abs(packet_duration_seconds - actual_duration)
                tolerance = max(1.0 / float(sample_rate_hz), 1e-9)
                if duration_error > tolerance:
                    self.logger.warning(
                        "DAS packet duration mismatch: header=%.12f, payload=%.12f, comm=%d",
                        packet_duration_seconds,
                        actual_duration,
                        comm_count,
                    )
                    packet_duration_seconds = actual_duration
                header = DASPacketHeader(
                    comm_count=comm_count,
                    sample_rate_hz=sample_rate_hz,
                    channel_count=channel_count,
                    data_bytes=data_bytes,
                    packet_duration_seconds=packet_duration_seconds,
                )
                packet = DASRawPacket(header=header, data_1d=data, receive_timestamp=packet_receive_time)
                self._update_gap_stats(comm_count)
                self._last_comm_count = comm_count
                self.packets_received += 1
                self.total_data_received += data_bytes + self.HEADER_STRUCT.size
                self._last_data_time = time.time()
                receive_ms = (time.perf_counter() - packet_started) * 1000.0
                self._receive_times_ms.append(receive_ms)
                if len(self._receive_times_ms) > 200:
                    self._receive_times_ms = self._receive_times_ms[-200:]
                expected_receive_ms = max(float(packet_duration_seconds) * 1000.0, 1.0)
                slow_receive_threshold_ms = max(500.0, expected_receive_ms * 1.5)
                if receive_ms > slow_receive_threshold_ms:
                    self.logger.warning(
                        "TAB3_NODE das_tcp.slow_receive comm=%s receive_ms=%.2f "
                        "threshold_ms=%.2f expected_packet_ms=%.2f data_bytes=%s",
                        comm_count,
                        receive_ms,
                        slow_receive_threshold_ms,
                        expected_receive_ms,
                        data_bytes,
                    )
                header_payload = {
                    "channel_count": channel_count,
                    "sample_rate_hz": sample_rate_hz,
                    "data_bytes": data_bytes,
                    "packet_duration_seconds": packet_duration_seconds,
                    "comm_count": comm_count,
                }
                stats_payload = self._statistics_payload(connected=True)
                self.header_updated.emit(header_payload)
                self.statistics_updated.emit(stats_payload)
                self.logger.debug(
                    "TAB3_NODE das_tcp.packet comm=%s packets=%d missing=%d receive_ms=%.2f samples_per_channel=%d packet_rate=%.2f data_rate_mbps=%.3f",
                    comm_count,
                    self.packets_received,
                    self.missing_packets,
                    receive_ms,
                    samples_per_channel,
                    self._last_packet_rate,
                    self._last_data_rate_mbps,
                )
                self.packet_received.emit(packet)
                self._log_performance_stats()
            except socket.timeout:
                continue
            except Exception as exc:
                if self._running:
                    self.logger.error("DAS receive error: %s", exc)
                    self.error_occurred.emit(f"DAS receive error: {exc}")
                break
        self._connected = False
        self.connection_status.emit(False, "DAS disconnected")
        self.statistics_updated.emit(self._statistics_payload(connected=False))
        self.logger.info(
            "DAS disconnected: packets=%d missing=%d last_comm=%s",
            self.packets_received,
            self.missing_packets,
            self._last_comm_count,
        )

    def _recv_exact(self, size: int) -> Optional[bytes]:
        if not self.client_socket:
            return None
        chunks = bytearray()
        remaining = size
        while remaining > 0 and self._running:
            try:
                chunk = self.client_socket.recv(min(1024 * 1024, remaining))
                if not chunk:
                    return None
                chunks.extend(chunk)
                remaining -= len(chunk)
            except socket.timeout:
                continue
            except OSError:
                return None
        return bytes(chunks) if len(chunks) == size else None

    def _reset_connection_stats(self) -> None:
        self.packets_received = 0
        self.missing_packets = 0
        self.total_data_received = 0
        self._last_comm_count = None
        self._last_data_time = 0.0
        self.last_stats_time = time.monotonic()
        self._stats_packets_at_last_log = 0
        self._receive_times_ms.clear()
        self._last_packet_rate = 0.0
        self._last_data_rate_mbps = 0.0
        self._last_avg_receive_time_ms = 0.0
        self.logger.debug("TAB3_NODE das_tcp.reset_connection_stats")

    def _update_gap_stats(self, comm_count: int) -> None:
        if self._last_comm_count is not None and comm_count > self._last_comm_count + 1:
            missing = comm_count - self._last_comm_count - 1
            self.missing_packets += missing
            self.logger.warning(
                "DAS comm_count gap: last=%d, current=%d, missing=%d",
                self._last_comm_count,
                comm_count,
                missing,
            )
        elif self._last_comm_count is not None and comm_count <= self._last_comm_count:
            self.logger.warning(
                "DAS comm_count reset/out-of-order: last=%d, current=%d",
                self._last_comm_count,
                comm_count,
            )

    def _statistics_payload(self, connected: bool) -> dict:
        return {
            "packets_received": self.packets_received,
            "missing_packets": self.missing_packets,
            "connected": connected,
            "last_comm_count": -1 if self._last_comm_count is None else self._last_comm_count,
            "packet_rate": self._last_packet_rate,
            "data_rate_mbps": self._last_data_rate_mbps,
            "avg_receive_time_ms": self._last_avg_receive_time_ms,
        }

    def _log_performance_stats(self) -> None:
        if self.packets_received <= 0 or self.packets_received % 50 != 0:
            return
        if self.packets_received == self._stats_packets_at_last_log:
            return
        current_time = time.monotonic()
        elapsed_time = max(current_time - self.last_stats_time, 1e-9)
        interval_packets = self.packets_received - self._stats_packets_at_last_log
        self._last_data_rate_mbps = (self.total_data_received / elapsed_time) / (1024 * 1024)
        self._last_packet_rate = interval_packets / elapsed_time
        self._last_avg_receive_time_ms = float(np.mean(self._receive_times_ms)) if self._receive_times_ms else 0.0
        max_receive_ms = float(np.max(self._receive_times_ms)) if self._receive_times_ms else 0.0
        self.logger.info(
            "TAB3_NODE das_tcp.stats packets=%d interval_packets=%d rate=%.2f pkt/s data_rate=%.3f MB/s avg_rx=%.2f ms max_rx=%.2f ms missing=%d last_comm=%s",
            self.packets_received,
            interval_packets,
            self._last_packet_rate,
            self._last_data_rate_mbps,
            self._last_avg_receive_time_ms,
            max_receive_ms,
            self.missing_packets,
            self._last_comm_count,
        )
        self.total_data_received = 0
        self._receive_times_ms.clear()
        self._stats_packets_at_last_log = self.packets_received
        self.last_stats_time = current_time
