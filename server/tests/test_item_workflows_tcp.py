"""Actual loopback Service framing/routing of repair, mail and renewal."""
import asyncio
import struct
import unittest
from server.kk_local.service import Service
from server.kk_local.wire import Message,GameDecoder,encode_game
from server.tests.test_competitive_rooms import prepare_handoff
from server.tests.test_local_service import hello
from server.tests import test_talisman_repair as repair_fixture
from server.kk_local import mailbox,renewal


class ItemWorkflowTCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_repair_mail_claim_delete_and_renewal_on_same_connection(self):
        fixture=repair_fixture.RepairTests();fixture.setUp();s=fixture.s
        catalog=bytearray(108);catalog[4]=25;catalog[48]=1
        struct.pack_into('<II',catalog,5,253030,25303001);struct.pack_into('<II',catalog,38,100,100)
        s.replace_shop_catalog(25,1,[bytes(catalog)])
        renewal.enable_offer(s,25303001,7);renewal.set_tickets(s,1001,500)
        renewal.register_lease(s,1001,0x100006,100000)
        s.unequip(1001,0x100006)
        item=bytearray(68);item[4]=25;struct.pack_into('<I',item,5,253030)
        key=mailbox.deliver(s,1001,'tcp',title='Test',sender='Local',body='hello',catalog=bytes(catalog),grant=bytes(item))
        service=Service(s,lambda:True,login_port=0,game_port=0,p2p_port=0,offline_adapter=True)
        writer=None
        try:
            await service.start();prepare_handoff(service.engine)
            reader,writer=await asyncio.open_connection('127.0.0.1',service.game_port)
            async def send(ident,p=b''):
                writer.write(encode_game(Message(ident,p)));await writer.drain()
            async def receive():
                while True:
                    h=await asyncio.wait_for(reader.readexactly(8),3)
                    b=await asyncio.wait_for(reader.readexactly(struct.unpack_from('<I',h,4)[0]),3)
                    m=GameDecoder().feed(h+b)[0]
                    if m.id:return m
            writer.write(encode_game(hello(2010)));await writer.drain()
            self.assertEqual((await receive()).id,2030)
            await send(4202,struct.pack('<I',2000000))
            self.assertEqual((await receive()).id,4203)
            await send(4204,struct.pack('<III',2000000,601001,0))
            self.assertEqual([(await receive()).id for _ in range(2)],[1120,4205])
            await send(1300);self.assertEqual((await receive()).id,1310)
            await send(1320,struct.pack('<QI',1001,key));detail=await receive()
            attachment=struct.unpack_from('<I',detail.payload,8)[0]
            #Native issues both commands without waiting for claim response.
            await send(2171,struct.pack('<QI',1001,attachment));await send(1340,struct.pack('<QI',1001,key))
            self.assertEqual([(await receive()).id for _ in range(2)],[1120,1350])
            await send(1500,struct.pack('<BII',25,253030,1))
            self.assertEqual([(await receive()).id for _ in range(2)],[1230,1510])
            p=bytearray(173);struct.pack_into('<IIQ',p,0,0x100006,105,1001)
            struct.pack_into('<Q',p,58,1001);struct.pack_into('<I',p,149,25303001);struct.pack_into('<I',p,161,100)
            for _ in range(2):
                await send(1420,bytes(p));out=[await receive() for _ in range(3)]
                self.assertEqual([m.id for m in out],[2161,1230,1430])
                self.assertEqual(struct.unpack('<I',out[1].payload)[0],400)
            self.assertEqual(s.db.execute('SELECT COUNT(*) FROM renewal_receipts').fetchone()[0],1)
            self.assertEqual(s.db.execute('PRAGMA foreign_key_check').fetchall(),[])
        finally:
            if writer:writer.close();await writer.wait_closed()
            await service.close();fixture.tearDown()
