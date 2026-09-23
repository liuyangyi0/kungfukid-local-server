"""Configured local points: durable, atomic and not original-server XP/gold."""
import struct
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from server.kk_local.store import Store
from server.kk_local.rooms import RoomHub
from server.kk_local.__main__ import match_point_policy
from server.kk_local.wire import Message
from server.tests import test_lab_settlement as fixtures


RATES={0:25,1:100,2:10}


class MatchPointStoreTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(':memory:');self.store.seed_local();self.store.provision_local(1002,'Second')
    def tearDown(self):self.store.close()

    def test_exact_profile_field_changes_once_and_other_state_preserved(self):
        before={u:self.store.snapshot(u) for u in (1001,1002)}
        profiles=self.store.award_match_points(1,{1001:1,1002:2},RATES)
        for uid,award in ((1001,100),(1002,10)):
            old=before[uid][2];expected=bytearray(old)
            struct.pack_into('<I',expected,245,struct.unpack_from('<I',old,245)[0]+award)
            self.assertEqual(profiles[uid],bytes(expected))
            self.assertEqual(self.store.snapshot(uid)[3],before[uid][3])
        self.assertEqual(self.store.award_match_points(1,{1002:2,1001:1},RATES),profiles)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM match_point_grants').fetchone()[0],2)

    def test_changed_outcome_or_rate_retry_is_rejected(self):
        before=self.store.award_match_points(1,{1001:1},RATES)
        for outcomes,rates in (({1001:2},RATES),({1001:1},{0:25,1:200,2:10})):
            with self.assertRaises(ValueError):self.store.award_match_points(1,outcomes,rates)
        self.assertEqual(self.store.snapshot(1001)[2],before[1001])

    def test_failure_for_second_player_rolls_back_entire_batch(self):
        before=self.store.snapshot(1001)
        with self.assertRaises(ValueError):self.store.award_match_points(1,{1001:1,9999:2},RATES)
        self.assertEqual(self.store.snapshot(1001),before)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM match_point_batches').fetchone()[0],0)

    def test_saturation_records_actual_award_and_never_wraps(self):
        p=bytearray(self.store.snapshot(1001)[2]);struct.pack_into('<I',p,245,0x7ffffffe)
        with self.store.db:self.store.db.execute('UPDATE accounts SET profile=? WHERE uid=1001',(bytes(p),))
        self.store.award_match_points(1,{1001:1},RATES)
        self.assertEqual(struct.unpack_from('<I',self.store.snapshot(1001)[2],245)[0],0x7fffffff)
        self.assertEqual(self.store.db.execute('SELECT awarded FROM match_point_grants').fetchone()[0],1)
        self.assertEqual(self.store.match_point_awards(1),{1001:1})

    def test_retry_after_restart_does_not_duplicate_points(self):
        with tempfile.TemporaryDirectory() as temp:
            path=str(Path(temp)/'local.sqlite3')
            s=Store(path);s.seed_local();first=s.award_match_points(9,{1001:0},RATES);s.close()
            s=Store(path)
            try:self.assertEqual(s.award_match_points(9,{1001:0},RATES),first)
            finally:s.close()

    def test_retry_does_not_restore_profile_before_a_later_training_award(self):
        self.store.award_match_points(1,{1001:1},RATES)
        self.store.training(1001,0,start=True)
        self.store.claim_training(1001,3600)
        latest=self.store.snapshot(1001)[2]
        self.assertEqual(self.store.award_match_points(1,{1001:1},RATES)[1001],latest)
        self.assertEqual(self.store.snapshot(1001)[2],latest)

    def test_policy_is_explicit_and_validated(self):
        self.assertIsNone(match_point_policy(SimpleNamespace()))
        self.assertEqual(match_point_policy(SimpleNamespace(match_win_points=100)),{0:0,1:100,2:0})
        for rates in ({0:0,1:-1,2:0},{0:0,1:True,2:0},{1:100}):
            with self.assertRaises(ValueError):RoomHub(match_point_rewards=rates)
        with self.assertRaises(ValueError):self.store.award_match_points(1,{2**64:1},RATES)


class MatchPointIntegrationTests(unittest.TestCase):
    setUp=fixtures.LabSettlementTests.setUp
    tearDown=fixtures.LabSettlementTests.tearDown
    report=fixtures.LabSettlementTests.report

    def test_consensus_updates_own_result_profile_and_duplicate_does_not_pay(self):
        self.hub.match_point_rewards=RATES.copy()
        before={u:self.store.snapshot(u)[2] for u in (1001,1002)}
        p=self.report((100,0))
        self.a.handle(self.ca,Message(4110,p));out=self.b.handle(self.cb,Message(4110,p))
        other=self.a.take_pending(self.ca)
        for uid,messages,index,award in ((1001,other,0,100),(1002,out,1,10)):
            updated=self.store.snapshot(uid)[2]
            self.assertEqual(struct.unpack_from('<I',updated,245)[0],struct.unpack_from('<I',before[uid],245)[0]+award)
            self.assertEqual(messages[0].payload[index*500+140:(index+1)*500],updated)
            self.assertEqual(struct.unpack_from('<I',messages[0].payload,index*500+34)[0],award)
            self.assertEqual(messages[0].payload[index*500+63:index*500+67],bytes(4))
            self.assertEqual(messages[0].payload[(1-index)*500+140:(2-index)*500],bytes(360))
        self.assertEqual(self.b.handle(self.cb,Message(4110,p)),[])
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM match_point_grants').fetchone()[0],2)

    def test_invalid_recipient_profile_prevents_any_durable_award(self):
        self.hub.match_point_rewards=RATES.copy()
        original=self.store.snapshot(1001)[2]
        bad=bytearray(self.store.snapshot(1002)[2]);bad[:4]=bytes(4)
        with self.store.db:self.store.db.execute('UPDATE accounts SET profile=? WHERE uid=1002',(bytes(bad),))
        p=self.report((100,0));self.a.handle(self.ca,Message(4110,p))
        from server.kk_local.wire import ProtocolError
        with self.assertRaises(ProtocolError):self.b.handle(self.cb,Message(4110,p))
        self.assertEqual(self.store.snapshot(1001)[2],original)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM match_point_batches').fetchone()[0],0)


if __name__=='__main__':unittest.main()
