"""
`src/pcie7821_api.py` 是 Python 到 `pcie7821_api.dll` 的硬件边界封装层。

这个模块把底层 DLL 的调用细节、DMA 对齐内存、ctypes 原型声明、错误码翻译和线程互斥保护收口到一处。当前实现通过 `AlignedBuffer` 保证缓冲区满足 DMA 对齐要求，通过单把 `threading.Lock` 保护 DLL 调用，避免 Python 层对底层设备状态造成不可控竞争。

对后续类似项目而言，这种“把硬件脏活隔离在单文件中”的方式很有价值。只要 API 封装边界稳定，上层线程、GUI、保存与网络模块就可以围绕 NumPy 数组和 Python 异常工作。
"""
import ctypes
import numpy as np
from pathlib import Path
from typing import Optional, Tuple
import os
import sys
import time
import threading

from .config import DMA_ALIGNMENT, get_error_message
from .logger import get_logger, PerformanceTimer

# Module logger
log = get_logger("api")


# ----- DMA-ALIGNED MEMORY BUFFER -----
# DMA transfers require 4KB (4096-byte) aligned memory addresses.
# Standard numpy allocation does not guarantee alignment, so we
# over-allocate and manually offset to the next aligned boundary.

class AlignedBuffer:
    """Memory buffer with specified alignment for DMA transfers"""

    def __init__(self, size: int, dtype: np.dtype, alignment: int = DMA_ALIGNMENT):
        """
        Create an aligned memory buffer.

        Args:
            size: Number of elements
            dtype: NumPy dtype
            alignment: Byte alignment (default 4096 for DMA)
        """
        self.size = size
        self.dtype = np.dtype(dtype)
        self.alignment = alignment
        self.itemsize = self.dtype.itemsize

        # Over-allocate by 'alignment' bytes to guarantee we can find
        # an aligned start address within the raw buffer
        total_bytes = size * self.itemsize + alignment
        self._raw_buffer = (ctypes.c_char * total_bytes)()

        # Find first aligned offset: (alignment - addr % alignment) % alignment
        raw_addr = ctypes.addressof(self._raw_buffer)
        offset = (alignment - (raw_addr % alignment)) % alignment

        # Create numpy array view at aligned address
        self.array = np.frombuffer(
            self._raw_buffer,
            dtype=self.dtype,
            count=size,
            offset=offset
        )

        # Store pointer for ctypes
        self._aligned_addr = raw_addr + offset

        log.debug(f"AlignedBuffer created: size={size}, dtype={dtype}, "
                  f"aligned_addr=0x{self._aligned_addr:X}, alignment_ok={self._aligned_addr % alignment == 0}")

    def get_ctypes_ptr(self):
        """Get ctypes pointer to aligned buffer"""
        if self.dtype == np.int16:
            return ctypes.cast(self._aligned_addr, ctypes.POINTER(ctypes.c_short))
        elif self.dtype == np.int32:
            return ctypes.cast(self._aligned_addr, ctypes.POINTER(ctypes.c_int))
        elif self.dtype == np.uint32:
            return ctypes.cast(self._aligned_addr, ctypes.POINTER(ctypes.c_uint))
        else:
            raise ValueError(f"Unsupported dtype: {self.dtype}")

    def __del__(self):
        """Ensure buffer is properly released"""
        self._raw_buffer = None
        self.array = None


# ----- API ERROR HANDLING -----

class PCIe7821Error(Exception):
    """Exception for PCIe-7821 API errors"""
    def __init__(self, code: int, message: str = ""):
        self.code = code
        self.message = message or get_error_message(code)
        super().__init__(f"PCIe-7821 Error {code}: {self.message}")


# ----- DLL WRAPPER CLASS -----
# Thread-safe Python wrapper around pcie7821_api.dll functions.
# All public methods acquire self._lock before calling into the DLL.

class PCIe7821API:
    """Python wrapper for pcie7821_api.dll"""

    def __init__(self, dll_path: Optional[str] = None):
        """
        Initialize the DLL wrapper.

        Args:
            dll_path: Path to pcie7821_api.dll. If None, searches in default locations.
        """
        self.dll = None
        self._is_open = False
        self._lock = threading.Lock()  # Thread safety for DLL calls

        log.info("Initializing PCIe7821API...")

        # Find DLL
        if dll_path is None:
            dll_path = self._find_dll()

        if not os.path.exists(dll_path):
            log.error(f"DLL not found: {dll_path}")
            raise FileNotFoundError(f"DLL not found: {dll_path}")

        log.info(f"Loading DLL from: {dll_path}")

        # Load DLL
        try:
            self.dll = ctypes.CDLL(dll_path)
            log.info("DLL loaded successfully")
        except OSError as e:
            log.error(f"Failed to load DLL: {e}")
            raise RuntimeError(f"Failed to load DLL: {e}")

        # Setup function prototypes
        self._setup_prototypes()

        # Buffers for data reading
        self._raw_buffer: Optional[AlignedBuffer] = None
        self._phase_buffer: Optional[AlignedBuffer] = None
        self._monitor_buffer: Optional[AlignedBuffer] = None

        log.info("PCIe7821API initialized")

    def _find_dll(self) -> str:
        """Find the DLL in default locations"""
        script_dir = Path(__file__).resolve().parent
        project_root = script_dir.parents[1]
        bundle_root = Path(getattr(sys, "_MEIPASS", project_root))
        exe_dir = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else project_root

        # Search paths (prioritize libs/ directory)
        search_paths = [
            bundle_root / "libs" / "pcie7821_api.dll",   # PyInstaller onefile temp bundle
            exe_dir / "libs" / "pcie7821_api.dll",       # External libs next to exe
            project_root / "libs" / "pcie7821_api.dll",  # Primary: libs/ folder
            script_dir / "pcie7821_api.dll",              # Fallback: same folder as this script
            project_root / "pcie7821_api.dll",            # Fallback: project root
            Path("pcie7821_api.dll"),                     # Current working directory
        ]

        for path in search_paths:
            log.debug(f"Checking DLL path: {path}")
            if path.exists():
                log.info(f"Found DLL at: {path}")
                return str(path)

        raise FileNotFoundError(
            f"pcie7821_api.dll not found. Please copy it to: {exe_dir / 'libs'}"
        )

    # ----- DLL FUNCTION PROTOTYPES -----
    # Must match DLL exports exactly (restype, argtypes)

    def _setup_prototypes(self):
        """Setup ctypes function prototypes to match DLL C API signatures"""
        log.debug("Setting up function prototypes...")

        # int pcie7821_open()
        self.dll.pcie7821_open.restype = ctypes.c_int
        self.dll.pcie7821_open.argtypes = []

        # void pcie7821_close()
        self.dll.pcie7821_close.restype = None
        self.dll.pcie7821_close.argtypes = []

        # int pcie7821_set_clk_src(unsigned int clk_src)
        self.dll.pcie7821_set_clk_src.restype = ctypes.c_int
        self.dll.pcie7821_set_clk_src.argtypes = [ctypes.c_uint]

        # int pcie7821_set_trig_dir(unsigned int trig_dir)
        self.dll.pcie7821_set_trig_dir.restype = ctypes.c_int
        self.dll.pcie7821_set_trig_dir.argtypes = [ctypes.c_uint]

        # int pcie7821_set_scan_rate(unsigned int scan_rate)
        self.dll.pcie7821_set_scan_rate.restype = ctypes.c_int
        self.dll.pcie7821_set_scan_rate.argtypes = [ctypes.c_uint]

        # int pcie7821_set_pusle_width(unsigned int pulse_high_width_ns)
        # Note: typo in DLL API name "pusle" instead of "pulse"
        self.dll.pcie7821_set_pusle_width.restype = ctypes.c_int
        self.dll.pcie7821_set_pusle_width.argtypes = [ctypes.c_uint]

        # int pcie7821_set_point_num_per_scan(unsigned int point_num_per_scan)
        self.dll.pcie7821_set_point_num_per_scan.restype = ctypes.c_int
        self.dll.pcie7821_set_point_num_per_scan.argtypes = [ctypes.c_uint]

        # int pcie7821_set_bypass_point_num(unsigned int bypass_point_num)
        self.dll.pcie7821_set_bypass_point_num.restype = ctypes.c_int
        self.dll.pcie7821_set_bypass_point_num.argtypes = [ctypes.c_uint]

        # int pcie7821_set_center_freq(unsigned int center_freq_hz)
        self.dll.pcie7821_set_center_freq.restype = ctypes.c_int
        self.dll.pcie7821_set_center_freq.argtypes = [ctypes.c_uint]

        # int pcie7821_set_upload_data_param(unsigned int upload_ch_num,
        #                                    unsigned int upload_data_src,
        #                                    unsigned int upload_data_rate)
        self.dll.pcie7821_set_upload_data_param.restype = ctypes.c_int
        self.dll.pcie7821_set_upload_data_param.argtypes = [
            ctypes.c_uint, ctypes.c_uint, ctypes.c_uint
        ]

        # int pcie7821_set_phase_dem_param(unsigned int data_rate2phase_dem,
        #                                  unsigned int space_avg_order,
        #                                  unsigned int space_merge_point_num,
        #                                  unsigned int space_region_diff_order,
        #                                  double detrend_filter_bw,
        #                                  unsigned int polarization_diversity_en)
        self.dll.pcie7821_set_phase_dem_param.restype = ctypes.c_int
        self.dll.pcie7821_set_phase_dem_param.argtypes = [
            ctypes.c_uint, ctypes.c_uint, ctypes.c_uint,
            ctypes.c_uint, ctypes.c_double, ctypes.c_uint
        ]

        # int pcie7821_point_num_per_ch_in_buf_query(unsigned int* p_point_num_in_buf_per_ch)
        self.dll.pcie7821_point_num_per_ch_in_buf_query.restype = ctypes.c_int
        self.dll.pcie7821_point_num_per_ch_in_buf_query.argtypes = [
            ctypes.POINTER(ctypes.c_uint)
        ]

        # int pcie7821_read_data(unsigned int point_num_per_ch,
        #                        short* p_data,
        #                        unsigned int* p_points_per_ch_returned)
        self.dll.pcie7821_read_data.restype = ctypes.c_int
        self.dll.pcie7821_read_data.argtypes = [
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_short),
            ctypes.POINTER(ctypes.c_uint)
        ]

        # int pcie7821_read_phase_data(unsigned int point_num_per_ch,
        #                              int* p_phase_data,
        #                              unsigned int* p_points_per_ch_returned)
        self.dll.pcie7821_read_phase_data.restype = ctypes.c_int
        self.dll.pcie7821_read_phase_data.argtypes = [
            ctypes.c_uint,
            ctypes.POINTER(ctypes.c_int),
            ctypes.POINTER(ctypes.c_uint)
        ]

        # int pcie7821_read_monitor_data(unsigned int* p_monitor_data)
        self.dll.pcie7821_read_monitor_data.restype = ctypes.c_int
        self.dll.pcie7821_read_monitor_data.argtypes = [
            ctypes.POINTER(ctypes.c_uint)
        ]

        # int pcie7821_start(void)
        self.dll.pcie7821_start.restype = ctypes.c_int
        self.dll.pcie7821_start.argtypes = []

        # int pcie7821_stop(void)
        self.dll.pcie7821_stop.restype = ctypes.c_int
        self.dll.pcie7821_stop.argtypes = []

        # int pcie7821_test_wr_reg(unsigned int addr, unsigned int data)
        self.dll.pcie7821_test_wr_reg.restype = ctypes.c_int
        self.dll.pcie7821_test_wr_reg.argtypes = [ctypes.c_uint, ctypes.c_uint]

        # int pcie7821_test_rd_reg(unsigned int addr, unsigned int* p_data)
        self.dll.pcie7821_test_rd_reg.restype = ctypes.c_int
        self.dll.pcie7821_test_rd_reg.argtypes = [
            ctypes.c_uint, ctypes.POINTER(ctypes.c_uint)
        ]

        log.debug("Function prototypes setup complete")

    def _check_result(self, result: int, operation: str = ""):
        """Check API result and raise exception on error"""
        if result != 0:
            log.error(f"{operation} failed with code {result}: {get_error_message(result)}")
            raise PCIe7821Error(result, f"{operation}: {get_error_message(result)}")

    # ----- DEVICE CONTROL -----

    def open(self) -> int:
        """
        Open the PCIe-7821 device.

        Returns:
            0 on success, error code on failure
        """
        log.info("Opening device...")
        with self._lock:
            start = time.perf_counter()
            result = self.dll.pcie7821_open()
            elapsed = (time.perf_counter() - start) * 1000

            if result == 0:
                self._is_open = True
                log.info(f"Device opened successfully in {elapsed:.1f} ms")
            else:
                log.error(f"Failed to open device: error code {result}")

            return result

    def close(self):
        """Close the PCIe-7821 device"""
        log.info("Closing device...")
        with self._lock:
            if self.dll is not None:
                self.dll.pcie7821_close()
                self._is_open = False
                log.info("Device closed")

            # Release buffers
            self._raw_buffer = None
            self._phase_buffer = None
            self._monitor_buffer = None

    @property
    def is_open(self) -> bool:
        """Check if device is open"""
        return self._is_open

    # ----- HARDWARE CONFIGURATION -----
    # FUTURE: these setters could be consolidated into a single configure() method

    def set_clk_src(self, clk_src: int) -> int:
        """
        Set clock source.

        Args:
            clk_src: 0=internal, 1=external
        """
        log.debug(f"set_clk_src({clk_src})")
        with self._lock:
            result = self.dll.pcie7821_set_clk_src(clk_src)
        log.debug(f"set_clk_src result: {result}")
        self._check_result(result, "set_clk_src")
        return result

    def set_trig_dir(self, trig_dir: int) -> int:
        """
        Set trigger direction.

        Args:
            trig_dir: 0=input, 1=output
        """
        log.debug(f"set_trig_dir({trig_dir})")
        with self._lock:
            result = self.dll.pcie7821_set_trig_dir(trig_dir)
        log.debug(f"set_trig_dir result: {result}")
        self._check_result(result, "set_trig_dir")
        return result

    def set_scan_rate(self, scan_rate: int) -> int:
        """
        Set scan rate in Hz.

        Args:
            scan_rate: Scan rate in Hz
        """
        log.debug(f"set_scan_rate({scan_rate})")
        with self._lock:
            result = self.dll.pcie7821_set_scan_rate(scan_rate)
        log.debug(f"set_scan_rate result: {result}")
        self._check_result(result, "set_scan_rate")
        return result

    def set_pulse_width(self, pulse_ns: int) -> int:
        """
        Set pulse width in nanoseconds.

        Args:
            pulse_ns: Pulse width in ns
        """
        log.debug(f"set_pulse_width({pulse_ns})")
        with self._lock:
            result = self.dll.pcie7821_set_pusle_width(pulse_ns)
        log.debug(f"set_pulse_width result: {result}")
        self._check_result(result, "set_pulse_width")
        return result

    def set_point_num_per_scan(self, point_num: int) -> int:
        """
        Set number of points per scan.

        Args:
            point_num: Number of points per scan
        """
        log.debug(f"set_point_num_per_scan({point_num})")
        with self._lock:
            result = self.dll.pcie7821_set_point_num_per_scan(point_num)
        log.debug(f"set_point_num_per_scan result: {result}")
        self._check_result(result, "set_point_num_per_scan")
        return result

    def set_bypass_point_num(self, bypass_num: int) -> int:
        """
        Set number of bypass points.

        Args:
            bypass_num: Number of points to bypass
        """
        log.debug(f"set_bypass_point_num({bypass_num})")
        with self._lock:
            result = self.dll.pcie7821_set_bypass_point_num(bypass_num)
        log.debug(f"set_bypass_point_num result: {result}")
        self._check_result(result, "set_bypass_point_num")
        return result

    def set_center_freq(self, freq_hz: int) -> int:
        """
        Set center frequency in Hz.

        Args:
            freq_hz: Center frequency in Hz
        """
        log.debug(f"set_center_freq({freq_hz})")
        with self._lock:
            result = self.dll.pcie7821_set_center_freq(freq_hz)
        log.debug(f"set_center_freq result: {result}")
        self._check_result(result, "set_center_freq")
        return result

    def set_upload_data_param(self, ch_num: int, data_src: int, data_rate: int) -> int:
        """
        Set upload data parameters.

        Args:
            ch_num: Number of channels (1, 2, or 4)
            data_src: Data source (0-4)
            data_rate: Data rate in ns
        """
        log.debug(f"set_upload_data_param(ch_num={ch_num}, data_src={data_src}, data_rate={data_rate})")
        with self._lock:
            result = self.dll.pcie7821_set_upload_data_param(ch_num, data_src, data_rate)
        log.debug(f"set_upload_data_param result: {result}")
        self._check_result(result, "set_upload_data_param")
        return result

    def set_phase_dem_param(self, rate2phase: int, space_avg_order: int,
                            merge_point_num: int, diff_order: int,
                            detrend_bw: float, polarization_en: bool) -> int:
        """
        Set phase demodulation parameters.

        Args:
            rate2phase: Rate to phase demodulation factor
            space_avg_order: Spatial averaging order
            merge_point_num: Number of points to merge
            diff_order: Differentiation order
            detrend_bw: Detrend filter bandwidth in Hz
            polarization_en: Enable polarization diversity
        """
        log.debug(f"set_phase_dem_param(rate2phase={rate2phase}, space_avg={space_avg_order}, "
                  f"merge={merge_point_num}, diff={diff_order}, detrend_bw={detrend_bw}, polar={polarization_en})")
        with self._lock:
            result = self.dll.pcie7821_set_phase_dem_param(
                rate2phase, space_avg_order, merge_point_num,
                diff_order, detrend_bw, int(polarization_en)
            )
        log.debug(f"set_phase_dem_param result: {result}")
        self._check_result(result, "set_phase_dem_param")
        return result

    # ----- DATA READING -----
    # All read methods return copies of aligned buffer data (safe for cross-thread use)

    def query_buffer_points(self) -> int:
        """
        Query number of points per channel in buffer.

        Returns:
            Number of points per channel available in buffer
        """
        point_num = ctypes.c_uint()
        with self._lock:
            start = time.perf_counter()
            result = self.dll.pcie7821_point_num_per_ch_in_buf_query(ctypes.byref(point_num))
            elapsed = (time.perf_counter() - start) * 1000

        if result != 0:
            log.error(
                f"query_buffer_points failed: code {result}, "
                f"points={point_num.value}, elapsed={elapsed:.1f}ms"
            )
            raise PCIe7821Error(result, "query_buffer_points")

        # 0xFFFFFFFF is a common invalid sentinel when a signed -1 leaks through
        # an unsigned output parameter. Never treat it as available DMA data.
        if point_num.value == 0xFFFFFFFF:
            log.error(
                "query_buffer_points returned invalid buffer count 0xFFFFFFFF; "
                "driver/device state is likely stale or faulted"
            )
            raise PCIe7821Error(
                -5,
                "query_buffer_points returned invalid buffer count 0xFFFFFFFF; "
                "reset the PCIe device/driver before starting acquisition again"
            )

        # Only log occasionally to avoid spam (every 100th call or when slow)
        if elapsed > 10:
            log.warning(f"query_buffer_points took {elapsed:.1f} ms, points={point_num.value}")

        return point_num.value

    def allocate_buffers(self, point_num: int, channel_num: int, frame_num: int,
                         merge_point_num: int = 1, is_phase: bool = True):
        """
        Allocate aligned buffers for data reading.

        Args:
            point_num: Points per scan
            channel_num: Number of channels
            frame_num: Number of frames
            merge_point_num: Merge point number for phase data
            is_phase: Whether allocating for phase data
        """
        log.info(f"Allocating buffers: point_num={point_num}, channels={channel_num}, "
                 f"frames={frame_num}, merge={merge_point_num}")

        # Raw data buffer (short)
        raw_size = point_num * channel_num * frame_num
        self._raw_buffer = AlignedBuffer(raw_size, np.int16)
        log.debug(f"Raw buffer allocated: {raw_size * 2 / 1024 / 1024:.2f} MB")

        # Phase data buffer (int)
        phase_point_num = point_num // merge_point_num
        phase_size = phase_point_num * channel_num * frame_num
        self._phase_buffer = AlignedBuffer(phase_size, np.int32)
        log.debug(f"Phase buffer allocated: {phase_size * 4 / 1024 / 1024:.2f} MB")

        # Monitor data buffer (uint)
        monitor_size = phase_point_num * channel_num
        self._monitor_buffer = AlignedBuffer(monitor_size, np.uint32)
        log.debug(f"Monitor buffer allocated: {monitor_size * 4 / 1024:.2f} KB")

        log.info("Buffer allocation complete")

    def read_data(self, point_num_per_ch: int, channel_num: int) -> Tuple[np.ndarray, int]:
        """
        Read raw data from device.

        Args:
            point_num_per_ch: Number of points per channel to read
            channel_num: Number of channels
            copy: Return an owned array. False exposes a view valid until the next phase read.

        Returns:
            Tuple of (data array, points actually returned per channel)
        """
        total_points = point_num_per_ch * channel_num

        # Ensure buffer is large enough
        if self._raw_buffer is None or self._raw_buffer.size < total_points:
            log.debug(f"Reallocating raw buffer: {total_points} points")
            self._raw_buffer = AlignedBuffer(total_points, np.int16)

        points_returned = ctypes.c_uint()

        with self._lock:
            start = time.perf_counter()
            result = self.dll.pcie7821_read_data(
                point_num_per_ch,
                self._raw_buffer.get_ctypes_ptr(),
                ctypes.byref(points_returned)
            )
            elapsed = (time.perf_counter() - start) * 1000

        if result != 0:
            log.error(f"read_data failed: code {result}")
            raise PCIe7821Error(result, "read_data")

        log.debug(f"read_data: requested={point_num_per_ch}, returned={points_returned.value}, "
                  f"time={elapsed:.1f}ms")

        # Return copy of data
        return self._raw_buffer.array[:total_points].copy(), points_returned.value

    def read_phase_data(
        self,
        point_num_per_ch: int,
        channel_num: int,
        copy: bool = True,
    ) -> Tuple[np.ndarray, int]:
        """
        Read phase data from device.

        Args:
            point_num_per_ch: Number of points per channel to read
            channel_num: Number of channels
            copy: Return an owned array. False exposes a view valid until the next phase read.

        Returns:
            Tuple of (phase data array, points actually returned per channel)
        """
        total_points = point_num_per_ch * channel_num

        # Ensure buffer is large enough
        if self._phase_buffer is None or self._phase_buffer.size < total_points:
            log.debug(f"Reallocating phase buffer: {total_points} points")
            self._phase_buffer = AlignedBuffer(total_points, np.int32)

        points_returned = ctypes.c_uint()

        with self._lock:
            start = time.perf_counter()
            result = self.dll.pcie7821_read_phase_data(
                point_num_per_ch,
                self._phase_buffer.get_ctypes_ptr(),
                ctypes.byref(points_returned)
            )
            elapsed = (time.perf_counter() - start) * 1000

        if result != 0:
            log.error(f"read_phase_data failed: code {result}")
            raise PCIe7821Error(result, "read_phase_data")

        log.debug(f"read_phase_data: requested={point_num_per_ch}, returned={points_returned.value}, "
                  f"time={elapsed:.1f}ms")

        phase_view = self._phase_buffer.array[:total_points]
        phase_data = phase_view.copy() if copy else phase_view
        return phase_data, points_returned.value

    def read_monitor_data(self, point_num: int, channel_num: int) -> np.ndarray:
        """
        Read monitor data from device.

        Args:
            point_num: Number of points
            channel_num: Number of channels
            copy: Return an owned array. False exposes a view valid until the next phase read.

        Returns:
            Monitor data array
        """
        total_points = point_num * channel_num

        # Ensure buffer is large enough
        if self._monitor_buffer is None or self._monitor_buffer.size < total_points:
            log.debug(f"Reallocating monitor buffer: {total_points} points")
            self._monitor_buffer = AlignedBuffer(total_points, np.uint32)

        with self._lock:
            start = time.perf_counter()
            result = self.dll.pcie7821_read_monitor_data(
                self._monitor_buffer.get_ctypes_ptr()
            )
            elapsed = (time.perf_counter() - start) * 1000

        if result != 0:
            log.error(f"read_monitor_data failed: code {result}")
            raise PCIe7821Error(result, "read_monitor_data")

        log.debug(f"read_monitor_data: points={total_points}, time={elapsed:.1f}ms")

        return self._monitor_buffer.array[:total_points].copy()

    def start(self) -> int:
        """Start acquisition"""
        log.info("Starting acquisition...")
        with self._lock:
            start = time.perf_counter()
            result = self.dll.pcie7821_start()
            elapsed = (time.perf_counter() - start) * 1000

        if result == 0:
            log.info(f"Acquisition started in {elapsed:.1f} ms")
        else:
            log.error(f"Failed to start acquisition: code {result}")

        return result

    def stop(self) -> int:
        """Stop acquisition"""
        log.info("Stopping acquisition...")
        with self._lock:
            start = time.perf_counter()
            result = self.dll.pcie7821_stop()
            elapsed = (time.perf_counter() - start) * 1000

        if result == 0:
            log.info(f"Acquisition stopped in {elapsed:.1f} ms")
        else:
            log.error(f"Failed to stop acquisition: code {result}")

        return result

    # ----- REGISTER ACCESS (TEST / DEBUG) -----

    def write_reg(self, addr: int, data: int) -> int:
        """
        Write to register (for testing).

        Args:
            addr: Register address (must be 4-byte aligned)
            data: Data to write
        """
        if addr % 4 != 0:
            raise ValueError("Register address must be 4-byte aligned")
        log.debug(f"write_reg(addr=0x{addr:X}, data=0x{data:X})")
        with self._lock:
            result = self.dll.pcie7821_test_wr_reg(addr, data)
        return result

    def read_reg(self, addr: int) -> int:
        """
        Read from register (for testing).

        Args:
            addr: Register address (must be 4-byte aligned)

        Returns:
            Register value
        """
        if addr % 4 != 0:
            raise ValueError("Register address must be 4-byte aligned")
        data = ctypes.c_uint()
        with self._lock:
            self.dll.pcie7821_test_rd_reg(addr, ctypes.byref(data))
        log.debug(f"read_reg(addr=0x{addr:X}) = 0x{data.value:X}")
        return data.value

    def __enter__(self):
        """Context manager entry"""
        self.open()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit"""
        self.close()
        return False

    def __del__(self):
        """Destructor"""
        if self._is_open:
            self.close()
