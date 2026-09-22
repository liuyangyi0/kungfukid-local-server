"""Finite lease configuration and receipts; caller owns transactions."""
from .transactions import require_transaction


class RenewalRepository:
    def __init__(self, db): self._db = db

    def configure_offer(self, key, days, raw):
        require_transaction(self._db)
        self._db.execute("INSERT INTO counters VALUES('renewal_revision',1) ON CONFLICT(name) DO UPDATE SET value=value+1")
        revision = self._db.execute("SELECT value FROM counters WHERE name='renewal_revision'").fetchone()[0]
        self._db.execute('INSERT INTO renewal_offers VALUES(?,?,?,?) ON CONFLICT(catalog_key) DO UPDATE SET days=excluded.days,catalog=excluded.catalog,revision=excluded.revision', (key, days, raw, revision))

    def register_lease(self, uid, instance, expires):
        require_transaction(self._db)
        self._db.execute('INSERT INTO renewal_leases VALUES(?,?,?) ON CONFLICT(uid,instance) DO UPDATE SET expires=excluded.expires', (uid, instance, expires))

    def lease(self, uid, instance):
        return self._db.execute('SELECT expires FROM renewal_leases WHERE uid=? AND instance=?', (uid, instance)).fetchone()

    def offers(self):
        return self._db.execute('SELECT catalog_key,days,catalog,revision FROM renewal_offers ORDER BY catalog_key').fetchall()

    def offer(self, key):
        return self._db.execute('SELECT catalog,days,revision FROM renewal_offers WHERE catalog_key=?', (key,)).fetchone()

    def receipt(self, uid, operation):
        return self._db.execute('SELECT signature FROM renewal_receipts WHERE uid=? AND operation=?', (uid, operation)).fetchone()

    def update_lease(self, uid, instance, deadline):
        require_transaction(self._db)
        self._db.execute('UPDATE renewal_leases SET expires=? WHERE uid=? AND instance=?', (deadline, uid, instance))

    def record_receipt(self, uid, operation, signature, instance, deadline):
        require_transaction(self._db)
        self._db.execute('INSERT INTO renewal_receipts VALUES(?,?,?,?,?)', (uid, operation, signature, instance, deadline))

    def reminders(self, uid, now):
        return self._db.execute('SELECT l.instance,l.expires,i.record,r.expires FROM renewal_leases l JOIN inventory i ON i.uid=l.uid AND i.instance=l.instance LEFT JOIN renewal_reminders r ON r.uid=l.uid AND r.instance=l.instance WHERE l.uid=? AND l.expires<=? ORDER BY l.instance', (uid, now)).fetchall()

    def hide_reminder(self, uid, instance, expires):
        require_transaction(self._db)
        self._db.execute('INSERT INTO renewal_reminders VALUES(?,?,?) ON CONFLICT(uid,instance) DO UPDATE SET expires=excluded.expires', (uid, instance, expires))
