"""Mode10 concurrent event groups driven by authenticated native observations.

HP is only a received-event projection. It never writes client HP or awards;
missing/direct-peer events can keep completion unconfirmed rather than guessed.
"""
from collections import Counter
import struct
from .foster_catalog import f32
from .layouts import finite,decode_battle
from .pve import StageBattle
from .wire import ProtocolError,Message


def positions(payload,roster):
    p=bytes(payload)
    if len(p)!=183 or struct.unpack_from('<I',p)[0]!=20405 or p[12:14]!=b'\1\1':raise ProtocolError('20405 exact183/header')
    rows=[];seen=set()
    for i in range(6):
        part=p[39+24*i:63+24*i];uid=struct.unpack_from('<Q',part)[0]
        if not uid:
            if any(part):raise ProtocolError('20405 vacant record not zero')
            continue
        if uid not in roster or uid in seen:raise ProtocolError('20405 roster identity')
        point=finite(struct.unpack_from('<fff',part,8));direction=struct.unpack_from('<I',part,20)[0]
        if direction>7:raise ProtocolError('20405 direction')
        rows.append((uid,point,direction));seen.add(uid)
    if seen!=set(roster):raise ProtocolError('20405 incomplete roster')
    return tuple(rows)


class FosterBattle:
    def __init__(self,plan,players):
        self.plan=plan.for_players(players)
        self.actors={};self.blocks={};self.motion={};self.event_versions={}
        self.positions=None;self.player_motion={};self.finished=False;self.finish_sequence=-1
        self.spawned=[0]*len(plan.groups);self.retired=[0]*len(plan.groups);self.triggered=[False]*len(plan.groups)
        self.players=players

    active=StageBattle.active

    def trigger(self,point):
        for i,g in enumerate(self.plan.groups):
            if all(g.trigger[j]<=point[j]<=g.trigger[j+3] for j in range(3)):self.triggered[i]=True

    def receive_positions(self,payload,roster):
        rows=positions(payload,roster)
        expected=Counter(self.plan.players[:self.players])
        if Counter((point,direction) for _,point,direction in rows)!=expected:raise ProtocolError('20405 differs from qualified script positions')
        if self.positions is not None:
            if self.positions[39:]!=payload[39:]:raise ProtocolError('changed20405 position snapshot')
            return False
        self.positions=bytes(payload)
        for _,point,_ in rows:self.trigger(point)
        return True

    def group_complete(self,index):
        g=self.plan.groups[index]
        if self.spawned[index]!=len(g.spawns):return False
        active=[a for a in self.actors.values() if a['active'] and a['group']==index]
        return all(a['hp']==0 for a in active) and self.retired[index]+len(active)==len(g.spawns)

    def complete(self):return self.finished and all(self.group_complete(i) for i in range(len(self.plan.groups)))

    def block_event(self,row):
        if row['id']==20404:
            for i,g in enumerate(self.plan.groups):
                if g.block==row['key'] and not self.group_complete(i):return False
        return StageBattle.block_event(self,row)

    def actor_event(self,row,players):
        actor=row['actor'];seq=row['sequence'];old=self.actors.get(actor)
        if not actor or actor in players or (old and seq<=max(old['sequence'],self.event_versions.get((row['sender'],actor),-1))):return False
        if row['id']==20400:
            if self.finished or (old and old['active']) or (old is None and len(self.actors)>=100):return False
            living=[0]*len(self.plan.groups);active=0
            for a in self.actors.values():
                if a['active']:
                    active+=1
                    if a['hp']>0:living[a['group']]+=1
            if active>=self.plan.global_limit:return False  #includes corpses, unlike sub-list limit
            matches=[]
            for i,g in enumerate(self.plan.groups):
                if not self.triggered[i] or living[i]>=min(g.sub_limit,g.group_limit) or self.spawned[i]>=len(g.spawns):continue
                expected=g.spawns[self.spawned[i]]
                if (row['template'],row['position'],row['direction_raw'])==(expected.template,expected.position,expected.direction):matches.append(i)
            if len(matches)!=1:return False
            group=matches[0];hp=self.plan.initial_hp[row['template']]
            self.spawned[group]+=1
            self.actors[actor]=dict(active=True,sequence=seq,template=row['template'],group=group,hp=hp,maximum_hp=hp)
        else:
            if old is None or not old['active']:return False
            if old['hp']==0:self.retired[old['group']]+=1
            self.actors[actor]=dict(old,active=False,sequence=seq)
        self.motion.pop(actor,None)
        for key in tuple(self.event_versions):
            if actor in key[1:]:self.event_versions.pop(key,None)
        return True

    def health(self,decoded):
        actor=self.actors.get(decoded['target'])
        if actor and actor['active']:
            actor['hp']=f32(min(actor['maximum_hp'],max(0,actor['hp']-decoded['signed_hp_amount'])))

    def player_position(self,uid,decoded,payload):
        sequence=decoded['sequence_15_raw'];raw=payload[:19]+payload[23:];old=self.player_motion.get(uid)
        if old and (sequence<old[0] or (sequence==old[0] and raw==old[1])):return
        self.player_motion[uid]=(sequence,raw);self.trigger(decoded['position'])


def observe_combat(hub,engine,c,message):
    from .engine import Phase
    from .pve import relay_actor
    room=engine.room
    if (room is None or room.request[46]!=10 or not isinstance(room.pve,FosterBattle) or
            room.stage!='battle' or c is None or c is not engine.game or c.phase!=Phase.BATTLE):return
    decoded=decode_battle(message.payload)
    if decoded is None:return
    if decoded['id']==8120 and decoded['sender']==c.uid and c.uid in room.fighters and (decoded['flag'],decoded['header_variant'])==(0,0):
        room.pve.player_position(c.uid,decoded,message.payload)
    elif decoded['id'] in (8120,8121):relay_actor(hub,engine,c,decoded,message.payload,observed=True)
