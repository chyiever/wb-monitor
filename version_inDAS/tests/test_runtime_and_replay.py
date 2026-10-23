"""Live parameters, long-run bounds, error diagnostics and large BIN replay."""
import gc
import struct
import sys
import tempfile
import time
import unittest
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PyQt5.QtTest import QTest
import test_acquisition_settings as fixtures
from edas_hardware.acquisition_settings import AcquisitionSettingsDialog
from edas_hardware.config import calculate_phase_point_num, AllParams
from edas_hardware.shared_stream import SharedStreamManager
from alignment import AlignedSessionCoordinator
from alignment.aligned_types import AlignedPacketFrame, DASSessionPacket, FIPSessionPacket
from das.storage_worker import DASStorageWorker, DASStorageRequest
from das.joint_formats import save_bin
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'fipedasREAD' / 'src'))
from fipedas_read.data_loader import load_data_file, load_joint_bin, build_space_time_matrix


class RuntimeAndReplayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.AcquisitionSettingsTests.setUpClass()
        cls.helper = fixtures.AcquisitionSettingsTests()

    def test_sections_crop_preview_icons_and_stable_button_style(self):
        with self.helper.window() as (w, folder):
            p = w.edas_panel
            p.open_acquisition_settings()
            d = p._settings_dialog
            self.assertNotIn('save.path', d.editors)
            self.assertNotIn('time_space.vmax', d.editors)
            self.helper.select(d, 'phase_demod.crop_distance_start', 2)
            self.helper.select(d, 'phase_demod.crop_distance_end', 12)
            total = calculate_phase_point_num(d.editors['basic.point_num_per_scan'].value(), d.editors['phase_demod.merge_point_num'].value())
            self.assertIn(f'原有 {total} CH → 裁剪后 10 CH', d.derived_labels['phase_demod.crop_distance_end'].text())
            d.reject()
            p.storage_settings_btn.click()
            self.assertIn('save.path', p._settings_dialog.editors)
            self.assertNotIn('basic.scan_rate', p._settings_dialog.editors)
            p._settings_dialog.reject()
            p.display_settings_btn.click()
            self.assertIn('comm.channel_end', p._settings_dialog.editors)
            self.assertNotIn('comm.length_comm_s', p._settings_dialog.editors)
            p._settings_dialog.reject()
            for i in range(w.tab_widget.count()): self.assertFalse(w.tab_widget.tabIcon(i).isNull())
            w._apply_global_control_metrics()
            before = w.data_comm_both_btn.styleSheet()
            w._apply_global_control_metrics()
            self.assertEqual(before, w.data_comm_both_btn.styleSheet())
            self.assertIn('font-size: 12pt', before)

    def test_live_display_shared_and_storage_edit_preserves_acquisition(self):
        with self.helper.window() as (w, folder):
            p = w.edas_panel
            d = AcquisitionSettingsDialog(p, w)
            for key, value in {'basic.scan_rate':1000, 'basic.point_num_per_scan':2048,
                'phase_demod.merge_point_num':4, 'display.length_load_s':.1,
                'display.length_plot_s':.4, 'comm.channel_start':0, 'comm.channel_end':9}.items():
                self.helper.select(d, key, value)
            self.assertTrue(d.apply_settings(), d.error_label.text())
            p.save_enable_check.setChecked(False)
            w.tab3_joint_storage_toggle_btn.setChecked(False)
            try:
                p._on_start()
                QTest.qWait(220)
                thread = p.acq_thread
                self.assertTrue(thread.isRunning())
                d = AcquisitionSettingsDialog(p, w, section='display')
                self.helper.select(d, 'comm.channel_start', 2)
                self.helper.select(d, 'comm.channel_end', 5)
                self.helper.select(d, 'time_space.axis_unit', 'km')
                self.helper.select(d, 'time_space.axis_origin', 1.25)
                self.helper.select(d, 'time_space.vmax', 20)
                self.assertTrue(d.apply_settings(), d.error_label.text())
                self.assertIs(thread, p.acq_thread)
                self.assertTrue(thread.isRunning())
                self.assertEqual(p._tcp_settings_snapshot['channel_start'], 2)
                w.tab3_joint_storage_toggle_btn.setChecked(True)
                live = AcquisitionSettingsDialog(p, w, section='display')
                self.assertTrue(live.editors['comm.channel_start'].isEnabled())
                self.helper.select(live, 'comm.channel_start', 3)
                self.assertTrue(live.apply_settings(), live.error_label.text())
                w.tab3_joint_storage_toggle_btn.setChecked(False)
                d = AcquisitionSettingsDialog(p, w, section='storage')
                self.helper.select(d, 'save.path', str(folder / 'raw'))
                self.assertTrue(d.apply_settings(), d.error_label.text())
                self.assertIs(thread, p.acq_thread)
                p.save_enable_check.setChecked(True)
                d = AcquisitionSettingsDialog(p, w, section='storage')
                self.assertFalse(d.editors['save.path'].isEnabled())
                d.editors['save.path'].setText(str(folder / 'other'))
                self.assertFalse(d.apply_settings())
                d.reject()
            finally: p._on_stop()

    def test_shared_range_change_inside_period_has_consistent_packet_shape(self):
        params = AllParams()
        params.basic.scan_rate = 4
        params.basic.point_num_per_scan = 512
        params.phase_demod.merge_point_num = 128
        width = calculate_phase_point_num(512,128)
        packets = []
        stream = SharedStreamManager()
        stream.sink = packets.append
        try:
            raw = np.arange(2*width,dtype=np.int32).reshape(2,width)
            settings = dict(comm_frames=4,channel_start=0,channel_end=0,enabled=True)
            stream._consume(raw,params,settings)
            stream._consume(raw,params,dict(settings,channel_end=width-1))
            self.assertEqual(packets[0].header.channel_count,width)
            expected = np.concatenate([raw,raw]).T * np.float32(np.pi/32767)
            np.testing.assert_allclose(packets[0].data_1d.reshape(width,4),expected,rtol=1e-6)
        finally: stream.shutdown()

    def test_missing_history_bounded_but_totals_preserved(self):
        c = AlignedSessionCoordinator()
        for index in range(1,1001): c._record_missing_ranges('fip',index*3,index*3+2)
        self.assertEqual(len(c._missing_ranges),256)
        self.assertEqual(c.snapshot_status().fip_missing_count,1000)
        c.reset_statistics()
        self.assertEqual(c.snapshot_status().fip_missing_count,0)

    def test_joint_writer_reports_original_exception(self):
        worker = DASStorageWorker()
        arr = np.arange(4.)
        frame = AlignedPacketFrame(0,0,1,FIPSessionPacket(0,1,4,arr,arr))
        messages, results = [], []
        worker.storage_saved.connect(messages.append)
        worker.request_finished.connect(lambda seq,ok:results.append(ok))
        with patch('das.storage_worker.save_joint', side_effect=PermissionError('test denied')):
            worker._save(DASStorageRequest([frame],'unused',0))
        self.assertEqual(results,[False])
        self.assertIn('PermissionError: test denied',worker.last_error)
        self.assertIn('PermissionError: test denied',messages[0])

    def test_protected_fip_only_cache_overflow_is_bounded_and_signalled_once(self):
        c = AlignedSessionCoordinator()
        c.max_cache_bytes = 100
        c.protect_unflushed_frames_after(-1)
        faults = []
        c.cache_overflow.connect(faults.append)
        for seq in range(10):
            arr = np.arange(10,dtype=np.float64)
            c.push_fip_packet(FIPSessionPacket(seq,1.,10.,arr,arr,unwrapped_by_sensor={1:arr}))
        self.assertEqual(len(faults),1)
        self.assertEqual(len(c._fip_packets),2)
        c.start_session()
        self.assertFalse(c._overflow_message)

    def test_shared_shape_changes_split_files_including_stop_tail(self):
        from das.manager import EDASManager
        matrices = [np.zeros((2,4)),np.zeros((2,4)),np.zeros((1,4))]
        frames = [AlignedPacketFrame(i,float(i),1.,das_packet=DASSessionPacket(i,1,4,m.shape[0],m)) for i,m in enumerate(matrices)]
        prefix, boundary = EDASManager._storage_prefix(frames)
        self.assertTrue(boundary)
        self.assertEqual([f.comm_count for f in prefix],[0,1])
        self.assertEqual(EDASManager._storage_prefix(frames[2:]),(frames[2:],False))

    def test_stop_tail_writes_two_files_when_shared_shape_changed(self):
        from das.manager import EDASManager
        with self.helper.window() as (w, folder):
            c = AlignedSessionCoordinator()
            c.start_session()
            w.tab3_joint_storage_toggle_btn.setChecked(True)
            w.tab3_storage_path_edit.setText(str(folder / 'joint'))
            m = EDASManager(w,c)
            m.sync_from_ui()
            for seq, channels in enumerate((2,1)):
                arr = np.arange(4.)
                c.push_fip_packet(FIPSessionPacket(seq,1,4,arr,arr))
                c.push_das_packet(DASSessionPacket(seq,1,4,channels,np.zeros((channels,4))))
            m.storage_worker.start()
            try:
                m._flush_joint_on_stop()
                self.assertEqual(m._last_snapshot_end_comm,1)
                self.assertEqual(m.storage_worker.stats['requests_saved'],2)
                self.assertEqual(len(list((folder / 'joint').iterdir())),2)
            finally: m.stop()

    def test_joint_filenames_do_not_overwrite_in_same_timestamp(self):
        from datetime import datetime
        arr = np.arange(4.)
        frame = AlignedPacketFrame(0,0,1,FIPSessionPacket(0,1,4,arr,arr))
        with tempfile.TemporaryDirectory() as folder, patch('das.joint_formats.datetime') as clock:
            clock.now.return_value = datetime(2026,10,23)
            first = Path(save_bin([frame],folder,'test'))
            second = Path(save_bin([frame],folder,'test'))
            self.assertNotEqual(first,second)
            self.assertTrue(first.exists() and second.exists())

    def test_stop_tail_respects_one_second_files_and_fixed_boundary(self):
        from das.manager import EDASManager
        with self.helper.window() as (w, folder):
            c = AlignedSessionCoordinator(); c.start_session()
            w.tab3_joint_storage_toggle_btn.setChecked(True)
            w.tab3_storage_interval_spin.setValue(1)
            w.tab3_storage_path_edit.setText(str(folder / 'joint'))
            m = EDASManager(w,c); m.sync_from_ui()
            for seq in range(3):
                arr=np.arange(4.)
                c.push_fip_packet(FIPSessionPacket(seq,1,4,arr,arr))
                c.push_das_packet(DASSessionPacket(seq,1,4,2,np.zeros((2,4))))
            # New FIP-only data may still arrive during an independent DAS stop.
            def late_packet(seq, ok):
                if seq==0:
                    arr=np.arange(4.)
                    c.push_fip_packet(FIPSessionPacket(3,1,4,arr,arr))
            m.storage_worker.request_finished.connect(late_packet)
            m.storage_worker.start()
            try:
                m._flush_joint_on_stop()
                self.assertEqual(m._last_snapshot_end_comm,2)
                self.assertEqual(m.storage_worker.stats['requests_saved'],3)
                files=list((folder/'joint').iterdir())
                self.assertEqual(len(files),3)
                for path in files:
                    self.assertEqual(len(load_data_file(path).comm_counts),1)
            finally: m.stop()

    def test_joint_and_manager_stop_ignore_reentrant_calls(self):
        from das.manager import EDASManager
        with self.helper.window() as (w, folder):
            calls=[]
            def during_drain():
                calls.append('drain')
                self.assertFalse(w.data_comm_both_btn.isEnabled())
                self.assertFalse(w.joint_stop_btn.isEnabled())
                w._stop_joint_acquisition()
            w.joint_stop_preparing.connect(lambda:calls.append('cut-input'))
            w.tab3_stop_requested.connect(during_drain)
            w._stop_joint_acquisition()
            self.assertEqual(calls,['cut-input','drain'])
            m=EDASManager(w,AlignedSessionCoordinator())
            with patch.object(m,'_stop_pipeline',side_effect=m.stop) as stop:
                m.stop()
                self.assertEqual(stop.call_count,1)
            self.assertFalse(m._stopping)

    def test_backlog_chunking_preserves_all_frames_in_order(self):
        from das.manager import EDASManager
        frames = [AlignedPacketFrame(i,float(i),1.,das_packet=DASSessionPacket(i,1,4,2,np.zeros((2,4)))) for i in range(13)]
        remaining, saved = frames, []
        while remaining:
            prefix, boundary = EDASManager._bounded_storage_prefix(remaining, 150)
            self.assertLessEqual(DASStorageRequest(prefix,'unused',0).estimated_bytes,150)
            saved.extend(f.comm_count for f in prefix)
            remaining = remaining[len(prefix):]
        self.assertEqual(saved,list(range(13)))

    def test_npz_numeric_frames_roundtrip_without_per_sample_objects(self):
        from das.joint_formats import save_npz
        matrix = np.arange(80,dtype=np.float32).reshape(2,40)
        arr = np.arange(40,dtype=np.float64)
        frames = [AlignedPacketFrame(i,float(i),1.,
            FIPSessionPacket(i,1,40,arr,arr,unwrapped_by_sensor={1:arr}),
            DASSessionPacket(i,1,40,2,matrix)) for i in range(2)]
        with tempfile.TemporaryDirectory() as folder:
            path = save_npz(frames,folder,'test')
            with np.load(path,allow_pickle=True) as archive:
                self.assertEqual(archive['das_raw_matrix'].shape,(2,))
                self.assertEqual(archive['das_raw_matrix'][0].dtype,np.float32)
                np.testing.assert_array_equal(archive['fip1_raw_data'][1],arr)
            data = load_data_file(Path(path))
            np.testing.assert_array_equal(data.das_frames[1],matrix)
            self.assertFalse(list(Path(folder).glob('*.part')))
            with patch('das.joint_formats.np.savez',side_effect=OSError('disk full')):
                with self.assertRaises(OSError): save_npz(frames,folder,'test')
            self.assertEqual(len(list(Path(folder).glob('*.npz'))),1)
            self.assertFalse(list(Path(folder).glob('*.part')))

    def test_lazy_bin_no_full_copy_truncation_rejected_and_preview_bounded(self):
        matrix = np.arange(20000,dtype=np.float32).reshape(20,1000)
        frame = AlignedPacketFrame(0,0,1,das_packet=DASSessionPacket(0,1,1000,20,matrix),fip_missing=True)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(save_bin([frame],folder,'test',das_dtype='<f4'))
            data = load_data_file(path)
            np.testing.assert_array_equal(data.das_frames[0],matrix)
            self.assertFalse(data.das_frames[0].flags.writeable)
            base = data.das_frames[0]
            while getattr(base,'base',None) is not None and not isinstance(base,np.memmap): base=base.base
            self.assertIsInstance(base,np.memmap)
            image, rect = build_space_time_matrix(data.das_frames,data.packet_start_times,data.das_rates_hz,
                data.packet_duration_seconds,0,19,1,1,False,max_pixels=1000)
            self.assertLessEqual(image.size,1000)
            del data,base
            gc.collect()
            truncated = Path(folder)/'truncated.bin'
            truncated.write_bytes(path.read_bytes()[:-10])
            with self.assertRaisesRegex(ValueError,'截断'): load_joint_bin(truncated)


if __name__ == '__main__': unittest.main()
