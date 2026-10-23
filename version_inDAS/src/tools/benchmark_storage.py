"""Opt-in real-disk benchmark; writes at most ~5.4 GB then removes its own temp files.

Run: python src/tools/benchmark_storage.py --directory E:/scratch --seconds 3
MB means decimal 1,000,000 bytes. fsync is included at file completion.
This measures a short synthetic workload, not long-term DMA acquisition.
"""
import argparse
import json
import sys
import tempfile
import threading
import time
from pathlib import Path

import numpy as np
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from edas_hardware.data_saver import BlockBasedFileSaver
from alignment.aligned_types import AlignedPacketFrame, DASSessionPacket
from das.joint_formats import save_bin


def run(directory, seconds, joint_format="bin"):
    das_dtype = "<f4" if joint_format == "bin32" else "<i4"
    block_frames, points, scan_rate = 5000, 3000, 50000
    raw = np.random.default_rng(7821).integers(-32767, 32768, block_frames * points, dtype=np.int32)
    matrix = np.ascontiguousarray(raw.reshape(block_frames, points).T, dtype=np.float32)
    matrix *= np.float32(np.pi / 32767)
    blocks = seconds * 10
    result = {'target_input_MB_s': 600, 'seconds': seconds, 'joint_format': joint_format, 'block_bytes': raw.nbytes,
              'directory': str(Path(directory).resolve()), 'modes': {}}
    for mode in ('das', 'joint', 'combined'):
        with tempfile.TemporaryDirectory(prefix='wb-storage-benchmark-', dir=directory) as temporary:
            folder = Path(temporary)
            saver = None
            peak_queue = 0
            producer_error = []
            def produce():
                nonlocal peak_queue
                try:
                    deadline = time.monotonic()
                    for _ in range(blocks):
                        if not saver.save_block(raw): producer_error.append('raw queue rejected a block')
                        peak_queue = max(peak_queue, saver.queue_size)
                        deadline += .1
                        time.sleep(max(0, deadline - time.monotonic()))
                except Exception as exc:
                    producer_error.append(str(exc))
            if mode in ('das', 'combined'):
                saver = BlockBasedFileSaver(str(folder / 'das'), buffer_size=8,
                    packet_frames=block_frames, file_frames_per_file=scan_rate)
                saver.start(scan_rate=scan_rate, points_per_frame=points, source_points_per_frame=points,
                            channel_num=1, data_source=0)
            started = time.monotonic()
            producer = None
            if saver:
                producer = threading.Thread(target=produce)
                producer.start()
            if mode in ('joint', 'combined'):
                for second in range(seconds):
                    frames = [AlignedPacketFrame(second * 10 + i, second + i / 10, .1,
                        das_packet=DASSessionPacket(second * 10 + i, .1, scan_rate, points, matrix,
                            raw_matrix=raw.reshape(block_frames, points).T),
                        fip_missing=True) for i in range(10)]
                    save_bin(frames, str(folder / 'joint'), 'synthetic-benchmark', das_dtype=das_dtype)
            if producer: producer.join()
            drain_started = time.monotonic()
            if saver: saver.stop()
            elapsed = time.monotonic() - started
            drain = time.monotonic() - drain_started
            written = sum(p.stat().st_size for p in folder.rglob('*.bin*') if not p.name.endswith('.part'))
            mode_result = {'elapsed_s': round(elapsed, 4), 'written_bytes': written,
                'output_MB_s': round(written / elapsed / 1e6, 2),
                'equivalent_input_MB_s': round(raw.nbytes * blocks / elapsed / 1e6, 2),
                'final_drain_s': round(drain, 4), 'peak_raw_queue_blocks': peak_queue,
                'producer_errors': producer_error}
            if saver:
                diag = saver.get_diagnostics_snapshot()
                mode_result.update(dropped_blocks=diag['dropped_blocks'], continuity_gap=diag['continuity_gap'],
                    frames_received=diag['frames_received'], frames_written=diag['frames_written'])
            result['modes'][mode] = mode_result
            print(json.dumps({mode: mode_result}, ensure_ascii=False), flush=True)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', required=True)
    parser.add_argument('--seconds', type=int, default=3)
    parser.add_argument('--report', type=Path)
    parser.add_argument('--joint-format', choices=['bin','bin32'], default='bin32')
    args = parser.parse_args()
    if not 1 <= args.seconds <= 30: parser.error('seconds must be 1..30')
    Path(args.directory).mkdir(parents=True, exist_ok=True)
    report = run(args.directory, args.seconds, args.joint_format)
    if args.report:
        args.report.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
