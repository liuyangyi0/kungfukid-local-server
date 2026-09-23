import sqlite3
import struct
import unittest
from unittest.mock import patch
from server.kk_local import shop_setup,shop,shop_buy_repair
from server.kk_local.store import Store,PERMANENT_WEAPON_DISPLAY_MINUTES
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.wire import Message
from server.kk_local.renewal import register_lease


def data():
    def row(kind,key):return '\t'.join(map(str,[kind,key]+[0]*22))
    return ('\n'.join(row(k,i) for k,i in ((12,121005),(25,253033),(64,640001),(60,600001),(30,300001)))).encode()


class LocalShopSetupTests(unittest.TestCase):
    def setUp(self):self.s=Store(':memory:');self.s.seed_local();self.rows=shop_setup.compile_rows(data())
    def tearDown(self):self.s.close()
    def install(self):shop_setup.install(self.s,self.rows,100,100)

    def test_explicit_local_catalog_all_gold_and_preserves_player_data(self):
        before=self.s.snapshot(1001);self.install()
        self.assertEqual(len(self.rows),4);self.assertEqual(self.s.snapshot(1001),before);self.assertEqual(self.s.gold_balance(1001),0)
        for kind,key,raw,grant,permanent in self.rows:
            self.assertEqual(shop.terms(raw),('gold',100));self.assertEqual(raw[4],kind)
            self.assertEqual(struct.unpack_from('<I',raw,5)[0],key)
            self.assertEqual((raw[83],raw[13]),(1,0))  # native rbpBuy eligibility: level >= 0
            self.assertEqual(struct.unpack_from('<H',grant,23)[0],0 if permanent else 100)
        self.assertEqual(len(self.s.shop_records(10,25)),1)
        self.assertEqual(len(self.s.shop_records(252,25)),1)
        self.assertEqual(len(self.s.shop_records(255,0)),4)
        self.assertEqual(len(self.s.shop_records(10,67)),2)

    def test_purchase_outfit_and_weapon_registers_no_expiry_entitlement(self):
        self.install();self.s.set_gold_balance(1001,300)
        for key in (121005,253033):
            p=bytearray(169);struct.pack_into('<IQ',p,0,111,1001);struct.pack_into('<Q',p,54,1001)
            struct.pack_into('<II',p,145,key,100)
            _,_,item,_=shop.purchase(self.s,1001,str(key),bytes(p));instance=struct.unpack_from('<I',item)[0]
            self.assertEqual(struct.unpack_from('<I',item,13)[0],PERMANENT_WEAPON_DISPLAY_MINUTES)
            self.assertTrue(self.s.db.execute('SELECT 1 FROM permanent_weapons WHERE uid=1001 AND instance=?',(instance,)).fetchone())
            if item[4]==25:
                with self.assertRaises(ValueError):register_lease(self.s,1001,instance,9999999999)
        self.assertEqual(self.s.gold_balance(1001),100)

    def test_supply_purchase_count_not_permanent(self):
        self.install();self.s.set_gold_balance(1001,100)
        p=bytearray(169);struct.pack_into('<IQ',p,0,111,1001);struct.pack_into('<Q',p,54,1001)
        struct.pack_into('<II',p,145,640001,100)
        item=shop.purchase(self.s,1001,'supply',bytes(p))[2];instance=struct.unpack_from('<I',item)[0]
        self.assertEqual(struct.unpack_from('<H',item,23)[0],100)
        self.assertFalse(self.s.db.execute('SELECT 1 FROM permanent_weapons WHERE instance=?',(instance,)).fetchone())

    def test_reinstall_changes_price_not_old_inventory_or_receipts(self):
        self.install();self.s.set_gold_balance(1001,100)
        p=bytearray(169);struct.pack_into('<IQ',p,0,111,1001);struct.pack_into('<Q',p,54,1001);struct.pack_into('<II',p,145,253033,100)
        original=shop.purchase(self.s,1001,'one',bytes(p));before=self.s.snapshot(1001)
        rows=shop_setup.compile_rows(data(),price=200,supply_count=50);shop_setup.install(self.s,rows,200,50)
        self.assertEqual(self.s.snapshot(1001),before);self.assertEqual(shop.purchase(self.s,1001,'one',bytes(p)),original)

    def test_repair_existing_test_catalog_preserves_wallet_inventory_and_receipts(self):
        old=[]
        for kind,key,raw,grant,permanent in self.rows:
            raw=bytearray(raw);raw[83]=0
            old.append((kind,key,bytes(raw),grant,permanent))
        shop_setup.install(self.s,old,100,100)
        self.s.set_gold_balance(1001,300)
        p=bytearray(169);struct.pack_into('<IQ',p,0,111,1001);struct.pack_into('<Q',p,54,1001)
        struct.pack_into('<II',p,145,253033,100)
        receipt=shop.purchase(self.s,1001,'before-repair',bytes(p));before=self.s.snapshot(1001)
        self.assertEqual(shop_buy_repair.repair_buy_eligibility(self.s),4)
        self.assertEqual(shop_buy_repair.repair_buy_eligibility(self.s),0)
        self.assertEqual(self.s.snapshot(1001),before)
        self.assertEqual(shop.purchase(self.s,1001,'before-repair',bytes(p)),receipt)
        for row in self.s.shop_cache_records():self.assertEqual((row.raw[83],row.raw[13]),(1,0))
        for key, in self.s.db.execute('SELECT catalog_key FROM gold_offers'):
            self.assertEqual(shop.current_catalog(self.s,key),self.s.commerce.offer(key)[0])

    def test_repair_rejects_non_test_or_mismatched_offer_without_partial_update(self):
        with self.assertRaises(ValueError):shop_buy_repair.repair_buy_eligibility(self.s)
        self.install()
        key=self.rows[0][1]
        with self.s.transaction('corrupt test offer'):
            self.s.db.execute('UPDATE gold_offers SET catalog_record=? WHERE catalog_key=?',
                              (bytes(108),key))
        before=self.s.shop_cache_records()
        with self.assertRaises(ValueError):shop_buy_repair.repair_buy_eligibility(self.s)
        self.assertEqual(self.s.shop_cache_records(),before)

    def test_failed_install_rolls_back_original_storefront(self):
        self.install();before=self.s.shop_cache_records()
        damaged=list(self.rows);kind,key,raw,grant,permanent=damaged[-1];damaged[-1]=(kind,key,raw,b'bad',permanent)
        with self.assertRaises(ValueError):shop_setup.install(self.s,damaged,100,100)
        self.assertEqual(self.s.shop_cache_records(),before)

    def test_repair_write_failure_rolls_back_catalog_and_offers(self):
        old=[]
        for kind,key,raw,grant,permanent in self.rows:
            raw=bytearray(raw);raw[83]=0
            old.append((kind,key,bytes(raw),grant,permanent))
        shop_setup.install(self.s,old,100,100)
        before=(self.s.commerce.catalog_entries(),self.s.commerce.offer_entries())
        update=self.s.commerce.update_catalog_and_offer
        def interrupted(*args):
            update(*args)
            raise RuntimeError('simulated interrupted repair')
        with patch.object(self.s.commerce,'update_catalog_and_offer',side_effect=interrupted):
            with self.assertRaises(RuntimeError):shop_buy_repair.repair_buy_eligibility(self.s)
        self.assertEqual((self.s.commerce.catalog_entries(),self.s.commerce.offer_entries()),before)
        self.assertFalse(self.s.in_transaction)

    def test_duplicate_source_and_bad_bounds_reject(self):
        for raw in (data()+b'\n'+data(),b'bad'):
            with self.assertRaises(ValueError):shop_setup.compile_rows(raw)
        for price in (0,True,21474837):
            with self.assertRaises(ValueError):shop_setup.compile_rows(data(),price=price)
