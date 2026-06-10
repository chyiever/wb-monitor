"""Trigger storage worker for the independent FIP Tab2 pipeline."""

from __future__ import annotations

import csv
import logging
from collections import deque
from datetime import datetime
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Deque, Dict, List, Tuple

import numpy as np
from PyQt5.QtCore import QThread

from .fip_types import FIPFeatureFrame, FIPTab2InputPacket, FIPTriggerSaveRequest


class FIPTriggerStorageWorker(QThread):
    """Persist trigger-centered signal and feature snippets to NPZ."""

    def __init__(self, storage_path: str = "D:/PCCP/FIPmonitor") -> None:
        super().__init__()
        self.logger = logging.getLogger(f"{__name__}.FIPTriggerStorageWorker")
        self.signal_queue: "Queue[FIPTab2InputPacket]" = Queue(maxsize=64)
        self.feature_queue: "Queue[FIPFeatureFrame]" = Queue(maxsize=256)
        self.request_queue: "Queue[FIPTriggerSaveRequest]" = Queue(maxsize=32)
        self.running = False
        # 排空模式标志：begin_drain() 后不再接受新包，run 循环排空后退出（T2-02）
        self._drain_mode = False

        self.storage_path = Path(storage_path)
        self.storage_path.mkdir(parents=True, exist_ok=True)

        self._signal_packets: Deque[FIPTab2InputPacket] = deque()
        self._feature_frames: Deque[FIPFeatureFrame] = deque()
        self._pending_requests: Deque[FIPTriggerSaveRequest] = deque()
        self._latest_time = 0.0
        self._max_history_seconds = 30.0

    def enqueue_signal_packet(self, packet: FIPTab2InputPacket) -> bool:
        """Queue one filtered signal packet for trigger storage."""
        return self._enqueue(self.signal_queue, packet)

    def enqueue_feature_frame(self, frame: FIPFeatureFrame) -> bool:
        """Queue one feature frame for trigger storage."""
        return self._enqueue(self.feature_queue, frame)

    def enqueue_trigger_request(self, request: FIPTriggerSaveRequest) -> bool:
        """Queue one trigger save request."""
        return self._enqueue(self.request_queue, request)

    def update_storage_settings(self, settings: Dict[str, float]) -> None:
        """Update output path used by the trigger storage worker."""
        path = settings.get("path")
        if path:
            self.storage_path = Path(path)
            self.storage_path.mkdir(parents=True, exist_ok=True)

    def reset_state(self) -> None:
        """清除缓存的信号包、特征帧和待处理请求，重置排空状态。"""
        self._signal_packets.clear()
        self._feature_frames.clear()
        self._pending_requests.clear()
        self._latest_time = 0.0
        self._drain_mode = False  # 下次启动前重置排空标志

    def run(self) -> None:
        """消费信号包、特征帧和触发存储请求。

        排空模式（_drain_mode=True）：
            停止接受新包后继续排空三个队列中已有的内容，
            以及 _pending_requests 中等待后触发时间窗口的请求。
            所有请求处理完毕后自行退出，保证停止时触发事件不被截断。
        """
        self.running = True
        self.logger.info("FIP trigger storage worker started")
        while self.running:
            handled = False
            handled |= self._drain_queue(self.signal_queue, self._handle_signal_packet)
            handled |= self._drain_queue(self.feature_queue, self._handle_feature_frame)
            handled |= self._drain_queue(self.request_queue, self._handle_request)
            self._flush_pending_requests()
            # 排空模式：三个队列都空且没有待处理请求时退出
            if self._drain_mode and not self.has_pending_work():
                self.logger.info("FIP trigger storage drain complete, exiting")
                break
            if not handled:
                self.msleep(50)

    def stop(self) -> None:
        """停止存储工作线程。

        若已进入排空模式（begin_drain 已调用），run 循环会在处理完
        所有待存储请求后自行退出；否则立即退出，可能丢弃未完成请求。
        建议先调用 begin_drain()，等待排空后再调用 stop()。
        """
        self.running = False

    def begin_drain(self) -> None:
        """进入排空模式，停止接受新包（T2-02 两阶段停止）。

        调用后 run 循环继续消费三个队列中已有的内容并尝试
        完成 _pending_requests，完成后自行退出。
        """
        self._drain_mode = True
        self.logger.info(
            "FIP trigger storage entering drain mode: "
            "signal_q=%d, feature_q=%d, request_q=%d, pending=%d",
            self.signal_queue.qsize(),
            self.feature_queue.qsize(),
            self.request_queue.qsize(),
            len(self._pending_requests),
        )

    def has_pending_work(self) -> bool:
        """判断是否还有未处理的工作（供调用方轮询排空进度）。

        Returns:
            True 表示仍有队列包或待落盘请求，False 表示已全部处理完毕。
        """
        return (
            not self.signal_queue.empty()
            or not self.feature_queue.empty()
            or not self.request_queue.empty()
            or len(self._pending_requests) > 0
        )

    def _handle_signal_packet(self, packet: FIPTab2InputPacket) -> None:
        self._signal_packets.append(packet)
        self._latest_time = max(self._latest_time, self._packet_end_time(packet))
        self._trim_signal_history()

    def _handle_feature_frame(self, frame: FIPFeatureFrame) -> None:
        self._feature_frames.append(frame)
        self._latest_time = max(self._latest_time, frame.end_time)
        self._trim_feature_history()

    def _handle_request(self, request: FIPTriggerSaveRequest) -> None:
        self._pending_requests.append(request)

    def _flush_pending_requests(self) -> None:
        remaining: Deque[FIPTriggerSaveRequest] = deque()
        while self._pending_requests:
            request = self._pending_requests.popleft()
            ready_time = request.event.start_time + request.post_trigger_seconds
            if self._latest_time >= ready_time:
                self._save_request(request)
            else:
                remaining.append(request)
        self._pending_requests = remaining

    def _save_request(self, request: FIPTriggerSaveRequest) -> None:
        event = request.event
        start_time = event.start_time - request.pre_trigger_seconds
        end_time = event.start_time + request.post_trigger_seconds
        merged_signal, merged_time, sample_rate = self._build_signal_snippet(start_time, end_time)

        feature_times = {name: [] for name in request.enabled_feature_names}
        feature_values = {name: [] for name in request.enabled_feature_names}
        for frame in self._feature_frames:
            if frame.center_time < start_time or frame.center_time > end_time:
                continue
            for feature_name in request.enabled_feature_names:
                if feature_name in frame.feature_values:
                    feature_times[feature_name].append(frame.center_time)
                    feature_values[feature_name].append(frame.feature_values[feature_name])

        now = datetime.now()
        filename = f"an-{now.strftime('%Y%m%d-%H%M%S.%f')[:-3]}.npz"
        file_path = self.storage_path / filename

        np.savez_compressed(
            file_path,
            signal=merged_signal,
            signal_time=merged_time,
            sample_rate=sample_rate,
            event_start_time=event.start_time,
            event_end_time=event.end_time,
            event_duration=event.duration,
            trigger_feature_names=np.array(event.trigger_feature_names, dtype=object),
            trigger_feature_count=event.trigger_feature_count,
            feature_times=np.array(feature_times, dtype=object),
            feature_values=np.array(feature_values, dtype=object),
        )
        self._append_event_details_csv(request, filename)

    def _build_signal_snippet(self, start_time: float, end_time: float) -> Tuple[np.ndarray, np.ndarray, float]:
        """Assemble the trigger-centered signal snippet from cached packets."""
        signal_segments: List[np.ndarray] = []
        signal_times: List[np.ndarray] = []
        sample_rate = 0.0

        for packet in self._signal_packets:
            packet_start = packet.timestamp
            packet_end = self._packet_end_time(packet)
            if packet_end <= start_time or packet_start >= end_time:
                continue

            sample_rate = packet.sample_rate
            overlap_start = max(start_time, packet_start)
            overlap_end = min(end_time, packet_end)
            start_index = max(0, int(round((overlap_start - packet_start) * packet.sample_rate)))
            end_index = min(len(packet.data), int(round((overlap_end - packet_start) * packet.sample_rate)))
            segment = packet.data[start_index:end_index]
            if len(segment) == 0:
                continue

            segment_time = packet_start + np.arange(start_index, end_index, dtype=np.float64) / packet.sample_rate
            signal_segments.append(segment)
            signal_times.append(segment_time)

        merged_signal = np.concatenate(signal_segments) if signal_segments else np.array([], dtype=np.float64)
        merged_time = np.concatenate(signal_times) if signal_times else np.array([], dtype=np.float64)
        return merged_signal, merged_time, sample_rate

    def _trim_signal_history(self) -> None:
        cutoff = self._latest_time - self._max_history_seconds
        while self._signal_packets:
            if self._packet_end_time(self._signal_packets[0]) >= cutoff:
                break
            self._signal_packets.popleft()

    def _trim_feature_history(self) -> None:
        cutoff = self._latest_time - self._max_history_seconds
        while self._feature_frames and self._feature_frames[0].end_time < cutoff:
            self._feature_frames.popleft()

    @staticmethod
    def _packet_end_time(packet: FIPTab2InputPacket) -> float:
        """Return the end time of one cached signal packet."""
        return packet.timestamp + (len(packet.data) / packet.sample_rate)

    def _append_event_details_csv(self, request: FIPTriggerSaveRequest, npz_filename: str) -> None:
        """Append a detailed per-feature event log to CSV."""
        csv_path = self.storage_path / "alarm_details.csv"
        needs_header = not csv_path.exists()
        with csv_path.open("a", newline="", encoding="utf-8") as csv_file:
            writer = csv.writer(csv_file)
            if needs_header:
                writer.writerow([
                    "event_id",
                    "npz_filename",
                    "event_start_time",
                    "event_end_time",
                    "event_duration",
                    "window_index",
                    "feature_name",
                    "feature_value",
                    "baseline",
                    "threshold",
                    "triggered",
                ])
            for window_result in request.event.window_results:
                for feature_name, feature_value in window_result.frame.feature_values.items():
                    writer.writerow([
                        request.event.event_id,
                        npz_filename,
                        f"{request.event.start_time:.6f}",
                        f"{request.event.end_time:.6f}",
                        f"{request.event.duration:.6f}",
                        window_result.frame.window_index,
                        feature_name,
                        f"{feature_value:.6f}",
                        f"{window_result.baselines.get(feature_name, 0.0):.6f}",
                        f"{window_result.thresholds.get(feature_name, 0.0):.6f}",
                        int(feature_name in window_result.triggered_features),
                    ])

    @staticmethod
    def _drain_queue(queue_obj: Queue, handler) -> bool:
        handled = False
        while True:
            try:
                item = queue_obj.get_nowait()
            except Empty:
                return handled
            handler(item)
            handled = True

    @staticmethod
    def _enqueue(queue_obj: Queue, item: object) -> bool:
        try:
            if queue_obj.full():
                try:
                    queue_obj.get_nowait()
                except Empty:
                    pass
            queue_obj.put(item, block=False)
            return True
        except Full:
            return False
