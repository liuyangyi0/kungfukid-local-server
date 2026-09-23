"""Title entitlements and tutorial completion receipts, without commits."""
from dataclasses import dataclass
from .transactions import require_transaction


@dataclass(frozen=True)
class TitleEntitlement:
    level: int
    choices: str
    claimed_key: int | None
    claimed_instance: int | None


class TitleRepository:
    def __init__(self, db):
        self._db = db

    def rule(self, level):
        return self._db.execute('SELECT choices FROM title_reward_rules WHERE level=?', (level,)).fetchone()

    def set_rule(self, level, choices):
        require_transaction(self._db)
        self._db.execute('INSERT INTO title_reward_rules VALUES(?,?) ON CONFLICT(level) DO UPDATE SET choices=excluded.choices',
                         (level, choices))

    def entitlement(self, uid, level):
        row = self._db.execute('SELECT level,choices,claimed_key,claimed_instance FROM title_entitlements WHERE uid=? AND level=?',
                               (uid, level)).fetchone()
        return TitleEntitlement(*row) if row is not None else None

    def pending(self, uid):
        row = self._db.execute('SELECT level,choices,claimed_key,claimed_instance FROM title_entitlements '
                               'WHERE uid=? AND claimed_key IS NULL ORDER BY level LIMIT 1', (uid,)).fetchone()
        return TitleEntitlement(*row) if row is not None else None

    def insert(self, uid, level, source, choices, claimed_key):
        require_transaction(self._db)
        self._db.execute('INSERT INTO title_entitlements(uid,level,source,choices,claimed_key) VALUES(?,?,?,?,?)',
                         (uid, level, source, choices, claimed_key))

    def mark_claimed(self, uid, level, key, instance):
        require_transaction(self._db)
        self._db.execute('UPDATE title_entitlements SET claimed_key=?,claimed_instance=? WHERE uid=? AND level=?',
                         (key, instance, uid, level))

    def tutorial_completed(self, uid):
        return self._db.execute('SELECT 1 FROM tutorial_completions WHERE uid=?', (uid,)).fetchone() is not None

    def record_tutorial(self, uid, room, serial):
        require_transaction(self._db)
        self._db.execute('INSERT INTO tutorial_completions VALUES(?,?,?)', (uid, room, serial))
