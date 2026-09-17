# FIP/eDAS Joint NPZ Replay

`fipedasREAD` 是一个独立的 FIP/eDAS 联合数据回放工具，用于查看 `wb-monitor` 生成的 `FIPeDAS-*.npz` 文件。

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

## 功能

- 兼容 FIP 独立 `*.npz`、eDAS 独立 `*.bin + *.json`、FIP+eDAS 联合 `*.npz` / `*.bin` / `*.h5`（自动识别，缺失数据源正常显示）。
- 左侧提供数据路径、文件列表、时域曲线源、FIP 预处理、EDAS 预处理和 timespace 参数。
- 文件信息栏显示数据类型、采集时刻、时长、采样率、通道数与数据量。
- 右侧显示两张时域图和一张 DAS timespace 图。
- 三张图共用横轴时间范围：任意一张图矩形放大或平移后，其余两张同步。
- 三张图采用垂直分割器，默认高度比例为 `1:1:2`，可手动拖动调整。
- timespace 默认 `Seismic` 色图，可切换 `RdBu`、`CoolWarm`、`Viridis`、`Plasma`、`Inferno`、`Magma`、`Gray` 或 `Jet`。
- 两张时域图右侧保留与 timespace 色标等宽的空白区，保证三张图真实绘图区宽度一致。
- 顶部标题栏显示软件名称与版本号（当前 `v1.1.0 · 2026-09-17`）。

更多字段说明和 GUI 细节见 [docs/README.md](docs/README.md)。
