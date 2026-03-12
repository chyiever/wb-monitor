# PCCP Wire Break Monitoring Software

## 项目概述

PCCP (Prestressed Concrete Cylinder Pipe) 断丝监测软件，基于光纤干涉仪和分布式光纤传感(DAS)技术的实时监测系统。

### 功能特点

- **Tab1 干涉仪(声发射)链路** - TCP通信、数据解析、相位展开、滤波、降采样、时域绘图、PSD绘图、数据存储
- **TCP通信模块** - 高吞吐数据接收、会话计数归一化、连接状态管理
- **实时可视化** - PyQtGraph 时域波形与功率谱显示
- **信号检测** - 基于阈值的异常检测和告警（Tab2）
- **数据存储** - NPZ格式相位数据记录与管理

## Tab1 已开发内容（截至 2026-03-12）

### 1) 数据通信与解析
- 已实现 `OptimizedTCPServer`（`src/comm/tcp_server_optimized.py`）
- TCP包格式：8字节头（`raw_comm_count` + `data_length`）+ 数据体
- 数据体解析：大端 `int64`（`<32,32>` 定点）转换为 `float64`
- 每次通信成功后会话计数自动归一化：主流程接收计数从 0 开始

### 2) 预处理链路（线程化）
- 已实现 `OptimizedTab1ThreadManager`（`src/processing/tab1_optimized_threads.py`）
- `DataProcessingThread` 完成：
   - 相位展开（`PhaseUnwrapper`）
   - 数字滤波（`SignalFilter`）
   - 系统降采样（`Downsampler`）
- 时域数据使用“滤波后 + 降采样”结果
- PSD数据使用“相位展开后、未滤波”数据（符合当前设计要求）

### 3) 时域绘图
- 已实现 `TimedomainPlotThread` + 主线程曲线更新
- 绘图方式：单条曲线 `setData` 覆盖更新（避免重复创建曲线）
- 显示策略：窗口化显示 + 更新节流（默认每5包更新）
- 已处理问题：
   - 启动后时域不自动刷新
   - 通信计数回退导致时域不更新
   - 时间轴抖动/重叠

### 4) PSD计算与绘图
- 已实现 `PSDPlotThread` + `PSDCalculator`（Welch）
- 计算函数：`scipy.signal.welch`
- PSD 更新节奏：默认每5包更新一次
- 显示策略：线性功率转 dB，并做范围裁剪

### 5) 存储
- 已实现 `DataStorageThread`
- 存储对象：相位展开数据（`unwrapped_data`）
- 格式：`.npz` 压缩存储，支持启停与路径配置

## 系统要求

- **Python**: 3.9.x
- **操作系统**: Windows 10/11 (64位)
- **内存**: >= 4GB RAM
- **网络**: TCP/IP 支持

## 安装依赖

```bash
pip install -r requirements.txt
```

## 运行程序

### 正常模式
```bash
python run.py
```

### 调试模式
```bash
python run.py --debug
```

### 指定日志文件
```bash
python run.py --log monitor.log
```

### 自定义配置
```bash
python run.py --config my_config.json
```

## 项目架构（当前）

```
wb-monitor/
├── config/
│   └── app_config.json
├── docs/
│   ├── 2026-3-11-声发射TCP通信丢包问题解决记录.md
│   ├── 2026-3-12-Tab1-声发射数据通信绘图功能开发问文档.md
│   ├── PCCP断丝监测软件 开发需求文档.txt
│   └── README.md
├── libs/
├── logs/
├── output/
├── ref/
│   └── TCP.py
├── resources/
├── src/
│   ├── comm/
│   │   ├── __init__.py
│   │   └── tcp_server_optimized.py
│   ├── config/
│   │   ├── __init__.py
│   │   └── system_config.py
│   ├── detection/
│   │   ├── __init__.py
│   │   └── threshold_detector.py
│   ├── features/
│   │   ├── __init__.py
│   │   └── feature_calculator.py
│   ├── processing/
│   │   ├── __init__.py
│   │   ├── downsampling.py
│   │   ├── phase_unwrap.py
│   │   ├── signal_filter.py
│   │   └── tab1_optimized_threads.py
│   ├── storage/
│   │   ├── __init__.py
│   │   └── detection_storage.py
│   ├── ui/
│   │   └── main_window.py
│   ├── visualization/
│   │   └── wave_plotter.py
│   └── main.py
├── README.md
├── requirements.txt
└── run.py
```

## 开发阶段

### 第一阶段（Tab1，已完成）
- [x] TCP通信与数据解析
- [x] 相位展开、滤波、降采样预处理链路
- [x] 时域绘图线程化更新
- [x] PSD计算与绘图线程化更新
- [x] Tab1 数据存储（NPZ）

### 第二阶段（Tab2，进行中）
- [x] 特征计算基础能力
- [x] 阈值检测基础能力
- [ ] Tab2 全流程联调与性能优化

### 第三阶段（规划）
- [ ] DAS数据处理
- [ ] 信号定位算法
- [ ] 高级分析与诊断功能

## 技术规格

### 数据格式
- **协议**: TCP/IP
- **数据包**: 8字节头部 + 1.6MB数据体
- **数据类型**: <32,32>定点数 (大端序)
- **采样率**: 1MHz
- **传输频率**: 5包/秒

### 性能指标
- **丢包率**: 0%
- **接收延迟**: <50ms
- **处理延迟**: <100ms
- **内存占用**: <2GB

## 配置说明

配置文件位置: `config/app_config.json`

主要配置项:
- `communication`: TCP连接参数
- `preprocessing`: 信号预处理
- `features`: 特征计算设置
- `detection`: 检测算法参数
- `storage`: 数据存储配置

## 故障排除

### 常见问题

1. **连接失败**
   - 检查IP地址和端口设置
   - 确认防火墙允许端口3677
   - 验证网络连接

2. **丢包率高**
   - 检查网络质量
   - 调整TCP缓冲区大小
   - 确认客户端发送频率

3. **性能问题**
   - 启用调试日志定位瓶颈
   - 检查系统资源使用
   - 优化处理参数

## 开发指南

### 代码规范
- 遵循PEP 8编码规范
- 使用类型提示
- 添加详细的文档字符串
- 英文注释和变量名

### 测试
```bash
# 运行调试模式
python run.py --debug

# 查看日志
tail -f logs/pccp_monitor.log
```

## 版权信息

- **版本**: 1.0.0
- **作者**: Claude
- **日期**: 2026-03-11
- **许可**: MIT License

## 更新日志

### v1.0.0 (2026-03-11)
- 完成TCP通信模块优化
- 实现零丢包率数据接收
- 基础GUI界面和实时可视化
- 数据处理管道建立
- 项目架构重构

### v1.0.1 (2026-03-12)
- 完成 Tab1 全链路线程化（通信-处理-时域-PSD-存储）
- 修复时域启动后不自动显示问题
- 修复通信计数回退导致时域不更新问题
- 实现每次通信成功后 `comm_count` 会话内从 0 开始计数
- 清理无用测试/调试脚本与旧模块，更新项目结构文档