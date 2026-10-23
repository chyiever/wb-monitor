"""wb-monitor with the acquisition host integrated as its second tab."""
from PyQt5.QtWidgets import (QLabel, QGroupBox, QPushButton, QHBoxLayout, QVBoxLayout,
    QFormLayout, QWidget, QFileDialog, QMessageBox)
from PyQt5.QtCore import QTimer, Qt, pyqtSignal
from dataclasses import asdict
from pathlib import Path
import time
from .main_window import MainWindow as BaseWindow
from .view_settings import ViewSettingsDialog
from .storage_settings import StorageSettingsDialog
from .runtime_status import RuntimeStatus
from .tab_icons import tab_icon
from .parameter_package import read_parameters, write_parameters, validate_structure
from edas_hardware.integrated_panel import IntegratedEDASPanel
from edas_hardware.acquisition_settings import AcquisitionSettingsDialog, config_value, format_fiber_distance
from edas_hardware.config import AllParams, DataSource


class MainWindow(BaseWindow):
    joint_stop_preparing = pyqtSignal()
    runtime_exception_reported = pyqtSignal(str)

    def __init__(self, simulation_mode=False):
        self.simulation_mode = simulation_mode
        self._view_settings_dialog = None
        self._joint_start_pending = False
        self._joint_stopping = False
        self._joint_start_stage = None
        self._joint_start_timer = None
        self._storage_settings_dialog = None
        self._parameters_ready = False
        self._parameter_transaction = False
        self.runtime_status = RuntimeStatus()
        self._runtime_app = None
        self._fip_packets_last = 0
        self._fip_packet_time = None
        self._fip_storage_session = 0
        self._fip_authoritative_active = False
        self._fip_connected = False
        self._fip_trigger_wait_at = None
        self._edas_frame_session = None
        self._edas_frame_count = 0
        self._edas_frame_time = None
        self._edas_fault = ''
        self._storage_error = ''
        self._joint_last_file = None
        self._fip_requested_at = None
        self._edas_requested_at = None
        self._joint_failure_baseline = 0
        self._fip_health_error = ''
        self._fip_receive_error_count = 0
        super().__init__()
        self._parameters_ready = True
        self._dashboard_timer = QTimer(self)
        self._dashboard_timer.setInterval(1000)
        self._dashboard_timer.timeout.connect(self._refresh_dashboard)
        self._dashboard_timer.start()
        self.tab3_joint_storage_toggle_btn.toggled.connect(self._joint_storage_toggled)
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
        self.edas_panel.pipeline_fault.connect(self._record_edas_fault)
        self.view_param_splitter.splitterMoved.connect(self._view_width_changed)
        self.edas_panel.integrated_splitter.splitterMoved.connect(self._edas_width_changed)
        self._view_width_changed()
        QTimer.singleShot(0, self._view_width_changed)
        self._refresh_dashboard()
        self._set_tab_icons()

    def _set_tab_icons(self):
        if not hasattr(self, '_tab_icon_cache'):
            self._tab_icon_cache = {name: tab_icon(name) for name in ('View', 'eDAS', 'FIP', 'Setting', 'Tab3')}
        for index in range(self.tab_widget.count()):
            self.tab_widget.setTabIcon(index, self._tab_icon_cache.get(self.tab_widget.tabText(index), self._tab_icon_cache['Tab3']))

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
        self.storage_settings_btn = QPushButton('存储参数设置…')
        self.storage_settings_btn.clicked.connect(self.open_storage_settings)
        layout.insertWidget(2, self.storage_settings_btn)
        self.runtime_group = QGroupBox('运行状态')
        form = QFormLayout(self.runtime_group)
        self.runtime_labels = {}
        self.runtime_lights = {}
        for caption, key in (('Fiber Length', 'fiber'), ('连续运行时长', 'uptime'),
                             ('eDAS', 'edas'), ('FIP', 'fip'), ('存储', 'storage'),
                             ('磁盘剩余空间', 'disk'), ('已存储文件数', 'files')):
            label = QLabel()
            label.setWordWrap(True)
            self.runtime_labels[key] = label
            if key in ('edas', 'fip', 'storage'):
                holder = QWidget()
                row = QHBoxLayout(holder)
                row.setContentsMargins(0, 0, 0, 0)
                light = QLabel()
                light.setFixedSize(14, 14)
                self.runtime_lights[key] = light
                row.addWidget(light)
                row.addWidget(label, 1)
                form.addRow(caption, holder)
            else: form.addRow(caption, label)
        layout.insertWidget(3, self.runtime_group)
        self.runtime_labels['uptime'].setToolTip('从当前连续采集/通信开始计时；全部停止后保留时长，下次启动重新计时。')
        self.runtime_labels['files'].setToolTip('本次打开软件后成功完成的数据文件总数，不含历史文件、空文件和正在写入的文件。')
        self.joint_control_group = QGroupBox('联合采集')
        bottom = self.joint_control_group
        controls = QVBoxLayout(bottom)
        self.joint_button_layout = QHBoxLayout()
        self.joint_stop_btn = QPushButton('Stop')
        self.joint_stop_btn.clicked.connect(self._stop_joint_acquisition)
        self.joint_button_layout.addWidget(self.joint_stop_btn)
        controls.addLayout(self.joint_button_layout)
        self.joint_state_label = QLabel('先监听，等待 FIP 客户端连接成功，1 s 后启动 eDAS。')
        self.joint_state_label.setWordWrap(True)
        self.joint_state_label.setMinimumWidth(0)
        self.joint_state_label.setMinimumHeight(3 * self.joint_state_label.fontMetrics().lineSpacing())
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

    def open_storage_settings(self):
        if self._storage_settings_dialog is not None:
            self._storage_settings_dialog.raise_()
            self._storage_settings_dialog.activateWindow()
            return
        dialog = StorageSettingsDialog(self)
        self._storage_settings_dialog = dialog
        def finished(_result):
            self._storage_settings_dialog = None
            self._refresh_dashboard()
            dialog.deleteLater()
        dialog.finished.connect(finished)
        dialog.show()

    def get_current_config(self):
        config = super().get_current_config()
        if hasattr(self, 'edas_panel'):
            config['edas_params'] = asdict(self.edas_panel._collect_params())
        return config

    def _apply_gui_config(self, config):
        params = AllParams()
        has_edas = isinstance(config.get('edas_params'), dict)
        if has_edas: self.edas_panel._merge_dict_into_dataclass(params, config['edas_params'])
        previous = self._parameter_transaction
        self._parameter_transaction = True
        try:
            super()._apply_gui_config(config)
            if has_edas: self.edas_panel.apply_acquisition_settings(params)
        finally:
            self._parameter_transaction = previous

    def _load_persisted_configuration(self, show_status=False):
        try:
            return super()._load_persisted_configuration(show_status)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            self._persist_logger.warning('Could not restore last parameters: %s', exc)
            self._apply_gui_config(self._default_gui_config)
            return False

    def export_parameters(self, path):
        path = Path(path)
        if path.suffix.lower() != '.par': path = path.with_suffix('.par')
        write_parameters(path, self.get_current_config())
        return path

    def import_parameters(self, path):
        if self.monitoring_active or self._edas_monitoring_active or self._joint_start_pending or self.edas_panel.acquisition_running():
            raise ValueError('请先停止 FIP 通信和 eDAS 采集，再导入参数。')
        state = read_parameters(path)
        validate_structure(state, self.get_current_config())
        params = AllParams()
        self.edas_panel._merge_dict_into_dataclass(params, state['edas_params'])
        dialog = AcquisitionSettingsDialog(self.edas_panel, self)
        try:
            for name, editor in dialog.editors.items():
                value = config_value(params, name)
                if hasattr(editor, 'minimum') and not editor.minimum() <= value <= editor.maximum():
                    raise ValueError(f'eDAS 参数超出范围：{name}')
                if hasattr(editor, 'itemData') and not any(editor.itemData(i) == value for i in range(editor.count())):
                    raise ValueError(f'eDAS 参数选项无效：{name}')
            if params.display.mode not in (0, 1) or params.display.wave_source not in ('phase', 'raw', 'monitor'):
                raise ValueError('eDAS 绘图模式无效')
            period = state['communication']['fip']['packet_duration_seconds']
            error = dialog.validate_draft(params, fip_period=period)
            if error: raise ValueError(error)
        finally:
            dialog.deleteLater()
        previous = self.get_current_config()
        self._parameter_transaction = True
        try:
            self._apply_gui_config(state)
            if not self._write_persisted_configuration(self.get_current_config()):
                raise OSError('导入参数无法保存到本地配置目录')
        except Exception:
            self._apply_gui_config(previous)
            raise
        finally:
            self._parameter_transaction = False
        self.config_changed.emit(self.get_current_config())
        self._refresh_dashboard()

    def _export_parameter_file(self):
        path, _ = QFileDialog.getSaveFileName(self, '导出本次全部传感参数', 'wb-monitor.par', 'wb-monitor 参数 (*.par)')
        if not path: return
        try:
            saved = self.export_parameters(path)
            self.status_bar.showMessage(f'参数已导出：{saved}', 8000)
        except (OSError, ValueError) as exc: QMessageBox.warning(self, '参数导出失败', str(exc))

    def _import_parameter_file(self):
        if self.monitoring_active or self._edas_monitoring_active or self._joint_start_pending or self.edas_panel.acquisition_running():
            QMessageBox.warning(self, '参数导入', '请先停止 FIP 通信和 eDAS 采集，再导入参数。')
            return
        path, _ = QFileDialog.getOpenFileName(self, '导入传感参数', '', 'wb-monitor 参数 (*.par)')
        if not path: return
        try:
            self.import_parameters(path)
            self.status_bar.showMessage(f'参数已导入并保存，下次启动自动恢复：{path}', 8000)
        except (OSError, ValueError, TypeError, KeyError) as exc: QMessageBox.warning(self, '参数导入失败', str(exc))

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
            if group.title() == '联合存储': group.hide()
        self.tab3_joint_storage_toggle_btn.hide()
        self.tab3_storage_path_edit.hide()
        switch_layout = self.phase_storage_check.parentWidget().layout()
        switch_layout.removeWidget(self.phase_storage_check)
        switch_layout.removeWidget(self.storage_path_edit)
        for col in range(6): switch_layout.setColumnStretch(col, 0)
        switch_layout.addWidget(self.phase_storage_check, 0, 0, 1, 2)
        switch_layout.addWidget(self.storage_path_edit, 1, 1)
        switch_layout.setColumnStretch(1, 1)
        self.tab3_edas_storage_toggle_btn.setChecked(False)
        self.tab3_edas_storage_toggle_btn.hide()
        self.tab3_edas_storage_path_edit.hide()
        for label in self.findChildren(QLabel):
            if label.text() in ('eDAS路径', '联合路径'): label.hide()
            label.setText(label.text().replace('eDAS通信', 'eDAS采集'))
            if label.text() == 'FIP路径':
                switch_layout.removeWidget(label)
                switch_layout.addWidget(label, 1, 0)
        setting_page = self.setting_apply_btn.parentWidget().parentWidget()
        package_group = QGroupBox('传感参数文件 (.par)')
        package_layout = QVBoxLayout(package_group)
        hint = QLabel('导出全部 FIP、eDAS、绘图、存储和全局设置；导入需先停止采集与通信。参数包采用程序专用加密封装和完整性校验。')
        self.parameter_package_hint = hint
        hint.setWordWrap(True)
        package_layout.addWidget(hint)
        buttons = QHBoxLayout()
        self.export_parameters_btn = QPushButton('导出参数 .par')
        self.import_parameters_btn = QPushButton('导入参数 .par')
        self.export_parameters_btn.clicked.connect(self._export_parameter_file)
        self.import_parameters_btn.clicked.connect(self._import_parameter_file)
        buttons.addWidget(self.export_parameters_btn)
        buttons.addWidget(self.import_parameters_btn)
        package_layout.addLayout(buttons)
        package_group.setMaximumWidth(700)
        setting_page.layout().insertWidget(1, package_group)

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
        if hasattr(self, 'parameter_package_hint'): self.parameter_package_hint.setWordWrap(True)
        if hasattr(self, 'joint_state_label'):
            self.joint_state_label.setWordWrap(True)
            self.joint_state_label.setMinimumHeight(3 * self.joint_state_label.fontMetrics().lineSpacing())
        if hasattr(self, 'edas_panel'):
            self.edas_panel._apply_extra_plot_settings()
            self.edas_panel._apply_panel_appearance()
            self.edas_panel._refresh_parameter_summary()
        if self._parameters_ready: self._refresh_dashboard()

    def _update_data_comm_buttons(self):
        super()._update_data_comm_buttons()
        if hasattr(self, 'joint_stop_btn') and hasattr(self, 'data_comm_both_btn'):
            busy = self._joint_stopping or self._joint_start_pending or (self.monitoring_active and self._edas_monitoring_active)
            self.data_comm_both_btn.setText('Start')
            self.data_comm_both_btn.setEnabled(not busy)
            self.joint_stop_btn.setEnabled(not self._joint_stopping and (self._joint_start_pending or self.monitoring_active or self._edas_monitoring_active))
            self._style_action_button(self.joint_stop_btn, True, min_height=44)
        if self._parameters_ready: self._refresh_dashboard()

    def _toggle_both_communication(self, _checked=False):
        if self._joint_stopping or self._joint_start_pending or (self.monitoring_active and self._edas_monitoring_active):
            return
        if self.monitoring_active or self._edas_monitoring_active or self.edas_panel.acquisition_running():
            # Stop handlers drain synchronously. Arm the restart only after both
            # have returned, since their state callbacks cancel pending starts.
            self._stop_joint_acquisition()
            self._both_comm_requested = True
            self._joint_start_pending = True
            self._joint_start_stage = 'restart_fip'
            self.joint_state_label.setText('两路已停止，等待 1 s 重新启动 FIP…')
            self._joint_start_timer.start()
            self._update_data_comm_buttons()
            return
        self._start_joint_fip()

    def _start_joint_fip(self):
        self._both_comm_requested = True
        self._reset_sync_tracking()
        self._joint_start_pending = True
        self._joint_start_stage = 'wait_fip_connection'
        self.joint_state_label.setText('监听中：等待 FIP（LabVIEW）客户端连接，尚未启动 eDAS。')
        self._tab3_logger.info('JOINT_START listening; waiting for FIP client before eDAS start')
        self._start_monitoring()
        # The FIP controller reports startup failure synchronously via its state callback.
        if not self.monitoring_active:
            self.joint_state_label.setText('FIP 启动失败，未启动 eDAS。')
            self._both_comm_requested = False
            self._joint_start_pending = False
            self._joint_start_stage = None
            self._update_data_comm_buttons()
            return
        if self._fip_connected:
            self._arm_joint_edas_start()
        self._update_data_comm_buttons()

    def _arm_joint_edas_start(self):
        if not (self._joint_start_pending and self._both_comm_requested and self._fip_connected):
            return
        if self._joint_start_stage != 'wait_fip_connection':
            return
        self._joint_start_stage = 'start_edas'
        self.joint_state_label.setText('已连接：FIP 客户端连接成功，等待 1 s 启动 eDAS。')
        self._tab3_logger.info('JOINT_START FIP connected; arming 1 s eDAS delay')
        self._joint_start_timer.start()

    def update_connection_status(self, connected, message):
        self._fip_connected = bool(connected)
        if not connected:
            self._fip_packet_time = None
            self._fip_packets_last = 0
            self._fip_trigger_wait_at = None
        super().update_connection_status(connected, message)
        if self._joint_start_pending and self._joint_start_stage in ('wait_fip_connection', 'start_edas'):
            if connected:
                self._arm_joint_edas_start()
            else:
                self._joint_start_timer.stop()
                self._joint_start_stage = 'wait_fip_connection'
                self.joint_state_label.setText('监听中：等待 FIP 连接；连接中断已取消 eDAS 启动倒计时。')
                self._tab3_logger.info('JOINT_START connection unavailable; eDAS delay cancelled')
        self._refresh_fip_phase_labels()
        if self._parameters_ready: self._refresh_dashboard()

    def _fip_phase(self):
        if not self.monitoring_active: return '未运行'
        if not self._fip_connected: return '监听中'
        if self._fip_packet_time is not None: return '已收数据'
        if self._joint_start_stage == 'start_edas': return '已连接'
        return '等待触发'

    def _refresh_fip_phase_labels(self):
        phase = self._fip_phase()
        for label in (self.conn_status_label, self.data_fip_status_label):
            label.setText(phase)
            label.setStyleSheet('color: ' + ('green' if phase == '已收数据' else '#b7791f' if self.monitoring_active else '#9aa0a6') + '; font-weight: bold;')

    def record_fip_packet_receive(self, comm_count, receive_time):
        first_packet = self._fip_packet_time is None
        super().record_fip_packet_receive(comm_count, receive_time)
        self._fip_packet_time = time.monotonic()
        self._refresh_fip_phase_labels()
        if first_packet:
            self._tab3_logger.info('FIP_STATE first complete packet received comm=%s', comm_count)
        if self._both_comm_requested and not self._joint_start_pending:
            self.joint_state_label.setText('已收数据：FIP 数据已到达；eDAS ' + ('已启动。' if self._edas_monitoring_active else '未启动。'))

    def _finish_joint_start(self):
        pending = self._joint_start_pending
        stage = self._joint_start_stage
        if not pending or stage not in ('restart_fip', 'start_edas'):
            return
        if stage == 'start_edas' and not self._fip_connected:
            self._joint_start_timer.stop()
            self._joint_start_stage = 'wait_fip_connection'
            self.joint_state_label.setText('监听中：等待 FIP 重新连接，尚未启动 eDAS。')
            return
        self._joint_start_pending = False
        self._joint_start_stage = None
        if pending and self._both_comm_requested and stage == 'restart_fip':
            self._start_joint_fip()
            return
        if pending and stage == 'start_edas' and self.monitoring_active and self._both_comm_requested:
            self._tab3_logger.info('JOINT_START connected delay complete; requesting eDAS acquisition')
            if not self._edas_monitoring_active:
                self.tab3_start_requested.emit()
            self.joint_state_label.setText(('已收数据：' if self._fip_packet_time is not None else '等待触发：') + 'eDAS 已启动，等待或接收 FIP 数据。' if self._edas_monitoring_active else 'eDAS 未启动，请检查采集卡和参数；FIP 保持监听。')
            self._refresh_fip_phase_labels()
        self._update_data_comm_buttons()

    def _stop_joint_acquisition(self):
        if self._joint_stopping:
            return
        self._joint_stopping = True
        self.joint_state_label.setText('正在停止采集并保存剩余数据，请等待…')
        self._update_data_comm_buttons()
        try:
            self._perform_joint_stop()
        finally:
            self._joint_stopping = False
            self._update_data_comm_buttons()

    def _perform_joint_stop(self):
        self._joint_start_pending = False
        self._joint_start_stage = None
        self._both_comm_requested = False
        if self._joint_start_timer is not None: self._joint_start_timer.stop()
        # Cut FIP input before waiting for the eDAS/joint storage tail.
        self.joint_stop_preparing.emit()
        # Drain eDAS first, then stop FIP; both stop handlers are idempotent.
        self.tab3_stop_requested.emit()
        self.stop_monitoring.emit()
        self._edas_monitoring_active = False
        self.monitoring_active = False
        self._fip_connected = False
        self._fip_packet_time = None
        self._fip_trigger_wait_at = None
        self._refresh_fip_phase_labels()
        self.joint_state_label.setText('eDAS 采集与 FIP 通信已停止。')
        self._update_data_comm_buttons()

    def set_fip_monitoring_active(self, active):
        if active and not self._fip_authoritative_active:
            self._fip_storage_session += 1
            self._fip_packets_last = 0
            self._fip_packet_time = None
            self._fip_requested_at = time.monotonic()
            self._fip_health_error = ''
            self._fip_receive_error_count = 0
        self._fip_authoritative_active = bool(active)
        super().set_fip_monitoring_active(active)
        if not active:
            self._fip_connected = False
            self._fip_packet_time = None
            self._fip_trigger_wait_at = None
            self._joint_start_pending = False
            self._joint_start_stage = None
            self._both_comm_requested = False
            if self._joint_start_timer is not None: self._joint_start_timer.stop()
            if hasattr(self, 'joint_state_label'):
                self.joint_state_label.setText('FIP 已停止。')
            self._update_data_comm_buttons()
        self._refresh_fip_phase_labels()

    def set_tab3_monitoring_active(self, active):
        if active:
            self._edas_fault = ''
            if not self._edas_monitoring_active: self._edas_requested_at = time.monotonic()
            if self._fip_connected and self._fip_packet_time is None:
                self._fip_trigger_wait_at = time.monotonic()
        super().set_tab3_monitoring_active(active)
        self._refresh_dashboard()

    def bind_runtime_sources(self, app):
        self._runtime_app = app
        self._refresh_dashboard()

    def record_das_storage(self, snapshot):
        self.runtime_status.observe('das', snapshot['session'], snapshot['files'], snapshot['failures'])

    def _joint_storage_toggled(self, enabled):
        if enabled:
            self._storage_error = ''
            worker = getattr(getattr(self._runtime_app, 'edas_manager', None), 'storage_worker', None)
            self._joint_failure_baseline = (worker.stats.get('save_failures', 0) + worker.stats.get('requests_dropped', 0)) if worker is not None else 0
        self._refresh_dashboard()

    def update_thread_stats(self, stats):
        super().update_thread_stats(stats)
        processing = stats.get('processing', {})
        errors = sum(int(processing.get(name, 0)) for name in ('processing_failure_count', 'phase_unwrap_failure_count', 'queue_drop_count'))
        self._fip_health_error = f'处理异常 / 丢包：{errors}' if errors else ''
        storage = stats.get('storage', {})
        self.runtime_status.observe('fip', self._fip_storage_session,
            storage.get('saved_file_count', 0), storage.get('storage_failure_count', 0))

    def update_statistics(self, stats):
        super().update_statistics(stats)
        packets = int(stats.get('packets_received', 0) or 0)
        if packets > 0 and packets != self._fip_packets_last:
            self._fip_packet_time = time.monotonic()
        self._fip_packets_last = packets
        self._fip_receive_error_count = int(stats.get('packets_lost', stats.get('missing_packets', 0)) or 0)

    def update_tab3_storage_status(self, path):
        super().update_tab3_storage_status(path)
        text = str(path)
        if text != self._joint_last_file and Path(text).suffix.lower() in ('.npz', '.bin', '.h5'):
            try: completed = Path(text).is_file()
            except (OSError, ValueError): completed = False
            if completed:
                self._storage_error = ''
                self._joint_last_file = text
                self.runtime_status.files['joint'] += 1
                self.runtime_status.last_write = time.monotonic()
                self.runtime_status.last_writes['joint'] = self.runtime_status.last_write
        if any(word in text.lower() for word in ('error', 'failed', '失败')):
            self._storage_error = text

    def _record_edas_fault(self, message):
        self._edas_fault = str(message)
        self._refresh_dashboard()

    def show_tab3_error(self, message):
        if any(word in str(message).lower() for word in ('storage', '存储', '写盘')):
            self._storage_error = str(message)
        else: self._edas_fault = str(message)
        self._refresh_dashboard()
        super().show_tab3_error(message)

    def _dashboard_light(self, key, color, text):
        colors = {'gray': '#9ca3af', 'yellow': '#eab308', 'green': '#22c55e', 'red': '#ef4444'}
        light = self.runtime_lights[key]
        light.setFixedSize(14, 14)
        light.setProperty('state', color)
        light.setStyleSheet(f'background: {colors[color]}; border-radius: 7px;')
        light.setToolTip(text)
        self.runtime_labels[key].setText(text)

    def _refresh_dashboard(self):
        if not self._parameters_ready or not hasattr(self, 'edas_panel'): return
        now = time.monotonic()
        panel = self.edas_panel
        thread = panel.acq_thread
        das_running = panel.acquisition_running()
        active = self.monitoring_active or self._edas_monitoring_active or das_running
        self.import_parameters_btn.setEnabled(not active and not self._joint_start_pending)
        self.runtime_labels['fiber'].setText(format_fiber_distance(panel.point_num_spin.value() * .4))
        self.runtime_labels['uptime'].setText(self.runtime_status.clock(active, now))
        if thread is not None:
            frames = int(thread.frames_acquired)
            if id(thread) != self._edas_frame_session or frames < self._edas_frame_count:
                self._edas_frame_session, self._edas_frame_count, self._edas_frame_time = id(thread), 0, None
            if frames > self._edas_frame_count:
                self._edas_frame_time = now
            self._edas_frame_count = frames
        grace = max(5., panel.length_plot_spin.value() * 3)
        das_good = das_running and self._edas_frame_time is not None and now - self._edas_frame_time <= grace
        if self._edas_fault: self._dashboard_light('edas', 'red', self._edas_fault)
        elif das_good: self._dashboard_light('edas', 'green', '采集正常')
        elif self._edas_monitoring_active or das_running:
            origin = self._edas_frame_time if self._edas_frame_time is not None else self._edas_requested_at
            failed = origin is not None and now - origin > grace
            self._dashboard_light('edas', 'red' if failed else 'yellow', '采集数据超时' if failed else '等待采集数据')
        elif not self.simulation_mode and (panel.api is None or not panel.api._is_open):
            self._dashboard_light('edas', 'red', '采集卡未连接')
        else: self._dashboard_light('edas', 'gray', '未运行')
        fip_timeout = max(5., self.fip_packet_duration_spin.value() * 3)
        fip_good = self.monitoring_active and self._fip_packet_time is not None and now - self._fip_packet_time <= fip_timeout
        if not self.monitoring_active: self._dashboard_light('fip', 'gray', '未运行')
        elif self._fip_health_error or self._fip_receive_error_count:
            self._dashboard_light('fip', 'red', self._fip_health_error or f'通信缺包：{self._fip_receive_error_count}')
        elif not self._fip_connected: self._dashboard_light('fip', 'yellow', '监听中：等待 FIP 连接')
        elif fip_good: self._dashboard_light('fip', 'green', '已收数据：接收正常')
        elif self._fip_packet_time is None and self._fip_trigger_wait_at is None:
            self._dashboard_light('fip', 'yellow', '已连接' if self._joint_start_stage == 'start_edas' else '等待触发')
        else:
            origin = self._fip_packet_time if self._fip_packet_time is not None else self._fip_trigger_wait_at
            failed = origin is not None and now - origin > fip_timeout
            self._dashboard_light('fip', 'red' if failed else 'yellow', '接收数据超时' if failed else ('等待触发：等待 FIP 首包' if self._fip_packet_time is None else '等待 FIP 数据'))
        self.record_das_storage(panel.storage_snapshot())
        app = self._runtime_app
        if app is not None and getattr(app, 'tab1_manager', None) is not None:
            self.update_thread_stats(app.tab1_manager.get_thread_stats())
        joint_worker = getattr(getattr(app, 'edas_manager', None), 'storage_worker', None)
        if joint_worker is not None:
            self.runtime_status.failures['joint'] = max(0, joint_worker.stats.get('save_failures', 0) + joint_worker.stats.get('requests_dropped', 0) - self._joint_failure_baseline)
        enabled = {'joint': self.tab3_joint_storage_toggle_btn.isChecked(),
                   'fip': self.phase_storage_check.isChecked(), 'das': panel.save_enable_check.isChecked()}
        all_paths = {'joint': self.tab3_storage_path_edit.text(), 'fip': self.storage_path_edit.text(), 'das': panel.save_path_edit.text()}
        disks = self.runtime_status.disk_space([all_paths[k] for k in enabled if enabled[k]] or [all_paths['joint']])
        self.runtime_labels['disk'].setText('；'.join(f'{drive} {free / 1024 ** 3:.1f} GiB' if free is not None else f'{drive} 无法查询' for drive, free, error in disks) or '未设置路径')
        self.runtime_labels['disk'].setToolTip('\n'.join(error for _, _, error in disks if error))
        files = self.runtime_status.files
        self.runtime_labels['files'].setText(f"{sum(files.values())}（联合 {files['joint']} / FIP {files['fip']} / DAS {files['das']}）")
        disk_bad = any(free is None or free <= 0 for _, free, _ in disks)
        storage_bad = (self._storage_error if enabled['joint'] else '') or any(self.runtime_status.failures.get(k, 0) for k in enabled if enabled[k])
        periods = {'joint': self.tab3_storage_interval_spin.value(), 'fip': self.storage_interval_spin.value(), 'das': panel.length_file_spin.value()}
        fresh = {key: key in self.runtime_status.last_writes and now - self.runtime_status.last_writes[key] < max(10., periods[key] * 3) for key in enabled if enabled[key]}
        if not any(enabled.values()): self._dashboard_light('storage', 'gray', '未启用')
        elif storage_bad or disk_bad: self._dashboard_light('storage', 'red', (self._storage_error if enabled['joint'] else '') or ('磁盘不可用或已满' if disk_bad else '存储线程异常'))
        elif fresh and all(fresh.values()):
            self._dashboard_light('storage', 'green', '文件保存正常')
        else: self._dashboard_light('storage', 'yellow', '等待数据 / 文件完成')
        for label in self.runtime_labels.values(): label.setWordWrap(True)

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
        self._set_tab_icons()

    def get_tab3_settings(self):
        settings = super().get_tab3_settings()
        settings['storage']['edas_enabled'] = False
        if hasattr(self, 'edas_panel'):
            enabled, spec = self.edas_panel._get_shared_filter_settings()
            settings['plot']['space_time_filter_enabled'] = enabled
            settings['plot']['space_time_filter_spec'] = spec
        return settings

    def _restore_tab3_settings(self, config):
        super()._restore_tab3_settings(config)
        self.tab3_edas_storage_toggle_btn.setChecked(False)

    def closeEvent(self, event):
        self._joint_start_pending = False
        self._joint_start_stage = None
        if self._joint_start_timer is not None: self._joint_start_timer.stop()
        if self._view_settings_dialog is not None: self._view_settings_dialog.reject()
        if self._storage_settings_dialog is not None: self._storage_settings_dialog.reject()
        if hasattr(self, '_dashboard_timer'): self._dashboard_timer.stop()
        # Controller cleanup drains alignment writers after the event loop exits.
        self.edas_panel.close()
        super().closeEvent(event)
