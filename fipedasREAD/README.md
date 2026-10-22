# FIP/eDAS Joint Replay

`fipedasREAD` 是独立的 FIP/eDAS 数据回放工具，支持 wb-monitor 联合 NPZ、BIN v1（FIPeDAS1）、bin32 v2（FIPeDAS2）、H5，以及已有的独立 FIP / eDAS 文件格式。

## 2026-10-23 大文件快速查看

- 当前源码的 BIN/bin32 打开路径采用只读内存映射：先扫描包头建立索引，按所选通道和预览访问文件，不先把整个多 GB 文件复制到内存。bin v1 保留磁盘 float64，bin32 保留 float32；只在预览时转换所需片段。
- 时空图在转换和拼接前按默认约 30 万像素预算增加时间抽样，避免先分配完整图像再缩小。显示滤波使用实际预览采样率；预览不替代完整原始数据分析。
- 检查截断、负长度、非法 FIP 数组数量及超大元数据头，错误文件明确报错；后台跳过排队中过期的重绘请求，缓存键检查文件大小和修改时间。
- 映射期间不要改写/删除当前文件；重新加载可使缓存失效。NPZ/H5 仍采用原加载路径，压缩文件不具备同样的按需映射性能。
- 本机 132,000,727 字节 bin32 热缓存合成对照：整文件复制约 69.7 ms、RSS 增加约 132 MB；映射索引约 0.86 ms，299136 像素预览约 26.2 ms。索引时间不等于完整冷盘读取时间，实际访问数据页仍占用内存/文件缓存。
- 旧 dist EXE 不会自动更新；使用当前源码，或运行本目录 build_exe.py 重新打包。本次生成的新回放 EXE 存放本地 dist，仍不上传 GitHub。

## 启动

在 `wb-monitor` 仓库根目录执行：

```powershell
python .\fipedasREAD\run.py
```

指定 joint 数据目录：

```powershell
python .\fipedasREAD\run.py D:\PCCP\FIPeDASDATA
```

指定单个 joint 文件：

```powershell
python .\fipedasREAD\run.py D:\PCCP\FIPeDASDATA\FIPeDAS-20260916-120000.000.npz
```

## 依赖

可使用本目录的依赖文件安装：

```powershell
pip install -r .\fipedasREAD\requirements.txt
```

当前依赖与主工程一致：

- PyQt5
- pyqtgraph
- numpy
- scipy

## 打包为独立 exe

使用 PyInstaller 打包（需先 `pip install pyinstaller pillow`）：

```powershell
python .\fipedasREAD\build_exe.py
```

生成的 exe 位于 `fipedasREAD\dist\FIPeDASReplay-YYYY.M.D.exe`（名称默认含当天日期），可在未安装 Python 的 Windows 机器上直接运行。常用变体：

```powershell
python .\fipedasREAD\build_exe.py --console      # 带控制台的调试版
python .\fipedasREAD\build_exe.py --onedir       # 文件夹版（启动更快）
python .\fipedasREAD\build_exe.py --clean-only   # 仅清理旧产物
python .\fipedasREAD\build_exe.py --name 自定义名称
```

## 功能

- 兼容 FIP 独立 `*.npz`、eDAS 独立 `*.bin + *.json`、FIP+eDAS 联合 `*.npz` / `*.bin` / `*.h5`（自动识别，缺失数据源正常显示）。
- 左侧提供数据路径、文件列表、时域曲线源、FIP 预处理、EDAS 预处理和 timespace 参数。
- 文件信息栏显示数据类型、开始采集时刻、时长、采样率、DAS通道数与数据量。
- 文件列表支持多选（Ctrl/Shift）：多个文件按时间拼接后统一绘图，时间轴单调连续。
- 右侧显示两张时域图和一张 DAS timespace 图。
- 三张图共用横轴时间范围：任意一张图矩形放大或平移后，其余两张同步。
- 三张图采用垂直分割器，默认高度比例为 `1:1:2`，可手动拖动调整。
- 左侧参数区分成两个 tab：「数据读取」（路径、文件列表）与「曲线·预处理·显示」（时域曲线源、FIP/EDAS 预处理、timespace）。
- 所有参数（含频带、通道范围输入）调整后经 120ms 去抖自动生效，无需手动应用；「重新加载数据」按钮用于清除后台缓存并强制从磁盘重读当前文件。
- EDAS 预处理参数同时作用于 DAS 时域曲线（波形源为 `DAS Channel` 时）与 DAS timespace 瀑布图。
- timespace 默认 `Seismic` 色图，可切换 `RdBu`、`CoolWarm`、`Viridis`、`Plasma`、`Inferno`、`Magma`、`Gray` 或 `Jet`。
- 两张时域图右侧保留与 timespace 色标等宽的空白区，保证三张图真实绘图区宽度一致。
- 顶部标题栏显示软件名称与版本号（当前 `v1.2.0 · 2026-09-17`）。
- **参数记忆**：所有界面参数自动保存，下次打开沿用上次设置；数据路径也会记忆（命令行参数仍优先）。
- **运行日志**：软件在本目录 `logs/fipedas_read.log` 生成运行日志（2MB 轮转、保留 3 份）。
- **性能**：超长序列先降采样再滤波，并缓存绘图结果，切换/重复查看文件更快。

更多字段说明和 GUI 细节见 [docs/README.md](docs/README.md)。

## 2026-10-21 双版本存储格式

内置版联合存储新增 `FIPeDAS2` 魔数与 `bin32` 格式（DAS float32、FIP float64）。当前 data_loader.py 同时支持旧 `FIPeDAS1` 与新格式。历史 dist EXE 未重打包；回放新格式请从当前源码运行。
