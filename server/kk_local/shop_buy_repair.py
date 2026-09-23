"""One-time repair for test-gold catalogues built with disabled native Buy controls.

The original UI requires an eligibility type at byte 83. Type 1 with level 0
allows every test account; type 0 disables rbpBuy before it can send 9040.
"""
import argparse
import json
from pathlib import Path

from .shop import qualified_grant
from .store import Store


def repair_buy_eligibility(store):
    """Upgrade only this test-gold storefront; preserve accounts and receipts."""
    changed=0
    with store.transaction('repair test shop buy eligibility'):
        if not store.commerce.test_gold_enabled():raise ValueError('not a test-gold storefront')
        catalog=store.commerce.catalog_entries()
        offers=store.commerce.offer_entries()
        if not catalog or len(catalog)!=len(offers):raise ValueError('test storefront catalog/offer mismatch')
        catalog_by_key={key:raw for _,_,_,key,raw in catalog}
        if len(catalog_by_key)!=len(catalog):raise ValueError('test storefront duplicate key')
        for key,raw in offers:
            if len(raw)!=108 or catalog_by_key.get(key)!=raw or raw[83] not in (0,1) or raw[13]!=0:
                raise ValueError('test storefront offer mismatch or unsupported eligibility')
            qualified_grant(store,key,store.commerce.offer(key)[1])
        for category,variant,item,key,raw in catalog:
            if raw[83]==1:continue
            fixed=raw[:83]+b'\x01'+raw[84:]
            store.commerce.update_catalog_and_offer(category,variant,item,key,fixed)
            changed+=1
    return changed


def main():
    p=argparse.ArgumentParser(description='Repair an existing test-gold storefront after stopping the service and backing up its database')
    p.add_argument('--database',required=True)
    args=p.parse_args()
    if not Path(args.database).is_file():p.error('existing player database required')
    from .public_admin import DatabaseLease
    with DatabaseLease(args.database):
        store=Store(args.database)
        try:changed=repair_buy_eligibility(store)
        finally:store.close()
    print(json.dumps(dict(policy='LOCAL_TEST_GOLD_BUY_ELIGIBILITY',changed=changed)))


if __name__=='__main__':main()
