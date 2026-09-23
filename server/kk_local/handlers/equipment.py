"""Equipment handlers; preserve existing phase gates and reply ordering."""
import struct
from .. import packets
from ..wire import Message


def handle(engine, c, message):
    from ..engine import Phase
    ident, p = message.id, message.payload
    if ident == 2080:
        engine.require(len(p) == 16, 'equip request length')
        if c.phase not in (Phase.LOBBY,Phase.ROOM):
            engine.record_unknown(c, ident, p)
            return []
        instance, slot = struct.unpack_from('<II',p)
        if engine.hub and engine.room and engine.room.members[c.uid].ready:
            return []
        try:
            changed = engine.store.equip(c.uid,instance,slot)
        except ValueError as exc:
            engine.record_unknown(c,ident,p)
            return [packets.equipment_rejection(str(exc))]
        # 0460 replaces the descriptor vector, including the displaced item.
        # 829E70 consumes2090's record at+16; preserve request prefix raw bits.
        if engine.hub:
            engine.hub.equipment_changed(engine)
        return [Message(1120,engine.store.snapshot(c.uid)[3]),Message(2090,p+changed)]
    if ident==2110:
        engine.require(not p,'expired item query length')
        if c is not engine.game or c.phase not in (Phase.LOBBY,Phase.ROOM):
            return []
        #8263A0/81EBB0 consume count32 followed by instance32 values.
        # This local store has entitlements, not an expiry scheduler; do
        # not reinterpret raw display duration as wall-clock expiration.
        return [Message(2120,struct.pack('<I',0))]
    if ident == 2300:
        engine.require(len(p) == 4, 'unequip request length')
        if c.phase not in (Phase.LOBBY, Phase.ROOM):
            engine.record_unknown(c, ident, p)
            return []
        if engine.hub and engine.room and engine.room.members[c.uid].ready:
            return []
        try:
            changed = engine.store.unequip(c.uid, struct.unpack('<I', p)[0])
        except ValueError:
            engine.record_unknown(c, ident, p)
            return []
        if changed is None:
            return []
        if engine.hub:
            engine.hub.equipment_changed(engine)
        # 829E20 ->8B3D60 ->9D0A90: instance4 + record68.
        # Crucially, consume2310 BEFORE the absolute inventory snapshot:
        # 9D0A90 reads the OLD slot to clear live weapon/appearance state.
        return [Message(2310, p + changed), Message(1120, engine.store.snapshot(c.uid)[3])]
    return None
