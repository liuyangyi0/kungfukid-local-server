import asyncio
import struct
import unittest
from server.kk_local.service import Service
from server.kk_local.wire import Message,encode_game,GameDecoder
from server.tests import test_quest_rewards as fixture,test_quests as quest_fixture
from server.tests.test_competitive_rooms import prepare_handoff
from server.tests.test_local_service import hello


class QuestRewardTCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_completed_list_then_claim_duplicate_over_real_transport(self):
        f=fixture.QuestRewardTests();f.setUp();writer=None
        service=Service(f.s,lambda:True,login_port=0,game_port=0,p2p_port=0,offline_adapter=True,map_catalog=f.maps)
        async def send(m):writer.write(encode_game(m));await writer.drain()
        async def receive(reader):
            while True:
                h=await asyncio.wait_for(reader.readexactly(8),3)
                p=await asyncio.wait_for(reader.readexactly(struct.unpack_from('<I',h,4)[0]),3)
                m=GameDecoder().feed(h+p)[0]
                if m.id!=0:return m
        try:
            f.finish()  #same persisted settlement API; the separate integration test covers4110 consensus
            await service.start();prepare_handoff(service.engine)
            reader,writer=await asyncio.open_connection('127.0.0.1',service.game_port)
            await send(hello(2010));self.assertEqual((await receive(reader)).id,2030)
            await send(Message(6000,bytes(4)));out=[await receive(reader) for _ in range(4)]
            self.assertEqual([m.id for m in out],[6020,6041,6042,6032])
            self.assertEqual(out[2].payload[16],4)
            previous=None
            for _ in range(2):
                await send(quest_fixture.action(6312,3001));out=[await receive(reader) for _ in range(4)]
                self.assertEqual([m.id for m in out],[1550,2160,1240,6302])
                self.assertEqual(struct.unpack('<i',out[2].payload)[0],50)
                if previous:self.assertEqual(out[1].payload,previous)
                previous=out[1].payload
            self.assertEqual(f.s.gold_balance(1001),50);self.assertEqual(len(f.s.snapshot(1001)[3]),8*68)
            await send(Message(6002));self.assertEqual(await receive(reader),Message(6042))
        finally:
            if writer:writer.close();await writer.wait_closed()
            await service.close();f.tearDown()
