"""One asyncio process owns SDK TCP, GS TCP, UDP and persistent local records."""
import asyncio
import contextlib
import ipaddress
import json
import socket
import struct
import time
from pathlib import Path
from .engine import Connection, Engine, Phase
from .wire import (GameDecoder, Message, ProtocolError, encode_game, encode_login,
                   read_login, sdp_header, sdp_reply)
from . import packets
from .auth import AuthError


class ReadyFile:
    def __init__(self, path, start_time=None):
        self.path = Path(path)
        self.start_time = time.time() if start_time is None else start_time

    def __call__(self):
        try:
            if self.path.stat().st_mtime < self.start_time:
                return False
            value = self.path.read_text(encoding='utf-8-sig').strip()
            return value.startswith('0x') and 0x10000 <= int(value, 16) <= 0xffffffff
        except (OSError, ValueError):
            return False

    def invalidate(self):
        # A previous client's pointer must not authorize a later client process.
        self.start_time = time.time()


class SdpProtocol(asyncio.DatagramProtocol):
    def __init__(self, service):
        self.service = service
        self.transport = None
        self.next_id = 1001

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, peer):
        engine = self.service.engine
        try:
            if engine is None:
                raise ProtocolError('SDK authentication has not completed')
            if self.service.native_auth is not None:
                self.service.authorize_udp(peer)
            if peer[0] != '127.0.0.1':
                raise ProtocolError('non-loopback UDP peer')
            ident, session, source, dest, body, extra = sdp_header(data)
            if ident == 1001:
                if extra or len(body) < 141:
                    raise ProtocolError('SDP2P login length')
                n = struct.unpack_from('>H', body)[0]
                if n > 20 or len(body) != 141 + n:
                    raise ProtocolError('SDP2P login text length')
                old = engine.p2p
                if old and engine.p2p_alive() and old['peer'] != peer:
                    raise ProtocolError('one P2P endpoint per adapter session')
                if old and engine.p2p_alive():
                    player, session = old['player'], old['session']
                else:
                    if engine.hub is not None:
                        player = session = engine.hub.allocate_p2p_id()
                    else:
                        player = session = self.next_id
                        self.next_id += 1
                    engine.register_p2p(player, session, peer)
                out = sdp_reply(1002, session, player, socket.inet_aton(peer[0]), peer[1])
            elif ident in (1013, 1012):
                lease = engine.p2p
                if (len(body) != 4 or extra or not engine.p2p_alive() or
                    lease['peer'] != peer or lease['session'] != session or lease['player'] != source):
                    raise ProtocolError('SDP2P lease mismatch')
                if ident == 1012:
                    engine.p2p = None
                    return
                lease['expires'] = engine.clock() + 60
                out = sdp_reply(1014, session, source, socket.inet_aton(peer[0]), peer[1])
            else:
                self.service.event('unsupported_udp', id=ident, size=len(data))
                return
            self.transport.sendto(out, peer)
            self.service.event('udp_control', id=ident, size=len(data))
        except (ProtocolError, ValueError, struct.error) as exc:
            self.service.event('udp_rejected', reason=str(exc))


class Service:
    def __init__(self, store, ready, *, host='127.0.0.1', login_port=8000,
                 game_port=8001, p2p_port=8001, offline_adapter=False,
                 event_sink=None, idle_seconds=30, training_rewards=False, account_uid=1001, hub=None,
                 native_auth=None, native_region_id=1, map_catalog=None, query_probe=False):
        if host != '127.0.0.1' or not ipaddress.ip_address(host).is_loopback:
            raise ValueError('Only IPv4 loopback is supported')
        if bool(offline_adapter)==bool(native_auth):
            raise ValueError('Choose explicit insecure offline adapter OR authenticated native bridge')
        if native_auth is not None and (native_auth.native_verifier is None or hub is not None):
            raise ValueError('Authenticated native bridge requires its verifier and a single active client per region')
        self.store, self.ready, self.host = store, ready, host
        self.login_port, self.game_port, self.p2p_port = login_port, game_port, p2p_port
        self.account_uid=account_uid
        self.training_rewards=training_rewards
        self.native_auth=native_auth
        self.native_region_id=native_region_id
        self.map_catalog=map_catalog
        self.query_probe=bool(query_probe)
        self.query_probe_count=0
        self.native_udp_peers={}
        self.engine = None if native_auth else Engine(store, game_port, p2p_port, training_rewards=training_rewards,
                                                       account_uid=account_uid,hub=hub,map_catalog=map_catalog)
        self.event_sink = event_sink or (lambda row: None)
        self.idle_seconds = idle_seconds
        self.servers, self.tasks, self.writers = [], set(), set()
        self.udp = None
        self.sequence = 0

    def event(self, event, **fields):
        # Never log request bodies, credentials, account/device strings.
        try:
            self.event_sink(dict(event=event, timestamp=time.time(), **fields))
        except OSError:
            pass  # Observability failure must not kill a gameplay session.

    def record_menu_query(self, c, message):
        """Opt-in bounded menu metadata; no authentication/chat/raw payloads."""
        if not self.query_probe or not message.id or c.phase not in (Phase.LOBBY,Phase.ROOM):
            return
        if self.query_probe_count>=2048:
            if self.query_probe_count==2048:self.event('query_probe_limit',limit=2048)
            self.query_probe_count+=1
            return
        self.query_probe_count+=1
        fields={}
        policy='empty' if not message.payload else 'metadata_only'
        # Native5143 is the previously recovered4-byte bundle-content query.
        if message.id==5143 and len(message.payload)==4:
            fields={'item_id':struct.unpack('<I',message.payload)[0]}
            policy='decoded_whitelist'
        elif message.id==9070 and len(message.payload)==2:
            fields={'category_code':message.payload[0],'variant_code':message.payload[1]}
            policy='decoded_whitelist'
        elif message.id==20360 and len(message.payload)==12:
            fields={'self_query':struct.unpack_from('<Q',message.payload)[0]==c.uid,
                    'selector_u32':struct.unpack_from('<I',message.payload,8)[0]}
            policy='decoded_whitelist'
        self.event('menu_query',seq=self.query_probe_count,connection=c.number,
                   phase=c.phase.value,id=message.id,size=len(message.payload),
                   body_policy=policy,fields=fields)

    async def start(self):
        try:
            gs = await asyncio.start_server(self._game, self.host, self.game_port)
            self.servers.append(gs)
            self.game_port = gs.sockets[0].getsockname()[1]
            if self.engine:
                self.engine.game_port = self.game_port
            self.udp, _ = await asyncio.get_running_loop().create_datagram_endpoint(
                lambda: SdpProtocol(self), local_addr=(self.host, self.p2p_port))
            self.p2p_port = self.udp.get_extra_info('sockname')[1]
            if self.engine:
                self.engine.p2p_port = self.p2p_port
            ls = await asyncio.start_server(self._login, self.host, self.login_port)
            self.servers.append(ls)
            self.login_port = ls.sockets[0].getsockname()[1]
            self.event('listening', login_port=self.login_port, game_port=self.game_port,
                       p2p_port=self.p2p_port, authority='PROVISIONAL_LOCAL_SERVICE')
            return self
        except BaseException:
            await self.close()
            raise

    async def close(self):
        for server in self.servers:
            server.close()
        for server in self.servers:
            await server.wait_closed()
        self.servers.clear()
        if self.udp:
            self.udp.close()
            self.udp = None
        for writer in tuple(self.writers):
            writer.close()
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    def _track(self, writer):
        if len(self.writers) >= 16:
            writer.close()
            return False
        self.writers.add(writer)
        self.tasks.add(asyncio.current_task())
        return True

    async def _finish(self, writer):
        self.writers.discard(writer)
        self.tasks.discard(asyncio.current_task())
        writer.close()
        with contextlib.suppress(OSError, asyncio.TimeoutError):
            await asyncio.wait_for(writer.wait_closed(), 2)

    async def _login(self, reader, writer):
        if not self._track(writer):
            return
        try:
            flags, body = await asyncio.wait_for(read_login(reader), 30)
            if flags != 1:
                raise ProtocolError('offline SDK session request must use the qualified encrypted envelope')
            binding=None
            if self.native_auth is not None:
                identity=self.native_auth.native_verifier.tcp_peer(writer.get_extra_info('peername'),writer.get_extra_info('sockname'))
                binding=self.native_auth.native_binding(identity,self.native_region_id)
                if binding['stage']!='bound':
                    raise AuthError('native_sdk_replay')
                if self.engine and (self.engine.game is not None or self.engine.bootstrap is not None):
                    raise AuthError('native_client_already_active')
                self.account_uid=binding['uid']
                self.engine=Engine(self.store,self.game_port,self.p2p_port,account_uid=self.account_uid,
                                   training_rewards=self.training_rewards,map_catalog=self.map_catalog)
                self.native_udp_peers.clear()
                binding['stage']='sdk'
            # The SDK payload is NOT parsed as a password. Secure mode requires
            # a password-authenticated ticket bound to this kernel-owned process.
            account = self.store.snapshot(self.account_uid)[0]
            writer.write(encode_login(packets.login_ack(account, self.account_uid)))
            await asyncio.wait_for(writer.drain(), 2)
            flags, body = await asyncio.wait_for(read_login(reader), 30)
            if flags != 0 or struct.unpack_from('<H', body)[0] != 1011:
                raise ProtocolError('expected SDK server-list request')
            if binding is None:
                self.engine.grant_offline_adapter_session()
            else:
                self.engine.grant_authenticated_session()
                binding['stage']='directory'
            writer.write(encode_login(packets.login_directory(self.game_port)))
            await asyncio.wait_for(writer.drain(), 2)
            self.event('authenticated_sdk_grant' if binding is not None else 'offline_sdk_grant')
            # Detect EOF correctly; do not use stale Socket.Connected status.
            while await reader.read(4096):
                pass
        except (ProtocolError, AuthError, ValueError, asyncio.IncompleteReadError, asyncio.TimeoutError, OSError) as exc:
            self.event('login_closed', reason=type(exc).__name__)
        finally:
            await self._finish(writer)

    async def _game(self, reader, writer):
        if not self._track(writer):
            return
        self.sequence += 1
        c = Connection(self.sequence)
        engine=self.engine
        identity=None
        if engine is None:
            await self._finish(writer)
            return
        decoder, lock = GameDecoder(), asyncio.Lock()
        last_rx = time.monotonic()
        accepted_at=last_rx

        async def send(messages):
            async with lock:
                for message in messages:
                    writer.write(encode_game(message))
                    self.event('tx', connection=c.number, phase=c.phase.value,
                               id=message.id, size=len(message.payload))
                await asyncio.wait_for(writer.drain(), 2)

        async def pulse():
            heartbeat = 0
            while True:
                if c.uid==0 and time.monotonic()-accepted_at>10:
                    writer.close()
                    return
                if self.native_auth is not None and identity is not None:
                    try:
                        self.native_auth.native_binding(identity,self.native_region_id)
                    except AuthError:
                        writer.close()
                        return
                if engine.hub is not None:
                    engine.hub.expire()
                if c is engine.game and engine.delivery_failed:
                    self.event('slow_recipient_closed',connection=c.number)
                    writer.close()
                    return
                if time.monotonic() - last_rx > self.idle_seconds:
                    writer.close()
                    return
                replies = engine.profile_ready(c, self.ready())+engine.take_pending(c)
                if time.monotonic() >= heartbeat:
                    replies.append(Message(0))
                    heartbeat = time.monotonic() + 1
                if replies:
                    await send(replies)
                await asyncio.sleep(.1)

        pulse_task = asyncio.create_task(pulse())
        def pulse_finished(task):
            if not task.cancelled() and task.exception() is not None:
                self.event('pulse_failed', connection=c.number, reason=type(task.exception()).__name__)
                writer.close()
        pulse_task.add_done_callback(pulse_finished)
        rate_start, rate_count = time.monotonic(), 0
        try:
            while data := await reader.read(65536):
                for message in decoder.feed(data):
                    last_rx = time.monotonic()
                    if last_rx - rate_start >= 1:
                        rate_start, rate_count = last_rx, 0
                    rate_count += 1
                    if rate_count > 5000:
                        raise ProtocolError('message rate limit')
                    previous = c.phase
                    binding=None
                    if self.native_auth is not None and message.id:
                        if identity is None:
                            identity=self.native_auth.native_verifier.tcp_peer(writer.get_extra_info('peername'),writer.get_extra_info('sockname'))
                        binding=self.native_auth.native_binding(identity,self.native_region_id)
                        if binding['uid']!=engine.account_uid:
                            raise AuthError('native_account_mismatch')
                        if c.phase==Phase.CONNECTED:
                            wanted='directory' if message.id==1010 else 'bootstrap' if message.id==2010 else None
                            if wanted is None or binding['stage']!=wanted:
                                raise AuthError('native_handoff_rejected')
                    self.record_menu_query(c,message)
                    if message.id==3010 and len(message.payload)==81:
                        chosen,resolved=struct.unpack_from('<ii',message.payload,38)
                        self.event('room_create_request',connection=c.number,phase=previous.value,
                                   mode=message.payload[46],capacity=message.payload[37],
                                   chosen_map=chosen,suggested_map=resolved)
                    try:
                        out = engine.handle(c, message)
                    except packets.RoomRequestRejected as exc:
                        if message.id!=3010:
                            raise
                        # 3030 is the native exact-u16 rejection consumer.
                        # Keep the session; local reason is not a native code.
                        self.event('room_create_rejected',connection=c.number,phase=c.phase.value,reason=exc.code)
                        out=[packets.room_rejection(exc.code)]
                    if binding is not None:
                        if message.id==1010: binding['stage']='bootstrap'
                        if message.id==2010: binding['stage']='game'
                        if message.id==2250 and c is engine.game and c.phase==Phase.LOBBY:
                            binding['lobby_ready']=True
                    if engine.last_consume_event:
                        self.event(connection=c.number, **engine.last_consume_event)
                    if self.query_probe and engine.last_chat_event:
                        self.event(connection=c.number, **engine.last_chat_event)
                    unknown_count = engine.unknown[(previous.value, message.id, len(message.payload))]
                    if not unknown_count and len(engine.unknown) >= 4096:
                        unknown_count = engine.unknown[('overflow', -1, 0)]
                    if message.id and (unknown_count <= 1 or unknown_count & (unknown_count - 1) == 0):
                        self.event('rx', connection=c.number, phase=previous.value,
                                   id=message.id, size=len(message.payload))
                    if out:
                        await send(out)
                    if message.id==3010 and c.phase==Phase.ROOM and engine.room is not None:
                        fixed=engine.room.resolved_request or engine.room.request
                        selected,resolved=struct.unpack_from('<ii',fixed,38)
                        self.event('room_map_selected',connection=c.number,mode=fixed[46],selected_map=selected,resolved_map=resolved)
                    if message.id == 1010:
                        c.bootstrap_sent = True
            decoder.eof()
        except (ProtocolError, AuthError, ValueError, asyncio.IncompleteReadError, ConnectionError, OSError) as exc:
            self.event('game_closed', connection=c.number, reason=type(exc).__name__)
        finally:
            pulse_task.cancel()
            await asyncio.gather(pulse_task, return_exceptions=True)
            if (c is engine.game or
                    (c is engine.bootstrap and c.phase != Phase.HANDOFF)):
                invalidate = getattr(self.ready, 'invalidate', None)
                if invalidate:
                    invalidate()
                if self.native_auth is not None and identity is not None:
                    self.native_auth.release_native(identity)
            engine.disconnect(c)
            await self._finish(writer)

    def report(self):
        if self.engine is None:
            return dict(schema='kk-local-service-report-v1',native_client_verified=False,
                        scope='password-authenticated local bridge; no game session yet',unknown=[])
        return dict(schema='kk-local-service-report-v1',
                    native_client_verified=False,
                    scope='experimental shared rooms' if self.engine.hub else 'single-account local adapter',
                    map_admission=self.map_catalog.summary() if self.map_catalog else {'scope':'legacy water4-only fixture'},
                    decoded_not_implemented=dict(self.engine.layout_observations),
                    unknown=[dict(phase=k[0], id=k[1], size=k[2], count=n)
                             for k, n in sorted(self.engine.unknown.items())])

    def authorize_udp(self, peer):
        identity=self.native_udp_peers.get(peer)
        if identity is None:
            identity=self.native_auth.native_verifier.udp_peer(peer)
            if len(self.native_udp_peers)>=64:
                raise AuthError('native_udp_endpoint_limit')
            self.native_udp_peers[peer]=identity
        binding=self.native_auth.native_binding(identity,self.native_region_id)
        if binding['stage']!='game' or binding['uid']!=self.engine.account_uid:
            raise AuthError('native_udp_session_required')
