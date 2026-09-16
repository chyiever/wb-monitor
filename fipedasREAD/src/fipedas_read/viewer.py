"""PyQtGraph GUI for FIP/eDAS joint NPZ replay."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QFileDialog,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QSplitter,
    QStatusBar,
    QVBoxLayout,
    QWidget,
)

from .data_loader import (
    JointReplayData,
    build_space_time_matrix,
    concatenate_das_channel,
    concatenate_fip_frames,
    iter_joint_npz_files,
    load_joint_npz,
)
from .preprocess import (
    FilterSpec,
    PreprocessSpec,
    decimate_for_plot,
    parse_band_text,
    preprocess_waveform,
    robust_levels,
)


pg.setConfigOptions(antialias=False)
pg.setConfigOption("background", "w")
pg.setConfigOption("foreground", "k")


class ReplayWindow(QMainWindow):
    """Simple joint NPZ replay window."""

    COLOR_MAPS = ("Seismic", "Viridis", "Plasma", "Inferno", "Magma", "Gray", "Jet")
    COLOR_BAR_WIDTH = 100

    def __init__(self, initial_path: Path) -> None:
        super().__init__()
        self.setWindowTitle("FIP/eDAS Joint NPZ Replay")
        self.resize(1500, 900)
        self._data_dir = initial_path if initial_path.is_dir() else initial_path.parent
        self._initial_file = initial_path if initial_path.is_file() else None
        self._current_data: Optional[JointReplayData] = None
        self._syncing_x_range = False
        self._auto_range_pending = True

        font = QFont()
        font.setPointSize(9)
        self.setFont(font)
        self._build_ui()
        self._connect_signals()
        self._scan_files(select_file=self._initial_file)

    def _build_ui(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(8, 8, 8, 8)
        root.setSpacing(8)
        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        root.addWidget(splitter)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_plot_panel())
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setSizes([390, 1110])
        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar(self))

    def _build_left_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(330)
        panel.setMaximumWidth(520)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 8, 0)
        layout.setSpacing(8)
        layout.addWidget(self._build_path_group())
        layout.addWidget(self._build_file_group(), 1)
        layout.addWidget(self._build_source_group())
        layout.addWidget(self._build_fip_preprocess_group())
        layout.addWidget(self._build_edas_preprocess_group())
        layout.addWidget(self._build_space_group())
        return panel

    def _build_path_group(self) -> QGroupBox:
        group = QGroupBox("数据路径")
        layout = QGridLayout(group)
        layout.setColumnStretch(0, 1)
        self.path_edit = QLineEdit(str(self._data_dir))
        self.browse_btn = QPushButton("浏览")
        self.refresh_btn = QPushButton("刷新")
        layout.addWidget(self.path_edit, 0, 0, 1, 2)
        layout.addWidget(self.browse_btn, 1, 0)
        layout.addWidget(self.refresh_btn, 1, 1)
        return group

    def _build_file_group(self) -> QGroupBox:
        group = QGroupBox("文件名列表")
        layout = QVBoxLayout(group)
        self.file_list = QListWidget()
        self.file_list.setSelectionMode(QListWidget.SingleSelection)
        layout.addWidget(self.file_list)
        self.file_info_label = QLabel("未加载")
        self.file_info_label.setWordWrap(True)
        self.file_info_label.setStyleSheet("color: #455a64;")
        layout.addWidget(self.file_info_label)
        return group

    def _build_source_group(self) -> QGroupBox:
        group = QGroupBox("时域曲线")
        layout = QGridLayout(group)
        layout.setColumnStretch(1, 1)
        layout.addWidget(QLabel("波形1"), 0, 0)
        self.curve1_combo = QComboBox()
        self.curve1_combo.addItems(["FIP1", "FIP2", "DAS Channel"])
        self.curve1_combo.setCurrentText("FIP1")
        layout.addWidget(self.curve1_combo, 0, 1)
        layout.addWidget(QLabel("波形2"), 1, 0)
        self.curve2_combo = QComboBox()
        self.curve2_combo.addItems(["FIP1", "FIP2", "DAS Channel"])
        self.curve2_combo.setCurrentText("FIP2")
        layout.addWidget(self.curve2_combo, 1, 1)
        layout.addWidget(QLabel("DAS通道"), 2, 0)
        self.das_channel_spin = QSpinBox()
        self.das_channel_spin.setRange(0, 100000)
        self.das_channel_spin.setValue(10)
        layout.addWidget(self.das_channel_spin, 2, 1)
        return group

    def _build_fip_preprocess_group(self) -> QGroupBox:
        group = QGroupBox("FIP预处理")
        layout = QGridLayout(group)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(5)
        self.fip_remove_mean_check = QCheckBox("去均值")
        self.fip_remove_mean_check.setChecked(True)
        self.fip_normalize_check = QCheckBox("归一化")
        self.fip_filter_check = QCheckBox("滤波")
        layout.addWidget(self.fip_remove_mean_check, 0, 0)
        layout.addWidget(self.fip_normalize_check, 0, 1)
        layout.addWidget(self.fip_filter_check, 0, 2)

        layout.addWidget(QLabel("频带"), 1, 0)
        self.fip_filter_band_edit = QLineEdit("500-6000")
        self.fip_filter_band_edit.setPlaceholderText("500-6000 / 100- / -1000")
        self.fip_filter_band_edit.setMaximumWidth(120)
        layout.addWidget(self.fip_filter_band_edit, 1, 1)
        layout.addWidget(QLabel("阶数"), 1, 2)
        self.fip_filter_order_spin = QSpinBox()
        self.fip_filter_order_spin.setRange(1, 10)
        self.fip_filter_order_spin.setValue(4)
        self.fip_filter_order_spin.setMaximumWidth(70)
        layout.addWidget(self.fip_filter_order_spin, 1, 3)

        layout.addWidget(QLabel("降采样"), 2, 0)
        self.fip_downsample_spin = QSpinBox()
        self.fip_downsample_spin.setRange(1, 1000)
        self.fip_downsample_spin.setValue(1)
        self.fip_downsample_spin.setMaximumWidth(90)
        layout.addWidget(self.fip_downsample_spin, 2, 1)
        layout.addWidget(QLabel("最大点"), 2, 2)
        self.max_points_spin = QSpinBox()
        self.max_points_spin.setRange(1000, 500000)
        self.max_points_spin.setSingleStep(1000)
        self.max_points_spin.setValue(80000)
        self.max_points_spin.setMaximumWidth(110)
        layout.addWidget(self.max_points_spin, 2, 3)
        return group

    def _build_edas_preprocess_group(self) -> QGroupBox:
        group = QGroupBox("EDAS预处理")
        layout = QGridLayout(group)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(5)
        self.edas_remove_mean_check = QCheckBox("去均值")
        self.edas_remove_mean_check.setChecked(True)
        self.edas_normalize_check = QCheckBox("归一化")
        self.edas_filter_check = QCheckBox("滤波")
        layout.addWidget(self.edas_remove_mean_check, 0, 0)
        layout.addWidget(self.edas_normalize_check, 0, 1)
        layout.addWidget(self.edas_filter_check, 0, 2)

        layout.addWidget(QLabel("频带"), 1, 0)
        self.edas_filter_band_edit = QLineEdit("500-6000")
        self.edas_filter_band_edit.setPlaceholderText("500-6000 / 100- / -1000")
        self.edas_filter_band_edit.setMaximumWidth(120)
        layout.addWidget(self.edas_filter_band_edit, 1, 1)
        layout.addWidget(QLabel("阶数"), 1, 2)
        self.edas_filter_order_spin = QSpinBox()
        self.edas_filter_order_spin.setRange(1, 10)
        self.edas_filter_order_spin.setValue(4)
        self.edas_filter_order_spin.setMaximumWidth(70)
        layout.addWidget(self.edas_filter_order_spin, 1, 3)

        layout.addWidget(QLabel("降采样"), 2, 0)
        self.edas_downsample_spin = QSpinBox()
        self.edas_downsample_spin.setRange(1, 1000)
        self.edas_downsample_spin.setValue(1)
        self.edas_downsample_spin.setMaximumWidth(90)
        layout.addWidget(self.edas_downsample_spin, 2, 1)
        self.apply_btn = QPushButton("应用参数")
        self.reset_view_btn = QPushButton("重置视图")
        layout.addWidget(self.apply_btn, 2, 2)
        layout.addWidget(self.reset_view_btn, 2, 3)
        return group

    def _build_space_group(self) -> QGroupBox:
        group = QGroupBox("Timespace")
        layout = QGridLayout(group)
        layout.setHorizontalSpacing(6)
        layout.setVerticalSpacing(5)
        layout.addWidget(QLabel("通道范围"), 0, 0)
        self.channel_range_edit = QLineEdit("0-199")
        self.channel_range_edit.setMaximumWidth(120)
        layout.addWidget(self.channel_range_edit, 0, 1)
        layout.addWidget(QLabel("时间降采样"), 1, 0)
        self.space_time_downsample_spin = QSpinBox()
        self.space_time_downsample_spin.setRange(1, 1000)
        self.space_time_downsample_spin.setValue(1)
        self.space_time_downsample_spin.setMaximumWidth(90)
        layout.addWidget(self.space_time_downsample_spin, 1, 1)
        layout.addWidget(QLabel("空间降采样"), 1, 2)
        self.space_downsample_spin = QSpinBox()
        self.space_downsample_spin.setRange(1, 1000)
        self.space_downsample_spin.setValue(1)
        self.space_downsample_spin.setMaximumWidth(90)
        layout.addWidget(self.space_downsample_spin, 1, 3)
        layout.addWidget(QLabel("颜色"), 2, 0)
        self.colormap_combo = QComboBox()
        self.colormap_combo.addItems(self.COLOR_MAPS)
        self.colormap_combo.setCurrentText("Seismic")
        self.colormap_combo.setMaximumWidth(120)
        layout.addWidget(self.colormap_combo, 2, 1)
        self.space_baseline_check = QCheckBox("逐通道去基线")
        self.space_baseline_check.setChecked(True)
        layout.addWidget(self.space_baseline_check, 3, 0, 1, 2)
        self.auto_levels_check = QCheckBox("自动色阶")
        self.auto_levels_check.setChecked(True)
        layout.addWidget(self.auto_levels_check, 3, 2, 1, 2)
        return group

    def _build_plot_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.curve1_plot = self._new_plot("Waveform 1")
        self.curve1_plot.setLabel("bottom", "Time", units="s")
        self.curve1_plot.setLabel("left", "Amplitude")
        self.curve1 = self.curve1_plot.plot(pen=pg.mkPen("#006d77", width=1.4), name="Waveform 1")
        self.curve2_plot = self._new_plot("Waveform 2")
        self.curve2_plot.setLabel("bottom", "Time", units="s")
        self.curve2_plot.setLabel("left", "Amplitude")
        self.curve2 = self.curve2_plot.plot(pen=pg.mkPen("#c1121f", width=1.4), name="Waveform 2")
        self.space_plot = self._new_plot("DAS Timespace")
        self.space_plot.setLabel("bottom", "Time", units="s")
        self.space_plot.setLabel("left", "Channel")
        self.space_image = pg.ImageItem(axisOrder="row-major")
        self.space_plot.addItem(self.space_image)
        self.histogram = pg.HistogramLUTWidget(orientation="vertical", gradientPosition="right")
        self.histogram.setMinimumWidth(90)
        self.histogram.setMaximumWidth(120)
        self.histogram.setImageItem(self.space_image)

        layout.addWidget(self._plot_row(self.curve1_plot, add_colorbar_space=True), 1)
        layout.addWidget(self._plot_row(self.curve2_plot, add_colorbar_space=True), 1)
        space_row = QWidget()
        space_layout = QHBoxLayout(space_row)
        space_layout.setContentsMargins(0, 0, 0, 0)
        space_layout.setSpacing(6)
        space_layout.addWidget(self.space_plot, 1)
        self.histogram.setFixedWidth(self.COLOR_BAR_WIDTH)
        space_layout.addWidget(self.histogram, 0)
        layout.addWidget(space_row, 1)

        self._set_axis_widths()
        self._configure_image_colormap()
        return panel

    def _plot_row(self, plot: pg.PlotWidget, add_colorbar_space: bool) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(plot, 1)
        if add_colorbar_space:
            spacer = QWidget()
            spacer.setFixedWidth(self.COLOR_BAR_WIDTH)
            layout.addWidget(spacer, 0)
        return row

    def _new_plot(self, title: str) -> pg.PlotWidget:
        plot = pg.PlotWidget(title=title)
        plot.showGrid(x=True, y=True)
        plot.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        plot.getViewBox().setMouseEnabled(x=True, y=True)
        plot.getViewBox().setMouseMode(pg.ViewBox.RectMode)
        plot.setMenuEnabled(True)
        if hasattr(plot, "showButtons"):
            plot.showButtons()
        try:
            plot.getViewBox().sigXRangeChanged.connect(lambda vb, rng, p=plot: self._sync_x_range(p, rng))
        except Exception:
            pass
        return plot

    def _set_axis_widths(self) -> None:
        for plot in (self.curve1_plot, self.curve2_plot, self.space_plot):
            plot.getAxis("left").setWidth(82)

    def _configure_image_colormap(self) -> None:
        name = self.colormap_combo.currentText() if hasattr(self, "colormap_combo") else "Seismic"
        cmap = _build_colormap(name)
        self.space_image.setColorMap(cmap)
        self.histogram.gradient.setColorMap(cmap)

    def _connect_signals(self) -> None:
        self.browse_btn.clicked.connect(self._browse)
        self.refresh_btn.clicked.connect(lambda: self._scan_files())
        self.path_edit.returnPressed.connect(lambda: self._scan_files())
        self.file_list.currentItemChanged.connect(self._on_file_selected)
        self.apply_btn.clicked.connect(self._redraw_current)
        self.reset_view_btn.clicked.connect(self._reset_view)
        for widget in (
            self.curve1_combo,
            self.curve2_combo,
            self.das_channel_spin,
            self.fip_remove_mean_check,
            self.fip_normalize_check,
            self.fip_filter_check,
            self.fip_filter_order_spin,
            self.fip_downsample_spin,
            self.edas_remove_mean_check,
            self.edas_normalize_check,
            self.edas_filter_check,
            self.edas_filter_order_spin,
            self.edas_downsample_spin,
            self.max_points_spin,
            self.space_time_downsample_spin,
            self.space_downsample_spin,
            self.colormap_combo,
            self.space_baseline_check,
            self.auto_levels_check,
        ):
            signal = getattr(widget, "valueChanged", None) or getattr(widget, "currentTextChanged", None) or getattr(widget, "toggled", None)
            if signal is not None:
                signal.connect(lambda *_args: self._redraw_current())
        self.fip_filter_band_edit.returnPressed.connect(self._redraw_current)
        self.edas_filter_band_edit.returnPressed.connect(self._redraw_current)
        self.channel_range_edit.returnPressed.connect(self._redraw_current)

    def _browse(self) -> None:
        chosen = QFileDialog.getExistingDirectory(self, "选择 FIPeDAS joint NPZ 目录", self.path_edit.text())
        if chosen:
            self.path_edit.setText(chosen)
            self._scan_files()

    def _scan_files(self, select_file: Optional[Path] = None) -> None:
        path = Path(self.path_edit.text().strip() or ".")
        if path.is_file():
            self._data_dir = path.parent
            select_file = path
            self.path_edit.setText(str(self._data_dir))
        else:
            self._data_dir = path
        files = iter_joint_npz_files(select_file or self._data_dir)
        if select_file is not None and select_file.is_file():
            files = [select_file]
        self.file_list.blockSignals(True)
        self.file_list.clear()
        for file_path in files:
            item = QListWidgetItem(file_path.name)
            item.setData(Qt.UserRole, str(file_path))
            self.file_list.addItem(item)
            if select_file is not None and file_path.resolve() == select_file.resolve():
                self.file_list.setCurrentItem(item)
        if self.file_list.count() and self.file_list.currentRow() < 0:
            self.file_list.setCurrentRow(self.file_list.count() - 1)
        current = self.file_list.currentItem()
        self.file_list.blockSignals(False)
        self.statusBar().showMessage(f"发现 {self.file_list.count()} 个 npz 文件")
        if current is not None:
            self._load_file(Path(current.data(Qt.UserRole)))
        else:
            self._current_data = None
            self.file_info_label.setText("未找到 .npz 文件")
            self._clear_plots()

    def _on_file_selected(self, current: Optional[QListWidgetItem], _previous: Optional[QListWidgetItem]) -> None:
        if current is None:
            return
        self._load_file(Path(current.data(Qt.UserRole)))

    def _load_file(self, path: Path) -> None:
        try:
            self._current_data = load_joint_npz(path)
        except Exception as exc:
            QMessageBox.critical(self, "读取失败", f"{path}\n\n{exc}")
            self._current_data = None
            self._clear_plots()
            return
        self._auto_range_pending = True
        self._update_file_info()
        self._redraw_current()

    def _update_file_info(self) -> None:
        data = self._current_data
        if data is None:
            self.file_info_label.setText("未加载")
            return
        fip_rate = _median_text(data.fip_rates_hz, "Hz")
        das_rate = _median_text(data.das_rates_hz, "Hz")
        das_channels = int(np.nanmax(data.das_channel_counts)) if data.das_channel_counts.size else 0
        self.file_info_label.setText(
            f"{data.format_version}\n"
            f"frames={data.frame_count}, time={data.start_time:.3f}-{data.end_time:.3f}s\n"
            f"FIP rate={fip_rate}, DAS rate={das_rate}, channels={das_channels}"
        )
        self.statusBar().showMessage(f"已加载 {data.path.name}")

    def _redraw_current(self) -> None:
        data = self._current_data
        if data is None:
            return
        try:
            self._configure_image_colormap()
            self._draw_curve(1, self.curve1_combo.currentText())
            self._draw_curve(2, self.curve2_combo.currentText())
            self._draw_space_time()
            if self._auto_range_pending:
                self._reset_view()
                self._auto_range_pending = False
        except Exception as exc:
            QMessageBox.warning(self, "绘图失败", str(exc))

    def _preprocess_spec(self, source: str) -> PreprocessSpec:
        is_fip = source in ("FIP1", "FIP2")
        if is_fip:
            low, high = parse_band_text(self.fip_filter_band_edit.text())
            return PreprocessSpec(
                remove_mean=self.fip_remove_mean_check.isChecked(),
                normalize=self.fip_normalize_check.isChecked(),
                downsample=self.fip_downsample_spin.value(),
                filter_spec=FilterSpec(
                    enabled=self.fip_filter_check.isChecked(),
                    low_hz=low,
                    high_hz=high,
                    order=self.fip_filter_order_spin.value(),
                ),
            )
        low, high = parse_band_text(self.edas_filter_band_edit.text())
        return PreprocessSpec(
            remove_mean=self.edas_remove_mean_check.isChecked(),
            normalize=self.edas_normalize_check.isChecked(),
            downsample=self.edas_downsample_spin.value(),
            filter_spec=FilterSpec(
                enabled=self.edas_filter_check.isChecked(),
                low_hz=low,
                high_hz=high,
                order=self.edas_filter_order_spin.value(),
            ),
        )

    def _draw_curve(self, curve_index: int, source: str) -> None:
        data = self._current_data
        if data is None:
            return
        if source == "FIP1":
            raw_t, raw_y, rate = concatenate_fip_frames(
                data.fip1_frames, data.packet_start_times, data.packet_duration_seconds, data.fip_rates_hz
            )
        elif source == "FIP2":
            raw_t, raw_y, rate = concatenate_fip_frames(
                data.fip2_frames, data.packet_start_times, data.packet_duration_seconds, data.fip_rates_hz
            )
        else:
            raw_t, raw_y, rate = concatenate_das_channel(
                data.das_frames,
                data.packet_start_times,
                data.packet_duration_seconds,
                data.das_rates_hz,
                self.das_channel_spin.value(),
            )
        spec = self._preprocess_spec(source)
        rel_t, y, effective_rate = preprocess_waveform(raw_y, rate, spec)
        if raw_t.size == raw_y.size and y.size:
            step = max(1, int(spec.downsample))
            times = raw_t[::step][: y.size]
        elif raw_t.size and rel_t.size:
            t0 = float(raw_t[0])
            times = t0 + np.arange(y.size, dtype=np.float64) / effective_rate
        else:
            times = rel_t
        plot_times, plot_values = decimate_for_plot(times, y, self.max_points_spin.value())
        curve = self.curve1 if curve_index == 1 else self.curve2
        plot = self.curve1_plot if curve_index == 1 else self.curve2_plot
        curve.setData(plot_times, plot_values)
        plot.setTitle(f"Waveform {curve_index}: {source}")

    def _draw_space_time(self) -> None:
        data = self._current_data
        if data is None:
            return
        ch0, ch1 = _parse_range(self.channel_range_edit.text(), 0, 199)
        matrix, rect = build_space_time_matrix(
            frames=data.das_frames,
            starts=data.packet_start_times,
            durations=data.packet_duration_seconds,
            rates=data.das_rates_hz,
            channel_start=ch0,
            channel_end=ch1,
            time_downsample=self.space_time_downsample_spin.value(),
            space_downsample=self.space_downsample_spin.value(),
            remove_baseline=self.space_baseline_check.isChecked(),
        )
        if matrix.size == 0:
            matrix = np.zeros((1, 1), dtype=np.float32)
            rect = (0.0, float(ch0), 1.0, 1.0)
        levels = robust_levels(matrix) if self.auto_levels_check.isChecked() else self.histogram.getLevels()
        self.space_image.setImage(matrix, autoLevels=False, levels=levels)
        self.space_image.setRect(*rect)
        self.histogram.setLevels(*levels)
        self.space_plot.setTitle("DAS Timespace")

    def _clear_plots(self) -> None:
        self.curve1.setData([], [])
        self.curve2.setData([], [])
        self.space_image.setImage(np.zeros((1, 1), dtype=np.float32), autoLevels=False, levels=(-1, 1))
        self.space_image.setRect(0.0, 0.0, 1.0, 1.0)

    def _reset_view(self) -> None:
        for plot in (self.curve1_plot, self.curve2_plot, self.space_plot):
            plot.enableAutoRange(axis=pg.ViewBox.XYAxes, enable=True)
            plot.autoRange()
        self._sync_common_initial_x_range()

    def _sync_common_initial_x_range(self) -> None:
        ranges = []
        for plot in (self.curve1_plot, self.curve2_plot, self.space_plot):
            try:
                x0, x1 = plot.getViewBox().viewRange()[0]
                if np.isfinite(x0) and np.isfinite(x1) and x1 > x0:
                    ranges.append((x0, x1))
            except Exception:
                pass
        if not ranges:
            return
        x0 = min(pair[0] for pair in ranges)
        x1 = max(pair[1] for pair in ranges)
        self._apply_x_range(None, (x0, x1), padding=0.0)

    def _sync_x_range(self, source_plot: pg.PlotWidget, x_range: tuple[float, float]) -> None:
        if self._syncing_x_range:
            return
        self._apply_x_range(source_plot, x_range, padding=0.0)

    def _apply_x_range(
        self,
        source_plot: Optional[pg.PlotWidget],
        x_range: tuple[float, float],
        padding: float,
    ) -> None:
        x0, x1 = float(x_range[0]), float(x_range[1])
        if not np.isfinite(x0) or not np.isfinite(x1) or x1 <= x0:
            return
        self._syncing_x_range = True
        try:
            for plot in (self.curve1_plot, self.curve2_plot, self.space_plot):
                if plot is source_plot:
                    continue
                plot.setXRange(x0, x1, padding=padding)
        finally:
            self._syncing_x_range = False


def _parse_range(text: str, default_start: int, default_end: int) -> tuple[int, int]:
    try:
        if "-" not in text:
            value = int(text.strip())
            return value, value
        left, right = text.split("-", 1)
        start = int(left.strip()) if left.strip() else default_start
        end = int(right.strip()) if right.strip() else default_end
        if end < start:
            start, end = end, start
        return start, end
    except Exception:
        return default_start, default_end


def _build_colormap(name: str) -> pg.ColorMap:
    normalized = (name or "Seismic").strip().lower()
    palettes = {
        "seismic": [
            [0, 0, 128],
            [0, 0, 255],
            [255, 255, 255],
            [255, 0, 0],
            [128, 0, 0],
        ],
        "viridis": [
            [68, 1, 84],
            [59, 82, 139],
            [33, 145, 140],
            [94, 201, 98],
            [253, 231, 37],
        ],
        "plasma": [
            [13, 8, 135],
            [126, 3, 168],
            [203, 71, 119],
            [248, 149, 64],
            [240, 249, 33],
        ],
        "inferno": [
            [0, 0, 4],
            [87, 15, 109],
            [187, 55, 84],
            [249, 142, 8],
            [252, 255, 164],
        ],
        "magma": [
            [0, 0, 4],
            [80, 18, 123],
            [182, 54, 121],
            [251, 136, 97],
            [252, 253, 191],
        ],
        "gray": [
            [0, 0, 0],
            [255, 255, 255],
        ],
        "jet": [
            [0, 0, 131],
            [0, 60, 170],
            [5, 255, 255],
            [255, 255, 0],
            [250, 0, 0],
            [128, 0, 0],
        ],
    }
    colors = np.asarray(palettes.get(normalized, palettes["seismic"]), dtype=np.ubyte)
    return pg.ColorMap(np.linspace(0.0, 1.0, len(colors)), colors)


def _median_text(values: np.ndarray, suffix: str) -> str:
    finite = values[np.isfinite(values) & (values > 0)] if values.size else np.array([])
    if finite.size == 0:
        return "n/a"
    value = float(np.nanmedian(finite))
    if value >= 1_000_000:
        return f"{value / 1_000_000:.3f} M{suffix}"
    if value >= 1_000:
        return f"{value / 1_000:.3f} k{suffix}"
    return f"{value:.3f} {suffix}"
