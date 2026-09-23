import struct
import unittest
from server.kk_local import packets
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.rooms import RoomHub
from server.kk_local.store import Store
from server.kk_local.wire import Message,ProtocolError
from server.tests.test_shared_rooms import lobby
from server.tests.test_local_service import create_room
from server.tests.test_local_service import hello
from server.tests.test_competitive_rooms import prepare_handoff

def settings():
    p=bytearray(48);struct.pack_into('<ii',p,0,804,804)
    p[9]=1;p[12]=1;p[14:21]=b'Changed';p[35:41]=b'locked'
    struct.pack_into('<H',p,46,240)
    return bytes(p)

class RoomSettingsTests(unittest.TestCase):
    def test_single_session_uses_same_settings_and_team_contract(self):
        s=Store(':memory:');s.seed_local()
        try:
            e=Engine(s);prepare_handoff(e);c=Connection(2);e.handle(c,hello(2010))
            e.register_p2p(101,102,('127.0.0.1',25000))
            e.handle(c,Message(1156,struct.pack('<QI',1001,101)))
            e.handle(c,create_room())
            self.assertEqual(e.handle(c,Message(3200,settings())),[Message(3220,settings())])
            self.assertEqual(e.handle(c,Message(3230,b'\1')),[Message(3250,struct.pack('<QBB',1001,1,0))])
            self.assertEqual(e.room.entry[11],0)  # Registry key is unchanged.
            self.assertEqual(e.room.entry[66],1)  # Local player team in3100.
            self.assertEqual(e.room.entry[106],1) # Fighter record+10 agrees.
            self.assertEqual(e.handle(c,Message(3230,b'\1')),[])
        finally:s.close()

    def test_team_change_preserves_registry_slot_and_revokes_ready(self):
        s=Store(':memory:');s.seed_local();s.provision_local(1002,'Peer')
        try:
            hub=RoomHub();a=Engine(s,hub=hub);b=Engine(s,hub=hub,account_uid=1002)
            ca=lobby(a,1);cb=lobby(b,3);a.handle(ca,create_room())
            b.handle(cb,Message(3070,struct.pack('<HB11s',1,0,b'')));a.take_pending(ca)
            room=a.room;room.members[1001].ready=True;room.members[1002].ready=True
            out=b.handle(cb,Message(3230,b'\0'))
            self.assertEqual(out[0],Message(3250,struct.pack('<QBB',1002,0,1)))
            self.assertEqual([m.id for m in out],[3250,4070,4070])
            self.assertEqual(a.take_pending(ca),out)
            self.assertEqual((room.members[1002].slot,room.members[1002].team),(1,0))
            self.assertFalse(any(m.ready for m in room.members.values()))
            self.assertEqual(b.handle(cb,Message(3230,b'\0')),[])
            self.assertEqual(b.handle(cb,Message(3230,b'\xff'))[0].id,20150)
            self.assertEqual(room.members[1002].team,0)
        finally:s.close()

    def test_exact_mapping_and_invalid_options(self):
        old=create_room().payload
        updated,reply=packets.update_room_request(old,settings())
        self.assertEqual(reply,Message(3220,settings()))
        self.assertEqual(updated[:21],settings()[14:35])
        self.assertEqual(updated[21:32],settings()[35:46])
        self.assertEqual(updated[32:37],bytes([0,1,0,1,0]))
        self.assertEqual(updated[37],old[37]);self.assertEqual(updated[46],old[46])
        for offset,value in ((8,1),(9,2),(11,1),(14,0),(46,0)):
            bad=bytearray(settings());bad[offset]=value
            with self.assertRaises(ProtocolError):packets.update_room_request(old,bytes(bad))

    def test_shared_owner_atomicity_reset_and_password_privacy(self):
        s=Store(':memory:');s.seed_local();s.provision_local(1002,'Peer')
        try:
            hub=RoomHub();a=Engine(s,hub=hub);b=Engine(s,hub=hub,account_uid=1002)
            ca=lobby(a,1);cb=lobby(b,3);a.handle(ca,create_room())
            b.handle(cb,Message(3070,struct.pack('<HB11s',1,0,b'')));a.take_pending(ca)
            room=a.room;before=room.request
            self.assertEqual(b.handle(cb,Message(3200,settings()))[0].id,20150)
            self.assertEqual(room.request,before)
            room.members[1002].ready=True
            out=a.handle(ca,Message(3200,settings()))
            self.assertEqual([m.id for m in out],[3220,4070])
            self.assertEqual(b.take_pending(cb),out);self.assertFalse(room.members[1002].ready)
            self.assertEqual(a.handle(ca,Message(3200,settings())),[])
            record=packets.room_list_record(1,room.request,2)
            self.assertEqual(record[31],1);self.assertNotIn(b'locked',record)
            prior=room.request;bad=bytearray(settings());struct.pack_into('<ii',bad,0,999999,999999)
            self.assertEqual(a.handle(ca,Message(3200,bytes(bad)))[0].id,20150)
            self.assertEqual(room.request,prior)
            ca.phase=Phase.BATTLE;room.stage='battle'
            self.assertEqual(a.handle(ca,Message(3200,settings()))[0].id,20150)
            self.assertEqual(room.request,prior)
        finally:s.close()
