# PCCP Wire Break Monitoring Software

## 项目概述

PCCP (Prestressed Concrete Cylinder Pipe) 断丝监测软件，基于光纤干涉仪和分布式光纤传感(DAS)技术的实时监测系统。

### 功能特点

- **光纤干涉仪数据处理** - 实时相位展开、信号滤波、特征提取
- **TCP通信模块** - 高效数据接收，零丢包率
- **实时可视化** - PyQtGraph波形显示和频谱分析
- **信号检测** - 基于阈值的异常检测和告警
- **数据存储** - NPZ格式数据记录和管理

## 系统要求

- **Python**: 3.9.x
- **操作系统**: Windows 10/11 (64位)
- **内存**: >= 4GB RAM
- **网络**: TCP/IP 支持

## 安装依赖

```bash
pip install -r requirements.txt
```

## 运行程序

### 正常模式
```bash
python run.py
```

### 调试模式
```bash
python run.py --debug
```

### 指定日志文件
```bash
python run.py --log monitor.log
```

### 自定义配置
```bash
python run.py --config my_config.json
```

## 项目架构

```
wb-monitor/
├── docs/                    # 项目文档
├── libs/                    # 第三方库
├── logs/                    # 运行日志
├── output/                  # 输出数据
├── resources/               # 资源文件
├── config/                  # 配置文件
├── src/                     # 源代码
│   ├── comm/               # 通信模块
│   ├── processing/         # 数据处理
│   ├── visualization/      # 可视化
│   ├── ui/                 # 用户界面
│   ├── detection/          # 信号检测
│   ├── storage/            # 数据存储
│   └── main.py            # 主程序
├── requirements.txt         # 依赖列表
└── run.py                  # 启动脚本
```

## 开发阶段

### 第一阶段 (已完成)
- [x] TCP通信模块优化
- [x] 数据处理管道
- [x] 基础GUI界面
- [x] 实时波形显示

### 第二阶段 (进行中)
- [ ] 特征计算模块
- [ ] 信号检测算法
- [ ] 数据存储系统
- [ ] 配置管理

### 第三阶段 (计划)
- [ ] DAS数据处理
- [ ] 信号定位算法
- [ ] 高级分析功能

## 技术规格

### 数据格式
- **协议**: TCP/IP
- **数据包**: 8字节头部 + 1.6MB数据体
- **数据类型**: <32,32>定点数 (大端序)
- **采样率**: 1MHz
- **传输频率**: 5包/秒

### 性能指标
- **丢包率**: 0%
- **接收延迟**: <50ms
- **处理延迟**: <100ms
- **内存占用**: <2GB

## 配置说明

配置文件位置: `config/app_config.json`

主要配置项:
- `communication`: TCP连接参数
- `preprocessing`: 信号预处理
- `features`: 特征计算设置
- `detection`: 检测算法参数
- `storage`: 数据存储配置

## 故障排除

### 常见问题

1. **连接失败**
   - 检查IP地址和端口设置
   - 确认防火墙允许端口3677
   - 验证网络连接

2. **丢包率高**
   - 检查网络质量
   - 调整TCP缓冲区大小
   - 确认客户端发送频率

3. **性能问题**
   - 启用调试日志定位瓶颈
   - 检查系统资源使用
   - 优化处理参数

## 开发指南

### 代码规范
- 遵循PEP 8编码规范
- 使用类型提示
- 添加详细的文档字符串
- 英文注释和变量名

### 测试
```bash
# 运行调试模式
python run.py --debug

# 查看日志
tail -f logs/pccp_monitor.log
```

## 版权信息

- **版本**: 1.0.0
- **作者**: Claude
- **日期**: 2026-03-11
- **许可**: MIT License

## 更新日志

### v1.0.0 (2026-03-11)
- 完成TCP通信模块优化
- 实现零丢包率数据接收
- 基础GUI界面和实时可视化
- 数据处理管道建立
- 项目架构重构