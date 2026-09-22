import struct
import unittest
from server.kk_local.foster_catalog import FosterPlan,Group,Spawn
from server.kk_local.foster import positions
from server.kk_local.maps import MapCatalog,MapDefinition
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_shared_rooms as fixture
from server.tests.test_local_service import create_room
from server.tests.test_pve import event


def plan():
    box=(-2,-2,-2,2,2,2)
    return FosterPlan(8110,('a','b'),(8.0,16.0),(((0.,0.,0.),2),((1.,0.,0.),2)),
        (Group((Spawn(0,(0.,0.,0.),2),),box,1,4,100),
         Group((Spawn(1,(5.,0.,0.),2),Spawn(1,(6.,0.,0.),2)),box,1,4,None)),4,('fixture',))


def initial_positions(uids=(1001,1002),points=((0.,0.,0.),(1.,0.,0.))):
    p=bytearray(183);struct.pack_into('<IQ',p,0,20405,1001);p[12:14]=b'\1\1'
    for i,(uid,point) in enumerate(zip(uids,points)):struct.pack_into('<QfffI',p,39+24*i,uid,*point,2)
    return Message(8071,bytes(p))


def spawn(actor,template,point,sequence):
    p=bytearray(event(20400,sequence,actor,template=template).payload)
    struct.pack_into('<fff',p,51,*point)
    return Message(8071,bytes(p))


def damage(room,actor,amount,sequence,sender=1001):
    p=bytearray(94);struct.pack_into('<IQ',p,0,8121,sender);p[12:14]=b'\1\1';struct.pack_into('<I',p,19,sequence)
    struct.pack_into('<QQ',p,39,actor,1002);struct.pack_into('<f',p,67,amount)
    struct.pack_into('<II',p,86,room.number,room.serial)
    return Message(8071,bytes(p))


class FosterTests(unittest.TestCase):
    setUp=fixture.SharedRoomTests.setUp
    tearDown=fixture.SharedRoomTests.tearDown

    def prepare(self):
        maps=MapCatalog({8110:MapDefinition(8110,'fixture',6,'unused','unused',(),())},{10:{8110}})
        maps.foster_plans={8110:plan()};self.e1.map_catalog=self.e2.map_catalog=maps
        p=bytearray(create_room().payload);p[46]=10;struct.pack_into('<II',p,38,8110,8110)
        self.e1.handle(self.c1,Message(3010,bytes(p)));self.e2.handle(self.c2,Message(3070,struct.pack('<HB11s',1,0,b'')))
        self.e1.take_pending(self.c1);self.e2.handle(self.c2,Message(4030));self.e1.take_pending(self.c1)
        self.e1.handle(self.c1,Message(4030));self.e2.take_pending(self.c2)

    def battle(self):
        self.prepare();self.e1.handle(self.c1,initial_positions())
        self.e1.handle(self.c1,Message(4160));self.e2.take_pending(self.c2)
        self.e2.handle(self.c2,Message(4160));self.e1.take_pending(self.c1)
        self.e1.handle(self.c1,Message(8040,struct.pack('<HQI',1,1001,0)))
        self.e2.handle(self.c2,Message(8040,struct.pack('<HQI',1,1002,0)));self.e1.take_pending(self.c1)

    def deliver(self,m):
        self.e1.handle(self.c1,m)
        return self.e2.take_pending(self.c2)

    def finish_event(self,sequence=99):
        p=bytearray(event(20407,sequence).payload);struct.pack_into('<II',p,39,1,self.e1.room.serial)
        return Message(8071,bytes(p))

    def report(self,reason=1,hp=100):
        p=bytearray(696)
        for slot,uid in enumerate((1001,1002)):
            at=87*slot;struct.pack_into('<HH',p,at,100,hp);struct.pack_into('<Q',p,at+29,uid)
            struct.pack_into('<HII',p,at+65,reason,1,self.e1.room.serial)
        return Message(4110,bytes(p))

    def test_position_snapshot_roster_not_slot_order_and_delivery_barrier(self):
        self.prepare();msg=initial_positions((1002,1001));self.e1.handle(self.c1,msg)
        self.assertEqual(self.e2.take_pending(self.c2),[])
        self.e1.handle(self.c1,Message(4160));self.e2.take_pending(self.c2)
        out=self.e2.handle(self.c2,Message(4160));self.assertEqual([m.id for m in out],[4170,8071,4180])
        self.assertEqual(out[1],msg);self.assertEqual([m.id for m in self.e1.take_pending(self.c1)],[4170,4180])
        self.assertTrue(all(self.e1.room.pve.triggered))

    def test_missing_or_invalid_positions_cannot_open_input(self):
        self.prepare()
        for msg in (initial_positions((1001,1001)),initial_positions((1001,9999)),initial_positions(points=((99.,0.,0.),(1.,0.,0.)))):
            with self.assertRaises(ProtocolError):self.e1.handle(self.c1,msg)
        self.e1.handle(self.c1,Message(4160));self.e2.take_pending(self.c2)
        out=self.e2.handle(self.c2,Message(4160))
        self.assertEqual([m.id for m in out],[4170]);self.assertEqual(self.e1.room.stage,'loading')

    def test_changed_and_post_loading_positions_do_not_teleport(self):
        self.prepare();self.e1.handle(self.c1,initial_positions())
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,initial_positions((1002,1001)))
        p=bytearray(initial_positions().payload);struct.pack_into('<I',p,19,3)
        self.assertEqual(self.e1.handle(self.c1,Message(8071,bytes(p))),[])

    def test_groups_advance_independently_and_living_limit_excludes_corpse(self):
        self.battle();state=self.e1.room.pve
        b=spawn(201,1,(5.,0.,0.),1);self.assertEqual(self.deliver(b),[b])
        a=spawn(200,0,(0.,0.,0.),2);self.assertEqual(self.deliver(a),[a])
        self.assertEqual(self.deliver(spawn(202,1,(6.,0.,0.),3)),[])
        hit=damage(self.e1.room,201,16,4);self.assertEqual(self.deliver(hit),[hit])
        self.assertEqual(self.deliver(hit),[]);self.assertEqual(state.actors[201]['hp'],0)
        next_spawn=spawn(202,1,(6.,0.,0.),5)
        self.assertEqual(self.deliver(next_spawn),[next_spawn])
        self.assertEqual(state.spawned,[1,2]);self.assertEqual(sum(a['active'] for a in state.actors.values()),3)

    def test_wrong_spawn_location_template_and_nonhost_damage_rejected(self):
        self.battle()
        self.assertEqual(self.deliver(spawn(200,0,(9.,0.,0.),1)),[])
        self.assertEqual(self.deliver(spawn(200,1,(0.,0.,0.),2)),[])
        self.deliver(spawn(200,0,(0.,0.,0.),3));state=self.e1.room.pve
        self.e2.handle(self.c2,damage(self.e1.room,200,8,4,sender=1002))
        self.assertEqual(state.actors[200]['hp'],8)

    def test_remove_alive_is_not_completed_and_finish_requires_full_receipts(self):
        self.battle();self.deliver(spawn(200,0,(0.,0.,0.),1));self.deliver(event(20401,2,actor=200))
        self.assertEqual(self.e1.room.pve.retired,[0,0]);self.deliver(self.finish_event())
        self.assertEqual(self.e1.handle(self.c1,self.report()),[])

    def test_clear_with_dead_pending_corpses_zero_awards_and_no_mode21_fields(self):
        self.battle();room=self.e1.room;before=self.s.snapshot(1001)
        for m in (spawn(200,0,(0.,0.,0.),1),spawn(201,1,(5.,0.,0.),2),damage(room,200,8,3),damage(room,201,16,4),
                  event(20401,5,actor=200),spawn(202,1,(6.,0.,0.),6),damage(room,202,16,7)):
            self.deliver(m)
        self.deliver(self.finish_event());self.assertTrue(room.pve.complete())
        result=self.e1.handle(self.c1,self.report())[0]
        self.assertEqual((result.id,len(result.payload)),(4120,1000));self.assertEqual(result.payload[92:104],bytes(12))
        self.assertEqual(before,self.s.snapshot(1001));self.assertEqual(self.e1.handle(self.c1,self.report()),[])

    def test_trigger_observation_and_hp_projection_over_sdp_not_double_charged(self):
        from server.kk_local.sdp_peer import SdpPeerRouter
        from server.kk_local.wire import encode_game
        from types import SimpleNamespace
        self.battle();router=SdpPeerRouter(self.h);targets=[SimpleNamespace(engine=self.e2)]
        self.deliver(spawn(200,0,(0.,0.,0.),1));hit=damage(self.e1.room,200,3,2)
        self.e1.enqueue(Message(999));router.observe_selection(self.e1,targets,encode_game(hit))
        self.assertEqual(self.e1.room.pve.actors[200]['hp'],5);self.assertEqual(self.e1.take_pending(self.c1),[Message(999)])
        self.assertEqual(self.deliver(hit),[]);self.assertEqual(self.e1.room.pve.actors[200]['hp'],5)

    def test_positions_parser_rejects_nonzero_empty_and_nonfinite(self):
        p=bytearray(initial_positions().payload);p[-1]=1
        with self.assertRaises(ProtocolError):positions(p,{1001,1002})
        p=bytearray(initial_positions().payload);struct.pack_into('<f',p,47,float('inf'))
        with self.assertRaises(ProtocolError):positions(p,{1001,1002})

    def test_healing_cap_and_corpse_global_capacity(self):
        from dataclasses import replace
        self.battle();state=self.e1.room.pve;state.plan=replace(state.plan,global_limit=2)
        self.deliver(spawn(200,0,(0.,0.,0.),1));self.deliver(spawn(201,1,(5.,0.,0.),2))
        self.deliver(damage(self.e1.room,201,5,3));self.assertEqual(state.actors[201]['hp'],11)
        self.deliver(damage(self.e1.room,201,-100,4));self.assertEqual(state.actors[201]['hp'],16)
        self.deliver(damage(self.e1.room,201,99,5));self.assertEqual(state.actors[201]['hp'],0)
        self.assertEqual(self.deliver(spawn(202,1,(6.,0.,0.),6)),[])
        self.deliver(event(20401,7,actor=201))
        m=spawn(202,1,(6.,0.,0.),8);self.assertEqual(self.deliver(m),[m])

    def test_unqualified_source_versions_never_compile_a_fallback_plan(self):
        from server.kk_local.foster_catalog import compile_plan
        with self.assertRaises(ValueError):compile_plan(b'unknown',b'os.execute("bad")',b'unknown')
