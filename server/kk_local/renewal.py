"""Explicitly configured same-instance weapon renewal; no default prices.

Native1420/173 ->1430;1500 mode1 ->1510;1400/1410 and1440/1450.
Server-owned finite leases, full-price tickets and quote lifetimes are local
policy. Display minutes alone NEVER enroll an old inventory item into a lease.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
import uuid
from .shop_catalog import ShopRecord
from .wire import Message
from .chat import system_notice


def tickets(store,uid):
    return store.commerce.balance(uid,'ticket')


def set_tickets(store,uid,value):
    if type(value) is not int or not 0<=value<=2147483647:raise ValueError('invalid ticket balance')
    with store.transaction('nested wallet update',immediate=False):store.commerce.set_balance(uid,'ticket',value)


def current_catalog(store,key):
    rows=store.commerce.catalog_by_key(key)
    if len(rows)!=1:raise ValueError('renewal catalog missing/ambiguous')
    r=ShopRecord(bytes(rows[0]))
    if (r.raw[4]!=25 or not r.item_id or r.u32(9)!=key or not r.raw[48] or
            not 0<r.u32(38)<=21474836 or r.u32(42)!=r.u32(38) or  # native price*100 signed32
            r.u32(30) or r.u32(34) or r.raw[46] or r.raw[49] or r.u32(77) or r.u32(88) or
            r.raw[13] or r.raw[83] not in (0,1)):
        raise ValueError('renewal requires simple undiscounted ticket offer')
    return r.raw


def enable_offer(store,key,days):
    if type(days) is not int or not 1<=days<=3650:raise ValueError('renewal days1..3650')
    raw=current_catalog(store,key)
    if store.in_transaction:raise ValueError('nested renewal configuration')
    with store.transaction(immediate=False):
        store.renewal.configure_offer(key,days,raw)


def register_lease(store,uid,instance,expires):
    """Explicit admin enrollment only. Never convert permanent inventory."""
    if type(expires) is not int or not 0<expires<=0x7fffffff:raise ValueError('invalid lease timestamp')
    if store.in_transaction:raise ValueError('nested lease configuration')
    with store.transaction(immediate=False):
        raw=owned_weapon(store,uid,instance)
        if store.inventory.permanent(uid,instance):raise ValueError('permanent weapon cannot become a lease')
        store.renewal.register_lease(uid,instance,expires)


def owned_weapon(store,uid,instance):
    row=store.inventory.record_row(uid,instance)
    if row is None or row[0][4]!=25 or struct.unpack_from('<I',row[0],19)[0] not in (0,1,2):raise ValueError('renewal target unavailable')
    return bytes(row[0])


def offers(store,kind,item):
    if kind!=25:return {}
    result={}
    for key,days,raw,revision in store.renewal.offers():
        if len(result)>=4000:raise ValueError('renewal catalog bound')
        if struct.unpack_from('<I',raw,5)[0]!=item:continue
        try:current=current_catalog(store,key)
        except ValueError:continue
        if current==raw:result[key]=(bytes(raw),days,revision)
    return result


def renew(store,uid,operation,p,offer,now):
    if len(p)!=173 or not 1<=len(operation)<=128:raise ValueError('renewal shape')
    instance,opcode=struct.unpack_from('<II',p)
    if not instance or opcode!=105 or struct.unpack_from('<Q',p,8)[0]!=uid or struct.unpack_from('<Q',p,58)[0]!=uid:
        raise ValueError('renewal identity')
    key=struct.unpack_from('<I',p,149)[0]
    raw,days,revision=offer;price=struct.unpack_from('<I',raw,38)[0]
    if key!=struct.unpack_from('<I',raw,9)[0] or struct.unpack_from('<I',p,161)[0]!=price:raise ValueError('renewal price/key mismatch')
    signature=hashlib.sha256(p+raw+struct.pack('<IQ',days,revision)).digest()
    if store.in_transaction:raise ValueError('nested renewal transaction')
    with store.transaction():
        prior=store.renewal.receipt(uid,operation)
        if prior:
            if prior[0]!=signature:raise ValueError('renewal operation conflict')
            item=store.inventory.record_row(uid,instance)
            balance=tickets(store,uid);return balance,item[0] if item else None
        actual=store.renewal.offer(key)
        if actual!=offer or current_catalog(store,key)!=raw:raise ValueError('renewal quote changed')
        record=owned_weapon(store,uid,instance)
        if record[5:9]!=raw[5:9] or store.inventory.permanent(uid,instance):
            raise ValueError('renewal mismatch/permanent target')
        lease=store.renewal.lease(uid,instance)
        if lease is None:raise ValueError('unregistered finite lease')
        if lease[0]<=now and struct.unpack_from('<H',record,17)[0]:
            raise ValueError('unequip expired weapon before renewal')
        deadline=max(now,lease[0])+days*86400
        if deadline>0x7fffffff or deadline-now>3650*86400:raise ValueError('renewal lifetime bound')
        balance=tickets(store,uid)
        if balance<price:raise ValueError('insufficient tickets')
        record=bytearray(record);struct.pack_into('<I',record,13,(deadline-now+59)//60)
        struct.pack_into('<I',record,19,1)
        # Do not clear a worn slot through2161: native teardown requires2310.
        # Expired equipped items were rejected above; use ordinary unequip first.
        balance-=price
        store.commerce.set_balance(uid,'ticket',balance)
        store.renewal.update_lease(uid,instance,deadline)
        store.inventory.update(uid,instance,bytes(record))
        store.renewal.record_receipt(uid,operation,signature,instance,deadline)
        return balance,bytes(record)


def reminders(store,uid,now):
    result=[]
    rows=store.renewal.reminders(uid,now)
    for instance,deadline,raw,hidden in rows:
        if deadline==hidden or raw[4]!=25 or struct.unpack_from('<I',raw,19)[0] not in (0,1,2):continue
        if store.inventory.permanent(uid,instance):continue
        found=offers(store,25,struct.unpack_from('<I',raw,5)[0])
        if not found:continue
        if len(result)>=4000:raise ValueError('renewal reminders bound')
        record=bytearray(16+len(next(iter(found.values()))[0]))
        struct.pack_into('<II',record,8,instance,2);record[16:]=next(iter(found.values()))[0]
        record[70]=100  # local full-price discount factor; native reminder+70
        result.append(bytes(record))
    return b''.join(result)


def handle(engine,c,message):
    from .engine import Phase
    if c is not engine.game or c.phase not in (Phase.LOBBY,Phase.ROOM):return []
    p=bytes(message.payload);ident=message.id;now=int(engine.wall_clock())
    failure=lambda:[Message(1430,bytes(5))] if ident==1420 else [system_notice('[本地服务] 续费操作未完成，请刷新价目与本人道具。')]
    if ident==1500:
        engine.renewal_quote=None
        if len(p)!=9 or struct.unpack_from('<I',p,5)[0]!=1:return failure()
        try:choices=offers(engine.store,p[0],struct.unpack_from('<I',p,1)[0])
        except ValueError:return failure()
        engine.renewal_quote=dict(connection=c,uid=c.uid,until=engine.clock()+600,operation=uuid.uuid4().hex,offers=choices)
        return [Message(1230,struct.pack('<I',tickets(engine.store,c.uid))),Message(1510,b''.join(v[0] for v in choices.values()))]
    if ident==1400:
        if p:return failure()
        try:return [Message(1410,reminders(engine.store,c.uid,now))]
        except ValueError:return failure()
    if ident==1440:
        if len(p)!=4:return failure()
        instance=struct.unpack('<I',p)[0]
        row=engine.store.renewal.lease(c.uid,instance)
        if row is None or row[0]>now:return failure()
        with engine.store.transaction(immediate=False):engine.store.renewal.hide_reminder(c.uid,instance,row[0])
        return [Message(1450,p)]
    q=engine.renewal_quote
    if len(p)!=173 or q is None or q['connection'] is not c or q['uid']!=c.uid or engine.clock()>=q['until']:return failure()
    room=engine.room
    if room is not None and engine.hub is not None:
        member=room.members.get(c.uid)
        if room.stage!='room' or member is None or member.ready or room.network_probe:return failure()
    key=struct.unpack_from('<I',p,149)[0];offer=q['offers'].get(key)
    if offer is None:return failure()
    try:balance,record=renew(engine.store,c.uid,q['operation'],p,offer,now)
    except ValueError:return failure()
    return ([Message(2161,record)] if record else [])+[Message(1230,struct.pack('<I',balance)),Message(1430,b'\1'+p[:4])]


def main():
    parser=argparse.ArgumentParser(description='Explicit local renewal configuration; existing backed-up DB only')
    parser.add_argument('--database',required=True)
    modes=parser.add_mutually_exclusive_group(required=True)
    modes.add_argument('--enable-offer',type=int,nargs=2,metavar=('CATALOG_KEY','DAYS'))
    modes.add_argument('--register-lease',type=int,nargs=3,metavar=('UID','INSTANCE','UNIX_EXPIRES'))
    modes.add_argument('--set-tickets',type=int,nargs=2,metavar=('UID','BALANCE'))
    args=parser.parse_args()
    if not Path(args.database).is_file():parser.error('existing database required')
    from .store import Store
    s=Store(args.database)
    try:
        if args.enable_offer:enable_offer(s,*args.enable_offer)
        elif args.register_lease:register_lease(s,*args.register_lease)
        else:set_tickets(s,*args.set_tickets)
        print(json.dumps(dict(status='configured',policy='SYSTEM_DESIGN_INFERRED',items_granted=0)))
    finally:s.close()


if __name__=='__main__':main()
