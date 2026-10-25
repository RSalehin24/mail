import sqlite3
import time
import uuid
from unittest.mock import patch

from helper.journal import Journal
from helper.maddy import LAUNCHER
from helper.process import CommandResult
from helper.protocol import HelperRequest
from shared.errors import ServiceError
from shared.files import atomic_json
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import imap_login
from tests.recovery_case_support import RecoveryCase
from tests.restore_support import DashboardRecoveryFixture, RecoveryArchive, bodies, deliver


class RecoveryTests(RecoveryCase):
    def test_encrypted_restore_then_partial_body_repair_preserves_newer_mail(self):
        target = self.create("partial-recovery")
        deliver(self.fixture, target, "selected-body-fixture-verified")
        dashboard = DashboardRecoveryFixture(self.fixture.root)
        archive = RecoveryArchive(self.fixture, dashboard, b"selected-body-fixture-verified")
        started = time.monotonic()
        archive.capture()
        self.assertEqual(archive.writers.events, ["locked", "stopped", "resumed", "unlocked"])
        self.assertTrue(archive.manifest["verified"])
        self.assertFalse(archive.manifest["restore_passed"])
        self.verify_dashboard(archive, dashboard)
        with archive.maddy() as connection:
            connection.login(target, MAILBOX_PASSWORD)
            self.assertIn(b"selected-body-fixture-verified", bodies(connection))
        archive.manifest.update(
            {"restore_passed": True, "restore_elapsed_seconds": time.monotonic() - started}
        )
        atomic_json(archive.backup.paths.manifest, archive.manifest)
        newer = self.create("newer-unrelated")
        deliver(self.fixture, newer, "newer-unrelated-message-preserved")
        helper = self.configure_deletion(archive)
        self.fail_after_body_removal(helper, archive, target)
        expected, actual = archive.repair_body()
        self.assertEqual(actual, expected)
        self.assert_body(target, b"selected-body-fixture-verified")
        self.assert_body(newer, b"newer-unrelated-message-preserved")
        self.assertTrue(helper.journal.unresolved())
        self.restore_removed_reference(archive, target, newer)

    def test_real_commit_failure_after_body_removal_keeps_a_durable_warning(self):
        target = self.create("commit-failure")
        deliver(self.fixture, target, "commit-failure-body-verified")
        archive = self.verified_archive(target, b"commit-failure-body-verified")
        unrelated = self.create("commit-unrelated")
        deliver(self.fixture, unrelated, "newer-unrelated-commit-mail-preserved")
        self.defer_deletion_failure(target)
        helper = self.configure_deletion(archive)
        request = self.assert_failed_commit(helper, archive, target)
        self.assert_no_deletion_replay(helper, request, unrelated)
        expected, actual = archive.repair_body()
        self.assertEqual(actual, expected)
        self.assertEqual(
            self.fixture.maddy.change(("creds", "create", target), MAILBOX_PASSWORD).code, 0
        )
        self.assert_body(target, b"commit-failure-body-verified")
        self.assert_body(unrelated, b"newer-unrelated-commit-mail-preserved")
        self.assertTrue(helper.journal.unresolved())

    def assert_failed_commit(self, helper, archive, target):
        request = HelperRequest(
            "delete", str(uuid.uuid4()), target, cutoff=archive.manifest["cutoff"]
        )
        responses = []
        original = self.fixture.maddy.change

        def observed(arguments, password=""):
            response = original(arguments, password)
            responses.append(
                {"operation": arguments[:2], "code": response.code, "output": response.output}
            )
            return response

        with patch.object(self.fixture.maddy, "change", side_effect=observed):
            result = helper.handle(request)
        self.assertEqual(result["state"], "reconciliation_required")
        failure = next(row for row in responses if row["operation"] == ("imap-acct", "remove"))
        self.assertIn("FOREIGN KEY constraint failed", failure["output"])
        self.assertEqual(failure["code"], 0)
        self.assertEqual(result["steps"][-1]["exit_code"], failure["code"])
        self.assertTrue(result["steps"][-1]["error_reported"])
        self.assertIn("messages may already have been removed", result["warning"])
        self.assertFalse(archive.body.exists())
        self.assertIn(target, result["postconditions"]["storage"])
        self.assertNotIn(target, result["postconditions"]["credentials"])
        self.assertTrue(Journal(helper.journal.path).unresolved())
        with sqlite3.connect(self.fixture.data / "imapsql.db") as connection:
            self.assertEqual(connection.execute("PRAGMA foreign_key_check").fetchall(), [])
            self.assertEqual(connection.execute("SELECT COUNT(*) FROM msgs").fetchone()[0], 2)
        return request

    def assert_no_deletion_replay(self, helper, request, unrelated):
        with patch.object(self.fixture.maddy, "change", wraps=self.fixture.maddy.change) as change:
            self.assertEqual(helper.handle(request)["state"], "reconciliation_required")
            with self.assertRaises(ServiceError):
                helper.handle(
                    HelperRequest("password", str(uuid.uuid4()), unrelated, MAILBOX_PASSWORD)
                )
        change.assert_not_called()

    def defer_deletion_failure(self, target):
        # A real deferred foreign key fails COMMIT after native filesystem deletion.
        # Only this disposable Maddy database receives the fault-injection table.
        with sqlite3.connect(self.fixture.data / "imapsql.db") as connection:
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute(
                "CREATE TABLE mail_admin_fixture_commit_guard("
                "user_id INTEGER REFERENCES users(id) DEFERRABLE INITIALLY DEFERRED)"
            )
            connection.execute(
                "INSERT INTO mail_admin_fixture_commit_guard SELECT id FROM users WHERE username=?",
                (target,),
            )
            count = connection.execute("SELECT COUNT(*) FROM mail_admin_fixture_commit_guard")
            self.assertEqual(count.fetchone()[0], 1)

    def restore_removed_reference(self, archive, target, unrelated):
        deliver(self.fixture, target, "newer-selected-account-message-preserved")
        with imap_login(self.fixture, target, MAILBOX_PASSWORD) as connection:
            self.assertEqual(connection.select("INBOX")[0], "OK")
            status, matches = connection.uid("SEARCH", None, "BODY", "selected-body-fixture")
            self.assertEqual(status, "OK")
            self.assertEqual(len(matches[0].split()), 1)
            identifier = matches[0].decode()
        self.fixture.command(
            (
                "exec",
                "maddy",
                LAUNCHER,
                "imap-msgs",
                "remove",
                "--uid",
                "--yes",
                target,
                "INBOX",
                identifier,
            )
        )
        with imap_login(self.fixture, target, MAILBOX_PASSWORD) as connection:
            remaining = bodies(connection)
        self.assertNotIn(b"selected-body-fixture-verified", remaining)
        self.assertIn(b"newer-selected-account-message-preserved", remaining)
        restored_uid = archive.restore_reference(target)
        self.assertNotEqual(restored_uid, identifier)
        self.assert_body(target, b"selected-body-fixture-verified")
        self.assert_body(target, b"newer-selected-account-message-preserved")
        self.assert_body(unrelated, b"newer-unrelated-message-preserved")

    def fail_after_body_removal(self, helper, archive, email):
        original = self.fixture.maddy.change

        def fault(arguments, password=""):
            if arguments[:2] == ("imap-acct", "remove"):
                archive.body.unlink()
                return CommandResult(1, "fixture partial body removal")
            return original(arguments, password)

        request = HelperRequest(
            "delete", str(uuid.uuid4()), email, cutoff=archive.manifest["cutoff"]
        )
        with patch.object(self.fixture.maddy, "change", side_effect=fault):
            result = helper.handle(request)
        self.assertEqual(result["state"], "reconciliation_required")
        self.assertIn("messages may already have been removed", result["warning"])
        inventory = self.fixture.maddy.inventories()
        self.assertIn(email, inventory["storage"])
        self.assertNotIn(email, inventory["credentials"])
        self.assertFalse(archive.body.exists())
        self.assertEqual(original(("creds", "create", email), MAILBOX_PASSWORD).code, 0)
