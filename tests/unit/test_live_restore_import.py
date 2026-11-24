import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from helper.backup_control import BackupControl
from host.backup_control import BackupWorker

JOB = "00000000-0000-4000-8000-000000000001"
PREVIOUS = "00000000-0000-4000-8000-000000000002"
EMAIL = "missing@rsalehin24.me"
KEYS = ["key-one", "key-two"]


def execute(path: Path, *statements: str) -> None:
    with closing(sqlite3.connect(path)) as connection, connection:
        for statement in statements:
            connection.execute(statement)


class LiveRestoreImportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.data = self.root / "data"
        (self.data / "messages").mkdir(parents=True)
        self.live = self.data / "imapsql.db"
        execute(
            self.live,
            "PRAGMA journal_mode=WAL",
            "CREATE TABLE extKeys(id TEXT PRIMARY KEY)",
            "CREATE TABLE users(id INTEGER PRIMARY KEY,username TEXT)",
        )
        self.dashboard = self.root / "dashboard" / "admin_data.db"
        self.dashboard.parent.mkdir()
        execute(
            self.dashboard,
            "CREATE TABLE mailboxes(email TEXT PRIMARY KEY,display_name,notes,created,"
            "updated,status)",
        )
        self.state = self.root / "state.db"
        execute(
            self.state,
            "CREATE TABLE incarnations(email TEXT PRIMARY KEY,generation,created,present)",
        )
        self.target = self.root / "staging" / PREVIOUS
        (self.target / "dashboard").mkdir(parents=True)
        execute(
            self.target / "dashboard/admin_data.db",
            "CREATE TABLE mailboxes(email,display_name,notes,created)",
            f"INSERT INTO mailboxes VALUES('{EMAIL}','Name','Notes',1000)",
        )
        (self.root / "control").mkdir()
        self.control = BackupControl(self.root / "control")
        self.worker = BackupWorker.__new__(BackupWorker)
        self.worker.control, self.worker.staging = self.control, self.root / "staging"
        self.worker.context = SimpleNamespace(
            deployment={
                "maddy_data_directory": str(self.data),
                "backup_sources": {"dashboard": str(self.dashboard.parent)},
            },
            coordination=SimpleNamespace(journal=SimpleNamespace(path=self.state)),
        )
        with self.control.connect() as ledger:
            ledger.execute("INSERT INTO snapshots VALUES(?,?,?)", ("a" * 64, 1.0, 1.0))
        self.control.begin((PREVIOUS, "preflight", "a" * 64, ""), 1.0)
        self.control.update(PREVIOUS, "complete", "complete", 2.0, result={"missing": [EMAIL]})
        self.control.begin((JOB, "restore", "a" * 64, PREVIOUS), 3.0)
        self.original = self.control.job(JOB, private=True)

    def plan(self, keys=KEYS):
        with self.control.connect() as ledger:
            ledger.execute("INSERT INTO backup_plans VALUES(?,?)", (JOB, json.dumps(keys)))
        for key in keys:
            (self.data / "messages" / key).write_bytes(b"body")

    def count(self, path: Path, table: str) -> int:
        with closing(sqlite3.connect(path)) as connection:
            return connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]  # noqa: S608

    def test_merge_needs_no_exclusive_access_while_another_connection_reads_and_writes(self):
        reader = sqlite3.connect(self.live)
        self.addCleanup(reader.close)
        reader.execute("BEGIN")
        reader.execute("SELECT COUNT(*) FROM extKeys").fetchone()

        def merge(live, *_):
            live.execute("INSERT INTO extKeys VALUES('key-one')")
            return 1

        with patch("host.backup_merge.merge_rows", merge):
            self.assertEqual(self.worker.merge_live(self.target, {}), 1)
        self.assertEqual(reader.execute("SELECT COUNT(*) FROM extKeys").fetchone()[0], 0)
        reader.rollback()
        self.assertEqual(self.count(self.live, "extKeys"), 1)
        mode = sqlite3.connect(self.live).execute("PRAGMA journal_mode").fetchone()[0]
        self.assertEqual(mode, "wal")

    def test_a_writer_holding_the_database_makes_the_merge_fail_without_changes(self):
        writer = sqlite3.connect(self.live, isolation_level=None)
        self.addCleanup(writer.close)
        writer.execute("BEGIN IMMEDIATE")
        with (
            patch("host.backup_control.LIVE_WRITE_WAIT_SECONDS", 0.05),
            patch("host.backup_merge.merge_rows") as merge,
            self.assertRaises(sqlite3.OperationalError),
        ):
            self.worker.merge_live(self.target, {})
        merge.assert_not_called()

    def test_a_failed_merge_rolls_back_every_row(self):
        def merge(live, *_):
            live.execute("INSERT INTO extKeys VALUES('key-one')")
            raise sqlite3.IntegrityError("synthetic")

        with patch("host.backup_merge.merge_rows", merge), self.assertRaises(sqlite3.Error):
            self.worker.merge_live(self.target, {})
        self.assertEqual(self.count(self.live, "extKeys"), 0)

    def test_record_import_writes_the_marker_and_rows_and_is_repeatable(self):
        for _ in range(2):
            self.worker.record_import(self.original, {EMAIL}, self.target)
        self.assertEqual(self.count(self.control.path, "backup_imports"), 1)
        self.assertEqual(self.count(self.dashboard, "mailboxes"), 1)
        self.assertEqual(self.count(self.state, "incarnations"), 1)

    def test_record_import_rolls_back_the_marker_when_metadata_is_missing(self):
        with self.assertRaises(Exception) as caught:
            self.worker.record_import(self.original, {"other@rsalehin24.me"}, self.target)
        self.assertEqual(caught.exception.code, "mailbox_metadata_missing")
        self.assertEqual(self.count(self.control.path, "backup_imports"), 0)

    def test_marker_alone_means_committed(self):
        self.plan()
        self.worker.record_import(self.original, {EMAIL}, self.target)
        self.assertTrue(self.worker.import_committed(self.original))

    def test_referenced_keys_without_marker_are_finished_and_no_file_is_lost(self):
        self.plan()
        execute(self.live, "INSERT INTO extKeys VALUES('key-one')")
        self.assertTrue(self.worker.import_committed(self.original))
        self.assertEqual(self.count(self.control.path, "backup_imports"), 1)
        self.assertEqual(self.count(self.dashboard, "mailboxes"), 1)
        self.assertTrue((self.data / "messages/key-one").exists())
        self.assertFalse((self.data / "messages/key-two").exists())

    def test_unreferenced_keys_without_marker_roll_back_only_the_staged_files(self):
        self.plan()
        unrelated = self.data / "messages/unrelated"
        unrelated.write_bytes(b"keep")
        self.assertFalse(self.worker.import_committed(self.original))
        self.assertEqual(self.count(self.control.path, "backup_imports"), 0)
        self.assertEqual(self.count(self.dashboard, "mailboxes"), 0)
        self.assertEqual(list((self.data / "messages").iterdir()), [unrelated])

    def test_a_recreated_empty_mailbox_proves_the_merge_committed_without_any_body_keys(self):
        self.plan([])
        execute(self.live, f"INSERT INTO users(username) VALUES('{EMAIL}')")
        self.assertTrue(self.worker.import_committed(self.original))
        self.assertEqual(self.count(self.control.path, "backup_imports"), 1)
        self.assertEqual(self.count(self.dashboard, "mailboxes"), 1)

    def test_a_restore_that_never_staged_anything_is_not_committed(self):
        self.assertFalse(self.worker.import_committed(self.original))
