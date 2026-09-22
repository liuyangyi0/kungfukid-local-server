import asyncio
import struct
import unittest
from server.kk_local.service import Service
from server.kk_local.wire import Message,GameDecoder,encode_game
from server.tests import test_ordinary_quests as fixtures,test_quests as actions
from server.tests.test_competitive_rooms import prepare_handoff
from server.tests.test_local_service import hello


class OrdinaryQuestTCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_query_accept_complete_successor_and_retry_wire_order(self):
        f=fixtures.OrdinaryQuestTests();f.setUp();writer=None
        service=Service(f.s,lambda:True,login_port=0,game_port=0,p2p_port=0,offline_adapter=True,map_catalog=f.maps)
        async def send(m):writer.write(encode_game(m));await writer.drain()
        async def read(reader):
            while True:
                h=await asyncio.wait_for(reader.readexactly(8),3)
                p=await asyncio.wait_for(reader.readexactly(struct.unpack_from('<I',h,4)[0]),3)
                m=GameDecoder().feed(h+p)[0]
                if m.id!=0:return m
        try:
            await service.start();prepare_handoff(service.engine)
            reader,writer=await asyncio.open_connection('127.0.0.1',service.game_port)
            await send(hello(2010));self.assertEqual((await read(reader)).id,2030)
            await send(Message(6000,bytes(4)));out=[await read(reader) for _ in range(3)]
            self.assertEqual([m.id for m in out],[6020,6041,6042]);self.assertEqual(len(out[0].payload),123)
            await send(actions.action(6050,1001));self.assertEqual((await read(reader)).id,6060)
            f.finish()  #separate integration test verifies the4110 consensus producer
            await send(Message(6000,bytes(4)));out=[await read(reader) for _ in range(8)]
            self.assertEqual([m.id for m in out],[6020,6041,6042,1550,2160,1240,6030,6040])
            self.assertEqual(len(out[0].payload),123);self.assertEqual(out[0].payload[6],3)
            await send(Message(6000,bytes(4)));out=[await read(reader) for _ in range(6)]
            self.assertEqual([m.id for m in out],[6020,6041,6042,1550,2160,1240]);self.assertEqual(len(out[0].payload),246)
            self.assertEqual(f.s.gold_balance(1001),20);self.assertEqual(len(f.s.snapshot(1001)[3]),8*68)
            await send(actions.action(6050,1002));self.assertEqual((await read(reader)).id,6060)
        finally:
            if writer:writer.close();await writer.wait_closed()
            await service.close();f.tearDown()
