import asyncio
import contextlib
from datetime import datetime,timedelta,timezone
import json
from pathlib import Path
import ssl
import struct
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization,hashes
from cryptography.x509.oid import NameOID
from server.kk_local.store import Store
from server.kk_local.storage.public_access import migrate,PublicAccessRepository,invite_digest
from server.kk_local.public_policy import PublicPolicy,ConnectionBudget,MemoryBudget
from server.kk_local.public_auth import PublicAuthManager
from server.kk_local.public_auth_api import strict_json
from server.kk_local.public_client import ReferenceClient
from server.kk_local.app.public import PublicRuntime
from server.kk_local.wire import Message,ProtocolError

PASSWORD='PublicSynthetic-Password42!'
def certificate(root):
    key=rsa.generate_private_key(public_exponent=65537,key_size=2048);name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')])
    cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(datetime.now(timezone.utc)-timedelta(days=1)).not_valid_after(datetime.now(timezone.utc)+timedelta(days=1))
          .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),critical=False).sign(key,hashes.SHA256()))
    (root/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM));(root/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
def prepare(root,policy=None):
    certificate(root);path=root/'public.sqlite3';store=Store(str(path));store.authentication;migrate(store.db);store.close()
    return SimpleNamespace(database=str(path),events=str(root/'events.jsonl'),auth_certificate=str(root/'cert.pem'),auth_key=str(root/'key.pem'),
                           listen_host='127.0.0.1',advertised_host='127.0.0.1',game_port=0,sdk_port=0,udp_port=0,auth_port=0,health_port=0,public_policy=policy or PublicPolicy())

class PublicUnitTests(unittest.TestCase):
    def test_unsafe_capacity_timeout_and_budget_overrides_refused(self):
        for options in (dict(online=101),dict(tls_seconds=60),dict(pending_connections=1000),dict(hash_wait_seconds=10),dict(outgoing_bytes=1<<40)):
            with self.assertRaises(ValueError):PublicPolicy(**options)
    def test_strict_json(self):
        for data in (b'{"a":1,"a":2}',b'{"x":NaN}',b'['*20+b']'*20):
            with self.assertRaises(ValueError):strict_json(data)
    def test_budget_categories(self):
        b=ConnectionBudget(PublicPolicy(pending_connections=2,pending_per_ip=1));a=b.acquire('one');a.promote(1)
        other=b.acquire('one');another=b.acquire('two')
        with self.assertRaises(ValueError):b.acquire('three')
        self.assertEqual(b.active,1);other.release();another.release();a.release();a.release()
        self.assertEqual((b.pending,b.active,len(b.by_ip),len(b.by_uid)),(0,0,0,0))
        m=MemoryBudget(20);m.set('a',15)
        with self.assertRaises(ProtocolError):m.set('b',10)
        m.release('a');self.assertEqual(m.used,0)
        m=MemoryBudget(100,per_group=20);m.set('queued',15,group=1)
        with self.assertRaises(ProtocolError):m.set('encoding',10,group=1)
        m.set('other',20,group=2);m.release('queued');m.set('encoding',10,group=1)
        self.assertEqual(m.used,30)

class PublicServerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.args=prepare(self.root)
        self.runtime=await PublicRuntime(self.args).start();self.clients=[]
    async def asyncTearDown(self):
        for c in self.clients:await c.close()
        await self.runtime.close();self.temp.cleanup()
    def client(self,ip=None):
        c=ReferenceClient('127.0.0.1',self.runtime.api.port,context=ssl.create_default_context(cafile=str(self.root/'cert.pem')),local_ip=ip);self.clients.append(c);return c
    def invite(self):
        import time
        with self.runtime.store.transaction():return self.runtime.auth.access.create_invite(int(time.time()))
    async def register(self,c,name):return await c.rpc('register',account=name,password=PASSWORD,invite_code=self.invite())
    async def test_reference_client_without_sdk_reaches_lobby_and_udp(self):
        c=self.client();await self.register(c,'PublicOne');uid=await c.login('PublicOne',PASSWORD)
        caps=await c.rpc('capabilities');self.assertEqual(caps['max_online'],100);self.assertFalse(caps['vm_required'])
        self.assertEqual(self.runtime.admission.by_uid[uid].engine.game.phase.value,'lobby')
        self.assertEqual(self.runtime.game.udp_sender.getsockname(),self.runtime.game.udp.get_extra_info('sockname'))
        self.assertFalse(self.runtime.game.udp_sender.getblocking())
        rows=await c.rpc('receipts',session=c.session);self.assertTrue(rows)
        self.assertNotIn(PASSWORD,(self.root/'events.jsonl').read_text())
        await c.rpc('logout',session=c.session);self.assertNotIn(uid,self.runtime.admission.by_uid)
    async def test_invite_is_required_atomic_and_one_use(self):
        c=self.client();code=self.invite()
        with self.assertRaisesRegex(ValueError,'invitation_required'):await c.rpc('register',account='NoInvite',password=PASSWORD)
        results=await asyncio.gather(*(c.rpc('register',account=name,password=PASSWORD,invite_code=code) for name in ('InviteOne','InviteTwo')),return_exceptions=True)
        self.assertEqual(sum(isinstance(r,dict) for r in results),1)
        row=self.runtime.store.db.execute('SELECT used_by FROM public_invites WHERE digest=?',(invite_digest(code),)).fetchone();self.assertIsNotNone(row[0])
        self.assertNotIn(code,str(self.runtime.store.db.execute('SELECT * FROM public_invites').fetchall()))
    async def test_private_api_and_malformed_json_fail(self):
        c=self.client();await self.register(c,'PrivateOne');await c.login('PrivateOne',PASSWORD)
        for op,args in [('bind_client',dict(session=c.session,pid=42)),('receipts',dict(session=c.session,uid=999))]:
            with self.assertRaises(ValueError):await c.rpc(op,**args)
    async def test_existing_task_rules_cannot_enable_public_rewards(self):
        self.assertFalse(self.runtime.admission.hub.permanent_battle_rewards_allowed)
        c=self.client();await self.register(c,'NoRankOne');await c.login('NoRankOne',PASSWORD)
        self.assertIsNotNone(self.runtime.admission.by_uid[c.grant['uid']].engine.public_commands)
    async def test_bad_invite_cannot_consume_valid_registration_budget(self):
        c=self.client()
        for _ in range(20):
            with self.assertRaises(ValueError):await c.rpc('register',account='WrongInvite',password=PASSWORD,invite_code='0'*64)
        self.assertEqual(len(self.runtime.api.register_global.rows),0)

    async def test_duplicate_authorization_and_cross_account_ready_preserve_owner(self):
        a=self.client();b=self.client();await self.register(a,'LeaseOwner');await self.register(b,'OtherOwner')
        await a.login('LeaseOwner',PASSWORD);await b.login('OtherOwner',PASSWORD)
        ticket=await a.rpc('select_region',session=a.session,region_id=1)
        with self.assertRaisesRegex(ValueError,'account_already_online'):await a.rpc('authorize_game',ticket=ticket['ticket'],region_id=1)
        with self.assertRaisesRegex(ValueError,'native_grant_mismatch'):await a.rpc('client_ready',session=a.session,game_credential=b.grant['game_credential'])
        self.assertTrue((await a.rpc('entry_status',session=a.session,uid=a.grant['uid']))['lobby_ready'])
        self.assertTrue((await b.rpc('entry_status',session=b.session,uid=b.grant['uid']))['lobby_ready'])

    async def test_slow_recipient_queue_failure_does_not_evict_another_account(self):
        a=self.client();b=self.client();await self.register(a,'SlowClient');await self.register(b,'GoodClient')
        await a.login('SlowClient',PASSWORD);await b.login('GoodClient',PASSWORD)
        engine=self.runtime.admission.by_uid[a.grant['uid']].engine
        for _ in range(5):engine.enqueue(Message(1550,bytes(600000)))
        self.assertTrue(engine.delivery_failed)
        for _ in range(30):
            if a.grant['uid'] not in self.runtime.admission.by_uid:break
            await asyncio.sleep(.1)
        self.assertNotIn(a.grant['uid'],self.runtime.admission.by_uid)
        self.assertTrue((await b.rpc('entry_status',session=b.session,uid=b.grant['uid']))['lobby_ready'])
        self.assertLessEqual(self.runtime.game.outgoing.used,self.runtime.args.public_policy.outgoing_bytes)

    async def test_udp_kernel_pressure_consumes_nonce_without_retransmission(self):
        from server.kk_local.wire import sdp_reply,sdp_header
        c=self.client();await self.register(c,'KernelBusy');await c.login('KernelBusy',PASSWORD)
        grant=self.runtime.admission.by_uid[c.grant['uid']];service=self.runtime.game
        raw=sdp_reply(1014,c.peer_session,c.peer_id,bytes(4),grant.udp_peer[1])
        sender=service.udp_sender;before=grant.transport_udp.sent
        class Busy:
            def sendto(self,*args):raise BlockingIOError()
        try:
            service.udp_sender=Busy();service.send_udp(raw,grant.udp_peer)
            self.assertEqual(grant.transport_udp.sent,before+1)
            self.assertEqual(service.metrics.counts['udp_kernel_pressure_drop'],1)
        finally:service.udp_sender=sender
        service.send_udp(raw,grant.udp_peer)
        packet=await asyncio.wait_for(asyncio.get_running_loop().sock_recv(c.udp,65536),2)
        self.assertEqual(sdp_header(c.udp_records.open(packet))[0],1014)
        self.assertEqual(grant.transport_udp.sent,before+2)
