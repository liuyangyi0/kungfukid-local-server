import struct
import tempfile
from pathlib import Path
import unittest

from server.kk_local.engine import Engine, Connection, Phase
from server.kk_local.store import Store
from server.kk_local.wire import Message, ProtocolError


class TrainingRewardsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name)/'training.db')
        self.store = Store(self.path)
        self.store.seed_local()

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_whole_hours_claim_is_once_and_preserves_unknown_profile_bytes(self):
        before = self.store.snapshot(1001)
        self.store.training(1001,1000,start=True)
        self.assertIsNone(self.store.claim_training(1001,4599))
        self.assertEqual(self.store.claim_training(1001,8200),dict(points=200,second=0,awarded=200))
        after = self.store.snapshot(1001)
        self.assertEqual(before[:2],after[:2])
        self.assertEqual(before[3],after[3])
        self.assertEqual(before[2][:245],after[2][:245])
        self.assertEqual(before[2][249:],after[2][249:])
        self.assertIsNone(self.store.claim_training(1001,8200))
        self.store.close()
        self.store=Store(self.path)
        self.assertIsNone(self.store.claim_training(1001,8201))
        self.assertEqual(self.store.snapshot(1001),after)
        self.store.training(1001,8201,start=True)
        self.assertEqual(self.store.claim_training(1001,11801)['awarded'],100)

    def test_cap_clock_rollback_and_unknown_account(self):
        self.store.training(1001,1000,start=True)
        self.assertIsNone(self.store.claim_training(1001,999))
        with self.assertRaises(ValueError):
            self.store.claim_training(999,10000)
        with self.assertRaises(ValueError):
            self.store.claim_training(1001,10000,points_per_hour=-1)
        self.assertEqual(self.store.claim_training(1001,10000000)['awarded'],2400)

    def test_score_notification_before_claim_ui_and_explicit_policy_gate(self):
        self.store.training(1001,1000,start=True)
        engine=Engine(self.store,wall_clock=lambda:8200,training_rewards=True)
        client=Connection(1,Phase.LOBBY,1001)
        engine.game=client
        out=engine.handle(client,Message(21006))
        self.assertEqual([m.id for m in out],[4300,21007])
        self.assertEqual(out[0].payload,struct.pack('<II',200,0))
        self.assertEqual(len(out[1].payload),56)
        self.assertEqual(struct.unpack_from('<II',out[1].payload,36),(100,2400))
        self.assertEqual([m.id for m in engine.handle(client,Message(21006))],[21005])
        engine.handle(client,Message(21002))
        self.assertEqual([m.id for m in engine.handle(client,Message(21006))],[21005])
        with self.assertRaises(ProtocolError):
            engine.handle(client,Message(21006,b'x'))
        engine.training_rewards=False
        self.assertEqual(engine.handle(client,Message(21006)),[])

    def test_failure_rolls_back_score_claim_and_training_together(self):
        self.store.training(1001,1000,start=True)
        self.store.db.execute("CREATE TRIGGER deny_claim BEFORE INSERT ON training_claims BEGIN SELECT RAISE(ABORT,'test'); END")
        before=self.store.snapshot(1001)
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.claim_training(1001,8200)
        self.assertEqual(self.store.snapshot(1001),before)
        self.assertEqual(self.store.training(1001,8200),(120,True))
        self.assertEqual(self.store.db.execute('SELECT count(*) FROM training_claims').fetchone()[0],0)
