# PCCP 断丝监测软件

## 项目概述
基于 Python 3.9 + PyQt5 开发的PCCP断丝监测软件，支持光纤干涉仪和DAS模块数据处理。

## 第一阶段开发
当前实现光纤干涉仪模块（Tab1、Tab2）的核心功能。

## 环境要求
- Python 3.9.x
- PyQt5 5.15.7
- PyQtGraph 0.13.3
- NumPy 1.23.5
- SciPy 1.9.3
- Pandas 1.5.3

## 安装依赖
```bash
pip install pyqt5==5.15.7 pyqtgraph==0.13.3 numpy==1.23.5 scipy==1.9.3 pandas==1.5.3 psutil==5.9.4
```

## 运行程序
```bash
python main.py
```

## 项目结构
```
wb-monitor/
├── config/          # 配置文件目录
├── data/            # 数据存储目录
├── logs/            # 日志目录
├── doc/             # 文档目录
├── src/             # 源代码目录
│   ├── comm/        # 通信模块
│   ├── processing/  # 数据处理模块
│   ├── detection/   # 检测模块
│   ├── visualization/ # 可视化模块
│   ├── storage/     # 存储模块
│   └── ui/          # 界面模块
├── main.py          # 程序入口
├── requirements.txt # 依赖列表
└── README.md        # 项目说明
```