"""Explicit versioned public schema migration, the DDL/commit authority."""
import sqlite3

VERSION=1

def version(db):
    try:row=db.execute('SELECT version FROM public_schema WHERE id=1').fetchone()
    except sqlite3.OperationalError as exc:
        if 'no such table: public_schema' not in str(exc):raise
        return None
    return row[0] if row else None

def migrate(db):
    if version(db)==VERSION:return
    if version(db) is not None:raise ValueError('unsupported public schema')
    statements=[
        'CREATE TABLE public_schema(id INTEGER PRIMARY KEY CHECK(id=1),version INTEGER NOT NULL)',
        'CREATE TABLE public_invites(digest BLOB PRIMARY KEY,created INTEGER NOT NULL,expires INTEGER NOT NULL,revoked INTEGER NOT NULL DEFAULT 0,used_by INTEGER UNIQUE REFERENCES accounts(uid))',
        'CREATE TABLE public_disabled(uid INTEGER PRIMARY KEY REFERENCES accounts(uid),disabled INTEGER NOT NULL DEFAULT 1)',
        'CREATE TABLE public_ledger(id INTEGER PRIMARY KEY AUTOINCREMENT,uid INTEGER NOT NULL,kind TEXT NOT NULL,entity INTEGER,old_value INTEGER,new_value INTEGER,created INTEGER NOT NULL)',
        'CREATE INDEX public_ledger_owner ON public_ledger(uid,id)',
    ]
    for table in ('gold_wallet','ticket_wallet'):
        statements.append(f"CREATE TRIGGER public_{table}_audit AFTER UPDATE OF balance ON {table} WHEN OLD.balance!=NEW.balance BEGIN INSERT INTO public_ledger(uid,kind,old_value,new_value,created) VALUES(NEW.uid,'{table}',OLD.balance,NEW.balance,CAST(strftime('%s','now') AS INTEGER)); END")
    for event,alias in (('INSERT','NEW'),('DELETE','OLD'),('UPDATE','NEW')):
        statements.append(f"CREATE TRIGGER public_inventory_{event.lower()} AFTER {event} ON inventory BEGIN INSERT INTO public_ledger(uid,kind,entity,created) VALUES({alias}.uid,'inventory_{event.lower()}',{alias}.instance,CAST(strftime('%s','now') AS INTEGER)); END")
    db.execute('BEGIN IMMEDIATE')
    try:
        for statement in statements:db.execute(statement)
        db.execute('INSERT INTO public_schema VALUES(1,?)',(VERSION,));db.commit()
    except BaseException:db.rollback();raise
