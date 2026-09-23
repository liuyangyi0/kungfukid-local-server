import asyncio
import struct
import unittest
from server.kk_local.service import Service
from server.kk_local.wire import Message,encode_game,GameDecoder
from server.kk_local.title_rewards import configure
from server.tests import test_tutorial as fixture
from server.tests.test_title_rewards import choice
from server.tests.test_competitive_rooms import prepare_handoff
from server.tests.test_local_service import hello


class TutorialTCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_private_flow_catalogue_claim_and_duplicate(self):
        f=fixture.TutorialTests();f.setUp()
        #Use the fixture store/catalog but a fresh hub so no preexisting fake GS.
        from server.kk_local.rooms import RoomHub
        hub=RoomHub(network_probe=True)
        configure(f.s,2,[choice()],f.maps.title_levels)
        service=Service(f.s,lambda:True,login_port=0,game_port=0,p2p_port=0,offline_adapter=True,hub=hub,map_catalog=f.maps)
        writer=None
        async def send(m):writer.write(encode_game(m));await writer.drain()
        async def receive(reader):
            while True:
                h=await asyncio.wait_for(reader.readexactly(8),3)
                b=await asyncio.wait_for(reader.readexactly(struct.unpack_from('<I',h,4)[0]),3)
                m=GameDecoder().feed(h+b)[0]
                if m.id not in (0,8090):return m
        try:
            await service.start();prepare_handoff(service.engine)
            reader,writer=await asyncio.open_connection('127.0.0.1',service.game_port)
            await send(hello(2010));self.assertEqual((await receive(reader)).id,2030)
            await send(fixture.request());created=await receive(reader)
            self.assertEqual((created.id,len(created.payload)),(3020,83))
            await send(Message(3550,struct.pack('<QI',1001,0)))
            await send(Message(3070,struct.pack('<HB11s',1,0,b'')))
            self.assertEqual([(await receive(reader)).id for _ in range(2)],[3100,3160])
            await send(Message(4030));self.assertEqual([(await receive(reader)).id for _ in range(2)],[4050,4080])
            await send(Message(4160));self.assertEqual([(await receive(reader)).id for _ in range(2)],[4170,4180])
            await send(Message(8040,struct.pack('<HQI',1,1001,0)));self.assertEqual((await receive(reader)).id,8070)
            self.assertIsNone(service.engine.p2p)
            await send(Message(4124));out=[await receive(reader) for _ in range(3)]
            self.assertEqual([m.id for m in out],[1550,4125,3115]);self.assertEqual(out[1].payload[0],2)
            p=bytearray(149);struct.pack_into('<I',p,145,25303001)
            for _ in range(2):
                await send(Message(4126,bytes(p)));self.assertEqual((await receive(reader)).id,2160)
                await receive(reader)  #local confirmation text, not an invented4127
            self.assertEqual(len(f.s.snapshot(1001)[3]),8*68)
            await send(fixture.request());self.assertEqual([(await receive(reader)).id for _ in range(2)],[4125,3115])
            self.assertFalse(hub.rooms)
        finally:
            if writer:writer.close();await writer.wait_closed()
            await service.close();f.tearDown()
