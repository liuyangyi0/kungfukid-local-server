"""Public appearance and owned-weapon views do not transmit opaque profiles."""
import struct
import unittest
from server.kk_local.engine import Phase,Connection
from server.kk_local.wire import Message,ProtocolError
from server.kk_local import packets
from server.tests import test_shared_rooms as fixtures


class PublicPlayerQueryTests(unittest.TestCase):
    setUp=fixtures.SharedRoomTests.setUp
    tearDown=fixtures.SharedRoomTests.tearDown
    join=fixtures.SharedRoomTests.join

    def query(self,ident=2420,target=1002):
        return self.e1.handle(self.c1,Message(ident,struct.pack('<Q',target)))

    def test_native_preview_offsets_real_equipment_and_private_profile_mask(self):
        profile=bytearray(self.s.snapshot(1002)[2]);profile[30:80]=b'P'*50
        struct.pack_into('<I',profile,245,987654321)
        with self.s.db:self.s.db.execute('UPDATE accounts SET profile=? WHERE uid=1002',(bytes(profile),))
        before=self.s.snapshot(1002)
        reply=self.query()[0]
        self.assertEqual(reply.id,2421)
        requester,target,count=struct.unpack_from('<QQB',reply.payload)
        self.assertEqual((requester,target,count),(1001,1002,7))
        self.assertEqual(len(reply.payload),377+68*count)
        self.assertEqual(reply.payload[21:42].split(b'\0')[0],b'Second')
        self.assertEqual(reply.payload[139:142],profile[122:125])
        self.assertEqual(reply.payload[377:],before[3])
        self.assertEqual(reply.payload[47:97],bytes(50))
        self.assertEqual(reply.payload[17+245:17+249],bytes(4))
        self.assertEqual(self.s.snapshot(1002),before)
        self.assertEqual(self.e2.take_pending(self.c2),[])

    def test_collection_uses_unique_resource_ids_not_instances_or_quantity(self):
        r=bytearray(68);struct.pack_into('<IBI',r,0,2999001,25,253033)
        struct.pack_into('<I',r,9,0xffffffff);struct.pack_into('<H',r,23,99)
        with self.s.db:
            self.s.db.execute('INSERT INTO inventory VALUES(?,?,?)',(1002,2999001,bytes(r)))
            struct.pack_into('<I',r,0,2999002)
            self.s.db.execute('INSERT INTO inventory VALUES(?,?,?)',(1002,2999002,bytes(r)))
        out=self.query(2430)[0]
        self.assertEqual(out.id,2431)
        self.assertEqual(struct.unpack_from('<QQI',out.payload),(1001,1002,2))
        self.assertEqual(len(out.payload),38)
        self.assertEqual([struct.unpack_from('<IIB',out.payload,20+i*9) for i in range(2)],
                         [(253030,0,0),(253033,0,0)])

    def test_offline_unknown_and_cross_uid_low32_do_not_disclose(self):
        for target in (0,9999,(1<<32)+1002):
            self.assertNotEqual(self.query(target=target)[0].id,2421)
        for phase in (Phase.HANDOFF,Phase.CLOSED):
            self.c2.phase=phase
            for ident in (2420,2430):self.assertNotIn(self.query(ident)[0].id,(2421,2431))

    def test_room_queries_allowed_battle_and_wrong_connection_ignored(self):
        self.join()
        self.assertEqual(self.query()[0].id,2421)
        self.c1.phase=Phase.BATTLE
        self.assertEqual(self.query(),[])
        self.assertEqual(self.e1.handle(Connection(90,Phase.LOBBY,1001),Message(2420,struct.pack('<Q',1002))),[])

    def test_no_invented_character_on_invalid_profile_and_length_bounds(self):
        profile=bytearray(self.s.snapshot(1002)[2]);profile[122]=0
        with self.s.db:self.s.db.execute('UPDATE accounts SET profile=? WHERE uid=1002',(bytes(profile),))
        self.assertNotEqual(self.query()[0].id,2421)
        with self.assertRaises(ProtocolError):self.e1.handle(self.c1,Message(2420,bytes(4)))
        with self.assertRaises(ProtocolError):packets.public_weapon_collection(1001,1002,b'x')


if __name__=='__main__':unittest.main()
