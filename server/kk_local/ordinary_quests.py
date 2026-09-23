"""BaseQuest profile-delta conditions, chained availability and atomic awards.

Native A48AA0 defines deltas; B974A8 labels indices1..8; A4B220 loads source
columns; A48D90 changes state/title; A48EE0 APPENDS a successor. Consensus
counting, overflow policy and configured rewards remain local service design.
"""
import json
import struct
from .wire import Message


def requirements(template):
    f=template.fields
    if template.family!='ordinary' or len(f)!=41:raise ValueError('ordinary source required')
    matches=int(f[5]);combo=int(f[6]);counters=tuple(map(int,f[7:36]));title=int(f[38])
    if (combo or not 0<=matches<=0x7fffffff or not 0<=title<=255 or
            any(not 0<=n<=0x7fffffff or (n and i not in range(1,9)) for i,n in enumerate(counters)) or
            not (matches or any(counters))):
        raise ValueError('ordinary combo/kill/badge condition lacks qualified producer')
    return matches,counters,title,int(f[39])


def tracking_ready(store,templates):
    rows=store.quests.ordinary_rules()
    from .quest_rewards import validate
    for key,definition,rule in rows:
        t=templates.get(('ordinary',key))
        if t is None or t.identity!=definition:continue
        try:validate(t,json.loads(rule))
        except ValueError:continue
        return True
    return False


def add_counters(profile,mode,outcome):
    #B974A8 confirms play/win pairs. Never increment kill/combo/badge fields.
    start=133+mode*8
    for offset in ((start,start+4) if outcome==1 else (start,)):
        n=struct.unpack_from('<I',profile,offset)[0]
        if n<0x7fffffff:struct.pack_into('<I',profile,offset,n+1)


def conditions_met(template,profile,baseline):
    matches,targets,_,_=requirements(template)
    if len(profile)!=360 or len(baseline)!=116:return False
    deltas=[]
    for i in range(29):
        now=struct.unpack_from('<I',profile,129+i*4)[0];old=struct.unpack_from('<I',baseline,i*4)[0]
        deltas.append(now-old if 0<=old<=now<=0x7fffffff else 0)
    return sum(deltas[i] for i in (1,3,5,7))>=matches and all(deltas[i]>=n for i,n in enumerate(targets))


def visible(store,uid,enabled,source):
    parents={}
    for (family,key),t in source.items():
        if family=='ordinary' and int(t.fields[40]):
            child=int(t.fields[39])
            if child:parents.setdefault(child,[]).append(t)
    saved={(family,key):(definition,state) for family,key,definition,state in store.quests.saved_states(uid)}
    result={}
    for identity,t in enabled.items():
        if t.family!='ordinary' or identity in saved or not parents.get(t.key):result[identity]=t;continue
        if any(saved.get(('ordinary',p.key))==(p.identity,3) for p in parents[t.key]):result[identity]=t
    return result


def validate_chains(templates):
    ordinary={k:t for (f,k),t in templates.items() if f=='ordinary' and int(t.fields[40])}
    for first in ordinary:
        seen=set();key=first
        while key:
            if key in seen or key not in ordinary:raise ValueError('ordinary successor cycle/missing source')
            seen.add(key);key=int(ordinary[key].fields[39])


def complete(store,uid,enabled,title_levels):
    """6000-only completion. Whole reward/title/task batch commits together."""
    from .quest_rewards import validate,grant_locked
    fresh=[]
    with store.transaction('nested ordinary completion'):
        profile=bytearray(store.snapshot(uid)[2])
        rows=store.quests.active_ordinary(uid)
        for key,definition,baseline,raw in rows:
            t=enabled.get(('ordinary',key))
            if t is None or t.identity!=definition:continue
            rule=json.loads(raw);validate(t,rule)
            if not conditions_met(t,profile,baseline):continue
            title=int(t.fields[38])
            if title and title not in title_levels:continue
            if title>profile[123] and store.titles.pending(uid) is not None:
                continue  #do not invalidate an already earned pending title choice
            instance,_,_=grant_locked(store,uid,rule)
            profile[123]=max(profile[123],title)
            store.quests.mark_claimed(uid,'ordinary',key,instance)
            fresh.append(key)
        if fresh:store.profiles.update_profile(uid,bytes(profile))
        #Return CURRENT item records for retry/reconnect, not saved grant copies.
        items=[];choices=[];has_receipt=False
        for key,definition,raw,instance in store.quests.claimed_ordinary(uid):
            t=enabled.get(('ordinary',key))
            if t is None or t.identity!=definition:continue
            has_receipt=True
            current=store.inventory.get(uid,instance)
            if current:items.append(current);choices.append(json.loads(raw))
        from .title_rewards import catalog_for
        rewards=([Message(1550,catalog_for(store,choices))]+[Message(2160,p) for p in items]) if items else []
        if has_receipt:rewards.append(Message(1240,struct.pack('<I',store.gold_balance(uid))))
        return fresh,rewards


def completion_messages(store,uid,fresh,successors):
    context=store.snapshot(uid)[2][:4]
    return ([Message(6030,struct.pack('<Q',uid)+context+struct.pack('<H',key)) for key in fresh]+
            [Message(6040,context+struct.pack('<H',key)) for key in sorted(successors)])
