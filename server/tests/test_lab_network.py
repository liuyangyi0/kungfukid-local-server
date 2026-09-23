import asyncio
import json
from pathlib import Path
import struct
import tempfile
import unittest

from server.kk_local.lab_network import LabEndpoint
from server.kk_local.lab_server import lab_endpoints
from server.kk_local import packets
from server.kk_local.engine import Connection, Engine, Phase
from server.kk_local.rooms import RoomHub
from server.kk_local.service import Service, SdpProtocol
from server.kk_local.store import Store
from server.kk_local.wire import Message, sdp_reply


class LabNetworkTests(unittest.TestCase):
    def test_small_private_network_and_exact_peer_required(self):
        endpoint = LabEndpoint('192.168.239.1', '192.168.239.20', '192.168.239.0/24')
        self.assertTrue(endpoint.accepts(('192.168.239.20', 1234)))
        # The coordinator may run alongside P1 inside VM1; P2 remains distinct.
        self.assertTrue(LabEndpoint('192.168.239.20', '192.168.239.20', '192.168.239.0/24').accepts(('192.168.239.20', 1234)))
        for peer in (None, ('127.0.0.1', 1234), ('192.168.239.21', 1234)):
            self.assertFalse(endpoint.accepts(peer))
        for host, peer, subnet in (
                ('0.0.0.0', '192.168.239.20', '192.168.239.0/24'),
                ('192.168.239.1', '192.168.239.255', '192.168.239.0/24'),
                ('192.168.239.1', '192.168.240.20', '192.168.239.0/24'),
                ('8.8.8.1', '8.8.8.2', '8.8.8.0/24'),
                ('192.168.1.1', '192.168.1.2', '192.168.0.0/16'),
                ('localhost', '192.168.239.20', '192.168.239.0/24')):
            with self.subTest(host=host, peer=peer, subnet=subnet), self.assertRaises(ValueError):
                LabEndpoint(host, peer, subnet)

    def test_default_service_stays_loopback_only(self):
        store = Store(':memory:'); store.seed_local()
        try:
            with self.assertRaises(ValueError):
                Service(store, lambda: True, offline_adapter=True, host='192.168.239.1')
            endpoint = LabEndpoint('192.168.239.1', '192.168.239.20', '192.168.239.0/24')
            with self.assertRaises(ValueError):
                Service(store, lambda: True, offline_adapter=True, host=endpoint.host, lab_endpoint=endpoint)
            svc = Service(store, lambda: True, offline_adapter=True, host=endpoint.host,
                          lab_endpoint=endpoint, hub=RoomHub())
            self.assertTrue(svc.accepts_peer((endpoint.peer, 1234)))
            self.assertFalse(svc.accepts_peer(('192.168.239.21', 1234)))
            self.assertEqual(svc.engine.advertised_host, endpoint.host)
        finally:
            store.close()

    def test_directory_addresses_are_not_remote_loopback(self):
        host = '192.168.239.1'
        self.assertEqual(packets.login_directory(19001, host).payload[6:10], bytes((1, 239, 168, 192)))
        endpoint = packets.catalog(19001, host)[0].payload
        self.assertEqual(struct.unpack_from('<H', endpoint, 4)[0], 19001)
        self.assertEqual(endpoint[6:26].rstrip(b'\0'), host.encode())
        lobby = packets.lobby_context(19001, host).payload
        self.assertEqual(lobby[4:24].rstrip(b'\0'), host.encode())
        self.assertEqual(struct.unpack_from('<H', lobby, 24)[0], 19001)
        self.assertEqual(packets.login_directory(8001).payload[6:10], b'\x01\x00\x00\x7f')

    def test_engine_queries_use_qualified_advertised_endpoint(self):
        store = Store(':memory:'); store.seed_local()
        try:
            engine = Engine(store, advertised_host='192.168.239.1')
            client = Connection(1, Phase.LOBBY, 1001); engine.game = client
            self.assertEqual(engine.handle(client, Message(1157))[0].payload[6:26].rstrip(b'\0'), b'192.168.239.1')
        finally:
            store.close()

    def test_config_requires_two_distinct_peers_and_ready_receipts(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory); path = root / 'lab.json'
            rows = [dict(uid=1001+i, account=f'Player{i}', nickname=f'Player{i}',
                         login_port=19000+i*10, game_port=19001+i*10, p2p_port=19001+i*10,
                         role_ready_file=str(root / f'ready{i}')) for i in range(2)]
            config = dict(schema='kk-offline-account-endpoints-v1', accounts=rows,
                          lab_network=dict(host='192.168.239.1', subnet='192.168.239.0/24',
                                           peers={'1001':'192.168.239.20', '1002':'192.168.239.21'}))
            path.write_text(json.dumps(config), encoding='utf-8')
            self.assertEqual(len(lab_endpoints(path)), 2)
            config['lab_network']['peers']['1002'] = '192.168.239.20'
            path.write_text(json.dumps(config), encoding='utf-8')
            with self.assertRaises(ValueError): lab_endpoints(path)


class LabPeerRejectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_wrong_peer_rejected_before_login_or_udp_parsing(self):
        store = Store(':memory:'); store.seed_local()
        rows = []
        endpoint = LabEndpoint('192.168.239.1', '192.168.239.20', '192.168.239.0/24')
        svc = Service(store, lambda: True, offline_adapter=True, host=endpoint.host,
                      lab_endpoint=endpoint, hub=RoomHub(), event_sink=rows.append)
        class Writer:
            closed = False
            def get_extra_info(self, key): return ('192.168.239.21', 1234)
            def close(self): self.closed = True
        writer = Writer()
        try:
            self.assertFalse(svc._track(writer))
            self.assertTrue(writer.closed)
            SdpProtocol(svc).datagram_received(b'not a packet', ('192.168.239.21', 1234))
            self.assertEqual([row['event'] for row in rows], ['peer_rejected', 'udp_rejected'])
            self.assertEqual(rows[-1]['reason'], 'unqualified UDP peer')
            self.assertIsNone(svc.engine.p2p)
        finally:
            store.close()
