import unittest
import uuid
from unittest.mock import patch

from shared.errors import ServiceError
from tests.api_support import ApiFixture
from tests.helper_support import MAILBOX_PASSWORD


class MailboxMetadataTests(unittest.TestCase):
    def setUp(self):
        self.fixture = ApiFixture()
        self.addCleanup(self.fixture.close)
        self.mailboxes = self.fixture.services.mailboxes
        self.request = {
            "request_id": str(uuid.uuid4()),
            "email": "metadata-fixture@rsalehin24.me",
            "password": MAILBOX_PASSWORD,
            "display_name": "Recovered display name",
            "notes": "Recovered fixture purpose",
        }

    def test_metadata_failure_then_discovery_then_retry_fills_missing_fields_without_cli_replay(
        self,
    ):
        with patch.object(
            self.mailboxes, "_audit", side_effect=ServiceError("storage_unavailable", 503)
        ):
            with self.assertRaises(ServiceError) as caught:
                self.mailboxes.create(self.request)
        self.assertEqual(caught.exception.code, "metadata_reconciliation_required")
        row = self.mailboxes.inventory()[0]
        self.assertEqual(row["status"], "ready")
        self.assertEqual(row["display_name"], "")
        count = len(self.fixture.host.maddy.calls)
        self.assertEqual(self.mailboxes.create(self.request)["state"], "complete")
        recovered = self.mailboxes.inventory()[0]
        self.assertEqual(recovered["display_name"], self.request["display_name"])
        self.assertEqual(recovered["notes"], self.request["notes"])
        self.assertEqual(len(self.fixture.host.maddy.calls), count)

    def test_duplicate_create_never_overwrites_existing_metadata_or_changes_the_password(self):
        self.mailboxes.create(self.request)
        count = len(self.fixture.host.maddy.calls)
        self.mailboxes.create({**self.request, "display_name": "Replacement", "notes": "Changed"})
        row = self.mailboxes.inventory()[0]
        self.assertEqual(row["display_name"], self.request["display_name"])
        self.assertEqual(row["notes"], self.request["notes"])
        self.assertEqual(len(self.fixture.host.maddy.calls), count)

    def test_partial_create_marks_preexisting_metadata_as_requiring_reconciliation(self):
        with self.fixture.services.authentication.database.transaction() as connection:
            connection.execute(
                "INSERT INTO mailboxes(email,created,updated,status) VALUES(?,0,0,'ready')",
                (self.request["email"],),
            )
        self.fixture.host.maddy.fail = ("imap-acct", "create")
        self.assertEqual(self.mailboxes.create(self.request)["state"], "reconciliation_required")
        with self.fixture.services.authentication.database.transaction() as connection:
            status = connection.execute("SELECT status FROM mailboxes").fetchone()[0]
        self.assertEqual(status, "reconciliation_required")
