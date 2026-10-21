"""PCIe control, acquisition and plots hosted inside the wb-monitor eDAS tab."""
from collections import deque
from copy import deepcopy
from pathlib import Path
import time

import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QScrollArea, QSplitter, QGroupBox, QLabel, QComboBox, QCheckBox, QPushButton,
    QFormLayout, QSizePolicy)

from .main_window import MainWindow as HardwareWindow
from .acquisition_settings import AcquisitionSettingsDialog
from scipy import signal


class IntegratedEDASPanel(HardwareWindow):
    pipeline_fault = pyqtSignal(str)

    def __init__(self, simulation_mode=False, appearance_host=None):
        self.appearance_host = appearance_host
        self._settings_dialog = None
        self._applying_acquisition_settings = False
        self._monitor_history = deque(maxlen=10000)
        self._full_rate_wave_blocks = deque()
        super().__init__(simulation_mode=simulation_mode)
        self.setMinimumSize(0, 0)
        self.setWindowFlags(Qt.Widget)
        self.pipeline_fault.connect(self._stop_on_pipeline_fault)
        self.tcp_tab3_manager.error_occurred.connect(self._shared_fault)
        self.mode_space_radio.setChecked(True)
        self.mode_time_radio.hide()
        self.mode_space_radio.hide()
        self.tab3_comm_enable_check.setText('共享数据至 View / 联合存储')
        self.tab3_comm_enable_check.setChecked(self.params.comm.enabled if getattr(self, '_restored_local_params', False) else True)
        if not self.time_space_widget.is_plot_enabled():
            self.time_space_widget.plot_btn.click()
        self._on_source_changed()
        self.waveform_enable_check.hide()
        self.monitor_enable_check.hide()
        self.spectrum_enable_check.hide()
        self._refresh_parameter_summary()

    def _get_settings_path(self):
        return Path(__file__).resolve().parents[2] / 'config' / 'edas_last_params.json'

    def _load_local_params(self):
        self._restored_local_params = self._settings_path.is_file()
        super()._load_local_params()

    def _setup_ui(self):
        self.setWindowFlags(Qt.Widget)
        super()._setup_ui()
        old_central = self.centralWidget()
        central = QWidget()
        outer = QHBoxLayout(central)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(0)
        self.integrated_splitter = QSplitter(Qt.Horizontal)
        self.integrated_splitter.setChildrenCollapsible(False)
        self.integrated_splitter.setHandleWidth(6)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(8, 8, 8, 8)
        left_layout.setSpacing(8)
        control_group = QGroupBox('采集控制')
        control_layout = QVBoxLayout(control_group)
        self.acquisition_settings_btn = QPushButton('采集参数设置…')
        self.acquisition_settings_btn.clicked.connect(self.open_acquisition_settings)
        control_layout.addWidget(self.acquisition_settings_btn)
        buttons = QHBoxLayout()
        buttons.addWidget(self.start_btn)
        buttons.addWidget(self.stop_btn)
        self.start_btn.show()
        self.stop_btn.show()
        control_layout.addLayout(buttons)
        self.settings_state_label = QLabel('停止采集后可修改硬件和存储配置。')
        control_layout.addWidget(self.settings_state_label)
        left_layout.addWidget(control_group)
        self.summary_labels = {}
        for title, fields in (
            ('采集参数', (('采样与点数', 'acquisition'), ('上传数据', 'source'), ('硬件配置', 'hardware'), ('相位处理', 'phase'))),
            ('DAS 独立存储', (('存储路径', 'path'), ('文件分段', 'storage'), ('保存设置', 'format'))),
            ('共享与显示', (('共享参数', 'shared'), ('绘图参数', 'display'))),
        ):
            group = QGroupBox(title)
            form = QFormLayout(group)
            form.setContentsMargins(10, 16, 10, 10)
            form.setSpacing(8)
            for caption, key in fields:
                label = QLabel()
                label.setTextFormat(Qt.PlainText)
                label.setTextInteractionFlags(Qt.TextSelectableByMouse)
                label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
                label.setMinimumWidth(0)
                label.setWordWrap(True)
                self.summary_labels[key] = label
                form.addRow(caption, label)
            if title == 'DAS 独立存储':
                form.addRow(self.save_enable_check)
                self.save_enable_check.show()
            if title == '共享与显示':
                self.shared_stream_check = QCheckBox('共享数据至 View / 联合存储')
                self.shared_stream_check.toggled.connect(self.tab3_comm_enable_check.setChecked)
                self.tab3_comm_enable_check.toggled.connect(self.shared_stream_check.setChecked)
                form.addRow(self.shared_stream_check)
            left_layout.addWidget(group)
        left_layout.addStretch()
        self.parameter_scroll = QScrollArea()
        self.parameter_scroll.setWidgetResizable(True)
        self.parameter_scroll.setFrameShape(QScrollArea.NoFrame)
        self.parameter_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.parameter_scroll.setMinimumWidth(360)
        self.parameter_scroll.setMaximumWidth(720)
        self.parameter_scroll.setWidget(left)
        right = QWidget()
        right_layout = QVBoxLayout(right)
        controls = QHBoxLayout()
        controls.addWidget(QLabel('波形数据源'))
        self.wave_source_combo = QComboBox()
        self.wave_source_combo.addItems(['PHASE / RAW', 'Monitor（选定位置趋势）'])
        controls.addWidget(self.wave_source_combo)
        self.integrated_psd_check = QCheckBox('显示 PSD')
        self.integrated_psd_check.setChecked(True)
        controls.addWidget(self.integrated_psd_check)
        controls.addStretch()
        right_layout.addLayout(controls)
        # A single grid column and fixed left-axis width keep waveform, PSD and
        # time-space data rectangles at exactly the same horizontal positions.
        grid = QGridLayout()
        grid.addWidget(self.plot_widget_1, 0, 0)
        grid.addWidget(self.plot_widget_2, 1, 0)
        ts = self.time_space_widget
        grid.addWidget(ts.plot_widget, 2, 0)
        grid.addWidget(ts.histogram_widget, 2, 1)
        for plot in (self.plot_widget_1, self.plot_widget_2, ts.plot_widget):
            plot.setMinimumSize(0, 160)
            plot.getAxis('left').setWidth(70)
            plot.getAxis('right').setWidth(0)
        self.plot_widget_1.setXLink(ts.plot_widget)
        grid.setColumnStretch(0, 1)
        grid.setRowStretch(0, 2)
        grid.setRowStretch(1, 1)
        grid.setRowStretch(2, 3)
        right_layout.addLayout(grid, 1)
        self.integrated_splitter.addWidget(self.parameter_scroll)
        self.integrated_splitter.addWidget(right)
        self.integrated_splitter.setStretchFactor(0, 0)
        self.integrated_splitter.setStretchFactor(1, 1)
        self._parameter_width = 448
        self.integrated_splitter.setSizes([self._parameter_width, 1100])
        outer.addWidget(self.integrated_splitter)
        self.takeCentralWidget()
        self.setCentralWidget(central)
        self._unused_original_ui = old_central
        old_central.setParent(self)
        old_central.hide()
        self.wave_source_combo.currentIndexChanged.connect(self._on_source_changed)
        self.integrated_psd_check.toggled.connect(self._set_psd_visible)

    def acquisition_running(self):
        return self.acq_thread is not None and self.acq_thread.isRunning()

    def open_acquisition_settings(self):
        if self._settings_dialog is not None:
            self._settings_dialog.raise_()
            self._settings_dialog.activateWindow()
            return
        dialog = AcquisitionSettingsDialog(self, self.window())
        self._settings_dialog = dialog
        def finished(_result):
            self._settings_dialog = None
            self._refresh_parameter_summary()
            dialog.deleteLater()
        dialog.finished.connect(finished)
        dialog.open()

    def apply_acquisition_settings(self, params):
        if self.acquisition_running():
            raise RuntimeError('请停止采集后再修改参数')
        self._applying_acquisition_settings = True
        try:
            self.params = deepcopy(params)
            self._apply_params_to_ui(self.params)
            self.mode_space_radio.setChecked(True)
            self._sync_shared_filter_settings(reset_tab1=True)
            self._sync_tcp_tab3_availability()
            self._configure_display_timer(self.params)
        finally:
            self._applying_acquisition_settings = False
        self._save_local_params()
        self._refresh_parameter_summary()
        self.statusBar.showMessage('采集参数已保存，下次启动采集时生效', 5000)

    def _save_local_params(self):
        if not self._applying_acquisition_settings:
            super()._save_local_params()

    def _refresh_parameter_summary(self):
        if not hasattr(self, 'summary_labels') or not hasattr(self, '_point_num_label'):
            return
        params = self._collect_params()
        summary = {
            'acquisition': f'Scan {params.basic.scan_rate:g} Hz · Points {params.basic.point_num_per_scan}\nPulse {params.basic.pulse_width_ns:g} ns',
            'source': f'{self.data_source_combo.currentText()} · {params.upload.channel_num} 通道\nDataRate {self.data_rate_combo.currentText()}',
            'hardware': f'Clock {"Ext" if self.clk_external_radio.isChecked() else "Int"} · Trig {"In" if self.trig_in_radio.isChecked() else "Out"}\nBypass {params.basic.bypass_point_num} · Center {params.basic.center_freq_mhz:g} MHz',
            'phase': f'SpaceAvg {params.phase_demod.space_avg_order} · Merge {params.phase_demod.merge_point_num}\nRate2Phase {params.phase_demod.rate2phase} · PolarDiv {"开" if params.phase_demod.polarization_diversity else "关"}',
            'path': params.save.path or '未设置',
            'storage': f'每 {params.save.length_file_s:g} s 一个文件\n每 {params.save.length_save_s:g} s 一个保存包',
            'format': f'{self.storage_format_combo.currentText()} · Save DS {params.save.storage_downsample_factor}\nLoad {params.display.length_load_s:g} s · 满盘覆盖 {"开" if params.save.overwrite_oldest_when_full else "关"}',
            'shared': f'通道 {params.comm.channel_start}–{params.comm.channel_end} · 周期 {params.comm.length_comm_s:g} s\n时间/空间降采样 {params.comm.time_downsample}/{params.comm.space_downsample}',
            'display': f'Plot {params.display.length_plot_s:g} s · Region {params.display.region_index}\n时空时间/空间降采样 {params.time_space.time_downsample}/{params.time_space.space_downsample}',
        }
        for key, text in summary.items():
            self.summary_labels[key].setText(text)
            self.summary_labels[key].setToolTip(text)
            self.summary_labels[key].setWordWrap(True)
        self.shared_stream_check.setChecked(self.tab3_comm_enable_check.isChecked())
        self.settings_state_label.setText('采集中：配置只读；停止后可编辑。' if self.acquisition_running() else '停止采集后可修改硬件和存储配置。')
        self._apply_panel_appearance()

    def _apply_panel_appearance(self):
        if self.appearance_host is None:
            return
        self.setFont(self.appearance_host.font())
        self.start_btn.setText('采集中' if self.acquisition_running() else '启动采集')
        self.stop_btn.setText('停止采集')
        self.save_enable_check.setText('DAS 独立存储：' + ('ON' if self.save_enable_check.isChecked() else 'OFF'))
        for button, active in ((self.acquisition_settings_btn, False), (self.start_btn, False),
                               (self.stop_btn, True), (self.save_enable_check, self.save_enable_check.isChecked())):
            button.setMinimumWidth(0)
            button.setMaximumWidth(16777215)
            self.appearance_host._style_action_button(button, active, min_height=34, min_width=80, font_size=14)
        for label in self.summary_labels.values(): label.setWordWrap(True)

    def _on_tcp_tab3_error(self, message):
        # The legacy callback is still the shared manager's UI slot; its visible
        # wording must describe the local data stream rather than a TCP sender.
        self.tab3_comm_last_error_label.setText(message)
        self.statusBar.showMessage('数据共享：' + message, 15000)

    def set_parameter_width(self, width):
        self._parameter_width = max(360, min(int(width), 720))
        total = max(sum(self.integrated_splitter.sizes()), self._parameter_width + 400)
        self.integrated_splitter.setSizes([self._parameter_width, total - self._parameter_width])

    def showEvent(self, event):
        super().showEvent(event)
        width = self.appearance_host._current_param_panel_width() if self.appearance_host is not None else self._parameter_width
        self.set_parameter_width(width)

    def _set_psd_visible(self, visible):
        self.plot_widget_2.setVisible(visible)
        self.spectrum_enable_check.setChecked(visible)

    def _time_space_full_window_required(self):
        return self.time_space_widget is not None and self.time_space_widget.is_plot_enabled()

    def _update_phase_display(self, data, channel_num, phase_scale=None, snapshot_kind=0, display_meta=None):
        if snapshot_kind != 3:
            return super()._update_phase_display(data, channel_num, phase_scale, snapshot_kind, display_meta)
        # Let the original bounded rolling display consume the prepared block,
        # then derive waveform and PSD from that SAME time window.
        super()._update_phase_display(data, channel_num, phase_scale, snapshot_kind, display_meta)
        ts = self.time_space_widget
        if ts._valid_block_count == 1:
            self._full_rate_wave_blocks.clear()
        if display_meta is not None and len(display_meta) >= 5:
            full_rate = np.asarray(display_meta[4], dtype=np.float32)
            if phase_scale is not None:
                full_rate = full_rate * phase_scale
            if self._filter_enabled:
                full_rate = self._apply_tab1_phase_waveform_filter(full_rate, full_rate.size, 1, 1)
            self._full_rate_wave_blocks.append(full_rate)
        while len(self._full_rate_wave_blocks) > max(1, ts._valid_block_count):
            self._full_rate_wave_blocks.popleft()
        if ts._display_buffer is None or ts._valid_block_count <= 0:
            return
        columns = ts._valid_block_count * ts._display_block_width
        matrix = ts._display_buffer[:, :columns]
        if not matrix.size:
            return
        start, end = ts._current_distance_bounds
        position = int(self.region_index_spin.value())
        row = int(round((position - start) / max(1, end - start) * max(0, matrix.shape[0] - 1)))
        row = min(max(row, 0), matrix.shape[0] - 1)
        duration = ts._valid_block_count * ts._display_block_duration_s
        trace = (np.concatenate(list(self._full_rate_wave_blocks)) if self._full_rate_wave_blocks
                 else np.asarray(matrix[row], dtype=np.float32))
        x = np.arange(trace.size, dtype=float) * duration / max(1, trace.size)
        if self.wave_source_combo.currentIndex() == 0:
            step = max(1, trace.size // 5000)
            self.plot_curve_1[0].setData(x[::step], trace[::step])
            for curve in self.plot_curve_1[1:]: curve.setData([], [])
            if not self._plot_zoom_locked.get('plot1', False):
                self.plot_widget_1.enableAutoRange(axis='y', enable=True)
        if self.integrated_psd_check.isChecked() and self.wave_source_combo.currentIndex() == 0 and trace.size > 3:
            f, p = signal.welch(trace, fs=trace.size / max(duration, 1e-9), nperseg=min(2048, trace.size))
            self.spectrum_curve.setData(f, 10 * np.log10(p + np.finfo(float).tiny))

    def _on_source_changed(self, *_args):
        monitor = self.wave_source_combo.currentIndex() == 1
        self.monitor_enable_check.setChecked(monitor)
        self.waveform_enable_check.setChecked(not monitor)
        self._monitor_history.clear()
        self.plot_widget_1.setLabel('top', 'Monitor temporal trend' if monitor else 'Time domain')
        self.plot_widget_1.setLabel('bottom', 'Time', units='s')
        self.plot_widget_1.setXLink(self.time_space_widget.plot_widget)

    def _update_monitor_display(self, data, channel_num):
        super()._update_monitor_display(data, channel_num)
        if self.wave_source_combo.currentIndex() != 1:
            return
        index = min(max(0, int(self.params.display.region_index)), max(0, len(data) - 1)) if hasattr(self.params.display, 'region_index') else 0
        values = np.asarray(data[index]).reshape(-1)
        now = time.monotonic()
        self._monitor_history.append((now, values.copy()))
        ts = self.time_space_widget
        duration = max(0.1, ts._valid_block_count * ts._display_block_duration_s)
        while self._monitor_history and self._monitor_history[0][0] < now - duration:
            self._monitor_history.popleft()
        x = np.array([t - now + duration for t, _ in self._monitor_history])
        for ch, curve in enumerate(self.plot_curve_1):
            curve.setData(x, [v[ch] if ch < len(v) else np.nan for _, v in self._monitor_history])
        if self.integrated_psd_check.isChecked() and len(x) >= 4 and x[-1] > x[0]:
            monitor_trace = np.array([v[0] for _, v in self._monitor_history])
            f, power = signal.welch(monitor_trace, fs=(len(x) - 1) / (x[-1] - x[0]), nperseg=min(256, len(x)))
            self.spectrum_curve.setData(f, 10 * np.log10(power + np.finfo(float).tiny))

    def _handle_full_data_block(self, data, data_source, channel_num):
        saver = self.data_saver
        dropped_before = getattr(saver, 'dropped_blocks', 0) if saver else 0
        super()._handle_full_data_block(data, data_source, channel_num)
        if saver and getattr(saver, 'dropped_blocks', 0) > dropped_before:
            self.pipeline_fault.emit('DAS独立存储队列已满或写入失败；停止采集，检查磁盘吞吐/日志')

    def _shared_fault(self, message):
        if '尾部' not in message:
            self.pipeline_fault.emit(message)

    def _stop_on_pipeline_fault(self, message):
        self.statusBar.showMessage(message, 30000)
        if self.acq_thread is not None:
            self._on_stop()

    def _on_start(self):
        super()._on_start()
        self._on_source_changed()
        if self.acq_thread is not None:
            for control in self._shared_shape_controls(): control.setEnabled(False)
        self._refresh_parameter_summary()

    def _shared_shape_controls(self):
        return (self.tab3_channel_start_spin, self.tab3_channel_end_spin,
                self.tab3_time_downsample_spin, self.tab3_space_downsample_spin,
                self.tab3_length_comm_spin)

    def _on_stop(self):
        super()._on_stop()
        for control in self._shared_shape_controls(): control.setEnabled(True)
        self._refresh_parameter_summary()

    def _update_status(self):
        super()._update_status()
        self._refresh_parameter_summary()
        saver = self.data_saver
        if saver and getattr(saver, '_write_error', '') and self.acq_thread is not None:
            self.pipeline_fault.emit('DAS写盘故障：' + saver._write_error)
        elif saver and getattr(saver, 'dropped_blocks', 0) > 0 and self.acq_thread is not None:
            self.pipeline_fault.emit('DAS保存出现丢块，请检查磁盘、压缩性能和日志')
