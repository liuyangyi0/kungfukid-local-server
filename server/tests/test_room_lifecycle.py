"""Membership preparation, reconnect rollback and scoped lifecycle cleanup."""
import struct
import unittest
from unittest.mock import patch

from server.kk_local.engine import Connection, Phase
from server.kk_local.wire import Message, ProtocolError
from server.tests import test_shared_rooms as fixtures
from server.tests.test_local_service import create_room


class RoomLifecycleTests(unittest.TestCase):
    def setUp(self):
        fixtures.SharedRoomTests.setUp(self)
        self.now = 100.0
        self.e1.clock = self.e2.clock = lambda: self.now
        self.e1.p2p['expires'] = self.e2.p2p['expires'] = 1000.0

    tearDown = fixtures.SharedRoomTests.tearDown
    join = fixtures.SharedRoomTests.join

    def test_failed_admission_keeps_members_invites_readiness_and_queues(self):
        self.e1.handle(self.c1, create_room())
        room = self.e1.room
        room.members[1001].ready = True
        invitation = dict(inviter=1001, expires=200.0)
        self.h.invites[1002] = invitation
        fighter = self.h.fighter

        def fail_peer(uid, member):
            if uid == 1001:
                raise ProtocolError('peer appearance unavailable')
            return fighter(uid, member)

        with patch.object(self.h, 'fighter', side_effect=fail_peer):
            with self.assertRaisesRegex(ProtocolError, 'appearance'):
                self.h.install(room, self.e2)
        self.assertEqual(set(room.members), {1001})
        self.assertEqual(room.owner, 1001)
        self.assertTrue(room.members[1001].ready)
        self.assertIs(self.h.invites[1002], invitation)
        self.assertIsNone(self.e2.room)
        self.assertEqual(self.c2.phase, Phase.LOBBY)
        self.assertEqual(self.e1.take_pending(self.c1), [])
        self.assertEqual(self.e2.take_pending(self.c2), [])

    def test_failed_restore_does_not_clear_activity_or_commit_membership(self):
        self.join()
        room = self.e2.room
        member = room.members[1002]
        member.activity = 2
        self.e2.disconnect(self.c2)
        self.e1.take_pending(self.c1)
        deadline = self.h.suspended[1002]
        self.c2 = Connection(20, Phase.LOBBY, 1002)
        self.e2.game = self.c2
        self.e2.register_p2p(3000, 3000, ('127.0.0.1', 30000))
        self.e2.p2p['bound'] = True
        fighter = self.h.fighter

        def fail_peer(uid, value):
            if uid == 1001:
                raise ProtocolError('peer snapshot unavailable')
            return fighter(uid, value)

        with patch.object(self.h, 'fighter', side_effect=fail_peer):
            with self.assertRaisesRegex(ProtocolError, 'snapshot'):
                self.h.restore_bound(self.e2)
        self.assertEqual(member.activity, 2)
        self.assertEqual(self.h.suspended[1002], deadline)
        self.assertEqual(self.c2.phase, Phase.LOBBY)
        self.assertIs(self.e2.room, room)
        self.assertEqual(self.e1.take_pending(self.c1), [])
        self.assertEqual(self.e2.take_pending(self.c2), [])
        with patch.object(self.h, 'fighter', wraps=fighter) as captured:
            replies = self.h.restore_bound(self.e2)
        self.assertEqual([m.id for m in replies], [3100, 3160, 3090])
        self.assertEqual([call.args[0] for call in captured.call_args_list], [1002, 1001])
        self.assertEqual(member.activity, 0)
        self.assertNotIn(1002, self.h.suspended)
        self.assertEqual(self.c2.phase, Phase.ROOM)
        self.assertEqual([m.id for m in self.e1.take_pending(self.c1)], [3090])

    def test_leave_cleans_departing_identity_without_erasing_peer_state(self):
        self.join()
        room = self.e1.room
        room.members[1002].ready = True
        room.loaded.update((1001, 1002))
        room.input_ready.update((1001, 1002))
        room.last_sequence.update({(1001, 1): 7, (1002, 1): 8})
        room.active_states.update({(1001, 1): 'own', (1002, 1): 'peer'})
        room.projectiles.update({1: dict(owner=1001), 2: dict(owner=1002)})
        room.pending_hit_receipts.update({1001: [(1002, 'own')], 1002: [(1001, 'departed'), (1002, 'keep')]})
        room.pending_death_receipts.update({1002: [(1001, 'departed'), (1002, 'keep')]})
        room.pair_selections[1002] = (1001, 'paired')
        room.motion_payloads.update({1001: b'own', 1002: b'peer'})
        self.h.invites.update({1002: dict(inviter=1001, expires=200.0),
                              1001: dict(inviter=1002, expires=200.0),
                              1004: dict(inviter=1002, expires=200.0)})
        before = self.s.snapshot(1002)
        out = self.e1.handle(self.c1, Message(3110))
        self.assertEqual([m.id for m in out], [3115])
        self.assertEqual([m.id for m in self.e2.take_pending(self.c2)], [3130, 3160, 4070])
        self.assertEqual(room.owner, 1002)
        self.assertEqual(set(room.members), {1002})
        self.assertEqual(room.loaded, {1002})
        self.assertEqual(room.input_ready, {1002})
        self.assertEqual(room.last_sequence, {(1002, 1): 8})
        self.assertEqual(room.active_states, {(1002, 1): 'peer'})
        self.assertEqual(room.projectiles, {2: dict(owner=1002)})
        self.assertEqual(room.pending_hit_receipts, {1002: [(1002, 'keep')]})
        self.assertEqual(room.pending_death_receipts, {1002: [(1002, 'keep')]})
        self.assertEqual(room.pair_selections, {})
        self.assertEqual(room.motion_payloads, {1002: b'peer'})
        self.assertEqual(self.h.invites, {1004: dict(inviter=1002, expires=200.0)})
        self.assertEqual(self.s.snapshot(1002), before)
        self.assertEqual(self.e1.handle(self.c1, Message(3110)), [])
        self.assertEqual(self.e2.take_pending(self.c2), [])

    def test_disconnect_expiry_boundary_is_idempotent_and_uses_owner_transfer(self):
        self.join()
        self.e1.disconnect(self.c1)
        self.assertEqual(self.h.suspended[1001], 130.0)
        self.assertEqual(self.e2.take_pending(self.c2), [Message(4070, struct.pack('<Q', 1001))])
        self.now = 129.999
        self.h.expire()
        self.assertEqual(self.e2.room.owner, 1001)
        self.assertEqual(self.e2.take_pending(self.c2), [])
        self.now = 130.0
        self.h.expire()
        self.assertEqual(self.e2.room.owner, 1002)
        self.assertNotIn(1001, self.h.suspended)
        self.assertIsNone(self.e1.room)
        self.assertEqual([m.id for m in self.e2.take_pending(self.c2)], [3130, 3160])
        self.h.expire()
        self.assertEqual(self.e2.take_pending(self.c2), [])
