from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from server.kk_local.maps import MapCatalog,MapDefinition,MapAdmissionError,contained
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.rooms import RoomHub
from server.kk_local.store import Store
from server.kk_local.wire import Message
from server.kk_local import packets
from server.tests.test_competitive_rooms import request,prepare_handoff
from server.tests.test_local_service import hello
from server.tests.test_shared_rooms import lobby


def catalog(chooser=lambda pool:pool[0]):
    def row(i,cap=8,missing=()):return MapDefinition(i,str(i),cap,'map.xml','world',('a.rws','a.bsp'),missing)
    return MapCatalog({90:row(90),89:row(89,4),804:row(804),104:row(104,missing=('a.bsp',)),9000:row(9000)},
                      {0:{90,89,804},1:{90,804},3:{804},5:{89,90,804,104}},
                      groups={1:{90,804}},chooser=chooser)


class MapTests(unittest.TestCase):
    def test_explicit_ids_modes_capacity_and_missing_resources(self):
        c=catalog()
        self.assertEqual(c.resolve(5,4,90,90),(90,90))
        self.assertEqual(c.resolve(5,4,89,0),(89,89))
        for args,reason in [((3,4,90,90),'map_not_allowed_for_mode'),
                            ((5,8,89,89),'map_capacity_exceeded'),
                            ((5,4,104,104),'map_resources_missing'),
                            ((5,4,9000,9000),'map_not_allowed_for_mode'),
                            ((5,4,999,999),'unknown_map_id'),
                            ((5,4,90,804),'map_selection_mismatch')]:
            with self.subTest(args=args),self.assertRaisesRegex(MapAdmissionError,reason):c.resolve(*args)

    def test_random_groups_filter_unavailable_and_keep_legal_suggestion(self):
        calls=[]
        c=catalog(lambda pool:calls.append(pool) or pool[-1])
        self.assertEqual(c.resolve(5,8,0,0),(804,804))
        self.assertEqual(calls[-1],(90,804))
        self.assertEqual(c.resolve(5,8,1,90),(90,90))
        self.assertEqual(len(calls),1)
        self.assertEqual(c.resolve(3,8,1,90),(804,804))
        with self.assertRaisesRegex(MapAdmissionError,'no_available_maps'):c.resolve(10,8,0,0)

    def test_contained_resource_paths(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(contained(d,'bridge/zd.bsp'),Path(d).resolve()/'bridge/zd.bsp')
            for value in ('../x','/x','C:/x','bridge/../x',''):
                with self.assertRaises(ValueError):contained(d,value)

    def test_actual_map90_entry_and_single_selection_per_room(self):
        calls=[];c=catalog(lambda pool:calls.append(pool) or 90)
        store=Store(':memory:');store.seed_local()
        try:
            e=Engine(store,map_catalog=c);prepare_handoff(e)
            g=Connection(2);e.handle(g,hello(2010))
            e.register_p2p(1001,1001,('127.0.0.1',50000));e.handle(g,Message(1156,struct.pack('<QI',1001,1001)))
            original=request(5,0,0)
            rows=e.handle(g,original)
            self.assertEqual(struct.unpack_from('<ii',rows[0].payload,12),(90,90))
            self.assertEqual(e.room.request,original.payload)
            self.assertEqual(struct.unpack_from('<ii',e.room.resolved_request,38),(90,90))
            self.assertEqual(e.handle(g,original),[])
            self.assertEqual(len(calls),1)
            self.assertEqual([m.id for m in e.handle(g,Message(4030))],[4050,4080])
            self.assertEqual(g.phase,Phase.LOADING)
        finally:store.close()

    def test_shared_directory_and_join_do_not_reroll(self):
        calls=[];c=catalog(lambda pool:calls.append(pool) or 90)
        store=Store(':memory:');store.seed_local();store.provision_local(1002,'Second')
        try:
            hub=RoomHub();a=Engine(store,hub=hub,map_catalog=c);b=Engine(store,hub=hub,map_catalog=c,account_uid=1002)
            ca=lobby(a,1);cb=lobby(b,3)
            a.handle(ca,request(1,0,0))
            directory=b.handle(cb,Message(2260,bytes(3)))[0]
            self.assertEqual(struct.unpack_from('<ii',directory.payload,31),(90,90))
            joined=b.handle(cb,Message(3070,struct.pack('<HB11s',1,0,b'')))
            self.assertEqual(struct.unpack_from('<ii',joined[0].payload,12),(90,90))
            self.assertEqual(len(calls),1)
        finally:store.close()

    def test_config_consumption_and_missing_bsp_do_not_admit_map(self):
        class Config:
            path=Path('synthetic.spf2');count=3;primary_backup_equal=True;consumed=set()
            def __init__(self,ignored):pass
            def xml(self,name):
                return ET.fromstring({
                    'mapmgr.xml':'<MapInfo><MapConfig MapId="90" Name="Bridge" MaxPlayer="8" xmlfile="bridge" worldpath="bridge"/><RandomMap MapId="1"><Map MapId="90"/></RandomMap></MapInfo>',
                    'mapselect.xml':'<MapSelectInfo><LobbyLevel Level="4"><BattleMode Mode="5"><Map Id="0"/><Map Id="90"/></BattleMode></LobbyLevel></MapSelectInfo>',
                    'maps/bridge.xml':'<MapConfig><MapList><Map file="bridge.rws" cofile="zd.bsp"/></MapList></MapConfig>'}[name])
        with tempfile.TemporaryDirectory() as d,patch('server.kk_local.maps.ClientConfig',Config):
            world=Path(d)/'Data/Map/bridge';world.mkdir(parents=True)
            (world/'bridge.rws').write_bytes(b'fixture')
            c=MapCatalog.from_client(d)
            self.assertEqual(c.eligible(5,8),())
            (world/'zd.bsp').write_bytes(b'fixture')
            c=MapCatalog.from_client(d)
            self.assertEqual(c.resolve(5,8,1,0),(90,90))


if __name__=='__main__':unittest.main()
