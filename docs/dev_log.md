# 开发日志

## 2026-04-29 02:04:03 +08:00

- GitHub 仓库：`https://github.com/chyiever/wb-monitor.git`
- GitHub 分支：`master`
- 代码提交：`fa75f86`
- 更新范围：`src/fip_tab1/fip_tab1_manager.py`

### 更新摘要

1. 将 Tab1 存储链路改为直接接收 `RawDataPacket`，不再依赖绘图处理后的 `ProcessedData`
2. 在存储线程内部补充相位展开、固定 `5x` 降采样和 `.npz` 存储请求构建逻辑
3. 为处理线程新增队列峰值、入队数、处理数、丢包数、异常数和相位展开失败数等统计信息
4. 为存储线程新增原始包入队数、处理数、缺包数、乱序/重复包数、等待次数和保存统计信息
5. 在 `OptimizedTab1ThreadManager` 中改为将原始包同时分发到绘图处理线程和存储线程
6. 新增线程统计统一出口 `get_thread_stats()`，便于后续调试和联调定位

### 说明

- 本次推送先上传最新 `src` 程序代码到 GitHub
- 当前工作区中其他 `docs` 删除项、`backup` 压缩包和新建说明文档未随本次 `src` 推送一起上传
- 2026-06-19 - 更新 eDAS Tab3 通信与绘图链路：服务端按 payload 字节数恢复 DAS 矩阵，新增 `comm_count` 缺口统计；Space-Time 图改为最近窗口多包滚动拼接，DAS Channel 曲线增加显示密度控制；补充协议和绘图更新文档并执行 UTF-8 中文自检。

## 2026-07-17 02:00:00 +08:00

- GitHub 仓库：`https://github.com/chyiever/wb-monitor.git`
- GitHub 分支：`dev`
- 更新范围：`src/ui/main_window.py`、`src/das_tab3/das_tab3_manager.py`、`src/das_tab3/das_storage_worker.py`、`docs/2026-07-17 数据存储.md`、`read/fip_edas_joint_reader.ipynb`

### 更新摘要

1. Tab3 存储区由 `Joint Raw Storage` 调整为 `raw storage`，新增可同时开启的 `FIP+eDAS SAVE` 与 `eDAS SAVE` 两个保存入口。
2. `FIP+eDAS SAVE` 继续采用 `comm_count` 对齐的 joint `.npz` 存储，并增加采样率、通道数、格式版本和创建时间等向后兼容元数据。
3. 新增 eDAS-only 后台存储线程 `EDASRawStorageWorker`，按完整 DAS 包写入 `.bin`，并生成同名 UTF-8 `.json` 元数据；支持 `Blocks/File` 分文件和 `Cache(packets)` 队列保护。
4. joint 保存仅在 FIP 与 eDAS 同时在线且对齐状态为 `aligned` 时写入；若只有 eDAS 在线则自动转入 Tab3 `eDAS SAVE`，若只有 FIP 在线则自动转入 Tab1 FIP 相位存储。
5. 优化 Tab3 左侧参数布局：Curve1/Curve2、DAS Channel/Display、Low Hz/High Hz 等参数并列显示，左侧面板增加滚动保护，避免文本重叠和按钮显示不全。
6. DAS Space-Time 图色标固定为手动 `Vmin/Vmax`，默认 `Seismic`、`-0.3 ~ 0.3`，不再随数据分位数自动更新。
7. 新增 `read/fip_edas_joint_reader.ipynb`，用于读取 `FIP+eDAS SAVE` joint `.npz`，输出 FIP/eDAS 采集参数并绘制 FIP 时域、FIP PSD、eDAS Space-Time、指定 eDAS channel 时域和 PSD。

### 验证

- `python -X utf8 -m py_compile src\ui\main_window.py src\das_tab3\das_storage_worker.py src\das_tab3\das_tab3_manager.py` 通过。
- eDAS-only 存储线程合成测试通过：3 个模拟包按 `Blocks/File=2` 生成 2 个 `.bin` 和 2 个 `.json`。
- joint `.npz` 合成测试通过：旧字段可读，新增采样率和通道数字段正确。
- `python -X utf8 tools\validate_tab3_pipeline.py` 通过，输出 `VALIDATION_OK packets_received=3 plot_payloads=3 last_shape=(16, 800) last_curve_points=2400`。
- MainWindow Tab3 offscreen UI 检查通过：默认 `seismic`、`Vmin=-0.3`、`Vmax=0.3`，新增 eDAS storage 设置项可读取。
- 已执行 UTF-8 中文自检，新增/修改的中文文档未发现问号乱码。

## 2026-07-17 02:25:00 +08:00

- 更新范围：`src/ui/main_window.py`、`docs/2026-07-17 数据存储.md`

### 更新摘要

1. 进一步压缩 Tab3 左侧参数布局，移除左侧 `QScrollArea`，避免参数区出现滚动条。
2. 将 `FIP+eDAS Path`、`eDAS Path` 与路径输入框合并到同一行显示。
3. 将通信状态、Live Header、Alignment、Space-Time 与 raw storage 中的短标签参数合并到更少行，减少纵向占用。
4. 将 Tab3 Control 的 Start/Plot 两个按钮改为同一行显示。

### 验证

- `python -X utf8 -m py_compile src\ui\main_window.py` 通过。
- MainWindow offscreen 检查通过：Tab3 未发现 `QScrollArea`，默认 `seismic` 与 eDAS 路径设置可读取。
- 已执行 UTF-8 中文自检，`src/ui/main_window.py` 未发现问号乱码。

## 2026-07-17 02:40:00 +08:00

- 更新范围：`src/ui/main_window.py`

### 更新摘要

1. 按 Tab1 参数区风格调整 Tab3 左侧参数区行间距与组内边距：外层 `QVBoxLayout` 间距为 `6`，各参数组 `QGridLayout` 横向/纵向间距均为 `6`。
2. Tab3 左侧面板宽度调整为 `560-600 px`，继续保持无滚动条布局。
3. `FIP+eDAS SAVE` 与 `eDAS SAVE` 按钮字号恢复为 `16px`、padding 为 `8px`，与 Tab1 控制按钮视觉密度更一致。

### 验证

- `python -X utf8 -m py_compile src\ui\main_window.py` 通过。
- MainWindow offscreen 检查通过：Tab3 未发现 `QScrollArea`，左侧面板宽度为 `560-600 px`，布局 spacing 为 `6`。
- 已执行 UTF-8 中文自检，`src/ui/main_window.py` 未发现问号乱码。

## 2026-07-17 02:55:00 +08:00

- 更新范围：`src/ui/main_window.py`

### 更新摘要

1. 增加 Tab3 左侧参数框之间的竖向间距，外层 `QVBoxLayout` spacing 从 `6` 调整为 `10`，让各参数框之间的边界更清晰。
2. 移除 Tab3 左侧底部空白 stretch，改为让 7 个参数组按 stretch 比例占用左侧高度，尽量填满左侧区域。
3. 为 Tab3 左侧各参数组设置 `QSizePolicy.Preferred, QSizePolicy.Expanding`，通信、Header、Alignment、Curve、Space-Time、raw storage、Control 均参与纵向空间分配。

### 验证

- `python -X utf8 -m py_compile src\ui\main_window.py` 通过。
- MainWindow offscreen 检查通过：Tab3 未发现 `QScrollArea`，左侧布局 spacing 为 `10`，左侧布局项数量为 `7`。
- 已执行 UTF-8 中文自检，`src/ui/main_window.py` 未发现问号乱码。

## 2026-07-17 03:10:00 +08:00

- 更新范围：`src/ui/main_window.py`、`src/das_tab3/das_storage_worker.py`、`docs/2026-07-17 数据存储.md`

### 更新摘要

1. 统一 Tab3 四个动作按钮尺寸：`FIP+eDAS SAVE`、`eDAS SAVE`、`Start/Stop DAS Monitoring`、`Plot Updates` 均设置为横向扩展、固定高度，最小高度为 `44`。
2. 统一四个动作按钮的 Tab3 toggle 样式，使用相同字号和 padding；storage 与 control 按钮行均设置对称 column stretch。
3. 补充 eDAS-only 独立存储 `.json` 元数据，新增 `output_dir`、`file_index`、`queue_packets`、`data_bytes_per_block`、`storage_parameters` 和 `das_parameters`。
4. `storage_parameters` 记录输出目录、分文件块数、队列容量、分文件策略和队列满时丢弃旧包策略；`das_parameters` 记录采样率、通道数、每通道样本数、包时长、单包字节数和矩阵形状。

### 验证

- `python -X utf8 -m py_compile src\ui\main_window.py src\das_tab3\das_storage_worker.py` 通过。
- MainWindow offscreen 检查通过：Tab3 四个动作按钮最小高度均为 `44`，无 `QScrollArea`。
- eDAS-only 存储线程合成测试通过：新元数据 `storage_parameters` 与 `das_parameters` 写入正确。
- 已执行 UTF-8 中文自检，本次修改文件未发现问号乱码。

## 2026-07-17 03:20:00 +08:00

- 更新范围：`docs/2026-07-17 数据存储.md`

### 更新摘要

1. 在数据存储文档中补充 `raw storage` 紧凑 UI 标签与存储模式归属说明。
2. 明确 `Len(s)` 和 `JCache` 属于 `FIP+eDAS SAVE` 联合存储，用于 joint `.npz` 的分块时长与对齐缓存。
3. 明确 `Blocks` 和 `Q` 属于 `eDAS SAVE` 独立存储，用于 eDAS-only `.bin + .json` 的分文件块数与写盘队列容量。
4. 补充两个按钮同时开启时，两套参数分别影响各自写盘链路且互不共享队列。

### 验证

- 已执行 UTF-8 中文自检，`docs/2026-07-17 数据存储.md` 和 `docs/dev_log.md` 未发现问号乱码。
- `git diff --check` 通过。

## 2026-07-17 23:05:00 +08:00

- GitHub 仓库：`https://github.com/chyiever/wb-monitor.git`
- GitHub 分支：`dev`
- 更新范围：`run.py`、`src/main.py`、`src/fip_tab1/fip_tcp_server.py`、`src/das_tab3/das_tcp_server.py`、`src/das_tab3/das_tab3_manager.py`、`src/das_tab3/das_plot_worker.py`、`src/das_tab3/das_storage_worker.py`、`src/ui/main_window.py`、`README.md`、`docs/2026-07-17-FIP-eDAS联调问题数量与修复日志.md`

### 更新摘要

1. 参照 `E:\codes\PCIe-7821\pcie7821_gui` 的 Time-Space 与 phase 时域刷新机制，Tab3 Space-Time 改为固定大小 `float32` 滚动显示缓存，避免每包拼接历史矩阵。
2. Tab3 UI 增加绘图限频和点数上限：FIP 对比曲线最小刷新间隔 `0.4 s`，DAS/Space-Time 最小刷新间隔 `0.2 s`，曲线进入 `setData()` 前限制到 `12000` 点。
3. Space-Time 每帧只更新图像和必要 `rect`，`Vmin/Vmax`、色标和 Histogram 范围改为参数变化时更新，降低主线程阻塞风险。
4. 新增 Tab3 debug 数据流节点日志，统一使用 `TAB3_NODE` 前缀，覆盖 main、DAS TCP、manager、plot worker、UI、joint storage 和 eDAS storage。
5. `run.py --debug` 改为由 `src.main` 统一配置日志；`--log` 支持指定 UTF-8 日志文件；debug 模式保留本项目详细日志，同时压制 matplotlib 内部 DEBUG 噪声。
6. 修正 FIP 性能日志 `Rate` 统计公式，改为按当前统计间隔内的包数计算真实区间包率，避免把累计包数误当作瞬时速率。
7. 联调日志结论更新：原始日志可确认 FIP 真实包率约 `5.001 pkt/s` 且未见缺包；旧日志没有 DAS 成功包和 Tab3 UI 帧耗时，因此不能判断 DAS/eDAS 丢包或绘图延时，本轮已补齐后续定位日志。

### 验证

- `python -m py_compile run.py src\main.py src\fip_tab1\fip_tcp_server.py src\das_tab3\das_tcp_server.py src\das_tab3\das_plot_worker.py src\das_tab3\das_tab3_manager.py src\das_tab3\das_storage_worker.py src\ui\main_window.py` 通过。
- `python tools\validate_tab3_pipeline.py` 通过，输出 `VALIDATION_OK packets_received=3 plot_payloads=3 last_shape=(16, 800) last_curve_points=2400`。
- MainWindow 离屏 UI 绘图冒烟测试通过，输出 `OFFSCREEN_OK True 12000 (0.0, 0.0, 1.001001001001001, 200.0)`。
- Tab3 worker 滚动缓存合成测试通过，输出 `WORKER_OK (400, 600) 240000 (400, 0, 399, 1, 67, 747)`。
- Debug 日志配置冒烟测试通过，输出 `DEBUG_LOG_OK True`，并确认 `TAB3_NODE` 可写入指定日志文件。
- 中文自检通过：本次新增和修改的源码、README、开发日志未发现 `Unicode replacement character` 或问号乱码。

## 2026-07-17 23:43:16 +08:00

- GitHub 仓库：`https://github.com/chyiever/wb-monitor.git`
- GitHub 分支：`dev`
- 更新范围：`src/fip_tab1/fip_tab1_manager.py`、`src/alignment/aligned_types.py`、`src/das_tab3/das_tab3_manager.py`、`src/das_tab3/das_storage_worker.py`、`src/ui/main_window.py`、`src/main.py`、`docs/2026-07-17 数据存储.md`、`docs/dev_log.md`

### 更新摘要

1. Tab1 通信设置新增 `FIP数量` 与 `绘图FIP`：支持在 1 个或 2 个 FIP 传感器之间切换；单 FIP 模式保持旧软件效果不变。
2. 双 FIP 模式下，单个 TCP 包仍为 `0.2 s`，包体按 `400000` 点解释：前 `200000` 点为 FIP1，后 `200000` 点为 FIP2。
3. Tab1 处理线程在同一 `comm_count` 内同时处理 FIP1/FIP2，并为两路分别维护相位展开、滤波、降采样状态；旧 `ProcessedData.downsampled_data` 等字段继续指向 Tab1 当前 `绘图FIP` 选择的传感器。
4. Tab1 相位存储更新为支持双 FIP：单 FIP 仍保存一维 `phase_data`；双 FIP 保存二维 `phase_data`，形状为 `2 x samples_per_sensor`，并新增 `fip1_phase_data`、`fip2_phase_data`、`fip_sensor_count` 与 `wb-monitor-tab1-fip-v2` 格式标记。
5. Tab3 在 Tab1 选择 `2个` FIP 后，Curve1/Curve2 选项从 `FIP` 动态切换为 `FIP1`、`FIP2`；两张曲线可分别绘制不同 FIP 传感器。
6. `FIPSessionPacket` 和 joint 存储升级为多 FIP 感知：joint `.npz` 格式版本更新为 `wb-monitor-joint-v3`，新增 `fip_sensor_count`、`fip_selected_sensor`、`fip1_raw_200khz`、`fip2_raw_200khz`、`fip1_display_data`、`fip2_display_data`，同时保留旧 `fip_raw_200khz` 与 `fip_display_data` 兼容字段。
7. 更新 `docs/2026-07-17 数据存储.md`，补充 Tab1 单/双 FIP 存储格式、Tab3 FIP1/FIP2 绘图选项、joint v3 字段和本次验证记录。

### 验证

- `python -m py_compile src\fip_tab1\fip_tab1_manager.py src\alignment\aligned_types.py src\das_tab3\das_storage_worker.py src\das_tab3\das_tab3_manager.py src\ui\main_window.py src\main.py` 通过。
- Tab1 双 FIP 合成测试通过：模拟 `400000` 点包，处理输出 FIP1/FIP2 各 `40000` 点，Tab1 存储请求形状为 `(2, 40000)`。
- MainWindow 离屏 UI 检查通过：默认 Tab3 选项为 `FIP`；Tab1 切到 `2个` 后 Curve1/Curve2 选项变为 `FIP1/FIP2`，并可选择 Tab1 绘图 FIP2。

## 2026-07-18 00:17:55 +08:00

- GitHub 仓库：`https://github.com/chyiever/wb-monitor.git`
- GitHub 分支：`dev`
- 更新范围：`src/fip_tab1/fip_tab1_manager.py`、`src/fip_tab1/fip_tcp_server.py`、`src/alignment/aligned_session_coordinator.py`、`src/das_tab3/das_tab3_manager.py`、`src/fip_tab2/fip_types.py`、`src/fip_tab2/fip_tab2_manager.py`、`src/fip_tab2/fip_feature_worker.py`、`src/ui/main_window.py`、`src/main.py`、`docs/2026-07-17 数据存储.md`、`docs/dev_log.md`

### 更新摘要

1. Tab1 通信设置新增 `单包时长(s)` 输入框，默认 `1.000 s`；新增 `采样率(MHz)` 输入框，默认 `1.000 MHz`。
2. FIP 拆包点数由 `round(sample_rate_hz * packet_duration_seconds)` 动态计算；双 FIP 时包体按每路点数切分为 FIP1/FIP2，默认 `1 s x 1 MHz x 2` 对应 `2,000,000` 个相位点。
3. `RawDataPacket`、`ProcessedData` 和 Tab1 存储请求增加 FIP 包时长与原始采样率字段；处理后的 `effective_rate` 改为 `sample_rate_hz / downsample_factor`。
4. Tab1 `.npz` 存储采样率改为 `raw_sample_rate_hz / 5`，默认仍为 `200K`；文件名采样率标签、`sample_rate`、`raw_sample_rate_hz`、`packet_duration_seconds` 和 `data_info` 元数据随 UI 参数更新。
5. FIP TCP 包体合法长度上限从旧 `10 MB` 提高到 `128 MB`，避免默认双 FIP 1s 包或更高采样率包被拒收。
6. Tab3 FIP 曲线、`FIPSessionPacket` 和 `AlignedSessionCoordinator` 改为使用 Tab1 传入的 `packet_duration_seconds`；默认空状态包时长改为 `1.0 s`。
7. Tab2 输入包增加 `packet_duration_seconds`，采样率或包时长变化时重置特征时间轴；缺包后按 `comm_count * packet_duration_seconds` 对齐新的时间原点。
8. 更新 `docs/2026-07-17 数据存储.md`，说明 Tab1 新输入参数、动态点数、动态存储采样率、`.npz` 元数据和本轮验证记录。

### 验证

- `python -m py_compile src\fip_tab1\fip_tab1_manager.py src\fip_tab1\fip_tcp_server.py src\alignment\aligned_session_coordinator.py src\das_tab3\das_tab3_manager.py src\fip_tab2\fip_types.py src\fip_tab2\fip_tab2_manager.py src\fip_tab2\fip_feature_worker.py src\ui\main_window.py src\main.py` 通过。
- Tab1 双 FIP 变采样率合成测试通过：模拟 `2 MHz`、`1 s`、双 FIP 包，处理输出 FIP1/FIP2 各 `400000` 点，`effective_rate=400000.0`；Tab1 存储请求形状为 `(2, 400000)`，`sample_rate=400000.0`。
- MainWindow 离屏 UI 检查通过：默认 `packet_duration_seconds=1.0`、`sample_rate_hz=1000000.0`；修改为 `2.0 s`、`2.5 MHz`、双 FIP 后，Tab3 Curve 选项为 `Off / DAS Channel / FIP1 / FIP2`。
- `git diff --check` 通过。

## 2026-07-18 01:07:03 +08:00

- GitHub 仓库：`https://github.com/chyiever/wb-monitor.git`
- GitHub 分支：`dev`
- 更新范围：`src/fip_tab1/fip_tcp_server.py`、`src/fip_tab1/fip_tab1_manager.py`、`src/das_tab3/das_tab3_manager.py`、`src/das_tab3/das_tcp_server.py`、`src/ui/main_window.py`、`src/main.py`、`read/fip_edas_joint_reader.ipynb`、`docs/2026-07-18-Tab3-FIP丢帧缺口与首点0分析修复.md`、`docs/2026-07-17-FIP-eDAS联调问题数量与修复日志.md`、`docs/2026-07-17 数据存储.md`、`docs/dev_log.md`

### 更新摘要

1. 分析 2026-07-18 00:26-00:40 日志，确认 Tab3 FIP 缺口来自 Tab1 `DataProcessingThread` 队列满后丢弃旧 FIP 包，而不是 DAS TCP 丢包；DAS 统计中 `missing=0`，`DASPlotWorker` 中 `dropped=0`。
2. FIP TCP 解析由 `struct.unpack` 改为 `np.frombuffer(dtype=">i8")`，保留 `<32,32>` 的 `int64 / 2^32` 协议解码，避免 2,000,000 点包创建巨大 Python tuple。
3. 新增 FIP 首样本诊断链路：`FIP_TCP_PARSE`、`FIP_TCP_FIRST_SAMPLE_ZERO`、`FIP_MAIN_FIRST_SAMPLE_ZERO`、`FIP_PROCESS_INPUT_FIRST_ZERO`、`FIP_PROCESS_UNFILTERED_FIRST_ZERO`、`TAB3_NODE manager.fip_first_zero`、`TAB3_NODE ui.fip_curve_first_zero`。
4. 删除处理链和存储链旧的 `max(abs(data)) > 5` 自动除以 `pi` 逻辑；后续只记录异常范围，不做猜测性幅值缩放，保持数据真实性和完整性。
5. Tab3 FIP 曲线和 joint 存储改为使用未滤波、已展开、降采样数据 `psd_data/psd_by_sensor`，不再使用滤波后的 `downsampled_data`。
6. `FIPSessionPacket` 中写入的 FIP 数组长度改为与 `fip_sample_rate_hz` 匹配，修复旧代码将 1 MHz 全量展开数组标记为约 200 kHz 的 joint 存储语义不一致问题。
7. 双 FIP 处理时，非 Tab1 当前选中的传感器跳过滤波路径，只生成未滤波降采样数据，降低处理线程 CPU 压力。
8. DAS `slow_receive` 阈值改为按包时长计算，默认 1 s 包不再因约 1000 ms 接收耗时误报 warning。
9. 更新 `read/fip_edas_joint_reader.ipynb`，新增 `FIP_SENSOR_TO_PLOT`，优先读取 `fip1_*` / `fip2_*` 分路字段，缺失时回退兼容字段。
10. 新增专项分析文档，并更新 FIP-eDAS 联调日志和数据存储文档。

### 验证

- `python -m py_compile src\fip_tab1\fip_tcp_server.py src\fip_tab1\fip_tab1_manager.py src\das_tab3\das_tab3_manager.py src\das_tab3\das_tcp_server.py src\ui\main_window.py src\main.py` 通过。
- 合成链路测试通过，输出 `SYNTHETIC_OK tcp_decode dual_fip_processing tab3_unfiltered notebook_fip2`。
- Notebook 编码和语法检查通过，输出 `question_count 0`、`replacement_count 0`、`code_cells_ok 6`。

## 2026-07-18 22:13:54 +08:00

- GitHub 仓库：`https://github.com/chyiever/wb-monitor.git`
- GitHub 分支：`dev`
- 更新范围：`src/main.py`、`src/processing/phase_unwrap.py`、`src/fip_tab1/fip_tcp_server.py`、`src/fip_tab1/fip_tab1_manager.py`、`src/ui/main_window.py`、`docs/2026-07-17-FIP-eDAS联调问题数量与修复日志.md`、`docs/dev_log.md`

### 更新摘要

1. 分析 2026-07-18 08:45:48 至 21:33:54 半天稳定性测试日志：FIP 处理链 `queue_dropped=0`、`gaps=0`，DAS TCP `missing=0`，DAS 绘图队列 `dropped=0`，未发现持续通信丢包。
2. 发现 FIP 单传感器配置与实际包点数不一致：UI 为 `sensor_count=1`、`1 s`、`1 MHz`，但 TCP 每包实收 `2,000,000` 点，旧代码会把降采样输出误标为 `200 kHz`。
3. `PCCPMonitorApp._process_data_packet()` 新增 FIP 包形状校验，按 `actual_points_per_sensor / packet_duration_seconds` 推导运行采样率，并通过 `FIP_PACKET_SHAPE_MISMATCH` 日志记录配置点数、实际点数和推导采样率。
4. 推导出的运行采样率会同步到滤波器、Tab1 处理线程、Tab1 存储线程、Tab3 FIP 曲线和 joint 存储元数据；当后续包形状重新匹配 UI 设置时自动清除 override。
5. `PhaseUnwrapper` 对越界输入 WARNING 做节流：首次和每 `500` 段输出一次，并记录 `segments` 与 `suppressed`，避免半天运行产生 4.6 万条重复 WARNING。
6. `OptimizedTCPServer.get_statistics()` 改为使用独立 UI 快照计算区间包率和区间吞吐，修复 UI 状态统计可能被累计包数误导的问题。
7. Tab3 曲线显示点数预算由 `12000` 下调到 `8000`，用于降低半天测试中偶发的 `ui.fip_curve_slow` 主线程慢帧。
8. `FIP_PROCESS_SENSOR` 日志补充 `raw_rate` 字段，后续可直接核对原始采样率和降采样后 `effective_rate` 是否一致。
9. 更新 FIP-eDAS 联调日志，补充半天测试的通信完整性、刷新延迟、告警风暴、采样率元数据一致性和修复验证记录。

### 验证

- `python -m py_compile src\main.py src\processing\phase_unwrap.py src\fip_tab1\fip_tcp_server.py src\fip_tab1\fip_tab1_manager.py src\ui\main_window.py` 通过。
- FIP 包形状推导与 PhaseUnwrapper 告警节流合成验证通过，输出 `SYNTHETIC_OK inferred_sample_rate 2000000.0 phase_range_warnings 3`。
- Tab1 处理线程采样率下传验证通过，输出 `PROCESS_OK raw_rate 20.0 effective_rate 4.0 points 4`。
- `python tools\validate_tab3_pipeline.py` 通过，输出 `VALIDATION_OK packets_received=3 plot_payloads=3 last_shape=(16, 800) last_curve_points=2400`。


## 2026-07-18 23:31:56 +08:00

- GitHub 仓库：`https://github.com/chyiever/wb-monitor.git`
- GitHub 分支：`dev`
- 更新范围：`src/ui/main_window.py`、`src/main.py`、`src/das_tab3/das_tab3_manager.py`、`src/fip_tab1/fip_tab1_manager.py`、`docs/2026-07-18-GUI大改日志.md`、`docs/2026-07-18-FIP和eDAS时间同步与通信检验.md`、`docs/各个tab参数含义与修改说明.md`、`docs/2026-07-17-FIP-eDAS联调问题数量与修复日志.md`、`docs/dev_log.md`

### 更新摘要

1. 按联调需求重组 GUI：新 Tab1 为 `View`，新 Tab2 为 `Data`，旧 Tab2 移为新 Tab3，旧 Tab4 改为 `Setting`。
2. `View` 合并 FIP/eDAS 绘图：Curve1/Curve2 时域曲线在左，PSD1/PSD2 共轴 dB 图在右，布局比例约 `7:3`。
3. 新增 Welch PSD 计算、PSD1/PSD2 开关、legend、手动坐标轴范围、自动范围和图件字体设置。
4. `Data` 集中 FIP/eDAS 通信参数、三种通信启动按钮、通信状态灯、成功/失败/丢包率、同步时间差、三种存储按钮、存储状态灯和存储计数。
5. FIP/eDAS 收包时按 `comm_count` 记录主机接收时间，实时计算首次、最新和平均同序号包时间差。
6. FIP/eDAS TCP 错误计入 Data tab 失败次数；存储完成或失败状态计入对应模块存储统计。
7. 旧 Tab1 绘图线程从可见 View 图件解绑，修复 FIP 启停可能清空或覆盖合并图件的问题。
8. 新增三份专题文档，并继续补充 FIP-eDAS 联调问题与修复日志。

### 验证

- `python -m py_compile src\ui\main_window.py src\main.py src\das_tab3\das_tab3_manager.py src\fip_tab1\fip_tab1_manager.py` 通过。
- MainWindow 离屏 GUI 构造通过，tab 顺序为 `['View', 'Data', 'Tab3', 'Setting']`。
- 合成 `20 Hz` 正弦信号 Welch PSD 验证通过，PSD 峰值显示在 pyqtgraph 对数横轴 `1.3` 附近。

## 2026-07-19 00:58:00 +08:00

- GitHub 仓库：`https://github.com/chyiever/wb-monitor.git`
- GitHub 分支：`dev`
- 更新范围：`src/ui/main_window.py`、`docs/2026-07-18-GUI大改日志.md`、`docs/各个tab参数含义与修改说明.md`、`docs/dev_log.md`

### 更新摘要

1. Data tab 中 FIP/eDAS 的 `IP` 标签改为 `监听地址`，并补充 placeholder 和 tooltip，明确本软件作为服务端时应绑定本机地址。
2. 明确默认 `0.0.0.0` 的含义：服务端监听所有本机网卡；客户端连接时应使用本机实际网卡 IP，不能把 `0.0.0.0` 作为目标地址。
3. View tab 左侧参数面板加宽并改为更紧凑的两列参数布局，覆盖 FIP 预处理、刷新参数、PSD 参数和坐标轴参数。
4. View tab 绘图开关短标签化为 `时域 ON/OFF`、`PSD ON/OFF`、`刷新 ON/OFF`，并统一按钮宽度、字号、圆角和颜色。
5. Space-Time 图和色标恢复为左右布局，色标固定在图右侧，避免 GUI 大改后色标落到图下方。
6. 新增统一按钮样式 helper，主操作按钮、开关按钮和次级按钮统一尺寸与视觉语义。
7. 更新 GUI 大改日志和 tab 参数说明文档。

### 验证

- `python -m py_compile src\ui\main_window.py` 通过。
- MainWindow 离屏构造通过，tab 顺序为 `['View', 'Data', 'Tab3', 'Setting']`。
- 生成 View tab 离屏截图，确认 Space-Time 色标位于图右侧。
- Qt 字体度量检查通过，View/Data/Storage/Setting 主按钮文本均能完整放入按钮可用宽度。

## 2026-07-19 01:18:00 +08:00

- GitHub 仓库：`https://github.com/chyiever/wb-monitor.git`
- GitHub 分支：`dev`
- 更新范围：`src/ui/main_window.py`、`src/main.py`、`.gitignore`、`docs/2026-07-18-GUI大改日志.md`、`docs/各个tab参数含义与修改说明.md`、`docs/dev_log.md`

### 更新摘要

1. 新增 GUI 本地参数快照 `config/gui_last_state.json`，用于保存现场最近一次参数。
2. MainWindow 启动时自动读取本地快照，恢复 FIP/eDAS 通信参数、View 绘图参数、PSD、坐标轴、存储路径、检测参数和全局字体。
3. GUI 参数变化后使用 `QTimer` 做 `700 ms` 防抖自动保存，关闭窗口前强制保存一次。
4. `保存配置`、`加载配置`、`重置配置` 按钮补全实现，菜单中的保存/打开配置也接入同一逻辑。
5. 保存使用 UTF-8 JSON，先写临时文件再替换正式快照，降低写入中断造成 JSON 损坏的风险。
6. 启动恢复只恢复参数，不自动启动 FIP/eDAS 通信；检测页总开关也保持关闭。
7. FIP 通信启动前会从 GUI 同步当前监听地址和端口到 `OptimizedTCPServer`，确保自动恢复后的监听参数真正生效。
8. GUI 快照与基础系统配置 `config/app_config.json` 分离，避免将现场 UI 参数覆盖到基础配置文件。
9. 新增 `.gitignore`，排除 `config/gui_last_state.json` 和临时写入文件，避免本机现场参数进入 Git。

### 验证

- `python -m py_compile src\ui\main_window.py src\main.py` 通过。
- 离屏持久化测试通过，输出 `PERSISTENCE_OK app_config_unchanged`。
- 自动保存测试通过，修改端口后快照中的 `communication.port` 自动更新为新值。

## 2026-07-19 01:30:00 +08:00

- GitHub 仓库：`https://github.com/chyiever/wb-monitor.git`
- GitHub 分支：`dev`
- 更新范围：`src/ui/main_window.py`、`src/das_tab3/das_plot_worker.py`、`docs/2026-07-18-GUI大改日志.md`、`docs/各个tab参数含义与修改说明.md`、`docs/dev_log.md`

### 更新摘要

1. View tab 将 PSD 从单一共轴图拆分为两个独立图件：PSD1 位于 Curve1 右侧，PSD2 位于 Curve2 右侧。
2. View 绘图区改为两行布局，每行左侧为时域图、右侧为该曲线的 PSD，单行内宽度比例约 `7:3`。
3. Curve1/Curve2 时域图移除标题，改用动态 legend；DAS 显示为 `eDAS ch=通道号`，FIP 显示为 `FIP1` 或 `FIP2`。
4. 曲线参数改为按 Curve 设置，Curve1/Curve2 均可独立选择来源、DAS 通道、DAS 带通开关和 DAS 带通频带。
5. 默认 View 配置调整为 Curve1=`FIP1`、Curve2=`DAS Channel`、Curve2 DAS 通道=`10`。
6. Curve1、Curve2、PSD1、PSD2 和 Space-Time 图均启用 pyqtgraph 矩形缩放模式。
7. DAS 绘图 worker 新增双 DAS 曲线 payload，支持同时查看两个不同 eDAS 通道。
8. Data tab 左侧集中通信控制、通信完整性和时间同步检验；右侧只保留存储控制与日志。
9. 存储路径顺序调整为联合路径、FIP 路径、eDAS 路径。
10. 通信和存储指示灯移动到底部状态栏左侧，并在灯后显示对应成功次数。
11. 通信控制与存储控制共六个主按钮统一为 `44 px` 最小高度。
12. 四个 tab 的标题增加最小宽度，避免标题显示不全。

### 验证

- `python -m py_compile src\ui\main_window.py src\das_tab3\das_plot_worker.py` 通过。
- MainWindow 离屏构造检查通过，确认 tab 顺序、默认 Curve 设置、两个 PSD 独立图件、矩形缩放模式和按钮高度。
- 合成 DAS 包验证通过，输出：

```text
{'c1_first': 3.0, 'c2_first': 10.0, 'legacy_first': 10.0, 'c1_len': 20, 'c2_len': 20}
```

## 2026-07-19 01:45:00 +08:00

- GitHub 仓库：`https://github.com/chyiever/wb-monitor.git`
- GitHub 分支：`dev`
- 更新范围：`src/main.py`、`docs/dev_log.md`

### 更新摘要

1. 新增 `DailyFileHandler`，本地运行日志按自然日写入独立 UTF-8 文件。
2. 默认日志路径从单一 `logs/pccp_monitor.log` 调整为每日文件：

```text
logs/pccp_monitor_YYYY-MM-DD_HH-MM-SS.log
```

3. 若启动参数指定 `--log some/path/debug.log`，实际写入文件会自动变为：

```text
some/path/debug_YYYY-MM-DD_HH-MM-SS.log
```

4. 软件长时间连续运行跨过午夜时，下一条日志会自动切换到新日期文件，不再一直追加到同一个文件。
5. 日志时间格式固定为 `YYYY-MM-DD HH:MM:SS`，每条日志都包含日期和时间。
6. 日志初始化信息补充 `file` 和 `daily_base`，方便从日志头部确认实际写入文件和配置基路径。

### 验证

- `python -m py_compile src\main.py` 通过。
- `DailyFileHandler` 合成写入验证通过，确认生成带日期、时、分、秒的日志文件，且日志内容以日期时间开头。
- 中文自检通过：`src/main.py` 和 `docs/dev_log.md` 未发现替换字符或中文行问号乱码。

## 2026-07-19 01:55:00 +08:00

- GitHub 仓库：`https://github.com/chyiever/wb-monitor.git`
- GitHub 分支：`dev`
- 更新范围：`src/main.py`、`src/ui/main_window.py`、`src/fip_tab1/fip_tab1_manager.py`、`docs/dev_log.md`

### 日志分析

分析文件：`logs/pccp_monitor_2026-07-19 - 副本.log`

1. 日志时间范围：`2026-07-19 01:19:17` 至 `2026-07-19 01:22:45`。
2. 日志级别统计：`INFO=164`，`WARNING=25`。
3. 高频节点：
   - `TimedomainPlotThread`：`42` 条
   - `DataProcessingThread`：`33` 条
   - `PSDCalculator`：`12` 条
4. `FIP_PROCESS_STATS` 显示处理线程无丢包、无缺口，但处理耗时偏高：
   - `comm=150`
   - `processed=151`
   - `queue_dropped=0`
   - `gaps=0`
   - `avg_ms=406.53`
   - `max_ms=9536.76`
5. 日志中旧 Tab1 绘图线程仍在后台工作：
   - `Updating time plot` 出现 `33` 次。
   - `PSD input/params/output` 各出现 `4` 次。
   - 单次 PSD 输入长度为 `200000` 点。
6. 结论：当前卡顿主要不是通信丢包导致，而是 FIP 大包处理、旧 Tab1 后台时域/PSD 绘图计算和新 View PSD 实时计算共同抢占 CPU；其中旧 Tab1 绘图线程已经无可见图件，属于无效后台负载。

### 更新摘要

1. 日志文件名从 `pccp_monitor_YYYY-MM-DD.log` 改为 `pccp_monitor_YYYY-MM-DD_HH-MM-SS.log`。
2. 若启动参数指定 `--log logs/debug.log`，实际文件名会变为 `logs/debug_YYYY-MM-DD_HH-MM-SS.log`。
3. 日志仍按自然日滚动；长时间运行跨过午夜时，下一条日志会以新日期和当时的时分秒创建新文件。
4. 修复状态栏指示灯看不到的问题：
   - FIP 启动时不再使用无限期 `showMessage(..., 0)`。
   - 状态栏高度固定为 `30 px`。
   - 右下角版本标注改为短文本 `PCCP v1.0 | 中科院半导体所`，完整名称放入 tooltip。
   - 线程统计文字压缩，减少挤占状态栏空间。
5. Tab1 View 的 Time-Space 区改为可拖拽垂直 splitter：
   - Curve/PSD 区和 Time-Space 区可手动调整高度。
   - Time-Space 面板设置 `280 px` 最小高度。
   - 上方 Curve/PSD 区设置 `300 px` 最小高度，防止两区互相压扁。
6. 旧 Tab1 时域/PSD 绘图线程在 View 接管图件后自动禁用：
   - `set_plot_widgets(None, None)` 会关闭旧 `TimedomainPlotThread` 和 `PSDPlotThread`。
   - `_distribute_processed_data()` 只有在旧图件真实存在时才向旧绘图队列分发数据。
   - View 的 `时域/PSD ON/OFF` 不再误启用旧绘图线程。
7. 新 View PSD 计算节流从 `0.25 s` 调整为 `1.0 s`，减少 Welch 计算对 GUI 刷新的影响。

### 验证

1. 编译检查通过：

```text
python -m py_compile src\main.py src\ui\main_window.py src\fip_tab1\fip_tab1_manager.py
```

2. 日志文件名检查通过，输出示例：

```text
pccp_monitor_2026-07-19_01-30-45.log
```

3. MainWindow 离屏构造检查通过：

```text
{'splitter': True, 'space_min': 280, 'status_height': 30, 'psd_interval': 1.0}
```

4. 旧 Tab1 绘图线程休眠验证通过：

```text
{'time_enabled': False, 'psd_enabled': False, 'time_queue': 0, 'psd_queue': 0}
```

## 2026-07-20 17:27:23 +08:00

- GitHub 仓库：`https://github.com/chyiever/wb-monitor.git`
- GitHub 分支：`dev`
- 更新范围：`src/ui/main_window.py`、`src/main.py`、`src/fip_tab1/fip_tcp_server.py`、`src/fip_tab1/fip_tab1_manager.py`、`src/processing/phase_unwrap.py`、`src/das_tab3/das_types.py`、`src/das_tab3/das_tcp_server.py`、`src/das_tab3/das_tab3_manager.py`、`README.md`、`docs/2026-07-18-FIP和eDAS时间同步与通信检验.md`、`docs/2026-07-17-FIP-eDAS联调问题数量与修复日志.md`、`docs/2026-07-18-GUI大改日志.md`、`docs/各个tab参数含义与修改说明.md`、`docs/dev_log.md`

### 日志分析

分析文件：`logs/pccp_monitor_2026-07-19_10-06-32.log`

1. `Processing queue full` 共 `6424` 次。
2. `TAB3_NODE das_tcp.slow_receive` 共 `10342` 次。
3. `TAB3_NODE plot_worker.slow` 共 `2593` 次。
4. `Input data outside expected range` 共 `10258` 次。

### 更新摘要

1. 状态栏单位名改为 `中国科学院半导体研究所`。
2. Setting 页按钮改为 `保存全局设置（重启生效）`，点击后只保存配置并提示重启，不再立即应用当前运行界面。
3. Data 页时间同步方向统一为 `FIP-eDAS`，显示 `首包时间差（FIP-eDAS）`、`最新同序号时间差（FIP-eDAS）` 和 `平均时间差（FIP-eDAS）`。
4. FIP/eDAS 同步时间戳改为 TCP 完整包体接收完成时刻，主线程只转发 packet 自带 `receive_timestamp`。
5. 首包时间差独立保存首个匹配包，不再受 2000 包滑动缓存裁剪影响；最新和平均值按新匹配包增量更新。
6. View 不可见时跳过可见曲线和 Space-Time 刷新；PSD 更新改为短时间合并调度，降低切换 tab 和切换曲线来源时的主线程压力。
7. Data 页通信统计合并为统一表格，集中显示状态、接收包、缺包/失败、丢包率和最近 Comm。
8. FIP TCP server 补充缺包、丢包率和最近 Comm 统计。
9. FIP 相位输入增加极端值修复；`PhaseUnwrapper` 区分归一化输入和工程/弧度输入，不再对工程/弧度相位强行乘 `pi`。
10. README、GUI 大改日志、参数说明、时间同步专项文档和联调修复日志均已更新到当前行为。

### 验证

```text
python -m py_compile src\ui\main_window.py src\main.py src\fip_tab1\fip_tcp_server.py src\fip_tab1\fip_tab1_manager.py src\processing\phase_unwrap.py src\das_tab3\das_types.py src\das_tab3\das_tcp_server.py src\das_tab3\das_tab3_manager.py
```

结果：编译检查通过。

## 2026-07-29 00:00:00 +08:00

- 更新范围：`src/ui/main_window.py`、`src/main.py`、`src/fip_tab1/fip_tab1_manager.py`、`src/fip_tab2/fip_tab2_manager.py`、`src/das_tab3/das_tab3_manager.py`、`src/das_tab3/das_storage_worker.py`、`src/config/system_config.py`、`config/app_config.json`、`config/gui_last_state.json`、`docs/dev_log.md`

### 更新摘要

1. 拆分 FIP 时域显示降采样、PSD 计算前降采样和实时存储降采样：
   - Tab1 的“降采样倍数”改为“时域显示降采样”，只影响时域显示/Tab2 输入。
   - Tab4 新增“PSD降采样”，默认 `1`，PSD 使用对应采样率计算频率轴。
   - Data 页新增 FIP 实时存储降采样，默认 `1`，默认按原始 `1 MHz` 采样率保存。
2. 修复 PSD 频率范围不随采样率/降采样语义变化的问题：
   - `ProcessedData` 增加 `display_sample_rate_hz` 和 `psd_sample_rate_hz`。
   - View PSD 缓存使用 PSD 专用数据源，不再使用已经为时域显示抽点后的曲线数据推断采样率。
   - Welch 单段 FFT 上限限制为 `50000` 点，避免默认 `1 MHz` PSD 造成 GUI 超大 FFT。
3. GUI 默认值调整：
   - FIP 数量默认 `2`。
   - 全局 GUI 字体默认 `8 pt`。
   - FIP 默认“无滤波”。
   - 系统默认降采样常量调整为 `1`，默认有效采样率保持 `1 MHz`。
4. Tab1 参数合并：
   - C1/C2 DAS 带通合并为一套 “DAS带通 + DAS带通(Hz)” 参数。
   - X/Y/PSD Y 轴范围、通道范围、色标范围、FIP 截止频率、DAS 截止频率均改为 `min-max` 文本格式，例如 `0-100`、`-0.3-0.3`、`-160-20`。
   - 保留旧配置字段读取兼容，新配置会同时写入合并后的 range 字段。
5. 存储语义更新：
   - Tab1 实时存储使用独立存储降采样因子，默认 `1`。
   - Tab3 joint 存储接收 FIP 原始展开数据与原始采样率；新增 `fip_raw_data`、`fip1_raw_data`、`fip2_raw_data` 字段，并保留旧 `fip_raw_200khz` 兼容字段；格式版本更新为 `wb-monitor-joint-v4`。

### 自检

1. 编译检查通过：
```text
python -m py_compile src\ui\main_window.py src\main.py src\fip_tab1\fip_tab1_manager.py src\fip_tab2\fip_tab2_manager.py src\das_tab3\das_tab3_manager.py src\das_tab3\das_storage_worker.py src\config\system_config.py
```

2. 离屏 GUI 与处理链路自检通过：
```text
self-check ok
```

验证点：
- `config/app_config.json` 与 `config/gui_last_state.json` 可正常解析。
- GUI 默认 FIP 数量为 `2`、FIP 滤波为“无滤波”、GUI 字体为 `8`、时域显示降采样/PSD 降采样/存储降采样均为 `1`。
- 范围文本 `0-100`、`-1-1`、`-160-20`、`-0.3-0.3`、`100-10000` 均可正确解析。
- 当时域显示降采样为 `5`、PSD 降采样为 `1` 时，显示采样率为 `200 kHz`，PSD 采样率保持 `1 MHz`。
- 当 PSD 降采样改为 `4` 时，显示采样率仍为 `200 kHz`，PSD 采样率变为 `250 kHz`。
- 存储线程默认采样率为 `1 MHz`；存储降采样改为 `4` 后，存储采样率变为 `250 kHz`。

## 2026-07-29 12:43:23 +08:00

- 更新范围：`src/ui/main_window.py`、`src/main.py`、`src/das_tab3/das_plot_worker.py`、`config/app_config.json`、`config/gui_last_state.json`、`docs/dev_log.md`

### 更新摘要

1. Tab1 参数区进一步整理：
   - 原“曲线与DAS预处理”拆分为“曲线选择”和“DAS绘图”两个参数框。
   - “曲线选择”仅保留 Curve1/Curve2 来源、DAS 通道和时域显示长度。
   - “DAS绘图”新增统一 DAS 滤波勾选框、滤波参数文本框和滤波阶数。
2. FIP 绘图参数重构：
   - 原“FIP预处理”改为“FIP绘图”。
   - FIP 默认不滤波，滤波由独立勾选框控制。
   - FIP 与 DAS 滤波参数均支持 `100-` 高通、`-1000` 低通、`500-6000` 带通三种写法。
   - FIP 绘图目标下拉框支持 `FIP1`、`FIP2`、`1和2`；当 FIP 数量为 `2` 时默认选择 `1和2`。
3. Space-Time 绘图增加实时滚动参数：
   - 新增“总时间长度(s)”，默认 `5.0 s`。
   - 新增“单次平移(s)”，默认 `1.0 s`。
   - DAS Space-Time worker 使用固定窗口缓存，缓存满后按单次平移长度向左滚动，再追加最新数据。
4. 字体和图形显示优化：
   - 所有 tab 的动作按钮基础字号调大，切换类按钮和次级按钮同步增大。
   - tab 名称字号从 `16 px` 提升到 `18 px`。
   - 图坐标轴标题默认字号从 `16 px` 调整为 `12 px`。
   - Space-Time 色标刻度使用全局刻度字体设置，与其他图刻度保持一致。
   - 底部状态栏通信/存储状态、线程统计和版本文字统一为 `8 pt`。
5. 配置兼容：
   - `config/gui_last_state.json` 同步写入新的 DAS/FIP 滤波默认值、Space-Time 默认值和 FIP `plot_target=both`。
   - 旧配置缺少 `das_filter_range` 时，恢复默认范围改为 `500-6000`。
   - 遗留 `get_fip_filter_range()` 默认范围同步为 `500-6000`。

### 自检

1. 编译检查通过：
```text
python -m py_compile src\ui\main_window.py src\main.py src\das_tab3\das_plot_worker.py src\das_tab3\das_tab3_manager.py src\fip_tab1\fip_tab1_manager.py src\fip_tab2\fip_tab2_manager.py src\das_tab3\das_storage_worker.py src\config\system_config.py
```

2. 离屏 GUI 与 DAS 滤波路径自检通过：
```text
self-check ok
```

3. Space-Time 固定窗口滚动自检通过：
```text
space-time ok (10, 500) 3.0 7.0
```

验证点：
- `config/app_config.json` 与 `config/gui_last_state.json` 可正常解析。
- Tab1 存在“曲线选择”、“DAS绘图”、“FIP绘图”三个目标参数框。
- FIP 数量默认 `2`，FIP 绘图目标默认 `1和2`，FIP 默认不滤波。
- 时域显示降采样、PSD 降采样、存储降采样默认均为 `1`。
- FIP/DAS 滤波参数 `100-`、`-1000`、`500-6000` 均可解析为对应高通、低通和带通。
- Space-Time 总时间长度默认 `5.0 s`，单次平移长度默认 `1.0 s`。
- 5 秒 Space-Time 窗口填满后按 1 秒长度丢弃旧列并向左平移，继续追加最新数据。
- tab 标题字号、状态栏字号和坐标轴标题默认字号均按本次要求生效。

## 2026-08-17 17:27:29 +08:00

- GitHub 仓库：`https://github.com/chyiever/wb-monitor.git`
- GitHub 分支：`dev`
- 更新范围：`src/ui/main_window.py`、`docs/2026-08-17-FIP联调问题梳理与修复日志.md`、`docs/dev_log.md`

### 日志分析

分析文件：`logs/pccp_monitor_2026-08-17_16-44-48.log`

1. 前两次测试（16:45:18、16:47:03）首包/数据异常，判定为采集软件启动顺序不对：第一包 `raw_first=-9223372036854775808`（`INT64_MIN`）且 FIP1 全 0，第二次仅收到 1 包即中断。
2. 最后一次较长测试（16:48:09~17:00:36，约 12.4 分钟）通信稳定：`Performance Stats` 显示 `Rate: 1.0 pkt/s`，`FIP_PROCESS_STATS` 显示 `queue_dropped=0`、`gaps=0`；16:48:12 收到 `comm=0`，16:58:12 收到 `comm=600`，600 秒 600 包，连续率约 100%。
3. 处理性能 `avg_ms=95~98`、`max_ms=194~207`；View FIP 曲线偶发 `ui.fip_curve_slow`（`elapsed_ms=85~118`、`plot_points=8000`）。
4. 时域图“只在最开始出现波形”根因：`update_tab3_fip_curve()` 使用 `comm_count * packet_duration + index/sample_rate` 作为绝对累计横轴，曲线随 `comm_count` 向右移动，而横轴范围不跟随，导致只有第一包（0~1 s）可见。

### 更新摘要

1. 时域图实时显示修复：新增 FIP 曲线滚动缓存 `_fip_curve_rolling`，按“显示时长(s)”窗口裁剪；新增 `_follow_time_axis()`，在曲线 `setData` 后把横轴滑动到 `[latest-window, latest]`；FIP 与 DAS 曲线统一走 `_render_tab3_curve()` 的横轴跟随。
2. 用户手动缩放/平移或勾选“手动范围”时不再抢回横轴；点击“自动”或复位后恢复跟随；`reset_tab3_views()` 清空 FIP 滚动缓存。
3. View 参数区紧凑化：DAS绘图/FIP绘图/PSD设置分别压为 1~2 行。
4. View 绘图区由嵌套 `QSplitter` 改为单一 `QGridLayout`：两个时域图左对齐、等高；外层垂直 splitter 等比例，使两个时域图高度之和等于 Space-Time 图高度。
5. Data 参数区紧凑化：FIP通信参数 5 参数压为 1 行；eDAS通信参数压为 2 行参数 + 2 行状态；FIP/eDAS时间同步检验改为 3 列紧凑网格。

### 自检

1. 编译检查通过：
```text
python -X utf8 -m py_compile src\ui\main_window.py src\main.py src\das_tab3\das_plot_worker.py src\das_tab3\das_tab3_manager.py src\fip_tab1\fip_tab1_manager.py
python -X utf8 -m compileall -q src
```

2. 离屏 GUI 自检通过：
```text
TAB_ORDER ['View', 'Data', 'Tab3', 'Setting']
HAS True True True True
ROLLING_KEYS [(1, 1)]
ROLLING_SIZES 2000 2000
ROLLING_AFTER_RESET 0
OFFSCREEN_OK
```

验证点：
- 新参数框（DAS绘图、FIP绘图、PSD设置）可正常构造。
- FIP 曲线滚动缓存可追加、裁剪，并在 `reset_tab3_views()` 后清空。
- 横轴跟随 `_follow_time_axis()` 与 DAS Space-Time 图构造无异常。

## 2026-08-17 22:56:09 +08:00

- GitHub 仓库：`https://github.com/chyiever/wb-monitor.git`
- GitHub 分支：`dev`
- 更新范围：`src/ui/main_window.py`、`src/main.py`、`src/das/*`、`src/fip/*`、`src/detection/*`、`src/constants/*`、`src/tools/*`、`src/processing/*`（目录重命名）、`README.md`、`docs/2026-08-17-GUI布局与架构优化日志.md`、`docs/dev_log.md`

### 日志分析

分析文件：`logs/pccp_monitor_2026-08-17_21-44-17.log`

1. 通信实时性/连续率良好：`Rate=1.0 pkt/s`，`FIP_PROCESS_STATS` 全程 `queue_dropped=0`、`gaps=0`，约 22.5 分钟收到约 1350 包。
2. FIP1 通道全程输出 `INT64_MIN` 垃圾数据（`FIP_SPLIT ... FIP1:first=-5.49755814e+11`），经修复后为 0；FIP2 全程有效。属 FIP1 通道异常而非通信问题。
3. FIP1 全 0 导致 `FIRST_ZERO` 类 WARNING 每包输出、未节流，7203 行日志中占 6714 行，形成日志洪泛。
4. 偶发 `ui.fip_curve_slow`（27 次，85~126 ms，8000 点）。

### 更新摘要

1. GUI 与图件：
   - 新增全局焦点样式，去除 tab/下拉/按钮/输入框的虚线焦点框。
   - 删除“曲线选择”中冗余“显示时长(s)”，时域滚动窗口统一由“时域窗口(s)”控制；为“时域窗口/FIP刷新/eDAS刷新/单曲线点数”加 tooltip 澄清语义。
   - FIP绘图“FIP相位展开”改名 `unwrap` 并移位，降采样与滤波阶数上下对齐。
   - “坐标轴”压为一行；“Space-Time”通道范围/总时间长度/单次平移合并一行等宽。
   - 输入控件最小高度约缩小 20%。
   - 两个时域图左轴固定 88 px、两个 PSD 左轴固定 64 px，解决 Y 轴刻度位数不同导致的左右不对齐。
   - PSD 对数横轴固定 6 个刻度，避免放大重叠。
2. 架构梳理：
   - `src/config`（Python 常量）重命名为 `src/constants`，与根 `config/`（JSON）去重。
   - `tools/` 移入 `src/tools/`。
   - 包/文件重命名：`fip_tab1`→`fip`、`das_tab3`→`das`、`fip_tab2`→`detection`，内部文件去掉冗余前后缀；删除死代码 `processing/tab1_optimized_threads.py`。
3. 存储优化：
   - 联合 npz 去冗余：删除 `fip_raw_200khz/fip_display_data/fip_raw_data/fip1_raw_data/fip2_raw_data`，仅保留每路唯一字段，格式版本升级 `wb-monitor-joint-v5`，消除约 6 倍 FIP 写放大。

### 自检

1. 编译与链路验证通过：
```text
python -X utf8 -m compileall -q src
VALIDATION_OK packets_received=3 plot_payloads=3 last_shape=(16, 800) last_curve_points=2400
```

2. 离屏 GUI 自检通过：
```text
TAB_ORDER ['View', 'Data', 'Tab3', 'Setting']
HAS_DISPLAY_SECONDS_SPIN False
UNWRAP_TEXT unwrap
PSD_TICK_LEVELS 6
OFFSCREEN_OK
```

3. 联合存储去冗余自检通过：
```text
HAS_fip_raw_200khz False
HAS_fip_raw_data False
VER wb-monitor-joint-v5
```

验证点：
- 新包名 `constants`、`fip`、`das`、`detection` 及 `src/tools` 均可正常导入。
- View 页无 `tab3_display_seconds_spin`，`unwrap` 文案、PSD 6 刻度、轴宽对齐均生效。
- joint npz 不再写入冗余 FIP 字段，版本为 v5。

## 2026-08-18 10:27:40 +08:00

- GitHub 仓库：`https://github.com/chyiever/wb-monitor.git`
- GitHub 分支：`dev`
- 更新范围：`src/ui/main_window.py`、`src/fip/manager.py`、`src/fip/tcp_server.py`、`src/das/manager.py`、`src/main.py`、`docs/2026-08-17-GUI布局与架构优化日志.md`、`docs/2026-08-18-FIP联调问题梳理与修复日志.md`、`docs/dev_log.md`

### 日志分析

分析文件：`logs/pccp_monitor_2026-08-18_00-01-05.log`（189152 行，约 9.8 小时）；存储样本 `logs/0000017-FIP2-1M-20260818T000538.347.npz`。

1. TCP/处理链连续无丢包：`comm=0~34800` 共 34801 包，`FIP_PROCESS_STATS` 全程 `queue_dropped=0`、`gaps=0`。
2. 存储链路丢包严重（核心问题）：`Storage queue full` 丢 5022 包、文件间缺 4444 包、停机排空超时滞留 1981 包，合计约 7000 包（约 1.94 h、约 20%）未落盘。
3. FIP1 通道全程 `INT64_MIN`（`FIP_SPLIT ... FIP1:first=-5.49755814e+11`），修复后全 0；npz 样本中 FIP1 为 1000 万全 0、FIP2 有效（`std≈2.42`），属 FIP1 传感器硬件问题。
4. 根因：FIP-only 存储 2 倍写放大（`phase_data` + `fip1_phase_data` + `fip2_phase_data` 重复）、存储队列 2000 包约 32 GB 内存膨胀触发换页、`FIRST_ZERO` 告警 132204 行未节流、停机排空超时仅 10 s。

### 更新摘要

1. View 参数区宽度可手动拖拽调整：`_create_tab1` 改用水平 `QSplitter`，默认宽度 448 px（较旧 560 px 缩小约 20%），宽度随 GUI 参数自动持久化。
2. FIP-only 存储去写放大：`_save_chunk` 不再写冗余 `fip1_phase_data/fip2_phase_data`，仅保留 `phase_data`，格式升级 `wb-monitor-tab1-fip-v3`。
3. 存储队列容量 `RAW_QUEUE_MAXSIZE` 由 2000 降为 120，避免内存膨胀/换页拖慢磁盘。
4. 停机排空超时由 10 s 放宽到 180 s，减少尾包丢失。
5. `FIRST_ZERO`/`FIP_*_FIRST_SAMPLE_ZERO` 类告警按 `comm % 50` 节流（`fip/manager.py`、`das/manager.py`、`ui/main_window.py`、`fip/tcp_server.py`、`main.py`）。

### 自检

1. 编译检查通过：`python -X utf8 -m compileall -q src`。
2. 离屏 GUI 自检通过：`TAB_ORDER ['View', 'Data', 'Tab3', 'Setting']`、`HAS_SPLITTER True`、`PARAM_MIN 360 PARAM_MAX 720`、`DEFAULT_WIDTH 448`。
3. 存储 v3 自检通过：`STORAGE_V3_OK True QUEUE_MAX 120`（不再含 `fip1_phase_data/fip2_phase_data`）。

## 2026-08-18 11:48:46 +08:00

- GitHub 仓库：`https://github.com/chyiever/wb-monitor.git`
- GitHub 分支：`dev`
- 更新范围：`src/main.py`、`src/ui/main_window.py`、`docs/2026-08-18-FIP联调问题梳理与修复日志.md`、`docs/dev_log.md`

### 日志分析

分析文件：`logs/pccp_monitor_2026-08-18_10-53-02.log`（222 行，约 5.3 分钟）；存储样本 `logs/0000005-FIP2-1M-20260818T105350.915.npz`。

1. 第一轮修复验证通过：文件 `0000001~0000026` 的 `start_comm/end_comm` 全程连续（26-35 → … → 276-285），无 `Storage queue full`；样本 34.5 MB（旧 68 MB）、`wb-monitor-tab1-fip-v3`；`FIRST_ZERO` 仅 `comm % 50 == 0` 输出。
2. 样本数据量正确：`phase_data` shape `(2, 10000000)`、`duration=10.0s`、`sample_rate=1MHz`、`total_values=20000000`；FIP2 有效（`std≈59.79`），FIP1 全 0（传感器硬件问题）。
3. 关闭窗口尾包丢失：日志在 `Saved data to 0000026`（end_comm=285）后直接跳 `Application cleanup completed`，缺少 `Storage drain finished / All Tab1 threads stopped`，最后约 10 s（comm 286~295）未落盘。
4. 时域图偶发慢帧：`ui.fip_curve_slow` 约 9 次/5 分钟（81~162 ms、8000 点）。

### 更新摘要

1. 修复关闭窗口时 FIP 线程未优雅停止：`src/main.py::cleanup()` 增加 `self.tab1_manager.stop()`（含存储排空 + `_flush_buffered_data`），与 `_stop_monitoring` 一致；重复停止幂等安全，消除关闭窗口尾包丢失。
2. 优化 FIP 时域图慢帧：PSD 缓存已知采样率时不再构造/存储 1M 点全量时间轴（原每包分配+拷贝约 16 MB/曲线），改为缓存 `sample_rate`；`_compute_view_welch_psd` 直接使用采样率，跳过 `_estimate_sample_rate_from_times` 的 1M 点 `np.diff`。

### 自检

1. 编译检查通过：`python -X utf8 -m compileall -q src`。
2. 离屏自检通过：PSD 缓存采样率路径正常（`cache times size=0`、`cache sample_rate=1000000.0`、Welch PSD 25000 频点、范围 20~500000 Hz）；DAS 无采样率回退路径（`_estimate_sample_rate_from_times`）正常。

## 2026-08-18  eDAS模块联调前潜在风险排查与修复

- 新增报告：`docs/2026-08-18-eDAS模块联调前潜在bug和风险排查与修复报告.md`。
- 排查范围：本软件 `src/das/`、`src/alignment/`、`src/ui/main_window.py`、`src/main.py`，以及 eDAS 发送端 `E:\codes\PCIe-7821\pcie7821_gui\src\tcp_tab3\`。
- 数据量复核：100 kHz x 800 点 x float64 x 1 s 约 640,000,000 bytes（约 610 MiB），原 512 MiB payload 上限不足以接收满速 1 s 包。

### 修复摘要

1. `src/das/tcp_server.py`
   - `MAX_PAYLOAD_BYTES` 提升到 1 GiB，支持满速 1 s eDAS 包。
   - header/payload 读取失败、非法 header、非法 `data_bytes` 时关闭当前连接等待重连，避免断线空读循环和 TCP 字节流错位。
   - `_recv_exact()` 返回 `bytearray`，big-endian `float64` 原地 byteswap 为本机字节序，减少大 payload 的整包额外拷贝。

2. `src/das/plot_worker.py`
   - 绘图队列新增 768 MiB 字节预算，超过预算时丢弃旧显示帧，保护通信和主流程。
   - eDAS 1 s 显示窗口历史裁剪改为严格大于左边界，避免边界上多保留前一秒整包。

3. `src/das/storage_worker.py`
   - eDAS raw 存储队列新增 2 GiB 字节预算，避免 UI 默认 200 包在满速下膨胀到百 GB 级。
   - 联合 FIP+eDAS 存储请求新增 `estimated_bytes`，队列按字节预算丢旧请求。
   - 单次联合 `.npz` 请求超过 2 GiB 时拒绝写入并向 UI 报错，提示缩短间隔、发送端降采样或改用 eDAS raw 存储。

4. `src/alignment/aligned_session_coordinator.py`、`src/das/manager.py`
   - 对齐缓存新增 2 GiB 字节预算，按“时间窗口 + 字节预算”共同裁剪。
   - 缓存接近字节预算时允许提前 flush 较短联合 chunk，避免满速大包下永远达不到 10 s interval。
   - eDAS 停止时存储线程排空等待从 5 s 延长到 180 s。

5. eDAS 发送端 `pcie7821_gui`
   - `PhaseQueueItem` 增加 `comm_count`。
   - `TCPTab3Manager` 在采集帧聚合成通信包时分配 `_next_comm_count`。
   - `TCPSenderWorker` 使用 `item.comm_count` 构包，不再按发送成功递增序号。
   - 修复网络未连接、发送失败或发送队列丢旧包时 eDAS 序号仍连续导致的 FIP/eDAS 假对齐风险。

### 验证

```text
python -X utf8 -m py_compile src\das\tcp_server.py src\das\plot_worker.py src\das\storage_worker.py src\das\manager.py src\alignment\aligned_session_coordinator.py
python -X utf8 src\tools\validate_tab3_pipeline.py
python -X utf8 -m compileall -q src
EDAS_SAFETY_OK 1 3 2 2 3221225472
```

eDAS 发送端：

```text
python -X utf8 -m py_compile src\tcp_tab3\tcp_types.py src\tcp_tab3\tcp_tab3_manager.py src\tcp_tab3\tcp_sender_worker.py
python -X utf8 -m unittest tests.test_tcp_tab3_comm_count
python -X utf8 -m unittest discover -s tests
```

结果：本软件 eDAS TCP 模拟验证通过，发送端 8 项测试通过。后续现场重点观察 `DAS comm_count gap`、`Alignment cache byte budget trimming active`、`storage.edas_enqueue queued_mb`、`.json` 中 `comm_counts` 连续性和 FIP/eDAS 同序号接收时间差。

## 2026-08-19 23:52:40 +08:00

- GitHub 仓库：`https://github.com/chyiever/wb-monitor.git`
- GitHub 分支：`dev`
- 更新范围：`src/das/plot_worker.py`、`src/ui/main_window.py`、`src/das/manager.py`、`docs/2026-08-19-View-DAS-Space-Time基线与色阶修复日志.md`、`docs/dev_log.md`

### 问题现象

View 页 DAS Space-Time 图长期以深蓝或深红为主，手动 V 范围从 `-1~1` 扩大到 `-3~3` 图像仍基本不变。检查现场导出数据（51 通道 x 10000 点、共 10 块）发现：多数通道含有接近 `±4π` 的固定相位基线（如通道 0 均值 `-12.5097`、通道 8 均值 `12.4716`），98.04% 样本 `abs(value) > 3`，原始小幅噪声变化被固定基线 + 手动色阶完全淹没。

### 更新摘要

1. `src/das/plot_worker.py`
   - Space-Time 显示帧改为逐通道中位数去基线：生成独立 C-order `float32` 副本后按时间轴 `np.nanmedian` 扣除各通道固定基线，中位数较均值更抗短时冲击。
   - 修复跨线程共享内存：`np.ascontiguousarray` 改为 `np.array(..., copy=True, order="C")`，保证 `plot_payload_ready` 携带独立矩阵，后台滚动缓冲原地更新不再污染已交给 UI 的帧。
   - 新增 `space_time_remove_baseline` 设置项，接入 worker 设置签名与日志。
2. `src/ui/main_window.py`
   - Space-Time 参数区新增 `逐通道去基线`（默认开）与 `自动色阶`（默认开）两个开关；手动 V 范围默认改为 `-1~1`，自动色阶开启时禁用输入框。
   - 自动色阶改为稳健对称范围 `(-P99.5(abs), +P99.5(abs))`，上限用 `alpha=0.2` 指数平滑降闪烁；新会话/清空 Space-Time 时重置平滑状态。
   - 两个开关均接入 GUI 配置保存/恢复，去基线关闭可查看原始绝对相位。
3. `src/das/manager.py`
   - 新增 `_log_das_value_stats`：每 50 包从原始矩阵有界等步长抽样约 100,000 样本，记录 min/max、1%/99% 分位数、median、NaN/Inf 数量，日志节点 `TAB3_NODE manager.das_values`，避免对最大规格 DAS 包做全矩阵分位数计算。

### 验证

1. 编译检查通过：`python -m py_compile src/das/plot_worker.py src/das/manager.py src/ui/main_window.py`。
2. `git diff --check` 通过，仅显示工作区既有 LF/CRLF 转换提示。
3. 导出数据数值验证：原始范围 `-13.0097 ~ 12.7132`，去基线后 `-0.6036 ~ 0.7588`，1%/99% `-0.1889 ~ 0.1914`，自动色阶 `-0.2974 ~ 0.2974`，超出比例约 0.5%，符合预期。
4. worker 路径验证：`ACTUAL_WORKER_OK shape=(51, 1820) owns=True min=-0.3300 max=0.3874 p99.5(abs)=0.1882`，连续帧 `np.shares_memory` 均为 `False`。
5. 已执行 UTF-8 中文自检，本次修改文件未发现问号乱码。

## 2026-08-20 00:38:07 +08:00

- GitHub 仓库：`https://github.com/chyiever/wb-monitor.git`
- GitHub 分支：`dev`
- 更新范围：`src/das/tcp_server.py`、`src/tools/simulate_das_client.py`、`docs/2026-6-19-通信协议与数据包格式.md`、`docs/dev_log.md`
- 关联仓库：`https://github.com/chyiever/pcie7821_gui.git`（发送端 `src/tcp_tab3/tcp_packet_builder.py`）

### 背景

原 DAS TCP 协议载荷为大端 `float64` 弧度，满速 1 s 包约 610 MiB（100 kHz x 800 点 x 8 字节），对应约 2.95 Gbps，超过 1 Gbps 链路，导致每包实际接收约 3.2 s、timespace 图与 comm 计数增长变慢。改用 `int32` 发送原始相位计数可把载荷减半到约 305 MiB。

### 更新摘要

1. `src/das/tcp_server.py`
   - 载荷校验由 `data_bytes % 8 != 0` 改为 `% 4 != 0`，`total_points = data_bytes // 4`。
   - 解析由 `>f8` 改为大端 `>i4`，原地 byteswap 后转 `int32`，再 `astype(float64)` 并乘 `DAS_INT32_TO_RADIANS`（`pi / 32767`）恢复弧度。
   - 新增常量 `DAS_PHASE_FIXED_POINT_SCALE = 32767.0`、`DAS_INT32_TO_RADIANS`，`MAX_PAYLOAD_BYTES` 注释更新为 int32 口径。
   - `DASRawPacket.data_1d` 仍为 `float64` 弧度，下游绘图、对齐、存储链路无感知变化。
2. `src/tools/simulate_das_client.py`
   - 模拟客户端改为生成 `int32` 相位计数并按 `>i4` 发送，`amplitude` 正弦先转弧度再 `round(x * 32767/pi)` 量化。
3. `docs/2026-6-19-通信协议与数据包格式.md`
   - 载荷描述由大端 `float64` 更新为大端 `int32`，`N = data_bytes/(4C)`，明确客户端不再做弧度换算、由服务端接收后统一 `phase_rad = phase_int32/32767*pi`，流程图 `Decode >f8` 改为 `Decode >i4`。
4. `docs/2026-07-17 数据存储.md`
   - eDAS TCP 载荷由 `>f8` 更新为 `>i4 int32`，写入流程同步说明服务端转换为 `float64` 弧度后仍以 `float64` 落盘。
5. 附：修复 DAS 断流误报。`_recv_exact()` 每次成功 `recv()` 到 chunk 后刷新 `_last_data_time`，watchdog 由「距最后完整包」改为「距最后收到字节」计时，大包慢收时不再误报「DAS has not received data for 10 seconds」。

### 验证

1. 编译检查通过：`python -X utf8 -m py_compile src\das\tcp_server.py src\tools\simulate_das_client.py`。
2. 端到端管道自检通过：`python -X utf8 src\tools\validate_tab3_pipeline.py` 输出 `VALIDATION_OK packets_received=3 plot_payloads=3 last_shape=(16, 800)`。
3. 发送端 `pcie7821_gui` 单测通过：`python -X utf8 -m unittest discover -s tests` 输出 `Ran 8 tests ... OK`。
4. 数值 round-trip 自检：int32 载荷解码值与发送矩阵逐值一致，`rad = int32/32767*pi` 与预期一致，`data_bytes` 由 64 降为 32（减半）。

## 2026-08-20 01:20:00 +08:00

- GitHub 仓库：`https://github.com/chyiever/wb-monitor.git`
- GitHub 分支：`dev`
- 更新范围：`src/das/tcp_server.py`、`src/tools/simulate_das_client.py`、`docs/2026-6-19-通信协议与数据包格式.md`、`docs/2026-07-17 数据存储.md`、`docs/dev_log.md`
- 关联仓库：`https://github.com/chyiever/pcie7821_gui.git`（发送端 `src/tcp_tab3/tcp_packet_builder.py`）

### 背景

现场 100 kHz x 461 通道 int32 联调日志显示丢包率约 38%（接收端 `packets=113 missing=69 last_comm=181`）。根因是带宽饱和：单包 184.4 MB、数据率约 1.48 Gbps，超过 1 Gbps 链路（发送端 `Slow TCP send` 约 1.4–1.6 s/包、接收端 `data_rate≈103 MB/s`），发送端 `queue_max=8` 队列持续堆满并「丢最旧包」。

### 更新摘要

1. `src/das/tcp_server.py`
   - payload 字节序由大端 `>i4` 改为小端 `<i4`（x86 本机字节序），去掉热路径上的 in-place byteswap。
   - 解析改为单趟 `np.frombuffer(payload, dtype="<i4").astype(np.float64)` 后原地乘 `DAS_INT32_TO_RADIANS`，减少一次整包内存读写。
2. `src/tools/simulate_das_client.py`
   - 模拟客户端载荷由 `>i4` 改为 `<i4`。
3. 文档同步：协议文档、数据存储文档改为小端 `int32` 口径。

### 效果与结论

- 小端去掉两端字节交换，实测 46.1M 样本下发送端构包由约 128 ms 降至约 39 ms，接收端解析省去约 60–100 ms 字节交换；属约 10% 开销优化，不能消除丢包。
- 丢包的根本解法仍需在发送端 `Tab3` 通信参数开启 `time_downsample` 或 `space_downsample`（任一 `=2` 即可把 184.4 MB/s 降到 92.2 MB/s ≈ 0.74 Gbps，落到 1 Gbps 内），或升级到 10 Gbps 链路。

### 验证

1. 编译检查通过：`python -X utf8 -m py_compile src\das\tcp_server.py src\tools\simulate_das_client.py`。
2. 端到端管道自检通过：`python -X utf8 src\tools\validate_tab3_pipeline.py` 输出 `VALIDATION_OK packets_received=3 plot_payloads=3 last_shape=(16, 800)`。
3. 发送端 `pcie7821_gui` 单测通过：`python -X utf8 -m unittest discover -s tests` 输出 `Ran 8 tests ... OK`。
4. 小端 round-trip 自检：int32 载荷解码值与发送矩阵逐值一致，`data_bytes=32` 减半保持。
