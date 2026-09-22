"""Ordinary service entry and distinct individual/team survival policies."""
import struct
import unittest
from server.kk_local.rooms import RoomHub
from server.kk_local.wire import Message
from server.tests import test_lab_settlement as fixtures


class IndividualSurvivalTests(unittest.TestCase):
    mode=0
    setUp=fixtures.LabSettlementTests.setUp
    tearDown=fixtures.LabSettlementTests.tearDown
    report=fixtures.LabSettlementTests.report

    def test_individual_survivor_not_everyone_with_same_team_color(self):
        self.a.room.members[1001].team=0;self.a.room.members[1002].team=0
        p=self.report((100,0))
        self.a.handle(self.ca,Message(4110,p));out=self.b.handle(self.cb,Message(4110,p))
        self.assertEqual((out[0].payload[10],out[0].payload[510]),(1,2))
        self.assertEqual(self.before,[self.store.snapshot(u) for u in (1001,1002)])

    def test_all_dead_is_draw_and_all_acknowledgements_reopen_room(self):
        p=self.report((0,0))
        self.a.handle(self.ca,Message(4110,p));out=self.b.handle(self.cb,Message(4110,p))
        self.a.take_pending(self.ca)
        self.assertEqual((out[0].payload[10],out[0].payload[510]),(0,0))
        self.a.handle(self.ca,Message(4115,struct.pack('<I',180000)))
        self.assertEqual(self.a.room.stage,'result')
        self.b.handle(self.cb,Message(4115,struct.pack('<I',180000)))
        self.assertEqual(self.a.room.stage,'room')

    def test_training_mode_not_settled_using_survival_policy(self):
        request=bytearray(self.a.room.request);request[46]=5;self.a.room.request=bytes(request)
        self.assertEqual(self.a.handle(self.ca,Message(4110,self.report())),[])
        self.assertEqual(self.a.room.result_reports,{})

    def test_normal_hub_enables_no_award_survival_without_special_launcher(self):
        self.assertTrue(RoomHub().lab_no_award_settlement)
        self.assertFalse(RoomHub(lab_no_award_settlement=False).lab_no_award_settlement)


if __name__=='__main__':unittest.main()
