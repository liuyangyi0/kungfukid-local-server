"""Composition of the legacy explicit loopback/offline adapter mode."""
import asyncio
from contextlib import AsyncExitStack
import json
from pathlib import Path
from .configuration import account_endpoints
from .features import load_content,room_hub
from .lifecycle import EventLog,own_services
from ..service import Service,ReadyFile
from ..store import Store


async def run(args):
    rows=account_endpoints(args.experimental_multi_account_config) if args.experimental_multi_account_config else None
    if rows is None and not args.role_ready_file:raise ValueError('--role-ready-file is required for the single-account endpoint')
    content=load_content(args,shared=bool(rows))
    async with AsyncExitStack() as stack:
        store=Store(args.database);stack.callback(store.close)
        emit=stack.enter_context(EventLog(args.log))
        services=[]
        def write_report():
            path=Path(args.report);path.parent.mkdir(parents=True,exist_ok=True)
            reports=[s.report() for s in services]
            path.write_text(json.dumps(reports if rows or len(reports)!=1 else reports[0],indent=2),encoding='utf-8')
        stack.callback(write_report)
        store.seed_local()
        if args.grant_plan:
            plan=json.loads(Path(args.grant_plan).read_text(encoding='utf-8-sig'))
            print(json.dumps(dict(grant_id=plan.get('grant_id'),added=store.apply_grant(plan))),flush=True)
        if rows:
            hub=room_hub(args,content)
            for row in rows:
                store.provision_local(row['uid'],row['account'],row['nickname'])
                services.append(Service(store,ReadyFile(row['role_ready_file']),account_uid=row['uid'],hub=hub,
                    login_port=row['login_port'],game_port=row['game_port'],p2p_port=row['p2p_port'],
                    offline_adapter=args.offline_client_adapter,training_rewards=args.local_training_rewards,
                    event_sink=lambda event,uid=row['uid']:emit(dict(account_uid=uid,**event)),map_catalog=content.maps))
        else:
            services.append(Service(store,ReadyFile(args.role_ready_file),login_port=args.login_port,
                game_port=args.game_port,p2p_port=args.p2p_port,offline_adapter=args.offline_client_adapter,
                event_sink=emit,training_rewards=args.local_training_rewards,map_catalog=content.maps))
        own_services(stack,services)
        for service in services:
            await service.start()
            print(json.dumps(dict(status='listening',account_uid=service.account_uid,login=service.login_port,
                                  game=service.game_port,udp=service.p2p_port)),flush=True)
        await asyncio.Event().wait()
