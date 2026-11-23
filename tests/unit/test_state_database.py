import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from helper import state_database as helper_state
from helper.forwarding import ForwardingControl
from helper.journal import Journal
from helper.recovery_records import RecoveryRecords
from helper.state_database import LEGACY_TABLES, STATE_FILE, prepare_state
from shared.errors import ServiceError

# The schemas the three databases had before they were merged, including the two recovery tables
# that nothing reads any more.
LEGACY_SCHEMAS = {
    'journal.db': (
        "CREATE TABLE operations(id TEXT PRIMARY KEY, operation TEXT NOT NULL, "
        "email TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL, "
        "state TEXT NOT NULL, phase TEXT, steps TEXT NOT NULL, "
        "preconditions TEXT NOT NULL, postconditions TEXT NOT NULL)",
        "CREATE TABLE incarnations(email TEXT PRIMARY KEY,generation TEXT NOT NULL, "
        "created REAL NOT NULL,present INTEGER NOT NULL)",
        "INSERT INTO operations VALUES('op-1','create','a@rsalehin24.me',1,2,'complete',NULL,"
        "'[]','{}','{}')",
        "INSERT INTO operations VALUES('op-2','delete','b@rsalehin24.me',3,4,'pending',NULL,"
        "'[]','{}','{}')",
        "INSERT INTO incarnations VALUES('a@rsalehin24.me','generation-a',1,1)",
    ),
    'recovery.db': (
        "CREATE TABLE recovery_records (id TEXT PRIMARY KEY, email TEXT NOT NULL, "
        "state TEXT NOT NULL, retention TEXT NOT NULL, deleted_at INTEGER NOT NULL, "
        "deadline INTEGER, policy_revision INTEGER NOT NULL, snapshot TEXT, "
        "display_name TEXT NOT NULL, notes TEXT NOT NULL, updated_at INTEGER NOT NULL)",
        "CREATE TABLE recovery_jobs (id TEXT PRIMARY KEY, kind TEXT NOT NULL)",
        "CREATE TABLE recovery_checkpoints (job_id TEXT PRIMARY KEY)",
        "CREATE TABLE recovery_audit (id INTEGER PRIMARY KEY, created INTEGER NOT NULL, "
        "action TEXT NOT NULL, request_id TEXT NOT NULL, email TEXT NOT NULL, "
        "outcome TEXT NOT NULL)",
        "INSERT INTO recovery_records VALUES('11111111-1111-4111-8111-111111111111',"
        "'gone@rsalehin24.me','deleted','7_days',10,20,1,'snap','Gone','Notes',10)",
        "INSERT INTO recovery_jobs VALUES('job-1','recovery_restore')",
        "INSERT INTO recovery_audit VALUES(7,10,'recovery_delete','req','a@rsalehin24.me','ok')",
        "INSERT INTO recovery_audit VALUES(9,11,'recovery_restore','req2','a@rsalehin24.me','ok')",
    ),
    'forwarding.db': (
        "CREATE TABLE metadata (id INTEGER PRIMARY KEY,revision INTEGER NOT NULL)",
        "CREATE TABLE rules (email TEXT PRIMARY KEY,destination TEXT NOT NULL,"
        "enabled INTEGER NOT NULL,created REAL NOT NULL,updated REAL NOT NULL)",
        "CREATE TABLE messages (email TEXT NOT NULL,body_key TEXT NOT NULL,"
        "destination TEXT NOT NULL,state TEXT NOT NULL,created REAL NOT NULL,"
        "updated REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,"
        "error TEXT NOT NULL DEFAULT '',body BLOB,PRIMARY KEY(email,body_key,destination))",
        "CREATE TABLE requests (id TEXT PRIMARY KEY,payload TEXT NOT NULL,result TEXT NOT NULL)",
        "CREATE TABLE verifications (destination TEXT PRIMARY KEY,state TEXT NOT NULL)",
        "CREATE TABLE audit (created REAL NOT NULL,action TEXT NOT NULL,email TEXT NOT NULL,"
        "request_id TEXT NOT NULL)",
        "INSERT INTO metadata VALUES(1,5)",
        "INSERT INTO rules VALUES('a@rsalehin24.me','out@example.com',1,1,2)",
        "INSERT INTO messages VALUES('a@rsalehin24.me','k1','out@example.com','queued',1,2,3,'',"
        "x'0102')",
        "INSERT INTO requests VALUES('r1','{}','{}')",
        "INSERT INTO verifications VALUES('out@example.com','queued')",
        "INSERT INTO audit VALUES(1,'forwarding_save','a@rsalehin24.me','r1')",
    ),
}
MIGRATED_COUNTS = {
    'operations': 2, 'incarnations': 1, 'recovery_records': 1, 'recovery_audit': 2,
    'metadata': 1, 'rules': 1, 'messages': 1, 'requests': 1, 'verifications': 1, 'audit': 1,
}


def write_legacy(directory, names=tuple(LEGACY_SCHEMAS)):
    for name in names:
        path = directory / name
        os.close(os.open(path, os.O_CREAT | os.O_WRONLY, 0o600))
        with sqlite3.connect(path) as connection:
            for statement in LEGACY_SCHEMAS[name]:
                connection.execute(statement)


def table_counts(path):
    with sqlite3.connect(path) as connection:
        tables = [row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {table: connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0]  # noqa: S608 -- names from sqlite_master
                for table in tables}


class StateDatabaseTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)
        self.state = self.directory / STATE_FILE

    def retired(self, name):
        return self.directory / (name + '.migrated')

    def assert_all_migrated_once(self):
        counts = table_counts(self.state)
        for table, expected in MIGRATED_COUNTS.items():
            self.assertEqual(counts[table], expected, table)

    def test_fresh_install_creates_the_schema_once_with_forwarding_revision_zero(self):
        prepare_state(self.state)
        prepare_state(self.state)
        with sqlite3.connect(self.state) as connection:
            self.assertEqual(connection.execute('SELECT version FROM schema_migrations').fetchall(),
                             [(1,)])
            self.assertEqual(connection.execute('SELECT * FROM metadata').fetchall(), [(1, 0)])
        self.assertEqual(self.state.stat().st_mode & 0o777, 0o600)

    def test_migration_copies_every_row_and_retires_the_old_files(self):
        write_legacy(self.directory)
        prepare_state(self.state)
        self.assert_all_migrated_once()
        for name in LEGACY_SCHEMAS:
            self.assertFalse((self.directory / name).exists(), name)
            self.assertTrue(self.retired(name).is_file(), name)
        with sqlite3.connect(self.state) as connection:
            self.assertEqual(connection.execute('SELECT id FROM recovery_audit ORDER BY id')
                             .fetchall(), [(7,), (9,)])
            self.assertEqual(connection.execute('SELECT revision,body FROM metadata, messages')
                             .fetchone(), (5, b'\x01\x02'))

    def test_dropped_recovery_tables_stay_only_in_the_retired_file(self):
        write_legacy(self.directory)
        prepare_state(self.state)
        self.assertNotIn('recovery_jobs', table_counts(self.state))
        self.assertNotIn('recovery_checkpoints', table_counts(self.state))
        self.assertEqual(table_counts(self.retired('recovery.db'))['recovery_jobs'], 1)

    def test_public_classes_read_the_migrated_data(self):
        write_legacy(self.directory)
        records = RecoveryRecords(self.state)
        journal = Journal(self.state, os.geteuid())
        forwarding = ForwardingControl(self.directory, self.directory)
        self.assertEqual(journal.incarnation('a@rsalehin24.me')['generation'], 'generation-a')
        self.assertEqual(journal.lookup('op-1')['state'], 'complete')
        self.assertEqual([row['id'] for row in journal.unresolved()], ['op-2'])
        self.assertEqual(records.get('11111111-1111-4111-8111-111111111111')['snapshot'], 'snap')
        listing = forwarding.listing()
        self.assertEqual(listing['revision'], 5)
        self.assertEqual(listing['rules'][0]['pending'], 1)

    def test_migration_is_idempotent(self):
        write_legacy(self.directory)
        prepare_state(self.state)
        prepare_state(self.state)
        RecoveryRecords(self.state)
        self.assert_all_migrated_once()

    def test_partial_old_files_migrate_and_the_rest_are_left_out(self):
        write_legacy(self.directory, ('journal.db',))
        prepare_state(self.state)
        self.assertEqual(table_counts(self.state)['operations'], 2)
        self.assertEqual(table_counts(self.state)['rules'], 0)
        self.assertEqual(table_counts(self.state)['metadata'], 1)

    def test_second_open_with_old_and_new_present_does_nothing(self):
        write_legacy(self.directory)
        prepare_state(self.state)
        shutil.copy2(self.retired('journal.db'), self.directory / 'journal.db')
        before = self.state.stat().st_mtime_ns
        prepare_state(self.state)
        self.assert_all_migrated_once()
        self.assertEqual(self.state.stat().st_mtime_ns, before)
        self.assertTrue((self.directory / 'journal.db').is_file())

    def test_old_file_written_after_the_copy_is_refused_not_ignored(self):
        write_legacy(self.directory)
        prepare_state(self.state)
        shutil.copy2(self.retired('journal.db'), self.directory / 'journal.db')
        with sqlite3.connect(self.directory / 'journal.db') as connection:
            connection.execute("INSERT INTO incarnations VALUES('late@rsalehin24.me','g',1,1)")
        with self.assertRaisesRegex(ServiceError, 'state_migration_source_changed'):
            prepare_state(self.state)
        self.assert_all_migrated_once()

    def test_crash_after_the_copy_before_the_rename_does_not_duplicate_rows(self):
        write_legacy(self.directory)
        with patch('helper.state_database._retire', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                prepare_state(self.state)
        self.assert_all_migrated_once()
        self.assertTrue((self.directory / 'recovery.db').is_file())
        prepare_state(self.state)
        self.assert_all_migrated_once()
        for name in LEGACY_SCHEMAS:
            self.assertFalse((self.directory / name).exists(), name)
            self.assertTrue(self.retired(name).is_file(), name)

    def test_crash_in_the_middle_of_the_copy_leaves_nothing_behind_and_retries_whole(self):
        write_legacy(self.directory)
        real_count = helper_state._count
        counted = []

        def interrupted(connection, table):
            counted.append(table)
            if len(counted) > 12:  # Part-way through the second old file's tables.
                raise KeyboardInterrupt
            return real_count(connection, table)

        with patch('helper.state_database._count', interrupted), self.assertRaises(
                KeyboardInterrupt):
            prepare_state(self.state)
        for name in LEGACY_SCHEMAS:
            self.assertTrue((self.directory / name).is_file(), name)
        self.assertEqual(table_counts(self.state), {})
        prepare_state(self.state)
        self.assert_all_migrated_once()

    def test_a_row_count_mismatch_aborts_the_whole_migration(self):
        write_legacy(self.directory)
        with patch('helper.state_database._count', side_effect=[2, 0, 5]):
            with self.assertRaisesRegex(ServiceError, 'state_migration_count_mismatch'):
                prepare_state(self.state)
        self.assertEqual(table_counts(self.state), {})
        self.assertTrue((self.directory / 'journal.db').is_file())

    def test_an_old_column_the_new_schema_lacks_is_refused_not_dropped(self):
        write_legacy(self.directory, ('journal.db',))
        with sqlite3.connect(self.directory / 'journal.db') as connection:
            connection.execute('ALTER TABLE incarnations ADD COLUMN extra TEXT')
        with self.assertRaisesRegex(ServiceError, 'state_migration_schema_mismatch'):
            prepare_state(self.state)
        self.assertTrue((self.directory / 'journal.db').is_file())

    def test_tables_of_the_three_databases_never_share_a_name(self):
        tables = [table for names in LEGACY_TABLES.values() for table in names]
        self.assertEqual(len(tables), len(set(tables)))
        prepare_state(self.state)
        expected = set(tables) | {'schema_migrations', 'legacy_imports'}
        self.assertEqual(set(table_counts(self.state)), expected)
        for name, schema in LEGACY_SCHEMAS.items():
            created = {statement.split()[2].split('(')[0] for statement in schema
                       if statement.startswith('CREATE TABLE')}
            self.assertLessEqual(set(LEGACY_TABLES[name]), created)

    def test_clashing_generic_names_keep_each_databases_own_rows(self):
        write_legacy(self.directory)
        prepare_state(self.state)
        with sqlite3.connect(self.state) as connection:
            self.assertEqual(connection.execute('SELECT action FROM audit').fetchall(),
                             [('forwarding_save',)])
            self.assertEqual(connection.execute('SELECT count(*) FROM recovery_audit')
                             .fetchone()[0], 2)

    def test_unsafe_files_are_refused(self):
        target = self.directory / 'elsewhere.db'
        target.touch(mode=0o600)
        self.state.symlink_to(target)
        with self.assertRaisesRegex(ServiceError, 'unsafe_state_file'):
            prepare_state(self.state)
        self.state.unlink()
        self.state.touch(mode=0o644)
        self.state.chmod(0o644)
        with self.assertRaisesRegex(ServiceError, 'unsafe_state_file'):
            prepare_state(self.state)
        self.state.unlink()
        (self.directory / 'journal.db').symlink_to(target)
        with self.assertRaisesRegex(ServiceError, 'unsafe_state_migration_source'):
            prepare_state(self.state)

    def test_a_newer_schema_version_is_refused(self):
        prepare_state(self.state)
        with sqlite3.connect(self.state) as connection:
            connection.execute('INSERT INTO schema_migrations VALUES(99,0)')
        with self.assertRaisesRegex(ServiceError, 'state_schema_unsupported'):
            prepare_state(self.state)

    def test_opening_a_current_database_does_not_rewrite_it(self):
        prepare_state(self.state)
        before = self.state.stat().st_mtime_ns
        prepare_state(self.state)
        self.assertEqual(self.state.stat().st_mtime_ns, before)
