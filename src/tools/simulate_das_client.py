"""Standalone DAS client simulator for Tab3 integration testing.

This script stays outside `src/` on purpose. It connects to the Tab3 DAS TCP
server and sends packets using the agreed protocol:

    >IIIId + big-endian float64 payload

Usage:
    python tools/simulate_das_client.py --host 127.0.0.1 --port 3678
"""

from __future__ import annotations

import argparse
import socket
import struct
import time
from typing import Optional

import numpy as np


HEADER_STRUCT = struct.Struct(">IIIId")


def build_payload(
    comm_count: int,
    sample_rate_hz: int,
    channel_count: int,
    packet_duration_seconds: float,
    amplitude: float,
    base_frequency_hz: float,
    pulse_every_packets: int,
) -> bytes:
    """Build one DAS packet as big-endian bytes."""
    samples_per_channel = int(round(sample_rate_hz * packet_duration_seconds))
    time_axis = np.arange(samples_per_channel, dtype=np.float64) / float(sample_rate_hz)

    matrix = np.zeros((channel_count, samples_per_channel), dtype=np.float64)
    active_channel = comm_count % max(channel_count, 1)
    pulse_enabled = pulse_every_packets > 0 and (comm_count % pulse_every_packets == 0)

    for channel_index in range(channel_count):
        frequency = base_frequency_hz + (channel_index % 5) * 2.5
        signal = amplitude * np.sin(2.0 * np.pi * frequency * time_axis)
        if pulse_enabled and channel_index == active_channel:
            center = samples_per_channel // 2
            width = max(8, samples_per_channel // 20)
            pulse = np.zeros_like(signal)
            start = max(0, center - width // 2)
            end = min(samples_per_channel, center + width // 2)
            pulse[start:end] = amplitude * 6.0
            signal = signal + pulse
        matrix[channel_index] = signal

    payload = np.asarray(matrix.reshape(-1), dtype=">f8").tobytes()
    header = HEADER_STRUCT.pack(
        comm_count,
        sample_rate_hz,
        channel_count,
        len(payload),
        packet_duration_seconds,
    )
    return header + payload


def send_packets(
    host: str,
    port: int,
    sample_rate_hz: int,
    channel_count: int,
    packet_duration_seconds: float,
    packet_count: int,
    amplitude: float,
    base_frequency_hz: float,
    pulse_every_packets: int,
    connect_retry_seconds: float = 0.5,
    startup_delay_seconds: float = 0.0,
    inter_packet_sleep_seconds: Optional[float] = None,
) -> int:
    """Connect to the DAS server and send packets."""
    if inter_packet_sleep_seconds is None:
        inter_packet_sleep_seconds = packet_duration_seconds

    if startup_delay_seconds > 0.0:
        time.sleep(startup_delay_seconds)

    last_error: Optional[Exception] = None
    for _ in range(20):
        try:
            with socket.create_connection((host, port), timeout=3.0) as sock:
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                for comm_count in range(packet_count):
                    packet_bytes = build_payload(
                        comm_count=comm_count,
                        sample_rate_hz=sample_rate_hz,
                        channel_count=channel_count,
                        packet_duration_seconds=packet_duration_seconds,
                        amplitude=amplitude,
                        base_frequency_hz=base_frequency_hz,
                        pulse_every_packets=pulse_every_packets,
                    )
                    sock.sendall(packet_bytes)
                    if inter_packet_sleep_seconds > 0.0:
                        time.sleep(inter_packet_sleep_seconds)
                return packet_count
        except OSError as exc:
            last_error = exc
            time.sleep(connect_retry_seconds)
    raise RuntimeError(f"Failed to connect to DAS server at {host}:{port}: {last_error}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Standalone DAS TCP client simulator")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=3678)
    parser.add_argument("--sample-rate", type=int, default=4000, dest="sample_rate_hz")
    parser.add_argument("--channels", type=int, default=32, dest="channel_count")
    parser.add_argument("--packet-duration", type=float, default=0.2, dest="packet_duration_seconds")
    parser.add_argument("--packets", type=int, default=20, dest="packet_count")
    parser.add_argument("--amplitude", type=float, default=1.0)
    parser.add_argument("--base-frequency", type=float, default=25.0, dest="base_frequency_hz")
    parser.add_argument("--pulse-every", type=int, default=5, dest="pulse_every_packets")
    parser.add_argument("--startup-delay", type=float, default=0.0, dest="startup_delay_seconds")
    parser.add_argument("--sleep", type=float, default=None, dest="inter_packet_sleep_seconds")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    sent = send_packets(
        host=args.host,
        port=args.port,
        sample_rate_hz=args.sample_rate_hz,
        channel_count=args.channel_count,
        packet_duration_seconds=args.packet_duration_seconds,
        packet_count=args.packet_count,
        amplitude=args.amplitude,
        base_frequency_hz=args.base_frequency_hz,
        pulse_every_packets=args.pulse_every_packets,
        startup_delay_seconds=args.startup_delay_seconds,
        inter_packet_sleep_seconds=args.inter_packet_sleep_seconds,
    )
    print(
        "SIM_OK "
        f"host={args.host} port={args.port} packets={sent} "
        f"channels={args.channel_count} sample_rate={args.sample_rate_hz}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
