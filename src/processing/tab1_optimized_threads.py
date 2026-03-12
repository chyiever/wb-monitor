"""
优化的Tab1线程架构 - 完全分离的多线程系统

核心设计原则：
1. 主线程：仅负责UI控制和TCP数据接收分发
2. 数据处理线程：相位展开、滤波、降采样
3. 时域绘图线程：独立的时域数据处理和绘制
4. PSD绘图线程：独立的PSD计算和绘制
5. 存储线程：独立的数据存储操作

优化目标：
- 主线程响应性最大化
- 各功能模块完全解耦
- 高效的线程间通信
- 最小化代码复杂度

Author: Claude
Date: 2026-03-12
"""

import time
import logging
import numpy as np
from queue import Queue, Empty, Full
from dataclasses import dataclass
from typing import Optional, Dict, Any
from PyQt5.QtCore import QThread, pyqtSignal, QObject, QTimer
from PyQt5.QtWidgets import QApplication
import pyqtgraph as pg


@dataclass
class RawDataPacket:
    """原始数据包"""
    timestamp: float
    phase_data: np.ndarray
    comm_count: int


@dataclass
class ProcessedData:
    """处理后的数据包"""
    timestamp: float
    unwrapped_data: np.ndarray  # 相位展开后的数据（用于存储）
    filtered_data: np.ndarray   # 滤波后的数据
    downsampled_data: np.ndarray  # 降采样后的数据（用于绘图）
    psd_data: np.ndarray  # PSD专用数据：相位展开后、未滤波，再按系统降采样抽取
    effective_rate: float
    comm_count: int


@dataclass
class StorageRequest:
    """存储请求"""
    data: np.ndarray
    comm_count: int
    timestamp: float
    data_type: str = "phase_unwrapped"


class DataProcessingThread(QThread):
    """数据处理线程 - 专注CPU密集型操作"""

    data_processed = pyqtSignal(object)  # ProcessedData

    def __init__(self, phase_unwrapper, signal_filter, downsampler):
        super().__init__()
        self.input_queue = Queue(maxsize=20)  # 增大队列避免丢包
        self.running = False

        # 处理组件
        self.phase_unwrapper = phase_unwrapper
        self.signal_filter = signal_filter
        self.downsampler = downsampler

        self.logger = logging.getLogger(f'{__name__}.DataProcessingThread')

    def add_raw_packet(self, packet: RawDataPacket) -> bool:
        """添加原始数据包，非阻塞"""
        try:
            if self.input_queue.full():
                # 丢弃最旧的数据包
                try:
                    discarded = self.input_queue.get_nowait()
                    self.logger.debug(f"Discarded old packet #{discarded.comm_count}")
                except Empty:
                    pass

            self.input_queue.put(packet, block=False)

            # 调试日志：记录数据包接收
            if packet.comm_count % 50 == 0:
                self.logger.info(f"DataProcessingThread received packet #{packet.comm_count}")

            return True
        except Full:
            self.logger.warning(f"Failed to queue packet #{packet.comm_count} - queue full")
            return False

    def run(self):
        """处理循环"""
        self.running = True
        self.logger.info("Data processing thread started")

        while self.running:
            try:
                packet = self.input_queue.get(timeout=0.1)
                processed = self._process_packet(packet)
                if processed:
                    self.data_processed.emit(processed)

            except Empty:
                continue
            except Exception as e:
                self.logger.error(f"Processing error: {e}")

    def _process_packet(self, packet: RawDataPacket) -> Optional[ProcessedData]:
        """处理单个数据包"""
        try:
            # 调试日志
            if packet.comm_count % 50 == 0:
                self.logger.info(f"Processing packet #{packet.comm_count}, data shape: {packet.phase_data.shape}")

            # 数据预处理
            phase_data = packet.phase_data
            if np.max(np.abs(phase_data)) > 5:
                phase_data = phase_data / np.pi

            # 相位展开
            unwrapped, _ = self.phase_unwrapper.unwrap_phase(phase_data)
            if len(unwrapped) == 0:
                self.logger.warning(f"Phase unwrapping failed for packet #{packet.comm_count}")
                return None

            # 滤波
            if self.signal_filter is not None:
                filtered, _ = self.signal_filter.apply_filter(unwrapped)
            else:
                filtered = unwrapped.copy()

            # 降采样
            downsampled, _ = self.downsampler.downsample(filtered)

            # PSD专用数据：使用相位展开后的未滤波数据（满足“PSD不走滤波器”要求）
            downsample_factor = max(1, self.downsampler.get_current_factor())
            psd_data = unwrapped[::downsample_factor]

            # 计算有效采样率
            effective_rate = 1000000.0 / self.downsampler.get_current_factor()

            # 调试日志
            if packet.comm_count % 50 == 0:
                self.logger.info(f"Packet #{packet.comm_count}: {len(unwrapped)}→{len(filtered)}→{len(downsampled)} samples")

            return ProcessedData(
                timestamp=packet.timestamp,
                unwrapped_data=unwrapped,
                filtered_data=filtered,
                downsampled_data=downsampled,
                psd_data=psd_data,
                effective_rate=effective_rate,
                comm_count=packet.comm_count
            )

        except Exception as e:
            self.logger.error(f"Error processing packet #{packet.comm_count}: {e}")
            return None

    def stop(self):
        """停止线程"""
        self.running = False
        self.logger.info("Data processing thread stopping")


class TimedomainPlotThread(QThread):
    """时域绘图线程"""

    plot_ready = pyqtSignal(np.ndarray, np.ndarray)  # timestamps, values

    def __init__(self):
        super().__init__()
        self.input_queue = Queue(maxsize=10)
        self.running = False
        self.enabled = True

        # 时域显示缓冲区
        self.data_buffer = []
        self.time_buffer = []
        self.window_duration = 1.0  # 显示窗口1秒
        self.update_interval = 5   # 每5个包更新一次
        self.packet_count = 0
        self.last_comm_count = None  # 用于丢弃重复/倒序数据包
        self.next_timestamp = 0.0    # 内部单调时间轴，避免外部时间戳抖动导致叠影

        self.logger = logging.getLogger(f'{__name__}.TimedomainPlotThread')

    def add_processed_data(self, data: ProcessedData):
        """添加处理后的数据"""
        if not self.enabled:
            return

        # 调试日志
        if data.comm_count % 50 == 0:
            self.logger.info(f"TimedomainPlotThread received processed packet #{data.comm_count}")

        try:
            if not self.input_queue.full():
                self.input_queue.put(data, block=False)
            else:
                self.logger.warning(f"TimePlotThread queue full, dropping packet #{data.comm_count}")
        except Full:
            pass  # 丢弃数据避免阻塞

    def set_enabled(self, enabled: bool):
        """启用/禁用时域绘图"""
        self.enabled = enabled
        if not enabled:
            # 清空缓冲区
            self.data_buffer.clear()
            self.time_buffer.clear()
            self.packet_count = 0
            self.last_comm_count = None
            self.next_timestamp = 0.0

    def set_window_duration(self, duration: float):
        """设置显示窗口时长"""
        self.window_duration = duration

    def run(self):
        """绘图循环"""
        self.running = True
        # 每次启动都重置状态，避免停启后残留历史时序
        self.data_buffer.clear()
        self.time_buffer.clear()
        self.packet_count = 0
        self.last_comm_count = None
        self.next_timestamp = 0.0
        self.logger.info("Timedomain plot thread started")

        while self.running:
            try:
                data = self.input_queue.get(timeout=0.2)
                if self.enabled:
                    self._process_time_data(data)

            except Empty:
                continue
            except Exception as e:
                self.logger.error(f"Time plot error: {e}")

    def _process_time_data(self, data: ProcessedData):
        """处理时域数据"""
        try:
            # 丢弃重复或倒序包，避免同一段数据重复绘制形成“多条轨迹”视觉效果
            if self.last_comm_count is not None and data.comm_count <= self.last_comm_count:
                self.logger.warning(
                    f"Skipping stale/duplicate packet in time plot thread: "
                    f"comm_count={data.comm_count}, last={self.last_comm_count}"
                )
                return

            # 调试日志
            if data.comm_count % 50 == 0:
                self.logger.info(f"TimedomainPlotThread processing packet #{data.comm_count}")

            # 时域显示降采样：200kHz -> 100kHz
            display_data = data.downsampled_data[::2]

            # 计算显示采样率和时间戳
            # 不能写死100kHz：effective_rate 会随前面板降采样倍数变化。
            # 若时间步长错误，会导致窗口内轨迹重叠，看起来像“多条曲线叠加”。
            display_sample_rate = max(data.effective_rate / 2.0, 1.0)
            dt = 1.0 / display_sample_rate

            # 使用内部单调时间轴，避免外部timestamp抖动/重复导致X轴回退或重叠。
            start_time = self.next_timestamp
            timestamps = start_time + np.arange(len(display_data)) * dt
            self.next_timestamp = start_time + len(display_data) * dt

            # 更新缓冲区
            self.data_buffer.extend(display_data)
            self.time_buffer.extend(timestamps)

            self.packet_count += 1
            self.last_comm_count = data.comm_count

            # 定期更新绘图（减少UI负载）
            if self.packet_count % self.update_interval == 0:
                self.logger.info(f"Updating time plot - packet count: {self.packet_count}")
                if data.comm_count % 50 == 0:
                    packet_span = len(display_data) * dt
                    self.logger.debug(
                        f"Time axis info - effective_rate={data.effective_rate:.1f}Hz, "
                        f"display_rate={display_sample_rate:.1f}Hz, span={packet_span:.4f}s"
                    )
                self._update_plot()

        except Exception as e:
            self.logger.error(f"Error processing time data for packet #{data.comm_count}: {e}")

    def _update_plot(self):
        """更新时域绘图"""
        try:
            if len(self.time_buffer) == 0:
                return

            # 保持显示窗口
            cutoff_time = self.time_buffer[-1] - self.window_duration
            keep_indices = [i for i, t in enumerate(self.time_buffer) if t >= cutoff_time]

            if keep_indices:
                start_idx = keep_indices[0]
                self.time_buffer = self.time_buffer[start_idx:]
                self.data_buffer = self.data_buffer[start_idx:]

                # 转换为相对时间
                times = np.array(self.time_buffer) - self.time_buffer[0]
                values = np.array(self.data_buffer)

                # 发射绘图信号
                self.plot_ready.emit(times, values)

        except Exception as e:
            self.logger.error(f"Error updating time plot: {e}")

    def stop(self):
        """停止线程"""
        self.running = False
        self.logger.info("Timedomain plot thread stopping")


class PSDPlotThread(QThread):
    """PSD绘图线程"""

    plot_ready = pyqtSignal(np.ndarray, np.ndarray)  # frequencies, psd_db

    def __init__(self, psd_calculator):
        super().__init__()
        self.input_queue = Queue(maxsize=5)
        self.running = False
        self.enabled = True

        self.psd_calculator = psd_calculator
        self.update_interval = 5  # 每5个包计算一次PSD
        self.packet_count = 0

        self.logger = logging.getLogger(f'{__name__}.PSDPlotThread')

    def add_processed_data(self, data: ProcessedData):
        """添加处理后的数据"""
        if not self.enabled:
            return

        try:
            if not self.input_queue.full():
                self.input_queue.put(data, block=False)
        except Full:
            pass

    def set_enabled(self, enabled: bool):
        """启用/禁用PSD绘图"""
        self.enabled = enabled
        if not enabled:
            self.packet_count = 0

    def run(self):
        """PSD计算循环"""
        self.running = True
        self.logger.info("PSD plot thread started")

        while self.running:
            try:
                data = self.input_queue.get(timeout=0.5)
                if self.enabled:
                    self.packet_count += 1
                    # 降低PSD计算频率
                    if self.packet_count % self.update_interval == 0:
                        self._calculate_psd(data)

            except Empty:
                continue
            except Exception as e:
                self.logger.error(f"PSD plot error: {e}")

    def _calculate_psd(self, data: ProcessedData):
        """计算PSD"""
        try:
            # 使用相位展开后、未滤波的数据计算PSD（满足需求）
            # 采样率与psd_data保持一致：effective_rate = ORIGINAL_SAMPLE_RATE / downsample_factor
            self.psd_calculator.sample_rate = data.effective_rate
            frequencies, psd = self.psd_calculator.compute_psd(data.psd_data)

            if len(frequencies) > 0:
                # 转换为dB
                psd_safe = np.maximum(psd, 1e-15)
                psd_db = np.clip(10 * np.log10(psd_safe), -200, 100)

                # 发射PSD绘图信号
                self.plot_ready.emit(frequencies, psd_db)

        except Exception as e:
            self.logger.error(f"Error calculating PSD: {e}")

    def stop(self):
        """停止线程"""
        self.running = False
        self.logger.info("PSD plot thread stopping")


class DataStorageThread(QThread):
    """数据存储线程"""

    def __init__(self, storage_path: str = "D:/PCCP/FIPdata"):
        super().__init__()
        self.input_queue = Queue(maxsize=50)  # 较大的存储队列
        self.running = False
        self.enabled = False

        self.storage_path = storage_path
        self.storage_interval = 10  # 每10个包存储一次
        self.packet_count = 0

        self.logger = logging.getLogger(f'{__name__}.DataStorageThread')

    def add_storage_request(self, request: StorageRequest):
        """添加存储请求"""
        if not self.enabled:
            return

        try:
            if not self.input_queue.full():
                self.input_queue.put(request, block=False)
        except Full:
            self.logger.warning("Storage queue full, dropping data")

    def set_enabled(self, enabled: bool):
        """启用/禁用存储"""
        self.enabled = enabled
        if enabled:
            self.packet_count = 0

    def set_storage_path(self, path: str):
        """设置存储路径"""
        self.storage_path = path

    def run(self):
        """存储循环"""
        self.running = True
        self.logger.info("Data storage thread started")

        while self.running:
            try:
                request = self.input_queue.get(timeout=0.2)
                if self.enabled:
                    self.packet_count += 1
                    # 控制存储频率
                    if self.packet_count % self.storage_interval == 0:
                        self._save_data(request)

            except Empty:
                continue
            except Exception as e:
                self.logger.error(f"Storage error: {e}")

    def _save_data(self, request: StorageRequest):
        """保存数据到文件"""
        try:
            from pathlib import Path
            from datetime import datetime

            # 确保存储目录存在
            base_path = Path(self.storage_path)
            base_path.mkdir(parents=True, exist_ok=True)

            # 生成文件名
            now = datetime.now()
            date_str = now.strftime("%Y%m%d")
            time_str = now.strftime("%H%M%S_%f")[:-3]
            filename = f"phase_data_{date_str}_{time_str}_#{request.comm_count:06d}.npz"
            file_path = base_path / filename

            # 保存NPZ格式
            np.savez_compressed(
                file_path,
                phase_data=request.data,
                comm_count=request.comm_count,
                timestamp=request.timestamp,
                sample_rate=1000000.0,  # 原始采样率
                data_info={
                    'type': request.data_type,
                    'length': len(request.data),
                    'save_time': now.isoformat()
                }
            )

            if self.packet_count % 100 == 0:
                self.logger.info(f"Saved data to {filename}")

        except Exception as e:
            self.logger.error(f"Error saving data: {e}")

    def stop(self):
        """停止线程"""
        self.running = False
        self.logger.info("Data storage thread stopping")


class OptimizedTab1ThreadManager(QObject):
    """优化的Tab1线程管理器"""

    def __init__(self, processors, psd_calculator):
        super().__init__()
        self.logger = logging.getLogger(f'{__name__}.OptimizedTab1ThreadManager')

        # 创建线程
        phase_unwrapper, signal_filter, downsampler = processors
        self.data_processor = DataProcessingThread(phase_unwrapper, signal_filter, downsampler)
        self.time_plotter = TimedomainPlotThread()
        self.psd_plotter = PSDPlotThread(psd_calculator)
        self.storage_thread = DataStorageThread()

        # 绘图控件引用
        self.time_plot_widget = None
        self.psd_plot_widget = None
        self.time_curve = None
        self.psd_curve = None

        # 设置信号连接
        self._setup_connections()

    def _setup_connections(self):
        """设置线程间信号连接"""
        # 数据处理完成 -> 分发到各线程
        self.data_processor.data_processed.connect(self._distribute_processed_data)

        # 绘图线程 -> UI更新（使用优化的信号机制）
        self.time_plotter.plot_ready.connect(self._update_time_plot)
        self.psd_plotter.plot_ready.connect(self._update_psd_plot)

    def set_plot_widgets(self, time_plot, psd_plot):
        """设置绘图控件"""
        self.time_plot_widget = time_plot
        self.psd_plot_widget = psd_plot

        # 清空所有现有的曲线避免重复
        if time_plot:
            time_plot.clear()  # 清空所有现有项目
            self.time_curve = time_plot.plot(pen='b', name='时域信号')

        if psd_plot:
            psd_plot.clear()  # 清空所有现有项目
            self.psd_curve = psd_plot.plot(pen='r', name='PSD')

    def _ensure_plot_curves(self):
        """确保绘图曲线对象可用（处理clear()后对象失效的情况）"""
        self.time_curve = self._ensure_single_curve(
            plot_widget=self.time_plot_widget,
            current_curve=self.time_curve,
            pen='b',
            name='时域信号'
        )

        self.psd_curve = self._ensure_single_curve(
            plot_widget=self.psd_plot_widget,
            current_curve=self.psd_curve,
            pen='r',
            name='PSD'
        )

    def _ensure_single_curve(self, plot_widget, current_curve, pen, name):
        """确保每个PlotWidget仅保留一条曲线，避免曲线不断叠加。"""
        if plot_widget is None:
            return None

        data_items = plot_widget.listDataItems()

        # 如果当前曲线不可用，优先复用现有第一条曲线，避免重复创建。
        if current_curve is None or current_curve not in data_items:
            current_curve = data_items[0] if data_items else None

        if current_curve is None:
            current_curve = plot_widget.plot(pen=pen, name=name)
            data_items = plot_widget.listDataItems()

        # 删除多余曲线，保证只剩一条可更新曲线。
        for item in list(data_items):
            if item is not current_curve:
                try:
                    plot_widget.removeItem(item)
                except Exception:
                    pass

        return current_curve

    def start(self):
        """启动所有线程"""
        # 清空绘图数据
        self._clear_plots()

        self.data_processor.start()
        self.time_plotter.start()
        self.psd_plotter.start()
        self.storage_thread.start()

        self.logger.info("All Tab1 threads started")

    def stop(self):
        """停止所有线程"""
        threads = [self.data_processor, self.time_plotter, self.psd_plotter, self.storage_thread]

        # 发送停止信号
        for thread in threads:
            thread.stop()

        # 等待线程结束
        for thread in threads:
            if thread.isRunning():
                thread.wait(3000)  # 最多等待3秒

        # 清空绘图数据
        self._clear_plots()

        self.logger.info("All Tab1 threads stopped")

    def _clear_plots(self):
        """清空所有绘图数据"""
        if QApplication.instance():
            if self.time_curve:
                QTimer.singleShot(0, lambda: self.time_curve.setData([], []))
            if self.psd_curve:
                QTimer.singleShot(0, lambda: self.psd_curve.setData([], []))

    def process_raw_packet(self, packet):
        """处理原始数据包 - 主线程调用"""
        success = self.data_processor.add_raw_packet(packet)

        # 每50个包记录一次，确认数据到达线程管理器
        if packet.comm_count % 50 == 0:
            self.logger.info(f"Tab1ThreadManager received packet #{packet.comm_count}, queued: {success}")

        return success

    def _distribute_processed_data(self, processed_data: ProcessedData):
        """分发处理后的数据到各线程"""
        # 调试日志
        if processed_data.comm_count % 50 == 0:
            self.logger.info(f"Distributing processed packet #{processed_data.comm_count}")

        # 发送到时域绘图线程
        self.time_plotter.add_processed_data(processed_data)

        # 发送到PSD绘图线程
        self.psd_plotter.add_processed_data(processed_data)

        # 发送到存储线程（仅存储相位展开后的数据）
        storage_request = StorageRequest(
            data=processed_data.unwrapped_data,
            comm_count=processed_data.comm_count,
            timestamp=processed_data.timestamp,
            data_type="phase_unwrapped"
        )
        self.storage_thread.add_storage_request(storage_request)

    def _update_time_plot(self, times, values):
        """更新时域绘图 - 线程安全的UI更新"""
        if not QApplication.instance():
            return

        self._ensure_plot_curves()

        # 调试：输出当前PlotWidget中的曲线项数量，便于定位重复曲线问题
        time_items_count = len(self.time_plot_widget.listDataItems()) if self.time_plot_widget else 0
        psd_items_count = len(self.psd_plot_widget.listDataItems()) if self.psd_plot_widget else 0
        self.logger.debug(
            f"PlotDataItems count - time: {time_items_count}, psd: {psd_items_count}"
        )

        if not self.time_curve:
            return

        try:
            self.time_curve.setData(times, values)
        except RuntimeError:
            # 曲线对象可能在外部clear()后失效，重建后重试一次
            self.time_curve = None
            self._ensure_plot_curves()
            if self.time_curve:
                self.time_curve.setData(times, values)
        except Exception as e:
            self.logger.error(f"Error updating time curve: {e}")

    def _update_psd_plot(self, frequencies, psd_db):
        """更新PSD绘图 - 线程安全的UI更新"""
        if not QApplication.instance():
            return

        self._ensure_plot_curves()
        if not self.psd_curve:
            return

        try:
            self.psd_curve.setData(frequencies, psd_db)
        except RuntimeError:
            # 曲线对象可能在外部clear()后失效，重建后重试一次
            self.psd_curve = None
            self._ensure_plot_curves()
            if self.psd_curve:
                self.psd_curve.setData(frequencies, psd_db)
        except Exception as e:
            self.logger.error(f"Error updating PSD curve: {e}")

    # 控制接口
    def toggle_time_plotting(self, enabled: bool):
        """控制时域绘图"""
        self.time_plotter.set_enabled(enabled)

        # 如果禁用绘图，清空现有曲线
        if not enabled and self.time_curve and QApplication.instance():
            QTimer.singleShot(0, lambda: self.time_curve.setData([], []))

    def toggle_psd_plotting(self, enabled: bool):
        """控制PSD绘图"""
        self.psd_plotter.set_enabled(enabled)

        # 如果禁用绘图，清空现有曲线
        if not enabled and self.psd_curve and QApplication.instance():
            QTimer.singleShot(0, lambda: self.psd_curve.setData([], []))

    def toggle_storage(self, enabled: bool):
        """控制数据存储"""
        self.storage_thread.set_enabled(enabled)

    def update_time_window(self, duration: float):
        """更新时域显示窗口"""
        self.time_plotter.set_window_duration(duration)

    def update_storage_path(self, path: str):
        """更新存储路径"""
        self.storage_thread.set_storage_path(path)

    def get_plot_status(self):
        """获取绘图状态（调试用）"""
        return {
            'time_curve_exists': self.time_curve is not None,
            'psd_curve_exists': self.psd_curve is not None,
            'time_plotting_enabled': self.time_plotter.enabled if hasattr(self.time_plotter, 'enabled') else 'unknown',
            'psd_plotting_enabled': self.psd_plotter.enabled if hasattr(self.psd_plotter, 'enabled') else 'unknown'
        }