"""Composition for the explicitly insecure, host-only two-VM fixture."""
import asyncio
from contextlib import AsyncExitStack
import json
from pathlib import Path
from .configuration import account_endpoints
from .features import load_content,room_hub
from .lifecycle import EventLog,own_services,start_services
from ..lab_network import LabEndpoint
from ..service import ReadyFile,Service
from ..store import Store
from ..sdp_peer import SdpPeerRouter


def lab_endpoints(path):
    config=json.loads(Path(path).read_text(encoding='utf-8-sig'))
    rows=account_endpoints(path);net=config.get('lab_network')
    if (not isinstance(net,dict) or set(net)!={'host','subnet','peers'} or
            not isinstance(net['peers'],dict) or set(net['peers'])!={str(row['uid']) for row in rows} or len(rows)!=2):
        raise ValueError('exactly two lab accounts and their peer assignments required')
    endpoints=[LabEndpoint(net['host'],net['peers'][str(row['uid'])],net['subnet']) for row in rows]
    if len({e.peer for e in endpoints})!=2:raise ValueError('each client must use a different VM address')
    return list(zip(rows,endpoints))


async def run(args):
    configured=lab_endpoints(args.config)
    content=load_content(args,shared=True)
    async with AsyncExitStack() as stack:
        store=Store(args.database);stack.callback(store.close)
        emit=stack.enter_context(EventLog(args.events,exclusive=True))
        hub=room_hub(args,content);peer_router=SdpPeerRouter(hub);services=[]
        for row,endpoint in configured:
            store.provision_local(row['uid'],row['account'],row['nickname'])
            services.append(Service(store,ReadyFile(row['role_ready_file']),host=endpoint.host,
                lab_endpoint=endpoint,offline_adapter=True,hub=hub,account_uid=row['uid'],
                login_port=row['login_port'],game_port=row['game_port'],p2p_port=row['p2p_port'],
                event_sink=lambda event,uid=row['uid']:emit(dict(account_uid=uid,**event)),
                map_catalog=content.maps,query_probe=True,sdp_router=peer_router))
        own_services(stack,services)
        if getattr(args,'repair_duplicate_weapon_instances',False):
            emit(dict(event='offline_weapon_identity_repair',changes=store.repair_duplicate_weapon_instances()))
        await start_services(services)
        print(json.dumps(dict(status='listening',authentication='INSECURE_HOST_ONLY_FIXTURE',
                              host=configured[0][1].host,clients=len(services))),flush=True)
        await asyncio.Event().wait()
