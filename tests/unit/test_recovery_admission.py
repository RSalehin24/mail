import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from host.mailbox_archive import MailboxArchives
from shared.errors import ServiceError


class RecoveryAdmissionTests(unittest.TestCase):
    def test_space_and_operation_limits_reject_before_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            archive = MailboxArchives(root / 'points', root / 'key', os.getuid())
            database = root / 'imapsql.db'
            bodies = root / 'messages'
            bodies.mkdir()
            with sqlite3.connect(database) as connection:
                connection.execute('CREATE TABLE users(id INTEGER,username TEXT)')
                connection.execute('CREATE TABLE extKeys(id TEXT,uid INTEGER)')
                connection.execute("INSERT INTO users VALUES(1,'cloud@rsalehin24.me')")
            with patch('host.mailbox_archive.os.statvfs', return_value=SimpleNamespace(
                f_bavail=1, f_frsize=4096,
            )), self.assertRaisesRegex(ServiceError, 'insufficient_recovery_space'):
                archive.admit(database, bodies, 'cloud@rsalehin24.me')
            with database.open('r+b') as writer:
                writer.truncate(33 * 1024**2)
            with self.assertRaisesRegex(ServiceError, 'recovery_operation_budget_exceeded'):
                archive.admit(database, bodies, 'cloud@rsalehin24.me')
            self.assertEqual(list((root / 'points').iterdir()), [])


class SharedRecoveryStoreTests(unittest.TestCase):
    """A 0750 store lets the dashboard's group read it; anything wider is still rejected."""

    def store(self, mode):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        root = Path(directory.name) / 'points'
        root.mkdir()
        root.chmod(mode)
        return root

    def test_a_group_readable_store_is_accepted_and_shares_new_files_with_its_group(self):
        root = self.store(0o750)
        archive = MailboxArchives(root, root.parent / 'key', os.getuid())
        self.assertEqual(archive._shared_group(), root.stat().st_gid)
        key = root.parent / 'key'
        key.chmod(0o600)
        with patch('host.mailbox_archive.os.chown') as change_group:
            archive._share(key)
        change_group.assert_called_once_with(key, -1, root.stat().st_gid)
        self.assertEqual(key.stat().st_mode & 0o777, 0o640)

    def test_a_private_store_shares_nothing(self):
        root = self.store(0o700)
        archive = MailboxArchives(root, root.parent / 'key', os.getuid())
        self.assertIsNone(archive._shared_group())
        archive._share(root.parent / 'key')
        self.assertEqual((root.parent / 'key').stat().st_mode & 0o777, 0o600)

    def test_group_write_or_any_other_access_is_rejected(self):
        for mode in (0o770, 0o705, 0o755, 0o702):
            with self.subTest(mode=oct(mode)), self.assertRaisesRegex(
                ServiceError, 'recovery_storage_invalid'
            ):
                root = self.store(mode)
                MailboxArchives(root, root.parent / 'key', os.getuid())
