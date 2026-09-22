"""Provisional shared room coordinator; no public authentication or combat authority.

Each explicitly configured offline account owns an Engine and endpoint set.
The hub owns rooms, readiness barriers and recipient selection, never client HP.
"""
from dataclasses import dataclass, field
from collections import OrderedDict
import struct

from . import packets
from .wire import Message, ProtocolError
from .room_lifecycle import RoomLifecycle
from .room_flow import RoomFlow
from .room_requests import RoomRequests
from .battle_relay import BattleRelay
from .battle_pairs import BattlePairs
from .battle_receipts import BattleReceipts
from .battle_results import BattleResults
from .team_series import SeriesProgress


@dataclass
class Member:
    engine: object
    slot: int
    team: int
    ready: bool = False
    spectator: bool = False
    activity: int = 0  # Native3550 room label, never a battle action/state.
    registry_key: int | None = None  # room presentation index, not PlayerRegistry slot
    network_delay_ms: int = 0  # 4080+13+slot*4 -> CPlayer.GetNetDelay


@dataclass
class SharedRoom:
    owner: int
    request: bytes
    number: int
    serial: int = 0
    stage: str = 'room'
    members: dict = field(default_factory=dict)
    loaded: set = field(default_factory=set)
    input_ready: set = field(default_factory=set)
    last_sequence: dict = field(default_factory=dict)
    motion_payloads: dict = field(default_factory=dict)
    active_states: dict = field(default_factory=dict)
    pair_selections: dict = field(default_factory=dict)
    pair_selection_versions: dict = field(default_factory=dict)
    projectiles: dict = field(default_factory=dict)
    pickup_requests: dict = field(default_factory=dict)
    chest_requests: dict = field(default_factory=dict)
    pending_hit_receipts: dict = field(default_factory=dict)
    pending_death_receipts: dict = field(default_factory=dict)
    spawned_collectibles: dict = field(default_factory=dict)
    resolved_request: bytes | None = None
    result_reports: dict = field(default_factory=dict)
    result_requests: set = field(default_factory=set)
    result_replies: dict = field(default_factory=dict)
    result_acks: set = field(default_factory=set)
    series: SeriesProgress | None = None
    battle_clock_origin: float | None = None
    battle_clock_last: int = 0
    network_probe: dict | None = None
    seat_exchange: dict | None = None
    talisman_pending: dict = field(default_factory=dict)
    talisman_sequences: dict = field(default_factory=dict)
    pve: object | None = None
    tutorial_pending: bool = False
    public_deadline: float = 0
    battle_dispatch_cache: OrderedDict = field(default_factory=OrderedDict)

    @property
    def fighters(self):
        return {uid:m for uid,m in self.members.items() if not m.spectator}


class RoomHub:
    def __init__(self, *, lab_no_award_settlement=True, match_point_rewards=None, combat_catalog=None, spectator_capacity=0, team_series_rounds=0, network_probe=False,talisman_catalog=None):
        self.engines = {}
        self.public_policy=None
        self.permanent_battle_rewards_allowed=True
        self.rooms = {}
        self.next_p2p_id = 1001
        self.suspended = {}
        self.invites = {}  # one live native invitation dialog per recipient
        self.invite_seconds = 60  # explicit local policy, not an old-server timer
        self.reconnect_seconds = 30  # Explicit local policy, waiting rooms only.
        self.lab_no_award_settlement = lab_no_award_settlement
        if match_point_rewards is not None:
            if (not isinstance(match_point_rewards,dict) or set(match_point_rewards)!={0,1,2} or
                    any(type(k) is not int or type(v) is not int or not 0<=v<=1000000
                        for k,v in match_point_rewards.items())):
                raise ValueError('invalid match point rewards')
            match_point_rewards=dict(match_point_rewards)
        self.match_point_rewards=match_point_rewards
        self.combat_catalog=combat_catalog
        if type(spectator_capacity) is not int or not 0<=spectator_capacity<=8:
            raise ValueError('spectator capacity must be0..8')
        self.spectator_capacity=spectator_capacity
        if type(team_series_rounds) is not int or team_series_rounds not in (0,1,3,5,7):
            raise ValueError('team series rounds must be0/1/3/5/7')
        if team_series_rounds and (spectator_capacity or match_point_rewards is not None):
            raise ValueError('experimental series excludes spectators and persistent rewards')
        self.team_series_rounds=team_series_rounds
        self.network_probe_enabled=bool(network_probe)
        self.talisman_catalog=talisman_catalog
        self._lifecycle=RoomLifecycle(self)
        self._flow=RoomFlow(self)
        self._requests=RoomRequests(self)
        self._battle=BattleRelay(self)
        self._pairs=BattlePairs(self)
        self._receipts=BattleReceipts(self)
        self._results=BattleResults(self)

    @staticmethod
    def start_identity(room):
        return (room.owner,room.request,tuple((u,id(m.engine.game),m.slot,m.team,m.registry_key,m.ready,m.spectator)
                for u,m in sorted(room.members.items())))

    def cancel_network_probe(self,room):
        return self._flow.cancel_network_probe(room)

    def begin_start(self,room):
        return self._flow.begin_start(room)

    def network_delay_reply(self,engine,c,p):
        return self._flow.network_delay_reply(engine, c, p)

    def start_match(self,room):
        return self._flow.start_match(room)

    def observer_limit(self, room):
        # Explicit opt-in local Mode1 slice; request+34 is the watch option.
        return self.spectator_capacity if room.series is None and room.request[46]==1 and room.request[34] else 0

    @staticmethod
    def position_key(room,team,slot,*,exclude=None):
        if room.request[46] not in packets.TEAM_MODES:return slot
        # Native CTeamRoom uses key+8 into roomconfig positions: two banks of
        #four with opposite orientation/ready effects. Assignment within the
        #banks is local policy; stable player/Host slots must not be rewritten.
        used={m.slot if m.registry_key is None else m.registry_key for u,m in room.fighters.items() if u!=exclude}
        return next((key for key in range(4*team,4*team+4) if key not in used),None)

    def advance_barriers(self, room):
        return self._flow.advance_barriers(room)

    def toggle_spectator(self, engine, c, message):
        return self._flow.toggle_spectator(engine, c, message)

    def attach(self, engine):
        if engine.account_uid in self.engines or len(self.engines)>=(self.public_policy.online if self.public_policy else 8):
            raise ValueError('duplicate or excessive offline account endpoints')
        if self.engines and next(iter(self.engines.values())).store is not engine.store:
            raise ValueError('shared rooms require one transactional Store')
        self.engines[engine.account_uid]=engine

    def allocate_p2p_id(self):
        if self.next_p2p_id>=0xffffffff:
            raise ProtocolError('P2P identifier exhausted')
        result=self.next_p2p_id
        self.next_p2p_id+=1
        return result

    @staticmethod
    def queue(engine, message):
        if engine.game is not None:
            engine.enqueue(message)

    def broadcast(self, room, message, *, exclude=None):
        for uid,member in room.members.items():
            if uid!=exclude:
                self.queue(member.engine,message)

    @staticmethod
    def fighter(uid, member):
        e=member.engine
        _,name,profile,inventory=e.store.snapshot(uid)
        if not e.p2p_alive() or not e.p2p.get('bound'):
            raise ProtocolError('member P2P lease unavailable')
        return packets.fighter_snapshot(uid,name,profile,inventory,member.slot,member.team,
                                        e.p2p['player'],ready=member.ready,spectator=member.spectator,registry_key=member.registry_key)

    def install(self, room, engine, *, spectator=False):
        return self._lifecycle.install(room, engine, spectator=spectator)

    def equipment_changed(self, engine):
        return self._lifecycle.equipment_changed(engine)

    def handle_activity(self, engine, c, message):
        return self._flow.handle_activity(engine, c, message)

    def disconnected(self, engine):
        return self._lifecycle.disconnected(engine)

    def expire(self):
        return self._lifecycle.expire()

    def drop_invites(self, uid):
        return self._lifecycle.drop_invites(uid)

    def restore_bound(self, engine):
        return self._lifecycle.restore_bound(engine)

    def leave(self, engine, *, acknowledge=False, departure=None):
        return self._lifecycle.leave(engine, acknowledge=acknowledge, departure=departure)

    def handle(self, engine, c, message):
        ident,p=message.id,message.payload
        self.expire()
        if ident==4124 or (ident==3010 and len(p)==81 and p[46]==4) or (engine.room and engine.room.request[46]==4):
            from .tutorial import handle as handle_tutorial
            answer=handle_tutorial(self,engine,c,message)
            if answer is not None:return answer
        if ident in (3500,3501,3502):
            return self.handle_invitation(engine,c,message)
        if ident==20571 or (ident==8071 and len(p)>=4 and struct.unpack_from('<I',p)[0] in (20400,20401,20403,20404,20405,20407)):
            from .pve import lifecycle
            return lifecycle(self,engine,c,message)
        if ident==4140:return self.network_delay_reply(engine,c,p)
        if ident==4201 or (ident==8071 and len(p)>=4 and struct.unpack_from('<I',p)[0] in (8291,8292)):
            from .talisman import handle as talisman_use
            return talisman_use(self,engine,c,message)
        if ident in (3260,3262,3263):
            from .seat_exchange import handle as exchange_seat
            return exchange_seat(self,engine,c,message)
        if ident==3091:
            return self.toggle_spectator(engine,c,message)
        if ident==3550:
            return self.handle_activity(engine,c,message)
        if ident==4111:
            return self.handle_series_result(engine,c,message)
        if ident in (4110,4115) and self.lab_no_award_settlement:
            if engine.room and engine.room.pve:
                from .pve import result as stage_result
                return stage_result(self,engine,c,message)
            if engine.room and engine.room.series:
                engine.record_unknown(c,ident,p);return []
            return self.handle_lab_result(engine,c,message)
        if ident not in (2260,3010,3070,3075,3110,3140,3200,3230,4030,4031,4051,4060,4160,8040,8071):
            return None
        result = self._requests.handle(engine, c, message)
        if result is not None:
            return result
        result = self._flow.handle(engine, c, message)
        if result is not None:
            return result
        if ident == 8071:
            return self._battle.handle(engine, c, message)
        return None

    def handle_invitation(self, engine, c, message):
        return self._requests.handle_invitation(engine, c, message)

    def observe_series_event(self,engine,decoded,*,recipients=None):
        return self._results.observe_series_event(engine, decoded, recipients=recipients)

    def handle_series_event(self,engine,c,decoded,payload):
        return self._results.handle_series_event(engine, c, decoded, payload)

    def handle_series_result(self,engine,c,message):
        return self._results.handle_series_result(engine, c, message)

    def handle_owned_slip(self,engine,c,decoded,payload):
        return self._pairs.handle_owned_slip(engine, c, decoded, payload)

    def observe_pair_selection(self,engine,decoded,*,recipients=None):
        return self._pairs.observe_pair_selection(engine, decoded, recipients=recipients)

    def handle_pair_transform(self,engine,c,decoded,payload):
        return self._pairs.handle_pair_transform(engine, c, decoded, payload)

    def handle_reborn_sync(self,engine,c,decoded,payload):
        return self._battle.handle_reborn_sync(engine, c, decoded, payload)

    def handle_death_notice(self,engine,c,decoded,payload):
        return self._receipts.handle_death_notice(engine, c, decoded, payload)

    def handle_hit_receipt(self,engine,c,decoded,payload):
        return self._receipts.handle_hit_receipt(engine, c, decoded, payload)

    def handle_pickup(self, engine, c, decoded, payload):
        return self._receipts.handle_pickup(engine, c, decoded, payload)

    def handle_lab_result(self, engine, c, message):
        return self._results.handle_lab_result(engine, c, message)
