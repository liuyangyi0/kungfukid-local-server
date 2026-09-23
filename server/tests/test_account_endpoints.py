import json
from pathlib import Path
import tempfile
import unittest

from server.kk_local.__main__ import account_endpoints
from server.kk_local.store import Store


class AccountEndpointsTests(unittest.TestCase):
    def test_config_validation_does_not_accept_alias_receipts_or_conflicting_ports(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            rows=[dict(uid=1001+i,account=f'Local{i}',nickname=f'Player{i}',
                       login_port=8000+i*100,game_port=8001+i*100,p2p_port=8001+i*100,
                       role_ready_file=str(root/f'ready{i}.txt')) for i in range(2)]
            path=root/'accounts.json'
            def validate(value):
                path.write_text(json.dumps(dict(schema='kk-offline-account-endpoints-v1',accounts=value)),encoding='utf-8')
                return account_endpoints(path)
            self.assertEqual(validate(rows),rows)
            for field,value in (('uid',1001),('account','Local0'),('login_port',8001),
                                ('p2p_port',8001),('role_ready_file',str(root/'sub'/'..'/'ready0.txt')),
                                ('uid',True),('login_port',0),('role_ready_file','relative.txt')):
                changed=[dict(r) for r in rows]; changed[1][field]=value
                with self.subTest(field=field,value=value), self.assertRaises(ValueError): validate(changed)

    def test_provision_is_idempotent_preserves_existing_profile_and_rejects_reassignment(self):
        s=Store(':memory:')
        try:
            s.seed_local(); s.provision_local(1002,'Second','第二人')
            before=s.snapshot(1002)
            self.assertEqual(len(before[2]),360)
            self.assertEqual(before[2][4:25].split(b'\0')[0].decode('gbk'),'第二人')
            s.provision_local(1002,'Second','NotAReset')
            self.assertEqual(s.snapshot(1002),before)
            with self.assertRaises(ValueError): s.provision_local(1002,'Different')
            self.assertEqual(s.snapshot(1002),before)
        finally: s.close()
