"""Offline-only database/invitation maintenance. No public administrative API."""
import argparse
import asyncio
from contextlib import closing
import getpass
import json
import os
from pathlib import Path
import sqlite3
import time
from .store import Store
from .auth import AuthManager
from .storage.public_maintenance import backup,check_database

class DatabaseLease:
    def __init__(self,path):self.path=Path(str(Path(path).resolve())+'.service.lock');self.file=None
    def __enter__(self):
        self.file=open(self.path,'a+b');self.file.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(self.file.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(self.file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except (OSError,IOError):self.file.close();self.file=None;raise ValueError('database is in use by the public service') from None
        return self
    def __exit__(self,*exc):
        if self.file:self.file.close();self.file=None


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--database',required=True)
    sub=p.add_subparsers(dest='action',required=True)
    sub.add_parser('init');m=sub.add_parser('migrate');m.add_argument('--backup',required=True)
    b=sub.add_parser('backup');b.add_argument('--output',required=True)
    r=sub.add_parser('restore');r.add_argument('--source',required=True)
    i=sub.add_parser('invite');i.add_argument('--count',type=int,default=1)
    sub.add_parser('revoke-invite');d=sub.add_parser('disable');d.add_argument('--uid',type=int,required=True)
    s=sub.add_parser('set-password');s.add_argument('--account',required=True)
    args=p.parse_args(argv);path=Path(args.database).resolve()
    if args.action=='restore':
        if path.exists():raise ValueError('restore only to a new database path')
        check_database(args.source);backup(args.source,path);check_database(path);print('restored to new database');return
    if args.action=='init':
        if path.exists():raise ValueError('init requires a new database')
        path.parent.mkdir(parents=True,exist_ok=True)
    elif not path.is_file():raise ValueError('existing database required')
    with DatabaseLease(path):
        if args.action=='backup':backup(path,args.output);print('backup complete');return
        if args.action=='migrate':backup(path,args.backup)
        store=Store(str(path))
        try:
            # Create the existing credential tables only via their authority.
            store.authentication
            if args.action in ('init','migrate'):store.migrate_public();print('public schema ready');return
            access=store.public_access
            if args.action=='invite':
                import time
                if not 1<=args.count<=100:raise ValueError('invitation batch limit 1..100')
                with store.transaction():codes=[access.create_invite(int(time.time())) for _ in range(args.count)]
                print(json.dumps({'invitation_codes':codes})) # intentional one-time operator output, never service logs
            elif args.action=='revoke-invite':
                code=getpass.getpass('Invitation code: ')
                with store.transaction():access.revoke_invite(code)
                print('invitation revoked')
            elif args.action=='disable':
                with store.transaction():access.set_disabled(args.uid,True);store.authentication.remove_account_sessions(args.uid)
                print('account disabled; sessions revoked')
            else:
                auth=AuthManager(store,[dict(id=1,name='Public',host='127.0.0.1',game_port=18001)])
                try:asyncio.run(auth.set_local_password(args.account,getpass.getpass('New password: ')))
                finally:auth.close()
                print('password replaced; sessions revoked')
        finally:store.close()

if __name__=='__main__':main()
