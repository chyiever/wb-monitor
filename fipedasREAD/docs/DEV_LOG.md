# 开发日志（fipedasREAD 回放工具）

## 2026-09-16

### 1. Timespace 图新增 Vmin/Vmax 手动色阶

**问题**：Timespace 图仅支持「自动色阶」（百分位色阶），无法手动固定色标范围，不方便对比不同文件、不同区段之间的色度。

**修改**：

- `src/fipedas_read/viewer.py`
  - `_build_space_group()` 中新增 Vmin / Vmax 两个 `QDoubleSpinBox`（范围 ±1e12，3 位小数），放入 Timespace 参数区。
  - `_draw_space_time()` 中：勾选「自动色阶」时使用 `robust_levels()` 计算，并把计算得到的色阶回填到 Vmin/Vmax 输入框（回填时 `blockSignals` 避免触发重绘死循环）；取消勾选时使用输入框中的 Vmin/Vmax（若 Vmin > Vmax 自动交换）。
  - 两个输入框加入信号联动，值变化自动重绘。

### 2. 数据加载与预处理多线程化（解决切换文件卡死）

**问题**：切换数据文件时界面「未响应」。原因：

- `_load_file()` 在 GUI 线程同步读取整个 NPZ（`load_joint_npz` 用 `allow_pickle=True` 并把 object 数组 `tolist()` 逐项转换）。
- `_redraw_current()` 在 GUI 线程同步完成全部重算：两条曲线逐帧拼接全速率时间轴 + 4 阶 `sosfiltfilt` 带通滤波（FIP1/FIP2 各一遍），timespace 逐帧切片拼接 + 色阶百分位。
- 所有控件 `valueChanged/toggled` 都直接触发 `_redraw_current`，切换选择时多次连锁重算。
- 无缓存、无去抖。

事件循环被以上重活长时间阻塞，Windows 判定窗口无响应。

**方案**：数据加载与预处理全部移入后台 `QThread`，GUI 线程只负责发请求、收结果、更新绘图。

- 新增 `src/fipedas_read/worker.py`
  - `ReplayWorker(QObject)`，通过 `moveToThread` 放入独立 `QThread`。
  - 以 `RedrawRequest`（含两条曲线与 timespace 的全部参数）作为输入，通过槽 `redraw()` 接收请求。
  - 内部按路径缓存 `JointReplayData`（默认缓存 4 个文件，LRU 式淘汰），切换回已看过的文件免重复 IO。
  - 计算完成后通过信号 `redrawDone(RedrawResult)` 返回：两条曲线降采样后的数据、timespace 矩阵/矩形/色阶、文件信息文本。
  - 出错通过 `failed(message, seq)` 返回。

- 修改 `src/fipedas_read/viewer.py`
  - 删除 GUI 线程内的 `_load_file` / `_redraw_current` / `_draw_curve` / `_draw_space_time` / `_update_file_info` 重活逻辑，改为：
    - `_schedule_redraw()`：从控件收集参数生成 `RedrawRequest`，递增请求序号 `_worker_seq`，用 120ms 单发 `QTimer` 去抖合并快速连续操作。
    - `_flush_requests()`：去抖到期后把请求发给 worker。
    - `_on_redraw_done()`：仅接受序号等于当前 `_worker_seq` 的结果（丢弃过期结果），`_apply_result()` 只做纯 UI 更新（`setData`/`setImage`/`setLevels`/标题/信息栏/状态栏）。
    - `_on_worker_failed()`：序号匹配时清空绘图并弹窗提示。
    - `closeEvent()` 中停止去抖定时器、`quit()` + `wait()` 回收 worker 线程，避免退出时崩溃。

**效果**：

- 文件加载、滤波、矩阵拼接都在后台线程，切换文件时界面保持可响应（可继续点击/缩放）。
- 快速连点多个文件时，去抖合并请求，且只有最新请求的结果会被应用。
- 缓存命中时切回文件无需重新读盘。

**验证**：

- 生成两个测试 `FIPeDAS-*.npz`，目录模式启动后快速切换，状态栏最终显示「已加载 FIPeDAS-b.npz」，无异常。
- 单文件加载/曲线/timespace/手动色阶均正常（offscreen 冒烟测试通过）。

### 待改进

- 滤波目前仍作用在全速率序列上（默认降采样 1）。若后续需要更大文件更快出图，可改为「先降采样再滤波」或引入金字塔缓存。
- 手动色阶与直方图拖动的联动可进一步细化。

## 2026-09-16（同日）

### 3. 界面布局与颜色设计优化（参考科研可视化设计规范）

**参考依据**（网上检索到的科研数据处理可视化设计规范）：

- 科研图表配色应使用色盲友好（colorblind-safe）的色板；禁用红绿二值编码，颜色应与形状/标签等第二通道配合。
- 热图（heatmap）应使用感知均匀的**顺序色图**（表示量级）或**发散色图**（围绕有意义中点，如零/基线）；避免使用彩虹（Jet）色图造成的伪边界。
- 离散类别色板建议不超过 6 种，选择 Okabe-Ito 等分离度高的色板。
- 界面应保持一致的间距、对齐与分组，让颜色服务于「减少读者理解负担」而非装饰。

**修改**（`src/fipedas_read/viewer.py`）：

- **曲线配色**：波形1 由 `#006d77`（青绿）改为 Okabe-Ito 色盲安全的 `#0072B2`（蓝），波形2 由 `#c1121f`（红）改为 `#D55E00`（朱红），蓝/朱红组合在色觉缺陷下仍可区分；线宽 1.4→1.6 提升可读性。
- **色图**：新增发散色图 `RdBu`、`CoolWarm`（围绕白色中点，适合 DAS timespace 的零基线数据），保留 `Jet` 置于列表末尾（不推荐但兼容）。
- **全局样式（QSS）**：
  - `QGroupBox` 圆角边框 + 浅色背景 + 加粗标题，视觉分组更清晰。
  - 输入框/下拉框统一 1px 边框 + 圆角，聚焦时高亮为品牌蓝。
  - 按钮 hover/pressed 状态反馈。
  - 文件列表选中项浅蓝高亮，状态栏浅灰底色。
- **交互**：
  - 「自动色阶」开启时 Vmin/Vmax 输入框自动禁用（置灰提示不可编辑），取消自动后恢复可编辑。
  - Vmin/Vmax、自动色阶增加 tooltip 说明。
  - 窗口默认尺寸 1500×900 → 1600×960，左栏与绘图区分配更均衡。

**验证**：offscreen 冒烟测试通过——文件加载、曲线/timespace 渲染正常；自动色阶切换时 Vmin/Vmax 禁用/启用正确；新增 RdBu/CoolWarm 色图可正常切换。

### 4. 软件名称/版本栏与文件信息增强

**需求**：

1. 显示数据文件信息：开始采集时刻、数据量、通道数、时长、采样率等。
2. 窗口最上方添加软件名字。
3. 添加开发版本日期。
4. 更新开发文档。

**修改**：

- `src/fipedas_read/viewer.py`
  - 新增类常量 `APP_NAME`（`FIP/eDAS 联合数据回放`）、`APP_VERSION`（`v1.1.0`）、`APP_BUILD_DATE`（`2026-09-17`）。
  - 窗口标题设为 `软件名 版本号`。
  - 新增 `_build_header()`：顶部深蓝色标题栏，左侧软件名（加粗白字），右侧版本号 + 构建日期（浅蓝），圆角与整体 QSS 风格一致。
- `src/fipedas_read/worker.py`
  - `_file_info_text()` 重写为结构化多行信息：
    - `格式`：数据格式版本。
    - `采集时刻`：使用文件 `created_at` 字段（回放起点墙钟时间）。
    - `时长`：`end_time - start_time`，附帧数。
    - `采样率`：FIP / DAS 中值采样率。
    - `通道数`：DAS 最大通道数。
    - `数据量`：FIP/DAS 总采样点数（k/M 单位）+ 文件大小（KB/MB）。
  - 新增 `_sum_samples()` / `_human_samples()` / `_human_bytes()` 辅助函数。

**验证**：offscreen 冒烟测试通过——文件信息正确显示采集时刻、时长、采样率、通道数与数据量；窗口标题与顶部标题栏显示软件名 + 版本号。

**后续微调**：顶部标题栏软件名改为水平居中并放大（字号 15px → 24px），版本号仍靠右显示。

### 5. 兼容单独 FIP、eDAS 与多种联合格式

**需求**：根据 `wb-monitor/docs/FIP／eDAS 通信协议与数据存储机制.md`，让回放工具兼容单独的 FIP 数据、单独的 eDAS 数据，以及不同格式的联合数据，并更新开发文档与 README。

**新增支持的格式**（`src/fipedas_read/data_loader.py`）：

| 数据 | 格式 | 版本 |
|---|---|---|
| FIP 独立 | `*.npz` | `wb-monitor-tab1-fip-v3` |
| eDAS 独立 | `*.bin + *.json` | `wb-monitor-edas-raw-v1` |
| 联合 | `*.npz` | `wb-monitor-joint-v5` / `-v6`（原有） |
| 联合 | `*.bin` | `wb-monitor-joint-bin-v1`（magic `FIPeDAS1` 自描述） |
| 联合 | `*.h5` | `wb-monitor-joint-h5-v1`（HDF5，依赖 h5py） |

**实现要点**：

- `load_fip_npz()`：读取 `phase_data`（1D 或 `sensor_count×N`），拆分 FIP1/FIP2；采样率取 `sample_rate` 或 `raw_sample_rate_hz`；`data_info` 中提取 `stream_start_time` 作为采集时刻。
- `load_edas_bin_json()`：读 `.json` 元数据（`matrix_shape_per_block`、`blocks_written`、`comm_counts`、`packet_start_times` 等），把 `.bin` 的 `<f8` 矩阵切分为逐帧 `das_frames`。
- `load_joint_bin()`：校验 magic `FIPeDAS1`，解析 JSON 头与逐帧记录（`<iddBBididi` 定长头 + FIP 数组 + eDAS 矩阵）。
- `load_joint_h5()`：读取 HDF5 数据集与根 attrs（`fip1_raw`/`fip2_raw`/`das_raw`/`comm_counts` 等）。
- `load_data_file(path)`：按后缀与关键字段自动分派（npz 通过关键字段区分联合/FIP 独立）。
- `iter_replay_files()`：自动发现 `FIPeDAS-*.npz/.bin/.h5`、`*-FIP*.npz`、`*-eDAS-*.bin(+json)`。
- 所有格式统一归一化为 `JointReplayData`（`_make_joint()` 统一补齐帧数、`fip_present`/`das_present` 掩码），上层 worker/viewer 无需感知格式差异。
- 文件信息栏新增「数据」行，标注 `FIP` / `eDAS` / `FIP+eDAS`；缺失的数据源对应曲线/图标题显示「无 FIP 数据」/「无 eDAS 数据」。
- `requirements.txt` 新增 `h5py`（读取联合 `.h5` 需要）。

**验证**：对五种格式各生成样例文件，`load_data_file` 全部正确读取（帧数、FIP/eDAS 存在标志、矩阵形状正确）；GUI 全流程逐文件切换冒烟测试通过。