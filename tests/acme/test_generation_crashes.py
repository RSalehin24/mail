import os
import re
import uuid
from unittest.mock import patch

from host.certificates import verify_served
from tests.certbot_case_support import PrivateAcmeCase
from tests.renewal_support import PrivateRenewal
from tests.tls_consumer_support import PrivateTlsConsumers
from tests.tls_crash_support import interrupt_generation


class GenerationCrashTests(PrivateAcmeCase):
    def setUp(self):
        super().setUp()
        self.host = PrivateRenewal(self.certbot)
        self.services = PrivateTlsConsumers(self.fixture, self.host)
        self.addCleanup(self.services.close)
        trust = self.services.combined_trust(self.network.root / 'issuance-root.pem')
        boundary = patch.dict(os.environ, trust)
        boundary.start()
        self.addCleanup(boundary.stop)
        self.services.start()

    def assert_complete_published_generations(self):
        for path in self.host.generations.paths.directory.iterdir():
            if re.fullmatch(r'[0-9a-f]{64}', path.name):
                self.host.generations.validator.validate(path)

    def assert_stopped_writer_can_resume(self, boundary):
        old_state = self.host.generations.read()
        source = self.fixture.certificates.issue('interrupted-' + str(uuid.uuid4()))
        expected = self.host.generations.validator.validate(source)['served_sha256']
        before = len(self.host.docker.created)
        interrupt_generation(self.host, source, boundary)
        self.assert_complete_published_generations()
        fingerprints = self.services.fingerprints()
        self.assertTrue(set(fingerprints.values()) <= {old_state['served_sha256'], expected})
        with self.host.coordination.consistent():
            generation = self.host.generations.stage(source)
            self.host.generations.publish(generation)
        self.services.resume_in_new_process()
        self.assertEqual(self.host.generations.read()['outcome'], 'active')
        actual = self.services.fingerprints()
        self.assertEqual(actual, dict.fromkeys(self.services.records, expected))
        self.assertEqual(len(self.host.docker.created), before)

    def test_sigkill_at_each_staging_and_publication_boundary_keeps_one_complete_pair(self):
        boundaries = (('staged', 'fullchain.pem'), ('staged', 'privkey.pem'),
                      ('before_rename', None), ('after_rename', None),
                      ('before_publication', None), ('after_publication', None))
        for boundary in boundaries:
            with self.subTest(boundary=boundary):
                self.assert_stopped_writer_can_resume(boundary)
        self.services.save_observation()

    def test_sigkill_between_actual_consumer_activations_resumes_without_another_issuance(self):
        for role in ('maddy', 'mail-admin'):
            with self.subTest(consumer=role):
                self.assert_stopped_writer_can_resume(('consumer', role))
        self.services.save_observation()

    def test_retention_protects_references_and_restarted_consumers_serve_the_active_pair(self):
        generations = self.host.generations
        active = generations.paths.directory / generations.read()['desired_generation']
        rollback, obsolete, desired = [
            generations.stage(self.fixture.certificates.issue(role))
            for role in ('rollback', 'obsolete', 'desired')
        ]
        with self.host.coordination.consistent():
            generations.publish(desired)
            record = generations.read()
            record['rollback_generations'] = [rollback.name]
            generations.write(record)
            for path in (active, rollback, obsolete, desired):
                age = generations.clock() - 8 * 86400
                os.utime(path, (age, age))
            generations.prune(7 * 86400)
        self.assertFalse(obsolete.exists())
        self.assertTrue(all(path.is_dir() for path in (active, rollback, desired)))
        before = len(self.host.docker.created)
        self.services.resume_in_new_process()
        expected = generations.validator.validate(desired)['served_sha256']
        for record in self.services.records.values():
            self.services.compose.docker.run(('restart', record['container']))
            verify_served(('127.0.0.1', record['tls_port']), expected, 15)
        self.assertEqual(self.services.fingerprints(),
                         dict.fromkeys(self.services.records, expected))
        self.assertEqual(len(self.host.docker.created), before)
        self.services.save_observation()
