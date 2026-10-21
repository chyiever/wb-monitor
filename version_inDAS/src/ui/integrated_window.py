"""wb-monitor with the acquisition host integrated as its second tab."""
from PyQt5.QtWidgets import QLabel, QGroupBox, QPushButton, QHBoxLayout
from PyQt5.QtCore import QTimer, Qt
from .main_window import MainWindow as BaseWindow
from .view_settings import ViewSettingsDialog
from edas_hardware.integrated_panel import IntegratedEDASPanel


class MainWindow(BaseWindow):
    def __init__(self, simulation_mode=False):
        self.simulation_mode = simulation_mode
        self._view_settings_dialog = None
        self._joint_start_pending = False
        self._joint_start_timer = None
        super().__init__()
        self._joint_start_timer = QTimer(self)
        self._joint_start_timer.setSingleShot(True)
        self._joint_start_timer.setTimerType(Qt.PreciseTimer)
        self._joint_start_timer.setInterval(1000)
        self._joint_start_timer.timeout.connect(self._finish_joint_start)
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

    def _create_parameter_panel(self):
        panel = super()._create_parameter_panel()
        layout = panel.layout()
        self._view_model_groups = []
        for i in range(layout.count() - 1, 0, -1):
            item = layout.itemAt(i)
            group = item.widget()
            if group is not None:
                layout.removeWidget(group)
                group.hide()
                self._view_model_groups.append(group)
        self.view_settings_btn = QPushButton('绘图参数设置…')
        self.view_settings_btn.clicked.connect(self.open_view_settings)
        layout.insertWidget(1, self.view_settings_btn)
        self.joint_control_group = QGroupBox('联合采集')
        bottom = self.joint_control_group
        from PyQt5.QtWidgets import QVBoxLayout
        controls = QVBoxLayout(bottom)
        self.joint_button_layout = QHBoxLayout()
        self.joint_stop_btn = QPushButton('Stop')
        self.joint_stop_btn.clicked.connect(self._stop_joint_acquisition)
        self.joint_button_layout.addWidget(self.joint_stop_btn)
        controls.addLayout(self.joint_button_layout)
        self.joint_state_label = QLabel('先启动 FIP，1 s 后启动 eDAS。')
        self.joint_state_label.setWordWrap(True)
        controls.addWidget(self.joint_state_label)
        layout.addWidget(bottom)
        return panel

    def open_view_settings(self):
        if self._view_settings_dialog is not None:
            self._view_settings_dialog.raise_()
            self._view_settings_dialog.activateWindow()
            return
        dialog = ViewSettingsDialog(self)
        self._view_settings_dialog = dialog
        def finished(_result):
            self._view_settings_dialog = None
            dialog.deleteLater()
        dialog.finished.connect(finished)
        dialog.show()

    def _init_ui(self):
        super()._init_ui()
        self.edas_panel = IntegratedEDASPanel(self.simulation_mode, appearance_host=self)
        self.edas_panel.parameter_scroll.setMinimumWidth(self.VIEW_PARAM_PANEL_MIN_WIDTH)
        self.edas_panel.parameter_scroll.setMaximumWidth(self.VIEW_PARAM_PANEL_MAX_WIDTH)
        self.edas_panel.fip_period_provider = lambda: self.fip_packet_duration_spin.value()
        self.tab_widget.insertTab(1, self.edas_panel, 'eDAS')
        self.tab_widget.setTabText(2, 'FIP')
        self.joint_button_layout.insertWidget(0, self.data_comm_both_btn)
        self.data_comm_both_btn.setCheckable(False)
        self.tab3_start_stop_btn.hide()
        self.tab3_start_stop_btn.setEnabled(False)
        comm_layout = self.start_stop_btn.parentWidget().layout()
        for button in (self.start_stop_btn, self.clear_stats_btn, self.tab3_start_stop_btn):
            comm_layout.removeWidget(button)
        for col in range(4): comm_layout.setColumnStretch(col, 0)
        comm_layout.addWidget(self.start_stop_btn, 0, 0)
        comm_layout.addWidget(self.clear_stats_btn, 0, 1)
        comm_layout.setColumnStretch(0, 1)
        comm_layout.setColumnStretch(1, 1)
        self._update_data_comm_buttons()
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
            self.edas_panel._apply_extra_plot_settings()
            self.edas_panel._apply_panel_appearance()
            self.edas_panel._refresh_parameter_summary()

    def _update_data_comm_buttons(self):
        super()._update_data_comm_buttons()
        if hasattr(self, 'joint_stop_btn') and hasattr(self, 'data_comm_both_btn'):
            busy = self._joint_start_pending or (self.monitoring_active and self._edas_monitoring_active)
            self.data_comm_both_btn.setText('Start')
            self.data_comm_both_btn.setEnabled(not busy)
            self.joint_stop_btn.setEnabled(self._joint_start_pending or self.monitoring_active or self._edas_monitoring_active)
            self._style_action_button(self.joint_stop_btn, True, min_height=44)

    def _toggle_both_communication(self, _checked=False):
        if self._joint_start_pending or (self.monitoring_active and self._edas_monitoring_active):
            return
        self._both_comm_requested = True
        self._reset_sync_tracking()
        self._start_monitoring()
        # The FIP controller reports startup failure synchronously via its state callback.
        if not self.monitoring_active:
            self.joint_state_label.setText('FIP 启动失败，未启动 eDAS。')
            self._both_comm_requested = False
            self._update_data_comm_buttons()
            return
        if self._edas_monitoring_active:
            self.joint_state_label.setText('FIP 与 eDAS 已启动。')
            self._update_data_comm_buttons()
            return
        self._joint_start_pending = True
        self.joint_state_label.setText('FIP 已启动，等待 1 s 启动 eDAS…')
        self._joint_start_timer.start()
        self._update_data_comm_buttons()

    def _finish_joint_start(self):
        pending = self._joint_start_pending
        self._joint_start_pending = False
        if pending and self.monitoring_active and self._both_comm_requested:
            if not self._edas_monitoring_active:
                self.tab3_start_requested.emit()
            self.joint_state_label.setText('FIP 与 eDAS 已启动。' if self._edas_monitoring_active else 'eDAS 未启动，请检查采集卡和参数；FIP 保持运行。')
        self._update_data_comm_buttons()

    def _stop_joint_acquisition(self):
        self._joint_start_pending = False
        self._both_comm_requested = False
        if self._joint_start_timer is not None: self._joint_start_timer.stop()
        # Drain eDAS first, then stop FIP; both stop handlers are idempotent.
        self.tab3_stop_requested.emit()
        self.stop_monitoring.emit()
        self._edas_monitoring_active = False
        self.monitoring_active = False
        self.joint_state_label.setText('eDAS 采集与 FIP 通信已停止。')
        self._update_data_comm_buttons()

    def set_fip_monitoring_active(self, active):
        super().set_fip_monitoring_active(active)
        if not active:
            self._joint_start_pending = False
            self._both_comm_requested = False
            if self._joint_start_timer is not None: self._joint_start_timer.stop()
            if hasattr(self, 'joint_state_label'):
                self.joint_state_label.setText('FIP 已停止。')
            self._update_data_comm_buttons()

    def _iter_plot_widgets(self):
        plots = super()._iter_plot_widgets()
        if hasattr(self, 'edas_panel'):
            plots.extend((self.edas_panel.plot_widget_1, self.edas_panel.plot_widget_2,
                          self.edas_panel.time_space_widget.plot_widget))
        return plots

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
        self._joint_start_pending = False
        if self._joint_start_timer is not None: self._joint_start_timer.stop()
        if self._view_settings_dialog is not None: self._view_settings_dialog.reject()
        # Controller cleanup drains alignment writers after the event loop exits.
        self.edas_panel.close()
        super().closeEvent(event)
