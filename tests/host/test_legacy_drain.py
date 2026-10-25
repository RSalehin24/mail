import subprocess
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from host.baseline import BaselineScope
from host.coordination import MaintenanceLocks
from host.locks import LockPolicy
from shared.errors import ServiceError
from shared.files import atomic_json, protected_json
from tests.host_support import GuestHelper
from tests.legacy_systemd_support import LegacySystemdFixture, systemctl


class LegacyDrainTests(unittest.TestCase):
    def setUp(self):
        self.fixture = LegacySystemdFixture(GuestHelper.load().record['instance_id'])
        self.fixture.provision()
        self.addCleanup(self.fixture.close)
        locks = [self.fixture.root / name for name in ('renewal.lock', 'maddy.lock')]
        for path in locks:
            path.touch(mode=0o600)
        self.scope = BaselineScope(MaintenanceLocks(*locks, LockPolicy(1)),
                                    (self.fixture.guard, None))

    def test_real_managed_legacy_writer_drains_before_baseline_scope_can_change_files(self):
        self.fixture.start_writer()
        target = self.fixture.root / 'untouched-before-baseline'
        target.write_bytes(b'original deployment')
        target.chmod(0o640)
        with ThreadPoolExecutor(max_workers=1) as workers:
            future = workers.submit(self.fixture.guard.enter, {'manual_renewal_inhibited': True})
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                paused = systemctl(('show', self.fixture.unit('timer'),
                                     '--property=UnitFileState', '--value'))
                if paused == 'disabled':
                    break
                time.sleep(0.1)
            self.assertFalse(future.done())
            with self.assertRaises(ServiceError), self.scope.consistent():
                target.write_bytes(b'premature capture or permission change')
            self.assertEqual(target.read_bytes(), b'original deployment')
            self.assertEqual(target.stat().st_mode & 0o777, 0o640)
            self.fixture.release()
            future.result(timeout=30)
        with self.scope.consistent():
            target.write_bytes(b'baseline allowed only after observed completion')
        self.assertTrue((self.fixture.root / 'writer-completed.json').is_file())
        self.assertEqual(protected_json(self.fixture.guard.record)['state'], 'legacy_drained')
        self.fixture.save({'result': 'pass', 'actual_managed_writer_drained': True})

    def test_real_manual_writer_leaves_completion_unknown_and_blocks_the_baseline_after_exit(self):
        atomic_json(self.fixture.guard.record, {'state': 'entering'})
        systemctl(('disable', '--now', self.fixture.unit('timer')))
        process = subprocess.Popen(self.fixture.writer_arguments(), stdout=subprocess.DEVNULL,
                                   stderr=subprocess.DEVNULL)
        try:
            self.fixture.wait_for('writer-started.json')
            with self.assertRaisesRegex(ServiceError, 'legacy_manual_run_active'):
                self.fixture.guard.verify()
            self.fixture.release()
            self.assertEqual(process.wait(timeout=15), 0)
            with self.assertRaisesRegex(ServiceError, 'legacy_manual_completion_unknown'):
                with self.scope.consistent():
                    self.fail('Unknown manual completion entered baseline capture')
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
        self.fixture.save({'result': 'pass', 'manual_completion_stayed_unknown': True})
