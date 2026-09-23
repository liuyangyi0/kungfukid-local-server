from __future__ import annotations

import hashlib
import struct
import tempfile
import unittest
from pathlib import Path

from spf2_index import parse_spf2_index, write_index


def _record(segment: bytes, target: int) -> bytes:
    if len(segment) > 8:
        raise ValueError("test radix segment is too long")
    return segment.ljust(8, b"\0") + struct.pack("<I", target)


def _fixture() -> bytes:
    entry_zero = b"first"
    entry_one = b"second"
    first_offset = 0x40
    second_offset = first_offset + len(entry_zero) + 4
    path_tree_offset = second_offset + len(entry_one) + 4

    child_offset = 4 + 2 * 12
    path_blob = (
        struct.pack("<I", 2)
        + _record(b"/a.txt", 0xFF000000)
        + _record(b"/folder/", child_offset)
        + struct.pack("<I", 1)
        + _record(b"b.xml", 0xFF000001)
    )
    entry_table_offset = path_tree_offset + len(path_blob)
    table = struct.pack(
        "<IIII", first_offset, len(entry_zero), second_offset, len(entry_one)
    )
    backup_table_offset = entry_table_offset + len(table)

    header = bytearray(0x40)
    struct.pack_into("<I", header, 0x28, path_tree_offset)
    struct.pack_into("<I", header, 0x2C, entry_table_offset)
    struct.pack_into("<I", header, 0x30, backup_table_offset)
    struct.pack_into("<I", header, 0x34, 2)
    return (
        bytes(header)
        + entry_zero
        + b"A" * 4
        + entry_one
        + b"B" * 4
        + path_blob
        + table
        + table
    )


class Spf2IndexTests(unittest.TestCase):
    def test_parses_paths_tables_and_payload_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "config.spf2"
            source.write_bytes(_fixture())

            result = parse_spf2_index(source)

            self.assertEqual(result.entry_count, 2)
            self.assertEqual(result.radix_node_count, 2)
            self.assertEqual([entry.path for entry in result.entries], ["/a.txt", "/folder/b.xml"])
            self.assertEqual(
                result.entries[0].payload_sha256,
                hashlib.sha256(b"first").hexdigest(),
            )
            self.assertEqual(result.entries[1].trailer_hex, b"B".hex() * 4)

    def test_refuses_mismatched_backup_table(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "bad.spf2"
            data = bytearray(_fixture())
            data[-1] ^= 0xFF
            source.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "tables differ"):
                parse_spf2_index(source)

    def test_output_directory_must_be_new(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            base = Path(temporary_directory)
            source = base / "config.spf2"
            source.write_bytes(_fixture())
            result = parse_spf2_index(source)
            output = base / "index"
            write_index(result, output)
            self.assertTrue((output / "entries.csv").is_file())
            with self.assertRaises(FileExistsError):
                write_index(result, output)


if __name__ == "__main__":
    unittest.main()
