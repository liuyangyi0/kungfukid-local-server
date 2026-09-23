"""COpenChestAct reservation reuses pickup choreography, not its payload tail."""
import struct
import unittest
from server.kk_local.layouts import decode_battle
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_shared_rooms as fixtures
from server.tests.test_pickup_handshake import pickup


def chest(ident,*,sender=1002,player=1002,key=123,value=1,seq=0):
    p=bytearray(55);struct.pack_into('<IQ',p,0,ident,sender)
    p[12:14]=b'\x01\x01';struct.pack_into('<I',p,19,seq)
    struct.pack_into('<QII',p,39,player,key,value)
    return Message(8071,bytes(p))


class WorldChestTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join
    battle=fixtures.SharedRoomTests.battle

    def test_full_request_approve_complete_keeps_inventory_unchanged(self):
        self.battle();before=self.s.snapshot(1002)
        request=chest(9500);self.e2.handle(self.c2,request)
        self.assertEqual(self.e1.take_pending(self.c1),[request])
        reply=chest(9501,sender=1001);self.e1.handle(self.c1,reply)
        self.assertEqual(self.e2.take_pending(self.c2),[reply])
        complete=chest(9502,seq=1);self.e2.handle(self.c2,complete)
        self.assertEqual(self.e1.take_pending(self.c1),[complete])
        self.assertEqual(self.e1.room.chest_requests,{})
        self.assertEqual(self.s.snapshot(1002),before)
        self.e2.handle(self.c2,complete);self.assertEqual(self.e1.take_pending(self.c1),[])

    def test_same_key_in_pickup_and_chest_has_independent_approval(self):
        self.battle();room=self.e1.room
        self.e2.handle(self.c2,pickup(9000,room));self.e1.take_pending(self.c1)
        self.e2.handle(self.c2,chest(9500,seq=1));self.e1.take_pending(self.c1)
        self.e1.handle(self.c1,pickup(9001,room,sender=1001));self.e2.take_pending(self.c2)
        with self.assertRaises(ProtocolError):self.e2.handle(self.c2,chest(9502,seq=2))
        self.assertEqual(room.chest_requests[1002]['status'],'pending')
        self.assertEqual(room.pickup_requests[1002]['status'],'approved')
        self.e1.handle(self.c1,chest(9501,sender=1001,seq=1));self.e2.take_pending(self.c2)
        done=chest(9502,seq=2);self.e2.handle(self.c2,done)
        self.assertEqual(self.e1.take_pending(self.c1),[done])
        self.assertIn(1002,room.pickup_requests)

    def test_host_fast_path_and_cancellation(self):
        self.battle();room=self.e1.room
        done=chest(9502,sender=1001,player=1001)
        self.e1.handle(self.c1,done);self.assertEqual(self.e2.take_pending(self.c2),[done])
        self.e2.handle(self.c2,chest(9500));self.e1.take_pending(self.c1)
        cancel=chest(9500,value=0,seq=1);self.e2.handle(self.c2,cancel)
        self.assertEqual(self.e1.take_pending(self.c1),[cancel])
        self.assertEqual(room.chest_requests,{})
        self.e1.handle(self.c1,chest(9501,sender=1001,seq=1))
        self.assertEqual(self.e2.take_pending(self.c2),[])

    def test_spoof_and_shape_rejected_no_fake_room_tail(self):
        self.battle()
        for m in (chest(9501),chest(9500,player=1001),chest(9500,value=3)):
            with self.assertRaises(ProtocolError):self.e2.handle(self.c2,m)
        for ident in (9500,9501,9502):
            m=chest(ident)
            self.assertNotIn('room_pair',decode_battle(m.payload))
            with self.assertRaises(ProtocolError):decode_battle(m.payload+bytes(8))
        self.assertEqual(self.e1.room.chest_requests,{})


if __name__=='__main__':unittest.main()
