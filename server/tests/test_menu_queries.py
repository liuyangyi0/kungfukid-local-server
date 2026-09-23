import struct
import unittest
from server.kk_local import packets
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.store import Store
from server.kk_local.wire import Message,ProtocolError
from server.tests.test_competitive_rooms import prepare_handoff
from server.tests.test_local_service import hello


class MenuQueryTests(unittest.TestCase):
    def test_unconfigured_purchase_refuses_without_mutation(self):
        s=Store(':memory:');s.seed_local()
        try:
            e=Engine(s);prepare_handoff(e);c=Connection(2);e.handle(c,hello(2010))
            before=s.snapshot(1001)
            for ident in (9040,9041):
                for phase in (Phase.LOBBY,Phase.ROOM):
                    c.phase=phase
                    for payload in (bytes(169),bytes([255])*169):
                        self.assertEqual(e.handle(c,Message(ident,payload)),[Message(9060,b'\x82\0')])
                    self.assertEqual(c.phase,phase)
                    self.assertEqual(s.snapshot(1001),before)
                with self.assertRaises(ProtocolError):e.handle(c,Message(ident,bytes(168)))
                c.phase=Phase.BATTLE
                self.assertEqual(e.handle(c,Message(ident,bytes(169))),[])
            retired=Connection(44,Phase.LOBBY,1001)
            self.assertEqual(e.handle(retired,Message(9040,bytes(169))),[])
            self.assertIsNone(e.room)
            self.assertEqual(s.snapshot(1001),before)
        finally:s.close()

    def test_native_empty_catalog_envelope(self):
        self.assertEqual(packets.empty_local_catalog(25,3),Message(9080,b'\x19\x03\0\0\0\0'))
        for value in (-1,256,True):
            with self.assertRaises(ProtocolError):packets.empty_local_catalog(value,0)

    def test_no_ranked_data_branch_and_terminated_description(self):
        m=packets.no_local_ranked_season()
        self.assertEqual(m.id,20370)
        self.assertEqual(m.payload[:36],bytes(36))
        self.assertTrue(m.payload[36:].endswith(b'\0'))
        self.assertIn(b'No local ranked season',m.payload[36:])

    def test_read_queries_do_not_mutate_account_or_claim_other_identity(self):
        s=Store(':memory:');s.seed_local()
        try:
            e=Engine(s);prepare_handoff(e);c=Connection(2);e.handle(c,hello(2010))
            before=s.snapshot(1001)
            for _ in range(2):
                self.assertEqual(e.handle(c,Message(1300)),[Message(1310)])
                self.assertEqual(e.handle(c,Message(1400)),[Message(1410)])
                self.assertEqual(e.handle(c,Message(9070,b'\x19\0')),[packets.empty_local_catalog(25,0)])
                self.assertEqual(e.handle(c,Message(20360,struct.pack('<QI',1001,0))),[packets.no_local_ranked_season()])
            self.assertEqual(e.handle(c,Message(20360,struct.pack('<QI',1002,0))),[])
            self.assertEqual(s.snapshot(1001),before)
            for m in (Message(9070,b'x'),Message(20360,b'x'),Message(1300,b'x')):
                with self.assertRaises(ProtocolError):e.handle(c,m)
            self.assertNotIn(1410,[m.id for m in e.handle(c,Message(1400,b'x'))])
            self.assertEqual(s.snapshot(1001),before)
            c.phase=Phase.BATTLE
            self.assertEqual(e.handle(c,Message(9070,b'\x19\0')),[])
            self.assertEqual(e.handle(c,Message(1300)),[])
            self.assertEqual(e.handle(c,Message(1400)),[])
        finally:s.close()
