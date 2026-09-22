"""Connection-role/phase router; local service rules, not recovered combat code."""
from dataclasses import dataclass
from enum import Enum
import struct
import time
import uuid
from collections import Counter, deque
from . import packets
from .wire import Message, ProtocolError
from .layouts import decode_known
from .chat import system_notice
from .handlers import dispatch, social, lobby, directory, session, single_room, consumption


class Phase(str, Enum):
    CONNECTED = 'connected'
    BOOTSTRAP = 'bootstrap'
    PROFILE = 'profile_sent'
    HANDOFF = 'handoff'
    LOBBY = 'lobby'
    ROOM = 'room'
    LOADING = 'loading'
    WAIT_READY = 'wait_ready'
    BATTLE = 'battle'
    CLOSED = 'closed'


@dataclass
class Connection:
    number: int
    phase: Phase = Phase.CONNECTED
    uid: int = 0
    bootstrap_sent: bool = False
    command_sequence: int = 0


@dataclass
class Room:
    owner: int
    request: bytes
    entry: bytes
    number: int = 1
    serial: int = 0
    resolved_request: bytes | None = None
    team: int = 0
    battle_clock_origin: float | None = None
    battle_clock_last: int = 0


class Engine:
    def __init__(self, store, game_port=8001, p2p_port=8001, clock=time.monotonic, wall_clock=time.time,
                 training_rewards=False, account_uid=1001, hub=None, map_catalog=None, advertised_host='127.0.0.1'):
        self.store, self.game_port, self.p2p_port = store, game_port, p2p_port
        self.clock = clock
        self.wall_clock = wall_clock
        self.training_rewards = training_rewards
        self.account_uid = account_uid
        self.advertised_host = advertised_host
        self.hub = hub
        self.map_catalog = map_catalog
        self.pending_messages = deque()
        self.pending_bytes = 0
        self.delivery_failed = False
        self.public_commands=None
        self.queue_budget=None
        store.snapshot(account_uid)
        if hub is not None:
            hub.attach(self)
        self.grant_until = 0.0
        self.bootstrap = None
        self.game = None
        self.room = None
        self.p2p = None
        self.unknown = Counter()
        self.pending_handoff = None
        self.layout_observations = Counter()
        self.consume_intents = {}
        self.last_consume_event = None
        self.transaction_namespace=uuid.uuid4().hex
        self.last_public_chat=float('-inf')
        self.last_chat_event=None
        self.last_chat_notice=float('-inf')
        self.talisman_repair_quote=None
        self.mail_preview=None
        self.mail_failed_delete=None
        self.renewal_quote=None
        self.title_offer=None
        self.quest_offer=None
        self.quest_notified=set()
        self.weapon_upgrade_offer=None

    def grant_offline_adapter_session(self):
        # Explicit loopback adapter grant. Does not validate real SDO credentials.
        self.grant_until = self.clock() + 120
        if self.bootstrap is None and self.game is None:
            self.pending_handoff = None

    def poll_battle_clock(self, c):
        """8090 ->828B90 ->985490 advances native battle+84.9E64E0
        consumes increases for passive MP; do NOT synthesize8127 or edit HP/MP.
        One-second monotonic cadence is local-server policy (native unit=1).
        Shared room deduplication prevents one timer per player doubling time.
        """
        room=self.room
        if c is not self.game or c.phase!=Phase.BATTLE or room is None:return
        if self.hub is not None:
            if room.stage!='battle' or c.uid not in room.members:return
            if room.series and room.series.phase!='playing':return
            now=room.members[room.owner].engine.clock()
        else:now=self.clock()
        if room.battle_clock_origin is None:return
        elapsed=min(0xffffffff,max(0,int(now-room.battle_clock_origin)))
        if elapsed<=room.battle_clock_last:return
        room.battle_clock_last=elapsed
        message=Message(8090,struct.pack('<I',elapsed))
        if self.hub is not None:self.hub.broadcast(room,message)
        else:self.enqueue(message)

    def grant_authenticated_session(self):
        """Trusted admission only: verified local PID or an own-cloud credential."""
        self.grant_until = self.clock() + 120

    @staticmethod
    def require(condition, message):
        if not condition:
            raise ProtocolError(message)

    def disconnect(self, c):
        if self.game is c and self.hub is not None:
            self.hub.disconnected(self)
        c.phase = Phase.CLOSED
        if self.bootstrap is c:
            self.bootstrap = None
        if self.game is c:
            self.talisman_repair_quote=None
            self.mail_preview=None;self.mail_failed_delete=None
            self.renewal_quote=None
            self.title_offer=None
            self.quest_offer=None
            self.quest_notified.clear()
            self.weapon_upgrade_offer=None
            self.game = None
            if self.hub is None or self.account_uid not in self.hub.suspended:
                self.room = None
            self.p2p = None
            self.consume_intents.clear()
            self.pending_messages.clear()
            self.pending_bytes = 0
            if self.queue_budget:self.queue_budget.release(self)

    def enqueue(self, message):
        if self.delivery_failed:
            return
        cap=self.public_commands.policy.per_client_outgoing if self.public_commands else 2*1024*1024
        if len(self.pending_messages)>=1024 or self.pending_bytes+len(message.payload)>cap:
            self.delivery_failed=True
            self.pending_messages.clear()
            self.pending_bytes=0
            if self.queue_budget:self.queue_budget.release(self)
            return  # Service pulse closes the slow recipient, never the sender.
        if self.queue_budget:
            try:self.queue_budget.set(self,self.pending_bytes+len(message.payload)+24*(len(self.pending_messages)+1),group=self.account_uid)
            except ProtocolError:
                self.delivery_failed=True;self.pending_messages.clear();self.pending_bytes=0;self.queue_budget.release(self);return
        self.pending_messages.append(message)
        self.pending_bytes+=len(message.payload)

    def take_pending(self, c):
        if c is not self.game:
            return []
        out=list(self.pending_messages)
        self.pending_messages.clear()
        self.pending_bytes=0
        if self.queue_budget:self.queue_budget.release(self)
        return out

    def cancel_pending(self, ident):
        """Retire an undelivered server control request once its reply arrived."""
        self.pending_messages=deque(m for m in self.pending_messages if m.id!=ident)
        self.pending_bytes=sum(len(m.payload) for m in self.pending_messages)
        if self.queue_budget:self.queue_budget.set(self,self.pending_bytes+24*len(self.pending_messages),group=self.account_uid)

    def profile_ready(self, c, ready):
        if c.phase == Phase.BOOTSTRAP and c.bootstrap_sent and ready:
            _, _, profile, inventory = self.store.snapshot(c.uid)
            c.phase = Phase.PROFILE
            return [Message(1151, profile + inventory)]
        return []

    def register_p2p(self, player, session, peer):
        if self.game is None or self.game.phase == Phase.CLOSED:
            raise ProtocolError('P2P requires the active GS session')
        self.p2p = dict(player=player, session=session, peer=peer,
                        uid=self.game.uid, expires=self.clock() + 60)

    def p2p_alive(self):
        return self.p2p is not None and self.p2p['expires'] >= self.clock()

    def handle(self, c, message):
        if self.public_commands and not self.public_commands.before(self,c,message):return []
        self.last_consume_event = None
        self.last_chat_event = None
        ident, p = message.id, message.payload
        self.require(c.phase != Phase.CLOSED, 'closed connection')
        c.command_sequence+=1
        if ident == 0:
            self.require(not p, 'heartbeat body')
            return []
        result = session.begin(self, c, message)
        if result is not None:
            return result
        self.require(c.uid != 0, 'message before session')
        result = session.bound(self, c, message)
        if result is not None:
            return result
        # A retiring bootstrap socket cannot mutate room/session state.
        if c is not self.game:
            self.record_unknown(c, ident, p)
            return []
        result = dispatch((social.handle, lobby.handle), self, c, message)
        if result is not None:
            return result
        if ident == 1156:
            self.require(len(p) == 12 and self.p2p_alive(), 'P2P binding unavailable')
            uid, handle = struct.unpack('<QI', p)
            self.require(uid == c.uid and handle == self.p2p['player'], 'P2P bind identity')
            self.p2p['bound'] = True
            return self.hub.restore_bound(self) if self.hub else []
        if ident == 2260 and c.phase == Phase.LOBBY and self.hub is None:
            self.require(len(p) == 3, 'room list request length')
            # 824280 accepts8 + N*259 bytes. The current single-session
            # service has no rooms while its sole player is in the lobby.
            # Return a truthful empty directory, not invented joinable rooms.
            self.require(self.room is None, 'lobby room ownership conflict')
            return [Message(2280, bytes(8))]
        result = directory.handle(self, c, message)
        if result is not None:
            return result
        if (self.hub is not None and self.room is not None and
                self.room.members[c.uid].spectator and ident in (4200,8071,4082)):
            return []  # observers never originate combat/consumable effects
        if ident == 4200 or (ident == 8071 and len(p)>=4 and struct.unpack_from('<I',p)[0]==8289):
            return self.handle_consumption(c,ident,p)
        if ident==3091 and self.hub is None:
            self.require(not p,'spectator toggle request length')
            if c.phase!=Phase.ROOM or self.room is None:return []
            # Current local room directory explicitly exposes zero spectator
            #capacity.821560 consumes signed failure at+8 and unlocks the UI;
            #only error0 enters the165+N*68 native role migration path.
            error=-3 if self.hub and self.room.stage!='room' else -2
            return [Message(3092,struct.pack('<Qi',c.uid,error))]
        if self.hub is not None:
            result=self.hub.handle(self,c,message)
            if result is not None:
                return result
        result = single_room.handle(self, c, message)
        if result is not None:
            return result
        self.record_unknown(c, ident, p)
        return []  # No fabricated success, inventory mutation or opaque forwarding.

    def chat_rejected(self, channel, reason):
        # Fixed local wording via native system text, not a guessed denial code.
        self.last_chat_event=dict(event='chat_result',channel=channel,outcome='rejected',reason=reason)
        notices={
            'routing_unavailable':'[本地服务] 当前未启用跨玩家私聊。',
            'recipient_unavailable':'[本地服务] 私聊目标不在线或不可用。',
            'recipient_ambiguous':'[本地服务] 无法唯一确定私聊目标。',
            'recipient_queue_failed':'[本地服务] 私聊未送达，接收端暂不可用。',
            'rate_limited':'[本地服务] 发言过快，请稍后再试。',
        }
        now=self.clock()
        if reason in notices and now-self.last_chat_notice>=1:
            self.last_chat_notice=now
            return [system_notice(notices[reason])]
        return []

    def record_unknown(self, c, ident, payload):
        if ident in (0x0820, 0x047e, 0x1f87, 9040, 9041, 9090, 9091,
                     6000, 6001, 6002, 6003, 6225, 20561, 20563, 20565,
                     1320,1340,1401,1402,1420,1440,2171,2250):
            decoded = decode_known(ident, payload)
            if decoded is not None:
                self.layout_observations[ident] += 1
        key = (c.phase.value, ident, len(payload))
        if key not in self.unknown and len(self.unknown) >= 4096:
            key = ('overflow', -1, 0)
        self.unknown[key] += 1

    def handle_consumption(self,c,ident,p):
        return consumption.handle(self,c,ident,p)
