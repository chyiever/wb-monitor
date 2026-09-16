# FIP/eDAS 通信协议与数据存储机制

> 更新时间：2026-09-16
> 适用代码：当前 `src/fip`、`src/das`、`src/alignment`、`src/ui` 实现

## 1. 文档范围

本文档统一说明 `wb-monitor` 当前版本中 FIP 与 eDAS 两类数据的通信、解析、对齐、存储和界面控制机制，重点覆盖：

- FIP TCP 协议与包体格式。
- eDAS TCP 协议与包体格式。
- FIP 独立 `.npz`、eDAS 独立 `.bin + .json`、FIP+eDAS 联合 `.npz` 的文件结构。
- 后台线程、非阻塞队列、增量 chunk、内存预算等防卡顿存储机制。
- Data 页（Tab2）内通信、同步、存储相关参数和按钮含义。
- 联调时应检查的通信连续性、对齐状态和文件字段。

## 2. 总体链路

### 2.1 模块角色

| 数据源 | 发送端 | `wb-monitor` 角色 | 默认端口 | 主要用途 |
|---|---|---|---:|---|
| FIP | LabVIEW RT / FIP 采集程序 | TCP Server，Tab1/FIP 链路接收 | `3677` | FIP 相位信号、Tab1/Tab2 分析、View 曲线、联合对齐 |
| eDAS | `pcie6921_gui` / eDAS 发送端 | TCP Server，Data 页/eDAS 链路接收 | `3678` | eDAS space-time 矩阵、View 图像、联合对齐 |

两条通信链路在 `wb-monitor` 内部是独立 TCP Server。Data 页提供统一控制按钮，可同时启动 FIP 与 eDAS，也可分别启动。


### 2.2 数据流

```text
--------------------+       +---------------------+
| FIP TCP payload    | ----> | FIPTCPServer        |
| >II + >i8 payload  |       | Q40.24 -> float64   |
+--------------------+       +----------+----------+
                                      |
                                      v
                           +----------------------+
                           | FIPManager           |
                           | split/process/store  |
                           +----------+-----------+
                                      |
                                      v
                           +----------------------+
                           | FIPSessionPacket     |
                           +----------+-----------+
                                      |
                                      v
+--------------------+       +----------------------+       +----------------------+
| eDAS TCP payload   | ----> | DASTCPServer         | ----> | eDAS manager         |
| >IIIId + <i4 data  |       | int32 -> radians     |       | matrix/plot/store    |
+--------------------+       +----------+-----------+       +----------+-----------+
                                      |                              |
                                      v                              v
                           +----------------------+       +----------------------+
                           | DASSessionPacket     | ----> | AlignedSessionCoord. |
                           +----------------------+       +----------+-----------+
                                                                 |
                                                                 v
                                                        +-------------------+
                                                        | joint .npz store  |
                                                        +-------------------+
```

### 2.3 `comm_count` 对齐原则

- FIP 和 eDAS 均使用 `comm_count` 作为包序号。
- eDAS 使用发送端传来的 `comm_count`。
- FIP 接收端会把发送端 `raw_comm_count` 归一化为当前连接内从 `0` 开始的 `comm_count`。
- `AlignedSessionCoordinator` 以 `comm_count` 为键缓存两路数据。
- 两路最新 `comm_count` 差值不超过 `MAX_COMM_COUNT_DRIFT=5` 时，且两路均在线，状态可为 `aligned`。
- 超过漂移阈值时状态为 `lagging`，不适合联合写盘分析。

## 3. FIP 通信协议

### 3.1 连接参数

| 项目 | 当前实现 |
|---|---|
| 角色 | `wb-monitor` 为 TCP Server |
| 默认监听地址 | `0.0.0.0` |
| 默认端口 | `3677` |
| 长连接 | 是 |
| `TCP_NODELAY` | 开启 |
| 接收缓冲 | 服务端 socket 提高接收缓冲，降低高吞吐丢包风险 |
| 最大包体 | `128 MiB`，超过则关闭连接并重新同步 |

### 3.2 包结构

FIP 每个 TCP 包由 8 字节头部和连续包体组成：

```text
+------------------------------+-------------------------------+
| Header: >II                  | Payload: big-endian int64     |
| 8 bytes                      | data_length bytes             |
+------------------------------+-------------------------------+
```

头部字段：

| 字段 | 类型 | 字节序 | 字节数 | 说明 |
|---|---|---|---:|---|
| `raw_comm_count` | `uint32` | big-endian | 4 | 发送端原始通信包序号 |
| `data_length` | `uint32` | big-endian | 4 | 包体字节数 |

包体字段：

| 项目 | 当前实现 |
|---|---|
| 元素类型 | signed `int64` |
| 字节序 | big-endian，即 NumPy `>i8` |
| 定点格式 | Q40.24 |
| 缩放 | `float64 = raw_int64 / 2**24` |
| 合法长度 | `data_length % 8 == 0` |

### 3.3 FIP 单/双传感器拆分

FIP 包体先解析为一维 `float64 phase_data`。随后按 UI 中的 `FIP数量`、`单包时长(s)`、`采样率(MHz)` 计算期望点数：

```text
points_per_sensor = round(sample_rate_hz * packet_duration_seconds)
expected_points = points_per_sensor * sensor_count
```

当前双传感器布局是交错排列：

```text
payload = [
  FIP1[0], FIP2[0],
  FIP1[1], FIP2[1],
  ...
]

FIP1 = payload[0::2]
FIP2 = payload[1::2]
```

处理规则：

- `FIP数量=1`：整包作为 FIP1。
- `FIP数量=2` 且点数达到期望值：截取期望长度后按交错方式拆为 FIP1/FIP2。
- `FIP数量=2` 且点数不足但可被 `2` 整除：记录 warning，按实际长度交错均分。
- 点数异常且无法均分：保留可用的 FIP1 数据，记录 warning。
- 多传感器处理、相位展开、滤波、降采样状态按传感器独立维护，避免 FIP1/FIP2 状态互相污染。

### 3.4 FIP 处理语义

FIP 管线分为“处理显示链路”和“独立存储链路”：

| 链路 | 输入 | 输出/用途 |
|---|---|---|
| 处理显示链路 | TCP 解析后的 `RawDataPacket.phase_data` | `ProcessedData`，用于 Tab1/View 绘图、PSD 和 Tab2 分析显示 |
| 原始存储/对齐链路 | 同一个 `RawDataPacket` | FIP 独立 `.npz` 与 Data 页 joint `.npz` 的 FIP 部分，保持未滤波、未相位展开的原始解码数据 |

FIP 的 `unwrap` 复选框只控制处理显示链路：

- `unwrap=OFF`：处理显示链路使用原始解码后的相位数值。
- `unwrap=ON`：每个传感器独立调用相位展开，仅影响 View/PSD/Tab2 分析显示，不影响任何存储文件。
- FIP 独立 `.npz` 与 Data 页 joint `.npz` 的 FIP 字段始终来自 `RawDataPacket.phase_data` 拆分后的原始 `float64` 数组；默认 `FIP降采样=1`，即不抽取。

无论 `unwrap` 状态如何，处理链路会修复明显异常的极端值或非有限值，并记录诊断日志；存储链路只做传感器拆分和必要的数值类型转换。

## 4. eDAS 通信协议

### 4.1 连接参数

| 项目 | 当前实现 |
|---|---|
| 发送端 | `pcie6921_gui` / eDAS TCP client |
| 接收端 | `wb-monitor` `DASTCPServer` |
| 默认监听地址 | `0.0.0.0` |
| 默认端口 | `3678` |
| 长连接 | 是 |
| `TCP_NODELAY` | 开启 |
| 接收缓冲 | 服务端 client socket 设置为 `16 MiB` |
| 最大包体 | `1 GiB`，超过则关闭连接并重新同步 |

### 4.2 包结构

eDAS 每个 TCP 包由 24 字节头部和连续载荷组成：

```text
+------------------------------+-------------------------------+
| Header: >IIIId               | Payload: little-endian int32  |
| 24 bytes                     | data_bytes bytes              |
+------------------------------+-------------------------------+
```

头部使用 Python `struct.Struct(">IIIId")`，字段为：

| 字段 | 类型 | 字节序 | 字节数 | 说明 |
|---|---|---|---:|---|
| `comm_count` | `uint32` | big-endian | 4 | 通信包序号 |
| `sample_rate_hz` | `uint32` | big-endian | 4 | 每通道时间采样率 |
| `channel_count` | `uint32` | big-endian | 4 | 空间通道数 |
| `data_bytes` | `uint32` | big-endian | 4 | 包体字节数 |
| `packet_duration_seconds` | `float64` | big-endian | 8 | 本包时长 |

包体字段：

| 项目 | 当前实现 |
|---|---|
| 元素类型 | signed `int32` |
| 字节序 | little-endian，即 NumPy `<i4` |
| 展开顺序 | C-order，空间优先 |
| 单位转换 | `phase_rad = phase_int32 / 32767 * pi` |
| 矩阵形状 | `channel_count x samples_per_channel` |

接收端以 `data_bytes` 和 `channel_count` 恢复点数：

```text
total_points = data_bytes / 4
samples_per_channel = total_points / channel_count
matrix = payload.reshape(channel_count, samples_per_channel)
actual_duration = samples_per_channel / sample_rate_hz
```

如果头部 `packet_duration_seconds` 与 `actual_duration` 的误差大于一个采样周期，接收端记录 warning，并以实际包体长度推算的时长为准。

### 4.3 发送端矩阵约定

eDAS 发送端从采集块中得到时间优先矩阵：

```text
A shape = frame_load_num x point_num_after_merge
```

经通道截取、空间降采样和时间降采样后，发送给 `wb-monitor` 的矩阵约定为：

```text
M = A[:, channel_start:channel_end+1:space_downsample].T[:, ::time_downsample]
M shape = channel_count x samples_per_channel
```

发送采样率和包时长：

```text
sample_rate_hz_sent = original_sample_rate_hz / time_downsample
packet_duration_seconds = samples_per_channel / sample_rate_hz_sent
```

当前约定是：发送端发送原始 `int32` 相位计数，接收端统一转换为弧度。

## 5. 通信可靠性与同步显示

### 5.1 完整收包

两条 TCP 链路都使用“精确读取指定字节数”的模式：

- 先读取固定长度 header。
- 校验包体长度和关键字段。
- 再循环读取恰好 `data_length` / `data_bytes` 字节。
- TCP 拆包、粘包不会影响协议解析。

非法包长会导致连接关闭并等待重连，避免字节流永久失步。

### 5.2 缺包统计

| 链路 | 缺包判断 |
|---|---|
| FIP | 当前归一化 `comm_count` 大于上一个 `comm_count + 1` |
| eDAS | 当前 `comm_count` 大于上一个 `comm_count + 1` |
| 对齐层 | 分别记录 FIP/eDAS `MissingRange`，显示最近缺口 |

`comm_count` 回退或重复会记录 reset/out-of-order 日志。

### 5.3 FIP-eDAS 接收时间差

Data 页会按同一 `comm_count` 配对 FIP/eDAS 的 TCP 完整包体接收完成时间，显示：

- 首包时间差：`FIP receive time - eDAS receive time`，形成后固定。
- 最新同序号时间差。
- 平均时间差。
- 匹配包数。

该时间差用于通信同步诊断，不等同于数据物理事件发生时间。

## 6. 存储总览

当前有三条存储链路：

| UI 按钮 | 文件格式 | 默认路径 | 数据来源 | 是否依赖另一数据源 |
|---|---|---|---|---|
| `FIP存储` | `.npz` | `D:/PCCP/FIPdata` | FIP `RawDataPacket` | 否 |
| `eDAS存储` | `.bin + .json` | `D:/PCCP/eDASDATA` | eDAS 解析后矩阵 | 否 |
| `同时存储FIP+eDAS` | joint `.npz` | `D:/PCCP/FIPeDASDATA` | `AlignedPacketFrame` | 是，要求两路在线且对齐 |

如果三个按钮同时打开，会产生三套文件：

```text
D:/PCCP/FIPdata        -> FIP 独立 .npz
D:/PCCP/eDASDATA       -> eDAS 独立 .bin + .json
D:/PCCP/FIPeDASDATA    -> FIP+eDAS joint .npz
```

联合存储不会替代单独存储；单独存储也不会自动写入联合文件。

## 7. FIP 独立 `.npz` 存储

### 7.1 写盘流程

```text
RawDataPacket.phase_data
  -> 按 FIP数量 拆分传感器
  -> 转为 float64 原始相位数组
  -> 按 FIP降采样 抽取（默认 1，不抽取）
  -> 累积到目标存储间隔
  -> np.savez_compressed(...)
```

当前实现特点：

- 写盘在线程 `DataStorageThread` 中完成。
- UI/主线程只做 `put_nowait` 非阻塞入队。
- 输入队列容量为 `120` 个原始 FIP 包。
- 队列满时丢弃新入队包并计数，不阻塞 UI。
- 停止监测时进入 drain 模式，尽量排空已入队请求。
- 修改 `FIP降采样`、采样率、包时长或传感器数量时，会刷新当前 chunk，避免不同参数混在一个文件中；`unwrap` 不再影响存储 chunk。

### 7.2 文件命名

```text
0000001-FIP-<sample_rate_label>-YYYYMMDDTHHMMSS.mmm.npz
0000001-FIP2-<sample_rate_label>-YYYYMMDDTHHMMSS.mmm.npz
```

| 片段 | 说明 |
|---|---|
| `0000001` | 当前存储会话内文件序号，7 位补零 |
| `FIP` | 单传感器文件 |
| `FIP2` | 双传感器文件 |
| `<sample_rate_label>` | 当前存储采样率，如 `1M`、`200K` |
| 时间戳 | 按会话已保存时长推算的文件时间 |

### 7.3 文件字段

当前格式版本：`wb-monitor-tab1-fip-v3`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `phase_data` | `float64[N]` 或 `float64[sensor_count, N]` | 主数据字段。双 FIP 时第 0 行为 FIP1，第 1 行为 FIP2 |
| `comm_count` | scalar | 当前 chunk 最后一包 `comm_count` |
| `timestamp` | scalar | 当前文件起点，单位秒；按当前存储会话已保存时长累计 |
| `sample_rate` | scalar | 存储采样率，等于 `raw_sample_rate_hz / FIP降采样` |
| `raw_sample_rate_hz` | scalar | 单路 FIP 原始采样率 |
| `packet_duration_seconds` | scalar | 单个 FIP TCP 包时长 |
| `fip_sensor_count` | `int32` | 文件内传感器数量 |
| `data_info` | dict-like object | 元数据 |
| `phase_unwrap_enabled` | bool | 固定为 `False`；存储文件不执行相位展开 |
| `format_version` | string | 当前为 `wb-monitor-tab1-fip-v3` |

`data_info` 关键字段：

| 字段 | 说明 |
|---|---|
| `type` | 固定为 `phase_raw_downsampled` |
| `length` / `samples_per_sensor` | 每路传感器样本数 |
| `total_values` | 文件内总数值个数 |
| `sensor_count` | FIP 传感器数量 |
| `downsample_factor` | UI `FIP降采样` 值 |
| `packet_duration_seconds` | 单包时长 |
| `raw_sample_rate_hz` | 单路原始采样率 |
| `packet_points_per_sensor` | 每包每路原始点数 |
| `storage_points_per_packet_per_sensor` | 每包每路存储点数 |
| `packet_count_estimate` | 当前 chunk 估计包数 |
| `start_comm_count` / `end_comm_count` | 当前 chunk 覆盖的包序号范围 |
| `duration_seconds` | 当前 chunk 时长 |
| `file_sequence` | 文件序号 |
| `stream_start_time` / `save_time` | 文件逻辑时间与实际写盘时间 |

### 7.4 读取示例

```python
import numpy as np

data = np.load("0000001-FIP2-1M-20260916T120000.000.npz", allow_pickle=True)
phase_data = data["phase_data"]
sample_rate = float(data["sample_rate"])

if phase_data.ndim == 1:
    fip1 = phase_data
else:
    fip1 = phase_data[0]
    fip2 = phase_data[1]
```

## 8. eDAS 独立 `.bin + .json` 存储

### 8.1 写盘流程

```text
DASRawPacket
  -> eDAS manager 解析为 channel_count x samples_per_channel 矩阵
  -> matrix: channel_count x samples_per_channel, float64 radians
  -> EDASRawStorageWorker.enqueue_packet()
  -> 后台写 .bin
  -> 同步维护 .json 元数据
```

当前实现特点：

- eDAS 独立存储不依赖 FIP 是否在线。
- 写盘在线程 `EDASRawStorageWorker` 中完成。
- UI/接收线程只做非阻塞入队。
- 全局输入队列最大 `4096` 包。
- UI `eDAS队列` 决定有效容量，当前默认 `200`。
- 队列内存预算为 `2 GiB`。
- 队列满或超过预算时丢弃最旧待写包，优先保护接收线程和 UI。
- 按 UI `eDAS块/文件` 分文件，默认每 `50` 个 eDAS 包切一个新文件。

### 8.2 `.bin` 文件命名

```text
0000001-eDAS-10000Hz-0051ch-10000pt-20260916T120000.000.bin
```

| 片段 | 说明 |
|---|---|
| `0000001` | 当前 eDAS 存储会话内文件序号 |
| `10000Hz` | eDAS 每通道采样率 |
| `0051ch` | 通道数 |
| `10000pt` | 每通道每包样本数 |
| 时间戳 | 文件创建时间 |

### 8.3 `.bin` 二进制布局

`.bin` 文件连续写入多个完整 eDAS block。每个 block 是一个 `channel_count x samples_per_channel` 的 C-order 矩阵：

```text
block 0: channel 0 samples, channel 1 samples, ..., channel C-1 samples
block 1: channel 0 samples, channel 1 samples, ..., channel C-1 samples
...
```

| 项目 | 值 |
|---|---|
| dtype | little-endian `float64`，即 `<f8` |
| 单位 | 弧度 |
| 单 block 形状 | `[channel_count, samples_per_channel]` |
| 文件形状 | `[blocks_written, channel_count, samples_per_channel]` |

读取示例：

```python
import json
import numpy as np
from pathlib import Path

bin_path = Path("0000001-eDAS-10000Hz-0051ch-10000pt-20260916T120000.000.bin")
meta = json.loads(bin_path.with_suffix(".json").read_text(encoding="utf-8"))

blocks = int(meta["blocks_written"])
channels, samples = meta["matrix_shape_per_block"]
raw = np.fromfile(bin_path, dtype="<f8")
matrix_blocks = raw.reshape(blocks, channels, samples)
```

### 8.4 `.json` 元数据字段

当前格式版本：`wb-monitor-edas-raw-v1`。

| 字段 | 说明 |
|---|---|
| `format_version` | 当前为 `wb-monitor-edas-raw-v1` |
| `storage_type` | 固定为 `edas_raw_storage` |
| `data_file` / `metadata_file` | 数据文件与元数据文件名 |
| `output_dir` | 输出目录 |
| `file_index` | 当前会话内文件序号 |
| `created_at` / `closed_at` | 文件创建与关闭时间 |
| `dtype` / `byte_order` / `array_order` | 二进制解析方式 |
| `matrix_shape_per_block` | `[channel_count, samples_per_channel]` |
| `sample_rate_hz` | 每通道采样率 |
| `packet_duration_seconds` | 单包时长 |
| `blocks_per_file` | UI `eDAS块/文件` |
| `queue_packets` | UI `eDAS队列` |
| `data_bytes_per_block` | 单包原始 eDAS payload 字节数 |
| `storage_parameters` | 输出目录、分文件策略、队列策略快照 |
| `das_parameters` | 采样率、通道数、点数、包时长快照 |
| `blocks_written` | 已写入 block 数 |
| `bytes_written` | 已写入字节数 |
| `comm_counts` | 每个 block 对应的 `comm_count` |
| `packet_start_times` / `packet_end_times` | 每个 block 的逻辑起止时间 |
| `block_bytes` | 每个 block 写入 `.bin` 的字节数 |

## 9. FIP+eDAS 联合 `.npz` 存储

### 9.1 写盘条件

`同时存储FIP+eDAS` 只在以下条件同时满足时写 joint `.npz`：

1. joint 存储按钮开启。
2. FIP 在线。
3. eDAS 在线。
4. `AlignedSessionCoordinator` 状态为 `aligned`。
5. 自上次写盘后累计的新帧时长达到 `联合间隔(s)`，或缓存字节预算接近上限触发提前 flush。

如果只收到一路数据：

- 只有 eDAS 在线：自动关闭 joint 按钮，并切换到 eDAS 独立存储。
- 只有 FIP 在线：自动关闭 joint 按钮，并切换到 FIP 独立存储。
- 两路都不在线：等待，不写文件。

### 9.2 写盘流程

```text
AlignedSessionCoordinator.get_frames_since(last_comm_count)
  -> 只取增量 AlignedPacketFrame
  -> 达到 joint chunk 条件
  -> DASStorageRequest 非阻塞入队
  -> DASStorageWorker 后台 np.savez_compressed(...)
```

当前实现特点：

- joint 写盘线程为 `DASStorageWorker`。
- 主线程只封装 `DASStorageRequest` 并入队。
- 队列最大请求数为 `32`。
- 队列内存预算为 `2 GiB`。
- 单次 joint 请求超过 `2 GiB` 时拒绝写入并向 UI 报错。
- 队列满或超过预算时丢弃最旧请求。
- joint 使用增量 chunk，不再每次写最近 N 秒全量快照，避免相邻文件大量重叠。
- 停止时写盘线程会 drain 已入队请求，降低尾部 chunk 丢失风险。

### 9.3 文件命名

```text
FIPeDAS-YYYYMMDD-HHMMSS.mmm.npz
```

文件时间戳为实际写盘创建时间。

### 9.4 文件字段

当前格式版本：`wb-monitor-joint-v6`。

| 字段 | 类型 | 说明 |
|---|---|---|
| `comm_counts` | `int32[]` | joint chunk 内帧序号 |
| `packet_start_times` | `float64[]` | 每帧逻辑起始时间，单位秒 |
| `packet_duration_seconds` | `float64[]` | 每帧持续时间，单位秒 |
| `fip_present` | `bool[]` | 对应帧是否有 FIP 数据 |
| `das_present` | `bool[]` | 对应帧是否有 eDAS 数据 |
| `fip_sensor_count` | `int32[]` | 每帧 FIP 传感器数量 |
| `fip_selected_sensor` | `int32[]` | 每帧 Tab1 选中的 FIP 编号 |
| `fip1_raw_data` | object array | FIP1 原始解码数据，未滤波、未相位展开；真实采样率看 `fip_sample_rate_hz` |
| `fip2_raw_data` | object array | FIP2 原始解码数据；单 FIP 或缺失时为空数组 |
| `das_raw_matrix` | object array | eDAS 矩阵，通常为 `channel_count x samples_per_channel` |
| `fip_sample_rate_hz` | `float64[]` | FIP 每帧采样率 |
| `das_sample_rate_hz` | `float64[]` | eDAS 每帧采样率 |
| `das_channel_count` | `int32[]` | eDAS 每帧通道数 |
| `incremental` | bool | `True` 表示增量 chunk |
| `format_version` | string | 当前为 `wb-monitor-joint-v6` |
| `created_at` | string | ISO 毫秒格式创建时间 |

注意：

- joint v6 只写每路 FIP 原始数据字段，不再写兼容旧格式的冗余 FIP 字段。
- `fip1_raw_data` / `fip2_raw_data` 只表达“FIP 原始数据”，不在字段名中编码采样率；真实采样率统一读取 `fip_sample_rate_hz`。
- eDAS 满速大矩阵长期保存建议优先使用 eDAS 独立 `.bin + .json`；joint `.npz` 适合对齐分析窗口或降采样后的短窗口。

### 9.5 读取示例

```python
import numpy as np

data = np.load("FIPeDAS-20260916-120000.000.npz", allow_pickle=True)

comm_counts = data["comm_counts"]
fip1_frames = list(data["fip1_raw_data"])
fip2_frames = list(data["fip2_raw_data"])
das_frames = list(data["das_raw_matrix"])

fip_rates = data["fip_sample_rate_hz"]
das_rates = data["das_sample_rate_hz"]
```

## 10. 防卡顿存储机制

### 10.1 原则

所有大体量写盘都遵守同一原则：

- 接收线程只解析协议和构造内存对象。
- UI/主线程只做轻量状态更新和非阻塞入队。
- `.npz` 压缩、`.bin` 写入、`.json` 元数据刷新都放到后台线程。
- 队列满时选择丢弃待写请求，而不是阻塞 TCP 接收或 Qt 事件循环。
- 停止监测时尽量 drain 已入队请求。

### 10.2 三条存储链路对比

| 链路 | 写盘线程 | 队列策略 | 内存保护 | 文件切分 |
|---|---|---|---|---|
| FIP `.npz` | `DataStorageThread` | `Queue(maxsize=120)`，满时丢新包 | 队列容量限制 | 按 `FIP间隔(s)` chunk |
| eDAS `.bin + .json` | `EDASRawStorageWorker` | 有效容量由 `eDAS队列` 决定，满时丢旧包 | `2 GiB` 队列字节预算 | 按 `eDAS块/文件` |
| joint `.npz` | `DASStorageWorker` | `Queue(maxsize=32)`，满时丢旧请求 | `2 GiB` 队列预算；单请求 `2 GiB` 上限 | 按 `联合间隔(s)` 或字节压力提前 flush |

### 10.3 卡顿风险边界

- `.npz` 是压缩格式，CPU 和内存压力明显高于顺序 `.bin`。
- joint `.npz` 同时包含 FIP object array 和 eDAS object array，满速大矩阵下可能很大。
- 当前实现可避免 UI 被写盘阻塞，但无法在磁盘吞吐长期低于输入速率时保证零丢包。
- 满速 eDAS 长时间原始保存应优先使用独立 `.bin + .json`，并确认 NVMe 写速满足现场数据率。

## 11. Data/Tab 参数与按钮

### 11.1 通信控制

| 控件 | 作用 |
|---|---|
| `同时启动FIP+eDAS` / `停止同时通信` | 同时启动或停止 FIP 与 eDAS 通信链路 |
| `启动FIP通信` / `停止FIP通信` | 只控制 FIP TCP Server |
| `启动eDAS通信` / `停止eDAS通信` | 只控制 eDAS TCP Server |

### 11.2 FIP 通信参数

| 控件 | 默认/范围 | 作用 |
|---|---|---|
| FIP `监听地址` | 通常 `0.0.0.0` | 绑定本机地址；不确定网卡时使用 `0.0.0.0` |
| FIP `端口` | `3677` | FIP TCP Server 监听端口 |
| `单包时长(s)` | 默认 `1.000` | 写入 FIP 处理、存储、对齐元数据 |
| `采样率(MHz)` | 默认 `1.000` | 单路 FIP 原始采样率 |
| `FIP数量` | `1个` / `2个` | 决定 FIP 包体拆分方式 |
| `绘图FIP` | `FIP1` / `FIP2` | 双 FIP 时选择 Tab1 兼容字段和默认绘图源 |
| `unwrap` | 默认 OFF | 只控制 FIP 处理显示链路是否相位展开；不影响 FIP 独立存储或 joint 存储 |

### 11.3 eDAS 通信参数与状态

| 控件/状态 | 作用 |
|---|---|
| eDAS `监听地址` | 绑定本机地址；推荐 `0.0.0.0` |
| eDAS `端口` | 默认 `3678` |
| `连接状态` | 显示 eDAS client 是否连接 |
| `接收包` | eDAS 已成功接收包数 |
| `缺包` | eDAS `comm_count` 缺口累计 |
| `通道` | 最新包 `channel_count` |
| `采样率` | 最新包 `sample_rate_hz` |
| `字节` | 最新包 `data_bytes` |
| `包长` | 最新包 `packet_duration_seconds` |
| `最近Comm` | 最新 eDAS `comm_count` |

### 11.4 时间同步与对齐状态

| 控件/状态 | 作用 |
|---|---|
| `首包时间差(FIP-eDAS)` | 首个同序号包的 TCP 完整接收时间差 |
| `最新同序号时间差(FIP-eDAS)` | 最新匹配 `comm_count` 的接收时间差 |
| `平均时间差(FIP-eDAS)` | 所有已匹配包的平均接收时间差 |
| `匹配包数` | 成功配对的 `comm_count` 数量 |
| `FIP Comm` / `eDAS Comm` | 对齐层看到的最新包号 |
| `对齐状态` | `waiting`、`single-source`、`aligned`、`lagging`、`stopped` |
| `FIP缺包` / `eDAS缺包` | 对齐层记录的缺口数 |
| `缺口范围` | 最近缺口范围，如 `fip:10-12` |

### 11.5 存储控制

| 控件 | 默认 | 作用 |
|---|---|---|
| `同时存储FIP+eDAS` | OFF | 开启 joint `.npz`，要求两路在线且对齐 |
| `FIP存储` | OFF | 开启 FIP 独立 `.npz` |
| `eDAS存储` | OFF | 开启 eDAS 独立 `.bin + .json` |
| `联合路径` | `D:/PCCP/FIPeDASDATA` | joint `.npz` 输出目录 |
| `FIP路径` | `D:/PCCP/FIPdata` | FIP `.npz` 输出目录 |
| `eDAS路径` | `D:/PCCP/eDASDATA` | eDAS `.bin + .json` 输出目录 |
| `FIP间隔(s)` | `10` | FIP 独立 `.npz` 目标 chunk 时长，范围 `10~300` |
| `联合间隔(s)` | `10.0` | joint `.npz` 目标 chunk 时长，范围 `1.0~60.0` |
| `缓存(s)` | `10.0` | 对齐缓存时间；代码保证至少大于 joint 间隔 `1 s` |
| `eDAS块/文件` | `50` | eDAS `.bin` 每个文件包含的完整包数 |
| `eDAS队列` | `200` | eDAS 独立存储有效队列容量 |
| `FIP降采样` | `1` | FIP 独立存储抽取因子，范围 `1~100`；默认 `1` 表示不降采样 |
| `预计文件` | 自动估算 | 显示 FIP/eDAS/joint 未压缩体量估计 |
| `FIP成功/失败`、`eDAS成功/失败` | 自动统计 | 存储状态计数 |
| `联合Last`、`eDAS Last` | 自动更新 | 最近一次写盘状态或文件名 |

## 12. 推荐联调检查项

### 12.1 通信检查

- FIP 和 eDAS 的 `接收包` 应持续增长。
- FIP/eDAS `缺包` 应保持 `0`；若增长，先检查发送端队列积压、网络链路和磁盘写速。
- eDAS `通道`、`采样率`、`字节`、`包长` 应与发送端配置一致。
- FIP `FIP数量`、`单包时长(s)`、`采样率(MHz)` 应与发送端实际包体一致；否则会出现包形状 warning。
- `对齐状态` 应稳定为 `aligned`；若为 `lagging`，说明两路 `comm_count` 明显错位。

### 12.2 存储检查

- joint `.npz` 只在两路在线且 `aligned` 时生成。
- eDAS 满速原始长期保存优先检查 `.bin + .json` 是否连续增长。
- 若 joint 状态提示单请求过大，应缩短 `联合间隔(s)`、降低 eDAS 发送端采样/通道规模，或改用 eDAS 独立 `.bin`。
- 若出现存储队列满日志，说明磁盘或压缩吞吐低于输入速率，应降低数据率或换更快磁盘。
- 读取 FIP 独立 `.npz` 时只依赖 `phase_data`；不要假设存在 `fip1_phase_data` / `fip2_phase_data`。
- 读取 joint `.npz` 时以 `format_version` 和 `fip_sample_rate_hz` 为准；字段名不再携带采样率。

## 13. 代码位置

| 功能 | 代码位置 |
|---|---|
| FIP TCP 接收协议 | `src/fip/tcp_server.py` |
| FIP 拆传感器、处理、独立存储 | `src/fip/manager.py` |
| eDAS TCP 接收协议 | `src/das/tcp_server.py` |
| eDAS 解析、绘图、存储调度 | `src/das/manager.py` |
| joint `.npz` 与 eDAS `.bin + .json` 写盘 | `src/das/storage_worker.py` |
| FIP/eDAS 对齐缓存 | `src/alignment/aligned_session_coordinator.py` |
| 对齐数据结构 | `src/alignment/aligned_types.py` |
| Data 页通信/存储 UI | `src/ui/main_window.py` |
| FIP/eDAS 总控连接 | `src/main.py` |
| joint 离线读取示例 | `read/fip_edas_joint_reader.ipynb` |
