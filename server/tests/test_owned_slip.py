"""Owned8270 components are not authority to move another participant."""
import struct
import unittest
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_shared_rooms as fixtures


def slip(sender=1001,first=1001,second=0,seq=0,distance=4.0):
    p=bytearray(87);struct.pack_into('<IQ',p,0,8270,sender)
    p[12:14]=b'\x01\x01';struct.pack_into('<I',p,19,seq)
    struct.pack_into('<QQ',p,39,first,second)
    if first:struct.pack_into('<fff',p,55,1,0,0);struct.pack_into('<f',p,79,distance)
    if second:struct.pack_into('<fff',p,67,-1,0,0);struct.pack_into('<f',p,83,distance)
    return Message(8071,bytes(p))


class OwnedSlipTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join
    battle=fixtures.SharedRoomTests.battle

    def test_either_owned_component_and_zero_packet_preserve_native_bytes(self):
        self.battle()
        for seq,pair in enumerate(((1001,0),(0,1001),(0,0))):
            m=slip(first=pair[0],second=pair[1],seq=seq,distance=-4.0)
            self.assertEqual(self.e1.handle(self.c1,m),[])
            self.assertEqual(self.e2.take_pending(self.c2),[m])
            self.e1.handle(self.c1,m);self.assertEqual(self.e2.take_pending(self.c2),[])
        m=slip(sender=1002,first=1002)
        self.e2.handle(self.c2,m);self.assertEqual(self.e1.take_pending(self.c1),[m])

    def test_paired_foreign_or_hidden_absent_fields_are_not_authorized(self):
        self.battle()
        for first,second in ((1001,1002),(0,1002),(1002,0),(1001,1001)):
            self.e1.handle(self.c1,slip(first=first,second=second))
        p=bytearray(slip().payload);struct.pack_into('<f',p,83,1)
        self.e1.handle(self.c1,Message(8071,bytes(p)))
        self.assertEqual(self.e1.room.last_sequence,{})
        self.assertEqual(self.e2.take_pending(self.c2),[])

    def test_transport_spoof_nonfinite_and_nonbattle_remain_rejected(self):
        self.battle()
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,slip(sender=1002))
        p=bytearray(slip().payload);struct.pack_into('<f',p,79,float('inf'))
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,Message(8071,bytes(p)))
        self.e1.room.stage='result'
        self.assertEqual(self.e1.handle(self.c1,slip()),[])
        self.assertEqual(self.e2.take_pending(self.c2),[])


if __name__=='__main__':unittest.main()
