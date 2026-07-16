"""Manager for the independent Tab3 DAS pipeline."""

from __future__ import annotations

import logging
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Deque

import numpy as np
from PyQt5.QtCore import QObject, QTimer

from alignment import AlignedSessionCoordinator, DASSessionPacket, FIPSessionPacket
from fip_tab1 import ProcessedData

from .das_plot_worker import DASPlotWorker
from .das_storage_worker import DASStorageRequest, DASStorageWorker, EDASRawStorageWorker
from .das_tcp_server import DASTCPServer
from .das_types import DASParsedPacket, DASRawPacket


class DASTab3Manager(QObject):
    """Own the Tab3 DAS server, plotting pipeline, and raw joint storage."""

    def __init__(self, main_window, coordinator: AlignedSessionCoordinator) -> None:
        super().__init__()
        self.logger = logging.getLogger(f"{__name__}.DASTab3Manager")
        self.main_window = main_window
        self.coordinator = coordinator
        settings = self.main_window.get_tab3_settings()
        self.server = DASTCPServer(
            ip=settings["communication"]["ip"],
            port=settings["communication"]["port"],
        )
        self.plot_worker = DASPlotWorker()
        # 独立存储线程：joint npz 与 eDAS-only bin/json 分开写入，互不抢队列。
        self.storage_worker = DASStorageWorker()
        self.edas_storage_worker = EDASRawStorageWorker()
        self._joint_storage_enabled = False
        self._joint_storage_path = "D:/PCCP/FIPeDASDATA"
        self._joint_interval_seconds = 10.0
        self._edas_storage_enabled = False
        self._edas_storage_path = "D:/PCCP/eDASDATA"
        self._edas_blocks_per_file = 50
        self._edas_queue_packets = 200
        self._fip_recent_packets: Deque[FIPSessionPacket] = deque(maxlen=64)
        self._storage_timer = QTimer()
        self._storage_timer.setInterval(1000)
        self._storage_timer.timeout.connect(self._maybe_store_snapshot)
        self._watchdog_timer = QTimer()
        self._watchdog_timer.setInterval(1000)
        self._watchdog_timer.timeout.connect(self._check_disconnect_timeout)
        self._last_snapshot_end_comm = -1
        self._disconnect_alert_active = False
        self._setup_connections()

    def _setup_connections(self) -> None:
        self.server.packet_received.connect(self._handle_raw_packet)
        self.server.connection_status.connect(self.main_window.update_tab3_connection_status)
        self.server.error_occurred.connect(self.main_window.show_tab3_error)
        self.server.header_updated.connect(self.main_window.update_tab3_header_status)
        self.server.statistics_updated.connect(self.main_window.update_tab3_packet_statistics)
        self.plot_worker.plot_payload_ready.connect(self.main_window.update_tab3_plot_payload)
        self.storage_worker.storage_saved.connect(self.main_window.update_tab3_storage_status)
        self.edas_storage_worker.storage_status.connect(self.main_window.update_tab3_edas_storage_status)
        self.coordinator.alignment_status_changed.connect(
            self.main_window.update_tab3_alignment_status
        )

    def start(self) -> bool:
        """启动 Tab3 管线：DAS TCP 服务器、绘图线程和存储线程。"""
        self.sync_from_ui()
        self.plot_worker.reset_state()
        if not self.plot_worker.isRunning():
            self.plot_worker.start()
        # 启动独立存储线程（T3-01）
        if not self.storage_worker.isRunning():
            self.storage_worker.start()
        if not self.edas_storage_worker.isRunning():
            self.edas_storage_worker.start()
        self._disconnect_alert_active = False
        self._watchdog_timer.start()
        self._storage_timer.start()
        started = self.server.start_server()
        if started:
            self.coordinator.update_online_state("das", False)
        return started

    def stop(self) -> None:
        """停止 Tab3 管线。"""
        self._storage_timer.stop()
        self._watchdog_timer.stop()
        self.server.stop_server()
        self.plot_worker.stop()
        if self.plot_worker.isRunning():
            self.plot_worker.wait(3000)
        # 停止存储线程（T3-01）
        self.storage_worker.stop()
        if self.storage_worker.isRunning():
            self.storage_worker.wait(5000)
        self.edas_storage_worker.stop()
        if self.edas_storage_worker.isRunning():
            self.edas_storage_worker.wait(5000)
        self.coordinator.update_online_state("das", False)

    def reset(self) -> None:
        """重置本地状态，准备新一轮监测会话。"""
        self._fip_recent_packets.clear()
        self._last_snapshot_end_comm = -1
        self._disconnect_alert_active = False
        self.plot_worker.reset_state()
        self.edas_storage_worker.reset_session()
        self.main_window.reset_tab3_views()

    def sync_from_ui(self) -> None:
        """将最新 UI 设置同步到服务器、绘图线程和存储线程。"""
        settings = self.main_window.get_tab3_settings()
        storage_settings = settings["storage"]
        self.server.ip = settings["communication"]["ip"]
        self.server.port = settings["communication"]["port"]
        self.plot_worker.update_settings(settings["plot"])
        self._joint_storage_enabled = bool(
            storage_settings.get("joint_enabled", storage_settings.get("enabled", False))
        )
        self._joint_storage_path = storage_settings.get("path", self._joint_storage_path)
        self._joint_interval_seconds = max(
            1.0, float(storage_settings.get("interval_seconds", self._joint_interval_seconds))
        )
        cache_seconds = max(
            float(storage_settings.get("cache_seconds", 10.0)),
            self._joint_interval_seconds + 1.0,
        )
        self.coordinator.cache_seconds = cache_seconds
        self._edas_storage_enabled = bool(storage_settings.get("edas_enabled", False))
        self._edas_storage_path = storage_settings.get("edas_path", self._edas_storage_path)
        self._edas_blocks_per_file = max(
            1, int(storage_settings.get("edas_blocks_per_file", self._edas_blocks_per_file))
        )
        self._edas_queue_packets = max(
            1, int(storage_settings.get("edas_queue_packets", self._edas_queue_packets))
        )

    def process_fip_processed_data(self, processed_data: ProcessedData) -> None:
        """接收 Tab1 处理后数据，推入对齐协调器并更新绘图。"""
        packet = FIPSessionPacket(
            comm_count=processed_data.comm_count,
            packet_duration_seconds=0.2,
            sample_rate_hz=processed_data.effective_rate,
            unwrapped_data=processed_data.unwrapped_data,
            display_data=processed_data.downsampled_data,
        )
        self._fip_recent_packets.append(packet)
        self.coordinator.push_fip_packet(packet)
        self.main_window.update_tab3_fip_curve(
            processed_data.comm_count,
            processed_data.downsampled_data,
            processed_data.effective_rate,
        )

    def _handle_raw_packet(self, raw_packet: DASRawPacket) -> None:
        parsed = self._parse_packet(raw_packet)
        self.coordinator.update_online_state("das", True)
        self.coordinator.push_das_packet(
            DASSessionPacket(
                comm_count=parsed.header.comm_count,
                packet_duration_seconds=parsed.header.packet_duration_seconds,
                sample_rate_hz=parsed.header.sample_rate_hz,
                channel_count=parsed.header.channel_count,
                matrix=parsed.matrix,
            )
        )
        if self._edas_storage_enabled:
            queued = self.edas_storage_worker.enqueue_packet(
                parsed,
                output_dir=self._edas_storage_path,
                blocks_per_file=self._edas_blocks_per_file,
                queue_packets=self._edas_queue_packets,
            )
            if not queued:
                self.main_window.update_tab3_edas_storage_status(
                    f"Dropped DAS packet comm={parsed.header.comm_count}; eDAS save queue full"
                )
        if not self.plot_worker.enqueue_packet(parsed):
            self.logger.warning("DAS plot queue rejected packet comm_count=%d", parsed.header.comm_count)

    def _parse_packet(self, raw_packet: DASRawPacket) -> DASParsedPacket:
        header = raw_packet.header
        if header.channel_count <= 0:
            raise ValueError(f"Invalid DAS channel_count: {header.channel_count}")
        if raw_packet.data_1d.size % header.channel_count != 0:
            raise ValueError(
                f"DAS payload size {raw_packet.data_1d.size} is not divisible by channel_count={header.channel_count}"
            )
        samples_per_channel = raw_packet.data_1d.size // header.channel_count
        matrix = np.ascontiguousarray(
            raw_packet.data_1d.reshape(header.channel_count, samples_per_channel)
        )
        packet_start_time = header.comm_count * header.packet_duration_seconds
        packet_end_time = packet_start_time + header.packet_duration_seconds
        return DASParsedPacket(
            header=header,
            matrix=matrix,
            packet_start_time=packet_start_time,
            packet_end_time=packet_end_time,
        )

    def _maybe_store_snapshot(self) -> None:
        """检查 joint 存储条件，并以增量方式异步提交 FIP+eDAS 写盘请求。

        FIP+eDAS SAVE 只在两路同时在线且对齐状态为 aligned 时写 joint npz。
        若只有一路在线，界面会自动转入对应单源保存：FIP 相位存储或 eDAS SAVE。
        """
        settings = self.main_window.get_tab3_settings()
        storage_settings = settings["storage"]
        self._joint_storage_enabled = bool(
            storage_settings.get("joint_enabled", storage_settings.get("enabled", False))
        )
        if not self._joint_storage_enabled:
            return

        status = self.coordinator.snapshot_status()
        frames = self.coordinator.get_frames_since(self._last_snapshot_end_comm)
        if not frames:
            return
        end_comm = frames[-1].comm_count
        if end_comm == self._last_snapshot_end_comm:
            return

        if not status.fip_online or not status.das_online:
            self._last_snapshot_end_comm = end_comm
            self._route_joint_storage_fallback(status)
            return
        if status.alignment_status != "aligned":
            self._last_snapshot_end_comm = end_comm
            self.main_window.update_tab3_storage_status(
                f"Waiting aligned state; current={status.alignment_status}, end_comm={end_comm}"
            )
            return

        interval_seconds = max(1.0, float(storage_settings.get("interval_seconds", self._joint_interval_seconds)))
        chunk_start = float(frames[0].packet_start_time)
        chunk_end = float(frames[-1].packet_start_time + frames[-1].packet_duration_seconds)
        chunk_seconds = max(0.0, chunk_end - chunk_start)
        if chunk_seconds + 1e-9 < interval_seconds:
            self.main_window.update_tab3_storage_status(
                f"Collecting joint chunk {chunk_seconds:.1f}/{interval_seconds:.1f}s"
            )
            return

        self._last_snapshot_end_comm = end_comm
        request = DASStorageRequest(
            frames=list(frames),
            output_dir=storage_settings.get("path", self._joint_storage_path),
            end_comm=end_comm,
        )
        self.storage_worker.enqueue_request(request)
        self.main_window.update_tab3_storage_status(
            f"Queued {len(frames)} frames (end_comm={end_comm})"
        )

    def _route_joint_storage_fallback(self, status) -> None:
        """Switch a joint-save request to the available single-source save mode."""
        if status.das_online and not status.fip_online:
            message = "FIP+eDAS SAVE requires FIP and eDAS; routed to eDAS SAVE."
            self._edas_storage_enabled = True
            self.main_window.route_tab3_joint_storage_fallback("edas", message)
            self.main_window.update_tab3_storage_status(message)
            return
        if status.fip_online and not status.das_online:
            message = "FIP+eDAS SAVE requires FIP and eDAS; routed to Tab1 FIP phase storage."
            self.main_window.route_tab3_joint_storage_fallback("fip", message)
            self.main_window.update_tab3_storage_status(message)
            return
        message = "FIP+eDAS SAVE waits for both FIP and eDAS communication."
        self.main_window.update_tab3_storage_status(message)

    def _build_snapshot_path(self, output_dir: str) -> Path:
        """构建快照文件路径（保留供外部调用，实际写盘由 DASStorageWorker 执行）。"""
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        now = datetime.now()
        return path / f"FIPeDAS-{now.strftime('%Y%m%d-%H%M%S.%f')[:-3]}.npz"

    def _check_disconnect_timeout(self) -> None:
        if not self.server.is_connected():
            self._disconnect_alert_active = False
            return
        age_seconds = self.server.last_data_age_seconds()
        if age_seconds < 10.0:
            self._disconnect_alert_active = False
            return
        if self._disconnect_alert_active:
            return
        self._disconnect_alert_active = True
        self.coordinator.update_online_state("das", False)
        self.main_window.show_tab3_error("DAS has not received data for 10 seconds.")
