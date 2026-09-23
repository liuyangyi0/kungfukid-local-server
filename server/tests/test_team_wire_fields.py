"""Independent byte offsets from 824490/81CF10/81CB00, not serializer round trips."""
import unittest
from server.kk_local import packets
from server.kk_local.store import Store
from server.tests.test_local_service import create_room


class TeamWireFieldsTests(unittest.TestCase):
    def test_self_and_peer_team_not_confused_with_registry_key(self):
        s=Store(':memory:');s.seed_local()
        try:
            _,name,profile,inventory=s.snapshot(1001)
            request=bytearray(create_room().payload);request[46]=1
            for slot,team in ((0,1),(1,0),(2,0),(3,1)):
                fighter=packets.fighter_snapshot(1001,name,profile,inventory,slot,team,1001)
                self.assertEqual(fighter[8:11],bytes((slot,slot,team)))
                entry=packets.room_entry_member(bytes(request),1001,1,slot,team,fighter).payload
                self.assertEqual(entry[10:12],bytes((slot,slot)))
                self.assertEqual(entry[66],team)
                self.assertEqual(entry[104:107],bytes((slot,slot,team)))
        finally:s.close()
