import sys
import tempfile
import time
import unittest
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
from das.joint_formats import save_bin
from fipedas_read.data_loader import load_joint_bin


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
            expected = np.concatenate([original.reshape(2, width)] * 2).T * (np.pi / 32767.)
            np.testing.assert_allclose(packets[0].data_1d.reshape(width, 4), expected, rtol=1e-6)
            np.testing.assert_array_equal(block, original)
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
