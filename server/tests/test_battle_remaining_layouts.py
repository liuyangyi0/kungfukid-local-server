"""Remaining cached82D0F0 shapes. Decoding never grants mode/object authority.

Local evidence: native-battle-consumer-corpus-20260919 and static-coverage-v5;
82C8E0/82CDC0/828DF0/829C80/8279E0/827FC0/827990. No original payload fixtures.
"""
import struct
import unittest
from server.kk_local.layouts import BATTLE_LENGTHS,decode_battle
from server.kk_local.pve import LENGTHS,decode as decode_pve
from server.kk_local.public_commands import BASIC_BATTLE
from server.kk_local.wire import ProtocolError

def body(ident,size):
    p=bytearray(size);struct.pack_into('<IQ',p,0,ident,1001);p[12:14]=b'\1\1';return p

class RemainingBattleLayouts(unittest.TestCase):
    def test_known_dispatch_census_has_one_explicit_ambiguous_shape(self):
        known={8120,8121,8122,8125,8126,8127,8140,8142,8143,8144,8150,8155,8156,8157,
               8270,8276,8278,8280,8282,8284,8286,8287,8288,8289,8290,8291,8292,8293,8294,8295,8296,8297,
               8394,8395,8396,8397,8400,8401,8402,8403,8404,8421,8440,8441,8450,8451,8452,
               9000,9001,9002,9500,9501,9502,20400,20401,20402,20403,20404,20405,20406,20407,20408}
        self.assertEqual(len(known),62)
        self.assertEqual(known-(set(BATTLE_LENGTHS)|set(LENGTHS)|{8289,8291,8292}),{8156})
        self.assertIsNone(decode_battle(body(8156,87)))
    def test_kof_header_and_scalar_do_not_become_live_team_aliases(self):
        self.assertEqual(decode_battle(body(8395,39))['mode_family'],'kof')
        p=body(8396,43);struct.pack_into('<I',p,39,0xffffffff)
        row=decode_battle(p);self.assertEqual(row['value_39_raw'],0xffffffff);self.assertNotIn('player',row)
    def test_effect_resource_is_bounded_matrix_finite_context_opaque(self):
        p=body(8421,139);p[39:43]=b'fx\0x';struct.pack_into('<I',p,71,0xfefefefe)
        row=decode_battle(p);self.assertEqual(row['resource_name_raw'],b'fx');self.assertEqual(row['update_context_raw'],0xfefefefe)
        self.assertEqual(row['resource_padding_raw'][0:1],b'x')
        struct.pack_into('<f',p,75,float('nan'))
        with self.assertRaises(ProtocolError):decode_battle(p)
        p=body(8421,139);p[39:71]=b'x'*32
        with self.assertRaises(ProtocolError):decode_battle(p)
    def test_weapon_creation_keeps_unknown_words_not_inventory(self):
        p=body(8452,63);struct.pack_into('<6I',p,39,1,2,3,4,5,6);row=decode_battle(p)
        self.assertEqual(row['owner_words_raw'],(1,2));self.assertEqual(row['parameter_47_raw'],3)
        self.assertEqual(row['creation_words_raw'],(4,5,6))
    def test_remaining_pve_slots_keep_raw_arguments_and_noop_opaque(self):
        p=body(20402,63);struct.pack_into('<6I',p,39,1,2,3,4,5,6);row=decode_pve(p)
        self.assertEqual(row['actor_words_raw'],(1,2));self.assertEqual(row['payload_51_63'],struct.pack('<III',4,5,6))
        p=body(20406,47);struct.pack_into('<II',p,39,7,8);row=decode_pve(p)
        self.assertEqual(row['sender'],1001);self.assertEqual(row['mode_arguments_raw'],(7,8))
        self.assertEqual(decode_pve(body(20408,63))['opaque_body'],bytes(24))
    def test_all_new_shapes_exact_and_still_not_exposed(self):
        for ident,size in ((8395,39),(8396,43),(8421,139),(8452,63),(20402,63),(20406,47),(20408,63)):
            decode=decode_pve if ident>=20400 else decode_battle
            decode(body(ident,size));self.assertNotIn(ident,BASIC_BATTLE)
            for p in (body(ident,size)[:-1],body(ident,size)+b'\0'):
                with self.assertRaises(ProtocolError):decode(p)
