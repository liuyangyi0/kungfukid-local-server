"""Training and match receipts share the caller's profile transaction."""
from .transactions import require_transaction


class ProgressionRepository:
    def __init__(self, db):
        self._db = db

    def battle_counter(self):
        return self._db.execute("SELECT value FROM counters WHERE name='battle'").fetchone()[0]

    def set_battle_counter(self, value):
        require_transaction(self._db)
        self._db.execute("UPDATE counters SET value=? WHERE name='battle'", (value,))

    def training(self, uid):
        return self._db.execute('SELECT started_at FROM offline_training WHERE uid=?', (uid,)).fetchone()

    def ensure_training(self, uid):
        require_transaction(self._db)
        self._db.execute('INSERT OR IGNORE INTO offline_training VALUES(?,NULL)', (uid,))

    def start_training(self, uid, now):
        require_transaction(self._db)
        self._db.execute('UPDATE offline_training SET started_at=? WHERE uid=? AND started_at IS NULL', (now, uid))

    def clear_training(self, uid):
        require_transaction(self._db)
        self._db.execute('UPDATE offline_training SET started_at=NULL WHERE uid=?', (uid,))

    def training_claimed(self, uid, started):
        return self._db.execute(
            'SELECT 1 FROM training_claims WHERE uid=? AND started_at=?', (uid, started)).fetchone() is not None

    def record_training_claim(self, uid, started, now, awarded):
        require_transaction(self._db)
        self._db.execute('INSERT INTO training_claims VALUES(?,?,?,?)', (uid, started, now, awarded))

    def match_receipt(self, battle):
        return self._db.execute('SELECT signature FROM match_point_batches WHERE battle=?', (battle,)).fetchone()

    def match_outcome(self, battle, uid):
        return self._db.execute('SELECT outcome FROM match_point_grants WHERE battle=? AND uid=?', (battle, uid)).fetchone()

    def match_awards(self, battle):
        return dict(self._db.execute('SELECT uid,awarded FROM match_point_grants WHERE battle=?', (battle,)))

    def record_match(self, battle, signature):
        require_transaction(self._db)
        self._db.execute('INSERT INTO match_point_batches VALUES(?,?)', (battle, signature))

    def record_match_award(self, battle, uid, outcome, awarded):
        require_transaction(self._db)
        self._db.execute('INSERT INTO match_point_grants VALUES(?,?,?,?)', (battle, uid, outcome, awarded))
