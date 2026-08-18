"""Manager for the independent Tab3 DAS pipeline."""

from __future__ import annotations

import logging
import time
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Deque

import numpy as np
from PyQt5.QtCore import QObject, QTimer

from alignment import AlignedSessionCoordinator, DASSessionPacket, FIPSessionPacket
from fip import ProcessedData

from .plot_worker import DASPlotWorker
from .storage_worker import DASStorageRequest, DASStorageWorker, EDASRawStorageWorker
from .tcp_server import DASTCPServer
from .types import DASParsedPacket, DASRawPacket


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
        # Separate writers: joint npz and eDAS-only bin/json use independent queues.
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
        self.logger.debug("TAB3_NODE manager.init settings=%s", settings)

    def _setup_connections(self) -> None:
        self.server.packet_received.connect(self._handle_raw_packet)
        self.server.connection_status.connect(self.main_window.update_tab3_connection_status)
        self.server.error_occurred.connect(self.main_window.show_tab3_error)
        if hasattr(self.main_window, 'record_edas_comm_failure'):
            self.server.error_occurred.connect(lambda _message: self.main_window.record_edas_comm_failure())
        self.server.header_updated.connect(self.main_window.update_tab3_header_status)
        self.server.statistics_updated.connect(self.main_window.update_tab3_packet_statistics)
        self.plot_worker.plot_payload_ready.connect(self.main_window.update_tab3_plot_payload)
        self.storage_worker.storage_saved.connect(self.main_window.update_tab3_storage_status)
        self.edas_storage_worker.storage_status.connect(self.main_window.update_tab3_edas_storage_status)
        self.coordinator.alignment_status_changed.connect(
            self.main_window.update_tab3_alignment_status
        )
        self.logger.debug("TAB3_NODE manager.connections_ready")

    def start(self) -> bool:
        """Start Tab3 pipeline: DAS TCP server, plot worker, and storage workers."""
        self.sync_from_ui()
        self.plot_worker.reset_state()
        self.logger.debug(
            "TAB3_NODE manager.start ip=%s port=%s joint_enabled=%s edas_enabled=%s",
            self.server.ip,
            self.server.port,
            self._joint_storage_enabled,
            self._edas_storage_enabled,
        )
        if not self.plot_worker.isRunning():
            self.plot_worker.start()
        # Start background storage workers.
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
            self.logger.info("Tab3 DAS pipeline started")
            return True
        self._storage_timer.stop()
        self._watchdog_timer.stop()
        self.plot_worker.stop()
        if self.plot_worker.isRunning():
            self.plot_worker.wait(3000)
        self.storage_worker.stop()
        if self.storage_worker.isRunning():
            self.storage_worker.wait(3000)
        self.edas_storage_worker.stop()
        if self.edas_storage_worker.isRunning():
            self.edas_storage_worker.wait(3000)
        self.logger.warning("TAB3_NODE manager.start_failed cleaned_up")
        return False

    def stop(self) -> None:
        """Stop the Tab3 pipeline."""
        self.logger.debug("TAB3_NODE manager.stop")
        self._storage_timer.stop()
        self._watchdog_timer.stop()
        self.server.stop_server()
        self.plot_worker.stop()
        if self.plot_worker.isRunning():
            self.plot_worker.wait(3000)
        # Stop background storage workers.
        self.storage_worker.stop()
        if self.storage_worker.isRunning():
            self.storage_worker.wait(5000)
        self.edas_storage_worker.stop()
        if self.edas_storage_worker.isRunning():
            self.edas_storage_worker.wait(5000)
        self.coordinator.update_online_state("das", False)
        self.logger.info("Tab3 DAS pipeline stopped")

    def reset(self) -> None:
        """Reset local state for a new monitoring session."""
        self._fip_recent_packets.clear()
        self._last_snapshot_end_comm = -1
        self._disconnect_alert_active = False
        self.plot_worker.reset_state()
        self.edas_storage_worker.reset_session()
        self.main_window.reset_tab3_views()
        self.logger.debug("TAB3_NODE manager.reset")

    def sync_from_ui(self) -> None:
        """Synchronize current UI settings into server, plot worker, and storage workers."""
        settings = self.main_window.get_tab3_settings()
        storage_settings = settings["storage"]
        self.server.ip = settings["communication"]["ip"]
        self.server.port = settings["communication"]["port"]
        plot_settings = dict(settings["plot"])
        plot_settings.setdefault("curve_max_points", 5000)
        plot_settings.setdefault("space_time_max_pixels", 120000)
        self.plot_worker.update_settings(plot_settings)
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
        self.logger.debug(
            "TAB3_NODE manager.sync ip=%s port=%s plot=%s joint=%s edas=%s cache_seconds=%.1f",
            self.server.ip,
            self.server.port,
            plot_settings,
            self._joint_storage_enabled,
            self._edas_storage_enabled,
            cache_seconds,
        )

    def process_fip_processed_data(self, processed_data: ProcessedData) -> None:
        """Receive processed Tab1 data, push it into alignment, and update plots."""
        packet_duration_seconds = max(float(getattr(processed_data, "packet_duration_seconds", 1.0)), 1e-6)
        selected_sensor = getattr(processed_data, "selected_sensor", 1)
        unwrapped_source_by_sensor = getattr(processed_data, "unwrapped_by_sensor", {}) or {
            selected_sensor: processed_data.unwrapped_data
        }
        selected_unwrapped = unwrapped_source_by_sensor.get(selected_sensor)
        if selected_unwrapped is None:
            selected_unwrapped = processed_data.unwrapped_data
        alignment_by_sensor = {
            sensor_index: np.asarray(values)
            for sensor_index, values in unwrapped_source_by_sensor.items()
        }
        display_source_by_sensor = getattr(processed_data, "downsampled_by_sensor", {}) or {
            selected_sensor: processed_data.downsampled_data
        }
        display_by_sensor = {
            sensor_index: np.asarray(values)
            for sensor_index, values in display_source_by_sensor.items()
        }
        selected_display = display_by_sensor.get(selected_sensor)
        if selected_display is None:
            selected_display = processed_data.downsampled_data
        selected_unwrapped = np.asarray(selected_unwrapped)
        selected_display = np.asarray(selected_display)
        raw_sample_rate = max(float(getattr(processed_data, "raw_sample_rate_hz", processed_data.effective_rate)), 1.0)
        display_sample_rate = max(float(getattr(processed_data, "display_sample_rate_hz", processed_data.effective_rate)), 1.0)
        psd_sample_rate = max(float(getattr(processed_data, "psd_sample_rate_hz", raw_sample_rate)), 1.0)
        psd_source_by_sensor = getattr(processed_data, "psd_by_sensor", {}) or {
            selected_sensor: processed_data.psd_data
        }
        psd_by_sensor = {
            sensor_index: np.asarray(values)
            for sensor_index, values in psd_source_by_sensor.items()
        }
        packet = FIPSessionPacket(
            comm_count=processed_data.comm_count,
            packet_duration_seconds=packet_duration_seconds,
            sample_rate_hz=raw_sample_rate,
            unwrapped_data=selected_unwrapped,
            display_data=selected_display,
            sensor_count=getattr(processed_data, "sensor_count", 1),
            selected_sensor=selected_sensor,
            unwrapped_by_sensor=alignment_by_sensor,
            display_by_sensor=display_by_sensor,
        )
        self._fip_recent_packets.append(packet)
        self.coordinator.push_fip_packet(packet)
        self.logger.debug(
            "TAB3_NODE manager.fip_packet comm=%s sensors=%s selected=FIP%s display_points=%d "
            "sample_rate=%.1f duration=%.6f recent=%d source=display_downsampled first=%.9g",
            processed_data.comm_count,
            getattr(processed_data, "sensor_count", 1),
            selected_sensor,
            len(selected_display),
            display_sample_rate,
            packet_duration_seconds,
            len(self._fip_recent_packets),
            float(selected_display[0]) if len(selected_display) else float("nan"),
        )
        if len(selected_display) and abs(float(selected_display[0])) <= 1e-12 and processed_data.comm_count % 50 == 0:
            self.logger.warning(
                "TAB3_NODE manager.fip_first_zero comm=%s selected=FIP%s source=display_downsampled value=%.9g",
                processed_data.comm_count,
                selected_sensor,
                float(selected_display[0]),
            )
        if processed_data.comm_count % 50 == 0:
            display_min = float(np.min(selected_display)) if len(selected_display) else float("nan")
            display_max = float(np.max(selected_display)) if len(selected_display) else float("nan")
            raw_min = float(np.min(selected_unwrapped)) if len(selected_unwrapped) else float("nan")
            raw_max = float(np.max(selected_unwrapped)) if len(selected_unwrapped) else float("nan")
            self.logger.info(
                "TAB3_NODE manager.fip_display comm=%s selected=FIP%s display_source=filtered_downsampled "
                "display_points=%d display_range=[%.9g,%.9g] raw_points=%d raw_range=[%.9g,%.9g]",
                processed_data.comm_count,
                selected_sensor,
                len(selected_display),
                display_min,
                display_max,
                len(selected_unwrapped),
                raw_min,
                raw_max,
            )
        self.main_window.update_tab3_fip_curve(
            processed_data.comm_count,
            selected_display,
            display_sample_rate,
            sensor_count=getattr(processed_data, "sensor_count", 1),
            values_by_sensor=display_by_sensor,
            packet_duration_seconds=packet_duration_seconds,
            psd_values_by_sensor=psd_by_sensor,
            psd_sample_rate_hz=psd_sample_rate,
        )

    def _handle_raw_packet(self, raw_packet: DASRawPacket) -> None:
        started = time.perf_counter()
        parsed = self._parse_packet(raw_packet)
        if hasattr(self.main_window, 'record_edas_packet_receive'):
            try:
                receive_time = float(getattr(raw_packet, "receive_timestamp", time.time()))
                self.main_window.record_edas_packet_receive(parsed.header.comm_count, receive_time)
            except Exception as sync_exc:
                self.logger.warning(
                    "Failed to update FIP/eDAS receive-time sync UI for DAS packet #%s: %s",
                    parsed.header.comm_count,
                    sync_exc,
                )
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
        storage_queued = False
        if self._edas_storage_enabled:
            storage_queued = self.edas_storage_worker.enqueue_packet(
                parsed,
                output_dir=self._edas_storage_path,
                blocks_per_file=self._edas_blocks_per_file,
                queue_packets=self._edas_queue_packets,
            )
            if not storage_queued:
                self.main_window.update_tab3_edas_storage_status(
                    f"Dropped DAS packet comm={parsed.header.comm_count}; eDAS save queue full"
                )
        plot_queued = self.plot_worker.enqueue_packet(parsed)
        if not plot_queued:
            self.logger.warning("DAS plot queue rejected packet comm_count=%d", parsed.header.comm_count)
        elapsed_ms = (time.perf_counter() - started) * 1000.0
        self.logger.debug(
            "TAB3_NODE manager.raw_packet comm=%s matrix_shape=%s parse_route_ms=%.2f edas_enabled=%s edas_queued=%s plot_queued=%s",
            parsed.header.comm_count,
            tuple(parsed.matrix.shape),
            elapsed_ms,
            self._edas_storage_enabled,
            storage_queued,
            plot_queued,
        )
        if elapsed_ms > 80.0:
            self.logger.warning(
                "TAB3_NODE manager.slow_raw_packet comm=%s elapsed_ms=%.2f matrix_shape=%s",
                parsed.header.comm_count,
                elapsed_ms,
                tuple(parsed.matrix.shape),
            )

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
        self.logger.debug(
            "TAB3_NODE manager.parse comm=%s channels=%d samples_per_channel=%d duration=%.6f",
            header.comm_count,
            header.channel_count,
            samples_per_channel,
            header.packet_duration_seconds,
        )
        return DASParsedPacket(
            header=header,
            matrix=matrix,
            packet_start_time=packet_start_time,
            packet_end_time=packet_end_time,
        )

    def _maybe_store_snapshot(self) -> None:
        """Check joint storage conditions and enqueue incremental FIP+eDAS writes.

        FIP+eDAS SAVE writes joint npz only when both sources are online and aligned.
        If only one source is online, the UI routes storage to the matching single-source path.
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
            self.logger.debug("TAB3_NODE manager.joint_storage no_frames status=%s", status.alignment_status)
            return
        end_comm = frames[-1].comm_count
        if end_comm == self._last_snapshot_end_comm:
            self.logger.debug("TAB3_NODE manager.joint_storage same_end_comm=%s", end_comm)
            return

        if not status.fip_online or not status.das_online:
            self._last_snapshot_end_comm = end_comm
            self.logger.debug(
                "TAB3_NODE manager.joint_storage fallback fip_online=%s das_online=%s end_comm=%s",
                status.fip_online,
                status.das_online,
                end_comm,
            )
            self._route_joint_storage_fallback(status)
            return
        if status.alignment_status != "aligned":
            self._last_snapshot_end_comm = end_comm
            self.logger.debug(
                "TAB3_NODE manager.joint_storage wait_alignment status=%s frames=%d end_comm=%s",
                status.alignment_status,
                len(frames),
                end_comm,
            )
            self.main_window.update_tab3_storage_status(
                f"Waiting aligned state; current={status.alignment_status}, end_comm={end_comm}"
            )
            return

        interval_seconds = max(1.0, float(storage_settings.get("interval_seconds", self._joint_interval_seconds)))
        chunk_start = float(frames[0].packet_start_time)
        chunk_end = float(frames[-1].packet_start_time + frames[-1].packet_duration_seconds)
        chunk_seconds = max(0.0, chunk_end - chunk_start)
        if chunk_seconds + 1e-9 < interval_seconds:
            self.logger.debug(
                "TAB3_NODE manager.joint_storage collecting chunk_seconds=%.3f interval=%.3f frames=%d end_comm=%s",
                chunk_seconds,
                interval_seconds,
                len(frames),
                end_comm,
            )
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
        self.logger.debug(
            "TAB3_NODE manager.joint_storage queued frames=%d end_comm=%s output_dir=%s",
            len(frames),
            end_comm,
            request.output_dir,
        )
        self.main_window.update_tab3_storage_status(
            f"Queued {len(frames)} frames (end_comm={end_comm})"
        )

    def _route_joint_storage_fallback(self, status) -> None:
        """Switch a joint-save request to the available single-source save mode."""
        if status.das_online and not status.fip_online:
            message = "FIP+eDAS SAVE requires FIP and eDAS; routed to eDAS SAVE."
            self._edas_storage_enabled = True
            self.logger.debug("TAB3_NODE manager.storage_fallback target=edas")
            self.main_window.route_tab3_joint_storage_fallback("edas", message)
            self.main_window.update_tab3_storage_status(message)
            return
        if status.fip_online and not status.das_online:
            message = "FIP+eDAS SAVE requires FIP and eDAS; routed to Tab1 FIP phase storage."
            self.logger.debug("TAB3_NODE manager.storage_fallback target=fip")
            self.main_window.route_tab3_joint_storage_fallback("fip", message)
            self.main_window.update_tab3_storage_status(message)
            return
        message = "FIP+eDAS SAVE waits for both FIP and eDAS communication."
        self.logger.debug("TAB3_NODE manager.storage_fallback target=wait")
        self.main_window.update_tab3_storage_status(message)

    def _build_snapshot_path(self, output_dir: str) -> Path:
        """Build a snapshot path; actual disk I/O is handled by DASStorageWorker."""
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
        self.logger.warning("TAB3_NODE manager.disconnect_timeout age_seconds=%.2f", age_seconds)
        self.main_window.show_tab3_error("DAS has not received data for 10 seconds.")
