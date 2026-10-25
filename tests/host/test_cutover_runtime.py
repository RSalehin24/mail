import os
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from host.release_files import file_digest
from shared.errors import ServiceError
from shared.files import protected_json
from tests.cutover_systemd_support import CutoverSystemdFixture
from tests.host_support import GuestHelper
from tests.legacy_systemd_support import systemctl
from tools.qualification_bundle import QualificationBundle


class CutoverRuntimeTests(unittest.TestCase):
    def setUp(self):
        host = GuestHelper.load()
        bundle = QualificationBundle(Path(os.environ['MAIL_ADMIN_HOST_EVIDENCE']))
        bundle.verify_source()
        self.host_record = host.record
        self.identity = {**bundle.identity, 'qualification_suite_sha256':
                         file_digest(bundle.artifacts['qualification_suite'])}
        self.fixture = None
        self.addCleanup(self.close_fixture)
        self.prepare_fixture()

    def close_fixture(self):
        if self.fixture is not None:
            self.fixture.close()
            self.fixture = None

    def prepare_fixture(self):
        self.close_fixture()
        self.fixture = CutoverSystemdFixture(self.host_record['instance_id'], self.identity)
        self.fixture.prepare_consumers(self.host_record['runtime_image_id'])
        self.fixture.legacy.guard.enter({'manual_renewal_inhibited': True})

    def assert_paused(self):
        self.assertEqual(systemctl(('show', self.fixture.legacy.unit('timer'),
                                    '--property=UnitFileState', '--value')), 'disabled')
        self.assertEqual(systemctl(('show', self.fixture.legacy.unit('timer'),
                                    '--property=ActiveState', '--value')), 'inactive')

    def fingerprints(self, operation):
        return {name: operation.host._consumer(name)
                for name in operation.host.context.deployment['consumers']}

    def assert_original(self, operation, original):
        files, consumers = original
        self.assertEqual({path: (file_digest(path), path.stat().st_mode & 0o777)
                          for path in operation.package.files.targets}, files)
        self.assertEqual(self.fingerprints(operation), consumers)

    def original(self, operation):
        return ({path: (file_digest(path), path.stat().st_mode & 0o777)
                 for path in operation.package.files.targets}, self.fingerprints(operation))

    def test_loaded_service_takes_both_locks_and_missed_timer_runs_follow_served_checks(self):
        operation = self.fixture.operation()
        stamp = Path('/var/lib/systemd/timers') / ('stamp-' + self.fixture.legacy.unit('timer'))
        yesterday = time.time() - 86400
        os.utime(stamp, (yesterday, yesterday))
        operation.run()
        self.assertEqual(protected_json(operation.outcome)['phase'], 'complete')
        path = self.fixture.root / 'service-observations.json'
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            runs = protected_json(path)['runs']
            if len(runs) >= 2:
                break
            time.sleep(0.1)
        self.assertGreaterEqual(len(runs), 2)
        self.assertTrue(all(run['shared_locks_available'] for run in runs))
        self.assertTrue(runs[-1]['served_validation_preceded_timer'])
        operation.host._loaded_service()
        self.assertFalse((self.fixture.root / 'writer-started.json').exists())
        operation.rollback()
        self.assertEqual(protected_json(operation.outcome)['phase'], 'rolled_back')

    def test_every_real_forward_file_and_activation_transition_requires_matched_rollback(self):
        for phase in ('activating', 'verifying_service', 'enabling_schedule', 'complete'):
            with self.subTest(phase=phase):
                self.fail_forward_phase(phase)
        for index in range(4):
            with self.subTest(installed_file=index):
                self.fail_file_install(index)

    def fail_forward_phase(self, selected):
        self.prepare_fixture()
        operation = self.fixture.operation()
        original = self.original(operation)
        phase = operation._phase

        def interrupted(value):
            phase(value)
            if value == selected:
                raise ServiceError('fixture_cutover_interruption', 503)

        with patch.object(operation, '_phase', side_effect=interrupted):
            with self.assertRaisesRegex(ServiceError, 'fixture_cutover_interruption'):
                operation.run()
        self.assert_paused()
        operation.rollback()
        self.assert_original(operation, original)

    def fail_file_install(self, selected):
        self.prepare_fixture()
        operation = self.fixture.operation()
        original = self.original(operation)
        replace = operation.package.files.replace
        calls = []

        def interrupted(path, content):
            replace(path, content)
            calls.append(path)
            if len(calls) == selected + 1:
                raise OSError('fixture interrupted after actual host-file replacement')

        with patch.object(operation.package.files, 'replace', side_effect=interrupted):
            with self.assertRaisesRegex(OSError, 'after actual host-file replacement'):
                operation.run()
        self.assert_paused()
        operation.rollback()
        self.assert_original(operation, original)

    def test_every_interrupted_real_rollback_stays_paused_until_its_matched_retry_is_verified(self):
        for selected in ('rollback_installing', 'rollback_activating', 'rollback_verifying',
                          'rollback_enabling_schedule', 'rolled_back'):
            with self.subTest(phase=selected):
                self.fail_rollback_phase(selected)

    def fail_rollback_phase(self, selected):
        self.prepare_fixture()
        operation = self.fixture.operation()
        original = self.original(operation)
        operation.run()
        phase = operation._phase

        def interrupted(value):
            phase(value)
            if value == selected:
                raise ServiceError('fixture_rollback_interruption', 503)

        with patch.object(operation, '_phase', side_effect=interrupted):
            with self.assertRaisesRegex(ServiceError, 'fixture_rollback_interruption'):
                operation.rollback()
        self.assert_paused()
        operation.rollback()
        self.assert_original(operation, original)
