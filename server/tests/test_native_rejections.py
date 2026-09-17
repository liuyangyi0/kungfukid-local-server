import struct
import unittest
from server.kk_local import packets
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.store import Store
from server.kk_local.wire import Message

class NativeRejectionTests(unittest.TestCase):
    def test_qualified_error_table_codes(self):
        self.assertEqual(packets.room_rejection('unknown_map_id'),Message(3030,b',\0'))
        self.assertEqual(packets.room_rejection('unsupported_room_mode'),Message(3030,b'r\0'))
        self.assertEqual(packets.room_rejection(),Message(3030,b'\x82\0'))
        self.assertEqual(packets.equipment_rejection('item not owned'),Message(2100,b'H\0'))
        self.assertEqual(packets.equipment_rejection('unqualified item slot'),Message(2100,b'&\0'))

    def test_equipment_denial_does_not_mutate_or_echo_request(self):
        s=Store(':memory:');s.seed_local()
        try:
            e=Engine(s);c=Connection(1,Phase.LOBBY,1001);e.game=c;before=s.snapshot(1001)
            p=struct.pack('<4I',0xffffffff,8,0xffffffff,0xffffffff)
            self.assertEqual(e.handle(c,Message(2080,p)),[Message(2100,struct.pack('<H',72))])
            self.assertEqual(s.snapshot(1001),before)
            self.assertEqual(e.handle(Connection(2,Phase.LOBBY,1001),Message(2080,p)),[])
        finally:s.close()
