"""Business-unit rollback and byte preservation across extracted repositories."""
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from server.kk_local.store import Store


class ProfileProgressionBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.s = Store(':memory:')
        self.s.seed_local()

    def tearDown(self):
        self.s.close()

    def test_repository_mutations_require_an_owned_transaction(self):
        profile = self.s.snapshot(1001)[2]
        p, g = self.s.profiles, self.s.progression
        calls = (
            lambda: p.insert(1002, 'Other', 'Other', profile),
            lambda: p.update_profile(1001, profile),
            lambda: p.rename(1001, 'Other', profile),
            lambda: g.set_battle_counter(99),
            lambda: g.ensure_training(1001),
            lambda: g.start_training(1001, 10),
            lambda: g.clear_training(1001),
            lambda: g.record_training_claim(1001, 0, 3600, 100),
            lambda: g.record_match(1, 'receipt'),
            lambda: g.record_match_award(1, 1001, 1, 100),
        )
        for call in calls:
            with self.subTest(operation=call):
                with self.assertRaises(ValueError):
                    call()
                self.assertFalse(self.s.in_transaction)
        self.assertIsNone(p.account(1002))
        self.assertIsNone(g.training(1001))
        self.assertIsNone(g.match_receipt(1))

    def test_public_operations_never_commit_an_unrelated_outer_transaction(self):
        before = self.s.snapshot(1001)
        counter = self.s.progression.battle_counter()
        calls = (
            lambda: self.s.set_nickname(1001, 'Other'),
            lambda: self.s.rename_local(1001, 'Other'),
            lambda: self.s.next_battle(),
            lambda: self.s.training(1001, 10, start=True),
            lambda: self.s.claim_training(1001, 3600),
            lambda: self.s.award_match_points(1, {1001: 1}, {0: 0, 1: 100, 2: 0}),
        )
        with self.assertRaisesRegex(RuntimeError, 'outer rollback'):
            with self.s.transaction():
                self.s.commerce.set_balance(1001, 'gold', 88)
                for call in calls:
                    with self.assertRaises(ValueError):
                        call()
                    self.assertTrue(self.s.in_transaction)
                    self.assertEqual(self.s.gold_balance(1001), 88)
                raise RuntimeError('outer rollback')
        self.assertEqual(self.s.snapshot(1001), before)
        self.assertEqual(self.s.gold_balance(1001), 0)
        self.assertEqual(self.s.progression.battle_counter(), counter)
        self.assertIsNone(self.s.progression.training(1001))

    def test_provision_explicitly_joins_registration_transaction(self):
        with self.assertRaisesRegex(RuntimeError, 'credentials failed'):
            with self.s.transaction():
                self.s.provision_local(1002, 'Other')
                allocated = self.s.inventory.rows(1002, ordered=True)
                self.assertEqual(len(allocated), 7)
                self.assertTrue(self.s.in_transaction)
                raise RuntimeError('credentials failed')
        self.assertIsNone(self.s.profiles.account(1002))
        self.assertEqual(self.s.inventory.rows(1002), [])
        self.s.provision_local(1002, 'Other')
        self.assertEqual(self.s.inventory.rows(1002, ordered=True), allocated)

    def test_both_rename_paths_preserve_opaque_profile_and_inventory(self):
        raw = bytes(i % 256 for i in range(360))
        with self.s.transaction():
            self.s.profiles.update_profile(1001, raw)
        items = self.s.snapshot(1001)[3]
        for rename, name in ((self.s.set_nickname, '测试'), (self.s.rename_local, '新名字')):
            rename(1001, name)
            expected = bytearray(raw)
            expected[4:25] = name.encode('gbk').ljust(21, b'\0')
            self.assertEqual(self.s.snapshot(1001), ('KKLocal', name, bytes(expected), items))
            self.assertEqual(self.s.profile_word(1001, 352), raw[352:356])
        with self.assertRaises(ValueError):
            self.s.profile_word(1001, 0)

    def test_rename_storage_failure_restores_name_and_profile(self):
        before = self.s.snapshot(1001)
        self.s.db.execute("CREATE TRIGGER deny_name AFTER UPDATE OF nickname ON accounts "
                          "BEGIN SELECT RAISE(ABORT,'test'); END")
        for rename in (self.s.set_nickname, self.s.rename_local):
            with self.assertRaises(sqlite3.IntegrityError):
                rename(1001, 'Other')
            self.assertFalse(self.s.in_transaction)
            self.assertEqual(self.s.snapshot(1001), before)

    def test_battle_ids_are_shared_between_connections_and_exhaustion_is_atomic(self):
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / 'profiles.db')
            a, b = Store(path), Store(path)
            try:
                first = a.next_battle()
                self.assertEqual(b.next_battle(), first + 1)
                self.assertEqual(a.next_battle(), first + 2)
                with a.transaction():
                    a.progression.set_battle_counter(0xffffffff)
                with self.assertRaisesRegex(ValueError, 'exhausted'):
                    b.next_battle()
                self.assertFalse(b.in_transaction)
                self.assertEqual(a.progression.battle_counter(), 0xffffffff)
            finally:
                b.close()
                a.close()

    def test_quest_hook_failure_rolls_back_the_whole_match_reward(self):
        self.s.provision_local(1002, 'Other')
        before = {uid: self.s.snapshot(uid) for uid in (1001, 1002)}

        def fail(store, *args):
            self.assertTrue(store.in_transaction)
            store.commerce.set_balance(1001, 'gold', 99)
            raise RuntimeError('quest progress failed')

        with patch('server.kk_local.quest_rewards.settled_locked', side_effect=fail):
            with self.assertRaisesRegex(RuntimeError, 'quest progress failed'):
                self.s.award_match_points(7, {1001: 1, 1002: 2}, {0: 0, 1: 100, 2: 10}, mode=1)
        self.assertFalse(self.s.in_transaction)
        self.assertEqual({uid: self.s.snapshot(uid) for uid in before}, before)
        self.assertEqual(self.s.gold_balance(1001), 0)
        self.assertIsNone(self.s.progression.match_receipt(7))
        self.assertEqual(self.s.match_point_awards(7), {})
