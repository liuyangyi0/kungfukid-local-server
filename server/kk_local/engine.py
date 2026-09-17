"""Connection-role/phase router; local service rules, not recovered combat code."""
from dataclasses import dataclass
from enum import Enum
import struct
import time
import uuid
from collections import Counter, deque
from . import packets
from .wire import Message, ProtocolError
from .layouts import decode_known, decode_consumption
from .chat import public_text,private_text,chat_reply,system_notice


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


class Engine:
    def __init__(self, store, game_port=8001, p2p_port=8001, clock=time.monotonic, wall_clock=time.time,
                 training_rewards=False, account_uid=1001, hub=None, map_catalog=None):
        self.store, self.game_port, self.p2p_port = store, game_port, p2p_port
        self.clock = clock
        self.wall_clock = wall_clock
        self.training_rewards = training_rewards
        self.account_uid = account_uid
        self.hub = hub
        self.map_catalog = map_catalog
        self.pending_messages = deque()
        self.pending_bytes = 0
        self.delivery_failed = False
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

    def grant_offline_adapter_session(self):
        # Explicit loopback adapter grant. Does not validate real SDO credentials.
        self.grant_until = self.clock() + 120
        if self.bootstrap is None and self.game is None:
            self.pending_handoff = None

    def grant_authenticated_session(self):
        """Called only after the local authentication service verifies a bound PID."""
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
            self.game = None
            if self.hub is None or self.account_uid not in self.hub.suspended:
                self.room = None
            self.p2p = None
            self.consume_intents.clear()
            self.pending_messages.clear()
            self.pending_bytes = 0

    def enqueue(self, message):
        if self.delivery_failed:
            return
        if len(self.pending_messages)>=1024 or self.pending_bytes+len(message.payload)>2*1024*1024:
            self.delivery_failed=True
            self.pending_messages.clear()
            self.pending_bytes=0
            return  # Service pulse closes the slow recipient, never the sender.
        self.pending_messages.append(message)
        self.pending_bytes+=len(message.payload)

    def take_pending(self, c):
        if c is not self.game:
            return []
        out=list(self.pending_messages)
        self.pending_messages.clear()
        self.pending_bytes=0
        return out

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
        self.last_consume_event = None
        self.last_chat_event = None
        ident, p = message.id, message.payload
        self.require(c.phase != Phase.CLOSED, 'closed connection')
        c.command_sequence+=1
        if ident == 0:
            self.require(not p, 'heartbeat body')
            return []
        if ident in (1010, 2010):
            self.require(c.phase == Phase.CONNECTED and len(p) == 96, 'login phase/length')
            uid = struct.unpack_from('<Q', p)[0]
            build = struct.unpack_from('<I', p, 49)[0]
            self.require(uid == self.account_uid and build == 594, 'unsupported adapter identity/build')
            c.uid = uid
            if ident == 1010:
                self.require(self.clock() <= self.grant_until and self.grant_until > 0,
                             'no recent offline SDK grant')
                self.require(self.bootstrap is None and self.game is None, 'one adapter session only')
                self.bootstrap, c.phase = c, Phase.BOOTSTRAP
                return packets.account_packets(self.store.snapshot(uid)[3], self.game_port,gold=self.store.gold_balance(uid))
            self.require(self.pending_handoff is not None and
                         self.pending_handoff[0] == uid and self.clock() <= self.pending_handoff[1],
                         'GS handoff expired or missing')
            self.require(self.game is None, 'GS ownership conflict')
            self.pending_handoff = None
            self.delivery_failed = False
            self.game, c.phase = c, Phase.LOBBY
            return [packets.lobby_context(self.p2p_port)]
        self.require(c.uid != 0, 'message before session')
        if ident == 3320:
            self.require(c is self.bootstrap and c.phase in (Phase.PROFILE, Phase.HANDOFF),
                         'role selection phase')
            self.require(p == struct.pack('<I', 1), 'unexpected role selector')
            if c.phase == Phase.HANDOFF:
                return [Message(3330, p)]
            c.phase = Phase.HANDOFF
            self.pending_handoff = (c.uid, self.clock() + 120)
            return [Message(3330, p), Message(1201, struct.pack('<I', 1))]
        if ident == 1157:
            self.require(len(p) == 0, 'catalog request length')
            return packets.catalog(self.game_port)
        # A retiring bootstrap socket cannot mutate room/session state.
        if c is not self.game:
            self.record_unknown(c, ident, p)
            return []
        if ident==5002 and c.phase in (Phase.LOBBY,Phase.ROOM,Phase.BATTLE):
            try:raw=public_text(p)
            except ProtocolError:return self.chat_rejected('public','invalid_request')
            now=self.clock()
            if now-self.last_public_chat<1:return self.chat_rejected('public','rate_limited')
            self.last_public_chat=now
            reply=chat_reply(c.uid,self.store.nickname(c.uid),raw)
            if self.hub is not None:
                if self.room is not None:
                    self.hub.broadcast(self.room,reply,exclude=c.uid)
                else:
                    for peer in self.hub.engines.values():
                        if peer is not self and peer.game is not None and peer.game.phase==Phase.LOBBY:
                            peer.enqueue(reply)
            self.last_chat_event=dict(event='chat_result',channel='public',outcome='accepted',reason='local_echo')
            return [reply]
        if ident==5000 and c.phase in (Phase.LOBBY,Phase.ROOM,Phase.BATTLE):
            try:recipient,raw=private_text(p)
            except ProtocolError:return self.chat_rejected('private','invalid_request')
            if self.hub is None:return self.chat_rejected('private','routing_unavailable')
            peers=[peer for peer in self.hub.engines.values()
                   if peer is not self and peer.game is not None
                   and peer.game.phase in (Phase.LOBBY,Phase.ROOM,Phase.BATTLE)
                   and peer.store.nickname(peer.account_uid)==recipient]
            if not peers:return self.chat_rejected('private','recipient_unavailable')
            if len(peers)!=1:return self.chat_rejected('private','recipient_ambiguous')
            if peers[0].delivery_failed:return self.chat_rejected('private','recipient_queue_failed')
            now=self.clock()
            if now-self.last_public_chat<1:return self.chat_rejected('private','rate_limited')
            self.last_public_chat=now
            reply=chat_reply(c.uid,self.store.nickname(c.uid),raw,recipient=recipient)
            peers[0].enqueue(reply)
            if peers[0].delivery_failed:return self.chat_rejected('private','recipient_queue_failed')
            self.last_chat_event=dict(event='chat_result',channel='private',outcome='accepted',reason='queued')
            return [reply]
        if ident==9070 and c.phase in (Phase.LOBBY,Phase.ROOM):
            self.require(len(p)==2,'catalog selector length')
            return [packets.encode_catalog(p[0],p[1],self.store.shop_records(p[0],p[1]))]
        if ident in (2540,2560) and c.phase==Phase.LOBBY:
            from .menu_layouts import decode_menu_request
            fields=decode_menu_request(ident,p)
            if fields['rank_category'] not in range(13):
                self.record_unknown(c,ident,p);return []
            # Never use the unresolved request identity to read another profile.
            directory,own=self.store.local_rankings(fields['rank_category'],c.uid)
            return [directory if ident==2540 else own]
        if ident==20546 and c.phase in (Phase.LOBBY,Phase.ROOM):
            self.require(not p,'profile field query length')
            # A2C170 writes the four bytes back to profile+352. Preserve bits;
            # the business meaning and downstream UI flags are not re-invented.
            return [Message(20547,self.store.profile_word(c.uid,352))]
        if ident in (20561,20563,20565) and c.phase==Phase.LOBBY:
            fields=decode_known(ident,p)
            # Only the unconfigured-event branch is supported. This is not an
            # implemented spending leaderboard, season calculation or prize grant.
            if ident==20561:return [packets.inactive_wealth_page(fields['page'])]
            if ident==20563:return [packets.inactive_wealth_self()]
            return [Message(20566,bytes(48))]
        if ident==1540 and c.phase in (Phase.LOBBY,Phase.ROOM):
            self.require(not p,'shop cache query length')
            try:records=self.store.shop_cache_records()
            except ValueError:
                return [system_notice('[本地服务] 商品目录存在冲突或超出容量，暂不可用。')]
            return [Message(1550,b''.join(record.raw for record in records))]
        if ident==1500 and c.phase in (Phase.LOBBY,Phase.ROOM):
            fields=decode_known(ident,p)
            if fields['query_mode']!=0:
                self.record_unknown(c,ident,p);return []  # renewal not qualified
            rows=self.store.shop_item_records(fields['item_kind'],fields['item_id'])
            return [Message(1510,b''.join(r.raw for r in rows))]
        if ident==9006 and c.phase in (Phase.LOBBY,Phase.ROOM):
            try:
                fields=decode_known(ident,p)
                if c.phase!=Phase.LOBBY or fields['actor']!=c.uid:raise ValueError('rename identity/phase')
                old=self.store.rename_local(c.uid,fields['nickname'])
            except ValueError:return [packets.nickname_rejected()]
            return [packets.nickname_changed(c.uid,old,fields['nickname'])]
        if ident in (9040,9041) and c.phase in (Phase.LOBBY,Phase.ROOM):
            self.require(len(p)==169,'purchase request length')
            # Never trust quoted prices, names or recipient fields as authority.
            self.layout_observations[ident]+=1
            if ident==9040:
                operation=f'{self.transaction_namespace}:{c.number}:{c.command_sequence}'
                try:result=self.store.purchase_gold_once(c.uid,operation,p)
                except ValueError:return [packets.purchase_unavailable()]
                return packets.gold_purchase_result(*result)
            return [packets.purchase_unavailable()]
        if ident in (1300,1400) and c.phase in (Phase.LOBBY,Phase.ROOM):
            self.require(len(p)==0,'information query length')
            # Local inbox/renewal notification queues are not configured yet.
            # Native 826C30/8269C0 consume headerless 339/124-byte lists.
            return [Message(ident+10,b'')]
        if ident==20360 and c.phase in (Phase.LOBBY,Phase.ROOM):
            self.require(len(p)==12,'ranked record request length')
            if struct.unpack_from('<Q',p)[0]!=c.uid:
                self.record_unknown(c,ident,p)
                return []  # Other-player lookup and its denial are not qualified.
            return [packets.no_local_ranked_season()]
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
        if ident == 4200 or (ident == 8071 and len(p)>=4 and struct.unpack_from('<I',p)[0]==8289):
            return self.handle_consumption(c,ident,p)
        if self.hub is not None:
            result=self.hub.handle(self,c,message)
            if result is not None:
                return result
        if ident==3075:
            self.require(len(p)==1,'automatic join length')
            return [system_notice('[本地服务] 暂无其他玩家创建的可加入房间。')]
        if ident==3140:
            self.require(len(p)==9,'room removal length')
            return [system_notice('[本地服务] 当前房间没有可移除的其他成员。')]
        if ident==3200:
            self.require(len(p)==48,'room settings length')
            if c.phase!=Phase.ROOM or self.room is None or self.room.owner!=c.uid:
                return [system_notice('[本地服务] 当前不能修改房间设置。')]
            try:updated,reply=packets.update_room_request(self.room.resolved_request or self.room.request,p,map_catalog=self.map_catalog)
            except (ValueError,ProtocolError):return [system_notice('[本地服务] 房间设置无效或地图不可用。')]
            if updated==self.room.resolved_request:return []
            self.room.request=self.room.resolved_request=updated
            return [reply]
        if ident==3230:
            self.require(len(p)==1,'team change length')
            if c.phase!=Phase.ROOM or self.room is None or p[0] not in (0,1):
                return [system_notice('[本地服务] 当前不能切换队伍。')]
            if self.room.team==p[0]:return []
            self.room.team=p[0]
            entry=bytearray(self.room.entry);entry[11]=p[0];self.room.entry=bytes(entry)
            return [Message(3250,struct.pack('<QBB',c.uid,p[0],entry[10]))]
        if ident == 2080:
            self.require(len(p) == 16, 'equip request length')
            if c.phase not in (Phase.LOBBY,Phase.ROOM):
                self.record_unknown(c, ident, p)
                return []
            instance, slot = struct.unpack_from('<II',p)
            if self.hub and self.room and self.room.members[c.uid].ready:
                return []
            try:
                changed = self.store.equip(c.uid,instance,slot)
            except ValueError as exc:
                self.record_unknown(c,ident,p)
                return [packets.equipment_rejection(str(exc))]
            # 0460 replaces the descriptor vector, including the displaced item.
            # 829E70 consumes2090's record at+16; preserve request prefix raw bits.
            if self.hub:
                self.hub.equipment_changed(self)
            return [Message(1120,self.store.snapshot(c.uid)[3]),Message(2090,p+changed)]
        if ident == 2300:
            self.require(len(p) == 4, 'unequip request length')
            if c.phase not in (Phase.LOBBY, Phase.ROOM):
                self.record_unknown(c, ident, p)
                return []
            if self.hub and self.room and self.room.members[c.uid].ready:
                return []
            try:
                changed = self.store.unequip(c.uid, struct.unpack('<I', p)[0])
            except ValueError:
                self.record_unknown(c, ident, p)
                return []
            if changed is None:
                return []
            if self.hub:
                self.hub.equipment_changed(self)
            # 829E20 ->8B3D60 ->9D0A90: instance4 + record68.
            # Crucially, consume2310 BEFORE the absolute inventory snapshot:
            # 9D0A90 reads the OLD slot to clear live weapon/appearance state.
            return [Message(2310, p + changed), Message(1120, self.store.snapshot(c.uid)[3])]
        if ident in (21000, 21002):
            if ident == 21000:
                self.require(len(p) == 8 and struct.unpack('<Q', p)[0] == c.uid,
                             'training query identity/length')
            else:
                self.require(not p, 'training start length')
            minutes, active = self.store.training(c.uid, self.wall_clock(), start=ident == 21002)
            # Querying an inactive state naturally causes the native client to
            # request21002. Only21005 is used for start updates, avoiding a loop.
            reply = 21001 if ident == 21000 else 21005
            return [Message(reply, packets.training_status(c.uid, minutes, active, rewards=self.training_rewards))]
        if ident == 21006 and self.training_rewards:
            self.require(not p, 'training claim length')
            if c.phase not in (Phase.LOBBY, Phase.ROOM):
                return []
            claim = self.store.claim_training(c.uid, self.wall_clock())
            minutes, active = self.store.training(c.uid, self.wall_clock())
            status = packets.training_status(c.uid, minutes, active, rewards=True)
            if claim is None:
                return [Message(21005, status)]  # No fake grant or success.
            # Real profile update precedes the UI acknowledgement.21007 causes
            # native21002 to begin a NEW training interval, not a second claim.
            return [Message(4300, struct.pack('<II', claim['points'], claim['second'])),
                    Message(21007, status)]
        if ident == 3010:
            self.require(self.p2p_alive() and self.p2p.get('bound'), 'P2P not bound')
            if c.phase == Phase.ROOM and self.room and p == self.room.request:
                return []  # Duplicate create must not reinstall a live CPlayer.
            self.require(c.phase == Phase.LOBBY, 'create room phase')
            resolved=packets.resolve_room_request(p,self.map_catalog)
            entry = packets.room_entry(resolved, c.uid,map_catalog=self.map_catalog)
            if p[46] in packets.COMPETITIVE_MODES:
                _,name,profile,inventory=self.store.snapshot(c.uid)
                fighter=packets.fighter_snapshot(c.uid,name,profile,inventory,0,0,self.p2p['player'])
                entry=packets.room_entry_member(resolved,c.uid,1,0,0,fighter,map_catalog=self.map_catalog).payload
            self.room, c.phase = Room(c.uid,p,entry,resolved_request=resolved), Phase.ROOM
            return [Message(3100, entry), Message(3160, struct.pack('<Q', c.uid))]
        if ident == 3110:
            # 954C50/954C70 send an empty request. Native 3115 -> 82D920 ->
            # 82CF60 resets battle/fighters and returns selector6 (lobby).
            # 3130 is a different, 8-byte peer departure notification.
            self.require(not p, 'leave request length')
            if c.phase == Phase.LOBBY and self.room is None:
                return []  # Do not repeat native teardown on duplicate requests.
            self.require(c.phase in (Phase.ROOM, Phase.BATTLE) and self.room is not None,
                         'leave requires a waiting room or battle')
            self.room = None
            self.consume_intents.clear()
            c.phase = Phase.LOBBY
            return [Message(3115)]
        if ident == 4030:
            self.require(not p and self.room is not None, 'start request shape/context')
            self.require(c.uid == self.room.owner and self.p2p_alive(), 'start owner/P2P')
            if c.phase in (Phase.LOADING, Phase.WAIT_READY, Phase.BATTLE):
                return []
            self.require(c.phase == Phase.ROOM, 'start phase')
            if self.room.request[46] in packets.COMPETITIVE_MODES:
                # This endpoint has only one actual member. Do not invent an
                # opponent or start a team match that immediately has no enemy.
                return []
            self.room.serial = self.store.next_battle()
            self.consume_intents.clear()
            c.phase = Phase.LOADING
            return [Message(4050, struct.pack('<Q', c.uid)), Message(4080, packets.battle_start(
                self.room.number, self.p2p['player'], self.room.serial))]
        if ident == 4160:
            self.require(not p, 'load notification length')
            if c.phase in (Phase.WAIT_READY, Phase.BATTLE):
                return []
            self.require(c.phase == Phase.LOADING, 'load notification phase')
            c.phase = Phase.WAIT_READY
            return [Message(4170, struct.pack('<Q', c.uid)), Message(4180)]
        if ident == 8040:
            self.require(len(p) == 14 and self.room is not None, 'ready shape/context')
            room_id, uid = struct.unpack_from('<HQ', p)
            self.require(room_id == self.room.number and uid == c.uid, 'ready identity')
            if c.phase == Phase.BATTLE:
                return []
            self.require(c.phase == Phase.WAIT_READY, 'ready phase')
            c.phase = Phase.BATTLE
            return [Message(8070, packets.battle_ready(self.room.number, self.room.serial))]
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
        # Effects execute in the native client before4200. Never echo8289 to
        # the sender: doing so could replay the effect and consume twice locally.
        details={}
        try:
            if c.phase != Phase.BATTLE or not self.room:
                raise ValueError('consumption outside battle')
            if ident==4200:
                if len(p)!=4:
                    raise ValueError('4200 length')
                instance=struct.unpack('<I',p)[0]
                details['instance']=instance
                _,record=self.store.consumable(c.uid,instance=instance)
                if struct.unpack_from('<H',record,23)[0]==0:
                    raise ValueError('empty consumable')
                # At most the two fixed equipped slots; clear on exit/disconnect.
                # Do not let host wall-time/load delays invalidate a native use.
                self.consume_intents[instance]=True
                self.last_consume_event=dict(event='consume_intent',instance=instance)
                return []
            event=decode_consumption(p)
            details.update(sequence=event['seq'],slot=event['slot'],battle=event['serial'])
            if (event['sender']!=c.uid or event['target']!=c.uid or
                (event['room'],event['serial'])!=(self.room.number,self.room.serial)):
                raise ValueError('consumption identity or battle mismatch')
            instance,_=self.store.consumable(c.uid,slot=event['slot'])
            intent=instance in self.consume_intents
            applied=self.store.consume_once(c.uid,self.room.serial,event['seq'],instance,event['signature'],intent)
            self.consume_intents.pop(instance,None)
            self.last_consume_event=dict(event='consume_applied' if applied else 'consume_duplicate',
                                        instance=instance,slot=event['slot'],sequence=event['seq'],battle=self.room.serial)
            return [packets.consumable_snapshot(c.uid,self.store.consumable_slots(c.uid))]
        except (ValueError,struct.error) as exc:
            self.last_consume_event=dict(event='consume_rejected',reason=str(exc),**details)
            return []
