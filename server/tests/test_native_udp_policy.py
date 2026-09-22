import unittest
from types import SimpleNamespace
from server.kk_local.native_udp_policy import DatagramPolicy,IngressBudget,RejectionSummary
from server.kk_local.native_crypto import NativeProtection,Records,UDP,RecordError

class UdpPolicyTests(unittest.TestCase):
    def setUp(self):self.now=100.;self.clock=lambda:self.now
    def test_bad_unknown_flood_does_not_take_bound_budget(self):
        budget=IngressBudget(clock=self.clock)
        self.assertEqual(sum(budget.allow(('192.0.2.1',i)) for i in range(4096)),128)
        self.assertTrue(budget.allow(('192.0.2.2',6000),b'owner'))
        sid=b'1'*16;key=b'2'*32;g=SimpleNamespace(transport_id=sid,transport_key=key,transport_udp=None)
        protection=NativeProtection(SimpleNamespace(grants={1:g},validate=lambda g:g))
        for _ in range(4096):
            with self.assertRaises(RecordError):protection.datagram(b'bad')
        wire=Records(key,sid,bytes(16),UDP,server=False).seal(b'valid')
        self.assertEqual(protection.datagram(wire)[1],b'valid')
    def test_shared_nat_has_independent_bound_owners(self):
        budget=IngressBudget(clock=self.clock)
        for _ in range(800):self.assertTrue(budget.allow(('192.0.2.1',1),b'a'))
        self.assertFalse(budget.allow(('192.0.2.1',1),b'a'))
        self.assertTrue(budget.allow(('192.0.2.1',2),b'b'))
        self.now+=.5
        self.assertEqual(sum(budget.allow(('192.0.2.1',1),b'a') for _ in range(300)),200)
    def test_unknown_global_bound_and_lru_expiry(self):
        p=DatagramPolicy(source_limit=4,source_idle=60)
        b=IngressBudget(policy=p,clock=self.clock)
        self.assertEqual(sum(b.allow((f'192.0.2.{i}',1)) for i in range(600)),512)
        self.assertLessEqual(len(b.sources),4)
        self.now+=61;b.allow(('198.51.100.1',1))
        self.assertEqual(list(b.sources),['198.51.100.1'])
    def test_summary_is_bounded_timed_and_not_per_packet(self):
        out=[];summary=RejectionSummary(out.append,clock=self.clock)
        for _ in range(4096):summary.add('invalid_record')
        self.assertEqual(out,[]);summary.flush();self.assertEqual(out,[])
        self.now+=10;summary.flush()
        self.assertEqual(len(out),1);self.assertEqual(out[0]['counts'],{'invalid_record':4096})
        summary.add('do-not-log-secret');self.now+=10;summary.flush()
        self.assertNotIn('do-not-log-secret',str(out));self.assertEqual(out[-1]['counts'],{'other':1})
    def test_policy_and_backward_clock(self):
        with self.assertRaises(ValueError):DatagramPolicy(unknown_rate=0)
        b=IngressBudget(clock=self.clock)
        for _ in range(128):self.assertTrue(b.allow(('192.0.2.1',1)))
        self.now-=1;self.assertFalse(b.allow(('192.0.2.1',1)))
        self.now+=2;self.assertTrue(b.allow(('192.0.2.1',1)))
