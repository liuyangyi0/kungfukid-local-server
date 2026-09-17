import json
import struct
import unittest
from server.kk_local.engine import Connection,Phase
from server.kk_local.service import Service
from server.kk_local.store import Store
from server.kk_local.wire import Message


class QueryProbeTests(unittest.TestCase):
    def test_opt_in_phase_body_boundaries_and_repeat_visibility(self):
        store=Store(':memory:');store.seed_local();events=[]
        try:
            s=Service(store,lambda:True,offline_adapter=True,event_sink=events.append)
            c=Connection(1,phase=Phase.LOBBY,uid=1001)
            s.record_menu_query(c,Message(1400));self.assertEqual(events,[])
            s.query_probe=True
            for _ in range(3):s.record_menu_query(c,Message(1400))
            self.assertEqual([r['seq'] for r in events],[1,2,3])
            s.record_menu_query(c,Message(5143,struct.pack('<I',123)))
            self.assertEqual(events[-1]['fields'],{'item_id':123})
            s.record_menu_query(c,Message(9070,b'\x19\x03'))
            self.assertEqual(events[-1]['fields'],{'category_code':25,'variant_code':3})
            s.record_menu_query(c,Message(20360,struct.pack('<QI',1001,7)))
            self.assertEqual(events[-1]['fields'],{'self_query':True,'selector_u32':7})
            s.record_menu_query(c,Message(9999,b'private-text-not-a-query'))
            self.assertEqual(events[-1]['body_policy'],'metadata_only')
            self.assertNotIn('private-text',json.dumps(events))
            count=len(events)
            for phase in (Phase.CONNECTED,Phase.BOOTSTRAP,Phase.BATTLE):
                c.phase=phase;s.record_menu_query(c,Message(1010,b'opaque-auth'))
            self.assertEqual(len(events),count)
            c.phase=Phase.LOBBY;s.query_probe_count=2048
            for _ in range(3):s.record_menu_query(c,Message(1400))
            self.assertEqual(sum(r['event']=='query_probe_limit' for r in events),1)
        finally:store.close()
