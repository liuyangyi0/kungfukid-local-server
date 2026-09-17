import struct
import unittest
from server.kk_local.engine import Engine,Phase
from server.kk_local.rooms import RoomHub
from server.kk_local.store import Store
from server.kk_local.wire import Message,ProtocolError
from server.tests.test_shared_rooms import lobby
from server.tests.test_local_service import create_room

class RoomRemovalTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:');self.s.seed_local();self.s.provision_local(1002,'Second')
        self.h=RoomHub();self.a=Engine(self.s,hub=self.h);self.b=Engine(self.s,hub=self.h,account_uid=1002)
        self.ca=lobby(self.a,1);self.cb=lobby(self.b,3)
        self.a.handle(self.ca,create_room())
        self.b.handle(self.cb,Message(3070,struct.pack('<HB11s',1,0,b'')))
        self.a.take_pending(self.ca)
    def tearDown(self):self.s.close()

    def test_one_native_departure_per_recipient_and_preparation_reset(self):
        room=self.a.room;room.members[1001].ready=True;room.members[1002].ready=True
        snapshot=self.s.snapshot(1002)
        out=self.a.handle(self.ca,Message(3140,struct.pack('<QB',1002,0)))
        self.assertEqual([m.id for m in out],[3150,4070])
        self.assertEqual(self.b.take_pending(self.cb),[Message(3150,struct.pack('<QB',1002,0))])
        self.assertEqual(self.cb.phase,Phase.LOBBY);self.assertIsNone(self.b.room)
        self.assertEqual(list(room.members),[1001]);self.assertFalse(room.members[1001].ready)
        self.assertEqual(self.s.snapshot(1002),snapshot)
        again=self.a.handle(self.ca,Message(3140,struct.pack('<QB',1002,0)))
        self.assertEqual([m.id for m in again],[20150]);self.assertEqual(self.b.take_pending(self.cb),[])

    def test_owner_scope_privilege_flag_and_loading_guards(self):
        for e,c,target,flag in ((self.b,self.cb,1001,0),(self.a,self.ca,1001,0),
                                (self.a,self.ca,9999,0),(self.a,self.ca,1002,1)):
            self.assertEqual(e.handle(c,Message(3140,struct.pack('<QB',target,flag)))[0].id,20150)
            self.assertEqual(len(self.a.room.members),2)
        self.a.room.stage='loading';self.ca.phase=Phase.LOADING
        self.assertEqual(self.a.handle(self.ca,Message(3140,struct.pack('<QB',1002,0)))[0].id,20150)
        with self.assertRaises(ProtocolError):self.a.handle(self.ca,Message(3140,b''))
        self.assertIsNotNone(self.b.room)
