"""Object/pair wire contracts; never exercise a client mutation or relay grant."""
import math
import struct
import unittest
from server.kk_local.layouts import decode_battle
from server.kk_local.wire import ProtocolError, Message
from server.tests import test_shared_rooms as fixtures


SHAPES={8127:63,8143:59,8144:115,8270:87,8280:55,8282:47,8284:75}


def packet(ident):
    p=bytearray(SHAPES[ident]);struct.pack_into('<IQ',p,0,ident,1001)
    p[12:14]=b'\x01\x01'
    return p


class ObjectLayoutTests(unittest.TestCase):
    def test_mp_is_snapshot_and_keeps_opaque_and_float_bits(self):
        p=packet(8127);struct.pack_into('<Q',p,39,1002)
        p[47:51]=b'ABCD';struct.pack_into('<fII',p,51,-0.0,8,23)
        out=decode_battle(p);p[47]=0
        self.assertEqual((out['player'],out['opaque_47_51'],out['room_pair']),
                         (1002,b'ABCD',(8,23)))
        self.assertEqual(out['current_mp_bits'],0x80000000)
        self.assertNotIn('mp_delta',out)

    def test_target_lock_keeps_full_uid_and_timeout_not_floats(self):
        p=packet(8143);struct.pack_into('<QQI',p,39,1002,0xffffffff7fc00000,15000)
        out=decode_battle(p)
        self.assertEqual((out['player'],out['target'],out['target_timeout_raw']),
                         (1002,0xffffffff7fc00000,15000))
        self.assertNotIn('room_pair',out)
        self.assertNotIn('position',out)

    def test_pair_transform_fields_have_distinct_actors_and_state_parameters(self):
        p=packet(8144);struct.pack_into('<QQ',p,39,1002,1003)
        struct.pack_into('<6fI6fII',p,55,1,2,3,0,0,1,3002,4,5,6,1,0,0,3006,811115)
        out=decode_battle(p)
        self.assertEqual((out['first_player'],out['second_player']),(1002,1003))
        self.assertEqual((out['first_position'],out['second_position']),((1,2,3),(4,5,6)))
        self.assertEqual((out['first_facing'],out['second_facing']),((0,0,1),(1,0,0)))
        self.assertEqual((out['first_state_raw'],out['second_state_raw'],out['second_skill_property_id']),
                         (3002,3006,811115))
        self.assertNotIn('room_pair',out)

    def test_slip_does_not_collapse_actors_directions_or_distances(self):
        p=packet(8270);struct.pack_into('<QQ8f',p,39,1002,0,1,0,0,0,0,-1,10,-4)
        out=decode_battle(p)
        self.assertEqual((out['first_player'],out['second_player']),(1002,0))
        self.assertEqual((out['first_direction'],out['second_direction']),((1,0,0),(0,0,-1)))
        self.assertEqual((out['first_distance'],out['second_distance']),(10,-4))

    def test_null_target_object_key_and_weapon_unknown_bytes_stay_distinct(self):
        p=packet(8280);struct.pack_into('<QQ',p,39,1002,0)
        self.assertEqual(decode_battle(p)['target'],0)
        p=packet(8282);struct.pack_into('<I',p,39,987);p[43:47]=b'WXYZ'
        out=decode_battle(p)
        self.assertEqual((out['object_key'],out['opaque_43_47']),(987,b'WXYZ'))
        p=packet(8284);struct.pack_into('<II',p,39,0xffffffff,1)
        p[47:59]=bytes(range(12));struct.pack_into('<QII',p,59,1002,8,23)
        out=decode_battle(p);p[47]=255
        self.assertEqual(out['weapon_operation_raw'],0xffffffff)
        self.assertEqual(out['opaque_47_59'],bytes(range(12)))
        self.assertEqual((out['player'],out['room_pair']),(1002,(8,23)))

    def test_nonfinite_all_consumed_float_fields_rejected(self):
        offsets={8127:[51],8144:list(range(55,79,4))+list(range(83,107,4)),8270:list(range(55,87,4))}
        for ident,fields in offsets.items():
            for offset in fields:
                for value in (math.nan,math.inf,-math.inf):
                    p=packet(ident);struct.pack_into('<f',p,offset,value)
                    with self.subTest(ident=ident,offset=offset,value=value):
                        with self.assertRaises(ProtocolError):decode_battle(p)

    def test_exact_local_shapes_and_8156_fallthrough_not_guessed(self):
        for ident in SHAPES:
            p=packet(ident)
            self.assertEqual(decode_battle(p)['id'],ident)
            for bad in (p[:-1],p+b'\0'):
                with self.assertRaises(ProtocolError):decode_battle(bad)
        p=packet(8270);struct.pack_into('<I',p,0,8156)
        self.assertIsNone(decode_battle(p))


class ObjectRelayBoundaryTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join
    battle=fixtures.SharedRoomTests.battle

    def test_decoding_does_not_open_unqualified_object_or_pair_mutations(self):
        self.battle()
        # Owned8127/8143/8280 and8270 components have separate relay tests.
        for ident in SHAPES.keys()-{8127,8143,8280,8270}:
            self.assertEqual(self.e1.handle(self.c1,Message(8071,bytes(packet(ident)))),[])
            self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(self.e1.room.last_sequence,{})


if __name__=='__main__':
    unittest.main()
