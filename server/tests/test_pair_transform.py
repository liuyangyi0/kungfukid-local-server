"""Target-owned8144 requires a qualified source8143 selection."""
import struct
import unittest
from server.kk_local.wire import Message,ProtocolError
from server.kk_local.engine import Phase
from server.tests import test_shared_rooms as fixtures


def selection(source=1001,target=1002,seq=0,timeout=15000):
    p=bytearray(59);struct.pack_into('<IQ',p,0,8143,source)
    p[12:14]=b'\x01\x01';struct.pack_into('<I',p,19,seq)
    struct.pack_into('<QQI',p,39,source,target,timeout)
    return Message(8071,bytes(p))


def transform(sender=1002,first=1001,second=1002,seq=0):
    p=bytearray(115);struct.pack_into('<IQ',p,0,8144,sender)
    p[12:14]=b'\x01\x01';struct.pack_into('<I',p,19,seq)
    struct.pack_into('<QQ6fI6fII',p,39,first,second,
                     10,20,30,0,0,1,2010,11,21,31,0,0,-1,4001,811115)
    return Message(8071,bytes(p))


class PairTransformTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join
    battle=fixtures.SharedRoomTests.battle

    def arm(self):
        self.e1.handle(self.c1,selection());self.e2.take_pending(self.c2)

    def test_selection_source_then_other_owner_returns_unmodified_pair(self):
        self.battle();self.arm();before=[self.s.snapshot(u) for u in (1001,1002)]
        msg=transform()
        self.assertEqual(self.e2.handle(self.c2,msg),[])
        self.assertEqual(self.e1.take_pending(self.c1),[msg])
        self.e2.handle(self.c2,msg)
        self.assertEqual(self.e1.take_pending(self.c1),[])
        self.assertEqual([self.s.snapshot(u) for u in (1001,1002)],before)
        self.assertNotIn(1002,self.e1.room.pair_selections)  #not reciprocal approval

    def test_initiator_cannot_write_for_target_and_unseen_peer_selection_is_not_invented(self):
        self.battle()
        self.e2.handle(self.c2,transform())
        self.assertEqual(self.e1.take_pending(self.c1),[])
        self.arm()
        self.e1.handle(self.c1,transform(sender=1001))
        self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertNotIn((1002,19),self.e1.room.last_sequence)
        with self.assertRaises(ProtocolError):self.e2.handle(self.c2,transform(sender=1001))

    def test_clear_unknown_timeout_expiry_and_foreign_actor_fail_closed(self):
        self.battle();now=[10.0];self.e1.clock=lambda:now[0];self.arm()
        self.e2.handle(self.c2,transform(first=9999));self.assertEqual(self.e1.take_pending(self.c1),[])
        now[0]=25.0
        self.e2.handle(self.c2,transform());self.assertEqual(self.e1.take_pending(self.c1),[])
        self.assertFalse(self.e1.room.pair_selections)
        for seq,target,timeout in ((1,1002,15000),(2,0,15000),(3,1002,1)):
            self.e1.handle(self.c1,selection(seq=seq,target=target,timeout=timeout));self.e2.take_pending(self.c2)
        self.e2.handle(self.c2,transform());self.assertEqual(self.e1.take_pending(self.c1),[])
        self.assertFalse(self.e1.room.pair_selections)

    def test_leave_and_new_round_clear_selection_without_false_camera_link(self):
        self.battle();self.arm();room=self.e1.room
        room.stage='room'
        for member in room.members.values():member.ready=True;member.engine.game.phase=Phase.ROOM
        self.e1.handle(self.c1,Message(4030));self.e2.take_pending(self.c2)
        self.assertFalse(room.pair_selections)
        room.pair_selections[1001]=(1002,1e30)
        self.h.leave(self.e2)
        self.assertFalse(room.pair_selections)

    def test_pose_bits_are_finite_and_state_ids_not_rewritten(self):
        self.battle();self.arm()
        bad=bytearray(transform().payload);struct.pack_into('<f',bad,55,float('nan'))
        with self.assertRaises(ProtocolError):self.e2.handle(self.c2,Message(8071,bytes(bad)))
        msg=transform();self.e2.handle(self.c2,msg)
        out=self.e1.take_pending(self.c1)[0]
        self.assertEqual((struct.unpack_from('<I',out.payload,79)[0],struct.unpack_from('<II',out.payload,107)),(2010,(4001,811115)))


if __name__=='__main__':unittest.main()
