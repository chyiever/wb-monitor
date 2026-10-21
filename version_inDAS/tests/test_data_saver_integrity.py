from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from edas_hardware.bz_format import iter_bz_packets
from edas_hardware.data_saver import (
    BitshuffleZstdFileSaver,
    BlockBasedFileSaver,
    calculate_storage_queue_capacities,
)


class DataSaverIntegrityTests(unittest.TestCase):
    SCAN_RATE = 10
    SOURCE_POINTS = 6
    SAVE_POINTS = 3
    CHANNELS = 2
    TOTAL_FRAMES = 12
    PACKET_FRAMES = 5
    FILE_FRAMES = 10

    @classmethod
    def _source_and_expected(cls) -> tuple[np.ndarray, np.ndarray]:
        source = np.arange(
            cls.TOTAL_FRAMES * cls.SOURCE_POINTS * cls.CHANNELS,
            dtype=np.int32,
        ).reshape(cls.TOTAL_FRAMES * cls.SOURCE_POINTS, cls.CHANNELS)
        expected = (
            source.reshape(cls.TOTAL_FRAMES, cls.SOURCE_POINTS, cls.CHANNELS)[:, ::2, :]
            .reshape(cls.TOTAL_FRAMES, cls.SAVE_POINTS * cls.CHANNELS)
            .copy()
        )
        return source, expected

    def _queue_blocks(self, saver, source: np.ndarray) -> None:
        start = 0
        for frame_count in (4, 3, 5):
            rows = frame_count * self.SOURCE_POINTS
            block = source[start:start + rows].copy()
            self.assertTrue(saver.save_block(block))
            self.assertFalse(block.flags.writeable)
            with self.assertRaises(ValueError):
                block[0, 0] = -1
            start += rows

    def _start(self, saver) -> None:
        saver.start(
            scan_rate=self.SCAN_RATE,
            points_per_frame=self.SAVE_POINTS,
            channel_num=self.CHANNELS,
            data_source=0,
            storage_downsample_factor=2,
            source_points_per_frame=self.SOURCE_POINTS,
        )

    def test_bin_round_trip_tail_rotation_downsample_and_ownership(self) -> None:
        source, expected = self._source_and_expected()
        with tempfile.TemporaryDirectory(prefix="pcie7821-bin-") as temp_dir:
            saver = BlockBasedFileSaver(
                temp_dir,
                packet_frames=self.PACKET_FRAMES,
                file_frames_per_file=self.FILE_FRAMES,
                buffer_size=8,
            )
            self._start(saver)
            self._queue_blocks(saver, source)
            saver.stop()

            files = sorted(Path(temp_dir).glob("*.bin"))
            self.assertEqual(len(files), 2)
            matrices = [
                np.fromfile(path, dtype=np.int32).reshape(-1, self.SAVE_POINTS * self.CHANNELS)
                for path in files
            ]
            actual = np.concatenate(matrices, axis=0)
            np.testing.assert_array_equal(actual, expected)
            self.assertEqual([matrix.shape[0] for matrix in matrices], [10, 2])

            diag = saver.get_diagnostics_snapshot()
            self.assertEqual(diag["dropped_blocks"], 0)
            self.assertEqual(diag["pending_frames"], 0)
            self.assertEqual(diag["packets_written"], 3)
            self.assertEqual(diag["frames_received"], self.TOTAL_FRAMES)
            self.assertEqual(diag["frames_written"], self.TOTAL_FRAMES)
            self.assertEqual(diag["continuity_gap"], 0)
            self.assertEqual(diag["bytes_written"], expected.nbytes)

    def test_bz_round_trip_crc_order_tail_rotation_and_downsample(self) -> None:
        source, expected = self._source_and_expected()
        with tempfile.TemporaryDirectory(prefix="pcie7821-bz-") as temp_dir:
            saver = BitshuffleZstdFileSaver(
                temp_dir,
                packet_frames=self.PACKET_FRAMES,
                file_frames_per_file=self.FILE_FRAMES,
                buffer_size=8,
                packet_queue_size=2,
                compressed_queue_size=2,
                compression_workers=2,
                bitshuffle_block_values=16,
            )
            self._start(saver)
            self._queue_blocks(saver, source)
            saver.stop()

            files = sorted(Path(temp_dir).glob("*.bz"))
            self.assertEqual(len(files), 2)
            packets = []
            packet_indices = []
            for path in files:
                for _file_info, packet_info, samples in iter_bz_packets(path, verify_crc=True):
                    packet_indices.append(packet_info["packet_index"])
                    packets.append(samples)
            actual = np.concatenate(packets, axis=0)
            np.testing.assert_array_equal(actual, expected)
            self.assertEqual(packet_indices, [0, 1, 2])
            self.assertEqual([packet.shape[0] for packet in packets], [5, 5, 2])

            diag = saver.get_diagnostics_snapshot()
            self.assertEqual(diag["dropped_blocks"], 0)
            self.assertEqual(diag["dropped_samples"], 0)
            self.assertEqual(diag["pending_frames"], 0)
            self.assertEqual(diag["packets_written"], 3)
            self.assertFalse(diag["has_cache"])
            self.assertEqual(diag["frames_received"], self.TOTAL_FRAMES)
            self.assertEqual(diag["frames_written"], self.TOTAL_FRAMES)
            self.assertEqual(diag["continuity_gap"], 0)

    def test_stop_waits_past_old_timeout_without_truncating(self) -> None:
        class DelayedWriter(BlockBasedFileSaver):
            def _write_packet(self, samples: np.ndarray) -> None:
                time.sleep(5.2)
                super()._write_packet(samples)

        source = np.arange(24, dtype=np.int32).reshape(4, 6)
        with tempfile.TemporaryDirectory(prefix="pcie7821-slow-bin-") as temp_dir:
            saver = DelayedWriter(
                temp_dir,
                packet_frames=4,
                file_frames_per_file=4,
                buffer_size=2,
            )
            saver.start(
                scan_rate=10,
                points_per_frame=6,
                source_points_per_frame=6,
            )
            self.assertTrue(saver.save_block(source.copy()))
            started = time.perf_counter()
            saver.stop()
            elapsed = time.perf_counter() - started

            files = list(Path(temp_dir).glob("*.bin"))
            self.assertGreaterEqual(elapsed, 5.0)
            self.assertEqual(len(files), 1)
            np.testing.assert_array_equal(
                np.fromfile(files[0], dtype=np.int32).reshape(4, 6),
                source,
            )
            self.assertEqual(saver.dropped_blocks, 0)

    def test_enqueue_after_stop_never_reports_success(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pcie7821-stop-race-") as temp_dir:
            saver = BlockBasedFileSaver(
                temp_dir,
                packet_frames=2,
                file_frames_per_file=4,
                buffer_size=2,
            )
            saver.start(scan_rate=10, points_per_frame=3, source_points_per_frame=3)
            first = np.arange(6, dtype=np.int32)
            self.assertTrue(saver.save_block(first))
            saver.stop()

            late = np.arange(6, dtype=np.int32)
            self.assertFalse(saver.save_block(late))
            self.assertTrue(late.flags.writeable)
            files = list(Path(temp_dir).glob("*.bin"))
            self.assertEqual(sum(path.stat().st_size for path in files), first.nbytes)

    def test_disk_overwrite_deletes_only_oldest_closed_data_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pcie7821-overwrite-") as temp_dir:
            root = Path(temp_dir)
            oldest = root / "0000001-old.bin"
            newer = root / "0000002-new.bz"
            active = root / "0000003-active.bin"
            unrelated = root / "notes.txt"
            oldest.write_bytes(b"old!")
            newer.write_bytes(b"newer!!!")
            active.write_bytes(b"active")
            unrelated.write_bytes(b"keep")
            os.utime(oldest, ns=(1_000_000_000, 1_000_000_000))
            os.utime(newer, ns=(2_000_000_000, 2_000_000_000))
            os.utime(active, ns=(3_000_000_000, 3_000_000_000))

            saver = BlockBasedFileSaver(
                temp_dir,
                overwrite_oldest_when_full=True,
                disk_reserve_bytes=0,
            )
            saver._current_filename = active.name
            fake_usage = type("DiskUsage", (), {"free": 0})()
            with patch("edas_hardware.data_saver.shutil.disk_usage", return_value=fake_usage):
                saver._ensure_disk_space(4)

            self.assertFalse(oldest.exists())
            self.assertTrue(newer.exists())
            self.assertTrue(active.exists())
            self.assertTrue(unrelated.exists())
            diag = saver.get_diagnostics_snapshot()
            self.assertEqual(diag["overwritten_files"], 1)
            self.assertEqual(diag["overwritten_bytes"], 4)

    def test_disk_overwrite_disabled_never_deletes_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pcie7821-no-overwrite-") as temp_dir:
            data_file = Path(temp_dir) / "old.bin"
            data_file.write_bytes(b"keep")
            saver = BlockBasedFileSaver(
                temp_dir,
                overwrite_oldest_when_full=False,
                disk_reserve_bytes=0,
            )
            fake_usage = type("DiskUsage", (), {"free": 0})()
            with patch("edas_hardware.data_saver.shutil.disk_usage", return_value=fake_usage):
                saver._ensure_disk_space(1024)
            self.assertTrue(data_file.exists())

    def test_disk_overwrite_raises_when_no_closed_data_file_is_available(self) -> None:
        with tempfile.TemporaryDirectory(prefix="pcie7821-overwrite-full-") as temp_dir:
            active = Path(temp_dir) / "active.bin"
            active.write_bytes(b"active")
            saver = BlockBasedFileSaver(
                temp_dir,
                overwrite_oldest_when_full=True,
                disk_reserve_bytes=0,
            )
            saver._current_filename = active.name
            fake_usage = type("DiskUsage", (), {"free": 0})()
            with patch("edas_hardware.data_saver.shutil.disk_usage", return_value=fake_usage):
                with self.assertRaises(OSError) as raised:
                    saver._ensure_disk_space(1)
            self.assertEqual(raised.exception.errno, 28)
            self.assertTrue(active.exists())

    def test_queue_capacity_is_bounded_by_bytes(self) -> None:
        mib = 1024 * 1024
        caps = calculate_storage_queue_capacities(
            block_bytes=56 * mib,
            packet_bytes=280 * mib,
            configured_max_blocks=200,
            available_memory_bytes=32 * 1024 * mib,
        )
        self.assertEqual(caps["memory_budget_bytes"], 1536 * mib)
        self.assertEqual(caps["raw_blocks"], 9)
        self.assertEqual(caps["packet_items"], 1)
        self.assertEqual(caps["compressed_items"], 1)
        self.assertLessEqual(
            caps["estimated_raw_queue_bytes"]
            + caps["estimated_packet_queue_bytes"]
            + caps["estimated_compressed_queue_bytes"],
            caps["memory_budget_bytes"],
        )


if __name__ == "__main__":
    unittest.main()
