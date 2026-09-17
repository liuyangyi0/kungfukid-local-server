"""Transactional local account snapshots; opaque record bytes are preserved."""
from pathlib import Path
import sqlite3
import struct
import hashlib
import unicodedata
from contextlib import nullcontext
from .shop_catalog import ShopRecord, MAX_RECORDS

# 8A3D10 constructs the UI slot table; 8A9700 maps suit kind18 to4.
EQUIPMENT_SLOTS = {12:(4,),13:(3,),14:(7,),15:(2,),16:(6,),17:(5,),
                   18:(4,),20:(10,),21:(11,),25:(8,9),64:(27,28)}


class Store:
    def __init__(self, path: str):
        if path != ':memory:':
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS accounts(
            uid INTEGER PRIMARY KEY, account TEXT NOT NULL UNIQUE,
            nickname TEXT NOT NULL, profile BLOB NOT NULL CHECK(length(profile)=360));
          CREATE TABLE IF NOT EXISTS inventory(
            uid INTEGER NOT NULL REFERENCES accounts(uid), instance INTEGER NOT NULL,
            record BLOB NOT NULL CHECK(length(record)=68), PRIMARY KEY(uid,instance));
          CREATE TABLE IF NOT EXISTS counters(name TEXT PRIMARY KEY, value INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS shop_catalog(
            category INTEGER NOT NULL CHECK(category BETWEEN 0 AND 255),
            variant INTEGER NOT NULL CHECK(variant BETWEEN 0 AND 255),
            ordinal INTEGER NOT NULL, item_id INTEGER NOT NULL,
            catalog_key INTEGER NOT NULL,
            record BLOB NOT NULL CHECK(length(record)=108),
            PRIMARY KEY(category,variant,item_id),
            UNIQUE(category,variant,catalog_key));
          CREATE TABLE IF NOT EXISTS gold_wallet(
            uid INTEGER PRIMARY KEY REFERENCES accounts(uid),
            balance INTEGER NOT NULL CHECK(balance BETWEEN 0 AND 2147483647));
          CREATE TABLE IF NOT EXISTS gold_offers(
            catalog_key INTEGER PRIMARY KEY,
            catalog_record BLOB NOT NULL CHECK(length(catalog_record)=108),
            grant_template BLOB NOT NULL CHECK(length(grant_template)=68));
          CREATE TABLE IF NOT EXISTS shop_receipts(
            uid INTEGER NOT NULL REFERENCES accounts(uid), operation_id TEXT NOT NULL,
            request_digest BLOB NOT NULL, balance INTEGER NOT NULL,
            item_record BLOB NOT NULL CHECK(length(item_record)=68),
            catalog_record BLOB NOT NULL CHECK(length(catalog_record)=108),
            PRIMARY KEY(uid,operation_id));
          INSERT OR IGNORE INTO counters VALUES('battle',0);
          CREATE TABLE IF NOT EXISTS offline_training(
            uid INTEGER PRIMARY KEY REFERENCES accounts(uid), started_at INTEGER);
          CREATE TABLE IF NOT EXISTS applied_grants(
            uid INTEGER NOT NULL REFERENCES accounts(uid), grant_id TEXT NOT NULL,
            PRIMARY KEY(uid,grant_id));
          CREATE TABLE IF NOT EXISTS consumption_events(
            uid INTEGER NOT NULL REFERENCES accounts(uid), battle INTEGER NOT NULL,
            sequence INTEGER NOT NULL, instance INTEGER NOT NULL,
            signature BLOB NOT NULL, remaining INTEGER NOT NULL,
            PRIMARY KEY(uid,battle,sequence));
          CREATE TABLE IF NOT EXISTS training_claims(
            uid INTEGER NOT NULL REFERENCES accounts(uid), started_at INTEGER NOT NULL,
            claimed_at INTEGER NOT NULL, points INTEGER NOT NULL,
            PRIMARY KEY(uid,started_at));
        ''')

    def replace_shop_catalog(self, category, variant, records):
        """Explicit local-admin import of complete records; never a client request.

        Preserves unknown bytes. Empty input explicitly clears only this category.
        No inventory grants, pricing inference or account balance changes.
        """
        if any(type(v) is not int or not 0<=v<=255 for v in (category,variant)):
            raise ValueError('invalid catalog selector')
        rows=[];items=set();keys=set()
        for raw in records:
            if len(rows)>=MAX_RECORDS:raise ValueError('catalog too large')
            record=ShopRecord(raw)
            item=record.item_id;key=record.u32(9)
            if not item or not key or item in items or key in keys:
                raise ValueError('invalid or duplicate catalog identity')
            items.add(item);keys.add(key)
            rows.append((category,variant,len(rows),item,key,record.raw))
        if self.db.in_transaction:raise ValueError('nested catalog transaction')
        with self.db:
            self.db.execute('DELETE FROM shop_catalog WHERE category=? AND variant=?',(category,variant))
            self.db.executemany('INSERT INTO shop_catalog VALUES(?,?,?,?,?,?)',rows)
        return len(rows)

    def shop_records(self, category, variant):
        if any(type(v) is not int or not 0<=v<=255 for v in (category,variant)):
            raise ValueError('invalid catalog selector')
        return tuple(ShopRecord(row[0]) for row in self.db.execute(
            'SELECT record FROM shop_catalog WHERE category=? AND variant=? ORDER BY ordinal',
            (category,variant)))

    def shop_cache_records(self):
        """Local 1540 policy: reuse imported catalog, never fabricate a record."""
        records={}
        for raw, in self.db.execute('SELECT DISTINCT record FROM shop_catalog ORDER BY catalog_key,record'):
            record=ShopRecord(bytes(raw));key=record.u32(9)
            if key in records and records[key].raw!=record.raw:
                raise ValueError('ambiguous catalog key')
            if key not in records and len(records)>=MAX_RECORDS:
                raise ValueError('catalog cache exceeds frame bound')
            records[key]=record
        return tuple(records.values())

    def shop_item_records(self, kind, item_id):
        """Local ordinary quick-query policy; distinct complete catalog records only."""
        rows=self.db.execute('SELECT DISTINCT record FROM shop_catalog WHERE item_id=? ORDER BY catalog_key,record',(item_id,))
        result=[]
        for row in rows:
            record=ShopRecord(row[0])
            if record.raw[4]==kind:
                if len(result)>=MAX_RECORDS:raise ValueError('item catalog too large')
                result.append(record)
        return tuple(result)

    def gold_balance(self, uid):
        row=self.db.execute('SELECT balance FROM gold_wallet WHERE uid=?',(uid,)).fetchone()
        return row[0] if row else 0

    def set_gold_balance(self, uid, balance):
        """Explicit local administrator funding, not an old-server economy rule."""
        if type(balance) is not int or not 0<=balance<=2147483647:
            raise ValueError('invalid gold balance')
        if self.db.in_transaction:raise ValueError('nested wallet transaction')
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO gold_wallet VALUES(?,?)',(uid,balance))

    def _gold_offer_record(self, key):
        rows=self.db.execute('SELECT DISTINCT record FROM shop_catalog WHERE catalog_key=?',(key,)).fetchall()
        if len(rows)!=1:raise ValueError('missing or ambiguous offer')
        r=ShopRecord(rows[0][0])
        if (not r.raw[48] or r.raw[46] or r.raw[49] or r.raw[83] or r.u32(88) or r.u32(77)
                or not 0<r.u32(30)<=2147483647 or r.u32(34)!=r.u32(30) or r.u32(38) or r.u32(42)):
            raise ValueError('offer requires unsupported settlement rules')
        return r

    def enable_gold_offer(self, key, grant_template):
        """Opt-in basic gold offer with an explicit complete unequipped item template."""
        if type(key) is not int or not 0<key<=0xffffffff:raise ValueError('invalid offer key')
        r=self._gold_offer_record(key)
        if (not isinstance(grant_template,bytes) or len(grant_template)!=68
                or struct.unpack_from('<I',grant_template,5)[0]!=r.item_id
                or grant_template[4] not in EQUIPMENT_SLOTS
                or grant_template[4]!=r.raw[4]
                or struct.unpack_from('<H',grant_template,17)[0]!=0
                or (grant_template[4]==64 and struct.unpack_from('<H',grant_template,23)[0]==0)):
            raise ValueError('invalid complete grant template')
        if self.db.in_transaction:raise ValueError('nested offer transaction')
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO gold_offers VALUES(?,?,?)',(key,r.raw,grant_template))

    def purchase_gold_once(self, uid, operation_id, request):
        """PROVISIONAL local transaction. Retry identity is server-generated, not wire data.

        One accepted frame is one intent. Same internal operation ID replays the
        receipt; a second client frame is a new intent, even with identical bytes.
        """
        if (not isinstance(operation_id,str) or not 1<=len(operation_id)<=128
                or not isinstance(request,bytes) or len(request)!=169):
            raise ValueError('invalid purchase operation')
        if self.db.in_transaction:raise ValueError('nested purchase transaction')
        digest=hashlib.sha256(request).digest()
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old=self.db.execute('SELECT request_digest,balance,item_record,catalog_record FROM shop_receipts WHERE uid=? AND operation_id=?',
                                (uid,operation_id)).fetchone()
            if old:
                if old[0]!=digest:raise ValueError('operation identity conflict')
                instance=struct.unpack_from('<I',old[2])[0]
                current=self.db.execute('SELECT record FROM inventory WHERE uid=? AND instance=?',(uid,instance)).fetchone()
                balance=self.gold_balance(uid)
                self.db.commit();return balance,current[0] if current else None,old[3]
            key=struct.unpack_from('<I',request,145)[0]
            offer=self.db.execute('SELECT catalog_record,grant_template FROM gold_offers WHERE catalog_key=?',(key,)).fetchone()
            if not offer:raise ValueError('offer not enabled')
            r=self._gold_offer_record(key)
            if r.raw!=offer[0]:raise ValueError('offer changed; administrator requalification required')
            if (struct.unpack_from('<I',request)[0]!=111
                    or struct.unpack_from('<Q',request,4)[0]!=uid
                    or struct.unpack_from('<Q',request,54)[0]!=uid
                    or struct.unpack_from('<I',request,149)[0]!=r.u32(30)
                    or any(struct.unpack_from('<4I',request,153))):
                raise ValueError('unsupported or mismatched purchase terms')
            balance=self.gold_balance(uid)
            if balance<r.u32(30):raise ValueError('insufficient gold')
            instance=self.db.execute('SELECT COALESCE(MAX(instance),0)+1 FROM inventory WHERE uid=?',(uid,)).fetchone()[0]
            if not 0<instance<=0xffffffff:raise ValueError('inventory instance space exhausted')
            record=bytearray(offer[1]);struct.pack_into('<I',record,0,instance);record=bytes(record)
            balance-=r.u32(30)
            self.db.execute('INSERT OR REPLACE INTO gold_wallet VALUES(?,?)',(uid,balance))
            self.db.execute('INSERT INTO inventory VALUES(?,?,?)',(uid,instance,record))
            self.db.execute('INSERT INTO shop_receipts VALUES(?,?,?,?,?,?)',
                            (uid,operation_id,digest,balance,record,r.raw))
            self.db.commit();return balance,record,r.raw
        except BaseException:
            self.db.rollback();raise

    def seed_local(self):
        return self.provision_local(1001, 'KKLocal', 'KKLocal')

    def provision_local(self, uid, account, nickname=None):
        """Explicit offline account provisioning, not old SDK/password authentication."""
        nickname = account if nickname is None else nickname
        if type(uid) is not int or not 0<uid<=0x7fffffffffffffff:
            raise ValueError('invalid account UID')
        if not isinstance(account,str) or not account.isascii() or not account.isalnum() or len(account)>20:
            raise ValueError('account must be1..20 ASCII alphanumeric characters')
        encoded=nickname.encode('gbk')
        if not encoded or len(encoded)>20 or b'\0' in encoded:
            raise ValueError('nickname must be1..20 GBK bytes')
        # Explicit local default, copied from VM-qualified fixture; not old economy.
        prior=self.db.execute('SELECT account FROM accounts WHERE uid=?',(uid,)).fetchone()
        if prior:
            if prior[0]!=account:
                raise ValueError('existing UID belongs to another account')
            return
        profile = bytearray(360)
        struct.pack_into('<I', profile, 0, 1)
        profile[4:25] = encoded.ljust(21,b'\0')
        profile[122] = profile[124] = 1
        items = [(121005, 12, 4), (131011, 13, 3), (141005, 14, 7),
                 (151005, 15, 2), (161005, 16, 6), (171005, 17, 5), (253030, 25, 8)]
        # Authentication registration may own a larger transaction covering
        # both this profile/inventory and its password record.
        transaction = nullcontext() if self.db.in_transaction else self.db
        with transaction:
            self.db.execute('INSERT INTO accounts VALUES(?,?,?,?)',
                            (uid, account, nickname, bytes(profile)))
            for i, (prop, kind, slot) in enumerate(items):
                instance = 0x100000 + i
                row = bytearray(68)
                struct.pack_into('<IBI', row, 0, instance, kind, prop)
                struct.pack_into('<H', row, 17, slot)
                self.db.execute('INSERT INTO inventory VALUES(?,?,?)',
                                (uid, instance, bytes(row)))

    def profile_word(self, uid, offset):
        # Internal service accessor, not a client-selected arbitrary offset API.
        if offset!=352:raise ValueError('unsupported profile word')
        row=self.db.execute('SELECT substr(profile,353,4) FROM accounts WHERE uid=?',(uid,)).fetchone()
        if row is None or len(row[0])!=4:raise ValueError('profile word unavailable')
        return bytes(row[0])

    def local_rankings(self, category, uid):
        from .rankings import local_rankings
        row=self.db.execute('SELECT profile FROM accounts WHERE uid=?',(uid,)).fetchone()
        if row is None:raise ValueError('ranking actor missing')
        return local_rankings(self.db.execute('SELECT uid,nickname,profile FROM accounts'),category,uid,row[0])

    def nickname(self, uid):
        row=self.db.execute('SELECT nickname FROM accounts WHERE uid=?',(uid,)).fetchone()
        if row is None:raise ValueError('unknown local account')
        return row[0]

    def snapshot(self, uid: int):
        account = self.db.execute('SELECT account,nickname,profile FROM accounts WHERE uid=?',
                                  (uid,)).fetchone()
        if not account:
            raise ValueError('unknown local account')
        records = [r[0] for r in self.db.execute(
            'SELECT record FROM inventory WHERE uid=? ORDER BY instance', (uid,))]
        return account[0], account[1], account[2], b''.join(records)

    def set_nickname(self, uid: int, nickname: str):
        encoded = nickname.encode('gbk')
        if not encoded or len(encoded) > 20 or b'\0' in encoded:
            raise ValueError('nickname must be 1..20 GBK bytes')
        _, _, old, _ = self.snapshot(uid)
        profile = bytearray(old)
        profile[4:25] = encoded.ljust(21, b'\0')
        with self.db:
            self.db.execute('UPDATE accounts SET nickname=?,profile=? WHERE uid=?',
                            (nickname, bytes(profile), uid))

    def rename_local(self, uid, nickname):
        """PROVISIONAL free local rename; exact-name uniqueness, lobby routing only."""
        encoded=nickname.encode('gbk')
        if (not encoded or len(encoded)>20 or nickname!=nickname.strip()
                or any(unicodedata.category(c).startswith('C') for c in nickname)):
            raise ValueError('invalid local nickname')
        if self.db.in_transaction:raise ValueError('nested rename transaction')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            if self.db.execute('SELECT 1 FROM accounts WHERE nickname=? AND uid<>?',(nickname,uid)).fetchone():
                raise ValueError('nickname already used')
            row=self.db.execute('SELECT nickname,profile FROM accounts WHERE uid=?',(uid,)).fetchone()
            if row is None:raise ValueError('unknown account')
            profile=bytearray(row[1]);profile[4:25]=encoded.ljust(21,b'\0')
            self.db.execute('UPDATE accounts SET nickname=?,profile=? WHERE uid=?',(nickname,bytes(profile),uid))
            self.db.commit();return row[0]
        except BaseException:
            self.db.rollback();raise

    def next_battle(self):
        with self.db:
            value = self.db.execute("SELECT value FROM counters WHERE name='battle'").fetchone()[0] + 1
            if value > 0xffffffff:
                raise ValueError('battle identifier exhausted')
            self.db.execute("UPDATE counters SET value=? WHERE name='battle'", (value,))
        return value

    def training(self, uid: int, now: float, *, start=False):
        """Persist real elapsed wall time; no invented reward/level progression."""
        if not self.db.execute('SELECT 1 FROM accounts WHERE uid=?', (uid,)).fetchone():
            raise ValueError('unknown local account')
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO offline_training VALUES(?,NULL)', (uid,))
            if start:
                self.db.execute('UPDATE offline_training SET started_at=? WHERE uid=? AND started_at IS NULL',
                                (int(now), uid))
        started = self.db.execute('SELECT started_at FROM offline_training WHERE uid=?', (uid,)).fetchone()[0]
        minutes = 0 if started is None else min(0x7fffffff // 60, max(0, int(now) - started) // 60)
        return minutes, started is not None

    def close(self):
        self.db.close()

    def claim_training(self, uid, now, *, points_per_hour=100, points_cap=2400):
        """PROVISIONAL local reward policy; atomic native score fields, not old rates.

        4300 ->9CD5B0 replaces profile+245/+249. Only the first score is
        awarded here; the second field and every other profile byte survive.
        Whole hours are credited once per start; a claim ends that interval.
        """
        if (type(points_per_hour) is not int or type(points_cap) is not int or
                not 1 <= points_per_hour <= points_cap <= 1000000):
            raise ValueError('invalid local training reward policy')
        if self.db.in_transaction:
            raise ValueError('nested reward transaction')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            _, _, raw, _ = self.snapshot(uid)
            training = self.db.execute('SELECT started_at FROM offline_training WHERE uid=?', (uid,)).fetchone()
            started = training[0] if training else None
            hours = 0 if started is None else max(0, int(now)-started)//3600
            score, second = struct.unpack_from('<II', raw, 245)
            if not hours:
                self.db.commit()
                return None
            if self.db.execute('SELECT 1 FROM training_claims WHERE uid=? AND started_at=?', (uid,started)).fetchone():
                self.db.commit()
                return None
            awarded = min(hours*points_per_hour, points_cap, 0x7fffffff-score)
            if awarded <= 0:
                self.db.commit()
                return None
            profile = bytearray(raw)
            struct.pack_into('<I', profile, 245, score+awarded)
            self.db.execute('UPDATE accounts SET profile=? WHERE uid=?', (bytes(profile),uid))
            self.db.execute('INSERT INTO training_claims VALUES(?,?,?,?)', (uid,started,int(now),awarded))
            self.db.execute('UPDATE offline_training SET started_at=NULL WHERE uid=?', (uid,))
            self.db.commit()
            return dict(points=score+awarded, second=second, awarded=awarded)
        except BaseException:
            self.db.rollback()
            raise

    def apply_grant(self, plan):
        if plan.get('schema') != 'kk-local-inventory-grant-v1' or plan.get('uid') != 1001:
            raise ValueError('invalid grant schema/identity')
        grant_id = plan.get('grant_id')
        if not isinstance(grant_id, str) or not 1 <= len(grant_id) <= 128:
            raise ValueError('invalid grant identity')
        rows = plan.get('rows')
        if not isinstance(rows, list) or not 1 <= len(rows) <= 4000:
            raise ValueError('invalid grant size')
        seen = set()
        for r in rows:
            if (not isinstance(r,list) or len(r)!=3 or any(type(x) is not int for x in r) or
                not 0 < r[0] <= 0xffffffff or r[1] not in (12,13,14,15,16,17,18,20,21,25,64,71,74) or
                not 1 <= r[2] <= 999 or r[0] in seen):
                raise ValueError('invalid or duplicate grant record')
            seen.add(r[0])
        if self.db.execute('SELECT 1 FROM applied_grants WHERE uid=1001 AND grant_id=?', (grant_id,)).fetchone():
            return 0
        records = self.db.execute('SELECT instance,record FROM inventory WHERE uid=1001').fetchall()
        existing = {(r[4],struct.unpack_from('<I',r,5)[0]):(i,r) for i,r in records}
        next_id = max((i for i,r in records), default=0x100000)+1
        if len(records)+len(rows)>8000 or next_id+len(rows)>0xffffffff:
            raise ValueError('inventory bound exceeded')
        added = 0
        with self.db:
            for prop, kind, quantity in rows:
                prior = existing.get((kind,prop))
                if prior:
                    instance, original = prior
                    record = bytearray(original)  # Preserve worn slot and unknown bytes.
                else:
                    instance = next_id; next_id += 1; added += 1
                    record = bytearray(68)
                    struct.pack_into('<IBI',record,0,instance,kind,prop)
                # UI8A3760 reads+23; do not apply to talismans where it is a scale.
                struct.pack_into('<H',record,23,max(quantity,struct.unpack_from('<H',record,23)[0]))
                self.db.execute('INSERT OR REPLACE INTO inventory VALUES(1001,?,?)',(instance,bytes(record)))
            self.db.execute('INSERT INTO applied_grants VALUES(1001,?)',(grant_id,))
        return added

    def equip(self, uid, instance, slot):
        found = self.db.execute('SELECT record FROM inventory WHERE uid=? AND instance=?',(uid,instance)).fetchone()
        if not found:
            raise ValueError('item not owned')
        record = bytearray(found[0])
        if slot not in EQUIPMENT_SLOTS.get(record[4],()):
            raise ValueError('unqualified item slot')
        with self.db:
            for other, raw in self.db.execute('SELECT instance,record FROM inventory WHERE uid=?',(uid,)).fetchall():
                if other != instance and struct.unpack_from('<H',raw,17)[0] == slot:
                    changed = bytearray(raw)
                    struct.pack_into('<H',changed,17,0)
                    self.db.execute('UPDATE inventory SET record=? WHERE uid=? AND instance=?',(bytes(changed),uid,other))
            struct.pack_into('<H',record,17,slot)
            self.db.execute('UPDATE inventory SET record=? WHERE uid=? AND instance=?',(bytes(record),uid,instance))
        return bytes(record)

    def unequip(self, uid, instance):
        """Keep the owned record; native2300 identifies it by instance, not slot."""
        found = self.db.execute('SELECT record FROM inventory WHERE uid=? AND instance=?',
                                (uid, instance)).fetchone()
        if not found:
            raise ValueError('item not owned')
        record = bytearray(found[0])
        slot = struct.unpack_from('<H', record, 17)[0]
        if slot == 0:
            return None  # A duplicate must not run native teardown a second time.
        if slot not in EQUIPMENT_SLOTS.get(record[4], ()):
            raise ValueError('unqualified item slot')
        struct.pack_into('<H', record, 17, 0)
        with self.db:
            self.db.execute('UPDATE inventory SET record=? WHERE uid=? AND instance=?',
                            (bytes(record), uid, instance))
        return bytes(record)

    def consumable(self, uid, *, instance=None, slot=None):
        if instance is not None:
            rows=self.db.execute('SELECT instance,record FROM inventory WHERE uid=? AND instance=?',
                                 (uid,instance)).fetchall()
        else:
            rows=[(i,r) for i,r in self.db.execute('SELECT instance,record FROM inventory WHERE uid=?',(uid,))
                  if struct.unpack_from('<H',r,17)[0]==slot]
        if len(rows)!=1:
            raise ValueError('consumable missing or ambiguous')
        i,r=rows[0]
        if r[4]!=64 or struct.unpack_from('<H',r,17)[0] not in (27,28):
            raise ValueError('not an equipped owned consumable')
        return i,r

    def consume_once(self, uid, battle, sequence, instance, signature, intent):
        """Atomically bill a correlated native event; never wrap quantity below0."""
        if self.db.in_transaction:
            raise ValueError('nested consumption transaction')
        self.db.execute('BEGIN IMMEDIATE')
        try:
            old=self.db.execute('SELECT instance,signature FROM consumption_events WHERE uid=? AND battle=? AND sequence=?',
                                (uid,battle,sequence)).fetchone()
            if old:
                if old!=(instance,signature):
                    raise ValueError('consumption event identity conflict')
                self.db.commit()
                return False
            if not intent:
                raise ValueError('missing4200 intent')
            _,raw=self.consumable(uid,instance=instance)
            count=struct.unpack_from('<H',raw,23)[0]
            if count==0:
                raise ValueError('consumable exhausted')
            record=bytearray(raw)
            struct.pack_into('<H',record,23,count-1)
            self.db.execute('UPDATE inventory SET record=? WHERE uid=? AND instance=?',(bytes(record),uid,instance))
            self.db.execute('INSERT INTO consumption_events VALUES(?,?,?,?,?,?)',
                            (uid,battle,sequence,instance,signature,count-1))
            self.db.commit()
            return True
        except BaseException:
            self.db.rollback()
            raise

    def consumable_slots(self, uid):
        result={}
        for slot in (27,28):
            try:
                result[slot]=self.consumable(uid,slot=slot)[1]
            except ValueError:
                result[slot]=bytes(68)
        return result
