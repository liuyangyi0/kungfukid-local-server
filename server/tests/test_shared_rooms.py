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
        directory=self.e2.handle(self.c2,Message(2260,bytes((1,1,136))))[0]
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
        self.assertEqual(struct.unpack_from('<H',other[1].payload,11)[0],0)  # shared Host, not receiver slot
        self.assertEqual(struct.unpack_from('<II',out[1].payload,13),(0,0))  # unmeasured; never P2P IDs
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
        self.assertEqual(restored[-1].payload[76],0)  # active slot, not spectator map


class SharedRoomNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_endpoint_tcp_join_fanout(self):
        store=Store(':memory:'); store.seed_local(); store.provision_local(1002,'Second')
        hub=RoomHub(team_series_rounds=getattr(self,'series_rounds',0),network_probe=getattr(self,'network_probe',False))
        services=[Service(store,lambda:True,login_port=0,game_port=0,p2p_port=0,
                          offline_adapter=True,hub=hub,account_uid=uid) for uid in (1001,1002)]
        writers=[]
        clock_samples={}
        async def receive(reader):
            while True:
                head=await asyncio.wait_for(reader.readexactly(8),2)
                body=await asyncio.wait_for(reader.readexactly(struct.unpack_from('<I',head,4)[0]),2)
                msg=GameDecoder().feed(head+body)[0]
                if msg.id==8090:
                    self.assertEqual(len(msg.payload),4)
                    value=struct.unpack('<I',msg.payload)[0]
                    self.assertGreater(value,0)
                    clock_samples.setdefault(reader,[]).append(value)
                    continue
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
            w1.write(encode_game(Message(2250,struct.pack('<II',1,10))));await w1.drain()
            directory=await receive(r1)
            self.assertEqual((directory.id,len(directory.payload)),(2270,144))
            self.assertEqual([struct.unpack_from('<Q',directory.payload,8+i*68)[0] for i in range(2)],
                             [1001,1002])
            w1.write(encode_game(Message(2420,struct.pack('<Q',1002))));await w1.drain()
            profile=await receive(r1)
            self.assertEqual((profile.id,len(profile.payload)),(2421,377+7*68))
            self.assertEqual(struct.unpack_from('<Q',profile.payload,8)[0],1002)
            w1.write(encode_game(Message(2430,struct.pack('<Q',1002))));await w1.drain()
            collection=await receive(r1)
            self.assertEqual((collection.id,len(collection.payload)),(2431,29))
            self.assertEqual(struct.unpack_from('<I',collection.payload,20)[0],253030)
            request=bytearray(create_room().payload);request[46]=getattr(self,'mode',0)
            w1.write(encode_game(Message(3010,bytes(request)))); await w1.drain()
            self.assertEqual([(await receive(r1)).id for _ in range(2)],[3100,3160])
            w2.write(encode_game(Message(2260,bytes((1,1,136))))); await w2.drain()
            self.assertEqual(len((await receive(r2)).payload),267)
            from server.tests.test_room_invites import invite,decline,accept
            w1.write(encode_game(invite()));await w1.drain()
            self.assertEqual((await receive(r2)).id,3500)
            w2.write(encode_game(decline()));await w2.drain()
            refused=await receive(r1)
            self.assertEqual((refused.id,refused.payload[8:29].split(b'\0')[0]),(3502,b'Second'))
            w1.write(encode_game(invite()));await w1.drain()
            self.assertEqual((await receive(r2)).id,3500)
            w2.write(encode_game(accept()));await w2.drain()
            self.assertEqual([(await receive(r2)).id for _ in range(3)],[3100,3160,3090])
            peer=await receive(r1)
            self.assertEqual((peer.id,struct.unpack_from('<Q',peer.payload)[0]),(3090,1002))
            # Transfer and return authority through the actual TCP router.
            for writer,reader,other,target in ((w1,r1,r2,1002),(w2,r2,r1,1001)):
                writer.write(encode_game(Message(4051,struct.pack('<IQ',1,target))));await writer.drain()
                update=Message(3160,struct.pack('<Q',target))
                self.assertEqual(await receive(reader),update)
                self.assertEqual(await receive(other),update)
                self.assertEqual(await receive(reader),Message(4052,b'\0\0'))
            w2.write(encode_game(Message(4030))); await w2.drain()
            self.assertEqual((await receive(r2)).id,4050)
            self.assertEqual((await receive(r1)).payload,struct.pack('<Q',1002))
            w1.write(encode_game(Message(4030)));await w1.drain()
            starts=[]
            for reader in (r1,r2):
                self.assertEqual((await receive(reader)).id,4050)
                start=await receive(reader)
                if hub.network_probe_enabled:self.assertEqual(start.id,4150)
                else:
                    self.assertEqual(start.id,4080);starts.append(start.payload)
            if hub.network_probe_enabled:
                for writer in (w1,w2):writer.write(encode_game(Message(4140)));await writer.drain()
                for reader in (r1,r2):
                    start=await receive(reader);self.assertEqual(start.id,4080);starts.append(start.payload)
                    self.assertTrue(all(0<x<10000 for x in struct.unpack_from('<II',start.payload,13)))
            self.assertEqual(starts[0],starts[1])  # one Host designation
            w1.write(encode_game(Message(4160)));await w1.drain()
            for reader in (r1,r2):self.assertEqual((await receive(reader)).id,4170)
            w2.write(encode_game(Message(4160)));await w2.drain()
            for reader in (r1,r2):
                self.assertEqual([(await receive(reader)).id for _ in range(2)],[4170,4180])
            w1.write(encode_game(Message(8040,struct.pack('<HQI',1,1001,0))));await w1.drain()
            w2.write(encode_game(Message(8040,struct.pack('<HQI',1,1002,0))));await w2.drain()
            for reader in (r1,r2):self.assertEqual((await receive(reader)).id,8070)
            w1.write(encode_game(Message(4082)));await w1.drain()
            self.assertEqual(await receive(r1),Message(4083,struct.pack('<QIII',1001,0,0,0)))
            from server.tests.test_owned_battle_relay import message as control
            for seq,ident in enumerate((8122,8125,8143,8280)):
                m=control(ident,seq)
                w1.write(encode_game(m));await w1.drain()
                self.assertEqual(await receive(r2),m)
                # Duplicate is followed by the next event on the same TCP
                # stream; that next comparison would detect an extra delivery.
                w1.write(encode_game(m));await w1.drain()
            end=control(8122,4)
            w1.write(encode_game(end));await w1.drain()
            self.assertEqual(await receive(r2),end)
            reverse=control(8143,0,sender=1002,player=1002,target=1001)
            w2.write(encode_game(reverse));await w2.drain()
            self.assertEqual(await receive(r1),reverse)  # also detects sender echo
            from server.tests.test_battle_effect_relay import effect
            room=services[0].engine.room
            hp=effect(8121,room,seq=5,amount=12.5)
            w1.write(encode_game(hp));await w1.drain()
            self.assertEqual(await receive(r2),hp)
            apply=effect(8150,room,sender=1002,target=1002,source=1002,seq=1)
            w2.write(encode_game(apply));await w2.drain()
            self.assertEqual(await receive(r1),apply)  # no HP echo on this stream
            cancel=effect(8150,room,target=1002,source=0,operation=0,seq=6)
            w1.write(encode_game(cancel));await w1.drain()
            self.assertEqual(await receive(r2),cancel)
            self.assertNotIn((1002,12),room.active_states)
            from server.tests.test_projectile_protocol import projectile
            for seq,ident in enumerate((8400,8403,8401,8404),start=7):
                m=projectile(ident,room,seq=seq)
                w1.write(encode_game(m));await w1.drain()
                self.assertEqual(await receive(r2),m)
                if ident==8400:
                    for hit_seq,hit_id in ((2,8402),(3,8401)):
                        hit_event=projectile(hit_id,room,sender=1002,seq=hit_seq)
                        w2.write(encode_game(hit_event));await w2.drain()
                        self.assertEqual(await receive(r1),hit_event)
            self.assertFalse(room.projectiles[10000]['alive'])
            from server.tests.test_pickup_handshake import pickup
            request=pickup(9000,room,seq=4)
            w2.write(encode_game(request));await w2.drain()
            self.assertEqual(await receive(r1),request)
            response=pickup(9001,room,sender=1001,seq=11)
            w1.write(encode_game(response));await w1.drain()
            self.assertEqual(await receive(r2),response)
            completion=pickup(9002,room,seq=5)
            w2.write(encode_game(completion));await w2.drain()
            self.assertEqual(await receive(r1),completion)
            self.assertEqual(room.pickup_requests,{})
            from server.tests.test_world_chest_handshake import chest
            request=chest(9500,seq=6)
            w2.write(encode_game(request));await w2.drain()
            self.assertEqual(await receive(r1),request)
            reply=chest(9501,sender=1001,seq=12)
            w1.write(encode_game(reply));await w1.drain()
            self.assertEqual(await receive(r2),reply)
            complete=chest(9502,seq=7)
            w2.write(encode_game(complete));await w2.drain()
            self.assertEqual(await receive(r1),complete)
            from server.kk_local.combat_catalog import CombatCatalog
            import xml.etree.ElementTree as ET
            hub.combat_catalog=CombatCatalog.from_xml(ET.fromstring(
                '<SkillProperty><PropertyItem SkillProId="811115"/></SkillProperty>'))
            hit=bytearray(effect(8121,room,seq=13).payload)
            struct.pack_into('<I',hit,56,811115);hit[85]=1
            hit=Message(8071,bytes(hit))
            w1.write(encode_game(hit));await w1.drain()
            self.assertEqual(await receive(r2),hit)
            receipt=bytearray(71);struct.pack_into('<IQ',receipt,0,8126,1001)
            receipt[12:14]=b'\x01\x01';struct.pack_into('<I',receipt,19,14)
            struct.pack_into('<QQQII',receipt,39,1001,1001,1002,811115,1)
            receipt=Message(8071,bytes(receipt))
            w1.write(encode_game(receipt));await w1.drain()
            self.assertEqual(await receive(r2),receipt)
            from server.tests.test_collectible_spawn import spawn
            collectible=spawn(seq=15)
            w1.write(encode_game(collectible));await w1.drain()
            self.assertEqual(await receive(r2),collectible)
            from server.tests.test_mp_snapshot_relay import mp_snapshot
            mp=mp_snapshot(room,value=18.75,seq=16)
            w1.write(encode_game(mp));await w1.drain()
            self.assertEqual(await receive(r2),mp)
            from server.tests.test_death_notice import notice
            countdown=notice(8278,target=1002,seq=17)
            w1.write(encode_game(countdown));await w1.drain()
            self.assertEqual(await receive(r2),countdown)
            terminal=notice(8286,target=1002,seq=18)
            w1.write(encode_game(terminal));await w1.drain()
            self.assertEqual(await receive(r2),terminal)
            if room.request[46]==16:
                from server.tests.test_mode16_protocol import snapshot,event
                for m in (snapshot(seq=19),event(seq=20)):
                    w1.write(encode_game(m));await w1.drain()
                    self.assertEqual(await receive(r2),m)
            from server.tests.test_pair_transform import selection,transform
            select=selection(seq=100)
            w1.write(encode_game(select));await w1.drain()
            self.assertEqual(await receive(r2),select)
            paired=transform(seq=100)
            w2.write(encode_game(paired));await w2.drain()
            self.assertEqual(await receive(r1),paired)
            selector=control(8293,101)
            w1.write(encode_game(selector));await w1.drain()
            self.assertEqual(await receive(r2),selector)
            from server.tests.test_owned_slip import slip
            movement=slip(sender=1002,first=1002,seq=102)
            w2.write(encode_game(movement));await w2.drain()
            self.assertEqual(await receive(r1),movement)
            from server.tests.test_weapon_throw_relay import throw
            thrown=throw(room,seq=103)
            w1.write(encode_game(thrown));await w1.drain()
            self.assertEqual(await receive(r2),thrown)
            #Real TCP control replies interleave with the native battle clock.
            for reader in (r1,r2):
                self.assertTrue(clock_samples.get(reader))
                values=clock_samples[reader]
                self.assertEqual(values,sorted(set(values)))
            if room.series:
                from server.tests.test_team_series import series_event,report_bytes
                for writer,reader,msg in (
                    (w1,r2,series_event(8294,seq=104)),
                    (w2,r1,series_event(8296,sender=1002,seq=104)),
                    (w1,r2,series_event(8295,seq=105)),
                    (w1,r2,series_event(8297,seq=106))):
                    writer.write(encode_game(msg));await writer.drain()
                    self.assertEqual(await receive(reader),msg)
                w1.write(encode_game(Message(4111,bytes(report_bytes(scores=(2,0),perfect=1)))));await w1.drain()
                for reader in (r1,r2):
                    result=await receive(reader)
                    self.assertEqual((result.id,len(result.payload)),(4112,309))
                    self.assertEqual(struct.unpack_from('<ii',result.payload,3),(2,0))
                for writer,uid in ((w1,1001),(w2,1002)):
                    for value in (3,0):writer.write(encode_game(Message(3550,struct.pack('<QI',uid,value))))
                    await writer.drain()
            else:
                report=bytearray(696)
                for slot,(uid,hp) in enumerate(((1001,100),(1002,0))):
                    offset=87*slot
                    struct.pack_into('<HH',report,offset,100,hp)
                    struct.pack_into('<Q',report,offset+29,uid)
                    struct.pack_into('<HII',report,offset+65,65535,room.number,room.serial)
                    if room.request[46]==16:struct.pack_into('<i',report,offset+37,150 if uid==1001 else 100)
                w1.write(encode_game(Message(4110,bytes(report))));await w1.drain()
                self.assertEqual(await receive(r2),Message(4100))
                w2.write(encode_game(Message(4110,bytes(report))));await w2.drain()
                for reader in (r1,r2):
                    result=await receive(reader)
                    self.assertEqual(result.id,4120)
                    self.assertEqual((result.payload[10],result.payload[510]),(1,2))
                w1.write(encode_game(Message(4115,bytes(4))));await w1.drain()
                w2.write(encode_game(Message(4115,bytes(4))));await w2.drain()
            for reader in (r1,r2):
                self.assertEqual([(await receive(reader)).id for _ in range(2)],[4070,4070])
            self.assertEqual(room.stage,'room')
            #3550 is a room-only remote label. Exercise both identities and
            #clear it before continuing to leave/return-to-channel operations.
            for writer,reader,uid in ((w1,r2,1001),(w2,r1,1002)):
                for value in (3,0):
                    label=Message(3550,struct.pack('<QI',uid,value))
                    writer.write(encode_game(label));await writer.drain()
                    self.assertEqual(await receive(reader),label)
            w1.write(encode_game(Message(3110)));await w1.drain()
            self.assertEqual((await receive(r1)).id,3115)
            self.assertEqual([(await receive(r2)).id for _ in range(2)],[3130,3160])
            w1.write(encode_game(Message(2060)));await w1.drain()
            self.assertEqual((await receive(r1)).id,2070)
            self.assertEqual(await asyncio.wait_for(r1.read(),2),b'')
            r1,w1=await asyncio.open_connection('127.0.0.1',services[0].game_port);writers.append(w1)
            w1.write(encode_game(hello(2010,1001)));await w1.drain()
            self.assertEqual((await receive(r1)).id,2030)
            w2.write(encode_game(Message(4031)));await w2.drain()
            self.assertEqual(await receive(r2),Message(4032,struct.pack('<Q',1002)))
            self.assertIsNone(services[1].engine.room)
        finally:
            for w in writers: w.close()
            for service in services: await service.close()
            store.close()
