import shutil
import sqlite3
import unittest
import uuid
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch

from backend.database import Database
from backend.routes_backups import hash_password
from helper.backup_control import BackupControl
from helper.protocol import HelperRequest
from host.backup_control import BackupWorker
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import RealMaddyFixture, imap_login
from tests.restore_support import FixtureWriters, bodies, deliver


class BackupMergeTests(unittest.TestCase):
    def test_live_merge_all_mailboxes_preserves_newer_mail_recreates_missing_and_is_repeatable(
        self,
    ):
        fixture = RealMaddyFixture()
        self.addCleanup(fixture.close)
        fixture.start()
        existing, missing = "existing@rsalehin24.me", "missing@rsalehin24.me"
        for email in (existing, missing):
            fixture.helper.handle(
                HelperRequest("create", str(uuid.uuid4()), email, MAILBOX_PASSWORD)
            )
            deliver(fixture, email, "backup-mail-" + email)
        with imap_login(fixture, existing, MAILBOX_PASSWORD) as connection:
            connection.create("Saved")
            connection.append(
                "Saved",
                "(\\Seen \\Flagged custom)",
                None,
                b"Subject: folder fixture\r\n\r\nfolder-body\r\n",
            )
        fixture.command(("stop", fixture.identifier))
        snapshot = fixture.root / "snapshot"
        snapshot.mkdir()
        shutil.copytree(fixture.data, snapshot / "maddy")
        dashboard = fixture.root / "dashboard"
        dashboard.mkdir(mode=0o700)
        database = Database.provision(dashboard / "admin_data.db", str(uuid.uuid4()))
        with database.transaction() as connection:
            for email in (existing, missing):
                connection.execute(
                    "INSERT INTO mailboxes(email,display_name,notes,created,updated,status) "
                    "VALUES(?,?,?,?,?,'ready')",
                    (email, "Name", "Notes", 1000, 1000),
                )
        shutil.copytree(dashboard, snapshot / "dashboard")
        with closing(sqlite3.connect(fixture.data / "imapsql.db")) as connection, connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("DELETE FROM users WHERE username=?", (missing,))
            connection.execute("DELETE FROM extKeys WHERE uid NOT IN (SELECT id FROM users)")
            # Remove one backed-up folder message so merge has both existing and absent mail.
            folder = connection.execute("SELECT id FROM mboxes WHERE name='Saved'").fetchone()[0]
            connection.execute("DELETE FROM msgs WHERE mboxId=?", (folder,))
            connection.execute("UPDATE mboxes SET msgsCount=0 WHERE id=?", (folder,))
        with closing(sqlite3.connect(fixture.data / "credentials.db")) as connection, connection:
            connection.execute("DELETE FROM passwords WHERE key=?", (missing,))
        with database.transaction() as connection:
            connection.execute("DELETE FROM mailboxes WHERE email=?", (missing,))
        fixture.command(("start", fixture.identifier))
        fixture.refresh_ports()
        fixture._wait()
        deliver(fixture, existing, "newer-live-mail")
        root = fixture.root / "backup-control"
        root.mkdir(mode=0o700)
        control = BackupControl(root)
        prepared = str(uuid.uuid4())
        snapshot_id = "a" * 64
        values = {
            "kind": "preflight",
            "snapshot": snapshot_id,
            "previous": "",
            "confirmation": snapshot_id,
            "acknowledged": True,
            "password_hashes": {},
        }
        with control.connect() as connection:
            connection.execute("INSERT INTO snapshots VALUES(?,?,?)", (snapshot_id, 1000, 1000))
        control.handle("backup_submit", prepared, values, 1000)
        control.update(
            prepared,
            "complete",
            "complete",
            1001,
            result={"mailboxes": [existing, missing], "missing": [missing]},
        )
        writers = FixtureWriters(fixture)
        worker = BackupWorker.__new__(BackupWorker)
        worker.root, worker.control = root, control
        worker.context = SimpleNamespace(
            deployment={
                **fixture.maddy.deployment,
                "maddy_data_directory": str(fixture.data),
                "backup_sources": {"dashboard": str(dashboard)},
            },
            coordination=writers,
            generations=SimpleNamespace(),
        )
        new_password = "new missing mailbox password 2026"
        jobid = str(uuid.uuid4())
        values.update(
            kind="restore",
            previous=prepared,
            password_hashes={missing: hash_password(new_password)},
        )
        control.handle("backup_submit", jobid, values, 1002)
        with (
            patch("host.backup_control.DockerMaddy", return_value=fixture.maddy),
            patch("host.backup_control.DockerSnapshots", return_value=writers),
        ):
            result = worker.import_live(
                control.job(jobid, private=True), snapshot, {"snapshot_id": "safety"}
            )
        self.assertEqual(result["messages_added"], 2)
        control.update(jobid, "complete", "complete", 1003)
        with imap_login(fixture, existing, MAILBOX_PASSWORD) as connection:
            text = bodies(connection)
            self.assertIn(b"newer-live-mail", text)
            self.assertEqual(text.count(b"backup-mail-"), 1)
            self.assertEqual(connection.select("Saved")[0], "OK")
            self.assertIn(b"folder-body", connection.fetch("1", "(BODY.PEEK[])")[1][0][1])
            self.assertIn(b"custom", connection.fetch("1", "(FLAGS)")[1][0])
        with imap_login(fixture, missing, new_password) as connection:
            self.assertIn(b"backup-mail-", bodies(connection))
        # A second independently confirmed restore must not duplicate messages.
        prepared2 = str(uuid.uuid4())
        values.update(kind="preflight", previous="", password_hashes={})
        control.handle("backup_submit", prepared2, values, 1004)
        control.update(
            prepared2,
            "complete",
            "complete",
            1005,
            result={"mailboxes": [existing, missing], "missing": []},
        )
        jobid2 = str(uuid.uuid4())
        values.update(kind="restore", previous=prepared2)
        control.handle("backup_submit", jobid2, values, 1006)
        with (
            patch("host.backup_control.DockerMaddy", return_value=fixture.maddy),
            patch("host.backup_control.DockerSnapshots", return_value=writers),
        ):
            result = worker.import_live(
                control.job(jobid2, private=True), snapshot, {"snapshot_id": "safety-2"}
            )
        self.assertEqual(result["messages_added"], 0)
        control.update(jobid2, "complete", "complete", 1007)
        # Interrupt after attached-database changes: all databases must roll back.
        from host.backup_merge import merge_rows
        from shared.errors import ServiceError

        control_id = str(uuid.uuid4())
        values.update(kind="restore", previous=prepared2)
        control.handle("backup_submit", control_id, values, 1008)

        def interrupted_rows(connection, *args):
            merge_rows(connection, *args)
            connection.execute("UPDATE credentials.passwords SET value='invalid-fixture-value'")
            raise ServiceError("synthetic_import_interruption", 503)

        with (
            patch("host.backup_control.DockerMaddy", return_value=fixture.maddy),
            patch("host.backup_control.DockerSnapshots", return_value=writers),
            patch("host.backup_merge.merge_rows", side_effect=interrupted_rows),
        ):
            with self.assertRaisesRegex(ServiceError, "synthetic_import_interruption"):
                worker.import_live(control.job(control_id, private=True), snapshot, {})
        with imap_login(fixture, existing, MAILBOX_PASSWORD) as connection:
            self.assertIn(b"newer-live-mail", bodies(connection))
        with control.connect() as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT 1 FROM backup_imports WHERE id=?", (control_id,)
                ).fetchone()
            )
        control.update(control_id, "reconciliation_required", "resuming_mail", 1009)
        worker.context.generations = SimpleNamespace(
            read=lambda: {"consumers": {}}, write=lambda state: None
        )
        writers.locks = SimpleNamespace(policy=fixture.helper.dependencies.lock_policy)
        worker.repository = SimpleNamespace(process=SimpleNamespace(reconcile=lambda: None))
        backup_lock = fixture.root / "backup.lock"
        backup_lock.touch(mode=0o600)
        (root / "recovery-worker.lock").touch(mode=0o600)
        unrelated = fixture.data / "messages" / "unrelated-unreferenced-file"
        unrelated.write_bytes(b"preserve me")
        reconcile_id = str(uuid.uuid4())
        values.update(kind="reconcile", previous=control_id)
        control.handle("backup_submit", reconcile_id, values, 1010)
        with (
            patch("host.backup_control.BACKUP_LOCK", backup_lock),
            patch("host.backup_control.DockerMaddy", return_value=fixture.maddy),
            patch("host.backup_control.DockerSnapshots", return_value=writers),
        ):
            reconciled = worker.reconcile(control.job(reconcile_id, private=True))
        self.assertFalse(reconciled["committed"])
        self.assertEqual(control.job(control_id)["state"], "failed")
        self.assertEqual(unrelated.read_bytes(), b"preserve me")
        with control.connect() as connection:
            import json

            keys = json.loads(
                connection.execute(
                    "SELECT keys FROM backup_plans WHERE id=?", (control_id,)
                ).fetchone()[0]
            )
        self.assertTrue(all(not (fixture.data / "messages" / key).exists() for key in keys))
        control.update(reconcile_id, "complete", "complete", 1011)
        # A response lost after COMMIT must reconcile as complete, without replay.
        committed_id = str(uuid.uuid4())
        values.update(kind="restore", previous=prepared2)
        control.handle("backup_submit", committed_id, values, 1012)
        real_resume = writers.resume

        def interrupted_resume(names):
            real_resume(names)
            raise ServiceError("synthetic_after_commit", 503)

        with (
            patch("host.backup_control.DockerMaddy", return_value=fixture.maddy),
            patch("host.backup_control.DockerSnapshots", return_value=writers),
            patch.object(writers, "resume", side_effect=interrupted_resume),
        ):
            with self.assertRaisesRegex(ServiceError, "synthetic_after_commit"):
                worker.import_live(control.job(committed_id, private=True), snapshot, {})
        control.update(committed_id, "reconciliation_required", "resuming_mail", 1013)
        values.update(kind="reconcile", previous=committed_id)
        reconcile_id2 = str(uuid.uuid4())
        control.handle("backup_submit", reconcile_id2, values, 1014)
        worker.context.backup = lambda: object()
        with (
            patch("host.backup_control.BACKUP_LOCK", backup_lock),
            patch("host.backup_control.DockerMaddy", return_value=fixture.maddy),
            patch("host.backup_control.DockerSnapshots", return_value=writers),
        ):
            with patch("host.backup_control.BackupOperations") as post_backup:
                reconciled = worker.reconcile(control.job(reconcile_id2, private=True))
                post_backup.return_value.run.assert_called_once()
        self.assertTrue(reconciled["committed"])
        self.assertEqual(control.job(committed_id)["state"], "complete")
