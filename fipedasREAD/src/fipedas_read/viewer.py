"""PyQtGraph GUI for FIP/eDAS joint NPZ replay."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import numpy as np
import pyqtgraph as pg
from PyQt5.QtCore import Qt, QThread, QTimer
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

from .data_loader import iter_joint_npz_files
from .preprocess import (
    FilterSpec,
    PreprocessSpec,
    parse_band_text,
)
from .worker import (
    CurveRequest,
    RedrawRequest,
    RedrawResult,
    ReplayWorker,
    SpaceRequest,
)


pg.setConfigOptions(antialias=False)
pg.setConfigOption("background", "w")
pg.setConfigOption("foreground", "k")


class ReplayWindow(QMainWindow):
    """Simple joint NPZ replay window."""

    COLOR_MAPS = ("Seismic", "RdBu", "CoolWarm", "Viridis", "Plasma", "Inferno", "Magma", "Gray", "Jet")
    COLOR_BAR_WIDTH = 100
    REDRAW_DEBOUNCE_MS = 120
    CURVE1_COLOR = "#0072B2"
    CURVE2_COLOR = "#D55E00"
    APP_STYLE = """
        QGroupBox {
            border: 1px solid #cfd8dc;
            border-radius: 6px;
            margin-top: 14px;
            padding-top: 2px;
            font-weight: 600;
            background: #f7f9fa;
        }
        QGroupBox::title {
            subcontrol-origin: margin;
            left: 10px;
            padding: 0 5px;
            color: #37474f;
        }
        QPushButton {
            background: #eef2f5;
            border: 1px solid #b0bec5;
            border-radius: 4px;
            padding: 4px 10px;
        }
        QPushButton:hover { background: #e0e8ed; }
        QPushButton:pressed { background: #d0dbe3; }
        QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {
            border: 1px solid #b0bec5;
            border-radius: 4px;
            padding: 2px 4px;
            background: #ffffff;
        }
        QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus, QComboBox:focus {
            border: 1px solid #0072b2;
        }
        QListWidget {
            border: 1px solid #cfd8dc;
            border-radius: 6px;
            background: #ffffff;
            outline: none;
        }
        QListWidget::item { padding: 2px 4px; }
        QListWidget::item:selected {
            background: #d6e9fb;
            color: #0b3d6e;
        }
        QStatusBar { background: #eceff1; }
        QStatusBar::item { border: none; }
    """

    def __init__(self, initial_path: Path) -> None:
        super().__init__()
        self.setWindowTitle("FIP/eDAS Joint NPZ Replay")
        self.resize(1600, 960)
        self.setStyleSheet(self.APP_STYLE)
        self._data_dir = initial_path if initial_path.is_dir() else initial_path.parent
        self._initial_file = initial_path if initial_path.is_file() else None
        self._syncing_x_range = False
        self._auto_range_pending = True
        self._worker_seq = 0
        self._pending_request: Optional[RedrawRequest] = None
        self._current_path: Optional[Path] = None

        self._redraw_timer = QTimer(self)
        self._redraw_timer.setSingleShot(True)
        self._redraw_timer.setInterval(self.REDRAW_DEBOUNCE_MS)
        self._redraw_timer.timeout.connect(self._flush_requests)

        font = QFont()
        font.setPointSize(9)
        self.setFont(font)
        self._build_ui()
        self._connect_signals()
        self._start_worker()
        self._scan_files(select_file=self._initial_file)

    def _start_worker(self) -> None:
        self._worker_thread = QThread(self)
        self._worker_thread.setObjectName("replay-worker")
        self._worker = ReplayWorker()
        self._worker.moveToThread(self._worker_thread)
        self._worker.redrawDone.connect(self._on_redraw_done)
        self._worker.failed.connect(self._on_worker_failed)
        self._worker_thread.start()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._redraw_timer.stop()
        self._worker_thread.quit()
        self._worker_thread.wait(3000)
        super().closeEvent(event)

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
        splitter.setSizes([410, 1190])
        self.setCentralWidget(central)
        self.setStatusBar(QStatusBar(self))

    def _update_manual_levels_state(self) -> None:
        auto = self.auto_levels_check.isChecked()
        self.space_vmin_spin.setEnabled(not auto)
        self.space_vmax_spin.setEnabled(not auto)

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
        self.auto_levels_check.setToolTip("勾选时自动按 2%~98% 百分位计算色阶；取消后可手动设置 Vmin/Vmax")
        layout.addWidget(self.auto_levels_check, 3, 2, 1, 2)
        layout.addWidget(QLabel("Vmin"), 4, 0)
        self.space_vmin_spin = QDoubleSpinBox()
        self.space_vmin_spin.setRange(-1e12, 1e12)
        self.space_vmin_spin.setDecimals(3)
        self.space_vmin_spin.setValue(-1.0)
        self.space_vmin_spin.setMaximumWidth(120)
        self.space_vmin_spin.setToolTip("手动色阶下限（需取消自动色阶）")
        layout.addWidget(self.space_vmin_spin, 4, 1)
        layout.addWidget(QLabel("Vmax"), 4, 2)
        self.space_vmax_spin = QDoubleSpinBox()
        self.space_vmax_spin.setRange(-1e12, 1e12)
        self.space_vmax_spin.setDecimals(3)
        self.space_vmax_spin.setValue(1.0)
        self.space_vmax_spin.setMaximumWidth(120)
        self.space_vmax_spin.setToolTip("手动色阶上限（需取消自动色阶）")
        layout.addWidget(self.space_vmax_spin, 4, 3)
        self._update_manual_levels_state()
        return group

    def _build_plot_panel(self) -> QWidget:
        panel = QWidget()
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        self.curve1_plot = self._new_plot("Waveform 1")
        self.curve1_plot.setLabel("bottom", "Time", units="s")
        self.curve1_plot.setLabel("left", "Amplitude")
        self.curve1 = self.curve1_plot.plot(pen=pg.mkPen(self.CURVE1_COLOR, width=1.6), name="Waveform 1")
        self.curve2_plot = self._new_plot("Waveform 2")
        self.curve2_plot.setLabel("bottom", "Time", units="s")
        self.curve2_plot.setLabel("left", "Amplitude")
        self.curve2 = self.curve2_plot.plot(pen=pg.mkPen(self.CURVE2_COLOR, width=1.6), name="Waveform 2")
        self.space_plot = self._new_plot("DAS Timespace")
        self.space_plot.setLabel("bottom", "Time", units="s")
        self.space_plot.setLabel("left", "Channel")
        self.space_image = pg.ImageItem(axisOrder="row-major")
        self.space_plot.addItem(self.space_image)
        self.histogram = pg.HistogramLUTWidget(orientation="vertical", gradientPosition="right")
        self.histogram.setMinimumWidth(90)
        self.histogram.setMaximumWidth(120)
        self.histogram.setImageItem(self.space_image)

        self.plot_height_splitter = QSplitter(Qt.Vertical)
        self.plot_height_splitter.setChildrenCollapsible(False)
        self.plot_height_splitter.setHandleWidth(7)
        layout.addWidget(self.plot_height_splitter, 1)

        curve1_row = self._plot_row(self.curve1_plot, add_colorbar_space=True)
        curve2_row = self._plot_row(self.curve2_plot, add_colorbar_space=True)
        curve1_row.setMinimumHeight(120)
        curve2_row.setMinimumHeight(120)
        space_row = QWidget()
        space_row.setMinimumHeight(180)
        space_layout = QHBoxLayout(space_row)
        space_layout.setContentsMargins(0, 0, 0, 0)
        space_layout.setSpacing(6)
        space_layout.addWidget(self.space_plot, 1)
        self.histogram.setFixedWidth(self.COLOR_BAR_WIDTH)
        space_layout.addWidget(self.histogram, 0)

        self.plot_height_splitter.addWidget(curve1_row)
        self.plot_height_splitter.addWidget(curve2_row)
        self.plot_height_splitter.addWidget(space_row)
        self.plot_height_splitter.setStretchFactor(0, 1)
        self.plot_height_splitter.setStretchFactor(1, 1)
        self.plot_height_splitter.setStretchFactor(2, 2)
        self.plot_height_splitter.setSizes([220, 220, 440])

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
        self.apply_btn.clicked.connect(self._schedule_redraw)
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
            self.space_vmin_spin,
            self.space_vmax_spin,
        ):
            signal = getattr(widget, "valueChanged", None) or getattr(widget, "currentTextChanged", None) or getattr(widget, "toggled", None)
            if signal is not None:
                signal.connect(lambda *_args: self._schedule_redraw())
        self.fip_filter_band_edit.returnPressed.connect(self._schedule_redraw)
        self.edas_filter_band_edit.returnPressed.connect(self._schedule_redraw)
        self.channel_range_edit.returnPressed.connect(self._schedule_redraw)
        self.auto_levels_check.toggled.connect(self._update_manual_levels_state)

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
            self._select_path(Path(current.data(Qt.UserRole)))
        else:
            self._current_path = None
            self.file_info_label.setText("未找到 .npz 文件")
            self._clear_plots()

    def _on_file_selected(self, current: Optional[QListWidgetItem], _previous: Optional[QListWidgetItem]) -> None:
        if current is None:
            return
        self._select_path(Path(current.data(Qt.UserRole)))

    def _select_path(self, path: Path) -> None:
        self._current_path = path
        self._auto_range_pending = True
        self._schedule_redraw()

    def _schedule_redraw(self) -> None:
        if self._current_path is None:
            return
        request = self._build_request()
        if request is None:
            return
        self._worker_seq += 1
        self._pending_request = request
        self._redraw_timer.start()

    def _flush_requests(self) -> None:
        if self._pending_request is not None:
            request = self._pending_request
            self._pending_request = None
            self._worker.redraw(request)

    def _build_request(self) -> Optional[RedrawRequest]:
        if self._current_path is None:
            return None
        ch0, ch1 = _parse_range(self.channel_range_edit.text(), 0, 199)
        curve1_source = self.curve1_combo.currentText()
        curve2_source = self.curve2_combo.currentText()
        return RedrawRequest(
            seq=self._worker_seq + 1,
            path=str(self._current_path),
            curve1=CurveRequest(
                source=curve1_source,
                das_channel=self.das_channel_spin.value(),
                preprocess=self._preprocess_spec(curve1_source),
                max_points=self.max_points_spin.value(),
            ),
            curve2=CurveRequest(
                source=curve2_source,
                das_channel=self.das_channel_spin.value(),
                preprocess=self._preprocess_spec(curve2_source),
                max_points=self.max_points_spin.value(),
            ),
            space=SpaceRequest(
                channel_start=ch0,
                channel_end=ch1,
                time_downsample=self.space_time_downsample_spin.value(),
                space_downsample=self.space_downsample_spin.value(),
                remove_baseline=self.space_baseline_check.isChecked(),
                auto_levels=self.auto_levels_check.isChecked(),
                vmin=self.space_vmin_spin.value(),
                vmax=self.space_vmax_spin.value(),
            ),
        )

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

    def _on_redraw_done(self, result: RedrawResult) -> None:
        if result.seq != self._worker_seq:
            return
        self._apply_result(result)

    def _apply_result(self, result: RedrawResult) -> None:
        self._configure_image_colormap()
        self.file_info_label.setText(result.file_info)
        self.statusBar().showMessage(f"已加载 {Path(result.path).name}")
        self.curve1.setData(result.curve1.times, result.curve1.values)
        self.curve1_plot.setTitle(result.curve1.title)
        self.curve2.setData(result.curve2.times, result.curve2.values)
        self.curve2_plot.setTitle(result.curve2.title)
        self.space_image.setImage(result.space_matrix, autoLevels=False, levels=result.space_levels)
        self.space_image.setRect(*result.space_rect)
        self.histogram.setLevels(*result.space_levels)
        if self.auto_levels_check.isChecked():
            self.space_vmin_spin.blockSignals(True)
            self.space_vmin_spin.setValue(result.space_levels[0])
            self.space_vmin_spin.blockSignals(False)
            self.space_vmax_spin.blockSignals(True)
            self.space_vmax_spin.setValue(result.space_levels[1])
            self.space_vmax_spin.blockSignals(False)
        if self._auto_range_pending:
            self._auto_range_pending = False
            self._reset_view()

    def _on_worker_failed(self, message: str, seq: int) -> None:
        if seq != self._worker_seq:
            return
        self._clear_plots()
        self.file_info_label.setText("加载失败")
        self.statusBar().showMessage("加载失败")
        QMessageBox.critical(self, "读取失败", message)

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
        "rdbu": [
            [103, 0, 31],
            [178, 24, 43],
            [247, 247, 247],
            [33, 102, 172],
            [5, 48, 97],
        ],
        "coolwarm": [
            [59, 76, 192],
            [154, 165, 222],
            [238, 240, 251],
            [244, 123, 123],
            [180, 4, 38],
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