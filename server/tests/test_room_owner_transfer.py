"""4051 changes actual room authority;4052 is not the owner update itself."""
import struct
import unittest
from server.kk_local.engine import Phase,Connection
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_shared_rooms as fixtures


def transfer(room=1,target=1002):
    return Message(4051,struct.pack('<IQ',room,target))


class OwnerTransferTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join
    battle=fixtures.SharedRoomTests.battle

    def test_transfer_resets_ready_notifies_and_changes_start_authority(self):
        self.join();room=self.e1.room
        self.e2.handle(self.c2,Message(4030));self.e1.take_pending(self.c1)
        old_members=tuple(room.members)
        out=self.e1.handle(self.c1,transfer())
        self.assertEqual([m.id for m in out],[4070,3160,4052])
        self.assertEqual(out[-1].payload,b'\0\0')
        self.assertEqual(struct.unpack('<Q',out[-2].payload)[0],1002)
        self.assertEqual([m.id for m in self.e2.take_pending(self.c2)],[4070,3160])
        self.assertEqual((room.owner,tuple(room.members)),(1002,old_members))
        self.assertTrue(all(not m.ready for m in room.members.values()))
        self.assertEqual([m.id for m in self.e1.handle(self.c1,Message(4030))],[4050])
        self.e2.take_pending(self.c2)
        out=self.e2.handle(self.c2,Message(4030))
        self.assertEqual([m.id for m in out],[4050,4080])
        self.assertEqual(struct.unpack_from('<H',out[-1].payload,11)[0],1)

    def test_nonowner_replay_wrong_room_self_and_absent_target_do_not_mutate(self):
        self.join();room=self.e1.room
        for engine,c,request,error in (
            (self.e2,self.c2,transfer(target=1001),129),
            (self.e1,self.c1,transfer(room=2),29),
            (self.e1,self.c1,transfer(target=1001),129),
            (self.e1,self.c1,transfer(target=9999),34),
        ):
            self.assertEqual(engine.handle(c,request),[Message(4052,struct.pack('<H',error))])
            self.assertEqual(room.owner,1001)
        self.e1.handle(self.c1,transfer());self.e2.take_pending(self.c2)
        self.assertEqual(self.e1.handle(self.c1,transfer()),[Message(4052,struct.pack('<H',129))])
        self.assertEqual(self.e2.take_pending(self.c2),[])

    def test_loading_battle_and_unbound_or_suspended_target_are_rejected(self):
        self.join();room=self.e1.room
        self.e2.p2p['bound']=False
        self.assertEqual(self.e1.handle(self.c1,transfer()),[Message(4052,struct.pack('<H',34))])
        self.e2.p2p['bound']=True
        self.h.suspended[1002]=self.e2.clock()+30
        self.assertEqual(self.e1.handle(self.c1,transfer()),[Message(4052,struct.pack('<H',34))])
        self.h.suspended.clear()
        room.stage='loading'
        self.assertEqual(self.e1.handle(self.c1,transfer()),[Message(4052,struct.pack('<H',30))])
        room.stage='battle';self.c1.phase=Phase.BATTLE
        self.assertEqual(self.e1.handle(self.c1,transfer()),[])
        self.assertEqual(room.owner,1001)

    def test_exact_connection_length_and_full_target_uid(self):
        self.join()
        self.assertEqual(self.e1.handle(Connection(99,Phase.ROOM,1001),transfer()),[])
        for p in (b'',bytes(8),bytes(13)):
            with self.assertRaises(ProtocolError):self.e1.handle(self.c1,Message(4051,p))
        # Low32 matching a real UID is insufficient.
        self.assertEqual(self.e1.handle(self.c1,transfer(target=(1<<32)+1002)),
                         [Message(4052,struct.pack('<H',34))])
        self.assertEqual(self.e1.room.owner,1001)


if __name__=='__main__':unittest.main()
