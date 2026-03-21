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
        self.server = DASTCPServer(ip=settings["communication"]["ip"], port=settings["communication"]["port"])
        self.plot_worker = DASPlotWorker()
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
        self.coordinator.alignment_status_changed.connect(self.main_window.update_tab3_alignment_status)

    def start(self) -> bool:
        """Start the Tab3 pipeline and the DAS TCP server."""
        self.sync_from_ui()
        self.plot_worker.reset_state()
        if not self.plot_worker.isRunning():
            self.plot_worker.start()
        self._disconnect_alert_active = False
        self._watchdog_timer.start()
        self._storage_timer.start()
        started = self.server.start_server()
        if started:
            self.coordinator.update_online_state("das", False)
        return started

    def stop(self) -> None:
        """Stop the Tab3 pipeline."""
        self._storage_timer.stop()
        self._watchdog_timer.stop()
        self.server.stop_server()
        self.plot_worker.stop()
        if self.plot_worker.isRunning():
            self.plot_worker.wait(3000)
        self.coordinator.update_online_state("das", False)

    def reset(self) -> None:
        """Reset local state for a new monitoring session."""
        self._fip_recent_packets.clear()
        self._last_snapshot_end_comm = -1
        self._disconnect_alert_active = False
        self.plot_worker.reset_state()
        self.main_window.reset_tab3_views()

    def sync_from_ui(self) -> None:
        """Apply the latest UI settings to the server and plot worker."""
        settings = self.main_window.get_tab3_settings()
        self.server.ip = settings["communication"]["ip"]
        self.server.port = settings["communication"]["port"]
        self.plot_worker.update_settings(settings["plot"])
        self.coordinator.cache_seconds = settings["storage"]["cache_seconds"]

    def process_fip_processed_data(self, processed_data: ProcessedData) -> None:
        """Receive processed FIP packets for alignment and cross-plotting."""
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
        self.plot_worker.enqueue_packet(parsed)

    def _parse_packet(self, raw_packet: DASRawPacket) -> DASParsedPacket:
        header = raw_packet.header
        samples_per_channel = int(round(header.sample_rate_hz * header.packet_duration_seconds))
        matrix = raw_packet.data_1d.reshape(header.channel_count, samples_per_channel)
        packet_start_time = header.comm_count * header.packet_duration_seconds
        packet_end_time = packet_start_time + header.packet_duration_seconds
        return DASParsedPacket(
            header=header,
            matrix=matrix,
            packet_start_time=packet_start_time,
            packet_end_time=packet_end_time,
        )

    def _maybe_store_snapshot(self) -> None:
        settings = self.main_window.get_tab3_settings()
        storage_settings = settings["storage"]
        if not storage_settings["enabled"]:
            return
        interval_seconds = max(1.0, float(storage_settings["interval_seconds"]))
        frames = self.coordinator.get_recent_frames(interval_seconds)
        if not frames:
            return
        end_comm = frames[-1].comm_count
        if end_comm == self._last_snapshot_end_comm:
            return
        file_path = self._build_snapshot_path(storage_settings["path"])
        payload = {
            "comm_counts": np.array([frame.comm_count for frame in frames], dtype=np.int32),
            "packet_start_times": np.array([frame.packet_start_time for frame in frames], dtype=np.float64),
            "packet_duration_seconds": np.array([frame.packet_duration_seconds for frame in frames], dtype=np.float64),
            "fip_present": np.array([not frame.fip_missing for frame in frames], dtype=bool),
            "das_present": np.array([not frame.das_missing for frame in frames], dtype=bool),
            "fip_raw_200khz": np.array(
                [frame.fip_packet.unwrapped_data if frame.fip_packet is not None else np.array([], dtype=np.float64) for frame in frames],
                dtype=object,
            ),
            "fip_display_data": np.array(
                [frame.fip_packet.display_data if frame.fip_packet is not None else np.array([], dtype=np.float64) for frame in frames],
                dtype=object,
            ),
            "das_raw_matrix": np.array(
                [frame.das_packet.matrix if frame.das_packet is not None else np.array([], dtype=np.float64) for frame in frames],
                dtype=object,
            ),
            "missing_ranges": np.array(self.coordinator.snapshot_status().missing_ranges, dtype=object),
        }
        np.savez_compressed(file_path, **payload)
        self._last_snapshot_end_comm = end_comm
        self.main_window.update_tab3_storage_status(str(file_path))

    def _build_snapshot_path(self, output_dir: str) -> Path:
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
