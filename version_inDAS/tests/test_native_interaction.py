"""Windows regression: failed hardware initialization must not break mouse hits."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch


def native_probe():
    from PyQt5.QtCore import Qt, QTimer, QPoint
    from PyQt5.QtTest import QTest, QSignalSpy
    from PyQt5.QtWidgets import QApplication, QMessageBox, QPushButton

    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / 'src'))
    from ui.integrated_window import MainWindow
    from edas_hardware.integrated_panel import IntegratedEDASPanel

    app = QApplication([])
    # A locked/disconnected Windows desktop cannot resolve native screen hits,
    # even for an ordinary top-level Qt button. Distinguish that environment
    # from a regression in the embedded acquisition panel.
    baseline = QPushButton('Native desktop availability probe')
    baseline.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    baseline.setGeometry(100, 100, 360, 100)
    baseline.show()
    baseline.raise_()
    baseline.activateWindow()
    QTest.qWait(300)
    desktop_available = app.widgetAt(baseline.mapToGlobal(QPoint(20, 20))) is baseline
    baseline.close()
    if not desktop_available:
        print('Native screen hit-test unavailable even for a standalone Qt button')
        raise SystemExit(77)
    warnings = []

    def dismiss_warning():
        for widget in app.topLevelWidgets():
            if isinstance(widget, QMessageBox) and widget.isVisible():
                warnings.append(widget.text())
                widget.accept()

    timer = QTimer()
    timer.timeout.connect(dismiss_warning)
    timer.start(50)
    with tempfile.TemporaryDirectory() as temporary:
        folder = Path(temporary)
        api = Mock()
        api.open.return_value = -1
        with patch('edas_hardware.main_window.PCIe7821API', return_value=api), \
             patch.object(MainWindow, '_load_persisted_configuration', return_value=False), \
             patch.object(IntegratedEDASPanel, '_load_local_params', return_value=None), \
             patch.object(IntegratedEDASPanel, '_get_settings_path', return_value=folder / 'edas.json'):
            window = MainWindow(simulation_mode=False)
            # Device initialization must wait until embedding is complete.
            assert api.open.call_count == 0
            window._persist_config_path = folder / 'gui.json'
            # Keep other desktop windows from obscuring native hit-test points.
            window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
            window.showMaximized()
            window.tab_widget.setCurrentWidget(window.edas_panel)
            QTest.qWait(300)
            panel = window.edas_panel

            def click_at_screen_position(button):
                window.raise_()
                window.activateWindow()
                QTest.qWait(100)
                # QCheckBox includes trailing layout space outside its hit area.
                position = button.mapToGlobal(QPoint(10, button.height() // 2))
                target = app.widgetAt(position)
                assert target is button, (button.text(), type(target).__name__, position, window.geometry())
                QTest.mouseClick(target, Qt.LeftButton,
                                 pos=target.mapFromGlobal(position))
                app.processEvents()

            try:
                assert api.open.call_count == 1
                assert len(warnings) == 1
                click_at_screen_position(panel.acquisition_settings_btn)
                assert panel._settings_dialog.isVisible()
                dialog = panel._settings_dialog
                original = panel._collect_params().basic.point_num_per_scan
                editor = dialog.editors['basic.point_num_per_scan']
                editor.setValue(original + 1024)
                # Use a native screen hit outside the dialog, not reject().
                dialog.move(window.mapToGlobal(QPoint(600, 300)))
                QTest.qWait(100)
                bar = window.tab_widget.tabBar()
                position = bar.mapToGlobal(QPoint(10, 10))
                assert not dialog.frameGeometry().contains(position)
                target = app.widgetAt(position)
                assert target is bar
                QTest.mouseClick(target, Qt.LeftButton, pos=target.mapFromGlobal(position))
                QTest.qWait(100)
                assert panel._settings_dialog is None
                assert panel._collect_params().basic.point_num_per_scan == original
                requested = QSignalSpy(window.tab3_start_requested)
                click_at_screen_position(panel.start_btn)
                assert len(requested) == 1
                panel.parameter_scroll.ensureWidgetVisible(panel.save_enable_check)
                app.processEvents()
                before = panel.save_enable_check.isChecked()
                click_at_screen_position(panel.save_enable_check)
                assert panel.save_enable_check.isChecked() != before
                panel.parameter_scroll.ensureWidgetVisible(panel.shared_stream_check)
                app.processEvents()
                before = panel.shared_stream_check.isChecked()
                click_at_screen_position(panel.shared_stream_check)
                assert panel.shared_stream_check.isChecked() != before
            finally:
                window.close()
                window._auto_save_timer.stop()
                app.processEvents()


class NativeInteractionTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == 'win32', 'Windows native window regression')
    def test_device_open_failure_preserves_mouse_targets(self):
        environment = dict(os.environ, QT_QPA_PLATFORM='windows')
        result = subprocess.run([sys.executable, '-X', 'utf8', str(Path(__file__).resolve()), '--probe'],
                                env=environment, capture_output=True, text=True,
                                encoding='utf-8', timeout=30)
        if result.returncode == 77: self.skipTest(result.stdout.strip())
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)


if __name__ == '__main__':
    if '--probe' in sys.argv:
        native_probe()
    else:
        unittest.main()
