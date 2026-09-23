"""Local relay policy, not a claim to reproduce the absent old server."""
import struct
import unittest

from server.kk_local.engine import Phase
from server.kk_local.wire import Message, ProtocolError
from server.tests import test_shared_rooms as fixtures


class RelayCounterTests(unittest.TestCase):
    setUp = fixtures.SharedRoomTests.setUp
    tearDown = fixtures.SharedRoomTests.tearDown
    join = fixtures.SharedRoomTests.join
    battle = fixtures.SharedRoomTests.battle

    def movement(self, seq, *, other=0):
        raw=bytearray(108)
        struct.pack_into('<IQ',raw,0,8120,1001)
        struct.pack_into('<II',raw,15,seq,other)
        return raw

    def state(self, seq, *, other=0, target=1001):
        raw=bytearray(87)
        struct.pack_into('<IQ',raw,0,8150,1001)
        raw[12:14]=b'\x01\x01'
        struct.pack_into('<II',raw,15,other,seq)
        struct.pack_into('<QQ',raw,39,target,1001)
        struct.pack_into('<III',raw,75,1,self.e1.room.number,self.e1.room.serial)
        return raw

    def deliver(self, raw):
        msg=Message(8071,bytes(raw))
        self.assertEqual(self.e1.handle(self.c1,msg),[])  # never echo
        return self.e2.take_pending(self.c2)

    def test_movement_uses_15_not_unchanged_19(self):
        self.battle()
        for seq in (0,1,2):
            raw=self.movement(seq)
            self.assertEqual(self.deliver(raw),[Message(8071,bytes(raw))])
        self.assertEqual(self.deliver(self.movement(2,other=999)),[])
        self.assertEqual(self.deliver(self.movement(1,other=1000)),[])

    def test_generic_and_movement_counters_do_not_suppress_each_other(self):
        self.battle()
        for raw in (self.state(900),self.movement(0),self.state(901),self.movement(1)):
            self.assertEqual(self.deliver(raw),[Message(8071,bytes(raw))])
        self.assertEqual(self.deliver(self.state(901,other=9999)),[])

    def test_same_motion_sequence_accepts_changed_position_not_exact_retry(self):
        self.battle();raw=self.movement(1)
        self.deliver(raw)
        raw[14]=1;struct.pack_into('<I',raw,27,20);struct.pack_into('<f',raw,51,12.5)
        self.assertEqual(self.deliver(raw),[Message(8071,bytes(raw))])
        self.assertEqual(self.deliver(raw),[])
        struct.pack_into('<f',raw,51,15)
        self.assertEqual(self.deliver(raw),[Message(8071,bytes(raw))])
        self.assertEqual(self.deliver(self.movement(0)),[])

    def test_unknown_header_does_not_consume_counter(self):
        self.battle()
        raw=self.state(50);raw[13]=2
        self.assertEqual(self.deliver(raw),[])
        raw=self.state(0)
        self.assertEqual(self.deliver(raw),[Message(8071,bytes(raw))])
        raw=self.state(1);raw[12:14]=b'\0\0'
        self.assertEqual(self.deliver(raw),[])

    def test_spoof_and_cross_target_still_rejected_before_counter_mutation(self):
        self.battle()
        raw=self.state(100,target=1002)
        self.assertEqual(self.deliver(raw),[])
        raw=self.movement(100);struct.pack_into('<Q',raw,4,1002)
        with self.assertRaises(ProtocolError): self.deliver(raw)
        self.assertEqual(self.e1.room.last_sequence,{})

    def test_8121_cross_target_still_rejected(self):
        self.battle()
        raw=bytearray(94);struct.pack_into('<IQ',raw,0,8121,1001)
        raw[12:14]=b'\x01\x01'
        struct.pack_into('<QQ',raw,39,1002,1001)
        struct.pack_into('<II',raw,86,self.e1.room.number,self.e1.room.serial)
        self.assertEqual(self.deliver(raw),[])
        self.assertEqual(self.e1.room.last_sequence,{})

    def test_unknown_state_operation_is_not_cancel_or_sequence_commit(self):
        self.battle()
        for operation in (2,6,0xffffffff):
            raw=self.state(100)
            struct.pack_into('<I',raw,75,operation)
            self.assertEqual(self.deliver(raw),[])
        self.assertEqual(self.e1.room.last_sequence,{})
        raw=self.state(0)
        self.assertEqual(self.deliver(raw),[Message(8071,bytes(raw))])
        raw=self.state(1)
        struct.pack_into('<Q',raw,47,0)
        struct.pack_into('<I',raw,75,0)
        self.assertEqual(self.deliver(raw),[Message(8071,bytes(raw))])

    def test_8126_decode_does_not_open_secondary_8150_production(self):
        self.battle()
        raw=bytearray(71);struct.pack_into('<IQ',raw,0,8126,1001)
        raw[12:14]=b'\x01\x01'
        struct.pack_into('<QQQII',raw,39,1001,1002,1001,811115,1)
        self.assertEqual(self.deliver(raw),[])
        self.assertEqual(self.e1.room.last_sequence,{})

    def test_new_round_clears_both_lanes(self):
        self.battle()
        self.deliver(self.movement(15));self.deliver(self.state(20))
        room=self.e1.room;previous=room.serial
        # Fixture starts from the waiting-room boundary; no native claim.
        room.stage='room'
        for member in room.members.values():
            member.engine.game.phase=Phase.ROOM
            member.ready=True
        self.e1.handle(self.c1,Message(4030))
        self.assertGreater(room.serial,previous)
        self.assertEqual(room.last_sequence,{})

    def test_unsigned_wrap_is_not_silently_accepted_as_new_epoch(self):
        self.battle()
        self.deliver(self.movement(0xffffffff))
        self.assertEqual(self.deliver(self.movement(0)),[])

    def test_leaving_removes_all_sender_lanes(self):
        self.battle()
        self.deliver(self.movement(15));self.deliver(self.state(20))
        room=self.e1.room
        self.h.leave(self.e1)
        self.assertFalse(any(key[0]==1001 for key in room.last_sequence))


if __name__=='__main__':
    unittest.main()
