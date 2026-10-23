import sqlite3
import unittest
import uuid
from contextlib import closing
from unittest.mock import patch

from helper.journal import Journal
from helper.protocol import HelperRequest
from shared.errors import ServiceError
from tests.helper_support import MAILBOX_PASSWORD
from tests.maddy_support import RealMaddyFixture, imap_login
from tests.restore_support import bodies, deliver


class ProvisioningFailureTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RealMaddyFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.start()
        self.request = HelperRequest('create', str(uuid.uuid4()),
                                     'provisioning-failure@rsalehin24.me', MAILBOX_PASSWORD)

    def reject_storage(self):
        with closing(sqlite3.connect(self.fixture.data / 'imapsql.db')) as connection:
            connection.execute('CREATE TRIGGER reject_fixture_user BEFORE INSERT ON users '
                               "BEGIN SELECT RAISE(ABORT,'fixture user creation refused'); END")
            connection.commit()

    def test_known_failed_storage_creation_rolls_back_only_owned_new_credentials(self):
        self.reject_storage()
        result = self.fixture.helper.handle(self.request)
        self.assertEqual(result['state'], 'failed')
        self.assertEqual([step['step'] for step in result['steps']],
                         ['credentials-created', 'storage-created', 'credentials-rolled-back'])
        self.assertTrue(result['steps'][1]['error_reported'])
        self.assertEqual(result['steps'][2]['exit_code'], 0)
        self.assertEqual(self.fixture.maddy.inventories(), {'credentials': [], 'storage': []})
        self.assertFalse(Journal(self.fixture.helper.journal.path).unresolved())
        with patch.object(self.fixture.maddy, 'change', wraps=self.fixture.maddy.change) as change:
            self.assertEqual(self.fixture.helper.handle(self.request)['state'], 'failed')
        change.assert_not_called()

    def test_created_storage_and_lost_cli_response_keep_access_mail_and_journal(self):
        original = self.fixture.maddy.change

        def lose_response(arguments, password=''):
            result = original(arguments, password)
            if arguments[:2] == ('imap-acct', 'create'):
                self.assertEqual(result.code, 0)
                raise ServiceError('command_completion_unknown', 503)
            return result

        with patch.object(self.fixture.maddy, 'change', side_effect=lose_response):
            with self.assertRaisesRegex(ServiceError, 'command_completion_unknown'):
                self.fixture.helper.handle(self.request)
        record = Journal(self.fixture.helper.journal.path).lookup(self.request.request_id)
        self.assertEqual((record['state'], record['phase']), ('unknown', 'storage-created'))
        self.assertEqual(record['postconditions'], {'credentials': [self.request.email],
                                                    'storage': [self.request.email]})
        deliver(self.fixture, self.request.email, 'lost-cli-response-retained-mail')
        with imap_login(self.fixture, self.request.email, MAILBOX_PASSWORD) as connection:
            self.assertIn(b'lost-cli-response-retained-mail', bodies(connection))
        with patch.object(self.fixture.maddy, 'change', wraps=self.fixture.maddy.change) as change:
            self.assertEqual(self.fixture.helper.handle(self.request)['state'], 'unknown')
        change.assert_not_called()

    def test_unknown_postconditions_never_authorize_credential_rollback(self):
        self.reject_storage()
        inventory = self.fixture.maddy.inventories()
        with patch.object(self.fixture.maddy, 'inventories', side_effect=[
            inventory, ServiceError('inventory_unavailable', 503),
        ]):
            result = self.fixture.helper.handle(self.request)
        self.assertEqual(result['state'], 'reconciliation_required')
        self.assertEqual([step['step'] for step in result['steps']],
                         ['credentials-created', 'storage-created'])
        self.assertEqual(result['postconditions'], {'observation': 'unavailable'})
        self.assertEqual(self.fixture.maddy.inventories(),
                         {'credentials': [self.request.email], 'storage': []})
