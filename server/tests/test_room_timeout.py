"""Native4031 self-timeout, not arbitrary kick or a battle abort request."""
import struct
import unittest
from server.kk_local.engine import Phase,Engine,Connection
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_shared_rooms as fixtures
from server.tests.test_local_service import create_room,hello


class RoomTimeoutTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join
    battle=fixtures.SharedRoomTests.battle

    def test_self_timeout_notifies_both_and_no_repeat_teardown(self):
        self.join();room=self.e1.room
        m=Message(4032,struct.pack('<Q',1002))
        self.assertEqual(self.e2.handle(self.c2,Message(4031)),[m])
        self.assertEqual(self.e1.take_pending(self.c1),[m])
        self.assertIsNone(self.e2.room);self.assertEqual(self.c2.phase,Phase.LOBBY)
        self.assertEqual(list(room.members),[1001])
        self.assertEqual(self.e2.handle(self.c2,Message(4031)),[])
        self.assertEqual(self.e1.take_pending(self.c1),[])

    def test_host_timeout_transfers_room_without_destroying_other_player(self):
        self.join();room=self.e1.room
        self.assertEqual(self.e1.handle(self.c1,Message(4031)),[Message(4032,struct.pack('<Q',1001))])
        self.assertEqual([m.id for m in self.e2.take_pending(self.c2)],[4032,3160])
        self.assertEqual(room.owner,1002);self.assertIs(self.e2.room,room)

    def test_late_loading_battle_or_result_timeout_does_not_remove_member(self):
        self.battle();room=self.e1.room
        for stage,phase in (('loading',Phase.LOADING),('battle',Phase.BATTLE),('result',Phase.BATTLE)):
            room.stage=stage;self.c1.phase=phase
            self.assertEqual(self.e1.handle(self.c1,Message(4031)),[])
            self.assertEqual(len(room.members),2)

    def test_no_target_argument_and_single_endpoint_timeout(self):
        self.join()
        with self.assertRaises(ProtocolError):self.e2.handle(self.c2,Message(4031,struct.pack('<Q',1001)))
        self.assertEqual(len(self.e1.room.members),2)
        solo=Engine(self.s);solo.grant_offline_adapter_session()
        bootstrap=Connection(20);solo.handle(bootstrap,hello());bootstrap.bootstrap_sent=True
        solo.profile_ready(bootstrap,True);solo.handle(bootstrap,Message(3320,struct.pack('<I',1)))
        solo.disconnect(bootstrap);c=Connection(21);solo.handle(c,hello(2010))
        solo.register_p2p(123,123,('127.0.0.1',31000))
        solo.handle(c,Message(1156,struct.pack('<QI',1001,123)))
        solo.handle(c,create_room())
        self.assertEqual(solo.handle(c,Message(4031)),[Message(4032,struct.pack('<Q',1001))])
        self.assertIsNone(solo.room);self.assertEqual(c.phase,Phase.LOBBY)


if __name__=='__main__':unittest.main()
