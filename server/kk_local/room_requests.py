"""RoomRequests: local service policy; RoomHub remains the only room state owner."""
import struct
from . import packets
from .wire import Message, ProtocolError
from .chat import system_notice
from .team_series import SeriesProgress


class RoomRequests:
    def __init__(self, hub):
        self.hub = hub

    def handle_invitation(self, engine, c, message):
        """Native3500 invitation,3501 consent and3502 decline; no friend graph."""
        hub = self.hub
        from .engine import Phase
        ident,p=message.id,message.payload
        engine.require(len(p)=={3500:39,3501:18,3502:37}[ident],'room invitation length')
        uid=c.uid
        if c is not engine.game or uid!=engine.account_uid:return []
        if ident==3500:
            sender=struct.unpack_from('<Q',p)[0]
            target,number=struct.unpack_from('<QH',p,29)
            engine.require(sender==uid,'invitation sender spoof')
            room=engine.room;peer=hub.engines.get(target)
            if (c.phase!=Phase.ROOM or room is None or room.stage!='room' or
                    number!=room.number or uid not in room.members or target==uid or
                    peer is None or peer.game is None or peer.game.phase!=Phase.LOBBY or
                    peer.room is not None or peer.delivery_failed or
                    len(room.fighters)>=room.request[37]):
                return [system_notice('[本地服务] 当前不能邀请该玩家进入房间。')]
            old=hub.invites.get(target)
            if old is not None:
                if old['inviter']==uid and old['room'] is room:return []
                return [system_notice('[本地服务] 对方正在处理另一份房间邀请。')]
            # Caller supplied name is never displayed. Use the authenticated
            #account and bind consent to both exact connections + room object.
            name=engine.store.nickname(uid).encode('gbk').ljust(21,b'\0')
            invite=Message(3500,struct.pack('<Q',uid)+name+struct.pack('<QH',target,number))
            hub.queue(peer,invite)
            if peer.delivery_failed:return [system_notice('[本地服务] 对方暂时无法接收房间邀请。')]
            hub.invites[target]=dict(inviter=uid,room=room,serial=room.serial,inviter_connection=c,
                recipient_connection=peer.game,expires=engine.clock()+hub.invite_seconds)
            return []
        inviter=struct.unpack_from('<Q',p)[0]
        target=struct.unpack_from('<Q',p,8 if ident==3501 else 29)[0]
        engine.require(target==uid,'invitation response identity spoof')
        pending=hub.invites.get(uid)
        if (pending is None or pending['inviter']!=inviter or
                pending['recipient_connection'] is not c):return []
        room=pending['room'];owner=hub.engines.get(inviter)
        if (owner is None or owner.game is not pending['inviter_connection'] or
                owner.room is not room or hub.rooms.get(room.number) is not room or
                room.stage!='room' or room.serial!=pending['serial'] or owner.game.phase!=Phase.ROOM or
                c.phase!=Phase.LOBBY or engine.room is not None):
            hub.invites.pop(uid,None)
            return [system_notice('[本地服务] 房间邀请已失效。')]
        if ident==3502:
            hub.invites.pop(uid,None)
            name=engine.store.nickname(uid).encode('gbk').ljust(21,b'\0')
            hub.queue(owner,Message(3502,struct.pack('<Q',inviter)+name+struct.pack('<Q',uid)))
            return []
        if struct.unpack_from('<H',p,16)[0]!=room.number:
            return [system_notice('[本地服务] 邀请房间不匹配。')]
        if (len(room.fighters)>=room.request[37] or
                any(u in hub.suspended or not m.engine.p2p_alive() or not m.engine.p2p.get('bound')
                    for u,m in room.members.items()) or not engine.p2p_alive() or not engine.p2p.get('bound')):
            hub.invites.pop(uid,None)
            return [system_notice('[本地服务] 房间已满或连接尚未就绪。')]
        # Explicit local rule: a current member's authenticated invitation is
        #a one-use grant for this room, including a password-protected room.
        #install prepares every native descriptor before changing membership.
        hub.install(room,engine)
        return engine.take_pending(c)

    def handle(self, engine, c, message):
        from .engine import Phase
        from .rooms import SharedRoom
        hub = self.hub
        ident, p = message.id, message.payload
        uid, room, require = c.uid, engine.room, engine.require
        if ident==2260:
            require(len(p)==3,'room directory length')
            # Native lobby polling can already be queued behind 3010 when
            # 3100 changes the server phase. Ignore this stale query; replying
            # with a lobby list or disconnecting would disrupt room entry.
            if c.phase != Phase.LOBBY:
                return []
            #92DD40: byte0 page, byte1 room-filter toggle, byte2 mode.
            #91B510/4E7280 use136 for all;0 is actual survival Mode0.
            if p[1] not in (0,1):
                engine.record_unknown(c,ident,p);return [packets.room_directory([])]
            candidates=[r for r in sorted(hub.rooms.values(),key=lambda r:r.number)
                        if r.request[46]!=4 and (p[2]==136 or p[2]==r.request[46]) and
                        (p[1]==1 or r.stage=='room')]
            page=max(1,p[0])
            records=[packets.room_list_record(r.number,r.resolved_request or r.request,len(r.fighters),waiting=r.stage=='room',map_catalog=engine.map_catalog,
                       spectators=len(r.members)-len(r.fighters),spectator_capacity=hub.observer_limit(r))
                     for r in candidates[(page-1)*9:page*9]]
            return [packets.room_directory(records)]
        if ident==3010:
            if room and c.phase==Phase.ROOM and p==room.request:
                return []
            require(c.phase==Phase.LOBBY and room is None,'create phase')
            resolved=packets.resolve_room_request(p,engine.map_catalog)
            packets.room_entry(resolved,uid,map_catalog=engine.map_catalog)  # Validate before allocation.
            number=next((i for i in range(1,256) if i not in hub.rooms),None)
            require(number is not None,'room IDs exhausted')
            if hub.team_series_rounds and p[46]==1 and p[37]>6:
                return [packets.room_rejection('series UI limited to six fighters')]
            new=SharedRoom(uid,p,number,resolved_request=resolved,
                           series=SeriesProgress(hub.team_series_rounds) if hub.team_series_rounds and p[46]==1 else None)
            hub.install(new,engine)
            hub.rooms[number]=new
        elif ident==3075:
            require(len(p)==1,'automatic join length')
            if c.phase!=Phase.LOBBY or room is not None:
                return [system_notice('[本地服务] 请先退出当前房间。')]
            #9215B0 sends the same+333 filter byte:136/all or exact mode.
            # Local matching policy chooses the first eligible room ID.
            # Never auto-enter password rooms or incomplete/reconnecting rosters.
            candidates=[r for r in sorted(hub.rooms.values(),key=lambda r:r.number)
                        if r.request[46]!=4 and r.stage=='room' and p[0] in (136,r.request[46])
                        and not r.request[21] and len(r.fighters)<r.request[37]
                        and not any(u in hub.suspended for u in r.members)]
            target=next((r for r in candidates if all(m.engine.p2p_alive() and m.engine.p2p.get('bound') for m in r.members.values())),None)
            if target is None or not engine.p2p_alive() or not engine.p2p.get('bound'):
                return [system_notice('[本地服务] 暂无可自动加入的房间。')]
            hub.install(target,engine)
        elif ident==3070:
            require(len(p)==14,'join length')
            number=struct.unpack_from('<H',p)[0]
            if c.phase==Phase.ROOM and room and room.number==number:
                return []  # Do not create the same native player twice.
            require(c.phase==Phase.LOBBY and room is None,'join phase')
            target=hub.rooms.get(number)
            # 824CB0 consumes request14+error32 using the recovered130-row
            # DBC96C error table. Priority here remains explicit local policy.
            error=None
            if p[2] not in (0,1) or (p[2] and hub.spectator_capacity==0):error=130
            elif target is None:error=29
            elif p[2] and not hub.observer_limit(target):error=130
            elif target.stage!='room':error=30
            elif (len(target.members)-len(target.fighters)>=hub.observer_limit(target) if p[2] else len(target.fighters)>=target.request[37]):error=32
            elif any(u in hub.suspended for u in target.members):error=130
            elif p[3:14].split(b'\0',1)[0]!=target.request[21:32].split(b'\0',1)[0]:error=31
            if error is not None:
                return [Message(3080,p+struct.pack('<I',error))]
            hub.install(target,engine,spectator=bool(p[2]))
        elif ident==3200:
            require(len(p)==48,'room settings length')
            if c.phase!=Phase.ROOM or room is None or room.stage!='room' or room.owner!=uid:
                return [system_notice('[本地服务] 只有等待房间的房主可以修改设置。')]
            try:updated,reply=packets.update_room_request(room.resolved_request or room.request,p,map_catalog=engine.map_catalog)
            except (ValueError,ProtocolError):return [system_notice('[本地服务] 房间设置无效或地图不可用。')]
            if room.series and updated[37]>6:return [system_notice('[本地服务] 多回合结果界面最多六名玩家。')]
            if updated==room.resolved_request:return []
            room.request=room.resolved_request=updated
            hub.broadcast(room,reply)
            for member_uid,member in room.members.items():
                if member.ready:
                    member.ready=False
                    hub.broadcast(room,Message(4070,struct.pack('<Q',member_uid)))
        elif ident==3230:
            require(len(p)==1,'team change length')
            if c.phase!=Phase.ROOM or room is None or room.stage!='room' or p[0] not in (0,1):
                return [system_notice('[本地服务] 当前不能切换队伍。')]
            member=room.members[uid]
            if member.spectator:return []
            if member.team==p[0]:return []
            key=hub.position_key(room,p[0],member.slot,exclude=uid)
            if key is None:return [system_notice('[本地服务] 目标队伍已满。')]
            member.team=p[0]
            member.registry_key=key
            #820160 writes key+9, team+8, then rebuilds position/UI from key.
            hub.broadcast(room,Message(3250,struct.pack('<QBB',uid,member.team,key)))
            for member_uid,other in room.members.items():
                if other.ready:
                    other.ready=False
                    hub.broadcast(room,Message(4070,struct.pack('<Q',member_uid)))
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
            hub.queue(removed,notice)
            hub.leave(removed,departure=notice)
        elif ident==4051:
            require(len(p)==12,'owner transfer length')
            if c.phase!=Phase.ROOM:return []  #4052 is a room-selector consumer.
            number,target=struct.unpack('<IQ',p)
            #805D70: room number32 + selected identity64. 81FC30 consumes
            #4052/error16;3160 is the separate native owner update. Error
            #selection, readiness reset and admission are local service policy.
            if room is None or number!=room.number:
                return [Message(4052,struct.pack('<H',29))]
            if room.stage!='room':
                return [Message(4052,struct.pack('<H',30))]
            if room.owner!=uid or target==uid:
                return [Message(4052,struct.pack('<H',129))]
            new_owner=room.members.get(target)
            if (new_owner is None or new_owner.spectator or target in hub.suspended or
                    new_owner.engine.game is None or not new_owner.engine.p2p_alive() or
                    not new_owner.engine.p2p.get('bound')):
                return [Message(4052,struct.pack('<H',34))]
            room.owner=target
            for member_uid,member in room.members.items():
                if member.ready:
                    member.ready=False
                    hub.broadcast(room,Message(4070,struct.pack('<Q',member_uid)))
            hub.broadcast(room,Message(3160,struct.pack('<Q',target)))
            hub.queue(engine,Message(4052,struct.pack('<H',0)))
        elif ident==4031:
            require(not p,'waiting timeout request length')
            #8035C0 timer expiry ->4031;820D00 consumes the departing UID.
            # Local acceptance retires only the authenticated requester. It
            # does not let a client choose another member or abort a new round.
            if c.phase!=Phase.ROOM or room is None or room.stage!='room':return []
            notice=Message(4032,struct.pack('<Q',uid))
            hub.queue(engine,notice)
            hub.leave(engine,departure=notice)
        elif ident==3110:
            require(not p,'leave length')
            if room is None and c.phase==Phase.LOBBY:
                return []
            require(room is not None,'leave outside room')
            hub.leave(engine,acknowledge=True)
        else:
            return None
        return engine.take_pending(c)
