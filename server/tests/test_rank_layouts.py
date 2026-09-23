import struct
import unittest
from server.kk_local.menu_layouts import decode_menu_request,decode_menu_response
from server.kk_local.wire import ProtocolError


class RankLayoutsTests(unittest.TestCase):
    def test_requests_and_unresolved_identity(self):
        self.assertEqual(decode_menu_request(2540,b'\x0c'),{'rank_category':12})
        data=bytearray(b'\x01abcdefgh')
        parsed=decode_menu_request(2560,data);data[1]=0
        self.assertEqual(parsed,{'rank_category':1,'opaque_identity':b'abcdefgh'})
        for ident,payload in ((2540,b''),(2540,b'ab'),(2560,b'12345678')):
            with self.assertRaises(ProtocolError):decode_menu_request(ident,payload)

    def test_all_record_fields_and_immutable_ownership(self):
        row=bytearray('甲'.encode('gbk').ljust(21,b'\0')+bytes([9])+struct.pack('<i',12345)+bytes([3]))
        parsed=decode_menu_response(2550,row)['records'][0];row[0]=0
        self.assertEqual((parsed['nickname'],parsed['rank_zero_based'],parsed['score'],parsed['rank_category']),('甲',9,12345,3))
        self.assertEqual(len(parsed['raw']),27)
        self.assertEqual(decode_menu_response(2550,b''),{'records':()})
        for data in (bytes(26),b'x'*27,b'\x81\0'+bytes(25)):
            with self.assertRaises(ProtocolError):decode_menu_response(2550,data)

    def test_self_rank_prefix_preserves_unknown_tail(self):
        self.assertEqual(decode_menu_response(2570,b'\x02'+struct.pack('<i',-1)+b'opaque'),
                         {'rank_category':2,'rank_zero_based':-1,'opaque_tail':b'opaque'})
        with self.assertRaises(ProtocolError):decode_menu_response(2570,bytes(4))
