"""
Tab1线程架构优化验证脚本

用于测试优化后的线程架构的性能和稳定性。

测试内容：
1. 线程启动/停止测试
2. 数据处理性能测试
3. 线程间通信测试
4. UI响应性测试
5. 内存使用情况测试

Author: Claude
Date: 2026-03-12
"""

import sys
import time
import logging
import numpy as np
import threading
from dataclasses import dataclass
from typing import List, Dict, Any

# 设置测试环境路径
sys.path.insert(0, 'E:/codes/pccpHOST/wb-monitor/src')

# 导入必要的模块
try:
    from processing.tab1_optimized_threads import (
        OptimizedTab1ThreadManager,
        RawDataPacket,
        DataProcessingThread,
        TimedomainPlotThread,
        PSDPlotThread,
        DataStorageThread
    )
    from visualization.wave_plotter import PSDCalculator
    from processing.phase_unwrap import PhaseUnwrapper
    from processing.signal_filter import SignalFilter
    from processing.downsampling import Downsampler
    print("✓ 成功导入优化的线程模块")
except ImportError as e:
    print(f"✗ 导入模块失败: {e}")
    sys.exit(1)


@dataclass
class TestResults:
    """测试结果"""
    test_name: str
    success: bool
    duration: float
    message: str
    metrics: Dict[str, Any] = None


class Tab1ThreadOptimizationTester:
    """Tab1线程优化测试器"""

    def __init__(self):
        self.logger = logging.getLogger(__name__)
        self.setup_logging()

        self.results: List[TestResults] = []
        self.test_data_packets = []

        # 生成测试数据
        self._generate_test_data()

    def setup_logging(self):
        """设置日志"""
        logging.basicConfig(
            level=logging.INFO,
            format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            handlers=[
                logging.StreamHandler(),
                logging.FileHandler('thread_optimization_test.log')
            ]
        )

    def _generate_test_data(self):
        """生成测试用的数据包"""
        print("生成测试数据...")

        # 生成100个测试数据包
        for i in range(100):
            # 模拟相位数据：200,000个样本点 @ 1MHz
            phase_data = np.random.randn(200000) * 0.1 + np.sin(2 * np.pi * 1000 * np.linspace(0, 0.2, 200000))

            packet = RawDataPacket(
                timestamp=i * 0.2,  # 每0.2秒一个包
                phase_data=phase_data,
                comm_count=i
            )

            self.test_data_packets.append(packet)

        print(f"✓ 生成了 {len(self.test_data_packets)} 个测试数据包")

    def test_thread_startup_shutdown(self) -> TestResults:
        """测试线程启动和关闭"""
        print("测试1: 线程启动/关闭...")
        start_time = time.time()

        try:
            # 创建处理组件
            phase_unwrapper = PhaseUnwrapper()
            signal_filter = SignalFilter(sample_rate=1000000.0)
            downsampler = Downsampler(method='decimate', factor=5)
            psd_calculator = PSDCalculator(sample_rate=200000.0)

            # 创建线程管理器
            processors = (phase_unwrapper, signal_filter, downsampler)
            manager = OptimizedTab1ThreadManager(processors, psd_calculator)

            # 启动线程
            manager.start()
            time.sleep(2)  # 让线程运行2秒

            # 检查线程状态
            all_running = (
                manager.data_processor.isRunning() and
                manager.time_plotter.isRunning() and
                manager.psd_plotter.isRunning() and
                manager.storage_thread.isRunning()
            )

            # 停止线程
            manager.stop()
            time.sleep(1)  # 等待线程停止

            # 检查线程是否已停止
            all_stopped = (
                not manager.data_processor.isRunning() and
                not manager.time_plotter.isRunning() and
                not manager.psd_plotter.isRunning() and
                not manager.storage_thread.isRunning()
            )

            duration = time.time() - start_time
            success = all_running and all_stopped

            metrics = {
                'startup_successful': all_running,
                'shutdown_successful': all_stopped,
                'total_duration': duration
            }

            message = f"启动成功: {all_running}, 关闭成功: {all_stopped}"

            return TestResults("线程启动/关闭", success, duration, message, metrics)

        except Exception as e:
            duration = time.time() - start_time
            return TestResults("线程启动/关闭", False, duration, f"异常: {e}")

    def test_data_processing_performance(self) -> TestResults:
        """测试数据处理性能"""
        print("测试2: 数据处理性能...")
        start_time = time.time()

        try:
            # 创建处理组件
            phase_unwrapper = PhaseUnwrapper()
            signal_filter = SignalFilter(sample_rate=1000000.0)
            signal_filter.design_filter('bandpass', (100, 10000), 4)
            downsampler = Downsampler(method='decimate', factor=5)
            psd_calculator = PSDCalculator(sample_rate=200000.0)

            # 创建线程管理器
            processors = (phase_unwrapper, signal_filter, downsampler)
            manager = OptimizedTab1ThreadManager(processors, psd_calculator)
            manager.start()

            # 发送测试数据包
            processed_count = 0
            failed_count = 0

            processing_start = time.time()

            for packet in self.test_data_packets[:50]:  # 测试50个包
                success = manager.process_raw_packet(packet)
                if success:
                    processed_count += 1
                else:
                    failed_count += 1

                time.sleep(0.01)  # 模拟实际的数据包间隔

            # 等待处理完成
            time.sleep(3)

            processing_duration = time.time() - processing_start
            manager.stop()

            duration = time.time() - start_time
            success_rate = processed_count / len(self.test_data_packets[:50])
            throughput = processed_count / processing_duration  # 包/秒

            success = success_rate > 0.95 and throughput > 10  # 成功率>95%, 吞吐量>10包/秒

            metrics = {
                'processed_packets': processed_count,
                'failed_packets': failed_count,
                'success_rate': success_rate,
                'throughput_pps': throughput,
                'processing_duration': processing_duration
            }

            message = f"处理包数: {processed_count}, 成功率: {success_rate:.2%}, 吞吐量: {throughput:.1f} 包/秒"

            return TestResults("数据处理性能", success, duration, message, metrics)

        except Exception as e:
            duration = time.time() - start_time
            return TestResults("数据处理性能", False, duration, f"异常: {e}")

    def test_thread_communication(self) -> TestResults:
        """测试线程间通信"""
        print("测试3: 线程间通信...")
        start_time = time.time()

        try:
            # 创建单个线程测试
            phase_unwrapper = PhaseUnwrapper()
            signal_filter = SignalFilter(sample_rate=1000000.0)
            downsampler = Downsampler(method='decimate', factor=5)

            # 测试数据处理线程
            data_processor = DataProcessingThread(phase_unwrapper, signal_filter, downsampler)

            received_signals = []

            def signal_handler(processed_data):
                received_signals.append(processed_data)

            data_processor.data_processed.connect(signal_handler)
            data_processor.start()

            # 发送几个测试包
            for packet in self.test_data_packets[:5]:
                data_processor.add_raw_packet(packet)

            time.sleep(2)  # 等待处理
            data_processor.stop()

            duration = time.time() - start_time
            success = len(received_signals) > 0

            metrics = {
                'signals_received': len(received_signals),
                'expected_signals': min(5, len(self.test_data_packets))
            }

            message = f"接收到 {len(received_signals)} 个处理完成信号"

            return TestResults("线程间通信", success, duration, message, metrics)

        except Exception as e:
            duration = time.time() - start_time
            return TestResults("线程间通信", False, duration, f"异常: {e}")

    def test_memory_usage(self) -> TestResults:
        """测试内存使用情况"""
        print("测试4: 内存使用情况...")
        start_time = time.time()

        try:
            import psutil
            process = psutil.Process()
            initial_memory = process.memory_info().rss / 1024 / 1024  # MB

            # 创建线程管理器并运行
            phase_unwrapper = PhaseUnwrapper()
            signal_filter = SignalFilter(sample_rate=1000000.0)
            downsampler = Downsampler(method='decimate', factor=5)
            psd_calculator = PSDCalculator(sample_rate=200000.0)

            processors = (phase_unwrapper, signal_filter, downsampler)
            manager = OptimizedTab1ThreadManager(processors, psd_calculator)
            manager.start()

            # 处理大量数据包
            for packet in self.test_data_packets:
                manager.process_raw_packet(packet)
                time.sleep(0.005)  # 加快处理速度

            time.sleep(3)  # 等待处理完成

            peak_memory = process.memory_info().rss / 1024 / 1024  # MB
            memory_increase = peak_memory - initial_memory

            manager.stop()

            final_memory = process.memory_info().rss / 1024 / 1024  # MB
            memory_after_cleanup = final_memory - initial_memory

            duration = time.time() - start_time

            # 内存增长应该合理（<500MB），清理后内存增长应该很小（<100MB）
            success = memory_increase < 500 and memory_after_cleanup < 100

            metrics = {
                'initial_memory_mb': initial_memory,
                'peak_memory_mb': peak_memory,
                'final_memory_mb': final_memory,
                'memory_increase_mb': memory_increase,
                'memory_after_cleanup_mb': memory_after_cleanup
            }

            message = f"内存使用: 初始 {initial_memory:.1f}MB, 峰值 {peak_memory:.1f}MB (+{memory_increase:.1f}MB), 最终 {final_memory:.1f}MB"

            return TestResults("内存使用情况", success, duration, message, metrics)

        except ImportError:
            duration = time.time() - start_time
            return TestResults("内存使用情况", False, duration, "psutil模块未安装，跳过内存测试")
        except Exception as e:
            duration = time.time() - start_time
            return TestResults("内存使用情况", False, duration, f"异常: {e}")

    def run_all_tests(self):
        """运行所有测试"""
        print("=" * 60)
        print("Tab1线程架构优化验证测试")
        print("=" * 60)

        tests = [
            self.test_thread_startup_shutdown,
            self.test_data_processing_performance,
            self.test_thread_communication,
            self.test_memory_usage
        ]

        for test_func in tests:
            try:
                result = test_func()
                self.results.append(result)

                status = "✓ 通过" if result.success else "✗ 失败"
                print(f"{status} | {result.test_name} | {result.duration:.2f}s | {result.message}")

                if result.metrics:
                    for key, value in result.metrics.items():
                        print(f"    {key}: {value}")

            except Exception as e:
                print(f"✗ 测试异常 | {test_func.__name__} | {e}")

            print()

        self._print_summary()

    def _print_summary(self):
        """打印测试总结"""
        print("=" * 60)
        print("测试总结")
        print("=" * 60)

        total_tests = len(self.results)
        passed_tests = sum(1 for r in self.results if r.success)
        failed_tests = total_tests - passed_tests

        print(f"总测试数: {total_tests}")
        print(f"通过: {passed_tests}")
        print(f"失败: {failed_tests}")
        print(f"成功率: {passed_tests/total_tests:.1%}")

        total_duration = sum(r.duration for r in self.results)
        print(f"总用时: {total_duration:.2f}秒")

        if failed_tests > 0:
            print("\n失败的测试:")
            for result in self.results:
                if not result.success:
                    print(f"  - {result.test_name}: {result.message}")

        print("\n" + "=" * 60)

        if passed_tests == total_tests:
            print("🎉 所有测试通过！Tab1线程架构优化成功！")
        else:
            print("⚠️  部分测试失败，需要进一步优化。")


def main():
    """主测试函数"""
    tester = Tab1ThreadOptimizationTester()
    tester.run_all_tests()


if __name__ == "__main__":
    main()