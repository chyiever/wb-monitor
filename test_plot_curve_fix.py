"""
时域绘图曲线重复问题修复验证脚本

此脚本用于验证时域绘图曲线是否还会重复创建。

测试内容：
1. 检查绘图控件初始状态
2. 模拟多次启动/停止监测
3. 验证曲线数量是否正确
4. 检查绘图更新是否正常

Author: Claude
Date: 2026-03-12
"""

import sys
import time
import numpy as np
from PyQt5.QtWidgets import QApplication
from PyQt5.QtCore import QTimer

# 设置测试环境路径
sys.path.insert(0, 'E:/codes/pccpHOST/wb-monitor/src')

try:
    from ui.main_window import MainWindow
    from processing.tab1_optimized_threads import OptimizedTab1ThreadManager
    from processing.phase_unwrap import PhaseUnwrapper
    from processing.signal_filter import SignalFilter
    from processing.downsampling import Downsampler
    from visualization.wave_plotter import PSDCalculator
    print("✓ 成功导入模块")
except ImportError as e:
    print(f"✗ 导入模块失败: {e}")
    sys.exit(1)


def test_plot_curve_creation():
    """测试绘图曲线创建和管理"""
    print("=" * 60)
    print("时域绘图曲线管理测试")
    print("=" * 60)

    # 创建Qt应用
    app = QApplication(sys.argv)

    try:
        # 1. 创建主窗口
        print("1. 创建主窗口...")
        main_window = MainWindow()

        # 检查初始绘图控件状态
        time_items_initial = main_window.time_plot.listDataItems()
        psd_items_initial = main_window.psd_plot.listDataItems()
        print(f"   初始时域曲线数量: {len(time_items_initial)}")
        print(f"   初始PSD曲线数量: {len(psd_items_initial)}")

        # 2. 创建线程管理器
        print("\n2. 创建线程管理器...")
        phase_unwrapper = PhaseUnwrapper()
        signal_filter = SignalFilter(sample_rate=1000000.0)
        downsampler = Downsampler(method='decimate', factor=5)
        psd_calculator = PSDCalculator(sample_rate=200000.0)

        processors = (phase_unwrapper, signal_filter, downsampler)
        manager = OptimizedTab1ThreadManager(processors, psd_calculator)

        # 3. 设置绘图控件（这是关键步骤）
        print("\n3. 设置绘图控件...")
        manager.set_plot_widgets(main_window.time_plot, main_window.psd_plot)

        # 检查设置后的曲线数量
        time_items_after_setup = main_window.time_plot.listDataItems()
        psd_items_after_setup = main_window.psd_plot.listDataItems()
        print(f"   设置后时域曲线数量: {len(time_items_after_setup)}")
        print(f"   设置后PSD曲线数量: {len(psd_items_after_setup)}")

        # 4. 多次启动停止测试
        print("\n4. 多次启动/停止测试...")
        for i in range(3):
            print(f"\n   第 {i+1} 次启动/停止:")

            # 启动线程
            manager.start()
            time.sleep(0.5)  # 短暂等待

            # 检查启动后曲线数量
            time_items_running = main_window.time_plot.listDataItems()
            psd_items_running = main_window.psd_plot.listDataItems()
            print(f"   启动后时域曲线数量: {len(time_items_running)}")
            print(f"   启动后PSD曲线数量: {len(psd_items_running)}")

            # 停止线程
            manager.stop()
            time.sleep(0.5)  # 等待停止完成

            # 检查停止后曲线数量
            time_items_stopped = main_window.time_plot.listDataItems()
            psd_items_stopped = main_window.psd_plot.listDataItems()
            print(f"   停止后时域曲线数量: {len(time_items_stopped)}")
            print(f"   停止后PSD曲线数量: {len(psd_items_stopped)}")

        # 5. 测试绘图更新
        print("\n5. 测试绘图更新...")
        manager.start()
        time.sleep(0.2)

        # 模拟绘图更新
        test_times = np.linspace(0, 1, 1000)
        test_values = np.sin(2 * np.pi * 10 * test_times)

        # 检查更新前曲线数量
        time_items_before_update = main_window.time_plot.listDataItems()
        print(f"   更新前时域曲线数量: {len(time_items_before_update)}")

        # 执行几次更新
        for j in range(5):
            manager._update_time_plot(test_times, test_values + j * 0.1)
            app.processEvents()  # 处理Qt事件
            time.sleep(0.1)

        # 检查更新后曲线数量
        time_items_after_update = main_window.time_plot.listDataItems()
        print(f"   更新后时域曲线数量: {len(time_items_after_update)}")

        manager.stop()

        # 6. 结果评估
        print("\n" + "=" * 60)
        print("测试结果评估:")

        expected_curves = 1  # 应该只有1条曲线
        final_time_curves = len(time_items_after_update)
        final_psd_curves = len(psd_items_stopped)

        time_test_passed = final_time_curves == expected_curves
        psd_test_passed = final_psd_curves == expected_curves

        print(f"时域曲线测试: {'✓ 通过' if time_test_passed else '✗ 失败'}")
        print(f"  预期曲线数: {expected_curves}, 实际曲线数: {final_time_curves}")

        print(f"PSD曲线测试: {'✓ 通过' if psd_test_passed else '✗ 失败'}")
        print(f"  预期曲线数: {expected_curves}, 实际曲线数: {final_psd_curves}")

        overall_success = time_test_passed and psd_test_passed
        print(f"\n总体结果: {'🎉 修复成功' if overall_success else '❌ 仍有问题'}")

        if not overall_success:
            print("\n建议检查:")
            if not time_test_passed:
                print("- 时域绘图曲线管理逻辑")
            if not psd_test_passed:
                print("- PSD绘图曲线管理逻辑")

        return overall_success

    except Exception as e:
        print(f"测试过程中发生异常: {e}")
        import traceback
        traceback.print_exc()
        return False

    finally:
        app.quit()


def main():
    """主测试函数"""
    print("时域绘图曲线重复问题修复验证")
    print("正在进行测试...")

    success = test_plot_curve_creation()

    print("\n" + "=" * 60)
    if success:
        print("✅ 修复验证成功！时域绘图曲线不再重复创建。")
    else:
        print("❌ 修复验证失败，需要进一步调试。")
    print("=" * 60)


if __name__ == "__main__":
    main()