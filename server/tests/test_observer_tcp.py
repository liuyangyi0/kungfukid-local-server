"""Three actual loopback TCP clients: two fighters and a read-only observer."""
import asyncio
import struct
import unittest
from server.kk_local.service import Service
from server.kk_local.rooms import RoomHub
from server.kk_local.store import Store
from server.kk_local.wire import Message,GameDecoder,encode_game
from server.tests.test_local_service import hello,create_room
from server.tests.test_mp_snapshot_relay import mp_snapshot


class ObserverTcpTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_endpoint_load_relay_result_and_return(self):
        store=Store(':memory:');store.seed_local()
        for uid in (1002,1003):store.provision_local(uid,'User'+str(uid))
        hub=RoomHub(spectator_capacity=1)
        services=[Service(store,lambda:True,login_port=0,game_port=0,p2p_port=0,
                  offline_adapter=True,hub=hub,account_uid=u) for u in (1001,1002,1003)]
        writers=[]
        async def receive(r):
            while True:
                head=await asyncio.wait_for(r.readexactly(8),2)
                body=await asyncio.wait_for(r.readexactly(struct.unpack_from('<I',head,4)[0]),2)
                m=GameDecoder().feed(head+body)[0]
                if m.id:return m
        async def send(w,m):w.write(encode_game(m));await w.drain()
        async def connect(s):
            e=s.engine;e.grant_offline_adapter_session()
            r,w=await asyncio.open_connection('127.0.0.1',s.game_port);writers.append(w)
            await send(w,hello(uid=e.account_uid))
            for ident in (1131,1020,1120,7080,7070,1151):self.assertEqual((await receive(r)).id,ident)
            await send(w,Message(3320,struct.pack('<I',1)))
            for ident in (3330,1201):self.assertEqual((await receive(r)).id,ident)
            w.close();await w.wait_closed();await asyncio.sleep(.02)
            r,w=await asyncio.open_connection('127.0.0.1',s.game_port);writers.append(w)
            await send(w,hello(2010,e.account_uid));self.assertEqual((await receive(r)).id,2030)
            key=hub.allocate_p2p_id()
            e.register_p2p(key,key,('127.0.0.1',30000+e.account_uid))
            await send(w,Message(1156,struct.pack('<QI',e.account_uid,key)))
            return r,w
        try:
            for s in services:await s.start()
            pairs=[await connect(s) for s in services]
            rs=[p[0] for p in pairs];ws=[p[1] for p in pairs]
            p=bytearray(create_room().payload);p[34]=1;p[37]=2;p[46]=1
            await send(ws[0],Message(3010,bytes(p)))
            for ident in (3100,3160):self.assertEqual((await receive(rs[0])).id,ident)
            for index,watch in ((1,0),(2,1)):
                await send(ws[index],Message(3070,struct.pack('<HB11s',1,watch,b'')))
                entry=await receive(rs[index]);self.assertEqual(entry.id,3100)
                self.assertEqual(entry.payload[10],8 if watch else 1)
                for ident in [3160]+[3090]*index:self.assertEqual((await receive(rs[index])).id,ident)
                for old in range(index):self.assertEqual((await receive(rs[old])).id,3090)
            await send(ws[1],Message(4030))
            for r in rs:self.assertEqual((await receive(r)).id,4050)
            await send(ws[0],Message(4030))
            for r in rs:
                self.assertEqual((await receive(r)).id,4050)
                start=await receive(r);self.assertEqual(start.id,4080)
                self.assertEqual(struct.unpack_from('<I',start.payload,21)[0],0)
            room=services[0].engine.room
            for index,w in enumerate(ws):
                await send(w,Message(4160))
                for r in rs:self.assertEqual((await receive(r)).id,4170)
                if index==2:
                    for r in rs[:2]:self.assertEqual((await receive(r)).id,4180)
            for i in (0,1):await send(ws[i],Message(8040,struct.pack('<HQI',1,1001+i,0)))
            for r in rs:self.assertEqual((await receive(r)).id,8070)
            self.assertEqual(room.input_ready,{1001,1002})
            event=mp_snapshot(room,value=20)
            await send(ws[0],event)
            for i in (1,2):self.assertEqual(await receive(rs[i]),event)
            # A watcher packet must never precede the next legitimate event.
            await send(ws[2],mp_snapshot(room,sender=1003,player=1003))
            reverse=mp_snapshot(room,sender=1002,player=1002,value=40)
            await send(ws[1],reverse)
            for i in (0,2):self.assertEqual(await receive(rs[i]),reverse)
            report=bytearray(696)
            for slot,uid in enumerate((1001,1002)):
                o=87*slot;struct.pack_into('<HH',report,o,100,100 if slot==0 else 0)
                struct.pack_into('<Q',report,o+29,uid);struct.pack_into('<II',report,o+67,1,room.serial)
            await send(ws[2],Message(4110,bytes(report)))
            await send(ws[0],Message(4110,bytes(report)))
            self.assertEqual(await receive(rs[1]),Message(4100))
            await send(ws[1],Message(4110,bytes(report)))
            for i,r in enumerate(rs):
                result=await receive(r);self.assertEqual((result.id,len(result.payload)),(4120,1000))
                if i==2:self.assertEqual(result.payload[140:500]+result.payload[640:1000],bytes(720))
            for i in (0,1):await send(ws[i],Message(4115,bytes(4)))
            await send(ws[2],Message(4115,bytes(4)))
            for r in rs:
                for _ in range(2):self.assertEqual((await receive(r)).id,4070)
            self.assertEqual(room.stage,'room');self.assertTrue(room.members[1003].spectator)
            await send(ws[2],Message(3110));self.assertEqual((await receive(rs[2])).id,3115)
            for i in (0,1):self.assertEqual((await receive(rs[i])).id,3130)
            self.assertEqual(set(room.members),{1001,1002})
        finally:
            for w in writers:w.close()
            for w in writers:await w.wait_closed()
            for s in services:await s.close()
            store.close()


if __name__=='__main__':unittest.main()
