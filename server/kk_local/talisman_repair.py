"""4202..4205 repair of existing type30 inventory, no invented repair economy.

Wire layout:873F30,874360,874600,826450. Rules must be explicitly installed
by the local administrator; material/quantity/capacity are PROVISIONAL policy.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import uuid
from .chat import system_notice
from .wire import Message


def import_rules(store,document):
    if (not isinstance(document,dict) or set(document)!={'schema','rules'} or
            document['schema']!='kk-talisman-repair-rules-v1' or
            not isinstance(document['rules'],list) or len(document['rules'])>4000):
        raise ValueError('invalid repair rules document')
    rows=[];seen=set()
    for row in document['rules']:
        if (not isinstance(row,dict) or set(row)!={'item','material','quantity','capacity'} or
                any(type(v) is not int for v in row.values())):raise ValueError('invalid repair rule fields')
        item,material,quantity,capacity=(row[k] for k in ('item','material','quantity','capacity'))
        if (not 0<item<2**32 or not 0<material<2**32 or item==material or item in seen or
                not 1<=quantity<=65535 or not 1<=capacity<=65535):raise ValueError('repair rule bounds or duplicate')
        seen.add(item);rows.append((item,material,quantity,capacity))
    if store.in_transaction:raise ValueError('nested repair rule import')
    with store.transaction(immediate=False):
        store.talisman.replace_repair_rules(rows)
    return len(rows)


def owned_item(store,uid,instance):
    row=store.inventory.record_row(uid,instance)
    if row is None or row[0][4]!=30 or struct.unpack_from('<I',row[0],19)[0] not in (0,1):
        raise ValueError('repair target unavailable')
    return bytes(row[0])


def rule_for(store,item):
    return store.talisman.repair_rule(item)


def repair(store,uid,operation,request,quote):
    if not isinstance(request,bytes) or len(request)!=12:raise ValueError('invalid repair request')
    instance,material,reserved=struct.unpack('<III',request)
    if (instance!=quote['instance'] or material!=quote['rule'][0] or reserved or
            not isinstance(operation,str) or not 1<=len(operation)<=128):raise ValueError('repair quote mismatch')
    signature=hashlib.sha256(request+quote['record']+struct.pack('<4Q',*quote['rule'])).digest()
    if store.in_transaction:raise ValueError('nested repair transaction')
    with store.transaction():
        old=store.talisman.repair_receipt(uid,operation)
        if old:
            if old[0]!=signature:raise ValueError('repair operation changed')
            return False  # no quota restore after later use
        raw=owned_item(store,uid,instance);item=struct.unpack_from('<I',raw,5)[0]
        if raw!=quote['record'] or rule_for(store,item)!=quote['rule']:raise ValueError('repair quote stale')
        material,needed,capacity,_=quote['rule']
        if struct.unpack_from('<H',raw,23)[0]>=capacity:raise ValueError('repair target already full')
        candidates=[]
        for key,record in store.inventory.rows(uid,ordered=True):
            if (record[4]==60 and struct.unpack_from('<I',record,5)[0]==material and
                    struct.unpack_from('<H',record,17)[0]==0 and struct.unpack_from('<I',record,19)[0] in (0,1)):
                candidates.append((key,record,struct.unpack_from('<H',record,23)[0]))
        if sum(row[2] for row in candidates)<needed:raise ValueError('repair materials insufficient')
        for key,record,quantity in candidates:
            amount=min(needed,quantity)
            if not amount:continue
            remaining=quantity-amount;needed-=amount
            if remaining:
                updated=bytearray(record);struct.pack_into('<H',updated,23,remaining)
                store.inventory.update(uid,key,bytes(updated))
            else:store.inventory.delete(uid,key)
            if not needed:break
        updated=bytearray(raw);struct.pack_into('<H',updated,23,capacity)
        store.inventory.update(uid,instance,bytes(updated))
        store.talisman.record_repair(uid,operation,signature)
        return True


def handle(engine,c,message):
    from .engine import Phase
    failure=lambda:[system_notice('[本地服务] 修理未完成，请重新获取报价并核对本人道具及材料。')]
    if c is not engine.game or c.phase not in (Phase.LOBBY,Phase.ROOM):return []
    room=engine.room
    if room is not None and engine.hub is not None:
        member=room.members.get(c.uid)
        if room.stage!='room' or member is None or member.ready or room.network_probe:return failure()
    p=bytes(message.payload)
    if message.id==4202:engine.talisman_repair_quote=None
    if len(p)!=(4 if message.id==4202 else 12):return failure()
    instance=struct.unpack_from('<I',p)[0]
    if message.id==4202:
        try:
            raw=owned_item(engine.store,c.uid,instance);item=struct.unpack_from('<I',raw,5)[0]
            rule=rule_for(engine.store,item)
            if rule is None or struct.unpack_from('<H',raw,23)[0]>=rule[2]:raise ValueError('unconfigured/full')
        except ValueError:return failure()
        engine.talisman_repair_quote=dict(instance=instance,record=raw,rule=rule,connection=c,
            uid=c.uid,until=engine.clock()+300,operation=uuid.uuid4().hex)
        return [Message(4203,struct.pack('<8I',instance,item,rule[0],0,rule[1],struct.unpack_from('<H',raw,23)[0],rule[2],0))]
    quote=engine.talisman_repair_quote
    if quote is None or quote['connection'] is not c or quote['uid']!=c.uid or engine.clock()>=quote['until']:return failure()
    try:repair(engine.store,c.uid,quote['operation'],p,quote)
    except ValueError:return failure()
    #4205 only acknowledges UI. Send the CURRENT absolute inventory first;
    #not the receipt's former snapshot and never a second material decrement.
    return [Message(1120,engine.store.snapshot(c.uid)[3]),Message(4205,struct.pack('<II',instance,0))]


def main():
    parser=argparse.ArgumentParser(description='Install explicit local talisman repair rules into an existing backed-up DB')
    parser.add_argument('--database',required=True);parser.add_argument('--rules',required=True)
    args=parser.parse_args()
    if not Path(args.database).is_file():parser.error('existing database required')
    from .store import Store
    document=json.loads(Path(args.rules).read_text(encoding='utf-8-sig'))
    store=Store(args.database)
    try:print(json.dumps(dict(rules=import_rules(store,document),inventory_changed=False)))
    finally:store.close()


if __name__=='__main__':main()
