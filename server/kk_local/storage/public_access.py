"""Public-mode invitation/access/asset audit metadata. No implicit migration."""
import hashlib
import secrets
from .transactions import require_transaction
from .public_schema import VERSION,version,migrate


def invite_digest(code):
    if not isinstance(code,str) or len(code)!=64 or any(c not in '0123456789abcdef' for c in code):raise ValueError('invitation_invalid')
    return hashlib.sha256(bytes.fromhex(code)).digest()

class PublicAccessRepository:
    def __init__(self,db):
        if version(db)!=VERSION:raise ValueError('public_schema_migration_required')
        self.db=db
    def create_invite(self,now,seconds=7*86400):
        require_transaction(self.db)
        if type(seconds) is not int or not 1<=seconds<=30*86400:raise ValueError('invitation lifetime')
        code=secrets.token_hex(32)
        self.db.execute('INSERT INTO public_invites(digest,created,expires) VALUES(?,?,?)',(invite_digest(code),now,now+seconds));return code
    def check_invite(self,digest,now):
        row=self.db.execute('SELECT expires,revoked,used_by FROM public_invites WHERE digest=?',(digest,)).fetchone()
        if row is None or row[0]<=now or row[1] or row[2] is not None:raise ValueError('invitation_invalid')
    def consume(self,digest,uid,now):
        require_transaction(self.db)
        cur=self.db.execute('UPDATE public_invites SET used_by=? WHERE digest=? AND expires>? AND revoked=0 AND used_by IS NULL',(uid,digest,now))
        if cur.rowcount!=1:raise ValueError('invitation_invalid')
    def revoke_invite(self,code):
        require_transaction(self.db);self.db.execute('UPDATE public_invites SET revoked=1 WHERE digest=?',(invite_digest(code),))
    def disabled(self,uid):return self.db.execute('SELECT 1 FROM public_disabled WHERE uid=? AND disabled=1',(uid,)).fetchone() is not None
    def set_disabled(self,uid,disabled):
        require_transaction(self.db);self.db.execute('INSERT INTO public_disabled VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET disabled=excluded.disabled',(uid,int(disabled)))
    def receipts(self,uid,after=0):
        if type(after) is not int or not 0<=after<=0x7fffffffffffffff:raise ValueError('invalid_cursor')
        rows=self.db.execute('SELECT id,kind,entity,old_value,new_value,created FROM public_ledger WHERE uid=? AND id>? ORDER BY id LIMIT 50',(uid,after)).fetchall()
        return [dict(zip(('id','kind','entity','before','after','created_at'),row)) for row in rows]
