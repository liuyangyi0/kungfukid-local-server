"""Synthetic original-wire integration. Not an original-client or SDK proof."""
import asyncio
import ast
import hashlib
from pathlib import Path
import socket
import struct
import json
import ssl
import tempfile
from datetime import datetime,timedelta,timezone
import unittest
from unittest.mock import patch

from server.kk_local.auth import AuthManager,AuthError
from server.kk_local.store import Store
from server.kk_local.engine import Phase
from server.kk_local.native_admission import NativeAdmission
from server.kk_local.native_auth_api import NativeAuthAPI
from server.kk_local.native_service import NativeService
from server.kk_local.native_relay import NativeRelay
from server.kk_local.wire import GameDecoder,Message,ProtocolError,encode_game,sdp_header,encode_login,read_login
from server.tests.test_local_service import hello,create_room

PASSWORD='Synthetic-Native-Cloud42'
REGIONS=[dict(id=1,name='Native',host='127.0.0.1',game_port=8001)]

async def fake_hash(encoded,salt):return hashlib.sha256(salt+encoded).digest()

def login_message(grant,ident=1010):
    p=bytearray(hello(ident,grant['uid']).payload);p[17:49]=bytes.fromhex(grant['game_credential'])
    name=grant['account'].encode();p[73:73+len(name)]=name
    return Message(ident,bytes(p))

def udp_packet(ident,*,session=0,source=0,extra=b'',body=b''):
    return struct.pack('<HHIIIIHBB',1,ident,session,0,source,0,0,0,len(extra))+extra+body

def udp_login(grant):
    name=grant['account'].encode('ascii')
    body=struct.pack('>H',len(name))+name+bytes(10)+b'KKN1:'+grant['udp_credential'].encode('ascii')+bytes(60)
    return udp_packet(1001,body=body)


class IsolationTests(unittest.TestCase):
    def test_no_main_import_of_paused_experiment(self):
        root=Path(__file__).resolve().parents[1]
        for path in (root/'kk_local').rglob('*.py'):
            text=path.read_text(encoding='utf-8-sig')
            for node in ast.walk(ast.parse(text)):
                if isinstance(node,ast.ImportFrom):self.assertNotIn('experimental',node.module or '',str(path))
                if isinstance(node,ast.Import):
                    for alias in node.names:self.assertNotIn('experimental',alias.name,str(path))
        for name in ('cloud_server','cloud_transport','cloud_game','cloud_auth','cloud_api','cloud_protocol'):
            self.assertFalse((root/'kk_local'/(name+'.py')).exists())
        self.assertFalse((root/'requirements-cloud.txt').exists())
    def test_native_cli_is_explicit_and_public_address_rejected(self):
        from server.kk_local.app.cli import parse_application
        options=['--mode','native','--enable-native-adapter-testing','--database','d.sqlite3','--events','e.jsonl',
                 '--auth-certificate','certificate.pem','--auth-key','private.pem','--check-config']
        mode,args,check=parse_application(options)
        self.assertEqual(mode,'native');self.assertTrue(check)
        with self.assertRaises(ValueError):parse_application(options+['--advertised-host','203.0.113.1'])


class NativeCloudTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store=Store(':memory:');self.now=1000
        self.auth=AuthManager(self.store,REGIONS,clock=lambda:self.now)
        self.hashing=patch.object(self.auth,'_hash',side_effect=fake_hash);self.hashing.start()
        self.admission=NativeAdmission(self.auth,host='127.0.0.1',game_port=0,udp_port=0)
        self.api=NativeAuthAPI(self.admission)
        self.events=[];self.service=await NativeService(self.admission,event_sink=self.events.append,plaintext_test_only=True).start()
        self.clients=[]
    async def asyncTearDown(self):
        for _,writer,_ in self.clients:writer.close()
        await self.service.close();self.hashing.stop();self.auth.close();self.store.close()
    async def call(self,op,**args):
        return await self.api.dispatch(dict(schema='kk-local-auth-v1',operation=op,arguments=args))
    async def grant(self,name):
        await self.call('register',account=name,password=PASSWORD)
        session=(await self.call('login',account=name,password=PASSWORD))['session']
        ticket=(await self.call('select_region',session=session,region_id=1))['ticket']
        result=await self.call('authorize_game',ticket=ticket,region_id=1)
        result.update(account=name.lower(),session=session)
        return result
    async def sdk(self,grant):
        r,w=await asyncio.open_connection('127.0.0.1',self.service.sdk_port);self.clients.append((r,w,None))
        raw=bytearray(encode_login(Message(1001,b'opaque-fixture')));raw[6]=1
        w.write(b'KKS1'+bytes.fromhex(grant['sdk_credential'])+raw);await w.drain()
        flags,body=await asyncio.wait_for(read_login(r),2)
        self.assertEqual(struct.unpack_from('<H',body)[0],1002)
        n=struct.unpack_from('>H',body,3)[0];self.assertEqual(body[5:5+n].decode(),grant['account'])
        at=5+n;n=struct.unpack_from('>H',body,at)[0];self.assertEqual(body[at+2:at+2+n].decode(),str(grant['uid']))
        w.write(encode_login(Message(1011)));await w.drain()
        self.assertEqual(struct.unpack_from('<H',(await asyncio.wait_for(read_login(r),2))[1])[0],1012)
        return r,w
    async def connect(self):
        r,w=await asyncio.open_connection('127.0.0.1',self.service.game_port);client=(r,w,GameDecoder());self.clients.append(client);return client
    async def until(self,client,ident):
        r,_,decoder=client;ids=[]
        async with asyncio.timeout(3):
            while True:
                data=await r.read(65536)
                if not data:raise AssertionError('unexpected EOF '+str(ids))
                for m in decoder.feed(data):
                    ids.append(m.id)
                    if m.id==ident:return m
    async def send(self,client,message):client[1].write(encode_game(message));await client[1].drain()
    async def lobby(self,grant):
        await self.sdk(grant)
        await self.call('client_ready',session=grant['session'],game_credential=grant['game_credential'])
        boot=await self.connect();await self.send(boot,login_message(grant));profile=await self.until(boot,1151)
        self.assertEqual(len(profile.payload),360+7*68)
        await self.send(boot,Message(3320,struct.pack('<I',1)));await self.until(boot,1201)
        boot[1].close();await boot[1].wait_closed()
        client=await self.connect();await self.send(client,login_message(grant,2010));await self.until(client,2030)
        return client
    def trusted_udp(self,grant,port):
        # Explicit fixture: admission supplied out of band, NOT claimed from UID/IP.
        actual=self.admission.by_uid[grant['uid']]
        body=bytes(141);self.service.relay.handle(actual,udp_packet(1001,body=body),('127.0.0.1',port))
        return actual.engine.p2p
    async def test_raw_lobby_room_start_and_no_foreign_envelope(self):
        a,b=await self.grant('NativeOne'),await self.grant('NativeTwo')
        ca,cb=await self.lobby(a),await self.lobby(b)
        pa,pb=self.trusted_udp(a,41001),self.trusted_udp(b,41002)
        await self.send(ca,Message(1156,struct.pack('<QI',a['uid'],pa['player'])))
        await self.send(cb,Message(1156,struct.pack('<QI',b['uid'],pb['player'])))
        await self.send(ca,create_room());await self.until(ca,3100)
        await self.send(cb,Message(3070,struct.pack('<HB11s',1,0,b'')));await self.until(cb,3100)
        await self.send(cb,Message(4030));await self.until(cb,4050)
        await self.send(ca,Message(4030));await self.until(ca,4080)
        await self.send(ca,Message(4160));await self.until(ca,4170)
        await self.send(cb,Message(4160));await self.until(cb,4180)
        for client,g in ((ca,a),(cb,b)):
            await self.send(client,Message(8040,struct.pack('<HQI',1,g['uid'],0)))
        await self.until(ca,8070)
        self.assertEqual(self.admission.by_uid[a['uid']].engine.game.phase,Phase.BATTLE)
    async def test_invalid_credential_uid_and_build_rejected(self):
        a=await self.grant('NativeOne')
        for offset,value in ((17,99),(0,0),(49,0)):
            client=await self.connect();m=login_message(a);p=bytearray(m.payload);p[offset]^=0xff
            await self.send(client,Message(1010,bytes(p)))
            self.assertEqual(await asyncio.wait_for(client[0].read(1),2),b'')
        self.assertIsNone(self.admission.by_uid[a['uid']].engine)
    async def test_credential_not_uid_or_ip_selects_account(self):
        a,b=await self.grant('NativeOne'),await self.grant('NativeTwo')
        m=login_message(a);p=bytearray(m.payload);p[17:49]=bytes.fromhex(b['game_credential'])
        with self.assertRaises(AuthError):self.admission.resolve(Message(1010,bytes(p)))
        with self.assertRaises(AuthError):await self.call('client_ready',session=b['session'],game_credential=a['game_credential'])
    async def test_raw_udp_default_is_fail_closed(self):
        a=await self.grant('NativeOne');await self.lobby(a)
        sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);sock.setblocking(False)
        try:
            await asyncio.get_running_loop().sock_sendto(sock,udp_packet(1001,body=bytes(141)),('127.0.0.1',self.service.udp_port))
            with self.assertRaises(asyncio.TimeoutError):await asyncio.wait_for(asyncio.get_running_loop().sock_recv(sock,4096),.15)
            self.assertIsNone(self.admission.by_uid[a['uid']].engine.p2p)
        finally:sock.close()
    async def test_logout_revokes_active_connection_and_preserves_inventory(self):
        a=await self.grant('NativeOne');c=await self.lobby(a);before=self.store.snapshot(a['uid'])
        await self.call('logout',session=a['session'])
        async with asyncio.timeout(2):
            while await c[0].read(65536):pass
        self.assertNotIn(a['uid'],self.admission.by_uid);self.assertEqual(self.store.snapshot(a['uid']),before)
    async def test_ticket_replay_expiry_and_duplicate_online(self):
        a=await self.grant('NativeOne');ticket=(await self.call('select_region',session=a['session'],region_id=1))['ticket']
        with self.assertRaises(AuthError):await self.call('authorize_game',ticket=ticket,region_id=1)
        self.now+=121
        with self.assertRaises(AuthError):self.admission.resolve(login_message(a))
    async def test_ready_gate_and_duplicate_bootstrap_do_not_kill_owner(self):
        a=await self.grant('NativeOne');await self.sdk(a);first=await self.connect();await self.send(first,login_message(a));await self.until(first,1120)
        self.assertEqual(self.admission.by_uid[a['uid']].engine.bootstrap.phase,Phase.BOOTSTRAP)
        original_peer=self.admission.by_uid[a['uid']].tcp_peer
        second=await self.connect();await self.send(second,login_message(a));self.assertEqual(await asyncio.wait_for(second[0].read(1),2),b'')
        self.assertEqual(self.admission.by_uid[a['uid']].tcp_peer,original_peer)
        await self.call('client_ready',session=a['session'],game_credential=a['game_credential']);await self.until(first,1151)
    async def test_native_pid_binding_is_not_cloud_authorization(self):
        with self.assertRaises(AuthError):await self.call('bind_client',uid=1001,pid=123,ticket='0'*64,region_id=1)
    async def test_fragmented_native_frames_and_prelogin_heartbeat(self):
        a=await self.grant('NativeOne');await self.sdk(a);client=await self.connect()
        await self.send(client,Message(0));raw=encode_game(login_message(a))
        for start in range(0,len(raw),7):client[1].write(raw[start:start+7]);await client[1].drain()
        await self.until(client,1120)
        self.assertEqual(self.admission.by_uid[a['uid']].engine.bootstrap.uid,a['uid'])
    async def test_udp_relay_same_room_only_and_logout_rejection(self):
        a,b,c=await self.grant('NativeOne'),await self.grant('NativeTwo'),await self.grant('NativeThree')
        clients=[await self.lobby(g) for g in (a,b,c)]
        leases=[self.trusted_udp(g,42000+i) for i,g in enumerate((a,b,c))]
        for client,g,p in zip(clients,(a,b,c),leases):
            await self.send(client,Message(1156,struct.pack('<QI',g['uid'],p['player'])))
        await self.send(clients[0],create_room());await self.until(clients[0],3100)
        await self.send(clients[1],Message(3070,struct.pack('<HB11s',1,0,b'')));await self.until(clients[1],3100)
        sent=[];relay=self.service.relay;relay.emit=lambda data,peer:sent.append((data,peer))
        grant=self.admission.by_uid[a['uid']];p=leases[0]
        packet=udp_packet(1008,session=p['session'],source=p['player'],extra=struct.pack('<I',leases[1]['player']),body=b'opaque-peer-data')
        relay.handle(grant,packet,('127.0.0.1',42000))
        self.assertEqual(len(sent),1);self.assertEqual(sdp_header(sent[0][0])[0],1009)
        bad=udp_packet(1008,session=p['session'],source=p['player'],extra=struct.pack('<I',leases[2]['player']),body=b'opaque-peer-data')
        with self.assertRaises(AuthError):relay.handle(grant,bad,('127.0.0.1',42000))
        self.assertEqual(len(sent),1)
    async def test_real_udp_registration_keepalive_and_peer_forward(self):
        a,b=await self.grant('NativeOne'),await self.grant('NativeTwo')
        ca,cb=await self.lobby(a),await self.lobby(b)
        sockets=[socket.socket(socket.AF_INET,socket.SOCK_DGRAM) for _ in range(2)]
        loop=asyncio.get_running_loop();address=('127.0.0.1',self.service.udp_port)
        try:
            for sock in sockets:sock.bind(('127.0.0.1',0));sock.setblocking(False)
            leases=[]
            for sock,g in zip(sockets,(a,b)):
                await loop.sock_sendto(sock,udp_login(g),address)
                reply=await asyncio.wait_for(loop.sock_recv(sock,4096),2)
                ident,session,source,player,body,extra=sdp_header(reply)
                self.assertEqual(ident,1002);leases.append((session,player))
            for client,g,lease in zip((ca,cb),(a,b),leases):
                await self.send(client,Message(1156,struct.pack('<QI',g['uid'],lease[1])))
            await self.send(ca,create_room());await self.until(ca,3100)
            await self.send(cb,Message(3070,struct.pack('<HB11s',1,0,b'')));await self.until(cb,3100)
            packet=udp_packet(1008,session=leases[0][0],source=leases[0][1],extra=struct.pack('<I',leases[1][1]),body=b'fixture-peer-payload')
            await loop.sock_sendto(sockets[0],packet,address)
            result=await asyncio.wait_for(loop.sock_recv(sockets[1],4096),2)
            self.assertEqual(sdp_header(result)[:4],(1009,leases[1][0],leases[0][1],leases[1][1]))
            self.assertTrue(result.endswith(b'fixture-peer-payload'))
            await loop.sock_sendto(sockets[0],udp_packet(1013,session=leases[0][0],source=leases[0][1],body=bytes(4)),address)
            self.assertEqual(sdp_header(await asyncio.wait_for(loop.sock_recv(sockets[0],4096),2))[0],1014)
        finally:
            for sock in sockets:sock.close()
    async def test_tls_account_api_and_no_credentials_in_events(self):
        from cryptography import x509
        from cryptography.x509.oid import NameOID
        from cryptography.hazmat.primitives import hashes,serialization
        from cryptography.hazmat.primitives.asymmetric import rsa
        key=rsa.generate_private_key(public_exponent=65537,key_size=2048)
        name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'localhost')]);now=datetime.now(timezone.utc)
        cert=(x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key())
              .serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=1)).not_valid_after(now+timedelta(hours=1))
              .add_extension(x509.SubjectAlternativeName([x509.DNSName('localhost')]),critical=False).sign(key,hashes.SHA256()))
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'cert.pem').write_bytes(cert.public_bytes(serialization.Encoding.PEM))
            (root/'key.pem').write_bytes(key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption()))
            server_context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);server_context.minimum_version=ssl.TLSVersion.TLSv1_2;server_context.load_cert_chain(root/'cert.pem',root/'key.pem')
            client_context=ssl.create_default_context(cafile=str(root/'cert.pem'))
            events=[];api=await NativeAuthAPI(self.admission,port=0,context=server_context,event_sink=events.append).start()
            async def rpc(op,**args):
                r,w=await asyncio.open_connection('127.0.0.1',api.port,ssl=client_context,server_hostname='localhost')
                try:
                    data=json.dumps(dict(schema='kk-local-auth-v1',operation=op,arguments=args)).encode()
                    w.write(struct.pack('!I',len(data))+data);await w.drain()
                    length=struct.unpack('!I',await r.readexactly(4))[0]
                    return json.loads(await r.readexactly(length))
                finally:w.close();await w.wait_closed()
            try:
                self.assertTrue((await rpc('register',account='TlsNative',password=PASSWORD))['ok'])
                result=await rpc('login',account='TlsNative',password=PASSWORD);token=result['result']['session']
                ticket=(await rpc('select_region',session=token,region_id=1))['result']['ticket']
                grant=(await rpc('authorize_game',ticket=ticket,region_id=1))['result']
                self.assertTrue((await rpc('client_ready',session=token,game_credential=grant['game_credential']))['ok'])
                log=json.dumps(events)
                for secret in (PASSWORD,token,ticket,grant['game_credential'],grant['udp_credential'],grant['sdk_credential']):self.assertNotIn(secret,log)
            finally:await api.close()
    async def test_udp_credential_cannot_be_replaced_by_tcp_ticket_or_other_account(self):
        a,b=await self.grant('NativeOne'),await self.grant('NativeTwo')
        await self.lobby(a);await self.lobby(b)
        for candidate in ({**a,'udp_credential':a['game_credential']},{**a,'udp_credential':b['udp_credential']}):
            with self.assertRaises(AuthError):self.admission.resolve_udp(udp_login(candidate),('127.0.0.1',42010))
        with self.assertRaises(AuthError):self.admission.resolve_udp(udp_login(a),('203.0.113.1',42010))
        await self.call('logout',session=a['session'])
        with self.assertRaises(AuthError):self.admission.resolve_udp(udp_login(a),('127.0.0.1',42010))
    async def test_sdk_requires_independent_ticket_before_game(self):
        a=await self.grant('NativeOne')
        with self.assertRaisesRegex(AuthError,'sdk_authentication_required'):self.admission.resolve(login_message(a))
        r,w=await asyncio.open_connection('127.0.0.1',self.service.sdk_port)
        try:
            w.write(b'KKS1'+bytes.fromhex(a['game_credential']));await w.drain()
            self.assertEqual(await asyncio.wait_for(r.read(1),2),b'')
        finally:w.close();await w.wait_closed()
        await self.sdk(a)
        self.assertEqual((await self.call('entry_status',session=a['session'],uid=a['uid']))['sdk_admitted'],True)
    async def test_entry_confirmation_waits_for_actual_lobby_request_and_cancel_revokes(self):
        a=await self.grant('NativeOne');client=await self.lobby(a)
        self.assertFalse((await self.call('entry_status',session=a['session'],uid=a['uid']))['lobby_ready'])
        await self.send(client,Message(2250,bytes(8)))
        async with asyncio.timeout(2):
            while not (await self.call('entry_status',session=a['session'],uid=a['uid']))['lobby_ready']:await asyncio.sleep(.01)
        await self.call('cancel_game',session=a['session'],game_credential=a['game_credential'])
        with self.assertRaises(AuthError):await self.call('entry_status',session=a['session'],uid=a['uid'])
        self.assertEqual(self.admission.grants,{})
