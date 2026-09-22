"""Owned type30 use: native8291/8292 precede4201, unlike potion4200.

Wire facts: canonical99AB90/99AE70,A28290,99AA80,99A620,9DE410.
Billing/idempotency/5s pairing are local policy, not original-server recovery.
"""
from decimal import Decimal,InvalidOperation
from pathlib import Path
import struct
from .maps import ClientConfig
from .wire import Message,ProtocolError


class TalismanCatalog:
    def __init__(self,costs):self.costs=dict(costs)

    @classmethod
    def from_xml(cls,root):
        if root.tag!='TalismanPro' or len(root)>10000:raise ValueError('talisman table shape')
        costs={}
        for row in root:
            if row.tag!='Talisman':raise ValueError('talisman record shape')
            try:
                ident=int(row.attrib['Id'])
                values=[Decimal(row.attrib[k])*100 for k in ('EquipCostPerBattle','ActiveCost')]
                if not 0<ident<2**32 or any(not v.is_finite() or v!=v.to_integral_value() or not 0<=v<=65535 for v in values):
                    raise ValueError('invalid talisman costs')
                value=tuple(map(int,values))
            except (KeyError,InvalidOperation):raise ValueError('missing/invalid talisman costs') from None
            if ident in costs and costs[ident]!=value:raise ValueError('conflicting talisman costs')
            costs[ident]=value
        return cls(costs)

    @classmethod
    def from_client(cls,root):
        return cls.from_xml(ClientConfig(Path(root)/'Data/config.spf2').xml('talisman.xml'))


def equipped(store,uid,*,instance=None,slot=None):
    rows=[]
    for i,raw in store.inventory.rows(uid):
        if raw[4]!=30:continue
        current=struct.unpack_from('<H',raw,17)[0]
        if (instance is None or instance==i) and (slot is None or current==slot):rows.append((i,raw))
    if len(rows)!=1 or struct.unpack_from('<H',rows[0][1],17)[0] not in (37,38):raise ValueError('talisman not uniquely equipped')
    if struct.unpack_from('<I',rows[0][1],19)[0] not in (0,1):raise ValueError('talisman unavailable')
    return rows[0]


def spend(store,uid,battle,instance,slot,kind,sequence,cost):
    """Atomic remaining quota; passive billing once/item/battle, not once/effect."""
    if store.in_transaction:raise ValueError('nested talisman billing')
    if kind not in (8291,8292) or slot not in (37,38) or not 0<=cost<=65535:raise ValueError('invalid talisman billing')
    seq=0 if kind==8291 else sequence
    with store.transaction():
        _,raw=equipped(store,uid,instance=instance,slot=slot)
        prior=store.talisman.use_receipt(uid,battle,instance,kind,seq)
        quantity=struct.unpack_from('<H',raw,23)[0]
        if prior:
            if prior[0]!=cost:raise ValueError('talisman billing identity conflict')
            return quantity,False
        if quantity<cost:raise ArithmeticError('talisman quota exhausted')
        quantity-=cost;new=bytearray(raw);struct.pack_into('<H',new,23,quantity)
        store.inventory.update(uid,instance,bytes(new))
        store.talisman.record_use(uid,battle,instance,kind,seq,cost)
        return quantity,True


def handle(hub,engine,c,message,*,observed_recipients=()):
    from .engine import Phase
    room=engine.room
    if (c is None or c is not engine.game or c.phase!=Phase.BATTLE or room is None or room.stage!='battle' or
            c.uid not in room.fighters or room.members[c.uid].engine is not engine):return []
    if hub.talisman_catalog is None:
        engine.record_unknown(c,message.id,message.payload);return []
    uid=c.uid;p=bytes(message.payload);now=engine.clock()
    for key,pending in tuple(room.talisman_pending.items()):
        if pending['until']<=now:del room.talisman_pending[key]
    if message.id==8071:
        if len(p)!=75 or p[12:14]!=b'\1\1':raise ProtocolError('talisman event shape')
        kind,sender=struct.unpack_from('<IQ',p)
        slot=struct.unpack_from('<I',p,39)[0];actor=struct.unpack_from('<Q',p,59)[0]
        engine.require(kind in (8291,8292) and sender==uid and actor==uid,'talisman identity')
        if slot not in (37,38) or struct.unpack_from('<II',p,67)!=(room.number,room.serial):return []
        try:instance,raw=equipped(engine.store,uid,slot=slot)
        except ValueError:return []
        item=struct.unpack_from('<I',raw,5)[0]
        rule=hub.talisman_catalog.costs.get(item)
        if rule is None:return []
        key=(uid,instance)
        if key not in room.talisman_pending:
            room.talisman_pending[key]=dict(until=now+5,payload=p,kind=kind,slot=slot,item=item,
                sequence=struct.unpack_from('<I',p,19)[0],cost=rule[kind==8292],delivered=set(observed_recipients))
        elif room.talisman_pending[key]['payload']==p:
            room.talisman_pending[key]['delivered'].update(observed_recipients)
        return []
    engine.require(len(p)==8,'talisman use exact8')
    instance=struct.unpack_from('<I',p)[0]  # second DWORD is NOT a trusted price
    pending=room.talisman_pending.pop((uid,instance),None)
    if pending is None:return []
    key=(uid,instance,pending['kind']);sequence=pending['sequence']
    previous=room.talisman_sequences.get(key,-1)
    if sequence<previous:return []
    try:
        quantity,fresh=spend(engine.store,uid,room.serial,instance,pending['slot'],pending['kind'],sequence,pending['cost'])
    except ArithmeticError:return [Message(4207,struct.pack('<II',instance,pending['item']))]
    except ValueError:return []
    if sequence>previous:
        # Native caller already applied its effect. Only replicas receive it.
        if fresh or pending['kind']==8291:
            for other,m in room.members.items():
                if other!=uid and other not in pending['delivered']:hub.queue(m.engine,Message(8071,pending['payload']))
        room.talisman_sequences[key]=sequence
    return [Message(4206,struct.pack('<III',instance,quantity,0))]
