"""Native weapon-upgrade wire bridge with explicit local, revisioned rules.

21410 ->21411(21B/level);21412(instance4) ->1240,2161,21413(22B).
Level/score are inventory+43/+47. Upgrade consumes score+gold; it does not
consume bag materials. Failure-keeps-level, odds and costs are local policy,
not recovered old-server formulas. No default rules or score grants.
"""
import argparse
import json
from pathlib import Path
import secrets
import struct
from .chat import system_notice
from .menu_layouts import decode_menu_request
from .wire import Message


def validate(rules):
    if not isinstance(rules,dict) or set(rules)!={'enabled','failure','levels'}:
        raise ValueError('weapon upgrade rules shape')
    if type(rules['enabled']) is not bool or rules['failure']!='consume_score_keep_level':
        raise ValueError('unsupported weapon upgrade policy')
    rows=rules['levels']
    if not isinstance(rows,list) or len(rows)>256 or (rules['enabled'] and len(rows)<2):
        raise ValueError('weapon upgrade needs2..256 levels')
    for i,r in enumerate(rows):
        if (not isinstance(r,dict) or set(r)!={'level','score','gold','odds','bonus_percent'} or
                any(type(v) is not int for v in r.values()) or r['level']!=i or
                not 1<=r['score']<=0x7fffffff or not 0<=r['gold']<=0x7fffffff or
                not 0<=r['odds']<=100 or not 0<=r['bonus_percent']<=100):
            raise ValueError('weapon upgrade level/range')
    return json.dumps(rules,sort_keys=True,separators=(',',':'))


def configure(store,rules,expected_revision):
    raw=validate(rules)
    if type(expected_revision) is not int or expected_revision<0:raise ValueError('weapon rule revision')
    if store.in_transaction:raise ValueError('nested weapon rule update')
    with store.transaction():
        row=store.upgrades.settings()
        current=row[0] if row else 0
        if current!=expected_revision:raise ValueError('weapon rules changed; reload before editing')
        if row and json.loads(row[1])['levels'] and not rules['levels']:
            raise ValueError('disable attempts without deleting the native bonus table')
        if rules['levels']:
            for item, in store.inventory.all_records():
                if item[4]==25 and struct.unpack_from('<I',item,43)[0]>=len(rules['levels']):
                    raise ValueError('configured table would orphan an existing weapon level')
        store.upgrades.set_settings(current + 1,raw)
        return current+1


def settings(store):
    row=store.upgrades.settings()
    if row is None:return 0,dict(enabled=False,failure='consume_score_keep_level',levels=[])
    rules=json.loads(row[1]);validate(rules)
    return row[0],rules


def table_packet(rules):
    validate(rules)
    if not rules['levels']:raise ValueError('empty native table is not a cache reset')
    return Message(21411,b''.join(struct.pack('<4IBI',r['level'],r['score'],r['gold'],r['odds'],0,r['bonus_percent']) for r in rules['levels']))


def publish(engine,c):
    """All newly authenticated players receive the same attack-bonus table."""
    if c is not engine.game:return []
    revision,rules=settings(engine.store)
    if not rules['levels']:
        engine.weapon_upgrade_offer=None
        return []
    message=table_packet(rules)
    engine.weapon_upgrade_offer=(c,revision)
    return [message]


def attempt(store,uid,operation,instance,revision,now):
    if (type(instance) is not int or not 0<instance<=0xffffffff or type(revision) is not int or revision<=0 or
            not isinstance(operation,str) or not 1<=len(operation)<=128):raise ValueError('upgrade request identity')
    if store.in_transaction:raise ValueError('nested weapon upgrade')
    with store.transaction():
        prior=store.upgrades.receipt(uid,operation)
        row=store.inventory.record_row(uid,instance)
        if row is None or len(row[0])!=68 or struct.unpack_from('<I',row[0])[0]!=instance or row[0][4]!=25:
            raise ValueError('owned weapon unavailable')
        original=bytes(row[0])
        if prior:
            if prior[0]!=instance:raise ValueError('upgrade operation collision')
            return bool(prior[1]),store.gold_balance(uid),original,False
        current,rules=settings(store)
        if current!=revision or not rules['enabled']:raise ValueError('upgrade table stale/disabled')
        if struct.unpack_from('<H',original,17)[0] not in (0,8,9) or struct.unpack_from('<I',original,19)[0] not in (0,1):
            raise ValueError('unsupported weapon status/slot')
        lease=store.renewal.lease(uid,instance)
        permanent=store.inventory.permanent(uid,instance)
        if lease and lease[0]<=now and not permanent:raise ValueError('weapon lease expired')
        level,score=struct.unpack_from('<II',original,43)
        if level+1>=len(rules['levels']):raise ValueError('weapon already at configured maximum')
        r=rules['levels'][level];gold=store.gold_balance(uid)
        if score<r['score'] or gold<r['gold']:raise ValueError('weapon score/gold insufficient')
        roll=secrets.randbelow(100);success=roll<r['odds']
        changed=bytearray(original);struct.pack_into('<II',changed,43,level+int(success),score-r['score'])
        changed=bytes(changed);gold-=r['gold']
        store.inventory.update(uid,instance,changed)
        store.commerce.set_balance(uid,'gold',gold)
        store.upgrades.record_attempt(uid,operation,instance,revision,int(success),roll,r['score'],r['gold'],original,changed)
        return success,gold,changed,True


def result_packets(result):
    success,gold,item,_=result
    ack=bytearray(22);ack[0]=int(success);ack[9:13]=item[:4]
    #861190 re-reads the inventory vector; apply2161 before its animation ACK.
    return [Message(1240,struct.pack('<I',gold)),Message(2161,item),Message(21413,bytes(ack))]


def handle(engine,c,message):
    from .engine import Phase
    if c is not engine.game or c.uid!=engine.account_uid or c.phase not in (Phase.LOBBY,Phase.ROOM):return []
    fields=decode_menu_request(message.id,message.payload)
    failure=lambda:[system_notice('[本地服务] 武器升级未执行，请检查配置版本、武器归属、熟练度和金币。')]
    if message.id==21410:
        out=publish(engine,c)
        if not settings(engine.store)[1]['enabled']:out.append(system_notice('[本地服务] 尚未启用武器升级规则。'))
        return out
    if engine.room and engine.hub:
        room=engine.room;member=room.members.get(c.uid)
        if room.stage!='room' or member is None or member.ready or room.network_probe:return failure()
    offer=engine.weapon_upgrade_offer
    if not offer or offer[0] is not c:return failure()
    operation=f'{engine.transaction_namespace}:{c.number}:{c.command_sequence}'
    try:result=attempt(engine.store,c.uid,operation,fields['instance'],offer[1],int(engine.wall_clock()))
    except ValueError:return failure()
    if engine.hub and struct.unpack_from('<H',result[2],17)[0]:engine.hub.equipment_changed(engine)
    return result_packets(result)


def main():
    from .store import Store
    p=argparse.ArgumentParser(description='Explicit weapon-upgrade rules; stop service and reconnect all clients after edits')
    p.add_argument('--database',required=True);p.add_argument('--rules',required=True);p.add_argument('--expected-revision',required=True,type=int)
    args=p.parse_args()
    if not Path(args.database).is_file():p.error('existing database required; back it up first')
    doc=json.loads(Path(args.rules).read_text(encoding='utf-8-sig'))
    if set(doc)!={'schema','enabled','failure','levels'} or doc.pop('schema')!='kk-weapon-upgrade-rules-v1':raise ValueError('weapon upgrade document')
    s=Store(args.database)
    try:revision=configure(s,doc,args.expected_revision)
    finally:s.close()
    print(json.dumps(dict(revision=revision,policy='SYSTEM_DESIGN_INFERRED / PROVISIONAL',inventory_changed=False)))


if __name__=='__main__':main()
