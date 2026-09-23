import struct
import unittest
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.store import Store
from server.kk_local.wire import Message,ProtocolError
from server.kk_local.menu_layouts import decode_menu_response

class ShopCacheTests(unittest.TestCase):
    def test_imported_only_deduplicated_lossless_and_no_account_change(self):
        s=Store(':memory:');s.seed_local()
        try:
            e=Engine(s);c=Connection(1,Phase.LOBBY,1001);e.game=c
            before=s.snapshot(1001)
            self.assertEqual(e.handle(c,Message(1540)),[Message(1550)])
            raw=bytearray(range(108));struct.pack_into('<II',raw,5,253033,9001)
            s.replace_shop_catalog(1,0,[bytes(raw)]);s.replace_shop_catalog(2,0,[bytes(raw)])
            reply=e.handle(c,Message(1540))[0]
            self.assertEqual(reply.payload,bytes(raw))
            self.assertEqual(decode_menu_response(1550,reply.payload)['records'][0].raw,bytes(raw))
            self.assertEqual(s.snapshot(1001),before)
            with self.assertRaises(ProtocolError):e.handle(c,Message(1540,b'x'))
            raw[-1]^=1;s.replace_shop_catalog(2,0,[bytes(raw)])
            self.assertEqual(e.handle(c,Message(1540))[0].id,20150)
            self.assertEqual(s.snapshot(1001),before)
            self.assertEqual(e.handle(Connection(2,Phase.LOBBY,1001),Message(1540)),[])
            c.phase=Phase.BATTLE
            self.assertEqual(e.handle(c,Message(1540)),[])
        finally:s.close()
