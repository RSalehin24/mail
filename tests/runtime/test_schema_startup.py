import sqlite3
import unittest
from contextlib import closing

from shared.validation import SCHEMA_VERSION
from tests.runtime_auth_support import RuntimeAuthentication
from tests.runtime_support import RuntimeFixture
from tests.schema_fault_support import interrupted_schema


class SchemaStartupTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RuntimeFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.start()
        authentication = RuntimeAuthentication(self, self.fixture)
        authentication.enroll()
        authentication.login()

    def assert_closed_startup(self):
        try:
            self.fixture.command(('start', self.fixture.identifier))
            state = self.fixture.exited()
            self.assertFalse(state['Running'])
            self.assertFalse(state['OOMKilled'])
            self.assertNotEqual(state['ExitCode'], 0)
            logs = self.fixture.command(('logs', '--tail', '30', self.fixture.identifier))
            self.assertIn('incompatible_schema', logs.stdout + logs.stderr)
        finally:
            self.fixture.command(('stop', self.fixture.identifier))

    def assert_compatible_restart(self):
        self.fixture.command(('start', self.fixture.identifier))
        self.fixture.ready()
        self.assertEqual(self.fixture.api('GET', '/api/accounts')[0], 200)
        self.assertEqual(self.fixture.api('POST', '/api/setup', {'token': 'invented'})[0], 403)

    def test_incompatible_schema_exits_without_accepting_writes_or_replacing_existing_state(self):
        self.fixture.command(('stop', self.fixture.identifier))
        with closing(sqlite3.connect(self.fixture.database.path)) as database:
            database.execute('PRAGMA user_version=99')
        try:
            self.assert_closed_startup()
            with closing(sqlite3.connect(self.fixture.database.path)) as database:
                self.assertEqual(database.execute('PRAGMA user_version').fetchone()[0], 99)
        finally:
            with closing(sqlite3.connect(self.fixture.database.path)) as database:
                database.execute('PRAGMA user_version=' + str(SCHEMA_VERSION))
        self.assert_compatible_restart()

    def test_interrupted_real_schema_commit_remains_closed_until_compatible_state_is_restored(self):
        self.fixture.command(('stop', self.fixture.identifier))
        original = self.fixture.database.path.read_bytes()
        with interrupted_schema(self.fixture.database):
            self.assert_closed_startup()
        self.assertEqual(self.fixture.database.path.read_bytes(), original)
        self.assert_compatible_restart()
