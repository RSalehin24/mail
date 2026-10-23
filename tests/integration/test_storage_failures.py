import sqlite3
from contextlib import closing
from unittest.mock import patch

from tests.api_support import ApiTestCase
from tests.fixture_values import PASSWORD
from tests.sqlite_fault_support import FaultedConnections


class StorageFailureApiTests(ApiTestCase):
    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.set_session(self.fixture.login())

    async def assert_closed(self):
        for route in ('/api/accounts', '/api/settings/relay', '/api/diagnostics'):
            result = await self.client.get(route)
            self.assertEqual(result.status_code, 503)
            self.assertEqual(result.headers['cache-control'], 'no-store')
            self.assertEqual(set(result.json()), {'error', 'request_id'})
            self.assertEqual(result.json()['error'], 'storage_unavailable')
        self.assertEqual(self.fixture.host.maddy.calls, [])

    async def test_real_busy_sqlite_cannot_authorize_work_or_report_success_and_recovers(self):
        with closing(sqlite3.connect(self.fixture.database.path)) as writer:
            writer.execute('BEGIN IMMEDIATE')
            try:
                await self.assert_closed()
            finally:
                writer.rollback()
        self.assertEqual((await self.client.get('/api/accounts')).status_code, 200)

    async def test_real_corrupt_database_cannot_authorize_work_or_reinitialize_state(self):
        path = self.fixture.database.path
        original = path.read_bytes()
        path.write_bytes(b'corrupt disposable fixture database')
        try:
            await self.assert_closed()
            self.assertEqual(path.read_bytes(), b'corrupt disposable fixture database')
        finally:
            path.write_bytes(original)
        self.assertEqual((await self.client.get('/api/accounts')).status_code, 200)

    async def test_native_sqlite_full_rolls_back_challenge_and_cannot_claim_a_committed_login(self):
        with self.fixture.database.transaction() as database:
            database.executescript('CREATE TABLE fixture_full_payload(value BLOB); '
                'CREATE TRIGGER fixture_force_full BEFORE INSERT ON auth_challenges BEGIN '
                'INSERT INTO fixture_full_payload VALUES(zeroblob(1048576)); END;')
            before = database.execute('SELECT COUNT(*) FROM auth_challenges').fetchone()[0]
        fault = FaultedConnections(self.fixture.database.path, 'full')
        with patch('backend.database.sqlite3.connect', side_effect=fault):
            result = await self.client.post('/api/login', json={
                'email': 'rsalehin24@gmail.com', 'password': PASSWORD,
            })
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.json()['error'], 'storage_unavailable')
        self.assertNotIn('challenge', result.json())
        self.assertNotIn('set-cookie', result.headers)
        self.assertIn(sqlite3.SQLITE_FULL, fault.errors)
        with self.fixture.database.transaction() as database:
            self.assertEqual(database.execute('SELECT COUNT(*) FROM auth_challenges')
                             .fetchone()[0], before)
            self.assertEqual(database.execute('SELECT COUNT(*) FROM fixture_full_payload')
                             .fetchone()[0], 0)
        self.assertEqual((await self.client.get('/api/accounts')).status_code, 200)
