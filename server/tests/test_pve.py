import struct
import unittest
from server.kk_local.engine import Phase
from server.kk_local.maps import MapCatalog,MapDefinition
from server.kk_local.stage_catalog import StagePlan
from server.kk_local.pve import decode
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_shared_rooms as fixture
from server.tests.test_local_service import create_room


def event(ident,sequence=1,actor=200,sender=1001,template=0):
    length={20400:67,20401:47,20403:92,20404:43,20407:47}[ident]
    p=bytearray(length);struct.pack_into('<IQ',p,0,ident,sender);p[12:14]=b'\1\1';struct.pack_into('<I',p,19,sequence)
    if ident in (20400,20401):struct.pack_into('<Q',p,39,actor)
    if ident==20400:struct.pack_into('<IfffI',p,47,template,1.0,2.0,3.0,2)
    if ident==20403:p[39]=1;struct.pack_into('<I',p,40,actor)
    if ident==20404:struct.pack_into('<I',p,39,actor)
    return Message(8071,bytes(p))


class PveTests(unittest.TestCase):
    setUp=fixture.SharedRoomTests.setUp
    tearDown=fixture.SharedRoomTests.tearDown

    def prepare(self):
        plan=StagePlan(9170,('sample',),((1,8,(((0,1),),((0,1),))),),('synthetic',))
        catalog=MapCatalog({9170:MapDefinition(9170,'fixture',8,'unused','unused',(),())},{21:{9170}})
        catalog.stage_plans={9170:plan};self.e1.map_catalog=self.e2.map_catalog=catalog
        request=bytearray(create_room().payload);request[46]=21;struct.pack_into('<II',request,38,9170,9170)
        self.e1.handle(self.c1,Message(3010,bytes(request)))
        self.e2.handle(self.c2,Message(3070,struct.pack('<HB11s',1,0,b'')));self.e1.take_pending(self.c1)
        self.e2.handle(self.c2,Message(4030));self.e1.take_pending(self.c1)
        self.e1.handle(self.c1,Message(4030));self.e2.take_pending(self.c2)

    def battle(self):
        self.prepare();self.e1.handle(self.c1,Message(4160));self.e2.take_pending(self.c2)
        self.e2.handle(self.c2,Message(4160));self.e1.take_pending(self.c1)
        self.e1.handle(self.c1,Message(8040,struct.pack('<HQI',1,1001,0)))
        self.e2.handle(self.c2,Message(8040,struct.pack('<HQI',1,1002,0)));self.e1.take_pending(self.c1)

    def wave(self,n):
        p=bytearray(40);struct.pack_into('<IIiI',p,0,1,self.e1.room.serial,n,1)
        return self.e1.handle(self.c1,Message(20571,bytes(p)))

    def test_first_wave_local_then_quota_and_live_actor_gate_next_wave(self):
        self.battle();self.assertEqual(self.e1.room.pve.index,0)
        self.assertEqual(self.wave(1),[])
        create=event(20400);self.e1.handle(self.c1,create)
        self.assertEqual(self.e2.take_pending(self.c2),[create]);self.assertEqual(self.wave(1),[])
        self.e1.handle(self.c1,create);self.assertEqual(self.e2.take_pending(self.c2),[])
        self.e1.handle(self.c1,event(20400,2,actor=201));self.assertEqual(self.e2.take_pending(self.c2),[])
        self.e1.handle(self.c1,event(20401,3));self.e2.take_pending(self.c2)
        out=self.wave(1);self.assertEqual(out[0].id,20572);self.assertEqual(struct.unpack_from('<i',out[0].payload,8)[0],2)
        self.e2.take_pending(self.c2);self.assertEqual(self.wave(1),[])
        self.e1.handle(self.c1,event(20400,4));self.e2.take_pending(self.c2)
        self.e1.handle(self.c1,event(20401,5));self.e2.take_pending(self.c2)
        self.assertEqual(struct.unpack_from('<i',self.wave(2)[0].payload,8)[0],-1)
        self.assertTrue(self.e1.room.pve.finished);self.e2.take_pending(self.c2)
        self.assertEqual(self.wave(2),[])

    def test_loading_walls_sync_only_final_state_before_4180_and_not_to_host(self):
        self.prepare()
        kept=event(20403,1,actor=9);removed=event(20403,2,actor=10)
        self.e1.handle(self.c1,kept);self.e1.handle(self.c1,removed);self.e1.handle(self.c1,event(20404,3,actor=10))
        self.assertEqual(self.e2.take_pending(self.c2),[])
        self.e1.handle(self.c1,Message(4160));self.e2.take_pending(self.c2)
        out=self.e2.handle(self.c2,Message(4160))
        self.assertEqual([m.id for m in out],[4170,8071,4180]);self.assertEqual(out[1],kept)
        self.assertEqual([m.id for m in self.e1.take_pending(self.c1)],[4170,4180])
        self.assertEqual(self.e2.handle(self.c2,Message(4160)),[])

    def test_bad_templates_player_identity_nonhost_and_removed_actor_rejected(self):
        self.battle()
        for m in (event(20400,1,actor=1002),event(20400,2,template=9)):
            self.e1.handle(self.c1,m);self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(self.e2.handle(self.c2,event(20400,3,sender=1002)),[])
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,event(20400,4,sender=1002))
        self.e1.handle(self.c1,event(20400,5));self.e2.take_pending(self.c2)
        self.e1.handle(self.c1,event(20401,6));self.e2.take_pending(self.c2)
        self.e1.handle(self.c1,event(20400,5));self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertFalse(self.e1.room.pve.active(200))

    def test_host_npc_motion_and_direction_only_after_registration(self):
        self.battle();self.e1.handle(self.c1,event(20400,1));self.e2.take_pending(self.c2)
        p=bytearray(108);struct.pack_into('<IQ',p,0,8120,200);struct.pack_into('<I',p,15,1)
        m=Message(8071,bytes(p));self.e1.handle(self.c1,m);self.assertEqual(self.e2.take_pending(self.c2),[m])
        struct.pack_into('<f',p,51,15.0);m=Message(8071,bytes(p));self.e1.handle(self.c1,m)
        self.assertEqual(self.e2.take_pending(self.c2),[m]);self.e1.handle(self.c1,m);self.assertEqual(self.e2.take_pending(self.c2),[])
        p=bytearray(51);struct.pack_into('<IQ',p,0,8122,1001);p[12:14]=b'\1\1'
        struct.pack_into('<I',p,19,2);struct.pack_into('<QI',p,39,200,7)
        m=Message(8071,bytes(p));self.e1.handle(self.c1,m);self.assertEqual(self.e2.take_pending(self.c2),[m])
        self.e1.handle(self.c1,event(20401,3));self.e2.take_pending(self.c2)
        struct.pack_into('<I',p,19,4);self.e1.handle(self.c1,Message(8071,bytes(p)))
        self.assertEqual(self.e2.take_pending(self.c2),[])

    def report(self,reason,hp):
        p=bytearray(696)
        for slot,uid in enumerate((1001,1002)):
            off=87*slot;struct.pack_into('<HH',p,off,100,hp);struct.pack_into('<Q',p,off+29,uid)
            struct.pack_into('<HII',p,off+65,reason,1,self.e1.room.serial)
        return Message(4110,bytes(p))

    def test_zero_award_failure_result_is_host_only_and_returns_room(self):
        self.battle();before=self.s.snapshot(1001);msg=self.report(2,0)
        self.e1.room.battle_clock_origin=0;self.e1.clock=lambda:20
        self.assertEqual(self.e2.handle(self.c2,msg),[])
        out=self.e1.handle(self.c1,msg);peer=self.e2.take_pending(self.c2)
        self.assertEqual([m.id for m in out],[4120]);self.assertEqual(len(out[0].payload),1000)
        self.assertEqual(struct.unpack_from('<I',out[0].payload,96)[0],20)
        self.assertEqual(struct.unpack_from('<Q',out[0].payload,500)[0],1001)
        self.assertEqual(struct.unpack_from('<Q',peer[0].payload,500)[0],1002)
        self.assertEqual(before,self.s.snapshot(1001));self.assertEqual(self.e1.handle(self.c1,msg),[])
        self.e1.handle(self.c1,Message(4115,bytes(4)));self.e2.handle(self.c2,Message(4115,bytes(4)))
        self.assertEqual(self.e1.room.stage,'room');self.assertIsNone(self.e1.room.pve)

    def test_premature_clear_or_unknown_reason_never_becomes_result(self):
        self.battle()
        for reason,hp in ((1,100),(2,1),(3,0)):
            self.assertEqual(self.e1.handle(self.c1,self.report(reason,hp)),[])
        self.assertEqual(self.e1.room.stage,'battle');self.assertFalse(self.e1.room.result_replies)

    def test_decode_is_strict_and_does_not_alias_mutable_input(self):
        p=bytearray(event(20400).payload);out=decode(p);p[47]=2
        self.assertEqual(out['template'],0)
        for bad in (p[:-1],p+b'x'):
            with self.assertRaises(ProtocolError):decode(bad)
        struct.pack_into('<f',p,51,float('nan'))
        with self.assertRaises(ProtocolError):decode(p)

    def test_udp_observation_records_lifecycle_without_tcp_echo_or_queue_drain(self):
        from server.kk_local.sdp_peer import SdpPeerRouter
        from server.kk_local.wire import encode_game
        from types import SimpleNamespace
        self.battle();router=SdpPeerRouter(self.h);targets=[SimpleNamespace(engine=self.e2)]
        self.e1.enqueue(Message(999))
        create=event(20400,1);router.observe_selection(self.e1,targets,encode_game(create))
        self.assertTrue(self.e1.room.pve.active(200));self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(self.e1.take_pending(self.c1),[Message(999)])
        self.e1.handle(self.c1,create);self.assertEqual(self.e2.take_pending(self.c2),[])
        router.observe_selection(self.e1,targets,encode_game(event(20401,2)))
        self.assertEqual(self.wave(1)[0].id,20572)

    def test_zero_award_clear_requires_every_wave_and_does_not_send_4100(self):
        self.battle();before=[self.s.snapshot(u) for u in (1001,1002)]
        for n in (1,2):
            self.e1.handle(self.c1,event(20400,n*2));self.e2.take_pending(self.c2)
            self.e1.handle(self.c1,event(20401,n*2+1));self.e2.take_pending(self.c2)
            self.wave(n);self.e2.take_pending(self.c2)
        out=self.e1.handle(self.c1,self.report(1,100));self.assertEqual([m.id for m in out],[4120])
        self.assertEqual(out[0].payload[10],1);self.assertEqual(struct.unpack_from('<I',out[0].payload,92)[0],2)
        self.assertEqual(before,[self.s.snapshot(u) for u in (1001,1002)])
        self.assertEqual([m.id for m in self.e2.take_pending(self.c2)],[4120])

    def test_disconnect_aborts_pve_without_results_or_awards(self):
        self.battle();before=self.s.snapshot(1002);self.h.leave(self.e1)
        self.assertEqual(self.e2.take_pending(self.c2),[Message(3115)])
        self.assertIsNone(self.e2.room);self.assertEqual(before,self.s.snapshot(1002))
