import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
if os.name == 'nt': os.environ.setdefault('QT_QPA_FONTDIR', r'C:\Windows\Fonts')
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QPoint

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT.parent / 'fipedasREAD' / 'src'))
from ui.integrated_window import MainWindow
from edas_hardware.integrated_panel import IntegratedEDASPanel
from edas_hardware.acquisition_settings import AcquisitionSettingsDialog
from alignment import AlignedSessionCoordinator, FIPSessionPacket
from das import EDASManager
from fipedas_read.data_loader import load_joint_bin


class IntegratedGUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_simulated_joint_tail_plots_controls_and_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            with patch.object(MainWindow, '_load_persisted_configuration', return_value=False), \
                 patch.object(IntegratedEDASPanel, '_load_local_params', return_value=None), \
                 patch.object(IntegratedEDASPanel, '_get_settings_path', return_value=folder / 'edas.json'):
                w = MainWindow(simulation_mode=True)
            w._persist_config_path = folder / 'gui.json'
            p = w.edas_panel
            dialog = AcquisitionSettingsDialog(p, w)
            for key, value in {'basic.scan_rate': 1000, 'basic.point_num_per_scan': 2048,
                'phase_demod.merge_point_num': 4, 'display.length_load_s': .1,
                'display.length_plot_s': 1., 'comm.length_comm_s': 1.,
                'comm.channel_start': 0, 'comm.channel_end': 99}.items():
                dialog.editors[key].setValue(value)
            dialog.editors['save.path'].setText(str(folder / 'das'))
            self.assertTrue(dialog.apply_settings(), dialog.error_label.text())
            p.save_enable_check.setChecked(True)
            p._set_combo_to_data(p.storage_format_combo, 'bin')
            w.tab3_storage_path_edit.setText(str(folder / 'joint'))
            w.tab3_joint_format_combo.setCurrentIndex(w.tab3_joint_format_combo.findData('bin'))
            w.tab3_joint_storage_toggle_btn.setChecked(True)
            co = AlignedSessionCoordinator()
            co.start_session()
            co.update_online_state('fip', True)
            manager = EDASManager(w, co)
            def inject_fip(header):
                count = header['comm_count']
                values = np.arange(1000, dtype=np.float64)
                co.push_fip_packet(FIPSessionPacket(count, 1., 1000., values, values))
            manager.server.header_updated.connect(inject_fip)
            w.show()
            w.tab_widget.setCurrentWidget(p)
            try:
                self.assertEqual([w.tab_widget.tabText(i) for i in range(w.tab_widget.count())],
                                 ['View', 'eDAS', 'FIP', 'Setting'])
                self.assertTrue(manager.start())
                # Wait on the observable result, allowing a loaded Windows host
                # scheduler to deliver three one-second simulated packets.
                until = time.monotonic() + 6.0
                while time.monotonic() < until:
                    self.app.processEvents()
                    time.sleep(.005)
                    if manager.server.packets_received >= 3:
                        break
                self.assertGreaterEqual(manager.server.packets_received, 3)
                self.assertGreater(len(p.plot_curve_1[0].getData()[0]), 0)
                self.assertGreater(p.spectrum_curve.getData()[0][-1], 400)  # full-rate PSD, not image DS
                self.assertGreater(p.time_space_widget._valid_block_count, 0)
                self.assertFalse(p.tab3_channel_start_spin.isEnabled())
                for plot in (p.plot_widget_1, p.time_space_widget.plot_widget):
                    self.assertEqual(plot.getAxis('left').width(), p.plot_widget_2.getAxis('left').width())
                p.integrated_psd_check.setChecked(False)
                self.assertTrue(p.plot_widget_2.isHidden())
                p.integrated_psd_check.setChecked(True)
                w.setting_show_tab3_check.setChecked(True)
                self.assertEqual(w.tab_widget.tabText(3), 'Tab3')
                manager.stop()
                files = sorted((folder / 'joint').glob('*.bin6432'))
                self.assertTrue(files)
                counts = np.concatenate([load_joint_bin(path).comm_counts for path in files])
                np.testing.assert_array_equal(counts, np.arange(manager.server.packets_received))
                self.assertTrue(list((folder / 'das').glob('*.bin')))
                self.assertEqual(manager.storage_worker.stats['save_failures'], 0)
                self.assertTrue(p.tab3_channel_start_spin.isEnabled())
                co.start_session()
                self.assertTrue(manager.start())
                until = time.monotonic() + 1.2
                while time.monotonic() < until:
                    self.app.processEvents()
                    time.sleep(.005)
                manager.stop()
            finally:
                manager.stop()
                w.close()
                w._auto_save_timer.stop()
                self.app.processEvents()


if __name__ == '__main__':
    unittest.main()
