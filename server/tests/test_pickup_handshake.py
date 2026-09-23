"""Native pickup reservation choreography through the elected room Host."""
import struct
import unittest
from server.kk_local.layouts import decode_battle
from server.kk_local.wire import Message, ProtocolError
from server.tests import test_shared_rooms as fixtures


def pickup(ident,room,*,sender=1002,player=1002,key=123,value=1,seq=0):
    p=bytearray(63);struct.pack_into('<IQ',p,0,ident,sender)
    p[12:14]=b'\x01\x01';struct.pack_into('<I',p,19,seq)
    struct.pack_into('<QIIII',p,39,player,key,value,room.number,room.serial)
    return Message(8071,bytes(p))


class PickupHandshakeTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join
    battle=fixtures.SharedRoomTests.battle

    def request(self):
        m=pickup(9000,self.e1.room)
        self.assertEqual(self.e2.handle(self.c2,m),[])
        self.assertEqual(self.e1.take_pending(self.c1),[m])
        return m

    def approve(self,value=1,seq=0):
        m=pickup(9001,self.e1.room,sender=1001,value=value,seq=seq)
        self.assertEqual(self.e1.handle(self.c1,m),[])
        self.assertEqual(self.e2.take_pending(self.c2),[m])
        return m

    def test_request_host_reply_completion_without_echo_or_persistent_grant(self):
        self.battle();before=self.s.snapshot(1002)
        req=self.request();self.assertEqual(self.e1.room.pickup_requests[1002]['status'],'pending')
        self.e2.handle(self.c2,req);self.assertEqual(self.e1.take_pending(self.c1),[])
        reply=self.approve();self.assertEqual(self.e1.room.pickup_requests[1002]['status'],'approved')
        self.e1.handle(self.c1,reply);self.assertEqual(self.e2.take_pending(self.c2),[])
        done=pickup(9002,self.e1.room,seq=1)
        self.assertEqual(self.e2.handle(self.c2,done),[])
        self.assertEqual(self.e1.take_pending(self.c1),[done])
        self.assertEqual(self.e1.room.pickup_requests,{})
        self.e2.handle(self.c2,done);self.assertEqual(self.e1.take_pending(self.c1),[])
        self.assertEqual(self.s.snapshot(1002),before)

    def test_denial_allows_later_retry_without_false_completion(self):
        self.battle();self.request();self.approve(0)
        self.assertEqual(self.e1.room.pickup_requests,{})
        with self.assertRaises(ProtocolError):self.e2.handle(self.c2,pickup(9002,self.e1.room,seq=1))
        retry=pickup(9000,self.e1.room,seq=1)
        self.e2.handle(self.c2,retry)
        self.assertEqual(self.e1.take_pending(self.c1),[retry])

    def test_cancel_reaches_host_and_late_reply_is_not_applied(self):
        self.battle();self.request();self.approve()
        cancel=pickup(9000,self.e1.room,value=0,seq=1)
        self.e2.handle(self.c2,cancel)
        self.assertEqual(self.e1.take_pending(self.c1),[cancel])
        self.e1.handle(self.c1,pickup(9001,self.e1.room,sender=1001,seq=1))
        self.assertEqual(self.e2.take_pending(self.c2),[])

    def test_host_own_fast_path_completion_does_not_need_wire_request(self):
        self.battle()
        done=pickup(9002,self.e1.room,sender=1001,player=1001)
        self.assertEqual(self.e1.handle(self.c1,done),[])
        self.assertEqual(self.e2.take_pending(self.c2),[done])

    def test_unsolicited_reply_and_mismatched_key_do_not_consume_host_sequence(self):
        self.battle();room=self.e1.room
        self.e1.handle(self.c1,pickup(9001,room,sender=1001,seq=99))
        self.assertEqual(room.last_sequence,{})
        self.request()
        self.e1.handle(self.c1,pickup(9001,room,sender=1001,key=456,seq=99))
        self.assertNotIn((1001,19),room.last_sequence)
        self.approve()

    def test_nonhost_forged_reply_actor_or_stale_room_rejected(self):
        self.battle();room=self.e1.room
        for msg in (pickup(9001,room),pickup(9000,room,player=1001),
                    pickup(9000,room,sender=1001),pickup(9000,room,player=9999)):
            with self.assertRaises(ProtocolError):self.e2.handle(self.c2,msg)
        p=bytearray(pickup(9000,room).payload);struct.pack_into('<I',p,59,room.serial+1)
        with self.assertRaises(ProtocolError):self.e2.handle(self.c2,Message(8071,bytes(p)))
        self.assertEqual(room.pickup_requests,{})

    def test_pending_request_does_not_grow_with_more_keys(self):
        self.battle();self.request();room=self.e1.room
        for key in range(200,210):self.e2.handle(self.c2,pickup(9000,room,key=key,seq=key))
        self.assertEqual(room.pickup_requests,{1002:dict(key=123,status='pending')})
        self.assertEqual(self.e1.take_pending(self.c1),[])

    def test_host_cannot_pick_item_reserved_for_other_member(self):
        self.battle();self.request();self.approve()
        with self.assertRaises(ProtocolError):
            self.e1.handle(self.c1,pickup(9002,self.e1.room,sender=1001,player=1001,seq=1))

    def test_length_value_and_readonly_decode(self):
        self.battle();room=self.e1.room
        for ident in (9000,9001,9002):
            m=pickup(ident,room);out=decode_battle(m.payload)
            self.assertEqual((out['player'],out['pickup_key'],out['pickup_value_raw'],out['room_pair']),
                             (1002,123,1,(room.number,room.serial)))
            with self.assertRaises(ProtocolError):decode_battle(m.payload[:-1])
        with self.assertRaises(ProtocolError):self.e2.handle(self.c2,pickup(9000,room,value=2))
        self.assertEqual(room.pickup_requests,{})


if __name__=='__main__':unittest.main()
