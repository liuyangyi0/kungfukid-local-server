"""Routing ownership, refusal precedence and exactly-once side-effect boundaries."""
from contextlib import ExitStack
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from server.kk_local.engine import Engine, Connection, Phase
from server.kk_local.handlers import dispatch, social, lobby, directory, equipment, training
from server.kk_local.store import Store
from server.kk_local.wire import Message, ProtocolError


class HandlerDispatchTests(unittest.TestCase):
    def setUp(self):
        self.store = Store(':memory:')
        self.store.seed_local()
        self.engine = Engine(self.store)
        self.connection = Connection(2, Phase.LOBBY, 1001)
        self.engine.game = self.connection

    def tearDown(self):
        self.store.close()

    def test_handled_empty_stops_chain_but_none_continues(self):
        first, handled, forbidden = Mock(return_value=None), Mock(return_value=[]), Mock()
        self.assertEqual(dispatch((first, handled, forbidden), self.engine, self.connection, Message(1)), [])
        first.assert_called_once()
        handled.assert_called_once()
        forbidden.assert_not_called()
        self.assertIsNone(dispatch((first,), self.engine, self.connection, Message(1)))

    def test_handler_error_is_not_swallowed_as_unhandled(self):
        failed, forbidden = Mock(side_effect=ProtocolError('bad shape')), Mock()
        with self.assertRaisesRegex(ProtocolError, 'bad shape'):
            dispatch((failed, forbidden), self.engine, self.connection, Message(1))
        forbidden.assert_not_called()

    def test_no_handler_runs_before_active_connection_and_identity_gates(self):
        with ExitStack() as stack:
            handlers = [stack.enter_context(patch.object(module, 'handle'))
                        for module in (social, lobby, directory, equipment, training)]
            messages = (Message(5002), Message(9070, b'\x19\0'),
                        Message(2250, struct.pack('<II', 1, 10)),
                        Message(2080, bytes(16)), Message(21000, struct.pack('<Q', 1001)))
            for message in messages:
                retired = Connection(20, Phase.LOBBY, 1001)
                self.assertEqual(self.engine.handle(retired, message), [])
                with self.assertRaises(ProtocolError):
                    self.engine.handle(Connection(21), message)
                with self.assertRaises(ProtocolError):
                    self.engine.handle(Connection(22, Phase.CLOSED, 1001), message)
            for handler in handlers:
                handler.assert_not_called()
        self.assertEqual(sum(self.engine.unknown.values()), 5)

    def test_hub_empty_reply_keeps_precedence_over_equipment_and_training(self):
        hub = SimpleNamespace(handle=Mock(return_value=[]))
        self.engine.hub = hub
        with patch.object(equipment, 'handle') as equip, patch.object(training, 'handle') as train:
            for message in (Message(2080, bytes(16)), Message(21002)):
                self.assertEqual(self.engine.handle(self.connection, message), [])
            equip.assert_not_called()
            train.assert_not_called()
        self.assertEqual(hub.handle.call_count, 2)
        self.assertEqual(self.engine.unknown, {})

    def test_menu_still_finishes_before_hub_and_sequence_is_not_incremented_twice(self):
        hub = SimpleNamespace(handle=Mock(side_effect=AssertionError('unexpected hub dispatch')))
        self.engine.hub = hub
        self.engine.transaction_namespace = 'test-only'
        self.connection.command_sequence = 8
        result = object()
        with patch('server.kk_local.shop.purchase', return_value=result) as buy, \
                patch('server.kk_local.shop.purchase_packets', return_value=[]) as replies:
            payload = bytes(169)
            self.assertEqual(self.engine.handle(self.connection, Message(9040, payload)), [])
            buy.assert_called_once_with(self.store, 1001, 'test-only:2:9', payload)
            replies.assert_called_once_with(result)
        hub.handle.assert_not_called()
        self.assertEqual(self.connection.command_sequence, 9)
        self.assertEqual(self.engine.layout_observations[9040], 1)
        self.assertEqual(self.engine.unknown, {})

    def test_phase_and_feature_gates_do_not_become_a_global_lobby_only_rule(self):
        self.connection.phase = Phase.BATTLE
        # Legacy training query/start is not globally restricted to the lobby.
        query = Message(21000, struct.pack('<Q', 1001))
        self.assertEqual([m.id for m in self.engine.handle(self.connection, query)], [21001])
        # Disabled claim falls through to unknown; enabled claim outside a
        # permitted phase is a handled denial, not an unknown protocol.
        self.assertEqual(self.engine.handle(self.connection, Message(21006)), [])
        self.assertEqual(self.engine.unknown[(Phase.BATTLE.value, 21006, 0)], 1)
        self.engine.training_rewards = True
        self.assertEqual(self.engine.handle(self.connection, Message(21006)), [])
        self.assertEqual(self.engine.unknown[(Phase.BATTLE.value, 21006, 0)], 1)
        self.assertEqual(self.engine.handle(self.connection, Message(9070, b'\x19\0')), [])
        self.assertEqual(self.engine.unknown[(Phase.BATTLE.value, 9070, 2)], 1)

    def test_directory_handled_denial_is_not_recorded_twice(self):
        request = Message(2250, struct.pack('<II', 1, 7))
        self.assertEqual(self.engine.handle(self.connection, request), [])
        self.assertEqual(self.engine.unknown[(Phase.LOBBY.value, 2250, 8)], 1)
        self.assertEqual(self.connection.command_sequence, 1)
