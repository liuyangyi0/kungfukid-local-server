import struct
import unittest
from server.kk_local.rankings import profile_score,local_rankings
from server.kk_local.menu_layouts import decode_menu_response
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.store import Store
from server.kk_local.wire import Message,ProtocolError

def profile(score):
    p=bytearray(360);struct.pack_into('<i',p,249,score);return bytes(p)

class RankingsTests(unittest.TestCase):
    def test_top100_ties_and_actor_outside_page(self):
        rows=((uid,'P'+str(uid),profile(uid)) for uid in range(1,121))
        directory,own=local_rankings(rows,0,1,profile(1))
        decoded=decode_menu_response(2550,directory.payload)['records']
        self.assertEqual(len(decoded),100)
        self.assertEqual((decoded[0]['nickname'],decoded[-1]['nickname']),('P120','P21'))
        self.assertEqual(decode_menu_response(2570,own.payload)['rank_zero_based'],119)
        directory,own=local_rankings([(2,'B',profile(5)),(1,'A',profile(5))],0,2,profile(5))
        self.assertEqual(decode_menu_response(2550,directory.payload)['records'][0]['nickname'],'A')
        self.assertEqual(own.payload,struct.pack('<Bi',0,1))

    def test_native_sum_wrap_and_invalid_categories(self):
        p=bytearray(360)
        for offset in (137,145,153,161):struct.pack_into('<i',p,offset,0x7fffffff)
        self.assertEqual(profile_score(p,1),-4)
        for category in (13,-1,True):
            with self.assertRaises(ProtocolError):profile_score(p,category)

    def test_active_lobby_queries_read_only_and_ignore_unresolved_identity(self):
        s=Store(':memory:');s.seed_local()
        try:
            e=Engine(s);c=Connection(1,Phase.LOBBY,1001);e.game=c
            before=s.snapshot(1001)
            self.assertEqual(e.handle(c,Message(2540,b'\0'))[0].id,2550)
            own=e.handle(c,Message(2560,b'\0'+b'\xff'*8))[0]
            self.assertEqual(own,Message(2570,bytes(5)))
            self.assertEqual(s.snapshot(1001),before)
            self.assertEqual(e.handle(Connection(2,Phase.LOBBY,1001),Message(2540,b'\0')),[])
            self.assertEqual(e.handle(c,Message(2540,b'\xff')),[])
            with self.assertRaises(ProtocolError):e.handle(c,Message(2540,b''))
            c.phase=Phase.BATTLE
            self.assertEqual(e.handle(c,Message(2540,b'\0')),[])
        finally:s.close()
