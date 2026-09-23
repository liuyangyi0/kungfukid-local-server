"""Bounded ingress fairness and aggregate metadata; never an identity check."""
from collections import OrderedDict,Counter
from dataclasses import dataclass
import time

@dataclass(frozen=True)
class DatagramPolicy:
    unknown_rate:int=64
    unknown_burst:int=128
    global_rate:int=256
    global_burst:int=512
    source_limit:int=512
    source_idle:int=60
    bound_rate:int=400
    bound_burst:int=800
    bound_limit:int=32
    summary_seconds:int=10
    def __post_init__(self):
        if any(type(v) is not int or not 1<=v<=65536 for v in vars(self).values()):raise ValueError('invalid datagram policy')

class Bucket:
    def __init__(self,burst,now):self.tokens=float(burst);self.at=now;self.seen=now
    def take(self,rate,burst,now):
        now=max(self.at,now);self.tokens=min(burst,self.tokens+(now-self.at)*rate);self.at=self.seen=now
        if self.tokens<1:return False
        self.tokens-=1;return True

class IngressBudget:
    def __init__(self,*,policy=None,clock=time.monotonic):
        self.policy=policy or DatagramPolicy();self.clock=clock
        self.sources=OrderedDict();self.bound=OrderedDict();self.global_unknown=Bucket(self.policy.global_burst,clock())
    def allow(self,peer,owner=None):
        p=self.policy;now=self.clock();table=self.bound if owner is not None else self.sources
        while table and now-next(iter(table.values())).seen>=p.source_idle:table.popitem(last=False)
        key=(owner,peer) if owner is not None else peer[0]
        rate,burst=(p.bound_rate,p.bound_burst) if owner is not None else (p.unknown_rate,p.unknown_burst)
        if key not in table:
            if len(table)>=(p.bound_limit if owner is not None else p.source_limit):table.popitem(last=False)
            table[key]=Bucket(burst,now)
        table.move_to_end(key)
        if not table[key].take(rate,burst,now):return False
        return owner is not None or self.global_unknown.take(p.global_rate,p.global_burst,now)

class RejectionSummary:
    REASONS=frozenset(('preauth_budget','invalid_record','identity_or_phase','native_shape','direct_not_advertised','other'))
    def __init__(self,emit,*,clock=time.monotonic,seconds=10):
        self.emit=emit;self.clock=clock;self.seconds=seconds;self.at=clock();self.counts=Counter()
    def add(self,reason):self.counts[reason if reason in self.REASONS else 'other']+=1
    def flush(self,*,force=False):
        now=self.clock()
        if not force and now-self.at<self.seconds:return
        if self.counts:self.emit(dict(event='native_udp_rejection_summary',counts=dict(self.counts)))
        self.counts.clear();self.at=now
