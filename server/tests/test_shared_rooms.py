import asyncio
import struct
import unittest

from server.kk_local.engine import Engine, Connection, Phase
from server.kk_local.rooms import RoomHub
from server.kk_local.store import Store
from server.kk_local.service import Service
from server.kk_local.wire import Message, ProtocolError, GameDecoder, encode_game
from server.tests.test_local_service import hello, create_room


def lobby(engine, number):
    engine.grant_offline_adapter_session()
    b=Connection(number)
    engine.handle(b,hello(uid=engine.account_uid)); b.bootstrap_sent=True
    engine.profile_ready(b,True)
    engine.handle(b,Message(3320,struct.pack('<I',1)))
    engine.disconnect(b)
    c=Connection(number+1)
    engine.handle(c,hello(2010,engine.account_uid))
    player=engine.hub.allocate_p2p_id()
    engine.register_p2p(player,player,('127.0.0.1',20000+number))
    engine.handle(c,Message(1156,struct.pack('<QI',c.uid,player)))
    return c


class SharedRoomTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:'); self.s.seed_local(); self.s.provision_local(1002,'Second')
        self.h=RoomHub()
        self.e1=Engine(self.s,hub=self.h)
        self.e2=Engine(self.s,hub=self.h,account_uid=1002)
        self.c1=lobby(self.e1,1); self.c2=lobby(self.e2,3)

    def tearDown(self): self.s.close()

    def join(self):
        self.assertEqual([m.id for m in self.e1.handle(self.c1,create_room())],[3100,3160])
        out=self.e2.handle(self.c2,Message(3070,struct.pack('<HB11s',1,0,b'')))
        self.assertEqual([m.id for m in out],[3100,3160,3090])
        self.assertEqual(out[0].payload[10],1)
        self.assertEqual(struct.unpack_from('<Q',out[1].payload)[0],1001)
        self.assertEqual([m.id for m in self.e1.take_pending(self.c1)],[3090])
        return out

    def battle(self):
        self.join()
        self.e2.handle(self.c2,Message(4030)); self.e1.take_pending(self.c1)
        self.e1.handle(self.c1,Message(4030)); self.e2.take_pending(self.c2)
        self.e1.handle(self.c1,Message(4160)); self.e2.take_pending(self.c2)
        self.e2.handle(self.c2,Message(4160)); self.e1.take_pending(self.c1)
        self.e1.handle(self.c1,Message(8040,struct.pack('<HQI',1,1001,0)))
        self.e2.handle(self.c2,Message(8040,struct.pack('<HQI',1,1002,0)))
        self.e1.take_pending(self.c1)

    def test_native_directory_and_actual_equipment_tail(self):
        self.e1.handle(self.c1,create_room())
        directory=self.e2.handle(self.c2,Message(2260,bytes(3)))[0]
        self.assertEqual((directory.id,len(directory.payload)),(2280,267))
        row=directory.payload[8:]
        self.assertEqual(struct.unpack_from('<H',row)[0],1)
        self.assertEqual(row[39:45],bytes([4,1,1,0,0,5]))
        self.assertEqual(struct.unpack_from('<II',row,23),(804,804))
        self.e2.handle(self.c2,Message(3070,struct.pack('<HB11s',1,0,b'')))
        peer=self.e1.take_pending(self.c1)[0]
        self.assertEqual((peer.id,len(peer.payload)),(3090,149+7*68))
        self.assertEqual(peer.payload[8:11],bytes([1,1,1]))
        self.assertEqual(peer.payload[11:32].split(b'\0')[0],b'Second')
        self.assertEqual(peer.payload[54:57],bytes([1,0,1]))
        self.assertEqual(peer.payload[64],7)
        self.assertEqual(peer.payload[149:],self.s.snapshot(1002)[3])

    def test_all_member_ready_load_and_tick_barriers(self):
        self.join()
        self.assertEqual(self.e1.handle(self.c1,Message(4030)),[])
        self.assertEqual(self.c1.phase,Phase.ROOM)
        self.assertFalse(self.e1.room.members[1001].ready)
        self.assertEqual([m.id for m in self.e2.handle(self.c2,Message(4030))],[4050])
        self.e1.take_pending(self.c1)
        self.assertEqual([m.id for m in self.e2.handle(self.c2,Message(4060))],[4070])
        self.e1.take_pending(self.c1)
        self.e2.handle(self.c2,Message(4030)); self.e1.take_pending(self.c1)
        out=self.e1.handle(self.c1,Message(4030))
        other=self.e2.take_pending(self.c2)
        self.assertEqual([m.id for m in out],[4050,4080])
        self.assertEqual([m.id for m in other],[4050,4080])
        self.assertEqual(struct.unpack_from('<H',other[1].payload,11)[0],1)
        self.assertEqual(struct.unpack_from('<II',out[1].payload,13),(1001,1002))
        self.assertEqual([m.id for m in self.e1.handle(self.c1,Message(4160))],[4170])
        self.assertEqual(self.c1.phase,Phase.LOADING)
        self.e2.take_pending(self.c2)
        self.assertEqual([m.id for m in self.e2.handle(self.c2,Message(4160))],[4170,4180])
        self.e1.take_pending(self.c1)
        self.assertEqual(self.e1.handle(self.c1,Message(8040,struct.pack('<HQI',1,1001,0))),[])
        self.assertEqual(self.c1.phase,Phase.WAIT_READY)
        self.assertEqual([m.id for m in self.e2.handle(self.c2,Message(8040,struct.pack('<HQI',1,1002,0)))],[8070])
        self.assertEqual([m.id for m in self.e1.take_pending(self.c1)],[8070])
        self.assertEqual((self.c1.phase,self.c2.phase),(Phase.BATTLE,Phase.BATTLE))

    def test_leave_transfers_owner_and_last_member_dissolves(self):
        self.join()
        self.assertEqual([m.id for m in self.e1.handle(self.c1,Message(3110))],[3115])
        self.assertEqual([m.id for m in self.e2.take_pending(self.c2)],[3130,3160])
        self.assertEqual(self.e2.room.owner,1002)
        self.e2.handle(self.c2,Message(3110))
        self.assertFalse(self.h.rooms)
        self.assertIsNone(self.e1.room)
        self.assertIsNone(self.e2.room)

    def test_disconnect_does_not_destroy_another_waiting_room(self):
        self.e1.handle(self.c1,create_room()); self.e2.handle(self.c2,create_room())
        now=[100.0]; self.e1.clock=lambda:now[0]
        self.e1.disconnect(self.c1)
        self.assertEqual(list(self.h.rooms),[1,2])
        now[0]+=31; self.h.expire()
        self.assertEqual(list(self.h.rooms),[2])
        self.assertEqual(self.e2.room.number,2)

    def test_invalid_join_has_no_partial_membership(self):
        self.e1.handle(self.c1,create_room())
        with self.assertRaises(ProtocolError): self.e2.handle(self.c2,Message(3070,bytes(13)))
        for p,code in ((struct.pack('<HB11s',99,0,b''),29),(struct.pack('<HB11s',1,1,b''),130),(struct.pack('<HB11s',1,0,b'bad'),31)):
            self.assertEqual(self.e2.handle(self.c2,Message(3070,p)),[Message(3080,p+struct.pack('<I',code))])
            self.assertIsNone(self.e2.room)
            self.assertEqual(list(self.e1.room.members),[1001])

    def test_relay_not_echoed_and_replay_or_spoof_rejected(self):
        self.battle()
        p=bytearray(108); struct.pack_into('<IQ',p,0,0x1fb8,1001); struct.pack_into('<I',p,19,1)
        self.assertEqual(self.e1.handle(self.c1,Message(8071,bytes(p))),[])
        self.assertEqual(self.e2.take_pending(self.c2),[Message(8071,bytes(p))])
        self.e1.handle(self.c1,Message(8071,bytes(p)))
        self.assertEqual(self.e2.take_pending(self.c2),[])
        struct.pack_into('<Q',p,4,1002)
        with self.assertRaises(ProtocolError): self.e1.handle(self.c1,Message(8071,bytes(p)))

    def test_battle_disconnect_aborts_without_fabricating_rewards(self):
        self.battle(); before=self.s.snapshot(1002)
        self.e1.disconnect(self.c1)
        self.assertEqual(self.e2.take_pending(self.c2),[Message(3115)])
        self.assertEqual(self.c2.phase,Phase.LOBBY)
        self.assertFalse(self.h.rooms)
        self.assertEqual(self.s.snapshot(1002),before)

    def test_account_isolation_and_slow_recipient_bound(self):
        with self.assertRaises(ProtocolError): self.e2.handle(Connection(8),hello(uid=1001))
        for _ in range(1025): self.e2.enqueue(Message(99))
        self.assertTrue(self.e2.delivery_failed)
        self.assertEqual(len(self.e2.pending_messages),0)
        self.assertFalse(self.e1.delivery_failed)

    def test_waiting_room_reconnect_requires_fresh_session_and_p2p(self):
        self.join()
        self.e2.disconnect(self.c2)
        self.assertIn(1002,self.h.suspended)
        self.assertEqual(self.e1.take_pending(self.c1),[Message(4070,struct.pack('<Q',1002))])
        self.assertEqual(self.e1.handle(self.c1,Message(4030)),[])
        with self.assertRaises(ProtocolError):
            self.e2.handle(Connection(9),hello(2010,1002))
        self.c2=lobby(self.e2,10)
        self.assertEqual(self.c2.phase,Phase.ROOM)
        self.assertEqual(self.e2.room.members[1002].slot,1)
        self.assertNotIn(1002,self.h.suspended)
        restored=self.e1.take_pending(self.c1)
        self.assertEqual(restored[-1].id,3090)
        self.assertEqual(restored[-1].payload[76],1)


class SharedRoomNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_endpoint_tcp_join_fanout(self):
        store=Store(':memory:'); store.seed_local(); store.provision_local(1002,'Second')
        hub=RoomHub()
        services=[Service(store,lambda:True,login_port=0,game_port=0,p2p_port=0,
                          offline_adapter=True,hub=hub,account_uid=uid) for uid in (1001,1002)]
        writers=[]
        async def receive(reader):
            while True:
                head=await asyncio.wait_for(reader.readexactly(8),2)
                body=await asyncio.wait_for(reader.readexactly(struct.unpack_from('<I',head,4)[0]),2)
                msg=GameDecoder().feed(head+body)[0]
                if msg.id: return msg
        async def connect(service):
            e=service.engine; e.grant_offline_adapter_session()
            r,w=await asyncio.open_connection('127.0.0.1',service.game_port); writers.append(w)
            w.write(encode_game(hello(uid=e.account_uid))); await w.drain()
            for ident in (1131,1020,1120,7080,7070,1151):
                self.assertEqual((await receive(r)).id,ident)
            w.write(encode_game(Message(3320,struct.pack('<I',1)))); await w.drain()
            self.assertEqual([(await receive(r)).id for _ in range(2)],[3330,1201])
            w.close(); await w.wait_closed(); await asyncio.sleep(.02)
            r,w=await asyncio.open_connection('127.0.0.1',service.game_port); writers.append(w)
            w.write(encode_game(hello(2010,e.account_uid))); await w.drain()
            self.assertEqual((await receive(r)).id,2030)
            p2p=hub.allocate_p2p_id()
            # UDP codec/lease is independently covered; focus this test on
            # live TCP recipient routing and per-connection send locks.
            e.register_p2p(p2p,p2p,('127.0.0.1',30000+e.account_uid))
            w.write(encode_game(Message(1156,struct.pack('<QI',e.account_uid,p2p)))); await w.drain()
            return r,w
        try:
            for service in services: await service.start()
            r1,w1=await connect(services[0]); r2,w2=await connect(services[1])
            w1.write(encode_game(create_room())); await w1.drain()
            self.assertEqual([(await receive(r1)).id for _ in range(2)],[3100,3160])
            w2.write(encode_game(Message(2260,bytes(3)))); await w2.drain()
            self.assertEqual(len((await receive(r2)).payload),267)
            w2.write(encode_game(Message(3070,struct.pack('<HB11s',1,0,b'')))); await w2.drain()
            self.assertEqual([(await receive(r2)).id for _ in range(3)],[3100,3160,3090])
            peer=await receive(r1)
            self.assertEqual((peer.id,struct.unpack_from('<Q',peer.payload)[0]),(3090,1002))
            w2.write(encode_game(Message(4030))); await w2.drain()
            self.assertEqual((await receive(r2)).id,4050)
            self.assertEqual((await receive(r1)).payload,struct.pack('<Q',1002))
        finally:
            for w in writers: w.close()
            for service in services: await service.close()
            store.close()
