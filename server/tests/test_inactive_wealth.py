import struct
import unittest
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.store import Store
from server.kk_local import packets
from server.kk_local.menu_layouts import decode_menu_response
from server.kk_local.wire import Message,ProtocolError

class InactiveWealthTests(unittest.TestCase):
    def test_native_no_entry_and_no_reward_branches(self):
        for page in (0,10):
            m=packets.inactive_wealth_page(page)
            parsed=decode_menu_response(m.id,m.payload)
            self.assertEqual(parsed['page'],page)
            self.assertIn(b'not configured',m.payload[4:68]);self.assertEqual(m.payload[67],0)
            self.assertTrue(all(row['presence_key']==0 for row in parsed['entries']))
        own=decode_menu_response(20564,packets.inactive_wealth_self().payload)
        self.assertEqual(own['rank_zero_based'],-1);self.assertEqual(own['consume'],0)
        self.assertEqual(own['rewards'],((0,0,0,0),)*3)
        for page in (-1,11,True):
            with self.assertRaises(ProtocolError):packets.inactive_wealth_page(page)

    def test_lobby_queries_never_grant_or_mutate(self):
        s=Store(':memory:');s.seed_local()
        try:
            e=Engine(s);c=Connection(1,Phase.LOBBY,1001);e.game=c;before=s.snapshot(1001)
            for request in (Message(20561,struct.pack('<I',0)),Message(20563),Message(20565)):
                self.assertEqual(e.handle(c,request)[0].id,request.id+1)
                self.assertEqual(s.snapshot(1001),before)
                self.assertEqual(e.handle(Connection(2,Phase.LOBBY,1001),request),[])
            with self.assertRaises(ProtocolError):e.handle(c,Message(20563,b'x'))
            c.phase=Phase.BATTLE
            self.assertEqual(e.handle(c,Message(20565)),[])
            self.assertEqual(s.gold_balance(1001),0)
        finally:s.close()
