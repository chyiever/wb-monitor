"""
Phase Unwrapping Module for PCCP Monitoring System

This module provides phase unwrapping algorithms to convert wrapped phase data
from FPGA demodulation (-1 to 1 range) into continuous unwrapped phase values.

The implementation uses numpy's unwrap function with additional validation
and error handling for robust real-time processing.

Author: Claude
Date: 2026-03-11
"""

import logging
import numpy as np
from typing import Optional, Tuple
import time


class PhaseUnwrapper:
    """
    Phase unwrapping processor for fiber interferometer signals.

    This class converts wrapped phase data from FPGA demodulation into
    continuous phase values using the phase difference method.
    """

    def __init__(self):
        """Initialize the phase unwrapper."""
        self.logger = logging.getLogger(__name__ + '.PhaseUnwrapper')

        # Processing statistics
        self.total_processed = 0
        self.total_discontinuities = 0
        self.last_process_time = 0

        # Previous phase for continuity checking
        self._last_phase = None

    def unwrap_phase(self, wrapped_phase: np.ndarray) -> Tuple[np.ndarray, dict]:
        """
        Unwrap phase data from [-1, 1] to continuous phase values.

        This method:
        1. Maps [-1, 1] to [-π, π] by multiplying by π
        2. Applies numpy's unwrap algorithm
        3. Ensures continuity with previous data segments

        Args:
            wrapped_phase: Input wrapped phase data in [-1, 1] range

        Returns:
            Tuple of (unwrapped_phase, statistics)
            - unwrapped_phase: Continuous phase values in radians
            - statistics: Processing statistics dictionary
        """
        start_time = time.time()

        try:
            # Validate input data
            if not isinstance(wrapped_phase, np.ndarray):
                wrapped_phase = np.array(wrapped_phase)

            if len(wrapped_phase) == 0:
                return np.array([]), self._get_statistics(start_time, 0)

            # Check data range
            if np.any(np.abs(wrapped_phase) > 1.1):  # Allow small tolerance
                self.logger.warning(
                    f"Input data outside expected range [-1, 1]: "
                    f"min={np.min(wrapped_phase):.3f}, max={np.max(wrapped_phase):.3f}"
                )

            # Map to [-π, π] range
            phase_rad = wrapped_phase * np.pi

            # Apply phase unwrapping
            unwrapped = np.unwrap(phase_rad, discont=np.pi)

            # Ensure continuity with previous segment
            if self._last_phase is not None:
                phase_offset = self._calculate_continuity_offset(unwrapped[0])
                unwrapped += phase_offset

            # Update last phase for next segment
            if len(unwrapped) > 0:
                self._last_phase = unwrapped[-1]

            # Count discontinuities (for statistics)
            discontinuities = self._count_discontinuities(phase_rad, unwrapped)
            self.total_discontinuities += discontinuities

            # Update statistics
            self.total_processed += len(wrapped_phase)
            self.last_process_time = time.time() - start_time

            return unwrapped, self._get_statistics(start_time, discontinuities)

        except Exception as e:
            self.logger.error(f"Error in phase unwrapping: {e}")
            return np.array([]), self._get_statistics(start_time, 0, error=str(e))

    def _calculate_continuity_offset(self, first_phase: float) -> float:
        """
        Calculate phase offset to maintain continuity between segments.

        Args:
            first_phase: First phase value of current segment

        Returns:
            Phase offset to add to current segment
        """
        if self._last_phase is None:
            return 0.0

        # Calculate the difference
        phase_diff = first_phase - self._last_phase

        # Find the multiple of 2π that minimizes the discontinuity
        n_2pi = np.round(phase_diff / (2 * np.pi))
        offset = -n_2pi * 2 * np.pi

        return offset

    def _count_discontinuities(self, wrapped: np.ndarray, unwrapped: np.ndarray) -> int:
        """
        Count the number of phase discontinuities that were unwrapped.

        Args:
            wrapped: Original wrapped phase data
            unwrapped: Unwrapped phase data

        Returns:
            Number of discontinuities found and corrected
        """
        try:
            if len(wrapped) <= 1:
                return 0

            # Calculate phase differences
            wrapped_diff = np.diff(wrapped)

            # Count jumps greater than π
            large_jumps = np.abs(wrapped_diff) > np.pi * 0.8  # Use 80% threshold for robustness

            return np.sum(large_jumps)

        except Exception:
            return 0

    def _get_statistics(self, start_time: float, discontinuities: int, error: str = None) -> dict:
        """
        Generate processing statistics.

        Args:
            start_time: Processing start timestamp
            discontinuities: Number of discontinuities in this processing
            error: Error message if any

        Returns:
            Statistics dictionary
        """
        processing_time = time.time() - start_time

        stats = {
            'processing_time': processing_time,
            'total_processed': self.total_processed,
            'total_discontinuities': self.total_discontinuities,
            'current_discontinuities': discontinuities,
            'last_phase': self._last_phase,
            'error': error
        }

        return stats

    def reset(self):
        """Reset the unwrapper state for new data session."""
        self._last_phase = None
        self.total_processed = 0
        self.total_discontinuities = 0
        self.last_process_time = 0

        self.logger.info("Phase unwrapper reset")

    def get_status(self) -> dict:
        """
        Get current unwrapper status.

        Returns:
            Status dictionary with processing information
        """
        return {
            'initialized': True,
            'last_phase': self._last_phase,
            'total_processed': self.total_processed,
            'total_discontinuities': self.total_discontinuities,
            'last_process_time': self.last_process_time,
            'average_discontinuities_per_segment': (
                self.total_discontinuities / max(1, self.total_processed // 200000)
            )
        }


def validate_phase_unwrapping(wrapped: np.ndarray, unwrapped: np.ndarray) -> dict:
    """
    Validate the quality of phase unwrapping results.

    This function checks for common unwrapping artifacts and provides
    quality metrics for the unwrapped phase data.

    Args:
        wrapped: Original wrapped phase data
        unwrapped: Unwrapped phase data

    Returns:
        Validation results dictionary
    """
    try:
        if len(wrapped) != len(unwrapped) or len(wrapped) == 0:
            return {'valid': False, 'error': 'Length mismatch or empty data'}

        # Re-wrap the unwrapped phase to check consistency
        rewrapped = np.mod(unwrapped + np.pi, 2 * np.pi) - np.pi
        original_wrapped = wrapped * np.pi

        # Calculate RMS error between original and re-wrapped
        rms_error = np.sqrt(np.mean((rewrapped - original_wrapped) ** 2))

        # Check for phase continuity
        phase_diff = np.diff(unwrapped)
        max_phase_jump = np.max(np.abs(phase_diff)) if len(phase_diff) > 0 else 0

        # Calculate phase range and variation
        phase_range = np.max(unwrapped) - np.min(unwrapped)
        phase_std = np.std(unwrapped)

        # Check for NaN or infinite values
        has_invalid = np.any(~np.isfinite(unwrapped))

        # Determine overall validity
        valid = (
            rms_error < 0.1 and  # Low reconstruction error
            max_phase_jump < np.pi and  # No large jumps
            not has_invalid  # No invalid values
        )

        results = {
            'valid': valid,
            'rms_error': rms_error,
            'max_phase_jump': max_phase_jump,
            'phase_range': phase_range,
            'phase_std': phase_std,
            'has_invalid_values': has_invalid,
            'quality_score': max(0, 1.0 - rms_error - max_phase_jump / np.pi)
        }

        return results

    except Exception as e:
        return {'valid': False, 'error': str(e)}

