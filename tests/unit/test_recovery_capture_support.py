import unittest
from types import SimpleNamespace

from tests.recovery_capture_support import source_dependencies


class RecoveryCaptureDependenciesTests(unittest.TestCase):
    def test_missing_alert_provider_inputs_do_not_block_independent_fixture_capture(self):
        profile = {'inputs': {'service_gid': 10001, 'lock_wait_seconds': 1,
                              'operation_timeout_seconds': 5}}
        journal = object()
        dependencies = source_dependencies(profile, SimpleNamespace(journal=journal))
        self.assertIs(dependencies.coordination.journal, journal)
        self.assertEqual(dependencies.services.docker.timeout, 5)
