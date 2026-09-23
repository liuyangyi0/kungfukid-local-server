import struct
import sqlite3
import unittest
from server.kk_local import title_rewards as titles
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.store import Store
from server.kk_local.maps import MapCatalog
from server.kk_local.wire import Message


def choice(key=25303001,item=253030):
    catalog=bytearray(108);catalog[4]=25;catalog[48]=1;struct.pack_into('<II',catalog,5,item,key)
    grant=bytearray(68);grant[4]=25;struct.pack_into('<I',grant,5,item);grant[-1]=55
    return dict(catalog_hex=catalog.hex(),grant_hex=grant.hex())


class TitleRewardTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:');self.s.seed_local();self.s.provision_local(1002,'Second')
        self.maps=MapCatalog({},{});self.maps.title_levels=frozenset(range(17))
        self.e=Engine(self.s,map_catalog=self.maps);self.c=Connection(2,Phase.LOBBY,1001);self.e.game=self.c
        titles.configure(self.s,2,[choice(),choice(25303301,253033)],self.maps.title_levels)

    def tearDown(self):self.s.close()

    def query(self):return self.e.handle(self.c,Message(6000,bytes(4)))
    def claim(self,key=25303001):
        p=bytearray(149);struct.pack_into('<I',p,145,key)
        return self.e.handle(self.c,Message(4126,bytes(p)))

    def test_earned_offer_catalogue_precedes_selector_and_claim_is_single(self):
        self.assertEqual(self.query(),[])
        self.assertNotIn(2160,[m.id for m in self.claim()])
        before=self.s.snapshot(1001);titles.issue(self.s,1001,2,self.maps.title_levels)
        out=self.query();self.assertEqual([m.id for m in out],[1550,4125])
        self.assertEqual(len(out[1].payload),64);self.assertEqual(out[1].payload[0],2)
        self.assertEqual(struct.unpack_from('<I',out[1].payload,8)[0],25303001)
        self.assertEqual(len(self.s.snapshot(1001)[3]),len(before[3]))
        result=self.claim();self.assertEqual(result[0].id,2160)
        record=result[0].payload;self.assertEqual(record[-1],55)
        self.assertEqual(self.claim()[0].payload,record)
        self.assertNotIn(2160,[m.id for m in self.claim(25303301)])
        self.assertEqual(len(self.s.snapshot(1001)[3]),len(before[3])+68)
        self.assertEqual(self.query(),[])  # no repeated popup after claim

    def test_stale_claim_cannot_take_next_title_on_same_connection(self):
        titles.issue(self.s,1001,2,self.maps.title_levels);self.query();old=self.claim()[0].payload
        titles.configure(self.s,3,[choice()],self.maps.title_levels);titles.issue(self.s,1001,3,self.maps.title_levels)
        self.assertEqual(self.query(),[]);self.assertEqual(self.claim()[0].payload,old)
        self.assertIsNone(self.s.db.execute('SELECT claimed_key FROM title_entitlements WHERE uid=1001 AND level=3').fetchone()[0])

    def test_deleted_reward_retry_does_not_resurrect(self):
        titles.issue(self.s,1001,2,self.maps.title_levels);self.query();raw=self.claim()[0].payload
        instance=struct.unpack_from('<I',raw)[0]
        with self.s.db:self.s.db.execute('DELETE FROM inventory WHERE uid=1001 AND instance=?',(instance,))
        self.assertNotIn(2160,[m.id for m in self.claim()])

    def test_config_changes_do_not_rewrite_earned_options(self):
        titles.issue(self.s,1001,2,self.maps.title_levels)
        titles.configure(self.s,2,[choice(25303301,253033)],self.maps.title_levels)
        self.query();self.assertEqual(struct.unpack_from('<I',self.claim()[0].payload,5)[0],253030)

    def test_shop_key_conflict_rolls_back_entitlement_and_title(self):
        raw=bytearray.fromhex(choice()['catalog_hex']);raw[-1]=9
        self.s.replace_shop_catalog(25,1,[bytes(raw)]);before=self.s.snapshot(1001)
        with self.assertRaises(ValueError):titles.issue(self.s,1001,2,self.maps.title_levels)
        self.assertEqual(before,self.s.snapshot(1001));self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM title_entitlements').fetchone()[0],0)

    def test_claim_storage_failure_rolls_back_new_inventory(self):
        titles.issue(self.s,1001,2,self.maps.title_levels);self.query();before=self.s.snapshot(1001)
        self.s.db.execute("CREATE TRIGGER deny_claim BEFORE UPDATE ON title_entitlements BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.claim()
        self.assertEqual(before,self.s.snapshot(1001))

    def test_cross_account_and_new_connection_need_their_own_offer(self):
        titles.issue(self.s,1001,2,self.maps.title_levels);self.query()
        e2=Engine(self.s,account_uid=1002,map_catalog=self.maps);c2=Connection(3,Phase.LOBBY,1002);e2.game=c2
        p=bytearray(149);struct.pack_into('<I',p,145,25303001)
        self.assertNotIn(2160,[m.id for m in e2.handle(c2,Message(4126,bytes(p)))])
        self.e.game=Connection(4,Phase.LOBBY,1001)
        self.assertNotIn(2160,[m.id for m in self.e.handle(self.e.game,Message(4126,bytes(p)))])

    def test_unknown_title_duplicate_choices_and_pending_title_block(self):
        with self.assertRaises(ValueError):titles.configure(self.s,99,[choice()],self.maps.title_levels)
        with self.assertRaises(ValueError):titles.configure(self.s,2,[choice(),choice()],self.maps.title_levels)
        titles.issue(self.s,1001,2,self.maps.title_levels)
        with self.assertRaises(ValueError):titles.issue(self.s,1001,3,self.maps.title_levels)

    def test_entitlement_and_claim_survive_store_restart(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as root:
            path=str(Path(root)/'account.sqlite3');s=Store(path);s.seed_local()
            titles.configure(s,2,[choice()],self.maps.title_levels);titles.issue(s,1001,2,self.maps.title_levels);s.close()
            s=Store(path)
            try:
                e=Engine(s,map_catalog=self.maps);c=Connection(20,Phase.LOBBY,1001);e.game=c
                self.assertEqual([m.id for m in titles.announce(e,c)],[1550,4125])
                first=titles.claim(s,1001,2,25303001)
            finally:s.close()
            s=Store(path)
            try:
                self.assertEqual(titles.claim(s,1001,2,25303001),first)
                self.assertEqual(len(s.snapshot(1001)[3]),8*68)
            finally:s.close()
