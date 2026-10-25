import os
import unittest
from pathlib import Path

from shared.files import protected_json
from tests.host_support import GuestHelper
from tests.recovery_activation_support import verify_mail
from tests.recovery_boot_support import RestoredBoot
from tests.recovery_evidence_support import ReplacementEvidence
from tests.recovery_web_support import RecoveredHttps, restored_permissions
from tools.qualification_bundle import QualificationBundle


class ReplacementRecoveryTests(unittest.TestCase):
    def setUp(self):
        host = GuestHelper.load()
        bundle = QualificationBundle(Path(os.environ['MAIL_ADMIN_HOST_EVIDENCE']))
        bundle.verify_source()
        context = protected_json(Path(os.environ['MAIL_ADMIN_REPLACEMENT_CONTEXT']))
        self.assertEqual(context['target_instance_id'], host.record['instance_id'])
        self.evidence = ReplacementEvidence(bundle, context)

    def test_actual_immutable_off_vm_snapshot_is_verified_before_publication_and_download(self):
        self.evidence.verify_transfer()
        self.evidence.verify_isolation()

    def test_complete_replacement_has_real_keys_boot_journal_tls_acme_mail_and_https(self):
        self.evidence.verify_transfer()
        self.evidence.verify_isolation()
        self.evidence.verify_phases()
        self.evidence.verify_real_acme_state()
        boot = RestoredBoot(self.evidence.target)
        boot.require_original_credentials()
        for role in ('maddy', 'mail-admin'):
            boot.consumer(role)
        boot.mail_and_helper()
        expected = self.evidence.target.context['message_observation']
        self.assertTrue(verify_mail(expected)['post_capture_message_absent'])
        self.assertTrue(restored_permissions()['restored_data_permissions_restrictive'])
        web = RecoveredHttps(10)
        web.cookie = protected_json(self.evidence.root / 'verified-cookie.json')['cookie']
        self.assertEqual(web.api('GET', '/api/accounts')[0], 200)
        profile = protected_json(Path('/etc/mail-admin/deployment.json'))
        self.assertEqual(profile['consumers']['mail-admin']['environment']
                         ['OPERATION_TIMEOUT_SECONDS'], '5')

    def test_off_source_key_decrypts_the_snapshot_and_selected_recovery_objectives_hold(self):
        self.evidence.verify_transfer()
        self.assertTrue(self.evidence.phase('activate')['relay_decrypted_with_matching_keys'])
        self.evidence.verify_phases()
        self.assertEqual(self.evidence.objectives()['result'], 'pass')
