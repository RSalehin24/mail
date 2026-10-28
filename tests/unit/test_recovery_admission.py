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
