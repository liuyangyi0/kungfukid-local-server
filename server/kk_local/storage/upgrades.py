"""Revisioned weapon-upgrade settings and immutable attempt receipts."""
from .transactions import require_transaction


class UpgradeRepository:
    def __init__(self, db): self._db = db

    def settings(self):
        return self._db.execute('SELECT revision,rules FROM weapon_upgrade_settings WHERE id=1').fetchone()

    def set_settings(self, revision, raw):
        require_transaction(self._db)
        self._db.execute('INSERT INTO weapon_upgrade_settings VALUES(1,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,rules=excluded.rules', (revision, raw))

    def receipt(self, uid, operation):
        return self._db.execute('SELECT instance,success FROM weapon_upgrade_receipts WHERE uid=? AND operation=?', (uid, operation)).fetchone()

    def record_attempt(self, uid, operation, instance, revision, success, roll, score, gold, before, after):
        require_transaction(self._db)
        self._db.execute('INSERT INTO weapon_upgrade_receipts VALUES(?,?,?,?,?,?,?,?,?,?)', (uid, operation, instance, revision, success, roll, score, gold, before, after))
