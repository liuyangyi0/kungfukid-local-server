"""Legacy single-endpoint room fallback; runs only after RoomHub declined."""
import struct
from .. import packets
from ..wire import Message, ProtocolError
from ..chat import system_notice


from . import dispatch, equipment, training


def handle(engine, c, message):
    from ..engine import Phase, Room
    ident, p = message.id, message.payload
    if ident==3075:
        engine.require(len(p)==1,'automatic join length')
        return [system_notice('[本地服务] 暂无其他玩家创建的可加入房间。')]
    if ident==3550:
        engine.require(len(p)==12 and struct.unpack_from('<Q',p)[0]==c.uid,
                     'room activity identity/length')
        if struct.unpack_from('<I',p,8)[0] not in (0,1,2,3):
            engine.record_unknown(c,ident,p)
        return []  # Native sender already updated itself; no peer here.
    if ident in (3500,3501,3502):
        engine.require(len(p)=={3500:39,3501:18,3502:37}[ident],'room invitation length')
        return [system_notice('[本地服务] 当前端点没有其他在线玩家可邀请。')]
    if ident==3140:
        engine.require(len(p)==9,'room removal length')
        return [system_notice('[本地服务] 当前房间没有可移除的其他成员。')]
    if ident==4051:
        engine.require(len(p)==12,'owner transfer length')
        if c.phase!=Phase.ROOM:return []
        number,target=struct.unpack('<IQ',p)
        # No peer exists in the single-endpoint adapter. Still release
        # the native transfer UI via its real error reply, never success.
        error=29 if engine.room is None or number!=engine.room.number else 34
        if engine.room is not None and engine.room.owner==target:error=129
        return [Message(4052,struct.pack('<H',error))]
    if ident==3200:
        engine.require(len(p)==48,'room settings length')
        if c.phase!=Phase.ROOM or engine.room is None or engine.room.owner!=c.uid:
            return [system_notice('[本地服务] 当前不能修改房间设置。')]
        try:updated,reply=packets.update_room_request(engine.room.resolved_request or engine.room.request,p,map_catalog=engine.map_catalog)
        except (ValueError,ProtocolError):return [system_notice('[本地服务] 房间设置无效或地图不可用。')]
        if updated==engine.room.resolved_request:return []
        engine.room.request=engine.room.resolved_request=updated
        return [reply]
    if ident==3230:
        engine.require(len(p)==1,'team change length')
        if c.phase!=Phase.ROOM or engine.room is None or p[0] not in (0,1):
            return [system_notice('[本地服务] 当前不能切换队伍。')]
        if engine.room.team==p[0]:return []
        engine.room.team=p[0]
        entry=bytearray(engine.room.entry);entry[66]=p[0];entry[106]=p[0];engine.room.entry=bytes(entry)
        return [Message(3250,struct.pack('<QBB',c.uid,p[0],entry[10]))]
    result = dispatch((equipment.handle, training.handle), engine, c, message)
    if result is not None:
        return result
    if ident == 3010:
        engine.require(engine.p2p_alive() and engine.p2p.get('bound'), 'P2P not bound')
        if c.phase == Phase.ROOM and engine.room and p == engine.room.request:
            return []  # Duplicate create must not reinstall a live CPlayer.
        engine.require(c.phase == Phase.LOBBY, 'create room phase')
        resolved=packets.resolve_room_request(p,engine.map_catalog)
        entry = packets.room_entry(resolved, c.uid,map_catalog=engine.map_catalog)
        if p[46] in packets.COMPETITIVE_MODES:
            _,name,profile,inventory=engine.store.snapshot(c.uid)
            fighter=packets.fighter_snapshot(c.uid,name,profile,inventory,0,0,engine.p2p['player'])
            entry=packets.room_entry_member(resolved,c.uid,1,0,0,fighter,map_catalog=engine.map_catalog).payload
        engine.room, c.phase = Room(c.uid,p,entry,resolved_request=resolved), Phase.ROOM
        return [Message(3100, entry), Message(3160, struct.pack('<Q', c.uid))]
    if ident == 3110:
        # 954C50/954C70 send an empty request. Native 3115 -> 82D920 ->
        # 82CF60 resets battle/fighters and returns selector6 (lobby).
        # 3130 is a different, 8-byte peer departure notification.
        engine.require(not p, 'leave request length')
        if c.phase == Phase.LOBBY and engine.room is None:
            return []  # Do not repeat native teardown on duplicate requests.
        engine.require(c.phase in (Phase.ROOM, Phase.BATTLE) and engine.room is not None,
                     'leave requires a waiting room or battle')
        engine.room = None
        engine.consume_intents.clear()
        c.phase = Phase.LOBBY
        return [Message(3115)]
    if ident==4082:
        engine.require(not p,'weapon switch request length')
        if c.phase!=Phase.BATTLE or engine.room is None:return []
        if engine.hub is not None and engine.room.stage!='battle':return []
        allowed,quantity=engine.store.weapon_switch_snapshot(c.uid)
        #8284F0 resumes local action1013 even on denial. Offline policy
        # is free switching; never fabricate a deduction or overwrite the
        # native kind74 item quantity with zero on a successful response.
        return [Message(4083,struct.pack('<QIII',c.uid,0,int(allowed),quantity))]
    if ident==4031:
        engine.require(not p,'waiting timeout request length')
        if c.phase!=Phase.ROOM or engine.room is None:return []
        engine.room=None;engine.consume_intents.clear();c.phase=Phase.LOBBY
        return [Message(4032,struct.pack('<Q',c.uid))]
    if ident == 4030:
        engine.require(not p and engine.room is not None, 'start request shape/context')
        engine.require(c.uid == engine.room.owner and engine.p2p_alive(), 'start owner/P2P')
        if c.phase in (Phase.LOADING, Phase.WAIT_READY, Phase.BATTLE):
            return []
        engine.require(c.phase == Phase.ROOM, 'start phase')
        if engine.room.request[46] in packets.COMPETITIVE_MODES:
            # This endpoint has only one actual member. Do not invent an
            # opponent or start a team match that immediately has no enemy.
            return []
        engine.room.serial = engine.store.next_battle()
        engine.consume_intents.clear()
        c.phase = Phase.LOADING
        return [Message(4050, struct.pack('<Q', c.uid)), Message(4080, packets.battle_start(
            engine.room.number, 0, engine.room.serial))]  # no measured delay in legacy single-endpoint path
    if ident == 4160:
        engine.require(not p, 'load notification length')
        if c.phase in (Phase.WAIT_READY, Phase.BATTLE):
            return []
        engine.require(c.phase == Phase.LOADING, 'load notification phase')
        c.phase = Phase.WAIT_READY
        return [Message(4170, struct.pack('<Q', c.uid)), Message(4180)]
    if ident == 8040:
        engine.require(len(p) == 14 and engine.room is not None, 'ready shape/context')
        room_id, uid = struct.unpack_from('<HQ', p)
        engine.require(room_id == engine.room.number and uid == c.uid, 'ready identity')
        if c.phase == Phase.BATTLE:
            return []
        engine.require(c.phase == Phase.WAIT_READY, 'ready phase')
        c.phase = Phase.BATTLE
        engine.room.battle_clock_origin=engine.clock()
        engine.room.battle_clock_last=0
        return [Message(8070, packets.battle_ready(engine.room.number, engine.room.serial))]
    return None
