"""
Optimized TCP Server Module for PCCP Monitoring System

Simplified and efficient TCP server for high-throughput data reception.
Based on reference TCP.py implementation.

Author: Claude
Date: 2026-03-11
Updated: 2026-03-12 - Performance optimizations for packet loss prevention
"""

import socket
import struct
import threading
import time
import logging
import numpy as np
import psutil
import os
from typing import Optional, Tuple
from PyQt5.QtCore import QObject, pyqtSignal


class DataPacket:
    """Data packet structure"""
    def __init__(self, data_array: np.ndarray, timestamp: float, packet_count: int):
        self.phase_data = data_array
        self.timestamp = timestamp
        self.comm_count = packet_count
        self.data_size = len(data_array) * 8


COMM_INTERVAL = 0.2  # 每次通信间隔（秒），发送方固定 5Hz


class OptimizedTCPServer(QObject):
    """Optimized TCP Server for PCCP data reception"""

    # Signals
    data_received = pyqtSignal(DataPacket)
    connection_status = pyqtSignal(bool, str)
    error_occurred = pyqtSignal(str)

    def __init__(self, ip: str = "0.0.0.0", port: int = 3677):
        super().__init__()

        self.ip = ip
        self.port = port

        # Sockets
        self.server_socket: Optional[socket.socket] = None
        self.client_socket: Optional[socket.socket] = None
        self.client_address: Optional[Tuple[str, int]] = None

        # Control flags
        self._running = False
        self._connected = False
        self._server_thread: Optional[threading.Thread] = None

        # Statistics
        self.packets_received = 0
        self.total_data_received = 0
        self.last_stats_time = time.time()

        # Performance monitoring
        self.performance_stats = {
            'receive_times': [],
            'tcp_queue_sizes': [],
            'last_monitor_time': time.time()
        }

        self.logger = logging.getLogger(__name__)

    def start_server(self) -> bool:
        """Start TCP server"""
        try:
            if self._running:
                return True

            self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.server_socket.bind((self.ip, self.port))
            self.server_socket.listen(1)

            self._running = True
            self.logger.info(f"TCP Server started on {self.ip}:{self.port}")

            self._server_thread = threading.Thread(target=self._server_loop, daemon=True)
            self._server_thread.start()

            self.connection_status.emit(True, f"Server started on {self.ip}:{self.port}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to start server: {e}")
            self.error_occurred.emit(f"Failed to start server: {e}")
            return False

    def stop_server(self):
        """Stop TCP server"""
        self._running = False

        if self.client_socket:
            try:
                self.client_socket.close()
            except:
                pass
            self.client_socket = None

        if self.server_socket:
            try:
                self.server_socket.close()
            except:
                pass
            self.server_socket = None

        self._connected = False
        self.connection_status.emit(False, "Server stopped")

    def _server_loop(self):
        """Main server loop"""
        while self._running and self.server_socket:
            try:
                self.logger.info("Waiting for client connection...")
                self.connection_status.emit(False, "Waiting for connection...")

                self.client_socket, self.client_address = self.server_socket.accept()

                # Optimize TCP settings for high-throughput data reception
                self.client_socket.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)

                # Increase TCP receive buffer to 8MB (was 1MB) to prevent packet loss
                self.client_socket.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 8388608)  # 8MB

                # Get actual buffer size for monitoring
                actual_buffer_size = self.client_socket.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
                self.logger.info(f"TCP receive buffer set to {actual_buffer_size} bytes")

                self._connected = True
                self.logger.info(f"Client connected from {self.client_address}")
                self.connection_status.emit(True, f"Connected to {self.client_address}")

                # Reset statistics on new connection
                self.packets_received = 0
                self.total_data_received = 0
                self.last_stats_time = time.time()
                self.performance_stats = {
                    'receive_times': [],
                    'tcp_queue_sizes': [],
                    'last_monitor_time': time.time()
                }

                self._receive_loop()

            except Exception as e:
                if self._running:
                    self.logger.error(f"Server error: {e}")
                    self._handle_disconnection()

    def _receive_loop(self):
        """Data reception loop - simplified without duplicate packet detection"""
        while self._running and self._connected and self.client_socket:
            try:
                packet_start_time = time.time()
                self.client_socket.settimeout(1.0)

                # Monitor TCP receive queue size
                self._monitor_tcp_queue()

                # Receive 8-byte header: comm_count + data_length
                header_bytes = self._recv_exact(8)
                if not header_bytes:
                    time.sleep(0.001)
                    continue

                comm_count, data_length = struct.unpack('>II', header_bytes)

                # Basic validation only
                if data_length == 0 or data_length > 10000000:
                    self.logger.warning(f"Invalid data length: {data_length}")
                    continue

                # Receive data body
                data_buff = self._recv_exact(data_length)
                if not data_buff:
                    self.logger.warning("Failed to receive complete data packet")
                    continue

                # Process data - no duplicate checking, accept all valid packets
                packet = self._process_data(data_buff, data_length, comm_count)
                if packet:
                    self.packets_received += 1
                    self.total_data_received += data_length

                    # Record performance metrics
                    packet_time = (time.time() - packet_start_time) * 1000.0
                    self.performance_stats['receive_times'].append(packet_time)

                    # Keep only recent performance data (last 100 packets)
                    if len(self.performance_stats['receive_times']) > 100:
                        self.performance_stats['receive_times'] = self.performance_stats['receive_times'][-100:]

                    self.data_received.emit(packet)

                    # Periodic performance reporting (every 50 packets)
                    if self.packets_received % 50 == 0:
                        self._log_performance_stats()

            except socket.timeout:
                continue
            except Exception as e:
                if self._running:
                    self.logger.error(f"Receive error: {e}")
                    break

        self._handle_disconnection()

    def _recv_exact(self, size: int) -> Optional[bytes]:
        """Receive exact number of bytes"""
        if not self.client_socket:
            return None

        data = bytearray()
        bytes_needed = size

        while bytes_needed > 0 and self._running:
            try:
                chunk_size = min(bytes_needed, 65536)  # 64KB chunks
                chunk = self.client_socket.recv(chunk_size)
                if not chunk:
                    return None
                data.extend(chunk)
                bytes_needed -= len(chunk)
            except socket.timeout:
                continue
            except socket.error:
                return None

        return bytes(data) if len(data) == size else None

    def _process_data(self, data_buff: bytes, data_length: int, comm_count: int) -> Optional[DataPacket]:
        """Process received data"""
        try:
            # 调试日志：记录接收到的数据
            self.logger.info(f"Processing data: length={data_length}, comm_count={comm_count}")

            # Check data length (must be multiple of 8 for <32,32> fixed point)
            if data_length % 8 != 0:
                self.logger.warning(f"Invalid data length: {data_length} (not multiple of 8)")
                return None

            # Parse <32,32> fixed point format (big endian)
            point_count = data_length // 8
            raw_values = struct.unpack(f'>{point_count}q', data_buff)

            # Convert to float (<32,32> format: divide by 2^32)
            data_array = np.array(raw_values, dtype=np.float64) / (2**32)

            # 调试日志：记录解析结果
            self.logger.info(f"Data parsed successfully: {point_count} points, range=[{np.min(data_array):.3f}, {np.max(data_array):.3f}]")

            # timestamp 仅作为绘图缓冲区的起始提示（seconds），
            # 绘图缓冲区会以实际样本数连续延伸，此值只在首包或断连重锚时有意义。
            # 用 0.0 即可，缓冲区内部自行维护连续时间轴。
            return DataPacket(data_array, 0.0, comm_count)

        except Exception as e:
            self.logger.error(f"Error processing data packet #{comm_count}: {e}")
            self.logger.error(f"Data length: {data_length}, first 32 bytes: {data_buff[:32].hex() if len(data_buff) >= 32 else data_buff.hex()}")
            return None

    def _monitor_tcp_queue(self):
        """Monitor TCP receive queue size"""
        try:
            if not self.client_socket:
                return

            # Get socket file descriptor
            sockfd = self.client_socket.fileno()

            # Try to get queue size using SIOCOUTQ (available bytes in receive buffer)
            if hasattr(socket, 'SIOCOUTQ'):
                import fcntl
                try:
                    # Get receive queue size (bytes waiting to be read)
                    queue_size = struct.unpack('I', fcntl.ioctl(sockfd, socket.SIOCOUTQ, struct.pack('I', 0)))[0]
                    self.performance_stats['tcp_queue_sizes'].append(queue_size)

                    # Keep only recent queue size data
                    if len(self.performance_stats['tcp_queue_sizes']) > 100:
                        self.performance_stats['tcp_queue_sizes'] = self.performance_stats['tcp_queue_sizes'][-100:]

                except (OSError, IOError):
                    pass
        except Exception:
            # Queue monitoring is non-critical, continue on any error
            pass

    def _log_performance_stats(self):
        """Log performance statistics"""
        try:
            current_time = time.time()
            elapsed_time = current_time - self.last_stats_time

            if elapsed_time > 0:
                # Calculate throughput
                data_rate_mbps = (self.total_data_received / elapsed_time) / (1024 * 1024)
                packet_rate = self.packets_received / elapsed_time

                # Calculate average receive time
                avg_receive_time = 0
                if self.performance_stats['receive_times']:
                    avg_receive_time = sum(self.performance_stats['receive_times']) / len(self.performance_stats['receive_times'])

                # Calculate average queue size
                avg_queue_size = 0
                if self.performance_stats['tcp_queue_sizes']:
                    avg_queue_size = sum(self.performance_stats['tcp_queue_sizes']) / len(self.performance_stats['tcp_queue_sizes'])

                # Log performance statistics
                self.logger.info(
                    f"Performance Stats - Packets: {self.packets_received}, "
                    f"Rate: {packet_rate:.1f} pkt/s, "
                    f"Throughput: {data_rate_mbps:.2f} MB/s, "
                    f"Avg RX Time: {avg_receive_time:.1f}ms, "
                    f"Avg Queue: {avg_queue_size:.0f} bytes"
                )

                # Reset counters for next interval
                self.total_data_received = 0
                self.last_stats_time = current_time

        except Exception as e:
            self.logger.warning(f"Error logging performance stats: {e}")

    def _handle_disconnection(self):
        """Handle client disconnection"""
        self._connected = False
        if self.client_socket:
            try:
                self.client_socket.close()
            except:
                pass
            self.client_socket = None
            self.client_address = None

        self.connection_status.emit(False, "Client disconnected")

    def get_statistics(self) -> dict:
        """Get connection statistics"""
        current_time = time.time()
        elapsed_time = current_time - self.last_stats_time

        # Calculate current rates
        data_rate_mbps = 0
        packet_rate = 0
        if elapsed_time > 0:
            data_rate_mbps = (self.total_data_received / elapsed_time) / (1024 * 1024)
            packet_rate = self.packets_received / elapsed_time

        # Get average performance metrics
        avg_receive_time = 0
        if self.performance_stats['receive_times']:
            avg_receive_time = sum(self.performance_stats['receive_times']) / len(self.performance_stats['receive_times'])

        avg_queue_size = 0
        max_queue_size = 0
        if self.performance_stats['tcp_queue_sizes']:
            avg_queue_size = sum(self.performance_stats['tcp_queue_sizes']) / len(self.performance_stats['tcp_queue_sizes'])
            max_queue_size = max(self.performance_stats['tcp_queue_sizes'])

        return {
            'connected': self._connected,
            'packets_received': self.packets_received,
            'total_data_received': self.total_data_received,
            'packet_rate': packet_rate,
            'data_rate_mbps': data_rate_mbps,
            'avg_receive_time_ms': avg_receive_time,
            'avg_tcp_queue_bytes': avg_queue_size,
            'max_tcp_queue_bytes': max_queue_size,
            'client_address': str(self.client_address) if self.client_address else None
        }

    def is_connected(self) -> bool:
        """Check connection status"""
        return self._connected and self.client_socket is not None