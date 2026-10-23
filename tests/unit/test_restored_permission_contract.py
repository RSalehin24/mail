import os
import unittest

from tests.recovery_web_support import private_data, require_mode
from tests.support import StateFixture


class RestoredPermissionContractTests(unittest.TestCase):
    def setUp(self):
        self.fixture = StateFixture()
        self.addCleanup(self.fixture.close)
        self.directory = self.fixture.root / 'data'
        self.directory.mkdir(mode=0o700)

    def test_empty_bind_placeholders_do_not_relax_database_and_message_permissions(self):
        placeholder = self.directory / 'maddy.conf'
        placeholder.touch(mode=0o644)
        database = self.directory / 'credentials.db'
        database.touch(mode=0o600)
        private_data(self.directory, {'maddy.conf', 'tls'})
        database.chmod(0o644)
        with self.assertRaises(RuntimeError):
            private_data(self.directory, {'maddy.conf', 'tls'})
        database.chmod(0o600)
        placeholder.write_bytes(b'unexpected content')
        with self.assertRaises(RuntimeError):
            private_data(self.directory, {'maddy.conf', 'tls'})

    def test_symlinks_and_unrelated_read_permissions_are_rejected(self):
        path = self.directory / 'message'
        path.touch(mode=0o600)
        require_mode(path, (os.getuid(), 0o600))
        with self.assertRaises(RuntimeError):
            require_mode(path, (os.getuid(), 0o640))
        (self.directory / 'linked-message').symlink_to(path)
        with self.assertRaises(RuntimeError):
            private_data(self.directory, set())
