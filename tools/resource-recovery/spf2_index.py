#!/usr/bin/env python3
"""Read the plaintext index of an SGDP/SPF2 package without executing the client.

The legacy client's ``config.spf2`` keeps its path radix tree, data offset table,
and a duplicate offset table in plaintext.  This tool inventories those records;
it does not decrypt or decompress entry payloads.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import struct
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path


HEADER_SIZE = 0x40
PATH_TREE_OFFSET_FIELD = 0x28
ENTRY_TABLE_OFFSET_FIELD = 0x2C
BACKUP_TABLE_OFFSET_FIELD = 0x30
ENTRY_COUNT_FIELD = 0x34
RADIX_SEGMENT_SIZE = 8
RADIX_RECORD_SIZE = 12
LEAF_MASK = 0xFF000000
LEAF_INDEX_MASK = 0x00FFFFFF


@dataclass(frozen=True)
class Spf2Entry:
    index: int
    path: str
    data_offset: int
    stored_size: int
    trailer_hex: str
    payload_sha256: str
    payload_first_16_bytes_hex: str


@dataclass(frozen=True)
class Spf2Index:
    source: str
    source_sha256: str
    file_size: int
    path_tree_offset: int
    entry_table_offset: int
    backup_table_offset: int
    entry_count: int
    radix_node_count: int
    entries: tuple[Spf2Entry, ...]


def _u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise ValueError(f"u32 read outside file at 0x{offset:X}")
    return struct.unpack_from("<I", data, offset)[0]


def _decode_path(raw: bytes) -> str:
    for encoding in ("utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("latin1")


def _parse_radix_tree(blob: bytes, entry_count: int) -> tuple[dict[int, bytes], int]:
    leaves: dict[int, bytes] = {}
    active_offsets: set[int] = set()
    node_offsets: set[int] = set()

    def walk(node_offset: int, prefix: bytes) -> None:
        if node_offset in active_offsets:
            raise ValueError(f"cycle in SPF2 path tree at 0x{node_offset:X}")
        if node_offset < 0 or node_offset + 4 > len(blob):
            raise ValueError(f"path node outside tree at 0x{node_offset:X}")

        count = _u32(blob, node_offset)
        node_end = node_offset + 4 + count * RADIX_RECORD_SIZE
        if count > entry_count or node_end > len(blob):
            raise ValueError(
                f"invalid path node at 0x{node_offset:X}: count={count}"
            )

        active_offsets.add(node_offset)
        node_offsets.add(node_offset)
        for record_index in range(count):
            record_offset = node_offset + 4 + record_index * RADIX_RECORD_SIZE
            segment_field = blob[
                record_offset : record_offset + RADIX_SEGMENT_SIZE
            ]
            segment = segment_field.rstrip(b"\0")
            target = _u32(blob, record_offset + RADIX_SEGMENT_SIZE)
            path = prefix + segment

            if target & LEAF_MASK == LEAF_MASK:
                entry_index = target & LEAF_INDEX_MASK
                if entry_index >= entry_count:
                    raise ValueError(f"path leaf index out of range: {entry_index}")
                previous = leaves.get(entry_index)
                if previous is not None and previous != path:
                    raise ValueError(f"conflicting paths for entry {entry_index}")
                leaves[entry_index] = path
            else:
                walk(target, path)
        active_offsets.remove(node_offset)

    walk(0, b"")
    expected = set(range(entry_count))
    actual = set(leaves)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise ValueError(f"path tree mismatch; missing={missing[:10]}, extra={extra[:10]}")
    return leaves, len(node_offsets)


def parse_spf2_index(source_path: Path) -> Spf2Index:
    source_path = source_path.resolve(strict=True)
    data = source_path.read_bytes()
    if len(data) < HEADER_SIZE:
        raise ValueError("SPF2 file is smaller than its fixed header")

    path_tree_offset = _u32(data, PATH_TREE_OFFSET_FIELD)
    entry_table_offset = _u32(data, ENTRY_TABLE_OFFSET_FIELD)
    backup_table_offset = _u32(data, BACKUP_TABLE_OFFSET_FIELD)
    entry_count = _u32(data, ENTRY_COUNT_FIELD)
    table_size = entry_count * 8

    if not (
        HEADER_SIZE <= path_tree_offset <= entry_table_offset <= backup_table_offset
    ):
        raise ValueError("SPF2 header offsets are not ordered")
    if entry_table_offset + table_size != backup_table_offset:
        raise ValueError("primary SPF2 entry table has an unexpected size")
    if backup_table_offset + table_size != len(data):
        raise ValueError("backup SPF2 entry table does not end at EOF")

    primary_table = data[entry_table_offset:backup_table_offset]
    backup_table = data[backup_table_offset:]
    if primary_table != backup_table:
        raise ValueError("primary and backup SPF2 entry tables differ")

    offsets_and_sizes = [
        struct.unpack_from("<II", primary_table, index * 8)
        for index in range(entry_count)
    ]
    for index, (data_offset, stored_size) in enumerate(offsets_and_sizes):
        payload_end = data_offset + stored_size
        expected_next = (
            offsets_and_sizes[index + 1][0]
            if index + 1 < entry_count
            else path_tree_offset
        )
        if data_offset < HEADER_SIZE or payload_end + 4 != expected_next:
            raise ValueError(
                f"entry {index} does not end four bytes before the next region"
            )

    path_blob = data[path_tree_offset:entry_table_offset]
    paths, radix_node_count = _parse_radix_tree(path_blob, entry_count)

    entries: list[Spf2Entry] = []
    for index, (data_offset, stored_size) in enumerate(offsets_and_sizes):
        payload = data[data_offset : data_offset + stored_size]
        trailer = data[data_offset + stored_size : data_offset + stored_size + 4]
        entries.append(
            Spf2Entry(
                index=index,
                path=_decode_path(paths[index]),
                data_offset=data_offset,
                stored_size=stored_size,
                trailer_hex=trailer.hex(),
                payload_sha256=hashlib.sha256(payload).hexdigest(),
                payload_first_16_bytes_hex=payload[:16].hex(),
            )
        )

    return Spf2Index(
        source=str(source_path),
        source_sha256=hashlib.sha256(data).hexdigest(),
        file_size=len(data),
        path_tree_offset=path_tree_offset,
        entry_table_offset=entry_table_offset,
        backup_table_offset=backup_table_offset,
        entry_count=entry_count,
        radix_node_count=radix_node_count,
        entries=tuple(entries),
    )


def write_index(index: Spf2Index, output_directory: Path) -> None:
    output_directory = output_directory.resolve(strict=False)
    output_directory.mkdir(parents=True, exist_ok=False)

    with (output_directory / "entries.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as output_file:
        field_names = list(asdict(index.entries[0]).keys()) if index.entries else []
        writer = csv.DictWriter(output_file, fieldnames=field_names)
        writer.writeheader()
        writer.writerows(asdict(entry) for entry in index.entries)

    summary = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": index.source,
        "source_sha256": index.source_sha256,
        "file_size": index.file_size,
        "path_tree_offset": index.path_tree_offset,
        "entry_table_offset": index.entry_table_offset,
        "backup_table_offset": index.backup_table_offset,
        "entry_count": index.entry_count,
        "radix_node_count": index.radix_node_count,
        "payload_decryption_attempted": False,
    }
    (output_directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Inventory the plaintext path and offset index in config.spf2."
    )
    parser.add_argument("source", type=Path, help="Path to config.spf2")
    parser.add_argument("output", type=Path, help="New output directory")
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    index = parse_spf2_index(arguments.source)
    write_index(index, arguments.output)
    print(f"Indexed {index.entry_count} SPF2 entries in {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
