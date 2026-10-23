"""Both waterfalls use the same streaming temporal filter, before decimation."""
import unittest
from unittest.mock import patch

import numpy as np
import test_acquisition_settings as settings_tests
from das.plot_worker import DASPlotWorker
from das.types import DASPacketHeader, DASParsedPacket
from edas_hardware.acquisition_settings import AcquisitionSettingsDialog
from edas_hardware.realtime_filter import RealtimeTimeAxisFilter, parse_filter_spec


class SharedWaterfallFilterTests(unittest.TestCase):
    def packet(self, data, start=0., rate=10000):
        duration = data.shape[1] / rate
        return DASParsedPacket(DASPacketHeader(1, rate, len(data), data.nbytes, duration),
                               data, start, start + duration)

    def worker(self):
        worker = DASPlotWorker()
        worker.update_settings({'space_time_filter_enabled': True,
                                'space_time_filter_spec': '2-100',
                                'space_time_remove_baseline': False,
                                'space_time_total_seconds': 5.,
                                'space_time_max_pixels': 300000})
        return worker

    def test_full_rate_filter_matches_tab2_across_chunks_and_packets(self):
        t = np.arange(40006) / 10000.
        data = np.array([np.sin(2*np.pi*20*t) + np.sin(2*np.pi*1800*t),
                         3 + np.cos(2*np.pi*60*t)], dtype=np.float32)
        original = data.copy()
        expected = RealtimeTimeAxisFilter().process(data.T, parse_filter_spec('2-100'), 10000).T
        worker = self.worker()
        first = self.packet(data[:, :20003])
        second = self.packet(data[:, 20003:], first.packet_end_time)
        a, _, _ = worker._build_space_time_payload(first, 0, 1, 7, 1)
        b, _, _ = worker._build_space_time_payload(second, 0, 1, 7, 1)
        reference = np.concatenate([expected[:, :20003:7], expected[:, 20003::7]], axis=1)
        np.testing.assert_allclose(a, reference[:, :a.shape[1]], atol=1e-7)
        np.testing.assert_allclose(b, reference, atol=1e-7)
        np.testing.assert_array_equal(data, original)

    def test_runtime_change_applied_at_packet_boundary_and_resets_history(self):
        worker = self.worker()
        data = np.random.default_rng(4).normal(size=(2, 1000)).astype(np.float32)
        first = self.packet(data)
        worker._build_space_time_payload(first, 0, 1, 1, 1)
        payloads = []
        worker.plot_payload_ready.connect(payloads.append)
        with patch.object(worker, 'isRunning', return_value=True):
            worker.update_settings({'space_time_filter_spec': '-50'})
            self.assertEqual(worker.settings['space_time_filter_spec'], '2-100')
        worker._process_packet(self.packet(data, first.packet_end_time))
        expected = RealtimeTimeAxisFilter().process(data.T, parse_filter_spec('-50'), 10000).T
        np.testing.assert_allclose(payloads[-1]['space_time_matrix'], expected, atol=1e-7)
        worker.update_settings({'space_time_filter_enabled': False})
        actual, _, _ = worker._build_space_time_payload(first, 0, 1, 1, 1)
        np.testing.assert_array_equal(actual, data)

    def test_gap_resets_filter_and_invalid_cutoff_warns_once(self):
        worker = self.worker()
        data = np.random.default_rng(1).normal(size=(2, 1000)).astype(np.float32)
        first, _, _ = worker._build_space_time_payload(self.packet(data), 0, 1, 1, 1)
        gap, _, _ = worker._build_space_time_payload(self.packet(data, 3.), 0, 1, 1, 1)
        np.testing.assert_allclose(gap[:, -1000:], first, atol=1e-7)
        warnings = []
        worker.filter_warning.connect(warnings.append)
        worker.update_settings({'space_time_filter_spec': '-5000'})
        for start in (4., 5.):
            matrix, _, _ = worker._build_space_time_payload(self.packet(data, start), 0, 1, 1, 1)
            self.assertEqual(matrix.size, 0)
        self.assertEqual(len(warnings), 1)

    def test_tab2_dialog_commit_notifies_view_but_draft_does_not(self):
        helper = settings_tests.AcquisitionSettingsTests()
        helper.setUpClass()
        with helper.window() as (window, folder):
            notifications = []
            window.tab3_settings_changed.connect(lambda: notifications.append(True))
            dialog = AcquisitionSettingsDialog(window.edas_panel, window, section='display')
            before = window.get_tab3_settings()['plot']['space_time_filter_spec']
            helper.select(dialog, 'time_space.filter_enabled', True)
            helper.select(dialog, 'time_space.filter_spec', '2-10')
            self.assertEqual(window.get_tab3_settings()['plot']['space_time_filter_spec'], before)
            self.assertTrue(dialog.apply_settings(), dialog.error_label.text())
            settings = window.get_tab3_settings()['plot']
            self.assertTrue(settings['space_time_filter_enabled'])
            self.assertEqual(settings['space_time_filter_spec'], '2-10')
            self.assertTrue(notifications)


if __name__ == '__main__':
    unittest.main()
