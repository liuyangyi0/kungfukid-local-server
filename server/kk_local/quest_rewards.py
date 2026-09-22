"""Explicit local quest contracts, driven only by committed battle consensus.

Native state4/3 and6311/6312/6301/6302 are recovered wire contracts. Event
bindings and reward amounts are SYSTEM_DESIGN_INFERRED / PROVISIONAL. Nothing
is enabled by default; no daily rollover or original XP formula is invented.
"""
import argparse
import json
from pathlib import Path
import struct
from .wire import Message
from .title_rewards import catalog_for


EVENTS=frozenset(('battle_play','battle_win',*(f'mode_{i}_{kind}' for i in range(4) for kind in ('play','win'))))


def validate(template,rule):
    if template.family not in ('ordinary','daily','newbie'):raise ValueError('unsupported quest family')
    if not isinstance(rule,dict) or set(rule)!={'events','gold','catalog_hex','grant_hex'}:
        raise ValueError('quest reward contract fields')
    events=rule['events'];gold=rule['gold']
    if type(gold) is not int or not 0<=gold<=0x7fffffff:raise ValueError('quest gold bounds')
    if template.family=='ordinary':
        from .ordinary_quests import requirements
        requirements(template)
        if events!=[]:raise ValueError('ordinary conditions come from source, not event overrides')
    else:
        if not isinstance(events,list) or len(events)!=3:raise ValueError('three condition bindings required')
        active=[]
        for i,event in enumerate(events):
            key=int(template.fields[3+i*3]);needed=int(template.fields[4+i*3])
            if needed:
                if not isinstance(event,str) or event not in EVENTS or key in active:raise ValueError('unresolved/duplicate task condition')
                active.append(key)
            elif event!='':raise ValueError('binding for unused condition')
        if not active:raise ValueError('empty completion condition')
    catalog=bytes.fromhex(rule['catalog_hex']);grant=bytes.fromhex(rule['grant_hex'])
    if bool(catalog)!=bool(grant):raise ValueError('both reward records required')
    if grant and (len(catalog)!=108 or len(grant)!=68 or catalog[4]!=grant[4] or grant[4] not in (25,60) or
                  not struct.unpack_from('<I',catalog,9)[0] or not struct.unpack_from('<I',grant,5)[0] or
                  catalog[5:9]!=grant[5:9] or struct.unpack_from('<H',grant,17)[0]!=0 or
                  struct.unpack_from('<I',grant,19)[0] not in (0,1) or
                  (grant[4]==60 and struct.unpack_from('<H',grant,23)[0]==0)):
        raise ValueError('quest reward requires complete unworn weapon/consumable records')
    return json.dumps(rule,sort_keys=True,separators=(',',':'))


def configure(store,rows,templates):
    from .quests import MAX_TASKS
    if not isinstance(rows,list) or len(rows)>MAX_TASKS:raise ValueError('quest contract bounds')
    values=[];seen=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'family','key','events','gold','catalog_hex','grant_hex'}:
            raise ValueError('quest reward entry fields')
        family,key=row['family'],row['key']
        if type(key) is not int or family not in ('ordinary','daily','newbie'):raise ValueError('quest identity')
        template=templates.get((family,key))
        if template is None or (family,key) in seen:raise ValueError('quest source missing/duplicate')
        rule={k:v for k,v in row.items() if k not in ('family','key')}
        data=validate(template,rule)
        if rule['grant_hex']:catalog_for(store,[rule])
        seen.add((family,key));values.append((family,key,template.identity,data))
    with store.transaction('nested quest rules',immediate=False):
        store.quests.replace_rules(values)


def frozen_rule(store,template):
    row=store.quests.rule(template.family,template.key)
    if row is None:return None
    if row[0]!=template.identity:raise ValueError('quest reward source drift')
    data=validate(template,json.loads(row[1]))
    return data


def counts_met(template,counts):
    return any(int(template.fields[4+i*3]) for i in range(3)) and all(
        count>=int(template.fields[4+i*3]) for i,count in enumerate(counts))


def settled_locked(store,battle,mode,outcomes,templates):
    """Called inside the new match receipt transaction, never on its retry.

    No public protocol accepts arbitrary progress increments. Unknown source
    definitions pause that task, without breaking the user's battle result.
    """
    if not store.in_transaction:raise ValueError('quest progress needs settlement transaction')
    if mode not in (0,1,2,3):return
    receipt=store.progression.match_receipt(battle)
    if receipt is None or json.loads(receipt[0])[-1]!=mode:raise ValueError('missing mode-bound battle receipt')
    for uid,outcome in outcomes.items():
        if store.progression.match_outcome(battle,uid)!=(outcome,):
            raise ValueError('missing player settlement')
        matched={'battle_play',f'mode_{mode}_play'}
        if outcome==1:matched.update(('battle_win',f'mode_{mode}_win'))
        rows=store.quests.active_extended(uid)
        for family,key,definition,raw,packed in rows:
            template=templates.get((family,key))
            if template is None or template.identity!=definition:continue
            rule=json.loads(raw)
            try:validate(template,rule)
            except ValueError:continue
            counts=list(struct.unpack('<3I',packed))
            for i,event in enumerate(rule['events']):
                if event in matched:counts[i]=min(counts[i]+1,int(template.fields[4+i*3]))
            state=4 if counts_met(template,counts) else 2
            store.quests.update_counts(uid,family,key,struct.pack('<3I',*counts),state)


def grant_locked(store,uid,rule):
    """Shared ordinary/extended reward mutation; caller commits state+receipt."""
    if not store.in_transaction:raise ValueError('quest grant needs transaction')
    balance=store.gold_balance(uid)+rule['gold']
    if balance>0x7fffffff:raise ValueError('quest wallet overflow')
    catalog=catalog_for(store,[rule]) if rule['grant_hex'] else b''
    item=None;instance=None
    if rule['grant_hex']:
        if store.inventory.count(uid)>=8000:raise ValueError('quest inventory full')
        instance=store.inventory.allocate_instance();item=bytearray.fromhex(rule['grant_hex'])
        struct.pack_into('<I',item,0,instance);item=bytes(item)
        store.inventory.insert(uid,instance,item)
    store.commerce.set_balance(uid,'gold',balance)
    return instance,item,catalog


def claim(store,uid,template):
    if template.family not in ('daily','newbie'):raise ValueError('ordinary quests auto-complete, not claim')
    with store.transaction('nested quest claim'):
        row=store.quests.progress(uid,template.family,template.key)
        if row is None or row.definition!=template.identity or row.state not in (3,4) or row.execution is None:
            raise ValueError('quest not completed')
        if store.quests.available_definition(template.family,template.key)!=(template.identity,):
            raise ValueError('quest unavailable/source changed')
        rule=json.loads(row.execution);validate(template,rule)
        if not counts_met(template,struct.unpack('<3I',row.counts)):raise ValueError('quest counters incomplete')
        if row.state==3:
            item=store.inventory.get(uid,row.claimed_instance)
            catalog=catalog_for(store,[rule]) if item else b''
        else:
            active=store.quests.rule(template.family,template.key)
            if active is None or active[0]!=template.identity:raise ValueError('quest reward rule disabled/source changed')
            instance,item,catalog=grant_locked(store,uid,rule)
            store.quests.mark_claimed(uid,template.family,template.key,instance)
        messages=([Message(1550,catalog),Message(2160,item)] if item else [])
        messages+=[Message(1240,struct.pack('<i',store.gold_balance(uid))),
                   Message(6301 if template.family=='daily' else 6302,struct.pack('<HB',template.key,3))]
        return messages


def notifications(store,uid,templates,offered):
    """Lists first, then completion signal, once per connection. No battle UI race."""
    result=[]
    for family,key,state in store.quests.completed(uid):
        if (family,key) not in templates or (family,key) in offered:continue
        result.append(Message(6031 if family=='daily' else 6032,struct.pack('<HB',key,4)))
        offered.add((family,key))
    return result


def main():
    from .maps import ClientConfig
    from .quests import from_config
    from .store import Store
    p=argparse.ArgumentParser(description='Explicit local quest completion/reward rules; stop service and back up DB')
    p.add_argument('--database',required=True);p.add_argument('--client-root',required=True);p.add_argument('--rules',required=True)
    args=p.parse_args()
    if not Path(args.database).is_file():p.error('existing database required')
    data=json.loads(Path(args.rules).read_text(encoding='utf-8-sig'))
    if set(data)!={'schema','tasks'} or data['schema']!='kk-quest-rewards-v1':raise ValueError('quest reward document')
    s=Store(args.database)
    try:
        configure(s,data['tasks'],from_config(ClientConfig(Path(args.client_root)/'Data/config.spf2')))
        print(json.dumps(dict(contracts=len(data['tasks']),policy='SYSTEM_DESIGN_INFERRED / PROVISIONAL',items_granted=0)))
    finally:s.close()


if __name__=='__main__':main()
