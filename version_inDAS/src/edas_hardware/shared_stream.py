"""Bounded, in-process phase stream. No sockets or wire serialization."""
import threading
import time
from collections import deque

import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal

from .config import DataSource, calculate_phase_point_num, calculate_cropped_point_count
from das.types import DASPacketHeader, DASRawPacket


class SharedStreamManager(QObject):
    status_changed = pyqtSignal(dict)
    statistics_changed = pyqtSignal(dict)
    availability_changed = pyqtSignal(dict)
    error_occurred = pyqtSignal(str)
    MEMORY_BUDGET = 1024 * 1024 * 1024

    def __init__(self):
        super().__init__()
        self.sink = None
        self._enabled = True
        self._session_ready = False
        self._running = True
        self._busy = False
        self._condition = threading.Condition()
        self._queue = deque()
        self._bytes = 0
        self._parts = []
        self._frames = 0
        self._count = 0
        self._sent = 0
        self._total_bytes = 0
        self._thread = threading.Thread(target=self._loop, name='eDAS-shared-stream', daemon=True)
        self._thread.start()

    def update_enabled(self, enabled, params):
        self._enabled = bool(enabled)
        self.publish_availability(params)

    def publish_availability(self, params):
        available = params.upload.channel_num == 1 and params.upload.data_source == DataSource.PHASE
        reason = '共享原始相位：单上传通道 PHASE 模式' if available else '共享要求单上传通道、PHASE 模式；其他模式仍可独立采集/存储'
        self.availability_changed.emit({'available': available, 'reason': reason})
        return available

    def start_session(self, params):
        self.stop_session()
        self._count = self._sent = self._total_bytes = 0
        self._session_ready = self.publish_availability(params)
        self.status_changed.emit({'state': 'shared', 'message': '进程内共享，无 TCP', 'connected': self._session_ready})
        return self._session_ready

    def stop_session(self):
        with self._condition:
            self._session_ready = False
            # Drain all accepted complete blocks before resetting the aggregator.
            while self._queue or self._busy:
                self._condition.wait(0.1)
        if self._frames:
            self.error_occurred.emit(f'采集停止：共享尾部 {self._frames} 帧不足一个对齐周期，未写入联合文件；原始尾部仅在启用DAS独立存储时保存')
        self._parts.clear()
        self._frames = 0

    def shutdown(self):
        self.stop_session()
        with self._condition:
            self._running = False
            self._condition.notify_all()
        self._thread.join(5)
        if self._thread.is_alive():
            raise RuntimeError('共享线程未能停止')

    def enqueue_phase_data(self, data, params, settings):
        if not self._session_ready:
            return
        # Keep numbering continuous while sharing is off. Enable is a routing gate,
        # never a reset of the acquisition clock.
        snapshot = dict(settings, enabled=self._enabled)
        size = int(data.nbytes)
        with self._condition:
            if self._bytes + size > self.MEMORY_BUDGET:
                self._session_ready = False
                self.error_occurred.emit('共享缓存超过 1 GiB；停止采集以显式报告数据链路过载')
                return
            self._queue.append((data, params, snapshot))
            self._bytes += size
            self._condition.notify_all()

    def _loop(self):
        while True:
            with self._condition:
                while not self._queue and self._running:
                    self._condition.wait()
                if not self._running and not self._queue:
                    return
                data, params, settings = self._queue.popleft()
                self._busy = True
            try:
                self._consume(data, params, settings)
            except Exception as exc:
                self._session_ready = False
                self.error_occurred.emit(f'共享数据链路故障：{exc}')
            finally:
                with self._condition:
                    self._bytes -= int(data.nbytes)
                    self._busy = False
                    self._condition.notify_all()

    def _consume(self, data, params, settings):
        width = calculate_cropped_point_count(calculate_phase_point_num(
            params.basic.point_num_per_scan, params.phase_demod.merge_point_num),
            params.phase_demod.crop_distance_start, params.phase_demod.crop_distance_end)
        raw = np.asarray(data).reshape(-1, width)
        start = max(0, int(settings.get('channel_start', 0)))
        end = min(width - 1, int(settings.get('channel_end', width - 1)))
        if end < start:
            raise ValueError('共享通道范围超出采集范围')
        spatial_step = max(1, int(settings.get('space_downsample', 1)))
        temporal_step = max(1, int(settings.get('time_downsample', 1)))
        period_frames = max(1, int(settings.get('comm_frames', params.basic.scan_rate)))
        # A single interval must fit within a bounded working set.
        interval_bytes = period_frames * ((end - start) // spatial_step + 1) * 4
        if interval_bytes > self.MEMORY_BUDGET:
            raise ValueError('共享周期数据超过 1 GiB；请缩短周期或减少通道')
        self._parts.append(raw[:, start:end + 1:spatial_step])
        self._frames += raw.shape[0]
        while self._frames >= period_frames:
            remaining, pieces = period_frames, []
            while remaining:
                part = self._parts[0]
                take = min(remaining, part.shape[0])
                pieces.append(part[:take])
                if take == part.shape[0]: self._parts.pop(0)
                else: self._parts[0] = part[take:]
                remaining -= take
            self._frames -= period_frames
            count = self._count
            self._count += 1
            if not settings['enabled']:
                continue
            matrix = pieces[0] if len(pieces) == 1 else np.concatenate(pieces, axis=0)
            # Channel-first float32 radians match the old TCP semantics while
            # avoiding its bytes serialization and float64 expansion.
            radians = np.ascontiguousarray(matrix[::temporal_step].T, dtype=np.float32)
            radians *= np.float32(np.pi / 32767.0)
            radians.setflags(write=False)
            duration = period_frames / float(params.basic.scan_rate)
            rate = float(params.basic.scan_rate) / temporal_step
            header = DASPacketHeader(count, rate, radians.shape[0], radians.nbytes, duration)
            header.channel_start = start
            header.channel_step = spatial_step
            if self.sink is not None:
                self.sink(DASRawPacket(header, radians.reshape(-1), time.time()))
            self._sent += 1
            self._total_bytes += radians.nbytes
            self.statistics_changed.emit({'acquired_packets': self._count, 'sent_packets': self._sent,
                'last_comm_count': count, 'bytes_sent': self._total_bytes, 'channel_count': radians.shape[0],
                'sample_rate_hz': rate, 'packet_duration_seconds': duration, 'data_bytes': radians.nbytes})

    def get_diagnostics_snapshot(self):
        return {'queued_bytes': self._bytes, 'queued_blocks': len(self._queue),
                'last_comm_count': self._count - 1, 'shared_packets': self._sent}
