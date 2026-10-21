# wb-monitor 内置 eDAS 版本

版本：**v_inDAS_261021**；对应 GitHub `master2/dev2`。

本版本将 PCIe-7821 eDAS 采集上位机与 wb-monitor 合并到一个进程。外置 TCP eDAS 版本保存在相邻的 `version_exDAS`，阶段版本为 `v_exDAS_261021`。

## 启动

在本目录安装依赖并启动：

```powershell
python -m pip install -r requirements.txt
python run.py
```

无采集卡时：

```powershell
python run.py --simulate
```

硬件模式需要 Windows、匹配采集卡的驱动，以及 `libs/pcie7821_api.dll`。启动脚本会将当前工作目录固定到本版本目录；两版本参数、日志相互独立。

## 页面

- **View**：FIP 与共享 eDAS 数据的联合显示，保留原曲线、PSD、时空图及刷新控制。
- **eDAS**：采集、相位处理、原 Setting 参数、独立 DAS 保存和共享数据开关；右侧为统一波形/Monitor 趋势、可关闭 PSD 和时空图。左侧参数较多，可滚动查看。
- **FIP**：FIP TCP、两路对齐状态、FIP 独立保存与联合保存；eDAS 改为本机采集，不再配置 eDAS TCP 地址/端口。
- **Setting**：字体与全局显示设置，控制是否显示旧信号检测 Tab3。

先核对采集参数与 FIP 包周期，再启动采集。共享要求单上传通道 PHASE 模式、共享周期与 FIP 包时长一致。关闭共享不会停止独立 DAS 采集或保存，也不会重置采集序号。RAW/多上传通道可用于独立采集保存，共享链路不开放。

## 保存格式与高速使用

- eDAS 页独立 BIN 保存原始 int32 计数；BZ 提供压缩选项，但本次没有验证其在 600 MB/s 下持续运行。
- 联合默认 **bin32**：FIP float64、DAS float32 弧度，具有明确的新魔数 `FIPeDAS2`。float32 约 7 位有效数字；需要原始计数时同时启用 DAS 独立保存。
- 联合 **bin** 保留旧 float64 格式和旧回放兼容性；磁盘写入量约为 DAS int32 输入的两倍。
- 根目录 `fipedasREAD` 的回放源码已支持 bin32。历史 dist EXE 未重打包，不能读取新格式；请使用更新后的源码运行回放。
- 10 秒 E 盘合成测试中，600 MB/s 输入同时独立/联合保存无丢块，bin32 结束排空约 47 ms；这不等于采集卡或长时间写入认证。
- 字节预算、磁盘错误或队列溢出会明确报告并停止采集；故障会话不能作为完整无损采集使用。

## 文档和验证

详见 [集成设计与存储评估](docs/2026-10-21-inDAS集成与存储评估.md) 与 [开发日志](docs/dev_log.md)。

```powershell
python -m unittest discover -s tests -v
python -m compileall -q src
```

实际写盘复测（临时数据会自动清理）：

```powershell
python src/tools/benchmark_storage.py --directory E:/scratch/wb-test --seconds 10 --joint-format bin32 --report result.json
```

本次已验证软件仿真、文件回读、短时实盘吞吐及正常/故障路径；真实硬件、两路物理同步触发和长时间运行需要现场验证。
