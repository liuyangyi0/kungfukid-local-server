"""Encrypted native ingress and authenticated relay; public policy is explicit.

No client attestation: possession of a credential grants only its server-owned
account permissions. Plaintext is restricted to isolated historical fixtures.
"""
import asyncio
import contextlib
import hashlib
import hmac
import socket
import sqlite3
import time
from .auth import AuthError
from .engine import Connection,Phase
from . import packets
from .native_relay import NativeRelay
from .native_crypto import NativeProtection,RecordError,SDK,GAME
from .native_udp_policy import DatagramPolicy,IngressBudget,RejectionSummary
from .public_policy import MemoryBudget,ConnectionBudget,FairRate
from .public_listener import BoundedListener
from .wire import GameDecoder,Message,ProtocolError,encode_game,sdp_header,read_login,encode_login


class NativeDatagrams(asyncio.DatagramProtocol):
    def __init__(self,service):self.service=service
    def connection_made(self,transport):self.service.udp=transport
    def datagram_received(self,data,peer):
        s=self.service
        began=time.monotonic();wire_size=len(data)
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
            if s.public_policy and not s.udp_bytes.take(grant.uid,len(data)+68):raise ProtocolError('public UDP byte rate')
            s.relay.handle(grant,data,peer)
        except RecordError:s.rejections.add('invalid_record')
        except AuthError:s.rejections.add('identity_or_phase')
        except (ProtocolError,ValueError):s.rejections.add('native_shape')
        finally:
            if s.metrics:s.metrics.udp_rx_bytes+=wire_size;s.metrics.observe('udp_dispatch',time.monotonic()-began)


class NativeService:
    def __init__(self,admission,*,game_port=0,udp_port=0,sdk_port=0,udp_login_verifier=None,
                 event_sink=None,idle_seconds=30,plaintext_test_only=False,datagram_policy=None,clock=time.monotonic,host='127.0.0.1',public_policy=None,metrics=None):
        if not public_policy and host!='127.0.0.1':raise ValueError('native development is loopback only')
        if public_policy and (plaintext_test_only or udp_login_verifier is not None):raise ValueError('public insecure override refused')
        self.host=host;self.public_policy=public_policy;self.metrics=metrics
        self.connections=ConnectionBudget(public_policy) if public_policy else None
        self.incoming=MemoryBudget(public_policy.incoming_bytes) if public_policy else None
        self.outgoing=MemoryBudget(public_policy.outgoing_bytes,per_group=public_policy.per_client_outgoing) if public_policy else None
        self.udp_bytes=FairRate(public_policy.bytes_per_second,public_policy.bytes_per_second*2,limit=100) if public_policy else None
        self.udp_send_bytes=FairRate(public_policy.udp_forward_bytes_per_second,public_policy.udp_forward_bytes_per_second*2,limit=1) if public_policy else None
        self.udp_control=FairRate(public_policy.udp_control_rate,public_policy.udp_control_burst,limit=100) if public_policy else None
        self.encode_slots=asyncio.Semaphore(public_policy.encode_workers if public_policy else 2);self.encode_waiters=0;self.encode_tasks=set()
        self.admission=admission;self.game_port=game_port;self.udp_port=udp_port
        self.sdk_port=sdk_port;self.sdk_listener=None
        self.udp_login_verifier=udp_login_verifier or admission.resolve_udp;self.event_sink=event_sink or (lambda _:None)
        self.idle_seconds=public_policy.idle_seconds if public_policy else idle_seconds;self.listener=None;self.udp=None;self.tasks=set();self.writers=set();self.sequence=0
        policy=datagram_policy or DatagramPolicy()
        self.ingress=IngressBudget(policy=policy,clock=clock)
        self.rejections=RejectionSummary(self.event_sink,clock=clock,seconds=policy.summary_seconds)
        self.summary_task=None
        self.relay=NativeRelay(admission,emit=self.send_udp,event=lambda **r:self.rejections.add('direct_not_advertised'))
        self.protection=None if plaintext_test_only else NativeProtection(admission)

    def event(self,event,**fields):
        with contextlib.suppress(OSError):self.event_sink(dict(event=event,**fields))

    def send_udp(self,data,peer):
        if self.public_policy:
            ident=sdp_header(data)[0]
            g=self.relay.peers.get(peer)
            if g is None:return
            if ident!=1009:
                if not self.udp_control.take(g.uid):return
            elif not self.udp_send_bytes.take(0,len(data)+68):
                if self.metrics:self.metrics.counts['udp_forward_budget_drop']+=1
                return
            if self.udp and self.udp.get_write_buffer_size()>self.public_policy.outgoing_bytes:
                if self.metrics:self.metrics.counts['udp_slow_transport_drop']+=1
                return
        if self.protection:
            grant=self.relay.peers.get(peer)
            if grant is None or grant.transport_udp is None:raise AuthError('encrypted UDP binding absent')
            self.admission.validate(grant);data=grant.transport_udp.seal(data)
        if self.udp:
            self.udp.sendto(data,peer)
            if self.metrics:
                self.metrics.udp_tx_bytes+=len(data)
                self.metrics.counts['udp_tx_packets']+=1
                self.metrics.udp_queue_peak=max(self.metrics.udp_queue_peak,self.udp.get_write_buffer_size())

    async def start(self):
        try:
            self.listener=(await BoundedListener(self.host,self.game_port,self.game,self.connections).start() if self.public_policy else await asyncio.start_server(self.game,self.host,self.game_port))
            self.game_port=self.listener.sockets[0].getsockname()[1]
            self.udp,_=await asyncio.get_running_loop().create_datagram_endpoint(lambda:NativeDatagrams(self),local_addr=(self.host,self.udp_port))
            if self.public_policy:self.udp.get_extra_info('socket').setsockopt(socket.SOL_SOCKET,socket.SO_RCVBUF,4*1024*1024)
            self.udp_port=self.udp.get_extra_info('sockname')[1]
            self.admission.game_port=self.game_port;self.admission.udp_port=self.udp_port
            self.sdk_listener=(await BoundedListener(self.host,self.sdk_port,self.sdk,self.connections).start() if self.public_policy else await asyncio.start_server(self.sdk,self.host,self.sdk_port))
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
        if self.encode_tasks:await asyncio.gather(*tuple(self.encode_tasks),return_exceptions=True)
        for grant in tuple(self.admission.grants.values()):self.relay.forget(grant);self.admission.release(grant)

    async def summarize(self):
        last=time.monotonic();maintenance=last
        while True:
            await asyncio.sleep(.01 if self.metrics else 1)
            now=time.monotonic()
            if self.metrics:self.metrics.observe('loop_lag',max(0,now-last-.01))
            last=now
            with contextlib.suppress(OSError):self.rejections.flush()
            if self.public_policy and now-maintenance>=1:self.admission.prune();self.admission.hub.expire();maintenance=now

    async def sdk(self,reader,writer,lease=None):
        if not self.public_policy and len(self.writers)>=32:writer.close();return
        self.writers.add(writer);self.tasks.add(asyncio.current_task());grant=None
        raw_writer=writer;deadline=time.monotonic()+(self.public_policy.admission_seconds if self.public_policy else 60)
        async def admit(awaitable,legacy):
            return await asyncio.wait_for(awaitable,max(.001,deadline-time.monotonic()) if self.public_policy else legacy)
        try:
            outer=None
            if self.protection:
                async with asyncio.timeout(self.public_policy.admission_seconds if self.public_policy else 30):
                    reader,writer,outer=await self.protection.accept(reader,writer,SDK,header_timeout=2 if self.public_policy else 10)
            if lease:lease.promote(outer.uid)
            # Own DLL connection admission prefix, outside the unchanged native
            # SDK frames. Never derive an account from encrypted opaque LS data.
            prefix=await admit(reader.readexactly(36),10)
            if prefix[:4]!=b'KKS1':raise AuthError('SDK admission prefix required')
            if outer is not None and not hmac.compare_digest(outer.sdk_credential_digest,hashlib.sha256(prefix[4:]).digest()):raise AuthError('encrypted SDK identity mismatch')
            grant=self.admission.claim_sdk(prefix[4:]);prefix=b''
            flags,_=await admit(read_login(reader),20)
            if flags!=1:raise ProtocolError('SDK authentication frame expected')
            self.admission.validate(grant)
            writer.write(encode_login(packets.login_ack(grant.account,grant.uid)));await asyncio.wait_for(writer.drain(),2)
            flags,body=await admit(read_login(reader),20)
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

    async def game(self,reader,writer,lease=None):
        if not self.public_policy and len(self.writers)>=32:writer.close();return
        self.writers.add(writer);self.tasks.add(asyncio.current_task());self.sequence+=1
        c=Connection(self.sequence);decoder=GameDecoder();grant=None;lock=asyncio.Lock();pulse_task=None
        read_owner=object()
        if self.public_policy:
            from .public_commands import validate_header
            decoder=GameDecoder(header_validator=validate_header)
        raw_writer=writer
        last_rx=time.monotonic();accepted_at=last_rx;rate_start=last_rx;rate_count=0;partial_since=None
        async def send(messages):
            if not messages:return
            reservation=object()
            try:
                if self.outgoing:self.outgoing.set(reservation,sum(2*(len(m.payload)+128) for m in messages),group=grant.uid)
                async with lock:
                    for message in messages:
                        if self.public_policy and len(message.payload)>4096:
                            if self.encode_waiters>=self.public_policy.encode_workers+self.public_policy.encode_waiters:raise ProtocolError('encoding budget')
                            self.encode_waiters+=1
                            try:
                                await self.encode_slots.acquire()
                                task=asyncio.create_task(asyncio.to_thread(encode_game,message));self.encode_tasks.add(task)
                                def encoded(done):
                                    self.encode_tasks.discard(done);self.encode_slots.release()
                                    if not done.cancelled():done.exception()
                                task.add_done_callback(encoded)
                                data=await asyncio.shield(task)
                            finally:self.encode_waiters-=1
                        else:data=encode_game(message)
                        writer.write(data)
                    await asyncio.wait_for(writer.drain(),2)
            finally:
                if self.outgoing:self.outgoing.release(reservation)
        async def pulse():
            heartbeat=0
            while True:
                self.admission.validate(grant);e=grant.engine
                if time.monotonic()-last_rx>self.idle_seconds or e.delivery_failed:writer.close();return
                if not self.public_policy:self.admission.hub.expire()
                e.poll_battle_clock(c)
                replies=e.profile_ready(c,grant.ready)+e.take_pending(c)
                if time.monotonic()>=heartbeat:heartbeat=time.monotonic()+1;replies.append(Message(0))
                await send(replies);await asyncio.sleep(.1)
        def completed(task):
            if not task.cancelled() and task.exception() is not None:writer.close()
        try:
            outer=None
            if self.protection:
                async with asyncio.timeout(self.public_policy.admission_seconds if self.public_policy else 30):
                    reader,writer,outer=await self.protection.accept(reader,writer,GAME,header_timeout=2 if self.public_policy else 10)
            if lease:lease.promote(outer.uid)
            while True:
                timeout=max(.001,10-(time.monotonic()-accepted_at)) if grant is None else self.idle_seconds
                if self.public_policy and partial_since is not None:timeout=min(timeout,max(.001,self.public_policy.admission_seconds-(time.monotonic()-partial_since)))
                data=await asyncio.wait_for(reader.read(65536),timeout)
                if not data:decoder.eof();break
                if self.incoming:self.incoming.set(read_owner,4*(len(decoder.buffer)+len(data)))
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
                        if self.outgoing:grant.engine.queue_budget=self.outgoing
                    else:self.admission.validate(grant)
                    e=grant.engine;dispatch_started=time.monotonic()
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
                    if message.id==2060 and c.phase==Phase.CLOSED:grant.lobby_ready=False;grant.admission_deadline=time.monotonic()+120
                    if message.id==1010:c.bootstrap_sent=True
                    await send(out+e.profile_ready(c,grant.ready))
                    if self.metrics:self.metrics.observe('game_dispatch',time.monotonic()-dispatch_started)
                    if c.phase==Phase.CLOSED:return
                    if pulse_task is None:pulse_task=asyncio.create_task(pulse());pulse_task.add_done_callback(completed)
                if self.incoming:self.incoming.set(read_owner,4*len(decoder.buffer))
                if decoder.buffer:
                    if partial_since is None:partial_since=time.monotonic()
                else:partial_since=None
        except (AuthError,ProtocolError,ValueError,OverflowError,OSError,sqlite3.DatabaseError,asyncio.TimeoutError,asyncio.IncompleteReadError) as exc:
            self.event('native_tcp_closed',reason=type(exc).__name__)
        finally:
            if self.incoming:self.incoming.release(read_owner)
            if pulse_task:pulse_task.cancel();await asyncio.gather(pulse_task,return_exceptions=True)
            if grant is not None:
                e=grant.engine
                current=e.game is c or (e.bootstrap is c and c.phase!=Phase.HANDOFF)
                if current:self.relay.forget(grant);self.admission.release(grant)
                else:e.disconnect(c)
            self.writers.discard(raw_writer);self.tasks.discard(asyncio.current_task());writer.close()
            with contextlib.suppress(OSError,asyncio.TimeoutError):await asyncio.wait_for(writer.wait_closed(),2)
