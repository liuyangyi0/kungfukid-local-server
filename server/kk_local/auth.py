"""Local account authentication, separate from the retired SDO SDK protocol.

No implicit password is assigned to legacy accounts. Passwords use salted
scrypt; session/ticket secrets are returned once and stored only as digests.
This module does NOT turn the existing offline SDK bypass into authentication.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import hashlib
import hmac
import re
import secrets
import sqlite3
import time


ALGORITHM = 'scrypt-n32768-r8-p3-v1'
SESSION_SECONDS = 8*3600
TICKET_SECONDS = 45


class AuthError(ValueError):
    """Public error code only; never embed a password or bearer credential."""


def normalize_account(account):
    if not isinstance(account,str) or not re.fullmatch(r'[A-Za-z0-9]{3,20}',account):
        raise AuthError('invalid_account_format')
    return account.lower()


def password_bytes(password):
    if not isinstance(password,str) or not 12<=len(password)<=128:
        raise AuthError('password_length_12_to_128_required')
    try:
        encoded=password.encode('utf-8')
    except UnicodeError:
        raise AuthError('invalid_password_encoding') from None
    if len(encoded)>512:
        raise AuthError('password_too_long')
    return encoded


def password_digest(encoded, salt):
    # OWASP scrypt's32MiB alternative; standard library, no plugin dependency.
    return hashlib.scrypt(encoded,salt=salt,n=32768,r=8,p=3,dklen=32,maxmem=128*1024*1024)


def token_digest(token):
    if not isinstance(token,str) or not re.fullmatch(r'[0-9a-f]{64}',token):
        raise AuthError('invalid_session')
    return hashlib.sha256(bytes.fromhex(token)).digest()


class AuthManager:
    def __init__(self, store, regions, *, clock=time.time, native_verifier=None):
        self.store=store
        self.db=store.db
        self.clock=clock
        self.native_verifier=native_verifier
        self.native_bindings={}
        self.regions={}
        for region in regions:
            if (not isinstance(region,dict) or set(region)!={'id','name','host','game_port'} or
                    type(region['id']) is not int or not 0<region['id']<=0xffffffff or
                    region['id'] in self.regions or region['host']!='127.0.0.1' or
                    type(region['game_port']) is not int or not 1<=region['game_port']<=65535 or
                    not isinstance(region['name'],str) or not 1<=len(region['name'])<=40):
                raise AuthError('invalid_local_region_configuration')
            self.regions[region['id']]=dict(region)
        if not self.regions or len(self.regions)>16:
            raise AuthError('invalid_local_region_configuration')
        self.pool=ThreadPoolExecutor(max_workers=2,thread_name_prefix='kk-password')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS auth_credentials(
            uid INTEGER PRIMARY KEY REFERENCES accounts(uid),
            normalized_account TEXT NOT NULL UNIQUE,
            algorithm TEXT NOT NULL, salt BLOB NOT NULL CHECK(length(salt)=16),
            digest BLOB NOT NULL CHECK(length(digest)=32));
          CREATE TABLE IF NOT EXISTS auth_sessions(
            digest BLOB PRIMARY KEY CHECK(length(digest)=32),
            uid INTEGER NOT NULL REFERENCES auth_credentials(uid),
            created INTEGER NOT NULL, expires INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS auth_tickets(
            digest BLOB PRIMARY KEY CHECK(length(digest)=32),
            session_digest BLOB NOT NULL REFERENCES auth_sessions(digest) ON DELETE CASCADE,
            uid INTEGER NOT NULL REFERENCES auth_credentials(uid),
            region INTEGER NOT NULL, expires INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS auth_failures(
            account TEXT PRIMARY KEY, failures INTEGER NOT NULL,
            blocked_until INTEGER NOT NULL, updated INTEGER NOT NULL);
        ''')

    async def _hash(self, encoded, salt):
        return await asyncio.get_running_loop().run_in_executor(self.pool,password_digest,encoded,salt)

    def close(self):
        self.pool.shutdown(wait=True,cancel_futures=True)

    def _cleanup(self, now):
        self.db.execute('DELETE FROM auth_sessions WHERE expires<=?',(now,))
        self.db.execute('DELETE FROM auth_tickets WHERE expires<=?',(now,))
        self.db.execute('DELETE FROM auth_failures WHERE updated<?',(now-3600,))

    async def register(self, account, password, nickname=None):
        name=normalize_account(account)
        encoded=password_bytes(password)
        nickname=account if nickname is None else nickname
        try:
            valid=isinstance(nickname,str) and 1<=len(nickname.encode('gbk'))<=20 and '\0' not in nickname
        except UnicodeError:
            valid=False
        if not valid:
            raise AuthError('invalid_nickname')
        salt=secrets.token_bytes(16)
        digest=await self._hash(encoded,salt)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            # Existing no-password fixture accounts are reserved, not claimable
            # through public registration. Local administration is explicit.
            if self.db.execute('SELECT 1 FROM accounts WHERE account=? COLLATE NOCASE',(name,)).fetchone():
                raise AuthError('account_unavailable')
            uid=max(1000,self.db.execute('SELECT COALESCE(MAX(uid),0) FROM accounts').fetchone()[0])+1
            if uid>0x7fffffffffffffff:
                raise AuthError('account_capacity_reached')
            self.store.provision_local(uid,name,nickname)
            self.db.execute('INSERT INTO auth_credentials VALUES(?,?,?,?,?)',(uid,name,ALGORITHM,salt,digest))
            self.db.commit()
            return dict(uid=uid,account=name,nickname=nickname)
        except BaseException:
            self.db.rollback()
            raise

    async def login(self, account, password):
        name=normalize_account(account)
        encoded=password_bytes(password)
        now=int(self.clock())
        with self.db:
            self._cleanup(now)
        blocked=self.db.execute('SELECT blocked_until FROM auth_failures WHERE account=?',(name,)).fetchone()
        if blocked and blocked[0]>now:
            raise AuthError('rate_limited')
        row=self.db.execute('SELECT uid,algorithm,salt,digest FROM auth_credentials WHERE normalized_account=?',(name,)).fetchone()
        salt=row[2] if row and row[1]==ALGORITHM else bytes(16)
        expected=row[3] if row and row[1]==ALGORITHM else bytes(32)
        actual=await self._hash(encoded,salt)
        valid=hmac.compare_digest(actual,expected) and row is not None and row[1]==ALGORITHM
        now=int(self.clock())
        self.db.execute('BEGIN IMMEDIATE')
        try:
            # Do not grant a stale password after a concurrent local reset.
            current=self.db.execute('SELECT uid,algorithm,salt,digest FROM auth_credentials WHERE normalized_account=?',(name,)).fetchone()
            valid=valid and current==row
            blocked=self.db.execute('SELECT blocked_until FROM auth_failures WHERE account=?',(name,)).fetchone()
            if blocked and blocked[0]>now:
                raise AuthError('rate_limited')
            if not valid:
                old=self.db.execute('SELECT failures FROM auth_failures WHERE account=?',(name,)).fetchone()
                failures=min(5,(old[0] if old else 0)+1)
                if old or self.db.execute('SELECT COUNT(*) FROM auth_failures').fetchone()[0]<10000:
                    self.db.execute('INSERT OR REPLACE INTO auth_failures VALUES(?,?,?,?)',
                                    (name,failures,now+30 if failures>=5 else 0,now))
                self.db.commit()
                raise AuthError('invalid_credentials')
            uid=row[0]
            self._cleanup(now)
            self.db.execute('DELETE FROM auth_failures WHERE account=?',(name,))
            # Bound sessions per account. Tokens from the oldest sessions and
            # their outstanding region tickets are revoked together.
            old=self.db.execute('SELECT digest FROM auth_sessions WHERE uid=? ORDER BY created DESC,rowid DESC',(uid,)).fetchall()
            for item in old[2:]:
                self.db.execute('DELETE FROM auth_sessions WHERE digest=?',item)
            token=secrets.token_hex(32)
            self.db.execute('INSERT INTO auth_sessions VALUES(?,?,?,?)',(token_digest(token),uid,now,now+SESSION_SECONDS))
            self.db.commit()
            return dict(uid=uid,account=name,session=token,expires_at=now+SESSION_SECONDS)
        except BaseException:
            if self.db.in_transaction:
                self.db.rollback()
            raise

    def _session(self, token):
        digest=token_digest(token)
        row=self.db.execute('SELECT uid,expires FROM auth_sessions WHERE digest=?',(digest,)).fetchone()
        if not row or row[1]<=int(self.clock()):
            raise AuthError('invalid_session')
        return digest,row[0]

    def list_regions(self, token):
        self._session(token)
        return [dict(r) for r in self.regions.values()]

    def select_region(self, token, region_id):
        if type(region_id) is not int or region_id not in self.regions:
            raise AuthError('invalid_region')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            digest,uid=self._session(token)
            now=int(self.clock())
            self.db.execute('DELETE FROM auth_tickets WHERE session_digest=?',(digest,))
            ticket=secrets.token_hex(32)
            self.db.execute('INSERT INTO auth_tickets VALUES(?,?,?,?,?)',
                            (token_digest(ticket),digest,uid,region_id,now+TICKET_SECONDS))
            self.db.commit()
            return dict(uid=uid,region=dict(self.regions[region_id]),ticket=ticket,expires_at=now+TICKET_SECONDS)
        except BaseException:
            self.db.rollback()
            raise

    def consume_ticket(self, ticket, uid, region_id):
        """For a trusted game gateway, not a publicly callable API operation."""
        if type(uid) is not int or type(region_id) is not int:
            raise AuthError('invalid_ticket')
        digest=token_digest(ticket)
        self.db.execute('BEGIN IMMEDIATE')
        try:
            row=self.db.execute('SELECT t.uid,t.region,t.expires,s.expires FROM auth_tickets t JOIN auth_sessions s ON s.digest=t.session_digest WHERE t.digest=?',(digest,)).fetchone()
            now=int(self.clock())
            if not row or row[:2]!=(uid,region_id) or min(row[2:])<=now:
                raise AuthError('invalid_ticket')
            self.db.execute('DELETE FROM auth_tickets WHERE digest=?',(digest,))
            self.db.commit()
            return uid
        except BaseException:
            self.db.rollback()
            raise

    def bind_native_client(self, ticket, uid, region_id, pid):
        if self.native_verifier is None:
            raise AuthError('native_bridge_unavailable')
        try:
            identity=self.native_verifier.process(pid)
        except ValueError:
            raise AuthError('native_process_rejected') from None
        # A running binding cannot be reassigned to another login, even with
        # a valid password. A fresh process/session is required.
        old=self.native_bindings.get(pid)
        if old and old['identity']==identity:
            raise AuthError('native_process_already_bound')
        for key,value in list(self.native_bindings.items()):
            if value['stage']!='game' and value['expires']<=int(self.clock()):
                self.native_bindings.pop(key,None)
        if len(self.native_bindings)>=16:
            raise AuthError('native_binding_limit')
        digest=token_digest(ticket)
        row=self.db.execute('SELECT session_digest FROM auth_tickets WHERE digest=?',(digest,)).fetchone()
        self.consume_ticket(ticket,uid,region_id)
        self.native_bindings[pid]=dict(identity=identity,uid=uid,region=region_id,
            session_digest=row[0],expires=int(self.clock())+240,stage='bound',lobby_ready=False)
        return dict(pid=pid,uid=uid,region_id=region_id,stage='bound')

    def native_binding(self, identity, region_id):
        row=self.native_bindings.get(identity.pid)
        if (not row or row['identity']!=identity or row['region']!=region_id or
                (row['stage']!='game' and row['expires']<=int(self.clock()))):
            raise AuthError('native_session_required')
        session=self.db.execute('SELECT expires FROM auth_sessions WHERE digest=?',(row['session_digest'],)).fetchone()
        if not session or session[0]<=int(self.clock()):
            raise AuthError('native_session_expired')
        return row

    def native_status(self, token, pid):
        _,uid=self._session(token)
        row=self.native_bindings.get(pid)
        if not row or row['uid']!=uid:
            raise AuthError('native_session_required')
        try:
            identity=self.native_verifier.process(pid)
            row=self.native_binding(identity,row['region'])
        except (ValueError,AuthError):
            raise AuthError('native_process_unavailable') from None
        return dict(pid=pid,uid=uid,stage=row['stage'],lobby_ready=row['lobby_ready'])

    def release_native(self, identity):
        row=self.native_bindings.get(identity.pid)
        if row and row['identity']==identity:
            self.native_bindings.pop(identity.pid,None)

    def logout(self, token):
        digest=token_digest(token)
        with self.db:
            self.db.execute('DELETE FROM auth_sessions WHERE digest=?',(digest,))

    async def set_local_password(self, account, password):
        """Local administrator-only recovery; never exposed through the network."""
        name=normalize_account(account)
        row=self.db.execute('SELECT uid FROM accounts WHERE account=? COLLATE NOCASE',(name,)).fetchall()
        if len(row)!=1:
            raise AuthError('local_account_missing_or_ambiguous')
        salt=secrets.token_bytes(16)
        digest=await self._hash(password_bytes(password),salt)
        uid=row[0][0]
        with self.db:
            self.db.execute('DELETE FROM auth_sessions WHERE uid=?',(uid,))
            self.db.execute('INSERT INTO auth_credentials VALUES(?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET normalized_account=excluded.normalized_account,algorithm=excluded.algorithm,salt=excluded.salt,digest=excluded.digest',
                            (uid,name,ALGORITHM,salt,digest))
            self.db.execute('DELETE FROM auth_failures WHERE account=?',(name,))
