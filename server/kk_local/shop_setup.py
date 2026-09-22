"""User-selected test-gold storefront compiled from this install's item table.

This is explicitly NEW local-server sale data, NOT captured original108B rows.
Unknown wire fields use the local baseline0 policy. Raw native imports remain
lossless and separate. No account, wallet or existing inventory is modified.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import struct
from .maps import ClientConfig
from .shop_catalog import MAX_RECORDS
from .shop import qualified_grant
from .store import Store,permanent_equipment_record,PERMANENT_WEAPON_DISPLAY_MINUTES


EQUIPMENT=frozenset((12,13,14,15,16,17,18,20,21,25))
SUPPLIES=frozenset((60,64,71,74))


def compile_rows(raw,price=100,supply_count=100):
    if type(price) is not int or not 1<=price<=21474836:raise ValueError('test shop price bounds')
    if type(supply_count) is not int or not 1<=supply_count<=999:raise ValueError('supply count1..999')
    seen=set();rows=[]
    for line in raw.decode('gb18030').splitlines():
        if not line.strip():continue
        fields=line.split('\t')
        if len(fields)<24:raise ValueError('short source item row')
        kind,item=int(fields[0]),int(fields[1])
        if not 0<item<=0xffffffff or item in seen:raise ValueError('source item identity')
        seen.add(item)
        if kind not in EQUIPMENT|SUPPLIES:continue
        #Source IDs are unique. Reuse the same ID as the local sale key; it is
        #not a claim about the original server's commercial SKU scheme.
        key=item;record=bytearray(108);record[4]=kind;record[48]=1
        struct.pack_into('<I',record,0,key)
        struct.pack_into('<II',record,5,item,key)
        #Expose all native body filters locally; actual models/icons remain
        #resolved by native item definition. No invented gender semantics+47.
        struct.pack_into('<I',record,14,0xffffffff)
        struct.pack_into('<II',record,22,PERMANENT_WEAPON_DISPLAY_MINUTES//60 if kind in EQUIPMENT else 0,
                         0 if kind in EQUIPMENT else supply_count)
        struct.pack_into('<II',record,30,price,price)
        grant=bytearray(68);grant[4]=kind;struct.pack_into('<II',grant,5,item,key)
        if kind in EQUIPMENT:grant=bytearray(permanent_equipment_record(grant))
        else:struct.pack_into('<H',grant,23,supply_count)
        rows.append((kind,key,bytes(record),bytes(grant),kind in EQUIPMENT))
    if not rows or len(rows)>MAX_RECORDS:raise ValueError('test shop record capacity')
    return sorted(rows,key=lambda r:(r[0],r[1]))


def install(store,rows,price,supply_count):
    """Atomic explicit storefront replacement; does not touch user holdings."""
    with store.transaction('nested shop installation'):
        #Explicit command owns the storefront tables only. Keep transaction
        #receipts and every account/wallet/inventory/entitlement untouched.
        store.commerce.clear_storefront()
        ordinal=Counter()
        for kind,key,record,grant,permanent in rows:
            store.commerce.insert_catalog((10,kind,ordinal[kind],key,key,record))
            ordinal[kind]+=1
            qualified_grant(store,key,grant)
            store.commerce.put_offer(key,record,grant,replace=False)
            store.commerce.put_permanent_policy(key,permanent)
        for name,value in (('test-gold','enabled'),('test-price',str(price)),('supply-count',str(supply_count))):
            store.commerce.put_setting(name,value)


def main():
    p=argparse.ArgumentParser(description='Install explicit test-gold/permanent shop, no account funding; stop service and back up DB')
    p.add_argument('--database',required=True);p.add_argument('--client-root',required=True)
    p.add_argument('--price',type=int,default=100);p.add_argument('--supply-count',type=int,default=100)
    p.add_argument('--replace-storefront',action='store_true',required=True)
    p.add_argument('--create-catalog-db',action='store_true',help='Create a NEW catalogue-only database; never a replacement player database')
    args=p.parse_args()
    exists=Path(args.database).exists()
    if args.create_catalog_db and exists:p.error('new catalogue database path already exists')
    if not args.create_catalog_db and not Path(args.database).is_file():p.error('existing destination database required')
    source=ClientConfig(Path(args.client_root)/'Data/config.spf2')
    rows=compile_rows(source.read('item.txt'),args.price,args.supply_count)
    s=Store(args.database)
    try:install(s,rows,args.price,args.supply_count)
    finally:s.close()
    print(json.dumps(dict(policy='LOCAL_TEST_GOLD_PERMANENT',price=args.price,supply_count=args.supply_count,
                         products=len(rows),counts=dict(Counter(r[0] for r in rows)),accounts_changed=False)))


if __name__=='__main__':main()
