import os
import tempfile
import unittest
from pathlib import Path

from host.locks import HostLock, LockPolicy
from host.preflight import provision_lock
from shared.errors import ServiceError

DASHBOARD_UID = 10001
ROOT_UID = 0


class SharedStateOwnershipTests(unittest.TestCase):
    """Runs as root in the controller, like the host workers that prepare the lock files."""

    def setUp(self):
        self.assertEqual(os.geteuid(), ROOT_UID, "The controller must run as root")
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.lock = Path(directory.name) / "mail-admin-maddy.lock"

    def test_root_provisions_a_lock_owned_by_the_dashboard_user_and_both_can_use_it(self):
        provision_lock(self.lock, DASHBOARD_UID)
        metadata = self.lock.lstat()
        self.assertEqual((metadata.st_uid, metadata.st_mode & 0o777), (DASHBOARD_UID, 0o600))
        with HostLock(self.lock, LockPolicy(1, DASHBOARD_UID)):
            pass

    def test_the_lock_is_refused_under_a_policy_naming_a_different_owner(self):
        provision_lock(self.lock, DASHBOARD_UID)
        with self.assertRaises(ServiceError), HostLock(self.lock, LockPolicy(1, ROOT_UID)):
            pass

    def test_an_existing_lock_with_the_wrong_owner_is_never_taken_over(self):
        provision_lock(self.lock, ROOT_UID)
        with self.assertRaises(ServiceError) as caught:
            provision_lock(self.lock, DASHBOARD_UID)
        self.assertEqual(caught.exception.code, "unsafe_lock_file")
