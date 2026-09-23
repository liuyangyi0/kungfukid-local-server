"""Synthetic Mode21 plan over two actual loopback game connections."""
import asyncio
import struct
import unittest
from server.kk_local.maps import MapCatalog,MapDefinition
from server.kk_local.stage_catalog import StagePlan
from server.kk_local.rooms import RoomHub
from server.kk_local.store import Store
from server.kk_local.service import Service
from server.kk_local.engine import Connection
from server.kk_local.wire import Message,encode_game,GameDecoder
from server.tests.test_local_service import hello,create_room
from server.tests.test_pve import event


class StageTCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_two_clients_start_wave_and_zero_award_finish(self):
        store=Store(':memory:');store.seed_local();store.provision_local(1002,'Second')
        hub=RoomHub(network_probe=True)
        mode=getattr(self,'mode',21);map_id=8110 if mode==10 else 9170
        maps=MapCatalog({map_id:MapDefinition(map_id,'fixture',8,'unused','unused',(),())},{mode:{map_id}})
        if mode==10:
            from server.tests.test_foster import plan
            maps.foster_plans={8110:plan()}
        else:maps.stage_plans={9170:StagePlan(9170,('test',),((1,8,(((0,1),),)),),('fixture',))}
        services=[Service(store,lambda:True,login_port=0,game_port=0,p2p_port=0,offline_adapter=True,
                          hub=hub,account_uid=u,map_catalog=maps) for u in (1001,1002)]
        writers=[]
        async def receive(reader):
            while True:
                head=await asyncio.wait_for(reader.readexactly(8),3)
                body=await asyncio.wait_for(reader.readexactly(struct.unpack_from('<I',head,4)[0]),3)
                m=GameDecoder().feed(head+body)[0]
                if m.id not in (0,8090):return m
        async def send(writer,message):writer.write(encode_game(message));await writer.drain()
        try:
            channels=[]
            for service in services:
                await service.start()
                e=service.engine;e.grant_offline_adapter_session();bootstrap=Connection(99)
                e.handle(bootstrap,hello(uid=service.account_uid));bootstrap.bootstrap_sent=True
                e.profile_ready(bootstrap,True);e.handle(bootstrap,Message(3320,struct.pack('<I',1)))
                e.disconnect(bootstrap)
                reader,writer=await asyncio.open_connection('127.0.0.1',service.game_port);writers.append(writer)
                await send(writer,hello(2010,service.account_uid));self.assertEqual((await receive(reader)).id,2030)
                peer=hub.allocate_p2p_id();service.engine.register_p2p(peer,peer,('127.0.0.1',30000+peer))
                await send(writer,Message(1156,struct.pack('<QI',service.account_uid,peer)))
                channels.append((reader,writer))
            (r1,w1),(r2,w2)=channels
            request=bytearray(create_room().payload);request[46]=mode;struct.pack_into('<II',request,38,map_id,map_id)
            await send(w1,Message(3010,bytes(request)));self.assertEqual([(await receive(r1)).id for _ in range(2)],[3100,3160])
            await send(w2,Message(3070,struct.pack('<HB11s',1,0,b'')))
            self.assertEqual([(await receive(r2)).id for _ in range(3)],[3100,3160,3090]);self.assertEqual((await receive(r1)).id,3090)
            await send(w2,Message(4030))
            for reader in (r1,r2):self.assertEqual((await receive(reader)).id,4050)
            await send(w1,Message(4030))
            for reader in (r1,r2):self.assertEqual([(await receive(reader)).id for _ in range(2)],[4050,4150])
            for writer in writers:await send(writer,Message(4140))
            for reader in (r1,r2):self.assertEqual((await receive(reader)).id,4080)
            await send(w1,event(20403,1,actor=10))
            if mode==10:
                from server.tests.test_foster import initial_positions
                await send(w1,initial_positions())
            await send(w1,Message(4160))
            for reader in (r1,r2):self.assertEqual((await receive(reader)).id,4170)
            await send(w2,Message(4160))
            self.assertEqual([(await receive(r1)).id for _ in range(2)],[4170,4180])
            expected=[4170,8071,8071,4180] if mode==10 else [4170,8071,4180]
            self.assertEqual([(await receive(r2)).id for _ in expected],expected)
            for uid,(_,writer) in zip((1001,1002),channels):await send(writer,Message(8040,struct.pack('<HQI',1,uid,0)))
            for reader in (r1,r2):self.assertEqual((await receive(reader)).id,8070)
            room=services[0].engine.room
            if mode==10:
                from server.tests.test_foster import spawn,damage
                for m in (spawn(200,0,(0.,0.,0.),2),damage(room,200,8,3),
                          spawn(201,1,(5.,0.,0.),4),damage(room,201,16,5),
                          spawn(202,1,(6.,0.,0.),6),damage(room,202,16,7)):
                    await send(w1,m);self.assertEqual(await receive(r2),m)
                marker=bytearray(event(20407,8).payload);struct.pack_into('<II',marker,39,1,room.serial)
                msg=Message(8071,bytes(marker));await send(w1,msg);self.assertEqual(await receive(r2),msg)
            else:
                for m in (event(20400,2),event(20401,3)):
                    await send(w1,m);self.assertEqual(await receive(r2),m)
                report=bytearray(40);struct.pack_into('<4I',report,0,1,room.serial,1,1)
                await send(w1,Message(20571,bytes(report)))
                for reader in (r1,r2):
                    out=await receive(reader);self.assertEqual(out.id,20572);self.assertEqual(struct.unpack_from('<i',out.payload,8)[0],-1)
            finish=bytearray(696)
            for slot,uid in enumerate((1001,1002)):
                offset=87*slot;struct.pack_into('<HH',finish,offset,100,100);struct.pack_into('<Q',finish,offset+29,uid)
                struct.pack_into('<HII',finish,offset+65,1,1,room.serial)
            await send(w1,Message(4110,bytes(finish)))
            for reader in (r1,r2):
                out=await receive(reader);self.assertEqual((out.id,len(out.payload)),(4120,1000))
            for writer in writers:await send(writer,Message(4115,bytes(4)))
            for reader in (r1,r2):self.assertEqual([(await receive(reader)).id for _ in range(2)],[4070,4070])
            self.assertEqual(room.stage,'room');self.assertIsNone(room.pve)
            self.assertEqual(store.gold_balance(1001),0)
        finally:
            for w in writers:w.close();await w.wait_closed()
            for s in services:await s.close()
            store.close()


class FosterTCPTests(StageTCPTests):
    mode=10
