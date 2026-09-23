"""Directory handlers; preserve existing phase gates and reply ordering."""
import struct
from .. import packets
from ..wire import ProtocolError
from ..chat import system_notice


def handle(engine, c, message):
    from ..engine import Phase
    ident, p = message.id, message.payload
    if ident==2250:
        engine.require(len(p)==8,'player directory request length')
        page,selector=struct.unpack('<II',p)
        #92DBB0 machine code fills the page omitted by its decompiler
        #view;92DAE0 returns10 for ordinary lobby,7 for PVE. Room2250
        #and2251 use other transitions: do not alias those to this view.
        if c.phase!=Phase.LOBBY or selector!=10:
            engine.record_unknown(c,ident,p)
            return []
        engines=engine.hub.engines.values() if engine.hub else (engine,)
        online=sorted((e for e in engines if e.game is not None and
            e.game.phase in (Phase.LOBBY,Phase.ROOM,Phase.LOADING,Phase.WAIT_READY,Phase.BATTLE) and
            not e.delivery_failed),key=lambda e:e.account_uid)
        # Local ordering: UID ascending, ten rows/page. Include engine so a
        #live request never falsely reports an entirely empty directory.
        page=max(1,page)
        pages=max(1,(len(online)+9)//10)
        rows=[packets.player_directory_record(e.account_uid,e.store.nickname(e.account_uid),
                   in_room=e.room is not None) for e in online[(page-1)*10:page*10]]
        return [packets.player_directory(rows,page=page,pages=pages)]
    if ident in (2420,2430):
        engine.require(len(p)==8,'public player query length')
        if c.phase not in (Phase.LOBBY,Phase.ROOM):return []
        target=struct.unpack('<Q',p)[0]
        peer=engine.hub.engines.get(target) if engine.hub else engine if target==c.uid else None
        if (peer is None or peer.game is None or peer.delivery_failed or
                peer.game.phase not in (Phase.LOBBY,Phase.ROOM,Phase.LOADING,Phase.WAIT_READY,Phase.BATTLE)):
            return [system_notice('[本地服务] 查询的玩家当前不在线。')]
        _,_,profile,inventory=peer.store.snapshot(target)
        try:
            reply=(packets.public_role_preview(c.uid,target,profile,inventory) if ident==2420 else
                   packets.public_weapon_collection(c.uid,target,inventory))
        except ProtocolError:
            engine.record_unknown(c,ident,p)
            return [system_notice('[本地服务] 该玩家的资料暂不能生成原生预览。')]
        return [reply]
    return None
