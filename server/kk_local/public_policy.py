"""Public-test policy: server-owned limits, not client/environment attestation."""
from dataclasses import dataclass,asdict
from collections import Counter,OrderedDict
import json
import time
from .auth import AuthError
from .wire import ProtocolError

@dataclass(frozen=True)
class PublicPolicy:
    online:int=100
    rooms:int=100
    pending_connections:int=64
    pending_per_ip:int=8
    authenticated_connections:int=400
    connections_per_uid:int=4
    tls_seconds:int=5
    header_seconds:int=2
    admission_seconds:int=10
    idle_seconds:int=30
    hash_workers:int=2
    hash_waiters:int=8
    hash_wait_seconds:int=2
    incoming_bytes:int=64*1024*1024
    outgoing_bytes:int=64*1024*1024
    per_client_outgoing:int=2*1024*1024
    messages_per_second:int=200
    bytes_per_second:int=256*1024
    login_ip_rate:int=2
    login_ip_burst:int=20
    login_account_per_minute:int=5
    registration_ip_per_hour:int=5
    registration_global_per_hour:int=120
    registration_global_burst:int=20
    auth_request_rate:int=20
    auth_request_burst:int=40
    session_request_rate:int=10
    session_request_burst:int=20
    expensive_rate:int=2
    expensive_burst:int=4
    database_rate:int=10
    database_burst:int=20
    unknown_rate:int=1
    unknown_burst:int=10
    udp_forward_bytes_per_second:int=16*1024*1024
    udp_control_rate:int=2
    udp_control_burst:int=4
    encode_workers:int=2
    encode_waiters:int=8
    def __post_init__(self):
        if any(type(v) is not int or v<=0 for v in asdict(self).values()):raise ValueError('invalid public policy')
        if not 1<=self.online<=100 or not 1<=self.rooms<=100:raise ValueError('public capacity limit')
        if self.hash_workers!=2 or self.hash_waiters>8:raise ValueError('hash budget limit')
        if self.encode_workers>2 or self.encode_waiters>8:raise ValueError('encode budget limit')
        if self.per_client_outgoing>self.outgoing_bytes:raise ValueError('queue budget order')
    @classmethod
    def load(cls,path):
        from .app.configuration import _unique_object
        from pathlib import Path
        p=Path(path)
        if p.stat().st_size>16384:raise ValueError('policy too large')
        data=json.loads(p.read_text(encoding='utf-8-sig'),object_pairs_hook=_unique_object)
        if set(data)!={'schema','limits'} or data['schema']!='kk-public-security-policy-v1':raise ValueError('policy schema')
        return cls(**data['limits'])
    def capabilities(self):
        return dict(protocol='kk-local-auth-v1',game_transport='kk-aesgcm-v1',native_build=594,
                    registration='invitation',ranked=False,persistent_battle_rewards=False,
                    client_attestation_required=False,vm_required=False,local_firewall_required=False,
                    max_online=self.online,max_room_players=8,max_rooms=self.rooms,
                    modes=[0,1,2,3,5],authority='validated-relay-not-authoritative-combat',
                    transaction_retry='new-native-frame-is-new-intent')

class MemoryBudget:
    def __init__(self,limit,*,per_group=None):self.limit=limit;self.per_group=per_group;self.used=0;self.peak=0;self.owners={};self.groups={};self.group_used=Counter()
    def set(self,owner,size,*,group=None):
        if size<0:raise ValueError('negative reservation')
        old=self.owners.get(owner,0)
        group=self.groups.get(owner,owner if group is None else group)
        if self.used-old+size>self.limit:raise ProtocolError('global buffer budget')
        if self.per_group and self.group_used[group]-old+size>self.per_group:raise ProtocolError('account buffer budget')
        self.used+=size-old;self.peak=max(self.peak,self.used)
        self.group_used[group]+=size-old
        if not self.group_used[group]:self.group_used.pop(group,None)
        if size:self.owners[owner]=size;self.groups[owner]=group
        else:self.owners.pop(owner,None);self.groups.pop(owner,None)
    def release(self,owner):self.set(owner,0)

class Lease:
    def __init__(self,budget,peer):self.budget=budget;self.peer=peer;self.uid=None;self.closed=False
    def promote(self,uid):
        if self.closed:raise AuthError('closed_connection')
        if self.uid is not None:
            if self.uid!=uid:raise AuthError('connection_identity_changed')
            return
        b=self.budget;p=b.policy
        if b.active>=p.authenticated_connections or b.by_uid[uid]>=p.connections_per_uid:raise AuthError('server_busy')
        b.pending-=1;b.by_ip[self.peer]-=1
        if not b.by_ip[self.peer]:del b.by_ip[self.peer]
        self.uid=uid;b.active+=1;b.by_uid[uid]+=1
    def release(self):
        if self.closed:return
        self.closed=True;b=self.budget
        if self.uid is None:
            b.pending-=1;b.by_ip[self.peer]-=1
            if not b.by_ip[self.peer]:del b.by_ip[self.peer]
        else:
            b.active-=1;b.by_uid[self.uid]-=1
            if not b.by_uid[self.uid]:del b.by_uid[self.uid]

class ConnectionBudget:
    def __init__(self,policy):self.policy=policy;self.pending=0;self.active=0;self.by_ip=Counter();self.by_uid=Counter()
    def acquire(self,peer):
        if self.pending>=self.policy.pending_connections or self.by_ip[peer]>=self.policy.pending_per_ip:raise AuthError('server_busy')
        self.pending+=1;self.by_ip[peer]+=1;return Lease(self,peer)

class FairRate:
    """Finite token-bucket cache. Source keys are supplied by the socket owner."""
    def __init__(self,rate,burst,*,limit=4096,clock=time.monotonic):
        self.rate=rate;self.burst=burst;self.limit=limit;self.clock=clock;self.rows=OrderedDict()
    def take(self,key,cost=1):
        now=self.clock();row=self.rows.pop(key,(float(self.burst),now))
        tokens=min(self.burst,row[0]+max(0,now-row[1])*self.rate)
        accepted=tokens>=cost
        self.rows[key]=(tokens-cost if accepted else tokens,max(now,row[1]))
        while len(self.rows)>self.limit:self.rows.popitem(last=False)
        return accepted
