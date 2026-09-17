"""Pluggable joint (FIP+eDAS) storage backends: bin / npz / h5.

Design goals:
    bin  - bare-binary streaming writer. No compression, pure buffered I/O.
           Sustains the full raw data rate (target >= 500 MB/s on NVMe/SSD).
    npz  - per-chunk np.savez_compressed archive (legacy, backward compatible).
    h5   - h5py container with optional gzip compression and chunking.

All three formats embed the same file-level metadata so a reader can recover,
without any external sidecar:

    created_at                 wall-clock when the chunk write started
    wall_clock_start           wall-clock when the first frame of the chunk
                               was captured (set by EDASManager)
    first/last_packet_start_time  session-relative seconds
    packet_duration_seconds
    fip_channel_count          == FIP sensor count
    fip_sample_rate_hz
    fip_duration_seconds
    das_channel_count
    das_sample_rate_hz
    das_duration_seconds
    frame_count / comm_counts / packet_start_times / fip_present / das_present
"""

from __future__ import annotations

import json
import logging
import struct
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

BIN_MAGIC = b"FIPeDAS1"
FORMAT_VERSION_BIN = "wb-monitor-joint-bin-v1"
FORMAT_VERSION_NPZ = "wb-monitor-joint-v6"
FORMAT_VERSION_H5 = "wb-monitor-joint-h5-v1"

VALID_FORMATS = ("bin", "npz", "h5")
VALID_H5_COMPRESSION = ("none", "gzip")


def _empty_fip_array() -> np.ndarray:
    return np.array([], dtype=np.float64)


def _frame_fip_arrays(frame: Any) -> List[np.ndarray]:
    """Return per-sensor raw (unwrapped) FIP arrays in sensor order."""
    fip = getattr(frame, "fip_packet", None)
    if fip is None:
        return []
    sensor_count = int(getattr(fip, "sensor_count", 1))
    mapping = getattr(fip, "unwrapped_by_sensor", None)
    if isinstance(mapping, dict) and mapping:
        selected = int(getattr(fip, "selected_sensor", 1))
        out: List[np.ndarray] = []
        for index in range(1, sensor_count + 1):
            arr = mapping.get(index)
            if arr is None:
                arr = fip.unwrapped_data if index == selected else _empty_fip_array()
            out.append(np.asarray(arr, dtype=np.float64))
        return out
    return [np.asarray(fip.unwrapped_data, dtype=np.float64)]


def _frame_das_matrix(frame: Any) -> Optional[np.ndarray]:
    das = getattr(frame, "das_packet", None)
    if das is None:
        return None
    return np.asarray(getattr(das, "matrix", np.array([], dtype=np.float64)), dtype=np.float64)


def build_chunk_metadata(
    frames: List[Any],
    created_at: str,
    wall_clock_start: Optional[str] = None,
) -> Dict[str, Any]:
    """Build the JSON-serializable file-level metadata shared by all formats."""
    if not frames:
        return {}
    first = frames[0]
    last = frames[-1]
    fip_packet = next(
        (getattr(frame, "fip_packet", None) for frame in frames if getattr(frame, "fip_packet", None) is not None),
        None,
    )
    das_packet = next(
        (getattr(frame, "das_packet", None) for frame in frames if getattr(frame, "das_packet", None) is not None),
        None,
    )
    packet_duration = float(getattr(first, "packet_duration_seconds", 1.0))
    fip_channel_count = int(getattr(fip_packet, "sensor_count", 0)) if fip_packet is not None else 0
    fip_sample_rate_hz = float(getattr(fip_packet, "sample_rate_hz", 0.0)) if fip_packet is not None else 0.0
    das_channel_count = int(getattr(das_packet, "channel_count", 0)) if das_packet is not None else 0
    das_sample_rate_hz = float(getattr(das_packet, "sample_rate_hz", 0.0)) if das_packet is not None else 0.0
    return {
        "format_version": FORMAT_VERSION_NPZ,
        "created_at": created_at,
        "wall_clock_start": wall_clock_start or created_at,
        "first_packet_start_time": float(getattr(first, "packet_start_time", 0.0)),
        "last_packet_start_time": float(getattr(last, "packet_start_time", 0.0)),
        "packet_duration_seconds": packet_duration,
        "fip_channel_count": fip_channel_count,
        "fip_sample_rate_hz": fip_sample_rate_hz,
        "fip_duration_seconds": float(getattr(fip_packet, "packet_duration_seconds", 0.0)) if fip_packet is not None else 0.0,
        "das_channel_count": das_channel_count,
        "das_sample_rate_hz": das_sample_rate_hz,
        "das_duration_seconds": float(getattr(das_packet, "packet_duration_seconds", 0.0)) if das_packet is not None else 0.0,
        "frame_count": len(frames),
        "comm_counts": [int(f.comm_count) for f in frames],
        "packet_start_times": [float(f.packet_start_time) for f in frames],
        "fip_present": [f.fip_packet is not None for f in frames],
        "das_present": [f.das_packet is not None for f in frames],
    }


def _chunk_timestamp_path(output_dir: str, suffix: str) -> Path:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    now = datetime.now()
    return output_path / f"FIPeDAS-{now.strftime('%Y%m%d-%H%M%S.%f')[:-3]}{suffix}"


def _as_float64_c(arr: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(arr, dtype="<f8")


def save_bin(
    frames: List[Any],
    output_dir: str,
    created_at: str,
    wall_clock_start: Optional[str] = None,
) -> str:
    """Stream raw FIP + eDAS frames into one self-describing binary file.

    Layout (little-endian):
        magic(8) | header_len(u32) | header_json | frame records...
    Each frame record:
        comm(i32) start(f64) duration(f64) fip_present(u8) das_present(u8)
        fip_sensor_count(i32) fip_sample_rate(f64)
        das_channel_count(i32) das_sample_rate(f64) n_fip_arrays(i32)
        then for each fip array: pts(i32) + raw f64 bytes
        then das: rows(i32) cols(i32) + raw f64 bytes
    """
    meta = build_chunk_metadata(frames, created_at, wall_clock_start)
    meta["format_version"] = FORMAT_VERSION_BIN
    file_path = _chunk_timestamp_path(output_dir, ".bin")
    header_json = json.dumps(meta, ensure_ascii=False).encode("utf-8")
    with open(file_path, "wb") as handle:
        handle.write(BIN_MAGIC)
        handle.write(struct.pack("<I", len(header_json)))
        handle.write(header_json)
        for frame in frames:
            _write_bin_frame(handle, frame)
    return str(file_path)


def _write_bin_frame(handle: Any, frame: Any) -> None:
    fip = getattr(frame, "fip_packet", None)
    das = getattr(frame, "das_packet", None)
    fip_arrays = _frame_fip_arrays(frame) if fip is not None else []
    das_matrix = _frame_das_matrix(frame)
    fip_sensor_count = int(getattr(fip, "sensor_count", 0)) if fip is not None else 0
    fip_rate = float(getattr(fip, "sample_rate_hz", 0.0)) if fip is not None else 0.0
    das_channel_count = int(getattr(das, "channel_count", 0)) if das is not None else 0
    das_rate = float(getattr(das, "sample_rate_hz", 0.0)) if das is not None else 0.0
    handle.write(
        struct.pack(
            "<iddBBididi",
            int(frame.comm_count),
            float(frame.packet_start_time),
            float(frame.packet_duration_seconds),
            1 if fip is not None else 0,
            1 if das is not None else 0,
            fip_sensor_count,
            fip_rate,
            das_channel_count,
            das_rate,
            len(fip_arrays),
        )
    )
    for arr in fip_arrays:
        arr = _as_float64_c(arr)
        handle.write(struct.pack("<i", int(arr.size)))
        handle.write(arr.tobytes(order="C"))
    if das_matrix is not None:
        das_matrix = _as_float64_c(das_matrix)
        rows, cols = int(das_matrix.shape[0]), int(das_matrix.shape[1])
        handle.write(struct.pack("<ii", rows, cols))
        handle.write(das_matrix.tobytes(order="C"))
    else:
        handle.write(struct.pack("<ii", 0, 0))


def save_npz(
    frames: List[Any],
    output_dir: str,
    created_at: str,
    wall_clock_start: Optional[str] = None,
) -> str:
    """Write one np.savez_compressed chunk (legacy joint format + metadata)."""
    meta = build_chunk_metadata(frames, created_at, wall_clock_start)
    file_path = _chunk_timestamp_path(output_dir, ".npz")
    payload: Dict[str, Any] = {
        "comm_counts": np.array([f.comm_count for f in frames], dtype=np.int32),
        "packet_start_times": np.array([f.packet_start_time for f in frames], dtype=np.float64),
        "packet_duration_seconds": np.array(
            [f.packet_duration_seconds for f in frames], dtype=np.float64
        ),
        "fip_present": np.array([not f.fip_missing for f in frames], dtype=bool),
        "das_present": np.array([not f.das_missing for f in frames], dtype=bool),
        "fip_sensor_count": np.array(
            [f.fip_packet.sensor_count if f.fip_packet is not None else 0 for f in frames],
            dtype=np.int32,
        ),
        "fip_selected_sensor": np.array(
            [f.fip_packet.selected_sensor if f.fip_packet is not None else 0 for f in frames],
            dtype=np.int32,
        ),
        "fip1_raw_data": np.array(
            [f.fip_packet.unwrapped_by_sensor.get(1, _empty_fip_array()) if f.fip_packet is not None else _empty_fip_array() for f in frames],
            dtype=object,
        ),
        "fip2_raw_data": np.array(
            [f.fip_packet.unwrapped_by_sensor.get(2, _empty_fip_array()) if f.fip_packet is not None else _empty_fip_array() for f in frames],
            dtype=object,
        ),
        "das_raw_matrix": np.array(
            [f.das_packet.matrix if f.das_packet is not None else _empty_fip_array() for f in frames],
            dtype=object,
        ),
        "fip_sample_rate_hz": np.array(
            [f.fip_packet.sample_rate_hz if f.fip_packet is not None else np.nan for f in frames],
            dtype=np.float64,
        ),
        "das_sample_rate_hz": np.array(
            [f.das_packet.sample_rate_hz if f.das_packet is not None else np.nan for f in frames],
            dtype=np.float64,
        ),
        "das_channel_count": np.array(
            [f.das_packet.channel_count if f.das_packet is not None else 0 for f in frames],
            dtype=np.int32,
        ),
        "incremental": np.bool_(True),
        "format_version": np.array(FORMAT_VERSION_NPZ),
        "created_at": np.array(meta["created_at"]),
        "wall_clock_start": np.array(meta["wall_clock_start"]),
        "metadata_json": np.array(json.dumps(meta, ensure_ascii=False)),
    }
    np.savez_compressed(file_path, **payload)
    return str(file_path)


def save_h5(
    frames: List[Any],
    output_dir: str,
    created_at: str,
    wall_clock_start: Optional[str] = None,
    compression: str = "none",
    compression_level: int = 4,
) -> str:
    """Write one h5 chunk with optional gzip compression and chunking."""
    import h5py

    meta = build_chunk_metadata(frames, created_at, wall_clock_start)
    meta["format_version"] = FORMAT_VERSION_H5
    file_path = _chunk_timestamp_path(output_dir, ".h5")

    fip_sensor_count = max(
        [int(f.fip_packet.sensor_count) if f.fip_packet is not None else 0 for f in frames] or [0]
    )
    das_channel_count = max(
        [int(f.das_packet.channel_count) if f.das_packet is not None else 0 for f in frames] or [0]
    )
    max_fip_pts = 0
    max_das_pts = 0
    for frame in frames:
        for arr in _frame_fip_arrays(frame):
            max_fip_pts = max(max_fip_pts, int(arr.size))
        das_matrix = _frame_das_matrix(frame)
        if das_matrix is not None and das_matrix.size:
            max_das_pts = max(max_das_pts, int(das_matrix.shape[1]))

    fip_sensors_data: Dict[int, np.ndarray] = {}
    for sensor_index in range(1, fip_sensor_count + 1):
        padded = np.zeros((len(frames), max_fip_pts), dtype="<f8")
        for i, frame in enumerate(frames):
            arrays = _frame_fip_arrays(frame)
            if sensor_index - 1 < len(arrays) and arrays[sensor_index - 1].size:
                pts = min(int(arrays[sensor_index - 1].size), max_fip_pts)
                padded[i, :pts] = arrays[sensor_index - 1][:pts]
        fip_sensors_data[sensor_index] = padded

    das_data = np.zeros((len(frames), das_channel_count, max_das_pts), dtype="<f8")
    for i, frame in enumerate(frames):
        das_matrix = _frame_das_matrix(frame)
        if das_matrix is not None and das_matrix.size:
            rows = min(int(das_matrix.shape[0]), das_channel_count)
            cols = min(int(das_matrix.shape[1]), max_das_pts)
            das_data[i, :rows, :cols] = das_matrix[:rows, :cols]

    compression_kwargs: Dict[str, Any] = {}
    if compression in ("gzip",):
        compression_kwargs["compression"] = "gzip"
        compression_kwargs["compression_opts"] = int(compression_level)
    dataset_kwargs = dict(compression_kwargs)
    if dataset_kwargs:
        dataset_kwargs["chunks"] = True

    with h5py.File(file_path, "w") as h5_file:
        for key, value in meta.items():
            if isinstance(value, (list, dict)):
                h5_file.attrs[key] = json.dumps(value, ensure_ascii=False)
            else:
                h5_file.attrs[key] = value
        h5_file.attrs["fip_channel_count"] = fip_sensor_count
        h5_file.attrs["das_channel_count"] = das_channel_count
        h5_file.attrs["fip_points_per_sensor"] = max_fip_pts
        h5_file.attrs["das_samples_per_channel"] = max_das_pts
        h5_file.attrs["metadata_json"] = json.dumps(meta, ensure_ascii=False)
        h5_file.create_dataset("comm_counts", data=np.array([f.comm_count for f in frames], dtype=np.int64))
        h5_file.create_dataset(
            "packet_start_times",
            data=np.array([f.packet_start_time for f in frames], dtype=np.float64),
        )
        h5_file.create_dataset("fip_present", data=np.array([f.fip_packet is not None for f in frames], dtype=bool))
        h5_file.create_dataset("das_present", data=np.array([f.das_packet is not None for f in frames], dtype=bool))
        for sensor_index in range(1, fip_sensor_count + 1):
            h5_file.create_dataset(f"fip{sensor_index}_raw", data=fip_sensors_data[sensor_index], **dataset_kwargs)
        if das_channel_count > 0 and max_das_pts > 0:
            h5_file.create_dataset("das_raw", data=das_data, **dataset_kwargs)
    return str(file_path)


def save_joint(
    fmt: str,
    frames: List[Any],
    output_dir: str,
    created_at: str,
    wall_clock_start: Optional[str] = None,
    h5_compression: str = "none",
    h5_compression_level: int = 4,
) -> str:
    """Dispatch a joint chunk to the configured backend. Returns the file path."""
    fmt = str(fmt or "npz").lower()
    if fmt not in VALID_FORMATS:
        fmt = "npz"
    if fmt == "bin":
        return save_bin(frames, output_dir, created_at, wall_clock_start)
    if fmt == "h5":
        return save_h5(
            frames,
            output_dir,
            created_at,
            wall_clock_start,
            compression=h5_compression,
            compression_level=h5_compression_level,
        )
    return save_npz(frames, output_dir, created_at, wall_clock_start)
