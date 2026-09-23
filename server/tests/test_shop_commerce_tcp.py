import asyncio
import struct
import unittest
from server.kk_local.engine import Connection
from server.kk_local.service import Service
from server.kk_local.wire import Message,GameDecoder,encode_game
from server.tests import test_shop_commerce as fixtures
from server.tests.test_local_service import hello


class ShopCommerceTCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_buy_gift_offline_mail_login_and_claim(self):
        f=fixtures.ShopCommerceTests();f.setUp();services=[];writers=[]
        async def connect(uid):
            service=Service(f.s,lambda:True,login_port=0,game_port=0,p2p_port=0,offline_adapter=True,account_uid=uid)
            services.append(service);await service.start()
            engine=service.engine;engine.grant_offline_adapter_session();b=Connection(1)
            engine.handle(b,hello(uid=uid));b.bootstrap_sent=True;engine.profile_ready(b,True)
            engine.handle(b,Message(3320,struct.pack('<I',1)));engine.disconnect(b)
            reader,writer=await asyncio.open_connection('127.0.0.1',service.game_port);writers.append(writer)
            await send(writer,hello(2010,uid=uid));self.assertEqual((await receive(reader)).id,2030)
            return reader,writer
        async def send(writer,message):writer.write(encode_game(message));await writer.drain()
        async def receive(reader):
            while True:
                h=await asyncio.wait_for(reader.readexactly(8),3)
                p=await asyncio.wait_for(reader.readexactly(struct.unpack_from('<I',h,4)[0]),3)
                m=GameDecoder().feed(h+p)[0]
                if m.id!=0:return m
        try:
            ra,wa=await connect(1001)
            await send(wa,Message(9070,b'\x19\1'));out=[await receive(ra) for _ in range(3)]
            self.assertEqual([m.id for m in out],[1240,1230,9080]);self.assertEqual(struct.unpack('<I',out[1].payload)[0],500)
            await send(wa,Message(9040,fixtures.buy()));out=[await receive(ra) for _ in range(3)]
            self.assertEqual([m.id for m in out],[1230,2160,9050]);self.assertEqual(struct.unpack('<I',out[0].payload)[0],400)
            await send(wa,Message(9090,fixtures.gift()));out=[await receive(ra) for _ in range(2)]
            self.assertEqual(out,[Message(1230,struct.pack('<I',300)),Message(9100,b'\1')])
            rb,wb=await connect(1002)
            await send(wb,Message(1300));listing=await receive(rb);self.assertEqual((listing.id,len(listing.payload)),(1310,339))
            key=struct.unpack_from('<I',listing.payload)[0]
            await send(wb,Message(1320,struct.pack('<QI',1002,key)));detail=await receive(rb);self.assertEqual(detail.id,1330)
            attachment=struct.unpack_from('<I',detail.payload,8)[0]
            for _ in range(2):
                await send(wb,Message(2171,struct.pack('<QI',1002,attachment)));inventory=await receive(rb)
                self.assertEqual((inventory.id,len(inventory.payload)),(1120,8*68))
            await send(wb,Message(1340,struct.pack('<QI',1002,key)));self.assertEqual((await receive(rb)).payload,struct.pack('<BI',1,key))
            self.assertEqual(len(f.s.snapshot(1001)[3]),8*68);self.assertEqual(len(f.s.snapshot(1002)[3]),8*68)
        finally:
            for writer in writers:writer.close();await writer.wait_closed()
            for service in services:await service.close()
            f.tearDown()
