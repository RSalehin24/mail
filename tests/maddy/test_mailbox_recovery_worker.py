import base64
import os
import sqlite3
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import patch

from argon2.low_level import Type, hash_secret_raw

from backend.database import Database
from helper.protocol import HelperRequest
from host.mailbox_recovery import MailboxRecovery
from shared.errors import ServiceError
from shared.files import atomic_json
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import RealMaddyFixture, imap_login
from tests.recovery_support import qualify_fixture
from tests.restore_support import FixtureWriters, bodies, deliver


class MailboxRecoveryWorkerTests(unittest.TestCase):
    def test_encrypted_delete_restore_new_password_and_unrelated_mailbox(self):
        fixture = RealMaddyFixture()
        self.addCleanup(fixture.close)
        fixture.start()
        email, other = "recover@rsalehin24.me", "other@rsalehin24.me"
        for account in (email, other):
            fixture.helper.handle(
                HelperRequest("create", str(uuid.uuid4()), account, MAILBOX_PASSWORD)
            )
        deliver(fixture, email, "encrypted-recovery-mail")
        deliver(fixture, other, "unrelated-mail-preserved")
        dashboard = fixture.root / "dashboard"
        dashboard.mkdir(mode=0o700)
        database = Database.provision(dashboard / "admin_data.db", str(uuid.uuid4()))
        with database.transaction() as connection:
            for account in (email, other):
                connection.execute(
                    "INSERT INTO mailboxes "
                    "(email,display_name,notes,created,updated,status) VALUES(?,?,?,?,?,?)",
                    (account, "Mailbox name", "Mailbox notes", 1000, 1000, "ready"),
                )
        root = fixture.root / "recovery"
        root.mkdir(mode=0o700)
        (root / "recovery-worker.lock").touch(mode=0o600)
        writers = FixtureWriters(fixture)
        context = SimpleNamespace(
            deployment={
                **fixture.maddy.deployment,
                "maddy_data_directory": str(fixture.data),
                "maddy_message_paths": ["messages"],
                "backup_sources": {"dashboard": str(dashboard)},
            },
            docker=SimpleNamespace(),
            generations=SimpleNamespace(),
            coordination=writers,
        )
        worker = MailboxRecovery(context, root)
        worker.maddy, worker.writers = fixture.maddy, writers
        policy = {
            "schema_version": 1,
            "revision": 1,
            "enabled": True,
            "permanent_deletion_enabled": True,
            "default_retention": "7_days",
            "protected": {other: "Operational mailbox"},
        }
        atomic_json(root / "recovery-policy.json", policy)
        qualify_fixture(root)
        identifier = str(uuid.uuid4())
        values = {
            "email": other,
            "confirmation": other,
            "acknowledged": True,
            "retention": "7_days",
            "policy_revision": 1,
            "permanent": False,
            "metadata": {"display_name": "", "notes": "", "created": 1000},
        }
        with self.assertRaisesRegex(ServiceError, "protected_recipient"):
            worker.control.handle("recovery_delete", identifier, values, 1000)
        values.update(email=email, confirmation=email)
        orphaned = str(uuid.uuid4())
        worker.control.handle('recovery_delete', orphaned, values, 999)
        worker.records.claim(999)
        worker.records.claim(1000)  # New worker observes an interruption before begin.
        pending = worker.control.handle('recovery_list', str(uuid.uuid4()), {'cursor': ''}, 1000)
        self.assertTrue(pending['mailboxes'][0]['deletion_pending'])
        cancelled = str(uuid.uuid4())
        worker.control.handle('recovery_reconcile', cancelled, {
            'record_id': orphaned, 'confirmation': email, 'acknowledged': True,
        }, 1000)
        worker.run()
        self.assertEqual(worker.records.job(cancelled)['state'], 'complete')
        self.assertEqual(worker.records.job(orphaned)['state'], 'failed')
        self.assertIn(email, fixture.maddy.inventories()['storage'])
        worker.control.handle("recovery_delete", identifier, values, 1000)
        worker.run()
        self.assertEqual(worker.records.job(identifier)["state"], "complete")
        self.assertEqual(worker.records.get(identifier)["state"], "deleted")
        self.assertNotIn(email, fixture.maddy.inventories()["storage"])
        self.assertTrue((root / "recovery-points" / (identifier + ".enc")).is_file())
        with imap_login(fixture, other, MAILBOX_PASSWORD) as connection:
            self.assertIn(b"unrelated-mail-preserved", bodies(connection))
        # Losing a key must never silently create a replacement over retained points.
        from host.mailbox_archive import MailboxArchives
        key_path = root / "recovery.key"
        retained_key = key_path.read_bytes()
        key_path.unlink()
        with self.assertRaisesRegex(ServiceError, "recovery_key_missing"):
            MailboxArchives(root / "recovery-points", key_path)
        self.assertFalse(key_path.exists())
        key_path.write_bytes(retained_key)
        key_path.chmod(0o600)
        encrypted_path = root / 'recovery-points' / (identifier + '.enc')
        encrypted = encrypted_path.read_bytes()
        encrypted_path.write_bytes(encrypted[:-1] + bytes([encrypted[-1] ^ 1]))
        self.assertFalse(worker.archives.available(identifier, email))
        encrypted_path.write_bytes(encrypted)
        key_path.write_bytes(bytes(32))
        wrong_key = MailboxArchives(root / 'recovery-points', key_path)
        self.assertFalse(wrong_key.available(identifier, email))
        key_path.write_bytes(retained_key)
        new_password = "restored mailbox password 24"
        salt = os.urandom(16)
        hashed = hash_secret_raw(new_password.encode(), salt, 3, 19456, 1, 64, Type.ID)
        value = (
            "argon2:3:19456:1:"
            + base64.b64encode(salt).decode()
            + ":"
            + (base64.b64encode(hashed).decode())
        )
        restore_id = str(uuid.uuid4())
        worker.control.handle(
            "recovery_restore",
            restore_id,
            {
                "record_id": identifier,
                "confirmation": email,
                "acknowledged": True,
                "password_hash": value,
            },
            1001,
        )
        worker.run()
        self.assertEqual(worker.records.job(restore_id)["state"], "complete")
        with imap_login(fixture, email, new_password) as connection:
            self.assertIn(b"encrypted-recovery-mail", bodies(connection))
        with sqlite3.connect(dashboard / "admin_data.db") as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT display_name,notes,created FROM mailboxes WHERE email=?", (email,)
                ).fetchone(),
                ("Mailbox name", "Mailbox notes", 1000),
            )
        self.assertNotIn(MAILBOX_PASSWORD.encode(), (root / "recovery.db").read_bytes())
        self.assertNotIn(new_password.encode(), (root / "recovery.db").read_bytes())
        # A policy change disabling deletion does not block recovery of retained mail.
        self.assertEqual(worker.records.get(identifier)["state"], "restored")
        # Failure before commit cannot leave partial credentials/metadata. A new
        # worker resumes only the recorded operation's staged body names.
        context.generations = SimpleNamespace(read=lambda: {"consumers": {"maddy": {}}})
        context.docker = SimpleNamespace(inspect=lambda name: {})
        from host.mailbox_store import import_rows as real_import
        from host.mailbox_store import stage_bodies as real_stage
        for phase in ('bodies', 'rows', 'metadata', 'committed'):
            with self.subTest(interrupted_phase=phase):
                deleted_id = str(uuid.uuid4())
                worker.control.handle('recovery_delete', deleted_id, values, 1002)
                worker.run()
                self.assertEqual(worker.records.get(deleted_id)['state'], 'deleted')
                operation_id = str(uuid.uuid4())
                worker.control.handle('recovery_restore', operation_id, {
                    'record_id': deleted_id, 'confirmation': email, 'acknowledged': True,
                    'password_hash': value,
                }, 1003)
                real_metadata, real_resume = worker._restore_metadata, writers.resume

                def interrupt_after(function):
                    def interrupted(*args, **kwargs):
                        function(*args, **kwargs)
                        raise ServiceError('synthetic_phase_interruption')
                    return interrupted

                targets = {
                    'bodies': ('host.mailbox_recovery.stage_bodies', real_stage),
                    'rows': ('host.mailbox_recovery.import_rows', real_import),
                    'metadata': (None, real_metadata), 'committed': (None, real_resume),
                }
                target, original = targets[phase]
                interruption = (patch(target, side_effect=interrupt_after(original)) if target
                                else patch.object(worker if phase == 'metadata' else writers,
                                    '_restore_metadata' if phase == 'metadata' else 'resume',
                                    side_effect=interrupt_after(original)))
                with interruption:
                    worker.run()
                self.assertEqual(worker.records.job(operation_id)['state'],
                                 'reconciliation_required')
                current = fixture.maddy.inventories()
                self.assertEqual(email in current['credentials'], phase == 'committed')
                self.assertEqual(email in current['storage'], phase == 'committed')
                worker = MailboxRecovery(context, root)
                worker.maddy, worker.writers = fixture.maddy, writers
                reconciliation = str(uuid.uuid4())
                worker.control.handle('recovery_reconcile', reconciliation, {
                    'record_id': deleted_id, 'confirmation': email, 'acknowledged': True,
                }, 1004)
                worker.run()
                self.assertEqual(worker.records.job(reconciliation)['state'], 'complete')
                self.assertEqual(worker.records.job(operation_id)['state'], 'complete')
                self.assertEqual(worker.records.checkpoint_for(deleted_id)['phase'], 'complete')
                with imap_login(fixture, email, new_password) as connection:
                    self.assertIn(b'encrypted-recovery-mail', bodies(connection))
                with imap_login(fixture, other, MAILBOX_PASSWORD) as connection:
                    self.assertIn(b'unrelated-mail-preserved', bodies(connection))
        # Admission failures happen before pausing mail or creating a deletion record.
        refused_id = str(uuid.uuid4())
        worker.control.handle('recovery_delete', refused_id, values, 1005)
        with patch('host.mailbox_archive.os.statvfs', return_value=SimpleNamespace(
                f_bavail=1, f_frsize=4096)), patch.object(writers, 'pause') as pause:
            worker.run()
            pause.assert_not_called()
        self.assertEqual(worker.records.job(refused_id)['state'], 'failed')

        # An interrupted delete is reconciled without repeating the destructive step.
        interrupted_id = str(uuid.uuid4())
        worker.control.handle("recovery_delete", interrupted_id, values, 1002)
        with patch.object(
            worker, "_remove_live", side_effect=ServiceError("synthetic_interruption")
        ):
            worker.run()
        self.assertEqual(worker.records.job(interrupted_id)["state"], "reconciliation_required")
        self.assertIn(email, fixture.maddy.inventories()["storage"])
        context.generations = SimpleNamespace(read=lambda: {"consumers": {"maddy": {}}})
        context.docker = SimpleNamespace(inspect=lambda name: {})
        reconcile_id = str(uuid.uuid4())
        worker.control.handle(
            "recovery_reconcile",
            reconcile_id,
            {"record_id": interrupted_id, "confirmation": email, "acknowledged": True},
            1003,
        )
        worker.run()
        self.assertEqual(worker.records.job(reconcile_id)["state"], "complete")
        self.assertEqual(worker.records.get(interrupted_id)["state"], "cancelled")
        # Thirty-day recovery, address conflicts, and independent permanent removal.
        thirty = str(uuid.uuid4())
        worker.control.handle("recovery_delete", thirty, {**values, "retention": "30_days"}, 1004)
        worker.run()
        record = worker.records.get(thirty)
        self.assertEqual(record["deadline"] - record["deleted_at"], 30 * 86400)
        fixture.maddy.change(("imap-acct", "create", email))
        fixture.maddy.change(("creds", "create", email), new_password)
        conflict = str(uuid.uuid4())
        worker.control.handle(
            "recovery_restore",
            conflict,
            {
                "record_id": thirty,
                "confirmation": email,
                "acknowledged": True,
                "password_hash": value,
            },
            1005,
        )
        worker.run()
        self.assertEqual(worker.records.job(conflict)["state"], "failed")
        self.assertEqual(worker.records.job(conflict)["error"], "account_conflict")
        self.assertEqual(worker.records.get(thirty)["state"], "deleted")
        fixture.maddy.change(("creds", "remove", "--yes", email))
        fixture.maddy.change(("imap-acct", "remove", "--yes", email))
        retry = str(uuid.uuid4())
        worker.control.handle(
            "recovery_restore",
            retry,
            {
                "record_id": thirty,
                "confirmation": email,
                "acknowledged": True,
                "password_hash": value,
            },
            1006,
        )
        worker.run()
        self.assertEqual(worker.records.job(retry)["state"], "complete")
        deadline = worker.records.get(thirty)['deadline']
        with patch('host.mailbox_recovery.time.time', return_value=deadline - 1):
            worker._expire()
        self.assertEqual(worker.records.get(thirty)['state'], 'restored')
        with patch('host.mailbox_recovery.time.time', return_value=deadline):
            worker._expire()
        self.assertEqual(worker.records.get(thirty)['state'], 'removed')
        before_points = set((root / "recovery-points").glob("*.enc"))
        permanent_id = str(uuid.uuid4())
        worker.control.handle("recovery_delete", permanent_id, {**values, "permanent": True}, 1007)
        worker.run()
        self.assertEqual(worker.records.job(permanent_id)["state"], "complete")
        self.assertEqual(set((root / "recovery-points").glob("*.enc")), before_points)
        self.assertNotIn(email, fixture.maddy.inventories()["storage"])
        # Removing protection from an operational address is an explicit admin decision.
        changed = worker.control.handle(
            "recovery_save_policy",
            str(uuid.uuid4()),
            {"policy": {**policy, "protected": {}}, "acknowledged": True},
            1008,
        )["policy"]
        forever = str(uuid.uuid4())
        worker.control.handle(
            "recovery_delete",
            forever,
            {
                **values,
                "email": other,
                "confirmation": other,
                "retention": "forever",
                "policy_revision": changed["revision"],
            },
            1009,
        )
        worker.run()
        self.assertEqual(worker.records.job(forever)["state"], "complete")
        self.assertIsNone(worker.records.get(forever)["deadline"])
        with patch("host.mailbox_recovery.time.time", return_value=2**31):
            worker._expire()
        self.assertEqual(
            set((root / "recovery-points").glob("*.enc")),
            {root / "recovery-points" / (forever + ".enc")},
        )
        remove_id = str(uuid.uuid4())
        worker.control.handle(
            "recovery_remove",
            remove_id,
            {"record_id": forever, "confirmation": other, "acknowledged": True},
            1010,
        )
        worker.run()
        self.assertEqual(worker.records.job(remove_id)["state"], "complete")
        self.assertFalse(list((root / "recovery-points").glob("*.enc")))
