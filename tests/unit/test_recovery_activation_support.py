import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.recovery_activation_support import RecoveryActivation


class RecoveryActivationGuardTests(unittest.TestCase):
    def activation(self):
        profile = {'dashboard_image_id': 'fixture-image',
                   'consumers': {'mail-admin': {'environment': {
                       'HASH_MEMORY_KIB': '8192', 'HASH_PARALLELISM': '1',
                       'HTTP_CONCURRENCY': '4', 'OPERATION_TIMEOUT_SECONDS': '5',
                   }}}, 'transient_resources': {'memory_limit_bytes': 134217728,
                       'tmpfs_limit_bytes': 8388608, 'cpu_limit': 0.5}}
        with patch('tests.recovery_activation_support.protected_json', return_value=profile):
            return RecoveryActivation(SimpleNamespace())

    def test_unsupported_or_partial_installation_blocks_application_start(self):
        from pathlib import Path

        activation = self.activation()
        activation.target = SimpleNamespace(root=Path('/fixture'),
                                            context={'target_instance_id': 'fixture'})
        for facts in ({'target_instance_id': 'other', 'roles_installed': 18,
                       'application_started': False},
                      {'target_instance_id': 'fixture', 'roles_installed': 17,
                       'application_started': False},
                      {'target_instance_id': 'fixture', 'roles_installed': 18,
                       'application_started': True}):
            with (self.subTest(facts=facts),
                  patch('tests.recovery_activation_support.protected_json', return_value=facts),
                  patch.object(activation, 'start_maddy') as start,
                  self.assertRaisesRegex(RuntimeError, 'Full-state installation')):
                activation.activate()
            start.assert_not_called()

    def test_other_or_partial_inventory_never_establishes_mail_restore(self):
        activation = self.activation()
        with (patch.object(activation.docker, 'run'),
              patch('tests.recovery_activation_support.DockerMaddy') as maddy):
            for inventory in ({'credentials': [], 'storage': []},
                              {'credentials': ['helper-lifecycle@rsalehin24.me'], 'storage': []},
                              {'credentials': ['unexpected@example.net'],
                               'storage': ['unexpected@example.net']}):
                maddy.return_value.inventories.return_value = inventory
                with self.subTest(inventory=inventory), self.assertRaises(RuntimeError):
                    activation.start_maddy()

    def test_credential_digest_sorts_complete_rows_without_assuming_upstream_column_names(self):
        from tests.recovery_activation_support import credential_digest

        rows = [('mailbox-b', 'verifier-b'), ('mailbox-a', 'verifier-a')]
        with patch('tests.recovery_activation_support.sqlite3.connect') as connect:
            connect.return_value.execute.return_value.fetchall.return_value = rows
            first = credential_digest()
            connect.return_value.execute.return_value.fetchall.return_value = list(reversed(rows))
            self.assertEqual(first, credential_digest())
            statement = connect.return_value.execute.call_args.args[0]
            self.assertEqual(statement, 'SELECT * FROM passwords')
            self.assertNotIn(json.dumps(rows), first)
