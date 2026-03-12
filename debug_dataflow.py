"""
数据流调试脚本

用于检查优化后的线程系统中的数据流问题。

Author: Claude
Date: 2026-03-12
"""

import logging
import time

# 设置详细的日志
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('debug_dataflow.log')
    ]
)

# 获取所有相关的日志器
loggers = [
    'src.main',
    'processing.tab1_optimized_threads.DataProcessingThread',
    'processing.tab1_optimized_threads.TimedomainPlotThread',
    'processing.tab1_optimized_threads.PSDPlotThread',
    'processing.tab1_optimized_threads.OptimizedTab1ThreadManager',
    'comm.tcp_server_optimized'
]

for logger_name in loggers:
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.DEBUG)

print("已设置详细日志记录")
print("请启动主程序，观察日志输出以找到数据流问题")
print("日志文件：debug_dataflow.log")

# 保持脚本运行
try:
    while True:
        time.sleep(1)
except KeyboardInterrupt:
    print("调试脚本结束")