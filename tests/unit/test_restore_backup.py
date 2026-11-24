import tempfile
import time
import unittest
import uuid
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from helper.backup_control import BackupControl
from helper.process import CommandResult
from host.backup_control import BackupWorker, login_plan
from host.restore_backup import RestoreCommand, parse
from shared.errors import ServiceError

SNAPSHOT = "a" * 64
PASSWORD = "a long and unusual password 2026"
MISSING, PRESENT = "missing@rsalehin24.me", "present@rsalehin24.me"


class ScriptedConsole:
    def __init__(self, secrets=(), answers=()):
        self.secrets, self.answers, self.lines = list(secrets), list(answers), []

    def say(self, text=""):
        self.lines.append(text)

    def ask(self, prompt):
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)

    def secret(self, prompt):
        return self.secrets.pop(0)


class FakeMaddy:
    def __init__(self, credentials, fail=False):
        self.credentials, self.fail, self.created = credentials, fail, []

    def inventories(self):
        return {"credentials": list(self.credentials), "storage": [PRESENT]}

    def change(self, arguments, password=""):
        self.created.append((arguments, password))
        return CommandResult(1 if self.fail else 0, "", "")


class LoginPlanTests(unittest.TestCase):
    def test_missing_mailboxes_are_imported_and_every_mailbox_without_a_login_gets_one(self):
        inventory = {"storage": [PRESENT, "half@rsalehin24.me"], "credentials": [PRESENT]}
        emails = {PRESENT, MISSING, "half@rsalehin24.me"}
        expected = ({MISSING}, {MISSING, "half@rsalehin24.me"})
        self.assertEqual(login_plan(emails, inventory), expected)

    def test_a_live_account_that_is_not_in_the_backup_and_lacks_a_login_is_refused(self):
        inventory = {"storage": [PRESENT, "stray@rsalehin24.me"], "credentials": [PRESENT]}
        with self.assertRaisesRegex(ServiceError, "partial_account_requires_operator_repair"):
            login_plan({PRESENT}, inventory)


class RestoreWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        (root / "staging").mkdir()
        self.worker = BackupWorker.__new__(BackupWorker)
        self.worker.root, self.worker.staging = root, root / "staging"
        self.worker.control = BackupControl(root)
        self.worker.catalog = Mock()
        self.worker.context = SimpleNamespace(deployment={})
        with self.worker.control.connect() as connection:
            connection.execute("INSERT INTO snapshots VALUES(?,?,?)", (SNAPSHOT, 1700000000, 1))
        # The worker lock needs root ownership, which the sandbox does not have.
        lock = patch("host.backup_control.HostLock", return_value=nullcontext())
        lock.start()
        self.addCleanup(lock.stop)
        self.forwarded = Mock()
        self.worker.finish_retention = Mock()

    def command(self, console):
        return RestoreCommand(self.worker, console, self.forwarded)

    def perform(self, job, passwords):
        self.passwords = dict(passwords)
        if job["kind"] == "preflight":
            return {
                "mailboxes": [PRESENT, MISSING],
                "missing": [MISSING],
                "needs_login": [MISSING],
                "bytes": 1,
                "verified": True,
            }
        return {"messages_added": 3}

    def test_restore_prompts_for_each_missing_mailbox_and_never_stores_a_password(self):
        console = ScriptedConsole(
            secrets=["short", PASSWORD, "different but long enough 1", PASSWORD, PASSWORD],
            answers=[SNAPSHOT],
        )
        with patch.object(BackupWorker, "perform", self.perform):
            self.assertEqual(self.command(console).restore(SNAPSHOT), 0)
        self.assertEqual(self.passwords, {MISSING: PASSWORD})
        shown = "\n".join(console.lines)
        self.assertIn(f"  {PRESENT}", shown)
        self.assertIn("Restored 3 messages.", shown)
        self.forwarded.assert_called_once_with()
        database = (Path(self.directory.name) / "backup-control.db").read_bytes()
        self.assertNotIn(PASSWORD.encode(), database)
        states = [job["state"] for job in self.worker.control.listing(
            {"kind": "operations", "page": 1, "page_size": 10})["items"]]
        self.assertEqual(states, ["complete", "complete"])

    def test_a_wrong_confirmation_changes_nothing(self):
        console = ScriptedConsole(secrets=[PASSWORD, PASSWORD], answers=["b" * 64])
        with patch.object(BackupWorker, "perform", self.perform):
            self.assertEqual(self.command(console).restore(SNAPSHOT), 2)
        self.assertEqual(self.passwords, {})
        self.forwarded.assert_not_called()
        kinds = [job["kind"] for job in self.worker.control.listing(
            {"kind": "operations", "page": 1, "page_size": 10})["items"]]
        self.assertEqual(kinds, ["preflight"])

    def test_empty_input_cancels_cleanly_and_removes_the_checked_copy(self):
        console = ScriptedConsole(secrets=[PASSWORD, PASSWORD], answers=[])
        with patch.object(BackupWorker, "perform", self.perform):
            self.assertEqual(self.command(console).restore(SNAPSHOT), 2)
        self.assertIn("Cancelled; nothing was changed.", console.lines)
        self.assertEqual(list(self.worker.staging.iterdir()), [])
        self.forwarded.assert_not_called()

    def test_a_failed_forwarding_sync_is_reported_not_hidden(self):
        self.forwarded.side_effect = ServiceError("cloudflare_unavailable", 503)
        console = ScriptedConsole(secrets=[PASSWORD, PASSWORD], answers=[SNAPSHOT])
        with patch.object(BackupWorker, "perform", self.perform):
            self.assertEqual(self.command(console).restore(SNAPSHOT), 0)
        self.assertIn("python3 -m host.sync_forwarding", "\n".join(console.lines))

    def test_only_an_exact_snapshot_id_is_accepted(self):
        for value in ("", "a" * 12, "A" * 64, "../etc"):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ServiceError, "exact_snapshot_id_required"):
                    self.command(ScriptedConsole()).restore(value)

    def test_list_shows_date_and_verification_after_refreshing(self):
        with self.worker.control.connect() as connection:
            connection.execute(
                "INSERT INTO snapshot_details VALUES(?,?)",
                (SNAPSHOT, '{"verification": {"verified_at": 1700000100}}'),
            )
        console = ScriptedConsole()
        self.command(console).list_snapshots()
        self.worker.catalog.refresh.assert_called_once_with()
        self.assertEqual(
            console.lines, [f"{SNAPSHOT}  2023-11-15 04:13 Dhaka  verified 2023-11-15 04:15 Dhaka"]
        )

    def test_reconcile_settles_each_interrupted_operation_in_this_process(self):
        stuck = "00000000-0000-4000-8000-000000000001"
        self.worker.control.begin((stuck, "backup", "", ""), time.time())
        console = ScriptedConsole()
        reconciled = {"committed": False}
        with patch.object(BackupWorker, "perform", lambda *_: reconciled):
            self.assertEqual(self.command(console).reconcile(), 0)
        self.assertIn("Operation rolled back.", console.lines)
        self.assertEqual(self.worker.control.job(stuck)["state"], "reconciliation_required")

    def test_nothing_to_reconcile(self):
        console = ScriptedConsole()
        self.assertEqual(self.command(console).reconcile(), 0)
        self.assertEqual(console.lines, ["Nothing needs reconciling."])


class RestoreEngineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.worker = BackupWorker.__new__(BackupWorker)
        self.worker.context = SimpleNamespace(deployment={})
        self.worker.control = BackupControl(Path(self.directory.name))
        with self.worker.control.connect() as connection:
            connection.execute("INSERT INTO snapshots VALUES(?,?,?)", (SNAPSHOT, 1, 1))

    def test_logins_are_created_through_maddy_and_existing_ones_are_skipped(self):
        maddy = FakeMaddy(credentials=[PRESENT])
        with patch("host.backup_control.DockerMaddy", return_value=maddy):
            self.worker.create_logins({PRESENT: "unused", MISSING: PASSWORD})
        self.assertEqual(maddy.created, [(("creds", "create", MISSING), PASSWORD)])

    def test_a_failed_login_creation_stops_with_a_code_and_without_the_password(self):
        with patch("host.backup_control.DockerMaddy", return_value=FakeMaddy([], fail=True)):
            with self.assertRaises(ServiceError) as caught:
                self.worker.create_logins({MISSING: PASSWORD})
        self.assertEqual(caught.exception.code, "credentials_create_failed")
        self.assertNotIn(PASSWORD, str(caught.exception))

    def test_restore_refuses_when_the_passwords_do_not_match_the_preflight(self):
        prepared = "00000000-0000-4000-8000-000000000002"
        self.worker.control.begin((prepared, "preflight", SNAPSHOT, ""), 1)
        self.worker.control.update(
            prepared, "complete", "complete", 2, result={"needs_login": [MISSING]}
        )
        job = {"id": "x", "snapshot": SNAPSHOT, "payload": {"previous": prepared}}
        with self.assertRaisesRegex(ServiceError, "missing_mailbox_passwords_required"):
            self.worker.restore(job, {})

    def test_a_failure_after_the_live_data_may_have_changed_needs_reconciliation(self):
        cases = (("safety_backup", "failed"), ("creating_logins", "reconciliation_required"))
        for phase, state in cases:
            with self.subTest(phase=phase):
                identifier = str(uuid.uuid4())
                self.worker.control.begin((identifier, "backup", "", ""), 1)
                self.worker.control.update(identifier, "running", phase, 2)
                self.worker.record_failure({"id": identifier}, ServiceError("boom", 503))
                self.assertEqual(self.worker.control.job(identifier)["state"], state)
                self.worker.control.update(identifier, "complete", "complete", 3)


class ParseTests(unittest.TestCase):
    def test_exactly_one_action_is_required(self):
        self.assertTrue(parse(["--list"]).list)
        self.assertEqual(parse([SNAPSHOT]).snapshot, SNAPSHOT)
        for arguments in ([], ["--list", SNAPSHOT], ["--list", "--reconcile"]):
            with self.subTest(arguments=arguments), self.assertRaises(SystemExit):
                parse(arguments)
