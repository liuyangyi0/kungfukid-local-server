"""Truthful empty expiry list under the current local entitlement policy."""
import unittest
from server.kk_local.engine import Phase
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_shared_rooms as fixtures


class ExpiryQueryTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join

    def test_lobby_and_room_return_count_not_inventory_records(self):
        before=self.s.snapshot(1001)
        self.assertEqual(self.e1.handle(self.c1,Message(2110)),[Message(2120,bytes(4))])
        self.join()
        self.assertEqual(self.e1.handle(self.c1,Message(2110)),[Message(2120,bytes(4))])
        self.assertEqual(self.s.snapshot(1001),before)

    def test_wrong_shape_and_battle_phase(self):
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,Message(2110,bytes(4)))
        self.c1.phase=Phase.BATTLE
        self.assertEqual(self.e1.handle(self.c1,Message(2110)),[])


if __name__=='__main__':unittest.main()
