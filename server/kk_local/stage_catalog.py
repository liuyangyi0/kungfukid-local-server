"""Compile the qualified9170 Lua plan without executing Lua or copying assets.

Exact source/runtime identities gate this small adapter. Other maps/versions
remain unsupported, not silently assigned the zombie map's25-wave rules.
"""
from collections import Counter
from dataclasses import dataclass
import hashlib
from pathlib import Path
import re
from .maps import ClientConfig

SCRIPT_SHA='2d966acbb0f2c255e3f0b644b45a845c863ab90acc4eec0dc517235920587c94'
RUNTIME_SHA='0528f4d1d668b73982d66cd7ff167fc869e2ce1e410bbe81f34ef2d41b21a84d'


@dataclass(frozen=True)
class StagePlan:
    map_id: int
    templates: tuple
    variants: tuple  # (min_players,max_players,tuple of sorted(template,count))
    source: tuple

    def waves(self,players):
        rows=[waves for low,high,waves in self.variants if low<=players<=high]
        if len(rows)!=1:raise ValueError('stage player count unsupported')
        return tuple(dict(w) for w in rows[0])


def compile_zombie(script,runtime):
    if hashlib.sha256(script).hexdigest()!=SCRIPT_SHA or hashlib.sha256(runtime).hexdigest()!=RUNTIME_SHA:
        raise ValueError('unqualified stage script/runtime version')
    source=re.sub(r'--[^\r\n]*','',script.decode('gb18030'))
    definitions=source.split('tMapMonsters=',1)[1].split('\n};',1)[0]
    names=re.findall(r'\["[^"\n]+"\]\s*=\s*\{\s*"([^"\n]+)"',definitions)
    if len(names)!=5 or len(set(names))!=5:raise ValueError('stage template names changed')
    names=tuple(sorted(names,key=lambda x:x.encode('gbk')))
    indices={name:i for i,name in enumerate(names)}
    groups={}
    for number,side,body in re.findall(r'tMonsterBorn(\d+)_(\d+)\s*=\s*\{(.*?)\n\};',source,re.S):
        match=re.search(r'MonsterList\s*=\s*\{([^}]*)\}',body)
        if match is None:raise ValueError('stage group lacks monster list')
        monsters=re.findall(r'"([^"\n]+)"',match[1])
        if not monsters or any(n not in indices for n in monsters):raise ValueError('unknown stage template')
        groups[int(number),int(side)]=tuple(indices[n] for n in monsters)
    begin=source.split('function MonsterWaveBegin',1)[1].split('local tWave',1)[0]
    branches=re.findall(r'(?:elseif|if)\s+(WaveIndex.*?)\s+then(.*?)(?=\belseif\b|\bend\b)',begin,re.S)
    variants=[]
    #This source's main maps<=2/<=4/else to sides2,4 /1,2,4 /all.
    for low,high,sides in ((1,2,(2,4)),(3,4,(1,2,4)),(5,8,(1,2,3,4))):
        waves=[]
        for wave in range(1,26):
            selected=[]
            for condition,body in branches:
                condition=re.sub(r'\s+','',condition)
                single=re.fullmatch(r'WaveIndex==(\d+)',condition)
                interval=re.fullmatch(r'WaveIndex>=(\d+)andWaveIndex<=(\d+)',condition)
                yes=(single and wave==int(single[1])) or (interval and int(interval[1])<=wave<=int(interval[2]))
                if yes:selected.append(body)
            if len(selected)!=1:raise ValueError('ambiguous stage wave branch')
            body=selected[0];counts=Counter()
            for side in sides:
                assignment=re.search(rf'tMB{side}\s*=\s*tMonsterBorn(\d+)_{side}\s*;',body)
                expression=re.search(rf'tMB{side}\.BornConfig\.Count\s*=\s*([^;]+);',body)
                if assignment is None or expression is None:raise ValueError('missing stage group assignment')
                expr=re.sub(r'\s+','',expression[1]);literal=re.fullmatch(r'\d+',expr)
                formula=re.fullmatch(r'(\d+)\+WaveIndex-(\d+)',expr)
                if literal:count=int(expr)
                elif formula:count=int(formula[1])+wave-int(formula[2])
                else:raise ValueError('unsupported stage count expression')
                if not 0<count<1000:raise ValueError('stage count bounds')
                #Runtime indexes rather than cycling: out-of-list nil creates nothing.
                counts.update(groups[int(assignment[1]),side][:count])
            waves.append(tuple(sorted(counts.items())))
        variants.append((low,high,tuple(waves)))
    return StagePlan(9170,names,tuple(variants),(SCRIPT_SHA,RUNTIME_SHA))


def from_client(root):
    config=ClientConfig(Path(root)/'Data/config.spf2')
    text=config.read('script/pve/config.lua').decode('gb18030')
    text=re.sub(r'--[^\r\n]*','',text)
    #Only the map-list section, not arbitrary occurrences elsewhere in config.
    maps=text.split('maps={',1)[1].split('};',1)[0]
    bindings=re.findall(r'\{\s*9170\s*,\s*"([A-Za-z0-9_]+)"\s*\}',maps)
    if bindings!=['act_zombiedefend_normal']:raise ValueError('stage9170 script binding mismatch')
    plan=compile_zombie(config.read('script/pve/act_zombiedefend_normal.lua'),config.read('script/pve/stageassault.lua'))
    return {9170:plan}
