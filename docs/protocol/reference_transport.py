"""Loopback-only framed transport reference; no application authentication."""
from __future__ import annotations

import argparse
import asyncio
import contextlib
import math
import struct
import time
from collections.abc import Callable

MAGIC = 0xAAEE
MAX_FRAME = 1024 * 1024
MASK64 = (1 << 64) - 1
KEYS = tuple(int.from_bytes(value, "little") for value in (
    b"00Na~1fd", b"00xxgg!@", b"<>>>SDSD", b"012123AS",
    b"<>>>sd!s", b"dasdasds", b"00xxLL:>", b"<>>>$#@@",
    b"01CscaSD", b"<>>>*s^6", b"kldk0MJI",
))


class ProtocolError(ValueError):
    pass


def align8(size: int) -> int:
    return (size + 7) & ~7


def _blocks(data: bytes, key_index: int, *, decode: bool = False) -> bytes:
    if len(data) % 8 or not 0 <= key_index < len(KEYS):
        raise ProtocolError("invalid block length or key")
    result = bytearray(len(data))
    key = KEYS[key_index]
    for offset in range(0, len(data), 8):
        value = struct.unpack_from("<Q", data, offset)[0]
        if decode:
            value ^= key
            value = (value >> 3) | ((value << 61) & MASK64)
        else:
            value = (((value << 3) & MASK64) | (value >> 61)) ^ key
        struct.pack_into("<Q", result, offset, value)
    return bytes(result)


def _header_size(header: bytes | bytearray, max_frame: int) -> int:
    magic, length_mask, encoded_len = struct.unpack_from("<HHI", header)
    if magic != MAGIC or length_mask != ((encoded_len ^ 0xBBCC) & 0x88AA):
        raise ProtocolError("invalid frame header")
    if encoded_len < 16 or encoded_len % 8:
        raise ProtocolError("invalid encoded length")
    frame_len = 8 + encoded_len
    if frame_len > max_frame:
        raise ProtocolError("frame too large")
    return frame_len


def encode_frame(message_id: int, payload: bytes = b"", key_index: int = 0,
                 *, max_frame: int = MAX_FRAME) -> bytes:
    if not 0 <= message_id <= 0xFFFFFFFF:
        raise ProtocolError("invalid message id")
    if not 0 <= key_index < len(KEYS):
        raise ProtocolError("invalid payload key")
    payload_size = len(payload)
    if payload_size > 0xFFFFFFFF or 24 + align8(payload_size) > max_frame:
        raise ProtocolError("frame too large")
    padded = bytes(payload) + bytes(align8(payload_size) - payload_size)
    encoded_payload = _blocks(padded, key_index)
    inner = struct.pack("<IHI", message_id, key_index, payload_size)
    inner += encoded_payload + bytes(6)
    encoded = _blocks(inner, 0)
    return struct.pack("<HHI", MAGIC, (len(encoded) ^ 0xBBCC) & 0x88AA,
                       len(encoded)) + encoded


def decode_frame(frame: bytes, *, max_frame: int = MAX_FRAME) -> tuple[int, bytes]:
    if len(frame) < 8:
        raise ProtocolError("truncated header")
    expected = _header_size(frame, max_frame)
    if len(frame) != expected:
        raise ProtocolError("frame length mismatch")
    inner = _blocks(frame[8:], 0, decode=True)
    message_id, key_index, payload_size = struct.unpack_from("<IHI", inner)
    if not 0 <= key_index < len(KEYS):
        raise ProtocolError("invalid payload key")
    if len(inner) != 16 + align8(payload_size):
        raise ProtocolError("inner length mismatch")
    payload = _blocks(inner[10:10 + align8(payload_size)], key_index, decode=True)
    return message_id, payload[:payload_size]


class StreamDecoder:
    """Keep at most one bounded partial frame; fail closed after malformed input."""

    def __init__(self, max_frame: int = MAX_FRAME):
        if max_frame < 24:
            raise ValueError("max_frame must be at least 24")
        self.max_frame = max_frame
        self.buffer = bytearray()
        self.failed = False

    def feed(self, data: bytes) -> list[tuple[int, bytes]]:
        if self.failed:
            raise ProtocolError("decoder is closed")
        messages = []
        cursor = 0
        try:
            while cursor < len(data):
                target = 8 if len(self.buffer) < 8 else _header_size(
                    self.buffer, self.max_frame)
                count = min(target - len(self.buffer), len(data) - cursor)
                self.buffer.extend(data[cursor:cursor + count])
                cursor += count
                if len(self.buffer) < 8:
                    continue
                frame_len = _header_size(self.buffer, self.max_frame)
                if len(self.buffer) == frame_len:
                    messages.append(decode_frame(bytes(self.buffer),
                                                 max_frame=self.max_frame))
                    self.buffer.clear()
        except ProtocolError:
            self.failed = True
            self.buffer.clear()
            raise
        return messages

    def eof(self) -> None:
        if self.failed or self.buffer:
            self.failed = True
            self.buffer.clear()
            raise ProtocolError("incomplete or invalid stream")


class TransportServer:
    """Transport inspection only: receive frames and emit scheduled empty heartbeats."""

    def __init__(self, *, heartbeat: float = 1.0, idle_timeout: float = 15.0,
                 max_clients: int = 16, max_frame: int = MAX_FRAME,
                 frames_per_second: int = 256,
                 log: Callable[[str], None] = print):
        if not (math.isfinite(heartbeat) and math.isfinite(idle_timeout)
                and 0 < heartbeat < idle_timeout):
            raise ValueError("require 0 < heartbeat < idle_timeout")
        if max_clients < 1 or frames_per_second < 1 or max_frame < 24:
            raise ValueError("invalid resource limit")
        self.heartbeat = heartbeat
        self.idle_timeout = idle_timeout
        self.max_clients = max_clients
        self.max_frame = max_frame
        self.frames_per_second = frames_per_second
        self.log = log
        self.clients: set[asyncio.Task] = set()

    async def _close_writer(self, writer: asyncio.StreamWriter) -> None:
        writer.close()
        with contextlib.suppress(ConnectionError, asyncio.TimeoutError):
            await asyncio.wait_for(writer.wait_closed(), 2.0)

    async def handle(self, reader: asyncio.StreamReader,
                     writer: asyncio.StreamWriter) -> None:
        if len(self.clients) >= self.max_clients:
            await self._close_writer(writer)
            return
        task = asyncio.current_task()
        self.clients.add(task)
        decoder = StreamDecoder(self.max_frame)
        now = time.monotonic()
        last_rx = now
        next_heartbeat = now + self.heartbeat
        rate_start, rate_count = now, 0
        try:
            while True:
                now = time.monotonic()
                if now - last_rx >= self.idle_timeout:
                    self.log("disconnect: idle timeout")
                    return
                if now >= next_heartbeat:
                    writer.write(encode_frame(0))
                    await asyncio.wait_for(writer.drain(), 2.0)
                    next_heartbeat = time.monotonic() + self.heartbeat
                wait = min(next_heartbeat - time.monotonic(),
                           last_rx + self.idle_timeout - time.monotonic())
                if wait <= 0:
                    continue
                try:
                    data = await asyncio.wait_for(reader.read(65536), wait)
                except asyncio.TimeoutError:
                    continue
                if not data:
                    decoder.eof()
                    return
                messages = decoder.feed(data)
                if messages:
                    last_rx = time.monotonic()
                    if last_rx - rate_start >= 1.0:
                        rate_start, rate_count = last_rx, 0
                    rate_count += len(messages)
                    if rate_count > self.frames_per_second:
                        raise ProtocolError("request rate exceeded")
                for message_id, payload in messages:
                    if message_id == 0 and not payload:
                        continue
                    self.log(f"rx message=0x{message_id:08X} length={len(payload)}")
                    # Nonempty application responses require a separate payload transform.
        except (ProtocolError, ConnectionError, asyncio.TimeoutError) as error:
            self.log(f"disconnect: {type(error).__name__}")
        finally:
            self.clients.discard(task)
            await self._close_writer(writer)

    async def close_clients(self) -> None:
        tasks = tuple(self.clients)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


async def serve(port: int, idle_timeout: float) -> None:
    service = TransportServer(idle_timeout=idle_timeout)
    listener = await asyncio.start_server(service.handle, "127.0.0.1", port,
                                         limit=65536)
    actual_port = listener.sockets[0].getsockname()[1]
    print(f"Transport only: 127.0.0.1:{actual_port}; application login not implemented")
    try:
        async with listener:
            await listener.serve_forever()
    finally:
        listener.close()
        await listener.wait_closed()
        await service.close_clients()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=19090)
    parser.add_argument("--idle-timeout", type=float, default=15.0)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    if not math.isfinite(args.idle_timeout) or args.idle_timeout <= 1.0:
        parser.error("idle-timeout must be finite and greater than 1 second")
    try:
        asyncio.run(serve(args.port, args.idle_timeout))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
