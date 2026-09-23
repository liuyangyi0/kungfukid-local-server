"""Native Host4110 -> server4100 -> peer4110, not a fabricated peer report."""
import struct
import unittest
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_lab_settlement as fixtures


class ReportRequestTests(unittest.TestCase):
    setUp=fixtures.LabSettlementTests.setUp
    tearDown=fixtures.LabSettlementTests.tearDown
    report=fixtures.LabSettlementTests.report

    def test_host_snapshot_requests_only_missing_peer_once(self):
        p=self.report()
        self.assertEqual(self.a.handle(self.ca,Message(4110,p)),[])
        self.assertEqual(self.b.take_pending(self.cb),[Message(4100)])
        self.assertEqual(set(self.a.room.result_reports),{1001})
        self.assertFalse(self.a.room.result_replies)
        self.a.handle(self.ca,Message(4110,p))
        self.assertEqual(self.b.take_pending(self.cb),[])
        out=self.b.handle(self.cb,Message(4110,p))
        self.assertEqual([m.id for m in out],[4120])
        self.assertEqual([m.id for m in self.a.take_pending(self.ca)],[4120])

    def test_p2_host_requests_p1_not_fixed_uid_or_slot_zero(self):
        self.a.room.owner=1002
        p=self.report((100,98))
        self.b.handle(self.cb,Message(4110,p))
        self.assertEqual(self.a.take_pending(self.ca),[Message(4100)])
        self.assertEqual(self.b.take_pending(self.cb),[])
        self.assertEqual([m.id for m in self.a.handle(self.ca,Message(4110,p))],[4120])

    def test_unsolicited_peer_does_not_drive_collection_or_disable_consensus(self):
        p=self.report()
        self.b.handle(self.cb,Message(4110,p))
        self.assertEqual(self.a.take_pending(self.ca),[])
        self.assertFalse(self.a.room.result_requests)
        with self.assertRaises(ProtocolError):self.a.handle(self.ca,Message(4110,self.report((100,0))))
        self.assertFalse(self.a.room.result_replies)

    def test_invalid_report_never_requests_or_mutates_and_queued_request_can_be_retired(self):
        with self.assertRaises(ProtocolError):self.a.handle(self.ca,Message(4110,b''))
        self.assertFalse(self.a.room.result_requests)
        self.assertEqual(self.b.take_pending(self.cb),[])
        p=self.report();self.a.handle(self.ca,Message(4110,p))
        self.assertEqual([m.id for m in self.b.pending_messages],[4100])
        out=self.b.handle(self.cb,Message(4110,p))
        self.assertEqual([m.id for m in out],[4120])
        self.assertEqual(self.b.pending_bytes,0)


if __name__=='__main__':unittest.main()
