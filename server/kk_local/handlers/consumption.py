"""Native consumable intent/receipt pairing; no replay of client-side effects."""
import struct
from .. import packets
from ..wire import Message, ProtocolError
from ..chat import system_notice


from ..layouts import decode_consumption


def handle(engine, c, ident, p):
    from ..engine import Phase
    # Effects execute in the native client before4200. Never echo8289 to
    # the sender: doing so could replay the effect and consume twice locally.
    details={}
    try:
        if c.phase != Phase.BATTLE or not engine.room:
            raise ValueError('consumption outside battle')
        if ident==4200:
            if len(p)!=4:
                raise ValueError('4200 length')
            instance=struct.unpack('<I',p)[0]
            details['instance']=instance
            _,record=engine.store.consumable(c.uid,instance=instance)
            if struct.unpack_from('<H',record,23)[0]==0:
                raise ValueError('empty consumable')
            # At most the two fixed equipped slots; clear on exit/disconnect.
            # Do not let host wall-time/load delays invalidate a native use.
            engine.consume_intents[instance]=True
            engine.last_consume_event=dict(event='consume_intent',instance=instance)
            return []
        event=decode_consumption(p)
        details.update(sequence=event['seq'],slot=event['slot'],battle=event['serial'])
        if (event['sender']!=c.uid or event['target']!=c.uid or
            (event['room'],event['serial'])!=(engine.room.number,engine.room.serial)):
            raise ValueError('consumption identity or battle mismatch')
        instance,_=engine.store.consumable(c.uid,slot=event['slot'])
        intent=instance in engine.consume_intents
        applied=engine.store.consume_once(c.uid,engine.room.serial,event['seq'],instance,event['signature'],intent)
        engine.consume_intents.pop(instance,None)
        engine.last_consume_event=dict(event='consume_applied' if applied else 'consume_duplicate',
                                    instance=instance,slot=event['slot'],sequence=event['seq'],battle=engine.room.serial)
        return [packets.consumable_snapshot(c.uid,engine.store.consumable_slots(c.uid))]
    except (ValueError,struct.error) as exc:
        engine.last_consume_event=dict(event='consume_rejected',reason=str(exc),**details)
        return []
