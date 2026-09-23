"""820160/816940: change room position key without moving registry slots."""
import struct
import unittest
from server.kk_local.engine import Engine,Phase
from server.kk_local.wire import Message
from server.tests import test_shared_rooms as fixture
from server.tests.test_local_service import create_room


class TeamPositionTests(unittest.TestCase):
    setUp=fixture.SharedRoomTests.setUp
    tearDown=fixture.SharedRoomTests.tearDown

    def create(self,mode=1,capacity=8):
        p=bytearray(create_room().payload);p[46]=mode;p[37]=capacity
        out=self.e1.handle(self.c1,Message(3010,bytes(p)))
        peer=self.e2.handle(self.c2,Message(3070,struct.pack('<HB11s',1,0,b'')))
        self.e1.take_pending(self.c1)
        return out[0],peer[0]

    def add(self,uid):
        self.s.provision_local(uid,'Peer'+str(uid))
        e=Engine(self.s,hub=self.h,account_uid=uid);c=fixture.lobby(e,uid)
        e.handle(c,Message(3070,struct.pack('<HB11s',1,0,b'')))
        return e,c

    def test_initial_team_banks_are_independent_from_stable_slots(self):
        for mode in (1,3):
            with self.subTest(mode=mode):
                if self.e1.room:
                    self.h.leave(self.e2);self.h.leave(self.e1)
                    self.e1.take_pending(self.c1);self.e2.take_pending(self.c2)
                first,second=self.create(mode)
                self.assertEqual(first.payload[10:12],bytes((0,0)))
                self.assertEqual(second.payload[10:12],bytes((1,4)))
                self.assertEqual(second.payload[104:107],bytes((1,4,1)))

    def test_switch_moves_key_bank_and_keeps_host_slot_and_equipment(self):
        self.create();room=self.e1.room;before=self.s.snapshot(1001)
        msg=Message(3250,struct.pack('<QBB',1001,1,5))
        self.assertEqual(self.e1.handle(self.c1,Message(3230,b'\1')),[msg])
        self.assertEqual(self.e2.take_pending(self.c2),[msg])
        self.assertEqual((room.members[1001].slot,room.members[1001].team,room.members[1001].registry_key),(0,1,5))
        self.assertEqual(room.owner,1001)
        self.assertEqual(self.s.snapshot(1001),before)
        raw=self.h.fighter(1001,room.members[1001])
        self.assertEqual(raw[8:11],bytes((0,5,1)))
        back=Message(3250,struct.pack('<QBB',1001,0,0))
        self.assertEqual(self.e1.handle(self.c1,Message(3230,b'\0')),[back])

    def test_full_bank_rejects_without_overlapping_another_player(self):
        self.create();peers={1001:(self.e1,self.c1),1002:(self.e2,self.c2)}
        self.e2.handle(self.c2,Message(3230,b'\0'))
        for uid in (1003,1004,1005):peers[uid]=self.add(uid)
        e,c=peers[1004];e.take_pending(c);e.handle(c,Message(3230,b'\0'))
        room=self.e1.room
        self.assertEqual(sorted(m.registry_key for m in room.fighters.values() if m.team==0),[0,1,2,3])
        e,c=peers[1005];e.take_pending(c);before=(room.members[1005].team,room.members[1005].registry_key)
        out=e.handle(c,Message(3230,b'\0'))
        self.assertFalse(any(m.id==3250 for m in out))
        self.assertEqual((room.members[1005].team,room.members[1005].registry_key),before)

    def test_unequal_team_sizes_do_not_start_native_team_match(self):
        self.create();e3,c3=self.add(1003);e4,c4=self.add(1004)
        e4.take_pending(c4);e4.handle(c4,Message(3230,b'\0'))
        for e,c in ((self.e2,self.c2),(e3,c3),(e4,c4)):
            e.take_pending(c);e.handle(c,Message(4030))
        self.e1.take_pending(self.c1);self.e1.handle(self.c1,Message(4030))
        self.assertEqual(self.e1.room.stage,'room');self.assertEqual(self.c1.phase,Phase.ROOM)
        self.assertFalse(self.e1.room.members[1001].ready)

    def test_nonteam_room_retains_single_position_array(self):
        _,peer=self.create(mode=0)
        self.assertEqual(peer.payload[10:12],bytes((1,1)))


if __name__=='__main__':unittest.main()
