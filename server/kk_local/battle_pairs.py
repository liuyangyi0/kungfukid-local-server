"""BattlePairs: local service policy; RoomHub remains the only room state owner."""
from .wire import Message


class BattlePairs:
    def __init__(self, hub):
        self.hub = hub

    def handle_owned_slip(self,engine,c,decoded,payload):
        """8270 ordinary owned component; paired residual movement stays closed."""
        hub = self.hub
        room=engine.room;uid=c.uid;require=engine.require
        require(c is engine.game and uid in room.fighters and room.members[uid].engine is engine,
                'slip connection mismatch')
        require(decoded['sender']==uid,'slip sender spoof')
        if (decoded['flag'],decoded['header_variant'])!=(1,1):return []
        first,second=decoded['first_player'],decoded['second_player']
        if (first,second) not in ((uid,0),(0,uid),(0,0)):
            engine.record_unknown(c,8071,payload);return []
        #977DA0/978000 zero the whole body before conditionally writing each
        #component. A missing UID is not permission to smuggle another vector.
        if ((not first and (any(decoded['first_direction']) or decoded['first_distance']!=0)) or
                (not second and (any(decoded['second_direction']) or decoded['second_distance']!=0))):
            engine.record_unknown(c,8071,payload);return []
        sequence=decoded['sequence_19_raw'];key=(uid,19)
        if sequence<=room.last_sequence.get(key,-1):return []
        room.last_sequence[key]=sequence
        hub.broadcast(room,Message(8071,payload),exclude=uid)
        return engine.take_pending(c)

    def observe_pair_selection(self,engine,decoded,*,recipients=None):
        """Record only a qualified selection; never deliver a second message."""
        hub = self.hub
        if engine.battle_delivery_capture is not None:return
        from .engine import Phase
        room=engine.room;uid=engine.account_uid
        if (room is None or room.stage!='battle' or engine.game is None or
                engine.game.phase!=Phase.BATTLE or uid not in room.fighters or
                room.members[uid].engine is not engine or decoded['id']!=8143 or
                decoded['sender']!=uid or decoded['player']!=uid or
                (decoded['flag'],decoded['header_variant'])!=(1,1)):return
        sequence=decoded['sequence_19_raw']
        if sequence<=room.pair_selection_versions.get(uid,-1):return
        room.pair_selection_versions[uid]=sequence
        target=decoded['target']
        if (target in room.fighters and target!=uid and decoded['target_timeout_raw']==15000 and
                (recipients is None or target in recipients)):
            room.pair_selections[uid]=(target,engine.clock()+15.0)
        else:room.pair_selections.pop(uid,None)

    def handle_pair_transform(self,engine,c,decoded,payload):
        """8143 source selection ->8144 target-owned paired transform.

        No reconstruction of position, collision or state values. Only a
        validated TCP or fully decoded, actually relayed P2P selection counts.
        """
        hub = self.hub
        room=engine.room;uid=c.uid;require=engine.require
        require(c is engine.game and uid in room.fighters and room.members[uid].engine is engine,
                'paired transform connection mismatch')
        require(decoded['sender']==uid,'paired transform sender spoof')
        first,second=decoded['first_player'],decoded['second_player']
        if (first not in room.fighters or second not in room.fighters or first==second or
                second!=uid or (decoded['flag'],decoded['header_variant'])!=(1,1)):
            engine.record_unknown(c,8071,payload);return []
        selection=room.pair_selections.get(first)
        if selection is None or selection[0]!=second:
            engine.record_unknown(c,8071,payload);return []
        if room.members[first].engine.clock()>=selection[1]:
            room.pair_selections.pop(first,None)
            engine.record_unknown(c,8071,payload);return []
        sequence=decoded['sequence_19_raw'];key=(uid,19)
        if sequence<=room.last_sequence.get(key,-1):return []
        room.last_sequence[key]=sequence
        hub.broadcast(room,Message(8071,payload),exclude=uid)
        #9E98F0 links camera/runtime pointers, NOT the8143 identity selector;
        #do not fabricate a reciprocal selection or reset this lease.
        return engine.take_pending(c)
