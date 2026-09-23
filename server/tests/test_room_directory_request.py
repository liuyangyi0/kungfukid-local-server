"""Native2260 page/filter/mode; first-page1 must not silently mean Mode1."""
import unittest
from server.kk_local.engine import Phase
from server.kk_local.wire import Message
from server.tests import test_shared_rooms as fixtures
from server.tests.test_local_service import create_room


class DirectoryRequestTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown

    def create(self,mode):
        p=bytearray(create_room().payload);p[46]=mode
        self.e1.handle(self.c1,Message(3010,bytes(p)))

    def count(self,page=1,filter=1,mode=136):
        out=self.e2.handle(self.c2,Message(2260,bytes((page,filter,mode))))
        self.assertEqual([m.id for m in out],[2280])
        return (len(out[0].payload)-8)//259

    def test_first_page_all_includes_reborn16_and_zero_is_not_all(self):
        self.create(16)
        self.assertEqual(self.count(),1)
        self.assertEqual(self.count(mode=16),1)
        self.assertEqual(self.count(mode=0),0)
        self.assertEqual(self.count(mode=1),0)
        self.assertEqual(self.count(page=2),0)
        self.assertEqual(self.count(page=0),1)  # explicit local first-page normalization

    def test_survival_zero_is_a_real_mode(self):
        self.create(0)
        self.assertEqual(self.count(mode=0),1)
        self.assertEqual(self.count(mode=16),0)
        self.assertEqual(self.count(),1)

    def test_waiting_filter_and_invalid_toggle_do_not_mutate(self):
        self.create(1);room=self.e1.room
        self.assertEqual(self.count(filter=0),1)
        room.stage='battle';self.c1.phase=Phase.BATTLE
        self.assertEqual(self.count(filter=0),0)
        self.assertEqual(self.count(filter=1),1)
        self.assertEqual(self.count(filter=2),0)
        self.assertEqual(room.stage,'battle')

    def test_auto_join_all_sentinel_and_exact_mode_are_not_conflated(self):
        self.create(16)
        self.assertEqual(self.e2.handle(self.c2,Message(3075,b'\0'))[0].id,20150)
        out=self.e2.handle(self.c2,Message(3075,b'\x88'))
        self.assertEqual([m.id for m in out],[3100,3160,3090])
        self.assertIs(self.e1.room,self.e2.room)


if __name__=='__main__':unittest.main()
