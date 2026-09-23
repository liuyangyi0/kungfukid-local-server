import copy
import sqlite3
import struct
import tempfile
from pathlib import Path
from unittest.mock import patch
import unittest
from server.kk_local import weapon_upgrade as upgrade
from server.kk_local.store import Store
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.menu_layouts import decode_menu_request,decode_menu_response
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_shared_rooms as room_fixture


def rules(odds=100):
    return dict(enabled=True,failure='consume_score_keep_level',levels=[
        dict(level=0,score=10,gold=20,odds=odds,bonus_percent=0),
        dict(level=1,score=20,gold=30,odds=odds,bonus_percent=5),
        dict(level=2,score=30,gold=0,odds=0,bonus_percent=10)])


def seed_weapon(s,uid=1001,score=80):
    instance,raw=next((i,r) for i,r in s.db.execute('SELECT instance,record FROM inventory WHERE uid=?',(uid,)) if r[4]==25)
    item=bytearray(raw);struct.pack_into('<II',item,43,0,score);item[66]=91
    with s.db:s.db.execute('UPDATE inventory SET record=? WHERE uid=? AND instance=?',(bytes(item),uid,instance))
    s.set_gold_balance(uid,200);return instance,bytes(item)


class WeaponUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:');self.s.seed_local();self.s.provision_local(1002,'Second')
        self.instance,self.before=seed_weapon(self.s)
        upgrade.configure(self.s,rules(),0)
        self.e=Engine(self.s,wall_clock=lambda:1000);self.c=Connection(2,Phase.LOBBY,1001);self.e.game=self.c
    def tearDown(self):self.s.close()
    def query(self):return self.e.handle(self.c,Message(21410))
    def send(self):return self.e.handle(self.c,Message(21412,struct.pack('<I',self.instance)))
    def attempt(self,op='one',rev=1):return upgrade.attempt(self.s,1001,op,self.instance,rev,1000)

    def test_table_and_result_layouts(self):
        m=self.query()[0];self.assertEqual((m.id,len(m.payload)),(21411,63))
        rows=decode_menu_response(m.id,m.payload)['records'];self.assertEqual(rows[1]['bonus_percent'],5);self.assertEqual(rows[2]['unknown_16'],0)
        self.assertEqual(struct.unpack_from('<I',m.payload,38)[0],5)  #integer percent, not float bits
        out=self.send();self.assertEqual([m.id for m in out],[1240,2161,21413])
        self.assertEqual(len(out[2].payload),22);d=decode_menu_response(21413,out[2].payload)
        self.assertTrue(d['success']);self.assertEqual(d['instance'],self.instance)
        self.assertEqual(struct.unpack_from('<II',out[1].payload,43),(1,70))
        self.assertEqual(struct.unpack('<I',out[0].payload)[0],180)

    def test_only_level_and_score_change_preserve_worn_and_permanent(self):
        self.s.set_weapons_permanent(1001);before=next(r[0] for r in self.s.db.execute('SELECT record FROM inventory WHERE uid=1001 AND instance=?',(self.instance,)))
        result=self.attempt();expected=bytearray(before);struct.pack_into('<II',expected,43,1,70)
        self.assertEqual(result[2],bytes(expected));self.assertEqual(result[2][66],91)
        self.assertTrue(self.s.db.execute('SELECT 1 FROM permanent_weapons WHERE uid=1001 AND instance=?',(self.instance,)).fetchone())

    def test_failure_spends_configured_score_and_gold_but_keeps_level(self):
        upgrade.configure(self.s,rules(0),1)
        with patch.object(upgrade.secrets,'randbelow',return_value=77):result=self.attempt(rev=2)
        self.assertFalse(result[0]);self.assertEqual(struct.unpack_from('<II',result[2],43),(0,70));self.assertEqual(result[1],180)
        row=self.s.db.execute('SELECT success,roll,score_cost,gold_cost FROM weapon_upgrade_receipts').fetchone()
        self.assertEqual(row,(0,77,10,20))

    def test_receipt_replay_never_charges_or_rolls_again_returns_current_item(self):
        first=self.attempt();second=self.attempt('two')
        with patch.object(upgrade.secrets,'randbelow',side_effect=AssertionError('must not roll')):
            replay=self.attempt()
        self.assertEqual(replay[:3],(True,150,second[2]));self.assertFalse(replay[3]);self.assertEqual(len(self.s.snapshot(1001)[3]),7*68)
        self.assertNotEqual(first[2],second[2])

    def test_revision_compare_swap_and_old_display_refused(self):
        self.query();upgrade.configure(self.s,rules(),1);before=self.s.snapshot(1001)
        self.assertNotIn(21413,[m.id for m in self.send()]);self.assertEqual(self.s.snapshot(1001),before)
        with self.assertRaises(ValueError):upgrade.configure(self.s,rules(),1)
        self.query();self.assertIn(21413,[m.id for m in self.send()])

    def test_query_connection_and_phase_requirements(self):
        self.assertNotIn(21413,[m.id for m in self.send()]);self.query()
        self.assertEqual(self.e.handle(Connection(4,Phase.LOBBY,1001),Message(21412,struct.pack('<I',self.instance))),[])
        self.c.phase=Phase.BATTLE;self.assertEqual(self.send(),[]);self.c.phase=Phase.LOBBY
        self.e.disconnect(self.c);self.assertIsNone(self.e.weapon_upgrade_offer)

    def test_foreign_item_invalid_shape_status_and_insufficient_score(self):
        other,_=seed_weapon(self.s,1002)
        with self.assertRaises(ValueError):upgrade.attempt(self.s,1001,'other',other,1,1000)
        for p in (b'',bytes(3),bytes(5)):
            with self.assertRaises(ProtocolError):self.e.handle(self.c,Message(21412,p))
        for offset,value in ((47,0),(19,2),(19,0xffffffff),(43,2)):
            item=bytearray(self.before);struct.pack_into('<I',item,offset,value)
            with self.s.db:self.s.db.execute('UPDATE inventory SET record=? WHERE uid=1001 AND instance=?',(bytes(item),self.instance))
            with self.assertRaises(ValueError):self.attempt(str(offset)+str(value))
        self.assertEqual(self.s.gold_balance(1001),200)

    def test_expired_lease_rejected_but_explicit_permanent_right_wins(self):
        with self.s.db:self.s.db.execute('INSERT INTO renewal_leases VALUES(?,?,?)',(1001,self.instance,999))
        with self.assertRaises(ValueError):self.attempt()
        self.s.set_weapons_permanent(1001);self.assertTrue(self.attempt()[0])

    def test_rollback_receipt_failure_preserves_all_state(self):
        self.s.db.execute("CREATE TRIGGER fail_upgrade BEFORE INSERT ON weapon_upgrade_receipts BEGIN SELECT RAISE(ABORT,'fixture'); END")
        before=self.s.snapshot(1001)
        with self.assertRaises(sqlite3.IntegrityError):self.attempt()
        self.assertEqual(self.s.snapshot(1001),before);self.assertEqual(self.s.gold_balance(1001),200)
        self.assertFalse(self.s.db.in_transaction)

    def test_insufficient_gold_does_not_consume_score(self):
        self.s.set_gold_balance(1001,19);before=self.s.snapshot(1001)
        with self.assertRaises(ValueError):self.attempt()
        self.assertEqual(self.s.snapshot(1001),before);self.assertEqual(self.s.gold_balance(1001),19)

    def test_disabled_defaults_and_bad_rule_bounds(self):
        for field,value in (('score',0),('level',4),('gold',-1),('odds',101),('bonus_percent',101)):
            r=rules();r['levels'][0][field]=value
            with self.assertRaises(ValueError):upgrade.configure(self.s,r,1)
        disabled=rules();disabled['enabled']=False;upgrade.configure(self.s,disabled,1)
        self.assertIn(21411,[m.id for m in self.query()]);self.assertNotIn(21413,[m.id for m in self.send()])
        for m,p in ((21411,b''),(21411,bytes(20)),(21413,b''),(21413,bytes(21))):
            with self.assertRaises(ProtocolError):decode_menu_response(m,p)

    def test_disable_keeps_existing_bonus_table_and_cannot_orphan_levels(self):
        self.attempt()
        empty=dict(enabled=False,failure='consume_score_keep_level',levels=[])
        with self.assertRaises(ValueError):upgrade.configure(self.s,empty,1)
        short=rules();short['enabled']=False;short['levels']=short['levels'][:1]
        with self.assertRaises(ValueError):upgrade.configure(self.s,short,1)
        disabled=rules();disabled['enabled']=False;upgrade.configure(self.s,disabled,1)
        out=self.query();self.assertEqual(out[0].id,21411)
        self.assertEqual(decode_menu_response(21411,out[0].payload)['records'][1]['bonus_percent'],5)
        self.assertEqual(self.s.gold_balance(1001),180)

    def test_receipt_survives_restart_and_deleted_weapon_not_recreated(self):
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/'db.sqlite3');s=Store(path);s.seed_local();instance,_=seed_weapon(s)
            upgrade.configure(s,rules(),0);original=upgrade.attempt(s,1001,'saved',instance,1,1000);s.close()
            s=Store(path)
            try:
                result=upgrade.attempt(s,1001,'saved',instance,1,1000);self.assertEqual(result[:3],original[:3]);self.assertFalse(result[3])
                with s.db:s.db.execute('DELETE FROM inventory WHERE uid=1001 AND instance=?',(instance,))
                with self.assertRaises(ValueError):upgrade.attempt(s,1001,'saved',instance,1,1000)
                self.assertEqual(s.gold_balance(1001),180)
            finally:s.close()


class WeaponUpgradeRoomTests(unittest.TestCase):
    setUp=room_fixture.SharedRoomTests.setUp
    tearDown=room_fixture.SharedRoomTests.tearDown
    join=room_fixture.SharedRoomTests.join

    def test_waiting_room_updates_remote_equipment_and_ready_blocks(self):
        self.join()
        self.a,self.ca,self.b,self.cb=self.e1,self.c1,self.e2,self.c2
        instance,_=seed_weapon(self.s);upgrade.configure(self.s,rules(),0)
        self.a.handle(self.ca,Message(21410));self.a.take_pending(self.ca);self.b.take_pending(self.cb)
        out=self.a.handle(self.ca,Message(21412,struct.pack('<I',instance)))
        self.assertEqual([m.id for m in out],[1240,2161,21413]);self.assertIn(3090,[m.id for m in self.b.take_pending(self.cb)])
        self.a.room.members[1001].ready=True;before=self.s.snapshot(1001)
        self.assertNotIn(21413,[m.id for m in self.a.handle(self.ca,Message(21412,struct.pack('<I',instance)))])
        self.assertEqual(self.s.snapshot(1001),before)
