import asyncio
import base64
import importlib.util
import json
import math
import os
from pathlib import Path
import socket
import struct
import tempfile
import time
import unittest
from unittest.mock import patch

from server.kk_local.wire import (Message, ProtocolError, GameDecoder, blocks,
                                  encode_game, encode_login, read_login, sdp_header)
from server.kk_local.engine import Engine, Connection, Phase
from server.kk_local.store import Store
from server.kk_local.service import Service, ReadyFile
from server.kk_local.layouts import decode_battle, decode_known, BATTLE_LENGTHS
from server.kk_local import packets


def hello(ident=1010, uid=1001):
    p = bytearray(96)
    struct.pack_into('<Q', p, 0, uid)
    struct.pack_into('<I', p, 49, 594)
    return Message(ident, bytes(p))


def create_room():
    p = bytearray(81)
    p[:10] = b'Local room'
    p[37], p[46] = 4, 5
    struct.pack_into('<H', p, 47, 180)
    return Message(3010, bytes(p))


class WireTests(unittest.TestCase):
    def test_existing_reference_cross_compatibility(self):
        path = Path(__file__).resolve().parents[2] / 'docs/protocol/reference_transport.py'
        spec = importlib.util.spec_from_file_location('kk_reference_transport', path)
        reference = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(reference)
        for key in range(11):
            for size in (0, 1, 7, 8, 9, 53, 81, 245, 360, 836, 4097):
                payload = bytes(i % 256 for i in range(size))
                actual = encode_game(Message(3100, payload), key)
                self.assertEqual(actual, reference.encode_frame(3100, payload, key))
                self.assertEqual(reference.decode_frame(actual), (3100, payload))
                self.assertEqual(GameDecoder().feed(actual), [Message(3100, payload)])

    def test_canonical_heartbeat(self):
        self.assertEqual(encode_game(Message(0)).hex(),
                         'eeaa88881000000030304e617e31666430304e617e316664')

    def test_all_keys_lengths(self):
        for key in range(11):
            for n in (0, 1, 7, 8, 9, 37, 53, 81, 245, 360, 836):
                m = Message(3010, bytes(i % 256 for i in range(n)))
                self.assertEqual(GameDecoder().feed(encode_game(m, key)), [m])

    def test_every_split_and_glued_frames(self):
        a, b = Message(1010, bytes(96)), Message(1157)
        raw = encode_game(a) + encode_game(b)
        for cut in range(len(raw) + 1):
            d = GameDecoder()
            self.assertEqual(d.feed(raw[:cut]) + d.feed(raw[cut:]), [a, b])

    def test_bad_headers_rejected_before_body(self):
        for n in (0, 15, 17, 1024 * 1024):
            with self.assertRaises(ProtocolError):
                GameDecoder().feed(struct.pack('<HHI', 0xaaee, (n ^ 0xbbcc) & 0x88aa, n))
        for position in (0, 2):
            b = bytearray(encode_game(Message(1)))
            b[position] ^= 1
            with self.assertRaises(ProtocolError):
                GameDecoder().feed(b)

    def test_bad_inner_key_and_length(self):
        for key, length in ((11, 0), (0, 99)):
            inner = struct.pack('<IHI', 1, key, length) + bytes(6)
            raw = struct.pack('<HHI', 0xaaee, (16 ^ 0xbbcc) & 0x88aa, 16) + blocks(inner, 0)
            with self.assertRaises(ProtocolError):
                GameDecoder().feed(raw)

    def test_nonempty_heartbeat_rejected(self):
        with self.assertRaises(ProtocolError):
            GameDecoder().feed(encode_game(Message(0, b'x')))

    def test_truncated_stream_eof(self):
        d = GameDecoder()
        d.feed(encode_game(Message(1010, bytes(96)))[:-1])
        with self.assertRaises(ProtocolError):
            d.eof()

    def test_sdk_string_order(self):
        p = packets.login_ack('KKLocal', 1001).payload
        n = struct.unpack_from('>H', p, 1)[0]
        self.assertEqual(p[3:3 + n], b'KKLocal')
        at = 3 + n
        k = struct.unpack_from('>H', p, at)[0]
        self.assertEqual(p[at + 2:at + 2 + k], b'1001')

    def test_validated_start_vector(self):
        # Same bytes as the native A06/A07 53-byte start experiment.
        actual = packets.battle_start(1, 1001, 1)
        self.assertEqual(actual, base64.b64decode(
            'AQAAAAABAAAAAAAAAOkDAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAQAAAAEAAAA='))
        self.assertEqual(len(actual), 53)
        # Test non-overlapping offsets, not only an encode/decode round-trip.
        self.assertEqual(actual[0:9], b'\1\0\0\0\0\1\0\0\0')
        self.assertEqual(actual[13:17], b'\xe9\3\0\0')
        self.assertEqual(actual[45:], b'\1\0\0\0\1\0\0\0')


class StoreTests(unittest.TestCase):
    def test_grant_preserves_equipment_training_and_is_once_only(self):
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/'inventory.db')
            s=Store(path); s.seed_local(); s.training(1001,1000,start=True)
            profile=s.snapshot(1001)[2]
            plan=dict(schema='kk-local-inventory-grant-v1',uid=1001,grant_id='test-full',
                      rows=[[253030,25,1],[253033,25,1],[643001,64,100]])
            self.assertEqual(s.apply_grant(plan),2)
            snapshot=s.snapshot(1001)
            self.assertEqual(snapshot[2],profile)
            self.assertEqual(struct.unpack_from('<H',snapshot[3],6*68+17)[0],8)
            self.assertEqual(struct.unpack_from('<H',snapshot[3],8*68+23)[0],100)
            self.assertEqual(s.apply_grant(plan),0)
            self.assertEqual(s.snapshot(1001),snapshot)
            invalid=dict(plan,grant_id='bad',rows=[[1,25,1],[2,30,100]])
            with self.assertRaises(ValueError):
                s.apply_grant(invalid)
            self.assertEqual(s.snapshot(1001),snapshot)
            s.close(); s=Store(path)
            self.assertEqual(s.apply_grant(plan),0)
            self.assertEqual(s.training(1001,1120),(2,True))
            self.assertEqual(s.snapshot(1001),snapshot)
            s.close()
    def test_training_persists_without_restarting_or_granting_rewards(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / 'training.db')
            s = Store(path); s.seed_local()
            original = s.snapshot(1001)
            self.assertEqual(s.training(1001, 1000), (0, False))
            self.assertEqual(s.training(1001, 1000, start=True), (0, True))
            self.assertEqual(s.training(1001, 1125, start=True), (2, True))
            s.close()
            s = Store(path)
            self.assertEqual(s.training(1001, 1180), (3, True))
            self.assertEqual(s.training(1001, 999), (0, True))
            self.assertEqual(s.snapshot(1001), original)
            with self.assertRaises(ValueError):
                s.training(9999, 1180, start=True)
            s.close()
    def test_restart_seed_idempotence_and_opaque_preservation(self):
        with tempfile.TemporaryDirectory() as d:
            path = str(Path(d) / 'test.db')
            s = Store(path)
            s.seed_local()
            s.seed_local()
            _, _, p, inv = s.snapshot(1001)
            self.assertEqual(len(inv), 476)
            self.assertEqual(p[4:11], b'KKLocal')
            self.assertEqual(p[310:317], bytes(7))
            blob = bytearray(p)
            blob[200] = 123
            s.db.execute('UPDATE accounts SET profile=?', (bytes(blob),))
            s.db.commit()
            s.set_nickname(1001, '测试')
            serial = s.next_battle()
            s.close()
            s = Store(path)
            s.seed_local()
            self.assertEqual(s.snapshot(1001)[1], '测试')
            self.assertEqual(s.snapshot(1001)[2][200], 123)
            self.assertEqual(s.snapshot(1001)[3], inv)
            self.assertEqual(s.next_battle(), serial + 1)
            with self.assertRaises(ValueError):
                s.set_nickname(1001, 'x' * 21)
            s.close()


class EngineTests(unittest.TestCase):
    def setUp(self):
        self.s = Store(':memory:')
        self.s.seed_local()
        self.now = 100
        self.e = Engine(self.s, clock=lambda: self.now)
        self.c = Connection(1)

    def tearDown(self):
        self.s.close()

    def bootstrap(self):
        self.e.grant_offline_adapter_session()
        out = self.e.handle(self.c, hello())
        self.assertEqual([m.id for m in out], [1131, 1020, 1120, 7080, 7070])
        self.assertEqual(struct.unpack_from('<I', out[0].payload, 20)[0], 1)
        self.assertEqual(self.e.profile_ready(self.c, True), [])
        self.c.bootstrap_sent = True
        self.assertEqual(self.e.profile_ready(self.c, False), [])
        self.assertEqual(self.e.profile_ready(self.c, True)[0].id, 1151)
        self.assertEqual(self.e.profile_ready(self.c, True), [])
        out = self.e.handle(self.c, Message(3320, struct.pack('<I', 1)))
        self.assertEqual([m.id for m in out], [3330, 1201])

    def lobby(self):
        self.bootstrap()
        self.e.disconnect(self.c)  # Real client closes the old stream first.
        self.g = Connection(2)
        self.assertEqual([m.id for m in self.e.handle(self.g, hello(2010))], [2030])
        self.e.register_p2p(1001, 1001, ('127.0.0.1', 50000))
        self.e.handle(self.g, Message(1156, struct.pack('<QI', 1001, 1001)))

    def test_complete_order_and_idempotence(self):
        self.lobby()
        out = self.e.handle(self.g, create_room())
        self.assertEqual([m.id for m in out], [3100, 3160])
        self.assertEqual(len(out[0].payload), 245)
        self.assertEqual(self.e.handle(self.g, create_room()), [])
        self.assertEqual([m.id for m in self.e.handle(self.g, Message(4030))], [4050, 4080])
        self.assertEqual(self.e.handle(self.g, Message(4030)), [])
        self.assertEqual([m.id for m in self.e.handle(self.g, Message(4160))], [4170, 4180])
        self.assertEqual(self.e.handle(self.g, Message(4160)), [])
        ready = Message(8040, struct.pack('<HQI', 1, 1001, 0))
        self.assertEqual([m.id for m in self.e.handle(self.g, ready)], [8070])
        self.assertEqual(self.e.handle(self.g, ready), [])
        self.assertEqual(self.g.phase, Phase.BATTLE)
        self.assertEqual(self.e.handle(self.g, Message(3110)), [Message(3115)])
        self.assertIsNone(self.e.room)
        self.assertTrue(self.e.p2p_alive())
        self.assertEqual(self.g.phase, Phase.LOBBY)
        self.assertEqual(self.e.handle(self.g, Message(3110)), [])
        self.assertEqual([m.id for m in self.e.handle(self.g, create_room())], [3100,3160])

    def test_prebattle_exit_and_invalid_body(self):
        self.lobby()
        self.e.handle(self.g, create_room())
        with self.assertRaises(ProtocolError):
            self.e.handle(self.g, Message(3110, b'x'))
        self.assertIsNotNone(self.e.room)
        self.assertEqual(self.e.handle(self.g, Message(3110)), [Message(3115)])
        self.assertIsNone(self.e.room)
        self.assertEqual(self.g.phase,Phase.LOBBY)
        self.assertEqual([m.id for m in self.e.handle(self.g,create_room())],[3100,3160])

    def test_no_auth_grant_and_zero_uid(self):
        with self.assertRaises(ProtocolError):
            self.e.handle(self.c, hello())
        self.e.grant_offline_adapter_session()
        with self.assertRaises(ProtocolError):
            self.e.handle(self.c, hello(uid=0))

    def test_early_and_wrong_phase_messages(self):
        self.lobby()
        for m in (Message(4160), Message(8040, bytes(14)), Message(4030)):
            with self.assertRaises(ProtocolError):
                self.e.handle(self.g, m)

    def test_expired_handoff(self):
        self.bootstrap()
        self.e.disconnect(self.c)
        self.now += 121
        with self.assertRaises(ProtocolError):
            self.e.handle(Connection(2), hello(2010))

    def test_invalid_p2p_identity_and_expiry(self):
        self.lobby()
        with self.assertRaises(ProtocolError):
            self.e.handle(self.g, Message(1156, struct.pack('<QI', 2, 1001)))
        self.now += 61
        with self.assertRaises(ProtocolError):
            self.e.handle(self.g, create_room())

    def test_unsupported_equipment_returns_native_error_without_mutation(self):
        self.lobby()
        before = self.s.snapshot(1001)
        self.assertEqual(self.e.handle(self.g, Message(0x820, struct.pack('<IIQ', 0x100006, 99, 123))),
                         [Message(2100, struct.pack('<H', 38))])
        self.assertEqual(self.s.snapshot(1001), before)
        self.assertEqual(self.e.layout_observations[0x820], 1)

    def test_equipment_snapshot_and_native_record_notification(self):
        self.lobby()
        self.s.apply_grant(dict(schema='kk-local-inventory-grant-v1',uid=1001,
                               grant_id='weapon-test',rows=[[253030,25,1],[253033,25,1]]))
        records=self.s.snapshot(1001)[3]
        target=next(struct.unpack_from('<I',records,i)[0] for i in range(0,len(records),68)
                    if struct.unpack_from('<I',records,i+5)[0]==253033)
        request=struct.pack('<IIQ',target,8,0x1122334455667788)
        out=self.e.handle(self.g,Message(2080,request))
        self.assertEqual([m.id for m in out],[1120,2090])
        self.assertEqual(len(out[1].payload),84)
        self.assertEqual(out[1].payload[:16],request)
        self.assertEqual(struct.unpack_from('<H',out[1].payload,33)[0],8)
        self.assertEqual(sum(struct.unpack_from('<H',out[0].payload,i+17)[0]==8
                             for i in range(0,len(out[0].payload),68)),1)
        with self.assertRaises(ValueError):
            self.s.equip(2002,target,8)

    def test_disconnected_old_stream_does_not_remove_current_gs(self):
        self.bootstrap()
        g = Connection(2)
        self.e.handle(g, hello(2010))
        self.e.disconnect(self.c)
        self.assertIs(self.e.game, g)

    def test_unknown_telemetry_is_bounded(self):
        self.lobby()
        for ident in range(100000, 105000):
            self.e.handle(self.g, Message(ident))
        self.assertEqual(len(self.e.unknown), 4097)
        self.assertEqual(self.e.unknown[('overflow', -1, 0)], 904)

    def test_mingxia_query_start_identity_and_claim_boundary(self):
        self.lobby()
        self.e.wall_clock = lambda: self.now
        query = Message(21000, struct.pack('<Q', 1001))
        status = self.e.handle(self.g, query)[0]
        self.assertEqual((status.id, len(status.payload)), (21001, 56))
        self.assertEqual(struct.unpack_from('<Q', status.payload)[0], 1001)
        self.assertEqual(struct.unpack_from('<I', status.payload, 28)[0], 0)
        started = self.e.handle(self.g, Message(21002))[0]
        self.assertEqual(started.id, 21005)
        self.assertEqual(struct.unpack_from('<I', started.payload, 28)[0], 1)
        self.now += 121
        again = self.e.handle(self.g, Message(21002))[0]
        self.assertEqual(struct.unpack_from('<I', again.payload, 20)[0], 2)
        before = self.s.snapshot(1001)
        self.assertEqual(self.e.handle(self.g, Message(21006)), [])
        self.assertEqual(self.s.snapshot(1001), before)
        for bad in (Message(21000, bytes(8)), Message(21000), Message(21002,b'x')):
            with self.assertRaises(ProtocolError):
                self.e.handle(self.g, bad)


class LayoutTests(unittest.TestCase):
    def test_battle_lengths_and_unknown_preservation(self):
        for ident, size in BATTLE_LENGTHS.items():
            b = bytearray(size)
            struct.pack_into('<IQ', b, 0, ident, 1001)
            self.assertEqual(decode_battle(bytes(b))['sender'], 1001)
            with self.assertRaises(ProtocolError):
                decode_battle(bytes(b[:-1]))
        b = bytearray(64)
        struct.pack_into('<I', b, 0, 0x20cd)
        self.assertIn('opaque_body', decode_battle(b))
        self.assertNotIn('winner', decode_battle(b))

    def test_nonfinite_and_count_validation(self):
        b = bytearray(108)
        struct.pack_into('<I', b, 0, 0x1fb8)
        struct.pack_into('<f', b, 51, math.inf)
        with self.assertRaises(ProtocolError):
            decode_battle(b)
        with self.assertRaises(ProtocolError):
            decode_known(0x848, struct.pack('<i', -1))
        self.assertEqual(decode_known(0x848, struct.pack('<iII', 2, 3, 4)), {'instances': (3, 4)})
        with self.assertRaises(ProtocolError):
            decode_known(0x47e, b'\x81\0' + bytes(66))

    def test_ready_file_must_be_fresh(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / 'ready.txt'
            p.write_text('0x10000000')
            os.utime(p, (1, 1))
            r = ReadyFile(p, start_time=100)
            self.assertFalse(r())
            p.write_text('0x10000000')
            os.utime(p, (101, 101))
            self.assertTrue(r())
            with patch('server.kk_local.service.time.time', return_value=200):
                r.invalidate()
            self.assertFalse(r())
            p.write_text('0x10000000')
            os.utime(p, (201, 201))
            self.assertTrue(r())
            p.write_text('0x0')
            self.assertFalse(r())


class NetworkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store = Store(':memory:')
        self.store.seed_local()
        self.ready = False
        self.events = []
        self.service = Service(self.store, lambda: self.ready, login_port=0,
                               game_port=0, p2p_port=0, offline_adapter=True,
                               event_sink=self.events.append)
        await self.service.start()
        self.connections = []

    async def asyncTearDown(self):
        for r, w in self.connections:
            w.close()
            await w.wait_closed()
        await self.service.close()
        self.store.close()

    async def connect(self, port):
        pair = await asyncio.open_connection('127.0.0.1', port)
        self.connections.append(pair)
        return pair

    async def game_receive(self, r):
        while True:
            h = await asyncio.wait_for(r.readexactly(8), 3)
            n = struct.unpack_from('<I', h, 4)[0]
            b = await asyncio.wait_for(r.readexactly(n), 3)
            m = GameDecoder().feed(h + b)[0]
            if m.id:
                return m

    async def test_live_tcp_and_udp_roundtrip(self):
        lr, lw = await self.connect(self.service.login_port)
        encrypted = bytearray(encode_login(Message(1001, b'opaque-local-test')))
        encrypted[6] = 1
        lw.write(encrypted[:5]); await lw.drain()
        lw.write(encrypted[5:]); await lw.drain()
        flags, ack = await asyncio.wait_for(read_login(lr), 3)
        self.assertEqual(struct.unpack_from('<H', ack)[0], 1002)
        lw.write(encode_login(Message(1011))); await lw.drain()
        flags, listing = await asyncio.wait_for(read_login(lr), 3)
        self.assertEqual(struct.unpack_from('<H', listing)[0], 1012)
        r, w = await self.connect(self.service.game_port)
        w.write(encode_game(hello())); await w.drain()
        self.assertEqual([(await self.game_receive(r)).id for _ in range(5)], [1131, 1020, 1120, 7080, 7070])
        self.ready = True
        self.assertEqual((await self.game_receive(r)).id, 1151)
        w.write(encode_game(Message(3320, struct.pack('<I', 1)))); await w.drain()
        self.assertEqual([(await self.game_receive(r)).id for _ in range(2)], [3330, 1201])
        w.close(); await w.wait_closed()
        await asyncio.sleep(.05)
        gr, gw = await self.connect(self.service.game_port)
        gw.write(encode_game(hello(2010))); await gw.drain()
        self.assertEqual((await self.game_receive(gr)).id, 2030)
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        udp.bind(('127.0.0.1', 0)); udp.setblocking(False)
        loop = asyncio.get_running_loop()
        try:
            request = struct.pack('<HHIIIIHBB', 1, 1001, 0, 0, 0, 0, 0, 0, 0) + bytes(141)
            await loop.sock_sendto(udp, request, ('127.0.0.1', self.service.p2p_port))
            ack, peer = await asyncio.wait_for(loop.sock_recvfrom(udp, 1000), 3)
            ident, session, src, player, body, _ = sdp_header(ack)
            self.assertEqual((ident, len(body)), (1002, 18))
            self.assertEqual(struct.unpack_from('>III', body), (0, player, session))
            keep = struct.pack('<HHIIIIHBB', 1, 1013, session, 0, player, 0, 0, 0, 0) + bytes(4)
            await loop.sock_sendto(udp, keep, peer)
            ack, _ = await asyncio.wait_for(loop.sock_recvfrom(udp, 1000), 3)
            self.assertEqual(sdp_header(ack)[0], 1014)
            gw.write(encode_game(Message(1156, struct.pack('<QI', 1001, player))) + encode_game(create_room()))
            await gw.drain()
            self.assertEqual([(await self.game_receive(gr)).id for _ in range(2)], [3100, 3160])
            for request, expected in ((Message(4030), [4050, 4080]), (Message(4160), [4170, 4180]),
                                      (Message(8040, struct.pack('<HQI', 1, 1001, 0)), [8070])):
                gw.write(encode_game(request)); await gw.drain()
                self.assertEqual([(await self.game_receive(gr)).id for _ in expected], expected)
            self.assertEqual(self.service.engine.game.phase, Phase.BATTLE)
        finally:
            udp.close()
        text = json.dumps(self.events)
        self.assertNotIn('opaque-local-test', text)
        self.assertNotIn('payload', text)

    async def test_bad_client_does_not_stop_listener(self):
        r, w = await self.connect(self.service.game_port)
        w.write(b'bad-head'); await w.drain()
        tail = await asyncio.wait_for(r.read(), 2)
        self.assertTrue(all(m.id == 0 for m in GameDecoder().feed(tail)))
        r2, w2 = await self.connect(self.service.game_port)
        self.assertTrue(self.service.servers[0].is_serving())

    async def test_network_boundary_explicit(self):
        with self.assertRaises(ValueError):
            Service(self.store, lambda: True, host='0.0.0.0', offline_adapter=True)
        with self.assertRaises(ValueError):
            Service(self.store, lambda: True)

    async def test_log_write_failure_does_not_stop_session(self):
        def unavailable(row):
            raise OSError('disk unavailable')
        self.service.event_sink = unavailable
        self.service.engine.grant_offline_adapter_session()
        r, w = await self.connect(self.service.game_port)
        w.write(encode_game(hello()))
        await w.drain()
        self.assertEqual([(await self.game_receive(r)).id for _ in range(5)],
                         [1131, 1020, 1120, 7080, 7070])

    async def test_disconnected_client_invalidates_readiness(self):
        invalidated = asyncio.Event()
        class Readiness:
            def __call__(self):
                return False
            def invalidate(self):
                invalidated.set()
        self.service.ready = Readiness()
        self.service.engine.grant_offline_adapter_session()
        r, w = await self.connect(self.service.game_port)
        w.write(encode_game(hello()))
        await w.drain()
        for _ in range(5):
            await self.game_receive(r)
        w.close()
        await w.wait_closed()
        await asyncio.wait_for(invalidated.wait(), 2)


if __name__ == '__main__':
    unittest.main()
