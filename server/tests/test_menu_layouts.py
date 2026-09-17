import struct
import unittest
from server.kk_local.menu_layouts import decode_menu_request as request, decode_menu_response as response
from server.kk_local.layouts import decode_known
from server.kk_local.wire import ProtocolError


class MenuLayoutsTests(unittest.TestCase):
    def test_player_directory_is_not_an_inventory_record(self):
        self.assertEqual(request(2250,struct.pack('<II',2,10)),{'value_0':2,'selector':10})
        raw=bytearray(76);struct.pack_into('<IIQ',raw,0,0,3,123)
        raw[16:21]=b'Actor'
        decoded=response(2270,raw)
        self.assertEqual(decoded['records'][0]['identity'],123)
        self.assertEqual(decoded['records'][0]['name_prefix'],b'Actor')
        self.assertEqual(len(decoded['records'][0]['raw']),68)
        self.assertEqual(response(2270,bytes(8))['records'],())
        for n in (0,7,9,75,77):
            with self.assertRaises(ProtocolError):response(2270,bytes(n))

    def test_owned_bytes_and_indirect_shop_length_guard(self):
        raw=bytearray(68);raw[0]=1
        decoded=response(2160,raw)
        raw[0]=9
        self.assertEqual(decoded['instance'],1)
        self.assertEqual(decoded['raw'][0],1)
        self.assertIsInstance(decoded['raw'],bytes)
        source=bytearray(16);source[12]=7
        decoded=request(1402,memoryview(source));source[12]=9
        self.assertEqual(decoded['opaque_tail'],b'\x07\0\0\0')
        for invalid in (0,100,'text',None):
            with self.assertRaises(ProtocolError):response(2160,invalid)
        from server.kk_local.wire import MAX_FRAME
        with self.assertRaises(ProtocolError):request(1402,bytearray(MAX_FRAME+1))
        for n in (0,107,109):
            with self.assertRaises(ProtocolError):response(9050,bytes(n))
        self.assertEqual(response(9050,bytes(108))['record'].raw,bytes(108))

    def test_purchase_family_offsets_no_private_text(self):
        for ident,n in ((9040,169),(9041,169),(9090,426),(9091,426)):
            raw=bytearray(b'Q'*n)
            struct.pack_into('<I',raw,0,109)
            struct.pack_into('<6I',raw,145,2501,100,2,30,7,9)
            fields=request(ident,bytes(raw))
            self.assertEqual(fields,dict(operation_code=109,catalog_key=2501,quoted_gold=100,
                quoted_credit=2,quoted_ticket=30,coupon_key=7,reference_key=9))
            self.assertEqual(decode_known(ident,bytes(raw)),fields)
            self.assertNotIn(b'Q',repr(fields).encode())
            with self.assertRaises(ProtocolError):request(ident,raw[:-1])

    def test_direction_and_exact_queries(self):
        self.assertIsNone(request(9050,bytes(108)))
        self.assertIsNone(response(9040,bytes(169)))
        for ident in (6001,6002,6003,6225,20563,20565):
            self.assertEqual(request(ident,b''),{})
            with self.assertRaises(ProtocolError):request(ident,b'x')
        self.assertEqual(request(20561,struct.pack('<I',10)),{'page':10})

    def test_rank_page_record_boundaries(self):
        raw=bytearray(878);struct.pack_into('<I',raw,0,10)
        for i in range(10):
            struct.pack_into('<III',raw,68+33*i,i+1,0,100+i)
            raw[80+33*i:101+33*i]=bytes([65+i])*21
            for j in range(3):struct.pack_into('<4I',raw,398+48*i+16*j,j+1,i,24,2)
        result=response(20562,raw)
        self.assertEqual(len(result['entries']),10)
        self.assertEqual(result['entries'][9]['consume'],109)
        self.assertEqual(result['entries'][9]['name_bytes'],b'J'*21)
        self.assertEqual(result['entries'][9]['rewards'][2],(3,9,24,2))
        struct.pack_into('<I',raw,0,11)
        with self.assertRaises(ProtocolError):response(20562,raw)
        self.assertEqual(response(20564,struct.pack('<i',-1)+bytes(64))['rank_zero_based'],-1)

    def test_quest_lists_and_errors(self):
        self.assertEqual(response(6226,struct.pack('<II',4,5)),{'quest_ids':(4,5)})
        self.assertEqual(len(response(6020,bytes(246))['records']),2)
        for ident,raw in ((6226,b'x'),(6020,bytes(124)),(20566,bytes(47)),(9060,b'x')):
            with self.assertRaises(ProtocolError):response(ident,raw)
        self.assertEqual(response(9060,b'\x82\0tail'),{'error_code':130,'opaque_tail':b'tail'})

    def test_quest_state_prefix_is_not_claimed_as_exact_length(self):
        for ident in (6031,6061,6091,6301,6032,6062,6092,6302,6033,6063,6093,6303):
            bias=4000 if ident%10==3 else 0
            fields=response(ident,b'\x34\x12\x03opaque')
            self.assertEqual(fields,dict(quest_id=0x1234,state=3,lookup_bias=bias,opaque_tail=b'opaque'))
            with self.assertRaises(ProtocolError):response(ident,b'xx')
        for ident in (6030,6060,6090):
            self.assertEqual(response(ident,bytes(12)+b'\x21\0')['quest_id'],33)
        self.assertEqual(response(6040,bytes(4)+b'\x21\0')['quest_id'],33)
        with self.assertRaises(ProtocolError):response(6040,bytes(5))
        record=bytearray(123);struct.pack_into('<H',record,4,1234)
        self.assertEqual(response(6020,record)['records'][0]['quest_id'],1234)
        for ident in (6041,6042,6043):
            raw=bytearray(282);struct.pack_into('<H',raw,153,25)
            self.assertEqual(response(ident,raw)['records'][1]['quest_id'],25)
            self.assertEqual(response(ident,raw)['records'][0]['lookup_bias'],4000 if ident==6043 else 0)
            with self.assertRaises(ProtocolError):response(ident,bytes(140))
        self.assertEqual(response(6223,b'\1'+bytes(4)+struct.pack('<I',22))['quest_id'],22)
        self.assertFalse(response(6223,b'\0'+struct.pack('<I',7))['success'])
        with self.assertRaises(ProtocolError):response(6223,b'\1'+bytes(4))

    def test_wallet_and_information_lists(self):
        for ident in (1240,1250):
            self.assertEqual(response(ident,struct.pack('<i',123)),{'balance':123})
            with self.assertRaises(ProtocolError):response(ident,bytes(5))
        for ident,stride in ((1310,339),(1410,124)):
            self.assertEqual(response(ident,b''),{'records':()})
            self.assertEqual(len(response(ident,bytes(2*stride))['records']),2)
            with self.assertRaises(ProtocolError):response(ident,bytes(stride-1))

    def test_information_actions_and_inventory_notifications(self):
        for ident in (1320,1340,2171):
            self.assertEqual(request(ident,struct.pack('<QI',123,456)),{'actor':123,'reference':456})
        self.assertEqual(request(1401,struct.pack('<QIII',123,456,7,0))['choice'],7)
        self.assertEqual(request(1402,struct.pack('<QII',123,456,0))['reference'],456)
        renew=bytearray(173);struct.pack_into('<I',renew,0,99);struct.pack_into('<I',renew,4,105)
        self.assertEqual(request(1420,renew)['purchase']['operation_code'],105)
        for ident in (2120,2121):
            self.assertEqual(response(ident,struct.pack('<III',2,10,11)),{'instances':(10,11)})
            with self.assertRaises(ProtocolError):response(ident,struct.pack('<I',0xffffffff))
        raw=bytearray(68);struct.pack_into('<I',raw,0,10);struct.pack_into('<I',raw,5,253033)
        struct.pack_into('<H',raw,23,99)
        for ident in (2160,4121):
            decoded=response(ident,raw)
            self.assertEqual(decoded['item_id'],253033)
            self.assertEqual(decoded['quantity'],99)
            self.assertEqual(decoded['quantity_mode'],'absolute')
            self.assertEqual(decoded['inventory_policy'],'upsert_instance')
            self.assertEqual(response(ident,raw),decoded)  # repeats do not add quantity
        self.assertEqual(response(2161,raw)['quantity'],99)
        self.assertEqual(response(2161,raw)['inventory_policy'],'replace_existing_instance')
        self.assertEqual(response(2162,struct.pack('<I',10)),{'instance':10})
        self.assertFalse(response(1350,b'\0')['success'])
        self.assertEqual(response(1350,b'\1'+struct.pack('<I',3))['reference'],3)


if __name__=='__main__':unittest.main()
