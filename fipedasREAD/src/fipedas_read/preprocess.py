"""Lightweight preprocessing helpers for offline replay."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
from scipy.signal import butter, sosfiltfilt


@dataclass(frozen=True)
class FilterSpec:
    enabled: bool = False
    low_hz: Optional[float] = 500.0
    high_hz: Optional[float] = 6000.0
    order: int = 4


@dataclass(frozen=True)
class PreprocessSpec:
    remove_mean: bool = True
    normalize: bool = False
    downsample: int = 1
    filter_spec: FilterSpec = FilterSpec()


def finite_1d(values: np.ndarray) -> np.ndarray:
    arr = np.asarray(values, dtype=np.float64).reshape(-1)
    if arr.size == 0:
        return arr
    finite = np.isfinite(arr)
    if np.all(finite):
        return arr
    if not np.any(finite):
        return np.zeros_like(arr)
    repaired = arr.copy()
    repaired[~finite] = np.nanmedian(repaired[finite])
    return repaired


def parse_band_text(text: str) -> tuple[Optional[float], Optional[float]]:
    """Parse strings such as '500-6000', '100-', '-1000', or '500'."""
    stripped = (text or "").strip()
    if not stripped:
        return None, None
    if "-" not in stripped:
        value = float(stripped)
        return value, None
    left, right = stripped.split("-", 1)
    low = float(left) if left.strip() else None
    high = float(right) if right.strip() else None
    return low, high


def _build_sos(sample_rate_hz: float, spec: FilterSpec) -> Optional[np.ndarray]:
    if not spec.enabled or sample_rate_hz <= 0:
        return None
    nyquist = sample_rate_hz * 0.5
    order = max(1, min(10, int(spec.order)))
    low = spec.low_hz
    high = spec.high_hz
    try:
        if low is not None and high is not None:
            low = max(0.1, min(float(low), nyquist * 0.95))
            high = max(low + 0.1, min(float(high), nyquist * 0.98))
            return butter(order, [low, high], btype="bandpass", fs=sample_rate_hz, output="sos")
        if low is not None:
            low = max(0.1, min(float(low), nyquist * 0.95))
            return butter(order, low, btype="highpass", fs=sample_rate_hz, output="sos")
        if high is not None:
            high = max(0.1, min(float(high), nyquist * 0.98))
            return butter(order, high, btype="lowpass", fs=sample_rate_hz, output="sos")
    except Exception:
        return None
    return None


def _apply_filter(values: np.ndarray, sample_rate_hz: float, spec: FilterSpec) -> np.ndarray:
    if values.size < 8:
        return values
    sos = _build_sos(sample_rate_hz, spec)
    if sos is None:
        return values
    try:
        return np.asarray(sosfiltfilt(sos, values), dtype=np.float64)
    except Exception:
        return values


def preprocess_space_matrix(
    matrix: np.ndarray,
    sample_rate_hz: float,
    spec: PreprocessSpec,
) -> np.ndarray:
    """Apply remove-mean / bandpass / normalize to a space-time matrix (row = channel)."""
    arr = np.asarray(matrix, dtype=np.float32)
    if arr.ndim != 2 or arr.size == 0:
        return arr
    if spec.remove_mean:
        arr = arr - np.nanmean(arr, axis=1, keepdims=True)
    sos = _build_sos(sample_rate_hz, spec.filter_spec)
    if sos is not None and arr.shape[1] >= 8:
        try:
            if np.isfinite(arr).all():
                arr = np.asarray(sosfiltfilt(sos, arr, axis=1), dtype=np.float32)
            else:
                out = np.empty_like(arr)
                for i in range(arr.shape[0]):
                    row = arr[i]
                    out[i] = sosfiltfilt(sos, row) if np.isfinite(row).all() else row
                arr = out
        except Exception:
            pass
    if spec.normalize and arr.size:
        peak = float(np.nanmax(np.abs(arr)))
        if peak > 0:
            arr = arr / peak
    return np.asarray(arr, dtype=np.float32)


def preprocess_waveform(
    values: np.ndarray,
    sample_rate_hz: float,
    spec: PreprocessSpec,
) -> tuple[np.ndarray, np.ndarray, float]:
    """Return time, processed values, and effective sample rate."""
    arr = finite_1d(values)
    rate = float(sample_rate_hz) if np.isfinite(sample_rate_hz) and sample_rate_hz > 0 else 1.0
    if arr.size == 0:
        return np.array([], dtype=np.float64), arr, rate
    if spec.remove_mean:
        arr = arr - float(np.mean(arr))
    arr = _apply_filter(arr, rate, spec.filter_spec)
    step = max(1, int(spec.downsample))
    if step > 1:
        arr = arr[::step]
        rate = rate / step
    if spec.normalize and arr.size:
        peak = float(np.nanmax(np.abs(arr)))
        if peak > 0:
            arr = arr / peak
    times = np.arange(arr.size, dtype=np.float64) / rate
    return times, np.asarray(arr, dtype=np.float64), rate


def decimate_for_plot(
    times: np.ndarray,
    values: np.ndarray,
    max_points: int,
) -> tuple[np.ndarray, np.ndarray]:
    count = min(int(times.size), int(values.size))
    if count <= 0:
        return np.array([], dtype=np.float64), np.array([], dtype=np.float64)
    step = max(1, int(np.ceil(count / max(1, int(max_points)))))
    return times[:count:step], values[:count:step]


def robust_levels(matrix: np.ndarray) -> tuple[float, float]:
    if matrix.size == 0:
        return -1.0, 1.0
    finite = matrix[np.isfinite(matrix)]
    if finite.size == 0:
        return -1.0, 1.0
    lo, hi = np.percentile(finite, [2.0, 98.0])
    if not np.isfinite(lo) or not np.isfinite(hi) or lo == hi:
        center = float(np.nanmedian(finite))
        radius = max(float(np.nanstd(finite)), 1e-6)
        return center - radius, center + radius
    return float(lo), float(hi)
