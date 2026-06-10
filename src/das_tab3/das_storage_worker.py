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

import logging
from datetime import datetime
from pathlib import Path
from queue import Empty, Full, Queue
from typing import Any, Dict, List

import numpy as np
from PyQt5.QtCore import QThread


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
        while self.running:
            try:
                request = self._queue.get(timeout=0.2)
                self._save(request)
            except Empty:
                continue
            except Exception as exc:
                self.stats["save_failures"] += 1
                self.logger.error("DAS storage worker error: %s", exc)

    def stop(self) -> None:
        """停止存储线程。设置 running=False，run 循环在下次超时后退出。"""
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
                # incremental=True 表示本文件是增量 chunk，不是全量快照（T3-02）
                "incremental": np.bool_(True),
            }

            np.savez_compressed(file_path, **payload)
            self.stats["requests_saved"] += 1
            self.logger.info(
                "DAS storage saved %d frames to %s", len(frames), file_path.name
            )
        except Exception as exc:
            self.stats["save_failures"] += 1
            self.logger.error("DAS storage _save failed: %s", exc)
