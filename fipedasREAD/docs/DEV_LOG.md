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