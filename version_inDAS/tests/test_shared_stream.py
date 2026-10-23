import sys
import tempfile
import time
import unittest
import logging
from types import SimpleNamespace
from pathlib import Path
from unittest.mock import patch
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT.parent / 'fipedasREAD' / 'src'))
from edas_hardware.shared_stream import SharedStreamManager
from edas_hardware.config import AllParams, calculate_phase_point_num
from edas_hardware.data_saver import BitshuffleZstdFileSaver
from alignment.aligned_types import AlignedPacketFrame, DASSessionPacket, FIPSessionPacket
from das.joint_formats import save_bin, save_joint
from das.manager import EDASManager
from das.types import DASPacketHeader, DASRawPacket
from das.local_source import LocalDASSource
from fipedas_read.data_loader import load_joint_bin, merge_joint_data, concatenate_das_channel, load_data_file, iter_replay_files


class SharedStreamTests(unittest.TestCase):
    def make_params(self):
        p = AllParams()
        p.basic.scan_rate = 4
        p.basic.point_num_per_scan = 512
        p.phase_demod.merge_point_num = 128
        p.display.frame_load_num = 2
        p.comm.comm_frame_num = 4
        return p

    def test_shared_period_conversion_no_mutation_and_gate_numbering(self):
        p = self.make_params()
        width = calculate_phase_point_num(512, 128)
        block = np.arange(2 * width, dtype=np.int32)
        original = block.copy()
        packets = []
        s = SharedStreamManager()
        s.sink = packets.append
        settings = dict(comm_frames=4, channel_start=0, channel_end=width - 1,
                        time_downsample=1, space_downsample=1)
        try:
            s.start_session(p)
            for _ in range(2): s.enqueue_phase_data(block, p, settings)
            s.stop_session()
            self.assertEqual(len(packets), 1)
            expected = np.concatenate([original.reshape(2, width)] * 2).T
            np.testing.assert_array_equal(packets[0].data_1d.reshape(width, 4), expected)
            np.testing.assert_array_equal(block, original)
            np.testing.assert_array_equal(packets[0].raw_matrix,
                                          np.concatenate([original.reshape(2, width)] * 2).T)
            self.assertEqual(packets[0].raw_matrix.dtype, np.dtype('int32'))
            self.assertFalse(packets[0].raw_matrix.flags.writeable)
            self.assertFalse(packets[0].data_1d.flags.writeable)
            s.start_session(p)
            s.update_enabled(False, p)
            for _ in range(2): s.enqueue_phase_data(block, p, settings)
            # Wait until the off interval has actually been processed.
            deadline = time.monotonic() + 2
            while s._count < 1 and time.monotonic() < deadline: time.sleep(.005)
            s.update_enabled(True, p)
            for _ in range(2): s.enqueue_phase_data(block, p, settings)
            s.stop_session()
            self.assertEqual(packets[-1].header.comm_count, 1)
        finally:
            s.shutdown()

    def test_budget_failure_is_explicit_and_does_not_grow_queue(self):
        s = SharedStreamManager()
        try:
            p = self.make_params()
            s.start_session(p)
            s.MEMORY_BUDGET = 4
            s.enqueue_phase_data(np.zeros(16, dtype=np.int32), p, {})
            self.assertFalse(s._session_ready)
            self.assertEqual(s._bytes, 0)
            self.assertEqual(len(s._queue), 0)
        finally:
            s.shutdown()

    def test_binary_stream_remains_replay_compatible_and_atomic(self):
        matrix = np.arange(30, dtype=np.float32).reshape(3, 10) / 10
        das = DASSessionPacket(0, 1., 10., 3, matrix)
        arr = np.arange(5, dtype=np.float64)
        fip = FIPSessionPacket(0, 1., 5., arr, arr)
        frame = AlignedPacketFrame(0, 0., 1., fip, das)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(save_bin([frame], directory, '2026-10-21'))
            replay = load_joint_bin(path)
            np.testing.assert_array_equal(replay.das_frames[0], matrix)
            np.testing.assert_array_equal(replay.fip1_frames[0], arr)
            self.assertEqual(list(Path(directory).glob('*.part')), [])
            with patch('das.joint_formats._write_bin_frame', side_effect=OSError('disk fault')):
                with self.assertRaises(OSError): save_bin([frame], directory, '2026-10-21')
            self.assertEqual(len(list(Path(directory).glob('*.bin'))), 1)

    def test_integer_bin_exact_two_fip_sensors_and_legacy_compatibility(self):
        raw = np.array([[-2147483648, -16777217, 0, 16777217, 2147483647]], dtype=np.int32)
        radians = raw.astype(np.float32) * np.float32(np.pi / 32767.)
        packet = DASRawPacket(DASPacketHeader(0, 5, 1, raw.nbytes, 1.), raw.reshape(-1), raw_matrix=raw)
        self.assertEqual(LocalDASSource._packet_bytes(packet), raw.nbytes)
        parsed = EDASManager._parse_packet(SimpleNamespace(logger=logging.getLogger('test')), packet)
        np.testing.assert_array_equal(parsed.matrix, radians)
        np.testing.assert_array_equal(parsed.raw_matrix, raw)
        das = DASSessionPacket(0, 1., 5., 1, parsed.matrix, raw_matrix=parsed.raw_matrix)
        f1 = np.array([.123456789012345, -3.5], dtype=np.float64)
        f2 = np.array([5.123456789012345, 9.5], dtype=np.float64)
        fip = FIPSessionPacket(0, 1., 2., f1, f1, sensor_count=2,
                               unwrapped_by_sensor={1: f1, 2: f2})
        frame = AlignedPacketFrame(0, 0., 1., fip, das)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(save_joint('bin', [frame], folder, '2026-10-23'))
            self.assertEqual(path.read_bytes()[:8], b'FIPeDAS3')
            self.assertEqual(path.suffix, '.bin6432')
            self.assertIn(path, iter_replay_files(Path(folder)))
            opened = load_data_file(path)
            np.testing.assert_array_equal(opened.das_frames[0], raw)
            del opened
            for lazy in (False, True):
                replay = load_joint_bin(path, lazy=lazy)
                self.assertEqual(replay.das_frames[0].dtype, np.dtype('int32'))
                self.assertEqual(replay.das_units, 'phase_integer')
                np.testing.assert_array_equal(replay.das_frames[0], raw)
                np.testing.assert_array_equal(replay.fip1_frames[0], f1)
                np.testing.assert_array_equal(replay.fip2_frames[0], f2)
                _, values, _ = concatenate_das_channel(replay.das_frames,
                    replay.packet_start_times, replay.packet_duration_seconds, replay.das_rates_hz, 0)
                np.testing.assert_array_equal(values, raw[0])
                from fipedas_read.worker import _compute_curve, _compute_space, _file_info_text, CurveRequest, SpaceRequest
                from fipedas_read.preprocess import PreprocessSpec
                curve = _compute_curve(1, CurveRequest('eDAS', 0, PreprocessSpec(remove_mean=False), 1000), replay)
                np.testing.assert_allclose(curve.values, raw[0].astype(np.float64) * (np.pi / 32767.))
                self.assertIn('[rad]', curve.title)
                image, _, _ = _compute_space(SpaceRequest(0, 0, 1, 1, False, True, -1, 1), replay)
                np.testing.assert_allclose(image, raw.astype(np.float32) * np.float32(np.pi / 32767.))
                np.testing.assert_array_equal(replay.das_frames[0], raw)
                self.assertIn('int32', _file_info_text(replay))
            old = load_joint_bin(Path(save_bin([frame], folder, '2026-10-23')))
            np.testing.assert_array_equal(old.das_frames[0], radians)
            bin32 = load_joint_bin(Path(save_joint('bin32', [frame], folder, '2026-10-23')))
            np.testing.assert_array_equal(bin32.das_frames[0], radians)
            with self.assertRaisesRegex(ValueError, '不能混合'):
                merge_joint_data([replay, old])
            self.assertEqual(merge_joint_data([replay, replay]).das_units, 'phase_integer')
            del replay
            das.raw_matrix = None
            with self.assertRaisesRegex(ValueError, 'original DAS integers'):
                save_joint('bin', [frame], folder, '2026-10-23')

    def test_missing_compression_dependency_fails_before_threads_start(self):
        with tempfile.TemporaryDirectory() as directory:
            s = BitshuffleZstdFileSaver(directory)
            with patch('edas_hardware.bz_format.zstd', None):
                with self.assertRaises(RuntimeError): s.start(points_per_frame=10)
            self.assertFalse(s.is_running)
            self.assertEqual(list(Path(directory).glob('*')), [])

    def test_bin32_round_trip_uses_explicit_new_magic(self):
        matrix = np.arange(150, dtype=np.float32).reshape(3, 50) / 17
        packet = DASSessionPacket(7, 1., 50., 3, matrix)
        frame = AlignedPacketFrame(7, 7., 1., das_packet=packet, fip_missing=True)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(save_bin([frame], directory, '2026-10-21', das_dtype='<f4'))
            self.assertEqual(path.read_bytes()[:8], b'FIPeDAS2')
            replay = load_joint_bin(path)
            self.assertEqual(replay.format_version, 'wb-monitor-joint-bin-v2')
            np.testing.assert_array_equal(replay.das_frames[0], matrix)


if __name__ == '__main__':
    unittest.main()
