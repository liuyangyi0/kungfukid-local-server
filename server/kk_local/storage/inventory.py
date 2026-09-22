"""Owned item persistence and global instance allocation, without commits."""
from .transactions import require_transaction


class InventoryRepository:
    def __init__(self,db):self._db=db

    def get(self,uid,instance):
        row=self.record_row(uid,instance)
        return row[0] if row else None

    def record_row(self, uid, instance):
        return self._db.execute('SELECT record FROM inventory WHERE uid=? AND instance=?', (uid, instance)).fetchone()

    def rows(self,uid,*,ordered=False,instance=None):
        if instance is not None:
            return self._db.execute('SELECT instance,record FROM inventory WHERE uid=? AND instance=?',(uid,instance)).fetchall()
        sql='SELECT instance,record FROM inventory WHERE uid=?'+(' ORDER BY instance' if ordered else '')
        return self._db.execute(sql,(uid,)).fetchall()

    def count(self,uid):
        return self._db.execute('SELECT COUNT(*) FROM inventory WHERE uid=?',(uid,)).fetchone()[0]

    def all_records(self):
        return self._db.execute('SELECT record FROM inventory').fetchall()

    def all_items(self):
        return self._db.execute('SELECT uid,instance,record FROM inventory ORDER BY instance,uid').fetchall()

    def permanent(self, uid, instance):
        return self._db.execute('SELECT 1 FROM permanent_weapons WHERE uid=? AND instance=?', (uid, instance)).fetchone() is not None

    def delete(self, uid, instance):
        require_transaction(self._db)
        self._db.execute('DELETE FROM inventory WHERE uid=? AND instance=?', (uid, instance))

    def insert(self,uid,instance,record):
        require_transaction(self._db)
        self._db.execute('INSERT INTO inventory VALUES(?,?,?)',(uid,instance,record))

    def update(self,uid,instance,record):
        require_transaction(self._db)
        self._db.execute('UPDATE inventory SET record=? WHERE uid=? AND instance=?',(record,uid,instance))

    def upsert(self,uid,instance,record):
        require_transaction(self._db)
        self._db.execute('INSERT INTO inventory VALUES(?,?,?) ON CONFLICT(uid,instance) DO UPDATE SET record=excluded.record',
                         (uid,instance,record))

    def grant_applied(self,uid,grant_id):
        return self._db.execute('SELECT 1 FROM applied_grants WHERE uid=? AND grant_id=?',(uid,grant_id)).fetchone() is not None

    def record_grant(self,uid,grant_id):
        require_transaction(self._db);self._db.execute('INSERT INTO applied_grants VALUES(?,?)',(uid,grant_id))

    def permanent_instances(self,uid):
        return {r[0] for r in self._db.execute('SELECT instance FROM permanent_weapons WHERE uid=?',(uid,))}

    def add_permanent(self,uid,instance,*,strict=False):
        require_transaction(self._db)
        sql='INSERT INTO permanent_weapons VALUES(?,?)' if strict else 'INSERT OR IGNORE INTO permanent_weapons VALUES(?,?)'
        self._db.execute(sql,(uid,instance))

    def allocate_instance(self):
        #One global native instance namespace;0xffffffff remains a sentinel.
        if not self._db.in_transaction:raise ValueError('inventory allocation requires transaction')
        maximum=self._db.execute('SELECT COALESCE(MAX(instance),0) FROM inventory').fetchone()[0]
        prior=self._db.execute("SELECT value FROM counters WHERE name='inventory_instance'").fetchone()
        value=max(0xfffff,maximum,prior[0] if prior else 0)+1
        if value>=0xffffffff:raise ValueError('inventory instance space exhausted')
        self._db.execute("INSERT INTO counters VALUES('inventory_instance',?) ON CONFLICT(name) DO UPDATE SET value=excluded.value",(value,))
        return value

    def consumption_receipt(self,uid,battle,sequence):
        return self._db.execute('SELECT instance,signature FROM consumption_events WHERE uid=? AND battle=? AND sequence=?',
                                (uid,battle,sequence)).fetchone()

    def record_consumption(self,uid,battle,sequence,instance,signature,remaining):
        require_transaction(self._db)
        self._db.execute('INSERT INTO consumption_events VALUES(?,?,?,?,?,?)',(uid,battle,sequence,instance,signature,remaining))
