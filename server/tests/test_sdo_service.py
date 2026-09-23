import asyncio
import json
import struct
import unittest
from urllib.parse import urlencode
from cryptography.hazmat.primitives.asymmetric import padding,rsa
from server.kk_local.auth import AuthManager
from server.kk_local.native_identity import NativeIdentity
from server.kk_local.sdo_service import SdoHttpServer
from server.kk_local.sdo_password import checksum
from server.kk_local.sdo_outer import wrap_outer
from server.kk_local.store import Store


class Verifier:
    pair=(NativeIdentity(2,20,'sdk'),NativeIdentity(1,10,'game'))
    valid=True
    def process(self,pid):
        if pid!=1:raise ValueError()
        return self.pair[1]
    def peer(self,*args):self.recheck(self.pair);return self.pair
    def recheck(self,pair):
        if not self.valid or pair!=self.pair:raise ValueError()


class SdoServiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store=Store(':memory:');self.verifier=Verifier()
        self.auth=AuthManager(self.store,[dict(id=1,name='Local',host='127.0.0.1',game_port=18001)],native_verifier=self.verifier)
        self.user=await self.auth.register('TestUser','FakePassword!42')
        self.key=rsa.generate_private_key(public_exponent=3,key_size=1024);self.events=[]
        self.http=await SdoHttpServer(self.auth,self.key,self.verifier,port=0,event_sink=self.events.append).start()
    async def asyncTearDown(self):
        await self.http.close();self.auth.close();self.store.close()
    async def request(self,path,fields,extra=''):
        r,w=await asyncio.open_connection('127.0.0.1',self.http.port)
        w.write((f'GET {path}?{urlencode(fields)} HTTP/1.1\r\nHost: 127.0.0.1:{self.http.port}\r\n'+extra+'\r\n').encode());await w.drain()
        data=await asyncio.wait_for(r.read(),5);w.close();await w.wait_closed()
        return data.split(b'\r\n\r\n',1)[0],json.loads(data.split(b'\r\n\r\n',1)[1])
    async def credentials(self,password):
        _,reply=await self.request('/authen/getGuid.json',{'generateDynamicKey':'1'})
        dk=reply['data']['dynamicKey'];suffix=b'\0'*14
        encrypted=self.key.public_key().encrypt(dk.encode()+password,padding.PKCS1v15())
        packet=struct.pack('<HII',1,1,128)+encrypted+struct.pack('<III',0,checksum(dk.encode()+suffix,0),14)+suffix
        return dict(guid=reply['data']['guid'],checkCodeFlag='1',encryptFlag='1',accountDomain='1',inputUserId=wrap_outer(b'TestUser',dk),password=wrap_outer(packet,dk))
    async def test_full_http_password_to_game_binding(self):
        fields=await self.credentials(b'FakePassword!42')
        headers,reply=await self.request('/authen/staticLogin.json',fields)
        self.assertEqual(reply['return_code'],'0');self.assertIn(b'CASTGC=',headers)
        self.assertEqual(self.auth.native_binding(self.verifier.pair[1],1)['uid'],self.user['uid'])
        _,again=await self.request('/authen/staticLogin.json',fields)
        self.assertNotEqual(again['return_code'],'0')
        self.assertNotIn(fields['password'],repr(self.events))
    async def test_wrong_password_and_unverified_peer_fail(self):
        fields=await self.credentials(b'IncorrectPassword42')
        _,reply=await self.request('/authen/staticLogin.json',fields)
        self.assertNotEqual(reply['return_code'],'0');self.assertFalse(self.auth.native_bindings)
        self.verifier.valid=False
        _,reply=await self.request('/authen/getGuid.json',{'generateDynamicKey':'1'})
        self.assertNotEqual(reply['return_code'],'0')
    async def test_origin_duplicate_fields_and_unsupported_paths_fail(self):
        for path,fields,extra in [('/authen/getGuid.json',{'generateDynamicKey':'1'},'Origin: http://evil.invalid\r\n'),('/authen/getGuid.json',[('generateDynamicKey','1'),('generateDynamicKey','1')],''),('/anything',{},'')]:
            _,reply=await self.request(path,fields,extra)
            self.assertNotEqual(reply['return_code'],'0')
    async def test_legacy_relative_target_and_account_type_do_not_grant(self):
        _,reply=await self.request('authen/checkAccountType.json',{'inputUserId':'TestUser'})
        self.assertEqual(reply['return_code'],'0')
        self.assertEqual(reply['data']['hasPwdLoginRecord'],'1')
        self.assertFalse(self.auth.native_bindings)
        _,reply=await self.request('authen/getGuid.json',{'generateDynamicKey':'1'})
        self.assertEqual(reply['return_code'],'0')

    async def test_promotion_empty_only_for_bound_authenticated_session(self):
        path='authen/getPromotionInfo.json'
        _,reply=await self.request(path,{'tgt':'a'*64})
        self.assertNotEqual(reply['return_code'],'0')
        fields=await self.credentials(b'FakePassword!42')
        _,login=await self.request('/authen/staticLogin.json',fields)
        token=login['data']['sessionId']
        binding=dict(self.auth.native_binding(self.verifier.pair[1],1))
        for tgt in ('','b'*64):
            _,reply=await self.request(path,{'tgt':tgt})
            self.assertNotEqual(reply['return_code'],'0')
        headers,reply=await self.request(path,{'tgt':token})
        self.assertEqual(reply['return_code'],'0')
        self.assertEqual(reply['data'],{'promotionUrl':'','failReason':''})
        self.assertNotIn(b'Set-Cookie:',headers)
        self.assertEqual(self.auth.native_binding(self.verifier.pair[1],1),binding)
        self.assertNotIn(token,repr(self.events))
        self.auth.logout(token)
        _,reply=await self.request(path,{'tgt':token})
        self.assertNotEqual(reply['return_code'],'0')


if __name__=='__main__':unittest.main()
