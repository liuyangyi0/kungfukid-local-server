"""Existing SDK handoff/session protocol; no new authentication or public endpoints."""
import struct
from .. import packets
from ..wire import Message, ProtocolError
from ..chat import system_notice


def begin(engine, c, message):
    from ..engine import Phase
    ident, p = message.id, message.payload
    if ident in (1010, 2010):
        engine.require(c.phase == Phase.CONNECTED and len(p) == 96, 'login phase/length')
        uid = struct.unpack_from('<Q', p)[0]
        build = struct.unpack_from('<I', p, 49)[0]
        engine.require(uid == engine.account_uid and build == 594, 'unsupported adapter identity/build')
        c.uid = uid
        if ident == 1010:
            engine.require(engine.clock() <= engine.grant_until and engine.grant_until > 0,
                         'no recent offline SDK grant')
            engine.require(engine.bootstrap is None and engine.game is None, 'one adapter session only')
            engine.bootstrap, c.phase = c, Phase.BOOTSTRAP
            return packets.account_packets(engine.store.snapshot(uid)[3], engine.game_port,gold=engine.store.gold_balance(uid),host=engine.advertised_host)
        engine.require(engine.pending_handoff is not None and
                     engine.pending_handoff[0] == uid and engine.clock() <= engine.pending_handoff[1],
                     'GS handoff expired or missing')
        engine.require(engine.game is None, 'GS ownership conflict')
        engine.pending_handoff = None
        engine.delivery_failed = False
        engine.game, c.phase = c, Phase.LOBBY
        from ..weapon_upgrade import publish
        return [packets.lobby_context(engine.p2p_port,engine.advertised_host)]+publish(engine,c)
    return None


def bound(engine, c, message):
    from ..engine import Phase
    ident, p = message.id, message.payload
    if ident==2060:
        #2070 closes the native current socket: lobby consumer824150
        # returns selector5; login consumer822470 clears the local profile.
        engine.require(not p,'channel return length')
        if c is engine.game:
            if c.phase!=Phase.LOBBY or engine.room is not None:
                return [system_notice('[本地服务] 请先离开房间，再返回选区。')]
            uid=c.uid
            engine.disconnect(c)
            engine.pending_handoff=(uid,engine.clock()+120)
        elif c is engine.bootstrap:
            engine.disconnect(c)
            engine.pending_handoff=None
            engine.grant_until=0.0
        else:
            raise ProtocolError('channel return requires current connection')
        return [Message(2070)]
    if ident == 3320:
        engine.require(c is engine.bootstrap and c.phase in (Phase.PROFILE, Phase.HANDOFF),
                     'role selection phase')
        engine.require(p == struct.pack('<I', 1), 'unexpected role selector')
        if c.phase == Phase.HANDOFF:
            return [Message(3330, p)]
        c.phase = Phase.HANDOFF
        engine.pending_handoff = (c.uid, engine.clock() + 120)
        return [Message(3330, p), Message(1201, struct.pack('<I', 1))]
    if ident == 1157:
        engine.require(len(p) == 0, 'catalog request length')
        return packets.catalog(engine.game_port,engine.advertised_host)
    return None
