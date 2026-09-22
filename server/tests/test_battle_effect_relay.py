"""Provisional owned-simulation relay; no server damage formula or rewards."""
import struct
import unittest
from server.kk_local.wire import Message, ProtocolError
from server.tests import test_shared_rooms as fixtures


def effect(ident,room,*,sender=1001,target=1001,source=1002,seq=0,amount=10,operation=1,code=12):
    p=bytearray(94 if ident==8121 else 87)
    struct.pack_into('<IQ',p,0,ident,sender);p[12:14]=b'\x01\x01'
    struct.pack_into('<I',p,19,seq);struct.pack_into('<QQ',p,39,target,source)
    if ident==8121:
        struct.pack_into('<f',p,67,amount)
        struct.pack_into('<II',p,86,room.number,room.serial)
    else:
        struct.pack_into('<I',p,55,code)
        struct.pack_into('<III',p,75,operation,room.number,room.serial)
    return Message(8071,bytes(p))


class EffectRelayTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join
    battle=fixtures.SharedRoomTests.battle

    def test_self_damage_and_heal_fanout_once_without_echo_or_database_change(self):
        self.battle();before=self.s.snapshot(1001)
        for seq,amount in enumerate((12.5,-3.0,0.0)):
            m=effect(8121,self.e1.room,seq=seq,amount=amount)
            self.assertEqual(self.e1.handle(self.c1,m),[])
            self.assertEqual(self.e2.take_pending(self.c2),[m])
            self.assertEqual(self.e1.handle(self.c1,m),[])
            self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(self.s.snapshot(1001),before)

    def test_environment_source_zero_and_second_player_reports_self(self):
        self.battle()
        m=effect(8121,self.e1.room,sender=1002,target=1002,source=0)
        self.assertEqual(self.e2.handle(self.c2,m),[])
        self.assertEqual(self.e1.take_pending(self.c1),[m])

    def test_unsupported_cross_target_is_not_a_disconnect_and_stale_battle_rejected(self):
        self.battle()
        self.assertEqual(self.e1.handle(self.c1,effect(8121,self.e1.room,target=1002)),[])
        m=effect(8121,self.e1.room);p=bytearray(m.payload)
        struct.pack_into('<I',p,90,self.e1.room.serial+1)
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,Message(8071,bytes(p)))
        self.assertEqual(self.e1.room.last_sequence,{})

    def test_absent_source_is_attribution_not_an_account_spoof(self):
        self.battle()
        m=effect(8121,self.e1.room,source=9999)
        self.assertEqual(self.e1.handle(self.c1,m),[])
        self.assertEqual(self.e2.take_pending(self.c2),[m])

    def test_host_cancel_requires_prior_admission_then_removes_ledger_entry(self):
        self.battle();room=self.e1.room
        cancel=effect(8150,room,target=1002,source=0,operation=0)
        self.assertEqual(self.e1.handle(self.c1,cancel),[])
        self.assertEqual(room.last_sequence,{})
        apply=effect(8150,room,sender=1002,target=1002,source=1002)
        self.e2.handle(self.c2,apply);self.e1.take_pending(self.c1)
        self.assertIn((1002,12),room.active_states)
        self.assertEqual(self.e1.handle(self.c1,cancel),[])
        self.assertEqual(self.e2.take_pending(self.c2),[cancel])
        self.assertNotIn((1002,12),room.active_states)
        self.e1.handle(self.c1,cancel)
        self.assertEqual(self.e2.take_pending(self.c2),[])

    def test_nonhost_cannot_cancel_other_player_and_host_cannot_cross_apply(self):
        self.battle();room=self.e1.room
        cases=((self.e2,self.c2,effect(8150,room,sender=1002,target=1001,source=0,operation=0)),
               (self.e1,self.c1,effect(8150,room,target=1002,source=1001)))
        for engine,connection,m in cases:
            self.assertEqual(engine.handle(connection,m),[])
        self.assertEqual(room.active_states,{})

    def test_cancel_parameters_are_zero_and_new_round_clears_state_history(self):
        self.battle();room=self.e1.room
        self.e1.handle(self.c1,effect(8150,room));self.e2.take_pending(self.c2)
        p=bytearray(effect(8150,room,source=0,operation=0,seq=1).payload)
        struct.pack_into('<I',p,63,1)
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,Message(8071,bytes(p)))
        self.assertIn((1001,12),room.active_states)
        room.stage='room'
        from server.kk_local.engine import Phase
        for member in room.members.values():member.ready=True;member.engine.game.phase=Phase.ROOM
        self.e1.handle(self.c1,Message(4030))
        self.assertEqual(room.active_states,{})

    def test_out_of_registry_code_cannot_grow_ledger_or_take_sequence(self):
        self.battle();room=self.e1.room
        for code in (256,0xffffffff):
            with self.assertRaises(ProtocolError):
                self.e1.handle(self.c1,effect(8150,room,code=code,seq=999))
        self.assertEqual(room.active_states,{})
        self.assertEqual(room.last_sequence,{})


if __name__=='__main__':unittest.main()
