"""Opt-in native Mode4/1201 private tutorial and completion transaction."""
import struct
from .wire import Message
from .chat import system_notice
from . import packets,title_rewards


def requested(p):return len(p)==81 and p[46]==4 and p[37]==1 and struct.unpack_from('<II',p,38)==(1201,1201)


def snapshot(engine,room):
    _,name,profile,inventory=engine.store.snapshot(room.owner)
    own=packets.fighter_snapshot(room.owner,name,profile,inventory,0,0,0)
    return packets.room_entry_member(room.request,room.owner,room.number,0,0,own,map_catalog=engine.map_catalog)


def complete(hub,engine,c):
    store=engine.store;room=engine.room;allowed=getattr(engine.map_catalog,'title_levels',())
    old_binding=engine.title_offer
    try:
        with store.transaction('nested tutorial completion'):
            if not store.titles.tutorial_completed(c.uid):
                title_rewards.issue_locked(store,c.uid,2,allowed,'tutorial_4124')
                store.titles.record_tutorial(c.uid,room.number,room.serial)
            replies=title_rewards.announce(engine,c,sync_empty=True)
    except BaseException:
        engine.title_offer=old_binding;raise
    #829B50 updates profile+123 before3115 re-enters924010's guide gate.
    hub.leave(engine,acknowledge=True)
    return replies+engine.take_pending(c)


def handle(hub,engine,c,message):
    from .engine import Phase
    from .rooms import SharedRoom,Member
    ident,p=message.id,message.payload;room=engine.room
    enabled=bool(getattr(engine.map_catalog,'tutorial_enabled',False))
    if c is not engine.game:return None
    if ident==3010 and len(p)==81 and p[46]==4:
        if not enabled:return [packets.room_rejection('tutorial disabled')]
        if room is not None:
            if room.request==p:return []
            return [packets.room_rejection('already in room')]
        if c.phase!=Phase.LOBBY or not requested(p):return [packets.room_rejection('tutorial shape')]
        if title_rewards.profile_title(engine.store,c.uid)>=2:
            try:return title_rewards.announce(engine,c,sync_empty=True)+[Message(3115)]
            except ValueError:return [system_notice('[本地服务] 教学已完成，奖励目录暂不可用。')]
        resolved=packets.resolve_room_request(p,engine.map_catalog)
        number=next((i for i in range(1,256) if i not in hub.rooms),None)
        if number is None:return [packets.room_rejection('room IDs exhausted')]
        new=SharedRoom(c.uid,bytes(p),number,resolved_request=resolved)
        new.members[c.uid]=Member(engine,0,0,registry_key=0);new.tutorial_pending=True
        snapshot(engine,new)  # validate complete profile/appearance before membership
        engine.room=new;c.phase=Phase.ROOM;hub.rooms[number]=new
        return [Message(3020,struct.pack('<H',number)+new.request)]
    if ident==4124:
        engine.require(not p,'tutorial completion empty')
        if not enabled:return []
        if room is None:
            if c.phase==Phase.LOBBY and engine.store.titles.tutorial_completed(c.uid):
                try:return title_rewards.announce(engine,c,sync_empty=True)+[Message(3115)]
                except ValueError:return []
            return []
        if (not requested(room.request) or room.stage!='battle' or c.phase!=Phase.BATTLE or
                room.owner!=c.uid or set(room.members)!={c.uid} or room.input_ready!={c.uid}):return []
        try:return complete(hub,engine,c)
        except ValueError:return [system_notice('[本地服务] 教学完成尚未提交，请检查奖励配置后重试。')]
    if room is None or room.request[46]!=4:return None
    if ident==3070:
        engine.require(len(p)==14,'tutorial join exact14')
        if c.uid!=room.owner or struct.unpack_from('<H',p)[0]!=room.number or any(p[2:]):return []
        if not room.tutorial_pending:return []
        reply=snapshot(engine,room);room.tutorial_pending=False
        return [reply,Message(3160,struct.pack('<Q',room.owner))]
    if ident in (4030,4060):
        engine.require(not p,'tutorial ready empty')
        if room.tutorial_pending or room.stage!='room' or c.phase!=Phase.ROOM:return []
        if ident==4060:return []
        room.members[c.uid].ready=True;hub.queue(engine,Message(4050,struct.pack('<Q',c.uid)))
        hub.start_match(room)  # private guide has no P2P registration or4150 probe
        return engine.take_pending(c)
    if ident in (3091,3200,3230,4051,3260,3262,3263,3500,3501,3502):
        return [system_notice('[本地服务] 教学房间是私人单人房间。')]
    return None
