"""Mailbox rows and attachment receipts; callers own all transactions."""
from .transactions import require_transaction


def next_key(db,name,floor=0):
    require_transaction(db)
    db.execute('INSERT INTO counters VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=value+1',(name,floor+1))
    result=db.execute('SELECT value FROM counters WHERE name=?',(name,)).fetchone()[0]
    if not 0<result<2**32:raise ValueError('mail identity space exhausted')
    return result


class MailRepository:
    def __init__(self,db):self._db=db

    def prior_delivery(self,uid,delivery):
        return self._db.execute('SELECT id,signature FROM mailbox WHERE uid=? AND delivery_id=?',(uid,delivery)).fetchone()

    def pending_count(self,uid):
        return self._db.execute('SELECT COUNT(*) FROM mailbox WHERE uid=? AND deleted=0',(uid,)).fetchone()[0]

    def allocate_id(self,*,attachment=False):
        return next_key(self._db,'mail_attachment' if attachment else 'mail_id',0x200000 if attachment else 0)

    def insert(self,uid,key,delivery,signature,record,catalog,grant,attachment):
        require_transaction(self._db)
        self._db.execute('INSERT INTO mailbox(uid,id,delivery_id,signature,list_record,catalog,grant_record,attachment) VALUES(?,?,?,?,?,?,?,?)',
                         (uid,key,delivery,signature,record,catalog,grant,attachment))

    def listing(self,uid):
        return self._db.execute('SELECT list_record,is_read FROM mailbox WHERE uid=? AND deleted=0 ORDER BY id DESC LIMIT 2049',(uid,)).fetchall()

    def detail(self,uid,key):
        return self._db.execute('SELECT attachment,catalog FROM mailbox WHERE uid=? AND id=? AND deleted=0',(uid,key)).fetchone()

    def mark_read(self,uid,key):
        require_transaction(self._db);self._db.execute('UPDATE mailbox SET is_read=1 WHERE uid=? AND id=?',(uid,key))

    def attachment(self,uid,reference):
        return self._db.execute('SELECT id,grant_record,claimed_instance,deleted FROM mailbox WHERE uid=? AND attachment=? AND attachment<>0',(uid,reference)).fetchone()

    def mark_claimed(self,uid,key,instance):
        require_transaction(self._db)
        self._db.execute('UPDATE mailbox SET claimed_instance=?,is_read=1 WHERE uid=? AND id=?',(instance,uid,key))

    def exists(self,uid,key):
        return self._db.execute('SELECT 1 FROM mailbox WHERE uid=? AND id=?',(uid,key)).fetchone() is not None

    def remove(self,uid,key):
        require_transaction(self._db);self._db.execute('UPDATE mailbox SET deleted=1 WHERE uid=? AND id=?',(uid,key))
