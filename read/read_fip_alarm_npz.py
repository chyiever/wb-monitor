"""Read and summarize FIP alarm NPZ files.

This utility reads one alarm NPZ file or scans a directory for alarm files.
It prints event metadata, stored signal summary, and feature-series summary.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path
from typing import Dict, Iterable

import numpy as np


def ensure_numpy_pickle_compatibility() -> None:
    """Register module aliases required by pickled object arrays."""
    try:
        numpy_private_core = importlib.import_module("numpy._core")
    except ModuleNotFoundError:
        numpy_private_core = importlib.import_module("numpy.core")

    sys.modules.setdefault("numpy._core", numpy_private_core)

    multiarray = importlib.import_module(f"{numpy_private_core.__name__}.multiarray")
    sys.modules.setdefault("numpy._core.multiarray", multiarray)


def load_alarm_npz(file_path: Path):
    """Load one alarm NPZ file with compatibility aliases enabled."""
    ensure_numpy_pickle_compatibility()
    return np.load(file_path, allow_pickle=True)


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser."""
    parser = argparse.ArgumentParser(
        description="Read FIP alarm NPZ files and print a concise summary."
    )
    parser.add_argument(
        "input_path",
        type=Path,
        help="Path to one .npz file or a directory containing alarm .npz files.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=20,
        help="Maximum number of files to read when a directory is provided.",
    )
    return parser


def iter_npz_files(input_path: Path, limit: int) -> Iterable[Path]:
    """Yield alarm NPZ files from a file path or directory path."""
    if input_path.is_file():
        yield input_path
        return

    files = sorted(input_path.glob("an-*.npz"))
    for file_path in files[: max(0, limit)]:
        yield file_path


def unpack_object_dict(array: np.ndarray) -> Dict[str, list]:
    """Convert an object array back into a Python dictionary."""
    if array.shape == ():
        return array.item()
    return array[()]


def summarize_feature_dict(feature_times: Dict[str, list], feature_values: Dict[str, list]) -> str:
    """Build a readable summary for feature time series."""
    lines = []
    for feature_name in sorted(feature_times.keys()):
        times = feature_times.get(feature_name, [])
        values = feature_values.get(feature_name, [])
        count = min(len(times), len(values))
        if count == 0:
            lines.append(f"  - {feature_name}: empty")
            continue
        lines.append(
            f"  - {feature_name}: count={count}, "
            f"time=[{times[0]:.3f}, {times[count - 1]:.3f}], "
            f"value=[{min(values):.6f}, {max(values):.6f}]"
        )
    return "\n".join(lines)


def read_alarm_file(file_path: Path) -> str:
    """Read one alarm NPZ file and return a text summary."""
    with load_alarm_npz(file_path) as data:
        signal = data["signal"]
        signal_time = data["signal_time"]
        sample_rate = float(data["sample_rate"])
        event_start_time = float(data["event_start_time"])
        event_end_time = float(data["event_end_time"])
        event_duration = float(data["event_duration"])
        trigger_feature_names = list(data["trigger_feature_names"])
        trigger_feature_count = int(data["trigger_feature_count"])
        feature_times = unpack_object_dict(data["feature_times"])
        feature_values = unpack_object_dict(data["feature_values"])

    signal_summary = (
        f"signal_len={len(signal)}, time_len={len(signal_time)}, sample_rate={sample_rate:.1f}"
    )
    if len(signal_time) > 0:
        signal_summary += f", signal_time=[{signal_time[0]:.6f}, {signal_time[-1]:.6f}]"

    summary_lines = [
        f"File: {file_path}",
        (
            "Event: "
            f"start={event_start_time:.3f}s, "
            f"end={event_end_time:.3f}s, "
            f"duration={event_duration:.3f}s"
        ),
        (
            "Trigger: "
            f"count={trigger_feature_count}, "
            f"features={trigger_feature_names}"
        ),
        f"Signal: {signal_summary}",
        "Features:",
        summarize_feature_dict(feature_times, feature_values),
    ]
    return "\n".join(summary_lines)


def main() -> int:
    """Program entry point."""
    parser = build_parser()
    args = parser.parse_args()

    input_path = args.input_path.expanduser()
    if not input_path.exists():
        parser.error(f"Input path does not exist: {input_path}")

    files = list(iter_npz_files(input_path, args.limit))
    if not files:
        parser.error(f"No alarm NPZ files found in: {input_path}")

    for index, file_path in enumerate(files):
        if index:
            print("-" * 80)
        print(read_alarm_file(file_path))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
