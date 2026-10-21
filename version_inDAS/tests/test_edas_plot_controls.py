"""Plot layout, physical coordinates and source/mode data-flow regression."""
import unittest
from unittest.mock import Mock, patch

import numpy as np
from PyQt5.QtCore import QPointF

import test_acquisition_settings as settings_tests
from edas_hardware.acquisition_settings import AcquisitionSettingsDialog
from edas_hardware.acquisition_thread import SimulatedAcquisitionThread
from edas_hardware.config import DataSource, DisplayMode
from edas_hardware.integrated_panel import IntegratedEDASPanel


class EDASPlotControlTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        settings_tests.AcquisitionSettingsTests.setUpClass()
        cls.helper = settings_tests.AcquisitionSettingsTests()
        cls.app = cls.helper.app

    def configure(self, p, w, **extra):
        dialog = AcquisitionSettingsDialog(p, w)
        values = {'basic.scan_rate': 1000, 'basic.point_num_per_scan': 2048,
                  'phase_demod.merge_point_num': 4, 'display.length_load_s': .1,
                  'display.length_plot_s': .4, 'comm.channel_start': 0,
                  'comm.channel_end': 9, 'time_space.distance_range_start': 2,
                  'time_space.distance_range_end': 12, 'time_space.space_downsample': 2,
                  'time_space.time_downsample': 2}
        values.update(extra)
        for path, value in values.items(): self.helper.select(dialog, path, value)
        self.assertTrue(dialog.apply_settings(), dialog.error_label.text())

    def test_psd_hidden_row_fills_with_time_space_and_fonts_restore(self):
        with self.helper.window() as (w, folder):
            p = w.edas_panel
            self.configure(p, w, **{'display.tick_font_size': 16})
            w.resize(1800, 1200)
            self.app.processEvents()
            before = p.time_space_widget.plot_widget.height()
            p.integrated_psd_check.setChecked(False)
            self.app.processEvents()
            self.assertTrue(p.plot_widget_2.isHidden())
            self.assertEqual(p.plot_grid.rowStretch(1), 0)
            self.assertEqual(p.plot_grid.cellRect(1, 0).height(), 0)
            self.assertGreater(p.time_space_widget.plot_widget.height(), before)
            # The image fills the bottom of the plot layout.
            self.assertLessEqual(abs(p.time_space_widget.plot_widget.geometry().bottom() - p.plot_grid.contentsRect().bottom()), 1)
            for plot in (p.plot_widget_1, p.plot_widget_2, p.time_space_widget.plot_widget):
                self.assertEqual(plot.getAxis('left').style['tickFont'].pointSize(), 16)
                self.assertEqual(plot.getAxis('left').style['tickFont'].family(), 'Times New Roman')
                self.assertEqual(plot.getAxis('left').width(), 112)
            p._save_local_params()
            with patch.object(IntegratedEDASPanel, '_get_settings_path', return_value=folder / 'edas.json'):
                restored = IntegratedEDASPanel(simulation_mode=True)
            try:
                self.assertEqual(restored._plot_font_size, 16)
                self.assertFalse(restored.integrated_psd_check.isChecked())
            finally:
                restored.close()

    def test_distance_axis_maps_pixel_centers_in_m_and_km_without_changing_data(self):
        with self.helper.window() as (w, folder):
            p = w.edas_panel
            self.configure(p, w)
            ts = p.time_space_widget
            raw = np.arange(20 * 512, dtype=np.float32).reshape(20, 512)
            self.assertTrue(ts.update_data(raw))
            ts._update_display()
            before = ts._display_buffer.copy()
            for unit, origin, dx in [('m', 125., 1.6), ('km', 1.25, .0016), ('points', 0., 1.)]:
                self.configure(p, w, **{'time_space.axis_unit': unit, 'time_space.axis_origin': origin})
                # Applying unchanged display-shape settings keeps the existing image.
                if ts._display_buffer is None:
                    ts.update_data(raw)
                ts._update_display()
                first = ts.image_item.mapToParent(QPointF(0.5, 0.5)).y()
                last = ts.image_item.mapToParent(QPointF(0.5, ts.image_item.image.shape[0] - .5)).y()
                self.assertAlmostEqual(first, 2 * dx + origin)
                self.assertAlmostEqual(last, 10 * dx + origin)
                np.testing.assert_array_equal(ts._display_buffer, before)
            self.configure(p, w, **{'time_space.axis_unit': 'km', 'time_space.axis_origin': 1.25})
            p._save_local_params()
            with patch.object(IntegratedEDASPanel, '_get_settings_path', return_value=folder / 'edas.json'):
                restored = IntegratedEDASPanel(simulation_mode=True)
            try:
                self.assertEqual(restored._timespace_unit, 'km')
                self.assertEqual(restored._timespace_origin, 1.25)
            finally:
                restored.close()

    def test_phase_time_space_channel_and_full_resolution_snapshot(self):
        with self.helper.window() as (w, folder):
            p = w.edas_panel
            self.configure(p, w)
            p.phase_channel_spin.setValue(5)
            self.assertEqual(p.params.display.region_index, 5)
            thread = SimulatedAcquisitionThread()
            thread.configure(p._collect_params())
            thread.set_display_request(DisplayMode.SPACE, 5, False, incremental_full_width=True)
            thread.set_display_transform(True, 2, 12, 2, 2, False, '')
            thread._display_publish_interval_s = 0
            frames = np.arange(100 * 512, dtype=np.int32).reshape(100, 512)
            thread._publish_latest_display_data(frames.reshape(-1), DataSource.PHASE, 1)
            data, source, channels, kind, meta = thread.take_latest_display_data()
            np.testing.assert_array_equal(meta[5], frames[-1])
            self.assertEqual(data.shape, (5, 50))
            scale = np.float32(np.pi / 32767.)
            p._update_phase_display(data, channels, scale, kind, meta)
            x, y = p.plot_curve_1[0].getData()
            np.testing.assert_allclose(y, frames[:, 5] * scale, rtol=1e-6)
            self.assertAlmostEqual(x[1] - x[0], .001)
            self.assertTrue(p.phase_channel_spin.isEnabled())
            p.phase_mode_combo.setCurrentIndex(p.phase_mode_combo.findData(int(DisplayMode.TIME)))
            self.assertEqual(p.params.display.mode, DisplayMode.TIME)
            self.assertFalse(p.phase_channel_spin.isEnabled())
            p._update_phase_display(data, channels, scale, kind, meta)
            x, y = p.plot_curve_1[0].getData()
            np.testing.assert_allclose(x, np.arange(512) * 1.6)
            np.testing.assert_allclose(y, frames[-1] * scale, rtol=1e-6)
            self.assertIn('Spatial', p.plot_widget_2.getAxis('bottom').labelText)
            p.phase_mode_combo.setCurrentIndex(p.phase_mode_combo.findData(int(DisplayMode.SPACE)))
            self.assertTrue(p.phase_channel_spin.isEnabled())

    def test_raw_hardware_source_and_monitor_live_switch_are_distinct(self):
        with self.helper.window() as (w, folder):
            p = w.edas_panel
            self.configure(p, w)
            self.assertEqual([p.wave_source_combo.itemText(i) for i in range(3)], ['Phase', 'RAW', 'Monitor'])
            p.wave_source_combo.setCurrentIndex(p.wave_source_combo.findData('raw'))
            self.assertEqual(p._collect_params().upload.data_source, DataSource.raw)
            self.assertTrue(p.time_space_widget.plot_widget.isHidden())
            params = p._collect_params()
            api = Mock()
            p.api = api
            self.assertTrue(p._configure_device(params))
            self.assertEqual(api.set_upload_data_param.call_args.args[1], DataSource.raw)
            raw = np.arange(params.basic.point_num_per_scan, dtype=np.int16)
            p._update_raw_display(raw, 1)
            np.testing.assert_array_equal(p.plot_curve_1[0].getData()[1], raw)
            with patch.object(p, 'acquisition_running', return_value=True):
                p.wave_source_combo.setCurrentIndex(p.wave_source_combo.findData('phase'))
                self.assertEqual(p.wave_source_combo.currentData(), 'raw')
                self.assertIn('停止', p.plot_source_notice.text())
            p.wave_source_combo.setCurrentIndex(p.wave_source_combo.findData('phase'))
            self.assertEqual(p._collect_params().upload.data_source, DataSource.PHASE)
            self.assertFalse(p.time_space_widget.plot_widget.isHidden())
            with patch.object(p, 'acquisition_running', return_value=True):
                p.wave_source_combo.setCurrentIndex(p.wave_source_combo.findData('monitor'))
                self.assertEqual(p.wave_source_combo.currentData(), 'monitor')
                self.assertTrue(p.monitor_enable_check.isChecked())
                self.assertEqual(p._collect_params().upload.data_source, DataSource.PHASE)
            p.phase_channel_spin.setValue(3)
            p._update_monitor_display(np.arange(512, dtype=np.float32), 1)
            self.assertEqual(p.plot_curve_1[0].getData()[1][-1], 3)


if __name__ == '__main__': unittest.main()
