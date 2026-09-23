import json
import unittest
from server.kk_local.public_metrics import Metrics


class PublicIngressMetricsTests(unittest.TestCase):
    def test_handshake_directions_and_close_reason(self):
        m = Metrics()
        m.event(dict(event='native_handshake_frame', direction='rx', id=1010))
        m.event(dict(event='native_handshake_frame', direction='tx', id=1151))
        m.event(dict(event='native_tcp_closed', reason='TimeoutError'))
        self.assertEqual(m.counts['native_handshake_rx_1010'], 1)
        self.assertEqual(m.counts['native_handshake_tx_1151'], 1)
        self.assertEqual(m.counts['native_tcp_closed'], 1)
        self.assertEqual(m.counts['native_tcp_closed_TimeoutError'], 1)

    def test_untrusted_labels_and_payload_never_become_metrics(self):
        m = Metrics()
        for i in range(1000):
            secret = f'secret-{i}'
            m.event(dict(event='native_tcp_closed', reason=secret, uid=i, payload=secret))
            m.event(dict(event='native_handshake_frame', direction='rx', id=30000+i, payload=secret))
            m.event(dict(event='native_handshake_frame', direction=secret, id=1010))
        self.assertEqual(len(m.counts), 3)
        self.assertNotIn('secret-', json.dumps(m.snapshot()))

    def test_type_or_direction_mismatch_has_one_finite_bucket(self):
        m = Metrics()
        for value in ('1010', 1010.0, True, None):
            m.event(dict(event='native_handshake_frame', direction='rx', id=value))
        self.assertEqual(dict(m.counts), {'native_handshake_other': 4})

    def test_result_rejection_codes_are_finite_and_do_not_log_raw_values(self):
        m=Metrics()
        m.protocol_rejection(4110,ValueError('settlement peer HP disagreement'))
        for i in range(100):m.protocol_rejection(4110,ValueError(f'private-{i}'))
        m.protocol_rejection(8071,ValueError('private-battle-payload'))
        self.assertEqual(m.counts['native_4110_rejected_peer_hp_disagreement'],1)
        self.assertEqual(m.counts['native_4110_rejected_other'],100)
        self.assertEqual(m.counts['native_8071_protocol_rejected'],1)
        self.assertNotIn('private-',json.dumps(m.snapshot()))

    def test_datagram_rejection_codes_are_finite(self):
        m=Metrics()
        m.datagram_rejection(ValueError('public battle header'))
        m.datagram_rejection(ValueError('SDP room binding required'))
        for i in range(100):m.datagram_rejection(ValueError(f'private-{i}'))
        self.assertEqual(m.counts['udp_rejection_detail_battle_header'],1)
        self.assertEqual(m.counts['udp_rejection_detail_room_binding'],1)
        self.assertEqual(m.counts['udp_rejection_detail_other'],100)
        self.assertNotIn('private-',json.dumps(m.snapshot()))
