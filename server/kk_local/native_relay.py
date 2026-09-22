"""Original SDP2P relay behind a trusted authenticated-datagram admission port.

No NAT hole punching is advertised. The old1001 authentication algorithm is not
guessed: NativeAdmission validates our explicit DLL ticket, not legacy userdata.
"""
import secrets
import socket
import struct
from .auth import AuthError
from .wire import ProtocolError,sdp_header,sdp_reply,encode_game
from .sdp_peer import SdpPeerRouter


class NativeRelay:
    def __init__(self,admission,*,emit,event=None):
        self.admission=admission;self.emit=emit;self.event=event or (lambda **_:None)
        self.peers={};self.rates={};self.observer=SdpPeerRouter(admission.hub)

    def forget(self,grant):
        for key,value in tuple(self.peers.items()):
            if value is grant:self.peers.pop(key,None)
        self.rates.pop(grant.credential_digest,None)
        for key,value in tuple(self.admission.by_player.items()):
            if value is grant:self.admission.by_player.pop(key,None)

    def handle(self,grant,data,peer):
        self.admission.validate(grant);e=grant.engine
        if e is None or e.game is None:raise AuthError('native_udp_before_game')
        ident,session,source,dest,body,extra=sdp_header(data)
        now=e.clock();start,count=self.rates.get(grant.credential_digest,(now,0))
        if now-start>=1:start,count=now,0
        if count>=200:raise ProtocolError('native UDP rate limit')
        self.rates[grant.credential_digest]=(start,count+1)
        if ident==1001:
            if extra or len(body)<141:raise ProtocolError('SDP login length')
            n=struct.unpack_from('>H',body)[0]
            if n>20 or len(body)!=141+n:raise ProtocolError('SDP login text length')
            if grant.udp_peer is not None and grant.udp_peer!=peer:raise AuthError('UDP endpoint reauthorization required')
            if peer in self.peers and self.peers[peer] is not grant:raise AuthError('UDP endpoint occupied')
            if not e.p2p_alive():
                player=self.admission.hub.allocate_p2p_id()
                # A random lease reduces off-path guessing; it is not a MAC.
                e.register_p2p(player,secrets.randbelow(0xffffffff)+1,peer)
            grant.udp_peer=peer;self.peers[peer]=grant
            lease=e.p2p;lease['expires']=now+60
            if self.admission.public_policy:lease.setdefault('confirmed',False)
            self.admission.by_player[lease['player']]=grant
            self.emit(sdp_reply(1002,lease['session'],lease['player'],socket.inet_aton(peer[0]),peer[1]),peer)
            return
        lease=e.p2p
        if (self.peers.get(peer) is not grant or not e.p2p_alive() or lease['peer']!=peer or
                lease['player']!=source or lease['session']!=session):raise AuthError('SDP lease rejected')
        if dest or struct.unpack_from('<H',data,20)[0]:raise ProtocolError('SDP routing header')
        # The server's random native session in1002 is a return-routability
        # challenge. A MAC alone proves the account, not the source UDP port.
        # A subsequent normal control/data packet must echo that session before
        # this endpoint can RECEIVE amplified room fanout.
        if self.admission.public_policy:lease['confirmed']=True
        if ident in (1012,1013):
            if extra or len(body)!=4:raise ProtocolError('SDP control length')
            if ident==1012:e.p2p=None;grant.udp_peer=None;self.forget(grant);return
            lease['expires']=now+60;self.emit(sdp_reply(1014,session,source,bytes(4),peer[1]),peer);return
        if ident in (1003,1006):
            if extra or len(body)!=14:raise ProtocolError('SDP connect length')
            self.event(event='native_direct_not_advertised',id=ident)
            return # Native fallback remains1008, never manufacture a punch ACK.
        if ident!=1008:raise ProtocolError('unsupported SDP control')
        if extra%4 or extra>64 or not body or data[22] not in (0,1):raise ProtocolError('SDP relay shape')
        if not extra:return
        if e.room is None or not lease.get('bound'):raise AuthError('SDP room binding required')
        checked_messages=None
        if self.admission.public_policy:
            checked_messages=e.public_commands.udp(e,body)
            if extra>7*4:raise ProtocolError('public fanout limit')
        ids=struct.unpack_from('<'+str(extra//4)+'I',data,24)
        if not all(ids) or len(set(ids))!=len(ids):raise ProtocolError('SDP duplicate target')
        targets=[]
        for player in ids:
            found=self.admission.by_player.get(player)
            if found is None or found is grant:raise ProtocolError('SDP target missing')
            self.admission.validate(found);other=found.engine
            if other.room is not e.room or not other.p2p_alive() or not other.p2p.get('bound') or found.udp_peer is None:
                raise AuthError('SDP target outside room or lease')
            if self.admission.public_policy and not other.p2p.get('confirmed'):raise AuthError('SDP target reachability unconfirmed')
            targets.append(found)
        if self.admission.public_policy:
            for message in checked_messages:
                deliveries=e.hub._battle.dispatch_public(e,e.game,message,recipients={t.uid for t in targets},udp=True)
                for target in targets:
                    for row in deliveries.get(target.uid,()):
                        packet=bytearray(data[:24]);struct.pack_into('<H',packet,2,1009)
                        struct.pack_into('<I',packet,4,target.engine.p2p['session'])
                        struct.pack_into('<I',packet,16,target.engine.p2p['player']);packet[23]=0
                        self.emit(bytes(packet)+encode_game(row),target.udp_peer)
            return
        for target in targets:
            packet=bytearray(data[:24]);struct.pack_into('<H',packet,2,1009)
            struct.pack_into('<I',packet,4,target.engine.p2p['session'])
            struct.pack_into('<I',packet,16,target.engine.p2p['player']);packet[23]=0
            self.emit(bytes(packet)+body,target.udp_peer)
        self.observer.observe_selection(e,targets,body,messages=checked_messages)
