"""Own-client cloud admission. Server policy, not a recovered SDK algorithm.

The native96-byte login has a32-byte credential field. A compatible client
adapter must carry the issued credential there; the unmodified SDK has NOT been
qualified to do so. No fallback to UID, source IP, or a fixed fixture account.
"""
from dataclasses import dataclass,field
import hashlib
import hmac
import secrets
import struct

from .auth import AuthError,token_digest
from .engine import Engine
from .rooms import RoomHub
from .wire import ProtocolError


@dataclass(eq=False)
class NativeGrant:
    uid:int
    region:int
    session_digest:bytes
    credential_digest:bytes
    expires:int
    account:str
    udp_credential_digest:bytes=b''
    sdk_credential_digest:bytes=b''
    sdk_connected:bool=False
    sdk_admitted:bool=False
    lobby_ready:bool=False
    engine:object=None
    ready:bool=False
    tcp_peer:tuple|None=None
    udp_peer:tuple|None=None
    transport_id:bytes=b''
    transport_key:bytes=field(default=b'',repr=False)
    transport_connections:set=field(default_factory=set,repr=False)
    transport_udp:object=field(default=None,repr=False)


class NativeAdmission:
    def __init__(self,auth,*,host,game_port,udp_port,sdk_port=0,hub=None,map_catalog=None,limit=8):
        if type(limit) is not int or not 1<=limit<=8:raise ValueError('native hub limit1..8')
        self.auth=auth;self.host=host;self.game_port=game_port;self.udp_port=udp_port
        self.sdk_port=sdk_port
        self.hub=hub or RoomHub();self.map_catalog=map_catalog;self.limit=limit
        self.grants={};self.by_uid={}

    def validate(self,grant):
        if self.grants.get(grant.credential_digest) is not grant:raise AuthError('native_grant_revoked')
        now=int(self.auth.clock());row=self.auth.records.session(grant.session_digest)
        if row is None or row[0]!=grant.uid or row[1]<=now:raise AuthError('invalid_session')
        if grant.engine is None and grant.expires<=now:raise AuthError('native_grant_expired')
        if grant.engine is not None and grant.engine.game is None:
            e=grant.engine
            if e.pending_handoff is not None and e.clock()>e.pending_handoff[1]:raise AuthError('native_handoff_expired')
        return grant

    def prune(self):
        for grant in tuple(self.grants.values()):
            try:self.validate(grant)
            except AuthError:self.release(grant)

    def issue(self,ticket,region_id):
        self.prune()
        if type(region_id) is not int or region_id not in self.auth.regions:raise AuthError('invalid_region')
        key=token_digest(ticket);row=self.auth.records.ticket(key);source=self.auth.records.ticket_session(key)
        if row is None or source is None:raise AuthError('invalid_ticket')
        uid=row[0]
        if uid in self.by_uid:raise AuthError('account_already_online')
        if len(self.grants)>=self.limit:raise AuthError('server_busy')
        account=self.auth.store.snapshot(uid)[0]
        raw=secrets.token_bytes(32);digest=hashlib.sha256(raw).digest()
        udp_raw=secrets.token_bytes(32)
        sdk_raw=secrets.token_bytes(32)
        self.auth.consume_ticket(ticket,uid,region_id)
        grant=NativeGrant(uid,region_id,source[0],digest,int(self.auth.clock())+120,account,
                          hashlib.sha256(udp_raw).digest(),hashlib.sha256(sdk_raw).digest())
        grant.transport_id=secrets.token_bytes(16);grant.transport_key=secrets.token_bytes(32)
        self.grants[digest]=grant;self.by_uid[uid]=grant
        return dict(uid=uid,game_credential=raw.hex(),udp_credential=udp_raw.hex(),expires_at=grant.expires,
                    transport='kk-aesgcm-v1',transport_id=grant.transport_id.hex(),transport_key=grant.transport_key.hex(),
                    sdk_credential=sdk_raw.hex(),sdk_host=self.host,sdk_port=self.sdk_port,
                    session_expires_at=self.auth.records.session(source[0])[1],
                    game_host=self.host,game_port=self.game_port,udp_port=self.udp_port)

    def for_session(self,session,credential):
        token=self.auth._session(session)
        if not isinstance(credential,str) or len(credential)!=64:raise AuthError('invalid_credential')
        try:digest=hashlib.sha256(bytes.fromhex(credential)).digest()
        except ValueError:raise AuthError('invalid_credential') from None
        grant=self.grants.get(digest)
        if grant is None or token[1]!=grant.uid or not hmac.compare_digest(token[0],grant.session_digest):raise AuthError('native_grant_mismatch')
        return self.validate(grant)

    def set_ready(self,session,credential):
        grant=self.for_session(session,credential);grant.ready=True
        return dict(ready=True)

    def claim_sdk(self,credential):
        if not isinstance(credential,bytes) or len(credential)!=32:raise AuthError('sdk_credential_rejected')
        digest=hashlib.sha256(credential).digest()
        grant=next((g for g in self.grants.values() if hmac.compare_digest(g.sdk_credential_digest,digest)),None)
        if grant is None:raise AuthError('sdk_credential_rejected')
        self.validate(grant)
        if grant.sdk_connected or grant.engine is not None:raise AuthError('sdk_connection_already_used')
        grant.sdk_connected=True
        return grant

    def resolve(self,message):
        p=message.payload
        if message.id not in (1010,2010) or len(p)!=96:raise ProtocolError('native login shape')
        uid=struct.unpack_from('<Q',p)[0];build=struct.unpack_from('<I',p,49)[0]
        grant=self.grants.get(hashlib.sha256(p[17:49]).digest())
        if grant is None:raise AuthError('native_credential_rejected')
        self.validate(grant)
        if not grant.sdk_admitted:raise AuthError('sdk_authentication_required')
        # Fixed SDK fields confirm the authenticated account, never select it.
        name=p[73:94].split(b'\0',1)[0]
        if uid!=grant.uid or build!=594 or name.lower()!=grant.account.lower().encode('ascii'):
            raise AuthError('native_identity_mismatch')
        if grant.engine is None:
            if message.id!=1010:raise AuthError('native_bootstrap_required')
            grant.engine=Engine(self.auth.store,self.game_port,self.udp_port,
                                account_uid=uid,hub=self.hub,map_catalog=self.map_catalog,
                                advertised_host=self.host)
            grant.engine.grant_authenticated_session()
        return grant

    def resolve_udp(self,data,peer):
        """Own DLL ticket in the existing129-byte user-data field, not old crypto.

        The field layout is native; KKN1 ticket semantics are explicitly ours.
        A bearer ticket does not provide per-datagram encryption or replay MACs.
        """
        from .wire import sdp_header
        ident,_,_,_,body,extra=sdp_header(data)
        if ident!=1001 or extra or len(body)<141:raise AuthError('udp_credential_rejected')
        n=struct.unpack_from('>H',body)[0]
        if not 1<=n<=20 or len(body)!=141+n:raise AuthError('udp_credential_rejected')
        user_data=body[12+n:]
        if len(user_data)!=129 or user_data[:5]!=b'KKN1:' or any(user_data[69:]):raise AuthError('udp_credential_rejected')
        try:
            text=user_data[5:69].decode('ascii')
            if len(text)!=64 or any(c not in '0123456789abcdef' for c in text):raise ValueError()
            digest=hashlib.sha256(bytes.fromhex(text)).digest()
        except (ValueError,UnicodeError):raise AuthError('udp_credential_rejected') from None
        grant=next((g for g in self.grants.values() if hmac.compare_digest(g.udp_credential_digest,digest)),None)
        if grant is None:raise AuthError('udp_credential_rejected')
        self.validate(grant)
        if (grant.engine is None or grant.engine.game is None or grant.tcp_peer is None or
                peer[0]!=grant.tcp_peer[0] or body[2:2+n].lower()!=grant.account.lower().encode('ascii')):
            raise AuthError('udp_game_binding_mismatch')
        return grant

    def release(self,grant):
        if self.grants.get(grant.credential_digest) is not grant:return
        e=grant.engine
        if e is not None:
            self.hub.leave(e)
            for c in (e.bootstrap,e.game):
                if c is not None:e.disconnect(c)
            self.hub.suspended.pop(grant.uid,None);self.hub.engines.pop(grant.uid,None)
        self.grants.pop(grant.credential_digest,None)
        grant.transport_key=b'';grant.transport_udp=None;grant.transport_connections.clear()
        if self.by_uid.get(grant.uid) is grant:self.by_uid.pop(grant.uid,None)
