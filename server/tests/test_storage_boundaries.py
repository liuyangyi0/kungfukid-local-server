import ast
from pathlib import Path
import sqlite3
import tempfile
import unittest
from server.kk_local.store import Store
from server.kk_local import mailbox,shop


class StorageBoundaryTests(unittest.TestCase):
    def setUp(self):self.s=Store(':memory:');self.s.seed_local()
    def tearDown(self):self.s.close()

    def test_repository_writes_cannot_implicitly_start_or_commit(self):
        calls=(lambda:self.s.inventory.allocate_instance(),
               lambda:self.s.inventory.insert(1001,777,bytes(68)),
               lambda:self.s.inventory.update(1001,0,bytes(68)),
               lambda:self.s.inventory.add_permanent(1001,0),
               lambda:self.s.commerce.set_balance(1001,'gold',2),
               lambda:self.s.commerce.put_offer(1,bytes(108),bytes(68)),
               lambda:self.s.mail.allocate_id(),lambda:self.s.mail.mark_read(1001,1))
        before=self.s.snapshot(1001)
        for call in calls:
            with self.assertRaises(ValueError):call()
        self.assertEqual(before,self.s.snapshot(1001));self.assertFalse(self.s.in_transaction)

    def test_cross_repository_rollback_restores_inventory_wallet_and_id_counter(self):
        before=self.s.snapshot(1001)
        with self.assertRaises(RuntimeError):
            with self.s.transaction():
                allocated=self.s.inventory.allocate_instance()
                self.s.inventory.insert(1001,allocated,bytes(68))
                self.s.commerce.set_balance(1001,'gold',123)
                raise RuntimeError('abort the whole business operation')
        self.assertEqual(self.s.snapshot(1001),before);self.assertEqual(self.s.gold_balance(1001),0)
        with self.s.transaction():self.assertEqual(self.s.inventory.allocate_instance(),allocated)

    def test_nested_public_operation_does_not_commit_or_rollback_outer_work(self):
        with self.assertRaises(RuntimeError):
            with self.s.transaction():
                self.s.commerce.set_balance(1001,'gold',20)
                with self.assertRaises(ValueError):mailbox.deliver(self.s,1001,'nested',title='t',sender='s',body='b')
                self.assertTrue(self.s.in_transaction);self.assertEqual(self.s.gold_balance(1001),20)
                raise RuntimeError('outer rollback')
        self.assertEqual(self.s.gold_balance(1001),0)

    def test_locked_mail_helper_joins_callers_transaction(self):
        with self.assertRaises(RuntimeError):
            with self.s.transaction():
                mailbox.deliver_locked(self.s,1001,'outer',title='t',sender='s',body='b')
                self.s.commerce.set_balance(1001,'ticket',44)
                self.assertEqual(self.s.mail.pending_count(1001),1)
                raise RuntimeError('rollback delivery and wallet')
        self.assertEqual(self.s.mail.pending_count(1001),0);self.assertEqual(self.s.commerce.balance(1001,'ticket'),0)

    def test_readers_only_observe_committed_business_unit(self):
        with tempfile.TemporaryDirectory() as d:
            path=str(Path(d)/'db.sqlite3');a=Store(path);a.seed_local();b=Store(path)
            try:
                with a.transaction():
                    a.commerce.set_balance(1001,'gold',23)
                    mailbox.deliver_locked(a,1001,'one',title='t',sender='s',body='b')
                    self.assertEqual(b.gold_balance(1001),0);self.assertEqual(b.mail.pending_count(1001),0)
                self.assertEqual(b.gold_balance(1001),23);self.assertEqual(b.mail.pending_count(1001),1)
            finally:b.close();a.close()

    def test_commit_failure_rolls_back_and_releases_transaction(self):
        #Deferred foreign-key errors occur on commit, not at insert.
        with self.s.db:self.s.db.execute('CREATE TABLE deferred_test(uid INTEGER REFERENCES accounts(uid) DEFERRABLE INITIALLY DEFERRED)')
        with self.assertRaises(sqlite3.IntegrityError):
            with self.s.transaction():
                self.s.commerce.set_balance(1001,'gold',12)
                self.s.db.execute('INSERT INTO deferred_test VALUES(99999)')
        self.assertFalse(self.s.in_transaction);self.assertEqual(self.s.gold_balance(1001),0)
        self.assertEqual(self.s.db.execute('SELECT COUNT(*) FROM deferred_test').fetchone()[0],0)

    def test_currency_is_whitelisted_not_sql(self):
        with self.s.transaction():
            with self.assertRaises(ValueError):self.s.commerce.set_balance(1001,'gold; DROP TABLE accounts',1)
            self.s.commerce.set_balance(1001,'gold',2)
        self.assertEqual(self.s.gold_balance(1001),2);self.assertTrue(self.s.commerce.account_exists(1001))

    def test_migrated_business_has_no_connection_escape_hatch(self):
        root=Path(__file__).resolve().parents[1]/'kk_local'
        for path in root.rglob('*.py'):
            relative=path.relative_to(root)
            if relative.parts[0]=='storage' or relative.as_posix()=='store.py':continue
            tree=ast.parse(path.read_text(encoding='utf-8'))
            forbidden=[n.attr for n in ast.walk(tree) if isinstance(n,ast.Attribute) and
                       n.attr in ('db','_db','execute','executemany','executescript','commit','rollback')]
            self.assertEqual(forbidden,[],relative)
        for path in (root/'storage').glob('*.py'):
            if path.name in ('schema.py','transactions.py'):continue
            relative=path.name
            tree=ast.parse(path.read_text(encoding='utf-8'))
            self.assertFalse(any(isinstance(n,ast.Attribute) and n.attr in ('commit','rollback') for n in ast.walk(tree)),relative)
