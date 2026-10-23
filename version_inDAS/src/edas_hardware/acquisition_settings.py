"""Transactional editors for the complete eDAS configuration.

The legacy widgets remain private model adapters. Every visible editor is a
fresh child of this dialog, never a widget moved out of a hidden QTabWidget.
"""
from copy import deepcopy

from PyQt5.QtCore import Qt, QEvent
from PyQt5.QtWidgets import (QDialog, QDialogButtonBox, QVBoxLayout, QFormLayout,
    QWidget, QScrollArea, QTabWidget, QLabel, QComboBox, QSpinBox, QDoubleSpinBox,
    QLineEdit, QCheckBox, QListView, QHBoxLayout, QPushButton, QFileDialog, QApplication)

from .config import ClockSource, TriggerDirection, DataSource, calculate_phase_point_num, calculate_cropped_point_count
from .realtime_filter import parse_filter_spec
from .dialog_combo import DialogComboBox, contains_owned_combo_popup


# label, configuration path, original editor (only its range/options are used)
SETTINGS_TABS = (
    ('基础采集', (
        ('扫描率 Scan (Hz)', 'basic.scan_rate', 'scan_rate_spin'),
        ('脉冲宽度 Pulse (ns)', 'basic.pulse_width_ns', 'pulse_width_spin'),
        ('每扫描点数 Points', 'basic.point_num_per_scan', 'point_num_spin'),
        ('上传通道 Channels', 'upload.channel_num', 'channel_combo'),
        ('数据源 Source', 'upload.data_source', 'data_source_combo'),
    )),
    ('硬件设置', (
        ('时钟 Clock', 'basic.clk_src', None),
        ('触发 Trig', 'basic.trig_dir', None),
        ('跳过点数 Bypass', 'basic.bypass_point_num', 'bypass_spin'),
        ('中心频率 (MHz)', 'basic.center_freq_mhz', 'center_freq_spin'),
        ('DataRate', 'upload.data_rate', 'data_rate_combo'),
        ('Rate2Phase', 'phase_demod.rate2phase', 'rate2phase_combo'),
        ('偏振分集 PolarDiv', 'phase_demod.polarization_diversity', 'polar_div_check'),
    )),
    ('相位处理', (
        ('空间平均 SpaceAvg', 'phase_demod.space_avg_order', 'space_avg_spin'),
        ('空间合并 Merge', 'phase_demod.merge_point_num', 'merge_points_spin'),
        ('差分阶数 DiffOrder', 'phase_demod.diff_order', 'diff_order_spin'),
        ('去趋势 Detrend (Hz)', 'phase_demod.detrend_bw', 'detrend_bw_spin'),
        ('裁剪起点 CropStart', 'phase_demod.crop_distance_start', 'crop_distance_start_spin'),
        ('裁剪终点 CropEnd（不含）', 'phase_demod.crop_distance_end', 'crop_distance_end_spin'),
    )),
    ('时长设置', (
        ('DLL 读取块 Length/Load (s)', 'display.length_load_s', 'length_load_spin'),
        ('显示窗口 Length/Plot (s)', 'display.length_plot_s', 'length_plot_spin'),
        ('保存包 Length/Save (s)', 'save.length_save_s', 'length_save_spin'),
        ('单文件 Length/File (s)', 'save.length_file_s', 'length_file_spin'),
        ('共享周期 Length/Comm (s)', 'comm.length_comm_s', 'tab3_length_comm_spin'),
    )),
    ('独立存储', (
        ('存储路径', 'save.path', 'save_path_edit'),
        ('文件格式', 'save.storage_format', 'storage_format_combo'),
        ('保存降采样 Save DS', 'save.storage_downsample_factor', 'save_downsample_spin'),
        ('满盘覆盖最旧文件', 'save.overwrite_oldest_when_full', 'overwrite_oldest_check'),
        ('Zstd 压缩级别', 'save.bz_zstd_level', 'bz_zstd_level_spin'),
        ('Bitshuffle 块长度', 'save.bz_bitshuffle_block_values', 'bz_bitshuffle_block_spin'),
        ('BZ 压缩线程数', 'save.bz_compression_workers', 'bz_compression_workers_spin'),
    )),
    ('绘图显示', (
        ('时空图纵轴', 'time_space.axis_unit', None),
        ('纵轴起点偏移 x0', 'time_space.axis_origin', None),
        ('波形位置 Region', 'display.region_index', 'region_index_spin'),
        ('相位显示为弧度 rad', 'display.rad_enable', 'rad_check'),
        ('显示滤波', 'time_space.filter_enabled', None),
        ('滤波参数 (Hz)', 'time_space.filter_spec', 'filter_spec_edit'),
        ('时空图空间起点', 'time_space.distance_range_start', 'ts.distance_start_spin'),
        ('时空图空间终点', 'time_space.distance_range_end', 'ts.distance_end_spin'),
        ('时空图窗口块数', 'time_space.window_frames', 'ts.window_frames_spin'),
        ('时空图时间降采样', 'time_space.time_downsample', 'ts.time_downsample_spin'),
        ('时空图空间降采样', 'time_space.space_downsample', 'ts.space_downsample_spin'),
        ('色标最小值', 'time_space.vmin', 'ts.vmin_spin'),
        ('色标最大值', 'time_space.vmax', 'ts.vmax_spin'),
        ('配色', 'time_space.colormap_type', 'ts.colormap_combo'),
    )),
    ('数据共享', (
        ('共享到 View / 联合存储', 'comm.enabled', 'tab3_comm_enable_check'),
        ('共享通道起点', 'comm.channel_start', 'tab3_channel_start_spin'),
        ('共享通道终点', 'comm.channel_end', 'tab3_channel_end_spin'),
        ('共享时间降采样', 'comm.time_downsample', 'tab3_time_downsample_spin'),
        ('共享空间降采样', 'comm.space_downsample', 'tab3_space_downsample_spin'),
    )),
)


def config_value(config, path):
    for name in path.split('.'):
        config = getattr(config, name)
    return config


def set_config_value(config, path, value):
    names = path.split('.')
    for name in names[:-1]:
        config = getattr(config, name)
    setattr(config, names[-1], value)


def format_fiber_distance(meters):
    return f'{meters / 1000:.2f} km' if meters > 10000 else f'{meters:.1f} m'


class AcquisitionSettingsDialog(QDialog):
    def __init__(self, panel, parent=None, section='all'):
        super().__init__(parent)
        self.panel = panel
        self.section = section
        self.setObjectName('edasAcquisitionSettings')
        self.setWindowTitle({'storage': 'DAS 独立存储参数', 'display': '共享与显示参数'}.get(section, 'eDAS 采集参数设置'))
        self.setWindowModality(Qt.NonModal)
        self.setFont(panel.font())
        self.resize(840, 600)
        self.setMinimumSize(740, 480)
        self.original_config = deepcopy(panel._collect_params())
        self.editors = {}
        self.derived_labels = {}
        layout = QVBoxLayout(self)
        self.notice = QLabel('仅点击“应用并保存”生效；点击窗口外或取消将关闭并放弃修改。硬件参数在下次启动采集时下发。')
        self.notice.setWordWrap(True)
        layout.addWidget(self.notice)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        for title, fields in SETTINGS_TABS:
            fields = tuple(field for field in fields if self._in_section(field[1]))
            if not fields: continue
            page = QWidget()
            form = QFormLayout(page)
            form.setContentsMargins(18, 18, 18, 18)
            form.setSpacing(12)
            form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)
            for label, path, original_attr in fields:
                original = (getattr(panel.time_space_widget, original_attr[3:]) if original_attr and original_attr.startswith('ts.')
                            else getattr(panel, original_attr) if original_attr else None)
                editor = self._make_editor(path, original, config_value(self.original_config, path))
                editor.setObjectName('edas_' + path.replace('.', '_'))
                editor.setMinimumHeight(30)
                if isinstance(editor, (QSpinBox, QDoubleSpinBox)):
                    editor.setMaximumWidth(220)
                elif isinstance(editor, QComboBox):
                    editor.setMaximumWidth(360)
                if original is not None:
                    editor.setToolTip(original.toolTip())
                self.editors[path] = editor
                if path == 'save.path':
                    holder = QWidget()
                    row = QHBoxLayout(holder)
                    row.setContentsMargins(0, 0, 0, 0)
                    row.addWidget(editor, 1)
                    browse = QPushButton('浏览…')
                    if panel.appearance_host is not None:
                        panel.appearance_host._style_secondary_button(browse)
                    browse.clicked.connect(self._browse_path)
                    row.addWidget(browse)
                    form.addRow(label, holder)
                elif path in ('basic.point_num_per_scan', 'phase_demod.merge_point_num', 'phase_demod.diff_order',
                              'phase_demod.crop_distance_start', 'phase_demod.crop_distance_end'):
                    editor.setFixedWidth(220)
                    holder = QWidget()
                    row = QHBoxLayout(holder)
                    row.setContentsMargins(0, 0, 0, 0)
                    row.addWidget(editor)
                    hint = QLabel()
                    self.derived_labels[path] = hint
                    row.addWidget(hint)
                    row.addStretch()
                    form.addRow(label, holder)
                else:
                    form.addRow(label, editor)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.NoFrame)
            scroll.setWidget(page)
            self.tabs.addTab(scroll, title)
        self.error_label = QLabel()
        self.error_label.setWordWrap(True)
        self.error_label.setStyleSheet('color: #b91c1c;')
        layout.addWidget(self.error_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.buttons.button(QDialogButtonBox.Save).setText('应用并保存')
        self.buttons.button(QDialogButtonBox.Cancel).setText('取消')
        self.buttons.button(QDialogButtonBox.Save).setAutoDefault(False)
        self.buttons.button(QDialogButtonBox.Cancel).setAutoDefault(False)
        if panel.appearance_host is not None:
            for button in self.buttons.buttons(): panel.appearance_host._style_secondary_button(button)
        self.buttons.accepted.connect(self.apply_settings)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        for path in ('basic.point_num_per_scan', 'phase_demod.merge_point_num', 'phase_demod.diff_order',
                     'phase_demod.crop_distance_start', 'phase_demod.crop_distance_end'):
            if path in self.editors: self.editors[path].valueChanged.connect(self._refresh_derived_labels)
        self._refresh_derived_labels()
        if 'time_space.axis_unit' in self.editors:
            self.editors['time_space.axis_unit'].currentIndexChanged.connect(self._update_origin_unit)
            self._update_origin_unit()
        if panel.acquisition_running():
            self.notice.setText('采集中：硬件/裁剪/读取块/共享周期只读；显示和共享范围可在线修改。独立保存进行时，请先关闭 SAVE 再修改保存格式与路径。')
            for path, editor in self.editors.items():
                if self._hardware_field(path) or (path.startswith('save.') and panel.data_saver is not None): editor.setEnabled(False)

    def _in_section(self, path):
        if self.section == 'all': return True
        if self.section == 'storage': return path.startswith('save.')
        if self.section == 'display':
            return path.startswith(('time_space.', 'comm.')) and path != 'comm.length_comm_s' or path.startswith('display.') and path != 'display.length_load_s'
        return self._hardware_field(path)

    @staticmethod
    def _hardware_field(path):
        return path.startswith(('basic.', 'upload.', 'phase_demod.')) or path in ('display.length_load_s', 'comm.length_comm_s')

    def _refresh_derived_labels(self):
        if 'basic.point_num_per_scan' not in self.editors: return
        points = self.editors['basic.point_num_per_scan'].value()
        dx = .4 * self.editors['phase_demod.merge_point_num'].value()
        gl = dx * self.editors['phase_demod.diff_order'].value()
        self.derived_labels['basic.point_num_per_scan'].setText('Fiber Length=' + format_fiber_distance(points * .4))
        self.derived_labels['phase_demod.merge_point_num'].setText(f'dx={dx:.1f} m')
        self.derived_labels['phase_demod.diff_order'].setText(f'GL={gl:.1f} m')
        total = calculate_phase_point_num(points, self.editors['phase_demod.merge_point_num'].value())
        count = calculate_cropped_point_count(total, self.editors['phase_demod.crop_distance_start'].value(),
                                             self.editors['phase_demod.crop_distance_end'].value())
        for path in ('phase_demod.crop_distance_start', 'phase_demod.crop_distance_end'):
            self.derived_labels[path].setText(f'原有 {total} CH → 裁剪后 {count} CH')

    def _update_origin_unit(self):
        unit = self.editors['time_space.axis_unit'].currentData()
        editor = self.editors['time_space.axis_origin']
        editor.setSuffix(' ' + unit if unit != 'points' else ' m / km')
        editor.setEnabled(unit != 'points')
        editor.setToolTip('x0 使用当前纵轴单位；Points 模式不应用偏移。距离=点号×dx+x0（km 模式中 dx 换算为 km）。')

    def showEvent(self, event):
        super().showEvent(event)
        QApplication.instance().installEventFilter(self)

    def hideEvent(self, event):
        QApplication.instance().removeEventFilter(self)
        super().hideEvent(event)

    def eventFilter(self, watched, event):
        if self.isVisible():
            if event.type() == QEvent.MouseButtonPress and isinstance(watched, QWidget):
                if contains_owned_combo_popup(self, event.globalPos()):
                    return super().eventFilter(watched, event)
                # Combo popups and owned file dialogs are part of this editor.
                if watched is not self and not self.isAncestorOf(watched):
                    if not self.frameGeometry().contains(event.globalPos()):
                        self.reject()
                        return True
            elif event.type() == QEvent.ApplicationDeactivate:
                self.reject()
        return super().eventFilter(watched, event)

    def _make_editor(self, path, original, value):
        if path == 'display.tick_font_size':
            editor = QSpinBox()
            editor.setRange(8, 28)
            editor.setValue(value)
            return editor
        if path == 'time_space.axis_unit':
            editor = DialogComboBox()
            editor.setView(QListView(editor))
            editor.setStyleSheet('QComboBox { combobox-popup: 0; }')
            for label, unit in [('Points', 'points'), ('Distance (m)', 'm'), ('Distance (km)', 'km')]:
                editor.addItem(label, unit)
            editor.setCurrentIndex(max(0, editor.findData(value)))
            return editor
        if path == 'time_space.axis_origin':
            editor = QDoubleSpinBox()
            editor.setDecimals(6)
            editor.setRange(-1e9, 1e9)
            editor.setValue(value)
            return editor
        if isinstance(original, QComboBox) or path in ('basic.clk_src', 'basic.trig_dir'):
            editor = DialogComboBox()
            editor.setView(QListView(editor))
            editor.setMaxVisibleItems(12)
            editor.setStyleSheet('QComboBox { combobox-popup: 0; }')
            if path == 'basic.clk_src':
                options = [('内部时钟', ClockSource.INTERNAL), ('外部时钟', ClockSource.EXTERNAL)]
            elif path == 'basic.trig_dir':
                options = [('输出触发', TriggerDirection.OUTPUT), ('输入触发', TriggerDirection.INPUT)]
            else:
                options = [(original.itemText(i), original.itemData(i)) for i in range(original.count())]
            for text, data in options: editor.addItem(text, data)
            index = editor.findData(value)
            if index < 0: index = editor.findText(str(value))
            if index >= 0: editor.setCurrentIndex(index)
        elif isinstance(original, QDoubleSpinBox):
            editor = QDoubleSpinBox()
            editor.setDecimals(original.decimals())
            editor.setRange(original.minimum(), original.maximum())
            editor.setSingleStep(original.singleStep())
            editor.setSuffix(original.suffix())
            editor.setValue(value)
        elif isinstance(original, QSpinBox):
            editor = QSpinBox()
            # Crop/Region bounds must permit a draft with a larger Points value
            # than the currently running configuration. Cross-field validation
            # happens before applying the draft.
            maximum = max(original.maximum(), 10000000) if 'crop_distance' in path or path == 'display.region_index' else original.maximum()
            editor.setRange(original.minimum(), maximum)
            editor.setSingleStep(original.singleStep())
            editor.setValue(value)
        elif isinstance(value, bool):
            editor = QCheckBox('启用')
            editor.setChecked(value)
        else:
            editor = QLineEdit(str(value))
        return editor

    def draft_config(self):
        config = deepcopy(self.panel._collect_params())
        for path, editor in self.editors.items():
            if isinstance(editor, QComboBox): value = editor.currentData()
            elif isinstance(editor, (QSpinBox, QDoubleSpinBox)): value = editor.value()
            elif isinstance(editor, QCheckBox): value = editor.isChecked()
            else: value = editor.text().strip()
            if value != config_value(self.original_config, path): set_config_value(config, path, value)
        self.panel._sync_length_frame_fields(config)
        return config

    def validate_draft(self, config, fip_period=None):
        valid, message = self.panel._validate_params(config)
        if not valid: return message
        if config.time_space.distance_range_end <= config.time_space.distance_range_start:
            return '时空图空间终点必须大于起点。'
        if config.time_space.vmax <= config.time_space.vmin:
            return '色标最大值必须大于最小值。'
        if config.save.enable and not config.save.path.strip():
            return '启用保存时需要设置存储路径。'
        if config.time_space.filter_enabled:
            try:
                spec = parse_filter_spec(config.time_space.filter_spec)
                if any(value >= config.basic.scan_rate / 2 for value in spec.cutoff_tuple):
                    return '滤波截止频率必须低于采样率的一半。'
            except (ValueError, TypeError) as exc: return f'滤波参数无效：{exc}'
        if config.comm.enabled and config.upload.channel_num == 1 and config.upload.data_source == DataSource.PHASE:
            width = calculate_cropped_point_count(calculate_phase_point_num(config.basic.point_num_per_scan,
                config.phase_demod.merge_point_num), config.phase_demod.crop_distance_start, config.phase_demod.crop_distance_end)
            if not 0 <= config.comm.channel_start <= config.comm.channel_end < width:
                return f'共享通道范围须在 0～{width - 1} 内。'
            expected = (self.panel.fip_period_provider() if hasattr(self.panel, 'fip_period_provider') else config.comm.length_comm_s) if fip_period is None else fip_period
            if abs(config.comm.length_comm_s - expected) > 1e-6:
                return f'共享周期必须与 FIP 包时长一致（当前 {expected:g} s）。'
        return ''

    def apply_settings(self):
        config = self.draft_config()
        error = self.panel.runtime_settings_error(config) or self.validate_draft(config)
        if error:
            self.error_label.setText(error)
            return False
        self.panel.apply_acquisition_settings(config)
        self.accept()
        return True

    def _browse_path(self):
        path = QFileDialog.getExistingDirectory(self, '选择 DAS 存储目录', self.editors['save.path'].text())
        if path: self.editors['save.path'].setText(path)
