"""Observe SDP game frames without applying them twice or merging counters."""
import struct
import unittest
from server.kk_local.engine import Phase
from server.kk_local.wire import encode_game,Message
from server.tests import test_sdp_peer as fixtures
from server.tests.test_pair_transform import selection,transform


class PeerPairSelectionTests(unittest.TestCase):
    setUp=fixtures.SdpPeerTests.setUp
    tearDown=fixtures.SdpPeerTests.tearDown
    packet=fixtures.SdpPeerTests.packet

    def battle(self):
        self.a.engine.room.stage='battle'
        for c in self.clients:c.phase=Phase.BATTLE

    def relay(self,message):
        body=encode_game(message)
        raw=self.packet(self.a,1008,body,struct.pack('<I',self.b.engine.p2p['player']))
        self.router.handle(self.a,raw,self.a.engine.p2p['peer'])
        self.assertEqual(self.b.udp.sent[-1][0][24:],body)

    def test_peer_selection_can_authorize_target_tcp_return_without_double_delivery(self):
        self.battle();self.relay(selection())
        room=self.a.engine.room
        self.assertEqual(room.pair_selections[1001][0],1002)
        self.assertEqual(room.last_sequence,{})
        self.assertEqual(self.a.engine.take_pending(self.clients[0]),[])
        self.assertEqual(self.b.engine.take_pending(self.clients[1]),[])
        msg=transform()
        self.b.engine.handle(self.clients[1],msg)
        self.assertEqual(self.a.engine.take_pending(self.clients[0]),[msg])
        self.assertEqual(len(self.b.udp.sent),1)

    def test_duplicate_old_or_expired_selection_cannot_extend_authorization(self):
        self.battle();self.relay(selection(seq=4));expiry=self.a.engine.room.pair_selections[1001][1]
        self.now=14;self.relay(selection(seq=4))
        self.assertEqual(self.a.engine.room.pair_selections[1001][1],expiry)
        self.now=16;self.b.engine.handle(self.clients[1],transform())
        self.assertEqual(self.a.engine.take_pending(self.clients[0]),[])
        self.relay(selection(seq=4));self.assertFalse(self.a.engine.room.pair_selections)
        self.relay(selection(seq=5));self.relay(selection(seq=6,target=0));self.relay(selection(seq=5))
        self.assertFalse(self.a.engine.room.pair_selections)

    def test_malformed_unrelated_or_wrong_actor_frames_do_not_grant_selection(self):
        self.battle()
        for body in (b'opaque',encode_game(selection())[:20],encode_game(Message(10000,bytes(24)))):
            raw=self.packet(self.a,1008,body,struct.pack('<I',self.b.engine.p2p['player']))
            self.router.handle(self.a,raw,self.a.engine.p2p['peer'])
            self.assertEqual(self.b.udp.sent[-1][0][24:],body)
        self.relay(selection(source=1002,target=1001))
        self.assertFalse(self.a.engine.room.pair_selections)
        self.assertEqual(self.a.engine.room.last_sequence,{})

    def test_waiting_room_or_missing_target_recipient_never_grants(self):
        self.relay(selection());self.assertFalse(self.a.engine.room.pair_selections)
        self.battle()
        from server.kk_local.layouts import decode_battle
        self.hub.observe_pair_selection(self.a.engine,decode_battle(selection().payload),recipients=set())
        self.assertFalse(self.a.engine.room.pair_selections)


if __name__=='__main__':unittest.main()
