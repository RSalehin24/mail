"""Backups copy a running Maddy: consistent SQLite snapshots, tolerated spool churn."""

import os
import shutil
import sqlite3
import tempfile
import time
import unittest
from contextlib import closing, nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from host import live_copy
from host.backup import BackupPaths, CaptureDependencies, SnapshotCapture
from shared.errors import ServiceError

SCHEMA = (
    "CREATE TABLE extKeys (id TEXT PRIMARY KEY, uid INTEGER, refs INTEGER)",
    "CREATE TABLE ledger (value INTEGER)",
)


def create_wal_database(path: Path, keys: tuple[str, ...] = ()) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode=WAL")
    for statement in SCHEMA:
        connection.execute(statement)
    connection.executemany("INSERT INTO extKeys VALUES (?, 1, 1)", [(key,) for key in keys])
    connection.commit()
    return connection


class LiveCaptureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.source, self.staging = self.root / "maddy", self.root / "staging"
        self.source.mkdir()
        self.staging.mkdir()

    def capture(self) -> Path:
        services = SimpleNamespace(inventories=lambda: {"credentials": [], "storage": []})
        scope = SimpleNamespace(consistent=nullcontext, journal=None)
        paths = BackupPaths(self.staging, self.root / "manifest", {"maddy": self.source})
        return SnapshotCapture(paths, CaptureDependencies(scope, services, time.time)).capture()

    def test_wal_database_written_during_capture_is_one_consistent_snapshot(self):
        writer = create_wal_database(self.source / "state.db")
        self.addCleanup(writer.close)
        writer.executemany("INSERT INTO ledger VALUES (?)", [(n,) for n in range(200)])
        writer.commit()
        original = live_copy.snapshot_database

        def snapshot_while_writing(source, target):
            writer.execute("INSERT INTO ledger VALUES (-1)")
            writer.commit()
            original(source, target)
            writer.execute("INSERT INTO ledger VALUES (-2)")
            writer.commit()

        with patch.object(live_copy, "snapshot_database", snapshot_while_writing):
            snapshot = self.capture()
        copied = snapshot / "maddy"
        with closing(sqlite3.connect(copied / "state.db")) as connection:
            self.assertEqual(connection.execute("PRAGMA integrity_check").fetchone()[0], "ok")
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM ledger").fetchone()[0], 201)
        self.assertEqual(sorted(path.name for path in copied.iterdir()), ["state.db"])

    def test_wal_side_files_are_skipped_but_other_files_are_copied(self):
        writer = create_wal_database(self.source / "state.db")
        self.addCleanup(writer.close)
        writer.execute("INSERT INTO ledger VALUES (1)")
        writer.commit()
        self.assertTrue((self.source / "state.db-wal").exists())
        (self.source / "notes.txt").write_text("kept")
        (self.source / "not-sqlite.db").write_text("plain text named like a database")
        copied = self.capture() / "maddy"
        self.assertEqual(
            sorted(path.name for path in copied.iterdir()),
            ["not-sqlite.db", "notes.txt", "state.db"],
        )
        self.assertEqual((copied / "not-sqlite.db").read_text(), "plain text named like a database")

    def test_spool_file_that_vanishes_during_copy_does_not_fail_capture(self):
        spool = self.source / "submission-queue"
        spool.mkdir()
        (spool / "stays").write_text("queued")
        (spool / "vanishes").write_text("delivered meanwhile")
        original = shutil.copy2

        def copy_after_delivery(source, target, **keywords):
            if Path(source).name == "vanishes":
                Path(source).unlink()
            return original(source, target, **keywords)

        with patch.object(live_copy.shutil, "copy2", copy_after_delivery):
            copied = self.capture() / "maddy/submission-queue"
        self.assertEqual([path.name for path in copied.iterdir()], ["stays"])

    def test_ownership_survives_for_copied_tree(self):
        (self.source / "file").write_text("x")
        copied = self.capture() / "maddy"
        self.assertEqual((copied / "file").stat().st_uid, os.getuid())


class MailStoreCaptureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.source, self.target = Path(directory.name, "maddy"), Path(directory.name, "copy")
        (self.source / "messages").mkdir(parents=True)
        for key in ("one", "two"):
            (self.source / "messages" / key).write_text(key)
        connection = create_wal_database(self.source / "imapsql.db", ("one", "two"))
        self.addCleanup(connection.close)
        self.index = connection

    def test_complete_copy_is_accepted_on_the_first_attempt(self):
        live_copy.copy_mail_store(self.source, self.target)
        self.assertEqual(sorted(path.name for path in (self.target / "messages").iterdir()),
                         ["one", "two"])

    def test_body_expunged_after_the_index_snapshot_triggers_one_more_attempt(self):
        original, attempts = live_copy.snapshot_database, []

        def snapshot_then_expunge(source, target):
            original(source, target)
            attempts.append(target)
            if len(attempts) == 1:
                (self.source / "messages" / "two").unlink()
                self.index.execute("DELETE FROM extKeys WHERE id='two'")
                self.index.commit()

        with patch.object(live_copy, "snapshot_database", snapshot_then_expunge):
            live_copy.copy_mail_store(self.source, self.target)
        self.assertEqual(len(attempts), 2)
        self.assertEqual([path.name for path in (self.target / "messages").iterdir()], ["one"])

    def test_persistently_missing_body_raises_after_three_attempts(self):
        (self.source / "messages" / "two").unlink()
        with patch.object(
            live_copy, "snapshot_database", wraps=live_copy.snapshot_database
        ) as snapshot, self.assertRaisesRegex(ServiceError, "backup_capture_inconsistent"):
            live_copy.copy_mail_store(self.source, self.target)
        self.assertEqual(snapshot.call_count, 3)

    def test_source_without_message_bodies_skips_the_consistency_check(self):
        shutil.rmtree(self.source / "messages")
        self.assertFalse(live_copy.has_mail_store(self.source))
