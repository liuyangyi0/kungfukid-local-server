import sqlite3
import struct
import unittest
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.store import Store
from server.kk_local.wire import Message
from server.kk_local import mailbox


class MailTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:');self.s.seed_local();self.s.provision_local(1002,'Second')
        self.e=Engine(self.s);self.c=Connection(2,Phase.LOBBY,1001);self.e.game=self.c
        catalog=bytearray(108);catalog[4]=25;struct.pack_into('<II',catalog,5,253033,25303301)
        item=bytearray(68);item[4]=25;struct.pack_into('<I',item,5,253033);item[-1]=88
        self.catalog=bytes(catalog);self.item=bytes(item)

    def tearDown(self):self.s.close()

    def deliver(self,uid=1001,identity='one',**kw):
        return mailbox.deliver(self.s,uid,identity,title='测试',sender='Local',body='附件',catalog=self.catalog,grant=self.item,**kw)

    def action(self,ident,key,uid=1001):return self.e.handle(self.c,Message(ident,struct.pack('<QI',uid,key)))

    def open(self):
        key=self.deliver();out=self.action(1320,key)
        self.assertEqual(out[0].id,1330)
        return key,struct.unpack_from('<I',out[0].payload,8)[0]

    def test_real_list_detail_and_no_grant_on_read(self):
        before=self.s.snapshot(1001);key=self.deliver()
        record=self.e.handle(self.c,Message(1300))[0]
        self.assertEqual((record.id,len(record.payload)),(1310,339))
        self.assertEqual(record.payload[4:9],'测试'.encode('gbk')+b'\0')
        attachment=struct.unpack_from('<I',record.payload,331)[0]
        self.assertNotEqual(key,attachment)
        detail=self.action(1320,key)[0].payload
        self.assertEqual(len(detail),136);self.assertEqual(detail[28:],self.catalog)
        self.assertEqual(struct.unpack_from('<III',detail),(key,1,attachment))
        self.assertEqual(before,self.s.snapshot(1001))
        self.assertEqual(struct.unpack_from('<I',mailbox.listing(self.s,1001),327)[0],1)

    def test_claim_auto_delete_and_retry_never_duplicate_or_resurrect(self):
        key,attachment=self.open();before=len(self.s.snapshot(1001)[3])
        self.assertEqual([m.id for m in self.action(2171,attachment)],[1120])
        self.assertEqual(len(self.s.snapshot(1001)[3]),before+68)
        self.assertEqual(self.action(1340,key),[Message(1350,struct.pack('<BI',1,key))])
        self.assertEqual(mailbox.listing(self.s,1001),b'')
        inventory=self.s.snapshot(1001)
        self.action(2171,attachment);self.assertEqual(inventory,self.s.snapshot(1001))
        new=self.s.db.execute('SELECT claimed_instance FROM mailbox WHERE uid=1001 AND id=?',(key,)).fetchone()[0]
        with self.s.db:self.s.db.execute('DELETE FROM inventory WHERE uid=1001 AND instance=?',(new,))
        self.action(2171,attachment);self.assertEqual(len(self.s.snapshot(1001)[3]),before)

    def test_failed_claim_keeps_following_automatic_delete_from_losing_mail(self):
        key,attachment=self.open()
        # Wrong attachment in the same native preview flow must not delete it.
        self.action(2171,attachment+99)
        self.assertEqual(self.action(1340,key),[Message(1350,struct.pack('<BI',0,key))])
        self.assertEqual(len(mailbox.listing(self.s,1001)),339)
        self.action(2171,attachment);self.assertEqual([m.id for m in self.action(1340,key)],[1350])

    def test_cross_account_and_no_preview_cannot_claim(self):
        key=self.deliver(1002)
        attachment=self.s.db.execute('SELECT attachment FROM mailbox WHERE uid=1002 AND id=?',(key,)).fetchone()[0]
        before=self.s.snapshot(1001)
        self.assertNotIn(1330,[m.id for m in self.action(1320,key)])
        self.assertNotIn(1120,[m.id for m in self.action(2171,attachment)])
        self.assertEqual(self.action(1340,key)[0].payload[0],0)
        self.assertNotIn(1330,[m.id for m in self.action(1320,key,1002)])
        self.assertEqual(before,self.s.snapshot(1001))

    def test_delivery_is_idempotent_and_has_no_hidden_currency_grant(self):
        key=self.deliver();self.assertEqual(self.deliver(),key)
        with self.assertRaises(ValueError):mailbox.deliver(self.s,1001,'one',title='Changed',sender='Local',body='')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM mailbox').fetchone()[0],1)
        self.assertEqual(self.s.gold_balance(1001),0)

    def test_claim_failure_rolls_back_new_instance_and_receipt(self):
        key,attachment=self.open();before=self.s.snapshot(1001)
        self.s.db.execute("CREATE TRIGGER fail_mail BEFORE UPDATE ON mailbox BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):mailbox.claim(self.s,1001,attachment)
        self.assertEqual(before,self.s.snapshot(1001))
        self.assertIsNone(self.s.db.execute('SELECT claimed_instance FROM mailbox WHERE id=?',(key,)).fetchone()[0])

    def test_text_only_mail_and_malformed_inputs(self):
        key=mailbox.deliver(self.s,1001,'text',title='Notice',sender='Local',body='hello')
        detail=self.action(1320,key)[0].payload
        self.assertEqual(detail[8:],bytes(128))
        for bad in ('a\0b','x'*21,'😀'):
            with self.assertRaises(ValueError):mailbox.deliver(self.s,1001,'bad',title=bad,sender='Local',body='')
        self.c.phase=Phase.BATTLE;self.assertEqual(self.e.handle(self.c,Message(1300)),[])

    def test_new_connection_cannot_reuse_previous_preview(self):
        _,attachment=self.open();self.e.game=Connection(3,Phase.LOBBY,1001)
        out=self.e.handle(self.e.game,Message(2171,struct.pack('<QI',1001,attachment)))
        self.assertNotIn(1120,[m.id for m in out])
