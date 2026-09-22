"""BattleResults: local service policy; RoomHub remains the only room state owner."""
from .wire import Message
from .team_series import decode_series_report, no_award_series_result
from .lab_settlement import decode_report, consensus_survival, consensus_elimination, consensus_reborn, result_payload


class BattleResults:
    def __init__(self, hub):
        self.hub = hub

    def observe_series_event(self,engine,decoded,*,recipients=None):
        """Read-only transport observation of a native Host/UI transition."""
        hub = self.hub
        from .engine import Phase
        room=engine.room;uid=engine.account_uid
        if (room is None or room.series is None or room.stage!='battle' or
                engine.game is None or engine.game.phase!=Phase.BATTLE or
                uid not in room.fighters or room.members[uid].engine is not engine or
                decoded['sender']!=uid):return None
        event=room.series.observe(decoded,room.owner,room.fighters,recipients)
        if event=='continue':
            room.battle_clock_origin=room.members[room.owner].engine.clock()
            room.battle_clock_last=0
        return event

    def handle_series_event(self,engine,c,decoded,payload):
        hub = self.hub
        room=engine.room;uid=c.uid
        engine.require(c is engine.game and uid in room.fighters and room.members[uid].engine is engine,
                       'series event connection mismatch')
        engine.require(decoded['sender']==uid,'series sender spoof')
        if room.series is None:
            engine.record_unknown(c,8071,payload);return []
        sequence=decoded['sequence_19_raw'];key=(uid,19)
        if sequence<=room.last_sequence.get(key,-1):return []
        event=hub.observe_series_event(engine,decoded)
        if event is None:
            engine.record_unknown(c,8071,payload);return []
        room.last_sequence[key]=sequence
        if event=='ready':hub.queue(room.members[room.owner].engine,Message(8071,payload))
        else:hub.broadcast(room,Message(8071,payload),exclude=uid)
        return engine.take_pending(c)

    def handle_series_result(self,engine,c,message):
        hub = self.hub
        room=engine.room;uid=c.uid
        if room is None or room.series is None or room.request[46]!=1:
            engine.record_unknown(c,message.id,message.payload);return []
        engine.require(c is engine.game and uid==room.owner and room.members[uid].engine is engine,
                       'series result requires current Host')
        report=decode_series_report(message.payload,room.fighters)
        series=room.series
        if series.report is not None:
            engine.require(series.report==report,'changed duplicate series report')
            return []  #4112 tears down the native battle UI: never re-send it.
        if room.stage!='battle' or not series.accepts_final(report):
            engine.record_unknown(c,message.id,message.payload);return []
        names={u:engine.store.snapshot(u)[1] for u in room.fighters}
        teams={u:m.team for u,m in room.fighters.items()}
        payload=no_award_series_result(report,names,teams,room.owner)
        series.report=report;series.phase='result';room.stage='result'
        room.result_acks.clear()
        room.result_replies={u:Message(4112,payload) for u in room.fighters}
        for u,reply in room.result_replies.items():hub.queue(room.members[u].engine,reply)
        return engine.take_pending(c)

    def handle_lab_result(self, engine, c, message):
        """No-award competitive consensus; legacy lab method/option names retained."""
        hub = self.hub
        from .engine import Phase
        room=engine.room
        if room is None or room.request[46] not in (0,1,2,3,16) or c.uid not in room.members:
            engine.record_unknown(c,message.id,message.payload)
            return []
        uid=c.uid
        if message.id==4115:
            engine.require(len(message.payload)==4,'4115 elapsed-time length')
            if uid not in room.result_replies:
                return []
            room.result_acks.add(uid)
            hub.advance_barriers(room)
            return engine.take_pending(c)
        if room.members[uid].spectator:return []  # observer cannot vote or earn a result
        if room.stage not in ('battle','result','room') or (room.stage=='room' and not room.result_replies):
            return []
        roster={member.slot:member_uid for member_uid,member in room.fighters.items()}
        decoded=decode_report(message.payload,roster,room.number,room.serial)
        # If a report arrived before our request was drained, do not send a
        #now-redundant4100 and make the native client build another snapshot.
        engine.cancel_pending(4100)
        prior=room.result_reports.get(uid)
        if prior is not None:
            engine.require(prior==decoded,'4110 changed duplicate report')
            # Native4120 performs teardown; never send it twice to one client.
            return []
        candidate={**room.result_reports,uid:decoded}
        # Local policy: Mode0 is individual survival; slot/team colors do not
        # create allies. Mode1 groups actual room teams. Score/reborn modes do
        # not reuse a remaining-HP winner rule.
        groups={u:(u if room.request[46] in (0,2) else m.team) for u,m in room.fighters.items()}
        if room.request[46]==16:
            outcomes=consensus_reborn(candidate,room.fighters)
        elif room.request[46] in (0,1):
            outcomes=consensus_survival(candidate,groups)
        else:
            outcomes=consensus_elimination(candidate,groups,team_mode=room.request[46]==3)
        replies={}
        if outcomes is not None:
            reborn_reports=candidate[room.owner] if room.request[46]==16 else None
            profiles={target:engine.store.snapshot(target)[2] for target in room.fighters}
            awards={}
            # Validate every outgoing profile before committing a durable grant.
            for target,profile in profiles.items():result_payload(outcomes,target,profile,reborn_reports=reborn_reports)
            quest_tracking=engine.store.quests.has_rules()
            if hub.match_point_rewards is not None or quest_tracking:
                profiles=engine.store.award_match_points(room.serial,outcomes,hub.match_point_rewards or {0:0,1:0,2:0},
                    mode=room.request[46],quest_templates=getattr(engine.map_catalog,'quest_templates',{}))
                awards=engine.store.match_point_awards(room.serial)
            for target,m in room.members.items():
                replies[target]=Message(4120,result_payload(outcomes,target,profiles.get(target),point_awards=awards,observer=m.spectator,reborn_reports=reborn_reports))
        room.result_reports=candidate
        #967780 gates the spontaneous4110 on CPlayer+1B74 (the elected Host).
        #82A9B0/selector9 handles server4100 by sending4110 unconditionally.
        #Request each missing fighter once; retain all-peer HP/score checks.
        if outcomes is None and room.owner in candidate:
            for target,m in room.fighters.items():
                if target not in candidate and target not in room.result_requests:
                    room.result_requests.add(target)
                    hub.queue(m.engine,Message(4100))
        if replies:
            room.result_replies=replies
            room.stage='result'
            for target,reply in replies.items():
                hub.queue(room.members[target].engine,reply)
        return engine.take_pending(c)
