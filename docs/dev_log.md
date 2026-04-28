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
