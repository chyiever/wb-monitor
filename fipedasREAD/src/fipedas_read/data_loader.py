"""Reader for wb-monitor FIP/eDAS joint NPZ files."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import numpy as np


@dataclass
class JointReplayData:
    path: Path
    format_version: str
    created_at: str
    comm_counts: np.ndarray
    packet_start_times: np.ndarray
    packet_duration_seconds: np.ndarray
    fip1_frames: list[np.ndarray] = field(default_factory=list)
    fip2_frames: list[np.ndarray] = field(default_factory=list)
    das_frames: list[np.ndarray] = field(default_factory=list)
    fip_rates_hz: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.float64))
    das_rates_hz: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.float64))
    das_channel_counts: np.ndarray = field(default_factory=lambda: np.array([], dtype=np.int32))
    fip_present: np.ndarray = field(default_factory=lambda: np.array([], dtype=bool))
    das_present: np.ndarray = field(default_factory=lambda: np.array([], dtype=bool))

    @property
    def frame_count(self) -> int:
        return int(max(len(self.fip1_frames), len(self.das_frames), self.comm_counts.size))

    @property
    def start_time(self) -> float:
        if self.packet_start_times.size:
            return float(np.nanmin(self.packet_start_times))
        return 0.0

    @property
    def end_time(self) -> float:
        if self.packet_start_times.size and self.packet_duration_seconds.size:
            count = min(self.packet_start_times.size, self.packet_duration_seconds.size)
            return float(np.nanmax(self.packet_start_times[:count] + self.packet_duration_seconds[:count]))
        return 0.0


def iter_joint_npz_files(path: Path) -> list[Path]:
    path = Path(path)
    if path.is_file():
        return [path] if path.suffix.lower() == ".npz" else []
    if not path.exists():
        return []
    preferred = sorted(path.glob("FIPeDAS-*.npz"))
    if preferred:
        return preferred
    return sorted(path.glob("*.npz"))


def load_joint_npz(path: Path) -> JointReplayData:
    path = Path(path)
    with np.load(path, allow_pickle=True) as data:
        keys = set(data.files)
        format_version = _read_scalar_string(data, "format_version", "unknown")
        created_at = _read_scalar_string(data, "created_at", "")
        comm_counts = _read_array(data, "comm_counts", dtype=np.int32)
        packet_start_times = _read_array(data, "packet_start_times", dtype=np.float64)
        packet_duration_seconds = _read_array(data, "packet_duration_seconds", dtype=np.float64)

        fip1_frames = _read_frame_list(data, _first_key(keys, "fip1_display_data", "fip1_raw_200khz", "fip_display_data", "fip_raw_200khz"))
        fip2_frames = _read_frame_list(data, _first_key(keys, "fip2_display_data", "fip2_raw_200khz"))
        if not fip1_frames and "fip_raw_data" in keys:
            fip1_frames = _read_frame_list(data, "fip_raw_data")
        if not fip1_frames and "fip1_raw_data" in keys:
            fip1_frames = _read_frame_list(data, "fip1_raw_data")
        if not fip2_frames and "fip2_raw_data" in keys:
            fip2_frames = _read_frame_list(data, "fip2_raw_data")

        das_frames = _read_matrix_list(data, _first_key(keys, "das_raw_matrix", "das_matrix", "edas_raw_matrix"))
        fip_rates_hz = _read_array(data, "fip_sample_rate_hz", dtype=np.float64)
        das_rates_hz = _read_array(data, "das_sample_rate_hz", dtype=np.float64)
        das_channel_counts = _read_array(data, "das_channel_count", dtype=np.int32)
        fip_present = _read_array(data, "fip_present", dtype=bool)
        das_present = _read_array(data, "das_present", dtype=bool)

    frame_count = max(len(fip1_frames), len(fip2_frames), len(das_frames), int(comm_counts.size), 1)
    packet_start_times = _fit_numeric_length(packet_start_times, frame_count, fill_mode="sequence")
    packet_duration_seconds = _fit_numeric_length(packet_duration_seconds, frame_count, fill_mode="duration")
    comm_counts = _fit_numeric_length(comm_counts, frame_count, fill_mode="index").astype(np.int32, copy=False)
    fip_rates_hz = _fit_numeric_length(fip_rates_hz, frame_count, fill_mode="nan")
    das_rates_hz = _fit_numeric_length(das_rates_hz, frame_count, fill_mode="nan")
    das_channel_counts = _fit_numeric_length(das_channel_counts, frame_count, fill_mode="zero").astype(np.int32, copy=False)
    fip_present = _fit_bool_length(fip_present, frame_count, default=True if fip1_frames else False)
    das_present = _fit_bool_length(das_present, frame_count, default=True if das_frames else False)

    return JointReplayData(
        path=path,
        format_version=format_version,
        created_at=created_at,
        comm_counts=comm_counts,
        packet_start_times=packet_start_times,
        packet_duration_seconds=packet_duration_seconds,
        fip1_frames=_fit_frame_list(fip1_frames, frame_count),
        fip2_frames=_fit_frame_list(fip2_frames, frame_count),
        das_frames=_fit_matrix_list(das_frames, frame_count),
        fip_rates_hz=fip_rates_hz,
        das_rates_hz=das_rates_hz,
        das_channel_counts=das_channel_counts,
        fip_present=fip_present,
        das_present=das_present,
    )


def concatenate_fip_frames(
    frames: Iterable[np.ndarray],
    starts: np.ndarray,
    durations: np.ndarray,
    rates: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, float]:
    time_parts: list[np.ndarray] = []
    value_parts: list[np.ndarray] = []
    rate_candidates: list[float] = []
    for index, raw_values in enumerate(frames):
        values = np.asarray(raw_values, dtype=np.float64).reshape(-1)
        if values.size == 0:
            continue
        rate = _rate_for_frame(rates, index, values.size, durations)
        if rate <= 0:
            continue
        start = float(starts[index]) if index < starts.size and np.isfinite(starts[index]) else 0.0
        time_parts.append(start + np.arange(values.size, dtype=np.float64) / rate)
        value_parts.append(values)
        rate_candidates.append(rate)
    if not time_parts:
        return np.array([], dtype=np.float64), np.array([], dtype=np.float64), 1.0
    return (
        np.concatenate(time_parts),
        np.concatenate(value_parts),
        float(np.nanmedian(rate_candidates)) if rate_candidates else 1.0,
    )


def concatenate_das_channel(
    frames: Iterable[np.ndarray],
    starts: np.ndarray,
    durations: np.ndarray,
    rates: np.ndarray,
    channel_index: int,
) -> tuple[np.ndarray, np.ndarray, float]:
    time_parts: list[np.ndarray] = []
    value_parts: list[np.ndarray] = []
    rate_candidates: list[float] = []
    for index, matrix in enumerate(frames):
        arr = np.asarray(matrix)
        if arr.ndim != 2 or arr.size == 0:
            continue
        channel = min(max(0, int(channel_index)), arr.shape[0] - 1)
        values = np.asarray(arr[channel, :], dtype=np.float64).reshape(-1)
        if values.size == 0:
            continue
        rate = _rate_for_frame(rates, index, values.size, durations)
        if rate <= 0:
            continue
        start = float(starts[index]) if index < starts.size and np.isfinite(starts[index]) else 0.0
        time_parts.append(start + np.arange(values.size, dtype=np.float64) / rate)
        value_parts.append(values)
        rate_candidates.append(rate)
    if not time_parts:
        return np.array([], dtype=np.float64), np.array([], dtype=np.float64), 1.0
    return (
        np.concatenate(time_parts),
        np.concatenate(value_parts),
        float(np.nanmedian(rate_candidates)) if rate_candidates else 1.0,
    )


def build_space_time_matrix(
    frames: Iterable[np.ndarray],
    starts: np.ndarray,
    durations: np.ndarray,
    rates: np.ndarray,
    channel_start: int,
    channel_end: int,
    time_downsample: int,
    space_downsample: int,
    remove_baseline: bool,
    max_pixels: int = 300_000,
) -> tuple[np.ndarray, tuple[float, float, float, float]]:
    rows: list[np.ndarray] = []
    min_channel = max(0, int(channel_start))
    max_channel = max(min_channel, int(channel_end))
    t0: Optional[float] = None
    t1: Optional[float] = None
    expected_rows: Optional[int] = None
    time_step = max(1, int(time_downsample))
    space_step = max(1, int(space_downsample))
    for index, matrix in enumerate(frames):
        arr = np.asarray(matrix)
        if arr.ndim != 2 or arr.size == 0:
            continue
        start_ch = min(max(min_channel, 0), arr.shape[0] - 1)
        end_ch = min(max(max_channel, start_ch), arr.shape[0] - 1)
        block = np.asarray(arr[start_ch:end_ch + 1:space_step, ::time_step], dtype=np.float32)
        if block.size == 0:
            continue
        if expected_rows is None:
            expected_rows = int(block.shape[0])
        if int(block.shape[0]) != expected_rows:
            continue
        rows.append(block)
        rate = _rate_for_frame(rates, index, arr.shape[1], durations)
        start_time = float(starts[index]) if index < starts.size and np.isfinite(starts[index]) else 0.0
        duration = arr.shape[1] / rate if rate > 0 else _duration_for_frame(durations, index, 0.0)
        t0 = start_time if t0 is None else min(t0, start_time)
        t1 = (start_time + duration) if t1 is None else max(t1, start_time + duration)
    if not rows:
        return np.empty((0, 0), dtype=np.float32), (0.0, 0.0, 1.0, 1.0)
    matrix = np.concatenate(rows, axis=1)
    if matrix.size > max_pixels:
        stride = int(np.ceil(matrix.size / max_pixels))
        matrix = matrix[:, ::max(1, stride)]
    if remove_baseline and matrix.size:
        matrix = matrix - np.nanmedian(matrix, axis=1, keepdims=True)
    x0 = float(t0 or 0.0)
    x1 = float(t1 if t1 is not None and t1 > x0 else x0 + 1.0)
    y0 = float(min_channel)
    y1 = float(min_channel + matrix.shape[0] * space_step)
    return np.ascontiguousarray(matrix, dtype=np.float32), (x0, y0, x1 - x0, max(1.0, y1 - y0))


def _first_key(keys: set[str], *candidates: str) -> Optional[str]:
    for key in candidates:
        if key in keys:
            return key
    return None


def _read_scalar_string(data: np.lib.npyio.NpzFile, key: str, default: str) -> str:
    if key not in data.files:
        return default
    value = data[key]
    try:
        if isinstance(value, np.ndarray) and value.shape == ():
            return str(value.item())
        return str(value)
    except Exception:
        return default


def _read_array(data: np.lib.npyio.NpzFile, key: str, dtype) -> np.ndarray:
    if key not in data.files:
        return np.array([], dtype=dtype)
    try:
        return np.asarray(data[key], dtype=dtype).reshape(-1)
    except Exception:
        return np.array([], dtype=dtype)


def _read_frame_list(data: np.lib.npyio.NpzFile, key: Optional[str]) -> list[np.ndarray]:
    if not key or key not in data.files:
        return []
    arr = data[key]
    if isinstance(arr, np.ndarray) and arr.dtype == object:
        return [np.asarray(item, dtype=np.float64).reshape(-1) for item in arr.tolist()]
    numeric = np.asarray(arr, dtype=np.float64)
    if numeric.ndim == 1:
        return [numeric]
    return [np.asarray(row, dtype=np.float64).reshape(-1) for row in numeric]


def _read_matrix_list(data: np.lib.npyio.NpzFile, key: Optional[str]) -> list[np.ndarray]:
    if not key or key not in data.files:
        return []
    arr = data[key]
    if isinstance(arr, np.ndarray) and arr.dtype == object:
        return [np.asarray(item, dtype=np.float32) for item in arr.tolist()]
    numeric = np.asarray(arr, dtype=np.float32)
    if numeric.ndim == 2:
        return [numeric]
    if numeric.ndim == 3:
        return [np.asarray(frame, dtype=np.float32) for frame in numeric]
    return []


def _fit_frame_list(frames: list[np.ndarray], count: int) -> list[np.ndarray]:
    empty = np.array([], dtype=np.float64)
    return [frames[i] if i < len(frames) else empty for i in range(count)]


def _fit_matrix_list(frames: list[np.ndarray], count: int) -> list[np.ndarray]:
    empty = np.empty((0, 0), dtype=np.float32)
    return [frames[i] if i < len(frames) else empty for i in range(count)]


def _fit_bool_length(values: np.ndarray, count: int, default: bool) -> np.ndarray:
    if values.size >= count:
        return values[:count].astype(bool, copy=False)
    result = np.full(count, bool(default), dtype=bool)
    if values.size:
        result[: values.size] = values.astype(bool, copy=False)
    return result


def _fit_numeric_length(values: np.ndarray, count: int, fill_mode: str) -> np.ndarray:
    if values.size >= count:
        return values[:count].astype(np.float64, copy=False)
    result = np.empty(count, dtype=np.float64)
    if fill_mode == "sequence":
        result[:] = np.arange(count, dtype=np.float64)
    elif fill_mode == "duration":
        result[:] = 1.0
    elif fill_mode == "index":
        result[:] = np.arange(count, dtype=np.float64)
    elif fill_mode == "zero":
        result[:] = 0.0
    else:
        result[:] = np.nan
    if values.size:
        result[: values.size] = values.astype(np.float64, copy=False)
    return result


def _rate_for_frame(rates: np.ndarray, index: int, samples: int, durations: np.ndarray) -> float:
    if index < rates.size and np.isfinite(rates[index]) and rates[index] > 0:
        return float(rates[index])
    duration = _duration_for_frame(durations, index, 0.0)
    if duration > 0 and samples > 0:
        return float(samples / duration)
    return 1.0


def _duration_for_frame(durations: np.ndarray, index: int, default: float) -> float:
    if index < durations.size and np.isfinite(durations[index]) and durations[index] > 0:
        return float(durations[index])
    return float(default)
