"""Receiver-derived fields, with unknown bytes and authority left explicit."""
import struct
import unittest
from server.kk_local.layouts import decode_battle
from server.kk_local.wire import ProtocolError, Message
from server.tests import test_shared_rooms as fixtures


SHAPES={8122:51,8125:53,8142:59,8276:51,8278:55,8286:55,8293:51}


def packet(ident):
    p=bytearray(SHAPES[ident]);struct.pack_into('<IQ',p,0,ident,1001)
    p[12:14]=b'\x01\x01'
    return p


class AuxiliaryLayoutTests(unittest.TestCase):
    def test_player_scalars_preserve_full_width_and_no_room_pair(self):
        for ident,field in ((8122,'direction_raw'),(8293,'value_47_raw')):
            p=packet(ident);struct.pack_into('<QI',p,39,1002,0xffffffff)
            out=decode_battle(p)
            self.assertEqual((out['player'],out[field]),(1002,0xffffffff))
            self.assertNotIn('room_pair',out)

    def test_action_argument_is_u16_not_following_room_number(self):
        p=packet(8125);struct.pack_into('<QIH',p,39,1002,6001303,65535)
        out=decode_battle(p)
        self.assertEqual((out['player'],out['action_id'],out['action_argument_51']),
                         (1002,6001303,65535))
        self.assertNotIn('room_pair',out)

    def test_object_room_offsets_differ_and_opaque_bytes_are_owned(self):
        p=packet(8142);struct.pack_into('<I',p,39,789)
        p[43:51]=b'abcdefgh';struct.pack_into('<II',p,51,2,9)
        out=decode_battle(p);p[43]=0
        self.assertEqual((out['object_key'],out['opaque_43_51'],out['room_pair']),
                         (789,b'abcdefgh',(2,9)))
        p=packet(8276);struct.pack_into('<III',p,39,321,2,10)
        out=decode_battle(p)
        self.assertEqual((out['object_key'],out['room_pair']),(321,(2,10)))

    def test_owner_ui_and_host_ready_are_not_interchangeable(self):
        p=packet(8278);struct.pack_into('<Q',p,39,1002)
        p[47:51]=b'abcd';struct.pack_into('<I',p,51,0xffffffff)
        out=decode_battle(p)
        self.assertEqual((out['player'],out['opaque_47_51'],out['ui_value_51_raw']),
                         (1002,b'abcd',0xffffffff))
        p=packet(8286);struct.pack_into('<QQ',p,39,1002,1003)
        out=decode_battle(p)
        self.assertEqual((out['sender'],out['initiator'],out['target']),(1001,1002,1003))
        self.assertNotIn('authorized',out)

    def test_every_shape_enforces_local_exact_length(self):
        for ident in SHAPES:
            with self.subTest(ident=ident):
                p=packet(ident)
                self.assertEqual(decode_battle(p)['id'],ident)
                for bad in (p[:-1],p+b'\0'):
                    with self.assertRaises(ProtocolError):decode_battle(bad)


class AuxiliaryRelayBoundaryTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join
    battle=fixtures.SharedRoomTests.battle

    def test_decoding_does_not_open_unqualified_auxiliary_mutation(self):
        self.battle()
        for ident in SHAPES.keys()-{8122,8125,8278,8286,8293}:
            self.assertEqual(self.e1.handle(self.c1,Message(8071,bytes(packet(ident)))),[])
            self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(self.e1.room.last_sequence,{})


if __name__=='__main__':
    unittest.main()
