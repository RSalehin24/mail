import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from tests.snapshot_consumer_support import SnapshotConsumerDocker


class SnapshotConsumerTests(unittest.TestCase):
    def fixture(self):
        services = SimpleNamespace(
            records={'maddy': {'container': 'owned-mail', 'tls_port': 30001},
                     'mail-admin': {'container': 'owned-web', 'tls_port': 30002}},
            compose=SimpleNamespace(docker=Mock()),
        )
        return SnapshotConsumerDocker(services)

    def test_restart_verification_uses_current_ports_instead_of_stale_fixture_records(self):
        docker = self.fixture()
        for endpoint, target, name in ((('127.0.0.1', 993), '993/tcp', 'owned-mail'),
                                       (('127.0.0.1', 443), '8443/tcp', 'owned-web')):
            with self.subTest(endpoint=endpoint):
                docker.services.compose.docker.inspect.return_value = {
                    'NetworkSettings': {'Ports': {target: [{'HostPort': '31000'}]}},
                }
                with patch('tests.snapshot_consumer_support.verify_served') as verify:
                    docker.verify(endpoint, 'expected-fingerprint', 15)
                docker.services.compose.docker.inspect.assert_called_with(name)
                verify.assert_called_once_with(('127.0.0.1', 31000), 'expected-fingerprint', 15)

    def test_unpublished_or_unowned_endpoint_cannot_report_verified_recovery(self):
        docker = self.fixture()
        docker.services.compose.docker.inspect.return_value = {'NetworkSettings': {'Ports': {}}}
        with patch('tests.snapshot_consumer_support.verify_served') as verify:
            with self.assertRaisesRegex(AssertionError, 'no published TLS port'):
                docker.verify(('127.0.0.1', 443), 'expected-fingerprint', 15)
            with self.assertRaisesRegex(AssertionError, 'Unexpected snapshot'):
                docker.verify(('mail.example', 443), 'expected-fingerprint', 15)
        verify.assert_not_called()
