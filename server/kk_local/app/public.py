"""Platform-independent public profile. No VM, local PID or client-root input."""
import asyncio
import contextlib
import json
from pathlib import Path
import signal
import ssl
import time
from ..public_admin import DatabaseLease,check_database
from ..public_auth import PublicAuthManager
from ..public_auth_api import PublicAuthAPI
from ..public_metrics import Metrics,RotatingSummary,health_server
from ..native_admission import NativeAdmission
from ..native_service import NativeService
from ..native_udp_policy import DatagramPolicy
from ..store import Store

class PublicRuntime:
    def __init__(self,args):self.args=args;self.stack=None;self.summary_task=None;self.metrics=Metrics()
    async def start(self):
        a=self.args
        context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
        context.options|=ssl.OP_NO_COMPRESSION
        context.load_cert_chain(a.auth_certificate,a.auth_key)
        from cryptography import x509
        from datetime import datetime,timezone
        certificate=x509.load_pem_x509_certificate(Path(a.auth_certificate).read_bytes())
        if not certificate.not_valid_before_utc<=datetime.now(timezone.utc)<certificate.not_valid_after_utc:raise ValueError('TLS certificate outside validity period')
        check_database(a.database)
        from ..combat_catalog import CombatCatalog
        combat=(CombatCatalog.from_projection_file(a.combat_receipt_policy)
                if getattr(a,'combat_receipt_policy',None) else
                CombatCatalog.from_file(a.combat_skill_xml) if getattr(a,'combat_skill_xml',None) else None)
        self.stack=contextlib.AsyncExitStack()
        try:
            self.stack.enter_context(DatabaseLease(a.database))
            self.store=Store(a.database,public=True);self.stack.callback(self.store.close)
            self.store.transaction_observer=lambda duration:self.metrics.observe('database',duration)
            Path(a.events).parent.mkdir(parents=True,exist_ok=True)
            self.log=RotatingSummary(a.events,self.metrics);self.stack.callback(self.log.close)
            self.auth=PublicAuthManager(self.store,[dict(id=1,name='Public non-ranked',host='127.0.0.1',game_port=a.game_port or 18001)],policy=a.public_policy)
            self.stack.callback(self.auth.close)
            self.admission=NativeAdmission(self.auth,host=a.advertised_host,game_port=a.game_port,udp_port=a.udp_port,sdk_port=a.sdk_port,limit=a.public_policy.online,public_policy=a.public_policy)
            self.admission.hub.combat_catalog=combat
            self.game=NativeService(self.admission,host=a.listen_host,game_port=a.game_port,udp_port=a.udp_port,sdk_port=a.sdk_port,public_policy=a.public_policy,
                                    metrics=self.metrics,event_sink=self.metrics.event,datagram_policy=DatagramPolicy(bound_limit=a.public_policy.online))
            self.api=PublicAuthAPI(self.admission,host=a.listen_host,port=a.auth_port,context=context,policy=a.public_policy,event_sink=self.metrics.event)
            self.stack.push_async_callback(self.game.close);self.stack.push_async_callback(self.api.close)
            await self.game.start();await self.api.start()
            self.health=await health_server(self.metrics,self.game,port=a.health_port)
            async def close_health():self.health.close();await self.health.wait_closed()
            self.stack.push_async_callback(close_health)
            self.summary_task=asyncio.create_task(self.summarize())
            return self
        except BaseException:await self.close();raise
    async def summarize(self):
        while True:
            await asyncio.sleep(10)
            self.metrics.counts['auth_listener_errors']=getattr(self.api.server,'errors',0)
            with contextlib.suppress(OSError):self.log.emit()
    def status(self):
        return dict(service_ready=True,mode='public',security_qualification='not-a-security-certification',
                    auth_port=self.api.port,sdk_port=self.game.sdk_port,game_port=self.game.game_port,udp_port=self.game.udp_port,
                    max_online=self.args.public_policy.online,ranked=False)
    async def close(self):
        if self.summary_task:self.summary_task.cancel();await asyncio.gather(self.summary_task,return_exceptions=True);self.summary_task=None
        if self.stack:await self.stack.aclose();self.stack=None

async def run(args):
    runtime=await PublicRuntime(args).start();stop=asyncio.Event();loop=asyncio.get_running_loop()
    for sig in (signal.SIGINT,signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):loop.add_signal_handler(sig,stop.set)
    try:print(json.dumps(runtime.status()),flush=True);await stop.wait()
    finally:await runtime.close()
