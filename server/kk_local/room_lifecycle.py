"""Room membership and disconnect/rejoin lifecycle.

RoomHub remains the sole state owner. This collaborator owns no second room,
invite or suspension registry; its facade calls preserve existing hook order.
"""
import struct
from . import packets
from .wire import Message, ProtocolError
from .chat import system_notice


class RoomLifecycle:
    def __init__(self, hub):
        self.hub = hub

    def install(self, room, engine, *, spectator=False):
        """Admit only after preparing every native record; no partial membership."""
        hub = self.hub
        from .rooms import Member
        from .engine import Phase
        uid=engine.account_uid
        fighters=room.fighters
        full=(len(room.members)-len(fighters)>=hub.observer_limit(room) if spectator else len(fighters)>=room.request[37])
        if room.stage!='room' or full:
            raise ProtocolError('room is full or already loading')
        slot=8 if spectator else next(i for i in range(8) if i not in {m.slot for m in fighters.values()})
        team=slot%2
        key=8 if spectator else hub.position_key(room,team,slot)
        if key is None:
            team=1-team;key=hub.position_key(room,team,slot)
        if key is None:raise ProtocolError('no room position key available')
        member=Member(engine,slot,team,spectator=spectator,registry_key=key)
        own=hub.fighter(uid,member)
        peers=[(u,m,hub.fighter(u,m)) for u,m in room.members.items()]
        if room.members and room.members[room.owner].spectator and not spectator:
            new_owner=uid
        else:new_owner=room.owner
        entry=packets.room_entry_member(room.resolved_request or room.request,new_owner,room.number,slot,member.team,own,map_catalog=engine.map_catalog,spectator_capacity=hub.spectator_capacity if room.request[46]==1 else 0,series_rounds=room.series.limit if room.series else 0)
        hub.drop_invites(uid)
        room.members[uid]=member
        changed_owner=room.owner!=new_owner
        room.owner=new_owner
        engine.room=room
        engine.game.phase=Phase.ROOM
        hub.queue(engine,entry)
        hub.queue(engine,Message(3160,struct.pack('<Q',room.owner)))
        for peer_uid,peer,raw in peers:
            hub.queue(engine,Message(3090,raw))
            if not peer.spectator and peer.activity:
                hub.queue(engine,Message(3550,struct.pack('<QI',peer_uid,peer.activity)))
            hub.queue(peer.engine,Message(3090,own))
            if changed_owner:hub.queue(peer.engine,Message(3160,struct.pack('<Q',new_owner)))
        for peer_uid,peer,_ in peers:
            if peer.ready:
                peer.ready=False
                hub.broadcast(room,Message(4070,struct.pack('<Q',peer_uid)))

    def equipment_changed(self, engine):
        hub = self.hub
        room=engine.room
        if room and room.stage=='room':
            raw=hub.fighter(engine.account_uid,room.members[engine.account_uid])
            hub.broadcast(room,Message(3090,raw),exclude=engine.account_uid)
            member=room.members[engine.account_uid]
            if not member.spectator and member.activity:
                hub.broadcast(room,Message(3550,struct.pack('<QI',engine.account_uid,member.activity)),exclude=engine.account_uid)

    def disconnected(self, engine):
        hub = self.hub
        hub.drop_invites(engine.account_uid)
        room=engine.room
        if room is None or room.stage!='room' or room.request[46]==4:
            hub.leave(engine)
            return
        uid=engine.account_uid
        room.members[uid].ready=False
        hub.suspended[uid]=engine.clock()+hub.reconnect_seconds
        hub.broadcast(room,Message(4070,struct.pack('<Q',uid)),exclude=uid)

    def expire(self):
        hub = self.hub
        for room in list(hub.rooms.values()):
            if hub.public_policy and room.public_deadline and room.members[room.owner].engine.clock()>=room.public_deadline:
                hub.leave(room.members[room.owner].engine,acknowledge=True);continue
            from .seat_exchange import expire as expire_seat
            expire_seat(hub,room)
            probe=room.network_probe
            if probe and (probe['identity']!=hub.start_identity(room) or
                          room.members[room.owner].engine.clock()-probe['start']>=10):
                hub.cancel_network_probe(room)
                hub.broadcast(room,system_notice('[本地服务] 开战检测已取消，请重新准备。'))
        for uid,deadline in list(hub.suspended.items()):
            engine=hub.engines[uid]
            if engine.clock()>=deadline:
                hub.suspended.pop(uid,None)
                hub.leave(engine)
        for target,invite in list(hub.invites.items()):
            inviter=hub.engines.get(invite['inviter'])
            if inviter is None or inviter.clock()>=invite['expires']:
                hub.invites.pop(target,None)

    def drop_invites(self, uid):
        hub = self.hub
        for target,invite in list(hub.invites.items()):
            if target==uid or invite['inviter']==uid:hub.invites.pop(target,None)

    def restore_bound(self, engine):
        hub = self.hub
        from .engine import Phase
        hub.expire()
        uid=engine.account_uid
        if uid not in hub.suspended:
            return []
        room=engine.room
        if room is None or room.stage!='room':
            return []
        member=room.members[uid]
        own=hub.fighter(uid,member)
        peers=[(u,m,hub.fighter(u,m)) for u,m in room.members.items() if u!=uid and u not in hub.suspended]
        entry=packets.room_entry_member(room.resolved_request or room.request,room.owner,room.number,member.slot,member.team,own,map_catalog=engine.map_catalog,spectator_capacity=hub.spectator_capacity if room.request[46]==1 else 0,series_rounds=room.series.limit if room.series else 0)
        # Prepare every record before changing state or queuing a reply.
        member.activity=0  # Reconnected client has not reported an open panel.
        hub.suspended.pop(uid,None)
        engine.game.phase=Phase.ROOM
        hub.queue(engine,entry)
        hub.queue(engine,Message(3160,struct.pack('<Q',room.owner)))
        for peer_uid,peer,raw in peers:
            hub.queue(engine,Message(3090,raw))
            if not peer.spectator and peer.activity:
                hub.queue(engine,Message(3550,struct.pack('<QI',peer_uid,peer.activity)))
            hub.queue(peer.engine,Message(3090,own))
        return engine.take_pending(engine.game)

    def leave(self, engine, *, acknowledge=False, departure=None):
        hub = self.hub
        from .engine import Phase
        if engine.room:
            from .seat_exchange import cancel as cancel_seat
            cancel_seat(hub,engine.room)
            hub.cancel_network_probe(engine.room)
        hub.drop_invites(engine.account_uid)
        room=engine.room
        if room is None:
            return
        uid=engine.account_uid
        hub.suspended.pop(uid,None)
        departed=room.members.pop(uid,None)
        room.pickup_requests.pop(uid,None)
        room.chest_requests.pop(uid,None)
        room.pending_hit_receipts.pop(uid,None)
        room.pending_death_receipts.pop(uid,None)
        for ledger in (room.pending_hit_receipts,room.pending_death_receipts):
            for reporter,rows in ledger.items():ledger[reporter]=[row for row in rows if row[0]!=uid]
        room.loaded.discard(uid)
        room.input_ready.discard(uid)
        room.result_acks.discard(uid)
        room.result_replies.pop(uid,None)
        room.result_requests.discard(uid)
        room.pair_selection_versions.pop(uid,None)
        room.motion_payloads.pop(uid,None)
        for registry in (room.talisman_pending,room.talisman_sequences):
            for key in tuple(registry):
                if key[0]==uid:del registry[key]
        for key in tuple(room.last_sequence):
            if key[0]==uid:
                del room.last_sequence[key]
        for key in tuple(room.active_states):
            if key[0]==uid:
                del room.active_states[key]
        for first,selection in tuple(room.pair_selections.items()):
            if first==uid or selection[0]==uid:del room.pair_selections[first]
        for key,record in tuple(room.projectiles.items()):
            if record['owner']==uid:
                del room.projectiles[key]
        engine.room=None
        engine.consume_intents.clear()
        if engine.game is not None:
            engine.game.phase=Phase.LOBBY
        if acknowledge:
            hub.queue(engine,Message(3115))
        if not room.members:
            hub.rooms.pop(room.number,None)
            return
        if room.stage!='room':
            if departed is not None and departed.spectator:
                hub.broadcast(room,departure or Message(3130,struct.pack('<Q',uid)))
                hub.advance_barriers(room)
                return
            # A running native match cannot be restored from just a roster.
            # Abort cleanly, without a fabricated result or reward. Re-entry
            # starts a fresh match; hot reconnect semantics remain unqualified.
            for other in list(room.members.values()):
                other.engine.room=None
                other.engine.consume_intents.clear()
                if other.engine.game:
                    other.engine.game.phase=Phase.LOBBY
                hub.queue(other.engine,Message(3115))
            room.members.clear()
            hub.rooms.pop(room.number,None)
            return
        hub.broadcast(room,departure or Message(3130,struct.pack('<Q',uid)))
        if room.owner==uid:
            candidates=room.fighters or room.members
            room.owner=min(candidates,key=lambda u:candidates[u].slot)
            hub.broadcast(room,Message(3160,struct.pack('<Q',room.owner)))
        # Composition changed: everyone must confirm readiness again.
        for other_uid,other in room.members.items():
            if other.ready:
                other.ready=False
                hub.broadcast(room,Message(4070,struct.pack('<Q',other_uid)))
