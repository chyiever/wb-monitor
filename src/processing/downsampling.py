"""
Downsampling Module for PCCP Monitoring System

This module provides various downsampling algorithms for reducing data rate
while preserving signal characteristics. Supports decimation and averaging
methods with anti-aliasing filtering.

Features:
- Multiple downsampling methods (decimation, averaging)
- Anti-aliasing filtering for high-quality downsampling
- Configurable downsampling ratios
- Performance monitoring and validation

Author: Claude
Date: 2026-03-11
"""

import logging
import numpy as np
from typing import Tuple, Dict, Any, Optional
from scipy import signal
import time


class Downsampler:
    """
    Signal downsampling processor with multiple algorithms.

    This class provides various downsampling methods for reducing data rate
    in real-time processing while maintaining signal quality.
    """

    # Downsampling method constants
    METHODS = ['decimate', 'average', 'subsample']

    def __init__(self, method: str = 'decimate', factor: int = 1):
        """
        Initialize the downsampler.

        Args:
            method: Downsampling method ('decimate', 'average', 'subsample')
            factor: Downsampling factor (must be positive integer)
        """
        self.logger = logging.getLogger(__name__ + '.Downsampler')

        # Validate parameters
        if method not in self.METHODS:
            raise ValueError(f"Unsupported downsampling method: {method}")

        if not isinstance(factor, int) or factor < 1:
            raise ValueError("Downsampling factor must be a positive integer")

        self.method = method
        self.factor = factor

        # Anti-aliasing filter for decimation
        self._aa_filter = None
        self._filter_state = None

        # Performance statistics
        self.total_input_samples = 0
        self.total_output_samples = 0
        self.total_processing_time = 0
        self.last_process_time = 0

        # Initialize anti-aliasing filter if needed
        if self.method == 'decimate' and self.factor > 1:
            self._design_antialiasing_filter()

    def _design_antialiasing_filter(self):
        """Design anti-aliasing filter for decimation."""
        try:
            # Design a lowpass filter with cutoff at Nyquist/factor
            # Use Butterworth filter with order based on factor
            order = min(8, max(2, self.factor))
            cutoff = 0.8 / self.factor  # Normalized frequency (0.8 for safety margin)

            self._aa_filter = signal.butter(
                order,
                cutoff,
                btype='low',
                analog=False,
                output='sos'
            )

            self._filter_state = signal.sosfilt_zi(self._aa_filter)

            self.logger.info(
                f"Designed anti-aliasing filter: order={order}, "
                f"cutoff={cutoff:.3f} (normalized)"
            )

        except Exception as e:
            self.logger.error(f"Failed to design anti-aliasing filter: {e}")
            self._aa_filter = None
            self._filter_state = None

    def downsample(self, data: np.ndarray) -> Tuple[np.ndarray, Dict[str, Any]]:
        """
        Apply downsampling to input data.

        Args:
            data: Input signal data

        Returns:
            Tuple of (downsampled_data, statistics)
        """
        start_time = time.time()

        try:
            # Validate input
            if not isinstance(data, np.ndarray):
                data = np.array(data)

            if len(data) == 0:
                return np.array([]), self._get_stats(start_time, 0, 0)

            if self.factor == 1:
                # No downsampling needed
                return data.copy(), self._get_stats(start_time, len(data), len(data))

            # Apply downsampling based on method
            if self.method == 'decimate':
                downsampled = self._decimate(data)
            elif self.method == 'average':
                downsampled = self._average_downsample(data)
            elif self.method == 'subsample':
                downsampled = self._subsample(data)
            else:
                raise ValueError(f"Unknown downsampling method: {self.method}")

            # Update statistics
            self.total_input_samples += len(data)
            self.total_output_samples += len(downsampled)
            self.last_process_time = time.time() - start_time
            self.total_processing_time += self.last_process_time

            return downsampled, self._get_stats(start_time, len(data), len(downsampled))

        except Exception as e:
            self.logger.error(f"Downsampling failed: {e}")
            return data.copy(), self._get_stats(start_time, len(data), len(data), error=str(e))

    def _decimate(self, data: np.ndarray) -> np.ndarray:
        """
        Decimate data with anti-aliasing filtering.

        Args:
            data: Input data

        Returns:
            Decimated data
        """
        try:
            if self._aa_filter is not None:
                # Apply anti-aliasing filter
                filtered_data, self._filter_state = signal.sosfilt(
                    self._aa_filter,
                    data,
                    zi=self._filter_state
                )
            else:
                filtered_data = data

            # Subsample
            decimated = filtered_data[::self.factor]

            return decimated

        except Exception as e:
            self.logger.error(f"Decimation failed: {e}")
            # Fallback to simple subsampling
            return data[::self.factor]

    def _average_downsample(self, data: np.ndarray) -> np.ndarray:
        """
        Downsample by averaging consecutive samples.

        Args:
            data: Input data

        Returns:
            Averaged downsampled data
        """
        # Calculate number of complete blocks
        n_blocks = len(data) // self.factor

        if n_blocks == 0:
            return np.array([])

        # Reshape data into blocks and average
        reshaped = data[:n_blocks * self.factor].reshape(n_blocks, self.factor)
        averaged = np.mean(reshaped, axis=1)

        return averaged

    def _subsample(self, data: np.ndarray) -> np.ndarray:
        """
        Simple subsampling without filtering.

        Args:
            data: Input data

        Returns:
            Subsampled data
        """
        return data[::self.factor]

    def set_downsampling_factor(self, factor: int) -> bool:
        """
        Change the downsampling factor.

        Args:
            factor: New downsampling factor

        Returns:
            True if factor changed successfully
        """
        try:
            if not isinstance(factor, int) or factor < 1:
                raise ValueError("Downsampling factor must be a positive integer")

            if factor == self.factor:
                return True  # No change needed

            self.factor = factor

            # Redesign anti-aliasing filter if needed
            if self.method == 'decimate':
                self._design_antialiasing_filter()

            self.logger.info(f"Downsampling factor changed to {factor}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to change downsampling factor: {e}")
            return False

    def get_current_factor(self) -> int:
        """
        Get the current downsampling factor.

        Returns:
            Current downsampling factor
        """
        return self.factor

    def set_method(self, method: str) -> bool:
        """
        Change the downsampling method.

        Args:
            method: New downsampling method

        Returns:
            True if method changed successfully
        """
        try:
            if method not in self.METHODS:
                raise ValueError(f"Unsupported downsampling method: {method}")

            if method == self.method:
                return True  # No change needed

            self.method = method

            # Design anti-aliasing filter if switching to decimation
            if method == 'decimate' and self.factor > 1:
                self._design_antialiasing_filter()
            else:
                self._aa_filter = None
                self._filter_state = None

            self.logger.info(f"Downsampling method changed to {method}")
            return True

        except Exception as e:
            self.logger.error(f"Failed to change downsampling method: {e}")
            return False

    def reset_state(self):
        """Reset the downsampler state for new data session."""
        if self._filter_state is not None and self._aa_filter is not None:
            self._filter_state = signal.sosfilt_zi(self._aa_filter)

        # Reset statistics
        self.total_input_samples = 0
        self.total_output_samples = 0
        self.total_processing_time = 0
        self.last_process_time = 0

        self.logger.info("Downsampler state reset")

    def get_effective_sample_rate(self, input_sample_rate: float) -> float:
        """
        Calculate the effective output sample rate.

        Args:
            input_sample_rate: Input sample rate in Hz

        Returns:
            Output sample rate in Hz
        """
        return input_sample_rate / self.factor

    def estimate_output_length(self, input_length: int) -> int:
        """
        Estimate output length for given input length.

        Args:
            input_length: Input data length

        Returns:
            Estimated output data length
        """
        if self.method == 'average':
            return input_length // self.factor
        else:
            return (input_length + self.factor - 1) // self.factor

    def validate_downsampling(self, original: np.ndarray, downsampled: np.ndarray,
                            input_sample_rate: float) -> Dict[str, Any]:
        """
        Validate downsampling quality.

        Args:
            original: Original signal data
            downsampled: Downsampled signal data
            input_sample_rate: Input sample rate in Hz

        Returns:
            Validation results dictionary
        """
        try:
            # Check length ratio
            expected_length = self.estimate_output_length(len(original))
            length_ratio = len(downsampled) / expected_length

            # Calculate signal preservation metrics
            if self.method == 'average' and len(downsampled) > 0:
                # For averaging, reconstruct signal for comparison
                reconstructed = np.repeat(downsampled, self.factor)[:len(original)]
                mse = np.mean((original - reconstructed) ** 2)
                snr_db = 10 * np.log10(np.var(original) / max(mse, 1e-12))
            else:
                # For other methods, compare at downsampled points
                original_subsampled = original[::self.factor]
                min_len = min(len(original_subsampled), len(downsampled))

                if min_len > 0:
                    mse = np.mean((original_subsampled[:min_len] - downsampled[:min_len]) ** 2)
                    snr_db = 10 * np.log10(np.var(original_subsampled[:min_len]) / max(mse, 1e-12))
                else:
                    mse = 0
                    snr_db = float('inf')

            # Check for aliasing (simplified)
            output_nyquist = input_sample_rate / (2 * self.factor)
            aliasing_risk = output_nyquist < (input_sample_rate / 4)  # Heuristic

            # Overall quality score
            quality_score = min(1.0, snr_db / 40.0) * (1.0 if not aliasing_risk else 0.7)

            valid = (
                0.95 <= length_ratio <= 1.05 and  # Correct length
                snr_db > 20 and  # Good signal preservation
                quality_score > 0.7  # Overall quality
            )

            return {
                'valid': valid,
                'length_ratio': length_ratio,
                'mse': mse,
                'snr_db': snr_db,
                'aliasing_risk': aliasing_risk,
                'quality_score': quality_score,
                'output_sample_rate': self.get_effective_sample_rate(input_sample_rate)
            }

        except Exception as e:
            return {
                'valid': False,
                'error': str(e)
            }

    def get_downsampler_info(self) -> Dict[str, Any]:
        """
        Get current downsampler configuration and statistics.

        Returns:
            Downsampler information dictionary
        """
        return {
            'method': self.method,
            'factor': self.factor,
            'has_antialiasing': self._aa_filter is not None,
            'total_input_samples': self.total_input_samples,
            'total_output_samples': self.total_output_samples,
            'compression_ratio': (
                self.total_input_samples / max(1, self.total_output_samples)
            ),
            'average_processing_time_per_sample': (
                self.total_processing_time / max(1, self.total_input_samples)
            ),
            'last_process_time': self.last_process_time
        }

    def _get_stats(self, start_time: float, input_count: int, output_count: int,
                  error: str = None) -> Dict[str, Any]:
        """
        Generate processing statistics.

        Args:
            start_time: Processing start time
            input_count: Number of input samples
            output_count: Number of output samples
            error: Error message if any

        Returns:
            Statistics dictionary
        """
        processing_time = time.time() - start_time

        return {
            'processing_time': processing_time,
            'input_samples': input_count,
            'output_samples': output_count,
            'compression_ratio': input_count / max(1, output_count),
            'throughput_msamples_per_sec': input_count / max(1e-6, processing_time) / 1e6,
            'method': self.method,
            'factor': self.factor,
            'error': error
        }

