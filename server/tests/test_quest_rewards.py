import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
from server.kk_local import quests,quest_rewards as rewards
from server.kk_local.store import Store
from server.kk_local.maps import MapCatalog
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_quests as fixtures,test_lab_settlement as battles
from server.tests.test_title_rewards import choice


def rule(family='newbie',key=3001,event='mode_1_win',gold=50,item=True):
    row=dict(family=family,key=key,events=[event,'',''],gold=gold,catalog_hex='',grant_hex='')
    if item:row.update(choice())
    return row


def catalog():
    #Synthetic source requires two victories, not an original task claim.
    result=fixtures.templates()
    for family,key in (('newbie',3001),('daily',2001)):
        t=result[(family,key)];fields=list(t.fields);fields[4]='2'
        result[(family,key)]=quests.Template(family,key,tuple(fields))
    return result


class QuestRewardTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:');self.s.seed_local();self.s.provision_local(1002,'Second')
        self.maps=MapCatalog({},{});self.maps.quest_templates=catalog();self.templates=self.maps.quest_templates
        quests.configure(self.s,[dict(family=f,key=k) for f,k in self.templates],self.templates)
        rewards.configure(self.s,[rule()],self.templates)
        self.e=Engine(self.s,map_catalog=self.maps);self.c=Connection(2,Phase.LOBBY,1001);self.e.game=self.c
        self.query();self.send(6052)

    def tearDown(self):self.s.close()
    def query(self):return self.e.handle(self.c,Message(6000,bytes(4)))
    def send(self,ident=6312,key=3001):return self.e.handle(self.c,fixtures.action(ident,key))
    def settle(self,battle,mode=1,outcome=1):
        return self.s.award_match_points(battle,{1001:outcome,1002:2},{0:0,1:0,2:0},mode=mode,quest_templates=self.templates)
    def state(self):return self.s.db.execute('SELECT state,counts,claimed_instance FROM quest_progress WHERE uid=1001 AND family="newbie" AND task_key=3001').fetchone()
    def finish(self):self.settle(1);self.settle(2)

    def test_receipts_advance_once_mode_and_result_gated(self):
        self.settle(1,0);self.settle(2,1,0);self.assertEqual(self.state()[:2],(2,bytes(12)))
        self.settle(3);self.settle(3);self.assertEqual(struct.unpack('<3I',self.state()[1]),(1,0,0))
        with self.assertRaises(ValueError):self.settle(3,0)
        self.settle(4);self.assertEqual(self.state()[0],4)
        self.settle(5);self.assertEqual(struct.unpack('<3I',self.state()[1]),(2,0,0))

    def test_claim_before_complete_denied_then_atomic_idempotent(self):
        before=self.s.snapshot(1001);self.assertNotIn(6302,[m.id for m in self.send()])
        self.assertEqual(self.s.snapshot(1001),before)
        self.finish();out=self.query()
        self.assertEqual([m.id for m in out],[6020,6041,6042,6032])
        self.assertEqual(out[2].payload[16],4);self.assertEqual(struct.unpack_from('<I',out[2].payload,57)[0],2)
        self.assertNotIn(6032,[m.id for m in self.query()])
        out=self.send();self.assertEqual([m.id for m in out],[1550,2160,1240,6302]);item=out[1].payload
        self.assertEqual(out[-1].payload,struct.pack('<HB',3001,3));self.assertEqual(self.s.gold_balance(1001),50)
        self.assertEqual(self.send()[1].payload,item);self.assertEqual(self.s.gold_balance(1001),50)
        self.assertEqual(len(self.s.snapshot(1001)[3]),len(before[3])+68)
        self.assertEqual(self.query()[2].payload,b'')
        self.assertNotIn(6092,[m.id for m in self.send(6082)])
        self.assertNotIn(6062,[m.id for m in self.send(6052)])

    def test_reward_retry_uses_current_record_and_never_resurrects(self):
        self.finish();raw=self.send()[1].payload;instance=struct.unpack_from('<I',raw)[0]
        updated=bytearray(raw);updated[-1]=83
        with self.s.db:self.s.db.execute('UPDATE inventory SET record=? WHERE uid=1001 AND instance=?',(bytes(updated),instance))
        self.assertEqual(self.send()[1].payload,bytes(updated))
        with self.s.db:self.s.db.execute('DELETE FROM inventory WHERE uid=1001 AND instance=?',(instance,))
        self.assertEqual([m.id for m in self.send()],[1240,6302]);self.assertEqual(self.s.gold_balance(1001),50)

    def test_frozen_rule_survives_configuration_update(self):
        rewards.configure(self.s,[rule(gold=999,item=False)],self.templates)
        self.finish();self.send();self.assertEqual(self.s.gold_balance(1001),50);self.assertIsNotNone(self.state()[2])

    def test_cancel_reaccept_resets_progress_and_takes_new_rule(self):
        self.settle(1);self.send(6082);self.assertEqual(self.state()[:2],(1,bytes(12)))
        rewards.configure(self.s,[rule(gold=80,item=False)],self.templates);self.send(6052)
        self.settle(2);self.assertEqual(self.state()[0],2);self.settle(3);self.send()
        self.assertEqual(self.s.gold_balance(1001),80);self.assertIsNone(self.state()[2])

    def test_rule_disable_and_source_drift_pause_without_fake_completion(self):
        rewards.configure(self.s,[],self.templates);self.settle(1);self.assertEqual(self.state()[:2],(2,bytes(12)))
        rewards.configure(self.s,[rule()],self.templates)
        changed=dict(self.templates);t=changed[('newbie',3001)];f=list(t.fields);f[22]='changed'
        changed[('newbie',3001)]=quests.Template('newbie',3001,tuple(f))
        self.s.award_match_points(2,{1001:1},{0:0,1:0,2:0},mode=1,quest_templates=changed)
        self.assertEqual(self.state()[:2],(2,bytes(12)))

    def test_wallet_and_catalogue_failure_roll_back_everything(self):
        self.finish();before=self.s.snapshot(1001)
        with self.s.db:self.s.db.execute('INSERT INTO gold_wallet VALUES(1001,2147483647)')
        self.assertNotIn(6302,[m.id for m in self.send()]);self.assertEqual(self.state()[0],4);self.assertEqual(self.s.snapshot(1001),before)
        with self.s.db:self.s.db.execute('UPDATE gold_wallet SET balance=0 WHERE uid=1001')
        conflict=bytearray.fromhex(choice()['catalog_hex']);conflict[-1]=99
        self.s.replace_shop_catalog(25,1,[bytes(conflict)])
        self.assertNotIn(6302,[m.id for m in self.send()]);self.assertEqual(self.s.snapshot(1001),before)

    def test_sql_error_in_claim_rolls_back_item_gold_and_receipt(self):
        self.finish();before=self.s.snapshot(1001)
        self.s.db.execute("CREATE TRIGGER fail_claim BEFORE UPDATE ON quest_progress WHEN NEW.state=3 BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.send()
        self.assertEqual(self.state()[0],4);self.assertEqual(self.s.gold_balance(1001),0);self.assertEqual(self.s.snapshot(1001),before)

    def test_progress_failure_rolls_back_settlement_then_retry_counts_once(self):
        self.s.db.execute("CREATE TRIGGER fail_progress BEFORE UPDATE ON quest_progress BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.settle(1)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM match_point_batches').fetchone()[0],0)
        self.s.db.execute('DROP TRIGGER fail_progress');self.settle(1);self.settle(1)
        self.assertEqual(struct.unpack('<3I',self.state()[1]),(1,0,0))

    def test_old_or_foreign_connection_cannot_claim(self):
        self.finish();self.e.game=Connection(20,Phase.LOBBY,1001)
        self.assertEqual(self.send(),[])
        e=Engine(self.s,account_uid=1002,map_catalog=self.maps);c=Connection(25,Phase.LOBBY,1002);e.game=c
        e.handle(c,Message(6000,bytes(4)));self.assertNotIn(6302,[m.id for m in e.handle(c,fixtures.action(6312,3001))])
        self.assertEqual(self.s.gold_balance(1001),0);self.assertEqual(self.s.gold_balance(1002),0)

    def test_daily_claim_persists_without_automatic_reset(self):
        rewards.configure(self.s,[rule('daily',2001,'battle_play',0,False)],self.templates)
        self.send(6051,2001);self.settle(1,0,2);self.settle(2,3,0)
        self.assertEqual(self.send(6311,2001)[-1],Message(6301,struct.pack('<HB',2001,3)))
        self.assertEqual(self.query()[1].payload[16],3)
        self.assertNotIn(6061,[m.id for m in self.send(6051,2001)])

    def test_unsupported_condition_and_ordinary_reward_are_rejected(self):
        for r in (rule(event='training_0'),rule('ordinary',1001),rule(gold=True)):
            with self.assertRaises(ValueError):rewards.configure(self.s,[r],self.templates)
        r=rule();r['events']=['battle_win','battle_play','']
        with self.assertRaises(ValueError):rewards.configure(self.s,[r],self.templates)

    def test_restart_preserves_completed_claim_and_does_not_repay(self):
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/'db.sqlite3');s=Store(path);s.seed_local()
            try:
                quests.configure(s,[dict(family='newbie',key=3001)],self.templates)
                rewards.configure(s,[rule()],self.templates);t=self.templates[('newbie',3001)]
                quests.transition(s,1001,t,2)
                for b in (1,2):s.award_match_points(b,{1001:1},{0:0,1:0,2:0},mode=1,quest_templates=self.templates)
                original=rewards.claim(s,1001,t)
            finally:s.close()
            s=Store(path)
            try:self.assertEqual(rewards.claim(s,1001,t),original);self.assertEqual(s.gold_balance(1001),50)
            finally:s.close()

    def test_v1_database_migrates_baseline_without_reward_eligibility(self):
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/'db.sqlite3');s=Store(path);s.seed_local();s.close()
            db=sqlite3.connect(path)
            db.executescript('DROP TABLE quest_progress; CREATE TABLE quest_progress(uid INTEGER,family TEXT,task_key INTEGER,definition TEXT,state INTEGER CHECK(state IN (1,2)),baseline BLOB,PRIMARY KEY(uid,family,task_key));')
            db.execute('INSERT INTO quest_progress VALUES(?,?,?,?,?,?)',(1001,'newbie',3001,self.templates[('newbie',3001)].identity,2,b'B'*116));db.commit();db.close()
            s=Store(path)
            try:
                row=s.db.execute('SELECT state,baseline,execution,counts,claimed_instance FROM quest_progress').fetchone()
                self.assertEqual(row,(2,b'B'*116,None,bytes(12),None))
                self.assertFalse(s.db.execute("SELECT 1 FROM sqlite_master WHERE name='quest_progress_v1_migration'").fetchone())
            finally:s.close()

    def test_preaccept_settlement_cannot_be_replayed_into_new_acceptance(self):
        self.send(6082);self.settle(1);self.send(6052);self.settle(1)
        self.assertEqual(self.state()[:2],(2,bytes(12)))
        self.settle(2);self.assertEqual(struct.unpack('<3I',self.state()[1]),(1,0,0))


class QuestConsensusIntegrationTests(unittest.TestCase):
    setUp=battles.LabSettlementTests.setUp
    tearDown=battles.LabSettlementTests.tearDown
    report=battles.LabSettlementTests.report

    def test_only_all_player_consensus_advances_and_zero_points_policy_is_preserved(self):
        table=catalog();maps=MapCatalog({},{});maps.quest_templates=table
        self.a.map_catalog=maps;self.b.map_catalog=maps
        quests.configure(self.store,[dict(family='newbie',key=3001)],table)
        rewards.configure(self.store,[rule()],table)
        for uid in (1001,1002):quests.transition(self.store,uid,table[('newbie',3001)],2)
        before={u:self.store.snapshot(u) for u in (1001,1002)}
        p=self.report((100,0));self.a.handle(self.ca,Message(4110,p))
        self.assertEqual(self.store.db.execute('SELECT counts FROM quest_progress WHERE uid=1001').fetchone()[0],bytes(12))
        self.b.handle(self.cb,Message(4110,p));self.b.handle(self.cb,Message(4110,p))
        self.assertEqual(self.store.db.execute('SELECT counts FROM quest_progress WHERE uid=1001').fetchone()[0],struct.pack('<3I',1,0,0))
        self.assertEqual(self.store.db.execute('SELECT counts FROM quest_progress WHERE uid=1002').fetchone()[0],bytes(12))
        self.assertEqual(before,{u:self.store.snapshot(u) for u in (1001,1002)})
