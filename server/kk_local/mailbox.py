"""Native1300/1320/1340/2171 mailbox with server-owned item attachments.

No currency attachments or purchases. Delivery is an explicit local-admin API;
client queries cannot create mail or supply a grant record. Unknown wire fields
remain zero as local encoding policy, not recovered original-server values.
"""
import argparse
import hashlib
import json
from pathlib import Path
import struct
from .chat import system_notice
from .wire import Message
from .storage.mail import next_key  #legacy helper import, SQL has one owner


def text_bytes(value,limit):
    if not isinstance(value,str) or '\0' in value:raise ValueError('invalid mail text')
    raw=value.encode('gbk')
    if len(raw)>limit or raw.decode('gbk')!=value:raise ValueError('mail text too long/unrepresentable')
    return raw


def deliver(store,uid,delivery_id,*,title,sender,body,catalog=b'',grant=b''):
    with store.transaction('nested mail delivery'):
        return deliver_locked(store,uid,delivery_id,title=title,sender=sender,body=body,catalog=catalog,grant=grant)


def deliver_locked(store,uid,delivery_id,*,title,sender,body,catalog=b'',grant=b''):
    """Shared insertion for admin delivery and atomic shop debit+gift."""
    if not store.in_transaction:raise ValueError('mail delivery requires transaction')
    title=text_bytes(title,20);sender=text_bytes(sender,20);body=text_bytes(body,200)
    if not isinstance(delivery_id,str) or not 1<=len(delivery_id)<=128:raise ValueError('delivery identity required')
    if bool(catalog)!=bool(grant):raise ValueError('attachment requires complete catalog and grant')
    catalog=bytes(catalog);grant=bytes(grant)
    if grant:
        if (len(catalog)!=108 or len(grant)!=68 or catalog[4]!=grant[4] or
                not struct.unpack_from('<I',catalog,9)[0] or catalog[5:9]!=grant[5:9] or
                grant[4] not in (12,13,14,15,16,17,18,20,21,25,30,60,64,71,74) or
                struct.unpack_from('<H',grant,17)[0]!=0 or struct.unpack_from('<I',grant,19)[0] not in (0,1)):
            raise ValueError('invalid attachment catalog/template')
    signature=hashlib.sha256(b'\0'.join((title,sender,body,catalog,grant))).digest()
    prior=store.mail.prior_delivery(uid,delivery_id)
    if prior:
        if prior[1]!=signature:raise ValueError('delivery identity conflict')
        return prior[0]
    if not store.commerce.account_exists(uid):raise ValueError('mail recipient missing')
    if store.mail.pending_count(uid)>=2048:
        raise ValueError('mailbox full')
    key=store.mail.allocate_id();attachment=store.mail.allocate_id(attachment=True) if grant else 0
    record=bytearray(339);struct.pack_into('<I',record,0,key)
    for offset,data in ((4,title),(83,sender),(126,body)):record[offset:offset+len(data)]=data
    struct.pack_into('<I',record,331,attachment)
    #125 is raw remaining-time display, not a proven expiration contract.
    #Local mail has no expiry; leave it zero rather than claim a date.
    store.mail.insert(uid,key,delivery_id,signature,bytes(record),catalog,grant,attachment)
    return key


def listing(store,uid):
    result=[]
    for raw,read in store.mail.listing(uid):
        if len(result)>=2048 or len(raw)!=339:raise ValueError('mailbox bound/corrupt row')
        row=bytearray(raw);struct.pack_into('<I',row,327,int(read));result.append(bytes(row))
    return b''.join(result)


def detail(store,uid,key):
    with store.transaction(immediate=False):
        row=store.mail.detail(uid,key)
        if row is None:raise ValueError('mail unavailable')
        store.mail.mark_read(uid,key)
        return struct.pack('<7I',key,1,row[0],0,0,0,0)+bytes(row[1]).ljust(108,b'\0')


def claim(store,uid,attachment):
    with store.transaction('nested mail claim'):
        row=store.mail.attachment(uid,attachment)
        if row is None:raise ValueError('attachment missing')
        key,raw,existing,deleted=row
        if existing is not None:
            return store.inventory.get(uid,existing)
        if deleted or len(raw)!=68:raise ValueError('attachment unavailable')
        if store.inventory.count(uid)>=8000:raise ValueError('inventory full')
        instance=store._allocate_inventory_instance()
        record=bytearray(raw);struct.pack_into('<I',record,0,instance)
        store.inventory.insert(uid,instance,bytes(record))
        store.mail.mark_claimed(uid,key,instance)
        return bytes(record)


def remove(store,uid,key):
    with store.transaction(immediate=False):
        if not store.mail.exists(uid,key):return False
        store.mail.remove(uid,key)
    return True


def handle(engine,c,message):
    from .engine import Phase
    fail=lambda:[system_notice('[本地服务] 邮件操作未完成，请刷新本人邮件列表。')]
    if c is not engine.game or c.phase not in (Phase.LOBBY,Phase.ROOM):return []
    p=message.payload;ident=message.id
    if ident==1300:
        engine.require(not p,'mail query empty')
        try:return [Message(1310,listing(engine.store,c.uid))]
        except ValueError:return fail()
    if len(p)!=12:return fail()
    uid,key=struct.unpack('<QI',p)
    if uid!=c.uid or key==0:return fail()
    if ident==1320:
        engine.mail_preview=None;engine.mail_failed_delete=None
        try:response=detail(engine.store,uid,key)
        except ValueError:return fail()
        engine.mail_preview=(c,key,struct.unpack_from('<I',response,8)[0])
        return [Message(1330,response)]
    if ident==2171:
        preview=engine.mail_preview
        if preview and preview[0] is c:engine.mail_failed_delete=preview[1]
        if not preview or preview[0] is not c or preview[2]!=key:return fail()
        try:claim(engine.store,uid,key)
        except ValueError:return fail()
        engine.mail_failed_delete=None
        return [Message(1120,engine.store.snapshot(uid)[3])]
    #89EE80 sends2171 immediately followed by1340. If claiming failed,
    #suppress that one automatic delete so the attachment remains recoverable.
    if engine.mail_failed_delete==key:
        engine.mail_failed_delete=None;success=False
    else:success=remove(engine.store,uid,key)
    return [Message(1350,struct.pack('<BI',success,key))]


def main():
    p=argparse.ArgumentParser(description='Explicit offline mail delivery to an existing backed-up local database')
    p.add_argument('--database',required=True);p.add_argument('--message',required=True)
    args=p.parse_args()
    if not Path(args.database).is_file():p.error('existing database required')
    document=json.loads(Path(args.message).read_text(encoding='utf-8-sig'))
    required={'schema','uid','delivery_id','title','sender','body'}
    if (not isinstance(document,dict) or not required<=document.keys() or
            set(document)-required-{'catalog_hex','grant_hex'} or document['schema']!='kk-local-mail-v1'):
        p.error('invalid mail document')
    from .store import Store
    s=Store(args.database)
    try:
        key=deliver(s,document['uid'],document['delivery_id'],title=document['title'],sender=document['sender'],body=document['body'],
            catalog=bytes.fromhex(document.get('catalog_hex','')),grant=bytes.fromhex(document.get('grant_hex','')))
        print(json.dumps(dict(mail_id=key,inventory_granted=False)))
    finally:s.close()


if __name__=='__main__':main()
