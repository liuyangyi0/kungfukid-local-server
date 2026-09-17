import argparse
import asyncio
import json
from pathlib import Path
from .service import Service, ReadyFile
from .store import Store
from .rooms import RoomHub


def account_endpoints(path):
    """Explicit trusted local endpoint assignments; no implicit identity guessing."""
    config=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    rows=config.get('accounts')
    if config.get('schema')!='kk-offline-account-endpoints-v1' or not isinstance(rows,list) or not 1<=len(rows)<=8:
        raise ValueError('invalid offline account endpoint configuration')
    uids=set(); names=set(); tcp=set(); udp=set(); receipts=set()
    for row in rows:
        if not isinstance(row,dict) or set(row)!={'uid','account','nickname','login_port','game_port','p2p_port','role_ready_file'}:
            raise ValueError('unexpected account endpoint fields')
        uid=row['uid']; name=row['account']; nickname=row['nickname']; ready=row['role_ready_file']
        if type(uid) is not int or not 0<uid<=0x7fffffffffffffff or uid in uids:
            raise ValueError('invalid or duplicate account UID')
        if not isinstance(name,str) or not name.isascii() or not name.isalnum() or len(name)>20 or name in names:
            raise ValueError('invalid or duplicate account name')
        if not isinstance(nickname,str) or not 1<=len(nickname.encode('gbk'))<=20 or '\0' in nickname:
            raise ValueError('invalid nickname')
        if not isinstance(ready,str) or not ready or '\0' in ready or not Path(ready).is_absolute():
            raise ValueError('readiness receipt must be an absolute path')
        receipt_key=str(Path(ready).resolve()).casefold()
        if receipt_key in receipts:
            raise ValueError('each client needs an independent readiness receipt')
        for key in ('login_port','game_port','p2p_port'):
            if type(row[key]) is not int or not 1<=row[key]<=65535:
                raise ValueError('invalid endpoint port')
        if row['login_port']==row['game_port'] or row['login_port'] in tcp or row['game_port'] in tcp or row['p2p_port'] in udp:
            raise ValueError('duplicate listening endpoints')
        uids.add(uid); names.add(name); tcp.update((row['login_port'],row['game_port']))
        udp.add(row['p2p_port']); receipts.add(receipt_key)
    return rows


async def run(args):
    from .maps import MapCatalog
    map_root=getattr(args,'map_client_root',None)
    map_catalog=MapCatalog.from_client(map_root) if map_root else None
    rows=account_endpoints(args.experimental_multi_account_config) if args.experimental_multi_account_config else None
    if rows is None and not args.role_ready_file:
        raise ValueError('--role-ready-file is required for the single-account endpoint')
    store = Store(args.database)
    store.seed_local()
    if args.grant_plan:
        plan = json.loads(Path(args.grant_plan).read_text(encoding='utf-8-sig'))
        print(json.dumps(dict(grant_id=plan.get('grant_id'),added=store.apply_grant(plan))),flush=True)
    Path(args.log).parent.mkdir(parents=True, exist_ok=True)
    log = open(args.log, 'a', encoding='utf-8', buffering=1)
    def emit(row):
        log.write(json.dumps(row, ensure_ascii=False) + '\n')
    services=[]
    try:
        if rows:
            hub=RoomHub()
            for row in rows:
                store.provision_local(row['uid'],row['account'],row['nickname'])
                services.append(Service(store,ReadyFile(row['role_ready_file']),account_uid=row['uid'],hub=hub,
                    login_port=row['login_port'],game_port=row['game_port'],p2p_port=row['p2p_port'],
                    offline_adapter=args.offline_client_adapter,training_rewards=args.local_training_rewards,
                    event_sink=lambda event,uid=row['uid']:emit(dict(account_uid=uid,**event)),map_catalog=map_catalog))
        else:
            services.append(Service(store, ReadyFile(args.role_ready_file), login_port=args.login_port,
                          game_port=args.game_port, p2p_port=args.p2p_port,
                          offline_adapter=args.offline_client_adapter, event_sink=emit,
                          training_rewards=args.local_training_rewards,map_catalog=map_catalog))
        for service in services:
            await service.start()
            print(json.dumps(dict(status='listening',account_uid=service.account_uid,login=service.login_port,
                                  game=service.game_port,udp=service.p2p_port)),flush=True)
        await asyncio.Event().wait()
    finally:
        for service in services:
            await service.close()
        Path(args.report).parent.mkdir(parents=True, exist_ok=True)
        reports=[service.report() for service in services]
        Path(args.report).write_text(json.dumps(reports if rows or len(reports)!=1 else reports[0], indent=2), encoding='utf-8')
        log.close()
        store.close()


def main():
    p = argparse.ArgumentParser(description='Loopback-only KK local adapter service (not public authentication)')
    p.add_argument('--offline-client-adapter', action='store_true', required=True)
    p.add_argument('--role-ready-file', help='Fresh native RoleProperty readiness receipt')
    p.add_argument('--map-client-root', help='Installed client root: read its own map config and check RWS/BSP; omitted legacy fixture stays804-only')
    p.add_argument('--experimental-multi-account-config',
                   help='Explicit per-account loopback endpoints with a shared room hub; native multiplayer unverified')
    p.add_argument('--database', default='server/.data/local.sqlite3')
    p.add_argument('--grant-plan', help='Explicit local inventory grant, applied once per account/grant ID')
    p.add_argument('--local-training-rewards', action='store_true',
                   help='PROVISIONAL local policy:100 points/hour,2400 cap per claim; no premium/chests')
    p.add_argument('--log', default='server/.data/events.jsonl')
    p.add_argument('--report', default='server/.data/report.json')
    p.add_argument('--login-port', type=int, default=8000)
    p.add_argument('--game-port', type=int, default=8001)
    p.add_argument('--p2p-port', type=int, default=8001)
    args = p.parse_args()
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        pass


if __name__ == '__main__':
    main()
