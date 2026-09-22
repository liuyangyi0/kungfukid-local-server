import sqlite3
import struct
import tempfile
from pathlib import Path
import unittest
from server.kk_local import quests,quest_rewards,ordinary_quests as ordinary
from server.kk_local.maps import MapCatalog
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.store import Store
from server.kk_local.wire import Message
from server.tests import test_quests as base,test_lab_settlement as battle_base
from server.tests.test_title_rewards import choice


def template(key,matches=2,title=3,next_key=0):
    row=quests.parse_templates('ordinary',base.source('ordinary',key))[key]
    fields=list(row.fields);fields[5]=str(matches);fields[38]=str(title);fields[39]=str(next_key)
    return quests.Template('ordinary',key,tuple(fields))


def contract(key,gold,item=False):
    return dict(family='ordinary',key=key,events=[],gold=gold,**(choice() if item else dict(catalog_hex='',grant_hex='')))


class OrdinaryQuestTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:');self.s.seed_local();self.s.provision_local(1002,'Second')
        self.maps=MapCatalog({},{});self.maps.title_levels=frozenset(range(17))
        self.table={('ordinary',1001):template(1001,next_key=1002),('ordinary',1002):template(1002,1,4)}
        self.maps.quest_templates=self.table
        quests.configure(self.s,[dict(family=f,key=k) for f,k in self.table],self.table)
        quest_rewards.configure(self.s,[contract(1001,20,True),contract(1002,5)],self.table)
        self.e=Engine(self.s,map_catalog=self.maps);self.c=Connection(2,Phase.LOBBY,1001);self.e.game=self.c

    def tearDown(self):self.s.close()
    def query(self):return self.e.handle(self.c,Message(6000,bytes(4)))
    def act(self,ident=6050,key=1001):return self.e.handle(self.c,base.action(ident,key))
    def accept(self):self.query();self.act()
    def settle(self,b,mode=0,outcome=1,players=True):
        return self.s.award_match_points(b,{1001:outcome,**({1002:2} if players else {})},{0:0,1:0,2:0},
                                        mode=mode,quest_templates=self.table)
    def finish(self):self.settle(1);self.settle(2,1,0)
    def state(self,key=1001):return self.s.db.execute("SELECT state FROM quest_progress WHERE uid=1001 AND family='ordinary' AND task_key=?",(key,)).fetchone()[0]

    def test_atomic_complete_title_and_successor_not_double_inserted(self):
        out=self.query();self.assertEqual(len(out[0].payload),123)
        self.assertNotIn(6060,[m.id for m in self.act(key=1002)])
        self.act();self.finish();out=self.query();ids=[m.id for m in out]
        self.assertEqual(ids,[6020,6041,6042,1550,2160,1240,6030,6040])
        self.assertEqual(len(out[0].payload),123);self.assertEqual(out[0].payload[6],3)
        self.assertEqual(struct.unpack_from('<H',out[-1].payload,4)[0],1002)
        self.assertEqual(self.s.gold_balance(1001),20);self.assertEqual(self.s.snapshot(1001)[2][123],3)
        out=self.query();self.assertEqual(len(out[0].payload),246);self.assertNotIn(6030,[m.id for m in out]);self.assertNotIn(6040,[m.id for m in out])
        self.assertEqual(self.s.gold_balance(1001),20)
        self.assertEqual(self.act(key=1002)[0].id,6060);self.settle(3,2);self.query()
        self.assertEqual(self.s.gold_balance(1001),25);self.assertEqual(self.state(1002),3);self.assertEqual(self.s.snapshot(1001)[2][123],4)

    def test_stat_offsets_mode_wins_draws_and_duplicate(self):
        self.accept();p0=self.s.snapshot(1001)[2]
        self.settle(1,0);self.settle(1,0);self.settle(2,1,0);self.settle(3,2,2);self.settle(4,3)
        p=self.s.snapshot(1001)[2]
        self.assertEqual(struct.unpack_from('<8I',p,133),(1,1,1,0,1,0,1,1))
        self.assertEqual(p[:133],p0[:133]);self.assertEqual(p[165:],p0[165:])

    def test_solo_other_modes_and_unqualified_source_do_not_count(self):
        self.accept();self.settle(1,0,players=False);self.settle(2,16)
        self.s.award_match_points(3,{1001:1,1002:2},{0:0,1:0,2:0},mode=0,quest_templates={})
        self.assertEqual(self.s.snapshot(1001)[2][133:165],bytes(32));self.assertEqual(self.state(),2)

    def test_counter_saturation_does_not_wrap_or_grant_old_statistics(self):
        p=bytearray(self.s.snapshot(1001)[2]);struct.pack_into('<II',p,133,0x7fffffff,0xffffffff)
        with self.s.db:self.s.db.execute('UPDATE accounts SET profile=? WHERE uid=1001',(bytes(p),))
        self.accept();self.settle(1);self.settle(2);self.query()
        self.assertEqual(struct.unpack_from('<II',self.s.snapshot(1001)[2],133),(0x7fffffff,0xffffffff));self.assertEqual(self.state(),2)

    def test_unknown_kill_combo_and_badge_conditions_rejected_not_guessed(self):
        for index in (6,7,16):
            f=list(self.table[('ordinary',1001)].fields);f[index]='1';t=quests.Template('ordinary',1001,tuple(f))
            with self.assertRaises(ValueError):ordinary.requirements(t)

    def test_cancel_reaccept_samples_new_baseline_and_freezes_new_reward(self):
        self.accept();self.settle(1);self.act(6080);quest_rewards.configure(self.s,[contract(1001,100),contract(1002,5)],self.table)
        self.act();self.settle(2);self.query();self.assertEqual(self.state(),2)
        self.settle(3);self.query();self.assertEqual(self.s.gold_balance(1001),100)

    def test_mid_task_reward_edit_does_not_rewrite_promise(self):
        self.accept();quest_rewards.configure(self.s,[contract(1001,999),contract(1002,5)],self.table)
        self.finish();self.query();self.assertEqual(self.s.gold_balance(1001),20)

    def test_sql_claim_failure_rolls_back_title_wallet_and_item(self):
        self.accept();self.finish();before=self.s.snapshot(1001)
        self.s.db.execute("CREATE TRIGGER reject_ordinary BEFORE UPDATE ON quest_progress WHEN NEW.state=3 BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.query()
        self.assertEqual(self.state(),2);self.assertEqual(self.s.snapshot(1001),before);self.assertEqual(self.s.gold_balance(1001),0)
        self.s.db.execute('DROP TRIGGER reject_ordinary');self.query();self.assertEqual(self.state(),3)

    def test_pending_title_choice_and_missing_title_table_pause_completion(self):
        self.accept();self.finish();self.maps.title_levels=frozenset();self.query();self.assertEqual(self.state(),2)
        self.maps.title_levels=frozenset(range(17))
        from server.kk_local import title_rewards
        title_rewards.configure(self.s,2,[choice()],self.maps.title_levels);title_rewards.issue(self.s,1001,2,self.maps.title_levels)
        self.query();self.assertEqual(self.state(),2);self.assertEqual(self.s.gold_balance(1001),0)

    def test_current_item_retry_no_resurrection_and_cannot_reset_complete(self):
        self.accept();self.finish();out=self.query();raw=next(m.payload for m in out if m.id==2160)
        instance=struct.unpack_from('<I',raw)[0]
        with self.s.db:self.s.db.execute('DELETE FROM inventory WHERE uid=1001 AND instance=?',(instance,))
        self.assertNotIn(2160,[m.id for m in self.query()]);self.assertEqual(self.s.gold_balance(1001),20)
        self.assertNotIn(6090,[m.id for m in self.act(6080)]);self.assertNotIn(6060,[m.id for m in self.act()])

    def test_successor_cycle_or_missing_source_rejected(self):
        bad=dict(self.table);bad[('ordinary',1002)]=template(1002,next_key=1001)
        with self.assertRaises(ValueError):quests.configure(self.s,[],bad)
        with self.assertRaises(ValueError):quests.configure(self.s,[],{('ordinary',1001):template(1001,next_key=7777)})

    def test_restart_and_repeated_query_does_not_reaward_gold_only(self):
        with tempfile.TemporaryDirectory() as d:
            p=str(Path(d)/'db.sqlite3');s=Store(p);s.seed_local();s.provision_local(1002,'Second')
            try:
                quests.configure(s,[dict(family='ordinary',key=1001)],self.table)
                quest_rewards.configure(s,[contract(1001,10)],self.table);quests.transition(s,1001,self.table[('ordinary',1001)],2)
                for b in (1,2):s.award_match_points(b,{1001:1,1002:2},{0:0,1:0,2:0},mode=0,quest_templates=self.table)
                fresh,_=ordinary.complete(s,1001,self.table,self.maps.title_levels);self.assertEqual(fresh,[1001])
            finally:s.close()
            s=Store(p)
            try:
                fresh,m=ordinary.complete(s,1001,self.table,self.maps.title_levels)
                self.assertEqual(fresh,[]);self.assertEqual(m,[Message(1240,struct.pack('<I',10))]);self.assertEqual(s.snapshot(1001)[2][123],3)
            finally:s.close()


class OrdinaryBattleIntegrationTests(unittest.TestCase):
    setUp=battle_base.LabSettlementTests.setUp
    tearDown=battle_base.LabSettlementTests.tearDown
    report=battle_base.LabSettlementTests.report

    def test_original_result_includes_server_counter_update_only_after_consensus(self):
        t={('ordinary',1001):template(1001,1,0)};maps=MapCatalog({},{});maps.quest_templates=t
        self.a.map_catalog=maps;self.b.map_catalog=maps
        quests.configure(self.store,[dict(family='ordinary',key=1001)],t);quest_rewards.configure(self.store,[contract(1001,0)],t)
        p=self.report((100,0));self.a.handle(self.ca,Message(4110,p))
        self.assertEqual(self.store.snapshot(1001)[2][141:149],bytes(8))
        self.b.handle(self.cb,Message(4110,p));out=self.a.take_pending(self.ca)
        result=next(m for m in out if m.id==4120)
        self.assertEqual(struct.unpack_from('<II',result.payload,140+141),(1,1))
        self.assertEqual(struct.unpack_from('<II',self.store.snapshot(1002)[2],141),(1,0))
