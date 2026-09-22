"""Host-generated temporary world objects; never persistent currency grants."""
import struct
import unittest
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_shared_rooms as fixtures


def spawn(kind=1,key=123,seq=0,sender=1001):
    p=bytearray(63);struct.pack_into('<IQ',p,0,8287,sender)
    p[12:14]=b'\x01\x01';struct.pack_into('<I',p,19,seq)
    struct.pack_into('<IIIfff',p,39,kind,key,2,10,0,-20)
    return Message(8071,bytes(p))


class CollectibleSpawnTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join
    battle=fixtures.SharedRoomTests.battle

    def test_host_spawns_gem_and_coin_same_key_separate_native_managers(self):
        self.battle();before=self.s.snapshot(1001);gold=self.s.gold_balance(1001)
        for seq,kind in enumerate((1,3)):
            m=spawn(kind,seq=seq)
            self.assertEqual(self.e1.handle(self.c1,m),[])
            self.assertEqual(self.e2.take_pending(self.c2),[m])
        self.assertEqual(len(self.e1.room.spawned_collectibles),2)
        self.assertEqual(self.s.snapshot(1001),before);self.assertEqual(self.s.gold_balance(1001),gold)

    def test_duplicate_kind_two_cannot_create_second_gem_with_same_key(self):
        self.battle();self.e1.handle(self.c1,spawn());self.e2.take_pending(self.c2)
        for m in (spawn(),spawn(2,seq=1)):
            self.assertEqual(self.e1.handle(self.c1,m),[])
            self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(self.e1.room.last_sequence[(1001,19)],0)

    def test_nonhost_and_unknown_kind_cannot_register(self):
        self.battle()
        with self.assertRaises(ProtocolError):self.e2.handle(self.c2,spawn(sender=1002))
        self.assertEqual(self.e1.handle(self.c1,spawn(99)),[])
        self.assertEqual(self.e1.room.spawned_collectibles,{})
        self.assertEqual(self.e1.room.last_sequence,{})

    def test_new_round_clears_spawn_keys(self):
        self.battle();room=self.e1.room;self.e1.handle(self.c1,spawn());self.e2.take_pending(self.c2)
        from server.kk_local.engine import Phase
        room.stage='room'
        for member in room.members.values():member.ready=True;member.engine.game.phase=Phase.ROOM
        self.e1.handle(self.c1,Message(4030))
        self.assertEqual(room.spawned_collectibles,{})


if __name__=='__main__':unittest.main()
