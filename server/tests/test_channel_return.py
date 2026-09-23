"""2060/2070 reconnect without reusing the retired connection or P2P lease."""
import unittest
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.store import Store
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_shared_rooms as fixtures
from server.tests.test_local_service import hello


class ChannelReturnTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join

    def test_lobby_return_ack_reconnect_and_old_close_preserves_new_connection(self):
        before=self.s.snapshot(1001)
        self.assertEqual(self.e1.handle(self.c1,Message(2060)),[Message(2070)])
        self.assertEqual(self.c1.phase,Phase.CLOSED)
        self.assertIsNone(self.e1.game);self.assertIsNone(self.e1.p2p)
        new=Connection(20)
        self.assertEqual([m.id for m in self.e1.handle(new,hello(2010,1001))],[2030])
        self.e1.disconnect(self.c1)  # old TCP finally may run after the reconnect
        self.assertIs(self.e1.game,new)
        self.assertEqual(self.e1.game.phase,Phase.LOBBY)
        self.assertEqual(self.s.snapshot(1001),before)
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,Message(0))

    def test_return_ticket_expires_and_cannot_be_used_by_other_uid(self):
        now=[100.0];self.e1.clock=lambda:now[0]
        self.e1.handle(self.c1,Message(2060))
        with self.assertRaises(ProtocolError):self.e1.handle(Connection(20),hello(2010,1002))
        now[0]=221
        with self.assertRaises(ProtocolError):self.e1.handle(Connection(21),hello(2010,1001))

    def test_room_return_is_rejected_without_teardown(self):
        self.join();room=self.e1.room
        out=self.e1.handle(self.c1,Message(2060))
        self.assertTrue(out);self.assertNotIn(2070,[m.id for m in out])
        self.assertIs(self.e1.room,room);self.assertEqual(self.c1.phase,Phase.ROOM)
        self.assertIs(self.e1.game,self.c1)

    def test_wrong_length_does_not_close_session(self):
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,Message(2060,b'x'))
        self.assertIs(self.e1.game,self.c1)

    def test_bootstrap_cancel_revokes_pending_local_grant(self):
        store=Store(':memory:');store.seed_local()
        try:
            engine=Engine(store);engine.grant_offline_adapter_session()
            c=Connection(50);engine.handle(c,hello())
            self.assertEqual(engine.handle(c,Message(2060)),[Message(2070)])
            self.assertEqual(c.phase,Phase.CLOSED)
            self.assertIsNone(engine.bootstrap);self.assertIsNone(engine.pending_handoff)
            self.assertEqual(engine.grant_until,0)
            with self.assertRaises(ProtocolError):engine.handle(Connection(51),hello())
        finally:store.close()


if __name__=='__main__':unittest.main()
