# FIP/eDAS joint NPZ 回放软件

本目录是独立的 FIP/eDAS 联合数据回放工具，用于查看 `wb-monitor` 生成的 `FIPeDAS-*.npz` 文件。

## 启动

在仓库根目录执行：

```powershell
python .\fipedasREAD\run.py
```

也可以指定联合数据目录或单个文件：

```powershell
python .\fipedasREAD\run.py D:\PCCP\FIPeDASDATA
python .\fipedasREAD\run.py D:\PCCP\FIPeDASDATA\FIPeDAS-20260916-120000.000.npz
```

依赖沿用主工程 `requirements.txt`：

- PyQt5
- pyqtgraph
- numpy
- scipy

## 支持的数据字段

当前优先读取 `wb-monitor-joint-v5` 字段：

- `comm_counts`
- `packet_start_times`
- `packet_duration_seconds`
- `fip1_display_data`
- `fip2_display_data`
- `das_raw_matrix`
- `fip_sample_rate_hz`
- `das_sample_rate_hz`
- `das_channel_count`
- `format_version`
- `created_at`

同时兼容部分旧字段：

- `fip1_raw_200khz`
- `fip2_raw_200khz`
- `fip_display_data`
- `fip_raw_200khz`
- `das_matrix`
- `edas_raw_matrix`

## GUI 功能

左侧区域：

- 数据路径选择和刷新。
- 文件名列表。
- 两条时域曲线的数据源选择：`FIP1`、`FIP2`、`DAS Channel`。
- DAS 通道号。
- FIP 预处理：去均值、归一化、Butterworth 滤波、频带、阶数、降采样。
- EDAS 预处理：去均值、归一化、Butterworth 滤波、频带、阶数、降采样。
- FIP 和 EDAS 滤波频带格式均支持 `500-6000`、`100-`、`-1000`。
- 时域曲线最大绘图点数。
- timespace 通道范围、时间降采样、空间降采样、颜色图、逐通道去基线、自动色阶。
- 取消「自动色阶」后可手动输入 Vmin/Vmax 固定 timespace 色标范围。

右侧区域：

- 第一张时域波形图。
- 第二张时域波形图。
- DAS timespace 图和色标。

timespace 颜色图默认使用 `Seismic`，也可切换为 `RdBu`、`CoolWarm`、`Viridis`、`Plasma`、`Inferno`、`Magma`、`Gray` 或 `Jet`。两张时域图右侧保留与 timespace 色标等宽的空白区，保证三张图的真实绘图区宽度一致，横轴刻度可上下对齐。

曲线配色采用色盲安全的蓝(`#0072B2`)/朱红(`#D55E00`)组合；界面通过统一 QSS 提供圆角分组框、聚焦高亮、按钮反馈与选中高亮，开「自动色阶」时 Vmin/Vmax 输入框自动置灰提示不可编辑。

三张图放在垂直分割器中，默认高度比例为 `1:1:2`。用户可以拖动图之间的分割条手动调整每张图的高度。

三张图均启用 pyqtgraph 矩形缩放。任意一张图缩放或平移后，另外两张图会同步横轴时间范围；纵轴保持各自独立，便于同时查看 FIP 幅值、DAS 单通道幅值和空间通道范围。

## 多线程设计

数据加载与预处理（NPZ 读取、滤波、逐帧拼接、timespace 矩阵、色阶计算）全部在后台 `QThread` 中完成，GUI 线程只负责发送参数、接收结果并更新绘图，避免切换文件或调整参数时界面卡死。

- 参数变化经 120ms 去抖合并后提交，快速连点时只有最新结果被应用。
- 后台按路径缓存最近加载的 4 个文件，切回已看过的文件无需重新读盘。
- 详细变更记录见 `docs/DEV_LOG.md`。

## 设计边界

这是一个简易离线查看器，不接入实时 TCP 通信，也不写回原始数据文件。大文件会在加载和绘制时占用较多内存；可以通过左侧的时域降采样、最大点数、timespace 时间降采样和空间降采样控制绘图开销。
