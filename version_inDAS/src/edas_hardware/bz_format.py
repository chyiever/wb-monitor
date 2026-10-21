"""Packet format helpers for Bitshuffle + Zstd .bz data files."""
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterator, Optional, Tuple, Union

import numpy as np

try:
    import zstandard as zstd
except ImportError:  # pragma: no cover - exercised on machines without optional dependency
    zstd = None


FILE_MAGIC = b"BZF1"
PACKET_MAGIC = b"BZS1"
VERSION = 1
DTYPE_INT32 = 1
ENDIAN_LITTLE = 1
INT32_ITEMSIZE = 4
INT32_BITS = 32

# magic, version, header_size, created_timestamp_ns,
# scan_rate_hz, points_per_frame, channel_num, data_source,
# storage_downsample_factor, packet_frames, file_duration_s,
# zstd_level, bitshuffle_block_values
FILE_HEADER_STRUCT = struct.Struct("<4sHHQIIIIIIIiI")

# Matches the format recommended in the compression guide.
PACKET_HEADER_STRUCT = struct.Struct("<4sHHQQIIIHHIiQQQQII")


@dataclass
class RawPacket:
    packet_index: int
    timestamp_ns: int
    scan_rate_hz: int
    points_per_frame: int
    frames: int
    samples: np.ndarray


@dataclass
class CompressedPacket:
    packet_index: int
    header: bytes
    payload: bytes
    metrics: Dict[str, float]


class NumpyBitshuffleBackend:
    """Reversible NumPy bit-plane shuffle for int32 packets.

    The project can replace this class with a native SIMD backend later without
    changing the surrounding packet or Zstd format. The fallback is deliberately
    simple and deterministic so offline notebooks can decode files without extra
    native packages.
    """

    def shuffle_int32(self, array: np.ndarray, block_values: int) -> bytes:
        samples = np.asarray(array, dtype="<i4")
        samples = np.ascontiguousarray(samples.reshape(-1))
        block_values = max(1, int(block_values))

        out = bytearray(samples.nbytes)
        out_offset = 0
        for start in range(0, samples.size, block_values):
            block = samples[start:start + block_values]
            n_values = int(block.size)
            byte_matrix = block.view(np.uint8).reshape(n_values, INT32_ITEMSIZE)
            bit_matrix = np.unpackbits(byte_matrix, axis=1, bitorder="little")
            shuffled = np.packbits(bit_matrix.T.reshape(-1), bitorder="little")
            byte_count = n_values * INT32_ITEMSIZE
            out[out_offset:out_offset + byte_count] = shuffled.tobytes()
            out_offset += byte_count

        return bytes(out)

    def unshuffle_int32(self, payload: bytes, item_count: int, block_values: int) -> np.ndarray:
        item_count = int(item_count)
        block_values = max(1, int(block_values))
        payload_array = np.frombuffer(payload, dtype=np.uint8)
        expected_bytes = item_count * INT32_ITEMSIZE
        if payload_array.size != expected_bytes:
            raise ValueError(f"bad shuffled byte count: {payload_array.size} != {expected_bytes}")

        result = np.empty(item_count, dtype="<i4")
        result_bytes = result.view(np.uint8).reshape(item_count, INT32_ITEMSIZE)
        in_offset = 0
        out_offset = 0
        while out_offset < item_count:
            n_values = min(block_values, item_count - out_offset)
            byte_count = n_values * INT32_ITEMSIZE
            block_bytes = payload_array[in_offset:in_offset + byte_count]
            shuffled_bits = np.unpackbits(block_bytes, bitorder="little")[:n_values * INT32_BITS]
            bit_matrix = shuffled_bits.reshape(INT32_BITS, n_values).T
            raw_bytes = np.packbits(
                bit_matrix.reshape(n_values, INT32_ITEMSIZE, 8),
                axis=2,
                bitorder="little",
            ).reshape(n_values, INT32_ITEMSIZE)
            result_bytes[out_offset:out_offset + n_values, :] = raw_bytes
            in_offset += byte_count
            out_offset += n_values

        return result


def pack_bz_file_header(
    *,
    scan_rate_hz: int,
    points_per_frame: int,
    channel_num: int,
    data_source: int,
    storage_downsample_factor: int,
    packet_frames: int,
    file_duration_s: int,
    zstd_level: int,
    bitshuffle_block_values: int,
    created_timestamp_ns: Optional[int] = None,
) -> bytes:
    created = int(time.time_ns() if created_timestamp_ns is None else created_timestamp_ns)
    return FILE_HEADER_STRUCT.pack(
        FILE_MAGIC,
        VERSION,
        FILE_HEADER_STRUCT.size,
        created,
        int(scan_rate_hz),
        int(points_per_frame),
        int(channel_num),
        int(data_source),
        int(storage_downsample_factor),
        int(packet_frames),
        int(file_duration_s),
        int(zstd_level),
        int(bitshuffle_block_values),
    )


def unpack_bz_file_header(header: bytes) -> Dict[str, int]:
    if len(header) < FILE_HEADER_STRUCT.size:
        raise ValueError("incomplete .bz file header")
    values = FILE_HEADER_STRUCT.unpack(header[:FILE_HEADER_STRUCT.size])
    magic = values[0]
    if magic != FILE_MAGIC:
        raise ValueError(f"bad .bz file magic: {magic!r}")
    return {
        "magic": magic,
        "version": values[1],
        "header_size": values[2],
        "created_timestamp_ns": values[3],
        "scan_rate_hz": values[4],
        "points_per_frame": values[5],
        "channel_num": values[6],
        "data_source": values[7],
        "storage_downsample_factor": values[8],
        "packet_frames": values[9],
        "file_duration_s": values[10],
        "zstd_level": values[11],
        "bitshuffle_block_values": values[12],
    }


def pack_packet_header(
    *,
    packet: RawPacket,
    item_count: int,
    raw_bytes: int,
    shuffled_bytes: int,
    compressed_bytes: int,
    block_values: int,
    zstd_level: int,
    raw_crc32: int,
    payload_crc32: int,
) -> bytes:
    return PACKET_HEADER_STRUCT.pack(
        PACKET_MAGIC,
        VERSION,
        PACKET_HEADER_STRUCT.size,
        int(packet.packet_index),
        int(packet.timestamp_ns),
        int(packet.scan_rate_hz),
        int(packet.points_per_frame),
        int(packet.frames),
        DTYPE_INT32,
        ENDIAN_LITTLE,
        int(block_values),
        int(zstd_level),
        int(item_count),
        int(raw_bytes),
        int(shuffled_bytes),
        int(compressed_bytes),
        int(raw_crc32),
        int(payload_crc32),
    )


def unpack_packet_header(header: bytes) -> Dict[str, int]:
    if len(header) < PACKET_HEADER_STRUCT.size:
        raise ValueError("incomplete packet header")
    values = PACKET_HEADER_STRUCT.unpack(header[:PACKET_HEADER_STRUCT.size])
    magic = values[0]
    if magic != PACKET_MAGIC:
        raise ValueError(f"bad packet magic: {magic!r}")
    return {
        "magic": magic,
        "version": values[1],
        "header_size": values[2],
        "packet_index": values[3],
        "timestamp_ns": values[4],
        "scan_rate_hz": values[5],
        "points_per_frame": values[6],
        "frames": values[7],
        "dtype_code": values[8],
        "endian_code": values[9],
        "bitshuffle_block_values": values[10],
        "zstd_level": values[11],
        "item_count": values[12],
        "raw_bytes": values[13],
        "shuffled_bytes": values[14],
        "compressed_bytes": values[15],
        "raw_crc32": values[16],
        "payload_crc32": values[17],
    }


class BitshuffleZstdCompressor:
    def __init__(self, bitshuffle_backend=None, *, zstd_level: int = 3, block_values: int = 65536):
        if zstd is None:
            raise RuntimeError(
                "zstandard is not installed. Install it with 'pip install zstandard' "
                "before using Bitshuffle+Zstd .bz storage."
            )
        self.bitshuffle = bitshuffle_backend or NumpyBitshuffleBackend()
        self.zstd_level = int(zstd_level)
        self.block_values = max(1, int(block_values))
        self.cctx = zstd.ZstdCompressor(
            level=self.zstd_level,
            write_content_size=True,
            write_checksum=True,
        )

    def compress_packet(self, packet: RawPacket) -> CompressedPacket:
        samples = np.asarray(packet.samples, dtype="<i4")
        if not samples.flags["C_CONTIGUOUS"]:
            samples = np.ascontiguousarray(samples)

        expected_shape = (int(packet.frames), int(packet.points_per_frame))
        if samples.shape != expected_shape:
            raise ValueError(f"bad packet shape: {samples.shape} != {expected_shape}")

        item_count = int(samples.size)
        raw_bytes = item_count * INT32_ITEMSIZE

        t0 = time.perf_counter()
        shuffled = self.bitshuffle.shuffle_int32(samples, self.block_values)
        t1 = time.perf_counter()
        payload = self.cctx.compress(shuffled)
        t2 = time.perf_counter()

        raw_crc32 = zlib.crc32(samples.view(np.uint8)) & 0xFFFFFFFF
        payload_crc32 = zlib.crc32(payload) & 0xFFFFFFFF
        header = pack_packet_header(
            packet=packet,
            item_count=item_count,
            raw_bytes=raw_bytes,
            shuffled_bytes=len(shuffled),
            compressed_bytes=len(payload),
            block_values=self.block_values,
            zstd_level=self.zstd_level,
            raw_crc32=raw_crc32,
            payload_crc32=payload_crc32,
        )
        metrics = {
            "packet_index": float(packet.packet_index),
            "frames": float(packet.frames),
            "points_per_frame": float(packet.points_per_frame),
            "raw_bytes": float(raw_bytes),
            "compressed_bytes": float(len(payload)),
            "compression_ratio": raw_bytes / len(payload) if payload else float("inf"),
            "bitshuffle_ms": (t1 - t0) * 1000.0,
            "zstd_ms": (t2 - t1) * 1000.0,
            "compress_ms": (t2 - t0) * 1000.0,
        }
        return CompressedPacket(packet.packet_index, header, payload, metrics)


def decompress_packet(header: Union[bytes, Dict[str, int]], payload: bytes, *, verify_crc: bool = True) -> np.ndarray:
    if zstd is None:
        raise RuntimeError("zstandard is not installed. Install it with 'pip install zstandard'.")
    info = unpack_packet_header(header) if isinstance(header, (bytes, bytearray)) else dict(header)
    if info["dtype_code"] != DTYPE_INT32 or info["endian_code"] != ENDIAN_LITTLE:
        raise ValueError("unsupported packet dtype or endian code")
    if int(info["compressed_bytes"]) != len(payload):
        raise ValueError(f"bad payload size: {len(payload)} != {info['compressed_bytes']}")
    if verify_crc and (zlib.crc32(payload) & 0xFFFFFFFF) != int(info["payload_crc32"]):
        raise ValueError(f"payload CRC mismatch in packet {info['packet_index']}")

    dctx = zstd.ZstdDecompressor()
    shuffled = dctx.decompress(payload, max_output_size=int(info["shuffled_bytes"]))
    if len(shuffled) != int(info["shuffled_bytes"]):
        raise ValueError(f"bad shuffled byte count: {len(shuffled)} != {info['shuffled_bytes']}")

    backend = NumpyBitshuffleBackend()
    samples = backend.unshuffle_int32(
        shuffled,
        item_count=int(info["item_count"]),
        block_values=int(info["bitshuffle_block_values"]),
    )
    if verify_crc and (zlib.crc32(samples.view(np.uint8)) & 0xFFFFFFFF) != int(info["raw_crc32"]):
        raise ValueError(f"raw CRC mismatch in packet {info['packet_index']}")
    return samples.reshape(int(info["frames"]), int(info["points_per_frame"]))


def iter_bz_packets(path: Union[str, Path], *, verify_crc: bool = True) -> Iterator[Tuple[Dict[str, int], Dict[str, int], np.ndarray]]:
    path = Path(path)
    with path.open("rb") as f:
        prefix = f.read(4)
        if not prefix:
            return
        if prefix == FILE_MAGIC:
            rest = f.read(FILE_HEADER_STRUCT.size - 4)
            file_info = unpack_bz_file_header(prefix + rest)
            extra = int(file_info["header_size"]) - FILE_HEADER_STRUCT.size
            if extra > 0:
                f.read(extra)
        elif prefix == PACKET_MAGIC:
            file_info = {}
            f.seek(0)
        else:
            raise ValueError(f"unrecognized .bz file magic: {prefix!r}")

        while True:
            header = f.read(PACKET_HEADER_STRUCT.size)
            if not header:
                break
            if len(header) != PACKET_HEADER_STRUCT.size:
                raise ValueError("truncated packet header")
            packet_info = unpack_packet_header(header)
            extra = int(packet_info["header_size"]) - PACKET_HEADER_STRUCT.size
            if extra > 0:
                f.read(extra)
            payload = f.read(int(packet_info["compressed_bytes"]))
            if len(payload) != int(packet_info["compressed_bytes"]):
                raise ValueError(f"truncated packet payload at index {packet_info['packet_index']}")
            yield file_info, packet_info, decompress_packet(packet_info, payload, verify_crc=verify_crc)


__all__ = [
    "FILE_MAGIC",
    "PACKET_MAGIC",
    "FILE_HEADER_STRUCT",
    "PACKET_HEADER_STRUCT",
    "RawPacket",
    "CompressedPacket",
    "NumpyBitshuffleBackend",
    "BitshuffleZstdCompressor",
    "pack_bz_file_header",
    "unpack_bz_file_header",
    "pack_packet_header",
    "unpack_packet_header",
    "decompress_packet",
    "iter_bz_packets",
]
