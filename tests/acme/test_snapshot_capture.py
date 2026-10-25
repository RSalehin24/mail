import dataclasses
import json
import os
import time
import uuid
from unittest.mock import patch

from helper.protocol import HelperRequest
from host.backup import BackupPaths, CaptureDependencies, SnapshotCapture
from host.docker import DockerSnapshots
from shared.errors import ServiceError
from tests.certbot_case_support import PrivateAcmeCase
from tests.helper_support import MAILBOX_PASSWORD
from tests.renewal_support import PrivateRenewal
from tests.snapshot_consumer_support import SnapshotConsumerDocker
from tests.tls_consumer_support import PrivateTlsConsumers


class ActualSnapshotCaptureTests(PrivateAcmeCase):
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
        self.fixture.maddy.deployment.update({
            'certificate_state': str(self.host.generations.paths.state),
            'tls_generations_directory': self.fixture.daemon_path(
                self.host.generations.paths.directory),
            'maddy_tls_target': '/data/tls',
        })
        self.host.coordination.locks = dataclasses.replace(
            self.host.coordination.locks, maddy=self.fixture.helper.dependencies.lock,
        )
        self.host.coordination.journal = self.fixture.helper.journal
        self.docker = SnapshotConsumerDocker(self.services)
        self.writers = DockerSnapshots((self.docker, self.fixture.maddy, self.host.generations))
        self.staging = self.fixture.root / 'captures'
        self.staging.mkdir(mode=0o700)

    def capture(self):
        paths = BackupPaths(self.staging, self.fixture.root / 'capture-manifest.json', {
            'maddy': self.fixture.data, 'dashboard': self.services.web.directories['data'],
            'keys': self.services.web.directories['keys'],
            'helper': self.fixture.helper.journal.path,
            'certificates': self.host.generations.paths.directory,
        })
        capture = SnapshotCapture(paths, CaptureDependencies(
            self.host.coordination, self.writers, time.time,
        ))
        original = capture._copy

        def observed(snapshot):
            self.assert_writers_and_locks_excluded()
            return original(snapshot)

        with (patch.object(capture, '_copy', side_effect=observed),
              patch('host.docker.verify_served', side_effect=self.docker.verify)):
            return capture.capture()

    def assert_writers_and_locks_excluded(self):
        for role in self.services.records:
            self.assertFalse(self.docker.inspect(role)['State']['Running'])
        request = HelperRequest('create', str(uuid.uuid4()), 'capture-blocked@rsalehin24.me',
                                MAILBOX_PASSWORD)
        with self.assertRaisesRegex(ServiceError, 'maintenance_busy'):
            self.fixture.helper.handle(request)
        with self.assertRaisesRegex(ServiceError, 'maintenance_busy'):
            self.host.renewal.run(self.certbot.pair)
        self.assertIsNone(self.fixture.helper.journal.lookup(request.request_id))
        self.assertEqual(self.host.docker.created, set())

    def test_both_real_writers_stop_and_only_previously_running_services_resume(self):
        expected = {'maddy', 'mail-admin'}
        first = self.capture()
        metadata = json.loads((first / 'snapshot.json').read_bytes())
        self.assertEqual(set(metadata['previously_running']), expected)
        self.assertTrue(all(self.docker.inspect(role)['State']['Running'] for role in expected))
        self.docker.run(('stop', '--time', '30', 'mail-admin'))
        second = self.capture()
        metadata = json.loads((second / 'snapshot.json').read_bytes())
        self.assertEqual(metadata['previously_running'], ['maddy'])
        self.assertTrue(self.docker.inspect('maddy')['State']['Running'])
        self.assertFalse(self.docker.inspect('mail-admin')['State']['Running'])
        self.assertFalse((first / 'captures').exists())
