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
        self.packets_received = 0
        self.logger = logging.getLogger(f"{__name__}.DASTCPServer")

    def start_server(self) -> bool:
        """Start the TCP server."""
        try:
            if self._running:
                return True
            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((self.ip, self.port))
            self.server_socket.listen(1)
            self._running = True
            self._server_thread = threading.Thread(target=self._server_loop, daemon=True)
            self._server_thread.start()
            self.connection_status.emit(False, f"DAS server started on {self.ip}:{self.port}")
            return True
        except Exception as exc:
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
                client_socket, client_address = self.server_socket.accept()
                self.client_socket = client_socket
                self.client_address = client_address
                self.client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                self.client_socket.settimeout(1.0)
                self._connected = True
                self.packets_received = 0
                self._last_data_time = 0.0
                self.connection_status.emit(True, f"DAS connected to {client_address}")
                self._receive_loop()
            except Exception as exc:
                if self._running:
                    self.logger.error("DAS server loop error: %s", exc)
        self._connected = False

    def _receive_loop(self) -> None:
        while self._running and self._connected and self.client_socket:
            try:
                header_bytes = self._recv_exact(self.HEADER_STRUCT.size)
                if not header_bytes:
                    continue
                comm_count, sample_rate_hz, channel_count, data_bytes, packet_duration_seconds = self.HEADER_STRUCT.unpack(header_bytes)
                if data_bytes <= 0 or data_bytes % 8 != 0:
                    self.error_occurred.emit(f"Invalid DAS data_bytes: {data_bytes}")
                    continue
                payload = self._recv_exact(data_bytes)
                if not payload:
                    continue
                data = np.frombuffer(payload, dtype=">f8").astype(np.float64, copy=False)
                header = DASPacketHeader(
                    comm_count=comm_count,
                    sample_rate_hz=sample_rate_hz,
                    channel_count=channel_count,
                    data_bytes=data_bytes,
                    packet_duration_seconds=packet_duration_seconds,
                )
                expected_points = int(round(sample_rate_hz * packet_duration_seconds * channel_count))
                if expected_points != len(data):
                    self.error_occurred.emit(
                        f"DAS payload length mismatch: expected={expected_points}, actual={len(data)}"
                    )
                    continue
                packet = DASRawPacket(header=header, data_1d=data)
                self.packets_received += 1
                self._last_data_time = time.time()
                self.header_updated.emit(
                    {
                        "channel_count": channel_count,
                        "sample_rate_hz": sample_rate_hz,
                        "data_bytes": data_bytes,
                        "packet_duration_seconds": packet_duration_seconds,
                        "comm_count": comm_count,
                    }
                )
                self.statistics_updated.emit({"packets_received": self.packets_received, "connected": True})
                self.packet_received.emit(packet)
            except socket.timeout:
                continue
            except Exception as exc:
                if self._running:
                    self.logger.error("DAS receive error: %s", exc)
                    self.error_occurred.emit(f"DAS receive error: {exc}")
                break
        self._connected = False
        self.connection_status.emit(False, "DAS disconnected")
        self.statistics_updated.emit({"packets_received": self.packets_received, "connected": False})

    def _recv_exact(self, size: int) -> Optional[bytes]:
        if not self.client_socket:
            return None
        chunks = bytearray()
        remaining = size
        while remaining > 0 and self._running:
            try:
                chunk = self.client_socket.recv(min(65536, remaining))
                if not chunk:
                    return None
                chunks.extend(chunk)
                remaining -= len(chunk)
            except socket.timeout:
                continue
            except OSError:
                return None
        return bytes(chunks) if len(chunks) == size else None
