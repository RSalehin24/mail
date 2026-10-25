import os
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

from host.certificates import CertificatePolicy, GenerationPaths, Generations, PairValidator
from host.coordination import Coordination, MaintenanceLocks
from host.locks import HostLock, LockPolicy
from shared.errors import ServiceError
from shared.files import atomic_json
from tests.helper_support import HelperFixture


class CoordinationExclusionTests(unittest.TestCase):
    def setUp(self):
        self.fixture = HelperFixture()
        self.addCleanup(self.fixture.close)
        state = self.fixture.root / 'generations.json'
        atomic_json(state, {'consumers': {}})
        self.generations = Generations(GenerationPaths(self.fixture.root, state),
            (CertificatePolicy(os.getgid(), os.getuid()), PairValidator(), time.time))
        self.renewal = self.fixture.root / 'renewal.lock'
        self.renewal.touch(mode=0o600)
        locks = MaintenanceLocks(self.renewal, self.fixture.lock, LockPolicy(0.2, os.getuid()))
        self.coordination = Coordination(locks, (self.fixture.journal, self.generations))

    def require_consistent(self):
        with self.coordination.consistent():
            return True

    def test_real_contended_renewal_is_acquired_before_maddy_and_waits_are_bounded(self):
        entered, paths = threading.Event(), []
        acquire = HostLock._acquire

        def observed(lock):
            paths.append(lock.path)
            if lock.path == self.renewal:
                entered.set()
            return acquire(lock)

        with HostLock(self.renewal, self.coordination.locks.policy):
            self.assert_maddy_remains_free_while_renewal_waits(observed, entered)
        self.assertEqual(paths[0], self.renewal)
        self.assertTrue(self.require_consistent())

    def assert_maddy_remains_free_while_renewal_waits(self, observed, entered):
        with (ThreadPoolExecutor(max_workers=1) as executor,
              patch('host.locks.HostLock._acquire', new=observed)):
            started = time.monotonic()
            future = executor.submit(self.require_consistent)
            self.assertTrue(entered.wait(1))
            with HostLock(self.fixture.lock, self.coordination.locks.policy):
                self.assertFalse(future.done())
            with self.assertRaisesRegex(ServiceError, 'maintenance_busy'):
                future.result(timeout=1)
            self.assertLess(time.monotonic() - started, 1)

    def test_actual_stable_lock_files_reject_modes_symlinks_and_unexpected_owners(self):
        policy = self.coordination.locks.policy
        before = [path.stat().st_ino for path in (self.renewal, self.fixture.lock)]
        for path in (self.renewal, self.fixture.lock):
            self.assert_unsafe_lock_rejected(path, policy)
        with self.coordination.consistent():
            self.assertEqual(before, [p.stat().st_ino for p in (self.renewal, self.fixture.lock)])

    def assert_unsafe_lock_rejected(self, path, policy):
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

    def test_durable_unknown_writers_exclude_maintenance_after_their_locks_are_released(self):
        request = self.fixture.request('create', 'coordination@rsalehin24.me')
        self.fixture.journal.begin(request, (time.time(), {}))
        self.fixture.journal.update(request.request_id, {'state': 'unknown'})
        with self.assertRaisesRegex(ServiceError, 'helper_reconciliation_required'):
            self.require_consistent()
        self.fixture.journal.update(request.request_id, {'state': 'failed'})
        state = self.generations.read()
        state['acme_run'] = {'state': 'unknown', 'container_id': 'unresolved-fixture-writer'}
        self.generations.write(state)
        with self.assertRaisesRegex(ServiceError, 'acme_completion_unknown'):
            self.require_consistent()
        state['acme_run']['state'] = 'completed'
        self.generations.write(state)
        self.assertTrue(self.require_consistent())
