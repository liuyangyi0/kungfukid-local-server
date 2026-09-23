import json
import unittest
from sdo_http_contract import response,guid_response,authentication_result


class HttpContractTests(unittest.TestCase):
    def test_guid_has_native_envelope_and_string_fields(self):
        value=json.loads(guid_response('SyntheticGuid0001','00001000020000300004'))
        self.assertEqual(value['return_code'],'0')
        self.assertEqual(value['data']['dynamicKey'],'00001000020000300004')
        self.assertNotIn('resultCode',value)

    def test_success_and_failure_are_distinct(self):
        good=json.loads(authentication_result(authenticated_uid=1002,ticket='SyntheticTicket01',session_id='SyntheticSession1'))
        bad=json.loads(authentication_result(authenticated_uid=None,ticket='',session_id=''))
        self.assertEqual(good['data']['sndaId'],'1002')
        self.assertLess(int(bad['return_code']),0)
        self.assertNotIn('ticket',bad['data'])

    def test_rejects_shadowing_and_nonflat_data(self):
        for data in ({'resultCode':'0'},{'nested':{}},{'value':None},{'value':'x\0y'}):
            with self.assertRaises(ValueError): response(0,data)

    def test_rejects_invalid_decisions_and_tokens(self):
        for uid,ticket,session in ((None,'SyntheticTicket01',''),(True,'SyntheticTicket01','SyntheticSession1'),(1002,'short','SyntheticSession1')):
            with self.assertRaises(ValueError): authentication_result(authenticated_uid=uid,ticket=ticket,session_id=session)


if __name__=='__main__': unittest.main()
