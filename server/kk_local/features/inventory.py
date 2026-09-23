"""Equipment and consumption rules; SQL and commits belong to storage/UoW."""
import struct

# 8A3D10 constructs the UI slot table; 8A9700 maps suit kind18 to4.
EQUIPMENT_SLOTS = {12:(4,),13:(3,),14:(7,),15:(2,),16:(6,),17:(5,),
                   18:(4,),20:(10,),21:(11,),25:(8,9),30:(37,38),64:(27,28)}

# Local long-duration presentation, NOT an original-server permanent sentinel.
# Historical provenance and version limits: protocol document section 40.
PERMANENT_WEAPON_DISPLAY_MINUTES = 5256000


def permanent_weapon_record(raw):
    """Encode an explicitly granted local entitlement; preserve other fields."""
    if len(raw) != 68 or raw[4] != 25:
        raise ValueError('permanent policy requires a weapon record')
    return permanent_equipment_record(raw)


def permanent_equipment_record(raw):
    """User-selected local no-expiry entitlement for supported outfit/weapon kinds."""
    if len(raw)!=68 or raw[4] not in (12,13,14,15,16,17,18,20,21,25):
        raise ValueError('unsupported permanent equipment kind')
    record = bytearray(raw)
    struct.pack_into('<I', record, 13, PERMANENT_WEAPON_DISPLAY_MINUTES)
    struct.pack_into('<I', record, 19, 1)
    struct.pack_into('<H', record, 23, 0)
    return bytes(record)


class InventoryService:
    def __init__(self,store):self.store=store;self.repo=store.inventory

    def apply_grant(self,plan):
        if plan.get('schema')!='kk-local-inventory-grant-v1' or plan.get('uid')!=1001:raise ValueError('invalid grant schema/identity')
        grant_id=plan.get('grant_id')
        if not isinstance(grant_id,str) or not 1<=len(grant_id)<=128:raise ValueError('invalid grant identity')
        rows=plan.get('rows')
        if not isinstance(rows,list) or not 1<=len(rows)<=4000:raise ValueError('invalid grant size')
        seen=set()
        for row in rows:
            if (not isinstance(row,list) or len(row)!=3 or any(type(x) is not int for x in row) or
                    not 0<row[0]<=0xffffffff or row[1] not in (12,13,14,15,16,17,18,20,21,25,64,71,74) or
                    not 1<=row[2]<=999 or row[0] in seen):raise ValueError('invalid or duplicate grant record')
            seen.add(row[0])
        if self.repo.grant_applied(1001,grant_id):return 0
        records=self.repo.rows(1001);permanent=self.repo.permanent_instances(1001)
        existing={(r[4],struct.unpack_from('<I',r,5)[0]):(i,r) for i,r in records}
        if len(records)+len(rows)>8000:raise ValueError('inventory bound exceeded')
        added=0
        with self.store.transaction('nested inventory grant'):
            for prop,kind,quantity in rows:
                prior=existing.get((kind,prop))
                if prior:instance,original=prior;record=bytearray(original)
                else:
                    instance=self.repo.allocate_instance();added+=1;record=bytearray(68)
                    struct.pack_into('<IBI',record,0,instance,kind,prop)
                struct.pack_into('<H',record,23,max(quantity,struct.unpack_from('<H',record,23)[0]))
                if instance in permanent:record=permanent_equipment_record(record)
                self.repo.upsert(1001,instance,bytes(record))
            self.repo.record_grant(1001,grant_id)
        return added

    def set_weapons_permanent(self,uid):
        if self.store.in_transaction:raise ValueError('nested permanent weapon transaction')
        if not self.store.commerce.account_exists(uid):raise ValueError('unknown local account')
        count=0
        with self.store.transaction(immediate=False):
            for instance,raw in self.repo.rows(uid,ordered=True):
                if raw[4]!=25:continue
                record=permanent_weapon_record(raw)
                self.repo.add_permanent(uid,instance)
                if record!=raw:self.repo.update(uid,instance,record)
                count+=1
        return count

    def equip(self,uid,instance,slot):
        raw=self.repo.get(uid,instance)
        if raw is None:raise ValueError('item not owned')
        record=bytearray(raw)
        #Preserve the existing native warehouse slot0 -> primary weapon policy.
        if slot==0 and record[4]==25:slot=8
        if slot not in EQUIPMENT_SLOTS.get(record[4],()):raise ValueError('unqualified item slot')
        with self.store.transaction(immediate=False):
            for other,raw in self.repo.rows(uid):
                if other!=instance and struct.unpack_from('<H',raw,17)[0]==slot:
                    changed=bytearray(raw);struct.pack_into('<H',changed,17,0)
                    self.repo.update(uid,other,bytes(changed))
            struct.pack_into('<H',record,17,slot)
            self.repo.update(uid,instance,bytes(record))
        return bytes(record)

    def unequip(self,uid,instance):
        raw=self.repo.get(uid,instance)
        if raw is None:raise ValueError('item not owned')
        record=bytearray(raw);slot=struct.unpack_from('<H',record,17)[0]
        if slot==0:return None  #do not repeat native teardown
        if slot not in EQUIPMENT_SLOTS.get(record[4],()):raise ValueError('unqualified item slot')
        struct.pack_into('<H',record,17,0)
        with self.store.transaction(immediate=False):self.repo.update(uid,instance,bytes(record))
        return bytes(record)

    def weapon_switch_snapshot(self,uid):
        #Native4083 targets the first kind74 record; ambiguous duplicates deny.
        records=[bytes(raw) for _,raw in self.repo.rows(uid,ordered=True)]
        tokens=[r for r in records if r[4]==74]
        counts={slot:sum(r[4]==25 and struct.unpack_from('<H',r,17)[0]==slot for r in records) for slot in (8,9)}
        quantity=struct.unpack_from('<H',tokens[0],23)[0] if len(tokens)==1 else 0
        return len(tokens)<=1 and counts=={8:1,9:1},quantity

    def consumable(self,uid,*,instance=None,slot=None):
        rows=self.repo.rows(uid,instance=instance)
        if instance is None:rows=[(i,r) for i,r in rows if struct.unpack_from('<H',r,17)[0]==slot]
        if len(rows)!=1:raise ValueError('consumable missing or ambiguous')
        i,r=rows[0]
        if r[4]!=64 or struct.unpack_from('<H',r,17)[0] not in (27,28):raise ValueError('not an equipped owned consumable')
        return i,r

    def consume_once(self,uid,battle,sequence,instance,signature,intent):
        with self.store.transaction('nested consumption transaction'):
            old=self.repo.consumption_receipt(uid,battle,sequence)
            if old:
                if old!=(instance,signature):raise ValueError('consumption event identity conflict')
                return False
            if not intent:raise ValueError('missing4200 intent')
            _,raw=self.consumable(uid,instance=instance)
            count=struct.unpack_from('<H',raw,23)[0]
            if count==0:raise ValueError('consumable exhausted')
            record=bytearray(raw);struct.pack_into('<H',record,23,count-1)
            self.repo.update(uid,instance,bytes(record))
            self.repo.record_consumption(uid,battle,sequence,instance,signature,count-1)
            return True

    def consumable_slots(self,uid):
        result={}
        for slot in (27,28):
            try:result[slot]=self.consumable(uid,slot=slot)[1]
            except ValueError:result[slot]=bytes(68)
        return result
