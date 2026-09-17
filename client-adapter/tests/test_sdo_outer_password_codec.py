"""Synthetic, offline math checks; not an original-client equivalence claim."""
import base64
import unittest

from cryptography.hazmat.decrepit.ciphers.algorithms import TripleDES
from cryptography.hazmat.primitives.ciphers import Cipher, modes
from sdo_outer_password_codec import derive_key, unwrap_outer, wrap_outer


class OuterCodecTests(unittest.TestCase):
    KEY = '00001000020000300004'

    def test_runtime_iv_not_disk_bytes(self):
        # Independent fixed-global observation; a self-roundtrip cannot detect
        # the wrong IV on both sides of the same implementation.
        from sdo_outer_password_codec import _IV
        self.assertEqual(_IV, b'23456789')

    def test_standard_des_known_answer(self):
        # Independent DES known-answer block verifies EDE primitive convention.
        key = bytes.fromhex('133457799BBCDFF1')
        encryptor = Cipher(TripleDES(key * 3), modes.ECB()).encryptor()
        result = encryptor.update(bytes.fromhex('0123456789ABCDEF')) + encryptor.finalize()
        self.assertEqual(result.hex().upper(), '85E813540F0AB405')

    def test_key_words_masks_and_c_string_termination(self):
        # 01 00 02 00 03 00 04 00 XOR mask, then swap each byte's nibbles.
        self.assertEqual(derive_key(self.KEY), bytes.fromhex('cd 6d ad ae ef 6f cf ac') + b'\x08' * 16)
        self.assertEqual(derive_key('00221000000000000000'), b'\x08' * 24)
        # Decimal word overflow is narrowed just as a source WORD assignment.
        self.assertEqual(derive_key('65537000020000300004'), derive_key(self.KEY))

    def test_synthetic_binary_roundtrips(self):
        for payload in (b'', b'A', b'1234567', b'12345678', b'KKTestAccount',
                        b'FakePassword!42', b'\0x\0', bytes(range(128))):
            with self.subTest(length=len(payload)):
                encoded = wrap_outer(payload, self.KEY)
                self.assertEqual(unwrap_outer(encoded, self.KEY), payload)
                self.assertEqual(len(base64.b64decode(encoded)), (len(payload)//8 + 1)*8)

    def test_fail_closed_malformed_inputs(self):
        for value in ('', 'abcd', '1'*19, '1'*21, 'x'*20):
            with self.assertRaises(ValueError): derive_key(value)
        for value in ('', '!', base64.b64encode(b'x').decode()):
            with self.assertRaises(ValueError): unwrap_outer(value, self.KEY)
        # Construct invalid padding explicitly, rather than assume any bitflip fails.
        from sdo_outer_password_codec import _IV
        encryptor = Cipher(TripleDES(derive_key(self.KEY)), modes.CBC(_IV)).encryptor()
        block = encryptor.update(b'1234567\0') + encryptor.finalize()
        with self.assertRaises(ValueError): unwrap_outer(base64.b64encode(block).decode(), self.KEY)


if __name__ == '__main__':
    unittest.main()
