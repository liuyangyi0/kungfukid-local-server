"""Provisional shared room coordinator; no public authentication or combat authority.

Each explicitly configured offline account owns an Engine and endpoint set.
The hub owns rooms, readiness barriers and recipient selection, never client HP.
"""
from dataclasses import dataclass, field
import struct
import time

from . import packets
from .wire import Message, ProtocolError
from .layouts import decode_battle
from .chat import system_notice


@dataclass
class Member:
    engine: object
    slot: int
    team: int
    ready: bool = False


@dataclass
class SharedRoom:
    owner: int
    request: bytes
    number: int
    serial: int = 0
    stage: str = 'room'
    members: dict = field(default_factory=dict)
    loaded: set = field(default_factory=set)
    input_ready: set = field(default_factory=set)
    last_sequence: dict = field(default_factory=dict)
    resolved_request: bytes | None = None


class RoomHub:
    def __init__(self):
        self.engines = {}
        self.rooms = {}
        self.next_p2p_id = 1001
        self.suspended = {}
        self.reconnect_seconds = 30  # Explicit local policy, waiting rooms only.

    def attach(self, engine):
        if engine.account_uid in self.engines or len(self.engines)>=8:
            raise ValueError('duplicate or excessive offline account endpoints')
        if self.engines and next(iter(self.engines.values())).store is not engine.store:
            raise ValueError('shared rooms require one transactional Store')
        self.engines[engine.account_uid]=engine

    def allocate_p2p_id(self):
        if self.next_p2p_id>=0xffffffff:
            raise ProtocolError('P2P identifier exhausted')
        result=self.next_p2p_id
        self.next_p2p_id+=1
        return result

    @staticmethod
    def queue(engine, message):
        if engine.game is not None:
            engine.enqueue(message)

    def broadcast(self, room, message, *, exclude=None):
        for uid,member in room.members.items():
            if uid!=exclude:
                self.queue(member.engine,message)

    @staticmethod
    def fighter(uid, member, *, update=False):
        e=member.engine
        _,name,profile,inventory=e.store.snapshot(uid)
        if not e.p2p_alive() or not e.p2p.get('bound'):
            raise ProtocolError('member P2P lease unavailable')
        return packets.fighter_snapshot(uid,name,profile,inventory,member.slot,member.team,
                                        e.p2p['player'],ready=member.ready,update=update)

    def install(self, room, engine):
        """Admit only after preparing every native record; no partial membership."""
        from .engine import Phase
        uid=engine.account_uid
        if room.stage!='room' or len(room.members)>=room.request[37]:
            raise ProtocolError('room is full or already loading')
        slot=next(i for i in range(8) if i not in {m.slot for m in room.members.values()})
        member=Member(engine,slot,slot%2)
        own=self.fighter(uid,member)
        peers=[(u,m,self.fighter(u,m)) for u,m in room.members.items()]
        entry=packets.room_entry_member(room.resolved_request or room.request,room.owner,room.number,slot,member.team,own,map_catalog=engine.map_catalog)
        room.members[uid]=member
        engine.room=room
        engine.game.phase=Phase.ROOM
        self.queue(engine,entry)
        self.queue(engine,Message(3160,struct.pack('<Q',room.owner)))
        for peer_uid,peer,raw in peers:
            self.queue(engine,Message(3090,raw))
            self.queue(peer.engine,Message(3090,own))
        for peer_uid,peer,_ in peers:
            if peer.ready:
                peer.ready=False
                self.broadcast(room,Message(4070,struct.pack('<Q',peer_uid)))

    def equipment_changed(self, engine):
        room=engine.room
        if room and room.stage=='room':
            raw=self.fighter(engine.account_uid,room.members[engine.account_uid],update=True)
            self.broadcast(room,Message(3090,raw),exclude=engine.account_uid)

    def disconnected(self, engine):
        room=engine.room
        if room is None or room.stage!='room':
            self.leave(engine)
            return
        uid=engine.account_uid
        room.members[uid].ready=False
        self.suspended[uid]=engine.clock()+self.reconnect_seconds
        self.broadcast(room,Message(4070,struct.pack('<Q',uid)),exclude=uid)

    def expire(self):
        for uid,deadline in list(self.suspended.items()):
            engine=self.engines[uid]
            if engine.clock()>=deadline:
                self.suspended.pop(uid,None)
                self.leave(engine)

    def restore_bound(self, engine):
        from .engine import Phase
        self.expire()
        uid=engine.account_uid
        if uid not in self.suspended:
            return []
        room=engine.room
        if room is None or room.stage!='room':
            return []
        member=room.members[uid]
        own=self.fighter(uid,member)
        peers=[(u,m,self.fighter(u,m)) for u,m in room.members.items() if u!=uid and u not in self.suspended]
        entry=packets.room_entry_member(room.resolved_request or room.request,room.owner,room.number,member.slot,member.team,own,map_catalog=engine.map_catalog)
        self.suspended.pop(uid,None)
        engine.game.phase=Phase.ROOM
        self.queue(engine,entry)
        self.queue(engine,Message(3160,struct.pack('<Q',room.owner)))
        for peer_uid,peer,raw in peers:
            self.queue(engine,Message(3090,raw))
            self.queue(peer.engine,Message(3090,self.fighter(uid,member,update=True)))
        return engine.take_pending(engine.game)

    def leave(self, engine, *, acknowledge=False, departure=None):
        from .engine import Phase
        room=engine.room
        if room is None:
            return
        uid=engine.account_uid
        self.suspended.pop(uid,None)
        room.members.pop(uid,None)
        room.loaded.discard(uid)
        room.input_ready.discard(uid)
        room.last_sequence.pop(uid,None)
        engine.room=None
        engine.consume_intents.clear()
        if engine.game is not None:
            engine.game.phase=Phase.LOBBY
        if acknowledge:
            self.queue(engine,Message(3115))
        if not room.members:
            self.rooms.pop(room.number,None)
            return
        if room.stage!='room':
            # A running native match cannot be restored from just a roster.
            # Abort cleanly, without a fabricated result or reward. Re-entry
            # starts a fresh match; hot reconnect semantics remain unqualified.
            for other in list(room.members.values()):
                other.engine.room=None
                other.engine.consume_intents.clear()
                if other.engine.game:
                    other.engine.game.phase=Phase.LOBBY
                self.queue(other.engine,Message(3115))
            room.members.clear()
            self.rooms.pop(room.number,None)
            return
        self.broadcast(room,departure or Message(3130,struct.pack('<Q',uid)))
        if room.owner==uid:
            room.owner=min(room.members,key=lambda u:room.members[u].slot)
            self.broadcast(room,Message(3160,struct.pack('<Q',room.owner)))
        # Composition changed: everyone must confirm readiness again.
        for other_uid,other in room.members.items():
            if other.ready:
                other.ready=False
                self.broadcast(room,Message(4070,struct.pack('<Q',other_uid)))

    def handle(self, engine, c, message):
        from .engine import Phase
        ident,p=message.id,message.payload
        self.expire()
        if ident not in (2260,3010,3070,3075,3110,3140,3200,3230,4030,4060,4160,8040,8071):
            return None
        uid=c.uid
        room=engine.room
        require=engine.require
        if ident==2260:
            require(c.phase==Phase.LOBBY and len(p)==3,'room directory phase/length')
            # Native pages have9 rows. Client's mode filter/page semantics
            # are preserved as a candidate: zero/all and one-based page.
            candidates=[r for r in sorted(self.rooms.values(),key=lambda r:r.number)
                        if p[0] in (0,r.request[46])]
            page=max(1,p[1])
            records=[packets.room_list_record(r.number,r.resolved_request or r.request,len(r.members),waiting=r.stage=='room',map_catalog=engine.map_catalog)
                     for r in candidates[(page-1)*9:page*9]]
            return [packets.room_directory(records)]
        if ident==3010:
            if room and c.phase==Phase.ROOM and p==room.request:
                return []
            require(c.phase==Phase.LOBBY and room is None,'create phase')
            resolved=packets.resolve_room_request(p,engine.map_catalog)
            packets.room_entry(resolved,uid,map_catalog=engine.map_catalog)  # Validate before allocation.
            number=next((i for i in range(1,256) if i not in self.rooms),None)
            require(number is not None,'room IDs exhausted')
            new=SharedRoom(uid,p,number,resolved_request=resolved)
            self.install(new,engine)
            self.rooms[number]=new
        elif ident==3075:
            require(len(p)==1,'automatic join length')
            if c.phase!=Phase.LOBBY or room is not None:
                return [system_notice('[本地服务] 请先退出当前房间。')]
            # Local matching policy: zero/all or exact mode, first eligible ID.
            # Never auto-enter password rooms or incomplete/reconnecting rosters.
            candidates=[r for r in sorted(self.rooms.values(),key=lambda r:r.number)
                        if r.stage=='room' and p[0] in (0,r.request[46])
                        and not r.request[21] and len(r.members)<r.request[37]
                        and not any(u in self.suspended for u in r.members)]
            target=next((r for r in candidates if all(m.engine.p2p_alive() and m.engine.p2p.get('bound') for m in r.members.values())),None)
            if target is None or not engine.p2p_alive() or not engine.p2p.get('bound'):
                return [system_notice('[本地服务] 暂无可自动加入的房间。')]
            self.install(target,engine)
        elif ident==3070:
            require(len(p)==14,'join length')
            number=struct.unpack_from('<H',p)[0]
            if c.phase==Phase.ROOM and room and room.number==number:
                return []  # Do not create the same native player twice.
            require(c.phase==Phase.LOBBY and room is None,'join phase')
            target=self.rooms.get(number)
            # 824CB0 consumes request14+error32 using the recovered130-row
            # DBC96C error table. Priority here remains explicit local policy.
            error=None
            if p[2]!=0:error=130  # spectator admission remains unqualified
            elif target is None:error=29
            elif target.stage!='room':error=30
            elif len(target.members)>=target.request[37]:error=32
            elif any(u in self.suspended for u in target.members):error=130
            elif p[3:14].split(b'\0',1)[0]!=target.request[21:32].split(b'\0',1)[0]:error=31
            if error is not None:
                return [Message(3080,p+struct.pack('<I',error))]
            self.install(target,engine)
        elif ident==3200:
            require(len(p)==48,'room settings length')
            if c.phase!=Phase.ROOM or room is None or room.stage!='room' or room.owner!=uid:
                return [system_notice('[本地服务] 只有等待房间的房主可以修改设置。')]
            try:updated,reply=packets.update_room_request(room.resolved_request or room.request,p,map_catalog=engine.map_catalog)
            except (ValueError,ProtocolError):return [system_notice('[本地服务] 房间设置无效或地图不可用。')]
            if updated==room.resolved_request:return []
            room.request=room.resolved_request=updated
            self.broadcast(room,reply)
            for member_uid,member in room.members.items():
                if member.ready:
                    member.ready=False
                    self.broadcast(room,Message(4070,struct.pack('<Q',member_uid)))
        elif ident==3230:
            require(len(p)==1,'team change length')
            if c.phase!=Phase.ROOM or room is None or room.stage!='room' or p[0] not in (0,1):
                return [system_notice('[本地服务] 当前不能切换队伍。')]
            member=room.members[uid]
            if member.team==p[0]:return []
            member.team=p[0]
            # 3250: UID, team, registry slot. Preserve the existing unique slot.
            self.broadcast(room,Message(3250,struct.pack('<QBB',uid,member.team,member.slot)))
            for member_uid,other in room.members.items():
                if other.ready:
                    other.ready=False
                    self.broadcast(room,Message(4070,struct.pack('<Q',member_uid)))
        elif ident==3140:
            require(len(p)==9,'room removal length')
            target=struct.unpack_from('<Q',p)[0]
            # Only ordinary owner removal is locally authorized. The extra
            # privilege flag is not a license to remove anyone outside this room.
            if (c.phase!=Phase.ROOM or room is None or room.stage!='room' or
                    room.owner!=uid or target==uid or target not in room.members or p[8]!=0):
                return [system_notice('[本地服务] 当前不能移除此房间成员。')]
            removed=room.members[target].engine
            notice=Message(3150,struct.pack('<QB',target,0))
            self.queue(removed,notice)
            self.leave(removed,departure=notice)
        elif ident==3110:
            require(not p,'leave length')
            if room is None and c.phase==Phase.LOBBY:
                return []
            require(room is not None,'leave outside room')
            self.leave(engine,acknowledge=True)
        elif ident in (4030,4060):
            require(not p and room is not None,'ready context/length')
            if room.stage!='room':
                return []
            member=room.members[uid]
            if ident==4060:
                if member.ready:
                    member.ready=False
                    self.broadcast(room,Message(4070,struct.pack('<Q',uid)))
            else:
                require(engine.p2p_alive(),'ready P2P expired')
                if uid==room.owner and room.request[46] in packets.COMPETITIVE_MODES:
                    if len(room.members)<2:
                        return []
                    if room.request[46] in packets.TEAM_MODES and len({m.team for m in room.members.values()})<2:
                        return []
                if uid==room.owner and not all(m.ready and u not in self.suspended for u,m in room.members.items() if u!=uid):
                    return []  # Do not lock the host ready before others are ready.
                if not member.ready:
                    member.ready=True
                    self.broadcast(room,Message(4050,struct.pack('<Q',uid)))
                # Non-owners can ready; only a host request can start the match.
                if uid==room.owner and all(m.ready and m.engine.p2p_alive() for m in room.members.values()):
                    room.serial=engine.store.next_battle()
                    room.stage='loading'
                    values={m.slot:m.engine.p2p['player'] for m in room.members.values()}
                    for m in room.members.values():
                        m.engine.game.phase=Phase.LOADING
                        m.engine.consume_intents.clear()
                        self.queue(m.engine,packets.battle_start_members(room.number,room.serial,m.slot,values))
        elif ident==4160:
            require(not p and room is not None,'load length/context')
            if uid in room.loaded:
                return []
            require(room.stage=='loading' and c.phase==Phase.LOADING,'load phase')
            room.loaded.add(uid)
            self.broadcast(room,Message(4170,struct.pack('<Q',uid)))
            if room.loaded==set(room.members):
                room.stage='wait_ready'
                for m in room.members.values():
                    m.engine.game.phase=Phase.WAIT_READY
                self.broadcast(room,Message(4180))
        elif ident==8040:
            require(len(p)==14 and room is not None,'input ready shape/context')
            number,who=struct.unpack_from('<HQ',p)
            require(number==room.number and who==uid,'input ready identity')
            if uid in room.input_ready:
                return []
            require(room.stage=='wait_ready' and c.phase==Phase.WAIT_READY,'input ready phase')
            room.input_ready.add(uid)
            if room.input_ready==set(room.members):
                room.stage='battle'
                for m in room.members.values():
                    m.engine.game.phase=Phase.BATTLE
                self.broadcast(room,Message(8070,packets.battle_ready(room.number,room.serial)))
        elif ident==8071:
            if room is None or c.phase!=Phase.BATTLE:
                return []
            decoded=decode_battle(p)
            # Only qualified state/attack/hit layouts. This is a trusted local
            # relay, not authoritative movement, damage or result validation.
            if decoded is None or decoded['id'] not in (0x1fb8,0x1fcc,0x1fd6):
                engine.record_unknown(c,ident,p)
                return []
            require(decoded['sender']==uid,'relay sender spoof')
            actor=decoded.get('attacker',decoded.get('player',uid))
            require(actor==uid,'relay actor spoof')
            if 'room_pair' in decoded:
                require(decoded['room_pair']==(room.number,room.serial),'relay stale battle')
            sequence=struct.unpack_from('<I',p,19)[0]
            if sequence<=room.last_sequence.get(uid,-1):
                return []
            room.last_sequence[uid]=sequence
            self.broadcast(room,Message(8071,p),exclude=uid)
        return engine.take_pending(c)
