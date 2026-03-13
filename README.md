# PCCP Wire Break Monitoring Software

## 项目概述

本项目是一个基于 Python 3.9 + PyQt5 的 PCCP 断丝监测软件原型，当前已完成两条核心链路：

- `Tab1 / FIP`：干涉仪声发射数据的 TCP 接收、预处理、时域绘图、PSD 绘图与相位数据存储
- `Tab2 / SigID`：基于 Tab1 下采样结果的短时特征提取、阈值检测、告警聚合、特征显示与触发存储

当前 GUI 中还保留了 `eDAS` 与 `SigLoc` 页签，但这两个模块仍是占位状态，尚未开发。

## 当前开发状态

### Tab1 已完成

- TCP 服务端接收 LabVIEW RT 发送的 FIP 数据
- 解析 `>II` 头部和 `>q` 大端 `int64` 负载
- 将 `<32,32>` 定点数转换为 `float64`
- 相位展开、数字滤波、系统降采样
- 时域波形实时显示
- PSD 实时计算与绘图
- 相位展开数据按 NPZ 格式保存
- Tab1 代码已整理为独立包 `src/fip_tab1`

### Tab2 已完成

- 独立的 Tab2 多线程流水线 `src/fip_tab2`
- 从 Tab1 接收处理后的下采样数据
- Tab2 自身可选带通预处理
- 短时特征提取
- 基于滑动基线和阈值因子的异常检测
- 连续异常窗口聚合为告警事件
- 最多 4 路特征曲线实时显示
- 告警表、基线显示、阈值配置
- 触发前后信号片段和对应特征结果保存

## 当前代码结构

```text
wb-monitor/
├─ config/
│  └─ app_config.json
├─ docs/
│  ├─ 2026-3-11-声发射TCP通信丢包问题解决记录.md
│  ├─ 2026-3-12-Tab1-声发射数据通信绘图功能开发问文档.md
│  └─ 2026-3-12-Tab2-声发射信号短时特征提取与异常检测（初步）.md
├─ logs/
├─ output/
├─ resources/
├─ src/
│  ├─ config/
│  │  ├─ __init__.py
│  │  └─ system_config.py
│  ├─ fip_tab1/
│  │  ├─ __init__.py
│  │  ├─ fip_plotter.py
│  │  ├─ fip_tab1_manager.py
│  │  └─ fip_tcp_server.py
│  ├─ fip_tab2/
│  │  ├─ __init__.py
│  │  ├─ fip_detection_worker.py
│  │  ├─ fip_feature_worker.py
│  │  ├─ fip_plot_worker.py
│  │  ├─ fip_tab2_manager.py
│  │  ├─ fip_trigger_storage.py
│  │  └─ fip_types.py
│  ├─ processing/
│  │  ├─ __init__.py
│  │  ├─ downsampling.py
│  │  ├─ phase_unwrap.py
│  │  └─ signal_filter.py
│  ├─ ui/
│  │  └─ main_window.py
│  └─ main.py
├─ requirements.txt
└─ run.py
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

说明：当前 `run.py` 已支持参数解析，但 `src/main.py` 里尚未完整接入自定义配置文件参数，`--config` 仍属于预留入口。

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

### 3. 界面

- 主界面：`src/ui/main_window.py`
- Tab1 提供：
  - 通信设置
  - 预处理参数
  - 相位数据存储设置
  - 时域/PSD 显示与启停
- Tab2 提供：
  - 特征勾选
  - Tab2 独立预处理参数
  - 滑动窗与显示时长参数
  - 各特征阈值因子
  - 告警清空与触发存储设置

## 关键数据流

### Tab1

`LabVIEW TCP -> OptimizedTCPServer -> RawDataPacket -> 相位展开 -> 滤波 -> 降采样 -> 时域绘图 / PSD / Tab2 转发 / NPZ 存储`

### Tab2

`Tab1 processed_data -> FIPFeatureWorker -> FIPDetectionWorker / FIPFeaturePlotWorker / FIPTriggerStorageWorker`

## 当前默认参数

- 原始采样率：`1 MHz`
- 默认系统降采样倍数：`5`
- Tab1 默认有效采样率：`200 kHz`
- Tab1 时域显示数据：`downsampled_data[::2]`，即默认约 `100 kHz`
- Tab1 默认 PSD 更新节流：每 `5` 包更新一次
- Tab2 默认启用特征：`short_energy`
- Tab2 默认阈值因子：各特征初始为 `3.0`
- Tab2 默认触发存储：
  - pre-trigger：`1.0 s`
  - post-trigger：`3.0 s`

## 文档索引

- [docs/2026-3-11-声发射TCP通信丢包问题解决记录.md](E:\codes\pccpHOST\wb-monitor\docs\2026-3-11-声发射TCP通信丢包问题解决记录.md)
- [docs/2026-3-12-Tab1-声发射数据通信绘图功能开发问文档.md](E:\codes\pccpHOST\wb-monitor\docs\2026-3-12-Tab1-声发射数据通信绘图功能开发问文档.md)
- [docs/2026-3-12-Tab2-声发射信号短时特征提取与异常检测（初步）.md](E:\codes\pccpHOST\wb-monitor\docs\2026-3-12-Tab2-声发射信号短时特征提取与异常检测（初步）.md)

## 已知现状

- `Tab1` 和 `Tab2` 已能正常运行
- `src` 中老的 `features`、`detection`、`storage` 等历史目录已清理
- `Tab1` 已重构为 `fip_tab1` 独立包，结构与 `fip_tab2` 更一致
- `run.py --config` 目前尚未完整贯通到主程序配置加载逻辑

## 后续建议

- 继续把 `ui/main_window.py` 拆分为更细的 Tab1 / Tab2 UI 子模块
- 为 `fip_tab1` 与 `fip_tab2` 增加自动化冒烟测试
- 在 README 中补充一份实际 TCP 发包格式示例
