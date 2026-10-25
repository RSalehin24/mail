import sqlite3
import tempfile
import unittest
import uuid
from pathlib import Path
from types import SimpleNamespace

from helper.journal import Journal
from helper.protocol import HelperRequest
from shared.errors import ServiceError
from tests.runtime_crash_support import wait_for_complete


class RuntimeCrashObservationTests(unittest.TestCase):
    def test_real_temporary_sqlite_write_lock_does_not_abort_completion_polling(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'journal.db'
            journal = Journal.provision(path)
            request = HelperRequest('create', str(uuid.uuid4()), 'fixture@example.net', 'password')
            journal.begin(request, (1, {}))
            journal.update(request.request_id, {'state': 'complete'})
            blocker = sqlite3.connect(path)
            blocker.execute('BEGIN IMMEDIATE')
            attempts = []

            def lookup(identifier):
                attempts.append(identifier)
                try:
                    return journal.lookup(identifier)
                finally:
                    blocker.rollback()

            try:
                result = wait_for_complete(SimpleNamespace(lookup=lookup), request.request_id)
            finally:
                blocker.close()
            self.assertEqual(result['state'], 'complete')
            self.assertGreaterEqual(len(attempts), 2)

    def test_corrupt_journal_is_rejected_without_retrying_it_as_a_temporary_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'journal.db'
            journal = Journal.provision(path)
            path.write_bytes(b'corrupt database')
            with self.assertRaisesRegex(ServiceError, 'journal_unavailable'):
                wait_for_complete(journal, str(uuid.uuid4()))
