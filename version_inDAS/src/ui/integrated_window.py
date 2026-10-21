"""wb-monitor with the acquisition host integrated as its second tab."""
from PyQt5.QtWidgets import QLabel, QGroupBox
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

    def _init_ui(self):
        super()._init_ui()
        self.edas_panel = IntegratedEDASPanel(self.simulation_mode)
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
