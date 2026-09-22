import struct
import unittest
import xml.etree.ElementTree as ET
from server.kk_local.talisman import TalismanCatalog,equipped,spend
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_shared_rooms as fixture


class TalismanTests(unittest.TestCase):
    setUp=fixture.SharedRoomTests.setUp
    tearDown=fixture.SharedRoomTests.tearDown
    join=fixture.SharedRoomTests.join
    battle=fixture.SharedRoomTests.battle

    def item(self,instance=2000000,slot=37,quantity=100):
        raw=bytearray(68);struct.pack_into('<IBII',raw,0,instance,30,303001,30300101)
        struct.pack_into('<H',raw,17,slot);struct.pack_into('<H',raw,23,quantity)
        with self.s.db:self.s.db.execute('INSERT INTO inventory VALUES(1001,?,?)',(instance,bytes(raw)))
        return instance

    def event(self,kind=8292,sequence=1,slot=37,uid=1001):
        p=bytearray(75);struct.pack_into('<IQ',p,0,kind,uid);p[12:14]=b'\1\1'
        struct.pack_into('<I',p,19,sequence);struct.pack_into('<I',p,39,slot)
        struct.pack_into('<QII',p,59,uid,self.e1.room.number,self.e1.room.serial)
        return Message(8071,bytes(p))

    def prepare(self,quantity=100):
        self.h.talisman_catalog=TalismanCatalog({303001:(8,20)})
        instance=self.item(quantity=quantity);self.battle();return instance

    def use(self,instance,kind=8292,sequence=1,slot=37):
        msg=self.event(kind,sequence,slot)
        self.assertEqual(self.e1.handle(self.c1,msg),[])
        self.assertEqual(self.e2.take_pending(self.c2),[])
        out=self.e1.handle(self.c1,Message(4201,struct.pack('<II',instance,0xffffffff)))
        return msg,out

    def test_active_pair_commits_once_and_ignores_claimed_price(self):
        instance=self.prepare();msg,out=self.use(instance)
        self.assertEqual(out,[Message(4206,struct.pack('<III',instance,80,0))])
        self.assertEqual(self.e2.take_pending(self.c2),[msg])
        _,out=self.use(instance)
        self.assertEqual(out,[Message(4206,struct.pack('<III',instance,80,0))])
        self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM talisman_uses').fetchone()[0],1)

    def test_passive_reapply_relays_but_fee_only_once_per_battle(self):
        instance=self.prepare()
        for seq in (1,2):
            msg,out=self.use(instance,8291,seq)
            self.assertEqual(out,[Message(4206,struct.pack('<III',instance,92,0))])
            self.assertEqual(self.e2.take_pending(self.c2),[msg])

    def test_second_slot_and_unequip_preserve_full_record(self):
        instance=self.item(slot=0)
        first=self.s.snapshot(1001)[3]
        self.s.equip(1001,instance,38)
        self.assertEqual(equipped(self.s,1001,slot=38)[0],instance)
        self.s.unequip(1001,instance)
        self.assertEqual(first,self.s.snapshot(1001)[3])

    def test_no_intent_no_quota_and_no_peer_effect(self):
        instance=self.prepare(quantity=1)
        self.assertEqual(self.e1.handle(self.c1,Message(4201,struct.pack('<II',instance,1))),[])
        _,out=self.use(instance)
        self.assertEqual(out,[Message(4207,struct.pack('<II',instance,303001))])
        self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(struct.unpack_from('<H',equipped(self.s,1001,instance=instance)[1],23)[0],1)

    def test_cross_account_and_stale_battle_are_not_billed(self):
        self.prepare()
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,self.event(uid=1002))
        p=bytearray(self.event().payload);struct.pack_into('<I',p,71,999)
        self.assertEqual(self.e1.handle(self.c1,Message(8071,bytes(p))),[])
        self.assertFalse(self.e1.room.talisman_pending)

    def test_billing_failure_rolls_back_quota(self):
        instance=self.prepare()
        self.s.db.execute("CREATE TRIGGER fail_talisman BEFORE INSERT ON talisman_uses BEGIN SELECT RAISE(ABORT,'fixture'); END")
        before=self.s.snapshot(1001)
        import sqlite3
        with self.assertRaises(sqlite3.IntegrityError):spend(self.s,1001,1,instance,37,8292,1,20)
        self.assertEqual(before,self.s.snapshot(1001))

    def test_expired_pair_and_unknown_catalog_do_not_forward(self):
        instance=self.prepare();now=[100.0];self.e1.clock=lambda:now[0]
        self.e1.handle(self.c1,self.event());now[0]+=6
        self.assertEqual(self.e1.handle(self.c1,Message(4201,struct.pack('<II',instance,20))),[])
        self.assertEqual(self.e2.take_pending(self.c2),[])

    def test_cost_precision_and_conflict_checks(self):
        c=TalismanCatalog.from_xml(ET.fromstring('<TalismanPro><Talisman Id="303001" EquipCostPerBattle="0.08" ActiveCost="0.82"/></TalismanPro>'))
        self.assertEqual(c.costs,{303001:(8,82)})
        for bad in ('NaN','-1','0.001','100000'):
            with self.assertRaises(ValueError):TalismanCatalog.from_xml(ET.fromstring(f'<TalismanPro><Talisman Id="1" EquipCostPerBattle="0" ActiveCost="{bad}"/></TalismanPro>'))

    def test_observed_udp_event_pairs_with_tcp_use_without_second_delivery(self):
        from server.kk_local.sdp_peer import SdpPeerRouter
        from server.kk_local.wire import encode_game
        from types import SimpleNamespace
        instance=self.prepare();msg=self.event()
        router=SdpPeerRouter(self.h)
        # Observe only a complete frame whose UDP recipient was already
        #validated and forwarded by the existing router; not a new relay.
        router.observe_selection(self.e1,[SimpleNamespace(engine=self.e2)],encode_game(msg))
        out=self.e1.handle(self.c1,Message(4201,struct.pack('<II',instance,20)))
        self.assertEqual(out,[Message(4206,struct.pack('<III',instance,80,0))])
        self.assertEqual(self.e2.take_pending(self.c2),[])
