"""Three distinct wire formats; never auto-detect one as another.

Game: docs/protocol/gfxz-client-protocol-with-battle.md sections3–5.
SDLogin and SDP2P: existing client-lab native consumers, September12 experiments.
Block encoding is NOT authentication/encryption suitable for a public service.
"""
from dataclasses import dataclass
import struct

KEYS = tuple(int.from_bytes(s.encode('ascii'), 'little') for s in (
    '00Na~1fd', '00xxgg!@', '<>>>SDSD', '012123AS', '<>>>sd!s',
    'dasdasds', '00xxLL:>', '<>>>$#@@', '01CscaSD', '<>>>*s^6', 'kldk0MJI'))
MASK64 = (1 << 64) - 1
MAX_FRAME = 1024 * 1024


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Message:
    id: int
    payload: bytes = b''


def blocks(data: bytes, key: int, decode: bool = False) -> bytes:
    if not 0 <= key < len(KEYS) or len(data) % 8:
        raise ProtocolError('invalid block key/alignment')
    out = bytearray(len(data))
    for i in range(0, len(data), 8):
        v = int.from_bytes(data[i:i + 8], 'little')
        if decode:
            v ^= KEYS[key]
            v = (v >> 3) | ((v << 61) & MASK64)
        else:
            v = (((v << 3) & MASK64) | (v >> 61)) ^ KEYS[key]
        struct.pack_into('<Q', out, i, v)
    return bytes(out)


def encode_game(message: Message, key: int = 0) -> bytes:
    n = (len(message.payload) + 7) & ~7
    if n + 24 > MAX_FRAME or not 0 <= message.id <= 0xffffffff:
        raise ProtocolError('frame too large or invalid ID')
    inner = struct.pack('<IHI', message.id, key, len(message.payload))
    inner += blocks(message.payload.ljust(n, b'\0'), key) + bytes(6)
    return struct.pack('<HHI', 0xaaee, (len(inner) ^ 0xbbcc) & 0x88aa,
                       len(inner)) + blocks(inner, 0)


class GameDecoder:
    def __init__(self,header_validator=None):
        self.buffer = bytearray()
        self.header_validator=header_validator

    def feed(self, data: bytes) -> list[Message]:
        # Network reader supplies bounded chunks; an incomplete frame is bounded.
        if len(self.buffer) + len(data) > MAX_FRAME + 65536:
            raise ProtocolError('receive buffer limit')
        self.buffer.extend(data)
        result = []
        while len(self.buffer) >= 8:
            magic, mask, n = struct.unpack_from('<HHI', self.buffer)
            if magic != 0xaaee or n < 16 or n % 8 or n + 8 > MAX_FRAME:
                raise ProtocolError('invalid game header')
            if mask != (n ^ 0xbbcc) & 0x88aa:
                raise ProtocolError('invalid game length mask')
            if self.header_validator and len(self.buffer)>=24:
                early=blocks(bytes(self.buffer[8:24]),0,True)
                early_id,_,early_size=struct.unpack_from('<IHI',early)
                if 16+((early_size+7)&~7)!=n:raise ProtocolError('inner length mismatch')
                self.header_validator(early_id,early_size)
            if len(self.buffer) < n + 8:
                break
            body = blocks(bytes(self.buffer[8:8 + n]), 0, True)
            ident, key, length = struct.unpack_from('<IHI', body)
            aligned = (length + 7) & ~7
            if 16 + aligned != n:
                raise ProtocolError('inner length mismatch')
            payload = blocks(body[10:10 + aligned], key, True)[:length]
            if ident == 0 and payload:
                raise ProtocolError('nonempty heartbeat')
            result.append(Message(ident, payload))
            del self.buffer[:n + 8]
        return result

    def eof(self):
        if self.buffer:
            raise ProtocolError('incomplete game frame at EOF')


def text_be(value: str) -> bytes:
    data = value.encode('ascii')
    if len(data) > 65535:
        raise ProtocolError('text too long')
    return struct.pack('>H', len(data)) + data


def encode_login(message: Message) -> bytes:
    body = struct.pack('<H', message.id) + message.payload
    if len(body) > 4096:
        raise ProtocolError('login body limit')
    return struct.pack('<HHHBB', 0xaaee, (len(body) ^ 0xffdd) & 0x88aa,
                       len(body), 0, 0) + body


async def read_login(reader):
    head = await reader.readexactly(8)
    magic, mask, n, flags, padding = struct.unpack('<HHHBB', head)
    if magic != 0xaaee or mask != (n ^ 0xffdd) & 0x88aa or n < 2:
        raise ProtocolError('invalid login header')
    if n + padding > 4096 or flags not in (0, 1):
        raise ProtocolError('unsupported login flags/size')
    body = await reader.readexactly(n + padding)
    return flags, body[padding:]


def sdp_header(data: bytes):
    if len(data) < 24 or len(data) > 32768:
        raise ProtocolError('invalid SDP2P size')
    version, ident, session, seq, source, dest, reserved, flags, extra = \
        struct.unpack_from('<HHIIIIHBB', data)
    if version != 1 or 24 + extra > len(data):
        raise ProtocolError('invalid SDP2P header')
    return ident, session, source, dest, data[24 + extra:], extra


def sdp_reply(ident: int, session: int, player: int, address: bytes, port: int):
    header = struct.pack('<HHIIIIHBB', 1, ident, session, 0, 0, player, 0, 0, 0)
    if ident == 1002:
        return header + struct.pack('>III', 0, player, session) + address + struct.pack('>H', port)
    if ident == 1014:
        return header + bytes(4)
    raise ProtocolError('unsupported SDP2P response')
