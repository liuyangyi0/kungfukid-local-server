"""Adversarial tests against server boundaries, not the original client."""
import asyncio
import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import time
import unittest
from unittest.mock import patch
from server.kk_local.auth import AuthManager,AuthError
from server.kk_local.public_auth import PublicAuthManager
from server.kk_local.public_policy import PublicPolicy,ConnectionBudget,FairRate
from server.kk_local.public_admin import backup,DatabaseLease,check_database
from server.kk_local.public_commands import PublicCommands,validate_battle
from server.kk_local.public_listener import BoundedListener
from server.kk_local.storage.public_access import migrate,PublicAccessRepository,invite_digest
from server.kk_local.store import Store
from server.kk_local.wire import Message,ProtocolError,GameDecoder,encode_game
from server.tests.test_public_server import PASSWORD,prepare


class PublicStorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.path=self.root/'db.sqlite3';self.store=Store(str(self.path));self.store.authentication
    def tearDown(self):self.store.close();self.temp.cleanup()
    def test_explicit_migration_and_backup_includes_wal(self):
        with self.assertRaisesRegex(ValueError,'migration_required'):Store(str(self.path),public=True)
        self.store.db.execute('PRAGMA journal_mode=WAL');self.store.db.execute('PRAGMA wal_autocheckpoint=0')
        self.store.seed_local();before=self.store.snapshot(1001)
        backup(self.path,self.root/'before.sqlite3');migrate(self.store.db);check_database(self.path)
        self.assertEqual(before,self.store.snapshot(1001))
        copy=Store(str(self.root/'before.sqlite3'))
        try:self.assertEqual(before,copy.snapshot(1001))
        finally:copy.close()
        with self.assertRaises(ValueError):backup(self.path,self.path)
        with self.assertRaises(ValueError):backup(self.path,self.root/'before.sqlite3')
    def test_migration_failure_rolls_back_without_reset(self):
        self.store.seed_local();before=self.store.snapshot(1001)
        self.store.db.execute('CREATE TABLE public_disabled(collision INT)')
        with self.assertRaises(sqlite3.OperationalError):migrate(self.store.db)
        self.assertEqual(before,self.store.snapshot(1001))
        self.assertIsNone(self.store.db.execute("SELECT name FROM sqlite_master WHERE name='public_schema'").fetchone())
    def test_database_lease_refuses_second_operator(self):
        with DatabaseLease(self.path):
            with self.assertRaises(ValueError):
                with DatabaseLease(self.path):pass
        with DatabaseLease(self.path):pass
    def test_external_writer_cannot_block_public_event_loop(self):
        migrate(self.store.db);self.store.close();self.store=Store(str(self.path),public=True)
        other=sqlite3.connect(self.path);other.execute('BEGIN IMMEDIATE');began=time.monotonic()
        try:
            with self.assertRaises(sqlite3.OperationalError):
                with self.store.transaction():pass
            self.assertLess(time.monotonic()-began,.2)
        finally:other.rollback();other.close()
    def test_invitation_rollback_expiry_revocation(self):
        migrate(self.store.db);access=PublicAccessRepository(self.store.db);self.store.seed_local()
        with self.store.transaction():code=access.create_invite(100,10)
        with self.assertRaises(RuntimeError):
            with self.store.transaction():access.consume(invite_digest(code),1001,101);raise RuntimeError('rollback')
        access.check_invite(invite_digest(code),101)
        with self.assertRaises(ValueError):access.check_invite(invite_digest(code),110)
        with self.store.transaction():access.revoke_invite(code)
        with self.assertRaises(ValueError):access.check_invite(invite_digest(code),102)
    def test_audit_and_assets_rollback_together_and_owner_filter(self):
        migrate(self.store.db);self.store.seed_local();self.store.provision_local(1002,'Second')
        access=PublicAccessRepository(self.store.db);before=access.receipts(1001)
        with self.assertRaises(RuntimeError):
            with self.store.transaction():
                self.store.db.execute('UPDATE gold_wallet SET balance=99 WHERE uid=1001');raise RuntimeError()
        self.assertEqual(access.receipts(1001),before)
        self.assertTrue(all(row['kind'].startswith('inventory') for row in access.receipts(1002)))

    def test_purchase_commit_lost_reply_retry_and_backup_recovery(self):
        from server.kk_local import shop
        from server.tests.test_shop_commerce import offer,buy
        migrate(self.store.db);self.store.seed_local();offer(self.store,'gold');self.store.set_gold_balance(1001,100)
        first=shop.purchase(self.store,1001,'server-operation-1',buy('gold'))
        self.assertEqual(shop.purchase(self.store,1001,'server-operation-1',buy('gold')),first)
        with self.assertRaises(ValueError):shop.purchase(self.store,1001,'new-intent',buy('gold'))
        backup(self.path,self.root/'after.sqlite3');restored=Store(str(self.root/'after.sqlite3'),public=True)
        try:
            self.assertEqual(restored.gold_balance(1001),0)
            self.assertEqual(restored.snapshot(1001),self.store.snapshot(1001))
            self.assertEqual(restored.public_access.receipts(1001),self.store.public_access.receipts(1001))
        finally:restored.close()

    def test_global_capacity_separate_from_eight_fighter_room_layout(self):
        from types import SimpleNamespace
        from server.kk_local.rooms import RoomHub
        hub=RoomHub();hub.public_policy=PublicPolicy()
        for uid in range(100):hub.attach(SimpleNamespace(account_uid=uid,store=self.store))
        with self.assertRaises(ValueError):hub.attach(SimpleNamespace(account_uid=100,store=self.store))
        self.assertEqual(hub.public_policy.capabilities()['max_room_players'],8)


class PublicHashTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'db.sqlite3'
        self.store=Store(str(self.path));self.store.authentication;migrate(self.store.db)
        self.auth=PublicAuthManager(self.store,[dict(id=1,name='Test',host='127.0.0.1',game_port=18001)],policy=PublicPolicy())
    async def asyncTearDown(self):self.auth.close();self.store.close();self.temp.cleanup()
    def invite(self):
        with self.store.transaction():return self.auth.access.create_invite(int(time.time()))
    async def test_kdf_budget_cancellation_does_not_release_executing_jobs(self):
        gate=asyncio.Event();started=0
        async def work(_,encoded,salt):
            nonlocal started
            started+=1;await gate.wait();return bytes(32)
        with patch.object(AuthManager,'_hash',work):
            tasks=[asyncio.create_task(self.auth._hash(b'x',bytes(16))) for _ in range(10)]
            for _ in range(20):
                if started==2:break
                await asyncio.sleep(.01)
            self.assertEqual(started,2)
            with self.assertRaisesRegex(AuthError,'server_busy'):await self.auth._hash(b'x',bytes(16))
            tasks[0].cancel();await asyncio.gather(tasks[0],return_exceptions=True)
            self.assertEqual(self.auth.hash_admitted,10);self.assertEqual(started,2)
            gate.set();await asyncio.gather(*tasks,return_exceptions=True);await asyncio.sleep(0)
            self.assertEqual(self.auth.hash_admitted,0)
    async def test_queue_timeout_is_not_wrong_password(self):
        gate=asyncio.Event()
        async def work(_,encoded,salt):await gate.wait();return bytes(32)
        with patch.object(AuthManager,'_hash',work):
            tasks=[asyncio.create_task(self.auth._hash(b'x',bytes(16))) for _ in range(2)]
            await asyncio.sleep(.02)
            try:
                with self.assertRaisesRegex(AuthError,'server_busy'):await self.auth.login('NoSuchUser',PASSWORD)
                self.assertIsNone(self.auth.records.failure('nosuchuser'))
            finally:gate.set();await asyncio.gather(*tasks)
    async def test_registration_failure_does_not_burn_invite(self):
        code=self.invite()
        with patch.object(self.auth.records,'insert_credential',side_effect=RuntimeError('storage')):
            with self.assertRaises(RuntimeError):await self.auth.register('InviteFail',PASSWORD,invite_code=code)
        self.auth.access.check_invite(invite_digest(code),int(time.time()))
        self.assertEqual(self.store.profiles.account_uids('invitefail'),[])
        await self.auth.register('InviteFail',PASSWORD,invite_code=code)
    async def test_wrong_password_does_not_revoke_legal_session(self):
        await self.auth.register('SameAccount',PASSWORD,invite_code=self.invite())
        valid=await self.auth.login('SameAccount',PASSWORD)
        for _ in range(5):
            with self.assertRaises(AuthError):await self.auth.login('SameAccount','DefinitelyWrongPassword')
        self.assertEqual(self.auth._session(valid['session'])[1],valid['uid'])
    async def test_password_and_disable_revoke_only_after_commit(self):
        await self.auth.register('RevokeUser',PASSWORD,invite_code=self.invite());session=await self.auth.login('RevokeUser',PASSWORD);seen=[]
        self.auth.revocation_handlers.append(seen.append)
        with self.assertRaises(RuntimeError):
            with self.store.transaction():self.auth.records.remove_account_sessions(session['uid']);raise RuntimeError()
        self.assertEqual(seen,[]);self.auth._session(session['session'])
        await self.auth.set_local_password('RevokeUser','ChangedSecretPassword')
        self.assertEqual(len(seen),1)
        with self.assertRaises(AuthError):self.auth._session(session['session'])
        session=await self.auth.login('RevokeUser','ChangedSecretPassword');self.auth.disable(session['uid'])
        with self.assertRaises(AuthError):self.auth._session(session['session'])
        with self.assertRaisesRegex(AuthError,'invalid_credentials'):await self.auth.login('RevokeUser','ChangedSecretPassword')


class PublicListenerTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_tls_leases_acquired_before_handshake_and_cancelled(self):
        from server.tests.test_public_server import certificate
        import ssl
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);certificate(root);context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.load_cert_chain(root/'cert.pem',root/'key.pem')
            budget=ConnectionBudget(PublicPolicy(pending_connections=2,pending_per_ip=2));called=[]
            async def handler(*args):called.append(True)
            server=await BoundedListener('127.0.0.1',0,handler,budget,ssl_context=context,tls_seconds=.3).start();writers=[]
            try:
                for _ in range(3):r,w=await asyncio.open_connection('127.0.0.1',server.port);writers.append(w)
                await asyncio.sleep(.05);self.assertEqual(budget.pending,2);self.assertEqual(called,[])
                await asyncio.sleep(.4);self.assertEqual(budget.pending,0)
            finally:
                server.close();await server.wait_closed()
                for w in writers:w.close()
                await asyncio.gather(*(w.wait_closed() for w in writers),return_exceptions=True)
            self.assertEqual(budget.pending,0)
    async def test_handler_exit_and_shutdown_release_promoted_leases(self):
        budget=ConnectionBudget(PublicPolicy());gate=asyncio.Event()
        async def handler(r,w,lease):lease.promote(1001);await gate.wait()
        s=await BoundedListener('127.0.0.1',0,handler,budget).start();r,w=await asyncio.open_connection('127.0.0.1',s.port)
        await asyncio.sleep(.03);self.assertEqual(budget.active,1)
        s.close();await s.wait_closed();w.close();await w.wait_closed()
        self.assertEqual((budget.active,budget.pending),(0,0))


class PublicGameplayTests(unittest.TestCase):
    def setUp(self):
        from server.tests.test_lab_settlement import LabSettlementTests
        self.fixture=LabSettlementTests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
        f=self.fixture;f.hub.public_policy=PublicPolicy();f.hub.permanent_battle_rewards_allowed=False
        f.a.public_commands=PublicCommands(PublicPolicy());f.b.public_commands=PublicCommands(PublicPolicy())
    def test_existing_quest_rules_and_rewards_never_persist_battle_claims(self):
        from server.kk_local import quests
        from server.kk_local import quest_rewards
        from server.tests.test_quests import templates
        from server.tests.test_quest_rewards import rule
        f=self.fixture;t=templates();quests.configure(f.store,[dict(family=k[0],key=k[1]) for k in t],t)
        quest_rewards.configure(f.store,[rule()],t)
        f.hub.match_point_rewards={0:999,1:999,2:999};self.assertTrue(f.store.quests.has_rules())
        before=f.store.db.total_changes
        with patch.object(f.store,'award_match_points',side_effect=AssertionError('public rewards')):
            f.a.handle(f.ca,Message(4110,f.report((100,0))));out=f.b.handle(f.cb,Message(4110,f.report((100,0))))
        self.assertEqual(out[0].id,4120);self.assertEqual(f.store.db.total_changes,before)
        self.assertEqual(f.before,[f.store.snapshot(u) for u in (1001,1002)])
    def test_actor_spoof_nan_and_unreviewed_battle_are_rejected(self):
        f=self.fixture
        def motion(uid,x=0):
            p=bytearray(108);struct.pack_into('<IQ',p,0,8120,uid);struct.pack_into('<f',p,51,x);struct.pack_into('<H',p,97,3002);return bytes(p)
        validate_battle(f.a,motion(1001))
        for payload in (motion(1002),motion(9999),motion(1001,float('nan')),motion(1001,float('inf'))):
            with self.assertRaises(ProtocolError):validate_battle(f.a,payload)
        with self.assertRaises(ProtocolError):f.a.public_commands.udp(f.a,encode_game(Message(9040,bytes(169))))
    def test_unknown_commands_do_not_register_broadcast_or_execute(self):
        f=self.fixture
        for ident in range(50000,50010):self.assertEqual(f.a.handle(f.ca,Message(ident)),[])
        self.assertEqual(len(f.a.unknown),0)
        with self.assertRaises(ProtocolError):f.a.handle(f.ca,Message(50011))
    def test_large_claimed_length_rejected_before_full_buffer(self):
        from server.kk_local.public_commands import validate_header
        decoder=GameDecoder(header_validator=validate_header)
        raw=encode_game(Message(3010,bytes(2000)))
        with self.assertRaises(ProtocolError):decoder.feed(raw[:32])

    def test_udp_fanout_requires_recipient_session_echo(self):
        from types import SimpleNamespace
        from server.kk_local.native_relay import NativeRelay
        f=self.fixture;emitted=[];admission=SimpleNamespace(public_policy=PublicPolicy(),hub=f.hub,by_player={},validate=lambda grant:grant)
        relay=NativeRelay(admission,emit=lambda data,peer:emitted.append((data,peer)))
        grants=[]
        for index,engine in enumerate((f.a,f.b),1):
            peer=('127.0.0.1',40000+index)
            engine.p2p=dict(player=index,session=100+index,peer=peer,uid=engine.account_uid,expires=engine.clock()+60,bound=True,confirmed=False)
            g=SimpleNamespace(engine=engine,credential_digest=bytes([index]),udp_peer=peer,uid=engine.account_uid)
            grants.append(g);relay.peers[peer]=g;admission.by_player[index]=g
        def packet(ident,g,body,targets=(),session=None):
            extra=struct.pack('<'+'I'*len(targets),*targets)
            return struct.pack('<HHIIIIHBB',1,ident,session or g.engine.p2p['session'],0,g.engine.p2p['player'],0,0,0,len(extra))+extra+body
        a,b=grants;motion=bytearray(108);struct.pack_into('<IQ',motion,0,8120,1001);struct.pack_into('<H',motion,97,3002)
        data=packet(1008,a,encode_game(Message(8071,bytes(motion))),(2,))
        with self.assertRaisesRegex(AuthError,'unconfirmed'):relay.handle(a,data,a.udp_peer)
        self.assertEqual(emitted,[])
        with self.assertRaises(AuthError):relay.handle(b,packet(1013,b,bytes(4),session=999),b.udp_peer)
        self.assertFalse(b.engine.p2p['confirmed'])
        relay.handle(b,packet(1013,b,bytes(4)),b.udp_peer);self.assertTrue(b.engine.p2p['confirmed']);emitted.clear()
        relay.handle(a,data,a.udp_peer);self.assertEqual(len(emitted),1)
