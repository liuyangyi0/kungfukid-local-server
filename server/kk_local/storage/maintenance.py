"""Read-only reference checks for explicitly requested offline inventory repair."""


class MaintenanceRepository:
    def __init__(self, db): self._db = db

    def consumption_exists(self, uid, instance):
        return self._db.execute('SELECT 1 FROM consumption_events WHERE uid=? AND instance=?', (uid, instance)).fetchone() is not None

    def linked_history_exists(self, uid, instance):
        queries = (
            'SELECT 1 FROM renewal_leases WHERE uid=? AND instance=?',
            'SELECT 1 FROM renewal_receipts WHERE uid=? AND instance=?',
            'SELECT 1 FROM mailbox WHERE uid=? AND claimed_instance=?',
            'SELECT 1 FROM title_entitlements WHERE uid=? AND claimed_instance=?',
            'SELECT 1 FROM quest_progress WHERE uid=? AND claimed_instance=?',
            'SELECT 1 FROM weapon_upgrade_receipts WHERE uid=? AND instance=?',
        )
        return any(self._db.execute(query, (uid, instance)).fetchone() for query in queries)
