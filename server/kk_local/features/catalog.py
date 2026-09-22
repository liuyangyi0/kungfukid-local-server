"""Catalogue validation and shelf policy, separate from SQL and wire framing."""
from ..shop_catalog import ShopRecord,MAX_RECORDS


class CatalogService:
    def __init__(self,store):self.store=store;self.repo=store.commerce

    def replace(self,category,variant,records):
        if any(type(v) is not int or not 0<=v<=255 for v in (category,variant)):raise ValueError('invalid catalog selector')
        rows=[];items=set();keys=set()
        for raw in records:
            if len(rows)>=MAX_RECORDS:raise ValueError('catalog too large')
            record=ShopRecord(raw);item=record.item_id;key=record.u32(9)
            if not item or not key or item in items or key in keys:raise ValueError('invalid or duplicate catalog identity')
            items.add(item);keys.add(key);rows.append((category,variant,len(rows),item,key,record.raw))
        with self.store.transaction('nested catalog transaction',immediate=False):self.repo.replace_catalog(category,variant,rows)
        return len(rows)

    def records(self,category,variant):
        if any(type(v) is not int or not 0<=v<=255 for v in (category,variant)):raise ValueError('invalid catalog selector')
        result=tuple(ShopRecord(raw) for raw in self.repo.catalog(category,variant))
        if result or not self.repo.test_gold_enabled():return result
        kinds=()
        if category in (252,253) and variant==25:kinds=(25,)
        elif category==10 and variant==30:kinds=(20,21)
        elif category==10 and variant==67:kinds=(60,64,71,74)
        elif category==19 and variant==19:kinds=(20,21)
        elif category==67 and variant==67:kinds=(64,71,74,60)
        elif category==255:kinds=(25,12,13,14,15,16,17,18,20,21,60,64,71,74)
        elif category==variant:kinds=(variant,)
        result=[ShopRecord(raw) for kind in kinds for raw in self.repo.catalog(10,kind)]
        return tuple(result[:64] if category==255 else result)

    def cache(self):
        records={}
        for raw in self.repo.catalog_cache():
            record=ShopRecord(bytes(raw));key=record.u32(9)
            if key in records and records[key].raw!=record.raw:raise ValueError('ambiguous catalog key')
            if key not in records and len(records)>=MAX_RECORDS:raise ValueError('catalog cache exceeds frame bound')
            records[key]=record
        return tuple(records.values())

    def by_item(self,kind,item):
        result=[]
        for raw in self.repo.catalog_by_item(item):
            record=ShopRecord(raw)
            if record.raw[4]==kind:
                if len(result)>=MAX_RECORDS:raise ValueError('item catalog too large')
                result.append(record)
        return tuple(result)

    def strict_gold_offer(self,key):
        rows=self.repo.catalog_by_key(key)
        if len(rows)!=1:raise ValueError('missing or ambiguous offer')
        r=ShopRecord(rows[0])
        if (not r.raw[48] or r.raw[46] or r.raw[49] or r.raw[83] or r.u32(88) or r.u32(77) or
                not 0<r.u32(30)<=2147483647 or r.u32(34)!=r.u32(30) or r.u32(38) or r.u32(42)):
            raise ValueError('offer requires unsupported settlement rules')
        return r
