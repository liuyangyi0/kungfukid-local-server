"""Owned8127 absolute-MP snapshots: preserve bits, do not add a server delta."""
import math
import struct
import unittest
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_shared_rooms as fixtures


def mp_snapshot(room,*,sender=1001,player=1001,value=10.5,seq=0):
    p=bytearray(63);struct.pack_into('<IQ',p,0,8127,sender)
    p[12:14]=b'\x01\x01';struct.pack_into('<I',p,19,seq)
    struct.pack_into('<Q',p,39,player);p[47:51]=b'KEEP'
    struct.pack_into('<fII',p,51,value,room.number,room.serial)
    return Message(8071,bytes(p))


class MpSnapshotRelayTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join
    battle=fixtures.SharedRoomTests.battle

    def test_absolute_values_order_and_negative_zero_bits_are_unchanged(self):
        self.battle();room=self.e1.room;before=self.s.snapshot(1001)
        for seq,value in enumerate((10.5,3.25,3.25,0.0,-0.0,77.5)):
            m=mp_snapshot(room,seq=seq,value=value)
            self.assertEqual(self.e1.handle(self.c1,m),[])
            self.assertEqual(self.e2.take_pending(self.c2),[m])
            self.e1.handle(self.c1,m)
            self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(self.s.snapshot(1001),before)
        self.assertEqual(room.active_states,{})

    def test_other_member_reports_self_but_host_cannot_spoof_peer(self):
        self.battle();room=self.e1.room
        m=mp_snapshot(room,sender=1002,player=1002,value=8)
        self.e2.handle(self.c2,m)
        self.assertEqual(self.e1.take_pending(self.c1),[m])
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,mp_snapshot(room,player=1002))
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,mp_snapshot(room,sender=1002))
        self.assertEqual(self.e2.take_pending(self.c2),[])

    def test_stale_round_nonfinite_wrong_lane_rejected_before_sequence_mutation(self):
        self.battle();room=self.e1.room
        p=bytearray(mp_snapshot(room).payload);struct.pack_into('<I',p,59,room.serial+1)
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,Message(8071,bytes(p)))
        for value in (math.nan,math.inf,-math.inf):
            with self.assertRaises(ProtocolError):self.e1.handle(self.c1,mp_snapshot(room,value=value))
        p=bytearray(mp_snapshot(room).payload);p[12:14]=b'\0\0'
        self.assertEqual(self.e1.handle(self.c1,Message(8071,bytes(p))),[])
        self.assertEqual(room.last_sequence,{})
        self.assertEqual(self.e2.take_pending(self.c2),[])


if __name__=='__main__':unittest.main()
