"""3550 updates remote room labels, not authoritative battle state."""
import struct
import unittest
from server.kk_local.engine import Phase
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_shared_rooms as fixtures
from server.tests import test_lab_settlement as results
from server.tests.test_local_service import create_room


def activity(uid,value):return Message(3550,struct.pack('<QI',uid,value))


class RoomActivityTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join
    battle=fixtures.SharedRoomTests.battle

    def test_labels_and_clear_reach_peer_only_without_changing_ready_or_profile(self):
        self.join();before=self.s.snapshot(1001)
        for value in (1,2,3,0):
            msg=activity(1001,value)
            self.assertEqual(self.e1.handle(self.c1,msg),[])
            self.assertEqual(self.e2.take_pending(self.c2),[msg])
            self.assertEqual(self.e1.handle(self.c1,msg),[])
            self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertFalse(self.e1.room.members[1001].ready)
        self.assertEqual(self.s.snapshot(1001),before)

    def test_identity_enum_and_battle_boundary(self):
        self.battle();room=self.e1.room
        for message in (Message(3550,b''),activity(1002,3),activity(1001+(1<<32),3)):
            with self.assertRaises(ProtocolError):self.e1.handle(self.c1,message)
        for value in (0,1,2,3,4,0xffffffff):self.e1.handle(self.c1,activity(1001,value))
        self.assertEqual(room.members[1001].activity,0)
        self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(self.c1.phase,Phase.BATTLE)
        room.stage='result'
        self.e1.handle(self.c1,activity(1001,3))
        self.assertEqual(room.members[1001].activity,0)  #no4115 yet

    def test_new_peer_receives_current_label_after_member_creation(self):
        self.e1.handle(self.c1,create_room())
        self.e1.handle(self.c1,activity(1001,2))
        out=self.e2.handle(self.c2,Message(3070,struct.pack('<HB11s',1,0,b'')))
        self.assertEqual([m.id for m in out],[3100,3160,3090,3550])
        self.assertEqual(out[-1],activity(1001,2))


class ResultActivityTests(unittest.TestCase):
    setUp=results.LabSettlementTests.setUp
    tearDown=results.LabSettlementTests.tearDown
    report=results.LabSettlementTests.report

    def test_result_label_deferred_until_all_acks_then_cleared_on_return(self):
        p=self.report()
        self.a.handle(self.ca,Message(4110,p));self.b.take_pending(self.cb)
        self.b.handle(self.cb,Message(4110,p));self.a.take_pending(self.ca)
        self.a.handle(self.ca,Message(4115,bytes(4)))
        self.a.handle(self.ca,activity(1001,3))
        self.assertEqual(self.b.take_pending(self.cb),[])
        out=self.b.handle(self.cb,Message(4115,bytes(4)))
        self.assertEqual([m for m in out if m.id==3550],[activity(1001,3)])
        self.a.take_pending(self.ca)
        self.b.handle(self.cb,activity(1002,3));self.a.take_pending(self.ca)
        self.a.handle(self.ca,activity(1001,0))
        self.assertEqual(self.b.take_pending(self.cb),[activity(1001,0)])
        self.b.handle(self.cb,activity(1002,0))
        self.assertEqual(self.a.take_pending(self.ca),[activity(1002,0)])


if __name__=='__main__':unittest.main()
