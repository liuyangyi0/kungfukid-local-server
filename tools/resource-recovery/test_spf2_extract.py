import tempfile
import unittest
import zlib
from pathlib import Path

from spf2_extract import (
    RECOVERED_CONFIG_KEY,
    decode_not_xor_zlib,
    safe_output_path,
)


def encode_not_xor(plaintext: bytes, key: bytes) -> bytes:
    return bytes(
        (~plain_byte ^ key[index % len(key)]) & 0xFF
        for index, plain_byte in enumerate(plaintext)
    )


class Spf2ExtractTests(unittest.TestCase):
    def test_not_xor_zlib_round_trip(self) -> None:
        original = b'<?xml version="1.0" encoding="gb2312"?>\r\n<Root />\r\n'
        compressed = zlib.compress(original)
        ciphertext = encode_not_xor(compressed, RECOVERED_CONFIG_KEY)
        self.assertEqual(
            decode_not_xor_zlib(ciphertext, RECOVERED_CONFIG_KEY), original
        )

    def test_truncated_zlib_is_rejected(self) -> None:
        compressed = zlib.compress(b"test payload")
        ciphertext = encode_not_xor(compressed[:-4], RECOVERED_CONFIG_KEY)
        with self.assertRaisesRegex(ValueError, "truncated"):
            decode_not_xor_zlib(ciphertext, RECOVERED_CONFIG_KEY)

    def test_empty_key_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "must not be empty"):
            decode_not_xor_zlib(b"anything", b"")

    def test_safe_output_path(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            self.assertEqual(
                safe_output_path(root, "/weapon/253178/combotip.xml"),
                root / "weapon" / "253178" / "combotip.xml",
            )

    def test_traversal_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            for unsafe_path in ("/../escape.xml", "/a/../../escape.xml", "C:/x"):
                with self.subTest(path=unsafe_path):
                    with self.assertRaisesRegex(ValueError, "unsafe"):
                        safe_output_path(root, unsafe_path)


if __name__ == "__main__":
    unittest.main()
