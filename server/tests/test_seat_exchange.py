import struct
import unittest
from server.kk_local.wire import Message
from server.tests import test_team_positions as fixture


class SeatTests(unittest.TestCase):
    setUp=fixture.TeamPositionTests.setUp
    tearDown=fixture.TeamPositionTests.tearDown
    create=fixture.TeamPositionTests.create

    def test_consent_swaps_position_and_team_without_slot_or_equipment_changes(self):
        self.create(mode=3);room=self.e1.room
        before=[self.s.snapshot(u) for u in (1001,1002)]
        p=struct.pack('<QQII',1001,1002,0,4)
        self.assertEqual(self.e1.handle(self.c1,Message(3260,p)),[])
        self.assertEqual(self.e2.take_pending(self.c2),[Message(3261,p)])
        self.assertEqual(self.e2.handle(self.c2,Message(3262,p)),[Message(3265,p)])
        self.assertEqual(self.e1.take_pending(self.c1),[Message(3265,p)])
        self.assertEqual([(m.slot,m.registry_key,m.team) for m in room.members.values()],[(0,4,1),(1,0,0)])
        self.assertEqual(before,[self.s.snapshot(u) for u in (1001,1002)])
        self.assertEqual(self.e2.handle(self.c2,Message(3262,p)),[Message(3264)])

    def test_empty_position_stays_on_same_side_and_cannot_overlap(self):
        self.create(mode=3)
        p=struct.pack('<QQII',1001,0,0,2)
        self.assertEqual(self.e1.handle(self.c1,Message(3260,p)),[Message(3265,p)])
        self.assertEqual(self.e1.room.members[1001].registry_key,2)
        self.e1.take_pending(self.c1)
        for dest in (4,5):
            self.assertEqual(self.e1.handle(self.c1,Message(3260,struct.pack('<QQII',1001,0,2,dest))),[Message(3264)])

    def test_decline_expiry_and_connection_replacement_do_not_move_players(self):
        self.create(mode=3);now=[100.0];self.e1.clock=self.e2.clock=lambda:now[0]
        p=struct.pack('<QQII',1001,1002,0,4)
        self.e1.handle(self.c1,Message(3260,p));self.e2.take_pending(self.c2)
        self.e2.handle(self.c2,Message(3263,p))
        self.assertEqual(self.e1.take_pending(self.c1),[Message(3264)])
        self.e1.handle(self.c1,Message(3260,p));self.e2.take_pending(self.c2)
        now[0]+=31;self.h.expire()
        self.assertIsNone(self.e1.room.seat_exchange)
        self.assertEqual(self.e1.room.members[1001].registry_key,0)

    def test_readied_target_and_unqualified_mode_are_rejected(self):
        self.create(mode=1)
        p=struct.pack('<QQII',1001,1002,0,4)
        self.assertEqual(self.e1.handle(self.c1,Message(3260,p)),[Message(3264)])
