"""Both waterfalls use the same streaming temporal filter, before decimation."""
import unittest
import threading
from unittest.mock import patch

import numpy as np
from PyQt5.QtCore import Qt
import test_acquisition_settings as settings_tests
from das.plot_worker import DASPlotWorker
from das.types import DASPacketHeader, DASParsedPacket
from edas_hardware.acquisition_settings import AcquisitionSettingsDialog
from edas_hardware.realtime_filter import RealtimeTimeAxisFilter, parse_filter_spec


class SharedWaterfallFilterTests(unittest.TestCase):
    def test_tab1_visibility_gates_image_work_without_changing_tab2(self):
        from alignment import AlignedSessionCoordinator
        from das.manager import EDASManager
        from das.types import DASRawPacket
        helper = settings_tests.AcquisitionSettingsTests()
        helper.setUpClass()
        with helper.window() as (window, folder):
            coordinator = AlignedSessionCoordinator()
            coordinator.start_session()
            manager = EDASManager(window, coordinator)
            window.tab_widget.setCurrentIndex(0)
            parsed = self.packet(np.ones((2, 100), dtype=np.float32))
            packet = DASRawPacket(parsed.header, parsed.matrix.reshape(-1))
            tab2_visible = window.edas_panel.integrated_timespace_check.isChecked()
            with patch.object(manager.space_time_worker, 'enqueue_packet') as image_queue:
                window.space_time_plot_btn.setChecked(False)
                manager._handle_raw_packet(packet)
                image_queue.assert_not_called()
                self.assertEqual(window.edas_panel.integrated_timespace_check.isChecked(), tab2_visible)
                window.space_time_plot_btn.setChecked(True)
                manager._handle_raw_packet(packet)
                image_queue.assert_called_once()
                window.edas_panel.integrated_timespace_check.setChecked(False)
                self.assertTrue(window.space_time_plot_btn.isChecked())
                window.tab_widget.setCurrentWidget(window.edas_panel)
                image_queue.reset_mock()
                manager._handle_raw_packet(packet)
                image_queue.assert_not_called()

    def test_hidden_tab2_skips_display_processing_but_dispatches_full_data(self):
        from edas_hardware.acquisition_thread import SimulatedAcquisitionThread
        from edas_hardware.config import AllParams, DataSource
        thread = SimulatedAcquisitionThread()
        params = AllParams()
        thread.configure(params)
        received = []
        thread.set_full_data_handler(lambda *args: received.append(args))
        data = np.arange(16, dtype=np.int32)
        thread.set_display_active(False)
        with patch.object(thread, '_get_display_points_per_frame', side_effect=AssertionError('hidden page computed a display')):
            thread._dispatch_full_data(data, DataSource.PHASE, 1)
            thread._publish_latest_display_data(data, DataSource.PHASE, 1)
        self.assertEqual(len(received), 1)
        self.assertIsNone(thread.take_latest_display_data())
        self.assertEqual(thread._display_history_frames, 0)
        thread.set_display_active(True)
        self.assertTrue(thread._display_active)

    def test_switching_tabs_updates_acquisition_visibility_without_changing_controls(self):
        from edas_hardware.acquisition_thread import SimulatedAcquisitionThread
        helper = settings_tests.AcquisitionSettingsTests()
        helper.setUpClass()
        with helper.window() as (window, folder):
            panel = window.edas_panel
            thread = SimulatedAcquisitionThread()
            thread.configure(panel._collect_params())
            panel.acq_thread = thread
            try:
                panel._sync_acquisition_display_request()
                self.assertTrue(thread._display_active)
                selected = panel.integrated_timespace_check.isChecked()
                window.tab_widget.setCurrentIndex(0)
                helper.app.processEvents()
                self.assertFalse(thread._display_active)
                self.assertFalse(panel._display_timer.isActive())
                window.tab_widget.setCurrentWidget(panel)
                helper.app.processEvents()
                self.assertTrue(thread._display_active)
                self.assertEqual(panel.integrated_timespace_check.isChecked(), selected)
            finally:
                panel.acq_thread = None

    def test_blocked_waterfall_does_not_block_waveform_worker(self):
        curves = DASPlotWorker(workload='curves')
        space = DASPlotWorker(workload='space_time')
        entered, release, received = threading.Event(), threading.Event(), threading.Event()
        def slow_image(**kwargs):
            entered.set()
            release.wait(3)
            return np.zeros((2, 10), np.float32), np.arange(10), np.arange(2)
        space._build_space_time_payload = slow_image
        curves.plot_payload_ready.connect(lambda payload: received.set(), Qt.DirectConnection)
        packet = self.packet(np.ones((2, 1000), dtype=np.float32))
        try:
            space.start()
            space.enqueue_packet(packet)
            self.assertTrue(entered.wait(2))
            with patch.object(curves, '_build_space_time_payload', side_effect=AssertionError('waveform must not filter image')):
                curves.start()
                curves.enqueue_packet(packet)
                self.assertTrue(received.wait(1), 'waveform waited for the blocked waterfall')
        finally:
            release.set()
            curves.stop(); space.stop()
            self.assertTrue(curves.wait(3000))
            self.assertTrue(space.wait(3000))

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
