import asyncio
import struct
import unittest

from server.kk_local import packets
from server.kk_local.engine import Engine, Connection, Phase
from server.kk_local.service import Service
from server.kk_local.store import Store
from server.kk_local.wire import GameDecoder, Message, ProtocolError, encode_game
from server.tests.test_local_service import hello, create_room


def request(mode=1, chosen=-1, suggested=803):
    body=bytearray(create_room().payload)
    body[46]=mode
    struct.pack_into('<ii',body,38,chosen,suggested)
    return Message(3010,bytes(body))


def prepare_handoff(engine):
    engine.grant_offline_adapter_session()
    bootstrap=Connection(1)
    engine.handle(bootstrap,hello());bootstrap.bootstrap_sent=True
    engine.profile_ready(bootstrap,True)
    engine.handle(bootstrap,Message(3320,struct.pack('<I',1)))
    engine.disconnect(bootstrap)


class CompetitiveRoomTests(unittest.TestCase):
    def test_shared_shape_modes_and_authoritative_random_pool(self):
        for mode in (0,1,2,3,5):
            p=packets.room_entry(request(mode).payload,1001)
            self.assertEqual(len(p),245)
            self.assertEqual(p[65],mode)
            self.assertEqual(struct.unpack_from('<ii',p,12),(804,804))
            self.assertEqual(p[62],4)
            self.assertEqual(struct.unpack_from('<H',p,67)[0],180)
        with self.assertRaises(packets.RoomRequestRejected):
            packets.room_entry(request(4).payload,1001)
        with self.assertRaises(packets.RoomRequestRejected):
            packets.room_entry(request(1,803,803).payload,1001)
        with self.assertRaises(ProtocolError):
            packets.room_entry(bytes(80),1001)

    def test_actual_self_record_duplicate_leave_and_no_fake_solo_match(self):
        store=Store(':memory:');store.seed_local()
        try:
            engine=Engine(store);prepare_handoff(engine)
            c=Connection(2);engine.handle(c,hello(2010))
            engine.register_p2p(1001,1001,('127.0.0.1',50000))
            engine.handle(c,Message(1156,struct.pack('<QI',1001,1001)))
            rows=engine.handle(c,request())
            self.assertEqual([m.id for m in rows],[3100,3160])
            entry=rows[0].payload
            self.assertEqual(struct.unpack_from('<Q',entry,96)[0],1001)
            self.assertEqual(entry[107:128].split(b'\0',1)[0],store.snapshot(1001)[1].encode('gbk'))
            self.assertEqual(struct.unpack_from('<I',entry,163)[0],1001)
            self.assertEqual(engine.handle(c,request()),[])
            self.assertEqual(engine.handle(c,Message(4030)),[])
            self.assertEqual(c.phase,Phase.ROOM)
            self.assertEqual(engine.room.serial,0)
            self.assertEqual(engine.handle(c,Message(3110)),[Message(3115)])
            self.assertEqual(c.phase,Phase.LOBBY)
        finally:store.close()


class CompetitiveRoomNetworkTests(unittest.IsolatedAsyncioTestCase):
    async def test_unsupported_mode_does_not_disconnect_or_mutate_room(self):
        store=Store(':memory:');store.seed_local();events=[]
        service=Service(store,lambda:True,login_port=0,game_port=0,p2p_port=0,
                        offline_adapter=True,event_sink=events.append)
        writer=None
        try:
            await service.start();prepare_handoff(service.engine)
            reader,writer=await asyncio.open_connection('127.0.0.1',service.game_port)
            async def receive():
                while True:
                    h=await asyncio.wait_for(reader.readexactly(8),3)
                    body=await asyncio.wait_for(reader.readexactly(struct.unpack_from('<I',h,4)[0]),3)
                    m=GameDecoder().feed(h+body)[0]
                    if m.id:return m
            writer.write(encode_game(hello(2010)));await writer.drain()
            self.assertEqual((await receive()).id,2030)
            service.engine.register_p2p(1001,1001,('127.0.0.1',50000))
            writer.write(encode_game(Message(1156,struct.pack('<QI',1001,1001))))
            writer.write(encode_game(request(4))+encode_game(Message(2260,bytes(3))))
            await writer.drain()
            self.assertEqual(await receive(),Message(3030,struct.pack('<H',114)))
            self.assertEqual((await receive()).id,2280)
            self.assertIsNone(service.engine.room)
            self.assertEqual(service.engine.game.phase,Phase.LOBBY)
            writer.write(encode_game(request()));await writer.drain()
            self.assertEqual([(await receive()).id for _ in range(2)],[3100,3160])
            self.assertEqual(sum(e['event']=='room_create_rejected' for e in events),1)
            self.assertFalse(any(e['event']=='game_closed' for e in events))
        finally:
            if writer:writer.close();await writer.wait_closed()
            await service.close();store.close()


if __name__=='__main__':unittest.main()
