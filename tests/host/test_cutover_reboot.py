import os
import unittest
from datetime import UTC, datetime
from pathlib import Path

from host.release_files import file_digest
from shared.files import protected_json
from tests.cutover_reboot_support import load_legacy, restore_fixture
from tests.host_reboot_support import boot_id, require_reboot
from tests.host_support import GuestHelper
from tests.kernel_scenario_support import require_matching_source
from tests.legacy_systemd_support import systemctl
from tools.qualification_bundle import QualificationBundle, validate_report_time
from tools.run_tests import timestamp


class CutoverRebootTests(unittest.TestCase):
    def test_timers_stay_disabled_before_and_during_cutover_across_a_real_kernel_boot(self):
        host = GuestHelper.load()
        bundle = QualificationBundle(Path(os.environ['MAIL_ADMIN_HOST_EVIDENCE']))
        bundle.verify_source()
        path = Path(os.environ['MAIL_ADMIN_CUTOVER_REBOOT'])
        self.assertEqual(path.name, 'prepared.json')
        self.assertEqual(path.parent.parent, Path('/root/mail-admin-fixture'))
        self.assertTrue(path.parent.name.startswith('cutover-reboot-'))
        record = protected_json(path)
        self.assertEqual(record['scope'], 'frozen-disposable-cutover-reboot')
        self.assertEqual(record['instance_id'], host.record['instance_id'])
        self.assertEqual(record['runtime_image_id'], host.record['runtime_image_id'])
        for key, value in bundle.identity.items():
            self.assertEqual(record[key], value)
        require_matching_source(record)
        require_reboot(record, {'instance_id': host.record['instance_id'], 'boot_id': boot_id()})
        completed = timestamp()
        validate_report_time({**bundle.context, 'now': datetime.now(UTC)},
                             {**record, 'completed_at_utc': completed})
        self.assertEqual(record['paused_boundary'], ['cutover_installation', 2])
        before = load_legacy(Path(record['before_root']), record['instance_id'])
        fixture, operation = restore_fixture(record)
        self.addCleanup(before.close)
        self.addCleanup(fixture.close)
        for legacy in (before, fixture.legacy):
            self.assertEqual(systemctl(('show', legacy.unit('timer'),
                                        '--property=UnitFileState', '--value')), 'disabled')
            self.assertEqual(systemctl(('show', legacy.unit('timer'),
                                        '--property=ActiveState', '--value')), 'inactive')
            self.assertFalse((legacy.root / 'writer-started.json').exists())
            self.assertFalse((legacy.root / 'service-observations.json').exists())
        self.assertEqual(protected_json(operation.outcome)['phase'], 'installing')
        operation.rollback()
        self.assertEqual(protected_json(operation.outcome)['phase'], 'rolled_back')
        self.assertEqual({str(path): [file_digest(path), path.stat().st_mode & 0o777]
                          for path in operation.package.files.targets}, record['original_files'])
