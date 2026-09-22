"""Talisman use/repair records, without billing policy or implicit commits."""
from .transactions import require_transaction


class TalismanRepository:
    def __init__(self, db): self._db = db

    def replace_repair_rules(self, rows):
        require_transaction(self._db)
        self._db.execute("INSERT INTO counters VALUES('repair_revision',1) ON CONFLICT(name) DO UPDATE SET value=value+1")
        revision = self._db.execute("SELECT value FROM counters WHERE name='repair_revision'").fetchone()[0]
        self._db.execute('DELETE FROM talisman_repair_rules')
        self._db.executemany('INSERT INTO talisman_repair_rules VALUES(?,?,?,?,?)', [(*row, revision) for row in rows])

    def repair_rule(self, item):
        return self._db.execute('SELECT material,quantity,capacity,revision FROM talisman_repair_rules WHERE item=?', (item,)).fetchone()

    def repair_receipt(self, uid, operation):
        return self._db.execute('SELECT signature FROM talisman_repairs WHERE uid=? AND operation=?', (uid, operation)).fetchone()

    def record_repair(self, uid, operation, signature):
        require_transaction(self._db)
        self._db.execute('INSERT INTO talisman_repairs VALUES(?,?,?)', (uid, operation, signature))

    def use_receipt(self, uid, battle, instance, kind, sequence):
        return self._db.execute('SELECT cost FROM talisman_uses WHERE uid=? AND battle=? AND instance=? AND kind=? AND sequence=?', (uid, battle, instance, kind, sequence)).fetchone()

    def record_use(self, uid, battle, instance, kind, sequence, cost):
        require_transaction(self._db)
        self._db.execute('INSERT INTO talisman_uses VALUES(?,?,?,?,?,?)', (uid, battle, instance, kind, sequence, cost))
