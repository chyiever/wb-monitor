"""Draft editors for View parameters; the original controls stay model adapters."""
import re
from PyQt5.QtCore import Qt, QEvent
from PyQt5.QtWidgets import (QApplication, QDialog, QVBoxLayout, QFormLayout,
    QTabWidget, QScrollArea, QWidget, QLabel, QDialogButtonBox, QComboBox,
    QSpinBox, QDoubleSpinBox, QLineEdit, QCheckBox, QListView)
from edas_hardware.realtime_filter import parse_filter_spec
from edas_hardware.dialog_combo import DialogComboBox, contains_owned_combo_popup


VIEW_FIELDS = (
    ('FIP 绘图', (
        ('启用滤波', 'fip_filter_enable_check'), ('滤波参数 (Hz)', 'fip_filter_range_edit'),
        ('滤波阶数', 'filter_order_spin'), ('相位解缠 unwrap', 'fip_phase_unwrap_check'),
        ('时域显示降采样', 'downsample_spin'), ('FIP 处理目标', 'fip_plot_sensor_combo'))),
    ('DAS 绘图', (
        ('启用滤波', 'tab3_das_filter_enable_check'), ('滤波参数 (Hz)', 'tab3_das_filter_range_edit'),
        ('滤波阶数', 'tab3_das_filter_order_spin'))),
    ('显示与刷新', (
        ('时域窗口 (s)', 'time_display_duration_spin'), ('FIP 刷新 (s)', 'view_fip_refresh_spin'),
        ('eDAS 刷新 (s)', 'view_edas_refresh_spin'), ('单曲线最多点数', 'view_curve_max_points_spin'),
        ('时域图', 'time_plot_btn'), ('PSD 图', 'psd_plot_btn'),
        ('时空图', 'space_time_plot_btn'), ('允许刷新', 'tab3_plot_toggle_btn'))),
    ('PSD', (
        ('显示 PSD1', 'view_psd1_check'), ('显示 PSD2', 'view_psd2_check'),
        ('Welch 窗长 (s)', 'psd_window_length_spin'), ('重叠率 (%)', 'psd_overlap_spin'))),
    ('坐标轴', (
        ('手动范围（关闭即自动）', 'view_axis_enable_check'), ('X 范围', 'view_x_range_edit'),
        ('Y 范围', 'view_y_range_edit'), ('PSD Y 范围', 'view_psd_y_range_edit'))),
    ('时空图', (
        ('通道范围（含两端）', 'tab3_channel_range_edit'),
        ('总时间长度 (s)', 'tab3_space_time_total_seconds_spin'),
        ('单次平移 (s)', 'tab3_space_time_shift_seconds_spin'),
        ('时间降采样', 'tab3_time_downsample_spin'), ('空间降采样', 'tab3_space_downsample_spin'),
        ('配色', 'tab3_colormap_combo'), ('逐通道去基线', 'tab3_space_time_remove_baseline_check'),
        ('自动色阶', 'tab3_auto_levels_check'), ('V 范围', 'tab3_v_range_edit'))),
)


class ViewSettingsDialog(QDialog):
    fields = VIEW_FIELDS
    dialog_title = 'View 参数设置'

    def __init__(self, window):
        super().__init__(window)
        self.host = window
        self.setWindowTitle(self.dialog_title)
        self.setFont(window.font())
        self.resize(840, 640)
        self.editors = {}
        layout = QVBoxLayout(self)
        notice = QLabel('仅“应用并保存”生效；点击窗口外或取消将放弃修改。全局字体在 Setting 调整。')
        notice.setWordWrap(True)
        layout.addWidget(notice)
        self.tabs = QTabWidget()
        layout.addWidget(self.tabs, 1)
        for title, fields in self.fields:
            page = QWidget()
            form = QFormLayout(page)
            form.setContentsMargins(20, 20, 20, 20)
            form.setSpacing(14)
            for caption, name in fields:
                source = getattr(window, name)
                editor = self.clone_editor(source)
                editor.setToolTip(source.toolTip())
                editor.setMinimumHeight(32)
                editor.setObjectName('view_' + name)
                self.editors[name] = editor
                form.addRow(caption, editor)
            scroll = QScrollArea()
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QScrollArea.NoFrame)
            scroll.setWidget(page)
            self.tabs.addTab(scroll, title)
        self.error_label = QLabel()
        self.error_label.setStyleSheet('color: #b91c1c;')
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)
        self.buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        for button in self.buttons.buttons():
            button.setAutoDefault(False)
            window._style_secondary_button(button)
        self.buttons.button(QDialogButtonBox.Save).setText('应用并保存')
        self.buttons.button(QDialogButtonBox.Cancel).setText('取消')
        self.buttons.accepted.connect(self.apply_settings)
        self.buttons.rejected.connect(self.reject)
        layout.addWidget(self.buttons)
        if 'tab3_auto_levels_check' in self.editors:
            self.editors['tab3_auto_levels_check'].toggled.connect(
                lambda enabled: self.editors['tab3_v_range_edit'].setEnabled(not enabled))
            self.editors['tab3_v_range_edit'].setEnabled(not self.editors['tab3_auto_levels_check'].isChecked())

    @staticmethod
    def clone_editor(source):
        if isinstance(source, QComboBox):
            editor = DialogComboBox()
            editor.setView(QListView(editor))
            editor.setStyleSheet('QComboBox { combobox-popup: 0; }')
            for i in range(source.count()): editor.addItem(source.itemText(i), source.itemData(i))
            editor.setCurrentIndex(source.currentIndex())
            editor.setEnabled(source.isEnabled())
            editor.setMaximumWidth(360)
        elif isinstance(source, (QSpinBox, QDoubleSpinBox)):
            editor = QDoubleSpinBox() if isinstance(source, QDoubleSpinBox) else QSpinBox()
            if isinstance(editor, QDoubleSpinBox): editor.setDecimals(source.decimals())
            editor.setRange(source.minimum(), source.maximum())
            editor.setSingleStep(source.singleStep())
            editor.setSuffix(source.suffix())
            editor.setValue(source.value())
            editor.setMaximumWidth(220)
        elif isinstance(source, QLineEdit):
            editor = QLineEdit(source.text())
            editor.setPlaceholderText(source.placeholderText())
        else:
            editor = QCheckBox('启用')
            editor.setChecked(source.isChecked())
        return editor

    def validate(self):
        for enabled, name in (('fip_filter_enable_check', 'fip_filter_range_edit'),
                              ('tab3_das_filter_enable_check', 'tab3_das_filter_range_edit')):
            if self.editors[enabled].isChecked():
                try: parse_filter_spec(self.editors[name].text())
                except ValueError as exc: return f'滤波参数无效：{exc}'
        pairs = [('tab3_channel_range_edit', True)]
        if self.editors['view_axis_enable_check'].isChecked():
            pairs.extend((name, False) for name in ('view_x_range_edit', 'view_y_range_edit', 'view_psd_y_range_edit'))
        if not self.editors['tab3_auto_levels_check'].isChecked(): pairs.append(('tab3_v_range_edit', False))
        number = r'[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?'
        for name, channel in pairs:
            match = re.fullmatch(rf'\s*({number})\s*-\s*({number})\s*', self.editors[name].text())
            if not match: return '范围格式应为起点-终点，例如 0-199 或 -1-1。'
            low, high = map(float, match.groups())
            if channel:
                if low < 0 or high < low or not low.is_integer() or not high.is_integer():
                    return '通道范围须为非负整数，且终点不小于起点。'
            elif high <= low: return '范围终点必须大于起点。'
        return ''

    @staticmethod
    def value(editor):
        if isinstance(editor, QComboBox): return editor.currentIndex()
        if isinstance(editor, (QSpinBox, QDoubleSpinBox)): return editor.value()
        if isinstance(editor, QLineEdit): return editor.text()
        return editor.isChecked()

    def apply_settings(self):
        error = self.validate()
        if error:
            self.error_label.setText(error)
            return False
        changed = []
        for name, editor in self.editors.items():
            source = getattr(self.host, name)
            value = self.value(editor)
            if value == self.value(source): continue
            previous = source.blockSignals(True)
            try:
                if isinstance(editor, QComboBox): source.setCurrentIndex(value)
                elif isinstance(editor, (QSpinBox, QDoubleSpinBox)): source.setValue(value)
                elif isinstance(editor, QLineEdit): source.setText(value)
                else: source.setChecked(value)
            finally:
                source.blockSignals(previous)
            changed.append(source)
        # Notify existing processing/plot handlers only after all final values exist.
        for source in changed:
            if isinstance(source, QComboBox):
                source.currentIndexChanged.emit(source.currentIndex())
                source.currentTextChanged.emit(source.currentText())
            elif isinstance(source, (QSpinBox, QDoubleSpinBox)): source.valueChanged.emit(source.value())
            elif isinstance(source, QLineEdit):
                source.textChanged.emit(source.text())
                source.editingFinished.emit()
            else: source.toggled.emit(source.isChecked())
        if self.host.view_axis_enable_check.isChecked(): self.host._apply_view_axes()
        elif self.host.view_axis_enable_check in changed:
            self.host._view_user_range_active = False
            for plot in (self.host.tab3_curve1_plot, self.host.tab3_curve2_plot,
                         self.host.view_psd1_plot, self.host.view_psd2_plot,
                         self.host.tab3_space_time_plot):
                plot.enableAutoRange(x=True, y=True)
                plot.autoRange()
        self.host._save_configuration()
        self.accept()
        return True

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
                if watched is not self and not self.isAncestorOf(watched) and not self.frameGeometry().contains(event.globalPos()):
                    self.reject()
                    return True
            elif event.type() == QEvent.ApplicationDeactivate: self.reject()
        return super().eventFilter(watched, event)
