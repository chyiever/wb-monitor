# PCCP Wire Break Monitoring Software

## 项目概述

本项目是基于 `Python 3.9 + PyQt5 + PyQtGraph` 开发的 PCCP 断丝监测软件原型。

当前已完成 3 个模块的基础开发：

- `Tab1 / FIP`
  - 干涉仪数据 TCP 接收
  - 相位展开、滤波、降采样
  - 时域波形与 PSD 绘图
  - 相位数据存储
- `Tab2 / SigID`
  - 基于 Tab1 处理结果的短时特征提取
  - 阈值检测
  - 告警事件聚合
  - 特征显示与触发存储
- `Tab3 / eDAS`
  - DAS TCP 接收
  - DAS 数据包解析与二维矩阵恢复
  - DAS 指定通道时域图
  - FIP 对比曲线
  - DAS `space-time` 图
  - FIP/DAS 按 `comm_count` 对齐状态维护
  - 对齐后联合原始数据存储

当前 `Tab4 / SigLoc` 仍为占位状态，后续用于 DAS 特征分析、触发定位与结果导出。

## 当前开发状态

### Tab1 已完成

- TCP 服务端接收 FIP 数据
- 解析 `>II` 头部与大端 `int64` 定点数据
- 定点数据转 `float64`
- 相位展开、数字滤波、系统降采样
- 时域波形实时显示
- PSD 实时计算与绘图
- `NPZ` 格式相位数据存储
- 代码结构已整理到 `src/fip_tab1`

### Tab2 已完成

- 独立的 `src/fip_tab2` 多线程流水线
- 从 Tab1 接收处理后的下采样数据
- Tab2 自身可选带通预处理
- 短时特征提取
- 基于滑动基线与阈值因子的异常检测
- 连续异常窗口聚合为告警事件
- 最多 4 路特征曲线显示
- 告警表与触发存储

### Tab3 已完成最小闭环

- DAS 服务端独立启动与停止
- DAS 包头解析：
  - `comm_count`
  - `sample_rate_hz`
  - `channel_count`
  - `data_bytes`
  - `packet_duration_seconds`
- DAS 一维数据恢复为二维矩阵
- DAS 指定通道时域曲线显示
- FIP 对比曲线显示
- DAS `space-time` 图显示
- FIP / DAS 按 `comm_count` 对齐状态维护
- 缺失包区间记录
- DAS 10 秒无数据提醒
- 联合原始数据定时存储

## 当前代码结构

```text
wb-monitor/
├── config/
│   └── app_config.json
├── docs/
│   ├── 2026-3-11-声发射TCP通信丢包问题解决记录.md
│   ├── 2026-3-12-Tab1-声发射数据通信绘图功能开发问文档.md
│   ├── 2026-3-12-Tab2-声发射信号短时特征提取与异常检测（初步）.md
│   ├── 2026-03-13-Tab3-Tab4-详细设计.md
│   └── 2026-03-14-Tab3-DAS数据接收对齐与绘图开发日志.md
├── logs/
├── output/
├── resources/
├── src/
│   ├── alignment/
│   │   ├── __init__.py
│   │   ├── aligned_session_coordinator.py
│   │   └── aligned_types.py
│   ├── config/
│   │   ├── __init__.py
│   │   └── system_config.py
│   ├── das_tab3/
│   │   ├── __init__.py
│   │   ├── das_plot_worker.py
│   │   ├── das_tab3_manager.py
│   │   ├── das_tcp_server.py
│   │   └── das_types.py
│   ├── fip_tab1/
│   │   ├── __init__.py
│   │   ├── fip_plotter.py
│   │   ├── fip_tab1_manager.py
│   │   └── fip_tcp_server.py
│   ├── fip_tab2/
│   │   ├── __init__.py
│   │   ├── fip_detection_worker.py
│   │   ├── fip_feature_worker.py
│   │   ├── fip_plot_worker.py
│   │   ├── fip_tab2_manager.py
│   │   ├── fip_trigger_storage.py
│   │   └── fip_types.py
│   ├── processing/
│   │   ├── __init__.py
│   │   ├── downsampling.py
│   │   ├── phase_unwrap.py
│   │   ├── signal_filter.py
│   │   └── tab1_optimized_threads.py
│   ├── ui/
│   │   └── main_window.py
│   └── main.py
├── tools/
│   ├── simulate_das_client.py
│   └── validate_tab3_pipeline.py
├── requirements.txt
├── run.py
└── README.md
```

## 运行方式

### 安装依赖

```bash
pip install -r requirements.txt
```

### 启动程序

```bash
python run.py
```

也可以直接运行：

```bash
python src/main.py
```

### 调试参数

```bash
python run.py --debug
python run.py --log monitor.log
python run.py --config my_config.json
```

说明：

- `run.py --config` 目前仍是预留入口，尚未完整打通到主配置加载流程。

## 主要模块说明

### 1. Tab1 主链路

- 入口：`src/main.py`
- TCP 接收：`src/fip_tab1/fip_tcp_server.py`
- Tab1 线程管理：`src/fip_tab1/fip_tab1_manager.py`
- PSD 计算与绘图工具：`src/fip_tab1/fip_plotter.py`
- 通用预处理组件：
  - `src/processing/phase_unwrap.py`
  - `src/processing/signal_filter.py`
  - `src/processing/downsampling.py`

### 2. Tab2 主链路

- 管理器：`src/fip_tab2/fip_tab2_manager.py`
- 特征提取：`src/fip_tab2/fip_feature_worker.py`
- 阈值检测：`src/fip_tab2/fip_detection_worker.py`
- 特征显示缓存：`src/fip_tab2/fip_plot_worker.py`
- 触发存储：`src/fip_tab2/fip_trigger_storage.py`
- 共享数据类型：`src/fip_tab2/fip_types.py`

### 3. Tab3 主链路

- 管理器：`src/das_tab3/das_tab3_manager.py`
- DAS TCP 接收：`src/das_tab3/das_tcp_server.py`
- DAS 绘图数据准备：`src/das_tab3/das_plot_worker.py`
- DAS 数据类型：`src/das_tab3/das_types.py`
- FIP / DAS 对齐协调器：`src/alignment/aligned_session_coordinator.py`
- 对齐数据类型：`src/alignment/aligned_types.py`

### 4. 界面

- 主界面：`src/ui/main_window.py`

当前界面能力：

- `Tab1`
  - 通信设置
  - 预处理参数
  - 相位数据存储
  - 时域/PSD 显示与启停
- `Tab2`
  - 特征勾选
  - Tab2 独立预处理参数
  - 滑动窗与显示时长参数
  - 阈值因子配置
  - 告警清空与触发存储设置
- `Tab3`
  - DAS 通信设置
  - 包头实时状态
  - 对齐状态
  - 曲线 1 / 曲线 2 控制
  - `space-time` 图参数
  - 联合原始存储设置
  - DAS 独立启停

## 关键数据流

### Tab1

`LabVIEW TCP -> OptimizedTCPServer -> RawDataPacket -> 相位展开 -> 滤波 -> 降采样 -> 时域绘图 / PSD / Tab2 转发 / NPZ 存储`

### Tab2

`Tab1 processed_data -> FIPFeatureWorker -> FIPDetectionWorker / FIPFeaturePlotWorker / FIPTriggerStorageWorker`

### Tab3

`DAS TCP -> DASTCPServer -> DASRawPacket -> DASParsedPacket -> DASPlotWorker -> Tab3 UI`

同时：

`Tab1 processed_data + DAS parsed packet -> AlignedSessionCoordinator -> Tab3 对齐状态 / 联合原始存储`

## DAS 联调工具

### 1. DAS 模拟发送器

```bash
python tools/simulate_das_client.py --host 127.0.0.1 --port 3678
```

可选参数示例：

```bash
python tools/simulate_das_client.py --host 127.0.0.1 --port 3678 --channels 32 --sample-rate 4000 --packets 20
```

功能：

- 模拟 DAS 客户端连接到 Tab3 服务端
- 按协议发送连续数据包
- 生成基础正弦信号与周期性脉冲异常

### 2. Tab3 headless 验证脚本

```bash
python tools/validate_tab3_pipeline.py
```

功能：

- 启动 `DASTCPServer`
- 启动 `DASPlotWorker`
- 自动发送模拟 DAS 数据
- 验证收包、解析、二维矩阵恢复和绘图 payload 生成

当前本地验证结果：

```text
VALIDATION_OK packets_received=3 plot_payloads=3 last_shape=(16, 800) last_curve_points=2400
```

## 当前默认参数

- FIP 原始采样率：`1 MHz`
- Tab1 默认系统降采样倍数：`5`
- Tab1 默认有效采样率：`200 kHz`
- Tab1 时域显示数据：`downsampled_data[::2]`
- Tab1 默认时域显示采样率：`100 kHz`
- Tab2 默认启用特征：`short_energy`
- Tab2 默认阈值因子：`3.0`
- Tab2 默认触发存储：
  - pre-trigger：`1.0 s`
  - post-trigger：`3.0 s`
- Tab3 默认 DAS 端口：`3678`
- Tab3 默认联合原始存储路径：`D:/PCCP/FIPeDASDATA`
- Tab3 默认联合原始存储时间窗：`10.0 s`
- Tab3 默认对齐缓存保留时长：`10.0 s`

## 文档索引

- [docs/2026-3-11-声发射TCP通信丢包问题解决记录.md](/E:/codes/pccpHOST/wb-monitor/docs/2026-3-11-声发射TCP通信丢包问题解决记录.md)
- [docs/2026-3-12-Tab1-声发射数据通信绘图功能开发问文档.md](/E:/codes/pccpHOST/wb-monitor/docs/2026-3-12-Tab1-声发射数据通信绘图功能开发问文档.md)
- [docs/2026-3-12-Tab2-声发射信号短时特征提取与异常检测（初步）.md](/E:/codes/pccpHOST/wb-monitor/docs/2026-3-12-Tab2-声发射信号短时特征提取与异常检测（初步）.md)
- [docs/2026-03-13-Tab3-Tab4-详细设计.md](/E:/codes/pccpHOST/wb-monitor/docs/2026-03-13-Tab3-Tab4-详细设计.md)
- [docs/2026-03-14-Tab3-DAS数据接收对齐与绘图开发日志.md](/E:/codes/pccpHOST/wb-monitor/docs/2026-03-14-Tab3-DAS数据接收对齐与绘图开发日志.md)

## 已知现状

- `Tab1`、`Tab2`、`Tab3` 已具备基础运行能力
- `Tab4` 尚未开发
- `Tab3` 已完成 headless 链路验证，但尚未完成完整 GUI 联调验收
- `run.py --config` 尚未完整接入自定义配置文件加载

## 后续建议

- 补充 `tab3` 的 GUI 联调与人工验收记录
- 在 `tab3` 中补齐 `space-time` 图的色图、颜色栏、`vmin/vmax`
- 在 `tab4` 中复用对齐层实现补零时间窗提取、DAS 特征分析和事件定位
- 为 `tab1/tab2/tab3` 增加自动化冒烟测试
