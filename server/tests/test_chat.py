import struct
import json
import unittest
from server.kk_local.chat import public_text,private_text,system_notice
from server.kk_local.engine import Engine,Connection,Phase
from server.kk_local.rooms import RoomHub
from server.kk_local.store import Store
from server.kk_local.wire import Message,ProtocolError
from server.tests.test_competitive_rooms import prepare_handoff
from server.tests.test_local_service import hello


def request(text):
    b=text.encode('gbk');p=bytearray(215);p[12]=len(b)+1;p[13:13+len(b)]=b
    return Message(5002,bytes(p))


class ChatTests(unittest.TestCase):
    def test_system_notice_native_bounds(self):
        for text in ('提示','x'*199):
            m=system_notice(text);raw=text.encode('gbk')
            self.assertEqual(m.id,20150);self.assertEqual(len(m.payload),215)
            self.assertEqual(m.payload[:12],bytes(12))
            n=m.payload[12];self.assertLess(n,201)
            self.assertEqual(m.payload[13:13+n-1],raw)
            self.assertEqual(m.payload[13+n],0)
        for text in ('','x'*200,'bad\ntext','\0','🙂',None):
            with self.assertRaises(ProtocolError):system_notice(text)

    def test_rejection_diagnostics_are_private_and_reset_per_message(self):
        s=Store(':memory:');s.seed_local();s.provision_local(1002,'Peer')
        try:
            e=Engine(s);e.game=Connection(1,Phase.LOBBY,1001)
            raw=bytearray(256);raw[29:33]=b'Peer';raw[50]=7;raw[55:61]=b'secret'
            msg=Message(5000,bytes(raw))
            def rejected(reason,message=msg):
                out=e.handle(e.game,message)
                self.assertTrue(all(m.id==20150 for m in out))
                self.assertEqual(e.last_chat_event,dict(event='chat_result',channel='private',
                                                       outcome='rejected',reason=reason))
                self.assertNotIn('secret',json.dumps(e.last_chat_event))
                self.assertNotIn('Peer',json.dumps(e.last_chat_event))
            rejected('routing_unavailable')
            hub=RoomHub();hub.attach(e);e.hub=hub
            rejected('recipient_unavailable')
            peer=Engine(s,account_uid=1002,hub=hub);peer.game=Connection(2,Phase.LOBBY,1002)
            peer.delivery_failed=True;rejected('recipient_queue_failed')
            peer.delivery_failed=False;peer.pending_bytes=2*1024*1024
            rejected('recipient_queue_failed')
            self.assertTrue(peer.delivery_failed)
            rejected('invalid_request',Message(5000,b'secret'))
            e.handle(e.game,Message(0));self.assertIsNone(e.last_chat_event)
            e.handle(Connection(3,Phase.LOBBY,1001),msg);self.assertIsNone(e.last_chat_event)
        finally:s.close()

    def test_private_chat_only_reaches_exact_online_recipient(self):
        s=Store(':memory:');s.seed_local();s.provision_local(1002,'Peer');s.provision_local(1003,'Other')
        try:
            hub=RoomHub();engines=[]
            for uid in (1001,1002,1003):
                e=Engine(s,account_uid=uid,hub=hub);e.game=Connection(uid,Phase.LOBBY,uid);engines.append(e)
            raw=bytearray(256);raw[29:33]=b'Peer';raw[50]=7;raw[55:61]=b'secret'
            self.assertEqual(private_text(bytes(raw)),('Peer',b'secret'))
            reply=engines[0].handle(engines[0].game,Message(5000,bytes(raw)))[0]
            self.assertEqual(engines[0].last_chat_event['reason'],'queued')
            self.assertEqual(reply.id,5001)
            self.assertEqual(engines[1].take_pending(engines[1].game),[reply])
            self.assertEqual(engines[2].take_pending(engines[2].game),[])
            raw[29:50]=b'Missing'.ljust(21,b'\0')
            self.assertEqual(engines[0].handle(engines[0].game,Message(5000,bytes(raw)))[0].id,20150)
        finally:s.close()

    def test_text_boundaries(self):
        self.assertEqual(public_text(request('测试').payload),'测试'.encode('gbk'))
        self.assertEqual(len(public_text(request('x'*199).payload)),199)
        for message in (request('x'*200),request(''),request('bad\nline')):
            with self.assertRaises(ProtocolError):public_text(message.payload)
        bad=bytearray(request('test').payload);bad[-1]=1
        with self.assertRaises(ProtocolError):public_text(bytes(bad))

    def test_authenticated_name_rate_limit_and_lobby_recipients(self):
        s=Store(':memory:');s.seed_local();s.provision_local(1002,'Peer');s.provision_local(1003,'Elsewhere')
        try:
            hub=RoomHub();now=[100.0];engines=[]
            for uid in (1001,1002,1003):
                e=Engine(s,account_uid=uid,hub=hub,clock=lambda:now[0]);c=Connection(uid,Phase.LOBBY,uid)
                e.game=c;engines.append(e)
            engines[2].game.phase=Phase.ROOM
            e=engines[0];p=bytearray(request('hello').payload);struct.pack_into('<Q',p,0,9999)
            reply=e.handle(e.game,Message(5002,bytes(p)))[0]
            self.assertEqual(reply.id,5003);self.assertEqual(len(reply.payload),256)
            self.assertEqual(struct.unpack_from('<Q',reply.payload)[0],1001)
            self.assertEqual(reply.payload[8:29].split(b'\0',1)[0],b'KKLocal')
            self.assertEqual(engines[1].take_pending(engines[1].game),[reply])
            self.assertEqual(engines[2].take_pending(engines[2].game),[])
            self.assertEqual(e.handle(e.game,request('again'))[0].id,20150)
            self.assertEqual(e.last_chat_event['reason'],'rate_limited')
            self.assertEqual(e.handle(e.game,request('again')),[])
            now[0]+=1
            self.assertEqual(len(e.handle(e.game,request('again'))),1)
            self.assertEqual(e.handle(Connection(99,Phase.LOBBY,1001),request('forged')),[])
        finally:s.close()


if __name__=='__main__':unittest.main()
