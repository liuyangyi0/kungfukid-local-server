import struct
import tempfile
import unittest
import subprocess
import sys
from pathlib import Path
from server.kk_local.store import Store
from server.kk_local.engine import Engine,Connection
from server.kk_local.wire import Message,ProtocolError
from server.kk_local.shop_catalog import decode_catalog
from server.tests.test_competitive_rooms import prepare_handoff
from server.tests.test_local_service import hello


def fixture(item,key,tag):
    # Synthetic codec fixture, not a VM-qualified sale offer.
    raw=bytearray([tag]*108)
    struct.pack_into('<II',raw,5,item,key)
    return bytes(raw)


class ShopStoreTests(unittest.TestCase):
    def test_explicit_import_command_requires_existing_database(self):
        from server.kk_local.shop_catalog import encode_catalog,ShopRecord
        with tempfile.TemporaryDirectory() as d:
            db=Path(d)/'db.sqlite3';payload=Path(d)/'payload.bin'
            s=Store(str(db));s.seed_local();s.close()
            raw=fixture(253033,25303301,17)
            payload.write_bytes(encode_catalog(25,1,[ShopRecord(raw)]).payload)
            args=[sys.executable,'-m','server.kk_local.shop_catalog','--database',str(db),
                  '--import-9080-payload',str(payload),'--replace']
            run=subprocess.run(args,cwd=Path(__file__).resolve().parents[2],capture_output=True,timeout=20)
            self.assertEqual(run.returncode,0,run.stderr)
            s=Store(str(db));self.assertEqual(s.shop_records(25,1)[0].raw,raw);s.close()
            args[args.index('--database')+1]=str(Path(d)/'missing.sqlite3')
            run=subprocess.run(args,cwd=Path(__file__).resolve().parents[2],capture_output=True,timeout=20)
            self.assertNotEqual(run.returncode,0)
            self.assertFalse((Path(d)/'missing.sqlite3').exists())

    def test_persist_order_and_failed_import_rollback(self):
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/'db.sqlite3');s=Store(path);s.seed_local()
            before=s.snapshot(1001)
            a=fixture(253033,25303301,17);b=fixture(253030,25303001,29)
            self.assertEqual(s.replace_shop_catalog(255,25,[a,b]),2)
            for invalid in ([a,a],[a,fixture(253030,25303301,33)],[a,b'bad']):
                with self.assertRaises((ValueError,ProtocolError)):s.replace_shop_catalog(255,25,invalid)
                self.assertEqual(tuple(r.raw for r in s.shop_records(255,25)),(a,b))
            self.assertEqual(s.snapshot(1001),before)
            s.close();s=Store(path)
            self.assertEqual(tuple(r.raw for r in s.shop_records(255,25)),(a,b))
            s.replace_shop_catalog(1,1,[a]);s.replace_shop_catalog(255,25,[])
            self.assertEqual(s.shop_records(255,25),())
            self.assertEqual(len(s.shop_records(1,1)),1)
            s.close()

    def test_query_serves_complete_records_but_does_not_enable_purchase(self):
        s=Store(':memory:');s.seed_local()
        try:
            raw=fixture(253033,25303301,17)
            s.replace_shop_catalog(25,1,[raw]);before=s.snapshot(1001)
            e=Engine(s);prepare_handoff(e);c=Connection(2);e.handle(c,hello(2010))
            reply=e.handle(c,Message(9070,b'\x19\x01'))[0]
            self.assertEqual(reply.id,9080)
            category,variant,records=decode_catalog(reply.payload)
            self.assertEqual((category,variant),(25,1))
            self.assertEqual(records[0].raw,raw)
            quick=e.handle(c,Message(1500,struct.pack('<BII',17,253033,0)))[0]
            self.assertEqual(quick,Message(1510,raw))
            self.assertEqual(e.handle(c,Message(1500,struct.pack('<BII',25,253033,0))),[Message(1510)])
            self.assertEqual(e.handle(c,Message(1500,struct.pack('<BII',17,253033,1))),[])
            self.assertEqual(e.handle(c,Message(9040,bytes(169))),[Message(9060,b'\x82\0')])
            self.assertEqual(s.snapshot(1001),before)
        finally:s.close()


if __name__=='__main__':unittest.main()
