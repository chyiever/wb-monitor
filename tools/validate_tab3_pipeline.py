"""Headless validation for Tab3 DAS receive and plot preparation.

This script keeps validation outside `src/`. It starts `DASTCPServer` and
`DASPlotWorker`, sends simulated packets, and checks that:

- TCP reception works
- packet header parsing works
- 2D matrix reconstruction works
- plot payloads are produced
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

from PyQt5.QtCore import QCoreApplication, QTimer

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from das_tab3 import DASTCPServer  # noqa: E402
from das_tab3.das_plot_worker import DASPlotWorker  # noqa: E402
from das_tab3.das_types import DASParsedPacket, DASRawPacket  # noqa: E402
from simulate_das_client import send_packets  # noqa: E402


def main() -> int:
    app = QCoreApplication(sys.argv)
    server = DASTCPServer(ip="127.0.0.1", port=3678)
    plot_worker = DASPlotWorker()
    plot_worker.update_settings(
        {
            "das_channel": 3,
            "display_seconds": 1.0,
            "channel_start": 0,
            "channel_end": 15,
            "time_downsample": 1,
            "space_downsample": 1,
            "apply_filter": False,
        }
    )

    results = {
        "packets_received": 0,
        "plot_payloads": 0,
        "last_shape": None,
        "last_curve_points": 0,
        "error": None,
    }

    def handle_packet(raw_packet: DASRawPacket) -> None:
        header = raw_packet.header
        samples_per_channel = int(round(header.sample_rate_hz * header.packet_duration_seconds))
        matrix = raw_packet.data_1d.reshape(header.channel_count, samples_per_channel)
        parsed = DASParsedPacket(
            header=header,
            matrix=matrix,
            packet_start_time=header.comm_count * header.packet_duration_seconds,
            packet_end_time=(header.comm_count + 1) * header.packet_duration_seconds,
        )
        results["packets_received"] += 1
        results["last_shape"] = matrix.shape
        plot_worker.enqueue_packet(parsed)

    def handle_plot_payload(payload: dict) -> None:
        results["plot_payloads"] += 1
        results["last_curve_points"] = len(payload.get("das_curve_values", []))
        if results["plot_payloads"] >= 3:
            QTimer.singleShot(0, app.quit)

    def handle_error(message: str) -> None:
        results["error"] = message
        QTimer.singleShot(0, app.quit)

    server.packet_received.connect(handle_packet)
    server.error_occurred.connect(handle_error)
    plot_worker.plot_payload_ready.connect(handle_plot_payload)

    if not server.start_server():
        print("VALIDATION_FAIL failed_to_start_server")
        return 1

    plot_worker.start()

    sender_thread = threading.Thread(
        target=send_packets,
        kwargs={
            "host": "127.0.0.1",
            "port": 3678,
            "sample_rate_hz": 4000,
            "channel_count": 16,
            "packet_duration_seconds": 0.2,
            "packet_count": 6,
            "amplitude": 1.0,
            "base_frequency_hz": 25.0,
            "pulse_every_packets": 2,
            "startup_delay_seconds": 0.4,
            "inter_packet_sleep_seconds": 0.05,
        },
        daemon=True,
    )
    sender_thread.start()

    timeout = QTimer()
    timeout.setSingleShot(True)
    timeout.timeout.connect(app.quit)
    timeout.start(10000)

    app.exec_()

    server.stop_server()
    plot_worker.stop()
    plot_worker.wait(3000)
    sender_thread.join(timeout=1.0)

    if results["error"]:
        print(f"VALIDATION_FAIL error={results['error']}")
        return 1
    if results["packets_received"] < 3:
        print(f"VALIDATION_FAIL packets_received={results['packets_received']}")
        return 1
    if results["plot_payloads"] < 3:
        print(f"VALIDATION_FAIL plot_payloads={results['plot_payloads']}")
        return 1

    print(
        "VALIDATION_OK "
        f"packets_received={results['packets_received']} "
        f"plot_payloads={results['plot_payloads']} "
        f"last_shape={results['last_shape']} "
        f"last_curve_points={results['last_curve_points']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
