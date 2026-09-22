"""Opt-in Mode1 observers: own roster, no8040 vote, no gameplay/reward authority."""
import struct
import unittest
from server.kk_local.engine import Engine,Phase
from server.kk_local.rooms import RoomHub
from server.kk_local.store import Store
from server.kk_local.wire import Message
from server.tests.test_shared_rooms import lobby
from server.tests.test_local_service import create_room


class ObserverRoomTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:');self.s.seed_local()
        for uid in (1002,1003):self.s.provision_local(uid,'User'+str(uid))
        self.h=RoomHub(spectator_capacity=1)
        self.es=[Engine(self.s,hub=self.h,account_uid=u) for u in (1001,1002,1003)]
        self.cs=[lobby(e,10+i*2) for i,e in enumerate(self.es)]
        p=bytearray(create_room().payload);p[37]=2;p[46]=1;p[34]=1
        self.es[0].handle(self.cs[0],Message(3010,bytes(p)))
        self.es[1].handle(self.cs[1],Message(3070,struct.pack('<HB11s',1,0,b'')))
        self.es[0].take_pending(self.cs[0])
        self.entry=self.es[2].handle(self.cs[2],Message(3070,struct.pack('<HB11s',1,1,b'')))
        self.room=self.es[0].room
        self.drain()

    def tearDown(self):self.s.close()

    def drain(self):
        return [e.take_pending(c) for e,c in zip(self.es,self.cs)]

    def start(self):
        self.es[1].handle(self.cs[1],Message(4030));self.drain()
        out=self.es[0].handle(self.cs[0],Message(4030));self.drain()
        self.assertEqual(self.room.stage,'loading')
        start=next(m for m in out if m.id==4080)
        self.assertEqual(struct.unpack_from('<I',start.payload,13+2*4)[0],0)
        for e,c in zip(self.es,self.cs):e.handle(c,Message(4160))
        self.drain();self.assertEqual(self.room.stage,'wait_ready')
        for e,c in zip(self.es[:2],self.cs[:2]):
            e.handle(c,Message(8040,struct.pack('<HQI',1,c.uid,0)))
        self.drain();self.assertEqual(self.room.stage,'battle')

    def report(self):
        p=bytearray(696)
        for uid,m in self.room.fighters.items():
            o=m.slot*87;struct.pack_into('<HH',p,o,100,100 if uid==1001 else 0)
            struct.pack_into('<Q',p,o+29,uid)
            struct.pack_into('<II',p,o+67,self.room.number,self.room.serial)
        return Message(4110,bytes(p))

    def test_native_observer_entry_and_directory_separate_capacities(self):
        p=self.entry[0].payload
        self.assertEqual((p[10],p[64],p[96+76]),(8,1,1))
        self.assertEqual(set(self.room.fighters),{1001,1002})
        self.assertTrue(self.room.members[1003].spectator)
        # Calling the directory builder is public wire verification, not native execution.
        from server.kk_local.packets import room_list_record
        r=room_list_record(1,self.room.request,2,spectators=1,spectator_capacity=1)
        self.assertEqual((r[34],struct.unpack_from('<HH',r,35),r[40]),(1,(1,1),2))

    def test_no_observer_ready_needed_and_no_observer_effects(self):
        self.start();before=self.s.snapshot(1003)
        e,c=self.es[2],self.cs[2]
        for ident in (4030,4082,4200,8071):self.assertEqual(e.handle(c,Message(ident)),[])
        self.assertEqual(self.s.snapshot(1003),before)
        self.assertEqual(self.room.input_ready,{1001,1002})
        self.assertEqual(self.room.last_sequence,{})

    def test_observer_result_has_no_fictitious_row_or_private_profile_and_ack_required(self):
        self.h.match_point_rewards={0:2,1:10,2:1}
        self.start();before=self.s.snapshot(1003)
        self.assertEqual(self.es[2].handle(self.cs[2],self.report()),[])
        self.assertEqual(self.room.result_reports,{})
        for e,c in zip(self.es[:2],self.cs[:2]):e.handle(c,self.report())
        rows=next(m for m in self.es[2].take_pending(self.cs[2]) if m.id==4120).payload
        self.assertEqual(len(rows),1000)
        self.assertEqual([struct.unpack_from('<Q',rows,i)[0] for i in (0,500)],[1001,1002])
        self.assertEqual(rows[140:500]+rows[640:1000],bytes(720))
        self.assertEqual(self.s.snapshot(1003),before)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM match_point_grants WHERE uid=1003').fetchone()[0],0)
        for e,c in zip(self.es[:2],self.cs[:2]):e.handle(c,Message(4115,bytes(4)))
        self.assertEqual(self.room.stage,'result')
        self.es[2].handle(self.cs[2],Message(4115,bytes(4)))
        self.assertEqual(self.room.stage,'room')
        self.assertTrue(self.room.members[1003].spectator)

    def test_observer_departure_does_not_abort_fighters(self):
        self.start();self.es[2].disconnect(self.cs[2])
        self.assertEqual(self.room.stage,'battle');self.assertIn(1,self.h.rooms)
        self.assertEqual(set(self.room.members),{1001,1002})
        for out in self.drain()[:2]:self.assertEqual([m.id for m in out],[3130])

    def test_last_observer_departure_unblocks_loading(self):
        self.es[1].handle(self.cs[1],Message(4030));self.drain()
        self.es[0].handle(self.cs[0],Message(4030));self.drain()
        for e,c in zip(self.es[:2],self.cs[:2]):e.handle(c,Message(4160))
        self.drain();self.assertEqual(self.room.stage,'loading')
        self.es[2].disconnect(self.cs[2]);self.drain()
        self.assertEqual(self.room.stage,'wait_ready')

    def test_departure_after_fighter_acks_does_not_leave_result_stuck(self):
        self.start()
        for e,c in zip(self.es[:2],self.cs[:2]):e.handle(c,self.report())
        self.drain()
        for e,c in zip(self.es[:2],self.cs[:2]):e.handle(c,Message(4115,bytes(4)))
        self.assertEqual(self.room.stage,'result')
        self.es[2].disconnect(self.cs[2])
        self.assertEqual(self.room.stage,'room')
        self.assertEqual(len(self.room.fighters),2)

    def test_no_host_transfer_to_observer_and_default_capacity_validation(self):
        out=self.es[0].handle(self.cs[0],Message(4051,struct.pack('<IQ',1,1003)))
        self.assertEqual(out[0].id,4052);self.assertNotEqual(out[0].payload,b'\0\0')
        self.assertEqual(self.room.owner,1001)
        for capacity in (-1,9,True):
            with self.assertRaises(ValueError):RoomHub(spectator_capacity=capacity)

    def test_spectator_reconnect_restores_observer_record_not_active_slot(self):
        self.es[2].disconnect(self.cs[2]);self.drain()
        self.cs[2]=lobby(self.es[2],22)
        self.assertEqual(self.cs[2].phase,Phase.ROOM)
        self.assertTrue(self.room.members[1003].spectator)
        for messages in self.drain()[:2]:
            self.assertEqual(messages[-1].payload[76],1)
            self.assertEqual(messages[-1].payload[8],8)

    def test_toggle_capacity_and_real_reinstall_payload(self):
        # Active slots are full, so observer cannot evict an existing fighter.
        out=self.es[2].handle(self.cs[2],Message(3091))
        self.assertEqual(struct.unpack_from('<i',out[0].payload,8)[0],-1)
        self.es[1].handle(self.cs[1],Message(3110));self.drain()
        out=self.es[2].handle(self.cs[2],Message(3091))
        record=next(m for m in out if m.id==3092).payload
        self.assertEqual((record[12],record[16+76],record[16+8]),(0,0,1))
        self.assertEqual(len(record),165+7*68)
        self.assertFalse(self.room.members[1003].spectator)
        self.drain()
        out=self.es[0].handle(self.cs[0],Message(3091))
        self.assertEqual([m.id for m in out],[3092,3160])
        self.assertEqual(self.room.owner,1003)
        self.assertTrue(self.room.members[1001].spectator)


if __name__=='__main__':unittest.main()
