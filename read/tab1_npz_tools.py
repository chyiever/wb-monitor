"""Utilities for reading, preprocessing, filtering, saving, and plotting Tab1 NPZ data."""

from __future__ import annotations

from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

import matplotlib.pyplot as plt
import numpy as np


Tab1Payload = Dict[str, object]


def iter_tab1_npz_files(input_path: Path, limit: Optional[int] = None) -> Iterable[Path]:
    """Yield Tab1 phase-data NPZ files from one file or a directory."""
    if input_path.is_file():
        yield input_path
        return

    files = sorted(input_path.glob("phase_data_*.npz"))
    if limit is None:
        yield from files
        return

    yield from files[: max(0, limit)]


def resolve_tab1_npz_files(
    input_path: Path,
    file_names: Optional[Sequence[str]] = None,
    start_index: int = 0,
    count: Optional[int] = None,
) -> List[Path]:
    """Resolve one or more Tab1 NPZ files from a file path or directory."""
    input_path = Path(input_path)
    if input_path.is_file():
        return [input_path]

    if file_names:
        resolved_files: List[Path] = []
        for name in file_names:
            candidate = Path(name)
            file_path = candidate if candidate.is_absolute() else input_path / candidate
            if not file_path.exists():
                raise FileNotFoundError(f"Tab1 NPZ file not found: {file_path}")
            resolved_files.append(file_path)
        return resolved_files

    files = sorted(input_path.glob("phase_data_*.npz"))
    if start_index < 0:
        raise ValueError("start_index must be >= 0")

    selected = files[start_index:]
    if count is not None:
        if count <= 0:
            return []
        selected = selected[:count]
    return selected


def load_tab1_npz(file_path: Path) -> Tab1Payload:
    """Load one Tab1 NPZ file into a plain Python dictionary."""
    with np.load(file_path, allow_pickle=True) as data:
        data_info = data["data_info"]
        if isinstance(data_info, np.ndarray):
            if data_info.shape == ():
                data_info = data_info.item()
            else:
                data_info = data_info.tolist()

        return {
            "file_path": str(file_path),
            "phase_data": np.asarray(data["phase_data"], dtype=np.float64),
            "comm_count": int(data["comm_count"]),
            "timestamp": float(data["timestamp"]),
            "sample_rate": float(data["sample_rate"]),
            "data_info": data_info,
        }


def load_multiple_tab1_npz(files: Sequence[Path]) -> List[Tab1Payload]:
    """Load multiple Tab1 NPZ files in order."""
    return [load_tab1_npz(Path(file_path)) for file_path in files]


def concatenate_tab1_payloads(payloads: Sequence[Tab1Payload]) -> Tab1Payload:
    """Concatenate multiple payloads into one continuous waveform payload."""
    if not payloads:
        raise ValueError("payloads must not be empty")

    sample_rates = [float(payload["sample_rate"]) for payload in payloads]
    reference_rate = sample_rates[0]
    for sample_rate in sample_rates[1:]:
        if not np.isclose(sample_rate, reference_rate):
            raise ValueError("All files must have the same sample_rate before concatenation")

    phase_data = np.concatenate(
        [np.asarray(payload["phase_data"], dtype=np.float64) for payload in payloads]
    )
    file_paths = [str(payload["file_path"]) for payload in payloads]
    timestamps = [float(payload["timestamp"]) for payload in payloads]
    comm_counts = [int(payload["comm_count"]) for payload in payloads]

    return {
        "file_path": file_paths[0] if len(file_paths) == 1 else file_paths,
        "phase_data": phase_data,
        "comm_count": sum(comm_counts),
        "timestamp": min(timestamps),
        "sample_rate": reference_rate,
        "data_info": {
            "type": "phase_data_concatenated",
            "length": int(len(phase_data)),
            "file_count": len(payloads),
            "source_files": file_paths,
            "comm_counts": comm_counts,
            "timestamps": timestamps,
        },
    }


def load_and_concatenate_tab1_npz(
    input_path: Path,
    file_names: Optional[Sequence[str]] = None,
    start_index: int = 0,
    count: Optional[int] = None,
) -> Tab1Payload:
    """Resolve, load, and concatenate one or more Tab1 NPZ files."""
    files = resolve_tab1_npz_files(
        input_path=input_path,
        file_names=file_names,
        start_index=start_index,
        count=count,
    )
    if not files:
        raise FileNotFoundError(f"No Tab1 NPZ files found in: {input_path}")
    return concatenate_tab1_payloads(load_multiple_tab1_npz(files))


def save_tab1_npz(
    file_path: Path,
    phase_data: np.ndarray,
    sample_rate: float,
    comm_count: int = 0,
    timestamp: float = 0.0,
    data_type: str = "phase_unwrapped_downsampled",
    extra_info: Optional[Dict[str, object]] = None,
) -> Path:
    """Save waveform data to a Tab1-compatible NPZ file."""
    file_path = Path(file_path)
    file_path.parent.mkdir(parents=True, exist_ok=True)

    data_info = {
        "type": data_type,
        "length": int(len(phase_data)),
        "save_time": None,
    }
    if extra_info:
        data_info.update(extra_info)

    np.savez_compressed(
        file_path,
        phase_data=np.asarray(phase_data, dtype=np.float64),
        comm_count=int(comm_count),
        timestamp=float(timestamp),
        sample_rate=float(sample_rate),
        data_info=data_info,
    )
    return file_path


def build_time_axis(sample_count: int, sample_rate: float) -> np.ndarray:
    """Build a monotonic time axis in seconds."""
    if sample_count <= 0:
        return np.array([], dtype=np.float64)
    return np.arange(sample_count, dtype=np.float64) / float(sample_rate)


def extract_signal_array(phase_data: np.ndarray) -> np.ndarray:
    """Return the waveform as a 1D float64 array."""
    values = np.asarray(phase_data, dtype=np.float64).reshape(-1)
    return values.copy()


def preprocess_waveform(
    phase_data: np.ndarray,
    sample_rate: float,
    remove_mean: bool = True,
    normalize: bool = False,
    start_time: Optional[float] = None,
    duration: Optional[float] = None,
    downsample_factor: int = 1,
) -> Tuple[np.ndarray, np.ndarray, float]:
    """Apply lightweight preprocessing for offline viewing."""
    values = extract_signal_array(phase_data)
    effective_rate = float(sample_rate)

    if values.size == 0:
        return np.array([], dtype=np.float64), values, effective_rate

    if remove_mean:
        values = values - np.mean(values)

    if start_time is not None or duration is not None:
        start_index = 0 if start_time is None else max(0, int(round(start_time * sample_rate)))
        end_index = len(values)
        if duration is not None:
            end_index = min(len(values), start_index + int(round(duration * sample_rate)))
        values = values[start_index:end_index]

    if downsample_factor > 1:
        values = values[::downsample_factor]
        effective_rate = effective_rate / downsample_factor

    if normalize and values.size > 0:
        peak = np.max(np.abs(values))
        if peak > 0:
            values = values / peak

    times = build_time_axis(len(values), effective_rate)
    return times, values, effective_rate


def compute_psd(
    phase_data: np.ndarray,
    sample_rate: float,
    remove_mean: bool = True,
    window: str = "hann",
) -> Tuple[np.ndarray, np.ndarray]:
    """Compute a one-sided PSD using a periodogram estimate."""
    values = extract_signal_array(phase_data)
    sample_rate = float(sample_rate)

    if values.size == 0:
        return np.array([], dtype=np.float64), np.array([], dtype=np.float64)
    if sample_rate <= 0:
        raise ValueError("sample_rate must be > 0")

    if remove_mean:
        values = values - np.mean(values)

    if window == "hann":
        window_values = np.hanning(values.size)
    elif window == "boxcar":
        window_values = np.ones(values.size, dtype=np.float64)
    else:
        raise ValueError(f"Unsupported window: {window}")

    spectrum = np.fft.rfft(values * window_values)
    freqs = np.fft.rfftfreq(values.size, d=1.0 / sample_rate)
    scale = sample_rate * np.sum(window_values**2)
    psd = (np.abs(spectrum) ** 2) / scale
    if values.size > 1:
        psd[1:-1] *= 2.0
    return freqs, psd


def apply_frequency_filter(
    phase_data: np.ndarray,
    sample_rate: float,
    lowcut: Optional[float] = None,
    highcut: Optional[float] = None,
    remove_mean: bool = True,
) -> np.ndarray:
    """Filter the waveform in the frequency domain with a simple pass band."""
    values = extract_signal_array(phase_data)
    sample_rate = float(sample_rate)

    if values.size == 0:
        return values
    if sample_rate <= 0:
        raise ValueError("sample_rate must be > 0")

    nyquist = sample_rate / 2.0
    if lowcut is None and highcut is None:
        return values - np.mean(values) if remove_mean else values
    if lowcut is not None and lowcut < 0:
        raise ValueError("lowcut must be >= 0")
    if highcut is not None and highcut <= 0:
        raise ValueError("highcut must be > 0")
    if highcut is not None and highcut > nyquist:
        raise ValueError(f"highcut must be <= Nyquist frequency ({nyquist:.3f} Hz)")
    if lowcut is not None and highcut is not None and lowcut > highcut:
        raise ValueError("lowcut must be <= highcut")

    working_values = values - np.mean(values) if remove_mean else values.copy()
    spectrum = np.fft.rfft(working_values)
    freqs = np.fft.rfftfreq(working_values.size, d=1.0 / sample_rate)
    mask = np.ones_like(freqs, dtype=bool)
    if lowcut is not None:
        mask &= freqs >= lowcut
    if highcut is not None:
        mask &= freqs <= highcut

    filtered_spectrum = spectrum * mask
    filtered_values = np.fft.irfft(filtered_spectrum, n=working_values.size)
    return np.asarray(filtered_values, dtype=np.float64)


def summarize_tab1_npz(payload: Tab1Payload) -> Dict[str, object]:
    """Build a concise summary for one loaded Tab1 NPZ payload."""
    phase_data = extract_signal_array(np.asarray(payload["phase_data"], dtype=np.float64))
    sample_rate = float(payload["sample_rate"])
    duration = 0.0 if sample_rate <= 0 else len(phase_data) / sample_rate

    return {
        "file_path": payload["file_path"],
        "comm_count": int(payload["comm_count"]),
        "timestamp": float(payload["timestamp"]),
        "sample_rate": sample_rate,
        "length": int(len(phase_data)),
        "duration_seconds": duration,
        "min": float(np.min(phase_data)) if phase_data.size else None,
        "max": float(np.max(phase_data)) if phase_data.size else None,
        "mean": float(np.mean(phase_data)) if phase_data.size else None,
        "std": float(np.std(phase_data)) if phase_data.size else None,
        "data_info": payload.get("data_info"),
    }


def plot_time_domain(
    phase_data: np.ndarray,
    sample_rate: float,
    start_time: Optional[float] = None,
    duration: Optional[float] = None,
    downsample_factor: int = 1,
    remove_mean: bool = True,
    normalize: bool = False,
    ax=None,
    title: str = "Tab1 Time-Domain Waveform",
):
    """Plot one time-domain waveform and return the axis object."""
    times, values, effective_rate = preprocess_waveform(
        phase_data=phase_data,
        sample_rate=sample_rate,
        remove_mean=remove_mean,
        normalize=normalize,
        start_time=start_time,
        duration=duration,
        downsample_factor=downsample_factor,
    )

    if ax is None:
        _, ax = plt.subplots(figsize=(12, 4))

    ax.plot(times, values, linewidth=0.8)
    ax.set_title(f"{title} ({effective_rate:.1f} Hz)")
    ax.set_xlabel("Time (s)")
    ax.set_ylabel("Amplitude")
    ax.grid(True, alpha=0.3)
    return ax


def plot_psd(
    phase_data: np.ndarray,
    sample_rate: float,
    remove_mean: bool = True,
    ax=None,
    title: str = "Tab1 PSD",
):
    """Plot the waveform PSD and return the axis object."""
    freqs, psd = compute_psd(
        phase_data=phase_data,
        sample_rate=sample_rate,
        remove_mean=remove_mean,
    )

    if ax is None:
        _, ax = plt.subplots(figsize=(12, 4))

    ax.plot(freqs, psd, linewidth=0.8)
    ax.set_title(title)
    ax.set_xlabel("Frequency (Hz)")
    ax.set_ylabel("PSD")
    ax.grid(True, alpha=0.3)
    return ax


def plot_filtered_time_domain(
    phase_data: np.ndarray,
    sample_rate: float,
    lowcut: Optional[float] = None,
    highcut: Optional[float] = None,
    start_time: Optional[float] = None,
    duration: Optional[float] = None,
    downsample_factor: int = 1,
    normalize: bool = False,
    ax=None,
    title: str = "Filtered Tab1 Time-Domain Waveform",
):
    """Filter first, then plot the filtered waveform in the time domain."""
    filtered_values = apply_frequency_filter(
        phase_data=phase_data,
        sample_rate=sample_rate,
        lowcut=lowcut,
        highcut=highcut,
        remove_mean=True,
    )
    return plot_time_domain(
        phase_data=filtered_values,
        sample_rate=sample_rate,
        start_time=start_time,
        duration=duration,
        downsample_factor=downsample_factor,
        remove_mean=False,
        normalize=normalize,
        ax=ax,
        title=title,
    )


def plot_tab1_analysis(
    phase_data: np.ndarray,
    sample_rate: float,
    lowcut: Optional[float] = None,
    highcut: Optional[float] = None,
    start_time: Optional[float] = None,
    duration: Optional[float] = None,
    downsample_factor: int = 1,
    normalize: bool = False,
):
    """Plot original time-domain, PSD, and filtered time-domain waveforms together."""
    fig, axes = plt.subplots(3, 1, figsize=(12, 12))
    plot_time_domain(
        phase_data=phase_data,
        sample_rate=sample_rate,
        start_time=start_time,
        duration=duration,
        downsample_factor=downsample_factor,
        normalize=normalize,
        ax=axes[0],
        title="Original Time-Domain Waveform",
    )
    plot_psd(
        phase_data=phase_data,
        sample_rate=sample_rate,
        ax=axes[1],
        title="Original PSD",
    )
    plot_filtered_time_domain(
        phase_data=phase_data,
        sample_rate=sample_rate,
        lowcut=lowcut,
        highcut=highcut,
        start_time=start_time,
        duration=duration,
        downsample_factor=downsample_factor,
        normalize=normalize,
        ax=axes[2],
        title=f"Filtered Time-Domain Waveform ({lowcut}, {highcut} Hz)",
    )
    fig.tight_layout()
    return fig, axes
