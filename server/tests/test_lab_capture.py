import unittest
import struct
from server.kk_local.lab_capture import LabCapture


class LabCaptureTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.capture = LabCapture(lambda event, **fields: self.events.append(dict(event=event,**fields)))

    def test_auth_chat_inventory_and_control_not_captured(self):
        for ident in (1010,2010,3010,1120,2080,5002,3320):
            self.capture.record('game-tcp','rx',ident,b'private','room')
        for ident in (1001,1002,1012,1013,1014):
            self.capture.record('sdp2p-udp','rx',ident,b'private','battle')
        self.capture.record('game-tcp','rx',8071,b'private','lobby')
        self.capture.record('sdp2p-udp','rx',1005,b'private','lobby')
        self.assertEqual(self.events, [])

    def test_shape_limit_and_byte_bound(self):
        for _ in range(100):
            self.capture.record('game-tcp','rx',8071,b'abcd','battle')
        self.capture.record('game-tcp','tx',4080,bytes(53),'loading')
        self.capture.record('sdp2p-udp','rx',1005,bytes(4097),'battle')
        self.assertEqual(len(self.events), 5)
        self.assertEqual(self.events[0]['payload_hex'], '61626364')
        self.assertEqual(self.events[3]['sample'], 4)

    def test_total_bound_also_bounds_identifier_map(self):
        for ident in range(2000, 4000):
            self.capture.record('sdp2p-udp','rx',ident,bytes(24),'battle')
        self.assertEqual(self.capture.total, 128)
        self.assertEqual(len(self.capture.counts), 128)
        self.assertEqual(len(self.events), 129)
        self.assertEqual(self.events[-1]['event'], 'lab_capture_limit')

    def test_result_reports_but_not_profile_bearing_replies(self):
        self.capture.record('game-tcp','rx',4110,bytes(696),'battle')
        self.capture.record('game-tcp','rx',4115,bytes(4),'battle')
        self.capture.record('game-tcp','tx',4120,bytes(1000),'battle')
        self.assertEqual([e['id'] for e in self.events],[4110,4115])

    def test_equal_size_inner_families_have_separate_bounded_samples(self):
        for ident in (8295,8297):
            body=struct.pack('<I',ident)+bytes(35)
            for _ in range(8):self.capture.record('game-tcp','rx',8071,body,'battle')
        self.assertEqual(len(self.events),8)
        self.assertEqual([e['battle_id'] for e in self.events],[8295]*4+[8297]*4)
        self.assertEqual(self.capture.total,8)

    def test_public_series_ui_but_not_profile_result_is_sampled(self):
        self.capture.record('game-tcp','rx',4111,bytes(309),'battle')
        self.capture.record('game-tcp','tx',4112,bytes(309),'battle')
        self.capture.record('game-tcp','tx',4120,bytes(1000),'battle')
        self.assertEqual([e['id'] for e in self.events],[4111,4112])
