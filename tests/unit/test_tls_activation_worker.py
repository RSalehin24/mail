"""Fresh activation must preserve fixture identity and reject production targets."""

import os
import unittest
import uuid
from unittest.mock import patch

from shared.files import atomic_json
from tests.support import StateFixture
from tests.tls_activation_worker import load_fixture


class ActivationWorkerTests(unittest.TestCase):
    def setUp(self):
        self.state = StateFixture()
        self.addCleanup(self.state.close)
        project = 'mail-admin-tls-' + str(uuid.uuid4())
        names = {'maddy': 'mail-admin-fixture-' + str(uuid.uuid4()),
                 'mail-admin': project + '-web'}
        self.context = {'mail_root': str(self.state.root), 'web_root': str(self.state.root),
                        'project': project, 'names': names, 'records': {
                            role: {'container': name, 'service': role,
                                   'compose_file': str(self.state.root / 'consumers.json')}
                            for role, name in names.items()
                        }}
        self.path = self.state.root / 'context.json'

    def load(self):
        atomic_json(self.path, self.context)
        with patch.dict(os.environ, {'MAIL_ADMIN_FIXTURE_ROOT': str(self.state.root)}):
            return load_fixture(self.path)

    def test_new_process_reconstructs_the_original_private_mail_container_identity(self):
        context, compose = self.load()
        self.assertEqual(compose.mail.container, context['names']['maddy'])
        self.assertEqual(compose.names, context['names'])
        self.assertEqual(compose.project, context['project'])

    def test_production_container_names_are_rejected_before_any_docker_operation(self):
        self.context['names']['maddy'] = 'maddy'
        self.context['records']['maddy']['container'] = 'maddy'
        with patch('host.docker.Docker.run') as docker:
            with self.assertRaisesRegex(RuntimeError, 'outside the private TLS fixture'):
                self.load()
        docker.assert_not_called()

    def test_changed_compose_path_is_rejected_before_any_docker_operation(self):
        self.context['records']['maddy']['compose_file'] = '/opt/maddy/docker-compose.yml'
        with patch('host.docker.Docker.run') as docker:
            with self.assertRaisesRegex(RuntimeError, 'identity changed'):
                self.load()
        docker.assert_not_called()
