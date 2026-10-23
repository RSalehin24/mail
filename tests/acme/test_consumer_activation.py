"""Validate certificate activation against real Compose, Maddy and HTTPS containers."""

import os
from unittest.mock import patch

from shared.errors import ServiceError
from tests.certbot_case_support import PrivateAcmeCase
from tests.renewal_support import PrivateRenewal
from tests.tls_consumer_support import PrivateTlsConsumers


class ConsumerActivationTests(PrivateAcmeCase):
    def test_failed_consumers_are_independent_and_retry_without_new_issuance_serves_the_pair(self):
        host = PrivateRenewal(self.certbot)
        services = PrivateTlsConsumers(self.fixture, host)
        self.addCleanup(services.close)
        trust = services.combined_trust(self.network.root / 'issuance-root.pem')
        with patch.dict(os.environ, trust):
            previous = services.start()
            original = services.fingerprints()
            self.assertEqual(len(set(original.values())), 1)
            for failed_role in ('maddy', 'mail-admin'):
                with self.subTest(failed_consumer=failed_role):
                    previous = self.assert_independent_failure(services, previous, failed_role)
            self.assertTrue(all(record['running'] for record in services.observe().values()))
            services.save_observation()

    def test_absent_first_dashboard_is_allowed_but_a_missing_deployed_dashboard_stays_pending(self):
        host = PrivateRenewal(self.certbot)
        services = PrivateTlsConsumers(self.fixture, host)
        self.addCleanup(services.close)
        trust = services.combined_trust(self.network.root / 'issuance-root.pem')
        with patch.dict(os.environ, trust):
            services.start_mail_only()
            host.renewal.run(self.certbot.pair)
            state = host.generations.read()
            self.assertEqual(state['outcome'], 'active')
            self.assertEqual(state['consumers']['mail-admin']['intent'], 'never_deployed')
            self.assertEqual(services.compose.docker.run((
                'ps', '-aq', '--filter', 'name=^/' + services.compose.names['mail-admin'] + '$',
            )), '')
            generation = host.generations.paths.directory / state['desired_generation']
            services.deploy('mail-admin', generation)
            host.generations.register('mail-admin')
            host.renewal.consumers = services.consumers()
            host.generations.activate(host.renewal.consumers)
            missing = services.compose.docker.inspect(services.compose.names['mail-admin'])['Id']
            services.compose.docker.run(('rm', '--force', missing))
            self.assert_missing_stays_pending(services)

    def assert_missing_stays_pending(self, services):
        host = services.renewal
        maddy = services.compose.docker.inspect(services.compose.names['maddy'])['Id']
        with self.assertRaisesRegex(ServiceError, 'certificate_activation_pending'):
            host.renewal.run(self.certbot.pair)
        state = host.generations.read()
        self.assertEqual(state['consumers']['mail-admin']['state'], 'pending')
        self.assertEqual(state['consumers']['mail-admin']['intent'], 'expected_running')
        self.assertEqual(state['consumers']['maddy']['state'], 'active')
        self.assertEqual(services.compose.docker.inspect(
            services.compose.names['maddy'])['Id'], maddy)

    def assert_independent_failure(self, services, previous, failed_role):
        host = services.renewal
        generation = host.generations.stage(self.certbot.pair if failed_role == 'maddy'
                                             else self.fixture.certificates.issue('next'))
        host.generations.publish(generation)
        image = services.compose.services[failed_role]['image']
        services.compose.services[failed_role]['image'] = 'sha256:' + '0' * 64
        services.compose.write()
        with self.assertRaisesRegex(ServiceError, 'certificate_activation_pending'):
            host.generations.activate(host.renewal.consumers)
        state = host.generations.read()
        other_role = 'mail-admin' if failed_role == 'maddy' else 'maddy'
        self.assertEqual(state['consumers'][failed_role]['state'], 'pending')
        self.assertEqual(state['consumers'][failed_role]['active_generation'], previous.name)
        self.assertEqual(state['consumers'][other_role]['active_generation'], generation.name)
        services.compose.services[failed_role]['image'] = image
        services.compose.write()
        before = len(host.docker.created)
        services.resume_in_new_process()
        self.assertEqual(len(host.docker.created), before)
        self.assertEqual(host.generations.read()['outcome'], 'active')
        expected = host.generations.validator.validate(generation)['served_sha256']
        self.assertEqual(services.fingerprints(), dict.fromkeys(services.records, expected))
        return generation
