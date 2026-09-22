"""Regressions migrated from the 2026-09-07 inventory findings.

Synthetic data only. Passing these tests is not a new native-client validation.
"""
import sqlite3
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from server.kk_local.engine import Connection, Engine, Phase
from server.kk_local.store import Store, PERMANENT_WEAPON_DISPLAY_MINUTES
from server.kk_local.wire import Message


class HistoricalInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = str(Path(self.temp.name) / 'local.db')
        self.store = Store(self.path)
        self.store.seed_local()
        self.plan = dict(schema='kk-local-inventory-grant-v1', uid=1001,
                         grant_id='historical-test',
                         rows=[[253033, 25, 1], [643001, 64, 7]])
        self.store.apply_grant(self.plan)
        self.engine = Engine(self.store)
        self.client = Connection(1, Phase.LOBBY, 1001)
        self.engine.game = self.client

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def records(self, uid=1001):
        return dict(self.store.db.execute(
            'SELECT instance,record FROM inventory WHERE uid=?', (uid,)))

    def item(self, prop):
        return next((i, r) for i, r in self.records().items()
                    if struct.unpack_from('<I', r, 5)[0] == prop)

    def test_slot_zero_equip_preserves_prefix_and_displaces_only_main(self):
        main, _ = self.item(253030)
        weapon, old = self.item(253033)
        # Unknown request bits are echoed unchanged, not interpreted as authority.
        request = struct.pack('<IIQ', weapon, 0, 0x1122334455667788)
        replies = self.engine.handle(self.client, Message(2080, request))
        self.assertEqual([m.id for m in replies], [1120, 2090])
        self.assertEqual(replies[1].payload[:16], request)
        self.assertEqual(replies[1].payload[16:], old[:17] + b'\x08\x00' + old[19:])
        self.assertEqual(struct.unpack_from('<H', self.records()[main], 17)[0], 0)
        self.assertEqual(replies[0].payload, self.store.snapshot(1001)[3])
        # Whole replacement includes clothes and supplies; never only the weapon.
        self.assertEqual(len(replies[0].payload), 9 * 68)
        self.assertEqual(self.client.phase, Phase.LOBBY)

    def test_slot_zero_not_general_auto_slot_and_store_denial_is_atomic(self):
        before = self.store.snapshot(1001)
        for prop in (121005, 643001):
            instance, _ = self.item(prop)
            with self.assertRaisesRegex(ValueError, 'unqualified item slot'):
                self.store.equip(1001, instance, 0)
        self.assertEqual(self.store.snapshot(1001), before)
        with self.assertRaisesRegex(ValueError, 'item not owned'):
            self.store.equip(1001, 999, 0)
        instance, _ = self.item(253033)
        self.assertEqual(self.engine.handle(self.client, Message(2080, struct.pack('<IIQ', instance, 0, 0)))[-1].id, 2090)
        self.assertIs(self.engine.game, self.client)

    def test_explicit_secondary_slot_and_other_account_ownership_preserved(self):
        instance, _ = self.item(253033)
        self.store.equip(1001, instance, 9)
        self.assertEqual(struct.unpack_from('<H', self.item(253030)[1], 17)[0], 8)
        self.assertEqual(struct.unpack_from('<H', self.item(253033)[1], 17)[0], 9)
        before = self.store.snapshot(1001)
        with self.assertRaisesRegex(ValueError, 'not owned'):
            self.store.equip(1002, instance, 0)
        self.assertEqual(self.store.snapshot(1001), before)

    def test_permanent_policy_is_opt_in_scoped_and_preserves_opaque_bytes(self):
        self.store.provision_local(1002, 'Other')
        other = self.store.snapshot(1002)
        profile = self.store.snapshot(1001)[:3]
        instance, raw = self.item(253033)
        raw = bytearray(raw); raw[9:13] = b'abcd'; raw[25:] = bytes(range(43))
        self.store.db.execute('UPDATE inventory SET record=? WHERE uid=1001 AND instance=?',
                              (bytes(raw), instance)); self.store.db.commit()
        before = self.records()
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM permanent_weapons').fetchone()[0], 0)
        self.assertEqual(self.store.set_weapons_permanent(1001), 2)
        after = self.records()
        self.assertEqual(before.keys(), after.keys())
        for instance, old in before.items():
            new = after[instance]
            if old[4] != 25:
                self.assertEqual(new, old)
                continue
            self.assertEqual(new[:13], old[:13])
            self.assertEqual(new[17:19], old[17:19])
            self.assertEqual(new[25:], old[25:])
            self.assertEqual(struct.unpack_from('<I', new, 13)[0], PERMANENT_WEAPON_DISPLAY_MINUTES)
            self.assertEqual(struct.unpack_from('<IH', new, 19), (1, 0))
        self.assertEqual(self.store.snapshot(1001)[:3], profile)
        self.assertEqual(self.store.snapshot(1002), other)
        self.assertEqual(self.store.set_weapons_permanent(1001), 2)
        self.assertEqual(self.records(), after)

    def test_permanent_survives_grant_equip_unequip_and_restart(self):
        self.store.set_weapons_permanent(1001)
        self.store.apply_grant(dict(self.plan, grant_id='later-weapon-grant', rows=[[253033, 25, 1]]))
        instance, raw = self.item(253033)
        self.assertEqual(struct.unpack_from('<H', raw, 23)[0], 0)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM permanent_weapons').fetchone()[0], 2)
        self.store.equip(1001, instance, 0)
        self.store.unequip(1001, instance)
        before = self.store.snapshot(1001)
        self.store.close(); self.store = Store(self.path)
        self.assertEqual(self.store.snapshot(1001), before)
        self.assertEqual(self.store.apply_grant(self.plan), 0)
        self.assertEqual(self.store.set_weapons_permanent(1001), 2)
        self.assertEqual(self.store.snapshot(1001), before)
        self.assertEqual(struct.unpack_from('<H', self.item(643001)[1], 23)[0], 7)

    def test_permanent_failure_rolls_back_records_and_entitlements(self):
        second, _ = self.item(253033)
        before = self.store.snapshot(1001)
        self.store.db.execute(f'''CREATE TRIGGER reject_permanent BEFORE UPDATE ON inventory
            WHEN NEW.instance={second} BEGIN SELECT RAISE(ABORT, 'test failure'); END''')
        self.store.db.commit()
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.set_weapons_permanent(1001)
        self.assertEqual(self.store.snapshot(1001), before)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM permanent_weapons').fetchone()[0], 0)
        with self.assertRaisesRegex(ValueError, 'unknown local account'):
            self.store.set_weapons_permanent(999)

    def test_old_database_open_adds_empty_policy_without_changing_items(self):
        before = self.store.snapshot(1001)
        self.store.db.execute('DROP TABLE permanent_weapons'); self.store.db.commit()
        self.store.close(); self.store = Store(self.path)
        self.assertEqual(self.store.snapshot(1001), before)
        self.assertEqual(self.store.db.execute('SELECT COUNT(*) FROM permanent_weapons').fetchone()[0], 0)

    def test_admin_command_no_password_and_no_database_creation(self):
        missing = str(Path(self.temp.name) / 'missing.db')
        base = [sys.executable, '-m', 'server.kk_local.accounts', '--database']
        failed = subprocess.run(base + [missing, 'set-weapons-permanent', 'KKLocal'], capture_output=True)
        self.assertNotEqual(failed.returncode, 0)
        self.assertFalse(Path(missing).exists())
        success = subprocess.run(base + [self.path, 'set-weapons-permanent', 'KKLocal'],
                                 capture_output=True, timeout=15)
        self.assertEqual(success.returncode, 0, success.stderr)
        self.assertIn(b'2 existing weapons', success.stdout)
        self.assertEqual(struct.unpack_from('<H', self.item(643001)[1], 23)[0], 7)
