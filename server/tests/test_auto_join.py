import struct
import unittest
from server.kk_local.engine import Engine,Phase
from server.kk_local.rooms import RoomHub
from server.kk_local.store import Store
from server.kk_local.wire import Message,ProtocolError
from server.tests.test_shared_rooms import lobby
from server.tests.test_local_service import create_room

class AutoJoinTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:');self.s.seed_local();self.s.provision_local(1002,'Peer')
        self.h=RoomHub();self.a=Engine(self.s,hub=self.h);self.b=Engine(self.s,hub=self.h,account_uid=1002)
        self.ca=lobby(self.a,1);self.cb=lobby(self.b,3)
    def tearDown(self):self.s.close()
    def test_actual_room_and_roster_are_reused(self):
        self.assertEqual(self.b.handle(self.cb,Message(3075,b'\0'))[0].id,20150)
        self.a.handle(self.ca,create_room())
        self.assertEqual(self.b.handle(self.cb,Message(3075,b'\1'))[0].id,20150)
        out=self.b.handle(self.cb,Message(3075,b'\5'))
        self.assertEqual([m.id for m in out],[3100,3160,3090])
        self.assertIs(self.a.room,self.b.room)
        self.assertEqual([m.id for m in self.a.take_pending(self.ca)],[3090])
        self.assertEqual(self.b.handle(self.cb,Message(3075,b'\0'))[0].id,20150)
        self.assertEqual(len(self.a.room.members),2)
    def test_locked_loading_and_stale_peer_not_joined(self):
        p=bytearray(create_room().payload);p[21:24]=b'key'
        self.a.handle(self.ca,Message(3010,bytes(p)));room=self.a.room
        self.assertEqual(self.b.handle(self.cb,Message(3075,b'\0'))[0].id,20150)
        p[21:24]=bytes(3);room.request=bytes(p);room.stage='loading'
        self.assertEqual(self.b.handle(self.cb,Message(3075,b'\0'))[0].id,20150)
        room.stage='room';self.a.p2p['expires']=-1
        self.assertEqual(self.b.handle(self.cb,Message(3075,b'\0'))[0].id,20150)
        self.assertEqual(self.cb.phase,Phase.LOBBY);self.assertIsNone(self.b.room)
        with self.assertRaises(ProtocolError):self.b.handle(self.cb,Message(3075,b''))
