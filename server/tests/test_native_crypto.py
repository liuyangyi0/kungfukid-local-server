"""Authenticated transport tests; synthetic peers, never original-client proof."""
import asyncio
import secrets
import socket
import struct
import unittest
from unittest.mock import patch
from server.kk_local.auth import AuthManager
from server.kk_local.store import Store
from server.kk_local.native_admission import NativeAdmission
from server.kk_local.native_service import NativeService
from server.kk_local.native_auth_api import NativeAuthAPI
from server.kk_local.native_crypto import Records,RecordReader,RecordWriter,RecordError,HEADER,SDK,GAME,UDP,LIMIT
from server.kk_local.wire import Message,GameDecoder,encode_game,encode_login,read_login,sdp_header
from server.tests.test_native_cloud import REGIONS,PASSWORD,fake_hash,login_message,udp_login,udp_packet

class RecordTests(unittest.TestCase):
    def pair(self,channel=GAME):
        args=(bytes([2])*32,bytes([1])*16,b'\x07'+bytes(15),channel)
        return Records(*args,server=False),Records(*args,server=True)
    def test_cng_cross_language_vector_and_bidirectional(self):
        c,s=self.pair();wire=c.seal(b'hello')
        self.assertEqual(wire.hex(),'4b4b45310200000001010101010101010101010101010101070000000000000000000000000000000000000000000000000000053dcdc8f99c4e85d21acce30965bdb1402329d04390')
        self.assertEqual(s.open(wire),b'hello');self.assertEqual(c.open(s.seal(b'reply')),b'reply')
    def test_tamper_every_byte_and_no_replay_state_poison(self):
        c,s=self.pair();wire=c.seal(b'bounded-secret')
        for at in range(len(wire)):
            bad=bytearray(wire);bad[at]^=1
            with self.assertRaises(RecordError):s.open(bytes(bad))
        self.assertEqual(s.open(wire),b'bounded-secret')
        with self.assertRaises(RecordError):s.open(wire)
    def test_channel_direction_connection_and_key_separation(self):
        c,s=self.pair();wire=c.seal(b'x')
        for args in ((bytes(32),s.sid,s.cid,GAME),(bytes([2])*32,s.sid,bytes(16),GAME),(bytes([2])*32,s.sid,s.cid,SDK)):
            with self.assertRaises(RecordError):Records(*args,server=True).open(wire)
        with self.assertRaises(RecordError):c.open(wire)
    def test_tcp_order_udp_reorder_and_window(self):
        c,s=self.pair();a,b=c.seal(b'a'),c.seal(b'b')
        with self.assertRaises(RecordError):s.open(b)
        self.assertEqual(s.open(a),b'a');self.assertEqual(s.open(b),b'b')
        c,s=self.pair(UDP);packets=[c.seal(str(i).encode()) for i in range(1030)]
        self.assertEqual(s.open(packets[1028]),b'1028');self.assertEqual(s.open(packets[1027]),b'1027')
        with self.assertRaises(RecordError):s.open(packets[1027])
        with self.assertRaises(RecordError):s.open(packets[4])
        self.assertEqual(s.open(packets[5]),b'5')
    def test_length_and_key_exhaustion(self):
        c,s=self.pair();wire=c.seal(b'payload')
        for bad in (b'plain',wire[:-1],wire+b'\0'):
            with self.assertRaises(RecordError):s.open(bad)
        c.sent=LIMIT
        with self.assertRaises(RecordError):c.seal(b'x')

class EncryptedNativeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store=Store(':memory:');self.auth=AuthManager(self.store,REGIONS)
        self.hashing=patch.object(self.auth,'_hash',side_effect=fake_hash);self.hashing.start()
        self.admission=NativeAdmission(self.auth,host='127.0.0.1',game_port=0,udp_port=0)
        self.api=NativeAuthAPI(self.admission);self.events=[]
        self.service=await NativeService(self.admission,event_sink=self.events.append).start();self.clients=[]
    async def asyncTearDown(self):
        for w in self.clients:w.close()
        await self.service.close();self.hashing.stop();self.auth.close();self.store.close()
    async def call(self,op,**args):return await self.api.dispatch(dict(schema='kk-local-auth-v1',operation=op,arguments=args))
    async def grant(self,name):
        await self.call('register',account=name,password=PASSWORD)
        session=(await self.call('login',account=name,password=PASSWORD))['session']
        ticket=(await self.call('select_region',session=session,region_id=1))['ticket']
        g=await self.call('authorize_game',ticket=ticket,region_id=1);g.update(account=name.lower(),session=session);return g
    def records(self,g,channel,cid=None):return Records(bytes.fromhex(g['transport_key']),bytes.fromhex(g['transport_id']),cid if cid is not None else secrets.token_bytes(16),channel,server=False)
    async def connect(self,g,channel,cid=None):
        port=self.service.sdk_port if channel==SDK else self.service.game_port
        r,w=await asyncio.open_connection('127.0.0.1',port);self.clients.append(w)
        records=self.records(g,channel,cid)
        return RecordReader(r,records),RecordWriter(w,records),GameDecoder()
    async def until(self,c,ident):
        async with asyncio.timeout(3):
            while True:
                data=await c[0].read(65536)
                if not data:raise AssertionError('encrypted EOF')
                for m in c[2].feed(data):
                    if m.id==ident:return m
    async def sdk(self,g):
        c=await self.connect(g,SDK);raw=bytearray(encode_login(Message(1001,b'opaque-fixture')));raw[6]=1
        c[1].write(b'KKS1'+bytes.fromhex(g['sdk_credential'])+raw);await c[1].drain()
        self.assertEqual(struct.unpack_from('<H',(await asyncio.wait_for(read_login(c[0]),2))[1])[0],1002)
        c[1].write(encode_login(Message(1011)));await c[1].drain()
        self.assertEqual(struct.unpack_from('<H',(await asyncio.wait_for(read_login(c[0]),2))[1])[0],1012)
    async def lobby(self,g):
        await self.sdk(g);await self.call('client_ready',session=g['session'],game_credential=g['game_credential'])
        c=await self.connect(g,GAME);c[1].write(encode_game(login_message(g)));await c[1].drain();await self.until(c,1151)
        c[1].write(encode_game(Message(3320,struct.pack('<I',1))));await c[1].drain();await self.until(c,1201);c[1].close()
        c=await self.connect(g,GAME);c[1].write(encode_game(login_message(g,2010)));await c[1].drain();await self.until(c,2030)
        c[1].write(encode_game(Message(2250,bytes(8))));await c[1].drain();return c
    async def test_sdk_bootstrap_handoff_lobby_and_udp_are_encrypted(self):
        g=await self.grant('SecureOne');await self.lobby(g)
        sock=socket.socket(socket.AF_INET,socket.SOCK_DGRAM);sock.setblocking(False)
        r=self.records(g,UDP,bytes(16));loop=asyncio.get_running_loop();peer=('127.0.0.1',self.service.udp_port)
        try:
            packet=r.seal(udp_login(g));await loop.sock_sendto(sock,packet,peer)
            wire=await asyncio.wait_for(loop.sock_recv(sock,65536),2);self.assertEqual(wire[:4],b'KKE1')
            reply=r.open(wire);self.assertEqual(sdp_header(reply)[0],1002)
            await loop.sock_sendto(sock,packet,peer)
            with self.assertRaises(asyncio.TimeoutError):await asyncio.wait_for(loop.sock_recv(sock,65536),.1)
            await loop.sock_sendto(sock,udp_login(g),peer)
            with self.assertRaises(asyncio.TimeoutError):await asyncio.wait_for(loop.sock_recv(sock,65536),.1)
            await self.call('logout',session=g['session'])
            await loop.sock_sendto(sock,r.seal(udp_login(g)),peer)
            with self.assertRaises(asyncio.TimeoutError):await asyncio.wait_for(loop.sock_recv(sock,65536),.1)
        finally:sock.close()
        for field in ('transport_key','game_credential','udp_credential','sdk_credential'):
            self.assertNotIn(g[field],str(self.events))
    async def test_plaintext_and_cross_account_inner_credentials_rejected(self):
        a,b=await self.grant('SecureOne'),await self.grant('SecureTwo');await self.sdk(b)
        c=await self.connect(a,GAME);c[1].write(encode_game(login_message(b)));await c[1].drain()
        self.assertEqual(await asyncio.wait_for(c[0].read(1),2),b'')
        self.assertIsNone(self.admission.by_uid[b['uid']].engine)
        r,w=await asyncio.open_connection('127.0.0.1',self.service.game_port);self.clients.append(w)
        w.write(encode_game(login_message(b)));await w.drain();self.assertEqual(await asyncio.wait_for(r.read(1),2),b'')
    async def test_connection_replay_is_not_fresh_login(self):
        g=await self.grant('SecureOne');await self.sdk(g)
        await self.call('client_ready',session=g['session'],game_credential=g['game_credential'])
        cid=secrets.token_bytes(16);c=await self.connect(g,GAME,cid)
        packet=encode_game(login_message(g));c[1].write(packet);await c[1].drain();await self.until(c,1151)
        duplicate=await self.connect(g,GAME,cid);duplicate[1].write(packet);await duplicate[1].drain()
        self.assertEqual(await asyncio.wait_for(duplicate[0].read(1),2),b'')
        self.assertIn(g['uid'],self.admission.by_uid)
    async def test_tcp_fragmentation(self):
        g=await self.grant('SecureOne');r,w=await asyncio.open_connection('127.0.0.1',self.service.sdk_port);self.clients.append(w)
        state=self.records(g,SDK);reader=RecordReader(r,state)
        raw=bytearray(encode_login(Message(1001,b'fragmented')));raw[6]=1
        packet=state.seal(b'KKS1'+bytes.fromhex(g['sdk_credential'])+raw)
        for at in range(0,len(packet),3):w.write(packet[at:at+3]);await w.drain();await asyncio.sleep(0)
        self.assertEqual(struct.unpack_from('<H',(await asyncio.wait_for(read_login(reader),2))[1])[0],1002)
