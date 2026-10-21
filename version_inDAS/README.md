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

dev2 最新参数界面：eDAS 页主区只显示配置摘要；点击“采集参数设置…”在七个小 Tab 中编辑详细参数。原 Setting 的硬件、采集时长和存储参数均在其中。参数区默认宽度与 View 一致，并可联动拖动调宽；采集中配置只读，停止后可修改，取消不改变配置。

完整检查记录见 [Tab2 参数弹窗与链路检查](docs/2026-10-21-Tab2参数弹窗与链路检查.md)。

dev2 绘图控制：波形数据源可选 Phase、RAW、Monitor；Phase/RAW 切换涉及采集卡上传数据，需停止采集后切换，Phase/Monitor 支持在线切换。PSD 后的 TIME/SPACE 下拉框沿用原上位机语义：TIME 显示每帧沿光纤的波形，SPACE 显示所选 CH 随时间变化，并与时空图时间轴联动。CH 从裁剪后的第一个通道 0 开始。关闭 PSD 后，其空间由时空图填满。

所有图的刻度、轴标签和标题字体统一为 Times New Roman，默认 12 pt，在“Setting → 所有图刻度与标签字号”统一调整，View/eDAS 同步生效；GUI 参数文字默认 10 pt，按钮 14 pt。旧版分别设置的字号迁移到这一入口。在“采集参数设置 → 绘图显示”可设置时空图纵轴为 Points、Distance (m) 或 Distance (km) 及偏移 x0（默认 0，使用当前纵轴单位）。距离坐标为点号×dx+x0；km 模式中 dx 自动除以 1000，裁剪、共享和落盘仍使用原始通道索引及数据。参数窗口外点击会放弃草稿，只有“应用并保存”生效。

View 参数区保留曲线选择，其他绘图配置通过“绘图参数设置…”打开六页草稿窗口（FIP 绘图、DAS 绘图、显示与刷新、PSD、坐标轴、时空图），应用并保存后写回并更新处理/显示链路。取消或点击窗口外不改变配置。参数区底部 Start 先启动 FIP，1 s 后启动 eDAS；Stop 先停止并排空 eDAS，再停止 FIP，同时取消等待中的启动。FIP 启动失败不会继续启动 eDAS；eDAS 启动失败时明确提示，FIP 保持运行。FIP 第三页不再提供 eDAS 启停按钮，eDAS 独立启停仍在第二页。

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
