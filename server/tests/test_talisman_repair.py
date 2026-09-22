import struct
import sqlite3
import unittest
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.store import Store
from server.kk_local.wire import Message
from server.kk_local.talisman_repair import import_rules,repair


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:');self.s.seed_local()
        self.e=Engine(self.s);self.c=Connection(2,Phase.LOBBY,1001);self.e.game=self.c
        self.now=[100.0];self.e.clock=lambda:self.now[0]
        self.document=dict(schema='kk-talisman-repair-rules-v1',rules=[dict(item=303001,material=601001,quantity=3,capacity=1000)])
        import_rules(self.s,self.document)
        self.add(2000000,30,303001,100,37)
        self.add(2000001,60,601001,2)
        self.add(2000002,60,601001,4)

    def tearDown(self):self.s.close()

    def add(self,instance,kind,item,quantity,slot=0):
        p=bytearray(68);struct.pack_into('<IBI',p,0,instance,kind,item)
        struct.pack_into('<H',p,17,slot);struct.pack_into('<H',p,23,quantity);p[-1]=77
        with self.s.db:self.s.db.execute('INSERT INTO inventory VALUES(1001,?,?)',(instance,bytes(p)))

    def quote(self):return self.e.handle(self.c,Message(4202,struct.pack('<I',2000000)))
    def confirm(self,p=None):return self.e.handle(self.c,Message(4204,p or struct.pack('<III',2000000,601001,0)))
    def record(self,i):
        row=self.s.db.execute('SELECT record FROM inventory WHERE uid=1001 AND instance=?',(i,)).fetchone()
        return row[0] if row else None

    def test_quote_layout_and_atomic_cross_stack_repair(self):
        before=self.record(2000000);inventory=self.s.snapshot(1001)
        msg=self.quote()[0]
        self.assertEqual(msg.id,4203)
        self.assertEqual(struct.unpack('<8I',msg.payload),(2000000,303001,601001,0,3,100,1000,0))
        self.assertEqual(inventory,self.s.snapshot(1001))
        out=self.confirm();self.assertEqual([m.id for m in out],[1120,4205])
        self.assertEqual(out[-1].payload,struct.pack('<II',2000000,0))
        self.assertIsNone(self.record(2000001))
        self.assertEqual(struct.unpack_from('<H',self.record(2000002),23)[0],3)
        expected=bytearray(before);struct.pack_into('<H',expected,23,1000)
        self.assertEqual(self.record(2000000),bytes(expected))

    def test_retry_after_use_does_not_refill_or_charge_again(self):
        self.quote();self.confirm()
        raw=bytearray(self.record(2000000));struct.pack_into('<H',raw,23,900)
        with self.s.db:self.s.db.execute('UPDATE inventory SET record=? WHERE uid=1001 AND instance=2000000',(bytes(raw),))
        before=self.s.snapshot(1001)
        self.assertEqual([m.id for m in self.confirm()],[1120,4205])
        self.assertEqual(before,self.s.snapshot(1001))
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM talisman_repairs').fetchone()[0],1)

    def test_missing_expired_and_replaced_connection_quotes_are_rejected(self):
        before=self.s.snapshot(1001)
        self.assertNotIn(4205,[m.id for m in self.confirm()])
        self.quote();self.now[0]+=300
        self.assertNotIn(4205,[m.id for m in self.confirm()])
        self.quote();self.e.game=Connection(3,Phase.LOBBY,1001)
        self.assertNotIn(4205,[m.id for m in self.e.handle(self.e.game,Message(4204,struct.pack('<III',2000000,601001,0)))])
        self.assertEqual(before,self.s.snapshot(1001))

    def test_tampering_and_rule_aba_reject(self):
        self.quote();before=self.s.snapshot(1001)
        for values in ((2000001,601001,0),(2000000,601002,0),(2000000,601001,1)):
            self.assertNotIn(4205,[m.id for m in self.confirm(struct.pack('<III',*values))])
        import_rules(self.s,self.document)  # same values, new policy revision
        self.assertNotIn(4205,[m.id for m in self.confirm()])
        self.assertEqual(before,self.s.snapshot(1001))

    def test_insufficient_or_equipped_material_does_not_partially_spend(self):
        raw=bytearray(self.record(2000002));struct.pack_into('<H',raw,17,37)
        with self.s.db:self.s.db.execute('UPDATE inventory SET record=? WHERE instance=2000002',(bytes(raw),))
        self.quote();before=self.s.snapshot(1001)
        self.assertNotIn(4205,[m.id for m in self.confirm()]);self.assertEqual(before,self.s.snapshot(1001))

    def test_sql_error_rolls_back_inventory_and_receipt(self):
        self.quote();before=self.s.snapshot(1001)
        self.s.db.execute("CREATE TRIGGER deny_repair BEFORE INSERT ON talisman_repairs BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.confirm()
        self.assertEqual(before,self.s.snapshot(1001));self.assertFalse(self.s.db.in_transaction)

    def test_unknown_account_or_full_target_never_gets_success(self):
        self.s.provision_local(1002,'Second');self.c.uid=1002
        self.assertNotIn(4203,[m.id for m in self.quote()]);self.c.uid=1001
        self.quote();self.confirm();self.assertNotIn(4203,[m.id for m in self.quote()])

    def test_invalid_rules_do_not_replace_previous_policy(self):
        before=self.s.db.execute('SELECT * FROM talisman_repair_rules').fetchall()
        for bad in (0,-1,65536,True,1.5):
            doc=dict(schema=self.document['schema'],rules=[dict(self.document['rules'][0],quantity=bad)])
            with self.assertRaises(ValueError):import_rules(self.s,doc)
            self.assertEqual(before,self.s.db.execute('SELECT * FROM talisman_repair_rules').fetchall())

    def test_changed_inventory_invalidates_quote_and_battle_forbids_repair(self):
        self.quote();self.s.equip(1001,2000000,38);before=self.s.snapshot(1001)
        self.assertNotIn(4205,[m.id for m in self.confirm()])
        self.c.phase=Phase.BATTLE;self.assertEqual(self.quote(),[]);self.assertEqual(self.confirm(),[])
        self.assertEqual(before,self.s.snapshot(1001))
