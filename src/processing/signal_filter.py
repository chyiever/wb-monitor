"""
Signal Filtering Module for PCCP Monitoring System

This module provides digital signal filtering capabilities using various
filter types (low-pass, high-pass, band-pass, band-stop) with Butterworth
design for real-time processing of fiber interferometer signals.

Features:
- Multiple filter types with configurable parameters
- Real-time filtering with minimal phase distortion
- Automatic filter design and validation
- Performance monitoring and error handling

Author: Claude
Date: 2026-03-11
"""

import logging
import numpy as np
from typing import Tuple, Optional, Dict, Any
from scipy import signal
import time


class SignalFilter:
    """
    Digital signal filter for real-time processing.

    This class implements Butterworth filters for various filtering operations
    on fiber interferometer signals with configurable parameters and
    real-time performance optimization.
    """

    # Filter type constants
    FILTER_TYPES = ['lowpass', 'highpass', 'bandpass', 'bandstop', 'none']

    def __init__(self, sample_rate: float = 1000000.0):
        """
        Initialize the signal filter.

        Args:
            sample_rate: Sampling rate in Hz
        """
        self.sample_rate = sample_rate
        self.logger = logging.getLogger(__name__ + '.SignalFilter')

        # Current filter configuration
        self.filter_type = 'none'
        self.cutoff_freq = None  # Single frequency or tuple
        self.filter_order = 4
        self.filter_coefficients = None

        # Filter state for continuous filtering
        self.filter_state = None

        # Performance statistics
        self.total_samples_processed = 0
        self.total_processing_time = 0
        self.last_filter_time = 0

    def design_filter(self, filter_type: str, cutoff_freq, order: int = 4) -> bool:
        """
        Design a digital filter with specified parameters.

        Args:
            filter_type: Type of filter ('lowpass', 'highpass', 'bandpass', 'bandstop', 'none')
            cutoff_freq: Cutoff frequency (single value) or frequencies (tuple for bandpass/bandstop)
            order: Filter order

        Returns:
            True if filter design successful, False otherwise
        """
        try:
            if filter_type not in self.FILTER_TYPES:
                raise ValueError(f"Unsupported filter type: {filter_type}")

            if filter_type == 'none':
                self.filter_type = 'none'
                self.filter_coefficients = None
                self.filter_state = None
                self.logger.info("Filter disabled")
                return True

            # Validate and normalize frequencies
            nyquist = self.sample_rate / 2.0

            if filter_type in ['lowpass', 'highpass']:
                if not isinstance(cutoff_freq, (int, float)):
                    raise ValueError("Single cutoff frequency required for lowpass/highpass")

                if cutoff_freq >= nyquist:
                    raise ValueError(f"Cutoff frequency {cutoff_freq} must be less than Nyquist {nyquist}")

                normalized_freq = cutoff_freq / nyquist
                self.cutoff_freq = cutoff_freq

            else:  # bandpass or bandstop
                if not isinstance(cutoff_freq, (tuple, list)) or len(cutoff_freq) != 2:
                    raise ValueError("Two cutoff frequencies required for bandpass/bandstop")

                low_freq, high_freq = cutoff_freq

                if low_freq >= high_freq:
                    raise ValueError("Low frequency must be less than high frequency")

                if high_freq >= nyquist:
                    raise ValueError(f"High cutoff frequency {high_freq} must be less than Nyquist {nyquist}")

                normalized_freq = [low_freq / nyquist, high_freq / nyquist]
                self.cutoff_freq = (low_freq, high_freq)

            # Design the filter
            sos = signal.butter(
                order,
                normalized_freq,
                btype=filter_type,
                analog=False,
                output='sos'
            )

            self.filter_coefficients = sos
            self.filter_type = filter_type
            self.filter_order = order

            # Initialize filter state
            self.filter_state = signal.sosfilt_zi(sos)

            self.logger.info(
                f"Designed {filter_type} filter: order={order}, "
                f"cutoff={cutoff_freq}, sample_rate={self.sample_rate}"
            )

            return True

        except Exception as e:
            self.logger.error(f"Filter design failed: {e}")
            return False

    def apply_filter(self, data: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Apply the configured filter to input data.

        Args:
            data: Input signal data

        Returns:
            Tuple of (filtered_data, statistics)
        """
        start_time = time.time()

        try:
            # Validate input
            if not isinstance(data, np.ndarray):
                data = np.array(data)

            if len(data) == 0:
                return np.array([]), self._get_filter_stats(start_time, 0)

            # Check for invalid values
            if np.any(~np.isfinite(data)):
                self.logger.warning("Input data contains NaN or infinite values")
                # Replace invalid values with zeros
                data = np.where(np.isfinite(data), data, 0)

            # Apply filtering
            if self.filter_type == 'none' or self.filter_coefficients is None:
                filtered_data = data.copy()
                stats = self._get_filter_stats(start_time, len(data))

            else:
                filtered_data, self.filter_state = signal.sosfilt(
                    self.filter_coefficients,
                    data,
                    zi=self.filter_state
                )

                stats = self._get_filter_stats(start_time, len(data))

            # Update statistics
            self.total_samples_processed += len(data)
            self.last_filter_time = time.time() - start_time
            self.total_processing_time += self.last_filter_time

            return filtered_data, stats

        except Exception as e:
            self.logger.error(f"Filter application failed: {e}")
            return data.copy(), self._get_filter_stats(start_time, len(data), error=str(e))

    def get_filter_response(self, frequencies: np.ndarray = None) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
        """
        Calculate the frequency response of the current filter.

        Args:
            frequencies: Frequency array for response calculation (None for default)

        Returns:
            Tuple of (frequencies, magnitude, phase)
        """
        try:
            if self.filter_coefficients is None:
                if frequencies is None:
                    frequencies = np.logspace(1, np.log10(self.sample_rate/2), 1000)
                magnitude = np.ones_like(frequencies)
                phase = np.zeros_like(frequencies)
                return frequencies, magnitude, phase

            # Default frequency array if not provided
            if frequencies is None:
                frequencies = np.logspace(1, np.log10(self.sample_rate/2), 1000)

            # Calculate frequency response
            w, h = signal.sosfreqz(
                self.filter_coefficients,
                worN=frequencies,
                fs=self.sample_rate
            )

            magnitude = np.abs(h)
            phase = np.angle(h)

            return w, magnitude, phase

        except Exception as e:
            self.logger.error(f"Error calculating filter response: {e}")
            if frequencies is None:
                frequencies = np.array([1, self.sample_rate/2])
            return frequencies, np.ones_like(frequencies), np.zeros_like(frequencies)

    def validate_filter_performance(self, test_signal: np.ndarray) -> Dict[str, Any]:
        """
        Validate filter performance using a test signal.

        Args:
            test_signal: Test signal for validation

        Returns:
            Validation results dictionary
        """
        try:
            if self.filter_type == 'none':
                return {
                    'valid': True,
                    'passthrough': True,
                    'attenuation_db': 0,
                    'phase_delay': 0,
                    'distortion': 0
                }

            # Apply filter to test signal
            filtered_signal, _ = self.apply_filter(test_signal)

            # Calculate performance metrics
            original_power = np.mean(test_signal**2)
            filtered_power = np.mean(filtered_signal**2)

            # Attenuation in dB
            if original_power > 0:
                attenuation_db = 10 * np.log10(filtered_power / original_power)
            else:
                attenuation_db = -float('inf')

            # Phase delay estimation (using cross-correlation)
            correlation = np.correlate(filtered_signal, test_signal, mode='full')
            delay_samples = np.argmax(correlation) - len(test_signal) + 1
            phase_delay = delay_samples / self.sample_rate

            # Distortion measurement (difference between original and filtered)
            min_len = min(len(test_signal), len(filtered_signal))
            signal_diff = test_signal[:min_len] - filtered_signal[:min_len]
            distortion = np.sqrt(np.mean(signal_diff**2))

            # Overall validity check
            valid = (
                np.abs(attenuation_db) < 50 and  # Reasonable attenuation
                np.abs(phase_delay) < 0.1 and    # Small phase delay
                distortion < np.std(test_signal) * 2  # Reasonable distortion
            )

            return {
                'valid': valid,
                'passthrough': False,
                'attenuation_db': attenuation_db,
                'phase_delay': phase_delay,
                'distortion': distortion,
                'original_power': original_power,
                'filtered_power': filtered_power
            }

        except Exception as e:
            self.logger.error(f"Filter validation failed: {e}")
            return {
                'valid': False,
                'error': str(e)
            }

    def reset_filter_state(self):
        """Reset the filter state for new data session."""
        if self.filter_coefficients is not None:
            self.filter_state = signal.sosfilt_zi(self.filter_coefficients)
            self.logger.info("Filter state reset")

    def get_filter_info(self) -> Dict[str, Any]:
        """
        Get current filter configuration information.

        Returns:
            Filter configuration dictionary
        """
        return {
            'type': self.filter_type,
            'cutoff_freq': self.cutoff_freq,
            'order': self.filter_order,
            'sample_rate': self.sample_rate,
            'active': self.filter_coefficients is not None,
            'total_samples_processed': self.total_samples_processed,
            'average_processing_time': (
                self.total_processing_time / max(1, self.total_samples_processed / 1000)
            ),
            'last_filter_time': self.last_filter_time
        }

    def _get_filter_stats(self, start_time: float, samples_count: int, error: str = None) -> Dict[str, Any]:
        """
        Generate filtering statistics.

        Args:
            start_time: Processing start time
            samples_count: Number of samples processed
            error: Error message if any

        Returns:
            Statistics dictionary
        """
        processing_time = time.time() - start_time

        stats = {
            'processing_time': processing_time,
            'samples_processed': samples_count,
            'throughput_msamples_per_sec': samples_count / max(1e-6, processing_time) / 1e6,
            'filter_type': self.filter_type,
            'error': error
        }

        return stats


class FilterBank:
    """
    Bank of multiple filters for parallel processing.

    This class allows applying multiple filters simultaneously
    for comparison or multi-band analysis.
    """

    def __init__(self, sample_rate: float = 1000000.0):
        """
        Initialize the filter bank.

        Args:
            sample_rate: Sampling rate in Hz
        """
        self.sample_rate = sample_rate
        self.filters: Dict[str, SignalFilter] = {}
        self.logger = logging.getLogger(__name__ + '.FilterBank')

    def add_filter(self, name: str, filter_type: str, cutoff_freq, order: int = 4) -> bool:
        """
        Add a filter to the bank.

        Args:
            name: Filter name/identifier
            filter_type: Type of filter
            cutoff_freq: Cutoff frequency/frequencies
            order: Filter order

        Returns:
            True if filter added successfully
        """
        try:
            filter_obj = SignalFilter(self.sample_rate)
            success = filter_obj.design_filter(filter_type, cutoff_freq, order)

            if success:
                self.filters[name] = filter_obj
                self.logger.info(f"Added filter '{name}' to filter bank")
                return True
            else:
                self.logger.error(f"Failed to add filter '{name}'")
                return False

        except Exception as e:
            self.logger.error(f"Error adding filter '{name}': {e}")
            return False

    def remove_filter(self, name: str):
        """Remove a filter from the bank."""
        if name in self.filters:
            del self.filters[name]
            self.logger.info(f"Removed filter '{name}' from filter bank")

    def apply_all_filters(self, data: np.ndarray) -> Dict[str, Tuple[np.ndarray, Dict]]:
        """
        Apply all filters in the bank to input data.

        Args:
            data: Input signal data

        Returns:
            Dictionary with filter results keyed by filter names
        """
        results = {}

        for name, filter_obj in self.filters.items():
            try:
                filtered_data, stats = filter_obj.apply_filter(data)
                results[name] = (filtered_data, stats)
            except Exception as e:
                self.logger.error(f"Error applying filter '{name}': {e}")
                results[name] = (data.copy(), {'error': str(e)})

        return results

    def get_filter_names(self) -> list:
        """Get list of filter names in the bank."""
        return list(self.filters.keys())

    def get_filter(self, name: str) -> Optional[SignalFilter]:
        """Get a specific filter by name."""
        return self.filters.get(name)