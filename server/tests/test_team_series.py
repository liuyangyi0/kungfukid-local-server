import struct
import unittest
from server.kk_local.team_series import decode_series_report,no_award_series_result
from server.kk_local.wire import ProtocolError
from server.kk_local.wire import Message,encode_game
from server.tests import test_shared_rooms as fixtures
from server.tests.test_local_service import create_room


def report_bytes(uids=(1002,1001),scores=(2,1),perfect=0):
    data=bytearray(309);data[2]=perfect
    struct.pack_into('<iiHH',data,3,*scores,65535,65535)
    for i,uid in enumerate(uids):
        struct.pack_into('<Q21sH',data,61+31*i,uid,b'CLIENT-SUPPLIED',65535)
    return data


class TeamSeriesCodecTests(unittest.TestCase):
    def test_exact_layout_and_names_owned_not_references_to_mutable_input(self):
        raw=report_bytes();decoded=decode_series_report(raw,{1001,1002})
        self.assertEqual(decoded.scores,(2,1))
        self.assertEqual([r.uid for r in decoded.rows],[1002,1001])
        self.assertEqual(decoded.rows[0].value_raw,65535)
        raw[69]=0
        self.assertEqual(decoded.rows[0].name_raw,b'CLIENT-SUPPLIED'.ljust(21,b'\0'))
        for size in (0,308,310,696,1000):
            with self.assertRaises(ProtocolError):decode_series_report(bytes(size),{1001,1002})

    def test_missing_duplicate_foreign_and_nonempty_unused_row_rejected(self):
        for uids in ((1001,),(1001,1001),(1001,9999)):
            with self.assertRaises(ProtocolError):decode_series_report(report_bytes(uids),{1001,1002})
        raw=report_bytes();raw[61+31*2+8]=1
        with self.assertRaises(ProtocolError):decode_series_report(raw,{1001,1002})
        raw=report_bytes();raw[69:90]=b'x'*21
        with self.assertRaises(ProtocolError):decode_series_report(raw,{1001,1002})

    def test_no_award_response_uses_public_names_not_reported_names_or_values(self):
        decoded=decode_series_report(report_bytes(),{1001,1002})
        out=no_award_series_result(decoded,{1001:'甲',1002:'乙'},{1001:0,1002:1},1002)
        self.assertEqual(len(out),309)
        self.assertEqual(struct.unpack_from('<ii',out,3),(2,1))  # Host team remains first
        self.assertEqual(out[19:61],bytes(42))  # no invented organization records
        self.assertEqual(out[69:90],'乙'.encode('gbk').ljust(21,b'\0'))
        self.assertNotIn(b'CLIENT-SUPPLIED',out)
        self.assertEqual([struct.unpack_from('<h',out,90+i*31)[0] for i in (0,1)],[0,0])

    def test_invalid_score_flag_and_public_names_fail_closed(self):
        for data in (report_bytes(scores=(-1,0)),report_bytes(perfect=2)):
            with self.assertRaises(ProtocolError):decode_series_report(data,{1001,1002})
        decoded=decode_series_report(report_bytes(),{1001,1002})
        for name in ('','x'*21,'x\0y','😀'):
            with self.assertRaises(ProtocolError):
                no_award_series_result(decoded,{1001:name,1002:'B'},{1001:0,1002:1},1001)

    def test_ui_team_capacity_and_host_membership_are_not_guessed(self):
        decoded=decode_series_report(report_bytes(),{1001,1002})
        with self.assertRaises(ProtocolError):no_award_series_result(decoded,{1001:'A',1002:'B'},{1001:0,1002:0},1001)
        with self.assertRaises(ProtocolError):no_award_series_result(decoded,{1001:'A',1002:'B'},{1001:0,1002:1},9999)
        uids=tuple(range(1,7));decoded=decode_series_report(report_bytes(uids),uids)
        names={u:str(u) for u in uids}
        with self.assertRaises(ProtocolError):no_award_series_result(decoded,names,{u:int(u>4) for u in uids},1)
        self.assertEqual(len(no_award_series_result(decoded,names,{u:int(u>3) for u in uids},1)),309)


def series_event(ident,*,sender=1001,seq=0,scores=(1,0),value=123):
    p=bytearray({8294:47,8295:39,8296:43,8297:39}[ident])
    struct.pack_into('<IQ',p,0,ident,sender);p[12:14]=b'\x01\x01';struct.pack_into('<I',p,19,seq)
    if ident==8294:struct.pack_into('<II',p,39,*scores)
    if ident==8296:struct.pack_into('<I',p,39,value)
    return Message(8071,bytes(p))


class TeamSeriesFlowTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    battle=fixtures.SharedRoomTests.battle

    def join(self):
        self.h.team_series_rounds=3
        p=bytearray(create_room().payload);p[46]=1;p[37]=2
        entry=self.e1.handle(self.c1,Message(3010,bytes(p)))[0]
        self.assertEqual(struct.unpack_from('<I',entry.payload,74)[0],3)
        peer=self.e2.handle(self.c2,Message(3070,struct.pack('<HB11s',1,0,b'')))[0]
        self.assertEqual(struct.unpack_from('<I',peer.payload,74)[0],3)
        self.e1.take_pending(self.c1)

    def interval(self):
        msg=series_event(8294)
        self.e1.handle(self.c1,msg);self.assertEqual(self.e2.take_pending(self.c2),[msg])
        ready=series_event(8296,sender=1002)
        self.e2.handle(self.c2,ready);self.assertEqual(self.e1.take_pending(self.c1),[ready])
        go=series_event(8295,seq=1)
        self.e1.handle(self.c1,go);self.assertEqual(self.e2.take_pending(self.c2),[go])

    def finish(self):
        end=series_event(8297,seq=2)
        self.e1.handle(self.c1,end);self.assertEqual(self.e2.take_pending(self.c2),[end])
        msg=Message(4111,bytes(report_bytes(scores=(2,0),perfect=1)))
        out=self.e1.handle(self.c1,msg);other=self.e2.take_pending(self.c2)
        self.assertEqual([m.id for m in out],[4112]);self.assertEqual(out,other)
        return msg

    def test_interval_host_continue_final_and_real3550_return_barrier(self):
        self.battle();before=[self.s.snapshot(u) for u in (1001,1002)]
        self.interval();msg=self.finish();room=self.e1.room
        self.assertEqual(room.stage,'result')
        self.e1.handle(self.c1,msg);self.assertEqual(self.e2.take_pending(self.c2),[])
        for e,c in ((self.e1,self.c1),(self.e2,self.c2)):
            self.assertEqual(e.handle(c,Message(4115,bytes(4))),[])
            self.assertEqual(e.handle(c,Message(3550,struct.pack('<QI',c.uid,0))),[])
        self.assertEqual(room.result_acks,set())  # neither ordinary ACK nor stale0
        for e,c in ((self.e1,self.c1),(self.e2,self.c2)):
            e.handle(c,Message(3550,struct.pack('<QI',c.uid,3)))
            e.handle(c,Message(3550,struct.pack('<QI',c.uid,0)))
        self.assertEqual(room.stage,'room')
        self.assertEqual([self.s.snapshot(u) for u in (1001,1002)],before)
        self.e1.take_pending(self.c1);self.e2.take_pending(self.c2)
        self.e2.handle(self.c2,Message(4030));self.e1.take_pending(self.c1)
        self.e1.handle(self.c1,Message(4030))
        self.assertEqual(room.series.scores,(0,0));self.assertIsNone(room.series.report)

    def test_invalid_sender_order_and_rollback_do_not_consume_versions(self):
        self.battle();room=self.e1.room
        for msg in (series_event(8295,seq=999),series_event(8294,seq=999,scores=(3,0))):
            self.assertEqual(self.e1.handle(self.c1,msg),[])
        self.e2.handle(self.c2,series_event(8294,sender=1002,seq=999))
        self.assertFalse(room.series.versions)
        with self.assertRaises(ProtocolError):self.e2.handle(self.c2,series_event(8294))
        self.interval()
        self.e1.handle(self.c1,series_event(8294,seq=2,scores=(0,0)))
        self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(room.series.scores,(1,0))

    def test_no_fake_4112_before_native_final_or_for_nonhost(self):
        self.battle();self.interval()
        msg=Message(4111,bytes(report_bytes(scores=(2,0),perfect=1)))
        self.assertEqual(self.e1.handle(self.c1,msg),[])
        with self.assertRaises(ProtocolError):self.e2.handle(self.c2,msg)
        self.finish()
        changed=Message(4111,bytes(report_bytes(scores=(2,1))))
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,changed)

    def test_native_skip_or_direct_peer_ready_does_not_require_invented_tcp_votes(self):
        self.battle()
        self.e1.handle(self.c1,series_event(8294));self.e2.take_pending(self.c2)
        go=series_event(8295,seq=1)
        self.e1.handle(self.c1,go)
        self.assertEqual(self.e2.take_pending(self.c2),[go])
        self.assertEqual(self.e1.room.series.phase,'playing')

    def test_ordinary_mode_stays_disabled_and_incompatible_policies_rejected(self):
        from server.kk_local.rooms import RoomHub
        for kw in ({'team_series_rounds':2},{'team_series_rounds':True},
                   {'team_series_rounds':3,'spectator_capacity':1},
                   {'team_series_rounds':3,'match_point_rewards':{0:0,1:0,2:0}}):
            with self.assertRaises(ValueError):RoomHub(**kw)
        self.h.team_series_rounds=3
        self.e1.handle(self.c1,create_room())  # Mode5 does not inherit series config
        self.assertIsNone(self.e1.room.series)


class TeamSeriesPeerTests(unittest.TestCase):
    def test_udp_interval_then_tcp_ready_continue_does_not_double_deliver(self):
        from server.tests.test_sdp_peer import SdpPeerTests
        from server.kk_local.engine import Phase
        from server.kk_local.team_series import SeriesProgress
        fixture=SdpPeerTests();fixture.setUp()
        try:
            room=fixture.a.engine.room;room.stage='battle';room.series=SeriesProgress(3)
            for c in fixture.clients:c.phase=Phase.BATTLE
            msg=series_event(8294)
            body=encode_game(msg)
            wire=fixture.packet(fixture.a,1008,body,struct.pack('<I',fixture.b.engine.p2p['player']))
            fixture.router.handle(fixture.a,wire,fixture.a.engine.p2p['peer'])
            self.assertEqual(room.series.phase,'interval');self.assertEqual(room.last_sequence,{})
            self.assertEqual(fixture.b.udp.sent[-1][0][24:],body)
            fixture.a.engine.handle(fixture.clients[0],msg)
            self.assertEqual(fixture.b.engine.take_pending(fixture.clients[1]),[])
            ready=series_event(8296,sender=1002)
            fixture.b.engine.handle(fixture.clients[1],ready)
            self.assertEqual(fixture.a.engine.take_pending(fixture.clients[0]),[ready])
        finally:fixture.tearDown()


class TeamSeriesNetworkTests(fixtures.SharedRoomNetworkTests):
    mode=1
    series_rounds=3


if __name__=='__main__':unittest.main()
