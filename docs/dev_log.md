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
