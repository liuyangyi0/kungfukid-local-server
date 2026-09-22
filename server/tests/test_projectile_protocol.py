"""Bounded TCP projectile lifecycle; no inferred player-hit authority."""
import math
import struct
import unittest
from server.kk_local.layouts import decode_battle
from server.kk_local.wire import Message, ProtocolError
from server.tests import test_shared_rooms as fixtures


SIZES={8400:131,8401:123,8402:123,8403:119,8404:51}


def projectile(ident,room,*,sender=1001,source=1001,key=10000,seq=0):
    p=bytearray(SIZES[ident]);struct.pack_into('<IQ',p,0,ident,sender)
    p[12:14]=b'\x01\x01';struct.pack_into('<I',p,19,seq)
    if ident==8400:
        struct.pack_into('<QII',p,39,source,key,12345)
        offset=55;pair=123;struct.pack_into('<I',p,119,2)
    else:
        struct.pack_into('<I',p,39,key)
        offset=51 if ident==8402 else 47
        pair=43 if ident==8404 else 111 if ident==8403 else 115
        if ident==8402:struct.pack_into('<Q',p,43,1002)
        elif ident!=8404:struct.pack_into('<I',p,43,2)
    if ident!=8404:
        for i,vector in enumerate(((1,0,0),(0,1,0),(0,0,1),(10,20,30))):
            struct.pack_into('<fffI',p,offset+16*i,*vector,0xffffffff)
    struct.pack_into('<II',p,pair,room.number,room.serial)
    return Message(8071,bytes(p))


class ProjectileProtocolTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join
    battle=fixtures.SharedRoomTests.battle

    def send(self,m,second=False):
        e,c,other,oc=(self.e2,self.c2,self.e1,self.c1) if second else (self.e1,self.c1,self.e2,self.c2)
        self.assertEqual(e.handle(c,m),[])
        return other.take_pending(oc)

    def test_create_update_remove_forward_in_order_and_keep_tombstone(self):
        self.battle();room=self.e1.room
        for seq,ident in enumerate((8400,8403,8401,8404)):
            m=projectile(ident,room,seq=seq)
            self.assertEqual(self.send(m),[m])
            self.assertEqual(self.send(m),[])
        self.assertFalse(room.projectiles[10000]['alive'])
        self.assertEqual(self.send(projectile(8400,room,seq=4)),[])
        self.assertEqual(self.send(projectile(8403,room,seq=5)),[])

    def test_other_player_cannot_reuse_or_delete_owned_key(self):
        self.battle();room=self.e1.room
        self.send(projectile(8400,room))
        for ident in (8400,8401,8403,8404):
            with self.assertRaises(ProtocolError):
                self.send(projectile(ident,room,sender=1002,source=1002),second=True)
        self.assertTrue(room.projectiles[10000]['alive'])
        self.assertNotIn((1002,19),room.last_sequence)

    def test_unobserved_object_update_does_not_claim_key_or_counter(self):
        self.battle();room=self.e1.room
        for ident in (8401,8403,8404):
            self.assertEqual(self.send(projectile(ident,room,seq=99)),[])
        self.assertEqual(room.projectiles,{})
        self.assertEqual(room.last_sequence,{})

    def test_host_environment_and_owned_second_player_sources(self):
        self.battle();room=self.e1.room
        m=projectile(8400,room,source=0)
        self.assertEqual(self.send(m),[m])
        m=projectile(8400,room,sender=1002,source=1002,key=20000)
        self.assertEqual(self.send(m,second=True),[m])
        with self.assertRaises(ProtocolError):
            self.send(projectile(8400,room,sender=1002,source=0,key=20001,seq=1),second=True)
        with self.assertRaises(ProtocolError):
            self.send(projectile(8400,room,source=9999,key=30000,seq=1))

    def test_hit_player_variant_decodes_without_granting_impact(self):
        self.battle();room=self.e1.room
        self.send(projectile(8400,room))
        m=projectile(8402,room,seq=1)
        out=decode_battle(m.payload)
        self.assertEqual((out['child_key'],out['target'],out['room_pair']),(10000,1002,(room.number,room.serial)))
        self.assertEqual(self.send(m),[])

    def test_target_witness_hit_and_one_followup_result_reach_creator(self):
        self.battle();room=self.e1.room
        self.send(projectile(8400,room))
        hit=projectile(8402,room,sender=1002,seq=0)
        self.assertEqual(self.send(hit,second=True),[hit])
        self.assertEqual(self.send(hit,second=True),[])
        result=projectile(8401,room,sender=1002,seq=1)
        self.assertEqual(self.send(result,second=True),[result])
        self.assertEqual(self.send(result,second=True),[])
        with self.assertRaises(ProtocolError):
            self.send(projectile(8401,room,sender=1002,seq=2),second=True)

    def test_hit_witness_does_not_grant_wall_redirect_or_delete(self):
        self.battle();room=self.e1.room;self.send(projectile(8400,room))
        self.send(projectile(8402,room,sender=1002),second=True)
        wall=bytearray(projectile(8401,room,sender=1002,seq=1).payload)
        struct.pack_into('<I',wall,43,3)
        with self.assertRaises(ProtocolError):self.send(Message(8071,bytes(wall)),second=True)
        for ident in (8403,8404):
            with self.assertRaises(ProtocolError):self.send(projectile(ident,room,sender=1002,seq=1),second=True)
        self.assertTrue(room.projectiles[10000]['alive'])

    def test_unknown_result_not_forwarded_or_committed(self):
        self.battle();room=self.e1.room;self.send(projectile(8400,room))
        p=bytearray(projectile(8401,room,seq=99).payload);struct.pack_into('<I',p,43,0xffffffff)
        self.assertEqual(self.send(Message(8071,bytes(p))),[])
        self.assertEqual(room.last_sequence[(1001,19)],0)

    def test_transform_flag_words_not_treated_as_nan(self):
        self.battle();room=self.e1.room
        for ident in (8400,8401,8402,8403):
            m=projectile(ident,room);out=decode_battle(m.payload)
            self.assertEqual(out['transform_words'],(0xffffffff,)*4)
            self.assertEqual(out['transform_vectors'][3],(10,20,30))
            offset=55 if ident==8400 else 51 if ident==8402 else 47
            for row in range(4):
                p=bytearray(m.payload);struct.pack_into('<f',p,offset+row*16,math.nan)
                with self.assertRaises(ProtocolError):decode_battle(p)

    def test_wrong_battle_bad_header_and_lengths_do_not_register(self):
        self.battle();room=self.e1.room
        m=projectile(8400,room)
        for delta in (-1,1):
            raw=m.payload[:delta] if delta<0 else m.payload+b'\0'
            with self.assertRaises(ProtocolError):self.send(Message(8071,raw))
        p=bytearray(m.payload);struct.pack_into('<I',p,127,room.serial+1)
        with self.assertRaises(ProtocolError):self.send(Message(8071,bytes(p)))
        p=bytearray(m.payload);p[13]=0
        self.assertEqual(self.send(Message(8071,bytes(p))),[])
        self.assertEqual(room.projectiles,{})

    def test_new_battle_clears_object_registry(self):
        self.battle();room=self.e1.room
        self.send(projectile(8400,room))
        from server.kk_local.engine import Phase
        room.stage='room'
        for member in room.members.values():member.ready=True;member.engine.game.phase=Phase.ROOM
        self.e1.handle(self.c1,Message(4030))
        self.assertEqual(room.projectiles,{})


if __name__=='__main__':unittest.main()
