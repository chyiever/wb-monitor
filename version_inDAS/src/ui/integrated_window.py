"""wb-monitor with the acquisition host integrated as its second tab."""
from PyQt5.QtWidgets import QLabel, QGroupBox
from PyQt5.QtCore import QTimer
from .main_window import MainWindow as BaseWindow
from edas_hardware.integrated_panel import IntegratedEDASPanel


class MainWindow(BaseWindow):
    def __init__(self, simulation_mode=False):
        self.simulation_mode = simulation_mode
        super().__init__()
        self.setWindowTitle('wb-monitor v_inDAS_261021')
        self.edas_panel.start_btn.clicked.disconnect()
        self.edas_panel.stop_btn.clicked.disconnect()
        self.edas_panel.start_btn.clicked.connect(self.tab3_start_requested.emit)
        self.edas_panel.stop_btn.clicked.connect(self.tab3_stop_requested.emit)
        self.edas_panel.pipeline_fault.connect(lambda _message: self.tab3_stop_requested.emit())
        self.view_param_splitter.splitterMoved.connect(self._view_width_changed)
        self.edas_panel.integrated_splitter.splitterMoved.connect(self._edas_width_changed)
        self._view_width_changed()
        QTimer.singleShot(0, self._view_width_changed)

    def _init_ui(self):
        super()._init_ui()
        self.edas_panel = IntegratedEDASPanel(self.simulation_mode, appearance_host=self)
        self.edas_panel.parameter_scroll.setMinimumWidth(self.VIEW_PARAM_PANEL_MIN_WIDTH)
        self.edas_panel.parameter_scroll.setMaximumWidth(self.VIEW_PARAM_PANEL_MAX_WIDTH)
        self.edas_panel.fip_period_provider = lambda: self.fip_packet_duration_spin.value()
        self.tab_widget.insertTab(1, self.edas_panel, 'eDAS')
        self.tab_widget.setTabText(2, 'FIP')
        for group in self.findChildren(QGroupBox):
            if group.title() == 'eDAS通信参数':
                group.setTitle('eDAS采集与共享状态')
                grid = group.layout()
                for col in range(4):
                    item = grid.itemAtPosition(0, col)
                    if item and item.widget(): item.widget().hide()
            if group.title() == 'eDAS独立存储':
                group.hide()
        self.tab3_edas_storage_toggle_btn.setChecked(False)
        self.tab3_edas_storage_toggle_btn.hide()
        self.tab3_edas_storage_path_edit.hide()
        for label in self.findChildren(QLabel):
            if label.text() == 'eDAS路径': label.hide()
            label.setText(label.text().replace('eDAS通信', 'eDAS采集'))

    def _view_width_changed(self, *_args):
        self.edas_panel.set_parameter_width(self._current_param_panel_width())
        self._schedule_auto_save()

    def _edas_width_changed(self, *_args):
        width = self.edas_panel.integrated_splitter.sizes()[0]
        self.edas_panel._parameter_width = width
        self._apply_param_panel_width(width)
        self._schedule_auto_save()

    def _apply_global_display_runtime_settings(self):
        super()._apply_global_display_runtime_settings()
        if hasattr(self, 'edas_panel'):
            self.edas_panel._apply_panel_appearance()
            self.edas_panel._refresh_parameter_summary()

    def _apply_tab3_visibility(self):
        super()._apply_tab3_visibility()
        if hasattr(self, 'edas_panel') and self.setting_show_tab3_check.isChecked():
            index = self.tab_widget.indexOf(self.detection_tab)
            self.tab_widget.removeTab(index)
            self.tab_widget.insertTab(3, self.detection_tab, 'Tab3')

    def get_tab3_settings(self):
        settings = super().get_tab3_settings()
        settings['storage']['edas_enabled'] = False
        return settings

    def _restore_tab3_settings(self, config):
        super()._restore_tab3_settings(config)
        self.tab3_edas_storage_toggle_btn.setChecked(False)

    def closeEvent(self, event):
        # Controller cleanup drains alignment writers after the event loop exits.
        self.edas_panel.close()
        super().closeEvent(event)
