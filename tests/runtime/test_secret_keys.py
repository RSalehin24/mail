"""Check persistent keys through actual non-root HTTPS container lifecycles."""

import unittest
from itertools import product

from tests.key_fault_support import missing_key, unreadable_key, wrong_key
from tests.runtime_auth_support import RuntimeAuthentication
from tests.runtime_support import RuntimeFixture


class RuntimeSecretKeyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = RuntimeFixture()
        self.addCleanup(self.fixture.close)
        self.fixture.start()
        authentication = RuntimeAuthentication(self, self.fixture)
        authentication.enroll()
        authentication.login()

    def materials(self):
        paths = [self.fixture.root / 'keys' / name
                 for name in ('encryption.key', 'verifier.key')]
        paths.append(self.fixture.root / 'installation.json')
        return {path: path.read_bytes() for path in paths}

    def test_key_bytes_identity_and_sessions_survive_actual_container_recreation(self):
        before = self.materials()
        previous = self.fixture.identifier
        self.fixture.command(('rm', '--force', previous))
        self.fixture.identifier = ''
        self.fixture._start_container()
        self.assertNotEqual(self.fixture.identifier, previous)
        self.assertEqual(self.materials(), before)
        self.assertEqual(self.fixture.api('GET', '/api/accounts')[0], 200)
        self.assertEqual(self.fixture.api('POST', '/api/setup', {'token': 'invented'})[0], 403)

    def test_missing_wrong_and_unreadable_keys_refuse_startup_without_generating_replacements(self):
        before = self.materials()
        cases = product(('encryption.key', 'verifier.key'),
                        (missing_key, wrong_key, unreadable_key))
        for name, fault in cases:
            with self.subTest(key=name, fault=fault.__name__):
                self.assert_rejected_key(self.fixture.root / 'keys' / name, fault)
                self.assertEqual(self.materials(), before)
                self.fixture.command(('start', self.fixture.identifier))
                self.fixture.ready()
                self.assertEqual(self.fixture.api('GET', '/api/accounts')[0], 200)

    def assert_rejected_key(self, path, fault):
        self.fixture.command(('stop', self.fixture.identifier))
        with fault(path) as expected:
            self.fixture.command(('start', self.fixture.identifier))
            state = self.fixture.exited()
            self.assertFalse(state['Running'])
            self.assertNotEqual(state['ExitCode'], 0)
            logs = self.fixture.command(('logs', '--tail', '30', self.fixture.identifier))
            self.assertIn(expected, logs.stdout + logs.stderr)
            if fault == missing_key:
                self.assertFalse(path.exists())
