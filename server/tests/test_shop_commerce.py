import sqlite3
import struct
import tempfile
from pathlib import Path
import unittest
from server.kk_local import shop,mailbox
from server.kk_local.store import Store
from server.kk_local.renewal import set_tickets,tickets
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.wire import Message


def offer(s,currency='ticket',kind=25,sale=0,current=100):
    item_id=253033 if kind==25 else 600001
    raw=bytearray(108);raw[4]=kind;raw[48]=1;raw[46]=sale
    struct.pack_into('<II',raw,5,item_id,20001)
    struct.pack_into('<II',raw,30 if currency=='gold' else 38,100,current)
    grant=bytearray(68);grant[4]=kind;struct.pack_into('<I',grant,5,item_id);struct.pack_into('<H',grant,23,5)
    s.replace_shop_catalog(kind,1,[bytes(raw)]);shop.enable_offer(s,20001,bytes(grant))
    return bytes(raw),bytes(grant)


def buy(currency='ticket',price=100,uid=1001):
    p=bytearray(169);struct.pack_into('<IQ',p,0,111 if currency=='gold' else 109,uid)
    struct.pack_into('<Q',p,54,uid);struct.pack_into('<I',p,145,20001)
    struct.pack_into('<I',p,149 if currency=='gold' else 157,price);return bytes(p)


def gift(name='Second',hint=0,price=100,text='送给你'):
    p=bytearray(426);struct.pack_into('<I',p,0,109);struct.pack_into('<Q',p,54,hint)
    name=name.encode('gbk');p[83:83+len(name)]=name;struct.pack_into('<I',p,145,20001);struct.pack_into('<I',p,157,price)
    text=text.encode('gbk');p[170:170+len(text)]=text;return bytes(p)


class ShopCommerceTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:');self.s.seed_local();self.s.provision_local(1002,'Second');self.raw,self.grant=offer(self.s)
        set_tickets(self.s,1001,500);self.s.set_gold_balance(1001,500)
    def tearDown(self):self.s.close()

    def test_ticket_purchase_uses_separate_wallet_and_native_ack(self):
        result=shop.purchase(self.s,1001,'buy',buy())
        self.assertEqual(result[:2],('ticket',400));self.assertEqual(self.s.gold_balance(1001),500)
        self.assertEqual([m.id for m in shop.purchase_packets(result)],[1230,2160,9050])
        self.assertEqual(shop.purchase(self.s,1001,'buy',buy()),result)

    def test_consumable_quantity_and_distinct_intents_get_unique_instances(self):
        self.s.replace_shop_catalog(25,1,[])  #do not reuse a live key for another product
        offer(self.s,'gold',60)
        a=shop.purchase(self.s,1001,'a',buy('gold'))
        b=shop.purchase(self.s,1001,'b',buy('gold'))
        self.assertEqual(struct.unpack_from('<H',a[2],23)[0],5);self.assertNotEqual(a[2][:4],b[2][:4])
        self.assertEqual(a[2][4],60);self.assertEqual(self.s.gold_balance(1001),300)

    def test_sale_purchase_and_gift_differ_as_native_senders_do(self):
        offer(self.s,sale=1,current=120)
        self.assertEqual(shop.purchase(self.s,1001,'a',buy(price=100))[1],400)
        self.assertEqual(shop.gift(self.s,1001,'b',gift(price=120))[0],280)
        offer(self.s,sale=1,current=80)
        self.assertEqual(shop.purchase(self.s,1001,'c',buy(price=80))[1],200)

    def test_invalid_prices_identities_credit_coupon_are_rejected(self):
        before=self.s.snapshot(1001)
        for offset,value in ((0,110),(4,1002),(54,1002),(149,1),(153,1),(157,1),(161,1),(165,1)):
            p=bytearray(buy());struct.pack_into('<I',p,offset,value)
            with self.assertRaises(ValueError):shop.purchase(self.s,1001,str(offset),bytes(p))
        self.assertEqual(self.s.snapshot(1001),before);self.assertEqual(tickets(self.s,1001),500)

    def test_stock_and_price_change_require_requalification(self):
        raw=bytearray(self.raw);struct.pack_into('<I',raw,38,90);self.s.replace_shop_catalog(25,1,[bytes(raw)])
        with self.assertRaises(ValueError):shop.purchase(self.s,1001,'x',buy(price=90))
        with self.assertRaises(ValueError):shop.gift(self.s,1001,'y',gift(price=90))
        self.assertEqual(tickets(self.s,1001),500)

    def test_purchase_failure_rolls_back_wallet_and_inventory(self):
        before=self.s.snapshot(1001)
        self.s.db.execute("CREATE TRIGGER stop_purchase BEFORE INSERT ON shop_receipts BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):shop.purchase(self.s,1001,'x',buy())
        self.assertEqual(self.s.snapshot(1001),before);self.assertEqual(tickets(self.s,1001),500)

    def test_gift_debits_once_then_mail_owner_claims_once(self):
        before=self.s.snapshot(1002);value,target,key,created=shop.gift(self.s,1001,'gift',gift())
        self.assertEqual((value,target,created),(400,1002,True));self.assertEqual(self.s.snapshot(1002),before)
        self.assertEqual(shop.gift(self.s,1001,'gift',gift()),(400,1002,key,False))
        attachment=self.s.db.execute('SELECT attachment FROM mailbox WHERE uid=1002 AND id=?',(key,)).fetchone()[0]
        with self.assertRaises(ValueError):mailbox.claim(self.s,1001,attachment)
        a=mailbox.claim(self.s,1002,attachment);b=mailbox.claim(self.s,1002,attachment)
        self.assertEqual(a,b);self.assertEqual(tickets(self.s,1001),400)
        self.assertEqual(len(self.s.snapshot(1002)[3]),len(before[3])+68)

    def test_bad_recipient_and_unsupported_gift_terms_do_not_charge(self):
        for p in (gift('Missing'),gift('KKLocal'),gift(hint=1003),gift(price=1),gift(text='x'*201)):
            with self.assertRaises(ValueError):shop.gift(self.s,1001,'x',p)
        self.s.provision_local(1003,'Third','Second')
        with self.assertRaises(ValueError):shop.gift(self.s,1001,'x',gift())
        self.assertEqual(tickets(self.s,1001),500);self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM mailbox').fetchone()[0],0)

    def test_gift_failure_rolls_back_mail_debit_and_receipt(self):
        self.s.db.execute("CREATE TRIGGER stop_gift BEFORE INSERT ON shop_gift_receipts BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):shop.gift(self.s,1001,'x',gift())
        self.assertEqual(tickets(self.s,1001),500);self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM mailbox').fetchone()[0],0)
        self.s.db.execute('DROP TRIGGER stop_gift');set_tickets(self.s,1001,1)
        with self.assertRaises(ValueError):shop.gift(self.s,1001,'x',gift())
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM mailbox').fetchone()[0],0)

    def test_gift_and_purchase_receipts_survive_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/'db.sqlite3');s=Store(path);s.seed_local();s.provision_local(1002,'Second');offer(s);set_tickets(s,1001,500)
            purchased=shop.purchase(s,1001,'buy',buy());given=shop.gift(s,1001,'gift',gift());s.close()
            s=Store(path)
            try:
                self.assertEqual(shop.purchase(s,1001,'buy',buy())[2],purchased[2])
                self.assertEqual(shop.gift(s,1001,'gift',gift()),(*given[:3],False));self.assertEqual(tickets(s,1001),300)
            finally:s.close()

    def test_replay_does_not_resurrect_deleted_purchase(self):
        r=shop.purchase(self.s,1001,'buy',buy());instance=struct.unpack_from('<I',r[2])[0]
        with self.s.db:self.s.db.execute('DELETE FROM inventory WHERE uid=1001 AND instance=?',(instance,))
        self.assertIsNone(shop.purchase(self.s,1001,'buy',buy())[2])
        self.assertEqual(tickets(self.s,1001),400)

    def test_shop_open_refreshes_wallets_and_old_connection_cannot_transact(self):
        e=Engine(self.s);c=Connection(1,Phase.LOBBY,1001);e.game=c
        out=e.handle(c,Message(9070,b'\x19\1'));self.assertEqual([m.id for m in out],[1240,1230,9080])
        self.assertEqual([m.id for m in e.handle(c,Message(9090,gift()))],[1230,9100])
        old=Connection(2,Phase.LOBBY,1001)
        self.assertEqual(e.handle(old,Message(9090,gift())),[])
        c.phase=Phase.BATTLE;self.assertEqual(e.handle(c,Message(9040,buy())),[])
        self.assertEqual(tickets(self.s,1001),400)

    def test_unsupported_requirements_and_invalid_grants_remain_disabled(self):
        for offset in (49,77,88):
            raw=bytearray(self.raw);raw[offset]=1;self.s.replace_shop_catalog(25,1,[bytes(raw)])
            with self.assertRaises(ValueError):shop.enable_offer(self.s,20001,self.grant)
        self.s.replace_shop_catalog(25,1,[self.raw]);grant=bytearray(self.grant);grant[17]=8
        with self.assertRaises(ValueError):shop.enable_offer(self.s,20001,bytes(grant))
