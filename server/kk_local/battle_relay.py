"""BattleRelay: local service policy; RoomHub remains the only room state owner."""
from .wire import Message
from .layouts import decode_battle
from .wire import ProtocolError


class BattleRelay:
    def __init__(self, hub):
        self.hub = hub

    @property
    def metrics(self):
        return self.hub.metrics

    @metrics.setter
    def metrics(self, value):
        self.hub.metrics = value

    def _count_combat(self, decoded, outcome, amount=1):
        if self.metrics is not None and decoded is not None and decoded['id'] in (8120,8121,8125,8126,8140):
            self.metrics.counts[f'battle_{decoded["id"]}_{outcome}']+=amount

    def dispatch_public(self,engine,c,message,*,recipients=None,udp=False):
        """One semantic execution across TCP/UDP, with per-recipient delivery.

        Native peer sends can repeat one event separately for each recipient.
        A shared sequence watermark alone would drop the second recipient; a
        payload-only relay would execute8126/8150 twice after TCP fallback.
        Cache bounded decisions, not raw network credentials. No awaits and no
        temporary copy of gameplay state: the existing handlers own semantics.
        """
        room=engine.room;uid=c.uid
        engine.require(room is not None and room.stage=='battle' and c is engine.game and
                       c.phase.value=='battle' and uid in room.fighters and room.members[uid].engine is engine,
                       'public battle dispatch context')
        payload=bytes(message.payload);key=(uid,payload)
        cache=room.battle_dispatch_cache
        entry=cache.get(key)
        decoded=decode_battle(payload)
        self._count_combat(decoded,'received')
        if entry is not None:
            lane=15 if decoded['id']==8120 else 19
            if decoded[f'sequence_{lane}_raw']<room.last_sequence.get((uid,lane),-1):
                self._count_combat(decoded,'stale')
                return {} if udp else []
            if decoded['id']==8150:
                current=room.active_states.get((decoded['target'],decoded['ustate_code']))
                if (decoded['operation']=='apply' and current!=(decoded['source'],decoded['parameter_signed'],decoded['event_raw'])) or (decoded['operation']=='cancel' and current is not None):return {} if udp else []
            if decoded['id'] in (8400,8401,8402,8403) and not room.projectiles.get(decoded['child_key'],{}).get('alive'):return {} if udp else []
        members={u:m.engine for u,m in room.members.items()}
        targets=set(members) if recipients is None else set(recipients)
        engine.require(targets<=set(room.fighters),'battle recipient outside current fighters')
        if entry is None:
            if decoded is not None and decoded['id']==8121:
                amount=decoded['signed_hp_amount']
                self._count_combat(decoded,'damage_positive' if amount>0 else
                                   'healing' if amount<0 else 'zero_amount')
            engine.require(all(e.battle_delivery_capture is None for e in members.values()),'reentrant battle capture')
            for e in members.values():e.battle_delivery_capture=[]
            try:
                returned=self.handle(engine,c,message)
                outputs={u:list(e.battle_delivery_capture) for u,e in members.items()}
                outputs[uid].extend(returned or [])
            except ProtocolError:
                self._count_combat(decoded,'rejected')
                raise
            finally:
                for e in members.values():e.battle_delivery_capture=None
            outputs={u:tuple(rows) for u,rows in outputs.items() if rows}
            entry=(outputs,set())
            if not outputs:self._count_combat(decoded,'suppressed')
            if outputs:cache[key]=entry
            # At most128 fixed<=334-byte input events and bounded outputs/room.
            while len(cache)>128:cache.popitem(last=False)
        outputs,delivered=entry
        result={u:rows for u,rows in outputs.items() if u in targets and u not in delivered}
        if result:self._count_combat(decoded,'delivered',len(result))
        delivered.update(result)
        if decoded and decoded['id']==8143 and (decoded['target']==0 or decoded['target'] in result):
            self.hub.observe_pair_selection(engine,decoded,recipients=set(result))
        if udp:return result
        for target,rows in result.items():
            for row in rows:members[target].enqueue(row)
        return engine.take_pending(c)

    def handle_reborn_sync(self,engine,c,decoded,payload):
        """Mode16 Host observations; no extra server points or native respawn."""
        hub = self.hub
        room=engine.room;uid=c.uid;require=engine.require
        if room.request[46]!=16:
            engine.record_unknown(c,8071,payload);return []
        require(c is engine.game and uid in room.fighters and room.members[uid].engine is engine,
                'reborn sync connection mismatch')
        require(decoded['sender']==uid,'reborn sync sender spoof')
        if uid!=room.owner or (decoded['flag'],decoded['header_variant'])!=(1,1):
            engine.record_unknown(c,8071,payload);return []
        if decoded['id']==8155:
            roster={m.slot:u for u,m in room.fighters.items()}
            for row in decoded['rows']:
                slot=row['slot'];expected=roster.get(slot,0)
                require(row['player']==expected,'reborn snapshot roster mismatch')
                if not expected:require(not any(payload[46+36*slot:82+36*slot]),'reborn snapshot nonempty vacant slot')
        else:
            require(decoded['participant'] in room.fighters,'reborn event participant absent')
            code=decoded['event_code_raw'];value=decoded['value_raw']
            valid=((code in (0,1,2) and value==0) or (code in (3,6) and value==3) or
                   (code==4 and value==4) or (code==5 and 5<=value<=65535) or
                   (code==7 and 4<=value<=65535))
            if not valid:
                engine.record_unknown(c,8071,payload);return []
        sequence=decoded['sequence_19_raw'];key=(uid,19)
        if sequence<=room.last_sequence.get(key,-1):return []
        room.last_sequence[key]=sequence
        hub.broadcast(room,Message(8071,payload),exclude=uid)
        return engine.take_pending(c)

    def handle(self, engine, c, message):
        from .engine import Phase
        hub = self.hub
        ident, p = message.id, message.payload
        uid, room, require = c.uid, engine.room, engine.require
        if room is None or c.phase!=Phase.BATTLE or room.stage!='battle':
            return []
        decoded=decode_battle(p)
        if room.pve and decoded is not None:
            from .pve import relay_actor
            answer=relay_actor(hub,engine,c,decoded,p)
            if answer is not None:return answer
        if decoded is not None and decoded['id'] in (8294,8295,8296,8297):
            return hub.handle_series_event(engine,c,decoded,p)
        if decoded is not None and decoded['id'] in (9000,9001,9002,9500,9501,9502):
            return hub.handle_pickup(engine,c,decoded,p)
        if decoded is not None and decoded['id']==8126:
            return hub.handle_hit_receipt(engine,c,decoded,p)
        if decoded is not None and decoded['id'] in (8278,8286):
            return hub.handle_death_notice(engine,c,decoded,p)
        if decoded is not None and decoded['id'] in (8155,8157):
            return hub.handle_reborn_sync(engine,c,decoded,p)
        if decoded is not None and decoded['id']==8144:
            return hub.handle_pair_transform(engine,c,decoded,p)
        if decoded is not None and decoded['id']==8270:
            return hub.handle_owned_slip(engine,c,decoded,p)
        # SYSTEM_DESIGN_INFERRED / PROVISIONAL: owned-player messages may
        # update that player's replicated controls; owned HP reports and
        # previously admitted Host cancellations have separate checks below.
        if decoded is None or decoded['id'] not in (8120,8121,8122,8125,8127,8140,8143,8150,8280,8284,8287,8288,8293,8400,8401,8402,8403,8404):
            engine.record_unknown(c,ident,p)
            return []
        require(c is engine.game and uid in room.members and
                room.members[uid].engine is engine,'relay connection/member mismatch')
        require(decoded['sender']==uid,'relay sender spoof')
        if decoded['id']==8284 and decoded['weapon_operation_raw'] not in (1,2,3):
            engine.record_unknown(c,ident,p);return []
        host_cancel=(decoded['id']==8150 and decoded['operation']=='cancel' and
                     uid==room.owner and decoded['target'] in room.members)
        actor=(decoded['target'] if decoded['id']==8121 else
               decoded.get('attacker',decoded.get('player',uid)))
        if decoded['id']==8288 and not actor:
            engine.record_unknown(c,ident,p);return []
        if decoded['id'] in (8284,8293) and actor not in room.fighters:
            #The native ownership query has a virtual-slot/Host extension.
            #Untracked special actors are unsupported, not proven forgery.
            engine.record_unknown(c,ident,p);return []
        if decoded['id'] in (8121,8150) and actor!=uid and not host_cancel:
            # Native reflection, attacker buffs and aura producers can
            # legitimately name another actor. Unsupported authority is
            # not a forged connection identity: retain the session and
            # do not forward, mutate a ledger or consume sequence.
            self._count_combat(decoded,'unsupported_actor')
            engine.record_unknown(c,ident,p)
            return []
        require(actor==uid or host_cancel,'relay actor spoof')
        # Source is attribution, not authentication. Native receivers
        # permit0/missing sources (environment, despawned NPC/effects).
        # Owned target and bound transport sender are checked separately.
        if decoded['id']==8150:
            # Definition lookup consumes a byte code. This local admission
            # bound also limits the per-room ledger to at most8*256 keys.
            require(decoded['ustate_code']<=255,'state code outside native byte registry')
        if decoded['id'] in (8143,8280):
            # Zero means no target. Never redirect an unknown UID to hub.
            require(decoded['target']==0 or decoded['target'] in room.members,
                    'relay target outside current room')
        if 'room_pair' in decoded:
            require(decoded['room_pair']==(room.number,room.serial),'relay stale battle')
        # Check retransmission before a one-use authorization is consumed;
        # repeated8401 must not become a spoof after its witness is retired.
        header=(decoded['flag'],decoded['header_variant'])
        if header==(0,0) and decoded['id']==8120:lane=15
        elif header==(1,1):lane=19
        else:
            self._count_combat(decoded,'unsupported_header')
            engine.record_unknown(c,ident,p);return []
        sequence=decoded[f'sequence_{lane}_raw'];key=(uid,lane)
        previous=room.last_sequence.get(key,-1)
        if lane==15:
            #7D1520: new action increments+15, continuation reads it.
            #19..22 is not written by this producer. Do not treat a change
            #there as a new position, or discard a real same-action update.
            motion=p[:19]+p[23:]
            if sequence<previous or (sequence==previous and room.motion_payloads.get(uid)==motion):
                self._count_combat(decoded,'sequence_stale')
                return []
        elif sequence<=previous:
            self._count_combat(decoded,'sequence_stale')
            return []
        if decoded['id']==8287:
            require(uid==room.owner,'collectible spawn requires elected Host')
            if decoded['spawn_family']=='unknown':
                engine.record_unknown(c,ident,p);return []
            spawn_key=(decoded['spawn_family'],decoded['object_id'])
            if spawn_key in room.spawned_collectibles:return []
            require(len(room.spawned_collectibles)<65536,'collectible registry capacity')
        if decoded['id']==8400:
            source=decoded['source']
            require(source==uid or (uid==room.owner and (source==0 or source in room.members)),
                    'projectile source not owned or Host-delegated')
            prior=room.projectiles.get(decoded['child_key'])
            if prior is not None:
                require(prior['owner']==uid,'projectile key owned by another connection')
                return []  # No native duplicate creation or within-round key resurrection.
            require(len(room.projectiles)<65536,'projectile registry capacity')
        elif decoded['id'] in (8401,8402,8403,8404):
            prior=room.projectiles.get(decoded['child_key'])
            if prior is None or not prior['alive']:
                return []  # Do not guess ownership of a peer-only/unobserved object.
            if decoded['id']==8402:
                if decoded['target']!=uid:
                    engine.record_unknown(c,ident,p);return []
            elif decoded['id']==8401:
                if decoded['result_state_raw'] not in (2,3,4,6):
                    engine.record_unknown(c,ident,p);return []
                #9953B0 emits8401/state2 after the local hit report reaches
                #its collision limit. This is a target witness, not a grant
                #to remove/redirect someone else's projectile.
                require(prior['owner']==uid or (decoded['result_state_raw']==2 and
                        uid in prior['hit_reporters']),'projectile result without owner/hit witness')
            else:
                require(prior['owner']==uid,'projectile event from non-owner')
        # All27 discovered8150 producers emit apply1 or cancel0. Keep the
        # decoder lossless, but do not forward an unqualified operation or
        # let it consume the sender's generic-event sequence watermark.
        if decoded['id']==8150 and decoded['operation']=='unknown':
            engine.record_unknown(c,ident,p)
            return []
        if decoded['id']==8150 and decoded['operation']=='cancel':
            require(decoded['source']==0 and decoded['parameter_signed']==0 and decoded['event_raw']==0,
                    'state cancel must use native zero source/parameters')
            if actor!=uid and (actor,decoded['ustate_code']) not in room.active_states:
                return []  # Host cannot cancel a state this service never admitted.
        # SYSTEM_DESIGN_INFERRED / PROVISIONAL: bounded local TCP replay
        # filter, NOT the native P2P receive algorithm. 7D1520 movement
        # writes +15, while 7D1730 generic events write +19. Never compare
        # the two counters or invent a counter for an unknown header.
        room.last_sequence[key]=sequence
        if lane==15:room.motion_payloads[uid]=motion
        if room.request[46]==10 and room.pve and decoded['id']==8120:
            room.pve.player_position(uid,decoded,p)
        if decoded['id']==8143:
            hub.observe_pair_selection(engine,decoded)
        if decoded['id']==8287:
            room.spawned_collectibles[spawn_key]=bytes(p[39:])
        if (decoded['id']==8121 and hub.combat_catalog is not None and
                hub.combat_catalog.permits_effect_free_receipt(decoded['skill_property_id']) and
                decoded['source'] in room.members and decoded['source']!=uid and
                decoded['callback_mode']==0):
            pending=room.pending_hit_receipts.setdefault(uid,[])
            outcomes=hub.combat_catalog.receipt_outcomes(decoded['skill_property_id'],
                decoded['hit_result_status'],decoded['attack_callback_flag'])
            pending.append((decoded['source'],decoded['skill_property_id'],decoded['hit_result_status'],sequence,outcomes))
            del pending[:-32]  # Explicit local bounded queue, not a guessed time window.
        if decoded['id']==8400:
            room.projectiles[decoded['child_key']]=dict(owner=uid,source=decoded['source'],
                definition_id=decoded['definition_id'],scene_id=decoded['scene_id_raw'],alive=True,hit_reporters=set())
        elif decoded['id']==8402:
            room.projectiles[decoded['child_key']]['hit_reporters'].add(uid)
        elif decoded['id']==8401 and uid!=room.projectiles[decoded['child_key']]['owner']:
            room.projectiles[decoded['child_key']]['hit_reporters'].discard(uid)
        elif decoded['id']==8404:
            room.projectiles[decoded['child_key']]['alive']=False
        if decoded['id']==8150:
            state_key=(decoded['target'],decoded['ustate_code'])
            if decoded['operation']=='apply':
                room.active_states[state_key]=(decoded['source'],decoded['parameter_signed'],decoded['event_raw'])
            else:
                room.active_states.pop(state_key,None)
        hub.broadcast(room,Message(8071,p),exclude=uid)
        return engine.take_pending(c)
