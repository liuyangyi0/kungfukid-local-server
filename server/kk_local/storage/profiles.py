"""Account/profile persistence; record interpretation belongs to business code."""
from .transactions import require_transaction


class ProfileRepository:
    def __init__(self, db):
        self._db = db

    def account_name(self, uid):
        return self._db.execute('SELECT account FROM accounts WHERE uid=?', (uid,)).fetchone()

    def account_uids(self, name):
        return self._db.execute('SELECT uid FROM accounts WHERE account=? COLLATE NOCASE', (name,)).fetchall()

    def maximum_uid(self):
        return self._db.execute('SELECT COALESCE(MAX(uid),0) FROM accounts').fetchone()[0]

    def exact_account_uid(self, name):
        return self._db.execute('SELECT uid FROM accounts WHERE account=?', (name,)).fetchone()

    def account(self, uid):
        return self._db.execute(
            'SELECT account,nickname,profile FROM accounts WHERE uid=?', (uid,)).fetchone()

    def profile(self, uid):
        return self._db.execute('SELECT profile FROM accounts WHERE uid=?', (uid,)).fetchone()

    def nickname(self, uid):
        return self._db.execute('SELECT nickname FROM accounts WHERE uid=?', (uid,)).fetchone()

    def nickname_in_use(self, nickname, uid):
        return self._db.execute(
            'SELECT 1 FROM accounts WHERE nickname=? AND uid<>?', (nickname, uid)).fetchone() is not None

    def ranking_rows(self):
        return self._db.execute('SELECT uid,nickname,profile FROM accounts').fetchall()

    def status_word(self, uid):
        # Fixed legacy profile field, never a client-selected SQL offset.
        return self._db.execute('SELECT substr(profile,353,4) FROM accounts WHERE uid=?', (uid,)).fetchone()

    def insert(self, uid, account, nickname, profile):
        require_transaction(self._db)
        self._db.execute('INSERT INTO accounts VALUES(?,?,?,?)', (uid, account, nickname, profile))

    def update_profile(self, uid, profile):
        require_transaction(self._db)
        self._db.execute('UPDATE accounts SET profile=? WHERE uid=?', (profile, uid))

    def rename(self, uid, nickname, profile):
        require_transaction(self._db)
        self._db.execute('UPDATE accounts SET nickname=?,profile=? WHERE uid=?', (nickname, profile, uid))
