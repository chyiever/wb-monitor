"""独立存储线程，将 DASTab3Manager 的写盘操作从主线程 QTimer 中解耦。

设计背景（T3-01）：
    原实现中 _maybe_store_snapshot 由 QTimer（主线程）每秒触发并直接调用
    np.savez_compressed，每次写盘 10–200 ms，周期性阻塞 Qt 事件循环，
    导致 UI 卡顿。本模块将写盘操作移至独立 QThread，主线程仅做
    O(1) 的 put_nowait 入队，不执行任何 I/O。

增量存储（T3-02）：
    每次仅写入自上次存储结束点起的新增帧（增量 chunk），而非每次
    都获取最近 N 秒的全量快照，消除相邻文件之间 ~90% 的数据重叠，
    大幅降低写放大比和 CPU 浪费。
"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from PyQt5.QtCore import QThread, pyqtSignal


class DASStorageRequest:
    """封装一次增量存储请求的元数据与帧列表。

    Attributes:
        frames: 本次待写盘的对齐帧列表（AlignedFrame 实例）。
        output_dir: 目标存储目录（字符串路径）。
        end_comm: 本批最后一帧的 comm_count，用于更新 _last_snapshot_end_comm。
    """

    def __init__(self, frames: List[Any], output_dir: str, end_comm: int) -> None:
        self.frames = frames
        self.output_dir = output_dir
        self.end_comm = end_comm


class DASStorageWorker(QThread):
    storage_saved = pyqtSignal(str)

    """独立线程，消费存储请求并执行 np.savez_compressed 写盘。

    主线程通过 enqueue_request(request) 将 DASStorageRequest 放入队列，
    本线程在后台持续消费，不阻塞主线程的 Qt 事件循环。

    队列设计：
        maxsize=32。若写盘速度持续慢于存储定时器产生请求的速度
        （通常 1 Hz），队列会堆积，此时丢弃最旧的请求（覆写最旧策略），
        保证内存不无限增长。

    使用方法：
        worker = DASStorageWorker()
        worker.start()
        worker.enqueue_request(request)   # 主线程调用，非阻塞
        worker.stop()
        worker.wait(5000)
    """

    INPUT_QUEUE_MAXSIZE = 32

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(f"{__name__}.DASStorageWorker")
        # 存储请求队列：主线程 put_nowait，本线程消费（T3-01）
        self._queue: Queue = Queue(maxsize=self.INPUT_QUEUE_MAXSIZE)
        self.running = False

        # 统计信息
        self.stats: Dict[str, int] = {
            "requests_enqueued": 0,
            "requests_saved": 0,
            "requests_dropped": 0,
            "save_failures": 0,
        }

    def enqueue_request(self, request: DASStorageRequest) -> None:
        """将存储请求放入队列（主线程调用，必须非阻塞）。

        Args:
            request: 封装了帧列表和输出路径的存储请求。

        若队列满（写盘速度跟不上），丢弃最旧的请求并记录告警。
        主线程绝不阻塞。
        """
        try:
            if self._queue.full():
                # 覆写最旧请求，保护主线程
                try:
                    self._queue.get_nowait()
                    self.stats["requests_dropped"] += 1
                    self.logger.warning(
                        "DAS storage queue full, dropped oldest request. "
                        "Disk may be too slow."
                    )
                except Empty:
                    pass
            self._queue.put_nowait(request)
            self.stats["requests_enqueued"] += 1
        except Full:
            self.stats["requests_dropped"] += 1
            self.logger.error("DAS storage queue full, failed to enqueue request.")

    def run(self) -> None:
        """存储循环：从队列取请求并写盘。"""
        self.running = True
        self.logger.info("DAS storage worker started")
        while self.running or not self._queue.empty():
            try:
                request = self._queue.get(timeout=0.2)
                self._save(request)
            except Empty:
                continue
            except Exception as exc:
                self.stats["save_failures"] += 1
                self.logger.error("DAS storage worker error: %s", exc)

    def stop(self) -> None:
        """停止存储线程；run 循环会先排空已入队请求再退出。"""
        self.running = False
        self.logger.info("DAS storage worker stopping, stats=%s", self.stats)

    def _save(self, request: DASStorageRequest) -> None:
        """执行实际的 npz 写盘操作（在存储线程中调用）。

        Args:
            request: 包含帧列表和输出目录的存储请求。

        文件命名：FIPeDAS-YYYYMMDD-HHMMSS.mmm.npz
        """
        try:
            frames = request.frames
            if not frames:
                return

            output_path = Path(request.output_dir)
            output_path.mkdir(parents=True, exist_ok=True)
            now = datetime.now()
            file_path = output_path / f"FIPeDAS-{now.strftime('%Y%m%d-%H%M%S.%f')[:-3]}.npz"

            # 构建 payload：结构与原实现保持兼容，增加 incremental=True 标记
            payload = {
                "comm_counts": np.array([f.comm_count for f in frames], dtype=np.int32),
                "packet_start_times": np.array(
                    [f.packet_start_time for f in frames], dtype=np.float64
                ),
                "packet_duration_seconds": np.array(
                    [f.packet_duration_seconds for f in frames], dtype=np.float64
                ),
                "fip_present": np.array([not f.fip_missing for f in frames], dtype=bool),
                "das_present": np.array([not f.das_missing for f in frames], dtype=bool),
                "fip_raw_200khz": np.array(
                    [
                        f.fip_packet.unwrapped_data
                        if f.fip_packet is not None
                        else np.array([], dtype=np.float64)
                        for f in frames
                    ],
                    dtype=object,
                ),
                "fip_display_data": np.array(
                    [
                        f.fip_packet.display_data
                        if f.fip_packet is not None
                        else np.array([], dtype=np.float64)
                        for f in frames
                    ],
                    dtype=object,
                ),
                "das_raw_matrix": np.array(
                    [
                        f.das_packet.matrix
                        if f.das_packet is not None
                        else np.array([], dtype=np.float64)
                        for f in frames
                    ],
                    dtype=object,
                ),
                "fip_sample_rate_hz": np.array(
                    [
                        f.fip_packet.sample_rate_hz
                        if f.fip_packet is not None
                        else np.nan
                        for f in frames
                    ],
                    dtype=np.float64,
                ),
                "das_sample_rate_hz": np.array(
                    [
                        f.das_packet.sample_rate_hz
                        if f.das_packet is not None
                        else np.nan
                        for f in frames
                    ],
                    dtype=np.float64,
                ),
                "das_channel_count": np.array(
                    [
                        f.das_packet.channel_count
                        if f.das_packet is not None
                        else 0
                        for f in frames
                    ],
                    dtype=np.int32,
                ),
                # incremental=True 表示本文件是增量 chunk，不是全量快照（T3-02）
                "incremental": np.bool_(True),
                "format_version": np.array("wb-monitor-joint-v2"),
                "created_at": np.array(now.isoformat(timespec="milliseconds")),
            }

            np.savez_compressed(file_path, **payload)
            self.stats["requests_saved"] += 1
            self.storage_saved.emit(str(file_path))
            self.logger.info(
                "DAS storage saved %d frames to %s", len(frames), file_path.name
            )
        except Exception as exc:
            self.stats["save_failures"] += 1
            self.logger.error("DAS storage _save failed: %s", exc)


class EDASRawStorageRequest:
    """One eDAS-only raw block queued for binary storage."""

    def __init__(
        self,
        packet: Any,
        output_dir: str,
        blocks_per_file: int,
        queue_packets: int,
    ) -> None:
        self.packet = packet
        self.output_dir = output_dir
        self.blocks_per_file = max(1, int(blocks_per_file))
        self.queue_packets = max(1, int(queue_packets))


class EDASRawStorageWorker(QThread):
    """Background writer for Tab3 eDAS-only raw storage.

    The writer follows the same engineering model as the PCIe-7821 eDAS saver:
    the GUI/receiver thread only enqueues complete blocks, while this thread owns
    file I/O, file rotation, and metadata persistence.
    """

    storage_status = pyqtSignal(str)

    INPUT_QUEUE_MAXSIZE = 4096

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(f"{__name__}.EDASRawStorageWorker")
        self._queue: Queue = Queue(maxsize=self.INPUT_QUEUE_MAXSIZE)
        self.running = False
        self._file_handle = None
        self._current_file_path: Optional[Path] = None
        self._current_metadata_path: Optional[Path] = None
        self._current_metadata: Optional[Dict[str, Any]] = None
        self._current_key: Optional[Tuple[str, int, int, int, int]] = None
        self._blocks_in_file = 0
        self._bytes_in_file = 0
        self._file_index = 0
        self.stats: Dict[str, int] = {
            "blocks_enqueued": 0,
            "blocks_saved": 0,
            "blocks_dropped": 0,
            "save_failures": 0,
            "files_created": 0,
            "bytes_written": 0,
        }

    def enqueue_packet(
        self,
        packet: Any,
        output_dir: str,
        blocks_per_file: int,
        queue_packets: int,
    ) -> bool:
        """Queue one parsed eDAS packet for binary storage without blocking."""
        request = EDASRawStorageRequest(packet, output_dir, blocks_per_file, queue_packets)
        capacity = max(1, min(request.queue_packets, self.INPUT_QUEUE_MAXSIZE))
        try:
            while self._queue.qsize() >= capacity:
                try:
                    self._queue.get_nowait()
                    self.stats["blocks_dropped"] += 1
                    self.logger.warning(
                        "eDAS raw storage queue full, dropped oldest packet. "
                        "Disk may be too slow."
                    )
                except Empty:
                    break
            self._queue.put_nowait(request)
            self.stats["blocks_enqueued"] += 1
            return True
        except Full:
            self.stats["blocks_dropped"] += 1
            self.logger.error("eDAS raw storage queue full, failed to enqueue packet.")
            return False

    def run(self) -> None:
        self.running = True
        self.logger.info("eDAS raw storage worker started")
        while self.running or not self._queue.empty():
            try:
                request = self._queue.get(timeout=0.2)
                self._write_request(request)
            except Empty:
                continue
            except Exception as exc:
                self.stats["save_failures"] += 1
                self.logger.error("eDAS raw storage worker error: %s", exc)
        self._close_current_file(closed_at=datetime.now().isoformat(timespec="milliseconds"))
        self.running = False
        self.logger.info("eDAS raw storage worker stopped, stats=%s", self.stats)

    def stop(self) -> None:
        """Stop accepting new loop iterations; run() drains already queued packets."""
        self.running = False
        self.logger.info("eDAS raw storage worker stopping, stats=%s", self.stats)

    def reset_session(self) -> None:
        """Clear pending packets and close any active file before a new session."""
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except Empty:
                break
        self._close_current_file(closed_at=datetime.now().isoformat(timespec="milliseconds"))
        self._current_key = None
        self._blocks_in_file = 0
        self._bytes_in_file = 0
        self._file_index = 0

    def _write_request(self, request: EDASRawStorageRequest) -> None:
        packet = request.packet
        matrix = np.asarray(packet.matrix, dtype="<f8", order="C")
        if matrix.ndim != 2 or matrix.size == 0:
            return

        key = self._make_key(request, matrix)
        if (
            self._file_handle is None
            or self._current_key != key
            or self._blocks_in_file >= request.blocks_per_file
        ):
            self._open_new_file(request, matrix, key)

        payload = matrix.tobytes(order="C")
        self._file_handle.write(payload)
        self._blocks_in_file += 1
        self._bytes_in_file += len(payload)
        self.stats["blocks_saved"] += 1
        self.stats["bytes_written"] += len(payload)
        self._append_metadata(packet, len(payload))
        self._write_metadata(closed_at=None)
        self.storage_status.emit(
            f"{self._current_file_path.name} blocks={self._blocks_in_file} comm={packet.header.comm_count}"
        )

    def _make_key(self, request: EDASRawStorageRequest, matrix: np.ndarray) -> Tuple[str, int, int, int, int]:
        output_dir = str(Path(request.output_dir))
        channel_count = int(matrix.shape[0])
        samples_per_channel = int(matrix.shape[1])
        sample_rate_hz = int(request.packet.header.sample_rate_hz)
        return (
            output_dir,
            sample_rate_hz,
            channel_count,
            samples_per_channel,
            int(request.blocks_per_file),
        )

    def _open_new_file(
        self,
        request: EDASRawStorageRequest,
        matrix: np.ndarray,
        key: Tuple[str, int, int, int, int],
    ) -> None:
        self._close_current_file(closed_at=datetime.now().isoformat(timespec="milliseconds"))
        output_path = Path(request.output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        self._file_index += 1
        now = datetime.now()
        timestamp = now.strftime("%Y%m%dT%H%M%S.%f")[:-3]
        channel_count = int(matrix.shape[0])
        samples_per_channel = int(matrix.shape[1])
        sample_rate_hz = int(request.packet.header.sample_rate_hz)
        filename = (
            f"{self._file_index:07d}-eDAS-{sample_rate_hz:04d}Hz-"
            f"{channel_count:04d}ch-{samples_per_channel:04d}pt-{timestamp}.bin"
        )
        self._current_file_path = output_path / filename
        self._current_metadata_path = self._current_file_path.with_suffix(".json")
        self._file_handle = open(self._current_file_path, "wb")
        self._current_key = key
        self._blocks_in_file = 0
        self._bytes_in_file = 0
        self.stats["files_created"] += 1
        data_bytes_per_block = int(request.packet.header.data_bytes)
        packet_duration_seconds = float(request.packet.header.packet_duration_seconds)
        self._current_metadata = {
            "format_version": "wb-monitor-edas-raw-v1",
            "storage_type": "edas_raw_storage",
            "data_file": self._current_file_path.name,
            "metadata_file": self._current_metadata_path.name,
            "output_dir": str(output_path),
            "file_index": int(self._file_index),
            "created_at": now.isoformat(timespec="milliseconds"),
            "closed_at": None,
            "dtype": "float64",
            "byte_order": "little",
            "array_order": "C",
            "matrix_shape_per_block": [channel_count, samples_per_channel],
            "sample_rate_hz": sample_rate_hz,
            "packet_duration_seconds": packet_duration_seconds,
            "blocks_per_file": int(request.blocks_per_file),
            "queue_packets": int(request.queue_packets),
            "data_bytes_per_block": data_bytes_per_block,
            "storage_parameters": {
                "output_dir": str(output_path),
                "blocks_per_file": int(request.blocks_per_file),
                "queue_packets": int(request.queue_packets),
                "file_split_policy": "split after blocks_per_file complete DAS packets",
                "queue_overflow_policy": "drop oldest packet before enqueueing the latest packet",
            },
            "das_parameters": {
                "sample_rate_hz": sample_rate_hz,
                "channel_count": channel_count,
                "samples_per_channel": samples_per_channel,
                "packet_duration_seconds": packet_duration_seconds,
                "data_bytes_per_block": data_bytes_per_block,
                "matrix_shape_per_block": [channel_count, samples_per_channel],
            },
            "blocks_written": 0,
            "bytes_written": 0,
            "comm_counts": [],
            "packet_start_times": [],
            "packet_end_times": [],
            "block_bytes": [],
        }
        self._write_metadata(closed_at=None)
        self.storage_status.emit(f"Started {self._current_file_path.name}")

    def _append_metadata(self, packet: Any, block_bytes: int) -> None:
        if self._current_metadata is None:
            return
        self._current_metadata["blocks_written"] = self._blocks_in_file
        self._current_metadata["bytes_written"] = self._bytes_in_file
        self._current_metadata["comm_counts"].append(int(packet.header.comm_count))
        self._current_metadata["packet_start_times"].append(float(packet.packet_start_time))
        self._current_metadata["packet_end_times"].append(float(packet.packet_end_time))
        self._current_metadata["block_bytes"].append(int(block_bytes))

    def _write_metadata(self, closed_at: Optional[str]) -> None:
        if self._current_metadata_path is None or self._current_metadata is None:
            return
        metadata = dict(self._current_metadata)
        metadata["closed_at"] = closed_at
        metadata["blocks_written"] = self._blocks_in_file
        metadata["bytes_written"] = self._bytes_in_file
        self._current_metadata_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _close_current_file(self, closed_at: Optional[str]) -> None:
        if self._file_handle is not None:
            self._file_handle.flush()
            self._file_handle.close()
            self._file_handle = None
        if self._current_metadata_path is not None and self._current_metadata is not None:
            self._write_metadata(closed_at=closed_at)
        self._current_file_path = None
        self._current_metadata_path = None
        self._current_metadata = None
