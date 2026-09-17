import sqlite3
import asyncio
import struct
import tempfile
import unittest
from pathlib import Path
from server.kk_local.store import Store
from server.kk_local.engine import Engine,Connection
from server.kk_local.wire import Message,GameDecoder,encode_game
from server.kk_local.service import Service
from server.tests.test_competitive_rooms import prepare_handoff
from server.tests.test_local_service import hello


def setup_offer(s):
    # Synthetic transactional fixture; never installed into the user's VM.
    r=bytearray(108);r[4]=25;struct.pack_into('<II',r,5,253033,25303301)
    struct.pack_into('<II',r,30,100,100);r[48]=1
    item=bytearray(68);item[4]=25;struct.pack_into('<I',item,5,253033)
    struct.pack_into('<H',item,23,1)
    s.replace_shop_catalog(25,1,[bytes(r)])
    s.enable_gold_offer(25303301,bytes(item));s.set_gold_balance(1001,250)
    req=bytearray(169);struct.pack_into('<IQ',req,0,111,1001)
    struct.pack_into('<Q',req,54,1001);struct.pack_into('<II',req,145,25303301,100)
    return bytes(req),bytes(r)


class PurchaseTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:');self.s.seed_local();self.req,self.catalog=setup_offer(self.s)
    def tearDown(self):self.s.close()

    def test_commit_replay_and_distinct_intents(self):
        before=len(self.s.snapshot(1001)[3])
        a=self.s.purchase_gold_once(1001,'operation-a',self.req)
        self.assertEqual(a[0],150)
        self.assertEqual(len(self.s.snapshot(1001)[3]),before+68)
        self.assertEqual(self.s.purchase_gold_once(1001,'operation-a',self.req),a)
        b=self.s.purchase_gold_once(1001,'operation-b',self.req)
        self.assertEqual(b[0],50);self.assertNotEqual(a[1][:4],b[1][:4])
        self.assertEqual(self.s.purchase_gold_once(1001,'operation-a',self.req)[0],50)
        with self.assertRaises(ValueError):self.s.purchase_gold_once(1001,'operation-c',self.req)
        self.assertEqual(self.s.gold_balance(1001),50)

    def test_untrusted_quotes_identity_and_special_terms(self):
        before=self.s.snapshot(1001)
        for offset,value in ((149,1),(153,1),(157,1),(161,1),(165,1),(4,1002),(54,1002),(0,109)):
            bad=bytearray(self.req);struct.pack_into('<I',bad,offset,value)
            with self.assertRaises(ValueError):self.s.purchase_gold_once(1001,f'bad-{offset}',bytes(bad))
        self.assertEqual(self.s.snapshot(1001),before);self.assertEqual(self.s.gold_balance(1001),250)
        self.s.purchase_gold_once(1001,'once',self.req)
        with self.assertRaises(ValueError):self.s.purchase_gold_once(1001,'once',bytes(169))

    def test_sql_failure_rolls_back_debit_and_item(self):
        before=self.s.snapshot(1001)
        self.s.db.execute("CREATE TRIGGER fail_receipt BEFORE INSERT ON shop_receipts BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.s.purchase_gold_once(1001,'fail',self.req)
        self.assertEqual(self.s.snapshot(1001),before);self.assertEqual(self.s.gold_balance(1001),250)
        self.assertFalse(self.s.db.in_transaction)

    def test_catalog_change_requires_requalification(self):
        changed=bytearray(self.catalog);struct.pack_into('<I',changed,30,101)
        self.s.replace_shop_catalog(25,1,[bytes(changed)])
        with self.assertRaises(ValueError):self.s.purchase_gold_once(1001,'changed',self.req)
        self.assertEqual(self.s.gold_balance(1001),250)

    def test_receipt_retry_does_not_restore_removed_item(self):
        result=self.s.purchase_gold_once(1001,'once',self.req)
        instance=struct.unpack_from('<I',result[1])[0]
        with self.s.db:self.s.db.execute('DELETE FROM inventory WHERE uid=? AND instance=?',(1001,instance))
        self.assertIsNone(self.s.purchase_gold_once(1001,'once',self.req)[1])

    def test_engine_bootstrap_and_success_sequence(self):
        probe=Engine(self.s);probe.grant_offline_adapter_session()
        core=next(m for m in probe.handle(Connection(1),hello(1010)) if m.id==1020)
        self.assertEqual(struct.unpack_from('<I',core.payload,16)[0],250)
        e=Engine(self.s);prepare_handoff(e);c=Connection(2);e.handle(c,hello(2010))
        messages=e.handle(c,Message(9040,self.req))
        self.assertEqual([m.id for m in messages],[1240,2160,9050])
        self.assertEqual(struct.unpack('<I',messages[0].payload)[0],150)
        self.assertEqual(messages[2].payload,self.catalog)
        self.assertEqual([m.id for m in e.handle(c,Message(9040,self.req))],[1240,2160,9050])
        self.assertEqual(self.s.gold_balance(1001),50)

    def test_receipts_survive_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/'db.sqlite3');s=Store(path);s.seed_local();req,_=setup_offer(s)
            original=s.purchase_gold_once(1001,'saved',req);s.close()
            s=Store(path)
            try:self.assertEqual(s.purchase_gold_once(1001,'saved',req),original)
            finally:s.close()


class PurchaseNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_intents_then_insufficient_balance_over_tcp(self):
        s=Store(':memory:');s.seed_local();req,catalog=setup_offer(s)
        service=Service(s,lambda:True,login_port=0,game_port=0,p2p_port=0,offline_adapter=True)
        writer=None
        try:
            await service.start();prepare_handoff(service.engine)
            reader,writer=await asyncio.open_connection('127.0.0.1',service.game_port)
            async def receive():
                while True:
                    header=await asyncio.wait_for(reader.readexactly(8),3)
                    body=await asyncio.wait_for(reader.readexactly(struct.unpack_from('<I',header,4)[0]),3)
                    msg=GameDecoder().feed(header+body)[0]
                    if msg.id:return msg
            writer.write(encode_game(hello(2010)));await writer.drain()
            self.assertEqual((await receive()).id,2030)
            for balance in (150,50):
                writer.write(encode_game(Message(9040,req)));await writer.drain()
                messages=[await receive() for _ in range(3)]
                self.assertEqual([m.id for m in messages],[1240,2160,9050])
                self.assertEqual(struct.unpack('<I',messages[0].payload)[0],balance)
                self.assertEqual(messages[2].payload,catalog)
            writer.write(encode_game(Message(9040,req)));await writer.drain()
            self.assertEqual(await receive(),Message(9060,b'\x82\0'))
            writer.write(encode_game(Message(9070,b'\x19\1')));await writer.drain()
            self.assertEqual((await receive()).id,9080)  # rejection kept connection alive
            self.assertEqual(s.gold_balance(1001),50)
            self.assertEqual(s.db.execute('SELECT COUNT(*) FROM shop_receipts').fetchone()[0],2)
        finally:
            if writer:writer.close();await writer.wait_closed()
            await service.close();s.close()


if __name__=='__main__':unittest.main()
