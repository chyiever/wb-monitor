# wb-monitor 双版本

- `version_exDAS/`：外置 eDAS 上位机版本，阶段版本 `v_exDAS_261021`，对应 master1/dev1。
- `version_inDAS/`：集成 PCIe-7821 eDAS 采集的版本，对应 master2/dev2。
- `fipedasREAD/`：独立数据回放工具；dist 不纳入版本管理。

请进入对应版本目录运行 `python run.py`，或在根目录执行 `python version_exDAS/run.py` / `python version_inDAS/run.py`。
