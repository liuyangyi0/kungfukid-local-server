import unittest
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.store import Store
from server.kk_local.wire import Message,ProtocolError
from server.kk_local.menu_layouts import decode_menu_response

class ProfileQueryTests(unittest.TestCase):
    def test_exact_original_bits_active_account_and_no_mutation(self):
        store=Store(':memory:');store.seed_local()
        try:
            p=bytearray(store.snapshot(1001)[2]);p[352:356]=b'\xff\x80\x01\xfe'
            with store.db:store.db.execute('UPDATE accounts SET profile=? WHERE uid=1001',(bytes(p),))
            before=store.snapshot(1001)
            engine=Engine(store);c=Connection(1,Phase.LOBBY,1001);engine.game=c
            for phase in (Phase.LOBBY,Phase.ROOM):
                c.phase=phase
                self.assertEqual(engine.handle(c,Message(20546)),[Message(20547,bytes(p[352:356]))])
            self.assertEqual(decode_menu_response(20547,bytes(p[352:356]))['profile_offset'],352)
            with self.assertRaises(ProtocolError):engine.handle(c,Message(20546,b'x'))
            self.assertEqual(engine.handle(Connection(2,Phase.LOBBY,1001),Message(20546)),[])
            c.phase=Phase.BATTLE
            self.assertEqual(engine.handle(c,Message(20546)),[])
            self.assertEqual(store.snapshot(1001),before)
            with self.assertRaises(ValueError):store.profile_word(1001,0)
        finally:store.close()
