"""No fake spectator success until roster/loading/settlement support exists."""
import struct
import unittest
from server.kk_local.engine import Phase
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_shared_rooms as fixtures


class SpectatorToggleBoundaryTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join

    def test_zero_capacity_releases_native_ui_without_migrating_player(self):
        self.join();room=self.e1.room;before=tuple(room.members.items())
        for _ in range(2):
            self.assertEqual(self.e1.handle(self.c1,Message(3091)),
                             [Message(3092,struct.pack('<Qi',1001,-2))])
        self.assertEqual(tuple(room.members.items()),before)
        self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(room.owner,1001)

    def test_only_room_selector_and_empty_request(self):
        self.assertEqual(self.e1.handle(self.c1,Message(3091)),[])
        self.join();room=self.e1.room
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,Message(3091,b'\1'))
        room.stage='loading'
        self.assertEqual(self.e1.handle(self.c1,Message(3091))[0].payload,struct.pack('<Qi',1001,-3))
        self.c1.phase=Phase.BATTLE
        self.assertEqual(self.e1.handle(self.c1,Message(3091)),[])


if __name__=='__main__':unittest.main()
