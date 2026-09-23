"""Local administrator registration with hidden input; no default password."""
import argparse
import asyncio
import getpass
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from server.kk_local.auth import AuthManager,AuthError
from server.kk_local.store import Store

def compatible_password(text):
    if not 12<=len(text)<=30 or any(not 33<=ord(c)<=126 for c in text):
        raise ValueError('Use12-30 visible ASCII characters for this SDK path')
    return text

async def register(path,account,nickname,password):
    compatible_password(password)
    store=Store(path)
    manager=AuthManager(store,[{'id':1,'name':'Local','host':'127.0.0.1','game_port':18001}])
    try:return await manager.register(account,password,nickname)
    finally:manager.close();store.close()

def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--database',required=True);p.add_argument('--account',required=True);p.add_argument('--nickname',required=True)
    a=p.parse_args()
    if not sys.stdin.isatty():
        p.error('interactive terminal required; do not pipe a password')
    first=getpass.getpass('New local password (12-30 visible ASCII): ')
    second=getpass.getpass('Confirm password: ')
    if first!=second:p.error('passwords differ')
    try:result=asyncio.run(register(a.database,a.account,a.nickname,first))
    except (ValueError,AuthError):
        print('Registration rejected; check account/nickname/password format or existing account.',file=sys.stderr)
        return 1
    finally:first=None;second=None
    # Deliberately do not output any session/token fields.
    print('Local account created. UID:',result['uid'])
    return 0

if __name__=='__main__':raise SystemExit(main())
