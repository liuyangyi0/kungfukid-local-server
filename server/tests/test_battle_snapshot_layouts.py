"""Fixed mode snapshot and event layouts, separate from room/mode authority."""
import math
import struct
import unittest
from server.kk_local.layouts import decode_battle
from server.kk_local.wire import ProtocolError, Message
from server.tests import test_shared_rooms as fixtures


SHAPES={8155:334,8157:55,8287:63,8288:55,8294:47,8295:39,8296:43,8297:39}


def packet(ident):
    p=bytearray(SHAPES[ident]);struct.pack_into('<IQ',p,0,ident,1001)
    p[12:14]=b'\x01\x01'
    return p


class SnapshotLayoutTests(unittest.TestCase):
    def test_fixed_eight_rows_uses_last_row_and_preserves_every_width(self):
        p=packet(8155);p[39:46]=bytes([255,254,253,252,251,250,249])
        for slot in range(8):
            o=46+36*slot
            struct.pack_into('<QIIHHIIIH',p,o,1000+slot,0xffffffff,100+slot,0xffff,200+slot,
                             300+slot,400+slot,500+slot,600+slot)
            p[o+34:o+36]=bytes([slot,255-slot])
        out=decode_battle(p)
        self.assertEqual(len(out['rows']),8)  # prefix byte255 is not a count
        row=out['rows'][7]
        self.assertEqual((row['slot'],row['player'],row['field_08_raw'],row['field_0c_raw']),
                         (7,1007,0xffffffff,107))
        self.assertEqual((row['field_10_raw'],row['field_12_raw']), (65535,207))
        self.assertEqual(row['fields_14_1c_raw'],(307,407,507))
        self.assertEqual((row['counter_20_raw'],row['opaque_22_24']),(607,b'\x07\xf8'))
        p[39]=0;p[-1]=0
        self.assertEqual(out['mode_prefix'][0],255)
        self.assertEqual(row['opaque_22_24'],b'\x07\xf8')

    def test_zero_and_duplicate_id_rows_are_not_dropped_or_deduplicated(self):
        p=packet(8155)
        struct.pack_into('<Q',p,46,1002);struct.pack_into('<Q',p,82,1002)
        out=decode_battle(p)
        self.assertEqual(tuple(r['player'] for r in out['rows']),(1002,1002,0,0,0,0,0,0))
        self.assertNotIn('room_pair',out)
        self.assertNotIn('authorized',out)

    def test_tracking_event_keeps_host_sender_distinct_from_participant(self):
        p=packet(8157);struct.pack_into('<QII',p,39,1002,0xffffffff,0x80000000)
        out=decode_battle(p)
        self.assertEqual((out['sender'],out['participant'],out['event_code_raw'],out['value_raw']),
                         (1001,1002,0xffffffff,0x80000000))

    def test_spawn_kind_unknown_not_coerced_to_coin(self):
        for kind,family in ((1,'gem'),(2,'gem'),(3,'coin'),(0,'unknown'),(99,'unknown')):
            p=packet(8287);struct.pack_into('<IIIfff',p,39,kind,123,0xffffffff,-1,2,3)
            out=decode_battle(p)
            self.assertEqual((out['spawn_family'],out['object_id'],out['spawn_region_index'],out['position']),
                             (family,123,0xffffffff,(-1,2,3)))
            self.assertNotIn('room_pair',out)
        for o in (51,55,59):
            for value in (math.nan,math.inf,-math.inf):
                p=packet(8287);struct.pack_into('<f',p,o,value)
                with self.assertRaises(ProtocolError):decode_battle(p)

    def test_same_length_event_and_action_packet_are_distinct(self):
        p=packet(8288);struct.pack_into('<Q',p,39,1002);p[47:55]=b'ABCDEFGH'
        out=decode_battle(p);p[47]=0
        self.assertEqual((out['player'],out['opaque_47_55']),(1002,b'ABCDEFGH'))
        self.assertNotIn('event_code_raw',out)

    def test_ui_arguments_and_header_only_signals(self):
        p=packet(8294);struct.pack_into('<II',p,39,0xffffffff,42)
        self.assertEqual(decode_battle(p)['interval_arguments_raw'],(0xffffffff,42))
        p=packet(8296);struct.pack_into('<I',p,39,9)
        out=decode_battle(p)
        self.assertEqual((out['sender'],out['value_39_raw']),(1001,9))
        for ident in (8295,8297):
            self.assertEqual(decode_battle(packet(ident))['opaque_body'],b'')

    def test_all_shapes_reject_truncation_and_trailing_bytes(self):
        for ident in SHAPES:
            self.assertEqual(decode_battle(packet(ident))['id'],ident)
            for bad in (packet(ident)[:-1],packet(ident)+b'\0'):
                with self.subTest(ident=ident):
                    with self.assertRaises(ProtocolError):decode_battle(bad)


class SnapshotRelayBoundaryTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join
    battle=fixtures.SharedRoomTests.battle

    def test_snapshot_or_spawn_decode_does_not_grant_mode_or_host_authority(self):
        self.battle()
        for ident in SHAPES:
            self.assertEqual(self.e1.handle(self.c1,Message(8071,bytes(packet(ident)))),[])
            self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(self.e1.room.last_sequence,{})


if __name__=='__main__':
    unittest.main()
