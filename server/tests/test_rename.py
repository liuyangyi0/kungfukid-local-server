import struct
import unittest
from server.kk_local.store import Store
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.wire import Message
from server.tests.test_competitive_rooms import prepare_handoff
from server.tests.test_local_service import hello


def req(uid,name):return Message(9006,struct.pack('<Q',uid)+name.encode('gbk').ljust(21,b'\0'))


class RenameTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:');self.s.seed_local();self.e=Engine(self.s)
        prepare_handoff(self.e);self.c=Connection(2);self.e.handle(self.c,hello(2010))
    def tearDown(self):self.s.close()
    def test_success_repeat_and_account_unchanged(self):
        before=self.s.snapshot(1001)
        for _ in range(2):
            m=self.e.handle(self.c,req(1001,'本地侠客'))[0]
            self.assertEqual((m.id,len(m.payload)),(9007,54))
            self.assertEqual(struct.unpack_from('<Q',m.payload,25)[0],1001)
            self.assertEqual(m.payload[33:].split(b'\0',1)[0],'本地侠客'.encode('gbk'))
        after=self.s.snapshot(1001)
        self.assertEqual(after[0],before[0]);self.assertEqual(after[3],before[3])
        self.assertEqual(after[1],'本地侠客')
        self.assertEqual(after[2][:4],before[2][:4]);self.assertEqual(after[2][25:],before[2][25:])
    def test_invalid_identity_duplicate_and_room_reject(self):
        self.s.provision_local(1002,'Other','已占用')
        before=self.s.snapshot(1001)
        for m in (req(1002,'改名'),req(1001,'已占用'),req(1001,'\n坏名'),req(1001,' name'),Message(9006,b'x')):
            reply=self.e.handle(self.c,m)[0]
            self.assertEqual((reply.id,len(reply.payload)),(9008,54))
            self.assertEqual(self.s.snapshot(1001),before)
        self.c.phase=Phase.ROOM
        self.assertEqual(self.e.handle(self.c,req(1001,'新名'))[0].id,9008)
        self.assertEqual(self.s.snapshot(1001),before)
        self.assertFalse(self.s.db.in_transaction)


if __name__=='__main__':unittest.main()
