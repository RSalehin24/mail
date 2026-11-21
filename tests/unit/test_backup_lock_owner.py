import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from host import backup_operations
from host.backup_operations import BackupOperations


class BackupLockOwnerTests(unittest.TestCase):
    """The shared /run/lock/mail-admin-backup.lock belongs to the deployment's state owner."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.lock = Path(directory.name) / "mail-admin-backup.lock"
        self.lock.touch(mode=0o600)
        patcher = mock.patch.object(backup_operations, "BACKUP_LOCK", self.lock)
        patcher.start()
        self.addCleanup(patcher.stop)

    def operations(self, **deployment):
        backup = mock.MagicMock()
        backup.verify_latest.return_value = {"verified": True}
        record = {"inputs": {"lock_wait_seconds": 1}, **deployment}
        return BackupOperations(backup, record)

    def test_a_lock_owned_by_the_state_owner_is_accepted(self):
        operations = self.operations(state_owner_uid=os.getuid())
        self.assertEqual(operations.verify(), {"verified": True})

    def test_the_retention_lock_stays_root_owned(self):
        self.assertEqual(self.operations(state_owner_uid=10001).lock_policy.owner, 0)
