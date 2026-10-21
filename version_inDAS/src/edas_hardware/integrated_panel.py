"""PCIe control, acquisition and plots hosted inside the wb-monitor eDAS tab."""
from collections import deque
from copy import deepcopy
from pathlib import Path
import time
import re

import numpy as np
from PyQt5.QtCore import Qt, pyqtSignal, QTimer
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QScrollArea, QSplitter, QGroupBox, QLabel, QComboBox, QCheckBox, QPushButton,
    QFormLayout, QSizePolicy, QSpinBox)

from .main_window import MainWindow as HardwareWindow
from .acquisition_settings import AcquisitionSettingsDialog, format_fiber_distance
from .config import calculate_phase_point_num, resolve_phase_crop_bounds, DisplayMode, DataSource
from scipy import signal


class IntegratedEDASPanel(HardwareWindow):
    pipeline_fault = pyqtSignal(str)

    def __init__(self, simulation_mode=False, appearance_host=None):
        self.appearance_host = appearance_host
        self._settings_dialog = None
        self._applying_acquisition_settings = False
        self._monitor_history = deque(maxlen=10000)
        self._full_rate_wave_blocks = deque()
        self._plot_controls_ready = False
        self._plot_font_size = 12
        self._timespace_unit = 'points'
        self._timespace_origin = 0.0
        super().__init__(simulation_mode=simulation_mode)
        self.setMinimumSize(0, 0)
        self.pipeline_fault.connect(self._stop_on_pipeline_fault)
        self.tcp_tab3_manager.error_occurred.connect(self._shared_fault)
        self._plot_controls_ready = True
        self._sync_plot_controls(self.params)
        self.mode_time_radio.hide()
        self.mode_space_radio.hide()
        self.tab3_comm_enable_check.setText('共享数据至 View / 联合存储')
        self.tab3_comm_enable_check.setChecked(self.params.comm.enabled if getattr(self, '_restored_local_params', False) else True)
        if not self.time_space_widget.is_plot_enabled():
            self.time_space_widget.plot_btn.click()
        self._on_source_changed(persist=False)
        self.waveform_enable_check.hide()
        self.monitor_enable_check.hide()
        self.spectrum_enable_check.hide()
        self._refresh_parameter_summary()

    def _init_device(self):
        # A startup warning opens a nested event loop. Do not let it create a
        # native window for this panel before QTabWidget has embedded it: on
        # Windows that leaves the child controls with incorrect mouse targets.
        QTimer.singleShot(0, self._init_embedded_device)

    def _init_embedded_device(self):
        super()._init_device()

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
            ('采集参数', (('Scan', 'acquisition'), ('Fiber Length', 'fiber_length'), ('dx', 'dx'),
                         ('GL', 'gl'), ('Polardiv', 'polar'), ('Detrend / 绘图滤波', 'filter'), ('crop：', 'crop'))),
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
                if key == 'polar':
                    label.setFixedSize(14, 14)
                    label.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
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
        for label, source in [('Phase', 'phase'), ('RAW', 'raw'), ('Monitor', 'monitor')]:
            self.wave_source_combo.addItem(label, source)
        self.wave_source_combo.setToolTip('Phase/RAW 为采集卡不同上传数据，切换需先停止采集。Phase 和 Monitor 可在线切换。')
        controls.addWidget(self.wave_source_combo)
        self.integrated_psd_check = QCheckBox('显示 PSD')
        self.integrated_psd_check.setChecked(True)
        controls.addWidget(self.integrated_psd_check)
        self.phase_mode_combo = QComboBox()
        self.phase_mode_combo.addItem('TIME', int(DisplayMode.TIME))
        self.phase_mode_combo.addItem('SPACE', int(DisplayMode.SPACE))
        self.phase_mode_combo.setToolTip('TIME：每帧沿光纤的波形；SPACE：所选通道随时间变化，与时空图时间轴对齐。')
        controls.addWidget(self.phase_mode_combo)
        self.phase_channel_label = QLabel('CH')
        controls.addWidget(self.phase_channel_label)
        self.phase_channel_spin = QSpinBox()
        self.phase_channel_spin.setRange(0, 10000000)
        self.phase_channel_spin.setMaximumWidth(100)
        self.phase_channel_spin.setToolTip('SPACE 模式的通道号，以裁剪后第一个通道为 CH 0。')
        controls.addWidget(self.phase_channel_spin)
        controls.addStretch()
        right_layout.addLayout(controls)
        self.plot_source_notice = QLabel()
        self.plot_source_notice.setWordWrap(True)
        self.plot_source_notice.hide()
        right_layout.addWidget(self.plot_source_notice)
        # A single grid column and fixed left-axis width keep waveform, PSD and
        # time-space data rectangles at exactly the same horizontal positions.
        grid = QGridLayout()
        self.plot_grid = grid
        grid.addWidget(self.plot_widget_1, 0, 0)
        grid.addWidget(self.plot_widget_2, 1, 0)
        ts = self.time_space_widget
        ts.histogram_widget.setMinimumHeight(160)
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
        self.phase_mode_combo.currentIndexChanged.connect(self._on_phase_mode_selected)
        self.phase_channel_spin.valueChanged.connect(self._on_phase_channel_selected)

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
        dialog.show()

    def apply_acquisition_settings(self, params):
        if self.acquisition_running():
            raise RuntimeError('请停止采集后再修改参数')
        self._applying_acquisition_settings = True
        try:
            self.params = deepcopy(params)
            self._apply_params_to_ui(self.params)
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

    def _collect_params(self):
        params = super()._collect_params()
        params.display.tick_font_size = self._plot_font_size
        params.display.wave_source = self.wave_source_combo.currentData()
        params.time_space.axis_unit = self._timespace_unit
        params.time_space.axis_origin = self._timespace_origin
        return params

    def _apply_params_to_ui(self, params):
        self._plot_font_size = params.display.tick_font_size
        self._timespace_unit = params.time_space.axis_unit
        self._timespace_origin = params.time_space.axis_origin
        super()._apply_params_to_ui(params)
        self._sync_plot_controls(params)

    def _sync_plot_controls(self, params):
        source = ('monitor' if params.display.wave_source == 'monitor' else 'phase') if params.upload.data_source == DataSource.PHASE else 'raw'
        for control, value in ((self.wave_source_combo, source), (self.phase_mode_combo, int(params.display.mode))):
            control.blockSignals(True)
            control.setCurrentIndex(max(0, control.findData(value)))
            control.blockSignals(False)
        self.mode_space_radio.setChecked(params.display.mode == DisplayMode.SPACE)
        self.mode_time_radio.setChecked(params.display.mode != DisplayMode.SPACE)
        self.integrated_psd_check.blockSignals(True)
        self.integrated_psd_check.setChecked(params.display.spectrum_enable)
        self.integrated_psd_check.blockSignals(False)
        self._set_psd_visible(params.display.spectrum_enable, persist=False)
        self.phase_channel_spin.blockSignals(True)
        start, end = resolve_phase_crop_bounds(
            calculate_phase_point_num(params.basic.point_num_per_scan, params.phase_demod.merge_point_num),
            params.phase_demod.crop_distance_start, params.phase_demod.crop_distance_end)
        self.phase_channel_spin.setMaximum(max(0, end - start - 1))
        self.phase_channel_spin.setValue(params.display.region_index)
        self.phase_channel_spin.blockSignals(False)
        self.region_index_spin.setValue(self.phase_channel_spin.value())
        self._apply_extra_plot_settings(params)
        if self._plot_controls_ready:
            self._on_source_changed(persist=False)

    def _apply_extra_plot_settings(self, params=None):
        params = params or self.params
        self.time_space_widget.set_vertical_axis(self._timespace_unit,
            .4 * params.phase_demod.merge_point_num, self._timespace_origin)
        self._apply_plot_fonts()

    def _apply_plot_fonts(self):
        if self.appearance_host is not None and hasattr(self.appearance_host, 'setting_plot_font_spin'):
            self._plot_font_size = self.appearance_host.setting_plot_font_spin.value()
        font = QFont('Times New Roman', self._plot_font_size)
        for plot in (self.plot_widget_1, self.plot_widget_2, self.time_space_widget.plot_widget):
            for name in ('bottom', 'left', 'top', 'right'):
                axis = plot.getAxis(name)
                axis.setTickFont(font)
                axis.label.setFont(font)
                axis.labelStyle.update({'font-family': 'Times New Roman', 'font-size': f'{self._plot_font_size}pt'})
                axis.setLabel(re.sub(r'<[^>]+>', '', axis.labelText), units=axis.labelUnits, **axis.labelStyle)
            plot.getAxis('left').setWidth(max(90, self._plot_font_size * 7))
        self.time_space_widget.histogram_widget.item.axis.setTickFont(font)

    def _set_time_plot_bottom_label(self, label):
        size = self.appearance_host.setting_plot_font_spin.value() if self.appearance_host is not None and hasattr(self.appearance_host, 'setting_plot_font_spin') else self._plot_font_size
        self.plot_widget_1.setLabel('bottom', label,
            **{'font-family': 'Times New Roman', 'font-size': f'{size}pt'})

    def _update_spectrum(self, *args, **kwargs):
        super()._update_spectrum(*args, **kwargs)
        self._apply_plot_fonts()

    def _on_phase_mode_selected(self, *_args):
        if not self._plot_controls_ready:
            return
        space = self.phase_mode_combo.currentData() == DisplayMode.SPACE
        self.mode_space_radio.setChecked(space)
        self.mode_time_radio.setChecked(not space)
        self._full_rate_wave_blocks.clear()
        self._update_wave_axes()
        self._save_local_params()

    def _on_phase_channel_selected(self, value):
        if not self._plot_controls_ready:
            return
        self.region_index_spin.setValue(value)
        self._full_rate_wave_blocks.clear()
        self._monitor_history.clear()
        self._save_local_params()

    def _update_wave_axes(self):
        source = self.wave_source_combo.currentData()
        space = self.phase_mode_combo.currentData() == DisplayMode.SPACE
        temporal = source == 'monitor' or (source == 'phase' and space)
        self.phase_mode_combo.setEnabled(source == 'phase')
        self.phase_channel_spin.setEnabled(source == 'monitor' or (source == 'phase' and space))
        self.phase_channel_label.setEnabled(self.phase_channel_spin.isEnabled())
        self.plot_widget_1.setXLink(self.time_space_widget.plot_widget if temporal else None)
        if not self._plot_zoom_locked.get('plot1', False):
            self.plot_widget_1.enableAutoRange(axis='x', enable=not temporal)
        self.plot_widget_1.setLabel('bottom', 'Time (s)' if temporal else 'Distance (m)')
        self.plot_widget_1.setLabel('top', 'Monitor' if source == 'monitor' else ('Phase · ' + self.phase_mode_combo.currentText() if source == 'phase' else 'RAW'))
        self.plot_widget_1.setLabel('left', 'Phase (rad)' if source == 'phase' and self.rad_check.isChecked() else ('Phase (counts)' if source == 'phase' else 'Amplitude'))
        self.plot_widget_2.setLabel('bottom', 'Frequency (Hz)' if temporal or source == 'raw' else 'Spatial frequency (1/m)')

    def _refresh_parameter_summary(self):
        if not hasattr(self, 'summary_labels') or not hasattr(self, '_point_num_label'):
            return
        params = self._collect_params()
        dx = .4 * params.phase_demod.merge_point_num
        start, end = resolve_phase_crop_bounds(
            calculate_phase_point_num(params.basic.point_num_per_scan, params.phase_demod.merge_point_num),
            params.phase_demod.crop_distance_start, params.phase_demod.crop_distance_end)
        count = end - start
        crop = (f'CH {start}~CH {end - 1}（Total: {count}CH，{format_fiber_distance(count * dx)}）'
                if count else '无有效通道（Total: 0CH，0.0 m）')
        summary = {
            'acquisition': f'{params.basic.scan_rate:g} Hz',
            'fiber_length': format_fiber_distance(params.basic.point_num_per_scan * .4),
            'dx': f'{dx:.1f} m',
            'gl': f'{dx * params.phase_demod.diff_order:.1f} m',
            'polar': '',
            'filter': f'{params.phase_demod.detrend_bw:g} Hz；' +
                      (params.time_space.filter_spec + ' Hz' if params.time_space.filter_enabled else '关闭'),
            'crop': crop,
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
        polar = self.summary_labels['polar']
        enabled = params.phase_demod.polarization_diversity
        polar.setStyleSheet('background-color: ' + ('#22c55e' if enabled else '#ef4444') + '; border-radius: 7px;')
        polar.setToolTip('Polardiv：' + ('开启' if enabled else '关闭'))
        self.shared_stream_check.setChecked(self.tab3_comm_enable_check.isChecked())
        self.settings_state_label.setText('采集中：配置只读；停止后可编辑。' if self.acquisition_running() else '停止采集后可修改硬件和存储配置。')
        self._apply_panel_appearance()

    def _apply_panel_appearance(self):
        self.summary_labels['polar'].setFixedSize(14, 14)
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

    def _set_psd_visible(self, visible, persist=True):
        self.plot_widget_2.setVisible(visible)
        self.spectrum_enable_check.setChecked(visible)
        self.plot_grid.setRowStretch(1, 1 if visible else 0)
        self.plot_grid.setRowStretch(2, (3 if visible else 4) if self.wave_source_combo.currentData() != 'raw' else 0)
        self.plot_grid.invalidate()
        if persist and self._plot_controls_ready and not self._applying_acquisition_settings:
            self._save_local_params()

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
        trace_rate = self.params.basic.scan_rate if self._full_rate_wave_blocks else trace.size / max(duration, 1e-9)
        x = duration - trace.size / trace_rate + np.arange(trace.size, dtype=float) / trace_rate
        phase = self.wave_source_combo.currentData() == 'phase'
        space = self.phase_mode_combo.currentData() == DisplayMode.SPACE
        if phase and not space:
            if display_meta is not None and len(display_meta) >= 6 and not self._filter_enabled:
                profile = np.asarray(display_meta[5], dtype=np.float32)
                if phase_scale is not None:
                    profile = profile * phase_scale
                positions = np.arange(profile.size)
                profile_step = 1
            else:
                profile = matrix[:, -1]
                positions = start + np.arange(profile.size) * ts._space_downsample
                profile_step = ts._space_downsample
            dx = .4 * self.params.phase_demod.merge_point_num
            origin_m = self._timespace_origin * (1000 if self._timespace_unit == 'km' else 1) if self._timespace_unit != 'points' else 0
            step = max(1, profile.size // 5000)
            self.plot_curve_1[0].setData(positions[::step] * dx + origin_m, profile[::step])
            for curve in self.plot_curve_1[1:]: curve.setData([], [])
        elif phase:
            step = max(1, trace.size // 5000)
            self.plot_curve_1[0].setData(x[::step], trace[::step])
            for curve in self.plot_curve_1[1:]: curve.setData([], [])
            if not self._plot_zoom_locked.get('plot1', False):
                self.plot_widget_1.enableAutoRange(axis='y', enable=True)
        if self.integrated_psd_check.isChecked() and phase and trace.size > 3:
            spectrum_input = trace if space else profile
            spectrum_rate = trace_rate if space else 1 / (dx * profile_step)
            f, p = signal.welch(spectrum_input, fs=spectrum_rate, nperseg=min(2048, spectrum_input.size))
            self.spectrum_curve.setData(f, 10 * np.log10(p + np.finfo(float).tiny))

    def _on_source_changed(self, *_args, persist=True):
        if not self._plot_controls_ready:
            return
        source = self.wave_source_combo.currentData()
        desired = DataSource.raw if source == 'raw' else DataSource.PHASE
        current = self.data_source_combo.currentData()
        if (desired == DataSource.PHASE) != (current == DataSource.PHASE):
            if self.acquisition_running() or (source == 'raw' and self.channel_combo.currentData() == 4):
                self.wave_source_combo.blockSignals(True)
                previous = self.params.display.wave_source if current == DataSource.PHASE else 'raw'
                self.wave_source_combo.setCurrentIndex(self.wave_source_combo.findData(previous))
                self.wave_source_combo.blockSignals(False)
                self.plot_source_notice.setText('请先停止采集，再切换 Phase/RAW。' if self.acquisition_running() else 'RAW 不支持 4 通道，请先在采集参数中改为 1 或 2 通道。')
                self.plot_source_notice.show()
                return
            self._set_combo_to_data(self.data_source_combo, desired)
            self.params.upload.data_source = desired
        if source == 'phase':
            space = self.phase_mode_combo.currentData() == DisplayMode.SPACE
            self.mode_space_radio.setChecked(space)
            self.mode_time_radio.setChecked(not space)
        self.params.display.wave_source = source
        monitor = source == 'monitor'
        self.monitor_enable_check.setChecked(monitor)
        self.waveform_enable_check.setChecked(not monitor)
        self._monitor_history.clear()
        self._full_rate_wave_blocks.clear()
        for curve in self.plot_curve_1: curve.setData([], [])
        self.spectrum_curve.setData([], [])
        self.plot_source_notice.setText('RAW 采集不产生 PHASE 时空图；切换回 Phase 或 Monitor 后恢复。' if source == 'raw' else '')
        self.plot_source_notice.setVisible(source == 'raw')
        self.time_space_widget.histogram_widget.setVisible(source != 'raw')
        self.time_space_widget.plot_widget.setVisible(source != 'raw')
        self._set_psd_visible(self.integrated_psd_check.isChecked(), persist=False)
        self._update_wave_axes()
        if persist:
            self._save_local_params()

    def _update_raw_display(self, data, channel_num):
        if self.wave_source_combo.currentData() == 'raw':
            super()._update_raw_display(data, channel_num)

    def _on_mode_changed(self, checked):
        super()._on_mode_changed(checked)
        if checked and self._plot_controls_ready:
            self._update_wave_axes()

    def _update_monitor_display(self, data, channel_num):
        super()._update_monitor_display(data, channel_num)
        if self.wave_source_combo.currentData() != 'monitor':
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
