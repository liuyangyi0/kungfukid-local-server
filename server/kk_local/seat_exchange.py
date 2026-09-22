"""Native KOF waiting-room consent exchange, independent of registry slots.

80D340 produces24 bytes;825B30 swaps key/team.30s consent lifetime and
connection/roster binding are provisional local-server policy.
"""
import struct
from .wire import Message


def cancel(hub,room):
    pending=room.seat_exchange
    room.seat_exchange=None
    if pending:
        for uid in pending['participants']:
            if uid in room.members:hub.queue(room.members[uid].engine,Message(3264))


def expire(hub,room):
    pending=room.seat_exchange
    if pending and (hub.start_identity(room)!=pending['identity'] or
                    room.members[room.owner].engine.clock()>=pending['deadline']):cancel(hub,room)


def handle(hub,engine,c,message):
    from .engine import Phase
    p=message.payload;engine.require(len(p)==24,'seat exchange exact24')
    room=engine.room
    if c is not engine.game or c.phase!=Phase.ROOM or room is None:return []
    reject=lambda:[Message(3264)]
    # The proven producer and consent UI are CUIKOFRoom. Do not invoke its
    #singleton against an unqualified room type just because IDs are shared.
    if room.request[46]!=3 or room.stage!='room' or room.network_probe:return reject()
    if any(u in hub.suspended or m.engine.game is None or m.engine.game.phase!=Phase.ROOM for u,m in room.members.items()):return reject()
    source,target,old,new=struct.unpack('<QQII',p)
    a=room.fighters.get(source);b=room.fighters.get(target)
    if (a is None or source==target or old>=8 or new>=8 or old==new or
            a.registry_key!=old or a.ready or (target and (b is None or b.registry_key!=new or b.ready))):return reject()
    if any(u!=target and m.registry_key==new for u,m in room.fighters.items()):return reject()
    if not target and new//4!=a.team:return reject()
    if message.id==3260:
        engine.require(source==c.uid and a.engine is engine,'seat exchange sender spoof')
        if room.seat_exchange:return [Message(3267)]
        if b:
            room.seat_exchange=dict(payload=p,participants=(source,target),identity=hub.start_identity(room),
                deadline=engine.clock()+30)
            hub.queue(b.engine,Message(3261,p));return []
    else:
        pending=room.seat_exchange
        if not pending or pending['payload']!=p or c.uid!=target or b is None or b.engine is not engine:return reject()
        room.seat_exchange=None
        if message.id==3263:
            hub.queue(a.engine,Message(3264));return []
    if b:
        a.registry_key,b.registry_key=b.registry_key,a.registry_key
        a.team,b.team=b.team,a.team
    else:a.registry_key=new
    for uid,m in room.members.items():
        if m.ready:
            m.ready=False;hub.broadcast(room,Message(4070,struct.pack('<Q',uid)))
    hub.broadcast(room,Message(3265,p))
    return engine.take_pending(c)
