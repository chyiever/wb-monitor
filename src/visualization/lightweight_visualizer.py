"""
轻量级可视化更新模块

专门处理从后台线程接收的预处理数据，在主线程进行轻量级UI更新。

Author: Claude
Date: 2026-03-12
"""

import time
import logging
import numpy as np
from typing import Optional, Dict, Any
from collections import deque
from PyQt5.QtCore import QObject, QTimer, pyqtSignal
import pyqtgraph as pg


class LightweightVisualizer(QObject):
    """轻量级可视化器 - 只在主线程进行UI更新"""

    # 信号
    plot_updated = pyqtSignal(str)

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__ + '.LightweightVisualizer')

        # 绘图组件引用
        self.time_plot: Optional[pg.PlotWidget] = None
        self.psd_plot: Optional[pg.PlotWidget] = None
        self.time_curve: Optional[pg.PlotCurveItem] = None
        self.psd_curve: Optional[pg.PlotCurveItem] = None

        # 数据缓冲区（主线程安全）
        self.time_data_buffer = deque(maxlen=100000)  # 时域数据缓冲
        self.time_timestamps = deque(maxlen=100000)   # 时间戳缓冲

        # 显示参数
        self.time_display_window = 1.0  # 显示窗口长度（秒）
        self.max_display_points = 50000  # 最大显示点数

        # 更新控制
        self.time_update_enabled = True
        self.psd_update_enabled = True

        # 性能保护
        self.last_time_update = 0
        self.last_psd_update = 0
        self.min_update_interval = 0.1  # 最小更新间隔100ms

        self.logger.info("Lightweight visualizer initialized")

    def set_plot_widgets(self, time_plot: pg.PlotWidget, psd_plot: pg.PlotWidget):
        """设置绘图控件"""
        self.time_plot = time_plot
        self.psd_plot = psd_plot

        # 获取或创建绘图曲线
        if self.time_plot:
            curves = self.time_plot.listDataItems()
            if curves:
                self.time_curve = curves[0]
            else:
                self.time_curve = self.time_plot.plot(pen=pg.mkPen('b', width=2), name='时域信号')

        if self.psd_plot:
            curves = self.psd_plot.listDataItems()
            if curves:
                self.psd_curve = curves[0]
            else:
                self.psd_curve = self.psd_plot.plot(pen=pg.mkPen('r', width=2), name='功率谱密度')

        self.logger.info("Plot widgets configured")

    def update_from_processed_data(self, processed_data):
        """从预处理数据更新可视化"""
        try:
            current_time = time.time()

            # 更新时域显示
            if (self.time_update_enabled and
                current_time - self.last_time_update > self.min_update_interval):
                self._update_time_display(processed_data)
                self.last_time_update = current_time

            # 更新PSD显示
            if (self.psd_update_enabled and
                processed_data.psd_frequencies is not None and
                current_time - self.last_psd_update > self.min_update_interval * 2):  # PSD更新频率更低
                self._update_psd_display(processed_data)
                self.last_psd_update = current_time

        except Exception as e:
            self.logger.error(f"Error updating visualization: {e}")

    def _update_time_display(self, processed_data):
        """更新时域显示"""
        try:
            if not self.time_curve or not hasattr(processed_data, 'time_display_data'):
                return

            # 添加新数据到缓冲区
            time_data = processed_data.time_display_data
            start_time = processed_data.timestamp

            # 计算时间戳（100kHz显示采样率）
            display_sample_rate = 100000.0  # 100kHz显示
            dt = 1.0 / display_sample_rate
            timestamps = start_time + np.arange(len(time_data)) * dt

            # 添加到缓冲区
            self.time_data_buffer.extend(time_data)
            self.time_timestamps.extend(timestamps)

            # 获取显示窗口内的数据
            if len(self.time_timestamps) > 0:
                current_time_end = self.time_timestamps[-1]
                cutoff_time = current_time_end - self.time_display_window

                # 找到窗口内的数据
                display_times = []
                display_values = []

                for t, v in zip(self.time_timestamps, self.time_data_buffer):
                    if t >= cutoff_time:
                        display_times.append(t - self.time_timestamps[0])  # 相对时间
                        display_values.append(v)

                # 降采样显示数据（如果点数太多）
                if len(display_times) > self.max_display_points:
                    step = len(display_times) // self.max_display_points
                    display_times = display_times[::step]
                    display_values = display_values[::step]

                # 更新曲线
                if len(display_times) > 0:
                    self.time_curve.setData(display_times, display_values)
                    self.plot_updated.emit("time_domain")

        except Exception as e:
            self.logger.error(f"Error updating time display: {e}")

    def _update_psd_display(self, processed_data):
        """更新PSD显示"""
        try:
            if (not self.psd_curve or
                processed_data.psd_frequencies is None or
                processed_data.psd_values is None):
                return

            frequencies = processed_data.psd_frequencies
            psd_values = processed_data.psd_values

            if len(frequencies) > 0 and len(psd_values) > 0:
                # 直接使用预计算的PSD数据
                self.psd_curve.setData(frequencies, psd_values)
                self.plot_updated.emit("psd")

        except Exception as e:
            self.logger.error(f"Error updating PSD display: {e}")

    def clear_all_data(self):
        """清空所有数据"""
        try:
            self.time_data_buffer.clear()
            self.time_timestamps.clear()

            if self.time_curve:
                self.time_curve.setData([], [])

            if self.psd_curve:
                self.psd_curve.setData([], [])

            self.logger.info("Cleared all visualization data")

        except Exception as e:
            self.logger.error(f"Error clearing data: {e}")

    def set_time_update_enabled(self, enabled: bool):
        """启用/禁用时域更新"""
        self.time_update_enabled = enabled
        if not enabled and self.time_curve:
            self.time_curve.setData([], [])
        self.logger.info(f"Time domain updates {'enabled' if enabled else 'disabled'}")

    def set_psd_update_enabled(self, enabled: bool):
        """启用/禁用PSD更新"""
        self.psd_update_enabled = enabled
        if not enabled and self.psd_curve:
            self.psd_curve.setData([], [])
        self.logger.info(f"PSD updates {'enabled' if enabled else 'disabled'}")

    def set_display_window(self, window_duration: float):
        """设置显示窗口长度"""
        self.time_display_window = max(0.1, window_duration)
        self.logger.info(f"Time display window set to {self.time_display_window}s")

    def set_update_interval(self, interval: float):
        """设置最小更新间隔"""
        self.min_update_interval = max(0.05, interval)  # 最小50ms
        self.logger.info(f"Update interval set to {self.min_update_interval*1000:.0f}ms")

    def get_statistics(self) -> Dict[str, Any]:
        """获取可视化统计信息"""
        return {
            'time_buffer_size': len(self.time_data_buffer),
            'time_update_enabled': self.time_update_enabled,
            'psd_update_enabled': self.psd_update_enabled,
            'display_window': self.time_display_window,
            'max_display_points': self.max_display_points,
            'update_interval': self.min_update_interval
        }