"""Mode21 native lifecycle/wave coordinator, no server combat simulation.

The elected native Host supplies observations. Matching a spawn/removal/quota
is not proof of a legitimate kill; no persistent awards are granted here.
"""
from collections import Counter
from dataclasses import dataclass,field
import struct
from .layouts import decode_battle,finite
from .wire import Message,ProtocolError
from .lab_settlement import decode_report

LENGTHS={20400:67,20401:47,20403:92,20404:43,20405:183,20407:47}


def decode(payload):
    p=bytes(payload)
    if len(p)<39:raise ProtocolError('PVE event header')
    ident,sender=struct.unpack_from('<IQ',p)
    if ident not in LENGTHS or len(p)!=LENGTHS[ident] or p[12:14]!=b'\1\1':raise ProtocolError('PVE event shape')
    row=dict(id=ident,sender=sender,sequence=struct.unpack_from('<I',p,19)[0],raw=p)
    if ident in (20400,20401):
        row['actor']=struct.unpack_from('<Q',p,39)[0]
        if ident==20400:
            row.update(template=struct.unpack_from('<I',p,47)[0],position=finite(struct.unpack_from('<fff',p,51)),
                       direction_raw=struct.unpack_from('<I',p,63)[0])
    elif ident==20403:
        if p[39]>1:raise ProtocolError('PVE block flag')
        row.update(key=struct.unpack_from('<I',p,40)[0],flag=p[39],corners=finite(struct.unpack_from('<12f',p,44)))
    elif ident==20404:row['key']=struct.unpack_from('<I',p,39)[0]
    elif ident==20407:row['room_pair']=struct.unpack_from('<II',p,39)
    return row


@dataclass
class StageBattle:
    waves: tuple
    index: int = 0
    finished: bool = False
    spawned: Counter = field(default_factory=Counter)
    actors: dict = field(default_factory=dict)
    blocks: dict = field(default_factory=dict)
    motion: dict = field(default_factory=dict)
    event_versions: dict = field(default_factory=dict)

    def active(self,uid):return uid in self.actors and self.actors[uid]['active']

    def actor_event(self,row,players):
        actor=row['actor'];sequence=row['sequence'];old=self.actors.get(actor)
        if not actor or actor in players:return False
        if old and sequence<=old['sequence']:return False
        if row['id']==20400:
            if self.finished or (old and old['active']) or (old is None and len(self.actors)>=100):return False
            template=row['template'];limit=self.waves[self.index].get(template,0)
            if self.spawned[template]>=limit:return False
            self.spawned[template]+=1
            self.actors[actor]=dict(active=True,sequence=sequence,template=template)
        else:
            if old is None or not old['active']:return False
            self.actors[actor]=dict(old,active=False,sequence=sequence)
        self.motion.pop(actor,None)
        for key in tuple(self.event_versions):
            if actor in key[1:]:self.event_versions.pop(key,None)
        return True

    def block_event(self,row):
        key=row['key'];old=self.blocks.get(key)
        if old and row['sequence']<=old[0]:return False
        if old is None and len(self.blocks)>=1024:return False
        self.blocks[key]=(row['sequence'],row['raw'] if row['id']==20403 else None)
        return True

    def report_wave(self,pair,payload):
        if len(payload)!=40:raise ProtocolError('20571 exact40')
        room,serial,wave,value=struct.unpack_from('<IIiI',payload)
        if (room,serial)!=pair or self.finished or wave!=self.index+1 or value!=1:return None
        if any(a['active'] for a in self.actors.values()) or dict(self.spawned)!=self.waves[self.index]:return None
        self.index+=1;self.spawned.clear()
        self.finished=self.index==len(self.waves)
        p=bytearray(40);struct.pack_into('<i',p,8,-1 if self.finished else self.index+1)
        return Message(20572,bytes(p))


def initial_blocks(hub,room):
    if room.request[46]==10 and room.pve.positions is not None:
        hub.broadcast(room,Message(8071,room.pve.positions),exclude=room.owner)
    for key in sorted(room.pve.blocks):
        payload=room.pve.blocks[key][1]
        if payload is not None:hub.broadcast(room,Message(8071,payload),exclude=room.owner)


def lifecycle(hub,engine,c,message,*,observed=False):
    from .engine import Phase
    room=engine.room
    if room is None or room.pve is None or room.request[46] not in (10,21):
        engine.record_unknown(c,message.id,message.payload);return []
    if c is None or c is not engine.game or c.uid!=room.owner or room.members[c.uid].engine is not engine:return []
    if message.id==20571:
        if room.request[46]!=21 or room.stage!='battle' or c.phase!=Phase.BATTLE:return []
        out=room.pve.report_wave((room.number,room.serial),message.payload)
        if out:hub.broadcast(room,out)
        return engine.take_pending(c)
    row=decode(message.payload)
    engine.require(row['sender']==c.uid,'PVE lifecycle sender spoof')
    loading=(room.stage=='loading' and c.phase==Phase.LOADING and c.uid not in room.loaded)
    battle=(room.stage=='battle' and c.phase==Phase.BATTLE)
    if row['id']==20405:
        if room.request[46]==10 and loading:room.pve.receive_positions(message.payload,room.fighters)
    elif row['id'] in (20403,20404):
        if not loading and not battle:return []
        accepted=room.pve.block_event(row)
        if accepted and battle and not observed:hub.broadcast(room,message,exclude=c.uid)
    elif battle and row['id'] in (20400,20401):
        if room.pve.actor_event(row,room.members) and not observed:hub.broadcast(room,message,exclude=c.uid)
    elif row['id']==20407:
        #827EF0 only casts CFosterMode(10), not CStageAssaultMode(21).
        #Record an observation, not a fake all-PVE finish/award broadcast.
        if row['room_pair']!=(room.number,room.serial):return []
        if room.request[46]==10 and battle:
            if row['sequence']>room.pve.finish_sequence and not room.pve.finished:
                room.pve.finish_sequence=row['sequence'];room.pve.finished=True
                if not observed:hub.broadcast(room,message,exclude=c.uid)
        else:engine.record_unknown(c,8071,message.payload)
    return [] if observed else engine.take_pending(c)


def relay_actor(hub,engine,c,decoded,payload,*,observed=False):
    """Return None for ordinary-player paths; only the Host owns live NPCs."""
    room=engine.room
    if room.pve is None:return None
    ident=decoded['id'];state=room.pve
    field={8120:'sender',8121:'target',8122:'player',8125:'player',8127:'player',8140:'attacker',8150:'target'}.get(ident)
    if field is None:return None
    actor=decoded.get(field)
    if actor in room.members:return None
    if c.uid!=room.owner or not state.active(actor):return []
    if ident!=8120 and decoded['sender']!=c.uid:raise ProtocolError('NPC transport sender spoof')
    if 'room_pair' in decoded and decoded['room_pair']!=(room.number,room.serial):return []
    if ident==8122 and decoded['direction_raw']>7:return []
    if ident==8150 and (decoded.get('operation') not in ('apply','cancel') or decoded['ustate_code']>255):return []
    if ident in (8121,8150):
        source=decoded['source']
        if source and source not in room.members and not state.active(source):return []
    header=(decoded['flag'],decoded['header_variant'])
    if ident==8120:
        if header!=(0,0):return []
        sequence=decoded['sequence_15_raw'];raw=payload[:19]+payload[23:]
        old=state.motion.get(actor)
        if old and (sequence<old[0] or (sequence==old[0] and raw==old[1])):return []
        state.motion[actor]=(sequence,raw)
    else:
        if header!=(1,1):return []
        sequence=decoded['sequence_19_raw'];key=(c.uid,actor)
        if sequence<=max(state.event_versions.get(key,-1),state.actors[actor]['sequence']):return []
        state.event_versions[key]=sequence
        if room.request[46]==10 and ident==8121:state.health(decoded)
    if not observed:hub.broadcast(room,Message(8071,payload),exclude=c.uid)
    return [] if observed else engine.take_pending(c)


def result(hub,engine,c,message):
    room=engine.room
    if room is None or room.pve is None or c is not engine.game:return []
    if message.id==4115:
        engine.require(len(message.payload)==4,'PVE result acknowledgement length')
        if c.uid in room.result_replies:
            room.result_acks.add(c.uid);hub.advance_barriers(room)
        return engine.take_pending(c)
    if c.uid!=room.owner or room.stage not in ('battle','result'):return []
    report=decode_report(message.payload,{m.slot:u for u,m in room.fighters.items()},room.number,room.serial)
    if c.uid in room.result_reports:
        engine.require(room.result_reports[c.uid]==report,'changed PVE finish report');return []
    reasons={r.result_parameter for r in report.values()}
    clear=reasons=={1} and (room.pve.complete() if room.request[46]==10 else
                          room.pve.finished and not any(a['active'] for a in room.pve.actors.values()))
    failed=reasons=={2} and not room.pve.finished and all(r.hp==0 for r in report.values())
    if not clear and not failed:
        engine.record_unknown(c,4110,message.payload);return []
    now=engine.clock();origin=room.battle_clock_origin
    elapsed=min(0x7fffffff,max(0,int(now-(now if origin is None else origin))))
    replies={}
    for recipient in room.members:
        parts=[]
        for uid in sorted(room.fighters,key=lambda u:(u==recipient,u)):
            profile=engine.store.snapshot(uid)[2]
            if len(profile)!=360:raise ProtocolError('PVE profile shape')
            row=bytearray(500);struct.pack_into('<Q',row,0,uid);row[10]=1 if clear else 2
            row[14:16]=profile[120:122];row[16:20]=profile[245:249]
            struct.pack_into('<I',row,87,0xffffffff)  #hide unqualified extra score
            if room.request[46]==21:
                struct.pack_into('<III',row,92,room.pve.index,elapsed,0)  #mode21 only
            row[140:]=profile;parts.append(bytes(row))
        replies[recipient]=Message(4120,b''.join(parts))
    room.result_reports[c.uid]=report;room.result_replies=replies;room.stage='result'
    for uid,reply in replies.items():hub.queue(room.members[uid].engine,reply)
    return engine.take_pending(c)
