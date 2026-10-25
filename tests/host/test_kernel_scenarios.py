import os
import unittest
from pathlib import Path

from shared.files import protected_json
from tests.host_reboot_support import require_reboot
from tests.host_support import GuestHelper
from tests.kernel_scenario_support import STAGING_BOUNDARIES, require_matching_source
from tools.maddy_fixture import MADDY_FIXTURE_IDENTITIES
from tools.qualification_bundle import QualificationBundle, validate_report_time


class KernelScenarioTests(unittest.TestCase):
    def observation(self, identifier):
        host = GuestHelper.load()
        bundle = QualificationBundle(Path(os.environ['MAIL_ADMIN_HOST_EVIDENCE']))
        bundle.verify_source()
        path = Path(os.environ['MAIL_ADMIN_KERNEL_OBSERVATION'])
        self.assertEqual(path.name, 'observation.json')
        self.assertEqual(path.parent.parent, Path('/root/mail-admin-fixture'))
        self.assertTrue(path.parent.name.startswith('kernel-'))
        record = protected_json(path)
        self.assertEqual(record['result'], 'pass')
        self.assertIn(identifier, record['selected_cases'])
        self.assertEqual(record['scope'], 'frozen-disposable-kernel-scenarios')
        self.assertEqual(record['instance_id'], host.record['instance_id'])
        self.assertEqual(record['runtime_image_id'], host.record['runtime_image_id'])
        self.assertIn(record['maddy_fixture_image_id'], MADDY_FIXTURE_IDENTITIES)
        for key, value in bundle.identity.items():
            self.assertEqual(record[key], value)
        require_matching_source(record)
        require_reboot(record, {'instance_id': record['instance_id'],
                                'boot_id': record['new_boot_id']})
        validate_report_time(bundle.context, record)
        memory = record['controller_memory']
        self.assertEqual(memory['state'], 'observed')
        self.assertEqual(memory['limit_bytes'], 192 * 1024**2)
        self.assertLessEqual(memory['peak_bytes'], memory['limit_bytes'])
        return record

    def test_kernel_power_cut_at_all_staging_publication_and_consumer_boundaries_recovers(self):
        record = self.observation('T15.03')
        self.assertEqual([tuple(item['boundary']) for item in record['staging']],
                         list(STAGING_BOUNDARIES))
        self.assertEqual(tuple(record['activation']['boundary']), ('consumer', 'maddy'))
        self.assertIs(record['staging_verified'], True)
        self.assertIs(record['consumer_activation_verified'], True)

    def test_referenced_and_rollback_pairs_survive_retention_and_an_actual_kernel_boot(self):
        record = self.observation('T15.06')
        self.assertIs(record['retention_verified'], True)
        self.assertIs(record['consumer_activation_verified'], True)

    def test_interrupted_offline_recovery_survives_kernel_boot_without_renewal_restarting_web(self):
        record = self.observation('T05.05')
        self.assertEqual(tuple(record['recovery']['boundary']), ('offline_utility', None))
        self.assertIs(record['offline_recovery_stayed_closed'], True)
