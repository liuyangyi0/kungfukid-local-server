import struct
import unittest

from server.kk_local.engine import Engine, Phase
from server.kk_local.lab_settlement import decode_report, result_payload
from server.kk_local.rooms import RoomHub
from server.kk_local.store import Store
from server.kk_local.wire import Message, ProtocolError
from server.tests.test_local_service import create_room
from server.tests.test_shared_rooms import lobby


class LabSettlementTests(unittest.TestCase):
    def setUp(self):
        self.store=Store(':memory:');self.store.seed_local();self.store.provision_local(1002,'Second')
        self.hub=RoomHub(lab_no_award_settlement=True)
        self.a=Engine(self.store,hub=self.hub);self.b=Engine(self.store,hub=self.hub,account_uid=1002)
        self.ca=lobby(self.a,1);self.cb=lobby(self.b,3)
        request=bytearray(create_room().payload);request[46]=getattr(self,'mode',1)
        self.a.handle(self.ca,Message(3010,bytes(request)))
        self.b.handle(self.cb,Message(3070,struct.pack('<HB11s',1,0,b'')))
        self.a.take_pending(self.ca)
        self.b.handle(self.cb,Message(4030));self.a.take_pending(self.ca)
        self.a.handle(self.ca,Message(4030));self.b.take_pending(self.cb)
        self.a.handle(self.ca,Message(4160));self.b.take_pending(self.cb)
        self.b.handle(self.cb,Message(4160));self.a.take_pending(self.ca)
        self.a.handle(self.ca,Message(8040,struct.pack('<HQI',1,1001,0)))
        self.b.handle(self.cb,Message(8040,struct.pack('<HQI',1,1002,0)))
        self.a.take_pending(self.ca)
        self.before=[self.store.snapshot(u) for u in (1001,1002)]

    def tearDown(self): self.store.close()

    def report(self,hp=(100,100)):
        p=bytearray(696)
        for slot,uid in enumerate((1001,1002)):
            base=slot*87
            struct.pack_into('<HH',p,base,100,hp[slot])
            struct.pack_into('<Q',p,base+29,uid)
            struct.pack_into('<HII',p,base+65,65535,1,self.a.room.serial)
        return bytes(p)

    def test_consensus_draw_private_profiles_no_rewards(self):
        p=self.report()
        self.assertEqual(self.a.handle(self.ca,Message(4110,p)),[])
        out=self.b.handle(self.cb,Message(4110,p))
        other=self.a.take_pending(self.ca)
        for messages,uid in ((other,1001),(out,1002)):
            self.assertEqual([m.id for m in messages],[4120])
            data=messages[0].payload;self.assertEqual(len(data),1000)
            for slot,row_uid in enumerate((1001,1002)):
                row=data[slot*500:(slot+1)*500]
                self.assertEqual(struct.unpack_from('<Q',row)[0],row_uid)
                self.assertEqual(row[10],0)
                self.assertEqual(row[140:],self.store.snapshot(uid)[2] if row_uid==uid else bytes(360))
                self.assertEqual(row[34:38],bytes(4));self.assertEqual(row[63:67],bytes(4))
        self.assertEqual(self.before,[self.store.snapshot(u) for u in (1001,1002)])

    def test_all_ack_barrier_and_no_duplicate_teardown(self):
        p=self.report()
        self.a.handle(self.ca,Message(4110,p));self.b.handle(self.cb,Message(4110,p));self.a.take_pending(self.ca)
        self.assertEqual(self.a.handle(self.ca,Message(4110,p)),[])
        self.a.handle(self.ca,Message(4115,struct.pack('<I',180000)))
        self.assertEqual(self.a.room.stage,'result')
        self.assertEqual(self.a.handle(self.ca,Message(4030)),[])
        self.b.handle(self.cb,Message(4115,struct.pack('<I',180000)))
        self.assertEqual((self.ca.phase,self.cb.phase),(Phase.ROOM,Phase.ROOM))
        self.assertEqual(self.a.room.stage,'room')
        self.assertFalse(self.a.room.loaded or self.a.room.input_ready)
        self.assertTrue(all(not m.ready for m in self.a.room.members.values()))
        self.a.take_pending(self.ca)
        self.assertEqual(self.a.handle(self.ca,Message(4115,bytes(4))),[])

    def test_survivor_only_policy_and_unknown_result_parameter(self):
        p=self.report((100,0))
        self.a.handle(self.ca,Message(4110,p));out=self.b.handle(self.cb,Message(4110,p))
        self.assertEqual((out[0].payload[10],out[0].payload[510]),(1,2))

    def test_disagreement_is_not_silently_reconciled(self):
        self.a.handle(self.ca,Message(4110,self.report()))
        with self.assertRaisesRegex(ProtocolError,'disagreement'):
            self.b.handle(self.cb,Message(4110,self.report((100,0))))
        self.assertEqual(set(self.a.room.result_reports),{1001})
        self.assertFalse(self.a.room.result_replies)
        self.assertEqual(self.a.room.stage,'battle')

    def test_invalid_identity_stale_hp_and_unused_slot(self):
        for offset,fmt,value in ((29,'<Q',9999),(67,'<I',99),(71,'<I',999),(2,'<H',101),(174,'<B',1)):
            p=bytearray(self.report());struct.pack_into(fmt,p,offset,value)
            with self.assertRaises(ProtocolError):self.a.handle(self.ca,Message(4110,p))
            self.assertFalse(self.a.room.result_reports)
        with self.assertRaises(ProtocolError):decode_report(bytes(695),{0:1001},1,1)

    def test_changed_duplicate_rejected_and_raw_buffer_owned(self):
        p=bytearray(self.report());self.a.handle(self.ca,Message(4110,p));p[0]=0
        self.assertEqual(self.a.room.result_reports[1001][1001].maximum_hp,100)
        with self.assertRaises(ProtocolError):self.a.handle(self.ca,Message(4110,self.report((0,100))))

    def test_can_be_disabled_and_no_profile_zero_identity(self):
        self.hub.lab_no_award_settlement=False
        self.assertIsNone(self.hub.handle(self.a,self.ca,Message(4110,self.report())))
        with self.assertRaises(ProtocolError):result_payload({1001:0},1001,bytes(360))

    def test_new_battle_clears_report_state_and_rejects_old_serial(self):
        p=self.report();self.a.handle(self.ca,Message(4110,p));self.b.handle(self.cb,Message(4110,p));self.a.take_pending(self.ca)
        self.a.handle(self.ca,Message(4115,bytes(4)));self.b.handle(self.cb,Message(4115,bytes(4)));self.a.take_pending(self.ca)
        self.b.handle(self.cb,Message(4030));self.a.take_pending(self.ca)
        self.a.handle(self.ca,Message(4030))
        self.assertFalse(self.a.room.result_reports or self.a.room.result_replies or self.a.room.result_acks)
        # Loading must not consume a queued previous-round report.
        self.assertEqual(self.a.handle(self.ca,Message(4110,p)),[])
