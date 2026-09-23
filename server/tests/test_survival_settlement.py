"""Ordinary service entry and distinct individual/team survival policies."""
import struct
import unittest
from server.kk_local.rooms import RoomHub
from server.kk_local.public_policy import PublicPolicy
from server.kk_local.public_metrics import Metrics
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

    def test_public_no_award_survival_disagreement_returns_draw_and_keeps_room(self):
        self.hub.public_policy=PublicPolicy()
        self.hub.permanent_battle_rewards_allowed=False
        metrics=Metrics();self.hub._battle.metrics=metrics
        self.a.handle(self.ca,Message(4110,self.report((100,0))))
        out=self.b.handle(self.cb,Message(4110,self.report((100,1))))
        first=self.a.take_pending(self.ca)
        self.assertEqual([m.id for m in first],[4120])
        self.assertEqual([m.id for m in out],[4120])
        self.assertEqual((out[0].payload[10],out[0].payload[510]),(0,0))
        self.assertEqual(metrics.counts['settlement_hp_disagreement_safe_draw'],1)
        self.assertEqual(self.before,[self.store.snapshot(u) for u in (1001,1002)])
        self.a.handle(self.ca,Message(4115,struct.pack('<I',180000)))
        self.b.handle(self.cb,Message(4115,struct.pack('<I',180000)))
        self.assertEqual(self.a.room.stage,'room')
        self.assertEqual(set(self.a.room.members),{1001,1002})

    def test_public_survival_winner_survives_numeric_hp_drift(self):
        self.hub.public_policy=PublicPolicy()
        self.hub.permanent_battle_rewards_allowed=False
        metrics=Metrics();self.hub._battle.metrics=metrics
        self.a.handle(self.ca,Message(4110,self.report((80,0))))
        out=self.b.handle(self.cb,Message(4110,self.report((60,0))))
        self.assertEqual((out[0].payload[10],out[0].payload[510]),(1,2))
        self.assertEqual(metrics.counts['settlement_hp_disagreement_safe_draw'],0)
        self.assertEqual(self.before,[self.store.snapshot(u) for u in (1001,1002)])


if __name__=='__main__':unittest.main()
