"""Readers for wb-monitor FIP/eDAS storage files.

Supported formats (see docs/FIP／eDAS 通信协议与数据存储机制.md):

- FIP 独立 `.npz`           (`wb-monitor-tab1-fip-v3`)
- eDAS 独立 `.bin + .json`  (`wb-monitor-edas-raw-v1`)
- FIP+eDAS 联合 `.npz`      (`wb-monitor-joint-v5` / `-v6`)
- FIP+eDAS 联合 `.bin`      (`wb-monitor-joint-bin-v1`, magic "FIPeDAS1")
- FIP+eDAS 联合 `.h5`       (`wb-monitor-joint-h5-v1`)
"""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Optional

import numpy as np

JOINT_NPZ_KEYS = (
    "comm_counts",
    "fip_present",
    "das_present",
    "fip1_raw_data",
    "fip2_raw_data",
    "das_raw_matrix",
    "fip1_display_data",
    "fip2_display_data",
)


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

    @property
    def has_fip(self) -> bool:
        return bool(self.fip_present.size and np.any(self.fip_present))

    @property
    def has_das(self) -> bool:
        return bool(self.das_present.size and np.any(self.das_present))


def iter_replay_files(path: Path) -> list[Path]:
    path = Path(path)
    if path.is_file():
        return [path] if _is_supported_file(path) else []
    if not path.exists():
        return []
    files: list[Path] = []
    files += sorted(path.glob("FIPeDAS-*.npz"))
    files += sorted(path.glob("FIPeDAS-*.bin"))
    files += sorted(path.glob("FIPeDAS-*.h5"))
    files += sorted(p for p in path.glob("*-FIP-*.npz"))
    files += sorted(p for p in path.glob("*-FIP2-*.npz"))
    files += sorted(p for p in path.glob("*-eDAS-*.bin") if p.with_suffix(".json").exists())
    if not files:
        files += sorted(path.glob("*.npz"))
        files += sorted(path.glob("*.h5"))
    seen: set[str] = set()
    result: list[Path] = []
    for file_path in files:
        key = str(file_path.resolve())
        if key not in seen:
            seen.add(key)
            result.append(file_path)
    return sorted(result)


def load_data_file(path: Path) -> JointReplayData:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".npz":
        with np.load(path, allow_pickle=True) as data:
            keys = set(data.files)
        if any(key in keys for key in JOINT_NPZ_KEYS):
            return load_joint_npz(path)
        return load_fip_npz(path)
    if suffix == ".h5":
        return load_joint_h5(path)
    if suffix == ".bin":
        if path.name.startswith("FIPeDAS-"):
            return load_joint_bin(path)
        return load_edas_bin_json(path)
    raise ValueError(f"不支持的文件类型: {path.name}")


def load_joint_npz(path: Path) -> JointReplayData:
    path = Path(path)
    with np.load(path, allow_pickle=True) as data:
        keys = set(data.files)
        format_version = _read_scalar_string(data, "format_version", "unknown")
        created_at = _read_scalar_string(data, "created_at", "") or _read_scalar_string(data, "wall_clock_start", "")
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

    return _make_joint(
        path=path,
        format_version=format_version,
        created_at=created_at,
        comm_counts=comm_counts,
        packet_start_times=packet_start_times,
        packet_duration_seconds=packet_duration_seconds,
        fip1_frames=fip1_frames,
        fip2_frames=fip2_frames,
        das_frames=das_frames,
        fip_rates_hz=fip_rates_hz,
        das_rates_hz=das_rates_hz,
        das_channel_counts=das_channel_counts,
        fip_present=fip_present,
        das_present=das_present,
    )


def load_fip_npz(path: Path) -> JointReplayData:
    path = Path(path)
    with np.load(path, allow_pickle=True) as data:
        format_version = _read_scalar_string(data, "format_version", "wb-monitor-tab1-fip")
        phase_data = np.asarray(data["phase_data"], dtype=np.float64)
        sample_rate = _read_scalar_number(data, "sample_rate", 0.0) or _read_scalar_number(data, "raw_sample_rate_hz", 0.0)
        packet_duration = _read_scalar_number(data, "packet_duration_seconds", 1.0)
        sensor_count = int(_read_scalar_number(data, "fip_sensor_count", 0.0))
        if sensor_count <= 0:
            sensor_count = 2 if phase_data.ndim == 2 and phase_data.shape[0] > 1 else 1
        comm_count = int(_read_scalar_number(data, "comm_count", 0.0))
        timestamp = _read_scalar_number(data, "timestamp", 0.0)
        info = _read_data_info(data)
        created_at = str(info.get("stream_start_time", "") or info.get("save_time", "") or "")

    if phase_data.ndim == 2:
        fip1 = np.asarray(phase_data[0], dtype=np.float64).reshape(-1)
        fip2 = np.asarray(phase_data[1], dtype=np.float64).reshape(-1) if phase_data.shape[0] > 1 else np.array([], dtype=np.float64)
    else:
        fip1 = np.asarray(phase_data, dtype=np.float64).reshape(-1)
        fip2 = np.array([], dtype=np.float64)

    return _make_joint(
        path=path,
        format_version=format_version,
        created_at=created_at,
        comm_counts=[comm_count],
        packet_start_times=[timestamp],
        packet_duration_seconds=[packet_duration],
        fip1_frames=[fip1],
        fip2_frames=[fip2],
        das_frames=[np.empty((0, 0), dtype=np.float32)],
        fip_rates_hz=[sample_rate if sample_rate > 0 else 1.0],
        das_rates_hz=[0.0],
        das_channel_counts=[0],
        fip_present=[True],
        das_present=[False],
    )


def load_edas_bin_json(bin_path: Path) -> JointReplayData:
    bin_path = Path(bin_path)
    meta = json.loads(bin_path.with_suffix(".json").read_text(encoding="utf-8"))
    shape = meta.get("matrix_shape_per_block", [0, 0])
    channels = int(shape[0])
    samples = int(shape[1])
    blocks = int(meta.get("blocks_written", 0))
    format_version = str(meta.get("format_version", "wb-monitor-edas-raw-v1"))
    created_at = str(meta.get("created_at", ""))
    rate = float(meta.get("sample_rate_hz", 0.0))
    packet_duration = float(meta.get("packet_duration_seconds", 1.0))

    raw = np.fromfile(bin_path, dtype="<f8")
    if blocks and channels and samples and raw.size >= blocks * channels * samples:
        matrices = raw[: blocks * channels * samples].reshape(blocks, channels, samples)
        # 一次性批量降为 float32（比逐块转换快数倍），das_frames 保存视图避免再复制
        matrices = matrices.astype(np.float32, copy=False)
    else:
        matrices = np.empty((0, 0, 0), dtype=np.float32)
    das_frames = [matrices[i] for i in range(matrices.shape[0])]
    frame_count = len(das_frames)

    comms = list(np.asarray(meta.get("comm_counts", []), dtype=np.int64))
    starts = list(np.asarray(meta.get("packet_start_times", []), dtype=np.float64))
    ends = list(np.asarray(meta.get("packet_end_times", []), dtype=np.float64))
    if len(comms) < frame_count:
        comms = list(range(frame_count))
    if len(starts) < frame_count:
        starts = [0.0] * frame_count
    durations = [
        float(ends[i] - starts[i]) if i < len(ends) and starts[i] > 0 else packet_duration
        for i in range(frame_count)
    ]

    return _make_joint(
        path=bin_path,
        format_version=format_version,
        created_at=created_at,
        comm_counts=comms,
        packet_start_times=starts,
        packet_duration_seconds=durations,
        fip1_frames=[],
        fip2_frames=[],
        das_frames=das_frames,
        fip_rates_hz=[],
        das_rates_hz=[rate] * frame_count,
        das_channel_counts=[channels] * frame_count,
        fip_present=[],
        das_present=[True] * frame_count,
    )


def load_joint_bin(path: Path) -> JointReplayData:
    path = Path(path)
    with open(path, "rb") as f:
        magic = f.read(8)
        if magic != b"FIPeDAS1":
            raise ValueError(f"不是 FIPeDAS joint bin 文件: {path.name}")
        header_len = struct.unpack("<I", f.read(4))[0]
        header = json.loads(f.read(header_len).decode("utf-8"))
        format_version = str(header.get("format_version", "wb-monitor-joint-bin-v1"))
        created_at = str(header.get("created_at", "") or header.get("wall_clock_start", ""))

        fip1_frames: list[np.ndarray] = []
        fip2_frames: list[np.ndarray] = []
        das_frames: list[np.ndarray] = []
        comms: list[int] = []
        starts: list[float] = []
        durations: list[float] = []
        fip_present: list[bool] = []
        das_present: list[bool] = []
        fip_rates: list[float] = []
        das_rates: list[float] = []
        das_counts: list[int] = []

        while True:
            rec = f.read(50)
            if len(rec) < 50:
                break
            (comm, start, dur, fip_p, das_p, fip_s, fip_r, das_c, das_r, n) = struct.unpack("<iddBBididi", rec)
            f1 = np.array([], dtype=np.float64)
            f2 = np.array([], dtype=np.float64)
            if n > 0:
                sensors: list[np.ndarray] = []
                for _ in range(n):
                    pts = struct.unpack("<i", f.read(4))[0]
                    sensors.append(np.frombuffer(f.read(pts * 8), dtype="<f8"))
                f1 = sensors[0]
                if len(sensors) > 1:
                    f2 = sensors[1]
            rows, cols = struct.unpack("<ii", f.read(8))
            if rows > 0 and cols > 0:
                das = np.frombuffer(f.read(rows * cols * 8), dtype="<f8").reshape(rows, cols)
            else:
                das = np.empty((0, 0), dtype=np.float32)

            fip1_frames.append(f1)
            fip2_frames.append(f2)
            das_frames.append(np.asarray(das, dtype=np.float32))
            comms.append(comm)
            starts.append(start)
            durations.append(dur)
            fip_present.append(bool(fip_p))
            das_present.append(bool(das_p))
            fip_rates.append(fip_r)
            das_rates.append(das_r)
            das_counts.append(das_c)

    return _make_joint(
        path=path,
        format_version=format_version,
        created_at=created_at,
        comm_counts=comms,
        packet_start_times=starts,
        packet_duration_seconds=durations,
        fip1_frames=fip1_frames,
        fip2_frames=fip2_frames,
        das_frames=das_frames,
        fip_rates_hz=fip_rates,
        das_rates_hz=das_rates,
        das_channel_counts=das_counts,
        fip_present=fip_present,
        das_present=das_present,
    )


def load_joint_h5(path: Path) -> JointReplayData:
    import h5py

    path = Path(path)
    fip1_frames: list[np.ndarray] = []
    fip2_frames: list[np.ndarray] = []
    das_frames: list[np.ndarray] = []
    comms: list[int] = []
    starts: list[float] = []
    durations: list[float] = []
    fip_present: list[bool] = []
    das_present: list[bool] = []
    fip_rates: list[float] = []
    das_rates: list[float] = []
    das_counts: list[int] = []

    with h5py.File(path, "r") as h:
        attrs = dict(h.attrs)
        format_version = str(attrs.get("format_version", "wb-monitor-joint-h5-v1"))
        created_at = str(attrs.get("created_at", "") or attrs.get("wall_clock_start", ""))
        packet_duration = float(attrs.get("packet_duration_seconds", 1.0))
        fip_rate = float(attrs.get("fip_sample_rate_hz", 0.0))
        das_rate = float(attrs.get("das_sample_rate_hz", 0.0))
        das_ch = int(attrs.get("das_channel_count", 0))
        count = int(attrs.get("frame_count", 0))
        if "comm_counts" in h:
            comms = [int(v) for v in np.asarray(h["comm_counts"][:], dtype=np.int64).reshape(-1)]
            count = max(count, len(comms))
        if "packet_start_times" in h:
            starts = [float(v) for v in np.asarray(h["packet_start_times"][:], dtype=np.float64).reshape(-1)]
        fip_p = np.asarray(h["fip_present"][:], dtype=bool).reshape(-1) if "fip_present" in h else None
        das_p = np.asarray(h["das_present"][:], dtype=bool).reshape(-1) if "das_present" in h else None
        fip1_raw = h["fip1_raw"][:] if "fip1_raw" in h else None
        fip2_raw = h["fip2_raw"][:] if "fip2_raw" in h else None
        das_raw = h["das_raw"][:] if "das_raw" in h else None

        for i in range(count):
            f1 = np.asarray(fip1_raw[i], dtype=np.float64).reshape(-1) if fip1_raw is not None else np.array([], dtype=np.float64)
            f2 = np.asarray(fip2_raw[i], dtype=np.float64).reshape(-1) if fip2_raw is not None else np.array([], dtype=np.float64)
            d = np.asarray(das_raw[i], dtype=np.float32) if das_raw is not None else np.empty((0, 0), dtype=np.float32)
            fip1_frames.append(f1)
            fip2_frames.append(f2)
            das_frames.append(d)
            durations.append(packet_duration)
            fip_rates.append(fip_rate)
            das_rates.append(das_rate)
            das_counts.append(das_ch)
            fip_present.append(bool(fip_p[i]) if fip_p is not None and i < fip_p.size else (f1.size > 0))
            das_present.append(bool(das_p[i]) if das_p is not None and i < das_p.size else (d.size > 0))

    return _make_joint(
        path=path,
        format_version=format_version,
        created_at=created_at,
        comm_counts=comms,
        packet_start_times=starts,
        packet_duration_seconds=durations,
        fip1_frames=fip1_frames,
        fip2_frames=fip2_frames,
        das_frames=das_frames,
        fip_rates_hz=fip_rates,
        das_rates_hz=das_rates,
        das_channel_counts=das_counts,
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


def _make_joint(
    path: Path,
    format_version: str,
    created_at: str,
    comm_counts,
    packet_start_times,
    packet_duration_seconds,
    fip1_frames: list[np.ndarray],
    fip2_frames: list[np.ndarray],
    das_frames: list[np.ndarray],
    fip_rates_hz,
    das_rates_hz,
    das_channel_counts,
    fip_present,
    das_present,
) -> JointReplayData:
    frame_count = max(len(fip1_frames), len(fip2_frames), len(das_frames), int(np.asarray(comm_counts).size), 1)
    packet_start_times = _fit_numeric_length(np.asarray(packet_start_times, dtype=np.float64).reshape(-1), frame_count, fill_mode="sequence")
    packet_duration_seconds = _fit_numeric_length(np.asarray(packet_duration_seconds, dtype=np.float64).reshape(-1), frame_count, fill_mode="duration")
    comm_counts = _fit_numeric_length(np.asarray(comm_counts, dtype=np.float64).reshape(-1), frame_count, fill_mode="index").astype(np.int32, copy=False)
    fip_rates_hz = _fit_numeric_length(np.asarray(fip_rates_hz, dtype=np.float64).reshape(-1), frame_count, fill_mode="nan")
    das_rates_hz = _fit_numeric_length(np.asarray(das_rates_hz, dtype=np.float64).reshape(-1), frame_count, fill_mode="nan")
    das_channel_counts = _fit_numeric_length(np.asarray(das_channel_counts, dtype=np.float64).reshape(-1), frame_count, fill_mode="zero").astype(np.int32, copy=False)
    fip_present = _fit_bool_length(np.asarray(fip_present, dtype=bool).reshape(-1), frame_count, default=True if fip1_frames else False)
    das_present = _fit_bool_length(np.asarray(das_present, dtype=bool).reshape(-1), frame_count, default=True if das_frames else False)

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


def _is_supported_file(path: Path) -> bool:
    path = Path(path)
    if path.suffix.lower() == ".npz":
        return True
    if path.suffix.lower() == ".h5":
        return True
    if path.suffix.lower() == ".bin":
        return path.name.startswith("FIPeDAS-") or path.with_suffix(".json").exists()
    return False


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


def _read_scalar_number(data: np.lib.npyio.NpzFile, key: str, default: float) -> float:
    if key not in data.files:
        return default
    try:
        value = data[key]
        if isinstance(value, np.ndarray):
            if value.size == 0:
                return default
            value = value.item() if value.shape == () else value.flatten()[0]
        return float(value)
    except Exception:
        return default


def _read_data_info(data: np.lib.npyio.NpzFile) -> dict:
    try:
        obj = data["data_info"]
        info = obj.item() if isinstance(obj, np.ndarray) and obj.shape == () else obj
        if isinstance(info, dict):
            return info
        if hasattr(info, "tolist"):
            return {}
    except Exception:
        pass
    return {}


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