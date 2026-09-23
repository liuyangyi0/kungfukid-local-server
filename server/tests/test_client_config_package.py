"""Unmocked distribution dependency test using an entirely synthetic SPF2.

Unlike the map-policy unit tests, this exercises the exported index/decode
modules through ClientConfig and MapCatalog without replacing their imports.
"""
from pathlib import Path
import struct
import tempfile
import unittest
import zlib
from server.kk_local.maps import ClientConfig,MapCatalog


def package(entries):
    # Explicit fixture encoding, not a copy of any proprietary resource payload.
    key=b'F48A715746514613BD70EF276119128F'
    payload=bytearray();records=[]
    root_size=4+12*len(entries)
    tree=bytearray(root_size);struct.pack_into('<I',tree,0,len(entries))
    for index,(path,text) in enumerate(entries):
        compressed=zlib.compress(text.encode('utf8'))
        cipher=bytes((~value^key[i%len(key)])&255 for i,value in enumerate(compressed))
        records.append((64+len(payload),len(cipher)))
        payload.extend(struct.pack('<I',0x2200)+cipher)
        chunks=[path.encode('ascii')[i:i+8] for i in range(0,len(path),8)]
        position=4+12*index
        for i,chunk in enumerate(chunks):
            tree[position:position+8]=chunk.ljust(8,b'\0')
            if i==len(chunks)-1:
                struct.pack_into('<I',tree,position+8,0xff000000|index)
            else:
                next_node=len(tree);struct.pack_into('<I',tree,position+8,next_node)
                tree.extend(struct.pack('<I',1)+bytes(12));position=next_node+4
    table=b''.join(struct.pack('<II',*row) for row in records)
    header=bytearray(64);tree_start=64+len(payload);table_start=tree_start+len(tree)
    struct.pack_into('<4I',header,40,tree_start,table_start,table_start+len(table),len(entries))
    return bytes(header+payload+tree)+table+table


ENTRIES=[
    ('mapmgr.xml','<MapInfo><MapConfig MapId="90" Name="SyntheticBridge" MaxPlayer="4" xmlfile="bridge" worldpath="bridge"/></MapInfo>'),
    ('mapselect.xml','<MapSelectInfo><LobbyLevel Level="4"><BattleMode Mode="5"><Map Id="90"/></BattleMode></LobbyLevel></MapSelectInfo>'),
    ('maps/bridge.xml','<MapConfig><MapList><Map file="bridge.rws" cofile="zd.bsp"/></MapList></MapConfig>'),
]


class ClientConfigPackageTests(unittest.TestCase):
    def test_actual_radix_cipher_zlib_xml_and_map_admission(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'Data').mkdir()
            path=root/'Data/config.spf2';path.write_bytes(package(ENTRIES))
            before=path.read_bytes()
            config=ClientConfig(path)
            self.assertEqual(config.xml('mapmgr.xml').tag,'MapInfo')
            self.assertEqual(config.count,3);self.assertTrue(config.primary_backup_equal)
            world=root/'Data/Map/bridge';world.mkdir(parents=True)
            (world/'bridge.rws').write_bytes(b'synthetic-rws-presence-only')
            (world/'zd.bsp').write_bytes(b'synthetic-bsp-presence-only')
            catalog=MapCatalog.from_client(root)
            self.assertEqual(catalog.eligible(5,4),(90,))
            self.assertEqual(catalog.resolve(5,4,90,90),(90,90))
            self.assertEqual(catalog.source['decoded_map_configs'],3)
            self.assertEqual(path.read_bytes(),before)
    def test_real_decoder_rejects_corruption_and_missing_resources(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'Data').mkdir()
            path=root/'Data/config.spf2'
            raw=bytearray(package(ENTRIES));raw[64]=1;path.write_bytes(raw)
            with self.assertRaises(ValueError):ClientConfig(path).xml('mapmgr.xml')
            path.write_bytes(package(ENTRIES))
            self.assertEqual(MapCatalog.from_client(root).eligible(5,4),())

if __name__=='__main__':unittest.main()
