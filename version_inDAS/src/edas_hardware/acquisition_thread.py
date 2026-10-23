"""
`src/acquisition_thread.py` 实现后台采集线程，是设备数据进入上位机后的第一站。

最新版本的采集线程已经不再只是“读数据然后发信号”。它同时负责动态轮询 DLL 缓冲区、控制单次读取块大小、生成完整数据块、维护最新显示快照、裁剪单通道 PHASE 空间范围、上报诊断快照，并在必要时配合主窗口完成停滞检测与自动恢复。

另一个值得记录的经验是：线程内部保留了较完整的运行态指标，例如最近一次查询耗时、最近一次读数耗时、当前阶段名称以及最后一次成功读取距离现在的时间。这些字段对现场分析“到底卡在 GUI、DLL、驱动还是磁盘”非常关键。
"""
import threading
import time
import numpy as np
from typing import Callable, Optional
from PyQt5.QtCore import QThread, pyqtSignal, QMutex, QWaitCondition, Qt

from .pcie7821_api import PCIe7821API, PCIe7821Error
from .config import (
    DataSource,
    AllParams,
    POLLING_CONFIG,
    OPTIMIZED_BUFFER_SIZES,
    resolve_phase_crop_bounds,
)
from .logger import get_logger
from .realtime_filter import FilterSpecError, RealtimeTimeAxisFilter, parse_filter_spec

# Module logger
log = get_logger("acq_thread")

# Minimum interval between GUI updates (ms)
MIN_GUI_UPDATE_INTERVAL_MS = 50  # 20 FPS max to prevent Qt signal queue backup

# Buffer query failures from the DLL are hardware-boundary faults. Retrying a
# small number of generic exceptions is acceptable, but explicit PCIe API
# errors should stop the acquisition loop before log spam hides the root cause.
BUFFER_QUERY_FAILURE_LIMIT = 5
BUFFER_QUERY_FAILURE_LOG_INTERVAL_S = 1.0


# ----- HARDWARE ACQUISITION THREAD -----
# Polls DMA buffer, reads data, emits Qt signals to GUI thread

class AcquisitionThread(QThread):
    """
    Data acquisition thread for PCIe-7821.

    Runs in a separate thread to avoid blocking the GUI.
    Uses Qt signals to communicate data to the main thread.
    """

    # Signals
    data_ready = pyqtSignal(np.ndarray, int, int)  # data, data_type, channel_num
    phase_data_ready = pyqtSignal(np.ndarray, int)  # phase_data, channel_num
    monitor_data_ready = pyqtSignal(np.ndarray, int)  # monitor_data, channel_num
    buffer_status = pyqtSignal(int, int)  # points_in_buffer, buffer_size_mb
    error_occurred = pyqtSignal(str)  # error message
    acquisition_started = pyqtSignal()
    acquisition_stopped = pyqtSignal()
    display_snapshot_ready = pyqtSignal()

    def __init__(self, api: PCIe7821API, parent=None):
        """
        Initialize acquisition thread.

        Args:
            api: PCIe7821API instance
            parent: Parent QObject
        """
        super().__init__(parent)
        self.api = api
        self._running = False
        self._paused = False

        # Parameters (will be set before starting)
        self._params: Optional[AllParams] = None
        self._total_point_num = 0
        self._point_num_after_merge = 0
        self._frame_num = 20
        self._channel_num = 1
        self._data_source = DataSource.PHASE

        # Thread synchronization
        self._mutex = QMutex()
        self._pause_condition = QWaitCondition()

        # Statistics
        self._frames_acquired = 0
        self._bytes_acquired = 0
        self._loop_count = 0
        self._last_log_time = 0
        self._phase_emit_count = 0
        self._raw_emit_count = 0
        self._monitor_emit_count = 0
        self._gui_skip_count = 0
        self._last_buffer_points = 0
        self._last_expected_points = 0
        self._last_wait_iterations = 0
        self._last_query_ms = 0.0
        self._last_read_ms = 0.0
        self._last_api_read_ms = 0.0
        self._last_crop_ms = 0.0
        self._last_dispatch_ms = 0.0
        self._last_display_publish_ms = 0.0
        self._last_monitor_read_ms = 0.0
        self._last_block_bytes = 0
        self._current_stage = "idle"
        self._current_stage_detail = ""
        self._current_stage_started_at = time.perf_counter()
        self._buffer_query_error_count = 0
        self._consecutive_buffer_query_errors = 0
        self._last_buffer_query_error_message = ""
        self._last_buffer_query_error_log_time = 0.0

        # Display handoff uses one overwriteable slot instead of queued large-array signals.
        self._last_gui_update_time = 0
        self._pending_phase_data = None
        self._pending_raw_data = None
        self._pending_monitor_data = None
        self._latest_display_data = None
        self._latest_display_lock = threading.Lock()
        self._display_active = True
        self._display_visibility_generation = 0
        self._applied_display_visibility_generation = 0
        self._display_history_chunks = []
        self._display_history_frames = 0
        self._display_history_signature = None
        self._display_target_frames = None
        self._display_publish_interval_s = 1.0
        self._display_publish_tolerance_s = 0.0
        self._last_display_publish_at = 0.0
        self._display_mode = None
        self._display_region_index = 0
        self._display_requires_full_window = False
        self._display_incremental_full_width = False
        self._display_image_enabled = True
        self._display_rad_enabled = False
        self._display_distance_start = 0
        self._display_distance_end = 0
        self._display_space_downsample = 1
        self._display_time_downsample = 1
        self._display_filter_enabled = False
        self._display_filter_spec = ""
        self._display_transform_request = None
        self._time_space_filter = RealtimeTimeAxisFilter(order=2)
        self._last_display_transform_ms = 0.0
        self._monitor_read_enabled = False
        self._full_data_handler: Optional[Callable[[np.ndarray, int, int], None]] = None
        self._last_successful_read_time = 0.0
        self._expected_block_duration_ms = 0.0
        self._acquisition_started_at = 0.0
        self._frame_rate_warning_logged = False

        # Dynamic polling: switch between fast/slow intervals based on buffer fill.
        # Hysteresis between high/low thresholds prevents oscillation.
        self._current_polling_interval = POLLING_CONFIG['low_freq_interval_ms'] / 1000.0
        self._high_freq_interval = POLLING_CONFIG['high_freq_interval_ms'] / 1000.0
        self._low_freq_interval = POLLING_CONFIG['low_freq_interval_ms'] / 1000.0
        self._buffer_threshold_high = POLLING_CONFIG['buffer_threshold_high']
        self._buffer_threshold_low = POLLING_CONFIG['buffer_threshold_low']

        log.info("AcquisitionThread initialized with dynamic polling")

    def configure(self, params: AllParams):
        """
        Configure acquisition parameters.

        Args:
            params: Configuration parameters
        """
        self._params = params
        self._total_point_num = params.basic.point_num_per_scan
        self._point_num_after_merge = self._total_point_num // params.phase_demod.merge_point_num
        self._frame_num = params.display.frame_load_num
        self._channel_num = params.upload.channel_num
        self._data_source = params.upload.data_source

        if self._data_source == DataSource.PHASE:
            points_per_frame = self._point_num_after_merge
            bytes_per_point = 4
        else:
            points_per_frame = self._total_point_num
            bytes_per_point = 2

        block_points_total = points_per_frame * self._frame_num * self._channel_num
        block_bytes_total = block_points_total * bytes_per_point
        block_duration_ms = self._frame_num / max(params.basic.scan_rate, 1) * 1000.0
        self._expected_block_duration_ms = block_duration_ms
        self._display_history_chunks = []
        self._display_history_frames = 0
        self._display_history_signature = None
        self._display_publish_interval_s = max(
            MIN_GUI_UPDATE_INTERVAL_MS / 1000.0,
            int(params.display.frame_plot_num) / max(1, int(params.basic.scan_rate)),
        )
        self._display_publish_tolerance_s = min(
            0.050,
            max(0.005, block_duration_ms / 1000.0 * 0.15),
        )
        self._last_display_publish_at = 0.0
        self._display_mode = int(getattr(params.display, "mode", 0))
        self._display_region_index = int(getattr(params.display, "region_index", 0))
        self._display_requires_full_window = False
        self._display_incremental_full_width = False
        self._display_transform_request = None
        self._time_space_filter.reset_design()
        self._last_display_transform_ms = 0.0
        self._monitor_read_enabled = bool(
            self._data_source == DataSource.PHASE
            and getattr(params.display, "monitor_plot_enabled", False)
        )

        log.info(f"Configured: total_points={self._total_point_num}, "
                 f"points_after_merge={self._point_num_after_merge}, "
                 f"frames={self._frame_num}, channels={self._channel_num}, "
                 f"data_source={self._data_source}, "
                 f"crop=[{params.phase_demod.crop_distance_start}, {params.phase_demod.crop_distance_end}), "
                 f"block_points={block_points_total}, block_bytes={block_bytes_total / 1024 / 1024:.2f}MB, "
                 f"block_duration={block_duration_ms:.1f}ms")

    def set_display_request(
        self,
        mode: int,
        region_index: int,
        require_full_window: bool,
        incremental_full_width: bool = False,
        image_enabled: bool = True,
    ) -> None:
        """Update the GUI snapshot shape without changing the full-data path."""
        request = (
            int(mode), int(region_index), bool(require_full_window), bool(incremental_full_width), bool(image_enabled)
        )
        current = (
            self._display_mode,
            self._display_region_index,
            self._display_requires_full_window,
            self._display_incremental_full_width,
            self._display_image_enabled,
        )
        if request == current:
            return

        (
            self._display_mode,
            self._display_region_index,
            self._display_requires_full_window,
            self._display_incremental_full_width,
            self._display_image_enabled,
        ) = request
        self._display_visibility_generation += 1
        with self._latest_display_lock:
            self._latest_display_data = None
        self._last_display_publish_at = 0.0
        log.info(
            "Display request changed: mode=%s, region=%d, full_window=%d, incremental_full_width=%d, image_enabled=%d",
            self._display_mode,
            self._display_region_index,
            int(self._display_requires_full_window),
            int(self._display_incremental_full_width),
            int(self._display_image_enabled),
        )

    def set_display_transform(
        self,
        rad_enabled: bool,
        distance_start: int,
        distance_end: int,
        space_downsample: int,
        time_downsample: int,
        filter_enabled: bool,
        filter_spec: str,
    ) -> None:
        """Update the display-side transform applied before the GUI receives a snapshot."""
        request = (
            bool(rad_enabled),
            int(distance_start),
            int(distance_end),
            max(1, int(space_downsample)),
            max(1, int(time_downsample)),
            bool(filter_enabled),
            str(filter_spec or "").strip(),
        )
        if request == self._display_transform_request:
            return

        self._display_transform_request = request
        (
            self._display_rad_enabled,
            self._display_distance_start,
            self._display_distance_end,
            self._display_space_downsample,
            self._display_time_downsample,
            self._display_filter_enabled,
            self._display_filter_spec,
        ) = request

        self._time_space_filter.reset_design()
        scan_rate = max(1, int(self._params.basic.scan_rate)) if self._params is not None else 1
        if self._display_filter_enabled:
            try:
                self._time_space_filter.configure(
                    parse_filter_spec(self._display_filter_spec),
                    scan_rate,
                )
            except FilterSpecError as exc:
                log.warning("Time-space filter unavailable in acquisition thread: %s", exc)

        with self._latest_display_lock:
            self._latest_display_data = None
        self._last_display_publish_at = 0.0
        log.info(
            "Display transform changed: rad=%d, dist=[%d,%d), space_ds=%d, time_ds=%d, filter=%d",
            int(self._display_rad_enabled),
            self._display_distance_start,
            self._display_distance_end,
            self._display_space_downsample,
            self._display_time_downsample,
            int(self._display_filter_enabled),
        )

    def _set_stage(self, stage: str, detail: str = ""):
        """Track the current internal stage for external diagnostics."""
        self._current_stage = stage
        self._current_stage_detail = detail
        self._current_stage_started_at = time.perf_counter()

    def get_diagnostics_snapshot(self) -> dict:
        """Return a lightweight diagnostic snapshot for stall analysis."""
        elapsed_s = (
            time.perf_counter() - self._acquisition_started_at
            if self._acquisition_started_at > 0.0
            else 0.0
        )
        configured_fps = max(1, int(self._params.basic.scan_rate)) if self._params else 0
        measured_fps = self._frames_acquired / elapsed_s if elapsed_s > 0.0 else 0.0
        points_per_frame = max(
            1,
            int(self._point_num_after_merge if self._data_source == DataSource.PHASE else self._total_point_num),
        )
        pending_frames = self._last_buffer_points / points_per_frame
        return {
            "loop_count": self._loop_count,
            "frames_acquired": self._frames_acquired,
            "bytes_acquired": self._bytes_acquired,
            "elapsed_s": elapsed_s,
            "configured_fps": configured_fps,
            "measured_fps": measured_fps,
            "measured_fps_ratio": measured_fps / configured_fps if configured_fps > 0 else 0.0,
            "driver_pending_frames": pending_frames,
            "phase_emit_count": self._phase_emit_count,
            "raw_emit_count": self._raw_emit_count,
            "monitor_emit_count": self._monitor_emit_count,
            "gui_skip_count": self._gui_skip_count,
            "current_stage": self._current_stage,
            "current_stage_detail": self._current_stage_detail,
            "stage_elapsed_ms": (time.perf_counter() - self._current_stage_started_at) * 1000.0,
            "last_buffer_points": self._last_buffer_points,
            "last_expected_points": self._last_expected_points,
            "buffer_ratio": (
                self._last_buffer_points / self._last_expected_points
                if self._last_expected_points > 0
                else 0.0
            ),
            "backlog_s": (
                self._last_buffer_points / self._last_expected_points
                * self._expected_block_duration_ms / 1000.0
                if self._last_expected_points > 0
                else 0.0
            ),
            "last_wait_iterations": self._last_wait_iterations,
            "last_query_ms": self._last_query_ms,
            "last_read_ms": self._last_read_ms,
            "last_api_read_ms": self._last_api_read_ms,
            "last_crop_ms": self._last_crop_ms,
            "last_dispatch_ms": self._last_dispatch_ms,
            "last_display_publish_ms": self._last_display_publish_ms,
            "last_display_transform_ms": self._last_display_transform_ms,
            "last_monitor_read_ms": self._last_monitor_read_ms,
            "last_block_bytes": self._last_block_bytes,
            "monitor_read_enabled": self._monitor_read_enabled,
            "buffer_query_error_count": self._buffer_query_error_count,
            "consecutive_buffer_query_errors": self._consecutive_buffer_query_errors,
            "last_buffer_query_error": self._last_buffer_query_error_message,
            "last_successful_read_age_s": (
                time.perf_counter() - self._last_successful_read_time
                if self._last_successful_read_time > 0
                else 0.0
            ),
            "polling_interval_ms": self._current_polling_interval * 1000.0,
            "is_running": self.is_running,
            "is_paused": self.is_paused,
        }

    def _apply_phase_spatial_crop(self, phase_data: np.ndarray) -> np.ndarray:
        """Crop single-channel PHASE data before it leaves the acquisition thread."""
        if (
            self._params is None
            or self._data_source != DataSource.PHASE
            or self._channel_num != 1
        ):
            return phase_data

        start, end = resolve_phase_crop_bounds(
            self._point_num_after_merge,
            self._params.phase_demod.crop_distance_start,
            self._params.phase_demod.crop_distance_end,
        )
        if start == 0 and end == self._point_num_after_merge:
            return phase_data

        flat = np.asarray(phase_data).reshape(-1)
        frame_count = flat.size // self._point_num_after_merge
        valid_points = frame_count * self._point_num_after_merge
        if frame_count <= 0:
            return np.ascontiguousarray(flat[:0])
        if valid_points != flat.size:
            log.warning(
                "Discarding incomplete PHASE crop tail: valid_points=%d, actual=%d, points_per_frame=%d",
                valid_points,
                flat.size,
                self._point_num_after_merge,
            )

        frame_matrix = flat[:valid_points].reshape(frame_count, self._point_num_after_merge)
        cropped = np.ascontiguousarray(frame_matrix[:, start:end])
        return cropped.reshape(-1)

    def _apply_monitor_spatial_crop(self, monitor_data: np.ndarray) -> np.ndarray:
        """Crop single-channel monitor data to match the PHASE crop range."""
        if (
            self._params is None
            or self._data_source != DataSource.PHASE
            or self._channel_num != 1
        ):
            return monitor_data

        start, end = resolve_phase_crop_bounds(
            self._point_num_after_merge,
            self._params.phase_demod.crop_distance_start,
            self._params.phase_demod.crop_distance_end,
        )
        if start == 0 and end == self._point_num_after_merge:
            return monitor_data

        return np.ascontiguousarray(monitor_data[start:end])

    def _get_display_points_per_frame(self, data_source: int, channel_num: int) -> int:
        """Return the frame width of data after acquisition-side display shaping."""
        if data_source != DataSource.PHASE:
            return max(0, int(self._total_point_num))

        if self._params is None or max(1, int(channel_num or 1)) != 1:
            return max(0, int(self._point_num_after_merge))

        start, end = resolve_phase_crop_bounds(
            self._point_num_after_merge,
            self._params.phase_demod.crop_distance_start,
            self._params.phase_demod.crop_distance_end,
        )
        return max(0, int(end - start))

    def _reset_buffer_query_error_state(self):
        """Reset consecutive query-error state after a successful buffer query."""
        self._consecutive_buffer_query_errors = 0
        self._last_buffer_query_error_message = ""

    def _is_fatal_buffer_query_error(self, exc: Exception) -> bool:
        """Return True for query errors that indicate a stale device/driver state."""
        if isinstance(exc, PCIe7821Error):
            return True
        return "0xFFFFFFFF" in str(exc)

    def _handle_buffer_query_error(
        self,
        exc: Exception,
        wait_count: int,
        expected_points: int,
        query_time_ms: float,
    ) -> bool:
        """
        Update query-error counters and decide whether the acquisition must stop.

        Returns True when the caller should break the wait loop immediately.
        """
        self._buffer_query_error_count += 1
        self._consecutive_buffer_query_errors += 1
        self._last_buffer_query_error_message = str(exc)
        self._last_wait_iterations = wait_count
        self._last_query_ms = query_time_ms

        fatal = self._is_fatal_buffer_query_error(exc)
        should_stop = fatal or self._consecutive_buffer_query_errors >= BUFFER_QUERY_FAILURE_LIMIT

        if should_stop:
            if fatal:
                message = (
                    "Fatal buffer query error; stopping acquisition. "
                    f"error={exc}"
                )
            else:
                message = (
                    f"Buffer query failed {self._consecutive_buffer_query_errors} consecutive times; "
                    f"stopping acquisition. last_error={exc}"
                )
            self._set_stage(
                "buffer_query_error",
                (
                    f"errors={self._consecutive_buffer_query_errors}, "
                    f"expected_points={expected_points}, query_ms={query_time_ms:.1f}"
                )
            )
            log.error(message)
            self._running = False
            self.error_occurred.emit(message)
            return True

        now = time.perf_counter()
        if now - self._last_buffer_query_error_log_time >= BUFFER_QUERY_FAILURE_LOG_INTERVAL_S:
            log.warning(
                "Error querying buffer (%d/%d): %s",
                self._consecutive_buffer_query_errors,
                BUFFER_QUERY_FAILURE_LIMIT,
                exc,
            )
            self._last_buffer_query_error_log_time = now

        return False

    def run(self):
        """Thread main loop"""
        log.info("=== Acquisition thread started ===")
        self._running = True
        self._frames_acquired = 0
        self._bytes_acquired = 0
        self._loop_count = 0
        self._last_log_time = time.time()
        self._phase_emit_count = 0
        self._raw_emit_count = 0
        self._monitor_emit_count = 0
        self._gui_skip_count = 0
        self._last_buffer_points = 0
        self._last_expected_points = 0
        self._last_wait_iterations = 0
        self._last_query_ms = 0.0
        self._last_read_ms = 0.0
        self._last_api_read_ms = 0.0
        self._last_crop_ms = 0.0
        self._last_dispatch_ms = 0.0
        self._last_display_publish_ms = 0.0
        self._last_display_transform_ms = 0.0
        self._last_monitor_read_ms = 0.0
        self._last_block_bytes = 0
        self._last_display_publish_at = 0.0
        self._buffer_query_error_count = 0
        self._consecutive_buffer_query_errors = 0
        self._last_buffer_query_error_message = ""
        self._last_buffer_query_error_log_time = 0.0
        self._frame_rate_warning_logged = False
        self._last_successful_read_time = time.perf_counter()
        self._acquisition_started_at = self._last_successful_read_time
        self.clear_latest_display_data()
        self._set_stage("started")

        self.acquisition_started.emit()
        log.debug("acquisition_started signal emitted")

        try:
            while self._running:
                self._loop_count += 1
                loop_start = time.perf_counter()

                # Periodic status log (every 5 seconds)
                now = time.time()
                if now - self._last_log_time > 5.0:
                    snapshot = self.get_diagnostics_snapshot()
                    log.info(
                        "Status: "
                        f"loops={snapshot['loop_count']}, frames={snapshot['frames_acquired']}, "
                        f"measured_fps={snapshot['measured_fps']:.1f}, "
                        f"fps_ratio={snapshot['measured_fps_ratio']:.3f}, "
                        f"driver_pending_frames={snapshot['driver_pending_frames']:.0f}, "
                        f"bytes={snapshot['bytes_acquired']/1024/1024:.1f}MB, "
                        f"stage={snapshot['current_stage']}, stage_ms={snapshot['stage_elapsed_ms']:.1f}, "
                        f"buffer={snapshot['last_buffer_points']}/{snapshot['last_expected_points']}, "
                        f"query_ms={snapshot['last_query_ms']:.1f}, read_ms={snapshot['last_read_ms']:.1f}, "
                        f"api_read_ms={snapshot['last_api_read_ms']:.1f}, "
                        f"dispatch_ms={snapshot['last_dispatch_ms']:.1f}, "
                        f"display_pub_ms={snapshot['last_display_publish_ms']:.1f}, "
                        f"emit_phase={snapshot['phase_emit_count']}, emit_raw={snapshot['raw_emit_count']}, "
                        f"gui_skips={snapshot['gui_skip_count']}"
                    )
                    if (
                        snapshot["elapsed_s"] >= 10.0
                        and snapshot["measured_fps_ratio"] < 0.90
                        and not self._frame_rate_warning_logged
                    ):
                        log.warning(
                            "Measured output frame rate is below configured scan rate: configured=%dHz, "
                            "measured=%.1ffps, ratio=%.3f; verify PolarDiv frame semantics and DLL/firmware limits",
                            snapshot["configured_fps"], snapshot["measured_fps"], snapshot["measured_fps_ratio"],
                        )
                        self._frame_rate_warning_logged = True
                    self._last_log_time = now

                # Check for pause
                self._mutex.lock()
                while self._paused and self._running:
                    log.debug("Thread paused, waiting...")
                    self._pause_condition.wait(self._mutex)
                self._mutex.unlock()

                if not self._running:
                    log.info("Thread stopping (running=False after pause check)")
                    break

                # Determine expected data size
                if self._data_source == DataSource.PHASE:
                    expected_points = self._point_num_after_merge * self._frame_num
                else:
                    expected_points = self._total_point_num * self._frame_num
                self._last_expected_points = expected_points

                log.debug(f"Loop {self._loop_count}: waiting for {expected_points} points")

                # Wait for enough data in buffer with dynamic polling
                wait_start = time.perf_counter()
                wait_count = 0
                self._set_stage("wait_buffer", f"expected_points={expected_points}")
                while self._running:
                    self._set_stage(
                        "query_buffer_points",
                        f"wait_count={wait_count}, expected_points={expected_points}"
                    )
                    query_start = time.perf_counter()
                    try:
                        points_in_buffer = self.api.query_buffer_points()
                        query_time = (time.perf_counter() - query_start) * 1000
                        self._last_buffer_points = points_in_buffer
                        self._last_query_ms = query_time
                        self._last_wait_iterations = wait_count
                        self._reset_buffer_query_error_state()

                        if query_time > 50:
                            log.warning(f"Slow query_buffer_points: {query_time:.1f}ms")

                        # Emit buffer status (throttled)
                        if wait_count % 100 == 0:
                            buffer_mb = points_in_buffer * self._channel_num * 2 // (1024 * 1024)
                            self.buffer_status.emit(points_in_buffer, buffer_mb)

                        if points_in_buffer >= expected_points:
                            wait_time = (time.perf_counter() - wait_start) * 1000
                            self._set_stage(
                                "buffer_ready",
                                f"points={points_in_buffer}, wait_ms={wait_time:.1f}, waits={wait_count}"
                            )
                            log.debug(f"Buffer ready: {points_in_buffer} points, waited {wait_time:.1f}ms ({wait_count} iterations)")
                            break

                        # Dynamic polling interval adjustment
                        self._adjust_polling_interval(points_in_buffer, expected_points)
                        self._set_stage(
                            "wait_buffer_sleep",
                            f"points={points_in_buffer}, sleep_ms={self._current_polling_interval * 1000.0:.1f}"
                        )
                        time.sleep(self._current_polling_interval)
                        wait_count += 1

                        if wait_count > 5000:  # 5 second timeout
                            log.error(f"Timeout waiting for data! points_in_buffer={points_in_buffer}, expected={expected_points}")
                            self.error_occurred.emit("Timeout waiting for data")
                            break
                    except Exception as e:
                        query_time = (time.perf_counter() - query_start) * 1000
                        if self._handle_buffer_query_error(e, wait_count, expected_points, query_time):
                            break
                        # Check if we should stop
                        if not self._running:
                            log.info("Thread stopping due to stop request during buffer query")
                            break
                        time.sleep(self._current_polling_interval)
                        wait_count += 1

                if not self._running:
                    log.info("Thread stopping (running=False after wait loop)")
                    break

                # Read data
                try:
                    read_start = time.perf_counter()
                    if self._data_source == DataSource.PHASE:
                        self._set_stage("read_phase_data", f"expected_points={expected_points}")
                        self._read_phase_data()
                    else:
                        self._set_stage("read_raw_data", f"expected_points={expected_points}")
                        self._read_raw_data()
                    read_time = (time.perf_counter() - read_start) * 1000
                    self._last_read_ms = read_time
                    self._set_stage("read_complete", f"read_ms={read_time:.1f}")
                    log.debug(f"Data read completed in {read_time:.1f}ms")

                except PCIe7821Error as e:
                    self._set_stage("read_error", str(e))
                    log.error(f"Read error: {e}")
                    self.error_occurred.emit(str(e))
                    time.sleep(0.1)
                    continue
                except Exception as e:
                    self._set_stage("read_exception", str(e))
                    log.error(f"Unexpected read error: {e}")
                    # Check if we should stop
                    if not self._running:
                        log.info("Thread stopping due to stop request during read error")
                        break
                    self.error_occurred.emit(f"Read error: {e}")
                    time.sleep(0.1)
                    continue

                self._frames_acquired += self._frame_num

                loop_time = (time.perf_counter() - loop_start) * 1000
                slow_threshold_ms = max(100.0, self._expected_block_duration_ms * 1.5)
                if loop_time > slow_threshold_ms:
                    log.warning(
                        f"Slow loop iteration: {loop_time:.1f}ms "
                        f"(expected_block={self._expected_block_duration_ms:.1f}ms)"
                    )

        except Exception as e:
            log.exception(f"Unexpected acquisition error: {e}")
            self.error_occurred.emit(f"Acquisition error: {e}")

        finally:
            self._set_stage("stopped")
            log.info(f"=== Acquisition thread stopped === (loops={self._loop_count}, frames={self._frames_acquired})")
            self.acquisition_stopped.emit()

    def _read_raw_data(self):
        """Read raw IQ data."""
        points_per_ch = self._total_point_num * self._frame_num
        log.debug(f"Reading raw data: {points_per_ch} points/ch, {self._channel_num} channels")
        self._last_monitor_read_ms = 0.0
        self._last_crop_ms = 0.0

        try:
            log.debug(
                f"Calling api.read_data: points_per_ch={points_per_ch}, channels={self._channel_num}, "
                f"estimated_block={points_per_ch * self._channel_num * 2 / 1024 / 1024:.2f}MB"
            )
            api_start = time.perf_counter()
            data, points_returned = self.api.read_data(points_per_ch, self._channel_num)
            self._last_api_read_ms = (time.perf_counter() - api_start) * 1000.0
        except Exception as e:
            log.error(f"Failed to read raw data: {e}")
            raise

        if points_returned != points_per_ch:
            log.warning(f"Raw read returned unexpected point count: requested={points_per_ch}, returned={points_returned}")

        self._bytes_acquired += len(data) * 2  # short = 2 bytes
        self._last_block_bytes = int(np.asarray(data).nbytes)
        self._last_successful_read_time = time.perf_counter()

        if self._channel_num > 1:
            data = data.reshape(-1, self._channel_num)

        dispatch_start = time.perf_counter()
        self._dispatch_full_data(data, self._data_source, self._channel_num)
        self._last_dispatch_ms = (time.perf_counter() - dispatch_start) * 1000.0

        publish_start = time.perf_counter()
        self._publish_latest_display_data(data, self._data_source, self._channel_num)
        self._last_display_publish_ms = (time.perf_counter() - publish_start) * 1000.0

    def _read_phase_data(self):
        """Read phase demodulated data."""
        points_per_ch = self._point_num_after_merge * self._frame_num
        log.debug(f"Reading phase data: {points_per_ch} points/ch, {self._channel_num} channels")

        try:
            log.debug(
                f"Calling api.read_phase_data: points_per_ch={points_per_ch}, channels={self._channel_num}, "
                f"estimated_block={points_per_ch * self._channel_num * 4 / 1024 / 1024:.2f}MB"
            )
            api_start = time.perf_counter()
            phase_dma_view, points_returned = self.api.read_phase_data(
                points_per_ch,
                self._channel_num,
                copy=False,
            )
            self._last_api_read_ms = (time.perf_counter() - api_start) * 1000.0
        except Exception as e:
            log.error(f"Failed to read phase data: {e}")
            raise

        if points_returned != points_per_ch:
            log.warning(f"Phase read returned unexpected point count: requested={points_per_ch}, returned={points_returned}")

        self._bytes_acquired += len(phase_dma_view) * 4  # int = 4 bytes

        crop_start = time.perf_counter()
        phase_data = self._apply_phase_spatial_crop(phase_dma_view)
        if np.shares_memory(phase_data, phase_dma_view):
            phase_data = np.array(phase_data, copy=True, order="C")
        self._last_crop_ms = (time.perf_counter() - crop_start) * 1000.0
        self._last_block_bytes = int(np.asarray(phase_data).nbytes)
        self._last_successful_read_time = time.perf_counter()

        if self._channel_num > 1:
            phase_data = phase_data.reshape(-1, self._channel_num)

        dispatch_start = time.perf_counter()
        self._dispatch_full_data(phase_data, DataSource.PHASE, self._channel_num)
        self._last_dispatch_ms = (time.perf_counter() - dispatch_start) * 1000.0

        publish_start = time.perf_counter()
        self._publish_latest_display_data(phase_data, DataSource.PHASE, self._channel_num)
        self._last_display_publish_ms = (time.perf_counter() - publish_start) * 1000.0

        self._read_monitor_data_if_enabled()

    def _read_monitor_data_if_enabled(self):
        """Read monitor data only when the user has enabled the monitor plot."""
        self._last_monitor_read_ms = 0.0
        if not self._monitor_read_enabled or not self._display_active:
            self._pending_monitor_data = None
            return

        try:
            monitor_start = time.perf_counter()
            monitor_data = self.api.read_monitor_data(
                self._point_num_after_merge, self._channel_num
            )
            self._last_monitor_read_ms = (time.perf_counter() - monitor_start) * 1000.0
            monitor_data = self._apply_monitor_spatial_crop(monitor_data)
            self._pending_monitor_data = (monitor_data, self._channel_num)
            self._emit_if_ready()
        except PCIe7821Error as e:
            log.warning(f"Monitor data read failed (non-critical): {e}")
        except Exception as e:
            log.warning(f"Monitor data read failed (non-critical): {e}")

    def set_full_data_handler(self, handler: Optional[Callable[[np.ndarray, int, int], None]]):
        """Set a non-GUI handler for saving and communication queue handoff."""
        self._full_data_handler = handler

    def set_monitor_read_enabled(self, enabled: bool):
        """Enable monitor DLL reads only while the monitor plot is visible."""
        self._monitor_read_enabled = bool(enabled and self._data_source == DataSource.PHASE)
        if not self._monitor_read_enabled:
            self._pending_monitor_data = None
            self._last_monitor_read_ms = 0.0

    def _dispatch_full_data(self, data: np.ndarray, data_source: int, channel_num: int):
        """Dispatch one complete acquisition block without entering the GUI event queue."""
        if self._full_data_handler is None:
            return
        try:
            self._full_data_handler(data, data_source, channel_num)
        except Exception as e:
            log.exception(f"Full data handler failed: {e}")

    def _append_display_history(self, frame_matrix: np.ndarray, target_frames: int, signature) -> None:
        """Append one read block to the retained display history."""
        if self._display_history_signature != signature:
            self._display_history_chunks = []
            self._display_history_frames = 0
            self._display_history_signature = signature
            self._last_display_publish_at = 0.0

        if not frame_matrix.flags.c_contiguous:
            frame_matrix = np.ascontiguousarray(frame_matrix)
        if frame_matrix.size:
            self._display_history_chunks.append(frame_matrix)
            self._display_history_frames += int(frame_matrix.shape[0])

        while self._display_history_frames > target_frames and self._display_history_chunks:
            overflow = self._display_history_frames - target_frames
            first = self._display_history_chunks[0]
            if overflow >= first.shape[0]:
                self._display_history_chunks.pop(0)
                self._display_history_frames -= int(first.shape[0])
            else:
                self._display_history_chunks[0] = first[overflow:]
                self._display_history_frames -= int(overflow)
                break

    def _should_publish_display_snapshot(self) -> bool:
        now = time.perf_counter()
        return (
            self._last_display_publish_at <= 0.0
            or now - self._last_display_publish_at
            >= self._display_publish_interval_s - self._display_publish_tolerance_s
        )

    def _current_display_history(self) -> np.ndarray:
        if not self._display_history_chunks:
            return np.empty((0, 0), dtype=np.int32)
        if len(self._display_history_chunks) == 1:
            return np.ascontiguousarray(self._display_history_chunks[0])
        return np.ascontiguousarray(np.concatenate(self._display_history_chunks, axis=0))

    def _apply_time_space_display_filter(self, block: np.ndarray) -> np.ndarray:
        """Apply the stateful time-axis filter to one rad-scaled display block."""
        if not self._display_filter_enabled:
            return block
        scan_rate = max(1, int(self._params.basic.scan_rate)) if self._params is not None else 1
        try:
            spec = parse_filter_spec(self._display_filter_spec)
            return self._time_space_filter.process(block, spec, scan_rate)
        except FilterSpecError as exc:
            log.warning("Time-space filter skipped in acquisition thread: %s", exc)
            return block

    def _build_time_space_display_block(self, frame_matrix: np.ndarray):
        """Apply rad conversion, distance crop and downsampling to one display history window.

        Returns a ``(display_block, source_frame_count, block_duration_s, distance_start,
        distance_end)`` tuple where ``display_block`` is the ``(space, time)`` float32
        array ready for ``ImageItem.setImage``, or ``None`` when nothing remains.
        """
        frame_count, point_count = frame_matrix.shape
        start = max(0, min(self._display_distance_start, max(0, point_count - 1)))
        end = (
            point_count
            if self._display_distance_end <= 0
            else min(point_count, max(start + 1, self._display_distance_end))
        )
        if start >= end:
            return None

        block = frame_matrix[:, start:end]
        if self._display_space_downsample > 1:
            block = block[:, :: self._display_space_downsample]
        # Without filtering the slice commutes with rad scaling. Decimate before
        # float conversion rather than allocating a full-rate display matrix.
        if not self._display_filter_enabled and self._display_time_downsample > 1:
            block = block[:: self._display_time_downsample, :]
        if self._display_rad_enabled:
            block = np.asarray(block).astype(np.float32, copy=True)
            block *= np.float32(np.pi / 32767.0)
        block = self._apply_time_space_display_filter(block)
        if self._display_filter_enabled and self._display_time_downsample > 1:
            block = block[:: self._display_time_downsample, :]
        if block.size == 0:
            return None

        block = np.ascontiguousarray(block.T)  # (space, time)
        scan_rate = (
            max(1.0, float(self._params.basic.scan_rate))
            if self._params is not None
            else 1.0
        )
        block_duration_s = frame_count / scan_rate
        return block, frame_count, block_duration_s, start, end

    def set_display_active(self, active):
        """Visibility gate only; full-data dispatch/storage remain independent."""
        active = bool(active)
        if active != self._display_active:
            self._display_active = active
            self._display_visibility_generation += 1
            with self._latest_display_lock:
                self._latest_display_data = None

    def _publish_latest_display_data(self, data: np.ndarray, data_source: int, channel_num: int):
        """Refresh the overwriteable GUI snapshot at the GUI display cadence."""
        if self._applied_display_visibility_generation != self._display_visibility_generation:
            # Reset streaming display state in its owning acquisition thread.
            self.clear_latest_display_data()
            self._time_space_filter.reset_state()
            self._applied_display_visibility_generation = self._display_visibility_generation
        if not self._display_active:
            return
        start = time.perf_counter()
        channel_num = max(1, int(channel_num or 1))
        points_per_frame = self._get_display_points_per_frame(data_source, channel_num)
        if points_per_frame <= 0:
            self._last_display_publish_ms = (time.perf_counter() - start) * 1000.0
            return
        target_frames = max(1, int(self._display_target_frames or self._params.display.frame_plot_num))
        arr = np.asarray(data)
        snapshot_kind = 0
        if int(data_source) == int(DataSource.PHASE):
            if self._display_incremental_full_width:
                snapshot_kind = 3 if self._display_image_enabled else 4
            elif not self._display_requires_full_window:
                snapshot_kind = 1 if int(self._display_mode or 0) == 1 else 2
        history_target_frames = target_frames if snapshot_kind != 2 else 4

        if channel_num == 1:
            flat = arr.reshape(-1)
            frame_count = flat.size // points_per_frame
            valid_points = frame_count * points_per_frame
            if frame_count <= 0:
                self._last_display_publish_ms = (time.perf_counter() - start) * 1000.0
                return
            frame_matrix = flat[:valid_points].reshape(frame_count, points_per_frame)
            if snapshot_kind == 1:
                region_index = min(max(0, self._display_region_index), points_per_frame - 1)
                frame_matrix = frame_matrix[:, region_index : region_index + 1]
            elif snapshot_kind == 2:
                frame_matrix = frame_matrix[-history_target_frames:]
            self._append_display_history(
                frame_matrix,
                history_target_frames,
                (
                    int(data_source),
                    channel_num,
                    points_per_frame,
                    snapshot_kind,
                    self._display_region_index if snapshot_kind == 1 else -1,
                ),
            )
        else:
            matrix = arr.reshape(-1, channel_num)
            frame_count = matrix.shape[0] // points_per_frame
            valid_rows = frame_count * points_per_frame
            if frame_count <= 0:
                self._last_display_publish_ms = (time.perf_counter() - start) * 1000.0
                return
            frame_matrix = matrix[:valid_rows, :].reshape(frame_count, points_per_frame * channel_num)
            if snapshot_kind == 1:
                region_index = min(max(0, self._display_region_index), points_per_frame - 1)
                start = region_index * channel_num
                frame_matrix = frame_matrix[:, start : start + channel_num]
            elif snapshot_kind == 2:
                frame_matrix = frame_matrix[-history_target_frames:]
            self._append_display_history(
                frame_matrix,
                history_target_frames,
                (
                    int(data_source),
                    channel_num,
                    points_per_frame,
                    snapshot_kind,
                    self._display_region_index if snapshot_kind == 1 else -1,
                ),
            )

        if not self._should_publish_display_snapshot():
            self._last_display_publish_ms = (time.perf_counter() - start) * 1000.0
            return

        with self._latest_display_lock:
            if self._latest_display_data is not None:
                self._gui_skip_count += 1
                self._last_display_publish_at = time.perf_counter()
                if snapshot_kind in (3, 4):
                    self._display_history_chunks = []
                    self._display_history_frames = 0
                self._last_display_publish_ms = (self._last_display_publish_at - start) * 1000.0
                return

        history = self._current_display_history()
        if history.size == 0:
            self._last_display_publish_ms = (time.perf_counter() - start) * 1000.0
            return

        display_meta = None
        if snapshot_kind == 4 and data_source == DataSource.PHASE and channel_num == 1:
            display_data, display_meta = self._build_waveform_only_snapshot(history)
        elif snapshot_kind == 3 and data_source == DataSource.PHASE and channel_num == 1:
            transform_start = time.perf_counter()
            transformed = self._build_time_space_display_block(history)
            self._last_display_transform_ms = (time.perf_counter() - transform_start) * 1000.0
            if transformed is None:
                self._last_display_publish_ms = (time.perf_counter() - start) * 1000.0
                return
            display_data, source_frame_count, block_duration_s, distance_start, distance_end = transformed
            region = min(max(0, self._display_region_index), history.shape[1] - 1)
            # Carry only ONE full-rate temporal channel alongside the decimated
            # image. PSD must not inherit Time-Space display decimation.
            waveform = np.array(history[:, region], dtype=np.float32, copy=True)
            # Retain one full-resolution spatial profile for TIME mode, alongside
            # the selected full-rate channel used by SPACE waveform/PSD.
            spatial_profile = np.array(history[-1], dtype=np.float32, copy=True)
            display_meta = (source_frame_count, block_duration_s, distance_start, distance_end, waveform, spatial_profile)
        elif channel_num == 1:
            display_data = np.ascontiguousarray(history.reshape(-1))
        else:
            display_data = np.ascontiguousarray(history.reshape(-1, channel_num))

        with self._latest_display_lock:
            self._latest_display_data = (
                display_data,
                data_source,
                channel_num,
                snapshot_kind,
                display_meta,
            )
        if snapshot_kind in (3, 4):
            self._display_history_chunks = []
            self._display_history_frames = 0

        self.display_snapshot_ready.emit()
        if data_source == DataSource.PHASE:
            self._phase_emit_count += 1
        else:
            self._raw_emit_count += 1
        self._last_display_publish_at = time.perf_counter()
        self._last_display_publish_ms = (self._last_display_publish_at - start) * 1000.0

    def _build_waveform_only_snapshot(self, history):
        """Prepare only a trace/profile off the GUI thread; no hidden image."""
        frames, width = history.shape
        region = min(max(0, self._display_region_index), width - 1)
        temporal = int(self._display_mode or 0) == 1
        source = history[:, region:region + 1] if temporal else history
        trace = np.empty(frames, dtype=np.float32)
        profile = np.array(history[-1], dtype=np.float32, copy=True)
        scale = np.float32(np.pi / 32767.) if self._display_rad_enabled else np.float32(1.)
        chunk_frames = max(1, min(8192, 1048576 // max(1, source.shape[1])))
        for offset in range(0, frames, chunk_frames):
            block = np.array(source[offset:offset + chunk_frames], dtype=np.float32, copy=True)
            block *= scale
            filtered = self._apply_time_space_display_filter(block)
            trace[offset:offset + len(block)] = filtered[:, 0 if temporal else region]
            if not temporal:
                profile = np.asarray(filtered[-1], dtype=np.float32)
        if temporal:
            profile *= scale
        duration = frames / max(float(self._params.basic.scan_rate), 1.)
        return trace, (duration, profile)

    def take_latest_display_data(self):
        """Atomically take the newest display snapshot for GUI-timer consumption."""
        with self._latest_display_lock:
            latest = self._latest_display_data
            self._latest_display_data = None
        return latest

    def clear_latest_display_data(self):
        """Release any display snapshot that has not yet been consumed."""
        with self._latest_display_lock:
            self._latest_display_data = None
        self._display_history_chunks = []
        self._display_history_frames = 0
        self._display_history_signature = None
        self._last_display_publish_at = 0.0

    def _emit_if_ready(self):
        """Emit pending data signals if enough time has passed since last update"""
        if not self._display_active:
            self._pending_phase_data = self._pending_raw_data = self._pending_monitor_data = None
            return
        current_time = time.perf_counter() * 1000  # ms
        elapsed = current_time - self._last_gui_update_time

        if elapsed < MIN_GUI_UPDATE_INTERVAL_MS:
            # Not enough time passed, skip this update (keep latest data pending)
            self._gui_skip_count += 1
            return

        # Emit all pending signals
        signals_emitted = 0

        if self._pending_phase_data is not None:
            phase_data, channel_num = self._pending_phase_data
            self._phase_emit_count += 1
            self._set_stage("emit_phase_signal", f"emit_seq={self._phase_emit_count}")
            log.debug(
                f"Emitting phase_data_ready signal: seq={self._phase_emit_count}, "
                f"shape={phase_data.shape}, bytes={np.asarray(phase_data).nbytes / 1024 / 1024:.2f}MB"
            )
            self.phase_data_ready.emit(phase_data, channel_num)
            self._pending_phase_data = None
            signals_emitted += 1

        if self._pending_raw_data is not None:
            data, data_source, channel_num = self._pending_raw_data
            self._raw_emit_count += 1
            self._set_stage("emit_raw_signal", f"emit_seq={self._raw_emit_count}")
            log.debug(
                f"Emitting data_ready signal: seq={self._raw_emit_count}, shape={data.shape}, "
                f"dtype={data.dtype}, bytes={np.asarray(data).nbytes / 1024 / 1024:.2f}MB"
            )
            self.data_ready.emit(data, data_source, channel_num)
            self._pending_raw_data = None
            signals_emitted += 1

        if self._pending_monitor_data is not None:
            monitor_data, channel_num = self._pending_monitor_data
            self._monitor_emit_count += 1
            self._set_stage("emit_monitor_signal", f"emit_seq={self._monitor_emit_count}")
            log.debug(
                f"Emitting monitor_data_ready signal: seq={self._monitor_emit_count}, "
                f"shape={monitor_data.shape}, bytes={np.asarray(monitor_data).nbytes / 1024:.1f}KB"
            )
            self.monitor_data_ready.emit(monitor_data, channel_num)
            self._pending_monitor_data = None
            signals_emitted += 1

        if signals_emitted > 0:
            self._last_gui_update_time = current_time
            self._set_stage("emit_complete", f"signals={signals_emitted}, elapsed_ms={elapsed:.1f}")
            log.debug(f"GUI update: emitted {signals_emitted} signals, elapsed={elapsed:.1f}ms")

    def _adjust_polling_interval(self, points_in_buffer: int, expected_points: int):
        """Adjust polling interval based on buffer usage"""
        if expected_points == 0:
            return

        buffer_usage_ratio = points_in_buffer / expected_points

        if buffer_usage_ratio >= self._buffer_threshold_high:
            # High buffer usage - use high frequency polling
            self._current_polling_interval = self._high_freq_interval
        elif buffer_usage_ratio <= self._buffer_threshold_low:
            # Low buffer usage - use low frequency polling
            self._current_polling_interval = self._low_freq_interval
        # else: keep current interval (hysteresis)

        # Log interval changes (throttled)
        if self._loop_count % 100 == 0:
            log.debug(f"Buffer usage: {buffer_usage_ratio:.1%}, polling interval: {self._current_polling_interval*1000:.1f}ms")

    def stop(self):
        """Request acquisition thread stop (non-blocking)."""
        snapshot = self.get_diagnostics_snapshot()
        log.info(
            "Stop requested: "
            f"stage={snapshot['current_stage']}, stage_ms={snapshot['stage_elapsed_ms']:.1f}, "
            f"buffer={snapshot['last_buffer_points']}/{snapshot['last_expected_points']}, "
            f"query_ms={snapshot['last_query_ms']:.1f}, read_ms={snapshot['last_read_ms']:.1f}"
        )
        self._running = False
        self.clear_latest_display_data()
        self._set_stage("stop_requested")

        # Wake up if paused
        self._mutex.lock()
        self._paused = False
        self._pause_condition.wakeAll()
        self._mutex.unlock()

    def wait_until_stopped(self, timeout_ms: int = 5000) -> bool:
        """Wait for thread exit without force-termination."""
        if not self.isRunning():
            return True

        log.debug(f"Waiting for thread to finish (timeout={timeout_ms}ms)...")
        stopped = self.wait(timeout_ms)
        if not stopped:
            snapshot = self.get_diagnostics_snapshot()
            log.warning(
                f"Thread did not finish in {timeout_ms}ms: stage={snapshot['current_stage']}, "
                f"stage_ms={snapshot['stage_elapsed_ms']:.1f}, buffer={snapshot['last_buffer_points']}/"
                f"{snapshot['last_expected_points']}, query_ms={snapshot['last_query_ms']:.1f}, "
                f"read_ms={snapshot['last_read_ms']:.1f}"
            )
        else:
            log.debug("Thread finished gracefully")
        return stopped

    def pause(self):
        """Pause acquisition"""
        log.info("Pause requested")
        self._mutex.lock()
        self._paused = True
        self._mutex.unlock()

    def resume(self):
        """Resume acquisition"""
        log.info("Resume requested")
        self._mutex.lock()
        self._paused = False
        self._pause_condition.wakeAll()
        self._mutex.unlock()

    @property
    def is_running(self) -> bool:
        """Check if acquisition is running"""
        return self._running and self.isRunning()

    @property
    def is_paused(self) -> bool:
        """Check if acquisition is paused"""
        return self._paused

    @property
    def frames_acquired(self) -> int:
        """Get number of frames acquired"""
        return self._frames_acquired

    @property
    def bytes_acquired(self) -> int:
        """Get total bytes acquired"""
        return self._bytes_acquired

    @property
    def point_num_after_merge(self) -> int:
        """Get points per scan after merge"""
        return self._point_num_after_merge

    @property
    def total_point_num(self) -> int:
        """Get total points per scan"""
        return self._total_point_num


# ----- SIMULATED ACQUISITION THREAD -----
# Generates random data for UI testing without hardware

class SimulatedAcquisitionThread(AcquisitionThread):
    """
    Simulated acquisition thread for testing without hardware.
    """

    def __init__(self, parent=None):
        """Initialize with dummy API"""
        log.info("Creating SimulatedAcquisitionThread")

        # Create a mock API
        class MockAPI:
            def query_buffer_points(self):
                return 100000

            def read_data(self, n, c):
                return np.random.randint(-32768, 32767, n*c, dtype=np.int16), n

            def read_phase_data(self, n, c):
                return np.random.randint(-100000, 100000, n*c, dtype=np.int32), n

            def read_monitor_data(self, n, c):
                return np.random.randint(0, 65535, n*c, dtype=np.uint32)

        self._mock_api = MockAPI()
        super().__init__(self._mock_api, parent)

    def run(self):
        """Simulated acquisition loop"""
        log.info("=== Simulated acquisition thread started ===")
        self._running = True
        self._frames_acquired = 0
        self._bytes_acquired = 0
        self._loop_count = 0
        self._last_log_time = time.time()

        self.acquisition_started.emit()

        try:
            while self._running:
                self._loop_count += 1
                loop_start = time.perf_counter()

                # Periodic status log
                now = time.time()
                if now - self._last_log_time > 5.0:
                    log.info(f"Simulation status: loops={self._loop_count}, frames={self._frames_acquired}")
                    self._last_log_time = now

                # Check for pause
                self._mutex.lock()
                while self._paused and self._running:
                    self._pause_condition.wait(self._mutex)
                self._mutex.unlock()

                if not self._running:
                    break

                # Simulate acquisition delay based on scan rate
                scan_rate = self._params.basic.scan_rate if self._params else 2000
                delay = self._frame_num / max(scan_rate, 1)
                time.sleep(delay)

                # Generate simulated data
                if self._data_source == DataSource.PHASE:
                    points = self._point_num_after_merge * self._frame_num
                    phase_data = np.random.randint(-100000, 100000, points * self._channel_num, dtype=np.int32)

                    phase_data = self._apply_phase_spatial_crop(phase_data)

                    if self._channel_num > 1:
                        phase_data = phase_data.reshape(-1, self._channel_num)

                    self._dispatch_full_data(phase_data, DataSource.PHASE, self._channel_num)
                    self._publish_latest_display_data(phase_data, DataSource.PHASE, self._channel_num)
                    self._bytes_acquired += len(phase_data.flatten()) * 4

                    # Simulated monitor data
                    if self._monitor_read_enabled and self._display_active:
                        monitor_data = np.random.randint(0, 65535, self._point_num_after_merge * self._channel_num, dtype=np.uint32)
                        monitor_data = self._apply_monitor_spatial_crop(monitor_data)
                        self._pending_monitor_data = (monitor_data, self._channel_num)
                        self._emit_if_ready()
                else:
                    points = self._total_point_num * self._frame_num
                    data = np.random.randint(-32768, 32767, points * self._channel_num, dtype=np.int16)

                    if self._channel_num > 1:
                        data = data.reshape(-1, self._channel_num)

                    self._dispatch_full_data(data, self._data_source, self._channel_num)
                    self._publish_latest_display_data(data, self._data_source, self._channel_num)
                    self._bytes_acquired += len(data.flatten()) * 2
                self._last_successful_read_time = time.perf_counter()

                # Emit buffer status (throttle this too - only every 10 loops)
                if self._loop_count % 10 == 0:
                    self.buffer_status.emit(100000, 10)

                self._frames_acquired += self._frame_num

                loop_time = (time.perf_counter() - loop_start) * 1000
                log.debug(f"Simulation loop {self._loop_count}: {loop_time:.1f}ms")

        except Exception as e:
            log.exception(f"Simulation error: {e}")
            self.error_occurred.emit(f"Simulation error: {e}")

        finally:
            log.info(f"=== Simulated acquisition thread stopped === (loops={self._loop_count})")
            self.acquisition_stopped.emit()
