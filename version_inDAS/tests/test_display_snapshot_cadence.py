import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from edas_hardware.acquisition_thread import SimulatedAcquisitionThread
from edas_hardware.config import AllParams, DataSource


class DisplaySnapshotCadenceTests(unittest.TestCase):
    def test_publish_tolerance_accepts_slightly_early_load_block(self) -> None:
        thread = SimulatedAcquisitionThread()
        thread._display_publish_interval_s = 0.4
        thread._display_publish_tolerance_s = 0.03
        thread._last_display_publish_at = 10.0

        with patch("edas_hardware.acquisition_thread.time.perf_counter", return_value=10.39):
            self.assertTrue(thread._should_publish_display_snapshot())
        with patch("edas_hardware.acquisition_thread.time.perf_counter", return_value=10.36):
            self.assertFalse(thread._should_publish_display_snapshot())

    def test_time_space_incremental_snapshots_do_not_overlap(self) -> None:
        params = AllParams()
        params.basic.scan_rate = 10
        params.basic.point_num_per_scan = 6
        params.phase_demod.merge_point_num = 1
        params.phase_demod.crop_distance_start = 0
        params.phase_demod.crop_distance_end = 0
        params.display.frame_load_num = 2
        params.display.frame_plot_num = 4
        params.upload.data_source = DataSource.PHASE

        thread = SimulatedAcquisitionThread()
        thread.configure(params)
        thread.set_display_request(0, 0, False, incremental_full_width=True)
        thread._display_publish_interval_s = 0.0

        first = np.arange(12, dtype=np.int32)
        second = np.arange(12, 24, dtype=np.int32)
        thread._publish_latest_display_data(first, DataSource.PHASE, 1)
        first_snapshot = thread.take_latest_display_data()
        thread._publish_latest_display_data(second, DataSource.PHASE, 1)
        second_snapshot = thread.take_latest_display_data()

        self.assertEqual(first_snapshot[3], 3)
        self.assertEqual(second_snapshot[3], 3)

        # kind=3 now carries the prepared (space, time) display block plus metadata.
        np.testing.assert_array_equal(first_snapshot[0], first.reshape(2, 6).T)
        np.testing.assert_array_equal(second_snapshot[0], second.reshape(2, 6).T)
        self.assertEqual(first_snapshot[4][0], 2)  # source_frame_count
        self.assertEqual(second_snapshot[4][0], 2)
        self.assertEqual(first_snapshot[4][2], 0)  # distance_start
        self.assertEqual(first_snapshot[4][3], 6)  # distance_end
        np.testing.assert_array_equal(first_snapshot[4][4], first.reshape(2, 6)[:, 0])
        thread.set_display_transform(False, 0, 6, 1, 2, False, '')
        thread._publish_latest_display_data(first, DataSource.PHASE, 1)
        snapshot = thread.take_latest_display_data()
        self.assertEqual(snapshot[0].shape[1], 1)
        self.assertEqual(snapshot[4][4].size, 2)


if __name__ == "__main__":
    unittest.main()
