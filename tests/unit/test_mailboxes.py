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

    def test_edit_existing_name_and_notes_preserves_mail_credentials(self):
        self.mailboxes.create(self.request)
        before = self.fixture.host.maddy.credentials.copy()
        result = self.mailboxes.update_metadata(self.request['email'], {
            'display_name': 'Cloud operations', 'notes': 'Billing and infrastructure',
        })
        self.assertEqual(result['display_name'], 'Cloud operations')
        row = self.mailboxes.inventory()[0]
        self.assertEqual(row['notes'], 'Billing and infrastructure')
        self.assertEqual(self.fixture.host.maddy.credentials, before)
        with self.assertRaisesRegex(ServiceError, 'account_not_found'):
            self.mailboxes.update_metadata('missing@rsalehin24.me', {
                'display_name': 'Missing', 'notes': '',
            })

    def test_sender_confirmation_refreshes_inventory_and_old_relay_revisions_do_not_verify(self):
        self.mailboxes.create(self.request)
        relay = self.fixture.services.relay
        settings = {'host': 'smtp-relay.brevo.com', 'port': 587, 'tls_mode': 'starttls',
                    'username': 'synthetic', 'credential': 'fixture-secret'}
        relay.save(settings)
        test = relay.test(self.request['email'], ('delivery', 'fixture'))
        self.assertEqual(self.mailboxes.inventory()[0]['outbound_readiness'], 'awaiting receipt')
        relay.confirm(test['test_id'])
        row = self.mailboxes.inventory()[0]
        self.assertEqual(row['outbound_readiness'], 'confirmed')
        self.assertEqual(row['sender_tests'][0]['confirmed'], 1)
        relay.save(settings)
        row = self.mailboxes.inventory()[0]
        self.assertEqual(row['outbound_readiness'], 'unverified')
        self.assertEqual(row['sender_tests'], [])

    def test_inventory_newest_first_and_missing_creation_date_backfilled_once(self):
        self.mailboxes.create(self.request)
        self.mailboxes.create({**self.request, 'request_id': str(uuid.uuid4()),
                               'email': 'second@rsalehin24.me'})
        with self.mailboxes.database.transaction() as connection:
            connection.execute('UPDATE mailboxes SET created=100 WHERE email=?',
                               (self.request['email'],))
            connection.execute('UPDATE mailboxes SET created=0 WHERE email=?',
                               ('second@rsalehin24.me',))
        original_clock = self.mailboxes.clock
        self.mailboxes.clock = lambda: 2000
        rows = self.mailboxes.inventory()
        self.assertEqual([row['email'] for row in rows],
                         ['second@rsalehin24.me', self.request['email']])
        self.assertEqual([row['created'] for row in rows], [2000, 100])
        self.mailboxes.clock = lambda: 3000
        self.assertEqual([row['created'] for row in self.mailboxes.inventory()], [2000, 100])
        self.mailboxes.clock = original_clock
