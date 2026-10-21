"""Manager for the independent FIP Tab2 pipeline."""

from __future__ import annotations

import logging
import time

from PyQt5.QtCore import QObject

from .detection_worker import FIPDetectionWorker
from .feature_worker import FIPFeatureWorker
from .plot_worker import FIPFeaturePlotWorker
from .trigger_storage import FIPTriggerStorageWorker
from .types import FIPTab2InputPacket


class FIPTab2Manager(QObject):
    """Coordinate Tab2 workers and route data between them."""

    def __init__(self, main_window, storage_path: str) -> None:
        super().__init__()
        self.logger = logging.getLogger(f"{__name__}.FIPTab2Manager")
        self.main_window = main_window

        self.feature_worker = FIPFeatureWorker()
        self.detection_worker = FIPDetectionWorker()
        self.plot_worker = FIPFeaturePlotWorker()
        self.storage_worker = FIPTriggerStorageWorker(storage_path=storage_path)
        self._setup_connections()

    def _setup_connections(self) -> None:
        self.feature_worker.feature_frame_ready.connect(self.detection_worker.enqueue_frame)
        self.feature_worker.feature_frame_ready.connect(self.plot_worker.enqueue_feature_frame)
        self.feature_worker.feature_frame_ready.connect(self.storage_worker.enqueue_feature_frame)
        self.feature_worker.packet_filtered.connect(self.storage_worker.enqueue_signal_packet)

        self.detection_worker.window_detection_ready.connect(self.plot_worker.enqueue_detection_result)
        self.detection_worker.alarm_event_ready.connect(self.main_window.add_alarm_event)
        self.detection_worker.baselines_updated.connect(self.main_window.update_baselines)
        self.detection_worker.trigger_save_requested.connect(self.storage_worker.enqueue_trigger_request)

        self.plot_worker.plot_payload_ready.connect(self.main_window.update_feature_displays)

    def start(self) -> None:
        """Start all Tab2 workers."""
        for worker in (self.feature_worker, self.detection_worker, self.plot_worker, self.storage_worker):
            if not worker.isRunning():
                worker.start()

    def stop(self) -> None:
        """两阶段停止所有 Tab2 工作线程（T2-02）。

        阶段一：排空触发存储队列
            将活跃告警事件推入存储请求队列后，进入排空模式，
            等待存储线程处理完所有待写盘请求（最多 5 s）。

        阶段二：停止所有线程
            向各线程发送停止信号，最多等待 3 s。
        """
        # --- 阶段一：触发排空 ---
        self.detection_worker.flush_active_event()
        self.storage_worker.begin_drain()

        # 等待存储线程排空（最多 5 s）
        drain_deadline = time.time() + 5.0
        while self.storage_worker.isRunning() and time.time() < drain_deadline:
            if not self.storage_worker.has_pending_work():
                break
            time.sleep(0.05)

        if self.storage_worker.has_pending_work():
            self.logger.warning(
                "Tab2 storage drain timeout: some trigger events may not have been saved."
            )

        # --- 阶段二：停止所有线程 ---
        for worker in (self.feature_worker, self.detection_worker, self.plot_worker, self.storage_worker):
            worker.stop()
        for worker in (self.feature_worker, self.detection_worker, self.plot_worker, self.storage_worker):
            if worker.isRunning():
                worker.wait(3000)

    def reset(self) -> None:
        """Reset all Tab2 worker state."""
        self.feature_worker.reset_state()
        self.detection_worker.reset_state()
        self.plot_worker.reset_state()
        self.storage_worker.reset_state()
        self.main_window.clear_alarm_table()
        self.main_window.clear_feature_displays()

    def process_processed_data(self, processed_data) -> None:
        """Forward Tab1 processed data into the Tab2 pipeline."""
        packet = FIPTab2InputPacket(
            timestamp=processed_data.timestamp,
            comm_count=processed_data.comm_count,
            sample_rate=getattr(processed_data, "display_sample_rate_hz", processed_data.effective_rate),
            data=processed_data.downsampled_data,
            packet_duration_seconds=max(float(getattr(processed_data, "packet_duration_seconds", 1.0)), 1e-6),
        )
        self.feature_worker.enqueue_packet(packet)

    def sync_from_ui(self) -> None:
        """Push the latest UI settings into all Tab2 workers."""
        compute_enabled = self.main_window.get_tab2_compute_enabled_features()
        plot_enabled = self.main_window.get_tab2_plot_enabled_features()
        window_settings = self.main_window.get_tab2_window_settings()
        preprocess_settings = self.main_window.get_tab2_preprocess_settings()
        storage_settings = self.main_window.get_tab2_storage_settings()
        thresholds = self.main_window.get_threshold_factors()

        self.feature_worker.update_compute_enabled(compute_enabled)
        self.feature_worker.update_window_settings(window_settings)
        self.feature_worker.update_preprocess_settings(preprocess_settings)
        self.plot_worker.update_plot_enabled(plot_enabled)
        self.plot_worker.update_display_settings(
            {"duration_seconds": window_settings.get("display_duration_seconds", 60.0)}
        )
        self.detection_worker.update_threshold_factors(thresholds)
        self.detection_worker.update_storage_settings(storage_settings)
        self.detection_worker.update_enabled_feature_names(
            [name for name, enabled in compute_enabled.items() if enabled]
        )
        self.storage_worker.update_storage_settings(storage_settings)

    def clear_alarm_history(self) -> None:
        """Clear the UI alarm table."""
        self.main_window.clear_alarm_table()
