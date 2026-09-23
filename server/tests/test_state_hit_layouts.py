"""Native wire-field tests only: never grant authority to mutate a player."""
import math
import struct
import unittest
from server.kk_local.layouts import decode_battle
from server.kk_local.wire import ProtocolError


def packet(ident,size):
    raw=bytearray(size)
    struct.pack_into('<IQ',raw,0,ident,1001)
    raw[12:14]=b'\x01\x01'
    struct.pack_into('<II',raw,15,17,29)
    struct.pack_into('<I',raw,23,2500)
    struct.pack_into('<QQ',raw,39,1002,1001)
    return raw


class StateHitLayoutTests(unittest.TestCase):
    def test_hit_receipt_preserves_three_body_identities_and_raw_result(self):
        raw=packet(8126,71)
        struct.pack_into('<QQQII',raw,39,1001,1002,1003,811115,0xffffffff)
        out=decode_battle(raw)
        self.assertEqual((out['sender'],out['reporter'],out['target'],out['source']),
                         (1001,1001,1002,1003))
        self.assertEqual((out['skill_property_id'],out['hit_result_status_raw']),
                         (811115,0xffffffff))
        self.assertNotIn('room_pair',out)
        self.assertNotIn('authorized',out)

    def test_hit_receipt_length_is_local_exact_policy(self):
        raw=packet(8126,71)
        for other in (raw[:-1],raw+b'\0'):
            with self.assertRaises(ProtocolError):decode_battle(other)
        struct.pack_into('<Q',raw,55,0)
        self.assertEqual(decode_battle(raw)['source'],0)

    def test_state_target_is_not_sender_and_parameter_is_signed(self):
        raw=packet(8150,87)
        struct.pack_into('<IiI',raw,55,250,-3,0xffffffff)
        raw[67:75]=bytes(range(8));struct.pack_into('<III',raw,75,1,2,9)
        out=decode_battle(raw)
        self.assertEqual((out['sender'],out['source'],out['target']),(1001,1001,1002))
        self.assertEqual(out['target'],out['player'])  # compatibility alias
        self.assertEqual(out['parameter_signed'],-3)
        self.assertEqual(out['code_parameter_event'][1],0xfffffffd)
        self.assertEqual(out['event_raw'],0xffffffff)
        self.assertEqual((out['operation'],out['room_pair']),('apply',(2,9)))
        raw[67]=99;self.assertEqual(out['opaque_67_75'],bytes(range(8)))

    def test_cancel_zero_source_and_unknown_action_preserved(self):
        raw=packet(8150,87);struct.pack_into('<Q',raw,47,0)
        out=decode_battle(raw)
        self.assertEqual((out['operation'],out['source']),('cancel',0))
        struct.pack_into('<I',raw,75,6)
        self.assertEqual(decode_battle(raw)['operation'],'unknown')

    def test_hit_hp_mp_and_callback_fields(self):
        raw=packet(8121,94)
        raw[55]=4;struct.pack_into('<I',raw,56,811115)
        raw[60:65]=b'ABCDE';raw[65]=1;raw[66]=7
        struct.pack_into('<fBfffBBII',raw,67,12.5,1,2.0,3.0,0.5,1,8,2,9)
        out=decode_battle(raw)
        self.assertEqual((out['target'],out['source']),(1002,1001))
        self.assertEqual(out['signed_hp_amount'],12.5)
        self.assertEqual((out['target_mp_argument'],out['source_mp_argument'],out['scalar_80']),(2.0,3.0,0.5))
        self.assertEqual((out['metadata_55'],out['skill_property_id'],out['opaque_60_65']),(4,811115,b'ABCDE'))
        self.assertEqual((out['attack_callback_flag'],out['damage_argument_66'],out['contribution_flag'],out['callback_mode'],out['hit_result_status']),(1,7,1,1,8))
        self.assertEqual(out['room_pair'],(2,9))

    def test_healing_and_negative_zero_bits_are_not_normalized(self):
        raw=packet(8121,94);struct.pack_into('<Q',raw,47,0)
        for value in (-25.0,-0.0,0.0):
            struct.pack_into('<f',raw,67,value)
            out=decode_battle(raw)
            self.assertEqual(out['signed_hp_amount'],value)
            self.assertEqual(out['signed_hp_amount_bits'],struct.unpack('<I',struct.pack('<f',value))[0])
            self.assertEqual(out['source'],0)

    def test_nonfinite_all_consumed_scalars_rejected(self):
        for offset in (67,72,76,80):
            for value in (math.nan,math.inf,-math.inf):
                raw=packet(8121,94);struct.pack_into('<f',raw,offset,value)
                with self.assertRaises(ProtocolError):decode_battle(raw)

    def test_known_shapes_are_strict_and_8124_stays_unhandled(self):
        for ident,size in ((8121,94),(8150,87)):
            raw=packet(ident,size)
            with self.assertRaises(ProtocolError):decode_battle(raw[:-1])
            with self.assertRaises(ProtocolError):decode_battle(raw+b'\0')
        self.assertIsNone(decode_battle(packet(8124,63)))
        # Discovery of a new large message must still reach the unknown-ID
        # path, not become a new connection-closing length rejection.
        self.assertIsNone(decode_battle(packet(99999,512)))

    def test_header_offsets_not_collapsed_to_one_sequence(self):
        raw=packet(8150,87);out=decode_battle(raw)
        self.assertEqual((out['sequence_15_raw'],out['sequence_19_raw']),(17,29))
        raw[13]=0;out=decode_battle(raw)
        self.assertEqual(out['header_variant'],0)
        self.assertNotIn('authorized',out);self.assertNotIn('duplicate',out)

    def test_unknown_header_and_action_do_not_grant_execution(self):
        raw=packet(8150,87);raw[13]=255;struct.pack_into('<I',raw,75,0xffffffff)
        out=decode_battle(memoryview(raw));self.assertEqual(out['operation'],'unknown')
        self.assertEqual(out['header_variant'],255)
        self.assertNotIn('authorized',out)


if __name__=='__main__':unittest.main()
