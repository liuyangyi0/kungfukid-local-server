import asyncio
import json
import os
from pathlib import Path
import socket
import sqlite3
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from server.kk_local.auth import AuthManager,AuthError,ALGORITHM
from server.kk_local.auth_service import AuthServer,run as run_auth_service
from server.kk_local.native_identity import NativeIdentity,WindowsNativeVerifier
from server.kk_local.service import Service
from server.kk_local.store import Store
from server.kk_local.wire import Message,encode_login,read_login,encode_game,GameDecoder
from server.tests.test_local_service import hello

PASSWORD='Synthetic-Only-Password42!'
REGIONS=[dict(id=1,name='Local',host='127.0.0.1',game_port=8001)]


class FakeVerifier:
    def __init__(self): self.identity=NativeIdentity(123,1,'test-client.exe')
    def process(self,pid):
        if pid!=self.identity.pid: raise ValueError('not test process')
        return self.identity
    def tcp_peer(self,peer,local): return self.identity
    def udp_peer(self,peer): return self.identity


class AuthTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=str(Path(self.temp.name)/'accounts.db')
        self.store=Store(self.path); self.store.seed_local()
        self.now=1000
        self.verifier=FakeVerifier()
        self.auth=AuthManager(self.store,REGIONS,clock=lambda:self.now,native_verifier=self.verifier)
    async def asyncTearDown(self):
        self.auth.close(); self.store.close(); self.temp.cleanup()

    async def registered(self):
        return await self.auth.register('NewPlayer',PASSWORD,'新玩家')

    async def test_registration_hashes_and_reserves_legacy_accounts(self):
        before=self.store.snapshot(1001)
        user=await self.registered()
        self.assertEqual(user['uid'],1002)
        row=self.store.db.execute('SELECT algorithm,salt,digest FROM auth_credentials').fetchone()
        self.assertEqual((row[0],len(row[1]),len(row[2])),(ALGORITHM,16,32))
        self.assertNotIn(PASSWORD.encode(),Path(self.path).read_bytes())
        self.assertEqual(self.store.snapshot(1001),before)
        self.assertEqual(len(self.store.snapshot(1002)[3]),7*68)
        with self.assertRaises(AuthError): await self.auth.register('newplayer',PASSWORD)
        with self.assertRaises(AuthError): await self.auth.register('KKLocal',PASSWORD)
        with self.assertRaises(AuthError): await self.auth.login('KKLocal',PASSWORD)

    async def test_registration_failure_rolls_back_account_and_items(self):
        self.store.db.execute("CREATE TRIGGER deny_credential BEFORE INSERT ON auth_credentials BEGIN SELECT RAISE(ABORT,'test'); END")
        with self.assertRaises(sqlite3.IntegrityError): await self.registered()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM accounts').fetchone()[0],1)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM inventory').fetchone()[0],7)

    async def test_invalid_password_lockout_and_restart(self):
        await self.registered()
        for _ in range(5):
            with self.assertRaisesRegex(AuthError,'invalid_credentials'):
                await self.auth.login('NewPlayer','Incorrect-password-long')
        with self.assertRaisesRegex(AuthError,'rate_limited'): await self.auth.login('NewPlayer',PASSWORD)
        self.now+=31
        result=await self.auth.login('NEWPLAYER',PASSWORD)
        self.assertEqual(result['uid'],1002)
        self.auth.close(); self.store.close()
        self.store=Store(self.path)
        self.auth=AuthManager(self.store,REGIONS,clock=lambda:self.now,native_verifier=self.verifier)
        self.assertEqual((await self.auth.login('NewPlayer',PASSWORD))['uid'],1002)

    async def test_region_ticket_is_one_use_identity_bound_and_revoked(self):
        await self.registered()
        session=(await self.auth.login('NewPlayer',PASSWORD))['session']
        self.assertEqual(self.auth.list_regions(session),REGIONS)
        with self.assertRaises(AuthError): self.auth.select_region(session,2)
        ticket=self.auth.select_region(session,1)['ticket']
        with self.assertRaises(AuthError): self.auth.consume_ticket(ticket,1001,1)
        self.assertEqual(self.auth.consume_ticket(ticket,1002,1),1002)
        with self.assertRaises(AuthError): self.auth.consume_ticket(ticket,1002,1)
        ticket=self.auth.select_region(session,1)['ticket']
        replacement=self.auth.select_region(session,1)['ticket']
        with self.assertRaises(AuthError): self.auth.consume_ticket(ticket,1002,1)
        self.auth.logout(session)
        with self.assertRaises(AuthError): self.auth.consume_ticket(replacement,1002,1)

    async def test_expiry_and_no_token_plaintext_in_database(self):
        await self.registered()
        result=await self.auth.login('NewPlayer',PASSWORD)
        ticket=self.auth.select_region(result['session'],1)['ticket']
        for secret in (result['session'],ticket): self.assertNotIn(secret.encode(),Path(self.path).read_bytes())
        self.now+=46
        with self.assertRaises(AuthError): self.auth.consume_ticket(ticket,1002,1)
        self.now=result['expires_at']
        with self.assertRaises(AuthError): self.auth.list_regions(result['session'])

    async def test_native_binding_rejects_wrong_process_and_pid_reuse(self):
        await self.registered()
        session=(await self.auth.login('NewPlayer',PASSWORD))['session']
        ticket=self.auth.select_region(session,1)['ticket']
        with self.assertRaises(AuthError): self.auth.bind_native_client(ticket,1002,1,124)
        self.auth.bind_native_client(ticket,1002,1,123)
        self.assertEqual(self.auth.native_status(session,123)['stage'],'bound')
        with self.assertRaises(AuthError): self.auth.bind_native_client(ticket,1002,1,123)
        self.verifier.identity=NativeIdentity(123,2,'test-client.exe')
        with self.assertRaises(AuthError): self.auth.native_status(session,123)

    async def test_local_password_setup_preserves_inventory_and_revokes_sessions(self):
        before=self.store.snapshot(1001)
        await self.auth.set_local_password('KKLocal',PASSWORD)
        old=(await self.auth.login('KKLocal',PASSWORD))['session']
        await self.auth.set_local_password('KKLocal','Replacement-Only-Password!')
        with self.assertRaises(AuthError): self.auth.list_regions(old)
        self.assertEqual(self.store.snapshot(1001),before)

    async def test_live_api_and_no_secrets_in_event_sink(self):
        events=[]
        server=await AuthServer(self.auth,port=0,event_sink=events.append).start()
        async def call(op,args):
            r,w=await asyncio.open_connection('127.0.0.1',server.port)
            payload=json.dumps(dict(schema='kk-local-auth-v1',operation=op,arguments=args)).encode()
            w.write(struct.pack('!I',len(payload))+payload); await w.drain()
            size=struct.unpack('!I',await r.readexactly(4))[0]
            result=json.loads(await r.readexactly(size)); w.close(); await w.wait_closed()
            return result
        try:
            self.assertTrue((await call('register',dict(account='ApiPlayer',password=PASSWORD)))['ok'])
            result=await call('login',dict(account='ApiPlayer',password=PASSWORD))
            self.assertTrue(result['ok'])
            session=result['result']['session']
            self.assertEqual((await call('regions',dict(session=session)))['result'],REGIONS)
            ticket=(await call('select_region',dict(session=session,region_id=1)))['result']['ticket']
            self.assertTrue((await call('bind_client',dict(ticket=ticket,uid=result['result']['uid'],region_id=1,pid=123)))['ok'])
            self.assertEqual((await call('status',dict(session=session,pid=123)))['result']['stage'],'bound')
            self.assertFalse((await call('consume_ticket',{}))['ok'])
            self.assertNotIn(PASSWORD,json.dumps(events))
            self.assertNotIn(session,json.dumps(events))
        finally: await server.close()

    async def test_native_cli_composes_logger_and_all_listeners(self):
        # Exercise the real run() composition, not just the two servers alone.
        from server.kk_local.maps import MapCatalog
        # This listener uses one port for TCP AND UDP. A TCP-only reservation
        # does not establish UDP availability (including Windows exclusions).
        for _ in range(32):
            with socket.socket(type=socket.SOCK_DGRAM) as udp, socket.socket() as tcp:
                udp.bind(('127.0.0.1',0))
                game_port=udp.getsockname()[1]
                try:
                    tcp.bind(('127.0.0.1',game_port))
                except OSError:
                    continue
                break
        else:
            self.fail('could not reserve a shared TCP/UDP test port')
        root=Path(self.temp.name)
        args=SimpleNamespace(client_root=str(root),database=str(root/'composed.db'),
            events=str(root/'composed.jsonl'),role_ready_file=str(root/'ready.txt'),
            game_port=game_port,login_port=0,auth_port=0)
        started=asyncio.Event()
        original_start=AuthServer.start
        async def observe_start(server):
            value=await original_start(server)
            started.set()
            return value
        with patch('server.kk_local.auth_service.WindowsNativeVerifier',return_value=self.verifier), patch.object(AuthServer,'start',observe_start), patch.object(MapCatalog,'from_client',return_value=MapCatalog({},{})) as read_maps:
            task=asyncio.create_task(run_auth_service(args))
            try:
                ready=asyncio.create_task(started.wait())
                try:
                    done,_=await asyncio.wait((ready,task),timeout=3,return_when=asyncio.FIRST_COMPLETED)
                    if task in done:
                        await task  # Surface startup errors, not a misleading readiness timeout.
                    self.assertIn(ready,done,'authentication listeners did not become ready')
                finally:
                    ready.cancel()
                    await asyncio.gather(ready,return_exceptions=True)
                self.assertFalse(task.done())
                read_maps.assert_called_once_with(root)
                rows=[json.loads(line) for line in Path(args.events).read_text(encoding='utf-8').splitlines()]
                self.assertTrue(any(r['event']=='listening' and r['game_port']==game_port for r in rows))
            finally:
                if not task.done():
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError): await task

    async def test_secure_sdk_rejects_unbound_and_accepts_password_selected_identity(self):
        service=Service(self.store,lambda:True,native_auth=self.auth,login_port=0,game_port=0,p2p_port=0)
        await service.start()
        writers=[]
        async def sdk():
            r,w=await asyncio.open_connection('127.0.0.1',service.login_port); writers.append(w)
            msg=bytearray(encode_login(Message(1001,b'opaque-sdk-test'))); msg[6]=1
            w.write(msg); await w.drain()
            return r,w
        async def receive(r):
            while True:
                header=await asyncio.wait_for(r.readexactly(8),3)
                n=struct.unpack_from('<I',header,4)[0]
                msg=GameDecoder().feed(header+await r.readexactly(n))[0]
                if msg.id: return msg
        try:
            r,w=await sdk()
            self.assertEqual(await asyncio.wait_for(r.read(),3),b'')
            self.assertIsNone(service.engine)
            await self.registered()
            session=(await self.auth.login('NewPlayer',PASSWORD))['session']
            ticket=self.auth.select_region(session,1)['ticket']
            self.auth.bind_native_client(ticket,1002,1,123)
            r,w=await sdk()
            _,ack=await read_login(r)
            self.assertIn(b'newplayer',ack)
            self.assertIn(b'1002',ack)
            w.write(encode_login(Message(1011))); await w.drain()
            _,directory=await read_login(r)
            self.assertEqual(struct.unpack_from('<H',directory)[0],1012)
            gr,gw=await asyncio.open_connection('127.0.0.1',service.game_port); writers.append(gw)
            gw.write(encode_game(hello(uid=1002))); await gw.drain()
            self.assertEqual([(await receive(gr)).id for _ in range(6)],[1131,1020,1120,7080,7070,1151])
            gw.write(encode_game(Message(3320,struct.pack('<I',1)))); await gw.drain()
            self.assertEqual([(await receive(gr)).id for _ in range(2)],[3330,1201])
            gw.close(); await gw.wait_closed(); await asyncio.sleep(.03)
            gr,gw=await asyncio.open_connection('127.0.0.1',service.game_port); writers.append(gw)
            gw.write(encode_game(hello(2010,1002))); await gw.drain()
            self.assertEqual((await receive(gr)).id,2030)
            gw.write(encode_game(Message(2250,bytes(8)))); await gw.drain(); await asyncio.sleep(.03)
            self.assertTrue(self.auth.native_status(session,123)['lobby_ready'])
            gw.write(encode_game(Message(2060)));await gw.drain()
            self.assertEqual((await receive(gr)).id,2070)
            self.assertEqual(await asyncio.wait_for(gr.read(),3),b'')
            self.assertFalse(self.auth.native_status(session,123)['lobby_ready'])
            gr,gw=await asyncio.open_connection('127.0.0.1',service.game_port);writers.append(gw)
            gw.write(encode_game(hello(2010,1002)));await gw.drain()
            self.assertEqual((await receive(gr)).id,2030)
            self.auth.logout(session)
            await asyncio.wait_for(gr.read(),3)
        finally:
            for w in writers: w.close()
            await service.close()


@unittest.skipUnless(os.name=='nt','Windows kernel peer attribution')
class WindowsIdentityTests(unittest.TestCase):
    def test_process_and_kernel_tcp_udp_owner(self):
        verifier=WindowsNativeVerifier(sys.executable)
        identity=verifier.process(os.getpid())
        self.assertEqual(identity.pid,os.getpid())
        listener=socket.socket(); client=socket.socket(); udp=socket.socket(socket.AF_INET,socket.SOCK_DGRAM)
        try:
            listener.bind(('127.0.0.1',0)); listener.listen(1)
            client.connect(listener.getsockname()); accepted,peer=listener.accept()
            try: self.assertEqual(verifier.tcp_peer(peer,accepted.getsockname()),identity)
            finally: accepted.close()
            udp.bind(('127.0.0.1',0))
            self.assertEqual(verifier.udp_peer(udp.getsockname()),identity)
            with self.assertRaises(ValueError): WindowsNativeVerifier('wrong-image.exe').process(os.getpid())
        finally: udp.close(); client.close(); listener.close()
