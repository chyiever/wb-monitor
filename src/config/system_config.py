"""
系统配置常量文件

定义系统级采样率和降采样参数，供所有模块共享使用。
注意：系统降采样和前面板降采样是同一个东西，默认10倍降采样。

Author: Claude
Date: 2026-03-12
"""

# 采样率配置
ORIGINAL_SAMPLE_RATE = 1000000.0  # 原始采样率 1MHz
SYSTEM_DOWNSAMPLE_FACTOR = 5      # 前面板默认降采样因子（相位展开后应用）
EFFECTIVE_SAMPLE_RATE = ORIGINAL_SAMPLE_RATE / SYSTEM_DOWNSAMPLE_FACTOR  # 默认有效采样率 200kHz

# 时域显示专用降采样
TIME_DISPLAY_DOWNSAMPLE = 2      # 时域显示额外降采样因子（200kHz → 100kHz）
TIME_DISPLAY_SAMPLE_RATE = EFFECTIVE_SAMPLE_RATE / TIME_DISPLAY_DOWNSAMPLE  # 时域显示采样率 100kHz

# 数据包配置
PACKET_DURATION = 0.2  # 每个数据包的时长（秒）
PACKETS_PER_SECOND = int(1.0 / PACKET_DURATION)  # 每秒数据包数量

# 性能相关配置
MAX_TIME_DISPLAY_POINTS = 100000    # 时域最大显示点数（适配100kHz显示）
MAX_PSD_SAMPLES = 80000             # PSD计算最大样本数（适配200kHz）
TIME_BUFFER_MAX_POINTS = 200000     # 时域缓冲区最大点数（1.0秒@200kHz）

# 日志和调试
PERFORMANCE_LOG_INTERVAL = 50      # 每N个数据包输出一次性能日志
FEATURE_PROCESSING_INTERVAL = 3    # 每N个数据包处理一次特征

def get_sample_rate_info():
    """
    获取采样率信息摘要

    Returns:
        dict: 包含采样率信息的字典
    """
    return {
        'original_sample_rate': ORIGINAL_SAMPLE_RATE,
        'system_downsample_factor': SYSTEM_DOWNSAMPLE_FACTOR,
        'effective_sample_rate': EFFECTIVE_SAMPLE_RATE,
        'original_rate_mhz': ORIGINAL_SAMPLE_RATE / 1e6,
        'effective_rate_khz': EFFECTIVE_SAMPLE_RATE / 1e3,
        'time_display_rate_khz': TIME_DISPLAY_SAMPLE_RATE / 1e3,
        'packet_duration': PACKET_DURATION,
        'packets_per_second': PACKETS_PER_SECOND,
        'time_display_downsample': TIME_DISPLAY_DOWNSAMPLE
    }

def log_sample_rate_info():
    """打印采样率配置信息"""
    info = get_sample_rate_info()
    print(f"系统采样率配置:")
    print(f"  原始采样率: {info['original_rate_mhz']:.1f}MHz")
    print(f"  主降采样: {SYSTEM_DOWNSAMPLE_FACTOR}x -> {info['effective_rate_khz']:.0f}kHz")
    print(f"  时域显示降采样: {TIME_DISPLAY_DOWNSAMPLE}x -> {info['time_display_rate_khz']:.0f}kHz")
    print(f"  数据包间隔: {info['packet_duration']}s ({info['packets_per_second']}包/秒)")
    print(f"  PSD/存储采样率: {info['effective_rate_khz']:.0f}kHz")
    print(f"  时域显示采样率: {info['time_display_rate_khz']:.0f}kHz")

if __name__ == '__main__':
    log_sample_rate_info()