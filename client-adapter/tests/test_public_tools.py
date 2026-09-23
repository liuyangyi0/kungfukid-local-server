import asyncio
from pathlib import Path
import struct
import sys
import tempfile
import subprocess
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'tools'))
from inspect_client import pe_summary,public_key_summary,sdk_compatibility
from create_local_account import register,compatible_password
from server.kk_local.auth import AuthError

class PublicToolTests(unittest.TestCase):
    def test_sdk_layout_mismatch_is_not_reported_as_supported(self):
        for rva in ('0xc8c0',None):
            result=sdk_compatibility({'export_ordinal_16_rva':rva})
            self.assertFalse(result['expected_export16_rva_match'])
            self.assertEqual(result['adapter_compatibility'],'unsupported_sdk_layout')
            self.assertEqual(result['expected_export16_rva'],'0xc1b0')
            self.assertIn('Do not change',result['compatibility_note'])

    def test_export_match_remains_only_a_candidate(self):
        result=sdk_compatibility({'export_ordinal_16_rva':'0xc1b0'})
        self.assertTrue(result['expected_export16_rva_match'])
        self.assertEqual(result['adapter_compatibility'],'candidate_only')
        self.assertIn('does not prove',result['compatibility_note'])

    def test_cli_refuses_piped_password_before_creating_database(self):
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'never-created.sqlite3'
            result=subprocess.run([sys.executable,str(Path(__file__).resolve().parents[1]/'tools/create_local_account.py'),
                                   '--database',str(db),'--account','SyntheticUser','--nickname','LocalPlayer'],
                                  input='',text=True,capture_output=True,timeout=10)
            self.assertNotEqual(result.returncode,0)
            self.assertIn('interactive terminal required',result.stderr)
            self.assertFalse(db.exists())

    def test_bounded_pe_and_architecture(self):
        d=bytearray(1024);d[:2]=b'MZ';struct.pack_into('<I',d,60,128);d[128:132]=b'PE\0\0'
        struct.pack_into('<HH',d,132,0x14c,1);struct.pack_into('<H',d,148,224)
        struct.pack_into('<H',d,152,0x10b);struct.pack_into('<I',d,180,0x400000)
        struct.pack_into('<III',d,388,4096,512,512)
        self.assertTrue(pe_summary(d)['i686'])
        for data in (b'',bytes(64),d[:200]):
            with self.assertRaises(ValueError):pe_summary(data)
    def test_public_key_format_without_any_private_material(self):
        # Deliberately synthetic odd1024-bit value, not an RSA key pair.
        raw=struct.pack('<H',1024)+((1<<1023)|1).to_bytes(128,'big')+(3).to_bytes(128,'big')
        self.assertTrue(public_key_summary(raw)['format_valid'])
        for data in (raw[:-1],b'PEM',raw[:130]+bytes(128)):
            with self.assertRaises(ValueError):public_key_summary(data)
    def test_registration_and_duplicate_do_not_reset_password(self):
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/'synthetic.sqlite3')
            result=asyncio.run(register(path,'SyntheticUser','LocalPlayer','SyntheticPassword42!'))
            self.assertGreater(result['uid'],0)
            with self.assertRaises(AuthError):
                asyncio.run(register(path,'SyntheticUser','Other','AnotherPassword42!'))
        for s in ('short','A'*31,'Has spaces inside','密码字符不能用于这里'):
            with self.assertRaises(ValueError):compatible_password(s)
