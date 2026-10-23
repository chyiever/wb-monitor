"""Replay timestamps follow every selection; integer storage remains untouched."""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT.parent / 'fipedasREAD' / 'src'))
from fipedas_read.data_loader import JointReplayData
from fipedas_read.preprocess import PreprocessSpec
from fipedas_read.worker import CurveRequest, SpaceRequest, _compute_curve, _compute_space


class ReplayTimeAndUnitsTests(unittest.TestCase):
    def data(self, starts=(239.,)):
        count = 1_000_003
        fip = np.arange(count, dtype=np.float64)
        raw = np.full((2, 100003), 32767, dtype=np.int32)
        return JointReplayData(Path('test.bin6432'), 'wb-monitor-joint-bin-v3', '',
            np.arange(len(starts)), np.array(starts), np.ones(len(starts)),
            fip1_frames=[fip for _ in starts], fip2_frames=[-fip for _ in starts],
            das_frames=[raw for _ in starts], fip_rates_hz=np.full(len(starts), count),
            das_rates_hz=np.full(len(starts), 100003), das_channel_counts=np.full(len(starts), 2),
            fip_present=np.ones(len(starts), dtype=bool), das_present=np.ones(len(starts), dtype=bool),
            das_units='phase_integer', das_scale_to_rad=np.pi / 32767.)

    def test_long_fip_waveforms_keep_full_second_with_automatic_and_manual_strides(self):
        data = self.data()
        for source in ('FIP1', 'FIP2'):
            for downsample in (1, 3, 7):
                request = CurveRequest(source, 0, PreprocessSpec(remove_mean=False, downsample=downsample), 62500)
                result = _compute_curve(1, request, data)
                # Value is its original sample index (or its negative), so
                # timestamps can be checked independently of display strides.
                original_index = result.values if source == 'FIP1' else -result.values
                np.testing.assert_allclose(result.times, 239 + original_index / 1_000_003., atol=1e-12)
                self.assertGreater(result.times[-1], 239.999)

    def test_multifile_gap_is_preserved_in_waveform_timestamps(self):
        data = self.data(starts=(239., 241.))
        result = _compute_curve(1, CurveRequest('FIP1', 0, PreprocessSpec(remove_mean=False, downsample=3), 62500), data)
        self.assertTrue(np.any(np.diff(result.times) > 1.))
        self.assertGreater(result.times[-1], 241.999)

    def test_das_conversion_precedes_preprocessing_and_is_not_repeated(self):
        data = self.data()
        request = CurveRequest('eDAS', 0, PreprocessSpec(remove_mean=False, downsample=3), 1000)
        curve = _compute_curve(1, request, data)
        np.testing.assert_allclose(curve.values, np.pi)
        self.assertEqual(curve.units, 'rad')
        self.assertGreater(curve.times[-1], 239.99)
        image, rect, _ = _compute_space(SpaceRequest(0, 1, 2, 1, False, True, -1, 1), data)
        np.testing.assert_allclose(image, np.float32(np.pi), rtol=1e-6)
        self.assertEqual(rect[0], 239.)
        self.assertAlmostEqual(rect[2], 1.)
        # Load-time arrays and the source file representation remain integers.
        self.assertEqual(data.das_frames[0].dtype, np.dtype('int32'))
        np.testing.assert_array_equal(data.das_frames[0], 32767)
        # Historical files already contain radians and must not be scaled twice.
        data.das_units = 'rad'
        data.das_frames = [np.full((2, 100003), np.pi, dtype=np.float32)]
        old = _compute_curve(1, request, data)
        np.testing.assert_allclose(old.values, np.pi, rtol=1e-6)


if __name__ == '__main__':
    unittest.main()
