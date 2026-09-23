"""Qualified Mode10/8110 source adapter; never execute client Lua.

Names/HP are global config definitions; script groups remain independent.
Other scripts, include bytecode versions and maps stay unqualified.
"""
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
import struct
from .maps import ClientConfig

CONFIG_SHA='142515f07e84aaab3ba8ea4d4c2b2ba76a14b70d35abc40fc09996e04061d914'
SCRIPT_SHA='c227b5bf3dae47e461a2e59b3d0832065d83e52c3bd10f44f6dc6f72deb3c15f'
INCLUDE_SHA='0a083607cab1456c0976038f1e78658a208607355c015060a4492259cde5280f'


@dataclass(frozen=True)
class Spawn:
    template: int
    position: tuple
    direction: int


@dataclass(frozen=True)
class Group:
    spawns: tuple
    trigger: tuple
    sub_limit: int
    group_limit: int
    block: int | None


@dataclass(frozen=True)
class FosterPlan:
    map_id: int
    templates: tuple
    initial_hp: tuple
    players: tuple  # ordered (position,direction), UID order is native room iteration
    groups: tuple
    global_limit: int
    source: tuple

    def for_players(self,count):
        if not 1<=count<=len(self.players):raise ValueError('Mode10 player count unsupported')
        return self


def f32(value):return struct.unpack('<f',struct.pack('<f',float(value)))[0]


def compile_plan(config,script,include):
    if tuple(hashlib.sha256(raw).hexdigest() for raw in (config,script,include))!=(CONFIG_SHA,SCRIPT_SHA,INCLUDE_SHA):
        raise ValueError('unqualified Mode10 config/script/include')
    config_text=re.sub(r'--[^\r\n]*','',config.decode('gb18030'))
    maps=config_text.split('monsters={',1)[0]
    if re.findall(r'\{\s*8110\s*,\s*"([A-Za-z0-9_]+)"\s*\}',maps)!=['act_jiedoudazhan_easy']:
        raise ValueError('Mode10 map binding mismatch')
    #Only literal row prefix before nested UState/drop tables. Preserve name whitespace.
    rows=[]
    for match in re.finditer(r'^\["[^"\r\n]+"\]\s*=\s*\{([^\r\n]+)',config_text,re.M):
        prefix=match[1].split('{',1)[0]
        fields=re.findall(r'"[^"\r\n]*"|[^,\s]+',prefix)
        if len(fields)<12 or not fields[0].startswith('"'):continue
        name=fields[0][1:-1]
        hp=f32(fields[11])
        if not hp>0 or hp==float('inf'):raise ValueError('Mode10 HP invalid')
        rows.append((name,hp))
    if len(rows)!=262 or len({name for name,_ in rows})!=262:raise ValueError('Mode10 global template set mismatch')
    rows.sort(key=lambda row:row[0].encode('gbk'))
    names=tuple(row[0] for row in rows);hp=tuple(row[1] for row in rows);indices={n:i for i,n in enumerate(names)}
    text=re.sub(r'--[^\r\n]*','',script.decode('gb18030'))
    players_text=text.split('local players',1)[1].split('local event_boxs',1)[0]
    players=[]
    for xyz,direction in re.findall(r'\{p=\{([^}]+)\},d=(\d+)\}',players_text):
        position=tuple(f32(v.strip()) for v in xyz.split(','))
        if len(position)!=3:raise ValueError('Mode10 player position shape')
        players.append((position,int(direction)))
    if len(players)!=6:raise ValueError('Mode10 six player positions required')
    parts=text.split('event=EVENT_GROUP({')
    if len(parts)!=3:raise ValueError('Mode10 group count changed')
    groups=[]
    for i,part in enumerate(parts[1:]):
        #Each qualified group has one sub-list. Dynamic expressions not interpreted.
        trigger=tuple(f32(v) for v in re.search(r'born=\{([^}]+)\}',part)[1].split(','))
        limit=re.search(r'max=(\d+),subs=\{\s*\{max=(\d+),monsters=',part)
        if len(trigger)!=6 or limit is None:raise ValueError('Mode10 group limits missing')
        block=re.search(r'block=(\d+)',part)
        spawns=[]
        for name,xyz,direction in re.findall(r'\{n="([^"]+)",p=\{([^}]+)\}(?:,\s*d=(\d+))?\}',part):
            if name not in indices:raise ValueError('Mode10 spawn missing global template')
            spawns.append(Spawn(indices[name],tuple(f32(v.strip()) for v in xyz.split(',')),int(direction) if direction else 2))
        if len(spawns)!=(2,21)[i]:raise ValueError('Mode10 spawn list changed')
        groups.append(Group(tuple(spawns),trigger,int(limit[2]),int(limit[1]),int(block[1]) if block else None))
    global_limit=int(re.search(r'map_init\(players,\s*event_boxs,\s*(\d+)\)',text)[1])
    return FosterPlan(8110,names,hp,tuple(players),tuple(groups),global_limit,(CONFIG_SHA,SCRIPT_SHA,INCLUDE_SHA))


def from_client(root):
    c=ClientConfig(Path(root)/'Data/config.spf2')
    plan=compile_plan(c.read('script/pve/config.lua'),c.read('script/pve/act_jiedoudazhan_easy.lua'),c.read('script/pve/include'))
    return {8110:plan}
