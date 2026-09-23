"""4082 wait/4083 permission reply with real loadout and preserved token count."""
import struct
import unittest
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_shared_rooms as fixtures


class WeaponSwitchProtocolTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join
    battle=fixtures.SharedRoomTests.battle

    def add(self,instance,kind,prop,slot=0,quantity=0):
        r=bytearray(68);struct.pack_into('<IBI',r,0,instance,kind,prop)
        struct.pack_into('<H',r,17,slot);struct.pack_into('<H',r,23,quantity)
        with self.s.db:self.s.db.execute('INSERT INTO inventory VALUES(?,?,?)',(1001,instance,bytes(r)))

    def test_single_weapon_denial_still_releases_native_wait(self):
        self.battle()
        self.assertEqual(self.e1.handle(self.c1,Message(4082)),[Message(4083,struct.pack('<QIII',1001,0,0,0))])
        self.assertEqual(self.e2.take_pending(self.c2),[])

    def test_dual_loadout_free_switch_preserves_real_token_count_and_profile(self):
        self.add(2000000,25,253033,9);self.add(2000001,74,740001,quantity=99)
        self.battle();before=self.s.snapshot(1001)
        wanted=[Message(4083,struct.pack('<QIII',1001,0,1,99))]
        for _ in range(2):self.assertEqual(self.e1.handle(self.c1,Message(4082)),wanted)
        self.assertEqual(self.s.snapshot(1001),before)
        self.assertEqual(self.e2.take_pending(self.c2),[])

    def test_dual_loadout_without_ticket_allowed_by_explicit_free_policy(self):
        self.add(2000000,25,253033,9);self.battle()
        self.assertEqual(self.e1.handle(self.c1,Message(4082)),[Message(4083,struct.pack('<QIII',1001,0,1,0))])

    def test_ambiguous_inventory_denied_without_mutation(self):
        self.add(2000000,25,253033,9);self.add(2000001,74,740001,quantity=99)
        self.add(2000002,74,740002,quantity=2);self.battle();before=self.s.snapshot(1001)
        self.assertEqual(self.e1.handle(self.c1,Message(4082))[0].payload[12:16],bytes(4))
        self.assertEqual(self.s.snapshot(1001),before)

    def test_wrong_length_and_inactive_phase_cannot_switch(self):
        self.assertEqual(self.e1.handle(self.c1,Message(4082)),[])
        self.battle()
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,Message(4082,b'x'))
        self.e1.room.stage='result'
        self.assertEqual(self.e1.handle(self.c1,Message(4082)),[])


if __name__=='__main__':unittest.main()
