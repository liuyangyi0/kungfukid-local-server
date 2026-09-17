"""Local-only password setup for an existing pre-authentication account.

Run interactively. Passwords are never accepted as command-line arguments.
"""
import argparse
import asyncio
import getpass
from .store import Store
from .auth import AuthManager


def main():
    p=argparse.ArgumentParser()
    p.add_argument('--database',required=True)
    p.add_argument('operation',choices=['set-password'])
    p.add_argument('account')
    args=p.parse_args()
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
