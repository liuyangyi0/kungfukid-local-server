"""Owned8284 copies native throw commands, not permanent inventory changes."""
import struct
import unittest
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_shared_rooms as fixtures


def throw(room,*,sender=1001,player=1001,operation=1,argument=0,seq=0):
    p=bytearray(75);struct.pack_into('<IQ',p,0,8284,sender)
    p[12:14]=b'\x01\x01';struct.pack_into('<I',p,19,seq)
    struct.pack_into('<II',p,39,operation,argument)
    # Unclosed action/weapon-specific fields are kept as bits, not a guessed Vec3.
    p[47:59]=bytes.fromhex('ffffffff 00000080 a5a5a5a5')
    struct.pack_into('<QII',p,59,player,room.number,room.serial)
    return Message(8071,bytes(p))


class WeaponThrowRelayTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join
    battle=fixtures.SharedRoomTests.battle

    def test_three_native_operations_forward_once_without_inventory_mutation(self):
        self.battle();before=[self.s.snapshot(u) for u in (1001,1002)]
        for seq,operation in enumerate((1,2,3)):
            msg=throw(self.e1.room,operation=operation,argument=0xffffffff,seq=seq)
            self.assertEqual(self.e1.handle(self.c1,msg),[])
            self.assertEqual(self.e2.take_pending(self.c2),[msg])
            self.e1.handle(self.c1,msg)
            self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual([self.s.snapshot(u) for u in (1001,1002)],before)

    def test_unknown_operation_or_special_actor_does_not_take_sequence(self):
        self.battle()
        for operation in (0,4,0xffffffff):self.e1.handle(self.c1,throw(self.e1.room,operation=operation,seq=999))
        self.e1.handle(self.c1,throw(self.e1.room,player=9999,seq=999))
        self.assertEqual(self.e1.room.last_sequence,{})
        self.assertEqual(self.e2.take_pending(self.c2),[])
        msg=throw(self.e1.room,operation=3,argument=0)
        self.e1.handle(self.c1,msg)
        self.assertEqual(self.e2.take_pending(self.c2),[msg])  # native receiver decides no-op

    def test_transport_identity_owned_actor_and_battle_pair_remain_required(self):
        self.battle();room=self.e1.room
        for msg in (throw(room,sender=1002),throw(room,player=1002)):
            with self.assertRaises(ProtocolError):self.e1.handle(self.c1,msg)
        raw=bytearray(throw(room).payload);struct.pack_into('<I',raw,71,room.serial+1)
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,Message(8071,bytes(raw)))
        self.assertEqual(room.last_sequence,{})
        msg=throw(room,sender=1002,player=1002)
        self.e2.handle(self.c2,msg)
        self.assertEqual(self.e1.take_pending(self.c1),[msg])


if __name__=='__main__':unittest.main()
