import struct
import unittest
from server.kk_local.engine import Connection,Engine,Phase,Room
from server.kk_local.store import Store
from server.kk_local.wire import Message
from server.kk_local.team_series import SeriesProgress
from server.tests import test_shared_rooms as fixture


class BattleClockTests(unittest.TestCase):
    setUp=fixture.SharedRoomTests.setUp
    tearDown=fixture.SharedRoomTests.tearDown
    join=fixture.SharedRoomTests.join
    battle=fixture.SharedRoomTests.battle

    def test_shared_clock_starts_at_all_ready_and_is_not_doubled(self):
        now=[100.0];self.e1.clock=self.e2.clock=lambda:now[0]
        self.battle()
        room=self.e1.room;self.assertEqual(room.battle_clock_origin,100)
        now[0]=100.99
        self.e1.poll_battle_clock(self.c1)
        self.assertEqual(self.e1.take_pending(self.c1),[])
        now[0]=101
        self.e1.poll_battle_clock(self.c1);self.e2.poll_battle_clock(self.c2)
        expected=[Message(8090,struct.pack('<I',1))]
        self.assertEqual(self.e1.take_pending(self.c1),expected)
        self.assertEqual(self.e2.take_pending(self.c2),expected)
        now[0]=104.1;self.e2.poll_battle_clock(self.c2)
        self.assertEqual(self.e1.take_pending(self.c1),[Message(8090,struct.pack('<I',4))])
        self.e2.take_pending(self.c2)
        now[0]=103;self.e1.poll_battle_clock(self.c1)
        self.assertEqual(self.e1.take_pending(self.c1),[])

    def test_wrong_connection_result_and_series_interval_do_not_tick(self):
        now=[100.0];self.e1.clock=self.e2.clock=lambda:now[0];self.battle()
        room=self.e1.room;now[0]=110
        self.e1.poll_battle_clock(Connection(999,Phase.BATTLE,1001))
        room.stage='result';self.e1.poll_battle_clock(self.c1)
        room.stage='battle';room.series=SeriesProgress(3,phase='interval')
        self.e1.poll_battle_clock(self.c1)
        self.assertEqual(self.e1.take_pending(self.c1),[])
        self.assertEqual(room.battle_clock_last,0)

    def test_normal_team_room_is_not_experimental_best_of_three(self):
        self.assertEqual(self.h.team_series_rounds,0)
        self.battle();self.assertIsNone(self.e1.room.series)

    def test_single_player_uses_same_packet_without_mp_mutation(self):
        with_store=Store(':memory:')
        try:
            with_store.seed_local();now=[20.0];e=Engine(with_store,clock=lambda:now[0])
            e.game=c=Connection(2,Phase.WAIT_READY,1001)
            e.room=Room(1001,bytes(81),bytes(245),serial=1)
            before=with_store.snapshot(1001)
            self.assertEqual([m.id for m in e.handle(c,Message(8040,struct.pack('<HQI',1,1001,0)))],[8070])
            now[0]=21;e.poll_battle_clock(c)
            self.assertEqual(e.take_pending(c),[Message(8090,struct.pack('<I',1))])
            self.assertEqual(before,with_store.snapshot(1001))
            c.phase=Phase.ROOM;now[0]=22;e.poll_battle_clock(c)
            self.assertEqual(e.take_pending(c),[])
        finally:with_store.close()
