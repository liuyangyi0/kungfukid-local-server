"""Native8286 terminal-ready and8278 countdown UI have different recipients."""
import struct
import unittest
from server.kk_local.wire import Message,ProtocolError
from server.tests import test_shared_rooms as fixtures


def notice(ident,*,sender=1001,target=1001,initiator=None,seq=0,seconds=5,marker=1):
    p=bytearray(55);struct.pack_into('<IQ',p,0,ident,sender)
    p[12:14]=b'\x01\x01';struct.pack_into('<I',p,19,seq)
    if ident==8286:struct.pack_into('<QQ',p,39,sender if initiator is None else initiator,target)
    else:struct.pack_into('<QII',p,39,target,marker,seconds)
    return Message(8071,bytes(p))


class DeathNoticeTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join
    battle=fixtures.SharedRoomTests.battle

    def test_self_terminal_and_host_terminal_preserve_bytes_without_echo(self):
        self.battle();before=[self.s.snapshot(u) for u in (1001,1002)]
        own=notice(8286,sender=1002,target=1002)
        self.assertEqual(self.e2.handle(self.c2,own),[])
        self.assertEqual(self.e1.take_pending(self.c1),[own])
        host=notice(8286,target=1002)
        self.assertEqual(self.e1.handle(self.c1,host),[])
        self.assertEqual(self.e2.take_pending(self.c2),[host])
        self.e1.handle(self.c1,host)
        self.assertEqual(self.e2.take_pending(self.c2),[])
        self.assertEqual([self.s.snapshot(u) for u in (1001,1002)],before)
        self.assertFalse(self.e1.room.result_reports)

    def test_remote_countdown_uses_elected_host_not_fixed_uid(self):
        self.battle();self.e1.room.owner=1002
        msg=notice(8278,sender=1002,target=1001,seconds=8)
        self.assertEqual(self.e2.handle(self.c2,msg),[])
        self.assertEqual(self.e1.take_pending(self.c1),[msg])
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,notice(8278,target=1002))

    def test_nonowner_target_forgery_and_unknown_actor_rejected(self):
        self.battle()
        bad=(notice(8286,sender=1002,target=1001),
             notice(8286,sender=1002,target=1002,initiator=1001),
             notice(8286,sender=1001,target=1002),
             notice(8286,sender=1002,target=1002+(1<<32)))
        for msg in bad:
            with self.assertRaises(ProtocolError):self.e2.handle(self.c2,msg)
        self.assertEqual(self.e1.take_pending(self.c1),[])
        self.assertEqual(self.e1.room.last_sequence,{})

    def test_unknown_countdown_fields_do_not_consume_sequence(self):
        self.battle()
        for msg in (notice(8278,target=1002,marker=0),notice(8278,target=1002,seconds=0xffffffff)):
            self.e1.handle(self.c1,msg)
        self.assertEqual(self.e1.room.last_sequence,{})
        self.assertEqual(self.e2.take_pending(self.c2),[])
        valid=notice(8278,target=1002)
        self.e1.handle(self.c1,valid)
        self.assertEqual(self.e2.take_pending(self.c2),[valid])

    def test_battle_only_generic_lane_and_exact_lengths(self):
        self.battle();room=self.e1.room
        p=bytearray(notice(8286).payload);p[12:14]=bytes(2)
        self.e1.handle(self.c1,Message(8071,bytes(p)))
        self.assertEqual(room.last_sequence,{})
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,Message(8071,bytes(p[:-1])))
        room.stage='result'
        self.assertEqual(self.e1.handle(self.c1,notice(8286)),[])
        self.assertEqual(self.e2.take_pending(self.c2),[])


if __name__=='__main__':unittest.main()
