import struct
import tempfile
from pathlib import Path
import unittest

from server.kk_local.store import Store
from server.kk_local.engine import Engine, Connection, Phase
from server.kk_local.wire import Message, ProtocolError


class EquipmentOperationsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / 'account.db')
        self.store = Store(self.path)
        self.store.seed_local()
        self.store.apply_grant(dict(schema='kk-local-inventory-grant-v1', uid=1001,
            grant_id='appearance-test', rows=[[181001,18,1],[201001,20,1],[211001,21,1]]))
        self.engine = Engine(self.store)
        self.client = Connection(1, Phase.LOBBY, 1001)
        self.engine.game = self.client

    def tearDown(self):
        self.store.close()
        self.directory.cleanup()

    def find(self, prop):
        return next((i,r) for i,r in self.store.db.execute('SELECT instance,record FROM inventory')
                    if struct.unpack_from('<I', r, 5)[0] == prop)

    def test_suit_displaces_body_but_not_owned_parts(self):
        original = self.store.snapshot(1001)
        instance,_ = self.find(181001)
        request = struct.pack('<IIQ',instance,4,0)
        reply = self.engine.handle(self.client, Message(2080,request))
        self.assertEqual([m.id for m in reply],[1120,2090])
        self.assertEqual(struct.unpack_from('<H',self.find(121005)[1],17)[0],0)
        self.assertEqual(struct.unpack_from('<H',self.find(181001)[1],17)[0],4)
        self.assertEqual(struct.unpack_from('<H',self.find(161005)[1],17)[0],6)
        self.assertEqual(len(self.store.snapshot(1001)[3]),len(original[3]))
        self.assertEqual(self.store.snapshot(1001)[2], original[2])

    def test_accessories_native_slots_and_wrong_slot_unchanged(self):
        for prop,slot in ((201001,10),(211001,11)):
            i,original=self.find(prop)
            self.store.equip(1001,i,slot)
            new=self.find(prop)[1]
            self.assertEqual(new[:17],original[:17])
            self.assertEqual(new[19:],original[19:])
            with self.assertRaises(ValueError):
                self.store.equip(1001,i,slot+20)
            self.assertEqual(self.find(prop)[1],new)

    def test_unequip_notification_precedes_snapshot_and_survives_restart(self):
        instance,old=self.find(253030)
        out=self.engine.handle(self.client,Message(2300,struct.pack('<I',instance)))
        self.assertEqual([m.id for m in out],[2310,1120])
        self.assertEqual(len(out[0].payload),72)
        self.assertEqual(out[0].payload[:4],struct.pack('<I',instance))
        new=self.find(253030)[1]
        self.assertEqual(new,old[:17]+b'\0\0'+old[19:])
        self.assertEqual(out[0].payload[4:],new)
        self.assertEqual(self.engine.handle(self.client,Message(2300,struct.pack('<I',instance))),[])
        self.store.close()
        self.store=Store(self.path)
        self.assertEqual(self.find(253030)[1],new)

    def test_ownership_phase_and_malformed_request(self):
        instance,_=self.find(253030)
        before=self.store.snapshot(1001)
        with self.assertRaises(ValueError):
            self.store.unequip(2002,instance)
        self.assertEqual(self.engine.handle(self.client,Message(2300,struct.pack('<I',999999))),[])
        self.client.phase=Phase.BATTLE
        self.assertEqual(self.engine.handle(self.client,Message(2300,struct.pack('<I',instance))),[])
        self.assertEqual(self.store.snapshot(1001),before)
        with self.assertRaises(ProtocolError):
            self.engine.handle(self.client,Message(2300,b'x'))

    def test_single_session_lobby_directory_is_truthfully_empty(self):
        for page in (0,1,255):
            self.assertEqual(self.engine.handle(self.client,Message(2260,bytes([0,page,0]))),
                             [Message(2280,bytes(8))])
        with self.assertRaises(ProtocolError):
            self.engine.handle(self.client,Message(2260,bytes(2)))
