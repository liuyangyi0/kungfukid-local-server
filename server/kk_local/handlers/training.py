"""Training handlers; preserve existing phase gates and reply ordering."""
import struct
from .. import packets
from ..wire import Message


def handle(engine, c, message):
    from ..engine import Phase
    ident, p = message.id, message.payload
    if ident in (21000, 21002):
        if ident == 21000:
            engine.require(len(p) == 8 and struct.unpack('<Q', p)[0] == c.uid,
                         'training query identity/length')
        else:
            engine.require(not p, 'training start length')
        minutes, active = engine.store.training(c.uid, engine.wall_clock(), start=ident == 21002)
        # Querying an inactive state naturally causes the native client to
        # request21002. Only21005 is used for start updates, avoiding a loop.
        reply = 21001 if ident == 21000 else 21005
        return [Message(reply, packets.training_status(c.uid, minutes, active, rewards=engine.training_rewards))]
    if ident == 21006 and engine.training_rewards:
        engine.require(not p, 'training claim length')
        if c.phase not in (Phase.LOBBY, Phase.ROOM):
            return []
        claim = engine.store.claim_training(c.uid, engine.wall_clock())
        minutes, active = engine.store.training(c.uid, engine.wall_clock())
        status = packets.training_status(c.uid, minutes, active, rewards=True)
        if claim is None:
            return [Message(21005, status)]  # No fake grant or success.
        # Real profile update precedes the UI acknowledgement.21007 causes
        # native21002 to begin a NEW training interval, not a second claim.
        return [Message(4300, struct.pack('<II', claim['points'], claim['second'])),
                Message(21007, status)]
    return None
