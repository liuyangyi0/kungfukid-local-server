"""Transaction ownership and commit-time failures for task/title delivery."""
import sqlite3
import unittest

from server.kk_local import quests, quest_rewards, ordinary_quests, title_rewards
from server.kk_local.store import Store
from server.kk_local.wire import Message
from server.tests.test_quest_rewards import catalog, rule
from server.tests.test_title_rewards import choice
from server.tests import test_tutorial as tutorial_fixture


class QuestStorageBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.s = Store(':memory:')
        self.s.seed_local()
        self.table = catalog()
        self.template = self.table[('newbie', 3001)]
        self.availability = [dict(family='newbie', key=3001)]
        quests.configure(self.s, self.availability, self.table)
        quest_rewards.configure(self.s, [rule()], self.table)

    def tearDown(self):
        self.s.close()

    def finish(self):
        quests.transition(self.s, 1001, self.template, 2)
        for battle in (1, 2):
            self.s.award_match_points(battle, {1001: 1}, {0: 0, 1: 0, 2: 0}, mode=1, quest_templates=self.table)

    def test_repository_writes_never_start_transactions(self):
        q, t = self.s.quests, self.s.titles
        calls = (
            lambda: q.replace_availability([]), lambda: q.replace_rules([]),
            lambda: q.transition(1001, 'newbie', 3001, self.template.identity, 2, bytes(116), None),
            lambda: q.update_counts(1001, 'newbie', 3001, bytes(12), 4),
            lambda: q.mark_claimed(1001, 'newbie', 3001, None),
            lambda: t.set_rule(2, '[]'), lambda: t.insert(1001, 2, 'test', '[]', 0),
            lambda: t.mark_claimed(1001, 2, 1, 1), lambda: t.record_tutorial(1001, 1, 1),
        )
        before = q.availability()
        for call in calls:
            with self.assertRaises(ValueError):
                call()
            self.assertFalse(self.s.in_transaction)
        self.assertEqual(q.availability(), before)
        self.assertIsNone(q.progress(1001, 'newbie', 3001))
        self.assertFalse(t.tutorial_completed(1001))

    def test_public_operations_do_not_commit_or_rollback_outer_work(self):
        calls = (
            lambda: quests.configure(self.s, self.availability, self.table),
            lambda: quest_rewards.configure(self.s, [rule()], self.table),
            lambda: quests.transition(self.s, 1001, self.template, 2),
            lambda: quest_rewards.claim(self.s, 1001, self.template),
            lambda: ordinary_quests.complete(self.s, 1001, {}, {2}),
            lambda: title_rewards.configure(self.s, 2, [choice()], {2}),
            lambda: title_rewards.issue(self.s, 1001, 2, {2}),
            lambda: title_rewards.claim(self.s, 1001, 2, 25303001),
        )
        with self.assertRaisesRegex(RuntimeError, 'outer rollback'):
            with self.s.transaction():
                self.s.commerce.set_balance(1001, 'gold', 99)
                for call in calls:
                    with self.assertRaisesRegex(ValueError, 'nested'):
                        call()
                    self.assertTrue(self.s.in_transaction)
                    self.assertEqual(self.s.gold_balance(1001), 99)
                raise RuntimeError('outer rollback')
        self.assertEqual(self.s.gold_balance(1001), 0)
        self.assertIsNone(self.s.quests.progress(1001, 'newbie', 3001))

    def test_locked_helpers_require_and_preserve_callers_transaction(self):
        reward = {k: v for k, v in rule().items() if k not in ('family', 'key')}
        for call in (
            lambda: quest_rewards.grant_locked(self.s, 1001, reward),
            lambda: quest_rewards.settled_locked(self.s, 1, 1, {1001: 1}, self.table),
            lambda: title_rewards.issue_locked(self.s, 1001, 2, {2}, 'test'),
        ):
            with self.assertRaises(ValueError):
                call()
        before = self.s.snapshot(1001)
        with self.assertRaisesRegex(RuntimeError, 'outer rollback'):
            with self.s.transaction():
                title_rewards.issue_locked(self.s, 1001, 2, {2}, 'test')
                quest_rewards.grant_locked(self.s, 1001, reward)
                self.s.titles.record_tutorial(1001, 1, 1)
                self.assertTrue(self.s.in_transaction)
                self.assertEqual(self.s.gold_balance(1001), 50)
                raise RuntimeError('outer rollback')
        self.assertEqual(self.s.snapshot(1001), before)
        self.assertEqual(self.s.gold_balance(1001), 0)
        self.assertIsNone(self.s.titles.entitlement(1001, 2))
        self.assertFalse(self.s.titles.tutorial_completed(1001))

    def test_replace_configuration_failure_keeps_old_rows(self):
        availability = self.s.quests.availability()
        original_rule = self.s.quests.rule('newbie', 3001)
        operations = (
            ('quest_availability', lambda: quests.configure(self.s, self.availability, self.table)),
            ('quest_reward_rules', lambda: quest_rewards.configure(self.s, [rule(gold=77)], self.table)),
        )
        for table, call in operations:
            self.s.db.execute(f"CREATE TRIGGER deny_replace BEFORE INSERT ON {table} BEGIN SELECT RAISE(ABORT,'test'); END")
            try:
                with self.assertRaises(sqlite3.IntegrityError):
                    call()
                self.assertFalse(self.s.in_transaction)
                self.assertEqual(self.s.quests.availability(), availability)
                self.assertEqual(self.s.quests.rule('newbie', 3001), original_rule)
            finally:
                self.s.db.execute('DROP TRIGGER deny_replace')

    def test_quest_claim_commit_failure_restores_progress_wallet_item_and_allocator(self):
        self.finish()
        before = self.s.snapshot(1001)
        saved = self.s.quests.progress(1001, 'newbie', 3001)
        counter = self.s.db.execute("SELECT value FROM counters WHERE name='inventory_instance'").fetchone()
        self.s.db.execute('CREATE TABLE delayed_failure(uid INTEGER REFERENCES accounts(uid) DEFERRABLE INITIALLY DEFERRED)')
        self.s.db.execute("CREATE TRIGGER fail_at_commit AFTER UPDATE ON quest_progress WHEN NEW.state=3 "
                          "BEGIN INSERT INTO delayed_failure VALUES(99999); END")
        with self.assertRaises(sqlite3.IntegrityError):
            quest_rewards.claim(self.s, 1001, self.template)
        self.assertFalse(self.s.in_transaction)
        self.assertEqual(self.s.snapshot(1001), before)
        self.assertEqual(self.s.quests.progress(1001, 'newbie', 3001), saved)
        self.assertEqual(self.s.gold_balance(1001), 0)
        self.assertEqual(self.s.db.execute("SELECT value FROM counters WHERE name='inventory_instance'").fetchone(), counter)
        self.s.db.execute('DROP TRIGGER fail_at_commit')
        result = quest_rewards.claim(self.s, 1001, self.template)
        self.assertEqual([m.id for m in result], [1550, 2160, 1240, 6302])
        self.assertEqual(quest_rewards.claim(self.s, 1001, self.template), result)
        self.assertEqual(self.s.gold_balance(1001), 50)


class TutorialCommitBoundaryTests(unittest.TestCase):
    setUp = tutorial_fixture.TutorialTests.setUp
    tearDown = tutorial_fixture.TutorialTests.tearDown
    enter = tutorial_fixture.TutorialTests.enter

    def test_commit_failure_restores_in_memory_offer_and_does_not_leave_room(self):
        title_rewards.configure(self.s, 2, [choice()], self.maps.title_levels)
        self.enter()
        room, binding, snapshot = self.e.room, self.e.title_offer, self.s.snapshot(1001)
        self.s.db.execute('CREATE TABLE delayed_failure(uid INTEGER REFERENCES accounts(uid) DEFERRABLE INITIALLY DEFERRED)')
        self.s.db.execute('CREATE TRIGGER fail_at_commit AFTER INSERT ON tutorial_completions '
                          'BEGIN INSERT INTO delayed_failure VALUES(99999); END')
        with self.assertRaises(sqlite3.IntegrityError):
            self.e.handle(self.c, Message(4124))
        self.assertIs(self.e.room, room)
        self.assertEqual(self.e.room.stage, 'battle')
        self.assertEqual(self.e.title_offer, binding)
        self.assertEqual(self.s.snapshot(1001), snapshot)
        self.assertFalse(self.s.titles.tutorial_completed(1001))
        self.assertIsNone(self.s.titles.entitlement(1001, 2))
        self.assertFalse(self.s.in_transaction)
        self.s.db.execute('DROP TRIGGER fail_at_commit')
        self.assertEqual([m.id for m in self.e.handle(self.c, Message(4124))], [1550, 4125, 3115])
