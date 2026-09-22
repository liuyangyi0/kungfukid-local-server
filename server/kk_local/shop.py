"""Local native shop transactions. Prices and grants require explicit offers.

85D230 produces9040;85C7E0 produces9090. No credit/coupon/VIP policy is
invented. A frame is a new purchase intent; internal operation IDs are the
retry boundary because neither native request carries a transaction nonce.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from .shop_catalog import ShopRecord
from .wire import Message


def terms(record,*,gift=False):
    r=ShopRecord(record)
    if (not r.raw[48] or r.raw[49] or r.u32(77) or r.u32(88) or
            r.raw[83] not in (0,1) or (r.raw[83]==1 and r.raw[13])):
        raise ValueError('shop offer requires unresolved eligibility/credit/associated rules')
    gold=r.u32(30)
    currency='gold' if gold else 'ticket';base=gold or r.u32(38)
    if not 0<base<=21474836:raise ValueError('shop native price arithmetic bounds')
    if currency=='gold' and (r.u32(38) or r.u32(42)):raise ValueError('mixed-currency offer')
    if currency=='ticket' and r.u32(34):raise ValueError('mixed-currency offer')
    current=r.u32(34 if gold else 42)
    #85D230 uses min(base,current) on sale.85C7E0 uses current directly.
    price=(current if gift else min(base,current)) if r.raw[46] else base
    if not 0<price<=21474836:raise ValueError('shop price out of range')
    if gift and currency!='ticket':raise ValueError('native gift is ticket-only')
    return currency,price


def current_catalog(store,key):
    rows=store.commerce.catalog_by_key(key)
    if len(rows)!=1:raise ValueError('shop catalog missing/ambiguous')
    return bytes(rows[0])


def qualified_grant(store,key,grant):
    from .store import EQUIPMENT_SLOTS
    if type(key) is not int or not 0<key<=0xffffffff:raise ValueError('shop key bounds')
    raw=current_catalog(store,key);terms(raw)
    if (not isinstance(grant,bytes) or len(grant)!=68 or raw[4]!=grant[4] or raw[5:9]!=grant[5:9] or
            not struct.unpack_from('<I',grant,5)[0] or grant[4] not in (*EQUIPMENT_SLOTS,60,71,74) or
            struct.unpack_from('<H',grant,17)[0] or struct.unpack_from('<I',grant,19)[0] not in (0,1) or
            (grant[4] in (60,64,71,74) and not struct.unpack_from('<H',grant,23)[0])):
        raise ValueError('shop needs complete unworn grant with valid quantity')
    return raw


def enable_offer(store,key,grant):
    raw=qualified_grant(store,key,grant)
    #Keep the existing physical table/receipts; its old name is compatibility.
    with store.transaction('nested shop enable',immediate=False):store.commerce.put_offer(key,raw,grant)


def balance(store,uid,currency):return store.commerce.balance(uid,currency)


def debit_locked(store,uid,currency,cost):
    if not store.in_transaction:raise ValueError('shop debit needs transaction')
    value=balance(store,uid,currency)
    if value<cost:raise ValueError('insufficient shop balance')
    store.commerce.set_balance(uid,currency,value-cost)
    return value-cost


def offer_locked(store,key,*,gift=False):
    row=store.commerce.offer(key)
    if row is None:raise ValueError('shop offer not enabled')
    raw,grant=row
    if raw!=current_catalog(store,key):raise ValueError('shop offer changed; requalification required')
    currency,cost=terms(raw,gift=gift)
    return raw,grant,currency,cost


def purchase(store,uid,operation,request):
    if not isinstance(operation,str) or not 1<=len(operation)<=128 or not isinstance(request,bytes) or len(request)!=169:
        raise ValueError('purchase operation/shape')
    digest=hashlib.sha256(request).digest()
    with store.transaction('nested shop transaction'):
        old=store.commerce.purchase_receipt(uid,operation)
        if old:
            if old[0]!=digest:raise ValueError('purchase receipt conflict')
            currency,_=terms(old[2]);instance=struct.unpack_from('<I',old[1])[0]
            item=store.inventory.get(uid,instance)
            result=(currency,balance(store,uid,currency),item,old[2])
        else:
            key=struct.unpack_from('<I',request,145)[0];raw,grant,currency,cost=offer_locked(store,key)
            code,gold,credit,ticket,coupon,associated=(struct.unpack_from('<I',request)[0],*struct.unpack_from('<5I',request,149))
            if (struct.unpack_from('<Q',request,4)[0]!=uid or struct.unpack_from('<Q',request,54)[0]!=uid or
                    credit or coupon or associated or
                    (code,gold,ticket)!=((111,cost,0) if currency=='gold' else (109,0,cost))):
                raise ValueError('purchase identity/price/terms mismatch')
            if store.inventory.count(uid)>=8000:raise ValueError('inventory full')
            remaining=debit_locked(store,uid,currency,cost)
            item=bytearray(grant);instance=store._allocate_inventory_instance();struct.pack_into('<I',item,0,instance);item=bytes(item)
            permanent=store.commerce.permanent_policy(key)
            if permanent:
                from .store import permanent_equipment_record
                item=permanent_equipment_record(item)
            store.inventory.insert(uid,instance,item)
            if permanent:store.inventory.add_permanent(uid,instance,strict=True)
            store.commerce.record_purchase(uid,operation,digest,remaining,item,raw)
            result=(currency,remaining,item,raw)
    return result


def purchase_packets(result):
    currency,value,item,raw=result
    return [Message(1240 if currency=='gold' else 1230,struct.pack('<I',value))]+(
        [Message(2160,item)] if item else [])+[Message(9050,raw)]


def gift_fields(p):
    if not isinstance(p,bytes) or len(p)!=426:raise ValueError('gift length')
    def text(raw):
        value,sep,_=raw.partition(b'\0')
        if not sep:raise ValueError('gift unterminated text')
        name=value.decode('gbk')
        if name.encode('gbk')!=value:raise ValueError('gift text encoding')
        return name
    name=text(p[83:104]);body=text(p[170:426])
    if not name or len(body.encode('gbk'))>200:raise ValueError('gift name/mail text bounds')
    if struct.unpack_from('<I',p)[0]!=109 or p[169] or any(struct.unpack_from('<II',p,149)) or any(struct.unpack_from('<II',p,161)):
        raise ValueError('gift currency/coupon/flag unsupported')
    return name,body,struct.unpack_from('<Q',p,54)[0],struct.unpack_from('<I',p,145)[0],struct.unpack_from('<I',p,157)[0]


def gift(store,uid,operation,p):
    #85C7E0 zeros sender fields; authenticated connection, not packet+4, owns it.
    name,body,hint,key,quote=gift_fields(p)
    if not isinstance(operation,str) or not 1<=len(operation)<=128:raise ValueError('gift operation')
    digest=hashlib.sha256(p).digest()
    with store.transaction('nested gift transaction'):
        prior=store.commerce.gift_receipt(uid,operation)
        if prior:
            if prior[0]!=digest:raise ValueError('gift receipt conflict')
            return balance(store,uid,'ticket'),prior[1],prior[2],False
        recipients=store.commerce.recipient_ids(name)
        if len(recipients)!=1 or recipients[0][0]==uid or hint not in (0,recipients[0][0]):raise ValueError('gift recipient mismatch')
        target=recipients[0][0]
        raw,grant,currency,cost=offer_locked(store,key,gift=True)
        if quote!=cost:raise ValueError('gift price changed')
        from .mailbox import deliver_locked
        #Mail is persisted now; inventory insertion happens only at2171 claim.
        delivery=f'shop:{uid}:'+hashlib.sha256(operation.encode('utf-8')).hexdigest()
        mail=deliver_locked(store,target,delivery,title='赠送道具',sender=store.nickname(uid),body=body,catalog=raw,grant=grant)
        value=debit_locked(store,uid,currency,cost)
        store.commerce.record_gift(uid,operation,digest,target,mail)
        return value,target,mail,True


def wallet_packets(store,uid):
    return [Message(1240,struct.pack('<I',balance(store,uid,'gold'))),Message(1230,struct.pack('<I',balance(store,uid,'ticket')))]


def main():
    from .store import Store
    p=argparse.ArgumentParser(description='Explicit native shop offers; stop service and back up DB')
    p.add_argument('--database',required=True);p.add_argument('--offers',required=True)
    args=p.parse_args()
    if not Path(args.database).is_file():p.error('existing database required')
    doc=json.loads(Path(args.offers).read_text(encoding='utf-8-sig'))
    if set(doc)!={'schema','offers'} or doc['schema']!='kk-shop-offers-v1' or not isinstance(doc['offers'],list):raise ValueError('shop offer document')
    #Validate the complete batch under one short DB transaction; no full DB copy.
    s=Store(args.database)
    try:
        checked=[];seen=set()
        for row in doc['offers']:
            if not isinstance(row,dict) or set(row)!={'key','grant_hex'}:raise ValueError('shop offer entry')
            if row['key'] in seen:raise ValueError('duplicate offer key')
            seen.add(row['key']);checked.append((row['key'],bytes.fromhex(row['grant_hex'])))
        with s.transaction('nested shop configuration'):
            rows=[(key,qualified_grant(s,key,grant),grant) for key,grant in checked]
            for key,raw,grant in rows:s.commerce.put_offer(key,raw,grant)
        print(json.dumps(dict(enabled=len(checked),wallets_changed=False)))
    finally:s.close()


if __name__=='__main__':main()
