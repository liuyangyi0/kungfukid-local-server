"""Native ranking fields, with explicitly local descending-score/UID tie policy."""
import heapq
import struct
from .wire import Message,ProtocolError

SCORE_OFFSETS={0:(249,),1:(137,145,153,161),2:(129,),3:(137,),4:(145,),
               5:(153,),6:(161,),7:(181,),8:(185,),9:(189,),10:(193,),11:(197,),12:(201,)}

def profile_score(profile,category):
    if type(category) is not int or category not in SCORE_OFFSETS or len(profile)!=360:
        raise ProtocolError('unsupported ranking category/profile')
    total=sum(struct.unpack_from('<i',profile,offset)[0] for offset in SCORE_OFFSETS[category])
    return ((total+(1<<31))%(1<<32))-(1<<31)  # same 32-bit addition as 88D250

def local_rankings(rows,category,actor,actor_profile):
    """One bounded-memory scan; only public names/stats, no account credentials."""
    actor_key=(-profile_score(actor_profile,category),actor)
    rank=0;seen=False
    def candidates():
        nonlocal rank,seen
        for uid,nickname,profile in rows:
            score=profile_score(profile,category)
            name=nickname.encode('gbk')
            if not 1<=len(name)<=20 or b'\0' in name:raise ProtocolError('ranking nickname')
            key=(-score,uid)
            seen |= uid==actor
            rank += key<actor_key
            yield key,name,score
    # Local ordering is provisional, not an assertion about the old service.
    selected=heapq.nsmallest(100,candidates(),key=lambda x:x[0])
    if not seen:raise ProtocolError('ranking actor missing')
    if rank>0x7ffffffe:raise ProtocolError('ranking range')
    payload=b''.join(name.ljust(21,b'\0')+bytes([i])+struct.pack('<iB',score,category)
                     for i,(_,name,score) in enumerate(selected))
    return Message(2550,payload),Message(2570,struct.pack('<Bi',category,rank))
