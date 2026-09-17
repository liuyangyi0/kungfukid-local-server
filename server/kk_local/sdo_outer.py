"""Offline candidate for sdologin.exe's OUTER credential envelope only.

Sources: 434320 (20 decimal digits -> 8 key bytes), 434450 (EDE CBC,
PKCS#7, C-string key expansion), 434240 (Base64). This does NOT unwrap
CSafeStore's inner RSA packet and must not be used to authenticate users.
No original DLL is loaded and no network or password logging is performed.
"""
import base64
import binascii
import re

from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes


_MASK = bytes.fromhex('dd d6 d8 ea fd f6 f8 ca')
# SDK runtime 0x529120, EXP-20260913-1930 attempt-a05/outer-globals.json.
# Disk bytes differed in the first two positions; do not use them as live globals.
_IV = bytes(b ^ i for i, b in enumerate(bytes.fromhex('32 32 36 36 32 32 3e 3e')))


def derive_key(dynamic_key: str) -> bytes:
    """Canonical digit-only subset; atol results truncate to 16 bits in source."""
    if not isinstance(dynamic_key, str) or re.fullmatch(r'[0-9]{20}', dynamic_key) is None:
        raise ValueError('expected_20_decimal_digits')
    words = b''.join((int(dynamic_key[i:i+5]) & 0xffff).to_bytes(2, 'little')
                     for i in range(0, 20, 5))
    transformed = bytes((((b ^ m) << 4) | ((b ^ m) >> 4)) & 255
                        for b, m in zip(words, _MASK))
    # Source appends a zero terminator, then strlen: embedded zero is significant.
    prefix = transformed.split(b'\0', 1)[0]
    return prefix + bytes([8 - len(prefix) % 8]) * (24 - len(prefix))


def wrap_outer(payload: bytes, dynamic_key: str) -> str:
    if not isinstance(payload, bytes):
        raise TypeError('payload_must_be_bytes')
    padding = 8 - len(payload) % 8
    cipher = Cipher(TripleDES(derive_key(dynamic_key)), modes.CBC(_IV)).encryptor()
    encrypted = cipher.update(payload + bytes([padding]) * padding) + cipher.finalize()
    return base64.b64encode(encrypted).decode('ascii')


def unwrap_outer(encoded: str, dynamic_key: str) -> bytes:
    try:
        encrypted = base64.b64decode(encoded, validate=True)
    except (ValueError, TypeError, binascii.Error):
        raise ValueError('invalid_outer_base64') from None
    if not encrypted or len(encrypted) % 8:
        raise ValueError('invalid_outer_block_size')
    cipher = Cipher(TripleDES(derive_key(dynamic_key)), modes.CBC(_IV)).decryptor()
    padded = cipher.update(encrypted) + cipher.finalize()
    count = padded[-1]
    if not 1 <= count <= 8 or padded[-count:] != bytes([count]) * count:
        raise ValueError('invalid_outer_padding')
    return padded[:-count]
