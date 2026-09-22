"""SDP2P 0.7.2.8 connection/relay envelopes from native consumers.

Wire meanings are separate from the PROVISIONAL host-only routing policy.
Sources: protocol document; local sdp-peer-consumers/main-peer-consumers evidence.
This transports peer bytes, never fabricates latency replies or combat results.
"""
from dataclasses import dataclass
from ipaddress import IPv4Address
import struct
import time

from .wire import ProtocolError, sdp_header, GameDecoder
from .layouts import decode_battle


def header(ident, session, source, dest, flags=0):
    return struct.pack('<HHIIIIHBB',1,ident,session,0,source,dest,0,flags,0)


def connect_fields(body):
    # 10008720/10008790: packed14 wire bytes, aligned16-byte native object.
    if len(body)!=14: raise ProtocolError('SDP connect payload length')
    target_or_session, local_address, port, ticket_or_target = struct.unpack('>IIHI',body)
    # Sender serializes the raw inet_addr DWORD, not a normal IPv4 BE integer.
    address = str(IPv4Address(struct.pack('<I',local_address)))
    return target_or_session,address,port,ticket_or_target


def peer_info(peer_id, endpoint, ticket):
    # 10008690/10008610: 20 wire bytes ->24 native bytes. ACK handlers call
    # htonl on address fields, unlike CONNECT_REQ's local raw inet_addr DWORD.
    ip,port=endpoint
    address=int(IPv4Address(ip))
    return struct.pack('>IIHIHI',peer_id,address,port,address,port,ticket)


def decode_relay(data):
    ident,session,source,dest,body,extra=sdp_header(data)
    if ident!=1008 or dest or data[22] not in (0,1) or not extra or extra%4 or extra>64 or not body:
        raise ProtocolError('SDP relay shape')
    targets=struct.unpack_from('<'+str(extra//4)+'I',data,24)
    if any(not target for target in targets) or len(set(targets))!=len(targets):
        raise ProtocolError('SDP relay target list')
    return session,source,targets,data[22],body


@dataclass
class PendingConnect:
    origin: object
    target: object
    room: object
    origin_session: int
    target_session: int
    origin_endpoint: tuple
    ticket: int
    expires: float


class SdpPeerRouter:
    """Opt-in lab routing among current RoomHub members only.

    Socket endpoints come from observed packet sources, never an arbitrary
    client-supplied address. No Internet relay, cross-room traffic, or fake ACKs
    for peers that do not exist. Connection policy is local server design.
    """
    def __init__(self, hub, clock=time.monotonic):
        self.hub=hub
        self.clock=clock
        self.services={}
        self.pending={}
        self.rates={}

    def attach(self, service):
        if (service.lab_endpoint is None or service.engine is None
                or service.engine.hub is not self.hub or service.account_uid in self.services):
            raise ValueError('P2P router requires unique host-only shared-room endpoints')
        self.services[service.account_uid]=service

    def _active(self, service):
        e=service.engine
        if (e is None or e.game is None or e.game.phase.value not in ('room','loading','wait_ready','battle')
                or not e.p2p_alive() or not e.p2p.get('bound') or e.room is None
                or e.account_uid not in e.room.members
                or e.room.members[e.account_uid].engine is not e):
            raise ProtocolError('SDP peer requires current room member and lease')
        return e

    def _target(self, source, peer_id):
        targets=[s for s in self.services.values() if s.engine.p2p and s.engine.p2p['player']==peer_id]
        if len(targets)!=1 or targets[0] is source:
            raise ProtocolError('SDP target unavailable')
        target=targets[0]
        a,b=self._active(source),self._active(target)
        if a.room is not b.room or target.udp is None:
            raise ProtocolError('SDP target outside current room')
        return target

    def _emit(self, target, ident, source_peer, body, flags=0, endpoint=None):
        lease=target.engine.p2p
        data=header(ident,lease['session'],source_peer,lease['player'],flags)+body
        target.udp.sendto(data,lease['peer'] if endpoint is None else endpoint)
        target.event('udp_peer_tx',id=ident,size=len(data),source_peer=source_peer)

    def handle(self, service, data, peer):
        ident,session,source,dest,body,extra=sdp_header(data)
        if ident not in (1003,1006,1008):return False
        e=self._active(service);lease=e.p2p
        if (not service.accepts_peer(peer) or source!=lease['player'] or session!=lease['session']
                or dest or struct.unpack_from('<I',data,8)[0] or struct.unpack_from('<H',data,20)[0]):
            raise ProtocolError('SDP sender lease/header mismatch')
        now=self.clock()
        start,count=self.rates.get(e.account_uid,(now,0))
        if now-start>=1:start,count=now,0
        if count>=200:raise ProtocolError('SDP peer rate limit')
        self.rates[e.account_uid]=(start,count+1)
        self.pending={key:value for key,value in self.pending.items() if value.expires>now}
        if ident==1008:
            if peer!=lease['peer']:raise ProtocolError('SDP relay must use registered socket')
            _,_,ids,flags,payload=decode_relay(data)
            # Validate all recipients before sending any bytes.
            targets=[self._target(service,target_id) for target_id in ids]
            for target in targets:self._emit(target,1009,source,payload,flags)
            self.observe_selection(e,targets,payload)
            service.event('udp_peer_relay',id=1008,targets=len(targets),size=len(payload))
            return True
        if extra or data[22]:raise ProtocolError('SDP connect control flags')
        first,address,port,last=connect_fields(body)
        # Host-only fixture: no NAT rewriting. Fail rather than advertise a
        # caller-chosen endpoint outside the explicitly admitted VM/socket.
        if (address,port)!=peer:raise ProtocolError('SDP advertised socket mismatch')
        if ident==1003:
            target=self._target(service,first)
            if not last:raise ProtocolError('SDP connect ticket is zero')
            key=(source,first)
            if key not in self.pending and len(self.pending)>=64:
                raise ProtocolError('SDP pending connection limit')
            self.pending[key]=PendingConnect(service,target,e.room,session,target.engine.p2p['session'],
                                             peer,last,now+30)
            #1005 CONNECT_FORWARD_REQ carries the initiator's ticket; the
            # target's native handler then sends1006 and starts its own punch.
            self._emit(target,1005,0,peer_info(source,peer,last))
        else:
            if first!=session:raise ProtocolError('SDP connect acknowledgement session mismatch')
            origin=self._target(service,last)
            pending=self.pending.get((last,source))
            if (pending is None or pending.origin is not origin or pending.target is not service
                    or pending.room is not e.room or pending.origin_session!=origin.engine.p2p['session']
                    or pending.target_session!=session):
                raise ProtocolError('SDP connect acknowledgement has no current request')
            # The initiator used a per-peer socket for1003, not necessarily
            # its login/control socket. Reply to that observed request source.
            self._emit(origin,1004,0,peer_info(source,peer,pending.ticket),endpoint=pending.origin_endpoint)
        service.event('udp_peer_control',id=ident,source_peer=source)
        return True

    def observe_selection(self,engine,targets,payload):
        """Read the known main-game envelope after relay; no extra forwarding.

        Small complete frames only. Unknown/fragmented peer bytes remain opaque.
        This never advances TCP battle watermarks or decodes authentication.
        """
        if engine.room is None or engine.room.stage not in ('loading','battle') or len(payload)>1024:return
        try:
            decoder=GameDecoder();messages=decoder.feed(payload);decoder.eof()
            if len(messages)>8:return
            observations=[]
            for message in messages:
                if message.id==8071 and len(message.payload)==59 and struct.unpack_from('<I',message.payload)[0]==8143:
                    observations.append(decode_battle(message.payload))
                elif message.id==8071 and len(message.payload)>=39 and struct.unpack_from('<I',message.payload)[0] in (8294,8295,8296,8297):
                    observations.append(decode_battle(message.payload))
                elif message.id==8071 and len(message.payload)==75 and struct.unpack_from('<I',message.payload)[0] in (8291,8292):
                    observations.append(dict(talisman_message=message))
                elif message.id==8071 and len(message.payload)>=39 and struct.unpack_from('<I',message.payload)[0] in (20400,20401,20403,20404,20405,20407):
                    observations.append(dict(pve_message=message))
                elif engine.room.request[46]==10 and message.id==8071 and len(message.payload)>=39 and struct.unpack_from('<I',message.payload)[0] in (8120,8121):
                    observations.append(dict(foster_combat=message))
        except ProtocolError:return
        recipients={target.engine.account_uid for target in targets}
        for observation in observations:
            if observation is None:continue
            if 'foster_combat' in observation:
                from .foster import observe_combat
                try:observe_combat(self.hub,engine,engine.game,observation['foster_combat'])
                except ProtocolError:pass
            elif 'pve_message' in observation:
                from .pve import lifecycle
                try:lifecycle(self.hub,engine,engine.game,observation['pve_message'],observed=True)
                except ProtocolError:pass
            elif 'talisman_message' in observation:
                from .talisman import handle as talisman_use
                try:talisman_use(self.hub,engine,engine.game,observation['talisman_message'],observed_recipients=recipients)
                except ProtocolError:pass  # relay already validated; side observation is not new authority
            elif observation['id']==8143:self.hub.observe_pair_selection(engine,observation,recipients=recipients)
            else:self.hub.observe_series_event(engine,observation,recipients=recipients)
