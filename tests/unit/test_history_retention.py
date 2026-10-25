import unittest

from backend.retention import BATCH_SIZE, HISTORY_SECONDS, cleanup
from tests.auth_support import AuthFixture


class HistoryRetentionTests(unittest.TestCase):
    def test_audit_and_unreferenced_relay_history_expire_in_bounded_batches_after_ninety_days(self):
        fixture = AuthFixture()
        self.addCleanup(fixture.close)
        cutoff = fixture.now - HISTORY_SECONDS
        old = BATCH_SIZE + 1
        with fixture.database.transaction() as database:
            database.executemany(
                'INSERT INTO audit_logs(created,action,outcome,request_id) VALUES(?,?,?,?)',
                ((cutoff - 1, 'retention-fixture', 'complete', 'old-' + str(index))
                 for index in range(old)),
            )
            database.executemany(
                'INSERT INTO relay_tests VALUES(?,?,?,?,1,1,0,?)',
                (('old-' + str(index), 1, 'mail@rsalehin24.me', 'rsalehin24@gmail.com', cutoff - 1)
                 for index in range(old)),
            )
            for label, created in (('boundary', cutoff), ('current', fixture.now)):
                database.execute('INSERT INTO audit_logs(created,action,outcome,request_id) '
                                 'VALUES(?,?,?,?)',
                                 (created, 'retention-fixture', 'complete', label))
                database.execute('INSERT INTO relay_tests VALUES(?,?,?,?,1,1,0,?)',
                                 (label, 1, 'mail@rsalehin24.me', 'rsalehin24@gmail.com', created))
        cleanup(fixture.database, fixture.now)
        self.assert_history(fixture.database, 1)
        cleanup(fixture.database, fixture.now)
        self.assert_history(fixture.database, 0)
        cleanup(fixture.database, fixture.now)
        self.assert_history(fixture.database, 0)

    def assert_history(self, database, old):
        with database.transaction() as connection:
            for query in ("SELECT request_id FROM audit_logs WHERE action='retention-fixture'",
                          'SELECT id FROM relay_tests'):
                retained = {row[0] for row in connection.execute(
                    query,
                )}
                self.assertTrue({'boundary', 'current'} <= retained)
                expired = [value for value in retained if value.startswith('old-')]
                self.assertEqual(len(expired), old)
