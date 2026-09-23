import asyncio
import json
from pathlib import Path
import struct
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from cryptography.hazmat.primitives.asymmetric import padding,rsa
from server.kk_local.auth import AuthManager,AuthError
from server.kk_local.store import Store
from sdo_password_bridge import PasswordBridge,checksum,decode_inner
from sdo_outer_password_codec import wrap_outer


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.store=Store(':memory:')
        self.auth=AuthManager(self.store,[dict(id=1,name='Local',host='127.0.0.1',game_port=8001)])
        self.user=await self.auth.register('SyntheticUser','FakePassword!42')
        self.key=rsa.generate_private_key(public_exponent=3,key_size=1024)
        self.now=100.;self.bridge=PasswordBridge(self.auth,self.key,clock=lambda:self.now)

    async def asyncTearDown(self):
        self.auth.close();self.store.close()

    def request(self,*,password=b'FakePassword!42',owner=('game',123),selector=0):
        issued=self.bridge.issue(owner);dk=issued['dynamicKey'];suffix=b'\0'*14
        encrypted=self.key.public_key().encrypt(dk.encode()+password,padding.PKCS1v15())
        packet=struct.pack('<HII',1,1,128)+encrypted+struct.pack('<III',selector,checksum(dk.encode()+suffix,selector),14)+suffix
        return dict(client_identity=owner,guid=issued['guid'],account_cipher=wrap_outer(b'SyntheticUser',dk),password_cipher=wrap_outer(packet,dk)),packet,dk

    async def test_qualified_packet_to_real_scrypt_login(self):
        request,_,_=self.request()
        result=await self.bridge.authenticate(**request)
        self.assertEqual(result['uid'],self.user['uid'])
        self.assertEqual(self.auth.list_regions(result['session'])[0]['id'],1)
        with self.assertRaisesRegex(AuthError,'invalid_credentials'):
            await self.bridge.authenticate(**request)

    async def test_wrong_password_is_not_success(self):
        request,_,_=self.request(password=b'DefinitelyWrong42')
        with self.assertRaisesRegex(AuthError,'invalid_credentials'):
            await self.bridge.authenticate(**request)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM auth_sessions').fetchone()[0],0)

    async def test_expiry_identity_corruption_and_concurrency(self):
        request,_,_=self.request();request['client_identity']=('game',999)
        with self.assertRaises(AuthError): await self.bridge.authenticate(**request)
        request,_,_=self.request();self.now+=31
        with self.assertRaises(AuthError): await self.bridge.authenticate(**request)
        request,_,_=self.request();request['password_cipher']='bad'
        with self.assertRaises(AuthError): await self.bridge.authenticate(**request)
        request,_,_=self.request()
        results=await asyncio.gather(self.bridge.authenticate(**request),self.bridge.authenticate(**request),return_exceptions=True)
        self.assertEqual(sum(isinstance(r,dict) for r in results),1)
        self.assertEqual(sum(isinstance(r,AuthError) for r in results),1)

    async def test_all_checksum_selectors_and_packet_guards(self):
        for selector in range(30):
            _,packet,dk=self.request(selector=selector)
            self.assertEqual(decode_inner(packet,dk,self.key),'FakePassword!42')
            bad=bytearray(packet);bad[-1]^=1
            with self.assertRaises(ValueError): decode_inner(bytes(bad),dk,self.key)
            with self.assertRaises(ValueError): decode_inner(packet,dk[::-1],self.key)


if __name__=='__main__': unittest.main()
