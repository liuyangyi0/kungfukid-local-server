"""Existing SQLite schema and bounded compatibility migration.

Extracted without changing DDL or row semantics. New schema revisions should
be introduced here, never hidden inside feature handlers.
"""


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS accounts(
        uid INTEGER PRIMARY KEY, account TEXT NOT NULL UNIQUE,
        nickname TEXT NOT NULL, profile BLOB NOT NULL CHECK(length(profile)=360));
      CREATE TABLE IF NOT EXISTS inventory(
        uid INTEGER NOT NULL REFERENCES accounts(uid), instance INTEGER NOT NULL,
        record BLOB NOT NULL CHECK(length(record)=68), PRIMARY KEY(uid,instance));
      CREATE TABLE IF NOT EXISTS permanent_weapons(
        uid INTEGER NOT NULL, instance INTEGER NOT NULL,
        PRIMARY KEY(uid,instance),
        FOREIGN KEY(uid,instance) REFERENCES inventory(uid,instance) ON DELETE CASCADE);
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
      CREATE TABLE IF NOT EXISTS shop_gift_receipts(
        uid INTEGER NOT NULL REFERENCES accounts(uid),operation TEXT NOT NULL,signature BLOB NOT NULL,
        recipient INTEGER NOT NULL REFERENCES accounts(uid),mail_id INTEGER NOT NULL,
        PRIMARY KEY(uid,operation));
      CREATE TABLE IF NOT EXISTS shop_offer_policy(catalog_key INTEGER PRIMARY KEY,permanent INTEGER NOT NULL CHECK(permanent IN (0,1)));
      CREATE TABLE IF NOT EXISTS shop_settings(name TEXT PRIMARY KEY,value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS weapon_upgrade_settings(
        id INTEGER PRIMARY KEY CHECK(id=1),revision INTEGER NOT NULL,rules TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS weapon_upgrade_receipts(
        uid INTEGER NOT NULL REFERENCES accounts(uid),operation TEXT NOT NULL,instance INTEGER NOT NULL,
        revision INTEGER NOT NULL,success INTEGER NOT NULL,roll INTEGER NOT NULL,
        score_cost INTEGER NOT NULL,gold_cost INTEGER NOT NULL,
        before_record BLOB NOT NULL CHECK(length(before_record)=68),
        after_record BLOB NOT NULL CHECK(length(after_record)=68),PRIMARY KEY(uid,operation));
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
      CREATE TABLE IF NOT EXISTS talisman_uses(
        uid INTEGER NOT NULL REFERENCES accounts(uid), battle INTEGER NOT NULL,
        instance INTEGER NOT NULL, kind INTEGER NOT NULL, sequence INTEGER NOT NULL,
        cost INTEGER NOT NULL CHECK(cost BETWEEN 0 AND 65535),
        PRIMARY KEY(uid,battle,instance,kind,sequence));
      CREATE TABLE IF NOT EXISTS talisman_repair_rules(
        item INTEGER PRIMARY KEY,material INTEGER NOT NULL,quantity INTEGER NOT NULL,
        capacity INTEGER NOT NULL,revision INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS talisman_repairs(
        uid INTEGER NOT NULL REFERENCES accounts(uid),operation TEXT NOT NULL,
        signature BLOB NOT NULL,PRIMARY KEY(uid,operation));
      CREATE TABLE IF NOT EXISTS mailbox(
        uid INTEGER NOT NULL REFERENCES accounts(uid),id INTEGER NOT NULL UNIQUE,
        delivery_id TEXT NOT NULL,signature BLOB NOT NULL,
        list_record BLOB NOT NULL CHECK(length(list_record)=339),
        catalog BLOB NOT NULL CHECK(length(catalog) IN (0,108)),
        grant_record BLOB NOT NULL CHECK(length(grant_record) IN (0,68)),
        attachment INTEGER NOT NULL,claimed_instance INTEGER,
        deleted INTEGER NOT NULL DEFAULT 0,is_read INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(uid,id),UNIQUE(uid,delivery_id));
      CREATE UNIQUE INDEX IF NOT EXISTS mailbox_attachment ON mailbox(attachment) WHERE attachment<>0;
      CREATE TABLE IF NOT EXISTS ticket_wallet(
        uid INTEGER PRIMARY KEY REFERENCES accounts(uid),balance INTEGER NOT NULL CHECK(balance BETWEEN 0 AND 2147483647));
      CREATE TABLE IF NOT EXISTS renewal_offers(
        catalog_key INTEGER PRIMARY KEY,days INTEGER NOT NULL,catalog BLOB NOT NULL CHECK(length(catalog)=108),revision INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS renewal_leases(
        uid INTEGER NOT NULL,instance INTEGER NOT NULL,expires INTEGER NOT NULL,
        PRIMARY KEY(uid,instance),FOREIGN KEY(uid,instance) REFERENCES inventory(uid,instance) ON DELETE CASCADE);
      CREATE TABLE IF NOT EXISTS renewal_reminders(
        uid INTEGER NOT NULL,instance INTEGER NOT NULL,expires INTEGER NOT NULL,
        PRIMARY KEY(uid,instance),FOREIGN KEY(uid,instance) REFERENCES inventory(uid,instance) ON DELETE CASCADE);
      CREATE TABLE IF NOT EXISTS renewal_receipts(
        uid INTEGER NOT NULL REFERENCES accounts(uid),operation TEXT NOT NULL,signature BLOB NOT NULL,
        instance INTEGER NOT NULL,expires INTEGER NOT NULL,PRIMARY KEY(uid,operation));
      CREATE TABLE IF NOT EXISTS title_reward_rules(level INTEGER PRIMARY KEY,choices TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS title_entitlements(
        uid INTEGER NOT NULL REFERENCES accounts(uid),level INTEGER NOT NULL,source TEXT NOT NULL,
        choices TEXT NOT NULL,claimed_key INTEGER,claimed_instance INTEGER,PRIMARY KEY(uid,level));
      CREATE TABLE IF NOT EXISTS tutorial_completions(
        uid INTEGER PRIMARY KEY REFERENCES accounts(uid),room INTEGER NOT NULL,battle INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS quest_availability(
        family TEXT NOT NULL,task_key INTEGER NOT NULL,definition TEXT NOT NULL,
        PRIMARY KEY(family,task_key));
      CREATE TABLE IF NOT EXISTS quest_progress(
        uid INTEGER NOT NULL REFERENCES accounts(uid),family TEXT NOT NULL,task_key INTEGER NOT NULL,
        definition TEXT NOT NULL,state INTEGER NOT NULL CHECK(state IN (1,2,3,4)),
        baseline BLOB NOT NULL CHECK(length(baseline)=116),execution TEXT,
        counts BLOB NOT NULL DEFAULT X'000000000000000000000000' CHECK(length(counts)=12),
        claimed_instance INTEGER,PRIMARY KEY(uid,family,task_key));
      CREATE TABLE IF NOT EXISTS quest_reward_rules(
        family TEXT NOT NULL,task_key INTEGER NOT NULL,definition TEXT NOT NULL,rule TEXT NOT NULL,
        PRIMARY KEY(family,task_key));
      CREATE TABLE IF NOT EXISTS training_claims(
        uid INTEGER NOT NULL REFERENCES accounts(uid), started_at INTEGER NOT NULL,
        claimed_at INTEGER NOT NULL, points INTEGER NOT NULL,
        PRIMARY KEY(uid,started_at));
      CREATE TABLE IF NOT EXISTS match_point_batches(
        battle INTEGER PRIMARY KEY, signature TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS match_point_grants(
        battle INTEGER NOT NULL REFERENCES match_point_batches(battle),
        uid INTEGER NOT NULL REFERENCES accounts(uid),
        outcome INTEGER NOT NULL CHECK(outcome BETWEEN 0 AND 2),
        awarded INTEGER NOT NULL CHECK(awarded BETWEEN 0 AND 1000000),
        PRIMARY KEY(battle,uid));
    ''')
    upgrade_quest_progress(db)


def initialize_auth(db):
    """Existing optional auth tables; never implicitly commit a caller's work."""
    if db.in_transaction:
        raise ValueError('authentication initialization requires no active transaction')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS auth_credentials(
        uid INTEGER PRIMARY KEY REFERENCES accounts(uid),
        normalized_account TEXT NOT NULL UNIQUE,
        algorithm TEXT NOT NULL, salt BLOB NOT NULL CHECK(length(salt)=16),
        digest BLOB NOT NULL CHECK(length(digest)=32));
      CREATE TABLE IF NOT EXISTS auth_sessions(
        digest BLOB PRIMARY KEY CHECK(length(digest)=32),
        uid INTEGER NOT NULL REFERENCES auth_credentials(uid),
        created INTEGER NOT NULL, expires INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS auth_tickets(
        digest BLOB PRIMARY KEY CHECK(length(digest)=32),
        session_digest BLOB NOT NULL REFERENCES auth_sessions(digest) ON DELETE CASCADE,
        uid INTEGER NOT NULL REFERENCES auth_credentials(uid),
        region INTEGER NOT NULL, expires INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS auth_failures(
        account TEXT PRIMARY KEY, failures INTEGER NOT NULL,
        blocked_until INTEGER NOT NULL, updated INTEGER NOT NULL);
    ''')


def upgrade_quest_progress(db):
    """Transactional v1 six-column migration; preserve every existing row."""
    columns=[r[1] for r in db.execute('PRAGMA table_info(quest_progress)')]
    if 'execution' in columns:return
    if columns!=['uid','family','task_key','definition','state','baseline']:
        raise ValueError('unrecognized quest schema; explicit migration required')
    db.execute('BEGIN IMMEDIATE')
    try:
        db.execute('ALTER TABLE quest_progress RENAME TO quest_progress_v1_migration')
        db.execute('''CREATE TABLE quest_progress(
            uid INTEGER NOT NULL REFERENCES accounts(uid),family TEXT NOT NULL,task_key INTEGER NOT NULL,
            definition TEXT NOT NULL,state INTEGER NOT NULL CHECK(state IN (1,2,3,4)),
            baseline BLOB NOT NULL CHECK(length(baseline)=116),execution TEXT,
            counts BLOB NOT NULL DEFAULT X'000000000000000000000000' CHECK(length(counts)=12),
            claimed_instance INTEGER,PRIMARY KEY(uid,family,task_key))''')
        db.execute('INSERT INTO quest_progress(uid,family,task_key,definition,state,baseline) '
                        'SELECT uid,family,task_key,definition,state,baseline FROM quest_progress_v1_migration')
        db.execute('DROP TABLE quest_progress_v1_migration')
        db.commit()
    except BaseException:db.rollback();raise
