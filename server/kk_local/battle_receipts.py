"""BattleReceipts: local service policy; RoomHub remains the only room state owner."""
import struct
from .wire import Message


class BattleReceipts:
    def __init__(self, hub):
        self.hub = hub

    def handle_death_notice(self,engine,c,decoded,payload):
        """Native terminal-ready and remote countdown UI, not server respawn."""
        hub = self.hub
        room=engine.room;uid=c.uid;require=engine.require
        require(c is engine.game and uid in room.fighters and
                room.members[uid].engine is engine,'death notice connection mismatch')
        require(decoded['sender']==uid,'death notice sender spoof')
        if (decoded['flag'],decoded['header_variant'])!=(1,1):
            engine.record_unknown(c,8071,payload);return []
        ident=decoded['id']
        target=decoded['target'] if ident==8286 else decoded['player']
        require(target in room.fighters,'death notice target outside fighters')
        if ident==8286:
            #8285E0 explicitly accepts initiator==target or initiator Host.
            require(decoded['initiator']==uid,'death terminal initiator spoof')
            require(target==uid or uid==room.owner,'death terminal requires owner or Host')
        else:
            #9F3330's nonlocal branch emits8278 with+47=1 and signed integer
            #countdown+51.828EB0 only updates the target owner's UI text.
            #Local policy delegates this cross-player UI request to one Host.
            require(uid==room.owner and target!=uid,'countdown requires remote Host target')
            if decoded['opaque_47_51']!=struct.pack('<I',1) or decoded['ui_value_51_raw']>0x7fffffff:
                engine.record_unknown(c,8071,payload);return []
        sequence=decoded['sequence_19_raw'];key=(uid,19)
        if sequence<=room.last_sequence.get(key,-1):return []
        room.last_sequence[key]=sequence
        if ident==8278:hub.queue(room.members[target].engine,Message(8071,payload))
        else:hub.broadcast(room,Message(8071,payload),exclude=uid)
        return engine.take_pending(c)

    def handle_hit_receipt(self,engine,c,decoded,payload):
        """Forward a correlated no-attacker-effect receipt only to its actor.

        Native82B8D0 can generate8150; nonempty/unknown effect lists remain
        closed until their overlap with direct8150 has a separate contract.
        The original receipt also appends Action+0x38 hit events and records
        SkillHitPostTracker state BEFORE dispatching attacker effects. Do not
        replace the entire receipt with a synthetic8150, or execute these
        client-side side effects again in the relay.
        """
        hub = self.hub
        room=engine.room;uid=c.uid;require=engine.require
        require(c is engine.game and uid in room.members and room.members[uid].engine is engine,
                'hit receipt connection mismatch')
        require(decoded['sender']==uid and decoded['reporter']==uid,'hit receipt sender/reporter spoof')
        if (decoded['target']!=uid or decoded['source'] not in room.fighters or
                hub.combat_catalog is None or not hub.combat_catalog.permits_effect_free_receipt(decoded['skill_property_id'])):
            hub._battle._count_combat(decoded,'unsupported_receipt')
            engine.record_unknown(c,8071,payload);return []
        if (decoded['flag'],decoded['header_variant'])!=(1,1):
            hub._battle._count_combat(decoded,'unsupported_header')
            return []
        sequence=decoded['sequence_19_raw'];key=(uid,19)
        if sequence<=room.last_sequence.get(key,-1):
            hub._battle._count_combat(decoded,'sequence_stale')
            return []
        wanted=(decoded['source'],decoded['skill_property_id'],decoded['hit_result_status_raw'])
        pending=room.pending_hit_receipts.get(uid,[])
        index=next((i for i,row in enumerate(pending)
                    if row[:2]==wanted[:2] and wanted[2] in row[4] and row[3]<sequence),None)
        if index is not None:
            del pending[index]
            #97A1F0/97AB50 can emit a second8126/status3 AFTER the normal
            #9790C0 receipt. It is not a retransmission and needs its own
            #one-use witness; never apply HP or create a death on the server.
            if wanted[2] in (1,5):
                terminal=room.pending_death_receipts.setdefault(uid,[])
                terminal.append((wanted[0],wanted[1],sequence))
                del terminal[:-32]
        elif wanted[2]==3:
            terminal=room.pending_death_receipts.get(uid,[])
            terminal_index=next((i for i,row in enumerate(terminal)
                if row[:2]==wanted[:2] and row[2]<sequence),None)
            if terminal_index is None:
                hub._battle._count_combat(decoded,'missing_witness')
                return []
            del terminal[terminal_index]
        else:
            hub._battle._count_combat(decoded,'missing_witness')
            return []
        room.last_sequence[key]=sequence
        if decoded['source']!=uid:
            hub.queue(room.members[decoded['source']].engine,Message(8071,payload))
        return engine.take_pending(c)

    def handle_pickup(self, engine, c, decoded, payload):
        """Correlate native Host reservation replies, never grant persistent items.

        SYSTEM_DESIGN_INFERRED / PROVISIONAL: the elected Host evaluates the
        native world's pickability. One outstanding request per room member.
        """
        hub = self.hub
        room=engine.room;uid=c.uid;require=engine.require
        require(c is engine.game and uid in room.members and room.members[uid].engine is engine,
                'pickup connection/member mismatch')
        require(decoded['sender']==uid,'pickup sender spoof')
        if 'room_pair' in decoded:
            require(decoded['room_pair']==(room.number,room.serial),'pickup stale battle')
        require((decoded['flag'],decoded['header_variant'])==(1,1),'pickup header variant')
        ident=decoded['id'];actor=decoded['player'];item=decoded['pickup_key'];value=decoded['pickup_value_raw']
        requests=room.pickup_requests if ident<9500 else room.chest_requests
        request_id=9000 if ident<9500 else 9500
        require(actor in room.members,'pickup actor outside room')
        sequence=decoded['sequence_19_raw'];sequence_key=(uid,19)
        if sequence<=room.last_sequence.get(sequence_key,-1):return []
        prior=requests.get(actor)
        if ident==request_id:
            require(actor==uid and value in (0,1),'pickup request identity/value')
            if value:
                # Original Host takes its local fast path, with no9000 request.
                if uid==room.owner or prior is not None:return []
                requests[actor]=dict(key=item,status='pending')
            else:
                if prior is None or prior['key']!=item:return []
                del requests[actor]
            room.last_sequence[sequence_key]=sequence
            hub.queue(room.members[room.owner].engine,Message(8071,payload))
        elif ident==request_id+1:
            require(uid==room.owner and value in (0,1),'pickup response requires Host and boolean result')
            if prior is None or prior['key']!=item or prior['status']!='pending':return []
            if value:
                require(not any(who!=actor and r['key']==item and r['status']=='approved'
                                for who,r in requests.items()),'pickup already reserved')
                prior['status']='approved'
            else:
                del requests[actor]
            room.last_sequence[sequence_key]=sequence
            hub.broadcast(room,Message(8071,payload),exclude=uid)
        else:
            require(actor==uid and value==1,'pickup completion identity/value')
            if uid!=room.owner:
                require(prior is not None and prior['key']==item and prior['status']=='approved',
                        'pickup completion without Host approval')
            else:
                require(not any(r['key']==item and r['status']=='approved'
                                for r in requests.values()),'Host pickup conflicts with reservation')
            requests.pop(actor,None)
            room.last_sequence[sequence_key]=sequence
            hub.broadcast(room,Message(8071,payload),exclude=uid)
        return engine.take_pending(c)
