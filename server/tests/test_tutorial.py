import struct
import unittest
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.maps import MapCatalog,MapDefinition
from server.kk_local.rooms import RoomHub
from server.kk_local.store import Store
from server.kk_local.wire import Message
from server.kk_local import title_rewards
from server.tests.test_title_rewards import choice


def request():
    p=bytearray(81);p[:5]=b'Guide';p[32:34]=b'\1\1';p[37]=1;p[46]=4
    struct.pack_into('<II',p,38,1201,1201)
    return Message(3010,bytes(p))


class TutorialTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:');self.s.seed_local();self.h=RoomHub(network_probe=True)
        self.maps=MapCatalog({1201:MapDefinition(1201,'guide',8,'unused','unused',(),())},{})
        self.maps.title_levels=frozenset(range(17));self.maps.tutorial_enabled=True
        self.e=Engine(self.s,hub=self.h,map_catalog=self.maps);self.c=Connection(2,Phase.LOBBY,1001);self.e.game=self.c
    def tearDown(self):self.s.close()

    def enter(self):
        out=self.e.handle(self.c,request());self.assertEqual(out,[Message(3020,struct.pack('<H',1)+request().payload)])
        self.assertIsNone(self.e.p2p);self.assertEqual(self.e.handle(self.c,Message(4030)),[])
        out=self.e.handle(self.c,Message(3070,struct.pack('<HB11s',1,0,b'')))
        self.assertEqual([m.id for m in out],[3100,3160]);self.assertEqual(len(out[0].payload),245)
        self.assertEqual(out[0].payload[160],7)  # own appearance was already delivered in1151;3100 is fixed245
        out=self.e.handle(self.c,Message(4030));self.assertEqual([m.id for m in out],[4050,4080])
        self.assertEqual([m.id for m in self.e.handle(self.c,Message(4160))],[4170,4180])
        self.assertEqual([m.id for m in self.e.handle(self.c,Message(8040,struct.pack('<HQI',1,1001,0)))],[8070])

    def test_native_creation_join_ready_without_fake_p2p_then_complete_before_leave(self):
        self.enter();out=self.e.handle(self.c,Message(4124))
        self.assertEqual([m.id for m in out],[4125,3115]);self.assertEqual(out[0].payload[0],2)
        self.assertEqual(len(out[0].payload),64);self.assertEqual(self.s.snapshot(1001)[2][123],2)
        self.assertEqual(self.c.phase,Phase.LOBBY);self.assertIsNone(self.e.room);self.assertFalse(self.h.rooms)
        self.assertEqual(len(self.s.snapshot(1001)[3]),7*68)

    def test_optional_reward_is_earned_not_granted_until_choice(self):
        title_rewards.configure(self.s,2,[choice()],self.maps.title_levels)
        self.enter();out=self.e.handle(self.c,Message(4124))
        self.assertEqual([m.id for m in out],[1550,4125,3115])
        p=bytearray(149);struct.pack_into('<I',p,145,25303001)
        out=self.e.handle(self.c,Message(4126,bytes(p)));self.assertEqual(out[0].id,2160)
        self.assertEqual(len(self.s.snapshot(1001)[3]),8*68)
        self.e.handle(self.c,Message(4124));self.e.handle(self.c,Message(4126,bytes(p)))
        self.assertEqual(len(self.s.snapshot(1001)[3]),8*68)

    def test_completion_not_accepted_before_all_input_or_from_another_mode(self):
        self.assertEqual(self.e.handle(self.c,Message(4124)),[])
        self.e.handle(self.c,request());self.assertEqual(self.e.handle(self.c,Message(4124)),[])
        self.assertEqual(self.s.snapshot(1001)[2][123],0)

    def test_private_room_is_hidden_and_disconnect_does_not_keep_stale_context(self):
        self.e.handle(self.c,request())
        self.s.provision_local(1002,'Second');peer=Engine(self.s,hub=self.h,account_uid=1002,map_catalog=self.maps)
        c2=Connection(3,Phase.LOBBY,1002);peer.game=c2
        out=peer.handle(c2,Message(2260,bytes((1,1,136))))
        self.assertEqual(len(out[0].payload),8)
        self.e.disconnect(self.c);self.assertFalse(self.h.rooms);self.assertFalse(self.h.suspended)

    def test_completed_profile_does_not_reenter_or_grant_again(self):
        self.enter();self.e.handle(self.c,Message(4124));out=self.e.handle(self.c,request())
        self.assertEqual([m.id for m in out],[4125,3115]);self.assertFalse(self.h.rooms)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM tutorial_completions').fetchone()[0],1)

    def test_catalogue_conflict_leaves_tutorial_and_profile_uncommitted(self):
        title_rewards.configure(self.s,2,[choice()],self.maps.title_levels)
        raw=bytearray.fromhex(choice()['catalog_hex']);raw[-1]=7;self.s.replace_shop_catalog(25,1,[bytes(raw)])
        self.enter();out=self.e.handle(self.c,Message(4124))
        self.assertNotIn(3115,[m.id for m in out]);self.assertEqual(self.e.room.stage,'battle')
        self.assertEqual(self.s.snapshot(1001)[2][123],0);self.assertIsNone(self.e.title_offer)

    def test_completion_receipt_failure_rolls_back_title_and_eligibility(self):
        import sqlite3
        self.enter();before=self.s.snapshot(1001)
        self.s.db.execute("CREATE TRIGGER deny_completion BEFORE INSERT ON tutorial_completions BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.e.handle(self.c,Message(4124))
        self.assertEqual(before,self.s.snapshot(1001));self.assertEqual(self.e.room.stage,'battle')
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM title_entitlements').fetchone()[0],0)
