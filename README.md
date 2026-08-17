# PCCP Wire Break Monitoring Software

## 项目概述

本项目是基于 `Python 3.9 + PyQt5 + PyQtGraph` 开发的 PCCP 断丝监测软件原型。

当前界面按现场联调工作流重组为 4 个 tab：

- `Tab1 / View`
  - 统一观察 FIP 和 eDAS 时域曲线
  - Curve1/Curve2 可独立选择 `Off`、`DAS Channel`、`FIP1`、`FIP2`
  - PSD1/PSD2 分别位于对应曲线右侧，使用 Welch 法计算
  - 显示 eDAS `space-time` 图和色标
  - View 不可见时跳过可见图件刷新，降低长时间运行后的 UI 压力
- `Tab2 / Data`
  - 集中 FIP/eDAS 通信启动、监听参数、连接状态和统一通信统计
  - 统一显示 `接收包`、`缺包/失败`、`丢包率`、`最近Comm`
  - 显示 FIP/eDAS 同序号包接收时间差，方向为 `FIP - eDAS`
  - 提供 FIP、eDAS 和 FIP+eDAS 联合存储控制
- `Tab3 / 检测`
  - 保留原 Tab2 的短时特征提取、阈值检测、告警事件和触发存储能力
  - 默认不启动，避免无需求时占用 CPU
- `Tab4 / Setting`
  - 管理 GUI 字体、图件标题、坐标轴标签和刻度字体
  - 全局显示设置保存后需重启软件生效

FIP/eDAS 同步时间戳取自 TCP 接收线程“完整包体接收完成”的时刻；`首包时间差（FIP-eDAS）` 一旦形成会固定保存，不再随滑动缓存裁剪变化。

## 当前开发状态

### FIP 主链路已完成

- TCP 服务端接收 FIP 数据
- 解析 `>II` 头部与大端 `int64` 定点数据
- 定点数据转 `float64`
- 相位输入异常值修复与归一化/工程量输入判别
- 相位展开、数字滤波、系统降采样
- FIP1/FIP2 曲线显示、PSD、Data 页统计和存储
- `NPZ` 格式相位数据存储
- 代码结构已整理到 `src/fip_tab1`

### eDAS 主链路已完成

- TCP 服务端接收 eDAS 数据
- 解析 `comm_count`、`sample_rate_hz`、`channel_count`、`data_bytes`、`packet_duration_seconds`
- DAS 一维数据恢复为二维矩阵
- 指定通道时域曲线显示
- eDAS `space-time` 图显示
- 缺失包统计、最近 Comm 显示和 Data 页统一通信统计
- eDAS 独立原始数据存储

### FIP/eDAS 联调能力已完成

- Data 页同时启动/停止 FIP 和 eDAS 通信
- FIP/eDAS 通信指示灯和存储指示灯
- FIP/eDAS 按 `comm_count` 配对的同步时间差显示
- 首包时间差固定保存，最新值和平均值增量更新
- FIP/eDAS 对齐状态维护和联合原始数据定时存储

### 检测页已完成

- 独立的 `src/fip_tab2` 多线程流水线
- 从 FIP 主链路接收处理后的下采样数据
- 检测页自身可选带通预处理
- 短时特征提取
- 基于滑动基线与阈值因子的异常检测
- 连续异常窗口聚合为告警事件
- 最多 4 路特征曲线显示
- 告警表与触发存储

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
python run.py --log logs/monitor.log
python run.py --debug --log logs/tab3_debug.log
python run.py --config my_config.json
```

说明：

- `python run.py --debug` 会把全局日志级别切到 `DEBUG`，用于联调阶段定位 Tab3 数据链路问题。
- `python run.py --debug --log logs/tab3_debug.log` 会把详细日志写入单独文件，便于和常规运行日志分开保存。
- `--log` 支持相对路径和绝对路径；相对路径以当前运行目录为基准。
- `run.py --config` 目前仍是预留入口，尚未完整打通到主配置加载流程。

Tab3 debug 日志节点统一使用 `TAB3_NODE` 前缀，重点节点包括：

- `main.start` / `main.stop` / `main.sync_settings`：主控启停和参数同步。
- `das_tcp.header` / `das_tcp.packet` / `das_tcp.stats`：DAS TCP 包头、完整包接收、真实区间包率、吞吐、接收耗时和缺包统计。
- `manager.fip_packet` / `manager.raw_packet` / `manager.parse`：FIP 转发、DAS 解析、对齐协调、绘图和存储路由。
- `plot_worker.enqueue` / `plot_worker.payload` / `plot_worker.stats`：绘图队列、丢旧保新、Space-Time 滚动缓存、处理耗时和队列峰值。
- `ui.fip_curve` / `ui.das_payload`：主线程曲线和 Space-Time 绘制耗时，用于定位鼠标卡顿或界面刷新延迟。
- `storage.joint_*` / `storage.edas_*`：联合存储和 eDAS-only 写盘入队、写盘耗时、文件切换和队列丢弃。

## 主要模块说明

### 1. FIP 主链路

- 入口：`src/main.py`
- TCP 接收：`src/fip_tab1/fip_tcp_server.py`
- Tab1 线程管理：`src/fip_tab1/fip_tab1_manager.py`
- PSD 计算与绘图工具：`src/fip_tab1/fip_plotter.py`
- 通用预处理组件：
  - `src/processing/phase_unwrap.py`
  - `src/processing/signal_filter.py`
  - `src/processing/downsampling.py`

### 2. 检测主链路

- 管理器：`src/fip_tab2/fip_tab2_manager.py`
- 特征提取：`src/fip_tab2/fip_feature_worker.py`
- 阈值检测：`src/fip_tab2/fip_detection_worker.py`
- 特征显示缓存：`src/fip_tab2/fip_plot_worker.py`
- 触发存储：`src/fip_tab2/fip_trigger_storage.py`
- 共享数据类型：`src/fip_tab2/fip_types.py`

### 3. eDAS 与联合存储主链路

- 管理器：`src/das_tab3/das_tab3_manager.py`
- DAS TCP 接收：`src/das_tab3/das_tcp_server.py`
- DAS 绘图数据准备：`src/das_tab3/das_plot_worker.py`
- DAS 数据类型：`src/das_tab3/das_types.py`
- FIP / DAS 对齐协调器：`src/alignment/aligned_session_coordinator.py`
- 对齐数据类型：`src/alignment/aligned_types.py`

### 4. 界面

- 主界面：`src/ui/main_window.py`

当前界面能力：

- `View`
  - Curve1/Curve2 时域图
  - PSD1/PSD2
  - eDAS Space-Time
  - FIP/eDAS 曲线源、滤波、刷新、点数、坐标轴和色标设置
- `Data`
  - FIP/eDAS 通信控制
  - FIP/eDAS 监听参数
  - 统一通信统计：状态、接收包、缺包/失败、丢包率、最近 Comm
  - 时间同步检验：首包、最新同序号和平均 `FIP-eDAS` 时间差
  - FIP、eDAS 和联合存储控制
- `Tab3 / 检测`
  - 特征勾选
  - 检测独立预处理参数
  - 滑动窗与显示时长参数
  - 阈值因子配置
  - 告警清空与触发存储设置
- `Setting`
  - GUI 和图件字体设置
  - 保存全局显示设置，重启软件后生效

## 关键数据流

### FIP 主链路

`LabVIEW TCP -> OptimizedTCPServer -> RawDataPacket -> 相位输入校验/修复 -> 相位展开 -> 滤波 -> 降采样 -> View 曲线/PSD -> 检测页转发 / NPZ 存储`

### 检测主链路

`FIP processed_data -> FIPFeatureWorker -> FIPDetectionWorker / FIPFeaturePlotWorker / FIPTriggerStorageWorker`

### eDAS 主链路

`DAS TCP -> DASTCPServer -> DASRawPacket -> DASParsedPacket -> DASPlotWorker -> View 曲线 / Space-Time`

同时：

`FIP processed_data + DAS parsed packet -> AlignedSessionCoordinator -> Data 对齐状态 / 联合原始存储`

### 时间同步检验

`FIP DataPacket.receive_timestamp + DASRawPacket.receive_timestamp -> MainWindow 按 comm_count 配对 -> Data 页显示 FIP-eDAS 首包/最新/平均时间差`

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
- FIP 默认端口：`3677`
- FIP 默认单包时长：`1.0 s`
- View 默认 Curve1：`FIP1`
- View 默认 Curve2：`DAS Channel`
- View 默认 Curve2 eDAS 通道：`10`
- View 时域曲线点数上限：`8000`
- View PSD 刷新节流：`1.0 s`
- 检测页默认启用特征：`short_energy`
- 检测页默认阈值因子：`3.0`
- 检测页默认触发存储：
  - pre-trigger：`1.0 s`
  - post-trigger：`3.0 s`
- eDAS 默认端口：`3678`
- FIP/eDAS 默认联合原始存储路径：`D:/PCCP/FIPeDASDATA`
- FIP/eDAS 默认联合原始存储时间窗：`10.0 s`
- FIP/eDAS 默认对齐缓存保留时长：`10.0 s`

## 文档索引

- [各个tab参数含义与修改说明](E:/codes/pccpHOST/wb-monitor/docs/各个tab参数含义与修改说明.md)
- [2026-07-18 GUI大改日志](E:/codes/pccpHOST/wb-monitor/docs/2026-07-18-GUI大改日志.md)
- [2026-07-18 FIP和eDAS时间同步与通信检验](E:/codes/pccpHOST/wb-monitor/docs/2026-07-18-FIP和eDAS时间同步与通信检验.md)
- [2026-07-17 FIP-eDAS联调问题数量与修复日志](E:/codes/pccpHOST/wb-monitor/docs/2026-07-17-FIP-eDAS联调问题数量与修复日志.md)
- [2026-07-18 Tab3-FIP丢帧缺口与首点0分析修复](E:/codes/pccpHOST/wb-monitor/docs/2026-07-18-Tab3-FIP丢帧缺口与首点0分析修复.md)
- [2026-07-17 数据存储](E:/codes/pccpHOST/wb-monitor/docs/2026-07-17 数据存储.md)
- [开发日志汇总](E:/codes/pccpHOST/wb-monitor/docs/dev_log.md)

## 已知现状

- `View`、`Data`、`Tab3 / 检测`、`Setting` 已具备基础运行能力
- FIP/eDAS 通信、同步统计、统一通信统计和联合存储已接入 Data 页
- `首包时间差（FIP-eDAS）` 使用 TCP 完整收包时间戳并固定首个匹配包
- Setting 页全局显示设置保存后需重启软件生效
- `run.py --config` 尚未完整接入自定义配置文件加载

## 后续建议

- 用 2026-07-20 后的新版本再做一次长时间联调，重点观察 `首包时间差（FIP-eDAS）` 是否固定，以及 `Processing queue full`、`plot_worker.slow`、`das_tcp.slow_receive` 是否下降。
- 为 View/Data/Tab3/Setting 增加自动化冒烟测试。
- 若仍有同步漂移，需要进一步引入设备侧硬件时间戳或触发源状态，而不是仅依赖主机收包时间。
