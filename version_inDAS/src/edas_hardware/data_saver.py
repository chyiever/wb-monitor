"""
`src/data_saver.py` 负责把采集得到的完整数据块异步写入磁盘，是实时链路中的后台存储模块。

当前工程采用的是典型生产者-消费者模型。前台线程只做 `put_nowait()` 入队，不在采集回调里直接执行磁盘写入；后台保存线程串行取队列、必要时完成 `dtype` 统一、写入二进制文件并处理分文件请求。这样做的目标，是在采集与磁盘吞吐冲突时优先保护采集线程与 GUI 的实时性。

如果后续需要更强的数据可靠性，应在这里继续扩展文件头、元数据索引、写入确认或失败恢复策略，而不是把写盘重新塞回 GUI 线程。
"""
import os
import queue
import shutil
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
import numpy as np

from .logger import get_logger
from .bz_format import (
    BitshuffleZstdCompressor,
    CompressedPacket,
    RawPacket,
    pack_bz_file_header,
)

MIB = 1024 * 1024
DEFAULT_STORAGE_MEMORY_BUDGET_BYTES = 1536 * MIB
MIN_STORAGE_MEMORY_BUDGET_BYTES = 64 * MIB
DEFAULT_DISK_RESERVE_BYTES = 64 * MIB


def calculate_storage_queue_capacities(
    block_bytes: int,
    packet_bytes: int,
    configured_max_blocks: int = 200,
    available_memory_bytes: Optional[int] = None,
) -> Dict[str, int]:
    """Size asynchronous storage queues by bytes instead of a fixed block count."""
    block_bytes = max(1, int(block_bytes))
    packet_bytes = max(1, int(packet_bytes))
    configured_max_blocks = max(1, int(configured_max_blocks))
    memory_budget = DEFAULT_STORAGE_MEMORY_BUDGET_BYTES
    if available_memory_bytes is not None and int(available_memory_bytes) > 0:
        memory_budget = min(
            memory_budget,
            max(MIN_STORAGE_MEMORY_BUDGET_BYTES, int(available_memory_bytes) // 10),
        )

    stage_budget = max(1, memory_budget // 3)
    raw_blocks = max(1, min(configured_max_blocks, stage_budget // block_bytes))
    packet_items = max(1, min(8, stage_budget // packet_bytes))
    compressed_items = max(1, min(8, stage_budget // packet_bytes))
    return {
        "memory_budget_bytes": memory_budget,
        "raw_blocks": raw_blocks,
        "packet_items": packet_items,
        "compressed_items": compressed_items,
        "estimated_raw_queue_bytes": raw_blocks * block_bytes,
        "estimated_packet_queue_bytes": packet_items * packet_bytes,
        "estimated_compressed_queue_bytes": compressed_items * packet_bytes,
    }


log = get_logger("data_saver")


class _DroppedPacketMarker:
    """Placeholder used to keep ordered .bz writer progress after compression failures."""

    def __init__(self, packet_index: int):
        self.packet_index = int(packet_index)


# ----- BASE DATA SAVER -----
# Single-file async saver: data queued from producer, written by background thread

class DataSaver:
    """
    Asynchronous data saver with queue-based buffering.

    Saves data to binary files in the format: {seq}-{HH}-{MM}-{SS}-{scan_rate}.bin
    Example: 1-12-30-45-2000.bin
    """

    def __init__(self, save_path: str = "save_data", buffer_size: int = 100,
                 overwrite_oldest_when_full: bool = False,
                 disk_reserve_bytes: int = DEFAULT_DISK_RESERVE_BYTES):
        """
        Initialize data saver.

        Args:
            save_path: Directory to save files
            buffer_size: Maximum number of data blocks in queue
        """
        self.save_path = Path(save_path)
        self.buffer_size = buffer_size
        self.overwrite_oldest_when_full = bool(overwrite_oldest_when_full)
        self.disk_reserve_bytes = max(0, int(disk_reserve_bytes))

        self._data_queue: queue.Queue = queue.Queue(maxsize=buffer_size)
        self._split_marker = object()
        self._save_thread: Optional[threading.Thread] = None
        self._running = False
        self._lifecycle_lock = threading.Lock()
        self._file_handle = None
        self._file_no = 0
        self._current_filename = ""
        self._scan_rate = 2000  # Default scan rate

        # Statistics
        self._bytes_written = 0
        self._blocks_written = 0
        self._dropped_blocks = 0
        self._write_error = ""
        self.completed_file_count = 0
        self._enqueue_count = 0
        self._max_queue_size_seen = 0
        self._last_write_ms = 0.0
        self._last_write_bytes = 0
        self._last_enqueue_ms = 0.0
        self._max_enqueue_ms = 0.0
        self._last_enqueued_block_bytes = 0

        self._overwritten_files = 0
        self._overwritten_bytes = 0

    def start(self, file_no: Optional[int] = None, scan_rate: int = 2000) -> str:
        """
        Start data saving.

        Args:
            file_no: Optional file number. If None, auto-increment.
            scan_rate: Scan rate in Hz for filename

        Returns:
            The filename being written to
        """
        if self._running:
            return self._current_filename

        # Ensure save directory exists
        self.save_path.mkdir(parents=True, exist_ok=True)

        # Set file number
        if file_no is not None:
            self._file_no = file_no
        else:
            self._file_no += 1

        self._scan_rate = scan_rate

        # Create filename with timestamp and scan rate
        # Format: seq-HH-MM-SS-scanrate.bin
        now = datetime.now()
        self._current_filename = f"{self._file_no}-{now.hour:02d}-{now.minute:02d}-{now.second:02d}-{scan_rate}.bin"

        # Open file
        filepath = self.save_path / self._current_filename
        self._file_handle = open(filepath, 'wb')

        log.info(f"Started saving to {filepath} (queue_capacity={self.buffer_size})")

        # Reset statistics
        self._bytes_written = 0
        self._blocks_written = 0
        self._dropped_blocks = 0
        self._write_error = ""
        self.completed_file_count = 0
        self._enqueue_count = 0
        self._max_queue_size_seen = 0
        self._last_write_ms = 0.0
        self._last_write_bytes = 0
        self._last_enqueue_ms = 0.0
        self._max_enqueue_ms = 0.0

        # Clear queue
        self._overwritten_files = 0
        self._overwritten_bytes = 0
        while not self._data_queue.empty():
            try:
                self._data_queue.get_nowait()
            except queue.Empty:
                break

        # Start save thread
        self._running = True
        self._save_thread = threading.Thread(target=self._save_loop, daemon=True)
        self._save_thread.start()

        return self._current_filename

    def stop(self):
        """Stop data saving and close file"""
        with self._lifecycle_lock:
            if not self._running:
                return
            self._running = False

            # The sentinel is ordered under the same lock as producer enqueues.
            if self._save_thread is not None:
                try:
                    self._data_queue.put(None, timeout=1.0)
                except queue.Full:
                    log.warning("Save queue full while stopping; waiting to enqueue sentinel")
                    self._data_queue.put(None)

        # Wait for save thread to drain queued data and exit.
        if self._save_thread is not None:

            self._join_thread_until_stopped(self._save_thread, "save thread")
            self._save_thread = None

        # Close file after the save thread has finished all pending writes.
        self._close_output_file()

        log.info(f"Stopped saving. Bytes written: {self._bytes_written}, "
                 f"Blocks: {self._blocks_written}, Dropped: {self._dropped_blocks}, "
                 f"Max queue: {self._max_queue_size_seen}/{self.buffer_size}, "
                 f"Last write: {self._last_write_ms:.1f}ms/{self._last_write_bytes}B")

    def _close_output_file(self):
        if self._file_handle is not None:
            try:
                self._file_handle.flush()
                os.fsync(self._file_handle.fileno())
                self._file_handle.close()
                self._file_handle = None
                has_data = self._file_frames_written > 0 if hasattr(self, '_file_frames_written') else self._bytes_written > 0
                if has_data: self.completed_file_count += 1
            except OSError as exc:
                self._write_error = str(exc)
                raise

    def save(self, data: np.ndarray) -> bool:
        """Queue one owned array reference for background saving."""
        queued, enqueue_ms, reason = self._enqueue_owned(data)
        if reason == "stopped":
            return False

        self._last_enqueue_ms = enqueue_ms
        self._max_enqueue_ms = max(self._max_enqueue_ms, enqueue_ms)
        block_bytes = int(data.nbytes) if isinstance(data, np.ndarray) else len(data)
        if not queued:
            self._dropped_blocks += 1
            log.warning(
                f"Save queue full, dropping block: bytes={block_bytes}, "
                f"dropped={self._dropped_blocks}, queue={self._data_queue.qsize()}/{self.buffer_size}"
            )
            return False

        self._enqueue_count += 1
        self._last_enqueued_block_bytes = block_bytes
        queue_size = self._data_queue.qsize()
        self._max_queue_size_seen = max(self._max_queue_size_seen, queue_size)
        if (
            self._enqueue_count <= 3
            or self._enqueue_count % 20 == 0
            or queue_size >= max(1, self.buffer_size // 2)
        ):
            log.debug(
                f"Queued save block #{self._enqueue_count}: bytes={block_bytes}, "
                f"queue={queue_size}/{self.buffer_size}"
            )
        return True

    def _enqueue_owned(
        self,
        data: np.ndarray,
        timeout_s: Optional[float] = None,
    ) -> tuple[bool, float, str]:
        """Atomically order producer data before the stop sentinel."""
        enqueue_start = time.perf_counter()
        with self._lifecycle_lock:
            if not self._running:
                return False, 0.0, "stopped"
            restore_writeable = self._freeze_queued_array(data)
            try:
                if timeout_s is None:
                    self._data_queue.put_nowait(data)
                else:
                    self._data_queue.put(data, timeout=max(0.0, float(timeout_s)))
            except queue.Full:
                self._restore_queued_array(data, restore_writeable)
                elapsed_ms = (time.perf_counter() - enqueue_start) * 1000.0
                return False, elapsed_ms, "full"
        elapsed_ms = (time.perf_counter() - enqueue_start) * 1000.0
        return True, elapsed_ms, "queued"

    @staticmethod
    def _freeze_queued_array(data: np.ndarray) -> bool:
        """Transfer an ndarray to background consumers without an expensive copy."""
        if isinstance(data, np.ndarray) and data.flags.writeable:
            data.flags.writeable = False
            return True
        return False

    @staticmethod
    def _restore_queued_array(data: np.ndarray, restore_writeable: bool) -> None:
        if restore_writeable and isinstance(data, np.ndarray):
            data.flags.writeable = True

    @staticmethod
    def _join_thread_until_stopped(thread: threading.Thread, label: str) -> None:
        """Wait for queued data to drain; never close a file under a live writer."""
        while thread.is_alive():
            thread.join(timeout=5.0)
            if thread.is_alive():
                log.warning(f"Waiting for {label} to drain storage queues")

    def _save_loop(self):
        """Background thread for saving data"""
        while True:
            try:
                item = self._data_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            except Exception as e:
                self._write_error = str(e)
                log.error(f"DataSaver error: {e}")
                continue

            try:
                if item is None:  # Sentinel
                    break
                if item is self._split_marker:
                    self._handle_split_request()
                    continue
                self._write_data(item)
            except Exception as e:
                self._write_error = str(e)
                log.error(f"DataSaver error: {e}")

    def _handle_split_request(self):
        """Handle a queued split request. Base saver does not split files."""
        return

    def _write_data(self, data):
        """Serialize one queued block and write it to disk."""
        if self._file_handle is not None:
            start = time.perf_counter()
            if isinstance(data, np.ndarray):
                if data.dtype != np.int32:
                    data = data.astype(np.int32)
                payload = memoryview(np.ascontiguousarray(data)).cast("B")
            else:
                payload = data

            self._ensure_disk_space(len(payload))
            self._file_handle.write(payload)
            self._last_write_ms = (time.perf_counter() - start) * 1000
            self._last_write_bytes = len(payload)
            self._bytes_written += len(payload)
            self._blocks_written += 1
            if self._last_write_ms > 50:
                log.warning(
                    f"Slow disk write: {self._last_write_ms:.1f}ms, bytes={len(payload)}, "
                    f"queue={self._data_queue.qsize()}/{self.buffer_size}"
                )

    def _oldest_deletable_data_files(self) -> List[Path]:
        """Return closed data files in the save directory, oldest first."""
        active_name = Path(self._current_filename).name if self._current_filename else ""
        candidates = []
        try:
            for path in self.save_path.iterdir():
                if not path.is_file() or path.suffix.lower() not in {".bin", ".bz"}:
                    continue
                if path.name == active_name:
                    continue
                try:
                    stat = path.stat()
                except OSError:
                    continue
                candidates.append((stat.st_mtime_ns, path.name, path))
        except OSError as exc:
            log.warning(f"Unable to scan storage directory for overwrite candidates: {exc}")
            return []
        candidates.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in candidates]

    def _ensure_disk_space(self, required_bytes: int) -> None:
        """Reclaim old data files before a write when overwrite mode is enabled."""
        if not self.overwrite_oldest_when_full:
            return

        required_bytes = max(0, int(required_bytes))
        target_free = required_bytes + self.disk_reserve_bytes
        try:
            free_bytes = int(shutil.disk_usage(self.save_path).free)
        except OSError as exc:
            log.warning(f"Unable to query free disk space for {self.save_path}: {exc}")
            return

        if free_bytes >= target_free:
            return

        for path in self._oldest_deletable_data_files():
            try:
                file_bytes = int(path.stat().st_size)
                path.unlink()
            except OSError as exc:
                log.warning(f"Failed to delete old storage file {path}: {exc}")
                continue

            free_bytes += file_bytes
            self._overwritten_files += 1
            self._overwritten_bytes += file_bytes
            log.warning(
                f"Disk overwrite mode deleted oldest data file: {path} "
                f"(bytes={file_bytes}, overwritten_files={self._overwritten_files})"
            )
            if free_bytes >= target_free:
                return

        raise OSError(
            28,
            f"Insufficient disk space in {self.save_path}; "
            "no closed .bin/.bz file can be deleted",
        )

    def get_diagnostics_snapshot(self) -> dict:
        """Return save-thread diagnostics for periodic logging."""
        return {
            "queue_size": self.queue_size,
            "buffer_size": self.buffer_size,
            "dropped_blocks": self._dropped_blocks,
            "blocks_written": self._blocks_written,
            "bytes_written": self._bytes_written,
            "enqueue_count": self._enqueue_count,
            "max_queue_size_seen": self._max_queue_size_seen,
            "last_write_ms": self._last_write_ms,
            "last_write_bytes": self._last_write_bytes,
            "last_enqueue_ms": self._last_enqueue_ms,
            "max_enqueue_ms": self._max_enqueue_ms,
            "last_enqueued_block_bytes": self._last_enqueued_block_bytes,
            "estimated_queue_bytes": self.queue_size * self._last_enqueued_block_bytes,
            "overwrite_oldest_when_full": self.overwrite_oldest_when_full,
            "overwritten_files": self._overwritten_files,
            "overwritten_bytes": self._overwritten_bytes,
            "is_running": self._running,
        }

    @property
    def is_running(self) -> bool:
        """Check if saver is running"""
        return self._running

    @property
    def bytes_written(self) -> int:
        """Get total bytes written"""
        return self._bytes_written

    @property
    def blocks_written(self) -> int:
        """Get total blocks written"""
        return self._blocks_written

    @property
    def dropped_blocks(self) -> int:
        """Get number of dropped blocks due to queue full"""
        return self._dropped_blocks

    @property
    def queue_size(self) -> int:
        """Get current queue size"""
        return self._data_queue.qsize()

    @property
    def current_filename(self) -> str:
        """Get current filename"""
        return self._current_filename

    @property
    def file_no(self) -> int:
        """Get current file number"""
        return self._file_no

    def __enter__(self):
        """Context manager entry"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.stop()
        return False

    def __del__(self):
        """Destructor"""
        self.stop()


# ----- PACKETIZED BINARY FILE SAVER -----
# Length-based .bin saver: aggregates acquisition blocks into Length/Save packets.
# Filename: {seq}-eDAS-{rate}Hz-{points}pt-{timestamp}.{ms}.bin

class BlockBasedFileSaver(DataSaver):
    """
    Packetized binary saver for raw .bin output.

    The public class name is kept for compatibility, but the runtime behavior is
    now length-based: incoming Length/Load acquisition blocks are aggregated into
    Length/Save write packets, and files rotate after Length/File frames. The
    .bin payload remains a plain continuous int32 stream without packet headers.
    """

    def __init__(self, save_path: str = "D:/eDAS_DATA",
                 blocks_per_file: int = 10,
                 packet_frames: int = 0,
                 file_duration_s: float = 10.0,
                 file_frames_per_file: Optional[int] = None,
                 buffer_size: int = 200,
                 frames_per_file: Optional[int] = None,
                 overwrite_oldest_when_full: bool = False,
                 disk_reserve_bytes: int = DEFAULT_DISK_RESERVE_BYTES):
        """Initialize packetized binary saving."""
        super().__init__(save_path, buffer_size, overwrite_oldest_when_full, disk_reserve_bytes)
        if frames_per_file is not None:
            blocks_per_file = frames_per_file
        self._legacy_blocks_per_file = max(1, int(blocks_per_file))
        self.packet_frames = max(0, int(packet_frames))
        self.file_duration_s = max(0.001, float(file_duration_s))
        self._explicit_file_frames_per_file = (
            max(1, int(file_frames_per_file)) if file_frames_per_file is not None else None
        )
        self._block_count = 0
        self._total_bytes_all_files = 0
        self._total_files_created = 0
        self._scan_rate = 2000
        self._points_per_frame = 0
        self._source_points_per_frame = 0
        self._channel_num = 1
        self._data_source = 0
        self._storage_downsample_factor = 1
        self._packet_points_per_frame = 0
        self._resolved_packet_frames = 0
        self._file_frames_per_file = 0
        self._file_frames_written = 0
        self._packets_written = 0
        self._frames_received = 0
        self._frames_written = 0
        self._pending_chunks: List[np.ndarray] = []
        self._pending_frames = 0
        self._packets_per_file = self._legacy_blocks_per_file

    def start(self, file_no: Optional[int] = None, scan_rate: int = 2000,
              points_per_frame: int = 0, channel_num: int = 1,
              data_source: int = 0, storage_downsample_factor: int = 1,
              source_points_per_frame: Optional[int] = None) -> str:
        """Start saving with length-based packet and file splitting capability."""
        if self._running:
            return self._current_filename
        if points_per_frame <= 0:
            raise ValueError("points_per_frame must be greater than 0 for .bin storage")

        self.save_path.mkdir(parents=True, exist_ok=True)
        if file_no is not None:
            self._file_no = file_no
        else:
            self._file_no += 1

        self._scan_rate = max(1, int(scan_rate))
        self._points_per_frame = max(1, int(points_per_frame))
        self._source_points_per_frame = max(
            self._points_per_frame,
            int(source_points_per_frame or points_per_frame),
        )
        self._channel_num = max(1, int(channel_num or 1))
        self._data_source = int(data_source)
        self._storage_downsample_factor = max(1, int(storage_downsample_factor or 1))
        self._packet_points_per_frame = self._points_per_frame * self._channel_num
        self._resolved_packet_frames = self.packet_frames if self.packet_frames > 0 else self._scan_rate
        self._file_frames_per_file = (
            self._explicit_file_frames_per_file
            if self._explicit_file_frames_per_file is not None
            else max(1, int(round(self.file_duration_s * self._scan_rate)))
        )
        self._packets_per_file = max(1, self._file_frames_per_file // max(1, self._resolved_packet_frames))

        self._block_count = 0
        self._total_bytes_all_files = 0
        self._total_files_created = 1
        self._file_frames_written = 0
        self._packets_written = 0
        self._frames_received = 0
        self._frames_written = 0
        self._pending_chunks = []
        self._pending_frames = 0
        self._bytes_written = 0
        self._blocks_written = 0
        self._dropped_blocks = 0
        self._write_error = ""
        self.completed_file_count = 0
        self._enqueue_count = 0
        self._max_queue_size_seen = 0
        self._last_write_ms = 0.0
        self._last_write_bytes = 0
        self._last_enqueue_ms = 0.0
        self._max_enqueue_ms = 0.0

        self._clear_queue(self._data_queue)
        self._overwritten_files = 0
        self._overwritten_bytes = 0
        self._current_filename = self._generate_filename()
        filepath = self.save_path / self._current_filename
        self._ensure_disk_space(0)
        self._file_handle = open(filepath, 'wb', buffering=1024 * 1024)

        log.info(
            f"Started packetized .bin saving to {filepath} "
            f"(packet_frames={self._resolved_packet_frames}, file_frames={self._file_frames_per_file}, "
            f"source_points={self._source_points_per_frame}, save_points={self._points_per_frame}, "
            f"save_ds={self._storage_downsample_factor}, file_duration_s={self.file_duration_s:.3f}, "
            f"queue_capacity={self.buffer_size})"
        )

        self._running = True
        self._save_thread = threading.Thread(target=self._save_loop, daemon=True)
        self._save_thread.start()
        return self._current_filename

    def _clear_queue(self, target_queue: queue.Queue):
        while not target_queue.empty():
            try:
                target_queue.get_nowait()
            except queue.Empty:
                break

    def save_block(self, block_data: np.ndarray) -> bool:
        """Queue one complete Length/Load acquisition block for Length/Save packetization."""
        return self.save(block_data)

    def save_frame(self, frame_data: np.ndarray) -> bool:
        """Backward-compatible alias for older callers; the payload is a block."""
        return self.save_block(frame_data)

    def _save_loop(self):
        """Aggregate acquisition blocks into Length/Save packets before writing."""
        while True:
            try:
                item = self._data_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            except Exception as e:
                self._write_error = str(e)
                log.error(f"DataSaver error: {e}")
                continue

            try:
                if item is None:
                    break
                frames = self._coerce_block_to_frame_matrix(item)
                if frames.size:
                    self._append_frames_and_emit_packets(frames)
            except Exception as e:
                self._dropped_blocks += 1
                self._write_error = str(e)
                log.exception(f"Packetized .bin save error; dropping block: {e}")
            finally:
                try:
                    self._data_queue.task_done()
                except ValueError:
                    pass

        self._flush_tail_packet()

    def _coerce_block_to_frame_matrix(self, data: np.ndarray) -> np.ndarray:
        arr = np.asarray(data)
        source_points = max(1, int(self._source_points_per_frame or self._points_per_frame))
        save_points = max(1, int(self._points_per_frame))
        factor = max(1, int(self._storage_downsample_factor or 1))

        if self._channel_num <= 1:
            flat = arr.reshape(-1)
            frame_count = flat.size // source_points
            valid_items = frame_count * source_points
            if valid_items < flat.size:
                dropped = flat.size - valid_items
                log.warning(f".bin packetizer dropped {dropped} trailing samples that do not fill a source frame")
            if frame_count <= 0:
                return np.empty((0, self._packet_points_per_frame), dtype=arr.dtype)
            framed = flat[:valid_items].reshape(frame_count, source_points)
            if factor > 1:
                framed = framed[:, ::factor]
            if framed.shape[1] != save_points:
                raise ValueError(
                    f"Unexpected .bin save frame width after downsample: expected={save_points}, "
                    f"actual={framed.shape[1]}, source={source_points}, factor={factor}"
                )
            return np.ascontiguousarray(framed)

        matrix = arr.reshape(-1, self._channel_num)
        frame_count = matrix.shape[0] // source_points
        valid_rows = frame_count * source_points
        if valid_rows < matrix.shape[0]:
            dropped = (matrix.shape[0] - valid_rows) * self._channel_num
            log.warning(f".bin packetizer dropped {dropped} trailing channel samples that do not fill a source frame")
        if frame_count <= 0:
            return np.empty((0, self._packet_points_per_frame), dtype=arr.dtype)
        framed = matrix[:valid_rows, :].reshape(frame_count, source_points, self._channel_num)
        if factor > 1:
            framed = framed[:, ::factor, :]
        if framed.shape[1] != save_points:
            raise ValueError(
                f"Unexpected .bin save frame width after downsample: expected={save_points}, "
                f"actual={framed.shape[1]}, source={source_points}, factor={factor}"
            )
        return np.ascontiguousarray(framed.reshape(frame_count, self._packet_points_per_frame))

    def _append_frames_and_emit_packets(self, frames: np.ndarray):
        self._frames_received += int(frames.shape[0])
        self._pending_chunks.append(frames)
        self._pending_frames += int(frames.shape[0])
        while self._pending_frames >= self._resolved_packet_frames:
            packet_samples = self._take_pending_frames(self._resolved_packet_frames)
            self._write_packet(packet_samples)

    def _take_pending_frames(self, frame_count: int) -> np.ndarray:
        remaining = int(frame_count)
        parts: List[np.ndarray] = []
        while remaining > 0 and self._pending_chunks:
            chunk = self._pending_chunks[0]
            take = min(remaining, int(chunk.shape[0]))
            parts.append(chunk[:take])
            if take == chunk.shape[0]:
                self._pending_chunks.pop(0)
            else:
                self._pending_chunks[0] = chunk[take:]
            self._pending_frames -= take
            remaining -= take

        if not parts:
            return np.empty((0, self._packet_points_per_frame), dtype=np.int32)
        if len(parts) == 1:
            return np.ascontiguousarray(parts[0])
        return np.ascontiguousarray(np.concatenate(parts, axis=0))

    def _flush_tail_packet(self):
        if self._pending_frames <= 0:
            return
        packet_samples = self._take_pending_frames(self._pending_frames)
        log.info(f"Flushing final .bin tail packet with {packet_samples.shape[0]} frames")
        self._write_packet(packet_samples)

    def _generate_filename(self) -> str:
        """Generate filename with the existing .bin naming format."""
        now = datetime.now()
        timestamp_str = now.strftime("%Y%m%dT%H%M%S")
        milliseconds = int((now.timestamp() % 1) * 1000)
        return (f"{self._file_no:07d}-eDAS-{self._scan_rate:04d}Hz-"
                f"{self._points_per_frame:04d}pt-{timestamp_str}.{milliseconds:03d}.bin")

    def _rotate_file(self):
        self._close_output_file()
        self._total_bytes_all_files += self._bytes_written
        self._file_no += 1
        self._current_filename = self._generate_filename()
        filepath = self.save_path / self._current_filename
        self._ensure_disk_space(0)
        self._file_handle = open(filepath, 'wb', buffering=1024 * 1024)
        self._bytes_written = 0
        self._file_frames_written = 0
        self._block_count = 0
        self._total_files_created += 1
        log.info(f"Split to new .bin file: {self._current_filename} (File #{self._total_files_created})")

    def _write_packet(self, samples: np.ndarray):
        if self._file_handle is None:
            self._current_filename = self._generate_filename()
            self._ensure_disk_space(0)
            self._file_handle = open(self.save_path / self._current_filename, 'wb', buffering=1024 * 1024)
        if self._file_frames_written >= self._file_frames_per_file:
            self._rotate_file()

        start = time.perf_counter()
        payload = memoryview(np.ascontiguousarray(samples, dtype=np.int32)).cast("B")
        self._ensure_disk_space(len(payload))
        self._file_handle.write(payload)
        self._last_write_ms = (time.perf_counter() - start) * 1000
        self._last_write_bytes = len(payload)
        self._bytes_written += len(payload)
        self._blocks_written += 1
        self._packets_written += 1
        self._frames_written += int(samples.shape[0])
        self._block_count += 1
        self._file_frames_written += int(samples.shape[0])

        if self._last_write_ms > 50:
            log.warning(
                f"Slow .bin disk write: {self._last_write_ms:.1f}ms, bytes={len(payload)}, "
                f"queue={self._data_queue.qsize()}/{self.buffer_size}"
            )
        if self._packets_written <= 3 or self._packets_written % 20 == 0:
            log.info(
                f"Wrote .bin packet #{self._packets_written}: frames={samples.shape[0]}, "
                f"write_ms={self._last_write_ms:.1f}, queue={self._data_queue.qsize()}/{self.buffer_size}, "
                f"pending_frames={self._pending_frames}, file_frames={self._file_frames_written}/{self._file_frames_per_file}"
            )

    def stop(self):
        """Stop and update total statistics."""
        was_running = self._running
        super().stop()
        if not was_running:
            return
        continuity_gap = self._frames_received - self._frames_written - self._pending_frames
        log.info(
            f"Storage integrity: format=bin, frames_received={self._frames_received}, "
            f"frames_written={self._frames_written}, pending_frames={self._pending_frames}, "
            f"continuity_gap={continuity_gap}, files={self._total_files_created}, "
            f"packets={self._packets_written}, bytes={self.total_bytes_all_files}, "
            f"storage_downsample_space_only={self._storage_downsample_factor}"
            f", dropped_blocks={self._dropped_blocks}"
        )

    def get_diagnostics_snapshot(self) -> dict:
        snapshot = super().get_diagnostics_snapshot()
        snapshot.update({
            "format": "bin",
            "packet_frames": self._resolved_packet_frames,
            "source_points_per_frame": self._source_points_per_frame,
            "storage_downsample_factor": self._storage_downsample_factor,
            "file_frames_per_file": self._file_frames_per_file,
            "file_frames_written": self._file_frames_written,
            "pending_frames": self._pending_frames,
            "packets_written": self._packets_written,
            "frames_received": self._frames_received,
            "frames_written": self._frames_written,
            "continuity_gap": self._frames_received - self._frames_written - self._pending_frames,
            "bytes_written": self.total_bytes_all_files,
        })
        return snapshot

    @property
    def total_bytes_all_files(self) -> int:
        """Get total bytes written across all files."""
        return self._total_bytes_all_files + self._bytes_written

    @property
    def total_files_created(self) -> int:
        """Get total number of files created."""
        return self._total_files_created

    @property
    def block_count(self) -> int:
        """Get current packet count in active file."""
        return self._block_count

    @property
    def blocks_per_file(self) -> int:
        """Backward-compatible packet count per file."""
        return self._packets_per_file

    @blocks_per_file.setter
    def blocks_per_file(self, value: int):
        """Backward-compatible setter for legacy callers."""
        self._packets_per_file = max(1, int(value))

    @property
    def frame_count(self) -> int:
        """Backward-compatible alias for block_count."""
        return self.block_count

    @property
    def frames_per_file(self) -> int:
        """Backward-compatible alias for blocks_per_file."""
        return self.blocks_per_file

    @frames_per_file.setter
    def frames_per_file(self, value: int):
        """Backward-compatible alias for blocks_per_file."""
        self.blocks_per_file = value


# ----- BITSHUFFLE + ZSTD FILE SAVER -----
# Packetizes int32 acquisition data and writes self-describing .bz files.

class BitshuffleZstdFileSaver(DataSaver):
    """
    Real-time packetized saver using Bitshuffle + Zstd.

    The producer path remains non-blocking. Acquisition blocks are queued into a
    bounded raw queue, a packetizer aggregates frames into packet-sized matrices,
    compression workers process packets in parallel, and a writer thread appends packet headers plus compressed payloads
    to .bz files.
    """

    def __init__(self, save_path: str = "D:/eDAS_DATA",
                 file_duration_s: float = 10.0,
                 packet_frames: int = 0,
                 zstd_level: int = 3,
                 bitshuffle_block_values: int = 65536,
                 file_frames_per_file: Optional[int] = None,
                 buffer_size: int = 200,
                 compressed_queue_size: int = 32,
                 compression_workers: int = 4,
                 packet_queue_size: Optional[int] = None,
                 raw_queue_put_timeout_s: float = 0.05,
                 overwrite_oldest_when_full: bool = False,
                 disk_reserve_bytes: int = DEFAULT_DISK_RESERVE_BYTES):
        super().__init__(save_path, buffer_size, overwrite_oldest_when_full, disk_reserve_bytes)
        self.file_duration_s = max(0.001, float(file_duration_s))
        self.packet_frames = max(0, int(packet_frames))
        self._explicit_file_frames_per_file = (
            max(1, int(file_frames_per_file)) if file_frames_per_file is not None else None
        )
        self.zstd_level = int(zstd_level)
        self.bitshuffle_block_values = max(1, int(bitshuffle_block_values))
        self.compression_workers = max(1, int(compression_workers or 1))
        self.packet_queue_size = (
            max(1, int(packet_queue_size))
            if packet_queue_size is not None
            else max(4, self.compression_workers * 2)
        )
        self.raw_queue_put_timeout_s = max(0.0, float(raw_queue_put_timeout_s))
        self.compressed_queue_size = max(1, int(compressed_queue_size))
        self._packet_queue: queue.Queue = queue.Queue(maxsize=self.packet_queue_size)
        self._compressed_queue: queue.Queue = queue.Queue(maxsize=self.compressed_queue_size)
        self._packetizer_thread: Optional[threading.Thread] = None
        self._compress_threads: List[threading.Thread] = []
        self._writer_thread: Optional[threading.Thread] = None
        self._pending_chunks: List[np.ndarray] = []
        self._pending_frames = 0
        self._packet_index = 0
        self._points_per_frame = 0
        self._source_points_per_frame = 0
        self._packet_points_per_frame = 0
        self._channel_num = 1
        self._data_source = 0
        self._storage_downsample_factor = 1
        self._resolved_packet_frames = 0
        self._file_frames_per_file = 0
        self._file_frames_written = 0
        self._total_bytes_all_files = 0
        self._total_files_created = 0
        self._packets_compressed = 0
        self._packets_written = 0
        self._frames_received = 0
        self._frames_written = 0
        self._last_compress_ms = 0.0
        self._max_compress_ms = 0.0
        self._last_compression_ratio = 0.0
        self._compression_not_realtime_count = 0
        self._slow_compression_packet_count = 0
        self._dropped_samples = 0
        self._stats_lock = threading.Lock()
        self._packet_queue_full_count = 0
        self._compressed_queue_full_count = 0
        self._max_packet_queue_size_seen = 0
        self._max_compressed_queue_size_seen = 0
        self._last_raw_high_watermark_log_ts = 0.0
        self._last_packet_queue_full_log_ts = 0.0
        self._last_compressed_queue_full_log_ts = 0.0
        self._last_slow_compression_log_ts = 0.0

    def start(self, file_no: Optional[int] = None, scan_rate: int = 2000,
              points_per_frame: int = 0, channel_num: int = 1,
              data_source: int = 0, storage_downsample_factor: int = 1,
              source_points_per_frame: Optional[int] = None) -> str:
        """Start .bz saving with packetized compression."""
        # Validate dependencies before opening files or starting any threads.
        BitshuffleZstdCompressor(zstd_level=self.zstd_level, block_values=self.bitshuffle_block_values)
        if self._running:
            return self._current_filename
        if points_per_frame <= 0:
            raise ValueError("points_per_frame must be greater than 0 for .bz storage")

        self.save_path.mkdir(parents=True, exist_ok=True)
        if file_no is not None:
            self._file_no = file_no
        else:
            self._file_no += 1

        self._scan_rate = max(1, int(scan_rate))
        self._points_per_frame = max(1, int(points_per_frame))
        self._source_points_per_frame = max(
            self._points_per_frame,
            int(source_points_per_frame or points_per_frame),
        )
        self._channel_num = max(1, int(channel_num or 1))
        self._data_source = int(data_source)
        self._storage_downsample_factor = max(1, int(storage_downsample_factor or 1))
        self._packet_points_per_frame = self._points_per_frame * self._channel_num
        self._resolved_packet_frames = self.packet_frames if self.packet_frames > 0 else self._scan_rate
        self._file_frames_per_file = (
            self._explicit_file_frames_per_file
            if self._explicit_file_frames_per_file is not None
            else max(1, int(round(self.file_duration_s * self._scan_rate)))
        )

        self._bytes_written = 0
        self._blocks_written = 0
        self._dropped_blocks = 0
        self._write_error = ""
        self.completed_file_count = 0
        self._enqueue_count = 0
        self._max_queue_size_seen = 0
        self._last_write_ms = 0.0
        self._last_write_bytes = 0
        self._pending_chunks = []
        self._pending_frames = 0
        self._packet_index = 0
        self._file_frames_written = 0
        self._total_bytes_all_files = 0
        self._total_files_created = 0
        self._packets_compressed = 0
        self._packets_written = 0
        self._last_compress_ms = 0.0
        self._frames_received = 0
        self._frames_written = 0
        self._max_compress_ms = 0.0
        self._last_compression_ratio = 0.0
        self._compression_not_realtime_count = 0
        self._slow_compression_packet_count = 0
        self._dropped_samples = 0
        self._packet_queue_full_count = 0
        self._compressed_queue_full_count = 0
        self._max_packet_queue_size_seen = 0
        self._max_compressed_queue_size_seen = 0
        self._last_raw_high_watermark_log_ts = 0.0
        self._last_packet_queue_full_log_ts = 0.0
        self._last_compressed_queue_full_log_ts = 0.0
        self._last_slow_compression_log_ts = 0.0
        self._last_enqueue_ms = 0.0
        self._max_enqueue_ms = 0.0

        self._clear_queue(self._data_queue)
        self._clear_queue(self._packet_queue)
        self._overwritten_files = 0
        self._overwritten_bytes = 0
        self._clear_queue(self._compressed_queue)
        self._open_new_file()

        self._running = True
        self._packetizer_thread = threading.Thread(target=self._packetizer_loop, name="bz-packetizer", daemon=True)
        self._compress_threads = [
            threading.Thread(target=self._compression_worker_loop, args=(worker_id,), name=f"bz-compress-{worker_id}", daemon=True)
            for worker_id in range(self.compression_workers)
        ]
        self._writer_thread = threading.Thread(target=self._writer_loop, name="bz-writer", daemon=True)
        self._writer_thread.start()
        for thread in self._compress_threads:
            thread.start()
        self._packetizer_thread.start()

        log.info(
            f"Started Bitshuffle+Zstd saving to {self.save_path / self._current_filename} "
            f"(zstd_level={self.zstd_level}, bitshuffle_block={self.bitshuffle_block_values}, "
            f"packet_frames={self._resolved_packet_frames}, file_duration_s={self.file_duration_s:.3f}, "
            f"source_points={self._source_points_per_frame}, save_points={self._points_per_frame}, "
            f"save_ds={self._storage_downsample_factor}, raw_queue=0/{self.buffer_size}, packet_queue=0/{self.packet_queue_size}, "
            f"compressed_queue=0/{self.compressed_queue_size}, workers={self.compression_workers}, cache=False)"
        )
        return self._current_filename

    def _clear_queue(self, target_queue: queue.Queue):
        while not target_queue.empty():
            try:
                target_queue.get_nowait()
            except queue.Empty:
                break

    def _generate_filename(self) -> str:
        now = datetime.now()
        timestamp_str = now.strftime("%Y%m%dT%H%M%S")
        milliseconds = int((now.timestamp() % 1) * 1000)
        return (f"{self._file_no:07d}-eDAS-{self._scan_rate:04d}Hz-"
                f"{self._points_per_frame:04d}pt-{timestamp_str}.{milliseconds:03d}.bz")

    def _open_new_file(self):
        self._current_filename = self._generate_filename()
        filepath = self.save_path / self._current_filename
        self._ensure_disk_space(0)
        self._file_handle = open(filepath, "wb", buffering=1024 * 1024)
        header = pack_bz_file_header(
            scan_rate_hz=self._scan_rate,
            points_per_frame=self._points_per_frame,
            channel_num=self._channel_num,
            data_source=self._data_source,
            storage_downsample_factor=self._storage_downsample_factor,
            packet_frames=self._resolved_packet_frames,
            file_duration_s=max(1, int(round(self.file_duration_s))),
            zstd_level=self.zstd_level,
            bitshuffle_block_values=self.bitshuffle_block_values,
        )
        self._file_handle.write(header)
        self._bytes_written = len(header)
        self._file_frames_written = 0
        self._total_files_created += 1
        log.info(f"Opened .bz file: {filepath}")

    def _rotate_file(self):
        self._close_output_file()
        self._total_bytes_all_files += self._bytes_written
        self._file_no += 1
        self._open_new_file()

    def save_block(self, block_data: np.ndarray) -> bool:
        """Queue one complete acquisition block for .bz packetization."""
        return self.save(block_data)

    def save_frame(self, frame_data: np.ndarray) -> bool:
        """Backward-compatible alias for older callers."""
        return self.save_block(frame_data)

    def save(self, data: np.ndarray) -> bool:
        block_bytes = int(data.nbytes) if isinstance(data, np.ndarray) else len(data)
        block_duration_s = self._estimate_block_duration_s(data)
        queued, enqueue_ms, reason = self._enqueue_owned(data, self.raw_queue_put_timeout_s)
        if reason == "stopped":
            return False

        self._last_enqueue_ms = enqueue_ms
        self._max_enqueue_ms = max(self._max_enqueue_ms, enqueue_ms)
        if not queued:
            dropped = self._increment_dropped_blocks()
            not_realtime = self._increment_not_realtime_count()
            backlog_s = self.buffer_size * block_duration_s if block_duration_s > 0 else 0.0
            log.warning(
                f"BZ raw queue full; compression is not realtime, dropping acquisition block: "
                f"bytes={block_bytes}, dropped={dropped}, not_realtime={not_realtime}, "
                f"raw_queue={self._data_queue.qsize()}/{self.buffer_size}, "
                f"packet_queue={self._packet_queue.qsize()}/{self.packet_queue_size}, "
                f"compressed_queue={self._compressed_queue.qsize()}/{self.compressed_queue_size}, "
                f"workers={self.compression_workers}, est_raw_backlog_s={backlog_s:.1f}"
            )
            return False

        self._enqueue_count += 1
        self._last_enqueued_block_bytes = block_bytes
        queue_size = self._data_queue.qsize()
        self._max_queue_size_seen = max(self._max_queue_size_seen, queue_size)
        self._log_raw_queue_high_watermark(queue_size, block_bytes, block_duration_s)
        if (
            self._enqueue_count <= 3
            or self._enqueue_count % 20 == 0
            or queue_size >= max(1, self.buffer_size // 2)
        ):
            log.debug(
                f"Queued .bz raw block #{self._enqueue_count}: bytes={block_bytes}, "
                f"raw_queue={queue_size}/{self.buffer_size}, "
                f"packet_queue={self._packet_queue.qsize()}/{self.packet_queue_size}, "
                f"compressed_queue={self._compressed_queue.qsize()}/{self.compressed_queue_size}, "
                f"pending_frames={self._pending_frames}, cache={self._has_cache()}"
            )
        return True

    def _estimate_block_duration_s(self, data: np.ndarray) -> float:
        try:
            arr = np.asarray(data)
            source_points = max(1, int(self._source_points_per_frame or self._points_per_frame))
            source_packet_points = source_points * max(1, int(self._channel_num or 1))
            frames = int(arr.size) // source_packet_points
            if frames > 0:
                return frames / max(1, self._scan_rate)
        except Exception:
            pass
        return 0.0

    def _log_raw_queue_high_watermark(self, queue_size: int, block_bytes: int, block_duration_s: float):
        high_watermark = max(1, int(self.buffer_size * 0.80))
        if queue_size < high_watermark:
            return
        now = time.time()
        if now - self._last_raw_high_watermark_log_ts < 5.0:
            return
        self._last_raw_high_watermark_log_ts = now
        backlog_s = queue_size * block_duration_s if block_duration_s > 0 else 0.0
        log.warning(
            f"BZ raw queue high watermark: bytes={block_bytes}, raw_queue={queue_size}/{self.buffer_size}, "
            f"packet_queue={self._packet_queue.qsize()}/{self.packet_queue_size}, "
            f"compressed_queue={self._compressed_queue.qsize()}/{self.compressed_queue_size}, "
            f"workers={self.compression_workers}, est_raw_backlog_s={backlog_s:.1f}"
        )

    def _increment_dropped_blocks(self, amount: int = 1) -> int:
        with self._stats_lock:
            self._dropped_blocks += int(amount)
            return self._dropped_blocks

    def _increment_not_realtime_count(self, amount: int = 1) -> int:
        with self._stats_lock:
            self._compression_not_realtime_count += int(amount)
            return self._compression_not_realtime_count

    def _record_compression_metrics(self, compress_ms: float, ratio: float, slow_packet: bool) -> tuple[int, int]:
        with self._stats_lock:
            self._packets_compressed += 1
            self._last_compress_ms = compress_ms
            self._max_compress_ms = max(self._max_compress_ms, compress_ms)
            self._last_compression_ratio = ratio
            if slow_packet:
                self._slow_compression_packet_count += 1
            return self._slow_compression_packet_count, self._compression_not_realtime_count

    def stop(self):
        """Stop .bz saving after draining queued raw blocks and the final tail packet."""
        with self._lifecycle_lock:
            if not self._running:
                return
            self._running = False
            try:
                self._data_queue.put(None, timeout=1.0)
            except queue.Full:
                log.warning("BZ raw queue full while stopping; waiting to enqueue sentinel")
                self._data_queue.put(None)

        if self._packetizer_thread is not None:
            self._join_thread_until_stopped(self._packetizer_thread, "BZ packetizer thread")
            self._packetizer_thread = None

        compressors_stopped = True
        for thread in self._compress_threads:
            self._join_thread_until_stopped(thread, thread.name)
        self._compress_threads = []

        if compressors_stopped:
            self._enqueue_writer_sentinel()
        else:
            log.error("BZ writer sentinel skipped because compression workers are still alive")

        if self._writer_thread is not None:
            self._join_thread_until_stopped(self._writer_thread, "BZ writer thread")
            self._writer_thread = None

        self._close_output_file()

        log.info(
            f"Stopped .bz saving. Files={self._total_files_created}, packets={self._packets_written}, "
            f"bytes={self.total_bytes_all_files}, dropped={self._dropped_blocks}, "
            f"raw_max_queue={self._max_queue_size_seen}/{self.buffer_size}, "
            f"packet_max_queue={self._max_packet_queue_size_seen}/{self.packet_queue_size}, "
            f"compressed_max_queue={self._max_compressed_queue_size_seen}/{self.compressed_queue_size}, "
            f"compression_not_realtime={self._compression_not_realtime_count}, "
            f"slow_compression_packets={self._slow_compression_packet_count}, "
            f"packet_queue_full={self._packet_queue_full_count}, compressed_queue_full={self._compressed_queue_full_count}, "
            f"workers={self.compression_workers}, max_compress_ms={self._max_compress_ms:.1f}"
        )
        continuity_gap = self._frames_received - self._frames_written - self._pending_frames
        log.info(
            f"Storage integrity: format=bz, frames_received={self._frames_received}, "
            f"frames_written={self._frames_written}, pending_frames={self._pending_frames}, "
            f"continuity_gap={continuity_gap}, packet_index_next={self._packet_index}, "
            f"packets_written={self._packets_written}, dropped_blocks={self._dropped_blocks}, "
            f"dropped_samples={self._dropped_samples}, "
            f"storage_downsample_space_only={self._storage_downsample_factor}"
        )

    def _packetizer_loop(self):
        try:
            while True:
                try:
                    item = self._data_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                except Exception as exc:
                    log.error(f"BZ raw queue error: {exc}")
                    continue

                try:
                    if item is None:
                        break
                    frames = self._coerce_block_to_frame_matrix(item)
                    if frames.size:
                        self._append_frames_and_emit_packets(frames)
                except Exception as exc:
                    dropped = self._increment_dropped_blocks()
                    not_realtime = self._increment_not_realtime_count()
                    log.exception(
                        f"BZ packetization failed; dropping block: {exc}; "
                        f"dropped={dropped}, not_realtime={not_realtime}"
                    )
                finally:
                    try:
                        self._data_queue.task_done()
                    except ValueError:
                        pass

            self._flush_tail_packet()
        finally:
            self._enqueue_compressor_sentinels()

    def _writer_loop(self):
        pending_packets: Dict[int, object] = {}
        next_packet_index = 0
        while True:
            try:
                item = self._compressed_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            except Exception as exc:
                log.error(f"BZ writer queue error: {exc}")
                continue

            try:
                if item is None:
                    next_packet_index = self._drain_ordered_writer_buffer(
                        pending_packets,
                        next_packet_index,
                        final=True,
                    )
                    if pending_packets:
                        log.error(f"BZ writer stopped with {len(pending_packets)} unordered packets still pending")
                    break

                packet_index = int(getattr(item, "packet_index"))
                if packet_index in pending_packets:
                    dropped = self._increment_dropped_blocks()
                    log.warning(f"Duplicate .bz packet index dropped: packet={packet_index}, dropped={dropped}")
                else:
                    pending_packets[packet_index] = item
                next_packet_index = self._drain_ordered_writer_buffer(pending_packets, next_packet_index)
            except Exception as exc:
                dropped = self._increment_dropped_blocks()
                log.exception(f"BZ writer failed while ordering packet; dropped={dropped}: {exc}")
            finally:
                try:
                    self._compressed_queue.task_done()
                except ValueError:
                    pass

    def _drain_ordered_writer_buffer(
        self,
        pending_packets: Dict[int, object],
        next_packet_index: int,
        final: bool = False,
    ) -> int:
        while True:
            if next_packet_index not in pending_packets:
                if final and pending_packets:
                    next_available = min(pending_packets)
                    if next_available > next_packet_index:
                        log.warning(
                            f"BZ writer skipping missing packet range: expected={next_packet_index}, "
                            f"next_available={next_available}"
                        )
                        next_packet_index = next_available
                        continue
                break

            item = pending_packets.pop(next_packet_index)
            if isinstance(item, _DroppedPacketMarker):
                next_packet_index += 1
                continue

            try:
                self._write_compressed_packet(item)
            except Exception as exc:
                dropped = self._increment_dropped_blocks()
                log.exception(f"BZ writer failed; packet lost: {exc}; dropped={dropped}")
            next_packet_index += 1
        return next_packet_index

    def _coerce_block_to_frame_matrix(self, data: np.ndarray) -> np.ndarray:
        arr = np.asarray(data, dtype=np.int32)
        source_points = max(1, int(self._source_points_per_frame or self._points_per_frame))
        save_points = max(1, int(self._points_per_frame))
        factor = max(1, int(self._storage_downsample_factor or 1))

        if self._channel_num <= 1:
            flat = arr.reshape(-1)
            frame_count = flat.size // source_points
            valid_items = frame_count * source_points
            if valid_items < flat.size:
                dropped = flat.size - valid_items
                self._dropped_samples += dropped
                log.warning(f"BZ packetizer dropped {dropped} trailing samples that do not fill a source frame")
            if frame_count <= 0:
                return np.empty((0, self._packet_points_per_frame), dtype=np.int32)
            framed = flat[:valid_items].reshape(frame_count, source_points)
            if factor > 1:
                framed = framed[:, ::factor]
            if framed.shape[1] != save_points:
                raise ValueError(
                    f"Unexpected BZ save frame width after downsample: expected={save_points}, "
                    f"actual={framed.shape[1]}, source={source_points}, factor={factor}"
                )
            return np.ascontiguousarray(framed)

        matrix = arr.reshape(-1, self._channel_num)
        frame_count = matrix.shape[0] // source_points
        valid_rows = frame_count * source_points
        if valid_rows < matrix.shape[0]:
            dropped = (matrix.shape[0] - valid_rows) * self._channel_num
            self._dropped_samples += dropped
            log.warning(f"BZ packetizer dropped {dropped} trailing channel samples that do not fill a source frame")
        if frame_count <= 0:
            return np.empty((0, self._packet_points_per_frame), dtype=np.int32)
        framed = matrix[:valid_rows, :].reshape(frame_count, source_points, self._channel_num)
        if factor > 1:
            framed = framed[:, ::factor, :]
        if framed.shape[1] != save_points:
            raise ValueError(
                f"Unexpected BZ save frame width after downsample: expected={save_points}, "
                f"actual={framed.shape[1]}, source={source_points}, factor={factor}"
            )
        return np.ascontiguousarray(framed.reshape(frame_count, self._packet_points_per_frame))

    def _append_frames_and_emit_packets(self, frames: np.ndarray):
        self._frames_received += int(frames.shape[0])
        self._pending_chunks.append(frames)
        self._pending_frames += int(frames.shape[0])
        while self._pending_frames >= self._resolved_packet_frames:
            packet_samples = self._take_pending_frames(self._resolved_packet_frames)
            self._enqueue_raw_packet(packet_samples)

    def _take_pending_frames(self, frame_count: int) -> np.ndarray:
        remaining = int(frame_count)
        parts: List[np.ndarray] = []
        while remaining > 0 and self._pending_chunks:
            chunk = self._pending_chunks[0]
            take = min(remaining, int(chunk.shape[0]))
            parts.append(chunk[:take])
            if take == chunk.shape[0]:
                self._pending_chunks.pop(0)
            else:
                self._pending_chunks[0] = chunk[take:]
            self._pending_frames -= take
            remaining -= take

        if not parts:
            return np.empty((0, self._packet_points_per_frame), dtype=np.int32)
        if len(parts) == 1:
            return np.ascontiguousarray(parts[0])
        return np.ascontiguousarray(np.concatenate(parts, axis=0))

    def _flush_tail_packet(self):
        if self._pending_frames <= 0:
            return
        packet_samples = self._take_pending_frames(self._pending_frames)
        log.info(f"Flushing final .bz tail packet with {packet_samples.shape[0]} frames")
        self._enqueue_raw_packet(packet_samples)

    def _enqueue_raw_packet(self, samples: np.ndarray):
        if samples.size == 0:
            return
        packet = RawPacket(
            packet_index=self._packet_index,
            timestamp_ns=time.time_ns(),
            scan_rate_hz=self._scan_rate,
            points_per_frame=self._packet_points_per_frame,
            frames=int(samples.shape[0]),
            samples=samples,
        )
        self._packet_index += 1

        if self._packet_queue.full():
            now = time.time()
            with self._stats_lock:
                self._packet_queue_full_count += 1
                full_count = self._packet_queue_full_count
            not_realtime = self._increment_not_realtime_count()
            if now - self._last_packet_queue_full_log_ts >= 5.0:
                self._last_packet_queue_full_log_ts = now
                log.warning(
                    f"BZ packet queue full; compression workers are behind: packet={packet.packet_index}, "
                    f"packet_queue={self._packet_queue.qsize()}/{self.packet_queue_size}, "
                    f"raw_queue={self._data_queue.qsize()}/{self.buffer_size}, workers={self.compression_workers}, "
                    f"packet_queue_full={full_count}, not_realtime={not_realtime}"
                )

        self._packet_queue.put(packet)
        queue_size = self._packet_queue.qsize()
        self._max_packet_queue_size_seen = max(self._max_packet_queue_size_seen, queue_size)

    def _compression_worker_loop(self, worker_id: int):
        compressor = BitshuffleZstdCompressor(
            zstd_level=self.zstd_level,
            block_values=self.bitshuffle_block_values,
        )
        while True:
            try:
                packet = self._packet_queue.get(timeout=0.1)
            except queue.Empty:
                continue
            except Exception as exc:
                log.error(f"BZ compression packet queue error: {exc}")
                continue

            try:
                if packet is None:
                    break
                self._compress_and_enqueue_packet(packet, compressor, worker_id)
            except Exception as exc:
                dropped = self._increment_dropped_blocks()
                not_realtime = self._increment_not_realtime_count()
                log.exception(
                    f"BZ compression failed; dropping packet={getattr(packet, 'packet_index', 'unknown')}: {exc}; "
                    f"dropped={dropped}, not_realtime={not_realtime}, worker={worker_id}"
                )
                if packet is not None and hasattr(packet, "packet_index"):
                    self._enqueue_compression_result(_DroppedPacketMarker(packet.packet_index), worker_id)
            finally:
                try:
                    self._packet_queue.task_done()
                except ValueError:
                    pass

    def _compress_and_enqueue_packet(self, packet: RawPacket, compressor: BitshuffleZstdCompressor, worker_id: int):
        compressed = compressor.compress_packet(packet)
        metrics = compressed.metrics
        metrics["worker_id"] = float(worker_id)
        compress_ms = float(metrics.get("compress_ms", 0.0))
        packet_duration_ms = packet.frames / max(1, self._scan_rate) * 1000.0
        ratio = float(metrics.get("compression_ratio", 0.0))
        slow_packet = compress_ms > packet_duration_ms
        slow_count, not_realtime_count = self._record_compression_metrics(compress_ms, ratio, slow_packet)

        if slow_packet:
            now = time.time()
            queue_pressure = (
                self._data_queue.qsize() > 0
                or self._packet_queue.qsize() > 0
                or self._compressed_queue.qsize() > 0
            )
            log_interval_s = 5.0 if queue_pressure else 30.0
            if now - self._last_slow_compression_log_ts >= log_interval_s:
                self._last_slow_compression_log_ts = now
                log.warning(
                    f"BZ compression slow packet: packet={packet.packet_index}, worker={worker_id}, "
                    f"compress_ms={compress_ms:.1f}, packet_duration_ms={packet_duration_ms:.1f}, "
                    f"raw_queue={self._data_queue.qsize()}/{self.buffer_size}, "
                    f"packet_queue={self._packet_queue.qsize()}/{self.packet_queue_size}, "
                    f"compressed_queue={self._compressed_queue.qsize()}/{self.compressed_queue_size}, "
                    f"slow_packets={slow_count}, not_realtime={not_realtime_count}"
                )

        self._enqueue_compression_result(compressed, worker_id)

    def _enqueue_compression_result(self, item: object, worker_id: int):
        packet_index = int(getattr(item, "packet_index", -1))
        warned_full = False
        while True:
            try:
                self._compressed_queue.put(item, timeout=0.5)
                queue_size = self._compressed_queue.qsize()
                self._max_compressed_queue_size_seen = max(self._max_compressed_queue_size_seen, queue_size)
                return
            except queue.Full:
                now = time.time()
                if not warned_full:
                    with self._stats_lock:
                        self._compressed_queue_full_count += 1
                        full_count = self._compressed_queue_full_count
                    not_realtime = self._increment_not_realtime_count()
                    warned_full = True
                else:
                    full_count = self._compressed_queue_full_count
                    not_realtime = self._compression_not_realtime_count
                if now - self._last_compressed_queue_full_log_ts >= 5.0:
                    self._last_compressed_queue_full_log_ts = now
                    log.warning(
                        f"BZ compressed queue full; writer is behind: packet={packet_index}, worker={worker_id}, "
                        f"compressed_queue={self._compressed_queue.qsize()}/{self.compressed_queue_size}, "
                        f"raw_queue={self._data_queue.qsize()}/{self.buffer_size}, "
                        f"packet_queue={self._packet_queue.qsize()}/{self.packet_queue_size}, "
                        f"compressed_queue_full={full_count}, not_realtime={not_realtime}"
                    )
                if self._writer_thread is not None and not self._writer_thread.is_alive():
                    dropped = self._increment_dropped_blocks()
                    log.error(
                        f"BZ writer thread is not alive; dropping compressed packet={packet_index}, "
                        f"worker={worker_id}, dropped={dropped}"
                    )
                    return

    def _enqueue_compressor_sentinels(self):
        for _ in range(self.compression_workers):
            while True:
                try:
                    self._packet_queue.put(None, timeout=1.0)
                    break
                except queue.Full:
                    log.warning("BZ packet queue full while stopping; waiting to enqueue compressor sentinel")

    def _enqueue_writer_sentinel(self) -> bool:
        while True:
            if self._writer_thread is not None and not self._writer_thread.is_alive():
                log.error("BZ writer thread is not alive; cannot enqueue writer sentinel")
                return False
            try:
                self._compressed_queue.put(None, timeout=1.0)
                return True
            except queue.Full:
                log.warning("BZ compressed queue full while stopping; waiting to enqueue writer sentinel")

    def _write_compressed_packet(self, packet: CompressedPacket):
        if self._file_handle is None:
            self._open_new_file()
        if self._file_frames_written >= self._file_frames_per_file:
            self._rotate_file()

        start = time.perf_counter()
        self._ensure_disk_space(len(packet.header) + len(packet.payload))
        self._file_handle.write(packet.header)
        self._file_handle.write(packet.payload)
        write_bytes = len(packet.header) + len(packet.payload)
        self._last_write_ms = (time.perf_counter() - start) * 1000.0
        self._last_write_bytes = write_bytes
        self._bytes_written += write_bytes
        self._blocks_written += 1
        self._packets_written += 1
        self._file_frames_written += int(packet.metrics.get("frames", 0.0))
        self._frames_written += int(packet.metrics.get("frames", 0.0))

        ratio = float(packet.metrics.get("compression_ratio", self._last_compression_ratio))
        compress_ms = float(packet.metrics.get("compress_ms", self._last_compress_ms))
        worker_id = int(packet.metrics.get("worker_id", -1))

        if self._last_write_ms > 50:
            log.warning(
                f"Slow .bz disk write: {self._last_write_ms:.1f}ms, bytes={write_bytes}, "
                f"compressed_queue={self._compressed_queue.qsize()}/{self.compressed_queue_size}"
            )
        if self._packets_written <= 3 or self._packets_written % 20 == 0:
            log.info(
                f"Wrote .bz packet #{self._packets_written}: packet={packet.packet_index}, "
                f"worker={worker_id}, ratio={ratio:.2f}, "
                f"compress_ms={compress_ms:.1f}, write_ms={self._last_write_ms:.1f}, "
                f"raw_queue={self._data_queue.qsize()}/{self.buffer_size}, "
                f"packet_queue={self._packet_queue.qsize()}/{self.packet_queue_size}, "
                f"compressed_queue={self._compressed_queue.qsize()}/{self.compressed_queue_size}, "
                f"pending_frames={self._pending_frames}, cache={self._has_cache()}, "
                f"slow_packets={self._slow_compression_packet_count}, "
                f"not_realtime={self._compression_not_realtime_count}, dropped={self._dropped_blocks}"
            )

    def _has_cache(self) -> bool:
        return (
            self._data_queue.qsize() > 0
            or self._packet_queue.qsize() > 0
            or self._compressed_queue.qsize() > 0
            or self._pending_frames > 0
        )

    def get_diagnostics_snapshot(self) -> dict:
        snapshot = super().get_diagnostics_snapshot()
        with self._stats_lock:
            dropped_blocks = self._dropped_blocks
            packets_compressed = self._packets_compressed
            compression_not_realtime_count = self._compression_not_realtime_count
            slow_compression_packet_count = self._slow_compression_packet_count
            last_compress_ms = self._last_compress_ms
            max_compress_ms = self._max_compress_ms
            last_compression_ratio = self._last_compression_ratio
            dropped_samples = self._dropped_samples
            packet_queue_full_count = self._packet_queue_full_count
            compressed_queue_full_count = self._compressed_queue_full_count
        snapshot.update({
            "format": "bz",
            "queue_size": self.queue_size,
            "raw_queue_size": self._data_queue.qsize(),
            "packet_queue_size": self._packet_queue.qsize(),
            "packet_queue_size_max": self.packet_queue_size,
            "compressed_queue_size": self._compressed_queue.qsize(),
            "compressed_queue_size_max": self.compressed_queue_size,
            "frames_received": self._frames_received,
            "frames_written": self._frames_written,
            "continuity_gap": self._frames_received - self._frames_written - self._pending_frames,
            "raw_queue_estimated_bytes": self._data_queue.qsize() * self._last_enqueued_block_bytes,
            "packet_queue_estimated_bytes": (
                self._packet_queue.qsize() * self._resolved_packet_frames * self._packet_points_per_frame * 4
            ),
            "compressed_queue_estimated_bytes": (
                self._compressed_queue.qsize() * self._resolved_packet_frames * self._packet_points_per_frame * 4
            ),
            "pending_frames": self._pending_frames,
            "packet_frames": self._resolved_packet_frames,
            "source_points_per_frame": self._source_points_per_frame,
            "storage_downsample_factor": self._storage_downsample_factor,
            "file_duration_s": self.file_duration_s,
            "compression_workers": self.compression_workers,
            "compression_threads_alive": sum(1 for thread in self._compress_threads if thread.is_alive()),
            "packets_compressed": packets_compressed,
            "packets_written": self._packets_written,
            "compression_not_realtime_count": compression_not_realtime_count,
            "slow_compression_packet_count": slow_compression_packet_count,
            "last_compress_ms": last_compress_ms,
            "max_compress_ms": max_compress_ms,
            "last_compression_ratio": last_compression_ratio,
            "dropped_samples": dropped_samples,
            "dropped_blocks": dropped_blocks,
            "packet_queue_full_count": packet_queue_full_count,
            "compressed_queue_full_count": compressed_queue_full_count,
            "has_cache": self._has_cache(),
            "bytes_written": self.total_bytes_all_files,
        })
        return snapshot

    @property
    def queue_size(self) -> int:
        return self._data_queue.qsize()

    @property
    def total_bytes_all_files(self) -> int:
        return self._total_bytes_all_files + self._bytes_written

    @property
    def total_files_created(self) -> int:
        return self._total_files_created

    @property
    def block_count(self) -> int:
        return self._packets_written

    @property
    def blocks_per_file(self) -> int:
        return self._file_frames_per_file

FrameBasedFileSaver = BlockBasedFileSaver


# ----- TIME-BASED FILE SAVER (LEGACY) -----
# Splits files by wall-clock duration. Kept for backward compatibility.

class TimedFileSaver(DataSaver):
    """
    Legacy data saver that creates new files every N seconds.
    Kept for backward compatibility.

    Filename format: {seq}-{HH}-{MM}-{SS}-{scan_rate}.bin
    Example: 1-12-30-45-2000.bin, 2-12-30-46-2000.bin, ...
    """

    def __init__(self, save_path: str = "save_data",
                 file_duration_s: float = 1.0,
                 buffer_size: int = 100):
        """
        Initialize timed file saver.

        Args:
            save_path: Directory to save files
            file_duration_s: Duration per file in seconds (default 1.0)
            buffer_size: Maximum number of data blocks in queue
        """
        super().__init__(save_path, buffer_size)
        self.file_duration = file_duration_s
        self._file_start_time: float = 0
        self._total_bytes_all_files = 0
        self._total_files_created = 0

    def start(self, file_no: Optional[int] = None, scan_rate: int = 2000) -> str:
        """Start saving with auto-split capability"""
        self._file_start_time = time.time()
        self._total_bytes_all_files = 0
        self._total_files_created = 1
        return super().start(file_no, scan_rate)

    def save(self, data: np.ndarray) -> bool:
        """Save data with auto-split check based on time"""
        if not self._running:
            return False

        # Check if need to create new file (time-based)
        elapsed = time.time() - self._file_start_time
        if elapsed >= self.file_duration:
            self._split_file()

        return super().save(data)

    def _split_file(self):
        """Close current file and open new one"""
        # Update total bytes
        self._total_bytes_all_files += self._bytes_written

        # Close current file
        self._close_output_file()

        # Increment file number and create new file
        self._file_no += 1
        now = datetime.now()
        self._current_filename = f"{self._file_no}-{now.hour:02d}-{now.minute:02d}-{now.second:02d}-{self._scan_rate}.bin"

        filepath = self.save_path / self._current_filename
        self._file_handle = open(filepath, 'wb')
        self._bytes_written = 0
        self._file_start_time = time.time()
        self._total_files_created += 1

        log.info(f"Split to new file: {self._current_filename}")

    def stop(self):
        """Stop and update total statistics"""
        self._total_bytes_all_files += self._bytes_written
        super().stop()
        log.info(f"Total files created: {self._total_files_created}, "
                 f"Total bytes: {self._total_bytes_all_files}")

    @property
    def total_bytes_all_files(self) -> int:
        """Get total bytes written across all files"""
        return self._total_bytes_all_files + self._bytes_written

    @property
    def total_files_created(self) -> int:
        """Get total number of files created"""
        return self._total_files_created
