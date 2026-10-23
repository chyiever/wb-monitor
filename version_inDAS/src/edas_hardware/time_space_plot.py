"""
`src/time_space_plot.py` 实现 Tab2 的 Time-Space 图控件，是当前项目中最复杂的自定义绘图部件之一。

最新版本的核心目标已经从“先把图画出来”转向“在持续实时刷新下稳定画图”。因此这里没有采用每次全量拼接历史矩阵的简单做法，而是维护固定大小的滚动显示缓冲，并通过 `PlotWidget + ImageItem + HistogramLUTWidget` 组合完成图像显示、坐标轴控制和颜色条管理。

对类似项目而言，本文件最值得参考的不是控件表面，而是它对显示语义、滚动缓冲、颜色范围和缩放锁定的系统化处理。
"""
from __future__ import annotations

from typing import Dict, Optional, Tuple

import numpy as np
from PyQt5.QtCore import QRectF, QTimer, pyqtSignal
from PyQt5.QtGui import QFont
from PyQt5.QtWidgets import (
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)
import pyqtgraph as pg

from .logger import get_logger
from .plot_interaction import ZoomablePlotViewBox
from .realtime_filter import FilterSpecError, RealtimeTimeAxisFilter, parse_filter_spec


log = get_logger("time_space_plot")


def _create_custom_colormaps() -> Dict[str, pg.ColorMap]:
    """Provide a few colormaps when the local pyqtgraph build is missing them."""
    custom_maps: Dict[str, pg.ColorMap] = {}
    custom_maps["jet"] = pg.ColorMap(
        np.linspace(0.0, 1.0, 9),
        np.array(
            [
                [0, 0, 127],
                [0, 0, 255],
                [0, 127, 255],
                [0, 255, 255],
                [127, 255, 127],
                [255, 255, 0],
                [255, 127, 0],
                [255, 0, 0],
                [127, 0, 0],
            ]
        ),
    )
    custom_maps["gray"] = pg.ColorMap(
        np.array([0.0, 1.0]),
        np.array([[0, 0, 0], [255, 255, 255]]),
    )
    custom_maps["seismic"] = pg.ColorMap(
        np.linspace(0.0, 1.0, 7),
        np.array(
            [
                [0, 0, 75],
                [0, 0, 255],
                [127, 127, 255],
                [255, 255, 255],
                [255, 127, 127],
                [255, 0, 0],
                [75, 0, 0],
            ]
        ),
    )
    return custom_maps


_CUSTOM_COLORMAPS = _create_custom_colormaps()

COLORMAP_OPTIONS = [
    ("Jet", "jet"),
    ("HSV", "hsv"),
    ("Viridis", "viridis"),
    ("Plasma", "plasma"),
    ("Inferno", "inferno"),
    ("Magma", "magma"),
    ("Seismic", "seismic"),
    ("Gray", "gray"),
]


class TimeSpacePlotWidget(QWidget):
    """Realtime time-space plot with fixed rolling image buffer."""

    parametersChanged = pyqtSignal()
    pointCountChanged = pyqtSignal(int)
    plotStateChanged = pyqtSignal(bool)

    DISPLAY_UPDATE_INTERVAL_MS = 150

    def __init__(self):
        super().__init__()

        self._max_window_frames = 100
        self._window_frames = 5
        self._distance_start = 40
        self._distance_end = 100
        self._time_downsample = 50
        self._space_downsample = 2
        self._colormap = "jet"
        self._vmin = -0.02
        self._vmax = 0.02
        self._scan_rate_hz = 2000.0
        self._axis_unit = 'points'
        self._axis_origin = 0.0
        self._axis_dx = 1.0
        self._filter_enabled = False
        self._filter_spec_text = "1-"
        self._filter_error_text = ""
        self._time_axis_filter = RealtimeTimeAxisFilter(order=2)

        self._plot_enabled = False
        self._pending_update = False
        self._full_point_num = 0
        self._zoom_locked = False

        self._display_buffer: Optional[np.ndarray] = None
        self._display_block_width = 0
        self._display_space_count = 0
        self._display_block_duration_s = 0.0
        self._source_frames_per_block = 0
        self._valid_block_count = 0
        self._current_distance_bounds: Tuple[int, int] = (self._distance_start, self._distance_end)

        self._display_update_timer = QTimer(self)
        self._display_update_timer.setSingleShot(True)
        self._display_update_timer.timeout.connect(self._flush_scheduled_display_update)

        self._setup_ui()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(5)
        layout.setContentsMargins(5, 5, 5, 5)

        control_panel = self._create_control_panel()
        control_panel.setMaximumHeight(140)
        layout.addWidget(control_panel)

        plot_layout = QHBoxLayout()
        plot_layout.setContentsMargins(0, 0, 0, 0)
        plot_layout.setSpacing(6)
        self._create_plot_area()
        plot_layout.addWidget(self.plot_widget, 1)
        self._create_colorbar()
        plot_layout.addWidget(self.histogram_widget, 0)

        plot_container = QWidget()
        plot_container.setLayout(plot_layout)
        layout.addWidget(plot_container, 1)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def _create_plot_area(self):
        self._view_box = ZoomablePlotViewBox()
        self._view_box.sigManualRangeChange.connect(self._on_manual_range_change)
        self._view_box.sigViewAllRequested.connect(self._restore_auto_range)

        self.plot_widget = pg.PlotWidget(viewBox=self._view_box)
        self.plot_widget.setMinimumSize(800, 400)
        self.plot_widget.setBackground("w")
        self.plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.plot_widget.setLabel(
            "bottom",
            "Time (s)",
            color="k",
            **{"font-size": "10pt", "font-family": "Times New Roman"},
        )
        self.plot_widget.setLabel(
            "left",
            "Distance (points)",
            color="k",
            **{"font-size": "10pt", "font-family": "Times New Roman"},
        )
        self.plot_widget.showAxis("top", show=False)
        self.plot_widget.showAxis("right", show=False)

        font = QFont("Times New Roman", 9)
        for axis_name in ("bottom", "left"):
            axis = self.plot_widget.getAxis(axis_name)
            axis.setTickFont(font)
            axis.setPen("k")
            axis.setTextPen("k")
            axis.setStyle(showValues=True)
            axis.enableAutoSIPrefix(False)

        self.image_item = pg.ImageItem(axisOrder="row-major")
        self.plot_widget.addItem(self.image_item)
        self._apply_colormap()
        self._restore_auto_range()

    def _create_colorbar(self):
        self.histogram_widget = pg.HistogramLUTWidget()
        self.histogram_widget.setMinimumWidth(140)
        self.histogram_widget.setMaximumWidth(180)
        self.histogram_widget.setMinimumHeight(400)
        self.histogram_widget.setBackground("w")
        self.histogram_widget.setImageItem(self.image_item)
        histogram_item = getattr(self.histogram_widget, "item", None)
        region = getattr(histogram_item, "region", None)
        if region is not None:
            region.setMovable(False)
        self._apply_color_levels()
        self._style_histogram_widget()
        QTimer.singleShot(100, self._style_histogram_widget)

        self._apply_colormap()

    def _style_histogram_widget(self):
        """Apply stable colorbar styling after pyqtgraph creates child items."""
        if not hasattr(self, "histogram_widget"):
            return
        self.histogram_widget.setBackground("w")
        plot_item = getattr(self.histogram_widget, "plotItem", None)
        if plot_item is not None:
            axis = plot_item.getAxis("left")
            if axis is not None:
                axis.setTickFont(QFont("Times New Roman", 8))
                axis.setPen("k")
                axis.setTextPen("k")
                axis.setStyle(showValues=True)

        gradient = getattr(self.histogram_widget, "gradient", None)
        if gradient is not None and hasattr(gradient, "setTickFont"):
            gradient.setTickFont(QFont("Times New Roman", 7))

    def _create_control_panel(self):
        group = QGroupBox()
        group.setFont(QFont("Times New Roman", 9))

        layout = QGridLayout(group)
        layout.setHorizontalSpacing(8)
        layout.setVerticalSpacing(6)
        layout.setContentsMargins(6, 6, 6, 6)

        row = 0
        distance_label = QLabel("Distance Range:")
        distance_label.setFont(QFont("Times New Roman", 8))
        layout.addWidget(distance_label, row, 0)

        from_label = QLabel("From:")
        from_label.setFont(QFont("Times New Roman", 8))
        layout.addWidget(from_label, row, 1)

        self.distance_start_spin = QSpinBox()
        self.distance_start_spin.setRange(0, 1000000)
        self.distance_start_spin.setValue(self._distance_start)
        self.distance_start_spin.setMaximumWidth(58)
        self.distance_start_spin.setFont(QFont("Times New Roman", 8))
        self.distance_start_spin.setToolTip("位置点序号从 0 开始（0 表示第一个空间点）")
        self.distance_start_spin.valueChanged.connect(self._on_distance_start_changed)
        layout.addWidget(self.distance_start_spin, row, 2)

        to_label = QLabel("To:")
        to_label.setFont(QFont("Times New Roman", 8))
        layout.addWidget(to_label, row, 3)

        self.distance_end_spin = QSpinBox()
        self.distance_end_spin.setRange(1, 1000000)
        self.distance_end_spin.setValue(self._distance_end)
        self.distance_end_spin.setMaximumWidth(58)
        self.distance_end_spin.setFont(QFont("Times New Roman", 8))
        self.distance_end_spin.setToolTip("位置点序号从 0 开始（0 表示第一个空间点）")
        self.distance_end_spin.valueChanged.connect(self._on_distance_end_changed)
        layout.addWidget(self.distance_end_spin, row, 4)

        window_label = QLabel("Window Frames:")
        window_label.setFont(QFont("Times New Roman", 8))
        layout.addWidget(window_label, row, 5)

        self.window_frames_spin = QSpinBox()
        self.window_frames_spin.setRange(1, self._max_window_frames)
        self.window_frames_spin.setValue(self._window_frames)
        self.window_frames_spin.setMaximumWidth(48)
        self.window_frames_spin.setFont(QFont("Times New Roman", 8))
        self.window_frames_spin.valueChanged.connect(self._on_window_frames_changed)
        layout.addWidget(self.window_frames_spin, row, 6)

        time_ds_label = QLabel("Time DS:")
        time_ds_label.setFont(QFont("Times New Roman", 8))
        layout.addWidget(time_ds_label, row, 7)

        self.time_downsample_spin = QSpinBox()
        self.time_downsample_spin.setRange(1, 1000)
        self.time_downsample_spin.setValue(self._time_downsample)
        self.time_downsample_spin.setMaximumWidth(48)
        self.time_downsample_spin.setFont(QFont("Times New Roman", 8))
        self.time_downsample_spin.valueChanged.connect(self._on_time_downsample_changed)
        layout.addWidget(self.time_downsample_spin, row, 8)

        space_ds_label = QLabel("Space DS:")
        space_ds_label.setFont(QFont("Times New Roman", 8))
        layout.addWidget(space_ds_label, row, 9)

        self.space_downsample_spin = QSpinBox()
        self.space_downsample_spin.setRange(1, 100)
        self.space_downsample_spin.setValue(self._space_downsample)
        self.space_downsample_spin.setMaximumWidth(48)
        self.space_downsample_spin.setFont(QFont("Times New Roman", 8))
        self.space_downsample_spin.valueChanged.connect(self._on_space_downsample_changed)
        layout.addWidget(self.space_downsample_spin, row, 10)

        row = 1
        color_range_label = QLabel("Color Range:")
        color_range_label.setFont(QFont("Times New Roman", 8))
        layout.addWidget(color_range_label, row, 0)

        min_label = QLabel("Min:")
        min_label.setFont(QFont("Times New Roman", 8))
        layout.addWidget(min_label, row, 1)

        self.vmin_spin = QDoubleSpinBox()
        self.vmin_spin.setRange(-10000.0, 10000.0)
        self.vmin_spin.setDecimals(6)
        self.vmin_spin.setSingleStep(0.001)
        self.vmin_spin.setValue(self._vmin)
        self.vmin_spin.setMaximumWidth(82)
        self.vmin_spin.setFont(QFont("Times New Roman", 8))
        self.vmin_spin.valueChanged.connect(self._on_vmin_changed)
        layout.addWidget(self.vmin_spin, row, 2)

        max_label = QLabel("Max:")
        max_label.setFont(QFont("Times New Roman", 8))
        layout.addWidget(max_label, row, 3)

        self.vmax_spin = QDoubleSpinBox()
        self.vmax_spin.setRange(-10000.0, 10000.0)
        self.vmax_spin.setDecimals(6)
        self.vmax_spin.setSingleStep(0.001)
        self.vmax_spin.setValue(self._vmax)
        self.vmax_spin.setMaximumWidth(82)
        self.vmax_spin.setFont(QFont("Times New Roman", 8))
        self.vmax_spin.valueChanged.connect(self._on_vmax_changed)
        layout.addWidget(self.vmax_spin, row, 4)

        colormap_label = QLabel("Colormap:")
        colormap_label.setFont(QFont("Times New Roman", 8))
        layout.addWidget(colormap_label, row, 5)

        self.colormap_combo = QComboBox()
        self.colormap_combo.setMaximumWidth(76)
        self.colormap_combo.setFont(QFont("Times New Roman", 8))
        for name, value in COLORMAP_OPTIONS:
            self.colormap_combo.addItem(name, value)
        self.colormap_combo.setCurrentText("Jet")
        self.colormap_combo.currentTextChanged.connect(self._on_colormap_changed)
        layout.addWidget(self.colormap_combo, row, 6)

        reset_btn = QPushButton("Reset to Defaults")
        reset_btn.setFont(QFont("Times New Roman", 8))
        reset_btn.setMaximumWidth(104)
        reset_btn.clicked.connect(self._reset_to_defaults)
        layout.addWidget(reset_btn, row, 7)

        self.plot_btn = QPushButton("PLOT")
        self.plot_btn.setFont(QFont("Times New Roman", 8, QFont.Bold))
        self.plot_btn.setMaximumWidth(56)
        self.plot_btn.setCheckable(True)
        self.plot_btn.clicked.connect(self._on_plot_button_clicked)
        self._update_plot_button_style()
        layout.addWidget(self.plot_btn, row, 8)

        layout.setColumnStretch(9, 1)
        return group

    def _get_colormap(self, colormap_name: str) -> Optional[pg.ColorMap]:
        try:
            return pg.colormap.get(colormap_name)
        except Exception:
            return _CUSTOM_COLORMAPS.get(colormap_name) or _CUSTOM_COLORMAPS.get("jet")

    def _apply_colormap(self):
        colormap = self._get_colormap(self._colormap)
        if colormap is None:
            return
        if hasattr(self, "image_item"):
            self.image_item.setColorMap(colormap)
        if hasattr(self, "histogram_widget") and hasattr(self.histogram_widget, "gradient"):
            self.histogram_widget.gradient.setColorMap(colormap)

    def _apply_color_levels(self):
        """Apply the front-panel color range without enabling histogram auto-levels."""
        if self._vmin >= self._vmax:
            return
        if hasattr(self, "image_item"):
            self.image_item.setLevels((self._vmin, self._vmax))
        if hasattr(self, "histogram_widget"):
            self.histogram_widget.setLevels(self._vmin, self._vmax)

    def _update_histogram_range(self, display_data: np.ndarray):
        """Show the full data distribution while keeping front-panel color levels fixed."""
        if not hasattr(self, "histogram_widget") or display_data.size == 0:
            return

        finite_mask = np.isfinite(display_data)
        if not np.any(finite_mask):
            histogram_min, histogram_max = self._vmin, self._vmax
        else:
            if np.all(finite_mask):
                data_min = float(np.min(display_data))
                data_max = float(np.max(display_data))
            else:
                finite_data = display_data[finite_mask]
                data_min = float(np.min(finite_data))
                data_max = float(np.max(finite_data))
            histogram_min = min(data_min, self._vmin)
            histogram_max = max(data_max, self._vmax)

        if histogram_min >= histogram_max:
            padding = max(abs(histogram_min) * 0.05, 1e-9)
            histogram_min -= padding
            histogram_max += padding

        histogram_item = getattr(self.histogram_widget, "item", None)
        if histogram_item is not None and hasattr(histogram_item, "setHistogramRange"):
            histogram_item.setHistogramRange(histogram_min, histogram_max, padding=0.05)

    def _invalidate_display_buffer(self, clear_image: bool = False):
        self._display_buffer = None
        self._display_block_width = 0
        self._display_space_count = 0
        self._display_block_duration_s = 0.0
        self._source_frames_per_block = 0
        self._valid_block_count = 0
        self._pending_update = False
        self._display_update_timer.stop()
        if clear_image and hasattr(self, "image_item"):
            self.image_item.setImage(np.zeros((1, 1)), autoLevels=False)
            self.image_item.setLevels((self._vmin, self._vmax))

    def _reset_filter_state(self):
        self._time_axis_filter.reset_state()

    def _set_filter_error(self, message: str):
        self._filter_error_text = message
        if hasattr(self, "filter_spec_edit"):
            self.filter_spec_edit.setToolTip(
                message or "Examples: 1- high-pass, -10 low-pass, 2-10 band-pass"
            )
        if hasattr(self, "filter_btn"):
            self._update_filter_button_style()

    def _on_plot_button_clicked(self, checked: bool):
        self._plot_enabled = checked
        self._reset_filter_state()
        if not checked:
            self._pending_update = False
            self._display_update_timer.stop()
        self._update_plot_button_style()
        self.plotStateChanged.emit(self._plot_enabled)
        log.info("Time-space plot %s", "enabled" if checked else "disabled")

    def _update_plot_button_style(self):
        if self._plot_enabled:
            self.plot_btn.setStyleSheet(
                """
                QPushButton {
                    background-color: #4CAF50;
                    color: white;
                    border: 1px solid #45a049;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background-color: #45a049;
                }
                """
            )
        else:
            self.plot_btn.setStyleSheet(
                """
                QPushButton {
                    background-color: #9E9E9E;
                    color: white;
                    border: 1px solid #757575;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background-color: #757575;
                }
                """
            )

    def _update_filter_button_style(self):
        if not hasattr(self, "filter_btn"):
            return
        if self._filter_enabled and self._filter_error_text:
            self.filter_btn.setStyleSheet(
                """
                QPushButton {
                    background-color: #F57C00;
                    color: white;
                    border: 1px solid #E65100;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background-color: #EF6C00;
                }
                """
            )
        elif self._filter_enabled:
            self.filter_btn.setStyleSheet(
                """
                QPushButton {
                    background-color: #1976D2;
                    color: white;
                    border: 1px solid #1565C0;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background-color: #1565C0;
                }
                """
            )
        else:
            self.filter_btn.setStyleSheet(
                """
                QPushButton {
                    background-color: #9E9E9E;
                    color: white;
                    border: 1px solid #757575;
                    border-radius: 3px;
                }
                QPushButton:hover {
                    background-color: #757575;
                }
                """
            )

    def _on_distance_start_changed(self, value: int):
        self._distance_start = value
        if self._distance_end <= self._distance_start:
            self.distance_end_spin.setValue(self._distance_start + 1)
            return
        self._reset_filter_state()
        self._invalidate_display_buffer(clear_image=True)
        self.parametersChanged.emit()

    def _on_distance_end_changed(self, value: int):
        if value <= self._distance_start:
            self.distance_end_spin.setValue(self._distance_start + 1)
            return
        self._distance_end = value
        self._reset_filter_state()
        self._invalidate_display_buffer(clear_image=True)
        self.parametersChanged.emit()

    def _on_window_frames_changed(self, value: int):
        self._window_frames = value
        self._invalidate_display_buffer(clear_image=True)
        self.parametersChanged.emit()

    def _on_space_downsample_changed(self, value: int):
        self._space_downsample = value
        self._reset_filter_state()
        self._invalidate_display_buffer(clear_image=True)
        self.parametersChanged.emit()

    def _on_time_downsample_changed(self, value: int):
        self._time_downsample = value
        self._invalidate_display_buffer(clear_image=True)
        self.parametersChanged.emit()

    def _on_colormap_changed(self, text: str):
        for name, value in COLORMAP_OPTIONS:
            if name == text:
                self._colormap = value
                break
        self._apply_colormap()
        self.parametersChanged.emit()

    def _on_vmin_changed(self, value: float):
        if value >= self._vmax:
            self.vmin_spin.setValue(self._vmax - self.vmin_spin.singleStep())
            return
        self._vmin = value
        self._apply_color_levels()
        self.parametersChanged.emit()

    def _on_vmax_changed(self, value: float):
        if value <= self._vmin:
            self.vmax_spin.setValue(self._vmin + self.vmax_spin.singleStep())
            return
        self._vmax = value
        self._apply_color_levels()
        self.parametersChanged.emit()

    def _on_filter_spec_changed(self):
        text = self.filter_spec_edit.text().strip()
        self._filter_spec_text = text
        self._time_axis_filter.reset_design()
        if self._filter_enabled:
            self._invalidate_display_buffer(clear_image=True)
            try:
                filter_spec = parse_filter_spec(text)
                self._time_axis_filter.configure(filter_spec, self._scan_rate_hz)
                self._set_filter_error("")
            except FilterSpecError as exc:
                self._set_filter_error(str(exc))
                log.warning("Invalid time-space filter parameter: %s", exc)
        else:
            self._set_filter_error("")
        self.parametersChanged.emit()

    def _on_filter_button_clicked(self, checked: bool):
        self._filter_enabled = checked
        self._time_axis_filter.reset_design()
        self._invalidate_display_buffer(clear_image=True)
        if checked:
            self._filter_spec_text = self.filter_spec_edit.text().strip()
            try:
                filter_spec = parse_filter_spec(self._filter_spec_text)
                self._time_axis_filter.configure(filter_spec, self._scan_rate_hz)
                self._set_filter_error("")
            except FilterSpecError as exc:
                self._set_filter_error(str(exc))
                log.warning("Time-space filter enabled with invalid parameter: %s", exc)
        else:
            self._set_filter_error("")
        self._update_filter_button_style()
        self.parametersChanged.emit()

    def _reset_to_defaults(self):
        self.window_frames_spin.setValue(5)
        self.distance_start_spin.setValue(40)
        self.distance_end_spin.setValue(100)
        self.time_downsample_spin.setValue(50)
        self.space_downsample_spin.setValue(2)
        self.colormap_combo.setCurrentText("Jet")
        self.vmin_spin.setValue(-0.02)
        self.vmax_spin.setValue(0.02)
        self._invalidate_display_buffer(clear_image=True)
        self.parametersChanged.emit()

    def _on_manual_range_change(self):
        self._zoom_locked = True
        self._view_box.disableAutoRange()

    def _restore_auto_range(self):
        self._zoom_locked = False
        self._view_box.enableAutoRange(x=True, y=True)
        self._view_box.autoRange(padding=0.0)

    def is_plot_enabled(self) -> bool:
        return self._plot_enabled

    def set_scan_rate(self, scan_rate_hz: float):
        if scan_rate_hz > 0:
            new_scan_rate = float(scan_rate_hz)
            if abs(self._scan_rate_hz - new_scan_rate) > 1e-12:
                self._scan_rate_hz = new_scan_rate
                self._time_axis_filter.reset_design()
                if self._filter_enabled:
                    self._invalidate_display_buffer(clear_image=True)

    def _apply_realtime_filter(self, display_block: np.ndarray) -> np.ndarray:
        if not self._filter_enabled:
            return display_block

        try:
            filter_spec = parse_filter_spec(self._filter_spec_text)
            filtered = self._time_axis_filter.process(
                display_block,
                filter_spec,
                self._scan_rate_hz,
            )
            if self._filter_error_text:
                self._set_filter_error("")
            return filtered
        except FilterSpecError as exc:
            message = str(exc)
            if message != self._filter_error_text:
                log.warning("Time-space filter skipped: %s", message)
            self._set_filter_error(message)
            return display_block

    def update_data(self, data: np.ndarray, display_scale: Optional[np.float32] = None) -> bool:
        if not self._plot_enabled:
            return False

        if data.ndim == 1:
            data = data.reshape(1, -1)

        frame_count, point_count = data.shape
        if point_count != self._full_point_num:
            self._full_point_num = point_count
            self.pointCountChanged.emit(point_count)

        display_block_info = self._build_display_block(data, display_scale)
        if display_block_info is None:
            return False

        display_block, block_duration_s, distance_bounds = display_block_info
        self._append_display_block(display_block, block_duration_s, frame_count, distance_bounds)
        self._schedule_display_update()
        return True

    def append_prepared_block(
        self,
        display_block: np.ndarray,
        block_duration_s: float,
        source_frame_count: int,
        distance_bounds: Tuple[int, int],
    ) -> bool:
        """Append an acquisition-thread-prepared ``(space, time)`` display block.

        The block is already rad-scaled, distance-cropped and time/space downsampled,
        so the GUI only manages the rolling buffer and schedules the image redraw.
        """
        if not self._plot_enabled:
            return False

        if display_block.ndim != 2 or display_block.size == 0:
            return False

        self._append_display_block(
            np.ascontiguousarray(display_block),
            float(block_duration_s),
            int(source_frame_count),
            tuple(distance_bounds),
        )
        self._schedule_display_update()
        return True

    def _build_display_block(
        self, data_block: np.ndarray, display_scale: Optional[np.float32] = None
    ) -> Optional[Tuple[np.ndarray, float, Tuple[int, int]]]:
        frame_count, point_count = data_block.shape

        start_idx = max(0, min(self._distance_start, max(0, point_count - 1)))
        end_idx = min(point_count, max(start_idx + 1, self._distance_end))
        if start_idx >= end_idx:
            return None

        display_block = data_block[:, start_idx:end_idx]
        if self._space_downsample > 1:
            display_block = display_block[:, :: self._space_downsample]
        if not self._filter_enabled and self._time_downsample > 1:
            display_block = display_block[:: self._time_downsample, :]
        if display_scale is not None:
            display_block = np.asarray(display_block).astype(np.float32, copy=True)
            display_block *= display_scale
        display_block = self._apply_realtime_filter(display_block)
        if self._filter_enabled and self._time_downsample > 1:
            display_block = display_block[:: self._time_downsample, :]

        if display_block.size == 0:
            return None

        # Keep the display buffer in (space, time) order.
        display_block = np.ascontiguousarray(display_block.T)
        block_duration_s = frame_count / max(self._scan_rate_hz, 1.0)
        return display_block, block_duration_s, (start_idx, end_idx)

    def _append_display_block(
        self,
        display_block: np.ndarray,
        block_duration_s: float,
        source_frame_count: int,
        distance_bounds: Tuple[int, int],
    ):
        space_count, block_width = display_block.shape
        needs_reset = (
            self._display_buffer is None
            or self._display_space_count != space_count
            or self._display_block_width != block_width
            or self._source_frames_per_block != source_frame_count
            or abs(self._display_block_duration_s - block_duration_s) > 1e-12
        )

        if needs_reset:
            self._display_space_count = space_count
            self._display_block_width = block_width
            self._display_block_duration_s = block_duration_s
            self._source_frames_per_block = source_frame_count
            self._display_buffer = np.zeros(
                (space_count, max(1, block_width * self._window_frames)),
                dtype=display_block.dtype,
            )
            self._valid_block_count = 0

        self._current_distance_bounds = distance_bounds

        if self._valid_block_count < self._window_frames:
            start_col = self._valid_block_count * self._display_block_width
            end_col = start_col + self._display_block_width
            self._display_buffer[:, start_col:end_col] = display_block
            self._valid_block_count += 1
            return

        self._display_buffer[:, :-self._display_block_width] = self._display_buffer[:, self._display_block_width :]
        self._display_buffer[:, -self._display_block_width :] = display_block

    def _schedule_display_update(self):
        self._pending_update = True
        if not self._display_update_timer.isActive():
            self._display_update_timer.start(self.DISPLAY_UPDATE_INTERVAL_MS)

    def _flush_scheduled_display_update(self):
        if not self._pending_update:
            return
        self._pending_update = False
        self._update_display()

    def _update_display(self):
        if self._display_buffer is None or self._valid_block_count <= 0:
            return

        valid_columns = self._valid_block_count * self._display_block_width
        if valid_columns == self._display_buffer.shape[1]:
            display_data = self._display_buffer
        else:
            display_data = np.ascontiguousarray(self._display_buffer[:, :valid_columns])
        total_duration_s = self._valid_block_count * self._display_block_duration_s
        distance_start, distance_end = self._current_distance_bounds

        self.image_item.setImage(display_data, autoLevels=False)
        self._apply_color_levels()
        self._update_histogram_range(display_data)
        scale = self._axis_dx / (1000.0 if self._axis_unit == 'km' else 1.0) if self._axis_unit != 'points' else 1.0
        origin = self._axis_origin if self._axis_unit != 'points' else 0.0
        spatial_step = max(1, self._space_downsample)
        self.image_item.setRect(
            QRectF(
                0.0,
                float((distance_start - spatial_step / 2) * scale + origin),
                float(max(total_duration_s, 1.0 / max(self._scan_rate_hz, 1.0))),
                float(display_data.shape[0] * spatial_step * scale),
            )
        )
        if not self._zoom_locked:
            self._view_box.enableAutoRange(x=True, y=True)
            self._view_box.autoRange(padding=0.0)

    def set_vertical_axis(self, unit='points', dx=1.0, origin=0.0):
        """Transform image coordinates only; buffers/cropping keep channel indices."""
        self._axis_unit = unit if unit in ('points', 'm', 'km') else 'points'
        self._axis_dx = float(dx)
        self._axis_origin = float(origin)
        title = 'Points' if self._axis_unit == 'points' else f'Distance ({self._axis_unit})'
        self.plot_widget.setLabel('left', title)
        self._update_display()

    def get_parameters(self):
        filter_spec_text = (
            self.filter_spec_edit.text().strip()
            if hasattr(self, "filter_spec_edit")
            else self._filter_spec_text
        )
        self._filter_spec_text = filter_spec_text
        return {
            "window_frames": self._window_frames,
            "distance_range_start": self._distance_start,
            "distance_range_end": self._distance_end,
            "time_downsample": self._time_downsample,
            "space_downsample": self._space_downsample,
            "colormap_type": self._colormap,
            "vmin": self._vmin,
            "vmax": self._vmax,
            "filter_enabled": self._filter_enabled,
            "filter_spec": filter_spec_text,
        }

    def get_filter_settings(self) -> Tuple[bool, str]:
        """Return the current shared FILTER switch and cutoff text."""
        return self._filter_enabled, self._filter_spec_text

    def set_filter_settings(self, enabled: bool, spec_text: str):
        """Receive the shared FILTER settings from the main control panel."""
        enabled = bool(enabled)
        spec_text = str(spec_text).strip()
        changed = enabled != self._filter_enabled or spec_text != self._filter_spec_text
        was_enabled = self._filter_enabled
        self._filter_enabled = enabled
        self._filter_spec_text = spec_text
        if changed:
            self._time_axis_filter.reset_design()
            if enabled or was_enabled:
                self._invalidate_display_buffer(clear_image=True)

        if enabled:
            try:
                filter_spec = parse_filter_spec(self._filter_spec_text)
                self._time_axis_filter.configure(filter_spec, self._scan_rate_hz)
                self._set_filter_error("")
            except FilterSpecError as exc:
                self._set_filter_error(str(exc))
        else:
            self._set_filter_error("")

    def set_parameters(self, params):
        if "window_frames" in params:
            self.window_frames_spin.setValue(int(params["window_frames"]))
        if "distance_range_start" in params:
            self.distance_start_spin.setValue(int(params["distance_range_start"]))
        if "distance_range_end" in params:
            self.distance_end_spin.setValue(int(params["distance_range_end"]))
        if "time_downsample" in params:
            self.time_downsample_spin.setValue(int(params["time_downsample"]))
        if "space_downsample" in params:
            self.space_downsample_spin.setValue(int(params["space_downsample"]))
        if "colormap_type" in params:
            for name, value in COLORMAP_OPTIONS:
                if value == params["colormap_type"]:
                    self.colormap_combo.setCurrentText(name)
                    break
        if "filter_spec" in params or "filter_enabled" in params:
            self.set_filter_settings(
                params.get("filter_enabled", self._filter_enabled),
                params.get("filter_spec", self._filter_spec_text),
            )
        if "vmin" in params or "vmax" in params:
            vmin = float(params.get("vmin", self._vmin))
            vmax = float(params.get("vmax", self._vmax))
            if vmin < vmax:
                self.vmin_spin.blockSignals(True)
                self.vmax_spin.blockSignals(True)
                self.vmin_spin.setValue(vmin)
                self.vmax_spin.setValue(vmax)
                self.vmin_spin.blockSignals(False)
                self.vmax_spin.blockSignals(False)
                self._vmin = vmin
                self._vmax = vmax
                self._apply_color_levels()

    def clear_data(self):
        self._invalidate_display_buffer(clear_image=True)
        log.debug("Time-space plot cleared")


def create_time_space_widget():
    log.info("Creating TimeSpacePlotWidget instance")
    return TimeSpacePlotWidget()


TimeSpacePlotWidgetV2 = TimeSpacePlotWidget


__all__ = ["TimeSpacePlotWidget", "TimeSpacePlotWidgetV2", "create_time_space_widget"]
