"""Explicit local-only administration of an existing account.

Run interactively. Passwords are never accepted as command-line arguments.
"""
import argparse
import asyncio
import getpass
from pathlib import Path
from .store import Store
from .auth import AuthManager


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--database',required=True)
    p.add_argument('operation',choices=['set-password','set-weapons-permanent'])
    p.add_argument('account')
    args=p.parse_args()
    if args.operation == 'set-weapons-permanent':
        if not Path(args.database).is_file():
            p.error('existing database required; stop the service and back it up first')
        store = Store(args.database)
        try:
            row = store.profiles.exact_account_uid(args.account)
            if row is None:
                p.error('unknown local account')
            count = store.set_weapons_permanent(row[0])
            print(f'{count} existing weapons marked permanent. Profile and supplies unchanged.')
        finally:
            store.close()
        return
    first=getpass.getpass('New local game password (12-128 characters): ')
    second=getpass.getpass('Confirm password: ')
    if first!=second:
        p.error('passwords differ')
    store=Store(args.database)
    manager=AuthManager(store,[dict(id=1,name='Local',host='127.0.0.1',game_port=8001)])
    try:
        asyncio.run(manager.set_local_password(args.account,first))
        print('Password updated; previous sessions revoked. Inventory unchanged.')
    finally:
        manager.close(); store.close()


if __name__=='__main__': main()
