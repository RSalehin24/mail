"""Private HTTP-01 issuance with real Certbot and real certificate validation."""

from host.certificates import PairValidator
from shared.errors import ServiceError
from tests.certbot_case_support import PrivateAcmeCase
from tests.renewal_support import PrivateRenewal


class CertbotIssuanceTests(PrivateAcmeCase):
    def test_real_issuance_and_ordinary_renewal_preserve_a_current_complete_pair(self):
        certbot = self.certbot
        validator = PairValidator(self.network.root / 'issuance-root.pem')
        certificate = validator.validate(certbot.pair)
        pair = {name: (certbot.pair / name).read_bytes()
                for name in ('fullchain.pem', 'privkey.pem')}
        renewed = certbot.renew()
        self.assertEqual(renewed.returncode, 0, (renewed.stdout + renewed.stderr)[-5000:])
        self.assertEqual(validator.validate(certbot.pair), certificate)
        self.assertEqual({name: (certbot.pair / name).read_bytes() for name in pair}, pair)
        self.assertIn('not due for renewal', renewed.stdout + renewed.stderr)
        self.assertTrue(all(run['exit_code'] == 0 for run in certbot.runs))

    def test_host_renewal_publishes_a_new_pair_only_when_certbot_decides_it_is_due(self):
        host = PrivateRenewal(self.certbot)
        host.renewal.run(self.certbot.pair)
        original = host.generations.read()
        host.renewal.run(self.certbot.pair)
        self.assertEqual(host.generations.read()['desired_generation'],
                         original['desired_generation'])
        self.certbot.make_renewal_due()
        host.renewal.run(self.certbot.pair)
        changed = host.generations.read()
        self.assertNotEqual(changed['desired_generation'], original['desired_generation'])
        self.assertNotEqual(changed['served_sha256'], original['served_sha256'])
        self.assertEqual(changed['acme_run']['state'], 'completed')
        self.assertEqual(changed['acme_run']['exit_code'], 0)
        self.assertEqual(host.generations.validator.validate(self.certbot.pair)['served_sha256'],
                         changed['served_sha256'])

    def test_failed_real_http01_validation_preserves_the_previous_pair_and_generation(self):
        host = PrivateRenewal(self.certbot)
        host.renewal.run(self.certbot.pair)
        previous = host.generations.read()
        pair = {name: (self.certbot.pair / name).read_bytes()
                for name in ('fullchain.pem', 'privkey.pem')}
        self.certbot.make_renewal_due()
        self.network.stop_dns()
        with self.assertRaisesRegex(ServiceError, 'acme_completion_unknown'):
            host.renewal.run(self.certbot.pair)
        host.certbot.reconcile()
        failed = host.generations.read()
        self.assertEqual(failed['acme_run']['state'], 'completed')
        self.assertNotEqual(failed['acme_run']['exit_code'], 0)
        self.assertEqual(failed['desired_generation'], previous['desired_generation'])
        self.assertEqual({name: (self.certbot.pair / name).read_bytes() for name in pair}, pair)
        self.assertEqual(host.generations.validator.validate(self.certbot.pair)['served_sha256'],
                         previous['served_sha256'])
        logs = self.fixture.command(('logs', failed['acme_run']['container_id']))
        self.assertIn('Certbot failed to authenticate', logs.stdout + logs.stderr)
        self.assertIn('/.well-known/acme-challenge/', logs.stdout + logs.stderr)
