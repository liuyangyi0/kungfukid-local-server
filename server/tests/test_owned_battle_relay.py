"""End-to-end Engine/RoomHub behavior for the qualified owned-control family."""
import struct
import unittest
from server.kk_local.wire import Message, ProtocolError
from server.tests import test_shared_rooms as fixtures


SIZES={8122:51,8125:53,8143:59,8280:55,8293:51}


def message(ident,seq=0,sender=1001,player=1001,target=1002):
    p=bytearray(SIZES[ident]);struct.pack_into('<IQ',p,0,ident,sender)
    p[12:14]=b'\x01\x01';struct.pack_into('<I',p,19,seq)
    struct.pack_into('<Q',p,39,player)
    if ident==8122:struct.pack_into('<I',p,47,3)
    elif ident==8125:struct.pack_into('<IH',p,47,6001303,2)
    elif ident==8293:struct.pack_into('<I',p,47,0xffffffff)
    else:
        struct.pack_into('<Q',p,47,target)
        if ident==8143:struct.pack_into('<I',p,55,15000)
    return Message(8071,bytes(p))


class OwnedBattleRelayTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join
    battle=fixtures.SharedRoomTests.battle

    def test_owned_families_reach_other_player_byte_exact_once(self):
        self.battle()
        for seq,ident in enumerate(SIZES):
            m=message(ident,seq)
            self.assertEqual(self.e1.handle(self.c1,m),[])
            self.assertEqual(self.e2.take_pending(self.c2),[m])
            self.assertEqual(self.e1.handle(self.c1,m),[])
            self.assertEqual(self.e2.take_pending(self.c2),[])

    def test_action_selector_raw_values_are_not_weapon_or_direction_enums(self):
        self.battle()
        for seq,value in enumerate((0xffffffff,0,1,0x80000000)):
            p=bytearray(message(8293,seq).payload);struct.pack_into('<I',p,47,value)
            m=Message(8071,bytes(p));self.e1.handle(self.c1,m)
            self.assertEqual(self.e2.take_pending(self.c2),[m])

    def test_unknown_special_actor_selector_is_not_admitted_or_a_forced_disconnect(self):
        self.battle()
        self.assertEqual(self.e1.handle(self.c1,message(8293,player=9000)),[])
        self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(self.e1.room.last_sequence,{})

    def test_reverse_direction_is_bound_to_second_account(self):
        self.battle()
        m=message(8143,sender=1002,player=1002,target=1001)
        self.assertEqual(self.e2.handle(self.c2,m),[])
        self.assertEqual(self.e1.take_pending(self.c1),[m])

    def test_zero_target_clears_without_rewriting_payload(self):
        self.battle()
        for seq,ident in enumerate((8143,8280)):
            m=message(ident,seq,target=0)
            self.e1.handle(self.c1,m)
            self.assertEqual(self.e2.take_pending(self.c2),[m])

    def test_actor_and_sender_spoof_rejected_without_sequence_change(self):
        self.battle()
        for ident in SIZES:
            for kwargs in ({'sender':1002},{'player':1002}):
                with self.assertRaises(ProtocolError):self.e1.handle(self.c1,message(ident,99,**kwargs))
        self.assertEqual(self.e1.room.last_sequence,{})
        self.assertEqual(self.e2.take_pending(self.c2),[])

    def test_foreign_target_rejected_without_poisoning_counter(self):
        self.battle()
        for ident in (8143,8280):
            with self.assertRaises(ProtocolError):self.e1.handle(self.c1,message(ident,99,target=9000))
        self.assertEqual(self.e1.room.last_sequence,{})
        m=message(8143,0);self.e1.handle(self.c1,m)
        self.assertEqual(self.e2.take_pending(self.c2),[m])

    def test_no_relay_before_battle_or_after_result(self):
        self.join()
        self.assertEqual(self.e1.handle(self.c1,message(8122)),[])
        self.assertEqual(self.e2.take_pending(self.c2),[])
        # Set up a real started room in a fresh fixture for the result boundary.
        self.h.leave(self.e1);self.h.leave(self.e2)
        self.e1.take_pending(self.c1);self.e2.take_pending(self.c2)
        self.battle();self.e1.room.stage='result'
        self.assertEqual(self.e1.handle(self.c1,message(8122)),[])
        self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(self.e1.room.last_sequence,{})

    def test_bad_length_and_wrong_transport_header_do_not_forward(self):
        self.battle()
        for ident in SIZES:
            m=message(ident)
            with self.assertRaises(ProtocolError):self.e1.handle(self.c1,Message(8071,m.payload[:-1]))
            p=bytearray(m.payload);p[13]=0
            self.assertEqual(self.e1.handle(self.c1,Message(8071,bytes(p))),[])
        self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(self.e1.room.last_sequence,{})


if __name__=='__main__':
    unittest.main()
