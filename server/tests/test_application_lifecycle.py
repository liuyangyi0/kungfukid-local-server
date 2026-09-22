import asyncio
from contextlib import AsyncExitStack
import contextlib
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from server.kk_local.app.lifecycle import EventLog,own_services,start_services
from server.kk_local.app import offline,lab
from server.kk_local.app.features import GameContent
from server.kk_local.app.cli import mode_parser
from server.kk_local.store import Store


class ApplicationLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_partial_start_closes_every_service_in_original_order(self):
        events=[]
        class S:
            def __init__(self,name,fail=False):self.name=name;self.fail=fail
            async def start(self):
                events.append('start:'+self.name)
                if self.fail:raise OSError('fixture bind failure')
            async def close(self):events.append('close:'+self.name)
        a,b=S('a'),S('b',True)
        with self.assertRaises(OSError):
            async with AsyncExitStack() as stack:
                own_services(stack,[a,b]);await start_services([a,b])
        self.assertEqual(events,['start:a','start:b','close:a','close:b'])

    async def test_cleanup_failure_does_not_skip_other_resources(self):
        events=[]
        class S:
            def __init__(self,n):self.n=n
            async def close(self):
                events.append(self.n)
                if self.n=='a':raise OSError('close failure')
        with self.assertRaises(OSError):
            async with AsyncExitStack() as stack:
                stack.callback(events.append,'store')
                own_services(stack,[S('a'),S('b')])
        self.assertEqual(events,['a','b','store'])

    async def test_offline_failed_start_closes_db_log_and_writes_report(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);owned=[];services=[]
            def create_store(path):s=Store(path);owned.append(s);return s
            class S:
                def __init__(self,*args,**kwargs):self.closed=False;services.append(self)
                async def start(self):raise OSError('fixture bind')
                async def close(self):self.closed=True
                def report(self):return {'closed':self.closed}
            args=mode_parser('offline').parse_args(['--offline-client-adapter','--role-ready-file',str(root/'ready'),
                '--database',str(root/'db'),'--log',str(root/'events'),'--report',str(root/'report')])
            with patch.object(offline,'Store',side_effect=create_store),patch.object(offline,'Service',S),patch.object(offline,'load_content',return_value=GameContent(None,None,None)):
                with self.assertRaises(OSError):await offline.run(args)
            with self.assertRaises(sqlite3.ProgrammingError):owned[0].db.execute('SELECT 1')
            self.assertTrue(services[0].closed);self.assertEqual(json.loads((root/'report').read_text()),{'closed':True})
            with (root/'events').open('a') as f:f.write('closed and released')

    async def test_lab_existing_log_refuses_and_closes_opened_store(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);events=root/'events';events.write_text('old evidence');owned=[]
            def create_store(path):s=Store(path);owned.append(s);return s
            args=mode_parser('lab').parse_args(['--insecure-host-only-lab','--config','ignored','--database',str(root/'db'),'--events',str(events)])
            with patch.object(lab,'Store',side_effect=create_store),patch.object(lab,'lab_endpoints',return_value=[]),patch.object(lab,'load_content',return_value=GameContent(None,None,None)):
                with self.assertRaises(FileExistsError):await lab.run(args)
            self.assertEqual(events.read_text(),'old evidence')
            with self.assertRaises(sqlite3.ProgrammingError):owned[0].db.execute('SELECT 1')

    async def test_cancelled_real_offline_service_closes_and_reports(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);started=asyncio.Event();original=offline.Service.start
            async def start(s):
                result=await original(s);started.set();return result
            args=mode_parser('offline').parse_args(['--offline-client-adapter','--role-ready-file',str(root/'ready'),
                '--database',str(root/'db'),'--log',str(root/'events'),'--report',str(root/'report'),
                '--login-port','0','--game-port','0','--p2p-port','0'])
            with patch.object(offline.Service,'start',start),contextlib.redirect_stdout(io.StringIO()):
                task=asyncio.create_task(offline.run(args))
                try:await asyncio.wait_for(started.wait(),3)
                finally:
                    task.cancel()
                    with self.assertRaises(asyncio.CancelledError):await task
            self.assertTrue((root/'report').is_file());self.assertIn('events',(root/'events').name)


class EventLogTests(unittest.TestCase):
    def test_database_initialization_error_closes_connection(self):
        class Connection:
            closed=False
            def execute(self,*args):pass
            def close(self):self.closed=True
        connection=Connection()
        with patch('server.kk_local.store.sqlite3.connect',return_value=connection),patch('server.kk_local.storage.schema.initialize',side_effect=RuntimeError('fixture schema error')):
            with self.assertRaises(RuntimeError):Store(':memory:')
        self.assertTrue(connection.closed)

    def test_append_exclusive_timestamp_and_io_failure_policy(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'log'
            with EventLog(path,timestamps=True) as log:log({'event':'test','timestamp':123})
            self.assertEqual(json.loads(path.read_text()),{'event':'test','timestamp':123})
            with EventLog(path) as log:log({'event':'second'})
            self.assertEqual(len(path.read_text().splitlines()),2)
            with self.assertRaises(FileExistsError):
                with EventLog(path,exclusive=True):pass
            class Broken:
                def write(self,*args):raise OSError('disk unavailable')
            log=EventLog(path);log.file=Broken();log({'event':'ignored io error'})
