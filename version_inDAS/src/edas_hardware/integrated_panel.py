"""PCIe control, acquisition and plots hosted inside the wb-monitor eDAS tab."""
from collections import deque
from pathlib import Path
import time

import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QScrollArea, QSplitter, QGroupBox, QLabel, QComboBox, QCheckBox)

from .main_window import MainWindow as HardwareWindow
from scipy import signal


class IntegratedEDASPanel(HardwareWindow):
    pipeline_fault = pyqtSignal(str)

    def __init__(self, simulation_mode=False):
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
        self.tab3_comm_enable_check.setChecked(True)
        if not self.time_space_widget.is_plot_enabled():
            self.time_space_widget.plot_btn.click()
        self._on_source_changed()
        self.waveform_enable_check.hide()
        self.monitor_enable_check.hide()
        self.spectrum_enable_check.hide()

    def _get_settings_path(self):
        return Path(__file__).resolve().parents[2] / 'config' / 'edas_last_params.json'

    def _setup_ui(self):
        super()._setup_ui()
        old_central = self.centralWidget()
        original_splitter = old_central.findChild(QSplitter)
        params_panel = original_splitter.widget(0)
        settings_page = self.plot_tabs.widget(3)
        central = QWidget()
        outer = QHBoxLayout(central)
        self.integrated_splitter = QSplitter(Qt.Horizontal)
        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.addWidget(params_panel)
        left_layout.addWidget(settings_page)
        shared = QGroupBox('进程内数据共享')
        shared_layout = QGridLayout(shared)
        shared_layout.addWidget(self.tab3_comm_enable_check, 0, 0, 1, 2)
        fields = [('通道起点', self.tab3_channel_start_spin), ('通道终点', self.tab3_channel_end_spin),
            ('时间降采样', self.tab3_time_downsample_spin), ('空间降采样', self.tab3_space_downsample_spin),
            ('对齐周期(s)', self.tab3_length_comm_spin)]
        for row, (text, control) in enumerate(fields, 1):
            shared_layout.addWidget(QLabel(text), row, 0)
            shared_layout.addWidget(control, row, 1)
        shared_layout.addWidget(self.tab3_comm_message_label, 6, 0, 1, 2)
        left_layout.addWidget(shared)
        left_layout.addStretch()
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setMinimumWidth(360)
        scroll.setWidget(left)
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
        # Keep Time-Space controls on the parameter side while its plot lives
        # in the shared grid. Original analysis and filtering callbacks remain.
        left_layout.insertWidget(2, ts)
        self.integrated_splitter.addWidget(scroll)
        self.integrated_splitter.addWidget(right)
        self.integrated_splitter.setSizes([420, 1100])
        outer.addWidget(self.integrated_splitter)
        self.takeCentralWidget()
        self.setCentralWidget(central)
        self._unused_original_ui = old_central
        old_central.hide()
        self.wave_source_combo.currentIndexChanged.connect(self._on_source_changed)
        self.integrated_psd_check.toggled.connect(self._set_psd_visible)

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

    def _shared_shape_controls(self):
        return (self.tab3_channel_start_spin, self.tab3_channel_end_spin,
                self.tab3_time_downsample_spin, self.tab3_space_downsample_spin,
                self.tab3_length_comm_spin)

    def _on_stop(self):
        super()._on_stop()
        for control in self._shared_shape_controls(): control.setEnabled(True)

    def _update_status(self):
        super()._update_status()
        saver = self.data_saver
        if saver and getattr(saver, '_write_error', '') and self.acq_thread is not None:
            self.pipeline_fault.emit('DAS写盘故障：' + saver._write_error)
        elif saver and getattr(saver, 'dropped_blocks', 0) > 0 and self.acq_thread is not None:
            self.pipeline_fault.emit('DAS保存出现丢块，请检查磁盘、压缩性能和日志')
