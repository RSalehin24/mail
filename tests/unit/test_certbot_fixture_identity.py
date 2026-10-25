import copy
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from shared.errors import ServiceError
from tests.certbot_host_support import PrivateCertbotDocker


class CertbotFixtureIdentityTests(unittest.TestCase):
    def setUp(self):
        self.name = 'mail-admin-certbot-00000000-0000-0000-0000-000000000001'
        self.identifier = 'a' * 64
        self.run = {'name': self.name, 'container_id': self.identifier}
        self.network = SimpleNamespace(name='mail-admin-acme-private-fixture', created=[])
        self.adapter = PrivateCertbotDocker.__new__(PrivateCertbotDocker)
        self.adapter.client = SimpleNamespace(network=self.network)
        self.adapter.created = set()
        self.record = {'Id': self.identifier, 'Name': '/' + self.name, 'Image': 'pinned-image',
                       'Config': {'Labels': {'mail-admin.certbot-run': self.name,
                                             'mail-admin.fixture': self.network.name}},
                       'HostConfig': {'NetworkMode': self.network.name}}
        self.adapter.docker = Mock()

    def adopt(self, record):
        self.adapter.docker.inspect.side_effect = [record, {'Id': 'pinned-image'}]
        self.adapter.adopt_recorded_run(self.run)

    def test_recovery_adopts_only_the_exact_recorded_private_container(self):
        self.adopt(self.record)
        self.assertEqual(self.adapter.created, {self.identifier})
        self.assertEqual(self.network.created, [self.identifier])

    def test_foreign_name_image_network_or_fixture_label_never_authorizes_adoption(self):
        replacements = ({'Id': 'b' * 64}, {'Name': '/maddy'}, {'Image': 'other-image'},
                        {'HostConfig': {'NetworkMode': 'host'}},
                        {'Config': {'Labels': {'mail-admin.certbot-run': self.name,
                                               'mail-admin.fixture': 'another-fixture'}}})
        for replacement in replacements:
            record = {**copy.deepcopy(self.record), **replacement}
            with self.subTest(replacement=replacement), self.assertRaises(
                (AssertionError, ServiceError),
            ):
                self.adopt(record)
            self.assertEqual(self.adapter.created, set())
            self.assertEqual(self.network.created, [])
