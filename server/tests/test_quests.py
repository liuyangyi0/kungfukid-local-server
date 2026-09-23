import json
from pathlib import Path
import sqlite3
import struct
import tempfile
import unittest
from server.kk_local import quests
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.maps import MapCatalog
from server.kk_local.menu_layouts import decode_menu_request,decode_menu_response
from server.kk_local.store import Store
from server.kk_local.wire import Message,ProtocolError


def source(family,key):
    fields=['0']*(41 if family=='ordinary' else 27);fields[0]=str(key)
    if family=='ordinary':fields[1:5]=['name','icon','description','done'];fields[40]='1'
    else:
        fields[22:26]=['name','description','done','icon']
        fields[3]='17';fields[4]='5'
    return ('\t'.join(fields)+'\r\n').encode('gb18030')


def templates():
    result={}
    for family,key in zip(quests.FAMILIES,(1001,2001,3001)):
        result[(family,key)]=quests.parse_templates(family,source(family,key))[key]
    return result


def action(ident,key):
    if ident in (6050,6080):return Message(ident,b'JUNKUID!'+struct.pack('<IH',0xdeadbeef,key))
    state={6051:2,6052:2,6081:1,6082:1,6311:3,6312:3}[ident]
    return Message(ident,struct.pack('<HBI',key,state,0xdeadbeef)+b'UNSET-STACK!')


class QuestTests(unittest.TestCase):
    def setUp(self):
        self.s=Store(':memory:');self.s.seed_local();self.s.provision_local(1002,'Second')
        self.maps=MapCatalog({},{});self.maps.quest_templates=templates()
        self.e=Engine(self.s,map_catalog=self.maps);self.c=Connection(2,Phase.LOBBY,1001);self.e.game=self.c
        self.rules=[dict(family=f,key=k) for f,k in self.maps.quest_templates]
        quests.configure(self.s,self.rules,self.maps.quest_templates)

    def tearDown(self):self.s.close()
    def query(self):return self.e.handle(self.c,Message(6000,bytes(4)))
    def send(self,ident,key):return self.e.handle(self.c,action(ident,key))

    def test_lists_use_source_ids_no_account_mutation(self):
        before=self.s.snapshot(1001);out=self.query()
        self.assertEqual([m.id for m in out],[6020,6041,6042])
        self.assertEqual([len(m.payload) for m in out],[123,141,141])
        for m,key in zip(out,(1001,2001,3001)):
            self.assertEqual(decode_menu_response(m.id,m.payload)['records'][0]['quest_id'],key)
        self.assertEqual(out[0].payload[6],1);self.assertEqual(out[1].payload[16],1)
        self.assertEqual(struct.unpack_from('<III',out[1].payload,53),(17,0,0))
        self.assertEqual(before,self.s.snapshot(1001))
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM quest_progress').fetchone()[0],0)

    def test_empty_ordinary_sentinel_and_native_lower_bound(self):
        quests.configure(self.s,self.rules[1:],self.maps.quest_templates)
        out=self.query();self.assertEqual(out[0],Message(6020,bytes(7)))
        self.assertEqual(decode_menu_response(6020,bytes(7)),{'records':()})
        for raw in (b'',bytes(6),bytes(8),b'x'*7,bytes(124)):
            with self.assertRaises(ProtocolError):decode_menu_response(6020,raw)

    def test_accept_baseline_preserved_on_retry_and_cancel(self):
        self.query()
        p=bytearray(self.s.snapshot(1001)[2]);p[129:245]=bytes(range(116))
        with self.s.db:self.s.db.execute('UPDATE accounts SET profile=? WHERE uid=1001',(bytes(p),))
        out=self.send(6050,1001);self.assertEqual([m.id for m in out],[6060])
        self.assertEqual(out[0].payload,struct.pack('<Q',1001)+p[:4]+struct.pack('<H',1001))
        p[129:245]=b'Z'*116
        with self.s.db:self.s.db.execute('UPDATE accounts SET profile=? WHERE uid=1001',(bytes(p),))
        self.assertNotIn(6060,[m.id for m in self.send(6050,1001)])
        row=self.query()[0].payload;self.assertEqual(row[6],2);self.assertEqual(row[7:],bytes(range(116)))
        self.assertEqual(self.send(6080,1001)[0].id,6090)
        self.assertEqual(self.query()[0].payload[7:],bytes(range(116)))
        self.send(6050,1001);self.assertEqual(self.query()[0].payload[7:],b'Z'*116)

    def test_extended_accept_cancel_and_denied_claim_never_rewards(self):
        self.query();before=self.s.snapshot(1001)
        for key,accept,cancel,claim in ((2001,6051,6081,6311),(3001,6052,6082,6312)):
            expected=Message(accept+10,struct.pack('<HB',key,2))
            self.assertEqual(self.send(accept,key),[expected]);self.assertEqual(self.send(accept,key),[expected])
            self.assertNotIn(claim-10,[m.id for m in self.send(claim,key)])
            self.assertTrue(any(k[1]==claim for k in self.e.unknown))
            self.assertEqual(self.send(cancel,key),[Message(cancel+10,struct.pack('<HB',key,1))])
        self.assertEqual(before,self.s.snapshot(1001));self.assertEqual(self.s.gold_balance(1001),0)

    def test_login_connection_phase_and_offer_guard(self):
        self.assertNotIn(6060,[m.id for m in self.send(6050,1001)])
        self.query()
        for c in (Connection(8,Phase.LOBBY,1001),Connection(9,Phase.LOBBY,1002)):
            self.assertEqual(self.e.handle(c,action(6050,1001)),[])
        self.c.phase=Phase.BATTLE;self.assertEqual(self.send(6050,1001),[])
        self.c.phase=Phase.LOBBY;self.e.disconnect(self.c);self.assertIsNone(self.e.quest_offer)

    def test_second_account_progress_is_independent_of_uninitialized_prefix(self):
        self.query();self.send(6050,1001)
        other=Engine(self.s,account_uid=1002,map_catalog=self.maps);c=Connection(8,Phase.LOBBY,1002);other.game=c
        out=other.handle(c,Message(6000,bytes(4)));self.assertEqual(out[0].payload[6],1)
        other.handle(c,action(6050,1001))
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM quest_progress').fetchone()[0],2)

    def test_state_payload_lengths_and_tail_not_nonce(self):
        for ident,key in ((6050,1001),(6080,1001),(6051,2001),(6081,2001),(6312,3001)):
            m=action(ident,key);fields=decode_menu_request(ident,m.payload)
            self.assertEqual(fields['quest_id'],key)
            with self.assertRaises(ProtocolError):decode_menu_request(ident,m.payload[:-1])
            with self.assertRaises(ProtocolError):decode_menu_request(ident,m.payload+b'\0')
        m=action(6051,2001);raw=bytearray(m.payload);raw[2]=3
        with self.assertRaises(ProtocolError):self.e.handle(self.c,Message(6051,bytes(raw)))

    def test_configuration_change_cannot_replace_accepted_meaning(self):
        self.query();self.send(6050,1001)
        changed=dict(self.maps.quest_templates)
        changed[('ordinary',1001)]=quests.parse_templates('ordinary',source('ordinary',1001).replace(b'name',b'new-name'))[1001]
        with self.assertRaises(ValueError):quests.configure(self.s,self.rules,changed)
        self.maps.quest_templates=changed
        self.assertNotIn(6020,[m.id for m in self.query()])
        self.assertNotIn(6090,[m.id for m in self.send(6080,1001)])

    def test_disable_reenable_does_not_reset_accepted_state(self):
        self.query();self.send(6050,1001)
        quests.configure(self.s,[],self.maps.quest_templates);self.assertEqual(self.query(),[])
        self.assertNotIn(6090,[m.id for m in self.send(6080,1001)])
        quests.configure(self.s,self.rules,self.maps.quest_templates);self.assertEqual(self.query()[0].payload[6],2)

    def test_transaction_failure_rolls_back_accept(self):
        self.query();self.s.db.execute("CREATE TRIGGER deny_quest BEFORE INSERT ON quest_progress BEGIN SELECT RAISE(ABORT,'fixture'); END")
        with self.assertRaises(sqlite3.IntegrityError):self.send(6050,1001)
        self.assertEqual(self.query()[0].payload[6],1);self.assertFalse(self.s.db.in_transaction)

    def test_catalog_bounds_unknown_ids_disabled_and_partial_family(self):
        for rules in ([self.rules[0]]*2,[dict(family='ordinary',key=999)],[dict(family='ordinary',key=True)]):
            with self.assertRaises(ValueError):quests.configure(self.s,rules,self.maps.quest_templates)
        for raw in (b'1\tname',source('ordinary',1001)*2):
            with self.assertRaises(ValueError):quests.parse_templates('ordinary',raw)
        class Config:
            def read(self,name):
                if name=='basequest.txt':return source('ordinary',1001)
                raise ValueError('missing')
        self.assertEqual(set(quests.from_config(Config())),{('ordinary',1001)})
        disabled=source('ordinary',1001).replace(b'\t1\r\n',b'\t0\r\n')
        table={('ordinary',1001):quests.parse_templates('ordinary',disabled)[1001]}
        with self.assertRaises(ValueError):quests.configure(self.s,[self.rules[0]],table)

    def test_persistence_after_store_restart(self):
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/'test.sqlite3');s=Store(path);s.seed_local()
            try:
                quests.configure(s,self.rules,self.maps.quest_templates)
                quests.transition(s,1001,self.maps.quest_templates[('ordinary',1001)],2)
            finally:s.close()
            s=Store(path)
            try:
                q=quests.qualified(s,self.maps.quest_templates)
                self.assertEqual(quests.list_packets(s,1001,q,('ordinary',))[0].payload[6],2)
                self.assertFalse(quests.transition(s,1001,q[('ordinary',1001)],2))
            finally:s.close()

    def test_title_offer_is_not_lost_when_quest_lists_are_enabled(self):
        from server.kk_local import title_rewards
        from server.tests.test_title_rewards import choice
        self.maps.title_levels=frozenset(range(17))
        title_rewards.configure(self.s,2,[choice()],self.maps.title_levels)
        title_rewards.issue(self.s,1001,2,self.maps.title_levels)
        self.assertEqual([m.id for m in self.query()],[6020,6041,6042,1550,4125])


if __name__=='__main__':unittest.main()
