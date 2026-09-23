import asyncio
import struct
import unittest
from server.kk_local.service import Service
from server.kk_local.wire import Message,encode_game,GameDecoder
from server.tests import test_quests as fixture
from server.tests.test_competitive_rooms import prepare_handoff
from server.tests.test_local_service import hello


class QuestTCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_accept_retry_cancel_and_unqualified_claim(self):
        f=fixture.QuestTests();f.setUp();writer=None
        service=Service(f.s,lambda:True,login_port=0,game_port=0,p2p_port=0,
                        offline_adapter=True,map_catalog=f.maps)
        async def send(message):writer.write(encode_game(message));await writer.drain()
        async def receive(reader):
            while True:
                h=await asyncio.wait_for(reader.readexactly(8),3)
                p=await asyncio.wait_for(reader.readexactly(struct.unpack_from('<I',h,4)[0]),3)
                m=GameDecoder().feed(h+p)[0]
                if m.id!=0:return m
        async def query(reader):
            await send(Message(6000,bytes(4)))
            out=[await receive(reader) for _ in range(3)]
            self.assertEqual([m.id for m in out],[6020,6041,6042]);return out
        try:
            await service.start();prepare_handoff(service.engine)
            reader,writer=await asyncio.open_connection('127.0.0.1',service.game_port)
            await send(hello(2010));self.assertEqual((await receive(reader)).id,2030)
            before=f.s.snapshot(1001);self.assertEqual((await query(reader))[0].payload[6],1)
            await send(fixture.action(6050,1001));self.assertEqual((await receive(reader)).id,6060)
            await send(fixture.action(6050,1001));self.assertNotEqual((await receive(reader)).id,6060)
            self.assertEqual((await query(reader))[0].payload[6],2)
            await send(fixture.action(6080,1001));self.assertEqual((await receive(reader)).id,6090)
            await send(fixture.action(6052,3001));self.assertEqual((await receive(reader)).payload,struct.pack('<HB',3001,2))
            await send(fixture.action(6312,3001));self.assertNotIn((await receive(reader)).id,(2160,1240,6302))
            out=await query(reader);self.assertEqual(out[0].payload[6],1);self.assertEqual(out[2].payload[16],2)
            self.assertEqual(f.s.snapshot(1001),before)
        finally:
            if writer:writer.close();await writer.wait_closed()
            await service.close();f.tearDown()
