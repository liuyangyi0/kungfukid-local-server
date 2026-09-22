"""3090+76 is observer storage, not refresh; exercise real equipment changes."""
import struct
import unittest
from server.kk_local.wire import Message
from server.tests import test_shared_rooms as fixtures


class ActiveRosterRefreshTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join

    def test_peer_equipment_and_unequip_stay_on_active_slot_branch(self):
        self.s.apply_grant(dict(schema='kk-local-inventory-grant-v1',uid=1001,
            grant_id='active-refresh',rows=[[253033,25,1]]))
        instance=next(i for i,r in self.s.db.execute('SELECT instance,record FROM inventory WHERE uid=1001')
                      if struct.unpack_from('<I',r,5)[0]==253033)
        self.join();room=self.e2.room
        for command,expected_count in ((Message(2080,struct.pack('<IIQ',instance,8,0)),7),
                                       (Message(2300,struct.pack('<I',instance)),6)):
            self.e1.handle(self.c1,command)
            peer=self.e2.take_pending(self.c2)
            self.assertEqual([m.id for m in peer],[3090])
            record=peer[0].payload
            self.assertEqual((struct.unpack_from('<Q',record)[0],record[8],record[9],record[76]),
                             (1001,0,0,0))
            self.assertEqual(record[64],expected_count)
            self.assertEqual(len(record),149+expected_count*68)
            items=[record[o:o+68] for o in range(149,len(record),68)]
            ids=[struct.unpack_from('<I',item,5)[0] for item in items]
            self.assertEqual(253033 in ids,command.id==2080)
            self.assertEqual((room.owner,len(room.members)),(1001,2))

    def test_new_ready_state_preserved_in_reconnect_active_record(self):
        self.join();self.e2.disconnect(self.c2);self.e1.take_pending(self.c1)
        self.c2=fixtures.lobby(self.e2,11)
        restored=self.e1.take_pending(self.c1)[-1].payload
        self.assertEqual(restored[76],0)
        self.assertEqual(restored[53],0)
        self.assertEqual((restored[8],restored[9]),(1,1))


if __name__=='__main__':unittest.main()
