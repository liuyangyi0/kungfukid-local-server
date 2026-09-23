"""Compose own original-wire development without importing quarantined code."""
import asyncio
import contextlib
import json
import ssl
from .lifecycle import EventLog,own_services,start_services
from ..auth import AuthManager
from ..store import Store
from ..native_admission import NativeAdmission
from ..native_auth_api import NativeAuthAPI
from ..native_service import NativeService


async def run(args):
    # Fail before touching the database if private TLS inputs are unavailable.
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.minimum_version=ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(args.auth_certificate,args.auth_key)
    async with contextlib.AsyncExitStack() as stack:
        store=Store(args.database);stack.callback(store.close)
        auth=AuthManager(store,[dict(id=1,name='Native',host='127.0.0.1',game_port=args.game_port)])
        stack.callback(auth.close)
        emit=stack.enter_context(EventLog(args.events,timestamps=True))
        admission=NativeAdmission(auth,host=args.advertised_host,game_port=args.game_port,udp_port=args.udp_port,sdk_port=args.sdk_port,limit=args.max_players)
        game=NativeService(admission,game_port=args.game_port,udp_port=args.udp_port,sdk_port=args.sdk_port,event_sink=emit)
        api=NativeAuthAPI(admission,port=args.auth_port,context=context,event_sink=emit)
        own_services(stack,[api,game]);await start_services([game,api])
        print(json.dumps(dict(status='listening',mode='native',host='127.0.0.1',
                              auth_port=api.port,sdk_port=game.sdk_port,game_port=game.game_port,udp_port=game.udp_port,
                              client_adapter_qualified=False,udp_admission='own_dll_ticket_required',
                              game_transport='kk-aesgcm-v1',plaintext_fallback=False,
                              public_ready=False)),flush=True)
        await asyncio.Event().wait()
