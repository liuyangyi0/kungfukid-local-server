"""Catalogue, wallets and sale receipts on the same caller-owned connection."""
from .transactions import require_transaction


class CommerceRepository:
    def __init__(self,db):self._db=db

    def catalog(self,category,variant):
        return tuple(r[0] for r in self._db.execute('SELECT record FROM shop_catalog WHERE category=? AND variant=? ORDER BY ordinal',(category,variant)))

    def catalog_by_key(self,key):
        return tuple(r[0] for r in self._db.execute('SELECT DISTINCT record FROM shop_catalog WHERE catalog_key=?',(key,)))

    def catalog_cache(self):
        return tuple(r[0] for r in self._db.execute('SELECT DISTINCT record FROM shop_catalog ORDER BY catalog_key,record'))

    def catalog_by_item(self,item):
        return tuple(r[0] for r in self._db.execute('SELECT DISTINCT record FROM shop_catalog WHERE item_id=? ORDER BY catalog_key,record',(item,)))

    def catalog_entries(self):
        return self._db.execute('SELECT category,variant,item_id,catalog_key,record FROM shop_catalog').fetchall()

    def offer_entries(self):
        return self._db.execute('SELECT catalog_key,catalog_record FROM gold_offers').fetchall()

    def update_catalog_and_offer(self,category,variant,item,key,record):
        require_transaction(self._db)
        self._db.execute('UPDATE shop_catalog SET record=? WHERE category=? AND variant=? AND item_id=?',
                         (record,category,variant,item))
        self._db.execute('UPDATE gold_offers SET catalog_record=? WHERE catalog_key=?',(record,key))

    def test_gold_enabled(self):
        return self._db.execute("SELECT 1 FROM shop_settings WHERE name='test-gold' AND value='enabled'").fetchone() is not None

    def replace_catalog(self,category,variant,rows):
        require_transaction(self._db)
        self._db.execute('DELETE FROM shop_catalog WHERE category=? AND variant=?',(category,variant))
        self._db.executemany('INSERT INTO shop_catalog VALUES(?,?,?,?,?,?)',rows)

    def insert_catalog(self,row):
        require_transaction(self._db);self._db.execute('INSERT INTO shop_catalog VALUES(?,?,?,?,?,?)',row)

    def clear_storefront(self):
        require_transaction(self._db)
        for table in ('shop_catalog','gold_offers','shop_offer_policy'):self._db.execute('DELETE FROM '+table)

    def offer(self,key):
        return self._db.execute('SELECT catalog_record,grant_template FROM gold_offers WHERE catalog_key=?',(key,)).fetchone()

    def has_offers(self):
        return self._db.execute('SELECT 1 FROM gold_offers LIMIT 1').fetchone() is not None

    def put_offer(self,key,catalog,grant,*,replace=True):
        require_transaction(self._db)
        sql='INSERT OR REPLACE INTO gold_offers VALUES(?,?,?)' if replace else 'INSERT INTO gold_offers VALUES(?,?,?)'
        self._db.execute(sql,(key,catalog,grant))

    def permanent_policy(self,key):
        return self._db.execute('SELECT permanent FROM shop_offer_policy WHERE catalog_key=?',(key,)).fetchone()==(1,)

    def put_permanent_policy(self,key,value):
        require_transaction(self._db);self._db.execute('INSERT INTO shop_offer_policy VALUES(?,?)',(key,int(value)))

    def put_setting(self,name,value):
        require_transaction(self._db)
        self._db.execute('INSERT INTO shop_settings VALUES(?,?) ON CONFLICT(name) DO UPDATE SET value=excluded.value',(name,value))

    @staticmethod
    def _wallet(currency):
        if currency not in ('gold','ticket'):raise ValueError('unknown wallet currency')
        return 'gold_wallet' if currency=='gold' else 'ticket_wallet'

    def balance(self,uid,currency='gold'):
        row=self._db.execute('SELECT balance FROM '+self._wallet(currency)+' WHERE uid=?',(uid,)).fetchone()
        return row[0] if row else 0

    def set_balance(self,uid,currency,value,*,replace=False):
        require_transaction(self._db);table=self._wallet(currency)
        sql=('INSERT OR REPLACE INTO '+table+' VALUES(?,?)' if replace else
             'INSERT INTO '+table+' VALUES(?,?) ON CONFLICT(uid) DO UPDATE SET balance=excluded.balance')
        self._db.execute(sql,(uid,value))

    def purchase_items(self, uid):
        return [r[0] for r in self._db.execute('SELECT item_record FROM shop_receipts WHERE uid=?', (uid,))]

    def purchase_receipt(self,uid,operation):
        return self._db.execute('SELECT request_digest,item_record,catalog_record FROM shop_receipts WHERE uid=? AND operation_id=?',(uid,operation)).fetchone()

    def record_purchase(self,uid,operation,digest,balance,item,catalog):
        require_transaction(self._db)
        self._db.execute('INSERT INTO shop_receipts VALUES(?,?,?,?,?,?)',(uid,operation,digest,balance,item,catalog))

    def gift_receipt(self,uid,operation):
        return self._db.execute('SELECT signature,recipient,mail_id FROM shop_gift_receipts WHERE uid=? AND operation=?',(uid,operation)).fetchone()

    def record_gift(self,uid,operation,digest,recipient,mail):
        require_transaction(self._db)
        self._db.execute('INSERT INTO shop_gift_receipts VALUES(?,?,?,?,?)',(uid,operation,digest,recipient,mail))

    def recipient_ids(self,name):
        return self._db.execute('SELECT uid FROM accounts WHERE nickname=? COLLATE BINARY LIMIT 2',(name,)).fetchall()

    def account_exists(self,uid):
        return self._db.execute('SELECT 1 FROM accounts WHERE uid=?',(uid,)).fetchone() is not None
