"""Distinct reborn16 Host sync and native-result prefix; no guessed revival."""
import struct
import unittest
from server.kk_local import packets
from server.kk_local.engine import Phase
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_lab_settlement as fixtures
from server.tests import test_shared_rooms as room_fixtures
from server.tests.test_local_service import create_room


def snapshot(sender=1001,seq=0):
    p=bytearray(334);struct.pack_into('<IQ',p,0,8155,sender)
    p[12:14]=b'\x01\x01';struct.pack_into('<I',p,19,seq)
    p[39:46]=b'PREFIX7'
    for slot,uid in enumerate((1001,1002)):
        struct.pack_into('<QIIHH',p,46+slot*36,uid,100+slot,1,2,3)
    return Message(8071,bytes(p))


def event(code=2,value=0,sender=1001,target=1002,seq=1):
    p=bytearray(55);struct.pack_into('<IQ',p,0,8157,sender)
    p[12:14]=b'\x01\x01';struct.pack_into('<I',p,19,seq)
    struct.pack_into('<QII',p,39,target,code,value)
    return Message(8071,bytes(p))


class Mode16Tests(unittest.TestCase):
    mode=16
    setUp=fixtures.LabSettlementTests.setUp
    tearDown=fixtures.LabSettlementTests.tearDown
    report=fixtures.LabSettlementTests.report

    def reborn_report(self,points=(150,100)):
        p=bytearray(self.report((30,100)))
        for slot,value in enumerate(points):
            o=87*slot
            struct.pack_into('<HH',p,o+4,60+slot,70+slot)
            struct.pack_into('<iiHH',p,o+37,value,1,4+slot,2+slot)
        return bytes(p)

    def test_admission_and_six_row_limit_remain_distinct_from_other_modes(self):
        self.assertEqual(self.a.room.request[46],16)
        request=bytearray(create_room().payload);request[46]=16;request[37]=8
        with self.assertRaises(packets.RoomRequestRejected):packets.resolve_room_request(bytes(request))
        request[46]=1
        self.assertEqual(packets.resolve_room_request(bytes(request))[37],8)

    def test_host_snapshot_and_event_are_byte_exact_no_echo_or_database_write(self):
        for msg in (snapshot(),event()):
            self.assertEqual(self.a.handle(self.ca,msg),[])
            self.assertEqual(self.b.take_pending(self.cb),[msg])
            self.a.handle(self.ca,msg)
            self.assertEqual(self.b.take_pending(self.cb),[])
        self.assertEqual([self.store.snapshot(u) for u in (1001,1002)],self.before)

    def test_snapshot_identity_vacancy_and_host_are_checked_before_watermark(self):
        self.b.handle(self.cb,snapshot(sender=1002))
        self.assertEqual(self.a.take_pending(self.ca),[])
        for offset,value,fmt in ((46,9999,'<Q'),(46+36,1001,'<Q'),(46+72+8,1,'<I')):
            p=bytearray(snapshot().payload);struct.pack_into(fmt,p,offset,value)
            with self.assertRaises(ProtocolError):self.a.handle(self.ca,Message(8071,bytes(p)))
        self.assertEqual(self.a.room.last_sequence,{})
        self.a.room.owner=1002
        self.b.handle(self.cb,snapshot(sender=1002))
        self.assertEqual(self.a.take_pending(self.ca),[snapshot(sender=1002)])

    def test_event_native_ranges_and_identity_never_invent_new_events(self):
        for code,value in ((8,0),(3,4),(5,4),(7,3),(2,3),(7,65536)):
            self.a.handle(self.ca,event(code,value))
        self.assertEqual(self.b.take_pending(self.cb),[])
        self.assertEqual(self.a.room.last_sequence,{})
        with self.assertRaises(ProtocolError):self.a.handle(self.ca,event(target=9999))
        for seq,(code,value) in enumerate(((0,0),(1,0),(2,0),(3,3),(4,4),(5,5),(6,3),(7,4))):
            msg=event(code,value,seq=seq)
            self.a.handle(self.ca,msg)
            self.assertEqual(self.b.take_pending(self.cb),[msg])

    def test_wrong_mode_or_result_stage_cannot_consume_snapshot(self):
        r=self.a.room;old=r.request;p=bytearray(old);p[46]=1;r.request=bytes(p)
        self.assertEqual(self.a.handle(self.ca,snapshot()),[])
        r.request=old;r.stage='result'
        self.assertEqual(self.a.handle(self.ca,snapshot()),[])
        self.assertEqual(self.b.take_pending(self.cb),[])
        self.assertEqual(r.last_sequence,{})

    def test_native_points_not_hp_or_ordinary_low8_kills_decide_local_result(self):
        p=self.reborn_report()
        self.a.handle(self.ca,Message(4110,p));self.assertEqual(self.b.take_pending(self.cb),[Message(4100)])
        out=self.b.handle(self.cb,Message(4110,p))
        self.assertEqual([m.id for m in out],[4120])
        for messages,uid in ((out,1002),(self.a.take_pending(self.ca),1001)):
            data=messages[0].payload
            for slot,u in enumerate((1001,1002)):
                row=data[500*slot:500*(slot+1)]
                self.assertEqual(row[10],1 if u==1001 else 2)  #HP30 still wins by150 points.
                self.assertEqual(struct.unpack_from('<iHHIII',row,67),
                                 ((150,100)[slot],4+slot,2+slot,60+slot,70+slot,slot+1))
                self.assertEqual(struct.unpack_from('<I',row,34)[0],0)
                self.assertEqual(row[140:],self.store.snapshot(uid)[2] if u==uid else bytes(360))
        self.assertEqual([self.store.snapshot(u) for u in (1001,1002)],self.before)

    def test_disagreement_rejected_and_negative_points_preserved(self):
        p=self.reborn_report((-25,-50))
        self.a.handle(self.ca,Message(4110,p));self.b.take_pending(self.cb)
        q=bytearray(p);struct.pack_into('<i',q,37,-24)
        with self.assertRaises(ProtocolError):self.b.handle(self.cb,Message(4110,bytes(q)))
        self.assertFalse(self.a.room.result_replies)
        out=self.b.handle(self.cb,Message(4110,p))
        self.assertEqual(struct.unpack_from('<i',out[0].payload,67)[0],-25)

    def test_tied_points_draw_stable_display_order_and_complete_return_barrier(self):
        p=self.reborn_report((100,100))
        self.a.handle(self.ca,Message(4110,p));self.b.take_pending(self.cb)
        out=self.b.handle(self.cb,Message(4110,p));self.a.take_pending(self.ca)
        self.assertEqual((out[0].payload[10],out[0].payload[510]),(0,0))
        self.assertEqual((struct.unpack_from('<I',out[0].payload,83)[0],struct.unpack_from('<I',out[0].payload,583)[0]),(1,2))
        self.a.handle(self.ca,Message(4115,bytes(4)))
        self.b.handle(self.cb,Message(4115,bytes(4)));self.a.take_pending(self.ca)
        self.assertEqual((self.ca.phase,self.cb.phase),(Phase.ROOM,Phase.ROOM))


class Mode16NetworkTests(room_fixtures.SharedRoomNetworkTests):
    mode=16


if __name__=='__main__':unittest.main()
