import struct
import unittest
import sqlite3
from server.kk_local import renewal
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.store import Store
from server.kk_local.wire import Message


class RenewalTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:');self.s.seed_local();self.s.provision_local(1002,'Second')
        self.e=Engine(self.s);self.c=Connection(2,Phase.LOBBY,1001);self.e.game=self.c
        self.now=[1000000];self.e.wall_clock=self.e.clock=lambda:self.now[0]
        self.instance=0x100006;self.key=25303001
        catalog=bytearray(108);catalog[4]=25;catalog[48]=1
        struct.pack_into('<II',catalog,5,253030,self.key);struct.pack_into('<II',catalog,38,100,100)
        self.catalog=bytes(catalog);self.s.replace_shop_catalog(25,1,[self.catalog])
        renewal.enable_offer(self.s,self.key,7);renewal.set_tickets(self.s,1001,500)
        renewal.register_lease(self.s,1001,self.instance,self.now[0]-100)
        self.s.unequip(1001,self.instance)

    def tearDown(self):self.s.close()
    def quote(self):return self.e.handle(self.c,Message(1500,struct.pack('<BII',25,253030,1)))
    def request(self):
        p=bytearray(173);struct.pack_into('<IIQ',p,0,self.instance,105,1001)
        struct.pack_into('<Q',p,58,1001);struct.pack_into('<I',p,149,self.key);struct.pack_into('<I',p,161,100)
        return bytes(p)
    def confirm(self,p=None):return self.e.handle(self.c,Message(1420,self.request() if p is None else p))
    def deadline(self):return self.s.db.execute('SELECT expires FROM renewal_leases WHERE uid=1001 AND instance=?',(self.instance,)).fetchone()[0]

    def test_quote_and_same_instance_expired_renewal(self):
        before=self.s.snapshot(1001);q=self.quote()
        self.assertEqual([m.id for m in q],[1230,1510]);self.assertEqual(q[1].payload,self.catalog)
        self.assertEqual(before,self.s.snapshot(1001))
        out=self.confirm();self.assertEqual([m.id for m in out],[2161,1230,1430])
        self.assertEqual(struct.unpack_from('<I',out[0].payload)[0],self.instance)
        self.assertEqual(struct.unpack_from('<H',out[0].payload,17)[0],0)
        self.assertEqual(self.deadline(),self.now[0]+7*86400)
        self.assertEqual(renewal.tickets(self.s,1001),400)
        self.assertEqual(len(before[3]),len(self.s.snapshot(1001)[3]))
        current=self.s.snapshot(1001);self.confirm()
        self.assertEqual(current,self.s.snapshot(1001));self.assertEqual(renewal.tickets(self.s,1001),400)

    def test_nonexpired_extends_old_deadline_and_keeps_slot(self):
        renewal.register_lease(self.s,1001,self.instance,self.now[0]+1000)
        self.s.equip(1001,self.instance,8)
        self.quote();out=self.confirm()
        self.assertEqual(self.deadline(),self.now[0]+1000+7*86400)
        self.assertEqual(struct.unpack_from('<H',out[0].payload,17)[0],8)

    def test_reminder_ignore_preserves_inventory_and_new_expiry_reappears(self):
        before=self.s.snapshot(1001)
        out=self.e.handle(self.c,Message(1400))[0]
        self.assertEqual((out.id,len(out.payload)),(1410,124))
        self.assertEqual(struct.unpack_from('<II',out.payload,8),(self.instance,2));self.assertEqual(out.payload[70],100)
        self.assertEqual(self.e.handle(self.c,Message(1440,struct.pack('<I',self.instance))),[Message(1450,struct.pack('<I',self.instance))])
        self.assertEqual(self.e.handle(self.c,Message(1400)),[Message(1410)])
        self.assertEqual(before,self.s.snapshot(1001))
        self.quote();self.confirm();self.now[0]=self.deadline()+1
        self.assertEqual(len(self.e.handle(self.c,Message(1400))[0].payload),124)

    def test_no_quote_wrong_owner_wrong_price_and_permanent_rejected(self):
        before=self.s.snapshot(1001)
        self.assertEqual(self.confirm(),[Message(1430,bytes(5))]);self.quote()
        for offset,value,fmt in ((0,0x10000d,'<I'),(4,111,'<I'),(8,1002,'<Q'),(58,1002,'<Q'),(149,5,'<I'),(161,1,'<I')):
            p=bytearray(self.request());struct.pack_into(fmt,p,offset,value)
            self.assertEqual(self.confirm(bytes(p)),[Message(1430,bytes(5))])
        self.assertEqual(before,self.s.snapshot(1001))
        self.s.set_weapons_permanent(1001)
        self.assertEqual(self.confirm(),[Message(1430,bytes(5))]);self.assertEqual(renewal.tickets(self.s,1001),500)

    def test_changed_offer_or_expired_quote_does_not_charge(self):
        self.quote();renewal.enable_offer(self.s,self.key,7)
        self.assertEqual(self.confirm(),[Message(1430,bytes(5))])
        self.quote();self.now[0]+=600
        self.assertEqual(self.confirm(),[Message(1430,bytes(5))]);self.assertEqual(renewal.tickets(self.s,1001),500)

    def test_atomic_rollback_and_insufficient_balance(self):
        self.quote();before=self.s.snapshot(1001);deadline=self.deadline()
        self.s.db.execute("CREATE TRIGGER fail_renew BEFORE INSERT ON renewal_receipts BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.confirm()
        self.assertEqual(before,self.s.snapshot(1001));self.assertEqual(deadline,self.deadline());self.assertEqual(renewal.tickets(self.s,1001),500)
        self.s.db.execute('DROP TRIGGER fail_renew');renewal.set_tickets(self.s,1001,99)
        self.assertEqual(self.confirm(),[Message(1430,bytes(5))]);self.assertEqual(renewal.tickets(self.s,1001),99)

    def test_deleted_item_retry_does_not_resurrect_or_bill(self):
        self.quote();self.confirm()
        with self.s.db:self.s.db.execute('DELETE FROM inventory WHERE uid=1001 AND instance=?',(self.instance,))
        self.assertEqual([m.id for m in self.confirm()],[1230,1430])
        self.assertEqual(renewal.tickets(self.s,1001),400)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM inventory WHERE uid=1001').fetchone()[0],6)

    def test_unregistered_lease_and_stale_catalog_are_not_enabled(self):
        with self.s.db:self.s.db.execute('DELETE FROM renewal_leases')
        self.quote();self.assertEqual(self.confirm(),[Message(1430,bytes(5))])
        raw=bytearray(self.catalog);struct.pack_into('<I',raw,38,200)
        self.s.replace_shop_catalog(25,1,[bytes(raw)])
        self.assertEqual(self.quote()[-1],Message(1510))
        with self.assertRaises(ValueError):renewal.enable_offer(self.s,self.key,7)

    def test_expired_equipped_weapon_requires_native_unequip_path_first(self):
        self.s.equip(1001,self.instance,8);self.quote()
        before=self.s.snapshot(1001)
        self.assertEqual(self.confirm(),[Message(1430,bytes(5))])
        self.assertEqual(before,self.s.snapshot(1001));self.assertEqual(renewal.tickets(self.s,1001),500)
