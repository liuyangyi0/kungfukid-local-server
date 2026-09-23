import struct
import unittest
from server.kk_local.shop_catalog import ShopRecord, encode_catalog, decode_catalog, MAX_RECORDS
from server.kk_local.wire import ProtocolError, encode_game, GameDecoder


class ShopCatalogTests(unittest.TestCase):
    def test_requirement_and_score_fields_do_not_overlap(self):
        raw=bytearray(108);raw[13]=7;raw[49]=1;raw[83]=1
        struct.pack_into('<I',raw,77,25303301)
        struct.pack_into('<III',raw,84,1234,1,15000)
        record=ShopRecord(bytes(raw))
        self.assertEqual(record.requirement,(1,7))
        fields=record.decoded_fields
        self.assertEqual(fields['associated_catalog_key'],25303301)
        self.assertEqual(fields['score_kind'],1)
        self.assertEqual(fields['score_value_raw'],15000)
        self.assertEqual(fields['credit_offset_enabled'],1)
        raw[83]=4
        self.assertEqual(ShopRecord(bytes(raw)).requirement,(4,1234))

    def test_lossless_nonzero_unknown_bytes_and_wire_roundtrip(self):
        # Synthetic boundary fixture, explicitly not a captured native product.
        records = (ShopRecord(bytes(range(108))), ShopRecord(bytes(reversed(range(108)))))
        message = encode_catalog(255, 25, records)
        decoded = GameDecoder().feed(encode_game(message))[0]
        category, variant, result = decode_catalog(decoded.payload)
        self.assertEqual((category, variant, result), (255, 25, records))
        self.assertEqual(encode_catalog(category, variant, result), message)

    def test_native_price_branch_and_admission(self):
        raw = bytearray(108)
        struct.pack_into('<I', raw, 5, 253033)
        struct.pack_into('<IIII', raw, 30, 100, 0, 200, 150)
        record = ShopRecord(bytes(raw))
        self.assertEqual(record.item_id, 253033)
        self.assertEqual(record.display_price, ('gold', 100, 0))
        self.assertFalse(record.admitted_by_client(255))
        self.assertTrue(record.admitted_by_client(19))
        struct.pack_into('<I', raw, 30, 0)
        raw[48] = 1
        self.assertEqual(ShopRecord(bytes(raw)).display_price, ('ticket', 200, 150))
        self.assertTrue(ShopRecord(bytes(raw)).admitted_by_client(255))

    def test_invalid_records_counts_and_partial_dicts(self):
        for body in (b'', bytes(5), struct.pack('<BBI', 1, 2, 1),
                     struct.pack('<BBI', 1, 2, 0)+b'x',
                     struct.pack('<BBI', 1, 2, MAX_RECORDS+1)):
            with self.assertRaises(ProtocolError): decode_catalog(body)
        for raw in (bytes(107), bytes(109), bytearray(108)):
            with self.assertRaises(ProtocolError): ShopRecord(raw)
        with self.assertRaises(ProtocolError): encode_catalog(1, 2, [{'item_id': 253033}])
        with self.assertRaises(ProtocolError): encode_catalog(256, 2)


if __name__ == '__main__': unittest.main()
