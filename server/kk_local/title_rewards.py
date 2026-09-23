"""Native4125 selector and4126 claim with explicit local entitlements.

Qualification is never inferred from a client choice or an arbitrary title
number. Tutorial completion and offline administration are separate issuers.
"""
import argparse
import json
from pathlib import Path
import struct
from .maps import ClientConfig
from .chat import system_notice
from .wire import Message
from .shop_catalog import MAX_RECORDS


def levels_from_client(root):
    root=ClientConfig(Path(root)/'Data/config.spf2').xml('roletitle.xml')
    if root.tag!='TitleSetting':raise ValueError('title table shape')
    levels=[]
    for row in root:
        level=int(row.attrib['Level'])
        if row.tag!='TitleLevel' or not 0<=level<=255 or level in levels:raise ValueError('title identity bounds/duplicate')
        levels.append(level)
    if not levels:raise ValueError('empty title table')
    return frozenset(levels)


def validate_choices(choices):
    if not isinstance(choices,list) or len(choices)>7:raise ValueError('title choices must contain0..7 options')
    seen=set()
    for row in choices:
        if not isinstance(row,dict) or set(row)!={'catalog_hex','grant_hex'}:raise ValueError('title option fields')
        catalog=bytes.fromhex(row['catalog_hex']);grant=bytes.fromhex(row['grant_hex'])
        if len(catalog)!=108 or len(grant)!=68:raise ValueError('title option full records required')
        key=struct.unpack_from('<I',catalog,9)[0]
        if (not key or key in seen or catalog[4]!=25 or grant[4]!=25 or catalog[5:9]!=grant[5:9] or
                not struct.unpack_from('<I',catalog,5)[0] or struct.unpack_from('<H',grant,17)[0] or
                struct.unpack_from('<I',grant,19)[0] not in (0,1)):
            raise ValueError('title option identity/type/slot')
        seen.add(key)
    return json.dumps(choices,sort_keys=True,separators=(',',':'))


def configure(store,level,choices,allowed):
    if type(level) is not int or not 1<=level<=255 or level not in allowed:raise ValueError('unqualified title level')
    data=validate_choices(choices)
    with store.transaction('nested title rule update',immediate=False):
        store.titles.set_rule(level,data)


def catalog_for(store,choices):
    #Native85C5C0 inserts by DWORD+9; first matching key wins. Do not send a
    #conflicting reward after a cached shop row and assume the client replaces it.
    records={r.u32(9):r.raw for r in store.shop_cache_records()}
    for choice in choices:
        raw=bytes.fromhex(choice['catalog_hex']);key=struct.unpack_from('<I',raw,9)[0]
        if key in records and records[key]!=raw:raise ValueError('reward catalogue conflicts with existing shop key')
        records[key]=raw
    if len(records)>MAX_RECORDS:raise ValueError('reward catalogue exceeds frame')
    return b''.join(records.values())


def profile_title(store,uid):return store.snapshot(uid)[2][123]


def issue_locked(store,uid,level,allowed,source):
    if not store.in_transaction:raise ValueError('title issue needs transaction')
    if level not in allowed or not 1<=level<=255:raise ValueError('title not in current client table')
    prior=store.titles.entitlement(uid,level)
    if prior:return json.loads(prior.choices)
    profile=bytearray(store.snapshot(uid)[2])
    if profile[123]>level:raise ValueError('title must not downgrade profile')
    if store.titles.pending(uid) is not None:
        raise ValueError('another title reward is pending')
    rule=store.titles.rule(level)
    data=rule[0] if rule else '[]';choices=json.loads(data)
    validate_choices(choices);catalog_for(store,choices)
    store.titles.insert(uid,level,source,data,None if choices else 0)
    profile[123]=level
    store.profiles.update_profile(uid,bytes(profile))
    return choices


def issue(store,uid,level,allowed):
    with store.transaction('nested title grant'):
        issue_locked(store,uid,level,allowed,'explicit_admin')


def award_packet(level,choices):
    p=bytearray(64);p[0]=level
    for i,row in enumerate(choices):p[8+i*8:12+i*8]=bytes.fromhex(row['catalog_hex'])[9:13]
    return Message(4125,bytes(p))


def announce(engine,c,*,sync_empty=False):
    allowed=getattr(engine.map_catalog,'title_levels',())
    if c is not engine.game:return []
    bound=engine.title_offer
    if bound and bound[0] is not c:return []
    if bound:
        row=engine.store.titles.entitlement(c.uid,bound[1])
    else:
        row=engine.store.titles.pending(c.uid)
    if row is not None and row.claimed_key is None:
        level,raw=row.level,row.choices
        if level not in allowed or profile_title(engine.store,c.uid)!=level:raise ValueError('title offer/profile/source mismatch')
        choices=json.loads(raw);catalog=catalog_for(engine.store,choices)
        engine.title_offer=(c,level)
        return [Message(1550,catalog),award_packet(level,choices)]
    if sync_empty:
        level=profile_title(engine.store,c.uid)
        if level not in allowed:raise ValueError('profile title absent from source')
        #Known limitation: no independent non-modal title-sync packet recovered.
        return [award_packet(level,[])]
    return []


def claim(store,uid,level,key):
    with store.transaction('nested title claim'):
        row=store.titles.entitlement(uid,level)
        if row is None:raise ValueError('title entitlement missing')
        choices,claimed,instance=row.choices,row.claimed_key,row.claimed_instance
        if claimed is not None:
            if claimed!=key:raise ValueError('another reward already chosen')
            return store.inventory.get(uid,instance)
        choices=json.loads(choices)
        selected=next((r for r in choices if struct.unpack_from('<I',bytes.fromhex(r['catalog_hex']),9)[0]==key),None)
        if selected is None:raise ValueError('reward key not offered')
        if store.inventory.count(uid)>=8000:raise ValueError('inventory full')
        item=bytearray.fromhex(selected['grant_hex']);instance=store.inventory.allocate_instance();struct.pack_into('<I',item,0,instance)
        store.inventory.insert(uid,instance,bytes(item))
        store.titles.mark_claimed(uid,level,key,instance)
        return bytes(item)


def handle_claim(engine,c,payload):
    from .engine import Phase
    fail=lambda:[system_notice('[本地服务] 领取未完成，请先打开本人已获得的奖励。')]
    if c is not engine.game or c.phase not in (Phase.LOBBY,Phase.ROOM):return []
    if engine.room and engine.hub and (engine.room.stage!='room' or engine.room.members[c.uid].ready):return fail()
    bound=engine.title_offer
    if len(payload)!=149 or bound is None or bound[0] is not c:return fail()
    key=struct.unpack_from('<I',payload,145)[0]
    if not key:return fail()
    try:record=claim(engine.store,c.uid,bound[1],key)
    except ValueError:return fail()
    #2160 is an absolute upsert, safe for response loss; no fake4127 ACK.
    return ([Message(2160,record)] if record else [])+[system_notice('[本地服务] 奖励已领取，请查看背包。')]


def main():
    p=argparse.ArgumentParser(description='Explicit local title rules/eligibility; stop service and back up DB first')
    p.add_argument('--database',required=True);p.add_argument('--client-root',required=True)
    group=p.add_mutually_exclusive_group(required=True)
    group.add_argument('--rules');group.add_argument('--issue',nargs=2,type=int,metavar=('UID','LEVEL'))
    args=p.parse_args()
    if not Path(args.database).is_file():p.error('existing database required')
    allowed=levels_from_client(args.client_root)
    from .store import Store
    s=Store(args.database)
    try:
        if args.rules:
            d=json.loads(Path(args.rules).read_text(encoding='utf-8-sig'))
            if set(d)!={'schema','level','choices'} or d['schema']!='kk-title-reward-rule-v1':raise ValueError('title rule document shape')
            configure(s,d['level'],d['choices'],allowed)
        else:issue(s,*args.issue,allowed)
        print(json.dumps(dict(status='configured',items_granted=0,policy='SYSTEM_DESIGN_INFERRED')))
    finally:s.close()


if __name__=='__main__':main()
