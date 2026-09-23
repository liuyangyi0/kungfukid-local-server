"""Replay the native 3010 -> queued2260 race seen in the two-VM trial."""
import unittest

from server.kk_local.engine import Engine, Phase
from server.kk_local.rooms import RoomHub
from server.kk_local.store import Store
from server.kk_local.wire import Message, ProtocolError
from server.tests.test_shared_rooms import lobby
from server.tests.test_local_service import create_room


class RoomDirectoryRaceTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:'); self.store.seed_local()
        self.hub = RoomHub(); self.engine = Engine(self.store, hub=self.hub)
        self.client = lobby(self.engine, 1)

    def tearDown(self):
        self.store.close()

    def test_late_poll_after_create_keeps_room_and_connection(self):
        request = bytearray(create_room().payload); request[37] = 2; request[46] = 1
        self.assertEqual([r.id for r in self.engine.handle(self.client, Message(3010, bytes(request)))], [3100,3160])
        room = self.engine.room; peer = self.engine.p2p
        before = self.store.snapshot(1001)
        self.assertEqual(self.engine.handle(self.client, Message(2260, bytes(3))), [])
        self.assertIs(self.engine.game, self.client)
        self.assertEqual(self.client.phase, Phase.ROOM)
        self.assertIs(self.engine.room, room)
        self.assertIs(self.engine.p2p, peer)
        self.assertEqual(set(room.members), {1001})
        self.assertEqual(self.store.snapshot(1001), before)
        # A real subsequent command still works; no fake directory reply.
        self.assertEqual([r.id for r in self.engine.handle(self.client, Message(3110))], [3115])
        self.assertEqual(self.engine.handle(self.client, Message(2260, bytes(3)))[0].id, 2280)

    def test_late_poll_ignored_in_loading_and_battle_but_bad_length_rejected(self):
        self.engine.handle(self.client, create_room())
        room = self.engine.room
        for phase in (Phase.ROOM, Phase.LOADING, Phase.WAIT_READY, Phase.BATTLE):
            self.client.phase = phase
            self.assertEqual(self.engine.handle(self.client, Message(2260, bytes(3))), [])
            with self.assertRaises(ProtocolError):
                self.engine.handle(self.client, Message(2260, bytes(2)))
            self.assertEqual(self.client.phase, phase)
            self.assertIs(self.engine.room, room)
