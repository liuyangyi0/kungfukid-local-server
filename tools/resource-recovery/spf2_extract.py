#!/usr/bin/env python3
"""Safely extract the recovered contents of the legacy ``config.spf2``.

This module reimplements only the two transforms established by static analysis
of ``sdfile.dll`` and by full-package validation.  It never loads or executes a
legacy binary and it never modifies the source package.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import zlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from spf2_index import Spf2Entry, parse_spf2_index


EXPECTED_FLAGS = 0x00002200
EXPECTED_COMPRESSION_SELECTOR = 2
EXPECTED_ENCRYPTION_SELECTOR = 2
RECOVERED_CONFIG_KEY = b"F48A715746514613BD70EF276119128F"


@dataclass(frozen=True)
class ExtractedEntry:
    index: int
    path: str
    stored_size: int
    encrypted_stream_size: int
    decoded_size: int
    decoded_sha256: str


def decode_not_xor_zlib(ciphertext: bytes, key: bytes) -> bytes:
    """Undo CSimpleEncrypt selector 2, then validate and inflate zlib data."""

    if not key:
        raise ValueError("SPF2 key must not be empty")
    compressed = bytes(
        (~(cipher_byte ^ key[index % len(key)])) & 0xFF
        for index, cipher_byte in enumerate(ciphertext)
    )
    decoder = zlib.decompressobj()
    decoded = decoder.decompress(compressed)
    decoded += decoder.flush()
    if not decoder.eof:
        raise ValueError("decoded SPF2 entry contains a truncated zlib stream")
    if decoder.unused_data:
        raise ValueError("decoded SPF2 entry has data after the zlib stream")
    if decoder.unconsumed_tail:
        raise ValueError("decoded SPF2 entry has an unconsumed zlib tail")
    return decoded


def safe_output_path(output_root: Path, package_path: str) -> Path:
    """Map a package path below ``output_root`` and reject traversal syntax."""

    normalized = package_path.replace("\\", "/").lstrip("/")
    pure_path = PurePosixPath(normalized)
    if not normalized or pure_path.is_absolute():
        raise ValueError(f"invalid empty or absolute SPF2 path: {package_path!r}")
    if any(part in ("", ".", "..") for part in pure_path.parts):
        raise ValueError(f"unsafe SPF2 path: {package_path!r}")
    if any(":" in part for part in pure_path.parts):
        raise ValueError(f"unsafe drive-like SPF2 path: {package_path!r}")
    return output_root.joinpath(*pure_path.parts)


def _read_flags(source_data: bytes, entry: Spf2Entry) -> int:
    if entry.stored_size < 4:
        raise ValueError(f"SPF2 entry {entry.index} is smaller than its flags")
    return int.from_bytes(
        source_data[entry.data_offset : entry.data_offset + 4], "little"
    )


def extract_spf2(
    source_path: Path,
    output_directory: Path,
    *,
    key: bytes = RECOVERED_CONFIG_KEY,
) -> tuple[ExtractedEntry, ...]:
    """Extract every indexed entry to a new directory.

    The entry table's ``stored_size`` excludes the final four encrypted bytes of
    each zlib stream.  Those bytes occupy the region previously labelled as a
    trailer and must be included before decrypting.
    """

    source_path = source_path.resolve(strict=True)
    output_directory = output_directory.resolve(strict=False)
    if output_directory.exists():
        raise FileExistsError(f"output directory already exists: {output_directory}")

    index = parse_spf2_index(source_path)
    source_data = source_path.read_bytes()
    staged: list[tuple[ExtractedEntry, Path, bytes]] = []

    for entry in index.entries:
        flags = _read_flags(source_data, entry)
        compression_selector = (flags >> 8) & 0x0F
        encryption_selector = (flags >> 12) & 0x0F
        if flags != EXPECTED_FLAGS:
            raise ValueError(
                f"unsupported SPF2 flags for entry {entry.index}: 0x{flags:08X}"
            )
        if (
            compression_selector != EXPECTED_COMPRESSION_SELECTOR
            or encryption_selector != EXPECTED_ENCRYPTION_SELECTOR
        ):
            raise ValueError(f"unexpected SPF2 selectors for entry {entry.index}")

        cipher_start = entry.data_offset + 4
        cipher_end = entry.data_offset + entry.stored_size + 4
        ciphertext = source_data[cipher_start:cipher_end]
        expected_cipher_size = entry.stored_size
        if len(ciphertext) != expected_cipher_size:
            raise ValueError(f"truncated encrypted stream for entry {entry.index}")

        decoded = decode_not_xor_zlib(ciphertext, key)
        destination = safe_output_path(output_directory, entry.path)
        record = ExtractedEntry(
            index=entry.index,
            path=entry.path,
            stored_size=entry.stored_size,
            encrypted_stream_size=len(ciphertext),
            decoded_size=len(decoded),
            decoded_sha256=hashlib.sha256(decoded).hexdigest(),
        )
        staged.append((record, destination, decoded))

    # Do not create a partial tree until every entry has decoded and validated.
    output_directory.mkdir(parents=True, exist_ok=False)
    for _, destination, decoded in staged:
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(decoded)

    records = tuple(record for record, _, _ in staged)
    with (output_directory / "manifest.csv").open(
        "w", encoding="utf-8-sig", newline=""
    ) as output_file:
        field_names = list(asdict(records[0]).keys()) if records else []
        writer = csv.DictWriter(output_file, fieldnames=field_names)
        writer.writeheader()
        writer.writerows(asdict(record) for record in records)

    summary = {
        "schema_version": 1,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source": str(source_path),
        "source_sha256": index.source_sha256,
        "entry_count": len(records),
        "decoded_total_bytes": sum(record.decoded_size for record in records),
        "flags": f"0x{EXPECTED_FLAGS:08X}",
        "compression_selector": EXPECTED_COMPRESSION_SELECTOR,
        "encryption_selector": EXPECTED_ENCRYPTION_SELECTOR,
        "transform": "CSimpleEncrypt selector 2 (NOT-XOR), then zlib",
        "key_sha256": hashlib.sha256(key).hexdigest(),
        "source_modified": False,
    }
    (output_directory / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Extract config.spf2 without loading any legacy executable or DLL."
    )
    parser.add_argument("source", type=Path, help="Path to config.spf2")
    parser.add_argument("output", type=Path, help="New output directory")
    parser.add_argument(
        "--key-ascii",
        default=RECOVERED_CONFIG_KEY.decode("ascii"),
        help="Recovered ASCII key (defaults to the validated config.spf2 key)",
    )
    return parser


def main() -> int:
    arguments = build_parser().parse_args()
    records = extract_spf2(
        arguments.source, arguments.output, key=arguments.key_ascii.encode("ascii")
    )
    print(
        f"Extracted {len(records)} entries "
        f"({sum(record.decoded_size for record in records)} decoded bytes)"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
