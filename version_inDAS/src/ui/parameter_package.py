"""Portable authenticated parameter container (not a password/access-control system)."""
import hashlib
import json
import math
import os
from pathlib import Path
import zlib

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


MAGIC = b'WBPAR\x00\x01\x00'
MAX_BYTES = 8 * 1024 * 1024
# App-owned portable key: prevents casual text inspection, not reverse engineering.
_KEY = hashlib.sha256(b'wb-monitor/inDAS/portable-parameter-container/v1').digest()


def write_parameters(path, parameters):
    path = Path(path)
    data = json.dumps({'format': 'wb-monitor-inDAS', 'version': 1, 'parameters': parameters},
                      ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')
    if len(data) > MAX_BYTES: raise ValueError('参数包过大')
    nonce = os.urandom(12)
    payload = MAGIC + nonce + AESGCM(_KEY).encrypt(nonce, zlib.compress(data), MAGIC)
    temporary = path.with_name(path.name + '.tmp')
    try:
        with temporary.open('wb') as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(str(temporary), str(path))
    finally:
        if temporary.exists(): temporary.unlink()


def read_parameters(path):
    with Path(path).open('rb') as stream: payload = stream.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES or len(payload) < len(MAGIC) + 28 or not payload.startswith(MAGIC):
        raise ValueError('不是受支持的 wb-monitor .par 参数文件')
    offset = len(MAGIC)
    try:
        compressed = AESGCM(_KEY).decrypt(payload[offset:offset + 12], payload[offset + 12:], MAGIC)
        decoder = zlib.decompressobj()
        data = decoder.decompress(compressed, MAX_BYTES + 1)
        if len(data) > MAX_BYTES or not decoder.eof or decoder.unused_data:
            raise ValueError('参数内容损坏或过大')
        document = json.loads(data.decode('utf-8'))
    except (InvalidTag, zlib.error, UnicodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError('参数包损坏、被修改或版本不兼容，未导入任何参数') from exc
    if not isinstance(document, dict) or document.get('format') != 'wb-monitor-inDAS' or document.get('version') != 1:
        raise ValueError('不支持的参数包版本')
    parameters = document.get('parameters')
    if not isinstance(parameters, dict): raise ValueError('参数结构无效')
    return parameters


def validate_structure(value, template, path='parameters'):
    """Reject wrong types/unknown/missing fields before touching any live widgets."""
    if isinstance(template, dict):
        if not isinstance(value, dict) or set(value) != set(template): raise ValueError(f'{path} 字段不完整或不兼容')
        for key in template: validate_structure(value[key], template[key], path + '.' + key)
    elif isinstance(template, (tuple, list)):
        if not isinstance(value, (tuple, list)) or len(value) > 4096: raise ValueError(f'{path} 列表无效')
        if template:
            for element in value: validate_structure(element, template[0], path)
    elif isinstance(template, bool):
        if type(value) is not bool: raise ValueError(f'{path} 应为开关值')
    elif isinstance(template, (int, float)):
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value): raise ValueError(f'{path} 数值无效')
        if isinstance(template, int) and int(value) != value: raise ValueError(f'{path} 应为整数')
    elif isinstance(template, str):
        if not isinstance(value, str) or len(value) > 8192 or '\x00' in value: raise ValueError(f'{path} 文本无效')
    elif value is not None:
        raise ValueError(f'{path} 类型无效')
