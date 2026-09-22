import struct
import unittest
from ipaddress import IPv4Address

from server.kk_local.lab_network import LabEndpoint
from server.kk_local.sdp_peer import SdpPeerRouter,connect_fields,peer_info,header,decode_relay
from server.kk_local.service import Service,SdpProtocol
from server.kk_local.store import Store
from server.kk_local.rooms import RoomHub
from server.kk_local.wire import Message,ProtocolError,sdp_header
from server.tests.test_shared_rooms import lobby
from server.tests.test_local_service import create_room


def connect_body(first,endpoint,last):
    ip,port=endpoint
    return struct.pack('>IIHI',first,int.from_bytes(IPv4Address(ip).packed,'little'),port,last)


class Transport:
    def __init__(self):self.sent=[]
    def sendto(self,data,peer):self.sent.append((data,peer))


class SdpPeerTests(unittest.TestCase):
    def setUp(self):
        self.now=0.0
        self.store=Store(':memory:');self.store.seed_local();self.store.provision_local(1002,'Second')
        self.hub=RoomHub();self.router=SdpPeerRouter(self.hub,clock=lambda:self.now)
        self.services=[];self.clients=[]
        for i,ip in enumerate(('10.0.0.10','10.0.0.11')):
            service=Service(self.store,lambda:True,host='10.0.0.10',offline_adapter=True,
                            account_uid=1001+i,hub=self.hub,sdp_router=self.router,
                            lab_endpoint=LabEndpoint('10.0.0.10',ip,'10.0.0.0/24'))
            service.udp=Transport();service.engine.clock=lambda:self.now
            c=lobby(service.engine,1+2*i)
            peer_id=service.engine.p2p['player']
            service.engine.register_p2p(peer_id,9001+i,(ip,40001+i))
            service.engine.handle(c,Message(1156,struct.pack('<QI',1001+i,peer_id)))
            self.services.append(service);self.clients.append(c)
        self.a,self.b=self.services
        self.a.engine.handle(self.clients[0],create_room())
        self.b.engine.handle(self.clients[1],Message(3070,struct.pack('<HB11s',1,0,b'')))
        self.a.engine.take_pending(self.clients[0])

    def tearDown(self):self.store.close()

    def packet(self,service,ident,body,extra=b'',flags=0):
        lease=service.engine.p2p
        h=bytearray(header(ident,lease['session'],lease['player'],0,flags));h[23]=len(extra)
        return bytes(h)+extra+body

    def request(self,ticket=123):
        endpoint=('10.0.0.10',50001)
        packet=self.packet(self.a,1003,connect_body(self.b.engine.p2p['player'],endpoint,ticket))
        self.assertTrue(self.router.handle(self.a,packet,endpoint))
        return packet,endpoint

    def test_wire_sizes_and_opposite_address_representations(self):
        body=connect_body(7,('10.0.0.10',50001),123)
        self.assertEqual(len(body),14)
        self.assertEqual(body[4:8],bytes((10,0,0,10))[::-1])
        # Non-palindromic address catches accidental double htonl.
        body=connect_body(7,('192.168.10.20',4567),123)
        self.assertEqual(body[4:8],bytes((20,10,168,192)))
        self.assertEqual(connect_fields(body),(7,'192.168.10.20',4567,123))
        reply=peer_info(7,('192.168.10.20',4567),123)
        self.assertEqual(len(reply),20)
        self.assertEqual(reply[4:8],bytes((192,168,10,20)))
        self.assertEqual(reply[10:14],bytes((192,168,10,20)))
        with self.assertRaises(ProtocolError):connect_fields(body+b'\0\0')

    def test_connect_forward_and_ack_use_recipient_session_and_observed_sockets(self):
        self.request()
        data,endpoint=self.b.udp.sent[-1]
        self.assertEqual(endpoint,('10.0.0.11',40002))
        self.assertEqual(sdp_header(data)[:4],(1005,9002,0,1002))
        self.assertEqual(data[24:],peer_info(1001,('10.0.0.10',50001),123))
        reply_endpoint=('10.0.0.11',50002)
        ack=self.packet(self.b,1006,connect_body(9002,reply_endpoint,1001))
        self.assertTrue(self.router.handle(self.b,ack,reply_endpoint))
        data,endpoint=self.a.udp.sent[-1]
        self.assertEqual(sdp_header(data)[:4],(1004,9001,0,1001))
        self.assertEqual(data[24:],peer_info(1002,reply_endpoint,123))
        self.assertEqual(endpoint,('10.0.0.10',50001))
        self.assertEqual(self.a.engine.room,self.b.engine.room)

    def test_pending_expiry_session_rollover_and_missing_request_rejected(self):
        ep=('10.0.0.11',50002)
        ack=self.packet(self.b,1006,connect_body(9002,ep,1001))
        with self.assertRaises(ProtocolError):self.router.handle(self.b,ack,ep)
        self.request();self.now=31
        with self.assertRaises(ProtocolError):self.router.handle(self.b,ack,ep)
        self.now=0;self.request();self.a.engine.p2p['session']=9999
        with self.assertRaises(ProtocolError):self.router.handle(self.b,ack,ep)
        self.assertEqual(self.a.udp.sent,[])

    def test_relay_preserves_opaque_reliable_payload_and_sets_target_header(self):
        for flags in (0,1):
            payload=b'\xaa\xbb\xcc\xdd opaque application data'
            data=self.packet(self.a,1008,payload,struct.pack('<I',1002),flags)
            self.assertEqual(decode_relay(data),(9001,1001,(1002,),flags,payload))
            self.router.handle(self.a,data,self.a.engine.p2p['peer'])
            out,peer=self.b.udp.sent[-1]
            self.assertEqual(sdp_header(out)[:4],(1009,9002,1001,1002))
            self.assertEqual(out[22:24],bytes((flags,0)))
            self.assertEqual(out[24:],payload)
            self.assertEqual(peer,self.b.engine.p2p['peer'])
        self.assertEqual(self.a.udp.sent,[])  # No sender echo / fabricated ping.

    def test_all_recipients_validated_before_any_relay(self):
        data=self.packet(self.a,1008,b'x',struct.pack('<II',1002,7777))
        with self.assertRaises(ProtocolError):self.router.handle(self.a,data,self.a.engine.p2p['peer'])
        self.assertEqual(self.b.udp.sent,[])
        self.hub.leave(self.b.engine,acknowledge=True)
        self.b.engine.handle(self.clients[1],create_room())
        with self.assertRaises(ProtocolError):
            self.router.handle(self.a,self.packet(self.a,1008,b'x',struct.pack('<I',1002)),self.a.engine.p2p['peer'])
        self.assertEqual(self.b.udp.sent,[])

    def test_spoofed_sender_socket_and_advertised_ip_rejected(self):
        original=self.packet(self.a,1008,b'x',struct.pack('<I',1002))
        for offset in (4,12):
            bad=bytearray(original);struct.pack_into('<I',bad,offset,9999)
            with self.assertRaises(ProtocolError):self.router.handle(self.a,bytes(bad),self.a.engine.p2p['peer'])
        with self.assertRaises(ProtocolError):self.router.handle(self.a,original,('10.0.0.10',50001))
        bad=self.packet(self.a,1003,connect_body(1002,('8.8.8.8',50001),123))
        with self.assertRaises(ProtocolError):self.router.handle(self.a,bad,('10.0.0.10',50001))
        self.assertEqual(self.b.udp.sent,[])

    def test_duplicates_are_bounded_and_rate_limited(self):
        for _ in range(200):self.request()
        self.assertEqual(len(self.router.pending),1)
        with self.assertRaisesRegex(ProtocolError,'rate limit'):self.request()
        self.now=1;self.request()

    def test_bad_relay_extra_recipients_and_flags_rejected(self):
        for targets,flags in ((b'',0),(b'x',0),(struct.pack('<I',0),0),
                              (struct.pack('<II',1002,1002),0),(struct.pack('<I',1002),2)):
            with self.assertRaises(ProtocolError):decode_relay(self.packet(self.a,1008,b'x',targets,flags))

    def test_datagram_protocol_delegates_to_router(self):
        protocol=SdpProtocol(self.a);protocol.connection_made(self.a.udp)
        data=self.packet(self.a,1008,b'payload',struct.pack('<I',1002))
        protocol.datagram_received(data,self.a.engine.p2p['peer'])
        self.assertEqual(sdp_header(self.b.udp.sent[-1][0])[0],1009)
        self.assertEqual(self.a.udp.sent,[])
