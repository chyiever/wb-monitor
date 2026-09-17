"""Background worker for file loading and plot-data computation."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
from PyQt5.QtCore import QObject, pyqtSignal, pyqtSlot

from .data_loader import (
    JointReplayData,
    build_space_time_matrix,
    concatenate_das_channel,
    concatenate_fip_frames,
    load_data_file,
)
from .preprocess import PreprocessSpec, decimate_for_plot, preprocess_waveform, robust_levels


@dataclass(frozen=True)
class CurveRequest:
    source: str
    das_channel: int
    preprocess: PreprocessSpec
    max_points: int


@dataclass(frozen=True)
class SpaceRequest:
    channel_start: int
    channel_end: int
    time_downsample: int
    space_downsample: int
    remove_baseline: bool
    auto_levels: bool
    vmin: float
    vmax: float


@dataclass(frozen=True)
class RedrawRequest:
    seq: int
    path: str
    curve1: CurveRequest
    curve2: CurveRequest
    space: SpaceRequest


@dataclass
class CurveResult:
    times: np.ndarray
    values: np.ndarray
    title: str


@dataclass
class RedrawResult:
    seq: int
    path: str
    file_info: str
    has_fip: bool
    has_das: bool
    curve1: CurveResult
    curve2: CurveResult
    space_matrix: np.ndarray
    space_rect: tuple[float, float, float, float]
    space_levels: tuple[float, float]


class ReplayWorker(QObject):
    """Runs file loading and plot-data computation off the GUI thread."""

    redrawDone = pyqtSignal(object)
    failed = pyqtSignal(str, object)

    def __init__(self, cache_size: int = 4) -> None:
        super().__init__()
        self._cache_size = max(1, int(cache_size))
        self._data_cache: dict[str, JointReplayData] = {}

    @pyqtSlot(object)
    def redraw(self, request: RedrawRequest) -> None:
        try:
            data = self._load_cached(request.path)
            result = _compute_redraw(request, data)
            self.redrawDone.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc), request.seq)

    def _load_cached(self, path_str: str) -> JointReplayData:
        path = Path(path_str)
        key = str(path.resolve())
        cached = self._data_cache.get(key)
        if cached is not None:
            return cached
        data = load_data_file(path)
        if len(self._data_cache) >= self._cache_size:
            oldest = next(iter(self._data_cache))
            del self._data_cache[oldest]
        self._data_cache[key] = data
        return data


def _compute_redraw(request: RedrawRequest, data: JointReplayData) -> RedrawResult:
    curve1 = _compute_curve(1, request.curve1, data)
    curve2 = _compute_curve(2, request.curve2, data)
    matrix, rect, levels = _compute_space(request.space, data)
    return RedrawResult(
        seq=request.seq,
        path=request.path,
        file_info=_file_info_text(data),
        has_fip=data.has_fip,
        has_das=data.has_das,
        curve1=curve1,
        curve2=curve2,
        space_matrix=matrix,
        space_rect=rect,
        space_levels=levels,
    )


def _compute_curve(curve_index: int, request: CurveRequest, data: JointReplayData) -> CurveResult:
    if request.source == "FIP1":
        raw_t, raw_y, rate = concatenate_fip_frames(
            data.fip1_frames, data.packet_start_times, data.packet_duration_seconds, data.fip_rates_hz
        )
    elif request.source == "FIP2":
        raw_t, raw_y, rate = concatenate_fip_frames(
            data.fip2_frames, data.packet_start_times, data.packet_duration_seconds, data.fip_rates_hz
        )
    else:
        raw_t, raw_y, rate = concatenate_das_channel(
            data.das_frames,
            data.packet_start_times,
            data.packet_duration_seconds,
            data.das_rates_hz,
            request.das_channel,
        )
    rel_t, y, effective_rate = preprocess_waveform(raw_y, rate, request.preprocess)
    if raw_t.size == raw_y.size and y.size:
        step = max(1, int(request.preprocess.downsample))
        times = raw_t[::step][: y.size]
    elif raw_t.size and rel_t.size:
        t0 = float(raw_t[0])
        times = t0 + np.arange(y.size, dtype=np.float64) / effective_rate
    else:
        times = rel_t
    plot_times, plot_values = decimate_for_plot(times, y, request.max_points)
    return CurveResult(plot_times, plot_values, f"Waveform {curve_index}: {request.source}")


def _compute_space(request: SpaceRequest, data: JointReplayData) -> tuple[np.ndarray, tuple[float, float, float, float], tuple[float, float]]:
    matrix, rect = build_space_time_matrix(
        frames=data.das_frames,
        starts=data.packet_start_times,
        durations=data.packet_duration_seconds,
        rates=data.das_rates_hz,
        channel_start=request.channel_start,
        channel_end=request.channel_end,
        time_downsample=request.time_downsample,
        space_downsample=request.space_downsample,
        remove_baseline=request.remove_baseline,
    )
    if matrix.size == 0:
        matrix = np.zeros((1, 1), dtype=np.float32)
        rect = (0.0, float(request.channel_start), 1.0, 1.0)
    if request.auto_levels:
        levels = robust_levels(matrix)
    else:
        vmin = request.vmin
        vmax = request.vmax
        if vmin > vmax:
            vmin, vmax = vmax, vmin
        levels = (vmin, vmax)
    return matrix, rect, levels


def _file_info_text(data: JointReplayData) -> str:
    fip_rate = _median_text(data.fip_rates_hz, "Hz")
    das_rate = _median_text(data.das_rates_hz, "Hz")
    das_channels = int(np.nanmax(data.das_channel_counts)) if data.das_channel_counts.size else 0
    fip_samples = _sum_samples(data.fip1_frames)
    das_samples = _sum_samples(data.das_frames)
    duration = max(0.0, data.end_time - data.start_time)
    file_size = int(data.path.stat().st_size) if data.path.exists() else 0
    created = (data.created_at or "").strip() or "n/a"
    kind = []
    if data.has_fip:
        kind.append("FIP")
    if data.has_das:
        kind.append("eDAS")
    lines = [
        f"格式: {data.format_version}",
        f"数据: {'+'.join(kind) if kind else '未知'}",
        f"采集时刻: {created}",
        f"时长: {duration:.3f} s（{data.frame_count} 帧）",
        f"采样率: FIP {fip_rate}  |  DAS {das_rate}",
        f"通道数: {das_channels}",
        f"数据量: FIP {_human_samples(fip_samples)}  |  DAS {_human_samples(das_samples)}  |  文件 {_human_bytes(file_size)}",
    ]
    return "\n".join(lines)


def _sum_samples(frames: list[np.ndarray]) -> int:
    total = 0
    for frame in frames:
        arr = np.asarray(frame)
        if arr.size:
            total += int(arr.size)
    return total


def _human_samples(count: int) -> str:
    if count >= 1_000_000:
        return f"{count / 1_000_000:.2f} M点"
    if count >= 1_000:
        return f"{count / 1_000:.1f} k点"
    return f"{count} 点"


def _human_bytes(size: int) -> str:
    if size >= 1_000_000:
        return f"{size / 1_000_000:.1f} MB"
    if size >= 1_000:
        return f"{size / 1_000:.1f} KB"
    return f"{size} B"


def _median_text(values: np.ndarray, suffix: str) -> str:
    finite = values[np.isfinite(values) & (values > 0)] if values.size else np.array([])
    if finite.size == 0:
        return "n/a"
    value = float(np.nanmedian(finite))
    if value >= 1_000_000:
        return f"{value / 1_000_000:.3f} M{suffix}"
    if value >= 1_000:
        return f"{value / 1_000:.3f} k{suffix}"
    return f"{value:.3f} {suffix}"