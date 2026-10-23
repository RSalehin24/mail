import sqlite3
import unittest
import uuid
from unittest.mock import patch

from helper.protocol import HelperRequest
from shared.errors import ServiceError
from tests.fixture_values import MAILBOX_PASSWORD
from tests.maddy_support import RealMaddyFixture
from tests.sqlite_fault_support import FaultedConnections


class JournalStorageTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RealMaddyFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.start()

    def request(self):
        return HelperRequest('create', str(uuid.uuid4()), 'journal-fault@rsalehin24.me',
                             MAILBOX_PASSWORD)

    def assert_failure_prevents_cli_mutations(self, fault):
        request = self.request()
        with (patch('helper.journal.sqlite3.connect', side_effect=fault),
              patch.object(self.fixture.maddy, 'change', wraps=self.fixture.maddy.change) as cli):
            with self.assertRaisesRegex(ServiceError, 'journal_unavailable'):
                self.fixture.helper.handle(request)
        observations = {('version',), ('creds', 'list'), ('imap-acct', 'list')}
        self.assertTrue(all(call.args[0] in observations for call in cli.call_args_list))
        self.assertIsNone(self.fixture.helper.journal.lookup(request.request_id))
        self.assertEqual(self.fixture.maddy.inventories(), {'credentials': [], 'storage': []})
        return request

    def test_native_read_only_journal_prevents_unrecorded_mutation_and_recovers(self):
        fault = FaultedConnections(self.fixture.helper.journal.path, 'read_only')
        request = self.assert_failure_prevents_cli_mutations(fault)
        self.assertIn(sqlite3.SQLITE_READONLY, fault.errors)
        result = self.fixture.helper.handle(request)
        self.assertEqual(result['state'], 'complete')
        recorded = self.fixture.helper.journal.lookup(request.request_id)
        self.assertEqual(recorded['state'], 'complete')

    def test_native_full_journal_prevents_unrecorded_mutation_and_recovers(self):
        journal = self.fixture.helper.journal
        with journal.connection() as connection:
            connection.executescript('CREATE TABLE fixture_payload(value BLOB); '
                'CREATE TRIGGER fixture_force_full BEFORE INSERT ON operations BEGIN '
                'INSERT INTO fixture_payload VALUES(zeroblob(1048576)); END;')
        fault = FaultedConnections(journal.path, 'full')
        request = self.assert_failure_prevents_cli_mutations(fault)
        self.assertIn(sqlite3.SQLITE_FULL, fault.errors)
        with journal.connection() as connection:
            self.assertEqual(connection.execute('SELECT COUNT(*) FROM fixture_payload')
                             .fetchone()[0], 0)
            connection.execute('DROP TRIGGER fixture_force_full')
        self.assertEqual(self.fixture.helper.handle(request)['state'], 'complete')
