"""Read-only map admission from the installed client's own configuration.

No second persisted resource registry. Reuses the existing SPF2 transforms;
only map configuration is decoded in memory. Selection is local service policy,
not a claim to have recovered old-server random selection or all map gameplay.
"""
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
import secrets
import struct
import sys
import xml.etree.ElementTree as ET


class MapAdmissionError(ValueError):
    pass


def contained(root, relative):
    value=relative.replace('\\','/')
    p=PurePosixPath(value)
    if not value or p.is_absolute() or any(x in ('.','..') or ':' in x for x in value.split('/')):
        raise ValueError('unsafe_map_resource_path')
    root=Path(root).resolve();result=(root/Path(*p.parts)).resolve()
    if not result.is_relative_to(root):raise ValueError('map_resource_escape')
    return result


class ClientConfig:
    """Bounded primary table reader; the installed clone has a differing backup.

    This matches the existing inventory grant reader's explicit primary-table
    policy. It does not relax the independent strict SPF2 extractor or repair
    either table. The mismatch is reported, not hidden.
    """
    def __init__(self, package):
        tool_root=Path(__file__).resolve().parents[2]/'tools/resource-recovery'
        if str(tool_root) not in sys.path:sys.path.insert(0,str(tool_root))
        from spf2_index import _parse_radix_tree, _decode_path
        self.path=Path(package).resolve(strict=True)
        if not 64<=self.path.stat().st_size<=128*1024*1024:raise ValueError('config_size')
        self.data=self.path.read_bytes()
        tree,table,backup,count=struct.unpack_from('<4I',self.data,40)
        if not (64<=tree<=table<backup and 1<=count<=10000 and
                table+count*8==backup and backup+count*8==len(self.data)):
            raise ValueError('config_boundaries')
        paths,_=_parse_radix_tree(self.data[tree:table],count)
        self.entries={};self.tree=tree;self.table=table;self.count=count
        self.primary_backup_equal=self.data[table:backup]==self.data[backup:]
        self.consumed=set()
        for index,raw in paths.items():
            key=_decode_path(raw).replace('\\','/').lstrip('/').casefold()
            if key in self.entries:raise ValueError('duplicate_config_path')
            self.entries[key]=index

    def xml(self, name):
        from spf2_extract import decode_not_xor_zlib, RECOVERED_CONFIG_KEY
        key=name.replace('\\','/').lstrip('/').casefold()
        matches=[i for p,i in self.entries.items() if p==key or p=='data/config/'+key]
        if len(matches)!=1:raise ValueError('missing_or_ambiguous_map_config')
        offset,size=struct.unpack_from('<II',self.data,self.table+matches[0]*8)
        if not (64<=offset and size>=4 and offset+size+4<=self.tree):raise ValueError('map_config_bounds')
        if struct.unpack_from('<I',self.data,offset)[0]!=0x2200:raise ValueError('map_config_flags')
        raw=decode_not_xor_zlib(self.data[offset+4:offset+size+4],RECOVERED_CONFIG_KEY)
        if len(raw)>8*1024*1024 or b'<!DOCTYPE' in raw.upper():raise ValueError('map_xml_bounds')
        self.consumed.add(key)
        return ET.fromstring(raw.decode('gb18030'))


@dataclass(frozen=True)
class MapDefinition:
    id: int
    name: str
    capacity: int
    config: str
    world: str
    required_files: tuple
    missing: tuple


class MapCatalog:
    def __init__(self, definitions, allowed, *, groups=None, chooser=secrets.choice, source=None):
        self.maps=dict(definitions)
        self.allowed={int(mode):frozenset(ids) for mode,ids in allowed.items()}
        self.groups={int(ident):frozenset(ids) for ident,ids in (groups or {}).items()}
        self.chooser=chooser;self.source=source or {}

    @classmethod
    def from_client(cls, client_root, *, lobby_level=4):
        root=Path(client_root).resolve(strict=True)
        config=ClientConfig(root/'Data/config.spf2')
        table=config.xml('mapmgr.xml');selection=config.xml('mapselect.xml')
        allowed={}
        levels=[n for n in selection.findall('LobbyLevel') if int(n.get('Level','-1'))==lobby_level]
        if len(levels)!=1:raise ValueError('map_lobby_level_missing_or_ambiguous')
        for mode in levels[0].findall('BattleMode'):
            ident=int(mode.attrib['Mode'])
            if ident in allowed:raise ValueError('duplicate_map_mode')
            allowed[ident]={int(n.attrib['Id']) for n in mode.findall('Map') if int(n.attrib['Id'])>0}
        definitions={}
        for row in table.findall('MapConfig'):
            ident=int(row.attrib['MapId']);capacity=int(row.attrib['MaxPlayer'])
            if ident in definitions or ident<0 or not 1<=capacity<=8:raise ValueError('invalid_map_definition')
            world=row.attrib['worldpath'];name=row.attrib.get('Name','')
            config_name='maps/'+row.attrib['xmlfile']+'.xml'
            required=[];missing=[]
            try:
                directory=contained(root/'Data/Map',world)
                scene_config=config.xml(config_name)
                scenes=scene_config.findall('./MapList/Map')
                if not scenes:raise ValueError('map_scenes_missing')
                for scene in scenes:
                    for attr in ('file','cofile'):
                        path=contained(directory,scene.attrib[attr]);required.append(str(path.relative_to(root)))
                        if not path.is_file() or path.stat().st_size==0:missing.append(str(path.relative_to(root)))
                    for part in scene.findall('./Foreground/Part'):
                        if part.get('file'):
                            path=contained(directory,part.attrib['file']);required.append(str(path.relative_to(root)))
                            if not path.is_file() or path.stat().st_size==0:missing.append(str(path.relative_to(root)))
            except (ValueError,KeyError,ET.ParseError,OSError):
                missing.append('configuration_or_resource_path_invalid')
            definitions[ident]=MapDefinition(ident,name,capacity,config_name,world,tuple(sorted(set(required))),tuple(sorted(set(missing))))
        groups={}
        for node in table.findall('RandomMap'):
            ident=int(node.attrib['MapId'])
            if ident<=0 or ident in definitions or ident in groups:raise ValueError('invalid_random_map_group')
            groups[ident]={int(n.attrib['MapId']) for n in node.findall('Map')}
            if not groups[ident] or not groups[ident]<=definitions.keys():raise ValueError('random_map_group_members')
        # Retain the explicit water4 compatibility choice already used by the
        # local service; it is registered but omitted from the level4 menus.
        if 804 in definitions:
            for mode in (0,1,2,3,5):allowed.setdefault(mode,set()).add(804)
        return cls(definitions,allowed,groups=groups,source=dict(package=str(config.path),entries=config.count,
            primary_backup_equal=config.primary_backup_equal,decoded_map_configs=len(config.consumed),
            lobby_level=lobby_level,water4_compatibility_override=True))

    def mode_ids(self, mode):
        ids=set()
        for ident in self.allowed.get(mode,()):ids.update(self.groups.get(ident,(ident,)))
        return ids

    def eligible(self, mode, capacity):
        return tuple(sorted(i for i in self.mode_ids(mode) if i in self.maps and
            not self.maps[i].missing and self.maps[i].capacity>=capacity))

    def resolve(self, mode, capacity, chosen, suggested):
        if capacity not in (2,4,6,8):raise MapAdmissionError('unsupported_room_capacity')
        if chosen in (0,-1) or chosen in self.groups:
            pool=self.eligible(mode,capacity)
            if chosen in self.groups:pool=tuple(i for i in pool if i in self.groups[chosen])
            if not pool:raise MapAdmissionError('no_available_maps_for_mode')
            selected=suggested if suggested in pool else self.chooser(pool)
            if selected not in pool:raise MapAdmissionError('invalid_random_selection')
            return selected,selected
        if chosen not in self.maps or chosen<=0:raise MapAdmissionError('unknown_map_id')
        if chosen not in self.mode_ids(mode):raise MapAdmissionError('map_not_allowed_for_mode')
        if self.maps[chosen].missing:raise MapAdmissionError('map_resources_missing')
        if capacity>self.maps[chosen].capacity:raise MapAdmissionError('map_capacity_exceeded')
        if suggested not in (0,-1,chosen):raise MapAdmissionError('map_selection_mismatch')
        return chosen,chosen

    def summary(self):
        modes=(0,1,2,3,5)
        return dict(schema='kk-installed-map-admission-v1',source=self.source,
            registered=len(self.maps),resource_ready=sum(not m.missing for m in self.maps.values()),
            random_groups={str(k):sorted(v) for k,v in self.groups.items()},
            playable_maps=sorted(set().union(*(self.eligible(mode,2) for mode in modes))),
            pools={str(mode):list(self.eligible(mode,8)) for mode in modes},
            unavailable=[dict(id=m.id,name=m.name,missing=list(m.missing)) for m in self.maps.values() if m.missing],
            native_all_maps_verified=False,policy='PROVISIONAL_LOCAL_SERVICE')


if __name__=='__main__':
    import argparse,json
    p=argparse.ArgumentParser();p.add_argument('--client-root',required=True)
    a=p.parse_args();print(json.dumps(MapCatalog.from_client(a.client_root).summary(),ensure_ascii=False,indent=2))
