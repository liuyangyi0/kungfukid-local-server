import struct
import unittest
from server.kk_local.engine import Phase
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_shared_rooms as fixture


class NetworkProbeTests(unittest.TestCase):
    setUp=fixture.SharedRoomTests.setUp
    tearDown=fixture.SharedRoomTests.tearDown
    join=fixture.SharedRoomTests.join

    def begin(self):
        self.h.network_probe_enabled=True
        self.now=[10.0];self.e1.clock=self.e2.clock=lambda:self.now[0]
        self.join();self.e2.handle(self.c2,Message(4030));self.e1.take_pending(self.c1)
        self.assertEqual([m.id for m in self.e1.handle(self.c1,Message(4030))],[4050,4150])
        self.assertEqual([m.id for m in self.e2.take_pending(self.c2)],[4050,4150])

    def test_all_replies_before_4080_and_real_delay_not_p2p(self):
        self.begin();room=self.e1.room
        self.assertEqual(room.serial,0);self.assertEqual(room.stage,'room')
        self.now[0]=10.010
        self.assertEqual(self.e1.handle(self.c1,Message(4140)),[])
        self.assertEqual(self.e1.handle(self.c1,Message(4030)),[])  # same probe
        self.now[0]=10.020
        out=self.e2.handle(self.c2,Message(4140));other=self.e1.take_pending(self.c1)
        self.assertEqual([m.id for m in out],[4080]);self.assertEqual(out,other)
        values=struct.unpack_from('<II',out[0].payload,13)
        self.assertTrue(9<=values[0]<=11 and 19<=values[1]<=21)
        self.assertEqual(self.c1.phase,Phase.LOADING)
        self.assertEqual(self.e2.handle(self.c2,Message(4140)),[])

    def test_timeout_cancels_without_battle_serial_or_stale_start(self):
        self.begin();room=self.e1.room;self.now[0]+=10;self.h.expire()
        self.assertIsNone(room.network_probe);self.assertEqual(room.serial,0)
        self.assertTrue(all(not m.ready for m in room.members.values()))
        self.e1.take_pending(self.c1);self.e2.take_pending(self.c2)
        self.assertEqual(self.e1.handle(self.c1,Message(4140)),[])

    def test_roster_or_readiness_change_invalidates_probe(self):
        self.begin();self.e2.handle(self.c2,Message(4060))
        self.e1.take_pending(self.c1)
        self.e1.handle(self.c1,Message(4140))
        self.assertIsNone(self.e1.room.network_probe);self.assertEqual(self.e1.room.serial,0)

    def test_bad_length_and_expired_p2p_cannot_start(self):
        self.begin()
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,Message(4140,b'x'))
        self.e2.p2p['expires']=0
        self.e1.handle(self.c1,Message(4140))
        self.assertEqual(self.e1.room.stage,'room');self.assertIsNone(self.e1.room.network_probe)


class NetworkProbeTCPTests(fixture.SharedRoomNetworkTests):
    network_probe=True
