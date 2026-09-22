"""Qualified8126 delivery without opening duplicate attacker-state creation."""
import struct
import unittest
import xml.etree.ElementTree as ET
from server.kk_local.combat_catalog import CombatCatalog
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_shared_rooms as fixtures
from server.tests.test_battle_effect_relay import effect


class CombatCatalogTests(unittest.TestCase):
    def test_empty_target_only_and_attacker_lists_differ(self):
        root=ET.fromstring('''<SkillProperty>
          <PropertyItem SkillProId="1"/>
          <PropertyItem SkillProId="2"><LogicEffects><TargetUstate UstateID="12"/></LogicEffects></PropertyItem>
          <PropertyItem SkillProId="3"><LogicEffects><AttackerUstate UstateID="13"/></LogicEffects></PropertyItem>
          <PropertyItem SkillProId="4"><Unknown/></PropertyItem>
        </SkillProperty>''')
        catalog=CombatCatalog.from_xml(root)
        self.assertEqual(catalog.safe_receipt_ids,frozenset((1,2)))
        self.assertFalse(catalog.permits_effect_free_receipt(999))

    def test_conflicting_duplicates_are_quarantined_even_if_effects_empty(self):
        root=ET.fromstring('''<SkillProperty>
          <PropertyItem SkillProId="1" SkillDamage="2"/>
          <PropertyItem SkillProId="1" SkillDamage="3"/>
          <PropertyItem SkillProId="1" SkillDamage="2"/>
          <PropertyItem SkillProId="2"/>
          <PropertyItem SkillProId="2"/>
        </SkillProperty>''')
        catalog=CombatCatalog.from_xml(root)
        self.assertEqual(catalog.conflicts,frozenset((1,)))
        self.assertEqual(catalog.safe_receipt_ids,frozenset((2,)))

    def test_invalid_identity_or_root_rejected(self):
        for xml in ('<Other/>','<SkillProperty><PropertyItem/></SkillProperty>',
                    '<SkillProperty><PropertyItem SkillProId="-1"/></SkillProperty>'):
            with self.assertRaises(ValueError):CombatCatalog.from_xml(ET.fromstring(xml))

    def test_guard_break_admission_excludes_conflicts_malformed_and_attacker_effects(self):
        catalog=CombatCatalog.from_xml(ET.fromstring('''<SkillProperty>
          <PropertyItem SkillProId="1" DefenceTear="1"/>
          <PropertyItem SkillProId="1" DefenceTear="0"/>
          <PropertyItem SkillProId="2" DefenceTear="oops"/>
          <PropertyItem SkillProId="3" DefenceTear="4294967296"/>
          <PropertyItem SkillProId="4" DefenceTear="1"><LogicEffects><AttackerUstate/></LogicEffects></PropertyItem>
          <PropertyItem SkillProId="5" DefenceTear="1"/>
        </SkillProperty>'''))
        for skill in (1,2,3,4,999):self.assertEqual(catalog.receipt_outcomes(skill,2,1),(2,))
        self.assertEqual(catalog.receipt_outcomes(5,2,1),(2,4))


class HitReceiptFlowTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join
    battle=fixtures.SharedRoomTests.battle

    def configure(self):
        self.h.combat_catalog=CombatCatalog.from_xml(ET.fromstring('''<SkillProperty>
          <PropertyItem SkillProId="811115"/>
          <PropertyItem SkillProId="811116"><LogicEffects><AttackerUstate/></LogicEffects></PropertyItem>
          <PropertyItem SkillProId="811117" DefenceTear="1"/>
        </SkillProperty>'''))
        self.battle()

    def hit(self,seq=0,skill=811115,status=1,callback=0):
        p=bytearray(effect(8121,self.e1.room,seq=seq).payload)
        struct.pack_into('<I',p,56,skill);p[85]=status;p[65]=callback
        m=Message(8071,bytes(p));self.e1.handle(self.c1,m)
        self.assertEqual(self.e2.take_pending(self.c2),[m])
        return m

    def receipt(self,seq=1,skill=811115,status=1):
        p=bytearray(71);struct.pack_into('<IQ',p,0,8126,1001)
        p[12:14]=b'\x01\x01';struct.pack_into('<I',p,19,seq)
        struct.pack_into('<QQQII',p,39,1001,1001,1002,skill,status)
        return Message(8071,bytes(p))

    def test_accepted_hit_then_correlated_receipt_forward_once(self):
        self.configure();self.hit()
        m=self.receipt()
        self.assertEqual(self.e1.handle(self.c1,m),[])
        self.assertEqual(self.e2.take_pending(self.c2),[m])
        self.e1.handle(self.c1,m)
        self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(self.e1.room.pending_hit_receipts[1001],[])

    def test_unknown_nonempty_or_missing_hit_do_not_authorize_receipt(self):
        self.configure()
        self.e1.handle(self.c1,self.receipt())
        self.assertEqual(self.e2.take_pending(self.c2),[])
        self.hit(skill=811116)
        self.e1.handle(self.c1,self.receipt(skill=811116))
        self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(self.e1.room.pending_hit_receipts,{})

    def test_result_mismatch_does_not_consume_matching_hit(self):
        self.configure();self.hit()
        self.e1.handle(self.c1,self.receipt(status=2))
        self.assertEqual(self.e2.take_pending(self.c2),[])
        self.e1.handle(self.c1,self.receipt())
        self.assertEqual(self.e2.take_pending(self.c2),[self.receipt()])

    def test_guard_break_receipt_changes_two_to_four_once_after_damage(self):
        self.configure();self.hit(skill=811117,status=2,callback=1)
        msg=self.receipt(skill=811117,status=4)
        self.e1.handle(self.c1,msg)
        self.assertEqual(self.e2.take_pending(self.c2),[msg])
        self.e1.handle(self.c1,self.receipt(seq=2,skill=811117,status=2))
        self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(self.e1.room.pending_hit_receipts[1001],[])

    def test_guard_break_requires_skill_and_native_guard_callback_shape(self):
        self.configure()
        for seq,skill,status,callback in ((0,811115,2,1),(2,811117,1,1),(4,811117,2,0)):
            self.hit(seq=seq,skill=skill,status=status,callback=callback)
            self.e1.handle(self.c1,self.receipt(seq=seq+1,skill=skill,status=4))
            self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(len(self.e1.room.pending_hit_receipts[1001]),3)

    def test_normal_receipt_then_death_followup_is_not_a_duplicate(self):
        self.configure();self.hit()
        self.e1.handle(self.c1,self.receipt(status=3))
        self.assertEqual(self.e2.take_pending(self.c2),[])  # no primary yet
        primary=self.receipt();self.e1.handle(self.c1,primary)
        self.assertEqual(self.e2.take_pending(self.c2),[primary])
        terminal=self.receipt(seq=2,status=3);self.e1.handle(self.c1,terminal)
        self.assertEqual(self.e2.take_pending(self.c2),[terminal])
        self.e1.handle(self.c1,self.receipt(seq=3,status=3))
        self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(self.e1.room.pending_death_receipts[1001],[])

    def test_guard_cannot_authorize_normal_death_followup(self):
        self.configure();self.hit(skill=811117,status=2,callback=1)
        self.e1.handle(self.c1,self.receipt(skill=811117,status=4));self.e2.take_pending(self.c2)
        self.e1.handle(self.c1,self.receipt(seq=2,skill=811117,status=3))
        self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual(self.e1.room.pending_death_receipts,{})

    def test_terminal_witness_is_bounded_and_does_not_survive_source_departure(self):
        self.configure()
        for n in range(40):
            self.hit(seq=n*2)
            self.e1.handle(self.c1,self.receipt(seq=n*2+1));self.e2.take_pending(self.c2)
        self.assertEqual(len(self.e1.room.pending_death_receipts[1001]),32)
        self.assertEqual(self.e1.room.pending_death_receipts[1001][0][2],17)
        room=self.e1.room
        self.h.leave(self.e2)
        self.assertEqual(room.pending_death_receipts[1001],[])

    def test_two_identical_real_hits_are_not_content_deduplicated(self):
        self.configure();self.hit(0);self.hit(1)
        for seq in (2,3):
            m=self.receipt(seq)
            self.e1.handle(self.c1,m)
            self.assertEqual(self.e2.take_pending(self.c2),[m])
        self.e1.handle(self.c1,self.receipt(4))
        self.assertEqual(self.e2.take_pending(self.c2),[])

    def test_sender_reporter_binding_is_independent_of_source(self):
        self.configure();self.hit()
        p=bytearray(self.receipt().payload);struct.pack_into('<Q',p,39,1002)
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,Message(8071,bytes(p)))
        self.assertEqual(len(self.e1.room.pending_hit_receipts[1001]),1)

    def test_pending_queue_bounded_without_wall_clock_guess(self):
        self.configure()
        for seq in range(40):self.hit(seq)
        pending=self.e1.room.pending_hit_receipts[1001]
        self.assertEqual(len(pending),32)
        self.assertEqual(pending[0][3],8)

    def test_new_round_clears_pending_receipts(self):
        self.configure();self.hit()
        self.e1.handle(self.c1,self.receipt());self.e2.take_pending(self.c2)
        self.hit(seq=2)
        room=self.e1.room;room.stage='room'
        from server.kk_local.engine import Phase
        for member in room.members.values():member.ready=True;member.engine.game.phase=Phase.ROOM
        self.e1.handle(self.c1,Message(4030))
        self.assertEqual(room.pending_hit_receipts,{})
        self.assertEqual(room.pending_death_receipts,{})


if __name__=='__main__':unittest.main()
