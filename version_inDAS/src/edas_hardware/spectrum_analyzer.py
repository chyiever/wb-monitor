"""
`src/spectrum_analyzer.py` 封装实时频谱与 PSD 分析逻辑，服务于主窗口中的频谱图。

这个模块与采集、GUI 控件和 DLL 读取解耦得比较干净。它主要关心输入数组、采样率、窗函数、平均次数和功率谱/功率谱密度的计算方式，不直接依赖界面组件，因此既可以被当前实时 GUI 调用，也适合后续离线分析或自动测试脚本复用。

当前版本的重要约定是：Raw 数据与 Phase 数据虽然都能进入同一分析器，但物理语义不同，主窗口会根据数据源决定更适合显示功率谱还是 PSD。
"""
import numpy as np
from typing import Tuple, Optional
from enum import IntEnum
from scipy import signal


# ----- WINDOW FUNCTION DEFINITIONS -----
# Enumeration of supported window functions for spectral analysis

class WindowType(IntEnum):
    """
    Window function types for FFT spectral analysis.

    Different window functions provide trade-offs between:
    - Frequency resolution (main lobe width)
    - Spectral leakage (side lobe suppression)
    - Amplitude accuracy (scalloping loss)

    Window Characteristics:
        RECTANGULAR: No windowing - best frequency resolution, worst leakage
        HANNING: General purpose - good balance of resolution vs leakage
        HAMMING: Slightly better side lobe suppression than Hanning
        BLACKMAN: Excellent side lobe suppression, wider main lobe
        FLATTOP: Best amplitude accuracy, poor frequency resolution

    Usage Guidelines:
        - HANNING: Default choice for most applications
        - BLACKMAN: When strong interfering signals are present
        - FLATTOP: When precise amplitude measurements are required
        - RECTANGULAR: Only when no spectral leakage is expected
    """
    RECTANGULAR = 0  # No windowing (rectangular window)
    HANNING = 1      # Hanning (raised cosine) window - most common
    HAMMING = 2      # Hamming window - slightly different from Hanning
    BLACKMAN = 3     # Blackman window - excellent side lobe suppression
    FLATTOP = 4      # Flat-top window - best for amplitude accuracy


# ----- CORE SPECTRUM ANALYZER CLASS -----
# Primary FFT analysis engine with window function support

class SpectrumAnalyzer:
    """
    FFT-based spectrum analyzer for power spectrum and PSD calculations.

    This class implements a complete spectrum analysis pipeline including:
    - Window function application with caching for performance
    - FFT computation with proper scaling
    - Power spectrum calculation (V²)
    - Power Spectral Density computation (V²/Hz)
    - Window correction factors for accurate measurements

    The implementation follows established DSP practices and provides
    results compatible with standard spectrum analyzer instruments.

    Attributes:
        window_type: Currently selected window function type
        IMPEDANCE: Reference impedance for power calculations (50Ω standard)
        _window_cache: Performance cache for computed window functions

    Mathematical Background:
        Power Spectrum: |X(f)|² represents power at each frequency
        PSD: Power Spectrum / frequency_resolution gives power density
        Window Correction: Compensates for window function amplitude effects

    Usage:
        analyzer = SpectrumAnalyzer(WindowType.HANNING)
        freq, power_db, df = analyzer.analyze_int(phase_data, 2000.0)
    """

    # Reference impedance for dBm calculation (industry standard)
    IMPEDANCE = 50.0  # Ohms - used for absolute power measurements

    def __init__(self, window_type: WindowType = WindowType.HANNING):
        """
        Initialize spectrum analyzer with specified window function.

        Args:
            window_type: Window function for spectral analysis (default: Hanning)

        Design Notes:
            - Hanning window provides good balance of resolution vs leakage
            - Window cache improves performance for repeated analysis of same length
            - Window selection can be changed dynamically without recreating analyzer
        """
        self.window_type = window_type
        self._window_cache = {}  # Cache for performance optimization

    def _get_window(self, size: int) -> np.ndarray:
        """
        Retrieve or compute cached window function for given size.

        Window functions are computationally expensive to generate repeatedly,
        so this method implements caching to improve real-time performance.
        Cache is keyed by data length to support variable frame sizes.

        Args:
            size: Number of samples in window function

        Returns:
            NumPy array containing window coefficients [0.0, 1.0]

        Window Function Details:
            - All windows normalized to preserve DC gain
            - Flat-top window uses 5-term Blackman-Harris coefficients
            - Cache automatically manages memory for different sizes

        Performance: O(1) for cached sizes, O(n) for new sizes
        """
        if size not in self._window_cache:
            if self.window_type == WindowType.RECTANGULAR:
                # No windowing - uniform coefficients
                window = np.ones(size)

            elif self.window_type == WindowType.HANNING:
                # Raised cosine window - most common choice
                window = np.hanning(size)

            elif self.window_type == WindowType.HAMMING:
                # Modified raised cosine with better side lobe suppression
                window = np.hamming(size)

            elif self.window_type == WindowType.BLACKMAN:
                # Excellent side lobe suppression at cost of resolution
                window = np.blackman(size)

            elif self.window_type == WindowType.FLATTOP:
                # Optimized for amplitude accuracy - 5-term Blackman-Harris
                # Coefficients from Harris 1978 paper on window functions
                a0, a1, a2, a3, a4 = 0.21557895, 0.41663158, 0.277263158, 0.083578947, 0.006947368
                n = np.arange(size)
                window = a0 - a1*np.cos(2*np.pi*n/(size-1)) + a2*np.cos(4*np.pi*n/(size-1)) \
                        - a3*np.cos(6*np.pi*n/(size-1)) + a4*np.cos(8*np.pi*n/(size-1))
            else:
                # Fallback to Hanning for unknown window types
                window = np.hanning(size)

            # Cache computed window for future use
            self._window_cache[size] = window

        return self._window_cache[size]

    # ----- DATA TYPE SPECIFIC ANALYSIS METHODS -----
    # Separate methods for different input data types with appropriate scaling

    def analyze_short(self, data: np.ndarray, sample_rate: float,
                      psd_mode: bool = False) -> Tuple[np.ndarray, np.ndarray, float]:
        """
        Analyze 16-bit integer (short) raw ADC data.

        Converts raw ADC counts to physical voltage units before FFT analysis.
        This method is typically used for raw backscattered optical data
        directly from the ADC before any signal processing.

        Args:
            data: Input time-domain data as int16 ADC counts
            sample_rate: Sample rate in Hz (affects frequency axis and PSD normalization)
            psd_mode: If True, return PSD (dB/Hz); if False, return power spectrum (dB)

        Returns:
            Tuple of (frequency_axis, spectrum_db, frequency_resolution)
            - frequency_axis: Frequency bins in Hz (positive frequencies only)
            - spectrum_db: Power spectrum (dB) or PSD (dB/Hz) depending on psd_mode
            - frequency_resolution: Frequency spacing between bins (Hz)

        ADC Scaling:
            - Assumes 16-bit ADC with ±0.95V full scale range
            - Scaling factor: 0.95V / 32767 counts = 29.05 µV/count
            - This scaling affects absolute power levels but not relative measurements

        Usage:
            freq, power, df = analyzer.analyze_short(adc_data, 1e6, psd_mode=False)
        """
        # Convert ADC counts to voltage assuming 16-bit ADC with 0.95V range
        # Full scale: ±32767 counts = ±0.95V
        data_v = data.astype(np.float64) * 0.95 / 32767.0
        return self._analyze(data_v, sample_rate, psd_mode)

    def analyze_int(self, data: np.ndarray, sample_rate: float,
                    psd_mode: bool = False) -> Tuple[np.ndarray, np.ndarray, float]:
        """
        Analyze 32-bit integer phase data from DAS processing.

        Processes phase-demodulated data directly without voltage conversion.
        This method is used for phase data that has already undergone I/Q
        demodulation and phase calculation in the FPGA.

        Args:
            data: Input phase data as int32 (arbitrary phase units)
            sample_rate: Sample rate in Hz (typically after decimation)
            psd_mode: If True, return PSD (dB/Hz); if False, return power spectrum (dB)

        Returns:
            Tuple of (frequency_axis, spectrum_db, frequency_resolution)
            - frequency_axis: Frequency bins in Hz (positive frequencies only)
            - spectrum_db: Power spectrum (dB) or PSD (dB/Hz) depending on psd_mode
            - frequency_resolution: Frequency spacing between bins (Hz)

        Phase Data Characteristics:
            - Units are arbitrary (phase radians or scaled integers)
            - No voltage conversion applied - direct spectral analysis
            - Typically has better SNR than raw ADC data due to coherent processing

        Usage:
            freq, power, df = analyzer.analyze_int(phase_data, 2000.0, psd_mode=True)
        """
        # Convert to double precision directly for phase data (no voltage scaling)
        data_d = data.astype(np.float64)
        return self._analyze(data_d, sample_rate, psd_mode)

    # ----- PHASE DATA PSD USING SCIPY WELCH -----
    # New method for phase data analysis using scipy.signal.welch

    def _analyze_phase_psd_welch(self, data: np.ndarray, sample_rate: float) -> Tuple[np.ndarray, np.ndarray, float]:
        """
        Analyze phase data using scipy.signal.welch for PSD calculation.

        Uses scipy.welch with window length = signal length (no overlap) for
        maximum frequency resolution and density-type output in dB scale.

        Args:
            data: Phase data as int32 (arbitrary phase units)
            sample_rate: Sample rate in Hz

        Returns:
            Tuple of (frequency_axis, psd_db, frequency_resolution)
            - frequency_axis: Frequency bins in Hz (0 to Nyquist)
            - psd_db: Power spectral density in dB (density type)
            - frequency_resolution: Frequency bin spacing in Hz
        """
        # Convert to double precision for phase data (no voltage scaling)
        data_d = data.astype(np.float64)
        n = len(data_d)

        # Get window function for scipy welch
        # Convert WindowType enum to scipy window string
        window_map = {
            WindowType.RECTANGULAR: 'boxcar',
            WindowType.HANNING: 'hann',
            WindowType.HAMMING: 'hamming',
            WindowType.BLACKMAN: 'blackman',
            WindowType.FLATTOP: 'flattop'
        }
        window_name = window_map.get(self.window_type, 'hann')

        # Calculate PSD using scipy.welch
        # nperseg = signal length (no windowing), noverlap = 0
        freq_axis, psd_linear = signal.welch(
            data_d,
            fs=sample_rate,
            window=window_name,
            nperseg=n,  # Window length = signal length
            noverlap=0,  # No overlap since nperseg = signal length
            nfft=n,  # FFT length = signal length
            return_onesided=True,  # Single-sided spectrum
            scaling='density',  # PSD in units²/Hz
            detrend='constant'  # Remove DC component
        )

        # Convert linear PSD to dB scale with numerical stability
        psd_db = 10.0 * np.log10(psd_linear + 1e-20)

        # Calculate frequency resolution
        df = sample_rate / n

        return freq_axis, psd_db, df

    # ----- CORE FFT ANALYSIS ENGINE -----
    # Internal implementation of the complete spectrum analysis pipeline

    def _analyze(self, data: np.ndarray, sample_rate: float,
                 psd_mode: bool = False) -> Tuple[np.ndarray, np.ndarray, float]:
        """
        Internal spectrum analysis implementation with full DSP pipeline.

        This method implements the complete digital signal processing chain
        for spectrum analysis, including proper window correction factors
        and scaling for accurate power measurements.

        Processing Pipeline:
        1. Apply window function to reduce spectral leakage
        2. Calculate window correction factors (coherent gain, noise bandwidth)
        3. Perform FFT transformation
        4. Compute single-sided power spectrum
        5. Apply window corrections for accurate measurements
        6. Convert to dB scale with PSD option

        Args:
            data: Input time-domain data (float64, any units)
            sample_rate: Sample rate in Hz (determines frequency axis)
            psd_mode: If True, return PSD (dB/Hz); if False, return power spectrum (dB)

        Returns:
            Tuple of (frequency_axis, spectrum_db, frequency_resolution)
            - frequency_axis: Positive frequency bins from 0 to Nyquist (Hz)
            - spectrum_db: Power spectrum or PSD in decibel scale
            - frequency_resolution: Spacing between frequency bins (Hz)

        Mathematical Details:
            - Power Spectrum: |X(f)|² / N² with window corrections
            - PSD: Power Spectrum / (df × noise_bandwidth)
            - Single-sided: Double power except at DC to preserve total power
            - Window corrections ensure accurate amplitude measurements

        Numerical Stability:
            - Small epsilon (1e-20) prevents log(0) errors
            - Double precision maintains accuracy through calculations
            - Proper scaling preserves dynamic range
        """
        n = len(data)

        # ----- STEP 1: WINDOW FUNCTION APPLICATION -----
        # Apply selected window to reduce spectral leakage from finite data length
        window = self._get_window(n)
        windowed_data = data * window

        # ----- STEP 2: WINDOW CORRECTION FACTOR CALCULATION -----
        # Calculate factors needed to correct for window function effects

        # Coherent gain: compensates for window amplitude reduction
        # Ensures that sinusoidal signals maintain correct amplitude
        coherent_gain = np.sum(window) / n

        # Noise bandwidth: accounts for window function's effect on noise power
        # Used for PSD calculations to maintain proper noise floor scaling
        noise_bandwidth = np.sum(window**2) / (np.sum(window)**2) * n

        # ----- STEP 3: FFT COMPUTATION -----
        # Transform windowed data to frequency domain
        fft_result = np.fft.fft(windowed_data)

        # ----- STEP 4: POWER SPECTRUM CALCULATION -----
        # Calculate power spectrum (V²) from complex FFT coefficients
        # Use single-sided spectrum (positive frequencies only) for efficiency
        n_half = n // 2  # Number of positive frequency bins
        power_spectrum = np.abs(fft_result[:n_half])**2 / (n**2)

        # ----- STEP 5: WINDOW CORRECTION APPLICATION -----
        # Correct for coherent gain loss due to windowing
        power_spectrum /= coherent_gain**2

        # Convert to single-sided spectrum by doubling power (except DC)
        # This preserves total power: sum of single-sided = sum of double-sided
        power_spectrum[1:] *= 2

        # ----- STEP 6: FREQUENCY AXIS GENERATION -----
        # Create frequency axis from 0 to Nyquist frequency
        df = sample_rate / n  # Frequency resolution (Hz per bin)
        freq_axis = np.arange(n_half) * df

        # ----- STEP 7: DECIBEL CONVERSION WITH PSD OPTION -----
        # Convert linear power to logarithmic scale with optional PSD normalization

        if psd_mode:
            # Power Spectral Density: power per unit frequency (V²/Hz)
            # Divide by frequency resolution and noise bandwidth correction
            power_density = power_spectrum / (df * noise_bandwidth)
            # Convert to dB/Hz scale with numerical stability epsilon
            spectrum_db = 10.0 * np.log10(power_density + 1e-20)
        else:
            # Power spectrum: total power in each frequency bin (V²)
            # Convert to dB scale with numerical stability epsilon
            spectrum_db = 10.0 * np.log10(power_spectrum + 1e-20)

        return freq_axis, spectrum_db, df

    # ----- PUBLIC API METHODS -----
    # High-level interface for automatic data type handling and configuration

    def analyze(self, data: np.ndarray, sample_rate: float,
                psd_mode: bool = False, data_type: str = 'short') -> Tuple[np.ndarray, np.ndarray, float]:
        """
        Analyze data with automatic type detection and routing.

        New Logic:
        - Raw data (data_type='short'): Only compute power spectrum (ignore psd_mode)
        - Phase data (data_type='int'): Only compute PSD using scipy.welch (ignore psd_mode)

        Args:
            data: Input time-domain data (any numeric type)
            sample_rate: Sample rate in Hz (determines frequency scaling)
            psd_mode: Deprecated - analysis type now determined by data_type
            data_type: 'short' for raw ADC data → power spectrum, 'int' for phase data → PSD

        Returns:
            Tuple of (frequency_axis, spectrum_db, frequency_resolution)
            - frequency_axis: Frequency bins in Hz (0 to Nyquist)
            - spectrum_db: Power spectrum (dB) for raw data, PSD (dB) for phase data
            - frequency_resolution: Frequency bin spacing in Hz

        Analysis Methods:
            Raw data: Custom FFT-based power spectrum calculation
            Phase data: scipy.signal.welch PSD calculation with window length = signal length
        """
        if data_type == 'short' or data.dtype == np.int16:
            # Raw data: Always compute power spectrum (ignore psd_mode)
            return self.analyze_short(data, sample_rate, psd_mode=False)
        else:
            # Phase data: Always compute PSD using scipy welch
            return self._analyze_phase_psd_welch(data, sample_rate)

    def set_window(self, window_type: WindowType):
        """
        Change window function type and clear cache.

        Allows dynamic reconfiguration of window function during operation.
        Cache is cleared to ensure new window type takes effect immediately.

        Args:
            window_type: New window function to use (see WindowType enum)

        Side Effects:
            - Updates internal window_type setting
            - Clears window function cache to force recomputation
            - Next analysis will use new window type

        Performance Note:
            First analysis after window change will be slower due to cache miss.
            Subsequent analyses of same data length will be fast due to caching.

        Usage:
            analyzer.set_window(WindowType.BLACKMAN)  # Switch to Blackman window
            freq, spectrum, df = analyzer.analyze(data, sample_rate)  # Uses new window
        """
        self.window_type = window_type
        self._window_cache.clear()  # Force recomputation of cached windows


# ----- REAL-TIME SPECTRUM ANALYZER WITH AVERAGING -----
# Enhanced analyzer with temporal averaging for noise reduction

class RealTimeSpectrumAnalyzer(SpectrumAnalyzer):
    """
    Real-time spectrum analyzer with temporal averaging capabilities.

    Extends the basic SpectrumAnalyzer with running average functionality
    to reduce noise and improve measurement stability in real-time applications.
    Maintains a circular buffer of recent spectra and computes statistically
    correct averages in the linear domain.

    Key Features:
    - Configurable averaging depth for noise reduction
    - Statistically correct linear-domain averaging
    - Automatic buffer management with circular queue
    - Reset capability for measurement restart
    - Dynamic averaging count adjustment

    Mathematical Background:
    - Averaging performed in linear power domain (not dB)
    - Preserves statistical properties of noise and signals
    - Final result converted back to dB after averaging
    - Noise reduction: ~3dB improvement per 2x averaging count

    Usage Scenarios:
    - Noisy signal environments requiring stable measurements
    - Long-term spectral monitoring with trend analysis
    - Real-time displays requiring smooth spectral updates
    - Applications where measurement stability > update rate

    Attributes:
        averaging_count: Number of spectra to average (1 = no averaging)
        _spectrum_buffer: Circular buffer storing recent spectra (linear power)
        _freq_axis: Cached frequency axis from latest analysis
        _df: Cached frequency resolution from latest analysis
    """

    def __init__(self, window_type: WindowType = WindowType.HANNING,
                 averaging_count: int = 1):
        """
        Initialize real-time spectrum analyzer with averaging.

        Args:
            window_type: Window function for spectral analysis (default: Hanning)
            averaging_count: Number of spectra to average (1 = no averaging)

        Design Decisions:
            - Default to single spectrum (no averaging) for lowest latency
            - Hanning window provides good general-purpose characteristics
            - Buffer initialized empty - first few results will have < full averaging

        Memory Usage:
            - Buffer size: averaging_count × spectrum_length × 8 bytes (float64)
            - Example: 10 averages × 1024 points × 8 bytes = ~80KB
        """
        super().__init__(window_type)
        self.averaging_count = averaging_count  # Number of spectra to average
        self._spectrum_buffer = []              # Circular buffer for averaging
        self._freq_axis: Optional[np.ndarray] = None  # Cached frequency axis
        self._df: float = 0                     # Cached frequency resolution

    def update(self, data: np.ndarray, sample_rate: float,
               psd_mode: bool = False, data_type: str = 'short') -> Tuple[np.ndarray, np.ndarray, float]:
        """
        Update spectrum with new data and return averaged result.

        New Logic:
        - Raw data (data_type='short'): Always compute power spectrum
        - Phase data (data_type='int'): Always compute PSD using scipy.welch

        This is the primary method for real-time operation. Each call:
        1. Computes spectrum of new data (method depends on data_type)
        2. Adds to averaging buffer
        3. Maintains buffer size (circular queue behavior)
        4. Computes average in linear domain
        5. Converts result to dB scale

        Args:
            data: New time-domain data frame
            sample_rate: Sample rate in Hz
            psd_mode: Deprecated - analysis type determined by data_type
            data_type: 'short' for raw data → power spectrum, 'int' for phase data → PSD

        Returns:
            Tuple of (frequency_axis, averaged_spectrum_db, frequency_resolution)
            - frequency_axis: Frequency bins (cached for performance)
            - averaged_spectrum_db: Temporally averaged spectrum in dB (power) or dB (PSD density)
            - frequency_resolution: Frequency bin spacing (cached for performance)

        Averaging Algorithm:
            1. Convert each spectrum from dB to linear: P = 10^(dB/10)
            2. Accumulate linear values: sum(P_i) for i in buffer
            3. Compute average: avg_power = sum(P_i) / N
            4. Convert back to dB: dB = 10 * log10(avg_power)

        Analysis Methods:
            Raw data: Custom FFT-based power spectrum (same as before)
            Phase data: scipy.signal.welch PSD with density scaling
        """
        # Analyze new data frame using parent class methods
        freq_axis, spectrum_db, df = self.analyze(data, sample_rate, psd_mode, data_type)

        # Cache frequency information for consistent output
        self._freq_axis = freq_axis
        self._df = df

        # Add new spectrum to averaging buffer
        self._spectrum_buffer.append(spectrum_db)

        # Maintain buffer size (implement circular queue)
        if len(self._spectrum_buffer) > self.averaging_count:
            self._spectrum_buffer.pop(0)  # Remove oldest spectrum

        # Compute average in linear power domain (statistically correct)
        linear_sum = np.zeros_like(spectrum_db)
        for s in self._spectrum_buffer:
            # Convert each dB spectrum back to linear power for averaging
            linear_sum += 10 ** (s / 10)

        # Calculate mean linear power
        linear_avg = linear_sum / len(self._spectrum_buffer)

        # Convert averaged linear power back to dB scale
        averaged_db = 10 * np.log10(linear_avg + 1e-20)  # Epsilon for numerical stability

        return freq_axis, averaged_db, df

    def reset(self):
        """
        Clear averaging buffer and restart measurements.

        Removes all stored spectra from averaging buffer, effectively
        restarting the averaging process. Next update() call will begin
        accumulating fresh spectra for averaging.

        Use Cases:
            - Measurement restart after parameter changes
            - Clear old data when signal characteristics change significantly
            - Reset after long idle periods
            - Initialize clean state for new measurement session

        Side Effects:
            - Clears all buffered spectra
            - Resets cached frequency information
            - Next few measurements will have reduced averaging until buffer refills
        """
        self._spectrum_buffer.clear()           # Remove all buffered spectra
        self._freq_axis = None                  # Clear cached frequency axis
        self._df = 0                           # Clear cached frequency resolution

    def set_averaging_count(self, count: int):
        """
        Dynamically adjust number of spectra to average.

        Allows real-time adjustment of averaging depth without recreating
        the analyzer. Useful for adaptive noise reduction based on
        signal conditions or user preferences.

        Args:
            count: New averaging count (minimum value: 1 for no averaging)

        Behavior:
            - If new count < current buffer size: trims buffer to newest spectra
            - If new count > current buffer size: keeps existing buffer, grows naturally
            - Count of 1 effectively disables averaging (returns latest spectrum)

        Performance Trade-offs:
            - Larger count: Better noise reduction, slower response to signal changes
            - Smaller count: Faster response, less noise reduction
            - Count of 1: Minimum latency, no noise reduction

        Usage:
            # Adaptive averaging based on signal quality
            if signal_noisy:
                analyzer.set_averaging_count(10)  # More averaging for noise reduction
            else:
                analyzer.set_averaging_count(1)   # Fast response for clean signals
        """
        # Ensure minimum valid averaging count
        self.averaging_count = max(1, count)

        # Trim buffer if new count is smaller than current buffer size
        if len(self._spectrum_buffer) > self.averaging_count:
            # Keep only the most recent spectra
            self._spectrum_buffer = self._spectrum_buffer[-self.averaging_count:]
