"""Final coordinator split: one owner, ordered dispatch and atomic repositories."""
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from server.kk_local.engine import Engine, Connection, Phase
from server.kk_local.rooms import RoomHub
from server.kk_local.store import Store
from server.kk_local.wire import Message, ProtocolError


class CoordinatorBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.store.seed_local()
        self.hub = RoomHub()
        self.engine = Engine(self.store, hub=self.hub)
        self.connection = Connection(1, Phase.LOBBY, 1001)
        self.engine.game = self.connection

    def tearDown(self):
        self.store.close()

    def test_room_components_have_one_owner_not_duplicate_room_collections(self):
        for name in ('_lifecycle', '_flow', '_requests', '_battle', '_pairs', '_receipts', '_results'):
            component = getattr(self.hub, name)
            self.assertEqual(vars(component), {'hub': self.hub})
        self.assertIs(self.hub.engines[1001], self.engine)
        self.assertIs(self.engine.hub, self.hub)

    def test_handled_empty_room_request_never_reaches_flow_or_single_endpoint(self):
        self.hub.expire = Mock()
        self.hub._requests = SimpleNamespace(handle=Mock(return_value=[]))
        self.hub._flow = SimpleNamespace(handle=Mock(side_effect=AssertionError('must not run')))
        # Legacy fallback would reject the missing P2P lease or bad payload.
        self.assertEqual(self.engine.handle(self.connection, Message(3010)), [])
        self.hub.expire.assert_called_once()
        self.hub._requests.handle.assert_called_once()
        self.hub._flow.handle.assert_not_called()
        self.assertEqual(self.connection.command_sequence, 1)
        self.assertIsNone(self.engine.room)

    def test_battle_route_runs_once_after_waiting_handlers_decline(self):
        order = []
        self.hub.expire = lambda: order.append('expire')
        self.hub._requests = SimpleNamespace(handle=lambda *args: order.append('requests'))
        self.hub._flow = SimpleNamespace(handle=lambda *args: order.append('flow'))
        def battle(*args):
            order.append('battle')
            return []
        self.hub._battle = SimpleNamespace(handle=battle)
        self.assertEqual(self.engine.handle(self.connection, Message(8071, b'other')), [])
        self.assertEqual(order, ['expire', 'requests', 'flow', 'battle'])
        self.assertEqual(self.engine.unknown, {})

    def test_room_error_is_not_reinterpreted_as_a_fallback(self):
        self.hub._requests = SimpleNamespace(handle=Mock(side_effect=ProtocolError('room rejected')))
        self.hub._flow = SimpleNamespace(handle=Mock())
        with self.assertRaisesRegex(ProtocolError, 'room rejected'):
            self.engine.handle(self.connection, Message(3070))
        self.hub._flow.handle.assert_not_called()
        self.assertIsNone(self.engine.room)

    def test_remaining_repository_writes_require_owned_transaction(self):
        s = self.store
        calls = (
            lambda: s.inventory.delete(1001, 1),
            lambda: s.renewal.configure_offer(1, 1, bytes(108)),
            lambda: s.renewal.register_lease(1001, 1, 100),
            lambda: s.renewal.update_lease(1001, 1, 100),
            lambda: s.renewal.record_receipt(1001, 'test', b'bytes', 1, 100),
            lambda: s.renewal.hide_reminder(1001, 1, 100),
            lambda: s.talisman.replace_repair_rules([]),
            lambda: s.talisman.record_repair(1001, 'test', b'bytes'),
            lambda: s.talisman.record_use(1001, 1, 1, 8291, 0, 1),
            lambda: s.upgrades.set_settings(1, '{}'),
            lambda: s.upgrades.record_attempt(1001, 'test', 1, 1, 1, 1, 1, 1, bytes(68), bytes(68)),
        )
        before = s.snapshot(1001)
        for call in calls:
            with self.assertRaises(ValueError):
                call()
            self.assertFalse(s.in_transaction)
        self.assertEqual(s.snapshot(1001), before)
        self.assertIsNone(s.upgrades.settings())
        self.assertEqual(s.renewal.offers(), [])
