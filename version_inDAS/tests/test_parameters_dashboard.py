import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import patch

from PyQt5.QtTest import QSignalSpy
import test_acquisition_settings as fixtures
from ui.integrated_window import MainWindow
from ui.parameter_package import read_parameters, write_parameters, MAGIC
from ui.runtime_status import RuntimeStatus
from ui.storage_settings import StorageSettingsDialog
from edas_hardware.integrated_panel import IntegratedEDASPanel
from edas_hardware.acquisition_settings import AcquisitionSettingsDialog


class ParametersDashboardTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixtures.AcquisitionSettingsTests.setUpClass()
        cls.helper = fixtures.AcquisitionSettingsTests()
        cls.app = cls.helper.app

    def test_package_encryption_random_nonce_round_trip_and_tamper(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            value = {'sensor': 'private sensor parameters', 'points': 32768}
            write_parameters(folder / 'a.par', value)
            write_parameters(folder / 'b.par', value)
            payload = (folder / 'a.par').read_bytes()
            self.assertTrue(payload.startswith(MAGIC))
            self.assertNotIn(b'private sensor parameters', payload)
            self.assertNotEqual(payload, (folder / 'b.par').read_bytes())
            self.assertEqual(read_parameters(folder / 'a.par'), value)
            (folder / 'a.par').write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
            with self.assertRaises(ValueError): read_parameters(folder / 'a.par')
            (folder / 'a.par').write_text(json.dumps(value), encoding='utf-8')
            with self.assertRaises(ValueError): read_parameters(folder / 'a.par')

    def test_all_parameters_export_import_and_next_launch_restore(self):
        with self.helper.window() as (w, folder):
            w.setting_plot_font_spin.setValue(15)
            w.port_spin.setValue(3888)
            draft = AcquisitionSettingsDialog(w.edas_panel, w)
            self.helper.select(draft, 'basic.point_num_per_scan', 32768)
            self.helper.select(draft, 'phase_demod.merge_point_num', 4)
            self.helper.select(draft, 'time_space.axis_unit', 'km')
            self.helper.select(draft, 'time_space.axis_origin', 1.5)
            self.assertTrue(draft.apply_settings(), draft.error_label.text())
            w.tab3_storage_path_edit.setText(str(folder / 'joint'))
            w.tab3_storage_interval_spin.setValue(3)
            w.tab3_joint_storage_toggle_btn.setChecked(True)
            exported = json.loads(json.dumps(w.get_current_config()))
            path = w.export_parameters(folder / 'sensors')
            self.assertEqual(path.suffix, '.par')
            w.port_spin.setValue(3999)
            w.setting_plot_font_spin.setValue(12)
            w.edas_panel.point_num_spin.setValue(16384)
            w.tab3_joint_storage_toggle_btn.setChecked(False)
            w.import_parameters(path)
            self.assertEqual(json.loads(json.dumps(w.get_current_config())), exported)
            self.assertFalse(w.monitoring_active)
            self.assertFalse(w._edas_monitoring_active)
            snapshot = json.loads((folder / 'gui.json').read_text(encoding='utf-8'))['config']
            with patch.object(MainWindow, '_read_persisted_configuration', return_value=snapshot), \
                 patch.object(IntegratedEDASPanel, '_load_local_params', return_value=None), \
                 patch.object(IntegratedEDASPanel, '_get_settings_path', return_value=folder / 'next_edas.json'):
                restored = MainWindow(simulation_mode=True)
            restored._persist_config_path = folder / 'next_gui.json'
            try:
                self.assertEqual(restored.port_spin.value(), 3888)
                self.assertEqual(restored.edas_panel.point_num_spin.value(), 32768)
                self.assertEqual(restored.edas_panel._timespace_origin, 1.5)
                self.assertEqual(restored.setting_plot_font_spin.value(), 15)
                self.assertTrue(restored.tab3_joint_storage_toggle_btn.isChecked())
                self.assertEqual(restored.tab3_storage_interval_spin.value(), 3)
                self.assertFalse(restored.monitoring_active)
                self.assertFalse(restored.edas_panel.acquisition_running())
            finally:
                restored.close()
                restored._auto_save_timer.stop()

    def test_bad_or_running_import_changes_no_parameters(self):
        with self.helper.window() as (w, folder):
            good = w.export_parameters(folder / 'good.par')
            state = json.loads(json.dumps(w.get_current_config()))
            invalid = json.loads(json.dumps(state))
            invalid['edas_params']['basic']['point_num_per_scan'] = 123
            write_parameters(folder / 'bad.par', invalid)
            with self.assertRaises(ValueError): w.import_parameters(folder / 'bad.par')
            self.assertEqual(json.loads(json.dumps(w.get_current_config())), state)
            invalid['edas_params']['basic']['point_num_per_scan'] = 'malformed'
            write_parameters(folder / 'bad.par', invalid)
            with self.assertRaises(ValueError): w.import_parameters(folder / 'bad.par')
            self.assertEqual(json.loads(json.dumps(w.get_current_config())), state)
            invalid = json.loads(json.dumps(state))
            invalid['edas_params']['save']['path'] = 'bad\x00path'
            write_parameters(folder / 'bad.par', invalid)
            with self.assertRaises(ValueError): w.import_parameters(folder / 'bad.par')
            w.monitoring_active = True
            with self.assertRaises(ValueError): w.import_parameters(good)
            self.assertEqual(json.loads(json.dumps(w.get_current_config())), state)
            w.monitoring_active = False

    def test_import_disk_failure_rolls_back_memory(self):
        with self.helper.window() as (w, folder):
            w.port_spin.setValue(4001)
            path = w.export_parameters(folder / 'a.par')
            w.port_spin.setValue(4002)
            before = json.loads(json.dumps(w.get_current_config()))
            with patch.object(w, '_write_persisted_configuration', return_value=False):
                with self.assertRaises(OSError): w.import_parameters(path)
            self.assertEqual(json.loads(json.dumps(w.get_current_config())), before)

    def test_joint_storage_dialog_controls_existing_pipeline_and_is_guarded(self):
        with self.helper.window() as (w, folder):
            self.assertTrue(w.tab3_joint_storage_toggle_btn.isHidden())
            self.assertTrue(w.tab3_storage_path_edit.isHidden())
            changed = QSignalSpy(w.tab3_settings_changed)
            draft = StorageSettingsDialog(w)
            draft.editors['tab3_joint_storage_toggle_btn'].setChecked(True)
            draft.editors['tab3_storage_path_edit'].setText('')
            self.assertFalse(draft.apply_settings())
            draft.editors['tab3_storage_path_edit'].setText(str(folder / 'joint'))
            draft.editors['tab3_storage_interval_spin'].setValue(4)
            self.assertTrue(draft.apply_settings(), draft.error_label.text())
            self.assertTrue(w.tab3_joint_storage_toggle_btn.isChecked())
            self.assertEqual(w.get_tab3_settings()['storage']['path'], str(folder / 'joint'))
            self.assertGreater(len(changed), 0)
            w.monitoring_active = True
            draft = StorageSettingsDialog(w)
            self.assertFalse(draft.editors['tab3_joint_format_combo'].isEnabled())
            self.assertTrue(draft.editors['tab3_joint_storage_toggle_btn'].isEnabled())
            draft.editors['tab3_storage_interval_spin'].setValue(5)
            self.assertFalse(draft.apply_settings())
            draft.reject()
            w.monitoring_active = False

    def test_dashboard_uses_real_progress_timeouts_and_completed_files(self):
        with self.helper.window() as (w, folder):
            p = w.edas_panel
            with patch.object(w.runtime_status, 'disk_space', return_value=[('X:', 100 * 1024 ** 3, '')]):
                w._refresh_dashboard()
                self.assertEqual(w.runtime_lights['edas'].property('state'), 'gray')
                w.set_fip_monitoring_active(True)
                self.assertEqual(w.runtime_lights['fip'].property('state'), 'yellow')
                w.update_statistics({'packets_received': 1})
                p.acq_thread = SimpleNamespace(isRunning=lambda: True, frames_acquired=100)
                w._edas_monitoring_active = True
                p._last_storage_snapshot = {'session': 1, 'files': 2, 'failures': 0, 'error': ''}
                try:
                    w.tab3_joint_storage_toggle_btn.setChecked(True)
                    completed = folder / 'joint.bin'
                    completed.write_bytes(b'completed')
                    w.update_tab3_storage_status(str(completed))
                    w.update_tab3_storage_status(str(completed))
                    w._refresh_dashboard()
                    self.assertEqual(w.runtime_lights['fip'].property('state'), 'green')
                    self.assertEqual(w.runtime_lights['edas'].property('state'), 'green')
                    self.assertEqual(w.runtime_lights['storage'].property('state'), 'green')
                    w.update_thread_stats({'processing': {'processing_failure_count': 1}})
                    w._refresh_dashboard()
                    self.assertEqual(w.runtime_lights['fip'].property('state'), 'red')
                    w.update_thread_stats({'processing': {'processing_failure_count': 0}})
                    self.assertEqual(w.runtime_status.files, {'joint': 1, 'das': 2, 'fip': 0})
                    self.assertIn('100.0 GiB', w.runtime_labels['disk'].text())
                    self.assertEqual(w.runtime_labels['fiber'].text(), p.summary_labels['fiber_length'].text())
                    w.update_tab3_storage_status('Storage failed: disk full')
                    w._refresh_dashboard()
                    self.assertEqual(w.runtime_lights['storage'].property('state'), 'red')
                    import time
                    with patch('ui.integrated_window.time.monotonic', return_value=time.monotonic() + 20):
                        w._refresh_dashboard()
                        self.assertEqual(w.runtime_lights['fip'].property('state'), 'red')
                        self.assertEqual(w.runtime_lights['edas'].property('state'), 'red')
                finally:
                    p.acq_thread = None
                    w.monitoring_active = False
                    w._edas_monitoring_active = False

    def test_runtime_counters_clock_and_disk_cache(self):
        status = RuntimeStatus()
        self.assertEqual(status.clock(True, 10), '00:00:00')
        self.assertEqual(status.clock(True, 16), '00:00:06')
        self.assertEqual(status.clock(False, 20), '00:00:10')
        self.assertEqual(status.clock(False, 30), '00:00:10')
        self.assertEqual(status.clock(True, 40), '00:00:00')
        for session, count in [(1, 2), (1, 2), (1, 3), (2, 1)]: status.observe('fip', session, count)
        self.assertEqual(status.files['fip'], 4)
        with tempfile.TemporaryDirectory() as temporary, patch('ui.runtime_status.shutil.disk_usage', return_value=SimpleNamespace(free=1000)) as query:
            status.disk_space([temporary], now=1)
            status.disk_space([temporary], now=2)
            self.assertEqual(query.call_count, 1)
            status.disk_space([temporary], now=7)
            self.assertEqual(query.call_count, 2)
        with patch('ui.runtime_status.Path.exists', side_effect=OSError('unavailable disk')):
            disks = status.disk_space(['unavailable'], now=10)
            self.assertIsNone(disks[0][1])

    def test_fip_failed_write_does_not_inflate_completed_count(self):
        import numpy as np
        from fip.manager import DataStorageThread
        with tempfile.TemporaryDirectory() as temporary:
            storage = DataStorageThread(None, storage_path=temporary)
            values = np.arange(10, dtype=float)
            arguments = (values, 0, 0, 10., 1., 10., 'raw')
            with patch('fip.manager.np.savez', side_effect=OSError('disk full')):
                storage._save_chunk(*arguments)
            self.assertEqual(storage.stats['saved_file_count'], 0)
            storage._save_chunk(*arguments)
            self.assertEqual(storage.stats['saved_file_count'], 1)
            self.assertEqual(len(list(Path(temporary).glob('*.npz'))), 1)


if __name__ == '__main__': unittest.main()
