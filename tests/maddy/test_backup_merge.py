import json
import shutil
import sqlite3
import threading
import unittest
import uuid
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch

from backend.database import Database
from helper.backup_control import BackupControl
from helper.protocol import HelperRequest
from host.backup_control import BackupWorker
from host.backup_merge import merge_rows
from shared.errors import ServiceError
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import RealMaddyFixture, imap_login
from tests.restore_support import FixtureWriters, bodies, deliver

EXISTING, MISSING = "existing@rsalehin24.me", "missing@rsalehin24.me"
SNAPSHOT_ID = "a" * 64


class ContinuousDelivery:
    """Keeps delivering mail while a restore runs, as the live server would."""

    def __init__(self, fixture, email):
        self.fixture, self.email = fixture, email
        self.delivered, self.errors = [], []
        self.stopping = threading.Event()
        self.thread = threading.Thread(target=self.run)

    def run(self):
        while not self.stopping.is_set():
            body = f"live-writer-{len(self.delivered)}"
            try:
                deliver(self.fixture, self.email, body)
            except Exception as error:  # noqa: BLE001 -- reported to the test thread
                self.errors.append(error)
                return
            self.delivered.append(body)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_):
        self.stopping.set()
        self.thread.join(timeout=30)


class BackupMergeTests(unittest.TestCase):
    def prepare(self):
        """A snapshot with two mailboxes, then a live server that lost one and gained mail."""
        fixture = RealMaddyFixture()
        self.addCleanup(fixture.close)
        fixture.start()
        for email in (EXISTING, MISSING):
            fixture.helper.handle(
                HelperRequest("create", str(uuid.uuid4()), email, MAILBOX_PASSWORD)
            )
            deliver(fixture, email, "backup-mail-" + email)
        with imap_login(fixture, EXISTING, MAILBOX_PASSWORD) as connection:
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
            for email in (EXISTING, MISSING):
                connection.execute(
                    "INSERT INTO mailboxes(email,display_name,notes,created,updated,status) "
                    "VALUES(?,?,?,?,?,'ready')",
                    (email, "Name", "Notes", 1000, 1000),
                )
        shutil.copytree(dashboard, snapshot / "dashboard")
        with closing(sqlite3.connect(fixture.data / "imapsql.db")) as connection, connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("DELETE FROM users WHERE username=?", (MISSING,))
            connection.execute("DELETE FROM extKeys WHERE uid NOT IN (SELECT id FROM users)")
            # Remove one backed-up folder message so merge has both existing and absent mail.
            folder = connection.execute("SELECT id FROM mboxes WHERE name='Saved'").fetchone()[0]
            connection.execute("DELETE FROM msgs WHERE mboxId=?", (folder,))
            connection.execute("UPDATE mboxes SET msgsCount=0 WHERE id=?", (folder,))
        with closing(sqlite3.connect(fixture.data / "credentials.db")) as connection, connection:
            connection.execute("DELETE FROM passwords WHERE key=?", (MISSING,))
        with database.transaction() as connection:
            connection.execute("DELETE FROM mailboxes WHERE email=?", (MISSING,))
        fixture.command(("start", fixture.identifier))
        fixture.refresh_ports()
        fixture._wait()
        deliver(fixture, EXISTING, "newer-live-mail")
        return self.worker_for(fixture, snapshot, dashboard)

    def worker_for(self, fixture, snapshot, dashboard):
        root = fixture.root / "backup-control"
        root.mkdir(mode=0o700)
        control = BackupControl(root)
        with control.connect() as connection:
            connection.execute("INSERT INTO snapshots VALUES(?,?,?)", (SNAPSHOT_ID, 1000, 1000))
        writers = FixtureWriters(fixture)
        writers.locks = SimpleNamespace(policy=fixture.helper.dependencies.lock_policy)
        worker = BackupWorker.__new__(BackupWorker)
        worker.root, worker.control = root, control
        worker.staging = fixture.root / "staging"
        worker.staging.mkdir()
        worker.repository = SimpleNamespace(process=SimpleNamespace(reconcile=lambda: None))
        worker.context = SimpleNamespace(
            deployment={
                **fixture.maddy.deployment,
                "maddy_data_directory": str(fixture.data),
                "backup_sources": {"dashboard": str(dashboard)},
            },
            coordination=writers,
            intents=SimpleNamespace(read=lambda: {"consumers": {}}, write=lambda state: None),
            backup=lambda: object(),
        )
        return SimpleNamespace(
            fixture=fixture, snapshot=snapshot, dashboard=dashboard, control=control,
            worker=worker, writers=writers,
        )  # fmt: skip

    def preflight(self, scene, missing, now):
        """Record a finished preflight whose staging directory is the extracted snapshot."""
        prepared = str(uuid.uuid4())
        scene.control.begin((prepared, "preflight", SNAPSHOT_ID, ""), now)
        scene.control.update(
            prepared,
            "complete",
            "complete",
            now + 1,
            result={
                "mailboxes": [EXISTING, MISSING],
                "missing": missing,
                "needs_login": missing,
            },
        )
        (scene.worker.staging / prepared).symlink_to(scene.snapshot)
        return prepared

    def begin_restore(self, scene, prepared, now):
        identifier = str(uuid.uuid4())
        scene.control.begin((identifier, "restore", SNAPSHOT_ID, prepared), now)
        return identifier

    def import_live(self, scene, identifier):
        with patch("host.backup_control.DockerMaddy", return_value=scene.fixture.maddy):
            return scene.worker.import_live(
                scene.control.job(identifier, private=True), scene.snapshot, {"snapshot_id": "s"}
            )

    def reconcile(self, scene, original, post_backup=False):
        scene.control.update(original, "reconciliation_required", "live_import", 2000)
        identifier = str(uuid.uuid4())
        scene.control.begin((identifier, "reconcile", "", original), 2001)
        lock = scene.fixture.root / "backup.lock"
        lock.touch(mode=0o600)
        with (
            patch("host.backup_control.BACKUP_LOCK", lock),
            patch("host.backup_control.DockerMaddy", return_value=scene.fixture.maddy),
            patch("host.backup_control.BackupOperations") as post,
        ):
            result = scene.worker.reconcile(scene.control.job(identifier, private=True))
        self.assertEqual(post.return_value.run.call_count, 1 if post_backup else 0)
        return result

    def planned_keys(self, scene, identifier):
        with scene.control.connect() as connection:
            row = connection.execute(
                "SELECT keys FROM backup_plans WHERE id=?", (identifier,)
            ).fetchone()
        return json.loads(row[0])

    def referenced(self, scene, keys):
        with closing(sqlite3.connect(scene.fixture.data / "imapsql.db")) as connection:
            return [
                key
                for key in keys
                if connection.execute("SELECT 1 FROM extKeys WHERE id=?", (key,)).fetchone()
            ]

    def marker(self, scene, identifier):
        with scene.control.connect() as connection:
            return connection.execute(
                "SELECT 1 FROM backup_imports WHERE id=?", (identifier,)
            ).fetchone()

    def dashboard_emails(self, scene):
        with closing(sqlite3.connect(scene.dashboard / "admin_data.db")) as connection:
            return [row[0] for row in connection.execute("SELECT email FROM mailboxes")]

    def started_at(self, fixture):
        return fixture.command(("inspect", "-f", "{{.State.StartedAt}}", "maddy")).stdout.strip()

    def test_live_merge_keeps_maddy_running_preserves_newer_mail_and_is_repeatable(self):
        scene = self.prepare()
        fixture = scene.fixture
        prepared = self.preflight(scene, [MISSING], 1000)
        jobid = self.begin_restore(scene, prepared, 1002)
        started = self.started_at(fixture)
        with ContinuousDelivery(fixture, EXISTING) as writer:
            result = self.import_live(scene, jobid)
        self.assertEqual(writer.errors, [])
        self.assertTrue(writer.delivered)
        self.assertEqual(self.started_at(fixture), started)
        self.assertEqual(scene.writers.events.count("stopped"), 0)
        self.assertEqual(result["messages_added"], 2)
        scene.control.update(jobid, "complete", "complete", 1003)
        # Maddy hashes the password itself; no hash or password passes through the job record.
        new_password = "new missing mailbox password 2026"
        with patch("host.backup_control.DockerMaddy", return_value=fixture.maddy):
            scene.worker.create_logins({MISSING: new_password})
        with imap_login(fixture, EXISTING, MAILBOX_PASSWORD) as connection:
            text = bodies(connection)
            self.assertIn(b"newer-live-mail", text)
            self.assertIn(writer.delivered[-1].encode(), text)
            self.assertEqual(text.count(b"backup-mail-"), 1)
            self.assertEqual(connection.select("Saved")[0], "OK")
            self.assertIn(b"folder-body", connection.fetch("1", "(BODY.PEEK[])")[1][0][1])
            self.assertIn(b"custom", connection.fetch("1", "(FLAGS)")[1][0])
        with imap_login(fixture, MISSING, new_password) as connection:
            self.assertIn(b"backup-mail-", bodies(connection))
        self.assertIn(MISSING, self.dashboard_emails(scene))
        self.assertIsNotNone(self.marker(scene, jobid))
        # A second independently confirmed restore must not duplicate messages.
        prepared2 = self.preflight(scene, [], 1004)
        jobid2 = self.begin_restore(scene, prepared2, 1006)
        self.assertEqual(self.import_live(scene, jobid2)["messages_added"], 0)
        with imap_login(fixture, MISSING, new_password) as connection:
            self.assertEqual(bodies(connection).count(b"backup-mail-"), 1)

    def test_a_crash_between_the_mail_merge_and_its_record_is_finished_by_reconcile(self):
        scene = self.prepare()
        prepared = self.preflight(scene, [MISSING], 1000)
        jobid = self.begin_restore(scene, prepared, 1002)
        with (
            patch.object(
                scene.worker, "record_import", side_effect=ServiceError("synthetic_crash", 503)
            ),
            self.assertRaisesRegex(ServiceError, "synthetic_crash"),
        ):
            self.import_live(scene, jobid)
        keys = self.planned_keys(scene, jobid)
        self.assertTrue(self.referenced(scene, keys))
        self.assertIsNone(self.marker(scene, jobid))
        self.assertNotIn(MISSING, self.dashboard_emails(scene))
        reconciled = self.reconcile(scene, jobid, post_backup=True)
        self.assertTrue(reconciled["committed"])
        self.assertIsNotNone(self.marker(scene, jobid))
        self.assertIn(MISSING, self.dashboard_emails(scene))
        self.assertEqual(scene.control.job(jobid)["state"], "complete")
        for key in self.referenced(scene, keys):
            self.assertTrue((scene.fixture.data / "messages" / key).exists())
        with patch("host.backup_control.DockerMaddy", return_value=scene.fixture.maddy):
            scene.worker.create_logins({MISSING: "new missing mailbox password 2026"})
        with imap_login(scene.fixture, MISSING, "new missing mailbox password 2026") as connection:
            self.assertIn(b"backup-mail-", bodies(connection))

    def test_a_crash_after_the_record_commits_reconciles_as_complete_without_replay(self):
        scene = self.prepare()
        prepared = self.preflight(scene, [MISSING], 1000)
        jobid = self.begin_restore(scene, prepared, 1002)
        with (
            patch.object(
                scene.worker, "remove_unreferenced", side_effect=ServiceError("late_crash", 503)
            ),
            self.assertRaisesRegex(ServiceError, "late_crash"),
        ):
            self.import_live(scene, jobid)
        self.assertIsNotNone(self.marker(scene, jobid))
        reconciled = self.reconcile(scene, jobid, post_backup=True)
        self.assertTrue(reconciled["committed"])
        self.assertEqual(scene.control.job(jobid)["state"], "complete")

    def test_a_crash_before_the_merge_commits_is_rolled_back_by_reconcile(self):
        scene = self.prepare()
        prepared = self.preflight(scene, [MISSING], 1000)
        jobid = self.begin_restore(scene, prepared, 1002)

        def interrupted_rows(connection, *arguments):
            merge_rows(connection, *arguments)
            raise ServiceError("synthetic_import_interruption", 503)

        with (
            patch("host.backup_merge.merge_rows", side_effect=interrupted_rows),
            self.assertRaisesRegex(ServiceError, "synthetic_import_interruption"),
        ):
            self.import_live(scene, jobid)
        keys = self.planned_keys(scene, jobid)
        self.assertEqual(self.referenced(scene, keys), [])
        self.assertTrue(all((scene.fixture.data / "messages" / key).exists() for key in keys))
        unrelated = scene.fixture.data / "messages" / "unrelated-unreferenced-file"
        unrelated.write_bytes(b"preserve me")
        reconciled = self.reconcile(scene, jobid)
        self.assertFalse(reconciled["committed"])
        self.assertEqual(scene.control.job(jobid)["state"], "failed")
        self.assertEqual(unrelated.read_bytes(), b"preserve me")
        self.assertTrue(all(not (scene.fixture.data / "messages" / key).exists() for key in keys))
        self.assertIsNone(self.marker(scene, jobid))
        self.assertNotIn(MISSING, self.dashboard_emails(scene))
        with imap_login(scene.fixture, EXISTING, MAILBOX_PASSWORD) as connection:
            self.assertIn(b"newer-live-mail", bodies(connection))
