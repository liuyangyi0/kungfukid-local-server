"""4080 field meaning is native; choosing the room owner is local policy."""
import struct
import unittest

from server.kk_local import packets
from server.kk_local.wire import Message, ProtocolError
from server.tests import test_shared_rooms as fixtures


class BattleHostTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join

    def test_both_recipients_receive_same_host_and_roster(self):
        self.join()
        self.e2.handle(self.c2,Message(4030));self.e1.take_pending(self.c1)
        a=self.e1.handle(self.c1,Message(4030))
        b=self.e2.take_pending(self.c2)
        pa=next(m.payload for m in a if m.id==4080)
        pb=next(m.payload for m in b if m.id==4080)
        self.assertEqual(pa,pb)
        self.assertEqual(struct.unpack_from('<H',pa,11)[0],0)
        self.assertEqual(struct.unpack_from('<II',pa,13),(0,0))  # unmeasured delay, not P2P handles
        self.assertEqual((self.c1.uid,self.c2.uid),(1001,1002))

    def test_transferred_owner_in_slot_one_not_lowest_slot_is_host(self):
        self.join()
        self.e1.handle(self.c1,Message(3110));self.e2.take_pending(self.c2)
        self.e1.handle(self.c1,Message(3070,struct.pack('<HB11s',1,0,b'')))
        self.e2.take_pending(self.c2)
        self.assertEqual(self.e1.room.owner,1002)
        self.e1.handle(self.c1,Message(4030));self.e2.take_pending(self.c2)
        b=self.e2.handle(self.c2,Message(4030));a=self.e1.take_pending(self.c1)
        pa=next(m.payload for m in a if m.id==4080)
        pb=next(m.payload for m in b if m.id==4080)
        self.assertEqual(pa,pb)
        self.assertEqual(struct.unpack_from('<H',pa,11)[0],1)
        self.assertEqual(struct.unpack_from('<II',pa,13),(0,0))

    def test_invalid_or_unoccupied_host_slot_rejected(self):
        for host in (-1,8,1,None):
            with self.assertRaises(ProtocolError):
                packets.battle_start_members(1,2,host,{0:1001})
        self.assertEqual(len(packets.battle_start_members(1,2,0,{0:1001}).payload),53)


if __name__=='__main__':
    unittest.main()
