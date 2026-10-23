import imaplib
import json
import smtplib
import sqlite3
import subprocess
import unittest
import uuid
from email.message import EmailMessage
from unittest.mock import patch

from helper.protocol import HelperRequest
from shared.errors import ServiceError
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import RealMaddyFixture, imap_login


class RealMaddyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RealMaddyFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.start()

    def create(self, local):
        email = local + "@rsalehin24.me"
        outcome = self.fixture.helper.handle(
            HelperRequest("create", str(uuid.uuid4()), email, MAILBOX_PASSWORD)
        )
        self.assertEqual(outcome["state"], "complete")
        return email

    def connect(self, email, password=MAILBOX_PASSWORD):
        return imap_login(self.fixture, email, password)

    def test_01_eight_baseline_pairs_and_real_delivery(self):
        locals = (
            "cloud",
            "demo_user.library",
            "dev_01",
            "dev_02",
            "library",
            "mail",
            "reyan",
            "salehin",
        )
        for local in locals:
            self.create(local)
        inventory = self.fixture.maddy.inventories()
        self.assertEqual(len(inventory["credentials"]), 8)
        self.assertEqual(inventory["credentials"], inventory["storage"])
        message = EmailMessage()
        message["From"], message["To"], message["Subject"] = (
            "fixture@example.net",
            "mail@rsalehin24.me",
            "Isolated delivery fixture",
        )
        message.set_content("Only synthetic mail. fixture-body-verified")
        with smtplib.SMTP("127.0.0.1", self.fixture.smtp_port, timeout=5) as smtp:
            self.assertEqual(smtp.send_message(message), {})
        with self.connect("mail@rsalehin24.me") as connection:
            self.assertEqual(connection.select("INBOX")[0], "OK")
            status, identifiers = connection.search(None, "ALL")
            self.assertEqual(status, "OK")
            self.assertTrue(identifiers[0])
            status, bodies = connection.fetch(identifiers[0].split()[-1], "(RFC822)")
            self.assertEqual(status, "OK")
            self.assertIn(b"fixture-body-verified", bodies[0][1])

    def test_02_reset_keeps_established_session_and_rejects_old_new_login(self):
        email = self.create("reset-fixture")
        connection = self.connect(email)
        self.addCleanup(connection.shutdown)
        new_password = " changed fixture password "
        outcome = self.fixture.helper.handle(
            HelperRequest("password", str(uuid.uuid4()), email, new_password)
        )
        self.assertEqual(outcome["state"], "complete")
        self.assertEqual(connection.noop()[0], "OK")
        with self.assertRaises(imaplib.IMAP4.error):
            with self.connect(email):
                self.fail("old password must fail new authentication")
        with self.connect(email, new_password) as fresh:
            self.assertEqual(fresh.noop()[0], "OK")

    def test_03_raw_password_upsert_and_helper_existing_only_guard(self):
        email = "missing-fixture@rsalehin24.me"
        with self.assertRaises(ServiceError):
            self.fixture.helper.handle(
                HelperRequest("password", str(uuid.uuid4()), email, MAILBOX_PASSWORD)
            )
        self.assertNotIn(email, self.fixture.maddy.inventories()["credentials"])
        result = self.fixture.maddy.change(("creds", "password", email), MAILBOX_PASSWORD)
        self.assertEqual(result.code, 0)
        self.assertIn(email, self.fixture.maddy.inventories()["credentials"])
        self.fixture.maddy.change(("creds", "remove", "--yes", email))

    def test_04_nobody_denied_and_daemon_launcher_umask_is_0077(self):
        result = self.fixture.command(("exec", "maddy", "/bin/cat", "/proc/1/status"))
        self.assertIn("Umask:\t0077", result.stdout)
        for filename in ("credentials.db", "imapsql.db"):
            for identity in ("65534:65534", "10001:10001"):
                self.assert_denied(identity, filename)
            self.assertEqual((self.fixture.data / filename).stat().st_mode & 0o777, 0o600)

    def test_utf8_72_byte_password_keeps_spaces_and_73_bytes_cannot_reach_cli(self):
        email, password = "utf8-fixture@rsalehin24.me", " " + "é" * 35 + " "
        payload = {
            "protocol": 1,
            "operation": "create",
            "request_id": str(uuid.uuid4()),
            "email": email,
            "password": password,
        }
        outcome = self.fixture.helper.handle(HelperRequest.parse(payload))
        self.assertEqual(outcome["state"], "complete")
        with self.connect(email, password) as connection:
            self.assertEqual(connection.noop()[0], "OK")
        for invalid in ("", password + "a", "x" * 16 + "\r", "x" * 16 + "\n",
                        "x" * 16 + "\0"):
            with self.subTest(invalid=repr(invalid)):
                self.assert_password_rejected_before_cli({
                    **payload, "operation": "password", "password": invalid,
                })
        with self.connect(email, password) as connection:
            self.assertEqual(connection.noop()[0], "OK")

    def assert_password_rejected_before_cli(self, payload):
        with patch.object(self.fixture.maddy, "change", wraps=self.fixture.maddy.change) as change:
            with self.assertRaises(ServiceError):
                self.fixture.helper.handle(HelperRequest.parse(payload))
        change.assert_not_called()

    def test_real_zero_exit_password_sql_error_keeps_old_password_and_requires_reconciliation(self):
        email = self.create("password-sql-failure")
        with sqlite3.connect(self.fixture.data / "credentials.db") as connection:
            for operation in ("INSERT", "UPDATE"):
                connection.execute(
                    "CREATE TRIGGER mail_admin_fixture_reject_"
                    + operation
                    + " BEFORE "
                    + operation
                    + " ON passwords "
                    "BEGIN SELECT RAISE(ABORT, 'fixture password write rejected'); END"
                )
        replacement = " unchanged-on-error fixture password "
        original, responses = self.fixture.maddy.change, []

        def observed(arguments, password=""):
            response = original(arguments, password)
            responses.append({"operation": arguments[:2], "output": response.output})
            return response

        with patch.object(self.fixture.maddy, "change", side_effect=observed):
            result = self.fixture.helper.handle(
                HelperRequest("password", str(uuid.uuid4()), email, replacement)
            )
        self.assertEqual(result["state"], "reconciliation_required", json.dumps(responses))
        self.assertEqual(result["steps"][-1]["exit_code"], 0)
        self.assertTrue(result["steps"][-1]["error_reported"])
        with self.connect(email) as connection:
            self.assertEqual(connection.noop()[0], "OK")
        with self.assertRaises(imaplib.IMAP4.error):
            self.connect(email, replacement)

    def test_real_special_folder_warning_keeps_created_storage_and_credentials(self):
        with sqlite3.connect(self.fixture.data / "imapsql.db") as connection:
            connection.execute(
                "CREATE TRIGGER mail_admin_fixture_reject_sent BEFORE INSERT ON mboxes "
                "WHEN NEW.name='Sent' BEGIN SELECT RAISE(ABORT, 'fixture folder rejected'); END"
            )
        email = "folder-warning@rsalehin24.me"
        result = self.fixture.helper.handle(
            HelperRequest("create", str(uuid.uuid4()), email, MAILBOX_PASSWORD)
        )
        self.assertEqual(result["state"], "reconciliation_required")
        self.assertEqual(result["steps"][-1]["exit_code"], 0)
        self.assertTrue(result["steps"][-1]["warnings"])
        self.assertIn(email, result["postconditions"]["credentials"])
        self.assertIn(email, result["postconditions"]["storage"])
        with self.connect(email) as connection:
            self.assertEqual(connection.select("INBOX")[0], "OK")

    def assert_denied(self, identity, filename):
        with self.assertRaises(subprocess.CalledProcessError) as caught:
            self.fixture.command(
                ("exec", "--user", identity, "maddy", "/bin/cat", "/data/" + filename)
            )
        self.assertIn(
            "permission denied",
            str(caught.exception).lower() + getattr(caught.exception, "stderr", "").lower(),
        )
