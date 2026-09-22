import struct
import unittest
from server.kk_local.store import Store


class WeaponIdentityTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:');self.s.seed_local();self.s.provision_local(1002,'Second')

    def tearDown(self):self.s.close()

    def weapon(self,uid):
        return next((i,r) for i,r in self.s.db.execute('SELECT instance,record FROM inventory WHERE uid=?',(uid,)) if r[4]==25)

    def duplicate_legacy(self):
        old,_=self.weapon(1001);current,raw=self.weapon(1002)
        record=bytearray(raw);struct.pack_into('<I',record,0,old)
        with self.s.db:
            self.s.db.execute('DELETE FROM inventory WHERE uid=1002 AND instance=?',(current,))
            self.s.db.execute('INSERT INTO inventory VALUES(1002,?,?)',(old,bytes(record)))
        return old,bytes(record)

    def test_accounts_and_grants_use_global_non_sentinel_identity(self):
        a=self.weapon(1001)[0];b=self.weapon(1002)[0]
        self.assertEqual(a,0x100006);self.assertNotEqual(a,b)
        self.s.apply_grant(dict(schema='kk-local-inventory-grant-v1',uid=1001,grant_id='new',rows=[[253033,25,1]]))
        ids=[i for i, in self.s.db.execute('SELECT instance FROM inventory')]
        self.assertEqual(len(ids),len(set(ids)))
        self.assertFalse({0,0xffffffff}&set(ids))
        before=self.s.snapshot(1002);self.s.provision_local(1002,'Second')
        self.assertEqual(before,self.s.snapshot(1002))

    def test_purchase_does_not_reuse_another_accounts_instance(self):
        from server.tests.test_shop_purchase import setup_offer
        request,_=setup_offer(self.s)
        before={i for i, in self.s.db.execute('SELECT instance FROM inventory')}
        result=self.s.purchase_gold_once(1001,'globally-new',request)
        instance=struct.unpack_from('<I',result[1])[0]
        self.assertNotIn(instance,before)
        self.assertEqual(self.s.purchase_gold_once(1001,'globally-new',request),result)

    def test_offline_repair_preserves_all_other_bytes_and_entitlement(self):
        old,_=self.duplicate_legacy();self.s.set_weapons_permanent(1002)
        _,before=self.weapon(1002);first=self.s.snapshot(1001)
        changes=self.s.repair_duplicate_weapon_instances()
        new,after=self.weapon(1002)
        self.assertEqual(changes,[dict(uid=1002,old_instance=old,new_instance=new)])
        self.assertNotEqual(old,new);self.assertEqual(before[4:],after[4:])
        self.assertEqual(first,self.s.snapshot(1001))
        self.assertTrue(self.s.db.execute('SELECT 1 FROM permanent_weapons WHERE uid=1002 AND instance=?',(new,)).fetchone())
        self.assertEqual(self.s.repair_duplicate_weapon_instances(),[])
        self.assertEqual(self.s.db.execute('PRAGMA foreign_key_check').fetchall(),[])

    def test_history_reference_refuses_without_partial_changes(self):
        old,_=self.duplicate_legacy()
        with self.s.db:self.s.db.execute('INSERT INTO consumption_events VALUES(1002,1,1,?,?,1)',(old,b'history'))
        before=self.s.snapshot(1002)
        with self.assertRaisesRegex(ValueError,'history'):self.s.repair_duplicate_weapon_instances()
        self.assertEqual(before,self.s.snapshot(1002))

    def test_purchase_receipt_is_not_rewritten(self):
        _,record=self.duplicate_legacy()
        with self.s.db:self.s.db.execute('INSERT INTO shop_receipts VALUES(1002,?,?,?,?,?)',('historic',b'digest',0,record,bytes(108)))
        before=self.s.snapshot(1002)
        with self.assertRaisesRegex(ValueError,'history'):self.s.repair_duplicate_weapon_instances()
        self.assertEqual(before,self.s.snapshot(1002))

    def test_allocator_reserves_minus_one_and_rolls_back(self):
        with self.s.db:self.s.db.execute("INSERT OR REPLACE INTO counters VALUES('inventory_instance',4294967294)")
        with self.assertRaises(ValueError):self.s.provision_local(1003,'Third')
        self.assertFalse(self.s.db.execute('SELECT 1 FROM accounts WHERE uid=1003').fetchone())
