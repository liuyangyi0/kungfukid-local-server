"""Native invitation consent is bound to one live room and two connections."""
import struct
import unittest
from server.kk_local.wire import Message,ProtocolError
from server.kk_local.engine import Phase
from server.tests import test_shared_rooms as fixtures


def invite(sender=1001,target=1002,room=1):
    return Message(3500,struct.pack('<Q',sender)+b'FAKE'.ljust(21,b'\0')+struct.pack('<QH',target,room))


def accept(inviter=1001,target=1002,room=1):
    return Message(3501,struct.pack('<QQH',inviter,target,room))


def decline(inviter=1001,target=1002):
    return Message(3502,struct.pack('<Q',inviter)+b'FAKE'.ljust(21,b'\0')+struct.pack('<Q',target))


class RoomInviteTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown

    def offer(self,password=b''):
        p=bytearray(fixtures.create_room().payload);p[21:32]=password.ljust(11,b'\0')
        self.e1.handle(self.c1,Message(3010,bytes(p)))
        self.assertEqual(self.e1.handle(self.c1,invite()),[])
        out=self.e2.take_pending(self.c2)
        self.assertEqual(len(out),1);self.assertEqual(out[0].id,3500)
        self.assertEqual(out[0].payload[8:29].split(b'\0')[0],self.s.nickname(1001).encode('gbk'))
        self.assertEqual(struct.unpack_from('<QH',out[0].payload,29),(1002,1))
        return self.e1.room

    def test_accept_installs_real_member_and_replay_does_not_duplicate(self):
        room=self.offer()
        out=self.e2.handle(self.c2,accept())
        self.assertEqual([m.id for m in out],[3100,3160,3090])
        self.assertEqual([m.id for m in self.e1.take_pending(self.c1)],[3090])
        self.assertIs(self.e2.room,room);self.assertEqual(len(room.members),2)
        self.assertEqual(self.h.invites,{})
        self.assertEqual(self.e2.handle(self.c2,accept()),[])
        self.assertEqual(self.e1.take_pending(self.c1),[])

    def test_decline_uses_actual_name_and_is_one_shot(self):
        self.offer();self.e2.handle(self.c2,decline())
        out=self.e1.take_pending(self.c1)
        self.assertEqual([m.id for m in out],[3502])
        self.assertEqual(out[0].payload[8:29].split(b'\0')[0],b'Second')
        self.assertIsNone(self.e2.room);self.assertFalse(self.h.invites)
        self.assertEqual(self.e2.handle(self.c2,decline()),[])
        self.assertEqual(self.e1.take_pending(self.c1),[])

    def test_password_room_admission_requires_actual_pending_consent(self):
        self.assertEqual(self.e2.handle(self.c2,accept()),[])
        room=self.offer(password=b'secret')
        self.assertEqual(self.e2.handle(self.c2,accept(room=2))[0].id,20150)
        self.assertIsNone(self.e2.room)
        self.e2.handle(self.c2,accept());self.assertIs(self.e2.room,room)

    def test_wrong_identity_length_and_repeated_offer(self):
        self.offer()
        self.assertEqual(self.e1.handle(self.c1,invite()),[])
        self.assertEqual(self.e2.take_pending(self.c2),[])
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,invite(sender=1002))
        with self.assertRaises(ProtocolError):self.e2.handle(self.c2,accept(target=1001))
        for ident in (3500,3501,3502):
            with self.assertRaises(ProtocolError):self.e1.handle(self.c1,Message(ident,b''))
        self.assertEqual(len(self.h.invites),1)

    def test_departure_disconnect_and_new_round_invalidate_pending_invitation(self):
        room=self.offer()
        self.e1.handle(self.c1,Message(3110));self.assertFalse(self.h.invites)
        self.e1.handle(self.c1,fixtures.create_room())
        self.assertEqual(self.e2.handle(self.c2,accept()),[])
        self.assertIsNone(self.e2.room)  # reused room number is insufficient
        self.e1.handle(self.c1,invite());self.e2.take_pending(self.c2)
        self.e2.disconnect(self.c2);self.assertFalse(self.h.invites)

    def test_loading_full_or_expired_room_does_not_auto_join(self):
        room=self.offer();room.stage='loading'
        out=self.e2.handle(self.c2,accept())
        self.assertNotIn(3100,[m.id for m in out]);self.assertIsNone(self.e2.room)
        room.stage='room';self.e1.handle(self.c1,invite());self.e2.take_pending(self.c2)
        room.serial+=1
        self.assertNotIn(3100,[m.id for m in self.e2.handle(self.c2,accept())])
        self.e1.handle(self.c1,invite());self.e2.take_pending(self.c2)
        self.h.invites[1002]['expires']=0
        self.assertEqual(self.e2.handle(self.c2,accept()),[])
        self.assertIsNone(self.e2.room)


if __name__=='__main__':unittest.main()
