"""
`src/config.py` 负责定义整个 PCIe-7821 eDAS 上位机的参数模型、枚举、校验规则和派生计算函数。

当前版本中，`AllParams` 及其下属 dataclass 已经成为 GUI、采集线程、TCP 发送、Time-Space 控件和保存链路之间共享的事实标准接口。这个模块既表达硬件参数，也表达上位机策略参数，因此是后续扩展配置持久化、脚本化测试和离线回放时必须首先保持稳定的边界。

需要特别注意的是，`frame_load_num` 属于软件侧批量读取策略，`frame_plot_num` 属于纯显示参数。把这两类参数分开表达，是当前版本避免现场调参歧义的重要经验。
"""
from dataclasses import dataclass, field
from typing import Dict, List, Tuple
from enum import IntEnum


# ----- ENUMERATION DEFINITIONS -----
# Hardware clock source options for timing synchronization

class ClockSource(IntEnum):
    """
    Clock source enumeration for PCIe-7821 timing control.

    INTERNAL: Use onboard crystal oscillator (default)
    EXTERNAL: Use external reference clock input

    Note: External clock requires proper signal conditioning
    """
    INTERNAL = 0
    EXTERNAL = 1


class TriggerDirection(IntEnum):
    """
    Trigger signal direction control.

    INPUT: Accept external trigger signals
    OUTPUT: Generate trigger output for synchronization

    Usage: Typically OUTPUT for master mode, INPUT for slave mode
    """
    INPUT = 0
    OUTPUT = 1


class DataSource(IntEnum):
    """
    Data processing pipeline selection.

    Defines which stage of signal processing to upload to host:
    - raw: Raw backscattered optical data (ADC output)
    - I_Q: In-phase/Quadrature demodulated signals
    - arc: Arctangent phase calculation arctan(Q/I)
    - PHASE: Final phase-demodulated DAS data (recommended)

    Note: PHASE provides best SNR and processing efficiency
    """
    raw = 0     # Raw scattered light data from ADC
    I_Q = 2     # I/Q demodulated signals
    arc = 3     # Arctan phase calculation
    PHASE = 4   # Phase-demodulated data (default)


class DisplayMode(IntEnum):
    """
    Waveform display mode selection.

    TIME: Show multiple frames overlaid (temporal analysis)
    SPACE: Show single spatial position over time (position analysis)
    TIME_SPACE: Show 2D time-space plot with rolling window (advanced analysis)

    Usage: TIME for overall signal inspection, SPACE for specific location monitoring,
           TIME_SPACE for spatiotemporal pattern analysis
    """
    TIME = 0       # Time domain display (multiple frames overlay)
    SPACE = 1      # Space domain display (single region over time)
    TIME_SPACE = 2 # Time-space 2D plot with rolling window


STORAGE_FORMAT_BIN = "bin"
STORAGE_FORMAT_BITSHUFFLE_ZSTD = "bz"

STORAGE_FORMAT_OPTIONS: List[Tuple[str, str]] = [
    ("BIN (.bin)", STORAGE_FORMAT_BIN),
    ("Bitshuffle+Zstd (.bz)", STORAGE_FORMAT_BITSHUFFLE_ZSTD),
]


# ----- PARAMETER DATA STRUCTURES -----
# Organized parameter groups using dataclasses for type safety and defaults

@dataclass
class BasicParams:
    """
    Core acquisition hardware parameters.

    These parameters directly control the PCIe-7821 FPGA acquisition engine.
    Changes require hardware reconfiguration and may affect data continuity.

    Attributes:
        clk_src: Clock source selection (internal/external)
        trig_dir: Trigger direction (input/output)
        scan_rate: Laser scan repetition rate in Hz (1-100000)
        pulse_width_ns: Laser pulse width in nanoseconds (10-1000)
        point_num_per_scan: Spatial sampling points per scan (512-262144)
        bypass_point_num: Initial points to skip (dead zone compensation)
        center_freq_mhz: RF center frequency in MHz (50-500)

    Validation: Use validate_point_num() before applying changes
    """
    clk_src: int = ClockSource.INTERNAL
    trig_dir: int = TriggerDirection.OUTPUT
    scan_rate: int = 2000                    # Hz - typical range 1000-5000
    pulse_width_ns: int = 100                # ns - affects spatial resolution
    point_num_per_scan: int = 20480          # Must align with channel constraints
    bypass_point_num: int = 60               # Skip initial fiber coupling region
    center_freq_mhz: int = 200               # MHz - RF demodulation frequency


@dataclass
class UploadParams:
    """
    Data upload configuration parameters.

    Controls how processed data is transferred from FPGA to host PC.
    Channel configuration affects memory allocation and processing requirements.

    Attributes:
        channel_num: Number of active channels (1, 2, or 4)
        data_source: Processing stage to upload (see DataSource enum)
        data_rate: Sampling interval in ns (affects bandwidth and fiber length)

    Note: 4-channel mode only supports PHASE data source due to bandwidth limits
    """
    channel_num: int = 1                     # 1, 2, or 4 channels
    data_source: int = DataSource.PHASE      # Recommended for best performance
    data_rate: int = 1                       # ns per sample (1=1GHz, 2=500MHz, etc)


@dataclass
class PhaseDemodParams:
    """
    Advanced phase demodulation algorithm parameters.

    These parameters control the FPGA-based phase processing pipeline.
    Optimization may be needed for specific fiber types or applications.

    Attributes:
        rate2phase: Decimation factor (1,2,3,4,5,10) - affects final data rate
        space_avg_order: Spatial averaging kernel size (reduces noise)
        merge_point_num: Spatial point merging factor (reduces data volume)
        crop_distance_start: Spatial crop start index for single-channel PHASE
        crop_distance_end: Spatial crop end index (exclusive); 0 disables crop
        diff_order: Differential order (0=absolute, 1=first derivative, etc)
        detrend_bw: High-pass filter bandwidth in Hz (removes DC drift)
        polarization_diversity: Enable polarization diversity processing

    Note: Higher space_avg_order improves SNR but reduces spatial resolution
    """
    rate2phase: int = 1                      # 250MHz base / rate2phase = final rate
    space_avg_order: int = 25                # Spatial averaging points
    merge_point_num: int = 25                # Spatial merging factor
    crop_distance_start: int = 0             # Single-channel PHASE crop start (inclusive)
    crop_distance_end: int = 0               # Single-channel PHASE crop end (exclusive), 0=disabled
    diff_order: int = 1                      # Differential processing order
    detrend_bw: float = 10                  # Hz - high-pass cutoff frequency
    polarization_diversity: bool = True     # Advanced polarization processing


@dataclass
class TimeSpaceParams:
    """
    Time-Space plot configuration parameters.

    Controls the 2D time-space plot visualization including rolling window
    behavior, spatial range selection, downsampling, and colormap settings.

    Attributes:
        window_frames: Number of frames to keep in rolling window (temporal dimension)
        distance_range_start: Starting distance index for display
        distance_range_end: Ending distance index for display
        time_downsample: Time dimension downsampling factor (1=no downsampling)
        space_downsample: Space dimension downsampling factor (1=no downsampling)
        colormap_type: Colormap type for 2D visualization
        vmin: Minimum value for color mapping
        vmax: Maximum value for color mapping
        filter_enabled: Enable display-only realtime temporal filtering
        filter_spec: Cutoff text, e.g. "1-", "-10", or "2-10"

    Performance: Larger windows and lower downsampling provide better visualization
                but require more memory and processing power.
    """
    window_frames: int = 5                   # Rolling window size in frames
    distance_range_start: int = 40          # Start index for distance range (updated default)
    distance_range_end: int = 100           # End index for distance range (updated default)
    time_downsample: int = 50               # Time downsampling factor
    space_downsample: int = 2               # Space downsampling factor
    colormap_type: str = "jet"              # PyQtGraph colormap name
    vmin: float = -0.02                     # Color range minimum (updated for phase data)
    vmax: float = 0.02                      # Color range maximum (updated for phase data)
    filter_enabled: bool = False            # Display-only filter switch for Tab2
    filter_spec: str = "1-"                 # 1- high-pass, -10 low-pass, 2-10 band-pass
    axis_unit: str = "points"               # points, m, km (display only)
    axis_origin: float = 0.0                # x0 in the selected display unit
    display_enabled: bool = True           # Integrated Tab2 time-space visibility


@dataclass
class DisplayParams:
    """
    Real-time display configuration parameters.

    Controls GUI visualization without affecting data acquisition.
    These parameters can be changed during operation without disruption.

    Attributes:
        mode: Display mode (TIME/SPACE - see DisplayMode enum)
        region_index: Spatial position for SPACE mode display
        frame_load_num: Number of frames requested from the DLL buffer per software read block
        frame_plot_num: Number of frames in each waveform/PSD/time-space display window and refresh period
        spectrum_enable: Enable FFT spectrum analysis display
        rad_enable: Convert phase data to radians for display (storage unaffected)
        waveform_plot_enabled: Enable plot 1 waveform updates
        monitor_plot_enabled: Enable plot 3 monitor updates in PHASE mode

    Note: Analysis type (Power Spectrum vs PSD) is now automatically determined:
          - Raw data: Power Spectrum
          - Phase data: PSD using scipy.welch

    The UI exposes Length/Load and Length/Plot in seconds. Runtime frame counts
    are derived from those lengths and scan_rate before acquisition starts.
    Length/Plot also drives the GUI latest-snapshot refresh interval.
    """
    mode: int = DisplayMode.SPACE
    tick_font_size: int = 12
    wave_source: str = "phase"              # phase, raw, monitor
    region_index: int = 0                    # Spatial position index for SPACE mode
    length_load_s: float = 0.2               # Seconds per DLL read block
    length_plot_s: float = 1.0               # Seconds per display window/refresh
    frame_load_num: int = 400                # Derived frames per DLL read block at default 2000 Hz
    frame_plot_num: int = 2000               # Derived frames per display refresh at default 2000 Hz
    spectrum_enable: bool = True             # Enable frequency domain analysis
    rad_enable: bool = True                  # Display-only radian conversion (default enabled)
    waveform_plot_enabled: bool = False      # Plot 1 switch
    monitor_plot_enabled: bool = False       # Plot 3 switch


@dataclass
class SaveParams:
    """
    Data storage configuration parameters.

    Controls automatic data logging to disk. File splitting prevents
    excessively large files and improves data management.

    Attributes:
        enable: Enable/disable automatic data saving
        path: Directory path for data files (must exist and be writable)
        file_prefix: Optional prefix for generated filenames
        length_save_s: Seconds per storage packet/write unit for both .bin and .bz
        length_file_s: Seconds of data per output file for both .bin and .bz
        storage_downsample_factor: Storage-only point picking factor (1=no downsampling)
        storage_format: File format, either raw .bin or compressed .bz
        overwrite_oldest_when_full: Delete the oldest closed data files when disk space is low
        bz_zstd_level: Zstd level for .bz packets
        bz_bitshuffle_block_values: Bitshuffle block size in int32 values
        bz_compression_workers: Number of parallel .bz compression workers

    Filename Format: {seq}-eDAS-{rate}Hz-{points}pt-{timestamp}.{ms}.bin
    Storage Format: Raw int32 .bin or packetized Bitshuffle+Zstd .bz

    Note: Ensure sufficient disk space - typical rate ~50-200 MB/min
    """
    enable: bool = False
    path: str = "D:/eDAS_DATA"               # Default storage directory
    file_prefix: str = ""                    # Optional filename prefix
    length_save_s: float = 1.0              # Seconds per stored packet/write unit
    length_file_s: float = 10.0              # Seconds of data per output file
    storage_downsample_factor: int = 1       # Save every Nth point; display/filter paths are unaffected
    storage_format: str = STORAGE_FORMAT_BIN # "bin" keeps the original raw binary saver
    overwrite_oldest_when_full: bool = False # Keep newest data by deleting oldest closed .bin/.bz files
    bz_zstd_level: int = 3                   # Default Zstd compression level for .bz
    bz_bitshuffle_block_values: int = 65536  # Bitshuffle block size in int32 values
    bz_compression_workers: int = 4          # Parallel .bz compression workers


@dataclass
class CommParams:
    """
    TCP communication packetization parameters.

    Length/Comm is configured in seconds in Tab3 and converted to a frame count
    before each acquisition session. It must be an integer multiple of
    Length/Load so outgoing packets are composed of complete acquisition blocks.

    The remaining fields mirror the Tab3 TCP settings so they are persisted
    alongside the rest of the local parameters (last_params.json).
    """
    length_comm_s: float = 1.0               # Seconds per TCP payload packet
    comm_frame_num: int = 2000               # Derived frames per TCP packet at default 2000 Hz
    enabled: bool = False                    # Master communication enable switch
    server_ip: str = "169.255.1.2"           # wb-monitor server address
    server_port: int = 3678                  # wb-monitor DAS TCP port
    channel_start: int = 50                  # First spatial channel to send (0-based index)
    channel_end: int = 100                   # Last spatial channel to send (0-based index)
    time_downsample: int = 1                 # Time downsampling factor
    space_downsample: int = 1                # Space downsampling factor


@dataclass
class AllParams:
    """
    Master parameter container.

    Aggregates all configuration parameters into a single structure
    for easy passing between modules and serialization.

    Usage:
        params = AllParams()
        params.basic.scan_rate = 5000
        params.save.enable = True

    Validation: Always validate parameters before hardware configuration
    """
    basic: BasicParams = field(default_factory=BasicParams)
    upload: UploadParams = field(default_factory=UploadParams)
    phase_demod: PhaseDemodParams = field(default_factory=PhaseDemodParams)
    display: DisplayParams = field(default_factory=DisplayParams)
    save: SaveParams = field(default_factory=SaveParams)
    time_space: TimeSpaceParams = field(default_factory=TimeSpaceParams)
    comm: CommParams = field(default_factory=CommParams)


# ----- GUI OPTION MAPPINGS -----
# Human-readable labels mapped to internal values for combo box controls

CHANNEL_NUM_OPTIONS: List[Tuple[str, int]] = [
    ("1", 1),    # Single channel mode
    ("2", 2),    # Dual channel mode
    ("4", 4),    # Quad channel mode (PHASE data only)
]

DATA_SOURCE_OPTIONS: List[Tuple[str, int]] = [
    ("RawBack", DataSource.raw),             # Raw ADC data
    ("I/Q", DataSource.I_Q),                 # I/Q demodulated signals
    ("Arctan", DataSource.arc),              # Arctan phase calculation
    ("Phase", DataSource.PHASE),             # Phase demodulated (recommended)
]

DATA_RATE_OPTIONS: List[Tuple[str, int]] = [
    ("1ns (1GHz)", 1),       # Highest resolution, maximum data rate
    ("2ns (500MHz)", 2),     # 2x decimation
    ("4ns (250MHz)", 4),     # 4x decimation
    ("8ns (125MHz)", 8),     # 8x decimation, lowest data rate
]

# Rate2Phase decimation options with calculated output rates
# Base rate after I/Q demodulation: 250MHz
# Final rate = 250MHz / rate2phase_factor
RATE2PHASE_OPTIONS: List[Tuple[str, int]] = [
    ("250M", 1),     # 250MHz / 1 = 250MHz (maximum rate)
    ("125M", 2),     # 250MHz / 2 = 125MHz
    ("83.33M", 3),   # 250MHz / 3 = 83.33MHz
    ("62.5M", 4),    # 250MHz / 4 = 62.5MHz
    ("50M", 5),      # 250MHz / 5 = 50MHz
    ("25M", 10),     # 250MHz / 10 = 25MHz (minimum rate)
]


# ----- HARDWARE CONSTRAINTS -----
# Maximum sampling points per channel (memory and bandwidth limitations)

MAX_POINT_NUM_1CH = 262144    # Single channel: 256K points max
MAX_POINT_NUM_2CH = 131072    # Dual channel: 128K points max (shared bandwidth)
MAX_POINT_NUM_4CH = 65536     # Quad channel: 64K points max (shared bandwidth)

# Memory alignment requirements for efficient DMA transfer
POINT_NUM_ALIGN_1CH = 512     # Single channel: 512-point alignment
POINT_NUM_ALIGN_2CH = 256     # Dual channel: 256-point alignment
POINT_NUM_ALIGN_4CH = 128     # Quad channel: 128-point alignment

# DMA memory alignment requirement (PCIe hardware constraint)
DMA_ALIGNMENT = 4096          # 4KB page alignment for optimal performance


# ----- ERROR CODE DEFINITIONS -----
# Standard error codes returned by PCIe-7821 API functions

ERROR_CODES: Dict[int, str] = {
    0: "Success",                    # Operation completed successfully
    -1: "Device open failed",        # Cannot access PCIe hardware
    -2: "Invalid parameter",         # Parameter validation failed
    -3: "Buffer overflow",           # Data buffer full, frames dropped
    -4: "Device not started",        # Acquisition not initiated
    -5: "DMA error",                 # Hardware DMA transfer error
}


def get_error_message(code: int) -> str:
    """
    Retrieve human-readable error message for API error codes.

    Args:
        code: Integer error code returned by PCIe-7821 API

    Returns:
        String description of the error condition

    Usage:
        result = api.start_acquisition()
        if result != 0:
            print(f"Error: {get_error_message(result)}")
    """
    return ERROR_CODES.get(code, f"Unknown error ({code})")


# ----- VALIDATION FUNCTIONS -----

def validate_point_num(point_num: int, channel_num: int) -> Tuple[bool, str]:
    """
    Validate point_num_per_scan against channel-specific constraints.

    PCIe-7821 has different memory and bandwidth limitations depending on
    the number of active channels. This function ensures parameters are
    within hardware limits and properly aligned for DMA efficiency.

    Args:
        point_num: Number of sampling points per scan
        channel_num: Number of active channels (1, 2, or 4)

    Returns:
        Tuple of (is_valid, error_message)
        is_valid: True if parameters are acceptable
        error_message: Description of constraint violation (empty if valid)

    Hardware Constraints:
        - Memory limitations reduce max points with more channels
        - DMA alignment requirements vary by channel count
        - Bandwidth sharing affects maximum achievable rates

    Usage:
        valid, msg = validate_point_num(20480, 2)
        if not valid:
            raise ValueError(f"Invalid configuration: {msg}")
    """
    # Single channel validation - highest capacity, 512-point alignment
    if channel_num == 1:
        if point_num > MAX_POINT_NUM_1CH:
            return False, f"Single channel mode: point_num must be <= {MAX_POINT_NUM_1CH}"
        if point_num % POINT_NUM_ALIGN_1CH != 0:
            return False, f"Single channel mode: point_num must be multiple of {POINT_NUM_ALIGN_1CH}"

    # Dual channel validation - shared bandwidth, 256-point alignment
    elif channel_num == 2:
        if point_num > MAX_POINT_NUM_2CH:
            return False, f"Dual channel mode: point_num must be <= {MAX_POINT_NUM_2CH}"
        if point_num % POINT_NUM_ALIGN_2CH != 0:
            return False, f"Dual channel mode: point_num must be multiple of {POINT_NUM_ALIGN_2CH}"

    # Quad channel validation - maximum sharing, 128-point alignment
    elif channel_num == 4:
        if point_num > MAX_POINT_NUM_4CH:
            return False, f"Quad channel mode: point_num must be <= {MAX_POINT_NUM_4CH}"
        if point_num % POINT_NUM_ALIGN_4CH != 0:
            return False, f"Quad channel mode: point_num must be multiple of {POINT_NUM_ALIGN_4CH}"

    return True, ""


def calculate_phase_point_num(point_num: int, merge_point_num: int) -> int:
    """Return the number of PHASE points per frame after FPGA merge."""
    return max(0, int(point_num) // max(1, int(merge_point_num)))


def resolve_phase_crop_bounds(total_points: int, crop_start: int, crop_end: int) -> Tuple[int, int]:
    """
    Resolve the effective PHASE spatial crop range.

    Rules:
    - crop_start == 0 and crop_end == 0 means no crop, keep the full range
    - start is inclusive
    - end is exclusive
    - end <= 0 means use total_points
    - end larger than total_points is clamped to total_points
    """
    total = max(0, int(total_points))
    start = max(0, int(crop_start))
    end = int(crop_end)

    if start == 0 and end == 0:
        return 0, total

    start = min(start, total)
    end = total if end <= 0 else min(end, total)
    if end < start:
        end = start

    return start, end


def calculate_cropped_point_count(total_points: int, crop_start: int, crop_end: int) -> int:
    """Return the point count after applying the PHASE spatial crop."""
    start, end = resolve_phase_crop_bounds(total_points, crop_start, crop_end)
    return max(0, end - start)


def calculate_fiber_length(point_num: int, data_rate: int, data_source: int, rate2phase: int) -> float:
    """
    Calculate equivalent fiber length based on acquisition parameters.

    Converts sampling parameters to physical fiber length using calibrated
    scaling factors. Different data sources have different spatial resolution
    characteristics due to processing differences.

    Args:
        point_num: Number of sampling points per scan
        data_rate: Sampling interval in nanoseconds
        data_source: Data processing stage (see DataSource enum)
        rate2phase: Phase decimation factor (for PHASE data source)

    Returns:
        Calculated fiber length in meters

    Scaling Factors:
        - Phase data: 0.4m * rate2phase per point (optimized processing)
        - Raw/I/Q data: 0.1m * data_rate per point (direct sampling)

    Physical Meaning:
        - Higher data_rate = longer sampling interval = coarser spatial resolution
        - rate2phase decimation trades spatial resolution for reduced data rate
        - Total length = points × spatial_resolution_per_point

    Usage:
        length = calculate_fiber_length(20480, 1, DataSource.PHASE, 4)
        print(f"Monitoring {length:.1f}m of fiber")
    """
    if data_source == DataSource.PHASE:
        # Phase data: optimized spatial resolution with decimation factor
        len_rbw = 0.4 * rate2phase  # meters per point
    else:
        # Raw/I/Q data: direct sampling resolution
        len_rbw = 0.1 * data_rate   # meters per point

    return point_num * len_rbw / 1000.0  # Convert to meters


def calculate_data_rate_mbps(scan_rate: int, point_num: int, channel_num: int) -> float:
    """
    Calculate sustained data rate in MB/s for bandwidth planning.

    Computes the continuous data throughput from PCIe-7821 to host based on
    acquisition parameters. Used for storage capacity planning and performance
    monitoring.

    Args:
        scan_rate: Laser scan repetition rate in Hz
        point_num: Sampling points per scan per channel
        channel_num: Number of active channels

    Returns:
        Data rate in megabytes per second (MB/s)

    Calculation:
        - Each data point is 16-bit (2 bytes) for raw data, 32-bit (4 bytes) for phase
        - Total rate = scans/sec × points/scan × bytes/point × channels
        - Result converted from bytes/s to MB/s

    Usage:
        rate = calculate_data_rate_mbps(2000, 20480, 2)
        if rate > 100:
            print(f"Warning: High data rate {rate:.1f} MB/s")

    Note: Actual rate may vary with data_source selection and compression
    """
    # Assuming 2 bytes per point (int16) - adjust for different data types if needed
    return scan_rate * point_num * 2 * channel_num / 1024.0 / 1024.0


# ----- PERFORMANCE OPTIMIZATION CONSTANTS -----
# Buffer sizes and timing parameters tuned for optimal system performance

OPTIMIZED_BUFFER_SIZES = {
    # Hardware buffer: FPGA FIFO + DMA ring buffer
    'hardware_buffer_frames': 50,           # Absorb burst traffic and timing jitter

    # Qt signal queue: Inter-thread communication buffer
    'signal_queue_frames': 20,              # Balance latency vs memory usage

    # Storage queue: Async file writing buffer (critical for continuous operation)
    'storage_queue_frames': 200,            # Upper bound; runtime also caps queues by estimated bytes

    # Display buffer: GUI visualization history
    'display_buffer_frames': 30             # Sufficient for smooth plotting updates
}

# Dynamic polling configuration for adaptive CPU usage
POLLING_CONFIG = {
    # High-frequency polling: Maximum responsiveness during heavy data flow
    'high_freq_interval_ms': 1,             # 1ms polling = ~1000 checks/sec

    # Low-frequency polling: CPU conservation during idle periods
    'low_freq_interval_ms': 10,             # 10ms polling = ~100 checks/sec

    # Adaptive switching thresholds based on buffer occupancy
    'buffer_threshold_high': 0.8,           # Switch to high freq when buffer > 80% full
    'buffer_threshold_low': 0.3             # Switch to low freq when buffer < 30% full
}

# System monitoring update intervals for GUI status displays
MONITOR_UPDATE_INTERVALS = {
    # Buffer status: Real-time monitoring for performance feedback
    'buffer_status_ms': 500,                # 2 Hz update rate balances accuracy vs overhead

    # System resources: Slower updates for CPU/disk/memory status
    'system_status_s': 10,                  # 0.1 Hz update sufficient for resource monitoring

    # Performance logging: Periodic detailed statistics capture
    'performance_log_s': 30                 # 30-second intervals for trend analysis
}
