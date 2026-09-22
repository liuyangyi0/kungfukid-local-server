import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from server.kk_local.app.cli import main,parse_application,mode_parser
from server.kk_local.app.configuration import read_settings


class ApplicationConfigTests(unittest.TestCase):
    def test_legacy_offline_flags_remain_compatible(self):
        mode,args,check=parse_application(['--offline-client-adapter','--role-ready-file','receipt.txt'])
        self.assertEqual(mode,'offline');self.assertFalse(check)
        self.assertEqual((args.database,args.login_port,args.game_port),('server/.data/local.sqlite3',8000,8001))
        self.assertFalse(args.experimental_stage21);self.assertEqual(args.experimental_team_series_rounds,0)
        from server.kk_local.__main__ import account_endpoints,match_point_policy
        from server.kk_local.app import configuration
        self.assertIs(account_endpoints,configuration.account_endpoints)
        self.assertIs(match_point_policy,configuration.match_point_policy)

    def test_modes_keep_explicit_insecure_switch(self):
        for argv in (['--mode','offline','--role-ready-file','r'],['--mode','lab','--config','x','--database','d','--events','e']):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):parse_application(argv)
        with self.assertRaises(ValueError):
            parse_application(['--offline-client-adapter','--role-ready-file','r','--experimental-mode10'])

    def test_settings_resolve_paths_and_cli_values_override(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);file=root/'server.json'
            file.write_text(json.dumps(dict(schema='kk-server-settings-v1',mode='auth',options=dict(
                database='data/account.sqlite3',client_root='client',role_ready_file='ready',events='logs/events.jsonl',auth_port=7998))))
            mode,args,check=parse_application(['--settings',str(file),'--auth-port','7997','--check-config'])
            self.assertEqual(mode,'auth');self.assertTrue(check);self.assertEqual(args.auth_port,7997)
            self.assertEqual(args.database,str(root/'data/account.sqlite3'));self.assertEqual(args.client_root,str(root/'client'))
            self.assertEqual(list(root.iterdir()),[file])

    def test_unknown_duplicate_or_wrong_type_options_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'s.json'
            base=dict(schema='kk-server-settings-v1',mode='offline',options=dict(offline_client_adapter=True,role_ready_file='r'))
            for key,value in (('password','do-not-log'),('game_port',True),('game_port','8001'),('experimental_stage21','yes')):
                doc={**base,'options':{**base['options'],key:value}};file.write_text(json.dumps(doc))
                with self.assertRaises(ValueError) as error:parse_application(['--settings',str(file)])
                self.assertNotIn('do-not-log',str(error.exception))
            file.write_text('{"schema":"kk-server-settings-v1","mode":"offline","mode":"auth","options":{}}')
            with self.assertRaises(ValueError):read_settings(file)

    def test_mode_conflict_and_output_alias_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            file=Path(d)/'s.json'
            file.write_text(json.dumps(dict(schema='kk-server-settings-v1',mode='offline',options=dict(
                offline_client_adapter=True,role_ready_file='r',database='db',log='db'))))
            with self.assertRaises(ValueError):parse_application(['--settings',str(file)])
            with self.assertRaises(ValueError):parse_application(['--mode','auth','--settings',str(file)])
            file.write_text(json.dumps(dict(schema='kk-server-settings-v1',mode='offline',options=dict(
                offline_client_adapter=True,role_ready_file='r',database='s.json'))))
            with self.assertRaises(ValueError):parse_application(['--settings',str(file)])

    def test_check_only_never_dispatches_runtime(self):
        with patch('server.kk_local.app.cli.runner',side_effect=AssertionError('must not start')),contextlib.redirect_stdout(io.StringIO()) as output:
            main(['--mode','sdo','--client-root','not-created','--database','missing.db','--runtime','unused-run','--check-config'])
        self.assertEqual(json.loads(output.getvalue()),dict(status='valid',mode='sdo',started=False,scope='configuration_only'))

    def test_auth_sdo_port_contracts_and_mode_specific_options(self):
        with self.assertRaises(ValueError):parse_application(['--offline-client-adapter','--role-ready-file','r','--login-port','8001'])
        with self.assertRaises(ValueError):parse_application(['--mode','auth','--database','d','--client-root','c','--events','e',
            '--role-ready-file','r','--auth-port','8000'])
        with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
            parse_application(['--mode','auth','--database','d','--client-root','c','--events','e','--role-ready-file','r','--offline-client-adapter'])
        args=mode_parser('sdo').parse_args(['--database','d','--client-root','c','--runtime','r'])
        self.assertEqual((args.api_port,args.http_port,args.login_port,args.game_port),(17999,18082,18000,18001))

    def test_fresh_process_help_and_check_do_not_require_sdo_crypto(self):
        code="from server.kk_local.app.cli import main; import sys; main(['--mode','sdo','--client-root','c','--database','d','--runtime','r','--check-config']); assert 'cryptography' not in sys.modules; assert 'server.kk_local.sdo_service' not in sys.modules"
        result=subprocess.run([sys.executable,'-B','-c',code],capture_output=True,timeout=15)
        self.assertEqual(result.returncode,0,result.stderr)
        for module in ('server.kk_local','server.kk_local.auth_service','server.kk_local.lab_server','server.kk_local.sdo_service'):
            result=subprocess.run([sys.executable,'-B','-m',module,'--help'],capture_output=True,timeout=15)
            self.assertEqual(result.returncode,0,(module,result.stderr))
