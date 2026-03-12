"""
多线程系统协调器

统一管理所有线程的生命周期和数据流。

Author: Claude
Date: 2026-03-12
"""

import logging
from typing import Dict, Any, Optional
from PyQt5.QtCore import QObject, pyqtSignal

from .multi_thread_system import (
    RawDataPacket,
    DataProcessingThread,
    TimeDomainPlotThread,
    PSDPlotThread,
    Tab2UpdateThread,
    StorageThread
)


class MultiThreadCoordinator(QObject):
    """多线程系统协调器"""

    # 统计信号
    system_stats_updated = pyqtSignal(dict)
    thread_error = pyqtSignal(str, str)  # thread_name, error_message

    def __init__(self):
        super().__init__()
        self.logger = logging.getLogger(__name__ + '.MultiThreadCoordinator')

        # 创建所有线程
        self.data_processing_thread = DataProcessingThread()
        self.time_plot_thread = TimeDomainPlotThread()
        self.psd_plot_thread = PSDPlotThread()
        self.tab2_update_thread = Tab2UpdateThread()
        self.storage_thread = StorageThread()

        # 线程状态
        self.threads_running = False

        # 统计信息
        self.stats = {
            'packets_received': 0,
            'packets_processed': 0,
            'time_plots_updated': 0,
            'psd_plots_updated': 0,
            'tab2_updates': 0,
            'storage_operations': 0
        }

        # 设置线程连接
        self._setup_thread_connections()

        self.logger.info("Multi-thread coordinator initialized")

    def _setup_thread_connections(self):
        """设置线程间连接"""

        # 数据处理线程 -> 其他线程
        self.data_processing_thread.data_processed.connect(self._distribute_processed_data)
        self.data_processing_thread.processing_error.connect(
            lambda err: self.thread_error.emit("DataProcessing", err)
        )

        # 绘图线程信号
        self.time_plot_thread.plot_updated.connect(
            lambda: self._update_stats('time_plots_updated')
        )
        self.time_plot_thread.plot_error.connect(
            lambda err: self.thread_error.emit("TimePlot", err)
        )

        self.psd_plot_thread.plot_updated.connect(
            lambda: self._update_stats('psd_plots_updated')
        )
        self.psd_plot_thread.plot_error.connect(
            lambda err: self.thread_error.emit("PSDPlot", err)
        )

        # Tab2更新信号
        self.tab2_update_thread.features_updated.connect(
            lambda: self._update_stats('tab2_updates')
        )

        # 存储线程信号
        self.storage_thread.storage_completed.connect(
            lambda: self._update_stats('storage_operations')
        )
        self.storage_thread.storage_error.connect(
            lambda err: self.thread_error.emit("Storage", err)
        )

    def set_processors(self, phase_unwrapper, signal_filter, downsampler,
                      feature_calculator, threshold_detector, psd_calculator):
        """设置处理组件"""

        # 数据处理线程
        self.data_processing_thread.set_processors(
            phase_unwrapper, signal_filter, downsampler,
            feature_calculator, threshold_detector
        )

        # PSD绘图线程
        self.psd_plot_thread.set_psd_calculator(psd_calculator)

        self.logger.info("Processors configured for all threads")

    def set_plot_widgets(self, time_plot, psd_plot):
        """设置绘图控件"""

        # 获取绘图曲线
        time_curves = time_plot.listDataItems()
        time_curve = time_curves[0] if time_curves else time_plot.plot(pen='b', name='时域信号')

        psd_curves = psd_plot.listDataItems()
        psd_curve = psd_curves[0] if psd_curves else psd_plot.plot(pen='r', name='PSD')

        # 设置到相应线程
        self.time_plot_thread.set_plot_widget(time_plot, time_curve)
        self.psd_plot_thread.set_plot_widget(psd_plot, psd_curve)

        self.logger.info("Plot widgets configured")

    def set_tab2_ui_connections(self, main_window):
        """设置Tab2界面连接"""
        try:
            # 连接Tab2更新信号到界面更新方法
            self.tab2_update_thread.features_updated.connect(
                lambda features: main_window.update_feature_displays({'features': features})
            )
            self.tab2_update_thread.detections_updated.connect(
                main_window.add_detection_results
            )

            self.logger.info("Tab2 UI connections configured")

        except Exception as e:
            self.logger.error(f"Error setting Tab2 connections: {e}")

    def start_all_threads(self):
        """启动所有线程"""
        try:
            if self.threads_running:
                self.logger.warning("Threads are already running")
                return

            # 启动所有线程
            self.data_processing_thread.start()
            self.time_plot_thread.start()
            self.psd_plot_thread.start()
            self.tab2_update_thread.start()
            self.storage_thread.start()

            self.threads_running = True

            self.logger.info("All threads started successfully")

        except Exception as e:
            self.logger.error(f"Error starting threads: {e}")
            self.thread_error.emit("System", f"Failed to start threads: {e}")

    def stop_all_threads(self):
        """停止所有线程"""
        try:
            if not self.threads_running:
                return

            self.logger.info("Stopping all threads...")

            # 停止所有线程
            threads = [
                self.data_processing_thread,
                self.time_plot_thread,
                self.psd_plot_thread,
                self.tab2_update_thread,
                self.storage_thread
            ]

            for thread in threads:
                thread.stop_processing()

            # 等待线程结束
            for i, thread in enumerate(threads):
                if thread.isRunning():
                    if not thread.wait(3000):  # 3秒超时
                        self.logger.warning(f"Thread {i} did not quit gracefully, terminating...")
                        thread.terminate()
                        thread.wait()

            self.threads_running = False

            self.logger.info("All threads stopped")

        except Exception as e:
            self.logger.error(f"Error stopping threads: {e}")

    def process_raw_data(self, packet) -> bool:
        """处理原始数据包"""
        try:
            # 创建原始数据包
            raw_packet = RawDataPacket(
                packet_id=packet.comm_count,
                timestamp=packet.timestamp,
                phase_data=packet.phase_data,
                comm_count=packet.comm_count
            )

            # 发送到数据处理线程
            success = self.data_processing_thread.add_data(raw_packet)

            if success:
                self.stats['packets_received'] += 1

            return success

        except Exception as e:
            self.logger.error(f"Error processing raw data: {e}")
            return False

    def _distribute_processed_data(self, processed_data):
        """分发处理后的数据到各个线程"""
        try:
            self.stats['packets_processed'] += 1

            # 发送到时域绘图线程
            self.time_plot_thread.add_data(processed_data)

            # 发送到PSD绘图线程（每5个包一次）
            if processed_data.packet_id % 5 == 0:
                self.psd_plot_thread.add_data(processed_data)

            # 发送到Tab2更新线程
            if processed_data.features or processed_data.detections:
                self.tab2_update_thread.add_data(processed_data)

            # 发送到存储线程
            self.storage_thread.add_data(processed_data)

        except Exception as e:
            self.logger.error(f"Error distributing processed data: {e}")

    def _update_stats(self, stat_name):
        """更新统计信息"""
        try:
            self.stats[stat_name] += 1

            # 每100次更新发送一次统计信息
            if sum(self.stats.values()) % 100 == 0:
                self.system_stats_updated.emit(self.stats.copy())

        except Exception as e:
            self.logger.error(f"Error updating stats: {e}")

    def set_storage_settings(self, enabled: bool, path: str):
        """设置存储参数"""
        self.storage_thread.set_storage_settings(enabled, path)

    def set_time_plot_enabled(self, enabled: bool):
        """启用/禁用时域绘图"""
        # 这可以通过控制是否发送数据到时域绘图线程来实现
        pass

    def set_psd_plot_enabled(self, enabled: bool):
        """启用/禁用PSD绘图"""
        # 这可以通过控制是否发送数据到PSD绘图线程来实现
        pass

    def get_system_statistics(self) -> Dict[str, Any]:
        """获取系统统计信息"""
        return {
            **self.stats,
            'threads_running': self.threads_running,
            'data_processing_queue': self.data_processing_thread.input_queue.qsize() if self.data_processing_thread else 0,
            'time_plot_queue': self.time_plot_thread.input_queue.qsize() if self.time_plot_thread else 0,
            'psd_plot_queue': self.psd_plot_thread.input_queue.qsize() if self.psd_plot_thread else 0,
        }