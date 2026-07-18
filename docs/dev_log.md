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
logs/pccp_monitor_YYYY-MM-DD.log
```

3. 若启动参数指定 `--log some/path/debug.log`，实际写入文件会自动变为：

```text
some/path/debug_YYYY-MM-DD.log
```

4. 软件长时间连续运行跨过午夜时，下一条日志会自动切换到新日期文件，不再一直追加到同一个文件。
5. 日志时间格式固定为 `YYYY-MM-DD HH:MM:SS`，每条日志都包含日期和时间。
6. 日志初始化信息补充 `file` 和 `daily_base`，方便从日志头部确认实际写入文件和配置基路径。

### 验证

- `python -m py_compile src\main.py` 通过。
- `DailyFileHandler` 合成写入验证通过，确认生成 `logs\daily_handler_smoke_2026-07-19.log`，且日志内容以日期时间开头。
- 中文自检通过：`src/main.py` 和 `docs/dev_log.md` 未发现替换字符或中文行问号乱码。
