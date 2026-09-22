"""Encrypted own-client TCP ingress + authenticated SDP relay, loopback only.

No OpenKFO, WebSocket, JSON/base64 game envelopes, or implicit bypass. Public
start is deliberately unavailable until client adapters are qualified. Default
SDK/TCP/UDP records require AES-GCM before native parsing. UDP additionally
requires the own DLL ticket in the original user-data field. Plaintext exists
only behind an explicit constructor flag for isolated historical unit fixtures.
"""
import asyncio
import contextlib
import hashlib
import hmac
import time
from .auth import AuthError
from .engine import Connection,Phase
from . import packets
from .native_relay import NativeRelay
from .native_crypto import NativeProtection,RecordError,SDK,GAME
from .native_udp_policy import DatagramPolicy,IngressBudget,RejectionSummary
from .wire import GameDecoder,Message,ProtocolError,encode_game,sdp_header,read_login,encode_login


class NativeDatagrams(asyncio.DatagramProtocol):
    def __init__(self,service):self.service=service
    def connection_made(self,transport):self.service.udp=transport
    def datagram_received(self,data,peer):
        s=self.service
        try:
            bound=s.relay.peers.get(peer)
            if bound is not None:
                try:s.admission.validate(bound)
                except AuthError:bound=None
            if not s.ingress.allow(peer,bound.credential_digest if bound is not None else None):
                s.rejections.add('preauth_budget');return
            outer=None
            if s.protection:outer,data=s.protection.datagram(data)
            ident,_,_,_,_,_=sdp_header(data)
            grant=s.relay.peers.get(peer)
            if ident==1001:
                if s.udp_login_verifier is None:raise AuthError('udp_login_verifier_required')
                grant=s.udp_login_verifier(data,peer)
            if grant is None:raise AuthError('unbound_udp')
            if outer is not None and outer is not grant:raise AuthError('encrypted UDP identity mismatch')
            s.relay.handle(grant,data,peer)
        except RecordError:s.rejections.add('invalid_record')
        except AuthError:s.rejections.add('identity_or_phase')
        except (ProtocolError,ValueError):s.rejections.add('native_shape')


class NativeService:
    def __init__(self,admission,*,game_port=0,udp_port=0,sdk_port=0,udp_login_verifier=None,
                 event_sink=None,idle_seconds=30,plaintext_test_only=False,datagram_policy=None,clock=time.monotonic):
        self.admission=admission;self.game_port=game_port;self.udp_port=udp_port
        self.sdk_port=sdk_port;self.sdk_listener=None
        self.udp_login_verifier=udp_login_verifier or admission.resolve_udp;self.event_sink=event_sink or (lambda _:None)
        self.idle_seconds=idle_seconds;self.listener=None;self.udp=None;self.tasks=set();self.writers=set();self.sequence=0
        policy=datagram_policy or DatagramPolicy()
        self.ingress=IngressBudget(policy=policy,clock=clock)
        self.rejections=RejectionSummary(self.event_sink,clock=clock,seconds=policy.summary_seconds)
        self.summary_task=None
        self.relay=NativeRelay(admission,emit=self.send_udp,event=lambda **r:self.rejections.add('direct_not_advertised'))
        self.protection=None if plaintext_test_only else NativeProtection(admission)

    def event(self,event,**fields):
        with contextlib.suppress(OSError):self.event_sink(dict(event=event,**fields))

    def send_udp(self,data,peer):
        if self.protection:
            grant=self.relay.peers.get(peer)
            if grant is None or grant.transport_udp is None:raise AuthError('encrypted UDP binding absent')
            self.admission.validate(grant);data=grant.transport_udp.seal(data)
        if self.udp:self.udp.sendto(data,peer)

    async def start(self):
        try:
            self.listener=await asyncio.start_server(self.game,'127.0.0.1',self.game_port)
            self.game_port=self.listener.sockets[0].getsockname()[1]
            self.udp,_=await asyncio.get_running_loop().create_datagram_endpoint(lambda:NativeDatagrams(self),local_addr=('127.0.0.1',self.udp_port))
            self.udp_port=self.udp.get_extra_info('sockname')[1]
            self.admission.game_port=self.game_port;self.admission.udp_port=self.udp_port
            self.sdk_listener=await asyncio.start_server(self.sdk,'127.0.0.1',self.sdk_port)
            self.sdk_port=self.sdk_listener.sockets[0].getsockname()[1];self.admission.sdk_port=self.sdk_port
            self.summary_task=asyncio.create_task(self.summarize())
            return self
        except BaseException:await self.close();raise

    async def close(self):
        if self.summary_task:
            self.summary_task.cancel();await asyncio.gather(self.summary_task,return_exceptions=True);self.summary_task=None
        with contextlib.suppress(OSError):self.rejections.flush(force=True)
        if self.sdk_listener:self.sdk_listener.close();await self.sdk_listener.wait_closed();self.sdk_listener=None
        if self.listener:self.listener.close();await self.listener.wait_closed();self.listener=None
        if self.udp:self.udp.close();self.udp=None
        for writer in tuple(self.writers):writer.close()
        tasks=list(self.tasks)
        for task in tasks:task.cancel()
        if tasks:await asyncio.gather(*tasks,return_exceptions=True)
        for grant in tuple(self.admission.grants.values()):self.relay.forget(grant);self.admission.release(grant)

    async def summarize(self):
        while True:
            await asyncio.sleep(1)
            with contextlib.suppress(OSError):self.rejections.flush()

    async def sdk(self,reader,writer):
        if len(self.writers)>=32:writer.close();return
        self.writers.add(writer);self.tasks.add(asyncio.current_task());grant=None
        raw_writer=writer
        try:
            outer=None
            if self.protection:reader,writer,outer=await self.protection.accept(reader,writer,SDK)
            # Own DLL connection admission prefix, outside the unchanged native
            # SDK frames. Never derive an account from encrypted opaque LS data.
            prefix=await asyncio.wait_for(reader.readexactly(36),10)
            if prefix[:4]!=b'KKS1':raise AuthError('SDK admission prefix required')
            if outer is not None and not hmac.compare_digest(outer.sdk_credential_digest,hashlib.sha256(prefix[4:]).digest()):raise AuthError('encrypted SDK identity mismatch')
            grant=self.admission.claim_sdk(prefix[4:]);prefix=b''
            flags,_=await asyncio.wait_for(read_login(reader),20)
            if flags!=1:raise ProtocolError('SDK authentication frame expected')
            self.admission.validate(grant)
            writer.write(encode_login(packets.login_ack(grant.account,grant.uid)));await asyncio.wait_for(writer.drain(),2)
            flags,body=await asyncio.wait_for(read_login(reader),20)
            if flags!=0 or body[:2]!=bytes((0xf3,3)):raise ProtocolError('SDK directory request expected')
            self.admission.validate(grant);grant.sdk_admitted=True
            self.event('native_sdk_authenticated',uid=grant.uid)
            writer.write(encode_login(packets.login_directory(self.game_port,self.admission.host)));await asyncio.wait_for(writer.drain(),2)
            while True:
                try:more=await asyncio.wait_for(reader.read(4096),2)
                except asyncio.TimeoutError:self.admission.validate(grant);continue
                if not more:break
                raise ProtocolError('unexpected SDK post-directory data')
        except (AuthError,ProtocolError,asyncio.IncompleteReadError,asyncio.TimeoutError,OSError,ValueError) as exc:
            self.event('native_sdk_closed',reason=type(exc).__name__)
        finally:
            if grant is not None:grant.sdk_connected=False
            self.writers.discard(raw_writer);self.tasks.discard(asyncio.current_task());writer.close()
            with contextlib.suppress(OSError,asyncio.TimeoutError):await asyncio.wait_for(writer.wait_closed(),2)

    async def game(self,reader,writer):
        if len(self.writers)>=32:writer.close();return
        self.writers.add(writer);self.tasks.add(asyncio.current_task());self.sequence+=1
        c=Connection(self.sequence);decoder=GameDecoder();grant=None;lock=asyncio.Lock();pulse_task=None
        raw_writer=writer
        last_rx=time.monotonic();accepted_at=last_rx;rate_start=last_rx;rate_count=0
        async def send(messages):
            if not messages:return
            async with lock:
                for message in messages:writer.write(encode_game(message))
                await asyncio.wait_for(writer.drain(),2)
        async def pulse():
            heartbeat=0
            while True:
                self.admission.validate(grant);e=grant.engine
                if time.monotonic()-last_rx>self.idle_seconds or e.delivery_failed:writer.close();return
                self.admission.hub.expire();e.poll_battle_clock(c)
                replies=e.profile_ready(c,grant.ready)+e.take_pending(c)
                if time.monotonic()>=heartbeat:heartbeat=time.monotonic()+1;replies.append(Message(0))
                await send(replies);await asyncio.sleep(.1)
        def completed(task):
            if not task.cancelled() and task.exception() is not None:writer.close()
        try:
            outer=None
            if self.protection:reader,writer,outer=await self.protection.accept(reader,writer,GAME)
            while True:
                timeout=max(.001,10-(time.monotonic()-accepted_at)) if grant is None else self.idle_seconds
                data=await asyncio.wait_for(reader.read(65536),timeout)
                if not data:decoder.eof();break
                for message in decoder.feed(data):
                    now=time.monotonic()
                    if now-rate_start>=1:rate_start=now;rate_count=0
                    rate_count+=1
                    if rate_count>5000:raise ProtocolError('message rate')
                    last_rx=now
                    if grant is None:
                        if now-accepted_at>=10:raise AuthError('native_login_timeout')
                        if message.id==0 and not message.payload:continue
                        if outer is not None and hashlib.sha256(message.payload[17:49]).digest()!=outer.credential_digest:raise AuthError('encrypted game identity mismatch')
                        grant=self.admission.resolve(message)
                    else:self.admission.validate(grant)
                    e=grant.engine
                    try:out=e.handle(c,message)
                    except packets.RoomRequestRejected as exc:
                        if message.id!=3010:raise
                        out=[packets.room_rejection(exc.code)]
                    if message.id in (1010,2010) and (e.bootstrap is c or e.game is c):
                        grant.tcp_peer=writer.get_extra_info('peername')
                        self.event('native_game_login',uid=grant.uid,id=message.id)
                    if message.id==2250 and c is e.game and c.phase==Phase.LOBBY:
                        if not grant.lobby_ready:self.event('native_lobby_ready',uid=grant.uid)
                        grant.lobby_ready=True
                    if message.id==2060 and c.phase==Phase.CLOSED:grant.lobby_ready=False
                    if message.id==1010:c.bootstrap_sent=True
                    await send(out+e.profile_ready(c,grant.ready))
                    if c.phase==Phase.CLOSED:return
                    if pulse_task is None:pulse_task=asyncio.create_task(pulse());pulse_task.add_done_callback(completed)
        except (AuthError,ProtocolError,ValueError,OSError,asyncio.TimeoutError,asyncio.IncompleteReadError) as exc:
            self.event('native_tcp_closed',reason=type(exc).__name__)
        finally:
            if pulse_task:pulse_task.cancel();await asyncio.gather(pulse_task,return_exceptions=True)
            if grant is not None:
                e=grant.engine
                current=e.game is c or (e.bootstrap is c and c.phase!=Phase.HANDOFF)
                if current:self.relay.forget(grant);self.admission.release(grant)
                else:e.disconnect(c)
            self.writers.discard(raw_writer);self.tasks.discard(asyncio.current_task());writer.close()
            with contextlib.suppress(OSError,asyncio.TimeoutError):await asyncio.wait_for(writer.wait_closed(),2)
