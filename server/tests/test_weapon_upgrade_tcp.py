import asyncio
import struct
import unittest
from server.kk_local.service import Service
from server.kk_local.wire import Message,GameDecoder,encode_game
from server.kk_local import weapon_upgrade as upgrade
from server.tests import test_weapon_upgrade as fixture
from server.tests.test_competitive_rooms import prepare_handoff
from server.tests.test_local_service import hello


class WeaponUpgradeTCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_bootstrap_same_table_and_upgrade_inventory_precedes_result(self):
        f=fixture.WeaponUpgradeTests();f.setUp();writer=None
        service=Service(f.s,lambda:True,login_port=0,game_port=0,p2p_port=0,offline_adapter=True)
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
            table=await read(reader);self.assertEqual((table.id,len(table.payload)),(21411,63))
            await send(Message(21410));self.assertEqual(await read(reader),table)
            await send(Message(21412,struct.pack('<I',f.instance)))
            out=[await read(reader) for _ in range(3)];self.assertEqual([m.id for m in out],[1240,2161,21413])
            self.assertEqual(struct.unpack_from('<II',out[1].payload,43),(1,70));self.assertEqual(out[2].payload[0],1)
            upgrade.configure(f.s,fixture.rules(0),1)
            await send(Message(21412,struct.pack('<I',f.instance)))
            self.assertNotEqual((await read(reader)).id,21413);self.assertEqual(f.s.gold_balance(1001),180)
            await send(Message(21410));self.assertEqual((await read(reader)).id,21411)
            await send(Message(21412,struct.pack('<I',f.instance)))
            out=[await read(reader) for _ in range(3)];self.assertEqual([m.id for m in out],[1240,2161,21413])
            self.assertEqual(struct.unpack_from('<II',out[1].payload,43),(1,50));self.assertEqual(out[2].payload[0],0)
            self.assertEqual(f.s.gold_balance(1001),150)
        finally:
            if writer:writer.close();await writer.wait_closed()
            await service.close();f.tearDown()
