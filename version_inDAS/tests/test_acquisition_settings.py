import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
if os.name == 'nt': os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
from PyQt5.QtCore import Qt, QPoint
from PyQt5.QtWidgets import QApplication, QComboBox, QDialogButtonBox
from PyQt5.QtTest import QTest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from ui.integrated_window import MainWindow
from edas_hardware.integrated_panel import IntegratedEDASPanel
from edas_hardware.acquisition_settings import AcquisitionSettingsDialog, config_value, format_fiber_distance
from edas_hardware.acquisition_thread import SimulatedAcquisitionThread
from edas_hardware.config import ClockSource, TriggerDirection, DataSource
from edas_hardware.data_saver import BitshuffleZstdFileSaver
from edas_hardware.bz_format import iter_bz_packets


class AcquisitionSettingsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    @contextmanager
    def window(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            with patch.object(MainWindow, '_load_persisted_configuration', return_value=False), \
                 patch.object(IntegratedEDASPanel, '_load_local_params', return_value=None), \
                 patch.object(IntegratedEDASPanel, '_get_settings_path', return_value=folder / 'edas.json'):
                w = MainWindow(simulation_mode=True)
            w._persist_config_path = folder / 'gui.json'
            w.show()
            w.tab_widget.setCurrentWidget(w.edas_panel)
            self.app.processEvents()
            try:
                yield w, folder
            finally:
                for dialog in w.findChildren(AcquisitionSettingsDialog): dialog.close()
                w.close()
                w._auto_save_timer.stop()
                self.app.processEvents()

    def select(self, dialog, path, value):
        editor = dialog.editors[path]
        if isinstance(editor, QComboBox):
            index = editor.findData(value)
            self.assertGreaterEqual(index, 0, (path, value))
            editor.setCurrentIndex(index)
        elif hasattr(editor, 'setValue'): editor.setValue(value)
        elif hasattr(editor, 'setChecked'): editor.setChecked(value)
        else: editor.setText(str(value))

    def test_all_settings_visible_editable_and_popup_belongs_to_dialog(self):
        with self.window() as (w, folder):
            p = w.edas_panel
            d = AcquisitionSettingsDialog(p, w)
            d.move(30, 30)
            d.show()
            self.app.processEvents()
            self.assertEqual(d.tabs.count(), 7)
            for key in ('basic.clk_src', 'basic.trig_dir', 'basic.bypass_point_num', 'basic.center_freq_mhz',
                        'upload.data_rate', 'phase_demod.rate2phase', 'phase_demod.polarization_diversity',
                        'display.length_load_s', 'display.length_plot_s', 'save.length_save_s',
                        'save.length_file_s', 'save.storage_downsample_factor', 'save.bz_zstd_level',
                        'save.bz_bitshuffle_block_values', 'save.bz_compression_workers'):
                self.assertIn(key, d.editors)
                self.assertTrue(d.editors[key].isEnabled(), key)
            d.tabs.setCurrentIndex(1)
            self.app.processEvents()
            combo = d.editors['upload.data_rate']
            combo.showPopup()
            self.app.processEvents()
            popup = combo.view().window().geometry()
            anchor = combo.mapToGlobal(QPoint(0, 0))
            self.assertLess(abs(popup.x() - anchor.x()), 20)
            self.assertTrue(popup.intersects(d.geometry()))
            second = combo.model().index(1, 0)
            QTest.mouseClick(combo.view().viewport(), Qt.LeftButton,
                             pos=combo.view().visualRect(second).center())
            self.assertEqual(combo.currentIndex(), 1)
            self.assertEqual(d.font().pointSize(), w.font().pointSize())
            d.reject()

    def test_colormap_popup_is_bounded_and_owned_click_does_not_cancel_draft(self):
        from edas_hardware.dialog_combo import contains_owned_combo_popup
        with self.window() as (w,folder):
            p=w.edas_panel
            d=AcquisitionSettingsDialog(p,w,section='display')
            d.show(); self.app.processEvents()
            combo=d.editors['time_space.colormap_type']
            for index in range(d.tabs.count()):
                d.tabs.setCurrentIndex(index);self.app.processEvents()
                if combo.isVisible(): break
            before=p._collect_params().time_space.colormap_type
            combo.showPopup();self.app.processEvents()
            popup=combo.view().window()
            self.assertTrue(d.frameGeometry().contains(popup.frameGeometry()))
            target=(combo.currentIndex()+1)%combo.count()
            model_index=combo.model().index(target,0)
            combo.view().scrollTo(model_index);self.app.processEvents()
            # Exercise ownership even when a native popup is positioned outside
            # the dialog's rectangle, as on the reported Windows desktop.
            popup.move(d.frameGeometry().right()+10,d.frameGeometry().top())
            pos=combo.view().visualRect(model_index).center()
            self.assertTrue(contains_owned_combo_popup(d,combo.view().viewport().mapToGlobal(pos)))
            QTest.mouseClick(combo.view().viewport(),Qt.LeftButton,pos=pos)
            self.assertTrue(d.isVisible())
            self.assertEqual(combo.currentIndex(),target)
            self.assertEqual(p._collect_params().time_space.colormap_type,before)
            selected=combo.currentData()
            self.assertTrue(d.apply_settings(),d.error_label.text())
            self.assertEqual(p._collect_params().time_space.colormap_type,selected)

    def test_cancel_and_invalid_draft_do_not_mutate_configuration(self):
        with self.window() as (w, folder):
            p = w.edas_panel
            before = asdict(p._collect_params())
            d = AcquisitionSettingsDialog(p, w)
            self.select(d, 'basic.scan_rate', 1000)
            self.select(d, 'display.length_load_s', .3)
            self.assertFalse(d.apply_settings())
            self.assertTrue(d.error_label.text())
            self.assertEqual(asdict(p._collect_params()), before)
            d.reject()
            self.assertEqual(asdict(p._collect_params()), before)

    def test_outside_click_discards_draft_and_live_distance_hints(self):
        with self.window() as (w, folder):
            p = w.edas_panel
            p._save_local_params()
            before = asdict(p._collect_params())
            saved = (folder / 'edas.json').read_bytes()
            p.open_acquisition_settings()
            d = p._settings_dialog
            d.move(w.mapToGlobal(QPoint(600, 300)))
            self.app.processEvents()
            self.assertGreaterEqual(d.width(), 840)
            self.assertEqual(d.windowModality(), Qt.NonModal)
            self.select(d, 'basic.point_num_per_scan', 32768)
            self.select(d, 'phase_demod.merge_point_num', 4)
            self.select(d, 'phase_demod.diff_order', 3)
            self.assertEqual(d.derived_labels['basic.point_num_per_scan'].text(), 'Fiber Length=13.11 km')
            self.assertEqual(d.derived_labels['phase_demod.merge_point_num'].text(), 'dx=1.6 m')
            self.assertEqual(d.derived_labels['phase_demod.diff_order'].text(), 'GL=4.8 m')
            # Internal input stays open. The actual outside mouse event cancels.
            QTest.mouseClick(d.editors['basic.point_num_per_scan'], Qt.LeftButton)
            self.assertTrue(d.isVisible())
            bar = w.tab_widget.tabBar()
            self.assertFalse(d.frameGeometry().contains(bar.mapToGlobal(QPoint(5, 5))))
            QTest.mouseClick(bar, Qt.LeftButton, pos=QPoint(5, 5))
            self.app.processEvents()
            self.assertIsNone(p._settings_dialog)
            self.assertEqual(asdict(p._collect_params()), before)
            self.assertEqual((folder / 'edas.json').read_bytes(), saved)

    def test_distance_units_and_seven_row_acquisition_summary(self):
        self.assertEqual(format_fiber_distance(10000), '10000.0 m')
        self.assertEqual(format_fiber_distance(10000.4), '10.00 km')
        with self.window() as (w, folder):
            p = w.edas_panel
            d = AcquisitionSettingsDialog(p, w)
            self.select(d, 'basic.point_num_per_scan', 32768)
            self.select(d, 'phase_demod.merge_point_num', 4)
            self.select(d, 'phase_demod.diff_order', 3)
            self.select(d, 'phase_demod.crop_distance_start', 2)
            self.select(d, 'phase_demod.crop_distance_end', 202)
            self.select(d, 'phase_demod.polarization_diversity', False)
            self.assertTrue(d.apply_settings(), d.error_label.text())
            self.assertEqual(p.summary_labels['fiber_length'].text(), '13.11 km')
            self.assertEqual(p.summary_labels['dx'].text(), '1.6 m')
            self.assertEqual(p.summary_labels['gl'].text(), '4.8 m')
            self.assertEqual(p.summary_labels['crop'].text(), 'CH 2~CH 201（Total: 200CH，320.0 m）')
            self.assertIn('#ef4444', p.summary_labels['polar'].styleSheet())
            self.assertNotIn('Pulse', p.summary_labels['acquisition'].text())
            self.assertNotIn('Points', p.summary_labels['acquisition'].text())
            self.assertNotIn('source', p.summary_labels)
            self.assertNotIn('hardware', p.summary_labels)
            self.assertEqual(p.summary_labels['acquisition'].parentWidget().layout().rowCount(), 7)

    def test_width_and_main_panel_appearance_match_view(self):
        with self.window() as (w, folder):
            p = w.edas_panel
            self.assertEqual(p.parameter_scroll.minimumWidth(), w.param_widget.minimumWidth())
            self.assertEqual(p.parameter_scroll.maximumWidth(), w.param_widget.maximumWidth())
            self.assertEqual(p.font().pointSize(), w.font().pointSize())
            self.assertLessEqual(abs(p.parameter_scroll.width() - w._current_param_panel_width()), 1)
            p.integrated_splitter.moveSplitter(560, 1)
            self.app.processEvents()
            self.assertLessEqual(abs(p.parameter_scroll.width() - w._current_param_panel_width()), 1)
            self.assertFalse(p.parameter_scroll.findChildren(QComboBox))
            self.assertTrue(all(label.wordWrap() for label in p.summary_labels.values()))
            p.open_acquisition_settings()
            self.app.processEvents()
            self.assertTrue(p._settings_dialog.isVisible())
            self.assertIs(p._settings_dialog.parentWidget(), w)
            p._settings_dialog.reject()

    def test_running_dialog_locks_hardware_but_allows_display_and_guards_bypass(self):
        with self.window() as (w, folder):
            p = w.edas_panel
            before = asdict(p._collect_params())
            with patch.object(p, 'acquisition_running', return_value=True):
                d = AcquisitionSettingsDialog(p, w)
                self.assertFalse(d.editors['basic.scan_rate'].isEnabled())
                self.assertTrue(d.editors['time_space.vmax'].isEnabled())
                self.assertTrue(d.buttons.button(QDialogButtonBox.Save).isEnabled())
                self.assertIn('只读', d.notice.text())
                d.editors['basic.scan_rate'].setValue(d.editors['basic.scan_rate'].value() + 1000)
                self.assertFalse(d.apply_settings())
            self.assertEqual(asdict(p._collect_params()), before)
            d.reject()

    def test_saved_shared_switch_is_restored_instead_of_forced_on(self):
        with self.window() as (w, folder):
            p = w.edas_panel
            p.shared_stream_check.setChecked(False)
            p._save_local_params()
            with patch.object(IntegratedEDASPanel, '_get_settings_path', return_value=folder / 'edas.json'):
                restored = IntegratedEDASPanel(simulation_mode=True)
            try:
                self.assertFalse(restored.tab3_comm_enable_check.isChecked())
                self.assertFalse(restored.shared_stream_check.isChecked())
            finally:
                restored.close()

    def test_dialog_values_reach_dll_acquisition_shared_stream_and_raw_saver(self):
        with self.window() as (w, folder):
            p = w.edas_panel
            p.save_enable_check.setChecked(True)
            d = AcquisitionSettingsDialog(p, w)
            changes = {
                'basic.scan_rate': 1000, 'basic.point_num_per_scan': 2048,
                'basic.clk_src': ClockSource.EXTERNAL, 'basic.trig_dir': TriggerDirection.INPUT,
                'basic.bypass_point_num': 64, 'basic.center_freq_mhz': 180,
                'upload.data_rate': 2, 'phase_demod.rate2phase': 2,
                'phase_demod.merge_point_num': 4, 'phase_demod.space_avg_order': 17,
                'phase_demod.diff_order': 2, 'phase_demod.detrend_bw': 12,
                'phase_demod.polarization_diversity': False,
                'phase_demod.crop_distance_start': 2, 'phase_demod.crop_distance_end': 202,
                'display.length_load_s': .1, 'display.length_plot_s': .4,
                'save.path': str(folder / 'raw'), 'save.storage_format': 'bin',
                'save.length_save_s': .1, 'save.length_file_s': .2,
                'save.storage_downsample_factor': 2, 'save.bz_zstd_level': 5,
                'save.bz_bitshuffle_block_values': 16384, 'save.bz_compression_workers': 2,
                'comm.channel_start': 0, 'comm.channel_end': 9,
                'comm.time_downsample': 2, 'comm.space_downsample': 2,
                'time_space.distance_range_start': 0, 'time_space.distance_range_end': 100,
            }
            for key, value in changes.items(): self.select(d, key, value)
            api = Mock()
            p.api = api
            self.assertTrue(d.apply_settings(), d.error_label.text())
            self.assertEqual(api.mock_calls, [])  # Draft application does not touch live hardware.
            params = p._collect_params()
            for key, value in changes.items(): self.assertEqual(config_value(params, key), value, key)
            self.assertEqual(params.display.frame_load_num, 100)
            self.assertEqual(params.save.length_file_s, .2)
            self.assertEqual(params.save.bz_compression_workers, 2)
            restored = AcquisitionSettingsDialog(p, w)
            self.assertEqual(restored.draft_config().save.bz_zstd_level, 5)
            restored.reject()
            self.assertTrue(p._configure_device(params))
            api.set_clk_src.assert_called_once_with(ClockSource.EXTERNAL)
            api.set_trig_dir.assert_called_once_with(TriggerDirection.INPUT)
            api.set_scan_rate.assert_called_once_with(1000)
            api.set_point_num_per_scan.assert_called_once_with(2048)
            api.set_bypass_point_num.assert_called_once_with(64)
            api.set_center_freq.assert_called_once_with(180000000)
            api.set_upload_data_param.assert_called_once_with(1, DataSource.PHASE, 2)
            api.set_phase_dem_param.assert_called_once_with(2, 17, 4, 2, 12, False)
            api.allocate_buffers.assert_called_once_with(2048, 1, 100, 4, True)
            thread = SimulatedAcquisitionThread()
            thread.configure(params)
            self.assertEqual(thread._frame_num, 100)
            self.assertEqual(thread._point_num_after_merge, 512)
            self.assertAlmostEqual(thread._display_publish_interval_s, .4)
            packets = []
            p.tcp_tab3_manager.sink = packets.append
            p.tcp_tab3_manager.start_session(params)
            p._tcp_settings_snapshot = p.get_tab3_comm_settings()
            self.assertTrue(p._start_data_saver(params))
            saver = p.data_saver
            raw = np.arange(1000 * 512, dtype=np.int32).reshape(1000, 512)
            try:
                for index in range(10):
                    block = thread._apply_phase_spatial_crop(raw[index * 100:(index + 1) * 100].reshape(-1))
                    p._handle_full_data_block(block, DataSource.PHASE, 1)
                    time.sleep(.003)
                p.tcp_tab3_manager.stop_session()
                p._stop_data_saver()
                self.assertEqual(len(packets), 1)
                packet = packets[0]
                self.assertEqual(packet.header.sample_rate_hz, 500)
                expected_shared = raw[::2, 2:12:2].T
                np.testing.assert_array_equal(packet.data_1d.reshape(5, 500), expected_shared)
                files = sorted((folder / 'raw').glob('*.bin'))
                self.assertEqual(len(files), 5)
                saved = np.concatenate([np.fromfile(path, dtype=np.int32).reshape(-1, 100) for path in files])
                np.testing.assert_array_equal(saved, raw[:, 2:202:2])
                self.assertEqual(saver.get_diagnostics_snapshot()['continuity_gap'], 0)
                self.assertIn('0.2 s', p.summary_labels['storage'].text())
                self.assertEqual(p.summary_labels['fiber_length'].text(), '819.2 m')
                p.save_enable_check.setChecked(True)
                compressed = AcquisitionSettingsDialog(p, w)
                self.select(compressed, 'save.storage_format', 'bz')
                self.select(compressed, 'save.path', str(folder / 'bz'))
                self.assertTrue(compressed.apply_settings(), compressed.error_label.text())
                with patch('edas_hardware.main_window.BitshuffleZstdFileSaver', wraps=BitshuffleZstdFileSaver) as factory:
                    self.assertTrue(p._start_data_saver(p._collect_params()))
                    self.assertEqual(factory.call_args.kwargs['zstd_level'], 5)
                    self.assertEqual(factory.call_args.kwargs['bitshuffle_block_values'], 16384)
                    self.assertEqual(factory.call_args.kwargs['compression_workers'], 2)
                    block = thread._apply_phase_spatial_crop(raw[:100].reshape(-1))
                    p._handle_full_data_block(block, DataSource.PHASE, 1)
                    p._stop_data_saver()
                bz_files = list((folder / 'bz').glob('*.bz'))
                self.assertEqual(len(bz_files), 1)
                decoded = [samples for _, _, samples in iter_bz_packets(bz_files[0], verify_crc=True)]
                np.testing.assert_array_equal(np.concatenate(decoded), raw[:100, 2:202:2])
            finally:
                p._stop_data_saver()
                p.tcp_tab3_manager.stop_session()


if __name__ == '__main__': unittest.main()
