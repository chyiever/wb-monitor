"""
`src` 包是 PCIe-7821 eDAS 上位机的核心源码入口。

当前工程采用相对集中的桌面应用结构：`main.py` 负责应用启动，`main_window.py` 负责界面与业务编排，`pcie7821_api.py` 负责 DLL 封装，`acquisition_thread.py` 负责采集线程，`data_saver.py` 负责异步落盘，`time_space_plot.py` 与 `spectrum_analyzer.py` 分别负责时空图和频谱分析，`tcp_tab3/` 负责 Tab3 的相位数据 TCP 通信链路。

该包的代码组织更偏向“单应用内聚”而不是“多层服务拆分”。后续做同类 DAS、DAQ 或高速实时显示项目时，可以把这里视为一个典型的“硬件采集 + GUI 显示 + 后台存储 + 外部通信”桌面架构参考样本。
"""
__version__ = "1.0.0"
__author__ = "PCIe-7821 Team"
