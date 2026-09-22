"""Offline consistent backup and schema checks; never part of packet handlers."""
from contextlib import closing
from pathlib import Path
import os
import sqlite3
import time
from .public_schema import VERSION,version

def backup(source,destination):
    src=Path(source).resolve(strict=True);dst=Path(destination).resolve()
    if src==dst or dst.exists():raise ValueError('backup destination must be new')
    fd=os.open(dst,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600);os.close(fd)
    deadline=time.monotonic()+30
    def progress(status,remaining,total):
        if time.monotonic()>deadline:raise TimeoutError('backup deadline exceeded')
    try:
        with closing(sqlite3.connect(src.as_uri()+'?mode=ro',uri=True,timeout=.05)) as original:
            with closing(sqlite3.connect(dst)) as copy:
                original.backup(copy,pages=128,sleep=.01,progress=progress)
                if copy.execute('PRAGMA integrity_check').fetchone()!=('ok',):raise ValueError('backup integrity failure')
    except BaseException:dst.unlink(missing_ok=True);raise


def check_database(path):
    p=Path(path).resolve(strict=True)
    with closing(sqlite3.connect(p.as_uri()+'?mode=ro',uri=True,timeout=.05)) as db:
        if version(db)!=VERSION:raise ValueError('public_schema_migration_required')
        if db.execute('PRAGMA quick_check').fetchone()!=('ok',):raise ValueError('database check failed')
