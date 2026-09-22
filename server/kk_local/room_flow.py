"""RoomFlow: local service policy; RoomHub remains the only room state owner."""
import struct
from dataclasses import replace
from . import packets
from .wire import Message, ProtocolError
from .team_series import SeriesProgress


class RoomFlow:
    def __init__(self, hub):
        self.hub = hub

    def cancel_network_probe(self,room):
        hub = self.hub
        if room.network_probe is None:return
        room.network_probe=None
        for uid,m in room.members.items():
            if m.ready:
                m.ready=False;hub.broadcast(room,Message(4070,struct.pack('<Q',uid)))

    def begin_start(self,room):
        hub = self.hub
        from .seat_exchange import cancel as cancel_seat
        cancel_seat(hub,room)
        if not hub.network_probe_enabled:
            hub.start_match(room);return
        if room.network_probe is not None:return
        now=room.members[room.owner].engine.clock()
        room.network_probe=dict(start=now,identity=hub.start_identity(room),pending=set(room.fighters))
        for m in room.members.values():m.network_delay_ms=0
        for m in room.fighters.values():hub.queue(m.engine,Message(4150))

    def network_delay_reply(self,engine,c,p):
        hub = self.hub
        from .engine import Phase
        engine.require(not p,'network delay reply length')
        room=engine.room
        if room is None or room.stage!='room' or c is not engine.game or c.phase!=Phase.ROOM:return []
        probe=room.network_probe
        if probe is None or c.uid not in probe['pending']:return []
        now=room.members[room.owner].engine.clock()
        if (probe['identity']!=hub.start_identity(room) or now-probe['start']>=10 or
                any(u in hub.suspended or not m.engine.p2p_alive() or m.engine.game is None or
                    m.engine.game.phase!=Phase.ROOM for u,m in room.members.items())):
            hub.cancel_network_probe(room);return engine.take_pending(c)
        import math
        room.members[c.uid].network_delay_ms=max(1,math.ceil(1000*max(0,now-probe['start'])))
        probe['pending'].remove(c.uid)
        if not probe['pending']:
            room.network_probe=None
            hub.start_match(room)
        return engine.take_pending(c)

    def start_match(self,room):
        hub = self.hub
        from .engine import Phase
        owner=room.members[room.owner].engine
        if room.request[46]==21:
            from .pve import StageBattle
            map_id=struct.unpack_from('<I',room.resolved_request or room.request,38)[0]
            plan=owner.map_catalog.stage_plans.get(map_id) if owner.map_catalog else None
            if plan is None:raise ProtocolError('stage plan unavailable')
            room.pve=StageBattle(plan.waves(len(room.fighters)))
        elif room.request[46]==10:
            from .foster import FosterBattle
            map_id=struct.unpack_from('<I',room.resolved_request or room.request,38)[0]
            plan=owner.map_catalog.foster_plans.get(map_id) if owner.map_catalog else None
            if plan is None:raise ProtocolError('Mode10 plan unavailable')
            room.pve=FosterBattle(plan,len(room.fighters))
        room.serial=owner.store.next_battle()
        if room.series:room.series=SeriesProgress(room.series.limit)
        for state in (room.last_sequence,room.motion_payloads,room.active_states,room.pair_selections,
                      room.pair_selection_versions,room.projectiles,room.pickup_requests,room.chest_requests,
                      room.pending_hit_receipts,room.pending_death_receipts,room.spawned_collectibles,
                      room.result_reports,room.result_requests,room.result_replies,room.result_acks,
                      room.loaded,room.input_ready,room.talisman_pending,room.talisman_sequences):state.clear()
        room.stage='loading'
        if hub.public_policy:room.public_deadline=owner.clock()+120
        values={m.slot:m.network_delay_ms for m in room.fighters.values()}
        host_slot=room.members[room.owner].slot
        for m in room.members.values():
            m.activity=0;m.engine.game.phase=Phase.LOADING;m.engine.consume_intents.clear()
            hub.queue(m.engine,packets.battle_start_members(room.number,room.serial,host_slot,values))

    def advance_barriers(self, room):
        """Load all views; wait for8040 only from fighters; ACK all result viewers."""
        hub = self.hub
        from .engine import Phase
        if room.stage=='loading' and room.loaded==set(room.members):
            if room.request[46]==10 and (room.pve is None or room.pve.positions is None):return
            room.stage='wait_ready'
            for m in room.members.values():m.engine.game.phase=Phase.WAIT_READY
            if room.pve:
                from .pve import initial_blocks
                initial_blocks(hub,room)
            #98C9C0 already invokes mode.vslot9 for an observer;829770/4180
            #would invoke it again. Only fighters need this initialization.
            for m in room.fighters.values():hub.queue(m.engine,Message(4180))
        if room.stage=='wait_ready' and room.fighters and room.input_ready==set(room.fighters):
            room.stage='battle'
            room.battle_clock_origin=room.members[room.owner].engine.clock()
            if hub.public_policy:room.public_deadline=room.battle_clock_origin+struct.unpack_from('<H',room.request,47)[0]+30
            room.battle_clock_last=0
            for m in room.members.values():m.engine.game.phase=Phase.BATTLE
            hub.broadcast(room,Message(8070,packets.battle_ready(room.number,room.serial)))
        if room.stage=='result' and room.result_acks==set(room.members):
            room.stage='room'
            room.public_deadline=0
            room.pve=None
            room.loaded.clear();room.input_ready.clear();room.last_sequence.clear();room.motion_payloads.clear()
            room.talisman_pending.clear();room.talisman_sequences.clear()
            room.active_states.clear();room.pair_selections.clear();room.pair_selection_versions.clear();room.projectiles.clear()
            room.pickup_requests.clear();room.chest_requests.clear()
            room.pending_hit_receipts.clear();room.pending_death_receipts.clear();room.spawned_collectibles.clear()
            for uid,m in room.members.items():
                m.ready=False;m.engine.game.phase=Phase.ROOM
                if not m.spectator:hub.broadcast(room,Message(4070,struct.pack('<Q',uid)))
            # A client's3550 can follow its4115 before the last peer ACK.
            # Defer the room-only consumer until the result barrier is clear.
            for uid,m in room.fighters.items():
                if m.activity:
                    hub.broadcast(room,Message(3550,struct.pack('<QI',uid,m.activity)),exclude=uid)

    def toggle_spectator(self, engine, c, message):
        hub = self.hub
        from .engine import Phase
        engine.require(not message.payload,'spectator toggle request length')
        room=engine.room;uid=c.uid
        if room is None or c.phase!=Phase.ROOM:return []
        old=room.members[uid]
        error=0
        if room.stage!='room':error=-3
        elif old.spectator and len(room.fighters)>=room.request[37]:error=-1
        elif not old.spectator and len(room.members)-len(room.fighters)>=hub.observer_limit(room):error=-2
        if error:return [Message(3092,struct.pack('<Qi',uid,error))]
        spectator=not old.spectator
        slot=8 if spectator else next(i for i in range(8) if i not in {m.slot for m in room.fighters.values()})
        key=8 if spectator else hub.position_key(room,old.team,slot,exclude=uid)
        if key is None:return [Message(3092,struct.pack('<Qi',uid,-1))]
        new=replace(old,slot=slot,spectator=spectator,ready=False,registry_key=key)
        notice=packets.spectator_transition(uid,spectator,hub.fighter(uid,new))
        # All native records are prepared before committing the role change.
        room.members[uid]=new
        for other_uid,m in room.members.items():
            if m.ready:
                m.ready=False
                hub.broadcast(room,Message(4070,struct.pack('<Q',other_uid)))
        hub.broadcast(room,notice)
        if room.fighters and room.members[room.owner].spectator:
            room.owner=min(room.fighters,key=lambda u:room.fighters[u].slot)
            hub.broadcast(room,Message(3160,struct.pack('<Q',room.owner)))
        return engine.take_pending(c)

    def handle_activity(self, engine, c, message):
        """Relay the authenticated member's native room label to room peers."""
        hub = self.hub
        from .engine import Phase
        p=message.payload
        engine.require(len(p)==12,'room activity length')
        uid,value=struct.unpack('<QI',p)
        engine.require(uid==c.uid,'room activity identity')
        if value not in (0,1,2,3):
            engine.record_unknown(c,message.id,p);return []
        room=engine.room
        if room is None or uid not in room.fighters:return []
        member=room.members[uid]
        if member.engine is not engine or c is not engine.game:return []
        if room.series and room.stage=='result' and room.series.phase=='result':
            #4112 enters selector7 but does not send4115. Native80BA10 sends
            #3550/0 when the user returns. Do not confuse that with result3.
            if uid not in room.result_replies:return []
            if uid in room.result_acks:return []
            if value==3:room.series.viewing.add(uid)
            elif value==0 and uid not in room.series.viewing:return []
            elif value not in (0,3):return []
            member.activity=value
            if value==0:
                room.result_acks.add(uid)
                hub.advance_barriers(room)
            return engine.take_pending(c)
        #3550 is selector7 only. Result processing reports3 after4115;
        #keep that observation pending, never invoke room UI during battle.
        if room.stage=='result' and uid in room.result_acks:
            member.activity=value
            return []
        if room.stage!='room' or c.phase!=Phase.ROOM:return []
        if member.activity==value:return []
        member.activity=value
        hub.broadcast(room,Message(3550,p),exclude=uid)
        return engine.take_pending(c)

    def handle(self, engine, c, message):
        from .engine import Phase
        hub = self.hub
        ident, p = message.id, message.payload
        uid, room, require = c.uid, engine.room, engine.require
        if ident in (4030,4060):
            require(not p and room is not None,'ready context/length')
            if room.stage!='room':
                return []
            member=room.members[uid]
            if member.spectator:return []
            if ident==4060:
                if member.ready:
                    member.ready=False
                    hub.broadcast(room,Message(4070,struct.pack('<Q',uid)))
            else:
                require(engine.p2p_alive(),'ready P2P expired')
                if uid==room.owner and room.request[46] in packets.COMPETITIVE_MODES:
                    if len(room.fighters)<2:
                        return []
                    if room.request[46] in packets.TEAM_MODES:
                        counts=[sum(m.team==t for m in room.fighters.values()) for t in (0,1)]
                        if not counts[0] or counts[0]!=counts[1]:return []  #816990 strategy ->8169B0
                    if room.series and any(sum(m.team==t for m in room.fighters.values())>3 for t in (0,1)):
                        return []
                if uid==room.owner and not all(m.ready and u not in hub.suspended for u,m in room.fighters.items() if u!=uid):
                    return []  # Do not lock the host ready before others are ready.
                if not member.ready:
                    member.ready=True
                    hub.broadcast(room,Message(4050,struct.pack('<Q',uid)))
                # Non-owners can ready; only a host request can start the match.
                if (uid==room.owner and all(m.ready for m in room.fighters.values()) and
                        all(u not in hub.suspended and m.engine.p2p_alive() for u,m in room.members.items())):
                    hub.begin_start(room)
        elif ident==4160:
            require(not p and room is not None,'load length/context')
            if uid in room.loaded:
                return []
            require(room.stage=='loading' and c.phase==Phase.LOADING,'load phase')
            room.loaded.add(uid)
            hub.broadcast(room,Message(4170,struct.pack('<Q',uid)))
            hub.advance_barriers(room)
        elif ident==8040:
            require(len(p)==14 and room is not None,'input ready shape/context')
            number,who=struct.unpack_from('<HQ',p)
            require(number==room.number and who==uid,'input ready identity')
            if room.members[uid].spectator:return []
            if uid in room.input_ready:
                return []
            require(room.stage=='wait_ready' and c.phase==Phase.WAIT_READY,'input ready phase')
            room.input_ready.add(uid)
            hub.advance_barriers(room)
        else:
            return None
        return engine.take_pending(c)
