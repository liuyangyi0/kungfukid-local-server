"""Credential/session persistence; no plaintext passwords or bearer tokens."""
from .transactions import require_transaction


class AuthRepository:
    def __init__(self, db):
        self._db = db

    def credential(self, name):
        return self._db.execute(
            'SELECT uid,algorithm,salt,digest FROM auth_credentials WHERE normalized_account=?', (name,)).fetchone()

    def insert_credential(self, uid, name, algorithm, salt, digest):
        require_transaction(self._db)
        self._db.execute('INSERT INTO auth_credentials VALUES(?,?,?,?,?)', (uid, name, algorithm, salt, digest))

    def replace_credential(self, uid, name, algorithm, salt, digest):
        require_transaction(self._db)
        self._db.execute('INSERT INTO auth_credentials VALUES(?,?,?,?,?) ON CONFLICT(uid) DO UPDATE SET '
                         'normalized_account=excluded.normalized_account,algorithm=excluded.algorithm,'
                         'salt=excluded.salt,digest=excluded.digest', (uid, name, algorithm, salt, digest))

    def failure(self, name):
        return self._db.execute('SELECT failures,blocked_until FROM auth_failures WHERE account=?', (name,)).fetchone()

    def failure_count(self):
        return self._db.execute('SELECT COUNT(*) FROM auth_failures').fetchone()[0]

    def record_failure(self, name, failures, blocked_until, now):
        require_transaction(self._db)
        self._db.execute('INSERT OR REPLACE INTO auth_failures VALUES(?,?,?,?)', (name, failures, blocked_until, now))

    def clear_failure(self, name):
        require_transaction(self._db)
        self._db.execute('DELETE FROM auth_failures WHERE account=?', (name,))

    def cleanup(self, now, failures_before):
        require_transaction(self._db)
        self._db.execute('DELETE FROM auth_sessions WHERE expires<=?', (now,))
        self._db.execute('DELETE FROM auth_tickets WHERE expires<=?', (now,))
        self._db.execute('DELETE FROM auth_failures WHERE updated<?', (failures_before,))

    def session(self, digest):
        return self._db.execute('SELECT uid,expires FROM auth_sessions WHERE digest=?', (digest,)).fetchone()

    def session_digests(self, uid):
        return self._db.execute(
            'SELECT digest FROM auth_sessions WHERE uid=? ORDER BY created DESC,rowid DESC', (uid,)).fetchall()

    def insert_session(self, digest, uid, now, expires):
        require_transaction(self._db)
        self._db.execute('INSERT INTO auth_sessions VALUES(?,?,?,?)', (digest, uid, now, expires))

    def remove_session(self, digest):
        require_transaction(self._db)
        self._db.execute('DELETE FROM auth_sessions WHERE digest=?', (digest,))

    def remove_account_sessions(self, uid):
        require_transaction(self._db)
        self._db.execute('DELETE FROM auth_sessions WHERE uid=?', (uid,))

    def ticket(self, digest):
        return self._db.execute('SELECT t.uid,t.region,t.expires,s.expires FROM auth_tickets t '
                                'JOIN auth_sessions s ON s.digest=t.session_digest WHERE t.digest=?', (digest,)).fetchone()

    def ticket_session(self, digest):
        return self._db.execute('SELECT session_digest FROM auth_tickets WHERE digest=?', (digest,)).fetchone()

    def insert_ticket(self, digest, session_digest, uid, region, expires):
        require_transaction(self._db)
        self._db.execute('INSERT INTO auth_tickets VALUES(?,?,?,?,?)', (digest, session_digest, uid, region, expires))

    def remove_ticket(self, digest):
        require_transaction(self._db)
        self._db.execute('DELETE FROM auth_tickets WHERE digest=?', (digest,))

    def remove_session_tickets(self, digest):
        require_transaction(self._db)
        self._db.execute('DELETE FROM auth_tickets WHERE session_digest=?', (digest,))
