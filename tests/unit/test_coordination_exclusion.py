import os
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from host.coordination import Coordination, MaintenanceLocks
from host.locks import HostLock, LockPolicy
from shared.errors import ServiceError
from tests.helper_support import HelperFixture


class CoordinationExclusionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = HelperFixture()
        self.addCleanup(self.fixture.close)
        locks = MaintenanceLocks(self.fixture.lock, LockPolicy(0.2, os.getuid()))
        self.coordination = Coordination(locks, self.fixture.journal)

    def require_consistent(self):
        with self.coordination.consistent():
            return True

    def test_a_contended_maddy_lock_wait_is_bounded(self):
        with HostLock(self.fixture.lock, self.coordination.locks.policy):
            with ThreadPoolExecutor(max_workers=1) as executor:
                started = time.monotonic()
                future = executor.submit(self.require_consistent)
                with self.assertRaisesRegex(ServiceError, 'maintenance_busy'):
                    future.result(timeout=1)
                self.assertLess(time.monotonic() - started, 1)
        self.assertTrue(self.require_consistent())

    def test_actual_stable_lock_file_rejects_modes_symlinks_and_unexpected_owners(self):
        path, policy = self.fixture.lock, self.coordination.locks.policy
        before = path.stat().st_ino
        path.chmod(0o640)
        with self.assertRaisesRegex(ServiceError, 'unsafe_lock_file'):
            with HostLock(path, policy):
                self.fail('Unsafe mode was accepted')
        path.chmod(0o600)
        with self.assertRaisesRegex(ServiceError, 'unsafe_lock_file'):
            with HostLock(path, LockPolicy(0.2, os.getuid() + 1)):
                self.fail('Unexpected owner was accepted')
        link = path.with_name(path.name + '.link')
        link.symlink_to(path)
        with self.assertRaises(OSError):
            with HostLock(link, policy):
                self.fail('A symlink lock was accepted')
        with self.coordination.consistent():
            self.assertEqual(before, path.stat().st_ino)

    def test_durable_unknown_writers_exclude_maintenance_after_their_locks_are_released(self):
        request = self.fixture.request('create', 'coordination@rsalehin24.me')
        self.fixture.journal.begin(request, (time.time(), {}))
        self.fixture.journal.update(request.request_id, {'state': 'unknown'})
        with self.assertRaisesRegex(ServiceError, 'helper_reconciliation_required'):
            self.require_consistent()
        self.fixture.journal.update(request.request_id, {'state': 'failed'})
        self.assertTrue(self.require_consistent())
