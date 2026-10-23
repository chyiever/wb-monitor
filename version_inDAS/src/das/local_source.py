"""Same manager-facing interface as the receiver, backed by a bounded mailbox."""
import threading
import time
import numpy as np
from collections import deque
from PyQt5.QtCore import QTimer
from .tcp_server import DASTCPServer


class LocalDASSource(DASTCPServer):
    MEMORY_BUDGET = 1024 * 1024 * 1024

    def __init__(self, panel):
        super().__init__('in-process', 0)
        self.panel = panel
        self._mailbox = deque()
        self._mailbox_bytes = 0
        self._mailbox_lock = threading.Lock()
        self._timer = QTimer(self)
        self._timer.setInterval(10)
        self._timer.timeout.connect(self._drain_one)
        panel.tcp_tab3_manager.sink = self.submit

    def start_server(self):
        if self._running:
            return True
        if self.panel.tab3_comm_enable_check.isChecked():
            expected = self.panel.fip_period_provider()
            actual = self.panel.tab3_length_comm_spin.value()
            if abs(expected - actual) > 1e-6:
                self.error_occurred.emit(f'共享周期 {actual:g}s 必须与FIP包时长 {expected:g}s 一致')
                return False
        self._reset_connection_stats()
        self._running = True
        self._connected = True
        self._last_data_time = time.time()
        self._timer.start()
        self.panel._on_start()
        ok = self.panel.acq_thread is not None and self.panel.acq_thread.isRunning()
        self._connected = ok
        if not ok:
            self._running = False
            self._timer.stop()
        self.connection_status.emit(ok, 'eDAS本机采集' if ok else 'eDAS采集未启动')
        return ok

    def stop_server(self):
        if self.panel.acq_thread is not None:
            self.panel._on_stop()
        self._timer.stop()
        while self._drain_one():
            pass
        self._running = self._connected = False
        self.connection_status.emit(False, 'eDAS采集已停止')

    def last_data_age_seconds(self):
        if self.panel.acq_thread is not None and self.panel.acq_thread.isRunning():
            return 0.0
        return super().last_data_age_seconds()

    def submit(self, packet):
        if not self._running:
            return
        if abs(packet.header.packet_duration_seconds - self.panel.fip_period_provider()) > 1e-6:
            raise ValueError('共享周期与FIP包时长不一致，请停止两路后统一参数')
        with self._mailbox_lock:
            packet_bytes = self._packet_bytes(packet)
            if self._mailbox_bytes + packet_bytes > self.MEMORY_BUDGET:
                raise BufferError('eDAS路由缓存超过1 GiB，接收端消费速度不足')
            self._mailbox.append(packet)
            self._mailbox_bytes += packet_bytes

    def _drain_one(self):
        with self._mailbox_lock:
            if not self._mailbox:
                return False
            packet = self._mailbox.popleft()
            self._mailbox_bytes -= self._packet_bytes(packet)
        self._last_data_time = time.time()
        self.packets_received += 1
        count = packet.header.comm_count
        if self._last_comm_count is not None and count > self._last_comm_count + 1:
            self.missing_packets += count - self._last_comm_count - 1
        self._last_comm_count = count
        self.total_data_received += packet.data_1d.nbytes
        self.header_updated.emit(vars(packet.header))
        self.packet_received.emit(packet)
        self.statistics_updated.emit({'packets_received': self.packets_received,
            'total_data_received': self.total_data_received, 'missing_packets': self.missing_packets})
        return True

    @staticmethod
    def _packet_bytes(packet):
        raw = packet.raw_matrix
        extra = raw.nbytes if raw is not None and not np.shares_memory(raw, packet.data_1d) else 0
        return packet.data_1d.nbytes + extra
