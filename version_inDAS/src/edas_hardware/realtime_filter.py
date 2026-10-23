"""
Realtime filtering helpers for display-only data streams.

The filter in this module is intentionally stateful and causal. It is designed
for short GUI update blocks that together form one continuous stream, so it
uses scipy.signal.sosfilt and carries the IIR state across blocks instead of
filtering each display window independently.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple

import numpy as np
from scipy import signal


class FilterSpecError(ValueError):
    """Raised when a filter specification cannot be parsed or designed."""


@dataclass(frozen=True)
class FilterSpec:
    """Parsed cutoff settings for one realtime temporal filter."""

    kind: str
    low_hz: Optional[float] = None
    high_hz: Optional[float] = None
    text: str = ""

    @property
    def cutoff_tuple(self) -> Tuple[float, ...]:
        if self.kind == "highpass":
            return (float(self.low_hz),)
        if self.kind == "lowpass":
            return (float(self.high_hz),)
        return (float(self.low_hz), float(self.high_hz))


def _parse_positive_float(value: str, field_name: str) -> float:
    try:
        parsed = float(value)
    except ValueError as exc:
        raise FilterSpecError(f"{field_name} must be a number.") from exc
    if parsed <= 0.0:
        raise FilterSpecError(f"{field_name} must be greater than 0 Hz.")
    return parsed


def parse_filter_spec(text: str) -> FilterSpec:
    """
    Parse filter text such as ``1-``, ``-10`` or ``2-10``.

    Returns:
        FilterSpec for high-pass, low-pass or band-pass filtering.
    """
    spec_text = (text or "").strip().replace(" ", "")
    if not spec_text:
        raise FilterSpecError("Filter parameter is empty.")
    if spec_text.count("-") != 1:
        raise FilterSpecError("Use 1-, -10, or 2-10 format.")

    left, right = spec_text.split("-", 1)
    if left and not right:
        low_hz = _parse_positive_float(left, "High-pass cutoff")
        return FilterSpec(kind="highpass", low_hz=low_hz, text=spec_text)
    if right and not left:
        high_hz = _parse_positive_float(right, "Low-pass cutoff")
        return FilterSpec(kind="lowpass", high_hz=high_hz, text=spec_text)
    if left and right:
        low_hz = _parse_positive_float(left, "Band-pass low cutoff")
        high_hz = _parse_positive_float(right, "Band-pass high cutoff")
        if low_hz >= high_hz:
            raise FilterSpecError("Band-pass low cutoff must be below high cutoff.")
        return FilterSpec(kind="bandpass", low_hz=low_hz, high_hz=high_hz, text=spec_text)

    raise FilterSpecError("Use 1-, -10, or 2-10 format.")


class RealtimeTimeAxisFilter:
    """Vectorized IIR filter for frame x position display blocks."""

    def __init__(self, order: int = 2):
        self._order = max(1, int(order))
        self._spec: Optional[FilterSpec] = None
        self._sample_rate_hz = 0.0
        self._sos: Optional[np.ndarray] = None
        self._zi: Optional[np.ndarray] = None
        self._column_count = 0

    def reset_state(self):
        """Drop only the streaming state, keeping the current filter design."""
        self._zi = None
        self._column_count = 0

    def reset_design(self):
        """Drop both filter coefficients and streaming state."""
        self._spec = None
        self._sample_rate_hz = 0.0
        self._sos = None
        self.reset_state()

    def _design_filter(self, spec: FilterSpec, sample_rate_hz: float) -> np.ndarray:
        sample_rate_hz = float(sample_rate_hz)
        if sample_rate_hz <= 0.0:
            raise FilterSpecError("Sample rate must be greater than 0 Hz.")

        nyquist_hz = sample_rate_hz * 0.5
        if spec.kind == "highpass":
            cutoff = float(spec.low_hz)
            if cutoff >= nyquist_hz:
                raise FilterSpecError("High-pass cutoff must be below Nyquist frequency.")
            return signal.butter(
                self._order,
                cutoff,
                btype="highpass",
                fs=sample_rate_hz,
                output="sos",
            )

        if spec.kind == "lowpass":
            cutoff = float(spec.high_hz)
            if cutoff >= nyquist_hz:
                raise FilterSpecError("Low-pass cutoff must be below Nyquist frequency.")
            return signal.butter(
                self._order,
                cutoff,
                btype="lowpass",
                fs=sample_rate_hz,
                output="sos",
            )

        low_hz = float(spec.low_hz)
        high_hz = float(spec.high_hz)
        if high_hz >= nyquist_hz:
            raise FilterSpecError("Band-pass high cutoff must be below Nyquist frequency.")
        return signal.butter(
            self._order,
            (low_hz, high_hz),
            btype="bandpass",
            fs=sample_rate_hz,
            output="sos",
        )

    def configure(self, spec: FilterSpec, sample_rate_hz: float):
        """Ensure the current filter design matches the requested spec and rate."""
        sample_rate_hz = float(sample_rate_hz)
        if self._spec == spec and abs(self._sample_rate_hz - sample_rate_hz) <= 1e-12:
            return

        self._sos = self._design_filter(spec, sample_rate_hz)
        self._spec = spec
        self._sample_rate_hz = sample_rate_hz
        self.reset_state()

    def _initialize_state(self, first_sample: np.ndarray):
        if self._sos is None:
            raise FilterSpecError("Filter has not been configured.")
        first_sample = np.asarray(first_sample, dtype=np.float64)
        zi_base = signal.sosfilt_zi(self._sos)
        self._zi = zi_base[:, :, None] * first_sample[None, None, :]
        self._column_count = int(first_sample.size)

    def process_channels_by_time(self, data_block: np.ndarray, spec: FilterSpec, sample_rate_hz: float) -> np.ndarray:
        """Same filter/state for channel-first blocks; keep time contiguous.

        Avoid transposing large shared matrices into a strided time-major
        working layout. The state remains compatible with process().
        """
        if data_block.ndim != 2:
            raise FilterSpecError('Realtime filter input must be a 2D array.')
        if data_block.size == 0:
            return np.asarray(data_block, dtype=np.float64)
        self.configure(spec, sample_rate_hz)
        data_float = np.ascontiguousarray(data_block, dtype=np.float64)
        if self._zi is None or self._column_count != data_float.shape[0]:
            self._initialize_state(data_float[:, 0])
        filtered, zi = signal.sosfilt(self._sos, data_float, axis=1,
                                    zi=self._zi.transpose(0, 2, 1))
        self._zi = zi.transpose(0, 2, 1)
        return filtered

    def process(self, data_block: np.ndarray, spec: FilterSpec, sample_rate_hz: float) -> np.ndarray:
        """
        Filter a ``frames x positions`` block along the time axis.

        The returned array is always a new float64 array, so callers can pass
        views into larger display buffers without risking in-place mutation.
        """
        if data_block.ndim != 2:
            raise FilterSpecError("Realtime filter input must be a 2D array.")
        if data_block.size == 0:
            return np.asarray(data_block, dtype=np.float64)

        self.configure(spec, sample_rate_hz)
        data_float = np.asarray(data_block, dtype=np.float64)
        column_count = data_float.shape[1]
        if self._zi is None or self._column_count != column_count:
            self._initialize_state(data_float[0, :])

        filtered, self._zi = signal.sosfilt(self._sos, data_float, axis=0, zi=self._zi)
        return np.ascontiguousarray(filtered)
