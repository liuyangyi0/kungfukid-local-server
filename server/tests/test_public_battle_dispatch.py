"""One shared combat interpreter for encrypted UDP and TCP fallback."""
import struct
import asyncio
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
import xml.etree.ElementTree as ET
from server.kk_local.engine import Engine
from server.kk_local.rooms import Member
from server.kk_local.public_commands import PublicCommands
from server.kk_local.public_policy import PublicPolicy
from server.kk_local.combat_catalog import CombatCatalog
from server.kk_local.native_relay import NativeRelay
from server.kk_local.wire import Message,GameDecoder,ProtocolError,encode_game,sdp_header
from server.tests import test_shared_rooms as room_fixtures
from server.tests.test_shared_rooms import lobby
from server.tests.test_battle_effect_relay import effect
from server.tests.test_projectile_protocol import projectile
from server.tests import test_hit_receipt_flow as hit_fixtures
from server.tests import test_public_server as server_fixtures


class PublicBattleDispatchTests(unittest.TestCase):
    setUp=room_fixtures.SharedRoomTests.setUp
    tearDown=room_fixtures.SharedRoomTests.tearDown
    join=room_fixtures.SharedRoomTests.join
    battle=room_fixtures.SharedRoomTests.battle
    def prepare(self):
        self.battle();self.h.public_policy=PublicPolicy();self.h.permanent_battle_rewards_allowed=False
        self.engines={1001:self.e1,1002:self.e2};self.grants={};self.sent=[]
        self.admission=SimpleNamespace(hub=self.h,public_policy=self.h.public_policy,by_player={},validate=lambda g:g)
        self.relay=NativeRelay(self.admission,emit=lambda data,peer:self.sent.append((data,peer)))
        self.h.combat_catalog=CombatCatalog.from_xml(ET.fromstring('<SkillProperty><PropertyItem SkillProId="811117" DefenceTear="1"/><PropertyItem SkillProId="811116"><LogicEffects><AttackerUstate/></LogicEffects></PropertyItem></SkillProperty>'))
        for e in self.engines.values():self.install(e)
    def install(self,e):
        e.public_commands=PublicCommands(self.h.public_policy);e.p2p['confirmed']=True
        g=SimpleNamespace(engine=e,uid=e.account_uid,credential_digest=str(e.account_uid).encode(),udp_peer=e.p2p['peer'])
        self.grants[g.uid]=g;self.admission.by_player[e.p2p['player']]=g;self.relay.peers[g.udp_peer]=g
    def third(self):
        self.s.provision_local(1003,'Third');e=Engine(self.s,hub=self.h,account_uid=1003);c=lobby(e,5)
        e.room=self.e1.room;e.room.members[1003]=Member(e,2,0);c.phase=self.c1.phase;self.engines[1003]=e;self.install(e);return e
    def udp(self,m,uid=1001,targets=(1002,)):
        e=self.engines[uid];extra=struct.pack('<'+'I'*len(targets),*(self.engines[u].p2p['player'] for u in targets))
        raw=struct.pack('<HHIIIIHBB',1,1008,e.p2p['session'],0,e.p2p['player'],0,0,0,len(extra))+extra+encode_game(m)
        self.relay.handle(self.grants[uid],raw,self.grants[uid].udp_peer)
    def drain(self):
        rows=[]
        for raw,peer in self.sent:
            decoder=GameDecoder();rows.extend((peer,m) for m in decoder.feed(sdp_header(raw)[4]));decoder.eof()
        self.sent=[];return rows
    def hit(self,seq=0):
        p=bytearray(effect(8121,self.e1.room,seq=seq).payload);struct.pack_into('<I',p,56,811117);p[85]=2;p[65]=1
        return Message(8071,bytes(p))
    def receipt(self,seq=1):return hit_fixtures.HitReceiptFlowTests.receipt(self,seq=seq,skill=811117,status=4)

    def test_udp_guard_then_tcp_break_receipt_no_double_application(self):
        self.prepare();before=self.s.snapshot(1001);hit=self.hit();self.udp(hit);self.assertEqual(self.drain()[0][1],hit)
        receipt=self.receipt();self.assertEqual(self.e1.handle(self.c1,receipt),[])
        self.assertEqual(self.e2.take_pending(self.c2),[receipt]);self.udp(receipt);self.assertEqual(self.drain(),[])
        self.assertEqual(self.e1.room.pending_hit_receipts[1001],[]);self.assertEqual(before,self.s.snapshot(1001))
    def test_tcp_guard_then_udp_receipt_requires_witness_and_catalog(self):
        self.prepare();receipt=self.receipt();self.udp(receipt);self.assertEqual(self.drain(),[])
        self.e1.handle(self.c1,self.hit());self.e2.take_pending(self.c2)
        self.udp(receipt);self.assertEqual(self.drain()[0][1],receipt)
        self.udp(receipt);self.assertEqual(self.drain(),[])
    def test_absent_or_attacker_effect_catalog_never_opens_receipt(self):
        self.prepare()
        attacker=CombatCatalog.from_xml(ET.fromstring('<SkillProperty><PropertyItem SkillProId="811117"><LogicEffects><AttackerUstate/></LogicEffects></PropertyItem></SkillProperty>'))
        for i,catalog in enumerate((None,CombatCatalog(frozenset()),attacker)):
            self.h.combat_catalog=catalog
            self.udp(self.hit(seq=2*i));self.drain();self.udp(self.receipt(seq=2*i+1));self.assertEqual(self.drain(),[])
    def test_peer_by_peer_same_event_delivers_each_once_tcp_fallback_fills_remaining(self):
        self.prepare();third=self.third();m=effect(8150,self.e1.room,source=1001)
        self.udp(m,targets=(1002,));self.assertEqual(len(self.drain()),1)
        self.udp(m,targets=(1002,));self.assertEqual(self.drain(),[])
        self.e1.handle(self.c1,m);self.assertEqual(self.e2.take_pending(self.c2),[]);self.assertEqual(third.take_pending(third.game),[m])
        self.assertEqual(len(self.e1.room.active_states),1)
    def test_udp_preserves_unrelated_tcp_pending_and_restores_capture(self):
        self.prepare();unrelated=Message(8090,bytes(4));self.e2.enqueue(unrelated)
        self.udp(self.hit());self.assertEqual(len(self.drain()),1)
        self.assertEqual(self.e2.take_pending(self.c2),[unrelated]);self.assertIsNone(self.e1.battle_delivery_capture)
        with self.assertRaises(ProtocolError):self.udp(projectile(8400,self.e1.room,sender=1001,source=9999,seq=1))
        self.assertIsNone(self.e2.battle_delivery_capture)
    def test_cancel_prevents_delayed_apply_delivery_to_new_recipient(self):
        self.prepare();self.third();room=self.e1.room
        applied=effect(8150,room,sender=1002,target=1002,source=1002)
        self.udp(applied,1002,(1001,));self.drain()
        cancel=effect(8150,room,target=1002,source=0,operation=0)
        self.udp(cancel,1001,(1002,1003));self.drain()
        self.udp(applied,1002,(1003,));self.assertEqual(self.drain(),[]);self.assertEqual(room.active_states,{})
    def test_udp_projectile_lifecycle_and_cross_owner_refusal(self):
        self.prepare();room=self.e1.room
        for seq,ident in enumerate((8400,8403,8401,8404)):
            m=projectile(ident,room,seq=seq);self.udp(m);self.assertEqual(self.drain()[0][1],m)
            self.udp(m);self.assertEqual(self.drain(),[])
        self.assertFalse(room.projectiles[10000]['alive'])
        self.udp(projectile(8400,room,seq=4));self.assertEqual(self.drain(),[])
    def test_grab_selection_is_armed_only_when_target_is_a_recipient(self):
        from server.tests.test_pair_transform import selection,transform
        self.prepare();third=self.third();m=selection()
        self.udp(m,targets=(1003,));self.drain();self.assertEqual(self.e1.room.pair_selections,{})
        self.udp(transform(),1002,(1001,));self.assertEqual(self.drain(),[])
        self.udp(m,targets=(1002,));self.drain();self.assertIn(1001,self.e1.room.pair_selections)
        self.udp(transform(),1002,(1001,1003));self.assertEqual(len(self.drain()),2)
        self.assertEqual(third.take_pending(third.game),[])
    def test_nonhost_cannot_use_udp_to_bypass_spawn_authority(self):
        from server.tests.test_collectible_spawn import spawn
        self.prepare()
        with self.assertRaises(ProtocolError):self.udp(spawn(sender=1002),1002,(1001,))
        self.assertEqual(self.e1.room.spawned_collectibles,{})
        self.udp(spawn());self.assertEqual(len(self.drain()),1)
    def test_cache_bounded_and_room_leave_clears(self):
        self.prepare()
        for seq in range(150):
            self.udp(effect(8150,self.e1.room,seq=seq,source=1001));self.drain()
        room=self.e1.room;self.assertLessEqual(len(room.battle_dispatch_cache),128)
        self.h.leave(self.e2);self.assertEqual(len(room.battle_dispatch_cache),0)
    def test_owned_action1013_notification_8288(self):
        self.prepare();p=bytearray(55);struct.pack_into('<IQ',p,0,8288,1001);p[12:14]=b'\1\1';struct.pack_into('<Q',p,39,1001)
        m=Message(8071,bytes(p));self.udp(m);self.assertEqual(self.drain()[0][1],m)
        struct.pack_into('<Q',p,39,1002)
        with self.assertRaises(ProtocolError):self.udp(Message(8071,bytes(p)))
    def test_server_xml_is_bounded_and_declarations_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'skills.xml';path.write_text('<SkillProperty><PropertyItem SkillProId="1" DefenceTear="1"/></SkillProperty>')
            self.assertEqual(CombatCatalog.from_file(path).receipt_outcomes(1,2,1),(2,4))
            path.write_text('<!DOCTYPE x [<!ENTITY x "a">]><SkillProperty/>')
            with self.assertRaises(ValueError):CombatCatalog.from_file(path)


class PublicBattleSocketTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp=server_fixtures.PublicServerTests.asyncSetUp
    asyncTearDown=server_fixtures.PublicServerTests.asyncTearDown
    client=server_fixtures.PublicServerTests.client
    invite=server_fixtures.PublicServerTests.invite
    register=server_fixtures.PublicServerTests.register
    async def test_encrypted_two_player_udp_guard_tcp_break_and_buff_cancel(self):
        a,b=self.client(),self.client();await self.register(a,'BattleOne');await self.register(b,'BattleTwo')
        await a.login('BattleOne',server_fixtures.PASSWORD);await b.login('BattleTwo',server_fixtures.PASSWORD)
        a.start_pump();b.start_pump()
        self.runtime.admission.hub.combat_catalog=CombatCatalog(frozenset((811117,)),guard_break_ids=(811117,))
        p=bytearray(81);p[:4]=b'Test';p[37]=2;p[46]=1;struct.pack_into('<H',p,47,180)
        await a.send(Message(3010,bytes(p)));entry=await a.read_until(3100);number=struct.unpack_from('<H',entry.payload)[0]
        await b.send(Message(3070,struct.pack('<HB11s',number,0,b'')));await b.read_until(3100)
        await b.send(Message(4030));await b.read_until(4050);await a.send(Message(4030))
        await asyncio.gather(a.read_until(4080),b.read_until(4080))
        for c in (a,b):await c.send(Message(4160))
        await asyncio.gather(a.read_until(4180),b.read_until(4180))
        for c in (a,b):await c.send(Message(8040,struct.pack('<HQI',number,c.grant['uid'],0)))
        await asyncio.gather(a.read_until(8070),b.read_until(8070))
        room=self.runtime.admission.by_uid[a.grant['uid']].engine.room;ua,ub=a.grant['uid'],b.grant['uid']
        hit=bytearray(effect(8121,room,sender=ua,target=ua,source=ub).payload);struct.pack_into('<I',hit,56,811117);hit[85]=2;hit[65]=1
        async def send_udp(c,target,m):await c.send_udp(c.sdp(1008,encode_game(m),(target.peer_id,)))
        async def receive_udp(c):
            raw=await asyncio.wait_for(asyncio.get_running_loop().sock_recv(c.udp,65536),3)
            decoder=GameDecoder();rows=decoder.feed(sdp_header(c.udp_records.open(raw))[4]);decoder.eof();return rows
        await send_udp(a,b,Message(8071,bytes(hit)));self.assertEqual(await receive_udp(b),[Message(8071,bytes(hit))])
        receipt=bytearray(71);struct.pack_into('<IQ',receipt,0,8126,ua);receipt[12:14]=b'\1\1';struct.pack_into('<I',receipt,19,1);struct.pack_into('<QQQII',receipt,39,ua,ua,ub,811117,4)
        reply=Message(8071,bytes(receipt));await a.send(reply);self.assertEqual(await b.read_until(8071),reply)
        await send_udp(a,b,reply)
        with self.assertRaises(asyncio.TimeoutError):await asyncio.wait_for(asyncio.get_running_loop().sock_recv(b.udp,65536),.1)
        buff=effect(8150,room,sender=ub,target=ub,source=ub)
        await send_udp(b,a,buff);self.assertEqual(await receive_udp(a),[buff])
        cancel=effect(8150,room,sender=ua,target=ub,source=0,seq=2,operation=0)
        await a.send(cancel);self.assertEqual(await b.read_until(8071),cancel)
        self.assertEqual(room.active_states,{})
