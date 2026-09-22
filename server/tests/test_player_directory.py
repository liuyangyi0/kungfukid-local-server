"""Real local online entries; no invented public rank or inventory leakage."""
import struct
import unittest
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.wire import Message,ProtocolError
from server.kk_local import packets
from server.tests import test_shared_rooms as fixtures


def query(page=1,selector=10):
    return Message(2250,struct.pack('<II',page,selector))


class PlayerDirectoryTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join

    def test_online_rows_are_not_inventory_and_use_real_names(self):
        out=self.e1.handle(self.c1,query())[0]
        self.assertEqual((out.id,len(out.payload)),(2270,8+2*68))
        self.assertEqual(struct.unpack_from('<II',out.payload),(1,1))
        rows=[out.payload[8+i*68:8+(i+1)*68] for i in range(2)]
        self.assertEqual([struct.unpack_from('<Q',r)[0] for r in rows],[1001,1002])
        self.assertEqual(rows[1][8:29].split(b'\0')[0],b'Second')
        self.assertEqual(rows[1][29:],bytes(39))
        # Not a profile dump, private currency, credentials or item records.
        self.assertNotIn(self.s.snapshot(1002)[2],out.payload)
        self.assertNotIn(self.s.snapshot(1002)[3],out.payload)

    def test_room_and_battle_presence_not_offline_or_handoff_accounts(self):
        self.e2.handle(self.c2,fixtures.create_room())
        out=self.e1.handle(self.c1,query())[0].payload
        self.assertEqual(out[8+68+46],1)
        self.c2.phase=Phase.BATTLE
        self.assertEqual(len(self.e1.handle(self.c1,query())[0].payload),144)
        for phase in (Phase.HANDOFF,Phase.CLOSED,Phase.BOOTSTRAP):
            self.c2.phase=phase
            self.assertEqual(len(self.e1.handle(self.c1,query())[0].payload),76)
        self.c2.phase=Phase.LOBBY;self.e2.delivery_failed=True
        self.assertEqual(len(self.e1.handle(self.c1,query())[0].payload),76)

    def test_page_and_selector_do_not_alias_room_or_pve_consumers(self):
        self.assertEqual(self.e1.handle(self.c1,query(0)),self.e1.handle(self.c1,query(1)))
        self.assertEqual(self.e1.handle(self.c1,query(2)),[Message(2270,struct.pack('<II',2,1))])
        self.assertEqual(self.e1.handle(self.c1,query(0xffffffff)),[Message(2270,struct.pack('<II',0xffffffff,1))])
        self.assertEqual(self.e1.handle(self.c1,query(selector=7)),[])
        self.join()
        self.assertEqual(self.e1.handle(self.c1,query()),[])

    def test_single_endpoint_only_self_and_invalid_request_is_nonmutating(self):
        e=Engine(self.s);c=Connection(8,Phase.LOBBY,1001);e.game=c
        self.assertEqual(len(e.handle(c,query())[0].payload),76)
        self.assertEqual(e.handle(Connection(9,Phase.LOBBY,1001),query()),[])
        for p in (b'',bytes(4),bytes(9)):
            with self.assertRaises(ProtocolError):e.handle(c,Message(2250,p))
        self.assertIsNone(e.room)

    def test_wire_constructor_bounds_and_chinese_name(self):
        row=packets.player_directory_record((1<<40)+1,'少侠',in_room=True)
        self.assertEqual(struct.unpack_from('<Q',row)[0],(1<<40)+1)
        self.assertEqual(row[8:29].split(b'\0')[0].decode('gbk'),'少侠')
        self.assertEqual(row[46],1)
        for rows in ([row]*11,[b'bad']):
            with self.assertRaises(ProtocolError):packets.player_directory(rows)
        for name in ('','x'*21,'a\0b'):
            with self.assertRaises(ProtocolError):packets.player_directory_record(1,name)


if __name__=='__main__':unittest.main()
