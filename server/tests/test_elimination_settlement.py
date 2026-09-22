"""Mode2/3 local reported-counter policy, kept separate from native verdicts."""
import struct
import unittest
from server.kk_local.lab_settlement import decode_report,consensus_elimination
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_lab_settlement as fixtures


class EliminationSettlementTests(unittest.TestCase):
    mode=2
    setUp=fixtures.LabSettlementTests.setUp
    tearDown=fixtures.LabSettlementTests.tearDown
    report=fixtures.LabSettlementTests.report

    def scores(self,a=(6,0),b=(5,0),hp=(0,100)):
        p=bytearray(self.report(hp))
        p[8],p[9]=a;p[95],p[96]=b
        struct.pack_into('<H',p,15,400);struct.pack_into('<H',p,102,500)
        return bytes(p)

    def settle(self,p):
        self.assertEqual(self.a.handle(self.ca,Message(4110,p)),[])
        out=self.b.handle(self.cb,Message(4110,p))
        self.assertEqual([m.id for m in out],[4120])
        return out[0].payload[10],out[0].payload[510]

    def test_individual_score_not_hp_or_victim_deaths(self):
        self.assertEqual(self.settle(self.scores()),(1,2))
        self.assertEqual(self.before,[self.store.snapshot(u) for u in (1001,1002)])

    def test_penalty_kills_subtracted_and_tie_is_draw(self):
        self.assertEqual(self.settle(self.scores((7,2),(5,0))),(0,0))

    def test_team_score_credits_opposing_penalties(self):
        q=bytearray(self.a.room.request);q[46]=3;self.a.room.request=bytes(q)
        self.assertEqual(self.settle(self.scores((4,2),(3,0))),(2,1))

    def test_disagreement_rejects_without_accepting_second_report(self):
        self.a.handle(self.ca,Message(4110,self.scores()))
        with self.assertRaisesRegex(ProtocolError,'score disagreement'):
            self.b.handle(self.cb,Message(4110,self.scores((5,0),(5,0))))
        self.assertEqual(set(self.a.room.result_reports),{1001})
        self.assertFalse(self.a.room.result_replies)

    def test_wire_byte_counters_not_promoted_to_full_native_words(self):
        p=self.scores((255,254),(1,0));room=self.a.room
        rows=decode_report(p,{0:1001,1:1002},room.number,room.serial)
        self.assertEqual((rows[1001].enemy_kills_low8,rows[1001].penalty_kills_low8,rows[1001].deaths_u16),
                         (255,254,400))
        self.assertEqual(consensus_elimination({1001:rows,1002:rows},{1001:0,1002:1},team_mode=False),
                         {1001:0,1002:0})

    def test_individual_colors_do_not_create_allies(self):
        self.a.room.members[1001].team=0;self.a.room.members[1002].team=0
        self.assertEqual(self.settle(self.scores()),(1,2))


if __name__=='__main__':unittest.main()
