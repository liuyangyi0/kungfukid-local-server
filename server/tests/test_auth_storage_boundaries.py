"""Auth transaction failures/races; real scrypt and network coverage stays in test_auth."""
import asyncio
import hashlib
import sqlite3
import unittest
from unittest.mock import patch

from server.kk_local.auth import AuthManager, AuthError, token_digest
from server.kk_local.store import Store


REGIONS = [dict(id=1, name='Local', host='127.0.0.1', game_port=8001)]
PASSWORD = 'Boundary-test-only-password'
REPLACEMENT = 'Replacement-test-only-password'


class AuthInitializationTests(unittest.TestCase):
    def test_optional_schema_cannot_commit_outer_work_or_start_a_worker(self):
        s = Store(':memory:')
        try:
            s.seed_local()
            self.assertIsNone(s.db.execute("SELECT name FROM sqlite_master WHERE name='auth_sessions'").fetchone())
            with s.transaction():
                s.commerce.set_balance(1001, 'gold', 42)
                with patch('server.kk_local.auth.ThreadPoolExecutor') as pool:
                    with self.assertRaisesRegex(ValueError, 'no active transaction'):
                        AuthManager(s, REGIONS)
                    pool.assert_not_called()
                self.assertTrue(s.in_transaction)
                self.assertEqual(s.gold_balance(1001), 42)
            # A refused initialization is retryable, rather than cached as ready.
            auth = AuthManager(s, REGIONS)
            try:
                self.assertIs(auth.records, s.authentication)
            finally:
                auth.close()
        finally:
            s.close()

    def test_repository_writes_require_the_callers_transaction(self):
        s = Store(':memory:')
        try:
            r = s.authentication
            digest, salt = bytes(32), bytes(16)
            calls = (
                lambda: r.insert_credential(1001, 'name', 'test', salt, digest),
                lambda: r.replace_credential(1001, 'name', 'test', salt, digest),
                lambda: r.record_failure('name', 1, 0, 0),
                lambda: r.clear_failure('name'),
                lambda: r.cleanup(0, -3600),
                lambda: r.insert_session(digest, 1001, 0, 10),
                lambda: r.remove_session(digest),
                lambda: r.remove_account_sessions(1001),
                lambda: r.insert_ticket(digest, digest, 1001, 1, 10),
                lambda: r.remove_ticket(digest),
                lambda: r.remove_session_tickets(digest),
            )
            for call in calls:
                with self.assertRaises(ValueError):
                    call()
                self.assertFalse(s.in_transaction)
            self.assertEqual(r.failure_count(), 0)
        finally:
            s.close()


class AuthTransactionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.s = Store(':memory:')
        self.s.seed_local()
        self.auth = AuthManager(self.s, REGIONS, clock=lambda: 1000)
        # Only transaction/race tests use this synthetic hash. Password hashing
        # parameters and persistence are exercised by the existing real tests.
        async def synthetic_hash(encoded, salt):
            return hashlib.sha256(salt + encoded).digest()
        self.hash_patch = patch.object(self.auth, '_hash', side_effect=synthetic_hash)
        self.hash_patch.start()
        self.user = await self.auth.register('Boundary', PASSWORD)
        self.uid = self.user['uid']

    async def asyncTearDown(self):
        self.hash_patch.stop()
        self.auth.close()
        self.s.close()

    async def login(self):
        return (await self.auth.login('Boundary', PASSWORD))['session']

    async def test_failed_password_commits_counter_before_public_error(self):
        for i in range(1, 6):
            with self.assertRaisesRegex(AuthError, 'invalid_credentials'):
                await self.auth.login('Boundary', REPLACEMENT)
            self.assertEqual(self.auth.records.failure('boundary'), (i, 1030 if i == 5 else 0))
            self.assertFalse(self.s.in_transaction)
        with self.assertRaisesRegex(AuthError, 'rate_limited'):
            await self.login()
        self.assertEqual(self.auth.records.session_digests(self.uid), [])

    async def test_session_cap_revokes_oldest_ticket_even_with_equal_timestamps(self):
        first = await self.login()
        ticket = self.auth.select_region(first, 1)['ticket']
        others = [await self.login() for _ in range(3)]
        with self.assertRaisesRegex(AuthError, 'invalid_session'):
            self.auth.list_regions(first)
        with self.assertRaisesRegex(AuthError, 'invalid_ticket'):
            self.auth.consume_ticket(ticket, self.uid, 1)
        self.assertEqual(len(self.auth.records.session_digests(self.uid)), 3)
        for token in others:
            self.assertEqual(self.auth.list_regions(token), REGIONS)

    async def test_failed_session_insert_restores_evicted_session_and_failure_record(self):
        tokens = [await self.login() for _ in range(3)]
        ticket = self.auth.select_region(tokens[0], 1)['ticket']
        with self.s.transaction():
            self.auth.records.record_failure('boundary', 2, 0, 1000)
        self.s.db.execute("CREATE TRIGGER deny_session BEFORE INSERT ON auth_sessions "
                          "BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            await self.login()
        self.assertEqual(self.auth.records.failure('boundary'), (2, 0))
        for token in tokens:
            self.assertEqual(self.auth.list_regions(token), REGIONS)
        self.assertEqual(self.auth.consume_ticket(ticket, self.uid, 1), self.uid)
        self.assertFalse(self.s.in_transaction)

    async def test_failed_password_reset_preserves_credentials_sessions_and_tickets(self):
        token = await self.login()
        ticket = self.auth.select_region(token, 1)['ticket']
        before = self.auth.records.credential('boundary')
        self.s.db.execute("CREATE TRIGGER deny_reset BEFORE UPDATE ON auth_credentials "
                          "BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            await self.auth.set_local_password('Boundary', REPLACEMENT)
        self.assertEqual(self.auth.records.credential('boundary'), before)
        self.assertEqual(self.auth.list_regions(token), REGIONS)
        self.assertEqual(self.auth.consume_ticket(ticket, self.uid, 1), self.uid)
        self.assertFalse(self.s.in_transaction)

    async def test_failed_ticket_replacement_leaves_previous_ticket_usable(self):
        token = await self.login()
        ticket = self.auth.select_region(token, 1)['ticket']
        self.s.db.execute("CREATE TRIGGER deny_ticket BEFORE INSERT ON auth_tickets "
                          "BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.auth.select_region(token, 1)
        self.assertEqual(self.auth.consume_ticket(ticket, self.uid, 1), self.uid)

    async def test_password_changed_while_hashing_never_grants_old_password(self):
        entered, release = asyncio.Event(), asyncio.Event()

        async def paused_hash(encoded, salt):
            if encoded == PASSWORD.encode():
                entered.set()
                await release.wait()
            return hashlib.sha256(salt + encoded).digest()

        with patch.object(self.auth, '_hash', side_effect=paused_hash):
            pending = asyncio.create_task(self.login())
            try:
                await asyncio.wait_for(entered.wait(), 2)
                self.assertFalse(self.s.in_transaction)
                await self.auth.set_local_password('Boundary', REPLACEMENT)
                release.set()
                with self.assertRaisesRegex(AuthError, 'invalid_credentials'):
                    await pending
            finally:
                release.set()
                if not pending.done():
                    pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        self.assertEqual(self.auth.records.session_digests(self.uid), [])
        self.assertEqual((await self.auth.login('Boundary', REPLACEMENT))['uid'], self.uid)

    async def test_auth_public_operations_cannot_commit_outer_business_work(self):
        token = await self.login()
        ticket = self.auth.select_region(token, 1)['ticket']
        before = self.auth.records.credential('boundary')
        with self.assertRaisesRegex(RuntimeError, 'outer rollback'):
            with self.s.transaction():
                self.s.commerce.set_balance(self.uid, 'gold', 99)
                for call in (
                    lambda: self.auth.logout(token),
                    lambda: self.auth.select_region(token, 1),
                    lambda: self.auth.consume_ticket(ticket, self.uid, 1),
                ):
                    with self.assertRaisesRegex(ValueError, 'nested'):
                        call()
                for coroutine in (
                    lambda: self.auth.login('Boundary', PASSWORD),
                    lambda: self.auth.register('Another', PASSWORD),
                    lambda: self.auth.set_local_password('Boundary', REPLACEMENT),
                ):
                    with self.assertRaisesRegex(ValueError, 'nested'):
                        await coroutine()
                self.assertTrue(self.s.in_transaction)
                self.assertEqual(self.s.gold_balance(self.uid), 99)
                raise RuntimeError('outer rollback')
        self.assertEqual(self.s.gold_balance(self.uid), 0)
        self.assertEqual(self.auth.records.credential('boundary'), before)
        self.assertEqual(self.s.profiles.account_uids('another'), [])
        self.assertEqual(self.auth.list_regions(token), REGIONS)
        self.assertEqual(self.auth.consume_ticket(ticket, self.uid, 1), self.uid)

    async def test_cancelled_hash_does_not_open_a_registration_transaction(self):
        entered = asyncio.Event()

        async def blocked_hash(*args):
            entered.set()
            await asyncio.Future()

        with patch.object(self.auth, '_hash', side_effect=blocked_hash):
            pending = asyncio.create_task(self.auth.register('Cancelled', PASSWORD))
            try:
                await asyncio.wait_for(entered.wait(), 2)
                self.assertFalse(self.s.in_transaction)
            finally:
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
        self.assertEqual(self.s.profiles.account_uids('cancelled'), [])
        self.assertIsNone(self.auth.records.credential('cancelled'))

    async def test_logout_rolls_back_cascade_when_storage_rejects_deletion(self):
        token = await self.login()
        ticket = self.auth.select_region(token, 1)['ticket']
        self.s.db.execute("CREATE TRIGGER deny_logout AFTER DELETE ON auth_sessions "
                          "BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.auth.logout(token)
        self.assertEqual(self.auth.list_regions(token), REGIONS)
        self.assertIsNotNone(self.auth.records.ticket(token_digest(ticket)))
        self.assertFalse(self.s.in_transaction)
